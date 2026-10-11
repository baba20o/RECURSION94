#!/usr/bin/env python3
"""Collect uninterrupted M4 renderer/VBlank budget evidence.

Each of two fresh, owned BlastEm sessions installs one main-side breakpoint at
``GameplayCommitAndReset`` and resumes exactly once.  With no host input
injection, the first attempt reaches that routine naturally after the
authoritative tick-720 timeout.  The measured interval therefore has no
post-step, renderer, DMA, VBlank, or periodic debugger stops.

At the breakpoint, the 68000 is outside interrupt context with VBlank masked.
The preceding, exactly 720th VBlank has committed the renderer queue for tick 719 and
``GameplayRendererBuildAndQueue`` has not yet run for the timeout/reset frame.
The collector then performs memory reads only.  GDB packets capable of writing
RAM, registers, or PC (G/M/P/X) are forbidden and fail the report.

This is a counter/capacity and cross-clock gate, not a cycle profiler.  It
proves that the bound Debug ROM completes the measured ordinary B01 interval
without the published renderer/DMA/VBlank faults or excessive divergence
between the Z80 hardware V-int clock and the delivered 68000 VBlank count.  It
does not establish exact VBlank headroom, PAL double-step cost,
reset/completion-frame cost, visual quality, other encounters,
physical-hardware equivalence, or Release-ROM equivalence.
"""

from __future__ import annotations

import argparse
import copy
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

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    GdbRemote,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    parse_vasm_symbols,
    require_gdb_port_available,
    require_stop,
    sha256_bytes,
    sha256_file,
    shutdown_emulator,
    verify_no_user_blastem_override,
    write_json,
    write_standard_pad_profile,
)
from tools.m3_runtime_evidence import (  # noqa: E402
    ENGINE_ABI_VERSION,
    ENGINE_ATTEMPT_TICKS,
    ENGINE_CONTROL_BASE,
    ENGINE_CONTROL_USED_BYTES,
    ENGINE_MAGIC,
    ENGINE_STATE_TERMINAL,
    ENGINE_TERMINAL_TIMEOUT,
    debugger_write_packets,
    parse_engine_control,
)


REPORT_SCHEMA = "r94-m4-uninterrupted-budget-evidence-v1"
EVIDENCE_TYPE = "blastem_gdb_m4_uninterrupted_renderer_vblank_budget"
SCHEMA_RELATIVE_PATH = (
    "tools/report_schemas/m4-budget-evidence.schema.json"
)
COLLECTOR_RELATIVE_PATH = "tools/m4_budget_evidence.py"
TRACE_RELATIVE_PATH = (
    "artifacts/traces/m4-renderer-budget-gdb-transcript.log"
)
ROM_RELATIVE_PATH = "build/recursion94-debug.bin"
EMULATOR_RELATIVE_PATH = ".toolcache/blastem/blastem.exe"
DEFAULT_CONFIG_RELATIVE_PATH = ".toolcache/blastem/default.cfg"
SYMBOL_MAP_RELATIVE_PATH = "build/recursion94-debug.sym"

SESSION_COUNT = 2
LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")
TERMINAL_BREAKPOINT_SYMBOL = "GameplayCommitAndReset"
FORBIDDEN_BREAKPOINT_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopPostGameplayStep",
    "DmaCommitPreparedQueue",
)

DEBUG_BASE = 0x00FFF000
DEBUG_BUDGET_BYTES = 0x48
DEBUG_MAGIC = 0x52393444
DEBUG_ABI_VERSION = 1
DEBUG_ABI_SIZE = 0x0400
BOOT_STAGE_RUNNING = 4
AUDIO_STATE_PLAYING = 2
AUDIO_COMMAND_COUNT_EXPECTED = 1
M2_HEALTHY_FLAGS = 0x003F
Z80_VINT_STARTUP_ALLOWANCE = 32
Z80_HEARTBEAT_MODULUS = 0x10000
AUDIO_HEALTH_CHECK = "audio_runtime_ready_and_playing"
CROSS_CLOCK_CHECK = "z80_m68k_cross_clock_within_startup_allowance"

AUDIO_SHADOW_BASE = 0x00FF9900
AUDIO_SHADOW_BYTES = 0x10
AUDIO_STATUS_SIGNATURE = 0x94
AUDIO_STATUS_ABI_VERSION = 0x01
AUDIO_STATUS_FLAGS_HEALTHY = 0x07
AUDIO_STATUS_FLAG_PAUSED = 0x08
AUDIO_STATUS_FLAG_QUEUE_FAULT = 0x10
AUDIO_STATUS_FLAG_YM_TIMEOUT = 0x20
AUDIO_STATUS_FAULT_MASK = (
    AUDIO_STATUS_FLAG_PAUSED
    | AUDIO_STATUS_FLAG_QUEUE_FAULT
    | AUDIO_STATUS_FLAG_YM_TIMEOUT
)
AUDIO_QUEUE_CAPACITY = 16
AUDIO_QUEUE_READ_EXPECTED = 1
AUDIO_ACTIVE_LAYERS_EXPECTED = 0x01
AUDIO_LAST_COMMAND_EXPECTED = 0x03

RENDERER_STATE_BASE = 0x00FF8300
RENDERER_STATE_USED_BYTES = 0x24
RENDERER_MAGIC = 0x52393456
RENDERER_VERSION = 1
RENDERER_BYTES = 0x0500
RENDERER_FLAG_INITIALIZED = 0x0001
RENDERER_FAULT_FLAGS = 0x009C
RENDERER_RESULT_OK = 0

DMA_QUEUE_BASE = 0x00FF9000
DMA_QUEUE_CONTROL_BYTES = 0x10
DMA_QUEUE_CAPACITY = 32
DMA_ORDINARY_WORD_LIMIT = 2048

RENDERER_SPRITE_LIMIT = 64
RENDERER_SCANLINE_SPRITE_LIMIT = 20
RENDERER_SCANLINE_PIXEL_LIMIT = 320

REQUIRED_CHECK_NAMES = (
    "two_fresh_owned_sessions",
    "uninterrupted_main_side_measurement",
    "read_only_gdb",
    "natural_tick_720_timeout_boundary",
    "stopped_reads_are_consistent",
    "runtime_debug_health",
    AUDIO_HEALTH_CHECK,
    CROSS_CLOCK_CHECK,
    "renderer_abi_and_terminal_alignment",
    "all_cumulative_fault_counters_zero",
    "terminal_queue_fully_committed",
    "dma_capacities_in_bounds",
    "renderer_capacities_in_bounds",
    "fresh_session_budget_reproducibility",
    "exact_hash_bindings_and_no_drift",
)

