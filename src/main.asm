; RECURSION 94: THE LAST CARTRIDGE
; Raw Sega Genesis / Mega Drive cartridge translation unit.

        include "hardware/memory_map.asm"

        org     $000000

        include "boot/vectors.asm"
        include "boot/header.asm"
        include "boot/startup.asm"
        include "boot/exceptions.asm"
        include "hardware/z80_bus.asm"
        include "hardware/audio_bridge.asm"
        include "hardware/controllers.asm"
        include "hardware/vdp.asm"
        include "hardware/dma.asm"
        include "hardware/sprites.asm"
        include "boot/m2_diagnostic.asm"
        include "boot/m3_diagnostic.asm"
        include "hardware/interrupts.asm"
        include "boot/diagnostic.asm"
        include "game/engine.asm"
        include "gameplay/gameplay.asm"
        include "gameplay/vocabulary.asm"
        include "gameplay/campaign_runtime.asm"
        include "gameplay/campaign_collision.asm"
        include "gameplay/campaign_projection.asm"
        include "render/gameplay_renderer.asm"
        include "ui/timeline_control.asm"
        include "../assets/generated/art/recursion94_art.inc.asm"
        include "../assets/generated/content/campaign_content.inc.asm"
        include "../assets/generated/campaign_runtime/campaign_runtime.inc.asm"
        include "../assets/generated/crc32/crc32_tables.inc.asm"

        even

RomImageEnd:
