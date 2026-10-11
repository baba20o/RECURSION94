from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from tools.m4_cycle_evidence import (
    BOOT_STAGE_RUNNING,
    MAIN_RELEASE_LIMIT_CPU_CYCLES,
    NTSC_FRAME_CPU_CYCLES,
    REPORT_SCHEMA,
    RESET_TRANSITION_LIMIT_CPU_CYCLES,
    SESSION_LAYOUT,
    analyse_main_records,
    analyse_projection_records,
    analyse_vblank_records,
    build_command_script,
    build_report,
    command_script_errors,
    _decode_supported_call,
    derive_pc_contract,
    report_contract_errors,
)
from tools.m2_evidence import EvidenceFailure, parse_vasm_symbols


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64


def synthetic_legacy_jsr_fixture() -> tuple[bytes, dict[str, int]]:
    """Invented legacy-loop opcode fixture, never a ROM or timing capture.

    Current M5 reset-token coalescing intentionally no longer has the legacy
    main-start signature. Test that old decoder without relabelling a current
    M5 binary as compatible historical evidence.
    """
    symbols = {
        "MainLoop": 0x100,
        "MainLoopGameplayStepCall": 0x110,
        "MainLoopPostGameplayStepCall": 0x114,
        "MainLoopCommitResetCall": 0x120,
        "MainLoopPostCommitReset": 0x124,
        "MainLoopPostGameplayStep": 0x140,
        "MainLoopWaitForVBlank": 0x180,
        "VBlankInterrupt": 0x400,
        "GameplayStep": 0x1000,
        "GameplayCommitAndReset": 0x1500,
        "GameplayRendererBuildAndQueue": 0x1800,
        "EngineBuildProjection": 0x1A00,
        "DEBUG_FRAME_TOKEN": 0xFFF014,
        "DEBUG_FRAME_COUNTER": 0xFFF010,
        "DEBUG_BOOT_STAGE": 0xFFF018,
        "ENGINE_TICK": 0xFF1410,
        "ENGINE_ATTEMPT": 0xFF1412,
        "ENGINE_CHECKPOINT_COUNT": 0xFF1420,
        "TIMELINE_ATTEMPT_LENGTH": 0xFF7000,
    }
    image = bytearray(0x2000)
    image[0x100:0x10A] = bytes.fromhex("537900FFF01446FC2500")
    for call, target in (
        (0x110, 0x1000),
        (0x120, 0x1500),
        (0x1010, 0x1A00),
        (0x1020, 0x1A00),
        (0x1030, 0x1A00),
    ):
        image[call:call + 4] = bytes.fromhex("6100") + (
            target - (call + 2)
        ).to_bytes(2, "big", signed=True)
    image[0x140:0x146] = bytes.fromhex("4EB900001800")
    image[0x146:0x14A] = bytes.fromhex("6000") + (
        0x100 - (0x146 + 2)
    ).to_bytes(2, "big", signed=True)
    image[0x420:0x422] = bytes.fromhex("4E73")
    image[0x1014:0x101A] = bytes.fromhex("427900FF1420")
    return bytes(image), symbols


def synthetic_main_records(
    crossing_index: int | None = None,
) -> tuple[list[dict], list[dict]]:
    main: list[dict] = []
    vblank: list[dict] = []
    for index in range(720):
        base = index * 1_000_000
        vblank_start = base + (20_000 if index == crossing_index else 100)
        vblank.extend(
            (
                {
                    "phase": "S",
                    "cycle": vblank_start,
                    "frame": index,
                    "attempt": 0,
                },
                {
                    "phase": "E",
                    "cycle": vblank_start + 70,
                    "frame": index + 1,
                    "attempt": 0,
                },
            )
        )
        start_frame = index + 1
        end_frame = index + 1
        renderer_cycle = base + 71_000
        end_cycle = base + 78_000
        if index == crossing_index:
            start_frame = index
            renderer_cycle += 1
            end_cycle += 1
            end_frame = index + 1
        checkpoint_start = 1 + index // 60
        start = {
            "phase": "S",
            "cycle": base + 1_000,
            "frame": start_frame,
            "tick": index,
            "attempt": 0,
            "checkpoints": checkpoint_start,
            "attempt_length": index,
        }
        if index < 719:
            checkpoint_end = 1 + (index + 1) // 60
            renderer = {
                "phase": "R",
                "cycle": renderer_cycle,
                "frame": end_frame,
                "tick": index + 1,
                "attempt": 0,
                "checkpoints": checkpoint_end,
                "attempt_length": index + 1,
            }
            end = {**renderer, "phase": "E", "cycle": end_cycle}
            main.extend((start, renderer, end))
            continue

        gameplay_end = {
            "phase": "G",
            "cycle": base + 71_000,
            "frame": end_frame,
            "tick": 720,
            "attempt": 0,
            "checkpoints": 13,
            "attempt_length": 720,
        }
        reset_start = {
            **gameplay_end,
            "phase": "C",
            "cycle": base + 71_070,
        }
        reset_end = {
            "phase": "D",
            "cycle": base + 211_070,
            "frame": end_frame,
            "tick": 0,
            "attempt": 1,
            "checkpoints": 1,
            "attempt_length": 0,
        }
        renderer = {
            **reset_end,
            "phase": "R",
            "cycle": base + 211_140,
        }
        end = {
            **reset_end,
            "phase": "E",
            "cycle": base + 218_140,
        }
        main.extend(
            (start, gameplay_end, reset_start, reset_end, renderer, end)
        )
    return main, vblank


