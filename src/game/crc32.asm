; CRC-32/ISO-HDLC (CRC-32/ADCCP), reflected polynomial $EDB88320.

; Crc32IsoHdlc
; In:  a0=byte stream, d0.w=length (0..$FFFF).
; Out: d0.l=CRC. Clobbers d0-d2/a0; preserves every callee-saved register.
Crc32IsoHdlc:
        move.l  a1,-(a7)
        move.l  #$FFFFFFFF,d1
        bsr.w   Crc32UpdateRaw
        not.l   d1
        move.l  d1,d0
        movea.l (a7)+,a1
        rts

; Crc32UpdateRaw
; In:  a0=byte stream, d0.w=length (0..$FFFF), d1.l=unfinalized CRC.
; Out: a0 advanced, d1.l=unfinalized CRC. Clobbers d0/d2/a1.
; An odd input address consumes one byte before the aligned T16 loop. Each
; aligned word uses the generated big-endian T16 index. Only inside a word
; loop, d1 is T(raw): bytes swapped within each 16-bit halfword. T commutes
; with XOR and the 16-bit shift; entry/exit restore the ordinary raw-CRC ABI.
; Separate even/odd loops avoid a per-call tail flag or callee-saved scratch.
Crc32UpdateRaw:
        tst.w   d0
        beq.w   .done

        move.l  a0,d2
        btst    #0,d2
        beq.s   .aligned
; Preserve the arbitrary-pointer ABI: a 68000 word read may never be odd.
        moveq   #0,d2
        move.b  (a0)+,d2
        eor.b   d1,d2
        lsl.w   #2,d2
        lsr.l   #8,d1
        lea     (Crc32ByteTable).l,a1
        move.l  (a1,d2.w),d2
        eor.l   d2,d1
        subq.w  #1,d0
        beq.w   .done

.aligned:
; A nonzero even byte count implies at least one word. Odd counts use a
; separate exit, including the one-byte case that must not transform d1.
        lsr.w   #1,d0
        bcc.s   .even_length
        bra.w   .odd_length
.even_length:
        subq.w  #1,d0
        ror.w   #8,d1
        swap    d1
        ror.w   #8,d1
        swap    d1
        lea     (Crc32T16Table).l,a1
; Split N-1 twice: d0 becomes ceil(N/4)-1 and the two carry bits select
; the first 1..4 updates. Every later iteration executes exactly four.
        lsr.w   #1,d0
        bcs.s   .even_even_words
        lsr.w   #1,d0
        bcs.s   .even_word1
        bra.s   .even_word3
.even_even_words:
        lsr.w   #1,d0
        bcs.s   .even_word0
        bra.s   .even_word2
.even_word0:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
.even_word1:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
.even_word2:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
.even_word3:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
        dbf     d0,.even_word0
        ror.w   #8,d1
        swap    d1
        ror.w   #8,d1
        swap    d1
        rts

.odd_length:
        tst.w   d0
        beq.w   .byte_tail
        subq.w  #1,d0
        ror.w   #8,d1
        swap    d1
        ror.w   #8,d1
        swap    d1
        lea     (Crc32T16Table).l,a1
        lsr.w   #1,d0
        bcs.s   .odd_even_words
        lsr.w   #1,d0
        bcs.s   .odd_word1
        bra.s   .odd_word3
.odd_even_words:
        lsr.w   #1,d0
        bcs.s   .odd_word0
        bra.s   .odd_word2
.odd_word0:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
.odd_word1:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
.odd_word2:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
.odd_word3:
        moveq   #0,d2
        move.w  (a0)+,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
        dbf     d0,.odd_word0
        ror.w   #8,d1
        swap    d1
        ror.w   #8,d1
        swap    d1
.byte_tail:
        moveq   #0,d2
        move.b  (a0)+,d2
        eor.b   d1,d2
        lsl.w   #2,d2
        lsr.l   #8,d1
        lea     (Crc32ByteTable).l,a1
        move.l  (a1,d2.w),d2
        eor.l   d2,d1
.done:
        rts

; Public test vector data. The routine contains no expected-result shortcut.
Crc32KnownVector:
        dc.b    "123456789"
Crc32KnownVectorEnd:
        even
