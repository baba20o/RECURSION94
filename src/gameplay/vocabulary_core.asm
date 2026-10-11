; Milestone 5 unwired vocabulary control, dash/phase, anchors, stage results,
; bounded update order, and extension projection.

; VocabularyInitialize
; Initializes only $FF1600-$FF17FF. It does not mutate the frozen M4 ABI.
; Out: d0.w=VOCAB_RESULT_OK. Clobbers d0-d2/a0-a1.
VocabularyInitialize:
        lea     (VOCAB_STATE_BASE).l,a0
        moveq   #0,d0
        move.w  #(VOCAB_STATE_BYTES_VALUE/4)-1,d1
.clear:
        move.l  d0,(a0)+
        dbf     d1,.clear

        move.l  #VOCAB_MAGIC_VALUE,(VOCAB_MAGIC).l
        move.w  #VOCAB_VERSION_VALUE,(VOCAB_VERSION).l
        move.w  #VOCAB_STATE_BYTES_VALUE,(VOCAB_BYTES).l
        move.w  #VOCAB_PROJECTION_BYTES,(VOCAB_PROJECTION_LENGTH).l
        move.w  #VOCAB_FLAG_INITIALIZED,(VOCAB_FLAGS).l
        move.b  #1,(VOCAB_ANCHOR_CAPACITY).l
        move.w  (ENGINE_ENCOUNTER_ID).l,d0
        move.w  d0,(VOCAB_STAGE_ID).l
        bsr.w   VocabularyResetOrdinary
        moveq   #VOCAB_RESULT_OK,d0
        rts

; VocabularyResetOrdinary
; Clears extension-owned ordinary state after EngineReset, preserves typed
; anchors and stage identity, then deterministically reapplies approved state.
; Out: d0.w=VOCAB_RESULT_OK. Clobbers d0-d2/a0-a1.
VocabularyResetOrdinary:
        clr.w   (VOCAB_RESULT).l
        clr.w   (VOCAB_OVERFLOW_FLAGS).l
        clr.w   (VOCAB_UPDATE_UNITS).l
        clr.w   (VOCAB_UPDATE_PEAK).l
        clr.w   (VOCAB_INTERACTION_RESULT).l
        clr.w   (VOCAB_STAGE_FLAGS).l
        clr.w   (VOCAB_STAGE_FINAL_TICK).l
        clr.w   (VOCAB_STAGE_OPTIONAL_METRIC).l
        clr.b   (VOCAB_STAGE_RANK).l
        clr.b   (VOCAB_BEAM_COUNT).l
        clr.b   (VOCAB_ACTIVE_ENEMY_COUNT).l
        clr.b   (VOCAB_ACTIVE_PROJECTILE_COUNT).l
        move.w  (VOCAB_FLAGS).l,d0
        andi.w  #VOCAB_FLAG_INITIALIZED|VOCAB_FLAG_ANCHOR_DIRTY,d0
        move.w  d0,(VOCAB_FLAGS).l

        moveq   #0,d0
        move.w  (ENGINE_ATTEMPT).l,d0
        cmpi.w  #$FFFF,d0
        beq.s   .attempt_ready
        addq.w  #1,d0
.attempt_ready:
        move.w  d0,(VOCAB_STAGE_ATTEMPTS).l
        moveq   #0,d0
        move.b  (TIMELINE_ECHO_COUNT).l,d0
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d0
        bls.s   .echo_ready
        moveq   #GAMEPLAY_ECHO_LIMIT,d0
        ori.w   #VOCAB_OVERFLOW_TARGET,(VOCAB_OVERFLOW_FLAGS).l
.echo_ready:
        move.b  d0,(VOCAB_STAGE_ECHO_COUNT).l

        lea     (VOCAB_ACTOR_BASE).l,a0
        moveq   #0,d0
        move.w  #((VOCAB_BEAM_END-VOCAB_ACTOR_BASE)/4)-1,d1
.clear_ordinary:
        move.l  d0,(a0)+
        dbf     d1,.clear_ordinary

        lea     (VOCAB_ACTOR_BASE+VOCAB_ACTOR_CARRY_SLOT).l,a0
        moveq   #ACTOR_CAPACITY-1,d1
