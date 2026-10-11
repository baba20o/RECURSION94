; Campaign-mode collision helpers.
;
; The caller remains responsible for room bounds and every canonical static
; rectangle, including physical rectangle zero.  For each axis, pass that
; corrected candidate through the matching door helper before storing it.
; This second pass never substitutes for or skips static collision.
;
; Device records do not carry dimensions, so every vocabulary door uses the
; shared rendered 16x32 leaf whose top-left is OBJECT_X/OBJECT_Y. Dash/phase
; protects actors from authored hazards but never bypasses a closed puzzle
; door; the caller still applies room/static collision before these helpers.
;
; Requires game/engine_layout.asm, gameplay/gameplay_layout.asm, and
; gameplay/vocabulary_layout.asm.

CAMPAIGN_DOOR_WIDTH_16_8        equ     16*256
CAMPAIGN_DOOR_HEIGHT_16_8       equ     32*256

; GameplayCampaignActorInHazard
; In: a0=active actor, a1=active hazard. Rectangular hazards use authored
; top-left width/height from semantic flags and actor AABB overlap; other
; hazards retain the bounded point/radius vocabulary contract.
; Out: d0.w=one on overlap, zero otherwise.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignActorInHazard:
        movem.l d3-d7/a2-a4,-(a7)
        movea.l a0,a2
        movea.l a1,a3

        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a3),d0
        moveq   #0,d1
        move.b  (VOCAB_CAMPAIGN_SEMANTIC_COUNT).l,d1
        cmp.w   d1,d0
        beq.w   .outside
        bhs.w   .outside
        movea.l (VOCAB_CAMPAIGN_SEMANTIC_PTR).l,a0
        move.l  a0,d1
        beq.w   .outside
        btst    #0,d1
        bne.w   .outside
        move.w  d0,d1
        lsl.w   #4,d1
        adda.w  d1,a0
        cmp.b   (VOCAB_CAMPAIGN_SEMANTIC_OBJECT,a0),d0
        bne.w   .outside
        move.w  (VOCAB_CAMPAIGN_SEMANTIC_FLAGS,a0),d7

        moveq   #0,d4
        move.w  d7,d4
        andi.w  #VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK,d4
        lsr.w   #VOCAB_CAMPAIGN_GEOMETRY_WIDTH_SHIFT,d4
        moveq   #0,d5
        move.w  d7,d5
        andi.w  #VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK,d5
        lsr.w   #8,d5
        lsr.w   #3,d5
        tst.w   d4
        beq.s   .radius
        tst.w   d5
        beq.w   .malformed

; Convert eight-pixel semantic units to signed 16.8 extents.
        moveq   #0,d0
        move.w  d4,d0
        lsl.l   #8,d0
        lsl.l   #3,d0
        move.l  d0,d4
        moveq   #0,d0
        move.w  d5,d0
        lsl.l   #8,d0
        lsl.l   #3,d0
        move.l  d0,d5

; Half-open X overlap: actor right > hazard left and actor left < right.
        move.l  (ACTOR_X,a2),d6
        moveq   #0,d0
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        lsl.l   #8,d0
        move.l  d6,d1
        add.l   d0,d1
        cmp.l   (OBJECT_X,a3),d1
        ble.s   .outside
        sub.l   d0,d6
        move.l  (OBJECT_X,a3),d1
        add.l   d4,d1
        cmp.l   d1,d6
        bge.s   .outside

; Half-open Y overlap mirrors X.
        move.l  (ACTOR_Y,a2),d6
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        move.l  d6,d1
        add.l   d0,d1
        cmp.l   (OBJECT_Y,a3),d1
        ble.s   .outside
        sub.l   d0,d6
        move.l  (OBJECT_Y,a3),d1
        add.l   d5,d1
        cmp.l   d1,d6
        bge.s   .outside
        moveq   #1,d0
        bra.s   .done

.radius:
        tst.w   d5
        bne.s   .malformed
        moveq   #0,d0
        move.b  (OBJECT_PARAM+1,a3),d0
        bne.s   .radius_ready
        moveq   #VOCAB_HAZARD_RADIUS_DEFAULT,d0
