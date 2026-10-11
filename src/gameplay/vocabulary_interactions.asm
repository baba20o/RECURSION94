; Milestone 5 unwired B-button vocabulary, devices, carryables, conveyors,
; and bounded beam/reflector cache construction.

; VocabularyWithinRange
; In: a0=actor, a1=object, d0.l=unsigned 16.8 axis range.
; Out: d0.w=1 when both axis distances are within range, otherwise zero.
; Clobbers d0-d2.
VocabularyWithinRange:
        move.l  d0,d2
        move.l  (ACTOR_X,a0),d1
        sub.l   (OBJECT_X,a1),d1
        bpl.s   .x_positive
        neg.l   d1
.x_positive:
        cmp.l   d2,d1
        bhi.s   .outside
        move.l  (ACTOR_Y,a0),d1
        sub.l   (OBJECT_Y,a1),d1
        bpl.s   .y_positive
        neg.l   d1
.y_positive:
        cmp.l   d2,d1
        bhi.s   .outside
        moveq   #1,d0
        rts
.outside:
        moveq   #0,d0
        rts

; VocabularyDropActorCarryable
; In: a0=actor, a1=its validated vocabulary extension. Releases a live owned
; cell at the actor's final position and always clears actor-side ownership.
; Invalid/stale slots fail closed and publish the bounded interaction overflow.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyDropActorCarryable:
        movem.l d3/a2-a3,-(a7)
        movea.l a0,a2
        movea.l a1,a3
        moveq   #0,d3
        move.b  (VOCAB_ACTOR_CARRY_SLOT,a3),d3
        cmpi.w  #VOCAB_NO_SLOT,d3
        beq.s   .clear_owner
        cmpi.w  #CARRYABLE_CAPACITY,d3
        bhs.s   .stale
        move.w  d3,d0
        mulu.w  #OBJECT_BYTES,d0
        lea     (CARRYABLE_POOL).l,a0
        adda.l  d0,a0
        tst.b   (OBJECT_ACTIVE,a0)
        beq.s   .stale
        move.w  (OBJECT_STATE,a0),d0
        andi.w  #VOCAB_CARRY_STATE_CARRIED|VOCAB_CARRY_STATE_SOCKETED,d0
        cmpi.w  #VOCAB_CARRY_STATE_CARRIED,d0
        bne.s   .stale
        move.w  (OBJECT_PARAM,a0),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_CARRYABLE_POWER_CELL,d0
        bne.s   .stale
        move.l  a2,d1
        subi.l  #ENGINE_ACTOR_BASE,d1
        lsr.l   #5,d1
        addq.w  #1,d1
        cmp.b   (OBJECT_PARAM+1,a0),d1
        bne.s   .stale
        clr.w   (OBJECT_STATE,a0)
        move.w  #VOCAB_CARRYABLE_POWER_CELL,(OBJECT_PARAM,a0)
        move.l  (ACTOR_X,a2),d0
        move.l  d0,(OBJECT_X,a0)
        move.l  (ACTOR_Y,a2),d0
        move.l  d0,(OBJECT_Y,a0)
        bra.s   .clear_owner
.stale:
        ori.w   #VOCAB_OVERFLOW_INTERACTION,(VOCAB_OVERFLOW_FLAGS).l
.clear_owner:
        move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)
        andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)
        movem.l (a7)+,d3/a2-a3
        rts

; VocabularyAdvanceHackInteraction
; In: a0=active actor, a1=hack terminal, d0.w=physical actor index.
; Advances one uninterrupted held-B step. OBJECT_TIMER is elapsed progress;
; static device velocity words retain owner+1 and ENGINE_TICK+1 touch stamps.
; Authored semantic duration zero/malformed bindings fail closed to one step.
; Out: d0.w=ACTIVATED while progressing, HACKED on completion, or NO_TARGET.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyAdvanceHackInteraction:
        movem.l d3-d7/a2-a4,-(a7)
        movea.l a0,a2
        movea.l a1,a3
        move.w  d0,d3
        tst.b   (ACTOR_ACTIVE,a2)
        beq.w   .no_target
        tst.b   (OBJECT_ACTIVE,a3)
        beq.w   .no_target
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d0
        bne.w   .no_target
        move.w  (OBJECT_STATE,a3),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .no_target
        andi.w  #VOCAB_DEVICE_STATE_HACKED,d1
        bne.w   .already_hacked

; A generated dependency group is the terminal's lock predicate. Query its
; declared result field/mask rather than assuming ACTIVE or OPEN ownership.
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.s   .gate_ready
        movea.l a3,a0
        bsr.w   CampaignDependencyGateSatisfied
        tst.w   d0
        beq.w   .no_target
.gate_ready:

        moveq   #1,d4
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.s   .duration_ready
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a3),d0
        bsr.w   CampaignGetSemanticDuration
        tst.w   d1
        bne.s   .duration_ready
        tst.w   d0
        beq.s   .duration_ready
        move.w  d0,d4