.clear_carry:
        move.b  #VOCAB_NO_SLOT,(a0)
        adda.w  #VOCAB_ACTOR_BYTES,a0
        dbf     d1,.clear_carry
        lea     (VOCAB_ENEMY_TARGET_BASE).l,a0
        moveq   #-1,d0
        moveq   #(VOCAB_ENEMY_TARGET_BYTES/4)-1,d1
.clear_targets:
        move.l  d0,(a0)+
        dbf     d1,.clear_targets

        bsr.w   VocabularyApplyAnchors
        moveq   #VOCAB_RESULT_OK,d0
        move.w  d0,(VOCAB_RESULT).l
        rts

; VocabularyGetActorExtension
; In: a0=aligned actor slot. Out: d0.w=physical index and a1=extension,
; or d0.w=$FFFF for an invalid pointer. Clobbers d0-d2/a1.
VocabularyGetActorExtension:
        move.l  a0,d0
        sub.l   #ENGINE_ACTOR_BASE,d0
        bmi.s   .invalid
        cmpi.l  #ACTOR_CAPACITY*ACTOR_BYTES,d0
        bhs.s   .invalid
        move.l  d0,d1
        andi.w  #ACTOR_BYTES-1,d1
        bne.s   .invalid
        lsr.l   #5,d0
        move.w  d0,d1
        lsl.w   #3,d1
        lea     (VOCAB_ACTOR_BASE).l,a1
        adda.w  d1,a1
        rts
.invalid:
        moveq   #-1,d0
        lea     (VOCAB_ACTOR_BASE).l,a1
        rts

; VocabularyApplyDashPhase
; In: a0=actor, d0.b=sanitized held, d1.b=pressed.
; C edge starts a six-tick collision-phase dash if cooldown is zero.
; Out: d0.w=result. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyApplyDashPhase:
        movem.l d3-d6/a2,-(a7)
        movea.l a0,a2
; VocabularyGetActorExtension returns its byte offset in d1, so preserve the
; caller's pressed-edge mask before that documented d0-d2 clobber. Otherwise
; physical slot 6 returns $30 in d1 and a real C edge is silently lost.
        moveq   #0,d6
        move.b  d1,d6
        tst.b   (ACTOR_ACTIVE,a2)
        beq.w   .invalid
        bsr.w   VocabularyGetActorExtension
        cmpi.w  #$FFFF,d0
        beq.w   .invalid

        andi.b  #INPUT_C,d6
        bne.s   .edge
        move.w  (VOCAB_ACTOR_FLAGS,a1),d0
        andi.w  #VOCAB_ACTOR_FLAG_DASHING,d0
        beq.w   .no_action
        bra.s   .apply_velocity
.edge:
; An edge during an already-running dash sustains, rather than interrupts, its
; authored velocity window. Only a new dash consults the cooldown.
        move.w  (VOCAB_ACTOR_FLAGS,a1),d0
        andi.w  #VOCAB_ACTOR_FLAG_DASHING,d0
        bne.s   .apply_velocity
        tst.b   (VOCAB_ACTOR_DASH_COOLDOWN,a1)
        bne.w   .cooldown

        ori.w   #VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED|VOCAB_ACTOR_FLAG_INVULNERABLE,(VOCAB_ACTOR_FLAGS,a1)
        move.b  #VOCAB_DASH_TICKS,(VOCAB_ACTOR_DASH_TIMER,a1)
        move.b  #VOCAB_DASH_COOLDOWN_TICKS,(VOCAB_ACTOR_DASH_COOLDOWN,a1)
        move.b  #VOCAB_DASH_INVULN_TICKS,(VOCAB_ACTOR_INVULN_TIMER,a1)
        move.b  #VOCAB_RESULT_DASHED,(VOCAB_ACTOR_LAST_ACTION,a1)
        ori.w   #VOCAB_FLAG_PHASE_ACTIVE,(VOCAB_FLAGS).l

.apply_velocity:
        move.l  (ACTOR_VX,a2),d3
        move.l  (ACTOR_VY,a2),d4
        tst.l   d3
        bne.s   .direction_ready
        tst.l   d4
        bne.s   .direction_ready
        move.l  #VOCAB_DASH_CARDINAL_SPEED,d3
        move.w  (ACTOR_FLAGS,a2),d5
        andi.w  #GAMEPLAY_ACTOR_FLAG_FACING_LEFT,d5
        beq.s   .direction_ready
        neg.l   d3
