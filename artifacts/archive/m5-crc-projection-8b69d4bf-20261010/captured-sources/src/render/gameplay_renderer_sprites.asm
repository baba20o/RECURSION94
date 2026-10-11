; Dual-mode SAT builder. Active campaigns project live pools in fixed physical
; order; non-campaign fixtures retain the stable B01 priority path. Every
; admission checks total SAT capacity, per-scanline sprite count, and
; per-scanline sprite-pixel load before touching the shadow table. Scanline
; updates are transactional: a late rejection rolls earlier lines back.

; RendererBuildSat
; Rebuilds the complete 80-entry shadow table and scanline ledgers.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererBuildSat:
        movem.l d3-d7/a2-a5,-(a7)

        lea     (SAT_SHADOW).l,a0
        moveq   #0,d0
        moveq   #0,d1
        moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        movea.l d0,a1
        moveq   #(SAT_SHADOW_BYTES/32)-1,d7
.clear_sat:
        movem.l d0-d6/a1,(a0)
        lea     32(a0),a0
        dbf     d7,.clear_sat
        clr.w   (RENDERER_SPRITE_COUNT).l
        clr.w   (RENDERER_SCANLINE_MAX).l
        clr.w   (RENDERER_SCANLINE_PIXEL_MAX).l

; Both scanline ledgers are contiguous and total 672 bytes, so one bounded
; 32-byte burst loop clears the byte counts and word pixel totals together.
        lea     (RENDERER_SCANLINE_COUNT_BASE).l,a0
        moveq   #((RENDERER_SCANLINE_COUNT_BYTES+RENDERER_SCANLINE_PIXEL_BYTES)/32)-1,d7
.clear_ledgers:
        movem.l d0-d6/a1,(a0)
        lea     32(a0),a0
        dbf     d7,.clear_ledgers

; Campaign encounters project the live pools below. The original B01 ordering
; remains the deterministic fallback for standalone and non-campaign fixtures.
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.w   .campaign

; Current incarnation always has first SAT priority and an explicit C badge.
; Presentation offsets below are bounded and read-only; simulation positions
; remain untouched.
.legacy_current:
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        tst.b   (ACTOR_ACTIVE,a0)
        beq.s   .hazard
        move.w  #DIAGNOSTIC_LETTER_A_TILE+2,d0
        move.w  #RENDERER_CURRENT_ATTRIBUTE,d1
        move.w  #RENDERER_TILE_CURRENT_0,d2
        moveq   #0,d3
        bsr.w   RendererAppendActorPair

; B01 has one authored hazard. It precedes objectives and echoes so collision
; danger cannot disappear when actors overlap.
.hazard:
        lea     (HAZARD_POOL).l,a2
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .switch
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_HAZARD_ATTRIBUTE+RENDERER_TILE_HAZARD,d2
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bsr.w   RendererTryAppendSprite

; Pressure switch and east terminal are objective-priority sprites.
.switch:
        move.w  #B01_SWITCH_CENTER_X-8,d0
        move.w  #B01_SWITCH_CENTER_Y-4,d1
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_SWITCH_IDLE,d2
        move.w  (GAMEPLAY_FLAGS).l,d4
        andi.w  #GAMEPLAY_FLAG_SWITCH_HELD,d4
        beq.s   .switch_ready
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_SWITCH_HELD,d2
.switch_ready:
        move.w  #RENDERER_SPRITE_SIZE_2X1,d3
        bsr.w   RendererTryAppendSprite

        move.w  #B01_TERMINAL_CENTER_X-8,d0
        move.w  #B01_TERMINAL_CENTER_Y-8,d1
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_TERMINAL,d2
        move.w  (GAMEPLAY_FLAGS).l,d4
        andi.w  #GAMEPLAY_FLAG_PROGRAM_STABLE,d4
        beq.s   .terminal_ready
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_TERMINAL_STABLE,d2
.terminal_ready:
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bsr.w   RendererTryAppendSprite

; A closed door only pulses when the current actor is spatially rejected at
; its canonical rectangle. The predicate is read-only and cannot affect
; simulation collision or door state. These entries precede the opaque door
; leaf in SAT order so the contact flash remains visible on its face.
        bsr.w   RendererDoorRejectionActive
        tst.w   d0
        beq.s   .door
        move.l  (GAMEPLAY_CURRENT_ACTOR+ACTOR_Y).l,d5
        asr.l   #8,d5
        subq.w  #4,d5
        moveq   #0,d4
        move.w  (ENGINE_TICK).l,d4
        lsr.w   #2,d4
        andi.w  #$0001,d4
        addi.w  #RENDERER_TILE_DOOR_REJECT_0,d4
        ori.w   #RENDERER_HAZARD_ATTRIBUTE,d4

        move.w  #112,d0
        move.w  d5,d1
        move.w  d4,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        move.w  #120,d0
        move.w  d5,d1
        move.w  d4,d2
        ori.w   #RENDERER_ATTR_HFLIP,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite

; The first canonical rectangle is represented by four linked 16x32 blocks.
; When gameplay projects the door open, only the small retracted latch remains.
.door:
        move.w  (B01_DEVICE+OBJECT_STATE).l,d4
        andi.w  #B01_DEVICE_STATE_DOOR_OPEN,d4
        bne.s   .door_open
        moveq   #3,d4
        move.w  #48,d5
.door_block:
        move.w  #112,d0
        move.w  d5,d1
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_DOOR,d2
        move.w  #RENDERER_SPRITE_SIZE_2X4,d3
        bsr.w   RendererTryAppendSprite
        addi.w  #32,d5
        dbf     d4,.door_block
        bra.s   .echoes
.door_open:
        move.w  #116,d0
        move.w  #108,d1
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_DOOR_OPEN,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite

; Physical actor slots 0..echo_count-1 are already chronological. The visible
; number is the chronological ordinal, independent of rotated replay-bank ID.
.echoes:
        moveq   #0,d7
        move.b  (TIMELINE_ECHO_COUNT).l,d7
        cmpi.w  #GAMEPLAY_ECHO_LIMIT,d7
        bls.s   .echo_count_ready
        moveq   #GAMEPLAY_ECHO_LIMIT,d7
        ori.w   #RENDERER_FLAG_INVALID_STATE,(RENDERER_FLAGS).l
.echo_count_ready:
        tst.w   d7
        beq.s   .enemy
        subq.w  #1,d7
        moveq   #0,d6
        lea     (ENGINE_ACTOR_BASE).l,a2
.echo:
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .echo_next
        move.w  d6,d0
        addq.w  #1,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        move.w  #RENDERER_ECHO_ATTRIBUTE,d1
        move.w  #RENDERER_TILE_ECHO_0,d2
        move.w  d6,d3
        movea.l a2,a0
        bsr.w   RendererAppendActorPair
.echo_next:
        adda.w  #ACTOR_BYTES,a2
        addq.w  #1,d6
        dbf     d7,.echo

; B01's one sentry and one carryable use fixed pool slot priority.
.enemy:
        lea     (ENEMY_POOL).l,a2
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .carryable
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_HAZARD_ATTRIBUTE+RENDERER_TILE_ENEMY,d2
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bsr.w   RendererTryAppendSprite
.carryable:
        lea     (CARRYABLE_POOL).l,a2
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .projectiles
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_CARRYABLE,d2
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bsr.w   RendererTryAppendSprite

; Optional projectile rendering is capped independently of the 32-slot
; simulation pool. Pool order and scanline rejection make degradation stable.
.projectiles:
        lea     (PROJECTILE_POOL).l,a2
        move.w  #PROJECTILE_CAPACITY-1,d7
        moveq   #0,d6
.projectile:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .projectile_next
        cmpi.w  #RENDERER_PROJECTILE_LIMIT,d6
        bhs.s   .projectiles_done
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #4,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #4,d1
        move.w  #RENDERER_HAZARD_ATTRIBUTE+RENDERER_TILE_PROJECTILE,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        tst.w   d0
        beq.s   .projectile_next
        addq.w  #1,d6
.projectile_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.projectile
.projectiles_done:
        bra.w   .publish

; Active campaign encounters use the projected pool records exclusively.
; Actors consume fixed priority first so all seven replay participants remain
; visible before objective, threat, carryable, hazard, and projectile pools.
.campaign:
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        tst.b   (ACTOR_ACTIVE,a0)
        beq.s   .campaign_echoes
        move.w  #DIAGNOSTIC_LETTER_A_TILE+2,d0
        move.w  #RENDERER_CURRENT_ATTRIBUTE,d1
        move.w  #RENDERER_TILE_CURRENT_0,d2
        moveq   #0,d3
        bsr.w   RendererAppendActorPair

.campaign_echoes:
        lea     (ENGINE_ACTOR_BASE).l,a2
        moveq   #0,d6
        moveq   #GAMEPLAY_ECHO_LIMIT-1,d7
.campaign_echo:
        tst.b   (ACTOR_ACTIVE,a2)
        beq.s   .campaign_echo_next
        move.w  d6,d0
        addq.w  #1,d0
        addi.w  #DIAGNOSTIC_DIGIT_ZERO_TILE,d0
        move.w  #RENDERER_ECHO_ATTRIBUTE,d1
        move.w  #RENDERER_TILE_ECHO_0,d2
        move.w  d6,d3
        movea.l a2,a0
        bsr.w   RendererAppendActorPair
.campaign_echo_next:
        adda.w  #ACTOR_BYTES,a2
        addq.w  #1,d6
        dbf     d7,.campaign_echo

; Live vocabulary devices consume the campaign cap in physical order. Core
; shapes cover switches, doors, terminals, and nodes; sockets/conveyors reuse
; switch art while emitters and reflectors reuse readable data-object art.
        lea     (DEVICE_POOL).l,a2
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d7
        cmpi.w  #DEVICE_CAPACITY,d7
        bls.s   .campaign_device_bound_ready
        moveq   #DEVICE_CAPACITY,d7
.campaign_device_bound_ready:
        tst.w   d7
        beq.w   .campaign_devices_done
        subq.w  #1,d7
.campaign_device:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.w   .campaign_device_next
        cmpi.w  #RENDERER_CAMPAIGN_DEVICE_LIMIT,d6
        bhs.w   .campaign_devices_done
        move.w  (OBJECT_PARAM,a2),d5
        andi.w  #VOCAB_PARAM_KIND_MASK,d5
        move.w  (OBJECT_STATE,a2),d4
        cmpi.w  #VOCAB_DEVICE_PRESSURE_SWITCH,d5
        beq.s   .campaign_switch
        cmpi.w  #VOCAB_DEVICE_TIMED_SWITCH,d5
        beq.s   .campaign_switch
        cmpi.w  #VOCAB_DEVICE_DOOR,d5
        beq.w   .campaign_door
        cmpi.w  #VOCAB_DEVICE_SECURITY_NODE,d5
        beq.w   .campaign_security_node
        cmpi.w  #VOCAB_DEVICE_HACK_TERMINAL,d5
        beq.w   .campaign_terminal
        cmpi.w  #VOCAB_DEVICE_REPAIR_TERMINAL,d5
        beq.w   .campaign_terminal
        cmpi.w  #VOCAB_DEVICE_SOCKET,d5
        beq.w   .campaign_switch
        cmpi.w  #VOCAB_DEVICE_CONVEYOR,d5
        beq.w   .campaign_switch
        cmpi.w  #VOCAB_DEVICE_BEAM_EMITTER,d5
        beq.w   .campaign_terminal
        cmpi.w  #VOCAB_DEVICE_SHIELD_EMITTER,d5
        beq.w   .campaign_terminal
        cmpi.w  #VOCAB_DEVICE_REFLECTOR,d5
        beq.w   .campaign_reflector
        bra.w   .campaign_device_next

