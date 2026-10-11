; Bounded fixed-slot object pools. Allocation always scans from slot zero.

; EnginePoolAllocateLowest
; In: a0=pool base, d0.w=capacity, d1.w=stride, d2.w=overflow flag.
; Out: d0.w=lowest free index and a0=cleared active slot, or d0.w=$FFFF.
; Clobbers d0-d2/a0-a1; preserves callee-saved registers.
EnginePoolAllocateLowest:
        movem.l d3-d4,-(a7)
        move.w  d0,d3
        subq.w  #1,d3
        moveq   #0,d4
.scan:
        tst.b   (a0)
        beq.s   .found
        adda.w  d1,a0
        addq.w  #1,d4
        dbf     d3,.scan

        or.w    d2,(ENGINE_OVERFLOW_FLAGS).l
        move.w  #ENGINE_ERROR_POOL_OVERFLOW,(ENGINE_LAST_ERROR).l
        moveq   #-1,d0
        movem.l (a7)+,d3-d4
        rts

.found:
        movea.l a0,a1
        moveq   #0,d0
        moveq   #(OBJECT_BYTES/4)-1,d3
.clear:
        move.l  d0,(a1)+
        dbf     d3,.clear
        move.b  #$01,(OBJECT_ACTIVE,a0)
        move.l  d4,d0
        movem.l (a7)+,d3-d4
        rts

; EnginePoolNoteAllocation
; In: d2.w=pool type 1..7. Clobbers d1/a1; preserves d0.
EnginePoolNoteAllocation:
        move.l  d0,-(a7)
        move.w  d2,d1
        subq.w  #1,d1
        lea     (ENGINE_POOL_COUNTS).l,a1
        adda.w  d1,a1
        addq.b  #1,(a1)
        move.b  (a1),d1
        lea     (ENGINE_POOL_HIGH_WATER).l,a1
        move.w  d2,d0
        subq.w  #1,d0
        adda.w  d0,a1
        cmp.b   (a1),d1
        bls.s   .done
        move.b  d1,(a1)
.done:
        move.l  (a7)+,d0
        rts

; EnginePoolFreeSlot
; In: a0=active slot, d2.w=pool type 1..7. Clobbers d0-d1/a1.
EnginePoolFreeSlot:
        movea.l a0,a1
        moveq   #0,d0
        moveq   #(OBJECT_BYTES/4)-1,d1
.clear:
        move.l  d0,(a1)+
        dbf     d1,.clear
        move.w  d2,d1
        subq.w  #1,d1
        lea     (ENGINE_POOL_COUNTS).l,a1
        adda.w  d1,a1
        tst.b   (a1)
        beq.s   .done
        subq.b  #1,(a1)
.done:
        rts

; EngineInstantiateCanonicalObject
; In: a1=12-byte canonical object record.
; Out: d0.w=slot index or $FFFF. Clobbers d0-d2/a0-a1.
EngineInstantiateCanonicalObject:
        movem.l d3/a2,-(a7)
        movea.l a1,a2
        moveq   #0,d3
        move.b  (OBJECT_INIT_TYPE,a2),d3
        cmpi.w  #POOL_TYPE_ENEMY,d3
        blo.s   .invalid
        cmpi.w  #POOL_TYPE_EFFECT,d3
        bhi.s   .invalid

        move.w  d3,d0
        subq.w  #1,d0
        lsl.w   #3,d0
        lea     (EnginePoolDescriptorTable,pc),a1
        adda.w  d0,a1
        move.w  4(a1),d0
        move.w  #OBJECT_BYTES,d1
        move.w  6(a1),d2
        movea.l (a1),a0
        bsr.w   EnginePoolAllocateLowest
        cmpi.w  #$FFFF,d0
        beq.s   .done

        moveq   #0,d1
        move.b  (OBJECT_INIT_LOGICAL_ID,a2),d1
        move.b  d1,(OBJECT_LOGICAL_ID,a0)
        move.w  (OBJECT_INIT_STATE,a2),d1
        move.w  d1,(OBJECT_STATE,a0)

        moveq   #0,d1
        move.w  (OBJECT_INIT_X,a2),d1
        ext.l   d1
        lsl.l   #8,d1
        move.l  d1,(OBJECT_X,a0)
        moveq   #0,d1
        move.w  (OBJECT_INIT_Y,a2),d1
        ext.l   d1
        lsl.l   #8,d1
        move.l  d1,(OBJECT_Y,a0)
        clr.l   (OBJECT_VX,a0)
        clr.l   (OBJECT_VY,a0)
        move.w  (OBJECT_INIT_PARAM_0,a2),d1
        move.w  d1,(OBJECT_TIMER,a0)
        move.w  (OBJECT_INIT_PARAM_1,a2),d1
        move.w  d1,(OBJECT_PARAM,a0)

        move.w  d3,d2
        bsr.w   EnginePoolNoteAllocation
        bra.s   .done
