; Motorola 68000 to Z80 audio bridge.
;
; Requires hardware/memory_map.asm. Public routines use the repository ABI:
; d0-d2/a0-a1 are caller-saved, d0 is the result, and callee-saved registers
; are untouched. No routine posts a frame tick; Z80 hardware V-int owns time.

AUDIO_BUS_TIMEOUT               equ     $3FFF
AUDIO_ZRAM_BYTES                equ     $2000
AUDIO_RESET_DELAY_ITERATIONS    equ     16

AUDIO_COMMAND_RING              equ     Z80_RAM+$1F00
AUDIO_STATUS                    equ     Z80_RAM+$1FE0
AUDIO_MAILBOX                   equ     Z80_RAM+$1FF0
AUDIO_BOOT_SMOKE_LATCH          equ     AUDIO_SHADOW_BASE+$10
AUDIO_LAST_POSTED_LAYER_MASK    equ     AUDIO_SHADOW_BASE+$11

AUDIO_STATUS_SIGNATURE          equ     AUDIO_STATUS+$00
AUDIO_STATUS_ABI_VERSION        equ     AUDIO_STATUS+$01
AUDIO_STATUS_STATE              equ     AUDIO_STATUS+$02
AUDIO_STATUS_FLAGS              equ     AUDIO_STATUS+$03
AUDIO_STATUS_HEARTBEAT_LO       equ     AUDIO_STATUS+$04
AUDIO_STATUS_HEARTBEAT_HI       equ     AUDIO_STATUS+$05
AUDIO_STATUS_QUEUE_READ         equ     AUDIO_STATUS+$06
AUDIO_STATUS_QUEUE_HIGH_WATER   equ     AUDIO_STATUS+$07
AUDIO_STATUS_COMMAND_COUNT_LO   equ     AUDIO_STATUS+$08
AUDIO_STATUS_COMMAND_COUNT_HI   equ     AUDIO_STATUS+$09
AUDIO_STATUS_LATE_TICK_COUNT    equ     AUDIO_STATUS+$0A
AUDIO_STATUS_YM_WRITE_COUNT     equ     AUDIO_STATUS+$0B
AUDIO_STATUS_PSG_WRITE_COUNT    equ     AUDIO_STATUS+$0C
AUDIO_STATUS_MOTIF_STEP         equ     AUDIO_STATUS+$0D
AUDIO_STATUS_ACTIVE_LAYERS      equ     AUDIO_STATUS+$0E
AUDIO_STATUS_LAST_ERROR_COMMAND equ     AUDIO_STATUS+$0F

AUDIO_MAILBOX_SIGNATURE         equ     AUDIO_MAILBOX+$00
AUDIO_MAILBOX_ABI_VERSION       equ     AUDIO_MAILBOX+$01
AUDIO_MAILBOX_QUEUE_WRITE       equ     AUDIO_MAILBOX+$02
AUDIO_MAILBOX_PRODUCER_FLAGS    equ     AUDIO_MAILBOX+$03
AUDIO_MAILBOX_URGENT_SEQUENCE   equ     AUDIO_MAILBOX+$04
AUDIO_MAILBOX_URGENT_ACK        equ     AUDIO_MAILBOX+$05
AUDIO_MAILBOX_URGENT_PAYLOAD    equ     AUDIO_MAILBOX+$06

AUDIO_DRIVER_SIGNATURE          equ     $94
AUDIO_DRIVER_ABI_VERSION        equ     $01
AUDIO_QUEUE_CAPACITY            equ     16
AUDIO_COMMAND_BYTES             equ     4

AUDIO_DEBUG_OFF                 equ     $0000
AUDIO_DEBUG_UPLOADING           equ     $0001
AUDIO_DEBUG_STARTING            equ     $0002
AUDIO_DEBUG_ERROR               equ     $0080

AUDIO_STATUS_FLAG_READY         equ     $01
AUDIO_STATUS_FLAG_YM_ACTIVE     equ     $02
AUDIO_STATUS_FLAG_PSG_ACTIVE    equ     $04

