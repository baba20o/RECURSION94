; Deterministic campaign ABI v3 controller.
; Requires game/engine.asm, gameplay/gameplay.asm, and gameplay/vocabulary.asm.
; Generated campaign tables are included later in the translation unit and are
; immutable forward references from this code.

CAMPAIGN_CONTROLLER_FORMAT_VERSION equ $0003
CAMPAIGN_CONTROLLER_NOT_FOUND      equ $FFFF
CAMPAIGN_DEPENDENCY_FLAG_RISING    equ $0001

; CampaignLoadEncounter
; In: d0.w=generated directory index.
; Out: d0.w=ENGINE_RESULT_OK, ENGINE_RESULT_NOT_FOUND,
;      ENGINE_RESULT_BAD_DESCRIPTOR, or ENGINE_RESULT_POOL_OVERFLOW.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignLoadEncounter:
        movem.l d3-d7/a2-a6,-(a7)
        move.w  d0,d7

; A new load invalidates every old campaign binding before inspecting ROM.
; The projected identity and all derived pointers therefore fail closed.
        lea     (VOCAB_CAMPAIGN_BASE).l,a0
        moveq   #0,d0
        moveq   #((VOCAB_CAMPAIGN_DERIVED_END-VOCAB_CAMPAIGN_BASE)/4)-1,d1
.clear_binding:
        move.l  d0,(a0)+
        dbf     d1,.clear_binding
        clr.w   (VOCAB_STAGE_ID).l

        cmpi.l  #VOCAB_MAGIC_VALUE,(VOCAB_MAGIC).l
        bne.w   .bad_contract
        move.w  (VOCAB_FLAGS).l,d0
        andi.w  #VOCAB_FLAG_INITIALIZED,d0
        beq.w   .bad_contract
        move.w  #CAMPAIGN_RUNTIME_FORMAT_VERSION,d0
        cmpi.w  #CAMPAIGN_CONTROLLER_FORMAT_VERSION,d0
        bne.w   .bad_contract
        cmpi.w  #CAMPAIGN_RUNTIME_ENCOUNTER_COUNT,d7
        bhs.w   .not_found

        move.w  d7,d0
        lsl.w   #2,d0
        lea     (CampaignRuntimeDescriptorTable).l,a0
        movea.l (a0,d0.w),a3
        lea     (CampaignRuntimeProfileTable).l,a0
        movea.l (a0,d0.w),a4
        move.l  a3,d0
        beq.w   .bad_contract
        btst    #0,d0
        bne.w   .bad_contract
        move.l  a4,d0
        beq.w   .bad_contract
        btst    #0,d0
        bne.w   .bad_contract

        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_PROFILE_INDEX,a4),d0
        cmp.w   d7,d0
        bne.w   .bad_contract
        moveq   #0,d4
        move.b  (CAMPAIGN_RUNTIME_PROFILE_ECHO_CAP,a4),d4
        beq.w   .bad_contract
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d4
        bhi.w   .bad_contract

        moveq   #0,d5
        move.w  (CAMPAIGN_RUNTIME_PROFILE_SEMANTIC_COUNT,a4),d5
        cmpi.w  #TOTAL_POOL_CAPACITY,d5
        bhi.w   .bad_contract
        moveq   #0,d6
        move.w  (CAMPAIGN_RUNTIME_PROFILE_GROUP_COUNT,a4),d6
        cmpi.w  #$00FF,d6
        bhi.w   .bad_contract
        moveq   #0,d3
        move.w  (CAMPAIGN_RUNTIME_PROFILE_EDGE_COUNT,a4),d3
        cmpi.w  #$00FF,d3
        bhi.w   .bad_contract

        movea.l (CAMPAIGN_RUNTIME_PROFILE_SEMANTIC_PTR,a4),a5
        movea.l (CAMPAIGN_RUNTIME_PROFILE_GROUP_PTR,a4),a6
        movea.l (CAMPAIGN_RUNTIME_PROFILE_EDGE_PTR,a4),a2
        move.l  a5,d0
        beq.w   .bad_contract
        btst    #0,d0
        bne.w   .bad_contract
        move.l  a6,d0
        beq.w   .bad_contract
        btst    #0,d0
        bne.w   .bad_contract
        move.l  a2,d0
        beq.w   .bad_contract
        btst    #0,d0
        bne.w   .bad_contract

        movea.l (CAMPAIGN_RUNTIME_PROFILE_TOKEN_PTR,a4),a0
        move.l  a0,d0
        beq.w   .bad_contract
        btst    #0,d0
        bne.w   .bad_contract
        lea     (CampaignRuntimeSemanticTokenTable).l,a1
        cmpa.l  a1,a0
        bne.w   .bad_contract
        cmpi.w  #CAMPAIGN_RUNTIME_SEMANTIC_TOKEN_COUNT,(CAMPAIGN_RUNTIME_PROFILE_TOKEN_COUNT,a4)
        bne.w   .bad_contract
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_FIELD_COUNT,(CAMPAIGN_RUNTIME_PROFILE_REFERENCE_FIELD_COUNT,a4)
        bne.w   .bad_contract

