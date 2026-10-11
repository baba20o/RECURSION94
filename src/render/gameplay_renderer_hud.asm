; Dynamic three-row HUD. All text uses resident diagnostic glyphs; four local
; punctuation tiles and three echo-state symbols extend the same 8x8 grid.

; RendererBuildHud
; Builds the release-facing TIME ss:ff, one-based attempt, echo identities,
; replacement state, PROGRAM STABLE state, and the reset-scan message.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererBuildHud:
        movem.l d3-d7/a2-a3,-(a7)

        lea     (RENDERER_HUD_BUFFER).l,a0
        moveq   #0,d0
        move.w  #((RENDERER_HUD_END-RENDERER_HUD_BUFFER)/4)-1,d1
.clear_hud:
        move.l  d0,(a0)+
        dbf     d1,.clear_hud

        lea     RendererHudTime(pc),a0
        lea     (RENDERER_HUD_ROW_0).l,a1
        moveq   #VISIBLE_WIDTH_CELLS,d0
        move.w  #RENDERER_HUD_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText
        lea     RendererHudStage(pc),a0
        lea     (RENDERER_HUD_ROW_0+24).l,a1
        moveq   #VISIBLE_WIDTH_CELLS-12,d0
        move.w  #RENDERER_HUD_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText
        moveq   #0,d0
        move.b  (VOCAB_CAMPAIGN_DIRECTORY_INDEX).l,d0
        cmpi.w  #98,d0
        bhs.s   .stage_saturated
        addq.w  #1,d0
        bra.s   .stage_ready
.stage_saturated:
        moveq   #99,d0
.stage_ready:
        move.w  #RENDERER_HUD_VALUE_ATTRIBUTE,d1
        lea     (RENDERER_HUD_ROW_0+30).l,a0
        bsr.w   RendererHudWriteTwoDigits
        lea     RendererHudTry(pc),a0
        lea     (RENDERER_HUD_ROW_0+40).l,a1
        moveq   #VISIBLE_WIDTH_CELLS-20,d0
        move.w  #RENDERER_HUD_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText
        lea     RendererHudEcho(pc),a0
        lea     (RENDERER_HUD_ROW_0+60).l,a1
        moveq   #VISIBLE_WIDTH_CELLS-30,d0
        move.w  #RENDERER_HUD_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText

; Remaining time is exact authoritative frames, shown as seconds:frames.
        moveq   #0,d3
        move.w  (ENGINE_TICK).l,d3
        cmpi.w  #ENGINE_ATTEMPT_TICKS,d3
        bls.s   .tick_valid
        move.w  #ENGINE_ATTEMPT_TICKS,d3
.tick_valid:
        move.w  d3,(RENDERER_LAST_TICK).l
        move.w  #ENGINE_ATTEMPT_TICKS,d4
        sub.w   d3,d4
        moveq   #0,d0
        move.w  d4,d0
        divu.w  #60,d0
        moveq   #0,d5
        move.w  d0,d5
        swap    d0
        moveq   #0,d6
        move.w  d0,d6

        move.w  d5,d0
        move.w  #RENDERER_HUD_VALUE_ATTRIBUTE,d1
        lea     (RENDERER_HUD_ROW_0+10).l,a0
        bsr.w   RendererHudWriteTwoDigits
        move.w  #RENDERER_HUD_VALUE_ATTRIBUTE+RENDERER_TILE_COLON,(RENDERER_HUD_ROW_0+14).l
        move.w  d6,d0
        move.w  #RENDERER_HUD_VALUE_ATTRIBUTE,d1
        lea     (RENDERER_HUD_ROW_0+16).l,a0
        bsr.w   RendererHudWriteTwoDigits

        moveq   #0,d0
        move.w  (ENGINE_ATTEMPT).l,d0
        cmpi.w  #998,d0
        bhs.s   .attempt_saturated
        addq.w  #1,d0
        bra.s   .attempt_ready
.attempt_saturated:
        move.w  #999,d0