EXPECTED_STATE_SYMBOLS = {
    "ENGINE_CONTROL_BASE": ENGINE_CONTROL_BASE,
    "ENGINE_MAGIC": ENGINE_CONTROL_BASE + 0x00,
    "ENGINE_STATE": ENGINE_CONTROL_BASE + 0x08,
    "ENGINE_ATTEMPT": ENGINE_CONTROL_BASE + 0x0E,
    "ENGINE_TICK": ENGINE_CONTROL_BASE + 0x10,
    "ENGINE_TERMINAL_REASON": ENGINE_CONTROL_BASE + 0x12,
    "ENGINE_LAST_ERROR": ENGINE_CONTROL_BASE + 0x14,
    "ENGINE_OVERFLOW_FLAGS": ENGINE_CONTROL_BASE + 0x16,
    "DEBUG_BASE": DEBUG_BASE,
    "DEBUG_MAGIC": DEBUG_BASE + 0x00,
    "DEBUG_ABI_VERSION": DEBUG_BASE + 0x04,
    "DEBUG_ABI_SIZE": DEBUG_BASE + 0x06,
    "DEBUG_SEQUENCE": DEBUG_BASE + 0x08,
    "DEBUG_HEARTBEAT": DEBUG_BASE + 0x0C,
    "DEBUG_FRAME_COUNTER": DEBUG_BASE + 0x10,
    "DEBUG_FRAME_TOKEN": DEBUG_BASE + 0x14,
    "DEBUG_FATAL_CODE": DEBUG_BASE + 0x16,
    "DEBUG_BOOT_STAGE": DEBUG_BASE + 0x18,
    "DEBUG_HARDWARE_FLAGS": DEBUG_BASE + 0x1A,
    "DEBUG_SPRITE_COUNT": DEBUG_BASE + 0x24,
    "DEBUG_SPRITE_SCANLINE_MAX": DEBUG_BASE + 0x26,
    "DEBUG_DMA_WORDS_FRAME": DEBUG_BASE + 0x28,
    "DEBUG_DMA_WORDS_MAX": DEBUG_BASE + 0x2A,
    "DEBUG_DMA_QUEUE_DEPTH": DEBUG_BASE + 0x2C,
    "DEBUG_DMA_QUEUE_HIGH_WATER": DEBUG_BASE + 0x2E,
    "DEBUG_DMA_OVERFLOW_COUNT": DEBUG_BASE + 0x30,
    "DEBUG_VBLANK_OVERRUN_COUNT": DEBUG_BASE + 0x32,
    "DEBUG_AUDIO_STATE": DEBUG_BASE + 0x34,
    "DEBUG_Z80_HEARTBEAT": DEBUG_BASE + 0x36,
    "DEBUG_AUDIO_COMMAND_COUNT": DEBUG_BASE + 0x38,
    "DEBUG_M2_FLAGS": DEBUG_BASE + 0x3A,
    "DEBUG_Z80_TIMEOUT_COUNT": DEBUG_BASE + 0x40,
    "DEBUG_DMA_DEFERRED_COUNT": DEBUG_BASE + 0x42,
    "DEBUG_DMA_REQUIRED_DROP_COUNT": DEBUG_BASE + 0x44,
    "DEBUG_DMA_INVALID_COUNT": DEBUG_BASE + 0x46,
    "RENDERER_STATE_BASE": RENDERER_STATE_BASE,
    "RENDERER_MAGIC": RENDERER_STATE_BASE + 0x00,
    "RENDERER_VERSION": RENDERER_STATE_BASE + 0x04,
    "RENDERER_BYTES": RENDERER_STATE_BASE + 0x06,
    "RENDERER_FLAGS": RENDERER_STATE_BASE + 0x08,
    "RENDERER_LAST_ATTEMPT": RENDERER_STATE_BASE + 0x0A,
    "RENDERER_SPRITE_COUNT": RENDERER_STATE_BASE + 0x14,
    "RENDERER_SCANLINE_MAX": RENDERER_STATE_BASE + 0x16,
    "RENDERER_SCANLINE_PIXEL_MAX": RENDERER_STATE_BASE + 0x18,
    "RENDERER_DROPPED_SPRITES": RENDERER_STATE_BASE + 0x1A,
    "RENDERER_DMA_REJECTS": RENDERER_STATE_BASE + 0x1C,
    "RENDERER_BACKLOG_FRAMES": RENDERER_STATE_BASE + 0x1E,
    "RENDERER_LAST_TICK": RENDERER_STATE_BASE + 0x20,
    "RENDERER_LAST_RESULT": RENDERER_STATE_BASE + 0x22,
    "RENDERER_STATE_USED_END": (
        RENDERER_STATE_BASE + RENDERER_STATE_USED_BYTES
    ),
    "DMA_QUEUE_BASE": DMA_QUEUE_BASE,
    "DMA_QUEUE_PUBLISHED_DEPTH": DMA_QUEUE_BASE + 0x00,
    "DMA_QUEUE_BUILD_DEPTH": DMA_QUEUE_BASE + 0x02,
    "DMA_QUEUE_BUILD_WORDS": DMA_QUEUE_BASE + 0x04,
    "DMA_QUEUE_COMMIT_WORDS": DMA_QUEUE_BASE + 0x08,
    "DMA_QUEUE_FRAME_PENDING": DMA_QUEUE_BASE + 0x0A,
    "DMA_QUEUE_RESERVED_0": DMA_QUEUE_BASE + 0x0C,
    "DMA_QUEUE_RESERVED_1": DMA_QUEUE_BASE + 0x0E,
    "AUDIO_SHADOW_BASE": AUDIO_SHADOW_BASE,
}
REQUIRED_SYMBOLS = (
    TERMINAL_BREAKPOINT_SYMBOL,
    *FORBIDDEN_BREAKPOINT_SYMBOLS,
    *EXPECTED_STATE_SYMBOLS,
)

LIMITATIONS = [
    (
        "The gate covers two neutral-input, NTSC Debug-ROM B01 attempts from "
        "startup through the natural tick-720 timeout breakpoint. It observes "
        "ordinary renderer commits through renderer tick 719."
    ),
    (
        "The gate reads cumulative renderer/DMA/VBlank counters, bounded "
        "capacity telemetry, the complete 68000 audio-status shadow, and the "
        "Z80 hardware V-int clock. It is not a "
        "CPU-cycle profiler and does not quantify remaining VBlank or "
        "simulation headroom."
    ),
    (
        "The cross-clock guard permits at most 32 more Z80 hardware V-ints "
        "than delivered 68000 VBlank handlers at the terminal stop. This "
        "conservative startup allowance covers the observed 25-V-int "
        "pre-display/startup phase with seven V-ints of margin; it does not "
        "waive drift accumulated during gameplay."
    ),
    (
        "The gate does not cover PAL double-step frames, reset/completion DMA, "
        "Program Stable, other encounters, visual correctness, physical "
        "hardware, or Release-ROM equivalence."
    ),
    (
        "A pass cannot waive a nonzero counter from any other genuinely "
        "uninterrupted run of the same hashes; such a result is a real fault "
        "until explained and fixed."
    ),
]


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def _stable_detail(value: Any) -> str:
    """Render nested evidence independently of insertion/key-sort order."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def symbol_layout_errors(symbols: dict[str, int]) -> list[str]:
    """Return every missing or moved state symbol used by fixed parsers."""
    errors: list[str] = []
    for name, expected in EXPECTED_STATE_SYMBOLS.items():
        observed = symbols.get(name)
        if observed is None:
            errors.append(f"{name} missing")
        elif observed != expected:
            errors.append(
                f"{name} is ${observed:08X}; ${expected:08X} required"
            )
    return errors


def parse_debug_budget(raw: bytes) -> dict[str, Any]:
    """Parse the exact debug prefix containing all budget counters."""
    if len(raw) != DEBUG_BUDGET_BYTES:
        raise ValueError(
            f"debug budget block is {len(raw)} bytes; "
            f"{DEBUG_BUDGET_BYTES} required"
        )
    return {
        "magic": _u32(raw, 0x00),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "abi_version": _u16(raw, 0x04),
        "abi_size": _u16(raw, 0x06),
        "sequence": _u32(raw, 0x08),
        "heartbeat": _u32(raw, 0x0C),
        "frame_counter": _u32(raw, 0x10),
        "frame_token": _u16(raw, 0x14),
        "fatal_code": _u16(raw, 0x16),
        "boot_stage": _u16(raw, 0x18),
        "hardware_flags": _u16(raw, 0x1A),
        "last_vdp_status": _u16(raw, 0x1C),
        "sprite_count": _u16(raw, 0x24),
        "sprite_scanline_max": _u16(raw, 0x26),
        "dma_words_frame": _u16(raw, 0x28),
        "dma_words_max": _u16(raw, 0x2A),
        "dma_queue_depth": _u16(raw, 0x2C),
        "dma_queue_high_water": _u16(raw, 0x2E),
        "dma_overflow_count": _u16(raw, 0x30),
        "vblank_overrun_count": _u16(raw, 0x32),
        "audio_state": _u16(raw, 0x34),
        "z80_heartbeat": _u16(raw, 0x36),
        "audio_command_count": _u16(raw, 0x38),
        "m2_flags": _u16(raw, 0x3A),
        "z80_timeout_count": _u16(raw, 0x40),
        "dma_deferred_count": _u16(raw, 0x42),
        "dma_required_drop_count": _u16(raw, 0x44),
        "dma_invalid_count": _u16(raw, 0x46),
    }


def parse_renderer_state(raw: bytes) -> dict[str, Any]:
    """Parse the renderer's stable, public state prefix."""
    if len(raw) != RENDERER_STATE_USED_BYTES:
        raise ValueError(
            f"renderer state is {len(raw)} bytes; "
            f"{RENDERER_STATE_USED_BYTES} required"
        )
    return {
        "magic": _u32(raw, 0x00),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 0x04),
        "bytes": _u16(raw, 0x06),
        "flags": _u16(raw, 0x08),
        "last_attempt": _u16(raw, 0x0A),
        "reset_phase": _u16(raw, 0x0C),
        "reset_previous_row": _u16(raw, 0x0E),
        "overlay_clear_destination": _u16(raw, 0x10),
        "overlay_trace_destination": _u16(raw, 0x12),
        "sprite_count": _u16(raw, 0x14),
        "scanline_max": _u16(raw, 0x16),
        "scanline_pixel_max": _u16(raw, 0x18),
        "dropped_sprites": _u16(raw, 0x1A),
        "dma_rejects": _u16(raw, 0x1C),
        "backlog_frames": _u16(raw, 0x1E),
        "last_tick": _u16(raw, 0x20),
        "last_result": _u16(raw, 0x22),
    }


def parse_dma_queue_control(raw: bytes) -> dict[str, Any]:
    """Parse the bounded DMA queue control header."""
    if len(raw) != DMA_QUEUE_CONTROL_BYTES:
        raise ValueError(
            f"DMA queue control is {len(raw)} bytes; "
            f"{DMA_QUEUE_CONTROL_BYTES} required"
        )
    return {
        "published_depth": _u16(raw, 0x00),
        "build_depth": _u16(raw, 0x02),
        "build_words": _u32(raw, 0x04),
        "commit_words": _u16(raw, 0x08),
        "frame_pending": _u16(raw, 0x0A),
        "reserved_0": _u16(raw, 0x0C),
        "reserved_1": _u16(raw, 0x0E),
    }


def parse_audio_shadow(raw: bytes) -> dict[str, Any]:
    """Parse the stable 68000 mirror of the 16-byte Z80 status block."""
    if len(raw) != AUDIO_SHADOW_BYTES:
        raise ValueError(
            f"audio shadow is {len(raw)} bytes; "
            f"{AUDIO_SHADOW_BYTES} required"
        )
    return {
        "signature": raw[0x00],
        "abi_version": raw[0x01],
        "state": raw[0x02],
        "flags": raw[0x03],
        "heartbeat": int.from_bytes(raw[0x04:0x06], "little"),
        "queue_read_sequence": raw[0x06],
        "queue_high_water": raw[0x07],
        "processed_commands": int.from_bytes(
            raw[0x08:0x0A], "little"
        ),
        "late_tick_count": raw[0x0A],
        "ym_write_count": raw[0x0B],
        "psg_write_count": raw[0x0C],
        "motif_step": raw[0x0D],
        "active_layers": raw[0x0E],
        "last_error_or_command": raw[0x0F],
    }


