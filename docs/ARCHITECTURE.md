# Cartridge architecture

## Build flow

Source JSON/text assets are validated and compiled by pure-stdlib Python into
`assets/generated`. sjasmplus assembles `src/audio/z80_driver.asm`. The generated
Z80 image and content binaries are included by a fixed-order Motorola-syntax
68000 translation unit. vasm emits raw debug/release images, listings, and
symbols. Python pads to 1 MiB, patches the Genesis checksum, validates every
header/section/budget, and writes machine-readable reports.

## Boot and state flow

```text
vectors → reset/TMSS → RAM/VDP clear → video fixture → Z80 upload
        → title → stage select → encounter → reset/result
        → replay theater / next stage → ending → title
```

The title waits for input and also starts attract mode. Attract mode feeds real
canonical replay bytes through the same encounter engine.

## Main loop

1. Wait for a VBlank frame token.
2. Latch controller held/pressed/released state.
3. Schedule one NTSC step or one/two PAL authoritative steps.
4. For each step: source input for every echo/current actor; update actors;
   resolve interaction intent; update devices/enemies/projectiles; resolve
   collision/damage/objectives; record current input; update timeline and
   checkpoint signature.
5. Build tilemap, sprite, HUD, and transfer queues in RAM.
6. Publish the prepared buffer atomically for the next VBlank.
7. Post only changed sound state/SFX commands and update debug instrumentation.

A successful attempt commit always rotates `TIMELINE_CURRENT_BANK`, which is
the saturation-safe reset epoch. The main loop snapshots that byte inside the
masked token transaction. If it changed, the loop finishes audio and renderer
presentation, masks VBlank, clears only accumulated frame tokens, and enters
the ordinary atomic `STOP #$2500` wait. VBlanks spent computing reset state are
therefore presentation/service frames, never catch-up simulation steps; frame,
heartbeat, sequence, and controller-sample counters remain monotonic.

## Echo, reset, and anchor

Seven physical 720-byte recording banks cover six echoes and the current
incarnation; compact descriptors rotate roles without copying banks. An input
byte contains held U/D/L/R/A/B/C in bits 0–6, with bit 7 required zero.
Opposite axes sanitize to neither direction. Each actor begins from the
compiled spawn and consumes its slot at the same authoritative index.
Actors do not body-block one another, but all collide with solids, doors,
hazards, projectiles, enemies, carryables, switches, and devices.

At attempt end, the current length is committed to the selected echo role. At
capacity the timeline requires an explicit replacement selection; protected
slots cannot be overwritten. Reset clears every ordinary-state owner,
reconstructs pools/devices from immutable level data in logical-ID order, then
applies a sorted typed anchor list. Anchors are idempotent logical records, not
pointers or arbitrary world bytes.

## Object and collision model

- Positions/velocities use signed 16.8 fixed point.
- Rooms use 8×8 collision cells and 16×16 visual metatiles.
- Actor versus static collision is axis-separated and deterministic.
- Hard pools: 7 actors, 16 enemies, 32 projectiles, 32 devices, 8 carryables,
  16 hazards, 12 boss components, and 24 cosmetic effects.
- Allocation scans lowest free index; target selection uses stable
  distance-then-index order. Overflow is fatal in debug and safe/reported in
  release.

Each authoritative tick gathers all inputs/intents from beginning-of-tick state
before mutation. It then resolves movement against beginning-of-tick geometry,
aggregates occupancy, resolves exclusive interactions in oldest-echo through
current order, updates projectiles, applies sorted damage/transitions, commits
device/door changes for the next tick, and finally commits terminal/hash state.

## Rendering

Plane B carries sector architecture; Plane A carries interactive overlays and
foregrounds; Window carries the HUD/timeline. Hardware sprites carry actors,
enemies, projectiles, carryables, identity badges, and critical effects.
Sector assets replace a fixed VRAM bank between encounters while shared font,
UI, actor, and effects banks remain resident. SAT ordering protects current
player, objectives, and hazards before cosmetic effects.

## Audio

The 68000 posts region, echo-layer count, music state, and SFX/control commands
to a versioned Z80 mailbox/queue; it does not post frame ticks. Z80 hardware
V-int is the sole sequencer clock. The driver owns all routine YM2612 and PSG
writes, quantizes layer changes to phrase boundaries when possible, and
guarantees an all-notes-off reset/pause transition. Music data is compiled from
original symbolic patterns.

The queue at Z80 `$1F00` holds 16 four-byte commands; `$1FE0` exposes status and
high water; `$1FF0` is an urgent versioned control mailbox. Mainline posts at
most four commands/display frame and coalesces repeated shot/dash cues.
Reset/pause/stop are urgent and cannot be dropped. Layer allocation is YM1
base, PSG noise echo 1, YM2 bass echo 2, YM3 harmony echo 3, YM4 lead echo 4,
PSG tones echo 5, YM5 resolution echo 6, and YM6 primary SFX. Sector tempos
80/100/120/140/160 BPM make each twelve-second loop an exact phrase.

## Level and replay formats

Each compiled encounter has an ID, sector, tile/collision maps, spawn, bounded
device/enemy descriptors, goals, timing, echo cap, anchor permissions, visual
and music IDs, budget declaration, and canonical replay ID. Bosses add a
deterministic phase table.

The host `.r94r` replay format is specified in `REPLAY_FORMAT.md`. Milestone
acceptance uses full attempt history through the actual commit/reset path;
composition-only payloads are replay-theater evidence, not determinism proof.

## Save, debug, and automation

Save uses two journaled SRAM records. A stable seqlocked 1 KiB RAM debug ABI at
`$FFF000` exposes signature/version, mode/stage, heartbeat/frame/attempt/tick,
controller/echo/anchor state, pool and budget maxima, CRC-32 checkpoints,
completion signature, stack low water, and fatal data. Debug-only overlays read
the same values. Emulator automation observes real runtime state and
independently recomputes authoritative CRCs; it never patches gameplay.

The pinned BlastEm source exposes `-b <frames>` even though that developer
option is omitted from public help, but the pinned Windows binary currently
access-violates on that path. It is retained as a non-required diagnostic until
the emulator/tool defect is resolved. Bounded automation instead uses the GDB
remote interface, serialized access to its fixed port 1234, counted VBlank
breakpoints, and versioned debug-ABI reads. `-e FILE` remains available for
hardware event logs. GUI capture uses the same hash-bound ROM and an explicit
window capture. A process exit or screenshot alone is never heartbeat proof.

Authoritative fields are serialized big-endian into at most 4 KiB and hashed
with CRC-32/ISO-HDLC (`123456789 → $CBF43926`) at frame zero, every 60 ticks,
tick 720, and terminal states. Renderer, audio playback, display counters,
debug data, unused padding, and raw pointers are excluded.
