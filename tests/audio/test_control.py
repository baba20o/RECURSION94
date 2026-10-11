from __future__ import annotations

import unittest

from tools.audio_reference.control import (
    AudioApplyModel,
    DesiredStateController,
    SyncState,
    next_boundary,
)


class SyncStateTests(unittest.TestCase):
    def test_wire_round_trip_encodes_mute_in_high_bit(self) -> None:
        state = SyncState(39, 0x55, True, 0xFE)
        command = state.wire_bytes()
        self.assertEqual(bytes((0x09, 39, 0xD5, 0xFE)), command)
        self.assertEqual(state, SyncState.from_wire(command))

    def test_wire_requires_exact_opcode_and_length(self) -> None:
        with self.assertRaises(ValueError):
            SyncState.from_wire(b"\x09\x01\x01")
        with self.assertRaisesRegex(ValueError, "opcode"):
            SyncState.from_wire(b"\x08\x01\x01\x01")

    def test_state_fields_are_strictly_bounded(self) -> None:
        invalid = (
            (-1, 0, False, 0),
            (64, 0, False, 0),
            (0, 0x80, False, 0),
            (0, 0, False, 256),
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    SyncState(*values)

    def test_next_boundary_is_strictly_later(self) -> None:
        self.assertEqual(6, next_boundary(0, 6))
        self.assertEqual(12, next_boundary(6, 6))
        self.assertEqual(96, next_boundary(95, 96))


class DesiredStateControllerTests(unittest.TestCase):
    def test_only_one_generation_is_in_flight_and_retries_are_identical(self) -> None:
        controller = DesiredStateController()
        controller.request(1, 0x03, False)
        first = controller.next_command()
        self.assertIsNotNone(first)
        self.assertIs(first, controller.next_command())
        self.assertEqual(first.wire_bytes(), controller.in_flight.wire_bytes())
        self.assertEqual(1, controller.retries)

    def test_intermediate_requests_coalesce_behind_in_flight_state(self) -> None:
        controller = DesiredStateController()
        controller.request(1, 0x01, False)
        first = controller.next_command()
        assert first is not None
        controller.request(1, 0x03, False)
        controller.request(1, 0x07, False)
        self.assertEqual(first, controller.next_command())
        self.assertTrue(controller.acknowledge(first))
        latest = controller.next_command()
        assert latest is not None
        self.assertEqual((1, 0x07, False), latest.state_tuple)
        self.assertEqual(2, latest.generation)
        self.assertEqual(2, controller.requests_coalesced)

    def test_stale_ack_does_not_clear_current_generation(self) -> None:
        controller = DesiredStateController()
        controller.request(3, 0x0F, False)
        current = controller.next_command()
        assert current is not None
        stale = SyncState(3, 0x07, False, current.generation)
        self.assertFalse(controller.acknowledge(stale))
        self.assertEqual(current, controller.in_flight)
        self.assertEqual(1, controller.acks_ignored)

    def test_generation_wrap_is_explicit_and_safe_with_one_in_flight(self) -> None:
        controller = DesiredStateController(first_generation=0xFF)
        controller.request(1, 1, False)
        last = controller.next_command()
        assert last is not None
        self.assertEqual(0xFF, last.generation)
        self.assertTrue(controller.acknowledge(last))
        controller.request(2, 1, False)
        wrapped = controller.next_command()
        assert wrapped is not None
        self.assertEqual(0, wrapped.generation)

    def test_request_equal_to_acknowledged_state_emits_nothing(self) -> None:
        controller = DesiredStateController(initial_scene_id=1, initial_layer_mask=1)
        self.assertFalse(controller.request(1, 1, False))
        self.assertIsNone(controller.next_command())
        self.assertFalse(controller.pending)


class AudioApplyModelTests(unittest.TestCase):
    def test_layer_addition_waits_for_row_boundary(self) -> None:
        model = AudioApplyModel(initial_scene_id=1, initial_layer_mask=1)
        model.receive(
            SyncState(1, 0x03, False, 1),
            scene_quantum_ticks=96,
        )
        model.advance_to(5)
        self.assertEqual(1, model.applied_layer_mask)
        self.assertEqual(1, model.audible_layer_mask)
        model.advance_to(6)
        self.assertEqual(3, model.applied_layer_mask)
        self.assertEqual(3, model.audible_layer_mask)
        self.assertIn(
            (6, "layer_on", 1, True),
            [
                (event.tick, event.kind, event.value, event.audible_write)
                for event in model.events
            ],
        )

    def test_non_cumulative_removal_is_immediate_then_addition_quantizes(self) -> None:
        model = AudioApplyModel(initial_scene_id=10, initial_layer_mask=0x0F)
        model.advance_to(7)
        model.receive(
            SyncState(10, 0x15, False, 1),
            scene_quantum_ticks=96,
        )
        self.assertEqual(0x05, model.applied_layer_mask)
        self.assertEqual(0x05, model.audible_layer_mask)
        removed_bits = [
            event.value for event in model.events if event.kind == "layer_off"
        ]
        self.assertEqual([1, 3], removed_bits)
        model.advance_to(11)
        self.assertEqual(0x05, model.applied_layer_mask)
        model.advance_to(12)
        self.assertEqual(0x15, model.applied_layer_mask)
        self.assertEqual(0x15, model.audible_layer_mask)

    def test_scene_and_mask_apply_atomically_at_later_scene_boundary(self) -> None:
        model = AudioApplyModel(initial_scene_id=1, initial_layer_mask=1)
        model.advance_to(7)
        model.receive(
            SyncState(2, 0x07, False, 1),
            scene_quantum_ticks=96,
        )
        model.advance_to(95)
        self.assertEqual(1, model.applied_scene_id)
        self.assertEqual(1, model.applied_layer_mask)
        model.advance_to(96)
        self.assertEqual(2, model.applied_scene_id)
        self.assertEqual(0x07, model.applied_layer_mask)
        self.assertEqual(0x07, model.audible_layer_mask)
        kinds = [event.kind for event in model.events if event.tick == 96]
        self.assertEqual(
            ["all_notes_off", "scene_apply", "logical_layer_apply", "audible_restart"],
            kinds,
        )

    def test_boss_phase_can_use_row_quantized_scene_boundary(self) -> None:
        model = AudioApplyModel(initial_scene_id=18, initial_layer_mask=0x7F)
        model.advance_to(13)
        model.receive(
            SyncState(31, 0x7F, False, 1),
            scene_quantum_ticks=6,
        )
        model.advance_to(17)
        self.assertEqual(18, model.applied_scene_id)
        model.advance_to(18)
        self.assertEqual(31, model.applied_scene_id)

    def test_mute_is_immediate_once_and_no_audible_writes_follow(self) -> None:
        model = AudioApplyModel(initial_scene_id=7, initial_layer_mask=0x1F)
        model.receive(
            SyncState(7, 0x1F, True, 1),
            scene_quantum_ticks=96,
        )
        mute_event_count = len(model.events)
        self.assertEqual(0, model.audible_layer_mask)
        self.assertEqual(
            ["all_notes_off", "mute"],
            [event.kind for event in model.events],
        )
        model.receive(
            SyncState(8, 0x0B, True, 2),
            scene_quantum_ticks=96,
        )
        model.advance_to(192)
        after_mute = model.events[mute_event_count:]
        self.assertFalse(any(event.audible_write for event in after_mute))
        self.assertEqual(192, model.current_tick)
        self.assertEqual(8, model.applied_scene_id)
        self.assertEqual(0x0B, model.applied_layer_mask)

    def test_unmute_resumes_only_at_safe_boundary(self) -> None:
        model = AudioApplyModel(
            initial_scene_id=3,
            initial_layer_mask=0x07,
            initial_muted=True,
        )
        model.advance_to(7)
        model.receive(
            SyncState(3, 0x07, False, 1),
            scene_quantum_ticks=96,
        )
        model.advance_to(11)
        self.assertEqual(0, model.audible_layer_mask)
        model.advance_to(12)
        self.assertEqual(0x07, model.audible_layer_mask)
        self.assertEqual("audible_resume", model.events[-1].kind)

    def test_duplicate_generation_is_idempotent(self) -> None:
        model = AudioApplyModel(initial_scene_id=1, initial_layer_mask=1)
        state = SyncState(1, 3, False, 5)
        model.receive(state, scene_quantum_ticks=96)
        before = list(model.events)
        model.receive(state, scene_quantum_ticks=96)
        self.assertEqual(before, model.events)
        with self.assertRaisesRegex(ValueError, "one generation"):
            model.receive(
                SyncState(1, 7, False, 5),
                scene_quantum_ticks=96,
            )

    def test_stale_generation_cannot_roll_back_newer_state(self) -> None:
        model = AudioApplyModel(initial_scene_id=1, initial_layer_mask=1)
        newer = SyncState(2, 3, False, 10)
        model.receive(newer, scene_quantum_ticks=6)
        returned = model.receive(
            SyncState(1, 1, False, 9),
            scene_quantum_ticks=6,
        )
        self.assertEqual(newer, returned)
        model.advance_to(6)
        self.assertEqual(2, model.applied_scene_id)
        self.assertEqual("stale_generation", model.events[0].kind)

    def test_generation_wrap_is_treated_as_newer(self) -> None:
        model = AudioApplyModel(initial_scene_id=1, initial_layer_mask=1)
        model.receive(SyncState(1, 1, False, 0xFF), scene_quantum_ticks=6)
        wrapped = SyncState(2, 1, False, 0)
        self.assertEqual(
            wrapped,
            model.receive(wrapped, scene_quantum_ticks=6),
        )
        model.advance_to(6)
        self.assertEqual(2, model.applied_scene_id)


if __name__ == "__main__":
    unittest.main()
