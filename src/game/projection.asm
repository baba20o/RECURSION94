; Explicit canonical big-endian state projection and CRC publication.

; EngineBuildProjection
; Out: d0.w=projection length, d1.l=CRC, a0=ENGINE_PROJECTION_BASE.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineBuildProjection:
        movem.l d3-d7/a2-a6,-(a7)
        ori.w   #ENGINE_FLAG_PROJECTION_VALID,(ENGINE_FLAGS).l
        lea     (ENGINE_PROJECTION_BASE).l,a1

        move.l  #PROJECTION_MAGIC_VALUE,(a1)+
        move.w  #PROJECTION_SCHEMA_VERSION,(a1)+
; The fixed schema length is known before publication. Writing it now permits
; the CRC to stream in projection order while the pool records are serialized.
        move.w  #ENGINE_PROJECTION_SCHEMA_BYTES,(a1)+

        move.w  (ENGINE_ENCOUNTER_ID).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_ATTEMPT).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_TICK).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_STATE).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_TERMINAL_REASON).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_FLAGS).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_OVERFLOW_FLAGS).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_LAST_ERROR).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_ROOM_LEFT).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_ROOM_TOP).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_ROOM_RIGHT).l,d0
        move.w  d0,(a1)+
        move.w  (ENGINE_ROOM_BOTTOM).l,d0
        move.w  d0,(a1)+
        move.b  (ENGINE_INPUT_HELD).l,d0
        move.b  d0,(a1)+
        move.b  (ENGINE_INPUT_PRESSED).l,d0
        move.b  d0,(a1)+
        move.b  (ENGINE_INPUT_RELEASED).l,d0
        move.b  d0,(a1)+
        move.b  (ENGINE_INPUT_PREVIOUS).l,d0
        move.b  d0,(a1)+
        move.b  (ENGINE_FACING_X).l,d0
        move.b  d0,(a1)+
        move.b  (ENGINE_ACTIVE_ACTOR_COUNT).l,d0
        move.b  d0,(a1)+
        move.w  (ENGINE_RECT_COUNT).l,d0
        move.w  d0,(a1)+

; Actors are serialized slot-by-slot in ascending physical index. Reserved
; bytes are deliberately omitted.
        lea     (ENGINE_ACTOR_BASE).l,a2
        moveq   #ACTOR_CAPACITY-1,d3
.actor:
        move.b  (ACTOR_ACTIVE,a2),d0
        move.b  d0,(a1)+
        move.b  (ACTOR_ROLE,a2),d0
        move.b  d0,(a1)+
        move.w  (ACTOR_FLAGS,a2),d0
        move.w  d0,(a1)+
        move.l  (ACTOR_X,a2),d0
        move.l  d0,(a1)+
        move.l  (ACTOR_Y,a2),d0
        move.l  d0,(a1)+
        move.l  (ACTOR_VX,a2),d0
        move.l  d0,(a1)+
        move.l  (ACTOR_VY,a2),d0
        move.l  d0,(a1)+
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        move.w  d0,(a1)+
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        move.w  d0,(a1)+
        move.b  (ACTOR_INPUT,a2),d0
        move.b  d0,(a1)+
        move.b  (ACTOR_PREVIOUS_INPUT,a2),d0
        move.b  d0,(a1)+
        move.w  (ACTOR_HEALTH,a2),d0
        move.w  d0,(a1)+
        move.w  (ACTOR_LOGICAL_ID,a2),d0
        move.w  d0,(a1)+
        adda.w  #ACTOR_BYTES,a2
        dbf     d3,.actor

