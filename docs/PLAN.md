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
sprite, indexed-CRC, whole-empty projection, and bounded gameplay scans now
have measured gains and sampled parity. The high-water candidate passes the
first-attempt checkpoint and extended reset timing samples. The first natural
later-attempt prefix completes its input/replay proofs but exposes real
three-actor checkpoint, ordinary-frame, and second-reset budget failures.
The independently reviewed offline repair passes protocol checks with all
30 observed IRQs anchored and all 312 original budget errors unchanged; the
failed raw capture remains immutable. Profile and optimize the measured hot
paths. The renderer-only follow-up saves 5,310–6,462 cycles in bounded samples
with all 6,240 buffer bytes unchanged. Its natural prefix preserves exact input,
replay, and projection CRCs, but still fails checkpoint, second-reset, and
ordinary-frame budgets. The bounded CRC/projection package now builds in all
three configurations with passing static validation and 77 focused native tests.
All four IRQ-free multi-echo checkpoint samples now fit the simulation limit
with identical CRCs. The prefix still fails ordinary-frame/blank timing.
The extended-reset collector's transitional-state stop is repaired and a
complete capture now exists: reset main interval 207,439 cycles and all 6,240
stored graphics bytes unchanged. Finish strict raw-protocol reanalysis for its
one-service-VBlank cadence (the immutable raw report's old two-service checker
still fails), then proceed with the separate ordinary-frame repair.
Preserve raw projection/SAT/HUD parity without removing
required mechanics or reducing echo counts.
The fresh CRC/projection candidate native All-suite rerun passes 1,011/1,011
without failures, errors, or skips. The preceding high-water run passed
982/982 after reviewed source-pin, frozen-fixture, and local dependency repairs. The prefix
collector and offline repair are independently reviewed and 61 focused native
tests pass; the first capture uses the unchanged high-water ROM/symbol pair. Every optimized
candidate needs fresh measured parity and timing, not the preceding image's pass.
Harden the late-entry VBlank detector. Refresh changed-ROM replay, counter,
cycle, and five-state visual evidence before independent image review.
Run Windows All and the fail-closed
`Milestone5` aggregate before freezing M5 or starting BOOT-sector production.
The historical M4 ROM/report archive remains immutable; old passes must not be
relabelled as current-ROM proof.
