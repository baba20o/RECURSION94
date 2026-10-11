from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "src/gameplay/campaign_collision.asm"
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")
WARNING_RE = re.compile(r"(?i)(?:^|\s)warning(?:\s+\w+)?\s*(?::|\[)")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class CampaignCollisionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")

    def test_axis_helpers_have_stable_candidate_return_abi(self) -> None:
        for axis in ("X", "Y"):
            label = f"GameplayCampaignResolveDoor{axis}"
            routine = _routine(self.source, label)
            self.assertIn("move.l  d0,d3", routine)
            self.assertIn("move.l  d1,d4", routine)
            self.assertIn("move.l  d3,d0", routine)
            self.assertIn("move.l  a0,d1", routine)
            self.assertIn("movem.l d3-d7/a2-a3,-(a7)", routine)
            self.assertIn("movem.l (a7)+,d3-d7/a2-a3", routine)

    def test_doors_are_filtered_in_a_bounded_physical_order_scan(self) -> None:
        for axis in ("X", "Y"):
            routine = _routine(
                self.source, f"GameplayCampaignResolveDoor{axis}"
            )
            scan_setup = "\n".join(
                (
                    "        lea     (DEVICE_POOL).l,a3",
                    "        moveq   #0,d7",
                    "        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d7",
                    "        cmpi.w  #DEVICE_CAPACITY,d7",
                    "        bls.s   .door_bound_ready",
                    "        moveq   #DEVICE_CAPACITY,d7",
                    ".door_bound_ready:",
                    "        tst.w   d7",
                    "        beq.w   .done",
                    "        subq.w  #1,d7",
                    ".door:",
                )
            )
            self.assertIn(scan_setup, routine)
            self.assertNotIn("moveq   #DEVICE_CAPACITY-1,d7", routine)
            self.assertEqual(routine.count("dbf     d7,.door"), 1)
            self.assertIn("(OBJECT_ACTIVE,a3)", routine)
            self.assertIn("#VOCAB_DEVICE_DOOR", routine)
            self.assertIn("#VOCAB_DEVICE_STATE_DISABLED", routine)
            self.assertIn("#VOCAB_DEVICE_STATE_OPEN", routine)

    def test_phase_and_dash_do_not_bypass_closed_puzzle_doors(self) -> None:
        for axis in ("X", "Y"):
            routine = _routine(
                self.source, f"GameplayCampaignResolveDoor{axis}"
            )
            self.assertNotIn("#VOCAB_ACTOR_FLAG_DASHING", routine)
            self.assertNotIn("#VOCAB_ACTOR_FLAG_PHASED", routine)
            self.assertNotIn("ENGINE_ROOM_", routine)
            self.assertNotIn("CANONICAL_RECTS", routine)

        preamble = self.source.split("GameplayCampaignResolveDoorX:", 1)[0]
        self.assertIn("physical rectangle zero", preamble)
        self.assertIn("room", preamble)
        self.assertIn("room/static collision", preamble)
        self.assertIn("never bypasses a closed puzzle", preamble)
        self.assertNotIn("B01_DEVICE", self.source)

    def test_door_aabb_matches_rendered_leaf_and_publishes_collision(self) -> None:
        self.assertIn(
            "CAMPAIGN_DOOR_WIDTH_16_8        equ     16*256",
            self.source,
        )
        self.assertIn(
            "CAMPAIGN_DOOR_HEIGHT_16_8       equ     32*256",
            self.source,
        )
        self.assertIn(
            "top-left is OBJECT_X/OBJECT_Y",
            self.source,
        )
        x_axis = _routine(self.source, "GameplayCampaignResolveDoorX")
        y_axis = _routine(self.source, "GameplayCampaignResolveDoorY")
        self.assertIn("#ACTOR_FLAG_COLLISION_X", x_axis)
        self.assertIn("#ACTOR_FLAG_COLLISION_Y", y_axis)
        for routine in (x_axis, y_axis):
            self.assertIn("#ENGINE_FLAG_COLLISION", routine)
            self.assertGreaterEqual(routine.count("ble.s   .next"), 2)
            self.assertGreaterEqual(routine.count("bge.s   .next"), 2)

    def test_projectile_world_query_clamps_scans_rectangles_and_closed_doors(
        self,
    ) -> None:
        routine = _routine(
            self.source, "GameplayCampaignProjectileHitsStatic"
        )
        self.assertIn("#VOCAB_PROJECTILE_HALF_EXTENT_16_8", routine)
        self.assertIn("#STATIC_RECT_CAPACITY,d7", routine)
        self.assertIn("lea     (CANONICAL_RECTS).l,a2", routine)
        self.assertIn("dbf     d7,.rectangle", routine)
        self.assertIn("lea     (DEVICE_POOL).l,a2", routine)
        self.assertIn("dbf     d7,.door", routine)
        self.assertIn("#VOCAB_DEVICE_STATE_OPEN", routine)
        self.assertIn("#CAMPAIGN_DOOR_WIDTH_16_8", routine)
        self.assertIn("#CAMPAIGN_DOOR_HEIGHT_16_8", routine)
        self.assertNotIn("B01_DEVICE", routine)
        self.assertNotIn("adda.w  #STATIC_RECT_BYTES,a2\n        subq", routine)

    def test_wall_mounted_exposed_nodes_have_a_narrow_friendly_aperture(
        self,
    ) -> None:
        aperture = _routine(
            self.source, "GameplayCampaignProjectileMayEnterNodeRect"
        )
        self.assertIn(
            "VOCAB_PROJECTILE_STATE_PLAYER|VOCAB_PROJECTILE_STATE_REFLECTED",
            aperture,
        )
        self.assertIn("lea     (DEVICE_POOL).l,a4", aperture)
        self.assertIn("moveq   #DEVICE_CAPACITY-1,d7", aperture)
        self.assertIn("cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0", aperture)
        self.assertIn("VOCAB_DEVICE_STATE_DISABLED", aperture)
        self.assertIn("VOCAB_DEVICE_STATE_ACTIVE", aperture)
        self.assertIn("(STATIC_RECT_X,a3)", aperture)
        self.assertIn("(STATIC_RECT_Y,a3)", aperture)
        self.assertIn("(STATIC_RECT_WIDTH,a3)", aperture)
        self.assertIn("(STATIC_RECT_HEIGHT,a3)", aperture)
        self.assertEqual(
            aperture.count(
                "cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0"
            ),
            2,
        )
        self.assertIn("tst.l   (OBJECT_VY,a2)", aperture)
        self.assertIn("move.l  (OBJECT_VY,a2),d5", aperture)

        collision = _routine(
            self.source, "GameplayCampaignProjectileHitsStatic"
        )
        self.assertIn(
            "bsr.w   GameplayCampaignProjectileMayEnterNodeRect",
            collision,
        )
        self.assertLess(
            collision.index(
                "bsr.w   GameplayCampaignProjectileMayEnterNodeRect"
            ),
            collision.index("bra.s   .hit"),
        )

    def test_standalone_module_is_warning_free_and_link_complete(self) -> None:
        assembler = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(assembler.is_file(), assembler)
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="m5-campaign-collision-", dir=ROOT / "build"
        ) as temporary:
            temporary_path = Path(temporary)
            wrapper = temporary_path / "campaign_collision_standalone.asm"
            output = temporary_path / "campaign_collision.bin"
            wrapper.write_text(
                "\n".join(
                    (
                        '        include "game/engine_layout.asm"',
                        '        include "gameplay/gameplay_layout.asm"',
                        '        include "gameplay/vocabulary_layout.asm"',
                        "",
                        "        org     $000000",
                        "",
                        '        include "gameplay/campaign_collision.asm"',
                        "",
                        "GameplayMoveActorAxisSeparated:",
                        "        rts",
                        "",
                        "VocabularyWithinRange:",
                        "        rts",
                        "",
                    )
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    str(assembler),
                    "-Fbin",
                    "-m68000",
                    "-no-opt",
                    "-x",
                    "-Isrc",
                    "-o",
                    output.relative_to(ROOT).as_posix(),
                    wrapper.relative_to(ROOT).as_posix(),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIsNone(WARNING_RE.search(completed.stdout), completed.stdout)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 512)
            self.assertLess(output.stat().st_size, 8192)


if __name__ == "__main__":
    unittest.main()
