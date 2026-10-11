# Asset and source provenance

## Project-authored

- Game design, story, names, code, levels, palettes, symbolic pixel sources,
  music patterns, instruments, sound effects, tools, and documentation are
  authored for this repository.
- Generated cartridge data is a deterministic transformation of those sources.

## External development inputs

No third-party runtime routine or asset has been incorporated.

Pinned assemblers, portable Python, compiler bootstrap, and emulator are
development tools only, retain their own licenses, and are recorded with source
URLs/checksums in `tools/toolchain.lock.json`. Public hardware
documentation informs clean-room register use and is cited in
`HARDWARE_CONTRACT.md`.

vasm 2.0f is used under its non-commercial-purpose notice. It is not embedded
in the ROM; commercial use of this exact build pipeline outside the M68k/AmigaOS
exception requires upstream written consent or an assembler substitution.

Any later external routine or asset must be listed here with source, author,
license, exact modification, incorporated files, and reason. Proprietary Sega
ROM content, disassemblies, ripped graphics/music, and commercial level layouts
are prohibited.
