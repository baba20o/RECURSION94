# R94R replay format, version 1

Files use extension `.r94r`, big-endian integers, even-byte chunk padding, a
64 KiB maximum file size, and at most 64 attempts. Expected results are
assertions; they never force simulation outcomes.

## Input contract

Each authoritative input byte is active-high:

| Bit | Meaning |
|---:|---|
| 0 | Up |
| 1 | Down |
| 2 | Left |
| 3 | Right |
| 4 | A / pulse |
| 5 | B / interact |
| 6 | C / phase |
| 7 | Reserved; must be zero |

Opposite directions sanitize to neutral. Disconnect supplies zero. `pressed` is
`held & ~previous`; `released` is `previous & ~held`. Index zero is the first
pre-step input. Tick 719 is processed before timer expiry. Playback beyond a
partial recording supplies zero and therefore produces defined release edges.
Start/pause/timeline does not consume an authoritative tick.

## Header

| Offset | Field |
|---:|---|
| `$00` | `char[4] "R94R"` |
| `$04` | `u16 format_version = 1` |
| `$06` | `u16 header_bytes = 64` |
| `$08` | `u32 flags` (`FULL_HISTORY` or `COMPOSITION`) |
| `$0C` | `u32 engine_compat_id` |
| `$10` | `u32 state_schema_id` |
| `$14` | `u64 content_id` (truncated manifest SHA-256) |
| `$1C` | `u16 stage_id` |
| `$1E` | `u8 region/timing mode` |
| `$1F` | `u8 initial_echo_count` |
| `$20` | `u16 attempt_count` |
| `$22` | `u8 maximum_active_echoes` |
| `$23` | `u8 anchor_capacity` |
| `$24` | `u16 chunk_count` |
| `$26` | `u16 checkpoint_count` |
| `$28` | `u32 initial_persistent_crc` |
| `$2C` | `u32 expected_final_state_crc` |
| `$30` | `u32 expected_completion_signature` |
| `$34` | `u32 payload_bytes` |
| `$38` | `u32 payload_crc32` |
| `$3C` | `u32 header_crc32` |

Each chunk begins with `char tag[4], u32 payload_bytes, u32 payload_crc32`.

Chunk payloads have these exact v1 shapes:

| Tag | Bytes | Payload |
|---|---:|---|
| `ATMP` | 12 + input data | `u16 ordinal, u16 frames, u8 encoding, u8 natural_end, u8 operation, u8 target, u32 uncompressed_crc`, then raw bytes or RLE records |
| `TLIN` | 4 | `u16 before_attempt, u8 operation, u8 target_slot` |
| `ANCH` | 12 | `u16 attempt, u16 completed_ticks, u16 record_type, u16 target_id, u16 state_id, u16 reserved_zero` |
| `CKPT` | 8 | `u16 attempt, u16 completed_ticks, u32 state_crc` |
| `OUTC` | 20 | `u8 terminal_reason, u8 reserved_zero, u16 attempts_used, u16 terminal_ticks, u8 active_echoes, u8 anchors_used, u32 score, u32 state_crc, u32 completion_signature` |

Mode flags are `FULL_HISTORY = $00000001` and
`COMPOSITION = $00000002`. Timing modes are NTSC 60 Hz = 0 and PAL 50 Hz = 1.
Natural end reasons are timeout 1, destroyed 2, manual abort 3, solved 4, and
phase transition 5. Attempt operations are append 0 and replace 1; append uses
target `$FF`. Timeline operations are protect 0, unprotect 1, and replacement
selection 2. Anchor record types are socket powered 1, terminal repaired 2,
conveyor direction 3, lock destroyed 4, bridge state 5, and route open 6.

Encoding 0 is raw bytes. Encoding 1 is `{u16 run_length, u8 input}` RLE.
Zero-length runs, bit 7, over-expansion, more than 720 frames, bad CRC, trailing
bytes, excess streams/chunks, or incompatible IDs reject before state mutation.

`FULL_HISTORY` begins with no echoes and executes every ordinary commit/reset
for non-solved recordings. Its final solved recording remains the current
incarnation because terminal success does not perform a commit/reset; therefore
that stream is not counted in `OUTC.active_echoes`. `SOLVED` is legal only on
the final attempt, and `OUTC.terminal_reason` must match the final `ATMP`.
Release acceptance replays use this mode. `COMPOSITION` may preload final
streams only for replay theater and capture.

Canonical order is all `TLIN` decisions immediately before their named
attempts, each `ATMP` in chronological order, chronological `ANCH` assertions,
chronological `CKPT` assertions, then exactly one `OUTC`. The payload CRC covers
chunk headers, payloads, and zero padding; each chunk CRC covers its payload;
the header CRC covers bytes `$00`–`$3B`.
