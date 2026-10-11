from __future__ import annotations

import struct
import unittest
import zlib
from pathlib import Path

from tools.audio_compiler.compiler import build_bank_binaries
from tools.audio_reference.bank import CompiledBank


ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"


def with_payload(image: bytes, payload: bytes) -> bytes:
    result = bytearray(image)
    result[12:16] = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
    result[16:] = payload
    return bytes(result)


class CompiledBankDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image = build_bank_binaries(CONTENT)["audio_bank_01.bin"]

    def test_descriptor_and_row_geometry_are_exact(self) -> None:
        bank = CompiledBank.parse(self.image)
        self.assertEqual(1, bank.bank_id)
        self.assertEqual(80, bank.tempo_bpm)
        self.assertEqual(2, bank.echo_cap)
        self.assertEqual(112, len(bank.payload))
        self.assertEqual(7, len(bank.row_notes(0)))
        self.assertEqual(16, len(bank.trace_pattern()))

    def test_layer_mask_changes_events_without_changing_fixed_key_off_cost(self) -> None:
        bank = CompiledBank.parse(self.image)
        full = bank.trace_pattern(0x7F)
        base = bank.trace_pattern(0x01)
        self.assertGreater(
            sum(row.note_events for row in full),
            sum(row.note_events for row in base),
        )
        self.assertTrue(all(row.ym_writes >= 5 for row in full))
        self.assertTrue(all(row.psg_writes >= 2 for row in full))
        self.assertLessEqual(max(row.ym_writes for row in full) + (2 * 4), 32)

    def test_size_magic_version_and_crc_corruption_fail_closed(self) -> None:
        fixtures = []
        fixtures.append(self.image[:-1])
        changed = bytearray(self.image)
        changed[0] ^= 1
        fixtures.append(bytes(changed))
        changed = bytearray(self.image)
        changed[4] = 2
        fixtures.append(bytes(changed))
        changed = bytearray(self.image)
        changed[-1] ^= 1
        fixtures.append(bytes(changed))
        for image in fixtures:
            with self.subTest(image_length=len(image)):
                with self.assertRaises(ValueError):
                    CompiledBank.parse(image)

    def test_invalid_note_and_note_above_cap_fail_even_with_matching_crc(self) -> None:
        payload = bytearray(self.image[16:])
        payload[0] = 13
        with self.assertRaisesRegex(ValueError, "invalid note"):
            CompiledBank.parse(with_payload(self.image, bytes(payload)))

        payload = bytearray(self.image[16:])
        payload[3] = 1
        with self.assertRaisesRegex(ValueError, "disabled layer"):
            CompiledBank.parse(with_payload(self.image, bytes(payload)))

    def test_empty_enabled_layer_fails_even_with_matching_crc(self) -> None:
        payload = bytearray(self.image[16:])
        for row in range(16):
            payload[row * 7] = 0
        with self.assertRaisesRegex(ValueError, "enabled layer 0 is empty"):
            CompiledBank.parse(with_payload(self.image, bytes(payload)))

    def test_row_and_mask_arguments_are_bounded(self) -> None:
        bank = CompiledBank.parse(self.image)
        for row in (-1, 16, True):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    bank.row_notes(row)
        for mask in (-1, 0x80, True):
            with self.subTest(mask=mask):
                with self.assertRaises(ValueError):
                    bank.trace_row(0, mask)


if __name__ == "__main__":
    unittest.main()
