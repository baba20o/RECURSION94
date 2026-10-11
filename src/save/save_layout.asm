; RECURSION 94 two-record odd-lane SRAM journal.
; The cartridge header declares byte-wide backup RAM at $200001-$203FFF.

SAVE_SRAM_SLOT_A               equ     $00200001
SAVE_SRAM_SLOT_B               equ     $00200201
SAVE_SRAM_ADDRESS_STRIDE       equ     2
SAVE_RECORD_BYTES              equ     256
SAVE_HEADER_BYTES              equ     32
SAVE_TRAILER_BYTES             equ     8
SAVE_PAYLOAD_BYTES             equ     SAVE_RECORD_BYTES-SAVE_HEADER_BYTES-SAVE_TRAILER_BYTES
SAVE_COMMIT_PHYSICAL_OFFSET    equ     SAVE_RECORD_COMMIT*SAVE_SRAM_ADDRESS_STRIDE

SAVE_RECORD_MAGIC              equ     $00
SAVE_RECORD_VERSION            equ     $04
SAVE_RECORD_SIZE               equ     $06
SAVE_RECORD_GENERATION         equ     $08
SAVE_RECORD_PAYLOAD_SIZE       equ     $0C
SAVE_RECORD_FLAGS              equ     $0E
SAVE_RECORD_RESERVED           equ     $10
SAVE_RECORD_PAYLOAD            equ     $20
SAVE_RECORD_CRC                equ     $F8
SAVE_RECORD_COMMIT             equ     $FC

SAVE_MAGIC_VALUE               equ     $52393453      ; "R94S"
SAVE_FORMAT_VERSION            equ     $0001
SAVE_RECORD_FLAG_VALID         equ     $0001
SAVE_COMMIT_MARKER             equ     $A55A94C3
SAVE_CRC_BYTES                 equ     SAVE_RECORD_CRC

SAVE_PAYLOAD_VERSION           equ     SAVE_RECORD_PAYLOAD+$00
SAVE_PAYLOAD_UNLOCKED_SECTOR   equ     SAVE_RECORD_PAYLOAD+$02
SAVE_PAYLOAD_SETTINGS          equ     SAVE_RECORD_PAYLOAD+$03
SAVE_PAYLOAD_COMPLETION_BITS   equ     SAVE_RECORD_PAYLOAD+$04
SAVE_PAYLOAD_NARRATIVE_BITS    equ     SAVE_RECORD_PAYLOAD+$08
SAVE_PAYLOAD_TOTAL_CLEARS      equ     SAVE_RECORD_PAYLOAD+$0C
SAVE_PAYLOAD_RESERVED          equ     SAVE_RECORD_PAYLOAD+$0E
SAVE_PAYLOAD_METRICS           equ     SAVE_RECORD_PAYLOAD+$10

SAVE_ENCOUNTER_COUNT           equ     20
SAVE_METRIC_CLEARED            equ     $00
SAVE_METRIC_RANK               equ     $01
SAVE_METRIC_ATTEMPTS           equ     $02
SAVE_METRIC_ECHOES             equ     $04
SAVE_METRIC_RESERVED           equ     $05
SAVE_METRIC_TIME_REMAINING     equ     $06
SAVE_METRIC_BYTES              equ     $08
SAVE_PAYLOAD_USED_END          equ     SAVE_PAYLOAD_METRICS+(SAVE_ENCOUNTER_COUNT*SAVE_METRIC_BYTES)

SAVE_SETTING_MUSIC             equ     $01
SAVE_SETTING_SFX               equ     $02
SAVE_SETTING_RESET_FLASH       equ     $04
SAVE_SETTINGS_MASK             equ     $07
SAVE_DEFAULT_SETTINGS          equ     SAVE_SETTING_MUSIC+SAVE_SETTING_SFX+SAVE_SETTING_RESET_FLASH

SAVE_RANK_NONE                 equ     0
SAVE_RANK_STABLE               equ     1
SAVE_RANK_OPTIMIZED            equ     2
SAVE_RANK_TRANSCENDENT         equ     3

