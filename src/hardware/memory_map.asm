; Fixed hardware and Milestone 1 memory constants.

INITIAL_STACK                   equ     $00FFFFFC
WORK_RAM_BASE                   equ     $00FF0000
WORK_RAM_LONGS                  equ     $00004000

HW_VERSION                      equ     $00A10001
IO_PORT1_DATA                   equ     $00A10003
IO_PORT2_DATA                   equ     $00A10005
IO_PORT1_CONTROL                equ     $00A10009
IO_PORT2_CONTROL                equ     $00A1000B
TMSS_REGISTER                   equ     $00A14000
Z80_RAM                         equ     $00A00000
Z80_BUS_REQUEST                 equ     $00A11100
Z80_RESET                       equ     $00A11200

VDP_DATA                        equ     $00C00000
VDP_CONTROL                     equ     $00C00004
PSG_PORT                        equ     $00C00011

SAT_SHADOW                      equ     $00FF8000
SAT_SHADOW_BYTES                equ     $0280
SAT_SHADOW_WORDS                equ     SAT_SHADOW_BYTES/2
DMA_QUEUE_BASE                  equ     $00FF9000
DMA_QUEUE_CAPACITY              equ     32
INPUT_STATE_BASE                equ     $00FF9800
INPUT_HELD                      equ     INPUT_STATE_BASE+$00
INPUT_PRESSED                   equ     INPUT_STATE_BASE+$01
INPUT_RELEASED                  equ     INPUT_STATE_BASE+$02
INPUT_RAW_HIGH                  equ     INPUT_STATE_BASE+$03
INPUT_RAW_LOW                   equ     INPUT_STATE_BASE+$04
INPUT_CONNECTED                 equ     INPUT_STATE_BASE+$05
INPUT_START_HELD                equ     INPUT_STATE_BASE+$06
INPUT_PREVIOUS                  equ     INPUT_STATE_BASE+$07
INPUT_SAMPLE_COUNT              equ     INPUT_STATE_BASE+$08
AUDIO_SHADOW_BASE               equ     $00FF9900

VRAM_PLANE_A                    equ     $C000
VRAM_WINDOW                     equ     $D000
VRAM_PLANE_B                    equ     $E000
VRAM_SAT                        equ     $F000
VRAM_HSCROLL                    equ     $FC00

PLANE_WIDTH_CELLS               equ     64
VISIBLE_WIDTH_CELLS             equ     40
VISIBLE_HEIGHT_CELLS            equ     28
PLANE_ROW_BYTES                 equ     PLANE_WIDTH_CELLS*2

