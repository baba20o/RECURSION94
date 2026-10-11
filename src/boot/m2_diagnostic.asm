; Explicit Debug-only fatal-screen fixture. Holding Start+A+B+C in the live
; controller sample executes one illegal instruction exactly once.

M2_FATAL_FIXTURE_TRIGGERED      equ     INPUT_STATE_BASE+$0C
M2_FATAL_FIXTURE_BUTTONS        equ     INPUT_BIT_A+INPUT_BIT_B+INPUT_BIT_C

M2DebugFatalFixture:
        ifd     BUILD_DEBUG
        tst.b   (M2_FATAL_FIXTURE_TRIGGERED).l
        bne.s   .complete
        tst.b   (INPUT_START_HELD).l
        beq.s   .complete
        moveq   #0,d0
        move.b  (INPUT_HELD).l,d0
        andi.b  #M2_FATAL_FIXTURE_BUTTONS,d0
        cmpi.b  #M2_FATAL_FIXTURE_BUTTONS,d0
        bne.s   .complete
        move.b  #$01,(M2_FATAL_FIXTURE_TRIGGERED).l
        illegal
.complete:
        endif
        rts
