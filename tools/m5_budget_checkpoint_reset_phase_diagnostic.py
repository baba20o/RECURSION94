#!/usr/bin/env python3
"""Read-only native-cycle diagnosis of the B03 timeout/reset checkpoint hold.

This helper is deliberately noncanonical diagnostic evidence.  It drives the
exact canonical attempt-0 input prefix with three host-only key transitions,
then observes authoritative totals 719 through 721 and the recovery VBlank.
The native debugger sets bounded breakpoints and prints state; it never writes
guest memory, registers, or PC.
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

from tools import m5_b03_runtime_evidence as b03  # noqa: E402
from tools import m5_budget_checkpoint_phase_diagnostic as regular  # noqa: E402
from tools import m5_budget_phase_diagnostic as native  # noqa: E402
from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    parse_vasm_symbols,
    sha256_file,
    verify_no_user_blastem_override,
    write_standard_pad_profile,
)


REPORT_SCHEMA = "r94-m5-b03-budget-checkpoint-reset-phase-diagnostic-v2"
EVIDENCE_TYPE = (
    "blastem_native_debugger_b03_timeout_reset_checkpoint_phase"
)
READY_MARKER = "R94R READY"
COMPLETE_MARKER = "R94R COMPLETE"
INPUT_MARKERS = (
    ("R94R INPUT 002E 08", b03.INPUT_RIGHT),
    ("R94R INPUT 0043 00", 0),
    ("R94R INPUT RESET 02", b03.INPUT_DOWN),
)
COLLECTOR_RELATIVE_PATH = (
    "tools/m5_budget_checkpoint_reset_phase_diagnostic.py"
)
REGULAR_HELPER_RELATIVE_PATH = (
    "tools/m5_budget_checkpoint_phase_diagnostic.py"
)
ROM_RELATIVE_PATH = regular.ROM_RELATIVE_PATH
SYMBOL_RELATIVE_PATH = regular.SYMBOL_RELATIVE_PATH
DEFAULT_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "checkpoint-reset-phase-final-visual-total719-total721-20260830.json"
)
DEFAULT_TRACE_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "checkpoint-reset-phase-final-visual-total719-total721-20260830.log"
)
CAPTURE_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "checkpoint-reset-phase-post-scheduler-repair-total719-total721-20260830.json"
)
CAPTURE_TRACE_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "checkpoint-reset-phase-post-scheduler-repair-total719-total721-20260830.log"
)
CAPTURE_REPORT_SHA256 = (
    "e9910c10407b6895090152ce613c5a1260fa6c6cb27d2b87e9aea7d8209b9709"
)
CAPTURE_TRACE_SHA256 = (
    "a675edbe8a17993091b67673801647bcb46476a711d11d4b3976110fa83c1fc8"
)
CAPTURE_COLLECTOR_SHA256 = (
    "3f768f4ef19a503d8aa09b663ef6d1bbc7b769f72259f578c83b18c16082f6cc"
)
CAPTURE_COMMAND_SCRIPT_SHA256 = (
    "89e7fd17bf45d5bf5215733b3e4c27cc00c02ca81e1e38bb76917c988c8caaf6"
)
CAPTURE_CONSOLE_SHA256 = (
    "6ae62555011159ce15e3e2c0dfcdcab774cab7719406016efbbc73348d608ade"
)
CAPTURE_RECORDS_SHA256 = (
    "d9ee055f64e307e0696cb17007b4da4cb5288916e44e2ca1c93cda0ac06fddaa"
)
CAPTURE_PC_CONTRACT_SHA256 = (
    "f2544aa345e7a5c011c21dceb5d45aaa7da7d22c1aad4119e5ab61ed9494bc02"
)
OFFLINE_REPORT_SCHEMA = (
    "r94-m5-b03-budget-checkpoint-reset-phase-offline-reanalysis-v1"
)
OFFLINE_EVIDENCE_TYPE = (
    "offline_reanalysis_of_native_b03_reset_checkpoint_capture"
)
DEFAULT_OFFLINE_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "checkpoint-reset-phase-post-scheduler-repair-offline-reanalysis-20260830.json"
)
TEST_RELATIVE_PATH = (
    "tests/emulator/test_m5_budget_checkpoint_reset_phase_diagnostic.py"
)
OFFLINE_CHECK_NAMES = frozenset(
    {
        "capture_schema_exact",
        "capture_stored_status_is_fixture_fail",
        "capture_rom_pin_exact",
        "capture_symbol_pin_exact",
        "capture_collector_hash_exact",
        "capture_trace_provenance_exact",
        "trace_reconstructs_exactly",
        "command_script_hash_exact",
        "command_script_frozen_exact",
        "command_policy_pass",
        "console_hash_exact",
        "console_contract_reanalysis_pass",
        "records_parse_exact",
        "records_hash_exact",
        "capture_pc_contract_exact",
        "original_analysis_error_exact",
        "original_console_error_exact",
        "corrected_analysis_pass",
        "corrected_analysis_has_no_errors",
        "source_debugger_writes_zero",
        "workspace_build_not_used_as_capture_input",
    }
)

CAPTURE_ROM_SHA256 = (
    "e46ba87201d4908876259b35079fe2074e8bdcb3b56cf58aa0e8768700922b00"
)
CAPTURE_SYMBOL_SHA256 = (
    "09fe544d009fbadcfdb076824d5a90eb4cf59ab7dc14a1a52c7ee5a93e9f03cc"
)
PINNED_ROM_SHA256 = (
    "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
)
PINNED_SYMBOL_SHA256 = (
    "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
)
PINNED_EMULATOR_SHA256 = regular.PINNED_EMULATOR_SHA256
PINNED_DEFAULT_CONFIG_SHA256 = regular.PINNED_DEFAULT_CONFIG_SHA256
BLASTEM_SOURCE_NODE = regular.BLASTEM_SOURCE_NODE
BLASTEM_VERSION = regular.BLASTEM_VERSION

BOOT_STAGE_RUNNING = regular.BOOT_STAGE_RUNNING
MASTER_CLOCKS_PER_M68K_CYCLE = regular.MASTER_CLOCKS_PER_M68K_CYCLE
NTSC_FRAME_MASTER_CLOCKS = regular.NTSC_FRAME_MASTER_CLOCKS
EXPECTED_ATTEMPT_0_REPLAY_CRC = b03.crc32_iso_hdlc(
    b03.ATTEMPT_0_INPUTS
)
EXPECTED_RESET_PROJECTION_CRC = 0x6A73D540
TARGET_TOTALS = (719, 720, 721)
TARGET_VBLANK_FRAMES = (719, 720, 721, 722, 723, 724)
FAIL_CLOSED_FRAME_LIMIT = 728
MAX_VBLANKS = FAIL_CLOSED_FRAME_LIMIT - TARGET_VBLANK_FRAMES[0] + 1
MAX_DMA_CALLS = MAX_VBLANKS * 6
EXTENDED_REPORT_SCHEMA = "r94-m5-b03-budget-reset-overlay-phase-diagnostic-v1"
EXTENDED_BASELINE_REPORT = "artifacts/diagnostics/m5-budget/reset-overlay-baseline-140039e3-total719-total724-20261010.json"
EXTENDED_BASELINE_TRACE = "artifacts/diagnostics/m5-budget/reset-overlay-baseline-140039e3-total719-total724-20261010.log"
EXTENDED_BASELINE_REPORT_SHA256 = "8d59136a6ec0c850ec116a62f69b83871c2ea27442e2a74dee50f62772a4a6a0"
EXTENDED_BASELINE_TRACE_SHA256 = "6dd5e82e5236d2f2b240eb72cf5f428888f7e21c0b5f7cbd9475a01f968495ff"
EXTENDED_TARGET_TOTALS = tuple(range(719, 725))
EXTENDED_TARGET_VBLANK_FRAMES = tuple(range(719, 727))
PREFIX_REPORT_SCHEMA = "r94-m5-b03-canonical-prefix-phase-diagnostic-v1"
PREFIX_CAPTURE_STEM = "artifacts/diagnostics/m5-budget/canonical-prefix-highwater-96dec577-attempt2-tick241-20261010"
PREFIX_CAPTURE_REPORT_SHA256 = "21d29dc3e129b01a0bf68e3c6d16a184014550aa343b884bfaecd1e029752cbf"
PREFIX_CAPTURE_TRACE_SHA256 = "b0b9adeb6c501a14658a9a21428d22d94885f2e6b867bb17c22b70687ca1f09f"
PREFIX_CAPTURE_COLLECTOR = "artifacts/diagnostics/m5-budget/canonical-prefix-highwater-96dec577-collector-1db162d8.py"
PREFIX_CAPTURE_COLLECTOR_SHA256 = "1db162d880e418614a7ce6f6c132f28ae9b3afb5fb330168399bd5fda58c04e2"
PREFIX_TOTAL_STEPS = 1681
PREFIX_FINAL_ATTEMPT = 2
PREFIX_FINAL_TICK = 241
PREFIX_PHYSICAL_FAIL_STOP = PREFIX_TOTAL_STEPS + 512
PREFIX_MAX_RECORDS = 24000
PREFIX_MAX_CONSOLE_BYTES = 4 * 1024 * 1024
PREFIX_CONSOLE_ROWS = 32760
PREFIX_SCHEDULE_SHA256 = "0215ce39982f901b2c29ea4a32405fa568aecb4227958f9a19d6e1093f8cabff"
PREFIX_SAMPLE_POST_TICKS = (*range(59, 62), *range(119, 122), *range(179, 182), *range(183, 197), *range(239, 242))
PREFIX_IRQ_TICK_RANGES = ((58, 62), (118, 122), (178, 197), (238, 242))
PREFIX_RUNTIME_PINS = {
    "report": ("artifacts/reports/m5-b03-runtime.json", "605f9118498908e1c263688abca72d833e993b27d22d00a08676577e7842798f"),
    "trace": ("artifacts/traces/m5-b03-runtime-gdb-transcript.log", "472e90d2508f0afa8c10797bf3fc4dd58304982e8e93245c13ee353ce6d6ceb9"),
    "replay": ("artifacts/replays/m5-b03-live-replay-ram.bin", "01ec5267ec497ceafd81879d449ec05ea44aef78112b5945f198e4a3bf5370e5"),
}
EXTENDED_FRAME_LIMIT = 730
EXTENDED_PC = {
    "main_begin": 0x000002D6,
    "main_done": 0x00000344,
    "renderer_mask": 0x00008C16,
    "renderer_unmasked": 0x00008DFA,
    "hud_begin": 0x00008CD2,
    "hud_end": 0x00008CD6,
    "sat_end": 0x00008CDA,
    "overlay_end": 0x00008CDE,
}
EXTENDED_WINDOWS = {
    "main_begin": "163900FF13F146FC2500",
    "main_done": "B63900FF13F166046000FF74",
    "renderer_mask": "46FC2700",
    "renderer_unmasked": "4CDF7CF84E75",
    "hud_begin": "6100EB56",
    "hud_end": "6100F330",
    "sat_end": "610004F0",
    "overlay_end": "61008322",
}
BUFFER_RANGES = ((0x00FF8000, 0x280), (0x00FF8600, 0x190))

PC = {
    "gameplay_call": 0x000002FA,
    "gameplay_return": 0x000002FE,
    "post_commit": 0x00000324,
    "reset_coalesce": 0x00000350,
    "reset_masked": 0x00000354,
    "reset_cleared": 0x0000035A,
    "wait_vblank": 0x0000035E,
    "timeout_projection_call": 0x00003D86,
    "reset_projection_call": 0x000039E2,
    "campaign_projection_end": 0x00006AA8,
    "engine_projection_start": 0x0000214A,
    "engine_projection_end": 0x00002362,
    "tail_start": 0x0000561E,
    "tail_end": 0x00005632,
    "raw_crc_start": 0x00006A62,
    "raw_crc_end": 0x00006A66,
    "renderer_start": 0x00008C1A,
    "renderer_end": 0x00008DF8,
    "dma_commit": 0x0000126A,
    "dma_issue_start": 0x00001440,
    "dma_issue_end": 0x000014E6,
    "vblank_entry": 0x0000169A,
    "vblank_ack": 0x000016BC,
    "vblank_post_dma": 0x000016E0,
    "vblank_status": 0x0000171A,
    "vblank_origin": 0x0000172C,
    "vblank_common": 0x0000173C,
    "vblank_rte": 0x00001850,
}
CAPTURE_PC = {
    **PC,
    "renderer_start": 0x00008B0E,
    "renderer_end": 0x00008CEC,
}
LOOKUP_ROM_SHA256 = "9a7416427e2fda17831ba769e5bcb57b74dda552e965cdff1c82575abacbed87"
LOOKUP_SYMBOL_SHA256 = "b8ba7740e63f4ed4dd27e3fe4f1dfb659d12ae2dea1aefc3a7ebc97c3cabab67"
LOOKUP_PC = {
    **PC, "campaign_projection_end": 0x6AB4,
    "raw_crc_start": 0x6A6E, "raw_crc_end": 0x6A72,
    "renderer_start": 0x8C26, "renderer_end": 0x8E04,
}
LOOKUP_EXTENDED_PC = {
    **EXTENDED_PC, "renderer_mask": 0x8C22,
    "renderer_unmasked": 0x8E06, "hud_begin": 0x8CDE,
    "hud_end": 0x8CE2, "sat_end": 0x8CE6, "overlay_end": 0x8CEA,
}
SPRITE_ROM_SHA256 = "09be6696b05e2efc766776d54cb04f6353fb4d3e904e5d3c76db1c2b9ceb7162"
SPRITE_SYMBOL_SHA256 = "828e9ff50549362d455112f41236e264beafb4c1e75ad3f9f8335fba878f22b8"
SPRITE_PC = {**LOOKUP_PC, "renderer_start": 0x8C1E, "renderer_end": 0x8DFC}
SPRITE_EXTENDED_PC = {
    **EXTENDED_PC, "renderer_mask": 0x8C1A, "renderer_unmasked": 0x8DFE,
    "hud_begin": 0x8CD6, "hud_end": 0x8CDA,
    "sat_end": 0x8CDE, "overlay_end": 0x8CE2,
}
PROFILE_ROM_PINS = {"baseline": PINNED_ROM_SHA256, "lookup": LOOKUP_ROM_SHA256, "sprite": SPRITE_ROM_SHA256}
PROFILE_SYMBOL_PINS = {"baseline": PINNED_SYMBOL_SHA256, "lookup": LOOKUP_SYMBOL_SHA256, "sprite": SPRITE_SYMBOL_SHA256}
PROFILE_PC = {"baseline": PC, "lookup": LOOKUP_PC, "sprite": SPRITE_PC}
PROFILE_EXTENDED_PC = {"baseline": EXTENDED_PC, "lookup": LOOKUP_EXTENDED_PC, "sprite": SPRITE_EXTENDED_PC}
INDEXED_ROM_SHA256 = "c051995c95da38c56d098acf0aa27182d2a51fc6390241382efaf0b9259bcfde"
INDEXED_SYMBOL_SHA256 = SPRITE_SYMBOL_SHA256
PROFILE_ROM_PINS["indexed"] = INDEXED_ROM_SHA256
PROFILE_SYMBOL_PINS["indexed"] = INDEXED_SYMBOL_SHA256
PROFILE_PC["indexed"] = dict(SPRITE_PC)
PROFILE_EXTENDED_PC["indexed"] = dict(SPRITE_EXTENDED_PC)
POOL_ROM_SHA256 = "25a74bd2d9314f2f1a2e5c9c49279449e1d1f78c8bcd7189600073b7a0ae6152"
POOL_SYMBOL_SHA256 = "ea8d8637ad5beb337a22266041dd5d05506e2f67b4471c617c1449a0a7827728"
PROFILE_ROM_PINS["pool"] = POOL_ROM_SHA256
PROFILE_SYMBOL_PINS["pool"] = POOL_SYMBOL_SHA256
PROFILE_PC["pool"] = {
    **SPRITE_PC, "timeout_projection_call": 0x3D9E,
    "reset_projection_call": 0x39FA, "campaign_projection_end": 0x6ACC,
    "tail_start": 0x5636, "tail_end": 0x564A,
    "raw_crc_start": 0x6A86, "raw_crc_end": 0x6A8A,
    "renderer_start": 0x8C36, "renderer_end": 0x8E14,
}
PROFILE_EXTENDED_PC["pool"] = {
    **SPRITE_EXTENDED_PC, "renderer_mask": 0x8C32,
    "renderer_unmasked": 0x8E16, "hud_begin": 0x8CEE,
    "hud_end": 0x8CF2, "sat_end": 0x8CF6, "overlay_end": 0x8CFA,
}
HIGH_WATER_ROM_SHA256 = "96dec5774bcda0d5d0fc1029a051122a8cc9e5f9b2bf62fcba95b87ede4dd11f"
HIGH_WATER_SYMBOL_SHA256 = "6da707bacf8436911b5583f954258c4f9aa54c7a222ba8bf51fe46dc0e795adb"
PROFILE_ROM_PINS["highwater"] = HIGH_WATER_ROM_SHA256
PROFILE_SYMBOL_PINS["highwater"] = HIGH_WATER_SYMBOL_SHA256
PROFILE_PC["highwater"] = {
    **PROFILE_PC["pool"], "timeout_projection_call": 0x3DB6,
    "reset_projection_call": 0x3A12, "campaign_projection_end": 0x6BAA,
    "engine_projection_start": 0x2162, "engine_projection_end": 0x237A,
    "tail_start": 0x56D2, "tail_end": 0x56E6,
    "raw_crc_start": 0x6B64, "raw_crc_end": 0x6B68,
    "renderer_start": 0x8D14, "renderer_end": 0x8EF2,
}
PROFILE_EXTENDED_PC["highwater"] = {
    **PROFILE_EXTENDED_PC["pool"], "renderer_mask": 0x8D10,
    "renderer_unmasked": 0x8EF4, "hud_begin": 0x8DCC,
    "hud_end": 0x8DD0, "sat_end": 0x8DD4, "overlay_end": 0x8DD8,
}
PROFILE_ROM_PINS["renderer_hwm"] = regular.RENDERER_HWM_ROM_SHA256
PROFILE_SYMBOL_PINS["renderer_hwm"] = regular.RENDERER_HWM_SYMBOL_SHA256
PROFILE_PC["renderer_hwm"] = {
    **PROFILE_PC["highwater"], "renderer_start": 0x8DA4, "renderer_end": 0x8F82,
}
PROFILE_EXTENDED_PC["renderer_hwm"] = {
    **PROFILE_EXTENDED_PC["highwater"], "renderer_mask": 0x8DA0,
    "renderer_unmasked": 0x8F84, "hud_begin": 0x8E5C,
    "hud_end": 0x8E60, "sat_end": 0x8E64, "overlay_end": 0x8E68,
}
PROFILE_ROM_PINS["crc_projection"] = regular.CRC_PROJECTION_ROM_SHA256
PROFILE_SYMBOL_PINS["crc_projection"] = regular.CRC_PROJECTION_SYMBOL_SHA256
PROFILE_PC["crc_projection"] = {
    **PROFILE_PC["renderer_hwm"], "timeout_projection_call": 0x3DFC,
    "reset_projection_call": 0x3A58, "campaign_projection_end": 0x6C66,
    "engine_projection_start": 0x2228, "engine_projection_end": 0x23B8,
    "tail_start": 0x5718, "tail_end": 0x57A2,
    "raw_crc_start": 0x6C20, "raw_crc_end": 0x6C24,
    "renderer_start": 0x8E60, "renderer_end": 0x903E,
}
PROFILE_EXTENDED_PC["crc_projection"] = {
    **PROFILE_EXTENDED_PC["renderer_hwm"], "renderer_mask": 0x8E5C,
    "renderer_unmasked": 0x9040, "hud_begin": 0x8F18,
    "hud_end": 0x8F1C, "sat_end": 0x8F20, "overlay_end": 0x8F24,
}
PC_SYMBOL_BINDINGS = {
    "gameplay_call": "MainLoopGameplayStepCall",
    "gameplay_return": "MainLoopPostGameplayStepCall",
    "post_commit": "MainLoopPostCommitReset",
    "reset_coalesce": "MainLoopResetTokenCoalesce",
    "reset_masked": "MainLoopResetTokenMasked",
    "reset_cleared": "MainLoopResetTokensCoalesced",
    "wait_vblank": "MainLoopWaitForVBlank",
    "engine_projection_start": "EngineBuildProjection",
    "tail_start": "VocabularyBuildProjectionTail",
    "renderer_start": "GameplayRendererBuildAndQueue",
    "dma_commit": "DmaCommitPreparedQueue",
    "dma_issue_start": "DmaIssueVramTransfer",
    "vblank_entry": "VBlankInterrupt",
}

ALLOWED_COMMANDS = frozenset(
    {"breakpoint", "commands", "printf", "if", "else", "continue", "end"}
)
MARKER_FIELDS = {
    "HI": ("cycle", "frame", "attempt", "tick", "current", "desired"),
    "MS": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "input",
        "d0",
        "checkpoint_count",
    ),
    "ME": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "result",
        "checkpoint_count",
    ),
    "MR": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "result",
        "checkpoint_count",
        "state_crc",
        "replay_crc",
    ),
    "PS": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "checkpoint_count",
        "engine_state",
    ),
    "PE": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "length",
        "crc",
        "checkpoint_count",
        "state_crc",
    ),
    "ES": ("cycle", "frame", "attempt", "tick", "checkpoint_count"),
    "EE": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "length",
        "crc",
        "checkpoint_count",
    ),
    "TS": ("cycle", "frame", "attempt", "tick", "destination"),
    "TE": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "destination",
        "length",
    ),
    "CS": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "source",
        "length",
    ),
    "CE": ("cycle", "frame", "attempt", "tick", "raw_crc"),
    "RG": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "sr",
        "token",
        "pending",
        "published_depth",
        "build_depth",
        "campaign_flags",
        "checkpoint_count",
        "renderer_attempt",
        "renderer_checkpoint_count",
        "backlog_frames",
    ),
    "RR": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "sr",
        "token",
        "pending",
        "result",
        "checkpoint_count",
        "renderer_attempt",
        "renderer_checkpoint_count",
        "reset_phase",
        "backlog_frames",
    ),
    "BC": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "snapshot_bank",
        "current_bank",
        "token",
        "sr",
    ),
    "BM": ("cycle", "frame", "attempt", "tick", "token", "sr"),
    "BZ": ("cycle", "frame", "attempt", "tick", "token", "sr"),
    "BW": ("cycle", "frame", "attempt", "tick", "token", "sr"),
    "VE": ("cycle", "frame", "attempt", "tick", "sr", "token"),
    "VA": ("cycle", "frame", "entry_status", "sequence"),
    "VC": (
        "cycle",
        "frame",
        "attempt",
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
    "VX": (
        "cycle",
        "frame",
        "attempt",
        "tick",
        "sequence",
        "counter",
        "token",
        "debug_words",
    ),
}
MARKER_RE = re.compile(
    r"^R94R (?P<phase>[A-Z]{1,2})(?P<values>(?: [0-9A-Fa-f]+)+)$"
)
EXTENDED_MARKER_FIELDS = {
    **{
        phase: ("cycle", "frame", "attempt", "tick", "sr", "token")
        for phase in ("MB", "MD", "RM", "RX", "HB", "HE", "SE", "OE", "WW")
    },
    "BT": ("frame", "attempt", "tick", "address", "long0", "long1", "long2", "long3"),
}
PREFIX_MARKER_FIELDS = {
    **MARKER_FIELDS,
    **{name: fields for name, fields in EXTENDED_MARKER_FIELDS.items() if name != "BT"},
    "ME": (*MARKER_FIELDS["ME"], "engine_state", "engine_input", "actor_count", "projectile_count"),
    "RB": EXTENDED_MARKER_FIELDS["BT"],
}
PREFIX_MARKER_RE = re.compile(r"^R94C (?P<phase>[A-Z]{1,2})(?P<values>(?: [0-9A-Fa-f]{1,8})+)$")


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
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def pc_contract(rom: bytes, symbols: Mapping[str, int], *, profile: str = "baseline") -> dict[str, Any]:
    if profile not in PROFILE_PC:
        raise EvidenceFailure("unknown reset diagnostic profile")
    addresses = PROFILE_PC[profile]
    relocation = 446 if profile == "crc_projection" else 258 if profile in {"highwater", "renderer_hwm"} else 36 if profile == "pool" else 12 if profile != "baseline" else 0
    renderer_relocation = addresses["renderer_start"] - PC["renderer_start"]
    errors: list[str] = []
    for name, symbol_name in PC_SYMBOL_BINDINGS.items():
        expected = addresses[name]
        if name == "renderer_start":
            expected -= 10
        if symbols.get(symbol_name) != expected:
            errors.append(
                f"{name} derivation ${expected:X} != {symbol_name} "
                f"${symbols.get(symbol_name, -1):X}"
            )
    if symbols.get("GameplayBuildProjection") != 0x000069F8 + relocation:
        errors.append("GameplayBuildProjection symbol drift")
    if symbols.get("GameplayCommitAndReset") != (0x36D0 if profile == "crc_projection" else 0x368A if profile in {"highwater", "renderer_hwm"} else 0x3672 if profile == "pool" else 0x365A):
        errors.append("GameplayCommitAndReset symbol drift")
    if symbols.get("MainLoopCommitResetCall") != 0x00000320:
        errors.append("MainLoopCommitResetCall symbol drift")
    if symbols.get("Crc32UpdateRaw") != 0x00001BF2:
        errors.append("Crc32UpdateRaw symbol drift")
    if symbols.get("RendererPrepareResetOverlay") != 0x000091CC + renderer_relocation + (6 if profile in {"highwater", "renderer_hwm", "crc_projection"} else 0):
        errors.append("RendererPrepareResetOverlay symbol drift")
    exact_windows = {
        "gameplay_call": (PC["gameplay_call"], "6100378E" if profile == "crc_projection" else "61003748" if profile in {"highwater", "renderer_hwm"} else "61003730" if profile == "pool" else "61003718"),
        "gameplay_return": (PC["gameplay_return"], "0C400008"),
        "post_commit": (PC["post_commit"], "4A406712"),
        "renderer_call": (0x0000033E, f"4EB9{addresses['renderer_start'] - 10:08X}"),
        "reset_coalesce": (PC["reset_coalesce"], "46FC2700"),
        "reset_masked": (PC["reset_masked"], "427900FFF014"),
        "reset_cleared": (PC["reset_cleared"], "60000002"),
        "wait_vblank": (PC["wait_vblank"], "4E722500"),
        "timeout_projection_call": (
            addresses["timeout_projection_call"],
            "61002DB8" if profile == "crc_projection" else "61002D42" if profile in {"highwater", "renderer_hwm"} else "61002C7C" if relocation else "61002C70",
        ),
        "reset_projection_call": (
            addresses["reset_projection_call"],
            "6100315C" if profile == "crc_projection" else "610030E6" if profile in {"highwater", "renderer_hwm"} else "61003020" if relocation else "61003014",
        ),
        "campaign_projection_return": (
            addresses["campaign_projection_end"],
            "4E75",
        ),
        "engine_projection_return": (
            addresses["engine_projection_end"],
            "4E75",
        ),
        "tail_return": (addresses["tail_end"], "4E75"),
        "raw_crc_call_return": (addresses["raw_crc_start"], "6100AFD04681" if profile == "crc_projection" else "6100B08C4681" if profile in {"highwater", "renderer_hwm"} else "6100B16A4681" if profile == "pool" else "6100B1824681" if relocation else "6100B18E4681"),
        "renderer_mask": (addresses["renderer_start"] - 4, "46FC2700"),
        "renderer_checkpoint_gate": (
            0x00008C7C + renderer_relocation,
            (
                "303900FF1420B07900FF83246748363900FF832433C000FF8324"
                "343900FF16C80242000167304A7900FF900066284A7900FF9002"
                "66204A4166085243B043661660060C400001660E33FC000400FF"
                "8322700460000128"
            ),
        ),
        "renderer_restore": (
            addresses["renderer_end"],
            "46C74CDF7CF84E75",
        ),
        "vblank_origin": (
            PC["vblank_origin"],
            "0C79FFFF00FFF0326706527900FFF032",
        ),
        "vblank_rte": (PC["vblank_rte"], "4E73"),
    }
    if profile == "indexed":
        exact_windows["crc_indexed_base_loop"] = (0x1C2A, "43F90001966074003418E05AB342E58A2431280042414841B58151C8FFEA")
    if profile == "pool":
        exact_windows["crc_indexed_base_loop"] = (0x1C2A, "43F90001967874003418E05AB342E58A2431280042414841B58151C8FFEA")
        exact_windows["pool_prior_current_count"] = (0x24D2, "48E71E30244836002C021A29000212C112C03401534241F900FF1440D0C218108A0412C4")
        exact_windows["pool_empty_skip"] = (0x2512, "224B4A05660E3A033003C0FC0018D2C06000008C")
    if profile in {"highwater", "renderer_hwm"}:
        exact_windows["crc_indexed_base_loop"] = (0x1C2A, "43F9000197EC74003418E05AB342E58A2431280042414841B58151C8FFEA" if profile == "renderer_hwm" else "43F90001975C74003418E05AB342E58A2431280042414841B58151C8FFEA")
        exact_windows["pool_prior_current_count"] = (0x24EA, "48E71E30244836002C021A29000212C112C03401534241F900FF1440D0C218108A0412C4")
        exact_windows["pool_empty_skip"] = (0x252A, "224B4A05660E3A033003C0FC0018D2C06000008C")
    if profile == "crc_projection":
        exact_windows.update(regular.CRC_PROJECTION_WINDOWS)
        for symbol_name, expected in regular.CRC_PROJECTION_SYMBOL_BINDINGS.items():
            if symbols.get(symbol_name) != expected:
                errors.append(f"CRC/projection {symbol_name} symbol drift")
    for name, (address, expected_hex) in exact_windows.items():
        expected = bytes.fromhex(expected_hex)
        if rom[address : address + len(expected)] != expected:
            errors.append(f"{name} exact ROM bytes drift")
    if errors:
        raise EvidenceFailure("; ".join(errors))
    return {
        "addresses": dict(addresses),
        "symbol_bindings": dict(PC_SYMBOL_BINDINGS),
        "exact_windows": {
            name: {
                "address": address,
                "hex": expected_hex,
                "sha256": _sha256_bytes(bytes.fromhex(expected_hex)),
            }
            for name, (address, expected_hex) in exact_windows.items()
        },
        "rom_windows": {
            name: {
                "address": address,
                "hex": rom[address : address + 16].hex().upper(),
            }
            for name, address in addresses.items()
        },
    }


def _range_body(
    value: str,
    minimum: int,
    maximum: int,
    *body: str,
) -> tuple[str, ...]:
    return (
        f"if {value} < ${minimum:X}",
        "continue",
        "else",
        f"if {value} < ${maximum + 1:X}",
        *body,
        "continue",
        "else",
        "continue",
        "end",
        "end",
    )


def build_command_script(
    symbols: Mapping[str, int], *, extended_overlay: bool = False, profile: str = "baseline"
) -> str:
    frame = _memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    boot = _memory(symbols["DEBUG_BOOT_STAGE"], "w")
    token = _memory(symbols["DEBUG_FRAME_TOKEN"], "w")
    sequence = _memory(symbols["DEBUG_SEQUENCE"], "l")
    attempt = _memory(symbols["ENGINE_ATTEMPT"], "w")
    tick = _memory(symbols["ENGINE_TICK"], "w")
    engine_state = _memory(symbols["ENGINE_STATE"], "w")
    input_held = _memory(symbols["INPUT_HELD"], "b")
    checkpoint_count = _memory(symbols["ENGINE_CHECKPOINT_COUNT"], "w")
    state_crc = _memory(symbols["ENGINE_STATE_CRC"], "l")
    replay_crc = _memory(symbols["GAMEPLAY_LAST_BANK_CRC"], "l")
    current_bank = _memory(symbols["TIMELINE_CURRENT_BANK"], "b")
    renderer_attempt = _memory(symbols["RENDERER_LAST_ATTEMPT"], "w")
    renderer_checkpoint = _memory(
        symbols["RENDERER_LAST_CHECKPOINT_COUNT"], "w"
    )
    renderer_result = _memory(symbols["RENDERER_LAST_RESULT"], "w")
    renderer_reset_phase = _memory(symbols["RENDERER_RESET_PHASE"], "w")
    renderer_backlog = _memory(symbols["RENDERER_BACKLOG_FRAMES"], "w")
    campaign_flags = _memory(symbols["VOCAB_CAMPAIGN_FLAGS"], "w")
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

    if profile not in PROFILE_PC or (profile != "baseline" and not extended_overlay):
        raise EvidenceFailure("lookup profile requires the extended diagnostic")
    pc = PROFILE_PC[profile]
    extra_pc = PROFILE_EXTENDED_PC[profile]
    addresses = {**pc, **extra_pc} if extended_overlay else pc
    frame_limit = EXTENDED_FRAME_LIMIT if extended_overlay else FAIL_CLOSED_FRAME_LIMIT
    ordered = tuple(addresses)
    lines = [f"breakpoint ${addresses[name]:X}" for name in ordered]
    indices = {name: index for index, name in enumerate(ordered)}

    def commands(name: str, body: Sequence[str]) -> None:
        lines.append(f"commands {indices[name]}")
        lines.extend(body)
        lines.append("end")

    commands(
        "gameplay_call",
        (
            f"if {attempt} < $1",
            f"if {tick} < $2CE",
            "continue",
            "else",
            f"if {tick} < $2CF",
            (
                'printf "R94R MS %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {input_held} d0 "
                f"{checkpoint_count}"
            ),
            "continue",
            "else",
            f"if {tick} < $2D0",
            (
                'printf "R94R MS %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {input_held} d0 "
                f"{checkpoint_count}"
            ),
            (
                'printf "R94R HI %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} $0 $2"
            ),
            'printf "R94R INPUT RESET 02\\n"',
            "else",
            "continue",
            "end",
            "end",
            "end",
            "else",
            f"if {attempt} < $2",
            f"if {tick} < ${4 if extended_overlay else 2:X}",
            (
                'printf "R94R MS %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {input_held} d0 "
                f"{checkpoint_count}"
            ),
            "continue",
            "else",
            "continue",
            "end",
            "else",
            "continue",
            "end",
            "end",
        ),
    )
    commands(
        "gameplay_return",
        _range_body(
            frame,
            719,
            FAIL_CLOSED_FRAME_LIMIT,
            (
                'printf "R94R ME %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} d0 {checkpoint_count}"
            ),
        ),
    )
    commands(
        "post_commit",
        _range_body(
            frame,
            719,
            FAIL_CLOSED_FRAME_LIMIT,
            (
                'printf "R94R MR %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} d0 {checkpoint_count} "
                f"{state_crc} {replay_crc}"
            ),
        ),
    )
    for name in ("timeout_projection_call", "reset_projection_call"):
        commands(
            name,
            _range_body(
                frame,
                719,
                FAIL_CLOSED_FRAME_LIMIT,
                (
                    'printf "R94R PS %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {attempt} {tick} "
                    f"{checkpoint_count} {engine_state}"
                ),
            ),
        )
    commands(
        "campaign_projection_end",
        _range_body(
            frame,
            719,
            FAIL_CLOSED_FRAME_LIMIT,
            (
                'printf "R94R PE %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} d0 d1 "
                f"{checkpoint_count} {state_crc}"
            ),
        ),
    )
    projection_boundaries = (
        (
            "engine_projection_start",
            "ES",
            f"cycle {frame} {attempt} {tick} {checkpoint_count}",
        ),
        (
            "engine_projection_end",
            "EE",
            f"cycle {frame} {attempt} {tick} d0 d1 {checkpoint_count}",
        ),
        (
            "tail_start",
            "TS",
            f"cycle {frame} {attempt} {tick} a0",
        ),
        (
            "tail_end",
            "TE",
            f"cycle {frame} {attempt} {tick} a0 d0",
        ),
        (
            "raw_crc_start",
            "CS",
            f"cycle {frame} {attempt} {tick} a0 d0",
        ),
        (
            "raw_crc_end",
            "CE",
            f"cycle {frame} {attempt} {tick} d1",
        ),
    )
    for name, phase, values in projection_boundaries:
        placeholders = "%x " * (len(MARKER_FIELDS[phase]) - 1) + "%x"
        commands(
            name,
            _range_body(
                frame,
                719,
                FAIL_CLOSED_FRAME_LIMIT,
                f'printf "R94R {phase} {placeholders}\\n" {values}',
            ),
        )
    commands(
        "renderer_start",
        _range_body(
            frame,
            719,
            FAIL_CLOSED_FRAME_LIMIT,
            (
                'printf "R94R RG %x %x %x %x %x %x %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} sr {token} {pending} "
                f"{published} {build_depth} {campaign_flags} "
                f"{checkpoint_count} {renderer_attempt} "
                f"{renderer_checkpoint} {renderer_backlog}"
            ),
        ),
    )
    renderer_line = (
        'printf "R94R RR %x %x %x %x %x %x %x %x %x %x %x %x %x\\n" '
        f"cycle {frame} {attempt} {tick} sr {token} {pending} d0 "
        f"{checkpoint_count} {renderer_attempt} {renderer_checkpoint} "
        f"{renderer_reset_phase} {renderer_backlog}"
    )
    buffer_lines = []
    if extended_overlay:
        for base, size in BUFFER_RANGES:
            for address in range(base, base + size, 16):
                values = " ".join(_memory(address + offset, "l") for offset in range(0, 16, 4))
                buffer_lines.append(
                    'printf "R94R BT %x %x %x %x %x %x %x %x\\n" '
                    f"{frame} {attempt} {tick} ${address:X} {values}"
                )
    commands(
        "renderer_end",
        (
            f"if {frame} < $2E",
            "continue",
            "else",
            f"if {frame} < $2F",
            (
                'printf "R94R HI %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} $1 $8"
            ),
            'printf "R94R INPUT 002E 08\\n"',
            "else",
            f"if {frame} < $43",
            "continue",
            "else",
            f"if {frame} < $44",
            (
                'printf "R94R HI %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} $8 $0"
            ),
            'printf "R94R INPUT 0043 00\\n"',
            "else",
            f"if {frame} < $2CF",
            "continue",
            "else",
            renderer_line,
            *buffer_lines,
            "continue",
            "end",
            "end",
            "end",
            "end",
            "end",
        ),
    )
    commands(
        "reset_coalesce",
        (
            (
                'printf "R94R BC %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} d3 {current_bank} "
                f"{token} sr"
            ),
            "continue",
        ),
    )
    commands(
        "reset_masked",
        (
            (
                'printf "R94R BM %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {token} sr"
            ),
            "continue",
        ),
    )
    commands(
        "reset_cleared",
        (
            (
                'printf "R94R BZ %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {token} sr"
            ),
            "continue",
        ),
    )
    commands(
        "wait_vblank",
        (
            *(
                (
                    f"if {frame} < $2CF",
                    "else",
                    'printf "R94R WW %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {attempt} {tick} sr {token}",
                    "end",
                ) if extended_overlay else ()
            ),
            f"if {attempt} < $1",
            "continue",
            "else",
            f"if {tick} < $1",
            (
                'printf "R94R BW %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {token} sr"
            ),
            "continue",
            "else",
            "continue",
            "end",
            "end",
        ),
    )
    commands(
        "vblank_entry",
        (
            f"if {boot} < ${BOOT_STAGE_RUNNING:X}",
            "continue",
            "else",
            f"if {frame} < $1",
            f'printf "{READY_MARKER}\\n"',
            "else",
            f"if {frame} < $2CE",
            "continue",
            "else",
            (
                'printf "R94R VE %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} sr {token}"
            ),
            "continue",
            "end",
            "end",
            "end",
        ),
    )
    commands(
        "vblank_ack",
        (
            f"if {frame} < $2CE",
            "continue",
            "else",
            (
                'printf "R94R VA %x %x %x %x\\n" '
                f"cycle {frame} {entry_status} {sequence}"
            ),
            "continue",
            "end",
        ),
    )
    queue_lines: list[str] = [
        f"if {frame} < $2CF",
        "continue",
        "else",
        (
            'printf "R94R VC %x %x %x %x %x %x %x %x\\n" '
            f"cycle {frame} {attempt} {tick} {pending} {published} "
            f"{build_depth} {build_words}"
        ),
    ]
    for index in range(6):
        address = entries + index * 12
        queue_lines.extend(
            (
                f"if {published} < ${index + 1:X}",
                "else",
                (
                    'printf "R94R Q %x %x %x %x %x %x %x\\n" '
                    f"{frame} ${index:X} {_memory(address, 'l')} "
                    f"{_memory(address + 4, 'w')} "
                    f"{_memory(address + 6, 'w')} "
                    f"{_memory(address + 8, 'w')} "
                    f"{_memory(address + 10, 'w')}"
                ),
                "end",
            )
        )
    queue_lines.extend(("continue", "end"))
    commands("dma_commit", tuple(queue_lines))
    for name, phase, text in (
        (
            "dma_issue_start",
            "DS",
            'printf "R94R DS %x %x %x %x %x %x\\n" '
            f"cycle {frame} a0 d0 d1 {commit_words}",
        ),
        (
            "dma_issue_end",
            "DE",
            'printf "R94R DE %x %x %x %x %x\\n" '
            f"cycle {frame} {commit_words} d0 d1",
        ),
        (
            "vblank_post_dma",
            "VP",
            'printf "R94R VP %x %x %x %x %x %x\\n" '
            f"cycle {frame} {commit_words} {debug_words} "
            f"{debug_depth} {pending}",
        ),
        (
            "vblank_status",
            "VS",
            'printf "R94R VS %x %x %x %x %x\\n" '
            f"cycle {frame} d0 {entry_status} {counter}",
        ),
        (
            "vblank_common",
            "VK",
            'printf "R94R VK %x %x %x %x %x\\n" '
            f"cycle {frame} d0 d1 {counter}",
        ),
    ):
        _ = phase
        commands(
            name,
            (
                f"if {frame} < $2CF",
                "continue",
                "else",
                text,
                "continue",
                "end",
            ),
        )
    commands(
        "vblank_origin",
        (
            'printf "R94R VO %x %x %x %x %x %x\\n" '
            f"cycle {frame} d0 d1 {counter} {sequence}",
            "continue",
        ),
    )
    commands(
        "vblank_rte",
        (
            f"if {boot} < ${BOOT_STAGE_RUNNING:X}",
            "continue",
            "else",
            f"if {frame} < $2CF",
            "continue",
            "else",
            (
                'printf "R94R VX %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} {sequence} {counter} "
                f"{token} {debug_words}"
            ),
            f"if {attempt} < $1",
            f"if {frame} < ${frame_limit:X}",
            "continue",
            "else",
            f'printf "{COMPLETE_MARKER}\\n"',
            "end",
            "else",
            f"if {tick} < ${5 if extended_overlay else 1:X}",
            f"if {frame} < ${frame_limit:X}",
            "continue",
            "else",
            f'printf "{COMPLETE_MARKER}\\n"',
            "end",
            "else",
            f'printf "{COMPLETE_MARKER}\\n"',
            "end",
            "end",
            "end",
            "end",
        ),
    )
    if extended_overlay:
        for name, phase in (
            ("main_begin", "MB"), ("main_done", "MD"),
            ("renderer_mask", "RM"), ("renderer_unmasked", "RX"),
            ("hud_begin", "HB"), ("hud_end", "HE"),
            ("sat_end", "SE"), ("overlay_end", "OE"),
        ):
            marker = (
                f'printf "R94R {phase} %x %x %x %x %x %x\\n" '
                f"cycle {frame} {attempt} {tick} sr {token}"
            )
            body = _range_body(frame, 719, frame_limit, marker)
            if name == "main_done":
                body = (
                    f"if {frame} < $2CF", "continue", "else", marker,
                    f"if {attempt} < $1", "continue", "else",
                    f"if {tick} < $4", "continue", "else",
                    f'printf "{COMPLETE_MARKER}\\n"', "end", "end", "end",
                )
            commands(name, body)
    lines.extend(("continue", ""))
    return "\n".join(lines)


def prefix_schedule() -> bytes:
    """The recorded route prefix, never a host-simulated outcome."""
    schedule = b03.ATTEMPT_0_INPUTS + b03.ATTEMPT_1_INPUTS + b03.ATTEMPT_2_INPUTS[:PREFIX_FINAL_TICK]
    if len(schedule) != PREFIX_TOTAL_STEPS or _sha256_bytes(schedule) != PREFIX_SCHEDULE_SHA256:
        raise EvidenceFailure("canonical prefix schedule bytes drift")
    return schedule


def prefix_input_stages() -> list[dict[str, Any]]:
    schedule = prefix_schedule()
    return [
        {"total": index + 1, "attempt": index // 720, "pre_tick": index % 720,
         "current": current, "desired": schedule[index + 1],
         "marker": f"R94C INPUT {index + 1:04X} {schedule[index + 1]:02X}"}
        for index, current in enumerate(schedule[:-1]) if current != schedule[index + 1]
    ]


def prefix_route_contract(root: Path) -> dict[str, Any]:
    sources = {}
    for label, (relative, expected) in PREFIX_RUNTIME_PINS.items():
        path = root / relative
        if sha256_file(path) != expected:
            raise EvidenceFailure(f"canonical prefix prior runtime {label} hash drift")
        sources[label] = _file_record(path, root)
    report = json.loads((root / PREFIX_RUNTIME_PINS["report"][0]).read_text("utf-8"))
    if report.get("status") != "pass" or len(report.get("sessions", [])) != 2:
        raise EvidenceFailure("canonical prefix prerequisite is not two passing sessions")
    schedules = (b03.ATTEMPT_0_INPUTS, b03.ATTEMPT_1_INPUTS, b03.ATTEMPT_2_INPUTS)
    for session in report["sessions"]:
        for field in ("attempt_inputs", "commanded_inputs", "pre_step_polled_inputs"):
            if any(session[field][str(index)] != list(schedule) for index, schedule in enumerate(schedules)):
                raise EvidenceFailure(f"canonical prefix prerequisite {field} drift")
    return {
        "sources": sources, "schedule_sha256": _sha256_bytes(prefix_schedule()),
        "authoritative_steps": PREFIX_TOTAL_STEPS,
        "input_stages": prefix_input_stages(),
        "reset_snapshots": {
            str(attempt): report["sessions"][0]["snapshots"][f"post_timeout_{attempt}"]
            for attempt in (1, 2)
        },
        "projectile_counts": {
            str(tick): snapshot["engine"]["pool_counts"][1]
            for tick in PREFIX_SAMPLE_POST_TICKS
            if (snapshot := report["sessions"][0]["snapshots"].get(f"attempt_2_tick_{tick}")) is not None
        },
        "scope": "Input route and prior natural reset state only; historical report is not this candidate's runtime or cycle pass.",
    }


def build_prefix_command_script(symbols: Mapping[str, int], *, profile: str) -> str:
    """Reuse the exact native phase markers for a bounded natural route prefix."""
    baseline_script = build_command_script(symbols, extended_overlay=True, profile=profile)
    addresses = {**PROFILE_PC[profile], **PROFILE_EXTENDED_PC[profile],
                 "campaign_projection_start": symbols["GameplayBuildProjection"]}
    frame = _memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    attempt = _memory(symbols["ENGINE_ATTEMPT"], "w")
    tick = _memory(symbols["ENGINE_TICK"], "w")
    boot = _memory(symbols["DEBUG_BOOT_STAGE"], "w")
    pre_ranges = ((58, 61), (118, 121), (178, 181), (182, 196), (238, 241))
    post_ranges = ((59, 62), (119, 122), (179, 182), (183, 197), (239, 242))
    checkpoint_ranges = tuple((tick, tick + 1) for tick in (60, 120, 180, 240))
    lines = [f"breakpoint ${address:X}" for address in addresses.values()]
    indices = {name: index for index, name in enumerate(addresses)}

    def marker(phase: str) -> str:
        candidates = {line for line in baseline_script.splitlines() if line.startswith(f'printf "R94R {phase} ')}
        if len(candidates) != 1:
            raise EvidenceFailure(f"prefix marker template {phase} is not unique")
        return candidates.pop().replace('printf "R94R ', 'printf "R94C ', 1)

    def commands(name: str, body: Sequence[str]) -> None:
        lines.extend((f"commands {indices[name]}", *body, "end"))

    def select_ranges(ranges: Sequence[tuple[int, int]], body: Sequence[str]) -> list[str]:
        if not ranges:
            return ["continue"]
        first, *rest = ranges
        return [f"if {tick} < ${first[0]:X}", "continue", "else",
                f"if {tick} < ${first[1]:X}", *body, "else",
                *select_ranges(rest, body), "end", "end"]

    def sampled(body: Sequence[str], ranges: Sequence[tuple[int, int]] = post_ranges) -> list[str]:
        return [f"if {attempt} < $2", "continue", "else", f"if {attempt} < $3",
                *select_ranges(ranges, body), "else", "continue", "end", "end"]

    def stages_for_attempt(number: int) -> list[str]:
        stages = [item for item in prefix_input_stages() if item["attempt"] == number]

        def choose(remaining: Sequence[Mapping[str, Any]]) -> list[str]:
            if not remaining:
                return ["continue"]
            stage, *rest = remaining
            return [f"if {tick} < ${stage['pre_tick']:X}", "continue", "else",
                    f"if {tick} < ${stage['pre_tick'] + 1:X}",
                    'printf "R94C HI %x %x %x %x %x %x\\n" '
                    f"cycle {frame} {attempt} {tick} ${stage['current']:X} ${stage['desired']:X}",
                    f'printf "{stage["marker"]}\\n"', "else", *choose(rest), "end", "end"]

        return choose(stages)

    commands("gameplay_call", [marker("MS"), f"if {attempt} < $1", *stages_for_attempt(0),
                              "else", f"if {attempt} < $2", *stages_for_attempt(1),
                              "else", f"if {attempt} < $3", *stages_for_attempt(2),
                              "else", 'printf "R94C LIMIT\\n"', f'printf "{COMPLETE_MARKER}\\n"',
                              "end", "end", "end"])
    post = marker("ME").replace('\\n"', ' %x %x %x %x\\n"', 1)
    post += " " + " ".join((_memory(symbols["ENGINE_STATE"], "w"),
                            _memory(symbols["ENGINE_INPUT_HELD"], "b"),
                            _memory(symbols["ENGINE_ACTIVE_ACTOR_COUNT"], "b"),
                            _memory(symbols["ENGINE_POOL_COUNTS"] + 1, "b")))
    commands("gameplay_return", (post, "continue"))
    replay_lines = [
        'printf "R94C RB %x %x %x %x %x %x %x %x\\n" '
        f"{frame} {attempt} {tick} ${address:X} " +
        " ".join(_memory(address + offset, "l") for offset in range(0, 16, 4))
        for address in range(0xFF0000, 0xFF1400, 16)
    ]
    commands("post_commit", (marker("MR"), *replay_lines, "continue"))
    for name in ("timeout_projection_call", "reset_projection_call"):
        commands(name, ("continue",))
    for name, phase in (("campaign_projection_start", "PS"), ("campaign_projection_end", "PE"),
                        ("engine_projection_start", "ES"), ("engine_projection_end", "EE"),
                        ("tail_start", "TS"), ("tail_end", "TE"),
                        ("raw_crc_start", "CS"), ("raw_crc_end", "CE")):
        commands(name, sampled((marker(phase), "continue"), checkpoint_ranges))
    for name, phase in (("renderer_start", "RG"), ("renderer_end", "RR"),
                        ("renderer_mask", "RM"), ("renderer_unmasked", "RX"),
                        ("hud_begin", "HB"), ("hud_end", "HE"),
                        ("sat_end", "SE"), ("overlay_end", "OE")):
        commands(name, sampled((marker(phase), "continue")))
    commands("main_begin", sampled((marker("MB"), "continue"), pre_ranges))
    commands("main_done", sampled((marker("MD"), f"if {tick} < $F1", "continue", "else",
                                   f'printf "{COMPLETE_MARKER}\\n"', "end")))
    for name, phase in (("reset_coalesce", "BC"), ("reset_masked", "BM"), ("reset_cleared", "BZ")):
        commands(name, (marker(phase), "continue"))
    commands("wait_vblank", (f"if {attempt} < $1", "continue", "else", f"if {tick} < $1",
                             marker("BW"), "continue", "else", *sampled((marker("WW"), "continue"), PREFIX_IRQ_TICK_RANGES), "end", "end"))
    commands("vblank_entry", (f"if {boot} < ${BOOT_STAGE_RUNNING:X}", "continue", "else",
                              f"if {frame} < $1", f'printf "{READY_MARKER}\\n"', "else",
                              *sampled((marker("VE"), "continue"), PREFIX_IRQ_TICK_RANGES), "end", "end"))
    queue_lines = [marker("VC")]
    published = _memory(symbols["DMA_QUEUE_PUBLISHED_DEPTH"], "w")
    for index in range(6):
        address = symbols["DMA_QUEUE_ENTRIES"] + index * 12
        queue_lines.extend((f"if {published} < ${index + 1:X}", "else",
                            'printf "R94C Q %x %x %x %x %x %x %x\\n" '
                            f"{frame} ${index:X} {_memory(address, 'l')} {_memory(address + 4, 'w')} "
                            f"{_memory(address + 6, 'w')} {_memory(address + 8, 'w')} {_memory(address + 10, 'w')}", "end"))
    commands("dma_commit", sampled((*queue_lines, "continue"), PREFIX_IRQ_TICK_RANGES))
    for name, phase in (("vblank_ack", "VA"), ("dma_issue_start", "DS"), ("dma_issue_end", "DE"),
                        ("vblank_post_dma", "VP"), ("vblank_status", "VS"), ("vblank_origin", "VO"),
                        ("vblank_common", "VK")):
        commands(name, sampled((marker(phase), "continue"), PREFIX_IRQ_TICK_RANGES))
    commands("vblank_rte", (f"if {frame} < ${PREFIX_PHYSICAL_FAIL_STOP:X}",
                            *sampled((marker("VX"), "continue"), PREFIX_IRQ_TICK_RANGES), "else",
                            'printf "R94C LIMIT\\n"', f'printf "{COMPLETE_MARKER}\\n"', "end"))
    lines.extend(("continue", ""))
    return "\n".join(lines)


def prefix_command_policy_errors(script: str, symbols: Mapping[str, int], *, profile: str) -> list[str]:
    errors = []
    if script != build_prefix_command_script(symbols, profile=profile):
        errors.append("prefix command program differs from exact generator")
    if len(script.encode("ascii")) > 96 * 1024 or len(script.splitlines()) > 2500:
        errors.append("prefix command program exceeds static size bounds")
    if script.splitlines().count("continue") > 512:
        errors.append("prefix static continue count exceeds512")
    for line in script.splitlines():
        if line and line.split(maxsplit=1)[0] not in ALLOWED_COMMANDS:
            errors.append("prefix command program contains a non-read-only command")
    try:
        prefix_console_capacity(script)
    except EvidenceFailure as error:
        errors.append(str(error))
    return errors


def prefix_console_capacity(script: str) -> dict[str, int]:
    """Upper bounds from printf operand widths, including wrapped command echo."""
    width = 120
    marker_widths = []
    for line in script.splitlines():
        match = re.fullmatch(r'printf "(R94C .*?)" (.*)', line)
        if match is None:
            continue
        format_text, operands = match.groups()
        args = operands.split()
        if format_text.count("%x") != len(args):
            raise EvidenceFailure("prefix printf capacity operand mismatch")
        # Byte/word memory reads have bounded widths; registers, cycle, long
        # reads and constants conservatively use the full unsigned32 width.
        marker_widths.append(len(format_text.replace("%x", "").replace(r"\n", "")) +
                             sum(2 if arg.endswith(".b") else 4 if arg.endswith(".w") else 8 for arg in args))
    if not marker_widths or max(marker_widths) >= width:
        raise EvidenceFailure("prefix runtime marker can wrap its console row")
    echo_rows = sum((len(line) + 2 + width - 1) // width for line in script.splitlines())
    # 512 extra rows cover breakpoint acknowledgements, initial console text,
    # all 23 stopped lifecycle displays/prompts, and final quit. Every runtime
    # marker (including WAIT/replay) is already charged to MAX_RECORDS.
    required_rows = PREFIX_MAX_RECORDS + echo_rows + 512
    if not required_rows < PREFIX_CONSOLE_ROWS <= 32767:
        raise EvidenceFailure("prefix full console exceeds bounded Win32 row capacity")
    return {"width": width, "height": PREFIX_CONSOLE_ROWS, "maximum_records": PREFIX_MAX_RECORDS,
            "maximum_marker_columns": max(marker_widths), "script_echo_rows": echo_rows,
            "other_console_rows_allowance": 512, "required_rows_upper_bound": required_rows,
            "maximum_utf8_console_bytes": PREFIX_MAX_CONSOLE_BYTES}


def prefix_expected_input_events() -> list[str]:
    """Exact bounded host transport transitions; not a simulation of gameplay."""
    pressed: set[str] = set()
    events = []
    for desired in (prefix_schedule()[0], *(item["desired"] for item in prefix_input_stages()), 0):
        for name, _virtual, _scan, _extended, bit in b03.KEYS:
            if name in pressed and not desired & bit:
                pressed.remove(name)
                events.append(f"{name}:up")
        for name, _virtual, _scan, _extended, bit in b03.KEYS:
            if desired & bit:
                events.append(f"{name}:reassert" if name in pressed else f"{name}:down")
                pressed.add(name)
    return events


def parse_prefix_markers(console_text: str) -> list[dict[str, Any]]:
    """Strict opt-in grammar; malformed or truncated marker text is evidence failure."""
    if len(console_text.encode("utf-8")) > PREFIX_MAX_CONSOLE_BYTES:
        raise EvidenceFailure("prefix console byte bound exceeded")
    literals = {READY_MARKER, COMPLETE_MARKER, *(item["marker"] for item in prefix_input_stages())}
    records = []
    for raw in console_text.splitlines():
        line = raw.strip()
        if not line.startswith(("R94C", "R94R")) or line in literals:
            continue
        match = PREFIX_MARKER_RE.fullmatch(line)
        if match is None or match["phase"] not in PREFIX_MARKER_FIELDS:
            raise EvidenceFailure(f"unknown/malformed prefix marker: {line}")
        fields = PREFIX_MARKER_FIELDS[match["phase"]]
        values = match["values"].split()
        if len(fields) != len(values):
            raise EvidenceFailure(f"prefix {match['phase']} field count mismatch")
        records.append({"phase": match["phase"], **dict(zip(fields, (int(value, 16) for value in values)))})
        if len(records) > PREFIX_MAX_RECORDS:
            raise EvidenceFailure("prefix record bound exceeded")
    return records


def prefix_input_errors(records: Sequence[Mapping[str, Any]], *, pre_count: int = PREFIX_TOTAL_STEPS,
                        complete: bool = True) -> list[str]:
    """Used before each next-mask post as well as on the completed raw capture."""
    errors = []
    if not 1 <= pre_count <= PREFIX_TOTAL_STEPS:
        return ["prefix PRE proof bound invalid"]
    ordered = [item for item in records if item["phase"] in {"MS", "ME"}]
    expected_order = [phase for _ in range(pre_count) for phase in ("MS", "ME")]
    if not complete:
        expected_order.pop()
    if [item["phase"] for item in ordered] != expected_order:
        errors.append("prefix PRE/POST count or alternating order mismatch")
    if ordered and ordered[0].get("frame") != 1:
        errors.append("prefix first PRE must follow READY frame0 at exact physical frame1")
    schedule = prefix_schedule()
    for phase in ("MS", "ME"):
        for index, item in enumerate(_by_phase(records, phase)):
            if index >= pre_count:
                errors.append("prefix contains excess authoritative step")
                break
            attempt, tick = divmod(index, 720)
            post_tick = tick + int(phase == "ME")
            if (item["attempt"], item["tick"], item["checkpoint_count"]) != (attempt, post_tick, 1 + post_tick // 60):
                errors.append(f"prefix {phase} identity/checkpoint mismatch at total {index + 1}")
            if not 1 <= item["frame"] < PREFIX_PHYSICAL_FAIL_STOP:
                errors.append("prefix physical fail-stop reached/out of bounds")
            if phase == "MS":
                if (item["input"], item["d0"]) != (schedule[index], schedule[index]):
                    errors.append(f"prefix PRE input/d0 mismatch at total {index + 1}")
            elif (item["result"] & 0xFFFF, item["engine_state"], item["engine_input"], item["actor_count"]) != (8 if post_tick == 720 else 0, 2 if post_tick == 720 else 1, schedule[index], attempt + 1):
                errors.append(f"prefix POST result/state/input/actors mismatch at total {index + 1}")
            if phase == "ME" and not 0 <= item["projectile_count"] <= 32:
                errors.append("prefix projectile count exceeds bounded pool")
    return errors


def prefix_console_contract_errors(console: str, script: str) -> list[str]:
    errors = []
    lines = [line.strip() for line in console.splitlines() if line.strip()]
    stages = prefix_input_stages()
    lifecycle = [READY_MARKER, *(item["marker"] for item in stages), COMPLETE_MARKER]
    positions = []
    for marker in lifecycle:
        if lines.count(marker) != 1:
            errors.append(f"prefix lifecycle multiplicity: {marker}")
            continue
        position = lines.index(marker)
        positions.append(position)
        expected = (("68K Breakpoint 21 hit", "169A:", "> continue") if marker == READY_MARKER else
                    ("68K Breakpoint 29 hit", "344:", ">") if marker == COMPLETE_MARKER else
                    ("68K Breakpoint 0 hit", "2FA:", "> continue"))
        tail = lines[position + 1:position + 4]
        if len(tail) != 3 or tail[0] != expected[0] or not tail[1].startswith(expected[1]) or tail[2] != expected[2]:
            errors.append(f"prefix stopped lifecycle mismatch: {marker}")
    if positions != sorted(positions):
        errors.append("prefix lifecycle order mismatch")
    if lines.count("> continue") != len(stages) + 2 or lines.count("> quit") != 0 or lines[-1:] != [">"]:
        errors.append("prefix exact staged continue/pre-quit prompt count mismatch")
    for index, command in enumerate(line for line in script.splitlines() if line.startswith("breakpoint ")):
        echo, ack = f"> {command}", f"68K Breakpoint {index} set at ${command.split('$', 1)[1]}"
        if lines.count(echo) != 1 or lines.count(ack) != 1 or lines.index(ack) != lines.index(echo) + 1:
            errors.append(f"prefix breakpoint lifecycle mismatch: {index}")
    for text in ("Failed to eval", "Unrecognized", "not a recognized command", "fgets failed"):
        if text in console:
            errors.append(f"prefix native debugger error: {text}")
    try:
        parse_prefix_markers(console)
    except EvidenceFailure as error:
        errors.append(str(error))
    return errors


def analyse_prefix(records: Sequence[Mapping[str, Any]], route: Mapping[str, Any]) -> dict[str, Any]:
    """Analyse observed timing, separately from transport/state proof and budgets.

    No physical cadence is padded or presumed. The 2,193-frame ceiling is only
    an emergency diagnostic stop; ordinary and reset budgets remain unchanged.
    """
    errors = prefix_input_errors(records)
    budget_errors: list[str] = []
    phases = {name: _by_phase(records, name) for name in PREFIX_MARKER_FIELDS}
    if len(records) > PREFIX_MAX_RECORDS or any(item["phase"] not in PREFIX_MARKER_FIELDS for item in records):
        errors.append("prefix record type/count bound mismatch")
    cycle_records = [item for item in records if "cycle" in item]
    for first, last in zip(cycle_records, cycle_records[1:]):
        if last["cycle"] < first["cycle"] or (last["cycle"] == first["cycle"] and (first["phase"], last["phase"]) != ("MS", "HI")):
            errors.append("prefix raw master clocks not monotonic/no-wrap")
            break
    if cycle_records and not 0 <= cycle_records[0]["cycle"] < cycle_records[-1]["cycle"] <= 0xFFFFFFFF:
        errors.append("prefix master clock bounds invalid")
    positions = {id(item): index for index, item in enumerate(records)}

    def ordered(items: Sequence[Mapping[str, Any]], label: str, *, contiguous: bool = False) -> None:
        indices = [positions[id(item)] for item in items]
        expected = list(range(indices[0], indices[-1] + 1)) if contiguous and indices else sorted(set(indices))
        if indices != expected:
            errors.append(f"prefix {label} marker order mismatch")

    def unique(phase: str, attempt: int, tick: int) -> Mapping[str, Any] | None:
        found = [item for item in phases[phase] if (item.get("attempt"), item.get("tick")) == (attempt, tick)]
        if len(found) != 1:
            errors.append(f"prefix {phase} multiplicity at {attempt}/{tick}")
            return None
        return found[0]

    def span(first: Mapping[str, Any], last: Mapping[str, Any], label: str) -> dict[str, Any]:
        try:
            return _span(first, last, label)
        except EvidenceFailure as error:
            errors.append(str(error))
            return {}

    def identity_coverage(phase: str, identities: Sequence[tuple[int, int]]) -> None:
        if [(item.get("attempt"), item.get("tick")) for item in phases[phase]] != list(identities):
            errors.append(f"prefix {phase} exact identity coverage mismatch")

    stages = prefix_input_stages()
    if len(phases["HI"]) != len(stages):
        errors.append("prefix input handoff count mismatch")
    for item, stage in zip(phases["HI"], stages):
        pre = unique("MS", stage["attempt"], stage["pre_tick"])
        if [item[key] for key in ("attempt", "tick", "current", "desired")] != [stage[key] for key in ("attempt", "pre_tick", "current", "desired")]:
            errors.append("prefix input handoff values mismatch")
        if pre and (any(pre[key] != item[key] for key in ("cycle", "frame", "attempt", "tick")) or positions[id(item)] != positions[id(pre)] + 1):
            errors.append("prefix input handoff not adjacent to identical PRE")

    reset_proofs = []
    for phase in ("MR", "BC", "BM", "BZ", "BW"):
        identity_coverage(phase, ((1, 0), (2, 0)))
    if len(phases["RB"]) != 640:
        errors.append("prefix reset replay rows must cover exactly two 5,120-byte snapshots")
    for attempt in (1, 2):
        local = {phase: unique(phase, attempt, 0) for phase in ("MR", "BC", "BM", "BZ", "BW")}
        rows = [item for item in phases["RB"] if (item["attempt"], item["tick"]) == (attempt, 0)]
        reference = route["reset_snapshots"][str(attempt)]
        mr = local["MR"]
        payload = b""
        if [item["address"] for item in rows] != list(range(0xFF0000, 0xFF1400, 16)):
            errors.append(f"prefix reset {attempt} replay byte coverage mismatch")
        else:
            payload = b"".join(item[f"long{index}"].to_bytes(4, "big") for item in rows for index in range(4))
            if _sha256_bytes(payload) != reference["replay_timeline"]["sha256"]:
                errors.append(f"prefix reset {attempt} replay/timeline differs from pinned natural reset")
        if mr:
            if (mr["result"] & 0xFFFF, mr["checkpoint_count"], mr["state_crc"], mr["replay_crc"]) != (0, 1, reference["engine"]["state_crc"], reference["gameplay"]["last_bank_crc"]):
                errors.append(f"prefix reset {attempt} published projection/replay mirrors mismatch")
            if any(item["frame"] != mr["frame"] for item in rows):
                errors.append(f"prefix reset {attempt} replay snapshot frame mismatch")
            ordered((mr, *rows), "reset replay", contiguous=True)
        if all(item is not None for item in local.values()):
            mr, bc, bm, bz, bw = (local[phase] for phase in ("MR", "BC", "BM", "BZ", "BW"))
            ordered((mr, bc, bm, bz, bw), "reset barriers")
            if (bc["snapshot_bank"], bc["current_bank"]) != ((6, 0) if attempt == 1 else (0, 1)):
                errors.append("prefix reset bank epoch mismatch")
            if bc["sr"] & 0x700 != 0x500 or any(item["sr"] & 0x700 != 0x700 for item in (bm, bz, bw)):
                errors.append("prefix reset IRQ masks mismatch")
            if bc["token"] != bm["token"] or bz["token"] != 0 or bw["token"] != 0 or len({item["frame"] for item in (bc, bm, bz, bw)}) != 1:
                errors.append("prefix reset token coalescing/frame mismatch")
            if not 1 <= bc["token"] <= 2:
                budget_errors.append(f"reset {attempt} service tokens outside one/two-frame budget")
            first, fresh = unique("MS", attempt - 1, 719), unique("MS", attempt, 0)
            terminal = unique("ME", attempt - 1, 720)
            if first and terminal and fresh:
                ordered((terminal, mr, *rows, bc, bm, bz, bw, fresh), "terminal/reset/fresh transition", contiguous=True)
                if not terminal["frame"] <= mr["frame"] <= bc["frame"] < fresh["frame"]:
                    errors.append("prefix terminal/reset service physical frame order mismatch")
                duration = span(first, bc, f"reset {attempt} total main-side work")
                if duration.get("delta_cpu_cycles_ceil", 0) > 256010:
                    budget_errors.append(f"reset {attempt} exceeds two-frame main-side cycle budget")
                if fresh["frame"] != bw["frame"] + 1:
                    budget_errors.append(f"reset {attempt} did not resume on exactly the first fresh VBlank")
                if bc["frame"] - first["frame"] != bc["token"]:
                    budget_errors.append(f"reset {attempt} service IRQ delta differs from coalesced tokens")
                reset_proofs.append({"attempt": attempt, "service_tokens": bc["token"], "pre_frame": first["frame"], "coalesce_frame": bc["frame"], "fresh_frame": fresh["frame"], "main_side": duration, "replay_sha256": _sha256_bytes(payload)})

    render_ids = [(2, tick) for tick in PREFIX_SAMPLE_POST_TICKS]
    for pre, post in zip(phases["MS"], phases["ME"]):
        if post["cycle"] <= pre["cycle"] or post["frame"] < pre["frame"]:
            errors.append("prefix paired PRE/POST cycle or physical frame ordering mismatch")
        elif post["frame"] != pre["frame"]:
            budget_errors.append(f"prefix gameplay crossed VBlank at {post['attempt']}/{post['tick']}")
    for phase in ("ME", "MS"):
        for first, last in zip(phases[phase], phases[phase][1:]):
            if last["frame"] < first["frame"]:
                errors.append("prefix physical frame counters run backwards")
            elif first["attempt"] == last["attempt"] and last["frame"] != first["frame"] + 1:
                budget_errors.append(f"prefix {phase} ordinary physical cadence drift at {last['attempt']}/{last['tick']}")
    for phase in ("RM", "RG", "RR", "RX", "MD"):
        identity_coverage(phase, render_ids)
    identity_coverage("MB", [(2, tick - 1) for tick in PREFIX_SAMPLE_POST_TICKS])
    for phase in ("PS", "ES", "EE", "TS", "TE", "CS", "CE", "PE"):
        identity_coverage(phase, [(2, tick) for tick in (60, 120, 180, 240)])
    rendered_ids = [(item["attempt"], item["tick"]) for item in phases["RR"] if item["result"] & 0xFFFF in (0, 3)]
    for phase in ("HB", "HE", "SE", "OE"):
        identity_coverage(phase, rendered_ids)
    for phase, mask in (("MB", 0x700), ("MD", 0x500), ("RM", 0x500), ("RX", 0x500), ("RG", 0x700), ("RR", 0x700), ("HB", 0x700), ("HE", 0x700), ("SE", 0x700), ("OE", 0x700)):
        if any(item["sr"] & 0x700 != mask for item in phases[phase]):
            errors.append(f"prefix {phase} IRQ mask mismatch")
        if any(item["token"] != 0 for item in phases[phase]):
            budget_errors.append(f"prefix sampled {phase} has nonzero queued frame token")
    samples = {}
    for tick in PREFIX_SAMPLE_POST_TICKS:
        names = ("MS", "MB", "ME", "RM", "RG", "RR", "RX", "MD")
        local = {phase: unique(phase, 2, tick - int(phase in {"MS", "MB"})) for phase in names}
        if any(item is None for item in local.values()):
            continue
        ordered([local[phase] for phase in ("MB", "MS", "ME", "RM", "RG", "RR", "RX", "MD")], f"tick {tick} main")
        sample = {"frame_pre": local["MS"]["frame"], "frame_post": local["ME"]["frame"], "actor_count": local["ME"]["actor_count"], "projectile_count": local["ME"]["projectile_count"]}
        for label, first, last in (("gameplay", "MS", "ME"), ("main", "MB", "MD"), ("renderer_masked", "RM", "RR")):
            sample[label] = span(local[first], local[last], f"tick {tick} {label}")
        if sample["gameplay"].get("delta_cpu_cycles_ceil", 0) > 83203:
            budget_errors.append(f"attempt2 tick {tick} GameplayStep exceeds 83203 CPU cycles")
        if sample["main"].get("delta_cpu_cycles_ceil", 0) > 128005:
            budget_errors.append(f"attempt2 tick {tick} whole main exceeds one-frame cycle budget")
        if str(tick) in route.get("projectile_counts", {}) and sample["projectile_count"] != route["projectile_counts"][str(tick)]:
            errors.append(f"attempt2 tick {tick} projectile count differs from pinned route")
        rr, rg = local["RR"], local["RG"]
        result = rr["result"] & 0xFFFF
        if result not in range(5):
            errors.append("prefix renderer returned unknown result")
        if result != (4 if tick % 60 == 0 else 0) or rr["backlog_frames"]:
            budget_errors.append(f"attempt2 tick {tick} renderer hold/recovery/backlog/rejection mismatch")
        if (rg["pending"], rg["published_depth"], rg["build_depth"], rg["backlog_frames"]) != (0, 0, 0, 0):
            budget_errors.append(f"attempt2 tick {tick} pre-render queue/backlog not empty")
        if (rg["campaign_flags"], rg["checkpoint_count"], rg["renderer_attempt"], rg["renderer_checkpoint_count"]) != (3, 1 + tick // 60, 2, 1 + (tick - 1) // 60):
            errors.append("prefix pre-render campaign/checkpoint/attempt mirrors mismatch")
        if rr["pending"] != int(result == 0):
            errors.append("prefix renderer result/publication mismatch")
        if any(item["frame"] != local["RM"]["frame"] for item in (rg, rr)):
            errors.append("prefix masked renderer physical frame changed")
        if rr["reset_phase"] != 0 or rr["renderer_attempt"] != 2 or (rr["renderer_checkpoint_count"], rr["checkpoint_count"]) != (1 + tick // 60, 1 + tick // 60):
            errors.append("prefix renderer publication mirrors/reset phase mismatch")
        if result == 4 and (tick % 60 != 0 or rr["pending"] != 0):
            errors.append("prefix checkpoint hold result outside checkpoint/zero-publication")
        if result in (0, 3):
            drawing = [unique(phase, 2, tick) for phase in ("HB", "HE", "SE", "OE")]
            if all(item is not None for item in drawing):
                ordered((rg, *drawing, rr), f"tick {tick} renderer")
                for label, first, last in (("hud", drawing[0], drawing[1]), ("sat", drawing[1], drawing[2]), ("overlay", drawing[2], drawing[3])):
                    sample[label] = span(first, last, f"tick {tick} {label}")
        if tick % 60 == 0:
            project = [unique(phase, 2, tick) for phase in ("PS", "ES", "EE", "TS", "TE", "CS", "CE", "PE")]
            if all(item is not None for item in project):
                ps, es, ee, ts, te, cs, ce, pe = project
                ordered((local["MS"], *project, local["ME"]), f"tick {tick} projection")
                if (ps["checkpoint_count"], es["checkpoint_count"], ee["checkpoint_count"], pe["checkpoint_count"], ps["engine_state"], ee["length"] & 0xFFFF) != (tick // 60, tick // 60, 1 + tick // 60, 1 + tick // 60, 1, 3638):
                    errors.append("prefix projection checkpoint progression/state/base length mismatch")
                if (pe["length"] & 0xFFFF, pe["crc"], pe["state_crc"]) != (3894, ce["raw_crc"] ^ 0xFFFFFFFF, ce["raw_crc"] ^ 0xFFFFFFFF):
                    errors.append("prefix projection length/CRC publication mismatch")
                if (cs["source"], cs["length"] & 0xFFFF, te["length"] & 0xFFFF) != (0xFF6E36, 256, 256) or ts["destination"] != cs["source"] or te["destination"] != cs["source"] + 256:
                    errors.append("prefix projection tail boundaries mismatch")
                for label, first, last in (("projection", ps, pe), ("engine_projection", es, ee), ("tail", ts, te), ("tail_crc", cs, ce)):
                    sample[label] = span(first, last, f"tick {tick} {label}")
                sample["projection_crc"] = pe["state_crc"]
        samples[str(tick)] = sample

    irq_summary = _analyse_prefix_irqs(records, phases, errors, budget_errors)
    return {"status": "pass" if not errors and not budget_errors else "fail",
            "protocol_status": "pass" if not errors else "fail", "budget_status": "pass" if not budget_errors else "fail",
            "errors": errors, "budget_errors": budget_errors,
            "marker_counts": {phase: len(items) for phase, items in phases.items()},
            "phase_spans": samples, "natural_resets": reset_proofs, "sampled_irqs": irq_summary,
            "irq_observation_coverage": prefix_irq_coverage(records),
            "observed_renderer_consumption": [
                {"attempt": renderer["attempt"], "tick": renderer["tick"], "pending": renderer["pending"],
                 "first_later_commit": next(({key: commit[key] for key in ("cycle", "frame", "attempt", "tick", "pending")}
                                             for commit in phases["VC"] if commit["cycle"] > renderer["cycle"]), None)}
                for renderer in phases["RR"]],
            "timing_scope": "Bounded natural 1,681-step route with strict full input proof; 26 later-attempt phase samples and four checkpoint projections. Raw /7 spans include intervening IRQs. Physical 2,193 is a diagnostic fail-stop, never an allowance. Not the full M5 cycle gate.",
            "protocol_domain_limitations": ["An empty-token sampled MD requires an observed same-identity WAIT. The rare IRQ between MD and MainLoop token test can legitimately omit WAIT, but is unsupported here and remains protocol-fail with raw evidence preserved; no inferred/missing WAIT is accepted."],
            "conclusion": "Natural canonical-route prefix observed; protocol " + ("failed" if errors else "passed") + "; sampled cycle/blank budget " + ("failed" if budget_errors else "passed") + ". Not a full M5 pass."}


def prefix_irq_coverage(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive IRQ coverage from the engine identity active during each interval.

    A lower-bound ME can already include an IRQ taken while the preceding
    tick was outside the logging gate. Conversely the final in-gate MS can
    cross an IRQ before its ME advances outside that gate. Neither boundary
    permits inferring unobserved IRQ markers from an absolute PRE frame.
    """
    engine = [item for item in records if item["phase"] in {"MS", "ME"}]
    intervals = []
    required: set[int] = set()
    for first, last in zip(engine, engine[1:]):
        if first["attempt"] != 2 or not any(lower <= first["tick"] < upper for lower, upper in PREFIX_IRQ_TICK_RANGES):
            continue
        frames = list(range(first["frame"] + 1, last["frame"] + 1))
        if frames:
            required.update(frames)
            fields = ("phase", "cycle", "frame", "attempt", "tick")
            intervals.append({"first": {key: first[key] for key in fields},
                              "last": {key: last[key] for key in fields}, "required_frames": frames})
    observed = [item["frame"] for item in records if item["phase"] == "VX"]
    return {"rule": "Every physical counter increment after an in-gate MS/ME and through the next engine observation requires a complete logged IRQ; the first lower-bound ME frame is already counted, not itself evidence of an observed IRQ.",
            "required_frames": sorted(required), "observed_frames": observed,
            "missing_frames": sorted(required - set(observed)),
            "unanchored_frames": sorted(set(observed) - required), "counter_intervals": intervals}


