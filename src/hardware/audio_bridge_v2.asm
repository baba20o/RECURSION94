; RECURSION 94 proposed 68000-to-Z80 audio ABI v2.
;
; This standalone source is deliberately not included by the current ROM.
; The mission controller must approve AUDIO_V2_SHADOW_BASE and wire generated
; banks before integration. No routine is interrupt-safe or callable from an
; interrupt. Every bus wait, copy, scan, and CRC loop has a static bound.

        org     $0000

Z80_RAM                         equ     $00A00000
Z80_BUS_REQUEST                 equ     $00A11100
Z80_RESET                       equ     $00A11200

AUDIO_V2_ACTIVE_BANK            equ     Z80_RAM+$1200
AUDIO_V2_COMMAND_RING           equ     Z80_RAM+$1F00
AUDIO_V2_EXTENDED_STATUS        equ     Z80_RAM+$1F40
AUDIO_V2_CORE_STATUS            equ     Z80_RAM+$1FE0
AUDIO_V2_MAILBOX                equ     Z80_RAM+$1FF0

AUDIO_V2_EXT_SIGNATURE          equ     AUDIO_V2_EXTENDED_STATUS+$00
AUDIO_V2_EXT_ABI_VERSION        equ     AUDIO_V2_EXTENDED_STATUS+$01
AUDIO_V2_EXT_REQUESTED_SCENE    equ     AUDIO_V2_EXTENDED_STATUS+$02
AUDIO_V2_EXT_REQUESTED_MASK     equ     AUDIO_V2_EXTENDED_STATUS+$03
AUDIO_V2_EXT_REQUESTED_GEN      equ     AUDIO_V2_EXTENDED_STATUS+$04
AUDIO_V2_EXT_APPLIED_SCENE      equ     AUDIO_V2_EXTENDED_STATUS+$05
AUDIO_V2_EXT_APPLIED_MASK       equ     AUDIO_V2_EXTENDED_STATUS+$06
AUDIO_V2_EXT_APPLIED_GEN        equ     AUDIO_V2_EXTENDED_STATUS+$07

AUDIO_V2_STATUS_SIGNATURE       equ     AUDIO_V2_CORE_STATUS+$00
AUDIO_V2_STATUS_ABI_VERSION     equ     AUDIO_V2_CORE_STATUS+$01
AUDIO_V2_STATUS_STATE           equ     AUDIO_V2_CORE_STATUS+$02
AUDIO_V2_STATUS_FLAGS           equ     AUDIO_V2_CORE_STATUS+$03
AUDIO_V2_STATUS_QUEUE_READ      equ     AUDIO_V2_CORE_STATUS+$06

AUDIO_V2_MAILBOX_SIGNATURE      equ     AUDIO_V2_MAILBOX+$00
AUDIO_V2_MAILBOX_ABI_VERSION    equ     AUDIO_V2_MAILBOX+$01
AUDIO_V2_MAILBOX_QUEUE_WRITE    equ     AUDIO_V2_MAILBOX+$02
AUDIO_V2_MAILBOX_URGENT_GEN     equ     AUDIO_V2_MAILBOX+$04
AUDIO_V2_MAILBOX_URGENT_ACK     equ     AUDIO_V2_MAILBOX+$05
AUDIO_V2_MAILBOX_URGENT_PAYLOAD equ     AUDIO_V2_MAILBOX+$06

