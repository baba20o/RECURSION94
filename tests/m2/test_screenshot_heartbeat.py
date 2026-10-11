from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from tools.m2_evidence import EvidenceFailure
from tools.m2_heartbeat_report import heartbeat_report
from tools.m2_screenshot_capture import (
    NATIVE_SCREENSHOT_KEY,
    controlled_session_root,
    launch_isolated_emulator,
    normal_png_facts,
    normal_screenshot_report,
    verify_native_screenshot_binding,
)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def indexed_png(
    width: int, height: int, indices: bytes, palette: bytes
) -> bytes:
    filtered = b"".join(
        b"\x00" + indices[row * width : (row + 1) * width]
        for row in range(height)
    )
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 3, 0, 0, 0))
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"PLTE", palette)
        + png_chunk(b"IDAT", zlib.compress(filtered))
        + png_chunk(b"IEND", b"")
    )


def screenshot_facts() -> dict[str, object]:
    return {
        "path": "artifacts/screenshots/m2-diagnostic.png",
        "bytes": 30_000,
        "width": 656,
        "height": 519,
        "mtime_ns": 200,
        "red_pixel_ratio": 0.001,
        "cyan_pixel_ratio": 0.014,
        "black_pixel_ratio": 0.90,
        "white_pixel_count": 18_000,
        "quantized_color_count": 25,
        "window_ownership": {
            "process_id": 94,
            "resolved_owner_process_id": 94,
            "capture_method": (
                "BlastEm ui.screenshot via isolated one-shot key binding"
            ),
            "ui_screenshot_key": NATIVE_SCREENSHOT_KEY[0],
        },
    }


def screenshot_session() -> dict[str, object]:
    return {
        "user_config_preflight": {"override_absent": True},
        "process_id": 94,
        "screenshot_binding": {"status": "pass"},
        "controller_profile": {
            "status": "pass",
            "port_1_assignment": "gamepad3.1",
            "detail": "standard pad",
            "runtime_copy": {"status": "pass"},
            "session_isolation": {"status": "pass"},
        },
    }


def make_screenshot_report(
    facts: dict[str, object],
) -> dict[str, object]:
    return normal_screenshot_report(
        evidence_type="test_normal_screenshot",
        rom_sha256="ab" * 32,
        screenshot=facts,
        capture_started_ns=100,
        expected_screenshot_path="artifacts/screenshots/m2-diagnostic.png",
        session=screenshot_session(),
        rom_unchanged=True,
        scope_limit="test",
    )