.attempt_ready:
        move.w  #RENDERER_HUD_VALUE_ATTRIBUTE,d1
        lea     (RENDERER_HUD_ROW_0+48).l,a0
        bsr.w   RendererHudWriteThreeDigits

        lea     RendererHudEchoes(pc),a0
        lea     (RENDERER_HUD_ROW_1).l,a1
        moveq   #VISIBLE_WIDTH_CELLS,d0
        move.w  #RENDERER_ECHO_HUD_ATTRIBUTE,d1
        bsr.w   RendererHudWriteText
        moveq   #0,d0
        move.b  (TIMELINE_ECHO_COUNT).l,d0
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d0
        bls.s   .echo_count_valid
        moveq   #GAMEPLAY_ECHO_LIMIT,d0
        ori.w   #RENDERER_FLAG_INVALID_STATE,(RENDERER_FLAGS).l
.echo_count_valid:
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        ori.w   #RENDERER_ECHO_HUD_ATTRIBUTE,d0
        move.w  d0,(RENDERER_HUD_ROW_0+70).l
        bsr.w   RendererHudBuildEchoSlots

; Mirror the two state categories which must remain explicit even when the
; underlying room art is visually crowded.
        move.w  #RENDERER_HUD_ATTRIBUTE,d7
        andi.w  #$FF9D,(RENDERER_FLAGS).l ; clear reset/replacement/stable mirrors
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        beq.s   .legacy_stable_state
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_COMPLETE,d0
        beq.s   .not_stable
        bra.s   .stable
.legacy_stable_state:
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_PROGRAM_STABLE,d0
        beq.s   .not_stable
.stable:
        ori.w   #RENDERER_FLAG_PROGRAM_STABLE,(RENDERER_FLAGS).l
        move.w  #RENDERER_DATA_ATTRIBUTE,d7
        moveq   #0,d0
        move.b  (VOCAB_STAGE_RANK).l,d0
        cmpi.w  #VOCAB_RANK_TRANSCENDENT,d0
        beq.s   .stable_transcendent
        cmpi.w  #VOCAB_RANK_OPTIMIZED,d0
        beq.s   .stable_optimized
        lea     RendererHudProgramStable(pc),a0
        bra.w   .write_status
.stable_optimized:
        lea     RendererHudProgramOptimized(pc),a0
        bra.w   .write_status
.stable_transcendent:
        lea     RendererHudProgramTranscendent(pc),a0
        bra.w   .write_status
.not_stable:
        tst.w   (RENDERER_RESET_PHASE).l
        beq.s   .not_reset
        ori.w   #RENDERER_FLAG_RESET_ACTIVE,(RENDERER_FLAGS).l
        move.w  #RENDERER_RESET_HUD_ATTRIBUTE,d7
        lea     RendererHudResetTrace(pc),a0
        bra.w   .write_status
.not_reset:
        andi.w  #$FFFD,(RENDERER_FLAGS).l
        move.w  (VOCAB_TIMELINE_UI_FLAGS).l,d0
        andi.w  #VOCAB_TIMELINE_UI_FLAG_OPEN,d0
        beq.s   .not_timeline
        ori.w   #RENDERER_FLAG_REPLACEMENT,(RENDERER_FLAGS).l
        move.w  #RENDERER_ECHO_HUD_ATTRIBUTE,d7
        lea     RendererHudTimeline(pc),a0
        bra.w   .write_status
.not_timeline:
; A successful reset leaves replacement metadata armed for the next future
; commit. While the engine is active that latent state must not preempt the
; live room objective; an explicitly opened timeline above still wins.
        cmpi.w  #ENGINE_STATE_ACTIVE,(ENGINE_STATE).l
        beq.w   .ordinary_status
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_REPLACEMENT_VISIBLE,d0
        bne.s   .replacement
        moveq   #0,d0
        move.b  (TIMELINE_FLAGS).l,d0
        andi.w  #TIMELINE_FLAG_REPLACEMENT_REQUIRED,d0
        beq.s   .ordinary_status
