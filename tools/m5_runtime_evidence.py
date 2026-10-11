#!/usr/bin/env python3
"""Collect the first live Milestone 5 campaign-runtime evidence slice.

Two independent portable BlastEm sessions boot the hash-bound Debug ROM.
Targeted window key messages exercise the configured standard three-button
pad. GDB is limited to breakpoint control and memory reads: this collector
never writes guest RAM, registers, or the program counter.

The bounded schedule proves campaign B01 initialization and deterministic
checkpoint publication. It deliberately does not claim stage completion,
the 24-step commit-terminal hold, multi-echo play, combat, rendering, audio,
performance, or Release parity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
    find_process_window,
    parse_vasm_symbols,
    post_virtual_key,
    require_gdb_port_available,
    require_stop,
    sha256_file,
    shutdown_emulator,
    verify_no_user_blastem_override,
    write_json,
    write_standard_pad_profile,
)
from tools.m3_runtime_evidence import (  # noqa: E402
    ACTOR_CAPACITY,
    ENGINE_CONTROL_USED_BYTES,
    INPUT_STATE_BASE,
    INPUT_STATE_BYTES,
    OBJECT_BYTES,
    POOL_LAYOUT,
    _read_region,
    crc32_iso_hdlc,
    debugger_write_packets,
    parse_engine_control,
    parse_input_state,
    verify_controller_bindings,
)


REPORT_SCHEMA = "r94-m5-runtime-evidence-v1"
EVIDENCE_TYPE = "blastem_gdb_m5_campaign_b01_initialization"
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"

ENGINE_CONTROL_BASE = 0x00FF1400
ENGINE_STATE_ACTIVE = 1
ENGINE_FLAG_LOADED = 0x0001
ENGINE_FLAG_PROJECTION_VALID = 0x0002
ENGINE_PROJECTION_BASE = 0x00FF6000
ENGINE_LEGACY_PROJECTION_BYTES = 3638
VOCAB_PROJECTION_BYTES = 256
CAMPAIGN_PROJECTION_BYTES = (
    ENGINE_LEGACY_PROJECTION_BYTES + VOCAB_PROJECTION_BYTES
)
CAMPAIGN_PROJECTION_VERSION = 2

VOCAB_STATE_BASE = 0x00FF1600
VOCAB_STATE_BYTES = 0x0200
VOCAB_MAGIC = 0x52393556
VOCAB_VERSION = 1
VOCAB_FLAG_INITIALIZED = 0x0001
VOCAB_CAMPAIGN_OFFSET = 0x00C8
VOCAB_CAMPAIGN_FLAG_ACTIVE = 0x0001
VOCAB_CAMPAIGN_FLAG_PROFILE_VALID = 0x0002
VOCAB_CAMPAIGN_FLAG_COMPLETE = 0x0004
VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR = 0x0008
VOCAB_DERIVED_OFFSET = 0x0100

ENGINE_STAGING_DESCRIPTOR = 0x00FF7000
ENCOUNTER_DESCRIPTOR_BYTES = 0x20
DEVICE_POOL = 0x00FF2600
DEVICE_CAPACITY = 32
CURRENT_ACTOR = 0x00FF18C0
ACTOR_BYTES = 0x20

PROJECTION_MAGIC = 0x52393450
PROJECTION_HEADER_BYTES = 0x28
PROJECTION_ACTOR_BYTES = 0x1E
PROJECTION_POOL_HEADER_BYTES = 4

B01_DIRECTORY_INDEX = 0
B01_STAGE_ID = 101
B01_ECHO_CAP = 2
B01_SEMANTIC_COUNT = 3
B01_GROUP_COUNT = 1
B01_EDGE_COUNT = 1
B01_COMPLETION_OBJECT = 2
B01_COMPLETION_FIELD = 1
B01_COMPLETION_FLAGS = 1
B01_COMPLETION_MASK = 0x0005
B01_COMPLETION_VALUE = 0x0005
B01_ROOM = (0, 0, 320, 224)
B01_SPAWN = (32, 112)
B01_RECTS = 6
B01_OBJECTS = 3
B01_DEVICES = (
    (0, 0x0000, 64, 112, 0, 0x0100),
    (1, 0x0000, 160, 96, 0, 0x0301),
    (2, 0x0000, 288, 112, 0, 0x0900),
)

SCHEDULE_FINAL_TICK = 120
RIGHT_FIRST_TICK = 5
RIGHT_LAST_TICK = 12
CAPTURE_TICKS = (1, 4, 5, 12, 13, 60, 120)
CHECKPOINT_TICKS = (0, 60, 120)
INPUT_RIGHT = 0x08
INPUT_B = 0x20

LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")
RIGHT_KEY = ("Right", 0x27, 0x4D, True)

REQUIRED_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopPostGameplayStep",
    "CampaignLoadEncounter",
    "CampaignEvaluateDependencies",
    "CampaignCheckCompletion",
    "GameplayBuildProjection",
    "CampaignRuntimeDescriptorB01",
    "CampaignRuntimeRectsB01",
    "CampaignRuntimeObjectsB01",
    "CampaignRuntimeProfileB01",
    "CampaignRuntimeSemanticsB01",
    "CampaignRuntimeDependencyGroupsB01",
    "CampaignRuntimeDependencyEdgesB01",
)

SOURCE_BINDING_PATHS = (
    "assets/generated/art/recursion94_art.bin",
    "assets/generated/art/recursion94_art.inc.asm",
    "assets/generated/campaign_runtime/campaign_runtime.inc.asm",
    "assets/generated/campaign_runtime/campaign_runtime_report.json",
    "assets/generated/content/campaign_content.bin",
    "assets/generated/content/campaign_content.inc.asm",
    "assets/generated/content/campaign_content.sha256",
    "assets/generated/content/campaign_content_report.json",
    "assets/generated/crc32/crc32_tables.bin",
    "assets/generated/crc32/crc32_tables.inc.asm",
    "assets/generated/crc32/crc32_tables.json",
    "assets/generated/z80_driver.bin",
    "content/campaign.json",
    "content/encounters/boot/B01.json",
    "content/encounters/boot/B02.json",
    "content/encounters/boot/B03.json",
    "content/encounters/boot/B04.json",
    "content/encounters/interrupt/I01.json",
    "content/encounters/interrupt/I02.json",
    "content/encounters/interrupt/I03.json",
    "content/encounters/interrupt/I04.json",
    "content/encounters/memory/M01.json",
    "content/encounters/memory/M02.json",
    "content/encounters/memory/M03.json",
    "content/encounters/memory/M04.json",
    "content/encounters/sound/S01.json",
    "content/encounters/sound/S02.json",
    "content/encounters/sound/S03.json",
    "content/encounters/sound/S04.json",
    "content/encounters/video/V01.json",
    "content/encounters/video/V02.json",
    "content/encounters/video/V03.json",
    "content/encounters/video/V04.json",
    "src/audio/z80_driver.asm",
    "src/boot/diagnostic.asm",
    "src/boot/exceptions.asm",
    "src/boot/header.asm",
    "src/boot/m2_diagnostic.asm",
    "src/boot/m3_diagnostic.asm",
    "src/boot/startup.asm",
    "src/boot/vectors.asm",
    "src/game/collision.asm",
    "src/game/crc32.asm",
    "src/game/encounters.asm",
    "src/game/engine.asm",
    "src/game/engine_core.asm",
    "src/game/engine_layout.asm",
    "src/game/object_pool.asm",
    "src/game/projection.asm",
    "src/gameplay/actor_sim.asm",
    "src/gameplay/campaign_collision.asm",
    "src/gameplay/campaign_projection.asm",
    "src/gameplay/campaign_runtime.asm",
    "src/gameplay/gameplay.asm",
    "src/gameplay/gameplay_core.asm",
    "src/gameplay/gameplay_layout.asm",
    "src/gameplay/replay.asm",
    "src/gameplay/vertical_slice.asm",
    "src/gameplay/vocabulary.asm",
    "src/gameplay/vocabulary_combat.asm",
    "src/gameplay/vocabulary_core.asm",
    "src/gameplay/vocabulary_interactions.asm",
    "src/gameplay/vocabulary_layout.asm",
    "src/hardware/audio_bridge.asm",
    "src/hardware/controllers.asm",
    "src/hardware/dma.asm",
    "src/hardware/interrupts.asm",
    "src/hardware/memory_map.asm",
    "src/hardware/sprites.asm",
    "src/hardware/vdp.asm",
    "src/hardware/z80_bus.asm",
    "src/main.asm",
    "src/render/gameplay_renderer.asm",
    "src/render/gameplay_renderer_assets.asm",
    "src/render/gameplay_renderer_core.asm",
    "src/render/gameplay_renderer_hud.asm",
    "src/render/gameplay_renderer_layout.asm",
    "src/render/gameplay_renderer_sprites.asm",
    "src/ui/timeline_control.asm",
)

ASSEMBLY_INPUT_PATTERN = re.compile(
    r'^\s*(include|incbin)\s+"([^"]+)"',
    re.IGNORECASE,
)


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def _s16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big", signed=True)


def _s32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big", signed=True)


def _hex32(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, tuple):
        return [_public(item) for item in value]
    return value


def parse_vocabulary_state(raw: bytes) -> dict[str, Any]:
    """Parse the projected vocabulary prefix and derived campaign pointers."""
    if len(raw) != VOCAB_STATE_BYTES:
        raise EvidenceFailure(
            f"vocabulary read is {len(raw)} bytes; {VOCAB_STATE_BYTES} required"
        )
    campaign = VOCAB_CAMPAIGN_OFFSET
    derived = VOCAB_DERIVED_OFFSET
    return {
        "magic": _u32(raw, 0x00),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 0x04),
        "bytes": _u16(raw, 0x06),
        "projection_length": _u16(raw, 0x08),
        "flags": _u16(raw, 0x0A),
        "result": _u16(raw, 0x0C),
        "overflow_flags": _u16(raw, 0x0E),
        "stage_id": _u16(raw, 0x16),
        "stage_flags": _u16(raw, 0x18),
        "campaign": {
            "flags": _u16(raw, campaign + 0x00),
            "directory_index": raw[campaign + 0x02],
            "echo_cap": raw[campaign + 0x03],
            "semantic_count": raw[campaign + 0x04],
            "group_count": raw[campaign + 0x05],
            "edge_count": raw[campaign + 0x06],
            "completion_object": raw[campaign + 0x07],
            "completion_field": raw[campaign + 0x08],
            "completion_flags": raw[campaign + 0x09],
            "completion_mask": _u16(raw, campaign + 0x0A),
            "completion_value": _u16(raw, campaign + 0x0C),
            "dependency_latches_hex": raw[
                campaign + 0x0E : campaign + 0x2E
            ].hex().upper(),
            "event_flags": _u16(raw, campaign + 0x2E),
            "last_interaction_actor": raw[campaign + 0x30],
            "last_interaction_object": raw[campaign + 0x31],
        },
        "derived": {
            "profile_ptr": _u32(raw, derived + 0x00),
            "semantic_ptr": _u32(raw, derived + 0x04),
            "group_ptr": _u32(raw, derived + 0x08),
            "edge_ptr": _u32(raw, derived + 0x0C),
        },
        "projected_prefix_sha256": _sha256_bytes(
            raw[:VOCAB_PROJECTION_BYTES]
        ),
        "full_state_sha256": _sha256_bytes(raw),
    }


def parse_descriptor(raw: bytes) -> dict[str, Any]:
    if len(raw) != ENCOUNTER_DESCRIPTOR_BYTES:
        raise EvidenceFailure(
            f"descriptor read is {len(raw)} bytes; "
            f"{ENCOUNTER_DESCRIPTOR_BYTES} required"
        )
    return {
        "id": _u16(raw, 0x00),
        "flags": _u16(raw, 0x02),
        "version": _u16(raw, 0x04),
        "bytes": _u16(raw, 0x06),
        "room": (
            _s16(raw, 0x08),
            _s16(raw, 0x0A),
            _s16(raw, 0x0C),
            _s16(raw, 0x0E),
        ),
        "spawn": (_s16(raw, 0x10), _s16(raw, 0x12)),
        "rect_count": _u16(raw, 0x14),
        "object_count": _u16(raw, 0x16),
        "rect_pointer": _u32(raw, 0x18),
        "object_pointer": _u32(raw, 0x1C),
        "sha256": _sha256_bytes(raw),
    }


def parse_object(raw: bytes, slot: int) -> dict[str, Any]:
    if len(raw) != OBJECT_BYTES:
        raise EvidenceFailure(
            f"object record is {len(raw)} bytes; {OBJECT_BYTES} required"
        )
    return {
        "slot": slot,
        "active": raw[0],
        "logical_id": raw[1],
        "state": _u16(raw, 0x02),
        "x_16_8": _s32(raw, 0x04),
        "y_16_8": _s32(raw, 0x08),
        "vx_16_8": _s32(raw, 0x0C),
        "vy_16_8": _s32(raw, 0x10),
        "timer": _u16(raw, 0x14),
        "param": _u16(raw, 0x16),
    }


def parse_devices(raw: bytes) -> list[dict[str, Any]]:
    if len(raw) != DEVICE_CAPACITY * OBJECT_BYTES:
        raise EvidenceFailure(
            f"device-pool read is {len(raw)} bytes; "
            f"{DEVICE_CAPACITY * OBJECT_BYTES} required"
        )
    return [
        parse_object(
            raw[slot * OBJECT_BYTES : (slot + 1) * OBJECT_BYTES],
            slot,
        )
        for slot in range(DEVICE_CAPACITY)
    ]


def parse_actor(raw: bytes) -> dict[str, Any]:
    if len(raw) != ACTOR_BYTES:
        raise EvidenceFailure(
            f"actor read is {len(raw)} bytes; {ACTOR_BYTES} required"
        )
    return {
        "active": raw[0],
        "role": raw[1],
        "flags": _u16(raw, 0x02),
        "x_16_8": _s32(raw, 0x04),
        "y_16_8": _s32(raw, 0x08),
        "vx_16_8": _s32(raw, 0x0C),
        "vy_16_8": _s32(raw, 0x10),
        "half_width": _u16(raw, 0x14),
        "half_height": _u16(raw, 0x16),
        "input": raw[0x18],
        "previous_input": raw[0x19],
        "health": _u16(raw, 0x1A),
        "logical_id": _u16(raw, 0x1C),
        "reserved": _u16(raw, 0x1E),
        "sha256": _sha256_bytes(raw),
    }


def parse_extended_projection(
    raw: bytes,
    *,
    live_vocabulary_prefix: bytes | None = None,
) -> dict[str, Any]:
    """Validate the legacy body plus the contiguous 256-byte M5 tail."""
    if len(raw) != CAMPAIGN_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"projection is {len(raw)} bytes; "
            f"{CAMPAIGN_PROJECTION_BYTES} required"
        )
    magic = _u32(raw, 0x00)
    version = _u16(raw, 0x04)
    declared = _u16(raw, 0x06)
    if magic != PROJECTION_MAGIC:
        raise EvidenceFailure(
            f"projection magic is ${magic:08X}; ${PROJECTION_MAGIC:08X} required"
        )
    if version != CAMPAIGN_PROJECTION_VERSION:
        raise EvidenceFailure(
            f"projection schema is {version}; "
            f"{CAMPAIGN_PROJECTION_VERSION} required"
        )
    if declared != CAMPAIGN_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"projection declares {declared}; "
            f"{CAMPAIGN_PROJECTION_BYTES} required"
        )

    header = {
        "magic": magic,
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "schema_version": version,
        "bytes": declared,
        "encounter": _u16(raw, 0x08),
        "attempt": _u16(raw, 0x0A),
        "tick": _u16(raw, 0x0C),
        "state": _u16(raw, 0x0E),
        "terminal_reason": _u16(raw, 0x10),
        "flags": _u16(raw, 0x12),
        "overflow_flags": _u16(raw, 0x14),
        "last_error": _u16(raw, 0x16),
        "room": (
            _s16(raw, 0x18),
            _s16(raw, 0x1A),
            _s16(raw, 0x1C),
            _s16(raw, 0x1E),
        ),
        "input_held": raw[0x20],
        "input_pressed": raw[0x21],
        "input_released": raw[0x22],
        "input_previous": raw[0x23],
        "facing_x": int.from_bytes(raw[0x24:0x25], "big", signed=True),
        "active_actor_count": raw[0x25],
        "rect_count": _u16(raw, 0x26),
    }

    cursor = PROJECTION_HEADER_BYTES
    actor_active = []
    for _slot in range(ACTOR_CAPACITY):
        actor_active.append(raw[cursor])
        cursor += PROJECTION_ACTOR_BYTES

    pools: list[dict[str, Any]] = []
    bounds_valid = all(value in (0, 1) for value in actor_active)
    for pool_type, name, _address, capacity in POOL_LAYOUT:
        observed_type = raw[cursor]
        observed_capacity = raw[cursor + 1]
        active_count = raw[cursor + 2]
        high_water = raw[cursor + 3]
        cursor += PROJECTION_POOL_HEADER_BYTES
        active_values: list[int] = []
        active_ids: list[int] = []
        for _slot in range(capacity):
            active = raw[cursor]
            logical_id = raw[cursor + 1]
            active_values.append(active)
            if active == 1:
                active_ids.append(logical_id)
            cursor += OBJECT_BYTES
        observed_count = sum(value == 1 for value in active_values)
        pool_valid = (
            observed_type == pool_type
            and observed_capacity == capacity
            and all(value in (0, 1) for value in active_values)
            and active_count == observed_count
            and high_water <= capacity
            and len(active_ids) == len(set(active_ids))
        )
        bounds_valid = bounds_valid and pool_valid
        pools.append(
            {
                "type": pool_type,
                "name": name,
                "capacity": capacity,
                "active_count": active_count,
                "observed_active_count": observed_count,
                "high_water": high_water,
                "active_logical_ids": active_ids,
                "valid": pool_valid,
            }
        )
    if cursor != ENGINE_LEGACY_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"legacy projection body ended at {cursor}; "
            f"{ENGINE_LEGACY_PROJECTION_BYTES} required"
        )
    bounds_valid = (
        bounds_valid
        and sum(value == 1 for value in actor_active)
        == header["active_actor_count"]
    )
    tail = raw[cursor:]
    tail_matches = (
        None
        if live_vocabulary_prefix is None
        else tail == live_vocabulary_prefix
    )
    return {
        "header": header,
        "actor_active_values": actor_active,
        "pools": pools,
        "bounds_valid": bounds_valid,
        "legacy_body_bytes": cursor,
        "tail_bytes": len(tail),
        "tail_sha256": _sha256_bytes(tail),
        "tail_matches_live_vocabulary": tail_matches,
        "crc32_iso_hdlc": crc32_iso_hdlc(raw),
        "crc32_iso_hdlc_hex": _hex32(crc32_iso_hdlc(raw)),
        "sha256": _sha256_bytes(raw),
    }


def make_snapshot(
    *,
    label: str,
    control_raw: bytes,
    vocabulary_raw: bytes,
    descriptor_raw: bytes,
    device_raw: bytes,
    actor_raw: bytes,
    input_raw: bytes,
    projection_raw: bytes,
    symbols: dict[str, int],
) -> dict[str, Any]:
    control = parse_engine_control(control_raw)
    vocabulary = parse_vocabulary_state(vocabulary_raw)
    descriptor = parse_descriptor(descriptor_raw)
    devices = parse_devices(device_raw)
    actor = parse_actor(actor_raw)
    input_state = parse_input_state(input_raw)
    projection = parse_extended_projection(
        projection_raw,
        live_vocabulary_prefix=vocabulary_raw[:VOCAB_PROJECTION_BYTES],
    )
    derived = vocabulary["derived"]
    pointer_matches = {
        "profile": derived["profile_ptr"]
        == symbols["CampaignRuntimeProfileB01"],
        "semantics": derived["semantic_ptr"]
        == symbols["CampaignRuntimeSemanticsB01"],
        "groups": derived["group_ptr"]
        == symbols["CampaignRuntimeDependencyGroupsB01"],
        "edges": derived["edge_ptr"]
        == symbols["CampaignRuntimeDependencyEdgesB01"],
        "rects": descriptor["rect_pointer"]
        == symbols["CampaignRuntimeRectsB01"],
        "objects": descriptor["object_pointer"]
        == symbols["CampaignRuntimeObjectsB01"],
    }
    return {
        "label": label,
        "engine": control,
        "vocabulary": vocabulary,
        "descriptor": descriptor,
        "devices": devices,
        "current_actor": actor,
        "input": input_state,
        "projection": {
            **projection,
            "published_crc32_iso_hdlc_hex": _hex32(
                control["state_crc"]
            ),
            "published_crc_matches": (
                projection["crc32_iso_hdlc"] == control["state_crc"]
            ),
        },
        "pointer_matches": pointer_matches,
        "_control_bytes": bytes(control_raw),
        "_vocabulary_bytes": bytes(vocabulary_raw),
        "_descriptor_bytes": bytes(descriptor_raw),
        "_device_bytes": bytes(device_raw),
        "_actor_bytes": bytes(actor_raw),
        "_input_bytes": bytes(input_raw),
        "_projection_bytes": bytes(projection_raw),
    }


def expected_held(tick: int) -> int:
    return INPUT_RIGHT if RIGHT_FIRST_TICK <= tick <= RIGHT_LAST_TICK else 0


def expected_pressed(tick: int) -> int:
    return INPUT_RIGHT if tick == RIGHT_FIRST_TICK else 0


def expected_released(tick: int) -> int:
    return INPUT_RIGHT if tick == RIGHT_LAST_TICK + 1 else 0


def _capture_snapshot(
    remote: GdbRemote,
    *,
    label: str,
    symbols: dict[str, int],
) -> dict[str, Any]:
    control_raw = _read_region(
        remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
    )
    control = parse_engine_control(control_raw)
    if control["projection_length"] != CAMPAIGN_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"{label} projection length is {control['projection_length']}; "
            f"{CAMPAIGN_PROJECTION_BYTES} required"
        )
    return make_snapshot(
        label=label,
        control_raw=control_raw,
        vocabulary_raw=_read_region(
            remote, VOCAB_STATE_BASE, VOCAB_STATE_BYTES
        ),
        descriptor_raw=_read_region(
            remote,
            ENGINE_STAGING_DESCRIPTOR,
            ENCOUNTER_DESCRIPTOR_BYTES,
        ),
        device_raw=_read_region(
            remote, DEVICE_POOL, DEVICE_CAPACITY * OBJECT_BYTES
        ),
        actor_raw=_read_region(remote, CURRENT_ACTOR, ACTOR_BYTES),
        input_raw=_read_region(
            remote, INPUT_STATE_BASE, INPUT_STATE_BYTES
        ),
        projection_raw=_read_region(
            remote, ENGINE_PROJECTION_BASE, CAMPAIGN_PROJECTION_BYTES
        ),
        symbols=symbols,
    )


def _post_right(window: int, *, pressed: bool) -> None:
    _name, virtual_key, scan_code, extended = RIGHT_KEY
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=pressed,
        extended=extended,
    )


def _resolve_assembly_input(
    root: Path,
    *,
    owner: Path,
    operand: str,
) -> Path:
    normalized = Path(operand.replace("\\", "/"))
    candidates = (
        root / normalized,
        root / "src" / normalized,
        owner.parent / normalized,
    )
    matches: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved not in matches:
            matches.append(resolved)
    if len(matches) != 1:
        relative_owner = owner.relative_to(root).as_posix()
        raise EvidenceFailure(
            f"{relative_owner} assembly input {operand!r} resolved to "
            f"{len(matches)} files"
        )
    try:
        matches[0].relative_to(root)
    except ValueError as error:
        raise EvidenceFailure(
            f"assembly input {operand!r} escapes the repository"
        ) from error
    return matches[0]


def _discover_translation_unit_inputs(root: Path) -> tuple[str, ...]:
    """Return every include/incbin input reachable from ``src/main.asm``."""
    pending = [(root / "src/main.asm").resolve()]
    discovered: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in discovered:
            continue
        if not source.is_file():
            raise EvidenceFailure(
                f"assembly input is missing: {source.as_posix()}"
            )
        discovered.add(source)
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise EvidenceFailure(
                f"included assembly is not UTF-8: {source.as_posix()}"
            ) from error
        for line in lines:
            match = ASSEMBLY_INPUT_PATTERN.match(line)
            if match is None:
                continue
            target = _resolve_assembly_input(
                root,
                owner=source,
                operand=match.group(2),
            )
            if match.group(1).lower() == "include":
                pending.append(target)
            else:
                discovered.add(target)
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in discovered)
    )


def _source_bindings(root: Path) -> dict[str, Any]:
    if len(SOURCE_BINDING_PATHS) != len(set(SOURCE_BINDING_PATHS)):
        raise EvidenceFailure("M5 source binding contains duplicate paths")
    translation_unit_inputs = _discover_translation_unit_inputs(root)
    missing_inputs = sorted(
        set(translation_unit_inputs) - set(SOURCE_BINDING_PATHS)
    )
    if missing_inputs:
        raise EvidenceFailure(
            "M5 source binding omits translation-unit inputs "
            f"{missing_inputs}"
        )
    sources: dict[str, str] = {}
    for relative in SOURCE_BINDING_PATHS:
        path = root / relative
        if not path.is_file():
            raise EvidenceFailure(f"M5 evidence binding is missing {path}")
        sources[relative] = sha256_file(path)
    canonical = json.dumps(
        sources,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return {
        "bundle_sha256": hashlib.sha256(canonical).hexdigest(),
        "sources": sources,
        "translation_unit_inputs": list(translation_unit_inputs),
        "policy": (
            "all transitive include/incbin inputs from src/main.asm, "
            "authoritative campaign manifests, generated campaign metadata, "
            "and the Z80 production source"
        ),
        "presentation_source_exclusion": (
            "raw assets/source art and map manifests are pure presentation; "
            "their compiled ROM include and binary remain hash-bound"
        ),
    }


def _session_checkpoint(
    session: dict[str, Any], tick: int
) -> dict[str, Any]:
    if tick == 0:
        return session["initial"]
    return session["captures"][str(tick)]


def _initialization_ok(snapshot: dict[str, Any]) -> bool:
    engine = snapshot["engine"]
    vocabulary = snapshot["vocabulary"]
    campaign = vocabulary["campaign"]
    descriptor = snapshot["descriptor"]
    devices = snapshot["devices"]
    actor = snapshot["current_actor"]
    projection = snapshot["projection"]
    projection_header = projection["header"]
    device_projection = next(
        pool for pool in projection["pools"] if pool["name"] == "device"
    )
    expected_devices = [
        {
            "active": 1,
            "logical_id": logical_id,
            "state": state,
            "x_16_8": x << 8,
            "y_16_8": y << 8,
            "timer": timer,
            "param": param,
        }
        for logical_id, state, x, y, timer, param in B01_DEVICES
    ]
    observed_devices = [
        {
            key: devices[index][key]
            for key in (
                "active",
                "logical_id",
                "state",
                "x_16_8",
                "y_16_8",
                "timer",
                "param",
            )
        }
        for index in range(len(B01_DEVICES))
    ]
    inactive_tail = all(
        item["active"] == 0 for item in devices[len(B01_DEVICES) :]
    )
    return (
        engine["state"] == ENGINE_STATE_ACTIVE
        and (engine["flags"] & (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID))
        == (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID)
        and engine["encounter"] == B01_DIRECTORY_INDEX
        and engine["attempt"] == 0
        and engine["tick"] == 0
        and engine["last_error"] == 0
        and engine["overflow_flags"] == 0
        and (
            engine["room_left"],
            engine["room_top"],
            engine["room_right"],
            engine["room_bottom"],
        )
        == B01_ROOM
        and (engine["spawn_x"], engine["spawn_y"])
        == (B01_SPAWN[0] << 8, B01_SPAWN[1] << 8)
        and engine["rect_count"] == B01_RECTS
        and engine["object_init_count"] == B01_OBJECTS
        and engine["pool_counts"] == [0, 0, 3, 0, 0, 0, 0]
        and vocabulary["magic"] == VOCAB_MAGIC
        and vocabulary["version"] == VOCAB_VERSION
        and vocabulary["bytes"] == VOCAB_STATE_BYTES
        and vocabulary["projection_length"] == VOCAB_PROJECTION_BYTES
        and (vocabulary["flags"] & VOCAB_FLAG_INITIALIZED)
        == VOCAB_FLAG_INITIALIZED
        and vocabulary["result"] == 0
        and vocabulary["overflow_flags"] == 0
        and vocabulary["stage_id"] == B01_STAGE_ID
        and campaign["flags"]
        == (VOCAB_CAMPAIGN_FLAG_ACTIVE | VOCAB_CAMPAIGN_FLAG_PROFILE_VALID)
        and campaign["directory_index"] == B01_DIRECTORY_INDEX
        and campaign["echo_cap"] == B01_ECHO_CAP
        and campaign["semantic_count"] == B01_SEMANTIC_COUNT
        and campaign["group_count"] == B01_GROUP_COUNT
        and campaign["edge_count"] == B01_EDGE_COUNT
        and campaign["completion_object"] == B01_COMPLETION_OBJECT
        and campaign["completion_field"] == B01_COMPLETION_FIELD
        and campaign["completion_mask"] == B01_COMPLETION_MASK
        and campaign["completion_value"] == B01_COMPLETION_VALUE
        and campaign["completion_flags"] == B01_COMPLETION_FLAGS
        and campaign["dependency_latches_hex"] == "00" * 32
        and campaign["event_flags"] == 0
        and campaign["last_interaction_actor"] == 0xFF
        and campaign["last_interaction_object"] == 0xFF
        and descriptor["id"] == B01_DIRECTORY_INDEX
        and descriptor["flags"] == 0
        and descriptor["version"] == 1
        and descriptor["bytes"] == ENCOUNTER_DESCRIPTOR_BYTES
        and tuple(descriptor["room"]) == B01_ROOM
        and tuple(descriptor["spawn"]) == B01_SPAWN
        and descriptor["rect_count"] == B01_RECTS
        and descriptor["object_count"] == B01_OBJECTS
        and all(snapshot["pointer_matches"].values())
        and observed_devices == expected_devices
        and inactive_tail
        and actor["active"] == 1
        and actor["role"] == 6
        and actor["flags"] == 0
        and actor["x_16_8"] == B01_SPAWN[0] << 8
        and actor["y_16_8"] == B01_SPAWN[1] << 8
        and actor["vx_16_8"] == 0
        and actor["vy_16_8"] == 0
        and actor["half_width"] == 4
        and actor["half_height"] == 4
        and actor["input"] == 0
        and actor["previous_input"] == 0
        and actor["health"] == 1
        and actor["logical_id"] == 6
        and actor["reserved"] == 0
        and projection_header["schema_version"]
        == CAMPAIGN_PROJECTION_VERSION
        and projection_header["bytes"] == CAMPAIGN_PROJECTION_BYTES
        and projection_header["encounter"] == B01_DIRECTORY_INDEX
        and projection_header["attempt"] == 0
        and projection_header["tick"] == 0
        and projection_header["state"] == ENGINE_STATE_ACTIVE
        and projection_header["terminal_reason"] == 0
        and (
            projection_header["flags"]
            & (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID)
        )
        == (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID)
        and projection_header["overflow_flags"] == 0
        and projection_header["last_error"] == 0
        and tuple(projection_header["room"]) == B01_ROOM
        and projection_header["active_actor_count"] == 1
        and projection_header["rect_count"] == B01_RECTS
        and device_projection["active_count"] == B01_OBJECTS
        and device_projection["high_water"] == B01_OBJECTS
        and device_projection["active_logical_ids"] == [0, 1, 2]
        and projection["bounds_valid"]
        and projection["tail_matches_live_vocabulary"] is True
        and projection["published_crc_matches"]
    )


def build_runtime_report(
    *,
    rom_sha256: str,
    symbol_sha256: str,
    sessions: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Purely evaluate two collected sessions and make an honest report."""
    if len(sessions) != 2:
        raise EvidenceFailure(
            f"exactly two sessions are required; got {len(sessions)}"
        )
    transcript = [
        line
        for session in sessions
        for line in session.get("_transcript_lines", [])
    ]
    write_packets = debugger_write_packets(transcript)
    checks: list[dict[str, Any]] = []

    checks.append(
        _check(
            "two_fresh_independent_sessions",
            len({session["metadata"]["process_id"] for session in sessions})
            == 2
            and all(
                session["metadata"]["runtime_copy"].get("status") == "pass"
                and session["metadata"]["runtime_copy"].get(
                    "temporary_copy_removed_after_session"
                )
                is True
                for session in sessions
            )
            and len(
                {
                    session["metadata"]["runtime_copy"][
                        "temporary_directory"
                    ]
                    for session in sessions
                }
            )
            == 2,
            "process IDs "
            f"{[session['metadata']['process_id'] for session in sessions]}",
        )
    )
    checks.append(
        _check(
            "read_only_debugger",
            not write_packets,
            f"guest write packets {write_packets}",
        )
    )
    checks.append(
        _check(
            "isolated_standard_pad_and_window_input",
            all(
                session["metadata"]["controller_profile"].get("status")
                == "pass"
                and session["metadata"]["controller_profile"].get(
                    "port_1_assignment"
                )
                == "gamepad3.1"
                and session["metadata"]["controller_profile"]
                .get("session_isolation", {})
                .get("status")
                == "pass"
                and session["metadata"].get("input_transport")
                == "targeted_PostMessageW"
                for session in sessions
            ),
            "both sessions used isolated gamepad3.1 profiles and "
            "targeted Right key messages",
        )
    )
    checks.append(
        _check(
            "symbol_bound_observation",
            all(
                session["metadata"]["initial_breakpoint_symbol"]
                == "VBlankInterrupt"
                and session["metadata"]["initial_breakpoint_role"]
                == STARTUP_PRIMING_ROLE
                and session["metadata"]["authoritative_origin"]
                == FIRST_RUNNING_VBLANK_ROLE
                and session["metadata"]["step_breakpoint_symbol"]
                == "MainLoopPostGameplayStep"
                and session["metadata"]["symbol_map_sha256"]
                == symbol_sha256
                and session["metadata"]["launch_flags"]
                == list(LAUNCH_FLAGS)
                for session in sessions
            ),
            "initialization was captured at the priming VBlank and movement "
            "at the post-gameplay symbol",
        )
    )
    checks.append(
        _check(
            "b01_campaign_abi_v3_initialized",
            all(_initialization_ok(session["initial"]) for session in sessions),
            "both initial snapshots require ACTIVE|PROFILE_VALID, directory 0, "
            "echo cap 2, counts 3/1/1, canonical descriptor/devices, and "
            "schema-v2 extended projection CRC",
        )
    )

    schedule_ok = all(
        len(session["observed_inputs"]) == SCHEDULE_FINAL_TICK
        and all(
            observation["tick"] == tick
            and observation["held"] == expected_held(tick)
            and observation["pressed"] == expected_pressed(tick)
            and observation["released"] == expected_released(tick)
            and observation["connected"] == 1
            and observation["start_held"] == 0
            for tick, observation in enumerate(
                session["observed_inputs"],
                start=1,
            )
        )
        for session in sessions
    )
    checks.append(
        _check(
            "bounded_real_pad_schedule_observed",
            schedule_ok,
            f"neutral except exact Right press/hold/release on ticks "
            f"{RIGHT_FIRST_TICK}..{RIGHT_LAST_TICK}; "
            f"{SCHEDULE_FINAL_TICK} authoritative steps; no B input",
        )
    )

    edges_ok = all(
        (
            session["captures"]["5"]["input"]["pressed"] & INPUT_RIGHT
        )
        == INPUT_RIGHT
        and (
            session["observed_inputs"][5]["pressed"] & INPUT_RIGHT
        )
        == 0
        and (
            session["captures"]["13"]["input"]["released"] & INPUT_RIGHT
        )
        == INPUT_RIGHT
        for session in sessions
    )
    checks.append(
        _check(
            "right_press_hold_release_edges",
            edges_ok,
            "Right pressed at tick 5, held without repeated edges, and "
            "released at tick 13",
        )
    )

    movement_ok = all(
        session["captures"]["12"]["current_actor"]["x_16_8"]
        > session["captures"]["4"]["current_actor"]["x_16_8"]
        and session["captures"]["13"]["current_actor"]["x_16_8"]
        == session["captures"]["12"]["current_actor"]["x_16_8"]
        and session["captures"]["120"]["current_actor"]["x_16_8"]
        == session["captures"]["13"]["current_actor"]["x_16_8"]
        for session in sessions
    )
    checks.append(
        _check(
            "guest_simulated_movement_then_neutral",
            movement_ok,
            "current actor moves only during the eight-frame Right hold",
        )
    )

    checkpoint_integrity = all(
        all(
            (
                snapshot := _session_checkpoint(session, tick)
            )["engine"]["tick"]
            == tick
            and snapshot["engine"]["state"] == ENGINE_STATE_ACTIVE
            and snapshot["engine"]["last_error"] == 0
            and snapshot["engine"]["overflow_flags"] == 0
            and snapshot["engine"]["projection_length"]
            == CAMPAIGN_PROJECTION_BYTES
            and snapshot["projection"]["header"]["tick"] == tick
            and snapshot["projection"]["tail_matches_live_vocabulary"]
            is True
            and snapshot["projection"]["published_crc_matches"]
            and snapshot["projection"]["bounds_valid"]
            for tick in CHECKPOINT_TICKS
        )
        for session in sessions
    )
    checks.append(
        _check(
            "extended_projection_checkpoints_valid",
            checkpoint_integrity,
            "ticks 0/60/120 publish schema 2, 3894 bytes, exact vocabulary "
            "tail, bounded pools, and an independently recomputed CRC",
        )
    )

    matching_checkpoints = all(
        _session_checkpoint(sessions[0], tick)["_projection_bytes"]
        == _session_checkpoint(sessions[1], tick)["_projection_bytes"]
        and _session_checkpoint(sessions[0], tick)["_control_bytes"]
        == _session_checkpoint(sessions[1], tick)["_control_bytes"]
        and _session_checkpoint(sessions[0], tick)["_vocabulary_bytes"]
        == _session_checkpoint(sessions[1], tick)["_vocabulary_bytes"]
        and _session_checkpoint(sessions[0], tick)["_device_bytes"]
        == _session_checkpoint(sessions[1], tick)["_device_bytes"]
        and _session_checkpoint(sessions[0], tick)["_actor_bytes"]
        == _session_checkpoint(sessions[1], tick)["_actor_bytes"]
        for tick in CHECKPOINT_TICKS
    )
    checks.append(
        _check(
            "two_session_checkpoint_bytes_match",
            matching_checkpoints,
            "engine, vocabulary, devices, current actor, and complete "
            "projection bytes match at ticks 0/60/120",
        )
    )

    no_completion = all(
        not (
            session["captures"]["120"]["vocabulary"]["campaign"]["flags"]
            & (
                VOCAB_CAMPAIGN_FLAG_COMPLETE
                | VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR
            )
        )
        and session["captures"]["120"]["engine"]["state"]
        == ENGINE_STATE_ACTIVE
        for session in sessions
    )
    checks.append(
        _check(
            "slice_remains_active_without_false_completion",
            no_completion,
            "the bounded movement schedule does not claim or manufacture "
            "PROGRAM_STABLE",
        )
    )

    passed = all(check["status"] == "pass" for check in checks)
    checkpoint_summary = []
    for tick in CHECKPOINT_TICKS:
        left = _session_checkpoint(sessions[0], tick)
        right = _session_checkpoint(sessions[1], tick)
        checkpoint_summary.append(
            {
                "tick": tick,
                "session_1_crc32": _hex32(
                    left["engine"]["state_crc"]
                ),
                "session_2_crc32": _hex32(
                    right["engine"]["state_crc"]
                ),
                "projection_sha256": left["projection"]["sha256"],
                "bytes_match": (
                    left["_projection_bytes"] == right["_projection_bytes"]
                ),
            }
        )
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "symbol_map_sha256": symbol_sha256,
        "checks": checks,
        "schedule": {
            "authoritative_steps": SCHEDULE_FINAL_TICK,
            "right_held_ticks_inclusive": [
                RIGHT_FIRST_TICK,
                RIGHT_LAST_TICK,
            ],
            "b_held_pressed_released": False,
            "capture_ticks": list(CAPTURE_TICKS),
            "checkpoint_ticks": list(CHECKPOINT_TICKS),
            "input_delivery": "targeted window key messages",
            "guest_memory_register_pc_writes": False,
        },
        "checkpoint_comparison": checkpoint_summary,
        "sessions": [_public(session) for session in sessions],
        "provenance": provenance,
        "debugger_write_packets": write_packets,
        "evidence_limits": [
            "This slice proves live Debug-ROM B01 campaign initialization, "
            "canonical descriptor/device reconstruction, ABI-v3 bindings, "
            "extended projection integrity, and deterministic checkpoints.",
            "Input is delivered through an isolated standard three-button "
            "pad profile and targeted window messages; GDB performs reads and "
            "breakpoint control only.",
            "The schedule intentionally does not complete B01 and makes no "
            "commit-terminal hold, multi-echo, replacement, combat, dash, "
            "carryable, beam, boss, visual, audio, performance, PAL, save, "
            "or Release-ROM claim.",
        ],
    }


