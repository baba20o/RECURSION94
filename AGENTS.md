# Repository operating contract

This file applies to every path in the repository.

## Product truth

- `prompt.txt` is the authoritative mission contract.
- A source tree, mockup, boot screen, or vertical slice is not a finished game.
- Never mark a skipped gate as passing and never hard-code a canonical replay
  outcome in place of simulation.
- Runtime gameplay is Motorola 68000 assembly; sound-side scheduling is Z80
  assembly. Host tools may be Python or PowerShell.
- All runtime storage, queues, pools, and loops are statically bounded.

## Assembly style

- Motorola 68000 source uses vasm Motorola syntax and `.asm`.
- Z80 source uses sjasmplus syntax and `.asm`.
- Mnemonics and registers are lowercase; exported labels use `PascalCase`;
  file-local labels begin with `.`.
- Constants use `UPPER_SNAKE_CASE`; hexadecimal literals use `$`.
- State widths are explicit in instruction suffixes. Avoid implicit `.w`.
- Generated assembly and binary data live only under `assets/generated/` and
  are never hand-edited.

## 68000 ABI

- Arguments: `d0-d2`, then `a0-a1`. Return value: `d0`; optional second value:
  `d1`. Condition codes may carry a Boolean result when documented.
- Caller-saved: `d0-d2/a0-a1`. Callee-saved: `d3-d7/a2-a6`.
- `a7` is the stack pointer. There is no frame pointer and no recursion.
- A routine that changes a callee-saved register restores it with `movem`.
- Interrupt handlers preserve every register they touch. They may not call
  ordinary gameplay routines.
- The stack begins at `$00FFFFFC`, grows down, has a 3 KiB address-space budget,
  and must not cross the debug guard below `$00FFF400`.

## Interrupt and hardware rules

- Gameplay simulation never runs inside an interrupt.
- 68000 VBlank only acknowledges the VDP, increments the frame token, and
  commits the prepared SAT/VDP transfer queue. The Z80 receives its own V-int
  and uses that as the sole sequencer tick.
- HBlank is disabled except for an explicitly budgeted sector effect.
- Outside reset/display-off code and interrupt commit code, VDP writes go
  through the hardware layer or a bounded transfer queue.
- DMA sources are even, ROM-resident or approved RAM, word-counted, and cannot
  cross an unverified bus boundary.
- Z80 RAM or 68000-side YM2612 access requires the documented bus handshake.
  Routine sound is Z80-owned; bus waits have debug timeouts.
- Save RAM is never written from an interrupt.

## Determinism

- One authoritative NTSC simulation step is one 60 Hz game frame.
- An active attempt is exactly 720 authoritative steps.
- PAL renders at 50 Hz and schedules six authoritative steps per five VBlanks;
  held input may feed both steps, but edges occur only on the first.
- No authoritative state depends on wall time, uninitialized bytes, allocation
  order, host tools, or uncontrolled randomness.
- Reset reconstructs ordinary state from compiled level data, then applies
  only approved anchor mutations.
- Replay bytes encode held Up/Down/Left/Right/A/B/C in bits 0–6; bit 7 is zero.
  Opposite axes sanitize to neutral. Pause/menu frames do not consume or record
  authoritative frames.

## Ownership and integration

- The mission controller owns `AGENTS.md`, build contracts, the RAM/VRAM maps,
  register conventions, public labels, and terminal-state decisions.
- Parallel authors receive non-overlapping files. Shared-interface changes must
  be proposed before implementation.
- A handoff names scope, inspected/changed files, assumptions, decisions,
  exact tests and results, remaining risks, and integration notes.
- After integration run the narrow test, both ROM builds, static validation,
  affected replays, and emulator smoke where available.

## Commands

```powershell
.\tools\bootstrap.ps1
.\tools\build.ps1 -Configuration Debug
.\tools\build.ps1 -Configuration Release
.\tools\test.ps1
.\tools\validate.ps1
```
