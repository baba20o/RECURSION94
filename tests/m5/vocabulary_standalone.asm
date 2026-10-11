; Link-complete fixture for the deliberately unwired M5 vocabulary.

        include "game/engine_layout.asm"
        include "gameplay/gameplay_layout.asm"

CAMPAIGN_CONTROLLER_NOT_FOUND equ $FFFF
CAMPAIGN_RUNTIME_SEMANTIC_FLAGS equ 14
CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_WIDTH_SHIFT equ 5
CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_WIDTH_MASK equ $07E0
CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_HEIGHT_MASK equ $F800

        org     $000000

        include "game/object_pool.asm"
        include "gameplay/vocabulary.asm"
        include "gameplay/campaign_collision.asm"

GameplayMoveActorAxisSeparated:
        rts

CampaignGetSemanticDuration:
        moveq   #0,d0
        moveq   #-1,d1
        rts

CampaignDependencyGateSatisfied:
        moveq   #1,d0
        rts

CampaignFindSemantic:
        moveq   #-1,d0
        suba.l  a0,a0
        rts

GameplaySetEchoProtected:
        moveq   #0,d0
        rts

GameplaySelectReplacement:
        moveq   #0,d0
        rts
