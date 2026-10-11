; Campaign projection publication.
; Requires game/engine.asm, gameplay/gameplay.asm, and gameplay/vocabulary.asm.

CAMPAIGN_PROJECTION_SCHEMA_VERSION equ $0002
CAMPAIGN_PROJECTION_SCHEMA_BYTES equ ENGINE_PROJECTION_SCHEMA_BYTES+VOCAB_PROJECTION_BYTES

; EngineBuildProjection hashes the frozen v1/base-length header before it
; returns its finalized CRC.  The campaign schema changes only those four
; header bytes, at the same fixed base-prefix length.  CRC linearity therefore
; permits this independently generated XOR correction before the 256-byte
; vocabulary tail is streamed into the unfinalized state.  This is a schema
; transform, never a checkpoint-result shortcut; host tests derive it again
; from the two headers and randomized payloads.
CAMPAIGN_PROJECTION_HEADER_CRC_XOR equ $59BED1EF

; GameplayBuildProjection
; Publishes the legacy engine schema once, appends the contiguous projected
; vocabulary/campaign prefix, then recomputes the authoritative CRC over the
; complete schema. EngineBuildProjection remains callable by legacy fixtures.
; Out: d0.w=length, d1.l=CRC, a0=ENGINE_PROJECTION_BASE; zeroes on failure.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayBuildProjection:
        movem.l d3-d7/a2-a6,-(a7)
        cmpi.l  #VOCAB_MAGIC_VALUE,(VOCAB_MAGIC).l
        bne.s   .invalid
        move.w  (VOCAB_FLAGS).l,d0
        andi.w  #VOCAB_FLAG_INITIALIZED,d0
        beq.s   .invalid

        bsr.w   EngineBuildProjection
        cmpi.w  #ENGINE_PROJECTION_SCHEMA_BYTES,d0
        bne.s   .invalid

        move.w  d0,d3
        move.l  d1,d5
        lea     (ENGINE_PROJECTION_BASE+ENGINE_PROJECTION_SCHEMA_BYTES).l,a0
        bsr.w   VocabularyBuildProjectionTail
        add.w   d0,d3
        cmpi.w  #CAMPAIGN_PROJECTION_SCHEMA_BYTES,d3
        bne.s   .invalid
        cmpi.w  #ENGINE_PROJECTION_END-ENGINE_PROJECTION_BASE,d3
        bhi.s   .invalid

        move.w  #CAMPAIGN_PROJECTION_SCHEMA_VERSION,(ENGINE_PROJECTION_BASE+$04).l
        move.w  d3,(ENGINE_PROJECTION_BASE+$06).l
        move.w  d3,(ENGINE_PROJECTION_LENGTH).l
; Convert the finalized base CRC back to its raw state, correct the two fixed
; header words at the frozen base-prefix length, then hash only the appended
; authoritative tail.  Rehashing all 3,894 bytes here exceeded one NTSC frame.
        move.l  d5,d1
        not.l   d1
        eori.l  #CAMPAIGN_PROJECTION_HEADER_CRC_XOR,d1
        lea     (ENGINE_PROJECTION_BASE+ENGINE_PROJECTION_SCHEMA_BYTES).l,a0
        move.w  #VOCAB_PROJECTION_BYTES,d0
        bsr.w   Crc32UpdateRaw
        not.l   d1
        move.l  d1,d4
        move.l  d4,(ENGINE_STATE_CRC).l
        move.l  d4,d1
        move.w  d3,d0
        lea     (ENGINE_PROJECTION_BASE).l,a0
        bra.s   .done

.invalid:
        andi.w  #$FFFD,(ENGINE_FLAGS).l
        move.w  #ENGINE_ERROR_PROJECTION_SIZE,(ENGINE_LAST_ERROR).l
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        clr.w   (ENGINE_PROJECTION_LENGTH).l
        moveq   #0,d0
        moveq   #0,d1
        lea     (ENGINE_PROJECTION_BASE).l,a0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

        assert  CAMPAIGN_PROJECTION_SCHEMA_BYTES<=$1000,"campaign projection exceeds engine projection RAM"
        assert  ENGINE_PROJECTION_BASE+CAMPAIGN_PROJECTION_SCHEMA_BYTES<=ENGINE_PROJECTION_END,"campaign projection address overflow"
        assert  ENGINE_PROJECTION_SCHEMA_BYTES=$0E36,"campaign CRC header correction base length drift"
        assert  VOCAB_PROJECTION_BYTES=$0100,"campaign CRC continuation tail length drift"
        assert  CAMPAIGN_PROJECTION_SCHEMA_BYTES=$0F36,"campaign CRC header correction total length drift"
