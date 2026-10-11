#!/usr/bin/env python3
"""Capture and validate hash-bound Milestone 2 YM2612/PSG VGM evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    apply_blastem_session_isolation,
    check,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    sha256_bytes,
    sha256_file,
    shutdown_emulator,
    tap_virtual_key,
    verify_no_user_blastem_override,
    write_json,
)


VGM_SAMPLE_RATE = 44100
MIN_CAPTURE_SAMPLES = VGM_SAMPLE_RATE


class VgmError(ValueError):
    """Malformed or unsupported VGM capture."""


def _u32(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset : offset + 4], "little")


def parse_vgm(payload: bytes) -> dict[str, Any]:
    if len(payload) < 0x40 or payload[0:4] != b"Vgm ":
        raise VgmError("capture is not a VGM file")
    version = _u32(payload, 0x08)
    data_relative = _u32(payload, 0x34) if version >= 0x00000150 else 0
    cursor = 0x40 if data_relative == 0 else 0x34 + data_relative
    if cursor < 0x40 or cursor >= len(payload):
        raise VgmError(f"invalid VGM data offset ${cursor:X}")

    ym_writes = 0
    psg_writes = 0
    ym_key_on = 0
    ym_key_off = 0
    ym_frequency_values: set[tuple[int, int]] = set()
    psg_tone_values: set[int] = set()
    psg_audible_volume = 0
    waits = 0
    wait_samples = 0
    ended = False

    def require(size: int) -> None:
        if cursor + size > len(payload):
            raise VgmError(
                f"truncated VGM command at ${cursor:X}: need {size} bytes"
            )

    while cursor < len(payload):
        command = payload[cursor]
        if command == 0x66:
            cursor += 1
            ended = True
            break
        if command == 0x50:
            require(2)
            value = payload[cursor + 1]
            psg_writes += 1
            if value & 0x80:
                if value & 0x10:
                    if (value & 0x0F) < 0x0F:
                        psg_audible_volume += 1
                else:
                    psg_tone_values.add(value & 0x7F)
            cursor += 2
            continue
        if command in {0x52, 0x53}:
            require(3)
            register = payload[cursor + 1]
            value = payload[cursor + 2]
            ym_writes += 1
            if command == 0x52 and register == 0x28:
                if value & 0xF0:
                    ym_key_on += 1
                else:
                    ym_key_off += 1
            if 0xA0 <= register <= 0xA6:
                ym_frequency_values.add((register, value))
            cursor += 3
            continue
        if command == 0x61:
            require(3)
            wait_samples += int.from_bytes(
                payload[cursor + 1 : cursor + 3], "little"
            )
            waits += 1
            cursor += 3
            continue
        if command == 0x62:
            wait_samples += 735
            waits += 1
            cursor += 1
            continue
        if command == 0x63:
            wait_samples += 882
            waits += 1
            cursor += 1
            continue
        if 0x70 <= command <= 0x7F:
            wait_samples += (command & 0x0F) + 1
            waits += 1
            cursor += 1
            continue
        if 0x80 <= command <= 0x8F:
            wait_samples += command & 0x0F
            waits += 1
            cursor += 1
            continue
        if command == 0x67:
            require(7)
            if payload[cursor + 1] != 0x66:
                raise VgmError("VGM data block is missing compatibility byte")
            block_size = _u32(payload, cursor + 3)
            require(7 + block_size)
            cursor += 7 + block_size
            continue
        if command == 0x68:
            require(12)
            cursor += 12
            continue
        if command == 0x64:
            require(4)
            cursor += 4
            continue
        if 0x30 <= command <= 0x3F or command in {0x4F}:
            require(2)
            cursor += 2
            continue
        if 0x51 <= command <= 0x5F or 0xA0 <= command <= 0xBF:
            require(3)
            cursor += 3
            continue
        if 0xC0 <= command <= 0xDF:
            require(4)
            cursor += 4
            continue
        if 0xE0 <= command <= 0xFF:
            require(5)
            cursor += 5
            continue
        stream_lengths = {
            0x90: 5,
            0x91: 5,
            0x92: 6,
            0x93: 11,
            0x94: 2,
            0x95: 5,
        }
        if command in stream_lengths:
            size = stream_lengths[command]
            require(size)
            cursor += size
            continue
        raise VgmError(f"unsupported VGM command ${command:02X} at ${cursor:X}")

    if not ended:
        raise VgmError("VGM end command is missing")
    return {
        "version_hex": f"{version:08X}",
        "sn76489_clock": _u32(payload, 0x0C),
        "ym2612_clock": _u32(payload, 0x2C),
        "header_total_samples": _u32(payload, 0x18),
        "parsed_wait_samples": wait_samples,
        "duration_seconds": wait_samples / VGM_SAMPLE_RATE,
        "wait_commands": waits,
        "ym2612_writes": ym_writes,
        "ym_key_on": ym_key_on,
        "ym_key_off": ym_key_off,
        "ym_frequency_value_count": len(ym_frequency_values),
        "psg_writes": psg_writes,
        "psg_audible_volume_writes": psg_audible_volume,
        "psg_tone_value_count": len(psg_tone_values),
        "ended": ended,
    }


def audio_report(
    *,
    rom_sha256: str,
    capture_path: Path,
    root: Path,
    capture_metadata: dict[str, Any],
) -> dict[str, Any]:
    payload = capture_path.read_bytes()
    facts = parse_vgm(payload)
    checks = [
        check(
            "vgm_clocks",
            facts["sn76489_clock"] > 0 and facts["ym2612_clock"] > 0,
            "SN76489/YM2612 clocks "
            f"{facts['sn76489_clock']}/{facts['ym2612_clock']}",
        ),
        check(
            "bounded_audible_duration",
            facts["parsed_wait_samples"] >= MIN_CAPTURE_SAMPLES,
            f"{facts['parsed_wait_samples']} samples "
            f"({facts['duration_seconds']:.3f} seconds)",
        ),
        check(
            "header_timing_matches_stream",
            facts["header_total_samples"] > 0
            and facts["header_total_samples"]
            == facts["parsed_wait_samples"],
            "header/parsed samples "
            f"{facts['header_total_samples']}/"
            f"{facts['parsed_wait_samples']}",
        ),
        check(
            "ym2612_activity",
            facts["ym2612_writes"] > 0
            and facts["ym_key_on"] >= 2
            and facts["ym_key_off"] >= 1
            and facts["ym_frequency_value_count"] >= 2,
            "writes/key-on/key-off/frequencies "
            f"{facts['ym2612_writes']}/{facts['ym_key_on']}/"
            f"{facts['ym_key_off']}/{facts['ym_frequency_value_count']}",
        ),
        check(
            "psg_activity",
            facts["psg_writes"] > 0
            and facts["psg_audible_volume_writes"] > 0
            and facts["psg_tone_value_count"] >= 2,
            "writes/audible-volume/tone-values "
            f"{facts['psg_writes']}/{facts['psg_audible_volume_writes']}/"
            f"{facts['psg_tone_value_count']}",
        ),
        check(
            "non_silent_dynamic_stream",
            facts["wait_commands"] > 0
            and facts["ym_key_on"] > 0
            and facts["psg_audible_volume_writes"] > 0,
            "timed stream contains audible events from both chips",
        ),
    ]
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "schema": 1,
        "scope": "Milestone2",
        "evidence_type": "blastem_vgm_ym2612_psg_capture",
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "capture": {
            "path": capture_path.relative_to(root).as_posix(),
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
            "format": "VGM",
            "facts": facts,
            **capture_metadata,
        },
        "notes": [
            "VGM is a playable chip-command capture, not a claim of human listening.",
            "Dynamic YM2612 and PSG events are parsed independently to reject silence.",
        ],
    }


def write_audio_profile(
    default_config: Path, profile_root: Path, capture_dir: Path
) -> dict[str, Any]:
    text = default_config.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    if not re.search(r"(?m)^\s*m\s+ui\.vgm_log\s*$", text):
        raise EvidenceFailure("pinned default.cfg has no M -> ui.vgm_log binding")
    replacements = {
        r"(?m)^\s*vgm_path\s+.*$": f"\tvgm_path {capture_dir.as_posix()}",
        r"(?m)^\s*vgm_template\s+.*$": (
            "\tvgm_template recursion94-m2-%Y%m%d-%H%M%S.vgm"
        ),
    }
    for pattern, replacement in replacements.items():
        text, count = re.subn(pattern, replacement, text, count=1)
        if count != 1:
            raise EvidenceFailure(
                f"pinned default.cfg did not match {pattern!r}"
            )
    destination = profile_root / "blastem" / "blastem.cfg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8", newline="\n")
    return {
        "path": destination.as_posix(),
        "sha256": sha256_file(destination),
        "source_sha256": sha256_file(default_config),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--duration-seconds", type=float, default=4.0)
    parser.add_argument("--window-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m2-audio-capture.json"
    )
    capture_dir = root / "artifacts/audio"
    capture_dir.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    process: subprocess.Popen[bytes] | None = None
    transcript: list[str] = []
    rom_sha256 = ""
    report: dict[str, Any]
    started = datetime.now(timezone.utc)

    try:
        if os.name != "nt":
            raise EvidenceFailure("live VGM capture requires Windows BlastEm")
        if not 2.0 <= args.duration_seconds <= 30.0:
            raise ValueError("--duration-seconds must be within 2..30")
        rom_sha256 = sha256_file(rom)
        if args.expected_sha256 and (
            rom_sha256 != args.expected_sha256.strip().lower()
        ):
            raise EvidenceFailure("Debug ROM does not match expected SHA-256")
        emulator_directory = root / ".toolcache/blastem"
        default_config = emulator_directory / "default.cfg"
        user_config_evidence = verify_no_user_blastem_override()
        launch_flags = [
            "-g",
            "-m",
            "gen",
            "-r",
            "U",
        ]
        with exclusive_file_lock(lock_path, 120.0) as lock:
            with tempfile.TemporaryDirectory(
                prefix="recursion94-m2-audio-"
            ) as profile:
                isolated_runtime, runtime_evidence = clone_blastem_runtime(
                    emulator_directory, Path(profile)
                )
                profile_evidence = write_audio_profile(
                    default_config, Path(profile), capture_dir
                )
                audio_profile_sha = profile_evidence["sha256"]
                profile_evidence["session_isolation"] = (
                    apply_blastem_session_isolation(
                        Path(profile_evidence["path"]),
                        Path(profile) / "session-data",
                        vgm_path=capture_dir,
                    )
                )
                profile_evidence["audio_profile_sha256_before_paths"] = (
                    audio_profile_sha
                )
                profile_evidence["sha256"] = profile_evidence[
                    "session_isolation"
                ]["config_sha256"]
                profile_evidence["runtime_copy"] = runtime_evidence
                command = [
                    str(isolated_runtime / "blastem.exe"),
                    *launch_flags,
                    str(rom),
                ]
                process = subprocess.Popen(
                    command,
                    cwd=isolated_runtime,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                window = find_process_window(
                    process.pid, args.window_timeout_seconds
                )
                time.sleep(1.5)
                tap_virtual_key(window, virtual_key=0x4D, scan_code=0x32)
                capture_started_ns = time.time_ns()
                time.sleep(args.duration_seconds)
                tap_virtual_key(window, virtual_key=0x4D, scan_code=0x32)
                time.sleep(1.0)
                candidates = [
                    path
                    for path in capture_dir.glob("recursion94-m2-*.vgm")
                    if path.stat().st_mtime_ns >= capture_started_ns - 2_000_000_000
                ]
                if len(candidates) != 1:
                    raise EvidenceFailure(
                        f"expected one fresh VGM capture, found {len(candidates)}"
                    )
                capture = candidates[0]
                if sha256_file(rom) != rom_sha256:
                    raise EvidenceFailure("Debug ROM changed during audio capture")
                report = audio_report(
                    rom_sha256=rom_sha256,
                    capture_path=capture,
                    root=root,
                    capture_metadata={
                        "started_utc": started.isoformat(),
                        "requested_seconds": args.duration_seconds,
                        "emulator_process_id": process.pid,
                        "launch_flags": launch_flags,
                        "serialized_lock": lock,
                        "profile": profile_evidence,
                        "user_config_preflight": user_config_evidence,
                        "toggle_binding": "M -> ui.vgm_log",
                    },
                )
                process.terminate()
                shutdown_emulator(None, process, transcript)
                process = None
    except Exception as error:
        report = {
            "schema": 1,
            "scope": "Milestone2",
            "evidence_type": "blastem_vgm_ym2612_psg_capture",
            "status": "fail",
            "rom_sha256": rom_sha256,
            "checks": [
                check(
                    "audio_capture_session",
                    False,
                    f"{type(error).__name__}: {error}",
                )
            ],
            "error": f"{type(error).__name__}: {error}",
        }
    finally:
        shutdown_emulator(None, process, transcript)
        write_json(report_path, report)

    passed = report["status"] == "pass"
    print(f"Milestone 2 VGM audio evidence: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
