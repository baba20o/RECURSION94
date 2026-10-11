from __future__ import annotations

import dataclasses
import hashlib
import unittest

from tools.save_reference.model import (
    COMMIT_MARKER,
    COMMIT_OFFSET,
    CRC_OFFSET,
    ENCOUNTER_COUNT,
    HEADER_BYTES,
    PAYLOAD_BYTES,
    RECORD_BYTES,
    SaveFormatError,
    SaveJournal,
    SavePayload,
    StageMetric,
    commit_write_plan,
    decode_record,
    encode_record,
)


class SavePayloadTests(unittest.TestCase):
    def test_default_payload_is_canonical_and_byte_exact(self) -> None:
        payload = SavePayload()
        encoded = payload.encode()
        self.assertEqual(len(encoded), PAYLOAD_BYTES)
        self.assertEqual(encoded[:4], bytes.fromhex("00010107"))
        self.assertEqual(encoded[4:16], bytes(12))
        self.assertEqual(
            encoded[16:24],
            bytes.fromhex("0000FFFFFF000000"),
        )
        self.assertEqual(SavePayload.decode(encoded), payload)
        self.assertEqual(len(payload.metrics), ENCOUNTER_COUNT)

    def test_clear_updates_progress_and_keeps_the_best_result(self) -> None:
        payload = SavePayload().with_clear(
            3, rank=1, attempts=5, echoes=2, time_remaining=80
        )
        self.assertEqual(payload.unlocked_sector, 2)
        self.assertEqual(payload.completion_bits, 1 << 3)
        self.assertEqual(payload.total_clears, 1)
        worse = payload.with_clear(
            3, rank=1, attempts=7, echoes=3, time_remaining=40
        )
        self.assertEqual(worse.metrics[3], payload.metrics[3])
        better = payload.with_clear(
            3, rank=2, attempts=4, echoes=2, time_remaining=100
        )
        self.assertEqual(better.metrics[3].rank, 2)

    def test_payload_rejects_bitmap_metric_and_reserved_drift(self) -> None:
        with self.assertRaises(SaveFormatError):
            dataclasses.replace(SavePayload(), completion_bits=1).encode()
        corrupt = bytearray(SavePayload().encode())
        corrupt[-1] = 1
        with self.assertRaises(SaveFormatError):
            SavePayload.decode(bytes(corrupt))
        with self.assertRaises(SaveFormatError):
            StageMetric(False, 1, 1, 0, 0).encode()

    def test_payload_and_metric_bounds_are_fail_closed(self) -> None:
        payload_cases = (
            {"unlocked_sector": 0},
            {"unlocked_sector": 6},
            {"settings": 8},
            {"completion_bits": 1 << ENCOUNTER_COUNT},
            {"narrative_bits": -1},
            {"narrative_bits": 0x100000000},
            {"total_clears": -1},
            {"total_clears": ENCOUNTER_COUNT + 1},
            {"metrics": SavePayload().metrics[:-1]},
            {"metrics": SavePayload().metrics + (StageMetric(),)},
        )
        for changes in payload_cases:
            with self.subTest(changes=changes), self.assertRaises(SaveFormatError):
                dataclasses.replace(SavePayload(), **changes).encode()

        metric_cases = (
            StageMetric(True, 0, 1, 0, 0),
            StageMetric(True, 4, 1, 0, 0),
            StageMetric(True, 1, 0, 0, 0),
            StageMetric(True, 1, 0xFFFF, 0, 0),
            StageMetric(True, 1, 1, 7, 0),
            StageMetric(True, 1, 1, 0, 721),
            StageMetric(False, 0, 0xFFFF, 0xFE, 0),
        )
        for metric in metric_cases:
            with self.subTest(metric=metric), self.assertRaises(SaveFormatError):
                metric.encode()

    def test_all_twenty_encounters_have_the_assembly_progression_bounds(self) -> None:
        for index in range(ENCOUNTER_COUNT):
            with self.subTest(index=index):
                payload = SavePayload().with_clear(
                    index,
                    rank=1,
                    attempts=1,
                    echoes=0,
                    time_remaining=0,
                )
                expected_sector = min(
                    5,
                    index // 4 + 1 + (1 if index % 4 == 3 else 0),
                )
                self.assertEqual(payload.unlocked_sector, expected_sector)
                self.assertEqual(payload.completion_bits, 1 << index)
                self.assertEqual(payload.total_clears, 1)
        for index in (-1, ENCOUNTER_COUNT):
            with self.subTest(index=index), self.assertRaises(SaveFormatError):
                SavePayload().with_clear(
                    index,
                    rank=1,
                    attempts=1,
                    echoes=0,
                    time_remaining=0,
                )

    def test_best_metric_order_is_rank_attempts_echoes_then_time(self) -> None:
        original = SavePayload().with_clear(
            0, rank=2, attempts=5, echoes=3, time_remaining=100
        )
        better_cases = (
            {"rank": 3, "attempts": 99, "echoes": 6, "time_remaining": 0},
            {"rank": 2, "attempts": 4, "echoes": 6, "time_remaining": 0},
            {"rank": 2, "attempts": 5, "echoes": 2, "time_remaining": 0},
            {"rank": 2, "attempts": 5, "echoes": 3, "time_remaining": 101},
        )
        worse_cases = (
            {"rank": 1, "attempts": 1, "echoes": 0, "time_remaining": 720},
            {"rank": 2, "attempts": 6, "echoes": 0, "time_remaining": 720},
            {"rank": 2, "attempts": 5, "echoes": 4, "time_remaining": 720},
            {"rank": 2, "attempts": 5, "echoes": 3, "time_remaining": 99},
        )
        for candidate in better_cases:
            with self.subTest(kind="better", candidate=candidate):
                updated = original.with_clear(0, **candidate)
                self.assertEqual(
                    updated.metrics[0],
                    StageMetric(True, **candidate),
                )
        for candidate in worse_cases:
            with self.subTest(kind="worse", candidate=candidate):
                updated = original.with_clear(0, **candidate)
                self.assertEqual(updated.metrics[0], original.metrics[0])


