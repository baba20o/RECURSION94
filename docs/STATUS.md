# Status

- **Current workstream:** 5 — core game vocabulary evidence freeze
- **Frozen milestone evidence:** 0 — environment/bootstrap; 1 — minimal
  cartridge; 2 — hardware foundation; 3 — deterministic engine core; 4 —
  echo vertical slice. The M4 automated evidence freeze is complete; literal
  human hands-on play and subjective listening are not claimed by it.
- **Current M5 candidate:** bounded CRC/projection optimization has measured
  checkpoint gains and sampled replay parity. The word CRC uses a
  generated big-endian index and an internal halfword-byte-swapped state,
  restoring the ordinary raw-CRC ABI at every exit. Four-word unrolling,
  layout-asserted contiguous copies, monotonic projection scan bounds, and a
  direct 27-zero-record transition preserve the fixed projection schema.
  Canonical Debug checksum/SHA-256: `$DA08` /
  `e9ea22b6192c8cddef195a854737ddc7c16344355758bcd0260ac5766c59687b`;
  symbols `cf293ed2fc64f2dd11b103ccbc13e27a0ea34a8218abca175b71d62fe5c5189e`.
  Canonical Release checksum/SHA-256: `$4B2D` /
  `2bbd22cf6165a6af1313694bd223fc5cd904461e5fe6d25f5a0a75e57e8b699d`;
  symbols `06e0c901bbba92e09d848558c48bde65557d1ad791c6f670258cc2595ca063ad`.
  B03 Debug checksum/SHA-256: `$0D28` /
  `8b69d4bf62cc286123632619daad1422cd7076d4620154900d6a8eade5ede4c5`;
  symbols `d81234dd46b0ee9744f94e8dbd37b04df8838e1237bdb77a41e456f4911eb131`.
  All three images remain 1,048,576 bytes; all three builds and static ROM
  checks pass. Native CRC, pool-bound, and source-contract tests pass 77/77
  with no failures, errors, or skips. This includes the exact rebuilt-ROM
  table-blob test and warning-free standalone assemblies. Generated CRC
  tables occupy 312,320 bytes, SHA-256
  `45a0f0cef6467c656dda8399a01c6d2ab73ca0e1b06236cc7addcbf8fc2f458a`.
  ROMs and symbols are archived under
  `artifacts/archive/m5-crc-projection-8b69d4bf-20261010/`.
  Full native host suites also pass: M3 59/59, M5 165/165, and Render 82/82, with exact
  reports `host-m3-crc-projection-20261010.json` and
  `host-m5-crc-projection-20261010.json` plus
  `host-render-crc-projection-20261010.json` under `artifacts/reports/`.
  The additive diagnostic profile passes 70/70 focused native tests; all
  older profiles and non-breakpoint marker programs remain unchanged.
  Native Windows All now passes 1,011/1,011, with zero failures, errors, or
  skips (`artifacts/reports/host-tests-crc-projection-20261010.json`). Its
  release gate remains false: save, full campaign replays, and live release
  validation are still pending. Independent review clears the exact probe
  boundaries and scripts for this ROM.
  The regular native probe passes: tick60 simulation falls from 80,828 to
  65,866 cycles, projection from 52,890 to 37,928, with exact sampled CRC
  parity and unchanged renderer spans. Its report SHA-256 is
  `86e340fdd89a88f8e144ababced0d145b5de61e854112511a739574583ee2e44`.
  The natural 1,681-step prefix passes protocol/input/replay checks. Its four
  IRQ-free three-actor checkpoint spans are 82,096 / 77,000 / 76,908 / 80,778,
  all below 83,203, with all four projection CRCs unchanged. Both 5,120-byte
  replay snapshots match. Reset lower-bound intervals fall to 205,945 and
  215,195 cycles with one service VBlank each; these are not complete reset
  acceptance measurements. The prefix still reports 66 ordinary-frame,
  cadence, queue, or blank-budget violations and five cumulative overruns.
  Final frame is 1,683 with token zero. Report SHA-256:
  `5fe84910c9c19905d2916cad41983e0e0fbca665328cde2613bdccdac1ae3148`.
  The first extended-reset probe stops prematurely at frame721/attempt1/tick720:
  its ISR completion condition mistakes transitional reconstruction state for
  a finished attempt. The failed raw report is preserved (SHA-256
  `7718fa6566c8f31e9df4d2f289fec35887255b85f09e537232b4c4da4b77fd10`).
  A candidate-only collector repair is in progress; no reset-window pass is
  claimed. All three raw pairs are under `artifacts/diagnostics/m5-budget/`
  with the `crc-projection-8b69d4bf` stems. Complete reset/graphics parity,
  ordinary timing repair, full current-ROM replays, and M5 aggregate remain required.
