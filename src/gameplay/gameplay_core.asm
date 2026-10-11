; Milestone 4 authoritative replay/echo controller and B01 step.

; GameplayInitialize
; Initializes the frozen M3 engine on encounter zero, owns the exact 5 KiB
; replay/timeline allocation, and starts a zero-echo attempt in physical bank 6.
; Out: gameplay result. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayInitialize:
        movem.l d3-d4/a2,-(a7)
        bsr.w   EngineInitialize
        bsr.w   VocabularyInitialize

; Initialization is the only operation which clears replay persistence. The
; loop covers exactly $FF0000-$FF13FF: seven 720-byte banks plus 56-byte
; descriptors plus 24-byte timeline control.
        lea     (REPLAY_BANK_BASE).l,a0
        moveq   #0,d0
        move.w  #((TIMELINE_CONTROL_END-REPLAY_BANK_BASE)/4)-1,d1
.clear_replay_timeline:
        move.l  d0,(a0)+
        dbf     d1,.clear_replay_timeline

        moveq   #0,d0
        ifd     DEBUG_START_ENCOUNTER
        move.w  #DEBUG_START_ENCOUNTER,d0
        endif
        bsr.w   CampaignLoadEncounter
        tst.w   d0
        bne.w   .engine_error

        move.l  #TIMELINE_MAGIC_VALUE,(TIMELINE_MAGIC).l
        move.w  #TIMELINE_VERSION_VALUE,(TIMELINE_VERSION).l
        move.w  #TIMELINE_CONTROL_BYTES,(TIMELINE_BYTES).l
        clr.b   (TIMELINE_ECHO_COUNT).l
        move.l  #$FFFFFFFF,(TIMELINE_ECHO_ORDER).l
        move.w  #$FFFF,(TIMELINE_ECHO_ORDER+4).l
        move.b  #TIMELINE_NO_SELECTION,(TIMELINE_SELECTED_ORDINAL).l
        move.b  #TIMELINE_FLAG_INITIALIZED,(TIMELINE_FLAGS).l
        clr.w   (TIMELINE_PENDING_REASON).l
        clr.w   (TIMELINE_ATTEMPT_LENGTH).l
        clr.w   (TIMELINE_LAST_RESULT).l

        move.l  #GAMEPLAY_MAGIC_VALUE,(GAMEPLAY_MAGIC).l
        move.w  #GAMEPLAY_VERSION_VALUE,(GAMEPLAY_VERSION).l
        move.w  #GAMEPLAY_CONTROL_BYTES_VALUE,(GAMEPLAY_BYTES).l
        move.w  #GAMEPLAY_FLAG_INITIALIZED,(GAMEPLAY_FLAGS).l
        clr.w   (GAMEPLAY_RESULT).l
        clr.w   (GAMEPLAY_LAST_COMMIT_REASON).l
        clr.w   (GAMEPLAY_REPLACEMENT_FLAGS).l
        clr.l   (GAMEPLAY_LAST_BANK_CRC).l

; Keeping the initial current recording in bank 6 makes banks 0 upward the
; deterministic lowest-free sequence as completed attempts accumulate.
        moveq   #GAMEPLAY_CURRENT_ACTOR_SLOT,d0
        bsr.w   GameplayClearCurrentBank
        bsr.w   GameplayReinstantiateActors
        tst.w   d0
        bne.s   .engine_error
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done

