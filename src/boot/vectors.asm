; The cartridge header begins at $100, so all 64 base 68000 vectors are
; explicitly covered here. Unexpected exceptions enter a bounded safe halt.

VectorTable:
        dc.l    INITIAL_STACK
        dc.l    ResetEntry
        dcb.l   28,SafeException
        dc.l    VBlankInterrupt
        dc.l    SafeException
        dcb.l   32,SafeException
VectorTableEnd:

        assert  VectorTable=$000000,"vector table must start at ROM offset $000000"
        assert  VectorTableEnd=$000100,"vector table must contain exactly 64 entries"
