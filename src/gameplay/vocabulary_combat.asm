; Milestone 5 unwired projectile damage, deterministic enemy targeting,
; pursuer/guard motion, shields, hazards, and beam contact.

; VocabularyDamageActor
; In: a0=actor, d0.w=damage. Shield/phase/invulnerability reject the hit.
; Out: d0.w=1 applied, zero ignored. Clobbers d0-d2/a0-a1.
VocabularyDamageActor:
        movem.l d3-d5/a2,-(a7)
        movea.l a0,a2
        move.w  d0,d3
        bsr.w   VocabularyGetActorExtension
        cmpi.w  #$FFFF,d0
        beq.s   .ignored
        move.w  d0,d5
        move.w  (VOCAB_ACTOR_FLAGS,a1),d4
        andi.w  #VOCAB_ACTOR_FLAG_INVULNERABLE|VOCAB_ACTOR_FLAG_PHASED|VOCAB_ACTOR_FLAG_SHIELDED,d4
        bne.s   .ignored
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .ignored
        tst.w   d3
        bne.s   .damage_ready
        moveq   #1,d3
.damage_ready:
        move.w  (ACTOR_HEALTH,a2),d4
        cmp.w   d3,d4
        bhi.s   .survived
        clr.w   (ACTOR_HEALTH,a2)
        clr.b   (ACTOR_ACTIVE,a2)
        clr.l   (ACTOR_VX,a2)
        clr.l   (ACTOR_VY,a2)
        tst.b   (ENGINE_ACTIVE_ACTOR_COUNT).l
        beq.s   .active_count_corrupt
        subq.b  #1,(ENGINE_ACTIVE_ACTOR_COUNT).l
        bra.s   .active_count_ready
.active_count_corrupt:
        ori.w   #VOCAB_OVERFLOW_UPDATE,(VOCAB_OVERFLOW_FLAGS).l
        ori.w   #VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,(VOCAB_CAMPAIGN_FLAGS).l
.active_count_ready:
        move.l  a1,-(a7)
        movea.l a2,a0
        bsr.w   VocabularyDropActorCarryable
        movea.l (a7)+,a1
        cmpi.w  #GAMEPLAY_CURRENT_ACTOR_SLOT,d5
        bne.s   .mark_hurt
        ori.w   #VOCAB_FLAG_CURRENT_DESTROYED,(VOCAB_FLAGS).l
        bra.s   .mark_hurt
.survived:
        sub.w   d3,(ACTOR_HEALTH,a2)
.mark_hurt:
        ori.w   #VOCAB_ACTOR_FLAG_HURT|VOCAB_ACTOR_FLAG_INVULNERABLE,(VOCAB_ACTOR_FLAGS,a1)
        move.b  #VOCAB_DAMAGE_INVULN_TICKS,(VOCAB_ACTOR_INVULN_TIMER,a1)
        moveq   #1,d0
        bra.s   .done
.ignored:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d5/a2
        rts

; VocabularyDamageEnemy
; In: a0=enemy, d0.w=damage. Enemy health is OBJECT_STATE high byte.
; Out: d0.w=1 applied, zero ignored. Clobbers d0-d2/a0-a1.
VocabularyDamageEnemy:
        movem.l d3-d5/a2,-(a7)
        movea.l a0,a2
        move.w  d0,d3
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .ignored
        move.w  (OBJECT_STATE,a2),d4
        move.w  d4,d0
        andi.w  #VOCAB_ENEMY_STATE_DISABLED,d0
        bne.s   .ignored
        move.w  d4,d5
        lsr.w   #VOCAB_ENEMY_HEALTH_SHIFT,d5
        bne.s   .health_ready
        moveq   #1,d5
.health_ready:
        tst.w   d3
        bne.s   .damage_ready
        moveq   #1,d3
.damage_ready:
        cmp.w   d3,d5
        bhi.s   .survived
        movea.l a2,a0
        moveq   #POOL_TYPE_ENEMY,d2
        bsr.w   EnginePoolFreeSlot
        moveq   #1,d0
        bra.s   .done