.duration_ready:

        move.w  d3,d5
        addq.w  #1,d5                   ; zero remains "no owner"
        cmp.w   (VOCAB_DEVICE_HOLD_OWNER,a3),d5
        beq.s   .owner_ready
        clr.w   (OBJECT_TIMER,a3)
        clr.l   (OBJECT_VX,a3)
        clr.l   (OBJECT_VY,a3)
        move.w  d5,(VOCAB_DEVICE_HOLD_OWNER,a3)
.owner_ready:
        moveq   #0,d6
        move.w  (OBJECT_TIMER,a3),d6
        cmp.w   d4,d6
        bhs.s   .complete
        addq.w  #1,d6
        cmp.w   d4,d6
        bhs.s   .complete
        move.w  d6,(OBJECT_TIMER,a3)
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,(OBJECT_STATE,a3)
        moveq   #VOCAB_RESULT_ACTIVATED,d7
        bra.s   .touch
.complete:
        move.w  d4,(OBJECT_TIMER,a3)
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_HACKED,(OBJECT_STATE,a3)
        moveq   #VOCAB_RESULT_HACKED,d7
.touch:
        moveq   #0,d0
        move.w  (ENGINE_TICK).l,d0
        addq.w  #1,d0
        clr.l   (OBJECT_VY,a3)
        move.w  d0,(VOCAB_DEVICE_HOLD_TOUCHED,a3)
        move.w  d7,d0
        bra.s   .done
.already_hacked:
; Completion is published only by the actor that performs the exact final
; held step. Re-interacting with an already hacked terminal must not relabel
; an echo's completed hold as the current actor's campaign commit.
        moveq   #VOCAB_RESULT_NO_TARGET,d0
        bra.s   .done
.no_target:
        moveq   #VOCAB_RESULT_NO_TARGET,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; VocabularySustainHackInteraction
; In: a0=active actor holding B after its initial edge. Only the terminal that
; already names this actor as hold owner can advance; other interaction
; priorities are never re-entered on held frames.
; Out: d0.w=result. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularySustainHackInteraction:
        movem.l d3-d7/a2-a4,-(a7)
        movea.l a0,a2
        tst.b   (ACTOR_ACTIVE,a2)
        beq.w   .invalid
        bsr.w   VocabularyGetActorExtension
        cmpi.w  #$FFFF,d0
        beq.w   .invalid
        move.w  d0,d3
        movea.l a1,a4
        move.w  d3,d4
        addq.w  #1,d4
        lea     (DEVICE_POOL).l,a3
        moveq   #DEVICE_CAPACITY-1,d7
.terminal:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .next
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d0
        bne.s   .next
        cmp.w   (VOCAB_DEVICE_HOLD_OWNER,a3),d4
        bne.s   .next
        tst.w   (OBJECT_TIMER,a3)
        beq.s   .next
        movea.l a2,a0
        movea.l a3,a1
        move.l  #VOCAB_INTERACTION_RANGE_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        beq.s   .next
        movea.l a2,a0
        movea.l a3,a1
        move.w  d3,d0
        bsr.w   VocabularyAdvanceHackInteraction
        cmpi.w  #VOCAB_RESULT_NO_TARGET,d0
        beq.s   .no_target
        move.b  d3,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l
        move.b  (OBJECT_LOGICAL_ID,a3),(VOCAB_CAMPAIGN_LAST_INTERACTION_OBJECT).l
        move.b  d0,(VOCAB_ACTOR_LAST_ACTION,a4)
        bra.s   .publish
.next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d7,.terminal
.no_target:
        moveq   #VOCAB_RESULT_NO_TARGET,d0
        bra.s   .publish
.invalid:
        moveq   #VOCAB_RESULT_INVALID,d0
.publish:
        move.w  d0,(VOCAB_INTERACTION_RESULT).l
        move.w  d0,(VOCAB_RESULT).l
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; VocabularyResolveInteraction
; In: a0=actor whose B edge is being resolved.
; Priority is socket > carried drop > pickup > terminal/node > timed switch >
; reflector > conveyor. Physical slot order breaks ties within one category.
; Out: d0.w=result. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyResolveInteraction:
        movem.l d3-d7/a2-a5,-(a7)
        movea.l a0,a2
        tst.b   (ACTOR_ACTIVE,a2)
        beq.w   .invalid
        bsr.w   VocabularyGetActorExtension
        cmpi.w  #$FFFF,d0
        beq.w   .invalid
        move.w  d0,d3                   ; physical actor index
        movea.l a1,a3

        moveq   #0,d4
        move.b  (VOCAB_ACTOR_CARRY_SLOT,a3),d4
        cmpi.w  #VOCAB_NO_SLOT,d4
        beq.w   .pickup

; A carried cell first seeks the lowest in-range unoccupied socket.
        lea     (DEVICE_POOL).l,a5
        moveq   #DEVICE_CAPACITY-1,d7