class SaveRecordTests(unittest.TestCase):
    def test_record_round_trip_covers_signature_version_crc_and_commit(self) -> None:
        payload = SavePayload().with_clear(
            0, rank=3, attempts=2, echoes=1, time_remaining=311
        )
        encoded = encode_record(payload, 0x12345678)
        self.assertEqual(len(encoded), RECORD_BYTES)
        self.assertEqual(encoded[:4], b"R94S")
        self.assertEqual(encoded[-4:], COMMIT_MARKER.to_bytes(4, "big"))
        decoded = decode_record(encoded)
        self.assertEqual(decoded.generation, 0x12345678)
        self.assertEqual(decoded.payload, payload)

    def test_canonical_generation_zero_record_is_byte_exact(self) -> None:
        encoded = encode_record(SavePayload(), 0)
        self.assertEqual(
            encoded[:HEADER_BYTES].hex(),
            "52393453000101000000000000d80001"
            "00000000000000000000000000000000",
        )
        self.assertEqual(encoded[-8:].hex(), "9d60386fa55a94c3")
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(),
            "2348d334eee51b2a7bbf8698437b2326"
            "1f21314dbd4a70bae3a214ab2866c58f",
        )

    def test_every_payload_byte_is_crc_protected(self) -> None:
        encoded = encode_record(SavePayload(), 7)
        for index in range(HEADER_BYTES, RECORD_BYTES - 8):
            with self.subTest(index=index):
                corrupt = bytearray(encoded)
                corrupt[index] ^= 0x80
                with self.assertRaises(SaveFormatError):
                    decode_record(bytes(corrupt))

    def test_header_and_commit_corruption_fail_closed(self) -> None:
        encoded = encode_record(SavePayload(), 1)
        for index in (*range(0, HEADER_BYTES), *range(RECORD_BYTES - 8, RECORD_BYTES)):
            with self.subTest(index=index):
                corrupt = bytearray(encoded)
                corrupt[index] ^= 1
                with self.assertRaises(SaveFormatError):
                    decode_record(bytes(corrupt))

    def test_commit_plan_is_exactly_invalidate_body_publish(self) -> None:
        encoded = encode_record(SavePayload(), 0x12345678)
        plan = commit_write_plan(encoded)
        self.assertEqual(CRC_OFFSET, 0xF8)
        self.assertEqual(COMMIT_OFFSET, 0xFC)
        self.assertEqual(len(plan), RECORD_BYTES + 4)
        self.assertEqual(
            plan[:4],
            tuple((COMMIT_OFFSET + index, 0) for index in range(4)),
        )
        self.assertEqual(
            plan[4:RECORD_BYTES],
            tuple(enumerate(encoded[:COMMIT_OFFSET])),
        )
        self.assertEqual(
            plan[RECORD_BYTES:],
            tuple(
                (COMMIT_OFFSET + index, value)
                for index, value in enumerate(encoded[COMMIT_OFFSET:])
            ),
        )
        malformed = bytearray(encoded)
        malformed[-1] = 0
        with self.assertRaises(SaveFormatError):
            commit_write_plan(bytes(malformed))