.campaign_switch:
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.w   .campaign_device_next
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_SWITCH_IDLE,d2
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.s   .campaign_switch_position
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_SWITCH_HELD,d2
.campaign_switch_position:
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #4,d1
        move.w  #RENDERER_SPRITE_SIZE_2X1,d3
        bra.w   .campaign_append_device

.campaign_door:
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_OPEN|VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .campaign_door_open
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_DOOR,d2
        move.w  #RENDERER_SPRITE_SIZE_2X4,d3
        bra.s   .campaign_append_device
.campaign_door_open:
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        addq.w  #4,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        addi.w  #12,d1
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_DOOR_OPEN,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bra.s   .campaign_append_device

.campaign_security_node:
; Live armed nodes are solid cobalt data objects. Disabled/destroyed nodes
; fail closed to the hollow objective outline even if stale ACTIVE bits remain.
        move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_TERMINAL,d2
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .campaign_terminal_position
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        beq.s   .campaign_terminal_position
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_TERMINAL_STABLE,d2
        bra.s   .campaign_terminal_position

.campaign_terminal:
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_TERMINAL,d2
        move.w  d4,d0
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE|VOCAB_DEVICE_STATE_HACKED|VOCAB_DEVICE_STATE_DISABLED,d0
        beq.s   .campaign_terminal_position
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_TERMINAL_STABLE,d2
.campaign_terminal_position:
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bra.s   .campaign_append_device

.campaign_reflector:
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_CARRYABLE,d2
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3

.campaign_append_device:
        bsr.w   RendererTryAppendSprite
        tst.w   d0
        beq.s   .campaign_device_next
        addq.w  #1,d6
.campaign_device_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.campaign_device
.campaign_devices_done:

        lea     (ENEMY_POOL).l,a2
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_ENEMY-1).l,d7
        cmpi.w  #ENEMY_CAPACITY,d7
        bls.s   .campaign_enemy_bound_ready
        moveq   #ENEMY_CAPACITY,d7
.campaign_enemy_bound_ready:
        tst.w   d7
        beq.w   .campaign_enemies_done
        subq.w  #1,d7
.campaign_enemy:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .campaign_enemy_next
        cmpi.w  #RENDERER_CAMPAIGN_ENEMY_LIMIT,d6
        bhs.s   .campaign_enemies_done
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_ENEMY_STATE_DISABLED,d0
        bne.s   .campaign_enemy_next
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_HAZARD_ATTRIBUTE+RENDERER_TILE_ENEMY,d2
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bsr.w   RendererTryAppendSprite
        tst.w   d0
        beq.s   .campaign_enemy_next
        addq.w  #1,d6
.campaign_enemy_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.campaign_enemy
.campaign_enemies_done:

        lea     (CARRYABLE_POOL).l,a2
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_CARRYABLE-1).l,d7
        cmpi.w  #CARRYABLE_CAPACITY,d7
        bls.s   .campaign_carryable_bound_ready
        moveq   #CARRYABLE_CAPACITY,d7
.campaign_carryable_bound_ready:
        tst.w   d7
        beq.w   .campaign_carryables_done
        subq.w  #1,d7
.campaign_carryable:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .campaign_carryable_next
        cmpi.w  #RENDERER_CAMPAIGN_CARRYABLE_LIMIT,d6
        bhs.s   .campaign_carryables_done
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #8,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #8,d1
        move.w  #RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_CARRYABLE,d2
        move.w  #RENDERER_SPRITE_SIZE_2X2,d3
        bsr.w   RendererTryAppendSprite
        tst.w   d0
        beq.s   .campaign_carryable_next
        addq.w  #1,d6
.campaign_carryable_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.campaign_carryable
.campaign_carryables_done:

        lea     (HAZARD_POOL).l,a2
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_HAZARD-1).l,d7
        cmpi.w  #HAZARD_CAPACITY,d7
        bls.s   .campaign_hazard_bound_ready
        moveq   #HAZARD_CAPACITY,d7
.campaign_hazard_bound_ready:
        tst.w   d7
        beq.w   .campaign_hazards_done
        subq.w  #1,d7
.campaign_hazard:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .campaign_hazard_next
        cmpi.w  #RENDERER_CAMPAIGN_HAZARD_LIMIT,d6
        bhs.s   .campaign_hazards_done
        move.w  (OBJECT_STATE,a2),d0
        andi.w  #VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .campaign_hazard_next
        movea.l a2,a0
        bsr.w   .append_campaign_hazard_extent
        addq.w  #1,d6
.campaign_hazard_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.campaign_hazard
.campaign_hazards_done:

; Current generated content owns one emitter and at most two cache segments.
; Each live segment is expanded from its origin across the inclusive cached
; length in bounded 32-pixel lattice chunks. Beams precede projectiles so a
; lethal authored ray cannot disappear behind noncritical projectile load.
        moveq   #0,d7
        move.b  (VOCAB_BEAM_COUNT).l,d7
        cmpi.w  #RENDERER_CAMPAIGN_BEAM_SEGMENT_LIMIT,d7
        bls.s   .campaign_beam_count_ready
        moveq   #RENDERER_CAMPAIGN_BEAM_SEGMENT_LIMIT,d7
        ori.w   #RENDERER_FLAG_INVALID_STATE|RENDERER_FLAG_BEAM_TRUNCATED|RENDERER_FLAG_SPRITE_DROPPED,(RENDERER_FLAGS).l
        cmpi.w  #$FFFF,(RENDERER_DROPPED_SPRITES).l
        beq.s   .campaign_beam_count_ready
        addq.w  #1,(RENDERER_DROPPED_SPRITES).l
.campaign_beam_count_ready:
        tst.w   d7
        beq.s   .campaign_beams_done
        subq.w  #1,d7
        lea     (VOCAB_BEAM_BASE).l,a2
        moveq   #0,d6                   ; admitted beam chunk sprites
