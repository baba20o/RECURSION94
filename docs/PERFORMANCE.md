# Performance and hardware budgets

These are conservative design thresholds. Milestones 2 and 3 establish live
hardware and bounded deterministic-state measurements; gameplay cycle timing
and campaign worst cases remain pending.

| Resource | Release threshold | Stress ceiling |
|---|---:|---:|
| Authoritative simulation | ≤ 65% of NTSC frame | ≤ 85% on PAL double-step frame |
| Natural reset transition | ≤ 2 NTSC frames | No wider implicit allowance |
| VBlank handler | ≤ 70% of measured VBlank | ≤ 85% |
| Queued DMA/VRAM words | 2,048 ordinary | 3,200 approved loading/reset frame |
| Hardware sprites | 64 ordinary / 72 soft stress | 80 hardware maximum |
| Sprites per scanline | 16 soft / 18 designed max | 20 hardware maximum |
| Active echoes | Campaign-defined 1–6 | 6 |
| Enemies | 12 canonical | 16 hard |
| Projectiles | 24 canonical | 32 hard |
| Devices | 24 canonical | 32 hard |
| DMA queue entries | 32 | 32 |
| Z80 command queue | 16 entries | 16 entries |
| Main RAM | 60 KiB owned | 64 KiB absolute |
| VRAM | 63 KiB owned/reserved | 64 KiB absolute |
| Z80 RAM | 7.75 KiB owned | 8 KiB absolute |
| ROM | 1 MiB image | 1 MiB absolute chosen layout |

## Measured fixtures

| Fixture | DMA words | DMA high water | Sprites | Scanline max | Z80 queue high water | Fault counters |
|---|---:|---:|---:|---:|---:|---|
| M2 diagnostic | 320 | 1 entry | 1 | 1 | 1 command | DMA overflow/deferred/required-drop/invalid and Z80 timeout all 0 |

The current Z80 image is 1,214 bytes and remains below the `$1000` immutable
driver ceiling. The ordinary M2 SAT transfer is 15.6% of the 2,048-word
runtime DMA threshold.

The live M3 core uses a fixed 3,638-byte canonical projection and 18,432-byte
ordinary-state region. Two runs kept actor count at 1 and pool counts at
enemy/device/carryable/hazard `1/1/1/1`, with every other pool zero, no
overflow flags, and all unused padding zero through 720 steps and reset.
Controller, DMA, audio, and engine error counters remained clear. CPU duration,
stack low water, multi-echo gameplay pools, and every canonical encounter/boss
still require measured rows. A budget failure cannot be “fixed” by silently
removing a required mechanic or echo.

## Milestone 5 combined-room budget gate

The required M5 gate uses the dedicated B03 Debug fixture and imports the
exact hash-bound `720 + 720 + 292` authoritative-step route from the passing
B03 runtime report. Two fresh owned processes must reproduce route-aligned
observations covering switch activation, echo projectile/node damage, closed
and open door states, dash traversal, terminal-hold start/late states, and
`PROGRAM STABLE`. Debugger guest-memory/register/PC writes are forbidden.

At every observation the semantic validator independently reparses raw
engine, vocabulary, pool, renderer, DMA, audio, and debug blocks. It requires
all cumulative renderer/DMA/VBlank/Z80/audio/engine/vocabulary fault fields to
remain zero; complete pool scans to agree with published counts/high-water
marks; vocabulary work to remain at or below 4,096 units; sprites, per-line
sprites, per-line pixels, ordinary DMA words, and DMA depth to remain within
64/20/320/2,048/32; and the Z80 cross-clock allowance to hold. Both complete
session signatures must match.

The report, transcript, schema, collector, probe, exact B03 ROM/symbol map,
runtime prerequisite report/trace/replay, emulator, and controller config are
all hash-bound. Aggregate validation calls
`tools.m5_budget_evidence.report_contract_errors` to recompute the contract;
a passing status row alone is insufficient. The canonical output is
`artifacts/reports/m5-b03-budget.json`. Until it passes, Milestone 5 remains
unfrozen and no measured B03 maxima are promoted into the table above.

