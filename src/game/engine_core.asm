; Deterministic fixed-frame engine core. No routine writes hardware or renderer
; state, allocates dynamically, or runs from an interrupt.

; EngineInitialize
; Out: d0.w=0. Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineInitialize:
        lea     (ENGINE_CONTROL_BASE).l,a0
        moveq   #0,d0
        move.w  #((ENGINE_STAGING_END-ENGINE_CONTROL_BASE)/4)-1,d1
.clear_owned_ram:
        move.l  d0,(a0)+
        dbf     d1,.clear_owned_ram

        move.l  #ENGINE_MAGIC_VALUE,(ENGINE_MAGIC).l
        move.w  #ENGINE_ABI_VERSION_VALUE,(ENGINE_ABI_VERSION).l
        move.w  #ENGINE_CONTROL_BYTES_VALUE,(ENGINE_CONTROL_BYTES).l
        move.w  #ENGINE_STATE_UNLOADED,(ENGINE_STATE).l
        move.b  #$01,(ENGINE_FACING_X).l
        moveq   #ENGINE_RESULT_OK,d0
        rts

; EngineLoadEncounter
; In: d0.w=encounter ID.
; Out: d0.w=0 success, 1 not found, 2 invalid descriptor, 3 pool overflow.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineLoadEncounter:
        movem.l d3-d4/a2,-(a7)
        move.w  d0,d3
        lea     (EncounterDescriptorTable,pc),a2
        move.w  #ENCOUNTER_DESCRIPTOR_COUNT-1,d4
.find:
        movea.l (a2)+,a0
        cmp.w   (ENCOUNTER_DESCRIPTOR_ID,a0),d3
        beq.s   .found
        dbf     d4,.find
        move.w  #ENGINE_ERROR_ENCOUNTER_NOT_FOUND,(ENGINE_LAST_ERROR).l
        moveq   #ENGINE_RESULT_NOT_FOUND,d0
        bra.s   .done

.found:
        move.w  d3,d0
        movem.l (a7)+,d3-d4/a2
        bra.w   EngineLoadDescriptor
.done:
        movem.l (a7)+,d3-d4/a2
        rts

; EngineLoadDescriptor
; In: a0=non-null even descriptor pointer, d0.w=encounter ID.
; The supplied ID must match ENCOUNTER_DESCRIPTOR_ID in the descriptor.
; Out: d0.w=0 success, 2 invalid descriptor, 3 pool overflow.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineLoadDescriptor:
        movem.l d3-d7/a2-a6,-(a7)
        move.w  d0,d3
        movea.l a0,a3
        move.l  a0,d1
        beq.w   .bad_descriptor
        btst    #0,d1
        bne.w   .bad_descriptor

; Validate the descriptor before mutating authoritative state.
        cmp.w   (ENCOUNTER_DESCRIPTOR_ID,a3),d3
        bne.w   .bad_descriptor
        cmpi.w  #ENCOUNTER_DESCRIPTOR_VERSION_VALUE,(ENCOUNTER_DESCRIPTOR_VERSION,a3)
        bne.w   .bad_descriptor
        cmpi.w  #ENCOUNTER_DESCRIPTOR_SIZE,(ENCOUNTER_DESCRIPTOR_BYTES,a3)
        bne.w   .bad_descriptor

        move.w  (ENCOUNTER_ROOM_LEFT,a3),d0
        cmp.w   (ENCOUNTER_ROOM_RIGHT,a3),d0
        bge.w   .bad_descriptor
        move.w  (ENCOUNTER_ROOM_TOP,a3),d0
        cmp.w   (ENCOUNTER_ROOM_BOTTOM,a3),d0
        bge.w   .bad_descriptor
        move.w  (ENCOUNTER_SPAWN_X,a3),d0
        cmp.w   (ENCOUNTER_ROOM_LEFT,a3),d0
        blt.w   .bad_descriptor
        cmp.w   (ENCOUNTER_ROOM_RIGHT,a3),d0
        bge.w   .bad_descriptor
        move.w  (ENCOUNTER_SPAWN_Y,a3),d0
        cmp.w   (ENCOUNTER_ROOM_TOP,a3),d0
        blt.w   .bad_descriptor
        cmp.w   (ENCOUNTER_ROOM_BOTTOM,a3),d0
        bge.w   .bad_descriptor

        moveq   #0,d0
        move.w  (ENCOUNTER_RECT_COUNT,a3),d0
        cmpi.w  #STATIC_RECT_CAPACITY,d0
        bhi.w   .bad_descriptor
        tst.w   d0
        beq.s   .rect_pointer_ok
        move.l  (ENCOUNTER_RECT_POINTER,a3),d1
        beq.w   .bad_descriptor
        btst    #0,d1
        bne.w   .bad_descriptor
