# Save journal format

Integration status: the save translation unit is currently standalone and
unwired from startup and the main game. This format and its host model do not
claim SRAM read/write proof in an emulator or on cartridge hardware.

The cartridge exposes 8 KiB of byte-wide SRAM on the odd address lane
`$200001-$203FFF`. RECURSION 94 uses two alternating 256-byte logical records:

| Logical record | 68000 address range | Physical stride |
|---|---|---|
| A | `$200001-$2001FF` | one byte every two addresses |
| B | `$200201-$2003FF` | one byte every two addresses |

Each record is big-endian:

| Offset | Bytes | Field |
|---:|---:|---|
| `$00` | 4 | `R94S` |
| `$04` | 2 | format version (`1`) |
| `$06` | 2 | record bytes (`256`) |
| `$08` | 4 | wrapping generation |
| `$0C` | 2 | payload bytes (`216`) |
| `$0E` | 2 | canonical flags (`$0001`) |
| `$10` | 16 | zero reserved header |
| `$20` | 216 | campaign payload |
| `$F8` | 4 | CRC-32/ISO-HDLC over bytes `$00-$F7` |
| `$FC` | 4 | commit marker `$A55A94C3` |

The writer chooses the inactive record, physically writes a zero commit marker
before any body byte, writes logical bytes `$00-$FB` in ascending order, and
writes the four-byte commit marker last. Loading validates both records and
selects the newer valid wrapping generation. A torn or corrupted newest record
therefore falls back to the previous committed generation. If neither record
validates, `SaveLoad` constructs canonical defaults without crashing.

For two valid records, B is newer than A exactly when
`0 < ((B - A) & $FFFFFFFF) < $80000000`. Equal generations and the ambiguous
half-range difference select A.

The payload layout uses absolute record offsets:

| Offset | Bytes | Field and bounds |
|---:|---:|---|
| `$20` | 2 | payload version (`1`) |
| `$22` | 1 | unlocked sector (`1..5`) |
| `$23` | 1 | settings (defined mask `$07`) |
| `$24` | 4 | completion bitmap (low 20 bits only) |
| `$28` | 4 | narrative flags |
| `$2C` | 2 | clear count (`0..20`) |
| `$2E` | 2 | zero reserved |
| `$30` | 160 | twenty fixed eight-byte best-result metrics |
| `$D0` | 40 | zero reserved tail through `$F7` |

Each metric is `{cleared:u8, rank:u8, attempts:u16, echoes:u8, reserved:u8,
time_remaining:u16}`. An uncleared metric is exactly
`{0,0,$FFFF,$FF,0,0}`. A cleared metric uses `cleared=1`, rank `1..3`,
attempts `1..$FFFE`, echoes `0..6`, time remaining `0..720`, and a zero
reserved byte. Metric clear flags must exactly reproduce both the 20-bit
completion bitmap and clear count. All unused bytes are zero and validated so
future migrations cannot accidentally reinterpret garbage.