.radius_ready:
        lsl.l   #8,d0
        movea.l a2,a0
        movea.l a3,a1
        bsr.w   VocabularyWithinRange
        bra.s   .done
.malformed:
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.outside:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; GameplayCampaignLatchDashDoorRoute
; In: a0=current/echo actor, a1=open door, d0.l=candidate X, d1.l=X velocity.
; If the authored completion mask requests DASH_ROUTE, latches it only when
; the current actor's full body clears this leaf during an active dash.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignLatchDashDoorRoute:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        movea.l a1,a3
        move.l  d0,d3
        move.l  d1,d4
        move.l  a2,d0
        cmpi.l  #GAMEPLAY_CURRENT_ACTOR,d0
        bne.w   .done
        move.w  (VOCAB_CAMPAIGN_COMPLETION_MASK).l,d0
        andi.w  #VOCAB_DEVICE_STATE_DASH_ROUTE,d0
        beq.w   .done
        lea     (VOCAB_ACTOR_BASE+GAMEPLAY_CURRENT_ACTOR_SLOT*VOCAB_ACTOR_BYTES).l,a0
        move.w  (VOCAB_ACTOR_FLAGS,a0),d0
        andi.w  #VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED,d0
        cmpi.w  #VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED,d0
        bne.w   .done
        tst.l   d4
        beq.w   .done

; The actor must overlap the 16x32 leaf vertically.
        move.l  (ACTOR_Y,a2),d6
        move.l  d6,d7
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d6
        add.l   d0,d7
        move.l  (OBJECT_Y,a3),d0
        cmp.l   d0,d7
        ble.w   .done
        addi.l  #CAMPAIGN_DOOR_HEIGHT_16_8,d0
        cmp.l   d0,d6
        bge.w   .done

        moveq   #0,d5
        move.w  (ACTOR_HALF_WIDTH,a2),d5
        lsl.l   #8,d5
        tst.l   d4
        bmi.s   .left
; Rightward proof requires the old left edge west of the leaf's right edge and
; the candidate left edge at or east of it.
        move.l  (OBJECT_X,a3),d0
        addi.l  #CAMPAIGN_DOOR_WIDTH_16_8,d0
        move.l  (ACTOR_X,a2),d1
        sub.l   d5,d1
        cmp.l   d0,d1
        bge.w   .done
        move.l  d3,d1
        sub.l   d5,d1
        cmp.l   d0,d1
        blt.w   .done
        bra.s   .latch
.left:
; Leftward proof is the exact mirror at the leaf's left edge.
        move.l  (OBJECT_X,a3),d0
        move.l  (ACTOR_X,a2),d1
        add.l   d5,d1
        cmp.l   d0,d1
        ble.w   .done
        move.l  d3,d1
        add.l   d5,d1
        cmp.l   d0,d1
        bgt.w   .done
.latch:
        moveq   #0,d0
        move.b  (VOCAB_CAMPAIGN_COMPLETION_OBJECT).l,d0
        lea     (DEVICE_POOL).l,a0
        moveq   #DEVICE_CAPACITY-1,d7
.completion:
        tst.b   (OBJECT_ACTIVE,a0)
        beq.s   .completion_next
        moveq   #0,d1
        move.b  (OBJECT_LOGICAL_ID,a0),d1
        cmp.w   d0,d1
        bne.s   .completion_next
        ori.w   #VOCAB_DEVICE_STATE_DASH_ROUTE,(OBJECT_STATE,a0)
        bra.s   .done
.completion_next:
        adda.w  #OBJECT_BYTES,a0
        dbf     d7,.completion
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

; GameplayCampaignMoveActorByDelta
; In: a0=active actor, d0.l=signed X delta, d1.l=signed Y delta.
; Applies conveyor/transport displacement through the same authoritative
; room/static/door resolver as input motion, then restores input velocity.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignMoveActorByDelta:
        movem.l d3-d5/a2,-(a7)
        movea.l a0,a2
        move.l  (ACTOR_VX,a2),d3
        move.l  (ACTOR_VY,a2),d4
        move.w  (ACTOR_FLAGS,a2),d5
        andi.w  #ACTOR_FLAG_COLLISION_X|ACTOR_FLAG_COLLISION_Y,d5
        move.l  d0,(ACTOR_VX,a2)
        move.l  d1,(ACTOR_VY,a2)
        movea.l a2,a0
        bsr.w   GameplayMoveActorAxisSeparated
        or.w    d5,(ACTOR_FLAGS,a2)
        move.l  d3,(ACTOR_VX,a2)
        move.l  d4,(ACTOR_VY,a2)
        movem.l (a7)+,d3-d5/a2
        rts