.socket_scan:
        tst.b   (OBJECT_ACTIVE,a5)
        beq.s   .socket_next
        move.w  (OBJECT_STATE,a5),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .socket_next
        move.w  (OBJECT_PARAM,a5),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SOCKET,d0
        bne.s   .socket_next
        move.w  (OBJECT_STATE,a5),d0
        andi.w  #VOCAB_DEVICE_STATE_OCCUPIED,d0
        bne.s   .socket_next
        movea.l a2,a0
        movea.l a5,a1
        move.l  #VOCAB_INTERACTION_RANGE_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        bne.s   .socket_cell
.socket_next:
        adda.w  #OBJECT_BYTES,a5
        dbf     d7,.socket_scan
        bra.s   .drop_cell

.socket_cell:
        move.w  d4,d0
        mulu.w  #OBJECT_BYTES,d0
        lea     (CARRYABLE_POOL).l,a4
        adda.l  d0,a4
        cmpi.w  #CARRYABLE_CAPACITY,d4
        bhs.w   .invalid
        tst.b   (OBJECT_ACTIVE,a4)
        beq.w   .invalid
        move.w  #VOCAB_CARRY_STATE_SOCKETED,(OBJECT_STATE,a4)
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a5),d0
        addq.w  #1,d0
        ori.w   #VOCAB_CARRYABLE_POWER_CELL,d0
        move.w  d0,(OBJECT_PARAM,a4)
        move.l  (OBJECT_X,a5),d0
        move.l  d0,(OBJECT_X,a4)
        move.l  (OBJECT_Y,a5),d0
        move.l  d0,(OBJECT_Y,a4)
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_OCCUPIED,(OBJECT_STATE,a5)
        andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)
        move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)
        move.b  #VOCAB_RESULT_SOCKETED,(VOCAB_ACTOR_LAST_ACTION,a3)
        moveq   #VOCAB_RESULT_SOCKETED,d0
        bra.w   .done

.drop_cell:
        cmpi.w  #CARRYABLE_CAPACITY,d4
        bhs.w   .invalid
        move.w  d4,d0
        mulu.w  #OBJECT_BYTES,d0
        lea     (CARRYABLE_POOL).l,a4
        adda.l  d0,a4
        tst.b   (OBJECT_ACTIVE,a4)
        beq.w   .invalid
        clr.w   (OBJECT_STATE,a4)
        move.w  #VOCAB_CARRYABLE_POWER_CELL,(OBJECT_PARAM,a4)
        move.l  (ACTOR_X,a2),d0
        move.l  d0,(OBJECT_X,a4)
        move.l  (ACTOR_Y,a2),d0
        move.l  d0,(OBJECT_Y,a4)
        andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)
        move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)
        move.b  #VOCAB_RESULT_DROPPED,(VOCAB_ACTOR_LAST_ACTION,a3)
        moveq   #VOCAB_RESULT_DROPPED,d0
        bra.w   .done

; An empty-handed actor picks the lowest physical in-range free power cell.
.pickup:
        lea     (CARRYABLE_POOL).l,a4
        moveq   #CARRYABLE_CAPACITY-1,d7
        moveq   #0,d4
.pickup_scan:
        tst.b   (OBJECT_ACTIVE,a4)
        beq.s   .pickup_next
        move.w  (OBJECT_PARAM,a4),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_CARRYABLE_POWER_CELL,d0
        bne.s   .pickup_next
        move.w  (OBJECT_STATE,a4),d0
        andi.w  #VOCAB_CARRY_STATE_CARRIED|VOCAB_CARRY_STATE_SOCKETED,d0
        bne.s   .pickup_next
        movea.l a2,a0
        movea.l a4,a1
        move.l  #VOCAB_INTERACTION_RANGE_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        bne.s   .pickup_cell
.pickup_next:
        adda.w  #OBJECT_BYTES,a4
        addq.w  #1,d4
        dbf     d7,.pickup_scan
        bra.s   .device_priority

.pickup_cell:
        move.w  #VOCAB_CARRY_STATE_CARRIED,(OBJECT_STATE,a4)
        move.w  d3,d0
        addq.w  #1,d0
        ori.w   #VOCAB_CARRYABLE_POWER_CELL,d0
        move.w  d0,(OBJECT_PARAM,a4)
        ori.w   #VOCAB_ACTOR_FLAG_CARRYING,(VOCAB_ACTOR_FLAGS,a3)
        move.b  d4,(VOCAB_ACTOR_CARRY_SLOT,a3)
        move.b  #VOCAB_RESULT_PICKED_UP,(VOCAB_ACTOR_LAST_ACTION,a3)
        moveq   #VOCAB_RESULT_PICKED_UP,d0
        bra.w   .done

; Choose the lowest numeric category, then the lowest physical device slot.
.device_priority:
        moveq   #$7F,d4                ; best priority
        suba.l  a4,a4                  ; no candidate
        lea     (DEVICE_POOL).l,a5
        moveq   #DEVICE_CAPACITY-1,d7
.device_scan:
        tst.b   (OBJECT_ACTIVE,a5)
        beq.s   .device_next
        move.w  (OBJECT_STATE,a5),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        beq.s   .device_actionable
