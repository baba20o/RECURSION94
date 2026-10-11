# Motorola 68000 register conventions

| Register | Convention |
|---|---|
| `d0-d2` | Caller-saved arguments, temporaries, results |
| `d3-d7` | Callee-saved long-lived values |
| `a0-a1` | Caller-saved pointer arguments/temporaries |
| `a2-a6` | Callee-saved subsystem pointers |
| `a7` | Supervisor/game stack; never repurposed |

- Primary return is `d0`; secondary return is `d1`.
- Byte/word parameters occupy the low part of a data register; callers clear or
  sign-extend when the callee contract requires it.
- Routines preserve callee-saved registers with one balanced `movem`.
- Leaf routines should avoid stack traffic when caller-saved registers suffice.
- No recursion, variable-sized stack allocation, or return-address tricks.
- Stack address space is `$FFF400-$FFFFFF` (3 KiB); debug builds fill the guard
  immediately below it and publish the measured low-water address.
- Macros must declare clobbers in their header comment and cannot hide an
  unbounded loop or hardware wait.
- VBlank saves/restores exactly the registers it touches, initially
  `d0-d2/a0-a2`; HBlank, when enabled, is stricter and touches only `d0/a0`.
- Exception entry switches to the same supervisor stack, snapshots a compact
  register/error record in the debug block, disables further interrupts, and
  enters the fatal renderer.
- Z80 code treats `af/bc/de/hl/ix/iy` as driver-owned; command handling preserves
  the sequencer state stored in Z80 RAM rather than relying on caller ABI.

## SAT rebuild entries

- `RendererBuildSat` retains its unconditional full 80-entry (640-byte) shadow
  clear. Initialization and callers without established shadow ownership use
  this entry; no incoming register value or previous count can shorten it.
- `RendererBuildSatOwnedShadow` is reserved for the initialized main-side
  producer after its pending-frame and checkpoint-hold guards. Its precondition
  is a complete preceding rebuild with an exact sprite count and a zero unused
  tail, with no intervening foreign SAT writer. It clears the preceding used
  prefix rounded up to 32 bytes. A prior unsigned word count above 64 sets
  `RENDERER_FLAG_INVALID_STATE` and takes the full-clear entry.
- `RendererBuildSatRebuild` is an internal shared worker reached only by those
  two wrappers. Its input is `d0.w`, a trusted 32-byte burst count in 0..20;
  it guards zero before DBF, resets the complete scanline ledgers, and rebuilds
  the ordinary sprite list. The wrappers tail-branch without opening a stack
  frame; the worker has one balanced save/restore and one ordinary return.
- All entries clobber `d0-d2/a0-a1` and preserve `d3-d7/a2-a6`. They neither
  change simulation state nor reduce the 640-byte SAT DMA transfer. A plausible
  but corrupt low prior count cannot establish ownership: arbitrary callers
  must use `RendererBuildSat`, not the owned entry.