; GameplayCampaignObjectBlocked
; In: a0=active object's candidate/current signed 16.8 center.
; Out: d0.w=one when an 8px-half-extent body overlaps bounds, a canonical
; static rectangle, or a closed vocabulary door. No state is mutated.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignObjectBlocked:
        movem.l d3-d7/a2,-(a7)
        movea.l a0,a2
        move.l  (OBJECT_X,a2),d3
        move.l  d3,d4
        subi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d3
        addi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d4
        move.l  (OBJECT_Y,a2),d5
        move.l  d5,d6
        subi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d5
        addi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d6

        moveq   #0,d0
        move.w  (ENGINE_ROOM_LEFT).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d3
        blt.w   .hit
        moveq   #0,d0
        move.w  (ENGINE_ROOM_RIGHT).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d4
        bgt.w   .hit
        moveq   #0,d0
        move.w  (ENGINE_ROOM_TOP).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d5
        blt.w   .hit
        moveq   #0,d0
        move.w  (ENGINE_ROOM_BOTTOM).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d6
        bgt.w   .hit

        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        cmpi.w  #STATIC_RECT_CAPACITY,d7
        bls.s   .rect_count_ready
        moveq   #STATIC_RECT_CAPACITY,d7
.rect_count_ready:
        tst.w   d7
        beq.s   .doors
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a0
.rect:
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a0),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d4
        ble.s   .rect_next
        moveq   #0,d1
        move.w  (STATIC_RECT_WIDTH,a0),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d3
        bge.s   .rect_next
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a0),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d6
        ble.s   .rect_next
        moveq   #0,d1
        move.w  (STATIC_RECT_HEIGHT,a0),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d5
        bge.s   .rect_next
        bra.w   .hit
.rect_next:
        adda.w  #STATIC_RECT_BYTES,a0
        dbf     d7,.rect

.doors:
        lea     (DEVICE_POOL).l,a0
        moveq   #DEVICE_CAPACITY-1,d7
.door:
        tst.b   (OBJECT_ACTIVE,a0)
        beq.s   .door_next
        move.w  (OBJECT_PARAM,a0),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.s   .door_next
        move.w  (OBJECT_STATE,a0),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .door_next
        andi.w  #VOCAB_DEVICE_STATE_OPEN,d1
        bne.s   .door_next
        move.l  (OBJECT_X,a0),d0
        cmp.l   d0,d4
        ble.s   .door_next
        addi.l  #CAMPAIGN_DOOR_WIDTH_16_8,d0
        cmp.l   d0,d3
        bge.s   .door_next
        move.l  (OBJECT_Y,a0),d0
        cmp.l   d0,d6
        ble.s   .door_next
        addi.l  #CAMPAIGN_DOOR_HEIGHT_16_8,d0
        cmp.l   d0,d5
        bge.s   .door_next
        bra.s   .hit
.door_next:
        adda.w  #OBJECT_BYTES,a0
        dbf     d7,.door
        moveq   #0,d0
        bra.s   .done
.hit:
        moveq   #1,d0
.done:
        movem.l (a7)+,d3-d7/a2
        rts

; GameplayCampaignMoveObjectAxisSeparated
; In: a0=active object with desired OBJECT_VX/VY.
; Moves X then Y, retaining the last non-blocked position on each axis.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignMoveObjectAxisSeparated:
        movem.l d3-d4/a2,-(a7)
        movea.l a0,a2
        move.l  (OBJECT_X,a2),d3
        move.l  (OBJECT_VX,a2),d0
        beq.s   .y
        add.l   d0,(OBJECT_X,a2)
        movea.l a2,a0
        bsr.w   GameplayCampaignObjectBlocked
        tst.w   d0
        beq.s   .y
        move.l  d3,(OBJECT_X,a2)
