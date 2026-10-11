; Bounded CPU-port VDP bring-up. Runtime DMA is enabled only around VBlank
; queue commits.

VDP_REGISTER_FATAL_DISPLAY     equ     $8144
VDP_COMMAND_FATAL_TEXT_COLOR   equ     $C0620000
VDP_FATAL_WAIT                 equ     $1FFF
VDP_PLANE_TABLE_WORDS          equ     PLANE_WIDTH_CELLS*32

VdpInitialize:
; Reset the control-port command latch, then establish all registers 0-18
; while display and interrupts are disabled.
        move.w  (VDP_CONTROL).l,d0
        lea     VdpInitialRegisters(pc),a0
        move.w  #18,d0
.write_registers:
        move.w  (a0)+,(VDP_CONTROL).l
        dbf     d0,.write_registers

; Clear all 64 KiB VRAM.
        move.l  #VDP_COMMAND_VRAM_WRITE_0,(VDP_CONTROL).l
        moveq   #0,d0
        move.w  #$7FFF,d1
.clear_vram:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear_vram

; Clear all 128 bytes CRAM.
        move.l  #VDP_COMMAND_CRAM_WRITE_0,(VDP_CONTROL).l
        moveq   #63,d1
.clear_cram:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear_cram

; Clear all 80 bytes VSRAM.
        move.l  #VDP_COMMAND_VSRAM_WRITE_0,(VDP_CONTROL).l
        moveq   #39,d1
.clear_vsram:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear_vsram
        rts

; d0.w: even VRAM byte address. Clobbers d0-d1.
VdpSetVramWriteAddress:
        moveq   #0,d1
        move.w  d0,d1
        andi.l  #$00003FFF,d0
        ori.l   #$00004000,d0
        swap    d0
        lsr.l   #8,d1
        lsr.l   #6,d1
        andi.l  #$00000003,d1
        or.l    d1,d0
        move.l  d0,(VDP_CONTROL).l
        rts

; Post-video fatal renderer. Interrupts are already masked by SafeException.
; It never waits indefinitely and uses the resident diagnostic font.
VdpShowFatalScreen:
        move.w  #VDP_FATAL_WAIT-1,d0
.wait_for_dma:
        move.w  (VDP_CONTROL).l,d1
        andi.w  #$0002,d1
        beq.s   .dma_idle
        dbf     d0,.wait_for_dma
.dma_idle:
        move.w  #VDP_REGISTER_DISPLAY_OFF,(VDP_CONTROL).l

        move.w  #VRAM_PLANE_A,d0
        bsr.w   VdpSetVramWriteAddress
        moveq   #0,d0
        move.w  #VDP_PLANE_TABLE_WORDS-1,d1
.clear_plane_a:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear_plane_a

        move.w  #VRAM_PLANE_B,d0
        bsr.w   VdpSetVramWriteAddress
        moveq   #0,d0
        move.w  #VDP_PLANE_TABLE_WORDS-1,d1
.clear_plane_b:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear_plane_b

        move.w  #VRAM_SAT,d0
        bsr.w   VdpSetVramWriteAddress
        moveq   #0,d0
        move.w  #SAT_SHADOW_WORDS-1,d1
.clear_sat:
        move.w  d0,(VDP_DATA).l
        dbf     d1,.clear_sat

        move.l  #VDP_COMMAND_CRAM_WRITE_0,(VDP_CONTROL).l
        move.w  #$000E,(VDP_DATA).l
        move.l  #VDP_COMMAND_FATAL_TEXT_COLOR,(VDP_CONTROL).l
        move.w  #$0EEE,(VDP_DATA).l

        lea     FatalTitle(pc),a0
        moveq   #12,d0
        moveq   #13,d1
        bsr.w   VdpWriteDiagnosticText
        lea     FatalCodeLine(pc),a0
        moveq   #14,d0
        moveq   #16,d1
        bsr.w   VdpWriteDiagnosticText
        move.w  #VDP_REGISTER_FATAL_DISPLAY,(VDP_CONTROL).l
        rts

VdpInitialRegisters:
        dc.w    $8004                   ; H-int off, mode 5
        dc.w    VDP_REGISTER_DISPLAY_OFF
        dc.w    $8230                   ; Plane A at $C000
        dc.w    $8334                   ; Window at $D000
        dc.w    $8407                   ; Plane B at $E000
        dc.w    $8578                   ; SAT at $F000 (H40 aligned)
        dc.w    $8600
        dc.w    $8700                   ; Backdrop palette 0, color 0
        dc.w    $8800
        dc.w    $8900
        dc.w    $8A00                   ; H-int counter, interrupt disabled
        dc.w    $8B00                   ; Full-screen H/V scroll
        dc.w    $8C81                   ; H40, no interlace, no shadow
        dc.w    $8D3F                   ; H-scroll table at $FC00
        dc.w    $8E00
        dc.w    $8F02                   ; Word auto-increment
        dc.w    $9001                   ; 64x32 scroll planes
        dc.w    $9100                   ; Window disabled at left edge
        dc.w    $9200                   ; Window disabled at top edge

FatalTitle:
        dc.b    "FATAL EXCEPTION",0
FatalCodeLine:
        dc.b    "CODE 0001",0
        even
