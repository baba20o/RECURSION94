; RECURSION 94 deterministic-engine fixed layouts.
;
; These ranges are the frozen Milestone 3 owners from docs/MEMORY_MAP.md.
; Nothing in the engine may address renderer, hardware, debug, or stack RAM.

ENGINE_CONTROL_BASE             equ     $00FF1400
ENGINE_CONTROL_END              equ     $00FF1800
ENGINE_ACTOR_BASE               equ     $00FF1800
ENGINE_ACTOR_REGION_END         equ     $00FF2000
ENGINE_POOL_BASE                equ     $00FF2000
ENGINE_POOL_REGION_END          equ     $00FF6000
ENGINE_PROJECTION_BASE          equ     $00FF6000
ENGINE_PROJECTION_END           equ     $00FF7000
ENGINE_STAGING_BASE             equ     $00FF7000
ENGINE_STAGING_END              equ     $00FF8000

ENGINE_MAGIC                    equ     ENGINE_CONTROL_BASE+$00
ENGINE_ABI_VERSION              equ     ENGINE_CONTROL_BASE+$04
ENGINE_CONTROL_BYTES            equ     ENGINE_CONTROL_BASE+$06
ENGINE_STATE                    equ     ENGINE_CONTROL_BASE+$08
ENGINE_FLAGS                    equ     ENGINE_CONTROL_BASE+$0A
ENGINE_ENCOUNTER_ID             equ     ENGINE_CONTROL_BASE+$0C
ENGINE_ATTEMPT                  equ     ENGINE_CONTROL_BASE+$0E
ENGINE_TICK                     equ     ENGINE_CONTROL_BASE+$10
ENGINE_TERMINAL_REASON          equ     ENGINE_CONTROL_BASE+$12
ENGINE_LAST_ERROR               equ     ENGINE_CONTROL_BASE+$14
ENGINE_OVERFLOW_FLAGS           equ     ENGINE_CONTROL_BASE+$16
ENGINE_PROJECTION_LENGTH        equ     ENGINE_CONTROL_BASE+$18
ENGINE_STATE_CRC                equ     ENGINE_CONTROL_BASE+$1A
ENGINE_CHECKPOINT_TICK          equ     ENGINE_CONTROL_BASE+$1E
ENGINE_CHECKPOINT_COUNT         equ     ENGINE_CONTROL_BASE+$20
ENGINE_INPUT_HELD               equ     ENGINE_CONTROL_BASE+$22
ENGINE_INPUT_PRESSED            equ     ENGINE_CONTROL_BASE+$23
ENGINE_INPUT_RELEASED           equ     ENGINE_CONTROL_BASE+$24
ENGINE_INPUT_PREVIOUS           equ     ENGINE_CONTROL_BASE+$25
ENGINE_FACING_X                 equ     ENGINE_CONTROL_BASE+$26
ENGINE_ACTIVE_ACTOR_COUNT       equ     ENGINE_CONTROL_BASE+$27
ENGINE_ROOM_LEFT                equ     ENGINE_CONTROL_BASE+$28
ENGINE_ROOM_TOP                 equ     ENGINE_CONTROL_BASE+$2A
ENGINE_ROOM_RIGHT               equ     ENGINE_CONTROL_BASE+$2C
ENGINE_ROOM_BOTTOM              equ     ENGINE_CONTROL_BASE+$2E
ENGINE_SPAWN_X                  equ     ENGINE_CONTROL_BASE+$30
ENGINE_SPAWN_Y                  equ     ENGINE_CONTROL_BASE+$34
ENGINE_RECT_COUNT               equ     ENGINE_CONTROL_BASE+$38
ENGINE_OBJECT_INIT_COUNT        equ     ENGINE_CONTROL_BASE+$3A
ENGINE_POOL_COUNTS              equ     ENGINE_CONTROL_BASE+$40
ENGINE_POOL_HIGH_WATER          equ     ENGINE_CONTROL_BASE+$48
ENGINE_CONTROL_USED_END         equ     ENGINE_CONTROL_BASE+$50

