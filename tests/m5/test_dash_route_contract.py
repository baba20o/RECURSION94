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


class DashRouteContractTests(unittest.TestCase):
    def test_dash_velocity_is_sustained_for_every_flagged_motion_step(self) -> None:
        routine = _routine(
            "src/gameplay/vocabulary_core.asm", "VocabularyApplyDashPhase"
        )
        preserve_pressed = routine.index("move.b  d1,d6")
        extension = routine.index("bsr.w   VocabularyGetActorExtension")
        edge_test = routine.index("andi.b  #INPUT_C,d6", extension)
        running = routine.index("#VOCAB_ACTOR_FLAG_DASHING,d0", edge_test)
        apply_velocity = routine.index(".apply_velocity:", running)
        load_velocity = routine.index("move.l  (ACTOR_VX,a2),d3", apply_velocity)

        self.assertLess(preserve_pressed, extension)
        self.assertLess(extension, edge_test)
        self.assertLess(edge_test, running)
        self.assertLess(running, apply_velocity)
        self.assertLess(apply_velocity, load_velocity)
        self.assertEqual(
            routine.count("move.b  d1,d6"),
            1,
            "the C-edge mask must be captured once, before the helper clobbers d1",
        )
        self.assertIn("beq.w   .no_action", routine[edge_test:apply_velocity])
        self.assertIn("bne.s   .apply_velocity", routine[edge_test:apply_velocity])

    def test_dash_timers_expire_after_hazard_resolution(self) -> None:
        step = _routine("src/gameplay/gameplay_core.asm", "GameplayStep")
        hazards = step.index("bsr.w   VocabularyUpdateActorsAndHazards")
        timers = step.index("bsr.w   VocabularyTickActorExtensions", hazards)
        transient = step.index("bsr.w   EngineUpdateTransientPools", timers)
        self.assertLess(hazards, timers)
        self.assertLess(timers, transient)

    def test_only_full_current_dash_crossing_latches_authored_route(self) -> None:
        helper = _routine(
            "src/gameplay/campaign_collision.asm",
            "GameplayCampaignLatchDashDoorRoute",
        )

        self.assertIn("#GAMEPLAY_CURRENT_ACTOR", helper)
        self.assertIn("#VOCAB_DEVICE_STATE_DASH_ROUTE", helper)
        self.assertIn(
            "#VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED", helper
        )
        self.assertIn("#CAMPAIGN_DOOR_HEIGHT_16_8", helper)
        self.assertIn("#CAMPAIGN_DOOR_WIDTH_16_8", helper)
        self.assertIn("(ACTOR_HALF_WIDTH,a2)", helper)
        self.assertIn("lea     (DEVICE_POOL).l,a0", helper)
        self.assertIn("moveq   #DEVICE_CAPACITY-1,d7", helper)
        self.assertIn("dbf     d7,.completion", helper)
        self.assertIn(
            "ori.w   #VOCAB_DEVICE_STATE_DASH_ROUTE,(OBJECT_STATE,a0)",
            helper,
        )

        resolve = _routine(
            "src/gameplay/campaign_collision.asm",
            "GameplayCampaignResolveDoorX",
        )
        opened = resolve.index("#VOCAB_DEVICE_STATE_OPEN")
        call = resolve.index(
            "bsr.w   GameplayCampaignLatchDashDoorRoute", opened
        )
        closed = resolve.index(".closed:", call)
        self.assertLess(opened, call)
        self.assertLess(call, closed)

    def test_b03_completion_mask_requires_unlock_hack_and_dash_route(self) -> None:
        import json

        encounter = json.loads(
            (ROOT / "content/encounters/boot/B03.json").read_text(
                encoding="utf-8"
            )
        )
        state = encounter["stage_completion"]["completion_state"]
        self.assertEqual(state["mask"], 0x1007)
        self.assertEqual(state["value"], 0x1007)


if __name__ == "__main__":
    unittest.main()
