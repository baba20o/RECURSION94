#!/usr/bin/env python3
"""Aggregate milestone/release validation without converting pending gates to passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import ntpath
import re
import sys
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    repository_root = str(Path(__file__).resolve().parents[1])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)


COMMAND_EXIT_NAMES = (
    "clean",
    "toolchain",
    "debug_build",
    "release_build",
    "b03_debug_build",
    "host_tests",
    "debug_static",
    "release_static",
    "b03_debug_static",
    "emulator_probe",
)
HOST_TEST_SUITE_PATHS = (
    "tests/assets",
    "tests/audio",
    "tests/campaign_runtime",
    "tests/content",
    "tests/emulator",
    "tests/host",
    "tests/m2",
    "tests/m3",
    "tests/m4",
    "tests/m5",
    "tests/render",
    "tests/replay",
    "tests/rom",
)
HOST_TEST_BINARY_CONFIGURATIONS = ("debug", "release")
FROZEN_M4_DEBUG_SHA256 = (
    "c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460"
)
FROZEN_M4_RELEASE_SHA256 = (
    "0cf94ab5a303bc077810f3d1fe2d4157070ffe6ca515ba0ae5568d3cb90f9d43"
)
FROZEN_M4_ARCHIVE = "artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5"
FROZEN_PRIOR_REPORTS = (
    (
        "artifacts/reports/m2-controller.json",
        1,
        31,
        True,
        "d06f3c00031ee069c01c222f953c7eed99e433016034e3054f26395d48941149",
    ),
    (
        "artifacts/reports/m2-video-dma.json",
        1,
        21,
        True,
        "a23ae0bb02eeb4410f95d14c9ce9409cf82e403ad0051657bd9df9e94cf4b5d5",
    ),
    (
        "artifacts/reports/m2-z80-audio-runtime.json",
        1,
        17,
        True,
        "2e6d9df2b175473a93273a726e99ba75e5efd53b3d13e033983474a155e70342",
    ),
    (
        "artifacts/reports/m2-fatal.json",
        1,
        14,
        True,
        "7a1db6b422e49b56dafc6170b13c48a885a46721f5d7bbe702b26be77f0b4c86",
    ),
    (
        "artifacts/reports/m2-screenshot.json",
        1,
        9,
        True,
        "49fdc86990dd5d8e171a90779d84f892116300680af03375df09d6fc64f0c8aa",
    ),
    (
        "artifacts/reports/m2-audio-capture.json",
        1,
        6,
        True,
        "ec93299218c8ae47596e21491eca3e14133413cbdac9468c88dad25ace8437fc",
    ),
    (
        "artifacts/reports/m3-reference.json",
        "r94-m3-reference-report-v1",
        0,
        False,
        "d4aea2fd19e39b3a6bb8b93b3b9cce098a767b0dd4801568e5e8400059792810",
    ),
    (
        "artifacts/reports/m3-runtime.json",
        "r94-m3-runtime-evidence-v2",
        26,
        True,
        "0b8c59d21b89aa0bd5ef4d0057ca451cff1e1e7bc9892d0a138b69f5b56c9890",
    ),
    (
        "artifacts/reports/m4-reference.json",
        "r94-m4-reference-report-v1",
        0,
        False,
        "2c8059669896a3be0f3f734b9f2eed0adf6f6b35fef33a35df8bd9c3fbb3c0d3",
    ),
    (
        "artifacts/reports/m4-runtime.json",
        "r94-m4-runtime-evidence-v1",
        23,
        True,
        "d8f385b0bac7c9eee7cd05f41fb838a935de7af08c887c29a166522b284b98ab",
    ),
    (
        "artifacts/reports/m4-renderer-budget.json",
        "r94-m4-uninterrupted-budget-evidence-v1",
        15,
        True,
        "212c340ce6b5fccf6e91d375dde5582fe37740c668c841c5af9d04833bafc131",
    ),
    (
        "artifacts/reports/m4-cycle-evidence.json",
        "r94-m4-native-cycle-evidence-v1",
        9,
        True,
        "c64758db76dfbb1897105568fa19b33a6598639c22699870bbbc91287d91aa13",
    ),
    (
        "artifacts/reports/m4-screenshot.json",
        "r94-m4-visual-evidence-v1",
        23,
        True,
        "52697c48fa6f2a50c3c2a2dda07705b1c9e8e7055ea88f0f20220c0ba60a1fa9",
    ),
    (
        "artifacts/reports/m4-audio-capture.json",
        "r94-m4-audio-evidence-v2",
        17,
        True,
        "0fe097143cdd0c744b16de22553daf6ba726b13641fd0d6a318fc315a8ea5e90",
    ),
    (
        "artifacts/reports/m4-gameplay-qa.json",
        "r94-m4-controller-playability-review-v1",
        7,
        True,
        "d2e29254f53bbebf19fe1d395c2151d9def39984c0ee4e1ae9cfad48afd341f7",
    ),
)
FROZEN_M4_REPLAY_SHA256 = (
    "2bdc8d142bce88625ea6970c635fcd53deaae75e8fffe57c43bbcd6613c1705e"
)
STATIC_ROM_CHECKS = {
    "image_size",
    "vector_table",
    "header_ascii",
    "header_fields",
    "rom_ram_bounds",
    "checksum",
    "region_metadata",
    "save_metadata",
}
M5_B01_CHECKS = {
    "two_fresh_independent_sessions",
    "read_only_debugger",
    "isolated_standard_pad_and_window_input",
    "symbol_bound_observation",
    "b01_campaign_abi_v3_initialized",
    "bounded_real_pad_schedule_observed",
    "right_press_hold_release_edges",
    "guest_simulated_movement_then_neutral",
    "extended_projection_checkpoints_valid",
    "two_session_checkpoint_bytes_match",
    "slice_remains_active_without_false_completion",
}
M5_B03_CHECKS = {
    "three_fresh_independent_sessions",
    "read_only_debugger",
    "isolated_standard_three_button_input",
    "fixture_symbol_binds_directory_index_2",
    "dual_breakpoint_rom_binding",
    "b03_campaign_abi_v3_initialized",
    "initial_projection_schema_crc_and_tail",
    "no_fatal_contract_or_pool_overflow",
    "actual_guest_input_schedules",
    "automatic_timeout_commit_transitions",
    "actual_zero_echo_negative_input_schedule",
    "live_two_echo_b03_scenario",
    "zero_echo_completion_rejected",
    "two_session_deterministic_parity",
    "guest_replay_ram_binds_all_three_attempts",
}
M5_B03_ACCEPTANCE = {
    "timed_switch_activated",
    "first_timeout_echo_committed",
    "two_timeout_echoes_committed",
    "security_node_exposed",
    "pressure_echo_holds_switch",
    "node_damage_owned_by_echo_replay",
    "current_blocked_west_until_open",
    "timed_door_and_commit_unlock_opened",
    "current_dashes_through_open_gate",
    "current_only_completion",
    "zero_echo_completion_rejected",
}
VISUAL_RUBRIC_CATEGORIES = (
    "Composition",
    "Silhouette readability",
    "Palette coherence",
    "Sprite readability",
    "Background readability",
    "Depth and layering",
    "Motion clarity",
    "Echo identity",
    "Hazard legibility",
    "UI hierarchy",
    "Hardware authenticity",
    "Professional finish",
)
M5_VISUAL_CAPTURE_LABELS = (
    "two_echo_armed",
    "node_disabled_door_open",
    "dash_route_open",
    "terminal_hold_19",
    "terminal_complete_20",
)
VISUAL_CRITIC_OBSERVABLE_TERMS = (
    "actor",
    "background",
    "door",
    "echo",
    "foreground",
    "hazard",
    "hud",
    "latch",
    "node",
    "outline",
    "palette",
    "pixel",
    "route",
    "scanline",
    "silhouette",
    "sprite",
    "switch",
    "terminal",
    "tile",
    "viewport",
)
VISUAL_CRITIC_OBSERVATION_TERMS = (
    "aligned",
    "bright",
    "clear",
    "closed",
    "contrasts",
    "dark",
    "distinct",
    "displays",
    "legible",
    "lit",
    "obscured",
    "open",
    "overlaps",
    "readable",
    "remains",
    "separates",
    "shows",
    "visible",
)
M2_EVIDENCE_REPORTS = (
    ("m2_controller_ram", "m2-controller.json"),
    ("m2_video_dma_ram", "m2-video-dma.json"),
    ("m2_z80_audio_runtime", "m2-z80-audio-runtime.json"),
    ("m2_fatal_fixture", "m2-fatal.json"),
    ("m2_screenshot_evidence", "m2-screenshot.json"),
    ("m2_audible_audio_evidence", "m2-audio-capture.json"),
)
M3_EVIDENCE_REPORTS = (
    ("m3_host_reference", "m3-reference.json", False, False),
    ("m3_live_runtime", "m3-runtime.json", True, True),
)
M4_EVIDENCE_REPORTS = (
    ("m4_host_reference", "m4-reference.json", False, False),
    ("m4_live_runtime_and_canonical_replay", "m4-runtime.json", True, True),
    (
        "m4_renderer_vblank_budget",
        "m4-renderer-budget.json",
        True,
        True,
    ),
    (
        "m4_m68000_cycle_budget",
        "m4-cycle-evidence.json",
        True,
        True,
    ),
    ("m4_visual_evidence", "m4-screenshot.json", True, True),
    ("m4_audio_evidence", "m4-audio-capture.json", True, True),
    ("m4_human_play_qa", "m4-gameplay-qa.json", True, True),
)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_exact_equal(first: Any, second: Any) -> bool:
    """Compare JSON values without Python's bool/int numeric coercion."""
    try:
        return json.dumps(
            first,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ) == json.dumps(
            second,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _valid_sha256_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _windows_path_identity(value: Any) -> str | None:
    """Normalize collector-emitted native-Windows paths for identity checks."""
    if not isinstance(value, str) or not value:
        return None
    return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))