; Proposed 80-byte main-RAM block. It does not overlap the wired v1 shadow,
; but remains an integration-time RAM-map decision.
AUDIO_V2_SHADOW_BASE            equ     $00FF9940
AUDIO_V2_DESIRED_SCENE          equ     AUDIO_V2_SHADOW_BASE+$00
AUDIO_V2_DESIRED_MASK           equ     AUDIO_V2_SHADOW_BASE+$01
AUDIO_V2_DESIRED_DIRTY          equ     AUDIO_V2_SHADOW_BASE+$02
AUDIO_V2_NEXT_GENERATION        equ     AUDIO_V2_SHADOW_BASE+$03
AUDIO_V2_IN_FLIGHT_SCENE        equ     AUDIO_V2_SHADOW_BASE+$04
AUDIO_V2_IN_FLIGHT_MASK         equ     AUDIO_V2_SHADOW_BASE+$05
AUDIO_V2_IN_FLIGHT_GENERATION   equ     AUDIO_V2_SHADOW_BASE+$06
AUDIO_V2_IN_FLIGHT_ACTIVE       equ     AUDIO_V2_SHADOW_BASE+$07
AUDIO_V2_LAST_RESULT            equ     AUDIO_V2_SHADOW_BASE+$08
AUDIO_V2_COMMAND_BUFFER         equ     AUDIO_V2_SHADOW_BASE+$10
AUDIO_V2_EXTENDED_MIRROR        equ     AUDIO_V2_SHADOW_BASE+$20
AUDIO_V2_CORE_MIRROR            equ     AUDIO_V2_SHADOW_BASE+$40
AUDIO_V2_SHADOW_BYTES           equ     $50

AUDIO_V2_MIRROR_REQUESTED_SCENE equ     AUDIO_V2_EXTENDED_MIRROR+$02
AUDIO_V2_MIRROR_REQUESTED_MASK  equ     AUDIO_V2_EXTENDED_MIRROR+$03
AUDIO_V2_MIRROR_REQUESTED_GEN   equ     AUDIO_V2_EXTENDED_MIRROR+$04
AUDIO_V2_MIRROR_APPLIED_SCENE   equ     AUDIO_V2_EXTENDED_MIRROR+$05
AUDIO_V2_MIRROR_APPLIED_MASK    equ     AUDIO_V2_EXTENDED_MIRROR+$06
AUDIO_V2_MIRROR_APPLIED_GEN     equ     AUDIO_V2_EXTENDED_MIRROR+$07
AUDIO_V2_MIRROR_STATE           equ     AUDIO_V2_CORE_MIRROR+$02

AUDIO_V2_SIGNATURE_VALUE        equ     $94
AUDIO_V2_ABI_VERSION_VALUE      equ     $02
AUDIO_V2_BUS_TIMEOUT            equ     $3FFF
AUDIO_V2_QUEUE_CAPACITY         equ     16
AUDIO_V2_COMMAND_BYTES          equ     4
AUDIO_V2_MAX_SCENE_ID           equ     39
AUDIO_V2_MAX_SFX_ID             equ     15
AUDIO_V2_BANK_BYTES             equ     128
AUDIO_V2_BANK_PAYLOAD_BYTES     equ     112
AUDIO_V2_BANK_ROWS              equ     16
AUDIO_V2_BANK_TICKS_PER_ROW     equ     6
AUDIO_V2_BANK_LAYERS            equ     7

AUDIO_V2_COMMAND_SFX            equ     $03
AUDIO_V2_COMMAND_PAUSE          equ     $04
AUDIO_V2_COMMAND_SYNC_STATE     equ     $09

AUDIO_V2_STATE_READY            equ     $01
AUDIO_V2_STATE_PAUSED           equ     $03
AUDIO_V2_STATE_STOPPED          equ     $04
AUDIO_V2_STATE_ERROR            equ     $80
AUDIO_V2_STATUS_BANK_INVALID    equ     $40

AUDIO_V2_OK                     equ     0
AUDIO_V2_TIMEOUT                equ     1
AUDIO_V2_BUSY                   equ     2
AUDIO_V2_BAD_ABI                equ     3
AUDIO_V2_BAD_ARGUMENT           equ     4
AUDIO_V2_BAD_BANK               equ     5
AUDIO_V2_UNSAFE_BANK_LOAD       equ     6

; Clear only the proposed controller/mirror block.
; Out: d0.w=0. Clobbers d0-d1/a0.
AudioV2Initialise:
        lea     (AUDIO_V2_SHADOW_BASE).l,a0
        moveq   #0,d1
        moveq   #AUDIO_V2_SHADOW_BYTES-1,d0
.clear:
        move.b  d1,(a0)+
        dbf     d0,.clear
        move.b  #$01,(AUDIO_V2_NEXT_GENERATION).l
        moveq   #AUDIO_V2_OK,d0
        rts