; Hash the fixed header and actor prefix once. EngineProjectPool then returns
; the raw CRC in d2 after hashing each pool header, active record, and
; coalesced inactive run during the same bounded serialization pass.
        lea     (ENGINE_PROJECTION_BASE).l,a0
        move.w  #PROJECTION_HEADER_BYTES+(ACTOR_CAPACITY*ACTOR_PROJECTION_BYTES),d0
        move.l  #$FFFFFFFF,d1
        bsr.w   Crc32UpdateRaw
        move.l  d1,d2
        movea.l a0,a1

        lea     (ENEMY_POOL).l,a0
        move.w  #ENEMY_CAPACITY,d0
        moveq   #POOL_TYPE_ENEMY,d1
        bsr.w   EngineProjectPool
        lea     (PROJECTILE_POOL).l,a0
        move.w  #PROJECTILE_CAPACITY,d0
        moveq   #POOL_TYPE_PROJECTILE,d1
        bsr.w   EngineProjectPool
        lea     (DEVICE_POOL).l,a0
        move.w  #DEVICE_CAPACITY,d0
        moveq   #POOL_TYPE_DEVICE,d1
        bsr.w   EngineProjectPool
        lea     (CARRYABLE_POOL).l,a0
        move.w  #CARRYABLE_CAPACITY,d0
        moveq   #POOL_TYPE_CARRYABLE,d1
        bsr.w   EngineProjectPool
        lea     (HAZARD_POOL).l,a0
        move.w  #HAZARD_CAPACITY,d0
        moveq   #POOL_TYPE_HAZARD,d1
        bsr.w   EngineProjectPool
        lea     (BOSS_COMPONENT_POOL).l,a0
        move.w  #BOSS_COMPONENT_CAPACITY,d0
        moveq   #POOL_TYPE_BOSS,d1
        bsr.w   EngineProjectPool
        lea     (EFFECT_POOL).l,a0
        move.w  #EFFECT_CAPACITY,d0
        moveq   #POOL_TYPE_EFFECT,d1
        bsr.w   EngineProjectPool

        move.l  a1,d3
        sub.l   #ENGINE_PROJECTION_BASE,d3
        cmpi.l  #ENGINE_PROJECTION_SCHEMA_BYTES,d3
        beq.s   .size_valid
        andi.w  #$FFFD,(ENGINE_FLAGS).l
        move.w  #ENGINE_ERROR_PROJECTION_SIZE,(ENGINE_LAST_ERROR).l
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        clr.w   (ENGINE_PROJECTION_LENGTH).l
        moveq   #0,d0
        moveq   #0,d1
        lea     (ENGINE_PROJECTION_BASE).l,a0
        bra.s   .done

.size_valid:
        move.w  d3,(ENGINE_PROJECTION_BASE+$06).l
        move.w  d3,(ENGINE_PROJECTION_LENGTH).l
        move.l  d2,d4
        not.l   d4
        move.l  d4,(ENGINE_STATE_CRC).l
        move.w  (ENGINE_TICK).l,d0
        move.w  d0,(ENGINE_CHECKPOINT_TICK).l
        cmpi.w  #$FFFF,(ENGINE_CHECKPOINT_COUNT).l
        beq.s   .checkpoint_done
        addq.w  #1,(ENGINE_CHECKPOINT_COUNT).l
.checkpoint_done:
        move.l  d4,d1
        move.w  d3,d0
        lea     (ENGINE_PROJECTION_BASE).l,a0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

; Crc32ProjectionIsoHdlc
; In: a0=completed projection, d0.w=length.
; Out: d0.l=CRC. Clobbers d0-d2/a0-a1; preserves every callee-saved register.
; Independent fixed-schema verifier retained for standalone tests and
; diagnostics. Runtime publication fuses this classification into
; EngineProjectPool and therefore does not perform this second 140-slot scan.
; Precondition: a canonical projection has all 24 bytes zero whenever an object
; active byte is zero. Arbitrary or malformed buffers must use Crc32IsoHdlc.
Crc32ProjectionIsoHdlc:
        cmpi.w  #ENGINE_PROJECTION_SCHEMA_BYTES,d0
        beq.s   .fixed_schema
        bra.w   Crc32IsoHdlc

.fixed_schema:
        movem.l d3/d5-d7/a3,-(a7)
        move.l  #$FFFFFFFF,d1
        move.w  #PROJECTION_HEADER_BYTES+(ACTOR_CAPACITY*ACTOR_PROJECTION_BYTES),d0
        bsr.w   Crc32UpdateRaw

        lea     (.pool_capacities,pc),a3
        moveq   #POOL_TYPE_COUNT-1,d7
.pool:
        moveq   #PROJECTION_POOL_HEADER_BYTES,d0
        bsr.w   Crc32UpdateRaw
        moveq   #0,d6
        move.b  (a3)+,d6
        subq.w  #1,d6
        moveq   #0,d5
.slot:
        tst.b   (a0)
        beq.s   .zero_slot
        tst.w   d5
        beq.s   .stream_slot
        move.w  d5,d0
        bsr.w   Crc32AdvanceZeroRecords
        moveq   #0,d5
.stream_slot:
        move.w  #OBJECT_PROJECTION_BYTES,d0
        bsr.w   Crc32UpdateRaw
        bra.s   .next_slot
.zero_slot:
        adda.w  #OBJECT_PROJECTION_BYTES,a0
        addq.w  #1,d5
.next_slot:
        dbf     d6,.slot

        tst.w   d5
        beq.s   .next_pool
        move.w  d5,d0
        bsr.w   Crc32AdvanceZeroRecords
.next_pool:
        dbf     d7,.pool

        not.l   d1
        move.l  d1,d0
        movem.l (a7)+,d3/d5-d7/a3
        rts

