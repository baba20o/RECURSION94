# M5 B03 visual publication-timing failure

This archive preserves the first objective B03 visual-capture attempt against
fixture ROM SHA-256
`f86729bd27faf4c56bbb3e3a396607f54d1aa8bfa38b55b1e4de2b6d0e53b31e`.
It is a deliberate **failed diagnostic**, not visual evidence.

The collector reached the requested target at attempt 2/tick 67, but its
post-DMA assertion incorrectly required the authoritative coordinates to
remain at tick 67. The coherent observation was already the exact tick-68
successor with renderer `last_tick=68`. The replacement capture must bind the
PNG to the target presentation committed during the intervening VBlank and
separately require the exact successor state; it may not hide or accept
unbounded coordinate drift.

Canonical SHA-256 values before archival:

- `m5-b03-visual-fail.json`:
  `892ac756eadabbf381482265b081c026f7c40d4ef2bf5cfc193bca16eedaf02c`
- `m5-b03-visual-fail-gdb-transcript.log`:
  `dd2f8d396205b4b37398377753b13e78b377090518ba05ee046c1662d29cc442`