def command_gate(name: str, code: int, *, required: bool = True) -> dict[str, Any]:
    return {
        "gate": name,
        "status": "pass" if code == 0 else "fail",
        "required": required,
        "exit_code": code,
    }


def host_tests_gate(*, root: Path, exit_code: int) -> dict[str, Any]:
    """Bind a successful host-test command to its complete All-suite report."""
    name = "host_unit_tests"
    report_path = root / "artifacts/reports/host-tests.json"
    report = load_json(report_path)
    errors: list[str] = []
    if exit_code != 0:
        errors.append(f"host test command exited {exit_code}")
    if report is None:
        errors.append(
            "host-tests.json is malformed"
            if report_path.is_file()
            else "host-tests.json does not exist"
        )
    else:
        if not _json_exact_equal(report.get("schema"), 1):
            errors.append("host-test report schema is not 1")
        if report.get("status") != "pass":
            errors.append("host-test report status is not pass")
        if report.get("requested_suite") != "All":
            errors.append("host-test report did not run requested_suite=All")
        runs = report.get("runs")
        if not isinstance(runs, list) or not runs:
            errors.append("host-test report has no suite rows")
            runs = []
        paths: list[str] = []
        for run in runs:
            if not isinstance(run, dict):
                errors.append("host-test suite row is malformed")
                continue
            path = run.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
            else:
                errors.append("host-test suite path is missing")
            tests_run = run.get("tests_run")
            if (
                not isinstance(tests_run, int)
                or isinstance(tests_run, bool)
                or tests_run <= 0
            ):
                errors.append(f"host-test suite {path!r} ran no tests")
            if run.get("status") != "pass":
                errors.append(f"host-test suite {path!r} did not pass")
            for field in ("failures", "errors", "skipped"):
                if not _json_exact_equal(run.get(field), 0):
                    errors.append(
                        f"host-test suite {path!r} has nonzero {field}"
                    )
        if len(paths) != len(set(paths)):
            errors.append("host-test suite paths are duplicated")
        if paths != list(HOST_TEST_SUITE_PATHS):
            errors.append(
                "host-test report does not contain the exact ordered All-suite paths"
            )
        m5_rows = [
            run for run in runs
            if isinstance(run, dict) and run.get("path") == "tests/m5"
        ]
        if len(m5_rows) != 1:
            errors.append("host-test report lacks exactly one tests/m5 row")
        binary_checks = report.get("binary_checks")
        if not isinstance(binary_checks, list):
            errors.append("host-test binary checks are malformed")
            binary_checks = []
        binary_configurations: list[str] = []
        for binary in binary_checks:
            if not isinstance(binary, dict):
                errors.append("host-test binary-check row is malformed")
                continue
            configuration = binary.get("configuration")
            if isinstance(configuration, str):
                binary_configurations.append(configuration)
            else:
                errors.append("host-test binary configuration is missing")
                continue
            expected_sha = (
                sha256(
                    root
                    / "build"
                    / f"recursion94-{configuration}.bin"
                )
                if configuration in HOST_TEST_BINARY_CONFIGURATIONS
                else None
            )
            if (
                binary.get("status") != "pass"
                or binary.get("gate_passed") is not True
                or expected_sha is None
                or binary.get("sha256") != expected_sha
            ):
                errors.append(
                    f"host-test {configuration!r} binary check is not a live-hash-bound pass"
                )
        if len(binary_configurations) != len(set(binary_configurations)):
            errors.append("host-test binary configurations are duplicated")
        if binary_configurations != list(HOST_TEST_BINARY_CONFIGURATIONS):
            errors.append(
                "host-test report does not contain exact Debug/Release binary checks"
            )
    if errors:
        return failed_gate(name, "invalid host-test evidence: " + "; ".join(errors))
    return {
        "gate": name,
        "status": "pass",
        "required": True,
        "exit_code": exit_code,
        "report_sha256": sha256(report_path),
    }


def pending_gate(name: str, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "gate": name,
        "status": "pending",
        "required": required,
        "detail": detail,
    }


def failed_gate(name: str, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "gate": name,
        "status": "fail",
        "required": required,
        "detail": detail,
    }


def _reported_rom_sha256(report: dict[str, Any]) -> Any:
    reported = report.get("rom_sha256")
    if reported is None and isinstance(report.get("rom"), dict):
        reported = report["rom"].get("sha256")
    if reported is None and isinstance(report.get("provenance"), dict):
        rom = report["provenance"].get("rom")
        if isinstance(rom, dict):
            reported = rom.get("sha256")
    return reported


def _record_errors(
    *,
    root: Path,
    record: Any,
    label: str,
    expected_path: str | None = None,
    sha_key: str = "sha256",
) -> list[str]:
    if not isinstance(record, dict):
        return [f"{label} provenance is missing or malformed"]
    relative = record.get("path")
    if not isinstance(relative, str) or not relative:
        return [f"{label} path is missing"]
    if expected_path is not None and relative != expected_path:
        return [f"{label} path {relative!r} is not {expected_path!r}"]
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return [f"{label} path escapes the repository root"]
    if not candidate.is_file():
        return [f"{label} artifact is missing: {relative}"]
    expected_sha = record.get(sha_key)
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        return [f"{label} SHA-256 is missing or malformed"]
    actual_sha = sha256(candidate)
    if actual_sha != expected_sha.lower():
        return [
            f"{label} SHA-256 drift: report {expected_sha}, current {actual_sha}"
        ]
    return []


def _source_binding_errors(root: Path, binding: Any) -> list[str]:
    if not isinstance(binding, dict):
        return ["source binding is missing or malformed"]
    sources = binding.get("sources")
    if not isinstance(sources, dict) or not sources:
        return ["source binding has no source hashes"]
    errors: list[str] = []
    normalized: dict[str, str] = {}
    for relative, expected_sha in sorted(sources.items()):
        if not isinstance(relative, str) or not isinstance(expected_sha, str):
            errors.append("source binding contains a malformed path/hash row")
            continue
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            errors.append(f"source binding path escapes root: {relative}")
            continue
        actual_sha = sha256(candidate)
        if actual_sha is None:
            errors.append(f"source binding artifact is missing: {relative}")
        elif actual_sha != expected_sha.lower():
            errors.append(
                f"source binding drift for {relative}: "
                f"report {expected_sha}, current {actual_sha}"
            )
        normalized[relative] = expected_sha
    canonical = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    bundle_sha = hashlib.sha256(canonical).hexdigest()
    if binding.get("bundle_sha256") != bundle_sha:
        errors.append("source binding bundle SHA-256 is not derived from its rows")
    translation_inputs = binding.get("translation_unit_inputs")
    if translation_inputs is not None:
        if (
            not isinstance(translation_inputs, list)
            or not translation_inputs
            or any(not isinstance(item, str) for item in translation_inputs)
            or not set(translation_inputs).issubset(sources)
        ):
            errors.append("translation-unit source binding is malformed or incomplete")
    return errors


def _exact_pass_rows(
    rows: Any,
    *,
    name_key: str,
    expected_names: set[str],
    label: str,
) -> list[str]:
    if not isinstance(rows, list):
        return [f"{label} rows are missing"]
    names = [
        item.get(name_key)
        for item in rows
        if isinstance(item, dict)
    ]
    errors: list[str] = []
    if (
        len(rows) != len(expected_names)
        or len(names) != len(rows)
        or set(names) != expected_names
        or len(set(names)) != len(names)
    ):
        errors.append(f"{label} rows do not match the required exact set")
    if any(
        not isinstance(item, dict) or item.get("status") != "pass"
        for item in rows
    ):
        errors.append(f"one or more {label} rows did not pass")
    return errors


def evidence_gate(
    name: str,
    report: dict[str, Any] | None,
    *,
    expected_rom_sha256: str | None = None,
    required: bool = True,
    require_rom_sha256: bool = False,
    require_checks: bool = False,
) -> dict[str, Any]:
    if report is None:
        return pending_gate(name, "evidence report does not exist", required=required)
    status = str(report.get("status", "")).lower()
    reported_sha = _reported_rom_sha256(report)
    if require_rom_sha256:
        sha_matches = (
            expected_rom_sha256 is not None
            and reported_sha is not None
            and reported_sha == expected_rom_sha256
        )
    else:
        sha_matches = (
            expected_rom_sha256 is None
            or reported_sha is None
            or reported_sha == expected_rom_sha256
        )
    checks = report.get("checks")
    checks_pass = (
        (checks is None and not require_checks)
        or (
            isinstance(checks, list)
            and (bool(checks) or not require_checks)
            and all(
                isinstance(item, dict) and item.get("status") == "pass"
                for item in checks
            )
        )
    )
    if status == "pass" and sha_matches and checks_pass:
        return {"gate": name, "status": "pass", "required": required}
    explicit_check_failure = isinstance(checks, list) and any(
        isinstance(item, dict) and item.get("status") == "fail"
        for item in checks
    )
    gate_status = (
        "fail"
        if (
            status == "fail"
            or not sha_matches
            or explicit_check_failure
            or (status == "pass" and not checks_pass)
        )
        else "pending"
    )
    return {
        "gate": name,
        "status": gate_status,
        "required": required,
        "detail": (
            "evidence ROM hash is missing or does not match current debug ROM"
            if not sha_matches
            else (
                "one or more evidence checks did not pass"
                if not checks_pass
                else f"evidence status is {status or 'unspecified'}"
            )
        ),
    }