.pool_capacities:
        dc.b    ENEMY_CAPACITY,PROJECTILE_CAPACITY,DEVICE_CAPACITY
        dc.b    CARRYABLE_CAPACITY,HAZARD_CAPACITY
        dc.b    BOSS_COMPONENT_CAPACITY,EFFECT_CAPACITY
        even

; Crc32AdvanceZeroRecords
; In: d0.w=consecutive zero-record count 1..32, d1.l=raw CRC.
; Out: d1.l advanced by count*24 zero bytes. Clobbers d0/d2/a1.
; Preserves d3 so the fused serializer can retain its bounded slot counter.
Crc32AdvanceZeroRecords:
; Neutral B01's composite run lengths take one generated transition. These
; exact checks precede the d3 save because the direct tail calls do not need a
; decomposition counter. Every other bounded count uses binary composition.
        cmpi.w  #15,d0
        beq.w   .direct_zero_records_15
        cmpi.w  #31,d0
        beq.w   .direct_zero_records_31
        cmpi.w  #7,d0
        beq.w   .direct_zero_records_7
        cmpi.w  #12,d0
        beq.w   .direct_zero_records_12
        cmpi.w  #24,d0
        beq.w   .direct_zero_records_24

        move.l  d3,-(a7)
        move.w  d0,d3
        btst    #0,d3
        beq.s   .zero_records_2
        lea     (Crc32ZeroRecords1Table).l,a1
        bsr.w   .apply_zero_transition
.zero_records_2:
        btst    #1,d3
        beq.s   .zero_records_4
        lea     (Crc32ZeroRecords2Table).l,a1
        bsr.w   .apply_zero_transition
.zero_records_4:
        btst    #2,d3
        beq.s   .zero_records_8
        lea     (Crc32ZeroRecords4Table).l,a1
        bsr.w   .apply_zero_transition
.zero_records_8:
        btst    #3,d3
        beq.s   .zero_records_16
        lea     (Crc32ZeroRecords8Table).l,a1
        bsr.w   .apply_zero_transition
.zero_records_16:
        btst    #4,d3
        beq.s   .zero_records_32
        lea     (Crc32ZeroRecords16Table).l,a1
        bsr.w   .apply_zero_transition
.zero_records_32:
        btst    #5,d3
        beq.s   .zero_records_done
        lea     (Crc32ZeroRecords32Table).l,a1
        bsr.w   .apply_zero_transition
.zero_records_done:
        move.l  (a7)+,d3
        rts

.direct_zero_records_15:
        lea     (Crc32ZeroRecords15Table).l,a1
        bra.w   .apply_zero_transition
.direct_zero_records_31:
        lea     (Crc32ZeroRecords31Table).l,a1
        bra.w   .apply_zero_transition
.direct_zero_records_7:
        lea     (Crc32ZeroRecords7Table).l,a1
        bra.w   .apply_zero_transition
.direct_zero_records_12:
        lea     (Crc32ZeroRecords12Table).l,a1
        bra.w   .apply_zero_transition
.direct_zero_records_24:
        lea     (Crc32ZeroRecords24Table).l,a1
        bra.w   .apply_zero_transition

; Apply one generated four-plane linear transition.
; In: a1=interleaved transition table, d1.l=raw CRC.
; Out: d1.l=advanced raw CRC. Clobbers d0/d2.
.apply_zero_transition:
        move.l  d1,d0
        moveq   #0,d2
        move.b  d0,d2
        lsl.w   #4,d2
        move.l  (a1,d2.w),d1

        lsr.w   #8,d0
        moveq   #0,d2
        move.b  d0,d2
        lsl.w   #4,d2
        move.l  4(a1,d2.w),d2
        eor.l   d2,d1

        swap    d0
        moveq   #0,d2
        move.b  d0,d2
        lsl.w   #4,d2
        move.l  8(a1,d2.w),d2
        eor.l   d2,d1

        lsr.w   #8,d0
        moveq   #0,d2
        move.b  d0,d2
        lsl.w   #4,d2
        move.l  12(a1,d2.w),d2
        eor.l   d2,d1
        rts

; EngineProjectPool
; In: a0=pool, a1=projection cursor, d0.w=capacity, d1.w=type,
;     d2.l=raw CRC through all preceding projection bytes.
; Out: a1 advanced, d2.l=raw CRC through this pool.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
; Prior storage is reset-zeroed or the preceding canonical projection; exact
; live counts are maintained by the pool API before publication.
EngineProjectPool:
        movem.l d3-d6/a2-a3,-(a7)
        movea.l a0,a2
        move.w  d0,d3
        move.l  d2,d6
