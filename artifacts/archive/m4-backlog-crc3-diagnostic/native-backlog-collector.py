#!/usr/bin/env python3
"""Read-only native-debugger capture of the first M4 renderer backlog branch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_ROM_SHA256 = (
    "01107b5f1dd14e2fa578247d266be95cbe97704248ae2e578d97b3f08551967e"
)
EXPECTED_BLASTEM_SHA256 = (
    "22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803"
)
MARKER_RE = re.compile(
    r"^R94B (?P<phase>[MREVWX]) "
    r"(?P<cycle>[0-9A-Fa-f]+) "
    r"(?P<frame>[0-9A-Fa-f]+) "
    r"(?P<tick>[0-9A-Fa-f]+) "
    r"(?P<attempt>[0-9A-Fa-f]+) "
    r"(?P<token>[0-9A-Fa-f]+) "
    r"(?P<pending>[0-9A-Fa-f]+) "
    r"(?P<backlog>[0-9A-Fa-f]+) "
    r"(?P<last_tick>[0-9A-Fa-f]+) "
    r"(?P<boot_stage>[0-9A-Fa-f]+) "
    r"(?P<sequence>[0-9A-Fa-f]+) "
    r"(?P<last_vdp_status>[0-9A-Fa-f]+) "
    r"(?P<vblank_overruns>[0-9A-Fa-f]+)$"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def memory(address: int, size: str) -> str:
    if size not in {"b", "w", "l"}:
        raise ValueError(size)
    return f"[${address:X}].{size}"


def listing_backlog_address(path: Path) -> int:
    lines = path.read_text("utf-8", errors="strict").splitlines()
    for index, line in enumerate(lines):
        if line.rstrip().endswith("162: .backlog:"):
            for following in lines[index + 1 : index + 5]:
                match = re.match(r"^00:([0-9A-F]{8})\s", following)
                if match:
                    return int(match.group(1), 16)
    raise RuntimeError("cannot derive current renderer .backlog address")


def build_script(
    *,
    addresses: dict[str, int],
    symbols: dict[str, int],
    backlog_address: int,
) -> str:
    frame = memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    tick = memory(symbols["ENGINE_TICK"], "w")
    attempt = memory(symbols["ENGINE_ATTEMPT"], "w")
    token = memory(symbols["DEBUG_FRAME_TOKEN"], "w")
    pending = memory(symbols["DMA_QUEUE_FRAME_PENDING"], "w")
    backlog = memory(symbols["RENDERER_BACKLOG_FRAMES"], "w")
    last_tick = memory(symbols["RENDERER_LAST_TICK"], "w")
    boot_stage = memory(symbols["DEBUG_BOOT_STAGE"], "w")
    sequence = memory(symbols["DEBUG_SEQUENCE"], "l")
    last_vdp_status = memory(symbols["DEBUG_LAST_VDP_STATUS"], "w")
    vblank_overruns = memory(
        symbols["DEBUG_VBLANK_OVERRUN_COUNT"], "w"
    )

    def marker(phase: str) -> str:
        return (
            f'printf "R94B {phase} %x %x %x %x %x %x %x %x %x %x '
            f'%x %x\\n" '
            f"cycle {frame} {tick} {attempt} {token} {pending} "
            f"{backlog} {last_tick} {boot_stage} {sequence} "
            f"{last_vdp_status} {vblank_overruns}"
        )

    return "\n".join(
        (
            f"breakpoint ${addresses['main_start']:X}",
            f"breakpoint ${symbols['GameplayRendererBuildAndQueue']:X}",
            f"breakpoint ${addresses['main_end']:X}",
            f"breakpoint ${addresses['vblank_entry']:X}",
            f"breakpoint ${addresses['vblank_rte']:X}",
            f"breakpoint ${backlog_address:X}",
            "commands 0",
            marker("M"),
            "continue",
            "end",
            "commands 1",
            marker("R"),
            "continue",
            "end",
            "commands 2",
            marker("E"),
            "continue",
            "end",
            "commands 3",
            marker("V"),
            "continue",
            "end",
            "commands 4",
            marker("W"),
            "continue",
            "end",
            "commands 5",
            marker("X"),
            'printf "R94B COMPLETE\\n"',
            "end",
            "continue",
            "",
        )
    )


def parse_markers(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = MARKER_RE.fullmatch(line.strip())
        if not match:
            continue
        record: dict[str, Any] = {"phase": match.group("phase")}
        for name, value in match.groupdict().items():
            if name != "phase":
                record[name] = int(value, 16)
        records.append(record)
    for index, record in enumerate(records):
        record["index"] = index
        if index:
            delta = record["cycle"] - records[index - 1]["cycle"]
            record["delta_master_from_previous"] = delta
            record["ceil_m68k_cycles_from_previous"] = (delta + 6) // 7
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()

    if os.name != "nt":
        raise RuntimeError("native Windows Python is required")

    root = args.root.resolve()
    sys.path.insert(0, str(root))
    from tools.m2_evidence import (  # noqa: PLC0415
        apply_blastem_session_isolation,
        clone_blastem_runtime,
        exclusive_file_lock,
        parse_vasm_symbols,
        verify_no_user_blastem_override,
        write_standard_pad_profile,
    )
    from tools.m4_cycle_evidence import derive_pc_contract  # noqa: PLC0415

    rom = root / "build" / "recursion94-debug.bin"
    symbols_path = root / "build" / "recursion94-debug.sym"
    listing_path = root / "build" / "recursion94-debug.lst"
    emulator_directory = root / ".toolcache" / "blastem"
    emulator = emulator_directory / "blastem.exe"
    default_config = emulator_directory / "default.cfg"
    cycle_collector = root / "tools" / "m4_cycle_evidence.py"
    collector = Path(__file__).resolve()
    lock_path = root / ".toolcache" / "locks" / "blastem-gdb.lock"
    inputs = {
        "rom": rom,
        "symbols": symbols_path,
        "listing": listing_path,
        "emulator": emulator,
        "default_config": default_config,
        "cycle_collector": cycle_collector,
        "collector": collector,
    }
    for name, path in inputs.items():
        if not path.is_file():
            raise RuntimeError(f"missing {name}: {path}")
    if sha256_file(rom) != EXPECTED_ROM_SHA256:
        raise RuntimeError("Debug ROM hash drift")
    if sha256_file(emulator) != EXPECTED_BLASTEM_SHA256:
        raise RuntimeError("BlastEm hash drift")
    verify_no_user_blastem_override()
    before = {name: sha256_file(path) for name, path in inputs.items()}

    symbols = parse_vasm_symbols(symbols_path)
    pc_contract = derive_pc_contract(rom.read_bytes(), symbols)
    backlog_address = listing_backlog_address(listing_path)
    script = build_script(
        addresses=pc_contract["addresses"],
        symbols=symbols,
        backlog_address=backlog_address,
    )
    allowed = {"breakpoint", "commands", "printf", "continue", "end"}
    commands = [
        line.strip().split(maxsplit=1)[0].lower()
        for line in script.splitlines()
        if line.strip()
    ]
    command_policy_errors = [
        f"unapproved command: {command}"
        for command in commands
        if command not in allowed
    ]
    if re.search(
        r"(?im)^\s*(?:set|load|save|softreset|sr|watchpoint|bind|"
        r"variable|array|append)\b",
        script,
    ):
        command_policy_errors.append("mutation command detected")
    if command_policy_errors:
        raise RuntimeError("; ".join(command_policy_errors))

    started = datetime.now(timezone.utc).isoformat()
    process: subprocess.Popen[Any] | None = None
    helper: subprocess.Popen[str] | None = None
    helper_result: dict[str, Any] | None = None
    runtime_copy: dict[str, Any] | None = None
    controller: dict[str, Any] | None = None
    isolated_config_sha256 = ""
    process_id = 0
    with exclusive_file_lock(lock_path, 600):
        with tempfile.TemporaryDirectory(
            prefix="r94-backlog-crc3-native-"
        ) as temporary_text:
            temporary = Path(temporary_text)
            isolated_runtime, runtime_copy = clone_blastem_runtime(
                emulator_directory, temporary
            )
            controller = write_standard_pad_profile(
                default_config, temporary
            )
            controller["session_isolation"] = (
                apply_blastem_session_isolation(
                    Path(controller["path"]),
                    temporary / "session-data",
                )
            )
            isolated_config_sha256 = sha256_file(
                Path(controller["path"])
            )
            command_file = temporary / "commands.txt"
            command_file.write_text(
                script, encoding="utf-8", newline="\n"
            )
            launch_command = [
                str(isolated_runtime / "blastem.exe"),
                "-d",
                "-g",
                "-m",
                "gen",
                "-r",
                "U",
                str(rom),
            ]
            creationflags = getattr(
                subprocess, "CREATE_NEW_CONSOLE", 0x10
            )
            try:
                process = subprocess.Popen(
                    launch_command,
                    cwd=isolated_runtime,
                    creationflags=creationflags,
                )
                process_id = process.pid
                helper_command = [
                    sys.executable,
                    str(cycle_collector),
                    "--console-helper",
                    "--process-id",
                    str(process.pid),
                    "--command-file",
                    str(command_file),
                    "--complete-marker",
                    "R94B COMPLETE",
                    "--timeout-seconds",
                    str(args.timeout_seconds),
                ]
                helper = subprocess.Popen(
                    helper_command,
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, stderr = helper.communicate(
                    timeout=args.timeout_seconds + 10
                )
                if helper.returncode != 0:
                    raise RuntimeError(
                        f"console helper failed: {stderr.strip()}"
                    )
                helper_result = json.loads(stdout)
                process.wait(timeout=5)
                if process.returncode != 0:
                    raise RuntimeError(
                        f"BlastEm exit code {process.returncode}"
                    )
            finally:
                if helper is not None and helper.poll() is None:
                    helper.kill()
                    helper.wait(timeout=5)
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    if helper_result is None or runtime_copy is None or controller is None:
        raise RuntimeError("native session produced no result")
    console_text = helper_result["console_text"]
    records = parse_markers(console_text)
    expected_phases = ["V", "W", "M", "V", "W", "R", "E", "M", "R", "X"]
    transcript_errors: list[str] = []
    if [record["phase"] for record in records] != expected_phases:
        transcript_errors.append(
            "unexpected marker phase sequence: "
            + "".join(record["phase"] for record in records)
        )
    if sum(
        line.strip() == "R94B COMPLETE"
        for line in console_text.splitlines()
    ) != 1:
        transcript_errors.append("completion marker is not unique")
    # Win32's console display wraps long printf definitions at the fixed
    # screen-buffer width. Removing only display newlines preserves the exact
    # command characters while allowing those visual wraps to be audited.
    flat_console = console_text.replace("\n", "")
    for line in script.splitlines():
        if line and line not in flat_console:
            transcript_errors.append(f"command not echoed: {line}")
    if re.search(
        r"(?im)^\s*>\s*(?:set|load|save|softreset|sr|watchpoint|bind)",
        console_text,
    ):
        transcript_errors.append("mutation command echoed")
    if any(
        phrase in console_text
        for phrase in ("Failed to eval", "Unrecognized", "fgets failed")
    ):
        transcript_errors.append("native debugger reported an error")
    if not records or records[-1]["phase"] != "X":
        transcript_errors.append("first backlog branch was not captured")

    after = {name: sha256_file(path) for name, path in inputs.items()}
    drift = [
        name for name in inputs if before[name] != after[name]
    ]
    if drift:
        raise RuntimeError("capture input drift: " + ", ".join(drift))

    args.trace.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.trace.write_text(
        console_text, encoding="utf-8", newline="\n"
    )
    trace_sha256 = sha256_file(args.trace)
    backlog_hit = records[-1] if records else {}
    report = {
        "status": (
            "pass"
            if not command_policy_errors
            and not transcript_errors
            and backlog_hit.get("tick") == 2
            and backlog_hit.get("attempt") == 0
            and backlog_hit.get("pending") == 1
            and backlog_hit.get("token") == 0
            and backlog_hit.get("backlog") == 0
            else "fail"
        ),
        "scope": (
            "read-only first renderer backlog native-cycle diagnostic"
        ),
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "rom_sha256": sha256_file(rom),
        "backlog_address": f"{backlog_address:08X}",
        "first_backlog_before_increment": backlog_hit,
        "records": records,
        "expected_phase_sequence": expected_phases,
        "command_script": script,
        "command_policy": {
            "status": (
                "pass" if not command_policy_errors else "fail"
            ),
            "errors": command_policy_errors,
            "allowed_commands": sorted(allowed),
            "guest_memory_or_register_writes": [],
            "input_injection": "none",
        },
        "transcript_policy": {
            "status": "pass" if not transcript_errors else "fail",
            "errors": transcript_errors,
        },
        "session": {
            "process_id": process_id,
            "launch_flags": ["-d", "-g", "-m", "gen", "-r", "U"],
            "debugger_transport": "native_win32_console",
            "gdb_port_opened": False,
            "isolated_runtime": runtime_copy,
            "isolated_config_sha256": isolated_config_sha256,
            "controller": controller,
            "console_geometry": helper_result["console"],
            "script_input_records": helper_result[
                "script_input_records"
            ],
            "quit_input_records": helper_result[
                "quit_input_records"
            ],
        },
        "provenance": {
            name: file_record(path, root)
            for name, path in inputs.items()
        },
        "trace": {
            "path": relative(args.trace, root),
            "bytes": args.trace.stat().st_size,
            "sha256": trace_sha256,
        },
        "master_clocks_per_m68k_cycle": 7,
        "pc_contract": pc_contract,
        "limitations": [
            "Pinned BlastEm emulator evidence is not physical hardware.",
            "The breakpoint samples state before the backlog branch mutates "
            "the backlog flag/counter/result.",
            "Breakpoint and console handling consume host time only; the "
            "native cycle value is sampled before the breakpointed "
            "instruction in Genesis master-clock units.",
        ],
    }
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
