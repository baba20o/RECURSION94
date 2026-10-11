; Fixed replay-bank descriptors, replacement selection, and reset rotation.

; GameplayGetBankAddress
; In: d0.w=bank 0..6. Out: a0=720-byte bank. Clobbers d0.
GameplayGetBankAddress:
        andi.l  #$0000FFFF,d0
        mulu.w  #REPLAY_BANK_BYTES,d0
        lea     (REPLAY_BANK_BASE).l,a0
        adda.l  d0,a0
        rts

; GameplayGetBankDescriptor
; In: d0.w=bank 0..6. Out: a0=descriptor. Clobbers d0.
GameplayGetBankDescriptor:
        andi.l  #$0000FFFF,d0
        lsl.w   #3,d0
        lea     (REPLAY_DESCRIPTOR_BASE).l,a0
        adda.w  d0,a0
        rts

; GameplayMirrorReplacementFlags
; Copies byte-sized timeline selection state into visible gameplay control.
GameplayMirrorReplacementFlags:
        moveq   #0,d0
        move.b  (TIMELINE_FLAGS).l,d0
        move.w  d0,(GAMEPLAY_REPLACEMENT_FLAGS).l
        andi.w  #$FFFB,(GAMEPLAY_FLAGS).l
        andi.w  #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_COMMIT_BLOCKED,d0
        beq.s   .done
        ori.w   #GAMEPLAY_FLAG_REPLACEMENT_VISIBLE,(GAMEPLAY_FLAGS).l
.done:
        rts

; GameplayGetEchoCap
; Out: d0.w=active campaign echo cap (1..6), or the physical legacy cap.
; Invalid campaign metadata fails closed to the physical bound and marks the
; projected campaign contract state. Clobbers d0-d1.
GameplayGetEchoCap:
        moveq   #GAMEPLAY_ECHO_LIMIT,d0
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d1
        beq.s   .done
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d1
        andi.w  #VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d1
        beq.s   .invalid
        moveq   #0,d0
        move.b  (VOCAB_CAMPAIGN_ECHO_CAP).l,d0
        cmpi.w  #1,d0
        blo.s   .invalid
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d0
        bls.s   .done
.invalid:
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
        moveq   #GAMEPLAY_ECHO_LIMIT,d0
.done:
        rts

; GameplayClearCurrentBank
; In: d0.w=new current bank. Clears exactly its descriptor and 720 input bytes.
; Generation advances modulo 256 with zero skipped. Clobbers d0-d2/a0-a1.
GameplayClearCurrentBank:
        movem.l d3/a2,-(a7)
        move.w  d0,d3
        bsr.w   GameplayGetBankDescriptor
        movea.l a0,a2
        moveq   #0,d1
        move.b  (REPLAY_DESCRIPTOR_GENERATION,a2),d1
        addq.b  #1,d1
        bne.s   .generation_ready
        moveq   #1,d1
.generation_ready:
        clr.l   (a2)
        clr.l   4(a2)
        move.b  #REPLAY_FLAG_CURRENT,(REPLAY_DESCRIPTOR_FLAGS,a2)
        move.b  d1,(REPLAY_DESCRIPTOR_GENERATION,a2)

        move.w  d3,d0
        bsr.w   GameplayGetBankAddress
        moveq   #0,d0
        move.w  #(REPLAY_BANK_BYTES/4)-1,d1
.clear:
        move.l  d0,(a0)+
        dbf     d1,.clear
        move.b  d3,(TIMELINE_CURRENT_BANK).l
        movem.l (a7)+,d3/a2
        rts

