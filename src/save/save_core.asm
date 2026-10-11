; Mainline-only save journal. No routine is callable from an interrupt.

; SaveInitialize
; Loads the newest valid journal generation or constructs canonical defaults.
; Out: d0.w=SAVE_RESULT_OK or SAVE_RESULT_DEFAULTS.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
SaveInitialize:
        lea     (SAVE_STAGING_BASE).l,a0
        moveq   #0,d0
        move.w  #((SAVE_RAM_END-SAVE_STAGING_BASE)/4)-1,d1
.clear:
        move.l  d0,(a0)+
        dbf     d1,.clear
        move.l  #SAVE_CONTROL_MAGIC_VALUE,(SAVE_CONTROL_MAGIC).l
        move.w  #SAVE_CONTROL_VERSION_VALUE,(SAVE_CONTROL_VERSION).l
        move.w  #SAVE_CONTROL_BYTES_VALUE,(SAVE_CONTROL_BYTES).l
        move.w  #SAVE_STATUS_INITIALIZED,(SAVE_CONTROL_STATUS).l
        move.b  #SAVE_SLOT_NONE,(SAVE_CONTROL_ACTIVE_SLOT).l
        bsr.w   SaveLoad
        rts

; SaveLoad
; Validates both physical records and selects the newer wrapping generation.
; Out: d0.w=OK or DEFAULTS. Clobbers d0-d2/a0-a1.
SaveLoad:
        movem.l d3-d7/a2-a4,-(a7)
        moveq   #0,d7                   ; at least one valid record
        move.b  #SAVE_SLOT_NONE,(SAVE_CONTROL_ACTIVE_SLOT).l
        clr.l   (SAVE_CONTROL_GENERATION).l
        clr.b   (SAVE_CONTROL_DIRTY).l

        lea     (SAVE_SRAM_SLOT_A).l,a0
        lea     (SAVE_STAGING_BASE).l,a1
        bsr.w   SaveReadOddLane
        lea     (SAVE_STAGING_BASE).l,a0
        bsr.w   SaveValidateRecord
        tst.w   d0
        bne.s   .slot_b
        lea     (SAVE_STAGING_BASE).l,a0
        lea     (SAVE_ACTIVE_BASE).l,a1
        bsr.w   SaveCopyRamRecord
        moveq   #1,d7
        clr.b   (SAVE_CONTROL_ACTIVE_SLOT).l

.slot_b:
        lea     (SAVE_SRAM_SLOT_B).l,a0
        lea     (SAVE_STAGING_BASE).l,a1
        bsr.w   SaveReadOddLane
        lea     (SAVE_STAGING_BASE).l,a0
        bsr.w   SaveValidateRecord
        tst.w   d0
        bne.s   .selection_complete
        tst.w   d7
        beq.s   .select_b
        move.l  (SAVE_STAGING_BASE+SAVE_RECORD_GENERATION).l,d0
        sub.l   (SAVE_ACTIVE_BASE+SAVE_RECORD_GENERATION).l,d0
        beq.s   .selection_complete
        cmpi.l  #$80000000,d0
        bhs.s   .selection_complete
.select_b:
        lea     (SAVE_STAGING_BASE).l,a0
        lea     (SAVE_ACTIVE_BASE).l,a1
        bsr.w   SaveCopyRamRecord
        moveq   #1,d7
        move.b  #1,(SAVE_CONTROL_ACTIVE_SLOT).l

.selection_complete:
        tst.w   d7
        beq.s   .defaults
        move.l  (SAVE_ACTIVE_BASE+SAVE_RECORD_GENERATION).l,d0
        move.l  d0,(SAVE_CONTROL_GENERATION).l
        move.l  (SAVE_ACTIVE_BASE+SAVE_RECORD_CRC).l,d0
        move.l  d0,(SAVE_CONTROL_LAST_CRC).l
        move.w  #SAVE_STATUS_INITIALIZED+SAVE_STATUS_LOADED,(SAVE_CONTROL_STATUS).l
        clr.w   (SAVE_CONTROL_LAST_ERROR).l
        clr.w   (SAVE_CONTROL_RESULT).l
        moveq   #SAVE_RESULT_OK,d0
        bra.s   .done