def frozen_prior_milestones_gate(*, root: Path) -> dict[str, Any]:
    """Verify immutable M2-M4 evidence without rebinding it to a new ROM."""
    name = "frozen_m2_m4_evidence"
    archive_root = root / FROZEN_M4_ARCHIVE
    required_files = {
        archive_root / "recursion94-debug.bin": FROZEN_M4_DEBUG_SHA256,
        archive_root / "recursion94-release.bin": FROZEN_M4_RELEASE_SHA256,
        root / "artifacts/replays/B01.r94r": FROZEN_M4_REPLAY_SHA256,
    }
    missing: list[str] = []
    errors: list[str] = []
    for path, expected_sha in required_files.items():
        actual_sha = sha256(path)
        if actual_sha is None:
            missing.append(path.relative_to(root).as_posix())
        elif actual_sha != expected_sha:
            errors.append(
                f"{path.relative_to(root).as_posix()} SHA-256 "
                f"{actual_sha} is not frozen {expected_sha}"
            )

    for (
        relative,
        expected_schema,
        expected_check_count,
        rom_bound,
        expected_report_sha,
    ) in FROZEN_PRIOR_REPORTS:
        path = root / relative
        report = load_json(path)
        if report is None:
            if path.is_file():
                errors.append(f"{relative} is not a valid JSON object")
            else:
                missing.append(relative)
            continue
        if sha256(path) != expected_report_sha:
            errors.append(f"{relative} bytes differ from the frozen report")
        if report.get("schema") != expected_schema:
            errors.append(f"{relative} has an unexpected schema")
        if report.get("status") != "pass":
            errors.append(f"{relative} is not a passing report")
        checks = report.get("checks")
        if expected_check_count:
            if (
                not isinstance(checks, list)
                or len(checks) != expected_check_count
                or any(
                    not isinstance(item, dict)
                    or item.get("status") != "pass"
                    for item in checks
                )
            ):
                errors.append(
                    f"{relative} does not retain its "
                    f"{expected_check_count} passing checks"
                )
        if rom_bound and _reported_rom_sha256(report) != FROZEN_M4_DEBUG_SHA256:
            errors.append(
                f"{relative} is not bound to the frozen M4 Debug ROM"
            )

    for filename in ("recursion94-debug.bin", "recursion94-release.bin"):
        path = archive_root / filename
        if not path.is_file():
            continue
        try:
            from tools.rom_tools.rom_image import validate_rom

            static = validate_rom(
                path.read_bytes(),
                expected_size=0x100000,
                require_all_vectors=True,
            )
            if static.get("status") != "pass":
                errors.append(f"{filename} failed archived static validation")
        except Exception as error:
            errors.append(
                f"{filename} archived static validation raised "
                f"{type(error).__name__}: {error}"
            )

    if errors:
        return failed_gate(name, "invalid frozen evidence: " + "; ".join(errors))
    if missing:
        return pending_gate(
            name,
            "frozen evidence is missing: " + ", ".join(sorted(missing)),
        )
    return {
        "gate": name,
        "status": "pass",
        "required": True,
        "debug_rom_sha256": FROZEN_M4_DEBUG_SHA256,
        "release_rom_sha256": FROZEN_M4_RELEASE_SHA256,
        "detail": (
            "M2-M4 reports remain bound to the archived historical ROMs; "
            "no current-ROM equivalence is claimed"
        ),
    }


def static_rom_report_gate(
    *,
    root: Path,
    name: str,
    report_filename: str,
    rom_relative_path: str,
) -> dict[str, Any]:
    report_path = root / "artifacts/reports" / report_filename
    report = load_json(report_path)
    if report is None:
        if report_path.is_file():
            return failed_gate(name, f"{report_filename} is malformed")
        return pending_gate(name, f"{report_filename} does not exist")
    current_sha = sha256(root / rom_relative_path)
    if current_sha is None:
        return pending_gate(name, f"{rom_relative_path} does not exist")
    errors: list[str] = []
    if report.get("status") != "pass":
        errors.append("static report status is not pass")
    facts = report.get("facts")
    if not isinstance(facts, dict) or facts.get("sha256") != current_sha:
        errors.append("static report SHA-256 is stale or missing")
    errors.extend(
        _exact_pass_rows(
            report.get("checks"),
            name_key="check",
            expected_names=STATIC_ROM_CHECKS,
            label="static ROM checks",
        )
    )
    if errors:
        return failed_gate(name, "; ".join(errors))
    return {
        "gate": name,
        "status": "pass",
        "required": True,
        "rom_sha256": current_sha,
    }


def _m5_runtime_common_errors(
    *,
    root: Path,
    report: dict[str, Any],
    expected_schema: str,
    expected_scope: str,
    expected_rom_path: str,
    expected_symbol_path: str,
    expected_checks: set[str],
) -> list[str]:
    errors: list[str] = []
    rom_sha = sha256(root / expected_rom_path)
    symbol_sha = sha256(root / expected_symbol_path)
    if report.get("schema") != expected_schema:
        errors.append("unexpected report schema")
    if report.get("scope") != expected_scope:
        errors.append("unexpected report scope")
    if report.get("status") != "pass":
        errors.append("report status is not pass")
    if report.get("rom_sha256") != rom_sha or rom_sha is None:
        errors.append("report ROM SHA-256 is stale or missing")
    if report.get("symbol_map_sha256") != symbol_sha or symbol_sha is None:
        errors.append("report symbol-map SHA-256 is stale or missing")
    errors.extend(
        _exact_pass_rows(
            report.get("checks"),
            name_key="check",
            expected_names=expected_checks,
            label="runtime checks",
        )
    )
    if report.get("debugger_write_packets") != []:
        errors.append("debugger write packet audit is not exactly empty")

    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        return errors + ["runtime provenance is missing"]
    errors.extend(
        _record_errors(
            root=root,
            record=provenance.get("rom"),
            label="runtime ROM",
            expected_path=expected_rom_path,
        )
    )
    errors.extend(
        _record_errors(
            root=root,
            record=provenance.get("symbol_map"),
            label="runtime symbol map",
            expected_path=expected_symbol_path,
        )
    )
    errors.extend(
        _record_errors(
            root=root,
            record=provenance.get("transcript"),
            label="runtime transcript",
        )
    )
    transcript = provenance.get("transcript")
    if not isinstance(transcript, dict) or not isinstance(
        transcript.get("commands"), int
    ) or transcript.get("commands", 0) <= 0:
        errors.append("runtime transcript command count is missing")
    errors.extend(
        _source_binding_errors(root, provenance.get("source_bindings"))
    )
    return errors


def _session_provenance_errors(
    report: dict[str, Any],
    *,
    session_ids: set[str],
    include_negative: bool = False,
) -> list[str]:
    sessions = report.get("sessions")
    if not isinstance(sessions, list):
        return ["runtime sessions are missing"]
    rows = list(sessions)
    if include_negative:
        negative = report.get("negative_session")
        if isinstance(negative, dict):
            rows.append(negative)
        else:
            return ["negative runtime session is missing"]
    ids = [
        row.get("session_id")
        for row in rows
        if isinstance(row, dict)
    ]
    errors: list[str] = []
    if (
        len(rows) != len(session_ids)
        or len(ids) != len(rows)
        or set(ids) != session_ids
    ):
        errors.append("runtime session IDs do not match the exact fresh-run set")
    process_ids: list[int] = []
    for row in rows:
        metadata = row.get("metadata") if isinstance(row, dict) else None
        if not isinstance(metadata, dict):
            errors.append("runtime session metadata is missing")
            continue
        process_id = metadata.get("process_id")
        if not isinstance(process_id, int) or process_id <= 0:
            errors.append("runtime session process ID is missing")
        else:
            process_ids.append(process_id)
        if not isinstance(metadata.get("transcript_sha256"), str):
            errors.append("runtime session transcript SHA-256 is missing")
        runtime_copy = metadata.get("runtime_copy")
        if (
            not isinstance(runtime_copy, dict)
            or runtime_copy.get("status") != "pass"
            or runtime_copy.get("temporary_copy_removed_after_session") is not True
        ):
            errors.append("runtime session isolation provenance did not pass")
    if len(set(process_ids)) != len(process_ids):
        errors.append("runtime sessions did not use distinct process IDs")
    return errors


def m5_b01_runtime_gate(
    *, root: Path, report: dict[str, Any] | None
) -> dict[str, Any]:
    name = "m5_b01_live_runtime"
    if report is None:
        if (root / "artifacts/reports/m5-runtime.json").is_file():
            return failed_gate(name, "m5-runtime.json is malformed")
        return pending_gate(name, "m5-runtime.json does not exist")
    errors = _m5_runtime_common_errors(
        root=root,
        report=report,
        expected_schema="r94-m5-runtime-evidence-v1",
        expected_scope="Milestone5",
        expected_rom_path="build/recursion94-debug.bin",
        expected_symbol_path="build/recursion94-debug.sym",
        expected_checks=M5_B01_CHECKS,
    )
    errors.extend(
        _session_provenance_errors(
            report,
            session_ids={"session_1", "session_2"},
        )
    )
    try:
        from tools.m5_runtime_evidence import _source_bindings

        if _source_bindings(root) != report.get("provenance", {}).get(
            "source_bindings"
        ):
            errors.append("B01 source binding is not the complete live contract")
    except Exception as error:
        errors.append(
            "B01 source-binding validation raised "
            f"{type(error).__name__}: {error}"
        )
    if errors:
        return failed_gate(name, "invalid B01 runtime evidence: " + "; ".join(errors))
    return {"gate": name, "status": "pass", "required": True}


def m5_b03_runtime_gate(
    *, root: Path, report: dict[str, Any] | None
) -> dict[str, Any]:
    name = "m5_b03_live_combined_room"
    if report is None:
        if (root / "artifacts/reports/m5-b03-runtime.json").is_file():
            return failed_gate(name, "m5-b03-runtime.json is malformed")
        return pending_gate(name, "m5-b03-runtime.json does not exist")
    errors = _m5_runtime_common_errors(
        root=root,
        report=report,
        expected_schema="r94-m5-b03-runtime-evidence-v1",
        expected_scope="Milestone5/B03",
        expected_rom_path="build/level-B03/recursion94-debug.bin",
        expected_symbol_path="build/level-B03/recursion94-debug.sym",
        expected_checks=M5_B03_CHECKS,
    )
    if report.get("acceptance_status") != "pass":
        errors.append("B03 acceptance status is not pass")
    errors.extend(
        _exact_pass_rows(
            report.get("acceptance"),
            name_key="claim",
            expected_names=M5_B03_ACCEPTANCE,
            label="B03 acceptance",
        )
    )
    errors.extend(
        _session_provenance_errors(
            report,
            session_ids={
                "positive_session_1",
                "positive_session_2",
                "zero_echo_negative",
            },
            include_negative=True,
        )
    )
    try:
        from tools.m5_b03_runtime_evidence import _source_bindings

        if _source_bindings(root) != report.get("provenance", {}).get(
            "source_bindings"
        ):
            errors.append("B03 source binding is not the complete live contract")
    except Exception as error:
        errors.append(
            "B03 source-binding validation raised "
            f"{type(error).__name__}: {error}"
        )
    schedule = report.get("schedule")
    if (
        not isinstance(schedule, dict)
        or schedule.get("attempt_lengths") != [720, 720, 292]
    ):
        errors.append("B03 authoritative schedule lengths are missing or stale")
    negative_session = report.get("negative_session")
    if (
        not isinstance(negative_session, dict)
        or not isinstance(
            negative_session.get("negative_commanded_inputs"), list
        )
        or len(negative_session["negative_commanded_inputs"]) != 240
    ):
        errors.append("B03 negative schedule is not exactly 240 steps")
    replay = report.get("guest_replay_ram")
    errors.extend(
        _record_errors(
            root=root,
            record=replay,
            label="B03 replay RAM",
            expected_path="artifacts/replays/m5-b03-live-replay-ram.bin",
        )
    )
    if not isinstance(replay, dict) or replay.get("bytes") != 5120:
        errors.append("B03 replay RAM byte count is not 5120")
    if errors:
        return failed_gate(name, "invalid B03 runtime evidence: " + "; ".join(errors))
    return {"gate": name, "status": "pass", "required": True}


def m5_budget_gate(
    *, root: Path, report: dict[str, Any] | None
) -> dict[str, Any]:
    name = "m5_b03_hardware_budget"
    if report is None:
        if (root / "artifacts/reports/m5-b03-budget.json").is_file():
            return failed_gate(name, "m5-b03-budget.json is malformed")
        return pending_gate(name, "m5-b03-budget.json does not exist")
    errors: list[str] = []
    if report.get("schema") != "r94-m5-b03-budget-evidence-v3":
        errors.append("unexpected budget report schema")
    if report.get("status") != "pass":
        errors.append("budget report status is not pass")
    if report.get("rom_sha256") != sha256(
        root / "build/level-B03/recursion94-debug.bin"
    ):
        errors.append("budget report ROM SHA-256 is stale or missing")
    if report.get("symbol_map_sha256") != sha256(
        root / "build/level-B03/recursion94-debug.sym"
    ):
        errors.append("budget report symbol-map SHA-256 is stale or missing")
    try:
        from tools.m5_budget_evidence import report_contract_errors

        errors.extend(report_contract_errors(report, root=root))
    except Exception as error:
        errors.append(
            "budget semantic validation raised "
            f"{type(error).__name__}: {error}"
        )
    if errors:
        return failed_gate(name, "invalid M5 budget evidence: " + "; ".join(errors))
    return {"gate": name, "status": "pass", "required": True}