.campaign_beam:
        move.w  d6,d0
        movea.l a2,a0
        bsr.w   .append_campaign_beam_segment
        move.w  d0,d6
        adda.w  #VOCAB_BEAM_BYTES,a2
        dbf     d7,.campaign_beam
.campaign_beams_done:

        lea     (PROJECTILE_POOL).l,a2
        moveq   #0,d6
        moveq   #0,d7
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_PROJECTILE-1).l,d7
        cmpi.w  #PROJECTILE_CAPACITY,d7
        bls.s   .campaign_projectile_bound_ready
        moveq   #PROJECTILE_CAPACITY,d7
.campaign_projectile_bound_ready:
        tst.w   d7
        beq.w   .campaign_projectiles_done
        subq.w  #1,d7
.campaign_projectile:
        tst.b   (OBJECT_ACTIVE,a2)
        beq.s   .campaign_projectile_next
        cmpi.w  #RENDERER_CAMPAIGN_PROJECTILE_LIMIT,d6
        bhs.s   .campaign_projectiles_done
        move.l  (OBJECT_X,a2),d0
        asr.l   #8,d0
        subq.w  #4,d0
        move.l  (OBJECT_Y,a2),d1
        asr.l   #8,d1
        subq.w  #4,d1
        move.w  #RENDERER_HAZARD_ATTRIBUTE+RENDERER_TILE_PROJECTILE,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        tst.w   d0
        beq.s   .campaign_projectile_next
        addq.w  #1,d6
.campaign_projectile_next:
        adda.w  #OBJECT_BYTES,a2
        dbf     d7,.campaign_projectile
.campaign_projectiles_done:

.publish:
        move.w  (RENDERER_SPRITE_COUNT).l,d0
        move.w  d0,(DEBUG_SPRITE_COUNT).l
        move.w  (RENDERER_SCANLINE_MAX).l,d0
        move.w  d0,(DEBUG_SPRITE_SCANLINE_MAX).l
        movem.l (a7)+,d3-d7/a2-a5
        rts

; .append_campaign_hazard_extent
; In: a0=active hazard object. Semantic rectangles are top-left width/height;
; point hazards retain center +/- radius. The B03 16x64 shutter fills its full
; lethal AABB with two 2x4 sprites; other shapes use four boundary markers.
; Palette 0/2/3 distinguishes safe/warning/active.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
.append_campaign_hazard_extent:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2

; Resolve the direct-index semantic record without linking the renderer-only
; fixture to the controller. Invalid bindings fail to the legacy radius shape.
        moveq   #0,d0
        move.b  (OBJECT_LOGICAL_ID,a2),d0
        moveq   #0,d1
        move.b  (VOCAB_CAMPAIGN_SEMANTIC_COUNT).l,d1
        cmp.w   d1,d0
        bhs.w   .campaign_hazard_radius
        movea.l (VOCAB_CAMPAIGN_SEMANTIC_PTR).l,a0
        move.l  a0,d1
        beq.w   .campaign_hazard_radius
        btst    #0,d1
        bne.w   .campaign_hazard_radius
        move.w  d0,d1
        lsl.w   #4,d1
        adda.w  d1,a0
        cmp.b   (VOCAB_CAMPAIGN_SEMANTIC_OBJECT,a0),d0
        bne.w   .campaign_hazard_radius

        moveq   #0,d3
        move.w  (VOCAB_CAMPAIGN_SEMANTIC_FLAGS,a0),d3
        andi.w  #VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK,d3
        lsr.w   #VOCAB_CAMPAIGN_GEOMETRY_WIDTH_SHIFT,d3
        moveq   #0,d7
        move.w  (VOCAB_CAMPAIGN_SEMANTIC_FLAGS,a0),d7
        andi.w  #VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK,d7
        lsr.w   #8,d7
        lsr.w   #3,d7
        tst.w   d3
        beq.w   .campaign_hazard_radius
        tst.w   d7
        beq.w   .campaign_hazard_radius
        lsl.w   #3,d3                   ; authored width in pixels
        lsl.w   #3,d7                   ; authored height in pixels

; Two 16x32 lattice blocks fill the complete B03 shutter. The tile bank owns
; two pixel-identical four-tile columns, so packing each row into one 2x4 SAT
; entry preserves the old raster while halving scanline-admission work.
        cmpi.w  #16,d3
        bne.s   .campaign_hazard_rectangle_bounds
        cmpi.w  #64,d7
        bne.s   .campaign_hazard_rectangle_bounds
        move.l  (OBJECT_X,a2),d4
        asr.l   #8,d4
        move.w  d4,d5
        addq.w  #8,d5
        move.l  (OBJECT_Y,a2),d6
        asr.l   #8,d6
        move.w  d6,d7
        addi.w  #32,d7
        moveq   #1,d3                   ; continuous 16x64 fill mode
        bra.s   .campaign_hazard_extent_ready

.campaign_hazard_rectangle_bounds:
        move.l  (OBJECT_X,a2),d4
        asr.l   #8,d4
        move.w  d4,d5
        add.w   d3,d5
        subq.w  #4,d4                   ; left boundary marker
        subq.w  #4,d5                   ; right boundary marker
        move.l  (OBJECT_Y,a2),d6
        asr.l   #8,d6
        add.w   d6,d7
        subq.w  #4,d6                   ; top boundary marker
        subq.w  #4,d7                   ; bottom boundary marker
        moveq   #0,d3                   ; four-corner mode
        bra.s   .campaign_hazard_extent_ready

.campaign_hazard_radius:
        moveq   #0,d3
        move.b  (OBJECT_PARAM+1,a2),d3
        bne.s   .campaign_hazard_radius_ready
        moveq   #VOCAB_HAZARD_RADIUS_DEFAULT,d3