.survived:
        sub.w   d3,d5
        lsl.w   #VOCAB_ENEMY_HEALTH_SHIFT,d5
        andi.w  #$00FF,d4
        or.w    d5,d4
        move.w  d4,(OBJECT_STATE,a2)
        moveq   #1,d0
        bra.s   .done
.ignored:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d5/a2
        rts

; VocabularyDamageSecurityNode
; In: a0=device, d0.w=damage. Breakable-node health is OBJECT_STATE high byte;
; ACTIVE is the explicit exposed/damageable gate and DISABLED is terminal.
; Out: d0.w=1 applied, zero ignored. Clobbers d0-d2/a0-a1.
VocabularyDamageSecurityNode:
        movem.l d3-d5/a2,-(a7)
        movea.l a0,a2
        move.w  d0,d3
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .ignored
        move.w  (OBJECT_PARAM,a2),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0
        bne.s   .ignored
        move.w  (OBJECT_STATE,a2),d4
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .ignored
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.s   .ignored
        move.w  d4,d5
        lsr.w   #VOCAB_ENEMY_HEALTH_SHIFT,d5
        bne.s   .health_ready
        moveq   #1,d5
.health_ready:
        tst.w   d3
        bne.s   .damage_ready
        moveq   #1,d3
.damage_ready:
        cmp.w   d3,d5
        bhi.s   .survived
        andi.w  #$00FE,d4
        ori.w   #VOCAB_DEVICE_STATE_DISABLED,d4
        move.w  d4,(OBJECT_STATE,a2)
        moveq   #1,d0
        bra.s   .done
.survived:
        sub.w   d3,d5
        lsl.w   #VOCAB_ENEMY_HEALTH_SHIFT,d5
        andi.w  #$00FF,d4
        or.w    d5,d4
        move.w  d4,(OBJECT_STATE,a2)
        moveq   #1,d0
        bra.s   .done
.ignored:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d5/a2
        rts

; VocabularyUpdateProjectilesAndDamage
; Moves and tests at most 32 projectiles. Friendly pulses choose the lowest
; physical enemy hit, then the lowest exposed security-node hit. Hostile
; pulses choose the lowest physical actor hit. This routine owns projectile
; motion/expiry and must replace, not supplement, generic projectile updates.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyUpdateProjectilesAndDamage:
        movem.l d3-d7/a2-a4,-(a7)
        lea     (PROJECTILE_POOL).l,a2
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_PROJECTILE-1).l,d7
        cmpi.w  #PROJECTILE_CAPACITY,d7
        bls.s   .projectile_bound_ready
        moveq   #PROJECTILE_CAPACITY,d7
.projectile_bound_ready:
        tst.w   d7
        beq.w   .count_ready
        subq.w  #1,d7
.projectile:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .next
        addq.w  #1,d6
        move.l  (OBJECT_VX,a2),d0
        add.l   d0,(OBJECT_X,a2)
        move.l  (OBJECT_VY,a2),d0
        add.l   d0,(OBJECT_Y,a2)
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        beq.s   .lifetime
        movea.l a2,a0
        bsr.w   GameplayCampaignProjectileHitsStatic
        tst.w   d0
        beq.s   .lifetime
        movea.l a2,a0
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolFreeSlot
        subq.w  #1,d6
        bra.w   .next
.lifetime:
        tst.w   (OBJECT_TIMER,a2)
        beq.s   .state
        subq.w  #1,(OBJECT_TIMER,a2)
        bne.s   .state
        movea.l a2,a0
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolFreeSlot
        subq.w  #1,d6
        bra.w   .next

.state:
        move.w  (OBJECT_STATE,a2),d5
        move.w  d5,d0
        andi.w  #VOCAB_PROJECTILE_STATE_PLAYER|VOCAB_PROJECTILE_STATE_REFLECTED,d0
        bne.s   .friendly
        andi.w  #VOCAB_PROJECTILE_STATE_ENEMY,d5
        bne.w   .hostile
        bra.w   .next