; Set the latest desired snapshot without allocating a wire generation.
; In: d0.w=scene 0..39, d1.w=layer bits 0..6 plus mute bit 7.
; Out: d0.w=0 unchanged/accepted, 4 invalid. Clobbers d0-d2/a0.
AudioV2SetDesiredState:
        cmpi.w  #AUDIO_V2_MAX_SCENE_ID,d0
        bhi.s   .bad
        cmpi.w  #$00FF,d1
        bhi.s   .bad
        move.w  d0,d2
        tst.w   d2
        beq.s   .stopped
        lea     (AudioV2SceneCapMaskTable).l,a0
        moveq   #0,d0
        move.b  0(a0,d2.w),d0
        not.b   d0
        andi.b  #$7F,d0
        and.b   d1,d0
        bne.s   .bad
        bra.s   .compare
.stopped:
        move.w  d1,d0
        andi.w  #$007F,d0
        bne.s   .bad
.compare:
        cmp.b   (AUDIO_V2_DESIRED_SCENE).l,d2
        bne.s   .changed
        cmp.b   (AUDIO_V2_DESIRED_MASK).l,d1
        beq.s   .ok
.changed:
        move.b  d2,(AUDIO_V2_DESIRED_SCENE).l
        move.b  d1,(AUDIO_V2_DESIRED_MASK).l
        move.b  #$01,(AUDIO_V2_DESIRED_DIRTY).l
.ok:
        moveq   #AUDIO_V2_OK,d0
        rts
.bad:
        moveq   #AUDIO_V2_BAD_ARGUMENT,d0
        rts

; Poll both status blocks, retire an accepted generation, and post at most one
; desired-state command. A full queue or BUSREQ timeout leaves the exact same
; generation in flight for a later retry. Intermediate desired states coalesce.
; Out: d0.w=0 success/idle or a bounded error. Clobbers d0-d2/a0-a1.
AudioV2Service:
        bsr.w   AudioV2PollStatus
        tst.w   d0
        bne.w   .done

        tst.b   (AUDIO_V2_IN_FLIGHT_ACTIVE).l
        beq.s   .consider_post
        move.b  (AUDIO_V2_MIRROR_REQUESTED_GEN).l,d0
        cmp.b   (AUDIO_V2_IN_FLIGHT_GENERATION).l,d0
        bne.s   .consider_post
        move.b  (AUDIO_V2_MIRROR_REQUESTED_SCENE).l,d0
        cmp.b   (AUDIO_V2_IN_FLIGHT_SCENE).l,d0
        bne.s   .consider_post

; A bank failure is acknowledged by generation and scene even though the Z80
; forces its requested mask to muted. This prevents an impossible bank from
; deadlocking presentation progress.
        clr.b   (AUDIO_V2_IN_FLIGHT_ACTIVE).l
        move.b  (AUDIO_V2_DESIRED_SCENE).l,d0
        cmp.b   (AUDIO_V2_IN_FLIGHT_SCENE).l,d0
        bne.s   .consider_post
        move.b  (AUDIO_V2_DESIRED_MASK).l,d0
        cmp.b   (AUDIO_V2_IN_FLIGHT_MASK).l,d0
        bne.s   .consider_post
        clr.b   (AUDIO_V2_DESIRED_DIRTY).l

.consider_post:
        tst.b   (AUDIO_V2_IN_FLIGHT_ACTIVE).l
        bne.s   .compose
        tst.b   (AUDIO_V2_DESIRED_DIRTY).l
        beq.s   .idle
        move.b  (AUDIO_V2_DESIRED_SCENE).l,(AUDIO_V2_IN_FLIGHT_SCENE).l
        move.b  (AUDIO_V2_DESIRED_MASK).l,(AUDIO_V2_IN_FLIGHT_MASK).l
        move.b  (AUDIO_V2_NEXT_GENERATION).l,d0
        move.b  d0,(AUDIO_V2_IN_FLIGHT_GENERATION).l
        addq.b  #1,(AUDIO_V2_NEXT_GENERATION).l
        move.b  #$01,(AUDIO_V2_IN_FLIGHT_ACTIVE).l