; GameplayFinalizeCurrentBank
; Computes CRC over exactly TIMELINE_ATTEMPT_LENGTH sanitized input bytes.
; Out: d0.w=0 or recording-overrun error. Clobbers d0-d2/a0-a1.
GameplayFinalizeCurrentBank:
        movem.l d3-d4/a2,-(a7)
        moveq   #0,d3
        move.w  (TIMELINE_ATTEMPT_LENGTH).l,d3
        cmpi.w  #REPLAY_BANK_BYTES,d3
        bhi.s   .overrun
        moveq   #0,d4
        move.b  (TIMELINE_CURRENT_BANK).l,d4
        cmpi.w  #REPLAY_BANK_COUNT,d4
        bhs.s   .overrun

        move.w  d4,d0
        bsr.w   GameplayGetBankDescriptor
        movea.l a0,a2
        move.w  d3,(REPLAY_DESCRIPTOR_LENGTH,a2)
        move.w  d4,d0
        bsr.w   GameplayGetBankAddress
        move.w  d3,d0
        bsr.w   Crc32IsoHdlc
        move.l  d0,(REPLAY_DESCRIPTOR_CRC,a2)
        move.l  d0,(GAMEPLAY_LAST_BANK_CRC).l
        move.b  (REPLAY_DESCRIPTOR_FLAGS,a2),d1
        andi.b  #REPLAY_FLAG_PROTECTED,d1
        ori.b   #REPLAY_FLAG_VALID|REPLAY_FLAG_ECHO,d1
        move.b  d1,(REPLAY_DESCRIPTOR_FLAGS,a2)
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.overrun:
        move.w  #GAMEPLAY_RESULT_RECORDING_OVERRUN,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_RECORDING_OVERRUN,d0
.done:
        movem.l (a7)+,d3-d4/a2
        rts

; GameplayFindFreeBank
; Out: d0.w=lowest bank not present in the active echo order, or $FFFF.
; Clobbers d0-d2/a0-a1.
GameplayFindFreeBank:
        movem.l d3-d5/a2,-(a7)
        moveq   #0,d3
.candidate:
        cmpi.w  #REPLAY_BANK_COUNT,d3
        bhs.s   .none
        moveq   #0,d4
        move.b  (TIMELINE_ECHO_COUNT).l,d4
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d4
        bhi.s   .none
        tst.w   d4
        beq.s   .found
        subq.w  #1,d4
        lea     (TIMELINE_ECHO_ORDER).l,a2
.scan_order:
        moveq   #0,d5
        move.b  (a2)+,d5
        cmp.w   d3,d5
        beq.s   .next_candidate
        dbf     d4,.scan_order
.found:
        move.w  d3,d0
        bra.s   .done
.next_candidate:
        addq.w  #1,d3
        bra.s   .candidate
.none:
        moveq   #-1,d0
.done:
        movem.l (a7)+,d3-d5/a2
        rts