The pending schema-v3 capture additionally audits 29 intentional checkpoint
holds (26 regular, two reset, one terminal), the 28 following recovery frames,
empty held-frame DMA, and exact recovery records. Its reset protocol must
account for the two reset-service VBlanks at each timeout without consuming
extra authoritative steps. These counter/capacity observations do not replace
a CPU-cycle measurement.

The historical post-repair tick-60 diagnostic
`artifacts/diagnostics/m5-budget/overrun-checkpoint-phase-post-repair-tick59-tick61-20260830.json`
measured 107,426 CPU cycles inside `GameplayStep`, including 64,312 cycles for
projection publication, and 108,188 cycles for the complete main-side work.
Although the held renderer left 8,296 cycles before the next nominal VBlank,
the simulation span exceeds the separate 83,203-cycle (65%) design threshold.
This older-ROM diagnostic is not a current-ROM cycle pass.

On 2026-10-10 the same bounded native diagnostic was rerun against B03
`140039e3...e7265e` and symbols `b1010589...5fa8b0`:
`artifacts/diagnostics/m5-budget/checkpoint-final-140039e3-tick59-tick61-20261010.json`
(SHA-256 `98b98610dd68735ae6bdc1bf3ba5fa4b9857fdc31c381f4262851ce7b931c035`).
It reproduces 107,428 simulation cycles, 64,310 projection cycles (47,592 base
plus 14,182 tail CRC), 108,188 complete main-side cycles, and 8,274 cycles of
headroom. Frame 61 holds video with zero DMA; frame 62 recovers four records /
440 words. The diagnostic protocol passes with zero debugger writes, but the
simulation threshold fails by 24,225 cycles. A measured repair and complete
current-ROM cycle gate remain required before M5 freeze; a counter-only pass
cannot waive that gate.

The first current-ROM schema-v3 run on 2026-10-10 fails at total723 / physical
frame725. The first reset hold and recovery match the expected empty queue
then five records /480 words. The next frame has six records /520 words, but
the captured VDP entry status `$0E20` lacks bit3 (VBlank). The existing overrun
counter remains zero: it cannot detect that the interrupt entered after blank
had already ended. This is PERF-002, not a capacity pass. The immutable failed
report and trace are under
`artifacts/archive/m5-budget-v3-reset-overlay-late-vblank-140039e3-20261010/`,
with SHA-256 `ce918b4c2568006e6ed9eab80c4e86cdfb120b5affd3b09ae09217bb9185e3ae`
and `85bce8930d91f6b29fffd77f369b155d3c19ee3f24cbd1c148aeeb635b230149`.
The separate visual run also sees growing physical-frame drift (+143 at
total1507; +184 at total1636) before failing its exact screenshot-state proof.
A bounded native reset diagnostic is being extended through totals723–724 to
attribute simulation, renderer interrupt-masking, and delayed interrupt entry
before runtime edits. No CPU or visual gate is waived by the zero counters.

The extended unchanged-ROM baseline now captures totals719–724. Its immutable
raw report/trace are
`artifacts/diagnostics/m5-budget/reset-overlay-baseline-140039e3-total719-total724-20261010.{json,log}`.
Separate reanalysis (same basename with `-reanalysis` before the date) keeps
protocol validity distinct from budget failure. At total722, uninterrupted
gameplay costs 52,168 cycles and the renderer's masked interval costs 76,498:
HUD 12,942, SAT 55,148, overlay 2,154, queue/tail 5,986, plus renderer setup.
The late interrupt then incurs further wall time; these spans are not a
passing one-frame measurement. The first reset call itself costs 185,606
cycles and fits its separate two-frame allowance. Stable entry720 phase shows
entry725 is 18,263 CPU cycles late and entry726 approximately 58,288 late;
both nominal deadlines fall inside renderer mask7. Six exact SAT/HUD/overlay
buffers are retained for byte-level optimization parity.

