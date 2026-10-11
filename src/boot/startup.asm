; Reset owns initialization. Interrupts remain masked and the display remains
; disabled until all RAM and video state is deterministic.

ResetEntry:
        move.w  #$2700,sr
        move.l  #INITIAL_STACK,a7

; Clear all 64 KiB without using the stack. The reset entry has no stack frame.
        move.l  #WORK_RAM_BASE,a0
        moveq   #0,d0
        move.w  #WORK_RAM_LONGS-1,d1
.clear_work_ram:
        move.l  d0,(a0)+
        dbf     d1,.clear_work_ram
        move.l  #INITIAL_STACK,a7

; Publish the minimum stable debug ABI before calling any hardware routine.
        move.l  #DEBUG_MAGIC_R94D,(DEBUG_MAGIC).l
        move.w  #DEBUG_ABI_VERSION_VALUE,(DEBUG_ABI_VERSION).l
        move.w  #DEBUG_ABI_SIZE_VALUE,(DEBUG_ABI_SIZE).l
        move.w  #BOOT_STAGE_RAM_READY,(DEBUG_BOOT_STAGE).l

; TMSS-equipped hardware exposes a nonzero low version nibble.
        moveq   #0,d0
        move.b  (HW_VERSION).l,d0
        andi.b  #$0F,d0
        beq.s   .tmss_complete
        move.l  #$53454741,(TMSS_REGISTER).l
.tmss_complete:

        bsr.w   Z80EnterSafeState
        move.w  #BOOT_STAGE_Z80_SAFE,(DEBUG_BOOT_STAGE).l

        bsr.w   ControllerInitialize
        bsr.w   VdpInitialize
        bsr.w   VdpLoadDiagnostic
        bsr.w   DmaInitialize
        bsr.w   AudioUploadAndStart
        move.w  #BOOT_STAGE_VIDEO_READY,(DEBUG_BOOT_STAGE).l
        bsr.w   GameplayInitialize
        tst.w   d0
        bne.w   SafeException
        jsr     (GameplayRendererInitialize).l
        tst.w   d0
        bne.w   SafeException

; Clear a possibly pending VDP interrupt, then enable display/V-int. Enabling
; register 1 can expose an already-asserted level-6 request even after another
; status read. Keep the boot stage at VIDEO_READY and admit one interrupt; the
; handler's pre-running path acknowledges it without polling input, advancing
; counters, publishing a token, or touching DMA. Re-mask immediately on return,
; declare RUNNING, then enter through a second STOP so the first gameplay token
; always comes from a fresh full VBlank handler.
        move.w  (VDP_CONTROL).l,d0
        move.w  #VDP_REGISTER_DISPLAY_ON,(VDP_CONTROL).l
        move.w  (VDP_CONTROL).l,d0
        stop    #$2500
        move.w  #$2700,sr
        move.w  #BOOT_STAGE_RUNNING,(DEBUG_BOOT_STAGE).l
        bra.w   MainLoopWaitForVBlank

MainLoop:
; Mask VBlank only while testing/decrementing the shared token. STOP lowers the
; mask and sleeps atomically, so an interrupt can never be lost between the
; empty-token test and sleep.
        move.w  #$2700,sr
        tst.w   (DEBUG_FRAME_TOKEN).l
        beq.w   MainLoopWaitForVBlank
        subq.w  #1,(DEBUG_FRAME_TOKEN).l
; Every successful commit/reset rotates the current replay bank. Preserve that
; epoch across the authoritative step so reset-only VBlanks can be coalesced
; after the renderer has prepared the reset presentation.
        move.b  (TIMELINE_CURRENT_BANK).l,d3
        move.w  #$2500,sr
        bsr.w   M2DebugFatalFixture
        bsr.w   AudioPostBootSmokeOnce
        jsr     (TimelineHandleMetaInput).l
        tst.w   d0
        bne.s   MainLoopGameplayStepComplete
        moveq   #0,d0
        move.b  (INPUT_HELD).l,d0
MainLoopGameplayStepCall:
        bsr.w   GameplayStep
MainLoopPostGameplayStepCall:
        cmpi.w  #GAMEPLAY_RESULT_TIMEOUT,d0
        beq.s   MainLoopCommitTimeout
        cmpi.w  #GAMEPLAY_RESULT_CURRENT_DESTROYED,d0
        beq.s   MainLoopCommitDestroyed
        tst.w   d0
        beq.s   MainLoopGameplayStepComplete
        cmpi.w  #GAMEPLAY_RESULT_PROGRAM_STABLE,d0
        beq.s   MainLoopGameplayStepComplete
; Every other gameplay result is a contract/runtime fault, never a renderable
; gameplay state.
        jmp     (SafeException).l
MainLoopCommitDestroyed:
        moveq   #GAMEPLAY_COMMIT_DESTROYED,d0
        bra.s   MainLoopCommitResetCall
MainLoopCommitTimeout:
        moveq   #GAMEPLAY_COMMIT_TIMEOUT,d0
MainLoopCommitResetCall:
        bsr.w   GameplayCommitAndReset
MainLoopPostCommitReset:
        tst.w   d0
        beq.s   MainLoopGameplayStepComplete
; At the authored echo cap, timeout/death deliberately pauses in the terminal
; replacement UI. It is not an engine fault: no further authoritative step is
; consumed until the player selects an echo and retries the pending commit.
        cmpi.w  #GAMEPLAY_RESULT_REPLACEMENT_REQUIRED,d0
        beq.s   MainLoopGameplayStepComplete
        cmpi.w  #GAMEPLAY_RESULT_PROTECTED,d0
        beq.s   MainLoopGameplayStepComplete
        jmp     (SafeException).l
MainLoopGameplayStepComplete:
        bsr.w   AudioPostEchoLayersIfChanged
MainLoopPostEngineStep:
MainLoopPostGameplayStep:
        jsr     (GameplayRendererBuildAndQueue).l
        cmp.b   (TIMELINE_CURRENT_BANK).l,d3
        bne.s   MainLoopResetTokenCoalesce
        bra.w   MainLoop
MainLoopResetTokenCoalesce:
; Reset work may span one or more VBlanks. Discard those accumulated tokens
; only after all main-side reset work is complete, then let STOP atomically
; admit the first fresh VBlank for the new attempt.
        move.w  #$2700,sr
MainLoopResetTokenMasked:
        clr.w   (DEBUG_FRAME_TOKEN).l
MainLoopResetTokensCoalesced:
        bra.w   MainLoopWaitForVBlank
MainLoopWaitForVBlank:
        stop    #$2500
        bra.w   MainLoop
