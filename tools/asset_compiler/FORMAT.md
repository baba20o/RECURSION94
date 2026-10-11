# RECURSION 94 art formats

All integer fields are big-endian. Generated files are deterministic and must
not be hand-edited. Source files are strict UTF-8 JSON. Unknown fields,
duplicate object keys, unsafe paths, non-finite numbers, and undeclared JSON
files are rejected.

## Color and tile rules

- Four palette slots are emitted as 16 CRAM words each.
- Entry zero is transparent and is always `$0000`.
- A Genesis color is `0000 BBB0 GGG0 RRR0`; each channel is rounded from
  eight bits to three bits.
- A tile is 8×8 indexed pixels packed into 32 bytes, two 4-bit pixels per byte,
  left pixel first.
- Tile zero is the transparent tile.
- Global deduplication considers exact, horizontal, vertical, and combined
  flips. References carry the required flip bits.

## Component files

`recursion94_art_palettes.bin`

: Raw four-bank CRAM image, exactly 128 bytes.

`recursion94_art_tiles.bin`

: Raw 32-byte pattern records, starting with transparent tile zero.

`recursion94_art_tilemaps.bin`

: `R94M` header (`magic, version:u16, count:u16, directory_offset:u32,
  data_offset:u32`), followed by 32-byte directory records and raw 16-bit VDP
  name-table entries. Directory records are
  `id[16], width:u16, height:u16, offset:u32, size:u32, reserved:u32`.

`recursion94_art_sprites.bin`

: `R94S` header with the same header shape. A 32-byte frame directory record
  is `id[16], width:u16, height:u16, piece_count:u16, reserved:u16,
  offset:u32, size:u32`. Each eight-byte piece is
  `x:s8, y:s8, size:u8, flags:u8, tile_attribute:u16, reserved:u16`.
  Size zero means a Genesis 1×1-cell sprite. Pieces are statically bounded.

`recursion94_art_animations.bin`

: `R94N` header with the same header shape. A 32-byte directory record is
  `id[16], loop_mode:u16, frame_count:u16, offset:u32, size:u32,
  total_ticks:u16, reserved:u16`. Each step is
  `sprite_frame_index:u16, duration_ticks:u16`. Loop modes are 0 once, 1 loop,
  and 2 ping-pong.

`recursion94_art.bin`

: Aggregate `R94A` container. Its 16-byte header is
  `magic, version:u16, section_count:u16, directory_offset:u32,
  file_size:u32`. Five 16-byte section records contain
  `type[4], offset:u32, size:u32, crc32:u32`. Types are `CRAM`, `TILE`, `TMAP`,
  `SPRT`, and `ANIM`. Sections are two-byte aligned.

`recursion94_art.inc.asm`

: vasm-compatible constants and a single `incbin` declaration for the
  aggregate bundle.

`recursion94_art_report.json`

: Source provenance, semantic source and output hashes, conversion metrics,
  tile-reuse classes, attribute coverage, and ROM/VRAM/CRAM/sprite budgets.

`recursion94_art.sha256`

: SHA-256 manifest for every other generated file.

The compiler does not claim that runtime upload, rendering, emulator behavior,
or subjective visual quality has been verified.