- **Preceding renderer-HWM candidate:** seven read-only campaign renderer scans use
  clamped, zero-guarded historical pool bounds; legacy renderer paths and
  gameplay are unchanged. All images are 1,048,576 bytes. Canonical Debug
  checksum/SHA-256: `$C1BE` /
  `bd21be6718d33dca944982d1c748f31f752251bb8ca099c22b9de889c71d20de`;
  symbols `5d156d07a9075219830889a176bb5758ca18e9973742e10b5710743568162260`.
  Canonical Release checksum/SHA-256: `$33E5` /
  `0570d12f0dae8bfc13e1f90bf20cac13cb77a6d6b77a23af037eb3440eca4efb`;
  symbols `31d38e46454d3cfc503a7a0bc9395216cf95b3a6dbd803acf18e92d0580c20b6`.
  B03 Debug checksum/SHA-256: `$F4D6` /
  `e6778f907da9f308cb587de695cdba5dc32371538e92ccd34676821428bf3c65`;
  symbols `a67cba2c05ca4b1920c51e00c4022b2d993c408b3c5b9899e511bfc0d90dc6f6`.
  Three builds and independent statics pass; 73 focused native tests pass,
  including the shared allocation proof and six new renderer-bound tests.
  The separately reviewed diagnostic profile and failure preservation pass
  65/65 focused native tests without changing any older profile or timing limit.
  The three prior fixed-capacity source assertions now require the typed bound,
  capacity clamp, and zero exit. Images/maps are archived under
  `artifacts/archive/m5-renderer-hwm-e6778f90-20261010/`.
  The first regular and extended-reset native samples pass protocol and their
  sampled budgets. Tick59/61 rendering saves 6,460/6,462 cycles; total722 saves
  5,310. All 6,240 renderer bytes and sampled projection CRCs match the preceding
  image. Simulation/projection and the reset main interval are unchanged.
  The natural 1,681-step prefix also passes protocol/input/replay and all four
  projection CRC comparisons, but reports 70 budget violations. IRQ-free
  third-attempt simulation at ticks60/120/180/240 is
  97,060/91,962/91,870/95,740 against 83,203; reset2 remains 258,196 against
  256,010. Final physical frame is 1,685 with token zero, while intermediate
  cadence failures and five counted overruns remain. Its immutable report is
  `canonical-prefix-renderer-hwm-e6778f90-attempt2-tick241-20261010.json`, SHA-256
  `d1e86bebd1ddab8577c654ec18d01cd03375c5b8cc9e02c638fe5f35a1e93b40`.
  Current-ROM full replays, Windows All, and M5 aggregate evidence remain
  pending. This is a bounded measured rendering gain, not a full M5 performance
  pass. Its independently reviewed paired comparison is
  `renderer-hwm-timing-parity-96dec577-to-e6778f90-20261010.json`, SHA-256
  `ac60c0f84054e491252ba820e830017e15cdb1800bfb6137b08bfcc182bb9a80`.
  The comparison helper passes 67/67 focused native tests and explicitly
  excludes later-attempt/full-route timing certification.
