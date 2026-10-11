from __future__ import annotations

import copy
import socket
import tempfile
import unittest
from pathlib import Path

from tools.m2_evidence import (
    FIRST_RUNNING_VBLANK_ROLE,
    STARTUP_PRIMING_ROLE,
    DEBUG_BLOCK_BYTES,
    EvidenceFailure,
    EXPECTED_DEBUG_ABI_SIZE,
    EXPECTED_DEBUG_ABI_VERSION,
    EXPECTED_DEBUG_MAGIC,
    PRIMARY_GAMEPLAY_SPRITE_SIZE_CODE,
    audio_readiness_facts,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    controller_report,
    parse_debug_block,
    parse_sat_entry,
    parse_vasm_symbols,
    parse_z80_status,
    require_gdb_port_available,
    verify_default_bindings,
    verify_no_user_blastem_override,
    vblank_entry_accounting,
    video_dma_report,
    write_disconnected_profile,
    write_standard_pad_profile,
    z80_audio_runtime_report,
)


def set_u16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = value.to_bytes(2, "big")


def set_u32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = value.to_bytes(4, "big")


def debug_fixture(
    index: int,
    *,
    held: int = 0,
    pressed: int = 0,
    released: int = 0,
) -> dict[str, int | str]:
    raw = bytearray(DEBUG_BLOCK_BYTES)
    set_u32(raw, 0x00, EXPECTED_DEBUG_MAGIC)
    set_u16(raw, 0x04, EXPECTED_DEBUG_ABI_VERSION)
    set_u16(raw, 0x06, EXPECTED_DEBUG_ABI_SIZE)
    set_u32(raw, 0x08, 100 + index * 2)
    set_u32(raw, 0x0C, 50 + index)
    set_u32(raw, 0x10, 50 + index)
    set_u16(raw, 0x14, 50 + index)
    set_u16(raw, 0x18, 4)
    raw[0x1E] = held
    raw[0x1F] = pressed
    raw[0x20] = released
    raw[0x21] = 1
    set_u16(raw, 0x24, 1)
    set_u16(raw, 0x26, 1)
    set_u16(raw, 0x28, 320)
    set_u16(raw, 0x2A, 320)
    set_u16(raw, 0x2C, 0)
    set_u16(raw, 0x2E, 1)
    set_u16(raw, 0x34, 2)
    set_u16(raw, 0x36, 10 + index)
    set_u16(raw, 0x38, 1)
    set_u16(raw, 0x3A, 0x003F)
    set_u32(raw, 0x3C, 1000 + index)
    return parse_debug_block(bytes(raw))


def sat_fixture(x: int) -> dict[str, int | str]:
    raw = bytearray(8)
    set_u16(raw, 0x00, 200)
    set_u16(raw, 0x02, 0)
    set_u16(raw, 0x04, 1)
    set_u16(raw, 0x06, x)
    return parse_sat_entry(bytes(raw))


def z80_status_fixture(index: int) -> dict[str, int | str]:
    raw = bytearray(16)
    raw[0x00] = 0x94
    raw[0x01] = 0x01
    raw[0x02] = 0x02
    raw[0x03] = 0x07
    raw[0x04:0x06] = (10 + index).to_bytes(2, "little")
    raw[0x06] = 1
    raw[0x07] = 1
    raw[0x08:0x0A] = (1).to_bytes(2, "little")
    raw[0x0B] = 6
    raw[0x0C] = 2
    raw[0x0D] = index
    raw[0x0E] = 1
    return parse_z80_status(bytes(raw))


def sample_fixture() -> list[dict[str, object]]:
    phases = [
        ("baseline", 0, 0, 0, 200),
        ("right_first", 0x08, 0x08, 0, 202),
        ("right_second", 0x08, 0, 0, 204),
        ("right_release", 0, 0, 0x08, 204),
        ("left_right_neutral", 0, 0, 0, 204),
        ("horizontal_release", 0, 0, 0, 204),
        ("up_down_neutral", 0, 0, 0, 204),
        ("final_neutral", 0, 0, 0, 204),
    ]
    return [
        {
            "label": label,
            "debug": debug_fixture(
                index, held=held, pressed=pressed, released=released
            ),
            "sat": sat_fixture(x),
            "audio_shadow": z80_status_fixture(index),
        }
        for index, (label, held, pressed, released, x) in enumerate(phases)
    ]


