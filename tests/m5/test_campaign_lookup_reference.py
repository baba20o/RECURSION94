"""Differential contract for the live-count-only campaign lookup fast path.

These host tests prove the algorithm over legal pool-count fixtures; they do
not execute 68000 code or replace current-ROM replay and native timing proof.
The paired controller source tests bind the proposed assembly to this narrow
algorithm without introducing a canonical-slot or object-type shortcut.
"""

from __future__ import annotations

import random
import unittest
from pathlib import Path

from tools.m3_reference.contracts import parse_layout_constants


ROOT = Path(__file__).resolve().parents[2]
C = parse_layout_constants(ROOT / "src/game/engine_layout.asm")
RAM_BASE = 0xFF0000
NOT_FOUND = (0xFFFF, 0xFFFF, 0)
POOL_NAMES = (
    ("ENEMY_POOL", "ENEMY_CAPACITY"),
    ("PROJECTILE_POOL", "PROJECTILE_CAPACITY"),
    ("DEVICE_POOL", "DEVICE_CAPACITY"),
    ("CARRYABLE_POOL", "CARRYABLE_CAPACITY"),
    ("HAZARD_POOL", "HAZARD_CAPACITY"),
    ("BOSS_COMPONENT_POOL", "BOSS_COMPONENT_CAPACITY"),
    ("EFFECT_POOL", "EFFECT_CAPACITY"),
)
POOLS = tuple(
    (index, C[base], C[capacity])
    for index, (base, capacity) in enumerate(POOL_NAMES, 1)
)
TRANSIENT_TYPES = frozenset(
    (C["POOL_TYPE_PROJECTILE"], C["POOL_TYPE_EFFECT"])
)


class PoolImage:
    def __init__(self) -> None:
        self.ram = bytearray(
            (index * 17 + 3) & 0xFF for index in range(0x6000)
        )
        for pool_type, base, capacity in POOLS:
            start = base - RAM_BASE
            size = capacity * C["OBJECT_BYTES"]
            self.ram[start:start + size] = bytes(size)
            self.ram[C["ENGINE_POOL_COUNTS"] - RAM_BASE + pool_type - 1] = 0

    def slot_offset(self, pool_type: int, slot: int) -> int:
        _, base, capacity = POOLS[pool_type - 1]
        if not 0 <= slot < capacity:
            raise ValueError("physical slot outside bounded pool")
        return base - RAM_BASE + slot * C["OBJECT_BYTES"]

    def live_count(self, pool_type: int) -> int:
        _, _, capacity = POOLS[pool_type - 1]
        return sum(
            bool(self.ram[self.slot_offset(pool_type, slot) + C["OBJECT_ACTIVE"]])
            for slot in range(capacity)
        )

    def validate_counts(self) -> None:
        for pool_type, _, _ in POOLS:
            stored = self.ram[
                C["ENGINE_POOL_COUNTS"] - RAM_BASE + pool_type - 1
            ]
            if stored != self.live_count(pool_type):
                raise ValueError("fixture violates legal live-pool count invariant")

    def write_slot(
        self, pool_type: int, slot: int, logical_id: int, *, active: int = 1
    ) -> None:
        start = self.slot_offset(pool_type, slot)
        self.ram[start:start + C["OBJECT_BYTES"]] = bytes(
            (logical_id + index * 7) & 0xFF
            for index in range(C["OBJECT_BYTES"])
        )
        self.ram[start + C["OBJECT_ACTIVE"]] = active
        self.ram[start + C["OBJECT_LOGICAL_ID"]] = logical_id
        self.ram[C["ENGINE_POOL_COUNTS"] - RAM_BASE + pool_type - 1] = (
            self.live_count(pool_type)
        )

    def free_slot(self, pool_type: int, slot: int) -> None:
        start = self.slot_offset(pool_type, slot)
        self.ram[start:start + C["OBJECT_BYTES"]] = bytes(C["OBJECT_BYTES"])
        self.ram[C["ENGINE_POOL_COUNTS"] - RAM_BASE + pool_type - 1] = (
            self.live_count(pool_type)
        )

    def allocate_lowest(self, pool_type: int, logical_id: int) -> int:
        _, _, capacity = POOLS[pool_type - 1]
        for slot in range(capacity):
            if not self.ram[self.slot_offset(pool_type, slot) + C["OBJECT_ACTIVE"]]:
                self.write_slot(pool_type, slot, logical_id)
                return slot
        return 0xFFFF


