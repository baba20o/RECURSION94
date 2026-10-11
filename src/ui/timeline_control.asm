; Standard three-button timeline/meta controls.
; Start opens/closes a paused timeline. Left/Right choose a chronological echo
; and C commits the current partial attempt. Every open/Start-held frame is
; consumed before GameplayStep, so menu input is never recorded.

; TimelineHandleMetaInput
; Out: d0.w=0 when gameplay should step, 1 when meta state consumed the frame.
; Invalid core results enter the normal fatal path.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
TimelineHandleMetaInput:
; A commit-triggering C press belongs only to the meta interface. After reset,
; keep consuming presentation frames until C is observed released so it cannot
; become an actor-local dash edge or a byte in the new replay.
        move.w  (VOCAB_TIMELINE_UI_FLAGS).l,d0
        andi.w  #VOCAB_TIMELINE_UI_FLAG_WAIT_RELEASE,d0
        beq.s   .state_ready
        moveq   #0,d0
        move.b  (INPUT_HELD).l,d0
        andi.b  #INPUT_C,d0
        bne.w   .consumed
        andi.w  #$FFFC,(VOCAB_TIMELINE_UI_FLAGS).l
        bsr.w   GameplayMirrorReplacementFlags
        bra.w   .consumed

.state_ready:
; A timeout/death that reaches the authored echo cap is a deliberate terminal
; replacement pause. Validate its pending reason, force the controller UI open,
; and allow only bounded selection/commit input; no gameplay step is consumed.
        cmpi.w  #ENGINE_STATE_ACTIVE,(ENGINE_STATE).l
        beq.w   .active
        cmpi.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        bne.w   .fatal
        move.w  (TIMELINE_PENDING_REASON).l,d0
        cmpi.w  #GAMEPLAY_COMMIT_TIMEOUT,d0
        beq.s   .terminal_timeout
        cmpi.w  #GAMEPLAY_COMMIT_DESTROYED,d0
        beq.s   .terminal_destroyed
; PROGRAM_STABLE is the only terminal state with no pending reset.
        tst.w   d0
        bne.w   .fatal
        cmpi.w  #GAMEPLAY_TERMINAL_PROGRAM_STABLE,(ENGINE_TERMINAL_REASON).l
        beq.w   .terminal_consumed
        bra.w   .fatal
.terminal_destroyed:
        cmpi.w  #GAMEPLAY_TERMINAL_CURRENT_DESTROYED,(ENGINE_TERMINAL_REASON).l
        bne.w   .fatal
        bra.s   .terminal_pending
.terminal_timeout:
        cmpi.w  #ENGINE_TERMINAL_TIMEOUT,(ENGINE_TERMINAL_REASON).l
        bne.w   .fatal
.terminal_pending:
        moveq   #0,d0
        move.b  (TIMELINE_FLAGS).l,d0
        andi.b  #TIMELINE_FLAG_REPLACEMENT_REQUIRED,d0
        beq.w   .fatal
        ori.w   #VOCAB_TIMELINE_UI_FLAG_OPEN,(VOCAB_TIMELINE_UI_FLAGS).l
        bsr.w   GameplayMirrorReplacementFlags
        moveq   #0,d0
        move.b  (TIMELINE_ECHO_COUNT).l,d0
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d0
        bhi.w   .fatal
        moveq   #0,d1
        move.b  (VOCAB_TIMELINE_UI_CURSOR).l,d1
        cmp.w   d0,d1
        blo.s   .terminal_cursor_ready
        clr.b   (VOCAB_TIMELINE_UI_CURSOR).l
.terminal_cursor_ready:
        bra.w   .pending_open

.terminal_consumed:
; Solved and corrupt/error terminal states never admit timeline mutation.
        andi.w  #$FFFE,(VOCAB_TIMELINE_UI_FLAGS).l
        bsr.w   GameplayMirrorReplacementFlags
        bra.w   .consumed

.active:
; Derive the Start edge in projected deterministic state.
        moveq   #0,d2
        move.b  (INPUT_START_HELD).l,d2
        moveq   #0,d1
        move.b  (VOCAB_TIMELINE_UI_START_PREVIOUS).l,d1
        move.b  d2,(VOCAB_TIMELINE_UI_START_PREVIOUS).l
        not.b   d1
        and.b   d2,d1

        move.w  (VOCAB_TIMELINE_UI_FLAGS).l,d0
        andi.w  #VOCAB_TIMELINE_UI_FLAG_OPEN,d0
        bne.w   .open