; A damaged repair terminal is a passive repair target, not an acting device.
        move.w  (OBJECT_PARAM,a5),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_REPAIR_TERMINAL,d0
        bne.s   .device_next
.device_actionable:
        movea.l a2,a0
        movea.l a5,a1
        move.l  #VOCAB_INTERACTION_RANGE_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        beq.s   .device_next
        move.w  (OBJECT_PARAM,a5),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        moveq   #$7F,d1
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d0
        beq.s   .priority_terminal
        cmpi.w  #VOCAB_DEVICE_REPAIR_TERMINAL,d0
        beq.s   .priority_terminal
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0
        beq.s   .priority_terminal
        cmpi.w  #VOCAB_DEVICE_TIMED_SWITCH,d0
        beq.s   .priority_switch
        cmpi.w  #VOCAB_DEVICE_REFLECTOR,d0
        beq.s   .priority_reflector
        cmpi.w  #VOCAB_DEVICE_CONVEYOR,d0
        bne.s   .device_next
        moveq   #4,d1
        bra.s   .priority_ready
.priority_terminal:
        moveq   #1,d1
        bra.s   .priority_ready
.priority_switch:
        moveq   #2,d1
        bra.s   .priority_ready
.priority_reflector:
        moveq   #3,d1
.priority_ready:
        cmp.w   d4,d1
        bhs.s   .device_next
        move.w  d1,d4
        movea.l a5,a4
.device_next:
        adda.w  #OBJECT_BYTES,a5
        dbf     d7,.device_scan
        move.l  a4,d0
        beq.w   .no_target

        move.w  (OBJECT_PARAM,a4),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d0
        beq.s   .hack
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0
        beq.s   .instant_hack
        cmpi.w  #VOCAB_DEVICE_REPAIR_TERMINAL,d0
        beq.s   .repair
        cmpi.w  #VOCAB_DEVICE_TIMED_SWITCH,d0
        beq.s   .timed
        cmpi.w  #VOCAB_DEVICE_REFLECTOR,d0
        beq.s   .rotate
        cmpi.w  #VOCAB_DEVICE_CONVEYOR,d0
        beq.s   .rotate
        bra.w   .invalid
.hack:
        movea.l a2,a0
        movea.l a4,a1
        move.w  d3,d0
        bsr.w   VocabularyAdvanceHackInteraction
        cmpi.w  #VOCAB_RESULT_NO_TARGET,d0
        beq.w   .no_target
        bra.s   .action_ready
.instant_hack:
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_HACKED,(OBJECT_STATE,a4)
        moveq   #VOCAB_RESULT_HACKED,d0
        bra.s   .action_ready
.repair:
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,(OBJECT_STATE,a4)
        andi.w  #$FFF7,(OBJECT_STATE,a4)
        movea.l a4,a0
        bsr.w   VocabularyRepairLinkedTarget
        moveq   #VOCAB_RESULT_REPAIRED,d0
        bra.s   .action_ready
.timed:
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,(OBJECT_STATE,a4)
; ABI-v3 encounters own switch timing in semantic metadata. Legacy standalone
; fixtures retain the fixed fallback, and malformed active bindings fail
; closed to that bounded value rather than loading an untrusted duration.
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        bne.s   .timed_legacy_duration
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a4),d0
        bsr.w   CampaignGetSemanticDuration
        tst.w   d1
        bne.s   .timed_legacy_duration
        tst.w   d0
        bne.s   .timed_store_duration
.timed_legacy_duration:
        move.w  #VOCAB_TIMED_SWITCH_TICKS,d0
.timed_store_duration:
        move.w  d0,(OBJECT_TIMER,a4)
        moveq   #VOCAB_RESULT_ACTIVATED,d0
        bra.s   .action_ready
.rotate:
        move.w  (OBJECT_STATE,a4),d1
        move.w  d1,d2
        andi.w  #VOCAB_DEVICE_STATE_DIRECTION_MASK,d2
        addi.w  #$0040,d2
        andi.w  #VOCAB_DEVICE_STATE_DIRECTION_MASK,d2
        andi.w  #$FF3F,d1
        or.w    d2,d1
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,d1
        move.w  d1,(OBJECT_STATE,a4)
        moveq   #VOCAB_RESULT_ACTIVATED,d0
.action_ready:
        move.b  d3,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l
        move.b  (OBJECT_LOGICAL_ID,a4),(VOCAB_CAMPAIGN_LAST_INTERACTION_OBJECT).l
        move.b  d0,(VOCAB_ACTOR_LAST_ACTION,a3)
        bra.s   .done
.no_target:
        moveq   #VOCAB_RESULT_NO_TARGET,d0
        bra.s   .done
.invalid:
        moveq   #VOCAB_RESULT_INVALID,d0
.done:
        move.w  d0,(VOCAB_INTERACTION_RESULT).l
        move.w  d0,(VOCAB_RESULT).l
        movem.l (a7)+,d3-d7/a2-a5
        rts