.direction_ready:
        tst.l   d3
        beq.s   .dash_y
        tst.l   d4
        beq.s   .dash_x_cardinal
        move.l  #VOCAB_DASH_DIAGONAL_SPEED,d5
        tst.l   d3
        bpl.s   .dash_x_diagonal_ready
        neg.l   d5
.dash_x_diagonal_ready:
        move.l  d5,d3
        move.l  #VOCAB_DASH_DIAGONAL_SPEED,d5
        tst.l   d4
        bpl.s   .dash_y_diagonal_ready
        neg.l   d5
.dash_y_diagonal_ready:
        move.l  d5,d4
        bra.s   .store_velocity
.dash_x_cardinal:
        move.l  #VOCAB_DASH_CARDINAL_SPEED,d5
        tst.l   d3
        bpl.s   .dash_x_ready
        neg.l   d5
.dash_x_ready:
        move.l  d5,d3
        bra.s   .store_velocity
.dash_y:
        move.l  #VOCAB_DASH_CARDINAL_SPEED,d5
        tst.l   d4
        bpl.s   .dash_y_ready
        neg.l   d5
.dash_y_ready:
        move.l  d5,d4
.store_velocity:
        move.l  d3,(ACTOR_VX,a2)
        move.l  d4,(ACTOR_VY,a2)
        moveq   #VOCAB_RESULT_DASHED,d0
        bra.s   .done
.no_action:
        moveq   #VOCAB_RESULT_OK,d0
        bra.s   .done
.cooldown:
        moveq   #VOCAB_RESULT_COOLDOWN,d0
        bra.s   .done
.invalid:
        moveq   #VOCAB_RESULT_INVALID,d0
.done:
        move.w  d0,(VOCAB_RESULT).l
        movem.l (a7)+,d3-d6/a2
        rts

; VocabularyTickActorExtensions
; Advances exactly seven extension slots in physical actor order.
; Clobbers d0-d2/a0-a1.
VocabularyTickActorExtensions:
        lea     (VOCAB_ACTOR_BASE).l,a0
        moveq   #ACTOR_CAPACITY-1,d2
.actor:
        tst.b   (VOCAB_ACTOR_DASH_COOLDOWN,a0)
        beq.s   .dash_timer
        subq.b  #1,(VOCAB_ACTOR_DASH_COOLDOWN,a0)
.dash_timer:
        tst.b   (VOCAB_ACTOR_DASH_TIMER,a0)
        beq.s   .invuln_timer
        subq.b  #1,(VOCAB_ACTOR_DASH_TIMER,a0)
        bne.s   .invuln_timer
        andi.w  #$FFFC,(VOCAB_ACTOR_FLAGS,a0)
.invuln_timer:
        tst.b   (VOCAB_ACTOR_INVULN_TIMER,a0)
        beq.s   .shield_timer
        subq.b  #1,(VOCAB_ACTOR_INVULN_TIMER,a0)
        bne.s   .shield_timer
        andi.w  #$FFFB,(VOCAB_ACTOR_FLAGS,a0)
.shield_timer:
        tst.b   (VOCAB_ACTOR_SHIELD_TIMER,a0)
        beq.s   .advance
        subq.b  #1,(VOCAB_ACTOR_SHIELD_TIMER,a0)
        bne.s   .advance
        andi.w  #$FFEF,(VOCAB_ACTOR_FLAGS,a0)
.advance:
        adda.w  #VOCAB_ACTOR_BYTES,a0
        dbf     d2,.actor

        lea     (VOCAB_ACTOR_BASE).l,a0
        moveq   #ACTOR_CAPACITY-1,d2
.phase_scan:
        move.w  (VOCAB_ACTOR_FLAGS,a0),d0
        andi.w  #VOCAB_ACTOR_FLAG_PHASED,d0
        bne.s   .phase_present
        adda.w  #VOCAB_ACTOR_BYTES,a0
        dbf     d2,.phase_scan
        andi.w  #$FFFD,(VOCAB_FLAGS).l
