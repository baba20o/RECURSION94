#!/usr/bin/env python3
"""Read-only native-cycle diagnosis of the B03 tick-60 checkpoint repair.

This helper is deliberately noncanonical.  It holds Up, observes only the
bounded tick-59 through tick-61 interval, and uses BlastEm's native debugger
to record emulated cycle boundaries.  It never writes guest memory, registers,
or PC.  The original fault analyser remains available solely to audit the
preserved pre-repair artifact.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


REPORT_SCHEMA = "r94-m5-b03-budget-checkpoint-repair-phase-diagnostic-v2"
EVIDENCE_TYPE = (
    "blastem_native_debugger_b03_tick60_checkpoint_repair_phase"
)
READY_MARKER = native.READY_MARKER
COMPLETE_MARKER = native.COMPLETE_MARKER
COLLECTOR_RELATIVE_PATH = "tools/m5_budget_checkpoint_phase_diagnostic.py"
CONSOLE_HELPER_RELATIVE_PATH = "tools/m5_budget_phase_diagnostic.py"
ROM_RELATIVE_PATH = "build/level-B03/recursion94-debug.bin"
SYMBOL_RELATIVE_PATH = "build/level-B03/recursion94-debug.sym"
EMULATOR_RELATIVE_PATH = ".toolcache/blastem/blastem.exe"
CONFIG_RELATIVE_PATH = ".toolcache/blastem/default.cfg"
DEFAULT_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "overrun-checkpoint-phase-post-repair-tick59-tick61-20260830.json"
)
DEFAULT_TRACE_RELATIVE_PATH = (
    "artifacts/diagnostics/m5-budget/"
    "overrun-checkpoint-phase-post-repair-tick59-tick61-20260830.log"
)

PINNED_ROM_SHA256 = (
    "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
)
PINNED_SYMBOL_SHA256 = (
    "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
)
PINNED_EMULATOR_SHA256 = (
    "22b46b9476f5fe208455d7823bb403eb6e49c467296475bca300efabaf757803"
)
PINNED_DEFAULT_CONFIG_SHA256 = (
    "27cf0b11a6e551f4427608ee32cc94018f82f9c655ba6eb5a5e69ecf8b1aba4e"
)
BLASTEM_SOURCE_NODE = native.BLASTEM_SOURCE_NODE
BLASTEM_VERSION = native.BLASTEM_VERSION

BOOT_STAGE_RUNNING = 4
UP_INPUT_MASK = 0x01
MASTER_CLOCKS_PER_M68K_CYCLE = 7
NTSC_FRAME_MASTER_CLOCKS = 262 * 3420
TARGET_PRE_TICKS = (58, 59)
TARGET_POST_TICKS = (59, 60)
TARGET_VBLANK_CURRENT_FRAMES = tuple(range(59, 64))
EXPECTED_ORIGIN_FRAME = 63
EXPECTED_DMA_CALLS_WHEN_PENDING = 4
MAX_VBLANKS = len(TARGET_VBLANK_CURRENT_FRAMES)
MAX_DMA_CALLS = MAX_VBLANKS * EXPECTED_DMA_CALLS_WHEN_PENDING

REPAIR_TARGET_PRE_TICKS = (58, 59, 60)
REPAIR_TARGET_POST_TICKS = (59, 60, 61)
REPAIR_TARGET_VBLANK_CURRENT_FRAMES = tuple(range(59, 63))
REPAIR_COMPLETE_FRAME = 62
REPAIR_MAX_VBLANKS = len(REPAIR_TARGET_VBLANK_CURRENT_FRAMES)
REPAIR_MAX_DMA_CALLS = (
    REPAIR_MAX_VBLANKS * EXPECTED_DMA_CALLS_WHEN_PENDING
)

# Every internal boundary is bound to exact bytes of the pinned ROM below.
PC = {
    "gameplay_call": 0x000002F2,
    "gameplay_return": 0x000002F6,
    "crc_start": 0x00001BC0,
    "crc_end": 0x00001BD2,
    "engine_projection_start": 0x0000212C,
    "engine_projection_end": 0x00002344,
    "tail_start": 0x00005600,
    "tail_end": 0x00005614,
    "campaign_projection_start": 0x000069DA,
    "campaign_projection_end": 0x00006A7A,
    "renderer_start": 0x00008AD4,
    "renderer_end": 0x00008C58,
    "dma_commit": 0x0000124C,
    "dma_issue_start": 0x00001422,
    "dma_issue_end": 0x000014C8,
    "vblank_entry": 0x0000167C,
    "vblank_ack": 0x0000169E,
    "vblank_post_dma": 0x000016C2,
    "vblank_status": 0x000016FC,
    "vblank_origin": 0x0000170E,
    "vblank_common": 0x0000171E,
    "vblank_rte": 0x00001832,
}
PC_SYMBOL_BINDINGS = {
    "gameplay_call": "MainLoopGameplayStepCall",
    "gameplay_return": "MainLoopPostGameplayStepCall",
    "crc_start": "Crc32IsoHdlc",
    "engine_projection_start": "EngineBuildProjection",
    "tail_start": "VocabularyBuildProjectionTail",
    "campaign_projection_start": "GameplayBuildProjection",
    "dma_commit": "DmaCommitPreparedQueue",
    "dma_issue_start": "DmaIssueVramTransfer",
    "vblank_entry": "VBlankInterrupt",
}

# Exact internal boundaries for the hardened checkpoint-repair ROM.  These are
# intentionally separate from PC above so the preserved pre-repair transcript
# remains independently analysable with its original address contract.
REPAIR_PC = {
    "gameplay_call": 0x000002FA,
    "gameplay_return": 0x000002FE,
    "raw_crc_start": 0x00006A62,
    "raw_crc_end": 0x00006A66,
    "engine_projection_start": 0x0000214A,
    "engine_projection_end": 0x00002362,
    "tail_start": 0x0000561E,
    "tail_end": 0x00005632,
    "campaign_projection_start": 0x000069F8,
    "campaign_projection_end": 0x00006AA8,
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
REPAIR_PC_SYMBOL_BINDINGS = {
    "gameplay_call": "MainLoopGameplayStepCall",
    "gameplay_return": "MainLoopPostGameplayStepCall",
    "engine_projection_start": "EngineBuildProjection",
    "tail_start": "VocabularyBuildProjectionTail",
    "campaign_projection_start": "GameplayBuildProjection",
    "dma_commit": "DmaCommitPreparedQueue",
    "dma_issue_start": "DmaIssueVramTransfer",
    "vblank_entry": "VBlankInterrupt",
}
LOOKUP_ROM_SHA256 = "9a7416427e2fda17831ba769e5bcb57b74dda552e965cdff1c82575abacbed87"
LOOKUP_SYMBOL_SHA256 = "b8ba7740e63f4ed4dd27e3fe4f1dfb659d12ae2dea1aefc3a7ebc97c3cabab67"
LOOKUP_REPAIR_PC = {
    **REPAIR_PC, "campaign_projection_start": 0x6A04,
    "campaign_projection_end": 0x6AB4, "raw_crc_start": 0x6A6E,
    "raw_crc_end": 0x6A72, "renderer_start": 0x8C26,
    "renderer_end": 0x8E04,
}
SPRITE_ROM_SHA256 = "09be6696b05e2efc766776d54cb04f6353fb4d3e904e5d3c76db1c2b9ceb7162"
SPRITE_SYMBOL_SHA256 = "828e9ff50549362d455112f41236e264beafb4c1e75ad3f9f8335fba878f22b8"
SPRITE_REPAIR_PC = {**LOOKUP_REPAIR_PC, "renderer_start": 0x8C1E, "renderer_end": 0x8DFC}
PROFILE_ROM_PINS = {"baseline": PINNED_ROM_SHA256, "lookup": LOOKUP_ROM_SHA256, "sprite": SPRITE_ROM_SHA256}
PROFILE_SYMBOL_PINS = {"baseline": PINNED_SYMBOL_SHA256, "lookup": LOOKUP_SYMBOL_SHA256, "sprite": SPRITE_SYMBOL_SHA256}
PROFILE_REPAIR_PC = {"baseline": REPAIR_PC, "lookup": LOOKUP_REPAIR_PC, "sprite": SPRITE_REPAIR_PC}
INDEXED_ROM_SHA256 = "c051995c95da38c56d098acf0aa27182d2a51fc6390241382efaf0b9259bcfde"
INDEXED_SYMBOL_SHA256 = SPRITE_SYMBOL_SHA256
PROFILE_ROM_PINS["indexed"] = INDEXED_ROM_SHA256
PROFILE_SYMBOL_PINS["indexed"] = INDEXED_SYMBOL_SHA256
PROFILE_REPAIR_PC["indexed"] = dict(SPRITE_REPAIR_PC)
POOL_ROM_SHA256 = "25a74bd2d9314f2f1a2e5c9c49279449e1d1f78c8bcd7189600073b7a0ae6152"
POOL_SYMBOL_SHA256 = "ea8d8637ad5beb337a22266041dd5d05506e2f67b4471c617c1449a0a7827728"
PROFILE_ROM_PINS["pool"] = POOL_ROM_SHA256
PROFILE_SYMBOL_PINS["pool"] = POOL_SYMBOL_SHA256
PROFILE_REPAIR_PC["pool"] = {
    **SPRITE_REPAIR_PC, "campaign_projection_start": 0x6A1C,
    "campaign_projection_end": 0x6ACC, "tail_start": 0x5636,
    "tail_end": 0x564A, "raw_crc_start": 0x6A86,
    "raw_crc_end": 0x6A8A, "renderer_start": 0x8C36,
    "renderer_end": 0x8E14,
}
HIGH_WATER_ROM_SHA256 = "96dec5774bcda0d5d0fc1029a051122a8cc9e5f9b2bf62fcba95b87ede4dd11f"
HIGH_WATER_SYMBOL_SHA256 = "6da707bacf8436911b5583f954258c4f9aa54c7a222ba8bf51fe46dc0e795adb"
PROFILE_ROM_PINS["highwater"] = HIGH_WATER_ROM_SHA256
PROFILE_SYMBOL_PINS["highwater"] = HIGH_WATER_SYMBOL_SHA256
PROFILE_REPAIR_PC["highwater"] = {
    **PROFILE_REPAIR_PC["pool"], "campaign_projection_start": 0x6AFA,
    "campaign_projection_end": 0x6BAA,
    "engine_projection_start": 0x2162, "engine_projection_end": 0x237A,
    "tail_start": 0x56D2, "tail_end": 0x56E6,
    "raw_crc_start": 0x6B64, "raw_crc_end": 0x6B68,
    "renderer_start": 0x8D14, "renderer_end": 0x8EF2,
}
ALLOWED_COMMANDS = frozenset(
    {"breakpoint", "commands", "printf", "if", "else", "continue", "end"}
)
MARKER_FIELDS = {
    "MS": ("cycle", "frame", "tick", "input", "d0", "sr"),
    "ME": ("cycle", "frame", "tick", "result", "sr"),
    "PS": ("cycle", "frame", "tick", "checkpoint_count", "sr"),
    "PE": ("cycle", "frame", "tick", "length", "crc", "checkpoint_count"),
    "ES": ("cycle", "frame", "tick", "checkpoint_count"),
    "EE": ("cycle", "frame", "tick", "length", "crc", "checkpoint_count"),
    "TS": ("cycle", "frame", "tick", "destination"),
    "TE": ("cycle", "frame", "tick", "destination", "length"),
    "CS": ("cycle", "frame", "tick", "source", "length"),
    "CE": ("cycle", "frame", "tick", "crc"),
    "RS": ("cycle", "frame", "tick", "sr", "pending"),
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
    "PV": (
        "cycle",
        "frame",
        "tick",
        "return_length",
        "return_crc",
        "state_crc",
        "published_length",
        "header_version",
        "header_length",
        "checkpoint_count",
    ),
    "RR": (
        "cycle",
        "frame",
        "tick",
        "sr",
        "pending",
        "result",
        "checkpoint_count",
        "renderer_checkpoint_count",
    ),
    "RG": (
        "cycle",
        "frame",
        "tick",
        "sr",
        "pending",
        "published_depth",
        "build_depth",
        "campaign_flags",
        "checkpoint_count",
        "renderer_checkpoint_count",
    ),
    "DP": (
        "cycle",
        "frame",
        "tick",
        "state_crc",
        "debug_crc",
        "published_length",
        "debug_length",
        "checkpoint_count",
        "debug_checkpoint_count",
        "renderer_checkpoint_count",
        "renderer_result",
    ),
}
MARKER_RE = re.compile(
    r"^R94P (?P<phase>[A-Z]{1,2})(?P<values>(?: [0-9A-Fa-f]+)+)$"
)


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


def pc_contract(rom: bytes, symbols: Mapping[str, int]) -> dict[str, Any]:
    errors: list[str] = []
    for name, symbol_name in PC_SYMBOL_BINDINGS.items():
        if symbols.get(symbol_name) != PC[name]:
            errors.append(
                f"{name} ${PC[name]:X} != {symbol_name} "
                f"${symbols.get(symbol_name, -1):X}"
            )
    if symbols.get("GameplayRendererBuildAndQueue") != PC["renderer_start"] - 10:
        errors.append("renderer start derivation drift")
    for name in (
        "crc_end",
        "engine_projection_end",
        "tail_end",
        "campaign_projection_end",
    ):
        if rom[PC[name] : PC[name] + 2] != bytes.fromhex("4E75"):
            errors.append(f"{name} is not the exact rts boundary")
    if rom[PC["renderer_start"] - 4 : PC["renderer_start"]] != bytes.fromhex(
        "46FC2700"
    ):
        errors.append("renderer mask-start opcode drift")
    if rom[PC["renderer_end"] : PC["renderer_end"] + 2] != bytes.fromhex("46C7"):
        errors.append("renderer restore opcode drift")
    if rom[PC["vblank_origin"] : PC["vblank_origin"] + 10] != bytes.fromhex(
        "0C79FFFF00FFF0326706"
    ):
        errors.append("VBlank origin bytes drift")
    if rom[PC["vblank_rte"] : PC["vblank_rte"] + 2] != bytes.fromhex("4E73"):
        errors.append("VBlank rte bytes drift")
    if errors:
        raise EvidenceFailure("; ".join(errors))
    return {
        "addresses": dict(PC),
        "symbol_bindings": dict(PC_SYMBOL_BINDINGS),
        "rom_windows": {
            name: {
                "address": address,
                "hex": rom[address : address + 16].hex().upper(),
            }
            for name, address in PC.items()
        },
    }


def repair_pc_contract(
    rom: bytes, symbols: Mapping[str, int], *, profile: str = "baseline"
) -> dict[str, Any]:
    """Bind every repaired phase boundary to symbols and exact ROM bytes."""
    errors: list[str] = []
    if profile not in PROFILE_REPAIR_PC:
        raise EvidenceFailure("unknown regular phase profile")
    addresses = PROFILE_REPAIR_PC[profile]
    for name, symbol_name in REPAIR_PC_SYMBOL_BINDINGS.items():
        if symbols.get(symbol_name) != addresses[name]:
            errors.append(
                f"{name} ${addresses[name]:X} != {symbol_name} "
                f"${symbols.get(symbol_name, -1):X}"
            )
    if symbols.get("Crc32UpdateRaw") != 0x00001BF2:
        errors.append("Crc32UpdateRaw symbol drift")
    if symbols.get("GameplayRendererBuildAndQueue") != (
        addresses["renderer_start"] - 10
    ):
        errors.append("repaired renderer start derivation drift")
    exact_windows = {
        "raw_crc_call": (
            addresses["raw_crc_start"],
            "6100B08C46812801" if profile == "highwater" else "6100B16A46812801" if profile == "pool" else "6100B18246812801" if profile != "baseline" else "6100B18E46812801",
        ),
        "raw_crc_return": (
            addresses["raw_crc_end"],
            "4681280123C400FF",
        ),
        "campaign_projection_return": (
            addresses["campaign_projection_end"],
            "4E75",
        ),
        "renderer_mask_start": (
            addresses["renderer_start"] - 4,
            "46FC2700",
        ),
        "renderer_checkpoint_gate": (
            0x00008C7C + addresses["renderer_start"] - REPAIR_PC["renderer_start"],
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
            REPAIR_PC["vblank_origin"],
            "0C79FFFF00FFF0326706527900FFF032",
        ),
        "vblank_rte": (REPAIR_PC["vblank_rte"], "4E73"),
    }
    if profile == "indexed":
        exact_windows["crc_indexed_base_loop"] = (0x1C2A, "43F90001966074003418E05AB342E58A2431280042414841B58151C8FFEA")
    if profile == "pool":
        exact_windows["crc_indexed_base_loop"] = (0x1C2A, "43F90001967874003418E05AB342E58A2431280042414841B58151C8FFEA")
        exact_windows["pool_prior_current_count"] = (0x24D2, "48E71E30244836002C021A29000212C112C03401534241F900FF1440D0C218108A0412C4")
        exact_windows["pool_empty_skip"] = (0x2512, "224B4A05660E3A033003C0FC0018D2C06000008C")
    if profile == "highwater":
        exact_windows["crc_indexed_base_loop"] = (0x1C2A, "43F90001975C74003418E05AB342E58A2431280042414841B58151C8FFEA")
        exact_windows["pool_prior_current_count"] = (0x24EA, "48E71E30244836002C021A29000212C112C03401534241F900FF1440D0C218108A0412C4")
        exact_windows["pool_empty_skip"] = (0x252A, "224B4A05660E3A033003C0FC0018D2C06000008C")
    for name, (address, expected_hex) in exact_windows.items():
        expected = bytes.fromhex(expected_hex)
        if rom[address : address + len(expected)] != expected:
            errors.append(f"{name} exact ROM bytes drift")
    for name in (
        "engine_projection_end",
        "tail_end",
        "campaign_projection_end",
    ):
        if rom[addresses[name] : addresses[name] + 2] != bytes.fromhex(
            "4E75"
        ):
            errors.append(f"{name} is not the exact rts boundary")
    if errors:
        raise EvidenceFailure("; ".join(errors))
    return {
        "addresses": dict(addresses),
        "symbol_bindings": dict(REPAIR_PC_SYMBOL_BINDINGS),
        "raw_crc_target": {
            "symbol": "Crc32UpdateRaw",
            "address": symbols["Crc32UpdateRaw"],
        },
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
    printf_line: str,
) -> tuple[str, ...]:
    return (
        f"if {value} < ${minimum:X}",
        "continue",
        "else",
        f"if {value} < ${maximum + 1:X}",
        printf_line,
        "continue",
        "else",
        "continue",
        "end",
        "end",
    )


def build_command_script(symbols: Mapping[str, int]) -> str:
    frame = _memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    tick = _memory(symbols["ENGINE_TICK"], "w")
    boot = _memory(symbols["DEBUG_BOOT_STAGE"], "w")
    token = _memory(symbols["DEBUG_FRAME_TOKEN"], "w")
    sequence = _memory(symbols["DEBUG_SEQUENCE"], "l")
    input_held = _memory(symbols["INPUT_HELD"], "b")
    entry_status = _memory(symbols["DEBUG_LAST_VDP_STATUS"], "w")
    counter = _memory(symbols["DEBUG_VBLANK_OVERRUN_COUNT"], "w")
    checkpoint_count = _memory(symbols["ENGINE_CHECKPOINT_COUNT"], "w")
    debug_words = _memory(symbols["DEBUG_DMA_WORDS_FRAME"], "w")
    debug_depth = _memory(symbols["DEBUG_DMA_QUEUE_DEPTH"], "w")
    pending = _memory(symbols["DMA_QUEUE_FRAME_PENDING"], "w")
    published = _memory(symbols["DMA_QUEUE_PUBLISHED_DEPTH"], "w")
    build_depth = _memory(symbols["DMA_QUEUE_BUILD_DEPTH"], "w")
    build_words = _memory(symbols["DMA_QUEUE_BUILD_WORDS"], "l")
    commit_words = _memory(symbols["DMA_QUEUE_COMMIT_WORDS"], "w")
    entries = symbols["DMA_QUEUE_ENTRIES"]

    ordered = tuple(PC)
    lines = [f"breakpoint ${PC[name]:X}" for name in ordered]
    indices = {name: index for index, name in enumerate(ordered)}

    def commands(name: str, body: Sequence[str]) -> None:
        lines.append(f"commands {indices[name]}")
        lines.extend(body)
        lines.append("end")

    commands(
        "gameplay_call",
        _range_body(
            tick,
            TARGET_PRE_TICKS[0],
            TARGET_PRE_TICKS[-1],
            (
                'printf "R94P MS %x %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} {input_held} d0 sr"
            ),
        ),
    )
    commands(
        "gameplay_return",
        _range_body(
            tick,
            TARGET_POST_TICKS[0],
            TARGET_POST_TICKS[-1],
            (
                'printf "R94P ME %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} d0 sr"
            ),
        ),
    )
    exact_tick_commands = {
        "campaign_projection_start": (
            "PS",
            f"cycle {frame} {tick} {checkpoint_count} sr",
        ),
        "campaign_projection_end": (
            "PE",
            f"cycle {frame} {tick} d0 d1 {checkpoint_count}",
        ),
        "engine_projection_start": (
            "ES",
            f"cycle {frame} {tick} {checkpoint_count}",
        ),
        "engine_projection_end": (
            "EE",
            f"cycle {frame} {tick} d0 d1 {checkpoint_count}",
        ),
        "tail_start": ("TS", f"cycle {frame} {tick} a0"),
        "tail_end": ("TE", f"cycle {frame} {tick} a0 d0"),
        "crc_start": ("CS", f"cycle {frame} {tick} a0 d0"),
        "crc_end": ("CE", f"cycle {frame} {tick} d0"),
    }
    for name, (phase, values) in exact_tick_commands.items():
        commands(
            name,
            _range_body(
                tick,
                60,
                60,
                f'printf "R94P {phase} ' + "%x " * (len(MARKER_FIELDS[phase]) - 1) + '%x\\n" ' + values,
            ),
        )
    for name, phase in (("renderer_start", "RS"), ("renderer_end", "RE")):
        commands(
            name,
            _range_body(
                tick,
                59,
                60,
                (
                    f'printf "R94P {phase} %x %x %x %x %x\\n" '
                    f"cycle {frame} {tick} sr {pending}"
                ),
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
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0] - 1:X}",
            "continue",
            "else",
            (
                'printf "R94P VE %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {token}"
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
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0] - 1:X}",
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
        f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
        "continue",
        "else",
        (
            'printf "R94P VC %x %x %x %x %x %x %x\\n" '
            f"cycle {frame} {tick} {pending} {published} {build_depth} {build_words}"
        ),
    ]
    for index in range(EXPECTED_DMA_CALLS_WHEN_PENDING):
        address = entries + index * 12
        queue_lines.append(
            'printf "R94P Q %x %x %x %x %x %x %x\\n" '
            f"{frame} ${index:X} {_memory(address, 'l')} "
            f"{_memory(address + 4, 'w')} {_memory(address + 6, 'w')} "
            f"{_memory(address + 8, 'w')} {_memory(address + 10, 'w')}"
        )
    queue_lines.extend(("continue", "end"))
    commands("dma_commit", tuple(queue_lines))
    commands(
        "dma_issue_start",
        (
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
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
        "dma_issue_end",
        (
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
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
        "vblank_post_dma",
        (
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
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
        "vblank_status",
        (
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
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
        "vblank_origin",
        (
            'printf "R94P VO %x %x %x %x %x %x\\n" '
            f"cycle {frame} d0 d1 {counter} {sequence}",
            "continue",
        ),
    )
    commands(
        "vblank_common",
        (
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
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
        "vblank_rte",
        (
            f"if {boot} < ${BOOT_STAGE_RUNNING:X}",
            "continue",
            "else",
            f"if {frame} < ${TARGET_VBLANK_CURRENT_FRAMES[0]:X}",
            "continue",
            "else",
            (
                'printf "R94P VX %x %x %x %x %x\\n" '
                f"cycle {frame} {sequence} {counter} {token}"
            ),
            f"if {frame} < ${EXPECTED_ORIGIN_FRAME:X}",
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


def build_repair_command_script(symbols: Mapping[str, int], *, profile: str = "baseline") -> str:
    """Generate the exact bounded tick59-through-tick61 repair probe."""
    frame = _memory(symbols["DEBUG_FRAME_COUNTER"], "l")
    tick = _memory(symbols["ENGINE_TICK"], "w")
    boot = _memory(symbols["DEBUG_BOOT_STAGE"], "w")
    token = _memory(symbols["DEBUG_FRAME_TOKEN"], "w")
    sequence = _memory(symbols["DEBUG_SEQUENCE"], "l")
    input_held = _memory(symbols["INPUT_HELD"], "b")
    entry_status = _memory(symbols["DEBUG_LAST_VDP_STATUS"], "w")
    counter = _memory(symbols["DEBUG_VBLANK_OVERRUN_COUNT"], "w")
    checkpoint_count = _memory(symbols["ENGINE_CHECKPOINT_COUNT"], "w")
    state_crc = _memory(symbols["ENGINE_STATE_CRC"], "l")
    projection_length = _memory(symbols["ENGINE_PROJECTION_LENGTH"], "w")
    projection_version = _memory(symbols["ENGINE_PROJECTION_BASE"] + 4, "w")
    projection_header_length = _memory(
        symbols["ENGINE_PROJECTION_BASE"] + 6, "w"
    )
    debug_crc = _memory(symbols["DEBUG_ENGINE_STATE_CRC"], "l")
    debug_projection_length = _memory(
        symbols["DEBUG_ENGINE_PROJECTION_LENGTH"], "w"
    )
    debug_checkpoint_count = _memory(
        symbols["DEBUG_ENGINE_CHECKPOINT_COUNT"], "w"
    )
    renderer_result = _memory(symbols["RENDERER_LAST_RESULT"], "w")
    renderer_checkpoint = _memory(
        symbols["RENDERER_LAST_CHECKPOINT_COUNT"], "w"
    )
    campaign_flags = _memory(symbols["VOCAB_CAMPAIGN_FLAGS"], "w")
    debug_words = _memory(symbols["DEBUG_DMA_WORDS_FRAME"], "w")
    debug_depth = _memory(symbols["DEBUG_DMA_QUEUE_DEPTH"], "w")
    pending = _memory(symbols["DMA_QUEUE_FRAME_PENDING"], "w")
    published = _memory(symbols["DMA_QUEUE_PUBLISHED_DEPTH"], "w")
    build_depth = _memory(symbols["DMA_QUEUE_BUILD_DEPTH"], "w")
    build_words = _memory(symbols["DMA_QUEUE_BUILD_WORDS"], "l")
    commit_words = _memory(symbols["DMA_QUEUE_COMMIT_WORDS"], "w")
    entries = symbols["DMA_QUEUE_ENTRIES"]

    if profile not in PROFILE_REPAIR_PC:
        raise EvidenceFailure("unknown regular phase profile")
    addresses = PROFILE_REPAIR_PC[profile]
    ordered = tuple(addresses)
    lines = [f"breakpoint ${addresses[name]:X}" for name in ordered]
    indices = {name: index for index, name in enumerate(ordered)}

    def commands(name: str, body: Sequence[str]) -> None:
        lines.append(f"commands {indices[name]}")
        lines.extend(body)
        lines.append("end")

    def exact_tick_body(*printf_lines: str) -> tuple[str, ...]:
        return (
            f"if {tick} < $3C",
            "continue",
            "else",
            f"if {tick} < $3D",
            *printf_lines,
            "continue",
            "else",
            "continue",
            "end",
            "end",
        )

    commands(
        "gameplay_call",
        _range_body(
            tick,
            REPAIR_TARGET_PRE_TICKS[0],
            REPAIR_TARGET_PRE_TICKS[-1],
            (
                'printf "R94P MS %x %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} {input_held} d0 sr"
            ),
        ),
    )
    commands(
        "gameplay_return",
        _range_body(
            tick,
            REPAIR_TARGET_POST_TICKS[0],
            REPAIR_TARGET_POST_TICKS[-1],
            (
                'printf "R94P ME %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} d0 sr"
            ),
        ),
    )
    commands(
        "campaign_projection_start",
        exact_tick_body(
            'printf "R94P PS %x %x %x %x %x\\n" '
            f"cycle {frame} {tick} {checkpoint_count} sr"
        ),
    )
    commands(
        "campaign_projection_end",
        exact_tick_body(
            'printf "R94P PE %x %x %x %x %x %x\\n" '
            f"cycle {frame} {tick} d0 d1 {checkpoint_count}",
            'printf "R94P PV %x %x %x %x %x %x %x %x %x %x\\n" '
            f"cycle {frame} {tick} d0 d1 {state_crc} "
            f"{projection_length} {projection_version} "
            f"{projection_header_length} {checkpoint_count}",
        ),
    )
    for name, phase, values in (
        (
            "engine_projection_start",
            "ES",
            f"cycle {frame} {tick} {checkpoint_count}",
        ),
        (
            "engine_projection_end",
            "EE",
            f"cycle {frame} {tick} d0 d1 {checkpoint_count}",
        ),
        ("tail_start", "TS", f"cycle {frame} {tick} a0"),
        ("tail_end", "TE", f"cycle {frame} {tick} a0 d0"),
        (
            "raw_crc_start",
            "CS",
            f"cycle {frame} {tick} a0 d0",
        ),
        ("raw_crc_end", "CE", f"cycle {frame} {tick} d1"),
    ):
        placeholders = "%x " * (len(MARKER_FIELDS[phase]) - 1) + "%x"
        commands(
            name,
            exact_tick_body(
                f'printf "R94P {phase} {placeholders}\\n" {values}'
            ),
        )
    commands(
        "renderer_start",
        _range_body(
            tick,
            59,
            61,
            (
                'printf "R94P RG %x %x %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending} {published} "
                f"{build_depth} {campaign_flags} {checkpoint_count} "
                f"{renderer_checkpoint}"
            ),
        ),
    )
    commands(
        "renderer_end",
        _range_body(
            tick,
            59,
            61,
            (
                'printf "R94P RR %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {pending} d0 "
                f"{checkpoint_count} {renderer_checkpoint}"
            ),
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
            f"if {frame} < $3A",
            "continue",
            "else",
            (
                'printf "R94P VE %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} sr {token}"
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
            f"if {frame} < $3A",
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
        f"if {frame} < $3B",
        "continue",
        "else",
        (
            'printf "R94P VC %x %x %x %x %x %x %x\\n" '
            f"cycle {frame} {tick} {pending} {published} "
            f"{build_depth} {build_words}"
        ),
    ]
    for index in range(EXPECTED_DMA_CALLS_WHEN_PENDING):
        address = entries + index * 12
        queue_lines.append(
            'printf "R94P Q %x %x %x %x %x %x %x\\n" '
            f"{frame} ${index:X} {_memory(address, 'l')} "
            f"{_memory(address + 4, 'w')} {_memory(address + 6, 'w')} "
            f"{_memory(address + 8, 'w')} {_memory(address + 10, 'w')}"
        )
    queue_lines.extend(("continue", "end"))
    commands("dma_commit", tuple(queue_lines))
    commands(
        "dma_issue_start",
        (
            f"if {frame} < $3B",
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
        "dma_issue_end",
        (
            f"if {frame} < $3B",
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
        "vblank_post_dma",
        (
            f"if {frame} < $3B",
            "continue",
            "else",
            (
                'printf "R94P VP %x %x %x %x %x %x\\n" '
                f"cycle {frame} {commit_words} {debug_words} "
                f"{debug_depth} {pending}"
            ),
            "continue",
            "end",
        ),
    )
    commands(
        "vblank_status",
        (
            f"if {frame} < $3B",
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
        "vblank_origin",
        (
            'printf "R94P VO %x %x %x %x %x %x\\n" '
            f"cycle {frame} d0 d1 {counter} {sequence}",
            "continue",
        ),
    )
    commands(
        "vblank_common",
        (
            f"if {frame} < $3B",
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
        "vblank_rte",
        (
            f"if {boot} < ${BOOT_STAGE_RUNNING:X}",
            "continue",
            "else",
            f"if {frame} < $3B",
            "continue",
            "else",
            (
                'printf "R94P DP %x %x %x %x %x %x %x %x %x %x %x\\n" '
                f"cycle {frame} {tick} {state_crc} {debug_crc} "
                f"{projection_length} {debug_projection_length} "
                f"{checkpoint_count} {debug_checkpoint_count} "
                f"{renderer_checkpoint} {renderer_result}"
            ),
            (
                'printf "R94P VX %x %x %x %x %x\\n" '
                f"cycle {frame} {sequence} {counter} {token}"
            ),
            f"if {frame} < ${REPAIR_COMPLETE_FRAME:X}",
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
    if continues > 96:
        errors.append(f"continue command bound exceeded: {continues}/96")
    if " set " in f" {script.lower()} ":
        errors.append("set token appears in diagnostic script")
    return errors


def repair_command_policy_errors(
    script: str, symbols: Mapping[str, int], *, profile: str = "baseline"
) -> list[str]:
    errors: list[str] = []
    if script != build_repair_command_script(symbols, profile=profile):
        errors.append("command script is not the exact generated repair script")
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
    if continues > 96:
        errors.append(f"continue command bound exceeded: {continues}/96")
    lowered = f" {script.lower()} "
    for token in (" set ", " write ", " x ", " display "):
        if token in lowered:
            errors.append(f"forbidden debugger token appears: {token.strip()}")
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


def console_contract_errors(console_text: str, script: str) -> list[str]:
    errors: list[str] = []
    stripped = [line.strip() for line in console_text.splitlines()]
    for marker in (READY_MARKER, COMPLETE_MARKER):
        if stripped.count(marker) != 1:
            errors.append(f"console marker count mismatch for {marker!r}")
    if READY_MARKER in stripped and COMPLETE_MARKER in stripped:
        if stripped.index(READY_MARKER) >= stripped.index(COMPLETE_MARKER):
            errors.append("READY/COMPLETE order mismatch")
    if stripped.count("> continue") != 2:
        errors.append("console must contain bootstrap and measured continue")
    if stripped.count("> quit") != 1:
        errors.append("console must contain one final quit")
    breakpoint_lines = [
        line for line in script.splitlines() if line.startswith("breakpoint ")
    ]
    for index, command in enumerate(breakpoint_lines):
        if stripped.count(f"> {command}") != 1:
            errors.append(f"breakpoint echo mismatch: {command}")
        address = command.split("$", 1)[1]
        if stripped.count(f"68K Breakpoint {index} set at ${address}") != 1:
            errors.append(f"breakpoint acknowledgement mismatch: {index}")
    for pattern in (
        "Failed to eval",
        "Unrecognized",
        "not a recognized command",
        "fgets failed",
    ):
        if any(pattern in line for line in stripped):
            errors.append(f"native debugger error present: {pattern}")
    r94p_lines = [line for line in stripped if line.startswith("R94P ")]
    if len(r94p_lines) != len(parse_markers(console_text)) + 2:
        errors.append("unknown or extra R94P console line")
    return errors


def _by_phase(
    records: Sequence[Mapping[str, Any]], phase: str
) -> list[Mapping[str, Any]]:
    return [record for record in records if record["phase"] == phase]


def _span(
    start: Mapping[str, Any], end: Mapping[str, Any], label: str
) -> dict[str, Any]:
    start_cycle = int(start["cycle"])
    end_cycle = int(end["cycle"])
    if not 0 <= start_cycle < end_cycle <= 0xFFFFFFFF:
        raise EvidenceFailure(f"{label} cycle delta is not positive/no-wrap")
    master = end_cycle - start_cycle
    return {
        "start_cycle_master": start_cycle,
        "end_cycle_master": end_cycle,
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
    baseline_phases = (
        "MS",
        "ME",
        "PS",
        "PE",
        "ES",
        "EE",
        "TS",
        "TE",
        "CS",
        "CE",
        "RS",
        "RE",
        "VE",
        "VA",
        "VC",
        "Q",
        "DS",
        "DE",
        "VP",
        "VS",
        "VO",
        "VK",
        "VX",
    )
    phases = {phase: _by_phase(records, phase) for phase in baseline_phases}
    expected_counts = {
        "MS": 2,
        "ME": 2,
        "PS": 1,
        "PE": 1,
        "ES": 1,
        "EE": 1,
        "TS": 1,
        "TE": 1,
        "CS": 1,
        "CE": 1,
        "RS": 2,
        "RE": 2,
        "VE": 5,
        "VA": 5,
        "VC": 5,
        "Q": 20,
        "DS": 12,
        "DE": 12,
        "VP": 5,
        "VS": 5,
        "VO": 1,
        "VK": 5,
        "VX": 5,
    }
    for phase, expected in expected_counts.items():
        if len(phases[phase]) != expected:
            errors.append(
                f"{phase} marker count {len(phases[phase])} != {expected}"
            )
    if not records:
        errors.append("no marker records")
    cycle_records = [record for record in records if "cycle" in record]
    if any(
        int(later["cycle"]) <= int(earlier["cycle"])
        for earlier, later in zip(cycle_records, cycle_records[1:])
    ):
        errors.append("raw marker cycles are not strictly monotonic")
    if errors:
        return {
            "status": "fail",
            "errors": errors,
            "marker_counts": {
                phase: len(items) for phase, items in phases.items()
            },
        }

    ms, me = phases["MS"], phases["ME"]
    if [record["tick"] for record in ms] != list(TARGET_PRE_TICKS):
        errors.append("gameplay pre ticks are not exactly 58,59")
    if [record["tick"] for record in me] != list(TARGET_POST_TICKS):
        errors.append("gameplay post ticks are not exactly 59,60")
    if [record["frame"] for record in ms] != [59, 60]:
        errors.append("gameplay starts are not debug frames 59,60")
    if [record["frame"] for record in me] != [59, 62]:
        errors.append("gameplay returns are not debug frames 59,62")
    if any(
        record["input"] != UP_INPUT_MASK or record["d0"] != UP_INPUT_MASK
        for record in ms
    ):
        errors.append("gameplay inputs are not held Up/d0=Up")
    if [record["tick"] for record in phases["RS"]] != [59, 60]:
        errors.append("renderer starts are not ticks 59,60")
    if [record["tick"] for record in phases["RE"]] != [59, 60]:
        errors.append("renderer ends are not ticks 59,60")
    if [record["frame"] for record in phases["RS"]] != [59, 62] or [
        record["frame"] for record in phases["RE"]
    ] != [59, 62]:
        errors.append("renderer frames are not exactly 59,62")
    if any(record["sr"] & 0x0700 != 0x0700 for record in phases["RS"] + phases["RE"]):
        errors.append("renderer boundaries are not interrupt mask 7")
    if any(record["pending"] != 0 for record in phases["RS"]):
        errors.append("renderer starts with a pending queue")
    if any(record["pending"] != 1 for record in phases["RE"]):
        errors.append("renderer does not publish pending at return")

    ps, pe = phases["PS"][0], phases["PE"][0]
    es, ee = phases["ES"][0], phases["EE"][0]
    ts, te = phases["TS"][0], phases["TE"][0]
    cs, ce = phases["CS"][0], phases["CE"][0]
    projection_order = [
        ms[1]["cycle"],
        ps["cycle"],
        es["cycle"],
        ee["cycle"],
        ts["cycle"],
        te["cycle"],
        cs["cycle"],
        ce["cycle"],
        pe["cycle"],
        me[1]["cycle"],
    ]
    if projection_order != sorted(projection_order) or len(set(projection_order)) != len(
        projection_order
    ):
        errors.append("tick-60 projection nesting/order mismatch")
    if any(record["tick"] != 60 for record in (ps, pe, es, ee, ts, te, cs, ce)):
        errors.append("projection markers are not all bound to tick 60")
    if es["checkpoint_count"] != 1 or ee["checkpoint_count"] != 2:
        errors.append("EngineBuildProjection checkpoint count is not 1->2")
    if ps["checkpoint_count"] != 1 or pe["checkpoint_count"] != 2:
        errors.append("campaign projection checkpoint count is not 1->2")
    if ee["length"] & 0xFFFF != 0x0E36 or pe["length"] & 0xFFFF != 0x0F36:
        errors.append("engine/campaign projection length mismatch")
    if ts["destination"] != 0x00FF6E36 or te["destination"] != 0x00FF6F36:
        errors.append("vocabulary tail destination/advance mismatch")
    if te["length"] & 0xFFFF != 0x0100:
        errors.append("vocabulary tail length is not 256")
    if cs["source"] != 0x00FF6000 or cs["length"] & 0xFFFF != 0x0F36:
        errors.append("campaign CRC source/length mismatch")
    if ce["crc"] != pe["crc"]:
        errors.append("campaign CRC result is not projection result")

    ve_frames = [record["frame"] for record in phases["VE"]]
    current_frames = [record["frame"] for record in phases["VC"]]
    if ve_frames != list(range(58, 63)):
        errors.append(f"VBlank entry prior-frame labels mismatch: {ve_frames}")
    if current_frames != list(TARGET_VBLANK_CURRENT_FRAMES):
        errors.append(f"VBlank current-frame labels mismatch: {current_frames}")
    for phase in ("VP", "VS", "VK", "VX"):
        if [record["frame"] for record in phases[phase]] != list(
            TARGET_VBLANK_CURRENT_FRAMES
        ):
            errors.append(f"{phase} frame labels mismatch")
    origin = phases["VO"][0]
    if (
        origin["frame"] != EXPECTED_ORIGIN_FRAME
        or origin["d0"] & 0xFFFF != 0
        or origin["d1"] & 0xFFFF != 8
        or origin["counter"] != 0
        or origin["sequence"] != 125
    ):
        errors.append(f"origin signature mismatch: {origin}")
    for record in phases["VK"]:
        frame = record["frame"]
        expected_counter = 1 if frame == EXPECTED_ORIGIN_FRAME else 0
        if record["counter"] != expected_counter:
            errors.append(f"frame {frame} counter mismatch")
        if frame < EXPECTED_ORIGIN_FRAME and (
            record["d0"] & 0xFFFF != 8 or record["d1"] & 0xFFFF != 8
        ):
            errors.append(f"frame {frame} clean predicate mismatch")
    if phases["VX"][-1]["counter"] != 1:
        errors.append("terminal diagnostic VBlank did not publish counter 1")
    if [record["sequence"] for record in phases["VX"]] != [
        118,
        120,
        122,
        124,
        126,
    ]:
        errors.append("VBlank exit seqlock sequence mismatch")
    if [record["token"] for record in phases["VX"]] != [1, 1, 1, 2, 3]:
        errors.append("VBlank token backlog signature mismatch")
    if any(not (record["entry_status"] & 0x0008) for record in phases["VA"]):
        errors.append("a target VBlank did not enter during VBlank")
    if any(record["exit_status"] & 0x0008 == 0 for record in phases["VS"][:-1]):
        errors.append("a pre-fault handler lost VBlank")
    if phases["VS"][-1]["exit_status"] & 0x0008:
        errors.append("fault handler unexpectedly retained VBlank")

    queue_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    dma_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    dma_end_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    expected_queue = (
        (0x00FF8000, 0xF000, 0x0140, 0x8000, 0),
        (0x00FF8600, 0xC000, 0x0028, 0x8001, 0),
        (0x00FF8650, 0xC080, 0x0028, 0x8001, 0),
        (0x00FF86A0, 0xC100, 0x0028, 0x8001, 0),
    )
    for frame in TARGET_VBLANK_CURRENT_FRAMES:
        queue = [
            record for record in phases["Q"] if record["frame"] == frame
        ]
        starts = [record for record in phases["DS"] if record["frame"] == frame]
        ends = [record for record in phases["DE"] if record["frame"] == frame]
        queue_by_frame[frame] = queue
        dma_by_frame[frame] = starts
        dma_end_by_frame[frame] = ends
        vc = next(record for record in phases["VC"] if record["frame"] == frame)
        vp = next(record for record in phases["VP"] if record["frame"] == frame)
        if vc["pending"] == 1:
            if len(starts) != 4 or len(ends) != 4:
                errors.append(f"frame {frame} pending DMA cardinality mismatch")
            if vc["published_depth"] != 4 or vc["build_depth"] != 4 or vc["build_words"] != 440:
                errors.append(f"frame {frame} pending queue control mismatch")
            if vp["commit_words"] != 440 or vp["debug_words"] != 440:
                errors.append(f"frame {frame} pending DMA words mismatch")
            for index, (record, expected) in enumerate(zip(queue, expected_queue)):
                actual = (
                    record["source"],
                    record["destination"],
                    record["words"],
                    record["flags"],
                    record["age"],
                )
                if record["index"] != index or actual != expected:
                    errors.append(f"frame {frame} queue record {index} mismatch")
            for index, (start, end, expected) in enumerate(
                zip(starts, ends, expected_queue)
            ):
                if (
                    start["source"],
                    start["destination"],
                    start["words"],
                ) != expected[:3]:
                    errors.append(
                        f"frame {frame} DMA call {index} argument mismatch"
                    )
                cumulative = sum(
                    item[2] for item in expected_queue[: index + 1]
                )
                if (
                    start["commit_words"] != cumulative
                    or end["commit_words"] != cumulative
                ):
                    errors.append(
                        f"frame {frame} DMA call {index} cumulative mismatch"
                    )
                if end["cycle"] <= start["cycle"]:
                    errors.append(
                        f"frame {frame} DMA call {index} cycle order mismatch"
                    )
        else:
            if starts or ends or vp["commit_words"] != 0 or vp["debug_words"] != 0:
                errors.append(f"frame {frame} empty queue performed DMA")
        if vp["depth"] != 0 or vp["pending"] != 0:
            errors.append(f"frame {frame} post-DMA queue not consumed")
        prior = frame - 1
        local_order: list[Mapping[str, Any]] = [
            next(record for record in phases["VE"] if record["frame"] == prior),
            next(record for record in phases["VA"] if record["frame"] == prior),
            vc,
            *queue,
        ]
        for start, end in zip(starts, ends):
            local_order.extend((start, end))
        local_order.extend(
            (
                vp,
                next(
                    record
                    for record in phases["VS"]
                    if record["frame"] == frame
                ),
            )
        )
        if frame == EXPECTED_ORIGIN_FRAME:
            local_order.append(origin)
        local_order.extend(
            (
                next(
                    record
                    for record in phases["VK"]
                    if record["frame"] == frame
                ),
                next(
                    record
                    for record in phases["VX"]
                    if record["frame"] == frame
                ),
            )
        )
        positions = [records.index(record) for record in local_order]
        if positions != sorted(positions) or len(set(positions)) != len(
            positions
        ):
            errors.append(f"frame {frame} raw VBlank marker order mismatch")
        status = next(
            record for record in phases["VS"] if record["frame"] == frame
        )
        ack = next(
            record for record in phases["VA"] if record["frame"] == prior
        )
        if status["entry_status"] != ack["entry_status"]:
            errors.append(f"frame {frame} entry-status binding mismatch")
    if [
        next(
            record["pending"]
            for record in phases["VC"]
            if record["frame"] == frame
        )
        for frame in TARGET_VBLANK_CURRENT_FRAMES
    ] != [1, 1, 0, 0, 1]:
        errors.append("checkpoint queue publication topology mismatch")

    spans: dict[str, Any] = {}
    try:
        spans = {
            "gameplay_tick59": _span(ms[0], me[0], "gameplay tick59"),
            "gameplay_tick60": _span(ms[1], me[1], "gameplay tick60"),
            "campaign_projection": _span(ps, pe, "campaign projection"),
            "engine_projection": _span(es, ee, "engine projection"),
            "vocabulary_tail_copy": _span(ts, te, "vocabulary tail"),
            "campaign_full_crc": _span(cs, ce, "campaign CRC"),
            "renderer_tick59": _span(phases["RS"][0], phases["RE"][0], "renderer tick59"),
            "renderer_tick60": _span(phases["RS"][1], phases["RE"][1], "renderer tick60"),
            "main_tick59": _span(ms[0], phases["RE"][0], "main tick59"),
            "main_tick60": _span(ms[1], phases["RE"][1], "main tick60"),
        }
        vblank_spans: dict[str, Any] = {}
        for current_frame in TARGET_VBLANK_CURRENT_FRAMES:
            entry = next(
                record
                for record in phases["VE"]
                if record["frame"] == current_frame - 1
            )
            exit_record = next(
                record
                for record in phases["VX"]
                if record["frame"] == current_frame
            )
            vblank_spans[str(current_frame)] = _span(
                entry, exit_record, f"VBlank {current_frame} entry-to-rte"
            )
        spans["vblank_entry_to_rte"] = vblank_spans
        crc_interrupt_master = sum(
            vblank_spans[str(frame)]["delta_master_clocks"]
            for frame in (61, 62)
        )
        crc_inclusive_master = spans["campaign_full_crc"][
            "delta_master_clocks"
        ]
        if not (
            cs["cycle"]
            < next(record["cycle"] for record in phases["VE"] if record["frame"] == 60)
            < next(record["cycle"] for record in phases["VX"] if record["frame"] == 61)
            < next(record["cycle"] for record in phases["VE"] if record["frame"] == 61)
            < next(record["cycle"] for record in phases["VX"] if record["frame"] == 62)
            < ce["cycle"]
        ):
            errors.append("campaign CRC interrupt nesting mismatch")
        crc_exclusive_master = crc_inclusive_master - crc_interrupt_master
        if crc_exclusive_master <= 0:
            raise EvidenceFailure("campaign CRC exclusive span is not positive")
        spans["campaign_full_crc_excluding_vblank"] = {
            "inclusive_master_clocks": crc_inclusive_master,
            "subtracted_vblank_frames": [61, 62],
            "subtracted_vblank_master_clocks": crc_interrupt_master,
            "delta_master_clocks": crc_exclusive_master,
            "delta_cpu_cycles_exact": (
                crc_exclusive_master // MASTER_CLOCKS_PER_M68K_CYCLE
                if crc_exclusive_master % MASTER_CLOCKS_PER_M68K_CYCLE == 0
                else None
            ),
            "delta_cpu_cycles_ceil": (
                crc_exclusive_master + MASTER_CLOCKS_PER_M68K_CYCLE - 1
            )
            // MASTER_CLOCKS_PER_M68K_CYCLE,
            "master_remainder_mod_7": (
                crc_exclusive_master % MASTER_CLOCKS_PER_M68K_CYCLE
            ),
        }
        gameplay60_interrupt_master = crc_interrupt_master
        gameplay60_exclusive_master = (
            spans["gameplay_tick60"]["delta_master_clocks"]
            - gameplay60_interrupt_master
        )
        spans["gameplay_tick60_excluding_vblank"] = {
            "inclusive_master_clocks": spans["gameplay_tick60"][
                "delta_master_clocks"
            ],
            "subtracted_vblank_frames": [61, 62],
            "subtracted_vblank_master_clocks": gameplay60_interrupt_master,
            "delta_master_clocks": gameplay60_exclusive_master,
            "delta_cpu_cycles_exact": (
                gameplay60_exclusive_master // MASTER_CLOCKS_PER_M68K_CYCLE
                if gameplay60_exclusive_master % MASTER_CLOCKS_PER_M68K_CYCLE
                == 0
                else None
            ),
            "delta_cpu_cycles_ceil": (
                gameplay60_exclusive_master
                + MASTER_CLOCKS_PER_M68K_CYCLE
                - 1
            )
            // MASTER_CLOCKS_PER_M68K_CYCLE,
            "master_remainder_mod_7": (
                gameplay60_exclusive_master % MASTER_CLOCKS_PER_M68K_CYCLE
            ),
        }
        vblank_entries = phases["VE"]
        entry_deltas = []
        for earlier, later in zip(vblank_entries, vblank_entries[1:]):
            delta = _span(earlier, later, "consecutive VBlank entries")
            delta["from_prior_frame"] = earlier["frame"]
            delta["to_prior_frame"] = later["frame"]
            delta["relative_to_nominal_master_clocks"] = (
                delta["delta_master_clocks"] - NTSC_FRAME_MASTER_CLOCKS
            )
            entry_deltas.append(delta)
        spans["vblank_entry_deltas"] = entry_deltas
        nominal_fault_entry = phases["VE"][-2]["cycle"] + NTSC_FRAME_MASTER_CLOCKS
        renderer60_end = phases["RE"][1]["cycle"]
        spans["fault_entry_phase"] = {
            "prior_entry_cycle_master": phases["VE"][-2]["cycle"],
            "nominal_fault_entry_cycle_master": nominal_fault_entry,
            "actual_fault_entry_cycle_master": phases["VE"][-1]["cycle"],
            "renderer60_end_cycle_master": renderer60_end,
            "renderer60_headroom_before_nominal_master_clocks": (
                nominal_fault_entry - renderer60_end
            ),
            "renderer60_contains_nominal_entry": (
                phases["RS"][1]["cycle"]
                <= nominal_fault_entry
                <= renderer60_end
            ),
            "actual_entry_lateness_master_clocks": (
                phases["VE"][-1]["cycle"] - nominal_fault_entry
            ),
        }
    except EvidenceFailure as error:
        errors.append(str(error))

    projection_cpu = spans.get("campaign_projection", {}).get(
        "delta_cpu_cycles_ceil", 0
    )
    engine_cpu = spans.get("engine_projection", {}).get(
        "delta_cpu_cycles_ceil", 0
    )
    crc_cpu = spans.get("campaign_full_crc", {}).get(
        "delta_cpu_cycles_ceil", 0
    )
    if projection_cpu <= engine_cpu or projection_cpu <= crc_cpu:
        errors.append("campaign projection does not contain measured subspans")
    dominant = max(
        (
            ("engine_projection", engine_cpu),
            ("campaign_full_crc", crc_cpu),
            (
                "renderer_tick60",
                spans.get("renderer_tick60", {}).get(
                    "delta_cpu_cycles_ceil", 0
                ),
            ),
        ),
        key=lambda item: item[1],
    )
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "marker_counts": {
            phase: len(items) for phase, items in phases.items()
        },
        "phase_spans": spans,
        "queue_records": {
            str(frame): [dict(record) for record in queue_by_frame[frame]]
            for frame in TARGET_VBLANK_CURRENT_FRAMES
        },
        "dma_calls": {
            str(frame): [dict(record) for record in dma_by_frame[frame]]
            for frame in TARGET_VBLANK_CURRENT_FRAMES
        },
        "origin_signature": dict(origin),
        "dominant_component": {
            "name": dominant[0],
            "cpu_cycles": dominant[1],
            "checkpoint_gameplay_cpu_cycles": spans.get(
                "gameplay_tick60", {}
            ).get("delta_cpu_cycles_ceil"),
            "ordinary_gameplay_cpu_cycles": spans.get(
                "gameplay_tick59", {}
            ).get("delta_cpu_cycles_ceil"),
        },
        "conclusion": (
            "tick-60 checkpoint projection and the following renderer delay "
            "the frame-63 VBlank handler into the exact exit-loss path"
            if not errors
            else "diagnostic contract failed; no timing conclusion admitted"
        ),
    }


def analyse_repair(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fail closed on the exact checkpoint-repair timing/topology claim."""
    errors: list[str] = []
    phases = {phase: _by_phase(records, phase) for phase in MARKER_FIELDS}
    expected_counts = {phase: 0 for phase in MARKER_FIELDS}
    expected_counts.update(
        {
            "MS": 3,
            "ME": 3,
            "PS": 1,
            "PE": 1,
            "PV": 1,
            "ES": 1,
            "EE": 1,
            "TS": 1,
            "TE": 1,
            "CS": 1,
            "CE": 1,
            "RG": 3,
            "RR": 3,
            "VE": 4,
            "VA": 4,
            "VC": 4,
            "Q": 16,
            "DS": 12,
            "DE": 12,
            "VP": 4,
            "VS": 4,
            "VK": 4,
            "DP": 4,
            "VX": 4,
        }
    )
    for phase, expected in expected_counts.items():
        if len(phases[phase]) != expected:
            errors.append(
                f"{phase} marker count {len(phases[phase])} != {expected}"
            )
    if not records:
        errors.append("no marker records")
    cycle_records = [record for record in records if "cycle" in record]
    for earlier, later in zip(cycle_records, cycle_records[1:]):
        earlier_cycle = int(earlier["cycle"])
        later_cycle = int(later["cycle"])
        if later_cycle < earlier_cycle:
            errors.append("raw marker cycles are not monotonic")
            break
        if later_cycle == earlier_cycle and (
            earlier["phase"], later["phase"]
        ) not in {("PE", "PV"), ("DP", "VX")}:
            errors.append(
                "equal-cycle markers are not an admitted same-stop pair"
            )
            break
    if errors:
        return {
            "status": "fail",
            "errors": errors,
            "marker_counts": {
                phase: len(items) for phase, items in phases.items()
            },
        }

    ms, me = phases["MS"], phases["ME"]
    rg, rr = phases["RG"], phases["RR"]
    if [record["tick"] for record in ms] != list(
        REPAIR_TARGET_PRE_TICKS
    ):
        errors.append("repair gameplay pre ticks are not exactly 58,59,60")
    if [record["tick"] for record in me] != list(
        REPAIR_TARGET_POST_TICKS
    ):
        errors.append("repair gameplay post ticks are not exactly 59,60,61")
    if [record["frame"] for record in ms] != [59, 60, 61]:
        errors.append("repair gameplay starts are not frames 59,60,61")
    if [record["frame"] for record in me] != [59, 60, 61]:
        errors.append("repair gameplay returns are not frames 59,60,61")
    if any(
        record["input"] != UP_INPUT_MASK or record["d0"] != UP_INPUT_MASK
        for record in ms
    ):
        errors.append("repair gameplay inputs are not held Up/d0=Up")
    if [record["tick"] for record in rg] != [59, 60, 61] or [
        record["tick"] for record in rr
    ] != [59, 60, 61]:
        errors.append("repair renderer ticks are not exactly 59,60,61")
    if [record["frame"] for record in rg] != [59, 60, 61] or [
        record["frame"] for record in rr
    ] != [59, 60, 61]:
        errors.append("repair renderer frames are not exactly 59,60,61")
    if any(record["sr"] & 0x0700 != 0x0700 for record in (*rg, *rr)):
        errors.append("repair renderer boundaries are not interrupt mask 7")
    if any(record["pending"] != 0 for record in rg):
        errors.append("repair renderer did not start with pending=0")
    if any(
        record["published_depth"] != 0 or record["build_depth"] != 0
        for record in rg
    ):
        errors.append("repair renderer did not start with empty queue depths")
    if any(record["campaign_flags"] & 1 == 0 for record in rg):
        errors.append("repair renderer gate was not active-campaign")
    if [record["checkpoint_count"] for record in rg] != [1, 2, 2]:
        errors.append("repair renderer engine checkpoint topology mismatch")
    if [record["renderer_checkpoint_count"] for record in rg] != [
        1,
        1,
        2,
    ]:
        errors.append("repair renderer seen-checkpoint topology mismatch")
    if [record["result"] & 0xFFFF for record in rr] != [0, 4, 0]:
        errors.append("repair renderer result topology is not OK/HOLD/OK")
    if [record["pending"] for record in rr] != [1, 0, 1]:
        errors.append("repair renderer publication topology mismatch")
    if [record["checkpoint_count"] for record in rr] != [1, 2, 2] or [
        record["renderer_checkpoint_count"] for record in rr
    ] != [1, 2, 2]:
        errors.append("repair renderer endpoint checkpoint topology mismatch")

    ps, pe, pv = phases["PS"][0], phases["PE"][0], phases["PV"][0]
    es, ee = phases["ES"][0], phases["EE"][0]
    ts, te = phases["TS"][0], phases["TE"][0]
    cs, ce = phases["CS"][0], phases["CE"][0]
    projection_order = [
        ms[1]["cycle"],
        ps["cycle"],
        es["cycle"],
        ee["cycle"],
        ts["cycle"],
        te["cycle"],
        cs["cycle"],
        ce["cycle"],
        pe["cycle"],
        me[1]["cycle"],
    ]
    if projection_order != sorted(projection_order) or len(
        set(projection_order)
    ) != len(projection_order):
        errors.append("repaired tick-60 projection nesting/order mismatch")
    if any(
        record["tick"] != 60
        for record in (ps, pe, pv, es, ee, ts, te, cs, ce)
    ):
        errors.append("repaired projection markers are not tick 60")
    if es["checkpoint_count"] != 1 or ee["checkpoint_count"] != 2:
        errors.append("repaired EngineBuildProjection count is not 1->2")
    if ps["checkpoint_count"] != 1 or pe["checkpoint_count"] != 2:
        errors.append("repaired campaign projection count is not 1->2")
    if ee["length"] & 0xFFFF != 0x0E36 or pe["length"] & 0xFFFF != 0x0F36:
        errors.append("repaired engine/campaign projection length mismatch")
    if ts["destination"] != 0x00FF6E36 or te["destination"] != 0x00FF6F36:
        errors.append("repaired vocabulary tail destination mismatch")
    if te["length"] & 0xFFFF != 0x0100:
        errors.append("repaired vocabulary tail length is not 256")
    if cs["source"] != 0x00FF6E36 or cs["length"] & 0xFFFF != 0x0100:
        errors.append("repaired raw CRC is not limited to the 256-byte tail")
    if pe["crc"] != ((~ce["crc"]) & 0xFFFFFFFF):
        errors.append("repaired raw CRC finalization mismatch")
    if (
        pv["return_length"] & 0xFFFF != 0x0F36
        or pv["return_crc"] != pe["crc"]
        or pv["state_crc"] != pe["crc"]
        or pv["published_length"] != 0x0F36
        or pv["header_version"] != 2
        or pv["header_length"] != 0x0F36
        or pv["checkpoint_count"] != 2
    ):
        errors.append("repaired full projection CRC/state/header parity failed")

    target_frames = list(REPAIR_TARGET_VBLANK_CURRENT_FRAMES)
    if [record["frame"] for record in phases["VE"]] != [58, 59, 60, 61]:
        errors.append("repair VBlank entry prior-frame labels mismatch")
    for phase in ("VC", "VP", "VS", "VK", "DP", "VX"):
        if [record["frame"] for record in phases[phase]] != target_frames:
            errors.append(f"repair {phase} frame labels mismatch")
    if phases["VO"]:
        errors.append("repaired interval reached the VBlank overrun origin")
    if any(record["counter"] != 0 for record in (*phases["VS"], *phases["VK"], *phases["VX"])):
        errors.append("repaired interval changed the VBlank overrun counter")
    if any(
        record["d0"] & 0xFFFF != 8 or record["d1"] & 0xFFFF != 8
        for record in phases["VK"]
    ):
        errors.append("repaired VBlank clean predicate mismatch")
    if any(not (record["entry_status"] & 0x0008) for record in phases["VA"]):
        errors.append("repaired VBlank did not enter during blank")
    if any(record["exit_status"] & 0x0008 == 0 for record in phases["VS"]):
        errors.append("repaired VBlank did not exit during blank")
    if [record["sequence"] for record in phases["VX"]] != [
        118,
        120,
        122,
        124,
    ]:
        errors.append("repair VBlank seqlock sequence mismatch")
    if [record["token"] for record in phases["VX"]] != [1, 1, 1, 1]:
        errors.append("repair frame token shows a backlog")

    dp_by_frame = {record["frame"]: record for record in phases["DP"]}
    if [dp_by_frame[frame]["tick"] for frame in target_frames] != [
        58,
        59,
        60,
        61,
    ]:
        errors.append("repair VBlank engine-tick cadence mismatch")
    for frame in (61, 62):
        dp = dp_by_frame[frame]
        if (
            dp["state_crc"] != pe["crc"]
            or dp["debug_crc"] != pe["crc"]
            or dp["published_length"] != 0x0F36
            or dp["debug_length"] != 0x0F36
            or dp["checkpoint_count"] != 2
            or dp["debug_checkpoint_count"] != 2
        ):
            errors.append(f"frame {frame} projection/debug parity mismatch")
    if (
        dp_by_frame[61]["renderer_checkpoint_count"] != 2
        or dp_by_frame[61]["renderer_result"] != 4
        or dp_by_frame[62]["renderer_checkpoint_count"] != 2
        or dp_by_frame[62]["renderer_result"] != 0
    ):
        errors.append("repair VBlank renderer hold/rebuild mirrors mismatch")

    expected_queue_pending = (
        (0x00FF8000, 0xF000, 0x0140, 0x8000, 0),
        (0x00FF8600, 0xC000, 0x0028, 0x8001, 0),
        (0x00FF8650, 0xC080, 0x0028, 0x8001, 0),
        (0x00FF86A0, 0xC100, 0x0028, 0x8001, 0),
    )
    expected_queue_consumed = tuple(
        (source, destination, words, flags | 0x4000, age)
        for source, destination, words, flags, age in expected_queue_pending
    )
    queue_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    dma_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    expected_pending = {59: 1, 60: 1, 61: 0, 62: 1}
    for frame in target_frames:
        vc = next(record for record in phases["VC"] if record["frame"] == frame)
        vp = next(record for record in phases["VP"] if record["frame"] == frame)
        queue = [record for record in phases["Q"] if record["frame"] == frame]
        starts = [record for record in phases["DS"] if record["frame"] == frame]
        ends = [record for record in phases["DE"] if record["frame"] == frame]
        queue_by_frame[frame] = queue
        dma_by_frame[frame] = starts
        if vc["pending"] != expected_pending[frame]:
            errors.append(f"frame {frame} pending topology mismatch")
        if vc["pending"]:
            if (
                vc["published_depth"] != 4
                or vc["build_depth"] != 4
                or vc["build_words"] != 440
                or len(starts) != 4
                or len(ends) != 4
                or vp["commit_words"] != 440
                or vp["debug_words"] != 440
            ):
                errors.append(f"frame {frame} 4-record/440-word DMA mismatch")
        elif starts or ends or vp["commit_words"] or vp["debug_words"]:
            errors.append(f"frame {frame} deferred queue performed DMA")
        if vp["depth"] != 0 or vp["pending"] != 0:
            errors.append(f"frame {frame} post-DMA queue not consumed")
        expected_queue = (
            expected_queue_pending if vc["pending"] else expected_queue_consumed
        )
        for index, (record, expected) in enumerate(zip(queue, expected_queue)):
            actual = (
                record["source"],
                record["destination"],
                record["words"],
                record["flags"],
                record["age"],
            )
            if record["index"] != index or actual != expected:
                errors.append(f"frame {frame} queue record {index} mismatch")
        for index, (start, end, expected) in enumerate(
            zip(starts, ends, expected_queue_pending)
        ):
            if (
                start["source"],
                start["destination"],
                start["words"],
            ) != expected[:3]:
                errors.append(f"frame {frame} DMA call {index} args mismatch")
            cumulative = sum(
                item[2] for item in expected_queue_pending[: index + 1]
            )
            if (
                start["commit_words"] != cumulative
                or end["commit_words"] != cumulative
                or end["cycle"] <= start["cycle"]
            ):
                errors.append(f"frame {frame} DMA call {index} span mismatch")
        prior = frame - 1
        local = [
            next(record for record in phases["VE"] if record["frame"] == prior),
            next(record for record in phases["VA"] if record["frame"] == prior),
            vc,
            *queue,
        ]
        for start, end in zip(starts, ends):
            local.extend((start, end))
        local.extend(
            (
                vp,
                next(record for record in phases["VS"] if record["frame"] == frame),
                next(record for record in phases["VK"] if record["frame"] == frame),
                dp_by_frame[frame],
                next(record for record in phases["VX"] if record["frame"] == frame),
            )
        )
        positions = [records.index(record) for record in local]
        if positions != sorted(positions) or len(set(positions)) != len(positions):
            errors.append(f"frame {frame} raw VBlank marker order mismatch")
        status = next(record for record in phases["VS"] if record["frame"] == frame)
        ack = next(record for record in phases["VA"] if record["frame"] == prior)
        if status["entry_status"] != ack["entry_status"]:
            errors.append(f"frame {frame} entry-status binding mismatch")

    spans: dict[str, Any] = {}
    try:
        spans = {
            "gameplay_tick59": _span(ms[0], me[0], "gameplay tick59"),
            "gameplay_tick60": _span(ms[1], me[1], "gameplay tick60"),
            "gameplay_tick61": _span(ms[2], me[2], "gameplay tick61"),
            "campaign_projection": _span(ps, pe, "campaign projection"),
            "engine_projection": _span(es, ee, "engine projection"),
            "vocabulary_tail_copy": _span(ts, te, "vocabulary tail"),
            "campaign_raw_tail_crc": _span(cs, ce, "campaign raw tail CRC"),
            "renderer_tick59": _span(rg[0], rr[0], "renderer tick59"),
            "renderer_tick60_defer": _span(rg[1], rr[1], "renderer tick60 defer"),
            "renderer_tick61": _span(rg[2], rr[2], "renderer tick61"),
            "main_tick59": _span(ms[0], rr[0], "main tick59"),
            "main_tick60": _span(ms[1], rr[1], "main tick60"),
            "main_tick61": _span(ms[2], rr[2], "main tick61"),
        }
        vblank_spans: dict[str, Any] = {}
        for frame in target_frames:
            entry = next(record for record in phases["VE"] if record["frame"] == frame - 1)
            exit_record = next(record for record in phases["VX"] if record["frame"] == frame)
            vblank_spans[str(frame)] = _span(entry, exit_record, f"VBlank {frame}")
        spans["vblank_entry_to_rte"] = vblank_spans
        entry_deltas = []
        for earlier, later in zip(phases["VE"], phases["VE"][1:]):
            delta = _span(earlier, later, "consecutive VBlank entries")
            delta["from_prior_frame"] = earlier["frame"]
            delta["to_prior_frame"] = later["frame"]
            delta["relative_to_nominal_master_clocks"] = (
                delta["delta_master_clocks"] - NTSC_FRAME_MASTER_CLOCKS
            )
            entry_deltas.append(delta)
        spans["vblank_entry_deltas"] = entry_deltas
        prior_entry = next(record for record in phases["VE"] if record["frame"] == 59)
        next_entry = next(record for record in phases["VE"] if record["frame"] == 60)
        nominal_next = prior_entry["cycle"] + NTSC_FRAME_MASTER_CLOCKS
        headroom = nominal_next - rr[1]["cycle"]
        spans["first_next_vblank_phase"] = {
            "prior_entry_cycle_master": prior_entry["cycle"],
            "nominal_next_entry_cycle_master": nominal_next,
            "actual_next_entry_cycle_master": next_entry["cycle"],
            "checkpoint_renderer_end_cycle_master": rr[1]["cycle"],
            "headroom_before_nominal_master_clocks": headroom,
            "headroom_cpu_cycles_floor": headroom // MASTER_CLOCKS_PER_M68K_CYCLE,
            "actual_entry_relative_to_nominal_master_clocks": (
                next_entry["cycle"] - nominal_next
            ),
            "checkpoint_main_completed_before_nominal_entry": headroom > 0,
        }
        if headroom <= 0:
            errors.append("repaired checkpoint main interval has no VBlank headroom")
        if spans["main_tick60"]["delta_master_clocks"] >= NTSC_FRAME_MASTER_CLOCKS:
            errors.append("repaired checkpoint main interval exceeds one NTSC frame")
    except EvidenceFailure as error:
        errors.append(str(error))

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "marker_counts": {
            phase: len(items) for phase, items in phases.items()
        },
        "phase_spans": spans,
        "projection_parity": {
            "length": pv["return_length"] & 0xFFFF,
            "crc": pv["return_crc"],
            "state_crc": pv["state_crc"],
            "header_version": pv["header_version"],
            "header_length": pv["header_length"],
            "debug_crc_frame61": dp_by_frame[61]["debug_crc"],
            "debug_crc_frame62": dp_by_frame[62]["debug_crc"],
        },
        "queue_records": {
            str(frame): [dict(record) for record in queue_by_frame[frame]]
            for frame in target_frames
        },
        "dma_calls": {
            str(frame): [dict(record) for record in dma_by_frame[frame]]
            for frame in target_frames
        },
        "origin_hits": [],
        "conclusion": (
            "the repaired tick-60 projection uses only raw tail CRC, the "
            "renderer deterministically holds one checkpoint frame, frame 61 "
            "performs no DMA, frame 62 rebuilds 4 records/440 words, and the "
            "first next VBlank retains measured headroom with no overrun"
            if not errors
            else "diagnostic contract failed; no repair conclusion admitted"
        ),
    }


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
        raise EvidenceFailure("live checkpoint phase diagnostic requires Windows")
    root = Path(args.root).resolve()
    rom_path = root / ROM_RELATIVE_PATH
    symbols_path = root / SYMBOL_RELATIVE_PATH
    emulator_dir = root / ".toolcache" / "blastem"
    emulator = emulator_dir / "blastem.exe"
    default_config = emulator_dir / "default.cfg"
    collector_path = root / COLLECTOR_RELATIVE_PATH
    console_helper_path = root / CONSOLE_HELPER_RELATIVE_PATH
    report_path = Path(args.report).resolve()
    trace_path = Path(args.trace).resolve()
    profile = getattr(args, "profile", "baseline")
    if report_path.exists() or trace_path.exists() or report_path == trace_path:
        raise EvidenceFailure("live diagnostic requires distinct new output paths")
    for path, label in (
        (rom_path, "B03 Debug ROM"),
        (symbols_path, "B03 symbols"),
        (emulator, "pinned BlastEm"),
        (default_config, "pinned config"),
        (collector_path, "checkpoint phase collector"),
        (console_helper_path, "native console helper"),
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
        "INPUT_HELD",
        "ENGINE_TICK",
        "ENGINE_CHECKPOINT_COUNT",
        "ENGINE_STATE_CRC",
        "ENGINE_PROJECTION_LENGTH",
        "ENGINE_PROJECTION_BASE",
        "DEBUG_LAST_VDP_STATUS",
        "DEBUG_VBLANK_OVERRUN_COUNT",
        "DEBUG_ENGINE_STATE_CRC",
        "DEBUG_ENGINE_PROJECTION_LENGTH",
        "DEBUG_ENGINE_CHECKPOINT_COUNT",
        "DEBUG_DMA_WORDS_FRAME",
        "DEBUG_DMA_QUEUE_DEPTH",
        "DMA_QUEUE_FRAME_PENDING",
        "DMA_QUEUE_PUBLISHED_DEPTH",
        "DMA_QUEUE_BUILD_DEPTH",
        "DMA_QUEUE_BUILD_WORDS",
        "DMA_QUEUE_COMMIT_WORDS",
        "DMA_QUEUE_ENTRIES",
        "RENDERER_LAST_RESULT",
        "RENDERER_LAST_CHECKPOINT_COUNT",
        "VOCAB_CAMPAIGN_FLAGS",
        "Crc32UpdateRaw",
        "GameplayRendererBuildAndQueue",
        *REPAIR_PC_SYMBOL_BINDINGS.values(),
    }
    missing = sorted(required_symbols - symbols.keys())
    if missing:
        raise EvidenceFailure("symbol map missing: " + ", ".join(missing))
    bound_pc = repair_pc_contract(rom_path.read_bytes(), symbols, profile=profile)
    script = build_repair_command_script(symbols, profile=profile)
    policy_errors = repair_command_policy_errors(script, symbols, profile=profile)
    if policy_errors:
        raise EvidenceFailure("; ".join(policy_errors))

    process: subprocess.Popen[Any] | None = None
    helper: subprocess.Popen[str] | None = None
    helper_result: dict[str, Any] | None = None
    runtime_copy: dict[str, Any] = {}
    controller: dict[str, Any] = {}
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="recursion94-m5-checkpoint-phase-") as text:
        temporary = Path(text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_dir, temporary
        )
        controller = write_standard_pad_profile(default_config, temporary)
        controller["session_isolation"] = apply_blastem_session_isolation(
            Path(controller["path"]), temporary / "session-data"
        )
        command_file = temporary / "checkpoint-phase-commands.txt"
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
        process = subprocess.Popen(
            launch, cwd=isolated_runtime, creationflags=creationflags
        )
        try:
            helper_command = [
                sys.executable,
                str(console_helper_path),
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
            from tools.m5_budget_checkpoint_reset_phase_diagnostic import preserve_native_failure

            def preserve_failure(payload: Mapping[str, Any]) -> None:
                preserve_native_failure(
                    report_path, trace_path, script=script,
                    helper_payload=payload,
                    metadata={"rom_profile": profile, "timeout_seconds": args.timeout_seconds, "rom": _file_record(rom_path, root), "symbols": _file_record(symbols_path, root), "collector": _file_record(collector_path, root), "console_helper": _file_record(console_helper_path, root), "pc_contract": bound_pc},
                )

            try:
                stdout, stderr = helper.communicate(timeout=args.timeout_seconds + 10)
            except subprocess.TimeoutExpired:
                preserve_failure({"error": "parent helper communication timeout", "console_text": "", "full_console_available": False})
                raise EvidenceFailure("parent helper timeout; console unavailable, failure metadata preserved")
            if helper.returncode != 0:
                try:
                    failed_payload = json.loads(stdout)
                except json.JSONDecodeError:
                    failed_payload = {"error": stderr.strip(), "console_text": "", "full_console_available": False}
                preserve_failure(failed_payload)
                raise EvidenceFailure(
                    f"console helper failed ({helper.returncode}); raw failure preserved: {failed_payload.get('error', stderr.strip())}"
                )
            helper_result = json.loads(stdout)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                preserve_failure({**helper_result, "error": "owned emulator exit timeout", "full_console_available": True})
                raise EvidenceFailure("owned emulator exit timeout; completed console preserved as failure")
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
    analysis = analyse_repair(records)
    trace = _trace_text(script, console_text)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(trace, encoding="utf-8", newline="\n")
    report = {
        "schema": REPORT_SCHEMA,
        "evidence_type": EVIDENCE_TYPE,
        "status": (
            "diagnostic_pass"
            if analysis["status"] == "pass" and not console_errors
            else "diagnostic_fail"
        ),
        "canonical": False,
        "diagnostic_only": True,
        "rom_profile": profile,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "bounds": {
            "pre_ticks": list(REPAIR_TARGET_PRE_TICKS),
            "post_ticks": list(REPAIR_TARGET_POST_TICKS),
            "vblank_frames": list(REPAIR_TARGET_VBLANK_CURRENT_FRAMES),
            "maximum_vblanks": REPAIR_MAX_VBLANKS,
            "maximum_dma_calls": REPAIR_MAX_DMA_CALLS,
            "input": "held Up; noncanonical after the canonical prefix",
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
        },
        "pc_contract": bound_pc,
        "host_input": {
            "window": helper_result["input_window"],
            "key": {"name": "Up", **native.UP_KEY},
            "input_injected_at_marker": READY_MARKER,
        },
        "command_script": script,
        "command_script_sha256": _sha256_bytes(script.encode("utf-8")),
        "console_sha256": _sha256_bytes(console_text.encode("utf-8")),
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
            "native_console_helper": {
                **_file_record(console_helper_path, root),
                "entrypoint": "--console-helper",
                "staged_ready_marker": READY_MARKER,
            },
            "trace": _file_record(trace_path, root),
            "isolated_runtime": runtime_copy,
            "isolated_config": controller,
        },
        "limitations": [
            "Noncanonical diagnostic only; it cannot satisfy the two-session budget gate.",
            "Held Up diverges from the canonical B03 route after its canonical prefix.",
            "Pinned BlastEm cycles are emulator evidence, not physical hardware timing.",
            "Debugger stops do not advance the emulated master-cycle counter.",
            "Projection CRC parity binds runtime return/state/header/debug mirrors; it does not dump the 3,894-byte projection during the measured interval.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if report["status"] != "diagnostic_pass":
        raise EvidenceFailure("checkpoint phase contract failed; artifacts preserved")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parent.parent)
    )
    parser.add_argument("--report", default=DEFAULT_REPORT_RELATIVE_PATH)
    parser.add_argument("--trace", default=DEFAULT_TRACE_RELATIVE_PATH)
    parser.add_argument("--profile", choices=("baseline", "lookup", "sprite", "indexed", "pool", "highwater"), default="baseline")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = collect(args)
    except Exception as error:
        print(f"M5 B03 checkpoint phase diagnostic: fail ({error})")
        return 1
    print(
        "M5 B03 checkpoint phase diagnostic: "
        f"{report['status']} ({args.report})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