BLOCK_SPECS = (
    (
        "engine_control",
        "engine",
        ENGINE_CONTROL_BASE,
        ENGINE_CONTROL_USED_BYTES,
        parse_engine_control,
    ),
    (
        "debug_budget",
        "debug",
        DEBUG_BASE,
        DEBUG_BUDGET_BYTES,
        parse_debug_budget,
    ),
    (
        "renderer_state",
        "renderer",
        RENDERER_STATE_BASE,
        RENDERER_STATE_USED_BYTES,
        parse_renderer_state,
    ),
    (
        "dma_queue_control",
        "dma_queue",
        DMA_QUEUE_BASE,
        DMA_QUEUE_CONTROL_BYTES,
        parse_dma_queue_control,
    ),
    (
        "audio_shadow",
        "audio_shadow",
        AUDIO_SHADOW_BASE,
        AUDIO_SHADOW_BYTES,
        parse_audio_shadow,
    ),
)


def expected_session_commands(terminal_address: int) -> list[str]:
    commands = [f"Z0,{terminal_address:x},2", "c"]
    for _round in range(2):
        commands.extend(
            f"m{address:x},{size:x}"
            for _name, _key, address, size, _parser in BLOCK_SPECS
        )
    commands.append("k")
    return commands


def _block_evidence(address: int, raw: bytes) -> dict[str, Any]:
    return {
        "address": f"{address:08X}",
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "raw_hex": raw.hex().upper(),
    }


def _cross_clock_observation(debug: dict[str, Any]) -> dict[str, int]:
    frame_counter = debug["frame_counter"]
    z80_heartbeat = debug["z80_heartbeat"]
    return {
        "m68k_frame_counter": frame_counter,
        "z80_vint_heartbeat": z80_heartbeat,
        "z80_vint_excess": z80_heartbeat - frame_counter,
        "startup_allowance_vints": Z80_VINT_STARTUP_ALLOWANCE,
    }


def _read_snapshot(remote: GdbRemote) -> dict[str, Any]:
    engine_raw = remote.read_memory(
        ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
    )
    debug_raw = remote.read_memory(DEBUG_BASE, DEBUG_BUDGET_BYTES)
    renderer_raw = remote.read_memory(
        RENDERER_STATE_BASE, RENDERER_STATE_USED_BYTES
    )
    dma_raw = remote.read_memory(
        DMA_QUEUE_BASE, DMA_QUEUE_CONTROL_BYTES
    )
    audio_raw = remote.read_memory(
        AUDIO_SHADOW_BASE, AUDIO_SHADOW_BYTES
    )
    debug = parse_debug_budget(debug_raw)
    return {
        "engine": parse_engine_control(engine_raw),
        "debug": debug,
        "cross_clock": _cross_clock_observation(debug),
        "renderer": parse_renderer_state(renderer_raw),
        "dma_queue": parse_dma_queue_control(dma_raw),
        "audio_shadow": parse_audio_shadow(audio_raw),
        "blocks": {
            "engine_control": _block_evidence(
                ENGINE_CONTROL_BASE, engine_raw
            ),
            "debug_budget": _block_evidence(DEBUG_BASE, debug_raw),
            "renderer_state": _block_evidence(
                RENDERER_STATE_BASE, renderer_raw
            ),
            "dma_queue_control": _block_evidence(
                DMA_QUEUE_BASE, dma_raw
            ),
            "audio_shadow": _block_evidence(
                AUDIO_SHADOW_BASE, audio_raw
            ),
        },
    }


def capture_stopped_snapshot(remote: GdbRemote) -> dict[str, Any]:
    """Read twice while stopped and retain an explicit consistency result."""
    first = _read_snapshot(remote)
    second = _read_snapshot(remote)
    first_hashes = {
        name: block["sha256"]
        for name, block in first["blocks"].items()
    }
    second_hashes = {
        name: block["sha256"]
        for name, block in second["blocks"].items()
    }
    first["read_consistency"] = {
        "status": (
            "pass" if first_hashes == second_hashes else "fail"
        ),
        "first_block_sha256": first_hashes,
        "second_block_sha256": second_hashes,
        "cpu_state": (
            "stopped at main-side GameplayCommitAndReset entry with "
            "interrupt mask inherited from MainLoop"
        ),
    }
    return first


def analyze_transcript(
    transcript: list[str],
    *,
    terminal_address: int,
    forbidden_addresses: dict[str, int],
) -> dict[str, Any]:
    """Prove the measured interval used one allowed breakpoint and resume."""
    commands = [
        line[2:] for line in transcript if line.startswith("> ")
    ]
    breakpoint_packets = [
        command
        for command in commands
        if re.match(r"^[Zz][0-4],", command)
    ]
    expected = f"Z0,{terminal_address:x},2"
    expected_commands = expected_session_commands(terminal_address)
    continues = [command for command in commands if command == "c"]
    interrupts = [
        command for command in commands if command == "<interrupt>"
    ]
    writes = debugger_write_packets(transcript)
    breakpoint_acknowledged = any(
        line == f"> {expected}"
        and index + 1 < len(transcript)
        and transcript[index + 1] == "< OK"
        for index, line in enumerate(transcript)
    )
    cleanup_errors = [
        line for line in transcript if line.startswith("!")
    ]

    forbidden_hits: list[str] = []
    for symbol, address in forbidden_addresses.items():
        pattern = re.compile(
            rf"^[Zz][0-4],0*{address:x},", re.IGNORECASE
        )
        if any(pattern.match(packet) for packet in breakpoint_packets):
            forbidden_hits.append(symbol)

    interval_lines: list[str] = []
    natural_stop = False
    continue_indices = [
        index
        for index, line in enumerate(transcript)
        if line == "> c"
    ]
    if len(continue_indices) == 1:
        start = continue_indices[0]
        for end in range(start + 1, len(transcript)):
            line = transcript[end]
            if line == "< S05" or line.startswith("< T05"):
                interval_lines = transcript[start : end + 1]
                natural_stop = interval_lines == ["> c", line]
                break

    passed = (
        commands == expected_commands
        and breakpoint_packets == [expected]
        and breakpoint_acknowledged
        and continues == ["c"]
        and not interrupts
        and not writes
        and not forbidden_hits
        and not cleanup_errors
        and natural_stop
    )
    return {
        "status": "pass" if passed else "fail",
        "commands": commands,
        "expected_commands": expected_commands,
        "terminal_breakpoint_packet": expected,
        "terminal_breakpoint_acknowledged": breakpoint_acknowledged,
        "breakpoint_packets": breakpoint_packets,
        "continue_packets": len(continues),
        "interrupt_packets": len(interrupts),
        "debugger_write_packets": writes,
        "forbidden_breakpoint_symbols_observed": forbidden_hits,
        "cleanup_errors": cleanup_errors,
        "measured_interval_transcript": interval_lines,
        "measured_interval": (
            "one uninterrupted continue from pre-run debugger halt to the "
            "natural timeout commit entry"
        ),
    }


def _budget_signature(session: dict[str, Any]) -> dict[str, Any]:
    observation = session["observation"]
    debug = observation["debug"]
    renderer = observation["renderer"]
    dma = observation["dma_queue"]
    audio = observation["audio_shadow"]
    engine = observation["engine"]
    return {
        "engine": {
            name: engine[name]
            for name in (
                "state",
                "attempt",
                "tick",
                "terminal_reason",
                "last_error",
                "overflow_flags",
            )
        },
        "renderer": {
            name: renderer[name]
            for name in (
                "flags",
                "sprite_count",
                "scanline_max",
                "scanline_pixel_max",
                "dropped_sprites",
                "dma_rejects",
                "backlog_frames",
                "last_tick",
                "last_result",
            )
        },
        "dma_debug": {
            name: debug[name]
            for name in (
                "sprite_count",
                "sprite_scanline_max",
                "dma_words_frame",
                "dma_words_max",
                "dma_queue_depth",
                "dma_queue_high_water",
                "dma_overflow_count",
                "vblank_overrun_count",
                "dma_deferred_count",
                "dma_required_drop_count",
                "dma_invalid_count",
            )
        },
        "audio_cross_clock": {
            name: debug[name]
            for name in (
                "frame_counter",
                "audio_state",
                "z80_heartbeat",
                "audio_command_count",
                "m2_flags",
                "z80_timeout_count",
            )
        },
        "cross_clock": observation["cross_clock"],
        "audio_shadow": audio,
        "dma_queue": dma,
    }


