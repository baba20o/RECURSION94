#!/usr/bin/env python3
"""Collect objective, hash-bound native B03 visual evidence.

Each required image is produced by a fresh, owned native-Windows BlastEm
process.  The canonical B03 controller schedule is imported from
``m5_b03_runtime_evidence`` and delivered only through the isolated standard
three-button pad's window.  GDB is restricted to software-breakpoint control,
bounded memory reads, register reads, and continue packets.

The native ``ui.screenshot`` edge is queued after the target gameplay state
has been rendered into the bounded DMA queue.  The first symbol-derived
post-DMA stop proves that exact state was published.  At the next ODD
presentation, the collector either consumes the one-shot screenshot while
stopped at the DMA entry or, when that empty entry wins the breakpoint race,
executes only the proven no-op commit and consumes the same request at the
guarded successor POST.  The report therefore binds every PNG to the first
published state and records the exact UI-service race order.

This collector deliberately performs no subjective visual scoring.  It emits
decoded pixel facts, state manifests, hashes, and capture provenance for a
later independent critic.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    GdbRemote,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    parse_vasm_symbols,
    require_gdb_port_available,
    require_stop,
    sha256_file,
    verify_no_user_blastem_override,
    write_json,
    write_standard_pad_profile,
)
from tools.m3_runtime_evidence import (  # noqa: E402
    _read_region,
    debugger_write_packets,
    verify_controller_bindings,
)
from tools import m4_visual_evidence as m4_visual  # noqa: E402
from tools import m5_b03_runtime_evidence as b03  # noqa: E402


REPORT_SCHEMA = "r94-m5-b03-visual-evidence-v1"
EVIDENCE_TYPE = "blastem_native_ui_screenshot_m5_b03_state_set"
ACCEPTANCE_STATUS = "evidence_ready"

PINNED_B03_ROM_SHA256 = (
    "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
)
PINNED_B03_SYMBOL_SHA256 = (
    "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
)
PINNED_RENDERER_SOURCE_SHA256 = {
    "src/render/gameplay_renderer_hud.asm": (
        "17cdb737c5b31385eec5e399654e2b8c65423b6f31e8404ba82ae5cb433b8a84"
    ),
    "src/render/gameplay_renderer_sprites.asm": (
        "26a82e412fbee4ddc91d0b013f7c595c2354e85f3260e0fc09c486fe204f96b9"
    ),
}

RENDERER_MAGIC = 0x52393456
RENDERER_VERSION = 1
RENDERER_DECLARED_BYTES = 0x0500
TARGET_RENDER_COMMIT_WORDS = 440
RENDERER_FLAG_INITIALIZED = 0x0001
RENDERER_FLAG_QUEUE_BACKLOG = 0x0004
RENDERER_FLAG_SPRITE_DROPPED = 0x0008
RENDERER_FLAG_DMA_REJECTED = 0x0010
RENDERER_FLAG_INVALID_STATE = 0x0080
RENDERER_FLAG_BEAM_TRUNCATED = 0x0200
RENDERER_FAULT_FLAGS = (
    RENDERER_FLAG_QUEUE_BACKLOG
    | RENDERER_FLAG_SPRITE_DROPPED
    | RENDERER_FLAG_DMA_REJECTED
    | RENDERER_FLAG_INVALID_STATE
    | RENDERER_FLAG_BEAM_TRUNCATED
)
RENDERER_SPRITE_LIMIT = 64
RENDERER_SCANLINE_SPRITE_LIMIT = 20
RENDERER_SCANLINE_PIXEL_LIMIT = 320
SAT_SHADOW_BASE = 0x00FF8000
SAT_SHADOW_BYTES = 0x0280
SAT_ENTRY_BYTES = 8
SAT_CAPACITY = SAT_SHADOW_BYTES // SAT_ENTRY_BYTES
RENDERER_HUD_BUFFER_OFFSET = 0x0300
RENDERER_HUD_ROW_WORDS = 40
RENDERER_HUD_ROW_BYTES = RENDERER_HUD_ROW_WORDS * 2
RENDERER_TILE_MASK = 0x07FF
RENDERER_TILE_CURRENT_0 = 65
RENDERER_TILE_CURRENT_1 = 74
RENDERER_TILE_TERMINAL = 57
RENDERER_TILE_TERMINAL_STABLE = 61
RENDERER_CURRENT_BADGE_TILE = 13
RENDERER_DASH_BADGE_TILE = 14
RENDERER_SPRITE_COORD_BIAS = 128
RENDERER_LAST_RESULT_OFFSET = 0x22
RENDERER_LAST_CHECKPOINT_COUNT_OFFSET = 0x24
RENDERER_RESULT_CHECKPOINT_HELD = 4
TERMINAL_PREVIOUS_TICK = 291
TERMINAL_PREVIOUS_CHECKPOINT_COUNT = 5
TERMINAL_CHECKPOINT_COUNT = 6
DEBUG_FRAME_COUNTER = 0x00FFF010
DMA_QUEUE_CONTROL_BASE = 0x00FF9000
DMA_QUEUE_CONTROL_BYTES = 0x10
DMA_QUEUE_FRAME_PENDING_OFFSET = 0x0A
DMA_QUEUE_CAPACITY = 32
MAX_IDLE_DMA_BOUNDARIES = 1

SCREENSHOT_KEY = ("F1", 0x70, 0x3B, False)
SCREENSHOT_CONFIG_KEY = "f1"
SOURCE_BINDING_PATHS = tuple(
    dict.fromkeys(
        (
            *b03.SOURCE_BINDING_PATHS,
            "src/hardware/dma.asm",
            "src/hardware/interrupts.asm",
            "src/hardware/memory_map.asm",
            "src/render/gameplay_renderer.asm",
            "src/render/gameplay_renderer_assets.asm",
            "src/render/gameplay_renderer_core.asm",
            "src/render/gameplay_renderer_hud.asm",
            "src/render/gameplay_renderer_layout.asm",
            "src/render/gameplay_renderer_sprites.asm",
            "tools/m4_visual_evidence.py",
            "tools/m2_evidence.py",
            "tools/m3_runtime_evidence.py",
            "tools/m5_b03_runtime_evidence.py",
            "tools/m5_visual_evidence.py",
        )
    )
)
REQUIRED_SYMBOLS = tuple(
    dict.fromkeys(
        (
            *b03.REQUIRED_SYMBOLS,
            "DmaCommitPreparedQueue",
            "GameplayRendererBuildAndQueue",
        )
    )
)
AUTHORITATIVE_RAW_KEYS = (
    "_control_bytes",
    "_gameplay_bytes",
    "_vocabulary_bytes",
    "_descriptor_bytes",
    "_rectangle_bytes",
    "_device_bytes",
    "_projectile_bytes",
    "_hazard_bytes",
    "_actor_bytes",
    "_replay_bytes",
    "_projection_bytes",
)
RAW_REGION_NAMES = {
    "_control_bytes": "engine_control",
    "_gameplay_bytes": "gameplay_control",
    "_vocabulary_bytes": "vocabulary_state",
    "_descriptor_bytes": "encounter_descriptor",
    "_rectangle_bytes": "canonical_rectangles",
    "_device_bytes": "device_pool",
    "_projectile_bytes": "projectile_pool",
    "_hazard_bytes": "hazard_pool",
    "_actor_bytes": "actor_pool",
    "_input_bytes": "input_state",
    "_replay_bytes": "replay_timeline",
    "_projection_bytes": "engine_projection",
    "_sat_bytes": "sat_shadow",
    "_renderer_bytes": "renderer_state",
}
STATE_READ_REGION_SPECS = (
    (
        "engine_control",
        b03.ENGINE_CONTROL_BASE,
        b03.ENGINE_CONTROL_USED_BYTES,
    ),
    (
        "gameplay_control",
        b03.GAMEPLAY_CONTROL_BASE,
        b03.GAMEPLAY_CONTROL_USED_BYTES,
    ),
    ("vocabulary_state", b03.VOCAB_STATE_BASE, b03.VOCAB_STATE_BYTES),
    (
        "encounter_descriptor",
        b03.ENGINE_STAGING_DESCRIPTOR,
        b03.ENCOUNTER_DESCRIPTOR_BYTES,
    ),
    (
        "canonical_rectangles",
        b03.CANONICAL_RECTS,
        len(b03.B03_RECTS) * b03.STATIC_RECT_BYTES,
    ),
    (
        "device_pool",
        b03.DEVICE_POOL,
        b03.DEVICE_CAPACITY * b03.OBJECT_BYTES,
    ),
    (
        "projectile_pool",
        b03.PROJECTILE_POOL,
        b03.PROJECTILE_CAPACITY * b03.OBJECT_BYTES,
    ),
    (
        "hazard_pool",
        b03.HAZARD_POOL,
        b03.HAZARD_CAPACITY * b03.OBJECT_BYTES,
    ),
    (
        "actor_pool",
        b03.ENGINE_ACTOR_BASE,
        b03.ACTOR_CAPACITY * b03.ACTOR_BYTES,
    ),
    ("input_state", b03.INPUT_STATE_BASE, b03.INPUT_STATE_BYTES),
    ("replay_timeline", b03.REPLAY_BANK_BASE, b03.REPLAY_TIMELINE_BYTES),
    (
        "engine_projection",
        b03.ENGINE_PROJECTION_BASE,
        b03.CAMPAIGN_PROJECTION_BYTES,
    ),
    ("sat_shadow", SAT_SHADOW_BASE, SAT_SHADOW_BYTES),
    (
        "renderer_state",
        m4_visual.RENDERER_STATE_BASE,
        RENDERER_DECLARED_BYTES,
    ),
)
BOUNDARY_READ_REGION_SPECS = (
    ("debug_frame_counter", DEBUG_FRAME_COUNTER, 4),
    ("dma_queue_control", DMA_QUEUE_CONTROL_BASE, DMA_QUEUE_CONTROL_BYTES),
)
AUTHORITATIVE_REGION_NAMES = tuple(
    RAW_REGION_NAMES[key] for key in AUTHORITATIVE_RAW_KEYS
)


@dataclass(frozen=True)
class CaptureSpec:
    name: str
    attempt: int
    tick: int
    output: str
    objective: str


CAPTURE_PLAN = (
    CaptureSpec(
        "two_echo_armed",
        2,
        67,
        "artifacts/screenshots/m5-b03-two-echo-armed.png",
        "two replay echoes, both switches, armed node, closed door, hazard",
    ),
    CaptureSpec(
        "node_disabled_door_open",
        2,
        196,
        "artifacts/screenshots/m5-b03-node-disabled-door-open.png",
        "echo-owned final node hit and dependency-opened door",
    ),
    CaptureSpec(
        "dash_route_open",
        2,
        201,
        "artifacts/screenshots/m5-b03-dash-route-open.png",
        "current actor east of the open door on the authored dash route",
    ),
    CaptureSpec(
        "terminal_hold_19",
        2,
        291,
        "artifacts/screenshots/m5-b03-terminal-hold-19.png",
        "nineteenth uninterrupted current-only terminal hold step",
    ),
    CaptureSpec(
        "terminal_complete_20",
        2,
        292,
        "artifacts/screenshots/m5-b03-terminal-complete-20.png",
        "twentieth hold step and PROGRAM_STABLE completion",
    ),
)

EXPECTED_STATUS_PRESENTATION = {
    "two_echo_armed": {
        "text": "SWITCH ACTIVE  ROUTE UPDATE PENDING",
        "palette": 0,
        "role": "active objective; latent replacement metadata is ignored",
    },
    "node_disabled_door_open": {
        "text": "ROUTE OPEN  FOLLOW CAMPAIGN OBJECTIVE",
        "palette": 0,
        "role": "active open-route objective",
    },
    "dash_route_open": {
        "text": "ROUTE OPEN  FOLLOW CAMPAIGN OBJECTIVE",
        "palette": 0,
        "role": "active dash-route objective",
    },
    "terminal_hold_19": {
        "text": "COMMIT HOLD 19/20 KEEP B HELD",
        "palette": 0,
        "role": "exact active completion hold progress",
    },
    "terminal_complete_20": {
        "text": "PROGRAM STABLE  EAST LINK COMPLETE",
        "palette": 2,
        "role": "terminal stable state has highest HUD priority",
    },
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def canonical_schedules() -> tuple[bytes, bytes, bytes]:
    """Return the live B03 module's exact schedule objects."""
    return tuple(  # type: ignore[return-value]
        b03.expected_attempt_inputs(attempt) for attempt in range(3)
    )


def schedule_provenance() -> dict[str, Any]:
    schedules = canonical_schedules()
    if tuple(map(len, schedules)) != (720, 720, 292):
        raise EvidenceFailure(
            "B03 visual plan requires the reviewed 720+720+292 schedule"
        )
    for spec in CAPTURE_PLAN:
        if spec.attempt != 2 or spec.tick not in b03.ATTEMPT_CAPTURE_TICKS[2]:
            raise EvidenceFailure(
                f"capture {spec.name} is not a reviewed B03 checkpoint"
            )
        if not 1 <= spec.tick <= len(schedules[spec.attempt]):
            raise EvidenceFailure(
                f"capture {spec.name} is outside its imported schedule"
            )
    framed = b"".join(
        len(schedule).to_bytes(2, "big") + schedule
        for schedule in schedules
    )
    return {
        "source_module": "tools.m5_b03_runtime_evidence",
        "source_symbols": [
            "ATTEMPT_0_INPUTS",
            "ATTEMPT_1_INPUTS",
            "ATTEMPT_2_INPUTS",
        ],
        "lengths": [len(schedule) for schedule in schedules],
        "attempt_sha256": [
            _sha256_bytes(schedule) for schedule in schedules
        ],
        "framed_bundle_sha256": _sha256_bytes(framed),
        "capture_plan": [
            {
                "name": spec.name,
                "attempt": spec.attempt,
                "tick": spec.tick,
                "objective": spec.objective,
                "output": spec.output,
                "expected_status_presentation": EXPECTED_STATUS_PRESENTATION[
                    spec.name
                ],
            }
            for spec in CAPTURE_PLAN
        ],
    }


def _drive_prefix(spec: CaptureSpec) -> bytes:
    schedules = canonical_schedules()
    return b"".join(
        (
            schedules[0],
            schedules[1],
            schedules[2][: spec.tick],
        )
    )


def install_screenshot_binding(config_path: Path) -> dict[str, Any]:
    """Replace only the isolated profile's default P screenshot binding."""
    text = config_path.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    if re.search(
        rf"(?m)^\s*{re.escape(SCREENSHOT_CONFIG_KEY)}\s+", text
    ):
        raise EvidenceFailure("isolated profile already maps F1")
    screenshot_bindings = re.findall(
        r"(?m)^\s*(\S+)\s+ui\.screenshot\s*$", text
    )
    matches = list(
        re.finditer(r"(?m)^(?P<indent>\s*)p\s+ui\.screenshot\s*$", text)
    )
    if screenshot_bindings != ["p"] or len(matches) != 1:
        raise EvidenceFailure(
            "isolated profile must contain exactly default P ui.screenshot"
        )
    match = matches[0]
    replacement = (
        f"{match.group('indent')}{SCREENSHOT_CONFIG_KEY} ui.screenshot"
    )
    text = text[: match.start()] + replacement + text[match.end() :]
    installed = re.findall(
        r"(?m)^\s*(\S+)\s+ui\.screenshot\s*$", text
    )
    if installed != [SCREENSHOT_CONFIG_KEY]:
        raise EvidenceFailure(
            "isolated profile screenshot binding installation drifted"
        )
    config_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "status": "pass",
        "removed_default_key": "p",
        "installed_config_key": SCREENSHOT_CONFIG_KEY,
        "windows_key": {
            "name": SCREENSHOT_KEY[0],
            "virtual_key": SCREENSHOT_KEY[1],
            "scan_code": SCREENSHOT_KEY[2],
            "extended": SCREENSHOT_KEY[3],
        },
        "only_ui_screenshot_binding": True,
        "config_sha256": sha256_file(config_path),
    }


def bind_session_screenshot(
    controller_profile: dict[str, Any],
) -> None:
    before = controller_profile["sha256"]
    binding = install_screenshot_binding(
        Path(controller_profile["path"])
    )
    controller_profile["session_isolation"][
        "config_sha256_before_screenshot_binding"
    ] = before
    controller_profile["screenshot_binding"] = binding
    controller_profile["sha256"] = binding["config_sha256"]


def derive_renderer_return_address(
    rom: bytes, symbols: dict[str, int]
) -> dict[str, Any]:
    """Derive the instruction immediately after the unique renderer call."""
    call = symbols["MainLoopPostGameplayStep"]
    target = symbols["GameplayRendererBuildAndQueue"]
    if call < 0 or call + 6 > len(rom):
        raise EvidenceFailure("renderer call symbol is outside the ROM")
    if rom[call : call + 2] == b"\x4e\xb9":
        resolved = int.from_bytes(rom[call + 2 : call + 6], "big")
        width = 6
        encoding = "jsr_abs_l"
    elif rom[call : call + 2] == b"\x61\x00":
        displacement = int.from_bytes(
            rom[call + 2 : call + 4], "big", signed=True
        )
        resolved = call + 2 + displacement
        width = 4
        encoding = "bsr_w"
    else:
        raise EvidenceFailure(
            "MainLoopPostGameplayStep is not a supported renderer call"
        )
    if resolved != target:
        raise EvidenceFailure(
            "MainLoopPostGameplayStep resolves to "
            f"${resolved:08X}, not renderer ${target:08X}"
        )
    return {
        "call_address": call,
        "call_address_hex": f"{call:08X}",
        "target_address": target,
        "target_address_hex": f"{target:08X}",
        "encoding": encoding,
        "instruction_hex": rom[call : call + width].hex().upper(),
        "return_address": call + width,
        "return_address_hex": f"{call + width:08X}",
        "derivation": (
            "decoded call exactly at MainLoopPostGameplayStep and required "
            "its target to equal GameplayRendererBuildAndQueue"
        ),
    }


def validate_requested_output_hashes(
    expected_rom: str, expected_symbols: str
) -> dict[str, Any]:
    """Require the caller and collector to name the same reviewed outputs."""
    exact = (
        expected_rom == PINNED_B03_ROM_SHA256
        and expected_symbols == PINNED_B03_SYMBOL_SHA256
    )
    if not exact:
        raise EvidenceFailure(
            "M5 visual capture is pinned to B03 ROM/symbol outputs "
            f"{PINNED_B03_ROM_SHA256}/{PINNED_B03_SYMBOL_SHA256}; got "
            f"{expected_rom}/{expected_symbols}"
        )
    return {
        "status": "pass",
        "rom_sha256": expected_rom,
        "symbol_map_sha256": expected_symbols,
        "exact_reviewed_outputs": True,
    }


def _hud_tile(character: str) -> int:
    if character == " ":
        return 0
    if "0" <= character <= "9":
        return 1 + ord(character) - ord("0")
    if "A" <= character <= "Z":
        return 11 + ord(character) - ord("A")
    punctuation = {":": 134, ".": 135, "/": 136, "-": 137}
    if character in punctuation:
        return punctuation[character]
    raise ValueError(f"unsupported renderer HUD character {character!r}")


def _hud_character(tile: int) -> str:
    if tile == 0:
        return " "
    if 1 <= tile <= 10:
        return chr(ord("0") + tile - 1)
    if 11 <= tile <= 36:
        return chr(ord("A") + tile - 11)
    punctuation = {134: ":", 135: ".", 136: "/", 137: "-"}
    return punctuation.get(tile, "?")


def parse_renderer_hud(renderer_raw: bytes) -> dict[str, Any]:
    """Decode the exact three 40-cell HUD rows from renderer-owned RAM."""
    end = RENDERER_HUD_BUFFER_OFFSET + 3 * RENDERER_HUD_ROW_BYTES
    if len(renderer_raw) != RENDERER_DECLARED_BYTES or end > len(renderer_raw):
        raise EvidenceFailure("renderer HUD source is outside its exact RAM image")
    rows: list[dict[str, Any]] = []
    for row_index in range(3):
        start = RENDERER_HUD_BUFFER_OFFSET + row_index * RENDERER_HUD_ROW_BYTES
        words = [
            int.from_bytes(renderer_raw[offset : offset + 2], "big")
            for offset in range(start, start + RENDERER_HUD_ROW_BYTES, 2)
        ]
        tiles = [word & RENDERER_TILE_MASK for word in words]
        rows.append(
            {
                "index": row_index,
                "words": words,
                "tiles": tiles,
                "palettes": [(word >> 13) & 0x3 for word in words],
                "priorities": [bool(word & 0x8000) for word in words],
                "text": "".join(_hud_character(tile) for tile in tiles).rstrip(),
                "raw_sha256": _sha256_bytes(
                    renderer_raw[start : start + RENDERER_HUD_ROW_BYTES]
                ),
            }
        )
    return {
        "row_count": len(rows),
        "row_words": RENDERER_HUD_ROW_WORDS,
        "rows": rows,
    }


def renderer_hud_row_exact(
    renderer: dict[str, Any],
    *,
    row_index: int,
    text: str,
    palette: int,
) -> bool:
    """Match text, tile IDs, priority, palette, blanks, and trailing cells."""
    if (
        not 0 <= row_index < 3
        or not 0 <= palette <= 3
        or len(text) > RENDERER_HUD_ROW_WORDS
    ):
        return False
    try:
        row = renderer["hud"]["rows"][row_index]
        words = row["words"]
    except (KeyError, IndexError, TypeError):
        return False
    expected: list[int] = []
    try:
        for character in text.ljust(RENDERER_HUD_ROW_WORDS):
            tile = _hud_tile(character)
            expected.append(0 if tile == 0 else 0x8000 | (palette << 13) | tile)
    except ValueError:
        return False
    return (
        len(words) == RENDERER_HUD_ROW_WORDS
        and words == expected
        and row.get("text") == text.rstrip()
    )


def parse_sat_shadow(raw: bytes, *, sprite_count: int) -> dict[str, Any]:
    """Decode the exact renderer SAT shadow and its bounded link chain."""
    if len(raw) != SAT_SHADOW_BYTES:
        raise EvidenceFailure(
            f"SAT shadow read is {len(raw)} bytes; {SAT_SHADOW_BYTES} required"
        )
    if not 0 <= sprite_count <= SAT_CAPACITY:
        raise EvidenceFailure(f"renderer sprite count {sprite_count} is invalid")
    entries: list[dict[str, Any]] = []
    for index in range(sprite_count):
        offset = index * SAT_ENTRY_BYTES
        y_word = int.from_bytes(raw[offset : offset + 2], "big")
        size_link = int.from_bytes(raw[offset + 2 : offset + 4], "big")
        attribute = int.from_bytes(raw[offset + 4 : offset + 6], "big")
        x_word = int.from_bytes(raw[offset + 6 : offset + 8], "big")
        width_tiles = ((size_link >> 10) & 0x3) + 1
        height_tiles = ((size_link >> 8) & 0x3) + 1
        entries.append(
            {
                "index": index,
                "x": (x_word & 0x01FF) - RENDERER_SPRITE_COORD_BIAS,
                "y": (y_word & 0x01FF) - RENDERER_SPRITE_COORD_BIAS,
                "width_tiles": width_tiles,
                "height_tiles": height_tiles,
                "width_pixels": width_tiles * 8,
                "height_pixels": height_tiles * 8,
                "link": size_link & 0x7F,
                "tile": attribute & RENDERER_TILE_MASK,
                "palette": (attribute >> 13) & 0x3,
                "priority": bool(attribute & 0x8000),
                "hflip": bool(attribute & 0x0800),
                "vflip": bool(attribute & 0x1000),
                "raw_hex": raw[offset : offset + SAT_ENTRY_BYTES].hex().upper(),
            }
        )
    links = [entry["link"] for entry in entries]
    expected_links = [*range(1, sprite_count), 0] if sprite_count else []
    tail = raw[sprite_count * SAT_ENTRY_BYTES :]
    return {
        "bytes": len(raw),
        "capacity": SAT_CAPACITY,
        "sprite_count": sprite_count,
        "entries": entries,
        "links": links,
        "expected_links": expected_links,
        "link_chain_exact": links == expected_links,
        "unused_tail_zero": not any(tail),
        "raw_sha256": _sha256_bytes(raw),
    }