class DebugAbiParserTests(unittest.TestCase):
    def test_parser_decodes_all_m2_fields_big_endian(self) -> None:
        decoded = debug_fixture(3, held=0x08, pressed=0x08, released=0x04)

        self.assertEqual(decoded["magic_ascii"], "R94D")
        self.assertEqual(decoded["sequence"], 106)
        self.assertEqual(decoded["input_held"], 0x08)
        self.assertEqual(decoded["input_pressed"], 0x08)
        self.assertEqual(decoded["input_released"], 0x04)
        self.assertEqual(decoded["sprite_count"], 1)
        self.assertEqual(decoded["dma_words_frame"], 320)
        self.assertEqual(decoded["z80_heartbeat"], 13)
        self.assertEqual(decoded["controller_samples"], 1003)
        self.assertEqual(decoded["dma_deferred_count"], 0)
        self.assertEqual(decoded["dma_required_drop_count"], 0)
        self.assertEqual(decoded["dma_invalid_count"], 0)

    def test_short_debug_block_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 72 required"):
            parse_debug_block(bytes(DEBUG_BLOCK_BYTES - 1))

    def test_sat_entry_decodes_terminator_and_coordinates(self) -> None:
        decoded = sat_fixture(234)
        self.assertEqual(decoded["link"], 0)
        self.assertEqual(decoded["size_code"], 0)
        self.assertEqual(decoded["x"], 234)
        self.assertEqual(decoded["y"], 200)

    def test_z80_status_uses_little_endian_counters(self) -> None:
        decoded = z80_status_fixture(3)
        self.assertEqual(decoded["signature"], 0x94)
        self.assertEqual(decoded["state"], 2)
        self.assertEqual(decoded["heartbeat"], 13)
        self.assertEqual(decoded["processed_commands"], 1)
        self.assertEqual(decoded["ym_write_count"], 6)
        self.assertEqual(decoded["psg_write_count"], 2)