.compose:
        move.b  #AUDIO_V2_COMMAND_SYNC_STATE,(AUDIO_V2_COMMAND_BUFFER).l
        move.b  (AUDIO_V2_IN_FLIGHT_SCENE).l,(AUDIO_V2_COMMAND_BUFFER+1).l
        move.b  (AUDIO_V2_IN_FLIGHT_MASK).l,(AUDIO_V2_COMMAND_BUFFER+2).l
        move.b  (AUDIO_V2_IN_FLIGHT_GENERATION).l,(AUDIO_V2_COMMAND_BUFFER+3).l
        lea     (AUDIO_V2_COMMAND_BUFFER).l,a0
        bsr.w   AudioV2PostCommand
        move.b  d0,(AUDIO_V2_LAST_RESULT).l
.done:
        rts
.idle:
        moveq   #AUDIO_V2_OK,d0
        move.b  d0,(AUDIO_V2_LAST_RESULT).l
        rts

; Atomically mirror 20 extended-status bytes and all 16 core-status bytes.
; Out: d0.w=0, 1 timeout, or 3 bad ABI. Clobbers d0-d2/a0-a1.
AudioV2PollStatus:
        bsr.w   AudioV2AcquireBus
        tst.w   d0
        bne.s   .done
        bsr.w   AudioV2ValidateAbiOwned
        tst.w   d0
        bne.s   .bad_abi

        lea     (AUDIO_V2_EXTENDED_STATUS).l,a0
        lea     (AUDIO_V2_EXTENDED_MIRROR).l,a1
        moveq   #19,d2
.copy_extended:
        move.b  (a0)+,(a1)+
        dbf     d2,.copy_extended
        lea     (AUDIO_V2_CORE_STATUS).l,a0
        lea     (AUDIO_V2_CORE_MIRROR).l,a1
        moveq   #15,d2
.copy_core:
        move.b  (a0)+,(a1)+
        dbf     d2,.copy_core
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_OK,d0
.done:
        rts
.bad_abi:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_BAD_ABI,d0
        rts

; Post one exact four-byte ordinary command with publication byte written last.
; In: a0=command. Out: d0.w=0, 1 timeout, 2 full, or 3 bad ABI.
; Clobbers d0-d2/a0-a1.
AudioV2PostCommand:
        bsr.w   AudioV2AcquireBus
        tst.w   d0
        bne.s   .done
        bsr.w   AudioV2ValidateAbiOwned
        tst.w   d0
        bne.s   .bad_abi

        moveq   #0,d1
        move.b  (AUDIO_V2_MAILBOX_QUEUE_WRITE).l,d1
        moveq   #0,d2
        move.b  (AUDIO_V2_STATUS_QUEUE_READ).l,d2
        sub.b   d2,d1
        andi.w  #$00FF,d1
        cmpi.w  #AUDIO_V2_QUEUE_CAPACITY,d1
        bcc.s   .full

        moveq   #0,d2
        move.b  (AUDIO_V2_MAILBOX_QUEUE_WRITE).l,d2
        andi.w  #$000F,d2
        lsl.w   #2,d2
        lea     (AUDIO_V2_COMMAND_RING).l,a1
        adda.w  d2,a1
        move.b  (a0),(a1)
        move.b  1(a0),1(a1)
        move.b  2(a0),2(a1)
        move.b  3(a0),3(a1)
        moveq   #0,d1
        move.b  (AUDIO_V2_MAILBOX_QUEUE_WRITE).l,d1
        addq.b  #1,d1
        move.b  d1,(AUDIO_V2_MAILBOX_QUEUE_WRITE).l
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_OK,d0
.done:
        rts
.full:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_BUSY,d0
        rts
.bad_abi:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_BAD_ABI,d0
        rts