ENGINE_MAGIC_VALUE              equ     $52393445      ; "R94E"
ENGINE_ABI_VERSION_VALUE        equ     $0001
ENGINE_CONTROL_BYTES_VALUE      equ     $0400

ENGINE_STATE_UNLOADED           equ     $0000
ENGINE_STATE_ACTIVE             equ     $0001
ENGINE_STATE_TERMINAL           equ     $0002
ENGINE_STATE_ERROR              equ     $0003

ENGINE_FLAG_LOADED              equ     $0001
ENGINE_FLAG_PROJECTION_VALID    equ     $0002
ENGINE_FLAG_COLLISION           equ     $0004

ENGINE_TERMINAL_NONE            equ     $0000
ENGINE_TERMINAL_TIMEOUT         equ     $0001

ENGINE_ERROR_NONE               equ     $0000
ENGINE_ERROR_ENCOUNTER_NOT_FOUND equ    $0001
ENGINE_ERROR_BAD_DESCRIPTOR     equ     $0002
ENGINE_ERROR_POOL_OVERFLOW      equ     $0003
ENGINE_ERROR_INVALID_STATE      equ     $0004
ENGINE_ERROR_PROJECTION_SIZE    equ     $0005

ENGINE_RESULT_OK                equ     $0000
ENGINE_RESULT_NOT_FOUND         equ     $0001
ENGINE_RESULT_BAD_DESCRIPTOR    equ     $0002
ENGINE_RESULT_POOL_OVERFLOW     equ     $0003
ENGINE_RESULT_NOT_ACTIVE        equ     $0004

ENGINE_ATTEMPT_TICKS            equ     720
ENGINE_CHECKPOINT_INTERVAL      equ     60

INPUT_UP                        equ     $01
INPUT_DOWN                      equ     $02
INPUT_LEFT                      equ     $04
INPUT_RIGHT                     equ     $08
INPUT_A                         equ     $10
INPUT_B                         equ     $20
INPUT_C                         equ     $40
INPUT_ALLOWED_MASK              equ     $7F

; Signed 16.8 fixed-point movement. $110 is $180/sqrt(2), rounded once.
ENGINE_CARDINAL_SPEED           equ     $00000180
ENGINE_DIAGONAL_SPEED           equ     $00000110
ENGINE_PROJECTILE_SPEED         equ     $00000300
ENGINE_PROJECTILE_TICKS         equ     60
ENGINE_PROJECTILE_OWNER_SHIFT   equ     8
ENGINE_PROJECTILE_DAMAGE_DEFAULT equ    1

ACTOR_ACTIVE                    equ     $00
ACTOR_ROLE                      equ     $01
ACTOR_FLAGS                     equ     $02
ACTOR_X                         equ     $04
ACTOR_Y                         equ     $08
ACTOR_VX                        equ     $0C
ACTOR_VY                        equ     $10
ACTOR_HALF_WIDTH                equ     $14
ACTOR_HALF_HEIGHT               equ     $16
ACTOR_INPUT                     equ     $18
ACTOR_PREVIOUS_INPUT            equ     $19
ACTOR_HEALTH                    equ     $1A
ACTOR_LOGICAL_ID                equ     $1C
ACTOR_RESERVED                  equ     $1E
ACTOR_BYTES                     equ     $20
ACTOR_PROJECTION_BYTES          equ     $1E
ACTOR_CAPACITY                  equ     7
ACTOR_CURRENT_ROLE              equ     6
ACTOR_FLAG_COLLISION_X          equ     $0001
ACTOR_FLAG_COLLISION_Y          equ     $0002

OBJECT_ACTIVE                   equ     $00
OBJECT_LOGICAL_ID               equ     $01
OBJECT_STATE                    equ     $02
OBJECT_X                        equ     $04
OBJECT_Y                        equ     $08
OBJECT_VX                       equ     $0C
OBJECT_VY                       equ     $10
OBJECT_TIMER                    equ     $14
OBJECT_PARAM                    equ     $16
OBJECT_BYTES                    equ     $18
OBJECT_PROJECTION_BYTES         equ     $18