; VocabularyRepairLinkedTarget
; In: a0=repair terminal. Its OBJECT_PARAM low byte is the target logical ID.
; Devices are searched before enemies; globally unique logical IDs make at most
; one match authoritative. A repaired target is enabled and a zero health byte
; is restored to one for breakable nodes/enemies.
; Out: d0.w=1 when a live target record matched, otherwise zero.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyRepairLinkedTarget:
        movem.l d3-d5/a2-a3,-(a7)
        movea.l a0,a2
        moveq   #0,d3
        move.b  (OBJECT_PARAM+1,a2),d3

        lea     (DEVICE_POOL).l,a3
        moveq   #DEVICE_CAPACITY-1,d5
.device:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .device_next
        moveq   #0,d4
        move.b  (OBJECT_LOGICAL_ID,a3),d4
        cmp.w   d3,d4
        bne.s   .device_next
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,(OBJECT_STATE,a3)
        andi.w  #$FFF7,(OBJECT_STATE,a3)
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0
        bne.s   .matched
        move.w  (OBJECT_STATE,a3),d0
        andi.w  #$FF00,d0
        bne.s   .matched
        ori.w   #$0100,(OBJECT_STATE,a3)
        bra.s   .matched
.device_next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d5,.device

        lea     (ENEMY_POOL).l,a3
        moveq   #ENEMY_CAPACITY-1,d5
.enemy:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .enemy_next
        moveq   #0,d4
        move.b  (OBJECT_LOGICAL_ID,a3),d4
        cmp.w   d3,d4
        bne.s   .enemy_next
        andi.w  #$FFFD,(OBJECT_STATE,a3)
        move.w  (OBJECT_STATE,a3),d0
        andi.w  #VOCAB_ENEMY_HEALTH_MASK,d0
        bne.s   .matched
        ori.w   #$0100,(OBJECT_STATE,a3)
.matched:
        moveq   #1,d0
        bra.s   .done
.enemy_next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d5,.enemy
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d5/a2-a3
        rts

; VocabularyFollowCarryables
; Snaps at most seven actor-owned cells to their carrier before device tests.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyFollowCarryables:
        movem.l d3-d5/a2-a4,-(a7)
        lea     (ENGINE_ACTOR_BASE).l,a2
        lea     (VOCAB_ACTOR_BASE).l,a3
        moveq   #ACTOR_CAPACITY-1,d5
        moveq   #0,d4
.actor:
        moveq   #0,d3
        move.b  (VOCAB_ACTOR_CARRY_SLOT,a3),d3
        cmpi.w  #VOCAB_NO_SLOT,d3
        beq.w   .next
        cmpi.w  #CARRYABLE_CAPACITY,d3
        bhs.s   .invalid_slot
        move.w  d3,d0
        mulu.w  #OBJECT_BYTES,d0
        lea     (CARRYABLE_POOL).l,a4
        adda.l  d0,a4
        tst.b   (OBJECT_ACTIVE,a4)
        beq.s   .invalid_slot
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .drop_inactive
        move.l  (ACTOR_X,a2),d0
        move.l  d0,(OBJECT_X,a4)
        move.l  (ACTOR_Y,a2),d0
        move.l  d0,(OBJECT_Y,a4)
        move.w  #VOCAB_CARRY_STATE_CARRIED,(OBJECT_STATE,a4)
        move.w  d4,d0
        addq.w  #1,d0
        ori.w   #VOCAB_CARRYABLE_POWER_CELL,d0
        move.w  d0,(OBJECT_PARAM,a4)
        bra.s   .next
.drop_inactive:
        clr.w   (OBJECT_STATE,a4)
        move.w  #VOCAB_CARRYABLE_POWER_CELL,(OBJECT_PARAM,a4)
        move.l  (ACTOR_X,a2),d0
        move.l  d0,(OBJECT_X,a4)
        move.l  (ACTOR_Y,a2),d0
        move.l  d0,(OBJECT_Y,a4)
        move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)
        andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)
        bra.s   .next
.invalid_slot:
        move.b  #VOCAB_NO_SLOT,(VOCAB_ACTOR_CARRY_SLOT,a3)
        andi.w  #$FFF7,(VOCAB_ACTOR_FLAGS,a3)
        ori.w   #VOCAB_OVERFLOW_INTERACTION,(VOCAB_OVERFLOW_FLAGS).l
.next:
        adda.w  #ACTOR_BYTES,a2
        adda.w  #VOCAB_ACTOR_BYTES,a3
        addq.w  #1,d4
        dbf     d5,.actor
        movem.l (a7)+,d3-d5/a2-a4
        rts

; VocabularyUpdateDevices
; Updates pressure/timed switches, linked doors, carryables, and conveyors in
; fixed physical order. OBJECT_TIMER is vocabulary-owned for permanent M5
; devices; future wiring must not pass those devices to generic expiry.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyUpdateDevices:
        movem.l d3-d7/a2-a5,-(a7)
        bsr.w   VocabularyFollowCarryables
        lea     (DEVICE_POOL).l,a2
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d7
        cmpi.w  #DEVICE_CAPACITY,d7
        bls.s   .device_bound_ready
        moveq   #DEVICE_CAPACITY,d7