class NormalScreenshotReportTests(unittest.TestCase):
    def test_native_indexed_png_is_decoded_into_m2_metrics(self) -> None:
        payload = indexed_png(
            2,
            2,
            bytes((0, 1, 2, 3)),
            bytes(
                (
                    0,
                    0,
                    0,
                    255,
                    255,
                    255,
                    255,
                    0,
                    0,
                    0,
                    255,
                    255,
                )
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "native.png"
            path.write_bytes(payload)
            facts = normal_png_facts(path)
        self.assertEqual((facts["width"], facts["height"]), (2, 2))
        self.assertEqual(facts["black_pixel_count"], 1)
        self.assertEqual(facts["white_pixel_count"], 1)
        self.assertEqual(facts["red_pixel_count"], 1)
        self.assertEqual(facts["cyan_pixel_count"], 1)
        self.assertEqual(facts["quantized_color_count"], 4)

    def test_native_screenshot_binding_is_exact_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "blastem.cfg"
            path.write_text("p ui.screenshot\n", encoding="utf-8")
            evidence = verify_native_screenshot_binding(path)
            self.assertEqual(evidence["status"], "pass")
            self.assertEqual(evidence["key"], "P")
            path.write_text(
                "p ui.screenshot\nf1 ui.screenshot\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                EvidenceFailure, "exactly P ui.screenshot"
            ):
                verify_native_screenshot_binding(path)

    def test_ephemeral_runtime_parent_is_project_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resolved = controlled_session_root(root)
            self.assertEqual(
                resolved, root / ".toolcache/evidence-sessions"
            )
            self.assertTrue(resolved.is_dir())

    def test_launch_retries_only_transient_windows_access_denied(self) -> None:
        denied = PermissionError("scanner contention")
        denied.winerror = 5
        expected = mock.Mock(pid=94)
        with (
            mock.patch(
                "tools.m2_screenshot_capture.subprocess.Popen",
                side_effect=(denied, denied, expected),
            ) as popen,
            mock.patch("tools.m2_screenshot_capture.time.sleep") as sleep,
        ):
            process, evidence = launch_isolated_emulator(
                ["blastem.exe", "game.bin"], cwd=Path("owned")
            )
        self.assertIs(process, expected)
        self.assertEqual(evidence["attempts"], 3)
        self.assertEqual(evidence["access_denied_retries"], 2)
        self.assertEqual(popen.call_count, 3)
        self.assertEqual(
            [item.args[0] for item in sleep.call_args_list],
            [0.25, 0.5],
        )

        wrong = PermissionError("different failure")
        wrong.winerror = 32
        with mock.patch(
            "tools.m2_screenshot_capture.subprocess.Popen",
            side_effect=wrong,
        ):
            with self.assertRaises(PermissionError):
                launch_isolated_emulator(
                    ["blastem.exe", "game.bin"], cwd=Path("owned")
                )

    def test_current_normal_diagnostic_frame_passes(self) -> None:
        report = make_screenshot_report(screenshot_facts())
        self.assertEqual(report["status"], "pass")
        self.assertTrue(
            all(item["status"] == "pass" for item in report["checks"])
        )

    def test_fatal_red_frame_is_rejected(self) -> None:
        facts = screenshot_facts()
        facts["red_pixel_ratio"] = 0.90
        report = make_screenshot_report(facts)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("normal_not_fatal", failures)

    def test_rich_gameplay_frame_without_cyan_passes(self) -> None:
        facts = screenshot_facts()
        facts["red_pixel_ratio"] = 0.0432
        facts["cyan_pixel_ratio"] = 0.00047
        facts["black_pixel_ratio"] = 0.3552
        facts["white_pixel_count"] = 23_726
        facts["quantized_color_count"] = 205
        report = make_screenshot_report(facts)
        self.assertEqual(report["status"], "pass")
        palette = next(
            item
            for item in report["checks"]
            if item["check"] == "recognized_normal_palette"
        )
        self.assertIn("False/True", palette["detail"])

    def test_stale_capture_timestamp_is_rejected(self) -> None:
        facts = screenshot_facts()
        facts["mtime_ns"] = 99
        report = make_screenshot_report(facts)
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("fresh_owned_capture", failures)

    def test_blank_frame_is_rejected(self) -> None:
        facts = screenshot_facts()
        facts["cyan_pixel_ratio"] = 0.0
        facts["black_pixel_ratio"] = 1.0
        facts["white_pixel_count"] = 0
        facts["quantized_color_count"] = 1
        report = make_screenshot_report(facts)
        self.assertEqual(report["status"], "fail")


def source_report(rom_sha: str, transcript_sha: str) -> dict[str, object]:
    samples = []
    for index in range(3):
        samples.append(
            {
                "label": f"sample-{index}",
                "debug": {
                    "magic": 0x52393444,
                    "abi_version": 1,
                    "abi_size": 0x400,
                    "boot_stage": 4,
                    "fatal_code": 0,
                    "hardware_flags": 0,
                    "sequence": 10 + index * 2,
                    "heartbeat": 5 + index,
                    "frame_counter": 5 + index,
                    "controller_samples": 100 + index,
                },
            }
        )
    return {
        "status": "pass",
        "rom_sha256": rom_sha,
        "checks": [{"check": "source", "status": "pass"}],
        "samples": samples,
        "session": {
            "breakpoint_symbol": "VBlankInterrupt",
            "breakpoint_hex": "0000150C",
            "portable_bundled_config": True,
            "user_config_preflight": {"override_absent": True},
        },
        "transcript": {"sha256": transcript_sha},
    }


class HeartbeatDerivativeTests(unittest.TestCase):
    def test_current_live_samples_and_transcript_pass(self) -> None:
        transcript = (
            "> Z0,150c,2\n< OK\n> c\n< S05\n"
            "> c\n< S05\n> c\n< S05\n"
        )
        transcript_sha = hashlib.sha256(transcript.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "artifacts/reports/m2-controller.json"
            trace_path = root / "artifacts/traces/m2-gdb-transcript.log"
            source_path.parent.mkdir(parents=True)
            trace_path.parent.mkdir(parents=True)
            source = source_report("cd" * 32, transcript_sha)
            source_path.write_text(json.dumps(source), encoding="utf-8")
            trace_path.write_text(
                transcript, encoding="utf-8", newline="\n"
            )
            report = heartbeat_report(
                root=root,
                rom_sha256="cd" * 32,
                source_report=source,
                source_report_path=source_path,
                transcript_path=trace_path,
            )
        self.assertEqual(report["status"], "pass")

    def test_flat_heartbeat_fails_closed(self) -> None:
        transcript = (
            "> Z0,150c,2\n< S05\n< S05\n< S05\n"
        )
        transcript_sha = hashlib.sha256(transcript.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "m2-controller.json"
            trace_path = root / "m2-gdb-transcript.log"
            source = source_report("ef" * 32, transcript_sha)
            source["samples"][1]["debug"]["heartbeat"] = 5
            source_path.write_text(json.dumps(source), encoding="utf-8")
            trace_path.write_text(
                transcript, encoding="utf-8", newline="\n"
            )
            report = heartbeat_report(
                root=root,
                rom_sha256="ef" * 32,
                source_report=source,
                source_report_path=source_path,
                transcript_path=trace_path,
            )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("authoritative_heartbeat_progression", failures)


if __name__ == "__main__":
    unittest.main()
