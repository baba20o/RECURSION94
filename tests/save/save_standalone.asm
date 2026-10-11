; Link-complete standalone save-journal translation unit. This is deliberately
; not included by the game startup or main translation unit.

        org     $000000

        include "game/crc32.asm"
        include "save/save.asm"
        include "../../assets/generated/crc32/crc32_tables.inc.asm"

; Machine-readable tail used by the host ABI test. Every entry is a long so
; host decoding is independent of assembler alignment choices.
SaveStandaloneAbi:
        dc.b    "SABI"
        dc.l    SAVE_SRAM_SLOT_A
        dc.l    SAVE_SRAM_SLOT_B
        dc.l    SAVE_SRAM_ADDRESS_STRIDE
        dc.l    SAVE_RECORD_BYTES
        dc.l    SAVE_HEADER_BYTES
        dc.l    SAVE_TRAILER_BYTES
        dc.l    SAVE_PAYLOAD_BYTES
        dc.l    SAVE_RECORD_MAGIC
        dc.l    SAVE_RECORD_VERSION
        dc.l    SAVE_RECORD_SIZE
        dc.l    SAVE_RECORD_GENERATION
        dc.l    SAVE_RECORD_PAYLOAD_SIZE
        dc.l    SAVE_RECORD_FLAGS
        dc.l    SAVE_RECORD_RESERVED
        dc.l    SAVE_RECORD_PAYLOAD
        dc.l    SAVE_RECORD_CRC
        dc.l    SAVE_RECORD_COMMIT
        dc.l    SAVE_CRC_BYTES
        dc.l    SAVE_COMMIT_PHYSICAL_OFFSET
        dc.l    SAVE_MAGIC_VALUE
        dc.l    SAVE_FORMAT_VERSION
        dc.l    SAVE_RECORD_FLAG_VALID
        dc.l    SAVE_COMMIT_MARKER
        dc.l    SAVE_PAYLOAD_VERSION
        dc.l    SAVE_PAYLOAD_UNLOCKED_SECTOR
        dc.l    SAVE_PAYLOAD_SETTINGS
        dc.l    SAVE_PAYLOAD_COMPLETION_BITS
        dc.l    SAVE_PAYLOAD_NARRATIVE_BITS
        dc.l    SAVE_PAYLOAD_TOTAL_CLEARS
        dc.l    SAVE_PAYLOAD_RESERVED
        dc.l    SAVE_PAYLOAD_METRICS
        dc.l    SAVE_PAYLOAD_USED_END
        dc.l    SAVE_ENCOUNTER_COUNT
        dc.l    SAVE_METRIC_CLEARED
        dc.l    SAVE_METRIC_RANK
        dc.l    SAVE_METRIC_ATTEMPTS
        dc.l    SAVE_METRIC_ECHOES
        dc.l    SAVE_METRIC_RESERVED
        dc.l    SAVE_METRIC_TIME_REMAINING
        dc.l    SAVE_METRIC_BYTES
        dc.l    SAVE_SETTINGS_MASK
        dc.l    SAVE_DEFAULT_SETTINGS
        dc.l    SAVE_RANK_NONE
        dc.l    SAVE_RANK_STABLE
        dc.l    SAVE_RANK_OPTIMIZED
        dc.l    SAVE_RANK_TRANSCENDENT
        dc.l    SAVE_STAGING_BASE
        dc.l    SAVE_ACTIVE_BASE
        dc.l    SAVE_CONTROL_BASE
        dc.l    SAVE_CONTROL_STATUS
        dc.l    SAVE_CONTROL_RESULT
        dc.l    SAVE_CONTROL_ACTIVE_SLOT
        dc.l    SAVE_CONTROL_DIRTY
        dc.l    SAVE_CONTROL_GENERATION
        dc.l    SAVE_CONTROL_LAST_CRC
        dc.l    SAVE_CONTROL_LAST_ERROR
        dc.l    SAVE_CONTROL_WRITE_COUNT
        dc.l    SAVE_CONTROL_USED_END
        dc.l    SAVE_RAM_END

SaveStandaloneEnd:
        assert  SaveStandaloneEnd-Crc32IsoHdlc>0,"save fixture emitted no code"
        assert  SAVE_RECORD_BYTES=256,"save fixture record ABI drift"
        assert  SAVE_RECORD_CRC=$F8,"save fixture CRC offset drift"
        assert  SAVE_RECORD_COMMIT=$FC,"save fixture commit offset drift"
