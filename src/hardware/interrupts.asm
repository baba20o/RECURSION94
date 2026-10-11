; Level-6 VBlank handler. Before BOOT_STAGE_RUNNING it only acknowledges the
; interrupt and returns; startup deliberately uses that side-effect-free path
; to absorb any level-6 request exposed by display enable. The running path
; advances the seqlocked debug heartbeat and saturating frame token, commits
; the prepared bounded DMA queue, then updates the visible heartbeat palette
; every 32 frames.

VBlankInterrupt:
        movem.l d0-d7/a0-a6,-(a7)
        move.w  (VDP_CONTROL).l,d0
        cmpi.w  #BOOT_STAGE_RUNNING,(DEBUG_BOOT_STAGE).l
        bne.w   .startup_return

        addq.l  #1,(DEBUG_SEQUENCE).l
        move.w  d0,(DEBUG_LAST_VDP_STATUS).l
        bsr.w   ControllerPoll
        addq.l  #1,(DEBUG_FRAME_COUNTER).l
        addq.l  #1,(DEBUG_HEARTBEAT).l
        cmpi.w  #$FFFF,(DEBUG_FRAME_TOKEN).l
        beq.s   .token_saturated
        addq.w  #1,(DEBUG_FRAME_TOKEN).l
.token_saturated:
        bsr.w   DmaCommitPreparedQueue

        move.l  (DEBUG_FRAME_COUNTER).l,d0
        andi.w  #$001F,d0
        bne.s   .complete
        move.l  #VDP_COMMAND_PULSE_CRAM_WRITE,(VDP_CONTROL).l
        move.l  (DEBUG_FRAME_COUNTER).l,d0
        andi.w  #$0020,d0
        beq.s   .pulse_cyan
        move.w  #$000E,(VDP_DATA).l
        bra.s   .complete
.pulse_cyan:
        move.w  #$0EE0,(VDP_DATA).l
.complete:
; Bit 3 is asserted only during vertical blank. Losing it before all commits
; complete is a measured VBlank budget overrun. A pending level-6 observed
; immediately after display enable can enter outside blank and is not timed.
        move.w  (VDP_CONTROL).l,d0
        move.w  (DEBUG_LAST_VDP_STATUS).l,d1
        andi.w  #$0008,d1
        beq.s   .within_vblank
        andi.w  #$0008,d0
        bne.s   .within_vblank
        cmpi.w  #$FFFF,(DEBUG_VBLANK_OVERRUN_COUNT).l
        beq.s   .within_vblank
        addq.w  #1,(DEBUG_VBLANK_OVERRUN_COUNT).l
.within_vblank:
; Gameplay never runs in the interrupt. Healthy main-side work completes and
; sleeps before the next VBlank, so these bounded mirrors observe the completed
; prior step. A budget overrun may expose an in-progress diagnostic snapshot;
; the renderer queue remains independently protected by its publication mask.
        move.w  (ENGINE_STATE).l,d0
        move.w  d0,(DEBUG_ENGINE_STATE).l
        move.w  (ENGINE_TICK).l,d0
        move.w  d0,(DEBUG_ENGINE_TICK).l
        move.w  (ENGINE_ENCOUNTER_ID).l,d0
        move.w  d0,(DEBUG_ENGINE_ENCOUNTER).l
        move.w  (ENGINE_LAST_ERROR).l,d0
        move.w  d0,(DEBUG_ENGINE_LAST_ERROR).l
        move.l  (ENGINE_STATE_CRC).l,d0
        move.l  d0,(DEBUG_ENGINE_STATE_CRC).l
        move.w  (ENGINE_CHECKPOINT_TICK).l,d0
        move.w  d0,(DEBUG_ENGINE_CHECKPOINT_TICK).l
        move.w  (ENGINE_CHECKPOINT_COUNT).l,d0
        move.w  d0,(DEBUG_ENGINE_CHECKPOINT_COUNT).l
        move.w  (ENGINE_ATTEMPT).l,d0
        move.w  d0,(DEBUG_ENGINE_ATTEMPT).l
        move.w  (ENGINE_OVERFLOW_FLAGS).l,d0
        move.w  d0,(DEBUG_ENGINE_OVERFLOW_FLAGS).l
        move.w  (ENGINE_PROJECTION_LENGTH).l,d0
        move.w  d0,(DEBUG_ENGINE_PROJECTION_LENGTH).l
        moveq   #0,d0
        move.b  (ENGINE_ACTIVE_ACTOR_COUNT).l,d0
        move.w  d0,(DEBUG_ENGINE_ACTIVE_ACTORS).l
        move.w  (GAMEPLAY_FLAGS).l,d0
        move.w  d0,(DEBUG_GAMEPLAY_FLAGS).l
        move.w  (GAMEPLAY_RESULT).l,d0
        move.w  d0,(DEBUG_GAMEPLAY_RESULT).l
        move.w  (GAMEPLAY_DOOR_STATE).l,d0
        move.w  d0,(DEBUG_GAMEPLAY_DOOR_STATE).l
        move.w  (GAMEPLAY_SWITCH_MASK).l,d0
        move.w  d0,(DEBUG_GAMEPLAY_SWITCH_MASK).l
        move.b  (TIMELINE_ECHO_COUNT).l,d0
        move.b  d0,(DEBUG_TIMELINE_ECHO_COUNT).l
        move.b  (TIMELINE_CURRENT_BANK).l,d0
        move.b  d0,(DEBUG_TIMELINE_CURRENT_BANK).l
        move.b  (TIMELINE_FLAGS).l,d0
        move.b  d0,(DEBUG_TIMELINE_FLAGS).l
        move.b  (TIMELINE_SELECTED_ORDINAL).l,d0
        move.b  d0,(DEBUG_TIMELINE_SELECTED_ORDINAL).l
        move.w  (TIMELINE_ATTEMPT_LENGTH).l,d0
        move.w  d0,(DEBUG_TIMELINE_ATTEMPT_LENGTH).l
        move.l  (GAMEPLAY_LAST_BANK_CRC).l,d0
        move.l  d0,(DEBUG_GAMEPLAY_LAST_BANK_CRC).l
        move.w  (GAMEPLAY_STABLE_TICK).l,d0
        move.w  d0,(DEBUG_GAMEPLAY_STABLE_TICK).l
        addq.l  #1,(DEBUG_SEQUENCE).l
.startup_return:
        movem.l (a7)+,d0-d7/a0-a6
        rte