.y:
        move.l  (OBJECT_Y,a2),d4
        move.l  (OBJECT_VY,a2),d0
        beq.s   .done
        add.l   d0,(OBJECT_Y,a2)
        movea.l a2,a0
        bsr.w   GameplayCampaignObjectBlocked
        tst.w   d0
        beq.s   .done
        move.l  d4,(OBJECT_Y,a2)
.done:
        movem.l (a7)+,d3-d4/a2
        rts

; GameplayCampaignClipBeam
; In: a0=one finite axis-aligned vocabulary beam record.
; Clamps its byte length to the nearest room edge, canonical rectangle, or
; closed door and marks BLOCKED when shortened. Geometry is read-only.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignClipBeam:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        moveq   #0,d3
        move.w  (VOCAB_BEAM_X,a2),d3
        moveq   #0,d4
        move.w  (VOCAB_BEAM_Y,a2),d4
        moveq   #0,d7
        move.b  (VOCAB_BEAM_LENGTH,a2),d7

; Room boundary is the initial candidate obstruction.
        cmpi.b  #VOCAB_BEAM_DIRECTION_RIGHT,(VOCAB_BEAM_DIRECTION,a2)
        beq.s   .room_right
        cmpi.b  #VOCAB_BEAM_DIRECTION_DOWN,(VOCAB_BEAM_DIRECTION,a2)
        beq.s   .room_down
        cmpi.b  #VOCAB_BEAM_DIRECTION_LEFT,(VOCAB_BEAM_DIRECTION,a2)
        beq.s   .room_left
        cmpi.b  #VOCAB_BEAM_DIRECTION_UP,(VOCAB_BEAM_DIRECTION,a2)
        bne.w   .zero
        move.w  d4,d0
        sub.w   (ENGINE_ROOM_TOP).l,d0
        bra.s   .room_candidate
.room_right:
        move.w  (ENGINE_ROOM_RIGHT).l,d0
        sub.w   d3,d0
        bra.s   .room_candidate
.room_down:
        move.w  (ENGINE_ROOM_BOTTOM).l,d0
        sub.w   d4,d0
        bra.s   .room_candidate
.room_left:
        move.w  d3,d0
        sub.w   (ENGINE_ROOM_LEFT).l,d0
.room_candidate:
        tst.w   d0
        bpl.s   .room_nonnegative
        moveq   #0,d0
.room_nonnegative:
        cmp.w   d7,d0
        bhs.s   .rectangles
        move.w  d0,d7
        ori.b   #VOCAB_BEAM_FLAG_BLOCKED,(VOCAB_BEAM_FLAGS,a2)

.rectangles:
        moveq   #0,d5
        move.w  (ENGINE_RECT_COUNT).l,d5
        cmpi.w  #STATIC_RECT_CAPACITY,d5
        bls.s   .rect_count_ready
        moveq   #STATIC_RECT_CAPACITY,d5
.rect_count_ready:
        tst.w   d5
        beq.s   .doors
        subq.w  #1,d5
        lea     (CANONICAL_RECTS).l,a3
.rect:
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        moveq   #0,d1
        move.w  (STATIC_RECT_Y,a3),d1
        moveq   #0,d2
        move.w  (STATIC_RECT_WIDTH,a3),d2
        moveq   #0,d6
        move.w  (STATIC_RECT_HEIGHT,a3),d6
        bsr.w   .consider
        adda.w  #STATIC_RECT_BYTES,a3
        dbf     d5,.rect

.doors:
        lea     (DEVICE_POOL).l,a3
        moveq   #DEVICE_CAPACITY-1,d5
.door:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .door_next
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.s   .door_next
        move.w  (OBJECT_STATE,a3),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .door_next
        andi.w  #VOCAB_DEVICE_STATE_OPEN,d1
        bne.s   .door_next
        move.l  (OBJECT_X,a3),d0
        asr.l   #8,d0
        move.l  (OBJECT_Y,a3),d1
        asr.l   #8,d1
        moveq   #16,d2
        moveq   #32,d6
        bsr.w   .consider
.door_next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d5,.door
        move.b  d7,(VOCAB_BEAM_LENGTH,a2)
        bra.w   .done

