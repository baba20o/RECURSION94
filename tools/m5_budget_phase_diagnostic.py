#!/usr/bin/env python3
"""Noncanonical B03 VBlank/DMA phase diagnostic using BlastEm native cycles.

This intentionally bounded helper observes only authoritative frames 2 and 3
of the pinned B03 Debug ROM.  It holds the first canonical input (Up), then
uses read-only native-debugger expressions to record the gameplay call, the
renderer interrupt-mask span, VBlank entry/ack/queue/DMA/exit phases, and the
known overrun compare site.  It never changes guest memory, registers, or PC.

The output is diagnostic evidence only.  It is not a canonical M5 budget run
and cannot make a campaign-wide or physical-hardware timing claim.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    parse_vasm_symbols,
    post_virtual_key,
    sha256_file,
    verify_no_user_blastem_override,
    write_standard_pad_profile,
)


REPORT_SCHEMA = "r94-m5-b03-budget-vblank-phase-post-shutter-packing-v1"
EVIDENCE_TYPE = (
    "blastem_native_debugger_b03_vblank_phase_post_shutter_packing"
)
READY_MARKER = "R94P READY"
COMPLETE_MARKER = "R94P COMPLETE"
COLLECTOR_RELATIVE_PATH = "tools/m5_budget_phase_diagnostic.py"
ROM_RELATIVE_PATH = "build/level-B03/recursion94-debug.bin"
SYMBOL_RELATIVE_PATH = "build/level-B03/recursion94-debug.sym"
EMULATOR_RELATIVE_PATH = ".toolcache/blastem/blastem.exe"
CONFIG_RELATIVE_PATH = ".toolcache/blastem/default.cfg"
DEFAULT_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "overrun-vblank-phase-post-shutter-packing-frame2-frame3-20260830.json"
)
DEFAULT_TRACE_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "overrun-vblank-phase-post-shutter-packing-frame2-frame3-20260830.log"
)

PINNED_ROM_SHA256 = (
    "2728497799e1673369c81d3e5e12759e45d44c1c4666e0fb2cc555fd2d20fc6f"
)
PINNED_SYMBOL_SHA256 = (
    "3f0f172dceec267267c5f841e7011de4477a2fb04a082476c138ada4f4cfb526"
)
PINNED_EMULATOR_SHA256 = (
    "22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803"
)
PINNED_DEFAULT_CONFIG_SHA256 = (
    "27cf0b11a6e551f4427608ee32cc94018f82f9c655ba6eb5a5e69ecf8b1aba4e"
)
BLASTEM_SOURCE_NODE = "732f5689d4381862da283c420b68f37ac320c1d8"
BLASTEM_VERSION = f"0.6.3-pre-{BLASTEM_SOURCE_NODE[:12]}"
BASELINE_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "overrun-vblank-phase-frame2-frame3-20260830.json"
)
BASELINE_TRACE_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "overrun-vblank-phase-frame2-frame3-20260830.log"
)
BASELINE_REPORT_SHA256 = (
    "f2ff5d95bf8291cea1b2aac2ebd9fe482f93518c1c8a4ab2b57f153284edee1c"
)
BASELINE_TRACE_SHA256 = (
    "7b9882d3baed49388c7fce45f68cd40d5ea3ddfcf0f75d5822c139d6a71256db"
)
BASELINE_ROM_SHA256 = (
    "f86729bd27faf4c56bbb3e3a396607f54d1aa8bfa38b55b1e4de2b6d0e53b31e"
)
BASELINE_CADENCE_DEBT_CPU_CYCLES = 8_062

BOOT_STAGE_RUNNING = 4
UP_INPUT_MASK = 0x01
UP_KEY = {
    "virtual_key": 0x26,
    "scan_code": 0x48,
    "extended": True,
}
MASTER_CLOCKS_PER_M68K_CYCLE = 7
NTSC_FRAME_MASTER_CLOCKS = 262 * 3420
MAX_OBSERVED_FRAMES = 3
EXPECTED_DMA_PER_FRAME = 4
MAX_DMA_CALLS = 2 * EXPECTED_DMA_PER_FRAME
EXPECTED_ORIGIN_HITS = 0

# Internal instruction boundaries are ROM-hash-bound below.  These are the
# narrowest useful boundaries around the exact B03 failure and its suspected
# interrupt-mask source; no symbol-less scan or unbounded stop is admitted.
PC = {
    "gameplay_call": 0x000002F2,
    "gameplay_return": 0x000002F6,
    "renderer_masked_start": 0x00008AD4,
    "renderer_hud_start": 0x00008B32,
    "renderer_hud_end_sat_start": 0x00008B36,
    "renderer_sat_end_overlay_start": 0x00008B3A,
    "renderer_overlay_end_queue_start": 0x00008B3E,
    "renderer_queue_end": 0x00008C00,
    "renderer_masked_end": 0x00008C58,
    "dma_commit": 0x0000124C,
    "dma_issue_start": 0x00001422,
    "dma_issue_end": 0x000014C8,
    "vblank_entry": 0x0000167C,
    "vblank_ack_complete": 0x0000169E,
    "vblank_post_dma": 0x000016C2,
    "vblank_exit_status_loaded": 0x000016FC,
    "vblank_overrun_compare": 0x0000170E,
    "vblank_common_checks": 0x0000171E,
    "vblank_rte": 0x00001832,
}

PC_SYMBOL_BINDINGS = {
    "gameplay_call": "MainLoopGameplayStepCall",
    "gameplay_return": "MainLoopPostGameplayStepCall",
    "dma_commit": "DmaCommitPreparedQueue",
    "dma_issue_start": "DmaIssueVramTransfer",
    "vblank_entry": "VBlankInterrupt",
}

ALLOWED_COMMANDS = frozenset(
    {"breakpoint", "commands", "printf", "if", "else", "continue", "end"}
)

MARKER_FIELDS = {
    "MS": ("cycle", "frame", "tick", "input", "d0", "sr"),
    "ME": ("cycle", "frame", "tick", "result", "sr"),
    "RS": ("cycle", "frame", "tick", "sr", "pending"),
    "RH": ("cycle", "frame", "tick", "sr", "pending"),
    "HS": ("cycle", "frame", "tick", "sr", "pending"),
    "SP": ("cycle", "frame", "tick", "sr", "pending"),
    "PQ": ("cycle", "frame", "tick", "sr", "pending"),
    "QE": ("cycle", "frame", "tick", "sr", "pending"),
    "RE": ("cycle", "frame", "tick", "sr", "pending"),
    "VE": ("cycle", "frame", "tick", "sr", "token"),
    "VA": ("cycle", "frame", "entry_status", "sequence"),
    "VC": (
        "cycle",
        "frame",
        "tick",
        "pending",
        "published_depth",
        "build_depth",
        "build_words",
    ),
    "Q": ("frame", "index", "source", "destination", "words", "flags", "age"),
    "DS": ("cycle", "frame", "source", "destination", "words", "commit_words"),
    "DE": ("cycle", "frame", "commit_words", "d0", "d1"),
    "VP": ("cycle", "frame", "commit_words", "debug_words", "depth", "pending"),
    "VS": ("cycle", "frame", "exit_status", "entry_status", "counter"),
    "VO": ("cycle", "frame", "d0", "d1", "counter", "sequence"),
    "VK": ("cycle", "frame", "d0", "d1", "counter"),
    "VX": ("cycle", "frame", "sequence", "counter", "token"),
}
MARKER_RE = re.compile(r"^R94P (?P<phase>[A-Z]{1,2})(?P<values>(?: [0-9A-Fa-f]+)+)$")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _memory(address: int, size: str) -> str:
    if size not in {"b", "w", "l"}:
        raise ValueError(f"invalid memory width {size!r}")
    return f"[${address:X}].{size}"


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.resolve().as_posix()
    return {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _pc_contract(rom: bytes, symbols: Mapping[str, int]) -> dict[str, Any]:
    errors: list[str] = []
    for name, symbol_name in PC_SYMBOL_BINDINGS.items():
        if symbols.get(symbol_name) != PC[name]:
            errors.append(
                f"{name} ${PC[name]:X} != {symbol_name} "
                f"${symbols.get(symbol_name, -1):X}"
            )
    renderer_entry = PC["renderer_masked_start"] - 10
    if symbols.get("GameplayRendererBuildAndQueue") != renderer_entry:
        errors.append("GameplayRendererBuildAndQueue symbol drift")
    if PC["renderer_masked_start"] != symbols.get(
        "GameplayRendererBuildAndQueue", -1
    ) + 10:
        errors.append("renderer mask-start derivation drift")
    if PC["renderer_masked_end"] <= PC["renderer_masked_start"]:
        errors.append("renderer mask interval is not positive")
    if rom[PC["renderer_masked_start"] - 4 : PC["renderer_masked_start"]] != bytes.fromhex(
        "46FC2700"
    ):
        errors.append("renderer mask instruction drift")
    if rom[PC["renderer_masked_end"] : PC["renderer_masked_end"] + 2] != bytes.fromhex(
        "46C7"
    ):
        errors.append("renderer SR-restore instruction drift")
    if rom[PC["vblank_overrun_compare"] : PC["vblank_overrun_compare"] + 10] != bytes.fromhex(
        "0C79FFFF00FFF0326706"
    ):
        errors.append("VBlank overrun compare bytes drift")
    if rom[PC["vblank_rte"] : PC["vblank_rte"] + 2] != bytes.fromhex("4E73"):
        errors.append("VBlank rte opcode drift")
    if errors:
        raise EvidenceFailure("; ".join(errors))
    return {
        "addresses": dict(PC),
        "symbol_bindings": dict(PC_SYMBOL_BINDINGS),
        "renderer_mask_start_derivation": (
            "instruction after move.w #$2700,sr at renderer entry+6"
        ),
        "renderer_mask_end_derivation": (
            "move.w d7,sr restore opcode; marker stops immediately before restore"
        ),
        "rom_windows": {
            name: {
                "address": address,
                "hex": rom[address : address + 16].hex().upper(),
            }
            for name, address in PC.items()
        },
    }


def build_command_script(symbols: Mapping[str, int]) -> str:
    frame = _memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    tick = _memory(symbols["ENGINE_TICK"], "w")
    boot = _memory(symbols["DEBUG_BOOT_STAGE"], "w")
    token = _memory(symbols["DEBUG_FRAME_TOKEN"], "w")
    sequence = _memory(symbols["DEBUG_SEQUENCE"], "l")
    input_held = _memory(symbols["INPUT_HELD"], "b")
    entry_status = _memory(symbols["DEBUG_LAST_VDP_STATUS"], "w")
    counter = _memory(symbols["DEBUG_VBLANK_OVERRUN_COUNT"], "w")
    debug_words = _memory(symbols["DEBUG_DMA_WORDS_FRAME"], "w")
    debug_depth = _memory(symbols["DEBUG_DMA_QUEUE_DEPTH"], "w")
    pending = _memory(symbols["DMA_QUEUE_FRAME_PENDING"], "w")
    published = _memory(symbols["DMA_QUEUE_PUBLISHED_DEPTH"], "w")
    build_depth = _memory(symbols["DMA_QUEUE_BUILD_DEPTH"], "w")
    build_words = _memory(symbols["DMA_QUEUE_BUILD_WORDS"], "l")
    commit_words = _memory(symbols["DMA_QUEUE_COMMIT_WORDS"], "w")
    entries = symbols["DMA_QUEUE_ENTRIES"]

    ordered = (
        "gameplay_call",
        "gameplay_return",
        "renderer_masked_start",
        "renderer_hud_start",
        "renderer_hud_end_sat_start",
        "renderer_sat_end_overlay_start",
        "renderer_overlay_end_queue_start",
        "renderer_queue_end",
        "renderer_masked_end",
        "vblank_entry",
        "vblank_ack_complete",
        "dma_commit",
        "dma_issue_start",
        "dma_issue_end",
        "vblank_post_dma",
        "vblank_exit_status_loaded",
        "vblank_overrun_compare",
        "vblank_common_checks",
        "vblank_rte",
    )
    lines = [f"breakpoint ${PC[name]:X}" for name in ordered]
    breakpoint_index = {name: index for index, name in enumerate(ordered)}

    def commands(index: int, body: Sequence[str]) -> None:
        lines.append(f"commands {index}")
        lines.extend(body)
        lines.append("end")

    commands(
        breakpoint_index["gameplay_call"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P MS %x %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} {input_held} d0 sr"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["gameplay_return"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P ME %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} d0 sr"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_masked_start"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P RS %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_hud_start"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P RH %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_hud_end_sat_start"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P HS %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_sat_end_overlay_start"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P SP %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_overlay_end_queue_start"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P PQ %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_queue_end"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P QE %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["renderer_masked_end"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P RE %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["vblank_entry"],
        (
            f"if {boot} < $4",
            "continue",
            "else",
            f"if {frame} < $1",
            f'printf "{READY_MARKER}\\n"',
            "else",
            (
                'printf "R94P VE %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {token}"
            ),
            "continue",
            "end",
            "end",
        ),
    )
    commands(
        breakpoint_index["vblank_ack_complete"],
        (
            f"if {frame} < $1",
            "continue",
            "else",
            (
                'printf "R94P VA %x %x %x %x\\n" '
                f"cycle {frame} {entry_status} {sequence}"
            ),
            "continue",
            "end",
        ),
    )
    queue_lines: list[str] = [
        f"if {frame} < $2",
        "continue",
        "else",
        (
            'printf "R94P VC %x %x %x %x %x %x %x\\n" '
            f"cycle {frame} {tick} {pending} {published} {build_depth} {build_words}"
        ),
    ]
    for index in range(EXPECTED_DMA_PER_FRAME):
        address = entries + index * 12
        queue_lines.append(
            'printf "R94P Q %x %x %x %x %x %x %x\\n" '
            f"{frame} ${index:X} {_memory(address, 'l')} "
            f"{_memory(address + 4, 'w')} {_memory(address + 6, 'w')} "
            f"{_memory(address + 8, 'w')} {_memory(address + 10, 'w')}"
        )
    queue_lines.extend(("continue", "end"))
    commands(breakpoint_index["dma_commit"], tuple(queue_lines))
    commands(
        breakpoint_index["dma_issue_start"],
        (
            f"if {frame} < $2",
            "continue",
            "else",
            (
                'printf "R94P DS %x %x %x %x %x %x\\n" '
                f"cycle {frame} a0 d0 d1 {commit_words}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["dma_issue_end"],
        (
            f"if {frame} < $2",
            "continue",
            "else",
            (
                'printf "R94P DE %x %x %x %x %x\\n" '
                f"cycle {frame} {commit_words} d0 d1"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["vblank_post_dma"],
        (
            f"if {frame} < $2",
            "continue",
            "else",
            (
                'printf "R94P VP %x %x %x %x %x %x\\n" '
                f"cycle {frame} {commit_words} {debug_words} {debug_depth} {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["vblank_exit_status_loaded"],
        (
            f"if {frame} < $2",
            "continue",
            "else",
            (
                'printf "R94P VS %x %x %x %x %x\\n" '
                f"cycle {frame} d0 {entry_status} {counter}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["vblank_overrun_compare"],
        (
            'printf "R94P VO %x %x %x %x %x %x\\n" '
            f"cycle {frame} d0 d1 {counter} {sequence}",
            "continue",
        ),
    )
    commands(
        breakpoint_index["vblank_common_checks"],
        (
            f"if {frame} < $2",
            "continue",
            "else",
            (
                'printf "R94P VK %x %x %x %x %x\\n" '
                f"cycle {frame} d0 d1 {counter}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        breakpoint_index["vblank_rte"],
        (
            f"if {boot} < $4",
            "continue",
            "else",
            f"if {frame} < $2",
            "continue",
            "else",
            (
                'printf "R94P VX %x %x %x %x %x\\n" '
                f"cycle {frame} {sequence} {counter} {token}"
            ),
            f"if {frame} < ${MAX_OBSERVED_FRAMES:X}",
            "continue",
            "else",
            f'printf "{COMPLETE_MARKER}\\n"',
            "end",
            "end",
            "end",
        ),
    )
    lines.extend(("continue", ""))
    return "\n".join(lines)


def command_policy_errors(script: str, symbols: Mapping[str, int]) -> list[str]:
    errors: list[str] = []
    if script != build_command_script(symbols):
        errors.append("command script is not the exact generated diagnostic script")
    continues = 0
    for line_number, raw in enumerate(script.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        command = stripped.split(maxsplit=1)[0].lower()
        if command not in ALLOWED_COMMANDS:
            errors.append(f"line {line_number}: command {command!r} is not read-only allowed")
        if command == "continue":
            continues += 1
    if continues > 48:
        errors.append(f"continue command bound exceeded: {continues}/48")
    if " set " in f" {script.lower()} ":
        errors.append("set token appears in diagnostic script")
    return errors


def parse_markers(console_text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in console_text.splitlines():
        match = MARKER_RE.fullmatch(line.strip())
        if not match:
            continue
        phase = match.group("phase")
        fields = MARKER_FIELDS.get(phase)
        if fields is None:
            raise EvidenceFailure(f"unknown R94P marker phase {phase!r}")
        values = match.group("values").split()
        if len(values) != len(fields):
            raise EvidenceFailure(
                f"{phase} marker has {len(values)} values; {len(fields)} required"
            )
        record = {"phase": phase}
        record.update(zip(fields, (int(value, 16) for value in values)))
        records.append(record)
    return records


def console_contract_errors(console_text: str, script: str) -> list[str]:
    errors: list[str] = []
    stripped = [line.strip() for line in console_text.splitlines()]
    for marker in (READY_MARKER, COMPLETE_MARKER):
        if stripped.count(marker) != 1:
            errors.append(f"console marker count mismatch for {marker!r}")
    if READY_MARKER in stripped and COMPLETE_MARKER in stripped:
        if stripped.index(READY_MARKER) >= stripped.index(COMPLETE_MARKER):
            errors.append("READY/COMPLETE console order mismatch")
    if stripped.count("> continue") != 2:
        errors.append("console must contain exactly bootstrap and measured continue")
    if stripped.count("> quit") != 1:
        errors.append("console must contain exactly one final quit")
    breakpoint_lines = [
        line for line in script.splitlines() if line.startswith("breakpoint ")
    ]
    for index, command in enumerate(breakpoint_lines):
        if stripped.count(f"> {command}") != 1:
            errors.append(f"console breakpoint command echo mismatch: {command}")
        address = command.split("$", 1)[1]
        expected_set = f"68K Breakpoint {index} set at ${address}"
        if stripped.count(expected_set) != 1:
            errors.append(f"console breakpoint set acknowledgement mismatch: {index}")
    for pattern in (
        "Failed to eval",
        "Unrecognized",
        "not a recognized command",
        "fgets failed",
    ):
        if any(pattern in line for line in stripped):
            errors.append(f"native debugger error text present: {pattern}")
    r94p_lines = [line for line in stripped if line.startswith("R94P ")]
    parsed_count = len(parse_markers(console_text))
    if len(r94p_lines) != parsed_count + 2:
        errors.append("unknown or extra R94P console line")
    return errors


def _by_phase(records: Sequence[Mapping[str, Any]], phase: str) -> list[Mapping[str, Any]]:
    return [record for record in records if record["phase"] == phase]


def _positive_delta(start: int, end: int, label: str) -> int:
    if not (0 <= start <= 0xFFFFFFFF and 0 <= end <= 0xFFFFFFFF):
        raise EvidenceFailure(f"{label} cycle is outside uint32")
    if end <= start:
        raise EvidenceFailure(f"{label} cycle delta is not positive/no-wrap")
    return end - start


def _span(start: Mapping[str, Any], end: Mapping[str, Any], label: str) -> dict[str, Any]:
    master = _positive_delta(start["cycle"], end["cycle"], label)
    return {
        "start_cycle_master": start["cycle"],
        "end_cycle_master": end["cycle"],
        "delta_master_clocks": master,
        "delta_cpu_cycles_exact": (
            master // MASTER_CLOCKS_PER_M68K_CYCLE
            if master % MASTER_CLOCKS_PER_M68K_CYCLE == 0
            else None
        ),
        "delta_cpu_cycles_ceil": (
            master + MASTER_CLOCKS_PER_M68K_CYCLE - 1
        )
        // MASTER_CLOCKS_PER_M68K_CYCLE,
        "master_remainder_mod_7": master % MASTER_CLOCKS_PER_M68K_CYCLE,
    }


def analyse(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    expected_counts = {
        "MS": 2,
        "ME": 2,
        "RS": 2,
        "RH": 2,
        "HS": 2,
        "SP": 2,
        "PQ": 2,
        "QE": 2,
        "RE": 2,
        "VE": 2,
        "VA": 2,
        "VC": 2,
        "Q": 8,
        "DS": 8,
        "DE": 8,
        "VP": 2,
        "VS": 2,
        "VO": EXPECTED_ORIGIN_HITS,
        "VK": 2,
        "VX": 2,
    }
    phases = {phase: _by_phase(records, phase) for phase in expected_counts}
    for phase, expected in expected_counts.items():
        if len(phases[phase]) != expected:
            errors.append(f"{phase} marker count {len(phases[phase])} != {expected}")
    if errors:
        return {"status": "fail", "errors": errors, "marker_counts": {k: len(v) for k, v in phases.items()}}

    expected_order: list[tuple[str, int, int | None]] = []

    def append_main(frame_number: int) -> None:
        expected_order.extend(
            (phase, frame_number, None)
            for phase in ("MS", "ME", "RS", "RH", "HS", "SP", "PQ", "QE", "RE")
        )

    def append_vblank(prior_frame: int, current_frame: int) -> None:
        expected_order.extend(
            (
                ("VE", prior_frame, None),
                ("VA", prior_frame, None),
                ("VC", current_frame, None),
            )
        )
        expected_order.extend(
            ("Q", current_frame, index)
            for index in range(EXPECTED_DMA_PER_FRAME)
        )
        for _index in range(EXPECTED_DMA_PER_FRAME):
            expected_order.extend(
                (("DS", current_frame, None), ("DE", current_frame, None))
            )
        expected_order.extend(
            (
                ("VP", current_frame, None),
                ("VS", current_frame, None),
                ("VK", current_frame, None),
                ("VX", current_frame, None),
            )
        )

    append_main(1)
    append_vblank(1, 2)
    append_main(2)
    append_vblank(2, 3)
    observed_order = [
        (
            str(record["phase"]),
            int(record["frame"]),
            int(record["index"]) if record["phase"] == "Q" else None,
        )
        for record in records
    ]
    if observed_order != expected_order:
        errors.append("raw marker order/frame/index contract mismatch")
    cycle_records = [record for record in records if "cycle" in record]
    if any(
        int(later["cycle"]) <= int(earlier["cycle"])
        for earlier, later in zip(cycle_records, cycle_records[1:])
    ):
        errors.append("raw marker cycles are not strictly monotonic")

    def frame_map(phase: str) -> dict[int, Mapping[str, Any]]:
        mapped = {record["frame"]: record for record in phases[phase]}
        if len(mapped) != len(phases[phase]):
            errors.append(f"{phase} has duplicate frame markers")
        return mapped

    ms = frame_map("MS")
    me = frame_map("ME")
    rs = frame_map("RS")
    rh = frame_map("RH")
    hs = frame_map("HS")
    sp = frame_map("SP")
    pq = frame_map("PQ")
    qe = frame_map("QE")
    re_end = frame_map("RE")
    # VE/VA label the prior completed debug frame; other VBlank markers label
    # the just-incremented current frame.
    ve_prior = frame_map("VE")
    va_prior = frame_map("VA")
    vc = frame_map("VC")
    vp = frame_map("VP")
    vs = frame_map("VS")
    vk = frame_map("VK")
    vx = frame_map("VX")
    if any(
        set(mapped) != {1, 2}
        for mapped in (ms, me, rs, rh, hs, sp, pq, qe, re_end)
    ):
        errors.append("main/renderer markers are not exactly debug frames 1 and 2")
    if set(ve_prior) != {1, 2} or set(va_prior) != {1, 2}:
        errors.append("VBlank entry/ack markers are not exactly prior frames 1 and 2")
    for phase, mapped in (("VC", vc), ("VP", vp), ("VS", vs), ("VK", vk), ("VX", vx)):
        if set(mapped) != {2, 3}:
            errors.append(f"{phase} markers are not exactly current frames 2 and 3")

    for frame in (1, 2):
        if ms[frame]["input"] != UP_INPUT_MASK or ms[frame]["d0"] != UP_INPUT_MASK:
            errors.append(f"frame {frame} gameplay call did not bind held Up input")
        if ms[frame]["tick"] != frame - 1 or me[frame]["tick"] != frame:
            errors.append(f"frame {frame} gameplay tick transition is not {frame - 1}->{frame}")
        if rs[frame]["tick"] != frame or re_end[frame]["tick"] != frame:
            errors.append(f"frame {frame} renderer tick binding mismatch")
        boundaries = (rs[frame], rh[frame], hs[frame], sp[frame], pq[frame], qe[frame], re_end[frame])
        if any(boundary["tick"] != frame for boundary in boundaries):
            errors.append(f"frame {frame} renderer subphase tick binding mismatch")
        if any(boundary["sr"] & 0x0700 != 0x0700 for boundary in boundaries):
            errors.append(f"frame {frame} renderer is not mask-7 at both boundaries")
        if rs[frame]["pending"] != 0 or re_end[frame]["pending"] != 1:
            errors.append(f"frame {frame} renderer pending ownership is not 0->1")
        if any(boundary["pending"] != 0 for boundary in (rh[frame], hs[frame], sp[frame], pq[frame])):
            errors.append(f"frame {frame} pending published before queue release boundary")
        if qe[frame]["pending"] != 1:
            errors.append(f"frame {frame} queue publish did not release pending last")

    expected_queue = (
        (0x00FF8000, 0xF000, 0x0140, 0x8000, 0),
        (0x00FF8600, 0xC000, 0x0028, 0x8001, 0),
        (0x00FF8650, 0xC080, 0x0028, 0x8001, 0),
        (0x00FF86A0, 0xC100, 0x0028, 0x8001, 0),
    )
    queue_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    dma_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    dma_end_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    for frame in (2, 3):
        queue = sorted(
            (record for record in phases["Q"] if record["frame"] == frame),
            key=lambda record: record["index"],
        )
        starts = [record for record in phases["DS"] if record["frame"] == frame]
        ends = [record for record in phases["DE"] if record["frame"] == frame]
        queue_by_frame[frame] = queue
        dma_by_frame[frame] = starts
        dma_end_by_frame[frame] = ends
        if len(queue) != EXPECTED_DMA_PER_FRAME or len(starts) != EXPECTED_DMA_PER_FRAME or len(ends) != EXPECTED_DMA_PER_FRAME:
            errors.append(f"frame {frame} queue/DMA cardinality mismatch")
            continue
        for index, (record, expected) in enumerate(zip(queue, expected_queue)):
            actual = (
                record["source"],
                record["destination"],
                record["words"],
                record["flags"],
                record["age"],
            )
            if record["index"] != index or actual != expected:
                errors.append(f"frame {frame} queue record {index} mismatch: {actual!r}")
        for index, (start, end, expected) in enumerate(zip(starts, ends, expected_queue)):
            if (start["source"], start["destination"], start["words"]) != expected[:3]:
                errors.append(f"frame {frame} DMA call {index} does not match queue record")
            if start["commit_words"] != sum(item[2] for item in expected_queue[: index + 1]):
                errors.append(f"frame {frame} DMA call {index} cumulative words mismatch")
            if end["commit_words"] != start["commit_words"]:
                errors.append(f"frame {frame} DMA call {index} end cumulative words mismatch")
            if end["cycle"] <= start["cycle"]:
                errors.append(f"frame {frame} DMA call {index} cycle order invalid")
        if vc[frame]["pending"] != 1 or vc[frame]["published_depth"] != 4 or vc[frame]["build_depth"] != 4 or vc[frame]["build_words"] != 440:
            errors.append(f"frame {frame} commit-entry queue control mismatch")
        if vp[frame]["commit_words"] != 440 or vp[frame]["debug_words"] != 440 or vp[frame]["depth"] != 0 or vp[frame]["pending"] != 0:
            errors.append(f"frame {frame} post-DMA queue control mismatch")
        if not (va_prior[frame - 1]["entry_status"] & 0x0008):
            errors.append(f"frame {frame} did not enter during VBlank")
        if vs[frame]["entry_status"] != va_prior[frame - 1]["entry_status"]:
            errors.append(f"frame {frame} VS entry status is not bound to VA")

    if not (vs[2]["exit_status"] & 0x0008):
        errors.append("frame 2 lost VBlank before exit unexpectedly")
    if not (vs[3]["exit_status"] & 0x0008):
        errors.append("frame 3 lost VBlank after shutter packing")
    origin = phases["VO"][0] if phases["VO"] else None
    if origin is not None:
        errors.append("post-packing run unexpectedly reached the overrun origin")
    if vk[2]["counter"] != 0 or vk[3]["counter"] != 0:
        errors.append("post-packing overrun counter is not zero on frames 2 and 3")
    for frame in (2, 3):
        if vk[frame]["d0"] & 0xFFFF != 8 or vk[frame]["d1"] & 0xFFFF != 8:
            errors.append(f"frame {frame} clean VBlank predicate is not d0=d1=$0008")
        if vx[frame]["counter"] != 0 or vx[frame]["token"] != 1:
            errors.append(f"frame {frame} rte counter/token binding mismatch")
    if vx[2]["sequence"] != 4 or vx[3]["sequence"] != 6:
        errors.append("VBlank seqlock exit sequence mismatch")

    spans: dict[str, Any] = {
        "gameplay": {},
        "renderer_masked": {},
        "renderer_subphases": {},
        "vblank": {},
        "dma_calls": {},
    }
    try:
        for frame in (1, 2):
            spans["gameplay"][str(frame)] = _span(ms[frame], me[frame], f"gameplay frame {frame}")
            spans["renderer_masked"][str(frame)] = _span(rs[frame], re_end[frame], f"renderer frame {frame}")
            spans["renderer_subphases"][str(frame)] = {
                "prebuild_validation": _span(rs[frame], rh[frame], f"renderer frame {frame} prebuild"),
                "build_hud": _span(rh[frame], hs[frame], f"renderer frame {frame} HUD"),
                "build_sat": _span(hs[frame], sp[frame], f"renderer frame {frame} SAT"),
                "prepare_reset_overlay": _span(sp[frame], pq[frame], f"renderer frame {frame} overlay"),
                "queue_begin_enqueue_publish": _span(pq[frame], qe[frame], f"renderer frame {frame} queue"),
                "post_publish_result": _span(qe[frame], re_end[frame], f"renderer frame {frame} post-publish"),
            }
        for frame in (2, 3):
            prior = frame - 1
            body = {
                "entry_to_ack": _span(ve_prior[prior], va_prior[prior], f"VBlank {frame} entry-to-ack"),
                "ack_to_commit": _span(va_prior[prior], vc[frame], f"VBlank {frame} ack-to-commit"),
                "commit_to_post_dma": _span(vc[frame], vp[frame], f"VBlank {frame} commit"),
                "post_dma_to_exit_status": _span(vp[frame], vs[frame], f"VBlank {frame} post-DMA-to-status"),
                "entry_to_exit_status": _span(ve_prior[prior], vs[frame], f"VBlank {frame} entry-to-status"),
                "entry_to_rte_opcode": _span(ve_prior[prior], vx[frame], f"VBlank {frame} entry-to-rte"),
                "renderer_restore_to_entry": _span(re_end[prior], ve_prior[prior], f"frame {frame} renderer-restore-to-VBlank"),
            }
            spans["vblank"][str(frame)] = body
            spans["dma_calls"][str(frame)] = [
                _span(start, end, f"frame {frame} DMA call {index}")
                for index, (start, end) in enumerate(zip(dma_by_frame[frame], dma_end_by_frame[frame]))
            ]
        entry_delta = _positive_delta(ve_prior[1]["cycle"], ve_prior[2]["cycle"], "consecutive VBlank entry")
        nominal_frame3_entry = ve_prior[1]["cycle"] + NTSC_FRAME_MASTER_CLOCKS
        renderer2_mask_contains_nominal = (
            rs[2]["cycle"] <= nominal_frame3_entry <= re_end[2]["cycle"]
        )
        entry_phase = {
            "frame2_entry_cycle_master": ve_prior[1]["cycle"],
            "frame3_entry_cycle_master": ve_prior[2]["cycle"],
            "consecutive_entry_delta_master_clocks": entry_delta,
            "nominal_ntsc_frame_master_clocks": NTSC_FRAME_MASTER_CLOCKS,
            "frame3_relative_lateness_growth_master_clocks": entry_delta - NTSC_FRAME_MASTER_CLOCKS,
            "frame3_relative_lateness_growth_cpu_cycles_rational": f"{entry_delta - NTSC_FRAME_MASTER_CLOCKS}/{MASTER_CLOCKS_PER_M68K_CYCLE}",
            "frame2_phase_mod_nominal_frame": ve_prior[1]["cycle"] % NTSC_FRAME_MASTER_CLOCKS,
            "frame3_phase_mod_nominal_frame": ve_prior[2]["cycle"] % NTSC_FRAME_MASTER_CLOCKS,
            "nominal_frame3_entry_cycle_master": nominal_frame3_entry,
            "renderer2_mask_start_cycle_master": rs[2]["cycle"],
            "renderer2_mask_end_cycle_master": re_end[2]["cycle"],
            "renderer2_mask_contains_nominal_frame3_entry": renderer2_mask_contains_nominal,
            "renderer2_headroom_before_nominal_frame3_entry_master_clocks": (
                nominal_frame3_entry - re_end[2]["cycle"]
            ),
            "entry_delta_not_later_than_nominal": (
                entry_delta <= NTSC_FRAME_MASTER_CLOCKS
            ),
        }
        if entry_delta > NTSC_FRAME_MASTER_CLOCKS:
            errors.append(
                "post-packing frame-3 entry still has positive relative lateness"
            )
        if renderer2_mask_contains_nominal:
            errors.append(
                "nominal frame-3 entry remains inside renderer-2 mask overhang"
            )
        if re_end[2]["cycle"] >= nominal_frame3_entry:
            errors.append(
                "renderer-2 mask does not finish before nominal frame-3 entry"
            )
    except (KeyError, EvidenceFailure) as error:
        errors.append(str(error))
        entry_phase = {}

    renderer_cycles = [spans["renderer_masked"][str(frame)]["delta_cpu_cycles_ceil"] for frame in (1, 2)] if spans["renderer_masked"] else []
    gameplay_cycles = [spans["gameplay"][str(frame)]["delta_cpu_cycles_ceil"] for frame in (1, 2)] if spans["gameplay"] else []
    dominant = None
    if renderer_cycles and gameplay_cycles:
        dominant = {
            "renderer_masked_cpu_cycles": renderer_cycles,
            "gameplay_cpu_cycles": gameplay_cycles,
            "largest_measured_component": (
                "renderer_masked" if max(renderer_cycles) >= max(gameplay_cycles) else "gameplay"
            ),
            "frame2_gameplay_plus_renderer_cpu_cycles": gameplay_cycles[1] + renderer_cycles[1],
            "ntsc_frame_cpu_cycles_floor": NTSC_FRAME_MASTER_CLOCKS // MASTER_CLOCKS_PER_M68K_CYCLE,
        }

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "marker_counts": {phase: len(items) for phase, items in phases.items()},
        "input_binding": {
            "expected_mask": UP_INPUT_MASK,
            "observed_gameplay_call_masks": [ms[1]["input"], ms[2]["input"]],
            "observed_d0_masks": [ms[1]["d0"], ms[2]["d0"]],
        },
        "queue_records": {str(frame): [dict(item) for item in queue_by_frame[frame]] for frame in (2, 3)},
        "dma_calls": {str(frame): [dict(item) for item in dma_by_frame[frame]] for frame in (2, 3)},
        "origin_signature": dict(origin) if origin is not None else None,
        "phase_spans": spans,
        "entry_phase_comparison": entry_phase,
        "dominant_component": dominant,
        "conclusion": (
            "post-packing frame 3 retained VBlank through the same four 440-word DMA "
            "transfers without reaching the overrun origin or incrementing the counter"
            if not errors
            else "diagnostic contract failed; no timing conclusion is admitted"
        ),
    }


def _find_game_window(process_id: int, timeout_seconds: float) -> dict[str, Any]:
    if os.name != "nt":
        raise EvidenceFailure("window discovery requires native Windows")
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        matches: list[dict[str, Any]] = []

        @callback_type
        def callback(window: int, _parameter: int) -> bool:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value != process_id or not user32.IsWindowVisible(window):
                return True
            title_buffer = ctypes.create_unicode_buffer(512)
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(window, title_buffer, len(title_buffer))
            user32.GetClassNameW(window, class_buffer, len(class_buffer))
            matches.append(
                {"handle": int(window), "title": title_buffer.value, "class": class_buffer.value}
            )
            return True

        user32.EnumWindows(callback, 0)
        non_console = [item for item in matches if item["class"] != "ConsoleWindowClass"]
        if non_console:
            preferred = [item for item in non_console if "recursion94" in item["title"].lower()]
            return (preferred or non_console)[0]
        time.sleep(0.05)
    raise EvidenceFailure(f"BlastEm PID {process_id} did not expose an SDL game window")


def _peek_owned_console_input(kernel32: Any, handle: Any, record_type: Any) -> dict[str, Any]:
    """Non-consuming bounded input telemetry, used only on diagnostic failure."""
    kernel32.PeekConsoleInputW.argtypes = (wintypes.HANDLE, ctypes.POINTER(record_type), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    kernel32.PeekConsoleInputW.restype = wintypes.BOOL
    kernel32.GetNumberOfConsoleInputEvents.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
    available, count = wintypes.DWORD(), wintypes.DWORD()
    if not kernel32.GetNumberOfConsoleInputEvents(handle, ctypes.byref(available)):
        return {"error": "GetNumberOfConsoleInputEvents failed", "consumed_records": 0}
    records = (record_type * 64)()
    if not kernel32.PeekConsoleInputW(handle, records, 64, ctypes.byref(count)):
        return {"error": "PeekConsoleInputW failed", "consumed_records": 0}
    observed = []
    for record in records[:count.value]:
        item = {"event_type": record.event_type}
        if record.event_type == 1:
            key = record.event.key
            item.update(key_down=bool(key.key_down), repeat_count=key.repeat_count, virtual_key=key.virtual_key, scan_code=key.scan_code, unicode_codepoint=ord(key.character.unicode), control_state=key.control_state)
        observed.append(item)
    return {"available_count": available.value, "maximum_records": 64, "records": observed, "consumed_records": 0}


def _staged_console_helper_payload(
    process_id: int,
    script: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Drive Reset->READY, inject canonical Up, then finish measurement."""
    if os.name != "nt":
        raise EvidenceFailure("staged console helper requires native Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = (wintypes.DWORD,)
    kernel32.AttachConsole.restype = wintypes.BOOL
    kernel32.FreeConsole.argtypes = ()
    kernel32.FreeConsole.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE

    class Coord(ctypes.Structure):
        _fields_ = (("X", ctypes.c_short), ("Y", ctypes.c_short))

    class SmallRect(ctypes.Structure):
        _fields_ = (
            ("Left", ctypes.c_short),
            ("Top", ctypes.c_short),
            ("Right", ctypes.c_short),
            ("Bottom", ctypes.c_short),
        )

    class ConsoleInfo(ctypes.Structure):
        _fields_ = (
            ("size", Coord),
            ("cursor", Coord),
            ("attributes", wintypes.WORD),
            ("window", SmallRect),
            ("maximum", Coord),
        )

    class Character(ctypes.Union):
        _fields_ = (("unicode", ctypes.c_wchar), ("ascii", ctypes.c_char))

    class KeyEvent(ctypes.Structure):
        _fields_ = (
            ("key_down", wintypes.BOOL),
            ("repeat_count", wintypes.WORD),
            ("virtual_key", wintypes.WORD),
            ("scan_code", wintypes.WORD),
            ("character", Character),
            ("control_state", wintypes.DWORD),
        )

    class EventUnion(ctypes.Union):
        _fields_ = (("key", KeyEvent), ("padding", ctypes.c_byte * 16))

    class InputRecord(ctypes.Structure):
        _fields_ = (("event_type", wintypes.WORD), ("event", EventUnion))

    kernel32.GetConsoleScreenBufferInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ConsoleInfo),
    )
    kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    kernel32.ReadConsoleOutputCharacterW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        Coord,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.ReadConsoleOutputCharacterW.restype = wintypes.BOOL
    kernel32.WriteConsoleInputW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(InputRecord),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.WriteConsoleInputW.restype = wintypes.BOOL

    kernel32.FreeConsole()
    deadline = time.monotonic() + timeout_seconds
    while not kernel32.AttachConsole(process_id):
        if time.monotonic() >= deadline:
            raise EvidenceFailure(
                f"could not attach to BlastEm console PID {process_id}: "
                f"WinError {ctypes.get_last_error()}"
            )
        time.sleep(0.02)

    generic_read_write = 0xC0000000
    share_read_write = 0x00000003
    open_existing = 3
    console_in = kernel32.CreateFileW(
        "CONIN$", generic_read_write, share_read_write, None, open_existing, 0, None
    )
    console_out = kernel32.CreateFileW(
        "CONOUT$", generic_read_write, share_read_write, None, open_existing, 0, None
    )
    invalid = ctypes.c_void_p(-1).value
    if console_in in {None, invalid} or console_out in {None, invalid}:
        raise EvidenceFailure(
            f"could not open CONIN$/CONOUT$: WinError {ctypes.get_last_error()}"
        )

    def read_console() -> tuple[str, dict[str, int]]:
        info = ConsoleInfo()
        if not kernel32.GetConsoleScreenBufferInfo(console_out, ctypes.byref(info)):
            raise EvidenceFailure(
                "GetConsoleScreenBufferInfo failed: "
                f"WinError {ctypes.get_last_error()}"
            )
        rows = info.cursor.Y + 1
        count = info.size.X * rows
        buffer = ctypes.create_unicode_buffer(count + 1)
        read = wintypes.DWORD()
        if not kernel32.ReadConsoleOutputCharacterW(
            console_out,
            buffer,
            count,
            Coord(0, 0),
            ctypes.byref(read),
        ):
            raise EvidenceFailure(
                "ReadConsoleOutputCharacterW failed: "
                f"WinError {ctypes.get_last_error()}"
            )
        raw = buffer[: read.value]
        lines = [
            raw[offset : offset + info.size.X].rstrip()
            for offset in range(0, len(raw), info.size.X)
        ]
        return "\n".join(lines).rstrip() + "\n", {
            "width": info.size.X,
            "height": info.size.Y,
            "cursor_x": info.cursor.X,
            "cursor_y": info.cursor.Y,
            "current_row_utf16": [ord(value) for value in raw[info.cursor.Y * info.size.X : (info.cursor.Y + 1) * info.size.X]],
            "raw_screen_buffer_utf16le_sha256": hashlib.sha256(raw.encode("utf-16-le")).hexdigest(),
            "raw_screen_buffer": raw,
        }

    def send_console(payload_text: str) -> int:
        payload = payload_text.replace("\n", "\r")
        records = (InputRecord * len(payload))()
        for index, character in enumerate(payload):
            records[index].event_type = 1
            records[index].event.key.key_down = 1
            records[index].event.key.repeat_count = 1
            records[index].event.key.character.unicode = character
        written = wintypes.DWORD()
        if not kernel32.WriteConsoleInputW(
            console_in, records, len(records), ctypes.byref(written)
        ):
            raise EvidenceFailure(
                f"WriteConsoleInputW failed: WinError {ctypes.get_last_error()}"
            )
        if written.value != len(records):
            raise EvidenceFailure(
                f"console input truncated: {written.value}/{len(records)}"
            )
        return written.value

    def wait_for_marker(marker: str) -> tuple[str, dict[str, int]]:
        transcript = ""
        console_info: dict[str, int] = {}
        while time.monotonic() < deadline:
            transcript, console_info = read_console()
            stripped = [line.strip() for line in transcript.splitlines()]
            if marker in stripped and stripped[-1:] == [">"]:
                return transcript, console_info
            time.sleep(0.02)
        tail = "\n".join(transcript.splitlines()[-50:])
        failure = EvidenceFailure(
            f"native debugger marker timed out: {marker}; console tail:\n{tail}"
        )
        failure.console_text = transcript
        failure.console_geometry = console_info
        failure.console_input_peek = _peek_owned_console_input(kernel32, console_in, InputRecord)
        raise failure

    initial = ""
    console_info: dict[str, int] = {}
    while time.monotonic() < deadline:
        initial, console_info = read_console()
        if "200: move #$2700, SR" in initial and any(
            line.strip() == ">" for line in initial.splitlines()
        ):
            break
        time.sleep(0.02)
    else:
        failure = EvidenceFailure("native debugger initial prompt timed out")
        failure.console_text = initial
        failure.console_geometry = console_info
        failure.console_input_peek = _peek_owned_console_input(kernel32, console_in, InputRecord)
        raise failure

    script_written = send_console(script)
    _ready_text, console_info = wait_for_marker(READY_MARKER)
    remaining = max(0.1, deadline - time.monotonic())
    input_window = _find_game_window(process_id, min(20.0, remaining))
    post_virtual_key(input_window["handle"], pressed=True, **UP_KEY)
    continue_written = send_console("continue\n")
    transcript, console_info = wait_for_marker(COMPLETE_MARKER)
    quit_written = send_console("quit\n")
    time.sleep(0.1)
    try:
        final_transcript, console_info = read_console()
    except EvidenceFailure:
        final_transcript = transcript + "> quit\n"
    kernel32.FreeConsole()
    return {
        "console_text": final_transcript,
        "script_input_records": script_written,
        "continue_input_records": continue_written,
        "quit_input_records": quit_written,
        "input_window": input_window,
        "input_injected_at_marker": READY_MARKER,
        "console": console_info,
    }