- **Preceding high-water ROMs:** all images are 1,048,576 bytes. Canonical Debug
  checksum/SHA-256: `$1A55` /
  `9b2122f405dc7c6a2692eea2250ac18acff3ded08804aa67ab72316cc1b86d34`;
  symbol map:
  `79400e37fe90553891a010d5714c073f8ce545b92db3f6242e48bce881c56f1d`.
  Canonical Release checksum/SHA-256: `$8C7C` /
  `9591acc588a60e6217330e810216950df43761ef2a74cd4914f788a407b572d6`;
  symbol map:
  `bba032fce4f3e3d98ce2da81c314b64170fc44655362a08646876ed415c04761`.
  The dedicated B03 Debug fixture checksum/SHA-256 is `$4D6D` /
  `96dec5774bcda0d5d0fc1029a051122a8cc9e5f9b2bf62fcba95b87ede4dd11f`;
  symbol map:
  `6da707bacf8436911b5583f954258c4f9aa54c7a222ba8bf51fe46dc0e795adb`.
  These add ten high-water-bounded gameplay scans to the measured lookup,
  sprite, indexed-CRC, and whole-empty projection repairs. Three builds and
  independent statics pass; 143 focused native Windows tests passed before
  the build-only integration repair. Code growth exposed three out-of-range
  relative calls in display-off renderer initialization; those calls now use
  explicit absolute-long JSR, with a new source regression. The current
  first-attempt tick60 and extended reset diagnostics pass their protocol and
  sampled timing checks, with exact sampled parity. Later multi-echo timing and
  full current-ROM replay/visual/aggregate recapture remain pending. Images/maps
  are archived under `artifacts/archive/m5-high-water-96dec577-20261010/`.
  The preceding
  replay-proven images/symbols are preserved under
  `artifacts/archive/m5-pre-timing-140039e3-20261010/`; their reports are not
  current-candidate passes.
- **Historical ROM identity:** the frozen M2–M4 Debug/Release hashes remain
  `c689adf9...883d2460` and `0cf94ab5...90f9d43` under
  `artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5/`. M5 validation checks
  those immutable bytes and frozen reports as historical evidence; it never
  relabels them as evidence for a current M5 ROM.
- **Latest validation:** `.\tools\validate.ps1 -Scope Milestone3` — PASS
  17/17; `.\tools\validate.ps1 -Scope Milestone4` — PASS 23/23. Both
  required-gate summaries record zero failed and zero pending gates.
- **Current focused host/static tests:** renderer plus M5 tests 203/203 after
  the runtime repairs. On 2026-10-10 the project-local Windows Python reran
  B01 evidence 7/7 and B03 evidence 18/18. Budget, visual, aggregate, and the
  authoritative Windows all-suite count remain pending while their evidence
  contracts settle. A WSL Python run crashed in the host interpreter; it is
  not counted as passing evidence.