.device_bound_ready:
        tst.w   d7
        beq.w   .done
        subq.w  #1,d7
.device:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .next
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .next
        move.w  (OBJECT_PARAM,a2),d6
        andi.w  #VOCAB_PARAM_KIND_MASK,d6
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d6
        beq.w   .hack_terminal
        cmpi.w  #VOCAB_DEVICE_PRESSURE_SWITCH,d6
        beq.s   .pressure
        cmpi.w  #VOCAB_DEVICE_TIMED_SWITCH,d6
        beq.w   .timed
        cmpi.w  #VOCAB_DEVICE_DOOR,d6
        beq.w   .door
        cmpi.w  #VOCAB_DEVICE_CONVEYOR,d6
        beq.w   .conveyor
        bra.w   .next

.hack_terminal:
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_HACKED,d0
        bne.w   .next
        tst.w   (OBJECT_TIMER,a2)
        beq.w   .next
        moveq   #0,d0
        move.w  (ENGINE_TICK).l,d0
        addq.w  #1,d0
        cmp.w   (VOCAB_DEVICE_HOLD_TOUCHED,a2),d0
        beq.w   .next
; No owning actor touched this terminal during the current authoritative step:
; release, range loss, death, or a closed dependency gate resets the hold.
        clr.w   (OBJECT_TIMER,a2)
        clr.l   (OBJECT_VX,a2)
        clr.l   (OBJECT_VY,a2)
        andi.w  #$FFFE,(OBJECT_STATE,a2)
        bra.w   .next

.pressure:
        moveq   #0,d5                   ; physical actor occupancy mask
        lea     (ENGINE_ACTOR_BASE).l,a3
        moveq   #ACTOR_CAPACITY-1,d4
        moveq   #0,d3
.pressure_actor:
        tst.b   (ACTOR_ACTIVE,a3)
        beq.s   .pressure_next
        movea.l a3,a0
        movea.l a2,a1
        move.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        beq.s   .pressure_next
        bset    d3,d5
.pressure_next:
        adda.w  #ACTOR_BYTES,a3
        addq.w  #1,d3
        dbf     d4,.pressure_actor
        move.w  d5,(OBJECT_TIMER,a2)
        tst.w   d5
        beq.s   .pressure_clear
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,(OBJECT_STATE,a2)
        bra.w   .next
.pressure_clear:
        andi.w  #$FFFE,(OBJECT_STATE,a2)
        bra.w   .next

.timed:
        tst.w   (OBJECT_TIMER,a2)
        beq.s   .timed_clear
        subq.w  #1,(OBJECT_TIMER,a2)
        ori.w   #VOCAB_DEVICE_STATE_ACTIVE,(OBJECT_STATE,a2)
        bra.w   .next
.timed_clear:
        andi.w  #$FFFE,(OBJECT_STATE,a2)
        bra.w   .next

.door:
; ABI-v3 dependency consumers retain their output state until the controller
; evaluates it later in this same authoritative frame. Vocabulary owns the
; one timer decrement; the controller owns every result-bit transition.
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.s   .door_legacy
        tst.w   (OBJECT_TIMER,a2)
        beq.w   .next
        subq.w  #1,(OBJECT_TIMER,a2)
        bra.w   .next

; This bounded low-byte match remains a format-v2 compatibility fallback.
.door_legacy:
        move.w  (OBJECT_PARAM,a2),d5
        andi.w  #VOCAB_PARAM_LINK_MASK,d5
        tst.w   d5
        beq.w   .next                   ; zero has no legacy group
        moveq   #0,d4
        lea     (DEVICE_POOL).l,a3
        moveq   #DEVICE_CAPACITY-1,d3
.door_source:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .door_next_source
        move.w  (OBJECT_STATE,a3),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .door_next_source
        move.w  (OBJECT_PARAM,a3),d0
        move.w  d0,d1
        andi.w  #VOCAB_PARAM_LINK_MASK,d0
        cmp.w   d5,d0
        bne.s   .door_next_source
        andi.w  #VOCAB_PARAM_KIND_MASK,d1
        cmpi.w  #VOCAB_DEVICE_PRESSURE_SWITCH,d1
        beq.s   .door_source_state
        cmpi.w  #VOCAB_DEVICE_TIMED_SWITCH,d1
        beq.s   .door_source_state
        cmpi.w  #VOCAB_DEVICE_SOCKET,d1
        bne.s   .door_next_source
.door_source_state:
        move.w  (OBJECT_STATE,a3),d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.s   .door_next_source
        moveq   #1,d4
        bra.s   .door_publish
.door_next_source:
        adda.w  #OBJECT_BYTES,a3
        dbf     d3,.door_source