.replacement:
        ori.w   #RENDERER_FLAG_REPLACEMENT,(RENDERER_FLAGS).l
        move.w  #RENDERER_ECHO_HUD_ATTRIBUTE,d7
        moveq   #0,d0
        move.b  (TIMELINE_FLAGS).l,d0
        andi.w  #TIMELINE_FLAG_SELECTION_VALID,d0
        beq.s   .replacement_unselected
        lea     RendererHudReplaceSelected(pc),a0
        lea     (RENDERER_HUD_ROW_2).l,a1
        moveq   #VISIBLE_WIDTH_CELLS,d0
        move.w  d7,d1
        bsr.w   RendererHudWriteText
        moveq   #0,d0
        move.b  (TIMELINE_SELECTED_ORDINAL).l,d0
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d0
        blo.s   .selected_valid
        moveq   #GAMEPLAY_ECHO_LIMIT-1,d0
        ori.w   #RENDERER_FLAG_INVALID_STATE,(RENDERER_FLAGS).l
.selected_valid:
        addq.w  #1,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        ori.w   #RENDERER_ECHO_HUD_ATTRIBUTE,d0
        move.w  d0,(RENDERER_HUD_ROW_2+18).l
        bra.w   .done
.replacement_unselected:
        lea     RendererHudReplacePrompt(pc),a0
        bra.w   .write_status
.ordinary_status:
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        beq.s   .legacy_ordinary_status
        bsr.w   RendererHudCampaignStatus
        move.l  d1,d6
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE,d7
        tst.l   d6
        beq.s   .campaign_status_bits
        lea     RendererHudCampaignCommitHold(pc),a0
        lea     (RENDERER_HUD_ROW_2).l,a1
        moveq   #VISIBLE_WIDTH_CELLS,d0
        move.w  d7,d1
        bsr.w   RendererHudWriteText
        moveq   #0,d0
        move.w  d6,d0
        move.w  d7,d1
        lea     (RENDERER_HUD_ROW_2+24).l,a0
        bsr.w   RendererHudWriteTwoDigits
        swap    d6
        moveq   #0,d0
        move.w  d6,d0
        move.w  d7,d1
        lea     (RENDERER_HUD_ROW_2+30).l,a0
        bsr.w   RendererHudWriteTwoDigits
        bra.w   .done
.campaign_status_bits:
        move.w  d0,d1
        andi.w  #RENDERER_CAMPAIGN_STATUS_DOOR_OPEN,d1
        bne.s   .campaign_route_open
        move.w  d0,d1
        andi.w  #RENDERER_CAMPAIGN_STATUS_SWITCH_ACTIVE,d1
        bne.s   .campaign_switch_active
        andi.w  #RENDERER_CAMPAIGN_STATUS_DOOR_CLOSED,d0
        bne.s   .campaign_route_closed
        lea     RendererHudCampaignActive(pc),a0
        bra.s   .write_status
.campaign_route_open:
        lea     RendererHudCampaignRouteOpen(pc),a0
        bra.s   .write_status
.campaign_switch_active:
        lea     RendererHudCampaignSwitchActive(pc),a0
        bra.s   .write_status
.campaign_route_closed:
        lea     RendererHudCampaignRouteClosed(pc),a0
        bra.s   .write_status

.legacy_ordinary_status:
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_SWITCH_HELD,d0
        beq.s   .switch_clear
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE,d7
        lea     RendererHudDoorOpen(pc),a0
        bra.s   .write_status
.switch_clear:
        bsr.w   RendererDoorRejectionActive
        tst.w   d0
        beq.s   .door_closed
        move.w  #RENDERER_HAZARD_ATTRIBUTE,d7
        lea     RendererHudGateRejected(pc),a0
        bra.s   .write_status
.door_closed:
        lea     RendererHudDoorClosed(pc),a0
