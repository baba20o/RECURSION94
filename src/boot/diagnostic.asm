; Original compact 1bpp diagnostic font and palette. Font rows are expanded to
; native 4bpp tiles during display-off initialization.

VdpLoadDiagnostic:
        bsr.w   VdpLoadDiagnosticPalette
        bsr.w   VdpLoadDiagnosticTiles
        bsr.w   VdpFillDiagnosticBackground
        bsr.w   VdpDrawDiagnosticText
        rts

VdpLoadDiagnosticPalette:
        move.l  #VDP_COMMAND_CRAM_WRITE_0,(VDP_CONTROL).l
        lea     DiagnosticPalette(pc),a0
        moveq   #63,d0
.copy:
        move.w  (a0)+,(VDP_DATA).l
        dbf     d0,.copy
        rts

VdpLoadDiagnosticTiles:
        movem.l d3-d4,-(a7)
        move.l  #VDP_COMMAND_FONT_WRITE,(VDP_CONTROL).l
        lea     DiagnosticFont1bpp(pc),a0
        move.w  #DIAGNOSTIC_FONT_GLYPHS-1,d3
.next_glyph:
        moveq   #7,d2
.next_row:
        moveq   #0,d0
        move.b  (a0)+,d0
        moveq   #0,d1
        moveq   #7,d4
.next_pixel:
        lsl.l   #4,d1
        lsl.b   #1,d0
        bcc.s   .transparent
        ori.b   #$01,d1
.transparent:
        dbf     d4,.next_pixel
        move.l  d1,(VDP_DATA).l
        dbf     d2,.next_row
        dbf     d3,.next_glyph

        lea     DiagnosticSpecialTiles(pc),a0
        moveq   #15,d0
.copy_special:
        move.l  (a0)+,(VDP_DATA).l
        dbf     d0,.copy_special
        movem.l (a7)+,d3-d4
        rts

VdpFillDiagnosticBackground:
        moveq   #0,d2
.next_row:
        move.w  d2,d0
        lsl.w   #7,d0
        addi.w  #VRAM_PLANE_B,d0
        bsr.w   VdpSetVramWriteAddress
        moveq   #VISIBLE_WIDTH_CELLS-1,d1
.next_cell:
        move.w  #DIAGNOSTIC_GRID_TILE,(VDP_DATA).l
        dbf     d1,.next_cell
        addq.w  #1,d2
        cmpi.w  #VISIBLE_HEIGHT_CELLS,d2
        blo.s   .next_row
        rts

VdpDrawDiagnosticText:
        lea     DiagnosticTitle(pc),a0
        moveq   #7,d0
        moveq   #14,d1
        bsr.w   VdpWriteDiagnosticText

        lea     DiagnosticBootLine(pc),a0
        moveq   #10,d0
        moveq   #10,d1
        bsr.w   VdpWriteDiagnosticText

        lea     DiagnosticVideoLine(pc),a0
        moveq   #12,d0
        moveq   #11,d1
        bsr.w   VdpWriteDiagnosticText

        lea     DiagnosticVBlankLine(pc),a0
        moveq   #14,d0
        moveq   #12,d1
        bsr.w   VdpWriteDiagnosticText

        lea     DiagnosticTagline(pc),a0
        moveq   #18,d0
        moveq   #9,d1
        bsr.w   VdpWriteDiagnosticText

        move.w  #VRAM_PLANE_A+(14*PLANE_ROW_BYTES)+(30*2),d0
        bsr.w   VdpSetVramWriteAddress
        move.w  #DIAGNOSTIC_PALETTE_BITS+DIAGNOSTIC_PULSE_TILE,(VDP_DATA).l
        rts

; a0: zero-terminated uppercase text, d0.w: row, d1.w: column.
; Clobbers d0-d2/a0.
VdpWriteDiagnosticText:
        move.w  d0,d2
        lsl.w   #7,d2
        add.w   d1,d1
        add.w   d1,d2
        addi.w  #VRAM_PLANE_A,d2
        move.w  d2,d0
        bsr.w   VdpSetVramWriteAddress
        move.w  #VISIBLE_WIDTH_CELLS-1,d2
.next_character:
        moveq   #0,d0
        move.b  (a0)+,d0
        beq.s   .done
        cmpi.b  #$20,d0
        beq.s   .blank
        cmpi.b  #$30,d0
        blo.s   .blank
        cmpi.b  #$39,d0
        bhi.s   .try_letter
        subi.b  #$30,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        bra.s   .attribute
.try_letter:
        cmpi.b  #$41,d0
        blo.s   .blank
        cmpi.b  #$5A,d0
        bhi.s   .blank
        subi.b  #$41,d0
        addi.w  #DIAGNOSTIC_LETTER_A_TILE,d0
        bra.s   .attribute
.blank:
        moveq   #0,d0
.attribute:
        ori.w   #DIAGNOSTIC_PALETTE_BITS,d0
.emit:
        move.w  d0,(VDP_DATA).l
        dbf     d2,.next_character
.done:
        rts

DiagnosticPalette:
; Palette 0: black field and a quiet blue-grey dot grid.
        dc.w    $0000,$0000,$0222,$0000,$0000,$0000,$0000,$0000
        dc.w    $0000,$0000,$0000,$0000,$0000,$0000,$0000,$0000