.friendly:
        lea     (ENEMY_POOL).l,a3
        moveq   #ENEMY_CAPACITY-1,d4
.enemy:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .enemy_next
        move.w  (OBJECT_STATE,a3),d0
        andi.w  #VOCAB_ENEMY_STATE_DISABLED,d0
        bne.s   .enemy_next
        move.l  (OBJECT_X,a3),d0
        sub.l   (OBJECT_X,a2),d0
        bpl.s   .enemy_x_positive
        neg.l   d0
.enemy_x_positive:
        cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bhi.s   .enemy_next
        move.l  (OBJECT_Y,a3),d0
        sub.l   (OBJECT_Y,a2),d0
        bpl.s   .enemy_y_positive
        neg.l   d0
.enemy_y_positive:
        cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bhi.s   .enemy_next
        moveq   #0,d0
        move.b  (OBJECT_PARAM+1,a2),d0
        movea.l a3,a0
        bsr.w   VocabularyDamageEnemy
        tst.w   d0
        beq.s   .enemy_next
        ori.w   #VOCAB_PROJECTILE_STATE_HIT,(OBJECT_STATE,a2)
        movea.l a2,a0
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolFreeSlot
        subq.w  #1,d6
        bra.w   .next
.enemy_next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d4,.enemy

; Enemies have collision priority. If none was hit, scan exposed breakable
; nodes in physical device-slot order and consume only an applied hit.
        lea     (DEVICE_POOL).l,a3
        moveq   #DEVICE_CAPACITY-1,d4
.security_node:
        tst.b   (OBJECT_ACTIVE,a3)
        beq.s   .security_next
        move.w  (OBJECT_PARAM,a3),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d0
        bne.s   .security_next
        move.w  (OBJECT_STATE,a3),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .security_next
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d1
        beq.s   .security_next
        move.l  (OBJECT_X,a3),d0
        sub.l   (OBJECT_X,a2),d0
        bpl.s   .security_x_positive
        neg.l   d0
.security_x_positive:
        cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bhi.s   .security_next
        move.l  (OBJECT_Y,a3),d0
        sub.l   (OBJECT_Y,a2),d0
        bpl.s   .security_y_positive
        neg.l   d0
.security_y_positive:
        cmpi.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bhi.s   .security_next
        moveq   #0,d0
        move.b  (OBJECT_PARAM+1,a2),d0
        movea.l a3,a0
        bsr.w   VocabularyDamageSecurityNode
        tst.w   d0
        beq.s   .security_next
        ori.w   #VOCAB_PROJECTILE_STATE_HIT,(OBJECT_STATE,a2)
        movea.l a2,a0
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolFreeSlot
        subq.w  #1,d6
        bra.w   .next
.security_next:
        adda.w  #OBJECT_BYTES,a3
        dbf     d4,.security_node
        bra.w   .next

.hostile:
        lea     (ENGINE_ACTOR_BASE).l,a3
        moveq   #ACTOR_CAPACITY-1,d4
.actor:
        tst.b   (ACTOR_ACTIVE,a3)
        beq.s   .actor_next
        movea.l a3,a0
        movea.l a2,a1
        move.l  #VOCAB_OBJECT_HALF_EXTENT_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        beq.s   .actor_next
        moveq   #0,d0
        move.b  (OBJECT_PARAM+1,a2),d0
        movea.l a3,a0
        bsr.w   VocabularyDamageActor
        tst.w   d0
        beq.s   .actor_next
        ori.w   #VOCAB_PROJECTILE_STATE_HIT,(OBJECT_STATE,a2)
        movea.l a2,a0
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolFreeSlot
        subq.w  #1,d6
        bra.s   .next
.actor_next:
        adda.w  #ACTOR_BYTES,a3
        dbf     d4,.actor