; Semantic IDs are a dense, globally unique direct index for this encounter.
        tst.w   d5
        beq.s   .semantics_done
        movea.l a5,a0
        moveq   #0,d0
        move.w  d5,d2
        subq.w  #1,d2
.semantic:
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_SEMANTIC_OBJECT,a0),d1
        cmp.w   d0,d1
        bne.w   .bad_contract
        addq.w  #1,d0
        adda.w  #CAMPAIGN_RUNTIME_SEMANTIC_BYTES,a0
        dbf     d2,.semantic
.semantics_done:

; Groups must partition the edge table into ordered, non-overlapping ranges.
; This makes total dependency work bounded by group_count+edge_count.
        tst.w   d6
        bne.s   .groups_present
        tst.w   d3
        bne.w   .bad_contract
        bra.w   .edges_done
.groups_present:
        movea.l a6,a0
        moveq   #0,d0                   ; next expected edge index
        move.w  d6,d2
        subq.w  #1,d2
.group:
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_GROUP_CONSUMER,a0),d1
        cmp.w   d5,d1
        bhs.w   .bad_contract
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_GROUP_POLICY,a0),d1
        cmpi.w  #CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ALL,d1
        beq.s   .group_policy_valid
        cmpi.w  #CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ANY,d1
        bne.w   .bad_contract
.group_policy_valid:
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_GROUP_FIRST_EDGE,a0),d1
        cmp.w   d0,d1
        bne.w   .bad_contract
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_GROUP_EDGE_COUNT,a0),d1
        beq.w   .bad_contract
        add.w   d1,d0
        cmp.w   d3,d0
        bhi.w   .bad_contract
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_GROUP_RESULT_FIELD,a0),d1
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_STATE,d1
        blo.w   .bad_contract
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_HEALTH,d1
        bhi.w   .bad_contract
        tst.w   (CAMPAIGN_RUNTIME_GROUP_RESULT_MASK,a0)
        beq.w   .bad_contract
        cmpi.w  #CAMPAIGN_DEPENDENCY_FLAG_RISING,(CAMPAIGN_RUNTIME_GROUP_FLAGS,a0)
        bne.w   .bad_contract
        adda.w  #CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES,a0
        dbf     d2,.group
        cmp.w   d3,d0
        bne.w   .bad_contract

        movea.l a2,a0
        move.w  d3,d2
        subq.w  #1,d2
.edge:
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_EDGE_PROVIDER,a0),d1
        cmp.w   d5,d1
        bhs.w   .bad_contract
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_EDGE_FIELD,a0),d1
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_STATE,d1
        blo.w   .bad_contract
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_HEALTH,d1
        bhi.w   .bad_contract
        tst.w   (CAMPAIGN_RUNTIME_EDGE_MASK,a0)
        beq.w   .bad_contract
        tst.w   (CAMPAIGN_RUNTIME_EDGE_FLAGS,a0)
        bne.w   .bad_contract
        adda.w  #CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES,a0
        dbf     d2,.edge
.edges_done:

        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_OBJECT,a4),d0
        cmpi.w  #CAMPAIGN_RUNTIME_NO_COMPLETION_OBJECT,d0
        beq.s   .completion_valid
        cmp.w   d5,d0
        bhs.w   .bad_contract
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_FIELD,a4),d0
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_STATE,d0
        blo.w   .bad_contract
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_HEALTH,d0
        bhi.w   .bad_contract
        tst.w   (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_MASK,a4)
        beq.w   .bad_contract
.completion_valid:
        move.w  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_FLAGS,a4),d0
        cmpi.w  #$00FF,d0
        bhi.w   .bad_contract

; Engine validation and reconstruction remain the sole canonical-state gate.
        movea.l a3,a0
        move.w  d7,d0
        bsr.w   EngineLoadDescriptor
        tst.w   d0
        bne.w   .engine_failed
        cmp.w   (ENGINE_OBJECT_INIT_COUNT).l,d5
        bne.w   .bad_contract

; Commit projected identity only after every generated and engine check passes.
        move.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,(VOCAB_CAMPAIGN_FLAGS).l
        move.b  d7,(VOCAB_CAMPAIGN_DIRECTORY_INDEX).l
        move.b  d4,(VOCAB_CAMPAIGN_ECHO_CAP).l
        move.b  d5,(VOCAB_CAMPAIGN_SEMANTIC_COUNT).l
        move.b  d6,(VOCAB_CAMPAIGN_GROUP_COUNT).l
        move.b  d3,(VOCAB_CAMPAIGN_EDGE_COUNT).l
        move.b  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_OBJECT,a4),(VOCAB_CAMPAIGN_COMPLETION_OBJECT).l
        move.b  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_FIELD,a4),(VOCAB_CAMPAIGN_COMPLETION_FIELD).l
        move.b  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_FLAGS+1,a4),(VOCAB_CAMPAIGN_COMPLETION_FLAGS).l
        move.w  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_MASK,a4),d0
        move.w  d0,(VOCAB_CAMPAIGN_COMPLETION_MASK).l
        move.w  (CAMPAIGN_RUNTIME_PROFILE_COMPLETION_VALUE,a4),d0
        move.w  d0,(VOCAB_CAMPAIGN_COMPLETION_VALUE).l
        move.w  (CAMPAIGN_RUNTIME_PROFILE_NUMERIC_ID,a4),d0
        move.w  d0,(VOCAB_STAGE_ID).l
        move.l  a4,(VOCAB_CAMPAIGN_PROFILE_PTR).l
        move.l  a5,(VOCAB_CAMPAIGN_SEMANTIC_PTR).l
        move.l  a6,(VOCAB_CAMPAIGN_GROUP_PTR).l
        move.l  a2,(VOCAB_CAMPAIGN_EDGE_PTR).l
        bsr.w   CampaignResetAttemptState
        moveq   #ENGINE_RESULT_OK,d0
        bra.s   .done