def m5_visual_capture_gate(
    *, root: Path, report: dict[str, Any] | None
) -> dict[str, Any]:
    name = "m5_b03_objective_visual_capture"
    if report is None:
        if (root / "artifacts/reports/m5-b03-visual.json").is_file():
            return failed_gate(name, "m5-b03-visual.json is malformed")
        return pending_gate(name, "m5-b03-visual.json does not exist")
    errors: list[str] = []
    if report.get("schema") != "r94-m5-b03-visual-evidence-v1":
        errors.append("unexpected visual-capture schema")
    if report.get("status") != "pass":
        errors.append("visual-capture status is not pass")
    if report.get("acceptance_status") != "evidence_ready":
        errors.append("objective visual evidence is not critic-ready")
    rom_sha = sha256(root / "build/level-B03/recursion94-debug.bin")
    symbol_sha = sha256(root / "build/level-B03/recursion94-debug.sym")
    if report.get("rom_sha256") != rom_sha or rom_sha is None:
        errors.append("visual-capture ROM SHA-256 is stale or missing")
    if report.get("symbol_map_sha256") != symbol_sha or symbol_sha is None:
        errors.append("visual-capture symbol SHA-256 is stale or missing")
    captures = report.get("captures")
    if (
        report.get("capture_count") != 5
        or not isinstance(captures, list)
        or len(captures) != 5
    ):
        errors.append("visual-capture report does not contain exactly five states")
        captures = []
    png_hashes: list[str] = []
    pixel_hashes: list[str] = []
    process_ids: list[int] = []
    capture_names: list[str] = []
    for capture in captures:
        if not isinstance(capture, dict):
            errors.append("visual capture row is malformed")
            continue
        capture_name = capture.get("name")
        if isinstance(capture_name, str):
            capture_names.append(capture_name)
        screenshot = capture.get("screenshot")
        if not isinstance(screenshot, dict):
            errors.append("visual capture screenshot record is missing")
            continue
        errors.extend(
            _record_errors(
                root=root,
                record=screenshot,
                label=f"visual PNG {capture.get('name', '?')}",
            )
        )
        png_sha = screenshot.get("sha256")
        pixel_sha = screenshot.get("pixel_sha256")
        if isinstance(png_sha, str):
            png_hashes.append(png_sha)
        if isinstance(pixel_sha, str) and len(pixel_sha) == 64:
            pixel_hashes.append(pixel_sha)
        else:
            errors.append("visual capture pixel SHA-256 is missing")
        if screenshot.get("publication_verified") is not True:
            errors.append("visual PNG publication was not verified")
        if (
            not isinstance(screenshot.get("published_mtime_ns"), int)
            or isinstance(screenshot.get("published_mtime_ns"), bool)
            or screenshot.get("published_mtime_ns", 0) <= 0
        ):
            errors.append("visual PNG publication time is missing")
        facts = capture.get("objective_state_facts")
        if (
            not isinstance(facts, dict)
            or not facts
            or any(value is not True for value in facts.values())
        ):
            errors.append("visual objective state facts are incomplete")
        session = capture.get("session")
        process_id = session.get("process_id") if isinstance(session, dict) else None
        if isinstance(process_id, int) and process_id > 0:
            process_ids.append(process_id)
        else:
            errors.append("visual capture process ID is missing")
    if len(set(png_hashes)) != 5 or len(set(pixel_hashes)) != 5:
        errors.append(
            "visual capture PNG/pixel identities are not five distinct hashes"
        )
    if len(set(process_ids)) != 5:
        errors.append("visual captures did not use five fresh processes")

    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(item, dict) or item.get("status") != "pass"
            for item in checks
        )
    ):
        errors.append("one or more objective visual checks did not pass")
    objective = report.get("objective_only")
    if not (
        isinstance(objective, dict)
        and objective.get("subjective_scores_emitted") is False
        and objective.get("rubric_review_performed") is False
        and objective.get("critic_handoff_required") is True
    ):
        errors.append("objective collector improperly claims a subjective review")
    handoff = report.get("critic_handoff")
    if not (
        isinstance(handoff, dict)
        and handoff.get("reserved_report_path")
        == "artifacts/reports/m5-b03-visual-critic.json"
        and handoff.get("reserved_schema")
        == "r94-m5-b03-independent-visual-review-v1"
        and isinstance(handoff.get("capture_manifest_sha256"), str)
        and len(handoff["capture_manifest_sha256"]) == 64
        and isinstance(handoff.get("captures"), list)
        and len(handoff["captures"]) == 5
    ):
        errors.append("visual critic handoff manifest is missing or malformed")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("visual provenance is missing")
    else:
        for key, label, expected_path in (
            (
                "rom",
                "visual ROM",
                "build/level-B03/recursion94-debug.bin",
            ),
            (
                "symbol_map",
                "visual symbol map",
                "build/level-B03/recursion94-debug.sym",
            ),
            (
                "transcript",
                "visual transcript",
                "artifacts/traces/m5-b03-visual-gdb-transcript.log",
            ),
            (
                "emulator",
                "visual emulator",
                ".toolcache/blastem/blastem.exe",
            ),
        ):
            errors.extend(
                _record_errors(
                    root=root,
                    record=provenance.get(key),
                    label=label,
                    expected_path=expected_path,
                )
            )
        errors.extend(
            _record_errors(
                root=root,
                record=provenance.get("default_config"),
                label="visual default config",
                expected_path=".toolcache/blastem/default.cfg",
                sha_key="source_sha256",
            )
        )
        transcript = provenance.get("transcript")
        if (
            not isinstance(transcript, dict)
            or transcript.get("guest_write_packets") != []
        ):
            errors.append("visual transcript write audit is not exactly empty")
        errors.extend(
            _source_binding_errors(root, provenance.get("source_bindings"))
        )

    try:
        from tools import m4_visual_evidence as m4_visual
        from tools import m5_b03_runtime_evidence as b03_visual
        from tools.m2_evidence import parse_vasm_symbols
        from tools.m3_runtime_evidence import verify_controller_bindings
        from tools.m5_budget_evidence import (
            PINNED_DEFAULT_CONFIG_SHA256,
            PINNED_EMULATOR_SHA256,
        )
        from tools.m5_visual_evidence import (
            CAPTURE_PLAN,
            REQUIRED_SYMBOLS,
            _source_bindings,
            audit_visual_trace,
            build_visual_report,
            derive_renderer_return_address,
        )

        live_provenance = (
            provenance if isinstance(provenance, dict) else {}
        )
        if not _json_exact_equal(
            _source_bindings(root),
            live_provenance.get("source_bindings"),
        ):
            errors.append(
                "visual source binding is not the complete live contract"
            )

        rom_path = root / "build/level-B03/recursion94-debug.bin"
        symbol_path = root / "build/level-B03/recursion94-debug.sym"
        live_symbols = parse_vasm_symbols(symbol_path)
        missing_symbols = [
            symbol for symbol in REQUIRED_SYMBOLS
            if symbol not in live_symbols
        ]
        if missing_symbols:
            raise ValueError(
                "live visual symbol map is missing required symbols "
                f"{missing_symbols}"
            )
        if live_symbols.get("DEBUG_START_ENCOUNTER") != 2:
            errors.append(
                "live visual symbol map does not select B03 encounter 2"
            )
        if live_provenance.get("rom", {}).get(
            "expected_sha256"
        ) != rom_sha:
            errors.append(
                "visual collection-time expected ROM hash is missing or stale"
            )
        if live_provenance.get("symbol_map", {}).get(
            "expected_sha256"
        ) != symbol_sha:
            errors.append(
                "visual collection-time expected symbol hash is missing or stale"
            )
        required_symbol_record = live_provenance.get(
            "symbol_map", {}
        ).get("required_symbols")
        expected_symbol_record = {
            symbol: f"{live_symbols[symbol]:08X}"
            for symbol in REQUIRED_SYMBOLS
        }
        if not _json_exact_equal(
            required_symbol_record, expected_symbol_record
        ):
            errors.append(
                "visual required-symbol record differs from the live map"
            )

        rom_bytes = rom_path.read_bytes()
        renderer_call = derive_renderer_return_address(
            rom_bytes, live_symbols
        )
        dma_commit_call = m4_visual.derive_post_dma_commit_address(
            rom_bytes, live_symbols
        )
        if not _json_exact_equal(
            live_provenance.get("renderer_call"), renderer_call
        ):
            errors.append(
                "visual renderer-call derivation differs from the live ROM"
            )
        if not _json_exact_equal(
            live_provenance.get("dma_commit_call"), dma_commit_call
        ):
            errors.append(
                "visual DMA-commit derivation differs from the live ROM"
            )
        pre_step_binding = b03_visual.verify_pre_step_rom_binding(
            rom_path, live_symbols
        )
        if not _json_exact_equal(
            live_provenance.get("pre_step_rom_binding"), pre_step_binding
        ):
            errors.append(
                "visual pre-step call binding differs from the live ROM"
            )

        default_config_path = root / ".toolcache/blastem/default.cfg"
        expected_default_config = {
            "path": ".toolcache/blastem/default.cfg",
            **verify_controller_bindings(default_config_path),
        }
        if not _json_exact_equal(
            live_provenance.get("default_config"), expected_default_config
        ):
            errors.append(
                "visual controller/default.cfg contract differs from live input"
            )
        if live_provenance.get("emulator", {}).get(
            "sha256"
        ) != PINNED_EMULATOR_SHA256:
            errors.append("visual emulator is not the pinned BlastEm binary")
        if expected_default_config.get(
            "source_sha256"
        ) != PINNED_DEFAULT_CONFIG_SHA256:
            errors.append("visual default.cfg is not the pinned configuration")

        runtime_directories: list[str] = []
        profile_paths: list[str] = []
        screenshot_paths: list[str] = []
        save_paths: list[str] = []
        vgm_paths: list[str] = []
        expected_allowed_packets = [
            "Z0 software breakpoint insert",
            "z0 software breakpoint remove",
            "c continue",
            "m bounded guest-memory read",
            "p read-only register read",
        ]
        for capture in captures:
            if not isinstance(capture, dict):
                continue
            capture_name = str(capture.get("name", "?"))
            session = capture.get("session")
            if not isinstance(session, dict):
                errors.append(
                    f"visual session provenance is missing for {capture_name}"
                )
                continue
            runtime_copy = session.get("runtime_copy")
            if not isinstance(runtime_copy, dict):
                errors.append(
                    f"visual runtime-copy provenance is missing for {capture_name}"
                )
                runtime_copy = {}
            runtime_directory = runtime_copy.get("temporary_directory")
            if isinstance(runtime_directory, str) and runtime_directory:
                runtime_directories.append(runtime_directory)
            else:
                errors.append(
                    f"visual runtime-copy directory is missing for {capture_name}"
                )
            runtime_ok = (
                runtime_copy.get("status") == "pass"
                and runtime_copy.get("executable_sha256")
                == PINNED_EMULATOR_SHA256
                and runtime_copy.get("default_config_sha256")
                == PINNED_DEFAULT_CONFIG_SHA256
                and runtime_copy.get(
                    "temporary_copy_removed_after_session"
                )
                is True
                and isinstance(runtime_copy.get("source_directory"), str)
                and bool(runtime_copy.get("source_directory"))
                and isinstance(runtime_copy.get("bundled_override"), str)
                and bool(runtime_copy.get("bundled_override"))
            )
            if not runtime_ok:
                errors.append(
                    f"visual runtime-copy contract failed for {capture_name}"
                )

            controller = session.get("controller_profile")
            if not isinstance(controller, dict):
                errors.append(
                    f"visual controller-profile provenance is missing for {capture_name}"
                )
                controller = {}
            isolation = controller.get("session_isolation")
            if not isinstance(isolation, dict):
                isolation = {}
            screenshot_binding = controller.get("screenshot_binding")
            if not isinstance(screenshot_binding, dict):
                screenshot_binding = {}
            profile_path = controller.get("path")
            if isinstance(profile_path, str) and profile_path:
                profile_paths.append(profile_path)
            for key, collection in (
                ("screenshot_path", screenshot_paths),
                ("save_path", save_paths),
                ("vgm_path", vgm_paths),
            ):
                value = isolation.get(key)
                if isinstance(value, str) and value:
                    collection.append(value)
            controller_ok = (
                controller.get("status") == "pass"
                and controller.get("source_sha256")
                == PINNED_DEFAULT_CONFIG_SHA256
                and controller.get("port_1_assignment") == "gamepad3.1"
                and _valid_sha256_text(
                    controller.get("device_profile_sha256_before_paths")
                )
                and _valid_sha256_text(controller.get("sha256"))
                and _json_exact_equal(
                    controller.get("runtime_copy"), runtime_copy
                )
                and isolation.get("status") == "pass"
                and isolation.get("config_in_exe_dir") is True
                and isolation.get("config_path") == profile_path
                and _valid_sha256_text(isolation.get("config_sha256"))
                and isolation.get("config_sha256")
                == isolation.get(
                    "config_sha256_before_screenshot_binding"
                )
                and screenshot_binding.get("status") == "pass"
                and screenshot_binding.get("removed_default_key") == "p"
                and screenshot_binding.get("installed_config_key") == "f1"
                and screenshot_binding.get("only_ui_screenshot_binding")
                is True
                and screenshot_binding.get("windows_key")
                == {
                    "name": "F1",
                    "virtual_key": 0x70,
                    "scan_code": 0x3B,
                    "extended": False,
                }
                and screenshot_binding.get("config_sha256")
                == controller.get("sha256")
            )
            if not controller_ok:
                errors.append(
                    f"visual controller isolation contract failed for {capture_name}"
                )
            path_relationship_ok = False
            if (
                isinstance(runtime_directory, str)
                and runtime_directory
                and isinstance(profile_path, str)
                and profile_path
            ):
                runtime_identity = _windows_path_identity(runtime_directory)
                owned_root = ntpath.dirname(runtime_identity or "")
                expected_data_root = ntpath.join(
                    owned_root, "session-data"
                )
                path_relationship_ok = (
                    runtime_identity is not None
                    and ntpath.basename(runtime_identity) == "blastem"
                    and _windows_path_identity(
                        runtime_copy.get("source_directory")
                    )
                    == _windows_path_identity(
                        (root / ".toolcache/blastem").resolve().as_posix()
                    )
                    and _windows_path_identity(
                        runtime_copy.get("bundled_override")
                    )
                    == ntpath.join(runtime_identity, "blastem.cfg")
                    and _windows_path_identity(profile_path)
                    == ntpath.join(runtime_identity, "blastem.cfg")
                    and _windows_path_identity(isolation.get("save_path"))
                    == ntpath.join(
                        expected_data_root, "saves", "$romname"
                    )
                    and _windows_path_identity(
                        isolation.get("screenshot_path")
                    )
                    == ntpath.join(expected_data_root, "screenshots")
                    and _windows_path_identity(isolation.get("vgm_path"))
                    == ntpath.join(expected_data_root, "vgm")
                )
            if not path_relationship_ok:
                errors.append(
                    f"visual owned-path relationships failed for {capture_name}"
                )

            screenshot = capture.get("screenshot")
            ownership = (
                screenshot.get("window_ownership", {})
                if isinstance(screenshot, dict)
                else {}
            )
            timing_ok = (
                isinstance(screenshot, dict)
                and isinstance(screenshot.get("mtime_ns"), int)
                and not isinstance(screenshot.get("mtime_ns"), bool)
                and screenshot.get("mtime_ns", 0) > 0
                and isinstance(screenshot.get("captured_utc"), str)
                and bool(screenshot.get("captured_utc"))
                and isinstance(screenshot.get("staging_path"), str)
                and bool(screenshot.get("staging_path"))
                and ownership.get("isolated_screenshot_path")
                == isolation.get("screenshot_path")
                and ownership.get("source_sha256")
                == screenshot.get("sha256")
                and isinstance(ownership.get("isolated_source_name"), str)
                and bool(ownership.get("isolated_source_name"))
            )
            if not timing_ok:
                errors.append(
                    f"visual PNG timing/window lineage failed for {capture_name}"
                )

            gdb = capture.get("gdb")
            if not (
                isinstance(gdb, dict)
                and gdb.get("guest_write_packets") == []
                and gdb.get("guest_memory_register_pc_writes") is False
                and gdb.get("all_stops_exact") is True
                and _json_exact_equal(
                    gdb.get("allowed_packets"), expected_allowed_packets
                )
            ):
                errors.append(
                    f"visual per-session GDB policy failed for {capture_name}"
                )

        for label, values in (
            ("runtime-copy directories", runtime_directories),
            ("controller profiles", profile_paths),
            ("screenshot directories", screenshot_paths),
            ("save paths", save_paths),
            ("VGM paths", vgm_paths),
        ):
            identities = [_windows_path_identity(value) for value in values]
            if (
                len(values) != len(CAPTURE_PLAN)
                or any(identity is None for identity in identities)
                or len(set(identities)) != len(CAPTURE_PLAN)
            ):
                errors.append(
                    f"visual sessions do not have five distinct {label}"
                )

        transcript_record = live_provenance.get("transcript")
        transcript_lines: list[str] | None = None
        if isinstance(transcript_record, dict):
            transcript_relative = transcript_record.get("path")
            if isinstance(transcript_relative, str):
                transcript_path = (root / transcript_relative).resolve()
                try:
                    transcript_path.relative_to(root.resolve())
                except ValueError:
                    transcript_path = Path()
                if transcript_path.is_file():
                    transcript_lines = transcript_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
        if transcript_lines is None:
            errors.append("live visual transcript could not be read")
        else:
            command_count = sum(
                line.startswith("> ") for line in transcript_lines
            )
            if not _json_exact_equal(
                transcript_record.get("lines"), len(transcript_lines)
            ):
                errors.append("visual transcript line count is stale")
            if not _json_exact_equal(
                transcript_record.get("commands"), command_count
            ):
                errors.append("visual transcript command count is stale")
            captures_by_name = {
                capture.get("name"): capture
                for capture in captures
                if isinstance(capture, dict)
            }
            for spec in CAPTURE_PLAN:
                begin_marker = f"# visual_{spec.name} begin"
                end_marker = f"# visual_{spec.name} end"
                begin_indices = [
                    index for index, line in enumerate(transcript_lines)
                    if line == begin_marker
                ]
                end_indices = [
                    index for index, line in enumerate(transcript_lines)
                    if line == end_marker
                ]
                if (
                    len(begin_indices) != 1
                    or len(end_indices) != 1
                    or begin_indices[0] >= end_indices[0]
                ):
                    errors.append(
                        f"visual transcript session range is invalid for {spec.name}"
                    )
                    continue
                session_lines = transcript_lines[
                    begin_indices[0] : end_indices[0] + 1
                ]
                session_payload = (
                    "\n".join(session_lines) + "\n"
                ).encode("utf-8")
                gdb = captures_by_name.get(spec.name, {}).get("gdb", {})
                expected_session_commands = sum(
                    line.startswith("> ") for line in session_lines
                )
                if not _json_exact_equal(
                    gdb.get("commands"), expected_session_commands
                ):
                    errors.append(
                        f"visual GDB command count is stale for {spec.name}"
                    )
                if gdb.get("transcript_sha256") != hashlib.sha256(
                    session_payload
                ).hexdigest():
                    errors.append(
                        f"visual session transcript hash is stale for {spec.name}"
                    )
            recomputed_audit = audit_visual_trace(
                transcript_lines,
                captures,
                pre_step=live_symbols["MainLoopGameplayStepCall"],
                post_step=live_symbols["MainLoopPostGameplayStep"],
                renderer_return=renderer_call["return_address"],
                dma_commit_entry=dma_commit_call["target_address"],
                vblank=live_symbols["VBlankInterrupt"],
                symbols=live_symbols,
            )
            if recomputed_audit.get("status") != "pass":
                errors.append("live visual transcript audit did not pass")
            if not _json_exact_equal(
                recomputed_audit,
                live_provenance.get("capture_protocol_audit"),
            ):
                errors.append(
                    "stored visual capture-protocol audit is stale"
                )

        specs_by_name = {spec.name: spec for spec in CAPTURE_PLAN}
        if set(capture_names) != set(specs_by_name):
            errors.append("visual capture names differ from the required plan")
        analyzed_pngs = 0
        for capture in captures:
            if not isinstance(capture, dict):
                continue
            spec = specs_by_name.get(capture.get("name"))
            screenshot = capture.get("screenshot")
            if spec is None or not isinstance(screenshot, dict):
                continue
            relative = screenshot.get("path")
            if relative != spec.output:
                errors.append(
                    f"visual PNG path for {spec.name} is not canonical"
                )
                continue
            png_path = (root / relative).resolve()
            try:
                png_path.relative_to(root.resolve())
            except ValueError:
                errors.append(f"visual PNG path for {spec.name} escapes root")
                continue
            analyzed = m4_visual.analyze_png(
                png_path, relative_path=relative
            )
            analyzed_pngs += 1
            stale_fields = [
                field for field, value in analyzed.items()
                if not _json_exact_equal(screenshot.get(field), value)
            ]
            if stale_fields:
                errors.append(
                    f"visual PNG analysis for {spec.name} differs in "
                    f"{stale_fields}"
                )
        if analyzed_pngs != len(CAPTURE_PLAN):
            errors.append("not every required visual PNG was independently decoded")

        rebuilt = build_visual_report(
            rom_sha256=report.get("rom_sha256", ""),
            symbol_sha256=report.get("symbol_map_sha256", ""),
            symbols=live_symbols,
            captures=captures,
            provenance=live_provenance,
        )
        if not _json_exact_equal(rebuilt, report):
            errors.append(
                "visual report is not the exact live-symbol semantic rebuild"
            )
    except Exception as error:
        errors.append(
            "visual semantic validation raised "
            f"{type(error).__name__}: {error}"
        )
    if errors:
        return failed_gate(
            name, "invalid M5 objective visual evidence: " + "; ".join(errors)
        )
    return {"gate": name, "status": "pass", "required": True}


def _has_concrete_visual_observation(value: Any) -> bool:
    """Require an observable subject and an explicit visual finding."""
    if not isinstance(value, str) or not value.strip():
        return False
    narrative = re.sub(r"\[[^\]\r\n]*\]", " ", value.casefold())

    def contains_any(terms: tuple[str, ...]) -> bool:
        return any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                narrative,
            )
            is not None
            for term in terms
        )

    return contains_any(VISUAL_CRITIC_OBSERVABLE_TERMS) and contains_any(
        VISUAL_CRITIC_OBSERVATION_TERMS
    )


def m5_visual_critic_gate(
    *,
    root: Path,
    report: dict[str, Any] | None,
    capture_report: dict[str, Any] | None,
) -> dict[str, Any]:
    name = "m5_b03_visual_critic"
    if report is None:
        if (
            root / "artifacts/reports/m5-b03-visual-critic.json"
        ).is_file():
            return failed_gate(name, "m5-b03-visual-critic.json is malformed")
        return pending_gate(name, "m5-b03-visual-critic.json does not exist")
    if capture_report is None:
        return failed_gate(
            name,
            "critic artifact exists without its objective visual report",
        )
    errors: list[str] = []
    if report.get("schema") != "r94-m5-b03-independent-visual-review-v1":
        errors.append("unexpected independent visual-review schema")
    if report.get("status") != "pass":
        errors.append("independent visual-review status is not pass")
    rom_sha = sha256(root / "build/level-B03/recursion94-debug.bin")
    symbol_sha = sha256(root / "build/level-B03/recursion94-debug.sym")
    if report.get("rom_sha256") != rom_sha or rom_sha is None:
        errors.append("critic ROM SHA-256 is stale or missing")
    if report.get("symbol_map_sha256") != symbol_sha or symbol_sha is None:
        errors.append("critic symbol-map SHA-256 is stale or missing")
    errors.extend(
        _record_errors(
            root=root,
            record=report.get("capture_report"),
            label="critic objective capture report",
            expected_path="artifacts/reports/m5-b03-visual.json",
        )
    )
    capture_record = report.get("capture_report")
    if (
        not isinstance(capture_record, dict)
        or capture_record.get("schema")
        != "r94-m5-b03-visual-evidence-v1"
    ):
        errors.append("critic capture-report schema binding is missing")
    handoff = capture_report.get("critic_handoff")
    expected_manifest_sha = (
        handoff.get("capture_manifest_sha256")
        if isinstance(handoff, dict)
        else None
    )
    handoff_capture_labels = [
        item.get("label")
        for item in (
            handoff.get("captures", [])
            if isinstance(handoff, dict)
            else []
        )
        if isinstance(item, dict) and isinstance(item.get("label"), str)
    ]
    canonical_capture_labels = set(handoff_capture_labels)
    if handoff_capture_labels != list(M5_VISUAL_CAPTURE_LABELS):
        errors.append("objective critic handoff labels are missing or out of order")
    if (
        not isinstance(expected_manifest_sha, str)
        or report.get("capture_manifest_sha256") != expected_manifest_sha
    ):
        errors.append("critic capture manifest binding is stale or missing")

    categories = report.get("categories")
    category_names: list[Any] = []
    scores: list[int] = []
    if isinstance(categories, list):
        for item in categories:
            if not isinstance(item, dict):
                continue
            category_names.append(item.get("category"))
            score = item.get("score")
            if isinstance(score, int) and not isinstance(score, bool):
                scores.append(score)
            evidence = item.get("evidence")
            captures = item.get("captures")
            aggregate_citations = [
                f"[{label}]" for label in M5_VISUAL_CAPTURE_LABELS
            ]
            if (
                not _has_concrete_visual_observation(evidence)
                or not isinstance(evidence, str)
                or any(
                    citation not in evidence
                    for citation in aggregate_citations
                )
            ):
                errors.append(
                    "critic category lacks concrete cross-scene evidence "
                    "with all exact bracketed citations"
                )
            if (
                not isinstance(captures, list)
                or captures != list(M5_VISUAL_CAPTURE_LABELS)
                or any(not isinstance(value, str) or not value for value in captures)
                or set(captures) != canonical_capture_labels
            ):
                errors.append("critic category lacks screenshot/state citations")
    if (
        not isinstance(categories, list)
        or len(categories) != len(VISUAL_RUBRIC_CATEGORIES)
        or category_names != list(VISUAL_RUBRIC_CATEGORIES)
    ):
        errors.append("critic categories do not match the exact prompt rubric")
    if len(scores) != len(VISUAL_RUBRIC_CATEGORIES) or any(
        score < 4 or score > 5 for score in scores
    ):
        errors.append("every visual rubric score must be an integer from 4 to 5")
    average = sum(scores) / len(scores) if scores else 0.0
    reported_average = report.get("average_score")
    if (
        not isinstance(reported_average, (int, float))
        or isinstance(reported_average, bool)
        or not math.isfinite(float(reported_average))
        or abs(float(reported_average) - average) > 1e-9
        or average < 4.2
    ):
        errors.append("critic average is stale, malformed, or below 4.2")
    if report.get("critical_gameplay_readability_defects") != []:
        errors.append("critical gameplay-readability defects are not zero")
    if report.get("severe_sprite_dropout_or_corruption") is not False:
        errors.append("severe sprite dropout/corruption findings are not zero")

    scenes = report.get("scenes")
    scene_labels: list[Any] = []
    if not _json_exact_equal(report.get("scene_count"), 5):
        errors.append("critic scene_count is not exactly five")
    if not isinstance(scenes, list) or len(scenes) != len(
        M5_VISUAL_CAPTURE_LABELS
    ):
        errors.append("critic does not contain exactly five per-scene reviews")
        scenes = []
    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append("critic per-scene review row is malformed")
            continue
        label = scene.get("label")
        scene_labels.append(label)
        expected_label = M5_VISUAL_CAPTURE_LABELS[scene_index]
        if label != expected_label or scene.get("status") != "pass":
            errors.append("critic per-scene label/order/status is invalid")
        scene_categories = scene.get("categories")
        scene_category_names: list[Any] = []
        scene_scores: list[int] = []
        if isinstance(scene_categories, list):
            for category in scene_categories:
                if not isinstance(category, dict):
                    continue
                scene_category_names.append(category.get("category"))
                score = category.get("score")
                if isinstance(score, int) and not isinstance(score, bool):
                    scene_scores.append(score)
                evidence = category.get("evidence")
                exact_citation = f"[{expected_label}]"
                if (
                    not _has_concrete_visual_observation(evidence)
                    or not isinstance(evidence, str)
                    or exact_citation not in evidence
                ):
                    errors.append(
                        f"critic scene {expected_label} category lacks "
                        "concrete evidence with its exact bracketed citation"
                    )
                if category.get("captures") != [expected_label]:
                    errors.append(
                        f"critic scene {expected_label} category lacks its exact screenshot citation"
                    )
        if (
            not isinstance(scene_categories, list)
            or len(scene_categories) != len(VISUAL_RUBRIC_CATEGORIES)
            or scene_category_names != list(VISUAL_RUBRIC_CATEGORIES)
        ):
            errors.append(
                f"critic scene {expected_label} categories do not match the exact rubric"
            )
        if len(scene_scores) != len(VISUAL_RUBRIC_CATEGORIES) or any(
            score < 4 or score > 5 for score in scene_scores
        ):
            errors.append(
                f"critic scene {expected_label} scores are not all integer 4..5"
            )
        scene_average = (
            sum(scene_scores) / len(scene_scores) if scene_scores else 0.0
        )
        reported_scene_average = scene.get("average_score")
        if (
            not isinstance(reported_scene_average, (int, float))
            or isinstance(reported_scene_average, bool)
            or not math.isfinite(float(reported_scene_average))
            or abs(float(reported_scene_average) - scene_average) > 1e-9
            or scene_average < 4.2
        ):
            errors.append(
                f"critic scene {expected_label} average is stale or below 4.2"
            )
        if scene.get("critical_gameplay_readability_defects") != []:
            errors.append(
                f"critic scene {expected_label} has critical readability defects"
            )
        if scene.get("severe_sprite_dropout_or_corruption") is not False:
            errors.append(
                f"critic scene {expected_label} has severe corruption"
            )
    if scene_labels != list(M5_VISUAL_CAPTURE_LABELS):
        errors.append("critic per-scene coverage is incomplete or out of order")

    reviewer = report.get("reviewer")
    if not (
        isinstance(reviewer, dict)
        and reviewer.get("independent_from_renderer_implementation") is True
        and reviewer.get("independent_from_capture_implementation") is True
        and isinstance(reviewer.get("role"), str)
        and reviewer["role"].strip()
        and isinstance(reviewer.get("method"), str)
        and reviewer["method"].strip()
    ):
        errors.append("critic independence/method provenance is insufficient")
    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(item, dict) or item.get("status") != "pass"
            for item in checks
        )
    ):
        errors.append("critic checks are missing or did not all pass")
    if errors:
        return failed_gate(
            name, "invalid independent M5 visual review: " + "; ".join(errors)
        )
    return {"gate": name, "status": "pass", "required": True}


def m4_budget_evidence_gate(
    *,
    root: Path,
    report: dict[str, Any] | None,
    expected_rom_sha256: str | None,
) -> dict[str, Any]:
    """Validate M4 budget evidence semantically, not by status rows alone."""
    base = evidence_gate(
        "m4_renderer_vblank_budget",
        report,
        expected_rom_sha256=expected_rom_sha256,
        require_rom_sha256=True,
        require_checks=True,
    )
    if base["status"] != "pass" or report is None:
        return base
    try:
        from tools.m4_budget_evidence import report_contract_errors

        errors = report_contract_errors(report, root=root)
    except Exception as error:
        errors = [
            "M4 budget artifact validation raised "
            f"{type(error).__name__}: {error}"
        ]
    if not errors:
        return base
    return {
        "gate": "m4_renderer_vblank_budget",
        "status": "fail",
        "required": True,
        "detail": "invalid M4 budget artifact: " + "; ".join(errors),
    }


def m4_cycle_evidence_gate(
    *,
    root: Path,
    report: dict[str, Any] | None,
    expected_rom_sha256: str | None,
) -> dict[str, Any]:
    """Validate native-cycle evidence through its semantic recomputation."""
    name = "m4_m68000_cycle_budget"
    base = evidence_gate(
        name,
        report,
        expected_rom_sha256=expected_rom_sha256,
        require_rom_sha256=True,
        require_checks=True,
    )
    if base["status"] != "pass" or report is None:
        return base
    try:
        from tools.m4_cycle_evidence import report_contract_errors

        errors = report_contract_errors(report, root=root)
    except Exception as error:
        errors = [
            "M4 cycle artifact validation raised "
            f"{type(error).__name__}: {error}"
        ]
    if not errors:
        return base
    return {
        "gate": name,
        "status": "fail",
        "required": True,
        "detail": "invalid M4 cycle artifact: " + "; ".join(errors),
    }


