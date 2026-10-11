from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Iterable

from tools.replay_tools.codec import (
    ANCH_STRUCT,
    ATMP_PREFIX_STRUCT,
    CHUNK_HEADER_STRUCT,
    FORMAT_VERSION,
    HEADER_BYTES,
    HEADER_STRUCT,
    MAGIC,
    MAX_FILE_BYTES,
    NO_TARGET,
    OUTC_STRUCT,
    TLIN_STRUCT,
    AnchorCommit,
    AnchorRecordType,
    Attempt,
    AttemptOperation,
    Checkpoint,
    CompatibilityIds,
    InputEncoding,
    NaturalEndReason,
    Outcome,
    Replay,
    ReplayCompatibilityError,
    ReplayIntegrityError,
    ReplayMode,
    ReplayValidationError,
    TimelineDecision,
    TimelineOperation,
    TimingMode,
    canonicalize_replay,
    content_id_from_sha256,
    decode_replay,
    encode_replay,
    replay_report,
    simulation_frames,
    validate_replay,
    zero_extended_inputs,
)


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools/replay_tools/codec.py"
COMPATIBILITY = CompatibilityIds(
    engine_compat_id=0x01020304,
    state_schema_id=0x05060708,
    content_id=0x1122334455667788,
)
FINAL_CRC = 0x22334455
FINAL_SIGNATURE = 0x66778899


def full_history_fixture() -> Replay:
    """Synthetic/non-canonical fixture: assertions were not simulated."""

    return Replay(
        mode=ReplayMode.FULL_HISTORY,
        engine_compat_id=COMPATIBILITY.engine_compat_id,
        state_schema_id=COMPATIBILITY.state_schema_id,
        content_id=COMPATIBILITY.content_id,
        stage_id=101,
        timing_mode=TimingMode.NTSC_60,
        initial_echo_count=0,
        maximum_active_echoes=2,
        anchor_capacity=1,
        initial_persistent_crc32=0x10203040,
        expected_final_state_crc32=FINAL_CRC,
        expected_completion_signature=FINAL_SIGNATURE,
        attempts=(
            Attempt(
                0,
                bytes((0x03, 0x10, 0x10)),
                InputEncoding.RAW,
                NaturalEndReason.TIMEOUT,
                AttemptOperation.APPEND,
            ),
            Attempt(
                1,
                bytes((0x20,)) * 4,
                InputEncoding.RLE,
                NaturalEndReason.TIMEOUT,
                AttemptOperation.APPEND,
            ),
            Attempt(
                2,
                bytes((0x40,)),
                InputEncoding.RAW,
                NaturalEndReason.SOLVED,
                AttemptOperation.REPLACE,
                1,
            ),
        ),
        timeline=(
            TimelineDecision(2, TimelineOperation.PROTECT, 0),
            TimelineDecision(2, TimelineOperation.REPLACE, 1),
        ),
        anchors=(
            AnchorCommit(
                1,
                2,
                AnchorRecordType.SOCKET_POWERED,
                target_id=100,
                state_id=1,
            ),
        ),
        checkpoints=(
            Checkpoint(0, 1, 0xAABBCCDD),
            Checkpoint(2, 1, 0x01010101),
        ),
        outcome=Outcome(
            NaturalEndReason.SOLVED,
            attempts_used=3,
            terminal_ticks=1,
            active_echoes=2,
            anchors_used=1,
            score=1234,
            state_crc32=FINAL_CRC,
            completion_signature=FINAL_SIGNATURE,
        ),
    )


def single_attempt_fixture(
    inputs: bytes = b"\x10",
    encoding: InputEncoding = InputEncoding.RAW,
) -> Replay:
    return Replay(
        mode=ReplayMode.FULL_HISTORY,
        engine_compat_id=COMPATIBILITY.engine_compat_id,
        state_schema_id=COMPATIBILITY.state_schema_id,
        content_id=COMPATIBILITY.content_id,
        stage_id=101,
        timing_mode=TimingMode.NTSC_60,
        initial_echo_count=0,
        maximum_active_echoes=1,
        anchor_capacity=0,
        initial_persistent_crc32=0,
        expected_final_state_crc32=FINAL_CRC,
        expected_completion_signature=FINAL_SIGNATURE,
        attempts=(
            Attempt(
                0,
                inputs,
                encoding,
                NaturalEndReason.SOLVED,
                AttemptOperation.APPEND,
            ),
        ),
        timeline=(),
        anchors=(),
        checkpoints=(),
        outcome=Outcome(
            NaturalEndReason.SOLVED,
            attempts_used=1,
            terminal_ticks=len(inputs),
            active_echoes=0,
            anchors_used=0,
            score=0,
            state_crc32=FINAL_CRC,
            completion_signature=FINAL_SIGNATURE,
        ),
    )


def b01_terminal_fixture(maximum_active_echoes: int = 2) -> Replay:
    """Two attempts: one committed echo plus the solved current incarnation."""

    source = single_attempt_fixture()
    return replace(
        source,
        maximum_active_echoes=maximum_active_echoes,
        attempts=(
            Attempt(
                0,
                b"\x20\x00",
                InputEncoding.RAW,
                NaturalEndReason.TIMEOUT,
                AttemptOperation.APPEND,
            ),
            Attempt(
                1,
                b"\x20",
                InputEncoding.RAW,
                NaturalEndReason.SOLVED,
                AttemptOperation.APPEND,
            ),
        ),
        outcome=replace(
            source.outcome,
            attempts_used=2,
            terminal_ticks=1,
            active_echoes=1,
        ),
    )


