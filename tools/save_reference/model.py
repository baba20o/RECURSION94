"""Byte-exact model of the cartridge's two-record odd-lane SRAM journal."""

from __future__ import annotations

import dataclasses
import struct
import zlib
from dataclasses import dataclass


MAGIC = b"R94S"
FORMAT_VERSION = 1
SRAM_SLOT_A = 0x00200001
SRAM_SLOT_B = 0x00200201
SRAM_ADDRESS_STRIDE = 2
RECORD_BYTES = 256
HEADER_BYTES = 32
TRAILER_BYTES = 8
PAYLOAD_BYTES = RECORD_BYTES - HEADER_BYTES - TRAILER_BYTES
CRC_OFFSET = RECORD_BYTES - TRAILER_BYTES
COMMIT_OFFSET = RECORD_BYTES - 4
ENCOUNTER_COUNT = 20
STAGE_METRIC_BYTES = 8
COMMIT_MARKER = 0xA55A94C3

FLAG_VALID = 0x0001
SETTING_MUSIC = 0x01
SETTING_SFX = 0x02
SETTING_RESET_FLASH = 0x04
DEFAULT_SETTINGS = SETTING_MUSIC | SETTING_SFX | SETTING_RESET_FLASH


class SaveFormatError(ValueError):
    """A record or decoded payload violates the frozen save ABI."""


@dataclass(frozen=True)
class StageMetric:
    cleared: bool = False
    rank: int = 0
    attempts: int = 0xFFFF
    echoes: int = 0xFF
    time_remaining: int = 0

    def validate(self) -> None:
        if not 0 <= self.rank <= 3:
            raise SaveFormatError("stage rank must be 0..3")
        if not 0 <= self.attempts <= 0xFFFF:
            raise SaveFormatError("stage attempts must fit u16")
        if not 0 <= self.echoes <= 0xFF:
            raise SaveFormatError("stage echo count must fit u8")
        if not 0 <= self.time_remaining <= 720:
            raise SaveFormatError("stage time remaining must be 0..720")
        if not self.cleared and (
            self.rank != 0
            or self.attempts != 0xFFFF
            or self.echoes != 0xFF
            or self.time_remaining != 0
        ):
            raise SaveFormatError("uncleared stage metrics must use canonical sentinels")
        if self.cleared and self.rank == 0:
            raise SaveFormatError("cleared stage must have a completion rank")
        if self.cleared and not 1 <= self.attempts < 0xFFFF:
            raise SaveFormatError("cleared stage attempts must be 1..65534")
        if self.cleared and self.echoes > 6:
            raise SaveFormatError("cleared stage echo count exceeds cartridge capacity")

    def encode(self) -> bytes:
        self.validate()
        return struct.pack(
            ">BBHBBH",
            1 if self.cleared else 0,
            self.rank,
            self.attempts,
            self.echoes,
            0,
            self.time_remaining,
        )

    @classmethod
    def decode(cls, data: bytes) -> "StageMetric":
        if len(data) != STAGE_METRIC_BYTES:
            raise SaveFormatError("stage metric has the wrong length")
        cleared, rank, attempts, echoes, reserved, remaining = struct.unpack(
            ">BBHBBH", data
        )
        if reserved != 0:
            raise SaveFormatError("stage metric reserved byte is nonzero")
        if cleared not in (0, 1):
            raise SaveFormatError("stage cleared flag is not canonical")
        metric = cls(bool(cleared), rank, attempts, echoes, remaining)
        metric.validate()
        return metric