def _capture_coherent_state(
    remote: GdbRemote,
    *,
    label: str,
    symbols: dict[str, int],
) -> dict[str, Any]:
    snapshot = b03._capture_snapshot(  # noqa: SLF001
        remote, label=label, symbols=symbols
    )
    sat_raw = _read_region(remote, SAT_SHADOW_BASE, SAT_SHADOW_BYTES)
    renderer_raw = _read_region(
        remote, m4_visual.RENDERER_STATE_BASE, RENDERER_DECLARED_BYTES
    )
    renderer = m4_visual.parse_renderer_state(
        renderer_raw[: m4_visual.RENDERER_STATE_BYTES]
    )
    renderer["last_checkpoint_count"] = int.from_bytes(
        renderer_raw[
            RENDERER_LAST_CHECKPOINT_COUNT_OFFSET:
            RENDERER_LAST_CHECKPOINT_COUNT_OFFSET + 2
        ],
        "big",
    )
    renderer["header_sha256"] = renderer["raw_sha256"]
    renderer["raw_sha256"] = _sha256_bytes(renderer_raw)
    renderer["hud"] = parse_renderer_hud(renderer_raw)
    renderer["sat"] = parse_sat_shadow(
        sat_raw, sprite_count=renderer["sprite_count"]
    )
    snapshot["renderer"] = renderer
    snapshot["_sat_bytes"] = sat_raw
    snapshot["_renderer_bytes"] = renderer_raw
    return snapshot


def _state_manifest(snapshot: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in snapshot.items()
        if not key.startswith("_")
    }
    raw_regions = {
        RAW_REGION_NAMES[key]: {
            "bytes": len(snapshot[key]),
            "sha256": _sha256_bytes(snapshot[key]),
            "raw_hex": snapshot[key].hex().upper(),
        }
        for key in RAW_REGION_NAMES
    }
    authoritative = b"".join(snapshot[key] for key in AUTHORITATIVE_RAW_KEYS)
    complete = b"".join(snapshot[key] for key in RAW_REGION_NAMES)
    return {
        **public,
        "raw_regions": raw_regions,
        "authoritative_bundle_sha256": _sha256_bytes(authoritative),
        "complete_read_bundle_sha256": _sha256_bytes(complete),
        "complete_read_bundle_bytes": len(complete),
    }


