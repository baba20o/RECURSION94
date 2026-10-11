; Bounded VDP DMA queue. Main-side producers publish complete 12-byte records;
; only DmaCommitPreparedQueue, called by VBlank, touches the VDP DMA registers.

DMA_QUEUE_PUBLISHED_DEPTH       equ     DMA_QUEUE_BASE+$00
DMA_QUEUE_BUILD_DEPTH           equ     DMA_QUEUE_BASE+$02
DMA_QUEUE_BUILD_WORDS           equ     DMA_QUEUE_BASE+$04
DMA_QUEUE_COMMIT_WORDS          equ     DMA_QUEUE_BASE+$08
DMA_QUEUE_FRAME_PENDING         equ     DMA_QUEUE_BASE+$0A
DMA_QUEUE_RESERVED_0            equ     DMA_QUEUE_BASE+$0C
DMA_QUEUE_RESERVED_1            equ     DMA_QUEUE_BASE+$0E
DMA_QUEUE_ENTRIES               equ     DMA_QUEUE_BASE+$10

DMA_QUEUE_ENTRY_SOURCE          equ     $00
DMA_QUEUE_ENTRY_DESTINATION     equ     $04
DMA_QUEUE_ENTRY_WORDS           equ     $06
DMA_QUEUE_ENTRY_FLAGS           equ     $08
DMA_QUEUE_ENTRY_AGE             equ     $0A
DMA_QUEUE_ENTRY_BYTES           equ     $0C

DMA_FLAG_REQUIRED               equ     $8000
DMA_FLAG_PROCESSED              equ     $4000
DMA_PRIORITY_MASK               equ     $0003
DMA_PRIORITY_CRITICAL           equ     $0000
DMA_PRIORITY_HIGH               equ     $0001
DMA_PRIORITY_NORMAL             equ     $0002
DMA_PRIORITY_LOW                equ     $0003
DMA_PRIORITY_LEVELS             equ     4

DMA_ORDINARY_WORD_LIMIT         equ     2048
DMA_SOURCE_WINDOW_BYTES         equ     $00020000
DMA_BUSY_TIMEOUT                equ     $1FFF
VDP_REGISTER_DISPLAY_ON_DMA     equ     $8174

DMA_VRAM_DYNAMIC_START          equ     (DIAGNOSTIC_GRID_TILE+1)*32
DMA_VRAM_DYNAMIC_END            equ     VRAM_PLANE_A
DMA_VRAM_PLANE_A_END            equ     VRAM_WINDOW
DMA_VRAM_WINDOW_END             equ     VRAM_PLANE_B
DMA_VRAM_PLANE_B_END            equ     VRAM_SAT
DMA_VRAM_SAT_END                equ     VRAM_SAT+SAT_SHADOW_BYTES
DMA_VRAM_HSCROLL_END            equ     VRAM_HSCROLL+$0100

DMA_QUEUE_STORAGE_END           equ     DMA_QUEUE_ENTRIES+(DMA_QUEUE_CAPACITY*DMA_QUEUE_ENTRY_BYTES)

        assert  DMA_QUEUE_STORAGE_END<=INPUT_STATE_BASE,"DMA queue must not overlap input state"

DmaInitialize:
        clr.w   (DMA_QUEUE_PUBLISHED_DEPTH).l
        clr.w   (DMA_QUEUE_BUILD_DEPTH).l
        clr.l   (DMA_QUEUE_BUILD_WORDS).l
        clr.w   (DMA_QUEUE_COMMIT_WORDS).l
        clr.w   (DMA_QUEUE_FRAME_PENDING).l
        clr.w   (DMA_QUEUE_RESERVED_0).l
        clr.w   (DMA_QUEUE_RESERVED_1).l
        clr.w   (DEBUG_DMA_WORDS_FRAME).l
        clr.w   (DEBUG_DMA_WORDS_MAX).l
        clr.w   (DEBUG_DMA_QUEUE_DEPTH).l
        clr.w   (DEBUG_DMA_QUEUE_HIGH_WATER).l
        clr.w   (DEBUG_DMA_OVERFLOW_COUNT).l
        clr.w   (DEBUG_VBLANK_OVERRUN_COUNT).l
        clr.w   (DEBUG_DMA_DEFERRED_COUNT).l
        clr.w   (DEBUG_DMA_REQUIRED_DROP_COUNT).l
        clr.w   (DEBUG_DMA_INVALID_COUNT).l
        ori.w   #M2_FLAG_DMA_ACTIVE,(DEBUG_M2_FLAGS).l
        rts

; Begin a producer transaction while retaining any optional records deferred
; by the previous VBlank. Clobbers d0-d2/a0.
DmaQueueBeginFrame:
        moveq   #0,d0
        move.w  (DMA_QUEUE_PUBLISHED_DEPTH).l,d0
        cmpi.w  #DMA_QUEUE_CAPACITY,d0
        bls.s   .depth_valid
        moveq   #0,d0
        clr.w   (DMA_QUEUE_PUBLISHED_DEPTH).l
        cmpi.w  #$FFFF,(DEBUG_DMA_INVALID_COUNT).l
        beq.s   .depth_valid
        addq.w  #1,(DEBUG_DMA_INVALID_COUNT).l
.depth_valid:
        move.w  d0,(DMA_QUEUE_BUILD_DEPTH).l
        clr.l   (DMA_QUEUE_BUILD_WORDS).l
        tst.w   d0
        beq.s   .complete
        lea     (DMA_QUEUE_ENTRIES).l,a0
        move.w  d0,d1
        subq.w  #1,d1
.sum_words:
        moveq   #0,d2
        move.w  DMA_QUEUE_ENTRY_WORDS(a0),d2
        add.l   d2,(DMA_QUEUE_BUILD_WORDS).l
        adda.w  #DMA_QUEUE_ENTRY_BYTES,a0
        dbf     d1,.sum_words
.complete:
        rts

; a0: approved even source, d0.w: even VRAM destination, d1.w: word count,
; d2.w: DMA_FLAG_REQUIRED plus one DMA_PRIORITY_* value.
; Returns d0.l=1 when appended, d0.l=0 when rejected.
DmaQueueEnqueue:
        movem.l d3-d5/a2,-(a7)
        andi.l  #$0000FFFF,d0
        andi.l  #$0000FFFF,d1
        andi.l  #$0000FFFF,d2
        move.l  a0,d3

; Only the public required flag and two priority bits may be supplied.
        move.w  d2,d4
        andi.w  #$7FFC,d4
        bne.w   .invalid

        move.l  d3,d4
        andi.l  #$00000001,d4
        bne.w   .invalid
        cmpi.l  #$00100000,d3
        blo.s   .source_allowed
        cmpi.l  #WORK_RAM_BASE,d3
        blo.w   .invalid
        cmpi.l  #$01000000,d3
        bhs.w   .invalid
.source_allowed:
        tst.w   d1
        beq.w   .invalid
        cmpi.w  #DMA_ORDINARY_WORD_LIMIT,d1
        bhi.w   .invalid

        move.w  d0,d4
        andi.w  #$0001,d4
        bne.w   .invalid

; Verify both an explicitly owned VRAM destination range and the conservative
; 128 KiB source window before publishing the transfer.
        moveq   #0,d4
        move.w  d1,d4
        add.l   d4,d4
        moveq   #0,d5
        move.w  d0,d5
        add.l   d4,d5
        cmpi.l  #$00010000,d5
        bhi.w   .invalid

        cmpi.l  #DMA_VRAM_DYNAMIC_START,d0
        blo.s   .check_plane_a
        cmpi.l  #DMA_VRAM_DYNAMIC_END,d0
        bhs.s   .check_plane_a
        cmpi.l  #DMA_VRAM_DYNAMIC_END,d5
        bls.w   .destination_allowed
        bra.w   .invalid

.check_plane_a:
        cmpi.l  #VRAM_PLANE_A,d0
        blo.w   .invalid
        cmpi.l  #DMA_VRAM_PLANE_A_END,d0
        bhs.s   .check_window
        cmpi.l  #DMA_VRAM_PLANE_A_END,d5
        bls.w   .destination_allowed
        bra.w   .invalid

.check_window:
        cmpi.l  #VRAM_WINDOW,d0
        blo.w   .invalid
        cmpi.l  #DMA_VRAM_WINDOW_END,d0
        bhs.s   .check_plane_b
        cmpi.l  #DMA_VRAM_WINDOW_END,d5
        bls.w   .destination_allowed
        bra.w   .invalid

.check_plane_b:
        cmpi.l  #VRAM_PLANE_B,d0
        blo.w   .invalid
        cmpi.l  #DMA_VRAM_PLANE_B_END,d0
        bhs.s   .check_sat
        cmpi.l  #DMA_VRAM_PLANE_B_END,d5
        bls.w   .destination_allowed
        bra.w   .invalid

