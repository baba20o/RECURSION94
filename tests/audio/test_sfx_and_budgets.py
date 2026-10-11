from __future__ import annotations

import itertools
import unittest

from tools.audio_compiler.compiler import Z80_LAYOUT
from tools.audio_reference.budgets import (
    BankBudget,
    ChipWriteBudget,
    FrameCommandBudget,
    MemoryRegion,
    OrdinaryQueueModel,
    UrgentMailboxModel,
    validate_memory_layout,
)
from tools.audio_reference.notes import NoteEvent, validate_note_events
from tools.audio_reference.sfx import (
    BoundedSfxCollector,
    SfxEvent,
    SfxPriority,
)


def event(
    sfx_id: int,
    priority: SfxPriority,
    sequence: int,
    source_id: int,
    *,
    merge_group: int | None = None,
) -> SfxEvent:
    return SfxEvent(sfx_id, priority, sequence, source_id, merge_group)


class SfxCollectorTests(unittest.TestCase):
    def test_duplicate_events_merge_with_saturating_multiplicity(self) -> None:
        collector = BoundedSfxCollector()
        for sequence in range(20):
            collector.add(event(1, SfxPriority.ROUTINE, sequence, sequence))
        emitted = collector.flush()
        self.assertEqual(1, len(emitted))
        self.assertEqual(15, emitted[0].multiplicity)
        self.assertEqual(0, emitted[0].sequence)
        self.assertEqual(19, collector.merged)

    def test_distinct_merge_groups_remain_distinct(self) -> None:
        collector = BoundedSfxCollector()
        collector.add(event(2, SfxPriority.ROUTINE, 1, 1, merge_group=1))
        collector.add(event(2, SfxPriority.ROUTINE, 2, 2, merge_group=2))
        self.assertEqual(2, collector.count)

    def test_capacity_replaces_worst_event_with_better_priority(self) -> None:
        collector = BoundedSfxCollector(capacity=3, emit_limit=2)
        collector.add(event(1, SfxPriority.COSMETIC, 1, 1, merge_group=1))
        collector.add(event(2, SfxPriority.ROUTINE, 2, 2, merge_group=2))
        collector.add(event(3, SfxPriority.ROUTINE, 3, 3, merge_group=3))
        self.assertTrue(
            collector.add(event(4, SfxPriority.TERMINAL, 4, 4, merge_group=4))
        )
        emitted = collector.flush()
        self.assertEqual([4, 2], [item.sfx_id for item in emitted])
        self.assertEqual(1, collector.dropped[SfxPriority.COSMETIC])
        self.assertEqual(1, collector.dropped[SfxPriority.ROUTINE])

    def test_lower_ranked_event_drops_when_capacity_is_full(self) -> None:
        collector = BoundedSfxCollector(capacity=2, emit_limit=2)
        collector.add(event(1, SfxPriority.CRITICAL, 1, 1, merge_group=1))
        collector.add(event(2, SfxPriority.ROUTINE, 2, 2, merge_group=2))
        self.assertFalse(
            collector.add(event(3, SfxPriority.COSMETIC, 3, 3, merge_group=3))
        )
        self.assertEqual(1, collector.dropped[SfxPriority.COSMETIC])

    def test_flush_never_emits_more_than_two_and_accounts_for_remainder(self) -> None:
        collector = BoundedSfxCollector(capacity=8, emit_limit=2)
        for index in range(8):
            collector.add(
                event(
                    index,
                    SfxPriority(index % 4),
                    index,
                    index,
                    merge_group=index,
                )
            )
        emitted = collector.flush()
        self.assertEqual(2, len(emitted))
        self.assertEqual(6, sum(collector.dropped))
        self.assertEqual(0, collector.count)

    def test_selection_is_independent_of_arrival_order_given_stable_keys(self) -> None:
        source = (
            event(1, SfxPriority.ROUTINE, 3, 3, merge_group=1),
            event(2, SfxPriority.TERMINAL, 7, 4, merge_group=2),
            event(3, SfxPriority.TERMINAL, 2, 9, merge_group=3),
            event(4, SfxPriority.CRITICAL, 1, 1, merge_group=4),
        )
        outputs = set()
        for permutation in itertools.permutations(source):
            collector = BoundedSfxCollector(capacity=8, emit_limit=2)
            for item in permutation:
                collector.add(item)
            outputs.add(tuple(event.sfx_id for event in collector.flush()))
        self.assertEqual({(3, 2)}, outputs)

    def test_wire_command_packs_priority_and_multiplicity(self) -> None:
        value = SfxEvent(
            sfx_id=0x22,
            priority=SfxPriority.CRITICAL,
            sequence=0x44,
            source_id=1,
            multiplicity=3,
        )
        self.assertEqual(bytes((0x03, 0x22, 0x23, 0x44)), value.wire_bytes())