.defaults:
        bsr.w   SaveBuildDefaults
        move.w  #SAVE_STATUS_INITIALIZED+SAVE_STATUS_RECOVERED_DEFAULTS,(SAVE_CONTROL_STATUS).l
        clr.w   (SAVE_CONTROL_LAST_ERROR).l
        move.w  #SAVE_RESULT_DEFAULTS,(SAVE_CONTROL_RESULT).l
        moveq   #SAVE_RESULT_DEFAULTS,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; SaveBuildDefaults
; Constructs a valid in-RAM generation-zero record without writing SRAM.
; Clobbers d0-d2/a0-a1.
SaveBuildDefaults:
        movem.l d3-d4/a2,-(a7)
        lea     (SAVE_ACTIVE_BASE).l,a0
        moveq   #0,d0
        moveq   #(SAVE_RECORD_BYTES/4)-1,d1
.clear:
        move.l  d0,(a0)+
        dbf     d1,.clear
        move.w  #SAVE_FORMAT_VERSION,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_VERSION).l
        move.b  #1,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_UNLOCKED_SECTOR).l
        move.b  #SAVE_DEFAULT_SETTINGS,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_SETTINGS).l

        lea     (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_METRICS).l,a2
        moveq   #SAVE_ENCOUNTER_COUNT-1,d4
.metric:
        clr.w   (SAVE_METRIC_CLEARED,a2)
        move.w  #$FFFF,(SAVE_METRIC_ATTEMPTS,a2)
        move.b  #$FF,(SAVE_METRIC_ECHOES,a2)
        clr.b   (SAVE_METRIC_RESERVED,a2)
        clr.w   (SAVE_METRIC_TIME_REMAINING,a2)
        adda.w  #SAVE_METRIC_BYTES,a2
        dbf     d4,.metric

        moveq   #0,d0
        bsr.w   SaveFinalizeActiveRecord
        clr.l   (SAVE_CONTROL_GENERATION).l
        move.b  #SAVE_SLOT_NONE,(SAVE_CONTROL_ACTIVE_SLOT).l
        clr.b   (SAVE_CONTROL_DIRTY).l
        movem.l (a7)+,d3-d4/a2
        rts

; SaveFinalizeActiveRecord
; In: d0.l=generation. Canonicalizes header/reserved bytes and CRC.
; Clobbers d0-d2/a0-a1.
SaveFinalizeActiveRecord:
        move.l  #SAVE_MAGIC_VALUE,(SAVE_ACTIVE_BASE+SAVE_RECORD_MAGIC).l
        move.w  #SAVE_FORMAT_VERSION,(SAVE_ACTIVE_BASE+SAVE_RECORD_VERSION).l
        move.w  #SAVE_RECORD_BYTES,(SAVE_ACTIVE_BASE+SAVE_RECORD_SIZE).l
        move.l  d0,(SAVE_ACTIVE_BASE+SAVE_RECORD_GENERATION).l
        move.w  #SAVE_PAYLOAD_BYTES,(SAVE_ACTIVE_BASE+SAVE_RECORD_PAYLOAD_SIZE).l
        move.w  #SAVE_RECORD_FLAG_VALID,(SAVE_ACTIVE_BASE+SAVE_RECORD_FLAGS).l

        lea     (SAVE_ACTIVE_BASE+SAVE_RECORD_RESERVED).l,a0
        moveq   #0,d0
        moveq   #3,d1
.header_reserved:
        move.l  d0,(a0)+
        dbf     d1,.header_reserved
        lea     (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_USED_END).l,a0
        moveq   #((SAVE_RECORD_CRC-SAVE_PAYLOAD_USED_END)/4)-1,d1
