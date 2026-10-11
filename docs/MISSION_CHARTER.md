# Mission charter

## Mission

Ship `build/recursion94-release.bin` and `build/recursion94-debug.bin` as real,
reproducible Sega Genesis / Mega Drive cartridge images for the complete
original game **RECURSION 94: The Last Cartridge**.

## Player promise

One incarnation fails inside a twelve-second deterministic encounter. On the
next attempt its recorded actions replay and still affect the world. Several
incarnations eventually perform distinct synchronized roles, letting the
present cross a threshold the past preserved. The system confirms
`PROGRAM STABLE`.

## Scope

- Five sectors, fifteen stages, and five concurrency-puzzle bosses.
- Top-down movement, pulse fire, phase dash, interaction, carrying, stealth,
  deterministic enemies, environmental devices, anchors, timeline management,
  replay theater, save/continue, attract mode, title/options/stage select,
  credits, and ending.
- Original Genesis-compatible art and a layered YM2612/PSG score.
- Primarily 68000 runtime assembly and a Z80 assembly sound driver.
- Windows-first pinned toolchain, tests, budget reports, emulator evidence,
  QA evidence, documentation, and package.

## Non-goals

- Sega CD, 32X, enhancement chips, six-button-only actions, emulator patches,
  runtime C, copied disassemblies/assets, video playback posing as gameplay, or
  a web/desktop runtime.
- Frame-level text programming by the player.
- Grind-based progression or uncontrolled randomness.

## Completion contract

Completion means every gate in section 53 of `prompt.txt` passes with evidence:
reproducible tools and builds; valid ROM/header/checksum/maps; verified video,
input, interrupts, DMA, Z80/YM2612/PSG; deterministic loop/echo/reset/anchor;
all gameplay vocabulary; all twenty reachable non-placeholder encounters and
canonical replays; save/continue/ending; audiovisual and hardware-budget QA;
zero known critical/high defects; and current documentation. The authoritative
gate is `.\tools\validate.ps1`.

## Valid terminal states

Only `COMPLETE`, `BLOCKED`, `BUDGET_EXHAUSTED`, or `INVALIDATED` are valid.
Any non-complete report lists exact failing gates, evidence, and resume
commands. A prototype, vertical slice, or “mostly done” state is not terminal.

