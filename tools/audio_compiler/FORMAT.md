# Audio scene metadata format

The project-local command
`.\.toolcache\python\python.exe .\tools\audio_compiler\__main__.py` validates
the campaign’s audio-facing declarations and the original score source at
`assets/source/music/audio_score_v2.json`, then emits deterministic JSON
metadata. On a normal Python installation, `python -m tools.audio_compiler` is
equivalent. The tool does not modify or link the ROM.

The generated directory contains:

- `audio_scene_registry.json`: scene IDs, encounter mappings, sector
  tempo/echo caps, voice ownership, and the four-byte desired-state contract.
- `audio_bank_manifest.json`: logical bank IDs, score hashes and row metrics,
  the proposed 8 KiB Z80 RAM partition, and hard size/write/note-lifetime
  budgets.
- `audio_registry.sha256`: SHA-256 values for the two JSON files.

JSON is UTF-8, LF-terminated, key-sorted, and indented by two spaces. Reordering
source object keys does not affect output. `--check` reconstructs all files and
requires exact byte identity.

## Stable scene IDs

Scene 0 is `NONE`. IDs 1–18 are the 18 manifest `music_id` values in first
campaign appearance order. IDs 19–34 are the 16 boss phases in campaign/phase
order. IDs 35–37 are `RESULT_STABLE`, `RESULT_OPTIMIZED`, and
`RESULT_TRANSCENDENT`. IDs 38–39 are `PROGRAM_STABLE` and
`SEED_TRANSMITTED`. The wire contract reserves IDs 0–63.

SFX IDs 1–15 cover shot, dash/phase, interact, door, switch, pickup/drop,
damage, death, reset, echo materialization, anchor, alert, boss phase, Program
Stable, and Seed Transmitted. Each entry freezes its priority and merge policy;
ID 0 is `NONE`.

## Desired-state command

One ordinary command is four bytes:

```text
opcode, scene_id, layer_mask_bits_0_6 | muted_bit_7, generation
```

Only one generation may be in flight. Acceptance acknowledgement and musical
application are distinct. Layer removals and mute are immediate; acquisitions
and scene changes use the scene’s declared 24-PPQN boundary. Muted sequencing
continues without audible writes so gameplay remains independent of sound.

## Banks and note safety

There are 18 encounter score banks plus shared result and final banks. Only one
score bank may occupy `$1200-$19FF`. The v2 tracker format deliberately uses
only 128 bytes:

```text
offset  bytes  meaning
$00       4   ASCII "R94A"
$04       1   format version (1)
$05       1   bank ID (1..20)
$06       1   tempo in BPM
$07       1   maximum echo layer (2..6)
$08       1   rows (16)
$09       1   PPQN ticks per row (6)
$0A       1   layer count (7)
$0B       1   bank flags (theme/result/final)
$0C       4   little-endian CRC-32/ISO-HDLC of payload
$10     112   16 row-major records of seven note bytes
```

Each note byte is `0` for rest or `1..12` for C through B. Every row begins by
keying off the five music FM voices and muting the two owned PSG voices, so a
note lasts at most six PPQN ticks. The compiler rejects notes outside a bank’s
echo cap, empty enabled layers, duplicate bank payloads, invalid tracker
characters, descriptor drift, and rows that exceed the YM2612/PSG write
budgets.

The score source stores every layer as four four-character chunks. Its note
alphabet is `.123456789ABC`, where `.` is rest. This is an original project
asset and declares that it uses no external samples.

`build_bank_binaries()` exposes the 20 exact bank images for standalone tests.
The normal compiler intentionally does not write them into the runtime asset
tree while v2 remains unwired; the manifest names their future generated
locations and reports their SHA-256 and CRC-32 values. Disable, scene
transition, pause, stop, reset, mute, invalid-bank, and timeout paths still
require bounded all-notes-off behavior. Host validation does not substitute
for later live ROM capture and listening-review evidence.

Scene quantization remains a producer contract. Encounter-track changes are
requested on 96-tick bar boundaries, while boss phases and layer acquisitions
use six-tick row boundaries. The compact Z80 v2 decoder applies a state already
published by the producer on its next row. Boss, result, and final scene IDs
that share a bank use registry-declared bounded start-row offsets, so their
first cues remain distinct without another active-bank allocation.