.door_publish:
        tst.w   d4
        beq.s   .door_close
        ori.w   #VOCAB_DEVICE_STATE_OPEN,(OBJECT_STATE,a2)
        bra.w   .next
.door_close:
        andi.w  #$FFFD,(OBJECT_STATE,a2)
        bra.w   .next

.conveyor:
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.w   .next
        lea     (ENGINE_ACTOR_BASE).l,a3
        moveq   #ACTOR_CAPACITY-1,d4
.conveyor_actor:
        tst.b   (ACTOR_ACTIVE,a3)
        beq.s   .conveyor_next
        movea.l a3,a0
        movea.l a2,a1
        move.l  #VOCAB_INTERACTION_RANGE_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        beq.s   .conveyor_next
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_DIRECTION_MASK,d0
        lsr.w   #VOCAB_DEVICE_STATE_DIRECTION_SHIFT,d0
        cmpi.w  #VOCAB_BEAM_DIRECTION_RIGHT,d0
        beq.s   .conveyor_right
        cmpi.w  #VOCAB_BEAM_DIRECTION_DOWN,d0
        beq.s   .conveyor_down
        cmpi.w  #VOCAB_BEAM_DIRECTION_LEFT,d0
        beq.s   .conveyor_left
        moveq   #0,d0
        move.l  #-VOCAB_CONVEYOR_SPEED,d1
        bra.s   .conveyor_move
.conveyor_right:
        move.l  #VOCAB_CONVEYOR_SPEED,d0
        moveq   #0,d1
        bra.s   .conveyor_move
.conveyor_down:
        moveq   #0,d0
        move.l  #VOCAB_CONVEYOR_SPEED,d1
        bra.s   .conveyor_move
.conveyor_left:
        move.l  #-VOCAB_CONVEYOR_SPEED,d0
        moveq   #0,d1
.conveyor_move:
        movea.l a3,a0
        bsr.w   GameplayCampaignMoveActorByDelta
.conveyor_next:
        adda.w  #ACTOR_BYTES,a3
        dbf     d4,.conveyor_actor
.next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.device
.done:
        movem.l (a7)+,d3-d7/a2-a5
        rts

; VocabularyBuildBeamCache
; Emits at most two axis-aligned segments per emitter. Emitters and reflectors
; are bounded independently. The nearest forward on-ray reflector wins, with
; physical slot order breaking equal-distance ties.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyBuildBeamCache:
        movem.l d3-d7/a2-a5,-(a7)
        lea     (VOCAB_BEAM_BASE).l,a0
        moveq   #0,d0
        move.w  #((VOCAB_BEAM_END-VOCAB_BEAM_BASE)/4)-1,d1
.clear_beams:
        move.l  d0,(a0)+
        dbf     d1,.clear_beams
        clr.b   (VOCAB_BEAM_COUNT).l

        lea     (DEVICE_POOL).l,a2
        moveq   #0,d6                   ; accepted emitters
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d7
        cmpi.w  #DEVICE_CAPACITY,d7
        bls.s   .emitter_bound_ready
        moveq   #DEVICE_CAPACITY,d7
.emitter_bound_ready:
        tst.w   d7
        beq.w   .done
        subq.w  #1,d7
.emitter:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .emitter_next
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .emitter_next
        move.w  (OBJECT_PARAM,a2),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_BEAM_EMITTER,d0
        bne.w   .emitter_next
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.w   .emitter_next
        cmpi.w  #VOCAB_BEAM_EMITTER_LIMIT,d6
        bhs.w   .beam_overflow

        moveq   #0,d5
        move.b  (VOCAB_BEAM_COUNT).l,d5
        cmpi.w  #VOCAB_BEAM_LIMIT,d5
        bhs.w   .beam_overflow
        move.w  d5,d0
        lsl.w   #3,d0
        lea     (VOCAB_BEAM_BASE).l,a4
        adda.w  d0,a4
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        move.w  d0,(VOCAB_BEAM_X,a4)
        move.l  (OBJECT_Y,a2),d0
        asr.l   #8,d0
        move.w  d0,(VOCAB_BEAM_Y,a4)
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_DIRECTION_MASK,d0
        lsr.w   #VOCAB_DEVICE_STATE_DIRECTION_SHIFT,d0
        move.b  d0,(VOCAB_BEAM_DIRECTION,a4)
        move.b  #VOCAB_BEAM_STEP_PIXELS*VOCAB_BEAM_MAX_STEPS,(VOCAB_BEAM_LENGTH,a4)
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        move.b  d0,(VOCAB_BEAM_SOURCE,a4)
        clr.b   (VOCAB_BEAM_FLAGS,a4)
        movea.l a4,a0
        bsr.w   GameplayCampaignClipBeam
        addq.b  #1,(VOCAB_BEAM_COUNT).l
        addq.w  #1,d6

; Find the nearest enabled reflector on the finite forward ray.
        suba.l  a5,a5                   ; no reflector selected
        move.w  #VOCAB_BEAM_STEP_PIXELS*VOCAB_BEAM_MAX_STEPS+1,d3
        lea     (DEVICE_POOL).l,a3
        moveq   #DEVICE_CAPACITY-1,d4
