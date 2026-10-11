from __future__ import annotations

import binascii
import hashlib
import json
import random
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.build_rom import compile_crc32_lookup_tables
from tools.crc32_tables import (
    BYTE_TABLE_BYTES,
    Crc32TableError,
    OBJECT_RECORD_BYTES,
    OUTPUT_BINARY,
    OUTPUT_INCLUDE,
    OUTPUT_NAMES,
    OUTPUT_REPORT,
    POLYNOMIAL_REFLECTED,
    T16_TABLE_BYTES,
    T16_TABLE_ENTRIES,
    TRANSITION_TABLE_BYTES,
    ZERO_RECORD_DIRECT_RUNS,
    ZERO_RECORD_POWERS,
    ZERO_RECORD_TABLE_COUNTS,
    build_tables,
    check_generated,
    compile_tables,
    crc32_update_raw,
    estimate_neutral_projection_cycles,
)
from tools.m3_reference.contracts import load_engine_contract
from tools.m3_reference.model import EngineModel, canonical_script_input


ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "assets/generated/crc32"
EXPECTED_BINARY_SHA256 = (
    "10c10d1ef280fe51536fb574b08ec84398a74d8bd31b0ce75a2f5d2535940e2a"
)
EXPECTED_TABLE_SHA256 = {
    "t16": "dece7bd308b236535fa543b6c7795b60695f25db147c1d065239e3a22d55431a",
    "byte": "da5c898b03a4b8559186e7ce3b034e14aab00e7672966ce3032e2b2a2fd4574e",
    "zero_records_1": "d31dcc439340e2c121ff3fb00fc18e1f606806963698d0ac7e3db91971125425",
    "zero_records_2": "b8c25e6f96934eafd8fd0c516f934f07b7f9d079b9249d2deb2f5ea4434804ad",
    "zero_records_4": "d07e5ebb1a1f9d21ecf2f28ebb08635fec2ce09045622f69cf6e643475aca434",
    "zero_records_8": "62b87188f4ebfa81496300ec9e675bd7d55a29be82123a7a509fa363f3d0df95",
    "zero_records_16": "703cbc27a1158b5725d7b11c981de389b0a58954ab60da29e92c625a032f562f",
    "zero_records_32": "25abcd4310a25e0ae67ddbcb47b0343be580f38e062890bd8894557d7a79b929",
    "zero_records_7": "64c5fe99a67af34aead15e5b5de346c0f36005913ff639e30227bd499226ddb2",
    "zero_records_12": "6b7d0945a8e8f65973426303938f1252cc96fb0368e0e76c4b959bf8a9f84cfb",
    "zero_records_15": "bd4d5fe8fe776cd681f29551873584ef008350e31861101c7dbaf91db4080d5b",
    "zero_records_24": "ac2c188843856d6c0cc0c8318923be000664f2a59415357e250fd2fca8f80317",
    "zero_records_31": "a86cf6f2d432801d267d4c9e29dc8df628c60550572bfe7b892800e9136b3887",
}
EXPECTED_CHECKPOINTS = (
    (0, 0xD6C025DF),
    (60, 0xF1DBFAF2),
    (120, 0x2F59755F),
    (180, 0x692B3E2B),
    (240, 0x2C112818),
    (300, 0xCE7D29BB),
    (360, 0x75073316),
    (420, 0x46E0733E),
    (480, 0x91CA7A02),
    (540, 0xDF679EBC),
    (600, 0xB7716439),
    (660, 0x56E943E0),
    (720, 0xF92B572A),
)
WARNING_RE = re.compile(r"\bwarning\b", re.IGNORECASE)


def _independent_byte_entry(index: int) -> int:
    value = index
    for _bit in range(8):
        value = (
            (value >> 1) ^ POLYNOMIAL_REFLECTED
            if value & 1
            else value >> 1
        )
    return value & 0xFFFFFFFF


def _u32be(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset : offset + 4], "big")


def _table_crc(payload: bytes, table: bytes) -> int:
    return _table_update_raw(0xFFFFFFFF, payload, table) ^ 0xFFFFFFFF


def _table_update_raw(raw_crc: int, payload: bytes, table: bytes) -> int:
    raw = raw_crc
    for value in payload:
        index = (raw ^ value) & 0xFF
        raw = ((raw >> 8) ^ _u32be(table, index * 4)) & 0xFFFFFFFF
    return raw


def _t16_update_raw(
    raw_crc: int,
    payload: bytes,
    *,
    t16_table: bytes,
    byte_table: bytes,
    address_parity: int = 0,
    lookup_offsets: list[int] | None = None,
) -> int:
    """Host mirror of the aligned-word assembly recurrence."""
    raw = raw_crc & 0xFFFFFFFF
    cursor = 0
    if payload and address_parity & 1:
        raw = _table_update_raw(raw, payload[:1], byte_table)
        cursor = 1
    while cursor + 1 < len(payload):
        word = int.from_bytes(payload[cursor : cursor + 2], "little")
        index = (raw ^ word) & 0xFFFF
        offset = index * 4
        if lookup_offsets is not None:
            lookup_offsets.append(offset)
        raw = ((raw >> 16) ^ _u32be(t16_table, offset)) & 0xFFFFFFFF
        cursor += 2
    if cursor < len(payload):
        raw = _table_update_raw(raw, payload[cursor:], byte_table)
    return raw


def _transition(
    raw_crc: int,
    binary: bytes,
    table_offset: int,
) -> int:
    output = 0
    for plane, shift in enumerate((0, 8, 16, 24)):
        index = (raw_crc >> shift) & 0xFF
        output ^= _u32be(
            binary,
            table_offset + index * 16 + plane * 4,
        )
    return output & 0xFFFFFFFF


def _advance_zero_records(
    raw_crc: int,
    records: int,
    *,
    binary: bytes,
    layout: dict[str, dict[str, int | str]],
) -> int:
    """Host mirror of the exact-run assembly dispatch and bounded fallback."""
    if not 1 <= records <= 32:
        raise AssertionError(f"zero-record run is out of range: {records}")
    if records in ZERO_RECORD_DIRECT_RUNS:
        return _transition(
            raw_crc,
            binary,
            int(layout[f"zero_records_{records}"]["offset"]),
        )
    output = raw_crc
    for power in ZERO_RECORD_POWERS:
        if records & power:
            output = _transition(
                output,
                binary,
                int(layout[f"zero_records_{power}"]["offset"]),
            )
    return output