.not_found:
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        andi.w  #$FFFC,(ENGINE_FLAGS).l
        clr.w   (ENGINE_PROJECTION_LENGTH).l
        move.w  #ENGINE_ERROR_ENCOUNTER_NOT_FOUND,(ENGINE_LAST_ERROR).l
        move.w  #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
        moveq   #ENGINE_RESULT_NOT_FOUND,d0
        bra.s   .done
.bad_contract:
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        andi.w  #$FFFC,(ENGINE_FLAGS).l
        clr.w   (ENGINE_PROJECTION_LENGTH).l
        move.w  #ENGINE_ERROR_BAD_DESCRIPTOR,(ENGINE_LAST_ERROR).l
        move.w  #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
        moveq   #ENGINE_RESULT_BAD_DESCRIPTOR,d0
        bra.s   .done
.engine_failed:
        cmpi.w  #ENGINE_RESULT_BAD_DESCRIPTOR,d0
        bne.s   .done
        move.w  #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

; CampaignResetAttemptState
; Clears attempt-local latches and interaction/event state while retaining the
; projected campaign identity and immutable derived ROM bindings.
; Out: d0.w=GAMEPLAY_RESULT_OK or GAMEPLAY_RESULT_INVALID_STATE.
; Clobbers d0-d2/a0-a1.
CampaignResetAttemptState:
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.s   .invalid
        lea     (VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BASE).l,a0
        moveq   #0,d0
        moveq   #(VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BYTES/4)-1,d1
.clear_latches:
        move.l  d0,(a0)+
        dbf     d1,.clear_latches
        clr.w   (VOCAB_CAMPAIGN_EVENT_FLAGS).l
        move.w  #$FFFF,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l
        andi.w  #$FFFB,(VOCAB_CAMPAIGN_FLAGS).l
        andi.w  #$FFF7,(VOCAB_FLAGS).l
        moveq   #GAMEPLAY_RESULT_OK,d0
        rts
.invalid:
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
        rts

; CampaignBeginFrame
; Clears only frame-local presentation and interaction facts. It never reads a
; frame counter and never advances a timer.
; Out: d0.w=GAMEPLAY_RESULT_OK or GAMEPLAY_RESULT_INVALID_STATE.
CampaignBeginFrame:
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.s   .invalid
        clr.w   (VOCAB_CAMPAIGN_EVENT_FLAGS).l
        move.w  #$FFFF,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l
        moveq   #GAMEPLAY_RESULT_OK,d0
        rts
.invalid:
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
        rts

; CampaignFindSemantic
; In: d0.w=globally unique logical object ID.
; Out: d0.w=semantic index and a0=record, or d0.w=$FFFF and a0=0.
; Clobbers d0-d2/a0-a1.
CampaignFindSemantic:
        move.w  d0,d2
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        bne.s   .not_found
        moveq   #0,d1
        move.b  (VOCAB_CAMPAIGN_SEMANTIC_COUNT).l,d1
        cmp.w   d1,d2
        bhs.s   .not_found
        movea.l (VOCAB_CAMPAIGN_SEMANTIC_PTR).l,a0
        move.l  a0,d1
        beq.s   .not_found
        btst    #0,d1
        bne.s   .not_found
        move.w  d2,d1
        lsl.w   #4,d1
        adda.w  d1,a0
        moveq   #0,d1
        move.b  (CAMPAIGN_RUNTIME_SEMANTIC_OBJECT,a0),d1
        cmp.w   d2,d1
        bne.s   .not_found
        move.w  d2,d0
        rts
.not_found:
        moveq   #-1,d0
        suba.l  a0,a0
        rts

; CampaignFindObject
; In: d0.w=globally unique logical object ID.
; Out: d0.w=pool type, d1.w=physical slot, a0=active object; or d0/d1=$FFFF,
;      a0=0. Projectile/effect pools are excluded because their transient slot
;      IDs are not authored campaign identities.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignFindObject:
        movem.l d3-d5/d7/a2-a3,-(a7)
        move.w  d0,d7
        bsr.w   CampaignFindSemantic
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.s   .not_found

        lea     (EnginePoolDescriptorTable).l,a2
; Legal allocation/free/reset paths maintain exact live counts. Skipping an
; empty pool preserves the same active-ID search and physical-slot ordering.
        lea     (ENGINE_POOL_COUNTS-1).l,a1
        moveq   #POOL_TYPE_ENEMY,d3