.campaign_hazard_radius_ready:
        move.l  (OBJECT_X,a2),d4
        asr.l   #8,d4
        move.w  d4,d5
        sub.w   d3,d4
        subq.w  #4,d4                   ; left marker top-left
        add.w   d3,d5
        subq.w  #4,d5                   ; right marker top-left
        move.l  (OBJECT_Y,a2),d6
        asr.l   #8,d6
        move.w  d6,d7
        sub.w   d3,d6
        subq.w  #4,d6                   ; top marker top-left
        add.w   d3,d7
        subq.w  #4,d7                   ; bottom marker top-left
        moveq   #0,d3                   ; four-corner mode

; State colors make the shutter's nonlethal warning legible before closing.
.campaign_hazard_extent_ready:
        movea.l #RENDERER_OBJECTIVE_ATTRIBUTE,a3
        move.w  (OBJECT_STATE,a2),d0
        move.w  d0,d1
        andi.w  #VOCAB_DEVICE_STATE_ACTIVE,d0
        bne.s   .campaign_hazard_active
        andi.w  #VOCAB_HAZARD_STATE_WARNING,d1
        beq.s   .campaign_hazard_attribute_ready
        movea.l #RENDERER_DATA_ATTRIBUTE,a3
        bra.s   .campaign_hazard_attribute_ready
.campaign_hazard_active:
        movea.l #RENDERER_HAZARD_ATTRIBUTE,a3
.campaign_hazard_attribute_ready:
        tst.w   d3
        bne.s   .campaign_hazard_filled_shutter
        move.w  d4,d0
        move.w  d6,d1
        move.l  a3,d2
        addi.w  #RENDERER_TILE_HAZARD,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        move.w  d5,d0
        move.w  d6,d1
        move.l  a3,d2
        addi.w  #RENDERER_TILE_HAZARD+2,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        move.w  d4,d0
        move.w  d7,d1
        move.l  a3,d2
        addi.w  #RENDERER_TILE_HAZARD+1,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        move.w  d5,d0
        move.w  d7,d1
        move.l  a3,d2
        addi.w  #RENDERER_TILE_HAZARD+3,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        bra.s   .campaign_hazard_done

.campaign_hazard_filled_shutter:
        move.w  d4,d0
        move.w  d6,d1
        move.l  a3,d2
        addi.w  #RENDERER_TILE_HAZARD,d2
        move.w  #RENDERER_SPRITE_SIZE_2X4,d3
        bsr.w   RendererTryAppendSprite
        move.w  d4,d0
        move.w  d7,d1
        move.l  a3,d2
        addi.w  #RENDERER_TILE_HAZARD,d2
        move.w  #RENDERER_SPRITE_SIZE_2X4,d3
        bsr.w   RendererTryAppendSprite
.campaign_hazard_done:
        movem.l (a7)+,d3-d7/a2-a3
        rts

; .append_campaign_beam_segment
; In: a0=beam-cache record, d0.w=beam sprites already admitted.
; Out: d0.w=updated admitted count. The cached length is inclusive, so length
; zero still emits one endpoint pixel. Tail chunks select the smallest 8-pixel
; hardware dimension that contains the remaining live span. At the generated
; ten-sprite room bound, any further visible chunk sets an explicit truncation
; flag; ordinary SAT/scanline rejection remains transactional in the callee.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
.append_campaign_beam_segment:
        movem.l d3-d7/a2-a3,-(a7)
        movea.l a0,a2
        movea.w d0,a3                   ; admitted beam sprite count
        moveq   #0,d4
        move.b  (VOCAB_BEAM_LENGTH,a2),d4
        addq.w  #1,d4                   ; inclusive origin..endpoint span
        moveq   #0,d5
        move.b  (VOCAB_BEAM_DIRECTION,a2),d5
        move.w  (VOCAB_BEAM_X,a2),d6
        move.w  (VOCAB_BEAM_Y,a2),d7

.campaign_beam_chunk:
        tst.w   d4
        beq.w   .campaign_beam_segment_done
        move.l  a3,d0
        cmpi.w  #RENDERER_CAMPAIGN_BEAM_SPRITE_LIMIT,d0
        bhs.w   .campaign_beam_truncated

        move.w  d4,d2
        cmpi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d2
        bls.s   .campaign_beam_span_ready
        moveq   #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d2
.campaign_beam_span_ready:
; Convert 1..32 logical pixels to one through four hardware tiles.
        move.w  d2,d0
        addq.w  #7,d0
        lsr.w   #3,d0
        subq.w  #1,d0
        cmpi.w  #VOCAB_BEAM_DIRECTION_LEFT,d5
        beq.s   .campaign_beam_horizontal_negative
        cmpi.w  #VOCAB_BEAM_DIRECTION_UP,d5
        beq.s   .campaign_beam_vertical_negative
        cmpi.w  #VOCAB_BEAM_DIRECTION_DOWN,d5
        beq.s   .campaign_beam_vertical_positive

; Right-facing horizontal chunk.
        move.w  d0,d3
        lsl.w   #8,d3
        lsl.w   #2,d3
        move.w  d6,d0
        move.w  d7,d1
        subq.w  #4,d1
        bra.s   .campaign_beam_append

.campaign_beam_horizontal_negative:
        move.w  d0,d3
        lsl.w   #8,d3
        lsl.w   #2,d3
        move.w  d3,d0
        lsr.w   #8,d0
        lsr.w   #2,d0
        addq.w  #1,d0
        lsl.w   #3,d0
        subq.w  #1,d0
        neg.w   d0
        add.w   d6,d0
        move.w  d7,d1
        subq.w  #4,d1
        bra.s   .campaign_beam_append

.campaign_beam_vertical_positive:
        move.w  d0,d3
        lsl.w   #8,d3
        move.w  d6,d0
        subq.w  #4,d0
        move.w  d7,d1
        bra.s   .campaign_beam_append

