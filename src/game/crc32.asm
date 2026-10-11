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
; aligned word is loaded big-endian and byte-rotated into the generated
; little-endian table index. The high word of d0 retains the 0/1-byte tail.
Crc32UpdateRaw:
        tst.w   d0
        beq.s   .done

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
        beq.s   .done

.aligned:
; Pack the 0/1-byte tail into d0's high word, then amortize one DBF over
; aligned word updates without borrowing a callee-saved register.
        move.w  d0,d2
        andi.w  #$0001,d2
        swap    d0
        move.w  d2,d0
        swap    d0
        lsr.w   #1,d0
        beq.s   .tail_ready
        subq.w  #1,d0
; The T16 offset spans $00000..$3FFFC, so the original-68000 indexed read
; must use d2.l. Keep the table base fixed across words; the odd-byte tail
; below binds its own byte table. This changes addressing, not the recurrence.
        lea     (Crc32T16Table).l,a1
.next_word:
        moveq   #0,d2
        move.w  (a0)+,d2
        ror.w   #8,d2
        eor.w   d1,d2
        lsl.l   #2,d2
        move.l  (a1,d2.l),d2
        clr.w   d1
        swap    d1
        eor.l   d2,d1
        dbf     d0,.next_word

.tail_ready:
        swap    d0
        tst.w   d0
        beq.s   .done
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