.pool:
        cmpi.w  #POOL_TYPE_PROJECTILE,d3
        beq.s   .next_pool
        cmpi.w  #POOL_TYPE_EFFECT,d3
        beq.s   .next_pool
        tst.b   (a1,d3.w)
        beq.s   .next_pool
        movea.l (a2),a3
        moveq   #0,d4
        move.w  4(a2),d4
        subq.w  #1,d4
        moveq   #0,d5
.slot:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .next_slot
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a3),d0
        cmp.w   d7,d0
        beq.s   .found
.next_slot:
        adda.w  #OBJECT_BYTES,a3
        addq.w  #1,d5
        dbf     d4,.slot
.next_pool:
        adda.w  #8,a2
        addq.w  #1,d3
        cmpi.w  #POOL_TYPE_COUNT,d3
        bls.s   .pool
.not_found:
        moveq   #-1,d0
        moveq   #-1,d1
        suba.l  a0,a0
        bra.s   .done
.found:
        move.w  d3,d0
        move.w  d5,d1
        movea.l a3,a0
.done:
        movem.l (a7)+,d3-d5/d7/a2-a3
        rts

; CampaignReadObjectField
; In: a0=active object, d0.w=generated reference field ID.
; Out: d0.w=value and d1.w=0, or d0.w=0 and d1.w=$FFFF on error.
; Health is the virtual unsigned high byte of OBJECT_STATE.
CampaignReadObjectField:
        move.w  d0,d2
        move.l  a0,d0
        beq.s   .invalid
        btst    #0,d0
        bne.s   .invalid
        tst.b   (OBJECT_ACTIVE,a0)
        beq.s   .invalid
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_STATE,d2
        beq.s   .state
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_TIMER,d2
        beq.s   .timer
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_PARAM,d2
        beq.s   .param
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_HEALTH,d2
        bne.s   .invalid
        moveq   #0,d0
        move.b  (OBJECT_STATE,a0),d0
        bra.s   .valid
.state:
        move.w  (OBJECT_STATE,a0),d0
        bra.s   .valid
.timer:
        move.w  (OBJECT_TIMER,a0),d0
        bra.s   .valid
.param:
        move.w  (OBJECT_PARAM,a0),d0
.valid:
        moveq   #0,d1
        rts
.invalid:
        moveq   #0,d0
        moveq   #-1,d1
        rts

; CampaignWriteObjectField
; In: a0=active object, d0.w=generated reference field ID, d1.w=value.
; Out: d0.w=0 on success or $FFFF on error. Health writes only the high byte
; of OBJECT_STATE and preserves its low-byte state bits.
CampaignWriteObjectField:
        move.w  d0,d2
        move.l  a0,d0
        beq.s   .invalid
        btst    #0,d0
        bne.s   .invalid
        tst.b   (OBJECT_ACTIVE,a0)
        beq.s   .invalid
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_STATE,d2
        beq.s   .state
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_TIMER,d2
        beq.s   .timer
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_PARAM,d2
        beq.s   .param
        cmpi.w  #CAMPAIGN_RUNTIME_REFERENCE_HEALTH,d2
        bne.s   .invalid
        andi.w  #$00FF,d1
        lsl.w   #8,d1
        move.w  (OBJECT_STATE,a0),d2
        andi.w  #$00FF,d2
        or.w    d1,d2
        move.w  d2,(OBJECT_STATE,a0)
        bra.s   .valid
.state:
        move.w  d1,(OBJECT_STATE,a0)
        bra.s   .valid
.timer:
        move.w  d1,(OBJECT_TIMER,a0)
        bra.s   .valid
.param:
        move.w  d1,(OBJECT_PARAM,a0)
.valid:
        moveq   #0,d0
        rts
.invalid:
        moveq   #-1,d0
        rts

; CampaignGetSemanticDuration
; In: d0.w=logical object ID.
; Out: d0.w=authored duration and d1.w=0, or d0.w=0/d1.w=$FFFF.
; a0 is the semantic record on success.
CampaignGetSemanticDuration:
        bsr.w   CampaignFindSemantic
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.s   .not_found
        move.w  (CAMPAIGN_RUNTIME_SEMANTIC_DURATION,a0),d0
        moveq   #0,d1
        rts
.not_found:
        moveq   #0,d0
        moveq   #-1,d1
        rts

; CampaignDependencyGateSatisfied
; In: a0=active dependency consumer.
; Out: d0.w=1 when the consumer has no generated group or its live declared
; result field matches that group's result mask/value; zero otherwise.
; Malformed active bindings set the campaign contract-error flag and fail
; closed. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignDependencyGateSatisfied:
        movem.l d3-d6/a2-a3,-(a7)
        movea.l a0,a2
        move.l  a2,d0
        beq.w   .contract_error
        btst    #0,d0
        bne.w   .contract_error
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .contract_error
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.w   .contract_error

        move.w  (OBJECT_PARAM,a2),d3
        andi.w  #VOCAB_PARAM_LINK_MASK,d3
        beq.s   .satisfied
        moveq   #0,d4
        move.b  (VOCAB_CAMPAIGN_GROUP_COUNT).l,d4
        cmp.w   d4,d3
        bhi.w   .contract_error
        movea.l (VOCAB_CAMPAIGN_GROUP_PTR).l,a3
        move.l  a3,d0
        beq.w   .contract_error
        btst    #0,d0
        bne.w   .contract_error
        move.w  d3,d0
        subq.w  #1,d0
        mulu.w  #CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES,d0
        adda.l  d0,a3

