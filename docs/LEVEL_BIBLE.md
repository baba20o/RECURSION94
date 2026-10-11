# Level bible

The campaign follows **teach → twist → combine → boss** in each sector. Basic
clear windows begin at 60–120 frames, narrow to 30–60 midgame, and only reach
15–30 in the final sector. Tighter execution is for OPTIMIZED/TRANSCENDENT
ranks, not required first clears.

Every encounter has a full-history canonical replay, a plausible failure
fixture, and a hardware-stress fixture. Bosses also have phase-boundary
regressions. Expected outcomes assert real simulation; they never force it.

## BOOT — restrained void / bone terminals / 80 BPM / echo cap 2

| ID / name | Initial state, mechanic, and required insight | Expected echoes and canonical roles | Alternatives, clear failure, worst case, replay |
|---|---|---|---|
| **B01 POWER-ON SELF** | One plate opens one distant door. Movement, twelve-second expiry, B interaction, and the first unavoidable reset teach that a past body can occupy space while the present moves. | Minimum 1; first clear 1–2. E1 holds the plate; current crosses and commits. | Alternative timing/path around plate edge, never solo. Failure highlights plate/rune trace. Worst: 2 actors + reset dissolve. `tests/replay/canonical/B01.r94r` |
| **B02 DEAD LETTER** | A sentry covers the direct lane; cover, pulse fire, and optional dash teach that one self can attract deterministic aim for another. | Minimum 1; first clear 1–2. E1 fires west to draw aim; current circles, dashes the lane, destroys rear node. | Current may become decoy and let echo shoot. Failure shows aim line and rear armor. Worst: 2 actors, sentry, 8 shots. `.../B02.r94r` |
| **B03 TWO-PHASE COMMIT** | Two switches and a breakable node control a timed door. Simultaneous work also has an order. Dash becomes required with a generous route. | Minimum 2; first clear 2–3. E1 holds A; E2 pulses B and breaks node; current crosses and commits. | Swap A/B roles. Failure identifies first unsatisfied dependency. Worst: 3 actors, door animation, 12 shots. `.../B03.r94r` |
| **B04 THE WATCHDOG** | Security gaze resets the observed incarnation; a scan motor, blind-spot plate, and shutdown port make observation a schedulable resource. | Minimum 2; first clear 2–4. E1 north decoy; E2 stalls scan/holds blind spot; current takes south route and shuts down the port. | Swap decoy/stall routes or destroy a cover node. Failure freezes the exact gaze ray. Worst: 3 actors, rotating cone, 16 projectiles. `.../B04.r94r` |

## VIDEO — cobalt machinery / scrolling and parallax / 100 BPM / echo cap 3

| ID / name | Initial state, mechanic, and required insight | Expected echoes and canonical roles | Alternatives, clear failure, worst case, replay |
|---|---|---|---|
| **V01 BUS ARBITRATION** | Reversible conveyors and two distant plates demonstrate that the same route changes under concurrent machine state. | Minimum 2; first clear 2–3. E1 holds reverse; E2 rides to far plate; current crosses the safe path. | Reverse roles or use a precise dash shortcut. Failure shows conveyor direction history. Worst: 3 actors + full conveyor animation. `.../V01.r94r` |
| **V02 RASTER RELAY** | A prism, beam, portable cell, and socket separate energy production, aiming, and delivery. | Minimum 2; first clear 2–4. E1 aims prism; E2 carries/drops cell through safe lane; current sockets/commits. | Current carries while E2 commits. Failure marks beam interruption or dropped cell owner. Worst: beam segments, 4 actors, cell. `.../V02.r94r` |
| **V03 DOUBLE BUFFER** | Lift, conveyors, cell, and hazards form a producer/consumer handoff; no incarnation can traverse the whole pipeline in time. | Minimum 3; first clear 3–4. E1 calls lift; E2 places cell; E3 receives/sockets; current takes powered route. | E2/E3 can exchange lift and carry jobs. Failure shows last valid handoff. Worst: moving platform + 4 actors + particles. `.../V03.r94r` |
| **V04 THE SCHEDULER** | Three zones receive execution windows on a visible 120-frame cycle; global commit opens only when all channels are serviced. | Minimum 3; first clear 3–5. E1 services A, E2 B, E3 C/reflector; current crosses and commits. | Rotate channel ownership. Failure timeline shows missed zone/window. Worst: parallax, scanline hazards, 4 actors, beams. `.../V04.r94r` |

## SOUND — waveform machinery / visual-audio phase / 120 BPM / echo cap 4

All audio-sensitive mechanics also use synchronized visual state. Authoritative
logic uses the 68000 beat phase, never Z80/YM playback position.

| ID / name | Initial state, mechanic, and required insight | Expected echoes and canonical roles | Alternatives, clear failure, worst case, replay |
|---|---|---|---|
| **S01 NOISE FLOOR** | A sound-sensitive Pursuer and pulse door make a recording author an audible lure, not just a path. | Minimum 2; first clear 2–3. E1 fires a repeating lure; E2 holds pulse gate; current takes silent route. | Current can lure late while an echo exits. Failure has visible sound rings and target line. Worst: 4 actors + rings + Pursuer. `.../S01.r94r` |
| **S02 PHASE LOCK** | Three broad, visibly pulsing terminals must be struck in successive phases so their flags overlap. | Minimum 3; first clear 3–4. E1/E2/E3 strike successive windows; current traverses. | Any role permutation works. Failure labels early/late phase rather than hiding timing. Worst: 4 actors, full machinery animation. `.../S02.r94r` |
| **S03 COUNTERPOINT** | Lure, gate, cell, turret, and shield combine into distinct repeating jobs whose overlap creates one safe corridor. | Minimum 4; first clear 4–6. E1 lures; E2 gates; E3 carries; E4 suppresses shield/turret; current completes receiver. | Redirect turret instead of suppressing it. Failure identifies role collapse. Worst: 5 actors, turret shots, cell, shields. `.../S03.r94r` |
| **S04 THE GARBAGE COLLECTOR** | A known scrub schedule disables the oldest recording; useful early work must survive loss of its author. | Minimum 4; first clear 4–7. E1 unlocks before frame 240; E2 decoys before frame 480; E3 shields; E4 buffers; current uploads checksum. | Protect/replace a different early role. Disabled recordings return next attempt. Worst: 5 actors, boss parts, scrub effect, shots. `.../S04.r94r` |

## MEMORY — amber branch lattice / persistent scars / 140 BPM / echo cap 5

Anchors are logical whitelisted state records. They remove one serial
dependency; they do not persist arbitrary world bytes.

| ID / name | Initial state, mechanic, and required insight | Expected echoes and canonical roles | Alternatives, clear failure, worst case, replay |
|---|---|---|---|
| **M01 WRITE ENABLE** | A cell/socket and repair terminal introduce one anchor. Anchoring the right completed task creates room in later twelve-second schedules. | Minimum 3 + anchor; first clear 4–6. Prelude anchors socket; E1 gates; E2 lures Warden; E3 repairs; current exits. | Anchor repaired terminal instead, with a harder carry route. Failure distinguishes resettable and anchored state. Worst: 5 actors + Warden. `.../M01.r94r` |
| **M02 BAD SECTOR** | Deterministic blackout zones and a Scrubber create explicit holes in a replay; timeline protection/replacement repairs the plan. | Minimum 4; first clear 4–7. Protect E1; replace failed slot; E1 acts before blackout, E2 after, E3 carries, E4 destroys Scrubber; current commits. | Protect carrier instead and rewrite opening. Failure highlights disabled interval and downstream role. Worst: 5 actors, blackout overlay, Scrubber, shots. `.../M02.r94r` |
| **M03 COMPACTION** | Conveyor, beam, shield, and two possible anchor targets ask which dependency is most expensive across resets. | Minimum 5; first clear 5–8. Anchor conveyor direction; E1 shields, E2 lures, E3 carries, E4 redirects, E5 operates terminal; current crosses. | Anchor lock instead; remains valid but costs a different role. Failure comparison teaches optimization. Worst: 6 actors, beam, shields, projectiles. `.../M03.r94r` |
| **M04 THE FORK BOMB** | A hostile mirrored process derives from selected input; actions must be useful even when reflected/inverted. | Minimum 5; first clear 5–9. E1 authors no-fire symmetric quarantine path; E2 seals, E3 shields, E4 reflects, E5 opens core; current writes kill token. | Author a fire stream that safely destroys a side node. Failure previews the exact fork transform. Worst: 6 friendly + mirrored actor, boss parts, beams. `.../M04.r94r` |

## INTERRUPT — controlled corruption / split scroll / 160 BPM / echo cap 6

| ID / name | Initial state, mechanic, and required insight | Expected echoes and canonical roles | Alternatives, clear failure, worst case, replay |
|---|---|---|---|
| **I01 PRIORITY INVERSION** | Redirected sentry, shields, anchor, and cell turn stable target-selection priority into a coordination tool. | Minimum 5; first clear 5–8. E1 becomes visible at interrupt windows; E2 shields; E3 reflects; E4 carries; E5 opens port; current commits. | Swap carrier/port roles or use anchored shield. Failure exposes deterministic target ranking. Worst: 6 actors + dense sentry shots. `.../I01.r94r` |
| **I02 NON-MASKABLE** | An unavoidable global raster burst is survived through overlapping local protection, not evasion. | Minimum 6; first clear 6–10. E1–E4 chain 15–30-frame shield windows, E5 redirects penultimate burst, E6 holds final gate; current traverses. | Shift shield ownership while preserving coverage. Failure timeline marks the uncovered interval. Worst: 7 actors aligned, burst effects, shields, projectiles. `.../I02.r94r` |
| **I03 VECTOR TABLE** | Seed payload, lift, beam, bridge, and socket form a six-role spatial producer/consumer pipeline. | Minimum 6; first clear 6–10. E1 retrieves/drops; E2 carries to lift; E3 calls/redirects; E4 receives; E5 powers bridge; E6 sockets/opens vector; current commits. | Two legal handoff points support role swaps. Failure identifies ownership/time of lost payload. Worst: 7 actors + payload + lift/beam/bridge animation. `.../I03.r94r` |
| **I04 ORACLE** | ORACLE previews a deterministic response derived from echo count/age/actions/anchors. Infrastructure, defense, transport, routing, and execution must coexist. | Minimum 6; first clear 6–12. E1 powers uplink; E2 shields; E3 redirects attacks; E4 carries first payload leg; E5 opens route; E6 stabilizes clock/finishes handoff; current transmits. | Response policy admits alternate carrier/redirect paths. Failure always names the broken role/phase. Worst: 7 actors, all boss components, beams/shots, split scroll, maximum audio layers. `.../I04.r94r` |

## Results and feedback

- First causal failure flashes the device and matching echo rune; Start opens
  detailed timeline history.
- Enemy aim lines and target tie-breaking are visible.
- At capacity, the player explicitly selects a slot to replace; protected slots
  and downstream role loss are shown before confirmation.
- Ranks are STABLE, OPTIMIZED, and TRANSCENDENT. Optimization windows never
  redefine the basic deterministic solution.