class SaveJournalTests(unittest.TestCase):
    def test_blank_sram_recovers_defaults_without_crashing(self) -> None:
        journal = SaveJournal()
        self.assertTrue(journal.recovered_defaults)
        self.assertIsNone(journal.active_slot)
        self.assertEqual(journal.payload, SavePayload())

    def test_commits_alternate_slots_and_survive_reload(self) -> None:
        journal = SaveJournal()
        first = SavePayload().with_clear(
            0, rank=1, attempts=3, echoes=1, time_remaining=120
        )
        self.assertTrue(journal.commit(first))
        self.assertEqual((journal.active_slot, journal.generation), (0, 1))
        second = first.with_clear(
            1, rank=2, attempts=2, echoes=1, time_remaining=240
        )
        self.assertTrue(journal.commit(second))
        self.assertEqual((journal.active_slot, journal.generation), (1, 2))
        reloaded = SaveJournal(journal.image())
        self.assertEqual(reloaded.payload, second)
        self.assertFalse(reloaded.recovered_defaults)

    def test_every_torn_write_point_preserves_last_committed_payload(self) -> None:
        baseline = SavePayload().with_clear(
            0, rank=1, attempts=4, echoes=1, time_remaining=90
        )
        candidate = baseline.with_clear(
            1, rank=2, attempts=3, echoes=2, time_remaining=180
        )
        seed = SaveJournal()
        self.assertTrue(seed.commit(baseline))
        image = seed.image()
        total_physical_writes = RECORD_BYTES + 4
        for fault_point in range(total_physical_writes):
            with self.subTest(fault_point=fault_point):
                journal = SaveJournal(image)
                self.assertFalse(
                    journal.commit(candidate, fault_after_bytes=fault_point)
                )
                self.assertEqual(journal.payload, baseline)

    def test_every_torn_write_point_invalidates_a_reused_valid_slot_first(self) -> None:
        first = SavePayload().with_clear(
            0, rank=1, attempts=4, echoes=1, time_remaining=90
        )
        baseline = first.with_clear(
            1, rank=2, attempts=3, echoes=2, time_remaining=180
        )
        candidate = baseline.with_clear(
            2, rank=3, attempts=2, echoes=0, time_remaining=300
        )
        seed = SaveJournal()
        self.assertTrue(seed.commit(first))
        self.assertTrue(seed.commit(baseline))
        self.assertEqual(seed.active_slot, 1)
        image = seed.image()
        for fault_point in range(RECORD_BYTES + 4):
            with self.subTest(fault_point=fault_point):
                journal = SaveJournal(image)
                self.assertFalse(
                    journal.commit(candidate, fault_after_bytes=fault_point)
                )
                self.assertEqual(journal.active_slot, 1)
                self.assertEqual(journal.generation, 2)
                self.assertEqual(journal.payload, baseline)

    def test_newest_corrupt_record_falls_back_to_previous_generation(self) -> None:
        first = SavePayload().with_clear(
            0, rank=1, attempts=3, echoes=1, time_remaining=100
        )
        second = first.with_clear(
            1, rank=1, attempts=4, echoes=2, time_remaining=80
        )
        journal = SaveJournal()
        journal.commit(first)
        journal.commit(second)
        journal.slots[1][HEADER_BYTES + 9] ^= 0x20
        recovered = SaveJournal(journal.image())
        self.assertEqual(recovered.payload, first)
        self.assertEqual(recovered.generation, 1)

    def test_corruption_of_every_newest_record_byte_falls_back(self) -> None:
        first = SavePayload().with_clear(
            0, rank=1, attempts=3, echoes=1, time_remaining=100
        )
        second = first.with_clear(
            1, rank=2, attempts=2, echoes=0, time_remaining=200
        )
        seed = SaveJournal()
        self.assertTrue(seed.commit(first))
        self.assertTrue(seed.commit(second))
        slot_a, slot_b = seed.image()
        for index in range(RECORD_BYTES):
            with self.subTest(index=index):
                corrupt = bytearray(slot_b)
                corrupt[index] ^= 1
                recovered = SaveJournal((slot_a, bytes(corrupt)))
                self.assertEqual(recovered.active_slot, 0)
                self.assertEqual(recovered.generation, 1)
                self.assertEqual(recovered.payload, first)

    def test_generation_wrap_selects_zero_after_ffffffff(self) -> None:
        older = encode_record(SavePayload(), 0xFFFFFFFF)
        newer_payload = SavePayload().with_clear(
            0, rank=1, attempts=2, echoes=1, time_remaining=200
        )
        newer = encode_record(newer_payload, 0)
        journal = SaveJournal((older, newer))
        self.assertEqual(journal.active_slot, 1)
        self.assertEqual(journal.payload, newer_payload)

    def test_commit_wraps_ffffffff_to_zero_and_remains_newest(self) -> None:
        original = SavePayload()
        candidate = original.with_clear(
            0, rank=1, attempts=2, echoes=0, time_remaining=200
        )
        blank = bytes([0xFF]) * RECORD_BYTES
        journal = SaveJournal((encode_record(original, 0xFFFFFFFF), blank))
        self.assertTrue(journal.commit(candidate))
        self.assertEqual(journal.active_slot, 1)
        self.assertEqual(journal.generation, 0)
        self.assertEqual(journal.payload, candidate)

    def test_generation_tie_and_half_range_ambiguity_choose_slot_a(self) -> None:
        first = SavePayload()
        second = SavePayload().with_clear(
            0, rank=1, attempts=2, echoes=0, time_remaining=200
        )
        for generation_a, generation_b in (
            (7, 7),
            (0, 0x80000000),
            (0x80000000, 0),
        ):
            with self.subTest(
                generation_a=generation_a, generation_b=generation_b
            ):
                journal = SaveJournal(
                    (
                        encode_record(first, generation_a),
                        encode_record(second, generation_b),
                    )
                )
                self.assertEqual(journal.active_slot, 0)
                self.assertEqual(journal.payload, first)


if __name__ == "__main__":
    unittest.main()
