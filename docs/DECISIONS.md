# Decision log

## D-001 — Raw binary assembly pipeline

- **Context:** A cartridge needs exact vector/header placement and the runtime
  must remain assembly-first.
- **Decision:** Assemble one Motorola-syntax 68000 translation unit to a raw
  binary, with modular source included in a fixed order. Assemble the Z80
  driver separately and `incbin` it. Host tooling pads and verifies the ROM and
  patches the checksum.
- **Rationale:** Exact layout, small reproducibility surface, readable listing,
  and no C runtime or linker ambiguity.
- **Rejected:** SGDK C runtime; undocumented proprietary assembler; early
  relocatable-link complexity.
- **Consequence:** Public labels and include order are mission-controller-owned.

## D-002 — Uncompressed authoritative input streams

- **Context:** Six past echoes plus the current attempt need exact 720-frame
  replay.
- **Decision:** Store one byte of held input per authoritative frame and
  descriptors/lengths for each of seven physical banks. Rotate bank roles
  instead of copying recordings at reset.
- **Rationale:** 5,054 bytes is modest, constant-time, inspectable, and avoids
  compression divergence.
- **Rejected:** Full world-state recording; variable-length compression in the
  runtime.
- **Consequence:** Blank frames cost bytes, but all seven banks plus descriptors
  occupy exactly 5 KiB and playback remains constant-time.

## D-003 — Fixed 60 Hz simulation with PAL cadence adaptation

- **Context:** The loop must be twelve real seconds under NTSC and must not
  silently become materially longer under PAL.
- **Decision:** NTSC runs one step/VBlank. PAL uses a 60/50 accumulator and runs
  six steps per five VBlanks. Replays declare timing mode.
- **Rationale:** 720 steps remain twelve seconds and deterministic.
- **Rejected:** 600-step PAL levels; a 14.4-second PAL loop.
- **Consequence:** PAL double-step frames are a required worst-case CPU test.

## D-004 — Data-driven campaign

- **Context:** Twenty encounters must share deep mechanics and deterministic
  verification.
- **Decision:** Compile original JSON level/music/replay sources into bounded
  tables consumed by one engine. Bosses use the same device/enemy primitives
  plus deterministic phase descriptors.
- **Rationale:** Content completeness becomes machine-checkable without
  replacing gameplay with host logic.
- **Rejected:** Twenty bespoke code paths; placeholder descriptor aliases.
- **Consequence:** The content compiler rejects missing insights, roles,
  replays, and hardware budgets.

## D-005 — Original programmatic pixel sources

- **Context:** Art must be original, reproducible, hardware-aware, and auditable.
- **Decision:** Author symbolic indexed-pixel/tile sources and palettes in the
  repository, then compile/deduplicate/flip-reuse them deterministically.
- **Rationale:** Exact palette/tile control with clear provenance.
- **Rejected:** Ripped sprites, proprietary fonts, opaque AI raster imports.
- **Consequence:** Generated art is never hand-edited.

## D-006 — Canonical serialization and CRC-32

- **Context:** Raw assembly structs contain padding/pointers and an in-ROM hash
  alone could falsely certify itself.
- **Decision:** Serialize only designated authoritative fields in a stable
  big-endian schema capped at 4 KiB, then compute CRC-32/ISO-HDLC. Checkpoints
  occur at frame zero, every 60 ticks, tick 720, and terminal states. The host
  independently reads emulator RAM and recomputes the projection and CRC.
- **Rationale:** Cross-build, cross-run proof with a standard known vector and
  no trust in renderer/audio/debug state.
- **Rejected:** Hashing raw RAM; trusting a ROM-published expected value.
- **Consequence:** State-schema coverage tests must mutate every authoritative
  field and prove the hash changes unless the states are explicitly equivalent.

## D-007 — Observe simulation at the stable post-step boundary

- **Context:** The first startup VBlank breakpoint is deliberately the stage-3
  acknowledgment-only priming ISR, not the scheduler origin. It polls no
  controller, advances no counters, and publishes no frame token. Asynchronous
  two-key window messages can likewise span more than one controller poll.
- **Decision:** Capture the canonical tick-zero image at the stage-3 priming
  ISR entry and treat the following stage-4 running full VBlank as the first
  controller poll/counter boundary. Observe every authoritative tick at the
  exported `MainLoopPostGameplayStepCall` symbol, before the tick-720 timeout
  branch can commit/reset. Observe that unmodified automatic transition at
  `MainLoopPostCommitReset`, then observe neutral tick 1 of the next attempt.
- **Rationale:** Every simulated tick remains observable without debugger
  writes or tolerance for skipped progression, while input evidence remains a
  real standard-controller path.
- **Rejected:** Allowing skipped ticks; debugger RAM/PC writes; counting the
  stage-3 acknowledgment-only priming entry as a running frame or scheduler
  origin.
- **Consequence:** Live evidence depends on both symbol and ROM hashes,
  explicitly records every breakpoint transition, and proves the shipped
  automatic timeout reset without controller injection.

## D-008 — Stage post-reset input at the latched meta-input boundary

- **Context:** The M4 manual Start+C commit/reset path can span a VBlank.
  Waiting until the post-reset observation to release the chord or press the
  next-attempt direction is too late: an intervening VBlank can poll the old
  keys, queue a frame token, and make stale Start/C bits the first consumed
  input of attempt 1.
- **Decision:** Stop at the exported `TimelineHandleMetaInput` entry after
  `ControllerPoll` has latched Start+C but before commit/reset begins. Verify
  that guest-latched chord, then post the C/Start releases and stage the exact
  next-attempt held input before resuming. The already-latched chord remains
  authoritative for the manual commit, while any VBlank during reset observes
  the staged state.
- **Rationale:** This preserves the standard-controller path and exact input
  chronology across a long reset without modifying guest memory, registers, or
  the program counter.
- **Rejected:** Posting transitions only after the reset; waiting for the next
  VBlank breakpoint; debugger input-byte writes; tolerating a polluted first
  frame.
- **Consequence:** Runtime, visual, and audio evidence records the meta-entry
  breakpoint, staged events, first consumed attempt bytes, and an empty GDB
  write-packet list. Those reports and their collector sources remain
  ROM/symbol/hash bound.
