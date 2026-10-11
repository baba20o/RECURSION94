#!/usr/bin/env python3
"""Collect hash-bound Motorola 68000 cycle evidence with BlastEm's native debugger.

The pinned Windows BlastEm executable is linked as a GUI application.  Its
native debugger therefore cannot be driven through ordinary redirected
stdin/stdout: ``terminal_win.c`` allocates a console and reopens CONIN$/CONOUT$.
This collector launches an owned console, attaches a short-lived helper to it,
and uses Win32 console input/output APIs.  No GDB port is opened and no guest
register or memory write command is permitted.

BlastEm's new 68000 core exposes the native-debugger variable ``cycle`` in
Genesis master-clock units.  The Genesis divider is seven, so every measured
interval must have a master-cycle delta divisible by seven before it is
reported as Motorola 68000 CPU cycles.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    parse_vasm_symbols,
    sha256_file,
    verify_no_user_blastem_override,
    write_standard_pad_profile,
)


REPORT_SCHEMA = "r94-m4-native-cycle-evidence-v1"
EVIDENCE_TYPE = "blastem_native_debugger_m68000_cycle_budget"
COLLECTOR_RELATIVE_PATH = "tools/m4_cycle_evidence.py"
LAUNCHER_RELATIVE_PATH = "tools/m4_cycle_probe.ps1"
SCHEMA_RELATIVE_PATH = "tools/report_schemas/m4-cycle-evidence.schema.json"
DEFAULT_REPORT_RELATIVE_PATH = "artifacts/reports/m4-cycle-evidence.json"
DEFAULT_TRACE_RELATIVE_PATH = (
    "artifacts/traces/m4-cycle-native-debugger.log"
)

BLASTEM_REVISION = "732f5689d438"
BLASTEM_VERSION = f"0.6.3-pre-{BLASTEM_REVISION}"
BLASTEM_EXPECTED_SHA256 = (
    "22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803"
)
MASTER_CLOCKS_PER_M68K_CYCLE = 7
M68K_COUNTER_MODULUS = 1 << 32

NTSC_FRAME_CPU_CYCLES = 128_005
MAIN_RELEASE_LIMIT_CPU_CYCLES = 83_203
MAIN_RELEASE_PERCENT = 65
RESET_TRANSITION_LIMIT_CPU_CYCLES = NTSC_FRAME_CPU_CYCLES * 2

NTSC_TOTAL_LINES = 262
NTSC_ACTIVE_LINES = 224
NTSC_VBLANK_LINES = NTSC_TOTAL_LINES - NTSC_ACTIVE_LINES
GENESIS_MASTER_CLOCKS_PER_LINE = 3_420
NTSC_VBLANK_MASTER_CLOCKS = (
    NTSC_VBLANK_LINES * GENESIS_MASTER_CLOCKS_PER_LINE
)
VBLANK_RELEASE_PERCENT = 70
VBLANK_RELEASE_LIMIT_CPU_CYCLES = (
    NTSC_VBLANK_MASTER_CLOCKS * VBLANK_RELEASE_PERCENT
) // (MASTER_CLOCKS_PER_M68K_CYCLE * 100)
M68K_AUTOVECTOR_ENTRY_CPU_CYCLES = 44
M68K_RTE_CPU_CYCLES = 20

EXPECTED_AUTHORITATIVE_STEPS = 720
EXPECTED_CHECKPOINT_TICKS = tuple(range(60, 720, 60))
EXPECTED_INITIAL_ATTEMPT = 0
EXPECTED_RESET_ATTEMPT = 1
BOOT_STAGE_RUNNING = 4

MAIN_START_SIGNATURE_PREFIX = bytes.fromhex("5379")
MAIN_START_SIGNATURE_SUFFIX = bytes.fromhex("46FC2500")
BSR_WORD_OPCODE = bytes.fromhex("6100")
JSR_ABSOLUTE_LONG_OPCODE = bytes.fromhex("4EB9")
RTE_OPCODE = bytes.fromhex("4E73")

REQUIRED_SYMBOLS = (
    "MainLoop",
    "MainLoopPostGameplayStep",
    "MainLoopWaitForVBlank",
    "MainLoopGameplayStepCall",
    "MainLoopPostGameplayStepCall",
    "MainLoopCommitResetCall",
    "MainLoopPostCommitReset",
    "GameplayRendererBuildAndQueue",
    "GameplayStep",
    "GameplayCommitAndReset",
    "EngineBuildProjection",
    "VBlankInterrupt",
    "DEBUG_FRAME_TOKEN",
    "DEBUG_FRAME_COUNTER",
    "DEBUG_BOOT_STAGE",
    "ENGINE_TICK",
    "ENGINE_ATTEMPT",
    "ENGINE_CHECKPOINT_COUNT",
    "TIMELINE_ATTEMPT_LENGTH",
)

SESSION_LAYOUT = (
    ("main-1", "main"),
    ("main-2", "main"),
    ("vblank-1", "vblank"),
    ("vblank-2", "vblank"),
)

FORBIDDEN_DEBUGGER_COMMANDS = frozenset(
    {
        "set",
        "load",
        "softreset",
        "sr",
        "bindup",
        "binddown",
        "watchpoint",
        "delete",
        "variable",
        "array",
        "append",
        "save",
    }
)
ALLOWED_DEBUGGER_COMMANDS = frozenset(
    {
        "breakpoint",
        "commands",
        "printf",
        "if",
        "else",
        "continue",
        "condition",
        "end",
        "quit",
    }
)

MAIN_MARKER_RE = re.compile(
    r"^R94C M (?P<phase>[SGCDRE]) "
    r"(?P<cycle>[0-9A-Fa-f]+) "
    r"(?P<frame>[0-9A-Fa-f]+) "
    r"(?P<tick>[0-9A-Fa-f]+) "
    r"(?P<attempt>[0-9A-Fa-f]+) "
    r"(?P<checkpoints>[0-9A-Fa-f]+) "
    r"(?P<attempt_length>[0-9A-Fa-f]+)$"
)
VBLANK_MARKER_RE = re.compile(
    r"^R94C V (?P<phase>[SE]) "
    r"(?P<cycle>[0-9A-Fa-f]+) "
    r"(?P<frame>[0-9A-Fa-f]+) "
    r"(?P<attempt>[0-9A-Fa-f]+)$"
)
PROJECTION_MARKER_RE = re.compile(
    r"^R94C P (?P<phase>[SE]) "
    r"(?P<cycle>[0-9A-Fa-f]+) "
    r"(?P<frame>[0-9A-Fa-f]+) "
    r"(?P<tick>[0-9A-Fa-f]+) "
    r"(?P<attempt>[0-9A-Fa-f]+) "
    r"(?P<checkpoints>[0-9A-Fa-f]+)$"
)
ERROR_PATTERNS = (
    "Failed to eval",
    "Unrecognized",
    "not a recognized command",
    "requires at least",
    "takes a max",
    "Breakpoint ",
    "fgets failed",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": _relative(path, root),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _rom_slice(rom: bytes, address: int, count: int) -> bytes:
    if address < 0 or address + count > len(rom):
        raise EvidenceFailure(
            f"ROM range ${address:06X}+{count} is outside {len(rom)} bytes"
        )
    return rom[address : address + count]


def _decode_supported_call(
    rom: bytes, address: int
) -> tuple[str, int, int] | None:
    """Decode the two exact call forms admitted by the cycle contract.

    The frozen M4 main loop used ``bsr.w``.  The larger M5 renderer requires
    the range-independent absolute-long ``jsr`` form.  Other JSR effective
    addresses and the byte-displacement BSR form remain outside this
    fail-closed evidence contract.
    """
    opcode = _rom_slice(rom, address, 2)
    if opcode == BSR_WORD_OPCODE:
        displacement = int.from_bytes(
            _rom_slice(rom, address + 2, 2), "big", signed=True
        )
        target = (address + 2 + displacement) & 0xFFFFFF
        return ("bsr.w", target, 4)
    if opcode == JSR_ABSOLUTE_LONG_OPCODE:
        target = int.from_bytes(
            _rom_slice(rom, address + 2, 4), "big", signed=False
        )
        return ("jsr_abs_l", target, 6)
    return None


def _decode_main_loop_branch(
    rom: bytes, address: int
) -> tuple[str, int, int] | None:
    """Decode the exact 68000 BRA forms used after the renderer call."""
    opcode = _rom_slice(rom, address, 2)
    if opcode[0] != 0x60:
        return None
    if opcode[1] != 0:
        displacement = int.from_bytes(opcode[1:2], "big", signed=True)
        return ("bra.s", (address + 2 + displacement) & 0xFFFFFF, 2)
    displacement = int.from_bytes(
        _rom_slice(rom, address + 2, 2), "big", signed=True
    )
    return ("bra.w", (address + 2 + displacement) & 0xFFFFFF, 4)


def derive_pc_contract(
    rom: bytes, symbols: Mapping[str, int]
) -> dict[str, Any]:
    """Derive and validate measured PCs from symbols plus exact ROM opcodes."""
    missing = sorted(set(REQUIRED_SYMBOLS) - set(symbols))
    if missing:
        raise EvidenceFailure(
            "cycle evidence symbol map is missing: " + ", ".join(missing)
        )

    main_loop = symbols["MainLoop"]
    renderer_call = symbols["MainLoopPostGameplayStep"]
    main_wait = symbols["MainLoopWaitForVBlank"]
    if not main_loop < renderer_call < main_wait:
        raise EvidenceFailure("main-loop symbols are not strictly ordered")

    frame_token = symbols["DEBUG_FRAME_TOKEN"] & 0xFFFFFFFF
    signature = (
        MAIN_START_SIGNATURE_PREFIX
        + frame_token.to_bytes(4, "big")
        + MAIN_START_SIGNATURE_SUFFIX
    )
    main_window = _rom_slice(rom, main_loop, renderer_call - main_loop)
    offsets = [
        match.start()
        for match in re.finditer(re.escape(signature), main_window)
    ]
    if len(offsets) != 1:
        raise EvidenceFailure(
            "main-loop token-decrement/start signature is not unique: "
            f"{len(offsets)} matches"
        )
    signature_address = main_loop + offsets[0]
    main_start = signature_address + 6

    renderer_instruction = _decode_supported_call(rom, renderer_call)
    if renderer_instruction is None:
        raise EvidenceFailure(
            "MainLoopPostGameplayStep is neither bsr.w nor absolute-long jsr"
        )
    renderer_call_kind, renderer_target, renderer_call_bytes = (
        renderer_instruction
    )
    renderer_symbol = symbols["GameplayRendererBuildAndQueue"]
    if renderer_target != renderer_symbol:
        raise EvidenceFailure(
            "renderer call target does not match GameplayRendererBuildAndQueue"
        )

    renderer_calls = []
    for call in range(main_start, renderer_call + renderer_call_bytes, 2):
        instruction = _decode_supported_call(rom, call)
        if instruction is not None and instruction[1] == renderer_symbol:
            renderer_calls.append((call, instruction[0], instruction[2]))
    expected_renderer_call = (
        renderer_call,
        renderer_call_kind,
        renderer_call_bytes,
    )
    if renderer_calls != [expected_renderer_call]:
        raise EvidenceFailure(
            "main loop must contain exactly one supported call opcode "
            "targeting GameplayRendererBuildAndQueue at "
            f"MainLoopPostGameplayStep; found {renderer_calls!r}"
        )

    main_end = renderer_call + renderer_call_bytes
    main_branch = _decode_main_loop_branch(rom, main_end)
    if main_branch is None:
        raise EvidenceFailure(
            "instruction after renderer call is not a supported main-loop bra"
        )
    if main_branch[1] != main_loop:
        raise EvidenceFailure(
            "instruction after renderer call does not branch to MainLoop"
        )

    def calls_to(target_address: int) -> list[int]:
        calls = []
        for call in range(main_start, renderer_call, 2):
            if _rom_slice(rom, call, 2) != BSR_WORD_OPCODE:
                continue
            call_displacement = int.from_bytes(
                _rom_slice(rom, call + 2, 2), "big", signed=True
            )
            call_target = (call + 2 + call_displacement) & 0xFFFFFF
            if call_target == target_address:
                calls.append(call)
        return calls

    gameplay_calls = calls_to(symbols["GameplayStep"])
    reset_calls = calls_to(symbols["GameplayCommitAndReset"])
    if len(gameplay_calls) != 1 or len(reset_calls) != 1:
        raise EvidenceFailure(
            "main loop must have one GameplayStep and one "
            "GameplayCommitAndReset call"
        )
    gameplay_call = gameplay_calls[0]
    gameplay_return = gameplay_call + 4
    reset_call = reset_calls[0]
    reset_return = reset_call + 4
    labelled_terminal_pcs = {
        "MainLoopGameplayStepCall": gameplay_call,
        "MainLoopPostGameplayStepCall": gameplay_return,
        "MainLoopCommitResetCall": reset_call,
        "MainLoopPostCommitReset": reset_return,
    }
    for label, derived in labelled_terminal_pcs.items():
        if symbols[label] != derived:
            raise EvidenceFailure(
                f"{label} ${symbols[label]:X} != derived ${derived:X}"
            )
    if not (
        main_start
        < gameplay_call
        < gameplay_return
        < reset_call
        < reset_return
        < renderer_call
    ):
        raise EvidenceFailure("terminal/reset main-loop PCs are not ordered")

    vblank_entry = symbols["VBlankInterrupt"]
    scan = _rom_slice(rom, vblank_entry, min(0x600, len(rom) - vblank_entry))
    rte_offsets = [
        offset
        for offset in range(0, len(scan) - 1, 2)
        if scan[offset : offset + 2] == RTE_OPCODE
    ]
    if len(rte_offsets) != 1:
        raise EvidenceFailure(
            "VBlankInterrupt must have exactly one shared rte opcode in "
            f"the bounded scan, found {len(rte_offsets)}"
        )
    vblank_rte = vblank_entry + rte_offsets[0]
    if vblank_rte - vblank_entry > 0x400:
        raise EvidenceFailure("VBlankInterrupt rte is outside bounded scan")

    gameplay_step = symbols["GameplayStep"]
    projection_targets = {
        "EngineBuildProjection": symbols["EngineBuildProjection"],
    }
    # M5 retains the engine projection but routes gameplay publication through
    # a bounded wrapper which appends the campaign tail and republishes the
    # complete CRC.  Derive the measured target from actual GameplayStep call
    # opcodes; do not silently keep measuring an uncalled legacy routine.
    if "GameplayBuildProjection" in symbols:
        projection_targets["GameplayBuildProjection"] = symbols[
            "GameplayBuildProjection"
        ]
    projection_calls_by_symbol = {
        name: [] for name in projection_targets
    }
    projection_scan = _rom_slice(
        rom, gameplay_step, min(0x400, len(rom) - gameplay_step)
    )
    for offset in range(0, len(projection_scan) - 3, 2):
        if projection_scan[offset : offset + 2] != BSR_WORD_OPCODE:
            continue
        displacement = int.from_bytes(
            projection_scan[offset + 2 : offset + 4],
            "big",
            signed=True,
        )
        call = gameplay_step + offset
        call_target = (call + 2 + displacement) & 0xFFFFFF
        for name, address in projection_targets.items():
            if call_target == address:
                projection_calls_by_symbol[name].append(call)
    eligible_projection_targets = [
        name
        for name, calls in projection_calls_by_symbol.items()
        if len(calls) >= 3
    ]
    if len(eligible_projection_targets) != 1:
        raise EvidenceFailure(
            "GameplayStep must have exactly one projection publication target "
            "with at least three call sites; found "
            f"{eligible_projection_targets!r}"
        )
    projection_symbol = eligible_projection_targets[0]
    projection_entry = projection_targets[projection_symbol]
    projection_calls = projection_calls_by_symbol[projection_symbol]
    projection_checkpoint_call = projection_calls[0]
    projection_checkpoint_return = projection_checkpoint_call + 4
    if _rom_slice(rom, projection_checkpoint_return, 2) != bytes.fromhex(
        "4279"
    ):
        raise EvidenceFailure(
            "regular checkpoint projection return is not followed by clr.w"
        )

    measured = {
        "main_start": main_start,
        "terminal_step_end": gameplay_return,
        "reset_start": reset_call,
        "reset_end": reset_return,
        "main_renderer": renderer_call,
        "main_end": main_end,
        "vblank_entry": vblank_entry,
        "vblank_rte": vblank_rte,
        "projection_entry": projection_entry,
        "projection_checkpoint_return": projection_checkpoint_return,
    }
    main_end_derivation = {
        "renderer_call_symbol": renderer_call,
        "renderer_target": renderer_target,
        "call_hex": _rom_slice(
            rom, renderer_call, renderer_call_bytes
        ).hex().upper(),
        "following_hex": _rom_slice(rom, main_end, 2).hex().upper(),
    }
    if renderer_call_kind == "bsr.w":
        # Retain the capture-time M4 field for historical report semantics.
        main_end_derivation["end_is_instruction_after_bsr_return"] = True
    else:
        main_end_derivation.update(
            {
                "renderer_call_kind": renderer_call_kind,
                "renderer_call_bytes": renderer_call_bytes,
                "end_is_instruction_after_call_return": True,
            }
        )

    return {
        "addresses": measured,
        "main_start_derivation": {
            "main_loop_symbol": main_loop,
            "token_decrement_address": signature_address,
            "start_is_first_instruction_after_token_decrement": True,
            "signature_hex": signature.hex().upper(),
        },
        "main_end_derivation": main_end_derivation,
        "terminal_reset_derivation": {
            "gameplay_step_call": gameplay_call,
            "terminal_step_end_is_instruction_after_bsr": True,
            "gameplay_step_call_hex": _rom_slice(
                rom, gameplay_call, 4
            ).hex().upper(),
            "reset_start_is_commit_reset_bsr": True,
            "reset_call": reset_call,
            "reset_end_is_instruction_after_bsr": True,
            "reset_call_hex": _rom_slice(
                rom, reset_call, 4
            ).hex().upper(),
        },
        "vblank_derivation": {
            "entry_symbol": vblank_entry,
            "rte_is_only_even_rte_in_bounded_scan": True,
            "bounded_scan_bytes": len(scan),
            "bounded_scan_rte_count": len(rte_offsets),
            "rte_offset": vblank_rte - vblank_entry,
            "rte_hex": _rom_slice(rom, vblank_rte, 2).hex().upper(),
            "running_marker_stage_symbol": "DEBUG_BOOT_STAGE",
            "running_marker_stage_value": BOOT_STAGE_RUNNING,
        },
        "projection_derivation": {
            "gameplay_step_symbol": gameplay_step,
            "projection_entry_symbol": projection_symbol,
            "projection_entry_address": projection_entry,
            "candidate_calls": projection_calls_by_symbol,
            "projection_calls_in_bounded_gameplay_step_scan": (
                projection_calls
            ),
            "regular_checkpoint_call_is_first_projection_call": True,
            "checkpoint_call": projection_checkpoint_call,
            "checkpoint_return_is_instruction_after_bsr": True,
            "checkpoint_call_hex": _rom_slice(
                rom, projection_checkpoint_call, 4
            ).hex().upper(),
        },
        "rom_bytes": {
            name: _rom_slice(rom, address, 2).hex().upper()
            for name, address in measured.items()
        },
    }


def _memory(address: int, size: str) -> str:
    if size not in {"b", "w", "l"}:
        raise ValueError(f"invalid native-debugger memory size {size!r}")
    return f"[${address:X}].{size}"


def build_command_script(
    mode: str,
    pc_contract: Mapping[str, Any],
    symbols: Mapping[str, int],
) -> str:
    """Return the only debugger script accepted for a measured session."""
    addresses = pc_contract["addresses"]
    frame = _memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    tick = _memory(symbols["ENGINE_TICK"], "w")
    attempt = _memory(symbols["ENGINE_ATTEMPT"], "w")
    checkpoints = _memory(symbols["ENGINE_CHECKPOINT_COUNT"], "w")
    attempt_length = _memory(symbols["TIMELINE_ATTEMPT_LENGTH"], "w")
    vblank_running_condition = None
    if mode in {"main", "vblank"}:
        boot_stage = _memory(symbols["DEBUG_BOOT_STAGE"], "w")
        vblank_running_condition = (
            f"{boot_stage} = ${BOOT_STAGE_RUNNING:X}"
        )

    if mode == "main":
        return "\n".join(
            (
                f"breakpoint ${addresses['main_start']:X}",
                f"breakpoint ${addresses['terminal_step_end']:X}",
                f"breakpoint ${addresses['reset_start']:X}",
                f"breakpoint ${addresses['reset_end']:X}",
                f"breakpoint ${addresses['main_renderer']:X}",
                f"breakpoint ${addresses['main_end']:X}",
                f"breakpoint ${addresses['vblank_entry']:X}",
                f"breakpoint ${addresses['vblank_rte']:X}",
                f"condition 1 {tick} = $2D0",
                f"condition 6 {vblank_running_condition}",
                f"condition 7 {vblank_running_condition}",
                "commands 0",
                (
                    'printf "R94C M S %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} "
                    f"{checkpoints} {attempt_length}"
                ),
                "continue",
                "end",
                "commands 1",
                (
                    'printf "R94C M G %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} "
                    f"{checkpoints} {attempt_length}"
                ),
                "continue",
                "end",
                "commands 2",
                (
                    'printf "R94C M C %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} "
                    f"{checkpoints} {attempt_length}"
                ),
                "continue",
                "end",
                "commands 3",
                (
                    'printf "R94C M D %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} "
                    f"{checkpoints} {attempt_length}"
                ),
                "continue",
                "end",
                "commands 4",
                (
                    'printf "R94C M R %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} "
                    f"{checkpoints} {attempt_length}"
                ),
                "continue",
                "end",
                "commands 5",
                (
                    'printf "R94C M E %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} "
                    f"{checkpoints} {attempt_length}"
                ),
                f"if {attempt} < $1",
                "continue",
                "else",
                'printf "R94C M COMPLETE\\n"',
                "end",
                "end",
                "commands 6",
                (
                    'printf "R94C V S %x %x %x\\n" '
                    f"cycle {frame} {attempt}"
                ),
                "continue",
                "end",
                "commands 7",
                (
                    'printf "R94C V E %x %x %x\\n" '
                    f"cycle {frame} {attempt}"
                ),
                "continue",
                "end",
                "continue",
                "",
            )
        )
    if mode == "vblank":
        return "\n".join(
            (
                f"breakpoint ${addresses['vblank_entry']:X}",
                f"breakpoint ${addresses['vblank_rte']:X}",
                f"condition 0 {vblank_running_condition}",
                f"condition 1 {vblank_running_condition}",
                "commands 0",
                (
                    'printf "R94C V S %x %x %x\\n" '
                    f"cycle {frame} {attempt}"
                ),
                "continue",
                "end",
                "commands 1",
                (
                    'printf "R94C V E %x %x %x\\n" '
                    f"cycle {frame} {attempt}"
                ),
                f"if {attempt} < $1",
                "continue",
                "else",
                'printf "R94C V COMPLETE\\n"',
                "end",
                "end",
                "continue",
                "",
            )
        )
    if mode == "projection":
        return "\n".join(
            (
                f"breakpoint ${addresses['projection_entry']:X}",
                (
                    "breakpoint "
                    f"${addresses['projection_checkpoint_return']:X}"
                ),
                "commands 0",
                f"if {tick} < $3C",
                "continue",
                "else",
                (
                    'printf "R94C P S %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} {checkpoints}"
                ),
                "continue",
                "end",
                "end",
                "commands 1",
                f"if {tick} < $3C",
                "continue",
                "else",
                (
                    'printf "R94C P E %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} {attempt} {checkpoints}"
                ),
                'printf "R94C P COMPLETE\\n"',
                "end",
                "end",
                "continue",
                "",
            )
        )
    raise ValueError(f"unknown cycle session mode {mode!r}")


def command_script_errors(
    script: str,
    mode: str,
    pc_contract: Mapping[str, Any],
    symbols: Mapping[str, int],
) -> list[str]:
    errors: list[str] = []
    expected = build_command_script(mode, pc_contract, symbols)
    if script != expected:
        errors.append("native-debugger command script is not exact")
    for raw in script.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        command = stripped.split(maxsplit=1)[0].lower()
        if command in FORBIDDEN_DEBUGGER_COMMANDS:
            errors.append(f"forbidden debugger command: {command}")
        if command not in ALLOWED_DEBUGGER_COMMANDS:
            errors.append(f"unapproved debugger command: {command}")
    if re.search(r"(?im)^\s*(?:set|load|save|bind(?:up|down))\b", script):
        errors.append("guest/debugger mutation command detected")
    return errors


def parse_console_markers(text: str, mode: str) -> list[dict[str, Any]]:
    patterns = {
        "main": MAIN_MARKER_RE,
        "vblank": VBLANK_MARKER_RE,
        "projection": PROJECTION_MARKER_RE,
    }
    try:
        pattern = patterns[mode]
    except KeyError as error:
        raise ValueError(f"unknown marker mode {mode!r}") from error
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = pattern.fullmatch(line.strip())
        if not match:
            continue
        record: dict[str, Any] = {"phase": match.group("phase")}
        for name, value in match.groupdict().items():
            if name != "phase":
                record[name] = int(value, 16)
        records.append(record)
    return records


def console_transcript_errors(
    text: str, mode: str, expected_script: str
) -> list[str]:
    errors: list[str] = []
    prefixes = {"main": "M", "vblank": "V", "projection": "P"}
    complete = f"R94C {prefixes[mode]} COMPLETE"
    if sum(line.strip() == complete for line in text.splitlines()) != 1:
        errors.append(f"{mode} completion marker is not unique")
    if "200: move #$2700, SR" not in text:
        errors.append("initial ResetEntry debugger stop is absent")
    if "> continue" not in text:
        errors.append("initial continue command echo is absent")
    for pattern in ERROR_PATTERNS:
        if pattern == "Breakpoint ":
            # Normal successful hits include "68K Breakpoint N hit".
            continue
        if pattern in text:
            errors.append(f"debugger console contains {pattern!r}")
    if re.search(r"(?im)^\s*>\s*(?:set|load|save|softreset|bind)", text):
        errors.append("debugger console echoes a mutation command")
    expected_lines = [line for line in expected_script.splitlines() if line]
    for line in expected_lines:
        if line not in text:
            errors.append(f"debugger console did not echo command: {line}")
    records = parse_console_markers(text, mode)
    if not records:
        errors.append(f"{mode} console contains no measurement records")
    return errors


def _counter_master_delta(start: int, end: int) -> tuple[int, bool]:
    if not 0 <= start < M68K_COUNTER_MODULUS:
        raise ValueError(f"start cycle is outside uint32: {start}")
    if not 0 <= end < M68K_COUNTER_MODULUS:
        raise ValueError(f"end cycle is outside uint32: {end}")
    if end >= start:
        delta = end - start
        wrapped = False
    elif start >= 0xF0000000 and end <= 0x0FFFFFFF:
        delta = end + M68K_COUNTER_MODULUS - start
        wrapped = True
    else:
        raise ValueError(
            "cycle counter moved backwards without a uint32 wrap; "
            "BlastEm cycle rebasing or transcript mispairing is not accepted"
        )
    if delta <= 0:
        raise ValueError("cycle delta must be positive")
    return delta, wrapped


def _normalized_master_delta(start: int, end: int) -> tuple[int, bool]:
    delta, wrapped = _counter_master_delta(start, end)
    if delta % MASTER_CLOCKS_PER_M68K_CYCLE:
        raise ValueError(
            f"master-cycle delta {delta} is not divisible by "
            f"{MASTER_CLOCKS_PER_M68K_CYCLE}"
        )
    return delta, wrapped


def _pair_records(
    records: Sequence[Mapping[str, Any]], mode: str
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    if len(records) % 2:
        raise ValueError(f"{mode} marker count is odd: {len(records)}")
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for index in range(0, len(records), 2):
        start, end = records[index], records[index + 1]
        if start.get("phase") != "S" or end.get("phase") != "E":
            raise ValueError(
                f"{mode} marker phases are not S/E at pair {index // 2}"
            )
        pairs.append((start, end))
    return pairs


def _main_record_groups(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Mapping[str, Any]]]:
    groups: list[dict[str, Mapping[str, Any]]] = []
    cursor = 0
    for index in range(EXPECTED_AUTHORITATIVE_STEPS):
        phases = (
            ("S", "G", "C", "D", "R", "E")
            if index == EXPECTED_AUTHORITATIVE_STEPS - 1
            else ("S", "R", "E")
        )
        if cursor + len(phases) > len(records):
            raise ValueError(
                f"main marker stream ends inside interval {index}"
            )
        selected = records[cursor : cursor + len(phases)]
        actual = tuple(record.get("phase") for record in selected)
        if actual != phases:
            raise ValueError(
                f"main marker phases {actual} != {phases} at interval "
                f"{index}"
            )
        groups.append(dict(zip(phases, selected, strict=True)))
        cursor += len(phases)
    if cursor != len(records):
        raise ValueError(
            f"main marker stream has {len(records) - cursor} extra records"
        )
    return groups


def _vblank_body_spans(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    spans = []
    for index, (start, end) in enumerate(_pair_records(records, "vblank")):
        if end["frame"] != start["frame"] + 1:
            raise ValueError(
                f"VBlank frame counter did not advance once at pair {index}"
            )
        delta_master, wrapped = _normalized_master_delta(
            start["cycle"], end["cycle"]
        )
        if wrapped:
            raise ValueError(
                "VBlank body cycle wrap is unsupported during a 720-step "
                "main capture"
            )
        spans.append(
            {
                "index": index,
                "start_cycle_master": start["cycle"],
                "end_cycle_master": end["cycle"],
                "delta_master": delta_master,
                "delta_cpu_cycles": (
                    delta_master // MASTER_CLOCKS_PER_M68K_CYCLE
                ),
                "start_frame_counter": start["frame"],
                "end_frame_counter": end["frame"],
                "attempt": end["attempt"],
            }
        )
    if len(spans) < EXPECTED_AUTHORITATIVE_STEPS:
        raise ValueError(
            f"main capture embedded VBlank count {len(spans)} is below "
            f"{EXPECTED_AUTHORITATIVE_STEPS}"
        )
    return spans


def _main_span(
    start: Mapping[str, Any],
    end: Mapping[str, Any],
    *,
    index: int,
    name: str,
    vblank_bodies: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    frame_delta = end["frame"] - start["frame"]
    if frame_delta < 0:
        raise ValueError(
            f"main {name} span {index} frame counter moved backwards: "
            f"{frame_delta}"
        )
    delta_master, wrapped = _counter_master_delta(
        start["cycle"], end["cycle"]
    )
    if wrapped:
        raise ValueError(
            f"main {name} span {index} wraps the cycle counter"
        )
    contained = [
        body
        for body in vblank_bodies
        if body["start_cycle_master"] >= start["cycle"]
        and body["end_cycle_master"] <= end["cycle"]
    ]
    partial = [
        body
        for body in vblank_bodies
        if body["start_cycle_master"] < end["cycle"]
        and body["end_cycle_master"] > start["cycle"]
        and body not in contained
    ]
    if partial:
        raise ValueError(
            f"main {name} span {index} partially overlaps a VBlank body"
        )
    subtracted_master = sum(body["delta_master"] for body in contained)
    exclusive_master = delta_master - subtracted_master
    if exclusive_master <= 0:
        raise ValueError(
            f"main {name} span {index} has no positive exclusive work"
        )
    raw_divider_remainder = (
        delta_master % MASTER_CLOCKS_PER_M68K_CYCLE
    )
    divider_remainder = (
        exclusive_master % MASTER_CLOCKS_PER_M68K_CYCLE
    )
    raw_cpu = (
        delta_master + MASTER_CLOCKS_PER_M68K_CYCLE - 1
    ) // MASTER_CLOCKS_PER_M68K_CYCLE
    delta_cpu = (
        exclusive_master + MASTER_CLOCKS_PER_M68K_CYCLE - 1
    ) // MASTER_CLOCKS_PER_M68K_CYCLE
    return {
        "start_cycle_master": start["cycle"],
        "end_cycle_master": end["cycle"],
        "raw_delta_master": delta_master,
        "raw_delta_cpu_cycles": raw_cpu,
        "raw_cpu_cycles_rational": (
            f"{delta_master}/{MASTER_CLOCKS_PER_M68K_CYCLE}"
        ),
        "raw_divider_remainder": raw_divider_remainder,
        "vblank_body_count": len(contained),
        "vblank_body_master_subtracted": subtracted_master,
        "vblank_body_indices": [body["index"] for body in contained],
        "delta_master": exclusive_master,
        "delta_cpu_cycles": delta_cpu,
        "cpu_cycles_rational": (
            f"{exclusive_master}/{MASTER_CLOCKS_PER_M68K_CYCLE}"
        ),
        "divider_remainder": divider_remainder,
        "divider_exact": divider_remainder == 0,
        "frame_delta": frame_delta,
        "interrupts_inside": len(contained),
        "counter_wrapped": wrapped,
    }


def _median(values: Sequence[int]) -> int | float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    total = ordered[midpoint - 1] + ordered[midpoint]
    return total // 2 if total % 2 == 0 else total / 2


def _main_segment_summary(
    intervals: Sequence[Mapping[str, Any]],
    segment: str,
    limit: int,
    gated_categories: frozenset[str],
) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    for name in ("ordinary", "checkpoint", "terminal_reset"):
        selected = [
            interval
            for interval in intervals
            if interval["category"] == name
        ]
        if not selected:
            raise ValueError(f"main category {name} has no intervals")
        maximum = max(
            selected,
            key=lambda item: item[segment]["delta_cpu_cycles"],
        )
        maximum_span = maximum[segment]
        maximum_raw = max(
            selected,
            key=lambda item: item[segment]["raw_delta_cpu_cycles"],
        )
        interrupt_inside_count = sum(
            item[segment]["interrupts_inside"] for item in selected
        )
        clean = [
            item[segment]["delta_cpu_cycles"]
            for item in selected
            if item[segment]["interrupts_inside"] == 0
        ]
        categories[name] = {
            "count": len(selected),
            "max_cpu_cycles": maximum_span["delta_cpu_cycles"],
            "max_delta_master": maximum_span["delta_master"],
            "max_at_tick": maximum["end_tick"],
            "max_raw_cpu_cycles": maximum_raw[segment][
                "raw_delta_cpu_cycles"
            ],
            "max_raw_delta_master": maximum_raw[segment][
                "raw_delta_master"
            ],
            "interrupt_inside_count": interrupt_inside_count,
            "clean_interval_count": len(clean),
            "clean_min_cpu_cycles": min(clean) if clean else None,
            "clean_median_cpu_cycles": _median(clean) if clean else None,
            "clean_max_cpu_cycles": max(clean) if clean else None,
            "limit_cpu_cycles": limit,
            "headroom_cpu_cycles": (
                limit - maximum_span["delta_cpu_cycles"]
            ),
            "status": (
                (
                    "pass"
                    if maximum_span["delta_cpu_cycles"] <= limit
                    else "fail"
                )
                if name in gated_categories
                else "observed"
            ),
        }
    return {
        "limit_cpu_cycles": limit,
        "categories": categories,
        "max_cpu_cycles": max(
            interval[segment]["delta_cpu_cycles"]
            for interval in intervals
        ),
    }


def analyse_main_records(
    records: Sequence[Mapping[str, Any]],
    interrupt_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    groups = _main_record_groups(records)
    vblank_bodies = _vblank_body_spans(interrupt_records)
    intervals: list[dict[str, Any]] = []
    checkpoint_ticks: list[int] = []
    terminal_parts: dict[str, Any] | None = None
    for index, group in enumerate(groups):
        start = group["S"]
        renderer = group["R"]
        end = group["E"]
        if start["tick"] != index:
            raise ValueError(
                f"main start tick {start['tick']} != pair index {index}"
            )
        if start["attempt"] != EXPECTED_INITIAL_ATTEMPT:
            raise ValueError("main start attempt changed before terminal pair")
        if start["attempt_length"] != index:
            raise ValueError(
                "attempt length at main start does not match tick"
            )
        for field in (
            "tick",
            "attempt",
            "checkpoints",
            "attempt_length",
        ):
            if renderer[field] != end[field]:
                raise ValueError(
                    f"renderer changed authoritative field {field} at "
                    f"main interval {index}"
                )
        if not start["frame"] <= renderer["frame"] <= end["frame"]:
            raise ValueError(
                f"main interval {index} frame markers are not monotonic"
            )
        terminal = index == EXPECTED_AUTHORITATIVE_STEPS - 1
        if terminal:
            gameplay_end = group["G"]
            reset_start = group["C"]
            reset_end = group["D"]
            if (
                end["attempt"] != EXPECTED_RESET_ATTEMPT
                or end["tick"] != 0
                or end["attempt_length"] != 0
            ):
                raise ValueError(
                    "terminal interval did not complete natural timeout/reset"
                )
            for record in (gameplay_end, reset_start):
                if (
                    record["attempt"] != EXPECTED_INITIAL_ATTEMPT
                    or record["tick"] != EXPECTED_AUTHORITATIVE_STEPS
                    or record["attempt_length"]
                    != EXPECTED_AUTHORITATIVE_STEPS
                ):
                    raise ValueError(
                        "terminal GameplayStep/reset-entry state is invalid"
                    )
            for record in (reset_end, renderer, end):
                if (
                    record["attempt"] != EXPECTED_RESET_ATTEMPT
                    or record["tick"] != 0
                    or record["attempt_length"] != 0
                ):
                    raise ValueError(
                        "post-reset terminal state is invalid"
                    )
            category = "terminal_reset"
        else:
            if (
                end["attempt"] != EXPECTED_INITIAL_ATTEMPT
                or end["tick"] != index + 1
                or end["attempt_length"] != index + 1
            ):
                raise ValueError(
                    f"main end state is not the next authoritative tick at {index}"
                )
            checkpoint_delta = (
                end["checkpoints"] - start["checkpoints"]
            )
            if checkpoint_delta == 1:
                category = "checkpoint"
                checkpoint_ticks.append(end["tick"])
            elif checkpoint_delta == 0:
                category = "ordinary"
            else:
                raise ValueError(
                    f"checkpoint counter delta {checkpoint_delta} at tick "
                    f"{end['tick']}"
                )

        simulation_span = _main_span(
            start,
            renderer,
            index=index,
            name="simulation",
            vblank_bodies=vblank_bodies,
        )
        renderer_span = _main_span(
            renderer,
            end,
            index=index,
            name="renderer",
            vblank_bodies=vblank_bodies,
        )
        total_span = _main_span(
            start,
            end,
            index=index,
            name="total",
            vblank_bodies=vblank_bodies,
        )
        if terminal:
            terminal_parts = {
                "terminal_step": {
                    **_main_span(
                        start,
                        gameplay_end,
                        index=index,
                        name="terminal_step",
                        vblank_bodies=vblank_bodies,
                    ),
                    "limit_cpu_cycles": MAIN_RELEASE_LIMIT_CPU_CYCLES,
                },
                "pre_reset_dispatch": _main_span(
                    gameplay_end,
                    reset_start,
                    index=index,
                    name="pre_reset_dispatch",
                    vblank_bodies=vblank_bodies,
                ),
                "reset_transition": {
                    **_main_span(
                        reset_start,
                        reset_end,
                        index=index,
                        name="reset_transition",
                        vblank_bodies=vblank_bodies,
                    ),
                    "limit_cpu_cycles": (
                        RESET_TRANSITION_LIMIT_CPU_CYCLES
                    ),
                },
                "post_reset_dispatch": _main_span(
                    reset_end,
                    renderer,
                    index=index,
                    name="post_reset_dispatch",
                    vblank_bodies=vblank_bodies,
                ),
                "post_reset_renderer": {
                    **renderer_span,
                    "limit_cpu_cycles": NTSC_FRAME_CPU_CYCLES,
                },
                "total_wall_and_exclusive": total_span,
            }
        intervals.append(
            {
                "index": index,
                "category": category,
                "simulation": simulation_span,
                "renderer": renderer_span,
                "total": total_span,
                # Backward-compatible aliases name the authoritative
                # simulation span, which is the 65-percent budget scope.
                "start_cycle_master": simulation_span[
                    "start_cycle_master"
                ],
                "end_cycle_master": simulation_span["end_cycle_master"],
                "delta_master": simulation_span["delta_master"],
                "delta_cpu_cycles": simulation_span[
                    "delta_cpu_cycles"
                ],
                "raw_delta_master": simulation_span[
                    "raw_delta_master"
                ],
                "raw_delta_cpu_cycles": simulation_span[
                    "raw_delta_cpu_cycles"
                ],
                "raw_cpu_cycles_rational": simulation_span[
                    "raw_cpu_cycles_rational"
                ],
                "divider_remainder": simulation_span[
                    "divider_remainder"
                ],
                "divider_exact": simulation_span["divider_exact"],
                "frame_delta": total_span["frame_delta"],
                "interrupts_inside": total_span["interrupts_inside"],
                "counter_wrapped": (
                    simulation_span["counter_wrapped"]
                    or renderer_span["counter_wrapped"]
                ),
                "frame_counter": start["frame"],
                "start_tick": start["tick"],
                "end_tick": end["tick"],
                "start_attempt": start["attempt"],
                "end_attempt": end["attempt"],
                "start_checkpoint_count": start["checkpoints"],
                "end_checkpoint_count": end["checkpoints"],
                "start_attempt_length": start["attempt_length"],
                "end_attempt_length": end["attempt_length"],
            }
        )

    if tuple(checkpoint_ticks) != EXPECTED_CHECKPOINT_TICKS:
        raise ValueError(
            f"checkpoint ticks {checkpoint_ticks} != "
            f"{list(EXPECTED_CHECKPOINT_TICKS)}"
        )

    segments = {
        "simulation": _main_segment_summary(
            intervals,
            "simulation",
            MAIN_RELEASE_LIMIT_CPU_CYCLES,
            frozenset({"ordinary", "checkpoint"}),
        ),
        "renderer": _main_segment_summary(
            intervals,
            "renderer",
            NTSC_FRAME_CPU_CYCLES,
            frozenset(),
        ),
        "total": _main_segment_summary(
            intervals,
            "total",
            NTSC_FRAME_CPU_CYCLES,
            frozenset({"ordinary", "checkpoint"}),
        ),
    }
    categories = segments["simulation"]["categories"]
    if terminal_parts is None:
        raise ValueError("terminal transition markers are missing")
    for name in (
        "terminal_step",
        "reset_transition",
        "post_reset_renderer",
    ):
        part = terminal_parts[name]
        part["headroom_cpu_cycles"] = (
            part["limit_cpu_cycles"] - part["delta_cpu_cycles"]
        )
        part["status"] = (
            "pass"
            if part["delta_cpu_cycles"] <= part["limit_cpu_cycles"]
            else "fail"
        )
    gate_statuses = [
        segments[segment]["categories"][category]["status"]
        for segment in ("simulation", "total")
        for category in ("ordinary", "checkpoint")
    ] + [
        terminal_parts[name]["status"]
        for name in (
            "terminal_step",
            "reset_transition",
            "post_reset_renderer",
        )
    ]

    return {
        "status": (
            "pass"
            if all(status == "pass" for status in gate_statuses)
            else "fail"
        ),
        "interval_count": len(intervals),
        "checkpoint_ticks": checkpoint_ticks,
        "categories": categories,
        "segments": segments,
        "terminal_transition": terminal_parts,
        "embedded_vblank": {
            "body_interval_count": len(vblank_bodies),
            "max_body_cpu_cycles": max(
                body["delta_cpu_cycles"] for body in vblank_bodies
            ),
            "records_sha256": _records_digest(interrupt_records),
        },
        "max_cpu_cycles": segments["simulation"]["max_cpu_cycles"],
        "total_max_cpu_cycles": segments["total"]["max_cpu_cycles"],
        "counter_wrap_count": sum(
            int(interval["counter_wrapped"]) for interval in intervals
        ),
        "exclusive_divider_remainder_interval_count": sum(
            int(
                not interval[segment]["divider_exact"]
            )
            for interval in intervals
            for segment in ("simulation", "renderer")
        ),
        "interrupt_inside_interval_count": sum(
            int(interval["total"]["vblank_body_count"] > 0)
            for interval in intervals
        ),
        "interrupts_inside_total": sum(
            interval["total"]["vblank_body_count"]
            for interval in intervals
        ),
        "intervals": intervals,
    }


def analyse_vblank_records(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pairs = _pair_records(records, "vblank")
    if len(pairs) < EXPECTED_AUTHORITATIVE_STEPS:
        raise ValueError(
            f"VBlank interval count {len(pairs)} is below "
            f"{EXPECTED_AUTHORITATIVE_STEPS}"
        )

    intervals: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(pairs):
        if end["frame"] != start["frame"] + 1:
            raise ValueError(
                f"VBlank frame counter did not advance once at pair {index}"
            )
        delta_master, wrapped = _normalized_master_delta(
            start["cycle"], end["cycle"]
        )
        body_cpu = delta_master // MASTER_CLOCKS_PER_M68K_CYCLE
        through_rte = body_cpu + M68K_RTE_CPU_CYCLES
        conservative_total = (
            body_cpu
            + M68K_AUTOVECTOR_ENTRY_CPU_CYCLES
            + M68K_RTE_CPU_CYCLES
        )
        intervals.append(
            {
                "index": index,
                "start_cycle_master": start["cycle"],
                "end_cycle_master": end["cycle"],
                "delta_master_to_rte_opcode": delta_master,
                "body_cpu_cycles_to_rte_opcode": body_cpu,
                "entry_through_rte_cpu_cycles": through_rte,
                "conservative_interrupt_cpu_cycles": conservative_total,
                "counter_wrapped": wrapped,
                "start_frame_counter": start["frame"],
                "end_frame_counter": end["frame"],
                "attempt": end["attempt"],
                "pulse_frame": end["frame"] % 32 == 0,
                "post_reset_commit": end["attempt"] >= EXPECTED_RESET_ATTEMPT,
            }
        )
        if end["attempt"] >= EXPECTED_RESET_ATTEMPT and index != len(pairs) - 1:
            raise ValueError("VBlank capture continued after post-reset commit")

    if intervals[-1]["attempt"] != EXPECTED_RESET_ATTEMPT:
        raise ValueError("VBlank capture did not include reset renderer commit")

    maximum = max(
        intervals,
        key=lambda item: item["conservative_interrupt_cpu_cycles"],
    )
    return {
        "interval_count": len(intervals),
        "denominator": {
            "ntsc_total_lines": NTSC_TOTAL_LINES,
            "ntsc_active_lines": NTSC_ACTIVE_LINES,
            "ntsc_vblank_lines": NTSC_VBLANK_LINES,
            "master_clocks_per_line": GENESIS_MASTER_CLOCKS_PER_LINE,
            "vblank_master_clocks": NTSC_VBLANK_MASTER_CLOCKS,
            "vblank_cpu_cycles_rational": (
                f"{NTSC_VBLANK_MASTER_CLOCKS}/"
                f"{MASTER_CLOCKS_PER_M68K_CYCLE}"
            ),
            "release_percent": VBLANK_RELEASE_PERCENT,
            "release_limit_cpu_cycles": (
                VBLANK_RELEASE_LIMIT_CPU_CYCLES
            ),
        },
        "scope": {
            "measured": (
                "BOOT_STAGE_RUNNING handler entry before its first "
                "instruction through arrival at the one shared rte opcode, "
                "including status acknowledge and boot-stage check"
            ),
            "marker_boot_stage_symbol": "DEBUG_BOOT_STAGE",
            "marker_boot_stage_value": BOOT_STAGE_RUNNING,
            "rte_cpu_cycles_added": M68K_RTE_CPU_CYCLES,
            "autovector_entry_cpu_cycles_added_conservatively": (
                M68K_AUTOVECTOR_ENTRY_CPU_CYCLES
            ),
        },
        "max_body_cpu_cycles_to_rte_opcode": maximum[
            "body_cpu_cycles_to_rte_opcode"
        ],
        "max_entry_through_rte_cpu_cycles": maximum[
            "entry_through_rte_cpu_cycles"
        ],
        "max_conservative_interrupt_cpu_cycles": maximum[
            "conservative_interrupt_cpu_cycles"
        ],
        "max_at_frame": maximum["end_frame_counter"],
        "headroom_cpu_cycles": (
            VBLANK_RELEASE_LIMIT_CPU_CYCLES
            - maximum["conservative_interrupt_cpu_cycles"]
        ),
        "counter_wrap_count": sum(
            int(interval["counter_wrapped"]) for interval in intervals
        ),
        "status": (
            "pass"
            if maximum["conservative_interrupt_cpu_cycles"]
            <= VBLANK_RELEASE_LIMIT_CPU_CYCLES
            else "fail"
        ),
        "intervals": intervals,
    }


def analyse_projection_records(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pairs = _pair_records(records, "projection")
    if len(pairs) != 1:
        raise ValueError(
            f"projection interval count {len(pairs)} != 1"
        )
    start, end = pairs[0]
    if (
        start["attempt"],
        end["attempt"],
        start["tick"],
        end["tick"],
    ) != (0, 0, 60, 60):
        raise ValueError(
            "projection probe did not isolate attempt 0 checkpoint tick 60"
        )
    if end["checkpoints"] != start["checkpoints"] + 1:
        raise ValueError(
            "projection probe did not publish exactly one checkpoint"
        )
    if end["frame"] != start["frame"]:
        raise ValueError("projection probe crossed a VBlank")
    delta_master, wrapped = _normalized_master_delta(
        start["cycle"], end["cycle"]
    )
    delta_cpu = delta_master // MASTER_CLOCKS_PER_M68K_CYCLE
    return {
        "interval_count": 1,
        "scope": (
            "the GameplayStep-selected projection publication entry through "
            "arrival at the regular checkpoint caller instruction after rts"
        ),
        "tick": 60,
        "start_cycle_master": start["cycle"],
        "end_cycle_master": end["cycle"],
        "delta_master": delta_master,
        "delta_cpu_cycles": delta_cpu,
        "counter_wrapped": wrapped,
        "counter_wrap_count": int(wrapped),
        "frame_delta": 0,
        "limit_cpu_cycles": MAIN_RELEASE_LIMIT_CPU_CYCLES,
        "headroom_cpu_cycles": MAIN_RELEASE_LIMIT_CPU_CYCLES - delta_cpu,
        "status": (
            "pass"
            if delta_cpu <= MAIN_RELEASE_LIMIT_CPU_CYCLES
            else "fail"
        ),
    }


def _measurement_signature(session: Mapping[str, Any]) -> dict[str, Any]:
    mode = session["mode"]
    measurement = session["measurement"]
    if mode == "main":
        return {
            "mode": mode,
            "records_sha256": session["records_sha256"],
            "interrupt_records_sha256": session[
                "interrupt_records_sha256"
            ],
            "segments": measurement["segments"],
            "terminal_transition": measurement["terminal_transition"],
            "checkpoint_ticks": measurement["checkpoint_ticks"],
            "max_cpu_cycles": measurement["max_cpu_cycles"],
        }
    return {
        "mode": mode,
        "records_sha256": session["records_sha256"],
        "interval_count": measurement["interval_count"],
        "max_conservative_interrupt_cpu_cycles": measurement[
            "max_conservative_interrupt_cpu_cycles"
        ],
        "max_at_frame": measurement["max_at_frame"],
    }


def _checks(
    sessions: Sequence[Mapping[str, Any]],
    provenance: Mapping[str, Any],
) -> list[dict[str, str]]:
    main = [session for session in sessions if session["mode"] == "main"]
    vblank = [session for session in sessions if session["mode"] == "vblank"]

    def check(name: str, passed: bool, detail: str) -> dict[str, str]:
        return {
            "check": name,
            "status": "pass" if passed else "fail",
            "detail": detail,
        }

    simulation_status = all(
        session["measurement"]["segments"]["simulation"]["categories"][
            category
        ]["status"]
        == "pass"
        for session in main
        for category in ("ordinary", "checkpoint")
    )
    total_status = all(
        session["measurement"]["segments"]["total"]["categories"][category][
            "status"
        ]
        == "pass"
        for session in main
        for category in ("ordinary", "checkpoint")
    )
    terminal_status = all(
        session["measurement"]["terminal_transition"][name]["status"]
        == "pass"
        for session in main
        for name in (
            "terminal_step",
            "reset_transition",
            "post_reset_renderer",
        )
    )
    vblank_status = all(
        session["measurement"]["status"] == "pass" for session in vblank
    )
    main_repro = (
        len(main) == 2
        and _measurement_signature(main[0]) == _measurement_signature(main[1])
    )
    vblank_repro = (
        len(vblank) == 2
        and _measurement_signature(vblank[0])
        == _measurement_signature(vblank[1])
    )
    readonly = all(
        not session["command_policy_errors"]
        and not session["console_policy_errors"]
        for session in sessions
    )
    required_hash_records = (
        "rom",
        "emulator",
        "default_config",
        "symbols",
        "collector",
        "schema",
        "launcher",
        "toolchain_lock",
        "trace",
    )
    session_config_hashes = provenance.get("session_config_sha256", {})
    exact_hashes = all(
        isinstance(provenance.get(key), Mapping)
        and _valid_sha256(provenance[key].get("sha256"))
        for key in required_hash_records
    ) and isinstance(session_config_hashes, Mapping) and all(
        _valid_sha256(value)
        for value in session_config_hashes.values()
    )
    return [
        check(
            "simulation_ordinary_checkpoint_65_percent",
            simulation_status,
            (
                "VBlank-body-exclusive ordinary/checkpoint simulation "
                f"<= {MAIN_RELEASE_LIMIT_CPU_CYCLES} cycles"
            ),
        ),
        check(
            "total_main_ordinary_checkpoint_one_frame",
            total_status,
            (
                "VBlank-body-exclusive ordinary/checkpoint total main "
                f"<= {NTSC_FRAME_CPU_CYCLES} cycles"
            ),
        ),
        check(
            "terminal_step_reset_renderer_transition_budgets",
            terminal_status,
            (
                f"terminal step <= {MAIN_RELEASE_LIMIT_CPU_CYCLES}, reset "
                f"<= {RESET_TRANSITION_LIMIT_CPU_CYCLES}, post-reset "
                f"renderer <= {NTSC_FRAME_CPU_CYCLES} cycles"
            ),
        ),
        check(
            "vblank_conservative_70_percent",
            vblank_status,
            (
                "entry/body plus 44-cycle autovector and 20-cycle rte "
                f"<= {VBLANK_RELEASE_LIMIT_CPU_CYCLES} cycles"
            ),
        ),
        check(
            "two_fresh_main_sessions_reproduce",
            main_repro,
            f"main signatures equal={main_repro}",
        ),
        check(
            "two_fresh_vblank_sessions_reproduce",
            vblank_repro,
            f"VBlank signatures equal={vblank_repro}",
        ),
        check(
            "native_debugger_is_read_only",
            readonly,
            "no guest/register/input mutation command or GDB transport",
        ),
        check(
            "master_clock_spans_monotonic_and_vblank_body_subtracted",
            all(
                session["measurement"]["counter_wrap_count"] == 0
                for session in sessions
            ),
            (
                "no counter wrap/rebase; raw master spans retained and "
                "embedded measured VBlank bodies subtracted before ceil(/7)"
            ),
        ),
        check(
            "hash_bound_provenance",
            exact_hashes,
            "ROM/emulator/config/symbol/tool/schema/trace hashes are present",
        ),
    ]


def _native_debugger_semantics() -> dict[str, Any]:
    return {
        "transport": "Win32 AttachConsole/CONIN$/CONOUT$",
        "gdb_used": False,
        "redirected_stdio_supported": False,
        "redirected_stdio_probe_result": (
            "anonymous pipes, inherited disk handles, and WSL PTY did "
            "not reach the -mwindows CRT streams; pinned terminal_win.c "
            "AllocConsole plus freopen requires console APIs"
        ),
        "cycle_expression": "cycle",
        "short_cycle_expression": "c",
        "short_cycle_expression_supported": False,
        "short_cycle_probe_result": "Failed to eval c",
        "cycle_units": "Genesis master clocks",
        "breakpoint_condition_semantics": (
            "condition INDEX EXPR is evaluated before command lists; "
            "false resumes immediately"
        ),
        "command_list_semantics": (
            "commands INDEX executes in order; continue returns false "
            "from the debugger loop and resumes the emulated CPU"
        ),
        "breakpoint_overhead": (
            "breakpoint dispatch, console I/O, framebuffer refresh, and "
            "host wait consume host time only; the emulated cycle counter "
            "is sampled before the breakpointed instruction"
        ),
        "vblank_marker_boot_stage_symbol": "DEBUG_BOOT_STAGE",
        "vblank_marker_boot_stage_value": BOOT_STAGE_RUNNING,
        "vblank_marker_condition_semantics": (
            "both VBlankInterrupt entry and shared-rte breakpoints require "
            "DEBUG_BOOT_STAGE == BOOT_STAGE_RUNNING; the deliberate "
            "pre-running acknowledge-only interrupt resumes without markers"
        ),
        "vblank_running_scope": (
            "actual VBlankInterrupt entry before its first instruction "
            "through arrival at the one shared rte opcode, including the "
            "status acknowledge and boot-stage check"
        ),
    }


def build_report(
    *,
    rom_sha256: str,
    sessions: Sequence[Mapping[str, Any]],
    pc_contract: Mapping[str, Any],
    provenance: Mapping[str, Any],
    limitations: Sequence[str],
) -> dict[str, Any]:
    checks = _checks(sessions, provenance)
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": (
            "pass"
            if checks and all(item["status"] == "pass" for item in checks)
            else "fail"
        ),
        "rom_sha256": rom_sha256,
        "capture_utc": datetime.now(timezone.utc).isoformat(),
        "budgets": {
            "master_clocks_per_m68k_cycle": (
                MASTER_CLOCKS_PER_M68K_CYCLE
            ),
            "ntsc_frame_cpu_cycles": NTSC_FRAME_CPU_CYCLES,
            "main_release_percent": MAIN_RELEASE_PERCENT,
            "main_release_limit_cpu_cycles": (
                MAIN_RELEASE_LIMIT_CPU_CYCLES
            ),
            "reset_transition_limit_cpu_cycles": (
                RESET_TRANSITION_LIMIT_CPU_CYCLES
            ),
            "vblank_release_percent": VBLANK_RELEASE_PERCENT,
            "vblank_release_limit_cpu_cycles": (
                VBLANK_RELEASE_LIMIT_CPU_CYCLES
            ),
        },
        "native_debugger_semantics": _native_debugger_semantics(),
        "pc_contract": pc_contract,
        "checks": checks,
        "sessions": list(sessions),
        "provenance": dict(provenance),
        "guest_write_commands": [],
        "limitations": list(limitations),
    }


def _records_digest(records: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        list(records), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def report_contract_errors(
    report: Mapping[str, Any], root: Path | None = None
) -> list[str]:
    """Recompute semantic decisions and optionally current-file provenance."""
    errors: list[str] = []
    if report.get("schema") != REPORT_SCHEMA:
        errors.append("schema identifier mismatch")
    if report.get("scope") != "Milestone4":
        errors.append("scope mismatch")
    if report.get("evidence_type") != EVIDENCE_TYPE:
        errors.append("evidence type mismatch")
    if report.get("status") not in {"pass", "fail"}:
        errors.append("invalid report status")
    if not _valid_sha256(report.get("rom_sha256")):
        errors.append("invalid ROM SHA-256")
    if report.get("guest_write_commands") != []:
        errors.append("guest_write_commands must be empty")
    if report.get(
        "native_debugger_semantics"
    ) != _native_debugger_semantics():
        errors.append("native debugger semantics contract mismatch")
    expected_budgets = {
        "master_clocks_per_m68k_cycle": MASTER_CLOCKS_PER_M68K_CYCLE,
        "ntsc_frame_cpu_cycles": NTSC_FRAME_CPU_CYCLES,
        "main_release_percent": MAIN_RELEASE_PERCENT,
        "main_release_limit_cpu_cycles": MAIN_RELEASE_LIMIT_CPU_CYCLES,
        "reset_transition_limit_cpu_cycles": (
            RESET_TRANSITION_LIMIT_CPU_CYCLES
        ),
        "vblank_release_percent": VBLANK_RELEASE_PERCENT,
        "vblank_release_limit_cpu_cycles": (
            VBLANK_RELEASE_LIMIT_CPU_CYCLES
        ),
    }
    if report.get("budgets") != expected_budgets:
        errors.append("budget contract mismatch")

    sessions = report.get("sessions")
    if not isinstance(sessions, list) or len(sessions) != len(SESSION_LAYOUT):
        errors.append("exactly four cycle sessions are required")
        sessions = []
    elif [
        (session.get("session_id"), session.get("mode"))
        for session in sessions
        if isinstance(session, Mapping)
    ] != list(SESSION_LAYOUT):
        errors.append("cycle session identifiers/modes are not exact")

    pc_contract = report.get("pc_contract")
    if not isinstance(pc_contract, Mapping):
        errors.append("pc_contract is missing")
        pc_contract = {}
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        errors.append("provenance is missing")
        provenance = {}

    for session in sessions:
        if not isinstance(session, Mapping):
            errors.append("session is not an object")
            continue
        mode = session.get("mode")
        records = session.get("records")
        interrupt_records = session.get("interrupt_records")
        if mode not in {"main", "vblank"} or not isinstance(records, list):
            errors.append("session mode/records invalid")
            continue
        if not isinstance(interrupt_records, list):
            errors.append("session interrupt_records invalid")
            continue
        if session.get("records_sha256") != _records_digest(records):
            errors.append(
                f"{session.get('session_id')} records hash mismatch"
            )
        if session.get("interrupt_records_sha256") != _records_digest(
            interrupt_records
        ):
            errors.append(
                f"{session.get('session_id')} interrupt records hash mismatch"
            )
        if mode == "vblank" and interrupt_records:
            errors.append(
                f"{session.get('session_id')} has unexpected embedded "
                "interrupt records"
            )
        try:
            recomputed = (
                analyse_main_records(records, interrupt_records)
                if mode == "main"
                else analyse_vblank_records(records)
            )
        except (KeyError, TypeError, ValueError) as error:
            errors.append(
                f"{session.get('session_id')} measurement invalid: {error}"
            )
            continue
        if recomputed != session.get("measurement"):
            errors.append(
                f"{session.get('session_id')} measurement derivation mismatch"
            )
        if session.get("command_policy_errors") != []:
            errors.append(
                f"{session.get('session_id')} command policy failed"
            )
        if session.get("console_policy_errors") != []:
            errors.append(
                f"{session.get('session_id')} console policy failed"
            )
        if not _valid_sha256(session.get("command_script_sha256")):
            errors.append(
                f"{session.get('session_id')} command hash invalid"
            )
        command_script = session.get("command_script")
        if not isinstance(command_script, str) or session.get(
            "command_script_sha256"
        ) != sha256_bytes(command_script.encode("utf-8")):
            errors.append(
                f"{session.get('session_id')} command content hash mismatch"
            )
        if not _valid_sha256(session.get("console_sha256")):
            errors.append(
                f"{session.get('session_id')} console hash invalid"
            )
        expected_session_status = (
            "pass"
            if recomputed.get("status") == "pass"
            and session.get("command_policy_errors") == []
            and session.get("console_policy_errors") == []
            else "fail"
        )
        if session.get("status") != expected_session_status:
            errors.append(
                f"{session.get('session_id')} status derivation mismatch"
            )

    checks = report.get("checks")
    try:
        recomputed_checks = _checks(sessions, provenance) if sessions else []
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"report checks cannot be recomputed: {error}")
        recomputed_checks = []
    if checks != recomputed_checks:
        errors.append("report checks do not match recomputed checks")
    expected_status = (
        "pass"
        if recomputed_checks
        and all(item["status"] == "pass" for item in recomputed_checks)
        else "fail"
    )
    if report.get("status") != expected_status:
        errors.append("top-level status does not match checks")

    if len(sessions) == len(SESSION_LAYOUT):
        for mode in ("main", "vblank"):
            selected = [s for s in sessions if s.get("mode") == mode]
            try:
                reproducible = (
                    len(selected) == 2
                    and _measurement_signature(selected[0])
                    == _measurement_signature(selected[1])
                )
            except (KeyError, TypeError):
                reproducible = False
            if not reproducible:
                errors.append(f"{mode} sessions are not reproducible")

    if root is not None and provenance:
        root = root.resolve()
        for key in (
            "rom",
            "emulator",
            "default_config",
            "symbols",
            "collector",
            "schema",
            "launcher",
            "toolchain_lock",
            "trace",
        ):
            record = provenance.get(key)
            if not isinstance(record, Mapping):
                errors.append(f"missing provenance record {key}")
                continue
            path_value = record.get("path")
            expected_hash = record.get("sha256")
            if not isinstance(path_value, str) or not _valid_sha256(
                expected_hash
            ):
                errors.append(f"invalid provenance record {key}")
                continue
            path = Path(path_value)
            if not path.is_absolute():
                path = root / path
            if not path.is_file():
                errors.append(f"provenance file missing: {key}")
            elif sha256_file(path) != expected_hash:
                errors.append(f"current provenance hash drift: {key}")

        rom_record = provenance.get("rom", {})
        if rom_record.get("sha256") != report.get("rom_sha256"):
            errors.append("ROM provenance does not match report")

        try:
            rom_path = root / provenance["rom"]["path"]
            symbol_path = root / provenance["symbols"]["path"]
            trace_path = root / provenance["trace"]["path"]
            symbol_values = parse_vasm_symbols(symbol_path)
            current_contract = derive_pc_contract(
                rom_path.read_bytes(), symbol_values
            )
            if current_contract != report.get("pc_contract"):
                errors.append("current ROM/symbol PC contract drift")
            for session in sessions:
                expected_script = build_command_script(
                    session["mode"], current_contract, symbol_values
                )
                if session.get("command_script") != expected_script:
                    errors.append(
                        f"{session.get('session_id')} exact command drift"
                    )
                expected_policy = command_script_errors(
                    session.get("command_script", ""),
                    session["mode"],
                    current_contract,
                    symbol_values,
                )
                if expected_policy != session.get("command_policy_errors"):
                    errors.append(
                        f"{session.get('session_id')} command policy "
                        "derivation mismatch"
                    )
            errors.extend(
                _trace_contract_errors(
                    trace_path.read_text("utf-8", errors="strict"),
                    sessions,
                )
            )
        except (KeyError, OSError, UnicodeError, ValueError, EvidenceFailure) as error:
            errors.append(f"PC contract provenance invalid: {error}")

    if report.get("status") == "pass" and errors:
        errors.append("passing report failed semantic validation")
    return errors


def _trace_text(sessions: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"# {REPORT_SCHEMA}",
        "# Transport: BlastEm native debugger via Win32 console APIs; no GDB",
    ]
    for session in sessions:
        lines.extend(
            (
                f"===== BEGIN {session['session_id']} =====",
                f"# mode={session['mode']}",
                f"# command_sha256={session['command_script_sha256']}",
                "----- COMMAND SCRIPT -----",
                session["command_script"].rstrip("\n"),
                "----- CONSOLE TRANSCRIPT -----",
                session["_console_text"].rstrip("\n"),
                f"===== END {session['session_id']} =====",
            )
        )
    return "\n".join(lines) + "\n"


def _trace_contract_errors(
    text: str, sessions: Sequence[Mapping[str, Any]]
) -> list[str]:
    errors: list[str] = []
    header = (
        f"# {REPORT_SCHEMA}\n"
        "# Transport: BlastEm native debugger via Win32 console APIs; no GDB\n"
    )
    if not text.startswith(header):
        return ["cycle trace header mismatch"]
    cursor = len(header)
    for session in sessions:
        session_id = session["session_id"]
        mode = session["mode"]
        begin = f"===== BEGIN {session_id} =====\n"
        end = f"===== END {session_id} =====\n"
        if not text.startswith(begin, cursor):
            errors.append(f"trace section order mismatch at {session_id}")
            return errors
        cursor += len(begin)
        section_end = text.find(end, cursor)
        if section_end < 0:
            errors.append(f"trace end marker missing for {session_id}")
            return errors
        section = text[cursor:section_end]
        metadata = (
            f"# mode={mode}\n"
            f"# command_sha256={session['command_script_sha256']}\n"
            "----- COMMAND SCRIPT -----\n"
        )
        if not section.startswith(metadata):
            errors.append(f"trace metadata mismatch for {session_id}")
            cursor = section_end + len(end)
            continue
        payload = section[len(metadata) :]
        divider = "\n----- CONSOLE TRANSCRIPT -----\n"
        command_text, found, console_text = payload.partition(divider)
        if not found:
            errors.append(f"trace console divider missing for {session_id}")
            cursor = section_end + len(end)
            continue
        command_text += "\n"
        if command_text != session.get("command_script"):
            errors.append(f"trace command text mismatch for {session_id}")
        if sha256_bytes(console_text.encode("utf-8")) != session.get(
            "console_sha256"
        ):
            errors.append(f"trace console hash mismatch for {session_id}")
        elif console_transcript_errors(
            console_text, mode, command_text
        ) != session.get("console_policy_errors"):
            errors.append(
                f"trace console policy derivation mismatch for {session_id}"
            )
        if parse_console_markers(console_text, mode) != session.get(
            "records"
        ):
            errors.append(f"trace records mismatch for {session_id}")
        expected_interrupt_records = (
            parse_console_markers(console_text, "vblank")
            if mode == "main"
            else []
        )
        if expected_interrupt_records != session.get("interrupt_records"):
            errors.append(
                f"trace interrupt records mismatch for {session_id}"
            )
        cursor = section_end + len(end)
    if cursor != len(text):
        errors.append("cycle trace has trailing or unbound content")
    return errors


def _public_session(session: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in session.items()
        if not key.startswith("_")
    }


def _console_helper_payload(
    process_id: int,
    script: str,
    complete_marker: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Attach to one owned console, execute a script, capture, then quit."""
    if os.name != "nt":
        raise EvidenceFailure("Win32 console helper requires native Windows")
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
        _fields_ = (
            ("unicode", ctypes.c_wchar),
            ("ascii", ctypes.c_char),
        )

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
        _fields_ = (
            ("key", KeyEvent),
            ("padding", ctypes.c_byte * 16),
        )

    class InputRecord(ctypes.Structure):
        _fields_ = (
            ("event_type", wintypes.WORD),
            ("event", EventUnion),
        )

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
                f"could not attach to owned BlastEm console PID {process_id}: "
                f"WinError {ctypes.get_last_error()}"
            )
        time.sleep(0.02)

    generic_read_write = 0xC0000000
    share_read_write = 0x00000003
    open_existing = 3
    console_in = kernel32.CreateFileW(
        "CONIN$",
        generic_read_write,
        share_read_write,
        None,
        open_existing,
        0,
        None,
    )
    console_out = kernel32.CreateFileW(
        "CONOUT$",
        generic_read_write,
        share_read_write,
        None,
        open_existing,
        0,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if console_in in {None, invalid} or console_out in {None, invalid}:
        raise EvidenceFailure(
            f"could not open CONIN$/CONOUT$: WinError "
            f"{ctypes.get_last_error()}"
        )

    def read_console() -> tuple[str, dict[str, int]]:
        info = ConsoleInfo()
        if not kernel32.GetConsoleScreenBufferInfo(
            console_out, ctypes.byref(info)
        ):
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
        }

    def send_console(text: str) -> int:
        payload = text.replace("\n", "\r")
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
                "WriteConsoleInputW failed: "
                f"WinError {ctypes.get_last_error()}"
            )
        if written.value != len(records):
            raise EvidenceFailure(
                f"console input truncated: {written.value}/{len(records)}"
            )
        return written.value

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
        raise EvidenceFailure("native debugger initial prompt timed out")

    written = send_console(script)
    transcript = initial
    while time.monotonic() < deadline:
        transcript, console_info = read_console()
        stripped = [line.strip() for line in transcript.splitlines()]
        if complete_marker in stripped and stripped[-1:] == [">"]:
            break
        time.sleep(0.02)
    else:
        tail = "\n".join(transcript.splitlines()[-40:])
        raise EvidenceFailure(
            f"native debugger completion timed out: {complete_marker}; "
            f"console tail follows:\n{tail}"
        )

    quit_written = send_console("quit\n")
    time.sleep(0.1)
    try:
        final_transcript, console_info = read_console()
    except EvidenceFailure:
        final_transcript = transcript + "> quit\n"
    kernel32.FreeConsole()
    return {
        "console_text": final_transcript,
        "script_input_records": written,
        "quit_input_records": quit_written,
        "console": console_info,
    }