def _run_live_session(
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
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    right_pressed = False
    input_events: list[str] = []
    session: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix=f"recursion94-m5-{session_id}-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        profile_sha_before_paths = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            profile_sha_before_paths
        )
        controller_profile["sha256"] = controller_profile[
            "session_isolation"
        ]["config_sha256"]
        controller_profile["runtime_copy"] = runtime_copy
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
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            initial = _capture_snapshot(
                remote,
                label="campaign_b01_attempt_0_tick_0",
                symbols=symbols,
            )
            if initial["engine"]["attempt"] != 0 or initial["engine"]["tick"] != 0:
                raise EvidenceFailure(
                    f"{session_id} initial attempt/tick is "
                    f"{initial['engine']['attempt']}/"
                    f"{initial['engine']['tick']}; 0/0 required"
                )
            window = find_process_window(process.pid, 20.0)
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-gameplay breakpoint insertion failed"
                )

            captures: dict[str, dict[str, Any]] = {}
            observations: list[dict[str, int]] = []
            for expected_tick in range(1, SCHEDULE_FINAL_TICK + 1):
                require_stop(remote.request("c"))
                control = parse_engine_control(
                    _read_region(
                        remote,
                        ENGINE_CONTROL_BASE,
                        ENGINE_CONTROL_USED_BYTES,
                    )
                )
                input_state = parse_input_state(
                    _read_region(
                        remote,
                        INPUT_STATE_BASE,
                        INPUT_STATE_BYTES,
                    )
                )
                if control["attempt"] != 0 or control["tick"] != expected_tick:
                    raise EvidenceFailure(
                        f"{session_id} observed attempt/tick "
                        f"{control['attempt']}/{control['tick']}; "
                        f"0/{expected_tick} required"
                    )
                observations.append(
                    {"tick": expected_tick, **input_state}
                )
                if expected_tick in CAPTURE_TICKS:
                    captures[str(expected_tick)] = _capture_snapshot(
                        remote,
                        label=f"attempt_0_tick_{expected_tick}",
                        symbols=symbols,
                    )
                if expected_tick == RIGHT_FIRST_TICK - 1:
                    _post_right(window, pressed=True)
                    right_pressed = True
                    input_events.append("Right:down")
                if expected_tick == RIGHT_LAST_TICK:
                    _post_right(window, pressed=False)
                    right_pressed = False
                    input_events.append("Right:up")

            if remote.request(f"z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-gameplay breakpoint removal failed"
                )
            session_lines = transcript[start_index:]
            session = {
                "session_id": session_id,
                "metadata": {
                    "process_id": process.pid,
                    "launch_flags": list(LAUNCH_FLAGS),
                    "initial_breakpoint_symbol": "VBlankInterrupt",
                    "initial_breakpoint": f"{vblank:08X}",
                    "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
                    "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
                    "step_breakpoint_symbol": "MainLoopPostGameplayStep",
                    "step_breakpoint": f"{post_step:08X}",
                    "symbol_map_sha256": sha256_file(symbol_path),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "input_transport": "targeted_PostMessageW",
                    "input_events": input_events,
                    "transcript_sha256": _sha256_bytes(
                        (
                            "\n".join(session_lines)
                            + ("\n" if session_lines else "")
                        ).encode("utf-8")
                    ),
                },
                "initial": initial,
                "captures": captures,
                "observed_inputs": observations,
                "_transcript_lines": session_lines,
            }
        finally:
            if window is not None and right_pressed:
                try:
                    _post_right(window, pressed=False)
                    input_events.append("Right:emergency-up")
                except Exception as error:
                    transcript.append(
                        "! emergency key release failed: "
                        f"{type(error).__name__}: {error}"
                    )
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if session is None:
        raise EvidenceFailure(f"{session_id} produced no evidence")
    session_lines = transcript[start_index:]
    session["_transcript_lines"] = session_lines
    session["metadata"]["transcript_sha256"] = _sha256_bytes(
        (
            "\n".join(session_lines) + ("\n" if session_lines else "")
        ).encode("utf-8")
    )
    return session