def _console_helper_main(args: argparse.Namespace) -> int:
    script = Path(args.command_file).read_text("utf-8", errors="strict")
    try:
        result = _staged_console_helper_payload(
            args.process_id,
            script,
            args.timeout_seconds,
        )
    except EvidenceFailure as error:
        result = {
            "error": str(error),
            "console_text": getattr(error, "console_text", ""),
            "console": getattr(error, "console_geometry", {}),
            "full_console_available": hasattr(error, "console_text"),
            "console_input_peek": getattr(error, "console_input_peek", None),
        }
        sys.stdout.write(json.dumps(result, sort_keys=True))
        sys.stdout.flush()
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True))
    sys.stdout.flush()
    return 0


def _trace_text(script: str, console: str) -> str:
    return (
        f"# {REPORT_SCHEMA}\n"
        "# NONCANONICAL DIAGNOSTIC; native debugger; read-only guest observation\n"
        "----- COMMAND SCRIPT -----\n"
        f"{script.rstrip()}\n"
        "----- CONSOLE TRANSCRIPT -----\n"
        f"{console.rstrip()}\n"
    )


def collect(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise EvidenceFailure("live phase diagnostic requires native Windows Python")
    root = Path(args.root).resolve()
    rom_path = root / ROM_RELATIVE_PATH
    symbols_path = root / SYMBOL_RELATIVE_PATH
    emulator_dir = root / ".toolcache" / "blastem"
    emulator = emulator_dir / "blastem.exe"
    default_config = emulator_dir / "default.cfg"
    collector_path = root / COLLECTOR_RELATIVE_PATH
    baseline_report_path = root / BASELINE_REPORT_RELATIVE_PATH
    baseline_trace_path = root / BASELINE_TRACE_RELATIVE_PATH
    report_path = Path(args.report).resolve()
    trace_path = Path(args.trace).resolve()
    for path, label in (
        (rom_path, "B03 Debug ROM"),
        (symbols_path, "B03 symbols"),
        (emulator, "pinned BlastEm"),
        (default_config, "pinned BlastEm config"),
        (collector_path, "diagnostic collector and native console helper"),
        (baseline_report_path, "pre-packing phase report"),
        (baseline_trace_path, "pre-packing phase trace"),
    ):
        if not path.is_file():
            raise EvidenceFailure(f"{label} missing: {path}")
    if sha256_file(rom_path) != PINNED_ROM_SHA256:
        raise EvidenceFailure("pinned B03 Debug ROM hash drift")
    if sha256_file(symbols_path) != PINNED_SYMBOL_SHA256:
        raise EvidenceFailure("pinned B03 symbol hash drift")
    if sha256_file(emulator) != PINNED_EMULATOR_SHA256:
        raise EvidenceFailure("pinned BlastEm executable hash drift")
    if sha256_file(default_config) != PINNED_DEFAULT_CONFIG_SHA256:
        raise EvidenceFailure("pinned BlastEm config hash drift")
    if sha256_file(baseline_report_path) != BASELINE_REPORT_SHA256:
        raise EvidenceFailure("pre-packing phase report hash drift")
    if sha256_file(baseline_trace_path) != BASELINE_TRACE_SHA256:
        raise EvidenceFailure("pre-packing phase trace hash drift")
    baseline_report = json.loads(
        baseline_report_path.read_text("utf-8", errors="strict")
    )
    if (
        baseline_report.get("status") != "diagnostic_pass"
        or baseline_report.get("provenance", {}).get("rom", {}).get("sha256")
        != BASELINE_ROM_SHA256
    ):
        raise EvidenceFailure("pre-packing phase report semantic binding drift")
    verify_no_user_blastem_override()

    symbols = parse_vasm_symbols(symbols_path)
    required_symbols = {
        "DEBUG_FRAME_COUNTER",
        "DEBUG_BOOT_STAGE",
        "DEBUG_FRAME_TOKEN",
        "DEBUG_SEQUENCE",
        "INPUT_HELD",
        "ENGINE_TICK",
        "DEBUG_LAST_VDP_STATUS",
        "DEBUG_VBLANK_OVERRUN_COUNT",
        "DEBUG_DMA_WORDS_FRAME",
        "DEBUG_DMA_QUEUE_DEPTH",
        "DMA_QUEUE_FRAME_PENDING",
        "DMA_QUEUE_PUBLISHED_DEPTH",
        "DMA_QUEUE_BUILD_DEPTH",
        "DMA_QUEUE_BUILD_WORDS",
        "DMA_QUEUE_COMMIT_WORDS",
        "DMA_QUEUE_ENTRIES",
        *PC_SYMBOL_BINDINGS.values(),
        "GameplayRendererBuildAndQueue",
    }
    missing = sorted(required_symbols - symbols.keys())
    if missing:
        raise EvidenceFailure("B03 symbol map missing: " + ", ".join(missing))
    pc_contract = _pc_contract(rom_path.read_bytes(), symbols)
    script = build_command_script(symbols)
    policy_errors = command_policy_errors(script, symbols)
    if policy_errors:
        raise EvidenceFailure("; ".join(policy_errors))

    process: subprocess.Popen[Any] | None = None
    helper: subprocess.Popen[str] | None = None
    helper_result: dict[str, Any] | None = None
    window_record: dict[str, Any] | None = None
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="recursion94-m5-phase-") as temp_text:
        temporary = Path(temp_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(emulator_dir, temporary)
        controller = write_standard_pad_profile(default_config, temporary)
        controller["session_isolation"] = apply_blastem_session_isolation(
            Path(controller["path"]), temporary / "session-data"
        )
        command_file = temporary / "phase-commands.txt"
        command_file.write_text(script, encoding="utf-8", newline="\n")
        launch = [
            str(isolated_runtime / "blastem.exe"),
            "-d",
            "-g",
            "-m",
            "gen",
            "-r",
            "U",
            str(rom_path),
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
        process = subprocess.Popen(launch, cwd=isolated_runtime, creationflags=creationflags)
        try:
            helper_command = [
                sys.executable,
                str(collector_path),
                "--console-helper",
                "--process-id",
                str(process.pid),
                "--command-file",
                str(command_file),
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
            stdout, stderr = helper.communicate(timeout=args.timeout_seconds + 10)
            if helper.returncode != 0:
                raise EvidenceFailure(
                    f"console helper failed ({helper.returncode}): {stderr.strip()}"
                )
            helper_result = json.loads(stdout)
            window_record = helper_result["input_window"]
            process.wait(timeout=5)
            if process.returncode != 0:
                raise EvidenceFailure(f"BlastEm exit code {process.returncode}")
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

    if helper_result is None:
        raise EvidenceFailure("native console helper returned no result")
    console_text = helper_result["console_text"]
    console_errors = console_contract_errors(console_text, script)
    records = parse_markers(console_text)
    analysis = analyse(records)
    baseline_analysis = baseline_report["analysis"]
    baseline_renderer_cycles = [
        baseline_analysis["phase_spans"]["renderer_masked"][frame][
            "delta_cpu_cycles_ceil"
        ]
        for frame in ("1", "2")
    ]
    post_renderer_cycles = [
        analysis["phase_spans"]["renderer_masked"][frame][
            "delta_cpu_cycles_ceil"
        ]
        for frame in ("1", "2")
    ]
    baseline_sat_cycles = [
        baseline_analysis["phase_spans"]["renderer_subphases"][frame][
            "build_sat"
        ]["delta_cpu_cycles_ceil"]
        for frame in ("1", "2")
    ]
    post_sat_cycles = [
        analysis["phase_spans"]["renderer_subphases"][frame]["build_sat"][
            "delta_cpu_cycles_ceil"
        ]
        for frame in ("1", "2")
    ]
    renderer_savings = [
        before - after
        for before, after in zip(baseline_renderer_cycles, post_renderer_cycles)
    ]
    sat_savings = [
        before - after
        for before, after in zip(baseline_sat_cycles, post_sat_cycles)
    ]
    same_queue_records = (
        baseline_analysis["queue_records"] == analysis["queue_records"]
    )
    same_dma_call_arguments = all(
        [
            {
                key: record[key]
                for key in ("source", "destination", "words", "commit_words")
            }
            for record in baseline_analysis["dma_calls"][frame]
        ]
        == [
            {
                key: record[key]
                for key in ("source", "destination", "words", "commit_words")
            }
            for record in analysis["dma_calls"][frame]
        ]
        for frame in ("2", "3")
    )
    attribution_errors: list[str] = []
    if not all(after < before for before, after in zip(baseline_renderer_cycles, post_renderer_cycles)):
        attribution_errors.append("post-packing renderer span did not improve both frames")
    if not all(after < before for before, after in zip(baseline_sat_cycles, post_sat_cycles)):
        attribution_errors.append("post-packing SAT span did not improve both frames")
    if min(renderer_savings) <= BASELINE_CADENCE_DEBT_CPU_CYCLES:
        attribution_errors.append(
            "minimum renderer savings did not exceed baseline cadence debt"
        )
    if not same_queue_records or not same_dma_call_arguments:
        attribution_errors.append(
            "post-packing queue/DMA workload differs from preserved baseline"
        )
    baseline_comparison = {
        "status": (
            "pass"
            if analysis["status"] == "pass" and not attribution_errors
            else "fail"
        ),
        "attribution_errors": attribution_errors,
        "baseline_cadence_debt_cpu_cycles": BASELINE_CADENCE_DEBT_CPU_CYCLES,
        "baseline_rom_sha256": BASELINE_ROM_SHA256,
        "post_packing_rom_sha256": PINNED_ROM_SHA256,
        "baseline_origin_signature": baseline_analysis["origin_signature"],
        "post_packing_origin_signature": analysis["origin_signature"],
        "baseline_renderer_masked_cpu_cycles": baseline_renderer_cycles,
        "post_packing_renderer_masked_cpu_cycles": post_renderer_cycles,
        "renderer_savings_cpu_cycles": renderer_savings,
        "minimum_renderer_savings_cpu_cycles": min(renderer_savings),
        "renderer_savings_exceeds_baseline_debt": (
            min(renderer_savings) > BASELINE_CADENCE_DEBT_CPU_CYCLES
        ),
        "baseline_sat_cpu_cycles": baseline_sat_cycles,
        "post_packing_sat_cpu_cycles": post_sat_cycles,
        "sat_savings_cpu_cycles": sat_savings,
        "baseline_entry_lateness_growth_master_clocks": baseline_analysis[
            "entry_phase_comparison"
        ]["frame3_relative_lateness_growth_master_clocks"],
        "post_packing_entry_lateness_growth_master_clocks": analysis[
            "entry_phase_comparison"
        ]["frame3_relative_lateness_growth_master_clocks"],
        "same_queue_records": same_queue_records,
        "same_dma_call_arguments": same_dma_call_arguments,
        "counter_transition": {
            "baseline_frame3_counter": next(
                record["counter"]
                for record in baseline_report["records"]
                if record["phase"] == "VX" and record["frame"] == 3
            ),
            "post_packing_frame3_counter": next(
                record["counter"]
                for record in records
                if record["phase"] == "VX" and record["frame"] == 3
            ),
        },
    }
    trace = _trace_text(script, console_text)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(trace, encoding="utf-8", newline="\n")
    report = {
        "schema": REPORT_SCHEMA,
        "evidence_type": EVIDENCE_TYPE,
        "status": (
            "diagnostic_pass"
            if analysis["status"] == "pass"
            and not console_errors
            and not attribution_errors
            else "diagnostic_fail"
        ),
        "canonical": False,
        "diagnostic_only": True,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "bounds": {
            "authoritative_frames_observed": [2, 3],
            "maximum_observed_frame": MAX_OBSERVED_FRAMES,
            "expected_dma_calls_per_frame": EXPECTED_DMA_PER_FRAME,
            "maximum_dma_calls": MAX_DMA_CALLS,
            "input": "held Up for the exact first two canonical gameplay calls",
            "debugger_guest_writes": 0,
        },
        "read_only_contract": {
            "allowed_commands": sorted(ALLOWED_COMMANDS),
            "command_policy_errors": policy_errors,
            "console_contract_errors": console_errors,
            "guest_memory_writes": 0,
            "guest_register_writes": 0,
            "guest_pc_writes": 0,
            "hardware_status_reads_by_debugger": 0,
            "status_binding": (
                "entry and exit VDP status are values already read by VBlankInterrupt"
            ),
        },
        "pc_contract": pc_contract,
        "host_input": {
            "window": window_record,
            "key": {"name": "Up", **UP_KEY},
            "pressed_before_first_continue": True,
            "guest_observation": analysis.get("input_binding"),
        },
        "command_script": script,
        "command_script_sha256": _sha256_bytes(script.encode("utf-8")),
        "console_sha256": _sha256_bytes(console_text.encode("utf-8")),
        "console_geometry": helper_result["console"],
        "records": records,
        "records_sha256": _sha256_bytes(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "analysis": analysis,
        "baseline_comparison": baseline_comparison,
        "provenance": {
            "rom": _file_record(rom_path, root),
            "symbols": _file_record(symbols_path, root),
            "emulator": {
                **_file_record(emulator, root),
                "version": BLASTEM_VERSION,
                "source_node": BLASTEM_SOURCE_NODE,
            },
            "default_config": _file_record(default_config, root),
            "collector": _file_record(collector_path, root),
            "native_console_helper": {
                **_file_record(collector_path, root),
                "entrypoint": "--console-helper",
                "staged_ready_marker": READY_MARKER,
            },
            "trace": _file_record(trace_path, root),
            "pre_packing_report": _file_record(baseline_report_path, root),
            "pre_packing_trace": _file_record(baseline_trace_path, root),
            "isolated_runtime": runtime_copy,
            "isolated_config": controller,
        },
        "limitations": [
            "Noncanonical diagnostic only; it does not replace the two-session 1732-step gate.",
            "Pinned BlastEm cycle evidence is not physical-hardware timing evidence.",
            "Only the first two canonical held-Up authoritative calls and VBlank frames 2/3 are observed.",
            "Debugger stops do not advance the emulated master-cycle counter, but this run is not a throughput claim.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if report["status"] != "diagnostic_pass":
        raise EvidenceFailure("phase diagnostic contract failed; artifacts preserved")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--report", default=DEFAULT_REPORT_RELATIVE_PATH)
    parser.add_argument("--trace", default=DEFAULT_TRACE_RELATIVE_PATH)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--console-helper", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--process-id", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--command-file", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.console_helper:
        if args.process_id is None or not args.command_file:
            raise SystemExit("console-helper arguments are incomplete")
        return _console_helper_main(args)
    root = Path(args.root).resolve()
    if not Path(args.report).is_absolute():
        args.report = str(root / args.report)
    if not Path(args.trace).is_absolute():
        args.trace = str(root / args.trace)
    try:
        report = collect(args)
    except (EvidenceFailure, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"M5 B03 phase diagnostic failed: {error}", file=sys.stderr)
        return 1
    print(
        "M5 B03 noncanonical phase diagnostic passed: "
        f"{report['analysis']['conclusion']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
