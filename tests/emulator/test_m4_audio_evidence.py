from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from tools.m3_runtime_evidence import crc32_iso_hdlc
from tools.m4_audio_evidence import (
    CANONICAL_ATTEMPT_0,
    FIRST_RUNNING_VBLANK_ROLE,
    POST_COMMIT_CAPTURE_FRAMES,
    REQUIRED_SYMBOLS,
    REPORT_SCHEMA,
    SOURCE_BINDING_PATHS,
    STARTUP_PRIMING_ROLE,
    VgmError,
    build_audio_report,
    configure_m4_audio_profile,
    _run_live_session,
    parse_audio_shadow,
    parse_gameplay_control,
    parse_replay_descriptor,
    parse_timeline,
    parse_vgm,
    recheck_existing_report,
    reconstruct_public_session,
    source_binding,
    verify_saved_report,
)


def vgm_fixture(
    *,
    early_layer: bool = False,
    omit_layer_mutes: bool = False,
    layer_enabled: bool = True,
) -> bytes:
    header = bytearray(0x40)
    header[0:4] = b"Vgm "
    header[0x08:0x0C] = (0x00000150).to_bytes(4, "little")
    header[0x0C:0x10] = (3_579_545).to_bytes(4, "little")
    header[0x2C:0x30] = (7_670_454).to_bytes(4, "little")
    commands = bytearray()
    layer_starts = {15 if early_layer else 45, 60, 75}
    layer_mutes = {start + 2 for start in layer_starts}
    for frame in range(90):
        if frame == 0:
            commands.extend((0x50, 0xE4, 0x50, 0xFA))
        if frame == 4:
            commands.extend((0x50, 0xFF))
        if frame % 15 == 0:
            note_low = 0x84 if (frame // 15) % 2 == 0 else 0xD3
            commands.extend((0x52, 0x28, 0x00))
            commands.extend((0x52, 0xA4, 0x22))
            commands.extend((0x52, 0xA0, note_low))
            commands.extend((0x52, 0x28, 0xF0))
            commands.extend((0x50, 0x86 if note_low == 0x84 else 0x8A))
            commands.extend((0x50, 0x0D if note_low == 0x84 else 0x0A))
            commands.extend((0x50, 0x9A))
        if layer_enabled and frame in layer_starts:
            commands.extend((0x50, 0xE4, 0x50, 0xF8))
        if (
            layer_enabled
            and not omit_layer_mutes
            and frame in layer_mutes
        ):
            commands.extend((0x50, 0xFF))
        commands.append(0x62)
    commands.append(0x66)
    payload = header + commands
    payload[0x04:0x08] = (len(payload) - 4).to_bytes(4, "little")
    payload[0x18:0x1C] = (90 * 735).to_bytes(4, "little")
    return bytes(payload)


def vgm_fixture_with_chip_write_gaps() -> bytes:
    payload = bytearray(vgm_fixture())
    cursor = 0x40
    insertions = 0
    while True:
        index = payload.find(bytes((0x50, 0xE4, 0x50, 0xF8)), cursor)
        if index < 0:
            break
        payload[index + 2 : index + 2] = bytes((0x70,))
        insertions += 1
        cursor = index + 6
    payload[0x04:0x08] = (len(payload) - 4).to_bytes(4, "little")
    payload[0x18:0x1C] = (
        90 * 735 + insertions
    ).to_bytes(4, "little")
    return bytes(payload)


def write_source_tree(root: Path) -> None:
    for index, relative in enumerate(SOURCE_BINDING_PATHS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"; source {index}\n", encoding="utf-8")


def audio_state(*, layers: int, commands: int, posted: int) -> dict:
    return {
        "signature": 0x94,
        "abi_version": 1,
        "state": 2,
        "flags": 0x07,
        "heartbeat": 100,
        "read_sequence": commands,
        "queue_high_water": 1,
        "processed_commands": commands,
        "late_tick_count": 0,
        "ym_write_count": 20,
        "psg_write_count": 20,
        "motif_step": 0,
        "active_layer_mask": layers,
        "last_error_or_command": 2,
        "boot_smoke_latch": 1,
        "last_posted_layer_mask": posted,
    }


def snapshot(
    *,
    tick: int,
    attempt: int,
    echo_count: int,
    layers: int,
    commands: int,
    posted: int,
) -> dict:
    return {
        "label": "fixture",
        "engine": {
            "magic": 0x52393445,
            "tick": tick,
            "attempt": attempt,
            "state": 1,
            "last_error": 0,
            "overflow_flags": 0,
        },
        "gameplay": {
            "magic": 0x52393447,
            "flags": 1,
            "result": 0,
            "last_commit_reason": 0,
            "last_bank_crc": 0,
        },
        "timeline": {
            "magic": 0x52393454,
            "echo_count": echo_count,
            "echo_order": [6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF],
            "current_bank": 6 if echo_count == 0 else 0,
            "attempt_length": tick if attempt == 0 else 0,
            "flags": 1,
        },
        "input": {
            "connected": 1,
            "held": 0,
            "pressed": 0,
            "released": 0,
            "start_held": 0,
        },
        "audio": audio_state(
            layers=layers, commands=commands, posted=posted
        ),
        "debug": {
            "fatal_code": 0,
            "hardware_flags": 0,
            "z80_timeout_count": 0,
        },
    }


def synthetic_session() -> dict:
    crc = crc32_iso_hdlc(CANONICAL_ATTEMPT_0)
    pre = snapshot(
        tick=40,
        attempt=0,
        echo_count=0,
        layers=1,
        commands=1,
        posted=1,
    )
    manual_entry = snapshot(
        tick=40,
        attempt=0,
        echo_count=0,
        layers=1,
        commands=1,
        posted=1,
    )
    manual_entry["input"].update(
        {"start_held": 1, "held": 0x40, "pressed": 0x40}
    )
    immediate = snapshot(
        tick=0,
        attempt=1,
        echo_count=1,
        layers=1,
        commands=1,
        posted=3,
    )
    immediate["gameplay"].update(
        {
            "last_commit_reason": 2,
            "result": 0,
            "last_bank_crc": crc,
        }
    )
    immediate["descriptor_6"] = {
        "length": 40,
        "flags": 0x03,
        "crc32_iso_hdlc": crc,
        "crc32_iso_hdlc_hex": f"{crc:08X}",
    }
    immediate["bank_6_inputs"] = list(CANONICAL_ATTEMPT_0)
    active = snapshot(
        tick=1,
        attempt=1,
        echo_count=1,
        layers=3,
        commands=2,
        posted=3,
    )
    final = snapshot(
        tick=46,
        attempt=1,
        echo_count=1,
        layers=3,
        commands=2,
        posted=3,
    )
    return {
        "metadata": {
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "session_isolation": {"status": "pass"},
            },
            "initial_breakpoint_symbol": "VBlankInterrupt",
            "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
            "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
            "counters_and_controller_poll_begin": (
                FIRST_RUNNING_VBLANK_ROLE
            ),
            "step_breakpoint_symbol": "MainLoopPostGameplayStep",
            "attempt_1_input_staging_breakpoint_symbol": (
                "TimelineHandleMetaInput"
            ),
            "attempt_1_input_staging_breakpoint": "0000578E",
            "attempt_1_input_staging_role": (
                "after_controller_poll_before_commit_reset"
            ),
            "transcript_sha256": "1" * 64,
        },
        "initial": snapshot(
            tick=0,
            attempt=0,
            echo_count=0,
            layers=1,
            commands=0,
            posted=1,
        ),
        "progression": list(range(1, 41)),
        "attempt_0_inputs": list(CANONICAL_ATTEMPT_0),
        "pre_commit": pre,
        "manual_commit_entry": manual_entry,
        "post_commit_immediate": immediate,
        "attempt_1_inputs": [0] * POST_COMMIT_CAPTURE_FRAMES,
        "post_commit_active": active,
        "final": final,
        "input_injection": {
            "status": "pass",
            "staged_keydown_order": ["Start", "C"],
            "staged_keyup_order": ["C", "Start"],
            "next_attempt_events_staged_at": (
                "TimelineHandleMetaInput after ControllerPoll"
            ),
            "guest_ram_register_or_pc_writes": False,
        },
        "_transcript_lines": ["> mfff000,48", "< 00"],
    }


