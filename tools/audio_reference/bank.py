"""Pure decoder and bounded row trace for the compact v2 score-bank format."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


BANK_MAGIC = b"R94A"
BANK_VERSION = 1
BANK_BYTES = 128
DESCRIPTOR_BYTES = 16
ROWS = 16
TICKS_PER_ROW = 6
LAYERS = 7
PAYLOAD_BYTES = ROWS * LAYERS
MAX_NOTE = 12
YM_LAYER_BITS = frozenset((0, 2, 3, 4, 6))
PERCUSSION_LAYER_BIT = 1
TEXTURE_LAYER_BIT = 5


@dataclass(frozen=True)
class RowTrace:
    """One constant-size sequencer row after fixed voice key-offs."""

    row: int
    notes: tuple[int, ...]
    note_events: int
    ym_writes: int
    psg_writes: int


@dataclass(frozen=True)
class CompiledBank:
    """A structurally and semantically validated v2 bank image."""

    bank_id: int
    tempo_bpm: int
    echo_cap: int
    flags: int
    crc32: int
    payload: bytes

    @classmethod
    def parse(cls, image: bytes) -> CompiledBank:
        """Decode an exact image and reject every non-canonical field."""

        if not isinstance(image, bytes):
            raise ValueError("bank image must be immutable bytes")
        if len(image) != BANK_BYTES:
            raise ValueError(f"bank image must contain exactly {BANK_BYTES} bytes")
        if image[:4] != BANK_MAGIC:
            raise ValueError("bank magic mismatch")
        if image[4] != BANK_VERSION:
            raise ValueError("bank version mismatch")
        bank_id = image[5]
        tempo_bpm = image[6]
        echo_cap = image[7]
        if not 1 <= bank_id <= 20:
            raise ValueError("bank ID must be within 1..20")
        if not 1 <= tempo_bpm <= 160:
            raise ValueError("bank tempo must be within 1..160 BPM")
        if not 0 <= echo_cap < LAYERS:
            raise ValueError("bank echo cap must be within 0..6")
        if image[8:11] != bytes((ROWS, TICKS_PER_ROW, LAYERS)):
            raise ValueError("bank row geometry mismatch")
        flags = image[11]
        if flags not in (0, 1, 2):
            raise ValueError("bank flags are not canonical")
        crc32 = struct.unpack_from("<I", image, 12)[0]
        payload = image[DESCRIPTOR_BYTES:]
        if len(payload) != PAYLOAD_BYTES:
            raise ValueError("bank payload size mismatch")
        if zlib.crc32(payload) & 0xFFFFFFFF != crc32:
            raise ValueError("bank payload CRC-32 mismatch")
        if any(note > MAX_NOTE for note in payload):
            raise ValueError("bank contains an invalid note value")

        for layer in range(LAYERS):
            notes = payload[layer::LAYERS]
            if layer <= echo_cap and not any(notes):
                raise ValueError(f"enabled layer {layer} is empty")
            if layer > echo_cap and any(notes):
                raise ValueError(f"disabled layer {layer} contains notes")
        return cls(bank_id, tempo_bpm, echo_cap, flags, crc32, payload)

    def row_notes(self, row: int) -> tuple[int, ...]:
        """Return the seven immutable note values for one bounded row."""

        if isinstance(row, bool) or not isinstance(row, int) or not 0 <= row < ROWS:
            raise ValueError(f"row must be within 0..{ROWS - 1}")
        start = row * LAYERS
        return tuple(self.payload[start : start + LAYERS])

    def trace_row(self, row: int, layer_mask: int = 0x7F) -> RowTrace:
        """Apply the authoritative layer mask and count worst-case chip writes."""

        if (
            isinstance(layer_mask, bool)
            or not isinstance(layer_mask, int)
            or not 0 <= layer_mask <= 0x7F
        ):
            raise ValueError("layer mask must be an unsigned seven-bit value")
        cap_mask = (1 << (self.echo_cap + 1)) - 1
        active_mask = layer_mask & cap_mask
        source = self.row_notes(row)
        notes = tuple(
            note if active_mask & (1 << layer) else 0
            for layer, note in enumerate(source)
        )
        ym_notes = sum(
            notes[layer] != 0 for layer in YM_LAYER_BITS
        )
        percussion = notes[PERCUSSION_LAYER_BIT] != 0
        texture = notes[TEXTURE_LAYER_BIT] != 0
        return RowTrace(
            row=row,
            notes=notes,
            note_events=sum(note != 0 for note in notes),
            ym_writes=5 + (3 * ym_notes),
            psg_writes=2 + (2 if percussion else 0) + (3 if texture else 0),
        )

    def trace_pattern(self, layer_mask: int = 0x7F) -> tuple[RowTrace, ...]:
        """Trace exactly one 16-row loop."""

        return tuple(self.trace_row(row, layer_mask) for row in range(ROWS))

