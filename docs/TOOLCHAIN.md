# Pinned toolchain

All tools install under `.toolcache` and are invoked by repository scripts.
No administrator rights, global PATH edits, Visual Studio, or runtime framework
is required. `tools/toolchain.lock.json` is the machine-readable authority.

| Tool | Pin | Source | License | Archive SHA-256 |
|---|---|---|---|---|
| CPython embeddable x64 | 3.12.10 | `https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip` | Python-2.0 | `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3` |
| Zig Windows x64 | 0.14.1 | `https://ziglang.org/download/0.14.1/zig-x86_64-windows-0.14.1.zip` | MIT | `554f5378228923ffd558eac35e21af020c73789d87afeabf4bfd16f2e6feed2c` |
| vasm source | 2.0f | `http://server.owl.de/~frank/tags/vasm2_0f.tar.gz` | Upstream non-commercial use/redistribution notice | `c84b2de1cbb87831795fe64a85c5d9a7002a766e3a7c30b0a2d7d5e99d878f49` |
| sjasmplus Windows | 1.23.1 | `https://github.com/z00m128/sjasmplus/releases/download/v1.23.1/sjasmplus-1.23.1.win.zip` | BSD-3-Clause | `fa0ca77e6e6dcdad77b2c64607b325dd6366844ab6438bfc97661fdfbc29aa35` |
| BlastEm Windows x64 | 0.6.3-pre-`732f5689d438` | `https://www.retrodev.com/blastem/nightlies/blastem-win64-0.6.3-pre-732f5689d438.zip` | GPL-3.0-or-later | `01dd73044d1333e40160da61da9e4ccb78fc5a100435b4d5f03ae4ba1f199a60` |

Zig compiles upstream vasm source locally into `vasmm68k_mot.exe`, including
the M68k backend, Motorola syntax, and upstream output modules. vasm is not
linked into or shipped inside the ROM. Its notice permits the archive to be
used for non-commercial purposes; the commercial exception is limited to
M68k/AmigaOS, and other commercial use requires written consent. This mission
uses the tool non-commercially. Anyone commercially exploiting the game must
obtain that consent or substitute a compatible permissively licensed/GPL
assembler pipeline. This tooling notice does not add code or assets to the ROM.

## Verified local invocation

```powershell
.\tools\bootstrap.ps1
```

On 2026-07-26 this passed on Windows 11 build 28020 / PowerShell 5.1 and emitted
`artifacts/reports/environment.json`. Exact smoke outputs:

- M68000 `moveq #1,d0; rts`: four bytes, SHA-256
  `af841c1d3eef93a81a3445ba358c37b149f4624bfe2af1175dfdcec99f0fa90f`.
- Z80 `ld a,$94; halt`: three bytes, SHA-256
  `e7e39756fd294c11d4c61dd9ea671dc45f34a2a663549a82b8a5eacf4f2aa1c3`.

The downloaded BlastEm executable is SHA-256
`22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803`.
Milestone 1 separately proved boot through Windows GDB RAM samples and a
hash-bound screenshot; file presence was not treated as emulator evidence.

Pinned source `blastem.c` implements `-b <frame-count>` as headless execution
with deterministic exit and `-e <file>` as a hardware event log:
`https://www.retrodev.com/repos/blastem/raw-file/732f5689d438/blastem.c`.
The pinned Windows executable currently exits `0xC0000005` on the hidden `-b`
path, even from its resource directory. That optional throughput probe is
therefore a recorded emulator/tool defect, not a passing gate. Counted GDB
VBlank breakpoints provide bounded runtime evidence; GUI capture remains a
separate visual gate.
