# M5 B03 debugger-perturbed budget failure

This archive preserves the first two-session B03 budget attempt against Debug
fixture ROM SHA-256
`f86729bd27faf4c56bbb3e3a396607f54d1aa8bfa38b55b1e4de2b6d0e53b31e`.
It is a deliberate **failed diagnostic**, not passing budget evidence.

The exact `720 + 720 + 292` route and all renderer/DMA capacity signatures
reproduced across both sessions, but stopping at every authoritative PRE/POST
boundary perturbed the emulated clocks. Both sessions first reported a VBlank
overrun count of 10 at attempt 0 tick 67 and reached 210 at terminal; the
frame token also accumulated to 448. Those values cannot be waived or relabeled
as an uninterrupted hardware-budget pass.

Canonical SHA-256 values before archival:

- `m5-b03-budget-fail.json`:
  `c97c513e0d06312f55a6f568c03d5e1c7af186f3df7b42aaf2452f4a4666675e`
- `m5-b03-budget-fail-gdb-transcript.log`:
  `5957a75973774202860ccbbd017ab0e13e1b49ad0ad0ca1063063959c9d2b7d7`

The replacement collector must avoid per-step debugger stops, verify the exact
guest replay bytes, and require the real cumulative VBlank/renderer/DMA fault
counters to remain zero.
