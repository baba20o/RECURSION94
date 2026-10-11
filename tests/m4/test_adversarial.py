from __future__ import annotations

import unittest
from pathlib import Path

from tools.m4_reference.contracts import load_gameplay_contract
from tools.m4_reference.model import GameplayModel, RecordingOverrun


ROOT = Path(__file__).resolve().parents[2]


class GameplayAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_gameplay_contract(
            ROOT, verify_source_hashes=False
        )
        cls.c = cls.contract.constants

    def new_model(self) -> GameplayModel:
        return GameplayModel(self.contract)

    def fill_capacity(self, model: GameplayModel) -> None:
        for ordinal in range(6):
            self.assertEqual(model.step(self.c["INPUT_A"] if ordinal & 1 else 0), 0)
            self.assertEqual(
                model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]),
                0,
            )
        self.assertEqual(model.echo_order, [6, 0, 1, 2, 3, 4])
        self.assertEqual(model.current_bank, 5)

    def test_recording_api_rejects_both_bank_boundaries(self) -> None:
        model = self.new_model()
        with self.assertRaises(RecordingOverrun):
            model.replays.record(6, -1, 0)
        with self.assertRaises(RecordingOverrun):
            model.replays.record(6, 720, 0)
        self.assertTrue(model.replays.canaries_intact())
        self.assertEqual(len(model.replays.bank(6)), 720)

    def test_current_alone_cannot_commit_terminal(self) -> None:
        model = self.new_model()
        current = model.actors[6]
        current.x = 288 << 8
        current.y = 112 << 8
        self.assertEqual(model.step(self.c["INPUT_B"]), 0)
        self.assertEqual(model.state, self.c["ENGINE_STATE_ACTIVE"])
        self.assertFalse(model.gameplay_flags & self.c["GAMEPLAY_FLAG_PROGRAM_STABLE"])

    def test_echo_b_does_not_commit_for_current(self) -> None:
        model = self.new_model()
        model.step(0)
        model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"])
        model.step(self.c["INPUT_B"])
        model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"])
        model.actors[0].x = 80 << 8
        model.actors[0].y = 112 << 8
        model.actors[1].x = 288 << 8
        model.actors[1].y = 112 << 8
        model.actors[6].x = 288 << 8
        model.actors[6].y = 112 << 8
        self.assertEqual(model.step(0), 0)
        self.assertTrue(model.switch_mask & 1)
        self.assertEqual(model.current_pressed & self.c["INPUT_B"], 0)
        self.assertEqual(model.state, self.c["ENGINE_STATE_ACTIVE"])

    def test_held_current_b_without_edge_cannot_commit(self) -> None:
        model = self.new_model()
        model.step(0)
        model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"])
        model.actors[0].x = 80 << 8
        model.actors[0].y = 112 << 8
        current = model.actors[6]
        current.x = 288 << 8
        current.y = 112 << 8
        current.input_held = self.c["INPUT_B"]
        self.assertEqual(model.step(self.c["INPUT_B"]), 0)
        self.assertEqual(model.current_pressed & self.c["INPUT_B"], 0)
        self.assertEqual(model.state, self.c["ENGINE_STATE_ACTIVE"])

    def test_door_change_applies_after_all_actor_movement(self) -> None:
        model = self.new_model()
        model.step(self.c["INPUT_RIGHT"])
        model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"])
        model.actors[0].x = 71 << 8
        model.actors[0].y = 112 << 8
        model.actors[6].x = 108 << 8
        model.actors[6].y = 112 << 8
        self.assertEqual(model.step(self.c["INPUT_RIGHT"]), 0)
        self.assertEqual(model.actors[6].x, 108 << 8)
        self.assertTrue(model.door_state & self.c["B01_DEVICE_STATE_DOOR_OPEN"])
        self.assertEqual(model.step(self.c["INPUT_RIGHT"]), 0)
        self.assertEqual(model.actors[6].x, (109 << 8) + 0x80)

    def test_full_capacity_commit_without_selection_changes_no_bank(self) -> None:
        model = self.new_model()
        self.fill_capacity(model)
        model.step(0)
        banks_before = model.replays.banks_bytes()
        descriptors_before = tuple(
            (item.length, item.crc, item.flags, item.generation)
            for item in model.descriptors
        )
        order_before = tuple(model.echo_order)
        current_before = model.current_bank
        attempt_before = model.attempt
        self.assertEqual(
            model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]),
            self.c["GAMEPLAY_RESULT_REPLACEMENT_REQUIRED"],
        )
        self.assertEqual(model.replays.banks_bytes(), banks_before)
        self.assertEqual(
            tuple(
                (item.length, item.crc, item.flags, item.generation)
                for item in model.descriptors
            ),
            descriptors_before,
        )
        self.assertEqual(tuple(model.echo_order), order_before)
        self.assertEqual(model.current_bank, current_before)
        self.assertEqual(model.attempt, attempt_before)
        self.assertTrue(model.timeline_flags & self.c["TIMELINE_FLAG_COMMIT_BLOCKED"])

    def test_protection_oldest_and_explicit_replacement(self) -> None:
        model = self.new_model()
        self.fill_capacity(model)
        model.step(0)
        self.assertEqual(model.set_echo_protected(0, True), 0)
        self.assertEqual(model.select_oldest_replacement(), 0)
        self.assertEqual(model.selected_ordinal, 1)
        self.assertTrue(
            model.timeline_flags & self.c["TIMELINE_FLAG_SELECTION_OLDEST"]
        )
        self.assertEqual(
            model.select_replacement(0),
            self.c["GAMEPLAY_RESULT_PROTECTED"],
        )
        self.assertIsNone(model.selected_ordinal)
        self.assertEqual(model.set_echo_protected(0, False), 0)
        self.assertEqual(model.select_replacement(2), 0)
        self.assertTrue(
            model.timeline_flags & self.c["TIMELINE_FLAG_SELECTION_EXPLICIT"]
        )
        self.assertEqual(model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]), 0)
        self.assertEqual(model.echo_order, [6, 0, 2, 3, 4, 5])
        self.assertEqual(model.current_bank, 1)
        self.assertIsNone(model.selected_ordinal)
        self.assertTrue(
            model.timeline_flags & self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
        )

    def test_all_protected_oldest_selection_fails_visible(self) -> None:
        model = self.new_model()
        self.fill_capacity(model)
        for ordinal in range(6):
            self.assertEqual(model.set_echo_protected(ordinal, True), 0)
        self.assertEqual(
            model.select_oldest_replacement(),
            self.c["GAMEPLAY_RESULT_PROTECTED"],
        )
        self.assertIsNone(model.selected_ordinal)
        self.assertTrue(model.timeline_flags & self.c["TIMELINE_FLAG_COMMIT_BLOCKED"])
        self.assertTrue(
            model.gameplay_flags & self.c["GAMEPLAY_FLAG_REPLACEMENT_VISIBLE"]
        )

    def test_protecting_selected_echo_invalidates_selection(self) -> None:
        model = self.new_model()
        self.fill_capacity(model)
        self.assertEqual(model.select_replacement(3), 0)
        self.assertEqual(model.selected_ordinal, 3)
        self.assertEqual(model.set_echo_protected(3, True), 0)
        self.assertIsNone(model.selected_ordinal)
        self.assertFalse(
            model.timeline_flags & self.c["TIMELINE_FLAG_SELECTION_VALID"]
        )
        self.assertTrue(
            model.timeline_flags & self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
        )

    def test_invalid_selection_does_not_mutate_replay_storage(self) -> None:
        model = self.new_model()
        self.fill_capacity(model)
        before = model.replay_region_bytes()
        self.assertEqual(
            model.select_replacement(6),
            self.c["GAMEPLAY_RESULT_INVALID_SELECTION"],
        )
        after = model.replay_region_bytes()
        # Only the two-byte timeline result may differ.
        self.assertEqual(before[:-2], after[:-2])
        self.assertEqual(model.echo_order, [6, 0, 1, 2, 3, 4])
        self.assertEqual(model.current_bank, 5)

    def test_reused_bank_generation_advances_and_zero_is_skipped(self) -> None:
        model = self.new_model()
        self.fill_capacity(model)
        model.step(0)
        old_generation = model.descriptors[1].generation
        self.assertEqual(model.select_replacement(2), 0)
        self.assertEqual(model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]), 0)
        self.assertEqual(model.current_bank, 1)
        expected = (old_generation + 1) & 0xFF or 1
        self.assertEqual(model.descriptors[1].generation, expected)
        self.assertEqual(model.descriptors[1].flags, self.c["REPLAY_FLAG_CURRENT"])
        self.assertEqual(model.replays.bank(1), b"\x00" * 720)


if __name__ == "__main__":
    unittest.main()