.zero:
        moveq   #0,d7
        ori.b   #VOCAB_BEAM_FLAG_BLOCKED,(VOCAB_BEAM_FLAGS,a2)
        move.b  d7,(VOCAB_BEAM_LENGTH,a2)
        bra.w   .done

; Consider half-open AABB x=d0, y=d1, width=d2, height=d6.
.consider:
        cmpi.b  #VOCAB_BEAM_DIRECTION_RIGHT,(VOCAB_BEAM_DIRECTION,a2)
        beq.s   .consider_right
        cmpi.b  #VOCAB_BEAM_DIRECTION_DOWN,(VOCAB_BEAM_DIRECTION,a2)
        beq.s   .consider_down
        cmpi.b  #VOCAB_BEAM_DIRECTION_LEFT,(VOCAB_BEAM_DIRECTION,a2)
        beq.s   .consider_left
; Up.
        cmp.w   d0,d3
        blt.s   .consider_done
        add.w   d2,d0
        cmp.w   d0,d3
        bge.s   .consider_done
        move.w  d1,d0
        add.w   d6,d0
        cmp.w   d4,d1
        bge.s   .consider_done
        move.w  d4,d1
        sub.w   d0,d1
        bpl.s   .distance_ready
        moveq   #0,d1
        bra.s   .distance_ready
.consider_right:
        cmp.w   d1,d4
        blt.s   .consider_done
        add.w   d6,d1
        cmp.w   d1,d4
        bge.s   .consider_done
        move.w  d0,d1
        add.w   d2,d0
        cmp.w   d3,d0
        ble.s   .consider_done
        sub.w   d3,d1
        bpl.s   .distance_ready
        moveq   #0,d1
        bra.s   .distance_ready
.consider_down:
        cmp.w   d0,d3
        blt.s   .consider_done
        add.w   d2,d0
        cmp.w   d0,d3
        bge.s   .consider_done
        move.w  d1,d0
        add.w   d6,d0
        cmp.w   d4,d0
        ble.s   .consider_done
        sub.w   d4,d1
        bpl.s   .distance_ready
        moveq   #0,d1
        bra.s   .distance_ready
.consider_left:
        cmp.w   d1,d4
        blt.s   .consider_done
        add.w   d6,d1
        cmp.w   d1,d4
        bge.s   .consider_done
        move.w  d0,d1
        add.w   d2,d1
        cmp.w   d3,d0
        bge.s   .consider_done
        move.w  d3,d0
        sub.w   d1,d0
        move.w  d0,d1
        bpl.s   .distance_ready
        moveq   #0,d1
.distance_ready:
        cmp.w   d7,d1
        bhs.s   .consider_done
        move.w  d1,d7
        ori.b   #VOCAB_BEAM_FLAG_BLOCKED,(VOCAB_BEAM_FLAGS,a2)
.consider_done:
        rts
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

; GameplayCampaignResolveDoorX
; In: a0=valid actor slot, d0.l=room/static-corrected candidate X,
;     d1.l=signed X velocity.
; Out: d0.l=door-corrected candidate X, d1.w=one on door collision else zero.
; Also publishes ACTOR_FLAG_COLLISION_X and ENGINE_FLAG_COLLISION on contact.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignResolveDoorX:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        move.l  d0,d3
        move.l  d1,d4
        suba.l  a0,a0                   ; collision result
        tst.l   d4
        beq.w   .done

; Preserve the actor half-width in a caller-saved address register.
        moveq   #0,d0
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        lsl.l   #8,d0
        movea.l d0,a1

; X resolution uses the actor's beginning-of-axis vertical interval.
        move.l  (ACTOR_Y,a2),d5
        move.l  d5,d6
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d5                   ; actor top
        add.l   d0,d6                   ; actor bottom

        lea     (DEVICE_POOL).l,a3
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d7
        cmpi.w  #DEVICE_CAPACITY,d7
        bls.s   .door_bound_ready
        moveq   #DEVICE_CAPACITY,d7
.door_bound_ready:
        tst.w   d7
        beq.w   .done
        subq.w  #1,d7