AUDIO_POST_OK                   equ     0
AUDIO_POST_TIMEOUT              equ     1
AUDIO_POST_BUSY                 equ     2
AUDIO_POLL_BAD_ABI              equ     2
AUDIO_POST_NOT_READY            equ     3

; Upload the complete generated driver into deterministic Z80 RAM and start it.
; Returns d0.w=0 on success or 1 after a bounded BUSREQ timeout.
AudioUploadAndStart:
        move.w  #AUDIO_DEBUG_OFF,(DEBUG_AUDIO_STATE).l
        clr.w   (DEBUG_Z80_HEARTBEAT).l
        clr.w   (DEBUG_AUDIO_COMMAND_COUNT).l
        clr.w   (DEBUG_Z80_TIMEOUT_COUNT).l
        andi.w  #$FFC7,(DEBUG_M2_FLAGS).l
        lea     (AUDIO_SHADOW_BASE).l,a0
        moveq   #0,d1
        moveq   #15,d0
.clear_shadow:
        move.b  d1,(a0)+
        dbf     d0,.clear_shadow
        clr.b   (AUDIO_BOOT_SMOKE_LATCH).l
; The Z80 driver boots with the base motif (mask bit 0) already active.
; Mirroring that fact here avoids a redundant command on the first frame.
        move.b  #$01,(AUDIO_LAST_POSTED_LAYER_MASK).l
        move.w  #AUDIO_DEBUG_UPLOADING,(DEBUG_AUDIO_STATE).l

        bsr.w   AudioAcquireBus
        tst.w   d0
        bne.s   .done

; Reset is asserted only after BUSREQ acknowledgement. The explicit delay is
; over 192 68000 cycles: 16 iterations of NOP+DBF alone exceed that bound.
        bsr.w   AudioAssertReset

; Power-on ZRAM is undefined. Clear all 8 KiB while both bus and reset are
; owned, then copy exactly the generated code image one byte at a time.
        lea     (Z80_RAM).l,a0
        moveq   #0,d1
        move.w  #AUDIO_ZRAM_BYTES-1,d0
.clear_zram:
        move.b  d1,(a0)+
        dbf     d0,.clear_zram

        lea     (Z80DriverImage).l,a0
        lea     (Z80_RAM).l,a1
        move.w  #Z80DriverImageSize-1,d0
.copy_driver:
        move.b  (a0)+,(a1)+
        dbf     d0,.copy_driver

; Initialize both versioned blocks while the Z80 remains reset. The entire
; payload area is already zero; producer/consumer publication bytes are
; written last so no partially initialized command can become visible.
        move.b  #AUDIO_DRIVER_SIGNATURE,(AUDIO_STATUS_SIGNATURE).l
        move.b  #AUDIO_DRIVER_ABI_VERSION,(AUDIO_STATUS_ABI_VERSION).l
        move.b  #AUDIO_DRIVER_SIGNATURE,(AUDIO_MAILBOX_SIGNATURE).l
        move.b  #AUDIO_DRIVER_ABI_VERSION,(AUDIO_MAILBOX_ABI_VERSION).l
        clr.b   (AUDIO_STATUS_QUEUE_READ).l
        clr.b   (AUDIO_MAILBOX_URGENT_ACK).l
        clr.b   (AUDIO_MAILBOX_QUEUE_WRITE).l
        clr.b   (AUDIO_MAILBOX_URGENT_SEQUENCE).l

; Hold reset for another explicit >=192-cycle interval after the final copied
; and initialized byte, independent of the much longer clear/copy duration.
        bsr.w   AudioAssertReset

; Release reset before BUSREQ so the Z80 begins at $0000 without a bus race.
        bsr.w   AudioReleaseBus
        ori.w   #M2_FLAG_Z80_UPLOADED,(DEBUG_M2_FLAGS).l
        move.w  #AUDIO_DEBUG_STARTING,(DEBUG_AUDIO_STATE).l
        moveq   #AUDIO_POST_OK,d0
