# M4 CRC3 first-backlog diagnostic

This archive is bound to Debug ROM SHA-256
`01107b5f1dd14e2fa578247d266be95cbe97704248ae2e578d97b3f08551967e`.
The authoritative diagnostic is `first-backlog-native.json`; its status,
command policy, and transcript policy are all `pass`.

The native debugger stopped before the first instruction at renderer-local
`.backlog` (`$000053C2`). It observed master cycle `19,339,361`, frame `2`,
engine tick `2`, attempt `0`, `DEBUG_FRAME_TOKEN=0`,
`DMA_QUEUE_FRAME_PENDING=1`, renderer last tick `1`, and backlog counter `0`
before the branch incremented it. The preceding main-loop marker proves that
the token had just changed from `1` to `0`, so the value before the main-loop
decrement was `1`.

## Exact startup sequence

| Event | Master cycle | Frame/tick | Token | Pending | Relevant result |
|---|---:|---:|---:|---:|---|
| First handler entry | 18,635,343 | 0/0 | 0 | 0 | Boot stage RUNNING |
| First handler `rte` | 18,651,317 | 1/0 | 1 | 0 | Entry VDP status `$5200`; VBlank bit clear |
| First main step starts | 18,651,891 | 1/0 | 0 | 0 | Latched token consumed |
| Second handler entry | 18,687,787 | 1/0 | 0 | 0 | Only 5,128 CPU cycles after main start |
| Second handler `rte` | 18,703,873 | 2/0 | 1 | 0 | Entry VDP status `$5208`; VBlank bit set |
| Tick-1 renderer starts | 18,758,781 | 2/1 | 1 | 0 | Queue is available |
| Tick-1 renderer returns | 19,250,643 | 2/1 | 1 | 1 | Tick 1 published |
| Immediate next main step | 19,251,147 | 2/1 | 0 | 1 | Accumulated token consumed |
| Tick-2 renderer starts | 19,337,457 | 2/2 | 0 | 1 | No intervening VBlank |
| First backlog branch | 19,339,361 | 2/2 | 0 | 1 | Last rendered tick remains 1 |

The first delivered handler is the already-latched pre-display interrupt: its
captured entry status has the vertical-blank bit clear. The real vertical
blank arrives shortly afterward while tick 1 is still simulating. It creates
a second token before tick 1 publishes its renderer queue. The main loop then
uses that accumulated token immediately, reaches tick 2 without another
VBlank commit, and correctly finds tick 1's queue pending. This is a startup
phase-alignment defect, not a steady-state renderer-duration miss.

The capture used pinned BlastEm SHA-256
`22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803`
in an isolated temporary runtime. The native script permits only breakpoint
definitions, read-only expressions/printing, control flow, continue, and
quit. It injected no input and issued no guest memory or register writes.

`first-backlog-gdb.json` is supplemental stopped-memory corroboration. It
independently records frame 2/tick 2, pending `1`, last renderer tick `1`, and
no GDB write packets. Its inherited budget-transcript sub-policy is not the
authoritative diagnostic policy because that older helper performs two
additional read-only extended-audio reads outside the budget collector's
fixed command template.

## Artifact hashes

- `first-backlog-native.json`:
  `7bed94b92e2e52a1ed1e512812a8dc849e23ba3d96b2faa65e4d7000686268db`
- `first-backlog-native.log`:
  `e027016b8fc133fbb6a43c585b5bd8471c1959a1a31d3805fb874837a9274639`
- `native-backlog-collector.py`:
  `da4cd1d2b28458a04aba45990f4b9d33a5d1bfd2b50dee45713d4deca5fc05b0`
- `first-backlog-gdb.json`:
  `0d23e3c07bf70e3d0ec87d7b78761dff10c6a756f124f7f90facc6d6e9e6efed`
- `first-backlog-gdb.log`:
  `51300e0dc36dea271503888dd385cdde3a89152ac93dee113af00439918b2727`