def original_lookup(
    image: PoolImage,
    logical_id: int,
    *,
    semantic_count: int = C["TOTAL_POOL_CAPACITY"],
    profile_valid: bool = True,
) -> tuple[int, int, int]:
    if not profile_valid or not 0 <= logical_id < semantic_count:
        return NOT_FOUND
    # Independent specification: first active match in pool/physical order.
    candidates = (
        (pool_type, slot, base + slot * C["OBJECT_BYTES"])
        for pool_type, base, capacity in POOLS
        if pool_type not in TRANSIENT_TYPES
        for slot in range(capacity)
    )
    return next(
        (
            candidate
            for candidate in candidates
            if image.ram[candidate[2] - RAM_BASE + C["OBJECT_ACTIVE"]]
            and image.ram[candidate[2] - RAM_BASE + C["OBJECT_LOGICAL_ID"]]
            == logical_id
        ),
        NOT_FOUND,
    )


def skip_empty_lookup(
    image: PoolImage,
    logical_id: int,
    *,
    semantic_count: int = C["TOTAL_POOL_CAPACITY"],
    profile_valid: bool = True,
    reads: list[tuple[int, int]] | None = None,
) -> tuple[int, int, int]:
    if not profile_valid or not 0 <= logical_id < semantic_count:
        return NOT_FOUND
    for pool_type, base, capacity in POOLS:
        if pool_type in TRANSIENT_TYPES:
            continue
        if image.ram[C["ENGINE_POOL_COUNTS"] - RAM_BASE + pool_type - 1] == 0:
            continue
        for slot in range(capacity):
            if reads is not None:
                reads.append((pool_type, slot))
            address = base + slot * C["OBJECT_BYTES"]
            start = address - RAM_BASE
            if (
                image.ram[start + C["OBJECT_ACTIVE"]]
                and image.ram[start + C["OBJECT_LOGICAL_ID"]] == logical_id
            ):
                return pool_type, slot, address
    return NOT_FOUND