.done:
        rts

; Request and wait for the Z80 bus. Reset is cancelled after BUSREQ is raised
; so a reset-held Z80 can acknowledge without executing unowned code.
; Returns d0.w=0 with the bus owned, or 1 with reset safely asserted.
AudioAcquireBus:
        move.w  #$0100,(Z80_BUS_REQUEST).l
        move.w  #$0100,(Z80_RESET).l
        move.w  #AUDIO_BUS_TIMEOUT-1,d0
.wait:
        moveq   #0,d1
        move.b  (Z80_BUS_REQUEST).l,d1
        andi.b  #$01,d1
        beq.s   .owned
        dbf     d0,.wait

        bsr.w   AudioRecordBusTimeout
        move.w  #$0000,(Z80_RESET).l
        move.w  #$0000,(Z80_BUS_REQUEST).l
        moveq   #AUDIO_POST_TIMEOUT,d0
        rts
.owned:
        moveq   #AUDIO_POST_OK,d0
        rts

; Assert Z80 reset and hold it for a conservative >=192-cycle pulse.
; Caller must already own the bus. Clobbers d0.
AudioAssertReset:
        move.w  #$0000,(Z80_RESET).l
        move.w  #AUDIO_RESET_DELAY_ITERATIONS-1,d0
.hold:
        nop
        dbf     d0,.hold
        rts

; Release order is a hardware invariant: reset first, then BUSREQ.
AudioReleaseBus:
        move.w  #$0100,(Z80_RESET).l
        move.w  #$0000,(Z80_BUS_REQUEST).l
        rts

; Mirror the stable 16-byte Z80 status block into the big-endian debug ABI.
; Bus ownership makes the multi-byte reads atomic. Returns 0, 1 timeout, or
; 2 when the signature/version is not initialized yet.
AudioPollStatus:
        bsr.w   AudioAcquireBus
        tst.w   d0
        bne.w   .done

        cmpi.b  #AUDIO_DRIVER_SIGNATURE,(AUDIO_STATUS_SIGNATURE).l
        bne.w   .bad_abi
        cmpi.b  #AUDIO_DRIVER_ABI_VERSION,(AUDIO_STATUS_ABI_VERSION).l
        bne.w   .bad_abi

; Mirror the entire status block atomically into the fixed 68000 audio shadow.
; Debuggers can inspect YM/PSG writes, queue high-water, late ticks, and errors
; at $FF990B/$FF990C/$FF9907/$FF990A/$FF990F without touching live Z80 RAM.
        lea     (AUDIO_STATUS).l,a0
        lea     (AUDIO_SHADOW_BASE).l,a1
        moveq   #15,d2
.copy_status:
        move.b  (a0)+,(a1)+
        dbf     d2,.copy_status

        moveq   #0,d1
        move.b  (AUDIO_SHADOW_BASE+$02).l,d1
        move.w  d1,(DEBUG_AUDIO_STATE).l

        moveq   #0,d1
        move.b  (AUDIO_SHADOW_BASE+$05).l,d1
        lsl.w   #8,d1
        move.b  (AUDIO_SHADOW_BASE+$04).l,d1
        move.w  d1,(DEBUG_Z80_HEARTBEAT).l

        moveq   #0,d1
        move.b  (AUDIO_SHADOW_BASE+$09).l,d1
        lsl.w   #8,d1
        move.b  (AUDIO_SHADOW_BASE+$08).l,d1
        move.w  d1,(DEBUG_AUDIO_COMMAND_COUNT).l

        andi.w  #$FFCF,(DEBUG_M2_FLAGS).l
        ori.w   #M2_FLAG_Z80_UPLOADED,(DEBUG_M2_FLAGS).l
        moveq   #0,d1
        move.b  (AUDIO_SHADOW_BASE+$03).l,d1
        btst    #1,d1
        beq.s   .ym_done
        ori.w   #M2_FLAG_YM2612_ACTIVE,(DEBUG_M2_FLAGS).l
