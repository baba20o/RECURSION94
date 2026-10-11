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