.door:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.w   .next
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.w   .next
        move.w  (OBJECT_STATE,a3),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .next
        andi.w  #VOCAB_DEVICE_STATE_OPEN,d1
        beq.s   .closed
        movem.l a0-a1,-(a7)
        movea.l a2,a0
        movea.l a3,a1
        move.l  d3,d0
        move.l  d4,d1
        bsr.w   GameplayCampaignLatchDashDoorRoute
        movem.l (a7)+,a0-a1
        bra.w   .next
.closed:

; Reject half-open non-overlap on the unchanged Y axis.
        move.l  (OBJECT_Y,a3),d0
        cmp.l   d0,d6
        ble.s   .next
        move.l  (OBJECT_Y,a3),d0
        addi.l  #CAMPAIGN_DOOR_HEIGHT_16_8,d0
        cmp.l   d0,d5
        bge.s   .next

; Reject half-open non-overlap at the proposed X coordinate.
        move.l  d3,d1
        move.l  a1,d2
        add.l   d2,d1                   ; actor right
        move.l  (OBJECT_X,a3),d0
        cmp.l   d0,d1
        ble.s   .next
        move.l  d3,d1
        sub.l   d2,d1                   ; actor left
        move.l  (OBJECT_X,a3),d0
        addi.l  #CAMPAIGN_DOOR_WIDTH_16_8,d0
        cmp.l   d0,d1
        bge.s   .next

        tst.l   d4
        bmi.s   .moving_left
; Moving right: stop immediately left of the closed door.
        move.l  (OBJECT_X,a3),d0
        move.l  a1,d1
        sub.l   d1,d0
        move.l  d0,d3
        bra.s   .collided
.moving_left:
; Moving left: stop immediately right of the closed door.
        move.l  (OBJECT_X,a3),d0
        addi.l  #CAMPAIGN_DOOR_WIDTH_16_8,d0
        move.l  a1,d1
        add.l   d1,d0
        move.l  d0,d3
.collided:
        moveq   #1,d0
        movea.l d0,a0
        ori.w   #ACTOR_FLAG_COLLISION_X,(ACTOR_FLAGS,a2)
        ori.w   #ENGINE_FLAG_COLLISION,(ENGINE_FLAGS).l
.next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d7,.door
.done:
        move.l  d3,d0
        move.l  a0,d1
        movem.l (a7)+,d3-d7/a2-a3
        rts

; GameplayCampaignResolveDoorY
; In: a0=valid actor slot, d0.l=room/static-corrected candidate Y,
;     d1.l=signed Y velocity. ACTOR_X must already contain corrected X.
; Out: d0.l=door-corrected candidate Y, d1.w=one on door collision else zero.
; Also publishes ACTOR_FLAG_COLLISION_Y and ENGINE_FLAG_COLLISION on contact.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignResolveDoorY:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        move.l  d0,d3
        move.l  d1,d4
        suba.l  a0,a0                   ; collision result
        tst.l   d4
        beq.w   .done

; Preserve the actor half-height in a caller-saved address register.
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        movea.l d0,a1

; Y resolution uses corrected X from the first axis.
        move.l  (ACTOR_X,a2),d5
        move.l  d5,d6
        moveq   #0,d0
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d5                   ; actor left
        add.l   d0,d6                   ; actor right

        lea     (DEVICE_POOL).l,a3
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d7
        cmpi.w  #DEVICE_CAPACITY,d7
        bls.s   .door_bound_ready
        moveq   #DEVICE_CAPACITY,d7
.door_bound_ready:
        tst.w   d7
        beq.w   .done
        subq.w  #1,d7
.door:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.w   .next
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.w   .next
        move.w  (OBJECT_STATE,a3),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .next
        andi.w  #VOCAB_DEVICE_STATE_OPEN,d1
        bne.w   .next

; Reject half-open non-overlap on corrected X.
        move.l  (OBJECT_X,a3),d0
        cmp.l   d0,d6
        ble.s   .next
        move.l  (OBJECT_X,a3),d0
        addi.l  #CAMPAIGN_DOOR_WIDTH_16_8,d0
        cmp.l   d0,d5
        bge.s   .next

