from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import m4_visual_evidence as m4_visual
from tools import m5_b03_runtime_evidence as m5_b03
from tools import m5_runtime_evidence as m5_b01
from tools import m5_visual_evidence as m5_visual
from tools.rom_tools.rom_image import validate_rom
from tests.emulator.test_m4_budget_evidence import (
    synthetic_provenance,
    synthetic_session,
)
from tests.emulator.test_m4_cycle_evidence import (
    synthetic_main_records,
    synthetic_session as synthetic_cycle_session,
    synthetic_vblank_records,
)
from tools.m2_evidence import parse_vasm_symbols
from tools.m3_runtime_evidence import verify_controller_bindings
from tools.m4_budget_evidence import (
    COLLECTOR_RELATIVE_PATH,
    DEFAULT_CONFIG_RELATIVE_PATH,
    EMULATOR_RELATIVE_PATH,
    EXPECTED_STATE_SYMBOLS,
    FORBIDDEN_BREAKPOINT_SYMBOLS,
    REQUIRED_SYMBOLS as BUDGET_REQUIRED_SYMBOLS,
    ROM_RELATIVE_PATH,
    SCHEMA_RELATIVE_PATH,
    SYMBOL_MAP_RELATIVE_PATH,
    TERMINAL_BREAKPOINT_SYMBOL,
    TRACE_RELATIVE_PATH,
    analyze_transcript,
    build_budget_report,
)
from tools.m4_cycle_evidence import (
    BLASTEM_REVISION as CYCLE_BLASTEM_REVISION,
    BLASTEM_VERSION as CYCLE_BLASTEM_VERSION,
    COLLECTOR_RELATIVE_PATH as CYCLE_COLLECTOR_PATH,
    DEFAULT_REPORT_RELATIVE_PATH as CYCLE_REPORT_PATH,
    DEFAULT_TRACE_RELATIVE_PATH as CYCLE_TRACE_PATH,
    LAUNCHER_RELATIVE_PATH as CYCLE_LAUNCHER_PATH,
    SCHEMA_RELATIVE_PATH as CYCLE_SCHEMA_PATH,
    SESSION_LAYOUT as CYCLE_SESSION_LAYOUT,
    _public_session as cycle_public_session,
    _records_digest as cycle_records_digest,
    _trace_text as cycle_trace_text,
    analyse_main_records,
    analyse_vblank_records,
    build_command_script as build_cycle_command_script,
    build_report as build_cycle_report,
    command_script_errors as cycle_command_script_errors,
    console_transcript_errors as cycle_console_errors,
    derive_pc_contract,
)
from tools.validation_report import (
    COMMAND_EXIT_NAMES,
    FROZEN_M4_ARCHIVE,
    FROZEN_M4_DEBUG_SHA256,
    FROZEN_PRIOR_REPORTS,
    HOST_TEST_BINARY_CONFIGURATIONS,
    HOST_TEST_SUITE_PATHS,
    M5_VISUAL_CAPTURE_LABELS,
    M2_EVIDENCE_REPORTS,
    M3_EVIDENCE_REPORTS,
    M4_EVIDENCE_REPORTS,
    VISUAL_RUBRIC_CATEGORIES,
    create_validation_report,
    emulator_failure_detail,
    host_tests_gate,
    m5_budget_gate,
    m5_visual_capture_gate,
    m5_visual_critic_gate,
)


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def gate(report: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in report["gates"] if item["gate"] == name)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_passing_host_tests_report(root: Path) -> None:
    binary_checks = []
    for configuration in HOST_TEST_BINARY_CONFIGURATIONS:
        binary_path = root / "build" / f"recursion94-{configuration}.bin"
        if not binary_path.is_file():
            raise AssertionError(
                f"host-test fixture binary is missing: {binary_path}"
            )
        binary_checks.append(
            {
                "configuration": configuration,
                "status": "pass",
                "sha256": file_sha256(binary_path),
                "gate_passed": True,
            }
        )
    write_json(
        root / "artifacts/reports/host-tests.json",
        {
            "schema": 1,
            "status": "pass",
            "requested_suite": "All",
            "runs": [
                {
                    "path": relative,
                    "tests_run": 1,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                    "status": "pass",
                }
                for relative in HOST_TEST_SUITE_PATHS
            ],
            "binary_checks": binary_checks,
            "pending_release_suites": {
                "Save": "known later release gate remains pending"
            },
            "release_gate_passed": False,
        },
    )