def _analyse_prefix_irqs(records: Sequence[Mapping[str, Any]], phases: Mapping[str, Sequence[Mapping[str, Any]]],
                         errors: list[str], budget_errors: list[str]) -> list[dict[str, Any]]:
    def inside(item: Mapping[str, Any]) -> bool:
        return item["attempt"] == 2 and any(first <= item["tick"] < last for first, last in PREFIX_IRQ_TICK_RANGES)

    frames = [item["frame"] for item in phases["VX"]]
    if frames != sorted(set(frames)) or not 1 <= len(frames) <= PREFIX_PHYSICAL_FAIL_STOP - 1498 + 1 or any(not 1 <= frame < PREFIX_PHYSICAL_FAIL_STOP for frame in frames):
        errors.append("prefix IRQ physical coverage/count bound mismatch")
    coverage = prefix_irq_coverage(records)
    if coverage["missing_frames"]:
        errors.append("prefix sampled engine counters require missing IRQ(s)")
    if coverage["unanchored_frames"]:
        errors.append("prefix logged IRQ lacks an in-gate engine counter interval")
    for lower, upper in PREFIX_IRQ_TICK_RANGES:
        first = next((item for item in records if item["phase"] == "ME" and (item["attempt"], item["tick"]) == (2, lower)), None)
        last = next((item for item in records if item["phase"] == "MS" and (item["attempt"], item["tick"]) == (2, upper - 1)), None)
        if last is None and upper > PREFIX_FINAL_TICK:
            last = next((item for item in records if item["phase"] == "MD" and (item["attempt"], item["tick"]) == (2, PREFIX_FINAL_TICK)), None)
        if first is None or last is None:
            errors.append("prefix complete IRQ window missing engine boundary")
            continue
        window = records[records.index(first):records.index(last) + 1]
        for before, after in zip(window, window[1:]):
            expected_delta = int(after["phase"] == "VC")
            if after["frame"] != before["frame"] + expected_delta:
                errors.append("prefix logged window frame counter changes outside exact VA/VC edge")
                break
    for phase in ("VA", "VE", "VC", "VP", "VS", "VK"):
        if [item["frame"] + int(phase in {"VE", "VA"}) for item in phases[phase]] != frames:
            errors.append(f"prefix {phase} exact IRQ coverage mismatch")
    queue4 = ((0xFF8000, 0xF000, 320, 0x8000, 0), (0xFF8600, 0xC000, 40, 0x8001, 0), (0xFF8650, 0xC080, 40, 0x8001, 0), (0xFF86A0, 0xC100, 40, 0x8001, 0))
    positions = {id(item): index for index, item in enumerate(records)}
    summaries = []
    # Only observed RR records enter this ledger. Unsampled boundary renders
    # cannot be invented; their queues still receive the full exact DMA audit.
    first_commits = {id(renderer): next((commit for commit in phases["VC"] if commit["cycle"] > renderer["cycle"]), None)
                     for renderer in phases["RR"]}
    previous_counter = 0
    covered = set()
    irq_names = {"VE", "VA", "VC", "Q", "DS", "DE", "VP", "VS", "VO", "VK", "VX"}
    for frame in frames:
        local = {name: [item for item in phases[name] if item["frame"] + int(name in {"VE", "VA"}) == frame] for name in irq_names}
        singles = ("VE", "VA", "VC", "VP", "VS", "VK", "VX")
        if any(len(local[name]) != 1 for name in singles):
            continue
        ve, va, vc, vp, vs, vk, vx = (local[name][0] for name in singles)
        if not inside(ve) or (ve["attempt"], ve["tick"]) != (vc["attempt"], vc["tick"]) or (vc["attempt"], vc["tick"]) != (vx["attempt"], vx["tick"]):
            errors.append(f"prefix IRQ {frame} engine identity/gate mismatch")
        entry = va["entry_status"] & 8
        increment = int(bool(entry) and not (vs["exit_status"] & 8))
        if vs["entry_status"] != va["entry_status"] or (vk["d0"] & 0xFFFF, vk["d1"] & 0xFFFF) != (vs["exit_status"] & (8 if entry else 0xFFFF), entry):
            errors.append(f"prefix IRQ {frame} raw blank predicates mismatch")
        if vs["counter"] < previous_counter or vk["counter"] != min(0xFFFF, vs["counter"] + increment) or vx["counter"] != vk["counter"]:
            errors.append(f"prefix IRQ {frame} cumulative counter mismatch")
        previous_counter = vx["counter"]
        if va["sequence"] != frame * 2 - 1 or vx["sequence"] != frame * 2 or ve["token"] + 1 != vx["token"] or ve["sr"] & 0x700 != 0x600:
            errors.append(f"prefix IRQ {frame} sequence/token/mask mismatch")
        if (ve["token"], vx["token"]) != (0, 1):
            budget_errors.append(f"prefix IRQ {frame} queued/backlogged frame tokens")
        if len(local["VO"]) != increment or any((item["d0"], item["d1"], item["counter"], item["sequence"]) != (0, 8, vs["counter"], frame * 2 - 1) for item in local["VO"]):
            errors.append(f"prefix IRQ {frame} overrun origin mismatch")
        if not entry or not (vs["exit_status"] & 8) or vx["counter"]:
            budget_errors.append(f"prefix IRQ {frame} late/outside blank or cumulative overrun")
        expected_queue = queue4 if vc["published_depth"] else ()
        words = sum(item[2] for item in expected_queue)
        if (vc["pending"], vc["published_depth"], vc["build_depth"], vc["build_words"]) != (int(bool(expected_queue)), len(expected_queue), len(expected_queue), words):
            errors.append(f"prefix IRQ {frame} exact bounded queue topology mismatch")
        if _record_values(local["Q"], "index", "source", "destination", "words", "flags", "age") != [[index, *item] for index, item in enumerate(expected_queue)]:
            errors.append(f"prefix IRQ {frame} queue bytes mismatch")
        if len(local["DS"]) != len(expected_queue) or len(local["DE"]) != len(expected_queue):
            errors.append(f"prefix IRQ {frame} DMA coverage mismatch")
        cumulative = 0
        for start, end, item in zip(local["DS"], local["DE"], expected_queue):
            cumulative += item[2]
            if [start[key] for key in ("source", "destination", "words", "commit_words")] != [*item[:3], cumulative] or end["commit_words"] != cumulative or end["cycle"] <= start["cycle"]:
                errors.append(f"prefix IRQ {frame} DMA issue mismatch")
        if (vp["commit_words"], vp["debug_words"], vp["depth"], vp["pending"], vx["debug_words"]) != (words, words, 0, 0, words):
            errors.append(f"prefix IRQ {frame} commit accounting mismatch")
        fresh_renderer = [item for item in phases["RR"] if first_commits[id(item)] is vc]
        prior_renderer = [item for item in phases["RR"] if (item["attempt"], item["tick"]) == (vc["attempt"], vc["tick"]) and item["cycle"] < ve["cycle"]]
        publication = "unsampled_renderer_identity"
        if fresh_renderer:
            # The engine may advance before the queue commits (RR181 to
            # VC1634/tick182 in the preserved capture); bind by event order.
            publication = "first_commit_of_sampled_renderer"
            if any(vc["pending"] != item["pending"] for item in fresh_renderer):
                errors.append(f"prefix IRQ {frame} does not consume the sampled renderer publication exactly once")
        elif prior_renderer:
            publication = "already_consumed_exact_empty"
            if vc["pending"] != 0:
                errors.append(f"prefix IRQ {frame} does not consume the sampled renderer publication exactly once")
        expected_order = [ve, va, vc, *local["Q"]]
        for start, end in zip(local["DS"], local["DE"]):
            expected_order.extend((start, end))
        expected_order.extend((vp, vs, *local["VO"], vk, vx))
        indices = [positions[id(item)] for item in expected_order]
        covered.update(indices)
        if indices != list(range(indices[0], indices[-1] + 1)):
            errors.append(f"prefix IRQ {frame} contiguous phase grammar mismatch")
        try:
            summaries.append({"frame": frame, "attempt": vc["attempt"], "tick": vc["tick"], "queue_depth": len(expected_queue), "words": words, "renderer_publication_binding": publication, "entry_to_rte": _span(ve, vx, f"prefix IRQ {frame}")})
        except EvidenceFailure as error:
            errors.append(str(error))
    if covered != {index for index, item in enumerate(records) if item["phase"] in irq_names}:
        errors.append("prefix orphan/duplicate IRQ marker")
    wait_ids = [(item["frame"], item["attempt"], item["tick"]) for item in phases["WW"]]
    if len(wait_ids) != len(set(wait_ids)) or len(wait_ids) > PREFIX_PHYSICAL_FAIL_STOP - 1498 + 1:
        errors.append("prefix WAIT duplicate identity/count bound mismatch")
    for wait in phases["WW"]:
        following = next((item for item in phases["VE"] if item["cycle"] > wait["cycle"]), None)
        previous = [item for item in phases["ME"] if (item["attempt"], item["tick"]) == (wait["attempt"], wait["tick"]) and item["cycle"] < wait["cycle"]]
        if not inside(wait) or wait["token"] != 0 or wait["sr"] & 0x700 != 0x700 or len(previous) != 1 or following is None or any(wait[key] != following[key] for key in ("frame", "attempt", "tick")):
            errors.append("prefix WAIT identity/mask/token/IRQ binding mismatch")
        matching_done = [item for item in phases["MD"] if all(item[key] == wait[key] for key in ("frame", "attempt", "tick")) and item["cycle"] < wait["cycle"]]
        if any(item["token"] != 0 for item in matching_done):
            errors.append("prefix nonempty-token MD cannot reach same-frame/identity WAIT")
    for done in phases["MD"]:
        if done["tick"] == PREFIX_FINAL_TICK or done["token"]:
            continue
        waits = [item for item in phases["WW"] if all(item[key] == done[key] for key in ("frame", "attempt", "tick")) and item["cycle"] > done["cycle"]]
        if len(waits) != 1:
            errors.append("prefix sampled empty-token main missing/duplicate WAIT")
    return summaries


