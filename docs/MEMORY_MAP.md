# Memory map

## 68000 address space

| Range | Size | Owner / lifetime | Alignment / overflow check |
|---|---:|---|---|
| `$000000-$0FFFFF` | 1 MiB | Padded cartridge ROM | Header end and build size assertion |
| `$200001-$203FFF` odd bytes | 8 KiB logical | Battery SRAM save slots | Header metadata, signature/version/CRC |
| `$A00000-$A01FFF` | 8 KiB | Z80 RAM and mailbox | Driver-size and mailbox assertions |
| `$A10000-$A1001F` | — | I/O/controller/version registers | Hardware routines only |
| `$A11100/$A11200` | — | Z80 bus request/reset | Timeout-guarded routines only |
| `$C00000/$C00004` | — | VDP data/control | Hardware layer only |
| `$FF0000-$FF13FF` | 5 KiB | Seven 720-byte replay banks + 80-byte descriptors | Exact compile-time bound |
| `$FF1400-$FF17FF` | 1 KiB | Timeline, persistence, anchors, bank ordering | Schema assertion |
| `$FF1800-$FF1FFF` | 2 KiB | Actors, input history, per-tick intents | Actor-layout report |
| `$FF2000-$FF5FFF` | 16 KiB | Encounter pools and mutable level state | Pool/content report |
| `$FF6000-$FF6FFF` | 4 KiB | Packed canonical hash projection | Serialization hard cap |
| `$FF7000-$FF7FFF` | 4 KiB | Level-load/reset staging | Compiled image bound |
| `$FF8000-$FF8FFF` | 4 KiB | Render state and SAT shadow | SAT exact bound + high water |
| `$FF9000-$FF97FF` | 2 KiB | Bounded DMA/VDP command queue | 32-entry hard cap |
| `$FF9800-$FF9FFF` | 2 KiB | Controller, scheduler, sound mailbox shadow | Layout assertion |
| `$FFA000-$FFAFFF` | 4 KiB | Save staging and integrity work | Save schema assertion |
| `$FFB000-$FFCFFF` | 8 KiB | Decode/general bounded scratch | High-water report |
| `$FFD000-$FFDFFF` | 4 KiB | Debug trace and checkpoint work | Ring-size assertion |
| `$FFE000-$FFEFFF` | 4 KiB | Explicit contingency/canaries | Must remain separately reported |
| `$FFF000-$FFF3FF` | 1 KiB | Stable emulator-visible debug ABI | Version/size/seqlock |
| `$FFF400-$FFFFFF` | 3 KiB | Supervisor/game stack | Guard + measured low water |

## Z80 RAM

| Range | Size | Owner |
|---|---:|---|
| `$0000-$0FFF` | 4 KiB | Driver code and immutable tables |
| `$1000-$17FF` | 2 KiB | Sequencer/instrument/pattern state |
| `$1800-$1EFF` | 1,792 B | Driver scratch and future bounded data |
| `$1F00-$1FDF` | 224 B | Command queue |
| `$1FE0-$1FEF` | 16 B | Driver status/counters |
| `$1FF0-$1FFF` | 16 B | Versioned 68000/Z80 mailbox |

## VRAM

| Range | Size | Owner |
|---|---:|---|
| `$0000-$07FF` | 2 KiB / 64 tiles | Font and diagnostic glyphs |
| `$0800-$1FFF` | 6 KiB / 192 tiles | UI and shared system art |
| `$2000-$3FFF` | 8 KiB / 256 tiles | Player, echo, enemy sprites |
| `$4000-$7FFF` | 16 KiB / 512 tiles | Sector/encounter tiles |
| `$8000-$9FFF` | 8 KiB / 256 tiles | Effects and boss components |
| `$A000-$BFFF` | 8 KiB / 256 tiles | Dynamic/animated tiles |
| `$C000-$CFFF` | 4 KiB | Plane A, 64×32 cells |
| `$D000-$DFFF` | 4 KiB | Window plane, 64×32 cells |
| `$E000-$EFFF` | 4 KiB | Plane B, 64×32 cells |
| `$F000-$F27F` | 640 B | Sprite attribute table |
| `$F280-$FBFF` | 2,432 B | Reserved transfer scratch |
| `$FC00-$FC7F` | 128 B | Ordinary horizontal scroll table |
| `$FC80-$FFFF` | 896 B | Reserved normally; completes the 1 KiB per-line scroll table for approved Sector 5 effects |

CRAM assigns palette 0 to the sector, 1 to current actors, 2 to echoes and
identity marks, and 3 to UI/effects. VSRAM uses the first four words for plane
scroll modes; unused words are zeroed.

The main-RAM rows are contiguous and total exactly 64 KiB. No owner may consume
the 4 KiB contingency implicitly.