.write_status:
        lea     (RENDERER_HUD_ROW_2).l,a1
        moveq   #VISIBLE_WIDTH_CELLS,d0
        move.w  d7,d1
        bsr.w   RendererHudWriteText
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

; RendererHudCampaignStatus
; Returns a compact ordinary-status mirror derived only from active campaign
; device records. The 32-record loop is fixed and does not mutate gameplay.
; Out: d0.w=RENDERER_CAMPAIGN_STATUS_* bits;
;      d1.l=(completion duration << 16)|current hold timer, or zero.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererHudCampaignStatus:
        movem.l d3-d6/a2,-(a7)
        moveq   #0,d3
        moveq   #0,d6
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        beq.w   .status_ready
        lea     (DEVICE_POOL).l,a2
        moveq   #DEVICE_CAPACITY-1,d5
.device:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .next
        move.w  (OBJECT_PARAM,a2),d4
        andi.w  #VOCAB_PARAM_KIND_MASK,d4
        move.w  (OBJECT_STATE,a2),d1
        cmpi.w  #VOCAB_DEVICE_PRESSURE_SWITCH,d4
        beq.s   .switch
        cmpi.w  #VOCAB_DEVICE_TIMED_SWITCH,d4
        beq.s   .switch
        cmpi.w  #VOCAB_DEVICE_DOOR,d4
        beq.w   .door
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d4
        beq.s   .terminal
        cmpi.w  #VOCAB_DEVICE_REPAIR_TERMINAL,d4
        beq.s   .terminal
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d4
        bne.w   .next
.terminal:
        ori.w   #RENDERER_CAMPAIGN_STATUS_TERMINAL,d3
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d4
        bne.s   .next
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        cmp.b   (VOCAB_CAMPAIGN_COMPLETION_OBJECT).l,d0
        bne.s   .next
        move.w  d1,d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_HACKED|VOCAB_DEVICE_STATE_DISABLED,d0
        cmpi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        bne.s   .next
        moveq   #0,d0
        move.w  (VOCAB_DEVICE_HOLD_OWNER,a2),d0
        cmpi.w  #GAMEPLAY_CURRENT_ACTOR_SLOT+1,d0
        bne.s   .next
        moveq   #0,d4
        move.w  (OBJECT_TIMER,a2),d4
        beq.s   .next
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        bsr.w   CampaignGetSemanticDuration
        tst.w   d1
        bne.s   .next
        tst.w   d0
        beq.s   .next
        cmpi.w  #99,d0
        bhi.s   .next
        cmp.w   d0,d4
        bhs.s   .next
        move.w  d0,d6
        swap    d6
        move.w  d4,d6
        bra.s   .next
.switch:
        move.w  d1,d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .next
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d1
        beq.s   .next
        ori.w   #RENDERER_CAMPAIGN_STATUS_SWITCH_ACTIVE,d3
        bra.s   .next
.door:
        move.w  d1,d0
        andi.w  #VOCAB_DEVICE_STATE_OPEN|VOCAB_DEVICE_STATE_DISABLED,d0
        beq.s   .door_closed
        ori.w   #RENDERER_CAMPAIGN_STATUS_DOOR_OPEN,d3
        bra.s   .next
.door_closed:
        ori.w   #RENDERER_CAMPAIGN_STATUS_DOOR_CLOSED,d3
.next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d5,.device
.status_ready:
        move.w  d3,d0
        move.l  d6,d1
        movem.l (a7)+,d3-d6/a2
        rts

; RendererHudBuildEchoSlots
; Writes six stable E-number-symbol identities. Active order is the gameplay
; chronological order; protected and selected descriptors get explicit glyphs.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererHudBuildEchoSlots:
        movem.l d3-d7/a2-a3,-(a7)
        moveq   #0,d6
        move.b  (TIMELINE_ECHO_COUNT).l,d6
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d6
        bls.s   .count_ready
        moveq   #GAMEPLAY_ECHO_LIMIT,d6