@dataclass(frozen=True)
class SavePayload:
    unlocked_sector: int = 1
    settings: int = DEFAULT_SETTINGS
    completion_bits: int = 0
    narrative_bits: int = 0
    total_clears: int = 0
    metrics: tuple[StageMetric, ...] = dataclasses.field(
        default_factory=lambda: tuple(StageMetric() for _ in range(ENCOUNTER_COUNT))
    )

    def validate(self) -> None:
        if not 1 <= self.unlocked_sector <= 5:
            raise SaveFormatError("unlocked sector must be 1..5")
        if self.settings & ~0x07:
            raise SaveFormatError("settings contain undefined bits")
        if self.completion_bits & ~((1 << ENCOUNTER_COUNT) - 1):
            raise SaveFormatError("completion bitmap contains undefined bits")
        if not 0 <= self.narrative_bits <= 0xFFFFFFFF:
            raise SaveFormatError("narrative bitmap must fit u32")
        if not 0 <= self.total_clears <= ENCOUNTER_COUNT:
            raise SaveFormatError("total clears is outside campaign bounds")
        if len(self.metrics) != ENCOUNTER_COUNT:
            raise SaveFormatError("save must contain exactly twenty metrics")
        metric_bits = 0
        for index, metric in enumerate(self.metrics):
            metric.validate()
            if metric.cleared:
                metric_bits |= 1 << index
        if metric_bits != self.completion_bits:
            raise SaveFormatError("metric clear flags disagree with completion bitmap")
        if metric_bits.bit_count() != self.total_clears:
            raise SaveFormatError("total clear count disagrees with completion bitmap")

    def encode(self) -> bytes:
        self.validate()
        output = bytearray(PAYLOAD_BYTES)
        struct.pack_into(
            ">HBBIIHH",
            output,
            0,
            FORMAT_VERSION,
            self.unlocked_sector,
            self.settings,
            self.completion_bits,
            self.narrative_bits,
            self.total_clears,
            0,
        )
        cursor = 16
        for metric in self.metrics:
            output[cursor : cursor + STAGE_METRIC_BYTES] = metric.encode()
            cursor += STAGE_METRIC_BYTES
        if cursor != 176:
            raise AssertionError("save metric layout drift")
        return bytes(output)

    @classmethod
    def decode(cls, data: bytes) -> "SavePayload":
        if len(data) != PAYLOAD_BYTES:
            raise SaveFormatError("save payload has the wrong length")
        (
            version,
            sector,
            settings,
            completion,
            narrative,
            total,
            reserved,
        ) = struct.unpack_from(">HBBIIHH", data, 0)
        if version != FORMAT_VERSION:
            raise SaveFormatError("payload version mismatch")
        if reserved != 0 or any(data[176:]):
            raise SaveFormatError("payload reserved bytes are nonzero")
        metrics = tuple(
            StageMetric.decode(
                data[
                    16 + index * STAGE_METRIC_BYTES :
                    16 + (index + 1) * STAGE_METRIC_BYTES
                ]
            )
            for index in range(ENCOUNTER_COUNT)
        )
        payload = cls(sector, settings, completion, narrative, total, metrics)
        payload.validate()
        return payload

    def with_clear(
        self,
        encounter_index: int,
        *,
        rank: int,
        attempts: int,
        echoes: int,
        time_remaining: int,
    ) -> "SavePayload":
        if not 0 <= encounter_index < ENCOUNTER_COUNT:
            raise SaveFormatError("encounter index is outside campaign")
        candidate = StageMetric(True, rank, attempts, echoes, time_remaining)
        candidate.validate()
        metrics = list(self.metrics)
        previous = metrics[encounter_index]
        if previous.cleared:
            previous_score = (
                previous.rank,
                -previous.attempts,
                -previous.echoes,
                previous.time_remaining,
            )
            candidate_score = (
                candidate.rank,
                -candidate.attempts,
                -candidate.echoes,
                candidate.time_remaining,
            )
            if previous_score >= candidate_score:
                candidate = previous
        metrics[encounter_index] = candidate
        completion = self.completion_bits | (1 << encounter_index)
        total = completion.bit_count()
        unlocked_sector = min(5, max(self.unlocked_sector, encounter_index // 4 + 1))
        if encounter_index % 4 == 3:
            unlocked_sector = min(5, unlocked_sector + 1)
        updated = dataclasses.replace(
            self,
            unlocked_sector=unlocked_sector,
            completion_bits=completion,
            total_clears=total,
            metrics=tuple(metrics),
        )
        updated.validate()
        return updated


@dataclass(frozen=True)
class DecodedRecord:
    generation: int
    flags: int
    payload: SavePayload


def encode_record(payload: SavePayload, generation: int) -> bytes:
    payload_bytes = payload.encode()
    if not 0 <= generation <= 0xFFFFFFFF:
        raise SaveFormatError("save generation must fit u32")
    output = bytearray(RECORD_BYTES)
    struct.pack_into(
        ">4sHHIHH",
        output,
        0,
        MAGIC,
        FORMAT_VERSION,
        RECORD_BYTES,
        generation,
        PAYLOAD_BYTES,
        FLAG_VALID,
    )
    output[HEADER_BYTES:CRC_OFFSET] = payload_bytes
    checksum = zlib.crc32(output[:CRC_OFFSET]) & 0xFFFFFFFF
    struct.pack_into(">II", output, CRC_OFFSET, checksum, COMMIT_MARKER)
    return bytes(output)


def decode_record(data: bytes) -> DecodedRecord:
    if len(data) != RECORD_BYTES:
        raise SaveFormatError("save record has the wrong length")
    magic, version, record_bytes, generation, payload_bytes, flags = struct.unpack_from(
        ">4sHHIHH", data, 0
    )
    checksum, commit = struct.unpack_from(">II", data, CRC_OFFSET)
    if magic != MAGIC:
        raise SaveFormatError("save signature mismatch")
    if version != FORMAT_VERSION:
        raise SaveFormatError("record version mismatch")
    if record_bytes != RECORD_BYTES or payload_bytes != PAYLOAD_BYTES:
        raise SaveFormatError("record size contract mismatch")
    if flags != FLAG_VALID:
        raise SaveFormatError("record flags are not canonical")
    if commit != COMMIT_MARKER:
        raise SaveFormatError("save commit marker is absent")
    if any(data[16:HEADER_BYTES]):
        raise SaveFormatError("record reserved bytes are nonzero")
    payload_data = data[HEADER_BYTES:CRC_OFFSET]
    actual = zlib.crc32(data[:CRC_OFFSET]) & 0xFFFFFFFF
    if checksum != actual:
        raise SaveFormatError("save payload CRC mismatch")
    return DecodedRecord(generation, flags, SavePayload.decode(payload_data))


def commit_write_plan(record: bytes) -> tuple[tuple[int, int], ...]:
    """Return the exact atomic byte-write order for one encoded record."""
    if len(record) != RECORD_BYTES:
        raise SaveFormatError("commit write plan requires one complete record")
    marker = struct.pack(">I", COMMIT_MARKER)
    if record[COMMIT_OFFSET:] != marker:
        raise SaveFormatError("commit write plan requires the canonical marker")
    return (
        tuple((COMMIT_OFFSET + offset, 0) for offset in range(4))
        + tuple(enumerate(record[:COMMIT_OFFSET]))
        + tuple(
            (COMMIT_OFFSET + offset, value)
            for offset, value in enumerate(marker)
        )
    )


def _newer(left: int, right: int) -> bool:
    difference = (left - right) & 0xFFFFFFFF
    return 0 < difference < 0x80000000


class SaveJournal:
    """Two physical records with commit-last fault injection."""

    def __init__(self, slots: tuple[bytes, bytes] | None = None) -> None:
        initial = bytes([0xFF]) * RECORD_BYTES
        source = slots if slots is not None else (initial, initial)
        if len(source) != 2 or any(len(slot) != RECORD_BYTES for slot in source):
            raise SaveFormatError("journal requires two 256-byte records")
        self.slots = [bytearray(source[0]), bytearray(source[1])]
        self.active_slot: int | None = None
        self.generation = 0
        self.payload = SavePayload()
        self.recovered_defaults = True
        self.load()

    def load(self) -> SavePayload:
        valid: list[tuple[int, DecodedRecord]] = []
        for index, slot in enumerate(self.slots):
            try:
                valid.append((index, decode_record(bytes(slot))))
            except SaveFormatError:
                continue
        if not valid:
            self.active_slot = None
            self.generation = 0
            self.payload = SavePayload()
            self.recovered_defaults = True
            return self.payload
        selected_index, selected = valid[0]
        for index, record in valid[1:]:
            if _newer(record.generation, selected.generation):
                selected_index, selected = index, record
        self.active_slot = selected_index
        self.generation = selected.generation
        self.payload = selected.payload
        self.recovered_defaults = False
        return self.payload

    def commit(
        self,
        payload: SavePayload,
        *,
        fault_after_bytes: int | None = None,
    ) -> bool:
        next_generation = (self.generation + 1) & 0xFFFFFFFF
        target = 0 if self.active_slot != 0 else 1
        record = encode_record(payload, next_generation)
        writes = 0
        for index, value in commit_write_plan(record):
            if fault_after_bytes is not None and writes >= fault_after_bytes:
                self.load()
                return False
            self.slots[target][index] = value
            writes += 1
        self.load()
        if self.active_slot != target or self.generation != next_generation:
            raise AssertionError("committed journal record did not validate")
        return True

    def image(self) -> tuple[bytes, bytes]:
        return bytes(self.slots[0]), bytes(self.slots[1])