.payload_reserved:
        move.l  d0,(a0)+
        dbf     d1,.payload_reserved

        lea     (SAVE_ACTIVE_BASE).l,a0
        move.w  #SAVE_CRC_BYTES,d0
        bsr.w   Crc32IsoHdlc
        move.l  d0,(SAVE_ACTIVE_BASE+SAVE_RECORD_CRC).l
        move.l  d0,(SAVE_CONTROL_LAST_CRC).l
        move.l  #SAVE_COMMIT_MARKER,(SAVE_ACTIVE_BASE+SAVE_RECORD_COMMIT).l
        rts

; SaveValidateRecord
; In: a0=256-byte RAM record. Out: d0.w=0 valid, 1 invalid.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
SaveValidateRecord:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        cmpi.l  #SAVE_MAGIC_VALUE,(SAVE_RECORD_MAGIC,a2)
        bne.w   .invalid
        cmpi.w  #SAVE_FORMAT_VERSION,(SAVE_RECORD_VERSION,a2)
        bne.w   .invalid
        cmpi.w  #SAVE_RECORD_BYTES,(SAVE_RECORD_SIZE,a2)
        bne.w   .invalid
        cmpi.w  #SAVE_PAYLOAD_BYTES,(SAVE_RECORD_PAYLOAD_SIZE,a2)
        bne.w   .invalid
        cmpi.w  #SAVE_RECORD_FLAG_VALID,(SAVE_RECORD_FLAGS,a2)
        bne.w   .invalid
        cmpi.l  #SAVE_COMMIT_MARKER,(SAVE_RECORD_COMMIT,a2)
        bne.w   .invalid

        lea     (SAVE_RECORD_RESERVED,a2),a0
        moveq   #15,d1
.header_reserved:
        tst.b   (a0)+
        bne.w   .invalid
        dbf     d1,.header_reserved

        cmpi.w  #SAVE_FORMAT_VERSION,(SAVE_PAYLOAD_VERSION,a2)
        bne.w   .invalid
        moveq   #0,d0
        move.b  (SAVE_PAYLOAD_UNLOCKED_SECTOR,a2),d0
        cmpi.w  #1,d0
        blo.w   .invalid
        cmpi.w  #5,d0
        bhi.w   .invalid
        moveq   #0,d0
        move.b  (SAVE_PAYLOAD_SETTINGS,a2),d0
        cmpi.w  #SAVE_SETTINGS_MASK,d0
        bhi.w   .invalid
        move.l  (SAVE_PAYLOAD_COMPLETION_BITS,a2),d4
        cmpi.l  #$00100000,d4
        bhs.w   .invalid
        moveq   #0,d5
        move.w  (SAVE_PAYLOAD_TOTAL_CLEARS,a2),d5
        cmpi.w  #SAVE_ENCOUNTER_COUNT,d5
        bhi.w   .invalid
        tst.w   (SAVE_PAYLOAD_RESERVED,a2)
        bne.w   .invalid

        lea     (SAVE_PAYLOAD_METRICS,a2),a3
        moveq   #0,d3                   ; recomputed completion bitmap
        moveq   #0,d2                   ; recomputed clear count
        moveq   #0,d6                   ; encounter index
        moveq   #SAVE_ENCOUNTER_COUNT-1,d7
.metric:
        moveq   #0,d0
        move.b  (SAVE_METRIC_CLEARED,a3),d0
        beq.s   .uncleared
        cmpi.w  #1,d0
        bne.w   .invalid
        moveq   #0,d0
        move.b  (SAVE_METRIC_RANK,a3),d0
        cmpi.w  #SAVE_RANK_STABLE,d0
        blo.w   .invalid
        cmpi.w  #SAVE_RANK_TRANSCENDENT,d0
        bhi.w   .invalid
        move.w  (SAVE_METRIC_ATTEMPTS,a3),d0
        beq.w   .invalid
        cmpi.w  #$FFFF,d0
        beq.w   .invalid
        moveq   #0,d0
        move.b  (SAVE_METRIC_ECHOES,a3),d0
        cmpi.w  #6,d0
        bhi.w   .invalid
        move.w  (SAVE_METRIC_TIME_REMAINING,a3),d0
        cmpi.w  #720,d0
        bhi.w   .invalid
        bset    d6,d3
        addq.w  #1,d2
        bra.s   .metric_reserved