class BudgetTests(unittest.TestCase):
    def test_frozen_z80_layout_is_exact_contiguous_8k(self) -> None:
        regions = tuple(
            MemoryRegion(name, start, end)
            for name, start, end, _purpose in Z80_LAYOUT
        )
        self.assertEqual(regions, validate_memory_layout(regions))
        self.assertEqual(0x2000, sum(region.size for region in regions))

    def test_layout_rejects_gap_overlap_duplicate_and_short_cover(self) -> None:
        invalid_layouts = (
            (MemoryRegion("a", 0, 3), MemoryRegion("b", 5, 7)),
            (MemoryRegion("a", 0, 3), MemoryRegion("b", 3, 7)),
            (MemoryRegion("a", 0, 3), MemoryRegion("a", 4, 7)),
            (MemoryRegion("a", 0, 6),),
        )
        for regions in invalid_layouts:
            with self.subTest(regions=regions):
                with self.assertRaises(ValueError):
                    validate_memory_layout(regions, address_space_bytes=8)

    def test_bank_budget_accepts_exact_limit_and_rejects_overflow(self) -> None:
        self.assertEqual(
            2032,
            BankBudget(payload_bytes=2032, event_count_per_tick=12)
            .validate()
            .payload_bytes,
        )
        with self.assertRaises(ValueError):
            BankBudget(payload_bytes=2033).validate()
        with self.assertRaises(ValueError):
            BankBudget(payload_bytes=1, event_count_per_tick=13).validate()
        with self.assertRaises(ValueError):
            BankBudget(
                payload_bytes=1,
                maximum_bytes=2040,
                descriptor_bytes=16,
                region_bytes=2048,
            ).validate()

    def test_chip_write_budget_is_fail_closed(self) -> None:
        ChipWriteBudget(ym_writes=32, psg_writes=12).validate()
        with self.assertRaises(ValueError):
            ChipWriteBudget(ym_writes=33, psg_writes=0).validate()
        with self.assertRaises(ValueError):
            ChipWriteBudget(ym_writes=0, psg_writes=13).validate()

    def test_frame_command_budget_is_three_under_four_service_slots(self) -> None:
        budget = FrameCommandBudget(state_commands=1, sfx_commands=2).validate()
        self.assertEqual(3, budget.total)
        with self.assertRaises(ValueError):
            FrameCommandBudget(state_commands=1, sfx_commands=3).validate()
        with self.assertRaises(ValueError):
            FrameCommandBudget(
                state_commands=2,
                sfx_commands=2,
                state_limit=2,
                service_limit=3,
            ).validate()

    def test_sustained_contract_rate_does_not_accumulate_queue(self) -> None:
        queue = OrdinaryQueueModel(capacity=16, service_per_vint=4)
        for _frame in range(720):
            accepted, serviced = queue.display_frame(3)
            self.assertEqual((3, 3), (accepted, serviced))
        self.assertEqual(0, queue.occupancy)
        self.assertEqual(3, queue.high_water)
        self.assertEqual(0, queue.dropped)

    def test_burst_queue_overflow_is_bounded_and_counted(self) -> None:
        queue = OrdinaryQueueModel(capacity=16, service_per_vint=4)
        self.assertEqual(16, queue.post(20))
        self.assertEqual(4, queue.dropped)
        self.assertEqual(16, queue.high_water)
        self.assertEqual(4, queue.service())
        self.assertEqual(12, queue.occupancy)

    def test_urgent_mailbox_never_overwrites_pending_command(self) -> None:
        mailbox = UrgentMailboxModel()
        self.assertTrue(mailbox.post(4))
        self.assertFalse(mailbox.post(6))
        self.assertEqual(4, mailbox.service())
        self.assertTrue(mailbox.post(6))
        self.assertEqual(1, mailbox.rejected)


class NoteInvariantTests(unittest.TestCase):
    def test_finite_non_overlapping_note_fixture_passes(self) -> None:
        fixture = (
            NoteEvent("ym_ch0", 0, 24),
            NoteEvent("ym_ch0", 24, 48),
            NoteEvent("psg_noise", 0, 6),
            NoteEvent("ym_ch5", 5, 3),
        )
        self.assertEqual(fixture, validate_note_events(fixture))

    def test_open_or_oversized_note_fails(self) -> None:
        for note in (
            NoteEvent("ym_ch0", 0, 0),
            NoteEvent("ym_ch0", 0, 385),
            NoteEvent("ym_ch0", 0, 24, explicit_key_off=False),
        ):
            with self.subTest(note=note):
                with self.assertRaises(ValueError):
                    validate_note_events((note,))

    def test_unknown_voice_and_monophonic_overlap_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown voice"):
            validate_note_events((NoteEvent("ym_ch6", 0, 1),))
        with self.assertRaisesRegex(ValueError, "overlaps"):
            validate_note_events(
                (
                    NoteEvent("ym_ch1", 0, 12),
                    NoteEvent("ym_ch1", 11, 12),
                )
            )


if __name__ == "__main__":
    unittest.main()
