; Replay-driven actor input and static/door collision.

; GameplaySanitizeInput
; In/Out: d0.b=UDLRABC. Bit 7 is cleared; opposing axes become neutral.
; Clobbers d1.
GameplaySanitizeInput:
        andi.b  #INPUT_ALLOWED_MASK,d0
        move.w  d0,d1
        andi.w  #INPUT_UP|INPUT_DOWN,d1
        cmpi.w  #INPUT_UP|INPUT_DOWN,d1
        bne.s   .vertical_ready
        andi.b  #$FC,d0
.vertical_ready:
        move.w  d0,d1
        andi.w  #INPUT_LEFT|INPUT_RIGHT,d1
        cmpi.w  #INPUT_LEFT|INPUT_RIGHT,d1
        bne.s   .done
        andi.b  #$F3,d0
.done:
        rts

; GameplayInitializeActor
; In: a0=actor slot, d0.b=role, d1.w=logical replay-bank ID.
; Clobbers d0-d2/a0-a1.
GameplayInitializeActor:
        move.b  #$01,(ACTOR_ACTIVE,a0)
        move.b  d0,(ACTOR_ROLE,a0)
        clr.w   (ACTOR_FLAGS,a0)
        move.l  (ENGINE_SPAWN_X).l,d2
        move.l  d2,(ACTOR_X,a0)
        move.l  (ENGINE_SPAWN_Y).l,d2
        move.l  d2,(ACTOR_Y,a0)
        clr.l   (ACTOR_VX,a0)
        clr.l   (ACTOR_VY,a0)
        move.w  #4,(ACTOR_HALF_WIDTH,a0)
        move.w  #4,(ACTOR_HALF_HEIGHT,a0)
        clr.b   (ACTOR_INPUT,a0)
        clr.b   (ACTOR_PREVIOUS_INPUT,a0)
        move.w  #1,(ACTOR_HEALTH,a0)
        move.w  d1,(ACTOR_LOGICAL_ID,a0)
        clr.w   (ACTOR_RESERVED,a0)
        rts

; GameplayApplyActorInput
; In: a0=active actor, d0.b=held input.
; Out: d0.b=sanitized held, d1.b=pressed, d2.b=released.
; Applies signed 16.8 motion, door-aware static collision, and A-edge fire.
; Preserves d3-d7/a2-a6.
GameplayApplyActorInput:
        movem.l d3-d7/a2,-(a7)
        movea.l a0,a2
        tst.b   (ACTOR_ACTIVE,a2)
        beq.w   .inactive
        bsr.w   GameplaySanitizeInput
        moveq   #0,d3
        move.b  d0,d3
        moveq   #0,d4
        move.b  (ACTOR_INPUT,a2),d4
        move.b  d4,(ACTOR_PREVIOUS_INPUT,a2)
        move.b  d3,(ACTOR_INPUT,a2)

        move.b  d4,d5
        not.b   d5
        and.b   d3,d5
        move.b  d3,d6
        not.b   d6
        and.b   d4,d6

; Derive deterministic normalized velocity.
        moveq   #0,d7
        btst    #2,d3
        beq.s   .not_left
        moveq   #-1,d7
.not_left:
        btst    #3,d3
        beq.s   .x_ready
        moveq   #1,d7
.x_ready:
        moveq   #0,d2
        btst    #0,d3
        beq.s   .not_up
        moveq   #-1,d2
.not_up:
        btst    #1,d3
        beq.s   .y_ready
        moveq   #1,d2
.y_ready:
        move.l  #ENGINE_CARDINAL_SPEED,d1
        tst.w   d7
        beq.s   .speed_ready
        tst.w   d2
        beq.s   .speed_ready
        move.l  #ENGINE_DIAGONAL_SPEED,d1
.speed_ready:
        moveq   #0,d0
        tst.w   d7
        beq.s   .store_vx
        move.l  d1,d0
        tst.w   d7
        bpl.s   .face_right
        neg.l   d0
        ori.w   #GAMEPLAY_ACTOR_FLAG_FACING_LEFT,(ACTOR_FLAGS,a2)
        bra.s   .store_vx
.face_right:
        andi.w  #$7FFF,(ACTOR_FLAGS,a2)
.store_vx:
        move.l  d0,(ACTOR_VX,a2)

        moveq   #0,d0
        tst.w   d2
        beq.s   .store_vy
        move.l  d1,d0
        tst.w   d2
        bpl.s   .store_vy
        neg.l   d0
.store_vy:
        move.l  d0,(ACTOR_VY,a2)

; C is edge-triggered for every replay actor. Dash/phase rewrites the derived
; velocity before the one authoritative movement step.
        moveq   #0,d0
        move.b  d3,d0
        moveq   #0,d1
        move.b  d5,d1
        movea.l a2,a0
        bsr.w   VocabularyApplyDashPhase

        movea.l a2,a0
        bsr.w   GameplayMoveActorAxisSeparated

; A is edge-triggered for echoes and current alike.
        moveq   #0,d0
        move.b  d5,d0
        andi.b  #INPUT_A,d0
        beq.s   .interaction
        move.b  #$01,(ENGINE_FACING_X).l
        move.w  (ACTOR_FLAGS,a2),d0
        andi.w  #GAMEPLAY_ACTOR_FLAG_FACING_LEFT,d0
        beq.s   .spawn
        move.b  #$FF,(ENGINE_FACING_X).l
