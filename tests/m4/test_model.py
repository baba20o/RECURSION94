from __future__ import annotations

import binascii
import unittest
from pathlib import Path

from tools.m4_reference.contracts import load_gameplay_contract
from tools.m4_reference.model import (
    GameplayModel,
    canonical_b01_first_attempt,
    crc32_iso_hdlc,
    run_b01_solution,
)
from tools.m4_reference.report import (
    GOLDEN_B01_TERMINAL_CRC,
    GOLDEN_MUTATED_TERMINAL_CRC,
)


ROOT = Path(__file__).resolve().parents[2]


class GameplayReferenceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_gameplay_contract(
            ROOT, verify_source_hashes=False
        )
        cls.c = cls.contract.constants

    def new_model(self) -> GameplayModel:
        return GameplayModel(self.contract)

    def test_crc32_iso_hdlc_known_vectors(self) -> None:
        vectors = (b"", b"123456789", bytes(range(256)), b"\x00" * 1024)
        for payload in vectors:
            with self.subTest(length=len(payload)):
                self.assertEqual(
                    crc32_iso_hdlc(payload),
                    binascii.crc32(payload) & 0xFFFFFFFF,
                )

    def test_replay_region_is_exact_big_endian_shape(self) -> None:
        model = self.new_model()
        region = model.replay_region_bytes()
        self.assertEqual(len(region), 5120)
        self.assertEqual(region[:5040], b"\x00" * 5040)
        descriptor6 = 5040 + 6 * 8
        self.assertEqual(region[descriptor6 : descriptor6 + 8], b"\0" * 6 + b"\x04\x01")
        self.assertEqual(region[5096:5104], b"R94T\x00\x01\x00\x18")
        self.assertEqual(region[5104:5114], b"\x00\x06" + b"\xFF" * 6 + b"\xFF\x01")
        self.assertTrue(model.replays.canaries_intact())

    def test_input_sanitation_edges_and_recording(self) -> None:
        model = self.new_model()
        self.assertEqual(model.sanitize_input(0x8F), 0)
        self.assertEqual(model.sanitize_input(0xFF), 0x70)
        self.assertEqual(model.step(0xFF), 0)
        self.assertEqual(model.replays.bank(6)[0], 0x70)
        self.assertEqual(model.current_pressed, 0x70)
        self.assertEqual(model.step(0xFF), 0)
        self.assertEqual(model.current_pressed, 0)
        self.assertEqual(model.step(0), 0)
        self.assertEqual(model.current_released, 0x70)
        self.assertEqual(model.timeline_attempt_length, 3)
        self.assertEqual(model.descriptors[6].length, 3)
        self.assertTrue(all(value & 0x80 == 0 for value in model.replays.bank(6)))

    def test_partial_commit_length_crc_and_zero_tail(self) -> None:
        model = self.new_model()
        inputs = canonical_b01_first_attempt(self.c)
        for value in inputs:
            self.assertEqual(model.step(value), 0)
        self.assertEqual(model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]), 0)
        descriptor = model.descriptors[6]
        self.assertEqual(descriptor.length, 40)
        self.assertEqual(descriptor.crc, crc32_iso_hdlc(bytes(inputs)))
        self.assertNotEqual(descriptor.crc, crc32_iso_hdlc(bytes(model.replays.bank(6))))
        self.assertEqual(model.replays.bank(6)[40:], b"\x00" * 680)
        self.assertEqual(descriptor.flags & 3, 3)

    def test_oldest_to_newest_playback_controls_physical_slots(self) -> None:
        model = self.new_model()
        self.assertEqual(model.step(self.c["INPUT_LEFT"] | self.c["INPUT_A"]), 0)
        self.assertEqual(model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]), 0)
        self.assertEqual(model.step(self.c["INPUT_RIGHT"] | self.c["INPUT_A"]), 0)
        self.assertEqual(model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"]), 0)
        self.assertEqual(model.echo_order, [6, 0])
        self.assertEqual(model.step(0), 0)
        projectiles = self.pools(model)
        self.assertTrue(projectiles[0].active)
        self.assertTrue(projectiles[1].active)
        self.assertLess(projectiles[0].vx, 0)
        self.assertGreater(projectiles[1].vx, 0)
        self.assertEqual(model.actors[0].logical_id, 6)
        self.assertEqual(model.actors[1].logical_id, 0)
        self.assertEqual(model.actors[6].logical_id, 1)

    def pools(self, model: GameplayModel):
        return model.pools[self.c["POOL_TYPE_PROJECTILE"]].slots

    def test_echoes_do_not_body_block(self) -> None:
        model = self.new_model()
        for _ in range(10):
            model.step(self.c["INPUT_RIGHT"])
        model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"])
        for _ in range(10):
            model.step(self.c["INPUT_RIGHT"])
            self.assertEqual(model.actors[0].x, model.actors[6].x)

    def test_real_reset_preserves_replay_but_rebuilds_ordinary_state(self) -> None:
        model = self.new_model()
        model.step(self.c["INPUT_A"])
        self.assertEqual(self.pools(model)[0].active, 1)
        recorded = bytes(model.replays.bank(6))
        model.commit_and_reset(self.c["GAMEPLAY_COMMIT_MANUAL"])
        self.assertEqual(bytes(model.replays.bank(6)), recorded)
        self.assertEqual(model.echo_order, [6])
        self.assertEqual(model.current_bank, 0)
        self.assertFalse(any(slot.active for slot in self.pools(model)))
        device = model.pools[self.c["POOL_TYPE_DEVICE"]].slots[0]
        self.assertEqual(device.state, 0)
        self.assertEqual(model.door_state, 0)
        self.assertEqual(model.attempt, 1)
        self.assertEqual(model.tick, 0)
        for actor in (model.actors[0], model.actors[6]):
            self.assertEqual((actor.x, actor.y), (32 << 8, 112 << 8))

    def test_b01_solution_repeats_exact_projection_and_signature(self) -> None:
        first = self.new_model()
        second = self.new_model()
        self.assertEqual(run_b01_solution(first), 1)
        self.assertEqual(run_b01_solution(second), 1)
        self.assertEqual(first.projection, second.projection)
        self.assertEqual(first.state_crc, second.state_crc)
        self.assertEqual(first.state_crc, GOLDEN_B01_TERMINAL_CRC)
        self.assertEqual(first.state_crc, 0x8762E745)
        self.assertEqual(first.tick, 166)
        self.assertEqual(first.terminal_reason, 0x0100)
        self.assertEqual(first.actors[0].x, 80 << 8)
        self.assertTrue(280 << 8 <= first.actors[6].x < 296 << 8)

    def test_mutated_echo_action_changes_terminal_signature(self) -> None:
        canonical = self.new_model()
        run_b01_solution(canonical)
        inputs = list(canonical_b01_first_attempt(self.c))
        inputs[35] = self.c["INPUT_A"]
        mutated = self.new_model()
        self.assertEqual(run_b01_solution(mutated, first_attempt=inputs), 1)
        self.assertEqual(mutated.state_crc, GOLDEN_MUTATED_TERMINAL_CRC)
        self.assertNotEqual(mutated.state_crc, canonical.state_crc)
        self.assertEqual(
            mutated.pools[self.c["POOL_TYPE_PROJECTILE"]].high_water,
            1,
        )

    def test_tick_720_is_terminal_and_post_terminal_is_immutable(self) -> None:
        model = self.new_model()
        for _ in range(719):
            self.assertEqual(model.step(0x80), 0)
        self.assertEqual(model.tick, 719)
        self.assertEqual(model.step(0x80), self.c["GAMEPLAY_RESULT_TIMEOUT"])
        self.assertEqual(model.tick, 720)
        self.assertEqual(model.timeline_attempt_length, 720)
        self.assertEqual(model.descriptors[6].length, 720)
        self.assertTrue(model.timeline_flags & self.c["TIMELINE_FLAG_RECORDING_FULL"])
        before = model.replay_region_bytes()
        terminal_crc = model.state_crc
        self.assertEqual(model.step(0), self.c["GAMEPLAY_RESULT_TIMEOUT"])
        self.assertEqual(model.replay_region_bytes(), before)
        self.assertEqual(model.state_crc, terminal_crc)
        self.assertTrue(model.replays.canaries_intact())


if __name__ == "__main__":
    unittest.main()