def _projection_crc(
    projection: bytes,
    *,
    capacities: tuple[int, ...],
    binary: bytes,
    layout: dict[str, dict[str, int | str]],
) -> int:
    t16_item = layout["t16"]
    byte_item = layout["byte"]
    t16_table = binary[
        int(t16_item["offset"]) :
        int(t16_item["offset"]) + int(t16_item["bytes"])
    ]
    byte_table = binary[
        int(byte_item["offset"]) :
        int(byte_item["offset"]) + int(byte_item["bytes"])
    ]
    raw = 0xFFFFFFFF
    cursor = 40 + 7 * 30
    raw = _t16_update_raw(
        raw,
        projection[:cursor],
        t16_table=t16_table,
        byte_table=byte_table,
    )

    for capacity in capacities:
        raw = _t16_update_raw(
            raw,
            projection[cursor : cursor + 4],
            t16_table=t16_table,
            byte_table=byte_table,
        )
        cursor += 4
        zero_records = 0
        for _slot in range(capacity):
            record = projection[cursor : cursor + OBJECT_RECORD_BYTES]
            if record == bytes(OBJECT_RECORD_BYTES):
                zero_records += 1
            else:
                if zero_records:
                    raw = _advance_zero_records(
                        raw,
                        zero_records,
                        binary=binary,
                        layout=layout,
                    )
                zero_records = 0
                raw = _t16_update_raw(
                    raw,
                    record,
                    t16_table=t16_table,
                    byte_table=byte_table,
                )
            cursor += OBJECT_RECORD_BYTES
        if zero_records:
            raw = _advance_zero_records(
                raw,
                zero_records,
                binary=binary,
                layout=layout,
            )

    if cursor != len(projection):
        raise AssertionError(
            f"projection scanner stopped at {cursor}, length is {len(projection)}"
        )
    return raw ^ 0xFFFFFFFF


def _publish_pool_fixture(
    prior: bytes,
    source: tuple[bytes, ...],
    *,
    pool_type: int,
    live_count: int,
    high_water: int,
    raw_crc: int,
    whole_empty_elision: bool,
    binary: bytes,
    layout: dict[str, dict[str, int | str]],
) -> tuple[bytes, int, int, int, bool]:
    """Mirror pool publication only for its explicit legal fixture domain.

    Returns projection, raw CRC, classified slots, record-store bytes, fast
    path selected. This is a host algorithm test, not 68000 execution evidence.
    The prior image must be reset-zeroed or a canonical pool projection; an
    arbitrary buffer with a zero header is deliberately not accepted.
    """
    capacity = len(source)
    if not 1 <= pool_type <= 7 or not 1 <= capacity <= 32:
        raise ValueError("invalid bounded pool fixture")
    if len(prior) != 4 + capacity * OBJECT_RECORD_BYTES:
        raise ValueError("prior projection length mismatch")
    if any(len(record) != OBJECT_RECORD_BYTES for record in source):
        raise ValueError("source record length mismatch")
    if live_count != sum(bool(record[0]) for record in source):
        raise ValueError("source live count is not exact")
    if not live_count <= high_water <= capacity:
        raise ValueError("source high-water count is not legal")
    if any(not record[0] and any(record) for record in source):
        raise ValueError("source inactive record is not cleared")
    prior_records = tuple(
        prior[4 + slot * OBJECT_RECORD_BYTES:4 + (slot + 1) * OBJECT_RECORD_BYTES]
        for slot in range(capacity)
    )
    if any(prior):  # All-zero storage is the initialize/reset exception.
        if prior[:2] != bytes((pool_type, capacity)):
            raise ValueError("prior projection header is not canonical")
        if prior[2] != sum(bool(record[0]) for record in prior_records):
            raise ValueError("prior projection live count is not exact")
        if not prior[2] <= prior[3] <= capacity:
            raise ValueError("prior projection high-water count is not legal")
        if any(not record[0] and any(record) for record in prior_records):
            raise ValueError("prior projection inactive record is not zero")

    t16_item, byte_item = layout["t16"], layout["byte"]
    t16_table = binary[
        int(t16_item["offset"]):int(t16_item["offset"]) + int(t16_item["bytes"])
    ]
    byte_table = binary[
        int(byte_item["offset"]):int(byte_item["offset"]) + int(byte_item["bytes"])
    ]

    def stream(seed: int, payload: bytes) -> int:
        return _t16_update_raw(
            seed, payload, t16_table=t16_table, byte_table=byte_table
        )

    header = bytes((pool_type, capacity, live_count, high_water))
    raw = stream(raw_crc, header)  # Header/high-water always participates.
    if whole_empty_elision and live_count == 0 and prior[2] == 0:
        raw = _advance_zero_records(
            raw, capacity, binary=binary, layout=layout
        )
        return header + prior[4:], raw, 0, 0, True

    output = bytearray(header)
    zero_run = 0
    stored_bytes = 0
    for record, previous in zip(source, prior_records):
        if record[0]:
            if zero_run:
                raw = _advance_zero_records(
                    raw, zero_run, binary=binary, layout=layout
                )
                zero_run = 0
            output.extend(record)
            raw = stream(raw, record)
            stored_bytes += OBJECT_RECORD_BYTES
        else:
            if previous[0]:
                output.extend(bytes(OBJECT_RECORD_BYTES))
                stored_bytes += OBJECT_RECORD_BYTES
            else:
                output.extend(previous)
            zero_run += 1
    if zero_run:
        raw = _advance_zero_records(
            raw, zero_run, binary=binary, layout=layout
        )
    return bytes(output), raw, capacity, stored_bytes, False