.next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.projectile
        cmpi.w  #PROJECTILE_CAPACITY,d6
        bls.s   .count_ready
        moveq   #PROJECTILE_CAPACITY,d6
        ori.w   #VOCAB_OVERFLOW_TARGET,(VOCAB_OVERFLOW_FLAGS).l
.count_ready:
        move.b  d6,(VOCAB_ACTIVE_PROJECTILE_COUNT).l
        movem.l (a7)+,d3-d7/a2-a4
        rts

; VocabularyFindEnemyTarget
; In: a0=enemy. Out: d0.w=lowest actor on equal Manhattan distance or $FFFF,
; d1.l=distance. Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyFindEnemyTarget:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        lea     (ENGINE_ACTOR_BASE).l,a3
        moveq   #ACTOR_CAPACITY-1,d7
        moveq   #-1,d6                  ; selected actor
        move.l  #$7FFFFFFF,d5
        moveq   #0,d4
.actor:
        tst.b   (ACTOR_ACTIVE,a3)
        beq.s   .next
        move.l  (ACTOR_X,a3),d3
        sub.l   (OBJECT_X,a2),d3
        bpl.s   .x_positive
        neg.l   d3
.x_positive:
        move.l  (ACTOR_Y,a3),d2
        sub.l   (OBJECT_Y,a2),d2
        bpl.s   .y_positive
        neg.l   d2
.y_positive:
        add.l   d2,d3
        cmp.l   d5,d3
        bhs.s   .next                  ; equal keeps lower physical index
        move.l  d3,d5
        move.w  d4,d6
.next:
        adda.w  #ACTOR_BYTES,a3
        addq.w  #1,d4
        dbf     d7,.actor
        move.w  d6,d0
        move.l  d5,d1
        movem.l (a7)+,d3-d7/a2-a3
        rts

; VocabularySpawnEnemyProjectile
; In: a0=enemy, a1=target actor. Out: lowest projectile slot or $FFFF.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularySpawnEnemyProjectile:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        movea.l a1,a3
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .invalid
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_ENEMY_STATE_DISABLED,d0
        bne.w   .invalid
        lea     (PROJECTILE_POOL).l,a0
        move.w  #PROJECTILE_CAPACITY,d0
        move.w  #OBJECT_BYTES,d1
        move.w  #OVERFLOW_PROJECTILE,d2
        bsr.w   EnginePoolAllocateLowest
        cmpi.w  #$FFFF,d0
        beq.w   .done
        move.w  d0,d7
        move.b  d0,(OBJECT_LOGICAL_ID,a0)
        move.w  #VOCAB_PROJECTILE_STATE_ENEMY,(OBJECT_STATE,a0)
        move.l  (OBJECT_X,a2),d0
        move.l  d0,(OBJECT_X,a0)
        move.l  (OBJECT_Y,a2),d0
        move.l  d0,(OBJECT_Y,a0)

        move.l  (ACTOR_X,a3),d3
        sub.l   (OBJECT_X,a2),d3
        move.l  (ACTOR_Y,a3),d4
        sub.l   (OBJECT_Y,a2),d4
        moveq   #0,d5
        moveq   #0,d6
        tst.l   d3
        beq.s   .vertical
        move.l  #VOCAB_ENEMY_PROJECTILE_SPEED,d5
        tst.l   d3
        bpl.s   .vertical
        neg.l   d5
.vertical:
        tst.l   d4
        beq.s   .velocity_ready
        move.l  #VOCAB_ENEMY_PROJECTILE_SPEED,d6
        tst.l   d4
        bpl.s   .diagonal
        neg.l   d6
.diagonal:
        tst.l   d5
        beq.s   .velocity_ready
        move.l  #VOCAB_ENEMY_PROJECTILE_DIAGONAL_SPEED,d5
        tst.l   d3
        bpl.s   .diagonal_x_ready
        neg.l   d5
.diagonal_x_ready:
        move.l  #VOCAB_ENEMY_PROJECTILE_DIAGONAL_SPEED,d6
        tst.l   d4
        bpl.s   .velocity_ready
        neg.l   d6
