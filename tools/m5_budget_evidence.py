#!/usr/bin/env python3
"""Collect hash-bound B03 renderer/DMA/pool budget evidence.

Two fresh owned BlastEm processes each execute the canonical 720+720+292
route. A permanent post-gameplay breakpoint and BlastEm's one-frame UI
advance enforce one VDP frame per ordinary step and two additional measured
reset-service frames at each timeout. Before every
advance, a unique host-only window-title sentinel must be overwritten by
BlastEm's own internal ``- PAUSED`` transition.  GDB performs breakpoint
control and read-only guest inspection; it never writes guest memory,
registers, or PC.

This is deliberately a counter/capacity gate, not a cycle profiler.  A pass
establishes exact frame/step cadence, bounded state, zero published cumulative
faults, and deterministic signatures for the bound NTSC Debug fixture only.
It makes no Release, PAL, physical-hardware, or spare-cycle-headroom claim.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import m5_b03_runtime_evidence as b03  # noqa: E402
from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    GdbRemote,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    parse_vasm_symbols,
    post_virtual_key,
    require_gdb_port_available,
    require_stop,
    sha256_file,
    verify_no_user_blastem_override,
    write_standard_pad_profile,
    write_json,
)
from tools.m3_runtime_evidence import (  # noqa: E402
    ACTOR_CAPACITY,
    ENGINE_ABI_VERSION,
    ENGINE_CHECKPOINT_INTERVAL,
    ENGINE_CONTROL_BASE,
    ENGINE_CONTROL_USED_BYTES,
    ENGINE_FLAG_LOADED,
    ENGINE_FLAG_PROJECTION_VALID,
    ENGINE_MAGIC,
    INPUT_STATE_BASE,
    INPUT_STATE_BYTES,
    OBJECT_BYTES,
    POOL_LAYOUT,
    debugger_write_packets,
    parse_engine_control,
    parse_input_state,
    verify_controller_bindings,
)
from tools.m4_budget_evidence import (  # noqa: E402
    AUDIO_QUEUE_CAPACITY,
    AUDIO_SHADOW_BASE,
    AUDIO_SHADOW_BYTES,
    AUDIO_STATE_PLAYING,
    AUDIO_STATUS_ABI_VERSION,
    AUDIO_STATUS_FAULT_MASK,
    AUDIO_STATUS_FLAGS_HEALTHY,
    AUDIO_STATUS_SIGNATURE,
    DEBUG_ABI_SIZE,
    DEBUG_ABI_VERSION,
    DEBUG_BASE,
    DEBUG_BUDGET_BYTES,
    DEBUG_MAGIC,
    DMA_ORDINARY_WORD_LIMIT,
    DMA_QUEUE_BASE,
    DMA_QUEUE_CAPACITY,
    DMA_QUEUE_CONTROL_BYTES,
    EXPECTED_STATE_SYMBOLS,
    M2_HEALTHY_FLAGS,
    RENDERER_BYTES,
    RENDERER_FLAG_INITIALIZED,
    RENDERER_MAGIC,
    RENDERER_SCANLINE_PIXEL_LIMIT,
    RENDERER_SCANLINE_SPRITE_LIMIT,
    RENDERER_SPRITE_LIMIT,
    RENDERER_STATE_BASE,
    RENDERER_STATE_USED_BYTES,
    RENDERER_VERSION,
    Z80_VINT_STARTUP_ALLOWANCE,
    parse_audio_shadow,
    parse_debug_budget,
    parse_dma_queue_control,
    parse_renderer_state,
    symbol_layout_errors as m4_symbol_layout_errors,
)


REPORT_SCHEMA = "r94-m5-b03-budget-evidence-v3"
EVIDENCE_TYPE = "blastem_gdb_m5_b03_combined_room_budget_v3"
DIAGNOSTIC_SCHEMA = "r94-m5-b03-budget-handshake-diagnostic-v1"
DIAGNOSTIC_EVIDENCE_TYPE = (
    "blastem_gdb_m5_b03_frame_advance_handshake_diagnostic"
)
OVERRUN_ORIGIN_DIAGNOSTIC_SCHEMA = (
    "r94-m5-b03-budget-overrun-origin-diagnostic-v1"
)
OVERRUN_ORIGIN_DIAGNOSTIC_EVIDENCE_TYPE = (
    "blastem_gdb_m5_b03_vblank_overrun_origin_diagnostic"
)
OVERRUN_NATURAL_DIAGNOSTIC_SCHEMA = (
    "r94-m5-b03-budget-overrun-natural-control-diagnostic-v1"
)
OVERRUN_NATURAL_DIAGNOSTIC_EVIDENCE_TYPE = (
    "blastem_gdb_m5_b03_vblank_overrun_natural_pre_post_control"
)
OVERRUN_CONTINUOUS_DIAGNOSTIC_SCHEMA = (
    "r94-m5-b03-budget-overrun-continuous-control-diagnostic-v1"
)
OVERRUN_CONTINUOUS_DIAGNOSTIC_EVIDENCE_TYPE = (
    "blastem_gdb_m5_b03_vblank_overrun_near_continuous_control"
)
DIAGNOSTIC_DIRECTORY_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget"
)
SCHEMA_RELATIVE_PATH = (
    "tools/report_schemas/m5-budget-evidence.schema.json"
)
COLLECTOR_RELATIVE_PATH = "tools/m5_budget_evidence.py"
PROBE_RELATIVE_PATH = "tools/m5_budget_probe.ps1"
B03_COLLECTOR_RELATIVE_PATH = "tools/m5_b03_runtime_evidence.py"
TRACE_RELATIVE_PATH = (
    "artifacts/traces/m5-b03-budget-gdb-transcript.log"
)
REPORT_RELATIVE_PATH = "artifacts/reports/m5-b03-budget.json"
ROM_RELATIVE_PATH = "build/level-B03/recursion94-debug.bin"
SYMBOL_MAP_RELATIVE_PATH = "build/level-B03/recursion94-debug.sym"
EMULATOR_RELATIVE_PATH = ".toolcache/blastem/blastem.exe"
DEFAULT_CONFIG_RELATIVE_PATH = ".toolcache/blastem/default.cfg"
RUNTIME_REPORT_RELATIVE_PATH = "artifacts/reports/m5-b03-runtime.json"
RUNTIME_TRACE_RELATIVE_PATH = (
    "artifacts/traces/m5-b03-runtime-gdb-transcript.log"
)
RUNTIME_REPLAY_RELATIVE_PATH = (
    "artifacts/replays/m5-b03-live-replay-ram.bin"
)

# These are the root-approved canonical B03 prerequisite bindings.  A future
# candidate must deliberately update this collector rather than silently
# accepting an unrelated passing report.
PINNED_ROM_SHA256 = (
    "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
)
PINNED_SYMBOL_SHA256 = (
    "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
)
PINNED_RUNTIME_REPORT_SHA256 = (
    "605f9118498908e1c263688abca72d833e993b27d22d00a08676577e7842798f"
)
PINNED_RUNTIME_TRACE_SHA256 = (
    "472e90d2508f0afa8c10797bf3fc4dd58304982e8e93245c13ee353ce6d6ceb9"
)
PINNED_RUNTIME_REPLAY_SHA256 = (
    "01ec5267ec497ceafd81879d449ec05ea44aef78112b5945f198e4a3bf5370e5"
)

SESSION_COUNT = 2
EXPECTED_SESSION_IDS = ("budget_session_1", "budget_session_2")
EXPECTED_AUTHORITATIVE_STEPS = 1732
RESET_TOTALS = (720, 1440)
RESET_SERVICE_FRAMES = 2
EXPECTED_PHYSICAL_FRAMES = EXPECTED_AUTHORITATIVE_STEPS + 4
CHECKPOINT_HOLD_TOTALS = (*range(60, 1441, 60), 1500, 1560, 1620, 1680, 1732)
CHECKPOINT_OBSERVATION_TOTALS = frozenset(
    source + offset for source in CHECKPOINT_HOLD_TOTALS[:-1] for offset in (1, 2)
)
ZERO_DMA_TOTALS = frozenset((1, *RESET_TOTALS, *(source + 1 for source in CHECKPOINT_HOLD_TOTALS[:-1])))
M5_RENDERER_PRIVATE_BYTES = 0x26
CHECKPOINT_DMA_BYTES = DMA_QUEUE_CONTROL_BYTES + 5 * 12
INPUT_ALLOWED_MASK = 0x7F
ENGINE_FLAG_COLLISION = 0x0004
VDP_STATUS_DEFINED_MASK = 0x03FF
VDP_STATUS_CADENCE_ALLOWED = frozenset(
    {0x0208, 0x020C, 0x0228, 0x022C}
)
B03_STATIC_POOL_COUNTS = {
    0: 0,  # enemy
    2: len(b03.B03_DEVICES),
    3: 0,  # carryable
    4: len(b03.B03_HAZARDS),
    5: 0,  # boss
    6: 0,  # effect
}
FRAME_ADVANCE_KEY = {
    "name": "F8",
    "virtual_key": 0x77,
    "scan_code": 0x42,
    "extended": False,
}
FRAME_STOP_TIMEOUT_SECONDS = 5.0
PAUSE_OVERWRITE_TIMEOUT_SECONDS = 5.0
SET_TITLE_TIMEOUT_SECONDS = 1.0
OVERRUN_ORIGIN_DIAGNOSTIC_STEPS = 3
OVERRUN_ORIGIN_ARM_AFTER_TOTAL = 2
OVERRUN_COUNTER_ADDRESS = DEBUG_BASE + 0x32
OVERRUN_CONTINUOUS_OBSERVE_SECONDS = 1.5
OVERRUN_CONTINUOUS_MINIMUM_CLEAR_FRAMES = 12
OVERRUN_ORIGIN_SITES = {
    "dma_busy_timeout": {
        "address": 0x000014D6,
        "guard_window_address": 0x000014C6,
        "guard_window_hex": "303900C0000402400002671451C9FFF2",
        "compare_opcode_hex": "0C79FFFF00FFF032",
        "increment_address": 0x000014E0,
        "increment_opcode_hex": "527900FFF032",
        "register_low_words": {"d0": 0x0002, "d1": 0xFFFF},
        "meaning": (
            "DmaIssueVramTransfer exhausted its statically bounded "
            "VDP DMA-busy poll"
        ),
    },
    "vblank_exit": {
        "address": 0x0000172C,
        "guard_window_address": 0x0000171A,
        "guard_window_hex": "323900FFF01C024100086716024000086610",
        "compare_opcode_hex": "0C79FFFF00FFF032",
        "increment_address": 0x00001736,
        "increment_opcode_hex": "527900FFF032",
        "register_low_words": {"d0": 0x0000, "d1": 0x0008},
        "meaning": (
            "VBlankInterrupt observed VBlank on entry and no VBlank at "
            "its post-commit exit check"
        ),
    },
}
BLASTEM_SOURCE_NODE = "732f5689d4381862da283c420b68f37ac320c1d8"
BLASTEM_REVISION = BLASTEM_SOURCE_NODE[:12]
BLASTEM_VERSION = f"0.6.3-pre-{BLASTEM_REVISION}"
PINNED_EMULATOR_SHA256 = (
    "22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803"
)
PINNED_DEFAULT_CONFIG_SHA256 = (
    "27cf0b11a6e551f4427608ee32cc94018f82f9c655ba6eb5a5e69ecf8b1aba4e"
)
BLASTEM_SOURCE_HASHES = {
    "bindings.c": (
        "d6f2e4d5d7b38a1180654ed7354776a3f1d2879b1e454733687d906f3810a429"
    ),
    "blastem.c": (
        "dc88e812d70d1ed3baffa4f488e5e1af3df3fb47245103792474eb4ce83c946c"
    ),
    "gdb_remote.c": (
        "9eda587f9d11c6a8a624dc2f17c2293ba5600a8cfa6370639841b6fb6708cf92"
    ),
    "genesis.c": (
        "8b02bb53c0105c29af88008b674f3105a9d487a83fbc50f3d500579bb4c85364"
    ),
    "render_sdl.c": (
        "c969bb16e1d83c76602835a9dc2caa5dde6899b5810e3260de5a7588d795b85e"
    ),
    "vdp.c": (
        "21fc02bca5ad4f2e16e2be364195e9f02907cc68e0538aa6be8c8e436a9498e2"
    ),
}
FRAME_ADVANCE_VBLANK_BOUNDARY_FACT = {
    "source_node": BLASTEM_SOURCE_NODE,
    "vdp.c_sha256": BLASTEM_SOURCE_HASHES["vdp.c"],
    "genesis.c_sha256": BLASTEM_SOURCE_HASHES["genesis.c"],
    "vdp_anchor": "vcounter_increment_and_inactive_start_frame_increment",
    "genesis_anchor": (
        "sync_components_detects_frame_change_after_vdp_run_context_then_"
        "frame_advance_clears_pauses_and_requests_exit"
    ),
    "timing_consequence": (
        "ui.advance pauses at or after the VDP frame/VBlank boundary; it "
        "is not neutral for a 68000 V-int exit-time measurement"
    ),
}
F8_UI_ADVANCE_BINDING = "f8 ui.advance"
F7_UI_PAUSE_BINDING = "f7 ui.pause"
VOCABULARY_FINALIZE_EXPECTED = 0x00005568
MAIN_LOOP_WAIT_EXPECTED = 0x0000035E
M2_STARTUP_FLAGS = 0x000F
VOCAB_HEADER_BYTES = 0x28
VOCAB_MAGIC = 0x52393556
VOCAB_VERSION = 1
VOCAB_BYTES = 0x0200
VOCAB_FLAG_INITIALIZED = 0x0001
VOCAB_FLAG_BUDGET_EXCEEDED = 0x0010
VOCAB_CPU_UNIT_LIMIT = 4096
VOCAB_CPU_WORST_UNITS = 2904
VOCAB_ANCHOR_LIMIT = 2
VOCAB_BEAM_LIMIT = 8
RENDERER_FAULT_FLAGS = 0x029C
BOOT_STAGE_RUNNING = 4
ENGINE_STATE_ACTIVE = 1
ENGINE_STATE_TERMINAL = 2
PROGRAM_STABLE_TERMINAL = 0x0100
WORK_RAM_START = 0x00FF0000
WORK_RAM_END = 0x01000000
ENGINE_POOL_REGION_START = 0x00FF2000
ENGINE_POOL_REGION_END = 0x00FF6000

SCHEDULE_SHA256 = (
    "8fd39913ce2f12f83ed266caf80cbf9672e4de16ef7c06ccc74e84dc41193449",
    "6238befa2c454d23d5818563dc2955e3c78a2c260fc1421abea84a8eb6a8d3de",
    "23d3d734ce56e00631fe481e34e93f2dfad75f6c73b48e7908cde00e714097d4",
)
COMBINED_SCHEDULE_SHA256 = (
    "102056a59905e1e7e9fa3c2aa32226cf69d6af9b3f1fc1b847d98de1cefe0c2d"
)
CAPTURE_TICKS_SHA256 = (
    "c3d502e50cbe11754e6108048c735c906857b2451065d0b92bfceeb7c3311c63"
)
CHECKPOINT_KEYS_SHA256 = (
    "da0ba47997cf414aa6962a2474480dab9bffa079c8a79ca123ebe75f95252b46"
)

EXPECTED_CAPTURE_TICKS = {
    0: (67, 719),
    1: (55, 196, 719),
    2: (
        55,
        67,
        86,
        87,
        183,
        185,
        186,
        188,
        190,
        191,
        193,
        195,
        196,
        197,
        198,
        201,
        273,
        291,
        292,
    ),
}
EXPECTED_CHECKPOINT_KEYS = (
    "initial",
    "post_timeout_1",
    "post_timeout_2",
    "attempt_2_tick_55",
    "attempt_2_tick_67",
    "attempt_2_tick_86",
    "attempt_2_tick_87",
    "attempt_2_tick_183",
    "attempt_2_tick_185",
    "attempt_2_tick_186",
    "attempt_2_tick_188",
    "attempt_2_tick_190",
    "attempt_2_tick_191",
    "attempt_2_tick_193",
    "attempt_2_tick_195",
    "attempt_2_tick_196",
    "attempt_2_tick_197",
    "attempt_2_tick_198",
    "attempt_2_tick_201",
    "attempt_2_tick_273",
    "attempt_2_tick_291",
    "terminal",
)


def _capture_labels() -> tuple[str, ...]:
    labels: list[str] = ["initial"]
    for attempt in (0, 1):
        labels.extend(
            f"attempt_{attempt}_tick_{tick}"
            for tick in EXPECTED_CAPTURE_TICKS[attempt]
        )
        labels.append(f"post_timeout_{attempt + 1}")
    labels.extend(
        "terminal" if tick == 292 else f"attempt_2_tick_{tick}"
        for tick in EXPECTED_CAPTURE_TICKS[2]
    )
    return tuple(labels)


EXPECTED_CAPTURE_LABELS = _capture_labels()
MEANINGFUL_WORST_CASE_LABELS = (
    "attempt_0_tick_67",
    "attempt_1_tick_196",
    "post_timeout_2",
    "attempt_2_tick_87",
    "attempt_2_tick_197",
    "attempt_2_tick_198",
    "attempt_2_tick_201",
    "attempt_2_tick_273",
    "attempt_2_tick_291",
    "terminal",
)

REQUIRED_CHECK_NAMES = (
    "canonical_b03_runtime_prerequisite",
    "frozen_schedule_and_checkpoint_contract",
    "two_fresh_owned_route_sessions",
    "exact_read_only_frame_advance_post_drive",
    "stable_route_aligned_observations",
    "runtime_abi_and_clock_health",
    "all_cumulative_fault_counters_zero",
    "bounded_pool_and_vocabulary_budget",
    "renderer_and_dma_capacities_in_bounds",
    "exact_checkpoint_holds_and_recovery",
    "audio_z80_health_and_cross_clock",
    "meaningful_worst_case_and_terminal_coverage",
    "exact_two_session_budget_signatures",
    "exact_hash_bindings_and_no_drift",
)

LIMITATIONS = [
    (
        "The pass covers only two fresh NTSC Debug-ROM B03 fixture sessions "
        "executing the exact canonical 720+720+292 authoritative-step route."
    ),
    (
        "One permanent POST breakpoint stops every authoritative step. "
        "Host-only title sentinels and BlastEm's internal frame-advance pause "
        "edge prove one VDP frame per ordinary step plus exactly two reset-"
        "service frames at each timeout; the measurements do not profile "
        "CPU cycles or quantify spare VBlank time."
    ),
    (
        "Checkpoint reads occur at the post-gameplay main-loop boundary. "
        "Each all-frame cadence row is one stable stopped read; the 27 "
        "budget checkpoints use immediate byte-for-byte double reads. "
        "Renderer telemetry describes the most recently completed renderer "
        "build, which can be the preceding authoritative step."
    ),
    (
        "No visual-quality, PAL double-step, save, physical-hardware, full "
        "campaign, or Release-ROM performance claim is made."
    ),
    (
        "No unmeasured Release-ROM equivalence is inferred from this Debug "
        "fixture evidence."
    ),
]

EXPECTED_EXTRA_SYMBOLS = {
    "VocabularyFinalizeStage": VOCABULARY_FINALIZE_EXPECTED,
    "MainLoopWaitForVBlank": MAIN_LOOP_WAIT_EXPECTED,
    "RENDERER_LAST_CHECKPOINT_COUNT": RENDERER_STATE_BASE + 0x24,
    "RENDERER_PRIVATE_USED_END": RENDERER_STATE_BASE + 0x26,
    "RENDERER_RESULT_CHECKPOINT_HELD": 4,
    "VOCAB_STATE_BASE": b03.VOCAB_STATE_BASE,
    "VOCAB_MAGIC": b03.VOCAB_STATE_BASE + 0x00,
    "VOCAB_VERSION": b03.VOCAB_STATE_BASE + 0x04,
    "VOCAB_BYTES": b03.VOCAB_STATE_BASE + 0x06,
    "VOCAB_FLAGS": b03.VOCAB_STATE_BASE + 0x0A,
    "VOCAB_OVERFLOW_FLAGS": b03.VOCAB_STATE_BASE + 0x0E,
    "VOCAB_UPDATE_UNITS": b03.VOCAB_STATE_BASE + 0x10,
    "VOCAB_UPDATE_PEAK": b03.VOCAB_STATE_BASE + 0x12,
    "VOCAB_ANCHOR_COUNT": b03.VOCAB_STATE_BASE + 0x22,
    "VOCAB_BEAM_COUNT": b03.VOCAB_STATE_BASE + 0x24,
    "VOCAB_ACTIVE_ENEMY_COUNT": b03.VOCAB_STATE_BASE + 0x25,
    "VOCAB_ACTIVE_PROJECTILE_COUNT": b03.VOCAB_STATE_BASE + 0x26,
}
for _pool_type, _name, _address, _capacity in POOL_LAYOUT:
    _symbol_stem = (
        "BOSS_COMPONENT" if _name == "boss" else _name.upper()
    )
    EXPECTED_EXTRA_SYMBOLS[f"{_symbol_stem}_POOL"] = _address
    EXPECTED_EXTRA_SYMBOLS[f"{_symbol_stem}_CAPACITY"] = _capacity

REQUIRED_SYMBOLS = tuple(
    dict.fromkeys(
        (
            *b03.REQUIRED_SYMBOLS,
            *EXPECTED_STATE_SYMBOLS,
            *EXPECTED_EXTRA_SYMBOLS,
        )
    )
)

# Every field returned by the three public cadence parsers is deliberately
# classified here.  cadence_row_errors checks this key set exactly, so a
# future parser field cannot silently become unaudited.  The category strings
# are documentation for the corresponding fail-closed checks below.
CADENCE_PARSED_FIELD_CONTRACT = {
    "engine": {
        "magic": "exact_abi",
        "magic_ascii": "exact_abi",
        "abi_version": "exact_abi",
        "control_bytes": "exact_abi",
        "state": "exact_route",
        "flags": "exact_legal_flag_domain",
        "encounter": "exact_b03_fixture",
        "attempt": "exact_route",
        "tick": "exact_route",
        "terminal_reason": "exact_route",
        "last_error": "zero_fault",
        "overflow_flags": "zero_fault",
        "projection_length": "exact_b03_fixture",
        "state_crc": "nonzero_checkpoint_signature",
        "checkpoint_tick": "exact_route",
        "checkpoint_count": "exact_route",
        "input_held": "exact_route",
        "input_pressed": "exact_route",
        "input_released": "exact_route",
        "input_previous": "exact_route",
        "facing_x": "exact_route",
        "active_actor_count": "exact_route_and_capacity",
        "room_left": "exact_b03_fixture",
        "room_top": "exact_b03_fixture",
        "room_right": "exact_b03_fixture",
        "room_bottom": "exact_b03_fixture",
        "spawn_x": "exact_b03_fixture",
        "spawn_y": "exact_b03_fixture",
        "rect_count": "exact_b03_fixture",
        "object_init_count": "exact_b03_fixture",
        "pool_counts": "bounded_and_b03_static_owners",
        "pool_high_water": "bounded_and_b03_static_owners",
    },
    "debug": {
        "magic": "exact_abi",
        "magic_ascii": "exact_abi",
        "abi_version": "exact_abi",
        "abi_size": "exact_abi",
        "sequence": "exact_route_clock",
        "heartbeat": "exact_route_clock",
        "frame_counter": "exact_route_clock",
        "frame_token": "exact_stopped_boundary",
        "fatal_code": "zero_fault",
        "boot_stage": "exact_running_stage",
        "hardware_flags": "zero_fault",
        "last_vdp_status": "exact_defined_lower_10_open_bus_ignored",
        "sprite_count": "bounded_capacity",
        "sprite_scanline_max": "bounded_capacity",
        "dma_words_frame": "bounded_capacity",
        "dma_words_max": "bounded_high_water",
        "dma_queue_depth": "bounded_capacity",
        "dma_queue_high_water": "bounded_high_water",
        "dma_overflow_count": "zero_fault",
        "vblank_overrun_count": "zero_fault",
        "audio_state": "exact_playing_state",
        "z80_heartbeat": "bounded_invariant_cross_clock",
        "audio_command_count": "exact_route",
        "m2_flags": "exact_healthy_flags",
        "z80_timeout_count": "zero_fault",
        "dma_deferred_count": "zero_fault",
        "dma_required_drop_count": "zero_fault",
        "dma_invalid_count": "zero_fault",
    },
    "input": {
        "held": "exact_route",
        "pressed": "exact_route",
        "released": "exact_route",
        "raw_high": "exact_three_button_electrical_sample",
        "raw_low": "exact_three_button_electrical_sample",
        "connected": "exact_boolean_connected",
        "start_held": "exact_boolean_not_held",
    },
}


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def _public(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if not key.startswith("_")
    }


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def schedule_contract() -> dict[str, Any]:
    """Fail closed if the imported canonical route definitions drift."""
    schedules = (
        b03.ATTEMPT_0_INPUTS,
        b03.ATTEMPT_1_INPUTS,
        b03.ATTEMPT_2_INPUTS,
    )
    lengths = tuple(len(schedule) for schedule in schedules)
    digests = tuple(_sha256_bytes(schedule) for schedule in schedules)
    combined = _sha256_bytes(b"".join(schedules))
    capture_ticks = {
        str(key): list(value)
        for key, value in b03.ATTEMPT_CAPTURE_TICKS.items()
    }
    capture_digest = _sha256_bytes(_stable_json(capture_ticks))
    checkpoint_digest = _sha256_bytes(
        json.dumps(
            list(b03.CHECKPOINT_KEYS),
            separators=(",", ":"),
        ).encode("ascii")
    )
    passed = (
        lengths == (720, 720, 292)
        and digests == SCHEDULE_SHA256
        and combined == COMBINED_SCHEDULE_SHA256
        and b03.ATTEMPT_CAPTURE_TICKS == EXPECTED_CAPTURE_TICKS
        and capture_digest == CAPTURE_TICKS_SHA256
        and b03.CHECKPOINT_KEYS == EXPECTED_CHECKPOINT_KEYS
        and checkpoint_digest == CHECKPOINT_KEYS_SHA256
    )
    result = {
        "status": "pass" if passed else "fail",
        "attempt_lengths": list(lengths),
        "attempt_sha256": list(digests),
        "combined_sha256": combined,
        "capture_ticks": capture_ticks,
        "capture_ticks_sha256": capture_digest,
        "checkpoint_keys": list(b03.CHECKPOINT_KEYS),
        "checkpoint_keys_sha256": checkpoint_digest,
        "expected_capture_labels": list(EXPECTED_CAPTURE_LABELS),
        "drive_implementation": {
            "provider": COLLECTOR_RELATIVE_PATH,
            "post": "MainLoopPostGameplayStep",
            "terminal_pre_post": "VocabularyFinalizeStage",
            "terminal_same_frame_guard": "MainLoopWaitForVBlank",
            "audit": "audit_frame_advance_transcript",
            "protocol": (
                "origin prime -> fresh PAUSED; while responsive set/read "
                "unique sentinel -> one F8 ui.advance (plus exactly two "
                "sentinel-verified service advances only at totals720/1440) -> exact POST -> "
                "read-only capture -> c -> same-frame PAUSED overwrite; "
                "terminal POST -> exact token-0 MainLoopWaitForVBlank guard"
            ),
            "authoritative_steps": EXPECTED_AUTHORITATIVE_STEPS,
            "physical_frames": EXPECTED_PHYSICAL_FRAMES,
            "sentinel_handshakes": EXPECTED_PHYSICAL_FRAMES,
            "ui_advance_pulses": EXPECTED_PHYSICAL_FRAMES + 1,
            "permanent_post_breakpoint": True,
            "guest_memory_register_pc_writes": False,
        },
    }
    if not passed:
        raise EvidenceFailure(
            "canonical B03 schedule/checkpoint contract drifted: "
            f"{json.dumps(result, sort_keys=True)}"
        )
    return result


def frame_advance_source_contract(default_config: Path) -> dict[str, Any]:
    """Bind the pinned UI/frame-end semantics and exact default bindings."""
    text = default_config.read_text(encoding="utf-8", errors="strict")
    normalized = text.replace("\r\n", "\n")
    f8_lines = re.findall(
        r"(?mi)^\s*f8\s+ui\.advance\s*$", normalized
    )
    f7_lines = re.findall(
        r"(?mi)^\s*f7\s+ui\.pause\s*$", normalized
    )
    passed = (
        sha256_file(default_config) == PINNED_DEFAULT_CONFIG_SHA256
        and len(f8_lines) == 1
        and len(f7_lines) == 1
    )
    return {
        "status": "pass" if passed else "fail",
        "blastem_version": BLASTEM_VERSION,
        "source_node": BLASTEM_SOURCE_NODE,
        "source_sha256": dict(BLASTEM_SOURCE_HASHES),
        "default_config_sha256": sha256_file(default_config),
        "bindings": {
            "frame_advance": F8_UI_ADVANCE_BINDING,
            "pause_forbidden_during_drive": F7_UI_PAUSE_BINDING,
            "frame_advance_matches": len(f8_lines),
            "pause_matches": len(f7_lines),
        },
        "source_semantics": {
            "ui_action_edge": "KEYUP",
            "ui_advance": (
                "sets frame_advance boolean to 1 and paused to 0"
            ),
            "frame_end": (
                "clears frame_advance, sets paused to 1, requests exit, "
                "and prevents the next frame/VBlank dispatch until the "
                "following ui.advance"
            ),
            "pause_caption": (
                "render_update_display writes '<caption> - PAUSED' only "
                "after the internal non-paused to paused edge"
            ),
            "gdb_stop": (
                "permanent breakpoint callback blocks emulation until c"
            ),
            "queue_bound": (
                "frame_advance is a boolean and cannot accumulate frames"
            ),
            "vdp_status_upper_bits": (
                "bits 15-10 are 68000 prefetch open bus and are ignored"
            ),
            "vdp_status_defined_lower_10": (
                "exactly 0x0208/0x020C/0x0228/0x022C at cadence stops: "
                "FIFO empty and VBlank, with only the instantaneous "
                "HBlank bit and informational sprite-collision latch "
                "allowed to vary"
            ),
        },
    }


def expected_physical_frame(total: int) -> int:
    return total + RESET_SERVICE_FRAMES * sum(total >= reset for reset in RESET_TOTALS)


def expected_audio_shadow_age(total: int) -> int:
    # AudioPollStatus runs before gameplay; reset service does not poll again.
    return RESET_SERVICE_FRAMES if total in RESET_TOTALS else 0


def expected_advance_events() -> list[tuple[int, str]]:
    events = [(1, "origin_prime")]
    for total in range(1, EXPECTED_AUTHORITATIVE_STEPS + 1):
        events.append((len(events) + 1, f"authoritative_total_{total}"))
        if total in RESET_TOTALS:
            for service in range(1, RESET_SERVICE_FRAMES + 1):
                events.append((len(events) + 1, f"reset_{total}_service_{service}"))
    return events


def expected_route_for_total(total: int) -> dict[str, int]:
    if not 1 <= total <= EXPECTED_AUTHORITATIVE_STEPS:
        raise ValueError(
            "authoritative total outside "
            f"1..{EXPECTED_AUTHORITATIVE_STEPS}: {total}"
        )
    if total < 720:
        state, attempt, tick = ENGINE_STATE_ACTIVE, 0, total
    elif total == 720:
        state, attempt, tick = ENGINE_STATE_ACTIVE, 1, 0
    elif total < 1440:
        state, attempt, tick = ENGINE_STATE_ACTIVE, 1, total - 720
    elif total == 1440:
        state, attempt, tick = ENGINE_STATE_ACTIVE, 2, 0
    elif total < EXPECTED_AUTHORITATIVE_STEPS:
        state, attempt, tick = ENGINE_STATE_ACTIVE, 2, total - 1440
    else:
        state, attempt, tick = ENGINE_STATE_TERMINAL, 2, 292
    return {
        "state": state,
        "attempt": attempt,
        "tick": tick,
    }


def expected_input_for_total(total: int) -> int:
    if not 1 <= total <= EXPECTED_AUTHORITATIVE_STEPS:
        raise ValueError(
            "authoritative total outside "
            f"1..{EXPECTED_AUTHORITATIVE_STEPS}: {total}"
        )
    if total <= 720:
        return b03.ATTEMPT_0_INPUTS[total - 1]
    if total <= 1440:
        return b03.ATTEMPT_1_INPUTS[total - 721]
    return b03.ATTEMPT_2_INPUTS[total - 1441]


def _attempt_schedule(attempt: int) -> bytes:
    schedules = (
        b03.ATTEMPT_0_INPUTS,
        b03.ATTEMPT_1_INPUTS,
        b03.ATTEMPT_2_INPUTS,
    )
    if not 0 <= attempt < len(schedules):
        raise ValueError(f"invalid B03 attempt {attempt}")
    return schedules[attempt]


def expected_engine_input_state(total: int) -> dict[str, int]:
    """Return exact post-step engine input/facing state for the route."""
    route = expected_route_for_total(total)
    attempt = route["attempt"]
    tick = route["tick"]
    if tick == 0:
        return {
            "held": 0,
            "pressed": 0,
            "released": 0,
            "previous": 0,
            "facing_x": 1,
        }
    schedule = _attempt_schedule(attempt)
    held = schedule[tick - 1]
    previous = schedule[tick - 2] if tick > 1 else 0
    facing_x = 1
    for value in schedule[:tick]:
        if value & b03.INPUT_LEFT:
            facing_x = -1
        elif value & b03.INPUT_RIGHT:
            facing_x = 1
    return {
        "held": held,
        "pressed": held & (~previous & INPUT_ALLOWED_MASK),
        "released": previous & (~held & INPUT_ALLOWED_MASK),
        "previous": previous,
        "facing_x": facing_x,
    }


def _three_button_raw_samples(held: int) -> tuple[int, int]:
    """Encode the exact connected standard-pad TH-high/TH-low samples."""
    high = 0x7F & ~(held & 0x0F)
    if held & b03.INPUT_B:
        high &= ~0x10
    if held & b03.INPUT_C:
        high &= ~0x20
    low = 0x33 & ~(held & (b03.INPUT_UP | b03.INPUT_DOWN))
    if held & b03.INPUT_A:
        low &= ~0x10
    return high, low


def expected_controller_input_state(total: int) -> dict[str, int]:
    """Return the exact VBlank-polled port state at a cadence stop."""
    held = expected_input_for_total(total)
    previous = (
        held if total in RESET_TOTALS
        else expected_input_for_total(total - 1) if total > 1 else 0
    )
    raw_high, raw_low = _three_button_raw_samples(held)
    return {
        "held": held,
        "pressed": held & (~previous & INPUT_ALLOWED_MASK),
        "released": previous & (~held & INPUT_ALLOWED_MASK),
        "raw_high": raw_high,
        "raw_low": raw_low,
        "connected": 1,
        "start_held": 0,
    }


def expected_checkpoint_for_total(total: int) -> tuple[int, int]:
    """Return exact published projection tick/count after this route step."""
    route = expected_route_for_total(total)
    tick = route["tick"]
    if tick == 0:
        return 0, 1
    if route["state"] == ENGINE_STATE_TERMINAL:
        # Initial publication, each prior 60-tick publication, then the
        # distinct terminal publication at tick 292.
        return tick, 2 + (tick - 1) // ENGINE_CHECKPOINT_INTERVAL
    checkpoint_tick = (
        tick // ENGINE_CHECKPOINT_INTERVAL
    ) * ENGINE_CHECKPOINT_INTERVAL
    return checkpoint_tick, 1 + tick // ENGINE_CHECKPOINT_INTERVAL


def expected_audio_for_total(total: int) -> dict[str, int]:
    if not 1 <= total <= EXPECTED_AUTHORITATIVE_STEPS:
        raise ValueError(
            "audio total outside "
            f"1..{EXPECTED_AUTHORITATIVE_STEPS}: {total}"
        )
    # AudioPostBootSmokeOnce first mirrors the Z80 status, then publishes the
    # one-shot command.  The processed-command mirror therefore remains zero
    # at POST total 1 and becomes one at the following VBlank.
    if total == 1:
        command_count, active_layers, last_command = 0, 0x00, 0x00
    elif total <= 720:
        command_count, active_layers, last_command = 1, 0x01, 0x03
    elif total <= 1440:
        command_count, active_layers, last_command = 2, 0x03, 0x02
    else:
        command_count, active_layers, last_command = 3, 0x07, 0x02
    return {
        "command_count": command_count,
        "queue_read_sequence": command_count,
        "active_layers": active_layers,
        "last_command": last_command,
    }


def published_limits_contract() -> dict[str, Any]:
    return {
        "renderer_sprites": RENDERER_SPRITE_LIMIT,
        "renderer_scanline_sprites": RENDERER_SCANLINE_SPRITE_LIMIT,
        "renderer_scanline_pixels": RENDERER_SCANLINE_PIXEL_LIMIT,
        "dma_words_per_ordinary_frame": DMA_ORDINARY_WORD_LIMIT,
        "dma_queue_entries": DMA_QUEUE_CAPACITY,
        "vocabulary_update_units": VOCAB_CPU_UNIT_LIMIT,
        "vocabulary_worst_units": VOCAB_CPU_WORST_UNITS,
        "fixed_pool_capacities": {
            name: capacity
            for _pool_type, name, _address, capacity in POOL_LAYOUT
        },
        "z80_startup_allowance_vints": Z80_VINT_STARTUP_ALLOWANCE,
    }


def symbol_layout_errors(symbols: dict[str, int]) -> list[str]:
    errors = m4_symbol_layout_errors(symbols)
    for name, expected in EXPECTED_EXTRA_SYMBOLS.items():
        observed = symbols.get(name)
        if observed is None:
            errors.append(f"{name} missing")
        elif observed != expected:
            errors.append(
                f"{name} is ${observed:08X}; ${expected:08X} required"
            )
    return errors


def parse_vocabulary_budget(raw: bytes) -> dict[str, Any]:
    if len(raw) != VOCAB_HEADER_BYTES:
        raise ValueError(
            f"vocabulary header is {len(raw)} bytes; "
            f"{VOCAB_HEADER_BYTES} required"
        )
    return {
        "magic": _u32(raw, 0x00),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 0x04),
        "bytes": _u16(raw, 0x06),
        "projection_length": _u16(raw, 0x08),
        "flags": _u16(raw, 0x0A),
        "result": _u16(raw, 0x0C),
        "overflow_flags": _u16(raw, 0x0E),
        "update_units": _u16(raw, 0x10),
        "update_peak": _u16(raw, 0x12),
        "interaction_result": _u16(raw, 0x14),
        "stage_id": _u16(raw, 0x16),
        "stage_flags": _u16(raw, 0x18),
        "stage_attempts": _u16(raw, 0x1A),
        "stage_final_tick": _u16(raw, 0x1C),
        "stage_optional_metric": _u16(raw, 0x1E),
        "stage_echo_count": raw[0x20],
        "stage_rank": raw[0x21],
        "anchor_count": raw[0x22],
        "anchor_capacity": raw[0x23],
        "beam_count": raw[0x24],
        "active_enemy_count": raw[0x25],
        "active_projectile_count": raw[0x26],
        "header_reserved": raw[0x27],
    }


def parse_pool_budget(
    raw: bytes,
    *,
    pool_type: int,
    name: str,
    address: int,
    capacity: int,
) -> dict[str, Any]:
    expected = capacity * OBJECT_BYTES
    if len(raw) != expected:
        raise ValueError(
            f"{name} pool is {len(raw)} bytes; {expected} required"
        )
    active_slots: list[int] = []
    active_ids: list[int] = []
    invalid_active: list[dict[str, int]] = []
    for slot in range(capacity):
        offset = slot * OBJECT_BYTES
        active = raw[offset]
        if active not in (0, 1):
            invalid_active.append({"slot": slot, "value": active})
        if active == 1:
            active_slots.append(slot)
            active_ids.append(raw[offset + 1])
    return {
        "pool_type": pool_type,
        "name": name,
        "address": f"{address:08X}",
        "capacity": capacity,
        "record_bytes": OBJECT_BYTES,
        "active_count": len(active_slots),
        "active_slots": active_slots,
        "active_logical_ids": active_ids,
        "invalid_active_values": invalid_active,
    }


BlockParser = Callable[[bytes], dict[str, Any]]


def _pool_parser(
    pool_type: int,
    name: str,
    address: int,
    capacity: int,
) -> BlockParser:
    def parse(raw: bytes) -> dict[str, Any]:
        return parse_pool_budget(
            raw,
            pool_type=pool_type,
            name=name,
            address=address,
            capacity=capacity,
        )

    return parse


BASE_BLOCK_SPECS: tuple[
    tuple[str, str, int, int, BlockParser], ...
] = (
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
    (
        "vocabulary_header",
        "vocabulary",
        b03.VOCAB_STATE_BASE,
        VOCAB_HEADER_BYTES,
        parse_vocabulary_budget,
    ),
)
POOL_BLOCK_SPECS: tuple[
    tuple[str, str, int, int, BlockParser], ...
] = tuple(
    (
        f"pool_{name}",
        name,
        address,
        capacity * OBJECT_BYTES,
        _pool_parser(pool_type, name, address, capacity),
    )
    for pool_type, name, address, capacity in POOL_LAYOUT
)
BLOCK_SPECS = (*BASE_BLOCK_SPECS, *POOL_BLOCK_SPECS)

# Exact B03 scenario read plan used by m5_b03_runtime_evidence._capture_snapshot.
# Keeping it explicit makes the trace grammar independently auditable instead
# of accepting arbitrary syntactically valid ``m`` traffic.
SCENARIO_BLOCK_SPECS: tuple[tuple[str, str, int, int], ...] = (
    ("engine_control", "_control_bytes", 0x00FF1400, 0x0050),
    ("gameplay_control", "_gameplay_bytes", 0x00FF1500, 0x0024),
    ("vocabulary_state", "_vocabulary_bytes", 0x00FF1600, 0x0200),
    ("encounter_descriptor", "_descriptor_bytes", 0x00FF7000, 0x0020),
    ("canonical_rectangles", "_rectangle_bytes", 0x00FF7020, 0x0060),
    ("device_pool", "_device_bytes", 0x00FF2600, 0x0300),
    ("projectile_pool", "_projectile_bytes", 0x00FF2200, 0x0300),
    ("hazard_pool", "_hazard_bytes", 0x00FF2B00, 0x0180),
    ("actor_slots", "_actor_bytes", 0x00FF1800, 0x00E0),
    ("input_state", "_input_bytes", 0x00FF9800, 0x0007),
    ("replay_timeline", "_replay_bytes", 0x00FF0000, 0x1400),
    ("campaign_projection", "_projection_bytes", 0x00FF6000, 0x0F36),
)


def _trace_memory_marker(
    transcript: list[str],
    *,
    edge: str,
    kind: str,
    identity: str,
) -> None:
    if edge not in {"begin", "end"}:
        raise ValueError(f"invalid trace-memory edge {edge!r}")
    if not re.fullmatch(r"[a-z0-9_]+", kind):
        raise ValueError(f"invalid trace-memory kind {kind!r}")
    if not re.fullmatch(r"[a-z0-9_]+", identity):
        raise ValueError(f"invalid trace-memory identity {identity!r}")
    transcript.append(
        f"# memory.{edge} kind={kind} id={identity}"
    )


def _scenario_evidence_from_snapshot(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    blocks: dict[str, Any] = {}
    raw_bundle = bytearray()
    for name, private_key, address, size in SCENARIO_BLOCK_SPECS:
        raw = snapshot.get(private_key)
        if not isinstance(raw, bytes) or len(raw) != size:
            raise EvidenceFailure(
                f"{snapshot.get('label')} scenario block {name} is not "
                f"the required {size} bytes"
            )
        blocks[name] = _block_evidence(address, raw)
        raw_bundle.extend(raw)
    return {
        "label": snapshot["label"],
        "blocks": blocks,
        "raw_bundle_sha256": _sha256_bytes(bytes(raw_bundle)),
    }


def _block_evidence(address: int, raw: bytes) -> dict[str, Any]:
    return {
        "address": f"{address:08X}",
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "raw_hex": raw.hex().upper(),
    }


def _route_position(label: str) -> tuple[int, int, int]:
    if label == "initial":
        return (ENGINE_STATE_ACTIVE, 0, 0)
    if label == "post_timeout_1":
        return (ENGINE_STATE_ACTIVE, 1, 0)
    if label == "post_timeout_2":
        return (ENGINE_STATE_ACTIVE, 2, 0)
    if label == "terminal":
        return (ENGINE_STATE_TERMINAL, 2, 292)
    match = re.fullmatch(r"attempt_([0-2])_tick_([0-9]+)", label)
    if not match:
        raise ValueError(f"unknown budget checkpoint label {label!r}")
    return (
        ENGINE_STATE_ACTIVE,
        int(match.group(1)),
        int(match.group(2)),
    )


def _authoritative_total(label: str) -> int:
    _state, attempt, tick = _route_position(label)
    return attempt * 720 + tick


def build_observation_from_raw(
    *,
    label: str,
    raw_by_name: dict[str, bytes],
    second_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build one self-authenticating observation from exact raw blocks."""
    expected_names = {spec[0] for spec in BLOCK_SPECS}
    if set(raw_by_name) != expected_names:
        raise ValueError(
            "budget raw block set mismatch: "
            f"{sorted(raw_by_name)} != {sorted(expected_names)}"
        )
    observation: dict[str, Any] = {
        "label": label,
        "route": {
            "expected_state": _route_position(label)[0],
            "expected_attempt": _route_position(label)[1],
            "expected_tick": _route_position(label)[2],
            "authoritative_total": _authoritative_total(label),
        },
        "pools": {},
        "blocks": {},
    }
    first_hashes: dict[str, str] = {}
    for block_name, key, address, size, parser in BLOCK_SPECS:
        raw = raw_by_name[block_name]
        if len(raw) != size:
            raise ValueError(
                f"{block_name} is {len(raw)} bytes; {size} required"
            )
        parsed = parser(raw)
        block = _block_evidence(address, raw)
        observation["blocks"][block_name] = block
        first_hashes[block_name] = block["sha256"]
        if block_name.startswith("pool_"):
            observation["pools"][key] = parsed
        else:
            observation[key] = parsed
    if second_hashes is None:
        second_hashes = dict(first_hashes)
    observation["cross_clock"] = {
        "m68k_frame_counter": observation["debug"]["frame_counter"],
        "z80_vint_heartbeat": observation["debug"]["z80_heartbeat"],
        "z80_vint_excess": (
            observation["debug"]["z80_heartbeat"]
            - observation["debug"]["frame_counter"]
            + expected_audio_shadow_age(_authoritative_total(label))
        ),
        "z80_shadow_age_vblanks": expected_audio_shadow_age(_authoritative_total(label)),
        "startup_allowance_vints": Z80_VINT_STARTUP_ALLOWANCE,
    }
    observation["read_consistency"] = {
        "status": (
            "pass" if first_hashes == second_hashes else "fail"
        ),
        "first_block_sha256": first_hashes,
        "second_block_sha256": second_hashes,
        "cpu_state": (
            "stopped at canonical B03 initial VBlank or "
            "permanent MainLoopPostGameplayStep main-side boundary"
        ),
    }
    return observation