; Post one exact four-byte urgent command without overwriting an unacknowledged
; payload. In: a0=command. Out: d0.w=0, 1 timeout, 2 busy, or 3 bad ABI.
; Clobbers d0-d2/a0.
AudioV2PostUrgent:
        bsr.w   AudioV2AcquireBus
        tst.w   d0
        bne.s   .done
        bsr.w   AudioV2ValidateAbiOwned
        tst.w   d0
        bne.s   .bad_abi
        moveq   #0,d1
        move.b  (AUDIO_V2_MAILBOX_URGENT_GEN).l,d1
        moveq   #0,d2
        move.b  (AUDIO_V2_MAILBOX_URGENT_ACK).l,d2
        cmp.b   d2,d1
        bne.s   .busy
        move.b  (a0),(AUDIO_V2_MAILBOX_URGENT_PAYLOAD).l
        move.b  1(a0),(AUDIO_V2_MAILBOX_URGENT_PAYLOAD+1).l
        move.b  2(a0),(AUDIO_V2_MAILBOX_URGENT_PAYLOAD+2).l
        move.b  3(a0),(AUDIO_V2_MAILBOX_URGENT_PAYLOAD+3).l
        addq.b  #1,d1
        move.b  d1,(AUDIO_V2_MAILBOX_URGENT_GEN).l
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_OK,d0
.done:
        rts
.busy:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_BUSY,d0
        rts
.bad_abi:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_BAD_ABI,d0
        rts

; Publish the all-notes-off pause used before a bank upload.
; Out: d0.w as AudioV2PostUrgent. Clobbers d0-d2/a0.
AudioV2RequestPause:
        move.b  #AUDIO_V2_COMMAND_PAUSE,(AUDIO_V2_COMMAND_BUFFER).l
        clr.b   (AUDIO_V2_COMMAND_BUFFER+1).l
        clr.b   (AUDIO_V2_COMMAND_BUFFER+2).l
        clr.b   (AUDIO_V2_COMMAND_BUFFER+3).l
        lea     (AUDIO_V2_COMMAND_BUFFER).l,a0
        bra.w   AudioV2PostUrgent

; Validate and enqueue one bounded YM channel-5 SFX.
; In: d0.w=SFX ID 1..15, d1.w=priority 0..3, d2.b=event sequence.
; Out: d0.w as AudioV2PostCommand or 4 invalid. Clobbers d0-d2/a0-a1.
AudioV2PostSfx:
        tst.w   d0
        beq.s   .bad
        cmpi.w  #AUDIO_V2_MAX_SFX_ID,d0
        bhi.s   .bad
        cmpi.w  #3,d1
        bhi.s   .bad
        move.b  #AUDIO_V2_COMMAND_SFX,(AUDIO_V2_COMMAND_BUFFER).l
        move.b  d0,(AUDIO_V2_COMMAND_BUFFER+1).l
        lsl.b   #4,d1
        ori.b   #$01,d1
        move.b  d1,(AUDIO_V2_COMMAND_BUFFER+2).l
        move.b  d2,(AUDIO_V2_COMMAND_BUFFER+3).l
        lea     (AUDIO_V2_COMMAND_BUFFER).l,a0
        bra.w   AudioV2PostCommand
.bad:
        moveq   #AUDIO_V2_BAD_ARGUMENT,d0
        rts

; Validate and upload exactly one 128-byte bank while the driver is already
; READY, PAUSED, or STOPPED. Call AudioV2RequestPause and observe PAUSED before
; a gameplay-time upload. A new state generation is forced after success.
; In: d0.w=expected bank ID 1..20, d1.w=byte count (must be 128), a0=ROM bank.
; Out: d0.w=0 or bounded error. Clobbers d0-d2/a0-a1.
AudioV2LoadBank:
        tst.b   (AUDIO_V2_IN_FLIGHT_ACTIVE).l
        bne.w   .busy
        cmpi.w  #1,d0
        blo.w   .bad_argument
        cmpi.w  #20,d0
        bhi.w   .bad_argument
        cmpi.w  #AUDIO_V2_BANK_BYTES,d1
        bne.w   .bad_argument

        cmpi.b  #"R",(a0)
        bne.w   .bad_bank
        cmpi.b  #"9",1(a0)
        bne.w   .bad_bank
        cmpi.b  #"4",2(a0)
        bne.w   .bad_bank
        cmpi.b  #"A",3(a0)
        bne.w   .bad_bank
        cmpi.b  #$01,4(a0)
        bne.w   .bad_bank
        cmp.b   5(a0),d0
        bne.w   .bad_bank
        tst.b   6(a0)
        beq.w   .bad_bank
        cmpi.b  #160,6(a0)
        bhi.w   .bad_bank
        cmpi.b  #6,7(a0)
        bhi.w   .bad_bank
        cmpi.b  #AUDIO_V2_BANK_ROWS,8(a0)
        bne.w   .bad_bank
        cmpi.b  #AUDIO_V2_BANK_TICKS_PER_ROW,9(a0)
        bne.w   .bad_bank
        cmpi.b  #AUDIO_V2_BANK_LAYERS,10(a0)
        bne.w   .bad_bank
        cmpi.b  #2,11(a0)
        bhi.w   .bad_bank