def synthetic_vblank_records() -> list[dict]:
    records: list[dict] = []
    for index in range(720):
        attempt = 1 if index == 719 else 0
        records.extend(
            (
                {
                    "phase": "S",
                    "cycle": index * 1_000 + 100,
                    "frame": index,
                    "attempt": 0,
                },
                {
                    "phase": "E",
                    "cycle": index * 1_000 + 170,
                    "frame": index + 1,
                    "attempt": attempt,
                },
            )
        )
    return records


def digest(records: list[dict]) -> str:
    payload = json.dumps(
        records, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def synthetic_session(
    session_id: str,
    mode: str,
    records: list[dict],
    interrupt_records: list[dict],
    measurement: dict,
) -> dict:
    command_script = "continue\n"
    return {
        "session_id": session_id,
        "mode": mode,
        "status": measurement["status"],
        "started_utc": "2026-01-01T00:00:00+00:00",
        "completed_utc": "2026-01-01T00:00:01+00:00",
        "process_id": 94,
        "launch_flags": ["-d", "-g", "-m", "gen", "-r", "U"],
        "debugger_transport": "native_win32_console",
        "gdb_port_opened": False,
        "command_script": command_script,
        "command_script_sha256": hashlib.sha256(
            command_script.encode("utf-8")
        ).hexdigest(),
        "command_policy_errors": [],
        "console_sha256": SHA,
        "console_policy_errors": [],
        "console_geometry": {"width": 120, "height": 9001},
        "script_input_records": 1,
        "quit_input_records": 5,
        "records": records,
        "records_sha256": digest(records),
        "interrupt_records": interrupt_records,
        "interrupt_records_sha256": digest(interrupt_records),
        "measurement": measurement,
        "isolated_runtime": {},
        "isolated_config": {"sha256": SHA},
    }


def synthetic_report() -> dict:
    main_records, embedded = synthetic_main_records()
    vblank_records = synthetic_vblank_records()
    main_measurement = analyse_main_records(main_records, embedded)
    vblank_measurement = analyse_vblank_records(vblank_records)
    sessions = []
    for session_id, mode in SESSION_LAYOUT:
        if mode == "main":
            sessions.append(
                synthetic_session(
                    session_id,
                    mode,
                    copy.deepcopy(main_records),
                    copy.deepcopy(embedded),
                    copy.deepcopy(main_measurement),
                )
            )
        else:
            sessions.append(
                synthetic_session(
                    session_id,
                    mode,
                    copy.deepcopy(vblank_records),
                    [],
                    copy.deepcopy(vblank_measurement),
                )
            )
    provenance = {
        name: {"path": f"{name}.bin", "sha256": SHA, "bytes": 1}
        for name in (
            "rom",
            "emulator",
            "default_config",
            "symbols",
            "collector",
            "schema",
            "launcher",
            "toolchain_lock",
            "trace",
        )
    }
    provenance["platform"] = {
        "os_name": "nt",
        "platform": "test",
        "python": "test",
    }
    provenance["session_config_sha256"] = {
        session_id: SHA for session_id, _mode in SESSION_LAYOUT
    }
    pc_contract = {
        "addresses": {
            name: index * 2
            for index, name in enumerate(
                (
                    "main_start",
                    "terminal_step_end",
                    "reset_start",
                    "reset_end",
                    "main_renderer",
                    "main_end",
                    "vblank_entry",
                    "vblank_rte",
                    "projection_entry",
                    "projection_checkpoint_return",
                ),
                start=1,
            )
        },
        "main_start_derivation": {},
        "main_end_derivation": {},
        "terminal_reset_derivation": {},
        "vblank_derivation": {},
        "projection_derivation": {},
        "rom_bytes": {
            str(index): "4E71" for index in range(10)
        },
    }
    return build_report(
        rom_sha256=SHA,
        sessions=sessions,
        pc_contract=pc_contract,
        provenance=provenance,
        limitations=["synthetic"],
    )


class MainAnalysisTests(unittest.TestCase):
    def test_clean_fixture_passes_all_scoped_budgets(self) -> None:
        records, embedded = synthetic_main_records()
        measurement = analyse_main_records(records, embedded)
        self.assertEqual("pass", measurement["status"])
        self.assertEqual(
            "pass",
            measurement["segments"]["simulation"]["categories"][
                "checkpoint"
            ]["status"],
        )
        self.assertEqual(
            "observed",
            measurement["segments"]["simulation"]["categories"][
                "terminal_reset"
            ]["status"],
        )
        self.assertEqual(
            RESET_TRANSITION_LIMIT_CPU_CYCLES,
            measurement["terminal_transition"]["reset_transition"][
                "limit_cpu_cycles"
            ],
        )

    def test_embedded_vblank_body_is_subtracted_without_hiding_raw(self) -> None:
        records, embedded = synthetic_main_records(crossing_index=10)
        measurement = analyse_main_records(records, embedded)
        interval = measurement["intervals"][10]["simulation"]
        self.assertEqual(70_001, interval["raw_delta_master"])
        self.assertEqual(70, interval["vblank_body_master_subtracted"])
        self.assertEqual(69_931, interval["delta_master"])
        self.assertEqual(1, interval["divider_remainder"])
        self.assertEqual(9_991, interval["delta_cpu_cycles"])
        self.assertEqual(1, measurement["interrupt_inside_interval_count"])
        self.assertEqual("pass", measurement["status"])

    def test_terminal_reset_has_independent_two_frame_gate(self) -> None:
        records, embedded = synthetic_main_records()
        reset_start = records[-4]
        reset_end = records[-3]
        reset_cycles = RESET_TRANSITION_LIMIT_CPU_CYCLES + 1
        reset_end["cycle"] = reset_start["cycle"] + reset_cycles * 7
        records[-2]["cycle"] = reset_end["cycle"] + 70
        records[-1]["cycle"] = reset_end["cycle"] + 7_070
        measurement = analyse_main_records(records, embedded)
        self.assertEqual("fail", measurement["status"])
        self.assertEqual(
            "fail",
            measurement["terminal_transition"]["reset_transition"][
                "status"
            ],
        )
        self.assertEqual(
            reset_cycles,
            measurement["terminal_transition"]["reset_transition"][
                "delta_cpu_cycles"
            ],
        )

    def test_terminal_marker_omission_fails_closed(self) -> None:
        records, embedded = synthetic_main_records()
        del records[-5]
        with self.assertRaisesRegex(ValueError, "marker stream"):
            analyse_main_records(records, embedded)


class OtherAnalysisTests(unittest.TestCase):
    def test_vblank_conservative_cost_passes(self) -> None:
        measurement = analyse_vblank_records(synthetic_vblank_records())
        self.assertEqual("pass", measurement["status"])
        self.assertEqual(74, measurement["max_conservative_interrupt_cpu_cycles"])

    def test_pre_running_ack_pair_is_not_accepted_as_running_work(self) -> None:
        records = synthetic_vblank_records()
        records[0:0] = [
            {
                "phase": "S",
                "cycle": 10,
                "frame": 0,
                "attempt": 0,
            },
            {
                "phase": "E",
                "cycle": 80,
                "frame": 0,
                "attempt": 0,
            },
        ]
        with self.assertRaisesRegex(
            ValueError, "frame counter did not advance once"
        ):
            analyse_vblank_records(records)

    def test_projection_scope_includes_caller_return(self) -> None:
        records = [
            {
                "phase": "S",
                "cycle": 700,
                "frame": 60,
                "tick": 60,
                "attempt": 0,
                "checkpoints": 1,
            },
            {
                "phase": "E",
                "cycle": 700 + 48_000 * 7,
                "frame": 60,
                "tick": 60,
                "attempt": 0,
                "checkpoints": 2,
            },
        ]
        measurement = analyse_projection_records(records)
        self.assertEqual(48_000, measurement["delta_cpu_cycles"])
        self.assertEqual("pass", measurement["status"])
        self.assertIn("after rts", measurement["scope"])

    def test_projection_script_filters_both_early_breakpoints(self) -> None:
        symbols = {
            "DEBUG_FRAME_COUNTER": 0x100,
            "ENGINE_TICK": 0x104,
            "ENGINE_ATTEMPT": 0x106,
            "ENGINE_CHECKPOINT_COUNT": 0x108,
            "TIMELINE_ATTEMPT_LENGTH": 0x10A,
        }
        contract = {
            "addresses": {
                "projection_entry": 0x200,
                "projection_checkpoint_return": 0x300,
            }
        }
        script = build_command_script(
            "projection", contract, symbols
        )
        self.assertEqual(
            2, script.count("if [$104].w < $3C")
        )
        self.assertEqual(
            [],
            command_script_errors(
                script, "projection", contract, symbols
            ),
        )


class ContractTests(unittest.TestCase):
    def test_synthetic_legacy_absolute_renderer_call_is_strictly_derived(self) -> None:
        rom, symbols = synthetic_legacy_jsr_fixture()
        contract = derive_pc_contract(rom, symbols)
        derivation = contract["main_end_derivation"]
        renderer_call = symbols["MainLoopPostGameplayStep"]

        self.assertEqual("jsr_abs_l", derivation["renderer_call_kind"])
        self.assertEqual(6, derivation["renderer_call_bytes"])
        self.assertEqual(
            symbols["GameplayRendererBuildAndQueue"],
            derivation["renderer_target"],
        )
        self.assertEqual(
            renderer_call + 6,
            contract["addresses"]["main_end"],
        )
        self.assertEqual(
            rom[renderer_call : renderer_call + 6].hex().upper(),
            derivation["call_hex"],
        )

    def test_duplicate_renderer_target_call_fails_closed(self) -> None:
        fixture, symbols = synthetic_legacy_jsr_fixture()
        rom = bytearray(fixture)
        contract = derive_pc_contract(bytes(rom), symbols)
        duplicate = contract["addresses"]["main_start"] + 10
        renderer = symbols["MainLoopPostGameplayStep"]
        rom[duplicate : duplicate + 6] = rom[renderer : renderer + 6]

        with self.assertRaisesRegex(
            EvidenceFailure, "exactly one supported call opcode"
        ):
            derive_pc_contract(bytes(rom), symbols)

    def test_current_m5_loop_is_not_accepted_as_legacy_timing_evidence(self) -> None:
        rom = (ROOT / "build/recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(ROOT / "build/recursion94-debug.sym")
        with self.assertRaisesRegex(
            EvidenceFailure, "token-decrement/start signature is not unique"
        ):
            derive_pc_contract(rom, symbols)

    def test_archived_m4_bsr_renderer_call_remains_supported(self) -> None:
        archive = (
            ROOT
            / "artifacts/archive/"
            "m4-frozen-roms-c689adf9-0cf94ab5/"
            "recursion94-debug.bin"
        )
        report = json.loads(
            (ROOT / "artifacts/reports/m4-cycle-evidence.json").read_text(
                encoding="utf-8"
            )
        )
        derivation = report["pc_contract"]["main_end_derivation"]
        renderer = derivation["renderer_call_symbol"]
        rom = archive.read_bytes()
        decoded = _decode_supported_call(rom, renderer)

        self.assertEqual("6100", rom[renderer : renderer + 2].hex().upper())
        self.assertEqual(("bsr.w", 0x5274, 4), decoded)
        self.assertEqual(
            derivation["call_hex"],
            rom[renderer : renderer + 4].hex().upper(),
        )

    def test_report_recomputes_without_errors(self) -> None:
        report = synthetic_report()
        self.assertEqual("pass", report["status"])
        self.assertEqual([], report_contract_errors(report))
        self.assertEqual(9, len(report["checks"]))

    def test_measurement_tamper_is_rejected(self) -> None:
        report = synthetic_report()
        report["sessions"][0]["measurement"]["intervals"][0][
            "simulation"
        ]["delta_cpu_cycles"] += 1
        errors = report_contract_errors(report)
        self.assertTrue(
            any("measurement derivation mismatch" in error for error in errors)
        )

    def test_budget_tamper_is_rejected(self) -> None:
        report = synthetic_report()
        report["budgets"]["main_release_limit_cpu_cycles"] += 1
        self.assertIn("budget contract mismatch", report_contract_errors(report))

    def test_boot_stage_semantics_tamper_is_rejected(self) -> None:
        report = synthetic_report()
        report["native_debugger_semantics"][
            "vblank_marker_boot_stage_value"
        ] = BOOT_STAGE_RUNNING - 1
        self.assertIn(
            "native debugger semantics contract mismatch",
            report_contract_errors(report),
        )

    def test_schema_declares_current_exact_contract(self) -> None:
        schema = json.loads(
            (ROOT / "tools/report_schemas/m4-cycle-evidence.schema.json")
            .read_text("utf-8")
        )
        self.assertEqual(
            REPORT_SCHEMA, schema["properties"]["schema"]["const"]
        )
        self.assertEqual(
            RESET_TRANSITION_LIMIT_CPU_CYCLES,
            schema["properties"]["budgets"]["properties"][
                "reset_transition_limit_cpu_cycles"
            ]["const"],
        )
        self.assertEqual(9, schema["properties"]["checks"]["minItems"])
        self.assertEqual(
            BOOT_STAGE_RUNNING,
            schema["properties"]["native_debugger_semantics"][
                "properties"
            ]["vblank_marker_boot_stage_value"]["const"],
        )

    def test_vblank_markers_use_identical_running_stage_filters(self) -> None:
        symbols = {
            "DEBUG_FRAME_COUNTER": 0xFFF010,
            "DEBUG_BOOT_STAGE": 0xFFF018,
            "ENGINE_TICK": 0xFF1410,
            "ENGINE_ATTEMPT": 0xFF140E,
            "ENGINE_CHECKPOINT_COUNT": 0xFF1420,
            "TIMELINE_ATTEMPT_LENGTH": 0xFF13FC,
        }
        addresses = {
            "main_start": 0x200,
            "terminal_step_end": 0x220,
            "reset_start": 0x230,
            "reset_end": 0x234,
            "main_renderer": 0x240,
            "main_end": 0x244,
            "vblank_entry": 0x300,
            "vblank_rte": 0x380,
        }
        contract = {"addresses": addresses}
        condition = (
            f"[$FFF018].w = ${BOOT_STAGE_RUNNING:X}"
        )
        for mode, indices in (
            ("main", (6, 7)),
            ("vblank", (0, 1)),
        ):
            script = build_command_script(mode, contract, symbols)
            self.assertEqual(
                [],
                command_script_errors(
                    script, mode, contract, symbols
                ),
            )
            self.assertEqual(
                [
                    f"condition {indices[0]} {condition}",
                    f"condition {indices[1]} {condition}",
                ],
                [
                    line
                    for line in script.splitlines()
                    if line.startswith("condition ")
                    and condition in line
                ],
            )
            self.assertIn(
                f"commands {indices[0]}\nprintf \"R94C V S",
                script,
            )
            self.assertIn(
                f"commands {indices[1]}\nprintf \"R94C V E",
                script,
            )
            self.assertNotIn("\nset ", script.lower())
        self.assertNotEqual(3, BOOT_STAGE_RUNNING)

    def test_main_script_is_read_only_and_embeds_vblank_markers(self) -> None:
        symbols = {
            "DEBUG_FRAME_COUNTER": 0xFFF010,
            "DEBUG_BOOT_STAGE": 0xFFF018,
            "ENGINE_TICK": 0xFF1410,
            "ENGINE_ATTEMPT": 0xFF140E,
            "ENGINE_CHECKPOINT_COUNT": 0xFF1420,
            "TIMELINE_ATTEMPT_LENGTH": 0xFF13FC,
        }
        contract = {
            "addresses": {
                "main_start": 0x200,
                "terminal_step_end": 0x220,
                "reset_start": 0x230,
                "reset_end": 0x234,
                "main_renderer": 0x240,
                "main_end": 0x244,
                "vblank_entry": 0x300,
                "vblank_rte": 0x380,
            }
        }
        script = build_command_script("main", contract, symbols)
        self.assertEqual(
            [], command_script_errors(script, "main", contract, symbols)
        )
        self.assertIn("R94C V S", script)
        self.assertIn("R94C M C", script)
        self.assertNotIn("\nset ", script.lower())


if __name__ == "__main__":
    unittest.main()
