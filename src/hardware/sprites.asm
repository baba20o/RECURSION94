; One-sprite Milestone 2 renderer. Logical coordinates are screen pixels;
; hardware SAT coordinates include the Genesis +128 bias.

SPRITE_STATE_BASE               equ     SAT_SHADOW+SAT_SHADOW_BYTES
SPRITE_STATE_X                  equ     SPRITE_STATE_BASE+$00
SPRITE_STATE_Y                  equ     SPRITE_STATE_BASE+$02
SPRITE_STATE_END                equ     SPRITE_STATE_BASE+$04

SPRITE_INITIAL_X                equ     156
SPRITE_INITIAL_Y                equ     176
SPRITE_MAX_X                    equ     312
SPRITE_MAX_Y                    equ     216
SPRITE_SPEED                    equ     2
SPRITE_COORDINATE_BIAS          equ     128
SPRITE_TILE_ATTRIBUTE           equ     DIAGNOSTIC_PALETTE_BITS+DIAGNOSTIC_PULSE_TILE
SPRITE_DMA_FLAGS                equ     DMA_FLAG_REQUIRED+DMA_PRIORITY_CRITICAL

        assert  SPRITE_STATE_END<=DMA_QUEUE_BASE,"sprite state must remain in render RAM"

SpriteInitialize:
        lea     (SAT_SHADOW).l,a0
        moveq   #0,d0
        move.w  #SAT_SHADOW_WORDS-1,d1
.clear_shadow:
        move.w  d0,(a0)+
        dbf     d1,.clear_shadow

        move.w  #SPRITE_INITIAL_X,(SPRITE_STATE_X).l
        move.w  #SPRITE_INITIAL_Y,(SPRITE_STATE_Y).l
        bsr.w   SpriteWriteSatEntry

        move.w  #$0001,(DEBUG_SPRITE_COUNT).l
        move.w  #$0001,(DEBUG_SPRITE_SCANLINE_MAX).l
        ori.w   #M2_FLAG_SPRITE_ACTIVE,(DEBUG_M2_FLAGS).l

        bsr.w   DmaQueueBeginFrame
        lea     (SAT_SHADOW).l,a0
        move.w  #VRAM_SAT,d0
        move.w  #SAT_SHADOW_WORDS,d1
        move.w  #SPRITE_DMA_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        beq.s   .complete
        bsr.w   DmaQueuePublish
.complete:
        rts

; Main-side render producer, called once per consumed frame token. The short
; mask-7 section makes SAT writes and queue publication one atomic transaction
; relative to VBlank. A backlog token never overwrites already-published work.
SpriteUpdateAndQueue:
        move.w  #$2700,sr
        tst.w   (DMA_QUEUE_FRAME_PENDING).l
        bne.w   .complete

        moveq   #0,d0
        move.b  (INPUT_HELD).l,d0
        move.w  (SPRITE_STATE_X).l,d1
        move.w  (SPRITE_STATE_Y).l,d2

        btst    #2,d0
        beq.s   .not_left
        cmpi.w  #SPRITE_SPEED,d1
        bhs.s   .move_left
        moveq   #0,d1
        bra.s   .not_left
.move_left:
        subq.w  #SPRITE_SPEED,d1
.not_left:
        btst    #3,d0
        beq.s   .not_right
        cmpi.w  #SPRITE_MAX_X-SPRITE_SPEED,d1
        bls.s   .move_right
        move.w  #SPRITE_MAX_X,d1
        bra.s   .not_right
.move_right:
        addq.w  #SPRITE_SPEED,d1
.not_right:

        btst    #0,d0
        beq.s   .not_up
        cmpi.w  #SPRITE_SPEED,d2
        bhs.s   .move_up
        moveq   #0,d2
        bra.s   .not_up
.move_up:
        subq.w  #SPRITE_SPEED,d2
.not_up:
        btst    #1,d0
        beq.s   .not_down
        cmpi.w  #SPRITE_MAX_Y-SPRITE_SPEED,d2
        bls.s   .move_down
        move.w  #SPRITE_MAX_Y,d2
        bra.s   .not_down
.move_down:
        addq.w  #SPRITE_SPEED,d2
.not_down:

        move.w  d1,(SPRITE_STATE_X).l
        move.w  d2,(SPRITE_STATE_Y).l
        bsr.w   SpriteWriteSatEntry

        bsr.w   DmaQueueBeginFrame
        lea     (SAT_SHADOW).l,a0
        move.w  #VRAM_SAT,d0
        move.w  #SAT_SHADOW_WORDS,d1
        move.w  #SPRITE_DMA_FLAGS,d2
        bsr.w   DmaQueueEnqueue
        tst.l   d0
        beq.s   .complete
        bsr.w   DmaQueuePublish

.complete:
        move.w  #$2500,sr
        rts

SpriteWriteSatEntry:
        moveq   #0,d0
        move.w  (SPRITE_STATE_Y).l,d0
        addi.w  #SPRITE_COORDINATE_BIAS,d0
        move.w  d0,(SAT_SHADOW+$00).l
        clr.w   (SAT_SHADOW+$02).l
        move.w  #SPRITE_TILE_ATTRIBUTE,(SAT_SHADOW+$04).l
        moveq   #0,d0
        move.w  (SPRITE_STATE_X).l,d0
        addi.w  #SPRITE_COORDINATE_BIAS,d0
        move.w  d0,(SAT_SHADOW+$06).l
        rts