def _failure_report(
    *,
    rom_sha256: str,
    symbol_sha256: str,
    error: Exception,
    provenance: dict[str, Any],
    transcript: list[str],
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "rom_sha256": rom_sha256,
        "symbol_map_sha256": symbol_sha256,
        "checks": [_check("m5_runtime_evidence_session", False, detail)],
        "error": detail,
        "provenance": provenance,
        "debugger_write_packets": debugger_write_packets(transcript),
        "evidence_limits": [
            "A failed capture proves no Milestone 5 runtime claim."
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--symbols", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-symbol-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    symbol_path = (
        args.symbols.resolve()
        if args.symbols
        else root / "build/recursion94-debug.sym"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m5-runtime.json"
    )
    trace_path = (
        args.trace.resolve()
        if args.trace
        else root / "artifacts/traces/m5-runtime-gdb-transcript.log"
    )
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    sessions: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    rom_sha256 = ""
    symbol_sha256 = ""
    report: dict[str, Any]

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M5 runtime evidence requires native Windows BlastEm"
            )
        if not 5.0 <= args.timeout_seconds <= 600.0:
            raise ValueError("--timeout-seconds must be within 5..600")
        if not 5.0 <= args.lock_timeout_seconds <= 600.0:
            raise ValueError(
                "--lock-timeout-seconds must be within 5..600"
            )

        emulator_directory = root / ".toolcache/blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        for path, description in (
            (rom, "Debug ROM"),
            (symbol_path, "Debug symbol map"),
            (emulator, "pinned BlastEm"),
            (default_config, "pinned default.cfg"),
        ):
            if not path.is_file():
                raise EvidenceFailure(f"{description} is missing: {path}")

        rom_sha256 = sha256_file(rom)
        symbol_sha256 = sha256_file(symbol_path)
        if args.expected_sha256 and (
            rom_sha256 != args.expected_sha256.strip().lower()
        ):
            raise EvidenceFailure(
                f"Debug ROM SHA-256 {rom_sha256} does not match "
                f"{args.expected_sha256.strip().lower()}"
            )
        if args.expected_symbol_sha256 and (
            symbol_sha256
            != args.expected_symbol_sha256.strip().lower()
        ):
            raise EvidenceFailure(
                f"Debug symbol SHA-256 {symbol_sha256} does not match "
                f"{args.expected_symbol_sha256.strip().lower()}"
            )

        bindings = verify_controller_bindings(default_config)
        if bindings["status"] != "pass":
            raise EvidenceFailure(
                "pinned default.cfg does not provide the standard pad bindings"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"Debug symbol map is missing required symbols {missing}"
            )
        user_config = verify_no_user_blastem_override()
        source_bindings = _source_bindings(root)
        provenance = {
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
                "hash_policy": "verified before and after both sessions",
            },
            "symbol_map": {
                "path": symbol_path.relative_to(root).as_posix(),
                "sha256": symbol_sha256,
                "symbols": {
                    name: f"{symbols[name]:08X}"
                    for name in REQUIRED_SYMBOLS
                },
            },
            "emulator": {
                "path": emulator.relative_to(root).as_posix(),
                "sha256": sha256_file(emulator),
            },
            "default_config": {
                "path": default_config.relative_to(root).as_posix(),
                **bindings,
            },
            "user_config_preflight": user_config,
            "source_bindings": source_bindings,
            "boot_contract": {
                "required_encounter": "B01",
                "required_directory_index": B01_DIRECTORY_INDEX,
                "required_attempt": 0,
                "selection": "compiled ROM startup state",
                "debugger_override": False,
                "failure_policy": (
                    "fail if the hash-bound ROM does not initialize B01"
                ),
            },
            "serialized_lock": lock_path.relative_to(root).as_posix(),
            "launch_flags": list(LAUNCH_FLAGS),
        }

        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            for number in (1, 2):
                sessions.append(
                    _run_live_session(
                        session_id=f"session_{number}",
                        rom=rom,
                        symbol_path=symbol_path,
                        emulator_directory=emulator_directory,
                        default_config=default_config,
                        symbols=symbols,
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
            "commands": sum(line.startswith("> ") for line in transcript),
        }

        if sha256_file(rom) != rom_sha256:
            raise EvidenceFailure("Debug ROM changed during M5 capture")
        if sha256_file(symbol_path) != symbol_sha256:
            raise EvidenceFailure("Debug symbol map changed during M5 capture")
        if (
            sha256_file(emulator)
            != provenance["emulator"]["sha256"]
        ):
            raise EvidenceFailure("pinned BlastEm changed during M5 capture")
        if (
            sha256_file(default_config)
            != provenance["default_config"]["source_sha256"]
        ):
            raise EvidenceFailure(
                "pinned default.cfg changed during M5 capture"
            )
        if _source_bindings(root) != provenance["source_bindings"]:
            raise EvidenceFailure(
                "M5 runtime/source binding changed during capture"
            )

        report = build_runtime_report(
            rom_sha256=rom_sha256,
            symbol_sha256=symbol_sha256,
            sessions=sessions,
            provenance=provenance,
        )
    except Exception as error:
        try:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(
                "\n".join(transcript) + ("\n" if transcript else ""),
                encoding="utf-8",
                newline="\n",
            )
            if transcript and trace_path.is_file():
                provenance["transcript"] = {
                    "path": trace_path.relative_to(root).as_posix(),
                    "sha256": sha256_file(trace_path),
                    "commands": sum(
                        line.startswith("> ") for line in transcript
                    ),
                }
        except OSError:
            pass
        report = _failure_report(
            rom_sha256=rom_sha256,
            symbol_sha256=symbol_sha256,
            error=error,
            provenance=provenance,
            transcript=transcript,
        )

    write_json(report_path, report)
    print(
        f"M5 runtime evidence: {report['status']} "
        f"({report_path.as_posix()})"
    )
    if report["status"] != "pass":
        print(report.get("error", "runtime evidence checks failed"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
