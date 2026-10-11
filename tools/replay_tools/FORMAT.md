# R94R v1 host payload layout

This file makes the typed payload fields named by the frozen
`docs/REPLAY_FORMAT.md` precise for the host codec. It does not alter the
frozen 64-byte header.

All integers are unsigned big-endian. A chunk is:

| Offset | Width | Field |
|---:|---:|---|
| 0 | 4 | tag |
| 4 | 4 | payload byte count |
| 8 | 4 | payload CRC-32 |
| 12 | variable | payload |
| next | 0 or 1 | zero pad to an even byte boundary |

The header payload CRC covers every chunk header, payload, and pad byte. A
chunk CRC covers its typed payload only. The header CRC covers bytes `$00`
through `$3B`.

## `ATMP`

The fixed prefix is 12 bytes:

| Offset | Width | Field |
|---:|---:|---|
| 0 | 2 | zero-based attempt ordinal |
| 2 | 2 | uncompressed frame count, 0–720 |
| 4 | 1 | encoding: 0 raw, 1 RLE |
| 5 | 1 | expected natural end reason |
| 6 | 1 | commit operation: 0 append, 1 replace |
| 7 | 1 | target slot; append requires `$FF` |
| 8 | 4 | CRC-32 of uncompressed recorded inputs |
| 12 | variable | raw bytes or `{u16 run, u8 input}` records |

RLE runs are nonzero, maximal, and expand to exactly the frame count. Expansion
is bounded before allocation. Bit 7 is invalid. Opposite axes remain present
in the recorded byte and sanitize only through the explicit simulation helper.

## `TLIN`

Exactly 4 bytes: `{u16 before_attempt, u8 operation, u8 target_slot}`.
Operations are protect 0, unprotect 1, and replacement selection 2. TLIN chunks
immediately precede the named next attempt. A replacement ATMP must match an
explicit unprotected selection. Only one slot may be protected.

## `ANCH`

Exactly 12 bytes:
`{u16 attempt, u16 completed_ticks, u16 record_type, u16 target_id,
u16 state_id, u16 reserved_zero}`.

Record types are socket-powered 1, terminal-repaired 2, conveyor-direction 3,
lock-destroyed 4, bridge-state 5, and route-open 6. Commits are chronological,
unique by typed target, and bounded by header anchor capacity.

## `CKPT`

Exactly the frozen 8-byte shape:
`{u16 attempt, u16 completed_ticks, u32 state_crc}`.

## `OUTC`

Exactly 20 bytes:
`{u8 terminal_reason, u8 reserved_zero, u16 attempts_used,
u16 terminal_ticks, u8 active_echoes, u8 anchors_used, u32 score,
u32 state_crc, u32 completion_signature}`.

There is exactly one final OUTC. Its CRC and signature repeat the header
assertions; neither value is treated as a simulated observation.

## Canonical chunk order and modes

History is emitted as TLIN decisions followed by each chronological ATMP.
Chronological ANCH assertions follow all attempts, then chronological CKPT
assertions, then one OUTC.

`FULL_HISTORY` starts with no echoes and validates every append/replacement
against a bounded timeline. Every non-solved attempt applies its declared
operation at the ordinary commit/reset boundary, including a non-solved final
record. A final `SOLVED` recording remains the current incarnation: its
operation and target still receive structural/timeline validation, but
terminal success does not perform the ordinary commit/reset. In particular, a
final APPEND/`$FF` stream is not added to `OUTC.active_echoes`.
`OUTC.terminal_reason` must equal the final ATMP natural end reason, and
`SOLVED` may occur only on that final attempt.

`COMPOSITION` has no TLIN chunks, contains one REPLACE stream for each
preloaded echo slot, and ends with one APPEND/`$FF` current stream.
Compatibility IDs are nonzero and may be required to equal a caller-supplied
engine/state/content triple. The `validate` CLI requires that triple; `inspect`
may omit it when examining a structurally valid unknown build.