class M2ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.samples = sample_fixture()
        self.disconnected_samples = copy.deepcopy(self.samples[:2])
        for index, sample in enumerate(self.disconnected_samples):
            sample["label"] = f"disconnected_{index}"
            sample["debug"]["input_connected"] = 0
            sample["debug"]["input_held"] = 0
            sample["debug"]["input_pressed"] = 0
            sample["debug"]["input_released"] = 0
            sample["debug"]["input_start_held"] = 0
        entry_accounting = vblank_entry_accounting([3, 4, 4, 4])
        self.session = {
            "status": "pass",
            "process_id": 42,
            "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
            "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
            "counters_and_controller_poll_begin": (
                FIRST_RUNNING_VBLANK_ROLE
            ),
            "vblank_entry_accounting": entry_accounting,
            "disconnected_vblank_entry_accounting": copy.deepcopy(
                entry_accounting
            ),
        }
        self.rom_sha = "ab" * 32
        self.injection = {
            "status": "pass",
            "detail": "targeted window messages injected",
        }
        self.disconnect_config = {
            "status": "pass",
            "detail": "port 1 omitted from isolated config",
        }
        self.connected_config = {
            "status": "pass",
            "detail": "standard pad",
            "port_1_assignment": "gamepad3.1",
        }

    def test_controller_fixture_passes_all_edges_and_neutralization(self) -> None:
        report = controller_report(
            samples=self.samples,
            disconnected_samples=self.disconnected_samples,
            rom_sha256=self.rom_sha,
            session=self.session,
            injection=self.injection,
            connected_config=self.connected_config,
            disconnect_config=self.disconnect_config,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["rom_sha256"], self.rom_sha)
        self.assertTrue(all(item["status"] == "pass" for item in report["checks"]))

    def test_controller_missing_pressed_edge_fails(self) -> None:
        samples = copy.deepcopy(self.samples)
        samples[1]["debug"]["input_pressed"] = 0
        report = controller_report(
            samples=samples,
            disconnected_samples=self.disconnected_samples,
            rom_sha256=self.rom_sha,
            session=self.session,
            injection=self.injection,
            connected_config=self.connected_config,
            disconnect_config=self.disconnect_config,
        )
        self.assertEqual(report["status"], "fail")
        failed = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("right_first_edge", failed)

    def test_missing_startup_priming_metadata_fails_closed(self) -> None:
        session = copy.deepcopy(self.session)
        session["initial_breakpoint_role"] = "running_full_vblank"
        report = controller_report(
            samples=self.samples,
            disconnected_samples=self.disconnected_samples,
            rom_sha256=self.rom_sha,
            session=session,
            injection=self.injection,
            connected_config=self.connected_config,
            disconnect_config=self.disconnect_config,
        )
        self.assertEqual(report["status"], "fail")
        failed = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "startup_priming_and_running_vblank_origin", failed
        )

    def test_video_dma_fixture_checks_sat_movement_and_budget(self) -> None:
        report = video_dma_report(
            samples=self.samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(report["status"], "pass")

    def test_video_dma_accepts_bounded_multi_sprite_gameplay_scene(self) -> None:
        samples = copy.deepcopy(self.samples)
        for sample in samples:
            sample["debug"]["sprite_count"] = 9
            sample["debug"]["sprite_scanline_max"] = 5
            sample["debug"]["dma_words_frame"] = 440
            sample["debug"]["dma_words_max"] = 440
            sample["debug"]["dma_queue_high_water"] = 4
            sample["sat"]["size_code"] = PRIMARY_GAMEPLAY_SPRITE_SIZE_CODE
            sample["sat"]["link"] = 1

        report = video_dma_report(
            samples=samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )

        self.assertEqual(report["status"], "pass")

        samples[-1]["sat"]["size_code"] = 0
        failing = video_dma_report(
            samples=samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(failing["status"], "fail")

    def test_video_dma_overflow_is_not_promoted(self) -> None:
        samples = copy.deepcopy(self.samples)
        samples[-1]["debug"]["dma_overflow_count"] = 1
        report = video_dma_report(
            samples=samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(report["status"], "fail")

    def test_video_dma_required_drop_and_invalid_entry_fail(self) -> None:
        for field in ("dma_required_drop_count", "dma_invalid_count"):
            with self.subTest(field=field):
                samples = copy.deepcopy(self.samples)
                samples[-1]["debug"][field] = 1
                report = video_dma_report(
                    samples=samples,
                    rom_sha256=self.rom_sha,
                    session=self.session,
                )
                self.assertEqual(report["status"], "fail")

    def test_video_dma_deferral_counter_is_reported_and_monotonic(self) -> None:
        samples = copy.deepcopy(self.samples)
        for index, sample in enumerate(samples):
            sample["debug"]["dma_deferred_count"] = index // 3
        passing = video_dma_report(
            samples=samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(passing["status"], "pass")

        samples[-1]["debug"]["dma_deferred_count"] = 0
        failing = video_dma_report(
            samples=samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(failing["status"], "fail")

    def test_z80_audio_fixture_requires_advancing_heartbeat(self) -> None:
        passing = z80_audio_runtime_report(
            samples=self.samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(passing["status"], "pass")

        samples = copy.deepcopy(self.samples)
        for sample in samples:
            sample["debug"]["z80_heartbeat"] = 7
        failing = z80_audio_runtime_report(
            samples=samples,
            rom_sha256=self.rom_sha,
            session=self.session,
        )
        self.assertEqual(failing["status"], "fail")


class HarnessContractTests(unittest.TestCase):
    def test_vblank_entry_accounting_excludes_stage_3_from_frames(self) -> None:
        accounting = vblank_entry_accounting([3, 4, 4, 4])
        self.assertEqual(accounting["status"], "pass")
        self.assertEqual(accounting["observed_vblank_entry_stops"], 4)
        self.assertEqual(accounting["startup_ack_only_entry_stops"], 1)
        self.assertEqual(accounting["running_full_entry_stops"], 3)

        invalid = vblank_entry_accounting([4, 4, 4])
        self.assertEqual(invalid["status"], "fail")

    def test_default_binding_preflight_is_hash_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "default.cfg"
            path.write_text(
                "\n".join(
                    (
                        "up gamepads.1.up",
                        "down gamepads.1.down",
                        "left gamepads.1.left",
                        "right gamepads.1.right",
                    )
                ),
                encoding="utf-8",
            )
            evidence = verify_default_bindings(path)

        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(len(evidence["sha256"]), 64)

    def test_vasm_symbol_map_finds_breakpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "debug.sym"
            path.write_text(
                "000003B6 VBlankInterrupt\n00000200 ResetEntry\n",
                encoding="utf-8",
            )
            symbols = parse_vasm_symbols(path)

        self.assertEqual(symbols["VBlankInterrupt"], 0x3B6)

    def test_disconnected_profile_uses_explicit_none_to_avoid_default(self) -> None:
        source = (
            "io {\n"
            "\tdevices {\n"
            "\t\t1 gamepad6.1\n"
            "\t\t2 gamepad6.2\n"
            "\t}\n"
            "}\n"
            "ea_multitap {\n"
            "\t1 gamepad6.1\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            default = root / "default.cfg"
            default.write_text(source, encoding="utf-8")
            evidence = write_disconnected_profile(default, root / "profile")
            modified = Path(evidence["path"]).read_text("utf-8")

        self.assertEqual(evidence["status"], "pass")
        self.assertNotIn("\t\t1 gamepad6.1", modified)
        self.assertIn("\t\t1 none", modified)
        self.assertIn("\t1 gamepad6.1", modified)

    def test_audio_readiness_rejects_early_baseline(self) -> None:
        sample = sample_fixture()[0]
        self.assertTrue(audio_readiness_facts(sample)["ready"])
        sample["audio_shadow"]["ym_write_count"] = 0
        self.assertFalse(audio_readiness_facts(sample)["ready"])

    def test_connected_profile_forces_standard_three_button_pad(self) -> None:
        source = (
            "io {\n"
            "\tdevices {\n"
            "\t\t1 gamepad6.1\n"
            "\t\t2 gamepad6.2\n"
            "\t}\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            default = root / "default.cfg"
            default.write_text(source, encoding="utf-8")
            evidence = write_standard_pad_profile(
                default, root / "profile"
            )
            modified = Path(evidence["path"]).read_text("utf-8")
        self.assertEqual(evidence["port_1_assignment"], "gamepad3.1")
        self.assertIn("1 gamepad3.1", modified)

    def test_occupied_gdb_port_is_rejected_before_launch(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            with self.assertRaisesRegex(
                RuntimeError, "refusing to attach to an unowned process"
            ):
                require_gdb_port_available(
                    "127.0.0.1", listener.getsockname()[1]
                )
        finally:
            listener.close()

    def test_portable_runtime_redirects_every_writable_path(self) -> None:
        default = (
            "ui {\n"
            "\tscreenshot_path $HOME\n"
            "\tvgm_path $HOME\n"
            "\tsave_path $USERDATA/blastem/$ROMNAME\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pinned = root / "pinned"
            pinned.mkdir()
            (pinned / "blastem.exe").write_bytes(b"pinned executable")
            (pinned / "default.cfg").write_text(
                default, encoding="utf-8"
            )
            runtime, clone = clone_blastem_runtime(
                pinned, root / "sandbox"
            )
            config = runtime / "blastem.cfg"
            config.write_text(default, encoding="utf-8")
            paths = apply_blastem_session_isolation(
                config, root / "owned-data"
            )
            modified = config.read_text("utf-8")

        self.assertEqual(clone["status"], "pass")
        self.assertEqual(len(clone["executable_sha256"]), 64)
        self.assertIn("config_in_exe_dir on", modified)
        self.assertIn(paths["save_path"], modified)
        self.assertIn(paths["screenshot_path"], modified)
        self.assertIn(paths["vgm_path"], modified)

    def test_real_user_override_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary)
            evidence = verify_no_user_blastem_override(local)
            self.assertTrue(evidence["override_absent"])
            override = local / "blastem/blastem.cfg"
            override.parent.mkdir()
            override.write_text("user data", encoding="utf-8")
            with self.assertRaisesRegex(
                EvidenceFailure, "will not be read or overwritten"
            ):
                verify_no_user_blastem_override(local)
            self.assertEqual(override.read_text("utf-8"), "user data")


if __name__ == "__main__":
    unittest.main()