.count_ready:
        moveq   #0,d3
        lea     (RENDERER_HUD_ROW_1+14).l,a2
        lea     (TIMELINE_ECHO_ORDER).l,a3
.slot:
        move.w  #RENDERER_HUD_DIM_ATTRIBUTE,d5
        move.w  #RENDERER_TILE_DASH,d4
        cmp.w   d6,d3
        bhs.s   .emit
        move.w  #RENDERER_ECHO_HUD_ATTRIBUTE,d5
        move.w  #RENDERER_TILE_ECHO_MARK,d4

        moveq   #0,d0
        move.b  (a3,d3.w),d0
        cmpi.w  #REPLAY_BANK_COUNT,d0
        bhs.s   .invalid_bank
        lsl.w   #3,d0
        lea     (REPLAY_DESCRIPTOR_BASE).l,a0
        adda.w  d0,a0
        moveq   #0,d0
        move.b  (REPLAY_DESCRIPTOR_FLAGS,a0),d0
        andi.w  #REPLAY_FLAG_PROTECTED,d0
        beq.s   .selection
        move.w  #RENDERER_TILE_LOCK,d4
.selection:
        moveq   #0,d0
        move.b  (TIMELINE_FLAGS).l,d0
        andi.w  #TIMELINE_FLAG_SELECTION_VALID,d0
        beq.s   .emit
        moveq   #0,d0
        move.b  (TIMELINE_SELECTED_ORDINAL).l,d0
        cmp.w   d3,d0
        bne.s   .emit
        move.w  #RENDERER_TILE_SELECTION,d4
        bra.s   .emit
.invalid_bank:
        move.w  #RENDERER_TILE_LOCK,d4
        ori.w   #RENDERER_FLAG_INVALID_STATE,(RENDERER_FLAGS).l
.emit:
        move.w  #DIAGNOSTIC_LETTER_A_TILE+4,d0 ; E
        or.w    d5,d0
        move.w  d0,(a2)
        move.w  d3,d0
        addq.w  #1,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        or.w    d5,d0
        move.w  d0,2(a2)
        or.w    d5,d4
        move.w  d4,4(a2)
        adda.w  #8,a2
        addq.w  #1,d3
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d3
        blo.w   .slot
        movem.l (a7)+,d3-d7/a2-a3
        rts

; RendererHudWriteText
; In: a0=zero-terminated ASCII, a1=tile destination, d0.w=max cells,
; d1.w=tile attribute. Unsupported characters become blanks.
; Clobbers d0-d2/a0-a1.
RendererHudWriteText:
        tst.w   d0
        beq.s   .done
        subq.w  #1,d0
.character:
        moveq   #0,d2
        move.b  (a0)+,d2
        beq.s   .done
        cmpi.b  #$20,d2
        beq.s   .blank
        cmpi.b  #$30,d2
        blo.s   .punctuation
        cmpi.b  #$39,d2
        bhi.s   .letter
        subi.w  #$30,d2
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d2
        bra.s   .attribute
.letter:
        cmpi.b  #$41,d2
        blo.s   .punctuation
        cmpi.b  #$5A,d2
        bhi.s   .punctuation
        subi.w  #$41,d2
        addi.w  #DIAGNOSTIC_LETTER_A_TILE,d2
        bra.s   .attribute
.punctuation:
        cmpi.b  #$3A,d2
        bne.s   .try_dot
        move.w  #RENDERER_TILE_COLON,d2
        bra.s   .attribute
.try_dot:
        cmpi.b  #$2E,d2
        bne.s   .try_slash
        move.w  #RENDERER_TILE_DOT,d2
        bra.s   .attribute
.try_slash:
        cmpi.b  #$2F,d2
        bne.s   .try_dash
        move.w  #RENDERER_TILE_SLASH,d2
        bra.s   .attribute
.try_dash:
        cmpi.b  #$2D,d2
        bne.s   .blank
        move.w  #RENDERER_TILE_DASH,d2
        bra.s   .attribute
.blank:
        moveq   #0,d2
        bra.s   .write