SAVE_STAGING_BASE              equ     $00FFA000
SAVE_ACTIVE_BASE               equ     $00FFA100
SAVE_CONTROL_BASE              equ     $00FFA200
SAVE_CONTROL_MAGIC             equ     SAVE_CONTROL_BASE+$00
SAVE_CONTROL_VERSION           equ     SAVE_CONTROL_BASE+$04
SAVE_CONTROL_BYTES             equ     SAVE_CONTROL_BASE+$06
SAVE_CONTROL_STATUS            equ     SAVE_CONTROL_BASE+$08
SAVE_CONTROL_RESULT            equ     SAVE_CONTROL_BASE+$0A
SAVE_CONTROL_ACTIVE_SLOT       equ     SAVE_CONTROL_BASE+$0C
SAVE_CONTROL_DIRTY             equ     SAVE_CONTROL_BASE+$0D
SAVE_CONTROL_GENERATION        equ     SAVE_CONTROL_BASE+$10
SAVE_CONTROL_LAST_CRC          equ     SAVE_CONTROL_BASE+$14
SAVE_CONTROL_LAST_ERROR        equ     SAVE_CONTROL_BASE+$18
SAVE_CONTROL_WRITE_COUNT       equ     SAVE_CONTROL_BASE+$1A
SAVE_CONTROL_USED_END          equ     SAVE_CONTROL_BASE+$1C
SAVE_RAM_END                   equ     $00FFA300

SAVE_CONTROL_MAGIC_VALUE       equ     $5239344A      ; "R94J"
SAVE_CONTROL_VERSION_VALUE     equ     $0001
SAVE_CONTROL_BYTES_VALUE       equ     $0100
SAVE_SLOT_NONE                 equ     $FF

SAVE_STATUS_INITIALIZED        equ     $0001
SAVE_STATUS_LOADED             equ     $0002
SAVE_STATUS_RECOVERED_DEFAULTS equ     $0004
SAVE_STATUS_WRITE_VERIFIED     equ     $0008
SAVE_STATUS_CORRUPT_FALLBACK   equ     $0010

SAVE_RESULT_OK                 equ     $0000
SAVE_RESULT_DEFAULTS           equ     $0001
SAVE_RESULT_INVALID_PAYLOAD    equ     $0002
SAVE_RESULT_WRITE_VERIFY       equ     $0003
SAVE_RESULT_INVALID_STAGE      equ     $0004

        assert  SAVE_SRAM_SLOT_A&1,"save slot A must use the odd SRAM lane"
        assert  SAVE_SRAM_SLOT_B&1,"save slot B must use the odd SRAM lane"
        assert  SAVE_SRAM_SLOT_A+((SAVE_RECORD_BYTES-1)*2)=$002001FF,"save slot A span drift"
        assert  SAVE_SRAM_SLOT_B+((SAVE_RECORD_BYTES-1)*2)=$002003FF,"save slot B span drift"
        assert  SAVE_SRAM_SLOT_B+((SAVE_RECORD_BYTES-1)*2)<=$00203FFF,"save journal exceeds declared SRAM"
        assert  SAVE_PAYLOAD_BYTES=216,"save payload ABI drift"
        assert  SAVE_RECORD_CRC=SAVE_RECORD_BYTES-SAVE_TRAILER_BYTES,"save CRC offset drift"
        assert  SAVE_RECORD_COMMIT=SAVE_RECORD_BYTES-4,"save commit offset drift"
        assert  SAVE_PAYLOAD_USED_END=$D0,"save metric table layout drift"
        assert  SAVE_PAYLOAD_USED_END<=SAVE_RECORD_CRC,"save payload overflows CRC trailer"
        assert  SAVE_METRIC_BYTES=8,"save metric ABI drift"
        assert  SAVE_ENCOUNTER_COUNT=20,"campaign/save encounter count drift"
        assert  SAVE_ACTIVE_BASE-SAVE_STAGING_BASE=SAVE_RECORD_BYTES,"save RAM record spacing drift"
        assert  SAVE_CONTROL_BASE-SAVE_ACTIVE_BASE=SAVE_RECORD_BYTES,"save control spacing drift"
        assert  SAVE_CONTROL_USED_END<=SAVE_RAM_END,"save control overflow"
        assert  SAVE_RAM_END<=$00FFB000,"save journal exceeds save-staging owner"