def composition_fixture() -> Replay:
    return Replay(
        mode=ReplayMode.COMPOSITION,
        engine_compat_id=COMPATIBILITY.engine_compat_id,
        state_schema_id=COMPATIBILITY.state_schema_id,
        content_id=COMPATIBILITY.content_id,
        stage_id=201,
        timing_mode=TimingMode.PAL_50,
        initial_echo_count=2,
        maximum_active_echoes=3,
        anchor_capacity=0,
        initial_persistent_crc32=0,
        expected_final_state_crc32=FINAL_CRC,
        expected_completion_signature=FINAL_SIGNATURE,
        attempts=(
            Attempt(
                0,
                b"\x10\x00",
                InputEncoding.RAW,
                NaturalEndReason.TIMEOUT,
                AttemptOperation.REPLACE,
                0,
            ),
            Attempt(
                1,
                b"\x20\x00",
                InputEncoding.RLE,
                NaturalEndReason.TIMEOUT,
                AttemptOperation.REPLACE,
                1,
            ),
            Attempt(
                2,
                b"\x40",
                InputEncoding.RAW,
                NaturalEndReason.SOLVED,
                AttemptOperation.APPEND,
                NO_TARGET,
            ),
        ),
        timeline=(),
        anchors=(),
        checkpoints=(),
        outcome=Outcome(
            NaturalEndReason.SOLVED,
            attempts_used=3,
            terminal_ticks=1,
            active_echoes=2,
            anchors_used=0,
            score=0,
            state_crc32=FINAL_CRC,
            completion_signature=FINAL_SIGNATURE,
        ),
    )


def parse_chunks(encoded: bytes) -> list[tuple[bytes, bytes, bytes]]:
    count = struct.unpack_from(">H", encoded, 0x24)[0]
    cursor = HEADER_BYTES
    chunks = []
    for _ in range(count):
        tag, payload_bytes, _crc = CHUNK_HEADER_STRUCT.unpack_from(encoded, cursor)
        cursor += CHUNK_HEADER_STRUCT.size
        payload = encoded[cursor : cursor + payload_bytes]
        cursor += payload_bytes
        padding = encoded[cursor : cursor + (payload_bytes & 1)]
        cursor += payload_bytes & 1
        chunks.append((tag, payload, padding))
    if cursor != len(encoded):
        raise AssertionError("fixture parser found trailing bytes")
    return chunks


def frame_chunk(
    tag: bytes,
    payload: bytes,
    *,
    crc: int | None = None,
    padding: bytes | None = None,
) -> bytes:
    checksum = zlib.crc32(payload) & 0xFFFFFFFF if crc is None else crc
    result = CHUNK_HEADER_STRUCT.pack(tag, len(payload), checksum) + payload
    if len(payload) & 1:
        result += b"\x00" if padding is None else padding
    return result


def rebuild_file(
    original: bytes,
    chunks: Iterable[bytes],
    *,
    extra_payload: bytes = b"",
    chunk_count: int | None = None,
    checkpoint_count: int | None = None,
) -> bytes:
    framed = tuple(chunks)
    payload = b"".join(framed) + extra_payload
    header = bytearray(original[:HEADER_BYTES])
    struct.pack_into(">H", header, 0x24, len(framed) if chunk_count is None else chunk_count)
    if checkpoint_count is not None:
        struct.pack_into(">H", header, 0x26, checkpoint_count)
    struct.pack_into(">I", header, 0x34, len(payload))
    struct.pack_into(">I", header, 0x38, zlib.crc32(payload) & 0xFFFFFFFF)
    struct.pack_into(">I", header, 0x3C, zlib.crc32(header[:60]) & 0xFFFFFFFF)
    return bytes(header) + payload


def mutate_header(
    encoded: bytes,
    offset: int,
    format_string: str,
    value: int,
) -> bytes:
    result = bytearray(encoded)
    struct.pack_into(format_string, result, offset, value)
    struct.pack_into(">I", result, 0x3C, zlib.crc32(result[:60]) & 0xFFFFFFFF)
    return bytes(result)


def replace_chunk(
    encoded: bytes,
    index: int,
    *,
    tag: bytes | None = None,
    payload: bytes | None = None,
    crc: int | None = None,
    padding: bytes | None = None,
) -> bytes:
    parsed = parse_chunks(encoded)
    old_tag, old_payload, _old_padding = parsed[index]
    framed = [
        frame_chunk(
            replacement_tag,
            replacement_payload,
            crc=crc if chunk_index == index else None,
            padding=padding if chunk_index == index else None,
        )
        for chunk_index, (current_tag, current_payload, _current_padding) in enumerate(
            parsed
        )
        for replacement_tag, replacement_payload in (
            (
                tag if chunk_index == index and tag is not None else current_tag,
                (
                    payload
                    if chunk_index == index and payload is not None
                    else current_payload
                ),
            ),
        )
    ]
    del old_tag, old_payload
    return rebuild_file(encoded, framed)


class ReplayCodecTests(unittest.TestCase):
    def test_full_history_solved_final_append_remains_current(self) -> None:
        replay = b01_terminal_fixture()
        encoded = encode_replay(replay)
        self.assertEqual(replay, decode_replay(encoded, COMPATIBILITY))
        self.assertEqual(1, replay.outcome.active_echoes)
        report = replay_report(replay, encoded)
        self.assertEqual(2, report["counts"]["attempts"])
        self.assertEqual(1, report["outcome_assertion"]["active_echoes"])

        incorrect_terminal_count = replace(
            replay,
            outcome=replace(replay.outcome, active_echoes=2),
        )
        with self.assertRaisesRegex(ReplayValidationError, "outside 1..1"):
            encode_replay(incorrect_terminal_count)

    def test_solved_current_does_not_require_a_free_echo_slot(self) -> None:
        at_capacity = b01_terminal_fixture(maximum_active_echoes=1)
        self.assertEqual(
            at_capacity,
            decode_replay(encode_replay(at_capacity), COMPATIBILITY),
        )

    def test_non_solved_final_append_commits_and_still_obeys_capacity(self) -> None:
        source = single_attempt_fixture()
        timeout = replace(
            source,
            attempts=(
                replace(
                    source.attempts[0],
                    natural_end_reason=NaturalEndReason.TIMEOUT,
                ),
            ),
            outcome=replace(
                source.outcome,
                terminal_reason=NaturalEndReason.TIMEOUT,
                active_echoes=1,
            ),
        )
        self.assertEqual(timeout, decode_replay(encode_replay(timeout)))

        two_commits = b01_terminal_fixture()
        two_commits = replace(
            two_commits,
            attempts=(
                two_commits.attempts[0],
                replace(
                    two_commits.attempts[1],
                    natural_end_reason=NaturalEndReason.TIMEOUT,
                ),
            ),
            outcome=replace(
                two_commits.outcome,
                terminal_reason=NaturalEndReason.TIMEOUT,
                active_echoes=2,
            ),
        )
        self.assertEqual(two_commits, decode_replay(encode_replay(two_commits)))

        no_free_slot = replace(two_commits, maximum_active_echoes=1)
        with self.assertRaisesRegex(
            ReplayValidationError, "APPEND exceeds maximum active echoes"
        ):
            encode_replay(no_free_slot)

    def test_terminal_reason_matches_final_attempt_and_solved_is_final_only(self) -> None:
        replay = b01_terminal_fixture()
        mismatch = replace(
            replay,
            outcome=replace(
                replay.outcome,
                terminal_reason=NaturalEndReason.TIMEOUT,
            ),
        )
        with self.assertRaisesRegex(
            ReplayValidationError,
            "must match the final attempt natural_end_reason",
        ):
            encode_replay(mismatch)

        premature_solved = replace(
            replay,
            attempts=(
                replace(
                    replay.attempts[0],
                    natural_end_reason=NaturalEndReason.SOLVED,
                ),
                replay.attempts[1],
            ),
        )
        with self.assertRaisesRegex(
            ReplayValidationError,
            "SOLVED is terminal and may appear only on the final attempt",
        ):
            encode_replay(premature_solved)

    def test_exact_64_byte_header_big_endian_and_crcs(self) -> None:
        encoded = encode_replay(full_history_fixture())
        self.assertEqual(MAGIC, encoded[:4])
        self.assertEqual(b"\x00\x01\x00\x40", encoded[4:8])
        self.assertEqual(b"\x01\x02\x03\x04", encoded[0x0C:0x10])
        self.assertEqual(b"\x05\x06\x07\x08", encoded[0x10:0x14])
        self.assertEqual(
            b"\x11\x22\x33\x44\x55\x66\x77\x88", encoded[0x14:0x1C]
        )
        unpacked = HEADER_STRUCT.unpack_from(encoded)
        self.assertEqual(64, HEADER_STRUCT.size)
        self.assertEqual(FORMAT_VERSION, unpacked[1])
        self.assertEqual(HEADER_BYTES, unpacked[2])
        self.assertEqual(len(encoded) - HEADER_BYTES, unpacked[18])
        self.assertEqual(
            zlib.crc32(encoded[HEADER_BYTES:]) & 0xFFFFFFFF, unpacked[19]
        )
        self.assertEqual(zlib.crc32(encoded[:60]) & 0xFFFFFFFF, unpacked[20])

    def test_round_trip_returns_frozen_immutable_graph(self) -> None:
        source = full_history_fixture()
        encoded = encode_replay(source)
        mutable = bytearray(encoded)
        decoded = decode_replay(mutable, COMPATIBILITY)
        mutable[-1] ^= 0xFF
        self.assertEqual(source, decoded)
        self.assertEqual(encoded, encode_replay(decoded))
        with self.assertRaises(FrozenInstanceError):
            decoded.stage_id = 102  # type: ignore[misc]
        self.assertIsInstance(decoded.attempts, tuple)
        self.assertIsInstance(decoded.attempts[0].inputs, bytes)

    def test_two_encodes_and_reports_are_byte_identical(self) -> None:
        replay = full_history_fixture()
        first = encode_replay(replay)
        second = encode_replay(replay)
        self.assertEqual(first, second)
        first_report = json.dumps(replay_report(replay, first), sort_keys=True)
        second_report = json.dumps(replay_report(replay, second), sort_keys=True)
        self.assertEqual(first_report, second_report)
        self.assertIn("not a canonical gameplay result", first_report)

    def test_raw_rle_and_auto_canonicalization_are_deterministic(self) -> None:
        replay = full_history_fixture()
        raw = canonicalize_replay(replay, "raw")
        rle = canonicalize_replay(replay, "rle")
        auto = canonicalize_replay(replay, "auto")
        self.assertTrue(all(a.encoding is InputEncoding.RAW for a in raw.attempts))
        self.assertTrue(all(a.encoding is InputEncoding.RLE for a in rle.attempts))
        self.assertEqual(
            (InputEncoding.RAW, InputEncoding.RLE, InputEncoding.RAW),
            tuple(a.encoding for a in auto.attempts),
        )
        for candidate in (raw, rle, auto):
            encoded = encode_replay(candidate)
            self.assertEqual(candidate, decode_replay(encoded, COMPATIBILITY))
            self.assertEqual(encoded, encode_replay(decode_replay(encoded)))

    def test_opposite_axes_preserved_then_sanitized_only_at_boundary(self) -> None:
        replay = full_history_fixture()
        decoded = decode_replay(encode_replay(replay))
        self.assertEqual(0x03, decoded.attempts[0].inputs[0])
        frames = simulation_frames(decoded.attempts[0], total_frames=5)
        self.assertEqual(0, frames[0].held)
        self.assertEqual(0x10, frames[1].held)
        self.assertEqual(0x10, frames[1].pressed)
        self.assertEqual(0, frames[2].pressed)
        self.assertEqual(0x10, frames[3].released)
        self.assertEqual(bytes((0x03, 0x10, 0x10, 0, 0)), zero_extended_inputs(decoded.attempts[0], 5))

    def test_partial_zero_and_719_720_frame_recordings(self) -> None:
        for frame_count in (0, 719, 720):
            with self.subTest(frame_count=frame_count):
                replay = single_attempt_fixture(bytes(frame_count))
                encoded = encode_replay(replay)
                decoded = decode_replay(encoded)
                self.assertEqual(frame_count, decoded.attempts[0].frame_count)
                self.assertEqual(720, len(zero_extended_inputs(decoded.attempts[0])))
        invalid = single_attempt_fixture(bytes(721))
        with self.assertRaisesRegex(ReplayValidationError, "exceeds 720"):
            encode_replay(invalid)

    def test_exact_64_attempt_bound_and_excess(self) -> None:
        attempts = []
        timeline = []
        for ordinal in range(64):
            if ordinal < 6:
                operation = AttemptOperation.APPEND
                target = NO_TARGET
            else:
                operation = AttemptOperation.REPLACE
                target = 0
                timeline.append(
                    TimelineDecision(ordinal, TimelineOperation.REPLACE, 0)
                )
            attempts.append(
                Attempt(
                    ordinal,
                    b"\x00",
                    InputEncoding.RAW,
                    (
                        NaturalEndReason.SOLVED
                        if ordinal == 63
                        else NaturalEndReason.TIMEOUT
                    ),
                    operation,
                    target,
                )
            )
        replay = replace(
            single_attempt_fixture(),
            maximum_active_echoes=6,
            attempts=tuple(attempts),
            timeline=tuple(timeline),
            outcome=replace(
                single_attempt_fixture().outcome,
                attempts_used=64,
                terminal_ticks=1,
                active_echoes=6,
            ),
        )
        encoded = encode_replay(replay)
        self.assertLessEqual(len(encoded), MAX_FILE_BYTES)
        self.assertEqual(64, len(decode_replay(encoded).attempts))
        excess = replace(
            replay,
            attempts=replay.attempts
            + (
                Attempt(
                    64,
                    b"",
                    InputEncoding.RAW,
                    NaturalEndReason.SOLVED,
                    AttemptOperation.REPLACE,
                    0,
                ),
            ),
            timeline=replay.timeline
            + (TimelineDecision(64, TimelineOperation.REPLACE, 0),),
            outcome=replace(replay.outcome, attempts_used=65),
        )
        with self.assertRaisesRegex(ReplayValidationError, "count must be 1..64"):
            encode_replay(excess)

    def test_header_and_payload_crc_corruption_fail_closed(self) -> None:
        encoded = bytearray(encode_replay(full_history_fixture()))
        encoded[0x0C] ^= 1
        with self.assertRaisesRegex(ReplayIntegrityError, "header_crc32"):
            decode_replay(encoded)

        encoded = bytearray(encode_replay(full_history_fixture()))
        encoded[-1] ^= 1
        with self.assertRaisesRegex(ReplayIntegrityError, "payload_crc32"):
            decode_replay(encoded)

        encoded = encode_replay(full_history_fixture())
        parsed = parse_chunks(encoded)
        framed = [
            frame_chunk(tag, payload, crc=(0 if index == 0 else None))
            for index, (tag, payload, _padding) in enumerate(parsed)
        ]
        repaired_outer = rebuild_file(encoded, framed)
        with self.assertRaisesRegex(ReplayIntegrityError, "chunks\\[0\\].payload_crc32"):
            decode_replay(repaired_outer)

    def test_unknown_flags_timing_stage_and_compatibility_ids(self) -> None:
        encoded = encode_replay(full_history_fixture())
        cases = (
            (0x08, ">I", 3, "header.flags"),
            (0x1E, ">B", 2, "timing"),
            (0x1C, ">H", 999, "stage"),
            (0x0C, ">I", 0, "engine_compat_id"),
            (0x10, ">I", 0, "state_schema_id"),
            (0x14, ">Q", 0, "content_id"),
        )
        for offset, fmt, value, message in cases:
            with self.subTest(message=message):
                mutated = mutate_header(encoded, offset, fmt, value)
                with self.assertRaisesRegex(ReplayValidationError, message):
                    decode_replay(mutated)
        with self.assertRaisesRegex(ReplayCompatibilityError, "engine_compat_id"):
            decode_replay(
                encoded,
                replace(COMPATIBILITY, engine_compat_id=0xDEADBEEF),
            )
        with self.assertRaisesRegex(ReplayCompatibilityError, "state_schema_id"):
            decode_replay(
                encoded,
                replace(COMPATIBILITY, state_schema_id=0xDEADBEEF),
            )
        with self.assertRaisesRegex(ReplayCompatibilityError, "content_id"):
            decode_replay(
                encoded,
                replace(COMPATIBILITY, content_id=0xDEADBEEF),
            )

    def test_endianness_wrong_version_and_file_size_bound(self) -> None:
        encoded = bytearray(encode_replay(full_history_fixture()))
        encoded[4:6] = b"\x01\x00"
        with self.assertRaisesRegex(ReplayIntegrityError, "unsupported version 256"):
            decode_replay(encoded)
        oversized = encode_replay(full_history_fixture()) + bytes(MAX_FILE_BYTES)
        with self.assertRaisesRegex(ReplayIntegrityError, "exceeds 65536"):
            decode_replay(oversized)
        encoded = encode_replay(full_history_fixture())
        wrong_payload_length = mutate_header(
            encoded,
            0x34,
            ">I",
            len(encoded) - HEADER_BYTES - 2,
        )
        with self.assertRaisesRegex(ReplayIntegrityError, "remaining file length"):
            decode_replay(wrong_payload_length)

    def test_echo_anchor_and_initial_count_bounds(self) -> None:
        replay = single_attempt_fixture()
        cases = (
            (replace(replay, maximum_active_echoes=0), "outside 1..6"),
            (replace(replay, maximum_active_echoes=7), "outside 1..6"),
            (replace(replay, anchor_capacity=3), "outside 0..2"),
            (replace(replay, initial_echo_count=1), "must begin with zero echoes"),
        )
        for candidate, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ReplayValidationError, message):
                    encode_replay(candidate)

    def test_chunk_padding_unknown_tag_and_trailing_bytes(self) -> None:
        encoded = encode_replay(single_attempt_fixture(b"\x10"))
        parsed = parse_chunks(encoded)
        self.assertEqual(b"\x00", parsed[0][2])
        framed = [
            frame_chunk(tag, payload, padding=(b"\x01" if index == 0 else None))
            for index, (tag, payload, _padding) in enumerate(parsed)
        ]
        nonzero_pad = rebuild_file(encoded, framed)
        with self.assertRaisesRegex(ReplayIntegrityError, "pad byte must be zero"):
            decode_replay(nonzero_pad)

        unknown = replace_chunk(encoded, 0, tag=b"NOPE")
        with self.assertRaisesRegex(ReplayValidationError, "unknown tag"):
            decode_replay(unknown)

        framed = [
            frame_chunk(tag, payload) for tag, payload, _padding in parsed
        ]
        trailing = rebuild_file(encoded, framed, extra_payload=b"\x00\x00")
        with self.assertRaisesRegex(ReplayIntegrityError, "trailing bytes"):
            decode_replay(trailing)

    def test_chunk_and_header_count_mismatches(self) -> None:
        encoded = encode_replay(full_history_fixture())
        too_many_chunks = mutate_header(encoded, 0x24, ">H", 10)
        with self.assertRaisesRegex(ReplayIntegrityError, "truncated chunk header"):
            decode_replay(too_many_chunks)
        too_few_attempts = mutate_header(encoded, 0x20, ">H", 2)
        with self.assertRaisesRegex(ReplayValidationError, "excess ATMP"):
            decode_replay(too_few_attempts)
        wrong_checkpoints = mutate_header(encoded, 0x26, ">H", 1)
        with self.assertRaisesRegex(ReplayValidationError, "header declares 1"):
            decode_replay(wrong_checkpoints)
        excess_bound = mutate_header(encoded, 0x24, ">H", 2049)
        with self.assertRaisesRegex(ReplayValidationError, "outside 2..2048"):
            decode_replay(excess_bound)

    def test_duplicate_missing_and_nonfinal_outcome_rejected(self) -> None:
        encoded = encode_replay(single_attempt_fixture())
        parsed = parse_chunks(encoded)
        framed = [frame_chunk(tag, payload) for tag, payload, _padding in parsed]
        duplicate = rebuild_file(encoded, framed + [framed[-1]])
        with self.assertRaisesRegex(ReplayValidationError, "OUTC must be the final"):
            decode_replay(duplicate)
        history = encode_replay(full_history_fixture())
        history_chunks = parse_chunks(history)
        missing = rebuild_file(
            history,
            [
                frame_chunk(tag, payload)
                for tag, payload, _padding in history_chunks[:-1]
            ],
        )
        with self.assertRaisesRegex(ReplayValidationError, "exactly one final OUTC"):
            decode_replay(missing)

    def test_raw_bit7_length_and_uncompressed_crc_rejected(self) -> None:
        encoded = encode_replay(single_attempt_fixture(b"\x10"))
        payload = bytearray(parse_chunks(encoded)[0][1])
        payload[12] = 0x80
        struct.pack_into(">I", payload, 8, zlib.crc32(b"\x80") & 0xFFFFFFFF)
        bit7 = replace_chunk(encoded, 0, payload=bytes(payload))
        with self.assertRaisesRegex(ReplayValidationError, "reserved bit 7"):
            decode_replay(bit7)

        payload = bytearray(parse_chunks(encoded)[0][1])
        payload.extend(b"\x00")
        trailing_raw = replace_chunk(encoded, 0, payload=bytes(payload))
        with self.assertRaisesRegex(ReplayIntegrityError, "raw byte count"):
            decode_replay(trailing_raw)

        payload = bytearray(parse_chunks(encoded)[0][1])
        struct.pack_into(">I", payload, 8, 0)
        bad_crc = replace_chunk(encoded, 0, payload=bytes(payload))
        with self.assertRaisesRegex(ReplayIntegrityError, "uncompressed input CRC"):
            decode_replay(bad_crc)

    def _rle_file_with_payload_data(
        self,
        frame_count: int,
        data: bytes,
        raw_for_crc: bytes = b"",
    ) -> bytes:
        encoded = encode_replay(single_attempt_fixture(b"\x00", InputEncoding.RLE))
        payload = bytearray(parse_chunks(encoded)[0][1][:12])
        struct.pack_into(">H", payload, 2, frame_count)
        struct.pack_into(">I", payload, 8, zlib.crc32(raw_for_crc) & 0xFFFFFFFF)
        payload.extend(data)
        return replace_chunk(encoded, 0, payload=bytes(payload))

    def test_rle_zero_run_bomb_partial_run_and_noncanonical_runs(self) -> None:
        cases = (
            (
                self._rle_file_with_payload_data(1, b"\x00\x00\x00"),
                ReplayValidationError,
                "zero-length",
            ),
            (
                self._rle_file_with_payload_data(720, b"\x02\xD1\x00"),
                ReplayValidationError,
                "decompression bomb",
            ),
            (
                self._rle_file_with_payload_data(1, b"\x00\x01"),
                ReplayIntegrityError,
                "partial run",
            ),
            (
                self._rle_file_with_payload_data(
                    2,
                    b"\x00\x01\x00\x00\x01\x00",
                    b"\x00\x00",
                ),
                ReplayValidationError,
                "not canonical",
            ),
            (
                self._rle_file_with_payload_data(2, b"\x00\x01\x00"),
                ReplayIntegrityError,
                "does not equal frame_count",
            ),
            (
                self._rle_file_with_payload_data(1, b"\x00\x01\x80"),
                ReplayValidationError,
                "reserved bit 7",
            ),
        )
        for encoded, error_type, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(error_type, message):
                    decode_replay(encoded)

    def test_unknown_attempt_timeline_anchor_and_outcome_tags(self) -> None:
        encoded = encode_replay(full_history_fixture())
        parsed = parse_chunks(encoded)
        cases = []
        atmp = bytearray(parsed[0][1])
        atmp[4] = 2
        cases.append((0, bytes(atmp), "InputEncoding"))
        atmp = bytearray(parsed[0][1])
        atmp[5] = 0
        cases.append((0, bytes(atmp), "NaturalEndReason"))
        atmp = bytearray(parsed[0][1])
        atmp[6] = 2
        cases.append((0, bytes(atmp), "AttemptOperation"))
        tlin = bytearray(parsed[2][1])
        tlin[2] = 3
        cases.append((2, bytes(tlin), "TimelineOperation"))
        anch = bytearray(parsed[5][1])
        struct.pack_into(">H", anch, 4, 99)
        cases.append((5, bytes(anch), "AnchorRecordType"))
        outc = bytearray(parsed[-1][1])
        outc[0] = 0
        cases.append((len(parsed) - 1, bytes(outc), "NaturalEndReason"))
        for index, payload, message in cases:
            with self.subTest(message=message):
                mutated = replace_chunk(encoded, index, payload=payload)
                with self.assertRaisesRegex(ReplayValidationError, message):
                    decode_replay(mutated)

    def test_reserved_fields_and_exact_payload_lengths(self) -> None:
        encoded = encode_replay(full_history_fixture())
        parsed = parse_chunks(encoded)
        anch = bytearray(parsed[5][1])
        struct.pack_into(">H", anch, 10, 1)
        with self.assertRaisesRegex(ReplayValidationError, "reserved"):
            decode_replay(replace_chunk(encoded, 5, payload=bytes(anch)))
        outc = bytearray(parsed[-1][1])
        outc[1] = 1
        with self.assertRaisesRegex(ReplayValidationError, "reserved"):
            decode_replay(
                replace_chunk(encoded, len(parsed) - 1, payload=bytes(outc))
            )
        with self.assertRaisesRegex(ReplayIntegrityError, "exactly 8 bytes"):
            decode_replay(replace_chunk(encoded, 6, payload=parsed[6][1] + b"\x00"))

    def test_timeline_protection_replacement_and_target_constraints(self) -> None:
        valid = full_history_fixture()
        validate_replay(valid)
        protected_replace = replace(
            valid,
            attempts=valid.attempts[:2]
            + (replace(valid.attempts[2], target_slot=0),),
            timeline=(
                TimelineDecision(2, TimelineOperation.PROTECT, 0),
                TimelineDecision(2, TimelineOperation.REPLACE, 0),
            ),
        )
        with self.assertRaisesRegex(ReplayValidationError, "protected slot"):
            encode_replay(protected_replace)

        unprotected_replace = replace(
            protected_replace,
            timeline=(
                TimelineDecision(2, TimelineOperation.PROTECT, 0),
                TimelineDecision(2, TimelineOperation.UNPROTECT, 0),
                TimelineDecision(2, TimelineOperation.REPLACE, 0),
            ),
        )
        self.assertEqual(
            unprotected_replace,
            decode_replay(encode_replay(unprotected_replace)),
        )

        missing_selection = replace(valid, timeline=valid.timeline[:1])
        with self.assertRaisesRegex(ReplayValidationError, "explicit TLIN"):
            encode_replay(missing_selection)
        bad_append_target = replace(
            single_attempt_fixture(),
            attempts=(
                replace(
                    single_attempt_fixture().attempts[0],
                    target_slot=0,
                ),
            ),
        )
        with self.assertRaisesRegex(ReplayValidationError, "target must be 0xFF"):
            encode_replay(bad_append_target)

    def test_duplicate_timeline_and_inactive_protection_rejected(self) -> None:
        replay = full_history_fixture()
        duplicate = replace(replay, timeline=replay.timeline + (replay.timeline[-1],))
        with self.assertRaisesRegex(ReplayValidationError, "duplicate timeline"):
            encode_replay(duplicate)
        inactive = replace(
            replay,
            timeline=(
                TimelineDecision(1, TimelineOperation.PROTECT, 1),
                *replay.timeline,
            ),
        )
        with self.assertRaisesRegex(ReplayValidationError, "inactive slot"):
            encode_replay(inactive)

    def test_composition_preload_contract_and_no_timeline(self) -> None:
        replay = composition_fixture()
        self.assertEqual(replay, decode_replay(encode_replay(replay), COMPATIBILITY))
        with_timeline = replace(
            replay,
            timeline=(TimelineDecision(1, TimelineOperation.PROTECT, 0),),
        )
        with self.assertRaisesRegex(ReplayValidationError, "cannot contain TLIN"):
            encode_replay(with_timeline)
        duplicate_target = replace(
            replay,
            attempts=(
                replay.attempts[0],
                replace(replay.attempts[1], target_slot=0),
                replay.attempts[2],
            ),
        )
        with self.assertRaisesRegex(ReplayValidationError, "duplicate composition"):
            encode_replay(duplicate_target)
        missing_current = replace(
            replay,
            initial_echo_count=1,
        )
        with self.assertRaisesRegex(ReplayValidationError, "one stream per"):
            encode_replay(missing_current)

    def test_attempt_checkpoint_and_anchor_chronology(self) -> None:
        replay = full_history_fixture()
        bad_attempts = replace(
            replay,
            attempts=(
                replay.attempts[0],
                replace(replay.attempts[1], ordinal=2),
                replay.attempts[2],
            ),
        )
        with self.assertRaisesRegex(ReplayValidationError, "outside 1..1"):
            encode_replay(bad_attempts)
        bad_checkpoints = replace(
            replay,
            checkpoints=tuple(reversed(replay.checkpoints)),
        )
        with self.assertRaisesRegex(ReplayValidationError, "not strictly chronological"):
            encode_replay(bad_checkpoints)
        second_anchor = AnchorCommit(
            0, 1, AnchorRecordType.ROUTE_OPEN, target_id=200, state_id=1
        )
        bad_anchors = replace(
            replay,
            anchor_capacity=2,
            anchors=(replay.anchors[0], second_anchor),
            outcome=replace(replay.outcome, anchors_used=2),
        )
        with self.assertRaisesRegex(ReplayValidationError, "not strictly chronological"):
            encode_replay(bad_anchors)

        encoded = encode_replay(replay)
        parsed = parse_chunks(encoded)
        swapped = [frame_chunk(tag, payload) for tag, payload, _pad in parsed]
        swapped[0], swapped[1] = swapped[1], swapped[0]
        with self.assertRaisesRegex(ReplayValidationError, "zero-based and chronological"):
            decode_replay(rebuild_file(encoded, swapped))

    def test_anchor_capacity_duplicate_target_and_tick_bounds(self) -> None:
        replay = full_history_fixture()
        with self.assertRaisesRegex(ReplayValidationError, "exceed header capacity"):
            encode_replay(replace(replay, anchor_capacity=0))
        duplicate = replace(
            replay,
            anchor_capacity=2,
            anchors=(
                replay.anchors[0],
                replace(replay.anchors[0], attempt=2, completed_ticks=1),
            ),
            outcome=replace(replay.outcome, anchors_used=2),
        )
        with self.assertRaisesRegex(ReplayValidationError, "duplicate typed anchor"):
            encode_replay(duplicate)
        late = replace(
            replay,
            anchors=(replace(replay.anchors[0], completed_ticks=5),),
        )
        with self.assertRaisesRegex(ReplayValidationError, "outside 0..4"):
            encode_replay(late)

    def test_checkpoint_tick_and_outcome_assertion_constraints(self) -> None:
        replay = full_history_fixture()
        late = replace(
            replay,
            checkpoints=(
                replace(replay.checkpoints[0], completed_ticks=4),
                replay.checkpoints[1],
            ),
        )
        with self.assertRaisesRegex(ReplayValidationError, "outside 0..3"):
            encode_replay(late)
        wrong_crc = replace(
            replay,
            outcome=replace(replay.outcome, state_crc32=0),
        )
        with self.assertRaisesRegex(ReplayValidationError, "does not match header"):
            encode_replay(wrong_crc)
        wrong_attempts = replace(
            replay,
            outcome=replace(replay.outcome, attempts_used=2),
        )
        with self.assertRaisesRegex(ReplayValidationError, "outside 3..3"):
            encode_replay(wrong_attempts)

    def test_chunk_payload_length_outside_file_rejected(self) -> None:
        encoded = bytearray(encode_replay(single_attempt_fixture()))
        struct.pack_into(">I", encoded, HEADER_BYTES + 4, 0xFFFFFFFF)
        struct.pack_into(
            ">I",
            encoded,
            0x38,
            zlib.crc32(encoded[HEADER_BYTES:]) & 0xFFFFFFFF,
        )
        struct.pack_into(">I", encoded, 0x3C, zlib.crc32(encoded[:60]) & 0xFFFFFFFF)
        with self.assertRaisesRegex(ReplayIntegrityError, "payload outside file"):
            decode_replay(encoded)

    def test_content_sha_truncation_is_explicit_big_endian(self) -> None:
        digest = "1122334455667788" + "00" * 24
        self.assertEqual(0x1122334455667788, content_id_from_sha256(digest))
        with self.assertRaisesRegex(ReplayValidationError, "64 hexadecimal"):
            content_id_from_sha256("00")
        with self.assertRaisesRegex(ReplayValidationError, "invalid hexadecimal"):
            content_id_from_sha256("z" * 64)

    def test_cli_validate_inspect_and_raw_rle_canonicalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.r94r"
            source.write_bytes(encode_replay(full_history_fixture()))
            compatibility_args = (
                "--engine-compat-id",
                hex(COMPATIBILITY.engine_compat_id),
                "--state-schema-id",
                hex(COMPATIBILITY.state_schema_id),
                "--content-id",
                hex(COMPATIBILITY.content_id),
            )
            validate = subprocess.run(
                (
                    sys.executable,
                    "-B",
                    str(CLI),
                    "validate",
                    str(source),
                    *compatibility_args,
                ),
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(0, validate.returncode, validate.stderr.decode())
            report = json.loads(validate.stdout)
            self.assertEqual("pass", report["status"])
            self.assertFalse(
                report["outcome_assertion"]["simulation_performed"]
            )
            missing_compatibility = subprocess.run(
                (
                    sys.executable,
                    "-B",
                    str(CLI),
                    "validate",
                    str(source),
                ),
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(1, missing_compatibility.returncode)
            self.assertIn(
                "validate requires engine",
                missing_compatibility.stderr.decode("utf-8"),
            )

            first_inspect = subprocess.run(
                (sys.executable, "-B", str(CLI), "inspect", str(source)),
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            second_inspect = subprocess.run(
                (sys.executable, "-B", str(CLI), "inspect", str(source)),
                cwd=ROOT,
                check=False,
                capture_output=True,
            )
            self.assertEqual(first_inspect.stdout, second_inspect.stdout)

            for encoding in ("raw", "rle", "auto"):
                output = root / f"{encoding}.r94r"
                command = subprocess.run(
                    (
                        sys.executable,
                        "-B",
                        str(CLI),
                        "canonicalize",
                        str(source),
                        str(output),
                        "--encoding",
                        encoding,
                    ),
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(0, command.returncode, command.stderr.decode())
                decoded = decode_replay(output.read_bytes(), COMPATIBILITY)
                if encoding == "raw":
                    self.assertTrue(
                        all(
                            attempt.encoding is InputEncoding.RAW
                            for attempt in decoded.attempts
                        )
                    )
                elif encoding == "rle":
                    self.assertTrue(
                        all(
                            attempt.encoding is InputEncoding.RLE
                            for attempt in decoded.attempts
                        )
                    )


if __name__ == "__main__":
    unittest.main()