class Crc32GeneratedTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = check_generated(GENERATED)
        cls.binary = cls.bundle.files[OUTPUT_BINARY]
        cls.report = json.loads(
            cls.bundle.files[OUTPUT_REPORT].decode("ascii")
        )
        cls.layout = {
            item["name"]: item for item in cls.report["tables"]
        }

    def test_exact_binary_and_component_hashes_are_bound(self) -> None:
        self.assertEqual(self.bundle.binary_bytes, 0x4B400)
        self.assertEqual(self.bundle.binary_sha256, EXPECTED_BINARY_SHA256)
        self.assertEqual(
            hashlib.sha256(self.binary).hexdigest(),
            EXPECTED_BINARY_SHA256,
        )
        self.assertEqual(
            dict(self.bundle.table_sha256),
            EXPECTED_TABLE_SHA256,
        )
        self.assertEqual(self.report["polynomial_reflected"], "EDB88320")
        self.assertEqual(self.report["t16_entries"], 65_536)
        self.assertEqual(self.report["t16_index_byte_order"], "little")
        self.assertEqual(
            self.report["zero_record_direct_runs"],
            list(ZERO_RECORD_DIRECT_RUNS),
        )
        self.assertEqual(
            self.report["zero_record_table_counts"],
            list(ZERO_RECORD_TABLE_COUNTS),
        )
        self.assertEqual(
            self.report["known_vectors"],
            {"empty": "00000000", "123456789": "CBF43926"},
        )

    def test_conservative_neutral_cycle_bound_has_release_headroom(self) -> None:
        estimate = estimate_neutral_projection_cycles()
        self.assertEqual(
            self.report["neutral_b01_cycle_estimate"],
            estimate,
        )
        self.assertEqual(estimate["serialization_cycles"], 15302)
        self.assertEqual(estimate["crc_cycles"], 25170)
        self.assertEqual(estimate["total_cycles"], 44568)
        self.assertEqual(estimate["second_slot_scan_records"], 0)
        self.assertEqual(
            estimate["scope"],
            "EngineBuildProjection only; excludes gameplay, renderer, "
            "interrupt, and reset costs",
        )
        self.assertIn("component-only", estimate["method"])
        self.assertEqual(estimate["neutral_zero_transition_applications"], 7)
        self.assertEqual(estimate["neutral_direct_composite_runs"], 6)
        self.assertEqual(estimate["neutral_power_fallback_runs"], 1)
        self.assertEqual(
            estimate["pre_direct_run_reported_bound_cycles"],
            48632,
        )
        self.assertEqual(estimate["direct_run_bound_reduction_cycles"], 4064)
        self.assertEqual(estimate["pre_t16_fused_bound_cycles"], 69532)
        self.assertEqual(
            estimate["conservative_bound_reduction_cycles"],
            24964,
        )
        self.assertEqual(
            estimate["ntsc_65_percent_limit_cycles"],
            83203,
        )
        self.assertGreater(
            estimate["headroom_to_65_percent_limit_cycles"],
            38000,
        )
        self.assertGreater(
            estimate["headroom_to_full_frame_cycles"],
            83000,
        )

    def test_every_t16_entry_matches_two_independent_byte_updates(self) -> None:
        item = self.layout["t16"]
        self.assertEqual(item["entries"], T16_TABLE_ENTRIES)
        self.assertEqual(item["index_byte_order"], "little")
        self.assertEqual(item["bytes"], T16_TABLE_BYTES)
        offset = int(item["offset"])
        for index in range(T16_TABLE_ENTRIES):
            raw = _independent_byte_entry(index & 0xFF)
            expected = (
                (raw >> 8)
                ^ _independent_byte_entry(
                    (raw ^ (index >> 8)) & 0xFF
                )
            ) & 0xFFFFFFFF
            actual = _u32be(self.binary, offset + index * 4)
            if actual != expected:
                self.fail(
                    f"T16 recurrence mismatch at {index:04X}: "
                    f"{actual:08X} != {expected:08X}"
                )

    def test_byte_table_matches_independent_polynomial_division(self) -> None:
        offset = int(self.layout["byte"]["offset"])
        for index in range(256):
            with self.subTest(index=index):
                self.assertEqual(
                    _u32be(self.binary, offset + index * 4),
                    _independent_byte_entry(index),
                )

    def test_t16_known_vectors_and_random_seeds_cover_length_and_alignment(self) -> None:
        t16_item = self.layout["t16"]
        byte_item = self.layout["byte"]
        t16_table = self.binary[
            int(t16_item["offset"]) :
            int(t16_item["offset"]) + int(t16_item["bytes"])
        ]
        byte_table = self.binary[
            int(byte_item["offset"]) :
            int(byte_item["offset"]) + int(byte_item["bytes"])
        ]
        for payload, expected in (
            (b"", 0x00000000),
            (b"123456789", 0xCBF43926),
        ):
            for parity in (0, 1):
                raw = _t16_update_raw(
                    0xFFFFFFFF,
                    payload,
                    t16_table=t16_table,
                    byte_table=byte_table,
                    address_parity=parity,
                )
                self.assertEqual(raw ^ 0xFFFFFFFF, expected)

        randomizer = random.Random(0x52393443)
        lengths = (0, 1, 2, 3, 4, 7, 8, 9, 254, 255, 256, 3638, 0xFFFF)
        for length in lengths:
            payload = randomizer.randbytes(length)
            for parity in (0, 1):
                for _case in range(3):
                    seed = randomizer.getrandbits(32)
                    with self.subTest(
                        length=length,
                        parity=parity,
                        seed=f"{seed:08X}",
                    ):
                        self.assertEqual(
                            _t16_update_raw(
                                seed,
                                payload,
                                t16_table=t16_table,
                                byte_table=byte_table,
                                address_parity=parity,
                            ),
                            crc32_update_raw(seed, payload),
                        )

    def test_t16_scaled_indices_cross_signed_word_and_64k_boundaries(self) -> None:
        t16_item, byte_item = self.layout["t16"], self.layout["byte"]
        t16_table = self.binary[
            int(t16_item["offset"]):int(t16_item["offset"]) + int(t16_item["bytes"])
        ]
        byte_table = self.binary[
            int(byte_item["offset"]):int(byte_item["offset"]) + int(byte_item["bytes"])
        ]
        indices = (0, 0x1FFF, 0x2000, 0x3FFF, 0x4000, 0x7FFF, 0x8000, 0xFFFF)
        offsets = [0, 0x7FFC, 0x8000, 0xFFFC, 0x10000, 0x1FFFC, 0x20000, 0x3FFFC]
        for seed in (0, 0xFFFFFFFF, 0x80000000, 0x1234ABCD):
            for parity in (0, 1):
                # Force each lookup index using an independently updated raw
                # CRC; this includes both signs and all four 64 KiB quarters.
                payload = bytearray(b"\xA5" if parity else b"")
                raw = crc32_update_raw(seed, bytes(payload))
                for index in indices:
                    pair = (index ^ (raw & 0xFFFF)).to_bytes(2, "little")
                    payload.extend(pair)
                    raw = crc32_update_raw(raw, pair)
                payload.append(0x5A)  # Also retain the odd trailing-byte path.
                observed: list[int] = []
                with self.subTest(seed=f"{seed:08X}", parity=parity):
                    self.assertEqual(
                        _t16_update_raw(
                            seed,
                            bytes(payload),
                            t16_table=t16_table,
                            byte_table=byte_table,
                            address_parity=parity,
                            lookup_offsets=observed,
                        ),
                        crc32_update_raw(seed, bytes(payload)),
                    )
                    # Narrowing the scaled index to d2.w, or sign-extending
                    # it, cannot satisfy these exact full-width addresses.
                    self.assertEqual(observed, offsets)

    def test_each_zero_record_transition_matches_byte_updates(self) -> None:
        randomizer = random.Random(0xEDB88320)
        for records in ZERO_RECORD_TABLE_COUNTS:
            item = self.layout[f"zero_records_{records}"]
            for _case in range(16):
                raw = randomizer.getrandbits(32)
                with self.subTest(records=records, raw=f"{raw:08X}"):
                    self.assertEqual(
                        _transition(raw, self.binary, item["offset"]),
                        crc32_update_raw(
                            raw,
                            bytes(records * OBJECT_RECORD_BYTES),
                        ),
                    )

    def test_every_direct_composite_table_entry_has_independent_exact_value(self) -> None:
        byte_item = self.layout["byte"]
        byte_table = self.binary[
            int(byte_item["offset"]) :
            int(byte_item["offset"]) + int(byte_item["bytes"])
        ]
        for records in ZERO_RECORD_DIRECT_RUNS:
            item = self.layout[f"zero_records_{records}"]
            offset = int(item["offset"])
            zeros = bytes(records * OBJECT_RECORD_BYTES)
            for index in range(256):
                for plane, shift in enumerate((0, 8, 16, 24)):
                    raw = index << shift
                    actual = _u32be(
                        self.binary,
                        offset + index * 16 + plane * 4,
                    )
                    expected = _table_update_raw(raw, zeros, byte_table)
                    if actual != expected:
                        self.fail(
                            f"direct table {records} entry {index:02X}/"
                            f"{shift} mismatch: {actual:08X} != "
                            f"{expected:08X}"
                        )

    def test_dispatch_covers_every_run_one_to_32_and_random_seeds(self) -> None:
        randomizer = random.Random(0x94)
        for records in range(1, 33):
            for case in range(32):
                raw = randomizer.getrandbits(32)
                transitioned = _advance_zero_records(
                    raw,
                    records,
                    binary=self.binary,
                    layout=self.layout,
                )
                with self.subTest(records=records, case=case):
                    self.assertEqual(
                        transitioned,
                        crc32_update_raw(
                            raw,
                            bytes(records * OBJECT_RECORD_BYTES),
                        ),
                    )

    def test_power_composition_remains_exact_for_every_run_one_to_32(self) -> None:
        randomizer = random.Random(0xB17C0DE)
        for records in range(1, 33):
            for case in range(8):
                raw = randomizer.getrandbits(32)
                transitioned = raw
                for power in ZERO_RECORD_POWERS:
                    if records & power:
                        transitioned = _transition(
                            transitioned,
                            self.binary,
                            self.layout[f"zero_records_{power}"]["offset"],
                        )
                with self.subTest(records=records, case=case):
                    self.assertEqual(
                        transitioned,
                        crc32_update_raw(
                            raw,
                            bytes(records * OBJECT_RECORD_BYTES),
                        ),
                    )

    def test_projection_fast_path_preserves_every_reference_checkpoint(self) -> None:
        contract = load_engine_contract(ROOT, verify_source_hashes=False)
        constants = dict(contract.constants)
        capacities = tuple(
            contract.pool_capacities[pool_type]
            for pool_type in range(1, 8)
        )
        model = EngineModel(contract)
        self.assertEqual(model.initialize(), 0)
        self.assertEqual(model.load_encounter(0), 0)
        observed = [
            (
                0,
                _projection_crc(
                    model.projection,
                    capacities=capacities,
                    binary=self.binary,
                    layout=self.layout,
                ),
            )
        ]
        for tick in range(constants["ENGINE_ATTEMPT_TICKS"]):
            model.step(canonical_script_input(tick, constants))
            if model.tick % constants["ENGINE_CHECKPOINT_INTERVAL"] == 0:
                accelerated = _projection_crc(
                    model.projection,
                    capacities=capacities,
                    binary=self.binary,
                    layout=self.layout,
                )
                self.assertEqual(
                    accelerated,
                    binascii.crc32(model.projection) & 0xFFFFFFFF,
                )
                observed.append((model.tick, accelerated))
        self.assertEqual(tuple(observed), EXPECTED_CHECKPOINTS)
        self.assertEqual(tuple(model.checkpoints), EXPECTED_CHECKPOINTS)

    def test_randomized_sparse_holey_and_all_active_projections_match(self) -> None:
        contract = load_engine_contract(ROOT, verify_source_hashes=False)
        capacities = tuple(
            contract.pool_capacities[pool_type]
            for pool_type in range(1, 8)
        )
        model = EngineModel(contract)
        self.assertEqual(model.initialize(), 0)
        self.assertEqual(model.load_encounter(0), 0)
        template = model.projection
        randomizer = random.Random(0x54313650)

        for mode in ("sparse", "holey", "all_active"):
            for case in range(24):
                projection = bytearray(template)
                cursor = 40 + 7 * 30
                for capacity in capacities:
                    header = cursor
                    cursor += 4
                    active_count = 0
                    for slot in range(capacity):
                        if mode == "all_active":
                            active = True
                        elif mode == "holey":
                            active = (slot + case) % 3 != 1
                        else:
                            active = randomizer.randrange(17) == 0
                        if active:
                            record = bytearray(
                                randomizer.randbytes(OBJECT_RECORD_BYTES)
                            )
                            record[0] = 1 + randomizer.randrange(255)
                            active_count += 1
                        else:
                            record = bytearray(OBJECT_RECORD_BYTES)
                        projection[
                            cursor : cursor + OBJECT_RECORD_BYTES
                        ] = record
                        cursor += OBJECT_RECORD_BYTES
                    projection[header + 2] = active_count
                    projection[header + 3] = max(
                        projection[header + 3],
                        active_count,
                    )
                self.assertEqual(cursor, len(projection))
                with self.subTest(mode=mode, case=case):
                    self.assertEqual(
                        _projection_crc(
                            bytes(projection),
                            capacities=capacities,
                            binary=self.binary,
                            layout=self.layout,
                        ),
                        binascii.crc32(projection) & 0xFFFFFFFF,
                    )

    def test_inactive_projection_elision_preserves_active_to_inactive_sequence(self) -> None:
        active = bytearray(range(OBJECT_RECORD_BYTES))
        active[0] = 1
        inactive = bytes(OBJECT_RECORD_BYTES)

        def publish(source: bytes, prior: bytes) -> tuple[bytes, int]:
            if source[0]:
                return source, OBJECT_RECORD_BYTES
            if prior[0] == 0:
                return prior, 0
            return inactive, OBJECT_RECORD_BYTES

        first, first_stores = publish(bytes(active), inactive)
        second, second_stores = publish(inactive, first)
        third, third_stores = publish(inactive, second)
        self.assertEqual(first, bytes(active))
        self.assertEqual(first_stores, OBJECT_RECORD_BYTES)
        self.assertEqual(second, inactive)
        self.assertEqual(second_stores, OBJECT_RECORD_BYTES)
        self.assertEqual(third, inactive)
        self.assertEqual(third_stores, 0)

    def _assert_pool_publication(
        self,
        prior: bytes,
        source: tuple[bytes, ...],
        *,
        pool_type: int,
        high_water: int,
        seed: int = 0xFFFFFFFF,
    ) -> tuple[bytes, int, int, int, bool]:
        capacity = len(source)
        live_count = sum(bool(record[0]) for record in source)
        before = (prior, source)
        arguments = {
            "pool_type": pool_type,
            "live_count": live_count,
            "high_water": high_water,
            "raw_crc": seed,
            "binary": self.binary,
            "layout": self.layout,
        }
        old = _publish_pool_fixture(
            prior, source, whole_empty_elision=False, **arguments
        )
        new = _publish_pool_fixture(
            prior, source, whole_empty_elision=True, **arguments
        )
        expected = bytes((pool_type, capacity, live_count, high_water)) + b"".join(source)
        self.assertEqual(new[:2], old[:2])
        self.assertEqual(new[0], expected)
        self.assertEqual(new[1], crc32_update_raw(seed, expected))
        self.assertEqual(len(new[0]), 4 + capacity * OBJECT_RECORD_BYTES)
        self.assertEqual(old[2], capacity)
        self.assertEqual(new[4], live_count == 0 and prior[2] == 0)
        self.assertEqual(new[2], 0 if new[4] else capacity)
        stores = sum(
            bool(record[0]) or bool(prior[4 + slot * OBJECT_RECORD_BYTES])
            for slot, record in enumerate(source)
        ) * OBJECT_RECORD_BYTES
        self.assertEqual(old[3], stores)
        self.assertEqual(new[3], 0 if new[4] else stores)
        self.assertEqual((prior, source), before)
        return new

    def test_whole_empty_publication_covers_initialize_and_reset_for_every_pool(self) -> None:
        contract = load_engine_contract(ROOT, verify_source_hashes=False)
        self.assertEqual(
            tuple(contract.pool_capacities[pool_type] for pool_type in range(1, 8)),
            (16, 32, 32, 8, 16, 12, 24),
        )
        zero = bytes(OBJECT_RECORD_BYTES)
        for pool_type in range(1, 8):
            capacity = contract.pool_capacities[pool_type]
            reset = bytes(4 + capacity * OBJECT_RECORD_BYTES)
            empty = (zero,) * capacity
            with self.subTest(pool_type=pool_type):
                initialized = self._assert_pool_publication(
                    reset, empty, pool_type=pool_type, high_water=0
                )
                self.assertTrue(initialized[4])
                # Reconstruction resets the complete projection, then may
                # reconstruct an active object at any physical slot.
                active = b"\x01\xA7" + bytes(range(2, OBJECT_RECORD_BYTES))
                loaded = self._assert_pool_publication(
                    reset,
                    empty[:-1] + (active,),
                    pool_type=pool_type,
                    high_water=1,
                )
                self.assertFalse(loaded[4])
                self.assertEqual(loaded[2], capacity)
                reinitialized = self._assert_pool_publication(
                    reset, empty, pool_type=pool_type, high_water=0
                )
                self.assertEqual(initialized, reinitialized)

    def test_whole_empty_publication_preserves_holes_reuse_and_clear_once(self) -> None:
        contract = load_engine_contract(ROOT, verify_source_hashes=False)
        zero = bytes(OBJECT_RECORD_BYTES)
        first = b"\x01\x24" + bytes(range(2, OBJECT_RECORD_BYTES))
        last = b"\x01\xE3" + bytes(reversed(range(2, OBJECT_RECORD_BYTES)))
        reused = b"\x01\x94" + b"\xD7" * (OBJECT_RECORD_BYTES - 2)
        for pool_type in range(1, 8):
            capacity = contract.pool_capacities[pool_type]
            empty = (zero,) * capacity
            first_only = (first,) + empty[1:]
            both = (first,) + empty[1:-1] + (last,)
            last_only = empty[:-1] + (last,)
            reused_hole = (reused,) + empty[1:-1] + (last,)
            prior = bytes(4 + capacity * OBJECT_RECORD_BYTES)
            phases = (
                ("initial_empty", empty, 0, True, 0),
                ("empty_to_active", first_only, 1, False, 24),
                ("second_active", both, 2, False, 48),
                ("hole_before_last", last_only, 2, False, 48),
                ("reuse_hole", reused_hole, 2, False, 48),
                ("active_to_empty", empty, 2, False, 48),
                ("steady_empty", empty, 2, True, 0),
                # Three transient allocations were all freed before the next
                # publication: live/prior counts are zero, high-water is not.
                ("allocate_free_between_publications", empty, 3, True, 0),
            )
            for name, source, high_water, fast, stores in phases:
                with self.subTest(pool_type=pool_type, phase=name):
                    result = self._assert_pool_publication(
                        prior, source, pool_type=pool_type, high_water=high_water
                    )
                    self.assertEqual(result[4], fast)
                    self.assertEqual(result[3], stores)
                    prior = result[0]

    def test_whole_empty_publication_still_hashes_high_water_header(self) -> None:
        contract = load_engine_contract(ROOT, verify_source_hashes=False)
        zero = bytes(OBJECT_RECORD_BYTES)
        for pool_type in range(1, 8):
            capacity = contract.pool_capacities[pool_type]
            source = (zero,) * capacity
            prior = bytes((pool_type, capacity, 0, 0)) + b"".join(source)
            observed = set()
            for high_water in (0, 1, capacity):
                with self.subTest(pool_type=pool_type, high_water=high_water):
                    result = self._assert_pool_publication(
                        prior,
                        source,
                        pool_type=pool_type,
                        high_water=high_water,
                        seed=0x13579BDF,
                    )
                    self.assertTrue(result[4])
                    self.assertEqual(result[0][4:], prior[4:])
                    self.assertEqual(result[0][3], high_water)
                    observed.add(result[1])
            self.assertEqual(len(observed), 3)

    def test_whole_empty_publication_matches_random_legal_prior_images(self) -> None:
        contract = load_engine_contract(ROOT, verify_source_hashes=False)
        randomizer = random.Random(0x504F4F4C)
        zero = bytes(OBJECT_RECORD_BYTES)
        for pool_type in range(1, 8):
            capacity = contract.pool_capacities[pool_type]
            prior = bytes(4 + capacity * OBJECT_RECORD_BYTES)
            high_water = 0
            for case in range(32):
                # Alternate dense, holey, sparse, and empty publications so
                # arbitrary active payloads also precede whole-pool clearing.
                active = tuple(
                    case % 4 == 0
                    or (case % 4 == 1 and slot % 3 == 2)
                    or (case % 4 == 2 and randomizer.randrange(7) == 0)
                    for slot in range(capacity)
                )
                source = tuple(
                    bytes((1,)) + randomizer.randbytes(OBJECT_RECORD_BYTES - 1)
                    if occupied else zero
                    for occupied in active
                )
                high_water = max(high_water, sum(active))
                with self.subTest(pool_type=pool_type, case=case):
                    result = self._assert_pool_publication(
                        prior,
                        source,
                        pool_type=pool_type,
                        high_water=high_water,
                        seed=randomizer.getrandbits(32),
                    )
                    prior = result[0]

    def test_whole_empty_fixture_rejects_malformed_prior_and_inexact_counts(self) -> None:
        capacity = 8
        zero = bytes(OBJECT_RECORD_BYTES)
        active = b"\x01\x94" + b"\xA5" * (OBJECT_RECORD_BYTES - 2)
        empty = (zero,) * capacity
        header = bytes((4, capacity, 0, 1))
        canonical = header + b"".join(empty)
        invalid = (
            ("zero_count_with_active_payload", header + active + b"".join(empty[1:]), empty, 0, 1),
            ("zero_header_with_active_payload", bytes(4) + active + b"".join(empty[1:]), empty, 0, 1),
            ("inactive_stale_payload", header + b"\x00\x94" + active[2:] + b"".join(empty[1:]), empty, 0, 1),
            ("nonzero_prior_count_without_active_slot", bytes((4, capacity, 1, 1)) + b"".join(empty), empty, 0, 1),
            ("source_zero_count_with_active_slot", canonical, (active,) + empty[1:], 0, 1),
            ("source_nonzero_count_without_active_slot", canonical, empty, 1, 1),
            ("source_live_above_high_water", canonical, (active,) + empty[1:], 1, 0),
        )
        for name, prior, source, count, high_water in invalid:
            for fast in (False, True):
                with self.subTest(case=name, whole_empty_elision=fast):
                    with self.assertRaises(ValueError):
                        _publish_pool_fixture(
                            prior,
                            source,
                            pool_type=4,
                            live_count=count,
                            high_water=high_water,
                            raw_crc=0xFFFFFFFF,
                            whole_empty_elision=fast,
                            binary=self.binary,
                            layout=self.layout,
                        )

    def test_generator_is_deterministic_and_stale_checks_fail_closed(self) -> None:
        self.assertEqual(build_tables(), build_tables())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "crc32"
            generated = compile_tables(output)
            self.assertEqual(set(generated.files), OUTPUT_NAMES)
            self.assertEqual(check_generated(output), generated)
            (output / OUTPUT_BINARY).write_bytes(
                generated.files[OUTPUT_BINARY][:-1] + b"\x00"
            )
            with self.assertRaisesRegex(
                Crc32TableError,
                "stale or corrupted",
            ):
                check_generated(output)

    def test_generated_include_has_one_binary_and_exact_layout_symbols(self) -> None:
        include = self.bundle.files[OUTPUT_INCLUDE].decode("ascii")
        self.assertEqual(include.count('incbin "assets/generated/crc32/'), 1)
        self.assertEqual(include.count("Crc32Tables:"), 1)
        self.assertEqual(include.count("Crc32TablesEnd:"), 1)
        for records in ZERO_RECORD_TABLE_COUNTS:
            self.assertIn(f"Crc32ZeroRecords{records}Table", include)
        self.assertIn("Crc32T16Table", include)
        self.assertIn("CRC32_TABLE_BINARY_BYTES        equ     $4B400", include)
        self.assertIn("CRC32_T16_TABLE_BYTES           equ     $40000", include)
        self.assertEqual(
            sum(int(item["bytes"]) for item in self.report["tables"]),
            T16_TABLE_BYTES
            + BYTE_TABLE_BYTES
            + len(ZERO_RECORD_TABLE_COUNTS) * TRANSITION_TABLE_BYTES,
        )