.phase_present:
        rts

; VocabularyStep
; Proposed bounded M5 phase. It is deliberately unwired in the M4 ROM.
; Out: d0.w=result. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyStep:
        movem.l d3-d7/a2-a6,-(a7)
        cmpi.l  #VOCAB_MAGIC_VALUE,(VOCAB_MAGIC).l
        bne.s   .invalid
        move.w  (VOCAB_FLAGS).l,d0
        andi.w  #VOCAB_FLAG_INITIALIZED,d0
        beq.s   .invalid

        bsr.w   VocabularyTickActorExtensions
        bsr.w   VocabularyUpdateDevices
        bsr.w   VocabularyBuildBeamCache
        bsr.w   VocabularyUpdateEnemies
        bsr.w   VocabularyUpdateProjectilesAndDamage
        bsr.w   VocabularyUpdateActorsAndHazards
        move.w  #VOCAB_CPU_WORST_UNITS,(VOCAB_UPDATE_UNITS).l
        move.w  (VOCAB_UPDATE_PEAK).l,d0
        cmpi.w  #VOCAB_CPU_WORST_UNITS,d0
        bhs.s   .budget_ready
        move.w  #VOCAB_CPU_WORST_UNITS,(VOCAB_UPDATE_PEAK).l
.budget_ready:
        cmpi.w  #VOCAB_CPU_UNIT_LIMIT,(VOCAB_UPDATE_UNITS).l
        bls.s   .ok
        ori.w   #VOCAB_FLAG_BUDGET_EXCEEDED,(VOCAB_FLAGS).l
        ori.w   #VOCAB_OVERFLOW_UPDATE,(VOCAB_OVERFLOW_FLAGS).l
.ok:
        moveq   #VOCAB_RESULT_OK,d0
        bra.s   .done
.invalid:
        moveq   #VOCAB_RESULT_INVALID,d0
.done:
        move.w  d0,(VOCAB_RESULT).l
        movem.l (a7)+,d3-d7/a2-a6
        rts

; VocabularySetAnchor
; In: d0.b=anchor type, d1.b=logical ID, d2.w=idempotent state.
; Existing type/ID records update in place; otherwise the bounded lowest free
; record is used. Out: result. Clobbers d0-d2/a0-a1.
VocabularySetAnchor:
        movem.l d3-d6/a2,-(a7)
        move.w  d0,d3
        move.w  d1,d4
        move.w  d2,d5
        cmpi.w  #VOCAB_ANCHOR_DEVICE_STATE,d3
        blo.w   .forbidden
        cmpi.w  #VOCAB_ANCHOR_ECHO_PROTECTION,d3
        bhi.w   .forbidden

        moveq   #0,d6
        move.b  (VOCAB_ANCHOR_COUNT).l,d6
        cmpi.w  #VOCAB_ANCHOR_LIMIT,d6
        bhi.s   .invalid
        lea     (VOCAB_ANCHOR_BASE).l,a2
        moveq   #0,d0
        tst.w   d6
        beq.s   .new_record
        move.w  d6,d1
        subq.w  #1,d1
.find:
        cmp.b   (VOCAB_ANCHOR_TYPE,a2),d3
        bne.s   .next
        cmp.b   (VOCAB_ANCHOR_LOGICAL_ID,a2),d4
        beq.s   .write
.next:
        adda.w  #VOCAB_ANCHOR_BYTES,a2
        addq.w  #1,d0
        dbf     d1,.find
.new_record:
        moveq   #0,d1
        move.b  (VOCAB_ANCHOR_CAPACITY).l,d1
        cmp.w   d1,d6
        bhs.s   .full
        cmpi.w  #VOCAB_ANCHOR_LIMIT,d6
        bhs.s   .full
        move.w  d6,d0
        lsl.w   #3,d0
        lea     (VOCAB_ANCHOR_BASE).l,a2
        adda.w  d0,a2
        addq.b  #1,(VOCAB_ANCHOR_COUNT).l
