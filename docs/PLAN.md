# Delivery plan

| Milestone | Acceptance evidence | State |
|---|---|---|
| 0. Environment and mission bootstrap | Pinned tools bootstrap; smoke assemblers; durable contracts; emulator resolved | Complete |
| 1. Minimal cartridge | Static ROM checks; heartbeat reached; stable screenshot; reproducible clean build | Complete — 9/9 gates |
| 2. Hardware foundation | Video, controller, VBlank, sprite, DMA, Z80/YM2612/PSG fixtures | Complete — 15/15 gates |
| 3. Deterministic engine | Repeat hashes; canonical reset; bounded pools; debug block | Complete — 17/17 gates |
| 4. Echo vertical slice | Playable switch/door solution; replay; visual/audio evidence | Automated evidence frozen — 23/23 gates; literal human play/listening remain release work |
| 5. Core vocabulary | Deterministic fixtures for combat, carrying, enemies, beams, timeline | In progress — current-ROM runtime, budget-v3, and visual/critic evidence freeze |
| 6. BOOT | Three stages, WATCHDOG, save, four passing replays | Pending |
| 7. VIDEO | Three stages, SCHEDULER, scrolling/parallax, four passing replays | Pending |
| 8. SOUND | Three stages, GARBAGE COLLECTOR, dynamic layers, four passing replays | Pending |
| 9. MEMORY | Three stages, FORK BOMB, anchors, four passing replays | Pending |
| 10. INTERRUPT | Three stages, ORACLE, final transmission, four passing replays | Pending |
| 11. Campaign integration | All menus, progression, continue, theater, attract, credits | Pending |
| 12. Optimization/red team | All budgets and regressions pass; no critical/high defects | Pending |
| 13. Release candidate | Fresh bootstrap and clean validation; final package/evidence | Pending |

Dependency order is strict through the Milestone 4 product proof. Later content
may be authored in parallel only against frozen interfaces.

## Current highest-leverage action

The pre-timing B01/B03 runtime recaptures pass on archived hashes. Lookup,
sprite, and indexed-CRC repairs now have measured gains and sampled parity,
but do not pass the separate simulation/blank budgets. Continue PERF-001/002
one bounded cause at a time: whole-empty projection elision, then bounded
gameplay scans as measurements justify. Preserve raw projection/SAT/HUD parity
and remeasure each change, including later-attempt checkpoints with echoes.
Harden the late-entry VBlank detector. Refresh changed-ROM replay, counter,
cycle, and five-state visual evidence before independent image review.
Run Windows All and the fail-closed
`Milestone5` aggregate before freezing M5 or starting BOOT-sector production.
The historical M4 ROM/report archive remains immutable; old passes must not be
relabelled as current-ROM proof.