; Reject half-open non-overlap at the proposed Y coordinate.
        move.l  d3,d1
        move.l  a1,d2
        add.l   d2,d1                   ; actor bottom
        move.l  (OBJECT_Y,a3),d0
        cmp.l   d0,d1
        ble.s   .next
        move.l  d3,d1
        sub.l   d2,d1                   ; actor top
        move.l  (OBJECT_Y,a3),d0
        addi.l  #CAMPAIGN_DOOR_HEIGHT_16_8,d0
        cmp.l   d0,d1
        bge.s   .next

        tst.l   d4
        bmi.s   .moving_up
; Moving down: stop immediately above the closed door.
        move.l  (OBJECT_Y,a3),d0
        move.l  a1,d1
        sub.l   d1,d0
        move.l  d0,d3
        bra.s   .collided
.moving_up:
; Moving up: stop immediately below the closed door.
        move.l  (OBJECT_Y,a3),d0
        addi.l  #CAMPAIGN_DOOR_HEIGHT_16_8,d0
        move.l  a1,d1
        add.l   d1,d0
        move.l  d0,d3
.collided:
        moveq   #1,d0
        movea.l d0,a0
        ori.w   #ACTOR_FLAG_COLLISION_Y,(ACTOR_FLAGS,a2)
        ori.w   #ENGINE_FLAG_COLLISION,(ENGINE_FLAGS).l
.next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d7,.door
.done:
        move.l  d3,d0
        move.l  a0,d1
        movem.l (a7)+,d3-d7/a2-a3
        rts

; GameplayCampaignProjectileMayEnterNodeRect
; In: a0=active projectile, a1=static rectangle its AABB overlaps.
; Out: d0.w=one only when a friendly axis-aligned pulse is travelling toward
; an active, exposed security node contained by this exact rectangle and is
; aligned with the node hit tolerance. The caller still owns damage.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignProjectileMayEnterNodeRect:
        movem.l d3-d7/a2-a4,-(a7)
        movea.l a0,a2
        movea.l a1,a3
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .reject
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_PROJECTILE_STATE_PLAYER|VOCAB_PROJECTILE_STATE_REFLECTED,d0
        beq.w   .reject

        lea     (DEVICE_POOL).l,a4
        moveq   #DEVICE_CAPACITY-1,d7
.node:
        tst.b   (OBJECT_ACTIVE,a4)
        beq.w   .next
        move.w  (OBJECT_PARAM,a4),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0
        bne.w   .next
        move.w  (OBJECT_STATE,a4),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .next
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d1
        beq.w   .next

; The node point must be mounted inside this particular half-open rectangle.
        move.l  (OBJECT_X,a4),d3
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d3
        blt.w   .next
        moveq   #0,d1
        move.w  (STATIC_RECT_WIDTH,a3),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d3
        bge.w   .next

        move.l  (OBJECT_Y,a4),d4
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d4
        blt.w   .next
        moveq   #0,d1
        move.w  (STATIC_RECT_HEIGHT,a3),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d4
        bge.w   .next

; Player pulses are horizontal today; the vertical path also keeps reflected
; axis-aligned pulses well-defined. Diagonal or stationary pulses get no hole.
        move.l  (OBJECT_VX,a2),d5
        beq.s   .vertical
        tst.l   (OBJECT_VY,a2)
        bne.s   .next
        move.l  d4,d0
        sub.l   (OBJECT_Y,a2),d0
        bpl.s   .horizontal_offset
        neg.l   d0
.horizontal_offset:
        cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bhi.s   .next
        cmp.l   (OBJECT_X,a2),d3
        tst.l   d5
        bmi.s   .travelling_left
        blt.s   .next
        bra.s   .accept
.travelling_left:
        bgt.s   .next
        bra.s   .accept

.vertical:
        move.l  (OBJECT_VY,a2),d5
        beq.s   .next
        move.l  d3,d0
        sub.l   (OBJECT_X,a2),d0
        bpl.s   .vertical_offset
        neg.l   d0
.vertical_offset:
        cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bhi.s   .next
        cmp.l   (OBJECT_Y,a2),d4
        tst.l   d5
        bmi.s   .travelling_up
        blt.s   .next
        bra.s   .accept
.travelling_up:
        bgt.s   .next
.accept:
        moveq   #1,d0
        bra.s   .done
.next:
        adda.w  #OBJECT_BYTES,a4
        dbf     d7,.node