The first isolated repair skips only pools with exact live count 0 during
`CampaignFindObject`; nonempty pools retain original ordered active-ID scans.
Its three built/static-validated candidates are Debug `f9b90701`,
Release `1c1ff419`, and B03 `9a741642`, archived under
`artifacts/archive/m5-lookup-only-9a741642-20261010/`. Focused Windows lookup
coverage passes 19/19. Native tick60 falls from 107,428 to 101,332 cycles
(6,096 saved), with unchanged projection CRCs and 64,310-cycle projection
cost. It still exceeds the simulation limit by 18,129. Post-reset total722
gameplay falls from 52,168 to 46,074 cycles; renderer cost is unchanged.
Entries725/726 now reach blank but their six-record commits run past its end;
entry727 is late. The exact lookup capture files are
`checkpoint-lookup-9a741642-tick59-tick61-20261010.{json,log}` and
`reset-overlay-lookup-9a741642-total719-total724-20261010.{json,log}` in the same
diagnostic directory. No threshold has been raised and no mechanic or actor
has been removed. The repair is retained for measured improvement, not
reported as a budget pass.

The next isolated repair tightens successful scanline sprite bookkeeping,
retaining both limit checks and exact rollback on a rejected sprite. B03
`09be6696` reduces total722 SAT work from 55,148 to 50,076 cycles (5,072
saved) and its masked renderer span from 76,498 to 71,428 (5,070 saved;
the two-cycle difference is observed phase variation). Tick60 simulation
remains 101,332. Six renderer snapshots totaling 6,240 bytes and all sampled
projection CRCs match exactly. Reset-overlay commits still overrun VBlank,
so this is an improvement, not a passing budget. The immutable comparison
`artifacts/diagnostics/m5-budget/sprite-loop-timing-parity-9a741642-to-09be6696-20261010.json`
has SHA-256 `42882ec81d5edf67710477888016984ef9d010385684fb64ab4a54f0a353015a`.
Both profiles reparse with tightened WAIT-anchor and nested interrupt checks;
26 diagnostic host tests pass. ROMs and symbols are archived under
`artifacts/archive/m5-sprite-loop-09be6696-20261010/`.

The following candidate, B03 `c051995c`, hoists the CRC T16 table base out of
the aligned-word loop and uses the original 68000's full-long-index read.
The recurrence, tables, odd-address prefix, and byte tail are unchanged.
Debug, Release, B03, and their independent static checks pass; the full native
CRC suite passes 25/25. The native regular/reset pair now passes its
diagnostic protocols and preserves all sampled CRCs and 6,240 renderer bytes.
Tick60 simulation falls 101,332 to 96,694 cycles (4,638 saved), projection
64,310 to 59,672, base serialization/CRC 47,592 to 44,762, and tail CRC
14,182 to 12,374. Total main-side work is 97,456 cycles. The simulation
threshold still fails by 13,491; later reset-overlay commits still overrun
blank. Raw files are `checkpoint-indexed-c051995c-tick59-tick61-20261010`
and `reset-overlay-indexed-c051995c-total719-total724-20261010`, each with
`.json` and `.log` under the diagnostic directory. The reset still crosses
two service VBlanks; no artificial timing padding was added.
Its paired comparison is
`crc-index-timing-parity-09be6696-to-c051995c-20261010.json`, SHA-256
`9e8f60d5514b88b31de8d6e017edf032ed0b18844e64f0fe4918f75bdf7eb9ff`;
the exact candidate ROMs/symbols are archived under
`artifacts/archive/m5-crc-index-c051995c-20261010/`.

The next candidate, B03 `25a74bd2`, bypasses slot classification only when
both the preceding projected live count and the current exact live count are
zero. It still writes and hashes the entire pool header, advances by the full
fixed payload length, and uses the existing zero-record CRC transform. A
formerly nonempty pool takes the normal clearing path. Its legal prior image
is reset-zeroed or a complete canonical projection, not arbitrary memory.
All three builds/statics and 28 native CRC/projection tests pass. Native
tick60 falls 96,694 to 89,912 cycles (6,782 saved), with projection 52,890,
base projection 37,980, unchanged tail CRC 12,374, and total main work 90,674.
The simulation limit still fails by 6,709. Timeout tick720 falls to 86,312;
its main/reset interval falls 271,935 to 258,376 (13,559 saved). Reset still
uses two service VBlanks. Ordinary post-reset simulation/rendering is
unchanged, so the later blank overruns remain. All 6,240 renderer bytes and
regular/timeout/reset CRCs match. The paired report
`empty-pool-timing-parity-c051995c-to-25a74bd2-20261010.json` has SHA-256
`ea87cf748a42e5a61ca14133f30b0349c7551ba627886fcc05209d7f60494fb8`
and records protocol/parity PASS, budget FAIL. ROMs/symbols are archived under
`artifacts/archive/m5-empty-pool-25a74bd2-20261010/`.

The high-water candidate, B03 `96dec577`, bounds ten gameplay scans using the
legal lowest-free allocator's historical maximum live count. It retains slot
order, active tests, actor scans, and every zero-bound publication side effect.
All three ROM builds and independent static checks pass. Code growth required
three display-off initialization calls to use absolute-long JSR; per-frame
renderer code is unchanged. Tick60 simulation falls from 89,912 to 80,828
cycles (9,084 saved), leaving 2,375 below the 83,203 limit; projection remains
52,890 and complete main work is 81,590. Timeout total720 simulation takes
78,380 and reset main work 250,444. At total722 simulation is 34,070 (12,004
saved), the masked renderer remains 71,428, and whole main is 107,448. The
extended reset diagnostic passes all sampled cycle/blank checks with exactly
IRQs719–726, no late IRQ727, and unchanged two-service-VBlank reset cadence.
All sampled CRCs and all 6,240 renderer bytes match the preceding candidate.
The exact `.json`/`.log` pairs are
`checkpoint-highwater-96dec577-tick59-tick61-20261010` and
`reset-overlay-highwater-96dec577-total719-total724-20261010` under
`artifacts/diagnostics/m5-budget/`. The paired reanalysis
`high-water-timing-parity-25a74bd2-to-96dec577-20261010.json` has SHA-256
`23d78a1fdcdb7309047d5bea443b045ecd38e4d7b9132064d4d53eac583fde66`.
This is a bounded sampled pass, not the canonical M5 cycle gate. Later
three-actor checkpoints, projectile interactions, full budget/replay routes,
and objective visual recapture remain required before M5 freeze.

## Measured campaign renderer high-water repair

B03 candidate `e6778f90` changes exactly seven read-only campaign scans: five
SAT object pools, the door-feedback predicate, and the HUD device-status scan.
Each uses its unsigned historical pool bound, clamps to capacity, and guards
zero before DBF. Legacy renderer paths, actor ordering, and gameplay remain
unchanged. Three builds/static checks and 73 focused native tests pass.

The regular and extended-reset native diagnostics both pass protocol and their
sampled budgets, using the separately reviewed `renderer_hwm` profile (65/65
focused native tests). At ticks 59/61, renderer work falls from 65,598/65,226
to 59,138/58,764 cycles: savings of 6,460/6,462. Tick60 simulation remains
80,828 and projection 52,890. At total722 the masked renderer falls from
71,428 to 66,118 cycles; whole main work falls from 107,448 to 102,138.
The reset main interval itself is unchanged at 250,444 cycles. Reset cadence,
all sampled projection CRCs, and all six exact renderer-buffer snapshots
(6,240 bytes) match the preceding high-water candidate. Both runs use zero
guest writes and no retries; native process cleanup is verified.

Raw artifacts under `artifacts/diagnostics/m5-budget/`:

- `checkpoint-renderer-hwm-e6778f90-tick59-tick61-20261010.{json,log}`;
  report SHA-256 `6fc3cebc759f71ff8e9298177a8b6817f83fd41e8a05d461b85fc67fe50cad47`.