; Check all 112 note bytes and require layers above the descriptor cap to rest.
        lea     16(a0),a1
        moveq   #AUDIO_V2_BANK_ROWS-1,d1
.row:
        moveq   #0,d2
.layer:
        moveq   #0,d0
        move.b  (a1)+,d0
        cmpi.w  #12,d0
        bhi.w   .bad_bank
        cmp.b   7(a0),d2
        bls.s   .next_layer
        tst.b   d0
        bne.w   .bad_bank
.next_layer:
        addq.w  #1,d2
        cmpi.w  #AUDIO_V2_BANK_LAYERS,d2
        blo.s   .layer
        dbf     d1,.row

; CRC-32/ISO-HDLC covers only the payload. Reconstruct its little-endian stored
; value explicitly so the result is independent of 68000 byte order.
        move.l  a0,-(a7)
        lea     16(a0),a0
        move.w  #AUDIO_V2_BANK_PAYLOAD_BYTES,d0
        bsr.w   AudioV2Crc32
        movea.l (a7)+,a0
        moveq   #0,d1
        move.b  15(a0),d1
        lsl.l   #8,d1
        move.b  14(a0),d1
        lsl.l   #8,d1
        move.b  13(a0),d1
        lsl.l   #8,d1
        move.b  12(a0),d1
        cmp.l   d1,d0
        bne.s   .bad_bank

        bsr.w   AudioV2AcquireBus
        tst.w   d0
        bne.s   .done
        bsr.w   AudioV2ValidateAbiOwned
        tst.w   d0
        bne.s   .bad_abi_owned
        moveq   #0,d0
        move.b  (AUDIO_V2_STATUS_STATE).l,d0
        cmpi.w  #AUDIO_V2_STATE_READY,d0
        beq.s   .safe
        cmpi.w  #AUDIO_V2_STATE_PAUSED,d0
        beq.s   .safe
        cmpi.w  #AUDIO_V2_STATE_STOPPED,d0
        beq.s   .safe
        cmpi.w  #AUDIO_V2_STATE_ERROR,d0
        bne.s   .unsafe_owned
        btst    #6,(AUDIO_V2_STATUS_FLAGS).l
        beq.s   .unsafe_owned
.safe:
        lea     (AUDIO_V2_ACTIVE_BANK).l,a1
        moveq   #AUDIO_V2_BANK_BYTES-1,d1
.copy:
        move.b  (a0)+,(a1)+
        dbf     d1,.copy
        bsr.w   AudioV2ReleaseBus
        move.b  #$01,(AUDIO_V2_DESIRED_DIRTY).l
        moveq   #AUDIO_V2_OK,d0
.done:
        rts
.bad_abi_owned:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_BAD_ABI,d0
        rts
.unsafe_owned:
        bsr.w   AudioV2ReleaseBus
        moveq   #AUDIO_V2_UNSAFE_BANK_LOAD,d0
        rts
.busy:
        moveq   #AUDIO_V2_BUSY,d0
        rts
.bad_argument:
        moveq   #AUDIO_V2_BAD_ARGUMENT,d0
        rts
.bad_bank:
        moveq   #AUDIO_V2_BAD_BANK,d0
        rts