; Retain the old live count before replacing the header. Only a pool empty in
; both publications can bypass slot classification; an active-to-empty pool
; still walks the ordinary path and clears its formerly active records.
        move.b  2(a1),d5
        move.b  d1,(a1)+
        move.b  d0,(a1)+
        move.w  d1,d2
        subq.w  #1,d2
        lea     (ENGINE_POOL_COUNTS).l,a0
        adda.w  d2,a0
        move.b  (a0),d4
        or.b    d4,d5
        move.b  d4,(a1)+
        lea     (ENGINE_POOL_HIGH_WATER).l,a0
        adda.w  d2,a0
        move.b  (a0),d4
        move.b  d4,(a1)+

; Hash the just-written pool header. a2 retains the source pool and a3 retains
; the projection cursor across the caller-saved streaming CRC routine.
        movea.l a1,a3
        lea     -PROJECTION_POOL_HEADER_BYTES(a1),a0
        moveq   #PROJECTION_POOL_HEADER_BYTES,d0
        move.l  d6,d1
        bsr.w   Crc32UpdateRaw
        move.l  d1,d6
        movea.l a3,a1

; The header, including its high-water mark, has already been hashed. Empty
; canonical payloads are all zero, so advance exactly capacity*24 bytes and
; apply the same trailing zero-record transition as the slot-by-slot path.
        tst.b   d5
        bne.s   .scan_slots
        move.w  d3,d5
        move.w  d3,d0
        mulu.w  #OBJECT_PROJECTION_BYTES,d0
        adda.w  d0,a1
        bra.w   .flush_trailing_run
.scan_slots:
        subq.w  #1,d3
        moveq   #0,d5
.slot:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .inactive_slot

.active_slot:
; Flush the preceding inactive run before hashing this nonzero record.
        tst.w   d5
        beq.s   .serialize_active
        movea.l a1,a3
        move.w  d5,d0
        move.l  d6,d1
        bsr.w   Crc32AdvanceZeroRecords
        move.l  d1,d6
        movea.l a3,a1
        moveq   #0,d5

.serialize_active:
        move.b  (OBJECT_ACTIVE,a2),d4
        move.b  d4,(a1)+
        move.b  (OBJECT_LOGICAL_ID,a2),d4
        move.b  d4,(a1)+
        move.w  (OBJECT_STATE,a2),d4
        move.w  d4,(a1)+
        move.l  (OBJECT_X,a2),d4
        move.l  d4,(a1)+
        move.l  (OBJECT_Y,a2),d4
        move.l  d4,(a1)+
        move.l  (OBJECT_VX,a2),d4
        move.l  d4,(a1)+
        move.l  (OBJECT_VY,a2),d4
        move.l  d4,(a1)+
        move.w  (OBJECT_TIMER,a2),d4
        move.w  d4,(a1)+
        move.w  (OBJECT_PARAM,a2),d4
        move.w  d4,(a1)+

; Hash the active record immediately. This is the only per-record stream call;
; inactive records are represented by coalesced generated transitions.
        movea.l a1,a3
        lea     -OBJECT_PROJECTION_BYTES(a1),a0
        move.w  #OBJECT_PROJECTION_BYTES,d0
        move.l  d6,d1
        bsr.w   Crc32UpdateRaw
        move.l  d1,d6
        movea.l a3,a1
        bra.s   .advance_slot

.inactive_slot:
; Allocation, free, and canonical reconstruction clear all six source longs.
; A prior inactive projection is already six zero longs, so steady publication
; advances without stores. A prior active byte is nonzero and clears all six
; longs exactly once on the active-to-inactive transition.
        addq.w  #1,d5
        tst.b   (a1)
        beq.s   .inactive_already_zero
        clr.l   (a1)+
        clr.l   (a1)+
        clr.l   (a1)+
        clr.l   (a1)+
        clr.l   (a1)+
        clr.l   (a1)+
        bra.s   .advance_slot
.inactive_already_zero:
        adda.w  #OBJECT_PROJECTION_BYTES,a1
.advance_slot:
        adda.w  #OBJECT_BYTES,a2
        dbf     d3,.slot

; A trailing inactive run must be applied before the next pool header.
.flush_trailing_run:
        tst.w   d5
        beq.s   .return_crc
        movea.l a1,a3
        move.w  d5,d0
        move.l  d6,d1
        bsr.w   Crc32AdvanceZeroRecords
        move.l  d1,d6
        movea.l a3,a1
.return_crc:
        move.l  d6,d2
        movem.l (a7)+,d3-d6/a2-a3
        rts

        assert  OBJECT_BYTES=OBJECT_PROJECTION_BYTES,"inactive object projection size drift"
