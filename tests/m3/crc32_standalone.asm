; Link-complete standalone CRC translation unit. The generated tables are
; placed once after the routines, matching their production ROM placement.

        org     $000000

        include "game/crc32.asm"
        include "../../assets/generated/crc32/crc32_tables.inc.asm"

Crc32StandaloneEnd:
        assert  Crc32StandaloneEnd-Crc32Tables=CRC32_TABLE_BINARY_BYTES,"standalone CRC table size drift"
        assert  Crc32KnownVectorEnd-Crc32KnownVector=9,"standalone CRC vector size drift"