def _read_raw_blocks(remote: GdbRemote) -> dict[str, bytes]:
    return {
        block_name: b03._read_region(remote, address, size)
        for block_name, _key, address, size, _parser in BLOCK_SPECS
    }


def capture_stopped_budget_observation(
    remote: GdbRemote,
    *,
    label: str,
) -> dict[str, Any]:
    _trace_memory_marker(
        remote.transcript,
        edge="begin",
        kind="budget_first",
        identity=label,
    )
    first = _read_raw_blocks(remote)
    _trace_memory_marker(
        remote.transcript,
        edge="end",
        kind="budget_first",
        identity=label,
    )
    _trace_memory_marker(
        remote.transcript,
        edge="begin",
        kind="budget_second",
        identity=label,
    )
    second = _read_raw_blocks(remote)
    _trace_memory_marker(
        remote.transcript,
        edge="end",
        kind="budget_second",
        identity=label,
    )
    second_hashes = {
        name: _sha256_bytes(raw) for name, raw in second.items()
    }
    return build_observation_from_raw(
        label=label,
        raw_by_name=first,
        second_hashes=second_hashes,
    )


def _capture_combined_snapshot(
    remote: GdbRemote,
    *,
    label: str,
    symbols: dict[str, int],
) -> dict[str, Any]:
    _trace_memory_marker(
        remote.transcript,
        edge="begin",
        kind="scenario",
        identity=label,
    )
    snapshot = b03._capture_snapshot(
        remote, label=label, symbols=symbols
    )
    _trace_memory_marker(
        remote.transcript,
        edge="end",
        kind="scenario",
        identity=label,
    )
    snapshot["_budget_observation"] = (
        capture_stopped_budget_observation(remote, label=label)
    )
    return snapshot