.invalid:
        move.w  #ENGINE_ERROR_BAD_DESCRIPTOR,(ENGINE_LAST_ERROR).l
        moveq   #-1,d0
.done:
        movem.l (a7)+,d3/a2
        rts

; EngineUpdatePool
; In: a0=pool base, d0.w=capacity, d2.w=pool type.
; Adds signed 16.8 velocity and expires slots with a nonzero timer.
EngineUpdatePool:
        movem.l d3/a2,-(a7)
        move.w  d0,d3
        movea.l a0,a2
        lea     (ENGINE_POOL_HIGH_WATER-1).l,a1
        moveq   #0,d0
        move.b  (a1,d2.w),d0
        cmp.w   d3,d0
        bls.s   .bound_ready
        move.w  d3,d0
.bound_ready:
        tst.w   d0
        beq.s   .done
        move.w  d0,d3
        subq.w  #1,d3
.next:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .advance
        move.l  (OBJECT_VX,a2),d0
        add.l   d0,(OBJECT_X,a2)
        move.l  (OBJECT_VY,a2),d0
        add.l   d0,(OBJECT_Y,a2)
        tst.w   (OBJECT_TIMER,a2)
        beq.s   .advance
        subq.w  #1,(OBJECT_TIMER,a2)
        bne.s   .advance
        movea.l a2,a0
        bsr.w   EnginePoolFreeSlot
.advance:
        adda.w  #OBJECT_BYTES,a2
        dbf     d3,.next
.done:
        movem.l (a7)+,d3/a2
        rts

EngineUpdateAllPools:
        lea     (ENEMY_POOL).l,a0
        move.w  #ENEMY_CAPACITY,d0
        moveq   #POOL_TYPE_ENEMY,d2
        bsr.w   EngineUpdatePool
        lea     (PROJECTILE_POOL).l,a0
        move.w  #PROJECTILE_CAPACITY,d0
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EngineUpdatePool
        lea     (DEVICE_POOL).l,a0
        move.w  #DEVICE_CAPACITY,d0
        moveq   #POOL_TYPE_DEVICE,d2
        bsr.w   EngineUpdatePool
        lea     (CARRYABLE_POOL).l,a0
        move.w  #CARRYABLE_CAPACITY,d0
        moveq   #POOL_TYPE_CARRYABLE,d2
        bsr.w   EngineUpdatePool
        lea     (HAZARD_POOL).l,a0
        move.w  #HAZARD_CAPACITY,d0
        moveq   #POOL_TYPE_HAZARD,d2
        bsr.w   EngineUpdatePool
        lea     (BOSS_COMPONENT_POOL).l,a0
        move.w  #BOSS_COMPONENT_CAPACITY,d0
        moveq   #POOL_TYPE_BOSS,d2
        bsr.w   EngineUpdatePool
        lea     (EFFECT_POOL).l,a0
        move.w  #EFFECT_CAPACITY,d0
        moveq   #POOL_TYPE_EFFECT,d2
        bsr.w   EngineUpdatePool
        rts