- **Resumed collector tests:** Windows B03 input collector 22/22; Windows
  aggregate module 55/55 before the added cycle gate, followed by both affected
  cycle-gate tests 2/2. Visual collector 45/45 under WSL. Final Windows All
  remains pending and none of these focused results substitutes for it.
  The lookup-only candidate passes 19/19 controller/lookup tests including
  warning-free assembly, and the combined Windows renderer/integration/lookup
  rerun passes 58/58. A direct embedded-Python `-m unittest` invocation could
  not import the repository; the passing rerun explicitly adds its root.
  The sprite-loop candidate passes Windows renderer/integration/campaign
  renderer tests 46/46. The indexed-CRC candidate passes the full native CRC
  suite 25/25, including source binding, standalone original-68000 assembly,
  table generation, arbitrary seeds, odd alignment, and full-width indices.
  Whole-empty projection coverage expands that suite to 28/28, with a second
  standalone assembly fixture covering projection, exact source binding,
  reset initialization, prior active records, reuse, and malformed-domain
  rejection. Runtime measurement remains separate.
  The first high-water Windows All run discovers 879 test entries with 33
  errors and zero skips, preserved in
  `artifacts/reports/host-tests-high-water-20261010.json`: 29 errors derive
  from a stale reviewed renderer pin, two legacy timing tests use the changed
  M5 main loop, one old CRC source assertion expects the pre-indexed loop,
  and one import error exposes the missing native `jsonschema` dependency.
  After explicit legacy-fixture/CRC assertion repairs, 103 focused native tests
  pass. The renderer sprite pin was refreshed only after independent exact
  source-delta, archived-opcode, raw-buffer, and CRC-parity review; native visual
  45/45 and aggregate/validator 125/125 now pass. The reproducible schema
  dependency repair is now implemented; an import failure does not count the
  affected module's undiscovered tests as passing.
  Resumed native bootstrap verifies all 35 original embedded-Python files and
  the unchanged isolated `_pth`, then installs six hash-pinned distributions
  without global Python or pip. A second bootstrap changes zero package files;
  offline verification checks 138 installed files and 39 real import caches.
  Actual Draft 2020-12 validation, local references, invalid-input rejection,
  and the native `rpds` dependency pass. The post-import verification is in
  `artifacts/reports/toolchain-python-packages-post-import-20261010.json`.
  All three ROMs rebuild and independently validate at the identical hashes
  above. The next full run discovers 979 tests with zero errors/skips and six
  failures (`host-tests-high-water-dependencies-20261010.json`): one new
  directory-case fixture also incorrectly changes the module basename; five
  newly importable budget tests mix the frozen pre-timing contract with current
  mutable ROM/symbol files. These test-fixture repairs do not refresh production
  budget pins or establish a current-ROM budget pass. The final coherent native
  rerun passes **982/982**, with zero failures, errors, or skips, recorded in
  `artifacts/reports/host-tests-high-water-verified-20261010.json`.
  Exact command: `.toolcache/python/python.exe tools/run_host_tests.py --root .
  --suite All --report artifacts/reports/host-tests-high-water-verified-20261010.json`.
  Its release gate remains false: save, full campaign replays, and live release
  evidence are unfinished. The new 1,681-step later-attempt timing diagnostic
  has independent review and 55 focused native tests. Its first live capture
  completes all 1,681 PRE/POST input proofs and both
  5,120-byte reset replay snapshots match; final attempt 2/tick 241 is physical
  frame 1,697. The stored report remains protocol FAIL and budget FAIL:
  two IRQ-boundary/publication-consumption assumptions are corrected only in
  a separately derived offline report, and real timing overruns remain.
  The independently reviewed correction passes 61/61 focused native tests;
  `canonical-prefix-highwater-96dec577-offline-reanalysis-20261010.json`
  records protocol PASS, all 30 observed IRQs anchored, and budget FAIL with
  all 312 original budget errors and raw timing spans exactly unchanged.
  Its SHA-256 is `ae164a352ddc145920a51d34f0ff89be53fb2c048a72e131b8b91bd9364f7814`.
  No emulator rerun was used for that derivation. Checkpoint 120 alone takes
  91,960 CPU cycles with no intervening IRQ, above the 83,203 limit. Reset 2
  takes at least 258,196 cycles versus its 256,010 allowance. Original report
  and console are immutable at
  `artifacts/diagnostics/m5-budget/canonical-prefix-highwater-96dec577-attempt2-tick241-20261010.{json,log}`.
  No retry, guest writes, or current-ROM performance pass is claimed.
  Its first reset capture timed out before gameplay because READY was followed
  by a nonempty debugger prompt (`>  y`). The exact 40-line failure tail is
  preserved as `reset-overlay-pool-25a74bd2-ready-timeout-20261010.txt` in the
  budget diagnostics; the original full console was not retained. The helper
  now preserves full console data on timeout, with 31 diagnostic tests passing.
  The prompt text's origin is unproven; no timing result is inferred from it.
  A subsequent regular checkpoint attempt also stopped before gameplay at
  READY (`> oug)`), preserved in
  `checkpoint-pool-25a74bd2-ready-timeout-20261010.txt`. Both screen readers
  include cells beyond the cursor on the final row, so stale screen cells are
  a concrete alternative to extra input. A bounded READY-only raw-buffer,
  cursor, and non-consuming input-queue snapshot is being prepared before
  another timing run; no prompt or timing acceptance has been relaxed.
  That READY-only snapshot was clean (cursor column 2, empty input queue),
  so it did not reproduce or explain the suffixes. A subsequent fresh regular
  and reset pair passes every unchanged protocol check; failures remain
  preserved and are not timing evidence.
- **Latest pre-optimization M5 budget/visual runs:** budget-v3 host coverage passes 58/58,
  but its live run fails at authoritative total723 / physical frame725:
  six DMA records /520 words reach a handler whose entry status lacks the
  VBlank bit (PERF-002), despite a zero overrun counter. The failed evidence
  is preserved in `artifacts/archive/m5-budget-v3-reset-overlay-late-vblank-140039e3-20261010/`.
  Visual capture passes its first target internally, then fails closed when
  the next target would be overwritten before native screenshot presentation.
  Physical-frame drift grows from +143 at total1507 to +184 at total1636;
  no final visual pass or retained first-target PNG is claimed.
- **Emulator RAM evidence:** isolated Windows BlastEm sessions prove a standard
  three-button pad and disconnect behavior, held/pressed/released edges,
  opposite-axis neutralization, sprite movement, a 320-word required SAT DMA,
  zero DMA errors, live Z80 sequencing, and advancing heartbeat/status