def emulator_failure_detail(report: dict[str, Any] | None) -> str:
    if not report:
        return "bounded emulator report missing"
    checks = report.get("checks")
    if isinstance(checks, list):
        for item in reversed(checks):
            if isinstance(item, dict) and item.get("detail"):
                return str(item["detail"])
    return (
        f"bounded emulator evidence status is "
        f"{report.get('status', 'unspecified')}"
    )


def create_validation_report(
    *, root: Path, scope: str, command_exits: dict[str, int]
) -> tuple[dict[str, Any], int]:
    if scope not in {
        "Milestone1",
        "Milestone2",
        "Milestone3",
        "Milestone4",
        "Milestone5",
        "Release",
    }:
        raise ValueError(f"unsupported validation scope: {scope}")
    missing_exits = set(COMMAND_EXIT_NAMES) - set(command_exits)
    if missing_exits:
        raise ValueError(
            f"missing command exit codes: {', '.join(sorted(missing_exits))}"
        )
    gates = [
        command_gate("safe_clean", command_exits["clean"]),
        command_gate("pinned_toolchain", command_exits["toolchain"]),
        command_gate("debug_build", command_exits["debug_build"]),
        command_gate("release_build", command_exits["release_build"]),
        host_tests_gate(root=root, exit_code=command_exits["host_tests"]),
        command_gate("debug_static_rom", command_exits["debug_static"]),
        command_gate("release_static_rom", command_exits["release_static"]),
    ]
    if scope in {"Milestone5", "Release"}:
        gates.extend(
            (
                command_gate(
                    "b03_debug_fixture_build",
                    command_exits["b03_debug_build"],
                ),
                command_gate(
                    "b03_debug_fixture_static_rom",
                    command_exits["b03_debug_static"],
                ),
            )
        )

    debug_rom = root / "build" / "recursion94-debug.bin"
    debug_sha = sha256(debug_rom)
    if scope != "Milestone5":
        heartbeat = load_json(
            root / "artifacts" / "reports" / "emulator-heartbeat.json"
        )
        screenshot = load_json(
            root / "artifacts" / "reports" / "boot-screenshot.json"
        )
        gates.extend(
            [
                evidence_gate(
                    "emulator_debug_heartbeat",
                    heartbeat,
                    expected_rom_sha256=debug_sha,
                ),
                evidence_gate(
                    "boot_screenshot_evidence",
                    screenshot,
                    expected_rom_sha256=debug_sha,
                ),
            ]
        )

    emulator_report = load_json(
        root / "artifacts" / "reports" / "emulator-smoke.json"
    )
    emulator_status = (
        "pass" if command_exits["emulator_probe"] == 0 else "fail"
    )
    gates.append(
        {
            "gate": "bounded_headless_emulator",
            "status": emulator_status,
            "required": False,
            "exit_code": command_exits["emulator_probe"],
            "detail": (
                None
                if emulator_status == "pass"
                else emulator_failure_detail(emulator_report)
            ),
        }
    )

    if scope in {"Milestone2", "Milestone3", "Milestone4"}:
        for gate_name, filename in M2_EVIDENCE_REPORTS:
            gates.append(
                evidence_gate(
                    gate_name,
                    load_json(root / "artifacts" / "reports" / filename),
                    expected_rom_sha256=debug_sha,
                    require_rom_sha256=True,
                    require_checks=True,
                )
            )

    m3_reports = (
        M3_EVIDENCE_REPORTS
        if scope == "Milestone3"
        else M3_EVIDENCE_REPORTS[:1]
        if scope == "Milestone4"
        else ()
    )
    for (
        gate_name,
        filename,
        require_rom_sha256,
        require_checks,
    ) in m3_reports:
        gates.append(
            evidence_gate(
                gate_name,
                load_json(root / "artifacts" / "reports" / filename),
                expected_rom_sha256=(
                    debug_sha if require_rom_sha256 else None
                ),
                require_rom_sha256=require_rom_sha256,
                require_checks=require_checks,
            )
        )

    if scope == "Milestone4":
        for (
            gate_name,
            filename,
            require_rom_sha256,
            require_checks,
        ) in M4_EVIDENCE_REPORTS:
            evidence = load_json(
                root / "artifacts" / "reports" / filename
            )
            if gate_name == "m4_renderer_vblank_budget":
                gates.append(
                    m4_budget_evidence_gate(
                        root=root,
                        report=evidence,
                        expected_rom_sha256=debug_sha,
                    )
                )
                continue
            if gate_name == "m4_m68000_cycle_budget":
                gates.append(
                    m4_cycle_evidence_gate(
                        root=root,
                        report=evidence,
                        expected_rom_sha256=debug_sha,
                    )
                )
                continue
            gates.append(
                evidence_gate(
                    gate_name,
                    evidence,
                    expected_rom_sha256=(
                        debug_sha if require_rom_sha256 else None
                    ),
                    require_rom_sha256=require_rom_sha256,
                    require_checks=require_checks,
                )
            )

    if scope in {"Milestone5", "Release"}:
        objective_visual = load_json(
            root / "artifacts/reports/m5-b03-visual.json"
        )
        gates.extend(
            (
                frozen_prior_milestones_gate(root=root),
                static_rom_report_gate(
                    root=root,
                    name="m5_current_debug_static_artifact",
                    report_filename="rom-debug-milestone5.json",
                    rom_relative_path="build/recursion94-debug.bin",
                ),
                static_rom_report_gate(
                    root=root,
                    name="m5_current_release_static_artifact",
                    report_filename="rom-release-milestone5.json",
                    rom_relative_path="build/recursion94-release.bin",
                ),
                static_rom_report_gate(
                    root=root,
                    name="m5_current_b03_static_artifact",
                    report_filename="rom-b03-milestone5.json",
                    rom_relative_path="build/level-B03/recursion94-debug.bin",
                ),
                m5_b01_runtime_gate(
                    root=root,
                    report=load_json(
                        root / "artifacts/reports/m5-runtime.json"
                    ),
                ),
                m5_b03_runtime_gate(
                    root=root,
                    report=load_json(
                        root / "artifacts/reports/m5-b03-runtime.json"
                    ),
                ),
                m5_budget_gate(
                    root=root,
                    report=load_json(
                        root / "artifacts/reports/m5-b03-budget.json"
                    ),
                ),
                pending_gate(
                    "m5_b03_native_cycle_budget",
                    "current-ROM simulation/reset/VBlank cycle evidence "
                    "is not implemented; current-ROM tick60 diagnostic "
                    "107428 cycles exceeds the 83203-cycle design limit; "
                    "counter/capacity evidence cannot waive this gate",
                ),
                m5_visual_capture_gate(
                    root=root,
                    report=objective_visual,
                ),
                m5_visual_critic_gate(
                    root=root,
                    report=load_json(
                        root
                        / "artifacts/reports/m5-b03-visual-critic.json"
                    ),
                    capture_report=objective_visual,
                ),
            )
        )

    if scope == "Release":
        gates.extend(
            [
                pending_gate(
                    "deterministic_replays",
                    "authoritative replay engine and canonical campaign fixtures are absent",
                ),
                pending_gate(
                    "save_integrity",
                    "runtime save journal/corruption round-trip evidence is absent",
                ),
                pending_gate(
                    "hardware_budgets",
                    "CPU/VBlank/DMA/sprite/pool/Z80 runtime measurements are absent",
                ),
                pending_gate(
                    "campaign_content",
                    "fifteen stages, five bosses, and twenty verified replays are absent",
                ),
                pending_gate(
                    "visual_qa",
                    "required deterministic captures and independent rubric reviews are absent",
                ),
                pending_gate(
                    "audio_qa",
                    "required deterministic audio captures and listening reviews are absent",
                ),
                pending_gate(
                    "red_team",
                    "release red-team and zero-critical/high-defect gates have not run",
                ),
            ]
        )

    required = [gate for gate in gates if gate["required"]]
    failed = [gate for gate in required if gate["status"] == "fail"]
    pending = [gate for gate in required if gate["status"] == "pending"]
    if failed:
        status = "FAIL"
        code = 1
    elif pending:
        status = "INCOMPLETE"
        code = 2
    else:
        status = "PASS"
        code = 0

    report = {
        "schema": 2,
        "scope": scope,
        "status": status,
        "release_complete": scope == "Release" and status == "PASS",
        "gates": gates,
        "summary": {
            "required": len(required),
            "passed": sum(gate["status"] == "pass" for gate in required),
            "failed": len(failed),
            "pending": len(pending),
        },
        "license_limitations": [
            "vasm 2.0f is under a non-commercial upstream license for this target; "
            "commercial distribution clearance is unresolved"
        ],
        "resume_commands": [
            ".\\tools\\build.ps1 -Configuration Debug",
            ".\\tools\\build.ps1 -Configuration Release",
            ".\\tools\\test.ps1",
            f".\\tools\\validate.ps1 -Scope {scope}",
        ],
    }
    return report, code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--scope",
        required=True,
        choices=(
            "Milestone1",
            "Milestone2",
            "Milestone3",
            "Milestone4",
            "Milestone5",
            "Release",
        ),
    )
    parser.add_argument("--report", required=True, type=Path)
    for name in COMMAND_EXIT_NAMES:
        parser.add_argument(f"--{name.replace('_', '-')}-exit", type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    command_exits = {
        name: int(getattr(args, f"{name}_exit")) for name in COMMAND_EXIT_NAMES
    }
    report, code = create_validation_report(
        root=root,
        scope=args.scope,
        command_exits=command_exits,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"{args.scope} validation: {report['status']} "
        f"({report['summary']['passed']} pass, {report['summary']['failed']} fail, "
        f"{report['summary']['pending']} pending)"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