.uncleared:
        tst.b   (SAVE_METRIC_RANK,a3)
        bne.w   .invalid
        cmpi.w  #$FFFF,(SAVE_METRIC_ATTEMPTS,a3)
        bne.w   .invalid
        cmpi.b  #$FF,(SAVE_METRIC_ECHOES,a3)
        bne.w   .invalid
        tst.w   (SAVE_METRIC_TIME_REMAINING,a3)
        bne.w   .invalid
.metric_reserved:
        tst.b   (SAVE_METRIC_RESERVED,a3)
        bne.w   .invalid
        adda.w  #SAVE_METRIC_BYTES,a3
        addq.w  #1,d6
        dbf     d7,.metric
        cmp.l   d3,d4
        bne.w   .invalid
        cmp.w   d2,d5
        bne.w   .invalid

        lea     (SAVE_PAYLOAD_USED_END,a2),a0
        moveq   #(SAVE_RECORD_CRC-SAVE_PAYLOAD_USED_END)-1,d1
.tail_reserved:
        tst.b   (a0)+
        bne.s   .invalid
        dbf     d1,.tail_reserved

        movea.l a2,a0
        move.w  #SAVE_CRC_BYTES,d0
        bsr.w   Crc32IsoHdlc
        cmp.l   (SAVE_RECORD_CRC,a2),d0
        bne.s   .invalid
        moveq   #SAVE_RESULT_OK,d0
        bra.s   .done
.invalid:
        moveq   #1,d0
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

; SaveCommit
; Atomically writes the inactive slot, commit marker last, then reads it back.
; Out: d0.w=OK, INVALID_PAYLOAD, or WRITE_VERIFY. Mainline only.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
SaveCommit:
        movem.l d3-d7/a2-a4,-(a7)
        move.l  (SAVE_CONTROL_GENERATION).l,d7
        addq.l  #1,d7
        move.l  d7,d0
        bsr.w   SaveFinalizeActiveRecord
        lea     (SAVE_ACTIVE_BASE).l,a0
        bsr.w   SaveValidateRecord
        tst.w   d0
        bne.w   .invalid_payload

        moveq   #0,d6                   ; target slot ordinal
        move.l  #SAVE_SRAM_SLOT_A,d5
        cmpi.b  #0,(SAVE_CONTROL_ACTIVE_SLOT).l
        bne.s   .target_ready
        moveq   #1,d6
        move.l  #SAVE_SRAM_SLOT_B,d5
.target_ready:
        clr.l   (SAVE_ACTIVE_BASE+SAVE_RECORD_COMMIT).l
        movea.l d5,a0
        lea     (SAVE_COMMIT_PHYSICAL_OFFSET,a0),a0
        moveq   #3,d1
.invalidate_marker:
        clr.b   (a0)
        addq.l  #2,a0
        dbf     d1,.invalidate_marker

        movea.l d5,a0
        lea     (SAVE_ACTIVE_BASE).l,a1
        bsr.w   SaveWriteOddLane

        move.l  #SAVE_COMMIT_MARKER,(SAVE_ACTIVE_BASE+SAVE_RECORD_COMMIT).l
        movea.l d5,a0
        lea     (SAVE_COMMIT_PHYSICAL_OFFSET,a0),a0
        lea     (SAVE_ACTIVE_BASE+SAVE_RECORD_COMMIT).l,a1
        moveq   #3,d1