POOL_TYPE_ENEMY                 equ     1
POOL_TYPE_PROJECTILE            equ     2
POOL_TYPE_DEVICE                equ     3
POOL_TYPE_CARRYABLE             equ     4
POOL_TYPE_HAZARD                equ     5
POOL_TYPE_BOSS                  equ     6
POOL_TYPE_EFFECT                equ     7
POOL_TYPE_COUNT                 equ     7

ENEMY_CAPACITY                  equ     16
PROJECTILE_CAPACITY             equ     32
DEVICE_CAPACITY                 equ     32
CARRYABLE_CAPACITY              equ     8
HAZARD_CAPACITY                 equ     16
BOSS_COMPONENT_CAPACITY         equ     12
EFFECT_CAPACITY                 equ     24
TOTAL_POOL_CAPACITY             equ     ENEMY_CAPACITY+PROJECTILE_CAPACITY+DEVICE_CAPACITY+CARRYABLE_CAPACITY+HAZARD_CAPACITY+BOSS_COMPONENT_CAPACITY+EFFECT_CAPACITY

ENEMY_POOL                      equ     $00FF2000
PROJECTILE_POOL                 equ     $00FF2200
DEVICE_POOL                     equ     $00FF2600
CARRYABLE_POOL                  equ     $00FF2A00
HAZARD_POOL                     equ     $00FF2B00
BOSS_COMPONENT_POOL             equ     $00FF2D00
EFFECT_POOL                     equ     $00FF2E80
ENGINE_POOL_USED_END            equ     EFFECT_POOL+(EFFECT_CAPACITY*OBJECT_BYTES)

OVERFLOW_ENEMY                  equ     $0001
OVERFLOW_PROJECTILE             equ     $0002
OVERFLOW_DEVICE                 equ     $0004
OVERFLOW_CARRYABLE              equ     $0008
OVERFLOW_HAZARD                 equ     $0010
OVERFLOW_BOSS                   equ     $0020
OVERFLOW_EFFECT                 equ     $0040

ENCOUNTER_DESCRIPTOR_ID         equ     $00
ENCOUNTER_DESCRIPTOR_FLAGS      equ     $02
ENCOUNTER_DESCRIPTOR_VERSION    equ     $04
ENCOUNTER_DESCRIPTOR_BYTES      equ     $06
ENCOUNTER_ROOM_LEFT             equ     $08
ENCOUNTER_ROOM_TOP              equ     $0A
ENCOUNTER_ROOM_RIGHT            equ     $0C
ENCOUNTER_ROOM_BOTTOM           equ     $0E
ENCOUNTER_SPAWN_X               equ     $10
ENCOUNTER_SPAWN_Y               equ     $12
ENCOUNTER_RECT_COUNT            equ     $14
ENCOUNTER_OBJECT_COUNT          equ     $16
ENCOUNTER_RECT_POINTER          equ     $18
ENCOUNTER_OBJECT_POINTER        equ     $1C
ENCOUNTER_DESCRIPTOR_SIZE       equ     $20
ENCOUNTER_DESCRIPTOR_VERSION_VALUE equ $0001

STATIC_RECT_X                   equ     $00
STATIC_RECT_Y                   equ     $02
STATIC_RECT_WIDTH               equ     $04
STATIC_RECT_HEIGHT              equ     $06
STATIC_RECT_BYTES               equ     $08
STATIC_RECT_CAPACITY            equ     32

OBJECT_INIT_TYPE                equ     $00
OBJECT_INIT_LOGICAL_ID          equ     $01
OBJECT_INIT_STATE               equ     $02
OBJECT_INIT_X                   equ     $04
OBJECT_INIT_Y                   equ     $06
OBJECT_INIT_PARAM_0             equ     $08
OBJECT_INIT_PARAM_1             equ     $0A
OBJECT_INIT_BYTES               equ     $0C