.ym_done:
        btst    #2,d1
        beq.s   .psg_done
        ori.w   #M2_FLAG_PSG_ACTIVE,(DEBUG_M2_FLAGS).l
.psg_done:
        bsr.w   AudioReleaseBus
        moveq   #AUDIO_POST_OK,d0
        rts

.bad_abi:
        andi.w  #$FFCF,(DEBUG_M2_FLAGS).l
        move.w  #AUDIO_DEBUG_ERROR,(DEBUG_AUDIO_STATE).l
        bsr.w   AudioReleaseBus
        moveq   #AUDIO_POLL_BAD_ABI,d0
.done:
        rts

; Post one four-byte ordinary command.
; In: a0 -> opcode,arg0,arg1,arg2. Returns 0 posted, 1 timeout, or 2 full.
AudioPostCommand:
        bsr.w   AudioAcquireBus
        tst.w   d0
        bne.s   .done

        moveq   #0,d1
        move.b  (AUDIO_MAILBOX_QUEUE_WRITE).l,d1
        moveq   #0,d2
        move.b  (AUDIO_STATUS_QUEUE_READ).l,d2
        sub.b   d2,d1
        andi.w  #$00FF,d1
        cmpi.w  #AUDIO_QUEUE_CAPACITY,d1
        bcc.s   .full

        moveq   #0,d2
        move.b  (AUDIO_MAILBOX_QUEUE_WRITE).l,d2
        andi.w  #$000F,d2
        lsl.w   #2,d2
        lea     (AUDIO_COMMAND_RING).l,a1
        adda.w  d2,a1
        move.b  (a0),(a1)
        move.b  1(a0),1(a1)
        move.b  2(a0),2(a1)
        move.b  3(a0),3(a1)

; The producer sequence is the sole publication store and is written last.
        moveq   #0,d1
        move.b  (AUDIO_MAILBOX_QUEUE_WRITE).l,d1
        addq.b  #1,d1
        move.b  d1,(AUDIO_MAILBOX_QUEUE_WRITE).l
        bsr.w   AudioReleaseBus
        moveq   #AUDIO_POST_OK,d0
        rts
.full:
        bsr.w   AudioReleaseBus
        moveq   #AUDIO_POST_BUSY,d0
.done:
        rts

; Post one four-byte urgent command without overwriting an unacknowledged one.
; In: a0 -> opcode,arg0,arg1,arg2. Returns 0 posted, 1 timeout, or 2 busy.
AudioPostUrgent:
        bsr.w   AudioAcquireBus
        tst.w   d0
        bne.s   .done

        moveq   #0,d1
        move.b  (AUDIO_MAILBOX_URGENT_SEQUENCE).l,d1
        moveq   #0,d2
        move.b  (AUDIO_MAILBOX_URGENT_ACK).l,d2
        cmp.b   d2,d1
        bne.s   .busy

        move.b  (a0),(AUDIO_MAILBOX_URGENT_PAYLOAD).l
        move.b  1(a0),(AUDIO_MAILBOX_URGENT_PAYLOAD+1).l
        move.b  2(a0),(AUDIO_MAILBOX_URGENT_PAYLOAD+2).l
        move.b  3(a0),(AUDIO_MAILBOX_URGENT_PAYLOAD+3).l

; The request sequence publishes the payload and is always written last.
        addq.b  #1,d1
        move.b  d1,(AUDIO_MAILBOX_URGENT_SEQUENCE).l
        bsr.w   AudioReleaseBus
        moveq   #AUDIO_POST_OK,d0
        rts
.busy:
        bsr.w   AudioReleaseBus
        moveq   #AUDIO_POST_BUSY,d0
.done:
        rts