; Palette 1: reserved current-actor palette.
        dcb.w   16,$0000
; Palette 2: reserved echo/identity palette.
        dcb.w   16,$0000
; Palette 3: diagnostic text and heartbeat.
        dc.w    $0000,$0EEA,$0444,$0666,$0888,$0AAA,$0CCC,$0EEE
        dc.w    $000E,$002E,$004E,$006E,$008E,$00AE,$0EE0,$0EEE
DiagnosticPaletteEnd:

        assert  DiagnosticPaletteEnd-DiagnosticPalette=128,"diagnostic palette must fill CRAM"

; Tiles 1-10: digits 0-9. Tiles 11-36: letters A-Z.
DiagnosticFont1bpp:
        dc.b    $70,$88,$98,$A8,$C8,$88,$70,$00       ; 0
        dc.b    $20,$60,$20,$20,$20,$20,$70,$00       ; 1
        dc.b    $70,$88,$08,$10,$20,$40,$F8,$00       ; 2
        dc.b    $F0,$08,$08,$70,$08,$08,$F0,$00       ; 3
        dc.b    $10,$30,$50,$90,$F8,$10,$10,$00       ; 4
        dc.b    $F8,$80,$80,$F0,$08,$08,$F0,$00       ; 5
        dc.b    $70,$80,$80,$F0,$88,$88,$70,$00       ; 6
        dc.b    $F8,$08,$10,$20,$40,$40,$40,$00       ; 7
        dc.b    $70,$88,$88,$70,$88,$88,$70,$00       ; 8
        dc.b    $70,$88,$88,$78,$08,$08,$70,$00       ; 9

        dc.b    $70,$88,$88,$F8,$88,$88,$88,$00       ; A
        dc.b    $F0,$88,$88,$F0,$88,$88,$F0,$00       ; B
        dc.b    $78,$80,$80,$80,$80,$80,$78,$00       ; C
        dc.b    $F0,$88,$88,$88,$88,$88,$F0,$00       ; D
        dc.b    $F8,$80,$80,$F0,$80,$80,$F8,$00       ; E
        dc.b    $F8,$80,$80,$F0,$80,$80,$80,$00       ; F
        dc.b    $78,$80,$80,$B8,$88,$88,$78,$00       ; G
        dc.b    $88,$88,$88,$F8,$88,$88,$88,$00       ; H
        dc.b    $F8,$20,$20,$20,$20,$20,$F8,$00       ; I
        dc.b    $38,$08,$08,$08,$08,$88,$70,$00       ; J
        dc.b    $88,$90,$A0,$C0,$A0,$90,$88,$00       ; K
        dc.b    $80,$80,$80,$80,$80,$80,$F8,$00       ; L
        dc.b    $88,$D8,$A8,$A8,$88,$88,$88,$00       ; M
        dc.b    $88,$C8,$A8,$98,$88,$88,$88,$00       ; N
        dc.b    $70,$88,$88,$88,$88,$88,$70,$00       ; O
        dc.b    $F0,$88,$88,$F0,$80,$80,$80,$00       ; P
        dc.b    $70,$88,$88,$88,$A8,$90,$68,$00       ; Q
        dc.b    $F0,$88,$88,$F0,$A0,$90,$88,$00       ; R
        dc.b    $78,$80,$80,$70,$08,$08,$F0,$00       ; S
        dc.b    $F8,$20,$20,$20,$20,$20,$20,$00       ; T
        dc.b    $88,$88,$88,$88,$88,$88,$70,$00       ; U
        dc.b    $88,$88,$88,$88,$88,$50,$20,$00       ; V
        dc.b    $88,$88,$88,$88,$A8,$D8,$88,$00       ; W
        dc.b    $88,$88,$50,$20,$50,$88,$88,$00       ; X
        dc.b    $88,$88,$50,$20,$20,$20,$20,$00       ; Y
        dc.b    $F8,$08,$10,$20,$40,$80,$F8,$00       ; Z
DiagnosticFont1bppEnd:

        assert  DiagnosticFont1bppEnd-DiagnosticFont1bpp=DIAGNOSTIC_FONT_GLYPHS*8,"diagnostic font size mismatch"

DiagnosticSpecialTiles:
; Tile 37: heartbeat block using palette color 14.
        dcb.l   8,$EEEEEEEE
; Tile 38: sparse grid point using palette color 2.
        dc.l    $20000000,$00000000,$00000000,$00000000
        dc.l    $00000000,$00000000,$00000000,$00000000
DiagnosticSpecialTilesEnd:

        assert  DiagnosticSpecialTilesEnd-DiagnosticSpecialTiles=64,"special tile size mismatch"

DiagnosticTitle:
        dc.b    "RECURSION 94",0
DiagnosticBootLine:
        dc.b    "INPUT LINK READY",0
DiagnosticVideoLine:
        dc.b    "SPRITE LINK READY",0
DiagnosticVBlankLine:
        dc.b    "DMA QUEUE READY",0
DiagnosticTagline:
        dc.b    "THE FUTURE HID IN 1994",0
        even