DEBUG_BASE                      equ     $00FFF000
DEBUG_MAGIC                     equ     DEBUG_BASE+$000
DEBUG_ABI_VERSION               equ     DEBUG_BASE+$004
DEBUG_ABI_SIZE                  equ     DEBUG_BASE+$006
DEBUG_SEQUENCE                  equ     DEBUG_BASE+$008
DEBUG_HEARTBEAT                 equ     DEBUG_BASE+$00C
DEBUG_FRAME_COUNTER             equ     DEBUG_BASE+$010
DEBUG_FRAME_TOKEN               equ     DEBUG_BASE+$014
DEBUG_FATAL_CODE                equ     DEBUG_BASE+$016
DEBUG_BOOT_STAGE                equ     DEBUG_BASE+$018
DEBUG_HARDWARE_FLAGS            equ     DEBUG_BASE+$01A
DEBUG_LAST_VDP_STATUS           equ     DEBUG_BASE+$01C
DEBUG_INPUT_HELD                equ     DEBUG_BASE+$01E
DEBUG_INPUT_PRESSED             equ     DEBUG_BASE+$01F
DEBUG_INPUT_RELEASED            equ     DEBUG_BASE+$020
DEBUG_INPUT_CONNECTED           equ     DEBUG_BASE+$021
DEBUG_INPUT_START_HELD          equ     DEBUG_BASE+$022
DEBUG_SPRITE_COUNT              equ     DEBUG_BASE+$024
DEBUG_SPRITE_SCANLINE_MAX       equ     DEBUG_BASE+$026
DEBUG_DMA_WORDS_FRAME           equ     DEBUG_BASE+$028
DEBUG_DMA_WORDS_MAX             equ     DEBUG_BASE+$02A
DEBUG_DMA_QUEUE_DEPTH           equ     DEBUG_BASE+$02C
DEBUG_DMA_QUEUE_HIGH_WATER      equ     DEBUG_BASE+$02E
DEBUG_DMA_OVERFLOW_COUNT        equ     DEBUG_BASE+$030
DEBUG_VBLANK_OVERRUN_COUNT      equ     DEBUG_BASE+$032
DEBUG_AUDIO_STATE               equ     DEBUG_BASE+$034
DEBUG_Z80_HEARTBEAT             equ     DEBUG_BASE+$036
DEBUG_AUDIO_COMMAND_COUNT       equ     DEBUG_BASE+$038
DEBUG_M2_FLAGS                  equ     DEBUG_BASE+$03A
DEBUG_CONTROLLER_SAMPLES        equ     DEBUG_BASE+$03C
DEBUG_Z80_TIMEOUT_COUNT         equ     DEBUG_BASE+$040
DEBUG_DMA_DEFERRED_COUNT        equ     DEBUG_BASE+$042
DEBUG_DMA_REQUIRED_DROP_COUNT   equ     DEBUG_BASE+$044
DEBUG_DMA_INVALID_COUNT         equ     DEBUG_BASE+$046
DEBUG_ENGINE_STATE              equ     DEBUG_BASE+$048
DEBUG_ENGINE_TICK               equ     DEBUG_BASE+$04A
DEBUG_ENGINE_ENCOUNTER          equ     DEBUG_BASE+$04C
DEBUG_ENGINE_LAST_ERROR         equ     DEBUG_BASE+$04E
DEBUG_ENGINE_STATE_CRC          equ     DEBUG_BASE+$050
DEBUG_ENGINE_CHECKPOINT_TICK    equ     DEBUG_BASE+$054
DEBUG_ENGINE_CHECKPOINT_COUNT   equ     DEBUG_BASE+$056
DEBUG_ENGINE_ATTEMPT            equ     DEBUG_BASE+$058
DEBUG_ENGINE_OVERFLOW_FLAGS     equ     DEBUG_BASE+$05A
DEBUG_ENGINE_PROJECTION_LENGTH  equ     DEBUG_BASE+$05C
DEBUG_ENGINE_ACTIVE_ACTORS      equ     DEBUG_BASE+$05E
DEBUG_GAMEPLAY_FLAGS            equ     DEBUG_BASE+$060
DEBUG_GAMEPLAY_RESULT           equ     DEBUG_BASE+$062
DEBUG_GAMEPLAY_DOOR_STATE       equ     DEBUG_BASE+$064
DEBUG_GAMEPLAY_SWITCH_MASK      equ     DEBUG_BASE+$066
DEBUG_TIMELINE_ECHO_COUNT       equ     DEBUG_BASE+$068
DEBUG_TIMELINE_CURRENT_BANK     equ     DEBUG_BASE+$069
DEBUG_TIMELINE_FLAGS            equ     DEBUG_BASE+$06A
DEBUG_TIMELINE_SELECTED_ORDINAL equ     DEBUG_BASE+$06B
DEBUG_TIMELINE_ATTEMPT_LENGTH   equ     DEBUG_BASE+$06C
DEBUG_GAMEPLAY_LAST_BANK_CRC    equ     DEBUG_BASE+$070
DEBUG_GAMEPLAY_STABLE_TICK      equ     DEBUG_BASE+$074

DEBUG_MAGIC_R94D                equ     $52393444
DEBUG_ABI_VERSION_VALUE         equ     $0001
DEBUG_ABI_SIZE_VALUE            equ     $0400

BOOT_STAGE_RAM_READY            equ     $0001
BOOT_STAGE_Z80_SAFE             equ     $0002
BOOT_STAGE_VIDEO_READY          equ     $0003
BOOT_STAGE_RUNNING              equ     $0004

HARDWARE_FLAG_Z80_TIMEOUT       equ     $0001

M2_FLAG_INPUT_ACTIVE            equ     $0001
M2_FLAG_SPRITE_ACTIVE           equ     $0002
M2_FLAG_DMA_ACTIVE              equ     $0004
M2_FLAG_Z80_UPLOADED            equ     $0008
M2_FLAG_YM2612_ACTIVE           equ     $0010
M2_FLAG_PSG_ACTIVE              equ     $0020

FATAL_UNEXPECTED_EXCEPTION      equ     $0001

VDP_COMMAND_VRAM_WRITE_0        equ     $40000000
VDP_COMMAND_CRAM_WRITE_0        equ     $C0000000
VDP_COMMAND_VSRAM_WRITE_0       equ     $40000010
VDP_COMMAND_FONT_WRITE          equ     $40200000
VDP_COMMAND_PULSE_CRAM_WRITE    equ     $C07C0000

VDP_REGISTER_DISPLAY_OFF        equ     $8104
VDP_REGISTER_DISPLAY_ON         equ     $8164

DIAGNOSTIC_PALETTE_BITS         equ     $6000
DIAGNOSTIC_DIGIT_ZERO_TILE      equ     1
DIAGNOSTIC_LETTER_A_TILE        equ     11
DIAGNOSTIC_PULSE_TILE           equ     37
DIAGNOSTIC_GRID_TILE            equ     38
DIAGNOSTIC_FONT_GLYPHS          equ     36