; Poll until READY and publish exactly one real 68000->Z80 PSG SFX command.
; The main loop may call this once per frame during bring-up: the latch is set
; only after successful publication, so a full/busy queue retries but success
; can never repost every frame. Returns 0 done, 1 timeout, 2 ABI/full, 3 wait.
AudioPostBootSmokeOnce:
; Poll first even after the one-shot is latched so one call per consumed frame
; keeps the 68000 shadow/debug heartbeat and hardware counters live.
        bsr.w   AudioPollStatus
        tst.w   d0
        bne.s   .done
        tst.b   (AUDIO_BOOT_SMOKE_LATCH).l
        bne.s   .already_posted
        btst    #0,(AUDIO_SHADOW_BASE+$03).l
        beq.s   .not_ready
        lea     (AudioBootSmokeCommand).l,a0
        bsr.w   AudioPostCommand
        tst.w   d0
        bne.s   .done
        move.b  #$01,(AUDIO_BOOT_SMOKE_LATCH).l
.done:
        rts
.already_posted:
        moveq   #AUDIO_POST_OK,d0
        rts
.not_ready:
        moveq   #AUDIO_POST_NOT_READY,d0
        rts

; Publish an audible, monotonic music-layer mask whenever the authoritative
; timeline echo count changes. Bit 0 is the base pulse and each of bits 1..6
; unlocks the corresponding echo layer. The Z80 applies the audible change at
; its next sequencer boundary; a failed/full post is retried next frame.
; Returns 0 unchanged/posted, 1 timeout, 2 full, or 3 while the driver starts.
AudioPostEchoLayersIfChanged:
        moveq   #0,d1
        move.b  (TIMELINE_ECHO_COUNT).l,d1
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d1
        bls.s   .count_bounded
        moveq   #GAMEPLAY_ECHO_LIMIT,d1
.count_bounded:
        lsl.w   #2,d1
        lea     (AudioEchoLayerCommands).l,a0
        adda.w  d1,a0
        move.b  1(a0),d1
        cmp.b   (AUDIO_LAST_POSTED_LAYER_MASK).l,d1
        beq.s   .unchanged
        btst    #0,(AUDIO_SHADOW_BASE+$03).l
        beq.s   .not_ready
        bsr.w   AudioPostCommand
        tst.w   d0
        bne.s   .done
        move.b  1(a0),(AUDIO_LAST_POSTED_LAYER_MASK).l
.done:
        rts
.unchanged:
        moveq   #AUDIO_POST_OK,d0
        rts
.not_ready:
        moveq   #AUDIO_POST_NOT_READY,d0
        rts

AudioRecordBusTimeout:
        ori.w   #HARDWARE_FLAG_Z80_TIMEOUT,(DEBUG_HARDWARE_FLAGS).l
        cmpi.w  #$FFFF,(DEBUG_Z80_TIMEOUT_COUNT).l
        beq.s   .counter_done
        addq.w  #1,(DEBUG_Z80_TIMEOUT_COUNT).l
.counter_done:
        andi.w  #$FFCF,(DEBUG_M2_FLAGS).l
        move.w  #AUDIO_DEBUG_ERROR,(DEBUG_AUDIO_STATE).l
        rts

        even

AudioBootSmokeCommand:
        dc.b    $03,$00,$00,$00

; SET_LAYERS commands indexed directly by the bounded echo count. Masks are
; cumulative so acquiring an echo never removes an earlier musical voice.
AudioEchoLayerCommands:
        dc.b    $02,$01,$00,$00
        dc.b    $02,$03,$00,$00
        dc.b    $02,$07,$00,$00
        dc.b    $02,$0F,$00,$00
        dc.b    $02,$1F,$00,$00
        dc.b    $02,$3F,$00,$00
        dc.b    $02,$7F,$00,$00

        even

Z80DriverImage:
        incbin  "assets/generated/z80_driver.bin"
Z80DriverImageEnd:
Z80DriverImageSize              equ     Z80DriverImageEnd-Z80DriverImage
        assert  Z80DriverImageSize>0,"Z80 driver image must not be empty"
        assert  Z80DriverImageSize<=$1F00,"Z80 driver image overlaps command ring"

        even