.reject:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d7/a2-a4
        rts

; GameplayCampaignProjectileHitsStatic
; In: a0=projectile object at its candidate/current signed 16.8 center.
; Out: d0.w=one when its fixed AABB leaves the room, overlaps any canonical
; static rectangle, or overlaps a closed campaign door. No slot is freed.
; A friendly axis-aligned pulse may traverse only the exact backing rectangle
; of an aligned, exposed security node. The caller's immediately following
; enemy/node scan remains the sole damage owner and consumes that pulse.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayCampaignProjectileHitsStatic:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a3
        tst.b   (OBJECT_ACTIVE,a3)
        beq.w   .clear

        move.l  (OBJECT_X,a3),d3
        move.l  d3,d4
        subi.l  #VOCAB_PROJECTILE_HALF_EXTENT_16_8,d3
        addi.l  #VOCAB_PROJECTILE_HALF_EXTENT_16_8,d4
        move.l  (OBJECT_Y,a3),d5
        move.l  d5,d6
        subi.l  #VOCAB_PROJECTILE_HALF_EXTENT_16_8,d5
        addi.l  #VOCAB_PROJECTILE_HALF_EXTENT_16_8,d6

; Room bounds are half-open; touching an edge remains inside.
        moveq   #0,d0
        move.w  (ENGINE_ROOM_LEFT).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d3
        blt.w   .hit
        moveq   #0,d0
        move.w  (ENGINE_ROOM_RIGHT).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d4
        bgt.w   .hit
        moveq   #0,d0
        move.w  (ENGINE_ROOM_TOP).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d5
        blt.w   .hit
        moveq   #0,d0
        move.w  (ENGINE_ROOM_BOTTOM).l,d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d6
        bgt.w   .hit

; Clamp a corrupt count before the bounded physical-order scan.
        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        cmpi.w  #STATIC_RECT_CAPACITY,d7
        bls.s   .count_ready
        moveq   #STATIC_RECT_CAPACITY,d7
.count_ready:
        tst.w   d7
        beq.s   .doors
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a2
.rectangle:
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a2),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d4
        ble.s   .next
        moveq   #0,d1
        move.w  (STATIC_RECT_WIDTH,a2),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d3
        bge.s   .next

        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a2),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d6
        ble.s   .next
        moveq   #0,d1
        move.w  (STATIC_RECT_HEIGHT,a2),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d5
        bge.s   .next
        movea.l a2,a1
        movea.l a3,a0
        bsr.w   GameplayCampaignProjectileMayEnterNodeRect
        tst.w   d0
        bne.s   .next
        bra.s   .hit
.next:
        adda.w  #STATIC_RECT_BYTES,a2
        dbf     d7,.rectangle

; Closed doors are live world collision for both player and enemy pulses.
.doors:
        lea     (DEVICE_POOL).l,a2
        moveq   #DEVICE_CAPACITY-1,d7
.door:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .door_next
        move.w  (OBJECT_PARAM,a2),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.s   .door_next
        move.w  (OBJECT_STATE,a2),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .door_next
        andi.w  #VOCAB_DEVICE_STATE_OPEN,d1
        bne.s   .door_next

        move.l  (OBJECT_X,a2),d0
        cmp.l   d0,d4
        ble.s   .door_next
        addi.l  #CAMPAIGN_DOOR_WIDTH_16_8,d0
        cmp.l   d0,d3
        bge.s   .door_next
        move.l  (OBJECT_Y,a2),d0
        cmp.l   d0,d6
        ble.s   .door_next
        addi.l  #CAMPAIGN_DOOR_HEIGHT_16_8,d0
        cmp.l   d0,d5
        bge.s   .door_next
        bra.s   .hit
.door_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.door
.clear:
        moveq   #0,d0
        bra.s   .done
.hit:
        moveq   #1,d0
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

        assert  ACTOR_BYTES=$20,"campaign collision actor stride changed"
        assert  VOCAB_ACTOR_BYTES=$08,"campaign collision extension stride changed"
        assert  CAMPAIGN_DOOR_WIDTH_16_8=16*256,"campaign door width changed"
        assert  CAMPAIGN_DOOR_HEIGHT_16_8=32*256,"campaign door height changed"