CANONICAL_DESCRIPTOR            equ     ENGINE_STAGING_BASE+$000
CANONICAL_RECTS                 equ     ENGINE_STAGING_BASE+$020
CANONICAL_RECTS_END             equ     CANONICAL_RECTS+(STATIC_RECT_CAPACITY*STATIC_RECT_BYTES)
CANONICAL_POOL_LAST_IDS         equ     ENGINE_STAGING_BASE+$1E0
CANONICAL_POOL_LAST_IDS_END     equ     CANONICAL_POOL_LAST_IDS+(POOL_TYPE_COUNT*2)
CANONICAL_POOL_COUNTS           equ     ENGINE_STAGING_BASE+$1F0
CANONICAL_OBJECTS               equ     ENGINE_STAGING_BASE+$200
CANONICAL_OBJECTS_END           equ     CANONICAL_OBJECTS+(TOTAL_POOL_CAPACITY*OBJECT_INIT_BYTES)

PROJECTION_MAGIC_VALUE          equ     $52393450      ; "R94P"
PROJECTION_SCHEMA_VERSION       equ     $0001
PROJECTION_HEADER_BYTES         equ     $28
PROJECTION_POOL_HEADER_BYTES    equ     $04
ENGINE_PROJECTION_SCHEMA_BYTES  equ     PROJECTION_HEADER_BYTES+(ACTOR_CAPACITY*ACTOR_PROJECTION_BYTES)+(POOL_TYPE_COUNT*PROJECTION_POOL_HEADER_BYTES)+(TOTAL_POOL_CAPACITY*OBJECT_PROJECTION_BYTES)

        assert  ENGINE_CONTROL_USED_END<=ENGINE_CONTROL_END,"engine control layout overflow"
        assert  ENGINE_ACTOR_BASE+(ACTOR_CAPACITY*ACTOR_BYTES)<=ENGINE_ACTOR_REGION_END,"actor layout overflow"
        assert  TOTAL_POOL_CAPACITY=140,"canonical pool capacity changed"
        assert  ENEMY_POOL>=ENGINE_POOL_BASE,"enemy pool below owned RAM"
        assert  ENEMY_POOL+(ENEMY_CAPACITY*OBJECT_BYTES)<=PROJECTILE_POOL,"enemy pool overlap"
        assert  PROJECTILE_POOL+(PROJECTILE_CAPACITY*OBJECT_BYTES)<=DEVICE_POOL,"projectile pool overlap"
        assert  DEVICE_POOL+(DEVICE_CAPACITY*OBJECT_BYTES)<=CARRYABLE_POOL,"device pool overlap"
        assert  CARRYABLE_POOL+(CARRYABLE_CAPACITY*OBJECT_BYTES)<=HAZARD_POOL,"carryable pool overlap"
        assert  HAZARD_POOL+(HAZARD_CAPACITY*OBJECT_BYTES)<=BOSS_COMPONENT_POOL,"hazard pool overlap"
        assert  BOSS_COMPONENT_POOL+(BOSS_COMPONENT_CAPACITY*OBJECT_BYTES)<=EFFECT_POOL,"boss pool overlap"
        assert  ENGINE_POOL_USED_END<=ENGINE_POOL_REGION_END,"object pool layout overflow"
        assert  CANONICAL_DESCRIPTOR+ENCOUNTER_DESCRIPTOR_SIZE<=CANONICAL_RECTS,"canonical descriptor overlap"
        assert  CANONICAL_RECTS_END<=CANONICAL_POOL_LAST_IDS,"canonical rectangle layout overflow"
        assert  CANONICAL_POOL_LAST_IDS_END<=CANONICAL_POOL_COUNTS,"canonical logical-ID scratch overlap"
        assert  CANONICAL_POOL_COUNTS+POOL_TYPE_COUNT<=CANONICAL_OBJECTS,"canonical pool count overlap"
        assert  CANONICAL_OBJECTS_END<=ENGINE_STAGING_END,"canonical staging overflow"
        assert  ENGINE_PROJECTION_SCHEMA_BYTES<=$1000,"canonical projection exceeds 4 KiB"
        assert  ENGINE_PROJECTION_BASE+ENGINE_PROJECTION_SCHEMA_BYTES<=ENGINE_PROJECTION_END,"projection RAM overflow"
        assert  ENGINE_STAGING_END=$00FF8000,"engine must stop below renderer RAM"
