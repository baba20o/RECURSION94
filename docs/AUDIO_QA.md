# Audio QA

Required deterministic captures: boot pulse, title, zero/one/two/four/maximum
echo layers, reset, boss phase, Program Stable, and final transmission.

Milestone 2 captured the boot driver in
`artifacts/audio/recursion94-m2-20260726-025027.vgm`, bound to Debug ROM
`2e66b738...`. The 3.872-second stream ends cleanly, contains 272 YM2612
writes (17 key-ons, 16 key-offs, 9 frequency values) and 59 PSG writes
(17 audible-volume changes, 5 tone values), and has identical header and
parsed wait totals. `artifacts/reports/m2-audio-capture.json` passes 6/6
technical checks.

VGM parsing proves dynamic chip-command output, not human listening quality.
Formal mixed-audio capture, listening review, clipping/stuck-note assessment,
and the remaining required musical states are still pending.

## Frozen score/driver review contract

- Z80 hardware V-int is the sole sequencer clock; 24 PPQN uses an integer
  fixed-point accumulator adapted to region.
- Sector clocks are 80/100/120/140/160 BPM, making a twelve-second attempt
  exactly 4/5/6/7/8 bars of 4/4.
- Layer allocation: YM1 base; PSG noise echo 1; YM2 bass echo 2; YM3 harmony
  echo 3; YM4 lead echo 4; PSG tones 1–2 texture echo 5; YM5 resolution echo 6;
  YM6 primary SFX.
- The driver shadows registers, explicitly keys off, supports all-notes-off,
  restores any stolen channel state, and publishes late-tick/queue high water.
- Reset clears pending SFX, keys off, rewinds to phrase start, and restarts with
  the new layer mask. Pause freezes phase under one verified silence policy.
- Gameplay and visual beat mechanics use a parallel deterministic 68000 phase;
  sound playback never drives authoritative state.

Each review records ROM hash, state/replay, WAV path, YM2612/PSG allocation,
silence/stuck-note/clipping/queue/channel-starvation checks, musical feedback,
defects, repair, and recapture comparison. Muted playability is tested
separately; it does not excuse weak audio.
