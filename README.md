# RECURSION 94: The Last Cartridge

**THE FUTURE HID IN 1994.**

RECURSION 94 is an original top-down action-puzzle game for the Sega Genesis /
Mega Drive. Every incarnation has twelve seconds. When the room resets, the
previous input stream returns as a world-interacting echo. The player composes
several past attempts into one synchronized solution.

The cartridge runtime is written primarily in Motorola 68000 assembly. Sound
is scheduled by a Z80 assembly driver. Host-side Python tools compile original
art, levels, music, replays, reports, and the final ROM.

## Supported development host

- Windows 10 or 11 x64 with Windows PowerShell 5.1 or PowerShell 7.
- Internet access for the first bootstrap.
- VS Code is optional.
- WSL2 is useful for development but is not required by the release build.

## Commands

```powershell
.\tools\bootstrap.ps1
.\tools\build.ps1 -Configuration Debug
.\tools\build.ps1 -Configuration Release
.\tools\run.ps1
.\tools\test.ps1
.\tools\validate.ps1
```

The bootstrap installs pinned tools under `.toolcache`; it does not require
administrator access or undocumented global paths.
It also installs the hash-pinned JSON Schema validator and its dependencies
from `tools/python-packages.lock.json` into the project-local Python directory.
No global Python packages or `pip` installation are required.
Before launching Python, bootstrap verifies the original embedded archive and
installed interpreter files, including its unchanged isolated path configuration.
Unexpected files or modified interpreter bytes fail verification; they are not
silently trusted because `python.exe` exists.

Expected release artifacts:

```text
build/recursion94-debug.bin
build/recursion94-release.bin
```

`build.ps1` defaults to Debug. Its Debug-only `-Level <ID>` option (for
example `-Level B03`) writes a separate fixture ROM to
`build/level-<ID>/recursion94-debug.bin`; the Release ROM always boots the
campaign entry. `run.ps1` rebuilds the Debug ROM before launching the pinned
BlastEm and returns without waiting; use `-Configuration Release` for the
release ROM or `-NoBuild` to launch an existing ROM.

`validate.ps1` defaults to `-Scope Release` (`Milestone1` through `Milestone5`
select earlier gates). It first runs `tools/clean.ps1`, which deletes the whole
`build/` directory, including `build/level-*` fixtures, and the generated
content, art, CRC32 and Z80 driver outputs under `assets/generated/`. It then
rebuilds Debug, Release and, for the Milestone5 and Release scopes, the B03
Debug fixture. Copy any ROM you want to keep before validating. Reports are
written under `artifacts/reports/`.

## Current truth

This public repository is an unfinished development checkpoint, not a release.
Milestones 0–4 have frozen automated evidence. Milestone 5 (core game
vocabulary) is in progress: deterministic replay and gameplay fixtures exist,
but simulation timing and reset-overlay VBlank failures remain open. Later
campaign, save/UI, quality, and release gates are still pending.

See [docs/STATUS.md](docs/STATUS.md) for exact ROM identities, measured results,
and remaining work. Historical evidence does not establish that a newer source
revision passes; current source may be ahead of the last measured ROM.
The authoritative mission contract is [prompt.txt](prompt.txt).

Downloaded tools, generated build outputs, and local scratch files are excluded
from version control. Archived diagnostic evidence is retained where indicated;
passing host tests alone do not establish a finished or performance-compliant
game. Automated checks do not substitute for human play or listening review.

`tools/validate.ps1` intentionally remains incomplete for the release scope:
its replay, save, hardware-budget, campaign, visual, audio and red-team gates
are still reported as pending, so the default run cannot pass and exits
nonzero (`INCOMPLETE`, or `FAIL` if any required gate fails).
`docs/FINAL_REPORT.md` will say `COMPLETE` only after every contract gate
passes.

## Controls

- D-pad: move
- A: pulse
- B: interact / carry
- C: phase dash
- Start: pause / timeline

All required actions work on a standard three-button controller.

## License and provenance

Project-authored source and assets are MIT licensed. Downloaded development
tools retain their own licenses and are not part of the ROM. External technical
sources and any non-original inputs are recorded in
`docs/ASSET_PROVENANCE.md` and `docs/HARDWARE_CONTRACT.md`.
The pinned vasm assembler has a separate non-commercial-purpose notice; see
`docs/ASSET_PROVENANCE.md` before using this exact toolchain commercially.
