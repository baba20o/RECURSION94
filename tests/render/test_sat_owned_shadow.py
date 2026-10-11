from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPRITES_PATH = ROOT / "src/render/gameplay_renderer_sprites.asm"
CORE_PATH = ROOT / "src/render/gameplay_renderer_core.asm"

SAT_BYTES = 640
SAT_ENTRY_BYTES = 8
SAT_BURST_BYTES = 32
SPRITE_LIMIT = 64
INVALID_STATE = 0x0080

GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


def _entry(ordinal: int, salt: int) -> bytes:
    """One complete eight-byte SAT entry with an initially zero link."""
    return struct.pack(
        ">HHHH",
        (0x0080 + ordinal + salt) & 0xFFFF,
        0x0500 | ((ordinal & 3) << 8),
        (0x2000 + salt * 67 + ordinal) & 0xFFFF,
        (0x0080 + ordinal * 3 + salt) & 0xFFFF,
    )


def _append(shadow: bytearray, count: int, entry: bytes) -> int:
    if count:
        link_offset = (count - 1) * SAT_ENTRY_BYTES + 2
        prior = int.from_bytes(shadow[link_offset : link_offset + 2], "big")
        shadow[link_offset : link_offset + 2] = (
            (prior & 0xFF80) | count
        ).to_bytes(2, "big")
    offset = count * SAT_ENTRY_BYTES
    shadow[offset : offset + SAT_ENTRY_BYTES] = entry
    return count + 1


def _render_after_clear(
    shadow: bytearray, decisions: tuple[tuple[bool, bytes], ...]
) -> bytes:
    count = 0
    for admitted, entry in decisions:
        if admitted:
            count = _append(shadow, count, entry)
    return bytes(shadow)


def _full_rebuild(
    prior: bytes, decisions: tuple[tuple[bool, bytes], ...]
) -> bytes:
    shadow = bytearray(prior)
    shadow[:] = bytes(SAT_BYTES)
    return _render_after_clear(shadow, decisions)


def _owned_rebuild(
    prior: bytes,
    prior_count: int,
    decisions: tuple[tuple[bool, bytes], ...],
) -> tuple[bytes, int, int]:
    shadow = bytearray(prior)
    flags = 0
    if prior_count > SPRITE_LIMIT:
        bursts = SAT_BYTES // SAT_BURST_BYTES
        flags |= INVALID_STATE
    else:
        bursts = (prior_count + 3) >> 2
    shadow[: bursts * SAT_BURST_BYTES] = bytes(
        bursts * SAT_BURST_BYTES
    )
    return _render_after_clear(shadow, decisions), flags, bursts


def _admitted(count: int, *, salt: int) -> tuple[tuple[bool, bytes], ...]:
    return tuple((True, _entry(index, salt)) for index in range(count))


def _legal_shadow(count: int) -> bytes:
    return _full_rebuild(bytes([0xA5]) * SAT_BYTES, _admitted(count, salt=1))


class SatOwnedShadowSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sprites = SPRITES_PATH.read_text(encoding="utf-8")
        cls.core = CORE_PATH.read_text(encoding="utf-8")

    def test_full_owned_and_worker_entries_are_exact_tail_wrappers(self) -> None:
        public = routine(self.sprites, "RendererBuildSat")
        owned = routine(self.sprites, "RendererBuildSatOwnedShadow")
        worker = routine(self.sprites, "RendererBuildSatRebuild")

        self.assertIn("move.w  #SAT_SHADOW_BYTES/32,d0", public)
        self.assertIn("bra.w   RendererBuildSatRebuild", public)
        self.assertNotIn("rts", public)
        self.assertNotIn("movem.l", public)

        ordered = (
            "moveq   #0,d0",
            "move.w  (RENDERER_SPRITE_COUNT).l,d0",
            "cmpi.w  #RENDERER_SPRITE_LIMIT,d0",
            "bhi.s   .invalid_prior_count",
            "addq.w  #3,d0",
            "lsr.w   #2,d0",
            "bra.w   RendererBuildSatRebuild",
            "ori.w   #RENDERER_FLAG_INVALID_STATE,(RENDERER_FLAGS).l",
            "bra.w   RendererBuildSat\n",
        )
        positions = [owned.index(token) for token in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("rts", owned)
        self.assertNotIn("bsr", owned)

        self.assertIn("movem.l d3-d7/a2-a5,-(a7)", worker)
        copied = worker.index("move.w  d0,d7")
        zeroed = worker.index("moveq   #0,d0")
        self.assertLess(copied, zeroed)
        for token in (
            "tst.w   d7",
            "beq.s   .sat_cleared",
            "subq.w  #1,d7",
            "dbf     d7,.clear_sat",
            ".sat_cleared:",
            "clr.w   (RENDERER_SPRITE_COUNT).l",
        ):
            self.assertIn(token, worker)
        self.assertLess(worker.index("tst.w   d7"), worker.index("subq.w  #1,d7"))
        code_references = [
            line
            for line in self.sprites.splitlines()
            if "RendererBuildSatRebuild" in line
            and not line.lstrip().startswith(";")
        ]
        self.assertEqual(len(code_references), 3)

    def test_initialization_uses_full_entry_and_guarded_frame_uses_owned(self) -> None:
        initialize = self.core.split("GameplayRendererInitialize:", 1)[1].split(
            "GameplayRendererBuildAndQueue:", 1
        )[0]
        frame = self.core.split("GameplayRendererBuildAndQueue:", 1)[1].split(
            "RendererLoadPaletteAndTiles:", 1
        )[0]
        self.assertEqual(initialize.count("bsr.w   RendererBuildSat"), 1)
        self.assertNotIn("RendererBuildSatOwnedShadow", initialize)
        self.assertEqual(frame.count("bsr.w   RendererBuildSatOwnedShadow"), 1)
        self.assertNotRegex(frame, r"bsr\.w\s+RendererBuildSat(?:\s|$)")

        pending = frame.index("tst.w   (DMA_QUEUE_FRAME_PENDING).l")
        pending_branch = frame.index("bne.w   .backlog", pending)
        build = frame.index("bsr.w   RendererBuildSatOwnedShadow")
        self.assertLess(pending, pending_branch)
        self.assertLess(pending_branch, build)
        held = frame.split(".checkpoint_hold:", 1)[1].split(
            ".checkpoint_ready:", 1
        )[0]
        self.assertIn("bra.w   .restore", held)
        self.assertNotIn("RendererBuildSat", held)
        self.assertNotIn("DmaQueue", held)

    def test_sat_shadow_and_count_write_sites_are_bounded(self) -> None:
        code_lines = [
            line.strip()
            for line in self.sprites.splitlines()
            if line.strip() and not line.lstrip().startswith(";")
        ]
        sat_references = [line for line in code_lines if "SAT_SHADOW" in line]
        self.assertEqual(
            sat_references,
            [
                "move.w  #SAT_SHADOW_BYTES/32,d0",
                "lea     (SAT_SHADOW).l,a0",
                "lea     (SAT_SHADOW).l,a2",
            ],
        )
        count_writes = [
            line
            for line in code_lines
            if "RENDERER_SPRITE_COUNT" in line
            and not line.startswith("move.w  (RENDERER_SPRITE_COUNT)")
        ]
        self.assertEqual(
            count_writes,
            [
                "clr.w   (RENDERER_SPRITE_COUNT).l",
                "addq.w  #1,(RENDERER_SPRITE_COUNT).l",
            ],
        )

        worker = routine(self.sprites, "RendererBuildSatRebuild")
        clear = worker.split("lea     (SAT_SHADOW).l,a0", 1)[1].split(
            ".sat_cleared:", 1
        )[0]
        self.assertEqual(clear.count("movem.l d0-d6/a1,(a0)"), 1)
        admission = routine(self.sprites, "RendererTryAppendSprite")
        append = admission.split("; Append with a zero link", 1)[1].split(
            ".rollback_scanlines:", 1
        )[0]
        for write in (
            "move.w  d1,-6(a2)",
            "move.w  d1,(a2)",
            "move.w  d7,2(a2)",
            "move.w  d6,4(a2)",
            "move.w  d1,6(a2)",
        ):
            self.assertEqual(append.count(write), 1)
        self.assertIn("addq.w  #1,(RENDERER_SPRITE_COUNT).l", append)


class SatOwnedShadowModelTests(unittest.TestCase):
    def test_every_valid_count_maps_to_bounded_rounded_bursts(self) -> None:
        prior = bytes(SAT_BYTES)
        for count in range(SPRITE_LIMIT + 1):
            with self.subTest(count=count):
                rebuilt, flags, bursts = _owned_rebuild(prior, count, ())
                self.assertEqual(bursts, (count + 3) // 4)
                self.assertLessEqual(bursts, 16)
                self.assertEqual(flags, 0)
                self.assertEqual(rebuilt, prior)

    def test_invalid_counts_flag_and_fall_back_to_full_80_entry_clear(self) -> None:
        dirty = bytes((index * 37 + 11) & 0xFF for index in range(SAT_BYTES))
        decisions = _admitted(9, salt=7)
        expected = _full_rebuild(dirty, decisions)
        for count in (65, 80, 0xFFFF):
            with self.subTest(count=count):
                rebuilt, flags, bursts = _owned_rebuild(
                    dirty, count, decisions
                )
                self.assertEqual(bursts, 20)
                self.assertEqual(flags & INVALID_STATE, INVALID_STATE)
                self.assertEqual(rebuilt, expected)
                self.assertEqual(len(rebuilt), SAT_BYTES)

    def test_all_growth_and_shrink_pairs_match_full_640_byte_rebuild(self) -> None:
        for prior_count in range(SPRITE_LIMIT + 1):
            prior = _legal_shadow(prior_count)
            self.assertEqual(
                prior[prior_count * SAT_ENTRY_BYTES :],
                bytes(SAT_BYTES - prior_count * SAT_ENTRY_BYTES),
            )
            for next_count in range(SPRITE_LIMIT + 1):
                decisions = _admitted(next_count, salt=9)
                expected = _full_rebuild(prior, decisions)
                actual, flags, _ = _owned_rebuild(
                    prior, prior_count, decisions
                )
                with self.subTest(prior=prior_count, next=next_count):
                    self.assertEqual(flags, 0)
                    self.assertEqual(len(actual), SAT_BYTES)
                    self.assertEqual(actual, expected)

    def test_first_middle_last_rejections_never_change_full_byte_result(self) -> None:
        schedules = (
            (False,),
            (False, True, True, True),
            (True, False, True, True),
            (True, True, True, False),
            tuple(index % 3 != 1 for index in range(64)),
        )
        for prior_count in range(SPRITE_LIMIT + 1):
            prior = _legal_shadow(prior_count)
            for ordinal, schedule in enumerate(schedules):
                decisions = tuple(
                    (admitted, _entry(index, 20 + ordinal))
                    for index, admitted in enumerate(schedule)
                )
                expected = _full_rebuild(prior, decisions)
                actual, flags, _ = _owned_rebuild(
                    prior, prior_count, decisions
                )
                with self.subTest(prior=prior_count, schedule=ordinal):
                    self.assertEqual(flags, 0)
                    self.assertEqual(actual, expected)

    def test_pending_and_checkpoint_hold_paths_leave_sat_unchanged(self) -> None:
        shadow = _legal_shadow(31)

        def maybe_build(*, pending: bool, checkpoint_hold: bool) -> bytes:
            if pending or checkpoint_hold:
                return shadow
            return _owned_rebuild(shadow, 31, _admitted(12, salt=31))[0]

        self.assertEqual(maybe_build(pending=True, checkpoint_hold=False), shadow)
        self.assertEqual(maybe_build(pending=False, checkpoint_hold=True), shadow)
        self.assertNotEqual(
            maybe_build(pending=False, checkpoint_hold=False), shadow
        )


if __name__ == "__main__":
    unittest.main()