.check_sat:
        cmpi.l  #VRAM_SAT,d0
        blo.w   .invalid
        cmpi.l  #DMA_VRAM_SAT_END,d0
        bhs.s   .check_hscroll
        cmpi.l  #DMA_VRAM_SAT_END,d5
        bls.w   .destination_allowed
        bra.w   .invalid

.check_hscroll:
        cmpi.l  #VRAM_HSCROLL,d0
        blo.w   .invalid
        cmpi.l  #DMA_VRAM_HSCROLL_END,d0
        bhs.w   .invalid
        cmpi.l  #DMA_VRAM_HSCROLL_END,d5
        bhi.w   .invalid
.destination_allowed:

        move.l  d3,d5
        andi.l  #DMA_SOURCE_WINDOW_BYTES-1,d5
        add.l   d4,d5
        cmpi.l  #DMA_SOURCE_WINDOW_BYTES,d5
        bhi.w   .invalid

        moveq   #0,d5
        move.w  (DMA_QUEUE_BUILD_DEPTH).l,d5
        cmpi.w  #DMA_QUEUE_CAPACITY,d5
        bhs.w   .overflow

        move.w  d5,d4
        mulu.w  #DMA_QUEUE_ENTRY_BYTES,d4
        lea     (DMA_QUEUE_ENTRIES).l,a1
        adda.l  d4,a1
        move.l  d3,DMA_QUEUE_ENTRY_SOURCE(a1)
        move.w  d0,DMA_QUEUE_ENTRY_DESTINATION(a1)
        move.w  d1,DMA_QUEUE_ENTRY_WORDS(a1)
        move.w  d2,DMA_QUEUE_ENTRY_FLAGS(a1)
        clr.w   DMA_QUEUE_ENTRY_AGE(a1)

        addq.w  #1,d5
        move.w  d5,(DMA_QUEUE_BUILD_DEPTH).l
        add.l   d1,(DMA_QUEUE_BUILD_WORDS).l
        cmp.w   (DEBUG_DMA_QUEUE_HIGH_WATER).l,d5
        bls.s   .accepted
        move.w  d5,(DEBUG_DMA_QUEUE_HIGH_WATER).l
.accepted:
        moveq   #1,d0
        movem.l (a7)+,d3-d5/a2
        rts

.invalid:
        cmpi.w  #$FFFF,(DEBUG_DMA_INVALID_COUNT).l
        beq.s   .invalid_counted
        addq.w  #1,(DEBUG_DMA_INVALID_COUNT).l
.invalid_counted:
        btst    #15,d2
        beq.s   .rejected
        bsr.w   DmaCountRequiredDrop
        bra.s   .rejected

.overflow:
        cmpi.w  #$FFFF,(DEBUG_DMA_OVERFLOW_COUNT).l
        beq.s   .overflow_counted
        addq.w  #1,(DEBUG_DMA_OVERFLOW_COUNT).l
.overflow_counted:
        btst    #15,d2
        beq.s   .rejected
        bsr.w   DmaCountRequiredDrop
.rejected:
        moveq   #0,d0
        movem.l (a7)+,d3-d5/a2
        rts

; Publish depth last, after every entry is complete. Producers call this with
; VBlank masked as one short bounded transaction.
DmaQueuePublish:
        moveq   #0,d0
        move.w  (DMA_QUEUE_BUILD_DEPTH).l,d0
        cmpi.w  #DMA_QUEUE_CAPACITY,d0
        bls.s   .valid
        moveq   #0,d0
        cmpi.w  #$FFFF,(DEBUG_DMA_INVALID_COUNT).l
        beq.s   .valid
        addq.w  #1,(DEBUG_DMA_INVALID_COUNT).l
.valid:
        move.w  d0,(DEBUG_DMA_QUEUE_DEPTH).l
        move.w  #$0001,(DMA_QUEUE_FRAME_PENDING).l
        move.w  d0,(DMA_QUEUE_PUBLISHED_DEPTH).l
        rts

; VBlank-only consumer. Required records are drained first, then optional
; records, in stable insertion order within each of four priorities. Optional
; records that exceed the 2,048-word frame cap are compacted for a later frame.
DmaCommitPreparedQueue:
        clr.w   (DMA_QUEUE_FRAME_PENDING).l
        clr.w   (DMA_QUEUE_COMMIT_WORDS).l
        clr.w   (DEBUG_DMA_WORDS_FRAME).l
        moveq   #0,d7
        move.w  (DMA_QUEUE_PUBLISHED_DEPTH).l,d7
        cmpi.w  #DMA_QUEUE_CAPACITY,d7
        bls.s   .depth_valid
        cmpi.w  #$FFFF,(DEBUG_DMA_INVALID_COUNT).l
        beq.s   .discard_corrupt
        addq.w  #1,(DEBUG_DMA_INVALID_COUNT).l