.velocity_ready:
        move.l  d5,(OBJECT_VX,a0)
        move.l  d6,(OBJECT_VY,a0)
        move.w  #ENGINE_PROJECTILE_TICKS,(OBJECT_TIMER,a0)
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        addq.w  #1,d0
        lsl.w   #VOCAB_PROJECTILE_OWNER_SHIFT,d0
        ori.w   #1,d0
        move.w  d0,(OBJECT_PARAM,a0)
        moveq   #POOL_TYPE_PROJECTILE,d2
        bsr.w   EnginePoolNoteAllocation
        move.w  d7,d0
        bra.s   .done
.invalid:
        moveq   #-1,d0
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

; VocabularyUpdateEnemies
; Targets actors deterministically, fires stationary turrets/alert guards,
; and moves pursuers/guards once in signed 16.8 space. It owns enemy motion and
; fire timers and must replace, not supplement, generic enemy updates.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyUpdateEnemies:
        movem.l d3-d7/a2-a5,-(a7)
        lea     (VOCAB_ENEMY_TARGET_BASE).l,a0
        moveq   #-1,d0
        moveq   #(VOCAB_ENEMY_TARGET_BYTES/4)-1,d1
.clear_targets:
        move.l  d0,(a0)+
        dbf     d1,.clear_targets

        lea     (ENEMY_POOL).l,a2
        lea     (VOCAB_ENEMY_TARGET_BASE).l,a5
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_ENEMY-1).l,d7
        cmpi.w  #ENEMY_CAPACITY,d7
        bls.s   .enemy_bound_ready
        moveq   #ENEMY_CAPACITY,d7
.enemy_bound_ready:
        tst.w   d7
        beq.w   .count_ready
        subq.w  #1,d7
.enemy:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .next
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_ENEMY_STATE_DISABLED,d0
        bne.w   .next
        addq.w  #1,d6
        movea.l a2,a0
        bsr.w   VocabularyFindEnemyTarget
        cmpi.w  #$FFFF,d0
        beq.w   .next
        move.b  d0,(a5)
        move.w  d0,d5
        mulu.w  #ACTOR_BYTES,d0
        lea     (ENGINE_ACTOR_BASE).l,a3
        adda.l  d0,a3
        move.l  d1,d4

        move.w  (OBJECT_PARAM,a2),d3
        andi.w  #VOCAB_ENEMY_KIND_MASK,d3
        cmpi.w  #VOCAB_ENEMY_TURRET,d3
        beq.s   .fire_control
        cmpi.w  #VOCAB_ENEMY_GUARD,d3
        beq.s   .guard
        cmpi.w  #VOCAB_ENEMY_PURSUER,d3
        beq.s   .pursue
        bra.w   .next
.guard:
        cmpi.l  #VOCAB_GUARD_DETECTION_RANGE_16_8,d4
        bhi.w   .guard_idle
        ori.w   #VOCAB_ENEMY_STATE_ALERT,(OBJECT_STATE,a2)
        bra.s   .pursue_and_fire
.guard_idle:
        andi.w  #$FFFE,(OBJECT_STATE,a2)
        clr.l   (OBJECT_VX,a2)
        clr.l   (OBJECT_VY,a2)
        bra.w   .next
.pursue_and_fire:
        bsr.s   .set_velocity
        bra.s   .fire_control
.pursue:
        bsr.s   .set_velocity
        bra.s   .move_enemy

.fire_control:
        tst.w   (OBJECT_TIMER,a2)
        beq.s   .fire
        subq.w  #1,(OBJECT_TIMER,a2)
        bra.s   .move_enemy
.fire:
        movea.l a2,a0
        movea.l a3,a1
        bsr.w   VocabularySpawnEnemyProjectile
        cmpi.w  #$FFFF,d0
        bne.s   .fire_ready
        ori.w   #VOCAB_OVERFLOW_TARGET,(VOCAB_OVERFLOW_FLAGS).l
