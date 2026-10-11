#!/usr/bin/env python3
"""Run bounded BlastEm probes and report exactly what they do (and do not) prove."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rom_tools.rom_image import RomValidationError, validate_rom  # noqa: E402
from tools.m2_evidence import (  # noqa: E402
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    verify_no_user_blastem_override,
    write_standard_pad_profile,
)


ACCESS_VIOLATION = 0xC0000005


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--event-log", type=Path)
    parser.add_argument("--mode", choices=("smoke", "benchmark", "capture"), default="smoke")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()
    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = args.report.resolve()
    event_log = (
        args.event_log.resolve()
        if args.event_log
        else (root / "artifacts" / "traces" / f"{rom.stem}-blastem.events")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    event_log.parent.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []
    try:
        static = validate_rom(rom.read_bytes(), require_all_vectors=True)
        checks.append({"gate": "static_rom", "status": "pass"})
    except (OSError, RomValidationError) as error:
        report = {
            "schema": 1,
            "mode": args.mode,
            "status": "fail",
            "checks": [{"gate": "static_rom", "status": "fail", "detail": str(error)}],
            "milestone_1_gate_passed": False,
            "release_gate_passed": False,
        }
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Emulator probe failed static preflight: {error}", file=sys.stderr)
        return 1

    emulator_directory = root / ".toolcache" / "blastem"
    emulator = emulator_directory / "blastem.exe"
    if not emulator.is_file():
        report = {
            "schema": 1,
            "mode": args.mode,
            "status": "fail",
            "checks": checks
            + [{"gate": "emulator_installed", "status": "fail", "detail": str(emulator)}],
            "milestone_1_gate_passed": False,
            "release_gate_passed": False,
        }
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return 1

    if event_log.exists():
        event_log.unlink()
    launch_flags = [
        "-b",
        str(args.frames),
        "-e",
        str(event_log),
    ]
    started = time.perf_counter()
    timed_out = False
    isolation_error: str | None = None
    isolation_evidence: dict[str, Any] = {}
    try:
        user_config = verify_no_user_blastem_override()
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        with exclusive_file_lock(lock_path, 120.0) as lock:
            with tempfile.TemporaryDirectory(
                prefix="recursion94-emulator-probe-"
            ) as profile:
                isolated_runtime, runtime_copy = clone_blastem_runtime(
                    emulator_directory, Path(profile)
                )
                controller_profile = write_standard_pad_profile(
                    emulator_directory / "default.cfg", Path(profile)
                )
                device_profile_sha = controller_profile["sha256"]
                controller_profile["session_isolation"] = (
                    apply_blastem_session_isolation(
                        Path(controller_profile["path"]),
                        Path(profile) / "session-data",
                    )
                )
                controller_profile["device_profile_sha256_before_paths"] = (
                    device_profile_sha
                )
                controller_profile["sha256"] = controller_profile[
                    "session_isolation"
                ]["config_sha256"]
                isolation_evidence = {
                    "user_config_preflight": user_config,
                    "serialized_lock": lock,
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                }
                command = [
                    str(isolated_runtime / "blastem.exe"),
                    *launch_flags,
                    str(rom),
                ]
                result = subprocess.run(
                    command,
                    cwd=isolated_runtime,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=args.timeout_seconds,
                )
        return_code: int | None = result.returncode
        output = result.stdout
    except subprocess.TimeoutExpired as error:
        timed_out = True
        return_code = None
        output = (error.stdout or "") + (error.stderr or "")
    except Exception as error:
        isolation_error = f"{type(error).__name__}: {error}"
        return_code = None
        output = isolation_error
    elapsed = time.perf_counter() - started
    unsigned_code = None if return_code is None else return_code & 0xFFFFFFFF
    process_passed = return_code == 0 and not timed_out
    if process_passed:
        checks.append(
            {
                "gate": "bounded_emulator_process",
                "status": "pass",
                "frames_requested": args.frames,
            }
        )
    else:
        detail = (
            isolation_error
            if isolation_error is not None
            else (
                "timeout"
                if timed_out
                else (
                    "Windows access violation 0xC0000005 in BlastEm headless mode"
                    if unsigned_code == ACCESS_VIOLATION
                    else f"exit code {return_code}"
                )
            )
        )
        checks.append(
            {
                "gate": "bounded_emulator_process",
                "status": "fail",
                "frames_requested": args.frames,
                "detail": detail,
            }
        )

    event_facts = {
        "path": event_log.relative_to(root).as_posix(),
        "present": event_log.is_file(),
        "bytes": event_log.stat().st_size if event_log.is_file() else 0,
        "sha256": sha256(event_log) if event_log.is_file() else None,
    }
    checks.extend(
        [
            {
                "gate": "debug_heartbeat_observed",
                "status": "pending",
                "detail": "hardware event logs do not expose the stable debug RAM ABI",
            },
            {
                "gate": "screenshot_evidence",
                "status": "pending",
                "detail": "pinned BlastEm has no deterministic screenshot CLI",
            },
        ]
    )
    report = {
        "schema": 1,
        "mode": args.mode,
        "status": "partial_pass" if process_passed else "fail",
        "emulator": ".toolcache/blastem/blastem.exe",
        "rom": {
            "path": rom.relative_to(root).as_posix(),
            "sha256": static["facts"]["sha256"],
        },
        "command": [
            "<temporary-copy>/blastem.exe",
            "-b",
            str(args.frames),
            "-e",
            event_facts["path"],
            rom.relative_to(root).as_posix(),
        ],
        "isolation": isolation_evidence,
        "process": {
            "return_code": return_code,
            "return_code_hex": (
                None if unsigned_code is None else f"0x{unsigned_code:08X}"
            ),
            "timed_out": timed_out,
            "wall_seconds": elapsed,
            "output": output[-4000:],
        },
        "event_log": event_facts,
        "checks": checks,
        "benchmark_caveat": (
            "host wall time is emulator throughput only; it is not a Genesis CPU, "
            "VBlank, DMA, sprite, or audio hardware-budget measurement"
        ),
        "milestone_1_gate_passed": False,
        "release_gate_passed": False,
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if not process_passed:
        print(
            f"BlastEm bounded probe: FAIL "
            f"({report['process']['return_code_hex']}); see {report_path}",
            file=sys.stderr,
        )
        return 1
    print(
        f"BlastEm bounded process: PASS ({args.frames} requested frames); "
        "heartbeat/screenshot gates remain pending"
    )
    return 2 if args.mode == "capture" else 0


if __name__ == "__main__":
    raise SystemExit(main())
