; Link-complete standalone renderer assembly fixture. It intentionally uses the
; production memory, engine, gameplay, and DMA contracts; only the display-off
; VDP address helper is stubbed because the fixture never executes.

        include "hardware/memory_map.asm"

        org     $000000

        include "game/engine_layout.asm"
        include "gameplay/gameplay_layout.asm"
        include "gameplay/vocabulary_layout.asm"
        include "hardware/dma.asm"
        include "render/gameplay_renderer.asm"

VdpSetVramWriteAddress:
        rts

; Renderer-only fixture fail-closed semantic lookup. Production links the
; campaign controller implementation; this standalone image never executes.
CampaignGetSemanticDuration:
        moveq   #0,d0
        moveq   #-1,d1
        rts