def _helper_main(args: argparse.Namespace) -> int:
    script = Path(args.command_file).read_text(
        "utf-8", errors="strict"
    )
    result = _console_helper_payload(
        args.process_id,
        script,
        args.complete_marker,
        args.timeout_seconds,
    )
    sys.stdout.write(json.dumps(result, sort_keys=True))
    sys.stdout.flush()
    return 0


def _run_live_session(
    *,
    root: Path,
    session_id: str,
    mode: str,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: Mapping[str, int],
    pc_contract: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    process: subprocess.Popen[Any] | None = None
    helper: subprocess.Popen[str] | None = None
    runtime_copy: dict[str, Any] | None = None
    started = datetime.now(timezone.utc).isoformat()
    script = build_command_script(mode, pc_contract, symbols)
    command_errors = command_script_errors(
        script, mode, pc_contract, symbols
    )
    if command_errors:
        raise EvidenceFailure("; ".join(command_errors))

    with tempfile.TemporaryDirectory(
        prefix=f"recursion94-m4-cycle-{session_id}-"
    ) as temporary_text:
        temporary = Path(temporary_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, temporary
        )
        controller = write_standard_pad_profile(
            default_config, temporary
        )
        controller["session_isolation"] = apply_blastem_session_isolation(
            Path(controller["path"]), temporary / "session-data"
        )
        isolated_config = Path(controller["path"])
        config_sha = sha256_file(isolated_config)

        command_file = temporary / "native-debugger-commands.txt"
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
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
        process = subprocess.Popen(
            launch_command,
            cwd=isolated_runtime,
            creationflags=creationflags,
        )
        complete_markers = {
            "main": "R94C M COMPLETE",
            "vblank": "R94C V COMPLETE",
            "projection": "R94C P COMPLETE",
        }
        complete_marker = complete_markers[mode]
        helper_command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--console-helper",
            "--process-id",
            str(process.pid),
            "--command-file",
            str(command_file),
            "--complete-marker",
            complete_marker,
            "--timeout-seconds",
            str(timeout_seconds),
        ]
        try:
            helper = subprocess.Popen(
                helper_command,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = helper.communicate(
                timeout=timeout_seconds + 10
            )
            if helper.returncode != 0:
                raise EvidenceFailure(
                    f"{session_id} console helper failed "
                    f"({helper.returncode}): {stderr.strip()}"
                )
            try:
                helper_result = json.loads(stdout)
            except json.JSONDecodeError as error:
                raise EvidenceFailure(
                    f"{session_id} helper returned invalid JSON"
                ) from error
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise EvidenceFailure(
                    f"{session_id} BlastEm did not exit after quit"
                ) from error
            if process.returncode != 0:
                raise EvidenceFailure(
                    f"{session_id} BlastEm exit code {process.returncode}"
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

        console_text = helper_result["console_text"]
        console_errors = console_transcript_errors(
            console_text, mode, script
        )
        records = parse_console_markers(console_text, mode)
        if mode == "main":
            interrupt_records = parse_console_markers(
                console_text, "vblank"
            )
            measurement = analyse_main_records(
                records, interrupt_records
            )
        elif mode == "vblank":
            interrupt_records = []
            measurement = analyse_vblank_records(records)
        elif mode == "projection":
            interrupt_records = []
            measurement = analyse_projection_records(records)
        else:
            raise ValueError(f"unknown cycle session mode {mode!r}")
        return {
            "session_id": session_id,
            "mode": mode,
            "status": (
                "pass"
                if not command_errors
                and not console_errors
                and measurement["status"] == "pass"
                else "fail"
            ),
            "started_utc": started,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "process_id": process.pid,
            "launch_flags": launch_command[1:-1],
            "debugger_transport": "native_win32_console",
            "gdb_port_opened": False,
            "command_script": script,
            "command_script_sha256": sha256_bytes(
                script.encode("utf-8")
            ),
            "command_policy_errors": command_errors,
            "console_sha256": sha256_bytes(
                console_text.encode("utf-8")
            ),
            "console_policy_errors": console_errors,
            "console_geometry": helper_result["console"],
            "script_input_records": helper_result[
                "script_input_records"
            ],
            "quit_input_records": helper_result["quit_input_records"],
            "records": records,
            "records_sha256": _records_digest(records),
            "interrupt_records": interrupt_records,
            "interrupt_records_sha256": _records_digest(
                interrupt_records
            ),
            "measurement": measurement,
            "isolated_runtime": runtime_copy,
            "isolated_config": {
                "sha256": config_sha,
                "controller": "gamepad3.1",
                "config_in_exe_dir": True,
                "temporary_copy_removed_after_session": True,
            },
            "_console_text": console_text,
        }


def collect_live(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise EvidenceFailure(
            "live native-cycle evidence requires native Windows Python"
        )
    root = Path(args.root).resolve()
    rom = Path(args.rom).resolve()
    report_path = Path(args.report).resolve()
    trace_path = Path(args.trace).resolve()
    emulator_directory = root / ".toolcache" / "blastem"
    emulator = emulator_directory / "blastem.exe"
    default_config = emulator_directory / "default.cfg"
    symbols_path = root / "build" / "recursion94-debug.sym"
    collector = root / COLLECTOR_RELATIVE_PATH
    schema = root / SCHEMA_RELATIVE_PATH
    launcher = root / LAUNCHER_RELATIVE_PATH
    toolchain_lock = root / "tools/toolchain.lock.json"

    for path, description in (
        (rom, "Debug ROM"),
        (emulator, "pinned BlastEm"),
        (default_config, "BlastEm default config"),
        (symbols_path, "Debug symbols"),
        (collector, "cycle collector"),
        (schema, "cycle schema"),
        (launcher, "cycle launcher"),
        (toolchain_lock, "toolchain lock"),
    ):
        if not path.is_file():
            raise EvidenceFailure(f"{description} is missing: {path}")

    rom_sha = sha256_file(rom)
    if rom_sha != args.expected_sha256.lower():
        raise EvidenceFailure(
            f"Debug ROM SHA-256 {rom_sha} != expected "
            f"{args.expected_sha256.lower()}"
        )
    if sha256_file(emulator) != BLASTEM_EXPECTED_SHA256:
        raise EvidenceFailure("pinned BlastEm executable hash drift")
    verify_no_user_blastem_override()

    capture_paths = {
        "rom": rom,
        "emulator": emulator,
        "default_config": default_config,
        "symbols": symbols_path,
        "collector": collector,
        "schema": schema,
        "launcher": launcher,
        "toolchain_lock": toolchain_lock,
    }
    capture_hashes = {
        name: sha256_file(path) for name, path in capture_paths.items()
    }

    def require_capture_inputs_unchanged(stage: str) -> None:
        drift = [
            name
            for name, path in capture_paths.items()
            if not path.is_file()
            or sha256_file(path) != capture_hashes[name]
        ]
        if drift:
            raise EvidenceFailure(
                f"capture input hash drift {stage}: {', '.join(drift)}"
            )

    symbols = parse_vasm_symbols(symbols_path)
    pc_contract = derive_pc_contract(rom.read_bytes(), symbols)
    sessions: list[dict[str, Any]] = []
    for session_id, mode in SESSION_LAYOUT:
        require_capture_inputs_unchanged(f"before {session_id}")
        sessions.append(
            _run_live_session(
                root=root,
                session_id=session_id,
                mode=mode,
                rom=rom,
                emulator_directory=emulator_directory,
                default_config=default_config,
                symbols=symbols,
                pc_contract=pc_contract,
                timeout_seconds=args.timeout_seconds,
            )
        )
        require_capture_inputs_unchanged(f"after {session_id}")

    require_capture_inputs_unchanged("before report assembly")
    trace_text = _trace_text(sessions)
    write_text(trace_path, trace_text)
    provenance = {
        "rom": _file_record(rom, root),
        "emulator": {
            **_file_record(emulator, root),
            "version": BLASTEM_VERSION,
            "revision": BLASTEM_REVISION,
        },
        "default_config": _file_record(default_config, root),
        "symbols": _file_record(symbols_path, root),
        "collector": _file_record(collector, root),
        "schema": _file_record(schema, root),
        "launcher": _file_record(launcher, root),
        "toolchain_lock": _file_record(toolchain_lock, root),
        "trace": _file_record(trace_path, root),
        "platform": {
            "os_name": os.name,
            "platform": platform.platform(),
            "python": sys.version,
        },
        "session_config_sha256": {
            session["session_id"]: session["isolated_config"]["sha256"]
            for session in sessions
        },
    }
    limitations = (
        "Pinned BlastEm is emulator evidence, not physical-hardware timing.",
        "The release gate covers neutral-input B01 on the Debug ROM; PAL "
        "double-step frames, other encounters, bosses, and Release-ROM "
        "equivalence require later campaign-wide measurement.",
        "The native debugger's cycle value is a 32-bit master-clock counter. "
        "This attempt finishes below BlastEm's cycle-rebase threshold; any "
        "backward movement fails. Raw and VBlank-body-exclusive master-clock "
        "spans, ceil(/7) CPU cycles, and phase remainders are retained.",
        "Each main session also measures every contained VBlank handler body. "
        "Those exact entry-to-rte-opcode master spans are subtracted from "
        "wall spans; the unmeasured fixed interrupt entry and rte costs remain "
        "in main results, making the exclusive-work budget conservative.",
        "The natural GameplayCommitAndReset transition has an explicit "
        f"{RESET_TRANSITION_LIMIT_CPU_CYCLES}-cycle (two NTSC frame) ceiling; "
        "it is not folded into the 65-percent authoritative-step budget.",
        "VBlank comparison uses the explicit NTSC 224-of-262 active-line "
        "contract and conservatively adds original-68000 44-cycle autovector "
        "entry plus 20-cycle rte costs to the measured handler body.",
        "VBlank entry and shared-rte markers require DEBUG_BOOT_STAGE == "
        "BOOT_STAGE_RUNNING. The deliberate stage-3 acknowledge-only "
        "startup interrupt is excluded; every measured full-path span still "
        "starts at the actual vector entry and includes the status "
        "acknowledge and boot-stage check.",
    )
    public_sessions = [_public_session(session) for session in sessions]
    report = build_report(
        rom_sha256=rom_sha,
        sessions=public_sessions,
        pc_contract=pc_contract,
        provenance=provenance,
        limitations=limitations,
    )
    errors = report_contract_errors(report, root)
    if errors:
        report["status"] = "fail"
        report["contract_errors"] = errors
    write_json(report_path, report)
    if errors:
        raise EvidenceFailure(
            "cycle evidence contract failed: " + "; ".join(errors)
        )
    if report["status"] != "pass":
        raise EvidenceFailure(
            "measured main/VBlank CPU budget exceeded; see report"
        )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--rom")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--report")
    parser.add_argument("--trace")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--validate-report")
    parser.add_argument(
        "--console-helper", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--process-id", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--command-file", help=argparse.SUPPRESS)
    parser.add_argument("--complete-marker", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.console_helper:
        required = (
            args.process_id,
            args.command_file,
            args.complete_marker,
        )
        if any(value in {None, ""} for value in required):
            parser.error("console-helper arguments are incomplete")
        return _helper_main(args)

    root = Path(args.root).resolve()
    if args.validate_report:
        report = json.loads(
            Path(args.validate_report).read_text("utf-8", errors="strict")
        )
        errors = report_contract_errors(report, root)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        return 0

    if not args.rom:
        args.rom = str(root / "build/recursion94-debug.bin")
    if not args.expected_sha256:
        parser.error("--expected-sha256 is required for live capture")
    if not args.report:
        args.report = str(root / DEFAULT_REPORT_RELATIVE_PATH)
    if not args.trace:
        args.trace = str(root / DEFAULT_TRACE_RELATIVE_PATH)

    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    try:
        with exclusive_file_lock(lock_path, args.lock_timeout_seconds):
            collect_live(args)
    except (EvidenceFailure, OSError, ValueError, TimeoutError) as error:
        print(f"M4 cycle evidence failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