def audit_state_manifest_raw(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute every raw-region and aggregate hash in one manifest."""
    expected_sizes = {
        name: size for name, _address, size in STATE_READ_REGION_SPECS
    }
    regions = manifest.get("raw_regions", {})
    errors: list[str] = []
    decoded: dict[str, bytes] = {}
    if not isinstance(regions, dict) or set(regions) != set(expected_sizes):
        errors.append(
            "raw region names differ from the exact capture read plan"
        )
    if isinstance(regions, dict):
        for name, expected_size in expected_sizes.items():
            item = regions.get(name, {})
            raw_hex = item.get("raw_hex") if isinstance(item, dict) else None
            if (
                not isinstance(raw_hex, str)
                or not re.fullmatch(
                    rf"[0-9A-F]{{{expected_size * 2}}}", raw_hex
                )
            ):
                errors.append(f"{name} raw_hex is not exactly {expected_size} bytes")
                continue
            raw = bytes.fromhex(raw_hex)
            decoded[name] = raw
            if item.get("bytes") != expected_size:
                errors.append(f"{name} byte count drifted")
            if item.get("sha256") != _sha256_bytes(raw):
                errors.append(f"{name} SHA-256 does not match raw_hex")
    authoritative = b"".join(
        decoded.get(name, b"") for name in AUTHORITATIVE_REGION_NAMES
    )
    complete = b"".join(
        decoded.get(name, b"")
        for name, _address, _size in STATE_READ_REGION_SPECS
    )
    expected_complete_bytes = sum(expected_sizes.values())
    if len(decoded) == len(expected_sizes):
        if manifest.get("authoritative_bundle_sha256") != _sha256_bytes(
            authoritative
        ):
            errors.append("authoritative bundle SHA-256 drifted")
        if manifest.get("complete_read_bundle_sha256") != _sha256_bytes(
            complete
        ):
            errors.append("complete read bundle SHA-256 drifted")
        if manifest.get("complete_read_bundle_bytes") != expected_complete_bytes:
            errors.append("complete read bundle byte count drifted")
    return {
        "schema": "r94-m5-b03-visual-raw-manifest-audit-v1",
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "region_count": len(decoded),
        "expected_region_count": len(expected_sizes),
        "complete_read_bundle_bytes": len(complete),
        "expected_complete_read_bundle_bytes": expected_complete_bytes,
        "authoritative_bundle_sha256": (
            _sha256_bytes(authoritative)
            if len(decoded) == len(expected_sizes)
            else None
        ),
        "complete_read_bundle_sha256": (
            _sha256_bytes(complete)
            if len(decoded) == len(expected_sizes)
            else None
        ),
    }


def reconstruct_state_manifest_from_raw(
    manifest: dict[str, Any], *, symbols: dict[str, int]
) -> dict[str, Any]:
    """Decode a report manifest solely from its exact raw-region bytes."""
    audit = audit_state_manifest_raw(manifest)
    if audit["status"] != "pass":
        raise EvidenceFailure(f"raw state manifest failed audit: {audit}")
    raw = {
        name: bytes.fromhex(item["raw_hex"])
        for name, item in manifest["raw_regions"].items()
    }
    snapshot = b03.make_snapshot(
        label=str(manifest.get("label", "reconstructed")),
        control_raw=raw["engine_control"],
        gameplay_raw=raw["gameplay_control"],
        vocabulary_raw=raw["vocabulary_state"],
        descriptor_raw=raw["encounter_descriptor"],
        rectangle_raw=raw["canonical_rectangles"],
        device_raw=raw["device_pool"],
        projectile_raw=raw["projectile_pool"],
        hazard_raw=raw["hazard_pool"],
        actor_raw=raw["actor_pool"],
        input_raw=raw["input_state"],
        replay_raw=raw["replay_timeline"],
        projection_raw=raw["engine_projection"],
        symbols=symbols,
    )
    renderer_raw = raw["renderer_state"]
    sat_raw = raw["sat_shadow"]
    renderer = m4_visual.parse_renderer_state(
        renderer_raw[: m4_visual.RENDERER_STATE_BYTES]
    )
    renderer["last_checkpoint_count"] = int.from_bytes(
        renderer_raw[
            RENDERER_LAST_CHECKPOINT_COUNT_OFFSET:
            RENDERER_LAST_CHECKPOINT_COUNT_OFFSET + 2
        ],
        "big",
    )
    renderer["header_sha256"] = renderer["raw_sha256"]
    renderer["raw_sha256"] = _sha256_bytes(renderer_raw)
    renderer["hud"] = parse_renderer_hud(renderer_raw)
    renderer["sat"] = parse_sat_shadow(
        sat_raw, sprite_count=renderer["sprite_count"]
    )
    snapshot["renderer"] = renderer
    snapshot["_sat_bytes"] = sat_raw
    snapshot["_renderer_bytes"] = renderer_raw
    return _state_manifest(snapshot)


def audit_state_manifest_reconstruction(
    manifest: dict[str, Any], *, symbols: dict[str, int]
) -> dict[str, Any]:
    """Require the complete public manifest to equal its raw-byte decode."""
    raw_audit = audit_state_manifest_raw(manifest)
    errors: list[str] = []
    reconstructed: dict[str, Any] | None = None
    if raw_audit["status"] != "pass":
        errors.append("raw envelope audit failed")
    else:
        try:
            reconstructed = reconstruct_state_manifest_from_raw(
                manifest, symbols=symbols
            )
        except Exception as error:
            errors.append(
                "raw decode failed: "
                f"{type(error).__name__}: {error}"
            )
    mismatched_fields: list[str] = []
    if reconstructed is not None and manifest != reconstructed:
        mismatched_fields = sorted(
            key
            for key in set(manifest) | set(reconstructed)
            if manifest.get(key) != reconstructed.get(key)
        )
        errors.append(
            "public parsed fields differ from the exact raw-byte decode"
        )
    return {
        "schema": "r94-m5-b03-visual-state-reconstruction-audit-v1",
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "mismatched_top_level_fields": mismatched_fields,
        "raw_manifest_audit": raw_audit,
        "exact_manifest_equality": reconstructed == manifest,
        "reconstructed_complete_read_bundle_sha256": (
            reconstructed.get("complete_read_bundle_sha256")
            if reconstructed is not None
            else None
        ),
        "reconstructed_authoritative_bundle_sha256": (
            reconstructed.get("authoritative_bundle_sha256")
            if reconstructed is not None
            else None
        ),
    }


def renderer_health(
    renderer: dict[str, Any], *, attempt: int, tick: int
) -> dict[str, bool]:
    return {
        "identity": (
            renderer.get("magic") == RENDERER_MAGIC
            and renderer.get("version") == RENDERER_VERSION
            and renderer.get("declared_bytes") == RENDERER_DECLARED_BYTES
            and bool(
                int(renderer.get("flags", 0))
                & RENDERER_FLAG_INITIALIZED
            )
        ),
        "coordinate": (
            renderer.get("last_attempt") == attempt
            and renderer.get("last_tick") == tick
        ),
        "fault_flags_clear": not (
            int(renderer.get("flags", 0)) & RENDERER_FAULT_FLAGS
        ),
        "fault_counters_clear": (
            renderer.get("dropped_sprites") == 0
            and renderer.get("dma_rejects") == 0
            and renderer.get("backlog_frames") == 0
            and renderer.get("last_result") == 0
        ),
        "bounded_ledgers": (
            0 <= int(renderer.get("sprite_count", -1))
            <= RENDERER_SPRITE_LIMIT
            and 0 <= int(renderer.get("scanline_sprite_max", -1))
            <= RENDERER_SCANLINE_SPRITE_LIMIT
            and 0 <= int(renderer.get("scanline_pixel_max", -1))
            <= RENDERER_SCANLINE_PIXEL_LIMIT
        ),
    }


def _base_state_facts(
    state: dict[str, Any], *, attempt: int, tick: int
) -> dict[str, bool]:
    engine = state["engine"]
    campaign = state["vocabulary"]["campaign"]
    renderer = state["renderer"]
    return {
        "exact_coordinate": (
            engine["attempt"] == attempt and engine["tick"] == tick
        ),
        "campaign_b03": (
            engine["encounter"] == b03.B03_DIRECTORY_INDEX
            and state["vocabulary"]["stage_id"] == b03.B03_STAGE_ID
        ),
        "engine_faults_clear": (
            engine["last_error"] == 0
            and engine["overflow_flags"] == 0
            and not campaign["flags"]
            & b03.VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR
            and state["projection"]["published_crc_matches"]
        ),
        **{
            f"renderer_{name}": value
            for name, value in renderer_health(
                renderer, attempt=attempt, tick=tick
            ).items()
        },
    }


def capture_semantic_facts(
    name: str, state: dict[str, Any]
) -> dict[str, bool]:
    specs = {spec.name: spec for spec in CAPTURE_PLAN}
    if name not in specs:
        raise ValueError(f"unknown B03 visual capture {name!r}")
    spec = specs[name]
    facts = _base_state_facts(
        state, attempt=spec.attempt, tick=spec.tick
    )
    devices = state["devices"]
    actors = state["actors"]
    current = state["current_actor"]
    campaign = state["vocabulary"]["campaign"]
    timeline = state["replay_timeline"]["timeline"]
    extension = state["vocabulary"]["actor_extensions"][
        b03.CURRENT_ACTOR_SLOT
    ]
    hazard = state["hazards"][0]
    renderer = state["renderer"]
    sat = renderer.get("sat", {})
    sat_entries = sat.get("entries", [])
    expected_status = EXPECTED_STATUS_PRESENTATION[name]
    facts.update(
        {
            "renderer_sat_exact_chain": (
                sat.get("sprite_count") == renderer.get("sprite_count")
                and sat.get("link_chain_exact") is True
                and sat.get("unused_tail_zero") is True
                and _valid_sha256(sat.get("raw_sha256"))
            ),
            "renderer_status_row_exact": renderer_hud_row_exact(
                renderer,
                row_index=2,
                text=expected_status["text"],
                palette=expected_status["palette"],
            ),
        }
    )

    if name == "two_echo_armed":
        facts.update(
            {
                "two_committed_echoes": (
                    timeline["echo_count"] == 2
                    and timeline["echo_order"][:2] == [6, 0]
                    and actors[0]["active"] == 1
                    and actors[0]["logical_id"] == 6
                    and actors[1]["active"] == 1
                    and actors[1]["logical_id"] == 0
                    and current["active"] == 1
                    and current["logical_id"] == 1
                ),
                "both_switches_active": (
                    bool(
                        devices[0]["state"]
                        & b03.VOCAB_DEVICE_STATE_ACTIVE
                    )
                    and bool(
                        devices[1]["state"]
                        & b03.VOCAB_DEVICE_STATE_ACTIVE
                    )
                ),
                "node_armed": (
                    bool(
                        devices[2]["state"]
                        & b03.VOCAB_DEVICE_STATE_ACTIVE
                    )
                    and devices[2]["state"] >> 8 == 3
                    and not devices[2]["state"]
                    & b03.VOCAB_DEVICE_STATE_DISABLED
                ),
                "door_closed": not (
                    devices[3]["state"] & b03.VOCAB_DEVICE_STATE_OPEN
                ),
                "hazard_geometry_present": (
                    hazard["active"] == 1
                    and hazard["logical_id"] == 5
                    and hazard["x_16_8"] == 152 * 256
                    and hazard["y_16_8"] == 80 * 256
                    and hazard["param"] == 0x0120
                ),
                "active_hud_ignores_latent_replacement": (
                    state["engine"]["state"] == b03.ENGINE_STATE_ACTIVE
                    and bool(timeline["flags"] & 0x01)
                    and renderer["hud"]["rows"][2]["text"]
                    == expected_status["text"]
                    and "REPLACE" not in renderer["hud"]["rows"][2]["text"]
                ),
            }
        )
        node = devices[2]
        node_geometry = [
            entry
            for entry in sat_entries
            if entry.get("x") == (node["x_16_8"] >> 8) - 8
            and entry.get("y") == (node["y_16_8"] >> 8) - 8
            and entry.get("width_tiles") == 2
            and entry.get("height_tiles") == 2
        ]
        facts.update(
            {
                "armed_node_unique_geometry": len(node_geometry) == 1,
                "armed_node_solid_palette_2": (
                    len(node_geometry) == 1
                    and node_geometry[0].get("tile")
                    == RENDERER_TILE_TERMINAL_STABLE
                    and node_geometry[0].get("palette") == 2
                    and node_geometry[0].get("priority") is True
                ),
            }
        )
    elif name == "node_disabled_door_open":
        facts.update(
            {
                "node_disabled": (
                    bool(
                        devices[2]["state"]
                        & b03.VOCAB_DEVICE_STATE_DISABLED
                    )
                    and devices[2]["state"] >> 8 == 0
                ),
                "door_dependency_open": (
                    bool(
                        devices[3]["state"]
                        & b03.VOCAB_DEVICE_STATE_OPEN
                    )
                    and devices[3]["timer"] == 150
                ),
                "completion_route_unlocked": bool(
                    devices[4]["state"] & b03.VOCAB_DEVICE_STATE_OPEN
                ),
                "hazard_still_present": (
                    hazard["active"] == 1 and hazard["logical_id"] == 5
                ),
                "two_echoes_still_composed": timeline["echo_count"] == 2,
            }
        )
        node = devices[2]
        node_geometry = [
            entry
            for entry in sat_entries
            if entry.get("x") == (node["x_16_8"] >> 8) - 8
            and entry.get("y") == (node["y_16_8"] >> 8) - 8
            and entry.get("width_tiles") == 2
            and entry.get("height_tiles") == 2
        ]
        facts.update(
            {
                "disabled_node_unique_geometry": len(node_geometry) == 1,
                "disabled_node_hollow_palette_0": (
                    len(node_geometry) == 1
                    and node_geometry[0].get("tile")
                    == RENDERER_TILE_TERMINAL
                    and node_geometry[0].get("palette") == 0
                    and node_geometry[0].get("priority") is True
                ),
            }
        )
    elif name == "dash_route_open":
        facts.update(
            {
                "door_open": bool(
                    devices[3]["state"] & b03.VOCAB_DEVICE_STATE_OPEN
                ),
                "dash_route_latched": devices[4]["state"]
                == (
                    b03.VOCAB_DEVICE_STATE_OPEN
                    | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
                ),
                "current_east_of_leaf": (
                    current["x_16_8"] >= 180 * 256
                    and current["x_16_8"]
                    - current["half_width"] * 256
                    >= 176 * 256
                ),
                "current_dash_visible_state": (
                    extension["flags"]
                    & (
                        b03.VOCAB_ACTOR_FLAG_DASHING
                        | b03.VOCAB_ACTOR_FLAG_PHASED
                    )
                    == (
                        b03.VOCAB_ACTOR_FLAG_DASHING
                        | b03.VOCAB_ACTOR_FLAG_PHASED
                    )
                    and extension["dash_timer"] > 0
                ),
                "two_echoes_still_composed": timeline["echo_count"] == 2,
            }
        )
        actor_x = current["x_16_8"] >> 8
        actor_y = current["y_16_8"] >> 8
        body_geometry = [
            entry
            for entry in sat_entries
            if entry.get("x") == actor_x - 12
            and entry.get("y") == actor_y - 12
            and entry.get("width_tiles") == 3
            and entry.get("height_tiles") == 3
            and entry.get("tile")
            in (RENDERER_TILE_CURRENT_0, RENDERER_TILE_CURRENT_1)
        ]
        badge_geometry = [
            entry
            for entry in sat_entries
            if entry.get("x") == actor_x + 17
            and entry.get("y") == actor_y - 24
            and entry.get("width_tiles") == 1
            and entry.get("height_tiles") == 1
        ]
        facts.update(
            {
                "dash_current_geometry_preserved": (
                    len(body_geometry) == 1 and len(badge_geometry) == 1
                ),
                "dash_current_rust_palette_3": (
                    len(body_geometry) == 1
                    and body_geometry[0].get("palette") == 3
                    and body_geometry[0].get("priority") is True
                ),
                "dash_current_D_badge_palette_3": (
                    len(badge_geometry) == 1
                    and badge_geometry[0].get("tile")
                    == RENDERER_DASH_BADGE_TILE
                    and badge_geometry[0].get("palette") == 3
                    and badge_geometry[0].get("priority") is True
                ),
            }
        )
    elif name == "terminal_hold_19":
        facts.update(
            {
                "active_not_complete": (
                    state["engine"]["state"] == b03.ENGINE_STATE_ACTIVE
                    and not campaign["flags"]
                    & b03.VOCAB_CAMPAIGN_FLAG_COMPLETE
                ),
                "hold_timer_19": (
                    devices[4]["state"]
                    == (
                        b03.VOCAB_DEVICE_STATE_OPEN
                        | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
                        | b03.VOCAB_DEVICE_STATE_ACTIVE
                    )
                    and devices[4]["timer"] == 19
                ),
                "current_only_owner": (
                    devices[4]["vx_16_8"]
                    == b03.CURRENT_ACTOR_SLOT + 1
                    and devices[4]["vy_16_8"] == 291
                    and campaign["last_interaction_actor"]
                    == b03.CURRENT_ACTOR_SLOT
                    and campaign["last_interaction_object"]
                    == b03.B03_COMPLETION_OBJECT
                ),
                "held_without_new_edge": (
                    state["engine"]["input_held"] == b03.INPUT_B
                    and state["engine"]["input_pressed"] == 0
                    and current["input"] == b03.INPUT_B
                    and state["gameplay"]["current_pressed"] == 0
                ),
                "two_echoes_still_composed": timeline["echo_count"] == 2,
                "hold_19_hud_exact": renderer["hud"]["rows"][2]["text"]
                == "COMMIT HOLD 19/20 KEEP B HELD",
                "hold_19_checkpoint_ordinal_exact": (
                    state["engine"]["checkpoint_count"]
                    == TERMINAL_PREVIOUS_CHECKPOINT_COUNT
                    and renderer.get("last_checkpoint_count")
                    == TERMINAL_PREVIOUS_CHECKPOINT_COUNT
                ),
            }
        )
    elif name == "terminal_complete_20":
        facts.update(
            {
                "program_stable_terminal": (
                    state["engine"]["state"] == b03.ENGINE_STATE_TERMINAL
                    and state["engine"]["terminal_reason"]
                    == b03.GAMEPLAY_TERMINAL_PROGRAM_STABLE
                    and state["gameplay"]["result"]
                    == b03.GAMEPLAY_RESULT_PROGRAM_STABLE
                ),
                "hold_timer_20": (
                    devices[4]["timer"] == 20
                    and devices[4]["state"] & b03.B03_COMPLETION_MASK
                    == b03.B03_COMPLETION_VALUE
                    and devices[4]["vx_16_8"]
                    == b03.CURRENT_ACTOR_SLOT + 1
                    and devices[4]["vy_16_8"] == 292
                ),
                "campaign_completed_by_current": (
                    bool(
                        campaign["flags"]
                        & b03.VOCAB_CAMPAIGN_FLAG_COMPLETE
                    )
                    and campaign["last_interaction_actor"]
                    == b03.CURRENT_ACTOR_SLOT
                    and campaign["last_interaction_object"]
                    == b03.B03_COMPLETION_OBJECT
                ),
                "stage_complete_result": state["vocabulary"]["result"]
                == b03.VOCAB_RESULT_STAGE_COMPLETE,
                "two_echoes_preserved": timeline["echo_count"] == 2,
                "terminal_stable_hud_priority": (
                    renderer["hud"]["rows"][2]["text"]
                    == "PROGRAM STABLE  EAST LINK COMPLETE"
                    and "REPLACE" not in renderer["hud"]["rows"][2]["text"]
                    and "COMMIT HOLD" not in renderer["hud"]["rows"][2]["text"]
                ),
                "terminal_checkpoint_ordinal_exact": (
                    state["engine"]["checkpoint_count"]
                    == TERMINAL_CHECKPOINT_COUNT
                    and renderer.get("last_checkpoint_count")
                    == TERMINAL_CHECKPOINT_COUNT
                ),
            }
        )
    return {key: bool(value) for key, value in facts.items()}


def _expected_successor(spec: CaptureSpec) -> tuple[int, int, int]:
    if spec.name == "terminal_complete_20":
        return (
            b03.ENGINE_STATE_TERMINAL,
            spec.attempt,
            spec.tick,
        )
    if spec.name == "terminal_hold_19":
        return (
            b03.ENGINE_STATE_TERMINAL,
            spec.attempt,
            spec.tick + 1,
        )
    return (b03.ENGINE_STATE_ACTIVE, spec.attempt, spec.tick + 1)


def expected_odd_presentation(
    publication_after_frame: int,
) -> dict[str, Any]:
    """Derive the exact next ODD UI-presentation phase from frame parity."""
    if (
        not isinstance(publication_after_frame, int)
        or isinstance(publication_after_frame, bool)
        or not 0 <= publication_after_frame <= 0xFFFFFFFF
    ):
        return {
            "status": "fail",
            "detail": "publication frame must be an unsigned 32-bit integer",
        }
    expected_idle_count = publication_after_frame & 1
    expected_odd_delta = expected_idle_count + 1
    presentation_frame = (
        publication_after_frame + expected_odd_delta
    ) & 0xFFFFFFFF
    if presentation_frame <= publication_after_frame:
        return {
            "status": "fail",
            "detail": "frame-counter wrap is outside this evidence contract",
        }
    return {
        "status": "pass",
        "publication_after_frame": publication_after_frame,
        "publication_after_parity": (
            "odd" if expected_idle_count else "even"
        ),
        "expected_idle_boundary_count": expected_idle_count,
        "expected_odd_delta": expected_odd_delta,
        "expected_odd_presentation_frame": presentation_frame,
    }


def classify_ui_service_boundary(
    spec: CaptureSpec,
    state: dict[str, Any],
    boundary: dict[str, Any],
    *,
    previous_frame: int,
    target_authoritative_sha256: str,
    target_renderer_sha256: str,
    expected_phase: str,
    target_commit_words: int,
) -> dict[str, Any]:
    """Classify one exact, frame-adjacent DMA-entry observation.

    An odd target-publication frame has exactly one intervening even VBlank
    before BlastEm's next ODD presentation.  That boundary is admissible only
    in the derived ``idle`` phase: the renderer remains the exact target, the
    queue has no published/build work, and the prior commit ledger is
    unchanged because the commit routine has not executed yet.  Gameplay may
    be interrupted mid-successor-step there, so authoritative bytes are
    recorded as diagnostic data and are deliberately not an identity gate.
    The derived ``resolution`` phase then requires the exact successor.

    The completed terminal has no authoritative successor, so its bytes stay
    exact during both idle and resolution phases.  At resolution it may have
    either no queue or an identical terminal-state republish queue.
    """
    try:
        engine = state["engine"]
        renderer = state["renderer"]
        frame = boundary["debug_frame_counter"]
        dma_queue = boundary["dma_queue"]
        pending = dma_queue["frame_pending"]
        authoritative_sha256 = state["authoritative_bundle_sha256"]
    except (KeyError, TypeError) as error:
        return {
            "status": "fail",
            "mode": "malformed",
            "detail": f"missing boundary/state field: {error}",
        }
    if expected_phase not in {"idle", "resolution"}:
        return {
            "status": "fail",
            "mode": "invalid_expected_phase",
            "detail": "expected_phase must be idle or resolution",
        }

    observed = {
        "engine_state": engine.get("state"),
        "attempt": engine.get("attempt"),
        "tick": engine.get("tick"),
        "debug_frame_counter": frame,
        "dma_frame_pending": pending,
    }
    if (
        not isinstance(frame, int)
        or isinstance(frame, bool)
        or frame != previous_frame + 1
    ):
        return {
            "status": "fail",
            "mode": "non_adjacent_frame",
            "observed": observed,
            "previous_frame": previous_frame,
            "detail": "debug frame counter must advance monotonically by one",
        }

    target_state = (
        b03.ENGINE_STATE_TERMINAL
        if spec.name == "terminal_complete_20"
        else b03.ENGINE_STATE_ACTIVE
    )
    target_coordinate = _coordinate_ok(
        state,
        engine_state=target_state,
        attempt=spec.attempt,
        tick=spec.tick,
    )
    target_health = renderer_health(
        renderer, attempt=spec.attempt, tick=spec.tick
    )
    target_identity = (
        _valid_sha256(target_authoritative_sha256)
        and authoritative_sha256 == target_authoritative_sha256
    )
    target_renderer_identity = (
        _valid_sha256(target_renderer_sha256)
        and state.get("raw_regions", {})
        .get("renderer_state", {})
        .get("sha256")
        == target_renderer_sha256
    )
    empty_idle_queue = (
        dma_queue.get("published_depth") == 0
        and dma_queue.get("build_depth") == 0
        and dma_queue.get("build_words") == 0
        and dma_queue.get("frame_pending") == 0
        and dma_queue.get("commit_words") == target_commit_words
        and dma_queue.get("reserved_0") == 0
        and dma_queue.get("reserved_1") == 0
    )
    exact_empty_resolution_queue = empty_idle_queue
    exact_republish_resolution_queue = (
        dma_queue.get("published_depth") == 4
        and dma_queue.get("build_depth") == 4
        and dma_queue.get("build_words") == TARGET_RENDER_COMMIT_WORDS
        and dma_queue.get("commit_words") == target_commit_words
        and dma_queue.get("frame_pending") == 1
        and dma_queue.get("reserved_0") == 0
        and dma_queue.get("reserved_1") == 0
    )

    if expected_phase == "idle":
        if (
            all(target_health.values())
            and target_renderer_identity
            and empty_idle_queue
            and (
                spec.name != "terminal_complete_20"
                or target_identity
            )
        ):
            return {
                "status": "pass",
                "mode": "pre_odd_empty_dma",
                "phase": expected_phase,
                "observed": observed,
                "previous_frame": previous_frame,
                "frame_delta": 1,
                "renderer_health": target_health,
                "target_authoritative_identity": target_identity,
                "target_renderer_identity": True,
                "empty_queue_before_noop_commit": True,
                "historical_commit_words_unchanged": True,
                "authoritative_service_steps": 0,
                "continue_required": True,
            }
        return {
            "status": "fail",
            "mode": "invalid_idle_phase",
            "phase": expected_phase,
            "observed": observed,
            "previous_frame": previous_frame,
            "renderer_health": target_health,
            "target_authoritative_identity": target_identity,
            "target_renderer_identity": target_renderer_identity,
            "empty_queue_before_noop_commit": empty_idle_queue,
            "detail": (
                "derived idle phase requires the exact target renderer plus "
                "an empty pending/build/depth queue; authoritative bytes are "
                "diagnostic because active gameplay may be interrupted "
                "mid-step, while a completed terminal remains exact"
            ),
        }

    if spec.name == "terminal_complete_20":
        if (
            target_coordinate
            and all(target_health.values())
            and target_identity
            and (
                exact_empty_resolution_queue
                or exact_republish_resolution_queue
            )
        ):
            mode = (
                "terminal_no_republish_queue"
                if exact_empty_resolution_queue
                else "terminal_republish_prepared_uncommitted"
            )
            return {
                "status": "pass",
                "mode": mode,
                "phase": expected_phase,
                "observed": observed,
                "previous_frame": previous_frame,
                "frame_delta": 1,
                "renderer_health": target_health,
                "target_authoritative_identity": True,
                "exact_queue_shape": True,
                "authoritative_service_steps": 0,
                "continue_required": False,
            }
        return {
            "status": "fail",
            "mode": "invalid_terminal_resolution",
            "phase": expected_phase,
            "observed": observed,
            "previous_frame": previous_frame,
            "renderer_health": target_health,
            "target_authoritative_identity": target_identity,
            "exact_empty_resolution_queue": exact_empty_resolution_queue,
            "exact_republish_resolution_queue": (
                exact_republish_resolution_queue
            ),
            "detail": (
                "terminal resolution requires exact unchanged terminal "
                "coordinate, healthy renderer, and one exact empty or "
                "440-word republish queue shape"
            ),
        }

    successor_state, successor_attempt, successor_tick = (
        _expected_successor(spec)
    )
    successor_coordinate = _coordinate_ok(
        state,
        engine_state=successor_state,
        attempt=successor_attempt,
        tick=successor_tick,
    )
    successor_health = renderer_health(
        renderer, attempt=successor_attempt, tick=successor_tick
    )
    if (
        successor_coordinate
        and all(successor_health.values())
        and exact_republish_resolution_queue
    ):
        return {
            "status": "pass",
            "mode": "successor_prepared_uncommitted",
            "phase": expected_phase,
            "observed": observed,
            "previous_frame": previous_frame,
            "frame_delta": 1,
            "renderer_health": successor_health,
            "target_authoritative_identity": False,
            "exact_queue_shape": True,
            "authoritative_service_steps": 1,
            "continue_required": False,
        }
    return {
        "status": "fail",
        "mode": "invalid_active_boundary",
        "phase": expected_phase,
        "observed": observed,
        "previous_frame": previous_frame,
        "target_renderer_health": target_health,
        "successor_renderer_health": successor_health,
        "target_authoritative_identity": target_identity,
        "detail": (
            "active boundary must be exact unchanged target/pending=0 or "
            "exact one-step successor/pending=1"
        ),
    }


def classify_odd_dma_boundary(
    spec: CaptureSpec,
    state: dict[str, Any],
    boundary: dict[str, Any],
    *,
    expected_frame: int,
    successor_post: dict[str, Any] | None,
    target_authoritative_sha256: str,
    target_renderer_sha256: str,
    expected_commit_words: int,
) -> dict[str, Any]:
    """Classify the calculated ODD DMA entry before its first instruction.

    Once an active successor has reached POST and built its exact renderer
    queue, the next VBlank may interrupt the following authoritative step.
    The pending framebuffer is therefore bound by the proven successor POST,
    successor renderer coordinate/health, and exact queue shape; the live
    authoritative bytes at that ODD interrupt are diagnostic.  A terminal
    successor cannot begin another step, and an already-completed terminal
    cannot drift before POST, so terminal authoritative identity stays exact.
    """
    try:
        engine_state, attempt, tick = _expected_successor(spec)
        successor_coordinate = _coordinate_ok(
            state,
            engine_state=engine_state,
            attempt=attempt,
            tick=tick,
        )
        target_health = renderer_health(
            state["renderer"],
            attempt=spec.attempt,
            tick=spec.tick,
        )
        target_renderer_identity = (
            state["raw_regions"]["renderer_state"]["sha256"]
            == target_renderer_sha256
        )
        target_authoritative_identity = (
            _valid_sha256(target_authoritative_sha256)
            and state["authoritative_bundle_sha256"]
            == target_authoritative_sha256
        )
        successor_health = renderer_health(
            state["renderer"], attempt=attempt, tick=tick
        )
        frame = boundary["debug_frame_counter"]
        queue = boundary["dma_queue"]
        empty_queue = (
            queue["published_depth"] == 0
            and queue["build_depth"] == 0
            and queue["build_words"] == 0
            and queue["commit_words"] == expected_commit_words
            and queue["frame_pending"] == 0
            and queue.get("reserved_0") == 0
            and queue.get("reserved_1") == 0
        )
        successor_queue_shape = (
            queue["published_depth"] == 4
            and queue["build_depth"] == 4
            and queue["build_words"] == TARGET_RENDER_COMMIT_WORDS
            and queue.get("reserved_0") == 0
            and queue.get("reserved_1") == 0
        )
        successor_authoritative_identity = (
            successor_post is not None
            and state["authoritative_bundle_sha256"]
            == successor_post["authoritative_bundle_sha256"]
        )
        terminal_successor = engine_state == b03.ENGINE_STATE_TERMINAL
        successor_pending = (
            successor_post is not None
            and successor_coordinate
            and all(successor_health.values())
            and (
                not terminal_successor
                or successor_authoritative_identity
            )
            and queue["commit_words"] == expected_commit_words
            and queue["frame_pending"] == 1
            and successor_queue_shape
        )
        target_empty = (
            all(target_health.values())
            and target_renderer_identity
            and empty_queue
            and (
                spec.name != "terminal_complete_20"
                or target_authoritative_identity
            )
            and (
                successor_post is None
                or state["authoritative_bundle_sha256"]
                == successor_post["authoritative_bundle_sha256"]
            )
        )
    except (KeyError, TypeError) as error:
        return {
            "status": "fail",
            "mode": "malformed_odd_dma_boundary",
            "detail": f"{type(error).__name__}: {error}",
        }
    queue_mode = (
        "successor_pending"
        if successor_pending
        else ("target_empty" if target_empty else "invalid")
    )
    passed = frame == expected_frame and queue_mode != "invalid"
    return {
        "status": "pass" if passed else "fail",
        "mode": "odd_dma_precommit",
        "queue_mode": queue_mode,
        "observed": {
            "engine_state": state["engine"]["state"],
            "attempt": state["engine"]["attempt"],
            "tick": state["engine"]["tick"],
            "debug_frame_counter": frame,
            "dma_frame_pending": queue["frame_pending"],
        },
        "expected_frame": expected_frame,
        "successor_post_proven": successor_post is not None,
        "successor_coordinate": successor_coordinate,
        "successor_authoritative_identity": (
            successor_authoritative_identity
        ),
        "successor_authoritative_bytes_role": (
            "exact_terminal_identity"
            if terminal_successor
            else "diagnostic_next_step_preemption"
        ),
        "target_authoritative_identity": target_authoritative_identity,
        "target_renderer_identity": target_renderer_identity,
        "target_renderer_health": target_health,
        "successor_renderer_health": successor_health,
        "empty_queue_before_noop_commit": empty_queue,
        "successor_queue_shape_valid": successor_queue_shape,
        "detail": (
            "calculated ODD DMA entry is target-renderer/empty or exact "
            "successor-renderer/pending"
        ),
    }


def presentation_boundary_summary(
    *,
    index: int,
    phase: str,
    state: dict[str, Any],
    boundary: dict[str, Any],
    classification: dict[str, Any],
) -> dict[str, Any]:
    """Return the hash-bound objective form embedded in frame_binding."""
    return {
        "index": index,
        "phase": phase,
        "publication_boundary": boundary,
        "classification": classification,
        "authoritative_bundle_sha256": state.get(
            "authoritative_bundle_sha256"
        ),
        "complete_read_bundle_sha256": state.get(
            "complete_read_bundle_sha256"
        ),
        "renderer_state_sha256": state.get("raw_regions", {})
        .get("renderer_state", {})
        .get("sha256"),
    }


def _renderer_checks(
    prefix: str,
    state: dict[str, Any],
    *,
    attempt: int,
    tick: int,
) -> list[dict[str, Any]]:
    return [
        _check(
            f"{prefix}_{name}",
            passed,
            f"renderer {name} at attempt {attempt} tick {tick}",
        )
        for name, passed in renderer_health(
            state["renderer"], attempt=attempt, tick=tick
        ).items()
    ]


def audit_terminal_presentation_service(
    spec: CaptureSpec,
    *,
    target_post_step: dict[str, Any],
    checkpoint_hold: dict[str, Any] | None,
    service_post: dict[str, Any] | None,
    target_post_boundary: dict[str, Any],
    checkpoint_hold_boundary: dict[str, Any] | None,
    service_post_boundary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Prove the one held checkpoint frame before terminal presentation.

    B03 completes on tick 292 and advances the projection checkpoint from
    five to six.  The renderer deliberately preserves the tick-291 video for
    that call (result 4).  One terminal meta-input service frame then reaches
    the common POST without calling GameplayStep; the following renderer call
    is the one allowed to build the terminal HUD/SAT and arm the screenshot.
    """
    terminal = spec.name == "terminal_complete_20"
    if not terminal:
        absent = all(
            item is None
            for item in (
                checkpoint_hold,
                service_post,
                checkpoint_hold_boundary,
                service_post_boundary,
            )
        )
        return {
            "schema": "r94-m5-terminal-presentation-service-audit-v1",
            "status": "pass" if absent else "fail",
            "required": False,
            "exact_absence": absent,
            "checks": {"terminal_service_absent": absent},
        }

    def raw_region(manifest: dict[str, Any], name: str) -> bytes:
        return bytes.fromhex(
            manifest["raw_regions"][name]["raw_hex"]
        )

    def exact_empty_queue(
        boundary: dict[str, Any] | None, *, commit_words: int
    ) -> bool:
        if not isinstance(boundary, dict):
            return False
        queue = boundary.get("dma_queue", {})
        return (
            queue.get("published_depth") == 0
            and queue.get("build_depth") == 0
            and queue.get("build_words") == 0
            and queue.get("commit_words") == commit_words
            and queue.get("frame_pending") == 0
            and queue.get("reserved_0") == 0
            and queue.get("reserved_1") == 0
        )

    checks: dict[str, bool] = {}
    if not all(
        isinstance(item, dict)
        for item in (
            target_post_step,
            checkpoint_hold,
            service_post,
            target_post_boundary,
            checkpoint_hold_boundary,
            service_post_boundary,
        )
    ):
        return {
            "schema": "r94-m5-terminal-presentation-service-audit-v1",
            "status": "fail",
            "required": True,
            "checks": {"all_required_records_present": False},
        }

    assert checkpoint_hold is not None
    assert service_post is not None
    assert checkpoint_hold_boundary is not None
    assert service_post_boundary is not None
    try:
        target_renderer_raw = raw_region(target_post_step, "renderer_state")
        hold_renderer_raw = raw_region(checkpoint_hold, "renderer_state")
        service_renderer_raw = raw_region(service_post, "renderer_state")
        target_sat_raw = raw_region(target_post_step, "sat_shadow")
        hold_sat_raw = raw_region(checkpoint_hold, "sat_shadow")
        service_sat_raw = raw_region(service_post, "sat_shadow")
    except (KeyError, TypeError, ValueError):
        return {
            "schema": "r94-m5-terminal-presentation-service-audit-v1",
            "status": "fail",
            "required": True,
            "checks": {"exact_raw_regions_present": False},
        }

    target_engine = target_post_step.get("engine", {})
    hold_engine = checkpoint_hold.get("engine", {})
    service_engine = service_post.get("engine", {})
    target_renderer = target_post_step.get("renderer", {})
    hold_renderer = checkpoint_hold.get("renderer", {})
    service_renderer = service_post.get("renderer", {})
    checks["terminal_coordinate_exact"] = all(
        engine.get("state") == b03.ENGINE_STATE_TERMINAL
        and engine.get("attempt") == spec.attempt
        and engine.get("tick") == spec.tick
        and engine.get("checkpoint_count") == TERMINAL_CHECKPOINT_COUNT
        for engine in (target_engine, hold_engine, service_engine)
    )
    checks["authoritative_state_unchanged"] = (
        target_post_step.get("authoritative_bundle_sha256")
        == checkpoint_hold.get("authoritative_bundle_sha256")
        == service_post.get("authoritative_bundle_sha256")
    )
    checks["target_previous_presentation_exact"] = (
        all(
            renderer_health(
                target_renderer,
                attempt=spec.attempt,
                tick=TERMINAL_PREVIOUS_TICK,
            ).values()
        )
        and target_renderer.get("last_checkpoint_count")
        == TERMINAL_PREVIOUS_CHECKPOINT_COUNT
        and renderer_hud_row_exact(
            target_renderer,
            row_index=2,
            text=EXPECTED_STATUS_PRESENTATION["terminal_hold_19"]["text"],
            palette=EXPECTED_STATUS_PRESENTATION["terminal_hold_19"][
                "palette"
            ],
        )
        and target_renderer.get("sat", {}).get("link_chain_exact") is True
        and target_renderer.get("sat", {}).get("unused_tail_zero") is True
    )

    expected_hold_renderer = bytearray(target_renderer_raw)
    expected_hold_renderer[
        RENDERER_LAST_RESULT_OFFSET:RENDERER_LAST_RESULT_OFFSET + 2
    ] = RENDERER_RESULT_CHECKPOINT_HELD.to_bytes(2, "big")
    expected_hold_renderer[
        RENDERER_LAST_CHECKPOINT_COUNT_OFFSET:
        RENDERER_LAST_CHECKPOINT_COUNT_OFFSET + 2
    ] = TERMINAL_CHECKPOINT_COUNT.to_bytes(2, "big")
    checks["checkpoint_hold_exact_renderer_mutation"] = (
        hold_renderer_raw == bytes(expected_hold_renderer)
        and service_renderer_raw == hold_renderer_raw
        and hold_sat_raw == target_sat_raw
        and service_sat_raw == target_sat_raw
    )
    checks["checkpoint_hold_identity_and_health"] = (
        hold_renderer.get("magic") == RENDERER_MAGIC
        and hold_renderer.get("version") == RENDERER_VERSION
        and hold_renderer.get("declared_bytes") == RENDERER_DECLARED_BYTES
        and bool(
            int(hold_renderer.get("flags", 0))
            & RENDERER_FLAG_INITIALIZED
        )
        and not (
            int(hold_renderer.get("flags", 0)) & RENDERER_FAULT_FLAGS
        )
        and hold_renderer.get("last_attempt") == spec.attempt
        and hold_renderer.get("last_tick") == TERMINAL_PREVIOUS_TICK
        and hold_renderer.get("last_result")
        == RENDERER_RESULT_CHECKPOINT_HELD
        and hold_renderer.get("last_checkpoint_count")
        == TERMINAL_CHECKPOINT_COUNT
        and hold_renderer.get("dropped_sprites") == 0
        and hold_renderer.get("dma_rejects") == 0
        and hold_renderer.get("backlog_frames") == 0
        and service_renderer == hold_renderer
    )

    target_frame = target_post_boundary.get("debug_frame_counter")
    hold_frame = checkpoint_hold_boundary.get("debug_frame_counter")
    service_frame = service_post_boundary.get("debug_frame_counter")
    bounded_hold_delta = (
        isinstance(target_frame, int)
        and not isinstance(target_frame, bool)
        and isinstance(hold_frame, int)
        and not isinstance(hold_frame, bool)
        and hold_frame in (target_frame, target_frame + 1)
    )
    expected_hold_commit = (
        TARGET_RENDER_COMMIT_WORDS
        if hold_frame == target_frame
        else 0
    )
    checks["checkpoint_hold_boundary_exact"] = (
        exact_empty_queue(
            target_post_boundary,
            commit_words=TARGET_RENDER_COMMIT_WORDS,
        )
        and bounded_hold_delta
        and exact_empty_queue(
            checkpoint_hold_boundary,
            commit_words=expected_hold_commit,
        )
    )
    checks["fresh_vblank_service_post_exact"] = (
        isinstance(service_frame, int)
        and not isinstance(service_frame, bool)
        and isinstance(hold_frame, int)
        and service_frame == hold_frame + 1
        and exact_empty_queue(service_post_boundary, commit_words=0)
    )
    status = "pass" if all(checks.values()) else "fail"
    return {
        "schema": "r94-m5-terminal-presentation-service-audit-v1",
        "status": status,
        "required": True,
        "checks": checks,
        "target_post_frame": target_frame,
        "checkpoint_hold_frame": hold_frame,
        "checkpoint_hold_vblank_delta": (
            hold_frame - target_frame
            if isinstance(target_frame, int)
            and isinstance(hold_frame, int)
            else None
        ),
        "service_post_frame": service_frame,
        "fresh_vblank_service_delta": (
            service_frame - hold_frame
            if isinstance(service_frame, int)
            and isinstance(hold_frame, int)
            else None
        ),
        "expected_checkpoint_count": TERMINAL_CHECKPOINT_COUNT,
        "held_renderer_tick": TERMINAL_PREVIOUS_TICK,
        "held_renderer_result": RENDERER_RESULT_CHECKPOINT_HELD,
    }


def evaluate_capture_record(
    spec: CaptureSpec,
    record: dict[str, Any],
    *,
    symbols: dict[str, int],
) -> list[dict[str, Any]]:
    """Recompute one capture's objective successor-POST/ODD-DMA proof."""
    checks: list[dict[str, Any]] = []
    target = record.get("target", {})
    checks.append(
        _check(
            f"{spec.name}_identity",
            (
                record.get("name") == spec.name
                and target.get("attempt") == spec.attempt
                and target.get("tick") == spec.tick
                and record.get("objective") == spec.objective
            ),
            "capture name, target coordinate, and objective are exact",
        )
    )

    session = record.get("session", {})
    checks.append(
        _check(
            f"{spec.name}_fresh_owned_session",
            (
                isinstance(session.get("process_id"), int)
                and not isinstance(session.get("process_id"), bool)
                and session["process_id"] > 0
                and session.get("launch_flags") == list(b03.LAUNCH_FLAGS)
                and session.get("input_transport")
                == "targeted_PostMessageW"
                and session.get("fresh_process") is True
                and session.get("standard_three_button_pad") is True
            ),
            "fresh owned PID, canonical launch flags, and window pad input",
        )
    )

    expected_prefix = _drive_prefix(spec)
    drive = record.get("input_drive", {})
    expected_drive_sha256 = _sha256_bytes(expected_prefix)
    checks.append(
        _check(
            f"{spec.name}_imported_schedule_drive",
            (
                drive.get("source_module")
                == "tools.m5_b03_runtime_evidence"
                and drive.get("steps") == len(expected_prefix)
                and drive.get("expected_sha256") == expected_drive_sha256
                and drive.get("observed_sha256") == expected_drive_sha256
                and drive.get("exact_match") is True
            ),
            f"all {len(expected_prefix)} imported schedule steps matched",
        )
    )

    binding = record.get("frame_binding", {})
    event_order = binding.get("event_order")
    gdb = record.get("gdb", {})
    capture_audit = gdb.get("capture_breakpoint_order", {})
    checks.append(
        _check(
            f"{spec.name}_read_only_gdb",
            (
                gdb.get("guest_write_packets") == []
                and gdb.get("guest_memory_register_pc_writes") is False
                and gdb.get("all_stops_exact") is True
                and capture_audit.get("status") == "pass"
                and capture_audit.get("event_order") == event_order
                and capture_audit.get("png_consume_event_kind")
                == binding.get("png_consume_event_kind")
                and capture_audit.get("png_consume_event_index")
                == binding.get("png_consume_event_index")
                and capture_audit.get("png_consume_point")
                == binding.get("png_consume_point")
                and capture_audit.get("competing_breakpoints_armed_once")
                is True
                and capture_audit.get("persistent_dma_breakpoint") is True
            ),
            "only bounded read/control packets and one persistent DMA guard were accepted",
        )
    )

    target_state = record.get("target_post_step", {})
    prepared = record.get("target_post_renderer", {})
    published = record.get("published_post_dma", {})
    target_post_boundary = binding.get("target_post_step_boundary", {})
    checkpoint_hold = record.get("terminal_checkpoint_hold")
    service_post = record.get("terminal_service_post")
    checkpoint_hold_boundary = binding.get(
        "terminal_checkpoint_hold_boundary"
    )
    service_post_boundary = binding.get("terminal_service_post_boundary")
    terminal_service_audit = audit_terminal_presentation_service(
        spec,
        target_post_step=target_state,
        checkpoint_hold=checkpoint_hold,
        service_post=service_post,
        target_post_boundary=target_post_boundary,
        checkpoint_hold_boundary=checkpoint_hold_boundary,
        service_post_boundary=service_post_boundary,
    )
    expected_ui_edge_point = (
        "terminal_service_post_before_renderer"
        if spec.name == "terminal_complete_20"
        else "target_post_step_before_renderer"
    )
    checks.append(
        _check(
            f"{spec.name}_terminal_presentation_service",
            (
                terminal_service_audit.get("status") == "pass"
                and binding.get("terminal_presentation_service")
                == terminal_service_audit
                and binding.get("ui_edge_queued_at")
                == expected_ui_edge_point
            ),
            json.dumps(
                terminal_service_audit,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    target_states = (target_state, prepared, published)
    expected_target_engine_state = (
        b03.ENGINE_STATE_TERMINAL
        if spec.name == "terminal_complete_20"
        else b03.ENGINE_STATE_ACTIVE
    )
    target_coordinates_ok = all(
        item.get("engine", {}).get("state")
        == expected_target_engine_state
        and
        item.get("engine", {}).get("attempt") == spec.attempt
        and item.get("engine", {}).get("tick") == spec.tick
        for item in target_states
    )
    checks.append(
        _check(
            f"{spec.name}_coherent_target_coordinate",
            target_coordinates_ok,
            "post-step, post-renderer, and committed target agree",
        )
    )
    target_authoritative_sha256 = published.get(
        "authoritative_bundle_sha256", ""
    )
    target_renderer_sha256 = published.get("raw_regions", {}).get(
        "renderer_state", {}
    ).get("sha256", "")
    target_sat_sha256 = published.get("raw_regions", {}).get(
        "sat_shadow", {}
    ).get("sha256", "")
    checks.append(
        _check(
            f"{spec.name}_published_authoritative_identity",
            (
                _valid_sha256(target_authoritative_sha256)
                and target_state.get("authoritative_bundle_sha256")
                == target_authoritative_sha256
                and prepared.get("authoritative_bundle_sha256")
                == target_authoritative_sha256
                and _valid_sha256(target_renderer_sha256)
                and prepared.get("raw_regions", {})
                .get("renderer_state", {})
                .get("sha256")
                == target_renderer_sha256
                and _valid_sha256(target_sat_sha256)
                and prepared.get("raw_regions", {})
                .get("sat_shadow", {})
                .get("sha256")
                == target_sat_sha256
            ),
            "target authoritative state, HUD renderer, and SAT survived publication",
        )
    )
    semantic = (
        capture_semantic_facts(spec.name, published)
        if published
        else {"published_state_present": False}
    )
    record["objective_state_facts"] = semantic
    checks.append(
        _check(
            f"{spec.name}_objective_state_facts",
            bool(semantic) and all(semantic.values()),
            json.dumps(semantic, sort_keys=True, separators=(",", ":")),
        )
    )
    if published:
        checks.extend(
            _renderer_checks(
                f"{spec.name}_published",
                published,
                attempt=spec.attempt,
                tick=spec.tick,
            )
        )

    publication_before = binding.get("publication_before_renderer", {})
    publication_after = binding.get("publication_after_renderer", {})
    target_frame = publication_after.get("debug_frame_counter")
    target_queue = publication_after.get("dma_queue", {})
    target_commit_words = target_queue.get("commit_words")
    presentation = expected_odd_presentation(target_frame)
    expected_pre_odd = presentation.get("expected_idle_boundary_count", -1)
    odd_frame = presentation.get("expected_odd_presentation_frame")
    legal_orders = (
        [
            ["successor_post", "odd_dma_precommit"],
            ["odd_dma_precommit", "successor_post"],
        ]
        if expected_pre_odd == 0
        else [
            [
                "successor_post",
                "pre_odd_empty_dma",
                "odd_dma_precommit",
            ],
            [
                "pre_odd_empty_dma",
                "successor_post",
                "odd_dma_precommit",
            ],
            [
                "pre_odd_empty_dma",
                "odd_dma_precommit",
                "successor_post",
            ],
        ]
    )
    order_valid = (
        presentation.get("status") == "pass"
        and isinstance(event_order, list)
        and event_order in legal_orders
    )
    events = binding.get("events", [])
    events_valid = (
        order_valid
        and isinstance(events, list)
        and len(events) == len(event_order)
        and all(
            isinstance(item, dict)
            and item.get("index") == index
            and item.get("kind") == event_order[index]
            for index, item in enumerate(events)
        )
    )
    checks.append(
        _check(
            f"{spec.name}_legal_state_machine_order",
            events_valid,
            json.dumps(
                {
                    "observed": event_order,
                    "allowed": legal_orders,
                    "presentation": presentation,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )

    target_publication_valid = (
        binding.get("state_machine")
        == "successor-post-vs-odd-dma-v1"
        and presentation.get("status") == "pass"
        and (
            publication_before == service_post_boundary
            if spec.name == "terminal_complete_20"
            else publication_before == target_post_boundary
        )
        and publication_before.get("debug_frame_counter")
        == target_frame - 1
        and binding.get("target_publication_vblank_delta") == 1
        and binding.get("target_publication_frame") == target_frame
        and binding.get("target_renderer_sha256")
        == target_renderer_sha256
        and binding.get("target_renderer_exact_at_publication") is True
        and binding.get("target_sat_sha256") == target_sat_sha256
        and binding.get("target_sat_exact_at_publication") is True
        and target_queue.get("published_depth") == 0
        and target_queue.get("build_depth") == 0
        and target_queue.get("build_words") == 0
        and target_queue.get("frame_pending") == 0
        and isinstance(target_commit_words, int)
        and not isinstance(target_commit_words, bool)
        and target_commit_words == TARGET_RENDER_COMMIT_WORDS
        and binding.get("calculated_odd_presentation_frame") == odd_frame
        and binding.get("publication_after_parity")
        == presentation.get("publication_after_parity")
        and binding.get("expected_idle_preemption_boundary_count")
        == expected_pre_odd
        and binding.get("expected_odd_delta")
        == presentation.get("expected_odd_delta")
        and binding.get("expected_odd_presentation_frame") == odd_frame
        and binding.get("competing_breakpoints_armed_once") is True
    )
    checks.append(
        _check(
            f"{spec.name}_target_publication_binding",
            target_publication_valid,
            "target renderer is exact, committed, and parity-bound",
        )
    )

    post_step = record.get("successor_post_step")
    successor = record.get("ui_service_successor", {})
    post_boundary = binding.get("successor_post_step_boundary")
    engine_state, successor_attempt, successor_tick = _expected_successor(
        spec
    )
    try:
        post_index = event_order.index("successor_post")
        odd_index = event_order.index("odd_dma_precommit")
    except (AttributeError, ValueError):
        post_index = -1
        odd_index = -1
    prior_to_post = event_order[:post_index] if post_index >= 0 else []
    expected_post_frame = (
        odd_frame
        if "odd_dma_precommit" in prior_to_post
        else (
            target_frame + 1
            if "pre_odd_empty_dma" in prior_to_post
            else target_frame
        )
    )
    expected_post_commit_words = (
        0
        if any(
            kind in {"pre_odd_empty_dma", "odd_dma_precommit"}
            for kind in prior_to_post
        )
        else target_commit_words
    )
    post_queue = (
        post_boundary.get("dma_queue", {})
        if isinstance(post_boundary, dict)
        else {}
    )
    successor_coordinate_valid = (
        isinstance(post_step, dict)
        and post_step.get("engine", {}).get("state") == engine_state
        and post_step.get("engine", {}).get("attempt")
        == successor_attempt
        and post_step.get("engine", {}).get("tick") == successor_tick
    )
    post_renderer_health = (
        renderer_health(
            post_step.get("renderer", {}),
            attempt=spec.attempt,
            tick=spec.tick,
        )
        if isinstance(post_step, dict)
        else {}
    )
    post_valid = (
        successor_coordinate_valid
        and successor.get("complete_read_bundle_sha256")
        == post_step.get("complete_read_bundle_sha256")
        and successor.get("authoritative_bundle_sha256")
        == post_step.get("authoritative_bundle_sha256")
        and successor.get("raw_regions", {})
        .get("renderer_state", {})
        .get("sha256")
        == post_step.get("raw_regions", {})
        .get("renderer_state", {})
        .get("sha256")
        == target_renderer_sha256
        and post_renderer_health
        and all(post_renderer_health.values())
        and isinstance(post_boundary, dict)
        and post_boundary.get("debug_frame_counter")
        == expected_post_frame
        and post_queue.get("published_depth") == 0
        and post_queue.get("build_depth") == 0
        and post_queue.get("build_words") == 0
        and post_queue.get("commit_words") == expected_post_commit_words
        and post_queue.get("frame_pending") == 0
        and (
            spec.name != "terminal_complete_20"
            or post_step.get("authoritative_bundle_sha256")
            == target_authoritative_sha256
        )
        and binding.get("successor_post_manifest_sha256")
        == post_step.get("complete_read_bundle_sha256")
        and binding.get("successor_post_coordinate")
        == {
            "engine_state": engine_state,
            "attempt": successor_attempt,
            "tick": successor_tick,
        }
        and binding.get("successor_post_exact") is True
        and binding.get("successor_post_renderer_boundary") is None
        and record.get("successor_post_renderer") is None
    )
    if events_valid and post_index >= 0:
        post_event = events[post_index]
        post_valid = post_valid and (
            post_event.get("frame") == expected_post_frame
            and post_event.get("state_complete_read_sha256")
            == post_step.get("complete_read_bundle_sha256")
            and post_event.get("authoritative_sha256")
            == post_step.get("authoritative_bundle_sha256")
            and post_event.get("renderer_sha256")
            == target_renderer_sha256
            and post_event.get("dma_queue") == post_queue
        )
    else:
        post_valid = False
    checks.append(
        _check(
            f"{spec.name}_exact_successor_post",
            post_valid,
            (
                "exact one-step successor coordinate is proven at POST while "
                "the renderer still identifies the target"
            ),
        )
    )
    if isinstance(post_step, dict) and post_step.get("renderer"):
        checks.extend(
            _renderer_checks(
                f"{spec.name}_successor_post_target_renderer",
                post_step,
                attempt=spec.attempt,
                tick=spec.tick,
            )
        )

    idle_records = record.get("ui_service_idle_boundaries", [])
    idle_valid = (
        isinstance(idle_records, list)
        and len(idle_records) == expected_pre_odd
        and expected_pre_odd in (0, 1)
    )
    recomputed_idle: list[dict[str, Any]] = []
    expected_idle_summaries: list[dict[str, Any]] = []
    if isinstance(idle_records, list):
        for index, item in enumerate(idle_records):
            if not isinstance(item, dict):
                idle_valid = False
                continue
            idle_state = item.get("state", {})
            idle_boundary = item.get("publication_boundary", {})
            classification = classify_ui_service_boundary(
                spec,
                idle_state,
                idle_boundary,
                previous_frame=target_frame,
                target_authoritative_sha256=target_authoritative_sha256,
                target_renderer_sha256=target_renderer_sha256,
                expected_phase="idle",
                target_commit_words=target_commit_words,
            )
            recomputed_idle.append(classification)
            expected_idle_summaries.append(
                presentation_boundary_summary(
                    index=index,
                    phase="pre_odd_empty_dma",
                    state=idle_state,
                    boundary=idle_boundary,
                    classification=classification,
                )
            )
            idle_event_index = (
                event_order.index("pre_odd_empty_dma")
                if isinstance(event_order, list)
                and "pre_odd_empty_dma" in event_order
                else -1
            )
            idle_event = (
                events[idle_event_index]
                if events_valid and idle_event_index >= 0
                else {}
            )
            idle_valid = idle_valid and (
                index == 0
                and item.get("index") == index
                and item.get("diagnostic_torn_snapshot") is True
                and idle_boundary.get("debug_frame_counter")
                == target_frame + 1
                and classification.get("status") == "pass"
                and classification.get("mode") == "pre_odd_empty_dma"
                and classification.get("continue_required") is True
                and item.get("classification") == classification
                and idle_event.get("frame") == target_frame + 1
                and idle_event.get("diagnostic_torn_snapshot") is True
                and idle_event.get("authoritative_sha256")
                == idle_state.get("authoritative_bundle_sha256")
                and idle_event.get("renderer_sha256")
                == target_renderer_sha256
                and idle_event.get("dma_queue")
                == idle_boundary.get("dma_queue")
            )
    checks.append(
        _check(
            f"{spec.name}_diagnostic_pre_odd_boundaries",
            idle_valid,
            json.dumps(
                {
                    "expected_count": expected_pre_odd,
                    "classifications": recomputed_idle,
                    "authoritative_bytes_are_diagnostic_only": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )

    png_state = record.get("png_precommit_state", {})
    png_boundary = binding.get("successor_boundary", {})
    prior_to_odd = event_order[:odd_index] if odd_index >= 0 else []
    expected_odd_commit_words = (
        0
        if "pre_odd_empty_dma" in prior_to_odd
        else target_commit_words
    )
    odd_classification = classify_odd_dma_boundary(
        spec,
        png_state,
        png_boundary,
        expected_frame=odd_frame,
        successor_post=(post_step if post_index < odd_index else None),
        target_authoritative_sha256=target_authoritative_sha256,
        target_renderer_sha256=target_renderer_sha256,
        expected_commit_words=expected_odd_commit_words,
    )
    odd_before_post = 0 <= odd_index < post_index
    expected_consume_event_kind = (
        "successor_post" if odd_before_post else "odd_dma_precommit"
    )
    expected_consume_event_index = (
        post_index if odd_before_post else odd_index
    )
    expected_consume_point = (
        "successor_post_after_verified_empty_odd_commit"
        if odd_before_post
        else "odd_dma_precommit"
    )
    odd_valid = (
        odd_index >= 0
        and odd_classification.get("status") == "pass"
        and binding.get("successor_resolution") == odd_classification
        and binding.get("odd_dma_frame_exact") is True
        and binding.get("odd_dma_queue_mode")
        == odd_classification.get("queue_mode")
    )
    if events_valid and odd_index >= 0:
        odd_event = events[odd_index]
        odd_valid = odd_valid and (
            odd_event.get("frame") == odd_frame
            and odd_event.get("queue_mode")
            == odd_classification.get("queue_mode")
            and odd_event.get("authoritative_sha256")
            == png_state.get("authoritative_bundle_sha256")
            and odd_event.get("renderer_sha256")
            == png_state.get("raw_regions", {})
            .get("renderer_state", {})
            .get("sha256")
            and odd_event.get("dma_queue")
            == png_boundary.get("dma_queue")
            and odd_event.get("png_consumed_while_stopped")
            is (not odd_before_post)
            and odd_event.get("verified_empty_commit_executed_before_png")
            is odd_before_post
        )
    else:
        odd_valid = False
    checks.append(
        _check(
            f"{spec.name}_calculated_odd_dma_boundary",
            odd_valid,
            json.dumps(
                odd_classification,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )

    expected_presentation_boundaries = [
        *expected_idle_summaries,
        presentation_boundary_summary(
            index=expected_pre_odd,
            phase="odd_dma_precommit",
            state=png_state,
            boundary=png_boundary,
            classification=odd_classification,
        ),
    ]
    race_order = (
        "post_before_odd"
        if 0 <= post_index < odd_index
        else "odd_before_post"
    )
    expected_service_steps = (
        0 if spec.name == "terminal_complete_20" else 1
    )
    consume_event_valid = (
        events_valid
        and expected_consume_event_index >= 0
        and events[expected_consume_event_index].get(
            "png_consumed_while_stopped"
        )
        is True
        and events[expected_consume_event_index].get("png_consume_point")
        == expected_consume_point
        and sum(
            item.get("png_consumed_while_stopped") is True
            for item in events
        )
        == 1
    )
    machine_binding_valid = (
        binding.get("allowed_event_order") is True
        and binding.get("pre_odd_empty_dma_count") == expected_pre_odd
        and binding.get("pre_odd_nonempty_dma_count") == 0
        and binding.get("nonempty_dma_commits_between_target_and_png")
        == 0
        and binding.get("idle_preemption_boundary_count")
        == expected_pre_odd
        and binding.get("idle_preemption_boundaries")
        == expected_idle_summaries
        and binding.get("presentation_boundaries")
        == expected_presentation_boundaries
        and binding.get("png_bound_precommit_frame") == odd_frame
        and binding.get("png_consumed_frame") == odd_frame
        and binding.get("png_consume_event_kind")
        == expected_consume_event_kind
        and binding.get("png_consume_event_index")
        == expected_consume_event_index
        and binding.get("png_consume_point") == expected_consume_point
        and binding.get("png_consumed_while_stopped_at_odd_dma_entry")
        is (not odd_before_post)
        and binding.get("png_consumed_while_stopped_at_successor_post")
        is odd_before_post
        and binding.get("odd_dma_commit_executed_before_png")
        is odd_before_post
        and binding.get("odd_dma_commit_before_png_verified_empty")
        is odd_before_post
        and binding.get("post_after_odd_guarded_from_next_dma")
        is odd_before_post
        and binding.get("png_consume_guarded_from_next_dma") is True
        and binding.get("race_order") == race_order
        and binding.get("race_handled") is True
        and binding.get("no_post_target_dma_transfer_before_png") is True
        and binding.get("exact_successor_phase") is True
        and binding.get("authoritative_service_steps")
        == expected_service_steps
        and binding.get("successor_dma_frame_pending_before_commit")
        == png_boundary.get("dma_queue", {}).get("frame_pending")
        and binding.get("ui_screenshot_key") == SCREENSHOT_KEY[0]
        and binding.get("ui_event_indices", {}).get("up")
        == binding.get("ui_event_indices", {}).get("down", -2) + 1
        and binding.get("ui_screenshot_edge_count") == 1
        and binding.get("ui_screenshot_rearm_count") == 0
        and binding.get("ui_screenshot_edge_reused_after_noop_commit")
        is odd_before_post
        and binding.get("screenshot_count") == 1
        and binding.get("missing_or_extra_screenshots") is False
        and binding.get("breakpoint_race_detected") is False
        and binding.get("native_png_presented_before_successor_dma_commit")
        is True
        and binding.get("stopped_at_successor_dma_commit_entry") is True
    )
    checks.append(
        _check(
            f"{spec.name}_native_frame_binding",
            (
                target_publication_valid
                and events_valid
                and post_valid
                and idle_valid
                and odd_valid
                and consume_event_valid
                and machine_binding_valid
            ),
            (
                "one native PNG is consumed at ODD or at the guarded POST "
                "after a verified empty ODD commit under the exact P/I/O "
                "contract"
            ),
        )
    )

    raw_manifest_items = [
        ("target_post_step", target_state),
        ("terminal_checkpoint_hold", checkpoint_hold),
        ("terminal_service_post", service_post),
        ("target_post_renderer", prepared),
        ("published_post_dma", published),
        ("successor_post_step", post_step),
        (
            "successor_post_renderer",
            record.get("successor_post_renderer", {}),
        ),
        ("png_precommit_state", png_state),
        ("ui_service_successor", record.get("ui_service_successor", {})),
        *[
            (f"pre_odd_{index}", item.get("state", {}))
            for index, item in enumerate(idle_records)
            if isinstance(item, dict)
        ],
    ]
    raw_manifest_audits = {
        name: audit_state_manifest_reconstruction(
            manifest, symbols=symbols
        )
        for name, manifest in raw_manifest_items
        if isinstance(manifest, dict) and manifest
    }
    expected_raw_manifest_names = {
        "target_post_step",
        "target_post_renderer",
        "published_post_dma",
        "successor_post_step",
        "png_precommit_state",
        "ui_service_successor",
        *{
            f"pre_odd_{index}" for index in range(len(idle_records))
        },
    }
    if spec.name == "terminal_complete_20":
        expected_raw_manifest_names.update(
            {"terminal_checkpoint_hold", "terminal_service_post"}
        )
    checks.append(
        _check(
            f"{spec.name}_raw_manifest_reconstruction",
            set(raw_manifest_audits) == expected_raw_manifest_names
            and all(
                item.get("status") == "pass"
                for item in raw_manifest_audits.values()
            ),
            json.dumps(
                raw_manifest_audits,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )

    screenshot = record.get("screenshot", {})
    viewport = screenshot.get("viewport", {})
    checks.append(
        _check(
            f"{spec.name}_decoded_png_integrity",
            (
                screenshot.get("path") == spec.output
                and _valid_sha256(screenshot.get("sha256"))
                and _valid_sha256(screenshot.get("pixel_sha256"))
                and isinstance(screenshot.get("bytes"), int)
                and not isinstance(screenshot.get("bytes"), bool)
                and screenshot["bytes"] > 100
                and isinstance(screenshot.get("width"), int)
                and screenshot["width"] >= 320
                and isinstance(screenshot.get("height"), int)
                and screenshot["height"] >= 224
                and viewport.get("pixels", 0) >= 320 * 224
                and screenshot.get("opaque_ratio", 0) > 0.95
                and screenshot.get("quantized_color_count", 0) >= 8
                and screenshot.get("luminance_entropy_bits", 0) > 1.0
                and screenshot.get("content_bounds") is not None
                and screenshot.get("publication_verified") is True
            ),
            "strictly decoded nonblank native framebuffer and final hash",
        )
    )
    ownership = screenshot.get("window_ownership", {})
    checks.append(
        _check(
            f"{spec.name}_owned_window_and_isolated_png",
            (
                ownership.get("process_id") == session.get("process_id")
                and ownership.get("resolved_owner_process_id")
                == session.get("process_id")
                and ownership.get("ui_screenshot_key")
                == SCREENSHOT_KEY[0]
                and ownership.get(
                    "isolated_source_removed_after_verified_copy"
                )
                is True
            ),
            "native source came from and was consumed inside the owned session",
        )
    )
    return checks

def build_visual_report(
    *,
    rom_sha256: str,
    symbol_sha256: str,
    symbols: dict[str, int],
    captures: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    names = [record.get("name") for record in captures]
    expected_names = [spec.name for spec in CAPTURE_PLAN]
    checks.append(
        _check(
            "exact_capture_set",
            sorted(names) == sorted(expected_names)
            and len(names) == len(set(names)) == len(expected_names),
            f"required={expected_names}; observed={names}",
        )
    )
    by_name = {
        record["name"]: record
        for record in captures
        if record.get("name") in expected_names
    }
    for spec in CAPTURE_PLAN:
        if spec.name not in by_name:
            checks.append(
                _check(
                    f"{spec.name}_present",
                    False,
                    "required capture is missing",
                )
            )
            continue
        try:
            checks.extend(
                evaluate_capture_record(
                    spec, by_name[spec.name], symbols=symbols
                )
            )
        except Exception as error:
            checks.append(
                _check(
                    f"{spec.name}_record_parse",
                    False,
                    f"{type(error).__name__}: {error}",
                )
            )

    trace_audit = provenance.get("capture_protocol_audit", {})
    trace_capture_items = trace_audit.get("captures", [])
    trace_by_name = {
        item.get("name"): item
        for item in trace_capture_items
        if isinstance(item, dict)
    } if isinstance(trace_capture_items, list) else {}
    trace_audit_valid = (
        trace_audit.get("schema")
        == "r94-m5-b03-visual-trace-audit-v1"
        and trace_audit.get("status") == "pass"
        and trace_audit.get("expected_session_order") == expected_names
        and trace_audit.get("observed_session_order") == expected_names
        and trace_audit.get("capture_count") == len(CAPTURE_PLAN)
        and trace_audit.get("session_ranges_nonoverlapping") is True
        and trace_audit.get("exact_session_tiling") is True
        and trace_audit.get("trace_line_count")
        == provenance.get("transcript", {}).get("lines")
        and trace_audit.get("trace_sha256")
        == provenance.get("transcript", {}).get("sha256")
        and trace_audit.get("guest_write_packets") == []
        and trace_audit.get("unexpected_control_packets") == []
        and set(trace_by_name) == set(expected_names)
        and all(
            trace_by_name[name].get("status") == "pass"
            and trace_by_name[name].get("prefix_audit", {}).get("status")
            == "pass"
            and trace_by_name[name].get("suffix_audit", {}).get("status")
            == "pass"
            and trace_by_name[name].get("event_order")
            == by_name.get(name, {}).get("frame_binding", {}).get(
                "event_order"
            )
            and trace_by_name[name].get("audit")
            == by_name.get(name, {}).get("gdb", {}).get(
                "capture_breakpoint_order"
            )
            and trace_by_name[name].get("transcript_sha256")
            == by_name.get(name, {}).get("gdb", {}).get(
                "transcript_sha256"
            )
            and trace_by_name[name].get("report_audit_matches") is True
            and trace_by_name[name].get("transcript_hash_matches") is True
            and trace_by_name[name].get("state_trace_matches") is True
            and trace_by_name[name].get("png_consume_matches") is True
            and trace_by_name[name].get("unexpected_control_packets") == []
            for name in expected_names
        )
    )
    checks.append(
        _check(
            "live_trace_protocol_reparse",
            trace_audit_valid,
            (
                "five exact session slices independently reparse to their "
                "bound successor-POST/ODD-DMA audits"
            ),
        )
    )

    process_ids = [
        record.get("session", {}).get("process_id") for record in captures
    ]
    png_hashes = [
        record.get("screenshot", {}).get("sha256") for record in captures
    ]
    pixel_hashes = [
        record.get("screenshot", {}).get("pixel_sha256")
        for record in captures
    ]
    dimensions = [
        (
            record.get("screenshot", {}).get("width"),
            record.get("screenshot", {}).get("height"),
        )
        for record in captures
    ]
    checks.extend(
        (
            _check(
                "fresh_process_per_capture",
                (
                    len(process_ids) == len(CAPTURE_PLAN)
                    and all(
                        isinstance(process_id, int) and process_id > 0
                        for process_id in process_ids
                    )
                    and len(set(process_ids)) == len(process_ids)
                ),
                f"owned PIDs={process_ids}",
            ),
            _check(
                "unique_native_pngs",
                (
                    len(png_hashes) == len(CAPTURE_PLAN)
                    and all(_valid_sha256(value) for value in png_hashes)
                    and len(set(png_hashes)) == len(png_hashes)
                    and len(set(pixel_hashes)) == len(pixel_hashes)
                ),
                "every semantic state produced a distinct native framebuffer",
            ),
            _check(
                "consistent_frame_dimensions",
                (
                    len(dimensions) == len(CAPTURE_PLAN)
                    and len(set(dimensions)) == 1
                ),
                f"dimensions={dimensions}",
            ),
            _check(
                "hash_binding_present",
                _valid_sha256(rom_sha256)
                and _valid_sha256(symbol_sha256),
                f"ROM={rom_sha256}; symbols={symbol_sha256}",
            ),
            _check(
                "objective_only_no_self_scoring",
                True,
                (
                    "collector records integrity/state/pixels only; an "
                    "independent critic must assess visual quality"
                ),
            ),
        )
    )
    passed = all(check["status"] == "pass" for check in checks)
    critic_manifest = [
        {
            "label": record.get("name"),
            "path": record.get("screenshot", {}).get("path"),
            "png_sha256": record.get("screenshot", {}).get("sha256"),
            "pixel_sha256": record.get("screenshot", {}).get(
                "pixel_sha256"
            ),
            "published_state_sha256": record.get(
                "published_post_dma", {}
            ).get("complete_read_bundle_sha256"),
            "published_authoritative_state_sha256": record.get(
                "published_post_dma", {}
            ).get("authoritative_bundle_sha256"),
            "objective_state_facts": record.get(
                "objective_state_facts", {}
            ),
            "renderer_health": (
                renderer_health(
                    record["published_post_dma"]["renderer"],
                    attempt=record["target"]["attempt"],
                    tick=record["target"]["tick"],
                )
                if (
                    isinstance(record.get("published_post_dma"), dict)
                    and "renderer"
                    in record.get("published_post_dma", {})
                    and isinstance(record.get("target"), dict)
                    and "attempt" in record.get("target", {})
                    and "tick" in record.get("target", {})
                )
                else {}
            ),
        }
        for record in captures
    ]
    critic_manifest_payload = json.dumps(
        critic_manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5/B03/native-visual-capture",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "acceptance_status": (
            ACCEPTANCE_STATUS if passed else "incomplete"
        ),
        "rom_sha256": rom_sha256,
        "symbol_map_sha256": symbol_sha256,
        "schedule": schedule_provenance(),
        "capture_count": len(captures),
        "captures": captures,
        "checks": checks,
        "objective_only": {
            "subjective_scores_emitted": False,
            "rubric_review_performed": False,
            "critic_handoff_required": True,
            "statement": (
                "PASS means capture integrity and objective state binding "
                "only; it is not a visual-quality judgment."
            ),
        },
        "critic_handoff": {
            "reserved_report_path": (
                "artifacts/reports/m5-b03-visual-critic.json"
            ),
            "reserved_schema": (
                "r94-m5-b03-independent-visual-review-v1"
            ),
            "capture_report_path": (
                "artifacts/reports/m5-b03-visual.json"
            ),
            "capture_report_schema": REPORT_SCHEMA,
            "critic_must_hash_capture_report_bytes": True,
            "capture_manifest_canonical_form": (
                "compact sorted-key ASCII JSON array in capture order"
            ),
            "capture_manifest_sha256": _sha256_bytes(
                critic_manifest_payload
            ),
            "captures": critic_manifest,
        },
        "provenance": provenance,
        "limitations": [
            "No subjective composition, readability, polish, or art score is made.",
            "No audio, performance, PAL, save, Release-ROM, or full-campaign claim is made.",
            "Each PNG is one NTSC B03 Debug-fixture state from a fresh process.",
        ],
    }


def report_exit_code(report: dict[str, Any]) -> int:
    return (
        0
        if (
            report.get("status") == "pass"
            and report.get("acceptance_status") == ACCEPTANCE_STATUS
            and all(
                item.get("status") == "pass"
                for item in report.get("checks", [])
            )
        )
        else 1
    )


def _source_bindings(root: Path) -> dict[str, Any]:
    sources: dict[str, str] = {}
    for relative in SOURCE_BINDING_PATHS:
        path = root / relative
        if not path.is_file():
            raise EvidenceFailure(
                f"M5 visual source binding is missing {relative}"
            )
        sources[relative] = sha256_file(path)
    source_pin_mismatches = {
        relative: {
            "expected_sha256": expected,
            "observed_sha256": sources.get(relative),
        }
        for relative, expected in PINNED_RENDERER_SOURCE_SHA256.items()
        if sources.get(relative) != expected
    }
    if source_pin_mismatches:
        raise EvidenceFailure(
            "M5 reviewed renderer source pins changed: "
            f"{source_pin_mismatches}"
        )
    canonical = json.dumps(
        sources, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "algorithm": "SHA-256",
        "canonical_form": "sorted compact path-to-sha256 JSON",
        "bundle_sha256": _sha256_bytes(canonical),
        "sources": sources,
        "reviewed_renderer_sources": {
            relative: {
                "expected_sha256": expected,
                "observed_sha256": sources[relative],
                "exact_match": True,
            }
            for relative, expected in PINNED_RENDERER_SOURCE_SHA256.items()
        },
    }


def _continue_and_read_pc(
    remote: GdbRemote, *, label: str
) -> int:
    require_stop(remote.request("c"))
    response = remote.request("p11")
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", response):
        raise EvidenceFailure(
            f"{label} PC read returned malformed {response!r}"
        )
    return int(response, 16)


def _require_stop_at(
    remote: GdbRemote, address: int, *, label: str
) -> None:
    pc = _continue_and_read_pc(remote, label=label)
    if pc != address:
        raise EvidenceFailure(
            f"{label} stopped at ${pc:08X}; ${address:08X} required"
        )


def audit_capture_breakpoint_order(
    lines: list[str],
    *,
    pre_step: int,
    post_step: int,
    renderer_return: int,
    dma_commit_entry: int,
    event_order: list[str],
) -> dict[str, Any]:
    """Parse the uniquely marked capture interval as an exact grammar."""
    legal_orders = [
        ["successor_post", "odd_dma_precommit"],
        ["odd_dma_precommit", "successor_post"],
        [
            "successor_post",
            "pre_odd_empty_dma",
            "odd_dma_precommit",
        ],
        [
            "pre_odd_empty_dma",
            "successor_post",
            "odd_dma_precommit",
        ],
        [
            "pre_odd_empty_dma",
            "odd_dma_precommit",
            "successor_post",
        ],
    ]
    result: dict[str, Any] = {
        "status": "fail",
        "event_order": event_order,
        "grammar": "exact-successor-post-vs-odd-dma-v1",
    }
    if event_order not in legal_orders:
        result["detail"] = "event order is not one of five legal orders"
        return result

    marker_pattern = re.compile(
        r"# (visual_[a-z0-9_]+) capture_protocol (begin|end)"
    )
    markers = [
        (index, match.group(1), match.group(2))
        for index, line in enumerate(lines)
        if (match := marker_pattern.fullmatch(line))
    ]
    begin_markers = [item for item in markers if item[2] == "begin"]
    end_markers = [item for item in markers if item[2] == "end"]
    if (
        len(begin_markers) != 1
        or len(end_markers) != 1
        or begin_markers[0][1] != end_markers[0][1]
        or begin_markers[0][0] >= end_markers[0][0]
    ):
        result["detail"] = (
            "capture protocol requires one matching begin/end marker pair"
        )
        result["protocol_markers"] = markers
        return result
    marker_start, marker_session_id, _ = begin_markers[0]
    marker_end = end_markers[0][0]
    terminal_service_required = (
        marker_session_id == "visual_terminal_complete_20"
    )
    suffix = lines[marker_end + 1 :]
    suffix_controls = [
        line
        for line in suffix
        if line.startswith("> ") or line.startswith("< ")
    ]
    if (
        any(line != "> k" for line in suffix_controls)
        or suffix_controls.count("> k") > 1
    ):
        result["detail"] = (
            "capture protocol suffix contains unexpected control traffic"
        )
        result["protocol_suffix_controls"] = suffix_controls
        return result

    target_post_removal = f"> z0,{post_step:x},2"
    post_removal_indices = [
        index
        for index, line in enumerate(lines[marker_start + 1 : marker_end])
        if line == target_post_removal
    ]
    post_removal_indices = [
        marker_start + 1 + index for index in post_removal_indices
    ]
    expected_post_removal_count = 3 if terminal_service_required else 2
    if len(post_removal_indices) != expected_post_removal_count:
        result["detail"] = (
            "target/service/successor POST removal cardinality drifted"
        )
        result["post_removal_indices"] = post_removal_indices
        return result
    start = marker_start + 1

    cursor = start
    structural_indices: list[int] = []
    memory_read_indices: list[int] = []

    def consume_pair(command: str, response: str) -> bool:
        nonlocal cursor
        if (
            cursor + 1 >= len(lines)
            or lines[cursor] != command
            or lines[cursor + 1] != response
        ):
            result["detail"] = (
                f"grammar mismatch at line {cursor}: expected "
                f"{command!r}, {response!r}; observed "
                f"{lines[cursor:cursor + 2]!r}"
            )
            return False
        structural_indices.extend((cursor, cursor + 1))
        cursor += 2
        return True

    def consume_exact_region(
        label: str, name: str, address: int, size: int
    ) -> bytes | None:
        nonlocal cursor
        output = bytearray()
        while len(output) < size:
            offset = len(output)
            amount = min(0xFF, size - offset)
            command = f"> m{address + offset:x},{amount:x}"
            if cursor + 1 >= len(lines):
                result["detail"] = f"{label}/{name} memory read lacks response"
                return None
            response = lines[cursor + 1]
            if (
                lines[cursor] != command
                or not re.fullmatch(rf"< [0-9A-F]{{{amount * 2}}}", response)
            ):
                result["detail"] = (
                    f"{label}/{name} exact read mismatch at line {cursor}: "
                    f"expected {command!r} plus {amount} uppercase bytes; "
                    f"observed {lines[cursor:cursor + 2]!r}"
                )
                return None
            output.extend(bytes.fromhex(response[2:]))
            memory_read_indices.extend((cursor, cursor + 1))
            cursor += 2
        return bytes(output)

    def consume_exact_observation(label: str) -> dict[str, Any] | None:
        raw_regions: dict[str, dict[str, Any]] = {}
        raw_by_name: dict[str, bytes] = {}
        read_start = cursor
        for name, address, size in STATE_READ_REGION_SPECS:
            raw = consume_exact_region(label, name, address, size)
            if raw is None:
                return None
            raw_by_name[name] = raw
            raw_regions[name] = {
                "address": f"{address:08X}",
                "bytes": size,
                "sha256": _sha256_bytes(raw),
                "raw_hex": raw.hex().upper(),
            }
        boundary_raw: dict[str, bytes] = {}
        for name, address, size in BOUNDARY_READ_REGION_SPECS:
            raw = consume_exact_region(label, name, address, size)
            if raw is None:
                return None
            boundary_raw[name] = raw
        authoritative = b"".join(
            raw_by_name[name] for name in AUTHORITATIVE_REGION_NAMES
        )
        complete = b"".join(
            raw_by_name[name]
            for name, _address, _size in STATE_READ_REGION_SPECS
        )
        dma_raw = boundary_raw["dma_queue_control"]
        return {
            "label": label,
            "memory_pair_count": (cursor - read_start) // 2,
            "raw_regions": raw_regions,
            "authoritative_bundle_sha256": _sha256_bytes(authoritative),
            "complete_read_bundle_sha256": _sha256_bytes(complete),
            "complete_read_bundle_bytes": len(complete),
            "publication_boundary": {
                "debug_frame_counter": int.from_bytes(
                    boundary_raw["debug_frame_counter"], "big"
                ),
                "dma_queue": {
                    "published_depth": int.from_bytes(dma_raw[0:2], "big"),
                    "build_depth": int.from_bytes(dma_raw[2:4], "big"),
                    "build_words": int.from_bytes(dma_raw[4:8], "big"),
                    "commit_words": int.from_bytes(dma_raw[8:10], "big"),
                    "frame_pending": int.from_bytes(
                        dma_raw[
                            DMA_QUEUE_FRAME_PENDING_OFFSET:
                            DMA_QUEUE_FRAME_PENDING_OFFSET + 2
                        ],
                        "big",
                    ),
                    "reserved_0": int.from_bytes(dma_raw[12:14], "big"),
                    "reserved_1": int.from_bytes(dma_raw[14:16], "big"),
                    "raw_hex": dma_raw.hex().upper(),
                    "raw_sha256": _sha256_bytes(dma_raw),
                },
            },
        }

    target_post_observation = consume_exact_observation("target_post_step")
    if target_post_observation is None:
        return result

    fixed_prefix = (
        (f"> z0,{pre_step:x},2", "< OK"),
        (target_post_removal, "< OK"),
        (f"> Z0,{renderer_return:x},2", "< OK"),
        ("> c", "< S05"),
        ("> p11", f"< {renderer_return:08X}"),
    )
    for command, response in fixed_prefix:
        if not consume_pair(command, response):
            return result
    first_renderer_observation = consume_exact_observation(
        (
            "terminal_checkpoint_hold"
            if terminal_service_required
            else "target_renderer_stop"
        )
    )
    if first_renderer_observation is None:
        return result
    terminal_checkpoint_hold_observation: dict[str, Any] | None = None
    terminal_service_post_observation: dict[str, Any] | None = None
    if terminal_service_required:
        terminal_checkpoint_hold_observation = first_renderer_observation
        for command, response in (
            (f"> z0,{renderer_return:x},2", "< OK"),
            (f"> Z0,{post_step:x},2", "< OK"),
            ("> c", "< S05"),
            ("> p11", f"< {post_step:08X}"),
        ):
            if not consume_pair(command, response):
                return result
        terminal_service_post_observation = consume_exact_observation(
            "terminal_service_post"
        )
        if terminal_service_post_observation is None:
            return result
        for command, response in (
            (target_post_removal, "< OK"),
            (f"> Z0,{renderer_return:x},2", "< OK"),
            ("> c", "< S05"),
            ("> p11", f"< {renderer_return:08X}"),
        ):
            if not consume_pair(command, response):
                return result
        target_renderer_observation = consume_exact_observation(
            "target_renderer_stop"
        )
        if target_renderer_observation is None:
            return result
    else:
        target_renderer_observation = first_renderer_observation
    for command, response in (
        (f"> z0,{renderer_return:x},2", "< OK"),
        (f"> Z0,{post_step:x},2", "< OK"),
        (f"> Z0,{dma_commit_entry:x},2", "< OK"),
    ):
        if not consume_pair(command, response):
            return result

    observed_stops: list[dict[str, Any]] = []
    target_frame_from_trace = target_renderer_observation[
        "publication_boundary"
    ]["debug_frame_counter"]
    presentation_from_trace = expected_odd_presentation(
        target_frame_from_trace
    )
    if presentation_from_trace.get("status") != "pass":
        result["detail"] = "target trace frame cannot derive ODD presentation"
        return result
    odd_frame_from_trace = presentation_from_trace[
        "expected_odd_presentation_frame"
    ]
    post_event_index = event_order.index("successor_post")
    odd_event_index = event_order.index("odd_dma_precommit")
    odd_before_post = odd_event_index < post_event_index
    png_consume_event_kind = (
        "successor_post" if odd_before_post else "odd_dma_precommit"
    )
    png_consume_event_index = (
        post_event_index if odd_before_post else odd_event_index
    )
    png_consume_point = (
        "successor_post_after_verified_empty_odd_commit"
        if odd_before_post
        else "odd_dma_precommit"
    )
    for event_index, kind in enumerate(event_order):
        address = (
            post_step
            if kind == "successor_post"
            else dma_commit_entry
        )
        for command, response in (
            ("> c", "< S05"),
            ("> p11", f"< {address:08X}"),
        ):
            if not consume_pair(command, response):
                return result
        stop_start = cursor
        observation = consume_exact_observation(
            f"event_{event_index}_{kind}"
        )
        if observation is None:
            return result
        observed_frame = observation["publication_boundary"][
            "debug_frame_counter"
        ]
        derived_kind = (
            "successor_post"
            if address == post_step
            else (
                "pre_odd_empty_dma"
                if observed_frame < odd_frame_from_trace
                else (
                    "odd_dma_precommit"
                    if observed_frame == odd_frame_from_trace
                    else "invalid_dma_frame"
                )
            )
        )
        if derived_kind != kind:
            result["detail"] = (
                f"event {event_index} report kind {kind} != trace-derived "
                f"{derived_kind} at frame {observed_frame}"
            )
            return result
        observed_stops.append(
            {
                "index": event_index,
                "kind": kind,
                "trace_derived_kind": derived_kind,
                "address": f"{address:08X}",
                "memory_pair_count": (cursor - stop_start) // 2,
                "state_read_binding": observation,
            }
        )
        if kind == "successor_post" and not consume_pair(
            f"> z0,{post_step:x},2", "< OK"
        ):
            return result
        if kind == png_consume_event_kind:
            png_begin = f"# {marker_session_id} png_consume begin"
            if cursor >= len(lines) or lines[cursor] != png_begin:
                result["detail"] = (
                    f"{kind} lacks in-stop PNG consume begin at line {cursor}"
                )
                return result
            structural_indices.append(cursor)
            cursor += 1
            if cursor >= len(lines):
                result["detail"] = "PNG consume begin lacks end/hash marker"
                return result
            png_match = re.fullmatch(
                rf"# {re.escape(marker_session_id)} png_consume end "
                r"sha256=([0-9a-f]{64})",
                lines[cursor],
            )
            if png_match is None:
                result["detail"] = (
                    f"{kind} has malformed PNG end marker at line {cursor}"
                )
                return result
            structural_indices.append(cursor)
            observed_stops[-1]["png_consume"] = {
                "while_stopped_before_next_control": True,
                "sha256": png_match.group(1),
                "begin_index": cursor - 1,
                "end_index": cursor,
                "event_kind": kind,
                "event_index": event_index,
                "consume_point": png_consume_point,
            }
            cursor += 1

    if not consume_pair(f"> z0,{dma_commit_entry:x},2", "< OK"):
        return result
    protocol_end = cursor - 1
    tail = lines[cursor:marker_end]
    if tail:
        result["detail"] = (
            "unexpected packet/response after capture protocol: "
            f"{tail!r}"
        )
        return result

    capture_lines = lines[start : protocol_end + 1]
    protocol_payload = ("\n".join(capture_lines) + "\n").encode("utf-8")
    packets = {
        "renderer_return_insertions": capture_lines.count(
            f"> Z0,{renderer_return:x},2"
        ),
        "renderer_return_removals": capture_lines.count(
            f"> z0,{renderer_return:x},2"
        ),
        "successor_post_insertions": capture_lines.count(
            f"> Z0,{post_step:x},2"
        ),
        "successor_post_removals": capture_lines.count(
            f"> z0,{post_step:x},2"
        ) - 1,
        "dma_commit_entry_insertions": capture_lines.count(
            f"> Z0,{dma_commit_entry:x},2"
        ),
        "dma_commit_entry_removals": capture_lines.count(
            f"> z0,{dma_commit_entry:x},2"
        ),
    }
    expected_rearm_count = 2 if terminal_service_required else 1
    exact_packets = packets == {
        "renderer_return_insertions": expected_rearm_count,
        "renderer_return_removals": expected_rearm_count,
        "successor_post_insertions": expected_rearm_count,
        "successor_post_removals": expected_rearm_count,
        "dma_commit_entry_insertions": 1,
        "dma_commit_entry_removals": 1,
    }
    result.update(
        {
            "status": "pass" if exact_packets else "fail",
            "detail": (
                "exact capture grammar accepted"
                if exact_packets
                else "breakpoint packet cardinality drifted"
            ),
            "protocol_start_index": start,
            "protocol_end_index": protocol_end,
            "protocol_marker_start_index": marker_start,
            "protocol_marker_end_index": marker_end,
            "protocol_session_id": marker_session_id,
            "protocol_suffix_controls": suffix_controls,
            "protocol_line_count": protocol_end - start + 1,
            "protocol_command_count": sum(
                line.startswith("> ") for line in capture_lines
            ),
            "protocol_sha256": _sha256_bytes(protocol_payload),
            "protocol_coverage": "exact-marker-bounded-all-lines-consumed",
            "structural_indices": structural_indices,
            "memory_read_pair_count": len(memory_read_indices) // 2,
            "target_post_read_binding": target_post_observation,
            "terminal_checkpoint_hold_read_binding": (
                terminal_checkpoint_hold_observation
            ),
            "terminal_service_post_read_binding": (
                terminal_service_post_observation
            ),
            "target_renderer_read_binding": target_renderer_observation,
            "unique_capture_breakpoint_packets": packets,
            "competing_breakpoints_armed_once": exact_packets,
            "persistent_dma_breakpoint": (
                packets["dma_commit_entry_insertions"] == 1
                and packets["dma_commit_entry_removals"] == 1
            ),
            "successor_post_stop_count": 1,
            "terminal_service_required": terminal_service_required,
            "png_consume_event_kind": png_consume_event_kind,
            "png_consume_event_index": png_consume_event_index,
            "png_consume_point": png_consume_point,
            "expected_dma_stop_count": sum(
                kind != "successor_post" for kind in event_order
            ),
            "observed_stops": observed_stops,
            "tail_inside_protocol": tail,
        }
    )
    return result


def _trace_observation_matches_report(
    observation: dict[str, Any],
    manifest: dict[str, Any],
    boundary: dict[str, Any],
    *,
    symbols: dict[str, int],
) -> bool:
    """Bind one exact trace read sequence to one raw report manifest."""
    if (
        audit_state_manifest_reconstruction(
            manifest, symbols=symbols
        ).get("status")
        != "pass"
    ):
        return False
    observed_regions = observation.get("raw_regions", {})
    report_regions = manifest.get("raw_regions", {})
    if set(observed_regions) != set(report_regions):
        return False
    for name, address, size in STATE_READ_REGION_SPECS:
        observed = observed_regions.get(name, {})
        reported = report_regions.get(name, {})
        if observed != {
            "address": f"{address:08X}",
            "bytes": size,
            "sha256": reported.get("sha256"),
            "raw_hex": reported.get("raw_hex"),
        }:
            return False
    return (
        observation.get("authoritative_bundle_sha256")
        == manifest.get("authoritative_bundle_sha256")
        and observation.get("complete_read_bundle_sha256")
        == manifest.get("complete_read_bundle_sha256")
        and observation.get("complete_read_bundle_bytes")
        == manifest.get("complete_read_bundle_bytes")
        and observation.get("publication_boundary") == boundary
    )


def audit_visual_session_prefix(
    lines: list[str],
    *,
    protocol_marker_index: int,
    spec: CaptureSpec,
    vblank: int,
    pre_step: int,
    post_step: int,
    symbols: dict[str, int],
) -> dict[str, Any]:
    """Consume the entire successful startup/input-drive trace prefix."""
    result: dict[str, Any] = {
        "schema": "r94-m5-b03-visual-session-prefix-audit-v1",
        "status": "fail",
    }
    session_id = f"visual_{spec.name}"
    if not lines or lines[0] != f"# {session_id} begin":
        result["detail"] = "session does not begin with its exact marker"
        return result
    cursor = 1
    memory_pair_count = 0

    def consume_pair(command: str, response: str) -> bool:
        nonlocal cursor
        if (
            cursor + 1 >= protocol_marker_index
            or lines[cursor] != command
            or lines[cursor + 1] != response
        ):
            result["detail"] = (
                f"prefix mismatch at line {cursor}: expected {command!r}, "
                f"{response!r}; observed {lines[cursor:cursor + 2]!r}"
            )
            return False
        cursor += 2
        return True

    def consume_region(name: str, address: int, size: int) -> bytes | None:
        nonlocal cursor, memory_pair_count
        output = bytearray()
        while len(output) < size:
            offset = len(output)
            amount = min(0xFF, size - offset)
            command = f"> m{address + offset:x},{amount:x}"
            if cursor + 1 >= protocol_marker_index:
                result["detail"] = f"prefix {name} read is truncated"
                return None
            response = lines[cursor + 1]
            if (
                lines[cursor] != command
                or not re.fullmatch(rf"< [0-9A-F]{{{amount * 2}}}", response)
            ):
                result["detail"] = (
                    f"prefix {name} exact read mismatch at line {cursor}"
                )
                return None
            output.extend(bytes.fromhex(response[2:]))
            cursor += 2
            memory_pair_count += 1
        return bytes(output)

    for command, response in (
        (f"> Z0,{vblank:x},2", "< OK"),
        ("> c", "< S05"),
        ("> p11", f"< {vblank:08X}"),
    ):
        if not consume_pair(command, response):
            return result
    initial_regions: dict[str, dict[str, Any]] = {}
    for name, address, size in STATE_READ_REGION_SPECS:
        raw = consume_region(f"initial/{name}", address, size)
        if raw is None:
            return result
        initial_regions[name] = {
            "address": f"{address:08X}",
            "bytes": size,
            "sha256": _sha256_bytes(raw),
            "raw_hex": raw.hex().upper(),
        }
    initial_manifest_envelope = {
        "label": f"{session_id}_initial",
        "raw_regions": {
            name: {
                "bytes": item["bytes"],
                "sha256": item["sha256"],
                "raw_hex": item["raw_hex"],
            }
            for name, item in initial_regions.items()
        },
    }
    initial_authoritative = b"".join(
        bytes.fromhex(initial_regions[name]["raw_hex"])
        for name in AUTHORITATIVE_REGION_NAMES
    )
    initial_complete = b"".join(
        bytes.fromhex(initial_regions[name]["raw_hex"])
        for name, _address, _size in STATE_READ_REGION_SPECS
    )
    initial_manifest_envelope.update(
        {
            "authoritative_bundle_sha256": _sha256_bytes(
                initial_authoritative
            ),
            "complete_read_bundle_sha256": _sha256_bytes(initial_complete),
            "complete_read_bundle_bytes": len(initial_complete),
        }
    )
    try:
        initial_manifest = reconstruct_state_manifest_from_raw(
            initial_manifest_envelope, symbols=symbols
        )
    except Exception as error:
        result["detail"] = (
            "prefix initial state raw decode failed: "
            f"{type(error).__name__}: {error}"
        )
        return result
    initial_reconstruction_audit = audit_state_manifest_reconstruction(
        initial_manifest, symbols=symbols
    )
    if initial_reconstruction_audit["status"] != "pass":
        result["detail"] = (
            "prefix initial state did not exactly round-trip through its "
            "raw manifest"
        )
        result["initial_state_reconstruction_audit"] = (
            initial_reconstruction_audit
        )
        return result
    if not b03.initialization_ok(initial_manifest):
        result["detail"] = (
            "prefix initial state did not satisfy the exact B03 "
            "initialization contract"
        )
        return result
    for command, response in (
        (f"> z0,{vblank:x},2", "< OK"),
        (f"> Z0,{pre_step:x},2", "< OK"),
        (f"> Z0,{post_step:x},2", "< OK"),
    ):
        if not consume_pair(command, response):
            return result

    coordinates: list[tuple[int, int, int]] = []
    for attempt, schedule in enumerate(canonical_schedules()):
        limit = spec.tick if attempt == spec.attempt else len(schedule)
        if attempt > spec.attempt:
            break
        for tick, desired in enumerate(schedule[:limit], start=1):
            for command, response in (
                ("> c", "< S05"),
                ("> p11", f"< {pre_step:08X}"),
                ("> p0", f"< {desired:08X}"),
            ):
                if not consume_pair(command, response):
                    return result
            pre_control_raw = consume_region(
                "pre/engine_control",
                b03.ENGINE_CONTROL_BASE,
                b03.ENGINE_CONTROL_USED_BYTES,
            )
            pre_input_raw = consume_region(
                "pre/input_state", b03.INPUT_STATE_BASE, b03.INPUT_STATE_BYTES
            )
            if pre_control_raw is None or pre_input_raw is None:
                return result
            try:
                pre_control = b03.parse_engine_control(pre_control_raw)
                pre_input = b03.parse_input_state(pre_input_raw)
            except Exception as error:
                result["detail"] = (
                    f"pre-step state parse failed at {attempt}/{tick}: {error}"
                )
                return result
            if (
                pre_control["state"] != b03.ENGINE_STATE_ACTIVE
                or pre_control["attempt"] != attempt
                or pre_control["tick"] != tick - 1
                or pre_control["input_held"] != desired
                or pre_input["held"] != desired
            ):
                result["detail"] = (
                    f"pre-step state/input drifted at {attempt}/{tick}"
                )
                return result
            if not consume_pair(f"> z0,{pre_step:x},2", "< OK"):
                return result
            for command, response in (
                ("> c", "< S05"),
                ("> p11", f"< {post_step:08X}"),
                (f"> Z0,{pre_step:x},2", "< OK"),
            ):
                if not consume_pair(command, response):
                    return result
            post_control_raw = consume_region(
                "post/engine_control",
                b03.ENGINE_CONTROL_BASE,
                b03.ENGINE_CONTROL_USED_BYTES,
            )
            post_input_raw = consume_region(
                "post/input_state", b03.INPUT_STATE_BASE, b03.INPUT_STATE_BYTES
            )
            if post_control_raw is None or post_input_raw is None:
                return result
            try:
                post_control = b03.parse_engine_control(post_control_raw)
                b03.parse_input_state(post_input_raw)
                _validate_post_step(
                    control=post_control,
                    attempt=attempt,
                    tick=tick,
                    desired=desired,
                )
            except Exception as error:
                result["detail"] = (
                    f"post-step state parse failed at {attempt}/{tick}: {error}"
                )
                return result
            coordinates.append((attempt, tick, desired))

    if cursor != protocol_marker_index:
        result["detail"] = (
            f"prefix parser stopped at {cursor}; marker is {protocol_marker_index}"
        )
        result["unconsumed_lines"] = lines[cursor:protocol_marker_index]
        return result
    prefix_lines = lines[:protocol_marker_index]
    result.update(
        {
            "status": "pass",
            "detail": "entire startup and imported input drive consumed",
            "protocol_marker_index": protocol_marker_index,
            "prefix_line_count": len(prefix_lines),
            "prefix_sha256": _sha256_bytes(
                ("\n".join(prefix_lines) + "\n").encode("utf-8")
            ),
            "memory_read_pair_count": memory_pair_count,
            "initial_state_regions": initial_regions,
            "initial_state_manifest": initial_manifest,
            "initial_state_reconstruction_audit": (
                initial_reconstruction_audit
            ),
            "initial_state_reconstructed_from_trace": True,
            "initialization_ok": True,
            "authoritative_step_count": len(coordinates),
            "input_drive_sha256": _sha256_bytes(
                bytes(item[2] for item in coordinates)
            ),
            "last_coordinate": (
                {
                    "attempt": coordinates[-1][0],
                    "tick": coordinates[-1][1],
                    "input": coordinates[-1][2],
                }
                if coordinates
                else None
            ),
            "coverage": "exact-all-lines-before-capture-protocol-marker",
        }
    )
    return result


def audit_visual_trace(
    lines: list[str],
    captures: list[dict[str, Any]],
    *,
    pre_step: int,
    post_step: int,
    renderer_return: int,
    dma_commit_entry: int,
    vblank: int,
    symbols: dict[str, int],
) -> dict[str, Any]:
    """Reparse all five unambiguous live-session trace slices."""
    allowed_control = re.compile(
        r"> (?:[Zz]0,[0-9a-f]+,2|c|p(?:0|11)|m[0-9a-f]+,[0-9a-f]+|k)"
    )
    expected_names = [spec.name for spec in CAPTURE_PLAN]
    observed_names = [
        match.group(1)
        for line in lines
        if (
            match := re.fullmatch(
                r"# visual_([a-z0-9_]+) begin", line
            )
        )
    ]
    by_name = {
        record.get("name"): record
        for record in captures
        if record.get("name") in expected_names
    }
    spec_by_name = {spec.name: spec for spec in CAPTURE_PLAN}
    capture_results: list[dict[str, Any]] = []
    occupied_ranges: list[tuple[int, int]] = []
    for name in expected_names:
        record = by_name.get(name, {})
        session_id = f"visual_{name}"
        begin = f"# {session_id} begin"
        end_marker = f"# {session_id} end"
        begin_indices = [
            index for index, line in enumerate(lines) if line == begin
        ]
        end_indices = [
            index
            for index, line in enumerate(lines)
            if line == end_marker
        ]
        if (
            len(begin_indices) != 1
            or len(end_indices) != 1
            or begin_indices[0] >= end_indices[0]
        ):
            capture_results.append(
                {
                    "name": name,
                    "session_id": session_id,
                    "status": "fail",
                    "detail": "session begin/end markers are not exact",
                }
            )
            continue
        begin_index = begin_indices[0]
        end_index = end_indices[0]
        occupied_ranges.append((begin_index, end_index))
        session_lines = lines[begin_index : end_index + 1]
        transcript_sha256 = _sha256_bytes(
            ("\n".join(session_lines) + "\n").encode("utf-8")
        )
        event_order = record.get("frame_binding", {}).get(
            "event_order", []
        )
        audit = audit_capture_breakpoint_order(
            session_lines,
            pre_step=pre_step,
            post_step=post_step,
            renderer_return=renderer_return,
            dma_commit_entry=dma_commit_entry,
            event_order=event_order,
        )
        prefix_audit = audit_visual_session_prefix(
            session_lines,
            protocol_marker_index=audit.get(
                "protocol_marker_start_index", -1
            ),
            spec=spec_by_name[name],
            vblank=vblank,
            pre_step=pre_step,
            post_step=post_step,
            symbols=symbols,
        )
        protocol_marker_end = audit.get("protocol_marker_end_index", -1)
        suffix_lines = session_lines[protocol_marker_end + 1 :]
        suffix_middle = suffix_lines[1:-1] if len(suffix_lines) >= 2 else []
        suffix_audit = {
            "schema": "r94-m5-b03-visual-session-suffix-audit-v1",
            "status": (
                "pass"
                if (
                    len(suffix_lines) >= 2
                    and suffix_lines[0] == "> k"
                    and suffix_lines[-1] == end_marker
                    and len(suffix_middle) <= 1
                    and all(
                        re.fullmatch(
                            r"# host cleanup taskkill exact owned pid "
                            r"[1-9][0-9]* rc=-?[0-9]+",
                            line,
                        )
                        for line in suffix_middle
                    )
                )
                else "fail"
            ),
            "lines": suffix_lines,
            "coverage": "exact-all-lines-after-capture-protocol-marker",
        }
        report_gdb = record.get("gdb", {})
        report_audit = report_gdb.get(
            "capture_breakpoint_order", {}
        )
        transcript_matches = (
            report_gdb.get("transcript_sha256")
            == transcript_sha256
        )
        report_audit_matches = report_audit == audit
        session_writes = debugger_write_packets(session_lines)
        unexpected_control_packets = [
            line
            for line in session_lines
            if line.startswith("> ") and not allowed_control.fullmatch(line)
        ]
        state_trace_matches = False
        png_consume_matches = False
        if audit.get("status") == "pass":
            target_post_binding = audit.get("target_post_read_binding", {})
            target_binding = audit.get("target_renderer_read_binding", {})
            state_trace_matches = (
                _trace_observation_matches_report(
                    target_post_binding,
                    record.get("target_post_step", {}),
                    record.get("frame_binding", {}).get(
                        "target_post_step_boundary", {}
                    ),
                    symbols=symbols,
                )
                and _trace_observation_matches_report(
                    target_binding,
                    record.get("target_post_renderer", {}),
                    record.get("frame_binding", {}).get(
                        "publication_after_renderer", {}
                    ),
                    symbols=symbols,
                )
            )
            terminal_required = name == "terminal_complete_20"
            terminal_hold_binding = audit.get(
                "terminal_checkpoint_hold_read_binding"
            )
            terminal_service_binding = audit.get(
                "terminal_service_post_read_binding"
            )
            if terminal_required:
                state_trace_matches = (
                    state_trace_matches
                    and _trace_observation_matches_report(
                        terminal_hold_binding or {},
                        record.get("terminal_checkpoint_hold", {}),
                        record.get("frame_binding", {}).get(
                            "terminal_checkpoint_hold_boundary", {}
                        ),
                        symbols=symbols,
                    )
                    and _trace_observation_matches_report(
                        terminal_service_binding or {},
                        record.get("terminal_service_post", {}),
                        record.get("frame_binding", {}).get(
                            "terminal_service_post_boundary", {}
                        ),
                        symbols=symbols,
                    )
                )
            else:
                state_trace_matches = (
                    state_trace_matches
                    and terminal_hold_binding is None
                    and terminal_service_binding is None
                    and record.get("terminal_checkpoint_hold") is None
                    and record.get("terminal_service_post") is None
                )
            idle_index = 0
            consumed_png_sha256: str | None = None
            consumed_png_kind: str | None = None
            consumed_png_index: int | None = None
            consumed_png_point: str | None = None
            observed_stops = audit.get("observed_stops", [])
            if len(observed_stops) != len(event_order):
                state_trace_matches = False
            for item in observed_stops:
                kind = item.get("kind")
                if kind == "successor_post":
                    manifest = record.get("successor_post_step", {})
                    boundary = record.get("frame_binding", {}).get(
                        "successor_post_step_boundary", {}
                    )
                elif kind == "pre_odd_empty_dma":
                    idle = record.get("ui_service_idle_boundaries", [])
                    if not isinstance(idle, list) or idle_index >= len(idle):
                        state_trace_matches = False
                        continue
                    manifest = idle[idle_index].get("state", {})
                    boundary = idle[idle_index].get(
                        "publication_boundary", {}
                    )
                    idle_index += 1
                elif kind == "odd_dma_precommit":
                    manifest = record.get("png_precommit_state", {})
                    boundary = record.get("frame_binding", {}).get(
                        "successor_boundary", {}
                    )
                else:
                    state_trace_matches = False
                    continue
                state_trace_matches = (
                    state_trace_matches
                    and _trace_observation_matches_report(
                        item.get("state_read_binding", {}),
                        manifest,
                        boundary,
                        symbols=symbols,
                    )
                )
                if "png_consume" in item:
                    consumed_png_sha256 = item["png_consume"].get(
                        "sha256"
                    )
                    consumed_png_kind = item.get("kind")
                    consumed_png_index = item.get("index")
                    consumed_png_point = item["png_consume"].get(
                        "consume_point"
                    )
            frame_binding = record.get("frame_binding", {})
            png_consume_matches = (
                _valid_sha256(consumed_png_sha256)
                and consumed_png_sha256
                == record.get("screenshot", {}).get("sha256")
                and consumed_png_kind
                == frame_binding.get("png_consume_event_kind")
                and consumed_png_index
                == frame_binding.get("png_consume_event_index")
                and consumed_png_point
                == frame_binding.get("png_consume_point")
                and sum(
                    1
                    for item in observed_stops
                    if "png_consume" in item
                )
                == 1
            )
        status = (
            "pass"
            if (
                record.get("session", {}).get("session_id")
                == session_id
                and audit.get("status") == "pass"
                and prefix_audit.get("status") == "pass"
                and suffix_audit.get("status") == "pass"
                and prefix_audit.get("authoritative_step_count")
                == record.get("input_drive", {}).get("steps")
                and prefix_audit.get("input_drive_sha256")
                == record.get("input_drive", {}).get("observed_sha256")
                and transcript_matches
                and report_audit_matches
                and state_trace_matches
                and png_consume_matches
                and session_writes == []
                and unexpected_control_packets == []
            )
            else "fail"
        )
        capture_results.append(
            {
                "name": name,
                "session_id": session_id,
                "status": status,
                "transcript_sha256": transcript_sha256,
                "report_transcript_sha256": report_gdb.get(
                    "transcript_sha256"
                ),
                "transcript_hash_matches": transcript_matches,
                "event_order": event_order,
                "audit": audit,
                "prefix_audit": prefix_audit,
                "suffix_audit": suffix_audit,
                "report_audit_matches": report_audit_matches,
                "state_trace_matches": state_trace_matches,
                "png_consume_matches": png_consume_matches,
                "guest_write_packets": session_writes,
                "unexpected_control_packets": unexpected_control_packets,
            }
        )

    nonoverlapping = all(
        first_end < second_start
        for (_, first_end), (second_start, _) in zip(
            occupied_ranges, occupied_ranges[1:]
        )
    )
    exact_session_tiling = (
        len(occupied_ranges) == len(CAPTURE_PLAN)
        and bool(occupied_ranges)
        and occupied_ranges[0][0] == 0
        and occupied_ranges[-1][1] == len(lines) - 1
        and all(
            first_end + 1 == second_start
            for (_, first_end), (second_start, _) in zip(
                occupied_ranges, occupied_ranges[1:]
            )
        )
    )
    guest_writes = debugger_write_packets(lines)
    unexpected_controls = [
        line
        for line in lines
        if line.startswith("> ") and not allowed_control.fullmatch(line)
    ]
    status = (
        "pass"
        if (
            observed_names == expected_names
            and len(captures) == len(CAPTURE_PLAN)
            and len(by_name) == len(CAPTURE_PLAN)
            and len(occupied_ranges) == len(CAPTURE_PLAN)
            and nonoverlapping
            and exact_session_tiling
            and guest_writes == []
            and unexpected_controls == []
            and all(
                item.get("status") == "pass"
                for item in capture_results
            )
        )
        else "fail"
    )
    return {
        "schema": "r94-m5-b03-visual-trace-audit-v1",
        "status": status,
        "expected_session_order": expected_names,
        "observed_session_order": observed_names,
        "capture_count": len(capture_results),
        "session_ranges_nonoverlapping": nonoverlapping,
        "exact_session_tiling": exact_session_tiling,
        "trace_line_count": len(lines),
        "trace_sha256": _sha256_bytes(
            ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        ),
        "session_ranges": [
            {"begin_index": begin, "end_index": end}
            for begin, end in occupied_ranges
        ],
        "guest_write_packets": guest_writes,
        "unexpected_control_packets": unexpected_controls,
        "addresses": {
            "vblank": f"{vblank:08X}",
            "pre_step": f"{pre_step:08X}",
            "post_step": f"{post_step:08X}",
            "renderer_return": f"{renderer_return:08X}",
            "dma_commit_entry": f"{dma_commit_entry:08X}",
        },
        "captures": capture_results,
    }

def _coordinate_ok(
    state: dict[str, Any],
    *,
    engine_state: int,
    attempt: int,
    tick: int,
) -> bool:
    engine = state["engine"]
    return (
        engine["state"] == engine_state
        and engine["attempt"] == attempt
        and engine["tick"] == tick
    )


def _capture_publication_boundary(
    remote: GdbRemote, *, label: str
) -> dict[str, Any]:
    del label
    frame_raw = _read_region(remote, DEBUG_FRAME_COUNTER, 4)
    dma_raw = _read_region(
        remote, DMA_QUEUE_CONTROL_BASE, DMA_QUEUE_CONTROL_BYTES
    )
    return {
        "debug_frame_counter": int.from_bytes(frame_raw, "big"),
        "dma_queue": {
            "published_depth": int.from_bytes(dma_raw[0:2], "big"),
            "build_depth": int.from_bytes(dma_raw[2:4], "big"),
            "build_words": int.from_bytes(dma_raw[4:8], "big"),
            "commit_words": int.from_bytes(dma_raw[8:10], "big"),
            "frame_pending": int.from_bytes(
                dma_raw[
                    DMA_QUEUE_FRAME_PENDING_OFFSET:
                    DMA_QUEUE_FRAME_PENDING_OFFSET + 2
                ],
                "big",
            ),
            "reserved_0": int.from_bytes(dma_raw[12:14], "big"),
            "reserved_1": int.from_bytes(dma_raw[14:16], "big"),
            "raw_hex": dma_raw.hex().upper(),
            "raw_sha256": _sha256_bytes(dma_raw),
        },
    }


def _prearm_native_screenshot(
    *,
    process_id: int,
    window: int,
    screenshot_directory: Path,
    events: list[str],
) -> dict[str, Any]:
    """Queue one complete UI edge at the audited pre-render POST."""
    screenshot_directory.mkdir(parents=True, exist_ok=True)
    identity = m4_visual._window_identity(process_id, window)  # noqa: SLF001
    before_files = m4_visual._screenshot_directory_state(  # noqa: SLF001
        screenshot_directory
    )
    if before_files:
        raise EvidenceFailure(
            "isolated screenshot directory is not empty before pre-arm"
        )
    capture_started_ns = time.time_ns()
    b03._post_key(window, SCREENSHOT_KEY, pressed=True)  # noqa: SLF001
    down_index = len(events)
    events.append("F1:ui.screenshot:down")
    b03._post_key(window, SCREENSHOT_KEY, pressed=False)  # noqa: SLF001
    up_index = len(events)
    events.append("F1:ui.screenshot:up")
    return {
        "identity": identity,
        "before_files": before_files,
        "capture_started_ns": capture_started_ns,
        "event_indices": {
            "down": down_index,
            "up": up_index,
        },
    }


def _native_screenshot_tool_output(consume_point: str) -> str:
    if consume_point == "odd_dma_precommit":
        return (
            "BlastEm native ODD framebuffer consumed while stopped "
            "at the calculated precommit DMA entry"
        )
    if consume_point == "successor_post_after_verified_empty_odd_commit":
        return (
            "BlastEm native ODD framebuffer consumed while stopped "
            "at the guarded same-frame successor POST after the "
            "verified empty ODD commit"
        )
    raise EvidenceFailure(f"invalid PNG consume point: {consume_point!r}")


def _capture_native_frame(
    *,
    remote: GdbRemote,
    root: Path,
    process_id: int,
    window: int,
    screenshot_directory: Path,
    output: Path,
    spec: CaptureSpec,
    symbols: dict[str, int],
    post_step: int,
    renderer_return: int,
    dma_commit_entry: int,
    transcript: list[str],
    events: list[str],
    prearmed: dict[str, Any],
    target_post_renderer: dict[str, Any],
    publication_before_renderer: dict[str, Any],
    publication_after_renderer: dict[str, Any],
) -> dict[str, Any]:
    """Run the successor-POST versus calculated-ODD-DMA state machine."""
    del process_id, window, events

    def consume_screenshot(*, consume_point: str) -> dict[str, Any]:
        try:
            tool_output = _native_screenshot_tool_output(consume_point)
        except EvidenceFailure as error:
            raise EvidenceFailure(f"{spec.name} {error}") from error
        transcript.append(f"# visual_{spec.name} png_consume begin")
        try:
            result = m4_visual._consume_native_screenshot(  # noqa: SLF001
                root=root,
                screenshot_directory=screenshot_directory,
                before_files=prearmed["before_files"],
                capture_started_ns=prearmed["capture_started_ns"],
                output=output,
                identity=prearmed["identity"],
                screenshot_key=SCREENSHOT_KEY,
                tool_output=tool_output,
            )
        except Exception as error:
            raise EvidenceFailure(
                f"{spec.name} native screenshot failed: "
                f"{type(error).__name__}: {error}"
            ) from error
        if m4_visual._screenshot_directory_state(  # noqa: SLF001
            screenshot_directory
        ):
            raise EvidenceFailure(
                f"{spec.name} isolated screenshot directory retained PNGs"
            )
        png_sha256 = result.get("sha256")
        if not _valid_sha256(png_sha256):
            raise EvidenceFailure(
                f"{spec.name} consumed PNG lacks a valid SHA-256"
            )
        transcript.append(
            f"# visual_{spec.name} png_consume end sha256={png_sha256}"
        )
        return result

    before_frame = publication_before_renderer["debug_frame_counter"]
    target_frame = publication_after_renderer["debug_frame_counter"]
    target_queue = publication_after_renderer["dma_queue"]
    if target_frame != before_frame + 1:
        raise EvidenceFailure(
            f"{spec.name} target renderer interval was not exactly one frame"
        )
    if (
        target_queue["published_depth"] != 0
        or target_queue["build_depth"] != 0
        or target_queue["build_words"] != 0
        or target_queue["frame_pending"] != 0
    ):
        raise EvidenceFailure(
            f"{spec.name} target renderer did not return after exact commit"
        )
    target_manifest = _state_manifest(target_post_renderer)
    target_renderer_sha256 = target_manifest["raw_regions"][
        "renderer_state"
    ]["sha256"]
    target_sat_sha256 = target_manifest["raw_regions"]["sat_shadow"][
        "sha256"
    ]
    if not all(
        renderer_health(
            target_post_renderer["renderer"],
            attempt=spec.attempt,
            tick=spec.tick,
        ).values()
    ):
        raise EvidenceFailure(
            f"{spec.name} target renderer was unhealthy at publication"
        )
    presentation = expected_odd_presentation(target_frame)
    if presentation["status"] != "pass":
        raise EvidenceFailure(
            f"{spec.name} invalid ODD presentation plan: {presentation}"
        )
    odd_frame = presentation["expected_odd_presentation_frame"]
    expected_pre_odd = presentation["expected_idle_boundary_count"]
    target_commit_words = target_queue["commit_words"]
    if target_commit_words != TARGET_RENDER_COMMIT_WORDS:
        raise EvidenceFailure(
            f"{spec.name} target publication committed {target_commit_words} "
            f"words; exact renderer transfer {TARGET_RENDER_COMMIT_WORDS} required"
        )

    if remote.request(f"z0,{renderer_return:x},2") != "OK":
        raise EvidenceFailure(
            f"{spec.name} target renderer-return removal failed"
        )
    if remote.request(f"Z0,{post_step:x},2") != "OK":
        raise EvidenceFailure(
            f"{spec.name} competing successor POST insertion failed"
        )
    if remote.request(f"Z0,{dma_commit_entry:x},2") != "OK":
        raise EvidenceFailure(
            f"{spec.name} competing DMA-entry insertion failed"
        )

    successor_raw: dict[str, Any] | None = None
    successor_manifest: dict[str, Any] | None = None
    successor_boundary: dict[str, Any] | None = None
    png_manifest: dict[str, Any] | None = None
    png_boundary: dict[str, Any] | None = None
    odd_classification: dict[str, Any] | None = None
    screenshot: dict[str, Any] | None = None
    idle_boundaries: list[dict[str, Any]] = []
    state_events: list[dict[str, Any]] = []
    event_order: list[str] = []
    post_proven = False
    png_consumed = False
    png_consume_point: str | None = None
    png_consume_event_index: int | None = None
    pre_odd_count = 0
    empty_dma_commits_executed = 0

    for event_index in range(3):
        pc = _continue_and_read_pc(
            remote, label=f"{spec.name} state-machine event {event_index}"
        )
        if pc == post_step:
            if post_proven:
                raise EvidenceFailure(
                    f"{spec.name} duplicate successor POST event"
                )
            successor_raw = _capture_coherent_state(
                remote,
                label=f"{spec.name}_exact_successor_post",
                symbols=symbols,
            )
            successor_manifest = _state_manifest(successor_raw)
            successor_boundary = _capture_publication_boundary(
                remote, label=f"{spec.name}_exact_successor_post"
            )
            engine_state, attempt, tick = _expected_successor(spec)
            expected_commit_words = (
                0
                if empty_dma_commits_executed
                else target_commit_words
            )
            queue = successor_boundary["dma_queue"]
            exact_successor = (
                _coordinate_ok(
                    successor_raw,
                    engine_state=engine_state,
                    attempt=attempt,
                    tick=tick,
                )
                and all(
                    renderer_health(
                        successor_raw["renderer"],
                        attempt=spec.attempt,
                        tick=spec.tick,
                    ).values()
                )
                and successor_manifest["raw_regions"][
                    "renderer_state"
                ]["sha256"]
                == target_renderer_sha256
                and queue["published_depth"] == 0
                and queue["build_depth"] == 0
                and queue["build_words"] == 0
                and queue["commit_words"] == expected_commit_words
                and queue["frame_pending"] == 0
                and target_frame
                <= successor_boundary["debug_frame_counter"]
                <= odd_frame
                and (
                    spec.name != "terminal_complete_20"
                    or successor_manifest[
                        "authoritative_bundle_sha256"
                    ]
                    == target_manifest[
                        "authoritative_bundle_sha256"
                    ]
                )
            )
            if not exact_successor:
                raise EvidenceFailure(
                    f"{spec.name} successor POST state/renderer/queue drifted"
                )
            odd_bound_before_post = (
                png_manifest is not None and not png_consumed
            )
            if (png_consumed or odd_bound_before_post) and (
                successor_boundary["debug_frame_counter"] != odd_frame
            ):
                raise EvidenceFailure(
                    f"{spec.name} guarded successor POST did not remain on "
                    "the ODD frame"
                )
            if remote.request(f"z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{spec.name} successor POST removal failed"
                )
            post_proven = True
            event_order.append("successor_post")
            state_events.append(
                {
                    "index": len(state_events),
                    "kind": "successor_post",
                    "frame": successor_boundary[
                        "debug_frame_counter"
                    ],
                    "state_complete_read_sha256": successor_manifest[
                        "complete_read_bundle_sha256"
                    ],
                    "authoritative_sha256": successor_manifest[
                        "authoritative_bundle_sha256"
                    ],
                    "renderer_sha256": successor_manifest[
                        "raw_regions"
                    ]["renderer_state"]["sha256"],
                    "dma_queue": successor_boundary["dma_queue"],
                }
            )
            if odd_bound_before_post:
                png_consume_point = (
                    "successor_post_after_verified_empty_odd_commit"
                )
                screenshot = consume_screenshot(
                    consume_point=png_consume_point
                )
                png_consumed = True
                png_consume_event_index = len(state_events) - 1
                state_events[-1].update(
                    {
                        "png_consumed_while_stopped": True,
                        "png_consume_point": png_consume_point,
                    }
                )
                break
            if png_consumed:
                break
            continue

        if pc != dma_commit_entry:
            raise EvidenceFailure(
                f"{spec.name} state-machine stopped at ${pc:08X}; "
                f"POST ${post_step:08X} or DMA ${dma_commit_entry:08X} "
                "required"
            )
        observed_raw = _capture_coherent_state(
            remote,
            label=f"{spec.name}_dma_event_{event_index}",
            symbols=symbols,
        )
        observed_manifest = _state_manifest(observed_raw)
        observed_boundary = _capture_publication_boundary(
            remote, label=f"{spec.name}_dma_event_{event_index}"
        )
        frame = observed_boundary["debug_frame_counter"]
        expected_commit_words = (
            0 if empty_dma_commits_executed else target_commit_words
        )

        if frame < odd_frame:
            if (
                png_consumed
                or expected_pre_odd != 1
                or frame != target_frame + 1
                or pre_odd_count != 0
            ):
                raise EvidenceFailure(
                    f"{spec.name} unexpected/duplicate pre-ODD DMA event"
                )
            classification = classify_ui_service_boundary(
                spec,
                observed_manifest,
                observed_boundary,
                previous_frame=target_frame,
                target_authoritative_sha256=target_manifest[
                    "authoritative_bundle_sha256"
                ],
                target_renderer_sha256=target_renderer_sha256,
                expected_phase="idle",
                target_commit_words=target_commit_words,
            )
            if classification["status"] != "pass":
                raise EvidenceFailure(
                    f"{spec.name} pre-ODD DMA was not target-renderer/empty: "
                    f"{classification}"
                )
            idle_record = {
                "index": 0,
                "diagnostic_torn_snapshot": True,
                "state": observed_manifest,
                "publication_boundary": observed_boundary,
                "classification": classification,
            }
            idle_boundaries.append(idle_record)
            pre_odd_count += 1
            empty_dma_commits_executed += 1
            event_order.append("pre_odd_empty_dma")
            state_events.append(
                {
                    "index": len(state_events),
                    "kind": "pre_odd_empty_dma",
                    "frame": frame,
                    "diagnostic_torn_snapshot": True,
                    "authoritative_sha256": observed_manifest[
                        "authoritative_bundle_sha256"
                    ],
                    "renderer_sha256": observed_manifest[
                        "raw_regions"
                    ]["renderer_state"]["sha256"],
                    "dma_queue": observed_boundary["dma_queue"],
                }
            )
            continue

        if frame != odd_frame or png_consumed:
            raise EvidenceFailure(
                f"{spec.name} wrong/duplicate ODD DMA frame {frame}; "
                f"{odd_frame} required"
            )
        odd_classification = classify_odd_dma_boundary(
            spec,
            observed_manifest,
            observed_boundary,
            expected_frame=odd_frame,
            successor_post=(
                successor_manifest if post_proven else None
            ),
            target_authoritative_sha256=target_manifest[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer_sha256,
            expected_commit_words=expected_commit_words,
        )
        if odd_classification["status"] != "pass":
            raise EvidenceFailure(
                f"{spec.name} invalid ODD precommit boundary: "
                f"{odd_classification}"
        )
        png_manifest = observed_manifest
        png_boundary = observed_boundary
        event_order.append("odd_dma_precommit")
        state_events.append(
            {
                "index": len(state_events),
                "kind": "odd_dma_precommit",
                "frame": frame,
                "queue_mode": odd_classification["queue_mode"],
                "authoritative_sha256": observed_manifest[
                    "authoritative_bundle_sha256"
                ],
                "renderer_sha256": observed_manifest["raw_regions"][
                    "renderer_state"
                ]["sha256"],
                "dma_queue": observed_boundary["dma_queue"],
                "png_consumed_while_stopped": post_proven,
                "verified_empty_commit_executed_before_png": (
                    not post_proven
                ),
            }
        )
        if post_proven:
            png_consume_point = "odd_dma_precommit"
            screenshot = consume_screenshot(
                consume_point=png_consume_point
            )
            png_consumed = True
            png_consume_event_index = len(state_events) - 1
            state_events[-1]["png_consume_point"] = png_consume_point
            break
        if (
            odd_classification["queue_mode"] != "target_empty"
            or odd_classification.get("empty_queue_before_noop_commit")
            is not True
        ):
            raise EvidenceFailure(
                f"{spec.name} ODD-before-POST queue was not empty"
            )
        empty_dma_commits_executed += 1

    if not post_proven or not png_consumed:
        raise EvidenceFailure(
            f"{spec.name} did not prove one POST and one ODD PNG event"
        )
    legal_orders = (
        [
            ["successor_post", "odd_dma_precommit"],
            ["odd_dma_precommit", "successor_post"],
        ]
        if expected_pre_odd == 0
        else [
            [
                "successor_post",
                "pre_odd_empty_dma",
                "odd_dma_precommit",
            ],
            [
                "pre_odd_empty_dma",
                "successor_post",
                "odd_dma_precommit",
            ],
            [
                "pre_odd_empty_dma",
                "odd_dma_precommit",
                "successor_post",
            ],
        ]
    )
    if event_order not in legal_orders:
        raise EvidenceFailure(
            f"{spec.name} illegal event order {event_order}"
        )
    if pre_odd_count != expected_pre_odd:
        raise EvidenceFailure(
            f"{spec.name} pre-ODD DMA count {pre_odd_count}; "
            f"{expected_pre_odd} required"
        )
    if remote.request(f"z0,{dma_commit_entry:x},2") != "OK":
        raise EvidenceFailure(
            f"{spec.name} persistent DMA breakpoint removal failed"
        )
    transcript.append(f"# visual_{spec.name} capture_protocol end")
    if (
        successor_raw is None
        or successor_manifest is None
        or successor_boundary is None
        or png_manifest is None
        or png_boundary is None
        or odd_classification is None
        or screenshot is None
        or png_consume_point is None
        or png_consume_event_index is None
    ):
        raise EvidenceFailure(
            f"{spec.name} state-machine evidence remained incomplete"
        )

    race_order = (
        "post_before_odd"
        if event_order.index("successor_post")
        < event_order.index("odd_dma_precommit")
        else "odd_before_post"
    )
    odd_before_post = race_order == "odd_before_post"
    png_consume_event_kind = (
        "successor_post" if odd_before_post else "odd_dma_precommit"
    )
    presentation_boundaries = [
        *[
            presentation_boundary_summary(
                index=item["index"],
                phase="pre_odd_empty_dma",
                state=item["state"],
                boundary=item["publication_boundary"],
                classification=item["classification"],
            )
            for item in idle_boundaries
        ],
        presentation_boundary_summary(
            index=len(idle_boundaries),
            phase="odd_dma_precommit",
            state=png_manifest,
            boundary=png_boundary,
            classification=odd_classification,
        ),
    ]
    return {
        "published": target_post_renderer,
        "successor": successor_raw,
        "successor_manifest": successor_manifest,
        "successor_post_step_manifest": successor_manifest,
        "successor_post_step_boundary": successor_boundary,
        "successor_post_renderer_manifest": None,
        "successor_post_renderer_boundary": None,
        "png_precommit_manifest": png_manifest,
        "idle_boundaries": idle_boundaries,
        "screenshot": screenshot,
        "frame_binding": {
            "state_machine": "successor-post-vs-odd-dma-v1",
            "capture_started_ns": prearmed["capture_started_ns"],
            "ui_event_indices": prearmed["event_indices"],
            "ui_screenshot_key": SCREENSHOT_KEY[0],
            "ui_edge_queued_at": "target_post_step_before_renderer",
            "renderer_return_address": f"{renderer_return:08X}",
            "successor_post_address": f"{post_step:08X}",
            "dma_commit_entry_address": f"{dma_commit_entry:08X}",
            "target_committed_before_renderer_return": True,
            "target_publication_vblank_delta": target_frame - before_frame,
            "target_publication_frame": target_frame,
            "target_renderer_sha256": target_renderer_sha256,
            "target_renderer_exact_at_publication": True,
            "target_sat_sha256": target_sat_sha256,
            "target_sat_exact_at_publication": True,
            "calculated_odd_presentation_frame": odd_frame,
            "publication_after_parity": presentation[
                "publication_after_parity"
            ],
            "expected_idle_preemption_boundary_count": expected_pre_odd,
            "expected_odd_delta": presentation["expected_odd_delta"],
            "expected_odd_presentation_frame": odd_frame,
            "competing_breakpoints_armed_once": True,
            "events": state_events,
            "event_order": event_order,
            "allowed_event_order": True,
            "pre_odd_empty_dma_count": pre_odd_count,
            "pre_odd_nonempty_dma_count": 0,
            "nonempty_dma_commits_between_target_and_png": 0,
            "idle_preemption_boundary_count": pre_odd_count,
            "idle_preemption_boundaries": presentation_boundaries[
                :pre_odd_count
            ],
            "presentation_boundaries": presentation_boundaries,
            "successor_post_manifest_sha256": successor_manifest[
                "complete_read_bundle_sha256"
            ],
            "successor_post_coordinate": {
                "engine_state": successor_manifest["engine"]["state"],
                "attempt": successor_manifest["engine"]["attempt"],
                "tick": successor_manifest["engine"]["tick"],
            },
            "successor_post_exact": True,
            "successor_post_step_boundary": successor_boundary,
            "successor_post_renderer_boundary": None,
            "successor_boundary": png_boundary,
            "successor_resolution": odd_classification,
            "odd_dma_frame_exact": True,
            "odd_dma_queue_mode": odd_classification["queue_mode"],
            "png_bound_precommit_frame": odd_frame,
            "png_consumed_frame": odd_frame,
            "png_consume_event_kind": png_consume_event_kind,
            "png_consume_event_index": png_consume_event_index,
            "png_consume_point": png_consume_point,
            "png_consumed_while_stopped_at_odd_dma_entry": (
                not odd_before_post
            ),
            "png_consumed_while_stopped_at_successor_post": (
                odd_before_post
            ),
            "odd_dma_commit_executed_before_png": odd_before_post,
            "odd_dma_commit_before_png_verified_empty": odd_before_post,
            "post_after_odd_guarded_from_next_dma": odd_before_post,
            "png_consume_guarded_from_next_dma": True,
            "race_order": race_order,
            "race_handled": True,
            "native_png_presented_before_successor_dma_commit": True,
            "stopped_at_successor_dma_commit_entry": True,
            "successor_dma_frame_pending_before_commit": png_boundary[
                "dma_queue"
            ]["frame_pending"],
            "no_post_target_dma_transfer_before_png": True,
            "exact_successor_phase": True,
            "authoritative_service_steps": (
                0 if spec.name == "terminal_complete_20" else 1
            ),
            "ui_screenshot_edge_count": 1,
            "ui_screenshot_rearm_count": 0,
            "ui_screenshot_edge_reused_after_noop_commit": odd_before_post,
            "screenshot_count": 1,
            "missing_or_extra_screenshots": False,
            "breakpoint_race_detected": False,
            "publication_before_renderer": publication_before_renderer,
            "publication_after_renderer": publication_after_renderer,
            "pipeline": (
                "arm exact successor POST and persistent DMA-entry guards; "
                "accept only parity-derived legal P/I/O orders; consume the "
                "single prearmed PNG at ODD, or after its verified empty "
                "commit at guarded POST; require exact successor POST before "
                "any later DMA"
            ),
        },
    }


def _validate_post_step(
    *,
    control: dict[str, Any],
    attempt: int,
    tick: int,
    desired: int,
) -> None:
    if attempt < 2 and tick == 720:
        if (
            control["state"] != b03.ENGINE_STATE_ACTIVE
            or control["attempt"] != attempt + 1
            or control["tick"] != 0
        ):
            raise EvidenceFailure(
                f"attempt {attempt} timeout reset coordinate drifted"
            )
        return
    expected_state = (
        b03.ENGINE_STATE_TERMINAL
        if attempt == 2 and tick == 292
        else b03.ENGINE_STATE_ACTIVE
    )
    if (
        control["state"] != expected_state
        or control["attempt"] != attempt
        or control["tick"] != tick
        or control["input_held"] != desired
    ):
        raise EvidenceFailure(
            f"post-step coordinate/input drifted at {attempt}/{tick}"
        )


def _next_desired(
    schedules: tuple[bytes, bytes, bytes],
    *,
    attempt: int,
    tick: int,
) -> int:
    schedule = schedules[attempt]
    if tick < len(schedule):
        return schedule[tick]
    if attempt + 1 < len(schedules):
        return schedules[attempt + 1][0]
    return 0


def _run_capture_session(
    *,
    root: Path,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    spec: CaptureSpec,
    staging_output: Path,
    renderer_return: int,
    dma_commit_entry: int,
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    session_id = f"visual_{spec.name}"
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: Any = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    events: list[str] = []
    record: dict[str, Any] | None = None
    schedules = canonical_schedules()
    observed = bytearray()
    expected = _drive_prefix(spec)

    with b03._owned_session_directory(  # noqa: SLF001
        prefix=f"recursion94-m5-visual-{spec.name}-",
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
        bind_session_screenshot(controller_profile)
        isolated_bindings = verify_controller_bindings(
            Path(controller_profile["path"])
        )
        if isolated_bindings["status"] != "pass":
            raise EvidenceFailure(
                f"{session_id} isolated standard pad binding drifted"
            )
        screenshot_directory = Path(
            controller_profile["session_isolation"]["screenshot_path"]
        )
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *b03.LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = b03._launch_owned_emulator(  # noqa: SLF001
                command, cwd=isolated_runtime, transcript=transcript
            )
            remote = GdbRemote(
                "127.0.0.1", 1234, timeout_seconds, transcript
            )
            if process.poll() is not None:
                raise EvidenceFailure(
                    f"{session_id} BlastEm exited early"
                )
            vblank = symbols["VBlankInterrupt"]
            pre_step = symbols["MainLoopGameplayStepCall"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint insertion failed"
                )
            _require_stop_at(
                remote, vblank, label=f"{session_id} startup VBlank"
            )
            window = find_process_window(process.pid, 20.0)
            b03._set_gameplay_mask(  # noqa: SLF001
                window, schedules[0][0], pressed, events
            )
            initial = _capture_coherent_state(
                remote, label=f"{session_id}_initial", symbols=symbols
            )
            if not b03.initialization_ok(initial):
                raise EvidenceFailure(
                    f"{session_id} did not initialize canonical B03"
                )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{pre_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} PRE breakpoint insertion failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} POST breakpoint insertion failed"
                )

            stop = False
            for attempt, schedule in enumerate(schedules):
                for tick, desired in enumerate(schedule, start=1):
                    consumed = b03._continue_to_pre_step(  # noqa: SLF001
                        remote,
                        pre_step=pre_step,
                        attempt=attempt,
                        expected_tick=tick,
                        desired=desired,
                        session_id=session_id,
                    )
                    observed.append(consumed)
                    b03._set_gameplay_mask(  # noqa: SLF001
                        window,
                        _next_desired(
                            schedules, attempt=attempt, tick=tick
                        ),
                        pressed,
                        events,
                    )
                    control, _input_state = (
                        b03._continue_to_post_step(  # noqa: SLF001
                            remote,
                            pre_step=pre_step,
                            post_step=post_step,
                            label=(
                                f"{session_id} post-step "
                                f"{attempt}/{tick}"
                            ),
                        )
                    )
                    _validate_post_step(
                        control=control,
                        attempt=attempt,
                        tick=tick,
                        desired=desired,
                    )
                    if attempt != spec.attempt or tick != spec.tick:
                        continue

                    transcript.append(
                        f"# {session_id} capture_protocol begin"
                    )
                    target_post_step = _capture_coherent_state(
                        remote,
                        label=f"{spec.name}_target_post_step",
                        symbols=symbols,
                    )
                    target_post_step_boundary = (
                        _capture_publication_boundary(
                            remote,
                            label=(
                                f"{spec.name}_target_post_step_boundary"
                            ),
                        )
                    )
                    if remote.request(f"z0,{pre_step:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{session_id} PRE removal at target failed"
                        )
                    if remote.request(f"z0,{post_step:x},2") != "OK":
                        raise EvidenceFailure(
                            f"{session_id} POST removal at target failed"
                        )
                    if (
                        remote.request(f"Z0,{renderer_return:x},2")
                        != "OK"
                    ):
                        raise EvidenceFailure(
                            f"{session_id} renderer-return insertion failed"
                        )
                    terminal_checkpoint_hold: dict[str, Any] | None = None
                    terminal_checkpoint_hold_manifest: (
                        dict[str, Any] | None
                    ) = None
                    terminal_checkpoint_hold_boundary: (
                        dict[str, Any] | None
                    ) = None
                    terminal_service_post: dict[str, Any] | None = None
                    terminal_service_post_manifest: (
                        dict[str, Any] | None
                    ) = None
                    terminal_service_post_boundary: (
                        dict[str, Any] | None
                    ) = None
                    if spec.name == "terminal_complete_20":
                        _require_stop_at(
                            remote,
                            renderer_return,
                            label=(
                                f"{session_id} terminal checkpoint hold"
                            ),
                        )
                        terminal_checkpoint_hold = _capture_coherent_state(
                            remote,
                            label=(
                                f"{spec.name}_terminal_checkpoint_hold"
                            ),
                            symbols=symbols,
                        )
                        terminal_checkpoint_hold_boundary = (
                            _capture_publication_boundary(
                                remote,
                                label=(
                                    f"{spec.name}_terminal_checkpoint_hold"
                                ),
                            )
                        )
                        if remote.request(
                            f"z0,{renderer_return:x},2"
                        ) != "OK":
                            raise EvidenceFailure(
                                f"{session_id} held renderer-return removal "
                                "failed"
                            )
                        if remote.request(f"Z0,{post_step:x},2") != "OK":
                            raise EvidenceFailure(
                                f"{session_id} terminal service POST "
                                "insertion failed"
                            )
                        _require_stop_at(
                            remote,
                            post_step,
                            label=f"{session_id} terminal service POST",
                        )
                        terminal_service_post = _capture_coherent_state(
                            remote,
                            label=f"{spec.name}_terminal_service_post",
                            symbols=symbols,
                        )
                        terminal_service_post_boundary = (
                            _capture_publication_boundary(
                                remote,
                                label=f"{spec.name}_terminal_service_post",
                            )
                        )
                        target_manifest_for_service = _state_manifest(
                            target_post_step
                        )
                        terminal_checkpoint_hold_manifest = _state_manifest(
                            terminal_checkpoint_hold
                        )
                        terminal_service_post_manifest = _state_manifest(
                            terminal_service_post
                        )
                        service_audit = audit_terminal_presentation_service(
                            spec,
                            target_post_step=target_manifest_for_service,
                            checkpoint_hold=(
                                terminal_checkpoint_hold_manifest
                            ),
                            service_post=terminal_service_post_manifest,
                            target_post_boundary=target_post_step_boundary,
                            checkpoint_hold_boundary=(
                                terminal_checkpoint_hold_boundary
                            ),
                            service_post_boundary=(
                                terminal_service_post_boundary
                            ),
                        )
                        if service_audit["status"] != "pass":
                            raise EvidenceFailure(
                                f"{session_id} terminal presentation "
                                f"service failed: {service_audit}"
                            )
                        if remote.request(f"z0,{post_step:x},2") != "OK":
                            raise EvidenceFailure(
                                f"{session_id} terminal service POST "
                                "removal failed"
                            )
                        if remote.request(
                            f"Z0,{renderer_return:x},2"
                        ) != "OK":
                            raise EvidenceFailure(
                                f"{session_id} terminal presentation "
                                "renderer-return insertion failed"
                            )
                        publication_before_renderer = (
                            terminal_service_post_boundary
                        )
                    else:
                        service_audit = audit_terminal_presentation_service(
                            spec,
                            target_post_step=_state_manifest(
                                target_post_step
                            ),
                            checkpoint_hold=None,
                            service_post=None,
                            target_post_boundary=target_post_step_boundary,
                            checkpoint_hold_boundary=None,
                            service_post_boundary=None,
                        )
                        publication_before_renderer = (
                            target_post_step_boundary
                        )
                    prearmed = _prearm_native_screenshot(
                        process_id=process.pid,
                        window=window,
                        screenshot_directory=screenshot_directory,
                        events=events,
                    )
                    _require_stop_at(
                        remote,
                        renderer_return,
                        label=f"{session_id} target renderer return",
                    )
                    target_post_renderer = _capture_coherent_state(
                        remote,
                        label=f"{spec.name}_target_post_renderer",
                        symbols=symbols,
                    )
                    publication_after_renderer = (
                        _capture_publication_boundary(
                            remote,
                            label=(
                                f"{spec.name}_target_renderer_boundary"
                            ),
                        )
                    )
                    target_manifest = _state_manifest(target_post_step)
                    prepared_manifest = _state_manifest(
                        target_post_renderer
                    )
                    if (
                        target_manifest["authoritative_bundle_sha256"]
                        != prepared_manifest[
                            "authoritative_bundle_sha256"
                        ]
                    ):
                        raise EvidenceFailure(
                            f"{session_id} authoritative state changed "
                            "inside renderer"
                        )
                    facts = capture_semantic_facts(
                        spec.name, prepared_manifest
                    )
                    if not all(facts.values()):
                        raise EvidenceFailure(
                            f"{session_id} target facts failed: {facts}"
                        )

                    native = _capture_native_frame(
                        remote=remote,
                        root=root,
                        process_id=process.pid,
                        window=window,
                        screenshot_directory=screenshot_directory,
                        output=staging_output,
                        spec=spec,
                        symbols=symbols,
                        post_step=post_step,
                        renderer_return=renderer_return,
                        dma_commit_entry=dma_commit_entry,
                        transcript=transcript,
                        events=events,
                        prearmed=prearmed,
                        target_post_renderer=target_post_renderer,
                        publication_before_renderer=(
                            publication_before_renderer
                        ),
                        publication_after_renderer=(
                            publication_after_renderer
                        ),
                    )
                    native["frame_binding"].update(
                        {
                            "target_post_step_boundary": (
                                target_post_step_boundary
                            ),
                            "terminal_checkpoint_hold_boundary": (
                                terminal_checkpoint_hold_boundary
                            ),
                            "terminal_service_post_boundary": (
                                terminal_service_post_boundary
                            ),
                            "terminal_presentation_service": service_audit,
                            "ui_edge_queued_at": (
                                "terminal_service_post_before_renderer"
                                if spec.name == "terminal_complete_20"
                                else "target_post_step_before_renderer"
                            ),
                        }
                    )
                    published_manifest = _state_manifest(
                        native["published"]
                    )
                    if (
                        prepared_manifest[
                            "authoritative_bundle_sha256"
                        ]
                        != published_manifest[
                            "authoritative_bundle_sha256"
                        ]
                        or prepared_manifest["raw_regions"][
                            "renderer_state"
                        ]["sha256"]
                        != published_manifest["raw_regions"][
                            "renderer_state"
                        ]["sha256"]
                        or prepared_manifest["raw_regions"][
                            "sat_shadow"
                        ]["sha256"]
                        != published_manifest["raw_regions"][
                            "sat_shadow"
                        ]["sha256"]
                    ):
                        raise EvidenceFailure(
                            f"{session_id} published state differs from "
                            "prepared state"
                        )
                    screenshot = native["screenshot"]
                    screenshot["staging_path"] = screenshot["path"]
                    screenshot["path"] = spec.output
                    screenshot["publication_verified"] = False
                    record = {
                        "name": spec.name,
                        "objective": spec.objective,
                        "target": {
                            "attempt": spec.attempt,
                            "tick": spec.tick,
                        },
                        "session": {
                            "session_id": session_id,
                            "process_id": process.pid,
                            "fresh_process": True,
                            "launch_flags": list(b03.LAUNCH_FLAGS),
                            "runtime_copy": runtime_copy,
                            "controller_profile": controller_profile,
                            "standard_three_button_pad": True,
                            "input_transport": "targeted_PostMessageW",
                            "input_events": events,
                        },
                        "input_drive": {
                            "source_module": (
                                "tools.m5_b03_runtime_evidence"
                            ),
                            "steps": len(observed),
                            "expected_sha256": _sha256_bytes(expected),
                            "observed_sha256": _sha256_bytes(
                                bytes(observed)
                            ),
                            "exact_match": bytes(observed) == expected,
                        },
                        "target_post_step": target_manifest,
                        "terminal_checkpoint_hold": (
                            terminal_checkpoint_hold_manifest
                        ),
                        "terminal_service_post": (
                            terminal_service_post_manifest
                        ),
                        "target_post_renderer": prepared_manifest,
                        "published_post_dma": published_manifest,
                        "ui_service_idle_boundaries": native[
                            "idle_boundaries"
                        ],
                        "successor_post_step": native[
                            "successor_post_step_manifest"
                        ],
                        "successor_post_renderer": native[
                            "successor_post_renderer_manifest"
                        ],
                        "png_precommit_state": native[
                            "png_precommit_manifest"
                        ],
                        "ui_service_successor": native[
                            "successor_manifest"
                        ],
                        "objective_state_facts": capture_semantic_facts(
                            spec.name, published_manifest
                        ),
                        "frame_binding": native["frame_binding"],
                        "screenshot": screenshot,
                    }
                    stop = True
                    break
                if stop:
                    break
            if record is None:
                raise EvidenceFailure(
                    f"{session_id} never reached target {spec.tick}"
                )
            if bytes(observed) != expected:
                raise EvidenceFailure(
                    f"{session_id} imported schedule prefix mismatched"
                )
        finally:
            if window is not None:
                for name, virtual_key, scan_code, extended, _bit in b03.KEYS:
                    if name in pressed:
                        try:
                            b03._post_key(  # noqa: SLF001
                                window,
                                (name, virtual_key, scan_code, extended),
                                pressed=False,
                            )
                            events.append(f"{name}:emergency-up")
                        except Exception as error:
                            transcript.append(
                                "# emergency key release failed: "
                                f"{type(error).__name__}: {error}"
                            )
            pressed.clear()
            b03._shutdown_owned_emulator(  # noqa: SLF001
                remote, process, transcript
            )
            transcript.append(f"# {session_id} end")

    if record is None:
        raise EvidenceFailure(f"{session_id} produced no record")
    session_lines = transcript[start_index:]
    writes = debugger_write_packets(session_lines)
    if writes:
        raise EvidenceFailure(
            f"{session_id} debugger write packets detected: {writes}"
        )
    capture_order = audit_capture_breakpoint_order(
        session_lines,
        pre_step=pre_step,
        post_step=post_step,
        renderer_return=renderer_return,
        dma_commit_entry=dma_commit_entry,
        event_order=record["frame_binding"]["event_order"],
    )
    if capture_order["status"] != "pass":
        raise EvidenceFailure(
            f"{session_id} capture breakpoint order failed: "
            f"{capture_order}"
        )
    if events.count("F1:ui.screenshot:down") != 1 or events.count(
        "F1:ui.screenshot:up"
    ) != 1:
        raise EvidenceFailure(
            f"{session_id} did not issue exactly one F1 edge"
        )
    record["gdb"] = {
        "transcript_sha256": _sha256_bytes(
            (
                "\n".join(session_lines)
                + ("\n" if session_lines else "")
            ).encode("utf-8")
        ),
        "commands": sum(line.startswith("> ") for line in session_lines),
        "guest_write_packets": writes,
        "guest_memory_register_pc_writes": False,
        "all_stops_exact": True,
        "capture_breakpoint_order": capture_order,
        "allowed_packets": [
            "Z0 software breakpoint insert",
            "z0 software breakpoint remove",
            "c continue",
            "m bounded guest-memory read",
            "p read-only register read",
        ],
    }
    return record


def _publish_screenshots(
    root: Path,
    staging: Path,
    captures: list[dict[str, Any]],
) -> None:
    for spec in CAPTURE_PLAN:
        record = next(
            item for item in captures if item["name"] == spec.name
        )
        source = staging / f"{spec.name}.png"
        destination = root / spec.output
        if not source.is_file():
            raise EvidenceFailure(
                f"staged screenshot is missing: {source}"
            )
        if sha256_file(source) != record["screenshot"]["sha256"]:
            raise EvidenceFailure(
                f"staged screenshot hash drifted: {spec.name}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.m5-visual-{os.getpid()}.tmp"
        )
        shutil.copy2(source, temporary)
        if sha256_file(temporary) != record["screenshot"]["sha256"]:
            temporary.unlink(missing_ok=True)
            raise EvidenceFailure(
                f"publication copy hash drifted: {spec.name}"
            )
        os.replace(temporary, destination)
        if sha256_file(destination) != record["screenshot"]["sha256"]:
            raise EvidenceFailure(
                f"published screenshot hash drifted: {spec.name}"
            )
        record["screenshot"]["publication_verified"] = True
        record["screenshot"]["published_mtime_ns"] = (
            destination.stat().st_mtime_ns
        )


def _failure_report(
    *,
    rom_sha256: str,
    symbol_sha256: str,
    error: Exception,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5/B03/native-visual-capture",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "acceptance_status": "incomplete",
        "rom_sha256": rom_sha256,
        "symbol_map_sha256": symbol_sha256,
        "error": f"{type(error).__name__}: {error}",
        "objective_only": {
            "subjective_scores_emitted": False,
            "rubric_review_performed": False,
            "critic_handoff_required": True,
        },
        "provenance": provenance,
    }


def _verify_unchanged(
    *,
    root: Path,
    rom: Path,
    symbol_path: Path,
    emulator: Path,
    default_config: Path,
    provenance: dict[str, Any],
) -> None:
    expectations = (
        (rom, provenance["rom"]["sha256"], "B03 Debug ROM"),
        (
            symbol_path,
            provenance["symbol_map"]["sha256"],
            "B03 symbol map",
        ),
        (
            emulator,
            provenance["emulator"]["sha256"],
            "pinned BlastEm",
        ),
        (
            default_config,
            provenance["default_config"]["source_sha256"],
            "pinned default.cfg",
        ),
    )
    for path, expected, description in expectations:
        if sha256_file(path) != expected:
            raise EvidenceFailure(
                f"{description} changed during visual capture"
            )
    if _source_bindings(root) != provenance["source_bindings"]:
        raise EvidenceFailure(
            "B03 renderer/source binding changed during visual capture"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", type=Path)
    parser.add_argument("--symbols", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-symbol-sha256", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()

    root = args.root.resolve()
    rom = (
        args.rom.resolve()
        if args.rom
        else root / "build/level-B03/recursion94-debug.bin"
    )
    symbol_path = (
        args.symbols.resolve()
        if args.symbols
        else root / "build/level-B03/recursion94-debug.sym"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m5-b03-visual.json"
    )
    trace_path = (
        args.trace.resolve()
        if args.trace
        else root / "artifacts/traces/m5-b03-visual-gdb-transcript.log"
    )
    emulator_directory = root / ".toolcache/blastem"
    emulator = emulator_directory / "blastem.exe"
    default_config = emulator_directory / "default.cfg"
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    captures: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    rom_sha256 = ""
    symbol_sha256 = ""
    report: dict[str, Any]
    staging: Path | None = None

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M5 visual evidence requires native Windows BlastEm"
            )
        if not 5.0 <= args.timeout_seconds <= 600.0:
            raise ValueError("--timeout-seconds must be within 5..600")
        if not 5.0 <= args.lock_timeout_seconds <= 600.0:
            raise ValueError(
                "--lock-timeout-seconds must be within 5..600"
            )
        expected_rom = args.expected_sha256.strip().lower()
        expected_symbols = args.expected_symbol_sha256.strip().lower()
        if not _valid_sha256(expected_rom):
            raise ValueError("--expected-sha256 must be lowercase SHA-256")
        if not _valid_sha256(expected_symbols):
            raise ValueError(
                "--expected-symbol-sha256 must be lowercase SHA-256"
            )
        output_pin = validate_requested_output_hashes(
            expected_rom, expected_symbols
        )
        for path, description in (
            (rom, "B03 Debug fixture ROM"),
            (symbol_path, "B03 Debug symbol map"),
            (emulator, "pinned BlastEm"),
            (default_config, "pinned default.cfg"),
        ):
            if not path.is_file():
                raise EvidenceFailure(f"{description} is missing: {path}")

        rom_sha256 = sha256_file(rom)
        symbol_sha256 = sha256_file(symbol_path)
        if rom_sha256 != expected_rom:
            raise EvidenceFailure(
                f"B03 ROM SHA-256 {rom_sha256} != {expected_rom}"
            )
        if symbol_sha256 != expected_symbols:
            raise EvidenceFailure(
                f"B03 symbol SHA-256 {symbol_sha256} != {expected_symbols}"
            )
        controller_bindings = verify_controller_bindings(default_config)
        if controller_bindings["status"] != "pass":
            raise EvidenceFailure(
                "pinned default.cfg lacks standard three-button bindings"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [
            symbol for symbol in REQUIRED_SYMBOLS if symbol not in symbols
        ]
        if missing:
            raise EvidenceFailure(
                f"B03 symbol map is missing required symbols {missing}"
            )
        if symbols["DEBUG_START_ENCOUNTER"] != b03.B03_DIRECTORY_INDEX:
            raise EvidenceFailure(
                "B03 visual ROM does not define DEBUG_START_ENCOUNTER=2"
            )
        rom_bytes = rom.read_bytes()
        renderer_call = derive_renderer_return_address(rom_bytes, symbols)
        dma_call = m4_visual.derive_post_dma_commit_address(
            rom_bytes, symbols
        )
        pre_step_binding = b03.verify_pre_step_rom_binding(
            rom, symbols
        )
        schedule = schedule_provenance()
        source_bindings = _source_bindings(root)
        user_config = verify_no_user_blastem_override()
        provenance = {
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
                "expected_sha256": expected_rom,
                "hash_policy": (
                    "mandatory caller-supplied hash, rechecked before publish"
                ),
            },
            "symbol_map": {
                "path": symbol_path.relative_to(root).as_posix(),
                "sha256": symbol_sha256,
                "expected_sha256": expected_symbols,
                "required_symbols": {
                    name: f"{symbols[name]:08X}"
                    for name in REQUIRED_SYMBOLS
                },
            },
            "reviewed_output_pin": output_pin,
            "emulator": {
                "path": emulator.relative_to(root).as_posix(),
                "sha256": sha256_file(emulator),
            },
            "default_config": {
                "path": default_config.relative_to(root).as_posix(),
                **controller_bindings,
            },
            "user_config_preflight": user_config,
            "source_bindings": source_bindings,
            "schedule": schedule,
            "pre_step_rom_binding": pre_step_binding,
            "renderer_call": renderer_call,
            "dma_commit_call": dma_call,
            "serialized_lock": lock_path.relative_to(root).as_posix(),
            "fresh_processes_required": len(CAPTURE_PLAN),
            "native_platform": "Windows",
        }

        staging_parent = root / "artifacts"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=".m5-b03-visual-staging-",
                dir=staging_parent,
            )
        )
        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            for spec in CAPTURE_PLAN:
                captures.append(
                    _run_capture_session(
                        root=root,
                        rom=rom,
                        symbol_path=symbol_path,
                        emulator_directory=emulator_directory,
                        default_config=default_config,
                        symbols=symbols,
                        spec=spec,
                        staging_output=staging / f"{spec.name}.png",
                        renderer_return=renderer_call["return_address"],
                        dma_commit_entry=dma_call["target_address"],
                        timeout_seconds=args.timeout_seconds,
                        transcript=transcript,
                    )
                )

        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        provenance["transcript"] = {
            "path": trace_path.relative_to(root).as_posix(),
            "sha256": sha256_file(trace_path),
            "commands": sum(
                line.startswith("> ") for line in transcript
            ),
            "lines": len(transcript),
            "guest_write_packets": debugger_write_packets(transcript),
        }
        if provenance["transcript"]["guest_write_packets"]:
            raise EvidenceFailure(
                "visual transcript contains guest write packets"
            )
        provenance["capture_protocol_audit"] = audit_visual_trace(
            transcript,
            captures,
            pre_step=symbols["MainLoopGameplayStepCall"],
            post_step=symbols["MainLoopPostGameplayStep"],
            renderer_return=renderer_call["return_address"],
            dma_commit_entry=dma_call["target_address"],
            vblank=symbols["VBlankInterrupt"],
            symbols=symbols,
        )
        if provenance["capture_protocol_audit"]["status"] != "pass":
            raise EvidenceFailure(
                "live visual transcript protocol audit failed: "
                f"{provenance['capture_protocol_audit']}"
            )
        _verify_unchanged(
            root=root,
            rom=rom,
            symbol_path=symbol_path,
            emulator=emulator,
            default_config=default_config,
            provenance=provenance,
        )
        preliminary = build_visual_report(
            rom_sha256=rom_sha256,
            symbol_sha256=symbol_sha256,
            symbols=symbols,
            captures=captures,
            provenance=provenance,
        )
        # Publication is intentionally the only preliminary failure: all
        # screenshots remain staged until every other objective gate passes.
        preliminary_failures = [
            check
            for check in preliminary["checks"]
            if check["status"] != "pass"
            and not check["check"].endswith("_decoded_png_integrity")
        ]
        if preliminary_failures:
            raise EvidenceFailure(
                "pre-publication visual gates failed: "
                f"{preliminary_failures}"
            )
        assert staging is not None
        staged_pngs = sorted(
            path.name for path in staging.glob("*.png") if path.is_file()
        )
        expected_pngs = sorted(
            f"{spec.name}.png" for spec in CAPTURE_PLAN
        )
        if staged_pngs != expected_pngs:
            raise EvidenceFailure(
                "staged screenshot set mismatch: "
                f"{staged_pngs} != {expected_pngs}"
            )
        _publish_screenshots(root, staging, captures)
        report = build_visual_report(
            rom_sha256=rom_sha256,
            symbol_sha256=symbol_sha256,
            symbols=symbols,
            captures=captures,
            provenance=provenance,
        )
        if report_exit_code(report):
            raise EvidenceFailure(
                "final objective visual report did not pass"
            )
    except Exception as error:
        try:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(
                "\n".join(transcript) + ("\n" if transcript else ""),
                encoding="utf-8",
                newline="\n",
            )
        except OSError:
            pass
        report = _failure_report(
            rom_sha256=rom_sha256,
            symbol_sha256=symbol_sha256,
            error=error,
            provenance=provenance,
        )
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    write_json(report_path, report)
    print(
        "M5 B03 native visual evidence: "
        f"{report['status']} ({report.get('acceptance_status')})"
    )
    if report.get("status") != "pass":
        print(report.get("error", "objective capture checks failed"))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
