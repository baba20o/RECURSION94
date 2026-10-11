; Common fatal sink for every exception and interrupt not explicitly owned.
; It is safe before VDP initialization and never returns into damaged state.

SafeException:
        move.w  #$2700,sr
        moveq   #0,d0
        cmpi.l  #DEBUG_MAGIC_R94D,(DEBUG_MAGIC).l
        bne.s   .publish_fatal
        cmpi.w  #DEBUG_ABI_VERSION_VALUE,(DEBUG_ABI_VERSION).l
        bne.s   .publish_fatal
        cmpi.w  #DEBUG_ABI_SIZE_VALUE,(DEBUG_ABI_SIZE).l
        bne.s   .publish_fatal
        cmpi.w  #BOOT_STAGE_VIDEO_READY,(DEBUG_BOOT_STAGE).l
        blo.s   .publish_fatal
        moveq   #1,d0

.publish_fatal:
        move.l  #DEBUG_MAGIC_R94D,(DEBUG_MAGIC).l
        move.w  #DEBUG_ABI_VERSION_VALUE,(DEBUG_ABI_VERSION).l
        move.w  #DEBUG_ABI_SIZE_VALUE,(DEBUG_ABI_SIZE).l
        move.w  #FATAL_UNEXPECTED_EXCEPTION,(DEBUG_FATAL_CODE).l
        tst.b   d0
        beq.s   SafeExceptionHalt
        bsr.w   VdpShowFatalScreen

SafeExceptionHalt:
        stop    #$2700
        bra.s   SafeExceptionHalt
