# M5 B03 successor-renderer pending diagnostic

This immutable diagnostic preserves the first strengthened five-process visual
probe failure on 2026-08-30, bound to Debug B03 ROM SHA-256
`f86729bd27faf4c56bbb3e3a396607f54d1aa8bfa38b55b1e4de2b6d0e53b31e`.

The second capture reached the calculated ODD DMA entry at frame 2045 with
the exact successor renderer coordinate (attempt 2, tick 197), a healthy
renderer, and the exact pending queue shape (depth 4, 440 words).  The
collector rejected it because the ODD observation's authoritative bundle no
longer equaled the preceding successor-POST observation.  Trace comparison
showed that the interrupt had preempted the next gameplay step: engine tick
remained 197 while gameplay, device timers, and replay diagnostic bytes had
already begun tick 198.  The queued renderer and DMA state remained the exact
successor presentation.

Archived byte hashes:

- `m5-b03-visual-fail.json`:
  `51216e0803f4e92ebab72b7193ba94582ad7dc1d253b056f2dc3e9b8b801e489`
- `m5-b03-visual-fail-gdb-transcript.log`:
  `b8228c73dc2fccb9937fb4b1353315f6b8b6a1fea6601205d5cab604bfa073ac`

No screenshot was published by this failed run.