; A consumer can own at most one group, so the encoded low byte must resolve
; back to this exact generated logical ID.
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_CONSUMER,a3),d0
        moveq   #0,d1
        move.b  (OBJECT_LOGICAL_ID,a2),d1
        cmp.w   d1,d0
        bne.w   .contract_error
        move.w  (CAMPAIGN_RUNTIME_GROUP_RESULT_MASK,a3),d5
        beq.w   .contract_error
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_RESULT_FIELD,a3),d0
        movea.l a2,a0
        bsr.w   CampaignReadObjectField
        tst.w   d1
        bne.w   .contract_error
        and.w   d5,d0
        move.w  (CAMPAIGN_RUNTIME_GROUP_RESULT_VALUE,a3),d6
        and.w   d5,d6
        cmp.w   d6,d0
        bne.s   .not_satisfied
.satisfied:
        moveq   #1,d0
        bra.s   .done
.contract_error:
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.not_satisfied:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d6/a2-a3
        rts

; CampaignApplyDependencyResult
; In: a0=consumer object, a1=group record, d0.w=zero for false/nonzero true.
; Out: d0.w=0 or $FFFF. Applies only the declared result mask.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignApplyDependencyResult:
        movem.l d3-d5/a2-a3,-(a7)
        move.w  d0,d3
        movea.l a0,a2
        movea.l a1,a3
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_RESULT_FIELD,a3),d0
        movea.l a2,a0
        bsr.w   CampaignReadObjectField
        tst.w   d1
        bne.s   .invalid
        move.w  d0,d4
        move.w  (CAMPAIGN_RUNTIME_GROUP_RESULT_MASK,a3),d5
        move.w  d5,d2
        not.w   d2
        and.w   d2,d4
        tst.w   d3
        beq.s   .store
        move.w  (CAMPAIGN_RUNTIME_GROUP_RESULT_VALUE,a3),d2
        and.w   d5,d2
        or.w    d2,d4
.store:
        move.w  d4,d1
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_RESULT_FIELD,a3),d0
        movea.l a2,a0
        bsr.w   CampaignWriteObjectField
        bra.s   .done
.invalid:
        moveq   #-1,d0
.done:
        movem.l (a7)+,d3-d5/a2-a3
        rts

; CampaignEvaluateDependencies
; Evaluates every directed group once in generated order. ALL/ANY provider
; ranges are immutable and disjoint. A projected bitset detects rising/falling
; edges. True outputs are maintained without refreshing duration timers, so
; callers may invoke this routine before and after combat in the same frame.
; No result depends on ENGINE_TICK or on the number of evaluator calls.
; Out: d0.w=GAMEPLAY_RESULT_OK or GAMEPLAY_RESULT_INVALID_STATE.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignEvaluateDependencies:
        movem.l d3-d7/a2-a6,-(a7)
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.w   .invalid
        moveq   #0,d7
        move.b  (VOCAB_CAMPAIGN_GROUP_COUNT).l,d7
        beq.w   .ok
        movea.l (VOCAB_CAMPAIGN_GROUP_PTR).l,a6
        move.l  a6,d0
        beq.w   .contract_error
        btst    #0,d0
        bne.w   .contract_error
        moveq   #0,d6
        subq.w  #1,d7
.group:
        moveq   #0,d4
        move.b  (CAMPAIGN_RUNTIME_GROUP_POLICY,a6),d4
        cmpi.w  #CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ALL,d4
        beq.s   .all_group
        cmpi.w  #CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ANY,d4
        bne.w   .contract_error
        moveq   #0,d5
        bra.s   .predicate_ready
.all_group:
        moveq   #1,d5
.predicate_ready:
        moveq   #0,d3
        move.b  (CAMPAIGN_RUNTIME_GROUP_EDGE_COUNT,a6),d3
        beq.w   .contract_error
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_FIRST_EDGE,a6),d0
        mulu.w  #CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES,d0
        movea.l (VOCAB_CAMPAIGN_EDGE_PTR).l,a5
        adda.l  d0,a5
        subq.w  #1,d3
.edge:
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_EDGE_PROVIDER,a5),d0
        bsr.w   CampaignFindObject
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.w   .contract_error
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_EDGE_FIELD,a5),d0
        bsr.w   CampaignReadObjectField
        tst.w   d1
        bne.w   .contract_error
        move.w  d0,d1
        and.w   (CAMPAIGN_RUNTIME_EDGE_MASK,a5),d1
        move.w  (CAMPAIGN_RUNTIME_EDGE_VALUE,a5),d2
        and.w   (CAMPAIGN_RUNTIME_EDGE_MASK,a5),d2
        cmp.w   d2,d1
        beq.s   .edge_true
        cmpi.w  #CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ALL,d4
        bne.s   .edge_next
        moveq   #0,d5
        bra.s   .edge_next
.edge_true:
        cmpi.w  #CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ANY,d4
        bne.s   .edge_next
        moveq   #1,d5