class VgmParserTests(unittest.TestCase):
    def test_distinguishes_boot_sfx_from_post_echo_percussion(self) -> None:
        facts = parse_vgm(vgm_fixture())
        self.assertEqual(facts["psg_boot_sfx_starts"], [0])
        self.assertEqual(
            facts["psg_layer_pulse_starts"],
            [45 * 735, 60 * 735, 75 * 735],
        )
        self.assertTrue(
            all(
                pulse["bounded_and_muted"]
                for pulse in facts["psg_layer_pulses"]
            )
        )
        self.assertEqual(
            facts["header_total_samples"], facts["parsed_wait_samples"]
        )

    def test_unmuted_layer_pulses_are_observable_failures(self) -> None:
        facts = parse_vgm(vgm_fixture(omit_layer_mutes=True))
        self.assertTrue(facts["psg_layer_pulses"])
        self.assertTrue(
            any(
                not pulse["bounded_and_muted"]
                for pulse in facts["psg_layer_pulses"]
            )
        )

    def test_cycle_timed_psg_pair_is_not_required_to_share_a_sample(self) -> None:
        facts = parse_vgm(vgm_fixture_with_chip_write_gaps())
        self.assertEqual(len(facts["psg_layer_pulses"]), 3)
        self.assertTrue(
            all(
                pulse["bounded_and_muted"]
                for pulse in facts["psg_layer_pulses"]
            )
        )

    def test_malformed_truncated_and_unknown_streams_fail_closed(self) -> None:
        with self.assertRaises(VgmError):
            parse_vgm(b"not VGM")
        truncated = bytearray(vgm_fixture())
        truncated[-2] = 0x61
        with self.assertRaises(VgmError):
            parse_vgm(bytes(truncated))
        unknown = bytearray(vgm_fixture())
        unknown[0x40] = 0x01
        with self.assertRaisesRegex(VgmError, "unsupported VGM command"):
            parse_vgm(bytes(unknown))