class Crc32StandaloneAssemblyTests(unittest.TestCase):
    def test_whole_empty_pool_source_binds_prior_count_header_crc_and_common_flush(self) -> None:
        source = (ROOT / "src/game/projection.asm").read_text("utf-8")
        routine = source.split("EngineProjectPool:\n", 1)[1]
        routine = routine.split("\n        assert ", 1)[0]
        lines = [
            re.sub(r"\s+", " ", line.split(";", 1)[0].strip())
            for line in routine.splitlines()
            if line.split(";", 1)[0].strip()
        ]
        scan = lines.index(".scan_slots:")
        # Exact prefix binds the old count before any header overwrite, the
        # current count before the high-water reload, and an unconditional
        # four-byte header CRC before the empty/empty decision.
        self.assertEqual(
            lines[:scan],
            [
                "movem.l d3-d6/a2-a3,-(a7)",
                "movea.l a0,a2",
                "move.w d0,d3",
                "move.l d2,d6",
                "move.b 2(a1),d5",
                "move.b d1,(a1)+",
                "move.b d0,(a1)+",
                "move.w d1,d2",
                "subq.w #1,d2",
                "lea (ENGINE_POOL_COUNTS).l,a0",
                "adda.w d2,a0",
                "move.b (a0),d4",
                "or.b d4,d5",
                "move.b d4,(a1)+",
                "lea (ENGINE_POOL_HIGH_WATER).l,a0",
                "adda.w d2,a0",
                "move.b (a0),d4",
                "move.b d4,(a1)+",
                "movea.l a1,a3",
                "lea -PROJECTION_POOL_HEADER_BYTES(a1),a0",
                "moveq #PROJECTION_POOL_HEADER_BYTES,d0",
                "move.l d6,d1",
                "bsr.w Crc32UpdateRaw",
                "move.l d1,d6",
                "movea.l a3,a1",
                "tst.b d5",
                "bne.s .scan_slots",
                # Only registers change after the header is published. The
                # word multiply bounds the advance without trusting d0's
                # incoming high word, and d3 still holds the full capacity.
                "move.w d3,d5",
                "move.w d3,d0",
                "mulu.w #OBJECT_PROJECTION_BYTES,d0",
                "adda.w d0,a1",
                "bra.w .flush_trailing_run",
            ],
        )
        self.assertEqual(
            lines[scan:scan + 4],
            [".scan_slots:", "subq.w #1,d3", "moveq #0,d5", ".slot:"],
        )
        flush = lines.index(".flush_trailing_run:")
        self.assertEqual(lines[flush - 1], "dbf d3,.slot")
        self.assertEqual(
            lines[flush:],
            [
                ".flush_trailing_run:",
                "tst.w d5",
                "beq.s .return_crc",
                "movea.l a1,a3",
                "move.w d5,d0",
                "move.l d6,d1",
                "bsr.w Crc32AdvanceZeroRecords",
                "move.l d1,d6",
                "movea.l a3,a1",
                ".return_crc:",
                "move.l d6,d2",
                "movem.l (a7)+,d3-d6/a2-a3",
                "rts",
            ],
        )
        constants = dict(load_engine_contract(ROOT, verify_source_hashes=False).constants)
        self.assertEqual(constants["OBJECT_PROJECTION_BYTES"], 24)
        self.assertEqual(constants["PROJECTION_POOL_HEADER_BYTES"], 4)
        for capacity_name in (
            "ENEMY_CAPACITY", "PROJECTILE_CAPACITY", "DEVICE_CAPACITY",
            "CARRYABLE_CAPACITY", "HAZARD_CAPACITY", "BOSS_COMPONENT_CAPACITY",
            "EFFECT_CAPACITY",
        ):
            capacity = constants[capacity_name]
            self.assertTrue(1 <= capacity <= 32)
            self.assertLess(capacity * constants["OBJECT_PROJECTION_BYTES"], 0x8000)

    def test_initialize_and_reconstruct_clear_the_complete_prior_projection(self) -> None:
        source = (ROOT / "src/game/engine_core.asm").read_text("utf-8")
        lines = [
            re.sub(r"\s+", " ", line.split(";", 1)[0].strip())
            for line in source.splitlines()
            if line.split(";", 1)[0].strip()
        ]
        initialize = lines.index("EngineInitialize:")
        self.assertEqual(
            lines[initialize + 1:initialize + 7],
            [
                "lea (ENGINE_CONTROL_BASE).l,a0",
                "moveq #0,d0",
                "move.w #((ENGINE_STAGING_END-ENGINE_CONTROL_BASE)/4)-1,d1",
                ".clear_owned_ram:",
                "move.l d0,(a0)+",
                "dbf d1,.clear_owned_ram",
            ],
        )
        reconstruct = lines.index("EngineReconstructCanonicalState:")
        clear = lines.index(".clear_ordinary:", reconstruct)
        self.assertEqual(
            lines[reconstruct + 1:clear],
            [
                "movem.l d3-d7/a2-a6,-(a7)",
                "lea (ENGINE_ACTOR_BASE).l,a0",
                *[f"moveq #0,d{index}" for index in range(7)],
                *[f"movea.l d0,a{index}" for index in range(1, 7)],
                "move.w #432,d7",
            ],
        )
        self.assertEqual(
            lines[clear + 1:clear + 7],
            [
                "movem.l d0-d6/a1-a6,(a0)",
                "adda.w #52,a0",
                "dbf d7,.clear_ordinary",
                "move.l d0,(a0)+",
                "move.l d0,(a0)+",
                "move.l d0,(a0)+",
            ],
        )
        constants = dict(load_engine_contract(ROOT, verify_source_hashes=False).constants)
        self.assertEqual(
            constants["ENGINE_ACTOR_BASE"] + 433 * 52 + 3 * 4,
            constants["ENGINE_PROJECTION_END"],
        )
        self.assertLessEqual(constants["ENGINE_CONTROL_BASE"], constants["ENGINE_ACTOR_BASE"])
        self.assertLessEqual(constants["ENGINE_ACTOR_BASE"], constants["ENGINE_PROJECTION_BASE"])
        self.assertLessEqual(constants["ENGINE_PROJECTION_END"], constants["ENGINE_STAGING_END"])

    def test_word_loop_hoists_t16_base_and_keeps_full_long_scaled_index(self) -> None:
        source = (ROOT / "src/game/crc32.asm").read_text("utf-8")
        lines = [
            re.sub(r"\s+", " ", line.split(";", 1)[0].strip())
            for line in source.splitlines()
            if line.split(";", 1)[0].strip()
        ]
        aligned = lines.index(".aligned:")
        word_loop = lines.index(".next_word:")
        tail = lines.index(".tail_ready:")
        self.assertLess(aligned, word_loop)
        self.assertLess(word_loop, tail)
        self.assertEqual(lines.count("lea (Crc32T16Table).l,a1"), 1)
        self.assertEqual(
            lines[word_loop - 3:word_loop],
            [
                "beq.s .tail_ready",
                "subq.w #1,d0",
                "lea (Crc32T16Table).l,a1",
            ],
        )
        # The zero extension and long shift/index retain all 18 offset bits.
        # No per-word LEA/ADDA may alter the fixed base between iterations.
        self.assertEqual(
            lines[word_loop + 1:tail],
            [
                "moveq #0,d2",
                "move.w (a0)+,d2",
                "ror.w #8,d2",
                "eor.w d1,d2",
                "lsl.l #2,d2",
                "move.l (a1,d2.l),d2",
                "clr.w d1",
                "swap d1",
                "eor.l d2,d1",
                "dbf d0,.next_word",
            ],
        )
        # The odd-address prefix and odd-length tail each bind the byte table
        # independently; neither may accidentally reuse the hoisted T16 base.
        for byte_path in (lines[:aligned], lines[tail:lines.index(".done:")]):
            byte_base = byte_path.index("lea (Crc32ByteTable).l,a1")
            self.assertEqual(byte_path[byte_base + 1], "move.l (a1,d2.w),d2")

    def test_zero_warning_link_complete_standalone_contains_tables_once(self) -> None:
        vasm = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(vasm.is_file(), "pinned vasm must be bootstrapped")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="crc32-", dir=ROOT / "build") as temporary:
            output = Path(temporary) / "crc32.bin"
            completed = subprocess.run(
                [
                    str(vasm),
                    "-Fbin",
                    "-m68000",
                    "-no-opt",
                    "-x",
                    "-Isrc",
                    "-o",
                    output.relative_to(ROOT).as_posix(),
                    "tests/m3/crc32_standalone.asm",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIsNone(WARNING_RE.search(completed.stdout), completed.stdout)
            payload = output.read_bytes()
            table = (GENERATED / OUTPUT_BINARY).read_bytes()
            self.assertEqual(payload.count(table), 1)
            self.assertTrue(payload.endswith(table))
            self.assertGreater(len(payload), len(table))
            self.assertLess(len(payload) - len(table), 256)

    def test_projection_serializer_standalone_assembles_without_warnings(self) -> None:
        vasm = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(vasm.is_file(), "pinned vasm must be bootstrapped")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="projection-crc32-", dir=ROOT / "build") as temporary:
            fixture = Path(temporary) / "projection.asm"
            output = Path(temporary) / "projection.bin"
            fixture.write_text(
                "        org $000000\n"
                '        include "game/engine_layout.asm"\n'
                '        include "game/crc32.asm"\n'
                '        include "game/projection.asm"\n'
                '        include "assets/generated/crc32/crc32_tables.inc.asm"\n',
                encoding="ascii",
            )
            completed = subprocess.run(
                [
                    str(vasm), "-Fbin", "-m68000", "-no-opt", "-x",
                    "-Isrc", "-I.", "-o", output.relative_to(ROOT).as_posix(),
                    fixture.relative_to(ROOT).as_posix(),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIsNone(WARNING_RE.search(completed.stdout), completed.stdout)
            payload = output.read_bytes()
            table = (GENERATED / OUTPUT_BINARY).read_bytes()
            self.assertEqual(payload.count(table), 1)
            self.assertTrue(payload.endswith(table))


class Crc32BuildIntegrationTests(unittest.TestCase):
    def test_build_stage_regenerates_and_rechecks_tables(self) -> None:
        result = compile_crc32_lookup_tables(root=ROOT)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["generated_files_current"])
        self.assertEqual(result["binary_bytes"], 0x4B400)
        self.assertEqual(result["binary_sha256"], EXPECTED_BINARY_SHA256)
        self.assertEqual(result["table_sha256"], EXPECTED_TABLE_SHA256)

    def test_production_translation_unit_emits_generated_tables_once_at_end(self) -> None:
        main = (ROOT / "src/main.asm").read_text(encoding="utf-8")
        include = 'include "../assets/generated/crc32/crc32_tables.inc.asm"'
        self.assertEqual(main.count(include), 1)
        self.assertGreater(
            main.index(include),
            main.index(
                'include "../assets/generated/content/campaign_content.inc.asm"'
            ),
        )
        all_runtime = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "src").rglob("*.asm"))
        )
        self.assertEqual(all_runtime.count(include), 1)

    def test_current_debug_rom_contains_one_exact_generated_table_blob(self) -> None:
        raw_path = ROOT / "build/recursion94-debug.raw"
        symbol_path = ROOT / "build/recursion94-debug.sym"
        self.assertTrue(raw_path.is_file(), "Debug raw image must be built")
        self.assertTrue(symbol_path.is_file(), "Debug symbols must be built")
        raw = raw_path.read_bytes()
        binary = (GENERATED / OUTPUT_BINARY).read_bytes()
        symbols: dict[str, int] = {}
        for line in symbol_path.read_text("utf-8").splitlines():
            address, name = line.split(maxsplit=1)
            symbols[name] = int(address, 16)
        start = symbols["Crc32Tables"]
        end = symbols["Crc32TablesEnd"]
        self.assertEqual(end - start, len(binary))
        self.assertEqual(raw[start:end], binary)
        self.assertEqual(raw.count(binary), 1)
        self.assertEqual(end, symbols["RomImageEnd"])
        self.assertLess(end, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
