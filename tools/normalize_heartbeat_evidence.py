#!/usr/bin/env python3
"""Validate and normalize an independent BlastEm GDB heartbeat audit."""

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


EXPECTED_MAGIC = "52393444"  # R94D
EXPECTED_ABI_VERSION = 1
EXPECTED_ABI_SIZE = 1024
EXPECTED_BOOT_STAGE = 4
VBLANK_VECTOR_INDEX = 30


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "status": "pass" if passed else "fail", "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    rom_path = args.rom.resolve()
    source_path = args.source_report.resolve()
    transcript_path = args.transcript.resolve()
    source = json.loads(source_path.read_text("utf-8"))
    transcript = transcript_path.read_text("utf-8")
    rom = rom_path.read_bytes()
    static = validate_rom(rom, require_all_vectors=True)
    rom_sha = static["facts"]["sha256"]
    expected_vblank = struct.unpack_from(">I", rom, VBLANK_VECTOR_INDEX * 4)[0]

    samples = [
        item["debug"]
        for item in source.get("vblank_samples", [])
        if isinstance(item, dict) and isinstance(item.get("debug"), dict)
    ]
    final_debug = source.get("final_debug")
    if isinstance(final_debug, dict):
        samples.append(final_debug)
    required_fields = {
        "magic_hex",
        "abi_version",
        "abi_size",
        "boot_stage",
        "fatal_code",
        "hardware_flags",
        "sequence",
        "heartbeat",
        "frame_counter",
        "frame_token",
    }
    samples_complete = len(samples) >= 4 and all(required_fields <= set(item) for item in samples)
    checks = [
        check(
            "rom_static_and_hash",
            static["status"] == "pass",
            f"sha256 {rom_sha}",
        ),
        check(
            "gdb_breakpoint",
            source.get("breakpoint_result") == "OK"
            and source.get("counted_breakpoint_stops", 0) >= 3,
            f"breakpoint {source.get('breakpoint_address_hex')} stops "
            f"{source.get('counted_breakpoint_stops')}",
        ),
        check(
            "vblank_pc",
            int(str(source.get("final_pc_hex", "0")), 16) == expected_vblank
            and int(str(source.get("breakpoint_address_hex", "0")), 16)
            == expected_vblank,
            f"ROM vector and audited PC ${expected_vblank:08X}",
        ),
        check(
            "sample_shape",
            samples_complete,
            f"{len(samples)} complete debug ABI samples",
        ),
    ]
    if samples_complete:
        checks.extend(
            [
                check(
                    "debug_magic",
                    all(str(item["magic_hex"]).upper() == EXPECTED_MAGIC for item in samples),
                    "all samples contain ASCII R94D",
                ),
                check(
                    "debug_abi",
                    all(
                        item["abi_version"] == EXPECTED_ABI_VERSION
                        and item["abi_size"] == EXPECTED_ABI_SIZE
                        for item in samples
                    ),
                    "ABI version 1, size 1024",
                ),
                check(
                    "boot_stage",
                    all(item["boot_stage"] == EXPECTED_BOOT_STAGE for item in samples),
                    "all samples reached boot stage 4",
                ),
                check(
                    "fatal_and_hardware_flags",
                    all(
                        item["fatal_code"] == 0 and item["hardware_flags"] == 0
                        for item in samples
                    ),
                    "fatal code and hardware flags remain zero",
                ),
                check(
                    "heartbeat_monotonic",
                    [item["heartbeat"] for item in samples]
                    == list(range(samples[0]["heartbeat"], samples[0]["heartbeat"] + len(samples))),
                    f"heartbeat {[item['heartbeat'] for item in samples]}",
                ),
                check(
                    "frame_counter_monotonic",
                    [item["frame_counter"] for item in samples]
                    == list(
                        range(
                            samples[0]["frame_counter"],
                            samples[0]["frame_counter"] + len(samples),
                        )
                    ),
                    f"frame counter {[item['frame_counter'] for item in samples]}",
                ),
                check(
                    "frame_token_monotonic",
                    [item["frame_token"] for item in samples]
                    == list(
                        range(
                            samples[0]["frame_token"],
                            samples[0]["frame_token"] + len(samples),
                        )
                    ),
                    f"frame token {[item['frame_token'] for item in samples]}",
                ),
                check(
                    "seqlock_monotonic_even",
                    [item["sequence"] for item in samples]
                    == list(
                        range(
                            samples[0]["sequence"],
                            samples[0]["sequence"] + 2 * len(samples),
                            2,
                        )
                    ),
                    f"sequence {[item['sequence'] for item in samples]}",
                ),
            ]
        )
    checks.append(
        check(
            "transcript_commands",
            transcript.count("> mfff000,20") >= 4
            and transcript.count("< S05") >= 4
            and f"< {expected_vblank:08X}" in transcript,
            "transcript contains repeated RAM reads, stops, and VBlank PC",
        )
    )
    passed = all(item["status"] == "pass" for item in checks)
    report = {
        "schema": 1,
        "status": "pass" if passed else "fail",
        "evidence_type": "independent_blastem_gdb_debug_abi",
        "rom": {
            "path": rom_path.relative_to(root).as_posix(),
            "sha256": rom_sha,
        },
        "source": {
            "report_path": source_path.relative_to(root).as_posix(),
            "report_sha256": sha256(source_path),
            "report_status": source.get("status"),
            "transcript_path": transcript_path.relative_to(root).as_posix(),
            "transcript_sha256": sha256(transcript_path),
        },
        "checks": checks,
        "observed": {
            "vblank_pc": expected_vblank,
            "sample_count": len(samples),
            "heartbeat": [item.get("heartbeat") for item in samples],
            "frame_counter": [item.get("frame_counter") for item in samples],
            "frame_token": [item.get("frame_token") for item in samples],
            "sequence": [item.get("sequence") for item in samples],
            "final": final_debug,
        },
        "scope_limit": (
            "The source audit's OS screenshot helper timed out after the GDB RAM "
            "proof completed; that timeout does not alter these validated debugger "
            "samples. Screenshot evidence is tracked separately. This report proves "
            "Milestone 1 boot/VBlank heartbeat only, not later gameplay gates."
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Normalized heartbeat evidence: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
