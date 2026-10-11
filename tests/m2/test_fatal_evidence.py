from __future__ import annotations

import copy
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tools.m2_fatal_evidence import (
    FATAL_BUTTONS,
    EvidenceFailure,
    capture_window,
    fatal_report,
    png_facts,
    verify_fatal_bindings,
)


def passing_fixture() -> dict[str, object]:
    halt = 0x324
    debug = {
        "frame_counter": 91,
        "heartbeat": 91,
        "fatal_code": 1,
    }
    session = {
        "write_commands": [],
        "bindings": {"status": "pass"},
        "direction_bindings": {"status": "pass"},
        "controller_profile": {
            "status": "pass",
            "port_1_assignment": "gamepad3.1",
            "detail": "standard three-button pad",
        },
    }
    return {
        "rom_sha256": "ab" * 32,
        "pre_exception": {
            "input_held": FATAL_BUTTONS,
            "input_start_held": 1,
            "input_connected": 1,
        },
        "trigger_value": 1,
        "fatal_sample": {
            "safe_entry_pc": "000002C8",
            "debug": copy.deepcopy(debug),
        },
        "halted_samples": [
            {"pc": "00000324", "debug": copy.deepcopy(debug)},
            {"pc": "00000324", "debug": copy.deepcopy(debug)},
        ],
        "safe_exception_address": 0x2C8,
        "halt_address": halt,
        "halt_opcode_hex": "4E72270060FA",
        "stop_observation": {
            "timed_out": True,
            "response": None,
            "observation_seconds": 1.5,
            "next_breakpoint": halt + 4,
        },
        "screenshot": {
            "bytes": 354,
            "width": 320,
            "height": 240,
            "sha256": "cd" * 32,
            "red_pixel_ratio": 0.995,
            "white_pixel_count": 340,
            "quantized_color_count": 2,
            "window_ownership": {
                "process_id": 94,
                "resolved_owner_process_id": 94,
                "capture_method": (
                    "BlastEm ui.screenshot via isolated one-shot key binding"
                ),
                "ui_screenshot_key": "P",
            },
        },
        "session": {
            **session,
            "process_id": 94,
            "screenshot_binding": {"status": "pass", "key": "P"},
        },
    }


class FatalReportTests(unittest.TestCase):
    def test_real_chord_guest_entry_visible_screen_and_stop_pass(self) -> None:
        report = fatal_report(**passing_fixture())
        self.assertEqual(report["status"], "pass")
        self.assertTrue(
            all(item["status"] == "pass" for item in report["checks"])
        )

    def test_opaque_or_incomplete_chord_fails(self) -> None:
        fixture = passing_fixture()
        fixture["pre_exception"]["input_held"] = 0x30
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("real_controller_chord", failures)
        self.assertEqual(report["status"], "fail")

    def test_advancing_counter_rejects_claimed_halt(self) -> None:
        fixture = passing_fixture()
        fixture["halted_samples"][1]["debug"]["frame_counter"] = 92
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("fatal_state_stable_at_halt_entry", failures)

    def test_ram_or_pc_write_packet_rejects_session(self) -> None:
        fixture = passing_fixture()
        fixture["session"]["write_commands"] = ["> Mfff000,1:01"]
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("no_pc_or_ram_patch", failures)

    def test_non_standard_profile_rejects_session(self) -> None:
        fixture = passing_fixture()
        fixture["session"]["controller_profile"][
            "port_1_assignment"
        ] = "gamepad6.1"
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("standard_three_button_profile", failures)

    def test_post_stop_breakpoint_response_rejects_halt_claim(self) -> None:
        fixture = passing_fixture()
        fixture["stop_observation"]["timed_out"] = False
        fixture["stop_observation"]["response"] = "S05"
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("masked_stop_quiescent", failures)

    def test_wrong_halt_opcode_rejects_halt_claim(self) -> None:
        fixture = passing_fixture()
        fixture["halt_opcode_hex"] = "4E714E714E71"
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("halt_loop_encoding", failures)

    def test_normal_non_red_screenshot_rejects_visible_fatal_claim(self) -> None:
        fixture = passing_fixture()
        fixture["screenshot"]["red_pixel_ratio"] = 0.001
        report = fatal_report(**fixture)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("visible_fatal_capture", failures)