.commit_marker:
        move.b  (a1)+,(a0)
        addq.l  #2,a0
        dbf     d1,.commit_marker

        movea.l d5,a0
        lea     (SAVE_STAGING_BASE).l,a1
        bsr.w   SaveReadOddLane
        lea     (SAVE_STAGING_BASE).l,a0
        bsr.w   SaveValidateRecord
        tst.w   d0
        bne.s   .verify_failed
        cmp.l   (SAVE_STAGING_BASE+SAVE_RECORD_GENERATION).l,d7
        bne.s   .verify_failed
        move.l  (SAVE_STAGING_BASE+SAVE_RECORD_CRC).l,d0
        cmp.l   (SAVE_ACTIVE_BASE+SAVE_RECORD_CRC).l,d0
        bne.s   .verify_failed

        move.b  d6,(SAVE_CONTROL_ACTIVE_SLOT).l
        move.l  d7,(SAVE_CONTROL_GENERATION).l
        clr.b   (SAVE_CONTROL_DIRTY).l
        ori.w   #SAVE_STATUS_LOADED+SAVE_STATUS_WRITE_VERIFIED,(SAVE_CONTROL_STATUS).l
        cmpi.w  #$FFFF,(SAVE_CONTROL_WRITE_COUNT).l
        beq.s   .write_counted
        addq.w  #1,(SAVE_CONTROL_WRITE_COUNT).l
.write_counted:
        clr.w   (SAVE_CONTROL_LAST_ERROR).l
        clr.w   (SAVE_CONTROL_RESULT).l
        moveq   #SAVE_RESULT_OK,d0
        bra.s   .done
.invalid_payload:
        move.w  #SAVE_RESULT_INVALID_PAYLOAD,(SAVE_CONTROL_LAST_ERROR).l
        move.w  #SAVE_RESULT_INVALID_PAYLOAD,(SAVE_CONTROL_RESULT).l
        moveq   #SAVE_RESULT_INVALID_PAYLOAD,d0
        bra.s   .done
.verify_failed:
        move.w  #SAVE_RESULT_WRITE_VERIFY,(SAVE_CONTROL_LAST_ERROR).l
        move.w  #SAVE_RESULT_WRITE_VERIFY,(SAVE_CONTROL_RESULT).l
        moveq   #SAVE_RESULT_WRITE_VERIFY,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; SaveMarkStageClear
; In: d0.w=index 0..19, d1.w=attempts, d2.l=$RREEtttt.
; Keeps the lexicographically best rank/attempt/echo/time result.
; Out: d0.w=result. Clobbers d0-d2/a0-a1.
SaveMarkStageClear:
        movem.l d3-d7/a2-a4,-(a7)
        moveq   #0,d7
        move.w  d0,d7
        cmpi.w  #SAVE_ENCOUNTER_COUNT,d7
        bhs.w   .invalid_stage
        move.w  d1,d6
        beq.w   .invalid_stage
        cmpi.w  #$FFFF,d6
        beq.w   .invalid_stage
        move.l  d2,d5
        move.l  d5,d4
        swap    d4
        lsr.w   #8,d4
        andi.w  #$00FF,d4               ; rank
        cmpi.w  #SAVE_RANK_STABLE,d4
        blo.w   .invalid_stage
        cmpi.w  #SAVE_RANK_TRANSCENDENT,d4
        bhi.w   .invalid_stage
        move.l  d5,d3
        swap    d3
        andi.w  #$00FF,d3               ; echoes
        cmpi.w  #6,d3
        bhi.w   .invalid_stage
        andi.l  #$0000FFFF,d5           ; time remaining
        cmpi.w  #720,d5
        bhi.w   .invalid_stage

        move.w  d7,d0
        lsl.w   #3,d0
        lea     (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_METRICS).l,a2
        adda.w  d0,a2
        tst.b   (SAVE_METRIC_CLEARED,a2)
        beq.s   .new_clear
        cmp.b   (SAVE_METRIC_RANK,a2),d4
        bhi.s   .write_metric
        blo.s   .progress_only
        cmp.w   (SAVE_METRIC_ATTEMPTS,a2),d6
        blo.s   .write_metric
        bhi.s   .progress_only
        moveq   #0,d0
        move.b  (SAVE_METRIC_ECHOES,a2),d0
        cmp.w   d0,d3
        blo.s   .write_metric
        bhi.s   .progress_only
        cmp.w   (SAVE_METRIC_TIME_REMAINING,a2),d5
        bhi.s   .write_metric
        bra.s   .progress_only