class EmulatorReportRegressionTests(unittest.TestCase):
    def test_one_check_failed_report_uses_available_detail(self) -> None:
        report = {
            "status": "fail",
            "checks": [{"status": "fail", "detail": "single bounded failure"}],
        }
        self.assertEqual(
            emulator_failure_detail(report),
            "single bounded failure",
        )

    def test_empty_or_malformed_checks_fall_back_to_status(self) -> None:
        self.assertEqual(
            emulator_failure_detail({"status": "fail", "checks": []}),
            "bounded emulator evidence status is fail",
        )
        self.assertEqual(
            emulator_failure_detail({"status": "fail", "checks": None}),
            "bounded emulator evidence status is fail",
        )

    def test_short_failed_report_does_not_break_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = Path(__file__).resolve().parents[2]
            for configuration in HOST_TEST_BINARY_CONFIGURATIONS:
                source = (
                    source_root
                    / "build"
                    / f"recursion94-{configuration}.bin"
                )
                destination = (
                    root / "build" / f"recursion94-{configuration}.bin"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            write_passing_host_tests_report(root)
            write_json(
                root / "artifacts/reports/emulator-smoke.json",
                {
                    "status": "fail",
                    "checks": [
                        {"status": "fail", "detail": "only available check"}
                    ],
                },
            )
            report, code = create_validation_report(
                root=root,
                scope="Milestone1",
                command_exits={
                    **{name: 0 for name in COMMAND_EXIT_NAMES},
                    "emulator_probe": 1,
                },
            )

        self.assertEqual(code, 2)
        bounded = gate(report, "bounded_headless_emulator")
        self.assertEqual(bounded["status"], "fail")
        self.assertFalse(bounded["required"])
        self.assertEqual(bounded["detail"], "only available check")


class Milestone2AggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_root = Path(__file__).resolve().parents[2]
        for configuration in HOST_TEST_BINARY_CONFIGURATIONS:
            source = (
                self.source_root
                / FROZEN_M4_ARCHIVE
                / f"recursion94-{configuration}.bin"
            )
            destination = (
                self.root / "build" / f"recursion94-{configuration}.bin"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        rom_path = self.root / "build/recursion94-debug.bin"
        rom = rom_path.read_bytes()
        self.rom_sha = hashlib.sha256(rom).hexdigest()
        self.assertEqual(FROZEN_M4_DEBUG_SHA256, self.rom_sha)
        write_passing_host_tests_report(self.root)
        self.exits = {name: 0 for name in COMMAND_EXIT_NAMES}
        write_json(
            self.root / "artifacts/reports/emulator-heartbeat.json",
            {"status": "pass", "rom_sha256": self.rom_sha},
        )
        write_json(
            self.root / "artifacts/reports/boot-screenshot.json",
            {"status": "pass", "rom_sha256": self.rom_sha},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_passing_m2_reports(self) -> None:
        for report_index, (_gate_name, filename) in enumerate(M2_EVIDENCE_REPORTS):
            write_json(
                self.root / "artifacts/reports" / filename,
                {
                    "schema": 1,
                    "status": "pass",
                    "rom_sha256": self.rom_sha,
                    "checks": [
                        {
                            "check": f"fixture_{report_index}",
                            "status": "pass",
                        }
                    ],
                },
            )

    def write_passing_m3_reports(self) -> None:
        for (
            report_index,
            (_gate_name, filename, require_rom_sha256, require_checks),
        ) in enumerate(M3_EVIDENCE_REPORTS):
            value: dict[str, object] = {
                "schema": 1,
                "status": "pass",
            }
            if require_rom_sha256:
                value["rom_sha256"] = self.rom_sha
            if require_checks:
                value["checks"] = [
                    {
                        "check": f"m3_fixture_{report_index}",
                        "status": "pass",
                    }
                ]
            write_json(
                self.root / "artifacts/reports" / filename,
                value,
            )

    def write_passing_m4_reports(self) -> None:
        for (
            report_index,
            (_gate_name, filename, require_rom_sha256, require_checks),
        ) in enumerate(M4_EVIDENCE_REPORTS):
            if filename == "m4-renderer-budget.json":
                continue
            if filename == "m4-cycle-evidence.json":
                self.write_passing_m4_cycle_report()
                continue
            value: dict[str, object] = {
                "schema": 1,
                "status": "pass",
            }
            if require_rom_sha256:
                value["rom_sha256"] = self.rom_sha
            if require_checks:
                value["checks"] = [
                    {
                        "check": f"m4_fixture_{report_index}",
                        "status": "pass",
                    }
                ]
            write_json(
                self.root / "artifacts/reports" / filename,
                value,
            )
        # Both specialized gates bind the same emulator/config/symbol files.
        # Build the renderer report last so its fixture reuses the exact
        # cycle-fixture copies instead of replacing shared provenance.
        self.write_passing_m4_budget_report()

    @staticmethod
    def cycle_marker(prefix: str, record: dict[str, object]) -> str:
        if prefix == "M":
            return (
                f"R94C M {record['phase']} {record['cycle']:x} "
                f"{record['frame']:x} {record['tick']:x} "
                f"{record['attempt']:x} {record['checkpoints']:x} "
                f"{record['attempt_length']:x}"
            )
        return (
            f"R94C V {record['phase']} {record['cycle']:x} "
            f"{record['frame']:x} {record['attempt']:x}"
        )

    def write_passing_m4_cycle_report(self) -> None:
        relative_inputs = (
            ".toolcache/blastem/blastem.exe",
            ".toolcache/blastem/default.cfg",
            CYCLE_COLLECTOR_PATH,
            CYCLE_SCHEMA_PATH,
            CYCLE_LAUNCHER_PATH,
            "tools/toolchain.lock.json",
        )
        for relative in relative_inputs:
            source = self.source_root / relative
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

        symbol_path = self.root / "build/recursion94-debug.sym"
        # The original full M4 map is no longer a mutable build output. Rebuild
        # this test-only subset from the frozen reports and archived opcodes;
        # never bind the historical parser to the changing M5 startup routine.
        frozen_budget = load_json_for_test(
            self.source_root / "artifacts/reports/m4-renderer-budget.json"
        )
        frozen_cycle = load_json_for_test(self.source_root / CYCLE_REPORT_PATH)
        frozen_contract = frozen_cycle["pc_contract"]
        frozen_symbols = {
            name: int(address, 16)
            for name, address in frozen_budget["provenance"]["symbol_map"][
                "symbols"
            ].items()
        }
        renderer_call = frozen_contract["addresses"]["main_renderer"]
        archived_rom = (self.root / "build/recursion94-debug.bin").read_bytes()
        self.assertEqual(b"\x61\x00", archived_rom[renderer_call:renderer_call + 2])
        renderer_target = renderer_call + 2 + int.from_bytes(
            archived_rom[renderer_call + 2:renderer_call + 4],
            "big",
            signed=True,
        )
        wait = frozen_contract["addresses"]["main_end"] + 2
        self.assertEqual(b"\x4e\x72\x25\x00", archived_rom[wait:wait + 4])
        terminal = frozen_contract["terminal_reset_derivation"]
        frozen_symbols.update({
            "MainLoop": frozen_contract["main_start_derivation"]["main_loop_symbol"],
            "MainLoopWaitForVBlank": wait,
            "MainLoopGameplayStepCall": terminal["gameplay_step_call"],
            "MainLoopPostGameplayStepCall": frozen_contract["addresses"]["terminal_step_end"],
            "MainLoopCommitResetCall": terminal["reset_call"],
            "MainLoopPostCommitReset": frozen_contract["addresses"]["reset_end"],
            "GameplayRendererBuildAndQueue": renderer_target,
            "GameplayStep": frozen_contract["projection_derivation"]["gameplay_step_symbol"],
            "EngineBuildProjection": frozen_contract["addresses"]["projection_entry"],
            # Exact fixed RAM addresses retained in the frozen command script.
            "ENGINE_CHECKPOINT_COUNT": 0x00FF1420,
            "TIMELINE_ATTEMPT_LENGTH": 0x00FF13FC,
        })
        for address in (0x00FF1420, 0x00FF13FC):
            self.assertIn(f"[${address:X}].w", frozen_cycle["sessions"][0]["command_script"])
        symbol_path.write_text(
            "".join(
                f"{address:08X} {name}\n"
                for name, address in sorted(frozen_symbols.items())
            ),
            encoding="utf-8",
        )
        symbols = parse_vasm_symbols(symbol_path)
        rom_path = self.root / "build/recursion94-debug.bin"
        pc_contract = derive_pc_contract(rom_path.read_bytes(), symbols)
        main_records, embedded = synthetic_main_records()
        vblank_records = synthetic_vblank_records()
        main_measurement = analyse_main_records(main_records, embedded)
        vblank_measurement = analyse_vblank_records(vblank_records)
        sessions = []
        for session_id, mode in CYCLE_SESSION_LAYOUT:
            if mode == "main":
                session = synthetic_cycle_session(
                    session_id,
                    mode,
                    json.loads(json.dumps(main_records)),
                    json.loads(json.dumps(embedded)),
                    json.loads(json.dumps(main_measurement)),
                )
            else:
                session = synthetic_cycle_session(
                    session_id,
                    mode,
                    json.loads(json.dumps(vblank_records)),
                    [],
                    json.loads(json.dumps(vblank_measurement)),
                )
            script = build_cycle_command_script(
                mode, pc_contract, symbols
            )
            session["command_script"] = script
            session["command_script_sha256"] = hashlib.sha256(
                script.encode("utf-8")
            ).hexdigest()
            session["command_policy_errors"] = cycle_command_script_errors(
                script, mode, pc_contract, symbols
            )
            console_lines = ["200: move #$2700, SR", ">"]
            console_lines.extend(
                f"> {line}" for line in script.splitlines()
            )
            prefix = "M" if mode == "main" else "V"
            console_lines.extend(
                self.cycle_marker(prefix, record)
                for record in session["records"]
            )
            if mode == "main":
                console_lines.extend(
                    self.cycle_marker("V", record)
                    for record in session["interrupt_records"]
                )
            console_lines.extend(
                (
                    (
                        "R94C M COMPLETE"
                        if mode == "main"
                        else "R94C V COMPLETE"
                    ),
                    ">",
                )
            )
            console_text = "\n".join(console_lines) + "\n"
            session["console_sha256"] = hashlib.sha256(
                console_text.encode("utf-8")
            ).hexdigest()
            session["console_policy_errors"] = cycle_console_errors(
                console_text, mode, script
            )
            session["records_sha256"] = cycle_records_digest(
                session["records"]
            )
            session[
                "interrupt_records_sha256"
            ] = cycle_records_digest(session["interrupt_records"])
            session["_console_text"] = console_text
            sessions.append(session)

        trace_path = self.root / CYCLE_TRACE_PATH
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            cycle_trace_text(sessions),
            encoding="utf-8",
            newline="\n",
        )

        def record(relative: str) -> dict[str, object]:
            path = self.root / relative
            return {
                "path": relative,
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }

        provenance = {
            "rom": record("build/recursion94-debug.bin"),
            "emulator": {
                **record(".toolcache/blastem/blastem.exe"),
                "version": CYCLE_BLASTEM_VERSION,
                "revision": CYCLE_BLASTEM_REVISION,
            },
            "default_config": record(
                ".toolcache/blastem/default.cfg"
            ),
            "symbols": record("build/recursion94-debug.sym"),
            "collector": record(CYCLE_COLLECTOR_PATH),
            "schema": record(CYCLE_SCHEMA_PATH),
            "launcher": record(CYCLE_LAUNCHER_PATH),
            "toolchain_lock": record("tools/toolchain.lock.json"),
            "trace": record(CYCLE_TRACE_PATH),
            "platform": {
                "os_name": "nt",
                "platform": "test",
                "python": "test",
            },
            "session_config_sha256": {
                session["session_id"]: session["isolated_config"]["sha256"]
                for session in sessions
            },
        }
        report = build_cycle_report(
            rom_sha256=self.rom_sha,
            sessions=[
                cycle_public_session(session) for session in sessions
            ],
            pc_contract=pc_contract,
            provenance=provenance,
            limitations=["synthetic aggregate-validation fixture"],
        )
        self.assertEqual("pass", report["status"])
        write_json(self.root / CYCLE_REPORT_PATH, report)

    def write_passing_m4_budget_report(self) -> None:
        source_root = Path(__file__).resolve().parents[2]
        emulator = self.root / EMULATOR_RELATIVE_PATH
        config = self.root / DEFAULT_CONFIG_RELATIVE_PATH
        collector = self.root / COLLECTOR_RELATIVE_PATH
        schema = self.root / SCHEMA_RELATIVE_PATH
        symbol_map = self.root / SYMBOL_MAP_RELATIVE_PATH
        for path in (emulator, config, collector, schema, symbol_map):
            path.parent.mkdir(parents=True, exist_ok=True)
        if not emulator.is_file():
            emulator.write_bytes(b"pinned emulator fixture")
        if not config.is_file():
            config.write_bytes(b"pinned controller config fixture")
        collector.write_bytes(
            (source_root / COLLECTOR_RELATIVE_PATH).read_bytes()
        )
        schema.write_bytes(
            (source_root / SCHEMA_RELATIVE_PATH).read_bytes()
        )

        if symbol_map.is_file():
            symbol_values = parse_vasm_symbols(symbol_map)
        else:
            symbol_values = dict(EXPECTED_STATE_SYMBOLS)
            symbol_values[TERMINAL_BREAKPOINT_SYMBOL] = 0x1234
            symbol_values.update(
                {
                    name: 0x2000 + index * 0x100
                    for index, name in enumerate(
                        FORBIDDEN_BREAKPOINT_SYMBOLS
                    )
                }
            )
            symbol_map.write_text(
                "".join(
                    f"{address:08X} {name}\n"
                    for name, address in sorted(symbol_values.items())
                ),
                encoding="utf-8",
            )

        sessions = [synthetic_session(1), synthetic_session(2)]
        terminal_address = symbol_values[TERMINAL_BREAKPOINT_SYMBOL]
        terminal_text = f"{terminal_address:08X}"
        for session in sessions:
            session["metadata"][
                "terminal_breakpoint_address"
            ] = terminal_text
            session["_transcript_lines"][1] = (
                f"> Z0,{terminal_address:x},2"
            )
            session["metadata"]["transcript_policy"] = analyze_transcript(
                session["_transcript_lines"],
                terminal_address=terminal_address,
                forbidden_addresses={
                    name: symbol_values[name]
                    for name in FORBIDDEN_BREAKPOINT_SYMBOLS
                },
            )
            session["metadata"]["transcript_sha256"] = hashlib.sha256(
                (
                    "\n".join(session["_transcript_lines"]) + "\n"
                ).encode("utf-8")
            ).hexdigest()
        trace_lines = [
            line
            for session in sessions
            for line in session["_transcript_lines"]
        ]
        trace = self.root / TRACE_RELATIVE_PATH
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(
            "\n".join(trace_lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        emulator_sha = file_sha256(emulator)
        config_sha = file_sha256(config)
        for session in sessions:
            metadata = session["metadata"]
            metadata["runtime_copy"][
                "executable_sha256"
            ] = emulator_sha
            metadata["runtime_copy"][
                "default_config_sha256"
            ] = config_sha
            metadata["controller_profile"][
                "source_sha256"
            ] = config_sha

        provenance = synthetic_provenance()
        provenance["rom"]["sha256"] = self.rom_sha
        provenance["rom"]["expected_sha256"] = self.rom_sha
        provenance["emulator"]["sha256"] = emulator_sha
        provenance["default_config"]["sha256"] = config_sha
        provenance["symbol_map"]["sha256"] = file_sha256(symbol_map)
        provenance["symbol_map"]["symbols"] = {
            name: f"{symbol_values[name]:08X}"
            for name in BUDGET_REQUIRED_SYMBOLS
        }
        provenance["report_schema"]["sha256"] = file_sha256(schema)
        provenance["collector"]["sha256"] = file_sha256(collector)
        provenance["transcript"]["sha256"] = file_sha256(trace)
        provenance["transcript"]["commands"] = sum(
            line.startswith("> ") for line in trace_lines
        )
        current_hashes = {
            "rom": self.rom_sha,
            "emulator": emulator_sha,
            "default_config": config_sha,
            "symbol_map": file_sha256(symbol_map),
            "report_schema": file_sha256(schema),
            "collector": file_sha256(collector),
        }
        provenance["hash_verification"] = {
            "status": "pass",
            "drift": [],
            "before_after": {
                name: {"before": value, "after": value}
                for name, value in current_hashes.items()
            },
        }
        report = build_budget_report(
            rom_sha256=self.rom_sha,
            sessions=sessions,
            provenance=provenance,
        )
        self.assertEqual("pass", report["status"])
        write_json(
            self.root
            / "artifacts/reports/m4-renderer-budget.json",
            report,
        )

    def test_m4_renderer_budget_is_a_hash_bound_required_gate(self) -> None:
        self.assertIn(
            (
                "m4_renderer_vblank_budget",
                "m4-renderer-budget.json",
                True,
                True,
            ),
            M4_EVIDENCE_REPORTS,
        )

    def test_m4_cycle_budget_is_a_hash_bound_required_gate(self) -> None:
        self.assertIn(
            (
                "m4_m68000_cycle_budget",
                "m4-cycle-evidence.json",
                True,
                True,
            ),
            M4_EVIDENCE_REPORTS,
        )

    def test_missing_real_m2_reports_remain_pending(self) -> None:
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone2",
            command_exits=self.exits,
        )

        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "INCOMPLETE")
        for gate_name, _filename in M2_EVIDENCE_REPORTS:
            self.assertEqual(gate(report, gate_name)["status"], "pending")

    def test_all_hash_bound_m2_reports_can_pass_scope(self) -> None:
        self.write_passing_m2_reports()
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone2",
            command_exits=self.exits,
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["release_complete"])

    def test_stale_m2_rom_hash_fails_gate(self) -> None:
        self.write_passing_m2_reports()
        controller = self.root / "artifacts/reports/m2-controller.json"
        write_json(
            controller,
            {
                "status": "pass",
                "rom_sha256": "00" * 32,
                "checks": [{"check": "fixture", "status": "pass"}],
            },
        )
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone2",
            command_exits=self.exits,
        )

        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(gate(report, "m2_controller_ram")["status"], "fail")

    def test_current_rom_m2_reports_cannot_replace_frozen_release_history(
        self,
    ) -> None:
        self.write_passing_m2_reports()
        report, code = create_validation_report(
            root=self.root,
            scope="Release",
            command_exits=self.exits,
        )

        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["release_complete"])
        self.assertEqual(
            gate(report, "frozen_m2_m4_evidence")["status"],
            "fail",
        )
        self.assertEqual(gate(report, "deterministic_replays")["status"], "pending")

    def test_missing_m3_reports_keep_milestone3_pending(self) -> None:
        self.write_passing_m2_reports()
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone3",
            command_exits=self.exits,
        )

        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "INCOMPLETE")
        for gate_name, _filename, _require_rom, _require_checks in M3_EVIDENCE_REPORTS:
            self.assertEqual(gate(report, gate_name)["status"], "pending")

    def test_hash_bound_m3_reports_can_pass_scope(self) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone3",
            command_exits=self.exits,
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["release_complete"])

    def test_stale_m3_runtime_rom_hash_fails_gate(self) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        runtime = self.root / "artifacts/reports/m3-runtime.json"
        write_json(
            runtime,
            {
                "status": "pass",
                "rom_sha256": "00" * 32,
                "checks": [{"check": "fixture", "status": "pass"}],
            },
        )
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone3",
            command_exits=self.exits,
        )

        self.assertEqual(code, 1)
        self.assertEqual(gate(report, "m3_live_runtime")["status"], "fail")

    def test_missing_m4_reports_keep_milestone4_pending(self) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )

        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(gate(report, "m3_host_reference")["status"], "pass")
        for gate_name, _filename, _require_rom, _require_checks in M4_EVIDENCE_REPORTS:
            self.assertEqual(gate(report, gate_name)["status"], "pending")

    def test_hash_bound_m4_reports_can_pass_scope(self) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["release_complete"])
        self.assertFalse(
            any(item["gate"] == "m3_live_runtime" for item in report["gates"])
        )

    def test_script_mode_cli_runs_m4_semantic_gates_from_non_root_cwd(
        self,
    ) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        invocation_directory = self.root / "non-root-cwd"
        invocation_directory.mkdir()
        report_path = self.root / "artifacts/reports/cli-validation.json"
        command = [
            sys.executable,
            str(self.source_root / "tools/validation_report.py"),
            "--root",
            str(self.root),
            "--scope",
            "Milestone4",
            "--report",
            str(report_path),
        ]
        for name in COMMAND_EXIT_NAMES:
            command.extend((f"--{name.replace('_', '-')}-exit", "0"))

        completed = subprocess.run(
            command,
            cwd=invocation_directory,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        report = json.loads(report_path.read_text("utf-8"))
        self.assertEqual(
            "pass",
            gate(report, "m4_renderer_vblank_budget")["status"],
        )
        self.assertEqual(
            "pass",
            gate(report, "m4_m68000_cycle_budget")["status"],
        )

    def test_m4_budget_current_collector_drift_fails_aggregate(
        self,
    ) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        (self.root / COLLECTOR_RELATIVE_PATH).write_bytes(
            b"changed collector"
        )
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )
        budget = gate(report, "m4_renderer_vblank_budget")
        self.assertEqual(1, code)
        self.assertEqual("fail", budget["status"])
        self.assertIn("current provenance hash drift", budget["detail"])

    def test_m4_cycle_semantic_tamper_fails_aggregate(self) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        path = self.root / CYCLE_REPORT_PATH
        value = json.loads(path.read_text("utf-8"))
        value["sessions"][0]["measurement"]["intervals"][0][
            "simulation"
        ]["delta_cpu_cycles"] += 1
        write_json(path, value)
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )
        cycle = gate(report, "m4_m68000_cycle_budget")
        self.assertEqual(1, code)
        self.assertEqual("fail", cycle["status"])
        self.assertIn("measurement derivation mismatch", cycle["detail"])

    def test_m4_cycle_current_collector_drift_fails_aggregate(
        self,
    ) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        (self.root / CYCLE_COLLECTOR_PATH).write_bytes(b"changed collector")
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )
        cycle = gate(report, "m4_m68000_cycle_budget")
        self.assertEqual(1, code)
        self.assertEqual("fail", cycle["status"])
        self.assertIn("current provenance hash drift", cycle["detail"])

    def test_m4_budget_edited_backlog_or_omitted_checks_fails(
        self,
    ) -> None:
        for mutation in ("backlog", "checks"):
            with self.subTest(mutation=mutation):
                self.write_passing_m2_reports()
                self.write_passing_m3_reports()
                self.write_passing_m4_reports()
                path = (
                    self.root
                    / "artifacts/reports/m4-renderer-budget.json"
                )
                value = json.loads(path.read_text("utf-8"))
                if mutation == "backlog":
                    value["sessions"][0]["observation"]["renderer"][
                        "backlog_frames"
                    ] = 1
                else:
                    value["checks"] = value["checks"][:2]
                write_json(path, value)
                report, code = create_validation_report(
                    root=self.root,
                    scope="Milestone4",
                    command_exits=self.exits,
                )
                self.assertEqual(1, code)
                self.assertEqual(
                    "fail",
                    gate(
                        report, "m4_renderer_vblank_budget"
                    )["status"],
                )

    def test_m4_budget_trace_execution_injection_fails_aggregate(
        self,
    ) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        report_path = (
            self.root / "artifacts/reports/m4-renderer-budget.json"
        )
        value = json.loads(report_path.read_text("utf-8"))
        trace_path = self.root / TRACE_RELATIVE_PATH
        lines = trace_path.read_text("utf-8").splitlines()
        continue_index = lines.index("> c")
        lines[continue_index:continue_index] = ["> s", "< S05"]
        trace_path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        value["provenance"]["transcript"]["sha256"] = file_sha256(
            trace_path
        )
        value["provenance"]["transcript"]["commands"] = sum(
            line.startswith("> ") for line in lines
        )
        write_json(report_path, value)
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )
        budget = gate(report, "m4_renderer_vblank_budget")
        self.assertEqual(1, code)
        self.assertEqual("fail", budget["status"])
        self.assertIn("transcript", budget["detail"])

    def test_stale_m4_runtime_rom_hash_fails_gate(self) -> None:
        self.write_passing_m2_reports()
        self.write_passing_m3_reports()
        self.write_passing_m4_reports()
        runtime = self.root / "artifacts/reports/m4-runtime.json"
        write_json(
            runtime,
            {
                "status": "pass",
                "rom_sha256": "00" * 32,
                "checks": [{"check": "fixture", "status": "pass"}],
            },
        )
        report, code = create_validation_report(
            root=self.root,
            scope="Milestone4",
            command_exits=self.exits,
        )

        self.assertEqual(code, 1)
        self.assertEqual(
            gate(report, "m4_live_runtime_and_canonical_replay")["status"],
            "fail",
        )