.campaign_beam_vertical_negative:
        move.w  d0,d3
        lsl.w   #8,d3
        move.w  d3,d1
        lsr.w   #8,d1
        addq.w  #1,d1
        lsl.w   #3,d1
        subq.w  #1,d1
        neg.w   d1
        add.w   d7,d1
        move.w  d6,d0
        subq.w  #4,d0

.campaign_beam_append:
        move.w  #RENDERER_HAZARD_ATTRIBUTE+RENDERER_TILE_HAZARD,d2
        bsr.w   RendererTryAppendSprite
        tst.w   d0
        beq.s   .campaign_beam_advance
        move.l  a3,d0
        addq.w  #1,d0
        movea.w d0,a3
.campaign_beam_advance:
; Full chunks advance exactly 32 pixels; a shorter tail consumes the remainder
; and exits before its coordinate is observed again.
        cmpi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d4
        bls.s   .campaign_beam_tail_done
        subi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d4
        cmpi.w  #VOCAB_BEAM_DIRECTION_RIGHT,d5
        beq.s   .campaign_beam_advance_right
        cmpi.w  #VOCAB_BEAM_DIRECTION_DOWN,d5
        beq.s   .campaign_beam_advance_down
        cmpi.w  #VOCAB_BEAM_DIRECTION_LEFT,d5
        beq.s   .campaign_beam_advance_left
        subi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d7
        bra.w   .campaign_beam_chunk
.campaign_beam_advance_right:
        addi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d6
        bra.w   .campaign_beam_chunk
.campaign_beam_advance_down:
        addi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d7
        bra.w   .campaign_beam_chunk
.campaign_beam_advance_left:
        subi.w  #RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS,d6
        bra.w   .campaign_beam_chunk
.campaign_beam_tail_done:
        clr.w   d4
        bra.s   .campaign_beam_segment_done

.campaign_beam_truncated:
        ori.w   #RENDERER_FLAG_BEAM_TRUNCATED|RENDERER_FLAG_SPRITE_DROPPED,(RENDERER_FLAGS).l
        cmpi.w  #$FFFF,(RENDERER_DROPPED_SPRITES).l
        beq.s   .campaign_beam_segment_done
        addq.w  #1,(RENDERER_DROPPED_SPRITES).l
.campaign_beam_segment_done:
        move.l  a3,d0
        movem.l (a7)+,d3-d7/a2-a3
        rts

; RendererDoorRejectionActive
; Out: d0.w=1 only while the closed B01 door is spatially rejecting the
; current actor, or while a closed campaign door is within the same bounded
; feedback radius. d1.l is the matched door center X when active.
; Reads actor/device state only. Clobbers d0-d2/a0-a1.
RendererDoorRejectionActive:
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.w   .campaign

        move.w  (B01_DEVICE+OBJECT_STATE).l,d0
        andi.w  #B01_DEVICE_STATE_DOOR_OPEN,d0
        bne.w   .inactive
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        tst.b   (ACTOR_ACTIVE,a0)
        beq.w   .inactive
        move.l  (ACTOR_X,a0),d0
        cmpi.l  #RENDERER_DOOR_FEEDBACK_MIN_X_16_8,d0
        blt.w   .inactive
        cmpi.l  #RENDERER_DOOR_FEEDBACK_MAX_X_16_8,d0
        bgt.w   .inactive
        move.l  (ACTOR_Y,a0),d0
        cmpi.l  #RENDERER_DOOR_FEEDBACK_MIN_Y_16_8,d0
        blt.w   .inactive
        cmpi.l  #RENDERER_DOOR_FEEDBACK_MAX_Y_16_8,d0
        bgt.w   .inactive
        move.l  #RENDERER_DOOR_MID_X_16_8,d1
        moveq   #1,d0
        rts

.campaign:
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        tst.b   (ACTOR_ACTIVE,a0)
        beq.w   .inactive
        lea     (DEVICE_POOL).l,a1
        moveq   #0,d2
        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_DEVICE-1).l,d2
        cmpi.w  #DEVICE_CAPACITY,d2
        bls.s   .campaign_bound_ready
        moveq   #DEVICE_CAPACITY,d2
.campaign_bound_ready:
        tst.w   d2
        beq.w   .inactive
        subq.w  #1,d2
.campaign_door:
        tst.b   (OBJECT_ACTIVE,a1)
        beq.s   .campaign_next
        move.w  (OBJECT_PARAM,a1),d0
        andi.w  #VOCAB_PARAM_KIND_MASK,d0
        cmpi.w  #VOCAB_DEVICE_DOOR,d0
        bne.s   .campaign_next
        move.w  (OBJECT_STATE,a1),d0
        andi.w  #VOCAB_DEVICE_STATE_OPEN|VOCAB_DEVICE_STATE_DISABLED,d0
        bne.s   .campaign_next

        move.l  (OBJECT_X,a1),d1
        addi.l  #8*256,d1
        move.l  (ACTOR_X,a0),d0
        sub.l   d1,d0
        bpl.s   .campaign_x_ready
        neg.l   d0
.campaign_x_ready:
        cmpi.l  #RENDERER_CAMPAIGN_DOOR_FEEDBACK_RADIUS_16_8,d0
        bgt.s   .campaign_next
        move.l  (ACTOR_Y,a0),d0
        sub.l   (OBJECT_Y,a1),d0
        subi.l  #16*256,d0
        bpl.s   .campaign_y_ready
        neg.l   d0
.campaign_y_ready:
        cmpi.l  #RENDERER_CAMPAIGN_DOOR_FEEDBACK_RADIUS_16_8,d0
        bgt.s   .campaign_next
        moveq   #1,d0
        rts
.campaign_next:
        adda.w  #OBJECT_BYTES,a1
        dbf     d2,.campaign_door
.inactive:
        moveq   #0,d0
        moveq   #0,d1
        rts