; EngineUpdateTransientPools
; Campaign vocabulary owns enemies, projectiles, devices, carryables, and
; hazards. Only short-lived effects retain the generic motion/TTL behavior.
; Boss components are permanent until their bounded phase controller mutates
; them and therefore must never expire through this helper.
EngineUpdateTransientPools:
        lea     (EFFECT_POOL).l,a0
        move.w  #EFFECT_CAPACITY,d0
        moveq   #POOL_TYPE_EFFECT,d2
        bsr.w   EngineUpdatePool
        rts

; EngineSpawnProjectileFromActor
; In: a0=actor slot. Out: d0.w=slot index or $FFFF.
EngineSpawnProjectileFromActor:
        movem.l d3/a2,-(a7)
        movea.l a0,a2
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .invalid
        lea     (PROJECTILE_POOL).l,a0
        move.w  #PROJECTILE_CAPACITY,d0
        move.w  #OBJECT_BYTES,d1
        move.w  #OVERFLOW_PROJECTILE,d2
        bsr.w   EnginePoolAllocateLowest
        cmpi.w  #$FFFF,d0
        beq.s   .done

        move.w  d0,d3
        move.b  d0,(OBJECT_LOGICAL_ID,a0)
        move.w  #$0001,(OBJECT_STATE,a0)
        move.l  (ACTOR_X,a2),d1
        move.l  d1,(OBJECT_X,a0)
        move.l  (ACTOR_Y,a2),d1
        move.l  d1,(OBJECT_Y,a0)
        move.l  #ENGINE_PROJECTILE_SPEED,d1
        tst.b   (ENGINE_FACING_X).l
        bpl.s   .store_velocity
        neg.l   d1
.store_velocity:
        move.l  d1,(OBJECT_VX,a0)
        clr.l   (OBJECT_VY,a0)
        move.w  #ENGINE_PROJECTILE_TICKS,(OBJECT_TIMER,a0)
        moveq   #0,d1
        move.b  (ACTOR_LOGICAL_ID,a2),d1
        addq.w  #1,d1
        lsl.w   #ENGINE_PROJECTILE_OWNER_SHIFT,d1
        ori.w   #ENGINE_PROJECTILE_DAMAGE_DEFAULT,d1
        move.w  d1,(OBJECT_PARAM,a0)
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolNoteAllocation
        move.w  d3,d0
        bra.s   .done
.invalid:
        moveq   #-1,d0
.done:
        movem.l (a7)+,d3/a2
        rts

        even

; ROM-only pool descriptors: base.l, capacity.w, overflow-mask.w.
EnginePoolDescriptorTable:
        dc.l    ENEMY_POOL
        dc.w    ENEMY_CAPACITY,OVERFLOW_ENEMY
        dc.l    PROJECTILE_POOL
        dc.w    PROJECTILE_CAPACITY,OVERFLOW_PROJECTILE
        dc.l    DEVICE_POOL
        dc.w    DEVICE_CAPACITY,OVERFLOW_DEVICE
        dc.l    CARRYABLE_POOL
        dc.w    CARRYABLE_CAPACITY,OVERFLOW_CARRYABLE
        dc.l    HAZARD_POOL
        dc.w    HAZARD_CAPACITY,OVERFLOW_HAZARD
        dc.l    BOSS_COMPONENT_POOL
        dc.w    BOSS_COMPONENT_CAPACITY,OVERFLOW_BOSS
        dc.l    EFFECT_POOL
        dc.w    EFFECT_CAPACITY,OVERFLOW_EFFECT
EnginePoolDescriptorTableEnd:

        assert  EnginePoolDescriptorTableEnd-EnginePoolDescriptorTable=POOL_TYPE_COUNT*8,"pool descriptor table size mismatch"