- **Determinism evidence:** two fresh isolated BlastEm sessions observed every
  authoritative tick 0–720, matched 13 independently recomputed CRC
  checkpoints, remained active through 719, terminated at 720, restored all
  16,384 pool/world bytes, and rebuilt the exact echo/current actor roster
  through the shipped automatic timeout commit/reset path, with 26/26 live
  gates and no debugger writes
- **M4 runtime evidence:** the live 23/23 report records a real standard-pad
  `Right×32 + neutral×8` partial attempt, Start+C reset, then
  `Right×165 + Right+B`; the second attempt reaches `PROGRAM STABLE` at tick
  166 with terminal CRC `$8762E745`, and fresh sessions reproduce it without
  debugger writes.
- **M4 visual evidence:** the 23/23 report binds five distinct exact 320×240
  native PNGs: zero echo, reset trace, echo-held open door, program stable, and
  a blocked current-only path. Structural decoding and the independent rubric
  pass; the separate 7/7 controller-driven QA is automated and is not
  represented as literal human hands-on testing.
- **M4 audio evidence:** the 17/17 schema-v2 report binds a 1.4186-second VGM
  with SHA-256
  `92db7981f9804659e6b4bb200e6bc410ea092112c291b95626fd4364e42a86bb`.
  It proves valid timing/clocks, audible chip-write events, the post-echo layer
  transition, bounded percussion pulses, and clear driver faults. This is
  structural capture analysis, not a subjective listening review.
- **M4 budget evidence:** two fresh uninterrupted sessions naturally reach
  tick 720. Each records 440 committed DMA words, 11 sprites, a maximum of
  four sprites on one scanline, and zero renderer, queue, VBlank, DMA, audio,
  or Z80 fault counters. The separate native-cycle report passes all 9/9
  simulation, transition, and VBlank cycle-budget checks.
- **M4 report freeze:** runtime 23/23, uninterrupted renderer budget 15/15,
  native cycles 9/9, visual 23/23, audio 17/17 (`r94-m4-audio-evidence-v2`),
  and controller-driven QA 7/7; all live reports are bound to the frozen Debug
  ROM above.
- **M5 B01 live evidence:** 11/11 checks pass in two fresh processes against
  the preceding canonical Debug `b6401e90` (2026-10-10). The exact 120-step real-pad schedule,
  ABI-v3 B01 initialization, extended projection CRCs, deterministic
  checkpoints, source bundle, symbol map, and transcript are hash-bound, with
  zero debugger writes.
- **M5 B03 live evidence:** preceding-ROM `140039e3` recapture passes 15/15 evidence checks
  and all 11 acceptance claims (2026-10-10). The native route uses two fresh
  parity sessions plus a separate
  zero-echo negative session and performs two exact 720-step timeout commits,
  echo-owned security-node damage, door opening, current dash traversal, and a
  20-step current-only terminal hold completing at tick 292. Exact replay RAM,
  source, ROM/symbol, and transcript hashes are retained; debugger writes are
  zero. The prior report, trace, and replay bytes are
  preserved under `artifacts/archive/m5-b03-runtime-pre-140039e3-20261010/`.
  Three resumed captures failed exact input proof at different held-key
  samples; the second completed one full positive route before the other
  session failed. Passive foreground polling found no sampled focus loss in
  the third failure. Raw diagnostic evidence is under
  `artifacts/diagnostics/m5-runtime/`; host input delivery is being isolated,
  with no ROM edits or guest-state repair. The final collector reasserts held
  keys before each next-frame proof, without release pulses or retry after a
  mismatch, and succeeds through observed foreground changes. Its runtime
  report SHA-256 is
  `605f9118498908e1c263688abca72d833e993b27d22d00a08676577e7842798f`;
  passive observer, focus log, raw report, and metadata enrichment are
  explicitly disclosed and hash-bound in provenance.
- **M5 aggregate validation:** `Milestone5` is a fail-closed scope with a
  dedicated B03 build/static check and current-ROM B01/B03 gates. Hardware
  budget, five-state objective visual capture, and an independent visual
  critic remain required; M5 cannot pass while any is missing, stale, failed,
  or semantically inconsistent. Release retains every later pending gate.