class FatalCollectorParsersTests(unittest.TestCase):
    def test_capture_uses_pid_owned_native_framebuffer_path(self) -> None:
        source = Path(capture_window.__code__.co_filename).read_text(
            encoding="utf-8"
        )
        body = source[
            source.index("def capture_window(") : source.index(
                "\ndef main()", source.index("def capture_window(")
            )
        ]
        self.assertIn("capture_native_screenshot", body)
        self.assertIn("screenshot_directory=screenshot_directory", body)
        self.assertNotIn("capture_process_window.ps1", body)

    def test_png_header_dimensions_and_hash_are_recorded(self) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                len(data).to_bytes(4, "big")
                + kind
                + data
                + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(
                    4, "big"
                )
            )

        width = 320
        height = 224
        row = bytes((0,)) + bytes((255, 0, 0, 255)) * width
        payload = b"\x89PNG\r\n\x1a\n"
        payload += chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
        )
        payload += chunk(b"IDAT", zlib.compress(row * height))
        payload += chunk(b"IEND", b"")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fatal.png"
            path.write_bytes(payload)
            facts = png_facts(path)
        self.assertEqual(facts["width"], 320)
        self.assertEqual(facts["height"], 224)
        self.assertEqual(len(facts["sha256"]), 64)
        self.assertEqual(facts["red_pixel_ratio"], 1.0)

    def test_indexed_png_palette_is_decoded_into_fatal_metrics(self) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                len(data).to_bytes(4, "big")
                + kind
                + data
                + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(
                    4, "big"
                )
            )

        width = 4
        height = 2
        filtered = (
            b"\x00" + bytes((0, 0, 1, 1))
            + b"\x00" + bytes((1, 0, 0, 1))
        )
        payload = b"\x89PNG\r\n\x1a\n"
        payload += chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0),
        )
        payload += chunk(b"PLTE", bytes((255, 0, 0, 255, 255, 255)))
        payload += chunk(b"IDAT", zlib.compress(filtered))
        payload += chunk(b"IEND", b"")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fatal-indexed.png"
            path.write_bytes(payload)
            facts = png_facts(path)
        self.assertEqual(facts["width"], width)
        self.assertEqual(facts["height"], height)
        self.assertEqual(facts["red_pixel_count"], 4)
        self.assertEqual(facts["red_pixel_ratio"], 0.5)
        self.assertEqual(facts["white_pixel_count"], 4)
        self.assertEqual(facts["quantized_color_count"], 2)

    def test_indexed_png_out_of_range_palette_index_fails_closed(self) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                len(data).to_bytes(4, "big")
                + kind
                + data
                + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(
                    4, "big"
                )
            )

        payload = b"\x89PNG\r\n\x1a\n"
        payload += chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", 1, 1, 8, 3, 0, 0, 0),
        )
        payload += chunk(b"PLTE", bytes((255, 0, 0)))
        payload += chunk(b"IDAT", zlib.compress(b"\x00\x01"))
        payload += chunk(b"IEND", b"")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fatal-invalid-indexed.png"
            path.write_bytes(payload)
            with self.assertRaisesRegex(EvidenceFailure, "exceeds PLTE"):
                png_facts(path)

    def test_non_png_capture_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fatal.png"
            path.write_bytes(b"not a png")
            with self.assertRaisesRegex(EvidenceFailure, "structural PNG"):
                png_facts(path)

    def test_fatal_key_bindings_are_hash_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "default.cfg"
            path.write_text(
                "\n".join(
                    (
                        "a gamepads.1.a",
                        "s gamepads.1.b",
                        "d gamepads.1.c",
                        "enter gamepads.1.start",
                    )
                ),
                encoding="utf-8",
            )
            facts = verify_fatal_bindings(path)
        self.assertEqual(facts["status"], "pass")
        self.assertEqual(len(facts["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