- `reset-overlay-renderer-hwm-e6778f90-total719-total724-20261010.{json,log}`;
  report SHA-256 `b1ffcd6d09667845a27b367b33be87df45bab6b11c7539b4d495080c01c67357`.

The candidate's natural prefix also completes all 1,681 input steps and both
reset replay snapshots, with protocol PASS and exact parity at all four
projection checkpoints. Its immutable report/log are
`canonical-prefix-renderer-hwm-e6778f90-attempt2-tick241-20261010.{json,log}`;
report SHA-256
`d1e86bebd1ddab8577c654ec18d01cd03375c5b8cc9e02c638fe5f35a1e93b40`.
All four GameplayStep intervals now contain no IRQ, directly measuring
97,060 / 91,962 / 91,870 / 95,740 cycles at ticks 60 / 120 / 180 / 240.
They still exceed the 83,203 simulation limit. Projection remains 52,890.
The second-reset lower bound remains 258,196 versus 256,010. The final physical
frame is 1,685 with zero queued tokens, but intermediate cadence/blank failures
remain: 70 reported budget violations, a cumulative overrun counter of five,
and a maximum raw main span of 152,732 at tick193. That whole-main span includes
interrupt service; it is not a pure simulation or renderer cost.

The independently reviewed pinned comparison is
`renderer-hwm-timing-parity-96dec577-to-e6778f90-20261010.json`, SHA-256
`ac60c0f84054e491252ba820e830017e15cdb1800bfb6137b08bfcc182bb9a80`.
Its helper passes 67/67 focused native tests. Its sampled budget claim is
explicitly limited to the first-attempt regular/reset windows, not the later
prefix or full route. Thus the renderer improvement is measured, but neither
the multi-echo simulation defect nor the M5 budget gate is closed.
Ordinary-frame/blank failures also require repair. The older late-entry
detector limitation remains open, so its counter alone cannot certify all
blank deadlines.

## CRC/projection candidate: measured checkpoint gains

B03 `8b69d4bf` retains the renderer improvement and adds a transformed-state
CRC word loop with four-word unrolling, exact contiguous projection copies,
monotonic pool scan bounds, and a direct 27-zero-record transition. The public
raw-CRC ABI and complete projection bytes/schema remain unchanged by design.
The generated table bundle grows by 4,096 bytes to 312,320 bytes; the three
ROM images still fit the chosen 1 MiB layout. Standalone assembly, all three
builds, static ROM validation, and 77 focused native host tests pass.
An independent code review covers the zero/odd/even/max-length paths,
full-width table indexing, every copied field, source-pointer advancement,
and the allocator/reset invariant required for omitted zero suffixes.

Short CRC calls can become slower, so only native whole-frame measurements
establish the net result. The first regular capture passes, with tick60
simulation 65,866 versus 80,828 cycles and projection 37,928 versus 52,890.
Base projection falls by 10,570, raw tail CRC by 3,732, and vocabulary copy by
662 cycles: 14,962 total. Sampled CRCs are equal and renderer spans unchanged.
Report `checkpoint-crc-projection-8b69d4bf-tick59-tick61-20261010.json` has
SHA-256 `86e340fdd89a88f8e144ababced0d145b5de61e854112511a739574583ee2e44`.

The natural 1,681-step prefix passes the input/replay protocol and all four
projection CRC comparisons. IRQ-free simulation at ticks60/120/180/240 is
82,096 / 77,000 / 76,908 / 80,778, all below 83,203. Reset lower-bound spans
are 205,945 / 215,195 with one service token each instead of two; the two
5,120-byte replay snapshots are unchanged. The report remains budget FAIL:
66 ordinary/cadence/queue/blank violations, five cumulative overruns, final
frame1,683/token0. This is not an ordinary-frame or full M5 pass. Prefix report
SHA-256: `5fe84910c9c19905d2916cad41983e0e0fbca665328cde2613bdccdac1ae3148`.

