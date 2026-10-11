from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class DeadEchoContractTests(unittest.TestCase):
    def test_echo_playback_skips_dead_physical_actors(self) -> None:
        routine = _routine(
            _source("src/gameplay/gameplay_core.asm"), "GameplayStep"
        )
        playback = routine.split(".echo_input_ready:", 1)[1].split(
            ".simulate_current:", 1
        )[0]

        active = playback.index("tst.b   (ACTOR_ACTIVE,a2)")
        skip = playback.index("beq.s   .echo_advanced", active)
        apply_input = playback.index("bsr.w   GameplayApplyActorInput", skip)
        advance = playback.index(".echo_advanced:", apply_input)
        self.assertLess(active, skip)
        self.assertLess(skip, apply_input)
        self.assertLess(apply_input, advance)

    def test_actor_input_routine_fails_closed_for_inactive_slots(self) -> None:
        routine = _routine(
            _source("src/gameplay/actor_sim.asm"),
            "GameplayApplyActorInput",
        )

        guard = routine.index("tst.b   (ACTOR_ACTIVE,a2)")
        branch = routine.index("beq.w   .inactive", guard)
        sanitize = routine.index("bsr.w   GameplaySanitizeInput", branch)
        inactive = routine.index(".inactive:", sanitize)
        done = routine.index(".done:", inactive)
        neutral = routine[inactive:done]

        self.assertLess(guard, branch)
        self.assertLess(branch, sanitize)
        self.assertEqual(neutral.count("moveq   #0,d0"), 1)
        self.assertEqual(neutral.count("moveq   #0,d1"), 1)
        self.assertEqual(neutral.count("moveq   #0,d2"), 1)

    def test_inactive_actor_cannot_spawn_projectiles(self) -> None:
        routine = _routine(
            _source("src/game/object_pool.asm"),
            "EngineSpawnProjectileFromActor",
        )

        guard = routine.index("tst.b   (ACTOR_ACTIVE,a2)")
        reject = routine.index("beq.s   .invalid", guard)
        allocate = routine.index("bsr.w   EnginePoolAllocateLowest", reject)
        invalid = routine.index(".invalid:", allocate)
        self.assertLess(guard, reject)
        self.assertLess(reject, allocate)
        self.assertIn("moveq   #-1,d0", routine[invalid:])

    def test_inactive_actor_cannot_dash_or_interact(self) -> None:
        core = _routine(
            _source("src/gameplay/vocabulary_core.asm"),
            "VocabularyApplyDashPhase",
        )
        interactions = _routine(
            _source("src/gameplay/vocabulary_interactions.asm"),
            "VocabularyResolveInteraction",
        )

        dash_guard = core.index("tst.b   (ACTOR_ACTIVE,a2)")
        dash_reject = core.index("beq.w   .invalid", dash_guard)
        dash_mutation = core.index("#VOCAB_ACTOR_FLAG_DASHING", dash_reject)
        self.assertLess(dash_guard, dash_reject)
        self.assertLess(dash_reject, dash_mutation)

        interact_guard = interactions.index("tst.b   (ACTOR_ACTIVE,a2)")
        interact_reject = interactions.index(
            "beq.w   .invalid", interact_guard
        )
        first_pool_scan = interactions.index("(DEVICE_POOL).l", interact_reject)
        self.assertLess(interact_guard, interact_reject)
        self.assertLess(interact_reject, first_pool_scan)

    def test_dead_carrier_drops_cell_and_clears_ownership(self) -> None:
        routine = _routine(
            _source("src/gameplay/vocabulary_interactions.asm"),
            "VocabularyFollowCarryables",
        )
        guard = routine.index("tst.b   (ACTOR_ACTIVE,a2)")
        branch = routine.index("beq.s   .drop_inactive", guard)
        drop = routine.index(".drop_inactive:", branch)
        invalid = routine.index(".invalid_slot:", drop)
        body = routine[drop:invalid]

        self.assertIn("clr.w   (OBJECT_STATE,a4)", body)
        self.assertIn(
            "move.w  #VOCAB_CARRYABLE_POWER_CELL,(OBJECT_PARAM,a4)", body
        )
        self.assertIn("move.l  d0,(OBJECT_X,a4)", body)
        self.assertIn("move.l  d0,(OBJECT_Y,a4)", body)
        self.assertIn(
            "move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)", body
        )
        self.assertIn(
            "andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)", body
        )

    def test_lethal_damage_stops_actor_velocity(self) -> None:
        routine = _routine(
            _source("src/gameplay/vocabulary_combat.asm"),
            "VocabularyDamageActor",
        )
        death = routine.index("clr.b   (ACTOR_ACTIVE,a2)")
        mark_hurt = routine.index(".mark_hurt:", death)
        body = routine[death:mark_hurt]

        self.assertIn("clr.l   (ACTOR_VX,a2)", body)
        self.assertIn("clr.l   (ACTOR_VY,a2)", body)

    def test_lethal_damage_updates_count_and_drops_carryable_atomically(
        self,
    ) -> None:
        routine = _routine(
            _source("src/gameplay/vocabulary_combat.asm"),
            "VocabularyDamageActor",
        )
        death = routine.index("clr.b   (ACTOR_ACTIVE,a2)")
        mark_hurt = routine.index(".mark_hurt:", death)
        body = routine[death:mark_hurt]

        decrement = body.index(
            "subq.b  #1,(ENGINE_ACTIVE_ACTOR_COUNT).l"
        )
        preserve = body.index("move.l  a1,-(a7)")
        drop = body.index("bsr.w   VocabularyDropActorCarryable")
        restore = body.index("movea.l (a7)+,a1")
        current = body.index(
            "cmpi.w  #GAMEPLAY_CURRENT_ACTOR_SLOT,d5"
        )
        self.assertLess(decrement, preserve)
        self.assertLess(preserve, drop)
        self.assertLess(drop, restore)
        self.assertLess(drop, current)

    def test_atomic_drop_helper_clears_both_sides_of_ownership(self) -> None:
        routine = _routine(
            _source("src/gameplay/vocabulary_interactions.asm"),
            "VocabularyDropActorCarryable",
        )

        self.assertIn("clr.w   (OBJECT_STATE,a0)", routine)
        self.assertIn(
            "move.w  #VOCAB_CARRYABLE_POWER_CELL,(OBJECT_PARAM,a0)",
            routine,
        )
        self.assertIn("move.l  d0,(OBJECT_X,a0)", routine)
        self.assertIn("move.l  d0,(OBJECT_Y,a0)", routine)
        self.assertIn(
            "move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)",
            routine,
        )
        self.assertIn(
            "andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)",
            routine,
        )
        self.assertIn(
            "#VOCAB_CARRY_STATE_CARRIED|VOCAB_CARRY_STATE_SOCKETED",
            routine,
        )
        self.assertIn("cmpi.w  #VOCAB_CARRYABLE_POWER_CELL,d0", routine)
        self.assertIn("cmp.b   (OBJECT_PARAM+1,a0),d1", routine)


if __name__ == "__main__":
    unittest.main()