class AbiParserTests(unittest.TestCase):
    def test_audio_shadow_parses_little_endian_driver_counters(self) -> None:
        raw = bytearray(18)
        raw[0:4] = bytes((0x94, 1, 2, 7))
        raw[4:6] = bytes((0x34, 0x12))
        raw[8:10] = bytes((0x78, 0x56))
        raw[14:18] = bytes((3, 2, 1, 3))
        parsed = parse_audio_shadow(bytes(raw))
        self.assertEqual(parsed["heartbeat"], 0x1234)
        self.assertEqual(parsed["processed_commands"], 0x5678)
        self.assertEqual(parsed["active_layer_mask"], 3)
        self.assertEqual(parsed["last_posted_layer_mask"], 3)

    def test_fixed_width_parsers_reject_wrong_lengths(self) -> None:
        for parser, payload in (
            (parse_audio_shadow, bytes(17)),
            (parse_gameplay_control, bytes(35)),
            (parse_timeline, bytes(23)),
            (parse_replay_descriptor, bytes(7)),
        ):
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(ValueError):
                    parser(payload)


class CollectorContractTests(unittest.TestCase):
    def test_meta_entry_release_staging_precedes_post_reset_stop(self) -> None:
        self.assertIn("TimelineHandleMetaInput", REQUIRED_SYMBOLS)
        source = inspect.getsource(_run_live_session)
        ordered_fragments = (
            'meta_input = symbols["TimelineHandleMetaInput"]',
            'remote.request(f"Z0,{meta_input:x},2")',
            "manual_commit_entry = _capture_snapshot(",
            "for key in (C_KEY, START_KEY):",
            'remote.request(f"z0,{meta_input:x},2")',
            'remote.request(f"Z0,{post_step:x},2")',
            "post_commit = _capture_snapshot(",
            'attempt_1_inputs.append(int(snapshot["input"]["held"]))',
            "bytes(attempt_1_inputs) != bytes(POST_COMMIT_CAPTURE_FRAMES)",
        )
        cursor = -1
        for fragment in ordered_fragments:
            with self.subTest(fragment=fragment):
                cursor = source.index(fragment, cursor + 1)


