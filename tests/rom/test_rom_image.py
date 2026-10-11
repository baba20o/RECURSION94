"""Unit tests for Genesis checksum, header, and vector validation."""

from __future__ import annotations

import struct
import unittest

from tools.rom_tools.rom_image import (
    CHECKSUM_OFFSET,
    DEFAULT_ROM_SIZE,
    EXPECTED_INITIAL_SSP,
    RomValidationError,
    calculate_checksum,
    patch_checksum,
    validate_rom,
)


def _put_text(rom: bytearray, offset: int, size: int, value: str) -> None:
    encoded = value.encode("ascii")
    if len(encoded) > size:
        raise ValueError(value)
    rom[offset : offset + size] = encoded.ljust(size, b" ")


def make_valid_rom() -> bytearray:
    rom = bytearray(b"\xFF" * DEFAULT_ROM_SIZE)
    for index in range(64):
        struct.pack_into(">I", rom, index * 4, 0x00000200)
    struct.pack_into(">I", rom, 0, EXPECTED_INITIAL_SSP)
    _put_text(rom, 0x100, 16, "SEGA MEGA DRIVE")
    _put_text(rom, 0x110, 16, "(C)R94 2026.JUL")
    _put_text(rom, 0x120, 48, "RECURSION 94 TEST")
    _put_text(rom, 0x150, 48, "RECURSION 94 TEST")
    _put_text(rom, 0x180, 14, "GM R94TEST-00")
    struct.pack_into(">H", rom, CHECKSUM_OFFSET, 0)
    _put_text(rom, 0x190, 16, "J")
    struct.pack_into(">IIII", rom, 0x1A0, 0, len(rom) - 1, 0x00FF0000, 0x00FFFFFF)
    rom[0x1B0:0x1BC] = b"RA" + bytes.fromhex("F820") + struct.pack(
        ">II", 0x00200001, 0x00203FFF
    )
    _put_text(rom, 0x1F0, 16, "JUE")
    rom[0x200:0x202] = bytes.fromhex("60FE")
    patch_checksum(rom)
    return rom


class ChecksumTests(unittest.TestCase):
    def test_checksum_is_wrapping_big_endian_word_sum(self) -> None:
        rom = bytearray(0x206)
        rom[0x200:0x206] = bytes.fromhex("1234 FFFF 0002")
        self.assertEqual(calculate_checksum(rom), 0x1235)

    def test_odd_declared_end_pads_low_byte_with_zero(self) -> None:
        rom = bytearray(0x203)
        rom[0x200:0x203] = bytes.fromhex("0102 03")
        self.assertEqual(calculate_checksum(rom), 0x0402)

    def test_header_bytes_do_not_participate(self) -> None:
        rom = make_valid_rom()
        original = calculate_checksum(rom)
        rom[0x120] ^= 0x01
        self.assertEqual(calculate_checksum(rom), original)

    def test_patch_writes_big_endian_checksum(self) -> None:
        rom = make_valid_rom()
        expected = calculate_checksum(rom)
        rom[CHECKSUM_OFFSET : CHECKSUM_OFFSET + 2] = b"\0\0"
        self.assertEqual(patch_checksum(rom), expected)
        self.assertEqual(struct.unpack_from(">H", rom, CHECKSUM_OFFSET)[0], expected)


class HeaderVectorTests(unittest.TestCase):
    def test_valid_fixture_passes(self) -> None:
        report = validate_rom(
            bytes(make_valid_rom()), assembled_bytes=0x202, require_all_vectors=True
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["facts"]["header"]["rom_end"], DEFAULT_ROM_SIZE - 1)

    def test_bad_checksum_fails(self) -> None:
        rom = make_valid_rom()
        rom[0x202] ^= 0x80
        with self.assertRaises(RomValidationError) as caught:
            validate_rom(bytes(rom), assembled_bytes=0x203)
        self.assertIn("checksum", {issue.check for issue in caught.exception.issues})

    def test_zero_exception_vector_fails(self) -> None:
        rom = make_valid_rom()
        struct.pack_into(">I", rom, 7 * 4, 0)
        patch_checksum(rom)
        with self.assertRaises(RomValidationError) as caught:
            validate_rom(bytes(rom), assembled_bytes=0x202)
        self.assertIn(
            "exception_vector_coverage",
            {issue.check for issue in caught.exception.issues},
        )

    def test_reset_vector_must_be_inside_assembled_bytes(self) -> None:
        rom = make_valid_rom()
        struct.pack_into(">I", rom, 4, 0x00010000)
        patch_checksum(rom)
        with self.assertRaises(RomValidationError) as caught:
            validate_rom(bytes(rom), assembled_bytes=0x202)
        self.assertIn("reset_vector_range", {issue.check for issue in caught.exception.issues})

    def test_declared_rom_end_must_match_file(self) -> None:
        rom = make_valid_rom()
        struct.pack_into(">I", rom, 0x1A4, len(rom) - 3)
        patch_checksum(rom, declared_end=len(rom) - 3)
        with self.assertRaises(RomValidationError) as caught:
            validate_rom(bytes(rom), assembled_bytes=0x202)
        self.assertIn("rom_end", {issue.check for issue in caught.exception.issues})

    def test_sram_contract_is_checked_when_declared(self) -> None:
        rom = make_valid_rom()
        struct.pack_into(">I", rom, 0x1B4, 0x00200000)
        patch_checksum(rom)
        with self.assertRaises(RomValidationError) as caught:
            validate_rom(bytes(rom), assembled_bytes=0x202)
        checks = {issue.check for issue in caught.exception.issues}
        self.assertIn("save_lane", checks)
        self.assertIn("save_contract", checks)

    def test_sram_type_flags_are_checked(self) -> None:
        rom = make_valid_rom()
        rom[0x1B2:0x1B4] = bytes.fromhex("0000")
        patch_checksum(rom)
        with self.assertRaises(RomValidationError) as caught:
            validate_rom(bytes(rom), assembled_bytes=0x202)
        self.assertIn(
            "save_type_flags",
            {issue.check for issue in caught.exception.issues},
        )


if __name__ == "__main__":
    unittest.main()