.new_clear:
        move.l  (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_COMPLETION_BITS).l,d0
        bset    d7,d0
        move.l  d0,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_COMPLETION_BITS).l
        addq.w  #1,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_TOTAL_CLEARS).l
.write_metric:
        move.b  #1,(SAVE_METRIC_CLEARED,a2)
        move.b  d4,(SAVE_METRIC_RANK,a2)
        move.w  d6,(SAVE_METRIC_ATTEMPTS,a2)
        move.b  d3,(SAVE_METRIC_ECHOES,a2)
        clr.b   (SAVE_METRIC_RESERVED,a2)
        move.w  d5,(SAVE_METRIC_TIME_REMAINING,a2)
        move.b  #1,(SAVE_CONTROL_DIRTY).l

.progress_only:
        move.l  d7,d0
        divu.w  #4,d0
        moveq   #0,d1
        move.w  d0,d1                  ; sector ordinal + 1
        addq.w  #1,d1
        swap    d0
        cmpi.w  #3,d0
        bne.s   .sector_clamp
        addq.w  #1,d1
.sector_clamp:
        cmpi.w  #5,d1
        bls.s   .sector_compare
        moveq   #5,d1
.sector_compare:
        moveq   #0,d0
        move.b  (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_UNLOCKED_SECTOR).l,d0
        cmp.w   d0,d1
        bls.s   .ok
        move.b  d1,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_UNLOCKED_SECTOR).l
        move.b  #1,(SAVE_CONTROL_DIRTY).l
.ok:
        moveq   #SAVE_RESULT_OK,d0
        bra.s   .done
.invalid_stage:
        moveq   #SAVE_RESULT_INVALID_STAGE,d0
.done:
        move.w  d0,(SAVE_CONTROL_RESULT).l
        movem.l (a7)+,d3-d7/a2-a4
        rts

; SaveSetSettings
; In: d0.b=music/sfx/reset-flash mask. Out: result.
SaveSetSettings:
        move.w  d0,d1
        andi.w  #$00FF,d1
        cmpi.w  #SAVE_SETTINGS_MASK,d1
        bhi.s   .invalid
        cmp.b   (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_SETTINGS).l,d1
        beq.s   .ok
        move.b  d1,(SAVE_ACTIVE_BASE+SAVE_PAYLOAD_SETTINGS).l
        move.b  #1,(SAVE_CONTROL_DIRTY).l
.ok:
        moveq   #SAVE_RESULT_OK,d0
        move.w  d0,(SAVE_CONTROL_RESULT).l
        rts
.invalid:
        moveq   #SAVE_RESULT_INVALID_PAYLOAD,d0
        move.w  d0,(SAVE_CONTROL_RESULT).l
        rts

; SaveGetStageMetric
; In: d0.w=index. Out: d0.w=result, a0=record or metric zero on failure.
SaveGetStageMetric:
        cmpi.w  #SAVE_ENCOUNTER_COUNT,d0
        bhs.s   .invalid
        lsl.w   #3,d0
        lea     (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_METRICS).l,a0
        adda.w  d0,a0
        moveq   #SAVE_RESULT_OK,d0
        rts
.invalid:
        lea     (SAVE_ACTIVE_BASE+SAVE_PAYLOAD_METRICS).l,a0
        moveq   #SAVE_RESULT_INVALID_STAGE,d0
        rts

; RAM/SRAM transfer helpers. Logical SRAM bytes use physical address stride 2.
SaveReadOddLane:
        move.w  #SAVE_RECORD_BYTES-1,d1
.byte:
        move.b  (a0),(a1)+
        addq.l  #2,a0
        dbf     d1,.byte
        rts

; Writes bytes $00-$FB only. SaveCommit invalidates and publishes the marker.
SaveWriteOddLane:
        move.w  #SAVE_RECORD_COMMIT-1,d1
.byte:
        move.b  (a1)+,(a0)
        addq.l  #2,a0
        dbf     d1,.byte
        rts

SaveCopyRamRecord:
        moveq   #(SAVE_RECORD_BYTES/4)-1,d1
.long:
        move.l  (a0)+,(a1)+
        dbf     d1,.long
        rts
