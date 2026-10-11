# Requirement-to-evidence matrix

Boot/hardware and M0–M4 passes below describe their frozen historical ROMs,
not the current M5 candidate. Only rows with current candidate hashes establish
current build/static results; affected full-runtime recapture remains required.

| Requirement | Test/evidence | Command | Latest result |
|---|---|---|---|
| Pinned host tools | Manifest checksum/version and exact-byte assembler smoke | `.\tools\bootstrap.ps1` | PASS 2026-07-26 |
| Current M5 Debug/release assembly | Canonical dual build | `.\tools\build.ps1 -Configuration Debug`; Release | PASS — high-water candidates Debug checksum `$1A55`, SHA-256 `9b2122f4...b86d34`; Release checksum `$8C7C`, SHA-256 `9591acc5...b572d6`; timing/replay recapture pending |
| Dedicated B03 Debug fixture | Debug build with compile-time encounter selection plus independent static validation | `.\tools\build.ps1 -Configuration Debug -Level B03`; `tools/validate_rom.py` | PASS — high-water checksum `$4D6D`, ROM SHA-256 `96dec577...4dd11f`, symbol SHA-256 `6da707ba...795adb`; aggregate remains pending |
| Frozen M2–M4 history | Exact archived Debug/Release ROMs, static integrity, frozen report bytes/check vectors, and B01 replay | `.\tools\validate.ps1 -Scope Milestone5` | PASS — historical hashes remain `c689adf9...883d2460` / `0cf94ab5...90f9d43`; no current-ROM equivalence is claimed |
| Current Debug/B03 symbol maps | Hash-bound public-symbol ABIs used by live collectors | `.\tools\validate.ps1 -Scope Milestone5` | Current canonical `79400e37...c56f1d`; B03 `6da707ba...795adb`; aggregate recapture pending |
| Header/vector/checksum | Binary parser tests and three current static reports | `.\tools\test.ps1 -Suite Rom`; `.\tools\validate.ps1 -Scope Milestone5` | PASS — canonical Debug/Release and dedicated B03 images |
| RAM/VRAM contracts | Generated reports and static assertions | `.\tools\validate.ps1 -Scope Milestone4` | PASS — fixed ownership and boundary assertions |
| Current focused host/static suites | M5 mechanics plus B01/B03/budget/visual/aggregate contracts | Targeted host runs | PASS — renderer plus M5 203; Windows B01 7 and B03 18 on 2026-10-10. Budget/visual/aggregate and final Windows All pending evidence-contract freeze |
| Emulator heartbeat | Hash-bound consecutive GDB VBlank samples | `.\tools\validate.ps1 -Scope Milestone4` | PASS — 11 checks |
| Boot visual | Isolated current-ROM capture and full PNG decode | `.\tools\capture.ps1 -NoBuild` | PASS — 9 checks; `artifacts/reports/boot-screenshot.json` |
| M2 controller | Real keyboard-to-pad injection, edge/held/disconnect fixtures | `.\tools\m2_probe.ps1` | PASS — 31/31 checks |
| M2 video and DMA | SAT movement, link/coordinates, budgets and fault counters | `.\tools\m2_probe.ps1` | PASS — 21/21; 320 words, no errors |
| Z80/YM2612/PSG runtime | Upload/status/command/heartbeat and chip-write counters | `.\tools\m2_probe.ps1` | PASS — 17/17 checks |
| Debug fatal path | Real Start+A+B+C, guest exception, exact red/white screen, masked STOP | `.\tools\m2_fatal_probe.ps1` | PASS — 14/14 checks |
| YM2612/PSG capture | Dynamic, timed VGM parser and isolated live capture | `.\tools\capture.ps1 -Audio -NoBuild` | PASS — 6/6 checks; 3.872 s |
| M2 clean aggregate | Clean rebuild plus static/runtime/visual/audio evidence | `.\tools\validate.ps1 -Scope Milestone2` | PASS — 15/15 required gates |
| M3 host reference | Independent fixed-point/pool/reset/projection/CRC model | `.\tools\test.ps1 -Suite Milestone3` | PASS — 43 M3 tests in the current all-suite run |
| M3 live determinism | Two fresh sessions; every tick; independent CRC; automatic timeout reset | `.\tools\m3_probe.ps1` | PASS — 26/26 gates |
| M3 clean aggregate | Clean dual build, 557 host tests, M2 dependencies, M3 host/live evidence | `.\tools\validate.ps1 -Scope Milestone3` | PASS — 17/17 required gates; 0 failed/pending |
| R94R replay codec | Raw/RLE round trip, integrity, bounds, chronology, adversaries | `.\tools\test.ps1 -Suite Replay` | PASS — 31 replay tests; campaign-wide canonical replays remain pending |
| M4 host reference | Independent replay/echo/B01 behavior and adversarial model | `.\tools\test.ps1 -Suite Milestone4` | PASS — 38 M4 tests in the current all-suite run |
| M4 live canonical replay | Real pad inputs, partial commit, echo-held switch, current crossing, independent CRC | `.\tools\m4_probe.ps1` | PASS — 23/23; attempt scripts `Right×32 + neutral×8`, then `Right×165 + Right+B`; terminal tick 166, CRC `$8762E745` |
| M4 uninterrupted renderer/DMA budget | Two fresh natural attempts with only the terminal breakpoint | `.\tools\m4_budget_probe.ps1` | PASS — 15/15; both reach tick 720; 440 DMA words, 11 sprites, scanline max 4, zero faults |
| M4 native cycle budgets | Native debugger master-clock spans for simulation, reset/transition, renderer, and VBlank | `.\tools\m4_cycle_probe.ps1` | PASS — 9/9 cycle checks |
| M4 visual evidence | Five isolated native screenshots, exact hash bindings, structural decode, distinct-state and rubric checks | `.\tools\m4_visual_probe.ps1` | PASS — 23/23; five exact 320×240 PNGs: zero echo, reset trace, echo-door-open, program-stable, blocked-path |
| M4 audio evidence | Real-pad acquisition, VGM parsing, layered motif transition, bounded pulse and fault checks | `.\tools\m4_audio_probe.ps1` | PASS — 17/17, schema `r94-m4-audio-evidence-v2`; 1.4186 s; SHA-256 `92db7981...e42a86bb` |
| M4 controller-driven QA | Automated independent playability observations bound to the visual sessions | `.\tools\m4_visual_probe.ps1 -FinalizeExisting` | PASS — 7/7; explicitly automated, not literal human hands-on testing |
| M4 clean aggregate | Clean dual build, 557 host tests, current hash-bound M2/M3 dependencies and M4 reports | `.\tools\validate.ps1 -Scope Milestone4` | PASS — 23/23 required gates; 0 failed/pending |
| M5 deterministic mechanic fixtures | Independent bounded host/reference models for shooting, dash/phase, carry/socket, turret/guard, conveyors, beam, timeline/replacement, interaction hold, and stage result semantics | `.\tools\test.ps1 -Suite Milestone5` | PASS — 114 focused M5 tests |
| M5 B01 current-runtime sanity | Two fresh standard-pad sessions; ABI-v3 initialization, movement/input edges, extended projection/checkpoints, no false completion | `.\tools\m5_probe.ps1` | PRECEDING-ROM PASS — 11/11 on 2026-10-10 against canonical Debug `b6401e90...a9aa9d28`; zero debugger writes; current candidate recapture pending |
| M5 B03 combined multi-echo room | Two 720-step commits, two echoes, echo-owned node damage, door/dash route, exact 20-step current-only terminal hold, zero-echo negative, replay RAM, parity | `.\tools\m5_b03_probe.ps1` | PRECEDING-ROM PASS — 15/15 +11/11 on `140039e3` (2026-10-10); bounded held-key reassertion, exact PRE/POST input proofs, zero guest writes; report SHA `605f9118...42798f`; lookup candidate recapture pending |
| M5 B03 hardware budget | Combined-room renderer/DMA/sprite/pool/audio fault and threshold measurements with semantic revalidation | `.\tools\m5_budget_probe.ps1` | FAIL — v3 current-ROM capture stops at total723 / physical725: six records /520 words but VBlank entry status lacks bit3; PERF-002. Host contract/diagnostic suites58/58 pass, not a hardware pass |
| M5 simulation-cycle threshold | Current-ROM native cycle measurement, separate from counter/capacity checks | Checkpoint-phase diagnostic, then later-attempt coverage | SAMPLED PASS — high-water `96dec577` tick60 80,828 versus 83,203; extended reset diagnostic also passes, with exact sampled CRC/raw-render parity. Full multi-echo cycle gate remains pending |
| M5 B03 objective visual set | Five fresh-process native captures with exact state/frame/PNG/pixel/provenance bindings; no collector self-scoring | `.\tools\m5_visual_probe.ps1` | FAIL-CLOSED — second scene would be overwritten before screenshot presentation; growing physical-frame drift requires timing repair before recapture. Focused collector45/45 passes, not a visual pass |
| M5 independent visual critic | Prompt §42 twelve-category rubric, every score ≥4, average ≥4.2, no critical readability defect or severe sprite corruption | Independent review of the exact objective capture manifest | Pending — required report `artifacts/reports/m5-b03-visual-critic.json`; objective collector cannot satisfy this gate |
| M5 clean aggregate | Current canonical Debug/Release and B03 fixture builds/statics, host tests, frozen prior evidence, B01/B03 live reports, counter/cycle budgets, objective captures, independent critic | `.\tools\validate.ps1 -Scope Milestone5` | Incomplete until counter/cycle budget, visual, and critic gates all exist and pass semantic/hash validation |
| Literal human hands-on play | Human play session and recorded findings | Manual | Pending — no literal human hands-on session is claimed by automated controller QA |
| Subjective audio listening | Human listening review of captured music/SFX | Manual | Pending — VGM timing/event analysis is not subjective listening |
| Campaign determinism | Runtime-backed canonical completion replays beyond the M4 room | `.\tools\test.ps1 -Suite Replay` | Not run |
| Save integrity | Host vectors and emulator round trip | `.\tools\test.ps1 -Suite Save` | Not run |
| Twenty encounters | Strict content manifests/compiler | `.\tools\test.ps1 -Suite Content` | PASS — 20 reachable entries, 15 primary/5 bosses; runtime replays pending |
| Release-wide visual rubric | Deterministic captures and independent scores beyond the M4 room | `.\tools\capture.ps1` | Not run |
| Release-wide audio rubric | Deterministic captures and listening review beyond the M4 room | `.\tools\capture.ps1 -Audio` | Not run |
| Release contract | Aggregate clean release gate | `.\tools\validate.ps1` | Not run |

The matrix will expand to one row per encounter and release gate as fixtures
land. The optional Windows headless-throughput probe currently fails in
BlastEm with `0xC0000005`; current isolated GDB, screenshot, and VGM evidence
does not depend on that path. The scoped M4 validator passes its implemented
23 automated gates; this does not convert the separately listed literal
human-play or subjective-listening work into completed evidence.