class AudioReportTests(unittest.TestCase):
    def test_committed_audio_report_binds_frozen_historical_rom(self) -> None:
        root = Path(__file__).resolve().parents[2]
        report_path = root / "artifacts/reports/m4-audio-capture.json"
        archive_rom = (
            root
            / "artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5"
            / "recursion94-debug.bin"
        )
        self.assertTrue(report_path.is_file(), "M4 audio report is missing")
        self.assertTrue(archive_rom.is_file(), "frozen M4 ROM is missing")
        report = json.loads(report_path.read_text("utf-8"))
        failures = verify_saved_report(
            root=root,
            rom=archive_rom,
            report=report,
            source_policy="captured",
        )
        self.assertEqual(failures, [])
        self.assertEqual(
            "c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460",
            report["rom_sha256"],
        )

    def make_report(
        self,
        root: Path,
        *,
        vgm: bytes | None = None,
        session: dict | None = None,
    ) -> tuple[dict, Path, Path]:
        write_source_tree(root)
        capture = root / "artifacts/audio/recursion94-m4-audio-fixture.vgm"
        capture.parent.mkdir(parents=True)
        capture.write_bytes(vgm or vgm_fixture())
        rom = root / "build/recursion94-debug.bin"
        rom.parent.mkdir(parents=True)
        rom.write_bytes(b"fixture ROM")
        binding = source_binding(root)
        rom_sha = __import__("hashlib").sha256(rom.read_bytes()).hexdigest()
        report = build_audio_report(
            root=root,
            rom_sha256=rom_sha,
            capture_path=capture,
            session=session or synthetic_session(),
            provenance={
                "rom": {"sha256": rom_sha},
                "source_binding_before": binding,
                "source_binding_after": binding,
                "emulator": {"sha256": "2" * 64},
                "symbol_map": {
                    "sha256": "3" * 64,
                    "symbols": {
                        "TimelineHandleMetaInput": "0000578E",
                    },
                },
            },
        )
        return report, rom, capture

    def test_valid_transition_passes_every_strict_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(Path(temporary))
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["checks"])
        self.assertTrue(
            all(item["status"] == "pass" for item in report["checks"])
        )

    def test_layer_pulse_before_acquisition_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), vgm=vgm_fixture(early_layer=True)
            )
        target = next(
            item
            for item in report["checks"]
            if item["check"]
            == "percussion_signature_only_after_echo_acquisition"
        )
        self.assertEqual(target["status"], "fail")
        self.assertEqual(report["status"], "fail")

    def test_missing_post_echo_layer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), vgm=vgm_fixture(layer_enabled=False)
            )
        target = next(
            item
            for item in report["checks"]
            if item["check"]
            == "percussion_signature_only_after_echo_acquisition"
        )
        self.assertEqual(target["status"], "fail")

    def test_wrong_input_schedule_and_debugger_write_fail(self) -> None:
        session = synthetic_session()
        session["attempt_0_inputs"][0] = 0
        session["_transcript_lines"].append("> Mff0000,1:00")
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), session=session
            )
        by_name = {item["check"]: item for item in report["checks"]}
        self.assertEqual(
            by_name["exact_real_pad_attempt_schedule"]["status"], "fail"
        )
        self.assertEqual(
            by_name["read_only_debugger_session"]["status"], "fail"
        )

    def test_missing_startup_priming_role_fails_closed(self) -> None:
        session = synthetic_session()
        session["metadata"].pop("initial_breakpoint_role")
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), session=session
            )
        by_name = {item["check"]: item for item in report["checks"]}
        self.assertEqual(
            by_name["symbol_bound_observation"]["status"], "fail"
        )
        self.assertEqual(report["status"], "fail")

    def test_missing_pre_reset_input_staging_fails_closed(self) -> None:
        session = synthetic_session()
        session["metadata"].pop("attempt_1_input_staging_role")
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), session=session
            )
        by_name = {item["check"]: item for item in report["checks"]}
        self.assertEqual(
            by_name["attempt_1_input_staged_before_commit_reset"]["status"],
            "fail",
        )
        self.assertEqual(report["status"], "fail")

    def test_wrong_meta_entry_chord_fails_closed(self) -> None:
        session = synthetic_session()
        session["manual_commit_entry"]["input"]["held"] = 0
        session["manual_commit_entry"]["input"]["pressed"] = 0
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), session=session
            )
        by_name = {item["check"]: item for item in report["checks"]}
        self.assertEqual(
            by_name["real_start_c_manual_commit"]["status"], "fail"
        )
        self.assertEqual(
            by_name["attempt_1_input_staged_before_commit_reset"]["status"],
            "fail",
        )
        self.assertEqual(report["status"], "fail")

    def test_post_reset_c_contamination_fails_closed(self) -> None:
        session = synthetic_session()
        session["attempt_1_inputs"][0] = 0x40
        with tempfile.TemporaryDirectory() as temporary:
            report, _rom, _capture = self.make_report(
                Path(temporary), session=session
            )
        by_name = {item["check"]: item for item in report["checks"]}
        self.assertEqual(
            by_name["attempt_1_input_staged_before_commit_reset"]["status"],
            "fail",
        )
        self.assertEqual(report["status"], "fail")

    def test_saved_report_rejects_rom_source_and_capture_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, rom, capture = self.make_report(root)
            self.assertEqual(
                verify_saved_report(root=root, rom=rom, report=report), []
            )
            source = root / SOURCE_BINDING_PATHS[0]
            source.write_text("; drift\n", encoding="utf-8")
            capture.write_bytes(capture.read_bytes() + b"\0")
            rom.write_bytes(b"drifted ROM")
            failures = verify_saved_report(
                root=root, rom=rom, report=report
            )
        self.assertIn("ROM SHA-256 drift", failures)
        self.assertIn("source binding drift", failures)
        self.assertIn("capture SHA-256 drift", failures)

    def test_saved_report_rejects_legacy_schema_and_missing_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, rom, _capture = self.make_report(root)
            report["schema"] = "r94-m4-audio-evidence-v1"
            report["checks"] = [
                item
                for item in report["checks"]
                if item["check"]
                != "attempt_1_input_staged_before_commit_reset"
            ]
            failures = verify_saved_report(
                root=root, rom=rom, report=report
            )
        self.assertIn("schema mismatch", failures)
        self.assertIn("required current checks are missing", failures)

    def test_captured_source_policy_rejects_internal_bundle_tamper(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, rom, _capture = self.make_report(root)
            report["provenance"]["source_binding_after"] = json.loads(
                json.dumps(
                    report["provenance"]["source_binding_after"]
                )
            )
            report["provenance"]["source_binding_after"]["sources"][
                SOURCE_BINDING_PATHS[0]
            ] = "0" * 64
            failures = verify_saved_report(
                root=root,
                rom=rom,
                report=report,
                source_policy="captured",
            )
        self.assertIn(
            "captured source binding bundle SHA-256 mismatch",
            failures,
        )
        self.assertIn(
            "captured source binding changed during capture",
            failures,
        )

    def test_captured_source_policy_fails_closed_on_bad_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, rom, _capture = self.make_report(root)
            report["provenance"] = []
            failures = verify_saved_report(
                root=root,
                rom=rom,
                report=report,
                source_policy="captured",
            )
        self.assertIn("provenance ROM SHA-256 mismatch", failures)
        self.assertEqual(
            2, failures.count("captured source binding is missing")
        )

    def test_hash_verified_offline_recheck_reuses_live_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, rom, _capture = self.make_report(root)
            trace = root / "artifacts/traces/m4-audio.log"
            trace.parent.mkdir(parents=True)
            trace.write_text("> mfff000,48\n< 00\n", encoding="utf-8")
            trace_sha = hashlib.sha256(trace.read_bytes()).hexdigest()
            report["provenance"]["transcript"] = {
                "path": trace.relative_to(root).as_posix(),
                "sha256": trace_sha,
            }
            report["session"]["metadata"]["transcript_sha256"] = trace_sha
            report_path = root / "artifacts/reports/m4-audio.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(
                json.dumps(report), encoding="utf-8"
            )
            rechecked = recheck_existing_report(
                root=root,
                rom=rom,
                report_path=report_path,
                trace_path=trace,
                expected_sha256=hashlib.sha256(
                    rom.read_bytes()
                ).hexdigest(),
            )
        self.assertEqual(rechecked["status"], "pass")
        self.assertTrue(
            rechecked["provenance"]["offline_recheck"]["no_emulator_rerun"]
        )

    def test_public_session_reconstruction_fails_closed(self) -> None:
        with self.assertRaisesRegex(Exception, "session is incomplete"):
            reconstruct_public_session({}, [])

    def test_source_bundle_is_order_stable_and_change_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_source_tree(root)
            first = source_binding(root)
            second = source_binding(root)
            self.assertEqual(first, second)
            path = root / SOURCE_BINDING_PATHS[-1]
            path.write_text("; changed\n", encoding="utf-8")
            third = source_binding(root)
        self.assertNotEqual(
            first["bundle_sha256"], third["bundle_sha256"]
        )


class AudioProfileTests(unittest.TestCase):
    def test_profile_binds_owned_m4_capture_path(self) -> None:
        text = (
            "bindings {\n"
            "\tkeys {\n"
            "\t\tm ui.vgm_log\n"
            "\t}\n"
            "}\n"
            "ui {\n"
            "\tvgm_path $HOME\n"
            "\tvgm_template old.vgm\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "blastem.cfg"
            config.write_text(text, encoding="utf-8")
            capture = root / "artifacts/audio"
            evidence = configure_m4_audio_profile(config, capture)
            configured = config.read_text("utf-8")
        self.assertEqual(evidence["status"], "pass")
        self.assertIn(capture.resolve().as_posix(), configured)
        self.assertIn("recursion94-m4-audio-", configured)


if __name__ == "__main__":
    unittest.main()