; RendererAppendActorPair
; In: a0=actor, d0.w=badge tile, d1.w=body palette/priority,
; d2.w=two-frame body base, d3.w=animation phase.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererAppendActorPair:
        movem.l d3-d7/a2,-(a7)
        movea.l a0,a2
        move.w  d0,d4
        move.w  d1,d5
        move.w  d2,d6
        move.w  d3,d7
; The current actor's exact dash/phase state is a temporary rust silhouette
; with an explicit D badge. Pointer identity remains authoritative below, so
; this palette change cannot make the current actor follow echo presentation.
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        cmpa.l  a0,a2
        bne.s   .actor_visual_ready
        lea     (VOCAB_ACTOR_BASE+GAMEPLAY_CURRENT_ACTOR_SLOT*VOCAB_ACTOR_BYTES).l,a0
        move.w  (VOCAB_ACTOR_FLAGS,a0),d0
        andi.w  #VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED,d0
        cmpi.w  #VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED,d0
        bne.s   .actor_visual_ready
        move.w  #DIAGNOSTIC_LETTER_A_TILE+3,d4 ; D
        move.w  #RENDERER_HAZARD_ATTRIBUTE,d5
.actor_visual_ready:
        bsr.w   .visual_offset
        move.w  d0,-(a7)

; Reset echoes select a dedicated hollow bracket-and-tail silhouette. All
; other resting bodies hold their directional silhouette; moving bodies
; select one of two deterministic nine-tile poses. Facing is carried by the
; SAT attribute in every case.
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        cmpa.l  a0,a2
        beq.s   .actor_ordinary_frame
        tst.w   (RENDERER_RESET_PHASE).l
        beq.s   .actor_ordinary_frame
        move.w  #RENDERER_TILE_ECHO_RESET,d2
        bra.s   .actor_frame_selected
.actor_ordinary_frame:
        moveq   #0,d2
        move.l  (ACTOR_VX,a2),d0
        or.l    (ACTOR_VY,a2),d0
        beq.s   .actor_frame_ready
        move.w  (ENGINE_TICK).l,d2
        lsr.w   #2,d2
        add.w   d7,d2
        andi.w  #$0001,d2
.actor_frame_ready:
        mulu.w  #RENDERER_ACTOR_FRAME_TILES,d2
        add.w   d6,d2
.actor_frame_selected:
        move.w  (ACTOR_FLAGS,a2),d0
        andi.w  #GAMEPLAY_ACTOR_FLAG_FACING_LEFT,d0
        beq.s   .actor_facing_ready
        ori.w   #RENDERER_ATTR_HFLIP,d2
.actor_facing_ready:
        or.w    d5,d2

        move.l  (ACTOR_X,a2),d0
        asr.l   #8,d0
        add.w   (a7),d0
        subi.w  #12,d0
        move.l  (ACTOR_Y,a2),d1
        asr.l   #8,d1
        subi.w  #12,d1
        move.w  #RENDERER_SPRITE_SIZE_3X3,d3
        bsr.w   RendererTryAppendSprite

; Badges occupy scanlines strictly above the 24x24 body. Echo ordinals spread
; laterally in chronological order, so coincident recordings remain legible.
        move.l  (ACTOR_X,a2),d0
        asr.l   #8,d0
        add.w   (a7),d0
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        cmpa.l  a0,a2
        beq.s   .current_badge
        subi.w  #16,d0
        move.w  d7,d2
        mulu.w  #5,d2
        add.w   d2,d0
        bra.s   .badge_y
.current_badge:
        tst.w   (a7)
        bmi.s   .current_badge_left
        addi.w  #17,d0
        bra.s   .badge_y
.current_badge_left:
        subi.w  #17,d0
.badge_y:
        move.l  (ACTOR_Y,a2),d1
        asr.l   #8,d1
        subi.w  #24,d1
        move.w  d5,d2
        andi.w  #$6000,d2
        ori.w   #RENDERER_ATTR_PRIORITY,d2
        or.w    d4,d2
        move.w  #RENDERER_SPRITE_SIZE_1X1,d3
        bsr.w   RendererTryAppendSprite
        addq.l  #2,a7
        movem.l (a7)+,d3-d7/a2
        rts

; Reset-only echo fan and blocked-current separation are presentation offsets.
; They read state but never mutate actor coordinates, collision, or door state.
; In: a2=actor, d7.w=echo ordinal.
; Out: d0.w=signed visual x offset. Clobbers d0-d1/a0-a1.
.visual_offset:
        lea     (GAMEPLAY_CURRENT_ACTOR).l,a0
        cmpa.l  a0,a2
        bne.s   .echo_visual_offset
        bsr.w   RendererDoorRejectionActive
        tst.w   d0
        beq.s   .visual_offset_zero
        move.w  (VOCAB_CAMPAIGN_FLAGS).l,d0
        andi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE,d0
        bne.s   .campaign_door_mid
        move.l  (ACTOR_X,a2),d0
        cmpi.l  #RENDERER_DOOR_MID_X_16_8,d0
        bge.s   .blocked_right
        moveq   #-RENDERER_BLOCKED_ACTOR_OFFSET,d0
        rts
.campaign_door_mid:
        move.l  (ACTOR_X,a2),d0
        cmp.l   d1,d0
        bge.s   .blocked_right
        moveq   #-RENDERER_BLOCKED_ACTOR_OFFSET,d0
        rts
.blocked_right:
        moveq   #RENDERER_BLOCKED_ACTOR_OFFSET,d0
        rts
.echo_visual_offset:
        tst.w   (RENDERER_RESET_PHASE).l
        beq.s   .visual_offset_zero
        move.w  d7,d0
        mulu.w  #RENDERER_RESET_ECHO_FAN_STEP,d0
        subi.w  #RENDERER_RESET_ECHO_FAN_LEFT,d0
        rts
.visual_offset_zero:
        moveq   #0,d0
        rts