.discard_corrupt:
        moveq   #0,d7
        clr.w   (DMA_QUEUE_PUBLISHED_DEPTH).l
.depth_valid:
        tst.w   d7
        beq.w   .finish_empty

        move.w  #VDP_REGISTER_DISPLAY_ON_DMA,(VDP_CONTROL).l

; Pass 1: required work, critical through low priority.
        moveq   #0,d6
.required_priority:
        lea     (DMA_QUEUE_ENTRIES).l,a2
        move.w  d7,d5
        subq.w  #1,d5
.required_entry:
        move.w  DMA_QUEUE_ENTRY_FLAGS(a2),d4
        btst    #15,d4
        beq.s   .required_next
        move.w  d4,d3
        andi.w  #DMA_PRIORITY_MASK,d3
        cmp.w   d6,d3
        bne.s   .required_next

        moveq   #0,d3
        move.w  (DMA_QUEUE_COMMIT_WORDS).l,d3
        moveq   #0,d4
        move.w  DMA_QUEUE_ENTRY_WORDS(a2),d4
        add.l   d4,d3
        cmpi.l  #DMA_ORDINARY_WORD_LIMIT,d3
        bhi.s   .required_drop

        move.w  d3,(DMA_QUEUE_COMMIT_WORDS).l
        ori.w   #DMA_FLAG_PROCESSED,DMA_QUEUE_ENTRY_FLAGS(a2)
        movea.l DMA_QUEUE_ENTRY_SOURCE(a2),a0
        moveq   #0,d0
        move.w  DMA_QUEUE_ENTRY_DESTINATION(a2),d0
        moveq   #0,d1
        move.w  DMA_QUEUE_ENTRY_WORDS(a2),d1
        bsr.w   DmaIssueVramTransfer
        bra.s   .required_next

.required_drop:
        ori.w   #DMA_FLAG_PROCESSED,DMA_QUEUE_ENTRY_FLAGS(a2)
        bsr.w   DmaCountRequiredDrop
.required_next:
        adda.w  #DMA_QUEUE_ENTRY_BYTES,a2
        dbf     d5,.required_entry
        addq.w  #1,d6
        cmpi.w  #DMA_PRIORITY_LEVELS,d6
        blo.w   .required_priority

; Pass 2: optional work in the same stable priority order.
        moveq   #0,d6
.optional_priority:
        lea     (DMA_QUEUE_ENTRIES).l,a2
        move.w  d7,d5
        subq.w  #1,d5
.optional_entry:
        move.w  DMA_QUEUE_ENTRY_FLAGS(a2),d4
        btst    #15,d4
        bne.s   .optional_next
        move.w  d4,d3
        andi.w  #DMA_PRIORITY_MASK,d3
        cmp.w   d6,d3
        bne.s   .optional_next

        moveq   #0,d3
        move.w  (DMA_QUEUE_COMMIT_WORDS).l,d3
        moveq   #0,d4
        move.w  DMA_QUEUE_ENTRY_WORDS(a2),d4
        add.l   d4,d3
        cmpi.l  #DMA_ORDINARY_WORD_LIMIT,d3
        bhi.s   .optional_defer

        move.w  d3,(DMA_QUEUE_COMMIT_WORDS).l
        ori.w   #DMA_FLAG_PROCESSED,DMA_QUEUE_ENTRY_FLAGS(a2)
        movea.l DMA_QUEUE_ENTRY_SOURCE(a2),a0
        moveq   #0,d0
        move.w  DMA_QUEUE_ENTRY_DESTINATION(a2),d0
        moveq   #0,d1
        move.w  DMA_QUEUE_ENTRY_WORDS(a2),d1
        bsr.w   DmaIssueVramTransfer
        bra.s   .optional_next

.optional_defer:
        cmpi.w  #$FFFF,DMA_QUEUE_ENTRY_AGE(a2)
        beq.s   .age_saturated
        addq.w  #1,DMA_QUEUE_ENTRY_AGE(a2)
.age_saturated:
        cmpi.w  #$FFFF,(DEBUG_DMA_DEFERRED_COUNT).l
        beq.s   .optional_next
        addq.w  #1,(DEBUG_DMA_DEFERRED_COUNT).l