; GameplayClearReplacementSelection
; Explicitly clears a pending selection. At six echoes replacement-required
; remains visible, so commit still cannot silently discard a replay.
; Out: d0.w=0. Clobbers d0-d2/a0-a1.
GameplayClearReplacementSelection:
        bsr.w   GameplayGetEchoCap
        move.w  d0,d2
        move.b  #TIMELINE_NO_SELECTION,(TIMELINE_SELECTED_ORDINAL).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C1,d0               ; keep initialized/stable/full
        moveq   #0,d1
        move.b  (TIMELINE_ECHO_COUNT).l,d1
        cmp.w   d2,d1
        bne.s   .store
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED,d0
.store:
        move.b  d0,(TIMELINE_FLAGS).l
        clr.w   (TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_OK,d0
        move.w  d0,(GAMEPLAY_RESULT).l
        rts

; GameplaySelectReplacement
; In: d0.w=echo ordinal (oldest=0). Selection is explicit and protected
; descriptors are rejected. Out: gameplay result code.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplaySelectReplacement:
        movem.l d3-d4/a2,-(a7)
        move.w  d0,d3
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_INITIALIZED,d0
        beq.w   .not_initialized
        moveq   #0,d4
        move.b  (TIMELINE_ECHO_COUNT).l,d4
        bsr.w   GameplayGetEchoCap
        cmp.w   d0,d4
        bne.w   .invalid
        cmp.w   d4,d3
        bhs.s   .invalid
        lea     (TIMELINE_ECHO_ORDER).l,a2
        moveq   #0,d0
        move.b  (a2,d3.w),d0
        cmpi.w  #REPLAY_BANK_COUNT,d0
        bhs.w   .invalid
        bsr.w   GameplayGetBankDescriptor
        move.b  (REPLAY_DESCRIPTOR_FLAGS,a0),d0
        andi.b  #REPLAY_FLAG_PROTECTED,d0
        bne.s   .protected

        move.b  d3,(TIMELINE_SELECTED_ORDINAL).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C3,d0
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_SELECTION_VALID|TIMELINE_FLAG_SELECTION_EXPLICIT,d0
        move.b  d0,(TIMELINE_FLAGS).l
        clr.w   (TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.protected:
        move.b  #TIMELINE_NO_SELECTION,(TIMELINE_SELECTED_ORDINAL).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C3,d0
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_COMMIT_BLOCKED,d0
        move.b  d0,(TIMELINE_FLAGS).l
        move.w  #GAMEPLAY_RESULT_PROTECTED,(TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_PROTECTED,d0
        bra.s   .done
.invalid:
        move.w  #GAMEPLAY_RESULT_INVALID_SELECTION,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_SELECTION,d0
        bra.s   .done
.not_initialized:
        move.w  #GAMEPLAY_RESULT_NOT_INITIALIZED,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_NOT_INITIALIZED,d0
.done:
        move.w  d0,(GAMEPLAY_RESULT).l
        movem.l (a7)+,d3-d4/a2
        rts

; GameplaySelectOldestReplacement
; Selects the first unprotected echo in oldest-to-newest order. Out: result.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplaySelectOldestReplacement:
        movem.l d3-d5/a2,-(a7)
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_INITIALIZED,d0
        beq.w   .not_initialized
        moveq   #0,d5
        move.b  (TIMELINE_ECHO_COUNT).l,d5
        bsr.w   GameplayGetEchoCap
        cmp.w   d0,d5
        bne.w   .invalid
        subq.w  #1,d5
        moveq   #0,d3
        lea     (TIMELINE_ECHO_ORDER).l,a2
.scan:
        moveq   #0,d0
        move.b  (a2,d3.w),d0
        cmpi.w  #REPLAY_BANK_COUNT,d0
        bhs.w   .invalid
        bsr.w   GameplayGetBankDescriptor
        move.b  (REPLAY_DESCRIPTOR_FLAGS,a0),d4
        andi.b  #REPLAY_FLAG_PROTECTED,d4
        beq.s   .selected
        addq.w  #1,d3
        dbf     d5,.scan

        move.b  #TIMELINE_NO_SELECTION,(TIMELINE_SELECTED_ORDINAL).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C3,d0
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_COMMIT_BLOCKED,d0
        move.b  d0,(TIMELINE_FLAGS).l
        move.w  #GAMEPLAY_RESULT_PROTECTED,(TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_PROTECTED,d0
        bra.s   .done
.selected:
        move.b  d3,(TIMELINE_SELECTED_ORDINAL).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C3,d0
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_SELECTION_VALID|TIMELINE_FLAG_SELECTION_OLDEST,d0
        move.b  d0,(TIMELINE_FLAGS).l
        clr.w   (TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.invalid:
        move.w  #GAMEPLAY_RESULT_INVALID_SELECTION,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_SELECTION,d0
        bra.s   .done
.not_initialized:
        move.w  #GAMEPLAY_RESULT_NOT_INITIALIZED,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_NOT_INITIALIZED,d0
.done:
        move.w  d0,(GAMEPLAY_RESULT).l
        movem.l (a7)+,d3-d5/a2
        rts

; GameplaySetEchoProtected
; In: d0.w=echo ordinal, d1.b=zero clear/nonzero protect. Out: result.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplaySetEchoProtected:
        movem.l d3-d5/a2,-(a7)
        move.w  d0,d3
        move.b  d1,d4
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_INITIALIZED,d0
        beq.s   .not_initialized
        moveq   #0,d5
        move.b  (TIMELINE_ECHO_COUNT).l,d5
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d5
        bhi.s   .invalid
        cmp.w   d5,d3
        bhs.s   .invalid
        lea     (TIMELINE_ECHO_ORDER).l,a2
        moveq   #0,d0
        move.b  (a2,d3.w),d0
        cmpi.w  #REPLAY_BANK_COUNT,d0
        bhs.s   .invalid
        bsr.w   GameplayGetBankDescriptor
        tst.b   d4
        beq.s   .clear
        ori.b   #REPLAY_FLAG_PROTECTED,(REPLAY_DESCRIPTOR_FLAGS,a0)
        moveq   #0,d0
        move.b  (TIMELINE_SELECTED_ORDINAL).l,d0
        cmp.w   d3,d0
        bne.s   .ok
        bsr.w   GameplayClearReplacementSelection
        bra.s   .ok
.clear:
        andi.b  #$F7,(REPLAY_DESCRIPTOR_FLAGS,a0)
.ok:
        clr.w   (TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done
.invalid:
        move.w  #GAMEPLAY_RESULT_INVALID_SELECTION,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_SELECTION,d0
        bra.s   .done
.not_initialized:
        move.w  #GAMEPLAY_RESULT_NOT_INITIALIZED,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_NOT_INITIALIZED,d0
.done:
        move.w  d0,(GAMEPLAY_RESULT).l
        movem.l (a7)+,d3-d5/a2
        rts

; GameplayCommitAndReset
; In: d0.w=commit reason. Finalizes a partial/full recording, rotates banks
; without copying, invokes the real EngineReset, and re-instantiates actors.
; At six echoes a prior explicit/oldest selection is mandatory.
; Out: gameplay result. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCommitAndReset:
        movem.l d3-d7/a2-a4,-(a7)
        move.w  d0,d3
        move.w  (GAMEPLAY_FLAGS).l,d0
        andi.w  #GAMEPLAY_FLAG_INITIALIZED,d0
        beq.w   .not_initialized

        moveq   #0,d4
        move.b  (TIMELINE_ECHO_COUNT).l,d4
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d4
        bhi.w   .invalid_state
        bsr.w   GameplayGetEchoCap
        movea.l d0,a4
        cmp.w   d0,d4
        bhi.w   .invalid_state
        blo.s   .selection_ready
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #TIMELINE_FLAG_SELECTION_VALID,d0
        beq.w   .replacement_required
        moveq   #0,d5
        move.b  (TIMELINE_SELECTED_ORDINAL).l,d5
        cmp.w   d4,d5
        bhs.w   .replacement_required
        lea     (TIMELINE_ECHO_ORDER).l,a2
        moveq   #0,d0
        move.b  (a2,d5.w),d0
        cmpi.w  #REPLAY_BANK_COUNT,d0
        bhs.w   .invalid_state
        bsr.w   GameplayGetBankDescriptor
        move.b  (REPLAY_DESCRIPTOR_FLAGS,a0),d0
        andi.b  #REPLAY_FLAG_PROTECTED,d0
        bne.w   .protected
.selection_ready:
        bsr.w   GameplayFinalizeCurrentBank
        tst.w   d0
        bne.w   .done

        moveq   #0,d6
        move.b  (TIMELINE_CURRENT_BANK).l,d6
        move.l  a4,d0
        cmp.w   d0,d4
        bhs.s   .replace

; Append the completed current replay as newest, then select the lowest bank
; not present in the updated echo order.
        lea     (TIMELINE_ECHO_ORDER).l,a2
        move.b  d6,(a2,d4.w)
        addq.w  #1,d4
        move.b  d4,(TIMELINE_ECHO_COUNT).l
        bsr.w   GameplayFindFreeBank
        cmpi.w  #$FFFF,d0
        beq.w   .invalid_state
        move.w  d0,d7
        bra.s   .new_current

.replace:
; Remove the selected ordinal, retain chronological order, append current as
; newest, and reuse the removed physical bank for the next current attempt.
        lea     (TIMELINE_ECHO_ORDER).l,a2
        moveq   #0,d7
        move.b  (a2,d5.w),d7
        move.w  d4,d0
        subq.w  #1,d0
        cmp.w   d0,d5
        beq.s   .append_current
        lea     (a2,d5.w),a3
        move.w  d4,d0
        subq.w  #2,d0
        sub.w   d5,d0
.shift:
        move.b  1(a3),(a3)+
        dbf     d0,.shift
.append_current:
        move.w  d4,d0
        subq.w  #1,d0
        move.b  d6,(a2,d0.w)

.new_current:
        move.w  d7,d0
        bsr.w   GameplayClearCurrentBank
        move.b  #TIMELINE_NO_SELECTION,(TIMELINE_SELECTED_ORDINAL).l
        moveq   #TIMELINE_FLAG_INITIALIZED,d0
        move.l  a4,d1
        cmp.w   d1,d4
        bne.s   .timeline_flags_ready
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED,d0
.timeline_flags_ready:
        move.b  d0,(TIMELINE_FLAGS).l
        clr.w   (TIMELINE_PENDING_REASON).l
        clr.w   (TIMELINE_ATTEMPT_LENGTH).l
        clr.w   (TIMELINE_LAST_RESULT).l
        move.w  d3,(GAMEPLAY_LAST_COMMIT_REASON).l
        bsr.w   GameplayMirrorReplacementFlags

; Reconstruct the same canonical ordinary state as EngineReset without its
; one-actor publication. Replay RAM above remains untouched; actor roles and
; the projected B01 device are installed before the sole reset checkpoint.
        bsr.w   EngineResetCanonicalState
        tst.w   d0
        bne.s   .invalid_state
        bsr.w   GameplayReinstantiateActors
        tst.w   d0
        bne.s   .done
        moveq   #GAMEPLAY_RESULT_OK,d0
        bra.s   .done

.replacement_required:
        move.w  d3,(TIMELINE_PENDING_REASON).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C3,d0
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_COMMIT_BLOCKED,d0
        move.b  d0,(TIMELINE_FLAGS).l
        move.w  #GAMEPLAY_RESULT_REPLACEMENT_REQUIRED,(TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_REPLACEMENT_REQUIRED,d0
        bra.s   .done
.protected:
        move.w  d3,(TIMELINE_PENDING_REASON).l
        move.b  #TIMELINE_NO_SELECTION,(TIMELINE_SELECTED_ORDINAL).l
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #$C3,d0
        ori.b   #TIMELINE_FLAG_REPLACEMENT_REQUIRED|TIMELINE_FLAG_COMMIT_BLOCKED,d0
        move.b  d0,(TIMELINE_FLAGS).l
        move.w  #GAMEPLAY_RESULT_PROTECTED,(TIMELINE_LAST_RESULT).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #GAMEPLAY_RESULT_PROTECTED,d0
        bra.s   .done
.invalid_state:
        move.w  #GAMEPLAY_RESULT_INVALID_STATE,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_INVALID_STATE,d0
        bra.s   .done
.not_initialized:
        move.w  #GAMEPLAY_RESULT_NOT_INITIALIZED,(TIMELINE_LAST_RESULT).l
        moveq   #GAMEPLAY_RESULT_NOT_INITIALIZED,d0
.done:
        move.w  d0,(GAMEPLAY_RESULT).l
        movem.l (a7)+,d3-d7/a2-a4
        rts
