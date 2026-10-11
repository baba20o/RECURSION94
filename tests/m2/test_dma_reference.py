from __future__ import annotations

import unittest
from pathlib import Path

from tools.dma_reference import DmaReferenceQueue, load_dma_contract


ROOT = Path(__file__).resolve().parents[2]


class DmaAssemblyContractTests(unittest.TestCase):
    def test_model_is_bound_to_current_assembly_equates(self) -> None:
        contract = load_dma_contract(ROOT)
        self.assertEqual(contract.capacity, 32)
        self.assertEqual(contract.entry_bytes, 12)
        self.assertEqual(contract.required_flag, 0x8000)
        self.assertEqual(contract.processed_flag, 0x4000)
        self.assertEqual(contract.priority_mask, 3)
        self.assertEqual(contract.priority_levels, 4)
        self.assertEqual(contract.word_limit, 2048)
        self.assertEqual(contract.source_window_bytes, 0x20000)
        self.assertEqual(contract.entries_base, contract.queue_base + 0x10)
        self.assertLessEqual(
            contract.queue_storage_end, contract.input_state_base
        )
        self.assertEqual(len(contract.assembly_sha256), 64)


class DmaReferenceVectors(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_dma_contract(ROOT)
        self.queue = DmaReferenceQueue(self.contract)
        self.destination = self.contract.vram_ranges[0][0]

    def enqueue(
        self,
        identifier: str,
        *,
        source: int = 0x1000,
        destination: int | None = None,
        words: int = 1,
        flags: int = 0,
    ) -> bool:
        return self.queue.enqueue(
            identifier,
            source=source,
            destination=(
                self.destination if destination is None else destination
            ),
            words=words,
            flags=flags,
        )

    def test_capacity_accepts_32_and_rejects_33rd_without_canary_damage(
        self,
    ) -> None:
        for index in range(32):
            self.assertTrue(self.enqueue(f"entry-{index}"))
        self.assertFalse(self.enqueue("overflow"))
        self.assertEqual(self.queue.depth, 32)
        self.assertEqual(self.queue.high_water, 32)
        self.assertEqual(self.queue.overflow_count, 1)
        self.assertEqual(self.queue.canaries, (0xA5, 0x5A))

    def test_invalid_source_word_destination_and_flag_vectors(self) -> None:
        vectors = (
            {"source": 0x1001},
            {"source": 0x00100000},
            {"source": 0x0001FFFE, "words": 2},
            {"words": 0},
            {"words": self.contract.word_limit + 1},
            {"destination": self.destination + 1},
            {"destination": 0},
            {
                "destination": self.contract.vram_ranges[4][1] - 2,
                "words": 2,
            },
            {"flags": self.contract.processed_flag},
        )
        for index, overrides in enumerate(vectors):
            with self.subTest(vector=index, overrides=overrides):
                arguments = {
                    "source": 0x1000,
                    "destination": self.destination,
                    "words": 1,
                    "flags": 0,
                    **overrides,
                }
                self.assertFalse(
                    self.queue.enqueue(f"invalid-{index}", **arguments)
                )
        self.assertEqual(self.queue.invalid_count, len(vectors))
        self.assertEqual(self.queue.depth, 0)
        self.assertEqual(self.queue.canaries, (0xA5, 0x5A))

    def test_required_invalid_or_overflow_is_counted_as_drop(self) -> None:
        required = self.contract.required_flag
        self.assertFalse(self.enqueue("invalid", words=0, flags=required))
        for index in range(self.contract.capacity):
            self.assertTrue(self.enqueue(f"fill-{index}"))
        self.assertFalse(self.enqueue("required-overflow", flags=required))
        self.assertEqual(self.queue.required_drop_count, 2)

    def test_required_before_optional_with_stable_priority_order(self) -> None:
        required = self.contract.required_flag
        vectors = (
            ("optional-normal", 2),
            ("required-low", required | 3),
            ("required-critical-a", required | 0),
            ("optional-critical-a", 0),
            ("required-critical-b", required | 0),
            ("optional-critical-b", 0),
            ("required-high", required | 1),
        )
        for identifier, flags in vectors:
            self.assertTrue(self.enqueue(identifier, flags=flags))
        result = self.queue.commit()
        self.assertEqual(
            result["issued"],
            [
                "required-critical-a",
                "required-critical-b",
                "required-high",
                "required-low",
                "optional-critical-a",
                "optional-critical-b",
                "optional-normal",
            ],
        )
        self.assertEqual(result["words"], len(vectors))
        self.assertEqual(result["deferred"], [])
        self.assertEqual(self.queue.canaries, (0xA5, 0x5A))

    def test_over_budget_optional_is_deferred_then_compacted(self) -> None:
        self.assertTrue(
            self.enqueue(
                "required",
                words=1800,
                flags=self.contract.required_flag,
            )
        )
        self.assertTrue(self.enqueue("optional", words=300))
        first = self.queue.commit()
        self.assertEqual(first["issued"], ["required"])
        self.assertEqual(first["words"], 1800)
        self.assertEqual(first["deferred"], ["optional"])
        self.assertEqual(self.queue.entries[0].age, 1)
        self.assertEqual(self.queue.deferred_count, 1)
        self.assertEqual(self.queue.canaries, (0xA5, 0x5A))

        second = self.queue.commit()
        self.assertEqual(second["issued"], ["optional"])
        self.assertEqual(second["words"], 300)
        self.assertEqual(second["deferred"], [])

    def test_over_budget_required_is_dropped_not_deferred(self) -> None:
        required = self.contract.required_flag
        self.assertTrue(
            self.enqueue("required-first", words=1500, flags=required)
        )
        self.assertTrue(
            self.enqueue("required-second", words=1000, flags=required)
        )
        result = self.queue.commit()
        self.assertEqual(result["issued"], ["required-first"])
        self.assertEqual(result["deferred"], [])
        self.assertEqual(result["required_drop_count"], 1)
        self.assertEqual(self.queue.canaries, (0xA5, 0x5A))


if __name__ == "__main__":
    unittest.main()