.edge_next:
        adda.w  #CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES,a5
        dbf     d3,.edge

        move.w  d6,d0
        move.w  d0,d1
        lsr.w   #3,d0
        andi.w  #$0007,d1
        lea     (VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BASE).l,a2
        adda.w  d0,a2
        moveq   #0,d2
        btst    d1,(a2)
        beq.s   .latch_ready
        moveq   #1,d2
.latch_ready:
        tst.w   d5
        beq.w   .predicate_false
        tst.w   d2
        beq.w   .rise

; While latched, zero-duration outputs remain level true. Timed outputs remain
; true only while their independently owned OBJECT_TIMER is nonzero.
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_CONSUMER,a6),d0
        bsr.w   CampaignFindObject
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.w   .contract_error
        movea.l a0,a4
        tst.w   (CAMPAIGN_RUNTIME_GROUP_DURATION,a6)
        beq.s   .maintain_true
        moveq   #CAMPAIGN_RUNTIME_REFERENCE_TIMER,d0
        movea.l a4,a0
        bsr.w   CampaignReadObjectField
        tst.w   d1
        bne.w   .contract_error
        tst.w   d0
        bne.s   .maintain_true
; An elapsed pulse clears its authored result without clearing the latch. This
; prevents a still-true predicate from retriggering every frame; a subsequent
; false edge rearms the group.
        movea.l a4,a0
        movea.l a6,a1
        moveq   #0,d0
        bsr.w   CampaignApplyDependencyResult
        tst.w   d0
        bne.w   .contract_error
        bra.w   .group_next
.maintain_true:
        movea.l a4,a0
        movea.l a6,a1
        moveq   #1,d0
        bsr.w   CampaignApplyDependencyResult
        tst.w   d0
        bne.w   .contract_error
        bra.w   .group_next

.rise:
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_CONSUMER,a6),d0
        bsr.w   CampaignFindObject
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.w   .contract_error
        movea.l a0,a4
        movea.l a4,a0
        movea.l a6,a1
        moveq   #1,d0
        bsr.w   CampaignApplyDependencyResult
        tst.w   d0
        bne.w   .contract_error
        move.w  (CAMPAIGN_RUNTIME_GROUP_DURATION,a6),d1
        beq.s   .set_latch
        moveq   #CAMPAIGN_RUNTIME_REFERENCE_TIMER,d0
        movea.l a4,a0
        bsr.w   CampaignWriteObjectField
        tst.w   d0
        bne.w   .contract_error
.set_latch:
        move.w  d6,d0
        move.w  d0,d1
        lsr.w   #3,d0
        andi.w  #$0007,d1
        lea     (VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BASE).l,a2
        adda.w  d0,a2
        bset    d1,(a2)
        ori.w   #VOCAB_CAMPAIGN_EVENT_DEPENDENCY,(VOCAB_CAMPAIGN_EVENT_FLAGS).l
        bra.s   .group_next

.predicate_false:
        tst.w   d2
        bne.s   .fall
        bra.s   .group_next
.fall:
        moveq   #0,d0
        move.b  (CAMPAIGN_RUNTIME_GROUP_CONSUMER,a6),d0
        bsr.w   CampaignFindObject
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.w   .contract_error
        movea.l a0,a4
        movea.l a4,a0
        movea.l a6,a1
        moveq   #0,d0
        bsr.w   CampaignApplyDependencyResult
        tst.w   d0
        bne.w   .contract_error
        tst.w   (CAMPAIGN_RUNTIME_GROUP_DURATION,a6)
        beq.s   .clear_latch
        moveq   #CAMPAIGN_RUNTIME_REFERENCE_TIMER,d0
        moveq   #0,d1
        movea.l a4,a0
        bsr.w   CampaignWriteObjectField
        tst.w   d0
        bne.w   .contract_error
.clear_latch:
        move.w  d6,d0
        move.w  d0,d1
        lsr.w   #3,d0
        andi.w  #$0007,d1
        lea     (VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BASE).l,a2
        adda.w  d0,a2
        bclr    d1,(a2)
        ori.w   #VOCAB_CAMPAIGN_EVENT_DEPENDENCY,(VOCAB_CAMPAIGN_EVENT_FLAGS).l
.group_next:
        adda.w  #CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES,a6
        addq.w  #1,d6
        dbf     d7,.group
.ok:
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.contract_error:
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.invalid:
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

; CampaignUpdateHazardControllers
; Advances authored, type-specific hazard states after dependency evaluation.
; A closing-door crush field is spatially bound to the live door whose
; top-left lies inside its rectangular bounds. It remains SAFE until that door
; has opened, publishes exactly the authored WARNING tail of the open timer,
; then becomes ACTIVE/CLOSING when the interval expires.
; Out: d0.w=GAMEPLAY_RESULT_OK or GAMEPLAY_RESULT_INVALID_STATE.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignUpdateHazardControllers:
        movem.l d3-d7/a2-a6,-(a7)
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.w   .invalid

        lea     (HAZARD_POOL).l,a2
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_HAZARD-1).l,d7
        cmpi.w  #HAZARD_CAPACITY,d7
        bls.s   .hazard_bound_ready
        moveq   #HAZARD_CAPACITY,d7
