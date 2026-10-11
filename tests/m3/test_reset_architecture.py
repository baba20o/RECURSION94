from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _routine(source: str, label: str, next_label: str) -> str:
    return source.split(f"\n{label}:", 1)[1].split(f"\n{next_label}:", 1)[0]


class ResetArchitectureTests(unittest.TestCase):
    def test_canonical_reconstruction_clear_is_exact_and_burst_bounded(self) -> None:
        source = (ROOT / "src/game/engine_core.asm").read_text(encoding="utf-8")
        routine = _routine(
            source,
            "EngineReconstructCanonicalState",
            "EngineResetCanonicalState",
        )
        clear = routine.split(
            "move.w  #ENGINE_FLAG_LOADED,(ENGINE_FLAGS).l", 1
        )[0]
        self.assertEqual(0x7000 - 0x1800, 433 * 52 + 12)
        self.assertEqual(clear.count("movem.l d0-d6/a1-a6,(a0)"), 1)
        self.assertIn("move.w  #432,d7", clear)
        self.assertIn("adda.w  #52,a0", clear)
        self.assertEqual(clear.count("move.l  d0,(a0)+"), 3)
        self.assertNotIn("EngineBuildProjection", routine)

    def test_public_reset_publishes_once_after_state_reconstruction(self) -> None:
        source = (ROOT / "src/game/engine_core.asm").read_text(encoding="utf-8")
        reset = source.split("\nEngineReset:", 1)[1].split("\nEngineStep:", 1)[0]
        self.assertEqual(reset.count("bsr.w   EngineResetCanonicalState"), 1)
        self.assertEqual(reset.count("bsr.w   EngineBuildProjection"), 1)
        self.assertNotIn("EngineLoadEncounter", reset)

        reconstruct = _routine(
            source,
            "EngineReconstructCanonical",
            "EngineReconstructCanonicalState",
        )
        self.assertIn("bsr.w   EngineBuildProjection", reconstruct)
        self.assertIn("moveq   #ENGINE_RESULT_OK,d0", reconstruct)

    def test_gameplay_reset_replaces_actor_before_sole_checkpoint(self) -> None:
        replay = (ROOT / "src/gameplay/replay.asm").read_text(encoding="utf-8")
        commit = replay.split("\nGameplayCommitAndReset:", 1)[1]
        reset_call = commit.index("bsr.w   EngineResetCanonicalState")
        actors_call = commit.index("bsr.w   GameplayReinstantiateActors")
        self.assertLess(reset_call, actors_call)
        post_actors = commit[actors_call:]
        self.assertLess(
            post_actors.index("tst.w   d0"),
            post_actors.index("moveq   #GAMEPLAY_RESULT_OK,d0"),
        )
        self.assertLess(
            post_actors.index("bne.s   .done"),
            post_actors.index("moveq   #GAMEPLAY_RESULT_OK,d0"),
        )
        self.assertNotIn(
            "bsr.w   EngineReset\n",
            commit[: commit.index("\n.replacement_required:", 1)],
        )

        core = (ROOT / "src/gameplay/gameplay_core.asm").read_text(
            encoding="utf-8"
        )
        actors = core.split("\nGameplayReinstantiateActors:", 1)[1].split(
            "\nGameplayStep:", 1
        )[0]
        self.assertEqual(actors.count("bsr.w   GameplayBuildProjection"), 1)
        self.assertNotIn(".clear_actors:", actors)
        self.assertEqual(actors.count("movem.l d0-d2/a1,"), 2)
        self.assertIn("movem.l d0-d2/a1,(a0)", actors)
        self.assertIn("movem.l d0-d2/a1,16(a0)", actors)


if __name__ == "__main__":
    unittest.main()
