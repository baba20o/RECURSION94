from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class CampaignFailureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gameplay = _read("src/gameplay/gameplay_core.asm")
        cls.runtime = _read("src/gameplay/campaign_runtime.asm")
        cls.vocabulary = _read("src/gameplay/vocabulary_core.asm")
        cls.timeline = _read("src/ui/timeline_control.asm")
        cls.startup = _read("src/boot/startup.asm")

    def test_campaign_contract_is_validated_before_recording(self) -> None:
        step = _routine(self.gameplay, "GameplayStep")
        contract = step.index(
            "#VOCAB_CAMPAIGN_FLAG_ACTIVE|"
            "VOCAB_CAMPAIGN_FLAG_PROFILE_VALID|"
            "VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR"
        )
        begin = step.index("bsr.w   CampaignBeginFrame", contract)
        begin_guard = step.index("tst.w   d0", begin)
        begin_reject = step.index("bne.w   .invalid_state", begin_guard)
        record = step.index("(TIMELINE_ATTEMPT_LENGTH).l", begin_reject)

        self.assertLess(contract, begin)
        self.assertLess(begin, begin_guard)
        self.assertLess(begin_guard, begin_reject)
        self.assertLess(begin_reject, record)

    def test_every_fallible_dependency_and_completion_call_propagates(self) -> None:
        step = _routine(self.gameplay, "GameplayStep")
        self.assertEqual(
            len(
                re.findall(
                    r"bsr\.w   CampaignEvaluateDependencies\s+"
                    r"tst\.w   d0\s+"
                    r"bne\.w   \.invalid_state",
                    step,
                )
            ),
            2,
        )
        completion = step.index("bsr.w   CampaignCheckCompletion")
        stable = step.index(
            "cmpi.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,d5", completion
        )
        nonzero = step.index("tst.w   d5", stable)
        reject = step.index("bne.w   .invalid_state", nonzero)
        self.assertLess(completion, stable)
        self.assertLess(stable, nonzero)
        self.assertLess(nonzero, reject)

    def test_attempt_reconstruction_checks_both_campaign_reset_results(
        self,
    ) -> None:
        routine = _routine(self.gameplay, "GameplayReinstantiateActors")
        vocabulary = routine.index("bsr.w   VocabularyResetOrdinary")
        vocabulary_guard = routine.index("tst.w   d0", vocabulary)
        campaign = routine.index(
            "bsr.w   CampaignResetAttemptState", vocabulary_guard
        )
        campaign_guard = routine.index("tst.w   d0", campaign)

        self.assertIn(
            "bne.w   .invalid_state",
            routine[vocabulary_guard:campaign],
        )
        self.assertIn(
            "bne.w   .invalid_state",
            routine[campaign_guard:],
        )

    def test_lethal_current_damage_precedes_same_frame_completion(self) -> None:
        completion = _routine(self.runtime, "CampaignCheckCompletion")
        predicate = completion.index(
            "move.w  (VOCAB_CAMPAIGN_COMPLETION_MASK).l,d4"
        )
        active = completion.index(
            "tst.b   (GAMEPLAY_CURRENT_ACTOR+ACTOR_ACTIVE).l", predicate
        )
        destroyed = completion.index(
            "#VOCAB_FLAG_CURRENT_DESTROYED", active
        )
        finalize = completion.index("bsr.w   VocabularyFinalizeStage", destroyed)

        self.assertLess(predicate, active)
        self.assertLess(active, destroyed)
        self.assertLess(destroyed, finalize)
        self.assertGreaterEqual(
            completion[active:finalize].count("bne.w   .not_complete")
            + completion[active:finalize].count("beq.w   .not_complete"),
            2,
        )

    def test_stage_result_uses_consumed_one_based_tick(self) -> None:
        finalize = _routine(self.vocabulary, "VocabularyFinalizeStage")
        load = finalize.index("move.w  (ENGINE_TICK).l,d1")
        increment = finalize.index("addq.w  #1,d1", load)
        clamp = finalize.index("#ENGINE_ATTEMPT_TICKS,d1", increment)
        store = finalize.index("(VOCAB_STAGE_FINAL_TICK).l", clamp)
        self.assertLess(load, increment)
        self.assertLess(increment, clamp)
        self.assertLess(clamp, store)

    def test_invalid_main_or_timeline_state_reaches_fatal_path(self) -> None:
        post_step = self.startup.split(
            "MainLoopPostGameplayStepCall:", 1
        )[1].split("MainLoopCommitTimeout:", 1)[0]
        self.assertIn("#GAMEPLAY_RESULT_PROGRAM_STABLE", post_step)
        self.assertIn("jmp     (SafeException).l", post_step)

        timeline = _routine(self.timeline, "TimelineHandleMetaInput")
        fatal = timeline.index(".fatal:")
        self.assertIn("jmp     (SafeException).l", timeline[fatal:])
        terminal = timeline[: timeline.index("\n.active:")]
        self.assertIn("#GAMEPLAY_TERMINAL_PROGRAM_STABLE", terminal)
        self.assertGreaterEqual(terminal.count("bra.w   .fatal"), 1)
        self.assertGreaterEqual(terminal.count("bne.w   .fatal"), 3)


if __name__ == "__main__":
    unittest.main()
