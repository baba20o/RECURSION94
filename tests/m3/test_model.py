from __future__ import annotations

import binascii
import unittest
from pathlib import Path

from tools.m3_reference.contracts import load_engine_contract
from tools.m3_reference.model import (
    EngineModel,
    FixedPool,
    canonical_script_input,
    crc32_iso_hdlc,
    run_script,
)
from tools.m3_reference.report import GOLDEN_TERMINAL_CRC


ROOT = Path(__file__).resolve().parents[2]


class EngineReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_engine_contract(
            ROOT, verify_source_hashes=False
        )
        cls.c = cls.contract.constants

    def new_loaded_model(self, encounter_id: int = 0) -> EngineModel:
        model = EngineModel(self.contract)
        self.assertEqual(model.load_encounter(encounter_id), 0)
        return model

    def test_crc32_iso_hdlc_known_vectors(self) -> None:
        vectors = (
            b"",
            b"123456789",
            bytes(range(256)),
            b"\x00" * 1024,
        )
        for payload in vectors:
            with self.subTest(length=len(payload)):
                self.assertEqual(
                    crc32_iso_hdlc(payload),
                    binascii.crc32(payload) & 0xFFFFFFFF,
                )
        self.assertEqual(crc32_iso_hdlc(b"123456789"), 0xCBF43926)

    def test_projection_is_exact_explicit_big_endian_shape(self) -> None:
        model = self.new_loaded_model()
        projection = model.projection
        self.assertEqual(len(projection), 3638)
        self.assertEqual(projection[:8], b"R94P\x00\x01\x0E\x36")
        self.assertEqual(projection[8:16], b"\x00\x00\x00\x00\x00\x00\x00\x01")
        self.assertEqual(projection[44:48], (32 << 8).to_bytes(4, "big"))
        self.assertEqual(projection[48:52], (112 << 8).to_bytes(4, "big"))
        first_pool_header = self.c["PROJECTION_HEADER_BYTES"] + (
            self.c["ACTOR_CAPACITY"] * self.c["ACTOR_PROJECTION_BYTES"]
        )
        self.assertEqual(
            projection[first_pool_header : first_pool_header + 4],
            bytes((1, 16, 1, 1)),
        )
        projectile_header = first_pool_header + 4 + 16 * 24
        self.assertEqual(
            projection[projectile_header : projectile_header + 4],
            bytes((2, 32, 0, 0)),
        )
        self.assertEqual(crc32_iso_hdlc(projection), model.state_crc)

    def test_input_sanitation_and_edges(self) -> None:
        model = self.new_loaded_model()
        self.assertEqual(model.sanitize_input(0x8F), 0)
        self.assertEqual(model.sanitize_input(0xFF), 0x70)
        self.assertEqual(model.step(0xFF), 0)
        self.assertEqual(model.input_held, 0x70)
        self.assertEqual(model.input_pressed, 0x70)
        self.assertEqual(model.step(0xFF), 0)
        self.assertEqual(model.input_pressed, 0)
        self.assertEqual(model.step(0), 0)
        self.assertEqual(model.input_released, 0x70)

    def test_fractional_axis_collision_is_not_truncated(self) -> None:
        model = self.new_loaded_model()
        actor = model.actors[0]
        actor.x = (106 << 8) + 0x40
        actor.y = 112 << 8
        model.step(self.c["INPUT_RIGHT"])
        self.assertEqual(actor.x, (107 << 8) + 0xC0)
        self.assertFalse(actor.flags & self.c["ACTOR_FLAG_COLLISION_X"])
        model.step(self.c["INPUT_RIGHT"])
        self.assertEqual(actor.x, 108 << 8)
        self.assertEqual(actor.y, 112 << 8)
        self.assertTrue(actor.flags & self.c["ACTOR_FLAG_COLLISION_X"])
        self.assertTrue(model.flags & self.c["ENGINE_FLAG_COLLISION"])

    def test_corner_case_proves_x_then_y_axis_order(self) -> None:
        model = self.new_loaded_model()
        actor = model.actors[0]
        actor.x = 107 << 8
        actor.y = 43 << 8
        model.step(self.c["INPUT_RIGHT"] | self.c["INPUT_DOWN"])
        self.assertEqual(actor.x, (108 << 8) + 0x10)
        self.assertEqual(actor.y, 44 << 8)
        self.assertFalse(actor.flags & self.c["ACTOR_FLAG_COLLISION_X"])
        self.assertTrue(actor.flags & self.c["ACTOR_FLAG_COLLISION_Y"])

    def test_every_pool_capacity_overflow_canary_and_lowest_reuse(self) -> None:
        self.assertEqual(self.c["ACTOR_CAPACITY"], 7)
        self.assertEqual(len(self.new_loaded_model().actors), 7)
        for pool_type, capacity in self.contract.pool_capacities.items():
            with self.subTest(pool_type=pool_type):
                pool = FixedPool(
                    pool_type,
                    capacity,
                    self.contract.pool_overflow_masks[pool_type],
                )
                indices = []
                for _ in range(capacity):
                    allocated = pool.allocate_lowest()
                    self.assertIsNotNone(allocated)
                    indices.append(allocated[0])
                self.assertEqual(indices, list(range(capacity)))
                self.assertEqual(pool.count, capacity)
                self.assertEqual(pool.high_water, capacity)
                self.assertIsNone(pool.allocate_lowest())
                self.assertTrue(pool.canaries_intact())
                pool.free(0)
                reused = pool.allocate_lowest()
                self.assertIsNotNone(reused)
                self.assertEqual(reused[0], 0)
                self.assertTrue(pool.canaries_intact())

    def test_engine_overflow_sets_exact_flag_without_growth(self) -> None:
        model = self.new_loaded_model()
        projectile_type = self.c["POOL_TYPE_PROJECTILE"]
        pool = model.pools[projectile_type]
        for _ in range(pool.capacity):
            self.assertIsNotNone(pool.allocate_lowest())
        self.assertEqual(len(pool.slots), pool.capacity)
        self.assertEqual(model.step(self.c["INPUT_A"]), 0)
        self.assertEqual(
            model.overflow_flags & self.c["OVERFLOW_PROJECTILE"],
            self.c["OVERFLOW_PROJECTILE"],
        )
        self.assertEqual(model.last_error, self.c["ENGINE_ERROR_POOL_OVERFLOW"])
        self.assertEqual(len(pool.slots), pool.capacity)
        self.assertTrue(pool.canaries_intact())

    def test_reset_restores_exact_actor_and_pool_ram(self) -> None:
        model = self.new_loaded_model()
        canonical = model.ordinary_state_bytes()
        self.assertEqual(
            len(canonical),
            self.c["ENGINE_POOL_REGION_END"] - self.c["ENGINE_ACTOR_BASE"],
        )
        for tick in range(100):
            model.step(canonical_script_input(tick, dict(self.c)))
        self.assertNotEqual(model.ordinary_state_bytes(), canonical)
        self.assertEqual(model.reset(), 0)
        self.assertEqual(model.ordinary_state_bytes(), canonical)
        self.assertEqual(model.attempt, 1)
        self.assertEqual(model.tick, 0)
        self.assertEqual(model.checkpoint_count, 1)

    def test_tick_719_is_processed_and_720_is_terminal(self) -> None:
        model = self.new_loaded_model()
        for _ in range(719):
            self.assertEqual(model.step(0), 0)
        self.assertEqual(model.tick, 719)
        self.assertEqual(model.state, self.c["ENGINE_STATE_ACTIVE"])
        self.assertEqual(model.checkpoint_count, 12)
        self.assertEqual(model.step(0), self.c["ENGINE_TERMINAL_TIMEOUT"])
        self.assertEqual(model.tick, 720)
        self.assertEqual(model.state, self.c["ENGINE_STATE_TERMINAL"])
        self.assertEqual(model.checkpoint_count, 13)
        self.assertEqual(
            tuple(tick for tick, _ in model.checkpoints),
            tuple(range(0, 721, 60)),
        )
        final_crc = model.state_crc
        self.assertEqual(model.step(0), self.c["ENGINE_TERMINAL_TIMEOUT"])
        self.assertEqual(model.tick, 720)
        self.assertEqual(model.state_crc, final_crc)

    def test_repeated_scripts_match_golden_and_mutation_diverges(self) -> None:
        inputs = [
            canonical_script_input(tick, dict(self.c))
            for tick in range(self.c["ENGINE_ATTEMPT_TICKS"])
        ]
        first = EngineModel(self.contract)
        second = EngineModel(self.contract)
        first_checkpoints = run_script(first, inputs)
        second_checkpoints = run_script(second, inputs)
        self.assertEqual(first_checkpoints, second_checkpoints)
        self.assertEqual(len(first_checkpoints), 13)
        self.assertEqual(first.state_crc, GOLDEN_TERMINAL_CRC)
        self.assertEqual(first.state_crc, 0xF92B572A)

        mutated_inputs = list(inputs)
        mutated_inputs[718] ^= self.c["INPUT_RIGHT"]
        mutated = EngineModel(self.contract)
        mutated_checkpoints = run_script(mutated, mutated_inputs)
        self.assertNotEqual(mutated_checkpoints, first_checkpoints)
        self.assertNotEqual(mutated.state_crc, first.state_crc)


if __name__ == "__main__":
    unittest.main()