.reflector:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.w   .reflector_next
        move.w  (OBJECT_STATE,a3),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .reflector_next
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_REFLECTOR,d0
        bne.w   .reflector_next

        move.l  (OBJECT_X,a3),d1
        asr.l   #8,d1
        move.l  (OBJECT_Y,a3),d2
        asr.l   #8,d2
        moveq   #0,d0
        move.b  (VOCAB_BEAM_DIRECTION,a4),d0
        cmpi.w  #VOCAB_BEAM_DIRECTION_RIGHT,d0
        beq.s   .reflector_right
        cmpi.w  #VOCAB_BEAM_DIRECTION_DOWN,d0
        beq.s   .reflector_down
        cmpi.w  #VOCAB_BEAM_DIRECTION_LEFT,d0
        beq.s   .reflector_left
; Up: exact X, nonnegative origin-Y distance.
        cmp.w   (VOCAB_BEAM_X,a4),d1
        bne.w   .reflector_next
        move.w  (VOCAB_BEAM_Y,a4),d0
        sub.w   d2,d0
        bmi.w   .reflector_next
        move.w  d0,d1
        bra.s   .reflector_distance
.reflector_right:
        cmp.w   (VOCAB_BEAM_Y,a4),d2
        bne.w   .reflector_next
        sub.w   (VOCAB_BEAM_X,a4),d1
        bmi.w   .reflector_next
        bra.s   .reflector_distance
.reflector_down:
        cmp.w   (VOCAB_BEAM_X,a4),d1
        bne.w   .reflector_next
        sub.w   (VOCAB_BEAM_Y,a4),d2
        bmi.w   .reflector_next
        move.w  d2,d1
        bra.s   .reflector_distance
.reflector_left:
        cmp.w   (VOCAB_BEAM_Y,a4),d2
        bne.w   .reflector_next
        move.w  (VOCAB_BEAM_X,a4),d0
        sub.w   d1,d0
        bmi.w   .reflector_next
        move.w  d0,d1
.reflector_distance:
        cmpi.w  #VOCAB_BEAM_STEP_PIXELS*VOCAB_BEAM_MAX_STEPS,d1
        bhi.s   .reflector_next
        moveq   #0,d0
        move.b  (VOCAB_BEAM_LENGTH,a4),d0
        cmp.w   d0,d1
        bhi.s   .reflector_next
        cmp.w   d3,d1
        bhs.s   .reflector_next         ; equal keeps lower physical slot
        move.w  d1,d3
        movea.l a3,a5
.reflector_next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d4,.reflector

        move.l  a5,d0
        beq.w   .emitter_next

; Truncate the incoming ray and append one redirected segment.
        moveq   #0,d5
        move.b  (VOCAB_BEAM_COUNT).l,d5
        cmpi.w  #VOCAB_BEAM_LIMIT,d5
        bhs.w   .beam_overflow
        movea.l a5,a3
        move.w  d5,d0
        lsl.w   #3,d0
        lea     (VOCAB_BEAM_BASE).l,a5
        adda.w  d0,a5
        move.l  (OBJECT_X,a3),d0
        asr.l   #8,d0
        move.w  d0,(VOCAB_BEAM_X,a5)
        move.l  (OBJECT_Y,a3),d0
        asr.l   #8,d0
        move.w  d0,(VOCAB_BEAM_Y,a5)
        moveq   #0,d0
        move.b  (VOCAB_BEAM_DIRECTION,a4),d0
        move.w  (OBJECT_STATE,a3),d1
        andi.w  #VOCAB_DEVICE_STATE_DIRECTION_MASK,d1
        lsr.w   #VOCAB_DEVICE_STATE_DIRECTION_SHIFT,d1
        add.w   d1,d0
        addq.w  #1,d0
        andi.w  #$0003,d0
        move.b  d0,(VOCAB_BEAM_DIRECTION,a5)
        move.b  #VOCAB_BEAM_STEP_PIXELS*VOCAB_BEAM_MAX_STEPS,(VOCAB_BEAM_LENGTH,a5)
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        move.b  d0,(VOCAB_BEAM_SOURCE,a5)
        move.b  #VOCAB_BEAM_FLAG_REFLECTED,(VOCAB_BEAM_FLAGS,a5)
        movea.l a5,a0
        bsr.w   GameplayCampaignClipBeam
        move.b  d3,(VOCAB_BEAM_LENGTH,a4)
        ori.b   #VOCAB_BEAM_FLAG_REFLECTED,(VOCAB_BEAM_FLAGS,a4)
        addq.b  #1,(VOCAB_BEAM_COUNT).l
        bra.w   .emitter_next
.beam_overflow:
        ori.w   #VOCAB_OVERFLOW_BEAM,(VOCAB_OVERFLOW_FLAGS).l
.emitter_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.emitter
.done:
        movem.l (a7)+,d3-d7/a2-a5
        rts