def build_cadence_row_from_raw(
    *,
    authoritative_total: int,
    engine_raw: bytes,
    debug_raw: bytes,
    input_raw: bytes,
    checkpoint_raw: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    expected = expected_route_for_total(authoritative_total)
    desired = expected_input_for_total(authoritative_total)
    engine = parse_engine_control(engine_raw)
    debug = parse_debug_budget(debug_raw)
    input_state = parse_input_state(input_raw)
    result = {
        "authoritative_total": authoritative_total,
        "expected": {
            **expected,
            "input": desired,
        },
        "input_proof": (
            "committed_replay"
            if authoritative_total in {720, 1440}
            else "engine_and_input_state"
        ),
        "observed": {
            "engine": engine,
            "debug": debug,
            "input": input_state,
            "z80_vint_excess": (
                debug["z80_heartbeat"] - debug["frame_counter"]
                + expected_audio_shadow_age(authoritative_total)
            ),
            "z80_shadow_age_vblanks": expected_audio_shadow_age(authoritative_total),
        },
        "blocks": {
            "engine_control": _block_evidence(
                ENGINE_CONTROL_BASE, engine_raw
            ),
            "debug_budget": _block_evidence(DEBUG_BASE, debug_raw),
            "input_state": _block_evidence(INPUT_STATE_BASE, input_raw),
        },
    }
    if checkpoint_raw is not None:
        renderer_raw = checkpoint_raw["renderer_private"]
        dma_raw = checkpoint_raw["dma_recovery"]
        if len(renderer_raw) != M5_RENDERER_PRIVATE_BYTES or len(dma_raw) != CHECKPOINT_DMA_BYTES:
            raise ValueError("checkpoint private read size mismatch")
        result["checkpoint_renderer"] = {
            "renderer": {
                **parse_renderer_state(renderer_raw[:RENDERER_STATE_USED_BYTES]),
                "last_checkpoint_count": _u16(renderer_raw, 0x24),
            },
            "dma_queue": parse_dma_queue_control(dma_raw[:DMA_QUEUE_CONTROL_BYTES]),
            "blocks": {
                "renderer_private": _block_evidence(RENDERER_STATE_BASE, renderer_raw),
                "dma_recovery": _block_evidence(DMA_QUEUE_BASE, dma_raw),
            },
        }
    return result


def capture_cadence_row(
    remote: GdbRemote,
    *,
    authoritative_total: int,
    trace_kind: str = "cadence",
) -> dict[str, Any]:
    if trace_kind not in {"cadence", "terminal_wait"}:
        raise ValueError(f"invalid cadence trace kind {trace_kind!r}")
    identity = f"{authoritative_total:04d}"
    _trace_memory_marker(
        remote.transcript,
        edge="begin",
        kind=trace_kind,
        identity=identity,
    )
    engine_raw = b03._read_region(
        remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
    )
    debug_raw = b03._read_region(
        remote, DEBUG_BASE, DEBUG_BUDGET_BYTES
    )
    input_raw = b03._read_region(
        remote, INPUT_STATE_BASE, INPUT_STATE_BYTES
    )
    _trace_memory_marker(
        remote.transcript,
        edge="end",
        kind=trace_kind,
        identity=identity,
    )
    checkpoint_raw = None
    if authoritative_total in CHECKPOINT_OBSERVATION_TOTALS or trace_kind == "terminal_wait":
        checkpoint_id = "terminal" if trace_kind == "terminal_wait" else identity
        _trace_memory_marker(remote.transcript, edge="begin", kind="checkpoint_renderer", identity=checkpoint_id)
        checkpoint_raw = {
            name: b03._read_region(remote, address, size)
            for name, address, size in _trace_read_plan("checkpoint_renderer")
        }
        _trace_memory_marker(remote.transcript, edge="end", kind="checkpoint_renderer", identity=checkpoint_id)
    return build_cadence_row_from_raw(
        authoritative_total=authoritative_total,
        engine_raw=engine_raw,
        debug_raw=debug_raw,
        input_raw=input_raw,
        checkpoint_raw=checkpoint_raw,
    )


def build_natural_boundary_from_raw(
    *,
    phase: str,
    authoritative_total: int,
    d0: int | None,
    engine_raw: bytes,
    input_raw: bytes,
) -> dict[str, Any]:
    if phase not in {"pre", "post"}:
        raise ValueError(f"invalid natural boundary phase {phase!r}")
    return {
        "phase": phase,
        "authoritative_total": authoritative_total,
        "d0": d0,
        "observed": {
            "engine": parse_engine_control(engine_raw),
            "input": parse_input_state(input_raw),
        },
        "blocks": {
            "engine_control": _block_evidence(
                ENGINE_CONTROL_BASE, engine_raw
            ),
            "input_state": _block_evidence(INPUT_STATE_BASE, input_raw),
        },
    }


def capture_natural_boundary(
    remote: GdbRemote,
    *,
    phase: str,
    authoritative_total: int,
    d0: int | None,
) -> dict[str, Any]:
    if phase not in {"pre", "post"}:
        raise ValueError(f"invalid natural boundary phase {phase!r}")
    _trace_memory_marker(
        remote.transcript,
        edge="begin",
        kind=f"natural_{phase}",
        identity=f"{authoritative_total:04d}",
    )
    engine_raw = b03._read_region(
        remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
    )
    input_raw = b03._read_region(
        remote, INPUT_STATE_BASE, INPUT_STATE_BYTES
    )
    _trace_memory_marker(
        remote.transcript,
        edge="end",
        kind=f"natural_{phase}",
        identity=f"{authoritative_total:04d}",
    )
    return build_natural_boundary_from_raw(
        phase=phase,
        authoritative_total=authoritative_total,
        d0=d0,
        engine_raw=engine_raw,
        input_raw=input_raw,
    )


def natural_boundary_errors(boundary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        phase = boundary["phase"]
        total = boundary["authoritative_total"]
        d0 = boundary["d0"]
        engine = boundary["observed"]["engine"]
        input_state = boundary["observed"]["input"]
        blocks = boundary["blocks"]
    except (KeyError, TypeError) as error:
        return [f"malformed natural boundary: {error}"]
    if phase not in {"pre", "post"}:
        return [f"invalid natural boundary phase {phase!r}"]
    if not 1 <= total <= OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
        return [f"natural boundary total {total!r} outside 1..3"]
    for name, address, size in (
        ("engine_control", ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES),
        ("input_state", INPUT_STATE_BASE, INPUT_STATE_BYTES),
    ):
        raw, block_errors = _public_block_raw(
            blocks.get(name), name=name, address=address, size=size
        )
        errors.extend(block_errors)
        if raw is not None:
            parsed = (
                parse_engine_control(raw)
                if name == "engine_control"
                else parse_input_state(raw)
            )
            if parsed != boundary["observed"][
                "engine" if name == "engine_control" else "input"
            ]:
                errors.append(f"natural {phase} {name} parse mismatch")
    desired = expected_input_for_total(total)
    expected_input = expected_controller_input_state(total)
    expected_tick = total - 1 if phase == "pre" else total
    if (
        engine["state"] != ENGINE_STATE_ACTIVE
        or engine["attempt"] != 0
        or engine["tick"] != expected_tick
        or input_state != expected_input
    ):
        errors.append(f"natural {phase} total {total} state/input mismatch")
    if phase == "pre" and d0 != desired:
        errors.append(f"natural PRE total {total} d0/input mismatch")
    if phase == "post" and d0 is not None:
        errors.append("natural POST unexpectedly publishes d0")
    if phase == "post" and engine["input_held"] != desired:
        errors.append(f"natural POST total {total} engine input mismatch")
    return errors


def capture_continuous_probe_state(
    remote: GdbRemote,
    *,
    identity: str,
) -> dict[str, Any]:
    _trace_memory_marker(
        remote.transcript,
        edge="begin",
        kind="continuous_state",
        identity=identity,
    )
    engine_raw = b03._read_region(
        remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
    )
    debug_raw = b03._read_region(
        remote, DEBUG_BASE, DEBUG_BUDGET_BYTES
    )
    input_raw = b03._read_region(
        remote, INPUT_STATE_BASE, INPUT_STATE_BYTES
    )
    _trace_memory_marker(
        remote.transcript,
        edge="end",
        kind="continuous_state",
        identity=identity,
    )
    return {
        "engine": parse_engine_control(engine_raw),
        "debug": parse_debug_budget(debug_raw),
        "input": parse_input_state(input_raw),
        "blocks": {
            "engine_control": _block_evidence(
                ENGINE_CONTROL_BASE, engine_raw
            ),
            "debug_budget": _block_evidence(DEBUG_BASE, debug_raw),
            "input_state": _block_evidence(INPUT_STATE_BASE, input_raw),
        },
    }


CADENCE_BLOCK_SPECS = {
    "engine_control": (
        ENGINE_CONTROL_BASE,
        ENGINE_CONTROL_USED_BYTES,
    ),
    "debug_budget": (DEBUG_BASE, DEBUG_BUDGET_BYTES),
    "input_state": (INPUT_STATE_BASE, INPUT_STATE_BYTES),
}


def _public_block_raw(
    block: Any,
    *,
    name: str,
    address: int,
    size: int,
) -> tuple[bytes | None, list[str]]:
    errors: list[str] = []
    if not isinstance(block, dict):
        return None, [f"{name} block missing"]
    if block.get("address") != f"{address:08X}":
        errors.append(f"{name} block address mismatch")
    if block.get("bytes") != size:
        errors.append(f"{name} block size mismatch")
    raw_hex = block.get("raw_hex")
    if (
        not isinstance(raw_hex, str)
        or raw_hex != raw_hex.upper()
        or not re.fullmatch(r"[0-9A-F]+", raw_hex)
    ):
        return None, [*errors, f"{name} raw hex invalid"]
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        return None, [*errors, f"{name} raw hex invalid"]
    if len(raw) != size:
        errors.append(f"{name} raw size mismatch")
    if block.get("sha256") != _sha256_bytes(raw):
        errors.append(f"{name} SHA-256 mismatch")
    return raw, errors


def cadence_row_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    total = row.get("authoritative_total")
    if not isinstance(total, int):
        return ["cadence total is not an integer"]
    physical_frame = expected_physical_frame(total)
    try:
        expected_route = expected_route_for_total(total)
        desired = expected_input_for_total(total)
        expected_audio = expected_audio_for_total(total)
    except ValueError as error:
        return [str(error)]
    expected = row.get("expected")
    if expected != {**expected_route, "input": desired}:
        errors.append(f"total {total} expected route/input mismatch")
    expected_proof = (
        "committed_replay"
        if total in {720, 1440}
        else "engine_and_input_state"
    )
    if row.get("input_proof") != expected_proof:
        errors.append(f"total {total} input proof mismatch")
    blocks = row.get("blocks")
    if not isinstance(blocks, dict) or set(blocks) != set(
        CADENCE_BLOCK_SPECS
    ):
        return [*errors, f"total {total} cadence block set mismatch"]
    raw: dict[str, bytes] = {}
    for name, (address, size) in CADENCE_BLOCK_SPECS.items():
        payload, block_errors = _public_block_raw(
            blocks.get(name),
            name=name,
            address=address,
            size=size,
        )
        errors.extend(
            f"total {total} {item}" for item in block_errors
        )
        if payload is not None:
            raw[name] = payload
    if set(raw) != set(CADENCE_BLOCK_SPECS):
        return errors
    rebuilt = build_cadence_row_from_raw(
        authoritative_total=total,
        engine_raw=raw["engine_control"],
        debug_raw=raw["debug_budget"],
        input_raw=raw["input_state"],
    )
    if row.get("observed") != rebuilt["observed"]:
        errors.append(f"total {total} parsed cadence fields mismatch")
    engine = rebuilt["observed"]["engine"]
    debug = rebuilt["observed"]["debug"]
    input_state = rebuilt["observed"]["input"]
    for name, parsed in (
        ("engine", engine),
        ("debug", debug),
        ("input", input_state),
    ):
        if set(parsed) != set(CADENCE_PARSED_FIELD_CONTRACT[name]):
            errors.append(
                f"total {total} {name} parsed-field contract mismatch"
            )
    engine_input = expected_engine_input_state(total)
    controller_input = expected_controller_input_state(total)
    checkpoint_tick, checkpoint_count = expected_checkpoint_for_total(total)
    terminal_reason = (
        PROGRAM_STABLE_TERMINAL
        if expected_route["state"] == ENGINE_STATE_TERMINAL
        else 0
    )
    exact_flags = ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID
    if (
        engine["magic"] != ENGINE_MAGIC
        or engine["magic_ascii"] != "R94E"
        or engine["abi_version"] != ENGINE_ABI_VERSION
        or engine["control_bytes"] != 0x0400
        or engine["state"] != expected_route["state"]
        or engine["flags"] not in {
            exact_flags,
            exact_flags | ENGINE_FLAG_COLLISION,
        }
        or engine["encounter"] != b03.B03_DIRECTORY_INDEX
        or engine["attempt"] != expected_route["attempt"]
        or engine["tick"] != expected_route["tick"]
        or engine["terminal_reason"] != terminal_reason
        or engine["last_error"] != 0
        or engine["overflow_flags"] != 0
        or engine["projection_length"]
        != b03.CAMPAIGN_PROJECTION_BYTES
        or engine["state_crc"] == 0
        or engine["checkpoint_tick"] != checkpoint_tick
        or engine["checkpoint_count"] != checkpoint_count
        or engine["input_held"] != engine_input["held"]
        or engine["input_pressed"] != engine_input["pressed"]
        or engine["input_released"] != engine_input["released"]
        or engine["input_previous"] != engine_input["previous"]
        or engine["facing_x"] != engine_input["facing_x"]
        or engine["active_actor_count"]
        != expected_route["attempt"] + 1
        or not 1 <= engine["active_actor_count"] <= ACTOR_CAPACITY
        or (
            engine["room_left"],
            engine["room_top"],
            engine["room_right"],
            engine["room_bottom"],
        )
        != b03.B03_ROOM
        or (engine["spawn_x"], engine["spawn_y"])
        != (b03.B03_SPAWN[0] << 8, b03.B03_SPAWN[1] << 8)
        or engine["rect_count"] != len(b03.B03_RECTS)
        or engine["object_init_count"] != len(b03.B03_DEVICES) + 1
    ):
        errors.append(f"total {total} engine cadence mismatch")
    if (
        raw["engine_control"][0x3C:0x40] != bytes(4)
        or raw["engine_control"][0x47] != 0
        or raw["engine_control"][0x4F] != 0
    ):
        errors.append(f"total {total} engine reserved bytes nonzero")
    if any(
        not (
            0
            <= engine["pool_counts"][index]
            <= engine["pool_high_water"][index]
            <= capacity
        )
        for index, (*_prefix, capacity) in enumerate(POOL_LAYOUT)
    ):
        errors.append(f"total {total} fixed-pool cadence exceeded")
    if any(
        engine["pool_counts"][index] != expected
        or engine["pool_high_water"][index] != expected
        for index, expected in B03_STATIC_POOL_COUNTS.items()
    ):
        errors.append(f"total {total} B03 static-pool cadence mismatch")
    if input_state != controller_input:
        errors.append(f"total {total} controller cadence mismatch")
    if (
        raw["debug_budget"][0x1E] != input_state["held"]
        or raw["debug_budget"][0x1F] != input_state["pressed"]
        or raw["debug_budget"][0x20] != input_state["released"]
        or raw["debug_budget"][0x21] != input_state["connected"]
        or raw["debug_budget"][0x22] != input_state["start_held"]
        or raw["debug_budget"][0x23] != 0
        or _u32(raw["debug_budget"], 0x3C) != physical_frame
    ):
        errors.append(f"total {total} Debug controller mirror mismatch")
    if (
        debug["magic"] != DEBUG_MAGIC
        or debug["magic_ascii"] != "R94D"
        or debug["abi_version"] != DEBUG_ABI_VERSION
        or debug["abi_size"] != DEBUG_ABI_SIZE
        or debug["sequence"] != physical_frame * 2
        or debug["frame_counter"] != physical_frame
        or debug["heartbeat"] != physical_frame
        or debug["frame_token"] != (2 if total in RESET_TOTALS else 0)
        or debug["boot_stage"] != BOOT_STAGE_RUNNING
        or debug["vblank_overrun_count"] != 0
        or debug["dma_overflow_count"] != 0
        or debug["dma_deferred_count"] != 0
        or debug["dma_required_drop_count"] != 0
        or debug["dma_invalid_count"] != 0
        or debug["z80_timeout_count"] != 0
        or debug["fatal_code"] != 0
        or debug["hardware_flags"] != 0
        or (
            debug["last_vdp_status"] & VDP_STATUS_DEFINED_MASK
        )
        not in VDP_STATUS_CADENCE_ALLOWED
        or debug["audio_state"] != AUDIO_STATE_PLAYING
        or debug["audio_command_count"]
        != expected_audio["command_count"]
        or debug["m2_flags"] != M2_HEALTHY_FLAGS
    ):
        errors.append(f"total {total} Debug cadence mismatch")
    if (
        not 0 <= debug["sprite_count"] <= RENDERER_SPRITE_LIMIT
        or not 0
        <= debug["sprite_scanline_max"]
        <= RENDERER_SCANLINE_SPRITE_LIMIT
        or not 0
        <= debug["dma_words_frame"]
        <= debug["dma_words_max"]
        <= DMA_ORDINARY_WORD_LIMIT
        or not 0
        <= debug["dma_queue_depth"]
        <= debug["dma_queue_high_water"]
        <= DMA_QUEUE_CAPACITY
    ):
        errors.append(f"total {total} Debug capacity telemetry exceeded")
    excess = rebuilt["observed"]["z80_vint_excess"]
    if not 0 <= excess <= Z80_VINT_STARTUP_ALLOWANCE:
        errors.append(f"total {total} Z80 cross-clock mismatch")
    if (debug["dma_words_frame"] == 0) != (total in ZERO_DMA_TOTALS):
        errors.append(f"total {total} exact zero-DMA frame set mismatch")
    return errors


def overrun_counter_isolation(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Prove that the preserved total-3 row differs only by the counter."""
    errors: list[str] = []
    try:
        total = row["authoritative_total"]
        blocks = row["blocks"]
        engine_raw = bytes.fromhex(blocks["engine_control"]["raw_hex"])
        debug_raw = bytes.fromhex(blocks["debug_budget"]["raw_hex"])
        input_raw = bytes.fromhex(blocks["input_state"]["raw_hex"])
        observed_counter = row["observed"]["debug"][
            "vblank_overrun_count"
        ]
    except (KeyError, TypeError, ValueError) as error:
        return {
            "status": "fail",
            "observed_counter": None,
            "preserved_row_errors": [f"malformed cadence row: {error}"],
            "counter_cleared_row_errors": [],
            "errors": ["could not isolate overrun counter"],
        }
    preserved_errors = cadence_row_errors(row)
    if total != OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
        errors.append("overrun-origin row is not authoritative total 3")
    if observed_counter != 1:
        errors.append("overrun-origin row counter is not exactly one")
    if preserved_errors != [f"total {total} Debug cadence mismatch"]:
        errors.append("preserved row has faults beyond the Debug gate")
    if len(debug_raw) != DEBUG_BUDGET_BYTES:
        errors.append("preserved Debug block size mismatch")
        cleared_errors: list[str] = []
    else:
        cleared = bytearray(debug_raw)
        cleared[0x32:0x34] = bytes(2)
        cleared_row = build_cadence_row_from_raw(
            authoritative_total=total,
            engine_raw=engine_raw,
            debug_raw=bytes(cleared),
            input_raw=input_raw,
        )
        cleared_errors = cadence_row_errors(cleared_row)
        if cleared_errors:
            errors.append(
                "counter-cleared host copy still violates cadence contract"
            )
    return {
        "status": "pass" if not errors else "fail",
        "observed_counter": observed_counter,
        "preserved_row_errors": preserved_errors,
        "counter_cleared_row_errors": cleared_errors,
        "host_only_transformation": (
            "zeroed debug_budget raw offsets 0x32..0x33 in an offline "
            "copy; no guest write"
        ),
        "errors": errors,
    }


def _window_title(window: int) -> str:
    if os.name != "nt":
        raise EvidenceFailure("window-title reads require native Windows")
    user32 = ctypes.windll.user32
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetWindowTextW.restype = ctypes.c_int
    length = user32.GetWindowTextLengthW(window)
    buffer = ctypes.create_unicode_buffer(max(length + 1, 2))
    copied = user32.GetWindowTextW(window, buffer, len(buffer))
    return buffer.value if copied > 0 else ""


def _set_window_title_bounded(
    window: int,
    sentinel: str,
    *,
    timeout_seconds: float,
) -> tuple[bool, float]:
    """Set WM_SETTEXT with a hard cross-process hung-window timeout."""
    if os.name != "nt":
        raise EvidenceFailure("window-title writes require native Windows")
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    )
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    payload = ctypes.create_unicode_buffer(sentinel)
    message_result = ctypes.c_size_t()
    started = time.monotonic()
    delivered = user32.SendMessageTimeoutW(
        window,
        0x000C,  # WM_SETTEXT
        0,
        ctypes.cast(payload, ctypes.c_void_p).value,
        0x0002,  # SMTO_ABORTIFHUNG
        max(1, int(timeout_seconds * 1000)),
        ctypes.byref(message_result),
    )
    elapsed = time.monotonic() - started
    if not delivered:
        raise TimeoutError(
            "SendMessageTimeoutW(WM_SETTEXT) failed or exceeded "
            f"{timeout_seconds:.3f}s for {sentinel!r}"
        )
    return bool(message_result.value), elapsed


def _sentinel_for(session_id: str, boundary_total: int) -> str:
    if not 0 <= boundary_total < EXPECTED_PHYSICAL_FRAMES:
        raise ValueError(
            f"sentinel boundary outside 0..1735: {boundary_total}"
        )
    return (
        f"R94-M5B03-{session_id}-FRAME-END-{boundary_total:04d}"
    )


def _wait_for_emulator_pause_overwrite(
    window: int,
    *,
    sentinel: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    samples = 0
    last = ""
    while time.monotonic() - started < timeout_seconds:
        samples += 1
        last = _window_title(window)
        if last != sentinel and last.upper().endswith(" - PAUSED"):
            return {
                "status": "pass",
                "observed_title": last,
                "elapsed_milliseconds": (
                    time.monotonic() - started
                )
                * 1000.0,
                "samples": samples,
                "timeout_seconds": timeout_seconds,
            }
        time.sleep(0.002)
    raise TimeoutError(
        "BlastEm did not overwrite the host sentinel with its internal "
        f"PAUSED title in {timeout_seconds:.3f}s; "
        f"sentinel={sentinel!r}, last={last!r}, samples={samples}"
    )


def _tap_frame_advance(
    window: int,
    *,
    index: int,
    purpose: str,
    transcript: list[str],
) -> dict[str, Any]:
    post_virtual_key(
        window,
        virtual_key=FRAME_ADVANCE_KEY["virtual_key"],
        scan_code=FRAME_ADVANCE_KEY["scan_code"],
        pressed=True,
        extended=FRAME_ADVANCE_KEY["extended"],
    )
    post_virtual_key(
        window,
        virtual_key=FRAME_ADVANCE_KEY["virtual_key"],
        scan_code=FRAME_ADVANCE_KEY["scan_code"],
        pressed=False,
        extended=FRAME_ADVANCE_KEY["extended"],
    )
    transcript.append(
        f"# ui.advance index={index} purpose={purpose}"
    )
    return {
        "index": index,
        "purpose": purpose,
        "key": FRAME_ADVANCE_KEY["name"],
        "complete_down_up_pulse": True,
    }


def _set_pause_sentinel(
    *,
    window: int,
    session_id: str,
    boundary_total: int,
    transcript: list[str],
) -> dict[str, Any]:
    sentinel = _sentinel_for(session_id, boundary_total)
    ok, set_elapsed = _set_window_title_bounded(
        window,
        sentinel,
        timeout_seconds=SET_TITLE_TIMEOUT_SECONDS,
    )
    if not ok:
        raise EvidenceFailure(
            "SendMessageTimeoutW(WM_SETTEXT) returned a false "
            f"message result for {sentinel!r}"
        )
    if (
        not math.isfinite(set_elapsed)
        or not 0.0 <= set_elapsed <= SET_TITLE_TIMEOUT_SECONDS
    ):
        raise EvidenceFailure(
            "bounded WM_SETTEXT elapsed outside "
            f"0..{SET_TITLE_TIMEOUT_SECONDS:.3f}s: {set_elapsed!r}"
        )
    readback = _window_title(window)
    if readback != sentinel:
        raise EvidenceFailure(
            f"sentinel readback {readback!r} != {sentinel!r}"
        )
    transcript.append(
        "# cadence.sentinel "
        f"boundary={boundary_total} title={sentinel!r}"
    )
    return {
        "boundary_total": boundary_total,
        "sentinel": sentinel,
        "sentinel_readback": readback,
        "set_window_text": (
            "SendMessageTimeoutW(WM_SETTEXT,SMTO_ABORTIFHUNG)"
        ),
        "set_timeout_seconds": SET_TITLE_TIMEOUT_SECONDS,
        "set_elapsed_milliseconds": set_elapsed * 1000.0,
    }


def _complete_sentinel_handshake(
    remote: GdbRemote,
    *,
    window: int,
    prepared: dict[str, Any],
    transcript: list[str],
    continue_guest: bool = True,
) -> dict[str, Any]:
    boundary_total = prepared["boundary_total"]
    sentinel = prepared["sentinel"]
    if continue_guest:
        remote.begin_continue()
    pause = _wait_for_emulator_pause_overwrite(
        window,
        sentinel=sentinel,
        timeout_seconds=PAUSE_OVERWRITE_TIMEOUT_SECONDS,
    )
    transcript.append(
        "# cadence.pause "
        f"boundary={boundary_total} title="
        f"{pause['observed_title']!r}"
    )
    return {
        **prepared,
        **pause,
    }


def _wait_for_origin_pause(
    window: int,
    *,
    initial_title: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if initial_title.upper().endswith(" - PAUSED"):
        raise EvidenceFailure(
            "fresh BlastEm title was already PAUSED before origin prime"
        )
    started = time.monotonic()
    samples = 0
    last = initial_title
    while time.monotonic() - started < timeout_seconds:
        samples += 1
        last = _window_title(window)
        if (
            last != initial_title
            and last.upper().endswith(" - PAUSED")
        ):
            return {
                "status": "pass",
                "initial_title": initial_title,
                "observed_title": last,
                "elapsed_milliseconds": (
                    time.monotonic() - started
                )
                * 1000.0,
                "samples": samples,
                "timeout_seconds": timeout_seconds,
            }
        time.sleep(0.002)
    raise TimeoutError(
        "origin prime did not reach a fresh internal PAUSED title in "
        f"{timeout_seconds:.3f}s; initial={initial_title!r}, "
        f"last={last!r}, samples={samples}"
    )


def _runtime_prerequisite_errors(
    payload: dict[str, Any],
    *,
    report_sha256: str,
    trace_sha256: str,
    replay_sha256: str,
) -> list[str]:
    errors: list[str] = []
    if report_sha256 != PINNED_RUNTIME_REPORT_SHA256:
        errors.append("runtime report SHA-256 does not match pinned PASS")
    if trace_sha256 != PINNED_RUNTIME_TRACE_SHA256:
        errors.append("runtime transcript SHA-256 does not match pinned PASS")
    if replay_sha256 != PINNED_RUNTIME_REPLAY_SHA256:
        errors.append("runtime replay SHA-256 does not match pinned PASS")
    if payload.get("status") != "pass":
        errors.append("runtime report status is not pass")
    if payload.get("acceptance_status") != "pass":
        errors.append("runtime report acceptance_status is not pass")
    if payload.get("rom_sha256") != PINNED_ROM_SHA256:
        errors.append("runtime report ROM binding mismatch")
    if payload.get("symbol_map_sha256") != PINNED_SYMBOL_SHA256:
        errors.append("runtime report symbol-map binding mismatch")
    checks = payload.get("checks")
    if (
        not isinstance(checks, list)
        or len(checks) != 15
        or any(
            not isinstance(check, dict)
            or check.get("status") != "pass"
            for check in checks
        )
    ):
        errors.append("runtime report does not contain 15 passing checks")
    if payload.get("debugger_write_packets") != []:
        errors.append("runtime report contains debugger guest writes")
    schedule = payload.get("schedule", {})
    if schedule.get("attempt_lengths") != [720, 720, 292]:
        errors.append("runtime report route lengths drifted")
    if schedule.get("guest_memory_register_pc_writes") is not False:
        errors.append("runtime report does not assert read-only GDB")
    transcript = payload.get("provenance", {}).get("transcript", {})
    if transcript.get("sha256") != trace_sha256:
        errors.append("runtime report transcript binding mismatch")
    replay = payload.get("guest_replay_ram", {})
    if (
        replay.get("sha256") != replay_sha256
        or replay.get("bytes") != b03.REPLAY_TIMELINE_BYTES
    ):
        errors.append("runtime report replay binding mismatch")
    sessions = payload.get("sessions")
    if not isinstance(sessions, list) or len(sessions) != SESSION_COUNT:
        errors.append("runtime report requires two parity sessions")
        return errors
    schedules = (
        b03.ATTEMPT_0_INPUTS,
        b03.ATTEMPT_1_INPUTS,
        b03.ATTEMPT_2_INPUTS,
    )
    expected_snapshot_set = set(EXPECTED_CAPTURE_LABELS)
    for index, session in enumerate(sessions, start=1):
        if session.get("session_id") != f"positive_session_{index}":
            errors.append(f"runtime session {index} identity mismatch")
        commanded = session.get("commanded_inputs", {})
        actual = session.get("attempt_inputs", {})
        pre = session.get("pre_step_polled_inputs", {})
        for attempt, expected in enumerate(schedules):
            key = str(attempt)
            expected_list = list(expected)
            if commanded.get(key) != expected_list:
                errors.append(
                    f"runtime session {index} commanded attempt "
                    f"{attempt} drifted"
                )
            if actual.get(key) != expected_list:
                errors.append(
                    f"runtime session {index} consumed attempt "
                    f"{attempt} drifted"
                )
            if pre.get(key) != expected_list:
                errors.append(
                    f"runtime session {index} PRE attempt {attempt} drifted"
                )
        snapshots = session.get("snapshots")
        if (
            not isinstance(snapshots, dict)
            or set(snapshots) != expected_snapshot_set
        ):
            errors.append(
                f"runtime session {index} checkpoint set drifted"
            )
        audit = session.get("metadata", {}).get(
            "dual_breakpoint_transcript", {}
        )
        if (
            audit.get("status") != "pass"
            or audit.get("expected_steps") != 1732
            or not audit.get("alternating_exact_pc_order")
            or not audit.get("remove_pre_then_continue_reinsert_pre")
        ):
            errors.append(
                f"runtime session {index} drive audit did not pass"
            )
    return errors


def load_runtime_prerequisite(
    *,
    report_path: Path,
    trace_path: Path,
    replay_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    for path, description in (
        (report_path, "canonical B03 runtime report"),
        (trace_path, "canonical B03 runtime transcript"),
        (replay_path, "canonical B03 runtime replay RAM"),
    ):
        if not path.is_file():
            raise EvidenceFailure(f"{description} is missing: {path}")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceFailure(
            f"could not parse canonical B03 runtime report: {error}"
        ) from error
    hashes = {
        "report_sha256": sha256_file(report_path),
        "trace_sha256": sha256_file(trace_path),
        "replay_sha256": sha256_file(replay_path),
    }
    errors = _runtime_prerequisite_errors(payload, **hashes)
    if errors:
        raise EvidenceFailure(
            "canonical B03 runtime prerequisite failed: "
            + "; ".join(errors)
        )
    return payload, {"status": "pass", **hashes}


def _session_schedule_proof(session: dict[str, Any]) -> dict[str, Any]:
    schedules = (
        b03.ATTEMPT_0_INPUTS,
        b03.ATTEMPT_1_INPUTS,
        b03.ATTEMPT_2_INPUTS,
    )
    consumed = session.get("attempt_inputs", {})
    commanded = session.get("commanded_inputs", {})
    post_observed = session.get("post_step_observed_inputs", {})
    replay_commits = session.get("timeout_commits", [])
    attempts = []
    passed = True
    for attempt, expected in enumerate(schedules):
        key = str(attempt)
        expected_list = list(expected)
        observed = post_observed.get(key)
        observed_nonreset = (
            isinstance(observed, list)
            and len(observed) == len(expected)
            and all(
                observed[index] == expected[index]
                for index in range(len(expected))
                if not (
                    attempt in {0, 1}
                    and index == len(expected) - 1
                )
            )
        )
        replay_proven = (
            attempt == 2
            or any(
                item.get("attempt") == attempt
                and item.get("status") == "pass"
                and item.get("committed_input_sha256")
                == _sha256_bytes(expected)
                and item.get("committed_input_raw_hex")
                == expected.hex().upper()
                for item in replay_commits
                if isinstance(item, dict)
            )
        )
        item_passed = (
            consumed.get(key) == expected_list
            and commanded.get(key) == expected_list
            and observed_nonreset
            and replay_proven
        )
        attempts.append(
            {
                "attempt": attempt,
                "steps": len(expected),
                "sha256": _sha256_bytes(expected),
                "commanded_equals_expected": (
                    commanded.get(key) == expected_list
                ),
                "consumed_equals_expected": (
                    consumed.get(key) == expected_list
                ),
                "post_observed_nonreset_equals_expected": (
                    observed_nonreset
                ),
                "timeout_replay_proves_final_step": replay_proven,
            }
        )
        passed = passed and item_passed
    return {
        "status": "pass" if passed else "fail",
        "attempts": attempts,
        "total_steps": sum(len(schedule) for schedule in schedules),
        "combined_sha256": _sha256_bytes(b"".join(schedules)),
    }


def _extract_budget_session(
    raw_session: dict[str, Any],
    *,
    session_id: str,
) -> dict[str, Any]:
    snapshots = raw_session.get("snapshots", {})
    if set(snapshots) != set(EXPECTED_CAPTURE_LABELS):
        raise EvidenceFailure(
            f"{session_id} B03 checkpoint set drifted: "
            f"{sorted(snapshots)}"
        )
    observations = []
    scenario_snapshots = []
    for label in EXPECTED_CAPTURE_LABELS:
        observation = snapshots[label].get("_budget_observation")
        if not isinstance(observation, dict):
            raise EvidenceFailure(
                f"{session_id} lacks budget observation {label}"
            )
        observations.append(observation)
        scenario_snapshots.append(
            _scenario_evidence_from_snapshot(snapshots[label])
        )
    facts = b03._scenario_facts(raw_session)
    if not facts or not all(facts.values()):
        failed = sorted(name for name, value in facts.items() if not value)
        raise EvidenceFailure(
            f"{session_id} canonical B03 route facts failed: {failed}"
        )
    schedule_proof = _session_schedule_proof(raw_session)
    if schedule_proof["status"] != "pass":
        raise EvidenceFailure(
            f"{session_id} exact canonical schedule proof failed"
        )
    cadence_rows = raw_session.get("cadence_rows")
    if (
        not isinstance(cadence_rows, list)
        or len(cadence_rows) != EXPECTED_AUTHORITATIVE_STEPS
    ):
        raise EvidenceFailure(
            f"{session_id} cadence row count is not "
            f"{EXPECTED_AUTHORITATIVE_STEPS}"
        )
    row_errors = [
        error
        for row in cadence_rows
        for error in cadence_row_errors(row)
    ]
    if row_errors:
        raise EvidenceFailure(
            f"{session_id} cadence rows failed: {row_errors[:3]}"
        )
    terminal_replay = snapshots["terminal"]["_replay_bytes"]
    terminal_replay_hash = _sha256_bytes(terminal_replay)
    if terminal_replay_hash != PINNED_RUNTIME_REPLAY_SHA256:
        raise EvidenceFailure(
            f"{session_id} terminal replay RAM hash "
            f"{terminal_replay_hash} does not match canonical B03"
        )
    terminal_wait = raw_session.get("terminal_wait_observation")
    if (
        not isinstance(terminal_wait, dict)
        or cadence_row_errors(terminal_wait)
    ):
        raise EvidenceFailure(
            f"{session_id} terminal WAIT observation is invalid"
        )
    metadata = dict(raw_session["metadata"])
    return {
        "session_id": session_id,
        "metadata": metadata,
        "route": {
            "status": "pass",
            "scenario_facts": facts,
            "schedule_proof": schedule_proof,
            "checkpoint_labels": list(EXPECTED_CAPTURE_LABELS),
            "scenario_snapshots": scenario_snapshots,
            "cadence_rows": cadence_rows,
            "timeout_commits": raw_session.get("timeout_commits", []),
            "terminal_wait_observation": terminal_wait,
            "terminal_replay": {
                "bytes": len(terminal_replay),
                "sha256": terminal_replay_hash,
                "raw_hex": terminal_replay.hex().upper(),
            },
        },
        "observations": observations,
        "_transcript_lines": raw_session["_transcript_lines"],
    }


def _all_observations(
    sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        observation
        for session in sessions
        for observation in session.get("observations", [])
    ]


def _pool_layout_static_ok() -> bool:
    previous_end = ENGINE_POOL_REGION_START
    for _pool_type, _name, address, capacity in POOL_LAYOUT:
        end = address + capacity * OBJECT_BYTES
        if (
            address < previous_end
            or address < WORK_RAM_START
            or end > WORK_RAM_END
            or address < ENGINE_POOL_REGION_START
            or end > ENGINE_POOL_REGION_END
        ):
            return False
        previous_end = end
    return sum(capacity for *_prefix, capacity in POOL_LAYOUT) == 140


def _observation_fault_values(
    observation: dict[str, Any],
) -> dict[str, int]:
    debug = observation["debug"]
    renderer = observation["renderer"]
    engine = observation["engine"]
    vocabulary = observation["vocabulary"]
    audio = observation["audio_shadow"]
    return {
        "engine_last_error": engine["last_error"],
        "engine_overflow_flags": engine["overflow_flags"],
        "vocabulary_overflow_flags": vocabulary["overflow_flags"],
        "vocabulary_budget_exceeded": (
            vocabulary["flags"] & VOCAB_FLAG_BUDGET_EXCEEDED
        ),
        "renderer_fault_flags": (
            renderer["flags"] & RENDERER_FAULT_FLAGS
        ),
        "renderer_dropped_sprites": renderer["dropped_sprites"],
        "renderer_dma_rejects": renderer["dma_rejects"],
        "renderer_backlog_frames": renderer["backlog_frames"],
        "dma_overflow_count": debug["dma_overflow_count"],
        "dma_deferred_count": debug["dma_deferred_count"],
        "dma_required_drop_count": debug["dma_required_drop_count"],
        "dma_invalid_count": debug["dma_invalid_count"],
        "vblank_overrun_count": debug["vblank_overrun_count"],
        "z80_timeout_count": debug["z80_timeout_count"],
        "debug_fatal_code": debug["fatal_code"],
        "debug_hardware_flags": debug["hardware_flags"],
        "audio_fault_flags": audio["flags"] & AUDIO_STATUS_FAULT_MASK,
        "audio_late_tick_count": audio["late_tick_count"],
    }


def _observation_pool_ok(observation: dict[str, Any]) -> bool:
    engine = observation["engine"]
    vocabulary = observation["vocabulary"]
    for index, (_pool_type, name, _address, capacity) in enumerate(
        POOL_LAYOUT
    ):
        pool = observation["pools"].get(name, {})
        if (
            pool.get("capacity") != capacity
            or pool.get("active_count") != engine["pool_counts"][index]
            or pool.get("invalid_active_values") != []
            or not (
                0
                <= engine["pool_counts"][index]
                <= engine["pool_high_water"][index]
                <= capacity
            )
        ):
            return False
    return (
        vocabulary["active_enemy_count"] == engine["pool_counts"][0]
        and vocabulary["active_projectile_count"]
        == engine["pool_counts"][1]
        and 0
        <= vocabulary["anchor_count"]
        <= vocabulary["anchor_capacity"]
        <= VOCAB_ANCHOR_LIMIT
        and 0 <= vocabulary["beam_count"] <= VOCAB_BEAM_LIMIT
        and vocabulary["header_reserved"] == 0
    )


def _observation_vocabulary_budget_ok(
    observation: dict[str, Any],
) -> bool:
    vocabulary = observation["vocabulary"]
    reset_boundary = observation["label"] in {
        "initial",
        "post_timeout_1",
        "post_timeout_2",
    }
    expected_units = 0 if reset_boundary else VOCAB_CPU_WORST_UNITS
    return (
        vocabulary["magic"] == VOCAB_MAGIC
        and vocabulary["version"] == VOCAB_VERSION
        and vocabulary["bytes"] == VOCAB_BYTES
        and vocabulary["flags"] & VOCAB_FLAG_INITIALIZED
        and vocabulary["stage_id"] == b03.B03_STAGE_ID
        and vocabulary["update_units"] == expected_units
        and vocabulary["update_peak"] == expected_units
        and 0 <= vocabulary["update_units"] <= VOCAB_CPU_UNIT_LIMIT
        and 0 <= vocabulary["update_peak"] <= VOCAB_CPU_UNIT_LIMIT
    )


def _observation_capacity_ok(observation: dict[str, Any]) -> bool:
    debug = observation["debug"]
    renderer = observation["renderer"]
    dma = observation["dma_queue"]
    return (
        0 <= renderer["sprite_count"] <= RENDERER_SPRITE_LIMIT
        and 0 <= debug["sprite_count"] <= RENDERER_SPRITE_LIMIT
        and debug["sprite_count"] == renderer["sprite_count"]
        and 0
        <= renderer["scanline_max"]
        <= RENDERER_SCANLINE_SPRITE_LIMIT
        and debug["sprite_scanline_max"] == renderer["scanline_max"]
        and 0
        <= renderer["scanline_pixel_max"]
        <= RENDERER_SCANLINE_PIXEL_LIMIT
        and 0 <= debug["dma_words_frame"] <= DMA_ORDINARY_WORD_LIMIT
        and debug["dma_words_frame"] <= debug["dma_words_max"]
        <= DMA_ORDINARY_WORD_LIMIT
        and 0 <= debug["dma_queue_depth"] <= DMA_QUEUE_CAPACITY
        and 0
        <= debug["dma_queue_high_water"]
        <= DMA_QUEUE_CAPACITY
        and 0 <= dma["published_depth"] <= DMA_QUEUE_CAPACITY
        and 0 <= dma["build_depth"] <= DMA_QUEUE_CAPACITY
        and 0 <= dma["build_words"] <= DMA_ORDINARY_WORD_LIMIT
        and 0 <= dma["commit_words"] <= DMA_ORDINARY_WORD_LIMIT
        and dma["frame_pending"] in (0, 1)
        and dma["reserved_0"] == 0
        and dma["reserved_1"] == 0
    )


def _observation_audio_clock_ok(
    observation: dict[str, Any],
) -> bool:
    debug = observation["debug"]
    audio = observation["audio_shadow"]
    clock = observation["cross_clock"]
    total = observation["route"]["authoritative_total"]
    if observation["label"] == "initial":
        return (
            debug["audio_state"] == AUDIO_STATE_PLAYING
            and debug["audio_command_count"] == 0
            and debug["m2_flags"] == M2_STARTUP_FLAGS
            and all(value == 0 for value in audio.values())
            and clock["m68k_frame_counter"] == 0
            and clock["z80_vint_heartbeat"] == 0
            and clock["z80_vint_excess"] == 0
        )
    expected = expected_audio_for_total(total)
    return (
        debug["audio_state"] == AUDIO_STATE_PLAYING
        and debug["audio_command_count"] == expected["command_count"]
        and debug["m2_flags"] == M2_HEALTHY_FLAGS
        and audio["signature"] == AUDIO_STATUS_SIGNATURE
        and audio["abi_version"] == AUDIO_STATUS_ABI_VERSION
        and audio["state"] == AUDIO_STATE_PLAYING
        and audio["flags"] == AUDIO_STATUS_FLAGS_HEALTHY
        and audio["heartbeat"] == debug["z80_heartbeat"]
        and audio["queue_read_sequence"]
        == expected["queue_read_sequence"]
        and 1 <= audio["queue_high_water"] <= AUDIO_QUEUE_CAPACITY
        and audio["processed_commands"] == expected["command_count"]
        and audio["processed_commands"] == debug["audio_command_count"]
        and audio["ym_write_count"] > 0
        and audio["psg_write_count"] > 0
        and audio["active_layers"] == expected["active_layers"]
        and audio["last_error_or_command"] == expected["last_command"]
        and clock["m68k_frame_counter"] == expected_physical_frame(total)
        and clock["z80_vint_heartbeat"]
        >= clock["m68k_frame_counter"] - expected_audio_shadow_age(total)
        and 0
        <= clock["z80_vint_excess"]
        <= Z80_VINT_STARTUP_ALLOWANCE
    )


def _observation_abi_clock_ok(
    observation: dict[str, Any],
) -> bool:
    engine = observation["engine"]
    debug = observation["debug"]
    renderer = observation["renderer"]
    route = observation["route"]
    terminal_reason_ok = (
        engine["terminal_reason"] == PROGRAM_STABLE_TERMINAL
        if route["expected_state"] == ENGINE_STATE_TERMINAL
        else engine["terminal_reason"] == 0
    )
    total = route["authoritative_total"]
    expected_boot_stage = (
        3 if observation["label"] == "initial" else BOOT_STAGE_RUNNING
    )
    return (
        engine["magic"] == ENGINE_MAGIC
        and engine["abi_version"] == ENGINE_ABI_VERSION
        and engine["control_bytes"] == 0x0400
        and engine["state"] == route["expected_state"]
        and engine["attempt"] == route["expected_attempt"]
        and engine["tick"] == route["expected_tick"]
        and terminal_reason_ok
        and debug["magic"] == DEBUG_MAGIC
        and debug["abi_version"] == DEBUG_ABI_VERSION
        and debug["abi_size"] == DEBUG_ABI_SIZE
        and debug["sequence"] == expected_physical_frame(total) * 2
        and debug["heartbeat"] == debug["frame_counter"]
        and debug["frame_counter"] == expected_physical_frame(total)
        and debug["frame_token"] == (2 if total in RESET_TOTALS else 0)
        and debug["boot_stage"] == expected_boot_stage
        and renderer["magic"] == RENDERER_MAGIC
        and renderer["version"] == RENDERER_VERSION
        and renderer["bytes"] == RENDERER_BYTES
        and renderer["flags"] & RENDERER_FLAG_INITIALIZED
        and renderer["last_result"] == 0
    )


def _budget_signature(session: dict[str, Any]) -> dict[str, Any]:
    observations = []
    for observation in session["observations"]:
        observations.append(
            {
                "label": observation["label"],
                "route": observation["route"],
                "engine": observation["engine"],
                "debug": observation["debug"],
                "renderer": observation["renderer"],
                "dma_queue": observation["dma_queue"],
                "audio_shadow": observation["audio_shadow"],
                "vocabulary": observation["vocabulary"],
                "pools": observation["pools"],
                "cross_clock": observation["cross_clock"],
                "block_sha256": observation["read_consistency"][
                    "first_block_sha256"
                ],
            }
        )
    return {
        "schedule": session["route"]["schedule_proof"],
        "scenario_facts": session["route"]["scenario_facts"],
        "scenario_snapshots": session["route"]["scenario_snapshots"],
        "cadence": [
            {
                "authoritative_total": row["authoritative_total"],
                "expected": row["expected"],
                "input_proof": row["input_proof"],
                "observed": row["observed"],
                "block_sha256": {
                    name: block["sha256"]
                    for name, block in row["blocks"].items()
                },
            }
            for row in session["route"]["cadence_rows"]
        ],
        "terminal_replay_sha256": session["route"][
            "terminal_replay"
        ]["sha256"],
        "terminal_wait": session["route"][
            "terminal_wait_observation"
        ],
        "checkpoint_renderers": [
            {"authoritative_total": row["authoritative_total"], "evidence": row["checkpoint_renderer"]}
            for row in session["route"]["cadence_rows"] if "checkpoint_renderer" in row
        ],
        "observations": observations,
    }


def _signature_record(session: dict[str, Any]) -> dict[str, Any]:
    signature = _budget_signature(session)
    return {
        "session_id": session["session_id"],
        "sha256": _sha256_bytes(_stable_json(signature)),
        "observation_count": len(signature["observations"]),
        "first_label": signature["observations"][0]["label"],
        "last_label": signature["observations"][-1]["label"],
    }


HASH_BINDING_NAMES = (
    "rom",
    "symbol_map",
    "emulator",
    "default_config",
    "runtime_report",
    "runtime_trace",
    "runtime_replay",
    "b03_collector",
    "collector",
    "report_schema",
    "probe",
)


def _hash_bindings_valid(
    sessions: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> bool:
    if any(
        not _valid_sha256(provenance.get(name, {}).get("sha256"))
        for name in HASH_BINDING_NAMES
    ):
        return False
    if (
        provenance["rom"]["sha256"] != PINNED_ROM_SHA256
        or provenance["symbol_map"]["sha256"] != PINNED_SYMBOL_SHA256
        or provenance["emulator"]["sha256"]
        != PINNED_EMULATOR_SHA256
        or provenance["default_config"]["sha256"]
        != PINNED_DEFAULT_CONFIG_SHA256
        or provenance["runtime_report"]["sha256"]
        != PINNED_RUNTIME_REPORT_SHA256
        or provenance["runtime_trace"]["sha256"]
        != PINNED_RUNTIME_TRACE_SHA256
        or provenance["runtime_replay"]["sha256"]
        != PINNED_RUNTIME_REPLAY_SHA256
        or _source_bindings_errors(
            provenance.get("source_bindings"), root=None
        )
    ):
        return False
    frame_contract = provenance.get("frame_advance_source_contract")
    if (
        not isinstance(frame_contract, dict)
        or frame_contract.get("status") != "pass"
        or frame_contract.get("blastem_version") != BLASTEM_VERSION
        or frame_contract.get("source_node") != BLASTEM_SOURCE_NODE
        or frame_contract.get("source_sha256")
        != BLASTEM_SOURCE_HASHES
        or frame_contract.get("default_config_sha256")
        != PINNED_DEFAULT_CONFIG_SHA256
    ):
        return False
    verification = provenance.get("hash_verification", {})
    before_after = verification.get("before_after")
    if (
        verification.get("status") != "pass"
        or verification.get("drift") != []
        or not isinstance(before_after, dict)
        or set(before_after) != set(HASH_BINDING_NAMES)
    ):
        return False
    for name in HASH_BINDING_NAMES:
        pair = before_after.get(name, {})
        if (
            pair.get("before") != pair.get("after")
            or pair.get("before") != provenance[name]["sha256"]
        ):
            return False
    transcript_lines: list[str] = []
    runtime_directories: set[str] = set()
    for session in sessions:
        lines = session.get("_transcript_lines")
        metadata = session.get("metadata", {})
        runtime = metadata.get("runtime_copy", {})
        profile = metadata.get("controller_profile", {})
        if not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines
        ):
            return False
        payload = (
            "\n".join(lines) + ("\n" if lines else "")
        ).encode("utf-8")
        if metadata.get("transcript_sha256") != _sha256_bytes(payload):
            return False
        if (
            runtime.get("executable_sha256")
            != provenance["emulator"]["sha256"]
            or runtime.get("default_config_sha256")
            != provenance["default_config"]["sha256"]
            or profile.get("session_isolation", {}).get("status") != "pass"
            or profile.get("port_1_assignment") != "gamepad3.1"
        ):
            return False
        runtime_directory = runtime.get("temporary_directory")
        if not isinstance(runtime_directory, str) or not runtime_directory:
            return False
        runtime_directories.add(runtime_directory)
        transcript_lines.extend(lines)
    if len(runtime_directories) != SESSION_COUNT:
        return False
    transcript_payload = (
        "\n".join(transcript_lines)
        + ("\n" if transcript_lines else "")
    ).encode("utf-8")
    return (
        provenance.get("transcript", {}).get("sha256")
        == _sha256_bytes(transcript_payload)
        and provenance.get("transcript", {}).get("commands")
        == sum(line.startswith("> ") for line in transcript_lines)
    )


def _legacy_event_filter_audit(
    lines: list[str],
    *,
    session_id: str,
    post_step: int,
    finalizer: int,
    wait_for_vblank: int,
) -> dict[str, Any]:
    post_insert = f"> Z0,{post_step:x},2"
    post_remove = f"> z0,{post_step:x},2"
    final_insert = f"> Z0,{finalizer:x},2"
    final_remove = f"> z0,{finalizer:x},2"
    wait_insert = f"> Z0,{wait_for_vblank:x},2"
    wait_remove = f"> z0,{wait_for_vblank:x},2"
    register_values: list[str] = []
    events: list[tuple[Any, ...]] = []
    malformed_protocol_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == post_insert:
            events.append(("insert_post",))
        elif line == post_remove:
            events.append(("remove_post",))
        elif line == final_insert:
            events.append(("insert_finalizer",))
        elif line == final_remove:
            events.append(("remove_finalizer",))
        elif line == wait_insert:
            events.append(("insert_wait",))
        elif line == wait_remove:
            events.append(("remove_wait",))
        elif (
            line.startswith("> Z0,") or line.startswith("> z0,")
        ) and line not in {
            post_insert,
            post_remove,
            final_insert,
            final_remove,
            wait_insert,
            wait_remove,
        }:
            # The startup VBlank lifecycle occurs before the permanent POST.
            events.append(("other_breakpoint", line))
        elif line == "> c":
            events.append(("continue",))
        elif line == "< S05":
            events.append(("stop_response",))
        elif line == "> p11":
            if index + 1 >= len(lines) or not lines[
                index + 1
            ].startswith("< "):
                events.append(("invalid_pc_read",))
            else:
                value = lines[index + 1].removeprefix("< ").upper()
                register_values.append(value)
                events.append(("pc", value))
                index += 1
        elif line.startswith("# cadence.sentinel "):
            match = re.fullmatch(
                r"# cadence\.sentinel boundary=([0-9]+) title=(.+)",
                line,
            )
            if not match:
                malformed_protocol_lines.append(line)
            else:
                try:
                    title = ast.literal_eval(match.group(2))
                except (SyntaxError, ValueError):
                    title = None
                boundary = int(match.group(1))
                expected_title = _sentinel_for(session_id, boundary)
                events.append(
                    (
                        "sentinel"
                        if title == expected_title
                        else "invalid_sentinel",
                        boundary,
                    )
                )
        elif line.startswith("# cadence.pause "):
            match = re.fullmatch(
                r"# cadence\.pause boundary=([0-9]+) title=(.+)",
                line,
            )
            if not match:
                malformed_protocol_lines.append(line)
            else:
                try:
                    title = ast.literal_eval(match.group(2))
                except (SyntaxError, ValueError):
                    title = None
                boundary = int(match.group(1))
                sentinel = _sentinel_for(session_id, boundary)
                valid = (
                    isinstance(title, str)
                    and title != sentinel
                    and title.upper().endswith(" - PAUSED")
                )
                events.append(
                    (
                        "pause" if valid else "invalid_pause",
                        boundary,
                    )
                )
        elif line.startswith("# cadence.origin_pause "):
            match = re.fullmatch(
                r"# cadence\.origin_pause title=(.+)", line
            )
            if not match:
                malformed_protocol_lines.append(line)
            else:
                try:
                    title = ast.literal_eval(match.group(1))
                except (SyntaxError, ValueError):
                    title = None
                events.append(
                    (
                        "origin_pause"
                        if isinstance(title, str)
                        and title.upper().endswith(" - PAUSED")
                        else "invalid_origin_pause",
                    )
                )
        elif line.startswith("# ui.advance "):
            match = re.fullmatch(
                r"# ui\.advance index=([0-9]+) purpose=(\S+)",
                line,
            )
            if not match:
                malformed_protocol_lines.append(line)
            else:
                events.append(
                    (
                        "advance",
                        int(match.group(1)),
                        match.group(2),
                    )
                )
        elif line.startswith("# cadence.stop "):
            match = re.fullmatch(
                r"# cadence\.stop total=([0-9]+) "
                r"role=(post|finalizer|terminal_post|terminal_wait) "
                r"pc=([0-9A-F]{8})",
                line,
            )
            if not match:
                malformed_protocol_lines.append(line)
            else:
                events.append(
                    (
                        "stop_marker",
                        int(match.group(1)),
                        match.group(2),
                        match.group(3),
                    )
                )
        index += 1

    # Discard the startup VBlank Z/c/S05/z prefix.  From permanent POST
    # insertion onward, every protocol-relevant event has one exact order.
    try:
        protocol_start = events.index(("insert_post",))
    except ValueError:
        protocol_events: list[tuple[Any, ...]] = events
    else:
        protocol_events = events[protocol_start:]

    expected_events: list[tuple[Any, ...]] = [
        ("insert_post",),
        ("insert_finalizer",),
        ("advance", 1, "origin_prime"),
        ("continue",),
        ("origin_pause",),
        ("sentinel", 0),
    ]
    for total in range(1, EXPECTED_AUTHORITATIVE_STEPS + 1):
        expected_events.append(
            ("advance", total + 1, f"authoritative_total_{total}")
        )
        expected_events.append(("stop_response",))
        if total < EXPECTED_AUTHORITATIVE_STEPS:
            expected_events.extend(
                [
                    ("pc", f"{post_step:08X}"),
                    (
                        "stop_marker",
                        total,
                        "post",
                        f"{post_step:08X}",
                    ),
                    ("continue",),
                    ("pause", total - 1),
                    ("sentinel", total),
                ]
            )
        else:
            expected_events.extend(
                [
                    ("pc", f"{finalizer:08X}"),
                    (
                        "stop_marker",
                        total,
                        "finalizer",
                        f"{finalizer:08X}",
                    ),
                    ("remove_finalizer",),
                    ("continue",),
                    ("stop_response",),
                    ("pc", f"{post_step:08X}"),
                    (
                        "stop_marker",
                        total,
                        "terminal_post",
                        f"{post_step:08X}",
                    ),
                    ("insert_wait",),
                    ("continue",),
                    ("stop_response",),
                    ("pc", f"{wait_for_vblank:08X}"),
                    (
                        "stop_marker",
                        total,
                        "terminal_wait",
                        f"{wait_for_vblank:08X}",
                    ),
                    ("remove_wait",),
                    ("remove_post",),
                    ("continue",),
                    ("pause", total - 1),
                ]
            )

    expected_registers = (
        [f"{post_step:08X}"] * (EXPECTED_AUTHORITATIVE_STEPS - 1)
        + [
            f"{finalizer:08X}",
            f"{post_step:08X}",
            f"{wait_for_vblank:08X}",
        ]
    )
    advance_indices = []
    for line in lines:
        match = re.fullmatch(
            r"# ui\.advance index=([0-9]+) purpose=.+", line
        )
        if match:
            advance_indices.append(int(match.group(1)))
    sentinel_boundaries = []
    pause_boundaries = []
    for line in lines:
        match = re.fullmatch(
            r"# cadence\.sentinel boundary=([0-9]+) title=.+", line
        )
        if match:
            sentinel_boundaries.append(int(match.group(1)))
        match = re.fullmatch(
            r"# cadence\.pause boundary=([0-9]+) title=.+", line
        )
        if match:
            pause_boundaries.append(int(match.group(1)))
    post_stop_totals: list[int] = []
    finalizer_totals: list[int] = []
    wait_totals: list[int] = []
    for line in lines:
        match = re.fullmatch(
            r"# cadence\.stop total=([0-9]+) "
            r"role=(post|finalizer|terminal_post|terminal_wait) "
            r"pc=([0-9A-F]{8})",
            line,
        )
        if not match:
            continue
        total = int(match.group(1))
        role = match.group(2)
        pc = int(match.group(3), 16)
        if role in {"post", "terminal_post"} and pc == post_step:
            post_stop_totals.append(total)
        elif role == "finalizer" and pc == finalizer:
            finalizer_totals.append(total)
        elif role == "terminal_wait" and pc == wait_for_vblank:
            wait_totals.append(total)
    forbidden_control = [
        line
        for line in lines
        if line == "> <interrupt>"
        or line == "> s"
        or line.startswith("> vCont;s")
        or "ui.pause" in line.lower()
        or re.fullmatch(r"# ui\.f7.*", line, flags=re.IGNORECASE)
        or line.startswith("# cadence.retry")
        or line.startswith("# ui.advance.retry")
    ]
    writes = debugger_write_packets(lines)
    exact_event_order = protocol_events == expected_events
    passed = (
        lines.count(post_insert) == 1
        and lines.count(post_remove) == 1
        and lines.count(final_insert) == 1
        and lines.count(final_remove) == 1
        and lines.count(wait_insert) == 1
        and lines.count(wait_remove) == 1
        and register_values == expected_registers
        and advance_indices
        == list(range(1, EXPECTED_AUTHORITATIVE_STEPS + 2))
        and sentinel_boundaries
        == list(range(EXPECTED_AUTHORITATIVE_STEPS))
        and pause_boundaries
        == list(range(EXPECTED_AUTHORITATIVE_STEPS))
        and post_stop_totals
        == list(range(1, EXPECTED_AUTHORITATIVE_STEPS + 1))
        and finalizer_totals == [EXPECTED_AUTHORITATIVE_STEPS]
        and wait_totals == [EXPECTED_AUTHORITATIVE_STEPS]
        and exact_event_order
        and not malformed_protocol_lines
        and not forbidden_control
        and not writes
    )
    return {
        "status": "pass" if passed else "fail",
        "expected_authoritative_steps": EXPECTED_AUTHORITATIVE_STEPS,
        "post_breakpoint_insertions": lines.count(post_insert),
        "post_breakpoint_removals": lines.count(post_remove),
        "finalizer_breakpoint_insertions": lines.count(final_insert),
        "finalizer_breakpoint_removals": lines.count(final_remove),
        "wait_breakpoint_insertions": lines.count(wait_insert),
        "wait_breakpoint_removals": lines.count(wait_remove),
        "post_stops": len(post_stop_totals),
        "finalizer_stops": len(finalizer_totals),
        "wait_stops": len(wait_totals),
        "exact_post_total_sequence": post_stop_totals
        == list(range(1, EXPECTED_AUTHORITATIVE_STEPS + 1)),
        "exact_pc_sequence": register_values == expected_registers,
        "sentinel_handshakes": len(sentinel_boundaries),
        "exact_sentinel_boundary_sequence": sentinel_boundaries
        == list(range(EXPECTED_AUTHORITATIVE_STEPS)),
        "pause_overwrites": len(pause_boundaries),
        "ui_advance_pulses": len(advance_indices),
        "exact_ui_advance_sequence": advance_indices
        == list(range(1, EXPECTED_AUTHORITATIVE_STEPS + 2)),
        "exact_event_order": exact_event_order,
        "protocol_event_count": len(protocol_events),
        "expected_protocol_event_count": len(expected_events),
        "malformed_protocol_lines": malformed_protocol_lines,
        "forbidden_control": forbidden_control,
        "debugger_write_packets": writes,
    }


def _trace_read_plan(
    kind: str,
) -> tuple[tuple[str, int, int], ...]:
    if kind == "checkpoint_renderer":
        return (
            ("renderer_private", RENDERER_STATE_BASE, M5_RENDERER_PRIVATE_BYTES),
            ("dma_recovery", DMA_QUEUE_BASE, CHECKPOINT_DMA_BYTES),
        )
    if kind == "scenario":
        return tuple(
            (name, address, size)
            for name, _private, address, size in SCENARIO_BLOCK_SPECS
        )
    if kind in {"budget_first", "budget_second"}:
        return tuple(
            (name, address, size)
            for name, _key, address, size, _parser in BLOCK_SPECS
        )
    if kind in {"cadence", "terminal_wait", "continuous_state"}:
        return tuple(
            (name, address, size)
            for name, (address, size) in CADENCE_BLOCK_SPECS.items()
        )
    if kind in {"natural_pre", "natural_post"}:
        return (
            (
                "engine_control",
                ENGINE_CONTROL_BASE,
                ENGINE_CONTROL_USED_BYTES,
            ),
            ("input_state", INPUT_STATE_BASE, INPUT_STATE_BYTES),
        )
    raise ValueError(f"unknown trace read kind {kind!r}")


def _chunk_plan(
    regions: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[str, int, int], ...]:
    chunks: list[tuple[str, int, int]] = []
    for name, address, size in regions:
        if size <= 0:
            raise ValueError(f"trace read region {name} has size {size}")
        consumed = 0
        while consumed < size:
            amount = min(0xFF, size - consumed)
            chunks.append((name, address + consumed, amount))
            consumed += amount
    return tuple(chunks)


def _consume_read_plan(
    lines: list[str],
    cursor: int,
    *,
    regions: tuple[tuple[str, int, int], ...],
) -> tuple[int, dict[str, bytes]]:
    assembled = {name: bytearray() for name, _address, _size in regions}
    for name, address, amount in _chunk_plan(regions):
        command = f"> m{address:x},{amount:x}"
        if cursor >= len(lines) or lines[cursor] != command:
            observed = lines[cursor] if cursor < len(lines) else "<EOF>"
            raise ValueError(
                f"expected exact memory command {command!r}; got "
                f"{observed!r}"
            )
        cursor += 1
        if cursor >= len(lines):
            raise ValueError(f"missing response for {command!r}")
        response = lines[cursor]
        match = re.fullmatch(
            rf"< ([0-9A-F]{{{amount * 2}}})", response
        )
        if not match:
            raise ValueError(
                f"memory response for {command!r} is not exactly "
                f"{amount} uppercase hexadecimal bytes: {response!r}"
            )
        assembled[name].extend(bytes.fromhex(match.group(1)))
        cursor += 1
    result = {name: bytes(raw) for name, raw in assembled.items()}
    for name, _address, size in regions:
        if len(result[name]) != size:
            raise ValueError(
                f"trace read {name} assembled {len(result[name])}; "
                f"{size} required"
            )
    return cursor, result


def _scenario_trace_report_errors(
    raw_sections: dict[tuple[str, str], dict[str, bytes]],
    session: dict[str, Any] | None,
) -> list[str]:
    if session is None:
        return []
    errors: list[str] = []
    session_id = str(session.get("session_id", "?"))
    route = session.get("route", {})
    cadence_rows = route.get("cadence_rows", [])
    if not isinstance(cadence_rows, list):
        return [f"{session_id} cadence rows unavailable for trace binding"]
    for total, row in enumerate(cadence_rows, start=1):
        raw = raw_sections.get(("cadence", f"{total:04d}"))
        if raw is None:
            errors.append(f"{session_id} trace cadence {total} missing")
            continue
        blocks = row.get("blocks", {}) if isinstance(row, dict) else {}
        for name in CADENCE_BLOCK_SPECS:
            if raw[name].hex().upper() != blocks.get(name, {}).get(
                "raw_hex"
            ):
                errors.append(
                    f"{session_id} trace cadence {total} {name} differs "
                    "from report raw"
                )
        if total in CHECKPOINT_OBSERVATION_TOTALS:
            private_raw = raw_sections.get(("checkpoint_renderer", f"{total:04d}"), {})
            for name, _address, _size in _trace_read_plan("checkpoint_renderer"):
                if private_raw.get(name, b"").hex().upper() != row.get("checkpoint_renderer", {}).get("blocks", {}).get(name, {}).get("raw_hex"):
                    errors.append(f"{session_id} checkpoint {total} {name} trace/report mismatch")
    wait = raw_sections.get(
        ("terminal_wait", f"{EXPECTED_AUTHORITATIVE_STEPS:04d}")
    )
    wait_report = route.get("terminal_wait_observation", {})
    for name, _address, _size in _trace_read_plan("checkpoint_renderer"):
        terminal_private = raw_sections.get(("checkpoint_renderer", "terminal"), {})
        if terminal_private.get(name, b"").hex().upper() != wait_report.get("checkpoint_renderer", {}).get("blocks", {}).get(name, {}).get("raw_hex"):
            errors.append(f"{session_id} terminal checkpoint {name} trace/report mismatch")
    if wait is None:
        errors.append(f"{session_id} terminal WAIT trace read missing")
    else:
        wait_blocks = (
            wait_report.get("blocks", {})
            if isinstance(wait_report, dict)
            else {}
        )
        for name in CADENCE_BLOCK_SPECS:
            if wait[name].hex().upper() != wait_blocks.get(name, {}).get(
                "raw_hex"
            ):
                errors.append(
                    f"{session_id} terminal WAIT trace {name} differs "
                    "from report raw"
                )

    observations = session.get("observations", [])
    by_label = {
        item.get("label"): item
        for item in observations
        if isinstance(item, dict)
    }
    for label in EXPECTED_CAPTURE_LABELS:
        observation = by_label.get(label, {})
        first = raw_sections.get(("budget_first", label))
        second = raw_sections.get(("budget_second", label))
        if first is None or second is None:
            errors.append(
                f"{session_id}/{label} budget trace double-read missing"
            )
            continue
        blocks = observation.get("blocks", {})
        second_hashes = observation.get("read_consistency", {}).get(
            "second_block_sha256", {}
        )
        for name, _key, _address, _size, _parser in BLOCK_SPECS:
            if first[name].hex().upper() != blocks.get(name, {}).get(
                "raw_hex"
            ):
                errors.append(
                    f"{session_id}/{label} trace budget first {name} "
                    "differs from report raw"
                )
            if first[name] != second[name]:
                errors.append(
                    f"{session_id}/{label} trace budget second {name} "
                    "differs from first"
                )
            if _sha256_bytes(second[name]) != second_hashes.get(name):
                errors.append(
                    f"{session_id}/{label} trace budget second {name} "
                    "hash differs from report"
                )

    scenario_items = route.get("scenario_snapshots", [])
    scenario_by_label = {
        item.get("label"): item
        for item in scenario_items
        if isinstance(item, dict)
    }
    for label in EXPECTED_CAPTURE_LABELS:
        raw = raw_sections.get(("scenario", label))
        report_item = scenario_by_label.get(label, {})
        if raw is None:
            errors.append(f"{session_id}/{label} scenario trace read missing")
            continue
        blocks = report_item.get("blocks", {})
        for name, _private, _address, _size in SCENARIO_BLOCK_SPECS:
            if raw[name].hex().upper() != blocks.get(name, {}).get(
                "raw_hex"
            ):
                errors.append(
                    f"{session_id}/{label} trace scenario {name} differs "
                    "from report raw"
                )
    return errors


def audit_frame_advance_transcript(
    lines: list[str],
    *,
    session_id: str,
    vblank: int,
    post_step: int,
    finalizer: int,
    wait_for_vblank: int,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse one complete session with a strict, consuming trace grammar."""
    begin_line = f"# {session_id} begin"
    end_line = f"# {session_id} end"
    breakpoint_commands = {
        f"> Z0,{vblank:x},2": "insert_vblank",
        f"> z0,{vblank:x},2": "remove_vblank",
        f"> Z0,{post_step:x},2": "insert_post",
        f"> z0,{post_step:x},2": "remove_post",
        f"> Z0,{finalizer:x},2": "insert_finalizer",
        f"> z0,{finalizer:x},2": "remove_finalizer",
        f"> Z0,{wait_for_vblank:x},2": "insert_wait",
        f"> z0,{wait_for_vblank:x},2": "remove_wait",
    }
    events: list[tuple[Any, ...]] = []
    grammar_errors: list[str] = []
    memory_sections: dict[tuple[str, str], dict[str, bytes]] = {}
    command_count = 0
    cursor = 0
    if not lines or lines[0] != begin_line:
        grammar_errors.append(
            f"session must begin with {begin_line!r}"
        )
    else:
        events.append(("begin",))
        cursor = 1

    while cursor < len(lines):
        line = lines[cursor]
        if line == end_line:
            events.append(("end",))
            cursor += 1
            if cursor != len(lines):
                grammar_errors.append("lines follow exact session end")
            break
        memory_match = re.fullmatch(
            r"# memory\.begin kind=([a-z0-9_]+) id=([a-z0-9_]+)",
            line,
        )
        if memory_match:
            kind, identity = memory_match.groups()
            key = (kind, identity)
            if key in memory_sections:
                grammar_errors.append(
                    f"duplicate memory section {kind}/{identity}"
                )
                cursor += 1
                continue
            try:
                regions = _trace_read_plan(kind)
                next_cursor, raw = _consume_read_plan(
                    lines, cursor + 1, regions=regions
                )
                expected_end = (
                    f"# memory.end kind={kind} id={identity}"
                )
                if (
                    next_cursor >= len(lines)
                    or lines[next_cursor] != expected_end
                ):
                    observed = (
                        lines[next_cursor]
                        if next_cursor < len(lines)
                        else "<EOF>"
                    )
                    raise ValueError(
                        f"expected {expected_end!r}; got {observed!r}"
                    )
            except (KeyError, ValueError) as error:
                grammar_errors.append(
                    f"memory section {kind}/{identity}: {error}"
                )
                # A malformed read plan cannot be resynchronized safely.
                cursor = len(lines)
                break
            memory_sections[key] = raw
            command_count += len(_chunk_plan(regions))
            events.append(("memory", kind, identity))
            cursor = next_cursor + 1
            continue
        if line in breakpoint_commands:
            if cursor + 1 >= len(lines) or lines[cursor + 1] != "< OK":
                observed = (
                    lines[cursor + 1]
                    if cursor + 1 < len(lines)
                    else "<EOF>"
                )
                grammar_errors.append(
                    f"{line!r} requires immediate '< OK'; got "
                    f"{observed!r}"
                )
                cursor += 1
                continue
            events.append((breakpoint_commands[line],))
            command_count += 1
            cursor += 2
            continue
        if line == "> c":
            events.append(("continue",))
            command_count += 1
            cursor += 1
            continue
        if line == "< S05":
            events.append(("stop_response",))
            cursor += 1
            continue
        if line == "> p11":
            if cursor + 1 >= len(lines) or not re.fullmatch(
                r"< [0-9A-F]{8}", lines[cursor + 1]
            ):
                observed = (
                    lines[cursor + 1]
                    if cursor + 1 < len(lines)
                    else "<EOF>"
                )
                grammar_errors.append(
                    f"'> p11' requires immediate 8-hex response; got "
                    f"{observed!r}"
                )
                cursor += 1
                continue
            events.append(
                ("pc", lines[cursor + 1].removeprefix("< "))
            )
            command_count += 1
            cursor += 2
            continue
        if line == "> k":
            events.append(("kill",))
            command_count += 1
            cursor += 1
            continue
        sentinel_match = re.fullmatch(
            r"# cadence\.sentinel boundary=([0-9]+) title=(.+)",
            line,
        )
        if sentinel_match:
            boundary = int(sentinel_match.group(1))
            try:
                title = ast.literal_eval(sentinel_match.group(2))
                expected_title = _sentinel_for(session_id, boundary)
            except (SyntaxError, ValueError):
                title = None
                expected_title = None
            events.append(
                (
                    "sentinel"
                    if title == expected_title
                    else "invalid_sentinel",
                    boundary,
                )
            )
            cursor += 1
            continue
        pause_match = re.fullmatch(
            r"# cadence\.pause boundary=([0-9]+) title=(.+)",
            line,
        )
        if pause_match:
            boundary = int(pause_match.group(1))
            try:
                title = ast.literal_eval(pause_match.group(2))
                sentinel = _sentinel_for(session_id, boundary)
            except (SyntaxError, ValueError):
                title = None
                sentinel = None
            valid = (
                isinstance(title, str)
                and title != sentinel
                and title.upper().endswith(" - PAUSED")
            )
            events.append(
                ("pause" if valid else "invalid_pause", boundary)
            )
            cursor += 1
            continue
        origin_match = re.fullmatch(
            r"# cadence\.origin_pause title=(.+)", line
        )
        if origin_match:
            try:
                title = ast.literal_eval(origin_match.group(1))
            except (SyntaxError, ValueError):
                title = None
            events.append(
                (
                    "origin_pause"
                    if isinstance(title, str)
                    and title.upper().endswith(" - PAUSED")
                    else "invalid_origin_pause",
                )
            )
            cursor += 1
            continue
        advance_match = re.fullmatch(
            r"# ui\.advance index=([0-9]+) purpose=(\S+)", line
        )
        if advance_match:
            events.append(
                (
                    "advance",
                    int(advance_match.group(1)),
                    advance_match.group(2),
                )
            )
            cursor += 1
            continue
        stop_match = re.fullmatch(
            r"# cadence\.stop total=([0-9]+) "
            r"role=(post|finalizer|terminal_post|terminal_wait) "
            r"pc=([0-9A-F]{8})",
            line,
        )
        if stop_match:
            events.append(
                (
                    "stop_marker",
                    int(stop_match.group(1)),
                    stop_match.group(2),
                    stop_match.group(3),
                )
            )
            cursor += 1
            continue
        grammar_errors.append(
            f"unrecognized or out-of-context trace line {cursor}: {line!r}"
        )
        cursor += 1
    else:
        grammar_errors.append(f"session lacks exact end {end_line!r}")

    expected_events: list[tuple[Any, ...]] = [
        ("begin",),
        ("insert_vblank",),
        ("continue",),
        ("stop_response",),
        ("memory", "scenario", "initial"),
        ("memory", "budget_first", "initial"),
        ("memory", "budget_second", "initial"),
        ("remove_vblank",),
        ("insert_post",),
        ("insert_finalizer",),
        ("advance", 1, "origin_prime"),
        ("continue",),
        ("origin_pause",),
        ("sentinel", 0),
    ]
    for total in range(1, EXPECTED_AUTHORITATIVE_STEPS + 1):
        expected_events.extend(
            [
                (
                    "advance",
                    expected_physical_frame(total - 1) + 2,
                    f"authoritative_total_{total}",
                ),
            ]
        )
        if total in RESET_TOTALS:
            for service in range(1, RESET_SERVICE_FRAMES + 1):
                boundary = expected_physical_frame(total - 1) + service - 1
                expected_events.extend([
                    ("pause", boundary), ("sentinel", boundary + 1),
                    ("advance", boundary + 3, f"reset_{total}_service_{service}"),
                ])
        expected_events.append(("stop_response",))
        if total < EXPECTED_AUTHORITATIVE_STEPS:
            expected_events.extend(
                [
                    ("pc", f"{post_step:08X}"),
                    (
                        "stop_marker",
                        total,
                        "post",
                        f"{post_step:08X}",
                    ),
                ]
            )
        else:
            expected_events.extend(
                [
                    ("pc", f"{finalizer:08X}"),
                    (
                        "stop_marker",
                        total,
                        "finalizer",
                        f"{finalizer:08X}",
                    ),
                    ("remove_finalizer",),
                    ("continue",),
                    ("stop_response",),
                    ("pc", f"{post_step:08X}"),
                    (
                        "stop_marker",
                        total,
                        "terminal_post",
                        f"{post_step:08X}",
                    ),
                ]
            )
        expected_events.append(("memory", "cadence", f"{total:04d}"))
        if total in CHECKPOINT_OBSERVATION_TOTALS:
            expected_events.append(("memory", "checkpoint_renderer", f"{total:04d}"))
        label = _capture_label_for_total(total)
        if label is not None:
            expected_events.extend(
                [
                    ("memory", "scenario", label),
                    ("memory", "budget_first", label),
                    ("memory", "budget_second", label),
                ]
            )
        if total < EXPECTED_AUTHORITATIVE_STEPS:
            expected_events.extend(
                [
                    ("continue",),
                    ("pause", expected_physical_frame(total) - 1),
                    ("sentinel", expected_physical_frame(total)),
                ]
            )
        else:
            expected_events.extend(
                [
                    ("insert_wait",),
                    ("continue",),
                    ("stop_response",),
                    ("pc", f"{wait_for_vblank:08X}"),
                    (
                        "stop_marker",
                        total,
                        "terminal_wait",
                        f"{wait_for_vblank:08X}",
                    ),
                    (
                        "memory",
                        "terminal_wait",
                        f"{total:04d}",
                    ),
                    ("memory", "checkpoint_renderer", "terminal"),
                    ("remove_wait",),
                    ("remove_post",),
                    ("continue",),
                    ("pause", expected_physical_frame(total) - 1),
                ]
            )
    expected_events.extend([("kill",), ("end",)])

    trace_report_errors = _scenario_trace_report_errors(
        memory_sections, session
    )
    memory_digest_payload = {
        f"{kind}/{identity}": {
            name: raw.hex().upper() for name, raw in blocks.items()
        }
        for (kind, identity), blocks in sorted(memory_sections.items())
    }
    memory_pairs = sum(
        len(_chunk_plan(_trace_read_plan(kind)))
        for kind, _identity in memory_sections
    )
    exact_event_order = events == expected_events
    passed = (
        not grammar_errors
        and exact_event_order
        and not trace_report_errors
        and memory_pairs == 8121
        and command_count
        == sum(line.startswith("> ") for line in lines)
    )
    post_markers = [
        item
        for item in events
        if item[:1] == ("stop_marker",)
        and len(item) >= 3
        and item[2] in {"post", "terminal_post"}
    ]
    return {
        "status": "pass" if passed else "fail",
        "expected_authoritative_steps": EXPECTED_AUTHORITATIVE_STEPS,
        "vblank_breakpoint_insertions": events.count(
            ("insert_vblank",)
        ),
        "vblank_breakpoint_removals": events.count(
            ("remove_vblank",)
        ),
        "post_breakpoint_insertions": events.count(("insert_post",)),
        "post_breakpoint_removals": events.count(("remove_post",)),
        "finalizer_breakpoint_insertions": events.count(
            ("insert_finalizer",)
        ),
        "finalizer_breakpoint_removals": events.count(
            ("remove_finalizer",)
        ),
        "wait_breakpoint_insertions": events.count(("insert_wait",)),
        "wait_breakpoint_removals": events.count(("remove_wait",)),
        "post_stops": len(post_markers),
        "finalizer_stops": sum(
            item[:3] == (
                "stop_marker",
                EXPECTED_AUTHORITATIVE_STEPS,
                "finalizer",
            )
            for item in events
        ),
        "wait_stops": sum(
            item[:3] == (
                "stop_marker",
                EXPECTED_AUTHORITATIVE_STEPS,
                "terminal_wait",
            )
            for item in events
        ),
        "exact_post_total_sequence": [item[1] for item in post_markers]
        == list(range(1, EXPECTED_AUTHORITATIVE_STEPS + 1)),
        "exact_pc_sequence": exact_event_order,
        "sentinel_handshakes": sum(
            item[:1] == ("sentinel",) for item in events
        ),
        "exact_sentinel_boundary_sequence": [
            item[1] for item in events if item[:1] == ("sentinel",)
        ]
        == list(range(EXPECTED_PHYSICAL_FRAMES)),
        "pause_overwrites": sum(
            item[:1] == ("pause",) for item in events
        ),
        "ui_advance_pulses": sum(
            item[:1] == ("advance",) for item in events
        ),
        "exact_ui_advance_sequence": [
            item[1] for item in events if item[:1] == ("advance",)
        ]
        == list(range(1, EXPECTED_PHYSICAL_FRAMES + 2)),
        "memory_read_sections": len(memory_sections),
        "memory_read_pairs": memory_pairs,
        "memory_read_bundle_sha256": _sha256_bytes(
            _stable_json(memory_digest_payload)
        ),
        "trace_report_binding_errors": trace_report_errors,
        "trace_report_bindings_exact": not trace_report_errors,
        "transcript_command_count": command_count,
        "exact_event_order": exact_event_order,
        "protocol_event_count": len(events),
        "expected_protocol_event_count": len(expected_events),
        "malformed_protocol_lines": grammar_errors,
        "forbidden_control": [
            item for item in grammar_errors if "trace line" in item
        ],
        "debugger_write_packets": debugger_write_packets(lines),
    }


def _session_transcript_lines(
    lines: list[str], session_id: str
) -> list[str]:
    begin = f"# {session_id} begin"
    end = f"# {session_id} end"
    if lines.count(begin) != 1 or lines.count(end) != 1:
        return []
    start = lines.index(begin)
    finish = lines.index(end, start + 1)
    if finish <= start:
        return []
    return lines[start : finish + 1]


def _terminal_replay_errors(
    replay: Any,
    *,
    prefix: str,
) -> list[str]:
    if not isinstance(replay, dict):
        return [f"{prefix} terminal replay missing"]
    raw_hex = replay.get("raw_hex")
    if (
        not isinstance(raw_hex, str)
        or raw_hex != raw_hex.upper()
        or not re.fullmatch(r"[0-9A-F]+", raw_hex)
    ):
        return [f"{prefix} terminal replay raw hex invalid"]
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        return [f"{prefix} terminal replay raw hex invalid"]
    errors = []
    if len(raw) != b03.REPLAY_TIMELINE_BYTES:
        errors.append(f"{prefix} terminal replay size mismatch")
    if replay.get("bytes") != len(raw):
        errors.append(f"{prefix} terminal replay byte count mismatch")
    digest = _sha256_bytes(raw)
    if replay.get("sha256") != digest:
        errors.append(f"{prefix} terminal replay SHA-256 mismatch")
    if digest != PINNED_RUNTIME_REPLAY_SHA256:
        errors.append(f"{prefix} terminal replay is not canonical B03")
    return errors


def checkpoint_renderer_errors(session: dict[str, Any]) -> list[str]:
    """Rebuild private state from raw and require all 29 holds and 28 recoveries."""
    errors: list[str] = []
    route = session.get("route", {})
    rows = route.get("cadence_rows", [])
    observed = {row.get("authoritative_total"): row for row in rows if isinstance(row, dict) and "checkpoint_renderer" in row}
    if set(observed) != CHECKPOINT_OBSERVATION_TOTALS:
        errors.append("checkpoint renderer observation total set mismatch")
    terminal = route.get("terminal_wait_observation", {})
    checks = [(total, row, False) for total, row in observed.items()]
    checks.append((EXPECTED_AUTHORITATIVE_STEPS, terminal, True))
    for total, row, terminal_hold in checks:
        if not isinstance(total, int) or not 1 <= total <= EXPECTED_AUTHORITATIVE_STEPS:
            errors.append("checkpoint renderer total invalid")
            continue
        private = row.get("checkpoint_renderer")
        if not isinstance(private, dict) or set(private) != {"renderer", "dma_queue", "blocks"}:
            errors.append(f"checkpoint {total} private renderer proof missing/malformed")
            continue
        blocks = private.get("blocks", {})
        if not isinstance(blocks, dict) or set(blocks) != {"renderer_private", "dma_recovery"}:
            errors.append(f"checkpoint {total} private block set mismatch")
            continue
        raw = {}
        for name, address, size in _trace_read_plan("checkpoint_renderer"):
            payload, block_errors = _public_block_raw(blocks.get(name), name=name, address=address, size=size)
            errors.extend(f"checkpoint {total}: {error}" for error in block_errors)
            if payload is not None:
                raw[name] = payload
        if len(raw) != 2:
            continue
        renderer = {**parse_renderer_state(raw["renderer_private"][:RENDERER_STATE_USED_BYTES]), "last_checkpoint_count": _u16(raw["renderer_private"], 0x24)}
        dma = parse_dma_queue_control(raw["dma_recovery"][:DMA_QUEUE_CONTROL_BYTES])
        if private["renderer"] != renderer or private["dma_queue"] != dma:
            errors.append(f"checkpoint {total} parsed private state mismatch")
        hold = terminal_hold or total - 1 in CHECKPOINT_HOLD_TOTALS
        source = total if terminal_hold else total - (1 if hold else 2)
        if source not in CHECKPOINT_HOLD_TOTALS:
            errors.append(f"checkpoint {total} source mismatch")
            continue
        expected = expected_route_for_total(source)
        expected_tick = (719 if source in RESET_TOTALS else expected["tick"] - 1) if hold else expected_route_for_total(total)["tick"] - 1
        if (
            renderer["magic"] != RENDERER_MAGIC or renderer["version"] != RENDERER_VERSION
            or renderer["bytes"] != RENDERER_BYTES or not renderer["flags"] & RENDERER_FLAG_INITIALIZED
            or renderer["flags"] & RENDERER_FAULT_FLAGS or renderer["last_result"] != (4 if hold else 0)
            or renderer["last_attempt"] != expected["attempt"] or renderer["last_tick"] != expected_tick
            or renderer["last_checkpoint_count"] != expected_checkpoint_for_total(source)[1]
            or not 0 <= renderer["sprite_count"] <= RENDERER_SPRITE_LIMIT
            or not 0 <= renderer["scanline_max"] <= RENDERER_SCANLINE_SPRITE_LIMIT
            or not 0 <= renderer["scanline_pixel_max"] <= RENDERER_SCANLINE_PIXEL_LIMIT
            or any(renderer[key] for key in ("dropped_sprites", "dma_rejects", "backlog_frames"))
        ):
            errors.append(f"checkpoint {total} hold/recovery renderer mismatch")
        words = (440 if terminal_hold else 0) if hold else (480 if source in RESET_TOTALS else 440)
        if dma != {"published_depth": 0, "build_depth": 0, "build_words": 0, "commit_words": words, "frame_pending": 0, "reserved_0": 0, "reserved_1": 0}:
            errors.append(f"checkpoint {total} exact empty/committed queue mismatch")
        if row.get("observed", {}).get("debug", {}).get("dma_words_frame") != words:
            errors.append(f"checkpoint {total} DMA Debug mirror mismatch")
        if not hold:
            expected_records = [
                (0xFF8000, 0xF000, 320, 0xC000, 0),
                (0xFF8600, 0xC000, 40, 0xC001, 0),
                (0xFF8650, 0xC080, 40, 0xC001, 0),
                (0xFF86A0, 0xC100, 40, 0xC001, 0),
            ]
            if source in RESET_TOTALS:
                expected_records.append((0xFF8740, renderer["overlay_trace_destination"], 40, 0xC002, 0))
                if renderer["overlay_trace_destination"] != 0xC180:
                    errors.append(f"checkpoint {total} reset trace destination mismatch")
            records = raw["dma_recovery"][DMA_QUEUE_CONTROL_BYTES:]
            for index, record in enumerate(expected_records):
                offset = index * 12
                actual = (_u32(records, offset), *(_u16(records, offset + delta) for delta in (4, 6, 8, 10)))
                if actual != record:
                    errors.append(f"checkpoint {total} recovery record {index} mismatch")
    return errors


def session_cadence_errors(session: dict[str, Any]) -> list[str]:
    session_id = str(session.get("session_id", "?"))
    route = session.get("route")
    if not isinstance(route, dict):
        return [f"{session_id} route missing"]
    rows = route.get("cadence_rows")
    if not isinstance(rows, list):
        return [f"{session_id} cadence rows missing"]
    errors: list[str] = []
    errors.extend(checkpoint_renderer_errors(session))
    if len(rows) != EXPECTED_AUTHORITATIVE_STEPS:
        errors.append(
            f"{session_id} cadence row count {len(rows)} != "
            f"{EXPECTED_AUTHORITATIVE_STEPS}"
        )
    if [
        row.get("authoritative_total")
        for row in rows
        if isinstance(row, dict)
    ] != list(range(1, EXPECTED_AUTHORITATIVE_STEPS + 1)):
        errors.append(f"{session_id} cadence totals are not exact")
    for row in rows:
        if not isinstance(row, dict):
            errors.append(f"{session_id} cadence row is not an object")
            continue
        errors.extend(
            f"{session_id}: {error}"
            for error in cadence_row_errors(row)
        )
    crc_by_checkpoint: dict[tuple[int, int, int], int] = {}
    last_checkpoint_by_attempt: dict[int, tuple[tuple[int, int, int], int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            engine = row["observed"]["engine"]
            key = (
                engine["attempt"],
                engine["checkpoint_tick"],
                engine["checkpoint_count"],
            )
            crc = engine["state_crc"]
        except (KeyError, TypeError):
            continue
        if key in crc_by_checkpoint and crc_by_checkpoint[key] != crc:
            errors.append(
                f"{session_id} checkpoint {key} state CRC changed "
                "between publications"
            )
        crc_by_checkpoint.setdefault(key, crc)
        previous = last_checkpoint_by_attempt.get(engine["attempt"])
        if previous is not None and previous[0] != key and previous[1] == crc:
            errors.append(
                f"{session_id} checkpoint {key} did not publish a new "
                "state CRC"
            )
        last_checkpoint_by_attempt[engine["attempt"]] = (key, crc)

    scenario_items = route.get("scenario_snapshots")
    scenario_by_label = {
        item.get("label"): item
        for item in scenario_items
        if isinstance(item, dict)
    } if isinstance(scenario_items, list) else {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        total = row.get("authoritative_total")
        if not isinstance(total, int):
            continue
        label = _capture_label_for_total(total)
        if label is None:
            continue
        scenario = scenario_by_label.get(label, {})
        scenario_blocks = scenario.get("blocks", {})
        cadence_blocks = row.get("blocks", {})
        for name in ("engine_control",):
            if scenario_blocks.get(name, {}).get("raw_hex") != (
                cadence_blocks.get(name, {}).get("raw_hex")
            ):
                errors.append(
                    f"{session_id}/{label} cadence {name} differs from "
                    "same-stop scenario raw"
                )
    terminal_wait = route.get("terminal_wait_observation")
    if not isinstance(terminal_wait, dict):
        errors.append(f"{session_id} terminal WAIT observation missing")
    else:
        errors.extend(
            f"{session_id} terminal WAIT: {error}"
            for error in cadence_row_errors(terminal_wait)
        )
        if terminal_wait.get("authoritative_total") != (
            EXPECTED_AUTHORITATIVE_STEPS
        ):
            errors.append(
                f"{session_id} terminal WAIT total mismatch"
            )
        if rows and isinstance(rows[-1], dict):
            terminal_post_blocks = rows[-1].get("blocks", {})
            terminal_wait_blocks = terminal_wait.get("blocks", {})
            for block_name in (
                "engine_control",
                "debug_budget",
                "input_state",
            ):
                if (
                    terminal_wait_blocks.get(block_name, {}).get("sha256")
                    != terminal_post_blocks.get(block_name, {}).get("sha256")
                ):
                    errors.append(
                        f"{session_id} terminal WAIT {block_name} "
                        "changed after terminal POST"
                    )
    excesses = {
        row.get("observed", {}).get("z80_vint_excess")
        for row in rows
        if isinstance(row, dict)
    }
    if isinstance(terminal_wait, dict):
        excesses.add(
            terminal_wait.get("observed", {}).get("z80_vint_excess")
        )
    if (
        len(excesses) != 1
        or not all(isinstance(item, int) for item in excesses)
        or not all(
            0 <= item <= Z80_VINT_STARTUP_ALLOWANCE
            for item in excesses
            if isinstance(item, int)
        )
    ):
        errors.append(f"{session_id} Z80 excess is not invariant/bounded")
    errors.extend(
        _terminal_replay_errors(
            route.get("terminal_replay"), prefix=session_id
        )
    )
    commits = route.get("timeout_commits")
    schedules = (b03.ATTEMPT_0_INPUTS, b03.ATTEMPT_1_INPUTS)
    if not isinstance(commits, list) or len(commits) != 2:
        errors.append(f"{session_id} timeout commit vector mismatch")
    else:
        for attempt, schedule in enumerate(schedules):
            item = commits[attempt]
            expected_bank = 6 if attempt == 0 else 0
            if (
                not isinstance(item, dict)
                or item.get("status") != "pass"
                or item.get("attempt") != attempt
                or item.get("authoritative_total")
                != (attempt + 1) * 720
                or item.get("committed_bank") != expected_bank
                or item.get("descriptor_length") != len(schedule)
                or item.get("descriptor_crc32_iso_hdlc")
                != b03.crc32_iso_hdlc(schedule)
                or not isinstance(item.get("descriptor_flags"), int)
                or item["descriptor_flags"]
                & (b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO)
                != (b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO)
                or item.get("committed_input_sha256")
                != _sha256_bytes(schedule)
                or item.get("committed_input_raw_hex")
                != schedule.hex().upper()
            ):
                errors.append(
                    f"{session_id} timeout commit {attempt} mismatch"
                )
    metadata = session.get("metadata")
    if not isinstance(metadata, dict):
        return [*errors, f"{session_id} metadata missing"]
    audit = metadata.get("frame_advance_audit")
    required_audit = {
        "status": "pass",
        "expected_authoritative_steps": EXPECTED_AUTHORITATIVE_STEPS,
        "vblank_breakpoint_insertions": 1,
        "vblank_breakpoint_removals": 1,
        "post_breakpoint_insertions": 1,
        "post_breakpoint_removals": 1,
        "finalizer_breakpoint_insertions": 1,
        "finalizer_breakpoint_removals": 1,
        "wait_breakpoint_insertions": 1,
        "wait_breakpoint_removals": 1,
        "post_stops": EXPECTED_AUTHORITATIVE_STEPS,
        "finalizer_stops": 1,
        "wait_stops": 1,
        "exact_post_total_sequence": True,
        "exact_pc_sequence": True,
        "sentinel_handshakes": EXPECTED_PHYSICAL_FRAMES,
        "exact_sentinel_boundary_sequence": True,
        "pause_overwrites": EXPECTED_PHYSICAL_FRAMES,
        "ui_advance_pulses": EXPECTED_PHYSICAL_FRAMES + 1,
        "exact_ui_advance_sequence": True,
        "memory_read_sections": 1871,
        "memory_read_pairs": 8121,
        "trace_report_binding_errors": [],
        "trace_report_bindings_exact": True,
        "transcript_command_count": 11600,
        "exact_event_order": True,
        "protocol_event_count": 14031,
        "expected_protocol_event_count": 14031,
        "malformed_protocol_lines": [],
        "forbidden_control": [],
        "debugger_write_packets": [],
    }
    if not isinstance(audit, dict) or any(
        audit.get(key) != value
        for key, value in required_audit.items()
    ):
        errors.append(f"{session_id} frame-advance audit mismatch")
    elif not _valid_sha256(audit.get("memory_read_bundle_sha256")):
        errors.append(
            f"{session_id} frame-advance memory bundle hash invalid"
        )
    origin_pause = metadata.get("origin_pause")
    if (
        not isinstance(origin_pause, dict)
        or origin_pause.get("status") != "pass"
        or not isinstance(origin_pause.get("initial_title"), str)
        or origin_pause["initial_title"].upper().endswith(" - PAUSED")
        or not isinstance(origin_pause.get("observed_title"), str)
        or not origin_pause["observed_title"]
        .upper()
        .endswith(" - PAUSED")
        or origin_pause.get("timeout_seconds")
        != PAUSE_OVERWRITE_TIMEOUT_SECONDS
        or not isinstance(
            origin_pause.get("elapsed_milliseconds"), (int, float)
        )
        or not math.isfinite(origin_pause["elapsed_milliseconds"])
        or not 0.0
        <= origin_pause["elapsed_milliseconds"]
        <= PAUSE_OVERWRITE_TIMEOUT_SECONDS * 1000.0
        or not isinstance(origin_pause.get("samples"), int)
        or origin_pause["samples"] < 1
    ):
        errors.append(f"{session_id} origin pause proof mismatch")
    handshakes = metadata.get("sentinel_handshakes")
    if (
        not isinstance(handshakes, list)
        or len(handshakes) != EXPECTED_PHYSICAL_FRAMES
    ):
        errors.append(f"{session_id} sentinel handshake count mismatch")
    else:
        for boundary, handshake in enumerate(handshakes):
            sentinel = _sentinel_for(session_id, boundary)
            if (
                not isinstance(handshake, dict)
                or handshake.get("status") != "pass"
                or handshake.get("boundary_total") != boundary
                or handshake.get("sentinel") != sentinel
                or handshake.get("sentinel_readback") != sentinel
                or handshake.get("set_window_text")
                != "SendMessageTimeoutW(WM_SETTEXT,SMTO_ABORTIFHUNG)"
                or handshake.get("set_timeout_seconds")
                != SET_TITLE_TIMEOUT_SECONDS
                or not isinstance(
                    handshake.get("set_elapsed_milliseconds"),
                    (int, float),
                )
                or not math.isfinite(
                    handshake["set_elapsed_milliseconds"]
                )
                or not 0.0
                <= handshake["set_elapsed_milliseconds"]
                <= SET_TITLE_TIMEOUT_SECONDS * 1000.0
                or not isinstance(
                    handshake.get("observed_title"), str
                )
                or handshake["observed_title"] == sentinel
                or not handshake["observed_title"]
                .upper()
                .endswith(" - PAUSED")
                or handshake.get("timeout_seconds")
                != PAUSE_OVERWRITE_TIMEOUT_SECONDS
                or not isinstance(
                    handshake.get("elapsed_milliseconds"),
                    (int, float),
                )
                or not math.isfinite(
                    handshake["elapsed_milliseconds"]
                )
                or not 0.0
                <= handshake["elapsed_milliseconds"]
                <= PAUSE_OVERWRITE_TIMEOUT_SECONDS * 1000.0
                or not isinstance(handshake.get("samples"), int)
                or handshake["samples"] < 1
            ):
                errors.append(
                    f"{session_id} sentinel handshake {boundary} mismatch"
                )
                break
    advances = metadata.get("ui_advance_events")
    if (
        not isinstance(advances, list)
        or len(advances) != EXPECTED_PHYSICAL_FRAMES + 1
        or [item.get("index") for item in advances]
        != list(range(1, EXPECTED_PHYSICAL_FRAMES + 2))
        or [(item.get("index"), item.get("purpose")) for item in advances] != expected_advance_events()
        or any(
            item.get("key") != "F8"
            or item.get("complete_down_up_pulse") is not True
            for item in advances
        )
    ):
        errors.append(f"{session_id} UI advance event vector mismatch")
    if metadata.get("frame_advance_source_contract") != {
        "version": BLASTEM_VERSION,
        "source_node": BLASTEM_SOURCE_NODE,
        "source_sha256": BLASTEM_SOURCE_HASHES,
    }:
        errors.append(f"{session_id} pinned frame source mismatch")
    proof = route.get("schedule_proof", {})
    if (
        proof.get("status") != "pass"
        or proof.get("total_steps") != EXPECTED_AUTHORITATIVE_STEPS
        or proof.get("combined_sha256")
        != COMBINED_SCHEDULE_SHA256
    ):
        errors.append(f"{session_id} schedule proof mismatch")
    return errors


def build_budget_report(
    *,
    sessions: list[dict[str, Any]],
    provenance: dict[str, Any],
    prerequisite: dict[str, Any],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    """Purely evaluate captured state; expected outcomes are never injected."""
    checks: list[dict[str, Any]] = []
    observations = _all_observations(sessions)
    all_lines = [
        line
        for session in sessions
        for line in session.get("_transcript_lines", [])
    ]
    writes = debugger_write_packets(all_lines)

    checks.append(
        _check(
            "canonical_b03_runtime_prerequisite",
            prerequisite.get("status") == "pass"
            and prerequisite.get("report_sha256")
            == PINNED_RUNTIME_REPORT_SHA256
            and prerequisite.get("trace_sha256")
            == PINNED_RUNTIME_TRACE_SHA256
            and prerequisite.get("replay_sha256")
            == PINNED_RUNTIME_REPLAY_SHA256,
            "bound prerequisite is PASS/PASS with the root-approved report, "
            "transcript, replay, ROM, and symbol hashes",
        )
    )
    checks.append(
        _check(
            "frozen_schedule_and_checkpoint_contract",
            schedule.get("status") == "pass"
            and schedule.get("attempt_lengths") == [720, 720, 292]
            and schedule.get("attempt_sha256") == list(SCHEDULE_SHA256)
            and schedule.get("combined_sha256")
            == COMBINED_SCHEDULE_SHA256
            and schedule.get("capture_ticks_sha256")
            == CAPTURE_TICKS_SHA256
            and schedule.get("checkpoint_keys_sha256")
            == CHECKPOINT_KEYS_SHA256
            and schedule.get("expected_capture_labels")
            == list(EXPECTED_CAPTURE_LABELS),
            "canonical imported schedules and checkpoint definitions match "
            "the frozen host hashes",
        )
    )

    pids = {
        session.get("metadata", {}).get("process_id")
        for session in sessions
    }
    runtimes = {
        session.get("metadata", {})
        .get("runtime_copy", {})
        .get("temporary_directory")
        for session in sessions
    }
    checks.append(
        _check(
            "two_fresh_owned_route_sessions",
            len(sessions) == SESSION_COUNT
            and tuple(
                session.get("session_id") for session in sessions
            )
            == EXPECTED_SESSION_IDS
            and len(pids) == SESSION_COUNT
            and all(isinstance(pid, int) and pid > 0 for pid in pids)
            and len(runtimes) == SESSION_COUNT
            and all(isinstance(item, str) and item for item in runtimes)
            and all(
                session.get("route", {}).get("status") == "pass"
                and all(
                    session["route"]
                    .get("scenario_facts", {})
                    .values()
                )
                for session in sessions
            ),
            f"session IDs {[item.get('session_id') for item in sessions]}; "
            f"owned PIDs {sorted(pids) if all(isinstance(x, int) for x in pids) else pids}",
        )
    )

    drive_ok = len(sessions) == SESSION_COUNT and not writes
    cadence_error_map: dict[str, list[str]] = {}
    for session in sessions:
        metadata = session.get("metadata", {})
        proof = session.get("route", {}).get("schedule_proof", {})
        cadence_errors = session_cadence_errors(session)
        cadence_error_map[str(session.get("session_id"))] = (
            cadence_errors
        )
        drive_ok = (
            drive_ok
            and not cadence_errors
            and metadata.get("session_mode") == "positive"
            and metadata.get("input_transport") == "targeted_PostMessageW"
            and metadata.get("frame_advance_transport")
            == "targeted_PostMessageW_F8_ui.advance"
            and metadata.get("launch_flags") == list(b03.LAUNCH_FLAGS)
            and proof.get("status") == "pass"
            and proof.get("total_steps") == EXPECTED_AUTHORITATIVE_STEPS
            and proof.get("combined_sha256")
            == COMBINED_SCHEDULE_SHA256
        )
    checks.append(
        _check(
            "exact_read_only_frame_advance_post_drive",
            drive_ok,
            "each session proves 1736 sentinel/PAUSED/F8 handshakes and "
            "1732 exact-POST "
            "transitions with one permanent POST breakpoint and one "
            "same-frame finalizer-to-terminal POST; "
            f"G/M/P/X packets {writes}; cadence errors {cadence_error_map}",
        )
    )

    stable_route_ok = (
        len(observations)
        == SESSION_COUNT * len(EXPECTED_CAPTURE_LABELS)
        and all(
            [item["label"] for item in session["observations"]]
            == list(EXPECTED_CAPTURE_LABELS)
            for session in sessions
        )
        and all(
            observation.get("read_consistency", {}).get("status")
            == "pass"
            and observation["engine"]["state"]
            == observation["route"]["expected_state"]
            and observation["engine"]["attempt"]
            == observation["route"]["expected_attempt"]
            and observation["engine"]["tick"]
            == observation["route"]["expected_tick"]
            for observation in observations
        )
    )
    checks.append(
        _check(
            "stable_route_aligned_observations",
            stable_route_ok,
            f"{len(observations)} double-read observations at exact canonical "
            "route positions",
        )
    )

    checks.append(
        _check(
            "runtime_abi_and_clock_health",
            bool(observations)
            and all(_observation_abi_clock_ok(item) for item in observations),
            "engine/Debug/renderer ABIs, authoritative totals, terminal "
            "reason, even Debug sequence, frame token, and boot stage valid",
        )
    )

    fault_records = [
        {
            "session_id": session["session_id"],
            "label": observation["label"],
            **_observation_fault_values(observation),
        }
        for session in sessions
        for observation in session["observations"]
    ]
    all_faults_zero = bool(fault_records) and all(
        all(
            value == 0
            for key, value in record.items()
            if key not in {"session_id", "label"}
        )
        for record in fault_records
    )
    checks.append(
        _check(
            "all_cumulative_fault_counters_zero",
            all_faults_zero,
            "all renderer/DMA/VBlank/Z80/audio/engine/vocabulary cumulative "
            f"fault fields are zero at {len(fault_records)} checkpoints",
        )
    )

    pool_budget_ok = (
        _pool_layout_static_ok()
        and bool(observations)
        and all(
            _observation_pool_ok(item)
            and _observation_vocabulary_budget_ok(item)
            for item in observations
        )
    )
    checks.append(
        _check(
            "bounded_pool_and_vocabulary_budget",
            pool_budget_ok,
            "all seven fixed pool counts/high-water values match complete "
            "slot scans inside Genesis work RAM; vocabulary update units "
            "are 0 at initial/timeout-reset boundaries and "
            f"{VOCAB_CPU_WORST_UNITS} at ordinary POST checkpoints, "
            f"within {VOCAB_CPU_UNIT_LIMIT}",
        )
    )

    capacity_ok = (
        bool(observations)
        and all(_observation_capacity_ok(item) for item in observations)
        and any(
            item["renderer"]["sprite_count"] > 0
            and item["debug"]["dma_words_max"] > 0
            for item in observations
        )
    )
    checks.append(
        _check(
            "renderer_and_dma_capacities_in_bounds",
            capacity_ok,
            "observed SAT/scanline/pixel/DMA word/depth values are within "
            f"{RENDERER_SPRITE_LIMIT}/{RENDERER_SCANLINE_SPRITE_LIMIT}/"
            f"{RENDERER_SCANLINE_PIXEL_LIMIT}/{DMA_ORDINARY_WORD_LIMIT}/"
            f"{DMA_QUEUE_CAPACITY} published conservative limits",
        )
    )

    checks.append(
        _check(
            "exact_checkpoint_holds_and_recovery",
            len(sessions) == SESSION_COUNT and all(not checkpoint_renderer_errors(session) for session in sessions),
            "exact 26 regular, two reset, and one terminal checkpoint holds; "
            "28 following commits contain the exact processed four/440-word "
            "or five/480-word records; all other cadence rows transfer DMA",
        )
    )

    checks.append(
        _check(
            "audio_z80_health_and_cross_clock",
            bool(observations)
            and all(
                _observation_audio_clock_ok(item)
                for item in observations
            ),
            "playing audio shadow is healthy with zero late/timeout faults; "
            "the stage-3 zero shadow and reset-delayed command/layer "
            "transitions are exact; Z80 V-int excess is invariant and "
            "within the published startup allowance",
        )
    )

    worst_case_ok = all(
        all(
            label
            in {
                observation["label"]
                for observation in session["observations"]
            }
            for label in MEANINGFUL_WORST_CASE_LABELS
        )
        and session["observations"][-1]["label"] == "terminal"
        and session["observations"][-1]["engine"]["state"]
        == ENGINE_STATE_TERMINAL
        for session in sessions
    )
    checks.append(
        _check(
            "meaningful_worst_case_and_terminal_coverage",
            len(sessions) == SESSION_COUNT and worst_case_ok,
            "switch, echo-projectile/node, two-echo closed/open leaf, dash, "
            "terminal-hold start/late, and PROGRAM_STABLE terminal snapshots "
            "are present in both sessions",
        )
    )

    signature_records = [
        _signature_record(session) for session in sessions
    ]
    signatures_equal = (
        len(signature_records) == SESSION_COUNT
        and signature_records[0]["sha256"]
        == signature_records[1]["sha256"]
    )
    checks.append(
        _check(
            "exact_two_session_budget_signatures",
            signatures_equal,
            f"budget signatures {signature_records}",
        )
    )

    checks.append(
        _check(
            "exact_hash_bindings_and_no_drift",
            _hash_bindings_valid(sessions, provenance),
            "ROM, symbols, canonical runtime prerequisite, emulator/config, "
            "B03 collector, budget collector/schema/probe, isolated copies, "
            "and transcript are SHA-256 bound and unchanged",
        )
    )

    passed = (
        len(sessions) == SESSION_COUNT
        and tuple(check["check"] for check in checks)
        == REQUIRED_CHECK_NAMES
        and all(check["status"] == "pass" for check in checks)
    )
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5/B03",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "acceptance_status": "pass" if passed else "incomplete",
        "rom_sha256": provenance.get("rom", {}).get("sha256", ""),
        "symbol_map_sha256": provenance.get("symbol_map", {}).get(
            "sha256", ""
        ),
        "checks": checks,
        "published_limits": published_limits_contract(),
        "schedule_contract": schedule,
        "runtime_prerequisite": prerequisite,
        "meaningful_worst_case_labels": list(
            MEANINGFUL_WORST_CASE_LABELS
        ),
        "budget_signatures": signature_records,
        "sessions": [_public(session) for session in sessions],
        "provenance": provenance,
        "debugger_write_packets": writes,
        "limitations": list(LIMITATIONS),
    }


def _observation_integrity_errors(
    observation: dict[str, Any],
    *,
    session_id: str,
) -> list[str]:
    errors: list[str] = []
    label = observation.get("label")
    prefix = f"{session_id}/{label}"
    if label not in EXPECTED_CAPTURE_LABELS:
        return [f"{prefix} unknown checkpoint label"]
    expected_names = {spec[0] for spec in BLOCK_SPECS}
    blocks = observation.get("blocks")
    if not isinstance(blocks, dict) or set(blocks) != expected_names:
        return [f"{prefix} raw block set mismatch"]
    raw_by_name: dict[str, bytes] = {}
    for block_name, _key, address, size, _parser in BLOCK_SPECS:
        block = blocks.get(block_name)
        if not isinstance(block, dict):
            errors.append(f"{prefix} block {block_name} missing")
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
        if block.get("sha256") != _sha256_bytes(raw):
            errors.append(f"{prefix} block {block_name} SHA-256 mismatch")
        raw_by_name[block_name] = raw
    if set(raw_by_name) != expected_names:
        return errors
    consistency = observation.get("read_consistency", {})
    first_hashes = consistency.get("first_block_sha256")
    second_hashes = consistency.get("second_block_sha256")
    if (
        consistency.get("status") != "pass"
        or not isinstance(first_hashes, dict)
        or not isinstance(second_hashes, dict)
        or set(first_hashes) != expected_names
        or first_hashes != second_hashes
        or first_hashes
        != {
            name: blocks[name]["sha256"]
            for name in expected_names
        }
    ):
        errors.append(f"{prefix} stopped double-read consistency failed")
    rebuilt = build_observation_from_raw(
        label=label,
        raw_by_name=raw_by_name,
        second_hashes=second_hashes,
    )
    for key in (
        "route",
        "engine",
        "debug",
        "renderer",
        "dma_queue",
        "audio_shadow",
        "vocabulary",
        "pools",
        "cross_clock",
        "blocks",
        "read_consistency",
    ):
        if observation.get(key) != rebuilt.get(key):
            errors.append(f"{prefix} parsed field {key} mismatch")
    return errors


def _scenario_snapshot_from_evidence(
    item: dict[str, Any],
    *,
    symbols: dict[str, int],
) -> dict[str, Any]:
    label = item.get("label")
    if label not in EXPECTED_CAPTURE_LABELS:
        raise ValueError(f"unknown scenario snapshot label {label!r}")
    blocks = item.get("blocks")
    expected_names = {name for name, *_rest in SCENARIO_BLOCK_SPECS}
    if not isinstance(blocks, dict) or set(blocks) != expected_names:
        raise ValueError(f"{label} scenario raw block set mismatch")
    raw_by_name: dict[str, bytes] = {}
    bundle = bytearray()
    for name, _private, address, size in SCENARIO_BLOCK_SPECS:
        raw, errors = _public_block_raw(
            blocks.get(name), name=name, address=address, size=size
        )
        if errors or raw is None:
            raise ValueError(
                f"{label} scenario {name}: {'; '.join(errors)}"
            )
        raw_by_name[name] = raw
        bundle.extend(raw)
    if item.get("raw_bundle_sha256") != _sha256_bytes(bytes(bundle)):
        raise ValueError(f"{label} scenario raw bundle SHA-256 mismatch")
    return b03.make_snapshot(
        label=label,
        control_raw=raw_by_name["engine_control"],
        gameplay_raw=raw_by_name["gameplay_control"],
        vocabulary_raw=raw_by_name["vocabulary_state"],
        descriptor_raw=raw_by_name["encounter_descriptor"],
        rectangle_raw=raw_by_name["canonical_rectangles"],
        device_raw=raw_by_name["device_pool"],
        projectile_raw=raw_by_name["projectile_pool"],
        hazard_raw=raw_by_name["hazard_pool"],
        actor_raw=raw_by_name["actor_slots"],
        input_raw=raw_by_name["input_state"],
        replay_raw=raw_by_name["replay_timeline"],
        projection_raw=raw_by_name["campaign_projection"],
        symbols=symbols,
    )


def _public_required_symbols(
    provenance: Any,
) -> tuple[dict[str, int] | None, list[str]]:
    if not isinstance(provenance, dict):
        return None, ["provenance missing for scenario raw validation"]
    public = provenance.get("required_symbols")
    if not isinstance(public, dict) or set(public) != set(REQUIRED_SYMBOLS):
        return None, ["required-symbol public map mismatch"]
    symbols: dict[str, int] = {}
    for name in REQUIRED_SYMBOLS:
        value = public.get(name)
        if not isinstance(value, str) or not re.fullmatch(
            r"[0-9A-F]{8}", value
        ):
            return None, [f"required symbol {name} is not 8-hex"]
        symbols[name] = int(value, 16)
    layout = symbol_layout_errors(symbols)
    if layout:
        return None, [f"required-symbol layout mismatch: {layout[:3]}"]
    return symbols, []


def _scenario_integrity_errors(
    session: dict[str, Any],
    *,
    provenance: Any,
    symbols_override: dict[str, int] | None = None,
) -> list[str]:
    session_id = str(session.get("session_id", "?"))
    route = session.get("route", {})
    items = route.get("scenario_snapshots")
    if (
        not isinstance(items, list)
        or [item.get("label") for item in items if isinstance(item, dict)]
        != list(EXPECTED_CAPTURE_LABELS)
    ):
        return [f"{session_id} scenario snapshot labels mismatch"]
    if symbols_override is None:
        symbols, errors = _public_required_symbols(provenance)
        if symbols is None:
            return [f"{session_id}: {item}" for item in errors]
    else:
        missing = [
            name for name in REQUIRED_SYMBOLS
            if name not in symbols_override
        ]
        if missing:
            return [
                f"{session_id}: live required symbols missing {missing}"
            ]
        symbols = {
            name: symbols_override[name] for name in REQUIRED_SYMBOLS
        }
        errors = []
    snapshots: dict[str, dict[str, Any]] = {}
    for item in items:
        try:
            snapshot = _scenario_snapshot_from_evidence(
                item, symbols=symbols
            )
        except (EvidenceFailure, KeyError, TypeError, ValueError) as error:
            errors.append(
                f"{session_id}/{item.get('label')} scenario raw invalid: "
                f"{error}"
            )
            continue
        label = snapshot["label"]
        expected_state, expected_attempt, expected_tick = _route_position(
            label
        )
        engine = snapshot["engine"]
        if (
            engine["state"] != expected_state
            or engine["attempt"] != expected_attempt
            or engine["tick"] != expected_tick
        ):
            errors.append(
                f"{session_id}/{label} scenario coordinate mismatch"
            )
        snapshots[label] = snapshot
    if set(snapshots) != set(EXPECTED_CAPTURE_LABELS):
        return errors
    if not b03.initialization_ok(snapshots["initial"]):
        errors.append(f"{session_id} scenario initial state invalid")
    recomputed_facts = b03._scenario_facts(
        {
            "snapshots": snapshots,
            "attempt_inputs": {
                "0": list(b03.ATTEMPT_0_INPUTS),
                "1": list(b03.ATTEMPT_1_INPUTS),
                "2": list(b03.ATTEMPT_2_INPUTS),
            },
        }
    )
    if (
        route.get("scenario_facts") != recomputed_facts
        or not recomputed_facts
        or not all(recomputed_facts.values())
    ):
        errors.append(f"{session_id} scenario facts do not reparse from raw")
    expected_commits: list[dict[str, Any]] = []
    try:
        expected_commits.append(
            _verify_timeout_commit(
                snapshots["post_timeout_1"],
                attempt=0,
                schedule=b03.ATTEMPT_0_INPUTS,
            )
        )
        expected_commits.append(
            _verify_timeout_commit(
                snapshots["post_timeout_2"],
                attempt=1,
                schedule=b03.ATTEMPT_1_INPUTS,
            )
        )
    except (EvidenceFailure, KeyError, TypeError, ValueError) as error:
        errors.append(f"{session_id} raw timeout commit invalid: {error}")
    if route.get("timeout_commits") != expected_commits:
        errors.append(
            f"{session_id} timeout commits do not reparse from scenario raw"
        )
    replay = route.get("terminal_replay", {})
    terminal_raw = snapshots["terminal"]["_replay_bytes"]
    if (
        replay.get("raw_hex") != terminal_raw.hex().upper()
        or replay.get("sha256") != _sha256_bytes(terminal_raw)
        or replay.get("bytes") != len(terminal_raw)
    ):
        errors.append(
            f"{session_id} terminal replay differs from scenario raw"
        )
    return errors


def _source_bindings_errors(
    binding: Any,
    *,
    root: Path | None,
) -> list[str]:
    if not isinstance(binding, dict):
        return ["source bindings missing"]
    sources = binding.get("sources")
    expected_paths = set(b03.SOURCE_BINDING_PATHS)
    if not isinstance(sources, dict) or set(sources) != expected_paths:
        return ["source binding path set mismatch"]
    if any(not _valid_sha256(value) for value in sources.values()):
        return ["source binding contains invalid SHA-256"]
    canonical = _stable_json(sources)
    if binding.get("bundle_sha256") != _sha256_bytes(canonical):
        return ["source binding bundle SHA-256 mismatch"]
    if root is not None:
        try:
            live = b03._source_bindings(root)
        except (EvidenceFailure, OSError) as error:
            return [f"live source binding validation failed: {error}"]
        if binding != live:
            return ["source bindings differ from live repository inputs"]
    return []


def report_contract_errors(
    report: dict[str, Any],
    root: Path | None = None,
) -> list[str]:
    """Validate public evidence without trusting its pass booleans."""
    errors: list[str] = []
    if report.get("schema") != REPORT_SCHEMA:
        errors.append("schema mismatch")
    if report.get("scope") != "Milestone5/B03":
        errors.append("scope mismatch")
    if report.get("evidence_type") != EVIDENCE_TYPE:
        errors.append("evidence type mismatch")
    if report.get("status") != "pass":
        errors.append("status is not pass")
    if report.get("acceptance_status") != "pass":
        errors.append("acceptance status is not pass")
    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or tuple(
            check.get("check") if isinstance(check, dict) else None
            for check in checks
        )
        != REQUIRED_CHECK_NAMES
        or any(
            not isinstance(check, dict)
            or check.get("status") != "pass"
            for check in checks
        )
    ):
        errors.append("required passing check vector mismatch")
    if report.get("debugger_write_packets") != []:
        errors.append("debugger write packet list is not empty")
    if report.get("limitations") != LIMITATIONS:
        errors.append("limitations drifted")
    if report.get("published_limits") != published_limits_contract():
        errors.append("published limits drifted")
    if report.get("meaningful_worst_case_labels") != list(
        MEANINGFUL_WORST_CASE_LABELS
    ):
        errors.append("meaningful worst-case labels drifted")
    if report.get("rom_sha256") != PINNED_ROM_SHA256:
        errors.append("ROM hash mismatch")
    if report.get("symbol_map_sha256") != PINNED_SYMBOL_SHA256:
        errors.append("symbol-map hash mismatch")
    schedule = report.get("schedule_contract", {})
    if (
        schedule.get("status") != "pass"
        or schedule.get("attempt_lengths") != [720, 720, 292]
        or schedule.get("attempt_sha256") != list(SCHEDULE_SHA256)
        or schedule.get("combined_sha256")
        != COMBINED_SCHEDULE_SHA256
        or schedule.get("capture_ticks_sha256")
        != CAPTURE_TICKS_SHA256
        or schedule.get("checkpoint_keys_sha256")
        != CHECKPOINT_KEYS_SHA256
    ):
        errors.append("schedule contract mismatch")
    prerequisite = report.get("runtime_prerequisite", {})
    if (
        prerequisite.get("status") != "pass"
        or prerequisite.get("report_sha256")
        != PINNED_RUNTIME_REPORT_SHA256
        or prerequisite.get("trace_sha256")
        != PINNED_RUNTIME_TRACE_SHA256
        or prerequisite.get("replay_sha256")
        != PINNED_RUNTIME_REPLAY_SHA256
    ):
        errors.append("runtime prerequisite mismatch")
    provenance = report.get("provenance")
    scenario_symbols: dict[str, int] | None = (
        {} if root is not None else None
    )
    if root is not None:
        live_symbol_path = root.resolve() / SYMBOL_MAP_RELATIVE_PATH
        if not live_symbol_path.is_file():
            errors.append("live required-symbol map file missing")
        else:
            try:
                live_symbols = parse_vasm_symbols(live_symbol_path)
            except (EvidenceFailure, OSError, ValueError) as error:
                errors.append(
                    f"live required-symbol map could not be parsed: {error}"
                )
            else:
                missing = [
                    name for name in REQUIRED_SYMBOLS
                    if name not in live_symbols
                ]
                if missing:
                    errors.append(
                        f"live required-symbol map missing {missing}"
                    )
                else:
                    scenario_symbols = {
                        name: live_symbols[name]
                        for name in REQUIRED_SYMBOLS
                    }
                    expected_public_symbols = {
                        name: f"{scenario_symbols[name]:08X}"
                        for name in REQUIRED_SYMBOLS
                    }
                    public_symbols = (
                        provenance.get("required_symbols")
                        if isinstance(provenance, dict)
                        else None
                    )
                    if public_symbols != expected_public_symbols:
                        errors.append(
                            "required symbols differ from live pinned "
                            "symbol map"
                        )
                    live_layout_errors = symbol_layout_errors(
                        scenario_symbols
                    )
                    if live_layout_errors:
                        errors.append(
                            "live required-symbol layout mismatch: "
                            f"{live_layout_errors[:3]}"
                        )
    sessions = report.get("sessions")
    if not isinstance(sessions, list) or len(sessions) != SESSION_COUNT:
        errors.append("two sessions required")
        return errors
    if tuple(item.get("session_id") for item in sessions) != (
        EXPECTED_SESSION_IDS
    ):
        errors.append("session identity/order mismatch")
    process_ids = [
        session.get("metadata", {}).get("process_id")
        for session in sessions
    ]
    runtime_directories = [
        session.get("metadata", {})
        .get("runtime_copy", {})
        .get("temporary_directory")
        for session in sessions
    ]
    if (
        any(
            not isinstance(process_id, int) or process_id <= 0
            for process_id in process_ids
        )
        or len(set(process_ids)) != SESSION_COUNT
    ):
        errors.append("owned session process identities are not unique")
    if (
        any(
            not isinstance(directory, str) or not directory
            for directory in runtime_directories
        )
        or len(set(runtime_directories)) != SESSION_COUNT
    ):
        errors.append("owned runtime directories are not unique")
    for session in sessions:
        errors.extend(session_cadence_errors(session))
        errors.extend(
            _scenario_integrity_errors(
                session,
                provenance=provenance,
                symbols_override=scenario_symbols,
            )
        )
        observations = session.get("observations")
        if (
            not isinstance(observations, list)
            or [item.get("label") for item in observations]
            != list(EXPECTED_CAPTURE_LABELS)
        ):
            errors.append(
                f"{session.get('session_id')} observation labels mismatch"
            )
            continue
        for observation in observations:
            errors.extend(
                _observation_integrity_errors(
                    observation,
                    session_id=session.get("session_id", "?"),
                )
            )
            if not _observation_abi_clock_ok(observation):
                errors.append(
                    f"{session.get('session_id')}/{observation['label']} "
                    "ABI/clock invalid"
                )
            if any(_observation_fault_values(observation).values()):
                errors.append(
                    f"{session.get('session_id')}/{observation['label']} "
                    "fault field nonzero"
                )
            if (
                not _observation_pool_ok(observation)
                or not _observation_vocabulary_budget_ok(observation)
                or not _observation_capacity_ok(observation)
                or not _observation_audio_clock_ok(observation)
            ):
                errors.append(
                    f"{session.get('session_id')}/{observation['label']} "
                    "budget contract invalid"
                )
    try:
        records = [_signature_record(session) for session in sessions]
    except (IndexError, KeyError, TypeError, ValueError) as error:
        records = []
        errors.append(f"could not rebuild budget signatures: {error}")
    if (
        len(records) != SESSION_COUNT
        or records[0]["sha256"] != records[1]["sha256"]
        or report.get("budget_signatures") != records
    ):
        errors.append("two-session signature mismatch")
    if not isinstance(provenance, dict):
        errors.append("provenance missing")
    else:
        errors.extend(
            _source_bindings_errors(
                provenance.get("source_bindings"), root=root
            )
        )
        for name, expected in (
            ("rom", PINNED_ROM_SHA256),
            ("symbol_map", PINNED_SYMBOL_SHA256),
            ("emulator", PINNED_EMULATOR_SHA256),
            ("default_config", PINNED_DEFAULT_CONFIG_SHA256),
            ("runtime_report", PINNED_RUNTIME_REPORT_SHA256),
            ("runtime_trace", PINNED_RUNTIME_TRACE_SHA256),
            ("runtime_replay", PINNED_RUNTIME_REPLAY_SHA256),
        ):
            if provenance.get(name, {}).get("sha256") != expected:
                errors.append(f"provenance {name} hash mismatch")
        source_contract = provenance.get(
            "frame_advance_source_contract", {}
        )
        if (
            source_contract.get("status") != "pass"
            or source_contract.get("blastem_version")
            != BLASTEM_VERSION
            or source_contract.get("source_node")
            != BLASTEM_SOURCE_NODE
            or source_contract.get("source_sha256")
            != BLASTEM_SOURCE_HASHES
            or source_contract.get("default_config_sha256")
            != PINNED_DEFAULT_CONFIG_SHA256
        ):
            errors.append("frame-advance source contract mismatch")
        verification = provenance.get("hash_verification", {})
        if (
            verification.get("status") != "pass"
            or verification.get("drift") != []
        ):
            errors.append("hash drift verification failed")
        if root is not None:
            resolved_root = root.resolve()
            expected_paths = _hash_binding_paths(resolved_root)
            for name, expected_path in expected_paths.items():
                binding = provenance.get(name, {})
                expected_relative = _relative_or_absolute(
                    expected_path, resolved_root
                )
                if binding.get("path") != expected_relative:
                    errors.append(f"provenance {name} path mismatch")
                    continue
                if not expected_path.is_file():
                    errors.append(f"provenance {name} file missing")
                    continue
                if sha256_file(expected_path) != binding.get("sha256"):
                    errors.append(f"provenance {name} live hash mismatch")
            transcript = provenance.get("transcript", {})
            expected_trace = resolved_root / TRACE_RELATIVE_PATH
            if transcript.get("path") != TRACE_RELATIVE_PATH:
                errors.append("budget transcript path mismatch")
            elif not expected_trace.is_file():
                errors.append("budget transcript file missing")
            elif sha256_file(expected_trace) != transcript.get("sha256"):
                errors.append("budget transcript live hash mismatch")
            else:
                trace_lines = expected_trace.read_text(
                    encoding="utf-8"
                ).splitlines()
                if provenance.get("transcript", {}).get(
                    "commands"
                ) != sum(line.startswith("> ") for line in trace_lines):
                    errors.append(
                        "budget transcript command count mismatch"
                    )
                symbols = parse_vasm_symbols(
                    expected_paths["symbol_map"]
                )
                covered_trace_lines: list[str] = []
                for session in sessions:
                    session_id = str(session.get("session_id", "?"))
                    session_lines = _session_transcript_lines(
                        trace_lines, session_id
                    )
                    covered_trace_lines.extend(session_lines)
                    session_payload = (
                        "\n".join(session_lines)
                        + ("\n" if session_lines else "")
                    ).encode("utf-8")
                    recomputed = audit_frame_advance_transcript(
                        session_lines,
                        session_id=session_id,
                        vblank=symbols["VBlankInterrupt"],
                        post_step=symbols[
                            "MainLoopPostGameplayStep"
                        ],
                        finalizer=symbols[
                            "VocabularyFinalizeStage"
                        ],
                        wait_for_vblank=symbols[
                            "MainLoopWaitForVBlank"
                        ],
                        session=session,
                    )
                    if (
                        not session_lines
                        or session.get("metadata", {}).get(
                            "transcript_sha256"
                        )
                        != _sha256_bytes(session_payload)
                        or recomputed
                        != session.get("metadata", {}).get(
                            "frame_advance_audit"
                        )
                    ):
                        errors.append(
                            f"{session_id} live transcript audit mismatch"
                        )
                if covered_trace_lines != trace_lines:
                    errors.append(
                        "budget transcript has lines outside ordered "
                        "session spans"
                    )
                if debugger_write_packets(trace_lines):
                    errors.append(
                        "budget transcript contains live debugger writes"
                    )
    return errors


def report_exit_code(report: dict[str, Any]) -> int:
    return (
        0
        if report.get("status") == "pass"
        and report.get("acceptance_status") == "pass"
        and not report_contract_errors(report)
        else 1
    )


def existing_report_errors(
    report: dict[str, Any],
    *,
    root: Path,
) -> list[str]:
    """Apply both the semantic public contract and the published schema."""
    errors = report_contract_errors(report, root=root)
    try:
        from jsonschema import Draft202012Validator

        schema = json.loads(
            (root / SCHEMA_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        schema_errors = sorted(
            Draft202012Validator(schema).iter_errors(report),
            key=lambda item: tuple(str(part) for part in item.path),
        )
        errors.extend(
            "schema "
            + "/".join(str(part) for part in item.path)
            + f": {item.message}"
            for item in schema_errors
        )
    except (ImportError, OSError, json.JSONDecodeError) as error:
        errors.append(f"could not validate published report schema: {error}")
    return errors


def overrun_origin_site_contract(rom_path: Path) -> dict[str, Any]:
    """Bind the diagnostic breakpoints to exact fault-path ROM bytes."""
    try:
        rom = rom_path.read_bytes()
    except OSError as error:
        return {
            "status": "fail",
            "rom_sha256": "",
            "sites": {},
            "errors": [f"could not read diagnostic ROM: {error}"],
        }
    errors: list[str] = []
    sites: dict[str, Any] = {}
    seen_addresses: set[int] = set()
    for name, contract in OVERRUN_ORIGIN_SITES.items():
        address = int(contract["address"])
        guard_address = int(contract["guard_window_address"])
        increment_address = int(contract["increment_address"])
        compare_expected = bytes.fromhex(
            str(contract["compare_opcode_hex"])
        )
        guard_expected = bytes.fromhex(str(contract["guard_window_hex"]))
        increment_expected = bytes.fromhex(
            str(contract["increment_opcode_hex"])
        )
        compare_observed = rom[address : address + len(compare_expected)]
        guard_observed = rom[
            guard_address : guard_address + len(guard_expected)
        ]
        increment_observed = rom[
            increment_address : increment_address + len(increment_expected)
        ]
        exact = (
            address not in seen_addresses
            and compare_observed == compare_expected
            and guard_observed == guard_expected
            and increment_observed == increment_expected
        )
        if not exact:
            errors.append(f"{name} fault-path ROM bytes mismatch")
        seen_addresses.add(address)
        sites[name] = {
            "address": f"{address:08X}",
            "guard_window_address": f"{guard_address:08X}",
            "guard_window_hex": guard_observed.hex().upper(),
            "compare_opcode_hex": compare_observed.hex().upper(),
            "increment_address": f"{increment_address:08X}",
            "increment_opcode_hex": increment_observed.hex().upper(),
            "register_low_words": dict(contract["register_low_words"]),
            "meaning": contract["meaning"],
            "exact": exact,
        }
    digest = _sha256_bytes(rom)
    if digest != PINNED_ROM_SHA256:
        errors.append("overrun-origin diagnostic ROM SHA-256 mismatch")
    return {
        "status": "pass" if not errors else "fail",
        "rom_sha256": digest,
        "sites": sites,
        "errors": errors,
    }


def _hash_binding_paths(root: Path) -> dict[str, Path]:
    return {
        "rom": root / ROM_RELATIVE_PATH,
        "symbol_map": root / SYMBOL_MAP_RELATIVE_PATH,
        "emulator": root / EMULATOR_RELATIVE_PATH,
        "default_config": root / DEFAULT_CONFIG_RELATIVE_PATH,
        "runtime_report": root / RUNTIME_REPORT_RELATIVE_PATH,
        "runtime_trace": root / RUNTIME_TRACE_RELATIVE_PATH,
        "runtime_replay": root / RUNTIME_REPLAY_RELATIVE_PATH,
        "b03_collector": root / B03_COLLECTOR_RELATIVE_PATH,
        "collector": root / COLLECTOR_RELATIVE_PATH,
        "report_schema": root / SCHEMA_RELATIVE_PATH,
        "probe": root / PROBE_RELATIVE_PATH,
    }


def _capture_hashes(paths: dict[str, Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise EvidenceFailure(f"hash-bound {name} is missing: {path}")
        hashes[name] = sha256_file(path)
    return hashes


def _provenance_bindings(
    *,
    root: Path,
    paths: dict[str, Path],
    hashes: dict[str, str],
) -> dict[str, Any]:
    return {
        name: {
            "path": _relative_or_absolute(path, root),
            "sha256": hashes[name],
        }
        for name, path in paths.items()
    }


def _capture_label_for_total(total: int) -> str | None:
    if total <= 720:
        attempt = 0
        tick = total
    elif total <= 1440:
        attempt = 1
        tick = total - 720
    else:
        attempt = 2
        tick = total - 1440
    if total == 720:
        return "post_timeout_1"
    if total == 1440:
        return "post_timeout_2"
    if tick not in EXPECTED_CAPTURE_TICKS[attempt]:
        return None
    if total == EXPECTED_AUTHORITATIVE_STEPS:
        return "terminal"
    return f"attempt_{attempt}_tick_{tick}"


def _verify_timeout_commit(
    snapshot: dict[str, Any],
    *,
    attempt: int,
    schedule: bytes,
) -> dict[str, Any]:
    committed_bank = 6 if attempt == 0 else 0
    descriptor = b03._descriptor_for_bank(snapshot, committed_bank)
    committed = b03._replay_bank_bytes(snapshot, committed_bank)
    timeline = snapshot["replay_timeline"]["timeline"]
    expected_order = [6] if attempt == 0 else [6, 0]
    passed = (
        timeline["echo_count"] == attempt + 1
        and timeline["echo_order"][: attempt + 1] == expected_order
        and descriptor["length"] == len(schedule)
        and descriptor["crc32_iso_hdlc"]
        == b03.crc32_iso_hdlc(schedule)
        and descriptor["flags"]
        & (b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO)
        == (b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO)
        and committed == schedule
    )
    result = {
        "status": "pass" if passed else "fail",
        "attempt": attempt,
        "authoritative_total": (attempt + 1) * 720,
        "committed_bank": committed_bank,
        "descriptor_length": descriptor["length"],
        "descriptor_crc32_iso_hdlc": descriptor[
            "crc32_iso_hdlc"
        ],
        "descriptor_flags": descriptor["flags"],
        "committed_input_sha256": _sha256_bytes(committed),
        "committed_input_raw_hex": committed.hex().upper(),
    }
    if not passed:
        raise EvidenceFailure(
            f"timeout attempt {attempt} replay commit failed: {result}"
        )
    return result


def _stop_timeout_detail(
    *,
    expected_total: int,
    last_row: dict[str, Any] | None,
    window: int,
) -> str:
    expected = expected_route_for_total(expected_total)
    if last_row is None:
        last = "initial stage-3 origin"
    else:
        observed = last_row["observed"]
        engine = observed["engine"]
        debug = observed["debug"]
        last = (
            f"last POST total={last_row['authoritative_total']} "
            f"state/attempt/tick={engine['state']}/"
            f"{engine['attempt']}/{engine['tick']} "
            f"frame={debug['frame_counter']} token={debug['frame_token']} "
            f"overrun={debug['vblank_overrun_count']}"
        )
    return (
        "the permitted F8 frame schedule did not reach permanent POST; "
        "no retry or extra "
        f"advance permitted; expected total={expected_total} "
        f"state/attempt/tick={expected['state']}/"
        f"{expected['attempt']}/{expected['tick']}; {last}; "
        f"window_title={_window_title(window)!r}"
    )


def _observe_exact_post(
    remote: GdbRemote,
    *,
    total: int,
    post_step: int,
    finalizer: int,
    last_row: dict[str, Any] | None,
    window: int,
    transcript: list[str],
) -> None:
    stop = remote.observe_stop_response(FRAME_STOP_TIMEOUT_SECONDS)
    if stop["timed_out"]:
        raise EvidenceFailure(
            _stop_timeout_detail(
                expected_total=total,
                last_row=last_row,
                window=window,
            )
        )
    require_stop(str(stop["response"]))
    pc = b03._read_register_u32(
        remote, 0x11, label=f"authoritative total {total}"
    )
    if total < EXPECTED_AUTHORITATIVE_STEPS:
        if pc != post_step:
            raise EvidenceFailure(
                f"total {total} stopped at PC ${pc:08X}; permanent "
                f"POST ${post_step:08X} required"
            )
        transcript.append(
            f"# cadence.stop total={total} role=post pc={pc:08X}"
        )
        return
    if pc != finalizer:
        raise EvidenceFailure(
            f"terminal total {total} first stopped at PC ${pc:08X}; "
            f"VocabularyFinalizeStage ${finalizer:08X} required"
        )
    transcript.append(
        f"# cadence.stop total={total} role=finalizer pc={pc:08X}"
    )
    if remote.request(f"z0,{finalizer:x},2") != "OK":
        raise EvidenceFailure("terminal finalizer breakpoint removal failed")
    require_stop(remote.request("c"))
    terminal_pc = b03._read_register_u32(
        remote, 0x11, label="same-frame terminal POST"
    )
    if terminal_pc != post_step:
        raise EvidenceFailure(
            "same-frame terminal continuation stopped at "
            f"${terminal_pc:08X}; POST ${post_step:08X} required"
        )
    transcript.append(
        "# cadence.stop "
        f"total={total} role=terminal_post pc={terminal_pc:08X}"
    )


def _run_frame_advance_budget_session(
    *,
    session_id: str,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: Any = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    input_events: list[str] = []
    sentinel_handshakes: list[dict[str, Any]] = []
    advance_events: list[dict[str, Any]] = []
    cadence_rows: list[dict[str, Any]] = []
    snapshots: dict[str, dict[str, Any]] = {}
    timeout_commits: list[dict[str, Any]] = []
    terminal_wait_observation: dict[str, Any] | None = None
    attempt_inputs: dict[str, list[int]] = {
        "0": [],
        "1": [],
        "2": [],
    }
    post_observed: dict[str, list[int]] = {
        "0": [],
        "1": [],
        "2": [],
    }
    session: dict[str, Any] | None = None
    with b03._owned_session_directory(
        prefix=f"recursion94-m5-budget-{session_id}-",
        transcript=transcript,
    ) as profile:
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        original_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            original_profile_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *b03.LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = b03._launch_owned_emulator(
                command,
                cwd=isolated_runtime,
                transcript=transcript,
            )
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            if process.poll() is not None:
                raise EvidenceFailure(
                    f"{session_id} BlastEm exited early with "
                    f"{process.returncode}"
                )
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            finalizer = symbols["VocabularyFinalizeStage"]
            wait_for_vblank = symbols["MainLoopWaitForVBlank"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            b03._set_gameplay_mask(
                window,
                b03.ATTEMPT_0_INPUTS[0],
                pressed,
                input_events,
            )
            snapshots["initial"] = _capture_combined_snapshot(
                remote, label="initial", symbols=symbols
            )
            if not b03.initialization_ok(snapshots["initial"]):
                raise EvidenceFailure(
                    f"{session_id} did not initialize canonical B03"
                )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} permanent POST insertion failed"
                )
            if remote.request(f"Z0,{finalizer:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} finalizer insertion failed"
                )

            initial_title = _window_title(window)
            advance_events.append(
                _tap_frame_advance(
                    window,
                    index=1,
                    purpose="origin_prime",
                    transcript=transcript,
                )
            )
            remote.begin_continue()
            origin_pause = _wait_for_origin_pause(
                window,
                initial_title=initial_title,
                timeout_seconds=PAUSE_OVERWRITE_TIMEOUT_SECONDS,
            )
            transcript.append(
                "# cadence.origin_pause "
                f"title={origin_pause['observed_title']!r}"
            )
            prepared_handshake = _set_pause_sentinel(
                window=window,
                session_id=session_id,
                boundary_total=0,
                transcript=transcript,
            )

            last_row: dict[str, Any] | None = None
            post_removed = False
            for total in range(1, EXPECTED_AUTHORITATIVE_STEPS + 1):
                advance_events.append(
                    _tap_frame_advance(
                        window,
                        index=len(advance_events) + 1,
                        purpose=f"authoritative_total_{total}",
                        transcript=transcript,
                    )
                )
                if total in RESET_TOTALS:
                    # Exactly two measured reset-service VBlanks occur before
                    # POST. No new input, simulation step, or GDB continue is
                    # issued while the one outstanding continue remains live.
                    for service in range(1, RESET_SERVICE_FRAMES + 1):
                        sentinel_handshakes.append(_complete_sentinel_handshake(
                            remote, window=window, prepared=prepared_handshake,
                            transcript=transcript, continue_guest=False,
                        ))
                        prepared_handshake = _set_pause_sentinel(
                            window=window, session_id=session_id,
                            boundary_total=expected_physical_frame(total - 1) + service,
                            transcript=transcript,
                        )
                        advance_events.append(_tap_frame_advance(
                            window, index=len(advance_events) + 1,
                            purpose=f"reset_{total}_service_{service}", transcript=transcript,
                        ))
                _observe_exact_post(
                    remote,
                    total=total,
                    post_step=post_step,
                    finalizer=finalizer,
                    last_row=last_row,
                    window=window,
                    transcript=transcript,
                )
                row = capture_cadence_row(
                    remote, authoritative_total=total
                )
                row_errors = cadence_row_errors(row)
                if row_errors:
                    raise EvidenceFailure(
                        f"{session_id} cadence total {total} failed: "
                        + "; ".join(row_errors)
                    )
                cadence_rows.append(row)
                last_row = row
                input_attempt = (
                    0 if total <= 720 else 1 if total <= 1440 else 2
                )
                desired = expected_input_for_total(total)
                attempt_inputs[str(input_attempt)].append(desired)
                post_observed[str(input_attempt)].append(
                    row["observed"]["input"]["held"]
                )

                label = _capture_label_for_total(total)
                if label is not None:
                    snapshots[label] = _capture_combined_snapshot(
                        remote, label=label, symbols=symbols
                    )
                    if total in {720, 1440}:
                        timeout_commits.append(
                            _verify_timeout_commit(
                                snapshots[label],
                                attempt=input_attempt,
                                schedule=(
                                    b03.ATTEMPT_0_INPUTS
                                    if input_attempt == 0
                                    else b03.ATTEMPT_1_INPUTS
                                ),
                            )
                        )

                if total < EXPECTED_AUTHORITATIVE_STEPS:
                    next_desired = expected_input_for_total(total + 1)
                    b03._set_gameplay_mask(
                        window,
                        next_desired,
                        pressed,
                        input_events,
                    )
                else:
                    if row["observed"]["debug"]["frame_token"] != 0:
                        raise EvidenceFailure(
                            "terminal POST teardown requires frame_token=0"
                        )
                    if (
                        remote.request(
                            f"Z0,{wait_for_vblank:x},2"
                        )
                        != "OK"
                    ):
                        raise EvidenceFailure(
                            f"{session_id} terminal WAIT insertion failed"
                        )
                    require_stop(remote.request("c"))
                    wait_pc = b03._read_register_u32(
                        remote,
                        0x11,
                        label="terminal MainLoopWaitForVBlank",
                    )
                    if wait_pc != wait_for_vblank:
                        raise EvidenceFailure(
                            "terminal POST continuation stopped at "
                            f"${wait_pc:08X}; exact WAIT "
                            f"${wait_for_vblank:08X} required before any "
                            "crossed-VBlank POST"
                        )
                    transcript.append(
                        "# cadence.stop "
                        f"total={total} role=terminal_wait "
                        f"pc={wait_pc:08X}"
                    )
                    terminal_wait_observation = capture_cadence_row(
                        remote,
                        authoritative_total=total,
                        trace_kind="terminal_wait",
                    )
                    wait_errors = cadence_row_errors(
                        terminal_wait_observation
                    )
                    if wait_errors:
                        raise EvidenceFailure(
                            f"{session_id} terminal WAIT state failed: "
                            + "; ".join(wait_errors)
                        )
                    if (
                        remote.request(
                            f"z0,{wait_for_vblank:x},2"
                        )
                        != "OK"
                    ):
                        raise EvidenceFailure(
                            f"{session_id} terminal WAIT removal failed"
                        )
                    if remote.request(f"z0,{post_step:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{session_id} permanent POST removal failed"
                        )
                    post_removed = True
                sentinel_handshakes.append(
                    _complete_sentinel_handshake(
                        remote,
                        window=window,
                        prepared=prepared_handshake,
                        transcript=transcript,
                    )
                )
                if total == EXPECTED_AUTHORITATIVE_STEPS:
                    break
                prepared_handshake = _set_pause_sentinel(
                    window=window,
                    session_id=session_id,
                    boundary_total=expected_physical_frame(total),
                    transcript=transcript,
                )

            if set(snapshots) != set(EXPECTED_CAPTURE_LABELS):
                raise EvidenceFailure(
                    f"{session_id} checkpoint set mismatch: "
                    f"{sorted(snapshots)}"
                )
            if not post_removed:
                raise EvidenceFailure(
                    f"{session_id} permanent POST was not removed"
                )
            raw_session = {
                "session_id": session_id,
                "metadata": {
                    "process_id": process.pid,
                    "launch_flags": list(b03.LAUNCH_FLAGS),
                    "session_mode": "positive",
                    "initial_breakpoint_symbol": "VBlankInterrupt",
                    "initial_breakpoint": f"{vblank:08X}",
                    "step_breakpoint_symbol": (
                        "MainLoopPostGameplayStep"
                    ),
                    "step_breakpoint": f"{post_step:08X}",
                    "terminal_pre_post_symbol": (
                        "VocabularyFinalizeStage"
                    ),
                    "terminal_pre_post_breakpoint": (
                        f"{finalizer:08X}"
                    ),
                    "terminal_wait_symbol": "MainLoopWaitForVBlank",
                    "terminal_wait_breakpoint": (
                        f"{wait_for_vblank:08X}"
                    ),
                    "drive_protocol": (
                        "origin prime reaches responsive PAUSED; bounded "
                        "WM_SETTEXT sentinel/readback occurs only while "
                        "paused; one F8 reaches exact permanent POST, with "
                        "two additional sentinel-verified service frames "
                        "only at timeout totals720/1440; reads "
                        "finish; c reaches the same frame end and BlastEm "
                        "overwrites PAUSED; finalizer continues same-frame "
                        "terminal POST; a temporary breakpoint proves the "
                        "next stop is token-0 MainLoopWaitForVBlank before "
                        "the final same-frame PAUSED overwrite"
                    ),
                    "input_transport": "targeted_PostMessageW",
                    "frame_advance_transport": (
                        "targeted_PostMessageW_F8_ui.advance"
                    ),
                    "input_edge_settle_milliseconds": (
                        b03.INPUT_EDGE_SETTLE_SECONDS * 1000
                    ),
                    "input_events": input_events,
                    "origin_pause": origin_pause,
                    "sentinel_handshakes": sentinel_handshakes,
                    "ui_advance_events": advance_events,
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "symbol_map_sha256": sha256_file(symbol_path),
                    "frame_advance_source_contract": {
                        "version": BLASTEM_VERSION,
                        "source_node": BLASTEM_SOURCE_NODE,
                        "source_sha256": BLASTEM_SOURCE_HASHES,
                    },
                },
                "attempt_inputs": attempt_inputs,
                "commanded_inputs": {
                    "0": list(b03.ATTEMPT_0_INPUTS),
                    "1": list(b03.ATTEMPT_1_INPUTS),
                    "2": list(b03.ATTEMPT_2_INPUTS),
                },
                "post_step_observed_inputs": post_observed,
                "timeout_commits": timeout_commits,
                "terminal_wait_observation": (
                    terminal_wait_observation
                ),
                "cadence_rows": cadence_rows,
                "snapshots": snapshots,
                "_transcript_lines": [],
            }
            session = _extract_budget_session(
                raw_session, session_id=session_id
            )
        finally:
            if window is not None:
                for (
                    name,
                    virtual_key,
                    scan_code,
                    extended,
                    _bit,
                ) in b03.KEYS:
                    if name in pressed:
                        try:
                            b03._post_key(
                                window,
                                (
                                    name,
                                    virtual_key,
                                    scan_code,
                                    extended,
                                ),
                                pressed=False,
                            )
                            input_events.append(f"{name}:emergency-up")
                        except Exception as error:
                            transcript.append(
                                "! emergency key release failed: "
                                f"{type(error).__name__}: {error}"
                            )
            pressed.clear()
            b03._shutdown_owned_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if session is None:
        raise EvidenceFailure(f"{session_id} produced no budget evidence")
    session_lines = transcript[start_index:]
    audit = audit_frame_advance_transcript(
        session_lines,
        session_id=session_id,
        vblank=symbols["VBlankInterrupt"],
        post_step=symbols["MainLoopPostGameplayStep"],
        finalizer=symbols["VocabularyFinalizeStage"],
        wait_for_vblank=symbols["MainLoopWaitForVBlank"],
        session=session,
    )
    if audit["status"] != "pass":
        raise EvidenceFailure(
            f"{session_id} frame-advance transcript audit failed: {audit}"
        )
    session["_transcript_lines"] = session_lines
    session["metadata"]["frame_advance_audit"] = audit
    session["metadata"]["transcript_sha256"] = _sha256_bytes(
        (
            "\n".join(session_lines)
            + ("\n" if session_lines else "")
        ).encode("utf-8")
    )
    cadence_errors = session_cadence_errors(session)
    if cadence_errors:
        raise EvidenceFailure(
            f"{session_id} public cadence contract failed: "
            f"{cadence_errors[:5]}"
        )
    return session


def diagnostic_output_path_errors(
    *,
    root: Path,
    report: Path | None,
    trace: Path | None,
) -> list[str]:
    if report is None or trace is None:
        return [
            "diagnostic mode requires explicit --report and --trace paths"
        ]
    diagnostic_root = (
        root / DIAGNOSTIC_DIRECTORY_RELATIVE_PATH
    ).resolve()
    resolved_report = report.resolve()
    resolved_trace = trace.resolve()
    errors: list[str] = []
    for name, path in (("report", resolved_report), ("trace", resolved_trace)):
        try:
            path.relative_to(diagnostic_root)
        except ValueError:
            errors.append(
                f"diagnostic {name} must be inside {diagnostic_root}"
            )
    if resolved_report == resolved_trace:
        errors.append("diagnostic report and trace paths must differ")
    canonical_report = (root / REPORT_RELATIVE_PATH).resolve()
    canonical_trace = (root / TRACE_RELATIVE_PATH).resolve()
    if resolved_report in {canonical_report, canonical_trace} or (
        resolved_trace in {canonical_report, canonical_trace}
    ):
        errors.append("diagnostic mode may not target canonical artifacts")
    return errors


def audit_diagnostic_transcript(
    lines: list[str],
    *,
    session_id: str,
    steps: int,
    vblank: int,
    post_step: int,
    initial_scenario: dict[str, Any],
    cadence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    cursor = 0
    memory_pairs = 0

    def consume(expected: str) -> None:
        nonlocal cursor
        observed = lines[cursor] if cursor < len(lines) else "<EOF>"
        if observed != expected:
            raise ValueError(
                f"line {cursor}: expected {expected!r}; got {observed!r}"
            )
        cursor += 1

    def consume_pair(command: str, response: str) -> None:
        consume(command)
        consume(response)

    def consume_memory(
        *,
        kind: str,
        identity: str,
        report_blocks: dict[str, Any],
    ) -> None:
        nonlocal cursor, memory_pairs
        consume(f"# memory.begin kind={kind} id={identity}")
        regions = _trace_read_plan(kind)
        cursor, raw = _consume_read_plan(
            lines, cursor, regions=regions
        )
        consume(f"# memory.end kind={kind} id={identity}")
        memory_pairs += len(_chunk_plan(regions))
        for name, payload in raw.items():
            if payload.hex().upper() != report_blocks.get(name, {}).get(
                "raw_hex"
            ):
                raise ValueError(
                    f"{kind}/{identity}/{name} differs from report raw"
                )

    def consume_pause(boundary: int) -> None:
        nonlocal cursor
        if cursor >= len(lines):
            raise ValueError("missing diagnostic PAUSED overwrite marker")
        match = re.fullmatch(
            rf"# cadence\.pause boundary={boundary} title=(.+)",
            lines[cursor],
        )
        if not match:
            raise ValueError(
                f"line {cursor}: invalid diagnostic pause marker"
            )
        title = ast.literal_eval(match.group(1))
        if (
            not isinstance(title, str)
            or title == _sentinel_for(session_id, boundary)
            or not title.upper().endswith(" - PAUSED")
        ):
            raise ValueError(
                f"line {cursor}: pause did not overwrite sentinel"
            )
        cursor += 1

    def consume_sentinel(boundary: int) -> None:
        consume(
            "# cadence.sentinel "
            f"boundary={boundary} "
            f"title={_sentinel_for(session_id, boundary)!r}"
        )

    try:
        if not 1 <= steps <= 725:
            raise ValueError("diagnostic steps must be within 1..725")
        if len(cadence_rows) != steps:
            raise ValueError("diagnostic cadence row count mismatch")
        consume(f"# {session_id} begin")
        consume_pair(f"> Z0,{vblank:x},2", "< OK")
        consume_pair("> c", "< S05")
        consume_memory(
            kind="scenario",
            identity="initial",
            report_blocks=initial_scenario["blocks"],
        )
        consume_pair(f"> z0,{vblank:x},2", "< OK")
        consume_pair(f"> Z0,{post_step:x},2", "< OK")
        consume("# ui.advance index=1 purpose=origin_prime")
        consume("> c")
        if cursor >= len(lines) or not re.fullmatch(
            r"# cadence\.origin_pause title='.* - PAUSED'", lines[cursor]
        ):
            raise ValueError("diagnostic origin PAUSED marker mismatch")
        cursor += 1
        consume_sentinel(0)
        for total in range(1, steps + 1):
            consume(
                "# ui.advance "
                f"index={total + 1} "
                f"purpose=authoritative_total_{total}"
            )
            consume("< S05")
            consume_pair("> p11", f"< {post_step:08X}")
            consume(
                "# cadence.stop "
                f"total={total} role=post pc={post_step:08X}"
            )
            consume_memory(
                kind="cadence",
                identity=f"{total:04d}",
                report_blocks=cadence_rows[total - 1]["blocks"],
            )
            if total < steps:
                consume("> c")
                consume_pause(total - 1)
                consume_sentinel(total)
            else:
                consume_pair(f"> z0,{post_step:x},2", "< OK")
                consume("> c")
                consume_pause(total - 1)
        consume("> k")
        consume(f"# {session_id} end")
        if cursor != len(lines):
            raise ValueError("lines follow diagnostic session end")
    except (KeyError, SyntaxError, ValueError) as error:
        errors.append(str(error))

    expected_memory_pairs = 56 + steps * 3
    expected_commands = expected_memory_pairs + steps * 2 + 7
    observed_commands = sum(line.startswith("> ") for line in lines)
    writes = debugger_write_packets(lines)
    if memory_pairs != expected_memory_pairs:
        errors.append(
            f"diagnostic memory pairs {memory_pairs} != "
            f"{expected_memory_pairs}"
        )
    if observed_commands != expected_commands:
        errors.append(
            f"diagnostic commands {observed_commands} != "
            f"{expected_commands}"
        )
    if writes:
        errors.append(f"diagnostic debugger writes present: {writes}")
    return {
        "status": "pass" if not errors else "fail",
        "steps": steps,
        "origin_prime_pulses": 1,
        "authoritative_f8_pulses": steps,
        "total_f8_pulses": steps + 1,
        "sentinel_handshakes": steps,
        "memory_read_pairs": memory_pairs,
        "expected_memory_read_pairs": expected_memory_pairs,
        "transcript_command_count": observed_commands,
        "expected_transcript_command_count": expected_commands,
        "debugger_write_packets": writes,
        "errors": errors,
    }


def audit_overrun_origin_transcript(
    lines: list[str],
    *,
    session_id: str,
    vblank: int,
    post_step: int,
    initial_scenario: dict[str, Any],
    cadence_rows: list[dict[str, Any]],
    origin_hit: dict[str, Any],
) -> dict[str, Any]:
    """Consume the fixed three-step, two-site origin-probe transcript."""
    errors: list[str] = []
    cursor = 0
    memory_pairs = 0

    def consume(expected: str) -> None:
        nonlocal cursor
        observed = lines[cursor] if cursor < len(lines) else "<EOF>"
        if observed != expected:
            raise ValueError(
                f"line {cursor}: expected {expected!r}; got {observed!r}"
            )
        cursor += 1

    def consume_pair(command: str, response: str) -> None:
        consume(command)
        consume(response)

    def consume_memory(
        *,
        kind: str,
        identity: str,
        report_blocks: dict[str, Any],
    ) -> None:
        nonlocal cursor, memory_pairs
        consume(f"# memory.begin kind={kind} id={identity}")
        regions = _trace_read_plan(kind)
        cursor, raw = _consume_read_plan(
            lines, cursor, regions=regions
        )
        consume(f"# memory.end kind={kind} id={identity}")
        memory_pairs += len(_chunk_plan(regions))
        for name, payload in raw.items():
            if payload.hex().upper() != report_blocks.get(name, {}).get(
                "raw_hex"
            ):
                raise ValueError(
                    f"{kind}/{identity}/{name} differs from report raw"
                )

    def consume_pause(boundary: int) -> None:
        nonlocal cursor
        if cursor >= len(lines):
            raise ValueError("missing overrun-origin PAUSED marker")
        match = re.fullmatch(
            rf"# cadence\.pause boundary={boundary} title=(.+)",
            lines[cursor],
        )
        if not match:
            raise ValueError(
                f"line {cursor}: invalid overrun-origin pause marker"
            )
        title = ast.literal_eval(match.group(1))
        if (
            not isinstance(title, str)
            or title == _sentinel_for(session_id, boundary)
            or not title.upper().endswith(" - PAUSED")
        ):
            raise ValueError(
                f"line {cursor}: pause did not overwrite sentinel"
            )
        cursor += 1

    def consume_sentinel(boundary: int) -> None:
        consume(
            "# cadence.sentinel "
            f"boundary={boundary} "
            f"title={_sentinel_for(session_id, boundary)!r}"
        )

    try:
        if len(cadence_rows) != OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
            raise ValueError("overrun-origin cadence row count mismatch")
        hit_name = origin_hit["site"]
        if hit_name not in OVERRUN_ORIGIN_SITES:
            raise ValueError("overrun-origin hit site is unknown")
        hit_contract = OVERRUN_ORIGIN_SITES[hit_name]
        hit_address = int(hit_contract["address"])
        d0 = int(origin_hit["d0"])
        d1 = int(origin_hit["d1"])
        counter_block = origin_hit["counter_before_increment"]
        counter_raw = bytes.fromhex(counter_block["raw_hex"])
        if counter_raw != bytes(2):
            raise ValueError("origin counter was not zero before increment")
        if counter_block != _block_evidence(
            OVERRUN_COUNTER_ADDRESS, counter_raw
        ):
            raise ValueError("origin counter block evidence mismatch")
        expected_registers = hit_contract["register_low_words"]
        if (
            d0 & 0xFFFF != int(expected_registers["d0"])
            or d1 & 0xFFFF != int(expected_registers["d1"])
        ):
            raise ValueError("origin fault-path register signature mismatch")

        consume(f"# {session_id} begin")
        consume_pair(f"> Z0,{vblank:x},2", "< OK")
        consume_pair("> c", "< S05")
        consume_memory(
            kind="scenario",
            identity="initial",
            report_blocks=initial_scenario["blocks"],
        )
        consume_pair(f"> z0,{vblank:x},2", "< OK")
        consume_pair(f"> Z0,{post_step:x},2", "< OK")
        consume("# ui.advance index=1 purpose=origin_prime")
        consume("> c")
        if cursor >= len(lines) or not re.fullmatch(
            r"# cadence\.origin_pause title='.* - PAUSED'", lines[cursor]
        ):
            raise ValueError("overrun-origin initial PAUSED marker mismatch")
        cursor += 1
        consume_sentinel(0)

        for total in (1, 2):
            consume(
                "# ui.advance "
                f"index={total + 1} "
                f"purpose=authoritative_total_{total}"
            )
            consume("< S05")
            consume_pair("> p11", f"< {post_step:08X}")
            consume(
                "# cadence.stop "
                f"total={total} role=post pc={post_step:08X}"
            )
            consume_memory(
                kind="cadence",
                identity=f"{total:04d}",
                report_blocks=cadence_rows[total - 1]["blocks"],
            )
            if total == OVERRUN_ORIGIN_ARM_AFTER_TOTAL:
                site_text = ",".join(
                    f"{name}={int(contract['address']):08X}"
                    for name, contract in OVERRUN_ORIGIN_SITES.items()
                )
                consume(
                    "# overrun_origin.arm boundary=2 sites=" + site_text
                )
                for contract in OVERRUN_ORIGIN_SITES.values():
                    address = int(contract["address"])
                    consume_pair(f"> Z0,{address:x},2", "< OK")
            consume("> c")
            consume_pause(total - 1)
            consume_sentinel(total)

        total = OVERRUN_ORIGIN_DIAGNOSTIC_STEPS
        consume(
            "# ui.advance index=4 purpose=authoritative_total_3"
        )
        consume("< S05")
        consume_pair("> p11", f"< {hit_address:08X}")
        consume(
            "# overrun_origin.hit total=3 "
            f"site={hit_name} pc={hit_address:08X}"
        )
        consume_pair("> p0", f"< {d0:08X}")
        consume_pair("> p1", f"< {d1:08X}")
        consume("# memory.begin kind=overrun_counter id=0003")
        consume_pair(
            f"> m{OVERRUN_COUNTER_ADDRESS:x},2",
            f"< {counter_raw.hex().upper()}",
        )
        memory_pairs += 1
        consume("# memory.end kind=overrun_counter id=0003")
        for contract in OVERRUN_ORIGIN_SITES.values():
            address = int(contract["address"])
            consume_pair(f"> z0,{address:x},2", "< OK")
        consume_pair("> c", "< S05")
        consume_pair("> p11", f"< {post_step:08X}")
        consume(
            "# cadence.stop total=3 role=post "
            f"pc={post_step:08X}"
        )
        consume_memory(
            kind="cadence",
            identity="0003",
            report_blocks=cadence_rows[2]["blocks"],
        )
        consume_pair(f"> z0,{post_step:x},2", "< OK")
        consume("> c")
        consume_pause(2)
        consume("> k")
        consume(f"# {session_id} end")
        if cursor != len(lines):
            raise ValueError("lines follow overrun-origin session end")
    except (KeyError, SyntaxError, TypeError, ValueError) as error:
        errors.append(str(error))

    expected_memory_pairs = (
        len(_chunk_plan(_trace_read_plan("scenario")))
        + OVERRUN_ORIGIN_DIAGNOSTIC_STEPS
        * len(_chunk_plan(_trace_read_plan("cadence")))
        + 1
    )
    expected_commands = expected_memory_pairs + 21
    observed_commands = sum(line.startswith("> ") for line in lines)
    writes = debugger_write_packets(lines)
    if memory_pairs != expected_memory_pairs:
        errors.append(
            f"overrun-origin memory pairs {memory_pairs} != "
            f"{expected_memory_pairs}"
        )
    if observed_commands != expected_commands:
        errors.append(
            f"overrun-origin commands {observed_commands} != "
            f"{expected_commands}"
        )
    if writes:
        errors.append(f"overrun-origin debugger writes present: {writes}")
    return {
        "status": "pass" if not errors else "fail",
        "fixed_authoritative_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
        "armed_after_clean_total": OVERRUN_ORIGIN_ARM_AFTER_TOTAL,
        "breakpoint_insertions": len(OVERRUN_ORIGIN_SITES),
        "breakpoint_removals": len(OVERRUN_ORIGIN_SITES),
        "origin_hits": 1 if not errors else 0,
        "memory_read_pairs": memory_pairs,
        "expected_memory_read_pairs": expected_memory_pairs,
        "transcript_command_count": observed_commands,
        "expected_transcript_command_count": expected_commands,
        "debugger_write_packets": writes,
        "errors": errors,
    }


def _run_frame_advance_diagnostic_session(
    *,
    steps: int,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    session_id = "budget_diagnostic"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: Any = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    input_events: list[str] = []
    handshakes: list[dict[str, Any]] = []
    advances: list[dict[str, Any]] = []
    cadence_rows: list[dict[str, Any]] = []
    initial_scenario: dict[str, Any] | None = None
    runtime_copy: dict[str, Any] = {}
    controller_profile: dict[str, Any] = {}
    process_id = 0
    with b03._owned_session_directory(
        prefix="recursion94-m5-budget-diagnostic-",
        transcript=transcript,
    ) as profile:
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        original_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            original_profile_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *b03.LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = b03._launch_owned_emulator(
                command, cwd=isolated_runtime, transcript=transcript
            )
            process_id = process.pid
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            finalizer = symbols["VocabularyFinalizeStage"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("diagnostic VBlank insertion failed")
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            b03._set_gameplay_mask(
                window,
                expected_input_for_total(1),
                pressed,
                input_events,
            )
            _trace_memory_marker(
                transcript,
                edge="begin",
                kind="scenario",
                identity="initial",
            )
            initial_snapshot = b03._capture_snapshot(
                remote, label="initial", symbols=symbols
            )
            _trace_memory_marker(
                transcript,
                edge="end",
                kind="scenario",
                identity="initial",
            )
            if not b03.initialization_ok(initial_snapshot):
                raise EvidenceFailure("diagnostic B03 initialization failed")
            initial_scenario = _scenario_evidence_from_snapshot(
                initial_snapshot
            )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("diagnostic VBlank removal failed")
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure("diagnostic POST insertion failed")

            initial_title = _window_title(window)
            advances.append(
                _tap_frame_advance(
                    window,
                    index=1,
                    purpose="origin_prime",
                    transcript=transcript,
                )
            )
            remote.begin_continue()
            origin_pause = _wait_for_origin_pause(
                window,
                initial_title=initial_title,
                timeout_seconds=PAUSE_OVERWRITE_TIMEOUT_SECONDS,
            )
            transcript.append(
                "# cadence.origin_pause "
                f"title={origin_pause['observed_title']!r}"
            )
            prepared = _set_pause_sentinel(
                window=window,
                session_id=session_id,
                boundary_total=0,
                transcript=transcript,
            )
            last_row: dict[str, Any] | None = None
            for total in range(1, steps + 1):
                advances.append(
                    _tap_frame_advance(
                        window,
                        index=total + 1,
                        purpose=f"authoritative_total_{total}",
                        transcript=transcript,
                    )
                )
                _observe_exact_post(
                    remote,
                    total=total,
                    post_step=post_step,
                    finalizer=finalizer,
                    last_row=last_row,
                    window=window,
                    transcript=transcript,
                )
                row = capture_cadence_row(
                    remote, authoritative_total=total
                )
                row_errors = cadence_row_errors(row)
                if row_errors:
                    raise EvidenceFailure(
                        f"diagnostic cadence total {total}: "
                        + "; ".join(row_errors)
                    )
                cadence_rows.append(row)
                last_row = row
                if total < steps:
                    b03._set_gameplay_mask(
                        window,
                        expected_input_for_total(total + 1),
                        pressed,
                        input_events,
                    )
                else:
                    if remote.request(f"z0,{post_step:x},2") != "OK":
                        raise EvidenceFailure(
                            "diagnostic POST removal failed"
                        )
                handshakes.append(
                    _complete_sentinel_handshake(
                        remote,
                        window=window,
                        prepared=prepared,
                        transcript=transcript,
                    )
                )
                if total < steps:
                    prepared = _set_pause_sentinel(
                        window=window,
                        session_id=session_id,
                        boundary_total=total,
                        transcript=transcript,
                    )
        finally:
            if window is not None:
                for name, virtual_key, scan_code, extended, _bit in b03.KEYS:
                    if name in pressed:
                        try:
                            b03._post_key(
                                window,
                                (name, virtual_key, scan_code, extended),
                                pressed=False,
                            )
                        except Exception as error:
                            transcript.append(
                                "! diagnostic emergency key release failed: "
                                f"{type(error).__name__}: {error}"
                            )
            b03._shutdown_owned_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if initial_scenario is None:
        raise EvidenceFailure("diagnostic produced no initial scenario")
    session_lines = transcript[start_index:]
    audit = audit_diagnostic_transcript(
        session_lines,
        session_id=session_id,
        steps=steps,
        vblank=symbols["VBlankInterrupt"],
        post_step=symbols["MainLoopPostGameplayStep"],
        initial_scenario=initial_scenario,
        cadence_rows=cadence_rows,
    )
    if audit["status"] != "pass":
        raise EvidenceFailure(
            f"diagnostic transcript audit failed: {audit}"
        )
    return {
        "session_id": session_id,
        "process_id": process_id,
        "steps": steps,
        "origin_pause": origin_pause,
        "sentinel_handshakes": handshakes,
        "ui_advance_events": advances,
        "cadence_rows": cadence_rows,
        "initial_scenario": initial_scenario,
        "transcript_audit": audit,
        "transcript_sha256": _sha256_bytes(
            ("\n".join(session_lines) + "\n").encode("utf-8")
        ),
        "runtime_copy": runtime_copy,
        "controller_profile": controller_profile,
        "input_events": input_events,
    }


def _run_overrun_origin_diagnostic_session(
    *,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    """Run exactly through total 3 and identify the first counter path."""
    session_id = "budget_overrun_origin_diagnostic"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: Any = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    input_events: list[str] = []
    handshakes: list[dict[str, Any]] = []
    advances: list[dict[str, Any]] = []
    cadence_rows: list[dict[str, Any]] = []
    initial_scenario: dict[str, Any] | None = None
    origin_hit: dict[str, Any] | None = None
    counter_isolation: dict[str, Any] | None = None
    origin_pause: dict[str, Any] = {}
    runtime_copy: dict[str, Any] = {}
    controller_profile: dict[str, Any] = {}
    process_id = 0
    with b03._owned_session_directory(
        prefix="recursion94-m5-budget-overrun-origin-",
        transcript=transcript,
    ) as profile:
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        original_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            original_profile_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *b03.LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = b03._launch_owned_emulator(
                command, cwd=isolated_runtime, transcript=transcript
            )
            process_id = process.pid
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "overrun-origin VBlank insertion failed"
                )
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            b03._set_gameplay_mask(
                window,
                expected_input_for_total(1),
                pressed,
                input_events,
            )
            _trace_memory_marker(
                transcript,
                edge="begin",
                kind="scenario",
                identity="initial",
            )
            initial_snapshot = b03._capture_snapshot(
                remote, label="initial", symbols=symbols
            )
            _trace_memory_marker(
                transcript,
                edge="end",
                kind="scenario",
                identity="initial",
            )
            if not b03.initialization_ok(initial_snapshot):
                raise EvidenceFailure(
                    "overrun-origin B03 initialization failed"
                )
            initial_scenario = _scenario_evidence_from_snapshot(
                initial_snapshot
            )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "overrun-origin VBlank removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    "overrun-origin POST insertion failed"
                )

            initial_title = _window_title(window)
            advances.append(
                _tap_frame_advance(
                    window,
                    index=1,
                    purpose="origin_prime",
                    transcript=transcript,
                )
            )
            remote.begin_continue()
            origin_pause = _wait_for_origin_pause(
                window,
                initial_title=initial_title,
                timeout_seconds=PAUSE_OVERWRITE_TIMEOUT_SECONDS,
            )
            transcript.append(
                "# cadence.origin_pause "
                f"title={origin_pause['observed_title']!r}"
            )
            prepared = _set_pause_sentinel(
                window=window,
                session_id=session_id,
                boundary_total=0,
                transcript=transcript,
            )
            last_row: dict[str, Any] | None = None
            for total in range(1, OVERRUN_ORIGIN_DIAGNOSTIC_STEPS + 1):
                advances.append(
                    _tap_frame_advance(
                        window,
                        index=total + 1,
                        purpose=f"authoritative_total_{total}",
                        transcript=transcript,
                    )
                )
                if total <= OVERRUN_ORIGIN_ARM_AFTER_TOTAL:
                    _observe_exact_post(
                        remote,
                        total=total,
                        post_step=post_step,
                        finalizer=symbols["VocabularyFinalizeStage"],
                        last_row=last_row,
                        window=window,
                        transcript=transcript,
                    )
                else:
                    stop = remote.observe_stop_response(
                        FRAME_STOP_TIMEOUT_SECONDS
                    )
                    if stop["timed_out"]:
                        raise EvidenceFailure(
                            _stop_timeout_detail(
                                expected_total=total,
                                last_row=last_row,
                                window=window,
                            )
                        )
                    require_stop(str(stop["response"]))
                    pc = b03._read_register_u32(
                        remote, 0x11, label="overrun-origin first stop"
                    )
                    hit_names = [
                        name
                        for name, contract in OVERRUN_ORIGIN_SITES.items()
                        if int(contract["address"]) == pc
                    ]
                    if len(hit_names) != 1:
                        raise EvidenceFailure(
                            "step 3 first stop was not one unique overrun "
                            f"origin: PC ${pc:08X}"
                        )
                    hit_name = hit_names[0]
                    transcript.append(
                        "# overrun_origin.hit total=3 "
                        f"site={hit_name} pc={pc:08X}"
                    )
                    d0 = b03._read_register_u32(
                        remote, 0, label="overrun-origin d0"
                    )
                    d1 = b03._read_register_u32(
                        remote, 1, label="overrun-origin d1"
                    )
                    _trace_memory_marker(
                        transcript,
                        edge="begin",
                        kind="overrun_counter",
                        identity="0003",
                    )
                    counter_raw = remote.read_memory(
                        OVERRUN_COUNTER_ADDRESS, 2
                    )
                    _trace_memory_marker(
                        transcript,
                        edge="end",
                        kind="overrun_counter",
                        identity="0003",
                    )
                    origin_hit = {
                        "authoritative_total": total,
                        "site": hit_name,
                        "pc": f"{pc:08X}",
                        "d0": d0,
                        "d1": d1,
                        "register_low_words": {
                            "d0": d0 & 0xFFFF,
                            "d1": d1 & 0xFFFF,
                        },
                        "counter_before_increment": _block_evidence(
                            OVERRUN_COUNTER_ADDRESS, counter_raw
                        ),
                        "meaning": OVERRUN_ORIGIN_SITES[hit_name][
                            "meaning"
                        ],
                    }
                    expected_registers = OVERRUN_ORIGIN_SITES[hit_name][
                        "register_low_words"
                    ]
                    if (
                        counter_raw != bytes(2)
                        or d0 & 0xFFFF != expected_registers["d0"]
                        or d1 & 0xFFFF != expected_registers["d1"]
                    ):
                        raise EvidenceFailure(
                            "overrun-origin pre-increment state mismatch"
                        )
                    for contract in OVERRUN_ORIGIN_SITES.values():
                        address = int(contract["address"])
                        if remote.request(f"z0,{address:x},2") != "OK":
                            raise EvidenceFailure(
                                "overrun-origin breakpoint removal failed "
                                f"at ${address:08X}"
                            )
                    require_stop(remote.request("c"))
                    post_pc = b03._read_register_u32(
                        remote, 0x11, label="overrun-origin step 3 POST"
                    )
                    if post_pc != post_step:
                        raise EvidenceFailure(
                            "step 3 continuation did not reach POST: "
                            f"${post_pc:08X}"
                        )
                    transcript.append(
                        "# cadence.stop total=3 role=post "
                        f"pc={post_pc:08X}"
                    )

                row = capture_cadence_row(
                    remote, authoritative_total=total
                )
                if total <= OVERRUN_ORIGIN_ARM_AFTER_TOTAL:
                    row_errors = cadence_row_errors(row)
                    if row_errors:
                        raise EvidenceFailure(
                            f"overrun-origin clean total {total}: "
                            + "; ".join(row_errors)
                        )
                else:
                    counter_isolation = overrun_counter_isolation(row)
                    if counter_isolation["status"] != "pass":
                        raise EvidenceFailure(
                            "overrun-origin total 3 was not an isolated "
                            f"counter fault: {counter_isolation}"
                        )
                cadence_rows.append(row)
                last_row = row

                if total < OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
                    b03._set_gameplay_mask(
                        window,
                        expected_input_for_total(total + 1),
                        pressed,
                        input_events,
                    )
                if total == OVERRUN_ORIGIN_ARM_AFTER_TOTAL:
                    site_text = ",".join(
                        f"{name}={int(contract['address']):08X}"
                        for name, contract in OVERRUN_ORIGIN_SITES.items()
                    )
                    transcript.append(
                        "# overrun_origin.arm boundary=2 sites=" + site_text
                    )
                    for contract in OVERRUN_ORIGIN_SITES.values():
                        address = int(contract["address"])
                        if remote.request(f"Z0,{address:x},2") != "OK":
                            raise EvidenceFailure(
                                "overrun-origin breakpoint insertion failed "
                                f"at ${address:08X}"
                            )
                if total == OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
                    if remote.request(f"z0,{post_step:x},2") != "OK":
                        raise EvidenceFailure(
                            "overrun-origin POST removal failed"
                        )
                handshakes.append(
                    _complete_sentinel_handshake(
                        remote,
                        window=window,
                        prepared=prepared,
                        transcript=transcript,
                    )
                )
                if total < OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
                    prepared = _set_pause_sentinel(
                        window=window,
                        session_id=session_id,
                        boundary_total=total,
                        transcript=transcript,
                    )
        finally:
            if window is not None:
                for name, virtual_key, scan_code, extended, _bit in b03.KEYS:
                    if name in pressed:
                        try:
                            b03._post_key(
                                window,
                                (name, virtual_key, scan_code, extended),
                                pressed=False,
                            )
                        except Exception as error:
                            transcript.append(
                                "! overrun-origin emergency key release "
                                f"failed: {type(error).__name__}: {error}"
                            )
            b03._shutdown_owned_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if (
        initial_scenario is None
        or origin_hit is None
        or counter_isolation is None
    ):
        raise EvidenceFailure(
            "overrun-origin diagnostic did not produce complete evidence"
        )
    session_lines = transcript[start_index:]
    audit = audit_overrun_origin_transcript(
        session_lines,
        session_id=session_id,
        vblank=symbols["VBlankInterrupt"],
        post_step=symbols["MainLoopPostGameplayStep"],
        initial_scenario=initial_scenario,
        cadence_rows=cadence_rows,
        origin_hit=origin_hit,
    )
    if audit["status"] != "pass":
        raise EvidenceFailure(
            f"overrun-origin transcript audit failed: {audit}"
        )
    return {
        "session_id": session_id,
        "process_id": process_id,
        "fixed_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
        "armed_after_clean_total": OVERRUN_ORIGIN_ARM_AFTER_TOTAL,
        "origin_pause": origin_pause,
        "sentinel_handshakes": handshakes,
        "ui_advance_events": advances,
        "cadence_rows": cadence_rows,
        "initial_scenario": initial_scenario,
        "origin_hit": origin_hit,
        "counter_isolation": counter_isolation,
        "transcript_audit": audit,
        "transcript_sha256": _sha256_bytes(
            ("\n".join(session_lines) + "\n").encode("utf-8")
        ),
        "runtime_copy": runtime_copy,
        "controller_profile": controller_profile,
        "input_events": input_events,
    }


def audit_natural_overrun_control_transcript(
    lines: list[str],
    *,
    symbols: dict[str, int],
    origin_site: str | None,
) -> dict[str, Any]:
    """Audit the bounded natural PRE/POST control without accepting UI drive."""
    pre = symbols["MainLoopGameplayStepCall"]
    post = symbols["MainLoopPostGameplayStep"]
    vblank = symbols["VBlankInterrupt"]
    errors: list[str] = []
    if (
        not lines
        or lines[0] != "# budget_overrun_natural_control begin"
        or lines[-1] != "# budget_overrun_natural_control end"
    ):
        errors.append("natural control session boundary mismatch")
    forbidden = [
        line
        for line in lines
        if "ui.advance" in line
        or "cadence.sentinel" in line
        or "cadence.pause" in line
        or "origin_pause" in line
    ]
    if forbidden:
        errors.append(f"natural control contains UI drive: {forbidden}")
    markers: list[tuple[int, str, int]] = []
    for line in lines:
        match = re.fullmatch(
            r"# natural_control\.stop total=([123]) "
            r"role=(pre|post) pc=([0-9A-F]{8})",
            line,
        )
        if match:
            markers.append(
                (int(match.group(1)), match.group(2), int(match.group(3), 16))
            )
    expected_markers = [
        (total, role, pre if role == "pre" else post)
        for total in (1, 2, 3)
        for role in ("pre", "post")
    ]
    if markers != expected_markers:
        errors.append("natural PRE/POST marker sequence mismatch")
    breakpoint_counts = {
        "vblank_insert": lines.count(f"> Z0,{vblank:x},2"),
        "vblank_remove": lines.count(f"> z0,{vblank:x},2"),
        "pre_insert": lines.count(f"> Z0,{pre:x},2"),
        "pre_remove": lines.count(f"> z0,{pre:x},2"),
        "post_insert": lines.count(f"> Z0,{post:x},2"),
        "post_remove": lines.count(f"> z0,{post:x},2"),
        "origin_insert": sum(
            lines.count(f"> Z0,{int(site['address']):x},2")
            for site in OVERRUN_ORIGIN_SITES.values()
        ),
        "origin_remove": sum(
            lines.count(f"> z0,{int(site['address']):x},2")
            for site in OVERRUN_ORIGIN_SITES.values()
        ),
    }
    if breakpoint_counts != {
        "vblank_insert": 1,
        "vblank_remove": 1,
        "pre_insert": 4,
        "pre_remove": 4,
        "post_insert": 1,
        "post_remove": 1,
        "origin_insert": 2,
        "origin_remove": 2,
    }:
        errors.append(
            f"natural control breakpoint lifecycle mismatch: {breakpoint_counts}"
        )
    clear_markers = [
        line for line in lines if line.startswith("# natural_control.origin_clear ")
    ]
    hit_markers = [
        line for line in lines if line.startswith("# overrun_origin.hit ")
    ]
    if origin_site is None:
        if len(clear_markers) != 1 or hit_markers:
            errors.append("natural clear origin marker mismatch")
    elif origin_site in OVERRUN_ORIGIN_SITES:
        if clear_markers or len(hit_markers) != 1:
            errors.append("natural fault origin marker mismatch")
    else:
        errors.append("natural origin site is unknown")
    memory_markers = {
        kind: sum(
            line.startswith(f"# memory.begin kind={kind} ")
            for line in lines
        )
        for kind in (
            "scenario",
            "natural_pre",
            "natural_post",
            "cadence",
            "overrun_counter",
        )
    }
    expected_counter_markers = 1 + (1 if origin_site is not None else 0)
    if memory_markers != {
        "scenario": 1,
        "natural_pre": 3,
        "natural_post": 3,
        "cadence": 3,
        "overrun_counter": expected_counter_markers,
    }:
        errors.append(
            f"natural control memory marker mismatch: {memory_markers}"
        )
    writes = debugger_write_packets(lines)
    if writes:
        errors.append(f"natural control debugger writes present: {writes}")
    return {
        "status": "pass" if not errors else "fail",
        "fixed_authoritative_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
        "natural_pre_stops": sum(item[1] == "pre" for item in markers),
        "natural_post_stops": sum(item[1] == "post" for item in markers),
        "origin_site": origin_site,
        "breakpoint_counts": breakpoint_counts,
        "memory_markers": memory_markers,
        "forbidden_ui_drive": forbidden,
        "debugger_write_packets": writes,
        "errors": errors,
    }


def _run_natural_overrun_control_session(
    *,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    session_id = "budget_overrun_natural_control"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: Any = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    input_events: list[str] = []
    cadence_rows: list[dict[str, Any]] = []
    pre_boundaries: list[dict[str, Any]] = []
    post_boundaries: list[dict[str, Any]] = []
    initial_scenario: dict[str, Any] | None = None
    origin_hit: dict[str, Any] | None = None
    counter_at_total_3_pre: dict[str, Any] | None = None
    runtime_copy: dict[str, Any] = {}
    controller_profile: dict[str, Any] = {}
    process_id = 0
    with b03._owned_session_directory(
        prefix="recursion94-m5-budget-overrun-natural-",
        transcript=transcript,
    ) as profile:
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        original_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            original_profile_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *b03.LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = b03._launch_owned_emulator(
                command, cwd=isolated_runtime, transcript=transcript
            )
            process_id = process.pid
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            vblank = symbols["VBlankInterrupt"]
            pre_step = symbols["MainLoopGameplayStepCall"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "natural control VBlank insertion failed"
                )
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            b03._set_gameplay_mask(
                window,
                expected_input_for_total(1),
                pressed,
                input_events,
            )
            _trace_memory_marker(
                transcript,
                edge="begin",
                kind="scenario",
                identity="initial",
            )
            initial_snapshot = b03._capture_snapshot(
                remote, label="initial", symbols=symbols
            )
            _trace_memory_marker(
                transcript,
                edge="end",
                kind="scenario",
                identity="initial",
            )
            if not b03.initialization_ok(initial_snapshot):
                raise EvidenceFailure(
                    "natural control B03 initialization failed"
                )
            initial_scenario = _scenario_evidence_from_snapshot(
                initial_snapshot
            )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "natural control VBlank removal failed"
                )
            if remote.request(f"Z0,{pre_step:x},2") != "OK":
                raise EvidenceFailure("natural control PRE insertion failed")
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure("natural control POST insertion failed")

            for total in range(1, OVERRUN_ORIGIN_DIAGNOSTIC_STEPS + 1):
                if total < OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
                    require_stop(remote.request("c"))
                    first_pc = b03._read_register_u32(
                        remote, 0x11, label=f"natural total {total} PRE"
                    )
                else:
                    remote.begin_continue()
                    stop = remote.observe_stop_response(
                        FRAME_STOP_TIMEOUT_SECONDS
                    )
                    if stop["timed_out"]:
                        raise EvidenceFailure(
                            "natural total 3 did not reach PRE or an "
                            "overrun-origin breakpoint"
                        )
                    require_stop(str(stop["response"]))
                    first_pc = b03._read_register_u32(
                        remote, 0x11, label="natural total 3 first stop"
                    )
                    hit_names = [
                        name
                        for name, contract in OVERRUN_ORIGIN_SITES.items()
                        if int(contract["address"]) == first_pc
                    ]
                    if hit_names:
                        if len(hit_names) != 1:
                            raise EvidenceFailure(
                                "natural control origin site was ambiguous"
                            )
                        hit_name = hit_names[0]
                        transcript.append(
                            "# overrun_origin.hit total=3 "
                            f"site={hit_name} pc={first_pc:08X}"
                        )
                        d0_origin = b03._read_register_u32(
                            remote, 0, label="natural origin d0"
                        )
                        d1_origin = b03._read_register_u32(
                            remote, 1, label="natural origin d1"
                        )
                        _trace_memory_marker(
                            transcript,
                            edge="begin",
                            kind="overrun_counter",
                            identity="natural_origin_0003",
                        )
                        counter_before = remote.read_memory(
                            OVERRUN_COUNTER_ADDRESS, 2
                        )
                        _trace_memory_marker(
                            transcript,
                            edge="end",
                            kind="overrun_counter",
                            identity="natural_origin_0003",
                        )
                        origin_hit = {
                            "site": hit_name,
                            "pc": f"{first_pc:08X}",
                            "d0": d0_origin,
                            "d1": d1_origin,
                            "counter_before_increment": _block_evidence(
                                OVERRUN_COUNTER_ADDRESS, counter_before
                            ),
                            "meaning": OVERRUN_ORIGIN_SITES[hit_name][
                                "meaning"
                            ],
                        }
                    elif first_pc == pre_step:
                        transcript.append(
                            "# natural_control.origin_clear total=3 "
                            f"pc={first_pc:08X}"
                        )
                    else:
                        raise EvidenceFailure(
                            "natural total 3 first stop was neither PRE nor "
                            f"an origin site: ${first_pc:08X}"
                        )
                    for contract in OVERRUN_ORIGIN_SITES.values():
                        address = int(contract["address"])
                        if remote.request(f"z0,{address:x},2") != "OK":
                            raise EvidenceFailure(
                                "natural origin breakpoint removal failed "
                                f"at ${address:08X}"
                            )
                    if origin_hit is not None:
                        require_stop(remote.request("c"))
                        first_pc = b03._read_register_u32(
                            remote,
                            0x11,
                            label="natural total 3 PRE after origin",
                        )

                if first_pc != pre_step:
                    raise EvidenceFailure(
                        f"natural total {total} stopped at ${first_pc:08X}; "
                        f"PRE ${pre_step:08X} required"
                    )
                transcript.append(
                    "# natural_control.stop "
                    f"total={total} role=pre pc={first_pc:08X}"
                )
                d0 = b03._read_register_u32(
                    remote, 0, label=f"natural total {total} PRE d0"
                )
                pre_boundary = capture_natural_boundary(
                    remote,
                    phase="pre",
                    authoritative_total=total,
                    d0=d0,
                )
                pre_errors = natural_boundary_errors(pre_boundary)
                if pre_errors:
                    raise EvidenceFailure(
                        f"natural total {total} PRE failed: {pre_errors}"
                    )
                pre_boundaries.append(pre_boundary)
                if total == OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
                    _trace_memory_marker(
                        transcript,
                        edge="begin",
                        kind="overrun_counter",
                        identity="natural_pre_0003",
                    )
                    counter_raw = remote.read_memory(
                        OVERRUN_COUNTER_ADDRESS, 2
                    )
                    _trace_memory_marker(
                        transcript,
                        edge="end",
                        kind="overrun_counter",
                        identity="natural_pre_0003",
                    )
                    counter_at_total_3_pre = _block_evidence(
                        OVERRUN_COUNTER_ADDRESS, counter_raw
                    )
                if remote.request(f"z0,{pre_step:x},2") != "OK":
                    raise EvidenceFailure(
                        f"natural total {total} PRE removal failed"
                    )
                next_desired = expected_input_for_total(total + 1)
                b03._set_gameplay_mask(
                    window,
                    next_desired,
                    pressed,
                    input_events,
                )
                require_stop(remote.request("c"))
                post_pc = b03._read_register_u32(
                    remote, 0x11, label=f"natural total {total} POST"
                )
                if post_pc != post_step:
                    raise EvidenceFailure(
                        f"natural total {total} stopped at ${post_pc:08X}; "
                        f"POST ${post_step:08X} required"
                    )
                transcript.append(
                    "# natural_control.stop "
                    f"total={total} role=post pc={post_pc:08X}"
                )
                if remote.request(f"Z0,{pre_step:x},2") != "OK":
                    raise EvidenceFailure(
                        f"natural total {total} PRE reinsertion failed"
                    )
                post_boundary = capture_natural_boundary(
                    remote,
                    phase="post",
                    authoritative_total=total,
                    d0=None,
                )
                post_errors = natural_boundary_errors(post_boundary)
                if post_errors:
                    raise EvidenceFailure(
                        f"natural total {total} POST failed: {post_errors}"
                    )
                post_boundaries.append(post_boundary)
                row = capture_cadence_row(
                    remote, authoritative_total=total
                )
                row_errors = cadence_row_errors(row)
                if origin_hit is None:
                    if row_errors:
                        raise EvidenceFailure(
                            f"natural total {total} cadence failed: "
                            + "; ".join(row_errors)
                        )
                elif total == OVERRUN_ORIGIN_DIAGNOSTIC_STEPS:
                    isolation = overrun_counter_isolation(row)
                    if isolation["status"] != "pass":
                        raise EvidenceFailure(
                            f"natural origin row was not isolated: {isolation}"
                        )
                cadence_rows.append(row)
                if total == OVERRUN_ORIGIN_ARM_AFTER_TOTAL:
                    site_text = ",".join(
                        f"{name}={int(contract['address']):08X}"
                        for name, contract in OVERRUN_ORIGIN_SITES.items()
                    )
                    transcript.append(
                        "# overrun_origin.arm boundary=2 sites=" + site_text
                    )
                    for contract in OVERRUN_ORIGIN_SITES.values():
                        address = int(contract["address"])
                        if remote.request(f"Z0,{address:x},2") != "OK":
                            raise EvidenceFailure(
                                "natural origin breakpoint insertion failed "
                                f"at ${address:08X}"
                            )

            if remote.request(f"z0,{pre_step:x},2") != "OK":
                raise EvidenceFailure("natural final PRE removal failed")
            if remote.request(f"z0,{post_step:x},2") != "OK":
                raise EvidenceFailure("natural final POST removal failed")
        finally:
            if window is not None:
                for name, virtual_key, scan_code, extended, _bit in b03.KEYS:
                    if name in pressed:
                        try:
                            b03._post_key(
                                window,
                                (name, virtual_key, scan_code, extended),
                                pressed=False,
                            )
                        except Exception as error:
                            transcript.append(
                                "! natural control emergency key release "
                                f"failed: {type(error).__name__}: {error}"
                            )
            b03._shutdown_owned_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if initial_scenario is None or counter_at_total_3_pre is None:
        raise EvidenceFailure("natural control produced incomplete evidence")
    session_lines = transcript[start_index:]
    audit = audit_natural_overrun_control_transcript(
        session_lines,
        symbols=symbols,
        origin_site=(origin_hit["site"] if origin_hit else None),
    )
    if audit["status"] != "pass":
        raise EvidenceFailure(
            f"natural control transcript audit failed: {audit}"
        )
    return {
        "session_id": session_id,
        "process_id": process_id,
        "fixed_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
        "drive": "natural_MainLoopGameplayStepCall_PRE_POST",
        "ui_advance_pulses": 0,
        "pre_boundaries": pre_boundaries,
        "post_boundaries": post_boundaries,
        "cadence_rows": cadence_rows,
        "initial_scenario": initial_scenario,
        "origin_hit": origin_hit,
        "counter_at_total_3_pre": counter_at_total_3_pre,
        "transcript_audit": audit,
        "transcript_sha256": _sha256_bytes(
            ("\n".join(session_lines) + "\n").encode("utf-8")
        ),
        "runtime_copy": runtime_copy,
        "controller_profile": controller_profile,
        "input_events": input_events,
        "symbol_map_sha256": sha256_file(symbol_path),
    }


def _interrupt_running_remote(remote: GdbRemote) -> str:
    """Bounded diagnostic-only Ctrl-C; never writes guest state."""
    remote.transcript.append("> <interrupt>")
    remote.socket.sendall(b"\x03")
    response = remote.response()
    remote.transcript.append(f"< {response}")
    return response


def _trace_section_raw(
    lines: list[str],
    *,
    kind: str,
    identity: str,
) -> dict[str, bytes]:
    begin = f"# memory.begin kind={kind} id={identity}"
    end = f"# memory.end kind={kind} id={identity}"
    if lines.count(begin) != 1 or lines.count(end) != 1:
        raise ValueError(f"trace section {kind}/{identity} is not unique")
    start = lines.index(begin)
    cursor, raw = _consume_read_plan(
        lines,
        start + 1,
        regions=_trace_read_plan(kind),
    )
    if cursor >= len(lines) or lines[cursor] != end:
        raise ValueError(f"trace section {kind}/{identity} end mismatch")
    return raw


def audit_continuous_overrun_transcript(
    lines: list[str],
    *,
    symbols: dict[str, int],
    initial_scenario: dict[str, Any],
    stopped_state: dict[str, Any],
    origin_hit: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[str] = []
    vblank = symbols["VBlankInterrupt"]
    pre = symbols["MainLoopGameplayStepCall"]
    post = symbols["MainLoopPostGameplayStep"]
    if (
        not lines
        or lines[0] != "# budget_overrun_continuous_control begin"
        or lines[-1] != "# budget_overrun_continuous_control end"
    ):
        errors.append("continuous control session boundary mismatch")
    forbidden = [
        line
        for line in lines
        if "ui.advance" in line
        or "cadence.sentinel" in line
        or "cadence.pause" in line
        or line in {
            f"> Z0,{pre:x},2",
            f"> z0,{pre:x},2",
            f"> Z0,{post:x},2",
            f"> z0,{post:x},2",
        }
    ]
    if forbidden:
        errors.append(f"continuous control contains per-frame drive: {forbidden}")
    lifecycle = {
        "vblank_insert": lines.count(f"> Z0,{vblank:x},2"),
        "vblank_remove": lines.count(f"> z0,{vblank:x},2"),
        "origin_insert": sum(
            lines.count(f"> Z0,{int(site['address']):x},2")
            for site in OVERRUN_ORIGIN_SITES.values()
        ),
        "origin_remove": sum(
            lines.count(f"> z0,{int(site['address']):x},2")
            for site in OVERRUN_ORIGIN_SITES.values()
        ),
        "continue_commands": lines.count("> c"),
        "interrupt_commands": lines.count("> <interrupt>"),
    }
    expected_interrupts = 0 if origin_hit else 1
    if lifecycle != {
        "vblank_insert": 1,
        "vblank_remove": 1,
        "origin_insert": 2,
        "origin_remove": 2,
        "continue_commands": 2,
        "interrupt_commands": expected_interrupts,
    }:
        errors.append(f"continuous breakpoint lifecycle mismatch: {lifecycle}")
    for command in [
        f"> Z0,{vblank:x},2",
        f"> z0,{vblank:x},2",
        *[
            command
            for site in OVERRUN_ORIGIN_SITES.values()
            for command in (
                f"> Z0,{int(site['address']):x},2",
                f"> z0,{int(site['address']):x},2",
            )
        ],
    ]:
        positions = [
            index for index, line in enumerate(lines) if line == command
        ]
        if any(
            index + 1 >= len(lines) or lines[index + 1] != "< OK"
            for index in positions
        ):
            errors.append(f"{command!r} lacks adjacent OK")
    begin_marker = (
        "# continuous_probe.begin "
        f"seconds={OVERRUN_CONTINUOUS_OBSERVE_SECONDS:.3f}"
    )
    if lines.count(begin_marker) != 1:
        errors.append("continuous measured-interval marker mismatch")
    else:
        interval_start = lines.index(begin_marker)
        first_fact_marker = next(
            (
                index
                for index in range(interval_start + 1, len(lines))
                if lines[index].startswith("# memory.begin kind=continuous_state ")
            ),
            len(lines),
        )
        interval = lines[interval_start + 1 : first_fact_marker]
        expected_prefix = ["> c", "< S05", "> p11"]
        if origin_hit is None:
            if (
                len(interval) < 5
                or interval[0] != "> c"
                or not interval[1].startswith("< no stop response within ")
                or interval[2:5] != ["> <interrupt>", "< S05", "> p11"]
            ):
                errors.append("continuous clear measured interval mismatch")
        elif interval[:3] != expected_prefix:
            errors.append("continuous hit measured interval mismatch")
        if interval.count("> c") != 1:
            errors.append("continuous interval does not contain one continue")
        if any(
            line.startswith("> ")
            and line not in {"> c", "> p11", "> p0", "> p1", "> <interrupt>"}
            for line in interval
        ):
            errors.append("continuous interval contains an extra command")
    try:
        initial_raw = _trace_section_raw(
            lines, kind="scenario", identity="initial"
        )
        stopped_raw = _trace_section_raw(
            lines, kind="continuous_state", identity="stopped"
        )
        for name, raw in initial_raw.items():
            if raw.hex().upper() != initial_scenario["blocks"][name][
                "raw_hex"
            ]:
                raise ValueError(f"initial/{name} report binding mismatch")
        for name, raw in stopped_raw.items():
            if raw.hex().upper() != stopped_state["blocks"][name][
                "raw_hex"
            ]:
                raise ValueError(f"stopped/{name} report binding mismatch")
    except (KeyError, ValueError) as error:
        errors.append(str(error))
    hit_markers = [
        line for line in lines if line.startswith("# continuous_probe.hit ")
    ]
    clear_markers = [
        line for line in lines if line.startswith("# continuous_probe.clear ")
    ]
    if origin_hit is None:
        if hit_markers or len(clear_markers) != 1:
            errors.append("continuous clear marker mismatch")
    elif origin_hit.get("site") in OVERRUN_ORIGIN_SITES:
        if len(hit_markers) != 1 or clear_markers:
            errors.append("continuous hit marker mismatch")
    else:
        errors.append("continuous origin site mismatch")
    writes = debugger_write_packets(lines)
    if writes:
        errors.append(f"continuous debugger writes present: {writes}")
    unknown_commands = [
        line
        for line in lines
        if line.startswith("> ")
        and not (
            line in {"> c", "> p11", "> p0", "> p1", "> k", "> <interrupt>"}
            or re.fullmatch(r"> [Zz]0,[0-9a-f]+,2", line)
            or re.fullmatch(r"> m[0-9a-f]+,[0-9a-f]+", line)
        )
    ]
    if unknown_commands:
        errors.append(f"continuous unknown commands: {unknown_commands}")
    return {
        "status": "pass" if not errors else "fail",
        "near_continuous_seconds": OVERRUN_CONTINUOUS_OBSERVE_SECONDS,
        "per_frame_pre_post_breakpoints": 0,
        "ui_advance_pulses": 0,
        "breakpoint_lifecycle": lifecycle,
        "forbidden_per_frame_drive": forbidden,
        "debugger_write_packets": writes,
        "unknown_commands": unknown_commands,
        "errors": errors,
    }


def _run_continuous_overrun_control_session(
    *,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    session_id = "budget_overrun_continuous_control"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: Any = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    input_events: list[str] = []
    initial_scenario: dict[str, Any] | None = None
    stopped_state: dict[str, Any] | None = None
    origin_hit: dict[str, Any] | None = None
    stop_kind = ""
    stopped_pc = 0
    observe_elapsed = 0.0
    runtime_copy: dict[str, Any] = {}
    controller_profile: dict[str, Any] = {}
    process_id = 0
    with b03._owned_session_directory(
        prefix="recursion94-m5-budget-overrun-continuous-",
        transcript=transcript,
    ) as profile:
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        original_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            original_profile_sha
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *b03.LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = b03._launch_owned_emulator(
                command, cwd=isolated_runtime, transcript=transcript
            )
            process_id = process.pid
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            vblank = symbols["VBlankInterrupt"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "continuous control VBlank insertion failed"
                )
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            b03._set_gameplay_mask(
                window,
                expected_input_for_total(1),
                pressed,
                input_events,
            )
            _trace_memory_marker(
                transcript,
                edge="begin",
                kind="scenario",
                identity="initial",
            )
            initial_snapshot = b03._capture_snapshot(
                remote, label="initial", symbols=symbols
            )
            _trace_memory_marker(
                transcript,
                edge="end",
                kind="scenario",
                identity="initial",
            )
            if not b03.initialization_ok(initial_snapshot):
                raise EvidenceFailure(
                    "continuous control B03 initialization failed"
                )
            initial_scenario = _scenario_evidence_from_snapshot(
                initial_snapshot
            )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    "continuous control VBlank removal failed"
                )
            for contract in OVERRUN_ORIGIN_SITES.values():
                address = int(contract["address"])
                if remote.request(f"Z0,{address:x},2") != "OK":
                    raise EvidenceFailure(
                        "continuous origin breakpoint insertion failed "
                        f"at ${address:08X}"
                    )
            transcript.append(
                "# continuous_probe.begin "
                f"seconds={OVERRUN_CONTINUOUS_OBSERVE_SECONDS:.3f}"
            )
            remote.begin_continue()
            stop = remote.observe_stop_response(
                OVERRUN_CONTINUOUS_OBSERVE_SECONDS
            )
            observe_elapsed = float(stop["observation_seconds"])
            if stop["timed_out"]:
                require_stop(_interrupt_running_remote(remote))
                stop_kind = "bounded_interrupt"
            else:
                require_stop(str(stop["response"]))
                stop_kind = "origin_breakpoint"
            stopped_pc = b03._read_register_u32(
                remote, 0x11, label="continuous probe stop"
            )
            hit_names = [
                name
                for name, contract in OVERRUN_ORIGIN_SITES.items()
                if int(contract["address"]) == stopped_pc
            ]
            d0 = b03._read_register_u32(
                remote, 0, label="continuous probe d0"
            )
            d1 = b03._read_register_u32(
                remote, 1, label="continuous probe d1"
            )
            stopped_state = capture_continuous_probe_state(
                remote, identity="stopped"
            )
            if stop_kind == "origin_breakpoint":
                if len(hit_names) != 1:
                    raise EvidenceFailure(
                        "continuous breakpoint stop was not one origin site: "
                        f"${stopped_pc:08X}"
                    )
                hit_name = hit_names[0]
                origin_hit = {
                    "site": hit_name,
                    "pc": f"{stopped_pc:08X}",
                    "d0": d0,
                    "d1": d1,
                    "register_low_words": {
                        "d0": d0 & 0xFFFF,
                        "d1": d1 & 0xFFFF,
                    },
                    "counter_before_increment": _block_evidence(
                        OVERRUN_COUNTER_ADDRESS,
                        bytes.fromhex(
                            stopped_state["blocks"]["debug_budget"][
                                "raw_hex"
                            ]
                        )[0x32:0x34],
                    ),
                    "meaning": OVERRUN_ORIGIN_SITES[hit_name]["meaning"],
                }
                transcript.append(
                    "# continuous_probe.hit "
                    f"site={hit_name} pc={stopped_pc:08X}"
                )
                expected_registers = OVERRUN_ORIGIN_SITES[hit_name][
                    "register_low_words"
                ]
                debug = stopped_state["debug"]
                if (
                    d0 & 0xFFFF != expected_registers["d0"]
                    or d1 & 0xFFFF != expected_registers["d1"]
                    or debug["vblank_overrun_count"] != 0
                    or debug["boot_stage"] != BOOT_STAGE_RUNNING
                    or debug["frame_counter"] < 1
                ):
                    raise EvidenceFailure(
                        "continuous origin pre-increment state mismatch"
                    )
                if hit_name == "vblank_exit" and (
                    debug["sequence"]
                    != debug["frame_counter"] * 2 - 1
                    or debug["frame_counter"] < 3
                    or debug["heartbeat"] != debug["frame_counter"]
                    or debug["frame_token"] < 1
                    or debug["last_vdp_status"] & 0x0008 == 0
                    or stopped_state["engine"]["state"]
                    != ENGINE_STATE_ACTIVE
                    or stopped_state["engine"]["attempt"] != 0
                    or not 2
                    <= stopped_state["engine"]["tick"]
                    < debug["frame_counter"]
                ):
                    raise EvidenceFailure(
                        "continuous VBlank-exit active-frame signature "
                        "mismatch"
                    )
            else:
                if hit_names:
                    raise EvidenceFailure(
                        "continuous timeout interrupt ambiguously landed on "
                        "an origin site"
                    )
                transcript.append(
                    "# continuous_probe.clear "
                    f"pc={stopped_pc:08X}"
                )
                debug = stopped_state["debug"]
                if (
                    debug["vblank_overrun_count"] != 0
                    or debug["frame_counter"]
                    < OVERRUN_CONTINUOUS_MINIMUM_CLEAR_FRAMES
                ):
                    raise EvidenceFailure(
                        "continuous clear run did not cover the minimum "
                        "healthy frame interval"
                    )
            for contract in OVERRUN_ORIGIN_SITES.values():
                address = int(contract["address"])
                if remote.request(f"z0,{address:x},2") != "OK":
                    raise EvidenceFailure(
                        "continuous origin breakpoint removal failed "
                        f"at ${address:08X}"
                    )
        finally:
            if window is not None:
                for name, virtual_key, scan_code, extended, _bit in b03.KEYS:
                    if name in pressed:
                        try:
                            b03._post_key(
                                window,
                                (name, virtual_key, scan_code, extended),
                                pressed=False,
                            )
                        except Exception as error:
                            transcript.append(
                                "! continuous control emergency key release "
                                f"failed: {type(error).__name__}: {error}"
                            )
            b03._shutdown_owned_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if initial_scenario is None or stopped_state is None:
        raise EvidenceFailure("continuous control produced incomplete evidence")
    session_lines = transcript[start_index:]
    audit = audit_continuous_overrun_transcript(
        session_lines,
        symbols=symbols,
        initial_scenario=initial_scenario,
        stopped_state=stopped_state,
        origin_hit=origin_hit,
    )
    if audit["status"] != "pass":
        raise EvidenceFailure(
            f"continuous control transcript audit failed: {audit}"
        )
    debug = stopped_state["debug"]
    return {
        "session_id": session_id,
        "process_id": process_id,
        "drive": "near_continuous_after_stage_ack",
        "observe_seconds": OVERRUN_CONTINUOUS_OBSERVE_SECONDS,
        "observe_elapsed_milliseconds": observe_elapsed * 1000.0,
        "stop_kind": stop_kind,
        "stopped_pc": f"{stopped_pc:08X}",
        "origin_hit": origin_hit,
        "stopped_state": stopped_state,
        "requested_facts": {
            "debug_sequence": debug["sequence"],
            "debug_frame_counter": debug["frame_counter"],
            "debug_heartbeat": debug["heartbeat"],
            "boot_stage": debug["boot_stage"],
            "engine_state": stopped_state["engine"]["state"],
            "engine_attempt": stopped_state["engine"]["attempt"],
            "engine_tick": stopped_state["engine"]["tick"],
            "frame_token": debug["frame_token"],
            "last_vdp_status": debug["last_vdp_status"],
            "vblank_overrun_count_before_increment": debug[
                "vblank_overrun_count"
            ],
            "dma_words_frame": debug["dma_words_frame"],
            "dma_words_max": debug["dma_words_max"],
            "dma_queue_depth": debug["dma_queue_depth"],
            "dma_queue_high_water": debug["dma_queue_high_water"],
        },
        "initial_scenario": initial_scenario,
        "transcript_audit": audit,
        "transcript_sha256": _sha256_bytes(
            ("\n".join(session_lines) + "\n").encode("utf-8")
        ),
        "runtime_copy": runtime_copy,
        "controller_profile": controller_profile,
        "input_events": input_events,
        "symbol_map_sha256": sha256_file(symbol_path),
    }


def _run_diagnostic_cli(
    *,
    root: Path,
    steps: int,
    report_path: Path | None,
    trace_path: Path | None,
    timeout_seconds: float,
    lock_timeout_seconds: float,
) -> int:
    path_errors = diagnostic_output_path_errors(
        root=root, report=report_path, trace=trace_path
    )
    if path_errors:
        for error in path_errors:
            print(error)
        return 2
    if not 1 <= steps <= 725:
        print("--diagnostic-steps must be within 1..725")
        return 2
    if not 5.0 <= timeout_seconds <= 600.0:
        print("--timeout-seconds must be within 5..600")
        return 2
    if not 5.0 <= lock_timeout_seconds <= 600.0:
        print("--lock-timeout-seconds must be within 5..600")
        return 2
    assert report_path is not None and trace_path is not None
    report_path = report_path.resolve()
    trace_path = trace_path.resolve()
    transcript: list[str] = []
    binding_paths = _hash_binding_paths(root)
    provenance: dict[str, Any] = {}
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live budget diagnostic requires native Windows BlastEm"
            )
        schedule_contract()
        hashes = _capture_hashes(binding_paths)
        for name, expected in (
            ("rom", PINNED_ROM_SHA256),
            ("symbol_map", PINNED_SYMBOL_SHA256),
            ("emulator", PINNED_EMULATOR_SHA256),
            ("default_config", PINNED_DEFAULT_CONFIG_SHA256),
        ):
            if hashes[name] != expected:
                raise EvidenceFailure(
                    f"diagnostic pinned {name} SHA-256 mismatch"
                )
        _runtime_payload, prerequisite = load_runtime_prerequisite(
            report_path=binding_paths["runtime_report"],
            trace_path=binding_paths["runtime_trace"],
            replay_path=binding_paths["runtime_replay"],
        )
        del _runtime_payload
        source_contract = frame_advance_source_contract(
            binding_paths["default_config"]
        )
        if source_contract["status"] != "pass":
            raise EvidenceFailure("diagnostic frame source contract failed")
        symbols = parse_vasm_symbols(binding_paths["symbol_map"])
        if [name for name in REQUIRED_SYMBOLS if name not in symbols]:
            raise EvidenceFailure("diagnostic symbol map is incomplete")
        layout_errors = symbol_layout_errors(symbols)
        if layout_errors:
            raise EvidenceFailure(
                f"diagnostic symbol layout failed: {layout_errors[:3]}"
            )
        verify_no_user_blastem_override()
        provenance = {
            "rom_sha256": hashes["rom"],
            "symbol_map_sha256": hashes["symbol_map"],
            "emulator_sha256": hashes["emulator"],
            "default_config_sha256": hashes["default_config"],
            "runtime_prerequisite": prerequisite,
            "frame_advance_source_contract": source_contract,
            "source_bindings": b03._source_bindings(root),
            "canonical_acceptance_eligible": False,
        }
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        with exclusive_file_lock(
            lock_path, lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            session = _run_frame_advance_diagnostic_session(
                steps=steps,
                rom=binding_paths["rom"],
                symbol_path=binding_paths["symbol_map"],
                emulator_directory=binding_paths["emulator"].parent,
                default_config=binding_paths["default_config"],
                symbols=symbols,
                timeout_seconds=timeout_seconds,
                transcript=transcript,
            )
        report = {
            "schema": DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_pass",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "requested_steps": steps,
            "session": session,
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "Diagnostic-only handshake/cadence evidence cannot satisfy "
                "the canonical two-session Milestone 5 budget gate."
            ],
        }
    except Exception as error:
        report = {
            "schema": DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_fail",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "requested_steps": steps,
            "error": f"{type(error).__name__}: {error}",
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "A failed diagnostic proves no canonical budget claim."
            ],
        }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "\n".join(transcript) + ("\n" if transcript else ""),
        encoding="utf-8",
        newline="\n",
    )
    write_json(report_path, report)
    print(
        f"M5 B03 budget diagnostic: {report['status']} "
        f"({steps} steps; {report_path})"
    )
    return 0 if report["status"] == "diagnostic_pass" else 1


def _run_overrun_origin_diagnostic_cli(
    *,
    root: Path,
    report_path: Path | None,
    trace_path: Path | None,
    timeout_seconds: float,
    lock_timeout_seconds: float,
) -> int:
    path_errors = diagnostic_output_path_errors(
        root=root, report=report_path, trace=trace_path
    )
    if path_errors:
        for error in path_errors:
            print(error)
        return 2
    if not 5.0 <= timeout_seconds <= 600.0:
        print("--timeout-seconds must be within 5..600")
        return 2
    if not 5.0 <= lock_timeout_seconds <= 600.0:
        print("--lock-timeout-seconds must be within 5..600")
        return 2
    assert report_path is not None and trace_path is not None
    report_path = report_path.resolve()
    trace_path = trace_path.resolve()
    transcript: list[str] = []
    binding_paths = _hash_binding_paths(root)
    provenance: dict[str, Any] = {}
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live overrun-origin diagnostic requires native Windows "
                "BlastEm"
            )
        schedule_contract()
        hashes = _capture_hashes(binding_paths)
        for name, expected in (
            ("rom", PINNED_ROM_SHA256),
            ("symbol_map", PINNED_SYMBOL_SHA256),
            ("emulator", PINNED_EMULATOR_SHA256),
            ("default_config", PINNED_DEFAULT_CONFIG_SHA256),
        ):
            if hashes[name] != expected:
                raise EvidenceFailure(
                    f"overrun-origin pinned {name} SHA-256 mismatch"
                )
        _runtime_payload, prerequisite = load_runtime_prerequisite(
            report_path=binding_paths["runtime_report"],
            trace_path=binding_paths["runtime_trace"],
            replay_path=binding_paths["runtime_replay"],
        )
        del _runtime_payload
        source_contract = frame_advance_source_contract(
            binding_paths["default_config"]
        )
        if source_contract["status"] != "pass":
            raise EvidenceFailure(
                "overrun-origin frame source contract failed"
            )
        site_contract = overrun_origin_site_contract(
            binding_paths["rom"]
        )
        if site_contract["status"] != "pass":
            raise EvidenceFailure(
                f"overrun-origin site contract failed: {site_contract}"
            )
        symbols = parse_vasm_symbols(binding_paths["symbol_map"])
        if [name for name in REQUIRED_SYMBOLS if name not in symbols]:
            raise EvidenceFailure(
                "overrun-origin symbol map is incomplete"
            )
        layout_errors = symbol_layout_errors(symbols)
        if layout_errors:
            raise EvidenceFailure(
                f"overrun-origin symbol layout failed: {layout_errors[:3]}"
            )
        verify_no_user_blastem_override()
        provenance = {
            "rom_sha256": hashes["rom"],
            "symbol_map_sha256": hashes["symbol_map"],
            "emulator_sha256": hashes["emulator"],
            "default_config_sha256": hashes["default_config"],
            "runtime_prerequisite": prerequisite,
            "frame_advance_source_contract": source_contract,
            "overrun_origin_site_contract": site_contract,
            "source_bindings": b03._source_bindings(root),
            "canonical_acceptance_eligible": False,
        }
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        with exclusive_file_lock(
            lock_path, lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            session = _run_overrun_origin_diagnostic_session(
                rom=binding_paths["rom"],
                symbol_path=binding_paths["symbol_map"],
                emulator_directory=binding_paths["emulator"].parent,
                default_config=binding_paths["default_config"],
                symbols=symbols,
                timeout_seconds=timeout_seconds,
                transcript=transcript,
            )
        report = {
            "schema": OVERRUN_ORIGIN_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_ORIGIN_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_pass",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "fixed_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
            "session": session,
            "result": {
                "first_increment_origin": session["origin_hit"]["site"],
                "pc": session["origin_hit"]["pc"],
                "meaning": session["origin_hit"]["meaning"],
            },
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "This fixed three-step discriminator is noncanonical and "
                "cannot satisfy the two-session Milestone 5 budget gate.",
                "Software breakpoints are armed only after the clean total-2 "
                "POST and removed at the first total-3 fault-path hit.",
            ],
        }
    except Exception as error:
        report = {
            "schema": OVERRUN_ORIGIN_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_ORIGIN_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_fail",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "fixed_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
            "error": f"{type(error).__name__}: {error}",
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "A failed overrun-origin diagnostic proves no canonical "
                "budget claim."
            ],
        }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "\n".join(transcript) + ("\n" if transcript else ""),
        encoding="utf-8",
        newline="\n",
    )
    write_json(report_path, report)
    print(
        "M5 B03 budget overrun-origin diagnostic: "
        f"{report['status']} ({report_path})"
    )
    return 0 if report["status"] == "diagnostic_pass" else 1


def _diagnostic_row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "authoritative_total": row["authoritative_total"],
        "engine_state": row["observed"]["engine"]["state"],
        "engine_attempt": row["observed"]["engine"]["attempt"],
        "engine_tick": row["observed"]["engine"]["tick"],
        "engine_input_held": row["observed"]["engine"]["input_held"],
        "input": dict(row["observed"]["input"]),
        "debug_sequence": row["observed"]["debug"]["sequence"],
        "debug_frame_counter": row["observed"]["debug"][
            "frame_counter"
        ],
        "debug_heartbeat": row["observed"]["debug"]["heartbeat"],
        "debug_frame_token": row["observed"]["debug"]["frame_token"],
        "last_vdp_status": row["observed"]["debug"][
            "last_vdp_status"
        ],
        "vblank_overrun_count": row["observed"]["debug"][
            "vblank_overrun_count"
        ],
        "dma_words_frame": row["observed"]["debug"][
            "dma_words_frame"
        ],
        "dma_words_max": row["observed"]["debug"]["dma_words_max"],
        "dma_queue_depth": row["observed"]["debug"][
            "dma_queue_depth"
        ],
        "blocks_sha256": {
            name: block["sha256"] for name, block in row["blocks"].items()
        },
    }


def _load_f8_origin_comparison(
    *,
    root: Path,
    report_path: Path,
    trace_path: Path,
) -> dict[str, Any]:
    path_errors = diagnostic_output_path_errors(
        root=root, report=report_path, trace=trace_path
    )
    if path_errors:
        raise EvidenceFailure(
            f"F8 comparison paths failed: {path_errors}"
        )
    try:
        report_raw = report_path.read_bytes()
        trace_raw = trace_path.read_bytes()
        report = json.loads(report_raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceFailure(
            f"could not load F8 origin comparison: {error}"
        ) from error
    session = report.get("session", {})
    rows = session.get("cadence_rows", [])
    provenance = report.get("provenance", {})
    source_contract = provenance.get("frame_advance_source_contract", {})
    runtime_prerequisite = provenance.get("runtime_prerequisite", {})
    current_site_contract = overrun_origin_site_contract(
        root / ROM_RELATIVE_PATH
    )
    current_source_bindings = b03._source_bindings(root)
    try:
        trace_lines = trace_raw.decode("utf-8").splitlines()
        recomputed_audit = audit_overrun_origin_transcript(
            trace_lines,
            session_id=session["session_id"],
            vblank=parse_vasm_symbols(
                root / SYMBOL_MAP_RELATIVE_PATH
            )["VBlankInterrupt"],
            post_step=parse_vasm_symbols(
                root / SYMBOL_MAP_RELATIVE_PATH
            )["MainLoopPostGameplayStep"],
            initial_scenario=session["initial_scenario"],
            cadence_rows=rows,
            origin_hit=session["origin_hit"],
        )
    except (KeyError, UnicodeDecodeError, ValueError) as error:
        raise EvidenceFailure(
            f"could not recompute F8 comparison transcript: {error}"
        ) from error
    if (
        report.get("schema") != OVERRUN_ORIGIN_DIAGNOSTIC_SCHEMA
        or report.get("evidence_type")
        != OVERRUN_ORIGIN_DIAGNOSTIC_EVIDENCE_TYPE
        or report.get("status") != "diagnostic_pass"
        or report.get("acceptance_status") != "incomplete"
        or report.get("canonical_acceptance_eligible") is not False
        or report.get("fixed_steps")
        != OVERRUN_ORIGIN_DIAGNOSTIC_STEPS
        or report.get("debugger_write_packets") != []
        or recomputed_audit.get("status") != "pass"
        or session.get("transcript_audit") != recomputed_audit
        or session.get("transcript_sha256") != _sha256_bytes(trace_raw)
        or not isinstance(rows, list)
        or len(rows) != OVERRUN_ORIGIN_DIAGNOSTIC_STEPS
        or [row.get("authoritative_total") for row in rows] != [1, 2, 3]
        or session.get("origin_hit", {}).get("site") != "vblank_exit"
        or session.get("origin_hit", {}).get("pc") != "0000170E"
        or session.get("origin_hit", {}).get("register_low_words")
        != {"d0": 0, "d1": 8}
        or session.get("origin_hit", {})
        .get("counter_before_increment", {})
        .get("raw_hex")
        != "0000"
        or session.get("counter_isolation", {}).get("status") != "pass"
        or provenance.get("rom_sha256") != PINNED_ROM_SHA256
        or provenance.get("symbol_map_sha256") != PINNED_SYMBOL_SHA256
        or provenance.get("emulator_sha256") != PINNED_EMULATOR_SHA256
        or provenance.get("default_config_sha256")
        != PINNED_DEFAULT_CONFIG_SHA256
        or provenance.get("overrun_origin_site_contract")
        != current_site_contract
        or provenance.get("source_bindings") != current_source_bindings
        or source_contract.get("status") != "pass"
        or source_contract.get("source_node") != BLASTEM_SOURCE_NODE
        or source_contract.get("default_config_sha256")
        != PINNED_DEFAULT_CONFIG_SHA256
        or source_contract.get("source_sha256") != BLASTEM_SOURCE_HASHES
        or runtime_prerequisite.get("status") != "pass"
        or runtime_prerequisite.get("report_sha256")
        != PINNED_RUNTIME_REPORT_SHA256
        or runtime_prerequisite.get("trace_sha256")
        != PINNED_RUNTIME_TRACE_SHA256
        or runtime_prerequisite.get("replay_sha256")
        != PINNED_RUNTIME_REPLAY_SHA256
        or [
            row.get("observed", {}).get("debug", {}).get(
                "vblank_overrun_count"
            )
            for row in rows
        ]
        != [0, 0, 1]
    ):
        raise EvidenceFailure(
            "F8 origin comparison does not satisfy its fixed pass contract"
        )
    if any(
        cadence_row_errors(row)
        for row in rows[:OVERRUN_ORIGIN_ARM_AFTER_TOTAL]
    ):
        raise EvidenceFailure("F8 comparison clean prefix failed reparse")
    return {
        "report_path": str(report_path),
        "report_sha256": _sha256_bytes(report_raw),
        "trace_path": str(trace_path),
        "trace_sha256": _sha256_bytes(trace_raw),
        "origin_site": "vblank_exit",
        "origin_pc": "0000170E",
        "origin_register_low_words": {"d0": 0, "d1": 8},
        "counter_sequence": [0, 0, 1],
        "rows": [_diagnostic_row_summary(row) for row in rows],
    }


def _run_natural_overrun_control_cli(
    *,
    root: Path,
    report_path: Path | None,
    trace_path: Path | None,
    comparison_report_path: Path | None,
    comparison_trace_path: Path | None,
    timeout_seconds: float,
    lock_timeout_seconds: float,
) -> int:
    path_errors = diagnostic_output_path_errors(
        root=root, report=report_path, trace=trace_path
    )
    if comparison_report_path is None or comparison_trace_path is None:
        path_errors.append(
            "natural control requires --comparison-report and "
            "--comparison-trace"
        )
    else:
        path_errors.extend(
            diagnostic_output_path_errors(
                root=root,
                report=comparison_report_path,
                trace=comparison_trace_path,
            )
        )
    if path_errors:
        for error in path_errors:
            print(error)
        return 2
    if not 5.0 <= timeout_seconds <= 600.0:
        print("--timeout-seconds must be within 5..600")
        return 2
    if not 5.0 <= lock_timeout_seconds <= 600.0:
        print("--lock-timeout-seconds must be within 5..600")
        return 2
    assert report_path is not None and trace_path is not None
    assert comparison_report_path is not None
    assert comparison_trace_path is not None
    report_path = report_path.resolve()
    trace_path = trace_path.resolve()
    comparison_report_path = comparison_report_path.resolve()
    comparison_trace_path = comparison_trace_path.resolve()
    transcript: list[str] = []
    binding_paths = _hash_binding_paths(root)
    provenance: dict[str, Any] = {}
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live natural control requires native Windows BlastEm"
            )
        schedule_contract()
        hashes = _capture_hashes(binding_paths)
        for name, expected in (
            ("rom", PINNED_ROM_SHA256),
            ("symbol_map", PINNED_SYMBOL_SHA256),
            ("emulator", PINNED_EMULATOR_SHA256),
            ("default_config", PINNED_DEFAULT_CONFIG_SHA256),
        ):
            if hashes[name] != expected:
                raise EvidenceFailure(
                    f"natural control pinned {name} SHA-256 mismatch"
                )
        _runtime_payload, prerequisite = load_runtime_prerequisite(
            report_path=binding_paths["runtime_report"],
            trace_path=binding_paths["runtime_trace"],
            replay_path=binding_paths["runtime_replay"],
        )
        del _runtime_payload
        source_contract = frame_advance_source_contract(
            binding_paths["default_config"]
        )
        if source_contract["status"] != "pass":
            raise EvidenceFailure(
                "natural control frame source contract failed"
            )
        if (
            source_contract["source_node"]
            != FRAME_ADVANCE_VBLANK_BOUNDARY_FACT["source_node"]
            or source_contract["source_sha256"]["vdp.c"]
            != FRAME_ADVANCE_VBLANK_BOUNDARY_FACT["vdp.c_sha256"]
            or source_contract["source_sha256"]["genesis.c"]
            != FRAME_ADVANCE_VBLANK_BOUNDARY_FACT["genesis.c_sha256"]
        ):
            raise EvidenceFailure(
                "natural control source timing fact is not hash-bound"
            )
        site_contract = overrun_origin_site_contract(
            binding_paths["rom"]
        )
        if site_contract["status"] != "pass":
            raise EvidenceFailure(
                f"natural control site contract failed: {site_contract}"
            )
        f8_comparison = _load_f8_origin_comparison(
            root=root,
            report_path=comparison_report_path,
            trace_path=comparison_trace_path,
        )
        symbols = parse_vasm_symbols(binding_paths["symbol_map"])
        if [name for name in REQUIRED_SYMBOLS if name not in symbols]:
            raise EvidenceFailure("natural control symbols are incomplete")
        layout_errors = symbol_layout_errors(symbols)
        if layout_errors:
            raise EvidenceFailure(
                f"natural control symbol layout failed: {layout_errors[:3]}"
            )
        verify_no_user_blastem_override()
        provenance = {
            "rom_sha256": hashes["rom"],
            "symbol_map_sha256": hashes["symbol_map"],
            "emulator_sha256": hashes["emulator"],
            "default_config_sha256": hashes["default_config"],
            "runtime_prerequisite": prerequisite,
            "frame_advance_source_contract": source_contract,
            "frame_advance_vblank_boundary_fact": dict(
                FRAME_ADVANCE_VBLANK_BOUNDARY_FACT
            ),
            "overrun_origin_site_contract": site_contract,
            "source_bindings": b03._source_bindings(root),
            "canonical_acceptance_eligible": False,
        }
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        with exclusive_file_lock(
            lock_path, lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            session = _run_natural_overrun_control_session(
                rom=binding_paths["rom"],
                symbol_path=binding_paths["symbol_map"],
                emulator_directory=binding_paths["emulator"].parent,
                default_config=binding_paths["default_config"],
                symbols=symbols,
                timeout_seconds=timeout_seconds,
                transcript=transcript,
            )
        natural_rows = session["cadence_rows"]
        natural_counter_sequence = [
            row["observed"]["debug"]["vblank_overrun_count"]
            for row in natural_rows
        ]
        natural_clean = (
            session["origin_hit"] is None
            and session["counter_at_total_3_pre"]["raw_hex"] == "0000"
            and natural_counter_sequence == [0, 0, 0]
            and not any(cadence_row_errors(row) for row in natural_rows)
        )
        frame_advance_induced = (
            natural_clean
            and f8_comparison["origin_site"] == "vblank_exit"
            and f8_comparison["counter_sequence"] == [0, 0, 1]
        )
        natural_shared_fault = (
            session["origin_hit"] is not None
            and session["origin_hit"]["site"] == "vblank_exit"
            and session["origin_hit"]["pc"] == "0000170E"
            and session["origin_hit"]["d0"] & 0xFFFF == 0
            and session["origin_hit"]["d1"] & 0xFFFF == 8
            and session["origin_hit"]["counter_before_increment"][
                "raw_hex"
            ]
            == "0000"
            and session["counter_at_total_3_pre"]["raw_hex"] == "0001"
            and natural_counter_sequence == [0, 0, 1]
        )
        if not frame_advance_induced and not natural_shared_fault:
            raise EvidenceFailure(
                "natural/F8 A/B result was neither a clean natural control "
                "nor the same isolated VBlank-exit fault"
            )
        conclusion = (
            "The pinned ui.advance protocol alone reaches the "
            "VBlankInterrupt exit-loss path at total 3; the pinned natural "
            "PRE/POST control remains at zero."
            if frame_advance_induced
            else (
                "Both the pinned ui.advance drive and the established "
                "natural PRE/POST drive reach the same VBlankInterrupt "
                "exit-loss path at total 3. ui.advance is therefore not "
                "the cause of this counter increment."
            )
        )
        report = {
            "schema": OVERRUN_NATURAL_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_NATURAL_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_pass",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "fixed_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
            "session": session,
            "comparison": {
                "f8_ui_advance": f8_comparison,
                "natural_pre_post": {
                    "origin_site": (
                        session["origin_hit"]["site"]
                        if session["origin_hit"]
                        else None
                    ),
                    "counter_sequence": natural_counter_sequence,
                    "rows": [
                        _diagnostic_row_summary(row)
                        for row in natural_rows
                    ],
                },
                "exact_first_three_inputs": [
                    expected_input_for_total(total)
                    for total in (1, 2, 3)
                ],
                "frame_advance_induced_vblank_exit_measurement": (
                    frame_advance_induced
                ),
                "shared_vblank_exit_fault": natural_shared_fault,
                "conclusion": conclusion,
            },
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "This three-step A/B root-cause diagnostic is noncanonical "
                "and cannot satisfy the full two-session budget gate.",
                "The control reproduces the established B03 runtime "
                "collector's natural PRE/POST drive only through total 3."
            ],
        }
    except Exception as error:
        report = {
            "schema": OVERRUN_NATURAL_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_NATURAL_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_fail",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "fixed_steps": OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
            "error": f"{type(error).__name__}: {error}",
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "A failed natural control proves no canonical budget claim."
            ],
        }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "\n".join(transcript) + ("\n" if transcript else ""),
        encoding="utf-8",
        newline="\n",
    )
    write_json(report_path, report)
    print(
        "M5 B03 budget natural-control diagnostic: "
        f"{report['status']} ({report_path})"
    )
    return 0 if report["status"] == "diagnostic_pass" else 1


def _run_continuous_overrun_control_cli(
    *,
    root: Path,
    report_path: Path | None,
    trace_path: Path | None,
    timeout_seconds: float,
    lock_timeout_seconds: float,
) -> int:
    path_errors = diagnostic_output_path_errors(
        root=root, report=report_path, trace=trace_path
    )
    if path_errors:
        for error in path_errors:
            print(error)
        return 2
    if not 5.0 <= timeout_seconds <= 600.0:
        print("--timeout-seconds must be within 5..600")
        return 2
    if not 5.0 <= lock_timeout_seconds <= 600.0:
        print("--lock-timeout-seconds must be within 5..600")
        return 2
    assert report_path is not None and trace_path is not None
    report_path = report_path.resolve()
    trace_path = trace_path.resolve()
    transcript: list[str] = []
    binding_paths = _hash_binding_paths(root)
    provenance: dict[str, Any] = {}
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live continuous control requires native Windows BlastEm"
            )
        schedule_contract()
        hashes = _capture_hashes(binding_paths)
        for name, expected in (
            ("rom", PINNED_ROM_SHA256),
            ("symbol_map", PINNED_SYMBOL_SHA256),
            ("emulator", PINNED_EMULATOR_SHA256),
            ("default_config", PINNED_DEFAULT_CONFIG_SHA256),
        ):
            if hashes[name] != expected:
                raise EvidenceFailure(
                    f"continuous control pinned {name} SHA-256 mismatch"
                )
        _runtime_payload, prerequisite = load_runtime_prerequisite(
            report_path=binding_paths["runtime_report"],
            trace_path=binding_paths["runtime_trace"],
            replay_path=binding_paths["runtime_replay"],
        )
        del _runtime_payload
        source_contract = frame_advance_source_contract(
            binding_paths["default_config"]
        )
        if source_contract["status"] != "pass":
            raise EvidenceFailure(
                "continuous control source contract failed"
            )
        site_contract = overrun_origin_site_contract(
            binding_paths["rom"]
        )
        if site_contract["status"] != "pass":
            raise EvidenceFailure(
                f"continuous control site contract failed: {site_contract}"
            )
        symbols = parse_vasm_symbols(binding_paths["symbol_map"])
        if [name for name in REQUIRED_SYMBOLS if name not in symbols]:
            raise EvidenceFailure("continuous control symbols are incomplete")
        layout_errors = symbol_layout_errors(symbols)
        if layout_errors:
            raise EvidenceFailure(
                f"continuous control symbol layout failed: {layout_errors[:3]}"
            )
        verify_no_user_blastem_override()
        provenance = {
            "rom_sha256": hashes["rom"],
            "symbol_map_sha256": hashes["symbol_map"],
            "emulator_sha256": hashes["emulator"],
            "default_config_sha256": hashes["default_config"],
            "runtime_prerequisite": prerequisite,
            "frame_advance_source_contract": source_contract,
            "frame_advance_vblank_boundary_fact": dict(
                FRAME_ADVANCE_VBLANK_BOUNDARY_FACT
            ),
            "overrun_origin_site_contract": site_contract,
            "source_bindings": b03._source_bindings(root),
            "canonical_acceptance_eligible": False,
        }
        lock_path = root / ".toolcache/locks/blastem-gdb.lock"
        with exclusive_file_lock(
            lock_path, lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            session = _run_continuous_overrun_control_session(
                rom=binding_paths["rom"],
                symbol_path=binding_paths["symbol_map"],
                emulator_directory=binding_paths["emulator"].parent,
                default_config=binding_paths["default_config"],
                symbols=symbols,
                timeout_seconds=timeout_seconds,
                transcript=transcript,
            )
        if session["origin_hit"] is None:
            conclusion = (
                "No overrun-origin path was reached during the bounded "
                "near-continuous interval for the pinned build. This "
                "clear interval does not attribute a fault from a different "
                "ROM build to debugger drive."
            )
            result_kind = "continuous_clear"
        else:
            conclusion = (
                "The overrun-origin path was reached during near-continuous "
                "execution with no F8 and no per-frame PRE/POST breakpoint; "
                "debugger frame stepping is not the cause."
            )
            result_kind = "continuous_origin_reproduced"
        report = {
            "schema": OVERRUN_CONTINUOUS_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_CONTINUOUS_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_pass",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "session": session,
            "result": {
                "kind": result_kind,
                "origin_site": (
                    session["origin_hit"]["site"]
                    if session["origin_hit"]
                    else None
                ),
                "conclusion": conclusion,
            },
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "This bounded root-cause diagnostic is noncanonical and "
                "cannot satisfy the full two-session budget gate.",
                "Only the two exact fault-path software breakpoints remain "
                "armed after the one-time stage-ack breakpoint."
            ],
        }
    except Exception as error:
        report = {
            "schema": OVERRUN_CONTINUOUS_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_CONTINUOUS_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_fail",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
            "error": f"{type(error).__name__}: {error}",
            "provenance": provenance,
            "debugger_write_packets": debugger_write_packets(transcript),
            "limitations": [
                "A failed continuous control proves no canonical budget claim."
            ],
        }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "\n".join(transcript) + ("\n" if transcript else ""),
        encoding="utf-8",
        newline="\n",
    )
    write_json(report_path, report)
    print(
        "M5 B03 budget continuous-control diagnostic: "
        f"{report['status']} ({report_path})"
    )
    return 0 if report["status"] == "diagnostic_pass" else 1


def _failure_report(
    *,
    error: Exception,
    provenance: dict[str, Any],
    transcript: list[str],
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5/B03",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "acceptance_status": "incomplete",
        "rom_sha256": provenance.get("rom", {}).get("sha256", ""),
        "symbol_map_sha256": provenance.get("symbol_map", {}).get(
            "sha256", ""
        ),
        "checks": [_check("m5_b03_budget_capture", False, detail)],
        "error": detail,
        "provenance": provenance,
        "debugger_write_packets": debugger_write_packets(transcript),
        "limitations": [
            "A failed capture proves no Milestone 5 hardware-budget claim."
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument(
        "--validate-existing",
        action="store_true",
        help="validate an existing report/trace without launching BlastEm",
    )
    parser.add_argument(
        "--diagnostic-steps",
        type=int,
        help=(
            "run one noncanonical 1..725-step handshake diagnostic; "
            "requires explicit --report/--trace paths under "
            f"{DIAGNOSTIC_DIRECTORY_RELATIVE_PATH}"
        ),
    )
    parser.add_argument(
        "--diagnose-overrun-origin",
        action="store_true",
        help=(
            "run the fixed noncanonical three-step read-only breakpoint "
            "discriminator for the B03 VBlank overrun counter; requires "
            "explicit --report/--trace paths under "
            f"{DIAGNOSTIC_DIRECTORY_RELATIVE_PATH}"
        ),
    )
    parser.add_argument(
        "--diagnose-overrun-natural-control",
        action="store_true",
        help=(
            "run the fixed natural PRE/POST three-step control against a "
            "passing F8 overrun-origin diagnostic"
        ),
    )
    parser.add_argument(
        "--diagnose-overrun-continuous-control",
        action="store_true",
        help=(
            "run the near-continuous post-stage-ack discriminator with "
            "only the two exact fault-path breakpoints armed"
        ),
    )
    parser.add_argument(
        "--comparison-report",
        type=Path,
        help="passing F8 overrun-origin diagnostic report",
    )
    parser.add_argument(
        "--comparison-trace",
        type=Path,
        help="trace bound by the F8 overrun-origin diagnostic report",
    )
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument(
        "--lock-timeout-seconds", type=float, default=120.0
    )
    args = parser.parse_args()

    root = args.root.resolve()
    selected_modes = sum(
        (
            bool(args.validate_existing),
            args.diagnostic_steps is not None,
            bool(args.diagnose_overrun_origin),
            bool(args.diagnose_overrun_natural_control),
            bool(args.diagnose_overrun_continuous_control),
        )
    )
    if selected_modes > 1:
        parser.error(
            "--validate-existing, --diagnostic-steps, and "
            "the two --diagnose-overrun-* modes are mutually exclusive"
        )
    if args.diagnose_overrun_natural_control:
        return _run_natural_overrun_control_cli(
            root=root,
            report_path=args.report,
            trace_path=args.trace,
            comparison_report_path=args.comparison_report,
            comparison_trace_path=args.comparison_trace,
            timeout_seconds=args.timeout_seconds,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    if args.diagnose_overrun_continuous_control:
        return _run_continuous_overrun_control_cli(
            root=root,
            report_path=args.report,
            trace_path=args.trace,
            timeout_seconds=args.timeout_seconds,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    if args.diagnose_overrun_origin:
        return _run_overrun_origin_diagnostic_cli(
            root=root,
            report_path=args.report,
            trace_path=args.trace,
            timeout_seconds=args.timeout_seconds,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    if args.diagnostic_steps is not None:
        return _run_diagnostic_cli(
            root=root,
            steps=args.diagnostic_steps,
            report_path=args.report,
            trace_path=args.trace,
            timeout_seconds=args.timeout_seconds,
            lock_timeout_seconds=args.lock_timeout_seconds,
        )
    report_path = (
        args.report.resolve()
        if args.report
        else root / REPORT_RELATIVE_PATH
    )
    trace_path = (
        args.trace.resolve()
        if args.trace
        else root / TRACE_RELATIVE_PATH
    )
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    binding_paths = _hash_binding_paths(root)
    transcript: list[str] = []
    sessions: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    report: dict[str, Any]

    if args.validate_existing:
        try:
            existing = json.loads(
                report_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            print(f"M5 B03 budget existing report: fail ({error})")
            return 1
        errors = existing_report_errors(existing, root=root)
        if errors:
            print("M5 B03 budget existing report: fail")
            for error in errors[:20]:
                print(error)
            return 1
        print("M5 B03 budget existing report: pass")
        return 0

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M5 B03 budget evidence requires native Windows BlastEm"
            )
        if not 5.0 <= args.timeout_seconds <= 600.0:
            raise ValueError("--timeout-seconds must be within 5..600")
        if not 5.0 <= args.lock_timeout_seconds <= 600.0:
            raise ValueError(
                "--lock-timeout-seconds must be within 5..600"
            )
        frozen_schedule = schedule_contract()
        hashes_before = _capture_hashes(binding_paths)
        if hashes_before["rom"] != PINNED_ROM_SHA256:
            raise EvidenceFailure("B03 ROM does not match pinned candidate")
        if hashes_before["symbol_map"] != PINNED_SYMBOL_SHA256:
            raise EvidenceFailure(
                "B03 symbol map does not match pinned candidate"
            )
        if hashes_before["emulator"] != PINNED_EMULATOR_SHA256:
            raise EvidenceFailure(
                "BlastEm executable does not match pinned revision"
            )
        if (
            hashes_before["default_config"]
            != PINNED_DEFAULT_CONFIG_SHA256
        ):
            raise EvidenceFailure(
                "BlastEm default.cfg does not match pinned revision"
            )

        runtime_payload, prerequisite = load_runtime_prerequisite(
            report_path=binding_paths["runtime_report"],
            trace_path=binding_paths["runtime_trace"],
            replay_path=binding_paths["runtime_replay"],
        )
        del runtime_payload
        default_config = binding_paths["default_config"]
        controller_bindings = verify_controller_bindings(default_config)
        if controller_bindings.get("status") != "pass":
            raise EvidenceFailure(
                "pinned default.cfg lacks standard three-button bindings"
            )
        frame_source_contract = frame_advance_source_contract(
            default_config
        )
        if frame_source_contract.get("status") != "pass":
            raise EvidenceFailure(
                "pinned BlastEm F7/F8 source/config contract failed"
            )
        symbols = parse_vasm_symbols(binding_paths["symbol_map"])
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"B03 symbol map is missing required symbols {missing}"
            )
        layout_errors = symbol_layout_errors(symbols)
        if layout_errors:
            raise EvidenceFailure(
                "budget telemetry symbol layout drifted: "
                + "; ".join(layout_errors)
            )
        if symbols["DEBUG_START_ENCOUNTER"] != b03.B03_DIRECTORY_INDEX:
            raise EvidenceFailure(
                "B03 fixture does not define DEBUG_START_ENCOUNTER=2"
            )
        pre_step_binding = b03.verify_pre_step_rom_binding(
            binding_paths["rom"], symbols
        )
        user_config = verify_no_user_blastem_override()
        provenance = _provenance_bindings(
            root=root,
            paths=binding_paths,
            hashes=hashes_before,
        )
        provenance.update(
            {
                "fixture": {
                    "directory_index": b03.B03_DIRECTORY_INDEX,
                    "stage_id": b03.B03_STAGE_ID,
                    "debug_start_encounter": symbols[
                        "DEBUG_START_ENCOUNTER"
                    ],
                },
                "required_symbols": {
                    name: f"{symbols[name]:08X}"
                    for name in REQUIRED_SYMBOLS
                },
                "default_config_contract": controller_bindings,
                "frame_advance_source_contract": (
                    frame_source_contract
                ),
                "user_config_preflight": user_config,
                "pre_step_rom_binding": pre_step_binding,
                "source_bindings": b03._source_bindings(root),
                "serialized_lock": _relative_or_absolute(lock_path, root),
                "launch_flags": list(b03.LAUNCH_FLAGS),
            }
        )

        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            for session_id in EXPECTED_SESSION_IDS:
                sessions.append(
                    _run_frame_advance_budget_session(
                        session_id=session_id,
                        rom=binding_paths["rom"],
                        symbol_path=binding_paths["symbol_map"],
                        emulator_directory=(
                            binding_paths["emulator"].parent
                        ),
                        default_config=default_config,
                        symbols=symbols,
                        timeout_seconds=args.timeout_seconds,
                        transcript=transcript,
                    )
                )

        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_payload = (
            "\n".join(transcript)
            + ("\n" if transcript else "")
        ).encode("utf-8")
        trace_path.write_bytes(trace_payload)
        provenance["transcript"] = {
            "path": _relative_or_absolute(trace_path, root),
            "sha256": _sha256_bytes(trace_payload),
            "commands": sum(
                line.startswith("> ") for line in transcript
            ),
        }

        hashes_after = _capture_hashes(binding_paths)
        drift = [
            name
            for name in HASH_BINDING_NAMES
            if hashes_before[name] != hashes_after[name]
        ]
        provenance["hash_verification"] = {
            "status": "pass" if not drift else "fail",
            "drift": drift,
            "before_after": {
                name: {
                    "before": hashes_before[name],
                    "after": hashes_after[name],
                }
                for name in HASH_BINDING_NAMES
            },
        }
        if drift:
            raise EvidenceFailure(
                f"hash-bound inputs changed during capture: {drift}"
            )
        if b03._source_bindings(root) != provenance["source_bindings"]:
            raise EvidenceFailure(
                "B03 runtime/source binding changed during capture"
            )
        if (
            frame_advance_source_contract(default_config)
            != provenance["frame_advance_source_contract"]
        ):
            raise EvidenceFailure(
                "BlastEm frame-advance source/config contract changed"
            )

        report = build_budget_report(
            sessions=sessions,
            provenance=provenance,
            prerequisite=prerequisite,
            schedule=frozen_schedule,
        )
    except Exception as error:
        try:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(
                "\n".join(transcript)
                + ("\n" if transcript else ""),
                encoding="utf-8",
                newline="\n",
            )
        except OSError:
            pass
        report = _failure_report(
            error=error,
            provenance=provenance,
            transcript=transcript,
        )

    write_json(report_path, report)
    print(
        f"M5 B03 budget evidence: {report['status']} "
        f"(acceptance {report['acceptance_status']}; "
        f"{report_path.as_posix()})"
    )
    if report_exit_code(report):
        print(
            report.get(
                "error",
                "budget evidence checks or public contract failed",
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