; RendererTryAppendSprite
; In: d0.w=x top-left, d1.w=y top-left, d2.w=tile attribute,
; d3.w=Genesis size bits. Out: d0.w=1 admitted, 0 culled/rejected.
; A sprite that would exceed 64 total entries, 20 entries on any scanline, or
; 320 sprite pixels on any scanline is rejected atomically. The bounded
; single pass may update earlier covered lines transiently, but rolls all of
; them back before returning a rejection. Global maxima publish only after
; every covered line succeeds.
; Clobbers d0-d2/a0-a1; preserves d3-d7/a2-a6.
RendererTryAppendSprite:
        movem.l d3-d7/a2-a3,-(a7)
        move.w  d0,d4                   ; logical x
        move.w  d1,d5                   ; logical y
        move.w  d2,d6                   ; tile attribute
        move.w  d3,d7                   ; size/link high bits

; Derive width and height directly from the hardware size encoding.
        move.w  d7,d2
        lsr.w   #8,d2
        andi.w  #$0003,d2
        addq.w  #1,d2
        lsl.w   #3,d2                   ; height pixels
        move.w  d7,d3
        lsr.w   #8,d3
        lsr.w   #2,d3
        andi.w  #$0003,d3
        addq.w  #1,d3
        lsl.w   #3,d3                   ; width pixels

; Fully offscreen sprites do not consume budget and are not defects.
        move.w  d4,d0
        add.w   d3,d0
        ble.w   .culled
        cmpi.w  #RENDERER_VISIBLE_PIXELS,d4
        bge.w   .culled
        move.w  d5,d0
        add.w   d2,d0
        ble.w   .culled
        cmpi.w  #RENDERER_VISIBLE_SCANLINES,d5
        bge.w   .culled

        moveq   #0,d0
        move.w  (RENDERER_SPRITE_COUNT).l,d0
        cmpi.w  #RENDERER_SPRITE_LIMIT,d0
        bhs.w   .budget_drop

; Clip the vertical coverage to the visible 224-line ledger.
        move.w  d5,d0
        bpl.s   .start_ready
        moveq   #0,d0
.start_ready:
        move.w  d5,d1
        add.w   d2,d1
        cmpi.w  #RENDERER_VISIBLE_SCANLINES,d1
        bls.s   .end_ready
        move.w  #RENDERER_VISIBLE_SCANLINES,d1
.end_ready:
        cmp.w   d1,d0
        bhs.w   .culled

        lea     (RENDERER_SCANLINE_COUNT_BASE).l,a2
        adda.w  d0,a2
        lea     (RENDERER_SCANLINE_PIXEL_BASE).l,a3
        move.w  d0,d2
        add.w   d2,d2
        adda.w  d2,a3
        sub.w   d0,d1
        subq.w  #1,d1
        movea.l a2,a0                   ; rollback boundary for count ledger
        movea.w d6,a1                   ; preserve tile attribute in low word
        moveq   #0,d2
        move.w  (RENDERER_SCANLINE_MAX).l,d2
        moveq   #0,d6
        move.w  (RENDERER_SCANLINE_PIXEL_MAX).l,d6
.mutate_scanline:
        move.b  (a2),d0
        cmpi.b  #RENDERER_SCANLINE_SPRITE_LIMIT,d0
        bhs.w   .rollback_scanlines
        addq.b  #1,d0
        cmp.b   d2,d0
        bls.s   .local_count_ready
        move.b  d0,d2
.local_count_ready:

        move.w  (a3),d0
        add.w   d3,d0
        cmpi.w  #RENDERER_SCANLINE_PIXEL_LIMIT,d0
        bhi.w   .rollback_scanlines
        cmp.w   d6,d0
        bls.s   .local_pixel_ready
        move.w  d0,d6
.local_pixel_ready:

        addq.b  #1,(a2)+
        move.w  d0,(a3)+
        dbf     d1,.mutate_scanline

; Publish the local maxima only after every covered line has committed.
        move.w  d2,(RENDERER_SCANLINE_MAX).l
        move.w  d6,(RENDERER_SCANLINE_PIXEL_MAX).l
        move.w  a1,d6                   ; restore tile attribute

; Append with a zero link, then patch only the preceding entry to this stable
; index. The final entry therefore always terminates the hardware link chain.
        moveq   #0,d0
        move.w  (RENDERER_SPRITE_COUNT).l,d0
        move.w  d0,d1
        lsl.w   #3,d1
        lea     (SAT_SHADOW).l,a2
        adda.w  d1,a2
        tst.w   d0
        beq.s   .write_entry
        move.w  -6(a2),d1
        andi.w  #$FF80,d1
        or.w    d0,d1
        move.w  d1,-6(a2)
.write_entry:
        move.w  d5,d1
        addi.w  #RENDERER_SPRITE_COORD_BIAS,d1
        move.w  d1,(a2)
        move.w  d7,2(a2)
        move.w  d6,4(a2)
        move.w  d4,d1
        addi.w  #RENDERER_SPRITE_COORD_BIAS,d1
        move.w  d1,6(a2)
        addq.w  #1,(RENDERER_SPRITE_COUNT).l
        moveq   #1,d0
        bra.s   .done

; a2/a3 still address the first failing line. Walk backward to the saved
; boundaries, undoing exactly the lines mutated by this sprite. The failing
; line itself was checked before either ledger write and needs no undo.
.rollback_scanlines:
        cmpa.l  a0,a2
        beq.s   .budget_drop
        subq.l  #1,a2
        subq.l  #2,a3
        subq.b  #1,(a2)
        sub.w   d3,(a3)
        bra.s   .rollback_scanlines

.budget_drop:
        ori.w   #RENDERER_FLAG_SPRITE_DROPPED,(RENDERER_FLAGS).l
        cmpi.w  #$FFFF,(RENDERER_DROPPED_SPRITES).l
        beq.s   .culled
        addq.w  #1,(RENDERER_DROPPED_SPRITES).l
.culled:
        moveq   #0,d0
.done:
        movem.l (a7)+,d3-d7/a2-a3
        rts