- **M5 cycle budget:** the 2026-10-10 `140039e3` tick-60 diagnostic fits total
  main-side work in one frame but measures 107,428 simulation cycles, above
  the separate 83,203-cycle design threshold by 24,225 cycles (PERF-001).
  Its zero-write diagnostic protocol passes, not the simulation budget.
  A measured repair remains required; the counter/capacity report cannot
  establish CPU-cycle compliance and the aggregate has a required pending
  `m5_b03_native_cycle_budget` gate.
- **Measured lookup repair:** B03 `9a741642` reduces tick60 to 101,332 cycles
  (6,096 saved, still 18,129 over the limit). Six raw sprite/HUD/overlay
  snapshots totalling 6,240 bytes and all measured projection CRCs match the
  preceding ROM exactly. The post-reset renderer still overruns blank.
  `lookup-only-timing-parity-140039e3-to-9a741642-20261010.json` records
  protocol/parity PASS and budget FAIL.
- **Measured sprite repair:** B03 `09be6696` reduces total722 SAT work by
  5,072 cycles (55,148 to 50,076), while tick60 remains 101,332. All 6,240
  captured sprite/HUD/overlay bytes and projection CRCs match the lookup
  candidate. Reset-overlay overruns remain. The paired report
  `sprite-loop-timing-parity-9a741642-to-09be6696-20261010.json` records
  protocol/parity PASS and budget FAIL; tightened WAIT/IRQ diagnostic tests
  pass 26/26. No M5 pass is claimed.
- **Measured indexed-CRC repair:** B03 `c051995c` reduces tick60 by 4,638
  cycles to 96,694 (still 13,491 above the limit). Projection costs 59,672;
  all sampled CRCs and 6,240 renderer bytes match the sprite candidate.
  Reset-overlay overruns remain.
- **Measured empty-pool projection repair:** B03 `25a74bd2` reduces tick60
  by 6,782 cycles to 89,912 (still 6,709 above the limit), with projection
  52,890. The reset main interval saves 13,559 cycles; service cadence stays
  at two VBlanks. Sampled CRCs and all 6,240 renderer bytes match. Ordinary
  post-reset renderer overruns remain; gameplay scan bounds are next.
- **Measured high-water gameplay repair:** B03 `96dec577` reduces tick60 to
  80,828 cycles, saving 9,084 and leaving 2,375 below the 83,203 limit.
  Projection remains 52,890. Timeout total720 gameplay takes 78,380; the reset
  main interval takes 250,444. At total722 gameplay takes 34,070 and the
  unchanged masked renderer 71,428, with whole main 107,448. Both bounded
  diagnostics pass protocol and sampled budget checks; the reset fixture no
  longer reaches late IRQ727 or commits outside blank. Its two reset-service
  VBlanks and coalesce count are unchanged. All sampled CRCs and six raw
  renderer buffers (6,240 bytes) match the empty-pool candidate exactly.
  The paired comparison is
  `high-water-timing-parity-25a74bd2-to-96dec577-20261010.json`, SHA-256
  `23d78a1fdcdb7309047d5bea443b045ecd38e4d7b9132064d4d53eac583fde66`.
  This is not a full M5 cycle pass: later multi-echo checkpoints, the canonical
  budget route, current-ROM replays, visual evidence, and critic remain pending.
- **Additional contract finding:** HW-003 records excess RUNNING interrupt
  responsibilities under the repository's strict ISR policy. The historical
  automated M4 reports remain intact, but do not waive that release blocker.
- **Known tooling limitation:** BlastEm's hidden Windows `-b` headless path
  exits with access violation `0xC0000005`; isolated GDB, screenshot, and VGM
  sessions are the stronger passing runtime proof
- **License limitation:** vasm 2.0f is usable here only for non-commercial
  purposes unless written permission is obtained; commercial exploitation
  requires permission or migration to the verified GNU Binutils path
- **Next action:** measure the built CRC/projection candidate against the preserved
  renderer-HWM checkpoint/reset/prefix baselines, requiring exact byte/CRC parity.
  Resolve the remaining timing failures before
  rerunning objective HUD/SAT captures and budget-v3, independently review the
  visuals, run Windows All and `Milestone5`, then
  freeze M5 only if every required gate passes

This file records evidence, not aspiration. The release scope remains
incomplete, and the deferred human-play and subjective-listening requirements
remain explicit release work.
