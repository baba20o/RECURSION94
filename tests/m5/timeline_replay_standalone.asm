; Link-complete ABI-v3 replay/timeline fixture.

        include "hardware/memory_map.asm"
        include "game/engine_layout.asm"
        include "gameplay/gameplay_layout.asm"
        include "gameplay/vocabulary_layout.asm"

        org     $000000

        include "gameplay/replay.asm"
        include "ui/timeline_control.asm"

Crc32IsoHdlc:
        moveq   #0,d0
        rts

EngineResetCanonicalState:
        moveq   #0,d0
        rts

GameplayReinstantiateActors:
        moveq   #0,d0
        rts

SafeException:
        rts
