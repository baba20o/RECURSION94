from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.m2_audio_capture import (
    VgmError,
    audio_report,
    parse_vgm,
    write_audio_profile,
)


def vgm_fixture(*, dynamic: bool = True) -> bytes:
    header = bytearray(0x40)
    header[0:4] = b"Vgm "
    header[0x08:0x0C] = (0x00000150).to_bytes(4, "little")
    header[0x0C:0x10] = (3_579_545).to_bytes(4, "little")
    header[0x2C:0x30] = (7_670_454).to_bytes(4, "little")
    commands = bytearray()
    if dynamic:
        commands.extend((0x52, 0xA0, 0x84))
        commands.extend((0x52, 0xA4, 0x22))
        commands.extend((0x52, 0x28, 0xF0))
        commands.extend((0x50, 0x86))
        commands.extend((0x50, 0x9A))
        commands.extend((0x61, 0x22, 0x56))
        commands.extend((0x52, 0x28, 0x00))
        commands.extend((0x52, 0xA0, 0xD3))
        commands.extend((0x52, 0x28, 0xF0))
        commands.extend((0x50, 0x8A))
        commands.extend((0x50, 0x9A))
        commands.extend((0x61, 0x22, 0x56))
    else:
        commands.extend((0x61, 0x22, 0x56))
        commands.extend((0x61, 0x22, 0x56))
    commands.append(0x66)
    payload = header + commands
    payload[0x04:0x08] = (len(payload) - 4).to_bytes(4, "little")
    payload[0x18:0x1C] = (44_100).to_bytes(4, "little")
    return bytes(payload)


class VgmParserTests(unittest.TestCase):
    def test_dynamic_ym2612_and_psg_stream_is_parsed(self) -> None:
        facts = parse_vgm(vgm_fixture())
        self.assertEqual(facts["parsed_wait_samples"], 44_100)
        self.assertGreaterEqual(facts["ym_key_on"], 2)
        self.assertGreaterEqual(facts["ym_key_off"], 1)
        self.assertGreaterEqual(facts["ym_frequency_value_count"], 2)
        self.assertGreaterEqual(facts["psg_tone_value_count"], 2)
        self.assertGreater(facts["psg_audible_volume_writes"], 0)

    def test_truncated_or_unknown_stream_fails_closed(self) -> None:
        with self.assertRaises(VgmError):
            parse_vgm(b"not a VGM")
        malformed = bytearray(vgm_fixture())
        malformed[0x40] = 0x01
        with self.assertRaisesRegex(VgmError, "unsupported VGM command"):
            parse_vgm(bytes(malformed))

    def test_silent_timed_stream_does_not_pass_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "artifacts/audio/silent.vgm"
            capture.parent.mkdir(parents=True)
            capture.write_bytes(vgm_fixture(dynamic=False))
            report = audio_report(
                rom_sha256="ab" * 32,
                capture_path=capture,
                root=root,
                capture_metadata={"fixture": True},
            )
        self.assertEqual(report["status"], "fail")

    def test_dynamic_stream_produces_hash_bound_passing_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "artifacts/audio/dynamic.vgm"
            capture.parent.mkdir(parents=True)
            capture.write_bytes(vgm_fixture())
            report = audio_report(
                rom_sha256="cd" * 32,
                capture_path=capture,
                root=root,
                capture_metadata={"fixture": True},
            )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["rom_sha256"], "cd" * 32)
        self.assertEqual(len(report["capture"]["sha256"]), 64)

    def test_header_sample_count_must_match_parsed_waits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = root / "artifacts/audio/mismatched.vgm"
            capture.parent.mkdir(parents=True)
            payload = bytearray(vgm_fixture())
            payload[0x18:0x1C] = (88_200).to_bytes(4, "little")
            capture.write_bytes(payload)
            report = audio_report(
                rom_sha256="ef" * 32,
                capture_path=capture,
                root=root,
                capture_metadata={"fixture": True},
            )
        timing = next(
            item
            for item in report["checks"]
            if item["check"] == "header_timing_matches_stream"
        )
        self.assertEqual(timing["status"], "fail")
        self.assertEqual(report["status"], "fail")


class AudioProfileTests(unittest.TestCase):
    def test_profile_binds_vgm_to_owned_capture_directory(self) -> None:
        default = (
            "bindings {\n"
            "\tkeys {\n"
            "\t\tm ui.vgm_log\n"
            "\t}\n"
            "}\n"
            "ui {\n"
            "\tvgm_path $HOME\n"
            "\tvgm_template blastem_%Y%m%d_%H%M%S.vgm\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "default.cfg"
            source.write_text(default, encoding="utf-8")
            capture = root / "artifacts/audio"
            evidence = write_audio_profile(
                source, root / "profile", capture
            )
            configured = Path(evidence["path"]).read_text("utf-8")

        self.assertIn(f"vgm_path {capture.as_posix()}", configured)
        self.assertIn("recursion94-m2-", configured)


if __name__ == "__main__":
    unittest.main()