def command_policy_errors(
    script: str, symbols: Mapping[str, int], *, extended_overlay: bool = False, profile: str = "baseline"
) -> list[str]:
    errors: list[str] = []
    if script != build_command_script(symbols, extended_overlay=extended_overlay, profile=profile):
        errors.append("command script is not the exact generated script")
    continues = 0
    for line_number, raw in enumerate(script.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        command = stripped.split(maxsplit=1)[0].lower()
        if command not in ALLOWED_COMMANDS:
            errors.append(
                f"line {line_number}: command {command!r} is not allowed"
            )
        if command == "continue":
            continues += 1
    if continues > 128:
        errors.append(f"continue command bound exceeded: {continues}/128")
    lowered = f" {script.lower()} "
    for token in (" set ", " write ", " x ", " display "):
        if token in lowered:
            errors.append(f"forbidden debugger token appears: {token.strip()}")
    return errors


def offline_capture_command_policy_errors(script: str) -> list[str]:
    """Audit the frozen capture script without consulting mutable build output."""
    errors: list[str] = []
    if _sha256_bytes(script.encode("utf-8")) != CAPTURE_COMMAND_SCRIPT_SHA256:
        errors.append("capture command script hash mismatch")
    expected_breakpoints = [
        f"breakpoint ${address:X}" for address in CAPTURE_PC.values()
    ]
    actual_breakpoints = [
        line.strip()
        for line in script.splitlines()
        if line.strip().startswith("breakpoint ")
    ]
    if actual_breakpoints != expected_breakpoints:
        errors.append("capture breakpoint sequence mismatch")
    continues = 0
    for line_number, raw in enumerate(script.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        command = stripped.split(maxsplit=1)[0].lower()
        if command not in ALLOWED_COMMANDS:
            errors.append(
                f"line {line_number}: command {command!r} is not allowed"
            )
        if command == "continue":
            continues += 1
    if continues > 128:
        errors.append(f"continue command bound exceeded: {continues}/128")
    lowered = f" {script.lower()} "
    for token in (" set ", " write ", " x ", " display "):
        if token in lowered:
            errors.append(f"forbidden debugger token appears: {token.strip()}")
    return errors


def parse_markers(
    console_text: str, *, extended_overlay: bool = False
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    marker_fields = {**MARKER_FIELDS, **EXTENDED_MARKER_FIELDS} if extended_overlay else MARKER_FIELDS
    for line in console_text.splitlines():
        match = MARKER_RE.fullmatch(line.strip())
        if not match:
            continue
        phase = match.group("phase")
        fields = marker_fields.get(phase)
        if fields is None:
            raise EvidenceFailure(f"unknown marker phase {phase!r}")
        values = match.group("values").split()
        if len(values) != len(fields):
            raise EvidenceFailure(
                f"{phase} marker has {len(values)} values; "
                f"{len(fields)} required"
            )
        record = {"phase": phase}
        record.update(zip(fields, (int(value, 16) for value in values)))
        records.append(record)
    return records


def console_contract_errors(
    console_text: str,
    script: str,
    *,
    renderer_end_pc: int | None = None,
    extended_overlay: bool = False,
) -> list[str]:
    errors: list[str] = []
    marker_fields = {**MARKER_FIELDS, **EXTENDED_MARKER_FIELDS} if extended_overlay else MARKER_FIELDS
    if renderer_end_pc is None:
        renderer_end_pc = PC["renderer_end"]
    renderer_end_prefix = f"{renderer_end_pc:X}:"
    lines = [line.strip() for line in console_text.splitlines() if line.strip()]
    lifecycle = (READY_MARKER, *(item[0] for item in INPUT_MARKERS), COMPLETE_MARKER)
    for marker in lifecycle:
        if lines.count(marker) != 1:
            errors.append(f"console marker count mismatch for {marker!r}")
    if all(marker in lines for marker in lifecycle):
        positions = [lines.index(marker) for marker in lifecycle]
        if positions != sorted(positions):
            errors.append("console lifecycle marker order mismatch")
        expected_stops = {
            READY_MARKER: ("68K Breakpoint 21 hit", "169A:", "> continue"),
            INPUT_MARKERS[0][0]: (
                "68K Breakpoint 17 hit",
                renderer_end_prefix,
                "> continue",
            ),
            INPUT_MARKERS[1][0]: (
                "68K Breakpoint 17 hit",
                renderer_end_prefix,
                "> continue",
            ),
            INPUT_MARKERS[2][0]: (
                "68K Breakpoint 0 hit",
                "2FA:",
                "> continue",
            ),
            COMPLETE_MARKER: ("68K Breakpoint 27 hit", "1850:", "> quit"),
        }
        if extended_overlay:
            expected_stops[COMPLETE_MARKER] = ("68K Breakpoint 29 hit", "344:", "> quit")
        for marker, position in zip(lifecycle, positions):
            expected = expected_stops[marker]
            actual = lines[position + 1 : position + 4]
            if (
                len(actual) != 3
                or actual[0] != expected[0]
                or not actual[1].startswith(expected[1])
                or actual[2] != expected[2]
            ):
                errors.append(f"console stop lifecycle mismatch for {marker!r}")
    if lines.count("> continue") != 5:
        errors.append("console must contain script plus four staged continues")
    if lines.count("> quit") != 1:
        errors.append("console must contain one final quit")
    breakpoint_lines = [
        line for line in script.splitlines() if line.startswith("breakpoint ")
    ]
    for index, command in enumerate(breakpoint_lines):
        echo = f"> {command}"
        ack = f"68K Breakpoint {index} set at ${command.split('$', 1)[1]}"
        if lines.count(echo) != 1 or lines.count(ack) != 1:
            errors.append(f"breakpoint lifecycle mismatch: {command}")
        elif lines.index(ack) != lines.index(echo) + 1:
            errors.append(f"breakpoint acknowledgement is not adjacent: {command}")
    for pattern in (
        "Failed to eval",
        "Unrecognized",
        "not a recognized command",
        "fgets failed",
    ):
        if any(pattern in line for line in lines):
            errors.append(f"native debugger error present: {pattern}")
    literals = set(lifecycle)
    for line in (line for line in lines if line.startswith("R94R ")):
        if line in literals:
            continue
        match = MARKER_RE.fullmatch(line)
        if not match or match.group("phase") not in marker_fields:
            errors.append(f"unknown R94R console line: {line}")
            continue
        if len(match.group("values").split()) != len(
            marker_fields[match.group("phase")]
        ):
            errors.append(f"malformed R94R console line: {line}")
    return errors


def _by_phase(
    records: Sequence[Mapping[str, Any]], phase: str
) -> list[Mapping[str, Any]]:
    return [record for record in records if record["phase"] == phase]


def _span(
    start: Mapping[str, Any], end: Mapping[str, Any], label: str
) -> dict[str, Any]:
    first = int(start["cycle"])
    last = int(end["cycle"])
    if not 0 <= first < last <= 0xFFFFFFFF:
        raise EvidenceFailure(f"{label} cycle span is not positive/no-wrap")
    delta = last - first
    return {
        "start_cycle_master": first,
        "end_cycle_master": last,
        "delta_master_clocks": delta,
        "delta_cpu_cycles_ceil": (
            delta + MASTER_CLOCKS_PER_M68K_CYCLE - 1
        )
        // MASTER_CLOCKS_PER_M68K_CYCLE,
        "master_remainder_mod_7": delta % MASTER_CLOCKS_PER_M68K_CYCLE,
    }


def _record_values(
    records: Sequence[Mapping[str, Any]], *fields: str
) -> list[list[int]]:
    return [[int(record[field]) for field in fields] for record in records]


def analyse(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Admit only exact one-token reset cadence; retain observed fault facts."""
    errors: list[str] = []
    phases = {phase: _by_phase(records, phase) for phase in MARKER_FIELDS}
    expected_counts = {phase: 0 for phase in MARKER_FIELDS}
    expected_counts.update(
        {
            "HI": 3,
            "MS": 3,
            "ME": 3,
            "MR": 1,
            "PS": 2,
            "PE": 2,
            "ES": 2,
            "EE": 2,
            "TS": 2,
            "TE": 2,
            "CS": 2,
            "CE": 2,
            "RG": 3,
            "RR": 3,
            "BC": 1,
            "BM": 1,
            "BZ": 1,
            "BW": 1,
            "VE": 6,
            "VA": 6,
            "VC": 6,
            "Q": 13,
            "DS": 13,
            "DE": 13,
            "VP": 6,
            "VS": 6,
            "VK": 6,
            "VX": 6,
        }
    )
    for phase, expected in expected_counts.items():
        if len(phases[phase]) != expected:
            errors.append(
                f"{phase} marker count {len(phases[phase])} != {expected}"
            )
    cycle_records = [record for record in records if "cycle" in record]
    admitted_equal = {("MS", "HI"), ("RR", "HI")}
    for earlier, later in zip(cycle_records, cycle_records[1:]):
        before, after = int(earlier["cycle"]), int(later["cycle"])
        if after < before or (
            after == before
            and (earlier["phase"], later["phase"]) not in admitted_equal
        ):
            errors.append("raw marker cycles/order are not monotonic")
            break

    hi = phases["HI"]
    if len(hi) == 3 and _record_values(
        hi, "frame", "attempt", "tick", "current", "desired"
    ) != [[46, 0, 46, 1, 8], [67, 0, 67, 8, 0], [720, 0, 719, 0, 2]]:
        errors.append("canonical prefix/reset host-input stops mismatch")

    ms, me = phases["MS"], phases["ME"]
    if len(ms) == 3:
        if _record_values(ms, "frame", "attempt", "tick") != [
            [719, 0, 718],
            [720, 0, 719],
            [723, 1, 0],
        ]:
            errors.append("gameplay PRE cadence is not exact totals 719..721")
        if _record_values(ms, "input", "d0") != [
            [0, 0],
            [0, 0],
            [b03.INPUT_DOWN, b03.INPUT_DOWN],
        ]:
            errors.append("gameplay PRE canonical input/d0 binding mismatch")
        if [item["checkpoint_count"] for item in ms] != [12, 12, 1]:
            errors.append("gameplay PRE checkpoint topology mismatch")
    if len(me) == 3:
        if _record_values(me, "frame", "attempt", "tick") != [
            [719, 0, 719],
            [720, 0, 720],
            [723, 1, 1],
        ]:
            errors.append("gameplay POST cadence is not exact totals 719..721")
        if [item["result"] & 0xFFFF for item in me] != [0, 8, 0]:
            errors.append("gameplay result topology is not OK/TIMEOUT/OK")

    projection_spans: list[dict[str, Any]] = []
    projection_phases = [
        phases[name] for name in ("PS", "ES", "EE", "TS", "TE", "CS", "CE", "PE")
    ]
    if all(len(items) == 2 for items in projection_phases):
        ps, es, ee, ts, te, cs, ce, pe = projection_phases
        identities = [[0, 720], [1, 0]]
        for index, identity in enumerate(identities):
            local = [items[index] for items in projection_phases]
            if [[item["attempt"], item["tick"]] for item in local] != [identity] * 8:
                errors.append(f"projection {index} attempt/tick binding mismatch")
            positions = [records.index(item) for item in local]
            if positions != sorted(positions):
                errors.append(f"projection {index} raw nesting mismatch")
            if (es[index]["checkpoint_count"], ee[index]["checkpoint_count"], ps[index]["checkpoint_count"], pe[index]["checkpoint_count"]) != (
                (12, 13, 12, 13) if index == 0 else (0, 1, 0, 1)
            ):
                errors.append(f"projection {index} checkpoint transition mismatch")
            if ee[index]["length"] & 0xFFFF != 0x0E36 or pe[index]["length"] & 0xFFFF != 0x0F36:
                errors.append(f"projection {index} length mismatch")
            if ts[index]["destination"] != 0x00FF6E36 or te[index]["destination"] != 0x00FF6F36 or te[index]["length"] & 0xFFFF != 0x0100:
                errors.append(f"projection {index} vocabulary-tail copy mismatch")
            if cs[index]["source"] != 0x00FF6E36 or cs[index]["length"] & 0xFFFF != 0x0100:
                errors.append(f"projection {index} raw-tail CRC bounds mismatch")
            if pe[index]["crc"] != ((~ce[index]["raw_crc"]) & 0xFFFFFFFF) or pe[index]["state_crc"] != pe[index]["crc"]:
                errors.append(f"projection {index} CRC/state parity mismatch")
            try:
                projection_spans.append(
                    {
                        "campaign": _span(ps[index], pe[index], f"projection {index}"),
                        "engine": _span(es[index], ee[index], f"engine projection {index}"),
                        "tail": _span(ts[index], te[index], f"tail {index}"),
                        "raw_tail_crc": _span(cs[index], ce[index], f"raw CRC {index}"),
                        "checkpoint_count": pe[index]["checkpoint_count"],
                        "crc": pe[index]["crc"],
                    }
                )
            except EvidenceFailure as error:
                errors.append(str(error))
        if pe[0]["crc"] in {0, pe[1]["crc"]}:
            errors.append("intermediate count-13 projection CRC is not distinct")
        if pe[1]["crc"] != EXPECTED_RESET_PROJECTION_CRC:
            errors.append("reset count-1 projection CRC mismatch")

    if len(phases["MR"]) == 1:
        mr = phases["MR"][0]
        if (
            [mr["attempt"], mr["tick"], mr["result"], mr["checkpoint_count"]]
            != [1, 0, 0, 1]
            or mr["state_crc"] != EXPECTED_RESET_PROJECTION_CRC
            or mr["replay_crc"] != EXPECTED_ATTEMPT_0_REPLAY_CRC
        ):
            errors.append("commit/reset return state or replay CRC mismatch")

    rg, rr = phases["RG"], phases["RR"]
    if len(rg) == len(rr) == 3:
        expected_identity = [[0, 719], [1, 0], [1, 1]]
        if _record_values(rg, "attempt", "tick") != expected_identity or _record_values(rr, "attempt", "tick") != expected_identity:
            errors.append("renderer total-step identity mismatch")
        if [item["frame"] for item in rg] != [719, 722, 723] or [
            item["frame"] for item in rr
        ] != [719, 722, 723]:
            errors.append("renderer reset-barrier frame topology mismatch")
        if any(item["sr"] & 0x0700 != 0x0700 for item in (*rg, *rr)):
            errors.append("renderer boundaries are not mask-7")
        if [item["token"] for item in rg] != [0, 2, 0] or [
            item["token"] for item in rr
        ] != [0, 2, 0]:
            errors.append("renderer pre-clear/fresh-token topology mismatch")
        if [item["pending"] for item in rg] != [0, 0, 0]:
            errors.append("renderer start FRAME_PENDING topology mismatch")
        if any(
            item["published_depth"] != 0
            or item["build_depth"] != 0
            or item["campaign_flags"] & 1 == 0
            for item in rg
        ):
            errors.append("renderer gate did not start empty active-campaign")
        if [item["renderer_attempt"] for item in rg] != [0, 0, 1] or [
            item["renderer_checkpoint_count"] for item in rg
        ] != [12, 12, 1]:
            errors.append("renderer pre-call attempt/checkpoint mirrors mismatch")
        if [item["result"] & 0xFFFF for item in rr] != [0, 4, 0]:
            errors.append("renderer result topology is not OK/HOLD/OK")
        if [item["pending"] for item in rr] != [1, 0, 1]:
            errors.append("renderer publication topology mismatch")
        if [item["backlog_frames"] for item in (*rg, *rr)] != [0] * 6:
            errors.append("renderer backlog counter changed")
        if [item["checkpoint_count"] for item in rg] != [12, 1, 1] or [item["renderer_checkpoint_count"] for item in rr] != [12, 1, 1]:
            errors.append("renderer checkpoint hold topology mismatch")
        if [item["renderer_attempt"] for item in rr] != [0, 1, 1] or [
            item["reset_phase"] for item in rr
        ] != [0, 12, 11]:
            errors.append("renderer reset attempt/phase topology mismatch")

    barriers = [phases[name] for name in ("BC", "BM", "BZ", "BW")]
    if all(len(items) == 1 for items in barriers):
        bc, bm, bz, bw = (items[0] for items in barriers)
        if [records.index(item) for item in (bc, bm, bz, bw)] != sorted(
            records.index(item) for item in (bc, bm, bz, bw)
        ):
            errors.append("reset coalesce barrier raw order mismatch")
        if any(
            [item["frame"], item["attempt"], item["tick"]]
            != [722, 1, 0]
            for item in (bc, bm, bz, bw)
        ):
            errors.append("reset coalesce barrier state binding mismatch")
        if (bc["snapshot_bank"], bc["current_bank"]) != (6, 0):
            errors.append("reset bank-epoch snapshot did not prove transition")
        if [bc["token"], bm["token"], bz["token"], bw["token"]] != [
            2,
            2,
            0,
            0,
        ]:
            errors.append("reset token coalesce 2->0 barrier mismatch")
        if bc["sr"] & 0x0700 != 0x0500 or any(
            item["sr"] & 0x0700 != 0x0700 for item in (bm, bz, bw)
        ):
            errors.append("reset token coalesce interrupt-mask topology mismatch")

    target_frames = list(TARGET_VBLANK_FRAMES)
    if len(phases["VE"]) == 6 and [item["frame"] for item in phases["VE"]] != [718, 719, 720, 721, 722, 723]:
        errors.append("VBlank entry prior-frame labels mismatch")
    for phase in ("VC", "VP", "VS", "VK", "VX"):
        if len(phases[phase]) == 6 and [item["frame"] for item in phases[phase]] != target_frames:
            errors.append(f"{phase} current-frame labels mismatch")
    if phases["VO"]:
        errors.append("reset interval reached VBlank overrun origin")
    if any(item["counter"] != 0 for item in (*phases["VS"], *phases["VK"], *phases["VX"])):
        errors.append("VBlank overrun counter changed")
    ack_by_current_frame = {
        item["frame"] + 1: item for item in phases["VA"]
    }
    for item in phases["VK"]:
        ack = ack_by_current_frame.get(item["frame"])
        if ack and ack["entry_status"] & 8 and (
            item["d0"] & 0xFFFF,
            item["d1"] & 0xFFFF,
        ) != (8, 8):
            errors.append(f"frame {item['frame']} VBlank clean predicate mismatch")
    if len(phases["VX"]) == 6:
        if [item["sequence"] for item in phases["VX"]] != [2 * frame for frame in target_frames]:
            errors.append("VBlank seqlock cadence mismatch")
        if [item["token"] for item in phases["VX"]] != [1, 1, 1, 2, 1, 1]:
            errors.append("VBlank token coalesce/fresh cadence mismatch")
    if len(phases["VE"]) == 6 and [item["token"] for item in phases["VE"]] != [
        0,
        0,
        0,
        1,
        0,
        0,
    ]:
        errors.append("VBlank pre-increment token barrier topology mismatch")
    fresh_ack = next(
        (item for item in phases["VA"] if item["frame"] == 722), None
    )
    if fresh_ack is None or fresh_ack["entry_status"] & 8 == 0:
        errors.append("post-coalesce fresh VBlank did not enter during blank")
    for item in phases["VS"]:
        ack = ack_by_current_frame.get(item["frame"])
        if ack and ack["entry_status"] & 8 and item["exit_status"] & 8 == 0:
            errors.append(f"frame {item['frame']} VBlank exited outside blank")

    expected_queue = {
        719: (
            (0x00FF8000, 0xF000, 0x0140, 0x8000, 0),
            (0x00FF8600, 0xC000, 0x0028, 0x8001, 0),
            (0x00FF8650, 0xC080, 0x0028, 0x8001, 0),
            (0x00FF86A0, 0xC100, 0x0028, 0x8001, 0),
        ),
    }
    expected_queue[720] = expected_queue[719]
    expected_queue[721] = ()
    expected_queue[722] = ()
    expected_queue[723] = ()
    expected_queue[724] = expected_queue[719] + (
        (0x00FF8740, 0xC180, 0x0028, 0x8002, 0),
    )
    expected_words = {
        719: 440,
        720: 440,
        721: 0,
        722: 0,
        723: 0,
        724: 480,
    }
    if len(phases["VC"]) == 6 and _record_values(
        phases["VC"], "attempt", "tick"
    ) != [[0, 718], [0, 719], [0, 720], [1, 0], [1, 0], [1, 1]]:
        errors.append("VBlank engine cadence around reset barrier mismatch")
    queue_summary: dict[str, Any] = {}
    for frame in target_frames:
        vc = next((item for item in phases["VC"] if item["frame"] == frame), None)
        vp = next((item for item in phases["VP"] if item["frame"] == frame), None)
        queue = [item for item in phases["Q"] if item["frame"] == frame]
        starts = [item for item in phases["DS"] if item["frame"] == frame]
        ends = [item for item in phases["DE"] if item["frame"] == frame]
        queue_summary[str(frame)] = {
            "vc": dict(vc) if vc else None,
            "vp": dict(vp) if vp else None,
            "records": [dict(item) for item in queue],
            "dma_calls": len(starts),
        }
        if vc is None or vp is None:
            continue
        expected = expected_queue[frame]
        words = expected_words[frame]
        if (vc["pending"], vc["published_depth"], vc["build_depth"], vc["build_words"]) != ((1 if expected else 0), len(expected), len(expected), words):
            errors.append(f"frame {frame} queue publication topology mismatch")
        actual = [
            (item["source"], item["destination"], item["words"], item["flags"], item["age"])
            for item in queue
        ]
        if [item["index"] for item in queue] != list(range(len(expected))) or tuple(actual) != expected:
            errors.append(f"frame {frame} exact queue records mismatch")
        if len(starts) != len(expected) or len(ends) != len(expected):
            errors.append(f"frame {frame} DMA call count mismatch")
        for index, (start, end, item) in enumerate(zip(starts, ends, expected)):
            cumulative = sum(entry[2] for entry in expected[: index + 1])
            if (start["source"], start["destination"], start["words"], start["commit_words"]) != (*item[:3], cumulative) or end["commit_words"] != cumulative or end["cycle"] <= start["cycle"]:
                errors.append(f"frame {frame} DMA call {index} mismatch")
        if (vp["commit_words"], vp["debug_words"], vp["depth"], vp["pending"]) != (words, words, 0, 0):
            errors.append(f"frame {frame} post-DMA state mismatch")

    for frame in sorted({item["frame"] for item in phases["VC"]} - set(target_frames)):
        vc = next(item for item in phases["VC"] if item["frame"] == frame)
        vp = next((item for item in phases["VP"] if item["frame"] == frame), None)
        queue = [item for item in phases["Q"] if item["frame"] == frame]
        starts = [item for item in phases["DS"] if item["frame"] == frame]
        queue_summary[str(frame)] = {
            "vc": dict(vc),
            "vp": dict(vp) if vp else None,
            "records": [dict(item) for item in queue],
            "dma_calls": len(starts),
        }

    renderer_observed = [dict(item) for item in rr]
    reset_hold = next((item for item in rr if item["attempt"] == 1 and item["tick"] == 0), None)
    tick1 = next((item for item in rr if item["attempt"] == 1 and item["tick"] == 1), None)
    tick2 = next((item for item in rr if item["attempt"] == 1 and item["tick"] == 2), None)
    caught_up_without_vblank = bool(
        tick1
        and tick2
        and tick1["frame"] == tick2["frame"]
        and tick1["pending"] == 1
        and (tick2["result"] & 0xFFFF) == 1
        and tick2["backlog_frames"] > tick1["backlog_frames"]
    )
    persistent_token_backlog = bool(
        reset_hold
        and tick2
        and reset_hold["token"] > 0
        and tick2["token"] > 0
        and max((item["token"] for item in phases["VX"]), default=0) > 1
    )
    reset_projection = phases["PE"][1] if len(phases["PE"]) == 2 else None
    timeout_return = phases["ME"][1] if len(phases["ME"]) >= 2 else None
    recovery_vc = next(
        (
            item
            for item in phases["VC"]
            if item["attempt"] == 1
            and item["tick"] == 1
            and item["published_depth"] == 5
            and item["build_words"] == 480
        ),
        None,
    )
    recovery_ack = (
        ack_by_current_frame.get(recovery_vc["frame"])
        if recovery_vc
        else None
    )
    observation = {
        "renderer_returns": renderer_observed,
        "reset_barrier": {
            name: dict(phases[name][0]) if len(phases[name]) == 1 else None
            for name in ("BC", "BM", "BZ", "BW")
        },
        "reset_hold_token": reset_hold["token"] if reset_hold else None,
        "maximum_vblank_exit_token": max(
            (item["token"] for item in phases["VX"]), default=None
        ),
        "caught_up_tick2_without_vblank": caught_up_without_vblank,
        "persistent_frame_token_backlog": persistent_token_backlog,
        "timeout_return_frame": timeout_return["frame"] if timeout_return else None,
        "reset_projection_return_frame": (
            reset_projection["frame"] if reset_projection else None
        ),
        "reset_hold_frame": reset_hold["frame"] if reset_hold else None,
        "recovery_commit": {
            "frame": recovery_vc["frame"] if recovery_vc else None,
            "entry_status": (
                recovery_ack["entry_status"] if recovery_ack else None
            ),
            "entered_during_blank": bool(
                recovery_ack and recovery_ack["entry_status"] & 8
            ),
        },
        "fresh_vblank": {
            "frame": 723,
            "entry_status": fresh_ack["entry_status"] if fresh_ack else None,
            "entered_during_blank": bool(
                fresh_ack and fresh_ack["entry_status"] & 8
            ),
        },
        "queue_by_frame": queue_summary,
    }
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "marker_counts": {phase: len(items) for phase, items in phases.items()},
        "projection_spans": projection_spans,
        "fault_observation": observation,
        "conclusion": (
            "reset barrier proves the bank epoch transition, coalesces two accrued tokens to zero, waits for one fresh VBlank, and commits the exact 5-record/480-word recovery before tick 2 without persistent backlog"
            if not errors
            else (
                "reset work crossed VBlank(s), queued frame tokens, and caused immediate tick-2 renderer backlog before the tick-1 queue commit"
                if caught_up_without_vblank
                else (
                    "reset work crossed VBlank(s) and left a persistent frame-token cadence backlog through tick 2"
                    if persistent_token_backlog
                    else "reset checkpoint discriminator failed closed"
                )
            )
        ),
    }


def extended_pc_contract(rom: bytes, symbols: Mapping[str, int], *, profile: str = "baseline") -> dict[str, Any]:
    contract = pc_contract(rom, symbols, profile=profile)
    addresses = PROFILE_EXTENDED_PC[profile]
    windows = {**EXTENDED_WINDOWS, "overlay_end": "61008316"} if profile == "lookup" else EXTENDED_WINDOWS
    if profile in {"sprite", "indexed"}:
        windows = {**EXTENDED_WINDOWS, "hud_begin": "6100EB5E", "hud_end": "6100F338", "overlay_end": "6100831E"}
    if profile == "pool":
        windows = {**EXTENDED_WINDOWS, "hud_begin": "6100EB5E", "hud_end": "6100F338", "overlay_end": "61008306"}
    if profile == "highwater":
        windows = {**EXTENDED_WINDOWS, "hud_begin": "6100EB5E", "hud_end": "6100F338", "sat_end": "610004F6", "overlay_end": "61008228"}
    if profile == "renderer_hwm":
        windows = {**EXTENDED_WINDOWS, "hud_begin": "6100EACE", "hud_end": "6100F2BE", "sat_end": "610004F6", "overlay_end": "61008198"}
    if profile == "crc_projection":
        windows = {**EXTENDED_WINDOWS, "hud_begin": "6100EACE", "hud_end": "6100F2BE", "sat_end": "610004F6", "overlay_end": "610080DC"}
    if symbols.get("MainLoop") != 0x2C2:
        raise EvidenceFailure("extended MainLoop symbol drift")
    for name, address in addresses.items():
        expected = bytes.fromhex(windows[name])
        if rom[address : address + len(expected)] != expected:
            raise EvidenceFailure(f"extended {name} ROM opcode drift")
    contract["extended_addresses"] = dict(addresses)
    contract["extended_exact_windows"] = dict(windows)
    return contract


def derive_prefix_reanalysis(root: Path) -> dict[str, Any]:
    """Reparse one immutable prefix trace; never rerun or rewrite its capture."""
    source_report = root / (PREFIX_CAPTURE_STEM + ".json")
    source_trace = root / (PREFIX_CAPTURE_STEM + ".log")
    source_collector = root / PREFIX_CAPTURE_COLLECTOR
    for path, expected in ((source_report, PREFIX_CAPTURE_REPORT_SHA256),
                           (source_trace, PREFIX_CAPTURE_TRACE_SHA256),
                           (source_collector, PREFIX_CAPTURE_COLLECTOR_SHA256)):
        if sha256_file(path) != expected:
            raise EvidenceFailure("prefix immutable source hash drift")
    capture = json.loads(source_report.read_text("utf-8"))
    trace = source_trace.read_text("utf-8")
    archived = root / "artifacts/archive/m5-high-water-96dec577-20261010/level-B03"
    rom, sym = archived / "recursion94-debug.bin", archived / "recursion94-debug.sym"
    if sha256_file(rom) != PROFILE_ROM_PINS["highwater"] or sha256_file(sym) != PROFILE_SYMBOL_PINS["highwater"]:
        raise EvidenceFailure("prefix archived ROM/symbol mismatch")
    symbols = parse_vasm_symbols(sym)
    script = build_prefix_command_script(symbols, profile="highwater")
    expected_original_errors = ["prefix sampled engine counters require missing IRQ(s)",
                                "prefix IRQ 1640 does not commit the preceding renderer publication"]
    if (capture.get("schema") != PREFIX_REPORT_SCHEMA or capture.get("command_script") != script or
            capture.get("status") != "diagnostic_fail" or capture["analysis"]["errors"] != expected_original_errors or
            capture["provenance"]["collector"]["sha256"] != PREFIX_CAPTURE_COLLECTOR_SHA256):
        raise EvidenceFailure("prefix original capture schema/script/status mismatch")
    delimiter = "----- CONSOLE TRANSCRIPT -----\n"
    if trace.count(delimiter) != 1:
        raise EvidenceFailure("prefix trace delimiter mismatch")
    console = trace.split(delimiter, 1)[1]
    reconstructed = _trace_text(script, console).replace(f"# {REPORT_SCHEMA}\n", f"# {PREFIX_REPORT_SCHEMA}\n", 1)
    if (trace != reconstructed or _sha256_bytes(script.encode()) != capture["command_script_sha256"] or
            _sha256_bytes(console.encode()) != capture["console_sha256"]):
        raise EvidenceFailure("prefix trace reconstruction/console hash mismatch")
    geometry = capture["console_geometry"]
    raw = geometry["raw_screen_buffer"]
    capacity = prefix_console_capacity(script)
    if (capture["console_capacity"] != capacity or (geometry["width"], geometry["height"]) != (120, PREFIX_CONSOLE_ROWS) or
            not 0 <= geometry["cursor_y"] < PREFIX_CONSOLE_ROWS - 1 or not 0 <= geometry["cursor_x"] < 120 or
            len(raw) != 120 * (geometry["cursor_y"] + 1) or
            _sha256_bytes(raw.encode("utf-16-le")) != geometry["raw_screen_buffer_utf16le_sha256"] or
            [ord(char) for char in raw[-120:]] != geometry["current_row_utf16"] or
            "\n".join(raw[offset:offset + 120].rstrip() for offset in range(0, len(raw), 120)).rstrip() + "\n" != console):
        raise EvidenceFailure("prefix complete raw console/geometry derivation mismatch")
    records = parse_prefix_markers(console)
    if records != capture["records"] or _sha256_bytes(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()) != capture["records_sha256"]:
        raise EvidenceFailure("prefix exact independently parsed raw records mismatch")
    pc = {**extended_pc_contract(rom.read_bytes(), symbols, profile="highwater"),
          "prefix_projection_entry": symbols["GameplayBuildProjection"]}
    if (capture["pc_contract"] != pc or prefix_console_contract_errors(console, script) or
            prefix_command_policy_errors(script, symbols, profile="highwater")):
        raise EvidenceFailure("prefix archived opcode/native console/command contract mismatch")
    # The pinned capture embeds the route/reset reference authenticated during
    # collection. Do not consult mutable current runtime reports or build paths.
    route = capture["prefix_route"]
    if (route["authoritative_steps"] != PREFIX_TOTAL_STEPS or route["schedule_sha256"] != _sha256_bytes(prefix_schedule()) or
            route["input_stages"] != prefix_input_stages() or
            any((route["sources"][key]["path"], route["sources"][key]["sha256"]) != pin for key, pin in PREFIX_RUNTIME_PINS.items())):
        raise EvidenceFailure("prefix captured route reference mismatch")
    analysis = analyse_prefix(records, route)
    if analysis["protocol_status"] != "pass":
        raise EvidenceFailure("prefix offline protocol analysis failed: " + "; ".join(analysis["errors"]))
    for field in ("budget_errors", "budget_status", "phase_spans", "natural_resets", "marker_counts"):
        if analysis[field] != capture["analysis"][field]:
            raise EvidenceFailure("prefix offline repair changed original budget/timing facts: " + field)
    return {
        "schema": "r94-m5-b03-canonical-prefix-offline-reanalysis-v1",
        "status": "diagnostic_pass" if analysis["status"] == "pass" else "diagnostic_fail",
        "offline_reanalysis_status": "pass", "diagnostic_protocol_status": analysis["protocol_status"],
        "cycle_blank_budget_status": analysis["budget_status"], "canonical": False, "diagnostic_only": True,
        "derivation": "offline_reanalysis_no_emulator_rerun", "completed_utc": datetime.now(timezone.utc).isoformat(),
        "source_capture": {"report": _file_record(source_report, root), "trace": _file_record(source_trace, root),
                           "archived_collector": _file_record(source_collector, root), "original_status": capture["status"],
                           "original_protocol_status": capture["diagnostic_protocol_status"],
                           "original_analysis_errors": capture["analysis"]["errors"],
                           "records_sha256": capture["records_sha256"], "console_sha256": capture["console_sha256"],
                           "raw_console_utf16le_sha256": geometry["raw_screen_buffer_utf16le_sha256"]},
        "provenance": {"archived_rom": _file_record(rom, root), "archived_symbols": _file_record(sym, root),
                       "current_helper": _file_record(root / COLLECTOR_RELATIVE_PATH, root),
                       "current_tests": _file_record(root / TEST_RELATIVE_PATH, root)},
        "corrections": ["Anchor required IRQs directionally after an in-gate engine observation, including the trailing transition out of its gate; never infer the already-counted lower-bound frame as a logged IRQ.",
                        "Each observed renderer publication binds to its first later commit by cycle order, even if the engine advances; a later same-identity commit without a new publication must be exact-empty."],
        "limitations": ["Unlogged IRQs before each window's lower-bound POST are not observed or certified.",
                        "Unsampled renderer identities retain exact queue/DMA proof but do not acquire invented RR markers; final RR241 has no subsequent IRQ in this capture.",
                        "All original budget errors and raw phase/reset spans are unchanged. Protocol reanalysis success is not a cycle/blank budget pass or a full M5 pass."],
        "read_only_contract": {"emulator_processes_started": 0, "debugger_commands_sent": 0, "guest_writes": 0,
                               "source_capture_modified": False, "mutable_build_inputs_used": False,
                               "records_reparsed_from_trace": True, "original_budget_errors_exactly_preserved": True},
        "analysis": analysis,
    }


def derive_extended_baseline(root: Path) -> dict[str, Any]:
    """Reanalyse the pinned baseline, preserving its original failed report."""
    source_report = root / EXTENDED_BASELINE_REPORT
    source_trace = root / EXTENDED_BASELINE_TRACE
    for path, expected in ((source_report, EXTENDED_BASELINE_REPORT_SHA256), (source_trace, EXTENDED_BASELINE_TRACE_SHA256)):
        if sha256_file(path) != expected:
            raise EvidenceFailure("extended baseline source hash drift")
    capture = json.loads(source_report.read_text("utf-8"))
    trace = source_trace.read_text("utf-8")
    archived = root / "artifacts/archive/m5-pre-timing-140039e3-20261010/level-B03"
    rom = archived / "recursion94-debug.bin"
    sym = archived / "recursion94-debug.sym"
    if sha256_file(rom) != PINNED_ROM_SHA256 or sha256_file(sym) != PINNED_SYMBOL_SHA256:
        raise EvidenceFailure("archived baseline ROM/symbol mismatch")
    symbols = parse_vasm_symbols(sym)
    script = build_command_script(symbols, extended_overlay=True)
    if capture.get("schema") != EXTENDED_REPORT_SCHEMA or capture.get("command_script") != script:
        raise EvidenceFailure("baseline schema/script mismatch")
    console = trace.split("----- CONSOLE TRANSCRIPT -----\n", 1)[1]
    reconstructed = _trace_text(script, console).replace(f"# {REPORT_SCHEMA}\n", f"# {EXTENDED_REPORT_SCHEMA}\n", 1)
    if trace != reconstructed or _sha256_bytes(console.encode()) != capture.get("console_sha256"):
        raise EvidenceFailure("baseline trace reconstruction/console hash mismatch")
    records = parse_markers(console, extended_overlay=True)
    if records != capture.get("records") or _sha256_bytes(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()) != capture.get("records_sha256"):
        raise EvidenceFailure("baseline exact raw records mismatch")
    if console_contract_errors(console, script, extended_overlay=True) or command_policy_errors(script, symbols, extended_overlay=True):
        raise EvidenceFailure("baseline native console/command protocol mismatch")
    if capture.get("pc_contract") != extended_pc_contract(rom.read_bytes(), symbols):
        raise EvidenceFailure("baseline archived opcode contract mismatch")
    analysis = analyse_extended(records)
    return {
        "schema": "r94-m5-b03-budget-reset-overlay-baseline-reanalysis-v1",
        "status": "diagnostic_pass" if analysis["status"] == "pass" else "diagnostic_fail",
        "diagnostic_protocol_status": analysis["protocol_status"],
        "cycle_blank_budget_status": analysis["budget_status"],
        "canonical": False, "diagnostic_only": True,
        "derivation": "offline_reanalysis_no_emulator_rerun",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "source_capture": {"report": _file_record(source_report, root), "trace": _file_record(source_trace, root), "original_status": capture["status"], "original_analysis_errors": capture["analysis"]["errors"], "original_collector": capture["provenance"]["collector"], "records_sha256": capture["records_sha256"]},
        "provenance": {"archived_rom": _file_record(rom, root), "archived_symbols": _file_record(sym, root), "current_helper": _file_record(root / COLLECTOR_RELATIVE_PATH, root), "current_tests": _file_record(root / TEST_RELATIVE_PATH, root)},
        "corrections": ["The bounded capture ends at total724 caller return; a ninth VBlank during its gameplay is valid diagnostic observation but fails the eight-frame timing/cadence budget, not the debugger protocol.", "Anchor nominal phase at explicit-WAIT frame720, not already-delayed-but-still-blank frame724."],
        "read_only_contract": {"emulator_processes_started": 0, "debugger_commands_sent": 0, "guest_writes": 0, "source_capture_modified": False, "mutable_build_inputs_used": False},
        "analysis": analysis,
    }


def derive_lookup_comparison(root: Path, *, candidate_profile: str = "lookup") -> dict[str, Any]:
    """Exact offline before/after proof for one isolated optimization."""
    if candidate_profile not in {"lookup", "sprite", "indexed", "pool", "highwater", "renderer_hwm"}:
        raise EvidenceFailure("unsupported isolated comparison profile")
    directory = root / "artifacts/diagnostics/m5-budget"
    captures = {
        "baseline_reset": ("reset-overlay-baseline-140039e3-total719-total724-20261010", EXTENDED_BASELINE_REPORT_SHA256, EXTENDED_BASELINE_TRACE_SHA256),
        "lookup_reset": ("reset-overlay-lookup-9a741642-total719-total724-20261010", "e8cc4149dc8ba95f0259ead6e4c50cd009fb9a47537672e90185256f1994a3a5", "02b61efd341fe314c9ec4d23546b5dfd42156532be2c869e3530a7b62153952c"),
        "baseline_regular": ("checkpoint-final-140039e3-tick59-tick61-20261010", "98b98610dd68735ae6bdc1bf3ba5fa4b9857fdc31c381f4262851ce7b931c035", "fb1de38ede7730fe9336d6d166ea6254132497e6b33d5ee4fbec979e86eb068d"),
        "lookup_regular": ("checkpoint-lookup-9a741642-tick59-tick61-20261010", "f6fd781038736198d7fc9e5a29367b8e7405904c0523b470f06e61c72332d555", "56c76013eff2eaa33ff818d7e338c1e3da8e99d80f9068582ebfcc98d4af1fb9"),
    }
    roles = {"baseline": "baseline", "lookup": "lookup"}
    if candidate_profile in {"sprite", "indexed", "pool", "highwater", "renderer_hwm"}:
        captures = {
            "baseline_reset": captures["lookup_reset"],
            "baseline_regular": captures["lookup_regular"],
            "lookup_reset": ("reset-overlay-sprite-09be6696-total719-total724-20261010", "17f4daf9035dba950093570832fec4e285938364b7c68f92a0e1c7819512b321", "ab1b9159273a38fe03525f4fc95d287918cd4ba2a1b9828a4907c3b1b40dda09"),
            "lookup_regular": ("checkpoint-sprite-09be6696-tick59-tick61-20261010", "29234a101b1247994da4e2b2b1b87cf7d4a199c12414fbb450b51701626d12f4", "899442b76066898e8c59508c1859fa9a5353202612459f0bd8be51414086dc94"),
        }
        roles = {"baseline": "lookup", "lookup": "sprite"}
    if candidate_profile in {"indexed", "pool", "highwater", "renderer_hwm"}:
        captures = {
            "baseline_reset": captures["lookup_reset"],
            "baseline_regular": captures["lookup_regular"],
            "lookup_reset": ("reset-overlay-indexed-c051995c-total719-total724-20261010", "077b296d4886f111e94ddbb204a4e9612005114ab8589b475489cbc7f20ccd1d", "c908c68b2e2b2fd422e8beb3d6d2bcd36197766db737848b3eb0f318aeff7f85"),
            "lookup_regular": ("checkpoint-indexed-c051995c-tick59-tick61-20261010", "bdf589b3c40871959d0c951ea2f00c5cd3c270349fe7668d29e9d0802771de46", "f1b7b69bfcb255294392e5a6c660304490dfaa8ce53928c18d609478646f9af6"),
        }
        roles = {"baseline": "sprite", "lookup": "indexed"}
    if candidate_profile in {"pool", "highwater", "renderer_hwm"}:
        captures = {
            "baseline_reset": captures["lookup_reset"],
            "baseline_regular": captures["lookup_regular"],
            "lookup_reset": ("reset-overlay-pool-25a74bd2-total719-total724-20261010", "2e1985f114ff9e476d6328e4abf556789dae967088621dc9845149215869ba37", "a314efb0c3de2d05dd54aa720eb3a9cea129e20fee463574cb4d29aef7e28cd7"),
            "lookup_regular": ("checkpoint-pool-25a74bd2-tick59-tick61-20261010", "29bf381c1475a19d3346d742f06dfdea38cd901bd53a8cbb550ee0bfd00d6ace", "6403c02d748d6ac1d0874e849a87a4524b8d36a7ce399a678cd2f75bd4a74d36"),
        }
        roles = {"baseline": "indexed", "lookup": "pool"}
    if candidate_profile in {"highwater", "renderer_hwm"}:
        captures = {
            "baseline_reset": captures["lookup_reset"],
            "baseline_regular": captures["lookup_regular"],
            "lookup_reset": ("reset-overlay-highwater-96dec577-total719-total724-20261010", "04011af7c1f75c4c518bbf169a560ec8368d589ff440199dc258393d6a808c77", "4f47773376fb623b6f525648b9974980b4541b9c69c1add023360ecbe568ea3b"),
            "lookup_regular": ("checkpoint-highwater-96dec577-tick59-tick61-20261010", "c852a44a1bc768cd22392381e1644b8916b3467e6922e5fb0d9aa250bda96462", "2243c934a1f5554e729afb2325f3ee3b4f1d9a7b7cec7a05d8e8413dc356b15c"),
        }
        roles = {"baseline": "pool", "lookup": "highwater"}
    if candidate_profile == "renderer_hwm":
        captures = {
            "baseline_reset": captures["lookup_reset"],
            "baseline_regular": captures["lookup_regular"],
            "lookup_reset": ("reset-overlay-renderer-hwm-e6778f90-total719-total724-20261010", "b1ffcd6d09667845a27b367b33be87df45bab6b11c7539b4d495080c01c67357", "feac204cabf748233953b55840830b964f87c741213caad127293f8f43024bac"),
            "lookup_regular": ("checkpoint-renderer-hwm-e6778f90-tick59-tick61-20261010", "6fc3cebc759f71ff8e9298177a8b6817f83fd41e8a05d461b85fc67fe50cad47", "e2b0152acbbe6d4b841380e507ca748ed5a66841338b9190be54e8c61baab453"),
        }
        roles = {"baseline": "highwater", "lookup": "renderer_hwm"}
    reports: dict[str, Any] = {}
    analyses: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    for name, (stem, report_hash, trace_hash) in captures.items():
        report_path, trace_path = directory / f"{stem}.json", directory / f"{stem}.log"
        if sha256_file(report_path) != report_hash or sha256_file(trace_path) != trace_hash:
            raise EvidenceFailure(f"lookup comparison immutable source drift: {name}")
        capture = json.loads(report_path.read_text("utf-8"))
        role, family = name.split("_")
        profile = roles[role]
        archive_name = {"baseline": "m5-pre-timing-140039e3-20261010", "lookup": "m5-lookup-only-9a741642-20261010", "sprite": "m5-sprite-loop-09be6696-20261010", "indexed": "m5-crc-index-c051995c-20261010", "pool": "m5-empty-pool-25a74bd2-20261010", "highwater": "m5-high-water-96dec577-20261010", "renderer_hwm": "m5-renderer-hwm-e6778f90-20261010"}[profile]
        archive = root / "artifacts/archive" / archive_name / "level-B03"
        rom, sym = archive / "recursion94-debug.bin", archive / "recursion94-debug.sym"
        rom_hash = PROFILE_ROM_PINS[profile]
        sym_hash = PROFILE_SYMBOL_PINS[profile]
        if sha256_file(rom) != rom_hash or sha256_file(sym) != sym_hash:
            raise EvidenceFailure(f"lookup comparison archived build drift: {name}")
        symbols = parse_vasm_symbols(sym)
        trace = trace_path.read_text("utf-8")
        if trace.count("----- CONSOLE TRANSCRIPT -----\n") != 1:
            raise EvidenceFailure(f"lookup comparison console delimiter: {name}")
        console = trace.split("----- CONSOLE TRANSCRIPT -----\n", 1)[1]
        if family == "reset":
            script = build_command_script(symbols, extended_overlay=True, profile=profile)
            policy = command_policy_errors(script, symbols, extended_overlay=True, profile=profile)
            console_errors = console_contract_errors(console, script, extended_overlay=True, renderer_end_pc=PROFILE_PC[profile]["renderer_end"])
            records = parse_markers(console, extended_overlay=True)
            reconstructed = _trace_text(script, console).replace(f"# {REPORT_SCHEMA}\n", f"# {EXTENDED_REPORT_SCHEMA}\n", 1)
            contract = extended_pc_contract(rom.read_bytes(), symbols, profile=profile)
            analysis = analyse_extended(records)
            protocol_pass = analysis["protocol_status"] == "pass"
        else:
            script = regular.build_repair_command_script(symbols, profile=profile)
            policy = regular.repair_command_policy_errors(script, symbols, profile=profile)
            console_errors = regular.console_contract_errors(console, script)
            records = regular.parse_markers(console)
            reconstructed = regular._trace_text(script, console)
            contract = regular.repair_pc_contract(rom.read_bytes(), symbols, profile=profile)
            analysis = regular.analyse_repair(records)
            protocol_pass = analysis["status"] == "pass"
        if policy or console_errors or not protocol_pass or trace != reconstructed or capture["command_script"] != script or capture["records"] != records or capture["pc_contract"] != contract:
            raise EvidenceFailure(f"lookup comparison raw protocol reanalysis failed: {name}")
        for field, payload in (("command_script_sha256", script.encode()), ("console_sha256", console.encode()), ("records_sha256", json.dumps(records, sort_keys=True, separators=(",", ":")).encode())):
            if capture[field] != _sha256_bytes(payload):
                raise EvidenceFailure(f"lookup comparison {field} mismatch: {name}")
        if capture["provenance"]["rom"]["sha256"] != rom_hash or capture["provenance"]["symbols"]["sha256"] != sym_hash:
            raise EvidenceFailure(f"lookup comparison capture ROM/symbol binding mismatch: {name}")
        reports[name], analyses[name] = capture, analysis
        sources[f"{profile}_{family}"] = {"report": _file_record(report_path, root), "trace": _file_record(trace_path, root), "archived_rom": _file_record(rom, root), "archived_symbols": _file_record(sym, root), "captured_collector": capture["provenance"]["collector"]}
    comparisons = []
    before_buffers = analyses["baseline_reset"]["renderer_buffers"]
    after_buffers = analyses["lookup_reset"]["renderer_buffers"]
    for before, after in zip(before_buffers, after_buffers):
        same_identity = (before["attempt"], before["tick"]) == (after["attempt"], after["tick"])
        regions = [{"address": left["address"], "bytes": left["bytes"], "before_sha256": left["sha256"], "after_sha256": right["sha256"], "exact_bytes_equal": left == right} for left, right in zip(before["regions"], after["regions"])]
        comparisons.append({"attempt": before["attempt"], "tick": before["tick"], "before_frame": before["frame"], "after_frame": after["frame"], "identity_equal": same_identity, "regions": regions})
    reset_projection_fields = ("attempt", "tick", "length", "crc", "checkpoint_count", "state_crc")
    before_projection = _record_values(_by_phase(reports["baseline_reset"]["records"], "PE"), *reset_projection_fields)
    after_projection = _record_values(_by_phase(reports["lookup_reset"]["records"], "PE"), *reset_projection_fields)
    parity = {
        "six_matching_renderer_samples": len(comparisons) == 6 and all(item["identity_equal"] for item in comparisons),
        "all_6240_renderer_bytes_equal": all(region["exact_bytes_equal"] for item in comparisons for region in item["regions"]),
        "timeout_and_reset_projection_crc_equal": before_projection == after_projection,
        "regular_projection_crc_and_all_mirrors_equal": analyses["baseline_regular"]["projection_parity"] == analyses["lookup_regular"]["projection_parity"],
    }
    before_tick60 = analyses["baseline_regular"]["phase_spans"]["gameplay_tick60"]["delta_cpu_cycles_ceil"]
    after_tick60 = analyses["lookup_regular"]["phase_spans"]["gameplay_tick60"]["delta_cpu_cycles_ceil"]
    budget_pass = after_tick60 <= 83203 and analyses["lookup_reset"]["budget_status"] == "pass"
    report = {
        "schema": "r94-m5-b03-" + {"lookup": "lookup-only", "sprite": "sprite-loop", "indexed": "crc-index", "pool": "empty-pool", "highwater": "high-water", "renderer_hwm": "renderer-hwm"}[candidate_profile] + "-timing-parity-v1",
        "status": "diagnostic_pass" if all(parity.values()) and budget_pass else "diagnostic_fail",
        "diagnostic_protocol_status": "pass",
        "parity_status": "pass" if all(parity.values()) else "fail",
        "cycle_blank_budget_status": "pass" if budget_pass else "fail",
        "canonical": False, "diagnostic_only": True,
        "derivation": "offline_comparison_of_four_pinned_native_captures",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "sources": sources, "parity_checks": parity,
        "comparison_profiles": {"before": roles["baseline"], "after": roles["lookup"]},
        "renderer_byte_comparisons": comparisons,
        "reset_projection_comparison": {"fields": list(reset_projection_fields), "before": before_projection, "after": after_projection},
        "regular_projection_parity": analyses["lookup_regular"]["projection_parity"],
        "tick60_simulation_budget": {"limit_cpu_cycles": 83203, "before_cpu_cycles": before_tick60, "after_cpu_cycles": after_tick60, "saved_cpu_cycles": before_tick60 - after_tick60, "remaining_excess_cpu_cycles": max(0, after_tick60 - 83203), "status": "fail" if after_tick60 > 83203 else "pass"},
        "analyses": {f"{roles[name.split('_')[0]]}_{name.split('_')[1]}": analysis for name, analysis in analyses.items()},
        "read_only_contract": {"emulator_processes_started": 0, "debugger_commands_sent": 0, "guest_writes": 0, "source_captures_modified": False, "mutable_build_inputs_used": False},
        "provenance": {"helper": _file_record(root / COLLECTOR_RELATIVE_PATH, root), "regular_helper": _file_record(root / REGULAR_HELPER_RELATIVE_PATH, root), "tests": _file_record(root / TEST_RELATIVE_PATH, root)},
        "conclusion": candidate_profile + " isolated-change sampled renderer/projection parity " + ("passes" if all(parity.values()) else "fails") + "; cycle/blank budget " + ("passes" if budget_pass else "fails") + ". Noncanonical bounded evidence only.",
    }
    if candidate_profile == "renderer_hwm":
        report["cycle_blank_budget_scope"] = {
            "regular_attempt": 0, "regular_ticks": [59, 60, 61],
            "reset_total_steps": [719, 720, 721, 722, 723, 724],
            "later_attempt_timing_certified": False, "full_route_timing_certified": False,
        }
        report["conclusion"] += " Only the first-attempt regular samples and first-reset window are covered; this is not a later-attempt or full-route timing pass."
    return report


def analyse_extended(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep the old reset proof, then diagnose (never waive) overlay timing."""
    fields = {**MARKER_FIELDS, **EXTENDED_MARKER_FIELDS}
    phases = {phase: _by_phase(records, phase) for phase in fields}
    errors: list[str] = []
    budget_errors: list[str] = []
    prefix: list[Mapping[str, Any]] = []
    for record in records:
        if record["phase"] in MARKER_FIELDS:
            prefix.append(record)
        if record["phase"] == "VX" and record["frame"] == 724:
            break
    legacy = analyse(prefix)
    errors.extend(f"legacy prefix: {error}" for error in legacy["errors"])
    observed_frames = [item["frame"] for item in phases["VX"]]
    if not 8 <= len(observed_frames) <= 12 or observed_frames != list(range(719, 719 + len(observed_frames))):
        errors.append("extended physical frames are not contiguous bounded coverage719..726(+through730)")
    if observed_frames != list(EXTENDED_TARGET_VBLANK_FRAMES):
        budget_errors.append("additional VBlank(s) before total724 caller return")
    extra_full_queue = any(item["frame"] == 727 and item["attempt"] == 1 and item["tick"] == 4 for item in phases["VC"])
    overflow_frames = [item["frame"] for item in phases["VS"] if item["entry_status"] & 8 and item["exit_status"] & 8 == 0]
    expected_counts = {
        "HI": 3, "MS": 6, "ME": 6, "MR": 1,
        **{name: 2 for name in ("PS", "PE", "ES", "EE", "TS", "TE", "CS", "CE")},
        **{name: 6 for name in ("RG", "RR", "MB", "MD", "RM", "RX")},
        **{name: 5 for name in ("HB", "HE", "SE", "OE")},
        **{name: 1 for name in ("BC", "BM", "BZ", "BW")},
        **{name: len(observed_frames) for name in ("VE", "VA", "VC", "VP", "VS", "VK", "VX")},
        **{name: 31 if extra_full_queue else 25 for name in ("Q", "DS", "DE")},
        "VO": len(overflow_frames), "BT": 390,
    }
    for phase, count in expected_counts.items():
        if len(phases[phase]) != count:
            errors.append(f"extended {phase} count {len(phases[phase])} != {count}")
    if not 1 <= len(phases["WW"]) <= 8:
        errors.append("extended WAIT marker count outside 1..8")
    waits719 = [item for item in phases["WW"] if (item["frame"], item["attempt"], item["tick"]) == (719, 0, 719)]
    if len(waits719) != 1:
        errors.append("missing/duplicate frame719 phase-anchor WAIT")
    expected_waits = [(item["frame"], item["attempt"], item["tick"]) for item in phases["MD"] if (item["attempt"], item["tick"]) != (1, 4) and (item["token"] == 0 or (item["attempt"], item["tick"]) == (1, 0))]
    if _record_values(phases["WW"], "frame", "attempt", "tick") != [list(identity) for identity in expected_waits]:
        errors.append("WAIT coverage does not match completed empty-token main iterations/reset barrier")
    for wait in phases["WW"]:
        done = [item for item in phases["MD"] if (item["frame"], item["attempt"], item["tick"]) == (wait["frame"], wait["attempt"], wait["tick"]) and item["cycle"] < wait["cycle"]]
        following = next((item for item in phases["VE"] if item["cycle"] > wait["cycle"]), None)
        if wait["sr"] & 0x700 != 0x700 or wait["token"] != 0 or len(done) != 1 or following is None or (following["frame"], following["attempt"], following["tick"]) != (wait["frame"], wait["attempt"], wait["tick"]):
            errors.append("WAIT identity/mask/token/preceding-MD/following-IRQ mismatch")
        if (wait["attempt"], wait["tick"]) == (1, 0):
            if len(phases["BW"]) != 1 or any(wait[key] != phases["BW"][0][key] for key in ("cycle", "frame", "attempt", "tick", "sr", "token")):
                errors.append("reset WAIT does not exactly bind legacy BW barrier")
        elif done and done[0]["token"] != 0:
            errors.append("ordinary WAIT preceded by a nonempty token")
    anchors = [next((item for item in phases["VE"] if item["frame"] == frame), None) for frame in (718, 719)]
    if any(item is None for item in anchors) or abs(anchors[1]["cycle"] - anchors[0]["cycle"] - NTSC_FRAME_MASTER_CLOCKS) > 128:
        errors.append("frame719-to720 nominal IRQ anchor period mismatch")
    cycle_records = [item for item in records if "cycle" in item]
    for before, after in zip(cycle_records, cycle_records[1:]):
        delta = after["cycle"] - before["cycle"]
        if delta < 0 or (delta == 0 and (before["phase"], after["phase"]) not in {("MS", "HI"), ("WW", "BW")}):
            errors.append("extended raw cycle order is not monotonic")
            break
    pre_ids = [(0, 718), (0, 719), (1, 0), (1, 1), (1, 2), (1, 3)]
    post_ids = [(0, 719), (0, 720), (1, 1), (1, 2), (1, 3), (1, 4)]
    render_ids = [(0, 719), (1, 0), (1, 1), (1, 2), (1, 3), (1, 4)]
    for phase, identities in (("MS", pre_ids), ("MB", pre_ids), ("ME", post_ids), ("RG", render_ids), ("RR", render_ids), ("MD", render_ids), ("RM", render_ids), ("RX", render_ids)):
        if [(item["attempt"], item["tick"]) for item in phases[phase]] != identities:
            errors.append(f"extended {phase} attempt/tick identities mismatch")
    for phase in ("HB", "HE", "SE", "OE"):
        if [(item["attempt"], item["tick"]) for item in phases[phase]] != [render_ids[0], *render_ids[2:]]:
            errors.append(f"extended {phase} rendered identities mismatch")
    if _record_values(phases["MS"], "input", "d0") != [[0, 0], [0, 0], *([[2, 2]] * 4)]:
        errors.append("extended canonical held input/d0 mismatch")
    if [item["result"] & 0xFFFF for item in phases["ME"]] != [0, 8, 0, 0, 0, 0]:
        errors.append("extended gameplay results mismatch")
    if [item["result"] & 0xFFFF for item in phases["RR"]] != [0, 4, 0, 0, 0, 0]:
        errors.append("extended renderer result mismatch")
    if [item["reset_phase"] for item in phases["RR"]] != [0, 12, 11, 10, 9, 8]:
        errors.append("extended reset overlay phase progression mismatch")
    if any(item["backlog_frames"] for item in phases["RR"]):
        budget_errors.append("renderer backlog observed")
    for phase in ("RG", "RR", "HB", "HE", "SE", "OE"):
        if any(item["sr"] & 0x700 != 0x700 for item in phases[phase]):
            errors.append(f"extended {phase} must be inside mask7")
    for phase in ("RM", "RX", "MD"):
        if any(item["sr"] & 0x700 != 0x500 for item in phases[phase]):
            errors.append(f"extended {phase} must be at mask5")
    expected_pre_frames = [719, 720, 723, 724, 725, 726]
    if [item["frame"] for item in phases["MS"]] != expected_pre_frames:
        budget_errors.append("authoritative/physical PRE cadence differs from 719,720,723..726")
    if [item["frame"] for item in phases["ME"]] != expected_pre_frames:
        budget_errors.append("authoritative/physical POST cadence differs from 719,720,723..726")
    queue_summary = dict(legacy["fault_observation"]["queue_by_frame"])
    queue4 = ((0xFF8000, 0xF000, 320, 0x8000, 0), (0xFF8600, 0xC000, 40, 0x8001, 0), (0xFF8650, 0xC080, 40, 0x8001, 0), (0xFF86A0, 0xC100, 40, 0x8001, 0))
    full_queues = [(725, 0xC180, 0xC280), (726, 0xC280, 0xC380)]
    if extra_full_queue:
        full_queues.append((727, 0xC380, 0xC480))
    for frame, clear, trace in full_queues:
        expected = (*queue4, (0xFF86F0, clear, 40, 0x8002, 0), (0xFF8740, trace, 40, 0x8002, 0))
        local = {phase: [item for item in phases[phase] if item["frame"] == frame] for phase in ("VC", "Q", "DS", "DE", "VP")}
        queue_summary[str(frame)] = local
        if len(local["VC"]) != 1 or _record_values(local["VC"], "attempt", "tick", "pending", "published_depth", "build_depth", "build_words") != [[1, frame - 723, 1, 6, 6, 520]]:
            errors.append(f"frame {frame} exact full-overlay queue topology mismatch")
        if _record_values(local["Q"], "index", "source", "destination", "words", "flags", "age") != [[index, *item] for index, item in enumerate(expected)]:
            errors.append(f"frame {frame} exact full-overlay queue records mismatch")
        if len(local["DS"]) != 6 or len(local["DE"]) != 6:
            errors.append(f"frame {frame} exact full-overlay DMA call count mismatch")
        for index, (start, end, item) in enumerate(zip(local["DS"], local["DE"], expected)):
            cumulative = sum(entry[2] for entry in expected[:index + 1])
            if [start[key] for key in ("source", "destination", "words", "commit_words")] != [*item[:3], cumulative] or end["commit_words"] != cumulative or end["cycle"] <= start["cycle"]:
                errors.append(f"frame {frame} DMA call {index} mismatch")
        if _record_values(local["VP"], "commit_words", "debug_words", "depth", "pending") != [[520, 520, 0, 0]]:
            errors.append(f"frame {frame} full-overlay post-DMA mismatch")
    for phase in ("VC", "VP", "VS", "VK", "VX"):
        if [item["frame"] for item in phases[phase]] != observed_frames:
            errors.append(f"extended {phase} physical frame coverage mismatch")
    for phase in ("VE", "VA"):
        if [item["frame"] for item in phases[phase]] != [frame - 1 for frame in observed_frames]:
            errors.append(f"extended {phase} preceding frame coverage mismatch")
    if [item["sequence"] for item in phases["VX"]] != [2 * frame for frame in observed_frames]:
        errors.append("extended final sequence mismatch")
    if [item["token"] for item in phases["VX"]] != [2 if frame == 722 else 1 for frame in observed_frames]:
        budget_errors.append("extended VBlank token progression mismatch")
    for frame in observed_frames[8:]:
        if frame == 727 and extra_full_queue:
            continue
        local = {phase: [item for item in phases[phase] if item["frame"] == frame] for phase in ("VC", "VP", "Q", "DS", "DE")}
        queue_summary[str(frame)] = local
        if _record_values(local["VC"], "attempt", "tick", "pending", "published_depth", "build_depth", "build_words") != [[1, 3, 0, 0, 0, 0]] or _record_values(local["VP"], "commit_words", "debug_words", "depth", "pending") != [[0, 0, 0, 0]] or any(local[phase] for phase in ("Q", "DS", "DE")):
            errors.append(f"extra frame {frame} is not exact zero-DMA interrupt during final gameplay")
    for ack in phases["VA"]:
        if ack["entry_status"] & 8 == 0:
            budget_errors.append(f"frame {ack['frame'] + 1} VBlank entered outside blank")
    for item in phases["VS"]:
        if item["entry_status"] & 8 and item["exit_status"] & 8 == 0:
            budget_errors.append(f"frame {item['frame']} VBlank exited outside blank")
    if any(item["counter"] for item in phases["VX"]):
        budget_errors.append("nonzero cumulative VBlank overrun counter")
    if [item["frame"] for item in phases["VO"]] != overflow_frames:
        errors.append("VBlank overrun-origin marker/frame mismatch")
    for index, item in enumerate(phases["VO"]):
        if (item["counter"], item["d0"], item["d1"], item["sequence"]) != (index, 0, 8, item["frame"] * 2 - 1):
            errors.append("VBlank overrun-origin raw predicate/counter mismatch")
    for item in phases["VX"]:
        if item["counter"] != sum(frame <= item["frame"] for frame in overflow_frames):
            errors.append("VBlank exact cumulative overrun counter mismatch")
    irq_phases = {"VE", "VA", "VC", "Q", "DS", "DE", "VP", "VS", "VO", "VK", "VX"}
    for frame in observed_frames:
        local = {phase: [item for item in phases[phase] if item["frame"] == frame - (1 if phase in {"VE", "VA"} else 0)] for phase in irq_phases}
        singleton = ("VE", "VA", "VC", "VP", "VS", "VK", "VX")
        if any(len(local[phase]) != 1 for phase in singleton):
            errors.append(f"frame {frame} IRQ boundary multiplicity mismatch")
            continue
        ve, va, vc, vp, vs, vk, vx = (local[phase][0] for phase in singleton)
        entry_blank = va["entry_status"] & 8
        expected_d0 = vs["exit_status"] & (8 if entry_blank else 0xFFFF)
        increment = int(bool(entry_blank) and not (vs["exit_status"] & 8))
        if vs["entry_status"] != va["entry_status"] or (vk["d0"] & 0xFFFF, vk["d1"] & 0xFFFF) != (expected_d0, entry_blank):
            errors.append(f"frame {frame} VA/VS/VK raw predicate binding mismatch")
        if vk["counter"] != vs["counter"] + increment or vx["counter"] != vk["counter"]:
            errors.append(f"frame {frame} VS/VK/VX exact counter progression mismatch")
        if va["sequence"] != frame * 2 - 1 or vx["token"] != ve["token"] + 1 or (ve["attempt"], ve["tick"]) != (vc["attempt"], vc["tick"]) or (vc["attempt"], vc["tick"]) != (vx["attempt"], vx["tick"]):
            errors.append(f"frame {frame} IRQ sequence/token/engine binding mismatch")
        expected_order = [ve, va, vc, *local["Q"]]
        for start, end in zip(local["DS"], local["DE"]):
            expected_order.extend((start, end))
        expected_order.extend((vp, vs, *local["VO"], vk, vx))
        positions = [next(index for index, candidate in enumerate(records) if candidate is item) for item in expected_order]
        if positions != list(range(positions[0], positions[-1] + 1)):
            errors.append(f"frame {frame} exact VE/VA/VC/Q*/(DS,DE)*/VP/VS/[VO]/VK/VX order mismatch")
    buffers = []
    for identity in render_ids:
        rows = [item for item in phases["BT"] if (item["attempt"], item["tick"]) == identity]
        expected_addresses = [address for base, size in BUFFER_RANGES for address in range(base, base + size, 16)]
        if [item["address"] for item in rows] != expected_addresses:
            errors.append(f"buffer coverage mismatch for {identity}")
            continue
        rr = next((item for item in phases["RR"] if (item["attempt"], item["tick"]) == identity), None)
        if rr is None or any(item["frame"] != rr["frame"] for item in rows):
            errors.append(f"buffer sample frame mismatch for {identity}")
        regions = []
        for base, size in BUFFER_RANGES:
            payload = b"".join(int(item[f"long{index}"]).to_bytes(4, "big") for item in rows if base <= item["address"] < base + size for index in range(4))
            regions.append({"address": base, "bytes": size, "hex": payload.hex().upper(), "sha256": _sha256_bytes(payload)})
        buffers.append({"attempt": identity[0], "tick": identity[1], "frame": rows[0]["frame"], "regions": regions})
    spans: dict[str, Any] = {}
    for index, total in enumerate(EXTENDED_TARGET_TOTALS):
        try:
            sample = {"gameplay": _span(phases["MS"][index], phases["ME"][index], "gameplay"), "main_token_to_caller_return": _span(phases["MB"][index], phases["MD"][index], "main"), "renderer_mask_to_restore": _span(phases["RM"][index], phases["RR"][index], "masked renderer"), "renderer_restore_to_caller_return": _span(phases["RR"][index], phases["MD"][index], "renderer return")}
            if total != 720:
                render_index = index if index == 0 else index - 1
                for label, first, last in (("hud", "HB", "HE"), ("sat", "HE", "SE"), ("overlay", "SE", "OE")):
                    sample[label] = _span(phases[first][render_index], phases[last][render_index], label)
                sample["queue_and_renderer_tail"] = _span(phases["OE"][render_index], phases["RR"][index], "queue publication")
            if sample["gameplay"]["delta_cpu_cycles_ceil"] > 83203:
                budget_errors.append(f"total {total} GameplayStep exceeds 83203 CPU-cycle provisional limit")
            spans[str(total)] = sample
        except (IndexError, EvidenceFailure) as error:
            errors.append(f"total {total} span: {error}")
    vblank_spans = {}
    for frame in observed_frames:
        entry = next((item for item in phases["VE"] if item["frame"] == frame - 1), None)
        end = next((item for item in phases["VX"] if item["frame"] == frame), None)
        if entry is not None and end is not None:
            try:
                vblank_spans[str(frame)] = _span(entry, end, f"VBlank {frame}")
            except EvidenceFailure as error:
                errors.append(str(error))
    irq_phase = []
    anchor = next((item for item in phases["VE"] if item["frame"] == 719), None)
    if anchor:
        for frame in observed_frames:
            actual = next((item for item in phases["VE"] if item["frame"] == frame - 1), None)
            nominal = anchor["cycle"] + (frame - 720) * NTSC_FRAME_MASTER_CLOCKS
            masks = [{"attempt": start["attempt"], "tick": start["tick"], "start_master": start["cycle"], "restore_master": end["cycle"], "nominal_inside_mask": start["cycle"] <= nominal < end["cycle"], "restore_after_nominal_master_clocks": end["cycle"] - nominal} for start, end in zip(phases["RM"], phases["RR"]) if start["cycle"] <= nominal <= end["cycle"]]
            irq_phase.append({"frame": frame, "anchor_frame": 720, "anchor_entry_master": anchor["cycle"], "nominal_entry_master": nominal, "actual_entry_master": actual["cycle"] if actual else None, "actual_lateness_master_clocks": actual["cycle"] - nominal if actual else None, "overlapping_renderer_masks": masks})
    return {
        "status": "pass" if not errors and not budget_errors else "fail",
        "protocol_status": "pass" if not errors else "fail",
        "budget_status": "pass" if not budget_errors else "fail",
        "errors": errors, "budget_errors": budget_errors,
        "legacy_prefix_analysis": legacy,
        "marker_counts": {phase: len(items) for phase, items in phases.items()},
        "phase_spans": spans, "vblank_entry_to_rte": vblank_spans,
        "queue_by_frame": queue_summary, "renderer_buffers": buffers,
        "irq_phase_inference": irq_phase,
        "timing_scope": "Raw spans use BlastEm master clocks /7; main/gameplay spans include intervening interrupts. Nominal IRQ entries are inferred from frame720 (after explicit WAIT at719), cross-checked against719, plus262*3420 clocks; frame724 is NOT used as a phase anchor because an entry can already be delayed while still inside blank. Instruction-boundary jitter is retained.",
        "conclusion": "bounded reset-overlay protocol captured; cycle/blank budget " + ("failed" if budget_errors else "passed") + ("; protocol errors present" if errors else ""),
    }


def _staged_console_helper_payload(
    process_id: int, script: str, timeout_seconds: float, *, ready_console_only: bool = False,
    canonical_prefix: bool = False,
) -> dict[str, Any]:
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
    access, sharing, open_existing = 0xC0000000, 0x00000003, 3
    console_in = kernel32.CreateFileW(
        "CONIN$", access, sharing, None, open_existing, 0, None
    )
    console_out = kernel32.CreateFileW(
        "CONOUT$", access, sharing, None, open_existing, 0, None
    )
    invalid = ctypes.c_void_p(-1).value
    if console_in in {None, invalid} or console_out in {None, invalid}:
        raise EvidenceFailure("could not open owned CONIN$/CONOUT$")
    capacity = prefix_console_capacity(script) if canonical_prefix else None
    if capacity:
        kernel32.SetConsoleScreenBufferSize.argtypes = (wintypes.HANDLE, Coord)
        kernel32.SetConsoleScreenBufferSize.restype = wintypes.BOOL
        if not kernel32.SetConsoleScreenBufferSize(console_out, Coord(capacity["width"], capacity["height"])):
            raise EvidenceFailure("owned prefix console bounded resize failed")

    def read_console() -> tuple[str, dict[str, int]]:
        info = ConsoleInfo()
        if not kernel32.GetConsoleScreenBufferInfo(
            console_out, ctypes.byref(info)
        ):
            raise EvidenceFailure("GetConsoleScreenBufferInfo failed")
        if capacity and (info.size.X, info.size.Y) != (capacity["width"], capacity["height"]):
            failure = EvidenceFailure("prefix owned console geometry changed before bounded read")
            failure.console_geometry = {"width": info.size.X, "height": info.size.Y, "cursor_x": info.cursor.X, "cursor_y": info.cursor.Y}
            raise failure
        count = info.size.X * (info.cursor.Y + 1)
        buffer = ctypes.create_unicode_buffer(count + 1)
        read = wintypes.DWORD()
        if not kernel32.ReadConsoleOutputCharacterW(
            console_out,
            buffer,
            count,
            Coord(0, 0),
            ctypes.byref(read),
        ):
            raise EvidenceFailure("ReadConsoleOutputCharacterW failed")
        raw = buffer[: read.value]
        lines = [
            raw[offset : offset + info.size.X].rstrip()
            for offset in range(0, len(raw), info.size.X)
        ]
        transcript = "\n".join(lines).rstrip() + "\n"
        geometry = {
            "width": info.size.X,
            "height": info.size.Y,
            "cursor_x": info.cursor.X,
            "cursor_y": info.cursor.Y,
            "current_row_utf16": [ord(value) for value in raw[info.cursor.Y * info.size.X : (info.cursor.Y + 1) * info.size.X]],
            "raw_screen_buffer_utf16le_sha256": _sha256_bytes(raw.encode("utf-16-le")),
            "raw_screen_buffer": raw,
        }
        if capacity and ((info.size.X, info.size.Y) != (capacity["width"], capacity["height"]) or
                         info.cursor.Y >= info.size.Y - 1 or len(transcript.encode("utf-8")) > PREFIX_MAX_CONSOLE_BYTES):
            failure = EvidenceFailure("prefix console geometry/capacity bound violated; possible scrolling/truncation")
            failure.console_text = transcript
            failure.console_geometry = geometry
            raise failure
        return transcript, geometry

    def peek_console_input() -> dict[str, Any]:
        return native._peek_owned_console_input(kernel32, console_in, InputRecord)

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
        ) or written.value != len(records):
            raise EvidenceFailure("WriteConsoleInputW failed or truncated")
        return written.value

    def wait_for(marker: str) -> tuple[str, dict[str, int]]:
        transcript = ""
        geometry: dict[str, int] = {}
        while time.monotonic() < deadline:
            transcript, geometry = read_console()
            lines = [line.strip() for line in transcript.splitlines()]
            if canonical_prefix and "R94C LIMIT" in lines:
                failure = EvidenceFailure("prefix physical/attempt fail-stop reached; no further host input sent")
                failure.console_text = transcript
                failure.console_geometry = geometry
                failure.console_input_peek = peek_console_input()
                raise failure
            if ready_console_only and marker == READY_MARKER and marker in lines:
                failure = EvidenceFailure("READY-only console diagnostic; no gameplay input or continue sent")
                failure.console_text = transcript
                failure.console_geometry = geometry
                failure.console_input_peek = peek_console_input()
                raise failure
            if marker in lines and lines[-1:] == [">"]:
                return transcript, geometry
            time.sleep(0.02)
        tail = "\n".join(transcript.splitlines()[-40:])
        failure = EvidenceFailure(f"console marker timeout {marker!r}:\n{tail}")
        failure.console_text = transcript
        failure.console_geometry = geometry
        failure.console_input_peek = peek_console_input()
        raise failure

    initial = ""
    geometry: dict[str, int] = {}
    while time.monotonic() < deadline:
        initial, geometry = read_console()
        if "200: move #$2700, SR" in initial and any(
            line.strip() == ">" for line in initial.splitlines()
        ):
            break
        time.sleep(0.02)
    else:
        failure = EvidenceFailure("native debugger initial prompt timed out")
        failure.console_text = initial
        failure.console_geometry = geometry
        failure.console_input_peek = peek_console_input()
        raise failure

    script_written = send_console(script)
    _ready, geometry = wait_for(READY_MARKER)
    remaining = max(0.1, deadline - time.monotonic())
    window = native._find_game_window(process_id, min(20.0, remaining))
    pressed: set[str] = set()
    input_events: list[str] = []
    b03._set_gameplay_mask(
        window["handle"], b03.INPUT_UP, pressed, input_events
    )
    continue_counts = [send_console("continue\n")]
    stages = [(item["marker"], item["desired"], item["total"]) for item in prefix_input_stages()] if canonical_prefix else [(marker, desired, None) for marker, desired in INPUT_MARKERS]

    def verify_prefix_input(transcript: str, geometry: Mapping[str, Any], *, pre_count: int, complete: bool) -> None:
        try:
            failures = prefix_input_errors(parse_prefix_markers(transcript), pre_count=pre_count, complete=complete)
            if failures:
                raise EvidenceFailure("; ".join(failures))
        except EvidenceFailure as failure:
            failure.console_text = transcript
            failure.console_geometry = geometry
            failure.console_input_peek = peek_console_input()
            raise

    for marker, desired, pre_count in stages:
        _transcript, geometry = wait_for(marker)
        if canonical_prefix:
            verify_prefix_input(_transcript, geometry, pre_count=pre_count, complete=False)
        b03._set_gameplay_mask(
            window["handle"], desired, pressed, input_events
        )
        continue_counts.append(send_console("continue\n"))
    transcript, geometry = wait_for(COMPLETE_MARKER)
    if canonical_prefix:
        verify_prefix_input(transcript, geometry, pre_count=PREFIX_TOTAL_STEPS, complete=True)
    b03._set_gameplay_mask(window["handle"], 0, pressed, input_events)
    if canonical_prefix:
        final, geometry, quit_written = _finish_prefix_console(transcript, geometry, send_console)
    else:
        quit_written = send_console("quit\n")
        time.sleep(0.1)
        try:
            final, geometry = read_console()
        except EvidenceFailure:
            final = transcript + "> quit\n"
    kernel32.FreeConsole()
    return {
        "console_text": final,
        "script_input_records": script_written,
        "continue_input_records": continue_counts,
        "quit_input_records": quit_written,
        "input_window": window,
        "input_events": input_events,
        "console": geometry,
        **({"console_capacity": capacity, "input_proof_checked_before_each_next_mask": True} if canonical_prefix else {}),
    }


def _finish_prefix_console(transcript: str, geometry: Mapping[str, Any], send_console: Any) -> tuple[str, Mapping[str, Any], int]:
    """Retain the last fully observed prompt; never invent text during teardown.

    The caller has just checked COMPLETE, capacity and the full input proof.
    Successful quit delivery and the parent's zero process exit are separate
    cleanup evidence; they are not represented as an observed console echo.
    """
    try:
        written = send_console("quit\n")
    except EvidenceFailure as failure:
        failure.console_text = transcript
        failure.console_geometry = geometry
        raise
    return transcript, geometry, written


def _console_helper_main(args: argparse.Namespace) -> int:
    script = Path(args.command_file).read_text("utf-8", errors="strict")
    try:
        result = _staged_console_helper_payload(
            args.process_id, script, args.timeout_seconds,
            **({"ready_console_only": True} if getattr(args, "ready_console_only", False) else {}),
            **({"canonical_prefix": True} if getattr(args, "canonical_prefix", False) else {}),
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


def preserve_native_failure(
    report_path: Path, trace_path: Path, *, script: str,
    helper_payload: Mapping[str, Any], metadata: Mapping[str, Any],
) -> None:
    """Preserve a failed owned capture; never turn its partial console into proof."""
    if report_path == trace_path or report_path.exists() or trace_path.exists():
        raise EvidenceFailure("native failure outputs must be distinct new paths")
    console = str(helper_payload.get("console_text", ""))
    trace = _trace_text(script, console)
    report = {
        "schema": "r94-m5-b03-native-capture-failure-v1",
        "status": "diagnostic_fail", "canonical": False,
        "diagnostic_only": True, "timing_evidence_complete": False,
        "failure": dict(helper_payload), "metadata": dict(metadata),
        "command_script_sha256": _sha256_bytes(script.encode()),
        "console_sha256": _sha256_bytes(console.encode()),
        "trace_sha256": _sha256_bytes(trace.encode()),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(trace)
    with report_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _trace_text(script: str, console: str) -> str:
    return (
        f"# {REPORT_SCHEMA}\n"
        "# NONCANONICAL RESET DIAGNOSTIC; native debugger; guest read-only\n"
        "----- COMMAND SCRIPT -----\n"
        f"{script.rstrip()}\n"
        "----- CONSOLE TRANSCRIPT -----\n"
        f"{console.rstrip()}\n"
    )


def derive_offline_reanalysis(
    root: Path,
    capture_report_path: Path,
    capture_trace_path: Path,
    *,
    completed_utc: str | None = None,
) -> dict[str, Any]:
    """Reanalyse one immutable native capture without starting an emulator."""
    root = root.resolve()
    capture_report_path = capture_report_path.resolve()
    capture_trace_path = capture_trace_path.resolve()
    if sha256_file(capture_report_path) != CAPTURE_REPORT_SHA256:
        raise EvidenceFailure("preserved repaired capture report hash drift")
    if sha256_file(capture_trace_path) != CAPTURE_TRACE_SHA256:
        raise EvidenceFailure("preserved repaired capture trace hash drift")
    capture = json.loads(
        capture_report_path.read_text("utf-8", errors="strict")
    )
    trace = capture_trace_path.read_text("utf-8", errors="strict")
    delimiter = "----- CONSOLE TRANSCRIPT -----\n"
    if trace.count(delimiter) != 1:
        raise EvidenceFailure("preserved trace console delimiter mismatch")
    console = trace.split(delimiter, 1)[1]
    script = capture.get("command_script")
    if not isinstance(script, str) or _trace_text(script, console) != trace:
        raise EvidenceFailure("preserved trace does not reconstruct exactly")

    records = parse_markers(console)
    analysis = analyse(records)
    console_errors = console_contract_errors(
        console, script, renderer_end_pc=CAPTURE_PC["renderer_end"]
    )
    policy_errors = offline_capture_command_policy_errors(script)
    expected_original_analysis_errors = [
        "reset bank-epoch snapshot did not prove transition"
    ]
    expected_original_console_errors = [
        "console stop lifecycle mismatch for 'R94R INPUT RESET 02'"
    ]
    semantic_checks = {
        "capture_schema_exact": capture.get("schema") == REPORT_SCHEMA,
        "capture_stored_status_is_fixture_fail": (
            capture.get("status") == "diagnostic_fail"
        ),
        "capture_rom_pin_exact": (
            capture.get("provenance", {}).get("rom", {}).get("sha256")
            == CAPTURE_ROM_SHA256
        ),
        "capture_symbol_pin_exact": (
            capture.get("provenance", {}).get("symbols", {}).get("sha256")
            == CAPTURE_SYMBOL_SHA256
        ),
        "capture_collector_hash_exact": (
            capture.get("provenance", {}).get("collector", {}).get("sha256")
            == CAPTURE_COLLECTOR_SHA256
        ),
        "capture_trace_provenance_exact": (
            capture.get("provenance", {}).get("trace", {}).get("sha256")
            == CAPTURE_TRACE_SHA256
        ),
        "trace_reconstructs_exactly": True,
        "command_script_hash_exact": (
            capture.get("command_script_sha256")
            == _sha256_bytes(script.encode("utf-8"))
            == CAPTURE_COMMAND_SCRIPT_SHA256
        ),
        "command_script_frozen_exact": (
            _sha256_bytes(script.encode("utf-8"))
            == CAPTURE_COMMAND_SCRIPT_SHA256
        ),
        "command_policy_pass": not policy_errors,
        "console_hash_exact": (
            capture.get("console_sha256")
            == _sha256_bytes(console.encode("utf-8"))
            == CAPTURE_CONSOLE_SHA256
        ),
        "console_contract_reanalysis_pass": not console_errors,
        "records_parse_exact": records == capture.get("records"),
        "records_hash_exact": (
            capture.get("records_sha256")
            == _sha256_bytes(
                json.dumps(
                    records, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            == CAPTURE_RECORDS_SHA256
        ),
        "capture_pc_contract_exact": (
            _sha256_bytes(
                json.dumps(
                    capture.get("pc_contract"),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            == CAPTURE_PC_CONTRACT_SHA256
        ),
        "original_analysis_error_exact": (
            capture.get("analysis", {}).get("errors")
            == expected_original_analysis_errors
        ),
        "original_console_error_exact": (
            capture.get("read_only_contract", {}).get(
                "console_contract_errors"
            )
            == expected_original_console_errors
        ),
        "corrected_analysis_pass": analysis.get("status") == "pass",
        "corrected_analysis_has_no_errors": not analysis.get("errors"),
        "source_debugger_writes_zero": all(
            capture.get("read_only_contract", {}).get(field) == 0
            for field in (
                "guest_memory_writes",
                "guest_register_writes",
                "guest_pc_writes",
            )
        ),
        "workspace_build_not_used_as_capture_input": True,
    }
    failed = [name for name, passed in semantic_checks.items() if not passed]
    if failed:
        raise EvidenceFailure(
            "offline reanalysis semantic checks failed: " + ", ".join(failed)
        )

    helper_path = root / COLLECTOR_RELATIVE_PATH
    test_path = root / TEST_RELATIVE_PATH
    workspace_build_scope = {
        "note": (
            "Mutable B03 build outputs advanced after the captured scheduler-"
            "repair run; they were not consulted and are not inputs to this "
            "derivation."
        ),
        "advanced_after_capture": True,
        "consulted_during_reanalysis": False,
        "used_as_capture_input": False,
    }
    report = {
        "schema": OFFLINE_REPORT_SCHEMA,
        "evidence_type": OFFLINE_EVIDENCE_TYPE,
        "status": "diagnostic_pass",
        "canonical": False,
        "diagnostic_only": True,
        "derivation": "offline_reanalysis_no_emulator_rerun",
        "completed_utc": completed_utc
        or datetime.now(timezone.utc).isoformat(),
        "source_capture": {
            "report": _file_record(capture_report_path, root),
            "trace": _file_record(capture_trace_path, root),
            "stored_status": capture["status"],
            "stored_collector_sha256": capture["provenance"]["collector"][
                "sha256"
            ],
            "command_script_sha256": capture["command_script_sha256"],
            "console_sha256": capture["console_sha256"],
            "records_sha256": capture["records_sha256"],
            "record_count": len(records),
            "original_analysis_errors": expected_original_analysis_errors,
            "original_console_contract_errors": (
                expected_original_console_errors
            ),
        },
        "corrections": {
            "bank_epoch": {
                "old_fixture_assumption": "physical bank 0 -> 1",
                "observed_canonical_transition": "physical bank 6 -> 0",
                "transition_still_exact": True,
            },
            "gameplay_pre_pc": {
                "old_console_fixture": "2F2",
                "current_pinned_pc": "2FA",
                "breakpoint_and_rom_contract_exact": True,
            },
        },
        "checks": semantic_checks,
        "command_policy_errors": policy_errors,
        "console_contract_errors": console_errors,
        "analysis": analysis,
        "read_only_contract": {
            "emulator_processes_started": 0,
            "debugger_commands_sent": 0,
            "guest_memory_writes": 0,
            "guest_register_writes": 0,
            "guest_pc_writes": 0,
            "source_capture_modified": False,
        },
        "provenance": {
            "captured_b03_rom": capture["provenance"]["rom"],
            "captured_b03_symbols": capture["provenance"]["symbols"],
            "captured_pc_contract": capture["pc_contract"],
            "captured_pc_contract_sha256": CAPTURE_PC_CONTRACT_SHA256,
            "current_helper": _file_record(helper_path, root),
            "current_test": _file_record(test_path, root),
            "workspace_build_scope": workspace_build_scope,
        },
        "conclusion": analysis["conclusion"],
        "limitations": [
            "This is a corrected offline derivation from one preserved native capture, not a second emulator session.",
            "It remains noncanonical and does not replace the full two-session budget gate.",
        ],
    }
    validation_errors = offline_reanalysis_errors(report, root)
    if validation_errors:
        raise EvidenceFailure(
            "derived offline report failed self-audit: "
            + "; ".join(validation_errors)
        )
    return report


def offline_reanalysis_errors(
    report: Mapping[str, Any], root: Path
) -> list[str]:
    """Fail closed on the identity and no-rerun contract of a derived report."""
    errors: list[str] = []
    root = root.resolve()
    if report.get("schema") != OFFLINE_REPORT_SCHEMA:
        errors.append("offline schema mismatch")
    if report.get("evidence_type") != OFFLINE_EVIDENCE_TYPE:
        errors.append("offline evidence type mismatch")
    if report.get("status") != "diagnostic_pass":
        errors.append("offline status is not diagnostic_pass")
    if (
        report.get("canonical") is not False
        or report.get("diagnostic_only") is not True
    ):
        errors.append("offline diagnostic/canonical classification mismatch")
    if report.get("derivation") != "offline_reanalysis_no_emulator_rerun":
        errors.append("offline derivation label mismatch")
    expected_corrections = {
        "bank_epoch": {
            "old_fixture_assumption": "physical bank 0 -> 1",
            "observed_canonical_transition": "physical bank 6 -> 0",
            "transition_still_exact": True,
        },
        "gameplay_pre_pc": {
            "old_console_fixture": "2F2",
            "current_pinned_pc": "2FA",
            "breakpoint_and_rom_contract_exact": True,
        },
    }
    if report.get("corrections") != expected_corrections:
        errors.append("offline correction contract mismatch")
    source = report.get("source_capture", {})
    if source.get("report", {}).get("sha256") != CAPTURE_REPORT_SHA256:
        errors.append("offline source report hash mismatch")
    if source.get("trace", {}).get("sha256") != CAPTURE_TRACE_SHA256:
        errors.append("offline source trace hash mismatch")
    if source.get("stored_collector_sha256") != CAPTURE_COLLECTOR_SHA256:
        errors.append("offline source collector hash mismatch")
    if source.get("stored_status") != "diagnostic_fail":
        errors.append("offline source stored status mismatch")
    read_only = report.get("read_only_contract", {})
    expected_zero = (
        "emulator_processes_started",
        "debugger_commands_sent",
        "guest_memory_writes",
        "guest_register_writes",
        "guest_pc_writes",
    )
    if any(read_only.get(field) != 0 for field in expected_zero):
        errors.append("offline no-emulator/read-only counters mismatch")
    if read_only.get("source_capture_modified") is not False:
        errors.append("offline source-modification claim mismatch")
    checks = report.get("checks", {})
    if set(checks) != OFFLINE_CHECK_NAMES:
        errors.append("offline semantic check set mismatch")
    if not checks or any(value is not True for value in checks.values()):
        errors.append("offline semantic checks are not all exact-pass")
    if report.get("command_policy_errors") != []:
        errors.append("offline command policy errors present")
    if report.get("console_contract_errors") != []:
        errors.append("offline console contract errors present")
    analysis = report.get("analysis", {})
    if analysis.get("status") != "pass" or analysis.get("errors") != []:
        errors.append("offline corrected analysis is not exact pass")
    if report.get("conclusion") != analysis.get("conclusion"):
        errors.append("offline conclusion does not match corrected analysis")
    provenance = report.get("provenance", {})
    current_paths = {
        "current_helper": root / COLLECTOR_RELATIVE_PATH,
        "current_test": root / TEST_RELATIVE_PATH,
    }
    for name, path in current_paths.items():
        if not path.is_file() or provenance.get(name) != _file_record(
            path, root
        ):
            errors.append(f"offline current provenance mismatch: {name}")
    captured_rom = provenance.get("captured_b03_rom")
    captured_symbols = provenance.get("captured_b03_symbols")
    if captured_rom != {
        "path": ROM_RELATIVE_PATH,
        "bytes": 1048576,
        "sha256": CAPTURE_ROM_SHA256,
    }:
        errors.append("offline captured B03 ROM provenance mismatch")
    if captured_symbols != {
        "path": SYMBOL_RELATIVE_PATH,
        "bytes": 86869,
        "sha256": CAPTURE_SYMBOL_SHA256,
    }:
        errors.append("offline captured B03 symbol provenance mismatch")
    workspace = provenance.get("workspace_build_scope", {})
    if workspace != {
        "note": (
            "Mutable B03 build outputs advanced after the captured scheduler-"
            "repair run; they were not consulted and are not inputs to this "
            "derivation."
        ),
        "advanced_after_capture": True,
        "consulted_during_reanalysis": False,
        "used_as_capture_input": False,
    }:
        errors.append("offline mutable-workspace distinction mismatch")
    capture_paths = {
        "report": root / CAPTURE_REPORT_RELATIVE_PATH,
        "trace": root / CAPTURE_TRACE_RELATIVE_PATH,
    }
    for name, path in capture_paths.items():
        if not path.is_file() or source.get(name) != _file_record(path, root):
            errors.append(f"offline source file drift: {name}")
    try:
        capture = json.loads(
            capture_paths["report"].read_text("utf-8", errors="strict")
        )
        trace = capture_paths["trace"].read_text("utf-8", errors="strict")
        delimiter = "----- CONSOLE TRANSCRIPT -----\n"
        if trace.count(delimiter) != 1:
            raise EvidenceFailure("source trace console delimiter mismatch")
        console = trace.split(delimiter, 1)[1]
        script = capture.get("command_script")
        if not isinstance(script, str) or _trace_text(script, console) != trace:
            raise EvidenceFailure("source trace reconstruction mismatch")
        records = parse_markers(console)
        recomputed_analysis = analyse(records)
        expected_source_fields = {
            "command_script_sha256": CAPTURE_COMMAND_SCRIPT_SHA256,
            "console_sha256": CAPTURE_CONSOLE_SHA256,
            "records_sha256": CAPTURE_RECORDS_SHA256,
        }
        for name, expected in expected_source_fields.items():
            if source.get(name) != expected or capture.get(name) != expected:
                errors.append(f"offline source {name} mismatch")
        if source.get("record_count") != len(records) or len(
            capture.get("records", [])
        ) != len(records):
            errors.append("offline source record_count mismatch")
        if report.get("analysis") != recomputed_analysis:
            errors.append("offline analysis does not match source records")
        if report.get("console_contract_errors") != console_contract_errors(
            console,
            script,
            renderer_end_pc=CAPTURE_PC["renderer_end"],
        ):
            errors.append("offline console audit does not match source trace")
        if report.get(
            "command_policy_errors"
        ) != offline_capture_command_policy_errors(script):
            errors.append("offline command audit does not match source script")
        if source.get("original_analysis_errors") != capture.get(
            "analysis", {}
        ).get("errors"):
            errors.append("offline original analysis errors mismatch")
        if source.get("original_console_contract_errors") != capture.get(
            "read_only_contract", {}
        ).get("console_contract_errors"):
            errors.append("offline original console errors mismatch")
        if provenance.get("captured_b03_rom") != capture.get(
            "provenance", {}
        ).get("rom"):
            errors.append("offline captured ROM does not match source report")
        if provenance.get("captured_b03_symbols") != capture.get(
            "provenance", {}
        ).get("symbols"):
            errors.append("offline captured symbols do not match source report")
        if provenance.get("captured_pc_contract") != capture.get("pc_contract"):
            errors.append("offline captured PC contract mismatch")
        if provenance.get(
            "captured_pc_contract_sha256"
        ) != CAPTURE_PC_CONTRACT_SHA256:
            errors.append("offline captured PC contract hash mismatch")
    except (EvidenceFailure, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        errors.append(f"offline source reanalysis failed: {error}")
    return errors


def write_offline_reanalysis(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    output = Path(args.offline_output).resolve()
    immutable_sources = {
        Path(args.capture_report).resolve(),
        Path(args.capture_trace).resolve(),
    }
    if output in immutable_sources:
        raise EvidenceFailure("offline output may not replace a source capture")
    report = derive_offline_reanalysis(
        root,
        Path(args.capture_report),
        Path(args.capture_trace),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report


def collect(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise EvidenceFailure("live reset diagnostic requires native Windows")
    root = Path(args.root).resolve()
    rom_path = root / ROM_RELATIVE_PATH
    symbols_path = root / SYMBOL_RELATIVE_PATH
    emulator_dir = root / ".toolcache" / "blastem"
    emulator = emulator_dir / "blastem.exe"
    default_config = emulator_dir / "default.cfg"
    collector_path = root / COLLECTOR_RELATIVE_PATH
    regular_path = root / REGULAR_HELPER_RELATIVE_PATH
    report_path = Path(args.report).resolve()
    trace_path = Path(args.trace).resolve()
    extended_overlay = bool(getattr(args, "extended_overlay", False))
    canonical_prefix = bool(getattr(args, "canonical_prefix", False))
    profile = getattr(args, "profile", "baseline")
    runtime_sources = regular.crc_projection_source_contract(root) if profile == "crc_projection" else regular.renderer_hwm_source_contract(root) if profile == "renderer_hwm" else {}
    if report_path == trace_path or report_path.exists() or trace_path.exists():
        raise EvidenceFailure("live diagnostic outputs must be distinct new paths")
    if canonical_prefix and (extended_overlay or getattr(args, "ready_console_only", False)):
        raise EvidenceFailure("canonical-prefix mode cannot be combined with other live diagnostic modes")
    if canonical_prefix and not 0 < args.timeout_seconds <= 240:
        raise EvidenceFailure("prefix timeout must remain positive and bounded to 240 seconds")
    if extended_overlay or canonical_prefix:
        protected = {root / name for name in (DEFAULT_REPORT_RELATIVE_PATH, DEFAULT_TRACE_RELATIVE_PATH, CAPTURE_REPORT_RELATIVE_PATH, CAPTURE_TRACE_RELATIVE_PATH, DEFAULT_OFFLINE_REPORT_RELATIVE_PATH)}
        if report_path in protected or trace_path in protected or report_path == trace_path:
            raise EvidenceFailure("extended capture requires distinct explicit new output paths")
        if report_path.exists() or trace_path.exists():
            raise EvidenceFailure("extended diagnostic outputs already exist; preserve them and choose new paths")
    for path, label in (
        (rom_path, "B03 Debug ROM"),
        (symbols_path, "B03 symbols"),
        (emulator, "pinned BlastEm"),
        (default_config, "pinned config"),
        (collector_path, "reset diagnostic"),
        (regular_path, "frozen regular checkpoint diagnostic"),
    ):
        if not path.is_file():
            raise EvidenceFailure(f"{label} missing: {path}")
    for path, expected, label in (
        (rom_path, PROFILE_ROM_PINS[profile], "ROM"),
        (symbols_path, PROFILE_SYMBOL_PINS[profile], "symbols"),
        (emulator, PINNED_EMULATOR_SHA256, "emulator"),
        (default_config, PINNED_DEFAULT_CONFIG_SHA256, "config"),
    ):
        if sha256_file(path) != expected:
            raise EvidenceFailure(f"pinned {label} hash drift")
    verify_no_user_blastem_override()
    symbols = parse_vasm_symbols(symbols_path)
    required_symbols = {
        "DEBUG_FRAME_COUNTER",
        "DEBUG_BOOT_STAGE",
        "DEBUG_FRAME_TOKEN",
        "DEBUG_SEQUENCE",
        "DEBUG_LAST_VDP_STATUS",
        "DEBUG_VBLANK_OVERRUN_COUNT",
        "DEBUG_DMA_WORDS_FRAME",
        "DEBUG_DMA_QUEUE_DEPTH",
        "ENGINE_ATTEMPT",
        "ENGINE_TICK",
        "ENGINE_STATE",
        "ENGINE_CHECKPOINT_COUNT",
        "ENGINE_STATE_CRC",
        "INPUT_HELD",
        "TIMELINE_CURRENT_BANK",
        "GAMEPLAY_LAST_BANK_CRC",
        "VOCAB_CAMPAIGN_FLAGS",
        "RENDERER_LAST_ATTEMPT",
        "RENDERER_LAST_CHECKPOINT_COUNT",
        "RENDERER_LAST_RESULT",
        "RENDERER_RESET_PHASE",
        "RENDERER_BACKLOG_FRAMES",
        "DMA_QUEUE_FRAME_PENDING",
        "DMA_QUEUE_PUBLISHED_DEPTH",
        "DMA_QUEUE_BUILD_DEPTH",
        "DMA_QUEUE_BUILD_WORDS",
        "DMA_QUEUE_COMMIT_WORDS",
        "DMA_QUEUE_ENTRIES",
        "GameplayBuildProjection",
        "GameplayCommitAndReset",
        "MainLoopCommitResetCall",
        "Crc32UpdateRaw",
        "RendererPrepareResetOverlay",
        *PC_SYMBOL_BINDINGS.values(),
    }
    if canonical_prefix:
        required_symbols.update(("ENGINE_INPUT_HELD", "ENGINE_ACTIVE_ACTOR_COUNT", "ENGINE_POOL_COUNTS"))
    missing = sorted(required_symbols - symbols.keys())
    if missing:
        raise EvidenceFailure("symbol map missing: " + ", ".join(missing))
    bound_pc = (extended_pc_contract if extended_overlay or canonical_prefix else pc_contract)(rom_path.read_bytes(), symbols, profile=profile)
    if canonical_prefix:
        route = prefix_route_contract(root)
        script = build_prefix_command_script(symbols, profile=profile)
        policy_errors = prefix_command_policy_errors(script, symbols, profile=profile)
        bound_pc = {**bound_pc, "prefix_projection_entry": symbols["GameplayBuildProjection"]}
    else:
        route = None
        script = build_command_script(symbols, extended_overlay=extended_overlay, profile=profile)
        policy_errors = command_policy_errors(script, symbols, extended_overlay=extended_overlay, profile=profile)
    if policy_errors:
        raise EvidenceFailure("; ".join(policy_errors))

    process: subprocess.Popen[Any] | None = None
    helper: subprocess.Popen[str] | None = None
    helper_result: dict[str, Any] | None = None
    runtime_copy: dict[str, Any] = {}
    controller: dict[str, Any] = {}
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(
        prefix="recursion94-m5-checkpoint-reset-"
    ) as text:
        temporary = Path(text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_dir, temporary
        )
        controller = write_standard_pad_profile(default_config, temporary)
        controller["session_isolation"] = apply_blastem_session_isolation(
            Path(controller["path"]), temporary / "session-data"
        )
        command_file = temporary / "checkpoint-reset-commands.txt"
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
        process = subprocess.Popen(
            launch,
            cwd=isolated_runtime,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10),
        )
        try:
            helper = subprocess.Popen(
                [
                    sys.executable,
                    str(collector_path),
                    "--console-helper",
                    "--process-id",
                    str(process.pid),
                    "--command-file",
                    str(command_file),
                    "--timeout-seconds",
                    str(args.timeout_seconds),
                    *(["--ready-console-only"] if getattr(args, "ready_console_only", False) else []),
                    *(["--canonical-prefix"] if canonical_prefix else []),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                stdout, stderr = helper.communicate(
                    timeout=args.timeout_seconds + 10
                )
            except subprocess.TimeoutExpired:
                preserve_native_failure(
                    report_path, trace_path, script=script,
                    helper_payload={"error": "parent helper communication timeout", "console_text": "", "full_console_available": False},
                    metadata={"rom_profile": profile, "timeout_seconds": args.timeout_seconds, "rom": _file_record(rom_path, root), "symbols": _file_record(symbols_path, root), "collector": _file_record(collector_path, root), "pc_contract": bound_pc},
                )
                raise EvidenceFailure("parent helper timeout; console unavailable, failure metadata preserved")
            if helper.returncode != 0:
                try:
                    failed_payload = json.loads(stdout)
                except json.JSONDecodeError:
                    failed_payload = {"error": stderr.strip(), "console_text": "", "full_console_available": False}
                preserve_native_failure(
                    report_path, trace_path, script=script,
                    helper_payload=failed_payload,
                    metadata={
                        "rom_profile": profile, "timeout_seconds": args.timeout_seconds,
                        "rom": _file_record(rom_path, root),
                        "symbols": _file_record(symbols_path, root),
                        "collector": _file_record(collector_path, root),
                        "emulator": _file_record(emulator, root),
                        "default_config": _file_record(default_config, root),
                        "native_console_helper": _file_record(root / regular.CONSOLE_HELPER_RELATIVE_PATH, root),
                        "ready_console_only": bool(getattr(args, "ready_console_only", False)),
                        "canonical_prefix": canonical_prefix,
                        "prefix_route": route,
                        "guest_writes": 0,
                        "pc_contract": bound_pc,
                        "helper_returncode": helper.returncode,
                        "helper_stderr": stderr,
                    },
                )
                raise EvidenceFailure(
                    f"console helper failed ({helper.returncode}); raw failure preserved: {failed_payload.get('error', stderr.strip())}"
                )
            helper_result = json.loads(stdout)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                preserve_native_failure(
                    report_path, trace_path, script=script,
                    helper_payload={**helper_result, "error": "owned emulator exit timeout", "full_console_available": True},
                    metadata={"rom_profile": profile, "timeout_seconds": args.timeout_seconds, "rom": _file_record(rom_path, root), "symbols": _file_record(symbols_path, root), "collector": _file_record(collector_path, root), "pc_contract": bound_pc},
                )
                raise EvidenceFailure("owned emulator exit timeout; completed console preserved as failure")
            if process.returncode != 0:
                if canonical_prefix:
                    preserve_native_failure(
                        report_path, trace_path, script=script,
                        helper_payload={**helper_result, "error": f"BlastEm exit code {process.returncode}", "full_console_available": True},
                        metadata={"rom_profile": profile, "canonical_prefix": True, "prefix_route": route,
                                  "rom": _file_record(rom_path, root), "symbols": _file_record(symbols_path, root),
                                  "collector": _file_record(collector_path, root), "pc_contract": bound_pc,
                                  "emulator": _file_record(emulator, root), "default_config": _file_record(default_config, root),
                                  "native_console_helper": _file_record(root / regular.CONSOLE_HELPER_RELATIVE_PATH, root),
                                  "input_transport": _file_record(root / "tools/m5_b03_runtime_evidence.py", root),
                                  "guest_writes": 0, "helper_returncode": helper.returncode, "emulator_returncode": process.returncode},
                    )
                elif profile in {"renderer_hwm", "crc_projection"} and extended_overlay:
                    preserve_native_failure(
                        report_path, trace_path, script=script,
                        helper_payload={**helper_result, "error": f"BlastEm exit code {process.returncode}", "full_console_available": True},
                        metadata={"rom_profile": profile, "extended_overlay": True, "timeout_seconds": args.timeout_seconds,
                                  "rom": _file_record(rom_path, root), "symbols": _file_record(symbols_path, root),
                                  "collector": _file_record(collector_path, root), "pc_contract": bound_pc,
                                  "emulator": _file_record(emulator, root), "default_config": _file_record(default_config, root),
                                  "native_console_helper": _file_record(root / regular.CONSOLE_HELPER_RELATIVE_PATH, root),
                                  "input_transport": _file_record(root / "tools/m5_b03_runtime_evidence.py", root),
                                  "candidate_runtime_sources": runtime_sources, "guest_writes": 0,
                                  "helper_returncode": helper.returncode, "helper_stderr": stderr,
                                  "emulator_returncode": process.returncode},
                    )
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

    console = helper_result["console_text"]
    console_errors = prefix_console_contract_errors(console, script) if canonical_prefix else console_contract_errors(console, script, extended_overlay=extended_overlay, renderer_end_pc=PROFILE_PC[profile]["renderer_end"])
    expected_events = prefix_expected_input_events() if canonical_prefix else [
        "Up:down",
        "Up:up",
        "Right:down",
        "Right:up",
        "Down:down",
        "Down:up",
    ]
    if helper_result.get("input_events") != expected_events:
        console_errors.append("host input transition event sequence mismatch")
    if canonical_prefix:
        try:
            records = parse_prefix_markers(console)
            analysis = analyse_prefix(records, route)
        except (EvidenceFailure, ValueError, KeyError) as error:
            preserve_native_failure(
                report_path, trace_path, script=script,
                helper_payload={**helper_result, "error": f"prefix parse/analysis failure: {error}", "full_console_available": True},
                metadata={"rom_profile": profile, "canonical_prefix": True, "prefix_route": route, "rom": _file_record(rom_path, root), "symbols": _file_record(symbols_path, root), "collector": _file_record(collector_path, root), "pc_contract": bound_pc},
            )
            raise EvidenceFailure("prefix parse/analysis failed; raw evidence preserved") from error
        if helper_result.get("console_capacity") != prefix_console_capacity(script) or helper_result.get("input_proof_checked_before_each_next_mask") is not True:
            console_errors.append("prefix owned-console capacity/staged input-proof metadata mismatch")
        if (helper_result.get("quit_input_records") != 5 or helper_result.get("script_input_records") != len(script) or
                helper_result.get("continue_input_records") != [9] * (len(prefix_input_stages()) + 1)):
            console_errors.append("prefix exact host command delivery counts mismatch")
    else:
        records = parse_markers(console, extended_overlay=extended_overlay)
        analysis = (analyse_extended if extended_overlay else analyse)(records)
    trace = _trace_text(script, console)
    selected_schema = PREFIX_REPORT_SCHEMA if canonical_prefix else EXTENDED_REPORT_SCHEMA if extended_overlay else REPORT_SCHEMA
    if extended_overlay or canonical_prefix:
        trace = trace.replace(f"# {REPORT_SCHEMA}\n", f"# {selected_schema}\n", 1)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(trace)
    report = {
        "schema": selected_schema,
        "evidence_type": EVIDENCE_TYPE,
        "status": (
            "diagnostic_pass"
            if analysis["status"] == "pass" and not console_errors
            else "diagnostic_fail"
        ),
        "canonical": False,
        "diagnostic_only": True,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "bounds": {
            "authoritative_totals": list(TARGET_TOTALS),
            "vblank_frames": list(TARGET_VBLANK_FRAMES),
            "frame_fail_limit": FAIL_CLOSED_FRAME_LIMIT,
            "maximum_vblanks": MAX_VBLANKS,
            "maximum_dma_calls": MAX_DMA_CALLS,
            "guest_debugger_writes": 0,
        },
        "read_only_contract": {
            "allowed_commands": sorted(ALLOWED_COMMANDS),
            "command_policy_errors": policy_errors,
            "console_contract_errors": console_errors,
            "guest_memory_writes": 0,
            "guest_register_writes": 0,
            "guest_pc_writes": 0,
        },
        "pc_contract": bound_pc,
        "host_input": {
            "window": helper_result["input_window"],
            "events": helper_result["input_events"],
            "stops": [item["marker"] for item in prefix_input_stages()] if canonical_prefix else [item[0] for item in INPUT_MARKERS],
        },
        "command_script": script,
        "command_script_sha256": _sha256_bytes(script.encode("utf-8")),
        "console_sha256": _sha256_bytes(console.encode("utf-8")),
        "console_geometry": helper_result["console"],
        "records": records,
        "records_sha256": _sha256_bytes(
            json.dumps(records, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ),
        "analysis": analysis,
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
            "frozen_regular_helper": _file_record(regular_path, root),
            "trace": _file_record(trace_path, root),
            "isolated_runtime": runtime_copy,
            "isolated_config": controller,
        },
        "limitations": [
            "Noncanonical diagnostic only; it cannot satisfy the full budget gate.",
            "Debugger stops do not advance BlastEm's emulated master clock.",
            "The run is bounded to the timeout/reset boundary, first fresh VBlank, and first next-attempt step.",
        ],
    }
    if extended_overlay:
        report["rom_profile"] = profile
        report["bounds"] = {
            "authoritative_totals": list(EXTENDED_TARGET_TOTALS),
            "vblank_frames": list(EXTENDED_TARGET_VBLANK_FRAMES),
            "frame_fail_limit": EXTENDED_FRAME_LIMIT,
            "maximum_vblanks": 12,
            "maximum_dma_calls": 72,
            "maximum_buffer_rows_expected": 390,
            "guest_debugger_writes": 0,
        }
        report["diagnostic_protocol_status"] = "pass" if analysis["protocol_status"] == "pass" and not console_errors else "fail"
        report["cycle_blank_budget_status"] = analysis["budget_status"]
        report["limitations"][-1] = "Bounded through authoritative total724 and physical frame726, with fail-stop at frame730; not a full-run budget proof."
    if canonical_prefix:
        report["rom_profile"] = profile
        report["evidence_type"] = "blastem_native_debugger_b03_canonical_prefix_sampled_phases"
        report["prefix_route"] = route
        report["console_capacity"] = helper_result["console_capacity"]
        report["bounds"] = {
            "authoritative_steps": PREFIX_TOTAL_STEPS, "final_attempt": PREFIX_FINAL_ATTEMPT,
            "final_tick": PREFIX_FINAL_TICK, "sampled_attempt": 2,
            "sampled_post_ticks": list(PREFIX_SAMPLE_POST_TICKS),
            "irq_engine_tick_windows_half_open": [list(pair) for pair in PREFIX_IRQ_TICK_RANGES],
            "physical_fail_stop_not_budget_allowance": PREFIX_PHYSICAL_FAIL_STOP,
            "maximum_records": PREFIX_MAX_RECORDS, "maximum_console_bytes": PREFIX_MAX_CONSOLE_BYTES,
            "reset_replay_rows": 640, "host_input_transition_stops": 21,
            "timeout_seconds": args.timeout_seconds, "guest_debugger_writes": 0,
        }
        report["diagnostic_protocol_status"] = "pass" if analysis["protocol_status"] == "pass" and not console_errors else "fail"
        report["cycle_blank_budget_status"] = analysis["budget_status"]
        report["limitations"][-1] = analysis["timing_scope"]
        report["limitations"].extend(analysis["protocol_domain_limitations"])
        report["provenance"]["input_transport"] = _file_record(root / "tools/m5_b03_runtime_evidence.py", root)
        report["provenance"]["native_console_helper"] = _file_record(root / regular.CONSOLE_HELPER_RELATIVE_PATH, root)
    if runtime_sources:
        report["provenance"]["candidate_runtime_sources"] = runtime_sources
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if report["status"] != "diagnostic_pass":
        raise EvidenceFailure("reset phase contract failed; artifacts preserved")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parent.parent)
    )
    parser.add_argument("--report", default=DEFAULT_REPORT_RELATIVE_PATH)
    parser.add_argument("--trace", default=DEFAULT_TRACE_RELATIVE_PATH)
    parser.add_argument("--extended-overlay", action="store_true", help="extend the live reset diagnostic through total724; requires explicit new report/trace paths")
    parser.add_argument("--canonical-prefix", action="store_true", help="observe the hash-bound 1681-step route and later-attempt phase samples; new outputs required; not a canonical budget gate")
    parser.add_argument("--ready-console-only", action="store_true", help="preserve one read-only READY console/input-queue diagnostic; never executes gameplay")
    parser.add_argument("--profile", choices=("baseline", "lookup", "sprite", "indexed", "pool", "highwater", "renderer_hwm", "crc_projection"), default="baseline")
    parser.add_argument("--extended-baseline-reanalyse", metavar="NEW_REPORT", help="write a new offline derivation from the pinned extended baseline without changing its capture")
    parser.add_argument("--lookup-compare", metavar="NEW_REPORT", help="write a pinned offline baseline/lookup-only timing and raw-buffer comparison")
    parser.add_argument("--sprite-compare", metavar="NEW_REPORT", help="write a pinned offline lookup-only/sprite-loop timing and raw-buffer comparison")
    parser.add_argument("--indexed-compare", metavar="NEW_REPORT", help="write a pinned offline sprite-loop/indexed-CRC timing and raw-buffer comparison")
    parser.add_argument("--pool-compare", metavar="NEW_REPORT", help="write a pinned offline indexed-CRC/empty-pool timing and raw-buffer comparison")
    parser.add_argument("--highwater-compare", metavar="NEW_REPORT", help="write a pinned offline empty-pool/high-water timing and raw-buffer comparison")
    parser.add_argument("--renderer-hwm-compare", metavar="NEW_REPORT", help="write a pinned offline high-water/renderer-HWM timing and raw-buffer comparison; first-attempt samples only")
    parser.add_argument("--prefix-reanalyse", metavar="NEW_REPORT", help="write a new hash-bound offline prefix reanalysis; preserve the original failed capture and every timing failure")
    parser.add_argument(
        "--offline-reanalyse",
        action="store_true",
        help="derive a new report from the pinned immutable capture; no emulator",
    )
    parser.add_argument(
        "--capture-report", default=CAPTURE_REPORT_RELATIVE_PATH
    )
    parser.add_argument("--capture-trace", default=CAPTURE_TRACE_RELATIVE_PATH)
    parser.add_argument(
        "--offline-output", default=DEFAULT_OFFLINE_REPORT_RELATIVE_PATH
    )
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
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
    if args.prefix_reanalyse:
        if any((args.canonical_prefix, args.extended_overlay, args.ready_console_only, args.offline_reanalyse,
                args.extended_baseline_reanalyse, args.lookup_compare, args.sprite_compare, args.indexed_compare,
                args.pool_compare, args.highwater_compare, args.renderer_hwm_compare)):
            raise EvidenceFailure("prefix offline reanalysis cannot be combined with another mode")
        output = Path(args.prefix_reanalyse)
        if not output.is_absolute():
            output = root / output
        if output.exists():
            raise EvidenceFailure("prefix reanalysis output already exists")
        report = derive_prefix_reanalysis(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"Prefix offline reanalysis: protocol={report['diagnostic_protocol_status']}, budget={report['cycle_blank_budget_status']}; original capture unchanged")
        return 0 if report["diagnostic_protocol_status"] == "pass" else 1
    if args.lookup_compare or args.sprite_compare or args.indexed_compare or args.pool_compare or args.highwater_compare or args.renderer_hwm_compare:
        if sum(bool(value) for value in (args.lookup_compare, args.sprite_compare, args.indexed_compare, args.pool_compare, args.highwater_compare, args.renderer_hwm_compare)) != 1:
            raise EvidenceFailure("choose exactly one isolated comparison")
        if args.renderer_hwm_compare and any((args.canonical_prefix, args.extended_overlay,
                args.ready_console_only, args.offline_reanalyse, args.extended_baseline_reanalyse)):
            raise EvidenceFailure("renderer-HWM offline comparison cannot be combined with another mode")
        output = Path(args.lookup_compare or args.sprite_compare or args.indexed_compare or args.pool_compare or args.highwater_compare or args.renderer_hwm_compare)
        if not output.is_absolute():
            output = root / output
        if output.exists():
            raise EvidenceFailure("lookup comparison output already exists")
        report = derive_lookup_comparison(root, candidate_profile="renderer_hwm" if args.renderer_hwm_compare else "highwater" if args.highwater_compare else "pool" if args.pool_compare else "indexed" if args.indexed_compare else "sprite" if args.sprite_compare else "lookup")
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.renderer_hwm_compare:
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        else:
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"Isolated timing comparison: protocol={report['diagnostic_protocol_status']}, parity={report['parity_status']}, budget={report['cycle_blank_budget_status']}")
        return 0 if report["diagnostic_protocol_status"] == report["parity_status"] == "pass" else 1
    if args.extended_baseline_reanalyse:
        output = Path(args.extended_baseline_reanalyse)
        if not output.is_absolute():
            output = root / output
        if output.exists():
            raise EvidenceFailure("extended reanalysis output already exists")
        report = derive_extended_baseline(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"Extended baseline reanalysed: protocol={report['diagnostic_protocol_status']}, budget={report['cycle_blank_budget_status']}")
        return 0 if report["diagnostic_protocol_status"] == "pass" else 1
    if args.offline_reanalyse:
        for name in ("capture_report", "capture_trace", "offline_output"):
            value = Path(getattr(args, name))
            if not value.is_absolute():
                value = root / value
            setattr(args, name, str(value.resolve()))
        try:
            report = write_offline_reanalysis(args)
        except (
            EvidenceFailure,
            OSError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as error:
            print(
                f"M5 B03 checkpoint reset offline reanalysis failed: {error}",
                file=sys.stderr,
            )
            return 1
        print(
            "M5 B03 noncanonical checkpoint reset offline reanalysis passed: "
            f"{report['conclusion']}"
        )
        return 0
    if not Path(args.report).is_absolute():
        args.report = str(root / args.report)
    if not Path(args.trace).is_absolute():
        args.trace = str(root / args.trace)
    try:
        report = collect(args)
    except (
        EvidenceFailure,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"M5 B03 checkpoint reset diagnostic failed: {error}", file=sys.stderr)
        return 1
    print(
        "M5 B03 noncanonical checkpoint reset diagnostic passed: "
        f"{report['analysis']['conclusion']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
