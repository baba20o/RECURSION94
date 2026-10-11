; Leave the sound CPU held in reset for Milestone 1. The bus request is bounded,
; and a timeout is published rather than permitting an unbounded boot hang.

Z80_BUS_TIMEOUT                 equ     $3FFF

Z80EnterSafeState:
        move.w  #$0100,(Z80_BUS_REQUEST).l
; Power-on reset must be cancelled for the BUSREQ acknowledge circuit to run.
; BUSREQ is already asserted, so the Z80 cannot execute unowned sound code.
        move.w  #$0100,(Z80_RESET).l
        move.w  #Z80_BUS_TIMEOUT-1,d0
.wait_for_bus:
        moveq   #0,d1
        move.b  (Z80_BUS_REQUEST).l,d1
        andi.b  #$01,d1
        beq.s   .bus_owned
        dbf     d0,.wait_for_bus
        ori.w   #HARDWARE_FLAG_Z80_TIMEOUT,(DEBUG_HARDWARE_FLAGS).l
        moveq   #1,d0
        bra.s   .release_bus
.bus_owned:
        moveq   #0,d0
.release_bus:
        move.w  #$0000,(Z80_RESET).l
        move.w  #$0000,(Z80_BUS_REQUEST).l
        rts