The first extended-reset capture is an immutable diagnostic failure. Faster
work exposes an existing stop-condition race: at ISR return frame721 the
attempt has incremented to1 but reconstruction has not yet cleared old tick720.
The old attempt>=1/tick>=5 condition emits COMPLETE before the reset returns.
No reset-window result is inferred from its missing records. The separately
reviewed candidate-only correction retains the physical frame730 failure
stop and requires exact attempt1/tick4 at main return for normal completion.
The original failed report SHA-256 is
`7718fa6566c8f31e9df4d2f289fec35887255b85f09e537232b4c4da4b77fd10`.
The corrected collector passes 72 focused native tests. Its complete capture
`reset-overlay-crc-projection-8b69d4bf-stopfix-total719-total724-20261010.json`
(SHA-256 `f06d2ba8bd9b7829609a8f23b2c8218e01943ef4f8e28068eeebede3e45c0a6c`)
ends at main return frame725/attempt1/tick4, token zero. The measured full
token-to-caller reset interval is 207,439 cycles (43,005 saved versus the
preceding renderer candidate). All six stored renderer snapshots contain the
same 6,240 bytes. One service token is coalesced at frame721; after an explicit
wait, frame722 supplies the next authoritative step. The raw report remains
FAIL because its analyzer still requires the old two-service cadence. A
separate hash-bound, raw-protocol derivation is required before treating this
sample as accepted; the original failed report will not be overwritten.
The immutable candidate ROMs and symbols are in
`artifacts/archive/m5-crc-projection-8b69d4bf-20261010/`.

## Natural third-attempt prefix: timing failure

The first high-water prefix capture naturally executes 1,681 authoritative
steps, proves every held input and both 5,120-byte reset replay snapshots, and
ends at attempt 2/tick 241 on physical frame 1,697. No guest writes or retries
were used. The immutable raw files are
`artifacts/diagnostics/m5-budget/canonical-prefix-highwater-96dec577-attempt2-tick241-20261010.{json,log}`.

The stored protocol result is FAIL: its analyzer incorrectly requires IRQs
that happened before the observed window and expects a renderer publication
to be committed twice when two IRQs interrupt the same engine tick. Separate
hash-bound offline reanalysis now passes the protocol checks; the captured
report is not rewritten. The new artifact is
`canonical-prefix-highwater-96dec577-offline-reanalysis-20261010.json` in the
same directory, SHA-256
`ae164a352ddc145920a51d34f0ff89be53fb2c048a72e131b8b91bd9364f7814`.
It binds the original collector, raw console geometry, reconstructed script,
independently reparsed records, and archived ROM/symbols without an emulator
rerun. Directional engine-counter coverage requires exactly the 30 captured
IRQs; deleting any complete IRQ fails. Renderer publications bind to their
first later commit, including tick-advanced commits; a fabricated second
commit fails. Independent review and 61/61 focused native tests pass.
All 312 original budget failures, raw phase spans, and reset facts remain
exactly unchanged. Neither protocol repair removes the observed timing failures:

| Third-attempt checkpoint | Raw GameplayStep CPU cycles | Limit |
|---|---:|---:|
| 60 | 107,055 | 83,203 |
| 120 | 91,960 | 83,203 |
| 180 | 101,956 | 83,203 |
| 240 | 105,827 | 83,203 |

Raw spans include any intervening IRQs; checkpoint 120 has none, so at least
8,757 cycles of its excess are simulation work, not interrupt accounting.
Each sampled checkpoint projection remains 52,890 cycles. The second reset
uses 258,196 cycles from GameplayStep entry to token coalescing, already above
the 256,010 allowance before adding its main-loop prelude. Maximum sampled
ordinary main span is 157,060 cycles (tick 195), and the last observed IRQ has
22 overruns and 12 queued tokens. These are diagnostics for the next repair,
not a full M5 performance pass.

## Uninterrupted M4 renderer/VBlank and cross-clock gate

Run the dedicated gate only after the final Debug ROM hash is fixed:

