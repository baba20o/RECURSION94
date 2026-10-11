; Debug-only deterministic-engine reset fixture. Start+B on a newly pressed B
; edge invokes the real reset path and suppresses the simulation step for that
; frame, leaving an observable canonical tick-zero state. A/C must be released
; so this can never overlap the Start+A+B+C fatal fixture.

M3DebugResetFixture:
        ifd     BUILD_DEBUG
        tst.b   (INPUT_START_HELD).l
        beq.s   .not_triggered
        moveq   #0,d0
        move.b  (INPUT_PRESSED).l,d0
        andi.b  #INPUT_BIT_B,d0
        beq.s   .not_triggered
        moveq   #0,d1
        move.b  (INPUT_HELD).l,d1
        andi.b  #INPUT_BIT_A+INPUT_BIT_C,d1
        bne.s   .not_triggered
        bsr.w   EngineReset
        tst.w   d0
        bne.w   SafeException
        moveq   #1,d0
        rts
.not_triggered:
        endif
        moveq   #0,d0
        rts
