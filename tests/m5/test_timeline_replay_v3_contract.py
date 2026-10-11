from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPLAY_PATH = ROOT / "src/gameplay/replay.asm"
TIMELINE_PATH = ROOT / "src/ui/timeline_control.asm"
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")
WARNING_RE = re.compile(
    r"(?i)(?:^|\s)warning(?:\s+[A-Za-z0-9_-]+)?\s*(?::|\[)"
)


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


class TimelineReplayV3ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.replay = REPLAY_PATH.read_text(encoding="utf-8")
        cls.timeline = TIMELINE_PATH.read_text(encoding="utf-8")

    def test_active_campaign_cap_requires_an_abi_v3_profile(self) -> None:
        routine = _routine(self.replay, "GameplayGetEchoCap")
        active = routine.index("#VOCAB_CAMPAIGN_FLAG_ACTIVE")
        profile = routine.index("#VOCAB_CAMPAIGN_FLAG_PROFILE_VALID")
        cap = routine.index("(VOCAB_CAMPAIGN_ECHO_CAP).l")

        self.assertLess(active, profile)
        self.assertLess(profile, cap)
        self.assertIn(
            "#VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,"
            "(VOCAB_CAMPAIGN_FLAGS).l",
            routine,
        )
        self.assertGreaterEqual(
            routine.count("moveq   #GAMEPLAY_ECHO_LIMIT,d0"), 2
        )

    def test_only_pending_timeout_or_death_can_use_terminal_timeline(self) -> None:
        routine = _routine(self.timeline, "TimelineHandleMetaInput")
        active_path = routine.index("\n.active:")
        terminal_guard = routine[:active_path]

        self.assertIn(
            "cmpi.w  #ENGINE_STATE_ACTIVE,(ENGINE_STATE).l",
            terminal_guard,
        )
        self.assertIn(
            "cmpi.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l",
            terminal_guard,
        )
        self.assertIn("#GAMEPLAY_COMMIT_TIMEOUT", terminal_guard)
        self.assertIn("#GAMEPLAY_COMMIT_DESTROYED", terminal_guard)
        self.assertIn("#ENGINE_TERMINAL_TIMEOUT", terminal_guard)
        self.assertIn(
            "#GAMEPLAY_TERMINAL_CURRENT_DESTROYED", terminal_guard
        )
        self.assertIn(
            "#TIMELINE_FLAG_REPLACEMENT_REQUIRED", terminal_guard
        )
        self.assertIn(
            "ori.w   #VOCAB_TIMELINE_UI_FLAG_OPEN,"
            "(VOCAB_TIMELINE_UI_FLAGS).l",
            terminal_guard,
        )
        self.assertIn("bra.w   .pending_open", terminal_guard)
        terminal_consumed = terminal_guard.split(
            ".terminal_consumed:", 1
        )[1]
        self.assertIn(
            "andi.w  #$FFFE,(VOCAB_TIMELINE_UI_FLAGS).l",
            terminal_consumed,
        )
        self.assertIn("bsr.w   GameplayMirrorReplacementFlags", terminal_guard)
        self.assertIn("bra.w   .consumed", terminal_guard)
        self.assertNotIn("INPUT_START_HELD", terminal_guard)

    def test_pending_terminal_commit_reuses_exact_reason(self) -> None:
        routine = _routine(self.timeline, "TimelineHandleMetaInput")
        commit = routine.split(".commit:", 1)[1].split(
            ".commit_ready:", 1
        )[0]
        pending = commit.index("(TIMELINE_PENDING_REASON).l")
        ready = commit.index(".commit_reason_ready:", pending)
        reset = commit.index("bsr.w   GameplayCommitAndReset", ready)

        self.assertIn("#GAMEPLAY_COMMIT_MANUAL", commit[:pending])
        self.assertIn("move.w  d1,d0", commit[pending:ready])
        self.assertLess(pending, ready)
        self.assertLess(ready, reset)

    def test_commit_input_is_consumed_through_c_release(self) -> None:
        routine = _routine(self.timeline, "TimelineHandleMetaInput")
        state_ready = routine.index(".state_ready:")
        release_guard = routine[:state_ready]
        commit_ready = routine.split(".commit_ready:", 1)[1].split(
            ".close:", 1
        )[0]

        self.assertIn(
            "#VOCAB_TIMELINE_UI_FLAG_WAIT_RELEASE", release_guard
        )
        self.assertIn("(INPUT_HELD).l", release_guard)
        self.assertIn("#INPUT_C", release_guard)
        self.assertIn("bne.w   .consumed", release_guard)
        self.assertIn(
            "#VOCAB_TIMELINE_UI_FLAG_WAIT_RELEASE", commit_ready
        )
        self.assertIn("bra.s   .consumed", commit_ready)

    def test_authored_cap_controls_selection_and_commit(self) -> None:
        for label in (
            "GameplaySelectReplacement",
            "GameplaySelectOldestReplacement",
            "GameplayCommitAndReset",
        ):
            self.assertIn(
                "bsr.w   GameplayGetEchoCap",
                _routine(self.replay, label),
            )

        timeline = _routine(self.timeline, "TimelineHandleMetaInput")
        self.assertIn("bsr.w   GameplayGetEchoCap", timeline)
        self.assertIn(
            "(VOCAB_TIMELINE_UI_CURSOR).l",
            timeline,
        )

    def test_standalone_modules_are_warning_free_and_link_complete(self) -> None:
        assembler = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(assembler.is_file(), assembler)
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="m5-timeline-replay-v3-", dir=ROOT / "build"
        ) as temporary:
            output = Path(temporary) / "timeline_replay.bin"
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
                    "tests/m5/timeline_replay_standalone.asm",
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