; Preserve the M4 Start+C chord: Start opens on its edge; a subsequent C edge
; commits while open. If both arrive in one sample, commit immediately.
        tst.b   d2
        beq.w   .not_consumed
        moveq   #0,d0
        move.b  (INPUT_PRESSED).l,d0
        andi.b  #INPUT_C,d0
        bne.w   .commit
        tst.b   d1
        beq.w   .consumed

        ori.w   #VOCAB_TIMELINE_UI_FLAG_OPEN,(VOCAB_TIMELINE_UI_FLAGS).l
        ori.w   #GAMEPLAY_FLAG_REPLACEMENT_VISIBLE,(GAMEPLAY_FLAGS).l
        moveq   #0,d0
        move.b  (TIMELINE_SELECTED_ORDINAL).l,d0
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d0
        blo.s   .store_open_cursor
        moveq   #0,d0
.store_open_cursor:
        move.b  d0,(VOCAB_TIMELINE_UI_CURSOR).l
        bra.w   .consumed

.open:
; A new Start edge closes the interface without advancing the simulation.
        tst.b   d1
        bne.w   .close

.pending_open:
        moveq   #0,d0
        move.b  (INPUT_PRESSED).l,d0
        move.w  d0,d1
        andi.w  #INPUT_LEFT|INPUT_RIGHT,d1
        beq.s   .open_action
        moveq   #0,d2
        move.b  (TIMELINE_ECHO_COUNT).l,d2
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d2
        bls.s   .echo_count_ready
        moveq   #GAMEPLAY_ECHO_LIMIT,d2
.echo_count_ready:
        tst.w   d2
        beq.s   .open_action
        moveq   #0,d1
        move.b  (VOCAB_TIMELINE_UI_CURSOR).l,d1
        cmp.w   d2,d1
        blo.s   .cursor_ready
        moveq   #0,d1
.cursor_ready:
        btst    #2,d0
        beq.s   .cursor_right
        tst.w   d1
        bne.s   .cursor_left_decrement
        move.w  d2,d1
.cursor_left_decrement:
        subq.w  #1,d1
        bra.s   .cursor_store
.cursor_right:
        addq.w  #1,d1
        cmp.w   d2,d1
        blo.s   .cursor_store
        moveq   #0,d1
.cursor_store:
        move.b  d1,(VOCAB_TIMELINE_UI_CURSOR).l

.open_action:
        moveq   #0,d0
        move.b  (INPUT_PRESSED).l,d0
        andi.b  #INPUT_C,d0
        beq.w   .consumed

; At the authored echo cap C first confirms an explicit replacement.
        moveq   #0,d1
        move.b  (TIMELINE_ECHO_COUNT).l,d1
        bsr.w   GameplayGetEchoCap
        cmp.w   d0,d1
        bne.s   .commit
        moveq   #0,d0
        move.b  (VOCAB_TIMELINE_UI_CURSOR).l,d0
        bsr.w   GameplaySelectReplacement
        tst.w   d0
        beq.s   .commit
        cmpi.w  #GAMEPLAY_RESULT_PROTECTED,d0
        beq.w   .consumed
        cmpi.w  #GAMEPLAY_RESULT_INVALID_SELECTION,d0
        beq.w   .consumed
        jmp     (SafeException).l

.commit:
        moveq   #GAMEPLAY_COMMIT_MANUAL,d0
; Automatic timeout/death replacement retains its exact terminal reason.
; A manual at-cap commit likewise reuses the pending manual reason.
        move.w  (TIMELINE_PENDING_REASON).l,d1
        beq.s   .commit_reason_ready
        move.w  d1,d0
.commit_reason_ready:
        bsr.w   GameplayCommitAndReset
        cmpi.w  #GAMEPLAY_RESULT_OK,d0
        beq.s   .commit_ready
        cmpi.w  #GAMEPLAY_RESULT_REPLACEMENT_REQUIRED,d0
        beq.w   .consumed
        cmpi.w  #GAMEPLAY_RESULT_PROTECTED,d0
        beq.w   .consumed
        jmp     (SafeException).l
.commit_ready:
        andi.w  #$FFFC,(VOCAB_TIMELINE_UI_FLAGS).l
        ori.w   #VOCAB_TIMELINE_UI_FLAG_WAIT_RELEASE,(VOCAB_TIMELINE_UI_FLAGS).l
        bsr.w   GameplayMirrorReplacementFlags
        bra.s   .consumed

.close:
        andi.w  #$FFFC,(VOCAB_TIMELINE_UI_FLAGS).l
        bsr.w   GameplayMirrorReplacementFlags
.consumed:
        moveq   #1,d0
        rts
.not_consumed:
        moveq   #0,d0
        rts
.fatal:
        jmp     (SafeException).l