.rect_pointer_ok:
        moveq   #0,d0
        move.w  (ENCOUNTER_OBJECT_COUNT,a3),d0
        cmpi.w  #TOTAL_POOL_CAPACITY,d0
        bhi.w   .bad_descriptor
        tst.w   d0
        beq.s   .object_pointer_ok
        move.l  (ENCOUNTER_OBJECT_POINTER,a3),d1
        beq.w   .bad_descriptor
        btst    #0,d1
        bne.w   .bad_descriptor
.object_pointer_ok:

; Staging is nonauthoritative until validation completes. Clear every byte so
; unused canonical space cannot leak into reset behavior.
        lea     (ENGINE_STAGING_BASE).l,a0
        moveq   #0,d0
        move.w  #((ENGINE_STAGING_END-ENGINE_STAGING_BASE)/4)-1,d1
.clear_staging:
        move.l  d0,(a0)+
        dbf     d1,.clear_staging

        movea.l a3,a0
        lea     (CANONICAL_DESCRIPTOR).l,a1
        moveq   #(ENCOUNTER_DESCRIPTOR_SIZE/4)-1,d1
.copy_descriptor:
        move.l  (a0)+,d0
        move.l  d0,(a1)+
        dbf     d1,.copy_descriptor

; Validate and copy ordered static rectangles.
        moveq   #0,d4
        move.w  (ENCOUNTER_RECT_COUNT,a3),d4
        beq.s   .rectangles_done
        movea.l (ENCOUNTER_RECT_POINTER,a3),a4
        lea     (CANONICAL_RECTS).l,a5
        subq.w  #1,d4
.rectangle:
        move.w  (STATIC_RECT_WIDTH,a4),d7
        ble.w   .bad_descriptor
        move.w  (STATIC_RECT_HEIGHT,a4),d2
        ble.w   .bad_descriptor
        moveq   #0,d5
        move.w  (STATIC_RECT_X,a4),d5
        ext.l   d5
        moveq   #0,d0
        move.w  (ENCOUNTER_ROOM_LEFT,a3),d0
        ext.l   d0
        cmp.l   d0,d5
        blt.w   .bad_descriptor
        moveq   #0,d1
        move.w  d7,d1
        add.l   d5,d1
        moveq   #0,d0
        move.w  (ENCOUNTER_ROOM_RIGHT,a3),d0
        ext.l   d0
        cmp.l   d0,d1
        bgt.w   .bad_descriptor
        moveq   #0,d6
        move.w  (STATIC_RECT_Y,a4),d6
        ext.l   d6
        moveq   #0,d0
        move.w  (ENCOUNTER_ROOM_TOP,a3),d0
        ext.l   d0
        cmp.l   d0,d6
        blt.w   .bad_descriptor
        moveq   #0,d1
        move.w  d2,d1
        add.l   d6,d1
        moveq   #0,d0
        move.w  (ENCOUNTER_ROOM_BOTTOM,a3),d0
        ext.l   d0
        cmp.l   d0,d1
        bgt.w   .bad_descriptor
        move.l  (a4)+,d0
        move.l  d0,(a5)+
        move.l  (a4)+,d0
        move.l  d0,(a5)+
        dbf     d4,.rectangle
.rectangles_done:

; Validate per-pool capacities and strictly ascending logical IDs, then copy
; canonical object records. Per-type scratch lives in unused staging space.
        moveq   #0,d4
        move.w  (ENCOUNTER_OBJECT_COUNT,a3),d4
        beq.w   .objects_done
        movea.l (ENCOUNTER_OBJECT_POINTER,a3),a4
        lea     (CANONICAL_OBJECTS).l,a5
        subq.w  #1,d4
.object:
        moveq   #0,d5
        move.b  (OBJECT_INIT_TYPE,a4),d5
        cmpi.w  #POOL_TYPE_ENEMY,d5
        blo.w   .bad_descriptor
        cmpi.w  #POOL_TYPE_EFFECT,d5
        bhi.w   .bad_descriptor

; Records may interleave pool types, but IDs within each type must increase.
; Storing ID+1 gives the zero-cleared scratch a distinct "not seen" sentinel.
        move.w  d5,d6
        subq.w  #1,d6
        add.w   d6,d6
        lea     (CANONICAL_POOL_LAST_IDS).l,a6
        adda.w  d6,a6
        moveq   #0,d1
        move.b  (OBJECT_INIT_LOGICAL_ID,a4),d1
        addq.w  #1,d1
        tst.w   (a6)
        beq.s   .logical_id_valid
        cmp.w   (a6),d1
        bls.w   .bad_descriptor
.logical_id_valid:
        move.w  d1,(a6)

        move.w  d5,d6
        subq.w  #1,d6
        lea     (CANONICAL_POOL_COUNTS).l,a6
        adda.w  d6,a6
        addq.b  #1,(a6)
        moveq   #0,d7
        move.b  (a6),d7
        lsl.w   #3,d6
        lea     (EnginePoolDescriptorTable,pc),a6
        move.w  4(a6,d6.w),d0
        cmp.w   d0,d7
        bhi.w   .bad_descriptor

        move.w  (OBJECT_INIT_X,a4),d0
        cmp.w   (ENCOUNTER_ROOM_LEFT,a3),d0
        blt.w   .bad_descriptor
        cmp.w   (ENCOUNTER_ROOM_RIGHT,a3),d0
        bge.w   .bad_descriptor
        move.w  (OBJECT_INIT_Y,a4),d0
        cmp.w   (ENCOUNTER_ROOM_TOP,a3),d0
        blt.w   .bad_descriptor
        cmp.w   (ENCOUNTER_ROOM_BOTTOM,a3),d0
        bge.w   .bad_descriptor

        move.l  (a4)+,d0
        move.l  d0,(a5)+
        move.l  (a4)+,d0
        move.l  d0,(a5)+
        move.l  (a4)+,d0
        move.l  d0,(a5)+
        dbf     d4,.object
.objects_done:

