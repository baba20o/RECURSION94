from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.build_rom import parse_sjasmplus_symbols


ROOT = Path(__file__).resolve().parents[2]


class EchoAudioLayerContractTests(unittest.TestCase):
    def test_68000_command_table_is_cumulative_for_zero_through_six_echoes(self) -> None:
        source = (ROOT / "src/hardware/audio_bridge.asm").read_text("utf-8")
        block = source.split("AudioEchoLayerCommands:", 1)[1].split("even", 1)[0]
        commands = re.findall(
            r"dc\.b\s+\$02,\$([0-9A-Fa-f]{2}),\$00,\$00",
            block,
        )

        self.assertEqual(
            [int(value, 16) for value in commands],
            [0x01, 0x03, 0x07, 0x0F, 0x1F, 0x3F, 0x7F],
        )

    def test_bridge_retries_and_only_latches_a_successful_post(self) -> None:
        source = (ROOT / "src/hardware/audio_bridge.asm").read_text("utf-8")
        routine = source.split("AudioPostEchoLayersIfChanged:", 1)[1].split(
            "AudioRecordBusTimeout:",
            1,
        )[0]

        self.assertIn("(TIMELINE_ECHO_COUNT).l", routine)
        self.assertIn("#GAMEPLAY_ECHO_LIMIT", routine)
        self.assertIn("bsr.w   AudioPostCommand", routine)
        self.assertLess(
            routine.index("tst.w   d0"),
            routine.index("move.b  1(a0),(AUDIO_LAST_POSTED_LAYER_MASK).l"),
        )

    def test_first_echo_layer_has_bounded_audible_z80_percussion(self) -> None:
        source = (ROOT / "src/audio/z80_driver.asm").read_text("utf-8")

        self.assertIn("STATE_LAYER_PULSE_TICKS", source)
        self.assertIn("LAYER_PULSE_VINTS", source)
        self.assertIn("call    TickLayerPulse", source)
        self.assertIn("bit     1,a", source)
        self.assertIn("PSG_LAYER_PERCUSSION_VOLUME", source)
        self.assertIn("ld      (STATE_LAYER_PULSE_TICKS),a", source)
        self.assertIn("ld      a,PSG_MUTE_NOISE", source)

    def test_current_z80_binary_contains_layer_state_below_reserved_runtime(self) -> None:
        symbol_path = ROOT / "build/z80-driver-debug.sym"
        self.assertTrue(symbol_path.is_file(), "Debug Z80 symbols must be built")
        symbols = parse_sjasmplus_symbols(symbol_path)

        self.assertEqual(symbols["STATE_LAYER_PULSE_TICKS"], 0x1008)
        self.assertLessEqual(symbols["DriverImageEnd"], 0x1000)
        self.assertLess(symbols["TickLayerPulse"], symbols["DriverImageEnd"])


if __name__ == "__main__":
    unittest.main()