.engine_error:
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(TIMELINE_LAST_RESULT).l
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(GAMEPLAY_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
.done:
        movem.l (a7)+,d3-d4/a2
        rts

; GameplayReinstantiateActors
; Rebuilds echo actors oldest-to-newest into physical slots 0..5 and current
; into physical slot 6, all at the immutable encounter spawn. Pools are the
; canonical state just reconstructed by EngineLoadEncounter or
; EngineResetCanonicalState. The reconstruction leaves its engine-only current
; actor in slot 0, so clear exactly that 32-byte record before installing the
; gameplay roster; every other actor slot is already zero.
; Out: d0.w=0. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayReinstantiateActors:
        movem.l d3-d7/a2-a4,-(a7)

        lea     (ENGINE_ACTOR_BASE).l,a0
        moveq   #0,d0
        moveq   #0,d1
        moveq   #0,d2
        movea.l d0,a1
        movem.l d0-d2/a1,(a0)
        movem.l d0-d2/a1,16(a0)

        moveq   #0,d6
        move.b  (TIMELINE_ECHO_COUNT).l,d6
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d6
        bhi.w   .invalid_state
        bsr.w   GameplayGetEchoCap
        cmp.w   d0,d6
        bhi.w   .invalid_state

        lea     (ENGINE_ACTOR_BASE).l,a2
        lea     (TIMELINE_ECHO_ORDER).l,a3
        moveq   #0,d5
        tst.w   d6
        beq.s   .current
        move.w  d6,d7
        subq.w  #1,d7
.echo:
        moveq   #0,d1
        move.b  (a3,d5.w),d1
        cmpi.w  #REPLAY_BANK_COUNT,d1
        bhs.w   .invalid_state
        move.w  d5,d0
        movea.l a2,a0
        bsr.w   GameplayInitializeActor
        adda.w  #ACTOR_BYTES,a2
        addq.w  #1,d5
        dbf     d7,.echo

.current:
        moveq   #0,d1
        move.b  (TIMELINE_CURRENT_BANK).l,d1
        cmpi.w  #REPLAY_BANK_COUNT,d1
        bhs.w   .invalid_state
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        moveq   #GAMEPLAY_CURRENT_ACTOR_SLOT,d0
        bsr.w   GameplayInitializeActor

        move.w  d6,d0
        addq.w  #1,d0
        move.b  d0,(ENGINE_ACTIVE_ACTOR_COUNT).l
        move.b  #GAMEPLAY_CURRENT_ACTOR_SLOT,(GAMEPLAY_CURRENT_ACTOR_INDEX).l
        move.b  d6,(GAMEPLAY_ECHO_ACTOR_COUNT).l
        clr.l   (ENGINE_INPUT_HELD).l
        move.b  #$01,(ENGINE_FACING_X).l
        clr.l   (GAMEPLAY_CURRENT_INPUT).l
        clr.w   (GAMEPLAY_PHASE).l
        clr.w   (GAMEPLAY_STEP_COUNT).l
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        move.w  d0,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d1
        beq.s   .legacy_reset
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID|VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.w   .invalid_state
        bsr.w   VocabularyResetOrdinary
        tst.w   d0
        bne.w   .invalid_state
        bsr.w   CampaignResetAttemptState
        tst.w   d0
        bne.w   .invalid_state
        bra.s   .reset_ready
.legacy_reset:
        bsr.w   GameplayResetVerticalSlice
.reset_ready:

; Publish the authoritative echo/current image as the sole initial checkpoint.
        clr.w   (ENGINE_CHECKPOINT_COUNT).l
        bsr.w   GameplayBuildProjection
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.w   .done

.invalid_state:
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        move.w  #ENGINE_ERROR_INVALID_STATE,(ENGINE_LAST_ERROR).l
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; GameplayStep
; In: d0.b=current held UDLRABC.
; Out: 0 active, 1 PROGRAM_STABLE, 8 timeout, or another gameplay error.
; One authoritative tick has this immutable order:
;   record current input;
;   simulate echoes oldest-to-newest;
;   simulate current;
;   resolve interactions;
;   advance fixed pools once;
;   publish at most one due/terminal checkpoint.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayStep:
        movem.l d3-d7/a2-a5,-(a7)
        move.b  d0,d7

        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_INITIALIZED,d0
        beq.w   .not_initialized
        cmpi.w  #ENGINE_STATE_ACTIVE,(ENGINE_STATE).l
        beq.s   .active
        cmpi.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        bne.w   .invalid_state
        cmpi.w  #GAMEPLAY_TERMINAL_PROGRAM_STABLE,(ENGINE_TERMINAL_REASON).l
        beq.w   .already_stable
        cmpi.w  #GAMEPLAY_TERMINAL_CURRENT_DESTROYED,(ENGINE_TERMINAL_REASON).l
        beq.w   .already_destroyed
        cmpi.w  #ENGINE_TERMINAL_TIMEOUT,(ENGINE_TERMINAL_REASON).l
        beq.w   .already_timeout
        bra.w   .invalid_state

.active:
        moveq   #0,d0
        move.b  d7,d0
        bsr.w   GameplaySanitizeInput
        move.b  d0,d7
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        move.w  d0,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d1
        beq.s   .frame_ready
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID|VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,d0
        cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0
        bne.w   .invalid_state
        bsr.w   CampaignBeginFrame
        tst.w   d0
        bne.w   .invalid_state
.frame_ready:

        moveq   #0,d6
        move.w  (ENGINE_TICK).l,d6
        cmpi.w  #REPLAY_BANK_BYTES,d6
        bhs.w   .recording_overrun
        moveq   #0,d5
        move.b  (TIMELINE_CURRENT_BANK).l,d5
        cmpi.w  #REPLAY_BANK_COUNT,d5
        bhs.w   .invalid_state

; Record before any simulation so a partial attempt has one byte per consumed
; authoritative step and can be finalized without padding or overrun.
        move.w  #GAMEPLAY_PHASE_RECORD_CURRENT,(GAMEPLAY_PHASE).l
        move.w  d5,d0
        bsr.w   GameplayGetBankAddress
        adda.w  d6,a0
        move.b  d7,(a0)
        move.w  d6,d4
        addq.w  #1,d4
        move.w  d4,(TIMELINE_ATTEMPT_LENGTH).l
        move.w  d5,d0
        bsr.w   GameplayGetBankDescriptor
        move.w  d4,(REPLAY_DESCRIPTOR_LENGTH,a0)
        cmpi.w  #REPLAY_BANK_BYTES,d4
        bne.s   .recorded
        ori.b   #TIMELINE_FLAG_RECORDING_FULL,(TIMELINE_FLAGS).l
.recorded:
        andi.w  #$FFFB,(ENGINE_FLAGS).l

; Playback actors occupy physical slots in the same chronological order as
; TIMELINE_ECHO_ORDER. A recording ending early contributes neutral input.
        move.w  #GAMEPLAY_PHASE_ECHOES,(GAMEPLAY_PHASE).l
        moveq   #0,d3
        move.b  (TIMELINE_ECHO_COUNT).l,d3
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d3
        bhi.w   .invalid_state
        bsr.w   GameplayGetEchoCap
        cmp.w   d0,d3
        bhi.w   .invalid_state
        lea     (ENGINE_ACTOR_BASE).l,a2
        lea     (TIMELINE_ECHO_ORDER).l,a3
        moveq   #0,d4
        tst.w   d3
        beq.s   .simulate_current
        subq.w  #1,d3
.echo:
        moveq   #0,d5
        move.b  (a3,d4.w),d5
        cmpi.w  #REPLAY_BANK_COUNT,d5
        bhs.w   .invalid_state
        move.w  d5,d0
        bsr.w   GameplayGetBankDescriptor
        moveq   #0,d1
        move.b  (REPLAY_DESCRIPTOR_FLAGS,a0),d1
        andi.b  #REPLAY_FLAG_VALID|REPLAY_FLAG_ECHO,d1
        cmpi.b  #REPLAY_FLAG_VALID|REPLAY_FLAG_ECHO,d1
        bne.w   .invalid_state
        moveq   #0,d0
        cmp.w   (REPLAY_DESCRIPTOR_LENGTH,a0),d6
        bhs.s   .echo_input_ready
        move.w  d5,d0
        bsr.w   GameplayGetBankAddress
        adda.w  d6,a0
        moveq   #0,d0
        move.b  (a0),d0
.echo_input_ready:
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .echo_advanced
        movea.l a2,a0
        bsr.w   GameplayApplyActorInput
.echo_advanced:
        adda.w  #ACTOR_BYTES,a2
        addq.w  #1,d4
        dbf     d3,.echo

.simulate_current:
        move.w  #GAMEPLAY_PHASE_CURRENT,(GAMEPLAY_PHASE).l
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a2
        moveq   #0,d0
        move.b  d7,d0
        movea.l a2,a0
        bsr.w   GameplayApplyActorInput
        move.b  d0,(GAMEPLAY_CURRENT_INPUT).l
        move.b  d1,(GAMEPLAY_CURRENT_PRESSED).l
        move.b  d2,(GAMEPLAY_CURRENT_RELEASED).l
        move.b  (ACTOR_PREVIOUS_INPUT,a2),(GAMEPLAY_CURRENT_PREVIOUS).l
        move.b  d0,(ENGINE_INPUT_HELD).l
        move.b  d1,(ENGINE_INPUT_PRESSED).l
        move.b  d2,(ENGINE_INPUT_RELEASED).l
        move.b  (ACTOR_PREVIOUS_INPUT,a2),(ENGINE_INPUT_PREVIOUS).l
        move.b  #$01,(ENGINE_FACING_X).l
        move.w  (ACTOR_FLAGS,a2),d0
        andi.w  #GAMEPLAY_ACTOR_FLAG_FACING_LEFT,d0
        beq.s   .current_facing_ready
        move.b  #$FF,(ENGINE_FACING_X).l
.current_facing_ready:

        move.w  #GAMEPLAY_PHASE_INTERACTIONS,(GAMEPLAY_PHASE).l
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.s   .campaign_vocabulary
        bsr.w   GameplayResolveVerticalSlice
        move.w  d0,d5

        move.w  #GAMEPLAY_PHASE_POOLS,(GAMEPLAY_PHASE).l
        bsr.w   EngineUpdateAllPools
        bra.w   .simulation_ready

.campaign_vocabulary:
; Campaign mode has exactly one owner for each mutable pool. Actor A/B/C edges
; were already resolved oldest-to-newest above.
        bsr.w   VocabularyUpdateDevices
        bsr.w   CampaignEvaluateDependencies
        tst.w   d0
        bne.w   .invalid_state
        bsr.w   VocabularyBuildBeamCache
        bsr.w   VocabularyUpdateEnemies
        bsr.w   VocabularyUpdateProjectilesAndDamage
        bsr.w   CampaignEvaluateDependencies
        tst.w   d0
        bne.w   .invalid_state
        bsr.w   CampaignUpdateHazardControllers
        tst.w   d0
        bne.w   .invalid_state
        bsr.w   VocabularyUpdateActorsAndHazards
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,d0
        bne.w   .invalid_state
; Dash/phase/invulnerability timers expire only after this frame's collision
; and damage scans, yielding exactly VOCAB_DASH_TICKS protected motion steps.
        bsr.w   VocabularyTickActorExtensions
        bsr.w   EngineUpdateTransientPools
        move.w  #VOCAB_CPU_WORST_UNITS,(VOCAB_UPDATE_UNITS).l
        move.w  (VOCAB_UPDATE_PEAK).l,d0
        cmpi.w  #VOCAB_CPU_WORST_UNITS,d0
        bhs.s   .campaign_budget_ready
        move.w  #VOCAB_CPU_WORST_UNITS,(VOCAB_UPDATE_PEAK).l
.campaign_budget_ready:
        bsr.w   CampaignCheckCompletion
        move.w  d0,d5
        cmpi.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,d5
        beq.s   .simulation_ready
        tst.w   d5
        bne.w   .invalid_state
        move.w  (VOCAB_FLAGS).l,d0
        andi.w  #VOCAB_FLAG_CURRENT_DESTROYED,d0
        beq.s   .campaign_active
        move.w  #GAMEPLAY_RESULT_CURRENT_DESTROYED,d5
        bra.s   .simulation_ready
.campaign_active:
        moveq   #GAMEPLAY_RESULT_OK,d5
.simulation_ready:

        addq.w  #1,d6
        move.w  d6,(ENGINE_TICK).l
        move.w  d6,(GAMEPLAY_STEP_COUNT).l
        cmpi.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,d5
        beq.s   .program_stable
        cmpi.w  #GAMEPLAY_RESULT_CURRENT_DESTROYED,d5
        beq.w   .current_destroyed
        cmpi.w  #ENGINE_ATTEMPT_TICKS,d6
        bhs.w   .timeout

; The frozen M3 checkpoint cadence remains authoritative.
        moveq   #0,d0
        move.w  d6,d0
        divu.w  #ENGINE_CHECKPOINT_INTERVAL,d0
        swap    d0
        tst.w   d0
        bne.s   .active_done
        move.w  #GAMEPLAY_PHASE_CHECKPOINT,(GAMEPLAY_PHASE).l
        bsr.w   GameplayBuildProjection
.active_done:
        clr.w   (GAMEPLAY_PHASE).l
        clr.w   (GAMEPLAY_RESULT).l
        clr.w   (TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.w   .done

.program_stable:
        move.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        move.w  #GAMEPLAY_TERMINAL_PROGRAM_STABLE,(ENGINE_TERMINAL_REASON).l
        move.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,(TIMELINE_LAST_RESULT).l
        move.w  #GAMEPLAY_PHASE_CHECKPOINT,(GAMEPLAY_PHASE).l
        bsr.w   GameplayBuildProjection
        clr.w   (GAMEPLAY_PHASE).l
        moveq   #GAMEPLAY_RESULT_PROGRAM_STABLE,d0
        bra.w   .done

.current_destroyed:
        move.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        move.w  #GAMEPLAY_TERMINAL_CURRENT_DESTROYED,(ENGINE_TERMINAL_REASON).l
        move.w  #GAMEPLAY_RESULT_CURRENT_DESTROYED,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_CURRENT_DESTROYED,(TIMELINE_LAST_RESULT).l
        move.w  #GAMEPLAY_PHASE_CHECKPOINT,(GAMEPLAY_PHASE).l
        bsr.w   GameplayBuildProjection
        clr.w   (GAMEPLAY_PHASE).l
        moveq   #GAMEPLAY_RESULT_CURRENT_DESTROYED,d0
        bra.w   .done

.timeout:
        move.w  #ENGINE_ATTEMPT_TICKS,(ENGINE_TICK).l
        move.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        move.w  #ENGINE_TERMINAL_TIMEOUT,(ENGINE_TERMINAL_REASON).l
        move.w  #GAMEPLAY_RESULT_TIMEOUT,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_TIMEOUT,(TIMELINE_LAST_RESULT).l
        move.w  #GAMEPLAY_PHASE_CHECKPOINT,(GAMEPLAY_PHASE).l
        bsr.w   GameplayBuildProjection
        clr.w   (GAMEPLAY_PHASE).l
        moveq   #GAMEPLAY_RESULT_TIMEOUT,d0
        bra.s   .done

.already_stable:
        moveq   #GAMEPLAY_RESULT_PROGRAM_STABLE,d0
        bra.s   .done
.already_destroyed:
        moveq   #GAMEPLAY_RESULT_CURRENT_DESTROYED,d0
        bra.s   .done
.already_timeout:
        moveq   #GAMEPLAY_RESULT_TIMEOUT,d0
        bra.s   .done
.recording_overrun:
        move.w  #GAMEPLAY_RESULT_RECORDING_OVERRUN,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_RECORDING_OVERRUN,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_RECORDING_OVERRUN,d0
        bra.s   .done
.not_initialized:
        move.w  #GAMEPLAY_RESULT_NOT_INITIALIZED,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_NOT_INITIALIZED,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_NOT_INITIALIZED,d0
        bra.s   .done
.invalid_state:
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(GAMEPLAY_RESULT).l
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
.done:
        movem.l (a7)+,d3-d7/a2-a5
        rts
