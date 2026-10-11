"""Genesis ROM construction and independent static validation."""

from .rom_image import (
    CHECKSUM_DATA_OFFSET,
    CHECKSUM_OFFSET,
    HEADER_OFFSET,
    HEADER_SIZE,
    VECTOR_COUNT,
    VECTOR_TABLE_SIZE,
    RomValidationError,
    calculate_checksum,
    inspect_rom,
    patch_checksum,
    validate_rom,
)

__all__ = [
    "CHECKSUM_DATA_OFFSET",
    "CHECKSUM_OFFSET",
    "HEADER_OFFSET",
    "HEADER_SIZE",
    "VECTOR_COUNT",
    "VECTOR_TABLE_SIZE",
    "RomValidationError",
    "calculate_checksum",
    "inspect_rom",
    "patch_checksum",
    "validate_rom",
]
