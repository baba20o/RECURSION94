from __future__ import annotations

import tempfile
import unittest
from pathlib import Path, PureWindowsPath

from tools.build_rom import (
    BuildFailure,
    find_tool_warning,
    inspect_z80_layout,
    parse_sjasmplus_symbols,
    sjasmplus_command,
)


class SjasmplusContractTests(unittest.TestCase):
    def test_command_contains_frozen_diagnostic_and_output_flags(self) -> None:
        root = Path("/project")
        command = sjasmplus_command(
            executable=root / ".toolcache/bin/sjasmplus.exe",
            root=root,
            source=root / "src/audio/z80_driver.asm",
            output=root / "assets/generated/z80_driver.bin",
            listing=root / "build/z80.lst",
            symbols=root / "build/z80.sym",
            sld=root / "build/z80.sld",
            define="BUILD_DEBUG=1",
        )

        required = [
            "--dirbol",
            "--syntax=abFw",
            "-Wall",
            "--cleanonerror",
            "--lstlab=sort",
        ]
        for flag in required:
            self.assertIn(flag, command)
        ordered_contract = [
            "--dirbol",
            "--syntax=abFw",
            "-Wall",
            "--cleanonerror",
            next(item for item in command if item.startswith("--raw=")),
            next(item for item in command if item.startswith("--lst=")),
            "--lstlab=sort",
            next(item for item in command if item.startswith("--sym=")),
            next(item for item in command if item.startswith("--sld=")),
        ]
        positions = [command.index(item) for item in ordered_contract]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(any(item.startswith("--raw=") for item in command))
        self.assertTrue(any(item.startswith("--lst=") for item in command))
        self.assertTrue(any(item.startswith("--sym=") for item in command))
        self.assertTrue(any(item.startswith("--sld=") for item in command))
        self.assertIn("-DBUILD_DEBUG=1", command)
        self.assertEqual(Path(command[-1]).as_posix(), "src/audio/z80_driver.asm")
        normalized = [item.replace("\\", "/") for item in command]
        self.assertEqual(normalized[0], ".toolcache/bin/sjasmplus.exe")
        self.assertIn("--raw=assets/generated/z80_driver.bin", normalized)
        self.assertIn("--lst=build/z80.lst", normalized)
        self.assertIn("--sym=build/z80.sym", normalized)
        self.assertIn("--sld=build/z80.sld", normalized)
        self.assertFalse(any("/project/" in item for item in normalized))
        self.assertFalse(any("\\" in item for item in command))

    def test_windows_pure_paths_are_emitted_with_forward_slashes(self) -> None:
        root = PureWindowsPath(r"C:\Projects\RECURSION94")
        command = sjasmplus_command(
            executable=root / ".toolcache/bin/sjasmplus.exe",
            root=root,
            source=root / "src/audio/z80_driver.asm",
            output=root / "assets/generated/z80_driver.bin",
            listing=root / "build/z80.lst",
            symbols=root / "build/z80.sym",
            sld=root / "build/z80.sld",
            define="BUILD_DEBUG=1",
        )
        self.assertEqual(command[0], ".toolcache/bin/sjasmplus.exe")
        self.assertEqual(command[-1], "src/audio/z80_driver.asm")
        self.assertFalse(any("\\" in item for item in command))

    def test_warning_detection_rejects_diagnostics_and_nonzero_summary(self) -> None:
        self.assertIsNotNone(
            find_tool_warning("driver.asm(12): warning: truncated expression\n")
        )
        self.assertIsNotNone(find_tool_warning("Errors: 0, warnings: 2\n"))
        self.assertIsNone(find_tool_warning("Errors: 0, warnings: 0\n"))

    def test_symbol_parser_accepts_sjasmplus_scalar_forms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "driver.sym"
            path.write_text(
                "\n".join(
                    (
                        "COMMAND_RING_BASE: EQU 0x00001F00",
                        "DRIVER_STATUS_BASE = $1FE0",
                        "URGENT_MAILBOX_BASE 1FF0h",
                        "DecimalValue: EQU 32",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            symbols = parse_sjasmplus_symbols(path)

        self.assertEqual(symbols["COMMAND_RING_BASE"], 0x1F00)
        self.assertEqual(symbols["DRIVER_STATUS_BASE"], 0x1FE0)
        self.assertEqual(symbols["URGENT_MAILBOX_BASE"], 0x1FF0)
        self.assertEqual(symbols["DecimalValue"], 32)


class Z80LayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.symbols = {
            "DRIVER_STATE_BASE": 0x1000,
            "DriverImageEnd": 0x0FFF,
            "CommandRingStart": 0x1F00,
            "CommandRingDataEnd": 0x1F40,
            "CommandRingRegionEnd": 0x1FE0,
            "DriverStatusStart": 0x1FE0,
            "DriverStatusEnd": 0x1FF0,
            "UrgentMailboxStart": 0x1FF0,
            "UrgentMailboxEnd": 0x2000,
        }

    def test_reserved_regions_are_contiguous_and_payload_is_bounded(self) -> None:
        report = inspect_z80_layout(payload_size=0x0FFF, symbols=self.symbols)

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["runtime_payload_headroom"], 1)
        self.assertEqual(report["payload_headroom"], 0x0F01)
        self.assertEqual(report["reserved_regions"][0]["live_entry_count"], 16)
        self.assertEqual(
            [
                (item["start"], item["end_exclusive"])
                for item in report["reserved_regions"]
            ],
            [(0x1F00, 0x1FE0), (0x1FE0, 0x1FF0), (0x1FF0, 0x2000)],
        )

    def test_payload_cannot_enter_command_ring(self) -> None:
        with self.assertRaisesRegex(BuildFailure, r"exceeding \$1F00"):
            inspect_z80_layout(payload_size=0x1F01, symbols=self.symbols)

    def test_payload_cannot_overwrite_runtime_state(self) -> None:
        self.symbols["DriverImageEnd"] = 0x1001
        with self.assertRaisesRegex(BuildFailure, r"runtime state at \$1000"):
            inspect_z80_layout(payload_size=0x1001, symbols=self.symbols)

    def test_reserved_base_symbol_is_required(self) -> None:
        self.symbols["DriverImageEnd"] = 0x1000
        del self.symbols["DriverStatusStart"]
        with self.assertRaisesRegex(
            BuildFailure, "layout symbol DriverStatusStart is missing"
        ):
            inspect_z80_layout(payload_size=0x1000, symbols=self.symbols)

    def test_reserved_base_symbol_must_match_contract(self) -> None:
        self.symbols["DriverImageEnd"] = 0x1000
        self.symbols["UrgentMailboxStart"] = 0x1FF1
        with self.assertRaisesRegex(
            BuildFailure, r"UrgentMailboxStart must be \$1FF0"
        ):
            inspect_z80_layout(payload_size=0x1000, symbols=self.symbols)


if __name__ == "__main__":
    unittest.main()