.hazard_bound_ready:
        tst.w   d7
        beq.w   .ok
        subq.w  #1,d7
.hazard:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .next
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        bsr.w   CampaignFindSemantic
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.w   .contract_error
        cmpi.w  #CAMPAIGN_RUNTIME_TYPE_CLOSING_DOOR_CRUSH_ZONE,(CAMPAIGN_RUNTIME_SEMANTIC_TYPE_CODE,a0)
        bne.w   .next
        movea.l a0,a3

; This controller requires a nonempty authored rectangle. Decode eight-pixel
; units into 16.8 bounds once, then locate its paired physical door.
        moveq   #0,d3
        move.w  (CAMPAIGN_RUNTIME_SEMANTIC_FLAGS,a3),d3
        andi.w  #VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK,d3
        lsr.w   #VOCAB_CAMPAIGN_GEOMETRY_WIDTH_SHIFT,d3
        moveq   #0,d4
        move.w  (CAMPAIGN_RUNTIME_SEMANTIC_FLAGS,a3),d4
        andi.w  #VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK,d4
        lsr.w   #8,d4
        lsr.w   #3,d4
        tst.w   d3
        beq.w   .contract_error
        tst.w   d4
        beq.w   .contract_error
        lsl.l   #8,d3
        lsl.l   #3,d3
        lsl.l   #8,d4
        lsl.l   #3,d4

        suba.l  a5,a5
        lea     (DEVICE_POOL).l,a4
        moveq   #DEVICE_CAPACITY-1,d6
.door:
        tst.b   (OBJECT_ACTIVE,a4)
        beq.s   .door_next
        move.w  (OBJECT_PARAM,a4),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.s   .door_next
        move.w  (OBJECT_STATE,a4),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .door_next

; Door top-left membership makes the binding deterministic and independent of
; physical slot order when unrelated doors exist elsewhere in the room.
        move.l  (OBJECT_X,a4),d0
        cmp.l   (OBJECT_X,a2),d0
        blt.s   .door_next
        move.l  (OBJECT_X,a2),d1
        add.l   d3,d1
        cmp.l   d1,d0
        bge.s   .door_next
        move.l  (OBJECT_Y,a4),d0
        cmp.l   (OBJECT_Y,a2),d0
        blt.s   .door_next
        move.l  (OBJECT_Y,a2),d1
        add.l   d4,d1
        cmp.l   d1,d0
        bge.s   .door_next
        movea.l a4,a5
        bra.s   .door_ready
.door_next:
        adda.w  #OBJECT_BYTES,a4
        dbf     d6,.door
        bra.w   .contract_error

.door_ready:
        move.w  (OBJECT_STATE,a5),d0
        andi.w  #VOCAB_DEVICE_STATE_OPEN,d0
        beq.s   .door_closed

; Opening arms the field in its otherwise unused timer. OBJECT_STATE remains
; one exact authored value: SAFE=0, WARNING=$10, or CLOSING/ACTIVE=1.
        move.w  #1,(OBJECT_TIMER,a2)
        moveq   #0,d0
        move.w  (OBJECT_TIMER,a5),d1
        cmp.w   (CAMPAIGN_RUNTIME_SEMANTIC_DURATION,a3),d1
        bhi.s   .store_state
        tst.w   d1
        beq.s   .door_closed_armed
        move.w  #VOCAB_HAZARD_STATE_WARNING,d0
        bra.s   .store_state

.door_closed:
        moveq   #0,d0
        tst.w   (OBJECT_TIMER,a2)
        beq.s   .safe_before_open
.door_closed_armed:
        moveq   #VOCAB_DEVICE_STATE_ACTIVE,d0
        bra.s   .store_state
.safe_before_open:
        moveq   #0,d0
.store_state:
        move.w  d0,(OBJECT_STATE,a2)
.next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.hazard
.ok:
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.contract_error:
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.invalid:
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

; CampaignCheckCompletion
; Completion is accepted only when this frame's last interaction came from the
; current actor and targeted the generated completion object, and the live
; generated field predicate matches. Echo interactions can never stabilize.
; Out: d0.w=GAMEPLAY_RESULT_OK, GAMEPLAY_RESULT_PROGRAM_STABLE, or
;      GAMEPLAY_RESULT_INVALID_STATE.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
CampaignCheckCompletion:
        movem.l d3-d4/a2,-(a7)
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        move.w  d0,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        bne.w   .invalid
        andi.w  #VOCAB_CAMPAIGN_FLAG_COMPLETE,d0
        bne.w   .already_complete

        moveq   #0,d3
        move.b  (VOCAB_CAMPAIGN_COMPLETION_OBJECT).l,d3
        cmpi.w  #CAMPAIGN_RUNTIME_NO_COMPLETION_OBJECT,d3
        beq.w   .not_complete
        cmpi.b  #GAMEPLAY_CURRENT_ACTOR_SLOT,(VOCAB_CAMPAIGN_LAST_INTERACTION_ACTOR).l
        bne.w   .not_complete
        cmp.b   (VOCAB_CAMPAIGN_LAST_INTERACTION_OBJECT).l,d3
        bne.w   .not_complete

        move.w  d3,d0
        bsr.w   CampaignFindObject
        cmpi.w  #CAMPAIGN_CONTROLLER_NOT_FOUND,d0
        beq.w   .contract_error
        movea.l a0,a2
        moveq   #0,d0
        move.b  (VOCAB_CAMPAIGN_COMPLETION_FIELD).l,d0
        movea.l a2,a0
        bsr.w   CampaignReadObjectField
        tst.w   d1
        bne.w   .contract_error
        move.w  (VOCAB_CAMPAIGN_COMPLETION_MASK).l,d4
        and.w   d4,d0
        move.w  (VOCAB_CAMPAIGN_COMPLETION_VALUE).l,d1
        and.w   d4,d1
        cmp.w   d1,d0
        bne.w   .not_complete