```powershell
$h = (Get-FileHash .\build\recursion94-debug.bin -Algorithm SHA256).Hash.ToLower()
.\tools\m4_budget_probe.ps1 -ExpectedSha256 $h
```

The launcher creates two fresh, isolated BlastEm sessions. Each session sets
one breakpoint at the main-side `GameplayCommitAndReset` entry, resumes once,
and runs without post-step, renderer, DMA, or VBlank breakpoints until the
neutral-input attempt reaches its natural tick-720 timeout. It then reads the
five fixed engine/debug/renderer/DMA/audio-shadow blocks twice and fails unless
the raw bytes, fixed addresses and sizes, SHA-256 values, parsed observations,
and both read rounds agree. Both sessions must stop at exactly 720 delivered
68000 VBlanks, before the 16-bit Z80 heartbeat can wrap, and must have zero renderer backlog,
VBlank overrun, dropped-sprite, DMA-reject, DMA overflow/deferred/required-drop,
and DMA-invalid counters; a fully committed queue with no pending frame; and
renderer/DMA capacities within their published bounds. The same existing
72-byte debug read also binds and reports `DEBUG_AUDIO_STATE`,
`DEBUG_Z80_HEARTBEAT`, `DEBUG_AUDIO_COMMAND_COUNT`, and `DEBUG_M2_FLAGS`.
Audio must be playing with the one boot command processed, all M2 health flags
set, and no Z80 timeout. The 16-byte `$FF9900` audio shadow must also have its
`$94`/v1 identity, exact ready+YM+PSG flags, no pause/queue-fault/YM-timeout
flags, zero late ticks, a 1..16 command-ring high-water mark, matching
heartbeat/command count, observed YM and PSG writes, the base layer active, and
the expected boot SFX as its last processed command.

The Z80 heartbeat is the sound driver's hardware V-int clock, independent of
the 68000 VBlank handler count. At the terminal stop it must be greater than or
equal to `DEBUG_FRAME_COUNTER`, and the excess may not exceed 32 V-ints. The
fixed allowance conservatively covers the observed 25-V-int pre-display and
startup phase with seven V-ints of margin; it does not excuse drift accumulated
during gameplay. This cross-clock check supplements the zero-backlog rule and
cannot turn a renderer fault into a pass. GDB `G/M/P/X` packets are forbidden.
The transcript is more restrictive than that minimum: the only accepted command
sequence is the terminal breakpoint, one continue, five fixed reads twice, and
shutdown. Steps, alternate continues, restarts, extra breakpoints, and any other
debugger command invalidate the run.
The report and transcript are written to
`artifacts/reports/m4-renderer-budget.json` and
`artifacts/traces/m4-renderer-budget-gdb-transcript.log`.
The launcher removes both canonical artifacts before any preflight so a failed
or interrupted new attempt cannot leave an old pass in place. Aggregate
validation reparses both trace read rounds, recomputes every check from the raw
bytes, and requires the current ROM, BlastEm executable, default config, symbol
map, schema, collector, and transcript hashes to match capture provenance.

This counter/cross-clock gate detects cumulative renderer faults and excessive
clock divergence, but does not measure exact CPU or VBlank headroom. It does
not cover PAL double-step frames, reset/completion DMA, other encounters,
visual quality, physical hardware, or Release-ROM equivalence. A nonzero
counter from any genuinely uninterrupted run of the same hashes remains a real
fault; a passing run cannot waive it.

## Native 68000 cycle gate

Run the cycle gate only against an intentionally frozen Debug ROM:

```powershell
$h = (Get-FileHash .\build\recursion94-debug.bin -Algorithm SHA256).Hash.ToLower()
.\tools\m4_cycle_probe.ps1 -ExpectedSha256 $h
```