.spawn:
        movea.l a2,a0
        bsr.w   EngineSpawnProjectileFromActor

; B resolves in chronological actor order using the same world-state routine
; for echoes and the current incarnation.
.interaction:
        moveq   #0,d0
        move.b  d5,d0
        andi.b  #INPUT_B,d0
        beq.s   .interaction_held
        movea.l a2,a0
        bsr.w   VocabularyResolveInteraction
        bra.s   .outputs
.interaction_held:
        moveq   #0,d0
        move.b  d3,d0
        andi.b  #INPUT_B,d0
        beq.s   .outputs
        movea.l a2,a0
        bsr.w   VocabularySustainHackInteraction
.outputs:
        moveq   #0,d0
        move.b  d3,d0
        moveq   #0,d1
        move.b  d5,d1
        moveq   #0,d2
        move.b  d6,d2
        bra.s   .done
.inactive:
        moveq   #0,d0
        moveq   #0,d1
        moveq   #0,d2
.done:
        movem.l (a7)+,d3-d7/a2
        rts

; GameplayMoveActorAxisSeparated
; In: a0=actor slot. Resolves X then Y in full signed 16.8 precision.
; The first encounter rectangle is skipped only when projected door state says
; open. Actors never test one another, so echoes do not body-block.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
GameplayMoveActorAxisSeparated:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        andi.w  #$FFFC,(ACTOR_FLAGS,a2)

; Resolve X first against room bounds.
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
        sub.l   d0,d5
        add.l   d0,d6

        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        beq.w   .store_x
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a3
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.s   .x_rect_loop
        move.w  (B01_DEVICE+OBJECT_STATE).l,d0
        andi.w  #B01_DEVICE_STATE_DOOR_OPEN,d0
        beq.s   .x_rect_loop
        adda.w  #STATIC_RECT_BYTES,a3
        subq.w  #1,d7
        bmi.w   .store_x
.x_rect_loop:
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

        move.l  d3,d1
        move.l  d3,d2
        moveq   #0,d0
        move.w  (ACTOR_HALF_WIDTH,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d1
        add.l   d0,d2
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d2
        ble.s   .x_next_rect
        moveq   #0,d2
        move.w  (STATIC_RECT_WIDTH,a3),d2
        lsl.l   #8,d2
        add.l   d0,d2
        cmp.l   d2,d1
        bge.s   .x_next_rect

        tst.l   d4
        bmi.s   .x_moving_left
        moveq   #0,d0
        move.w  (STATIC_RECT_X,a3),d0
        ext.l   d0
        moveq   #0,d1
        move.w  (ACTOR_HALF_WIDTH,a2),d1
        sub.l   d1,d0
        lsl.l   #8,d0
        move.l  d0,d3
        bra.s   .x_collided
.x_moving_left:
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
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        beq.s   .publish_x
        move.l  d3,d0
        move.l  d4,d1
        movea.l a2,a0
        bsr.w   GameplayCampaignResolveDoorX
        move.l  d0,d3
.publish_x:
        move.l  d3,(ACTOR_X,a2)

; Resolve Y second using corrected X.
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
        sub.l   d0,d5
        add.l   d0,d6

        moveq   #0,d7
        move.w  (ENGINE_RECT_COUNT).l,d7
        beq.w   .store_y
        subq.w  #1,d7
        lea     (CANONICAL_RECTS).l,a3
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.s   .y_rect_loop
        move.w  (B01_DEVICE+OBJECT_STATE).l,d0
        andi.w  #B01_DEVICE_STATE_DOOR_OPEN,d0
        beq.s   .y_rect_loop
        adda.w  #STATIC_RECT_BYTES,a3
        subq.w  #1,d7
        bmi.w   .store_y
.y_rect_loop:
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

        move.l  d3,d1
        move.l  d3,d2
        moveq   #0,d0
        move.w  (ACTOR_HALF_HEIGHT,a2),d0
        lsl.l   #8,d0
        sub.l   d0,d1
        add.l   d0,d2
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        lsl.l   #8,d0
        cmp.l   d0,d2
        ble.s   .y_next_rect
        moveq   #0,d2
        move.w  (STATIC_RECT_HEIGHT,a3),d2
        lsl.l   #8,d2
        add.l   d0,d2
        cmp.l   d2,d1
        bge.s   .y_next_rect

        tst.l   d4
        bmi.s   .y_moving_up
        moveq   #0,d0
        move.w  (STATIC_RECT_Y,a3),d0
        ext.l   d0
        moveq   #0,d1
        move.w  (ACTOR_HALF_HEIGHT,a2),d1
        sub.l   d1,d0
        lsl.l   #8,d0
        move.l  d0,d3
        bra.s   .y_collided
.y_moving_up:
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
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        beq.s   .publish_y
        move.l  d3,d0
        move.l  d4,d1
        movea.l a2,a0
        bsr.w   GameplayCampaignResolveDoorY
        move.l  d0,d3
.publish_y:
        move.l  d3,(ACTOR_Y,a2)

        move.w  (ACTOR_FLAGS,a2),d0
        andi.w  #ACTOR_FLAG_COLLISION_X|ACTOR_FLAG_COLLISION_Y,d0
        beq.s   .done
        ori.w   #ENGINE_FLAG_COLLISION,(ENGINE_FLAGS).l
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts
