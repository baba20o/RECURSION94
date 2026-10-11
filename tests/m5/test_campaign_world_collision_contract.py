from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _routine(path: str, label: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8")
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class CampaignWorldCollisionContractTests(unittest.TestCase):
    def test_conveyor_displacement_uses_authoritative_actor_collision(self) -> None:
        helper = _routine(
            "src/gameplay/campaign_collision.asm",
            "GameplayCampaignMoveActorByDelta",
        )
        self.assertIn("bsr.w   GameplayMoveActorAxisSeparated", helper)
        self.assertIn("move.l  d3,(ACTOR_VX,a2)", helper)
        self.assertIn("move.l  d4,(ACTOR_VY,a2)", helper)

        devices = _routine(
            "src/gameplay/vocabulary_interactions.asm",
            "VocabularyUpdateDevices",
        ).split(".conveyor:", 1)[1]
        self.assertIn("bsr.w   GameplayCampaignMoveActorByDelta", devices)
        self.assertNotIn("addi.l  #VOCAB_CONVEYOR_SPEED,(ACTOR_", devices)
        self.assertNotIn("subi.l  #VOCAB_CONVEYOR_SPEED,(ACTOR_", devices)

        hazards = _routine(
            "src/gameplay/vocabulary_combat.asm",
            "VocabularyUpdateActorsAndHazards",
        )
        conveyor = hazards.split(".hazard_conveyor:", 1)[1].split(
            ".damage_hazard:", 1
        )[0]
        self.assertIn(
            "bsr.w   GameplayCampaignMoveActorByDelta", conveyor
        )
        self.assertNotIn("addi.l  #VOCAB_CONVEYOR_SPEED,(ACTOR_", conveyor)
        self.assertNotIn("subi.l  #VOCAB_CONVEYOR_SPEED,(ACTOR_", conveyor)

    def test_enemy_motion_stops_at_bounds_rectangles_and_closed_doors(self) -> None:
        blocked = _routine(
            "src/gameplay/campaign_collision.asm",
            "GameplayCampaignObjectBlocked",
        )
        for field in (
            "ENGINE_ROOM_LEFT",
            "ENGINE_ROOM_RIGHT",
            "ENGINE_ROOM_TOP",
            "ENGINE_ROOM_BOTTOM",
        ):
            self.assertIn(field, blocked)
        self.assertIn("lea     (CANONICAL_RECTS).l,a0", blocked)
        self.assertIn("dbf     d7,.rect", blocked)
        self.assertIn("lea     (DEVICE_POOL).l,a0", blocked)
        self.assertIn("#VOCAB_DEVICE_DOOR", blocked)
        self.assertIn("#VOCAB_DEVICE_STATE_OPEN", blocked)
        self.assertIn("dbf     d7,.door", blocked)

        movement = _routine(
            "src/gameplay/campaign_collision.asm",
            "GameplayCampaignMoveObjectAxisSeparated",
        )
        self.assertEqual(
            movement.count("bsr.w   GameplayCampaignObjectBlocked"), 2
        )
        enemies = _routine(
            "src/gameplay/vocabulary_combat.asm",
            "VocabularyUpdateEnemies",
        )
        self.assertIn(
            "bsr.w   GameplayCampaignMoveObjectAxisSeparated", enemies
        )
        self.assertNotIn("add.l   d0,(OBJECT_X,a2)", enemies)
        self.assertNotIn("add.l   d0,(OBJECT_Y,a2)", enemies)

    def test_beam_cache_clips_before_reflection_and_after_redirect(self) -> None:
        clip = _routine(
            "src/gameplay/campaign_collision.asm",
            "GameplayCampaignClipBeam",
        )
        self.assertIn("lea     (CANONICAL_RECTS).l,a3", clip)
        self.assertIn("dbf     d5,.rect", clip)
        self.assertIn("lea     (DEVICE_POOL).l,a3", clip)
        self.assertIn("#VOCAB_DEVICE_DOOR", clip)
        self.assertIn("#VOCAB_DEVICE_STATE_OPEN", clip)
        self.assertIn("#VOCAB_BEAM_FLAG_BLOCKED", clip)
        self.assertIn("move.b  d7,(VOCAB_BEAM_LENGTH,a2)", clip)

        cache = _routine(
            "src/gameplay/vocabulary_interactions.asm",
            "VocabularyBuildBeamCache",
        )
        self.assertEqual(
            cache.count("bsr.w   GameplayCampaignClipBeam"), 2
        )
        reflector = cache.split(".reflector_distance:", 1)[1].split(
            ".reflector_next:", 1
        )[0]
        self.assertIn("move.b  (VOCAB_BEAM_LENGTH,a4),d0", reflector)
        self.assertIn("cmp.w   d0,d1", reflector)


if __name__ == "__main__":
    unittest.main()
