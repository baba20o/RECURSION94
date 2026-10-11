from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class AttemptTerminalReplacementContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.startup = (ROOT / "src/boot/startup.asm").read_text(
            encoding="utf-8"
        )
        cls.replay = (ROOT / "src/gameplay/replay.asm").read_text(
            encoding="utf-8"
        )

    def test_automatic_end_attempts_enter_pending_ui_instead_of_fatal(self) -> None:
        start = self.startup.index("MainLoopPostCommitReset:")
        end = self.startup.index("MainLoopGameplayStepComplete:", start)
        result = self.startup[start:end]

        required = result.index(
            "#GAMEPLAY_RESULT_REPLACEMENT_REQUIRED"
        )
        protected = result.index("#GAMEPLAY_RESULT_PROTECTED", required)
        fatal = result.index("SafeException", protected)
        self.assertIn("beq.s   MainLoopGameplayStepComplete", result)
        self.assertLess(required, protected)
        self.assertLess(protected, fatal)

    def test_pending_commit_does_not_finalize_or_clear_partial_recording(self) -> None:
        commit = _routine(self.replay, "GameplayCommitAndReset")
        selection = commit.index(".selection_ready:")
        finalize = commit.index("bsr.w   GameplayFinalizeCurrentBank", selection)
        replacement = commit.index(".replacement_required:", finalize)
        pending_body = commit[replacement : commit.index(".protected:", replacement)]

        self.assertLess(selection, finalize)
        self.assertLess(finalize, replacement)
        self.assertIn(
            "move.w  d3,(TIMELINE_PENDING_REASON).l", pending_body
        )
        self.assertNotIn("GameplayFinalizeCurrentBank", pending_body)
        self.assertNotIn("TIMELINE_ATTEMPT_LENGTH", pending_body)

    def test_retry_finalizes_exact_partial_length_before_reset(self) -> None:
        finalize = _routine(self.replay, "GameplayFinalizeCurrentBank")
        commit = _routine(self.replay, "GameplayCommitAndReset")

        self.assertIn(
            "move.w  (TIMELINE_ATTEMPT_LENGTH).l,d3", finalize
        )
        self.assertIn("move.w  d3,(REPLAY_DESCRIPTOR_LENGTH,a2)", finalize)
        self.assertIn("bsr.w   Crc32IsoHdlc", finalize)

        finalize_call = commit.index("bsr.w   GameplayFinalizeCurrentBank")
        reset = commit.index("bsr.w   EngineResetCanonicalState", finalize_call)
        clear_length = commit.index(
            "clr.w   (TIMELINE_ATTEMPT_LENGTH).l", finalize_call
        )
        self.assertLess(finalize_call, clear_length)
        self.assertLess(clear_length, reset)


if __name__ == "__main__":
    unittest.main()