.attribute:
        or.w    d1,d2
.write:
        move.w  d2,(a1)+
        dbf     d0,.character
.done:
        rts

; RendererHudWriteTwoDigits
; In: d0.w=0..99, d1.w=attribute, a0=two-cell destination.
; Clobbers d0-d2/a0-a1.
RendererHudWriteTwoDigits:
        andi.l  #$0000FFFF,d0
        divu.w  #10,d0
        moveq   #0,d2
        move.w  d0,d2
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d2
        or.w    d1,d2
        move.w  d2,(a0)+
        swap    d0
        andi.w  #$000F,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        or.w    d1,d0
        move.w  d0,(a0)
        rts

; RendererHudWriteThreeDigits
; In: d0.w=0..999, d1.w=attribute, a0=three-cell destination.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererHudWriteThreeDigits:
        move.l  d3,-(a7)
        andi.l  #$0000FFFF,d0
        divu.w  #100,d0
        moveq   #0,d2
        move.w  d0,d2
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d2
        or.w    d1,d2
        move.w  d2,(a0)+
        swap    d0
        moveq   #0,d3
        move.w  d0,d3
        move.l  d3,d0
        divu.w  #10,d0
        moveq   #0,d2
        move.w  d0,d2
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d2
        or.w    d1,d2
        move.w  d2,(a0)+
        swap    d0
        andi.w  #$000F,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        or.w    d1,d0
        move.w  d0,(a0)
        move.l  (a7)+,d3
        rts

RendererHudStage:
        dc.b    "STG",0
RendererHudTime:
        dc.b    "TIME",0
RendererHudTry:
        dc.b    "TRY",0
RendererHudEcho:
        dc.b    "ECHO",0
RendererHudEchoes:
        dc.b    "ECHOES",0
RendererHudProgramStable:
        dc.b    "PROGRAM STABLE  EAST LINK COMPLETE",0
RendererHudProgramOptimized:
        dc.b    "PROGRAM OPTIMIZED  TRACE COMPACT",0
RendererHudProgramTranscendent:
        dc.b    "PROGRAM TRANSCENDENT  ZERO WASTE",0
RendererHudResetTrace:
        dc.b    "RESET IN PROGRESS  WORLD RESTORED",0
RendererHudTimeline:
        dc.b    "TIMELINE  LEFT RIGHT SELECT  C COMMIT",0
RendererHudReplaceSelected:
        dc.b    "REPLACE E",0
RendererHudReplacePrompt:
        dc.b    "REPLACE  SELECT ECHO",0
RendererHudDoorOpen:
        dc.b    "ROUTE OPEN  CROSS TO EAST TERMINAL",0
RendererHudGateRejected:
        dc.b    "GATE SEALED  ECHO MUST HOLD SWITCH",0
RendererHudDoorClosed:
        dc.b    "OBJECTIVE  HOLD SWITCH  OPEN GATE",0
RendererHudCampaignRouteOpen:
        dc.b    "ROUTE OPEN  FOLLOW CAMPAIGN OBJECTIVE",0
RendererHudCampaignSwitchActive:
        dc.b    "SWITCH ACTIVE  ROUTE UPDATE PENDING",0
RendererHudCampaignRouteClosed:
        dc.b    "ROUTE SEALED  ACTIVATE LINKED DEVICE",0
RendererHudCampaignActive:
        dc.b    "CAMPAIGN ACTIVE  TRACE OBJECTIVES",0
RendererHudCampaignCommitHold:
        dc.b    "COMMIT HOLD 00/00 KEEP B HELD",0
RendererWorldStableLine0:
        dc.b    "PROGRAM STABLE",0
RendererWorldStableLine1:
        dc.b    "EAST LINK COMPLETE",0
        even

        assert  RendererHudTry-RendererHudTime<=8,"HUD TIME label unexpectedly long"
        assert  RendererHudProgramStable-RendererHudEchoes<=8,"HUD ECHOES label unexpectedly long"
