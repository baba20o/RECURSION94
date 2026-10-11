; Encounter 0 pressure-switch, first-rectangle door, and east terminal.

; GameplayResetVerticalSlice
; Restores the derived B01 device to canonical closed/unheld state.
; Clobbers d0-d2/a0-a1.
GameplayResetVerticalSlice:
        move.b  #$01,(B01_DEVICE+OBJECT_ACTIVE).l
        clr.w   (B01_DEVICE+OBJECT_STATE).l
        clr.l   (B01_DEVICE+OBJECT_VX).l
        clr.l   (B01_DEVICE+OBJECT_VY).l
        clr.w   (B01_DEVICE+OBJECT_TIMER).l
        clr.w   (B01_DEVICE+OBJECT_PARAM).l
        clr.w   (GAMEPLAY_DOOR_STATE).l
        clr.w   (GAMEPLAY_SWITCH_MASK).l
        clr.w   (GAMEPLAY_RESULT).l
        clr.w   (GAMEPLAY_STABLE_TICK).l
        andi.w  #GAMEPLAY_FLAG_INITIALIZED|GAMEPLAY_FLAG_REPLACEMENT_VISIBLE,(GAMEPLAY_FLAGS).l
        andi.b  #$BF,(TIMELINE_FLAGS).l
        rts

; GameplayResolveVerticalSlice
; Runs after every actor has moved and before pools/checkpoint publication.
; Out: d0.w=1 only when current commits PROGRAM_STABLE, otherwise zero.
; Projected B01 device state contains switch, next-tick door, stable result,
; and the physical actor occupancy mask in OBJECT_PARAM.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayResolveVerticalSlice:
        movem.l d3-d7/a2,-(a7)
        moveq   #0,d5                   ; seven-bit physical actor mask

; Echo actors are physical slots zero through echo_count-1.
        moveq   #0,d3
        move.b  (TIMELINE_ECHO_COUNT).l,d3
        beq.s   .test_current
        subq.w  #1,d3
        moveq   #0,d4
        lea     (ENGINE_ACTOR_BASE).l,a2
.echo:
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .echo_next
        move.l  (ACTOR_X,a2),d0
        cmpi.l  #B01_SWITCH_MIN_X_16_8,d0
        blt.s   .echo_next
        cmpi.l  #B01_SWITCH_MAX_X_16_8,d0
        bge.s   .echo_next
        move.l  (ACTOR_Y,a2),d0
        cmpi.l  #B01_SWITCH_MIN_Y_16_8,d0
        blt.s   .echo_next
        cmpi.l  #B01_SWITCH_MAX_Y_16_8,d0
        bge.s   .echo_next
        bset    d4,d5
.echo_next:
        adda.w  #ACTOR_BYTES,a2
        addq.w  #1,d4
        dbf     d3,.echo

.test_current:
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a2
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .publish
        move.l  (ACTOR_X,a2),d0
        cmpi.l  #B01_SWITCH_MIN_X_16_8,d0
        blt.s   .publish
        cmpi.l  #B01_SWITCH_MAX_X_16_8,d0
        bge.s   .publish
        move.l  (ACTOR_Y,a2),d0
        cmpi.l  #B01_SWITCH_MIN_Y_16_8,d0
        blt.s   .publish
        cmpi.l  #B01_SWITCH_MAX_Y_16_8,d0
        bge.s   .publish
        bset    #GAMEPLAY_CURRENT_ACTOR_SLOT,d5

.publish:
        move.w  d5,(GAMEPLAY_SWITCH_MASK).l
        move.w  d5,(B01_DEVICE+OBJECT_PARAM).l
        move.w  (B01_DEVICE+OBJECT_STATE).l,d6
        andi.w  #B01_DEVICE_STATE_PROGRAM_STABLE,d6
        andi.w  #$FFE7,(GAMEPLAY_FLAGS).l ; clear switch/door visible bits
        tst.w   d5
        beq.s   .door_ready
        ori.w   #B01_DEVICE_STATE_SWITCH_HELD|B01_DEVICE_STATE_DOOR_OPEN,d6
        ori.w   #GAMEPLAY_FLAG_SWITCH_HELD|GAMEPLAY_FLAG_DOOR_OPEN,(GAMEPLAY_FLAGS).l
.door_ready:
        move.w  d6,(B01_DEVICE+OBJECT_STATE).l
        move.w  d6,(GAMEPLAY_DOOR_STATE).l

; Only an echo-held switch can authorize the current actor's east-terminal B
; edge. Echo B presses never commit the room.
        move.w  d5,d0
        andi.w  #$003F,d0
        beq.s   .not_stable
        moveq   #0,d0
        move.b  (GAMEPLAY_CURRENT_PRESSED).l,d0
        andi.b  #INPUT_B,d0
        beq.s   .not_stable
        move.l  (ACTOR_X,a2),d0
        cmpi.l  #B01_TERMINAL_MIN_X_16_8,d0
        blt.s   .not_stable
        cmpi.l  #B01_TERMINAL_MAX_X_16_8,d0
        bge.s   .not_stable
        move.l  (ACTOR_Y,a2),d0
        cmpi.l  #B01_TERMINAL_MIN_Y_16_8,d0
        blt.s   .not_stable
        cmpi.l  #B01_TERMINAL_MAX_Y_16_8,d0
        bge.s   .not_stable

        ori.w   #B01_DEVICE_STATE_PROGRAM_STABLE,(B01_DEVICE+OBJECT_STATE).l
        move.w  (B01_DEVICE+OBJECT_STATE).l,d0
        move.w  d0,(GAMEPLAY_DOOR_STATE).l
        ori.w   #GAMEPLAY_FLAG_PROGRAM_STABLE,(GAMEPLAY_FLAGS).l
        ori.b   #TIMELINE_FLAG_PROGRAM_STABLE,(TIMELINE_FLAGS).l
        move.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,(GAMEPLAY_RESULT).l
        move.w  (ENGINE_TICK).l,d0
        addq.w  #1,d0
        move.w  d0,(GAMEPLAY_STABLE_TICK).l
        moveq   #GAMEPLAY_RESULT_PROGRAM_STABLE,d0
        bra.s   .done
.not_stable:
        moveq   #GAMEPLAY_RESULT_OK,d0
.done:
        movem.l (a7)+,d3-d7/a2
        rts
