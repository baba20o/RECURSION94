from __future__ import annotations

import ast
import random
import re
import struct
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAYOUT_SOURCES = (
    ROOT / "src/hardware/memory_map.asm",
    ROOT / "src/game/engine_layout.asm",
    ROOT / "src/gameplay/gameplay_layout.asm",
    ROOT / "src/hardware/dma.asm",
    ROOT / "src/render/gameplay_renderer_layout.asm",
)
RENDER_SOURCES = tuple(sorted((ROOT / "src/render").glob("*.asm")))
EQU_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s+equ\s+(.+?)\s*$", re.IGNORECASE)
WARNING_RE = re.compile(r"(?i)(?:^|\s)warning(?:\s+[A-Za-z0-9_-]+)?\s*(?::|\[)")


def _safe_eval(expression: str, symbols: dict[str, int]) -> int:
    expression = re.sub(r"\$([0-9A-Fa-f]+)", r"0x\1", expression)
    expression = expression.replace("/", "//")
    parsed = ast.parse(expression, mode="eval")
    allowed = (
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.FloorDiv,
        ast.BitAnd,
        ast.BitOr,
        ast.LShift,
        ast.RShift,
        ast.UnaryOp,
        ast.USub,
        ast.UAdd,
        ast.BinOp,
        ast.Load,
    )
    if any(not isinstance(node, allowed) for node in ast.walk(parsed)):
        raise ValueError(f"unsupported equ expression: {expression}")
    return int(eval(compile(parsed, "<equ>", "eval"), {"__builtins__": {}}, symbols))


def parse_equ_contract() -> dict[str, int]:
    pending: list[tuple[str, str]] = []
    for path in LAYOUT_SOURCES:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split(";", 1)[0].strip()
            match = EQU_RE.match(line)
            if match:
                pending.append((match.group(1), match.group(2).strip()))

    symbols: dict[str, int] = {}
    while pending:
        deferred: list[tuple[str, str]] = []
        progress = False
        for name, expression in pending:
            try:
                symbols[name] = _safe_eval(expression, symbols)
            except (NameError, ValueError):
                deferred.append((name, expression))
            else:
                progress = True
        if not progress:
            break
        pending = deferred
    return symbols


@dataclass(frozen=True)
class Sprite:
    y: int
    height: int
    width: int


class ScanlineLedger:
    def __init__(self) -> None:
        self.counts = [0] * 224
        self.pixels = [0] * 224

    def admit(self, sprite: Sprite) -> bool:
        start = max(sprite.y, 0)
        end = min(sprite.y + sprite.height, 224)
        if end <= start:
            return False
        covered = range(start, end)
        if any(
            self.counts[line] >= 20
            or self.pixels[line] + sprite.width > 320
            for line in covered
        ):
            return False
        for line in covered:
            self.counts[line] += 1
            self.pixels[line] += sprite.width
        return True


class RendererAdmissionModel:
    """Host model for the assembly renderer's protected admission state."""

    def __init__(self, *, transactional: bool) -> None:
        self.transactional = transactional
        self.counts = bytearray(224)
        self.pixels = [0] * 224
        self.scanline_max = 0
        self.pixel_max = 0
        self.sprite_count = 0
        self.sat = bytearray(80 * 8)
        self.dropped = 0

    def protected_bytes(self) -> bytes:
        """Ledger/max/count/SAT bytes which a rejected sprite may not alter."""

        return b"".join(
            (
                bytes(self.counts),
                b"".join(value.to_bytes(2, "big") for value in self.pixels),
                struct.pack(
                    ">HHH",
                    self.scanline_max,
                    self.pixel_max,
                    self.sprite_count,
                ),
                bytes(self.sat),
            )
        )

    def full_bytes(self) -> bytes:
        return self.protected_bytes() + self.dropped.to_bytes(2, "big")

    def _drop(self) -> bool:
        self.dropped = min(self.dropped + 1, 0xFFFF)
        return False

    def _append_sat(self, sprite: Sprite) -> None:
        index = self.sprite_count
        offset = index * 8
        if index:
            previous = int.from_bytes(self.sat[offset - 6 : offset - 4], "big")
            previous = (previous & 0xFF80) | index
            self.sat[offset - 6 : offset - 4] = previous.to_bytes(2, "big")
        width_cells = sprite.width // 8
        height_cells = sprite.height // 8
        size = ((width_cells - 1) << 10) | ((height_cells - 1) << 8)
        attribute = 0x8000 | (index & 0x07FF)
        x = ((sprite.y * 3) + sprite.width) % 320
        self.sat[offset : offset + 8] = struct.pack(
            ">HHHH",
            (sprite.y + 128) & 0xFFFF,
            size,
            attribute,
            x + 128,
        )
        self.sprite_count += 1

    def admit(self, sprite: Sprite) -> bool:
        start = max(sprite.y, 0)
        end = min(sprite.y + sprite.height, 224)
        if end <= start:
            return False
        if self.sprite_count >= 64:
            return self._drop()

        if not self.transactional:
            if any(
                self.counts[line] >= 20
                or self.pixels[line] + sprite.width > 320
                for line in range(start, end)
            ):
                return self._drop()
            for line in range(start, end):
                self.counts[line] += 1
                self.pixels[line] += sprite.width
        else:
            committed: list[int] = []
            for line in range(start, end):
                if (
                    self.counts[line] >= 20
                    or self.pixels[line] + sprite.width > 320
                ):
                    for prior in reversed(committed):
                        self.counts[prior] -= 1
                        self.pixels[prior] -= sprite.width
                    return self._drop()
                self.counts[line] += 1
                self.pixels[line] += sprite.width
                committed.append(line)

        self.scanline_max = max(self.scanline_max, max(self.counts[start:end]))
        self.pixel_max = max(self.pixel_max, max(self.pixels[start:end]))
        self._append_sat(sprite)
        return True


class RendererLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.symbols = parse_equ_contract()

    def test_ram_layout_and_dma_window_are_exact(self) -> None:
        s = self.symbols
        self.assertEqual(s["RENDERER_STATE_BASE"], 0xFF8300)
        self.assertGreaterEqual(
            s["RENDERER_STATE_BASE"], s["SAT_SHADOW"] + s["SAT_SHADOW_BYTES"]
        )
        self.assertEqual(s["RENDERER_SCANLINE_COUNT_BYTES"], 224)
        self.assertEqual(s["RENDERER_SCANLINE_PIXEL_BYTES"], 448)
        self.assertEqual(
            s["RENDERER_HUD_END"] - s["RENDERER_HUD_BUFFER"], 3 * 40 * 2
        )
        self.assertLessEqual(s["RENDERER_DYNAMIC_END"], s["RENDERER_RAM_END"])
        self.assertLessEqual(s["RENDERER_RAM_END"], s["DMA_QUEUE_BASE"])
        source_offset = s["RENDERER_HUD_BUFFER"] & 0x1FFFF
        self.assertLessEqual(
            source_offset + s["RENDERER_DYNAMIC_END"] - s["RENDERER_HUD_BUFFER"],
            0x20000,
        )

    def test_vram_tiles_preserve_resident_font_and_plane_tables(self) -> None:
        s = self.symbols
        self.assertEqual(s["RENDERER_TILE_BASE"], 39)
        self.assertEqual(s["RENDERER_ACTOR_FRAME_TILES"], 9)
        self.assertEqual(s["RENDERER_TILE_HAZARD_COLUMN_TILES"], 4)
        self.assertEqual(s["RENDERER_TILE_HAZARD_SHUTTER_TILES"], 8)
        self.assertEqual(s["RENDERER_TILE_COUNT"], 104)
        self.assertEqual(s["RENDERER_TILE_BYTES"], 104 * 32)
        self.assertEqual(
            s["RENDERER_TILE_CURRENT_1"] - s["RENDERER_TILE_CURRENT_0"], 9
        )
        self.assertEqual(
            s["RENDERER_TILE_ECHO_1"] - s["RENDERER_TILE_ECHO_0"], 9
        )
        self.assertEqual(
            s["RENDERER_TILE_ECHO_RESET"] - s["RENDERER_TILE_ECHO_1"], 9
        )
        self.assertEqual(
            s["RENDERER_TILE_PROJECTILE"] - s["RENDERER_TILE_CARRYABLE"], 4
        )
        self.assertGreaterEqual(
            s["RENDERER_VRAM_TILE_START"], s["DMA_VRAM_DYNAMIC_START"]
        )
        self.assertLessEqual(
            s["RENDERER_VRAM_TILE_END"], s["DMA_VRAM_DYNAMIC_END"]
        )
        self.assertEqual(s["VRAM_PLANE_A"], 0xC000)
        self.assertEqual(s["VRAM_PLANE_B"], 0xE000)
        self.assertEqual(s["VRAM_SAT"], 0xF000)

    def test_per_frame_transfer_budget_is_bounded(self) -> None:
        s = self.symbols
        self.assertEqual(s["RENDERER_DMA_SAT_WORDS"], 320)
        self.assertEqual(s["RENDERER_DMA_HUD_WORDS"], 120)
        self.assertEqual(s["RENDERER_DMA_RESET_WORDS"], 80)
        self.assertEqual(s["RENDERER_DMA_WORST_WORDS"], 520)
        self.assertLessEqual(
            s["RENDERER_DMA_WORST_WORDS"], s["DMA_ORDINARY_WORD_LIMIT"]
        )
        self.assertEqual(s["RENDERER_DMA_WORST_ENTRIES"], 6)
        self.assertLessEqual(
            s["RENDERER_DMA_WORST_ENTRIES"], s["DMA_QUEUE_CAPACITY"]
        )

    def test_authored_sprite_bound_leaves_sat_headroom(self) -> None:
        s = self.symbols
        self.assertEqual(s["RENDERER_B01_WORST_SPRITES"], 31)
        self.assertEqual(s["RENDERER_PROJECTILE_LIMIT"], 6)
        self.assertEqual(s["RENDERER_DOOR_FEEDBACK_SPRITES"], 2)
        self.assertEqual(s["RENDERER_B01_WORST_SCANLINE_SPRITES"], 20)
        self.assertEqual(s["RENDERER_B01_WORST_SCANLINE_PIXELS"], 312)
        self.assertLessEqual(
            s["RENDERER_B01_WORST_SCANLINE_SPRITES"],
            s["RENDERER_SCANLINE_SPRITE_LIMIT"],
        )
        self.assertLessEqual(
            s["RENDERER_B01_WORST_SCANLINE_PIXELS"],
            s["RENDERER_SCANLINE_PIXEL_LIMIT"],
        )
        self.assertEqual(s["RENDERER_SAT_CAPACITY"], 80)
        self.assertEqual(s["RENDERER_SPRITE_LIMIT"], 64)
        self.assertLessEqual(
            s["RENDERER_B01_WORST_SPRITES"], s["RENDERER_SPRITE_LIMIT"]
        )