def _all_hash_bindings_valid(
    sessions: list[dict[str, Any]], provenance: dict[str, Any]
) -> bool:
    required = (
        provenance.get("rom", {}).get("sha256"),
        provenance.get("rom", {}).get("expected_sha256"),
        provenance.get("emulator", {}).get("sha256"),
        provenance.get("default_config", {}).get("sha256"),
        provenance.get("symbol_map", {}).get("sha256"),
        provenance.get("report_schema", {}).get("sha256"),
        provenance.get("collector", {}).get("sha256"),
        provenance.get("transcript", {}).get("sha256"),
    )
    if not all(_valid_sha256(value) for value in required):
        return False
    verification = provenance.get("hash_verification", {})
    if (
        verification.get("status") != "pass"
        or verification.get("drift") != []
    ):
        return False
    before_after = verification.get("before_after")
    expected_before_after = {
        "rom",
        "emulator",
        "default_config",
        "symbol_map",
        "report_schema",
        "collector",
    }
    if (
        not isinstance(before_after, dict)
        or set(before_after) != expected_before_after
        or any(
            not isinstance(pair, dict)
            or pair.get("before") != pair.get("after")
            or pair.get("before")
            != provenance.get(name, {}).get("sha256")
            for name, pair in before_after.items()
        )
    ):
        return False
    emulator_sha = provenance["emulator"]["sha256"]
    default_config_sha = provenance["default_config"]["sha256"]
    for session in sessions:
        metadata = session.get("metadata", {})
        profile = metadata.get("controller_profile", {})
        runtime = metadata.get("runtime_copy", {})
        if not all(
            _valid_sha256(value)
            for value in (
                metadata.get("transcript_sha256"),
                profile.get("sha256"),
                profile.get("source_sha256"),
                runtime.get("executable_sha256"),
                runtime.get("default_config_sha256"),
            )
        ):
            return False
        session_lines = session.get("_transcript_lines")
        if not isinstance(session_lines, list) or not all(
            isinstance(line, str) for line in session_lines
        ):
            return False
        session_payload = (
            "\n".join(session_lines)
            + ("\n" if session_lines else "")
        ).encode("utf-8")
        if metadata["transcript_sha256"] != sha256_bytes(
            session_payload
        ):
            return False
        if (
            runtime["executable_sha256"] != emulator_sha
            or runtime["default_config_sha256"] != default_config_sha
            or profile["source_sha256"] != default_config_sha
            or profile.get("session_isolation", {}).get(
                "status"
            )
            != "pass"
            or profile.get("session_isolation", {}).get(
                "config_sha256"
            )
            != profile["sha256"]
        ):
            return False
    all_lines = [
        line
        for session in sessions
        for line in session["_transcript_lines"]
    ]
    transcript_payload = (
        "\n".join(all_lines) + ("\n" if all_lines else "")
    ).encode("utf-8")
    if provenance["transcript"]["sha256"] != sha256_bytes(
        transcript_payload
    ):
        return False
    return True