; Request Z80 bus ownership with a bounded wait.
; Out: d0.w=0 owned or 1 timeout/reset asserted. Clobbers d0-d1.
AudioV2AcquireBus:
        move.w  #$0100,(Z80_BUS_REQUEST).l
        move.w  #$0100,(Z80_RESET).l
        move.w  #AUDIO_V2_BUS_TIMEOUT-1,d0
.wait:
        moveq   #0,d1
        move.b  (Z80_BUS_REQUEST).l,d1
        andi.b  #$01,d1
        beq.s   .owned
        dbf     d0,.wait
        move.w  #$0000,(Z80_RESET).l
        move.w  #$0000,(Z80_BUS_REQUEST).l
        moveq   #AUDIO_V2_TIMEOUT,d0
        rts
.owned:
        moveq   #AUDIO_V2_OK,d0
        rts

; Release reset first, then BUSREQ.
AudioV2ReleaseBus:
        move.w  #$0100,(Z80_RESET).l
        move.w  #$0000,(Z80_BUS_REQUEST).l
        rts

; Validate all producer/consumer signatures while the caller owns the bus.
; Out: d0.w=0 or 3. Clobbers d0.
AudioV2ValidateAbiOwned:
        cmpi.b  #AUDIO_V2_SIGNATURE_VALUE,(AUDIO_V2_EXT_SIGNATURE).l
        bne.s   .bad
        cmpi.b  #AUDIO_V2_ABI_VERSION_VALUE,(AUDIO_V2_EXT_ABI_VERSION).l
        bne.s   .bad
        cmpi.b  #AUDIO_V2_SIGNATURE_VALUE,(AUDIO_V2_STATUS_SIGNATURE).l
        bne.s   .bad
        cmpi.b  #AUDIO_V2_ABI_VERSION_VALUE,(AUDIO_V2_STATUS_ABI_VERSION).l
        bne.s   .bad
        cmpi.b  #AUDIO_V2_SIGNATURE_VALUE,(AUDIO_V2_MAILBOX_SIGNATURE).l
        bne.s   .bad
        cmpi.b  #AUDIO_V2_ABI_VERSION_VALUE,(AUDIO_V2_MAILBOX_ABI_VERSION).l
        bne.s   .bad
        moveq   #AUDIO_V2_OK,d0
        rts
.bad:
        moveq   #AUDIO_V2_BAD_ABI,d0
        rts

; CRC-32/ISO-HDLC, reflected polynomial $EDB88320.
; In: a0=bytes, d0.w=length. Out: d0.l=CRC. Clobbers d0-d2/a0.
AudioV2Crc32:
        move.l  #$FFFFFFFF,d1
        tst.w   d0
        beq.s   .finish
        subq.w  #1,d0
.next_byte:
        moveq   #0,d2
        move.b  (a0)+,d2
        eor.b   d2,d1
        moveq   #7,d2
.next_bit:
        lsr.l   #1,d1
        bcc.s   .no_polynomial
        eori.l  #$EDB88320,d1
.no_polynomial:
        dbf     d2,.next_bit
        dbf     d0,.next_byte
.finish:
        not.l   d1
        move.l  d1,d0
        rts

; Scene cap masks freeze the 40-entry registry ABI.
AudioV2SceneCapMaskTable:
        dc.b    $00
        dc.b    $07,$07,$0F,$0F,$0F,$0F,$1F,$1F,$1F,$1F
        dc.b    $3F,$3F,$3F,$3F,$7F,$7F,$7F,$7F
        dc.b    $07,$07,$07,$0F,$0F,$0F,$1F,$1F,$1F
        dc.b    $3F,$3F,$3F,$7F,$7F,$7F,$7F
        dc.b    $7F,$7F,$7F,$7F,$7F
AudioV2SceneCapMaskTableEnd:
        even

AudioBridgeV2ImageEnd:
        assert  AudioV2SceneCapMaskTableEnd-AudioV2SceneCapMaskTable=40
        assert  AUDIO_V2_SHADOW_BASE>=$00FF9900+$20
        assert  AUDIO_V2_SHADOW_BASE+AUDIO_V2_SHADOW_BYTES<=$00FFF000