.fire_ready:
        move.w  #VOCAB_TURRET_FIRE_TICKS,(OBJECT_TIMER,a2)
        bra.s   .move_enemy

; File-local subroutine uses a2 enemy and a3 selected actor.
.set_velocity:
        moveq   #0,d0
        move.l  (ACTOR_X,a3),d1
        cmp.l   (OBJECT_X,a2),d1
        beq.s   .vx_ready
        move.l  #VOCAB_ENEMY_SPEED,d0
        bhi.s   .vx_ready
        neg.l   d0
.vx_ready:
        move.l  d0,(OBJECT_VX,a2)
        moveq   #0,d0
        move.l  (ACTOR_Y,a3),d1
        cmp.l   (OBJECT_Y,a2),d1
        beq.s   .vy_ready
        move.l  #VOCAB_ENEMY_SPEED,d0
        bhi.s   .vy_ready
        neg.l   d0
.vy_ready:
        move.l  d0,(OBJECT_VY,a2)
        rts
.move_enemy:
        movea.l a2,a0
        bsr.w   GameplayCampaignMoveObjectAxisSeparated
.next:
        adda.w  #OBJECT_BYTES,a2
        addq.l  #1,a5
        dbf     d7,.enemy
        cmpi.w  #ENEMY_CAPACITY,d6
        bls.s   .count_ready
        moveq   #ENEMY_CAPACITY,d6
        ori.w   #VOCAB_OVERFLOW_TARGET,(VOCAB_OVERFLOW_FLAGS).l
.count_ready:
        move.b  d6,(VOCAB_ACTIVE_ENEMY_COUNT).l
        movem.l (a7)+,d3-d7/a2-a5
        rts

; VocabularyActorOnBeam
; In: a0=actor, a1=beam record. Out: d0.w=1 on the finite segment.
; Contact uses an inclusive four-pixel perpendicular tolerance and rejects
; points behind the ray origin, beyond its length, or on an invalid direction.
; Clobbers d0-d2.
VocabularyActorOnBeam:
        move.l  (ACTOR_X,a0),d0
        asr.l   #8,d0
        move.l  (ACTOR_Y,a0),d1
        asr.l   #8,d1
        moveq   #0,d2
        move.b  (VOCAB_BEAM_DIRECTION,a1),d2
        cmpi.w  #VOCAB_BEAM_DIRECTION_RIGHT,d2
        beq.s   .horizontal_positive
        cmpi.w  #VOCAB_BEAM_DIRECTION_LEFT,d2
        beq.s   .horizontal_negative
        cmpi.w  #VOCAB_BEAM_DIRECTION_DOWN,d2
        beq.s   .vertical
        cmpi.w  #VOCAB_BEAM_DIRECTION_UP,d2
        bne.w   .outside
.vertical:
        move.w  (VOCAB_BEAM_X,a1),d2
        sub.w   d0,d2
        bpl.s   .vertical_cross_positive
        neg.w   d2
.vertical_cross_positive:
; BHI makes exactly four pixels inclusive.
        cmpi.w  #4,d2
        bhi.s   .outside
        moveq   #0,d2
        move.b  (VOCAB_BEAM_LENGTH,a1),d2
        cmpi.b  #VOCAB_BEAM_DIRECTION_DOWN,(VOCAB_BEAM_DIRECTION,a1)
        beq.s   .vertical_positive
        move.w  (VOCAB_BEAM_Y,a1),d0
        sub.w   d1,d0
        bmi.s   .outside
        cmp.w   d2,d0
        bhi.s   .outside
        bra.s   .inside
.vertical_positive:
        sub.w   (VOCAB_BEAM_Y,a1),d1
        bmi.s   .outside
        cmp.w   d2,d1
        bhi.s   .outside
        bra.s   .inside
.horizontal_positive:
        move.w  (VOCAB_BEAM_Y,a1),d2
        sub.w   d1,d2
        bpl.s   .horizontal_cross_positive
        neg.w   d2