.optional_next:
        adda.w  #DMA_QUEUE_ENTRY_BYTES,a2
        dbf     d5,.optional_entry
        addq.w  #1,d6
        cmpi.w  #DMA_PRIORITY_LEVELS,d6
        blo.w   .optional_priority

        move.w  #VDP_REGISTER_DISPLAY_ON,(VDP_CONTROL).l

; Compact every unprocessed optional record in original insertion order.
        lea     (DMA_QUEUE_ENTRIES).l,a2
        lea     (DMA_QUEUE_ENTRIES).l,a3
        moveq   #0,d4
        moveq   #0,d6
        move.w  d7,d5
        subq.w  #1,d5
.compact:
        move.w  DMA_QUEUE_ENTRY_FLAGS(a2),d3
        andi.w  #DMA_FLAG_PROCESSED,d3
        bne.s   .consumed
        move.l  (a2),(a3)
        move.l  $04(a2),$04(a3)
        move.l  $08(a2),$08(a3)
        moveq   #0,d3
        move.w  DMA_QUEUE_ENTRY_WORDS(a2),d3
        add.l   d3,d6
        addq.w  #1,d4
        adda.w  #DMA_QUEUE_ENTRY_BYTES,a3
.consumed:
        adda.w  #DMA_QUEUE_ENTRY_BYTES,a2
        dbf     d5,.compact

        move.w  d4,(DMA_QUEUE_PUBLISHED_DEPTH).l
        move.w  d4,(DMA_QUEUE_BUILD_DEPTH).l
        move.w  d4,(DEBUG_DMA_QUEUE_DEPTH).l
        move.l  d6,(DMA_QUEUE_BUILD_WORDS).l

        move.w  (DMA_QUEUE_COMMIT_WORDS).l,d0
        move.w  d0,(DEBUG_DMA_WORDS_FRAME).l
        cmp.w   (DEBUG_DMA_WORDS_MAX).l,d0
        bls.s   .complete
        move.w  d0,(DEBUG_DMA_WORDS_MAX).l
.complete:
        rts

.finish_empty:
        clr.w   (DMA_QUEUE_BUILD_DEPTH).l
        clr.l   (DMA_QUEUE_BUILD_WORDS).l
        clr.w   (DEBUG_DMA_QUEUE_DEPTH).l
        rts

; a0: source, d0.w: VRAM destination, d1.w: words. VBlank-only and already
; validated. Clobbers d0-d2/a0-a1.
DmaIssueVramTransfer:
        move.w  d1,d2
        andi.w  #$00FF,d2
        ori.w   #$9300,d2
        move.w  d2,(VDP_CONTROL).l
        move.w  d1,d2
        lsr.w   #8,d2
        andi.w  #$00FF,d2
        ori.w   #$9400,d2
        move.w  d2,(VDP_CONTROL).l

        move.l  a0,d2
        lsr.l   #1,d2
        move.w  d2,d1
        andi.w  #$00FF,d1
        ori.w   #$9500,d1
        move.w  d1,(VDP_CONTROL).l
        lsr.l   #8,d2
        move.w  d2,d1
        andi.w  #$00FF,d1
        ori.w   #$9600,d1
        move.w  d1,(VDP_CONTROL).l
        lsr.l   #8,d2
        andi.w  #$007F,d2
        ori.w   #$9700,d2
        move.w  d2,(VDP_CONTROL).l

; Construct a VRAM write command and set CD5 for memory-to-VDP DMA.
        moveq   #0,d1
        move.w  d0,d1
        andi.l  #$00003FFF,d0
        ori.l   #$00004000,d0
        swap    d0
        lsr.l   #8,d1
        lsr.l   #6,d1
        andi.l  #$00000003,d1
        ori.l   #$00000080,d1
        or.l    d1,d0
        move.l  d0,(VDP_CONTROL).l

        move.w  #DMA_BUSY_TIMEOUT-1,d1
.wait_busy:
        move.w  (VDP_CONTROL).l,d0
        andi.w  #$0002,d0
        beq.s   .complete
        dbf     d1,.wait_busy
        cmpi.w  #$FFFF,(DEBUG_VBLANK_OVERRUN_COUNT).l
        beq.s   .complete
        addq.w  #1,(DEBUG_VBLANK_OVERRUN_COUNT).l
.complete:
        rts

DmaCountRequiredDrop:
        cmpi.w  #$FFFF,(DEBUG_DMA_REQUIRED_DROP_COUNT).l
        beq.s   .complete
        addq.w  #1,(DEBUG_DMA_REQUIRED_DROP_COUNT).l
.complete:
        rts
