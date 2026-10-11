#!/usr/bin/env python3
"""Bind the final independent GDB PASS report to the current validated ROM."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rom_tools.rom_image import validate_rom  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "status": "pass" if passed else "fail", "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    rom_path = args.rom.resolve()
    source_path = args.source_report.resolve()
    source = json.loads(source_path.read_text("utf-8"))
    rom = rom_path.read_bytes()
    static = validate_rom(rom, require_all_vectors=True)
    rom_sha = static["facts"]["sha256"]
    header_checksum = f"{static['facts']['header']['stored_checksum']:04X}"
    vblank_pc = struct.unpack_from(">I", rom, 30 * 4)[0]

    transcript_path = root / source.get("transcript", "")
    source_checks = source.get("checks")
    samples = source.get("samples")
    valid_samples = isinstance(samples, list) and len(samples) >= 3
    checks = [
        result(
            "source_report_status",
            source.get("status") == "pass" and source.get("schema") == 1,
            f"source status {source.get('status')!r}",
        ),
        result(
            "source_checks",
            isinstance(source_checks, dict)
            and len(source_checks) == 18
            and all(value is True for value in source_checks.values()),
            f"{sum(value is True for value in source_checks.values()) if isinstance(source_checks, dict) else 0}/18 source checks true",
        ),
        result(
            "rom_binding",
            source.get("rom", {}).get("sha256") == rom_sha,
            f"current and audited sha256 {rom_sha}",
        ),
        result(
            "checksum_binding",
            source.get("rom", {}).get("header_checksum_hex") == header_checksum,
            f"current and audited checksum ${header_checksum}",
        ),
        result(
            "transcript_binding",
            transcript_path.is_file()
            and sha256(transcript_path) == source.get("transcript_sha256"),
            f"transcript {source.get('transcript_sha256')}",
        ),
        result(
            "sample_count",
            valid_samples and source.get("sample_count") == len(samples),
            f"{len(samples) if isinstance(samples, list) else 0} samples",
        ),
    ]
    if valid_samples:
        expected = list(range(len(samples)))
        checks.extend(
            [
                result(
                    "pc_and_stop",
                    all(
                        int(item.get("pc_hex", "0"), 16) == vblank_pc
                        and item.get("stop") == "S05"
                        for item in samples
                    ),
                    f"all samples stopped at VBlank ${vblank_pc:08X}",
                ),
                result(
                    "debug_identity",
                    all(
                        item.get("magic_hex") == "52393444"
                        and item.get("magic_ascii") == "R94D"
                        and item.get("abi_version") == 1
                        and item.get("abi_size") == 1024
                        for item in samples
                    ),
                    "R94D ABI version 1, size 1024",
                ),
                result(
                    "safe_boot_state",
                    all(
                        item.get("boot_stage") == 4
                        and item.get("fatal_code") == 0
                        and item.get("hardware_flags") == 0
                        for item in samples
                    ),
                    "boot stage 4, fatal 0, hardware flags 0",
                ),
                result(
                    "monotonic_counters",
                    [item.get("heartbeat") for item in samples] == expected
                    and [item.get("frame_counter") for item in samples] == expected
                    and [item.get("frame_token") for item in samples] == expected
                    and [item.get("sequence") for item in samples]
                    == [2 * index for index in expected],
                    f"heartbeat/frame/token {expected}, sequence {[2 * index for index in expected]}",
                ),
            ]
        )
    if transcript_path.is_file():
        transcript = transcript_path.read_text("utf-8", errors="replace")
        checks.append(
            result(
                "transcript_content",
                transcript.count("> mfff000,20") >= 3
                and transcript.count("< S05") >= 3
                and transcript.rstrip().endswith("< OK\n> k"),
                "three stops/RAM reads and clean breakpoint removal are recorded",
            )
        )

    passed = all(item["status"] == "pass" for item in checks)
    report = {
        "schema": 1,
        "status": "pass" if passed else "fail",
        "evidence_type": "independent_windows_blastem_gdb_debug_abi",
        "rom": {
            "path": rom_path.relative_to(root).as_posix(),
            "sha256": rom_sha,
            "header_checksum_hex": header_checksum,
        },
        "source": {
            "report_path": source_path.relative_to(root).as_posix(),
            "report_sha256": sha256(source_path),
            "transcript_path": transcript_path.relative_to(root).as_posix(),
            "transcript_sha256": sha256(transcript_path),
            "source_checks": source_checks,
        },
        "checks": checks,
        "samples": samples,
        "scope_limit": (
            "This hash-bound derivative independently rechecks the canonical "
            "Windows BlastEm GDB RAM proof. It proves Milestone 1 boot/VBlank "
            "heartbeat only; screenshot and later gameplay/audio gates are separate."
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Final heartbeat evidence normalization: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