; A lethal hit later in the same frame takes precedence over an earlier B
; edge. Dead current actors can never stabilize an encounter.
        tst.b   (GAMEPLAY_CURRENT_ACTOR+ACTOR_ACTIVE).l
        beq.w   .not_complete
        move.w  (VOCAB_FLAGS).l,d0
        andi.w  #VOCAB_FLAG_CURRENT_DESTROYED,d0
        bne.w   .not_complete

        move.w  (VOCAB_STAGE_ID).l,d0
        moveq   #0,d1
        bsr.w   VocabularyFinalizeStage
        ori.w   #VOCAB_CAMPAIGN_FLAG_COMPLETE,(VOCAB_CAMPAIGN_FLAGS).l
        ori.w   #VOCAB_CAMPAIGN_EVENT_COMPLETION,(VOCAB_CAMPAIGN_EVENT_FLAGS).l
        move.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        move.w  #GAMEPLAY_TERMINAL_PROGRAM_STABLE,(ENGINE_TERMINAL_REASON).l
        ori.w   #GAMEPLAY_FLAG_PROGRAM_STABLE,(GAMEPLAY_FLAGS).l
        ori.b   #TIMELINE_FLAG_PROGRAM_STABLE,(TIMELINE_FLAGS).l
        move.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,(TIMELINE_LAST_RESULT).l
        move.w  #GAMEPLAY_COMMIT_PROGRAM_STABLE,(GAMEPLAY_LAST_COMMIT_REASON).l
        move.w  (ENGINE_TICK).l,d0
        cmpi.w  #ENGINE_ATTEMPT_TICKS,d0
        bhs.s   .stable_tick_ready
        addq.w  #1,d0
.stable_tick_ready:
        move.w  d0,(GAMEPLAY_STABLE_TICK).l
.already_complete:
        moveq   #GAMEPLAY_RESULT_PROGRAM_STABLE,d0
        bra.s   .done
.not_complete:
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.contract_error:
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.invalid:
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
.done:
        movem.l (a7)+,d3-d4/a2
        rts

        assert  CAMPAIGN_RUNTIME_FORMAT_VERSION=CAMPAIGN_CONTROLLER_FORMAT_VERSION,"campaign controller requires ABI v3"
        assert  CAMPAIGN_RUNTIME_PROFILE_BYTES=64,"campaign profile ABI drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_BYTES=16,"campaign semantic ABI drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_BYTES=VOCAB_CAMPAIGN_SEMANTIC_BYTES,"campaign semantic stride alias drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_OBJECT=VOCAB_CAMPAIGN_SEMANTIC_OBJECT,"campaign semantic object alias drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_FLAGS=VOCAB_CAMPAIGN_SEMANTIC_FLAGS,"campaign semantic flags alias drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_WIDTH_SHIFT=VOCAB_CAMPAIGN_GEOMETRY_WIDTH_SHIFT,"campaign geometry width shift drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_WIDTH_MASK=VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK,"campaign geometry width mask drift"
        assert  CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_HEIGHT_MASK=VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK,"campaign geometry height mask drift"
        assert  CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES=16,"campaign group ABI drift"
        assert  CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES=8,"campaign edge ABI drift"
        assert  CAMPAIGN_RUNTIME_REFERENCE_STATE=1,"campaign state field ID drift"
        assert  CAMPAIGN_RUNTIME_REFERENCE_TIMER=2,"campaign timer field ID drift"
        assert  CAMPAIGN_RUNTIME_REFERENCE_PARAM=3,"campaign param field ID drift"
        assert  CAMPAIGN_RUNTIME_REFERENCE_HEALTH=4,"campaign health field ID drift"
        assert  CAMPAIGN_RUNTIME_ENCOUNTER_COUNT<=$00FF,"campaign directory index exceeds projected byte"
        assert  CampaignRuntimeDescriptorTableEnd-CampaignRuntimeDescriptorTable=CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"campaign descriptor table size drift"
        assert  CampaignRuntimeProfileTableEnd-CampaignRuntimeProfileTable=CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"campaign profile table size drift"
        assert  VOCAB_CAMPAIGN_DEPENDENCY_LATCH_BYTES*8>=$0100,"campaign latch bitset cannot address byte group indices"
        assert  VOCAB_CAMPAIGN_DERIVED_END<=VOCAB_SCRATCH_BASE,"campaign derived pointers overlap scratch"