class Milestone5AggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source_root = Path(__file__).resolve().parents[2]
        self.exits = {name: 0 for name in COMMAND_EXIT_NAMES}
        self._copy_m5_baseline()
        write_passing_host_tests_report(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def copy(self, relative: str, destination: str | None = None) -> None:
        source = self.source_root / relative
        target = self.root / (destination or relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _copy_report_bindings(
        self, report_relative: str, *, fixture_archive: str | None = None
    ) -> None:
        self.copy(
            f"{fixture_archive}/{Path(report_relative).name}"
            if fixture_archive else report_relative,
            report_relative,
        )
        report = load_json_for_test(self.root / report_relative)
        provenance = report.get("provenance", {})
        for key in ("rom", "symbol_map", "transcript"):
            record = provenance.get(key)
            if isinstance(record, dict) and isinstance(record.get("path"), str):
                self.copy(
                    f"{fixture_archive}/{Path(record['path']).name}"
                    if fixture_archive and key == "transcript" else record["path"],
                    record["path"],
                )
                record["sha256"] = file_sha256(self.root / record["path"])
        source_module = (
            m5_b03 if report_relative.endswith("m5-b03-runtime.json") else m5_b01
        )
        for relative in source_module.SOURCE_BINDING_PATHS:
            self.copy(relative)
        replay = report.get("guest_replay_ram")
        if isinstance(replay, dict) and isinstance(replay.get("path"), str):
            self.copy(
                f"{fixture_archive}/{Path(replay['path']).name}"
                if fixture_archive else replay["path"],
                replay["path"],
            )
        # These temporary reports exercise aggregate hash/source plumbing.
        # Rebind copied inputs without altering captured checks or outcomes;
        # they are synthetic unit fixtures, never new current-ROM evidence.
        report["rom_sha256"] = provenance["rom"]["sha256"]
        report["symbol_map_sha256"] = provenance["symbol_map"]["sha256"]
        provenance["source_bindings"] = source_module._source_bindings(self.root)
        write_json(self.root / report_relative, report)

    def _copy_m5_baseline(self) -> None:
        for filename in (
            "recursion94-debug.bin",
            "recursion94-release.bin",
        ):
            self.copy(f"{FROZEN_M4_ARCHIVE}/{filename}")
        self.copy("artifacts/replays/B01.r94r")
        for relative, *_rest in FROZEN_PRIOR_REPORTS:
            self.copy(relative)

        for relative in (
            "build/recursion94-debug.bin",
            "build/recursion94-debug.sym",
            "build/recursion94-release.bin",
            "build/recursion94-release.sym",
            "build/level-B03/recursion94-debug.bin",
            "build/level-B03/recursion94-debug.sym",
        ):
            self.copy(relative)
        for rom_relative, report_filename in (
            ("build/recursion94-debug.bin", "rom-debug-milestone5.json"),
            ("build/recursion94-release.bin", "rom-release-milestone5.json"),
            ("build/level-B03/recursion94-debug.bin", "rom-b03-milestone5.json"),
        ):
            write_json(
                self.root / "artifacts/reports" / report_filename,
                validate_rom((self.root / rom_relative).read_bytes()),
            )
        self._copy_report_bindings("artifacts/reports/m5-runtime.json")
        # Live recapture may intentionally replace the current report with a
        # failure diagnostic. Retain actual prior passing data for these unit
        # fixtures without changing or masking that current workspace result.
        self._copy_report_bindings(
            "artifacts/reports/m5-b03-runtime.json",
            fixture_archive="artifacts/archive/m5-b03-runtime-pre-140039e3-20261010",
        )

    def aggregate(self, *, scope: str = "Milestone5") -> tuple[dict, int]:
        return create_validation_report(
            root=self.root,
            scope=scope,
            command_exits=self.exits,
        )

    def _write_visual_gate_fixture(self) -> dict[str, object]:
        for relative in (
            "build/level-B03/recursion94-debug.bin",
            "build/level-B03/recursion94-debug.sym",
        ):
            self.copy(relative)
        for relative in (
            ".toolcache/blastem/blastem.exe",
            ".toolcache/blastem/default.cfg",
            *m5_visual.SOURCE_BINDING_PATHS,
        ):
            self.copy(relative)

        symbol_path = self.root / "build/level-B03/recursion94-debug.sym"
        rom_path = self.root / "build/level-B03/recursion94-debug.bin"
        symbols = parse_vasm_symbols(symbol_path)
        rom_bytes = rom_path.read_bytes()
        renderer_call = m5_visual.derive_renderer_return_address(
            rom_bytes, symbols
        )
        dma_commit_call = m4_visual.derive_post_dma_commit_address(
            rom_bytes, symbols
        )
        pre_step_binding = m5_b03.verify_pre_step_rom_binding(
            rom_path, symbols
        )

        trace_relative = (
            "artifacts/traces/m5-b03-visual-gdb-transcript.log"
        )
        trace_path = self.root / trace_relative
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_lines = [
            line
            for spec in m5_visual.CAPTURE_PLAN
            for line in (
                f"# visual_{spec.name} begin",
                f"# visual_{spec.name} end",
            )
        ]
        trace_path.write_text(
            "\n".join(trace_lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        image_sources = (
            "artifacts/screenshots/m4-before-repair-echo-door-open.png",
            "artifacts/screenshots/m4-before-repair-program-stable.png",
            "artifacts/screenshots/m4-before-repair-reset-trace.png",
            "artifacts/screenshots/m4-before-repair-zero-echo.png",
            "artifacts/screenshots/m4-blocked-path.png",
        )
        captures: list[dict[str, object]] = []
        for index, (spec, source) in enumerate(
            zip(m5_visual.CAPTURE_PLAN, image_sources, strict=True)
        ):
            self.copy(source, spec.output)
            png_path = self.root / spec.output
            session_root = f"C:/fixture/visual-{index}"
            runtime_copy = {
                "status": "pass",
                "source_directory": (
                    self.root / ".toolcache/blastem"
                ).resolve().as_posix(),
                "temporary_directory": f"{session_root}/blastem",
                "executable_sha256": file_sha256(
                    self.root / ".toolcache/blastem/blastem.exe"
                ),
                "default_config_sha256": file_sha256(
                    self.root / ".toolcache/blastem/default.cfg"
                ),
                "bundled_override": f"{session_root}/blastem/blastem.cfg",
                "temporary_copy_removed_after_session": True,
            }
            profile_path = f"{session_root}/blastem/blastem.cfg"
            isolation_sha = hashlib.sha256(
                f"isolation-{index}".encode("ascii")
            ).hexdigest()
            profile_sha = hashlib.sha256(
                f"profile-{index}".encode("ascii")
            ).hexdigest()
            screenshot_directory = f"{session_root}/session-data/screenshots"
            controller_profile = {
                "status": "pass",
                "detail": "isolated standard three-button fixture",
                "path": profile_path,
                "sha256": profile_sha,
                "source_sha256": file_sha256(
                    self.root / ".toolcache/blastem/default.cfg"
                ),
                "port_1_assignment": "gamepad3.1",
                "device_profile_sha256_before_paths": hashlib.sha256(
                    f"device-{index}".encode("ascii")
                ).hexdigest(),
                "runtime_copy": runtime_copy,
                "session_isolation": {
                    "status": "pass",
                    "config_path": profile_path,
                    "config_sha256": isolation_sha,
                    "config_sha256_before_screenshot_binding": isolation_sha,
                    "config_in_exe_dir": True,
                    "save_path": f"{session_root}/session-data/saves/$ROMNAME",
                    "screenshot_path": screenshot_directory,
                    "vgm_path": f"{session_root}/session-data/vgm",
                },
                "screenshot_binding": {
                    "status": "pass",
                    "removed_default_key": "p",
                    "installed_config_key": "f1",
                    "windows_key": {
                        "name": "F1",
                        "virtual_key": 0x70,
                        "scan_code": 0x3B,
                        "extended": False,
                    },
                    "only_ui_screenshot_binding": True,
                    "config_sha256": profile_sha,
                },
            }
            screenshot = {
                **m4_visual.analyze_png(
                    png_path, relative_path=spec.output
                ),
                "staging_path": f"artifacts/.fixture/{spec.name}.png",
                "mtime_ns": png_path.stat().st_mtime_ns,
                "captured_utc": "2026-07-26T00:00:00+00:00",
                "publication_verified": True,
                "published_mtime_ns": png_path.stat().st_mtime_ns,
                "window_ownership": {
                    "process_id": 4100 + index,
                    "resolved_owner_process_id": 4100 + index,
                    "ui_screenshot_key": "F1",
                    "isolated_screenshot_path": screenshot_directory,
                    "isolated_source_name": f"native-{index}.png",
                    "source_sha256": file_sha256(png_path),
                    "isolated_source_removed_after_verified_copy": True,
                },
            }
            session_lines = [
                f"# visual_{spec.name} begin",
                f"# visual_{spec.name} end",
            ]
            session_sha = hashlib.sha256(
                ("\n".join(session_lines) + "\n").encode("utf-8")
            ).hexdigest()
            captures.append(
                {
                    "name": spec.name,
                    "session": {
                        "session_id": f"visual_{spec.name}",
                        "process_id": 4100 + index,
                        "fresh_process": True,
                        "launch_flags": list(m5_b03.LAUNCH_FLAGS),
                        "runtime_copy": runtime_copy,
                        "controller_profile": controller_profile,
                        "standard_three_button_pad": True,
                        "input_transport": "targeted_PostMessageW",
                    },
                    "gdb": {
                        "transcript_sha256": session_sha,
                        "commands": 0,
                        "guest_write_packets": [],
                        "guest_memory_register_pc_writes": False,
                        "all_stops_exact": True,
                        "allowed_packets": [
                            "Z0 software breakpoint insert",
                            "z0 software breakpoint remove",
                            "c continue",
                            "m bounded guest-memory read",
                            "p read-only register read",
                        ],
                    },
                    "objective_state_facts": {"fixture_state": True},
                    "screenshot": screenshot,
                }
            )

        default_config_path = self.root / ".toolcache/blastem/default.cfg"
        stored_audit = {"status": "pass", "fixture": "canonical"}
        provenance = {
            "rom": {
                "path": "build/level-B03/recursion94-debug.bin",
                "sha256": file_sha256(rom_path),
                "expected_sha256": file_sha256(rom_path),
            },
            "symbol_map": {
                "path": "build/level-B03/recursion94-debug.sym",
                "sha256": file_sha256(symbol_path),
                "expected_sha256": file_sha256(symbol_path),
                "required_symbols": {
                    name: f"{symbols[name]:08X}"
                    for name in m5_visual.REQUIRED_SYMBOLS
                },
            },
            "emulator": {
                "path": ".toolcache/blastem/blastem.exe",
                "sha256": file_sha256(
                    self.root / ".toolcache/blastem/blastem.exe"
                ),
            },
            "default_config": {
                "path": ".toolcache/blastem/default.cfg",
                **verify_controller_bindings(default_config_path),
            },
            "transcript": {
                "path": trace_relative,
                "sha256": file_sha256(trace_path),
                "commands": 0,
                "lines": len(trace_lines),
                "guest_write_packets": [],
            },
            "source_bindings": m5_visual._source_bindings(  # noqa: SLF001
                self.root
            ),
            "pre_step_rom_binding": pre_step_binding,
            "renderer_call": renderer_call,
            "dma_commit_call": dma_commit_call,
            "capture_protocol_audit": stored_audit,
        }
        return {
            "schema": "r94-m5-b03-visual-evidence-v1",
            "scope": "Milestone5/B03/native-visual-capture",
            "evidence_type": m5_visual.EVIDENCE_TYPE,
            "status": "pass",
            "acceptance_status": "evidence_ready",
            "rom_sha256": file_sha256(rom_path),
            "symbol_map_sha256": file_sha256(symbol_path),
            "capture_count": len(captures),
            "captures": captures,
            "checks": [{"check": "fixture", "status": "pass"}],
            "objective_only": {
                "subjective_scores_emitted": False,
                "rubric_review_performed": False,
                "critic_handoff_required": True,
            },
            "critic_handoff": {
                "reserved_report_path": (
                    "artifacts/reports/m5-b03-visual-critic.json"
                ),
                "reserved_schema": (
                    "r94-m5-b03-independent-visual-review-v1"
                ),
                "capture_manifest_sha256": "a" * 64,
                "captures": [
                    {"label": capture["name"]} for capture in captures
                ],
            },
            "provenance": provenance,
            "limitations": ["validator fixture"],
        }

    def _write_visual_critic_fixture(
        self,
    ) -> tuple[dict[str, object], dict[str, object]]:
        capture_report = self._write_visual_gate_fixture()
        capture_report_path = (
            self.root / "artifacts/reports/m5-b03-visual.json"
        )
        write_json(capture_report_path, capture_report)

        aggregate_citations = " ".join(
            f"[{label}]" for label in M5_VISUAL_CAPTURE_LABELS
        )
        aggregate_categories = [
            {
                "category": category,
                "score": 5,
                "evidence": (
                    f"{aggregate_citations}: the sprite remains distinct "
                    f"in the pinned {category} comparison."
                ),
                "captures": list(M5_VISUAL_CAPTURE_LABELS),
            }
            for category in VISUAL_RUBRIC_CATEGORIES
        ]
        scenes = [
            {
                "label": label,
                "status": "pass",
                "categories": [
                    {
                        "category": category,
                        "score": 5,
                        "evidence": (
                            f"[{label}] the sprite remains distinct in the "
                            f"pinned {category} state."
                        ),
                        "captures": [label],
                    }
                    for category in VISUAL_RUBRIC_CATEGORIES
                ],
                "average_score": 5.0,
                "critical_gameplay_readability_defects": [],
                "severe_sprite_dropout_or_corruption": False,
            }
            for label in M5_VISUAL_CAPTURE_LABELS
        ]
        critic_report = {
            "schema": "r94-m5-b03-independent-visual-review-v1",
            "status": "pass",
            "rom_sha256": file_sha256(
                self.root / "build/level-B03/recursion94-debug.bin"
            ),
            "symbol_map_sha256": file_sha256(
                self.root / "build/level-B03/recursion94-debug.sym"
            ),
            "capture_report": {
                "path": "artifacts/reports/m5-b03-visual.json",
                "sha256": file_sha256(capture_report_path),
                "schema": "r94-m5-b03-visual-evidence-v1",
            },
            "capture_manifest_sha256": capture_report["critic_handoff"][
                "capture_manifest_sha256"
            ],
            "categories": aggregate_categories,
            "average_score": 5.0,
            "critical_gameplay_readability_defects": [],
            "severe_sprite_dropout_or_corruption": False,
            "scene_count": len(scenes),
            "scenes": scenes,
            "reviewer": {
                "role": "independent visual critic",
                "method": "per-scene review of each pinned objective PNG",
                "independent_from_renderer_implementation": True,
                "independent_from_capture_implementation": True,
            },
            "checks": [{"check": "per_scene_rubric", "status": "pass"}],
        }
        return capture_report, critic_report

    def _run_mocked_visual_gate(
        self,
        report: dict[str, object],
        *,
        audit: dict[str, object] | None = None,
        rebuilt: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], mock.Mock, mock.Mock]:
        selected_audit = (
            audit
            if audit is not None
            else report["provenance"]["capture_protocol_audit"]
        )
        selected_rebuild = rebuilt if rebuilt is not None else report
        with (
            mock.patch.object(
                m5_visual,
                "audit_visual_trace",
                return_value=selected_audit,
            ) as audit_mock,
            mock.patch.object(
                m5_visual,
                "build_visual_report",
                return_value=selected_rebuild,
            ) as build_mock,
        ):
            result = m5_visual_capture_gate(root=self.root, report=report)
        return result, audit_mock, build_mock

    def test_m5_live_and_frozen_gates_pass_while_new_qa_is_pending(
        self,
    ) -> None:
        report, code = self.aggregate()

        self.assertEqual(2, code)
        self.assertEqual("INCOMPLETE", report["status"])
        for name in (
            "host_unit_tests",
            "frozen_m2_m4_evidence",
            "m5_current_debug_static_artifact",
            "m5_current_release_static_artifact",
            "m5_current_b03_static_artifact",
            "m5_b01_live_runtime",
            "m5_b03_live_combined_room",
        ):
            self.assertEqual("pass", gate(report, name)["status"])
        for name in (
            "m5_b03_hardware_budget",
            "m5_b03_native_cycle_budget",
            "m5_b03_objective_visual_capture",
            "m5_b03_visual_critic",
        ):
            self.assertEqual("pending", gate(report, name)["status"])

    def test_unimplemented_m5_cycle_gate_cannot_be_status_only_pass(self) -> None:
        write_json(
            self.root / "artifacts/reports/m5-b03-cycle-evidence.json",
            {"status": "pass", "rom_sha256": file_sha256(
                self.root / "build/level-B03/recursion94-debug.bin"
            )},
        )
        for scope in ("Milestone5", "Release"):
            with self.subTest(scope=scope):
                report, code = self.aggregate(scope=scope)
                measured = gate(report, "m5_b03_native_cycle_budget")
                self.assertEqual(2, code)
                self.assertTrue(measured["required"])
                self.assertEqual("pending", measured["status"])
                self.assertIn("83203", measured["detail"])

    def test_host_unit_gate_binds_successful_all_suite_report(self) -> None:
        report_path = self.root / "artifacts/reports/host-tests.json"
        original = load_json_for_test(report_path)
        accepted = host_tests_gate(root=self.root, exit_code=0)

        self.assertEqual("pass", accepted["status"])
        self.assertEqual(file_sha256(report_path), accepted["report_sha256"])

        for mutation in (
            "schema_boolean",
            "report_status",
            "requested_suite",
            "failures",
            "errors",
            "skipped",
            "boolean_zero",
            "suite_status",
            "missing_suite",
            "extra_suite",
            "suite_order",
            "missing_m5",
            "duplicate_m5",
            "zero_tests",
            "missing_binary",
            "duplicate_binary",
            "binary_status",
            "binary_gate",
            "binary_hash",
            "binary_order",
        ):
            with self.subTest(mutation=mutation):
                value = json.loads(json.dumps(original))
                run = next(
                    item
                    for item in value["runs"]
                    if item["path"] == "tests/m5"
                )
                if mutation == "schema_boolean":
                    value["schema"] = True
                elif mutation == "report_status":
                    value["status"] = "fail"
                elif mutation == "requested_suite":
                    value["requested_suite"] = "Milestone5"
                elif mutation in {"failures", "errors", "skipped"}:
                    run[mutation] = 1
                elif mutation == "boolean_zero":
                    run["skipped"] = False
                elif mutation == "suite_status":
                    run["status"] = "fail"
                elif mutation == "missing_suite":
                    value["runs"].pop(0)
                elif mutation == "extra_suite":
                    extra = dict(run)
                    extra["path"] = "tests/unbound"
                    value["runs"].append(extra)
                elif mutation == "suite_order":
                    value["runs"].reverse()
                elif mutation == "missing_m5":
                    value["runs"] = [
                        item
                        for item in value["runs"]
                        if item["path"] != "tests/m5"
                    ]
                elif mutation == "duplicate_m5":
                    value["runs"].append(dict(run))
                elif mutation == "zero_tests":
                    run["tests_run"] = 0
                elif mutation == "missing_binary":
                    value["binary_checks"].pop()
                elif mutation == "duplicate_binary":
                    value["binary_checks"].append(
                        dict(value["binary_checks"][0])
                    )
                elif mutation == "binary_status":
                    value["binary_checks"][0]["status"] = "fail"
                elif mutation == "binary_gate":
                    value["binary_checks"][0]["gate_passed"] = False
                elif mutation == "binary_hash":
                    value["binary_checks"][0]["sha256"] = "0" * 64
                else:
                    value["binary_checks"].reverse()
                write_json(report_path, value)
                rejected = host_tests_gate(root=self.root, exit_code=0)
                self.assertEqual("fail", rejected["status"])

        write_json(report_path, original)
        debug_binary = self.root / "build/recursion94-debug.bin"
        debug_bytes = debug_binary.read_bytes()
        debug_binary.write_bytes(debug_bytes + b"\0")
        self.assertEqual(
            "fail",
            host_tests_gate(root=self.root, exit_code=0)["status"],
        )
        debug_binary.write_bytes(debug_bytes)

        write_json(report_path, original)
        self.assertEqual(
            "fail",
            host_tests_gate(root=self.root, exit_code=7)["status"],
        )
        report_path.unlink()
        self.assertEqual(
            "fail",
            host_tests_gate(root=self.root, exit_code=0)["status"],
        )

        write_json(report_path, original)
        aggregate_value = json.loads(json.dumps(original))
        aggregate_value["runs"][0]["skipped"] = 1
        write_json(report_path, aggregate_value)
        aggregate, code = self.aggregate()
        self.assertEqual(1, code)
        self.assertEqual("fail", gate(aggregate, "host_unit_tests")["status"])

    def test_missing_frozen_archive_is_pending_not_rebound_to_current(
        self,
    ) -> None:
        (
            self.root
            / FROZEN_M4_ARCHIVE
            / "recursion94-debug.bin"
        ).unlink()
        report, code = self.aggregate()

        self.assertEqual(2, code)
        self.assertEqual(
            "pending", gate(report, "frozen_m2_m4_evidence")["status"]
        )

    def test_tampered_frozen_archive_fails(self) -> None:
        path = (
            self.root
            / FROZEN_M4_ARCHIVE
            / "recursion94-debug.bin"
        )
        path.write_bytes(path.read_bytes()[:-1] + b"\0")
        report, code = self.aggregate()

        self.assertEqual(1, code)
        self.assertEqual(
            "fail", gate(report, "frozen_m2_m4_evidence")["status"]
        )

    def test_tampered_frozen_report_fails(self) -> None:
        path = self.root / "artifacts/reports/m4-runtime.json"
        value = load_json_for_test(path)
        value["checks"] = value["checks"][:-1]
        write_json(path, value)
        report, code = self.aggregate()

        self.assertEqual(1, code)
        self.assertEqual(
            "fail", gate(report, "frozen_m2_m4_evidence")["status"]
        )

    def test_stale_or_omitted_b01_runtime_contract_fails(self) -> None:
        path = self.root / "artifacts/reports/m5-runtime.json"
        original = load_json_for_test(path)
        for mutation in ("rom", "checks", "writes"):
            with self.subTest(mutation=mutation):
                value = json.loads(json.dumps(original))
                if mutation == "rom":
                    value["rom_sha256"] = "00" * 32
                elif mutation == "checks":
                    value["checks"] = value["checks"][:-1]
                else:
                    value["debugger_write_packets"] = ["Mff0000,1:00"]
                write_json(path, value)
                report, code = self.aggregate()
                self.assertEqual(1, code)
                self.assertEqual(
                    "fail", gate(report, "m5_b01_live_runtime")["status"]
                )
        write_json(path, original)

    def test_b01_source_or_transcript_drift_fails(self) -> None:
        for relative in (
            "src/gameplay/vocabulary_core.asm",
            "artifacts/traces/m5-runtime-gdb-transcript.log",
        ):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                report, code = self.aggregate()
                self.assertEqual(1, code)
                self.assertEqual(
                    "fail", gate(report, "m5_b01_live_runtime")["status"]
                )
                path.write_bytes(original)

    def test_b03_acceptance_symbol_and_replay_tamper_fail(self) -> None:
        report_path = self.root / "artifacts/reports/m5-b03-runtime.json"
        original_report = load_json_for_test(report_path)
        replay_path = (
            self.root / "artifacts/replays/m5-b03-live-replay-ram.bin"
        )
        original_replay = replay_path.read_bytes()
        for mutation in ("acceptance", "symbol", "replay"):
            with self.subTest(mutation=mutation):
                value = json.loads(json.dumps(original_report))
                if mutation == "acceptance":
                    value["acceptance"][0]["status"] = "incomplete"
                    write_json(report_path, value)
                elif mutation == "symbol":
                    value["symbol_map_sha256"] = "11" * 32
                    write_json(report_path, value)
                else:
                    replay_path.write_bytes(original_replay[:-1] + b"\0")
                report, code = self.aggregate()
                self.assertEqual(1, code)
                self.assertEqual(
                    "fail",
                    gate(report, "m5_b03_live_combined_room")["status"],
                )
                write_json(report_path, original_report)
                replay_path.write_bytes(original_replay)

    def test_missing_b03_runtime_is_pending(self) -> None:
        (self.root / "artifacts/reports/m5-b03-runtime.json").unlink()
        report, code = self.aggregate()

        self.assertEqual(2, code)
        self.assertEqual(
            "pending",
            gate(report, "m5_b03_live_combined_room")["status"],
        )

    def test_malformed_existing_m5_report_fails_not_pending(self) -> None:
        path = self.root / "artifacts/reports/m5-runtime.json"
        path.write_text("{", encoding="utf-8")
        report, code = self.aggregate()

        self.assertEqual(1, code)
        self.assertEqual(
            "fail", gate(report, "m5_b01_live_runtime")["status"]
        )

    def test_stale_static_report_fails(self) -> None:
        path = (
            self.root / "artifacts/reports/rom-b03-milestone5.json"
        )
        value = load_json_for_test(path)
        value["facts"]["sha256"] = "22" * 32
        write_json(path, value)
        report, code = self.aggregate()

        self.assertEqual(1, code)
        self.assertEqual(
            "fail",
            gate(report, "m5_current_b03_static_artifact")["status"],
        )

    def test_status_only_budget_visual_and_critic_reports_fail(self) -> None:
        fixtures = (
            (
                "m5-b03-budget.json",
                {
                    "schema": "r94-m5-b03-budget-evidence-v3",
                    "status": "pass",
                },
                "m5_b03_hardware_budget",
            ),
            (
                "m5-b03-visual.json",
                {
                    "schema": "r94-m5-b03-visual-evidence-v1",
                    "status": "pass",
                },
                "m5_b03_objective_visual_capture",
            ),
            (
                "m5-b03-visual-critic.json",
                {
                    "schema": (
                        "r94-m5-b03-independent-visual-review-v1"
                    ),
                    "status": "pass",
                },
                "m5_b03_visual_critic",
            ),
        )
        for filename, value, gate_name in fixtures:
            with self.subTest(filename=filename):
                path = self.root / "artifacts/reports" / filename
                write_json(path, value)
                report, code = self.aggregate()
                self.assertEqual(1, code)
                self.assertEqual("fail", gate(report, gate_name)["status"])
                path.unlink()

    def test_m5_budget_rejects_v1_v2_and_accepts_v3_contract(self) -> None:
        report = {
            "schema": "r94-m5-b03-budget-evidence-v3",
            "status": "pass",
            "rom_sha256": file_sha256(
                self.root / "build/level-B03/recursion94-debug.bin"
            ),
            "symbol_map_sha256": file_sha256(
                self.root / "build/level-B03/recursion94-debug.sym"
            ),
        }
        with mock.patch(
            "tools.m5_budget_evidence.report_contract_errors",
            return_value=[],
        ) as contract:
            accepted = m5_budget_gate(root=self.root, report=report)
        self.assertEqual("pass", accepted["status"])
        contract.assert_called_once_with(report, root=self.root)

        for version in (1, 2):
            with self.subTest(version=version):
                legacy = dict(report)
                legacy["schema"] = f"r94-m5-b03-budget-evidence-v{version}"
                with mock.patch(
                    "tools.m5_budget_evidence.report_contract_errors",
                    return_value=[],
                ):
                    rejected = m5_budget_gate(root=self.root, report=legacy)
                self.assertEqual("fail", rejected["status"])
                self.assertIn("schema", rejected["detail"])

    def test_m5_visual_critic_requires_all_five_per_scene_reviews(
        self,
    ) -> None:
        capture_report, critic_report = self._write_visual_critic_fixture()
        accepted = m5_visual_critic_gate(
            root=self.root,
            report=critic_report,
            capture_report=capture_report,
        )
        self.assertEqual("pass", accepted["status"])

        aggregate_only = json.loads(json.dumps(critic_report))
        del aggregate_only["scenes"]
        rejected = m5_visual_critic_gate(
            root=self.root,
            report=aggregate_only,
            capture_report=capture_report,
        )
        self.assertEqual("fail", rejected["status"])
        self.assertIn("per-scene", rejected["detail"])

    def test_m5_visual_critic_rejects_generic_or_uncited_praise(
        self,
    ) -> None:
        capture_report, original = self._write_visual_critic_fixture()
        accepted = m5_visual_critic_gate(
            root=self.root,
            report=original,
            capture_report=capture_report,
        )
        self.assertEqual("pass", accepted["status"])

        first_label = M5_VISUAL_CAPTURE_LABELS[0]
        all_citations = " ".join(
            f"[{label}]" for label in M5_VISUAL_CAPTURE_LABELS
        )
        mutations = {
            "scene_generic_with_exact_citation": (
                "scenes",
                f"[{first_label}] looks excellent and highly polished.",
            ),
            "scene_concrete_without_bracketed_citation": (
                "scenes",
                "The sprite remains distinct at the objective state.",
            ),
            "aggregate_generic_with_all_citations": (
                "categories",
                f"{all_citations}: every scene looks excellent and polished.",
            ),
            "aggregate_concrete_missing_one_citation": (
                "categories",
                " ".join(
                    f"[{label}]" for label in M5_VISUAL_CAPTURE_LABELS[:-1]
                )
                + ": the sprite remains distinct in every comparison.",
            ),
        }
        for mutation, (section, evidence) in mutations.items():
            with self.subTest(mutation=mutation):
                value = json.loads(json.dumps(original))
                if section == "scenes":
                    value["scenes"][0]["categories"][0][
                        "evidence"
                    ] = evidence
                else:
                    value["categories"][0]["evidence"] = evidence
                rejected = m5_visual_critic_gate(
                    root=self.root,
                    report=value,
                    capture_report=capture_report,
                )
                self.assertEqual("fail", rejected["status"])
                self.assertIn("concrete", rejected["detail"])

    def test_m5_visual_critic_rejects_per_scene_contract_spoofs(
        self,
    ) -> None:
        capture_report, original = self._write_visual_critic_fixture()
        for mutation in (
            "missing_scene",
            "wrong_label",
            "missing_category",
            "category_order",
            "score_below_four",
            "boolean_score",
            "average_below_threshold",
            "stale_average",
            "nonfinite_average",
            "missing_evidence",
            "wrong_capture_citation",
            "critical_defect",
            "severe_corruption",
        ):
            with self.subTest(mutation=mutation):
                value = json.loads(json.dumps(original))
                scene = value["scenes"][0]
                if mutation == "missing_scene":
                    value["scenes"].pop()
                    value["scene_count"] = 4
                elif mutation == "wrong_label":
                    scene["label"] = "unbound_scene"
                elif mutation == "missing_category":
                    scene["categories"].pop()
                elif mutation == "category_order":
                    scene["categories"][0], scene["categories"][1] = (
                        scene["categories"][1],
                        scene["categories"][0],
                    )
                elif mutation == "score_below_four":
                    scene["categories"][0]["score"] = 3
                elif mutation == "boolean_score":
                    scene["categories"][0]["score"] = True
                elif mutation == "average_below_threshold":
                    for category in scene["categories"]:
                        category["score"] = 4
                    scene["average_score"] = 4.0
                elif mutation == "stale_average":
                    scene["average_score"] = 4.9
                elif mutation == "nonfinite_average":
                    scene["average_score"] = float("nan")
                elif mutation == "missing_evidence":
                    scene["categories"][0]["evidence"] = ""
                elif mutation == "wrong_capture_citation":
                    scene["categories"][0]["captures"] = [
                        M5_VISUAL_CAPTURE_LABELS[1]
                    ]
                elif mutation == "critical_defect":
                    scene["critical_gameplay_readability_defects"] = [
                        "player silhouette is obscured"
                    ]
                else:
                    scene["severe_sprite_dropout_or_corruption"] = True
                rejected = m5_visual_critic_gate(
                    root=self.root,
                    report=value,
                    capture_report=capture_report,
                )
                self.assertEqual("fail", rejected["status"])

    def test_m5_visual_gate_recomputes_live_contract(self) -> None:
        report = self._write_visual_gate_fixture()
        result, audit, rebuild = self._run_mocked_visual_gate(report)

        self.assertEqual("pass", result["status"])
        live_symbols = parse_vasm_symbols(
            self.root / "build/level-B03/recursion94-debug.sym"
        )
        audit.assert_called_once()
        self.assertEqual(live_symbols, audit.call_args.kwargs["symbols"])
        self.assertEqual(
            live_symbols["MainLoopGameplayStepCall"],
            audit.call_args.kwargs["pre_step"],
        )
        self.assertEqual(
            live_symbols["MainLoopPostGameplayStep"],
            audit.call_args.kwargs["post_step"],
        )
        rebuild.assert_called_once()
        self.assertEqual(live_symbols, rebuild.call_args.kwargs["symbols"])

    def test_m5_visual_stale_stored_audit_fails(self) -> None:
        report = self._write_visual_gate_fixture()
        report["provenance"]["capture_protocol_audit"] = {
            "status": "pass",
            "fixture": "stale",
        }
        result, _audit, _rebuild = self._run_mocked_visual_gate(
            report,
            audit={"status": "pass", "fixture": "canonical"},
        )

        self.assertEqual("fail", result["status"])
        self.assertIn("capture-protocol audit is stale", result["detail"])

    def test_m5_visual_trace_mutation_fails_with_rehashed_file(self) -> None:
        report = self._write_visual_gate_fixture()
        transcript = report["provenance"]["transcript"]
        trace_path = self.root / transcript["path"]
        trace_path.write_text(
            trace_path.read_text("utf-8") + "> c\n",
            encoding="utf-8",
            newline="\n",
        )
        trace_lines = trace_path.read_text("utf-8").splitlines()
        transcript["sha256"] = file_sha256(trace_path)
        transcript["lines"] = len(trace_lines)
        transcript["commands"] = sum(
            line.startswith("> ") for line in trace_lines
        )
        result, _audit, _rebuild = self._run_mocked_visual_gate(
            report,
            audit={"status": "pass", "fixture": "mutated-trace"},
        )

        self.assertEqual("fail", result["status"])
        self.assertIn("capture-protocol audit is stale", result["detail"])

    def test_m5_visual_trace_path_cannot_be_redirected(self) -> None:
        report = self._write_visual_gate_fixture()
        transcript = report["provenance"]["transcript"]
        canonical = self.root / transcript["path"]
        redirected = self.root / "artifacts/traces/redirected-visual.log"
        redirected.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(canonical, redirected)
        transcript["path"] = "artifacts/traces/redirected-visual.log"
        transcript["sha256"] = file_sha256(redirected)
        result, _audit, _rebuild = self._run_mocked_visual_gate(report)

        self.assertEqual("fail", result["status"])
        self.assertIn("m5-b03-visual-gdb-transcript.log", result["detail"])

    def test_m5_visual_collection_time_expected_hashes_are_required(
        self,
    ) -> None:
        for binding in ("rom", "symbol_map"):
            with self.subTest(binding=binding):
                report = self._write_visual_gate_fixture()
                del report["provenance"][binding]["expected_sha256"]
                result, _audit, _rebuild = self._run_mocked_visual_gate(
                    report
                )
                self.assertEqual("fail", result["status"])
                self.assertIn("collection-time expected", result["detail"])

    def test_m5_visual_session_isolation_provenance_is_required(self) -> None:
        for mutation in (
            "runtime_copy",
            "controller_profile",
            "runtime_hash",
            "profile_hash",
            "window_lineage",
            "path_relationship",
            "case_alias_collision",
        ):
            with self.subTest(mutation=mutation):
                report = self._write_visual_gate_fixture()
                capture = report["captures"][0]
                session = capture["session"]
                if mutation == "runtime_copy":
                    del session["runtime_copy"]
                elif mutation == "controller_profile":
                    del session["controller_profile"]
                elif mutation == "runtime_hash":
                    session["runtime_copy"]["executable_sha256"] = "0" * 64
                elif mutation == "profile_hash":
                    session["controller_profile"]["sha256"] = "1" * 64
                elif mutation == "window_lineage":
                    capture["screenshot"]["window_ownership"][
                        "isolated_screenshot_path"
                    ] = "C:/unowned/screenshots"
                elif mutation == "path_relationship":
                    session["runtime_copy"]["bundled_override"] = (
                        "C:/unowned/blastem.cfg"
                    )
                    profile = session["controller_profile"]
                    profile["path"] = "C:/unowned/blastem.cfg"
                    profile["session_isolation"]["config_path"] = (
                        "C:/unowned/blastem.cfg"
                    )
                else:
                    first = report["captures"][0]
                    second = report["captures"][1]
                    first_runtime = first["session"]["runtime_copy"]
                    second_session = second["session"]
                    second_runtime = second_session["runtime_copy"]
                    second_runtime["temporary_directory"] = first_runtime[
                        "temporary_directory"
                    ].upper()
                    second_runtime["bundled_override"] = first_runtime[
                        "bundled_override"
                    ].upper()
                    first_profile = first["session"]["controller_profile"]
                    second_profile = second_session["controller_profile"]
                    second_profile["path"] = first_profile["path"].upper()
                    second_isolation = second_profile["session_isolation"]
                    first_isolation = first_profile["session_isolation"]
                    second_isolation["config_path"] = first_isolation[
                        "config_path"
                    ].upper()
                    for key in ("save_path", "screenshot_path", "vgm_path"):
                        second_isolation[key] = first_isolation[key].upper()
                    second["screenshot"]["window_ownership"][
                        "isolated_screenshot_path"
                    ] = second_isolation["screenshot_path"]
                result, _audit, _rebuild = self._run_mocked_visual_gate(
                    report
                )
                self.assertEqual("fail", result["status"])

    def test_m5_visual_per_session_gdb_command_count_is_recomputed(
        self,
    ) -> None:
        report = self._write_visual_gate_fixture()
        report["captures"][0]["gdb"]["commands"] = 1
        result, _audit, _rebuild = self._run_mocked_visual_gate(report)

        self.assertEqual("fail", result["status"])
        self.assertIn("GDB command count is stale", result["detail"])

    def test_m5_visual_missing_or_wrong_live_symbols_fail(self) -> None:
        for mutation in ("missing", "wrong"):
            with self.subTest(mutation=mutation):
                report = self._write_visual_gate_fixture()
                symbol_path = (
                    self.root / "build/level-B03/recursion94-debug.sym"
                )
                symbol_text = symbol_path.read_text("utf-8")
                if mutation == "missing":
                    symbol_text = "\n".join(
                        line for line in symbol_text.splitlines()
                        if not line.endswith(
                            " GameplayRendererBuildAndQueue"
                        )
                    ) + "\n"
                else:
                    original_address = parse_vasm_symbols(symbol_path)[
                        "GameplayRendererBuildAndQueue"
                    ]
                    symbol_text = symbol_text.replace(
                        f"{original_address:08X} GameplayRendererBuildAndQueue",
                        f"{original_address + 2:08X} GameplayRendererBuildAndQueue",
                    )
                symbol_path.write_text(
                    symbol_text, encoding="utf-8", newline="\n"
                )
                symbol_sha = file_sha256(symbol_path)
                report["symbol_map_sha256"] = symbol_sha
                report["provenance"]["symbol_map"]["sha256"] = symbol_sha
                if mutation == "wrong":
                    symbols = parse_vasm_symbols(symbol_path)
                    report["provenance"]["symbol_map"][
                        "required_symbols"
                    ] = {
                        name: f"{symbols[name]:08X}"
                        for name in m5_visual.REQUIRED_SYMBOLS
                    }
                result, _audit, _rebuild = self._run_mocked_visual_gate(
                    report
                )
                self.assertEqual("fail", result["status"])
                self.assertIn("visual semantic validation raised", result["detail"])

    def test_m5_visual_spoofed_png_metrics_fail(self) -> None:
        report = self._write_visual_gate_fixture()
        report["captures"][0]["screenshot"]["width"] += 1
        result, _audit, _rebuild = self._run_mocked_visual_gate(report)

        self.assertEqual("fail", result["status"])
        self.assertIn("differs in ['width']", result["detail"])

    def test_m5_visual_boolean_cannot_spoof_numeric_png_metric(self) -> None:
        report = self._write_visual_gate_fixture()
        report["captures"][0]["screenshot"]["opaque_ratio"] = True
        result, _audit, _rebuild = self._run_mocked_visual_gate(report)

        self.assertEqual("fail", result["status"])
        self.assertIn("opaque_ratio", result["detail"])

    def test_m5_visual_changed_png_with_stale_facts_fails(self) -> None:
        report = self._write_visual_gate_fixture()
        screenshot = report["captures"][0]["screenshot"]
        replacement = (
            "artifacts/screenshots/m4-echo-door-open.png"
        )
        self.copy(replacement, screenshot["path"])
        changed_path = self.root / screenshot["path"]
        screenshot["sha256"] = file_sha256(changed_path)
        screenshot["bytes"] = changed_path.stat().st_size
        screenshot["published_mtime_ns"] = changed_path.stat().st_mtime_ns
        result, _audit, _rebuild = self._run_mocked_visual_gate(report)

        self.assertEqual("fail", result["status"])
        self.assertIn("visual PNG analysis", result["detail"])

    def test_m5_visual_stale_semantic_rebuild_fails_and_uses_symbols(
        self,
    ) -> None:
        report = self._write_visual_gate_fixture()
        rebuilt = json.loads(json.dumps(report))
        rebuilt["limitations"] = ["live rebuilt contract"]
        result, _audit, build = self._run_mocked_visual_gate(
            report, rebuilt=rebuilt
        )

        self.assertEqual("fail", result["status"])
        self.assertIn("exact live-symbol semantic rebuild", result["detail"])
        live_symbols = parse_vasm_symbols(
            self.root / "build/level-B03/recursion94-debug.sym"
        )
        self.assertEqual(live_symbols, build.call_args.kwargs["symbols"])

    def test_m5_visual_emulator_config_or_source_drift_fails(self) -> None:
        mutations = (
            ".toolcache/blastem/blastem.exe",
            ".toolcache/blastem/default.cfg",
            m5_visual.SOURCE_BINDING_PATHS[0],
        )
        for relative in mutations:
            with self.subTest(relative=relative):
                report = self._write_visual_gate_fixture()
                path = self.root / relative
                path.write_bytes(path.read_bytes() + b"\n")
                result, _audit, _rebuild = self._run_mocked_visual_gate(
                    report
                )
                self.assertEqual("fail", result["status"])

    def test_m5_visual_rehashed_emulator_or_config_drift_still_fails(
        self,
    ) -> None:
        for kind in ("emulator", "default_config"):
            with self.subTest(kind=kind):
                report = self._write_visual_gate_fixture()
                if kind == "emulator":
                    path = self.root / ".toolcache/blastem/blastem.exe"
                    path.write_bytes(path.read_bytes() + b"\n")
                    report["provenance"]["emulator"]["sha256"] = (
                        file_sha256(path)
                    )
                else:
                    path = self.root / ".toolcache/blastem/default.cfg"
                    path.write_bytes(path.read_bytes() + b"\n")
                    report["provenance"]["default_config"] = {
                        "path": ".toolcache/blastem/default.cfg",
                        **verify_controller_bindings(path),
                    }
                result, _audit, _rebuild = self._run_mocked_visual_gate(
                    report
                )
                self.assertEqual("fail", result["status"])
                self.assertIn("not the pinned", result["detail"])

    def test_b03_build_or_static_command_failure_fails(self) -> None:
        for exit_name, gate_name in (
            ("b03_debug_build", "b03_debug_fixture_build"),
            ("b03_debug_static", "b03_debug_fixture_static_rom"),
        ):
            with self.subTest(exit_name=exit_name):
                exits = dict(self.exits)
                exits[exit_name] = 7
                report, code = create_validation_report(
                    root=self.root,
                    scope="Milestone5",
                    command_exits=exits,
                )
                self.assertEqual(1, code)
                self.assertEqual("fail", gate(report, gate_name)["status"])

    def test_release_keeps_all_pending_gates_and_inherits_m5(self) -> None:
        report, code = self.aggregate(scope="Release")

        self.assertEqual(2, code)
        self.assertFalse(report["release_complete"])
        for name in (
            "frozen_m2_m4_evidence",
            "m5_b01_live_runtime",
            "m5_b03_live_combined_room",
        ):
            self.assertEqual("pass", gate(report, name)["status"])
        for name in (
            "deterministic_replays",
            "save_integrity",
            "hardware_budgets",
            "campaign_content",
            "visual_qa",
            "audio_qa",
            "red_team",
        ):
            self.assertEqual("pending", gate(report, name)["status"])

    def test_cli_accepts_milestone5_and_writes_exact_scope(self) -> None:
        report_path = self.root / "artifacts/reports/cli-m5.json"
        command = [
            sys.executable,
            str(self.source_root / "tools/validation_report.py"),
            "--root",
            str(self.root),
            "--scope",
            "Milestone5",
            "--report",
            str(report_path),
        ]
        for name in COMMAND_EXIT_NAMES:
            command.extend((f"--{name.replace('_', '-')}-exit", "0"))
        completed = subprocess.run(
            command,
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(2, completed.returncode)
        self.assertEqual(
            "Milestone5",
            load_json_for_test(report_path)["scope"],
        )

    def test_powershell_selects_exact_m5_artifacts(self) -> None:
        wrapper = (self.source_root / "tools/validate.ps1").read_text(
            encoding="utf-8"
        )
        for fragment in (
            '"Milestone5"',
            '"validation-milestone5.json"',
            '"rom-debug-milestone5.json"',
            '"rom-release-milestone5.json"',
            '"rom-b03-milestone5.json"',
            '"--b03-debug-build-exit"',
            '"--b03-debug-static-exit"',
            '"-Level", "B03"',
        ):
            self.assertIn(fragment, wrapper)


def load_json_for_test(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text("utf-8"))
    assert isinstance(value, dict)
    return value


if __name__ == "__main__":
    unittest.main()