.write:
        move.b  d3,(VOCAB_ANCHOR_TYPE,a2)
        move.b  d4,(VOCAB_ANCHOR_LOGICAL_ID,a2)
        move.w  d5,(VOCAB_ANCHOR_STATE,a2)
        clr.l   (VOCAB_ANCHOR_PARAM,a2)
        ori.w   #VOCAB_FLAG_ANCHOR_DIRTY,(VOCAB_FLAGS).l
        moveq   #VOCAB_RESULT_OK,d0
        bra.s   .done
.full:
        ori.w   #VOCAB_OVERFLOW_ANCHOR,(VOCAB_OVERFLOW_FLAGS).l
        moveq   #VOCAB_RESULT_ANCHOR_FULL,d0
        bra.s   .done
.forbidden:
        moveq   #VOCAB_RESULT_ANCHOR_FORBIDDEN,d0
        bra.s   .done
.invalid:
        moveq   #VOCAB_RESULT_INVALID,d0
.done:
        move.w  d0,(VOCAB_RESULT).l
        movem.l (a7)+,d3-d6/a2
        rts

; VocabularyApplyAnchors
; Replays at most two typed records after canonical ordinary reconstruction.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyApplyAnchors:
        movem.l d3-d7/a2-a4,-(a7)
        moveq   #0,d7
        move.b  (VOCAB_ANCHOR_COUNT).l,d7
        cmpi.w  #VOCAB_ANCHOR_LIMIT,d7
        bhi.w   .invalid
        tst.w   d7
        beq.w   .done_ok
        subq.w  #1,d7
        lea     (VOCAB_ANCHOR_BASE).l,a4
.record:
        moveq   #0,d3
        move.b  (VOCAB_ANCHOR_TYPE,a4),d3
        moveq   #0,d4
        move.b  (VOCAB_ANCHOR_LOGICAL_ID,a4),d4
        move.w  (VOCAB_ANCHOR_STATE,a4),d5
        cmpi.w  #VOCAB_ANCHOR_ECHO_PROTECTION,d3
        beq.w   .echo_anchor
        cmpi.w  #VOCAB_ANCHOR_SOCKETED_CELL,d3
        beq.s   .cell_anchor

        lea     (DEVICE_POOL).l,a2
        moveq   #DEVICE_CAPACITY-1,d6
.device:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .device_next
        cmp.b   (OBJECT_LOGICAL_ID,a2),d4
        bne.s   .device_next
        move.w  d5,(OBJECT_STATE,a2)
        ori.w   #VOCAB_DEVICE_STATE_ANCHORED,(OBJECT_STATE,a2)
        bra.w   .next_record
.device_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d6,.device
        bra.w   .next_record

.cell_anchor:
        lea     (CARRYABLE_POOL).l,a2
        moveq   #CARRYABLE_CAPACITY-1,d6
.cell:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .cell_next
        cmp.b   (OBJECT_LOGICAL_ID,a2),d4
        bne.s   .cell_next
        move.w  #VOCAB_CARRY_STATE_SOCKETED,(OBJECT_STATE,a2)
        move.w  d5,(OBJECT_PARAM,a2)
        bra.s   .socket_device
.cell_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d6,.cell
        bra.s   .next_record
.socket_device:
        lea     (DEVICE_POOL).l,a2
        moveq   #DEVICE_CAPACITY-1,d6
.socket:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .socket_next
        move.w  (OBJECT_PARAM,a2),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SOCKET,d0
        bne.s   .socket_next
        cmp.b   (OBJECT_LOGICAL_ID,a2),d5
        bne.s   .socket_next
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_OCCUPIED|VOCAB_DEVICE_STATE_ANCHORED,(OBJECT_STATE,a2)
        bra.s   .next_record
.socket_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d6,.socket
        bra.s   .next_record

.echo_anchor:
        move.w  d4,d0
        moveq   #0,d1
        tst.w   d5
        beq.s   .echo_value_ready
        moveq   #1,d1
.echo_value_ready:
        bsr.w   GameplaySetEchoProtected

.next_record:
        adda.w  #VOCAB_ANCHOR_BYTES,a4
        dbf     d7,.record
.done_ok:
        moveq   #VOCAB_RESULT_OK,d0
        bra.s   .done
.invalid:
        ori.w   #VOCAB_OVERFLOW_ANCHOR,(VOCAB_OVERFLOW_FLAGS).l
        moveq   #VOCAB_RESULT_INVALID,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; VocabularyRequestEchoReplacement
