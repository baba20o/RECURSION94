; Deterministic actor collision against room bounds and ordered static AABBs.
; Positions are signed 16.8 centers; rectangle and half-extents are pixels.

; EngineMoveActorAxisSeparated
; In: a0=actor slot. Out: actor X/Y and collision flags updated.
; Clobbers d0-d2/a0-a1 and preserves all callee-saved registers.
EngineMoveActorAxisSeparated:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        andi.w  #$FFFC,(ACTOR_FLAGS,a2)

; Resolve X first, using the beginning-of-axis Y.
        move.l  (ACTOR_X,a2),d3
        move.l  (ACTOR_VX,a2),d4
        add.l   d4,d3

        moveq   #0,d5
        move.w  (ENGINE_ROOM_LEFT).l,d5
        ext.l   d5
        moveq   #0,d6
        move.w  (ACTOR_HALF_WIDTH,a2),d6
        add.l   d6,d5
        lsl.l   #8,d5
        cmp.l   d5,d3
        bge.s   .x_min_ok
        move.l  d5,d3
        ori.w   #ACTOR_FLAG_COLLISION_X,(ACTOR_FLAGS,a2)
.x_min_ok:
        moveq   #0,d5
        move.w  (ENGINE_ROOM_RIGHT).l,d5
        ext.l   d5
        moveq   #0,d6
        move.w  (ACTOR_HALF_WIDTH,a2),d6
        sub.l   d6,d5
        lsl.l   #8,d5
        cmp.l   d5,d3
        ble.s   .x_max_ok
        move.l  d5,d3
        ori.w   #ACTOR_FLAG_COLLISION_X,(ACTOR_FLAGS,a2)
.x_max_ok:
        tst.l   d4
        beq.w   .store_x

        move.l  (ACTOR_Y,a2),d5
        move.l  d5,d6
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d5                   ; actor top
        add.l   d0,d6                   ; actor bottom

        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        beq.w   .store_x
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a3
.x_rect_loop:
; Reject non-overlap on Y (half-open intervals).
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d6
        ble.s   .x_next_rect
        moveq   #0,d1
        move.w  (STATIC_RECT_HEIGHT,a3),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d5
        bge.s   .x_next_rect

; Reject non-overlap on proposed X.
        move.l  d3,d1
        move.l  d3,d2
        moveq   #0,d0
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d1                   ; actor left
        add.l   d0,d2                   ; actor right
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d2
        ble.s   .x_next_rect
        moveq   #0,d2
        move.w  (STATIC_RECT_WIDTH,a3),d2
        lsl.l   #8,d2
        add.l   d0,d2                   ; rectangle right
        cmp.l   d2,d1
        bge.s   .x_next_rect

        tst.l   d4
        bmi.s   .x_from_left
; Moving right: place actor immediately left of the rectangle.
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        moveq   #0,d1
        move.w  (ACTOR_HALF_WIDTH,a2),d1
        sub.l   d1,d0
        lsl.l   #8,d0
        move.l  d0,d3
        bra.s   .x_collided
.x_from_left:
; Moving left: place actor immediately right of the rectangle.
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        moveq   #0,d1
        move.w  (STATIC_RECT_WIDTH,a3),d1
        add.l   d1,d0
        moveq   #0,d1
        move.w  (ACTOR_HALF_WIDTH,a2),d1
        add.l   d1,d0
        lsl.l   #8,d0
        move.l  d0,d3
.x_collided:
        ori.w   #ACTOR_FLAG_COLLISION_X,(ACTOR_FLAGS,a2)
.x_next_rect:
        adda.w  #STATIC_RECT_BYTES,a3
        dbf     d7,.x_rect_loop
.store_x:
        move.l  d3,(ACTOR_X,a2)

; Resolve Y second, using the corrected X from the first axis.
        move.l  (ACTOR_Y,a2),d3
        move.l  (ACTOR_VY,a2),d4
        add.l   d4,d3

        moveq   #0,d5
        move.w  (ENGINE_ROOM_TOP).l,d5
        ext.l   d5
        moveq   #0,d6
        move.w  (ACTOR_HALF_HEIGHT,a2),d6
        add.l   d6,d5
        lsl.l   #8,d5
        cmp.l   d5,d3
        bge.s   .y_min_ok
        move.l  d5,d3
        ori.w   #ACTOR_FLAG_COLLISION_Y,(ACTOR_FLAGS,a2)
.y_min_ok:
        moveq   #0,d5
        move.w  (ENGINE_ROOM_BOTTOM).l,d5
        ext.l   d5
        moveq   #0,d6
        move.w  (ACTOR_HALF_HEIGHT,a2),d6
        sub.l   d6,d5
        lsl.l   #8,d5
        cmp.l   d5,d3
        ble.s   .y_max_ok
        move.l  d5,d3
        ori.w   #ACTOR_FLAG_COLLISION_Y,(ACTOR_FLAGS,a2)
.y_max_ok:
        tst.l   d4
        beq.w   .store_y

        move.l  (ACTOR_X,a2),d5
        move.l  d5,d6
        moveq   #0,d0
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d5                   ; actor left
        add.l   d0,d6                   ; actor right

        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        beq.w   .store_y
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a3
.y_rect_loop:
; Reject non-overlap on corrected X.
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d6
        ble.s   .y_next_rect
        moveq   #0,d1
        move.w  (STATIC_RECT_WIDTH,a3),d1
        lsl.l   #8,d1
        add.l   d0,d1
        cmp.l   d1,d5
        bge.s   .y_next_rect

; Reject non-overlap on proposed Y.
        move.l  d3,d1
        move.l  d3,d2
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d1                   ; actor top
        add.l   d0,d2                   ; actor bottom
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d2
        ble.s   .y_next_rect
        moveq   #0,d2
        move.w  (STATIC_RECT_HEIGHT,a3),d2
        lsl.l   #8,d2
        add.l   d0,d2                   ; rectangle bottom
        cmp.l   d2,d1
        bge.s   .y_next_rect

        tst.l   d4
        bmi.s   .y_from_above
; Moving down: place actor immediately above the rectangle.
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        moveq   #0,d1
        move.w  (ACTOR_HALF_HEIGHT,a2),d1
        sub.l   d1,d0
        lsl.l   #8,d0
        move.l  d0,d3
        bra.s   .y_collided
.y_from_above:
; Moving up: place actor immediately below the rectangle.
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        moveq   #0,d1
        move.w  (STATIC_RECT_HEIGHT,a3),d1
        add.l   d1,d0
        moveq   #0,d1
        move.w  (ACTOR_HALF_HEIGHT,a2),d1
        add.l   d1,d0
        lsl.l   #8,d0
        move.l  d0,d3
.y_collided:
        ori.w   #ACTOR_FLAG_COLLISION_Y,(ACTOR_FLAGS,a2)
.y_next_rect:
        adda.w  #STATIC_RECT_BYTES,a3
        dbf     d7,.y_rect_loop
.store_y:
        move.l  d3,(ACTOR_Y,a2)

        move.w  (ACTOR_FLAGS,a2),d0
        andi.w  #ACTOR_FLAG_COLLISION_X|ACTOR_FLAG_COLLISION_Y,d0
        beq.s   .done
        ori.w   #ENGINE_FLAG_COLLISION,(ENGINE_FLAGS).l
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts
