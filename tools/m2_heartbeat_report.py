#!/usr/bin/env python3
"""Derive current-ROM heartbeat evidence from the live M2 GDB sample report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_MAGIC = 0x52393444
EXPECTED_ABI_VERSION = 1
EXPECTED_ABI_SIZE = 0x400
BOOT_STAGE_RUNNING = 4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def increments_once(values: list[int]) -> bool:
    return bool(values) and all(
        later == earlier + 1
        for earlier, later in zip(values, values[1:])
    )


def heartbeat_report(
    *,
    root: Path,
    rom_sha256: str,
    source_report: dict[str, Any],
    source_report_path: Path,
    transcript_path: Path,
) -> dict[str, Any]:
    samples = source_report.get("samples")
    samples = samples if isinstance(samples, list) else []
    debug = [
        sample.get("debug", {})
        for sample in samples
        if isinstance(sample, dict)
    ]
    source_checks = source_report.get("checks")
    source_checks = source_checks if isinstance(source_checks, list) else []
    heartbeats = [int(item.get("heartbeat", -1)) for item in debug]
    frames = [int(item.get("frame_counter", -1)) for item in debug]
    sequences = [int(item.get("sequence", -1)) for item in debug]
    controller_samples = [
        int(item.get("controller_samples", -1)) for item in debug
    ]
    transcript = (
        transcript_path.read_text("utf-8", errors="replace")
        if transcript_path.is_file()
        else ""
    )
    transcript_sha = (
        sha256_file(transcript_path) if transcript_path.is_file() else None
    )
    reported_transcript_sha = source_report.get("transcript", {}).get(
        "sha256"
    )
    breakpoint_hex = str(
        source_report.get("session", {}).get("breakpoint_hex", "")
    )
    breakpoint_command = (
        f"> Z0,{int(breakpoint_hex, 16):x},2"
        if breakpoint_hex
        else ""
    )
    checks = [
        check(
            "source_report_status",
            source_report.get("status") == "pass"
            and bool(source_checks)
            and all(item.get("status") == "pass" for item in source_checks),
            f"source status/checks {source_report.get('status')}/"
            f"{len(source_checks)}",
        ),
        check(
            "current_rom_binding",
            source_report.get("rom_sha256") == rom_sha256,
            "source/current SHA-256 "
            f"{source_report.get('rom_sha256')}/{rom_sha256}",
        ),
        check(
            "sample_count",
            len(debug) >= 3 and len(debug) == len(samples),
            f"{len(debug)} decoded samples",
        ),
        check(
            "debug_identity",
            bool(debug)
            and all(
                int(item.get("magic", -1)) == EXPECTED_MAGIC
                and int(item.get("abi_version", -1)) == EXPECTED_ABI_VERSION
                and int(item.get("abi_size", -1)) == EXPECTED_ABI_SIZE
                for item in debug
            ),
            "R94D ABI 1/$0400 across current samples",
        ),
        check(
            "safe_running_state",
            bool(debug)
            and all(
                int(item.get("boot_stage", -1)) == BOOT_STAGE_RUNNING
                and int(item.get("fatal_code", -1)) == 0
                and int(item.get("hardware_flags", -1)) == 0
                for item in debug
            ),
            "running stage, fatal clear, hardware flags clear",
        ),
        check(
            "authoritative_heartbeat_progression",
            increments_once(heartbeats)
            and increments_once(frames)
            and heartbeats == frames,
            f"heartbeat/frame {heartbeats}/{frames}",
        ),
        check(
            "seqlock_progression",
            bool(sequences)
            and all(value >= 0 and value % 2 == 0 for value in sequences)
            and all(
                later == earlier + 2
                for earlier, later in zip(sequences, sequences[1:])
            ),
            f"sequences {sequences}",
        ),
        check(
            "controller_sample_progression",
            increments_once(controller_samples),
            f"controller samples {controller_samples}",
        ),
        check(
            "vblank_breakpoint_transcript",
            source_report.get("session", {}).get("breakpoint_symbol")
            == "VBlankInterrupt"
            and bool(breakpoint_command)
            and breakpoint_command in transcript
            and transcript.count("< S05") >= len(samples),
            f"breakpoint {breakpoint_hex}, stops {transcript.count('< S05')}",
        ),
        check(
            "transcript_binding",
            transcript_sha is not None
            and transcript_sha == reported_transcript_sha,
            f"actual/reported transcript SHA-256 "
            f"{transcript_sha}/{reported_transcript_sha}",
        ),
        check(
            "portable_runtime_isolation",
            source_report.get("session", {}).get(
                "portable_bundled_config"
            )
            is True
            and source_report.get("session", {})
            .get("user_config_preflight", {})
            .get("override_absent")
            is True,
            "live source used temporary bundled configuration",
        ),
    ]
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "schema": 1,
        "scope": "Milestone2",
        "evidence_type": "current_m2_blastem_gdb_heartbeat_derivative",
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "rom": {
            "path": "build/recursion94-debug.bin",
            "sha256": rom_sha256,
        },
        "checks": checks,
        "samples": [
            {
                "label": sample.get("label"),
                "sequence": item.get("sequence"),
                "heartbeat": item.get("heartbeat"),
                "frame_counter": item.get("frame_counter"),
                "controller_samples": item.get("controller_samples"),
                "fatal_code": item.get("fatal_code"),
            }
            for sample, item in zip(samples, debug)
        ],
        "source": {
            "report_path": source_report_path.relative_to(root).as_posix(),
            "report_sha256": (
                sha256_file(source_report_path)
                if source_report_path.is_file()
                else None
            ),
            "transcript_path": transcript_path.relative_to(root).as_posix(),
            "transcript_sha256": transcript_sha,
        },
        "scope_limit": (
            "This derives boot/VBlank heartbeat proof from the current live M2 "
            "GDB samples. Screenshot, gameplay, and release determinism remain "
            "separate gates."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    rom = args.rom.resolve()
    source_path = (
        args.source_report.resolve()
        if args.source_report
        else root / "artifacts/reports/m2-controller.json"
    )
    transcript_path = (
        args.transcript.resolve()
        if args.transcript
        else root / "artifacts/traces/m2-gdb-transcript.log"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/emulator-heartbeat.json"
    )
    source_report = json.loads(source_path.read_text("utf-8"))
    report = heartbeat_report(
        root=root,
        rom_sha256=sha256_file(rom),
        source_report=source_report,
        source_report_path=source_path,
        transcript_path=transcript_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        "Current emulator heartbeat evidence: "
        f"{'PASS' if report['status'] == 'pass' else 'FAIL'}"
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