; In: d0.w=chronological echo ordinal. Out: frozen gameplay result.
VocabularyRequestEchoReplacement:
        bsr.w   GameplaySelectReplacement
        rts

; VocabularyFinalizeStage
; In: d0.w=stage ID, d1.w=optional authored metric.
; Produces STABLE, OPTIMIZED, or TRANSCENDENT from deterministic counters.
; Out: d0.w=VOCAB_RESULT_STAGE_COMPLETE. Clobbers d0-d2/a0-a1.
VocabularyFinalizeStage:
        move.w  d0,(VOCAB_STAGE_ID).l
        move.w  d1,(VOCAB_STAGE_OPTIONAL_METRIC).l
        moveq   #0,d0
        move.w  (ENGINE_ATTEMPT).l,d0
        cmpi.w  #$FFFF,d0
        beq.s   .attempt_ready
        addq.w  #1,d0
.attempt_ready:
        move.w  d0,(VOCAB_STAGE_ATTEMPTS).l
; Completion occurs inside the authoritative step before ENGINE_TICK advances.
; Publish the consumed one-based tick so result timing matches STABLE_TICK.
        move.w  (ENGINE_TICK).l,d1
        addq.w  #1,d1
        cmpi.w  #ENGINE_ATTEMPT_TICKS,d1
        bls.s   .tick_ready
        move.w  #ENGINE_ATTEMPT_TICKS,d1
.tick_ready:
        move.w  d1,(VOCAB_STAGE_FINAL_TICK).l
        moveq   #0,d2
        move.b  (TIMELINE_ECHO_COUNT).l,d2
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d2
        bls.s   .echo_ready
        moveq   #GAMEPLAY_ECHO_LIMIT,d2
.echo_ready:
        move.b  d2,(VOCAB_STAGE_ECHO_COUNT).l
        move.w  #VOCAB_STAGE_FLAG_CLEARED,(VOCAB_STAGE_FLAGS).l
        move.b  #VOCAB_RANK_STABLE,(VOCAB_STAGE_RANK).l

        cmpi.w  #VOCAB_OPTIMIZED_ATTEMPT_MAX,d0
        bhi.s   .rank_ready
        cmpi.w  #VOCAB_OPTIMIZED_ECHO_MAX,d2
        bhi.s   .rank_ready
        ori.w   #VOCAB_STAGE_FLAG_OPTIMIZED,(VOCAB_STAGE_FLAGS).l
        move.b  #VOCAB_RANK_OPTIMIZED,(VOCAB_STAGE_RANK).l

        cmpi.w  #VOCAB_TRANSCENDENT_ATTEMPT_MAX,d0
        bhi.s   .rank_ready
        cmpi.w  #VOCAB_TRANSCENDENT_ECHO_MAX,d2
        bhi.s   .rank_ready
        move.w  #ENGINE_ATTEMPT_TICKS,d0
        sub.w   d1,d0
        cmpi.w  #VOCAB_TRANSCENDENT_TIME_MIN,d0
        blo.s   .rank_ready
        ori.w   #VOCAB_STAGE_FLAG_TRANSCENDENT,(VOCAB_STAGE_FLAGS).l
        move.b  #VOCAB_RANK_TRANSCENDENT,(VOCAB_STAGE_RANK).l
.rank_ready:
        ori.w   #VOCAB_FLAG_STAGE_COMPLETE,(VOCAB_FLAGS).l
        moveq   #VOCAB_RESULT_STAGE_COMPLETE,d0
        move.w  d0,(VOCAB_RESULT).l
        rts

; VocabularyBuildProjectionTail
; In: a0=destination immediately after frozen projection.
; Out: d0.w=256 bytes, a0=advanced. Copies a fixed authoritative prefix.
; Clobbers d0-d2/a0-a1.
VocabularyBuildProjectionTail:
        lea     (VOCAB_STATE_BASE).l,a1
        move.w  #(VOCAB_PROJECTION_BYTES/4)-1,d1
.copy:
        move.l  (a1)+,(a0)+
        dbf     d1,.copy
        move.w  #VOCAB_PROJECTION_BYTES,d0
        rts
