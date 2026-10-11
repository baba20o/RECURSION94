# Sega Genesis / Mega Drive hardware contract

This document records only assumptions the cartridge is allowed to rely on.

## Sources and verification status

- Sega, *Genesis Software Development Manual*, version 2.0 (1991-07-09):
  `https://gendev.spritesmind.net/files/md/tech/Genesis%20Software%20Development%20Manual%20%5BVersion%202.0%5D%20%5B1991-07-09%5D.pdf`
- Sega, *Genesis Technical Overview* (hosted scan):
  `https://manualzz.com/doc/13948468/sega-genesis-technical-overview`
- Motorola, *M68000 Family Programmer's Reference Manual*:
  `https://www.nxp.com/docs/en/reference-manual/M68000PRM.pdf`
- Motorola, *M68000 8-/16-/32-Bit Microprocessors User's Manual*,
  section8 (MC68000 instruction timing), rechecked 2026-10-10:
  [original-68000 timing tables](https://www.nxp.com/docs/en/reference-manual/MC68000UM.pdf).
  Indexed operands can use a long register without an index-size timing
  penalty; do not substitute the neighboring MC68008/MC68010 tables.
- Plutiedev ROM-header cross-check:
  `https://plutiedev.com/rom-header`
- Plutiedev three-button controller timing cross-check:
  `https://plutiedev.com/controllers`
- BlastEm pinned source and debugger documentation:
  `https://www.retrodev.com/repos/blastem/file/732f5689d438`

The Sega/Motorola manuals are the primary hardware authorities. Plutiedev is a
readable secondary implementation cross-check. Emulator behavior is evidence,
not a substitute for the hardware contracts.

## CPU and bus roles

- The Motorola 68000 owns boot, deterministic simulation, controller polling,
  level state, rendering preparation, save writes, and commands to sound.
- The Z80 owns bounded YM2612/PSG sequencing after its driver is copied into the
  8 KiB Z80 RAM window. Its hardware V-int is the only sequencer clock.
- The 68000 requests the Z80 bus before copying the driver or changing mailbox
  fields that cannot be safely single-byte posted. Debug builds time out.

## Boot

- Vector 0 contains initial SP `$00FFFFFC`; vector 1 contains `ResetEntry`.
- Reset masks interrupts, checks/writes `SEGA` to TMSS when the hardware version
  requires it, requests/resets Z80, initializes VDP registers with display off,
  clears 64 KiB work RAM/VRAM/CRAM/VSRAM, installs safe exception handlers,
  uploads initial tiles/palettes, then enables VBlank and display.
- Every exception vector has a defined handler. Debug handlers reach a visible
  fatal code; release handlers enter a safe bounded halt.
- Runtime status is `$2500`, admitting the level-6 VBlank interrupt while
  masking levels 1–5. `STOP` uses the same mask.

## Video and interrupts

- Gameplay targets H40 320×224 with 64×32 planes.
- 68000 VBlank is the only always-enabled main-CPU interrupt. Startup admits
  one stage-3 priming ISR that acknowledges VDP status and returns without
  polling input, advancing counters, publishing a token, or committing DMA.
  Once `BOOT_STAGE_RUNNING` is published, each full handler acknowledges VDP
  status, increments a saturating frame token, commits the prepared SAT and
  bounded transfer queue, and returns. It does not post a duplicate sound tick.
- Gameplay consumes one token at a time and never mutates render buffers being
  committed.
- A successful replay-bank rotation marks a completed reset. After all
  main-side reset audio/render work, the loop masks level 6, coalesces only the
  accumulated frame-token word to zero, and reaches `STOP #$2500`. An IRQ
  already pending behind that mask or the next VBlank supplies the first fresh
  token for the new attempt; no physical/debug counter is rewound.
- HBlank is normally disabled. Sector 5 may enable one precomputed,
  constant-time palette/scroll effect after its measured budget passes.
- DMA uses even word sources, explicit word counts, and a per-frame queue.
  Required transfers cannot be silently dropped.

## VDP limits adopted by the project

- 64 KiB VRAM, 128-byte CRAM (four 16-color palettes), and 80-byte VSRAM.
- H40 hardware maximum is 80 sprites/frame and 20 sprites/scanline; the engine
  targets at most 64 allocated entries and uses scanline-aware priority.
- Sprite link chains terminate with link zero. SAT is aligned at `$F000`.
- The exact VRAM ownership is in `MEMORY_MAP.md`; `$FC00-$FFFF` may become the
  full per-line scroll table only in an approved mode. Generated reports reject
  overlap or tile indices beyond the owned bank.

## Controllers and region

- Port 1 supports a standard three-button pad by driving the TH line and
  sampling active-low states. Held, pressed, released, and disconnect state are
  latched once per running full VBlank. The stage-3 acknowledgment-only startup
  priming ISR performs no electrical read. The electrical read is two phases:
  write `$40` then read D-pad/B/C; write `$00` then read A/Start. Each phase
  includes the documented settling delay. Reading more than once per running
  full VBlank is prohibited.
- Six-button detection may add conveniences but never gates a required action.
- Version register bit 6 selects PAL timing. NTSC performs one authoritative
  step per VBlank; PAL uses a 60/50 accumulator. Timing mode is serialized in
  replays.

## Save memory

- The selected board model exposes 8 KiB logical byte-wide SRAM on odd
  addresses `$200001-$203FFF`, enabled through `$A130F1`.
- The ROM header declares the same range. Writes happen only in menu/result
  states with interrupts enabled but outside interrupt code.
- Two journaled records contain signature, schema, sequence, payload length,
  payload, and CRC. On corruption the newest valid record wins; if neither is
  valid the game safely creates defaults.

## Milestone 1 evidence and remaining hardware gates

- The 1 MiB ROM with checksum `$1FE4` passes vector, header, SRAM-declaration,
  bounds, and checksum validation.
- Windows BlastEm GDB samples at three consecutive stage-4 running VBlank
  entries prove
  `R94D`, ABI 1, boot stage 4, fatal 0, hardware flags 0, and strictly advancing
  heartbeat/frame/token counters.
- A hash-bound corrected-ROM screenshot proves stable H40 tile output and the
  visible heartbeat fixture.
- Still unverified for Milestone 2 and later: controller
  disconnect/floating-bit policy, sprite/SAT behavior, DMA boundary and budget
  behavior, real Z80 driver upload, YM2612/PSG output, SRAM runtime behavior,
  and a second-emulator or physical-hardware cross-check.

## Adopted cartridge-header facts

- Checksum is the low 16 bits of the sum of big-endian words from `$000200`
  through the padded ROM end.
- Header ROM range is `$000000-$0FFFFF` for the chosen 1 MiB image; main RAM is
  `$FF0000-$FFFFFF`.
- Device field declares `J` for the required three-button pad.
- SRAM header bytes are `RA,$F8,$20` with start `$200001` and end `$203FFF`,
  matching battery-backed, byte-wide, odd-address storage.