def build_budget_report(
    *,
    rom_sha256: str,
    sessions: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Purely evaluate captured sessions and build the public gate report."""
    checks: list[dict[str, Any]] = []
    observations = [
        session["observation"] for session in sessions
    ]

    distinct_pids = {
        session["metadata"]["process_id"] for session in sessions
    }
    runtime_directories = [
        session["metadata"]["runtime_copy"]["temporary_directory"]
        for session in sessions
    ]
    distinct_runtimes = set(runtime_directories)
    expected_session_ids = [
        f"session_{number}" for number in range(1, SESSION_COUNT + 1)
    ]
    expected_terminal_address = (
        provenance.get("symbol_map", {})
        .get("symbols", {})
        .get(TERMINAL_BREAKPOINT_SYMBOL)
    )
    checks.append(
        _check(
            "two_fresh_owned_sessions",
            len(sessions) == SESSION_COUNT
            and [session.get("session_id") for session in sessions]
            == expected_session_ids
            and len(distinct_pids) == SESSION_COUNT
            and len(distinct_runtimes) == SESSION_COUNT
            and all(
                isinstance(directory, str) and bool(directory)
                for directory in runtime_directories
            )
            and all(
                session["metadata"].get("process_id", 0) > 0
                and session["metadata"].get("launch_flags")
                == list(LAUNCH_FLAGS)
                and session["metadata"].get("profile_cleanup_verified")
                and session["metadata"].get("terminal_breakpoint_symbol")
                == TERMINAL_BREAKPOINT_SYMBOL
                and session["metadata"].get("terminal_breakpoint_address")
                == expected_terminal_address
                and session["metadata"].get("input_injection") == "none"
                and session["metadata"].get("resume_count") == 1
                and session["metadata"]
                .get("controller_profile", {})
                .get("status")
                == "pass"
                and session["metadata"]
                .get("controller_profile", {})
                .get("session_isolation", {})
                .get("status")
                == "pass"
                and session["metadata"]
                .get("controller_profile", {})
                .get("port_1_assignment")
                == "gamepad3.1"
                for session in sessions
            ),
            f"session IDs {expected_session_ids}; process IDs "
            f"{[session['metadata']['process_id'] for session in sessions]}; "
            f"isolated runtimes {runtime_directories}",
        )
    )

    transcript_policies = [
        session["metadata"]["transcript_policy"]
        for session in sessions
    ]
    checks.append(
        _check(
            "uninterrupted_main_side_measurement",
            len(sessions) == SESSION_COUNT
            and all(
                policy.get("status") == "pass"
                and policy.get("continue_packets") == 1
                and policy.get("commands")
                == expected_session_commands(
                    int(
                        session["metadata"][
                            "terminal_breakpoint_address"
                        ],
                        16,
                    )
                )
                and not policy.get(
                    "forbidden_breakpoint_symbols_observed"
                )
                for session, policy in zip(
                    sessions, transcript_policies
                )
            ),
            "each session used one continue and only the "
            "GameplayCommitAndReset breakpoint; no post-step, DMA, or "
            "VBlank breakpoint occurred",
        )
    )
    all_transcript = [
        line
        for session in sessions
        for line in session.get("_transcript_lines", [])
    ]
    writes = debugger_write_packets(all_transcript)
    checks.append(
        _check(
            "read_only_gdb",
            not writes
            and all(
                not policy.get("debugger_write_packets")
                for policy in transcript_policies
            ),
            f"G/M/P/X packets {writes}",
        )
    )

    terminal_values = [
        {
            key: observation["engine"][key]
            for key in (
                "magic",
                "abi_version",
                "control_bytes",
                "state",
                "attempt",
                "tick",
                "terminal_reason",
                "last_error",
                "overflow_flags",
            )
        }
        for observation in observations
    ]
    checks.append(
        _check(
            "natural_tick_720_timeout_boundary",
            len(observations) == SESSION_COUNT
            and all(
                engine["magic"] == ENGINE_MAGIC
                and engine["abi_version"] == ENGINE_ABI_VERSION
                and engine["control_bytes"] == 0x0400
                and engine["state"] == ENGINE_STATE_TERMINAL
                and engine["attempt"] == 0
                and engine["tick"] == ENGINE_ATTEMPT_TICKS
                and engine["terminal_reason"] == ENGINE_TERMINAL_TIMEOUT
                and engine["last_error"] == 0
                and engine["overflow_flags"] == 0
                for engine in (
                    observation["engine"]
                    for observation in observations
                )
            ),
            f"terminal observations {_stable_detail(terminal_values)}",
        )
    )

    checks.append(
        _check(
            "stopped_reads_are_consistent",
            len(observations) == SESSION_COUNT
            and all(
                observation["read_consistency"]["status"] == "pass"
                for observation in observations
            ),
            "all five blocks matched across two reads while the CPU was "
            "stopped",
        )
    )

    debug_values = [
        {
            key: observation["debug"][key]
            for key in (
                "magic",
                "abi_version",
                "abi_size",
                "sequence",
                "heartbeat",
                "frame_counter",
                "frame_token",
                "fatal_code",
                "boot_stage",
                "hardware_flags",
                "audio_state",
                "z80_heartbeat",
                "audio_command_count",
                "m2_flags",
            )
        }
        for observation in observations
    ]
    checks.append(
        _check(
            "runtime_debug_health",
            len(observations) == SESSION_COUNT
            and all(
                debug["magic"] == DEBUG_MAGIC
                and debug["abi_version"] == DEBUG_ABI_VERSION
                and debug["abi_size"] == DEBUG_ABI_SIZE
                and debug["sequence"] % 2 == 0
                and debug["heartbeat"] == debug["frame_counter"]
                and debug["frame_counter"] == ENGINE_ATTEMPT_TICKS
                and debug["frame_counter"] < Z80_HEARTBEAT_MODULUS
                and debug["frame_token"] == 0
                and debug["fatal_code"] == 0
                and debug["boot_stage"] == BOOT_STAGE_RUNNING
                and debug["hardware_flags"] == 0
                for debug in (
                    observation["debug"]
                    for observation in observations
                )
            ),
            f"debug observations {_stable_detail(debug_values)}",
        )
    )

    audio_values = [
        {
            "session_id": session["session_id"],
            "audio_state": observation["debug"]["audio_state"],
            "z80_heartbeat": observation["debug"]["z80_heartbeat"],
            "audio_command_count": observation["debug"][
                "audio_command_count"
            ],
            "m2_flags": observation["debug"]["m2_flags"],
            "z80_timeout_count": observation["debug"][
                "z80_timeout_count"
            ],
            **{
                f"shadow_{name}": value
                for name, value in observation["audio_shadow"].items()
            },
        }
        for session, observation in zip(sessions, observations)
    ]
    checks.append(
        _check(
            AUDIO_HEALTH_CHECK,
            len(audio_values) == SESSION_COUNT
            and all(
                values["audio_state"] == AUDIO_STATE_PLAYING
                and values["z80_heartbeat"] > 0
                and values["audio_command_count"]
                == AUDIO_COMMAND_COUNT_EXPECTED
                and values["m2_flags"] == M2_HEALTHY_FLAGS
                and values["z80_timeout_count"] == 0
                and values["shadow_signature"]
                == AUDIO_STATUS_SIGNATURE
                and values["shadow_abi_version"]
                == AUDIO_STATUS_ABI_VERSION
                and values["shadow_state"] == AUDIO_STATE_PLAYING
                and values["shadow_flags"]
                == AUDIO_STATUS_FLAGS_HEALTHY
                and not (
                    values["shadow_flags"] & AUDIO_STATUS_FAULT_MASK
                )
                and values["shadow_heartbeat"]
                == values["z80_heartbeat"]
                and values["shadow_queue_read_sequence"]
                == AUDIO_QUEUE_READ_EXPECTED
                and 1
                <= values["shadow_queue_high_water"]
                <= AUDIO_QUEUE_CAPACITY
                and values["shadow_processed_commands"]
                == AUDIO_COMMAND_COUNT_EXPECTED
                and values["shadow_processed_commands"]
                == values["audio_command_count"]
                and values["shadow_late_tick_count"] == 0
                and values["shadow_ym_write_count"] > 0
                and values["shadow_psg_write_count"] > 0
                and values["shadow_active_layers"]
                == AUDIO_ACTIVE_LAYERS_EXPECTED
                and values["shadow_last_error_or_command"]
                == AUDIO_LAST_COMMAND_EXPECTED
                for values in audio_values
            ),
            f"audio observations {_stable_detail(audio_values)}; "
            "required playing state "
            f"{AUDIO_STATE_PLAYING}, command count "
            f"{AUDIO_COMMAND_COUNT_EXPECTED}, M2 flags "
            f"${M2_HEALTHY_FLAGS:04X}, zero Z80 timeouts/late ticks/"
            f"fault flags, queue high-water within {AUDIO_QUEUE_CAPACITY}, "
            "and observed YM/PSG writes",
        )
    )

    cross_clock_values: list[dict[str, int | str]] = []
    cross_clock_valid = len(observations) == SESSION_COUNT
    for session, observation in zip(sessions, observations):
        debug = observation["debug"]
        derived = _cross_clock_observation(debug)
        captured = observation.get("cross_clock")
        cross_clock_values.append(
            {
                "session_id": session["session_id"],
                **derived,
            }
        )
        cross_clock_valid = (
            cross_clock_valid
            and captured == derived
            and derived["m68k_frame_counter"] == ENGINE_ATTEMPT_TICKS
            and derived["m68k_frame_counter"] < Z80_HEARTBEAT_MODULUS
            and derived["z80_vint_heartbeat"]
            >= derived["m68k_frame_counter"]
            and 0
            <= derived["z80_vint_excess"]
            <= Z80_VINT_STARTUP_ALLOWANCE
        )
    checks.append(
        _check(
            CROSS_CLOCK_CHECK,
            cross_clock_valid,
            "cross-clock observations "
            f"{_stable_detail(cross_clock_values)}; terminal Z80 "
            "heartbeat must be at least the 68000 frame counter with no "
            f"more than {Z80_VINT_STARTUP_ALLOWANCE} excess V-ints",
        )
    )

    renderer_values = [
        observation["renderer"] for observation in observations
    ]
    checks.append(
        _check(
            "renderer_abi_and_terminal_alignment",
            len(renderer_values) == SESSION_COUNT
            and all(
                renderer["magic"] == RENDERER_MAGIC
                and renderer["version"] == RENDERER_VERSION
                and renderer["bytes"] == RENDERER_BYTES
                and renderer["flags"] & RENDERER_FLAG_INITIALIZED
                and not renderer["flags"] & RENDERER_FAULT_FLAGS
                and renderer["last_attempt"] == 0
                and renderer["last_tick"] == ENGINE_ATTEMPT_TICKS - 1
                and renderer["last_result"] == RENDERER_RESULT_OK
                for renderer in renderer_values
            ),
            "renderer ABI valid, current fault flags clear, and last "
            f"completed build is tick {ENGINE_ATTEMPT_TICKS - 1}",
        )
    )

    fault_values: list[dict[str, int | str]] = []
    for session, observation in zip(sessions, observations):
        debug = observation["debug"]
        renderer = observation["renderer"]
        fault_values.append(
            {
                "session_id": session["session_id"],
                "renderer_backlog_frames": renderer[
                    "backlog_frames"
                ],
                "vblank_overrun_count": debug[
                    "vblank_overrun_count"
                ],
                "renderer_dropped_sprites": renderer[
                    "dropped_sprites"
                ],
                "renderer_dma_rejects": renderer["dma_rejects"],
                "dma_overflow_count": debug["dma_overflow_count"],
                "dma_deferred_count": debug["dma_deferred_count"],
                "dma_required_drop_count": debug[
                    "dma_required_drop_count"
                ],
                "dma_invalid_count": debug["dma_invalid_count"],
                "z80_timeout_count": debug["z80_timeout_count"],
            }
        )
    checks.append(
        _check(
            "all_cumulative_fault_counters_zero",
            len(fault_values) == SESSION_COUNT
            and all(
                all(
                    value == 0
                    for key, value in values.items()
                    if key != "session_id"
                )
                for values in fault_values
            ),
            f"fault counters {_stable_detail(fault_values)}",
        )
    )

    queue_values = [
        observation["dma_queue"] for observation in observations
    ]
    checks.append(
        _check(
            "terminal_queue_fully_committed",
            len(queue_values) == SESSION_COUNT
            and all(
                queue["published_depth"] == 0
                and queue["build_depth"] == 0
                and queue["build_words"] == 0
                and queue["frame_pending"] == 0
                and queue["reserved_0"] == 0
                and queue["reserved_1"] == 0
                and observation["debug"]["dma_queue_depth"] == 0
                for queue, observation in zip(
                    queue_values, observations
                )
            ),
            f"DMA queue controls {_stable_detail(queue_values)}",
        )
    )

    dma_capacity_values: list[dict[str, int]] = []
    for observation in observations:
        debug = observation["debug"]
        queue = observation["dma_queue"]
        dma_capacity_values.append(
            {
                "words_frame": debug["dma_words_frame"],
                "words_max": debug["dma_words_max"],
                "debug_depth": debug["dma_queue_depth"],
                "high_water": debug["dma_queue_high_water"],
                "published_depth": queue["published_depth"],
                "build_depth": queue["build_depth"],
                "build_words": queue["build_words"],
                "commit_words": queue["commit_words"],
            }
        )
    checks.append(
        _check(
            "dma_capacities_in_bounds",
            len(dma_capacity_values) == SESSION_COUNT
            and all(
                0 < values["words_frame"]
                <= DMA_ORDINARY_WORD_LIMIT
                and values["words_frame"] <= values["words_max"]
                <= DMA_ORDINARY_WORD_LIMIT
                and 0 <= values["debug_depth"]
                <= DMA_QUEUE_CAPACITY
                and 0 < values["high_water"]
                <= DMA_QUEUE_CAPACITY
                and 0 <= values["published_depth"]
                <= DMA_QUEUE_CAPACITY
                and 0 <= values["build_depth"]
                <= DMA_QUEUE_CAPACITY
                and 0 <= values["build_words"]
                <= DMA_ORDINARY_WORD_LIMIT
                and 0 < values["commit_words"]
                <= DMA_ORDINARY_WORD_LIMIT
                for values in dma_capacity_values
            ),
            f"DMA capacities {_stable_detail(dma_capacity_values)}; limits "
            f"{DMA_ORDINARY_WORD_LIMIT} words/{DMA_QUEUE_CAPACITY} entries",
        )
    )

    sprite_capacity_values: list[dict[str, int]] = []
    for observation in observations:
        debug = observation["debug"]
        renderer = observation["renderer"]
        sprite_capacity_values.append(
            {
                "renderer_sprites": renderer["sprite_count"],
                "debug_sprites": debug["sprite_count"],
                "renderer_scanline": renderer["scanline_max"],
                "debug_scanline": debug["sprite_scanline_max"],
                "scanline_pixels": renderer["scanline_pixel_max"],
            }
        )
    checks.append(
        _check(
            "renderer_capacities_in_bounds",
            len(sprite_capacity_values) == SESSION_COUNT
            and all(
                0 < values["renderer_sprites"]
                <= RENDERER_SPRITE_LIMIT
                and values["debug_sprites"]
                == values["renderer_sprites"]
                and 0 < values["renderer_scanline"]
                <= RENDERER_SCANLINE_SPRITE_LIMIT
                and values["debug_scanline"]
                == values["renderer_scanline"]
                and 0 < values["scanline_pixels"]
                <= RENDERER_SCANLINE_PIXEL_LIMIT
                for values in sprite_capacity_values
            ),
            "renderer capacities "
            f"{_stable_detail(sprite_capacity_values)}; limits "
            f"{RENDERER_SPRITE_LIMIT} sprites/"
            f"{RENDERER_SCANLINE_SPRITE_LIMIT} per scanline/"
            f"{RENDERER_SCANLINE_PIXEL_LIMIT} pixels",
        )
    )

    signatures = [
        _budget_signature(session) for session in sessions
    ]
    checks.append(
        _check(
            "fresh_session_budget_reproducibility",
            len(signatures) == SESSION_COUNT
            and signatures[0] == signatures[1],
            f"budget signatures equal: "
            f"{len(signatures) == SESSION_COUNT and signatures[0] == signatures[1]}",
        )
    )
    checks.append(
        _check(
            "exact_hash_bindings_and_no_drift",
            _all_hash_bindings_valid(sessions, provenance)
            and provenance["rom"]["sha256"] == rom_sha256
            and provenance["rom"]["expected_sha256"] == rom_sha256,
            "ROM, expected ROM, emulator, source config, per-session "
            "configs, symbol map, collector, schema, and transcript are "
            "SHA-256 bound and unchanged",
        )
    )

    passed = (
        len(sessions) == SESSION_COUNT
        and tuple(check["check"] for check in checks)
        == REQUIRED_CHECK_NAMES
        and all(check["status"] == "pass" for check in checks)
    )
    public_sessions = [
        {
            key: value
            for key, value in session.items()
            if not key.startswith("_")
        }
        for session in sessions
    ]
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "sessions": public_sessions,
        "provenance": provenance,
        "debugger_write_packets": writes,
        "limitations": list(LIMITATIONS),
    }


def _observation_integrity_errors(
    session: dict[str, Any], session_number: int
) -> list[str]:
    errors: list[str] = []
    prefix = f"session {session_number}"
    observation = session.get("observation")
    if not isinstance(observation, dict):
        return [f"{prefix} observation is not an object"]
    expected_observation_keys = {
        "engine",
        "debug",
        "cross_clock",
        "renderer",
        "dma_queue",
        "audio_shadow",
        "blocks",
        "read_consistency",
    }
    if set(observation) != expected_observation_keys:
        errors.append(f"{prefix} observation field set mismatch")
    blocks = observation.get("blocks")
    if not isinstance(blocks, dict):
        return [f"{prefix} blocks are missing or not an object"]
    expected_block_names = {spec[0] for spec in BLOCK_SPECS}
    if set(blocks) != expected_block_names:
        errors.append(f"{prefix} block set mismatch")

    expected_hashes: dict[str, str] = {}
    for block_name, observation_key, address, size, parser in BLOCK_SPECS:
        block = blocks.get(block_name)
        if not isinstance(block, dict):
            errors.append(f"{prefix} block {block_name} is missing")
            continue
        if block.get("address") != f"{address:08X}":
            errors.append(f"{prefix} block {block_name} address mismatch")
        if block.get("bytes") != size:
            errors.append(f"{prefix} block {block_name} size mismatch")
        raw_hex = block.get("raw_hex")
        if (
            not isinstance(raw_hex, str)
            or raw_hex != raw_hex.upper()
            or not re.fullmatch(r"[0-9A-F]+", raw_hex)
        ):
            errors.append(f"{prefix} block {block_name} raw hex invalid")
            continue
        try:
            raw = bytes.fromhex(raw_hex)
        except ValueError:
            errors.append(f"{prefix} block {block_name} raw hex invalid")
            continue
        if len(raw) != size:
            errors.append(f"{prefix} block {block_name} raw size mismatch")
            continue
        digest = sha256_bytes(raw)
        expected_hashes[block_name] = digest
        if block.get("sha256") != digest:
            errors.append(f"{prefix} block {block_name} SHA-256 mismatch")
        try:
            parsed = parser(raw)
        except Exception as error:
            errors.append(
                f"{prefix} block {block_name} parse failed: {error}"
            )
            continue
        if observation.get(observation_key) != parsed:
            errors.append(
                f"{prefix} block {block_name} does not match "
                f"{observation_key}"
            )

    debug = observation.get("debug")
    if isinstance(debug, dict):
        try:
            expected_cross_clock = _cross_clock_observation(debug)
        except (KeyError, TypeError):
            errors.append(f"{prefix} cross-clock source fields invalid")
        else:
            if observation.get("cross_clock") != expected_cross_clock:
                errors.append(
                    f"{prefix} cross-clock derivation does not match "
                    "the debug block"
                )

    consistency = observation.get("read_consistency")
    if not isinstance(consistency, dict):
        errors.append(f"{prefix} read consistency record is missing")
    else:
        if consistency.get("status") != "pass":
            errors.append(f"{prefix} stopped reads are inconsistent")
        if consistency.get("first_block_sha256") != expected_hashes:
            errors.append(f"{prefix} first-read hashes do not match blocks")
        if consistency.get("second_block_sha256") != expected_hashes:
            errors.append(f"{prefix} second-read hashes do not match blocks")
    return errors


def _public_hash_binding_errors(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        return ["provenance is missing or not an object"]
    rom_sha = report.get("rom_sha256")
    required_hashes = (
        ("rom.sha256", provenance.get("rom", {}).get("sha256")),
        (
            "rom.expected_sha256",
            provenance.get("rom", {}).get("expected_sha256"),
        ),
        ("emulator.sha256", provenance.get("emulator", {}).get("sha256")),
        (
            "default_config.sha256",
            provenance.get("default_config", {}).get("sha256"),
        ),
        (
            "symbol_map.sha256",
            provenance.get("symbol_map", {}).get("sha256"),
        ),
        (
            "report_schema.sha256",
            provenance.get("report_schema", {}).get("sha256"),
        ),
        ("collector.sha256", provenance.get("collector", {}).get("sha256")),
        (
            "transcript.sha256",
            provenance.get("transcript", {}).get("sha256"),
        ),
    )
    for name, value in required_hashes:
        if not _valid_sha256(value):
            errors.append(f"invalid provenance hash {name}")
    if (
        provenance.get("rom", {}).get("sha256") != rom_sha
        or provenance.get("rom", {}).get("expected_sha256") != rom_sha
    ):
        errors.append("provenance ROM hashes do not match report ROM")
    if provenance.get("report_schema", {}).get("id") != REPORT_SCHEMA:
        errors.append("provenance report schema identifier mismatch")
    verification = provenance.get("hash_verification")
    if not isinstance(verification, dict):
        errors.append("hash verification record is missing")
    else:
        if verification.get("status") != "pass":
            errors.append("hash verification did not pass")
        if verification.get("drift") != []:
            errors.append("hash verification drift is not empty")
        before_after = verification.get("before_after")
        expected_names = {
            "rom",
            "emulator",
            "default_config",
            "symbol_map",
            "report_schema",
            "collector",
        }
        if (
            not isinstance(before_after, dict)
            or set(before_after) != expected_names
        ):
            errors.append("hash before/after records are missing")
        else:
            for name, pair in before_after.items():
                expected_hash = provenance.get(name, {}).get("sha256")
                if (
                    not isinstance(pair, dict)
                    or pair.get("before") != pair.get("after")
                    or pair.get("before") != expected_hash
                ):
                    errors.append(
                        "hash before/after records contain drift or "
                        f"binding mismatch for {name}"
                    )

    emulator_sha = provenance.get("emulator", {}).get("sha256")
    config_sha = provenance.get("default_config", {}).get("sha256")
    sessions = report.get("sessions")
    if isinstance(sessions, list):
        for index, session in enumerate(sessions, 1):
            metadata = (
                session.get("metadata", {})
                if isinstance(session, dict)
                else {}
            )
            runtime = metadata.get("runtime_copy", {})
            profile = metadata.get("controller_profile", {})
            isolation = profile.get("session_isolation", {})
            if runtime.get("executable_sha256") != emulator_sha:
                errors.append(
                    f"session {index} runtime executable hash mismatch"
                )
            if runtime.get("default_config_sha256") != config_sha:
                errors.append(
                    f"session {index} runtime config hash mismatch"
                )
            if profile.get("source_sha256") != config_sha:
                errors.append(
                    f"session {index} controller source hash mismatch"
                )
            if (
                profile.get("status") != "pass"
                or isolation.get("status") != "pass"
                or isolation.get("config_sha256") != profile.get("sha256")
            ):
                errors.append(
                    f"session {index} isolated controller profile invalid"
                )
    return errors


def _split_trace_sessions(
    transcript: list[str],
) -> tuple[dict[str, list[str]], list[str]]:
    errors: list[str] = []
    sessions: dict[str, list[str]] = {}
    cursor = 0
    for number in range(1, SESSION_COUNT + 1):
        session_id = f"session_{number}"
        begin = f"# {session_id} begin"
        end = f"# {session_id} end"
        if cursor >= len(transcript) or transcript[cursor] != begin:
            errors.append(f"trace is missing ordered marker {begin}")
            return sessions, errors
        try:
            end_index = transcript.index(end, cursor + 1)
        except ValueError:
            errors.append(f"trace is missing marker {end}")
            return sessions, errors
        sessions[session_id] = transcript[cursor : end_index + 1]
        cursor = end_index + 1
    if cursor != len(transcript):
        errors.append("trace contains data outside the two session blocks")
    return sessions, errors


def _trace_read_errors(
    session: dict[str, Any],
    transcript: list[str],
    session_number: int,
) -> list[str]:
    errors: list[str] = []
    prefix = f"session {session_number}"
    observation = session.get("observation", {})
    blocks = observation.get("blocks", {})
    reads: list[bytes] = []
    for index, line in enumerate(transcript):
        if not line.startswith("> m"):
            continue
        if index + 1 >= len(transcript) or not transcript[
            index + 1
        ].startswith("< "):
            errors.append(f"{prefix} trace memory read has no response")
            continue
        try:
            reads.append(bytes.fromhex(transcript[index + 1][2:]))
        except ValueError:
            errors.append(f"{prefix} trace memory response is not hex")
    expected_read_count = len(BLOCK_SPECS) * 2
    if len(reads) != expected_read_count:
        errors.append(
            f"{prefix} trace has {len(reads)} memory reads; "
            f"{expected_read_count} required"
        )
        return errors
    consistency = observation.get("read_consistency", {})
    for round_index in range(2):
        for block_index, (
            block_name,
            _key,
            _address,
            size,
            _parser,
        ) in enumerate(BLOCK_SPECS):
            raw = reads[round_index * len(BLOCK_SPECS) + block_index]
            if len(raw) != size:
                errors.append(
                    f"{prefix} trace {block_name} round "
                    f"{round_index + 1} size mismatch"
                )
                continue
            digest = sha256_bytes(raw)
            hash_key = (
                "first_block_sha256"
                if round_index == 0
                else "second_block_sha256"
            )
            if consistency.get(hash_key, {}).get(block_name) != digest:
                errors.append(
                    f"{prefix} trace {block_name} round "
                    f"{round_index + 1} hash mismatch"
                )
            if round_index == 0 and blocks.get(block_name, {}).get(
                "raw_hex"
            ) != raw.hex().upper():
                errors.append(
                    f"{prefix} trace first {block_name} read does not "
                    "match report"
                )
    return errors


def _current_provenance_errors(
    report: dict[str, Any], root: Path
) -> tuple[list[str], list[dict[str, Any]] | None]:
    errors: list[str] = []
    provenance = report.get("provenance", {})
    expected_files = (
        ("rom", ROM_RELATIVE_PATH),
        ("emulator", EMULATOR_RELATIVE_PATH),
        ("default_config", DEFAULT_CONFIG_RELATIVE_PATH),
        ("symbol_map", SYMBOL_MAP_RELATIVE_PATH),
        ("report_schema", SCHEMA_RELATIVE_PATH),
        ("collector", COLLECTOR_RELATIVE_PATH),
        ("transcript", TRACE_RELATIVE_PATH),
    )
    for key, relative in expected_files:
        record = provenance.get(key)
        if not isinstance(record, dict):
            errors.append(f"provenance record {key} is missing")
            continue
        if record.get("path") != relative:
            errors.append(f"provenance path {key} is not canonical")
        path = root / relative
        if not path.is_file():
            errors.append(f"current provenance file is missing: {relative}")
            continue
        if record.get("sha256") != sha256_file(path):
            errors.append(f"current provenance hash drift: {key}")

    symbol_path = root / SYMBOL_MAP_RELATIVE_PATH
    symbols: dict[str, int] = {}
    if symbol_path.is_file():
        try:
            symbols = parse_vasm_symbols(symbol_path)
        except Exception as error:
            errors.append(f"current symbol map parse failed: {error}")
        else:
            layout = symbol_layout_errors(symbols)
            errors.extend(f"current symbol layout: {item}" for item in layout)
            reported_symbols = provenance.get("symbol_map", {}).get(
                "symbols"
            )
            expected_symbols = {
                name: f"{symbols[name]:08X}"
                for name in REQUIRED_SYMBOLS
                if name in symbols
            }
            if reported_symbols != expected_symbols:
                errors.append(
                    "reported symbols do not match the current symbol map"
                )

    trace_path = root / TRACE_RELATIVE_PATH
    if not trace_path.is_file() or not symbols:
        return errors, None
    transcript = trace_path.read_text("utf-8").splitlines()
    if provenance.get("transcript", {}).get("commands") != sum(
        line.startswith("> ") for line in transcript
    ):
        errors.append("transcript command count mismatch")
    trace_sessions, split_errors = _split_trace_sessions(transcript)
    errors.extend(split_errors)
    public_sessions = report.get("sessions")
    if not isinstance(public_sessions, list) or len(public_sessions) != 2:
        return errors, None

    hydrated = copy.deepcopy(public_sessions)
    forbidden = {
        name: symbols[name]
        for name in FORBIDDEN_BREAKPOINT_SYMBOLS
        if name in symbols
    }
    if len(forbidden) != len(FORBIDDEN_BREAKPOINT_SYMBOLS):
        return errors, None
    for index, session in enumerate(hydrated, 1):
        session_id = f"session_{index}"
        lines = trace_sessions.get(session_id)
        if lines is None:
            errors.append(f"trace session {session_id} is missing")
            continue
        session["_transcript_lines"] = lines
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        metadata = session.get("metadata", {})
        if metadata.get("transcript_sha256") != sha256_bytes(payload):
            errors.append(f"session {index} transcript SHA-256 mismatch")
        policy = analyze_transcript(
            lines,
            terminal_address=symbols[TERMINAL_BREAKPOINT_SYMBOL],
            forbidden_addresses=forbidden,
        )
        if metadata.get("transcript_policy") != policy:
            errors.append(f"session {index} transcript policy mismatch")
        errors.extend(_trace_read_errors(session, lines, index))
    return errors, hydrated


def report_contract_errors(
    report: dict[str, Any], *, root: Path | None = None
) -> list[str]:
    """Independently re-evaluate a public report and its bound raw evidence."""
    errors: list[str] = []
    required = (
        "schema",
        "scope",
        "evidence_type",
        "status",
        "rom_sha256",
        "checks",
        "sessions",
        "provenance",
        "debugger_write_packets",
        "limitations",
    )
    for key in required:
        if key not in report:
            errors.append(f"missing top-level field {key}")
    if report.get("schema") != REPORT_SCHEMA:
        errors.append("schema identifier mismatch")
    if report.get("scope") != "Milestone4":
        errors.append("scope mismatch")
    if report.get("evidence_type") != EVIDENCE_TYPE:
        errors.append("evidence type mismatch")
    if report.get("status") not in {"pass", "fail"}:
        errors.append("invalid status")
    if not _valid_sha256(report.get("rom_sha256")):
        errors.append("invalid ROM SHA-256")

    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty list")
        check_names: tuple[Any, ...] = ()
    else:
        if any(
            not isinstance(item, dict)
            or item.get("status") not in {"pass", "fail"}
            or not isinstance(item.get("detail"), str)
            for item in checks
        ):
            errors.append("invalid check record")
        check_names = tuple(
            item.get("check") if isinstance(item, dict) else None
            for item in checks
        )

    sessions = report.get("sessions")
    if not isinstance(sessions, list):
        errors.append("sessions must be a list")
        sessions = []
    if report.get("status") == "pass":
        if len(sessions) != SESSION_COUNT:
            errors.append("passing report requires exactly two sessions")
        if check_names != REQUIRED_CHECK_NAMES:
            errors.append(
                "passing report check set/order does not match the exact "
                "required contract"
            )
        if any(
            not isinstance(item, dict) or item.get("status") != "pass"
            for item in checks
        ):
            errors.append("passing report contains a failed check")
        if report.get("debugger_write_packets") != []:
            errors.append("passing report contains debugger writes")

        expected_ids = ["session_1", "session_2"]
        if [
            session.get("session_id")
            if isinstance(session, dict)
            else None
            for session in sessions
        ] != expected_ids:
            errors.append("passing report session IDs/order are invalid")

        for index, session in enumerate(sessions, 1):
            if isinstance(session, dict):
                errors.extend(_observation_integrity_errors(session, index))
            else:
                errors.append(f"session {index} is not an object")

        errors.extend(_public_hash_binding_errors(report))

        # Recompute every non-provenance check from the public observations.
        # Exact transcript/hash reconstruction follows when root is supplied.
        try:
            recompute_sessions = copy.deepcopy(sessions)
            for session in recompute_sessions:
                session["_transcript_lines"] = []
            recomputed = build_budget_report(
                rom_sha256=report["rom_sha256"],
                sessions=recompute_sessions,
                provenance=report["provenance"],
            )
            recomputed_checks = {
                item["check"]: item
                for item in recomputed["checks"]
                if item["check"] != "exact_hash_bindings_and_no_drift"
            }
            reported_checks = {
                item["check"]: item
                for item in checks
                if isinstance(item, dict)
                and item.get("check")
                != "exact_hash_bindings_and_no_drift"
            }
            if reported_checks != recomputed_checks:
                errors.append(
                    "reported semantic checks do not match recomputed "
                    "observations"
                )
        except Exception as error:
            errors.append(f"semantic check recomputation failed: {error}")

        if root is not None:
            current_errors, hydrated = _current_provenance_errors(
                report, root.resolve()
            )
            errors.extend(current_errors)
            if hydrated is not None:
                try:
                    recomputed = build_budget_report(
                        rom_sha256=report["rom_sha256"],
                        sessions=hydrated,
                        provenance=report["provenance"],
                    )
                    if (
                        report.get("status") != recomputed.get("status")
                        or report.get("checks") != recomputed.get("checks")
                        or report.get("debugger_write_packets")
                        != recomputed.get("debugger_write_packets")
                    ):
                        errors.append(
                            "report does not match full trace/provenance "
                            "recomputation"
                        )
                except Exception as error:
                    errors.append(
                        f"full artifact recomputation failed: {error}"
                    )

    if not isinstance(report.get("limitations"), list) or not report.get(
        "limitations"
    ):
        errors.append("limitations must be non-empty")
    return errors


def _run_live_session(
    *,
    session_id: str,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    observation: dict[str, Any] | None = None
    runtime_copy: dict[str, Any] | None = None
    controller_profile: dict[str, Any] | None = None
    process_id: int | None = None
    profile_path: Path | None = None
    started_utc = datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(
        prefix=f"recursion94-m4-budget-{session_id}-"
    ) as profile_text:
        profile_path = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile_path
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile_path
        )
        device_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile_path / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            device_profile_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]

        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=isolated_runtime,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process_id = process.pid
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            if process.poll() is not None:
                raise EvidenceFailure(
                    f"{session_id} BlastEm exited early with "
                    f"{process.returncode}"
                )

            terminal = symbols[TERMINAL_BREAKPOINT_SYMBOL]
            if remote.request(f"Z0,{terminal:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} terminal breakpoint insertion failed"
                )

            # This is the complete measured interval. Do not add debugger
            # interaction between this continue and its natural stop.
            require_stop(remote.request("c"))
            observation = capture_stopped_snapshot(remote)
        finally:
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")

    if (
        observation is None
        or runtime_copy is None
        or controller_profile is None
        or process_id is None
        or profile_path is None
    ):
        raise EvidenceFailure(f"{session_id} produced no observation")
    cleanup_verified = not profile_path.exists()
    session_lines = transcript[start_index:]
    transcript_policy = analyze_transcript(
        session_lines,
        terminal_address=symbols[TERMINAL_BREAKPOINT_SYMBOL],
        forbidden_addresses={
            name: symbols[name]
            for name in FORBIDDEN_BREAKPOINT_SYMBOLS
        },
    )
    return {
        "session_id": session_id,
        "metadata": {
            "process_id": process_id,
            "started_utc": started_utc,
            "launch_flags": list(LAUNCH_FLAGS),
            "runtime_copy": runtime_copy,
            "controller_profile": controller_profile,
            "profile_cleanup_verified": cleanup_verified,
            "terminal_breakpoint_symbol": TERMINAL_BREAKPOINT_SYMBOL,
            "terminal_breakpoint_address": (
                f"{symbols[TERMINAL_BREAKPOINT_SYMBOL]:08X}"
            ),
            "input_injection": "none",
            "resume_count": 1,
            "transcript_policy": transcript_policy,
            "transcript_sha256": hashlib.sha256(
                (
                    "\n".join(session_lines)
                    + ("\n" if session_lines else "")
                ).encode("utf-8")
            ).hexdigest(),
        },
        "observation": observation,
        "_transcript_lines": session_lines,
    }


def _write_transcript(path: Path, transcript: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(transcript) + ("\n" if transcript else ""),
        encoding="utf-8",
        newline="\n",
    )


def _failure_report(
    *,
    rom_sha256: str,
    error: Exception,
    provenance: dict[str, Any],
    sessions: list[dict[str, Any]],
    transcript: list[str],
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    public_sessions = [
        {
            key: value
            for key, value in session.items()
            if not key.startswith("_")
        }
        for session in sessions
    ]
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "rom_sha256": (
            rom_sha256 if _valid_sha256(rom_sha256) else "0" * 64
        ),
        "checks": [
            _check("m4_uninterrupted_budget_session", False, detail)
        ],
        "sessions": public_sessions,
        "provenance": provenance,
        "debugger_write_packets": debugger_write_packets(transcript),
        "limitations": list(LIMITATIONS),
        "error": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--lock-timeout-seconds", type=float, default=600.0
    )
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    expected_sha256 = args.expected_sha256.strip().lower()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m4-renderer-budget.json"
    )
    trace_path = (
        args.trace.resolve()
        if args.trace
        else root / TRACE_RELATIVE_PATH
    )
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    sessions: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    rom_sha256 = ""

    try:
        # An interrupted or failed new capture must never leave a prior pass
        # looking canonical.
        report_path.unlink(missing_ok=True)
        trace_path.unlink(missing_ok=True)
        if os.name != "nt":
            raise EvidenceFailure(
                "live M4 budget evidence requires native Windows BlastEm"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError(
                "--expected-sha256 must be exactly 64 hexadecimal digits"
            )
        if not 15.0 <= args.timeout_seconds <= 600.0:
            raise ValueError("--timeout-seconds must be within 15..600")
        if not 10.0 <= args.lock_timeout_seconds <= 900.0:
            raise ValueError(
                "--lock-timeout-seconds must be within 10..900"
            )

        emulator_directory = root / ".toolcache/blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        symbol_path = root / "build/recursion94-debug.sym"
        schema_path = root / SCHEMA_RELATIVE_PATH
        collector_path = Path(__file__).resolve()
        for path, description in (
            (rom, "Debug ROM"),
            (emulator, "pinned BlastEm"),
            (default_config, "pinned default.cfg"),
            (symbol_path, "Debug symbol map"),
            (schema_path, "M4 budget report schema"),
            (collector_path, "M4 budget collector"),
        ):
            if not path.is_file():
                raise EvidenceFailure(f"{description} is missing: {path}")

        rom_sha256 = sha256_file(rom)
        if rom_sha256 != expected_sha256:
            raise EvidenceFailure(
                f"Debug ROM SHA-256 {rom_sha256} does not match "
                f"{expected_sha256}"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [
            name for name in REQUIRED_SYMBOLS if name not in symbols
        ]
        if missing:
            raise EvidenceFailure(
                f"Debug symbol map is missing required symbols {missing}"
            )
        layout_errors = symbol_layout_errors(symbols)
        if layout_errors:
            raise EvidenceFailure(
                f"Debug state-symbol layout drift: {layout_errors}"
            )

        user_config = verify_no_user_blastem_override()
        provenance = {
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "rom": {
                "path": _relative_or_absolute(rom, root),
                "sha256": rom_sha256,
                "expected_sha256": expected_sha256,
                "hash_policy": (
                    "mandatory invocation hash; verified before and after "
                    "both sessions"
                ),
            },
            "emulator": {
                "path": _relative_or_absolute(emulator, root),
                "sha256": sha256_file(emulator),
            },
            "default_config": {
                "path": _relative_or_absolute(default_config, root),
                "sha256": sha256_file(default_config),
            },
            "symbol_map": {
                "path": _relative_or_absolute(symbol_path, root),
                "sha256": sha256_file(symbol_path),
                "symbols": {
                    name: f"{symbols[name]:08X}"
                    for name in REQUIRED_SYMBOLS
                },
            },
            "report_schema": {
                "path": SCHEMA_RELATIVE_PATH,
                "sha256": sha256_file(schema_path),
                "id": REPORT_SCHEMA,
            },
            "collector": {
                "path": _relative_or_absolute(collector_path, root),
                "sha256": sha256_file(collector_path),
            },
            "user_config_preflight": user_config,
            "serialized_lock": _relative_or_absolute(lock_path, root),
            "launch_flags": list(LAUNCH_FLAGS),
        }

        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            for number in range(1, SESSION_COUNT + 1):
                sessions.append(
                    _run_live_session(
                        session_id=f"session_{number}",
                        rom=rom,
                        emulator_directory=emulator_directory,
                        default_config=default_config,
                        symbols=symbols,
                        timeout_seconds=args.timeout_seconds,
                        transcript=transcript,
                    )
                )

        _write_transcript(trace_path, transcript)
        provenance["transcript"] = {
            "path": _relative_or_absolute(trace_path, root),
            "sha256": sha256_file(trace_path),
            "commands": sum(
                line.startswith("> ") for line in transcript
            ),
        }

        before_after = {
            "rom": (
                provenance["rom"]["sha256"],
                sha256_file(rom),
            ),
            "emulator": (
                provenance["emulator"]["sha256"],
                sha256_file(emulator),
            ),
            "default_config": (
                provenance["default_config"]["sha256"],
                sha256_file(default_config),
            ),
            "symbol_map": (
                provenance["symbol_map"]["sha256"],
                sha256_file(symbol_path),
            ),
            "report_schema": (
                provenance["report_schema"]["sha256"],
                sha256_file(schema_path),
            ),
            "collector": (
                provenance["collector"]["sha256"],
                sha256_file(collector_path),
            ),
        }
        drift = [
            name
            for name, (before, after) in before_after.items()
            if before != after
        ]
        provenance["hash_verification"] = {
            "status": "pass" if not drift else "fail",
            "drift": drift,
            "before_after": {
                name: {"before": before, "after": after}
                for name, (before, after) in before_after.items()
            },
        }
        if drift:
            raise EvidenceFailure(
                f"capture inputs changed during collection: {drift}"
            )

        report = build_budget_report(
            rom_sha256=rom_sha256,
            sessions=sessions,
            provenance=provenance,
        )
        contract_errors = report_contract_errors(report, root=root)
        if contract_errors:
            raise EvidenceFailure(
                f"report contract errors: {contract_errors}"
            )
    except Exception as error:
        _write_transcript(trace_path, transcript)
        if trace_path.is_file():
            provenance["transcript"] = {
                "path": _relative_or_absolute(trace_path, root),
                "sha256": sha256_file(trace_path),
                "commands": sum(
                    line.startswith("> ") for line in transcript
                ),
            }
        report = _failure_report(
            rom_sha256=rom_sha256,
            error=error,
            provenance=provenance,
            sessions=sessions,
            transcript=transcript,
        )

    write_json(report_path, report)
    if report["status"] != "pass":
        print(
            "M4 uninterrupted renderer/VBlank/cross-clock budget "
            "evidence: FAIL "
            f"({report.get('error', 'one or more checks failed')})",
            file=sys.stderr,
        )
        return 1
    print(
        "M4 uninterrupted renderer/VBlank/cross-clock budget evidence: "
        "PASS; "
        f"ROM {report['rom_sha256']}; two fresh tick-720 sessions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
