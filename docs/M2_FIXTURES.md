# Milestone 2 fixture contract

Milestone 2 proves each hardware path independently before the deterministic
game engine is allowed to depend on it. All evidence must name the exact Debug
ROM SHA-256.

## Input fixture

- Initialize port 1 as a standard three-button pad and restore TH high after
  every sample.
- Sample exactly once per `BOOT_STAGE_RUNNING` full VBlank. The deliberate
  stage-3 startup priming ISR acknowledges VDP status only and does not poll
  the controller, advance counters, or publish a frame token.
- Normalize held bits as `UDLRABC` in bits 0–6; bit 7 is zero.
- Expose held, pressed, released, Start, connected, and sample count through
  the fixed debug ABI.
- A scripted test holds Right across at least two samples, then releases it.
  Required observations:
  - first held sample: Right held and pressed;
  - second held sample: Right held and not pressed;
  - release sample: Right released and not held;
  - sample counter advances once per running full VBlank; the startup priming
    entry is reported separately and excluded.
- Simultaneous Left+Right and Up+Down normalize to neutral.
- Emulator evidence records physical VBlank breakpoint-entry stops separately
  as one stage-3 acknowledgment-only priming entry followed by stage-4 running
  full-VBlank entries.

## Sprite fixture

- Render one required 8×8 diagnostic sprite from an owned tile.
- D-pad input moves it within the 320×224 safe rectangle.
- SAT terminates with link zero and is committed from the fixed shadow buffer.
- Debug counters report one required sprite and a scanline maximum of one.
- A screenshot must show the sprite, diagnostic text, and no plane corruption.

## DMA fixture

- Use the 32-entry bounded queue, not a direct unreported transfer.
- Validate even source, nonzero word count, owned destination, and queue
  capacity before publishing an entry.
- The VBlank consumer drains only the fixed budget and records words/frame,
  maximum words, depth, high-water mark, overflow count, and late-VBlank count.
- The normal SAT transfer is 320 words. Overflow and invalid-entry host vectors
  must fail safely without overwriting adjacent RAM.
- Transfers crossing a 128 KiB source boundary must be split or rejected until
  the split implementation is proven.

## Z80 and audio fixture

- Copy a driver no larger than `$1F00` into the 8 KiB Z80 RAM aperture using
  byte accesses while the bus is owned.
- Hold reset for at least 192 68000 cycles after upload, then release reset
  before releasing the bus.
- Z80 IM1 V-int is the only music-sequencer clock.
- ZRAM `$1FE0` exposes a signature, driver state, and advancing heartbeat.
- ZRAM `$1F00` begins a 16×4-byte command ring; `$1FF0` is the urgent mailbox.
- Produce an original, bounded YM2612 motif plus an audible PSG path. Evidence
  must show an advancing Z80 heartbeat, actual YM writes, actual PSG writes,
  and a capture or event trace sufficient to reject silence and stuck notes.
- Shutdown/reset commands key off all YM operators and silence PSG channels.

## Fatal fixture

- An intentional debug-only exception after video initialization reaches a
  visible fatal screen, publishes a nonzero fatal code, and stops boundedly.
- Ordinary boot keeps the fatal code zero.

## Passing aggregate

Milestone 2 passes only when input, video/sprite, DMA, Z80, YM2612, PSG, fatal
reporting, and budget instrumentation each have hash-bound emulator evidence;
host/static checks alone cannot promote a pending runtime gate.