; Commit the validated immutable descriptor to control state.
        move.w  d3,(ENGINE_ENCOUNTER_ID).l
        clr.w   (ENGINE_ATTEMPT).l
        clr.w   (ENGINE_CHECKPOINT_COUNT).l
        move.w  (ENCOUNTER_ROOM_LEFT,a3),d0
        move.w  d0,(ENGINE_ROOM_LEFT).l
        move.w  (ENCOUNTER_ROOM_TOP,a3),d0
        move.w  d0,(ENGINE_ROOM_TOP).l
        move.w  (ENCOUNTER_ROOM_RIGHT,a3),d0
        move.w  d0,(ENGINE_ROOM_RIGHT).l
        move.w  (ENCOUNTER_ROOM_BOTTOM,a3),d0
        move.w  d0,(ENGINE_ROOM_BOTTOM).l

        moveq   #0,d0
        move.w  (ENCOUNTER_SPAWN_X,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        move.l  d0,(ENGINE_SPAWN_X).l
        moveq   #0,d0
        move.w  (ENCOUNTER_SPAWN_Y,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        move.l  d0,(ENGINE_SPAWN_Y).l
        move.w  (ENCOUNTER_RECT_COUNT,a3),d0
        move.w  d0,(ENGINE_RECT_COUNT).l
        move.w  (ENCOUNTER_OBJECT_COUNT,a3),d0
        move.w  d0,(ENGINE_OBJECT_INIT_COUNT).l

        bsr.w   EngineReconstructCanonical
        bra.s   .done

.bad_descriptor:
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        andi.w  #$FFFC,(ENGINE_FLAGS).l
        clr.w   (ENGINE_PROJECTION_LENGTH).l
        move.w  #ENGINE_ERROR_BAD_DESCRIPTOR,(ENGINE_LAST_ERROR).l
        moveq   #ENGINE_RESULT_BAD_DESCRIPTOR,d0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

; EngineReconstructCanonical
; Recreates every ordinary-state owner from validated canonical staging and
; publishes the resulting checkpoint.
EngineReconstructCanonical:
        bsr.w   EngineReconstructCanonicalState
        tst.w   d0
        bne.s   .done
        bsr.w   EngineBuildProjection
        moveq   #ENGINE_RESULT_OK,d0
.done:
        rts

; EngineReconstructCanonicalState
; Recreates ordinary state from the already validated canonical staging image
; without publishing.  The split lets gameplay replace the canonical actor
; with its echo/current roster before the one authoritative reset checkpoint.
; Out: d0.w=0 success or 3 pool overflow. Clobbers d0-d2/a0-a1.
; Preserves d3-d7/a2-a6.
EngineReconstructCanonicalState:
        movem.l d3-d7/a2-a6,-(a7)
        lea     (ENGINE_ACTOR_BASE).l,a0

; Clear the complete ordinary/projection range in fixed 52-byte bursts.  The
; thirteen data/address registers in the MOVEM are all zero; d7 is the bounded
; loop counter and a0 remains the destination.  433*52+12 is exactly $5800.
        moveq   #0,d0
        moveq   #0,d1
        moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        movea.l d0,a1
        movea.l d0,a2
        movea.l d0,a3
        movea.l d0,a4
        movea.l d0,a5
        movea.l d0,a6
        move.w  #432,d7
.clear_ordinary:
        movem.l d0-d6/a1-a6,(a0)
        adda.w  #52,a0
        dbf     d7,.clear_ordinary
        move.l  d0,(a0)+
        move.l  d0,(a0)+
        move.l  d0,(a0)+

        move.w  #ENGINE_FLAG_LOADED,(ENGINE_FLAGS).l
        move.w  #ENGINE_STATE_ACTIVE,(ENGINE_STATE).l
        clr.w   (ENGINE_TICK).l
        clr.w   (ENGINE_TERMINAL_REASON).l
        clr.w   (ENGINE_LAST_ERROR).l
        clr.w   (ENGINE_OVERFLOW_FLAGS).l
        clr.w   (ENGINE_PROJECTION_LENGTH).l
        clr.l   (ENGINE_STATE_CRC).l
        clr.w   (ENGINE_CHECKPOINT_TICK).l
        clr.w   (ENGINE_CHECKPOINT_COUNT).l
        clr.l   (ENGINE_INPUT_HELD).l
        move.b  #$01,(ENGINE_FACING_X).l
        move.b  #$01,(ENGINE_ACTIVE_ACTOR_COUNT).l

        lea     (ENGINE_POOL_COUNTS).l,a0
        moveq   #0,d0
        moveq   #3,d1
.clear_pool_counters:
        move.l  d0,(a0)+
        dbf     d1,.clear_pool_counters

        lea     (ENGINE_ACTOR_BASE).l,a2
        move.b  #$01,(ACTOR_ACTIVE,a2)
        move.b  #ACTOR_CURRENT_ROLE,(ACTOR_ROLE,a2)
        clr.w   (ACTOR_FLAGS,a2)
        move.l  (ENGINE_SPAWN_X).l,d0
        move.l  d0,(ACTOR_X,a2)
        move.l  (ENGINE_SPAWN_Y).l,d0
        move.l  d0,(ACTOR_Y,a2)
        clr.l   (ACTOR_VX,a2)
        clr.l   (ACTOR_VY,a2)
        move.w  #4,(ACTOR_HALF_WIDTH,a2)
        move.w  #4,(ACTOR_HALF_HEIGHT,a2)
        clr.b   (ACTOR_INPUT,a2)
        clr.b   (ACTOR_PREVIOUS_INPUT,a2)
        move.w  #1,(ACTOR_HEALTH,a2)
        clr.w   (ACTOR_LOGICAL_ID,a2)
        clr.w   (ACTOR_RESERVED,a2)

        moveq   #0,d3
        move.w  (ENGINE_OBJECT_INIT_COUNT).l,d3
        beq.s   .objects_done
        lea     (CANONICAL_OBJECTS).l,a2
        subq.w  #1,d3
.object:
        movea.l a2,a1
        bsr.w   EngineInstantiateCanonicalObject
        cmpi.w  #$FFFF,d0
        beq.s   .overflow
        adda.w  #OBJECT_INIT_BYTES,a2
        dbf     d3,.object
.objects_done:
        moveq   #ENGINE_RESULT_OK,d0
        bra.s   .done
.overflow:
        move.w  #ENGINE_STATE_ERROR,(ENGINE_STATE).l
        move.w  #ENGINE_ERROR_POOL_OVERFLOW,(ENGINE_LAST_ERROR).l
        moveq   #ENGINE_RESULT_POOL_OVERFLOW,d0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts

; EngineResetCanonicalState
; Restores all ordinary owners from the immutable, already validated canonical
; staging image and increments the attempt ordinal, but does not publish.
; Out: d0.w=0 success, 3 reconstruction failure, or 4 when no encounter loaded.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineResetCanonicalState:
        movem.l d3-d4,-(a7)
        move.w  (ENGINE_FLAGS).l,d0
        andi.w  #ENGINE_FLAG_LOADED,d0
        beq.s   .not_active
        move.w  (ENGINE_ENCOUNTER_ID).l,d3
        move.w  (ENGINE_ATTEMPT).l,d4
        cmpi.w  #$FFFF,d4
        beq.s   .attempt_ready
        addq.w  #1,d4
.attempt_ready:
        move.w  d4,(ENGINE_ATTEMPT).l
        bsr.w   EngineReconstructCanonicalState
        tst.w   d0
        bne.s   .done
        moveq   #ENGINE_RESULT_OK,d0
        bra.s   .done
.not_active:
        move.w  #ENGINE_ERROR_INVALID_STATE,(ENGINE_LAST_ERROR).l
        moveq   #ENGINE_RESULT_NOT_ACTIVE,d0
.done:
        movem.l (a7)+,d3-d4
        rts

; EngineReset
; Public engine reset: restore from validated canonical staging, increment the
; attempt ordinal, and publish exactly one initial checkpoint.
; Out: d0.w=0 success, 3 reconstruction failure, or 4 when no encounter loaded.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineReset:
        bsr.w   EngineResetCanonicalState
        tst.w   d0
        bne.s   .done
        bsr.w   EngineBuildProjection
        moveq   #ENGINE_RESULT_OK,d0
.done:
        rts

; EngineStep
; In: d0.b=UDLRABC held bits. Reserved bit and opposite axes are sanitized.
; Out: d0.w=0 while active, terminal reason when tick 720 completes, or 4 when
; the engine is not active. Exactly one authoritative tick is consumed.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EngineStep:
        movem.l d3-d7/a2-a6,-(a7)
        cmpi.w  #ENGINE_STATE_ACTIVE,(ENGINE_STATE).l
        beq.s   .active
        cmpi.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        bne.s   .not_active
        move.w  (ENGINE_TERMINAL_REASON).l,d0
        bra.w   .done

.not_active:
        move.w  #ENGINE_ERROR_INVALID_STATE,(ENGINE_LAST_ERROR).l
        moveq   #ENGINE_RESULT_NOT_ACTIVE,d0
        bra.w   .done

.active:
        moveq   #0,d3
        move.b  d0,d3
        andi.b  #INPUT_ALLOWED_MASK,d3
        move.w  d3,d4
        andi.w  #INPUT_UP|INPUT_DOWN,d4
        cmpi.w  #INPUT_UP|INPUT_DOWN,d4
        bne.s   .vertical_sanitized
        andi.b  #$FC,d3
.vertical_sanitized:
        move.w  d3,d4
        andi.w  #INPUT_LEFT|INPUT_RIGHT,d4
        cmpi.w  #INPUT_LEFT|INPUT_RIGHT,d4
        bne.s   .horizontal_sanitized
        andi.b  #$F3,d3
.horizontal_sanitized:

        moveq   #0,d4
        move.b  (ENGINE_INPUT_HELD).l,d4
        move.b  d4,(ENGINE_INPUT_PREVIOUS).l
        move.b  d3,(ENGINE_INPUT_HELD).l
        move.b  d4,d5
        not.b   d5
        and.b   d3,d5
        move.b  d5,(ENGINE_INPUT_PRESSED).l
        move.b  d3,d6
        not.b   d6
        and.b   d4,d6
        move.b  d6,(ENGINE_INPUT_RELEASED).l

        lea     (ENGINE_ACTOR_BASE).l,a2
        move.b  d4,(ACTOR_PREVIOUS_INPUT,a2)
        move.b  d3,(ACTOR_INPUT,a2)
        andi.w  #$FFFB,(ENGINE_FLAGS).l

; Derive signed direction components after opposite-axis neutralization.
        moveq   #0,d4
        btst    #2,d3
        beq.s   .not_left
        moveq   #-1,d4
.not_left:
        btst    #3,d3
        beq.s   .x_direction_done
        moveq   #1,d4
.x_direction_done:
        moveq   #0,d5
        btst    #0,d3
        beq.s   .not_up
        moveq   #-1,d5
.not_up:
        btst    #1,d3
        beq.s   .y_direction_done
        moveq   #1,d5
.y_direction_done:

        move.l  #ENGINE_CARDINAL_SPEED,d6
        tst.w   d4
        beq.s   .speed_ready
        tst.w   d5
        beq.s   .speed_ready
        move.l  #ENGINE_DIAGONAL_SPEED,d6
.speed_ready:
        moveq   #0,d7
        tst.w   d4
        beq.s   .vx_ready
        move.l  d6,d7
        tst.w   d4
        bpl.s   .facing_ready
        neg.l   d7
.facing_ready:
        move.b  d4,(ENGINE_FACING_X).l
.vx_ready:
        move.l  d7,(ACTOR_VX,a2)

        moveq   #0,d0
        tst.w   d5
        beq.s   .vy_ready
        move.l  d6,d0
        tst.w   d5
        bpl.s   .vy_ready
        neg.l   d0
.vy_ready:
        move.l  d0,(ACTOR_VY,a2)

        movea.l a2,a0
        bsr.w   EngineMoveActorAxisSeparated

; A is edge-triggered and exercises the real lowest-free projectile pool.
        moveq   #0,d0
        move.b  (ENGINE_INPUT_PRESSED).l,d0
        andi.b  #INPUT_A,d0
        beq.s   .no_projectile
        movea.l a2,a0
        bsr.w   EngineSpawnProjectileFromActor
.no_projectile:
        bsr.w   EngineUpdateAllPools

; Tick 719 is processed above; incrementing to 720 then publishes timeout.
        addq.w  #1,(ENGINE_TICK).l
        cmpi.w  #ENGINE_ATTEMPT_TICKS,(ENGINE_TICK).l
        blo.s   .still_active
        move.w  #ENGINE_ATTEMPT_TICKS,(ENGINE_TICK).l
        move.w  #ENGINE_TERMINAL_TIMEOUT,(ENGINE_TERMINAL_REASON).l
        move.w  #ENGINE_STATE_TERMINAL,(ENGINE_STATE).l
        bsr.w   EngineBuildProjection
        moveq   #ENGINE_TERMINAL_TIMEOUT,d0
        bra.s   .done

.still_active:
; Publish stable checkpoints every 60 authoritative ticks.
        moveq   #0,d0
        move.w  (ENGINE_TICK).l,d0
        divu.w  #ENGINE_CHECKPOINT_INTERVAL,d0
        swap    d0
        tst.w   d0
        bne.s   .no_checkpoint
        bsr.w   EngineBuildProjection
.no_checkpoint:
        moveq   #ENGINE_RESULT_OK,d0
.done:
        movem.l (a7)+,d3-d7/a2-a6
        rts