.horizontal_cross_positive:
        cmpi.w  #4,d2
        bhi.s   .outside
        sub.w   (VOCAB_BEAM_X,a1),d0
        bmi.s   .outside
        moveq   #0,d2
        move.b  (VOCAB_BEAM_LENGTH,a1),d2
        cmp.w   d2,d0
        bhi.s   .outside
        bra.s   .inside
.horizontal_negative:
        move.w  (VOCAB_BEAM_Y,a1),d2
        sub.w   d1,d2
        bpl.s   .horizontal_negative_cross
        neg.w   d2
.horizontal_negative_cross:
        cmpi.w  #4,d2
        bhi.s   .outside
        move.w  (VOCAB_BEAM_X,a1),d1
        sub.w   d0,d1
        bmi.s   .outside
        moveq   #0,d2
        move.b  (VOCAB_BEAM_LENGTH,a1),d2
        cmp.w   d2,d1
        bhi.s   .outside
.inside:
        moveq   #1,d0
        rts
.outside:
        moveq   #0,d0
        rts

; VocabularyUpdateActorsAndHazards
; Applies shield emitters before damage fields and beams. Actor slot order and
; hazard/beam cache order are deterministic; damage invulnerability prevents
; same-frame multi-hit ambiguity.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
VocabularyUpdateActorsAndHazards:
        movem.l d3-d7/a2-a5,-(a7)
        lea     (ENGINE_ACTOR_BASE).l,a2
        lea     (VOCAB_ACTOR_BASE).l,a3
        moveq   #ACTOR_CAPACITY-1,d7
.actor:
        tst.b   (ACTOR_ACTIVE,a2)
        beq.w   .next_actor
        andi.w  #$FFEF,(VOCAB_ACTOR_FLAGS,a3)
        clr.b   (VOCAB_ACTOR_SHIELD_TIMER,a3)

; Active shield emitters establish protection before any damage scan.
        lea     (DEVICE_POOL).l,a4
        moveq   #0,d6
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d6
        cmpi.w  #DEVICE_CAPACITY,d6
        bls.s   .shield_bound_ready
        moveq   #DEVICE_CAPACITY,d6
.shield_bound_ready:
        tst.w   d6
        beq.w   .hazards
        subq.w  #1,d6
.shield_device:
        tst.b   (OBJECT_ACTIVE,a4)
        beq.s   .shield_next
        move.w  (OBJECT_STATE,a4),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .shield_next
        move.w  (OBJECT_PARAM,a4),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_SHIELD_EMITTER,d0
        bne.s   .shield_next
        move.w  (OBJECT_STATE,a4),d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.s   .shield_next
        movea.l a2,a0
        movea.l a4,a1
        move.l  #VOCAB_INTERACTION_RANGE_16_8,d0
        bsr.w   VocabularyWithinRange
        tst.w   d0
        beq.s   .shield_next
        ori.w   #VOCAB_ACTOR_FLAG_SHIELDED,(VOCAB_ACTOR_FLAGS,a3)
        move.b  #2,(VOCAB_ACTOR_SHIELD_TIMER,a3)
        bra.s   .hazards
.shield_next:
        adda.w  #OBJECT_BYTES,a4
        dbf     d6,.shield_device

.hazards:
        lea     (HAZARD_POOL).l,a4
        moveq   #0,d6
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_HAZARD-1).l,d6
        cmpi.w  #HAZARD_CAPACITY,d6
        bls.s   .hazard_bound_ready
        moveq   #HAZARD_CAPACITY,d6
.hazard_bound_ready:
        tst.w   d6
        beq.w   .beam_setup
        subq.w  #1,d6
.hazard:
        tst.b   (OBJECT_ACTIVE,a4)
        beq.w   .hazard_next
; Campaign hazard states share the low-byte DISABLED bit with devices.
        move.w  (OBJECT_STATE,a4),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .hazard_next
; Safe and warning states are explicitly non-operative. Closing/armed fields
; publish ACTIVE before collision, shield, conveyor, or damage effects apply.
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d1
        beq.w   .hazard_next
        move.w  (OBJECT_PARAM,a4),d0
        move.w  d0,d5
        andi.w  #VOCAB_HAZARD_KIND_MASK,d5
        movea.l a2,a0
        movea.l a4,a1
        bsr.w   GameplayCampaignActorInHazard
        tst.w   d0
        beq.s   .hazard_next
        cmpi.w  #VOCAB_HAZARD_CONVEYOR_FIELD,d5
        beq.s   .hazard_conveyor
        cmpi.w  #VOCAB_HAZARD_SHIELD_FIELD,d5
        bne.s   .damage_hazard
        ori.w   #VOCAB_ACTOR_FLAG_SHIELDED,(VOCAB_ACTOR_FLAGS,a3)
        move.b  #2,(VOCAB_ACTOR_SHIELD_TIMER,a3)
        bra.s   .hazard_next
.hazard_conveyor:
        move.w  (OBJECT_STATE,a4),d0
        andi.w  #VOCAB_DEVICE_STATE_DIRECTION_MASK,d0
        lsr.w   #VOCAB_DEVICE_STATE_DIRECTION_SHIFT,d0
        cmpi.w  #VOCAB_BEAM_DIRECTION_RIGHT,d0
        beq.s   .hazard_conveyor_right
        cmpi.w  #VOCAB_BEAM_DIRECTION_DOWN,d0
        beq.s   .hazard_conveyor_down
        cmpi.w  #VOCAB_BEAM_DIRECTION_LEFT,d0
        beq.s   .hazard_conveyor_left
        moveq   #0,d0
        move.l  #-VOCAB_CONVEYOR_SPEED,d1
        bra.s   .hazard_conveyor_move
.hazard_conveyor_right:
        move.l  #VOCAB_CONVEYOR_SPEED,d0
        moveq   #0,d1
        bra.s   .hazard_conveyor_move
.hazard_conveyor_down:
        moveq   #0,d0
        move.l  #VOCAB_CONVEYOR_SPEED,d1
        bra.s   .hazard_conveyor_move
.hazard_conveyor_left:
        move.l  #-VOCAB_CONVEYOR_SPEED,d0
        moveq   #0,d1
.hazard_conveyor_move:
        movea.l a2,a0
        bsr.w   GameplayCampaignMoveActorByDelta
        bra.s   .hazard_next
.damage_hazard:
        cmpi.w  #VOCAB_HAZARD_DAMAGE_FIELD,d5
        bne.s   .hazard_next
        moveq   #1,d0
        movea.l a2,a0
        bsr.w   VocabularyDamageActor
.hazard_next:
        adda.w  #OBJECT_BYTES,a4
        dbf     d6,.hazard

.beam_setup:
        moveq   #0,d6
        move.b  (VOCAB_BEAM_COUNT).l,d6
        cmpi.w  #VOCAB_BEAM_LIMIT,d6
        bls.s   .beam_count_ready
        moveq   #VOCAB_BEAM_LIMIT,d6
        ori.w   #VOCAB_OVERFLOW_BEAM,(VOCAB_OVERFLOW_FLAGS).l
.beam_count_ready:
        tst.w   d6
        beq.s   .next_actor
        subq.w  #1,d6
        lea     (VOCAB_BEAM_BASE).l,a5
.beam:
        movea.l a2,a0
        movea.l a5,a1
        bsr.w   VocabularyActorOnBeam
        tst.w   d0
        beq.s   .beam_next
        moveq   #1,d0
        movea.l a2,a0
        bsr.w   VocabularyDamageActor
.beam_next:
        adda.w  #VOCAB_BEAM_BYTES,a5
        dbf     d6,.beam
.next_actor:
        adda.w  #ACTOR_BYTES,a2
        adda.w  #VOCAB_ACTOR_BYTES,a3
        dbf     d7,.actor
        movem.l (a7)+,d3-d7/a2-a5
        rts
