#!/usr/bin/env python3
"""Strict, deterministic, standard-library-only R94R v1 host codec.

Expected outcomes in this module are assertions carried by a replay file.  No
function simulates gameplay or treats those assertions as observed results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass, replace
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable, Sequence


MAGIC = b"R94R"
FORMAT_VERSION = 1
HEADER_BYTES = 64
CHUNK_HEADER_BYTES = 12
MAX_FILE_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = MAX_FILE_BYTES - HEADER_BYTES
MAX_ATTEMPTS = 64
MAX_FRAMES = 720
MAX_ACTIVE_ECHOES = 6
MAX_ANCHOR_CAPACITY = 2
MAX_PROTECTED_SLOTS = 1
MAX_CHECKPOINTS = 1024
MAX_CHUNKS = 2048
NO_TARGET = 0xFF

HEADER_STRUCT = struct.Struct(">4sHHIIIQHBBHBBHHIIIIII")
HEADER_PREFIX_STRUCT = struct.Struct(">4sHHIIIQHBBHBBHHIIIII")
CHUNK_HEADER_STRUCT = struct.Struct(">4sII")
ATMP_PREFIX_STRUCT = struct.Struct(">HHBBBBI")
TLIN_STRUCT = struct.Struct(">HBB")
ANCH_STRUCT = struct.Struct(">HHHHHH")
CKPT_STRUCT = struct.Struct(">HHI")
OUTC_STRUCT = struct.Struct(">BBHHBBIII")

KNOWN_TAGS = frozenset((b"ATMP", b"TLIN", b"ANCH", b"CKPT", b"OUTC"))
KNOWN_STAGE_IDS = frozenset(
    (
        101,
        102,
        103,
        104,
        201,
        202,
        203,
        204,
        301,
        302,
        303,
        304,
        401,
        402,
        403,
        404,
        501,
        502,
        503,
        504,
    )
)


class ReplayMode(IntEnum):
    FULL_HISTORY = 0x00000001
    COMPOSITION = 0x00000002


class TimingMode(IntEnum):
    NTSC_60 = 0
    PAL_50 = 1


class InputEncoding(IntEnum):
    RAW = 0
    RLE = 1


class NaturalEndReason(IntEnum):
    TIMEOUT = 1
    DESTROYED = 2
    MANUAL_ABORT = 3
    SOLVED = 4
    PHASE_TRANSITION = 5


class AttemptOperation(IntEnum):
    APPEND = 0
    REPLACE = 1


class TimelineOperation(IntEnum):
    PROTECT = 0
    UNPROTECT = 1
    REPLACE = 2


class AnchorRecordType(IntEnum):
    SOCKET_POWERED = 1
    TERMINAL_REPAIRED = 2
    CONVEYOR_DIRECTION = 3
    LOCK_DESTROYED = 4
    BRIDGE_STATE = 5
    ROUTE_OPEN = 6


class ReplayError(ValueError):
    """Base class for replay codec failures."""


class ReplayIntegrityError(ReplayError):
    """Raised for framing, length, padding, or checksum corruption."""


class ReplayCompatibilityError(ReplayError):
    """Raised when engine, state, or content compatibility does not match."""


class ReplayValidationError(ReplayError):
    """Raised when typed replay semantics violate the frozen contract."""


@dataclass(frozen=True, slots=True)
class CompatibilityIds:
    engine_compat_id: int
    state_schema_id: int
    content_id: int


@dataclass(frozen=True, slots=True)
class Attempt:
    ordinal: int
    inputs: bytes
    encoding: InputEncoding
    natural_end_reason: NaturalEndReason
    operation: AttemptOperation
    target_slot: int = NO_TARGET

    @property
    def frame_count(self) -> int:
        return len(self.inputs)

    @property
    def uncompressed_crc32(self) -> int:
        return zlib.crc32(self.inputs) & 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class TimelineDecision:
    before_attempt: int
    operation: TimelineOperation
    target_slot: int


@dataclass(frozen=True, slots=True)
class AnchorCommit:
    attempt: int
    completed_ticks: int
    record_type: AnchorRecordType
    target_id: int
    state_id: int


@dataclass(frozen=True, slots=True)
class Checkpoint:
    attempt: int
    completed_ticks: int
    state_crc32: int


@dataclass(frozen=True, slots=True)
class Outcome:
    terminal_reason: NaturalEndReason
    attempts_used: int
    terminal_ticks: int
    active_echoes: int
    anchors_used: int
    score: int
    state_crc32: int
    completion_signature: int


@dataclass(frozen=True, slots=True)
class Replay:
    mode: ReplayMode
    engine_compat_id: int
    state_schema_id: int
    content_id: int
    stage_id: int
    timing_mode: TimingMode
    initial_echo_count: int
    maximum_active_echoes: int
    anchor_capacity: int
    initial_persistent_crc32: int
    expected_final_state_crc32: int
    expected_completion_signature: int
    attempts: tuple[Attempt, ...]
    timeline: tuple[TimelineDecision, ...]
    anchors: tuple[AnchorCommit, ...]
    checkpoints: tuple[Checkpoint, ...]
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class SimulationFrame:
    held: int
    pressed: int
    released: int


@dataclass(frozen=True, slots=True)
class _Header:
    mode: ReplayMode
    engine_compat_id: int
    state_schema_id: int
    content_id: int
    stage_id: int
    timing_mode: TimingMode
    initial_echo_count: int
    attempt_count: int
    maximum_active_echoes: int
    anchor_capacity: int
    chunk_count: int
    checkpoint_count: int
    initial_persistent_crc32: int
    expected_final_state_crc32: int
    expected_completion_signature: int
    payload_bytes: int
    payload_crc32: int
    header_crc32: int


def _validation_error(path: str, message: str) -> None:
    raise ReplayValidationError(f"{path}: {message}")


def _integrity_error(path: str, message: str) -> None:
    raise ReplayIntegrityError(f"{path}: {message}")


def _require_int(
    value: Any,
    path: str,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _validation_error(path, "expected integer, not Boolean")
    if not minimum <= value <= maximum:
        _validation_error(path, f"{value} outside {minimum}..{maximum}")
    return value


def _require_enum(value: Any, enum_type: type[IntEnum], path: str) -> IntEnum:
    if not isinstance(value, enum_type):
        _validation_error(path, f"expected {enum_type.__name__}")
    return value


def _validate_compatibility_ids(
    engine_compat_id: Any,
    state_schema_id: Any,
    content_id: Any,
    path: str,
) -> None:
    _require_int(engine_compat_id, f"{path}.engine_compat_id", 1, 0xFFFFFFFF)
    _require_int(state_schema_id, f"{path}.state_schema_id", 1, 0xFFFFFFFF)
    _require_int(content_id, f"{path}.content_id", 1, 0xFFFFFFFFFFFFFFFF)


def content_id_from_sha256(digest: str) -> int:
    """Return the big-endian first 64 bits of a full SHA-256 hex digest."""

    if not isinstance(digest, str) or len(digest) != 64:
        raise ReplayValidationError("sha256: expected exactly 64 hexadecimal digits")
    try:
        raw = bytes.fromhex(digest)
    except ValueError as exc:
        raise ReplayValidationError("sha256: invalid hexadecimal digest") from exc
    return int.from_bytes(raw[:8], "big")


def _validate_input_bytes(inputs: bytes, path: str) -> None:
    if not isinstance(inputs, bytes):
        _validation_error(path, "inputs must be immutable bytes")
    if len(inputs) > MAX_FRAMES:
        _validation_error(path, f"recording exceeds {MAX_FRAMES} frames")
    for frame, value in enumerate(inputs):
        if value & 0x80:
            _validation_error(
                f"{path}[{frame}]",
                "reserved bit 7 must be zero in recorded input",
            )


def sanitize_input_for_simulation(value: int) -> int:
    """Apply axis sanitation at the explicit simulation boundary.

    Recorded bytes are otherwise preserved exactly.  Bit 7 is an invalid replay
    byte and is rejected rather than silently masked.
    """

    _require_int(value, "input", 0, 0xFF)
    if value & 0x80:
        _validation_error("input", "reserved bit 7 must be zero")
    held = value
    if held & 0x03 == 0x03:
        held &= ~0x03
    if held & 0x0C == 0x0C:
        held &= ~0x0C
    return held


def zero_extended_inputs(
    attempt_or_inputs: Attempt | bytes,
    total_frames: int = MAX_FRAMES,
) -> bytes:
    """Return a recording padded with zero held input through `total_frames`."""

    _require_int(total_frames, "total_frames", 0, MAX_FRAMES)
    inputs = (
        attempt_or_inputs.inputs
        if isinstance(attempt_or_inputs, Attempt)
        else attempt_or_inputs
    )
    _validate_input_bytes(inputs, "inputs")
    if len(inputs) > total_frames:
        _validation_error("inputs", "recording is longer than requested playback")
    return inputs + bytes(total_frames - len(inputs))


def simulation_frames(
    attempt_or_inputs: Attempt | bytes,
    total_frames: int = MAX_FRAMES,
) -> tuple[SimulationFrame, ...]:
    """Zero-extend and derive held/pressed/released at simulation boundary."""

    playback = zero_extended_inputs(attempt_or_inputs, total_frames)
    frames: list[SimulationFrame] = []
    previous = 0
    for raw in playback:
        held = sanitize_input_for_simulation(raw)
        pressed = held & ~previous & 0x7F
        released = previous & ~held & 0x7F
        frames.append(SimulationFrame(held, pressed, released))
        previous = held
    return tuple(frames)


def _validate_attempt(attempt: Attempt, expected_ordinal: int) -> None:
    if not isinstance(attempt, Attempt):
        _validation_error(f"attempts[{expected_ordinal}]", "expected Attempt")
    _require_int(
        attempt.ordinal,
        f"attempts[{expected_ordinal}].ordinal",
        expected_ordinal,
        expected_ordinal,
    )
    _validate_input_bytes(attempt.inputs, f"attempts[{expected_ordinal}].inputs")
    _require_enum(
        attempt.encoding,
        InputEncoding,
        f"attempts[{expected_ordinal}].encoding",
    )
    _require_enum(
        attempt.natural_end_reason,
        NaturalEndReason,
        f"attempts[{expected_ordinal}].natural_end_reason",
    )
    _require_enum(
        attempt.operation,
        AttemptOperation,
        f"attempts[{expected_ordinal}].operation",
    )
    _require_int(
        attempt.target_slot,
        f"attempts[{expected_ordinal}].target_slot",
        0,
        0xFF,
    )


def _validate_outcome(replay: Replay, active_echoes: int) -> None:
    outcome = replay.outcome
    if not isinstance(outcome, Outcome):
        _validation_error("outcome", "expected Outcome")
    _require_enum(
        outcome.terminal_reason, NaturalEndReason, "outcome.terminal_reason"
    )
    if outcome.terminal_reason is not replay.attempts[-1].natural_end_reason:
        _validation_error(
            "outcome.terminal_reason",
            "must match the final attempt natural_end_reason",
        )
    _require_int(
        outcome.attempts_used,
        "outcome.attempts_used",
        len(replay.attempts),
        len(replay.attempts),
    )
    _require_int(outcome.terminal_ticks, "outcome.terminal_ticks", 0, MAX_FRAMES)
    if outcome.terminal_ticks > replay.attempts[-1].frame_count:
        _validation_error(
            "outcome.terminal_ticks",
            "cannot exceed the final recording length",
        )
    _require_int(
        outcome.active_echoes,
        "outcome.active_echoes",
        active_echoes,
        active_echoes,
    )
    _require_int(
        outcome.anchors_used,
        "outcome.anchors_used",
        len(replay.anchors),
        len(replay.anchors),
    )
    _require_int(outcome.score, "outcome.score", 0, 0xFFFFFFFF)
    _require_int(outcome.state_crc32, "outcome.state_crc32", 0, 0xFFFFFFFF)
    _require_int(
        outcome.completion_signature,
        "outcome.completion_signature",
        0,
        0xFFFFFFFF,
    )
    if outcome.state_crc32 != replay.expected_final_state_crc32:
        _validation_error(
            "outcome.state_crc32",
            "does not match header expected_final_state_crc32 assertion",
        )
    if outcome.completion_signature != replay.expected_completion_signature:
        _validation_error(
            "outcome.completion_signature",
            "does not match header expected_completion_signature assertion",
        )


def _validate_full_history(replay: Replay) -> int:
    if replay.initial_echo_count != 0:
        _validation_error(
            "initial_echo_count", "FULL_HISTORY must begin with zero echoes"
        )
    decisions_by_attempt: dict[int, list[TimelineDecision]] = {}
    prior_boundary = 0
    decision_keys: set[tuple[int, TimelineOperation, int]] = set()
    for index, decision in enumerate(replay.timeline):
        path = f"timeline[{index}]"
        if not isinstance(decision, TimelineDecision):
            _validation_error(path, "expected TimelineDecision")
        _require_int(
            decision.before_attempt,
            f"{path}.before_attempt",
            1,
            len(replay.attempts) - 1,
        )
        if decision.before_attempt < prior_boundary:
            _validation_error(path, "timeline decisions are not chronological")
        prior_boundary = decision.before_attempt
        _require_enum(decision.operation, TimelineOperation, f"{path}.operation")
        _require_int(
            decision.target_slot,
            f"{path}.target_slot",
            0,
            replay.maximum_active_echoes - 1,
        )
        key = (
            decision.before_attempt,
            decision.operation,
            decision.target_slot,
        )
        if key in decision_keys:
            _validation_error(path, "duplicate timeline decision")
        decision_keys.add(key)
        decisions_by_attempt.setdefault(decision.before_attempt, []).append(decision)

    active: set[int] = set()
    protected: set[int] = set()
    final_ordinal = len(replay.attempts) - 1
    for ordinal, attempt in enumerate(replay.attempts):
        if (
            attempt.natural_end_reason is NaturalEndReason.SOLVED
            and ordinal != final_ordinal
        ):
            _validation_error(
                f"attempts[{ordinal}].natural_end_reason",
                "SOLVED is terminal and may appear only on the final attempt",
            )
        solved_current = (
            ordinal == final_ordinal
            and attempt.natural_end_reason is NaturalEndReason.SOLVED
        )
        pending_replacement: int | None = None
        for decision in decisions_by_attempt.get(ordinal, []):
            target = decision.target_slot
            if decision.operation is TimelineOperation.PROTECT:
                if target not in active:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "cannot protect an inactive slot",
                    )
                if target in protected:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "slot is already protected",
                    )
                if len(protected) >= MAX_PROTECTED_SLOTS:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "only one slot may be protected",
                    )
                protected.add(target)
            elif decision.operation is TimelineOperation.UNPROTECT:
                if target not in protected:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "cannot unprotect an unprotected slot",
                    )
                protected.remove(target)
            else:
                if pending_replacement is not None:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "more than one replacement selection",
                    )
                if target not in active:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "cannot replace an inactive slot",
                    )
                if target in protected:
                    _validation_error(
                        f"timeline.before_attempt[{ordinal}]",
                        "cannot replace a protected slot",
                    )
                pending_replacement = target

        if attempt.operation is AttemptOperation.APPEND:
            if attempt.target_slot != NO_TARGET:
                _validation_error(
                    f"attempts[{ordinal}].target_slot",
                    "APPEND target must be 0xFF",
                )
            if pending_replacement is not None:
                _validation_error(
                    f"attempts[{ordinal}]",
                    "replacement decision was not consumed",
                )
            # PROGRAM_STABLE terminates GameplayStep without calling
            # GameplayCommitAndReset. The final solved stream therefore remains
            # the current incarnation and never enters the active echo order.
            if solved_current:
                continue
            if len(active) >= replay.maximum_active_echoes:
                _validation_error(
                    f"attempts[{ordinal}]",
                    "APPEND exceeds maximum active echoes",
                )
            free = next(
                slot
                for slot in range(replay.maximum_active_echoes)
                if slot not in active
            )
            active.add(free)
        else:
            if not 0 <= attempt.target_slot < replay.maximum_active_echoes:
                _validation_error(
                    f"attempts[{ordinal}].target_slot",
                    "REPLACE target outside echo slots",
                )
            if pending_replacement != attempt.target_slot:
                _validation_error(
                    f"attempts[{ordinal}]",
                    "REPLACE must match an explicit TLIN replacement selection",
                )
            if attempt.target_slot not in active:
                _validation_error(
                    f"attempts[{ordinal}]",
                    "REPLACE target is inactive",
                )
            if attempt.target_slot in protected:
                _validation_error(
                    f"attempts[{ordinal}]",
                    "REPLACE target is protected",
                )
    return len(active)


def _validate_composition(replay: Replay) -> int:
    if replay.timeline:
        _validation_error("timeline", "COMPOSITION cannot contain TLIN decisions")
    expected_attempts = replay.initial_echo_count + 1
    if len(replay.attempts) != expected_attempts:
        _validation_error(
            "attempts",
            "COMPOSITION requires one stream per initial echo plus final current",
        )
    targets: set[int] = set()
    for ordinal, attempt in enumerate(replay.attempts):
        if ordinal < replay.initial_echo_count:
            if attempt.operation is not AttemptOperation.REPLACE:
                _validation_error(
                    f"attempts[{ordinal}].operation",
                    "preloaded composition streams use REPLACE",
                )
            if not 0 <= attempt.target_slot < replay.initial_echo_count:
                _validation_error(
                    f"attempts[{ordinal}].target_slot",
                    "preloaded target outside initial echo slots",
                )
            if attempt.target_slot in targets:
                _validation_error(
                    f"attempts[{ordinal}].target_slot",
                    "duplicate composition target",
                )
            targets.add(attempt.target_slot)
        else:
            if (
                attempt.operation is not AttemptOperation.APPEND
                or attempt.target_slot != NO_TARGET
            ):
                _validation_error(
                    f"attempts[{ordinal}]",
                    "final composition current must use APPEND/0xFF",
                )
    if targets != set(range(replay.initial_echo_count)):
        _validation_error("attempts", "composition does not populate every echo slot")
    return replay.initial_echo_count


def _validate_anchors(replay: Replay) -> None:
    if len(replay.anchors) > replay.anchor_capacity:
        _validation_error("anchors", "anchor commits exceed header capacity")
    prior_key: tuple[int, int, int, int, int] | None = None
    logical_keys: set[tuple[AnchorRecordType, int]] = set()
    for index, anchor in enumerate(replay.anchors):
        path = f"anchors[{index}]"
        if not isinstance(anchor, AnchorCommit):
            _validation_error(path, "expected AnchorCommit")
        _require_int(anchor.attempt, f"{path}.attempt", 0, len(replay.attempts) - 1)
        _require_int(
            anchor.completed_ticks,
            f"{path}.completed_ticks",
            0,
            replay.attempts[anchor.attempt].frame_count,
        )
        _require_enum(anchor.record_type, AnchorRecordType, f"{path}.record_type")
        _require_int(anchor.target_id, f"{path}.target_id", 1, 0xFFFF)
        _require_int(anchor.state_id, f"{path}.state_id", 1, 0xFFFF)
        order_key = (
            anchor.attempt,
            anchor.completed_ticks,
            int(anchor.record_type),
            anchor.target_id,
            anchor.state_id,
        )
        if prior_key is not None and order_key <= prior_key:
            _validation_error(path, "anchor assertions are not strictly chronological")
        prior_key = order_key
        logical_key = (anchor.record_type, anchor.target_id)
        if logical_key in logical_keys:
            _validation_error(path, "duplicate typed anchor target")
        logical_keys.add(logical_key)


def _validate_checkpoints(replay: Replay) -> None:
    if len(replay.checkpoints) > MAX_CHECKPOINTS:
        _validation_error(
            "checkpoints", f"count exceeds bounded maximum {MAX_CHECKPOINTS}"
        )
    prior: tuple[int, int] | None = None
    for index, checkpoint in enumerate(replay.checkpoints):
        path = f"checkpoints[{index}]"
        if not isinstance(checkpoint, Checkpoint):
            _validation_error(path, "expected Checkpoint")
        _require_int(
            checkpoint.attempt,
            f"{path}.attempt",
            0,
            len(replay.attempts) - 1,
        )
        _require_int(
            checkpoint.completed_ticks,
            f"{path}.completed_ticks",
            0,
            replay.attempts[checkpoint.attempt].frame_count,
        )
        _require_int(
            checkpoint.state_crc32,
            f"{path}.state_crc32",
            0,
            0xFFFFFFFF,
        )
        key = (checkpoint.attempt, checkpoint.completed_ticks)
        if prior is not None and key <= prior:
            _validation_error(
                path, "checkpoints are not strictly chronological and unique"
            )
        prior = key


def validate_replay(replay: Replay) -> None:
    """Fail closed on every semantic and bounded-storage invariant."""

    if not isinstance(replay, Replay):
        _validation_error("replay", "expected immutable Replay")
    _require_enum(replay.mode, ReplayMode, "mode")
    _validate_compatibility_ids(
        replay.engine_compat_id,
        replay.state_schema_id,
        replay.content_id,
        "compatibility",
    )
    _require_int(replay.stage_id, "stage_id", 1, 0xFFFF)
    if replay.stage_id not in KNOWN_STAGE_IDS:
        _validation_error("stage_id", f"unknown stage ID {replay.stage_id}")
    _require_enum(replay.timing_mode, TimingMode, "timing_mode")
    _require_int(
        replay.maximum_active_echoes,
        "maximum_active_echoes",
        1,
        MAX_ACTIVE_ECHOES,
    )
    _require_int(
        replay.initial_echo_count,
        "initial_echo_count",
        0,
        replay.maximum_active_echoes,
    )
    _require_int(
        replay.anchor_capacity,
        "anchor_capacity",
        0,
        MAX_ANCHOR_CAPACITY,
    )
    _require_int(
        replay.initial_persistent_crc32,
        "initial_persistent_crc32",
        0,
        0xFFFFFFFF,
    )
    _require_int(
        replay.expected_final_state_crc32,
        "expected_final_state_crc32",
        0,
        0xFFFFFFFF,
    )
    _require_int(
        replay.expected_completion_signature,
        "expected_completion_signature",
        0,
        0xFFFFFFFF,
    )
    if not isinstance(replay.attempts, tuple):
        _validation_error("attempts", "must be an immutable tuple")
    if not 1 <= len(replay.attempts) <= MAX_ATTEMPTS:
        _validation_error("attempts", f"count must be 1..{MAX_ATTEMPTS}")
    for ordinal, attempt in enumerate(replay.attempts):
        _validate_attempt(attempt, ordinal)
    if not isinstance(replay.timeline, tuple):
        _validation_error("timeline", "must be an immutable tuple")
    if not isinstance(replay.anchors, tuple):
        _validation_error("anchors", "must be an immutable tuple")
    if not isinstance(replay.checkpoints, tuple):
        _validation_error("checkpoints", "must be an immutable tuple")

    if replay.mode is ReplayMode.FULL_HISTORY:
        active_echoes = _validate_full_history(replay)
    else:
        active_echoes = _validate_composition(replay)
    _validate_anchors(replay)
    _validate_checkpoints(replay)
    _validate_outcome(replay, active_echoes)

    chunk_count = (
        len(replay.attempts)
        + len(replay.timeline)
        + len(replay.anchors)
        + len(replay.checkpoints)
        + 1
    )
    if chunk_count > MAX_CHUNKS:
        _validation_error("chunks", f"count exceeds {MAX_CHUNKS}")


def _encode_rle(inputs: bytes) -> bytes:
    if not inputs:
        return b""
    result = bytearray()
    start = 0
    while start < len(inputs):
        value = inputs[start]
        end = start + 1
        while end < len(inputs) and inputs[end] == value and end - start < 0xFFFF:
            end += 1
        result.extend(struct.pack(">HB", end - start, value))
        start = end
    return bytes(result)


def _encode_attempt(attempt: Attempt) -> bytes:
    data = (
        attempt.inputs
        if attempt.encoding is InputEncoding.RAW
        else _encode_rle(attempt.inputs)
    )
    prefix = ATMP_PREFIX_STRUCT.pack(
        attempt.ordinal,
        attempt.frame_count,
        int(attempt.encoding),
        int(attempt.natural_end_reason),
        int(attempt.operation),
        attempt.target_slot,
        attempt.uncompressed_crc32,
    )
    return prefix + data


def _frame_chunk(tag: bytes, payload: bytes) -> bytes:
    if tag not in KNOWN_TAGS:
        raise ReplayValidationError(f"chunk: unknown tag {tag!r}")
    framed = bytearray(
        CHUNK_HEADER_STRUCT.pack(
            tag,
            len(payload),
            zlib.crc32(payload) & 0xFFFFFFFF,
        )
    )
    framed.extend(payload)
    if len(payload) & 1:
        framed.append(0)
    return bytes(framed)


def encode_replay(replay: Replay) -> bytes:
    """Encode an immutable Replay into deterministic R94R v1 bytes."""

    validate_replay(replay)
    chunks: list[bytes] = []
    decisions_by_attempt: dict[int, list[TimelineDecision]] = {}
    for decision in replay.timeline:
        decisions_by_attempt.setdefault(decision.before_attempt, []).append(decision)
    for attempt in replay.attempts:
        for decision in decisions_by_attempt.get(attempt.ordinal, ()):
            chunks.append(
                _frame_chunk(
                    b"TLIN",
                    TLIN_STRUCT.pack(
                        decision.before_attempt,
                        int(decision.operation),
                        decision.target_slot,
                    ),
                )
            )
        chunks.append(_frame_chunk(b"ATMP", _encode_attempt(attempt)))
    for anchor in replay.anchors:
        chunks.append(
            _frame_chunk(
                b"ANCH",
                ANCH_STRUCT.pack(
                    anchor.attempt,
                    anchor.completed_ticks,
                    int(anchor.record_type),
                    anchor.target_id,
                    anchor.state_id,
                    0,
                ),
            )
        )
    for checkpoint in replay.checkpoints:
        chunks.append(
            _frame_chunk(
                b"CKPT",
                CKPT_STRUCT.pack(
                    checkpoint.attempt,
                    checkpoint.completed_ticks,
                    checkpoint.state_crc32,
                ),
            )
        )
    outcome = replay.outcome
    chunks.append(
        _frame_chunk(
            b"OUTC",
            OUTC_STRUCT.pack(
                int(outcome.terminal_reason),
                0,
                outcome.attempts_used,
                outcome.terminal_ticks,
                outcome.active_echoes,
                outcome.anchors_used,
                outcome.score,
                outcome.state_crc32,
                outcome.completion_signature,
            ),
        )
    )
    payload = b"".join(chunks)
    if len(payload) > MAX_PAYLOAD_BYTES:
        _validation_error(
            "payload", f"encoded file would exceed {MAX_FILE_BYTES} bytes"
        )
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    prefix = HEADER_PREFIX_STRUCT.pack(
        MAGIC,
        FORMAT_VERSION,
        HEADER_BYTES,
        int(replay.mode),
        replay.engine_compat_id,
        replay.state_schema_id,
        replay.content_id,
        replay.stage_id,
        int(replay.timing_mode),
        replay.initial_echo_count,
        len(replay.attempts),
        replay.maximum_active_echoes,
        replay.anchor_capacity,
        len(chunks),
        len(replay.checkpoints),
        replay.initial_persistent_crc32,
        replay.expected_final_state_crc32,
        replay.expected_completion_signature,
        len(payload),
        payload_crc,
    )
    if len(prefix) != 60:
        raise AssertionError("internal R94R header prefix size")
    header = prefix + struct.pack(">I", zlib.crc32(prefix) & 0xFFFFFFFF)
    encoded = header + payload
    if len(encoded) > MAX_FILE_BYTES:
        raise AssertionError("bounded replay size check failed")
    return encoded


def _decode_header(data: bytes) -> _Header:
    if len(data) < HEADER_BYTES:
        _integrity_error("header", f"file shorter than {HEADER_BYTES} bytes")
    try:
        (
            magic,
            version,
            header_bytes,
            raw_flags,
            engine_compat_id,
            state_schema_id,
            content_id,
            stage_id,
            raw_timing,
            initial_echo_count,
            attempt_count,
            maximum_active_echoes,
            anchor_capacity,
            chunk_count,
            checkpoint_count,
            initial_persistent_crc32,
            expected_final_state_crc32,
            expected_completion_signature,
            payload_bytes,
            payload_crc32,
            header_crc32,
        ) = HEADER_STRUCT.unpack_from(data, 0)
    except struct.error as exc:
        _integrity_error("header", f"cannot unpack: {exc}")
    if magic != MAGIC:
        _integrity_error("header.magic", "expected R94R")
    if version != FORMAT_VERSION:
        _integrity_error("header.format_version", f"unsupported version {version}")
    if header_bytes != HEADER_BYTES:
        _integrity_error("header.header_bytes", f"expected {HEADER_BYTES}")
    calculated_header_crc = zlib.crc32(data[:60]) & 0xFFFFFFFF
    if header_crc32 != calculated_header_crc:
        _integrity_error("header.header_crc32", "checksum mismatch")
    if len(data) > MAX_FILE_BYTES:
        _integrity_error("file", f"exceeds {MAX_FILE_BYTES}-byte limit")
    if payload_bytes > MAX_PAYLOAD_BYTES:
        _integrity_error("header.payload_bytes", "exceeds bounded payload")
    if HEADER_BYTES + payload_bytes != len(data):
        _integrity_error(
            "header.payload_bytes", "does not match exact remaining file length"
        )
    payload = data[HEADER_BYTES:]
    if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc32:
        _integrity_error("header.payload_crc32", "checksum mismatch")
    try:
        mode = ReplayMode(raw_flags)
    except ValueError:
        _validation_error(
            "header.flags",
            "exactly one known FULL_HISTORY or COMPOSITION flag is required",
        )
    try:
        timing_mode = TimingMode(raw_timing)
    except ValueError:
        _validation_error("header.timing_mode", f"unknown timing ID {raw_timing}")
    _validate_compatibility_ids(
        engine_compat_id,
        state_schema_id,
        content_id,
        "header",
    )
    if stage_id not in KNOWN_STAGE_IDS:
        _validation_error("header.stage_id", f"unknown stage ID {stage_id}")
    _require_int(attempt_count, "header.attempt_count", 1, MAX_ATTEMPTS)
    _require_int(
        maximum_active_echoes,
        "header.maximum_active_echoes",
        1,
        MAX_ACTIVE_ECHOES,
    )
    _require_int(
        initial_echo_count,
        "header.initial_echo_count",
        0,
        maximum_active_echoes,
    )
    _require_int(
        anchor_capacity,
        "header.anchor_capacity",
        0,
        MAX_ANCHOR_CAPACITY,
    )
    _require_int(chunk_count, "header.chunk_count", 2, MAX_CHUNKS)
    _require_int(
        checkpoint_count,
        "header.checkpoint_count",
        0,
        MAX_CHECKPOINTS,
    )
    return _Header(
        mode,
        engine_compat_id,
        state_schema_id,
        content_id,
        stage_id,
        timing_mode,
        initial_echo_count,
        attempt_count,
        maximum_active_echoes,
        anchor_capacity,
        chunk_count,
        checkpoint_count,
        initial_persistent_crc32,
        expected_final_state_crc32,
        expected_completion_signature,
        payload_bytes,
        payload_crc32,
        header_crc32,
    )


def _check_compatibility(
    header: _Header, expected: CompatibilityIds | None
) -> None:
    if expected is None:
        return
    if not isinstance(expected, CompatibilityIds):
        _validation_error("compatibility", "expected CompatibilityIds")
    _validate_compatibility_ids(
        expected.engine_compat_id,
        expected.state_schema_id,
        expected.content_id,
        "expected_compatibility",
    )
    mismatches = []
    if header.engine_compat_id != expected.engine_compat_id:
        mismatches.append("engine_compat_id")
    if header.state_schema_id != expected.state_schema_id:
        mismatches.append("state_schema_id")
    if header.content_id != expected.content_id:
        mismatches.append("content_id")
    if mismatches:
        raise ReplayCompatibilityError(
            "header compatibility mismatch: " + ", ".join(mismatches)
        )


def _frame_chunks(data: bytes, header: _Header) -> tuple[tuple[bytes, bytes], ...]:
    chunks: list[tuple[bytes, bytes]] = []
    cursor = HEADER_BYTES
    for index in range(header.chunk_count):
        if cursor + CHUNK_HEADER_BYTES > len(data):
            _integrity_error(f"chunks[{index}]", "truncated chunk header")
        tag, payload_bytes, payload_crc = CHUNK_HEADER_STRUCT.unpack_from(data, cursor)
        cursor += CHUNK_HEADER_BYTES
        payload_end = cursor + payload_bytes
        if payload_end < cursor or payload_end > len(data):
            _integrity_error(f"chunks[{index}]", "payload outside file")
        payload = data[cursor:payload_end]
        if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
            _integrity_error(f"chunks[{index}].payload_crc32", "checksum mismatch")
        cursor = payload_end
        if payload_bytes & 1:
            if cursor >= len(data):
                _integrity_error(f"chunks[{index}].padding", "missing pad byte")
            if data[cursor] != 0:
                _integrity_error(f"chunks[{index}].padding", "pad byte must be zero")
            cursor += 1
        if tag not in KNOWN_TAGS:
            _validation_error(f"chunks[{index}].tag", f"unknown tag {tag!r}")
        chunks.append((tag, payload))
    if cursor != len(data):
        _integrity_error("chunks", "trailing bytes after declared chunk_count")
    return tuple(chunks)


def _enum_from_byte(
    enum_type: type[IntEnum], value: int, path: str
) -> IntEnum:
    try:
        return enum_type(value)
    except ValueError:
        _validation_error(path, f"unknown {enum_type.__name__} value {value}")


def _decode_attempt(payload: bytes, chunk_index: int) -> Attempt:
    path = f"chunks[{chunk_index}].ATMP"
    if len(payload) < ATMP_PREFIX_STRUCT.size:
        _integrity_error(path, "payload shorter than 12-byte prefix")
    (
        ordinal,
        frame_count,
        raw_encoding,
        raw_end_reason,
        raw_operation,
        target_slot,
        expected_crc,
    ) = ATMP_PREFIX_STRUCT.unpack_from(payload, 0)
    if frame_count > MAX_FRAMES:
        _validation_error(f"{path}.frame_count", f"exceeds {MAX_FRAMES}")
    encoding = _enum_from_byte(InputEncoding, raw_encoding, f"{path}.encoding")
    end_reason = _enum_from_byte(
        NaturalEndReason, raw_end_reason, f"{path}.natural_end_reason"
    )
    operation = _enum_from_byte(
        AttemptOperation, raw_operation, f"{path}.operation"
    )
    encoded = payload[ATMP_PREFIX_STRUCT.size :]
    if encoding is InputEncoding.RAW:
        if len(encoded) != frame_count:
            _integrity_error(path, "raw byte count does not equal frame_count")
        inputs = encoded
    else:
        if len(encoded) % 3:
            _integrity_error(path, "RLE payload has trailing partial run")
        expanded = bytearray()
        previous_value: int | None = None
        for run_index in range(len(encoded) // 3):
            run_length, value = struct.unpack_from(">HB", encoded, run_index * 3)
            if run_length == 0:
                _validation_error(
                    f"{path}.runs[{run_index}]", "zero-length RLE run"
                )
            if value == previous_value:
                _validation_error(
                    f"{path}.runs[{run_index}]",
                    "adjacent equal runs are not canonical",
                )
            if (
                len(expanded) + run_length > frame_count
                or len(expanded) + run_length > MAX_FRAMES
            ):
                _validation_error(
                    f"{path}.runs[{run_index}]",
                    "RLE over-expansion/decompression bomb",
                )
            if value & 0x80:
                _validation_error(
                    f"{path}.runs[{run_index}]",
                    "reserved bit 7 must be zero",
                )
            expanded.extend(bytes((value,)) * run_length)
            previous_value = value
        if len(expanded) != frame_count:
            _integrity_error(path, "RLE expansion does not equal frame_count")
        inputs = bytes(expanded)
    _validate_input_bytes(inputs, f"{path}.inputs")
    if zlib.crc32(inputs) & 0xFFFFFFFF != expected_crc:
        _integrity_error(path, "uncompressed input CRC mismatch")
    return Attempt(
        ordinal,
        bytes(inputs),
        encoding,
        end_reason,
        operation,
        target_slot,
    )


def _decode_typed_chunks(
    chunks: tuple[tuple[bytes, bytes], ...],
    header: _Header,
) -> Replay:
    attempts: list[Attempt] = []
    timeline: list[TimelineDecision] = []
    anchors: list[AnchorCommit] = []
    checkpoints: list[Checkpoint] = []
    outcome: Outcome | None = None
    phase = 0
    for chunk_index, (tag, payload) in enumerate(chunks):
        path = f"chunks[{chunk_index}]"
        if tag == b"TLIN":
            if phase != 0:
                _validation_error(path, "TLIN appears after assertion chunks")
            if len(payload) != TLIN_STRUCT.size:
                _integrity_error(path, "TLIN payload must be exactly 4 bytes")
            before_attempt, raw_operation, target_slot = TLIN_STRUCT.unpack(payload)
            if before_attempt != len(attempts) or before_attempt == 0:
                _validation_error(
                    path, "TLIN must immediately precede its next attempt boundary"
                )
            operation = _enum_from_byte(
                TimelineOperation, raw_operation, f"{path}.operation"
            )
            timeline.append(
                TimelineDecision(before_attempt, operation, target_slot)
            )
        elif tag == b"ATMP":
            if phase != 0:
                _validation_error(path, "ATMP appears after assertion chunks")
            attempt = _decode_attempt(payload, chunk_index)
            if attempt.ordinal != len(attempts):
                _validation_error(
                    f"{path}.ordinal",
                    "attempt ordinals must be zero-based and chronological",
                )
            if len(attempts) >= header.attempt_count:
                _validation_error(path, "excess ATMP stream")
            attempts.append(attempt)
        elif tag == b"ANCH":
            if len(attempts) != header.attempt_count:
                _validation_error(path, "ANCH appears before all ATMP streams")
            if phase > 1:
                _validation_error(path, "ANCH appears after CKPT/OUTC")
            phase = 1
            if len(payload) != ANCH_STRUCT.size:
                _integrity_error(path, "ANCH payload must be exactly 12 bytes")
            (
                attempt,
                ticks,
                raw_record_type,
                target_id,
                state_id,
                reserved,
            ) = ANCH_STRUCT.unpack(payload)
            if reserved:
                _validation_error(path, "ANCH reserved field must be zero")
            record_type = _enum_from_byte(
                AnchorRecordType, raw_record_type, f"{path}.record_type"
            )
            anchors.append(
                AnchorCommit(attempt, ticks, record_type, target_id, state_id)
            )
        elif tag == b"CKPT":
            if len(attempts) != header.attempt_count:
                _validation_error(path, "CKPT appears before all ATMP streams")
            if phase > 2:
                _validation_error(path, "CKPT appears after OUTC")
            phase = 2
            if len(payload) != CKPT_STRUCT.size:
                _integrity_error(path, "CKPT payload must be exactly 8 bytes")
            attempt, ticks, state_crc = CKPT_STRUCT.unpack(payload)
            checkpoints.append(Checkpoint(attempt, ticks, state_crc))
        else:
            if chunk_index != len(chunks) - 1:
                _validation_error(path, "OUTC must be the final chunk")
            if outcome is not None:
                _validation_error(path, "duplicate OUTC chunk")
            phase = 3
            if len(payload) != OUTC_STRUCT.size:
                _integrity_error(path, "OUTC payload must be exactly 20 bytes")
            (
                raw_reason,
                reserved,
                attempts_used,
                terminal_ticks,
                active_echoes,
                anchors_used,
                score,
                state_crc,
                signature,
            ) = OUTC_STRUCT.unpack(payload)
            if reserved:
                _validation_error(path, "OUTC reserved field must be zero")
            reason = _enum_from_byte(
                NaturalEndReason, raw_reason, f"{path}.terminal_reason"
            )
            outcome = Outcome(
                reason,
                attempts_used,
                terminal_ticks,
                active_echoes,
                anchors_used,
                score,
                state_crc,
                signature,
            )
    if len(attempts) != header.attempt_count:
        _validation_error(
            "chunks.ATMP",
            f"header declares {header.attempt_count}, decoded {len(attempts)}",
        )
    if len(checkpoints) != header.checkpoint_count:
        _validation_error(
            "chunks.CKPT",
            f"header declares {header.checkpoint_count}, decoded {len(checkpoints)}",
        )
    if outcome is None:
        _validation_error("chunks.OUTC", "exactly one final OUTC is required")
    replay = Replay(
        header.mode,
        header.engine_compat_id,
        header.state_schema_id,
        header.content_id,
        header.stage_id,
        header.timing_mode,
        header.initial_echo_count,
        header.maximum_active_echoes,
        header.anchor_capacity,
        header.initial_persistent_crc32,
        header.expected_final_state_crc32,
        header.expected_completion_signature,
        tuple(attempts),
        tuple(timeline),
        tuple(anchors),
        tuple(checkpoints),
        outcome,
    )
    validate_replay(replay)
    return replay


def decode_replay(
    data: bytes | bytearray | memoryview,
    expected_compatibility: CompatibilityIds | None = None,
) -> Replay:
    """Decode only after header, payload, and every chunk CRC validate.

    The returned object graph is frozen and contains only tuples and immutable
    bytes.  No partially decoded mutable structure escapes on failure.
    """

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ReplayIntegrityError("file: expected a bytes-like object")
    if len(data) > MAX_FILE_BYTES:
        _integrity_error("file", f"exceeds {MAX_FILE_BYTES}-byte limit")
    immutable = bytes(data)
    header = _decode_header(immutable)
    _check_compatibility(header, expected_compatibility)
    chunks = _frame_chunks(immutable, header)
    return _decode_typed_chunks(chunks, header)


def canonicalize_replay(replay: Replay, encoding: str) -> Replay:
    """Return a new replay with deterministic raw, RLE, or smallest encoding."""

    validate_replay(replay)
    if encoding not in {"raw", "rle", "auto"}:
        _validation_error("encoding", "expected raw, rle, or auto")
    attempts = []
    for attempt in replay.attempts:
        if encoding == "raw":
            selected = InputEncoding.RAW
        elif encoding == "rle":
            selected = InputEncoding.RLE
        else:
            rle_bytes = _encode_rle(attempt.inputs)
            selected = (
                InputEncoding.RLE
                if len(rle_bytes) < len(attempt.inputs)
                else InputEncoding.RAW
            )
        attempts.append(replace(attempt, encoding=selected))
    canonical = replace(replay, attempts=tuple(attempts))
    validate_replay(canonical)
    return canonical


def replay_report(
    replay: Replay,
    encoded: bytes | None = None,
) -> dict[str, Any]:
    """Return a deterministic JSON-serializable inspection report."""

    validate_replay(replay)
    report: dict[str, Any] = {
        "schema": "recursion94.replay_report",
        "schema_version": 1,
        "status": "pass",
        "format": {
            "magic": "R94R",
            "version": FORMAT_VERSION,
            "byte_order": "big",
            "header_bytes": HEADER_BYTES,
            "maximum_file_bytes": MAX_FILE_BYTES,
        },
        "mode": replay.mode.name,
        "compatibility": {
            "engine_compat_id": f"{replay.engine_compat_id:08x}",
            "state_schema_id": f"{replay.state_schema_id:08x}",
            "content_id": f"{replay.content_id:016x}",
        },
        "stage_id": replay.stage_id,
        "timing_mode": replay.timing_mode.name,
        "initial_echo_count": replay.initial_echo_count,
        "maximum_active_echoes": replay.maximum_active_echoes,
        "anchor_capacity": replay.anchor_capacity,
        "initial_persistent_crc32": f"{replay.initial_persistent_crc32:08x}",
        "expected_final_state_crc32": f"{replay.expected_final_state_crc32:08x}",
        "expected_completion_signature": (
            f"{replay.expected_completion_signature:08x}"
        ),
        "attempts": [
            {
                "ordinal": attempt.ordinal,
                "frames": attempt.frame_count,
                "encoding": attempt.encoding.name,
                "natural_end_reason": attempt.natural_end_reason.name,
                "operation": attempt.operation.name,
                "target_slot": (
                    None if attempt.target_slot == NO_TARGET else attempt.target_slot
                ),
                "uncompressed_crc32": f"{attempt.uncompressed_crc32:08x}",
            }
            for attempt in replay.attempts
        ],
        "timeline": [
            {
                "before_attempt": decision.before_attempt,
                "operation": decision.operation.name,
                "target_slot": decision.target_slot,
            }
            for decision in replay.timeline
        ],
        "anchors": [
            {
                "attempt": anchor.attempt,
                "completed_ticks": anchor.completed_ticks,
                "record_type": anchor.record_type.name,
                "target_id": anchor.target_id,
                "state_id": anchor.state_id,
            }
            for anchor in replay.anchors
        ],
        "checkpoints": [
            {
                "attempt": checkpoint.attempt,
                "completed_ticks": checkpoint.completed_ticks,
                "state_crc32": f"{checkpoint.state_crc32:08x}",
            }
            for checkpoint in replay.checkpoints
        ],
        "outcome_assertion": {
            "terminal_reason": replay.outcome.terminal_reason.name,
            "attempts_used": replay.outcome.attempts_used,
            "terminal_ticks": replay.outcome.terminal_ticks,
            "active_echoes": replay.outcome.active_echoes,
            "anchors_used": replay.outcome.anchors_used,
            "score": replay.outcome.score,
            "state_crc32": f"{replay.outcome.state_crc32:08x}",
            "completion_signature": (
                f"{replay.outcome.completion_signature:08x}"
            ),
            "assertion_only": True,
            "simulation_performed": False,
        },
        "counts": {
            "attempts": len(replay.attempts),
            "timeline_decisions": len(replay.timeline),
            "anchors": len(replay.anchors),
            "checkpoints": len(replay.checkpoints),
            "chunks": (
                len(replay.attempts)
                + len(replay.timeline)
                + len(replay.anchors)
                + len(replay.checkpoints)
                + 1
            ),
        },
        "fixture_classification": "unverified replay assertions; not a canonical gameplay result",
    }
    if encoded is not None:
        immutable = bytes(encoded)
        report["file"] = {
            "bytes": len(immutable),
            "sha256": hashlib.sha256(immutable).hexdigest(),
            "header_crc32": f"{struct.unpack_from('>I', immutable, 60)[0]:08x}",
            "payload_crc32": f"{struct.unpack_from('>I', immutable, 56)[0]:08x}",
        }
    return report


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _integer_argument(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer {value!r}") from exc


def _add_compatibility_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine-compat-id", type=_integer_argument)
    parser.add_argument("--state-schema-id", type=_integer_argument)
    parser.add_argument("--content-id", type=_integer_argument)


def _compatibility_from_args(
    args: argparse.Namespace, *, required: bool = False
) -> CompatibilityIds | None:
    values = (
        args.engine_compat_id,
        args.state_schema_id,
        args.content_id,
    )
    if all(value is None for value in values):
        if required:
            raise ReplayValidationError(
                "compatibility: validate requires engine, state schema, "
                "and content IDs"
            )
        return None
    if any(value is None for value in values):
        raise ReplayValidationError(
            "compatibility: provide engine, state schema, and content IDs together"
        )
    expected = CompatibilityIds(*values)
    _validate_compatibility_ids(
        expected.engine_compat_id,
        expected.state_schema_id,
        expected.content_id,
        "compatibility",
    )
    return expected


def _emit_report(report: dict[str, Any], path: Path | None) -> None:
    encoded = _json_bytes(report)
    if path is None:
        sys.stdout.buffer.write(encoded)
    else:
        _write_atomic(path, encoded)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and inspect R94R v1 files")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "inspect"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("input", type=Path)
        subparser.add_argument("--report", type=Path)
        _add_compatibility_arguments(subparser)
    canonicalize = subparsers.add_parser("canonicalize")
    canonicalize.add_argument("input", type=Path)
    canonicalize.add_argument("output", type=Path)
    canonicalize.add_argument(
        "--encoding", choices=("raw", "rle", "auto"), required=True
    )
    canonicalize.add_argument("--report", type=Path)
    _add_compatibility_arguments(canonicalize)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        source = args.input.read_bytes()
        compatibility = _compatibility_from_args(
            args, required=args.command == "validate"
        )
        replay = decode_replay(source, compatibility)
        if args.command == "canonicalize":
            replay = canonicalize_replay(replay, args.encoding)
            encoded = encode_replay(replay)
            _write_atomic(args.output, encoded)
            report = replay_report(replay, encoded)
            report["command"] = "canonicalize"
            report["canonical_encoding"] = args.encoding
        else:
            report = replay_report(replay, source)
            report["command"] = args.command
        _emit_report(report, args.report)
        return 0
    except (OSError, ReplayError) as exc:
        failure = {
            "schema": "recursion94.replay_report",
            "schema_version": 1,
            "status": "fail",
            "error": str(exc),
            "simulation_performed": False,
        }
        sys.stderr.buffer.write(_json_bytes(failure))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
