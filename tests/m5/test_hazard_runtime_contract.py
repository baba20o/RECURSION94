from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tools.campaign_runtime.compiler import (
    SEMANTIC_FLAG_GEOMETRY_HEIGHT_MASK,
    SEMANTIC_FLAG_GEOMETRY_HEIGHT_SHIFT,
    SEMANTIC_FLAG_GEOMETRY_WIDTH_MASK,
    SEMANTIC_FLAG_GEOMETRY_WIDTH_SHIFT,
    build_runtime_artifacts,
)
from tools.m5_reference.manifest import (
    ENTITY_VOCABULARY_MANIFEST,
    HAZARD_STATE_WARNING,
)


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class HazardRuntimeContractTests(unittest.TestCase):
    def test_b03_rectangle_is_exactly_encoded_without_timer_aliasing(
        self,
    ) -> None:
        artifacts = build_runtime_artifacts(ROOT / "content")
        report = json.loads(
            artifacts.files["campaign_runtime_report.json"].decode("utf-8")
        )
        shutter = next(
            obj
            for encounter in report["encounters"]
            if encounter["id"] == "B03"
            for obj in encounter["objects"]
            if obj["authored_logical_id"] == "B03_SHUTTER_FIELD"
        )
        flags = shutter["semantic_record"]["flags"]
        width_units = (
            flags & SEMANTIC_FLAG_GEOMETRY_WIDTH_MASK
        ) >> SEMANTIC_FLAG_GEOMETRY_WIDTH_SHIFT
        height_units = (
            flags & SEMANTIC_FLAG_GEOMETRY_HEIGHT_MASK
        ) >> SEMANTIC_FLAG_GEOMETRY_HEIGHT_SHIFT

        self.assertEqual(shutter["object_timer"], 0)
        self.assertEqual(shutter["semantic_record"]["geometry_width"], 16)
        self.assertEqual(shutter["semantic_record"]["geometry_height"], 64)
        self.assertEqual(width_units * 8, 16)
        self.assertEqual(height_units * 8, 64)

    def test_warning_is_visible_but_distinct_from_active_closing(self) -> None:
        states = ENTITY_VOCABULARY_MANIFEST[
            "closing_door_crush_zone"
        ].state_map()
        self.assertEqual(states["safe"], 0)
        self.assertEqual(states["warning"], HAZARD_STATE_WARNING)
        self.assertEqual(states["closing"], 1)
        self.assertNotEqual(states["warning"], states["closing"])

    def test_hazard_effects_require_active_and_use_geometry_helper(self) -> None:
        hazards = _routine(
            _source("src/gameplay/vocabulary_combat.asm"),
            "VocabularyUpdateActorsAndHazards",
        ).split(".hazards:", 1)[1].split(".hazard_next:", 1)[0]
        active = hazards.index("VOCAB_DEVICE_STATE_ACTIVE")
        helper = hazards.index("GameplayCampaignActorInHazard")
        damage = hazards.index(".damage_hazard:")
        self.assertLess(active, helper)
        self.assertLess(helper, damage)
        self.assertNotIn("VocabularyWithinRange", hazards)

    def test_rectangular_collision_decodes_both_semantic_dimensions(
        self,
    ) -> None:
        routine = _routine(
            _source("src/gameplay/campaign_collision.asm"),
            "GameplayCampaignActorInHazard",
        )
        for token in (
            "VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK",
            "VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK",
            "ACTOR_HALF_WIDTH",
            "ACTOR_HALF_HEIGHT",
            "OBJECT_X",
            "OBJECT_Y",
        ):
            self.assertIn(token, routine)
        self.assertIn("VOCAB_HAZARD_RADIUS_DEFAULT", routine)
        self.assertIn("VocabularyWithinRange", routine)

    def test_shutter_controller_has_safe_warning_closing_sequence(
        self,
    ) -> None:
        routine = _routine(
            _source("src/gameplay/campaign_runtime.asm"),
            "CampaignUpdateHazardControllers",
        )
        for token in (
            "CAMPAIGN_RUNTIME_TYPE_CLOSING_DOOR_CRUSH_ZONE",
            "CAMPAIGN_RUNTIME_SEMANTIC_DURATION",
            "VOCAB_HAZARD_STATE_WARNING",
            "VOCAB_DEVICE_STATE_ACTIVE",
            "VOCAB_DEVICE_STATE_OPEN",
            "DEVICE_CAPACITY-1",
        ):
            self.assertIn(token, routine)
        scan_setup = "\n".join(
            (
                "        lea     (HAZARD_POOL).l,a2",
                "        moveq   #0,d7",
                "        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_HAZARD-1).l,d7",
                "        cmpi.w  #HAZARD_CAPACITY,d7",
                "        bls.s   .hazard_bound_ready",
                "        moveq   #HAZARD_CAPACITY,d7",
                ".hazard_bound_ready:",
                "        tst.w   d7",
                "        beq.w   .ok",
                "        subq.w  #1,d7",
                ".hazard:",
            )
        )
        self.assertIn(scan_setup, routine)
        self.assertNotIn("moveq   #HAZARD_CAPACITY-1,d7", routine)
        self.assertIn("move.w  #1,(OBJECT_TIMER,a2)", routine)
        self.assertIn("moveq   #0,d0", routine)
        self.assertIn("move.w  #VOCAB_HAZARD_STATE_WARNING,d0", routine)
        self.assertIn("moveq   #VOCAB_DEVICE_STATE_ACTIVE,d0", routine)
        self.assertNotIn("VOCAB_HAZARD_STATE_TRIGGERED", routine)

        step = _routine(
            _source("src/gameplay/gameplay_core.asm"), "GameplayStep"
        )
        second_dependencies = step.rindex("bsr.w   CampaignEvaluateDependencies")
        controller = step.index(
            "bsr.w   CampaignUpdateHazardControllers",
            second_dependencies,
        )
        damage = step.index("bsr.w   VocabularyUpdateActorsAndHazards")
        self.assertLess(second_dependencies, controller)
        self.assertLess(controller, damage)


if __name__ == "__main__":
    unittest.main()
