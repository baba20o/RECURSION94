from __future__ import annotations

import itertools
import re
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")
TOKEN_MAX = 0xFFFF
REPLAY_BANK_COUNT = 7
GAMEPLAY_ECHO_LIMIT = 6


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


def _vblank_token_increment(token: int) -> int:
    """Mirror the saturating word increment in VBlankInterrupt."""

    if not 0 <= token <= TOKEN_MAX:
        raise ValueError(token)
    return min(token + 1, TOKEN_MAX)


@dataclass(frozen=True)
class ResetBarrierResult:
    token_at_entry: int
    token_after_clear: int
    pending_at_stop: bool
    stop_was_waiting: bool
    token_after_stop_returns: int
    token_after_next_consume: int
    stale_vblanks_serviced: int
    fresh_vblanks_serviced: int


def _run_reset_barrier(token: int, irq_timing: str) -> ResetBarrierResult:
    """Model mask/clear/STOP, including an IRQ pending behind the mask.

    ``before_mask`` is deliberately stale: its completed token must be erased,
    and STOP remains asleep until another VBlank. The other timings describe a
    new VBlank request asserted after the barrier masks interrupts; its handler
    cannot run until STOP atomically lowers the mask.
    """

    if irq_timing not in {
        "before_mask",
        "between_mask_and_clear",
        "after_clear",
    }:
        raise ValueError(irq_timing)

    token_at_entry = token
    stale_vblanks = 0
    pending = False
    if irq_timing == "before_mask":
        token = _vblank_token_increment(token)
        stale_vblanks += 1

    # move.w #$2700,sr
    masked = True
    if irq_timing == "between_mask_and_clear":
        pending = True

    # clr.w (DEBUG_FRAME_TOKEN).l
    token = 0
    token_after_clear = token
    if irq_timing == "after_clear":
        pending = True

    # stop #$2500 atomically unmasks and waits. A request already pending runs
    # immediately; otherwise a later VBlank is required to wake the CPU.
    masked = False
    stop_was_waiting = not pending
    if not pending:
        pending = True
    fresh_vblanks = 0
    if pending and not masked:
        token = _vblank_token_increment(token)
        fresh_vblanks += 1
        pending = False

    token_after_stop = token
    token -= 1
    return ResetBarrierResult(
        token_at_entry=token_at_entry,
        token_after_clear=token_after_clear,
        pending_at_stop=not stop_was_waiting,
        stop_was_waiting=stop_was_waiting,
        token_after_stop_returns=token_after_stop,
        token_after_next_consume=token,
        stale_vblanks_serviced=stale_vblanks,
        fresh_vblanks_serviced=fresh_vblanks,
    )


@dataclass(frozen=True)
class NormalIterationResult:
    processed_step: bool
    sleeping_for_vblank: bool
    token: int


def _run_normal_iteration(
    token: int, *, irq_during_work: bool = False
) -> NormalIterationResult:
    """Model the unchanged path: consume one queued token and loop."""

    if not 0 <= token <= TOKEN_MAX:
        raise ValueError(token)
    if token == 0:
        return NormalIterationResult(False, True, 0)
    token -= 1
    if irq_during_work:
        token = _vblank_token_increment(token)
    return NormalIterationResult(True, False, token)


def _append_current(echo_order: tuple[int, ...], current: int) -> int:
    updated_order = echo_order + (current,)
    return next(
        bank for bank in range(REPLAY_BANK_COUNT) if bank not in updated_order
    )


def _replace_selected(echo_order: tuple[int, ...], ordinal: int) -> int:
    return echo_order[ordinal]


class ResetFrameSchedulerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.startup = (ROOT / "src/boot/startup.asm").read_text(
            encoding="utf-8"
        )
        cls.replay = (ROOT / "src/gameplay/replay.asm").read_text(
            encoding="utf-8"
        )

    def test_current_bank_snapshot_is_inside_the_masked_token_transaction(
        self,
    ) -> None:
        loop = self.startup.split("MainLoop:", 1)[1].split(
            "MainLoopGameplayStepCall:", 1
        )[0]
        mask = loop.index("move.w  #$2700,sr")
        consume = loop.index("subq.w  #1,(DEBUG_FRAME_TOKEN).l")
        snapshot = loop.index("move.b  (TIMELINE_CURRENT_BANK).l,d3")
        unmask = loop.index("move.w  #$2500,sr", snapshot)

        self.assertLess(mask, consume)
        self.assertLess(consume, snapshot)
        self.assertLess(snapshot, unmask)

    def test_bank_change_is_checked_only_after_reset_presentation_is_built(
        self,
    ) -> None:
        post = self.startup.index("MainLoopPostGameplayStep:")
        coalesce = self.startup.index("MainLoopResetTokenCoalesce:", post)
        body = self.startup[post:coalesce]
        renderer = body.index("jsr     (GameplayRendererBuildAndQueue).l")
        comparison = body.index("cmp.b   (TIMELINE_CURRENT_BANK).l,d3")
        changed = re.search(
            r"(?m)^\s*bne\.[sw]\s+MainLoopResetTokenCoalesce\s*$", body
        )
        normal = re.search(r"(?m)^\s*bra\.[sw]\s+MainLoop\s*$", body)

        self.assertIsNotNone(changed)
        self.assertIsNotNone(normal)
        assert changed is not None
        assert normal is not None
        self.assertLess(renderer, comparison)
        self.assertLess(comparison, changed.start())
        self.assertLess(changed.end(), normal.start())

    def test_changed_bank_discards_only_tokens_then_atomically_waits(self) -> None:
        coalesce = self.startup.index("MainLoopResetTokenCoalesce:")
        wait = self.startup.index("MainLoopWaitForVBlank:", coalesce)
        barrier = self.startup[coalesce:wait]
        mask = barrier.index("move.w  #$2700,sr")
        masked_label = barrier.index("MainLoopResetTokenMasked:")
        clear = barrier.index("clr.w   (DEBUG_FRAME_TOKEN).l")
        cleared_label = barrier.index("MainLoopResetTokensCoalesced:")
        branch = barrier.index("bra.w   MainLoopWaitForVBlank")

        self.assertLess(mask, masked_label)
        self.assertLess(masked_label, clear)
        self.assertLess(clear, cleared_label)
        self.assertLess(cleared_label, branch)
        self.assertEqual(
            re.findall(r"(?m)^\s*clr\.[bwl]\s+([^\s;]+)", barrier),
            ["(DEBUG_FRAME_TOKEN).l"],
        )
        for state_that_must_remain_monotonic in (
            "DEBUG_FRAME_COUNTER",
            "DEBUG_HEARTBEAT",
            "DEBUG_SEQUENCE",
            "INPUT_SAMPLE_COUNT",
            "DEBUG_CONTROLLER_SAMPLES",
            "INPUT_HELD",
            "INPUT_PRESSED",
        ):
            self.assertNotIn(state_that_must_remain_monotonic, barrier)

        wait_body = _routine(self.startup, "MainLoopWaitForVBlank")
        self.assertIn(
            "MainLoopWaitForVBlank:\n"
            "        stop    #$2500\n"
            "        bra.w   MainLoop",
            wait_body,
        )

    def test_reset_barrier_discards_saturated_and_queued_stale_tokens(self) -> None:
        for queued, timing in itertools.product(
            (0, 1, 2, TOKEN_MAX),
            ("before_mask", "between_mask_and_clear", "after_clear"),
        ):
            with self.subTest(queued=queued, timing=timing):
                result = _run_reset_barrier(queued, timing)
                self.assertEqual(result.token_after_clear, 0)
                self.assertEqual(result.token_after_stop_returns, 1)
                self.assertEqual(result.token_after_next_consume, 0)
                self.assertEqual(result.fresh_vblanks_serviced, 1)
                self.assertEqual(
                    result.stop_was_waiting, timing == "before_mask"
                )
                self.assertEqual(
                    result.stale_vblanks_serviced,
                    1 if timing == "before_mask" else 0,
                )

    def test_no_reset_path_retains_normal_token_behavior(self) -> None:
        expected_without_irq = {
            0: NormalIterationResult(False, True, 0),
            1: NormalIterationResult(True, False, 0),
            2: NormalIterationResult(True, False, 1),
            TOKEN_MAX: NormalIterationResult(True, False, TOKEN_MAX - 1),
        }
        for queued, expected in expected_without_irq.items():
            with self.subTest(queued=queued, irq=False):
                self.assertEqual(_run_normal_iteration(queued), expected)

        for queued in (1, 2, TOKEN_MAX):
            with self.subTest(queued=queued, irq=True):
                result = _run_normal_iteration(
                    queued, irq_during_work=True
                )
                self.assertTrue(result.processed_step)
                self.assertFalse(result.sleeping_for_vblank)
                self.assertEqual(
                    result.token,
                    _vblank_token_increment(queued - 1),
                )

    def test_commit_source_routes_only_success_to_a_new_current_bank(self) -> None:
        commit = _routine(self.replay, "GameplayCommitAndReset")
        clear = _routine(self.replay, "GameplayClearCurrentBank")
        free = _routine(self.replay, "GameplayFindFreeBank")

        old_current = commit.index(
            "move.b  (TIMELINE_CURRENT_BANK).l,d6"
        )
        append_current = commit.index("move.b  d6,(a2,d4.w)", old_current)
        find_free = commit.index("bsr.w   GameplayFindFreeBank", append_current)
        save_free = commit.index("move.w  d0,d7", find_free)
        replace = commit.index(".replace:", save_free)
        select_removed = commit.index("move.b  (a2,d5.w),d7", replace)
        new_current = commit.index(".new_current:", select_removed)
        pass_choice = commit.index("move.w  d7,d0", new_current)
        clear_call = commit.index("bsr.w   GameplayClearCurrentBank", pass_choice)

        self.assertLess(old_current, append_current)
        self.assertLess(append_current, find_free)
        self.assertLess(find_free, save_free)
        self.assertLess(save_free, replace)
        self.assertLess(replace, select_removed)
        self.assertLess(select_removed, new_current)
        self.assertLess(new_current, pass_choice)
        self.assertLess(pass_choice, clear_call)
        self.assertIn("cmp.w   d3,d5", free)
        self.assertIn("beq.s   .next_candidate", free)
        self.assertIn("move.w  d3,d0", free)
        self.assertIn("move.b  d3,(TIMELINE_CURRENT_BANK).l", clear)

        replacement_required = commit.split(
            ".replacement_required:", 1
        )[1].split(".protected:", 1)[0]
        protected = commit.split(".protected:", 1)[1].split(
            ".invalid_state:", 1
        )[0]
        for blocked in (replacement_required, protected):
            self.assertNotIn("GameplayClearCurrentBank", blocked)
            self.assertNotIn("TIMELINE_CURRENT_BANK", blocked)

    def test_legal_append_and_replacement_models_always_rotate_bank(self) -> None:
        banks = tuple(range(REPLAY_BANK_COUNT))

        for echo_count in range(GAMEPLAY_ECHO_LIMIT):
            for order in itertools.permutations(banks, echo_count):
                for current in banks:
                    if current in order:
                        continue
                    new_current = _append_current(order, current)
                    self.assertNotEqual(new_current, current)
                    self.assertNotIn(new_current, order + (current,))

        for echo_count in range(1, GAMEPLAY_ECHO_LIMIT + 1):
            for order in itertools.permutations(banks, echo_count):
                for current in banks:
                    if current in order:
                        continue
                    for ordinal in range(echo_count):
                        new_current = _replace_selected(order, ordinal)
                        self.assertNotEqual(new_current, current)
                        self.assertIn(new_current, order)


if __name__ == "__main__":
    unittest.main()