The pinned Windows BlastEm build is a GUI executable whose native debugger
reopens `CONIN$` and `CONOUT$`; redirected pipes, inherited file handles, and a
WSL PTY do not control it. The collector therefore launches an owned isolated
console and uses Win32 console input/output APIs. It never opens GDB, injects
controller input, or issues a debugger command that writes guest memory or
registers. BlastEm exposes the monotonic value as `cycle`; `p/x c` is not
supported by the pinned revision. The value is in Genesis master clocks, so
reports preserve raw master spans and convert to 68000 cycles with a divider of
seven.

Each of two fresh main sessions records the first instruction after the frame
token decrement, the exported terminal GameplayStep return, the
`GameplayCommitAndReset` call and return, the renderer call and return, and
every VBlank handler entry and `rte` opcode. Exact contained handler-body
master spans are subtracted from wall spans before `ceil(master/7)`. Raw wall
values, exclusive values, divider remainders, and crossing counts all remain
in the report. Fixed interrupt-entry and `rte` time is deliberately not
subtracted from main work, so the exclusive main comparison is conservative.
Two additional fresh sessions measure VBlank entry through the `rte` opcode;
the gate then adds 44 original-68000 autovector cycles and 20 `rte` cycles.

The enforced scopes are:

- ordinary and checkpoint simulation: at most 83,203 cycles (65% of the
  128,005-cycle NTSC frame);
- ordinary and checkpoint simulation plus renderer: at most 128,005 cycles;
- terminal GameplayStep: at most 83,203 cycles;
- natural reset call through its return: at most 256,010 cycles (two NTSC
  frames);
- post-reset renderer: at most 128,005 cycles; and
- conservative VBlank interrupt: at most 12,996 cycles (70% of the explicit
  38-line NTSC VBlank denominator).

The report and transcript are
`artifacts/reports/m4-cycle-evidence.json` and
`artifacts/traces/m4-cycle-native-debugger.log`. Both main signatures and both
VBlank signatures must reproduce exactly. Aggregate validation treats this as
the separate required `m4_m68000_cycle_budget` gate and reruns semantic
derivation against current ROM, symbols, commands, raw records, transcript,
and provenance hashes.

The frozen CRC3 Debug-ROM capture (`c689adf9...`) passes and reproduces exactly
across both main sessions and both VBlank sessions. The deliberate stage-3
acknowledge-only startup interrupt is not an authoritative VBlank and is
excluded by identical entry/shared-`rte` stage-4 conditions; every certified
span is the full RUNNING handler from actual vector entry through the shared
`rte`. VBlank-body-exclusive checkpoint simulation peaks at 55,020 cycles and
checkpoint simulation plus renderer peaks at 125,316 of 128,005 cycles (2,689
cycles headroom). Ordinary simulation plus renderer peaks at 83,171 cycles.
The tick-1 and tick-2 renderer paths cost 70,284 and 70,226 cycles in both
sessions; neither takes the former 520-cycle shortcut. Terminal GameplayStep,
natural reset, and post-reset renderer peak at 54,774, 154,380, and 78,694
cycles. The standalone conservative VBlank cost peaks at 10,087 of 12,996
cycles. The canonical report and trace SHA-256 values are
`c64758db76dfbb1897105568fa19b33a6598639c22699870bbbc91287d91aa13`
and
`8914a94e5bf3df808fa01db698e2debb6ef514bf766bbbd9a5ac6f4c03e3a4f3`.

The archived `c2d71b49` Debug-ROM capture is an intentional failed fixture at
`artifacts/archive/m4-cycle-overrun-c2d71b49/`. It reproduced in both sessions:
ordinary total work peaked at 83,173 cycles, checkpoint simulation at 59,624,
and checkpoint total at 129,920, exceeding one NTSC frame by 1,915 cycles.
Terminal step/reset/post-reset-renderer costs were
59,378/158,982/78,694 cycles and passed their separate limits. Conservative
VBlank peaked at 11,780 of 12,996 cycles. This archived result cannot be used
as a pass for another ROM hash.

This gate covers the neutral-input B01 Debug-ROM path in a pinned emulator. It
does not establish physical-hardware timing, PAL double-step headroom, other
encounters or bosses, or Release-ROM equivalence.