class RendererReferenceTests(unittest.TestCase):
    def test_scanline_count_admission_is_atomic(self) -> None:
        ledger = ScanlineLedger()
        for _ in range(20):
            self.assertTrue(ledger.admit(Sprite(y=100, height=8, width=8)))
        before = (ledger.counts[:], ledger.pixels[:])
        self.assertFalse(ledger.admit(Sprite(y=100, height=8, width=8)))
        self.assertEqual((ledger.counts, ledger.pixels), before)
        self.assertEqual(max(ledger.counts), 20)
        self.assertEqual(max(ledger.pixels), 160)

    def test_scanline_pixel_admission_is_atomic(self) -> None:
        ledger = ScanlineLedger()
        for _ in range(10):
            self.assertTrue(ledger.admit(Sprite(y=32, height=32, width=32)))
        before = (ledger.counts[:], ledger.pixels[:])
        self.assertFalse(ledger.admit(Sprite(y=48, height=8, width=8)))
        self.assertEqual((ledger.counts, ledger.pixels), before)
        self.assertEqual(max(ledger.pixels), 320)

    def test_transactional_rejection_rolls_back_each_limit_at_each_line(
        self,
    ) -> None:
        start = 80
        height = 24
        positions = {
            "first": start,
            "middle": start + (height // 2),
            "last": start + height - 1,
        }
        for limit in ("count", "pixels"):
            for position, failing_line in positions.items():
                with self.subTest(limit=limit, position=position):
                    model = RendererAdmissionModel(transactional=True)
                    for offset, line in enumerate(range(start, start + height)):
                        model.counts[line] = (offset * 3) % 5
                        model.pixels[line] = model.counts[line] * 8
                    if limit == "count":
                        model.counts[failing_line] = 20
                        model.pixels[failing_line] = 160
                    else:
                        model.counts[failing_line] = 10
                        model.pixels[failing_line] = 320
                    model.scanline_max = max(model.counts)
                    model.pixel_max = max(model.pixels)
                    before = model.protected_bytes()

                    self.assertFalse(
                        model.admit(Sprite(y=start, height=height, width=8))
                    )
                    self.assertEqual(model.protected_bytes(), before)
                    self.assertEqual(model.dropped, 1)

    def test_transactional_success_publishes_exact_byte_and_word_maxima(
        self,
    ) -> None:
        model = RendererAdmissionModel(transactional=True)
        start = 80
        height = 24
        for line in range(start, start + height):
            model.counts[line] = 19
            model.pixels[line] = 312

        self.assertTrue(
            model.admit(Sprite(y=start, height=height, width=8))
        )
        self.assertEqual(
            model.counts[start : start + height], bytes([20]) * height
        )
        self.assertEqual(model.pixels[start : start + height], [320] * height)
        self.assertEqual(model.scanline_max, 20)
        self.assertEqual(model.pixel_max, 320)
        self.assertEqual(model.sprite_count, 1)

    def test_transactional_admission_matches_preflight_reference(self) -> None:
        generator = random.Random(0x94B01)
        sprites = [
            Sprite(
                y=generator.randrange(-31, 224),
                height=generator.choice((8, 16, 24, 32)),
                width=generator.choice((8, 16, 24, 32)),
            )
            for _ in range(400)
        ]
        reference = RendererAdmissionModel(transactional=False)
        transactional = RendererAdmissionModel(transactional=True)
        for ordinal, sprite in enumerate(sprites):
            with self.subTest(ordinal=ordinal, sprite=sprite):
                self.assertEqual(
                    transactional.admit(sprite),
                    reference.admit(sprite),
                )
                self.assertEqual(
                    transactional.full_bytes(),
                    reference.full_bytes(),
                )

    def test_transactional_caps_and_dropped_counter_are_exact(self) -> None:
        scanline = RendererAdmissionModel(transactional=True)
        for _ in range(20):
            self.assertTrue(scanline.admit(Sprite(y=100, height=8, width=16)))
        self.assertEqual(scanline.scanline_max, 20)
        self.assertEqual(scanline.pixel_max, 320)
        before = scanline.protected_bytes()
        self.assertFalse(scanline.admit(Sprite(y=100, height=8, width=8)))
        self.assertEqual(scanline.protected_bytes(), before)
        self.assertEqual(scanline.dropped, 1)

        total = RendererAdmissionModel(transactional=True)
        for index in range(64):
            self.assertTrue(
                total.admit(Sprite(y=(index % 28) * 8, height=8, width=8))
            )
        self.assertEqual(total.sprite_count, 64)
        self.assertLessEqual(total.scanline_max, 20)
        self.assertLessEqual(total.pixel_max, 320)
        before = total.protected_bytes()
        self.assertFalse(total.admit(Sprite(y=216, height=8, width=8)))
        self.assertEqual(total.protected_bytes(), before)
        self.assertEqual(total.dropped, 1)

        total.dropped = 0xFFFF
        self.assertFalse(total.admit(Sprite(y=216, height=8, width=8)))
        self.assertEqual(total.dropped, 0xFFFF)

    def test_transactional_sat_output_is_deterministic_and_stably_linked(self) -> None:
        sprites = [
            Sprite(y=32 + ((index * 13) % 160), height=8, width=8)
            for index in range(31)
        ]

        def render() -> tuple[tuple[bool, ...], bytes]:
            model = RendererAdmissionModel(transactional=True)
            decisions = tuple(model.admit(sprite) for sprite in sprites)
            return decisions, model.full_bytes()

        first_decisions, first_bytes = render()
        second_decisions, second_bytes = render()
        self.assertEqual(first_decisions, (True,) * len(sprites))
        self.assertEqual(first_decisions, second_decisions)
        self.assertEqual(first_bytes, second_bytes)

        model = RendererAdmissionModel(transactional=True)
        for sprite in sprites:
            self.assertTrue(model.admit(sprite))
        links = [
            int.from_bytes(model.sat[offset + 2 : offset + 4], "big") & 0x007F
            for offset in range(0, model.sprite_count * 8, 8)
        ]
        self.assertEqual(links[:-1], list(range(1, len(sprites))))
        self.assertEqual(links[-1], 0)

    def test_representative_b01_critical_overlap_stays_below_h40_limit(self) -> None:
        ledger = ScanlineLedger()
        sprites = [
            Sprite(100, 24, 24),  # current body
            Sprite(88, 8, 8),  # current C badge, disjoint from body
            Sprite(168, 16, 16),  # hazard
            Sprite(108, 8, 16),  # switch
            Sprite(104, 16, 16),  # terminal
            Sprite(48, 32, 16),
            Sprite(80, 32, 16),
            Sprite(112, 32, 16),
            Sprite(144, 32, 16),
        ]
        for _ in range(6):
            sprites.extend((Sprite(100, 24, 24), Sprite(88, 8, 8)))
        sprites.extend(
            (
                Sprite(108, 16, 16),  # sentry
                Sprite(108, 16, 16),  # carryable
            )
        )
        sprites.extend(Sprite(112, 8, 8) for _ in range(6))
        sprites.extend(
            (
                Sprite(108, 8, 8),  # closed-door left bracket
                Sprite(108, 8, 8),  # closed-door right bracket
            )
        )
        self.assertEqual(len(sprites), 31)
        for sprite in sprites:
            self.assertTrue(ledger.admit(sprite))
        self.assertEqual(max(ledger.counts), 20)
        self.assertEqual(max(ledger.pixels), 312)

    def test_sat_links_are_stable_and_terminated(self) -> None:
        for count in (1, 2, 31, 64):
            links = [index + 1 for index in range(count)]
            links[-1] = 0
            self.assertEqual(links[:-1], list(range(1, count)))
            self.assertEqual(links[-1], 0)
            self.assertTrue(all(0 <= link < 80 for link in links))

    def test_twelve_frame_reset_trace_is_fast_and_clears_last_row(self) -> None:
        rows = [3 + phase * 2 for phase in range(12)]
        self.assertEqual(rows, list(range(3, 26, 2)))
        events: list[tuple[int | None, int | None]] = []
        previous: int | None = None
        for row in rows:
            events.append((previous, row))
            previous = row
        events.append((previous, None))
        self.assertEqual(events[0], (None, 3))
        self.assertEqual(events[-1], (25, None))
        self.assertEqual(len(events), 13)

    def test_authoritative_time_display_boundaries(self) -> None:
        expected = {
            0: "12:00",
            1: "11:59",
            59: "11:01",
            60: "11:00",
            719: "00:01",
            720: "00:00",
        }
        for tick, display in expected.items():
            remaining = 720 - min(tick, 720)
            self.assertEqual(f"{remaining // 60:02d}:{remaining % 60:02d}", display)


class RendererSourceTests(unittest.TestCase):
    def test_public_entry_points_and_main_side_queue_contract_exist(self) -> None:
        core = (ROOT / "src/render/gameplay_renderer_core.asm").read_text(
            encoding="utf-8"
        )
        self.assertEqual(core.count("\nGameplayRendererInitialize:"), 1)
        self.assertEqual(core.count("\nGameplayRendererBuildAndQueue:"), 1)
        initialize = core.split("GameplayRendererInitialize:", 1)[1].split(
            "GameplayRendererBuildAndQueue:",
            1,
        )[0]
        self.assertIn(
            "ori.w   #M2_FLAG_SPRITE_ACTIVE,(DEBUG_M2_FLAGS).l",
            initialize,
        )
        build = core.split("GameplayRendererBuildAndQueue:", 1)[1].split(
            "RendererLoadPaletteAndTiles:", 1
        )[0]
        self.assertIn("(DMA_QUEUE_FRAME_PENDING).l", build)
        self.assertIn("DmaQueueBeginFrame", build)
        self.assertEqual(build.count("DmaQueueEnqueue"), 6)
        self.assertIn("DmaQueuePublish", build)
        self.assertNotIn("(VDP_DATA).l", build)
        self.assertNotIn("(VDP_CONTROL).l", build)
        self.assertNotIn("GameplayStep", build)
        self.assertNotIn("EngineStep", build)

    def test_new_checkpoint_holds_exactly_one_video_frame_before_rebuild(
        self,
    ) -> None:
        layout = (
            ROOT / "src/render/gameplay_renderer_layout.asm"
        ).read_text(encoding="utf-8")
        core = (ROOT / "src/render/gameplay_renderer_core.asm").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "RENDERER_STATE_USED_END         equ     RENDERER_STATE_BASE+$24",
            layout,
        )
        self.assertIn(
            "RENDERER_LAST_CHECKPOINT_COUNT  equ     RENDERER_STATE_BASE+$24",
            layout,
        )
        self.assertIn(
            "RENDERER_PRIVATE_USED_END       equ     RENDERER_STATE_BASE+$26",
            layout,
        )
        self.assertIn(
            "assert  RENDERER_PRIVATE_USED_END<=RENDERER_SCANLINE_COUNT_BASE",
            layout,
        )
        initialize = core.split("GameplayRendererInitialize:", 1)[1].split(
            "GameplayRendererBuildAndQueue:", 1
        )[0]
        build = core.split("GameplayRendererBuildAndQueue:", 1)[1].split(
            "RendererLoadPaletteAndTiles:", 1
        )[0]
        self.assertIn(
            "(ENGINE_CHECKPOINT_COUNT).l,d0", initialize
        )
        self.assertIn(
            "(RENDERER_LAST_CHECKPOINT_COUNT).l", initialize
        )
        pending = build.index("(DMA_QUEUE_FRAME_PENDING).l")
        checkpoint = build.index("(ENGINE_CHECKPOINT_COUNT).l", pending)
        hud = build.index("bsr.w   RendererBuildHud", checkpoint)
        self.assertLess(pending, checkpoint)
        self.assertLess(checkpoint, hud)
        defer = build[checkpoint:hud]
        self.assertIn(
            "cmp.w   (RENDERER_LAST_CHECKPOINT_COUNT).l,d0", defer
        )
        self.assertIn(
            "move.w  d0,(RENDERER_LAST_CHECKPOINT_COUNT).l", defer
        )
        self.assertIn("#VOCAB_CAMPAIGN_FLAG_ACTIVE", defer)
        self.assertIn("(DMA_QUEUE_PUBLISHED_DEPTH).l", defer)
        self.assertIn("(DMA_QUEUE_BUILD_DEPTH).l", defer)
        self.assertIn("addq.w  #1,d3", defer)
        self.assertIn("cmpi.w  #1,d0", defer)
        self.assertIn(
            "#RENDERER_RESULT_CHECKPOINT_HELD,(RENDERER_LAST_RESULT).l",
            defer,
        )
        self.assertIn("bra.w   .restore", defer)
        self.assertNotIn("DmaQueue", defer)
        self.assertNotIn("RendererBuild", defer)

        last_attempt = 0
        last_checkpoint = 1
        outcomes = []
        cases = (
            # attempt, count, campaign, published, build
            (0, 1, True, 0, 0),
            (0, 2, True, 0, 0),
            (0, 2, True, 0, 0),
            (0, 4, True, 0, 0),       # malformed jump renders
            (0, 5, False, 0, 0),      # non-campaign renders
            (0, 6, True, 1, 0),       # queued work renders/backlogs
            (0, 7, True, 0, 1),
            (1, 1, True, 0, 0),       # canonical reset holds once
            (1, 1, True, 0, 0),
            (1, 2, True, 0, 0),
            (2, 3, True, 0, 0),       # malformed reset count renders
        )
        for attempt, checkpoint_count, campaign, published, build_depth in cases:
            prior_attempt = last_attempt
            prior_checkpoint = last_checkpoint
            if attempt != last_attempt:
                last_attempt = attempt
            changed = checkpoint_count != last_checkpoint
            if changed:
                last_checkpoint = checkpoint_count
            exact_transition = (
                checkpoint_count == 1
                if attempt != prior_attempt
                else checkpoint_count == ((prior_checkpoint + 1) & 0xFFFF)
            )
            held = (
                changed
                and campaign
                and published == 0
                and build_depth == 0
                and exact_transition
            )
            outcomes.append(held)
        self.assertEqual(
            outcomes,
            [False, True, False, False, False, False, False, True, False, True, False],
        )

    def test_echo_identity_and_state_text_are_explicit(self) -> None:
        hud = (ROOT / "src/render/gameplay_renderer_hud.asm").read_text(
            encoding="utf-8"
        )
        sprites = (ROOT / "src/render/gameplay_renderer_sprites.asm").read_text(
            encoding="utf-8"
        )
        for text in (
            '"PROGRAM STABLE  EAST LINK COMPLETE"',
            '"RESET IN PROGRESS  WORLD RESTORED"',
            '"REPLACE  SELECT ECHO"',
            '"ROUTE OPEN  CROSS TO EAST TERMINAL"',
            '"GATE SEALED  ECHO MUST HOLD SWITCH"',
            '"OBJECTIVE  HOLD SWITCH  OPEN GATE"',
            '"COMMIT HOLD 00/00 KEEP B HELD"',
            '"PROGRAM STABLE"',
            '"EAST LINK COMPLETE"',
        ):
            self.assertIn(text, hud)
        self.assertNotIn('"TICK"', hud)
        self.assertNotIn("RendererHudTick", hud)
        self.assertIn('"ECHOES"', hud)
        self.assertIn("TIMELINE_ECHO_ORDER", hud)
        self.assertIn("RENDERER_TILE_ECHO_MARK", hud)
        self.assertIn("RENDERER_TILE_LOCK", hud)
        self.assertIn("RENDERER_TILE_SELECTION", hud)
        self.assertIn("DIAGNOSTIC_DIGIT_ZERO_TILE", sprites)
        self.assertIn("RendererAppendActorPair", sprites)

    def test_quiet_floor_and_semantic_plane_marks_are_explicit(self) -> None:
        core = (ROOT / "src/render/gameplay_renderer_core.asm").read_text(
            encoding="utf-8"
        )
        floor = core.split(".floor:", 1)[1].split(".wall:", 1)[0]
        self.assertNotIn("eor.w", floor)
        self.assertIn("andi.w  #$0007,d0", floor)
        self.assertIn("andi.w  #$0003,d0", floor)
        self.assertIn("cmpi.w  #13,d6", floor)
        self.assertIn("cmpi.w  #16,d6", floor)
        self.assertIn(
            "#RENDERER_ATTR_PALETTE_2+RENDERER_TILE_TERMINAL_PAD",
            core,
        )
        self.assertIn(
            "#RENDERER_ATTR_PALETTE_3+RENDERER_TILE_HAZARD_PAD",
            core,
        )
        self.assertIn("RENDERER_TILE_WALL_SHADOW_H", core)
        self.assertIn("RENDERER_TILE_WALL_SHADOW_V", core)
        self.assertIn("RENDERER_TILE_ROUTE_CORE", core)
        self.assertIn("RENDERER_ATTR_VFLIP", core)

    def test_actor_motion_uses_24x24_frames_without_losing_badges(self) -> None:
        sprites = (ROOT / "src/render/gameplay_renderer_sprites.asm").read_text(
            encoding="utf-8"
        )
        actor_pair = sprites.split("RendererAppendActorPair:", 1)[1].split(
            "RendererTryAppendSprite:",
            1,
        )[0]
        self.assertIn("(ACTOR_VX,a2)", actor_pair)
        self.assertIn("(ACTOR_VY,a2)", actor_pair)
        self.assertIn("GAMEPLAY_ACTOR_FLAG_FACING_LEFT", actor_pair)
        self.assertIn("RENDERER_ATTR_HFLIP", actor_pair)
        self.assertIn("mulu.w  #RENDERER_ACTOR_FRAME_TILES,d2", actor_pair)
        self.assertIn("RENDERER_SPRITE_SIZE_3X3", actor_pair)
        self.assertEqual(actor_pair.count("subi.w  #12"), 2)
        self.assertIn("subi.w  #24,d1", actor_pair)
        self.assertIn("mulu.w  #5,d2", actor_pair)
        self.assertIn("RENDERER_SPRITE_SIZE_1X1", actor_pair)
        self.assertIn("RENDERER_RESET_PHASE", actor_pair)
        self.assertIn("RENDERER_TILE_ECHO_RESET", actor_pair)
        self.assertEqual(actor_pair.count("cmpa.l  a0,a2"), 4)
        self.assertNotIn("cmpi.w  #RENDERER_ATTR_PALETTE_1", actor_pair)
        self.assertNotIn("cmpi.w  #RENDERER_ATTR_PALETTE_2", actor_pair)
        self.assertIn(
            "#VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED,d0",
            actor_pair,
        )
        self.assertIn("#RENDERER_HAZARD_ATTRIBUTE,d5", actor_pair)
        self.assertLess(
            actor_pair.index("RENDERER_TILE_ECHO_RESET"),
            actor_pair.index("(ACTOR_VX,a2)"),
        )
        self.assertIn("RENDERER_RESET_ECHO_FAN_STEP", actor_pair)
        self.assertIn("RENDERER_RESET_ECHO_FAN_LEFT", actor_pair)
        self.assertIn("RendererDoorRejectionActive", actor_pair)
        self.assertIn("RENDERER_BLOCKED_ACTOR_OFFSET", actor_pair)
        self.assertIn("RENDERER_DOOR_MID_X_16_8", actor_pair)
        self.assertEqual(actor_pair.count("add.w   (a7),d0"), 2)
        self.assertEqual(
            actor_pair.count("bsr.w   RendererTryAppendSprite"),
            2,
        )
        self.assertIn(
            "#RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_CARRYABLE",
            sprites,
        )

    def test_visual_offsets_are_bounded_reset_and_blocked_presentations(self) -> None:
        symbols = parse_equ_contract()
        left = symbols["RENDERER_RESET_ECHO_FAN_LEFT"]
        step = symbols["RENDERER_RESET_ECHO_FAN_STEP"]
        offsets = [
            (ordinal * step) - left
            for ordinal in range(symbols["GAMEPLAY_ECHO_LIMIT"])
        ]
        self.assertEqual(offsets, [-10, -6, -2, 2, 6, 10])
        self.assertEqual(max(map(abs, offsets)), 10)
        self.assertEqual(symbols["RENDERER_BLOCKED_ACTOR_OFFSET"], 8)
        self.assertLessEqual(symbols["RENDERER_BLOCKED_ACTOR_OFFSET"], 12)

    def test_palette_language_and_reset_scan_are_transparent_and_bounded(self) -> None:
        assets = (ROOT / "src/render/gameplay_renderer_assets.asm").read_text(
            encoding="utf-8"
        )
        palette_source = assets.split("RendererPalette:", 1)[1].split(
            "RendererPaletteEnd:",
            1,
        )[0]
        palette_words = [
            int(value, 16)
            for value in re.findall(r"\$([0-9A-Fa-f]{4})", palette_source)
        ]
        self.assertEqual(len(palette_words), 64)
        palette_0 = palette_words[0:16]
        palette_1 = palette_words[16:32]
        palette_2 = palette_words[32:48]
        palette_3 = palette_words[48:64]
        self.assertEqual(palette_0[2], 0x0000)  # true-black quiet floor
        self.assertNotEqual(palette_0[3], 0x0000)  # route seam survives
        self.assertNotEqual(palette_0[4], 0x0000)  # wall depth survives
        self.assertEqual(palette_1[1], 0x0EEE)  # bone current
        self.assertEqual(palette_2[1:5], [0x0EC4, 0x0C62, 0x0A42, 0x0822])
        self.assertEqual(
            palette_3[8:14],
            [0x0008, 0x002A, 0x004C, 0x006E, 0x028E, 0x04AE],
        )
        self.assertEqual(palette_3[14], 0x0EE0)

        core = (ROOT / "src/render/gameplay_renderer_core.asm").read_text(
            encoding="utf-8"
        )
        reset_row = core.split("RendererBuildResetOverlayRows:", 1)[1].split(
            "RendererBuildCompletionRows:",
            1,
        )[0]
        self.assertIn("RendererBuildOverlayClearRows", reset_row)
        self.assertEqual(reset_row.count("RENDERER_TILE_TRACE"), 2)
        self.assertIn("RENDERER_RESET_ATTRIBUTE", reset_row)
        self.assertIn("RENDERER_ATTR_HFLIP", reset_row)
        symbols = parse_equ_contract()
        self.assertEqual(
            symbols["RENDERER_RESET_ATTRIBUTE"]
            & symbols["RENDERER_ATTR_PRIORITY"],
            0,
        )
        self.assertNotEqual(
            symbols["RENDERER_ECHO_ATTRIBUTE"]
            & symbols["RENDERER_ATTR_PRIORITY"],
            0,
        )
        self.assertNotEqual(
            symbols["RENDERER_RESET_HUD_ATTRIBUTE"]
            & symbols["RENDERER_ATTR_PRIORITY"],
            0,
        )
        for attribute in (
            "RENDERER_CURRENT_ATTRIBUTE",
            "RENDERER_ECHO_ATTRIBUTE",
            "RENDERER_OBJECTIVE_ATTRIBUTE",
            "RENDERER_DATA_ATTRIBUTE",
            "RENDERER_HAZARD_ATTRIBUTE",
        ):
            self.assertNotEqual(
                symbols[attribute] & symbols["RENDERER_ATTR_PRIORITY"],
                0,
                attribute,
            )

    def test_completion_stages_two_world_rows_and_clears_before_other_states(
        self,
    ) -> None:
        core = (ROOT / "src/render/gameplay_renderer_core.asm").read_text(
            encoding="utf-8"
        )
        completion = core.split("RendererBuildCompletionRows:", 1)[1].split(
            "RendererPrepareResetOverlay:",
            1,
        )[0]
        self.assertIn("RendererWorldStableLine0", completion)
        self.assertIn("RendererWorldStableLine1", completion)
        self.assertIn("RENDERER_OVERLAY_CLEAR_ROW", completion)
        self.assertIn("RENDERER_OVERLAY_TRACE_ROW", completion)
        self.assertEqual(completion.count("RENDERER_TILE_DASH"), 8)

        staging = core.split("RendererPrepareResetOverlay:", 1)[1]
        stable_index = staging.index("GAMEPLAY_FLAG_PROGRAM_STABLE")
        staged_clear_index = staging.index("RENDERER_FLAG_COMPLETION_STAGED")
        reset_index = staging.index("RendererBuildResetOverlayRows")
        self.assertLess(stable_index, staged_clear_index)
        self.assertLess(staged_clear_index, reset_index)
        self.assertIn("RENDERER_COMPLETION_FIRST_ROW", staging)
        self.assertIn("RENDERER_COMPLETION_SECOND_ROW", staging)
        self.assertIn("RendererBuildOverlayClearRows", staging)

    def test_closed_door_feedback_is_spatial_read_only_and_bounded(self) -> None:
        sprites = (ROOT / "src/render/gameplay_renderer_sprites.asm").read_text(
            encoding="utf-8"
        )
        predicate = sprites.split("RendererDoorRejectionActive:", 1)[1].split(
            "RendererAppendActorPair:",
            1,
        )[0]
        self.assertIn("(B01_DEVICE+OBJECT_STATE).l", predicate)
        self.assertIn("(ACTOR_X,a0)", predicate)
        self.assertIn("(ACTOR_Y,a0)", predicate)
        self.assertIn("RENDERER_DOOR_FEEDBACK_MIN_X_16_8", predicate)
        self.assertIn("RENDERER_DOOR_FEEDBACK_MAX_X_16_8", predicate)
        self.assertIn("RENDERER_DOOR_FEEDBACK_MIN_Y_16_8", predicate)
        self.assertIn("RENDERER_DOOR_FEEDBACK_MAX_Y_16_8", predicate)
        self.assertNotRegex(
            predicate,
            r"move\.[bwl]\s+[^,\n]+,\s*\((?:B01_DEVICE|GAMEPLAY_)",
        )

        sat = sprites.split("RendererBuildSat:", 1)[1].split(
            "RendererDoorRejectionActive:",
            1,
        )[0]
        legacy_sat = sat.split("\n.campaign:", 1)[0]
        self.assertIn("RENDERER_TILE_DOOR_REJECT_0", sat)
        self.assertLess(
            sat.index("RENDERER_TILE_DOOR_REJECT_0"),
            sat.index(".door_block:"),
        )
        self.assertIn("move.w  #112,d0", sat)
        self.assertIn("move.w  #120,d0", sat)
        self.assertEqual(legacy_sat.count("RENDERER_SPRITE_SIZE_1X1"), 4)
        self.assertIn("RENDERER_ATTR_HFLIP", sat)
        self.assertIn("RENDERER_PROJECTILE_LIMIT", sat)

    def test_sprite_admission_checks_both_h40_limits(self) -> None:
        sprites = (ROOT / "src/render/gameplay_renderer_sprites.asm").read_text(
            encoding="utf-8"
        )
        admission = sprites.split("RendererTryAppendSprite:", 1)[1]
        self.assertIn("RENDERER_SCANLINE_SPRITE_LIMIT", admission)
        self.assertIn("RENDERER_SCANLINE_PIXEL_LIMIT", admission)
        self.assertIn("RENDERER_SCANLINE_COUNT_BASE", admission)
        self.assertIn("RENDERER_SCANLINE_PIXEL_BASE", admission)
        self.assertNotIn(".check_scanline:", admission)
        self.assertNotIn(".commit_scanline:", admission)
        self.assertIn(".mutate_scanline:", admission)
        self.assertIn(".rollback_scanlines:", admission)
        self.assertEqual(admission.count("dbf     d1,.mutate_scanline"), 1)
        self.assertLess(
            admission.index(".mutate_scanline:"),
            admission.index(".rollback_scanlines:"),
        )
        transaction = admission.split(".mutate_scanline:", 1)[1].split(
            "; Publish the local maxima",
            1,
        )[0]
        self.assertNotIn("(RENDERER_SCANLINE_MAX).l", transaction)
        self.assertNotIn("(RENDERER_SCANLINE_PIXEL_MAX).l", transaction)
        self.assertIn(
            "cmpi.b  #RENDERER_SCANLINE_SPRITE_LIMIT,d0", transaction
        )
        self.assertIn("addq.b  #1,d0", transaction)
        self.assertIn("cmp.b   d2,d0", transaction)
        self.assertIn("move.b  d0,d2", transaction)
        self.assertNotIn("moveq   #0,d0", transaction)
        self.assertIn("addq.b  #1,(a2)+", transaction)
        self.assertIn("move.w  d0,(a3)+", transaction)
        self.assertNotIn("addq.l  #1,a2", transaction)
        self.assertNotIn("addq.l  #2,a3", transaction)
        self.assertLess(
            transaction.index("bhi.w   .rollback_scanlines"),
            transaction.index("addq.b  #1,(a2)+"),
        )
        self.assertIn("subq.b  #1,(a2)", admission)
        self.assertIn("sub.w   d3,(a3)", admission)
        self.assertLess(
            admission.index("; Publish the local maxima"),
            admission.index("; Append with a zero link"),
        )
        self.assertIn("RENDERER_FLAG_SPRITE_DROPPED", admission)

    def test_sat_and_contiguous_ledgers_use_zeroed_burst_clears(self) -> None:
        sprites = (ROOT / "src/render/gameplay_renderer_sprites.asm").read_text(
            encoding="utf-8"
        )
        build = sprites.split("RendererBuildSat:", 1)[1].split(
            "; Current incarnation",
            1,
        )[0]
        self.assertEqual(build.count("movem.l d0-d6/a1,(a0)"), 2)
        self.assertIn("#(SAT_SHADOW_BYTES/32)-1,d7", build)
        self.assertIn(
            "#((RENDERER_SCANLINE_COUNT_BYTES+"
            "RENDERER_SCANLINE_PIXEL_BYTES)/32)-1,d7",
            build,
        )
        self.assertIn("movea.l d0,a1", build)
        for register in range(7):
            self.assertIn(f"moveq   #0,d{register}", build)
        self.assertNotIn(".clear_counts:", build)
        self.assertNotIn(".clear_pixels:", build)
        self.assertEqual(build.count("lea     32(a0),a0"), 2)

    def test_renderer_assets_are_authored_data_not_generated_placeholders(self) -> None:
        assets = (ROOT / "src/render/gameplay_renderer_assets.asm").read_text(
            encoding="utf-8"
        )
        self.assertIn("RendererPalette:", assets)
        self.assertIn("RendererTileData:", assets)
        self.assertIn("renderer tile data size mismatch", assets)
        self.assertNotIn("incbin", assets.lower())
        self.assertNotIn("assets/generated", assets)

    def test_tile_art_keeps_floor_quiet_and_gameplay_shapes_distinct(self) -> None:
        assets = (ROOT / "src/render/gameplay_renderer_assets.asm").read_text(
            encoding="utf-8"
        )
        tile_source = assets.split("RendererTileData:", 1)[1].split(
            "RendererTileDataEnd:",
            1,
        )[0]
        tile_words = [
            int(value, 16)
            for value in re.findall(r"\$([0-9A-Fa-f]{8})", tile_source)
        ]
        symbols = parse_equ_contract()
        self.assertEqual(len(tile_words), symbols["RENDERER_TILE_COUNT"] * 8)
        tiles = [
            tuple(tile_words[offset : offset + 8])
            for offset in range(0, len(tile_words), 8)
        ]
        base = symbols["RENDERER_TILE_BASE"]

        def art(tile_name: str, count: int) -> tuple[tuple[int, ...], ...]:
            start = symbols[tile_name] - base
            return tuple(tiles[start : start + count])

        floor_a = art("RENDERER_TILE_FLOOR_A", 1)[0]
        floor_b = art("RENDERER_TILE_FLOOR_B", 1)[0]
        self.assertEqual(set(floor_a), {0x22222222})
        self.assertNotIn(0, floor_b)
        self.assertLessEqual(len(set(floor_b)), 3)

        current_0 = art("RENDERER_TILE_CURRENT_0", 9)
        current_1 = art("RENDERER_TILE_CURRENT_1", 9)
        echo_0 = art("RENDERER_TILE_ECHO_0", 9)
        echo_1 = art("RENDERER_TILE_ECHO_1", 9)
        echo_reset = art("RENDERER_TILE_ECHO_RESET", 9)
        hazard = art("RENDERER_TILE_HAZARD", 8)
        enemy = art("RENDERER_TILE_ENEMY", 4)
        carryable = art("RENDERER_TILE_CARRYABLE", 4)
        terminal = art("RENDERER_TILE_TERMINAL", 4)
        self.assertNotEqual(current_0, current_1)
        self.assertNotEqual(echo_0, echo_1)
        self.assertNotEqual(echo_reset, echo_0)
        self.assertNotEqual(echo_reset, echo_1)
        self.assertNotEqual(current_0, echo_0)
        self.assertEqual(hazard[:4], hazard[4:])
        self.assertEqual(
            len({current_0, echo_0, hazard, enemy, carryable, terminal}),
            6,
        )
        self.assertTrue(all(any(row != 0 for row in tile) for tile in carryable))

        def raster(frame: tuple[tuple[int, ...], ...]) -> tuple[str, ...]:
            rows = [["0"] * 24 for _ in range(24)]
            for tile_index, tile in enumerate(frame):
                tile_x = tile_index // 3
                tile_y = tile_index % 3
                for row_index, word in enumerate(tile):
                    pixels = f"{word:08X}"
                    start = tile_x * 8
                    rows[tile_y * 8 + row_index][start : start + 8] = pixels
            return tuple("".join(row) for row in rows)

        current_pixels = raster(current_0)
        echo_pixels = raster(echo_0)
        reset_echo_pixels = raster(echo_reset)
        self.assertEqual(sum(pixel != "0" for pixel in current_pixels[-1]), 16)
        self.assertEqual(sum(pixel != "0" for pixel in echo_pixels[-1]), 10)
        self.assertGreater(
            sum(pixel == "0" for row in echo_pixels for pixel in row),
            sum(pixel == "0" for row in current_pixels for pixel in row),
        )
        current_mask = tuple(
            pixel != "0" for row in current_pixels for pixel in row
        )
        reset_echo_mask = tuple(
            pixel != "0" for row in reset_echo_pixels for pixel in row
        )
        self.assertGreaterEqual(
            sum(
                current != reset
                for current, reset in zip(current_mask, reset_echo_mask)
            ),
            256,
        )
        self.assertEqual(
            sum(pixel != "0" for pixel in reset_echo_pixels[12][:12]),
            12,
        )
        self.assertGreaterEqual(
            sum(reset_echo_pixels[row][23] != "0" for row in range(2, 22)),
            20,
        )
        self.assertGreater(
            sum(pixel == "0" for row in reset_echo_pixels for pixel in row),
            400,
        )
        self.assertEqual(
            sum(pixel == "5" for row in current_pixels for pixel in row[:12]),
            0,
        )
        self.assertGreater(
            sum(pixel == "5" for row in current_pixels for pixel in row[12:]),
            0,
        )

        trace = art("RENDERER_TILE_TRACE", 1)[0]
        trace_pixels = "".join(f"{word:08X}" for word in trace)
        self.assertIn("0", trace_pixels)
        self.assertGreater(trace_pixels.count("0"), len(trace_pixels) // 2)
        self.assertNotEqual(
            art("RENDERER_TILE_WALL_SHADOW_H", 1),
            art("RENDERER_TILE_WALL_SHADOW_V", 1),
        )
        self.assertNotEqual(
            art("RENDERER_TILE_ROUTE_CORE", 1),
            art("RENDERER_TILE_FLOOR_B", 1),
        )
        self.assertNotEqual(
            art("RENDERER_TILE_DOOR_REJECT_0", 1),
            art("RENDERER_TILE_DOOR_REJECT_1", 1),
        )
        for reject_tile in (
            art("RENDERER_TILE_DOOR_REJECT_0", 1)[0],
            art("RENDERER_TILE_DOOR_REJECT_1", 1)[0],
        ):
            reject_pixels = "".join(f"{word:08X}" for word in reject_tile)
            self.assertGreaterEqual(
                sum(pixel != "0" for pixel in reject_pixels),
                len(reject_pixels) // 2,
            )

    def test_renderer_translation_unit_owns_only_renderer_includes(self) -> None:
        unit = (ROOT / "src/render/gameplay_renderer.asm").read_text(encoding="utf-8")
        includes = re.findall(r'include\s+"([^"]+)"', unit)
        self.assertEqual(
            includes,
            [
                "render/gameplay_renderer_layout.asm",
                "render/gameplay_renderer_assets.asm",
                "render/gameplay_renderer_hud.asm",
                "render/gameplay_renderer_sprites.asm",
                "render/gameplay_renderer_core.asm",
            ],
        )


class RendererAssemblyTests(unittest.TestCase):
    def test_standalone_vasm_is_zero_warning_and_link_complete(self) -> None:
        vasm = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(vasm.is_file(), f"missing pinned assembler: {vasm}")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="renderer-contract-", dir=ROOT / "build"
        ) as temporary:
            output = Path(temporary) / "renderer.bin"
            relative_output = output.relative_to(ROOT).as_posix()
            command = [
                str(vasm),
                "-Fbin",
                "-m68000",
                "-no-opt",
                "-x",
                "-Isrc",
                "-o",
                relative_output,
                "tests/render/renderer_standalone.asm",
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIsNone(WARNING_RE.search(completed.stdout), completed.stdout)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 4096)
            self.assertLess(output.stat().st_size, 12288)


if __name__ == "__main__":
    unittest.main()
