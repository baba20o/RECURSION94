#!/usr/bin/env python3
"""Run available host/ROM unit suites and report missing later suites honestly."""

from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rom_tools.rom_image import RomValidationError, validate_rom  # noqa: E402


AVAILABLE = {
    "Host": "tests/host",
    "Toolchain": "tests/host",
    "Rom": "tests/rom",
    "Milestone2": "tests/m2",
    "Milestone3": "tests/m3",
    "Milestone4": "tests/m4",
    "Milestone5": "tests/m5",
    "Render": "tests/render",
    "Assets": "tests/assets",
    "Content": "tests/content",
    "CampaignRuntime": "tests/campaign_runtime",
    "Audio": "tests/audio",
    "Replay": "tests/replay",
    "Evidence": "tests/emulator",
}
PENDING = {
    "Save": "runtime save implementation and round-trip fixture are not implemented",
    "Emulator": "use emulator_probe.py or m2_probe.ps1 for live emulator evidence",
}
RELEASE_PENDING = {
    **PENDING,
    "CanonicalReplay": (
        "the host R94R codec is implemented, but runtime-backed canonical "
        "campaign replay fixtures are not yet complete"
    ),
}


def run_suite(root: Path, relative: str) -> tuple[unittest.TestResult, str]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(root / relative), pattern="test_*.py", top_level_dir=str(root))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return result, stream.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--suite",
        default="All",
        choices=("All", *AVAILABLE.keys(), *PENDING.keys()),
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    requested = args.suite

    if requested in PENDING:
        report: dict[str, Any] = {
            "schema": 1,
            "status": "pending",
            "requested_suite": requested,
            "reason": PENDING[requested],
            "tests_run": 0,
            "gate_passed": False,
        }
        code = 2
    else:
        relative_suites = (
            sorted(set(AVAILABLE.values())) if requested == "All" else [AVAILABLE[requested]]
        )
        runs = []
        code = 0
        for relative in relative_suites:
            result, output = run_suite(root, relative)
            print(output, end="")
            runs.append(
                {
                    "path": relative,
                    "tests_run": result.testsRun,
                    "failures": len(result.failures),
                    "errors": len(result.errors),
                    "skipped": len(result.skipped),
                    "status": "pass" if result.wasSuccessful() else "fail",
                }
            )
            if not result.wasSuccessful():
                code = 1

        binary_checks = []
        if requested in {"All", "Rom"}:
            for configuration in ("debug", "release"):
                path = root / "build" / f"recursion94-{configuration}.bin"
                if not path.is_file():
                    binary_checks.append(
                        {
                            "configuration": configuration,
                            "status": "not_built",
                            "gate_passed": False,
                        }
                    )
                    continue
                try:
                    validation = validate_rom(path.read_bytes(), require_all_vectors=True)
                    binary_checks.append(
                        {
                            "configuration": configuration,
                            "status": "pass",
                            "sha256": validation["facts"]["sha256"],
                            "gate_passed": True,
                        }
                    )
                except RomValidationError as error:
                    binary_checks.append(
                        {
                            "configuration": configuration,
                            "status": "fail",
                            "issues": [issue.as_dict() for issue in error.issues],
                            "gate_passed": False,
                        }
                    )
                    code = 1
        report = {
            "schema": 1,
            "status": "pass" if code == 0 else "fail",
            "requested_suite": requested,
            "runs": runs,
            "binary_checks": binary_checks,
            "pending_release_suites": RELEASE_PENDING if requested == "All" else {},
            "release_gate_passed": False,
        }

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    report_path = args.report or root / "artifacts" / "reports" / "host-tests.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8", newline="\n")
    if code == 0:
        print("Available host/static test suites: PASS")
    elif code == 2:
        print(text, file=sys.stderr, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