class CampaignLookupReferenceTests(unittest.TestCase):
    def assert_equivalent(
        self, image: PoolImage, logical_id: int, **kwargs
    ) -> tuple[int, int, int]:
        image.validate_counts()
        before = bytes(image.ram)
        expected = original_lookup(image, logical_id, **kwargs)
        reads: list[tuple[int, int]] = []
        observed = skip_empty_lookup(image, logical_id, reads=reads, **kwargs)
        self.assertEqual(observed, expected)
        self.assertEqual(
            bytes(image.ram), before,
            "lookup mutated authoritative or unrelated RAM",
        )
        self.assertEqual(
            reads, sorted(set(reads)), "scan order or boundedness changed"
        )
        self.assertTrue(
            all(pool_type not in TRANSIENT_TYPES for pool_type, _ in reads)
        )
        return observed

    def test_empty_pools_skip_all_slots_even_with_stale_inactive_payload(self) -> None:
        image = PoolImage()
        for pool_type, _, capacity in POOLS:
            for slot in range(capacity):
                image.write_slot(pool_type, slot, 7, active=0)
        reads: list[tuple[int, int]] = []
        self.assertEqual(self.assert_equivalent(image, 7), NOT_FOUND)
        self.assertEqual(skip_empty_lookup(image, 7, reads=reads), NOT_FOUND)
        self.assertEqual(reads, [])

    def test_every_physical_slot_and_pool_preserves_identity(self) -> None:
        for pool_type, base, capacity in POOLS:
            for slot in range(capacity):
                with self.subTest(pool_type=pool_type, slot=slot):
                    image = PoolImage()
                    image.write_slot(pool_type, slot, 37, active=0x80)
                    expected = (
                        NOT_FOUND if pool_type in TRANSIENT_TYPES
                        else (pool_type, slot, base + slot * C["OBJECT_BYTES"])
                    )
                    self.assertEqual(self.assert_equivalent(image, 37), expected)
                    self.assertEqual(self.assert_equivalent(image, 38), NOT_FOUND)

    def test_nonzero_count_never_truncates_capacity_scan(self) -> None:
        image = PoolImage()
        pool_type, base, capacity = POOLS[2]
        image.write_slot(pool_type, capacity - 1, 139)
        self.assertEqual(
            self.assert_equivalent(image, 139),
            (pool_type, capacity - 1, base + (capacity - 1) * C["OBJECT_BYTES"]),
        )
        reads: list[tuple[int, int]] = []
        skip_empty_lookup(image, 139, reads=reads)
        self.assertEqual(reads, [(pool_type, slot) for slot in range(capacity)])

    def test_free_reuse_and_moved_identity_preserve_results(self) -> None:
        image = PoolImage()
        pool_type, base, _ = POOLS[0]
        self.assertEqual(image.allocate_lowest(pool_type, 10), 0)
        self.assertEqual(image.allocate_lowest(pool_type, 11), 1)
        self.assertEqual(self.assert_equivalent(image, 10), (pool_type, 0, base))
        image.free_slot(pool_type, 0)
        self.assertEqual(self.assert_equivalent(image, 10), NOT_FOUND)
        self.assertEqual(image.allocate_lowest(pool_type, 12), 0)
        self.assertEqual(self.assert_equivalent(image, 12), (pool_type, 0, base))
        image.free_slot(pool_type, 1)
        self.assertEqual(image.allocate_lowest(pool_type, 10), 1)
        self.assertEqual(
            self.assert_equivalent(image, 10),
            (pool_type, 1, base + C["OBJECT_BYTES"]),
        )

    def test_transient_collisions_and_duplicate_precedence_are_unchanged(self) -> None:
        image = PoolImage()
        for pool_type, _, capacity in POOLS:
            image.write_slot(pool_type, capacity - 1, 9)
        image.write_slot(C["POOL_TYPE_DEVICE"], 0, 9)
        self.assertEqual(
            self.assert_equivalent(image, 9),
            (
                C["POOL_TYPE_ENEMY"], C["ENEMY_CAPACITY"] - 1,
                C["ENEMY_POOL"] + (C["ENEMY_CAPACITY"] - 1) * C["OBJECT_BYTES"],
            ),
        )
        image.free_slot(C["POOL_TYPE_ENEMY"], C["ENEMY_CAPACITY"] - 1)
        self.assertEqual(
            self.assert_equivalent(image, 9),
            (C["POOL_TYPE_DEVICE"], 0, C["DEVICE_POOL"]),
        )

    def test_semantic_gate_still_precedes_live_pool_access(self) -> None:
        image = PoolImage()
        image.write_slot(C["POOL_TYPE_DEVICE"], 0, 7)
        for logical_id, kwargs in (
            (7, {"profile_valid": False}), (7, {"semantic_count": 7}),
            (-1, {}), (140, {}), (255, {}),
        ):
            with self.subTest(logical_id=logical_id, kwargs=kwargs):
                reads: list[tuple[int, int]] = []
                self.assertEqual(self.assert_equivalent(image, logical_id, **kwargs), NOT_FOUND)
                self.assertEqual(
                    skip_empty_lookup(image, logical_id, reads=reads, **kwargs),
                    NOT_FOUND,
                )
                self.assertEqual(reads, [])

    def test_bounded_deterministic_sparse_and_full_pool_differential(self) -> None:
        generator = random.Random(0x94C0)
        for sample in range(32):
            image = PoolImage()
            for pool_type, _, capacity in POOLS:
                for slot in range(capacity):
                    active = 1 if sample == 0 else generator.randrange(2)
                    image.write_slot(
                        pool_type, slot, generator.randrange(140), active=active
                    )
            for logical_id in range(140):
                with self.subTest(sample=sample, logical_id=logical_id):
                    self.assert_equivalent(image, logical_id)

    def test_inconsistent_counts_are_outside_the_equivalence_claim(self) -> None:
        image = PoolImage()
        image.write_slot(C["POOL_TYPE_DEVICE"], 0, 7)
        image.ram[
            C["ENGINE_POOL_COUNTS"] - RAM_BASE + C["POOL_TYPE_DEVICE"] - 1
        ] = 0
        with self.assertRaisesRegex(ValueError, "legal live-pool count"):
            image.validate_counts()
        self.assertNotEqual(original_lookup(image, 7), skip_empty_lookup(image, 7))


if __name__ == "__main__":
    unittest.main()
