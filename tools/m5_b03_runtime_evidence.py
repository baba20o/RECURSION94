#!/usr/bin/env python3
"""Collect hash-bound live evidence from the Debug B03 fixture.

Two fresh positive BlastEm sessions execute the same real-pad schedule for
deterministic parity, and a third fresh session executes a no-C, zero-echo
negative route. GDB performs breakpoint control and memory reads only.

The positive schedule records two exact 720-step timeout echoes, attributes
three node hits to the second echo, proves the current actor cannot cross the
closed 16x32 leaf, and then proves a sustained dash fully clears that open
leaf before current-only completion. Any missing causal gate fails closed.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
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
from tools.m4_runtime_evidence import (  # noqa: E402
    GAMEPLAY_CONTROL_BASE,
    GAMEPLAY_CONTROL_USED_BYTES,
    REPLAY_FLAG_CURRENT,
    REPLAY_FLAG_ECHO,
    REPLAY_FLAG_VALID,
    REPLAY_TIMELINE_BYTES,
    REPLAY_BANK_BASE,
    parse_gameplay_control,
    parse_replay_timeline,
)
from tools.m5_runtime_evidence import (  # noqa: E402
    ACTOR_BYTES,
    CAMPAIGN_PROJECTION_BYTES,
    CAMPAIGN_PROJECTION_VERSION,
    DEVICE_CAPACITY,
    DEVICE_POOL,
    ENGINE_CONTROL_BASE,
    ENGINE_CONTROL_USED_BYTES,
    ENGINE_FLAG_LOADED,
    ENGINE_FLAG_PROJECTION_VALID,
    ENGINE_PROJECTION_BASE,
    ENGINE_STAGING_DESCRIPTOR,
    ENGINE_STATE_ACTIVE,
    ENGINE_LEGACY_PROJECTION_BYTES,
    ENCOUNTER_DESCRIPTOR_BYTES,
    PROJECTION_HEADER_BYTES,
    VOCAB_CAMPAIGN_FLAG_ACTIVE,
    VOCAB_CAMPAIGN_FLAG_COMPLETE,
    VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR,
    VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,
    VOCAB_PROJECTION_BYTES,
    VOCAB_STATE_BASE,
    VOCAB_STATE_BYTES,
    _hex32,
    _public,
    _sha256_bytes,
    parse_actor,
    parse_descriptor,
    parse_devices,
    parse_extended_projection,
    parse_object,
    parse_vocabulary_state,
)


REPORT_SCHEMA = "r94-m5-b03-runtime-evidence-v1"
EVIDENCE_TYPE = "blastem_gdb_m5_campaign_b03_debug_fixture"
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"

ENGINE_STATE_TERMINAL = 2
GAMEPLAY_TERMINAL_PROGRAM_STABLE = 0x0100
GAMEPLAY_RESULT_PROGRAM_STABLE = 1
CANONICAL_RECTS = 0x00FF7020
STATIC_RECT_BYTES = 8
PROJECTILE_POOL = 0x00FF2200
PROJECTILE_CAPACITY = 32
HAZARD_POOL = 0x00FF2B00
HAZARD_CAPACITY = 16
ENGINE_ACTOR_BASE = 0x00FF1800
CURRENT_ACTOR_SLOT = 6
ACTOR_FLAG_COLLISION_X = 0x0001

VOCAB_MAGIC = 0x52393556
VOCAB_VERSION = 1
VOCAB_FLAG_INITIALIZED = 0x0001
VOCAB_ACTOR_BASE_OFFSET = 0x40
VOCAB_ACTOR_BYTES = 8
VOCAB_ACTOR_FLAG_DASHING = 0x0001
VOCAB_ACTOR_FLAG_PHASED = 0x0002
VOCAB_ACTOR_FLAG_INVULNERABLE = 0x0004
VOCAB_DEVICE_STATE_ACTIVE = 0x0001
VOCAB_DEVICE_STATE_OPEN = 0x0002
VOCAB_DEVICE_STATE_HACKED = 0x0004
VOCAB_DEVICE_STATE_DISABLED = 0x0008
VOCAB_DEVICE_STATE_DASH_ROUTE = 0x1000
VOCAB_RESULT_ACTIVATED = 0x0006
VOCAB_RESULT_HACKED = 0x0007
VOCAB_RESULT_NO_TARGET = 0x0009
VOCAB_RESULT_STAGE_COMPLETE = 0x000F

B03_DIRECTORY_INDEX = 2
B03_STAGE_ID = 103
B03_ECHO_CAP = 2
B03_SEMANTIC_COUNT = 6
B03_GROUP_COUNT = 3
B03_EDGE_COUNT = 4
B03_COMPLETION_OBJECT = 4
B03_COMPLETION_FIELD = 1
B03_COMPLETION_FLAGS = 1
B03_COMPLETION_MASK = 0x1007
B03_COMPLETION_VALUE = 0x1007
B03_ROOM = (0, 0, 320, 224)
B03_SPAWN = (32, 112)
B03_RECTS = (
    (0, 0, 320, 16),
    (144, 16, 16, 48),
    (48, 48, 48, 32),
    (208, 48, 48, 32),
    (144, 80, 16, 32),
    (144, 128, 16, 32),
    (64, 160, 48, 32),
    (208, 160, 48, 32),
    (0, 16, 16, 192),
    (144, 176, 16, 32),
    (304, 16, 16, 192),
    (0, 208, 320, 16),
)
B03_DEVICES = (
    (0, 0x0000, 64, 48, 0, 0x0100),
    (1, 0x0000, 64, 176, 0, 0x0200),
    (2, 0x0300, 224, 176, 0, 0x0B01),
    (3, 0x0000, 160, 96, 0, 0x0302),
    (4, 0x0000, 288, 112, 0, 0x0903),
)
B03_HAZARDS = ((5, 0x0000, 152, 80, 0, 0x0120),)

INPUT_UP = 0x01
INPUT_DOWN = 0x02
INPUT_LEFT = 0x04
INPUT_RIGHT = 0x08
INPUT_A = 0x10
INPUT_B = 0x20
INPUT_C = 0x40

# Name, virtual-key, scan code, extended-key flag, gameplay input bit.
KEYS = (
    ("Up", 0x26, 0x48, True, INPUT_UP),
    ("Down", 0x28, 0x50, True, INPUT_DOWN),
    ("Left", 0x25, 0x4B, True, INPUT_LEFT),
    ("Right", 0x27, 0x4D, True, INPUT_RIGHT),
    ("A", 0x41, 0x1E, False, INPUT_A),
    ("B", 0x53, 0x1F, False, INPUT_B),
    ("C", 0x44, 0x20, False, INPUT_C),
)
ATTEMPT_0_INPUTS = (
    bytes((INPUT_UP,)) * 46
    + bytes((INPUT_RIGHT,)) * 21
    + bytes(653)
)
ATTEMPT_1_INPUTS = (
    bytes((INPUT_DOWN,)) * 43
    + bytes((INPUT_RIGHT,)) * 11
    + bytes((INPUT_B,))
    + bytes((INPUT_UP,)) * 14
    + bytes((INPUT_RIGHT,)) * 61
    + bytes((INPUT_DOWN,)) * 6
    + bytes((INPUT_RIGHT,)) * 43
    + bytes((INPUT_DOWN,)) * 3
    + bytes((INPUT_A,))
    + bytes(4)
    + bytes((INPUT_A,))
    + bytes(4)
    + bytes((INPUT_A,))
    + bytes(527)
)
ATTEMPT_2_INPUTS = (
    bytes((INPUT_DOWN,)) * 3
    + bytes((INPUT_RIGHT,)) * 83
    + bytes(111)
    + bytes((INPUT_RIGHT | INPUT_C,))
    + bytes((INPUT_RIGHT,)) * 74
    + bytes((INPUT_B,)) * 20
)

ATTEMPT_CAPTURE_TICKS = {
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
CHECKPOINT_KEYS = (
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

NEGATIVE_INPUTS = (
    bytes((INPUT_DOWN,)) * 29
    + bytes((INPUT_RIGHT,)) * 72
    + bytes((INPUT_DOWN,)) * 6
    + bytes((INPUT_RIGHT,)) * 16
    + bytes((INPUT_UP,)) * 6
    + bytes((INPUT_RIGHT,)) * 72
    + bytes((INPUT_UP,)) * 29
    + bytes((INPUT_B,))
    + bytes(9)
)
NEGATIVE_CAPTURE_TICKS = (231, 240)

LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")
INPUT_EDGE_SETTLE_SECONDS = 0.025
REQUIRED_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopGameplayStepCall",
    "MainLoopPostGameplayStep",
    "GameplayStep",
    "CampaignLoadEncounter",
    "CampaignEvaluateDependencies",
    "CampaignCheckCompletion",
    "GameplayBuildProjection",
    "CampaignRuntimeDescriptorB03",
    "CampaignRuntimeRectsB03",
    "CampaignRuntimeObjectsB03",
    "CampaignRuntimeProfileB03",
    "CampaignRuntimeSemanticsB03",
    "CampaignRuntimeDependencyGroupsB03",
    "CampaignRuntimeDependencyEdgesB03",
    "DEBUG_START_ENCOUNTER",
)
SOURCE_BINDING_PATHS = (
    "content/campaign.json",
    "content/encounters/boot/B03.json",
    "src/boot/startup.asm",
    "src/game/engine_layout.asm",
    "src/game/object_pool.asm",
    "src/gameplay/actor_sim.asm",
    "src/gameplay/campaign_collision.asm",
    "src/gameplay/campaign_runtime.asm",
    "src/gameplay/gameplay_core.asm",
    "src/gameplay/replay.asm",
    "src/gameplay/vocabulary_combat.asm",
    "src/gameplay/vocabulary_core.asm",
    "src/gameplay/vocabulary_interactions.asm",
    "src/gameplay/vocabulary_layout.asm",
    "src/ui/timeline_control.asm",
    "assets/generated/campaign_runtime/campaign_runtime.inc.asm",
    "assets/generated/campaign_runtime/campaign_runtime_report.json",
    "assets/generated/content/campaign_content.bin",
    "assets/generated/content/campaign_content_report.json",
)


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _s16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big", signed=True)


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def expected_attempt_inputs(attempt: int) -> bytes:
    schedules = (ATTEMPT_0_INPUTS, ATTEMPT_1_INPUTS, ATTEMPT_2_INPUTS)
    if not 0 <= attempt < len(schedules):
        raise ValueError(f"attempt outside 0..2: {attempt}")
    return schedules[attempt]


def parse_rectangles(raw: bytes, count: int) -> list[tuple[int, int, int, int]]:
    """Parse an exact bounded canonical rectangle prefix."""
    if not 0 <= count <= 32:
        raise EvidenceFailure(f"rectangle count {count} is outside 0..32")
    expected = count * STATIC_RECT_BYTES
    if len(raw) != expected:
        raise EvidenceFailure(
            f"rectangle read is {len(raw)} bytes; {expected} required"
        )
    return [
        (
            _s16(raw, offset),
            _s16(raw, offset + 2),
            _u16(raw, offset + 4),
            _u16(raw, offset + 6),
        )
        for offset in range(0, expected, STATIC_RECT_BYTES)
    ]


def parse_fixed_pool(
    raw: bytes, *, capacity: int
) -> list[dict[str, Any]]:
    if len(raw) != capacity * OBJECT_BYTES:
        raise EvidenceFailure(
            f"pool read is {len(raw)} bytes; "
            f"{capacity * OBJECT_BYTES} required"
        )
    return [
        parse_object(
            raw[slot * OBJECT_BYTES : (slot + 1) * OBJECT_BYTES],
            slot,
        )
        for slot in range(capacity)
    ]


def parse_actor_slots(raw: bytes) -> list[dict[str, Any]]:
    if len(raw) != ACTOR_CAPACITY * ACTOR_BYTES:
        raise EvidenceFailure(
            f"actor read is {len(raw)} bytes; "
            f"{ACTOR_CAPACITY * ACTOR_BYTES} required"
        )
    return [
        {
            "slot": slot,
            **parse_actor(
                raw[slot * ACTOR_BYTES : (slot + 1) * ACTOR_BYTES]
            ),
        }
        for slot in range(ACTOR_CAPACITY)
    ]


def parse_b03_vocabulary(raw: bytes) -> dict[str, Any]:
    parsed = parse_vocabulary_state(raw)
    parsed["metrics"] = {
        "stage_attempts": _u16(raw, 0x1A),
        "stage_final_tick": _u16(raw, 0x1C),
        "stage_echo_count": raw[0x20],
        "stage_rank": raw[0x21],
        "beam_count": raw[0x24],
        "active_enemy_count": raw[0x25],
        "active_projectile_count": raw[0x26],
    }
    extensions = []
    for slot in range(ACTOR_CAPACITY):
        offset = VOCAB_ACTOR_BASE_OFFSET + slot * VOCAB_ACTOR_BYTES
        extensions.append(
            {
                "slot": slot,
                "flags": _u16(raw, offset),
                "dash_timer": raw[offset + 2],
                "dash_cooldown": raw[offset + 3],
                "invulnerability_timer": raw[offset + 4],
                "carry_slot": raw[offset + 5],
                "shield_timer": raw[offset + 6],
                "last_action": raw[offset + 7],
            }
        )
    parsed["actor_extensions"] = extensions
    return parsed


def _pointer_matches(
    vocabulary: dict[str, Any],
    descriptor: dict[str, Any],
    symbols: dict[str, int],
) -> dict[str, bool]:
    derived = vocabulary["derived"]
    return {
        "profile": derived["profile_ptr"]
        == symbols["CampaignRuntimeProfileB03"],
        "semantics": derived["semantic_ptr"]
        == symbols["CampaignRuntimeSemanticsB03"],
        "groups": derived["group_ptr"]
        == symbols["CampaignRuntimeDependencyGroupsB03"],
        "edges": derived["edge_ptr"]
        == symbols["CampaignRuntimeDependencyEdgesB03"],
        "rects": descriptor["rect_pointer"]
        == symbols["CampaignRuntimeRectsB03"],
        "objects": descriptor["object_pointer"]
        == symbols["CampaignRuntimeObjectsB03"],
    }


def make_snapshot(
    *,
    label: str,
    control_raw: bytes,
    gameplay_raw: bytes,
    vocabulary_raw: bytes,
    descriptor_raw: bytes,
    rectangle_raw: bytes,
    device_raw: bytes,
    projectile_raw: bytes,
    hazard_raw: bytes,
    actor_raw: bytes,
    input_raw: bytes,
    replay_raw: bytes,
    projection_raw: bytes,
    symbols: dict[str, int],
) -> dict[str, Any]:
    """Parse one coherent breakpoint observation."""
    engine = parse_engine_control(control_raw)
    gameplay = parse_gameplay_control(gameplay_raw)
    vocabulary = parse_b03_vocabulary(vocabulary_raw)
    descriptor = parse_descriptor(descriptor_raw)
    rectangles = parse_rectangles(rectangle_raw, len(B03_RECTS))
    devices = parse_devices(device_raw)
    projectiles = parse_fixed_pool(
        projectile_raw, capacity=PROJECTILE_CAPACITY
    )
    hazards = parse_fixed_pool(hazard_raw, capacity=HAZARD_CAPACITY)
    actors = parse_actor_slots(actor_raw)
    input_state = parse_input_state(input_raw)
    replay = parse_replay_timeline(replay_raw)
    projection = parse_extended_projection(
        projection_raw,
        live_vocabulary_prefix=vocabulary_raw[:VOCAB_PROJECTION_BYTES],
    )
    public_replay = {
        key: value for key, value in replay.items() if key != "banks"
    }
    return {
        "label": label,
        "engine": engine,
        "gameplay": gameplay,
        "vocabulary": vocabulary,
        "descriptor": descriptor,
        "rectangles": rectangles,
        "devices": devices,
        "projectiles": projectiles,
        "hazards": hazards,
        "actors": actors,
        "current_actor": actors[CURRENT_ACTOR_SLOT],
        "input": input_state,
        "replay_timeline": public_replay,
        "projection": {
            **projection,
            "published_crc32_iso_hdlc_hex": _hex32(engine["state_crc"]),
            "published_crc_matches": (
                projection["crc32_iso_hdlc"] == engine["state_crc"]
            ),
        },
        "pointer_matches": _pointer_matches(
            vocabulary, descriptor, symbols
        ),
        "_control_bytes": bytes(control_raw),
        "_gameplay_bytes": bytes(gameplay_raw),
        "_vocabulary_bytes": bytes(vocabulary_raw),
        "_descriptor_bytes": bytes(descriptor_raw),
        "_rectangle_bytes": bytes(rectangle_raw),
        "_device_bytes": bytes(device_raw),
        "_projectile_bytes": bytes(projectile_raw),
        "_hazard_bytes": bytes(hazard_raw),
        "_actor_bytes": bytes(actor_raw),
        "_input_bytes": bytes(input_raw),
        "_replay_bytes": bytes(replay_raw),
        "_projection_bytes": bytes(projection_raw),
    }


def _object_signature(
    objects: list[dict[str, Any]], count: int
) -> list[tuple[int, int, int, int, int, int]]:
    return [
        (
            item["logical_id"],
            item["state"],
            item["x_16_8"] >> 8,
            item["y_16_8"] >> 8,
            item["timer"],
            item["param"],
        )
        for item in objects[:count]
    ]


def initialization_ok(snapshot: dict[str, Any]) -> bool:
    engine = snapshot["engine"]
    vocabulary = snapshot["vocabulary"]
    campaign = vocabulary["campaign"]
    descriptor = snapshot["descriptor"]
    projection = snapshot["projection"]
    projection_header = projection["header"]
    device_projection = next(
        pool for pool in projection["pools"] if pool["name"] == "device"
    )
    hazard_projection = next(
        pool for pool in projection["pools"] if pool["name"] == "hazard"
    )
    timeline = snapshot["replay_timeline"]["timeline"]
    current = snapshot["current_actor"]
    return (
        engine["state"] == ENGINE_STATE_ACTIVE
        and (
            engine["flags"]
            & (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID)
        )
        == (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID)
        and engine["encounter"] == B03_DIRECTORY_INDEX
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
        == B03_ROOM
        and (engine["spawn_x"], engine["spawn_y"])
        == (B03_SPAWN[0] << 8, B03_SPAWN[1] << 8)
        and engine["rect_count"] == len(B03_RECTS)
        and engine["object_init_count"] == 6
        and engine["pool_counts"] == [0, 0, 5, 0, 1, 0, 0]
        and vocabulary["magic"] == VOCAB_MAGIC
        and vocabulary["version"] == VOCAB_VERSION
        and vocabulary["flags"] & VOCAB_FLAG_INITIALIZED
        and vocabulary["stage_id"] == B03_STAGE_ID
        and campaign["flags"]
        == (
            VOCAB_CAMPAIGN_FLAG_ACTIVE
            | VOCAB_CAMPAIGN_FLAG_PROFILE_VALID
        )
        and campaign["directory_index"] == B03_DIRECTORY_INDEX
        and campaign["echo_cap"] == B03_ECHO_CAP
        and campaign["semantic_count"] == B03_SEMANTIC_COUNT
        and campaign["group_count"] == B03_GROUP_COUNT
        and campaign["edge_count"] == B03_EDGE_COUNT
        and campaign["completion_object"] == B03_COMPLETION_OBJECT
        and campaign["completion_field"] == B03_COMPLETION_FIELD
        and campaign["completion_flags"] == B03_COMPLETION_FLAGS
        and campaign["completion_mask"] == B03_COMPLETION_MASK
        and campaign["completion_value"] == B03_COMPLETION_VALUE
        and campaign["dependency_latches_hex"] == "00" * 32
        and campaign["event_flags"] == 0
        and campaign["last_interaction_actor"] == 0xFF
        and campaign["last_interaction_object"] == 0xFF
        and descriptor["id"] == B03_DIRECTORY_INDEX
        and descriptor["version"] == 1
        and descriptor["bytes"] == ENCOUNTER_DESCRIPTOR_BYTES
        and tuple(descriptor["room"]) == B03_ROOM
        and tuple(descriptor["spawn"]) == B03_SPAWN
        and descriptor["rect_count"] == len(B03_RECTS)
        and descriptor["object_count"] == 6
        and snapshot["rectangles"] == list(B03_RECTS)
        and all(snapshot["pointer_matches"].values())
        and _object_signature(snapshot["devices"], 5)
        == list(B03_DEVICES)
        and _object_signature(snapshot["hazards"], 1)
        == list(B03_HAZARDS)
        and all(item["active"] == 0 for item in snapshot["devices"][5:])
        and all(item["active"] == 0 for item in snapshot["hazards"][1:])
        and all(item["active"] == 0 for item in snapshot["actors"][:6])
        and current["active"] == 1
        and current["role"] == CURRENT_ACTOR_SLOT
        and current["x_16_8"] == B03_SPAWN[0] << 8
        and current["y_16_8"] == B03_SPAWN[1] << 8
        and current["health"] == 1
        and current["logical_id"] == 6
        and timeline["echo_count"] == 0
        and timeline["current_bank"] == 6
        and timeline["echo_order"] == [0xFF] * 6
        and projection_header["schema_version"]
        == CAMPAIGN_PROJECTION_VERSION
        and projection_header["bytes"] == CAMPAIGN_PROJECTION_BYTES
        and projection_header["encounter"] == B03_DIRECTORY_INDEX
        and projection_header["attempt"] == 0
        and projection_header["tick"] == 0
        and tuple(projection_header["room"]) == B03_ROOM
        and projection_header["rect_count"] == len(B03_RECTS)
        and device_projection["active_logical_ids"] == [0, 1, 2, 3, 4]
        and hazard_projection["active_logical_ids"] == [5]
        and projection["legacy_body_bytes"]
        == ENGINE_LEGACY_PROJECTION_BYTES
        and projection["tail_bytes"] == VOCAB_PROJECTION_BYTES
        and projection["bounds_valid"]
        and projection["tail_matches_live_vocabulary"] is True
        and projection["published_crc_matches"]
    )


def _snapshot_raw_bundle(snapshot: dict[str, Any]) -> bytes:
    return b"".join(
        snapshot[key]
        for key in (
            "_control_bytes",
            "_gameplay_bytes",
            "_vocabulary_bytes",
            "_descriptor_bytes",
            "_rectangle_bytes",
            "_device_bytes",
            "_projectile_bytes",
            "_hazard_bytes",
            "_actor_bytes",
            "_input_bytes",
            "_replay_bytes",
            "_projection_bytes",
        )
    )


def _descriptor_for_bank(snapshot: dict[str, Any], bank: int) -> dict[str, Any]:
    return snapshot["replay_timeline"]["descriptors"][bank]


def _replay_bank_bytes(snapshot: dict[str, Any], bank: int) -> bytes:
    if not 0 <= bank < 7:
        raise ValueError(f"replay bank outside 0..6: {bank}")
    start = bank * 720
    return snapshot["_replay_bytes"][start : start + 720]


def _active_projectiles(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        projectile
        for projectile in snapshot["projectiles"]
        if projectile["active"]
    ]


def _b03_door_geometry_ok(snapshot: dict[str, Any]) -> bool:
    door = snapshot["devices"][3]
    return bool(
        door["active"] == 1
        and door["logical_id"] == 3
        and door["param"] == 0x0302
        and door["x_16_8"] == 160 * 256
        and door["y_16_8"] == 96 * 256
    )


def _echo_projectile_spawn_ok(
    snapshot: dict[str, Any],
    *,
    expected_node_state: int,
) -> bool:
    projectiles = _active_projectiles(snapshot)
    if len(projectiles) != 1:
        return False
    projectile = projectiles[0]
    echo = snapshot["actors"][1]
    current = snapshot["current_actor"]
    node = snapshot["devices"][2]
    return bool(
        snapshot["engine"]["attempt"] == 2
        and echo["active"] == 1
        and echo["logical_id"] == 0
        and echo["input"] == INPUT_A
        and echo["x_16_8"] == 204 * 256
        and echo["y_16_8"] == 169 * 256
        and current["active"] == 1
        and current["logical_id"] == 1
        and current["input"] == 0
        and snapshot["gameplay"]["current_input"] == 0
        and snapshot["gameplay"]["current_pressed"] == 0
        and node["state"] == expected_node_state
        and node["logical_id"] == 2
        and node["param"] == 0x0B01
        and node["x_16_8"] == 224 * 256
        and node["y_16_8"] == 176 * 256
        and projectile["state"] & 0x0001
        and projectile["param"] == 0x0101
        and projectile["x_16_8"] == 207 * 256
        and projectile["y_16_8"] == 169 * 256
        and projectile["vx_16_8"] > 0
        and projectile["vy_16_8"] == 0
        and abs(projectile["y_16_8"] - node["y_16_8"]) <= 8 * 256
        and projectile["x_16_8"] < node["x_16_8"]
    )


def _echo_projectile_pre_hit_ok(
    snapshot: dict[str, Any],
    *,
    expected_node_state: int,
) -> bool:
    projectiles = _active_projectiles(snapshot)
    if len(projectiles) != 1:
        return False
    projectile = projectiles[0]
    node = snapshot["devices"][2]
    return bool(
        snapshot["engine"]["attempt"] == 2
        and snapshot["actors"][1]["logical_id"] == 0
        and snapshot["actors"][1]["input"] == 0
        and snapshot["current_actor"]["input"] == 0
        and snapshot["gameplay"]["current_pressed"] == 0
        and node["state"] == expected_node_state
        and node["x_16_8"] == 224 * 256
        and node["y_16_8"] == 176 * 256
        and projectile["param"] == 0x0101
        and projectile["x_16_8"] == 213 * 256
        and projectile["y_16_8"] == 169 * 256
        and projectile["vx_16_8"] == 3 * 256
        and projectile["vy_16_8"] == 0
        and 8 * 256
        < node["x_16_8"] - projectile["x_16_8"]
        <= 11 * 256
        and abs(projectile["y_16_8"] - node["y_16_8"]) <= 8 * 256
    )


def _scenario_facts(session: dict[str, Any]) -> dict[str, bool]:
    snapshots = session["snapshots"]
    post_1 = snapshots["post_timeout_1"]
    post_2 = snapshots["post_timeout_2"]
    timed = snapshots["attempt_2_tick_55"]
    pressure = snapshots["attempt_2_tick_67"]
    blocked_contact = snapshots["attempt_2_tick_86"]
    blocked_start = snapshots["attempt_2_tick_87"]
    spawn_1 = snapshots["attempt_2_tick_183"]
    pre_hit_1 = snapshots["attempt_2_tick_185"]
    hit_1 = snapshots["attempt_2_tick_186"]
    spawn_2 = snapshots["attempt_2_tick_188"]
    pre_hit_2 = snapshots["attempt_2_tick_190"]
    hit_2 = snapshots["attempt_2_tick_191"]
    spawn_3 = snapshots["attempt_2_tick_193"]
    pre_hit_3 = snapshots["attempt_2_tick_195"]
    hit_3 = snapshots["attempt_2_tick_196"]
    pre_crossing = snapshots["attempt_2_tick_197"]
    crossing = snapshots["attempt_2_tick_198"]
    east = snapshots["attempt_2_tick_201"]
    hold_start = snapshots["attempt_2_tick_273"]
    hold_late = snapshots["attempt_2_tick_291"]
    terminal = snapshots["terminal"]

    timed_device = timed["devices"][1]
    pressure_device = pressure["devices"][0]
    crossing_extension = crossing["vocabulary"]["actor_extensions"][
        CURRENT_ACTOR_SLOT
    ]
    east_extension = east["vocabulary"]["actor_extensions"][
        CURRENT_ACTOR_SLOT
    ]
    terminal_campaign = terminal["vocabulary"]["campaign"]
    terminal_timeline = terminal["replay_timeline"]["timeline"]
    node_damage_owned = bool(
        _echo_projectile_spawn_ok(
            spawn_1, expected_node_state=0x0301
        )
        and _echo_projectile_pre_hit_ok(
            pre_hit_1, expected_node_state=0x0301
        )
        and not _active_projectiles(hit_1)
        and hit_1["devices"][2]["state"] == 0x0201
        and _echo_projectile_spawn_ok(
            spawn_2, expected_node_state=0x0201
        )
        and _echo_projectile_pre_hit_ok(
            pre_hit_2, expected_node_state=0x0201
        )
        and not _active_projectiles(hit_2)
        and hit_2["devices"][2]["state"] == 0x0101
        and _echo_projectile_spawn_ok(
            spawn_3, expected_node_state=0x0101
        )
        and _echo_projectile_pre_hit_ok(
            pre_hit_3, expected_node_state=0x0101
        )
        and not _active_projectiles(hit_3)
        and hit_3["devices"][2]["state"]
        & VOCAB_DEVICE_STATE_DISABLED
        and hit_3["devices"][2]["state"] >> 8 == 0
        and terminal["devices"][2]["state"]
        & VOCAB_DEVICE_STATE_DISABLED
        and terminal["devices"][2]["state"] >> 8 == 0
    )
    return {
        "timed_switch_activated": (
            timed_device["state"] & VOCAB_DEVICE_STATE_ACTIVE
            and 0 < timed_device["timer"] < 180
            and timed["actors"][1]["active"] == 1
            and timed["actors"][1]["logical_id"] == 0
            and timed["actors"][1]["input"] == INPUT_B
            and timed["current_actor"]["input"] == INPUT_RIGHT
            and timed["gameplay"]["current_pressed"] == 0
            and timed["vocabulary"]["campaign"][
                "last_interaction_actor"
            ]
            == 1
            and timed["vocabulary"]["campaign"][
                "last_interaction_object"
            ]
            == 1
        ),
        "first_timeout_echo_committed": (
            post_1["engine"]["attempt"] == 1
            and post_1["engine"]["tick"] == 0
            and post_1["replay_timeline"]["timeline"]["echo_count"] == 1
            and post_1["replay_timeline"]["timeline"]["echo_order"][0] == 6
            and _descriptor_for_bank(post_1, 6)["length"]
            == len(ATTEMPT_0_INPUTS)
            and _descriptor_for_bank(post_1, 6)["flags"]
            & (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            == (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
        ),
        "security_node_exposed": (
            timed["devices"][2]["state"] & VOCAB_DEVICE_STATE_ACTIVE
            and not (
                timed["devices"][2]["state"]
                & VOCAB_DEVICE_STATE_DISABLED
            )
            and timed["devices"][2]["state"] >> 8 == 3
        ),
        "pressure_echo_holds_switch": (
            pressure_device["state"] & VOCAB_DEVICE_STATE_ACTIVE
            and pressure_device["timer"] & 0x0001
            and pressure["actors"][0]["active"] == 1
            and pressure["actors"][0]["logical_id"] == 6
            and pressure["actors"][0]["input"] == INPUT_RIGHT
            and pressure["current_actor"]["input"] == INPUT_RIGHT
            and pressure["gameplay"]["current_pressed"] == 0
        ),
        "two_timeout_echoes_committed": (
            post_2["engine"]["attempt"] == 2
            and post_2["engine"]["tick"] == 0
            and post_2["replay_timeline"]["timeline"]["echo_count"] == 2
            and post_2["replay_timeline"]["timeline"]["echo_order"][:2]
            == [6, 0]
            and _descriptor_for_bank(post_2, 6)["length"]
            == len(ATTEMPT_0_INPUTS)
            and _descriptor_for_bank(post_2, 0)["length"]
            == len(ATTEMPT_1_INPUTS)
        ),
        "node_damage_owned_by_echo_replay": node_damage_owned,
        "current_blocked_west_until_open": (
            _b03_door_geometry_ok(blocked_contact)
            and _b03_door_geometry_ok(blocked_start)
            and _b03_door_geometry_ok(pre_hit_3)
            and _b03_door_geometry_ok(hit_3)
            and blocked_contact["devices"][3]["state"]
            & VOCAB_DEVICE_STATE_OPEN
            == 0
            and blocked_contact["current_actor"]["x_16_8"] == 156 * 256
            and blocked_contact["current_actor"]["y_16_8"] > 96 * 256
            and blocked_contact["current_actor"]["y_16_8"] < 128 * 256
            and blocked_contact["current_actor"]["input"] == INPUT_RIGHT
            and blocked_contact["current_actor"]["flags"]
            & ACTOR_FLAG_COLLISION_X
            and blocked_start["devices"][3]["state"]
            & VOCAB_DEVICE_STATE_OPEN
            == 0
            and blocked_start["current_actor"]["x_16_8"] == 156 * 256
            and blocked_start["current_actor"]["y_16_8"] > 96 * 256
            and blocked_start["current_actor"]["y_16_8"] < 128 * 256
            and blocked_start["current_actor"]["input"] == 0
            and pre_hit_3["devices"][3]["state"]
            & VOCAB_DEVICE_STATE_OPEN
            == 0
            and pre_hit_3["current_actor"]["x_16_8"] == 156 * 256
            and pre_hit_3["current_actor"]["y_16_8"] > 96 * 256
            and pre_hit_3["current_actor"]["y_16_8"] < 128 * 256
            and hit_3["current_actor"]["x_16_8"] == 156 * 256
            and hit_3["current_actor"]["input"] == 0
        ),
        "timed_door_and_commit_unlock_opened": (
            hit_3["devices"][3]["state"] & VOCAB_DEVICE_STATE_OPEN
            and hit_3["devices"][3]["timer"] == 150
            and hit_3["devices"][4]["state"] & VOCAB_DEVICE_STATE_OPEN
            and terminal["devices"][3]["state"]
            & VOCAB_DEVICE_STATE_OPEN
            and terminal["devices"][3]["timer"] > 0
        ),
        "current_dashes_through_open_gate": (
            _b03_door_geometry_ok(pre_crossing)
            and _b03_door_geometry_ok(crossing)
            and _b03_door_geometry_ok(east)
            and pre_crossing["devices"][4]["state"]
            == VOCAB_DEVICE_STATE_OPEN
            and pre_crossing["devices"][3]["state"]
            & VOCAB_DEVICE_STATE_OPEN
            and pre_crossing["current_actor"]["x_16_8"] == 156 * 256
            and pre_crossing["current_actor"]["input"] == 0
            and crossing["devices"][4]["state"]
            == VOCAB_DEVICE_STATE_OPEN
            and crossing["devices"][3]["state"] & VOCAB_DEVICE_STATE_OPEN
            and crossing["current_actor"]["x_16_8"] == 162 * 256
            and crossing["current_actor"]["y_16_8"] > 96 * 256
            and crossing["current_actor"]["y_16_8"] < 128 * 256
            and crossing["current_actor"]["input"]
            == INPUT_RIGHT | INPUT_C
            and crossing["gameplay"]["current_pressed"] & INPUT_C
            and crossing_extension["flags"]
            & (
                VOCAB_ACTOR_FLAG_DASHING
                | VOCAB_ACTOR_FLAG_PHASED
                | VOCAB_ACTOR_FLAG_INVULNERABLE
            )
            == (
                VOCAB_ACTOR_FLAG_DASHING
                | VOCAB_ACTOR_FLAG_PHASED
                | VOCAB_ACTOR_FLAG_INVULNERABLE
            )
            and crossing_extension["dash_timer"] > 0
            and east["devices"][3]["state"] & VOCAB_DEVICE_STATE_OPEN
            and east["current_actor"]["x_16_8"] >= 180 * 256
            and east["current_actor"]["x_16_8"]
            - east["current_actor"]["half_width"] * 256
            >= 176 * 256
            and east["current_actor"]["y_16_8"] > 96 * 256
            and east["current_actor"]["y_16_8"] < 128 * 256
            and east_extension["flags"]
            & (VOCAB_ACTOR_FLAG_DASHING | VOCAB_ACTOR_FLAG_PHASED)
            == (VOCAB_ACTOR_FLAG_DASHING | VOCAB_ACTOR_FLAG_PHASED)
            and east_extension["dash_timer"] > 0
            and east["devices"][4]["state"]
            == (
                VOCAB_DEVICE_STATE_OPEN
                | VOCAB_DEVICE_STATE_DASH_ROUTE
            )
        ),
        "current_only_completion": (
            hold_start["engine"]["state"] == ENGINE_STATE_ACTIVE
            and hold_start["engine"]["attempt"] == 2
            and hold_start["engine"]["tick"] == 273
            and hold_start["devices"][4]["state"]
            == (
                VOCAB_DEVICE_STATE_OPEN
                | VOCAB_DEVICE_STATE_DASH_ROUTE
                | VOCAB_DEVICE_STATE_ACTIVE
            )
            and hold_start["devices"][4]["timer"] == 1
            and hold_start["devices"][4]["vx_16_8"]
            == CURRENT_ACTOR_SLOT + 1
            and hold_start["devices"][4]["vy_16_8"] == 273
            and hold_start["vocabulary"]["result"]
            == VOCAB_RESULT_ACTIVATED
            and hold_start["current_actor"]["input"] == INPUT_B
            and hold_start["gameplay"]["current_pressed"] == INPUT_B
            and hold_start["engine"]["input_held"] == INPUT_B
            and hold_start["engine"]["input_pressed"] == INPUT_B
            and not hold_start["vocabulary"]["campaign"]["flags"]
            & VOCAB_CAMPAIGN_FLAG_COMPLETE
            and hold_late["engine"]["state"] == ENGINE_STATE_ACTIVE
            and hold_late["engine"]["attempt"] == 2
            and hold_late["engine"]["tick"] == 291
            and hold_late["devices"][4]["state"]
            == (
                VOCAB_DEVICE_STATE_OPEN
                | VOCAB_DEVICE_STATE_DASH_ROUTE
                | VOCAB_DEVICE_STATE_ACTIVE
            )
            and hold_late["devices"][4]["timer"] == 19
            and hold_late["devices"][4]["vx_16_8"]
            == CURRENT_ACTOR_SLOT + 1
            and hold_late["devices"][4]["vy_16_8"] == 291
            and hold_late["vocabulary"]["result"]
            == VOCAB_RESULT_ACTIVATED
            and hold_late["current_actor"]["input"] == INPUT_B
            and hold_late["gameplay"]["current_pressed"] == 0
            and hold_late["engine"]["input_held"] == INPUT_B
            and hold_late["engine"]["input_pressed"] == 0
            and not hold_late["vocabulary"]["campaign"]["flags"]
            & VOCAB_CAMPAIGN_FLAG_COMPLETE
            and session["attempt_inputs"]["2"][272:292]
            == [INPUT_B] * 20
            and terminal["engine"]["state"] == ENGINE_STATE_TERMINAL
            and terminal["engine"]["terminal_reason"]
            == GAMEPLAY_TERMINAL_PROGRAM_STABLE
            and terminal["engine"]["attempt"] == 2
            and terminal["engine"]["tick"] == len(ATTEMPT_2_INPUTS)
            and terminal["gameplay"]["result"]
            == GAMEPLAY_RESULT_PROGRAM_STABLE
            and terminal_campaign["flags"]
            & VOCAB_CAMPAIGN_FLAG_COMPLETE
            and terminal_campaign["last_interaction_actor"]
            == CURRENT_ACTOR_SLOT
            and terminal_campaign["last_interaction_object"]
            == B03_COMPLETION_OBJECT
            and terminal["current_actor"]["active"] == 1
            and terminal["current_actor"]["role"] == CURRENT_ACTOR_SLOT
            and terminal["current_actor"]["logical_id"] == 1
            and terminal["current_actor"]["input"] == INPUT_B
            and terminal["gameplay"]["current_input"] == INPUT_B
            and terminal["gameplay"]["current_pressed"] == 0
            and terminal["gameplay"]["current_previous"] == INPUT_B
            and terminal["engine"]["input_held"] == INPUT_B
            and terminal["engine"]["input_pressed"] == 0
            and terminal["engine"]["input_previous"] == INPUT_B
            and session["attempt_inputs"]["2"][-1] == INPUT_B
            and terminal["actors"][0]["input"] == 0
            and terminal["actors"][1]["input"] == 0
            and terminal["devices"][4]["active"] == 1
            and terminal["devices"][4]["logical_id"] == 4
            and terminal["devices"][4]["param"] == 0x0903
            and terminal["devices"][4]["state"] & B03_COMPLETION_MASK
            == B03_COMPLETION_VALUE
            and terminal["devices"][4]["timer"] == 20
            and terminal["devices"][4]["vx_16_8"]
            == CURRENT_ACTOR_SLOT + 1
            and terminal["devices"][4]["vy_16_8"] == 292
            and terminal["vocabulary"]["result"]
            == VOCAB_RESULT_STAGE_COMPLETE
            and terminal_timeline["echo_count"] == 2
        ),
    }


def _negative_zero_echo_ok(session: dict[str, Any]) -> bool:
    hacked = session["snapshots"]["negative_tick_231"]
    final = session["snapshots"]["negative_tick_240"]
    hacked_campaign = hacked["vocabulary"]["campaign"]
    final_campaign = final["vocabulary"]["campaign"]
    return bool(
        all(not value & INPUT_C for value in session["negative_inputs"])
        and
        hacked["engine"]["state"] == ENGINE_STATE_ACTIVE
        and hacked["engine"]["attempt"] == 0
        and hacked["engine"]["tick"] == 231
        and hacked["replay_timeline"]["timeline"]["echo_count"] == 0
        and hacked["current_actor"]["role"] == CURRENT_ACTOR_SLOT
        and hacked["current_actor"]["logical_id"] == 6
        and hacked["current_actor"]["input"] == INPUT_B
        and hacked["current_actor"]["x_16_8"] == 272 * 256
        and hacked["current_actor"]["y_16_8"] == 112 * 256
        and hacked["vocabulary"]["actor_extensions"][
            CURRENT_ACTOR_SLOT
        ]["flags"]
        & (VOCAB_ACTOR_FLAG_DASHING | VOCAB_ACTOR_FLAG_PHASED)
        == 0
        and hacked["gameplay"]["current_pressed"] == INPUT_B
        and session["negative_inputs"][230] == INPUT_B
        and hacked_campaign["last_interaction_actor"] == 0xFF
        and hacked_campaign["last_interaction_object"] == 0xFF
        and hacked["devices"][4]["state"] == 0
        and hacked["devices"][4]["timer"] == 0
        and hacked["devices"][4]["vx_16_8"] == 0
        and hacked["devices"][4]["vy_16_8"] == 0
        and hacked["vocabulary"]["result"] == VOCAB_RESULT_NO_TARGET
        and hacked["devices"][4]["param"] == 0x0903
        and hacked["devices"][4]["x_16_8"] == 288 * 256
        and hacked["devices"][4]["y_16_8"] == 112 * 256
        and not hacked_campaign["flags"] & VOCAB_CAMPAIGN_FLAG_COMPLETE
        and final["engine"]["state"] == ENGINE_STATE_ACTIVE
        and final["engine"]["attempt"] == 0
        and final["engine"]["tick"] == len(NEGATIVE_INPUTS)
        and final["replay_timeline"]["timeline"]["echo_count"] == 0
        and not final["devices"][3]["state"] & VOCAB_DEVICE_STATE_OPEN
        and final["devices"][4]["state"] == 0
        and final["devices"][4]["timer"] == 0
        and not final["devices"][4]["state"]
        & VOCAB_DEVICE_STATE_DASH_ROUTE
        and not final_campaign["flags"] & VOCAB_CAMPAIGN_FLAG_COMPLETE
    )


def _source_bindings(root: Path) -> dict[str, Any]:
    sources: dict[str, str] = {}
    for relative in SOURCE_BINDING_PATHS:
        path = root / relative
        if not path.is_file():
            raise EvidenceFailure(f"B03 evidence binding is missing {path}")
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
    }


def verify_pre_step_rom_binding(
    rom: Path,
    symbols: dict[str, int],
) -> dict[str, Any]:
    """Bind the PRE symbol to the unique bsr.w GameplayStep in the ROM."""
    pre_step = symbols["MainLoopGameplayStepCall"]
    gameplay_step = symbols["GameplayStep"]
    payload = rom.read_bytes()
    if pre_step < 0 or pre_step + 4 > len(payload):
        raise EvidenceFailure(
            f"pre-step address ${pre_step:08X} is outside the ROM"
        )
    opcode = int.from_bytes(payload[pre_step : pre_step + 2], "big")
    displacement = int.from_bytes(
        payload[pre_step + 2 : pre_step + 4],
        "big",
        signed=True,
    )
    # 68000 word branches use the extension-word address (opcode + 2) as
    # their displacement base.
    resolved = pre_step + 2 + displacement
    matching_calls = []
    for address in range(0, len(payload) - 3, 2):
        if payload[address : address + 2] != b"\x61\x00":
            continue
        candidate_displacement = int.from_bytes(
            payload[address + 2 : address + 4],
            "big",
            signed=True,
        )
        if address + 2 + candidate_displacement == gameplay_step:
            matching_calls.append(address)
    if (
        opcode != 0x6100
        or resolved != gameplay_step
        or matching_calls != [pre_step]
    ):
        raise EvidenceFailure(
            "MainLoopGameplayStepCall is not the unique ROM bsr.w to "
            f"GameplayStep: opcode ${opcode:04X}, resolved ${resolved:08X}, "
            f"matches {[f'${item:08X}' for item in matching_calls]}"
        )
    return {
        "status": "pass",
        "instruction": "bsr.w GameplayStep",
        "address": f"{pre_step:08X}",
        "opcode": f"{opcode:04X}",
        "resolved_target": f"{resolved:08X}",
        "unique_matching_call_count": 1,
    }


def build_runtime_report(
    *,
    rom_sha256: str,
    symbol_sha256: str,
    sessions: list[dict[str, Any]],
    negative_session: dict[str, Any],
    provenance: dict[str, Any],
    replay_path: str,
) -> dict[str, Any]:
    """Evaluate observed sessions without rewriting any expected outcome."""
    if len(sessions) != 2:
        raise EvidenceFailure(
            f"exactly two sessions are required; got {len(sessions)}"
        )
    all_sessions = [*sessions, negative_session]
    transcript = [
        line
        for session in all_sessions
        for line in session.get("_transcript_lines", [])
    ]
    write_packets = debugger_write_packets(transcript)
    checks: list[dict[str, Any]] = []

    checks.append(
        _check(
            "three_fresh_independent_sessions",
            len(
                {
                    item["metadata"]["process_id"]
                    for item in all_sessions
                }
            )
            == 3
            and len(
                {
                    item["metadata"]["runtime_copy"]["temporary_directory"]
                    for item in all_sessions
                }
            )
            == 3,
            "two parity runs and one zero-echo negative run used distinct "
            "processes and portable runtime copies",
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
            "isolated_standard_three_button_input",
            all(
                item["metadata"]["controller_profile"].get("status")
                == "pass"
                and item["metadata"]["controller_profile"].get(
                    "port_1_assignment"
                )
                == "gamepad3.1"
                and item["metadata"]["controller_profile"]
                .get("session_isolation", {})
                .get("status")
                == "pass"
                and item["metadata"]["input_transport"]
                == "targeted_PostMessageW"
                for item in all_sessions
            ),
            "all three sessions used isolated gamepad3.1 profiles",
        )
    )
    checks.append(
        _check(
            "fixture_symbol_binds_directory_index_2",
            provenance.get("fixture", {}).get("debug_start_encounter") == 2
            and all(
                item["metadata"]["symbol_map_sha256"] == symbol_sha256
                for item in all_sessions
            ),
            "DEBUG_START_ENCOUNTER is 2 and every observation uses the "
            "matching symbol map",
        )
    )
    checks.append(
        _check(
            "dual_breakpoint_rom_binding",
            provenance.get("pre_step_rom_binding", {}).get("status")
            == "pass"
            and all(
                item["metadata"].get("pre_step_breakpoint_symbol")
                == "MainLoopGameplayStepCall"
                and item["metadata"].get("drive_protocol", "").startswith(
                    "validate read-only d0"
                )
                and item["metadata"]
                .get("dual_breakpoint_transcript", {})
                .get("status")
                == "pass"
                for item in all_sessions
            ),
            "the PRE stop is ROM-bound to the unique bsr.w GameplayStep and "
            "every session enforces PRE-input/POST-state ordering",
        )
    )
    checks.append(
        _check(
            "b03_campaign_abi_v3_initialized",
            all(
                initialization_ok(item["snapshots"]["initial"])
                for item in all_sessions
            ),
            "directory 2 / stage 103, echo cap 2, counts 6/3/4, completion "
            "4/state/$1007, canonical spawn/rectangles/objects, and derived "
            "B03 pointers are live in all sessions",
        )
    )
    checks.append(
        _check(
            "initial_projection_schema_crc_and_tail",
            all(
                item["snapshots"]["initial"]["projection"][
                    "published_crc_matches"
                ]
                and item["snapshots"]["initial"]["projection"][
                    "tail_matches_live_vocabulary"
                ]
                is True
                and item["snapshots"]["initial"]["projection"]["bounds_valid"]
                for item in all_sessions
            ),
            "all initial projections are 3894-byte schema 2 images with "
            "independently checked CRC and exact 256-byte campaign tail",
        )
    )
    checks.append(
        _check(
            "no_fatal_contract_or_pool_overflow",
            all(
                snapshot["engine"]["last_error"] == 0
                and snapshot["engine"]["overflow_flags"] == 0
                and snapshot["vocabulary"]["overflow_flags"] == 0
                and not snapshot["vocabulary"]["campaign"]["flags"]
                & VOCAB_CAMPAIGN_FLAG_CONTRACT_ERROR
                for item in all_sessions
                for snapshot in item["snapshots"].values()
            ),
            "every captured checkpoint has zero engine error, zero engine/"
            "vocabulary overflow, and no campaign contract-error flag",
        )
    )
    checks.append(
        _check(
            "actual_guest_input_schedules",
            all(
                bytes(item["attempt_inputs"]["0"]) == ATTEMPT_0_INPUTS
                and bytes(item["attempt_inputs"]["1"]) == ATTEMPT_1_INPUTS
                and bytes(item["attempt_inputs"]["2"]) == ATTEMPT_2_INPUTS
                and bytes(item["commanded_inputs"]["0"])
                == ATTEMPT_0_INPUTS
                and bytes(item["commanded_inputs"]["1"])
                == ATTEMPT_1_INPUTS
                and bytes(item["commanded_inputs"]["2"])
                == ATTEMPT_2_INPUTS
                and bytes(item["pre_step_polled_inputs"]["0"])
                == ATTEMPT_0_INPUTS
                and bytes(item["pre_step_polled_inputs"]["1"])
                == ATTEMPT_1_INPUTS
                and bytes(item["pre_step_polled_inputs"]["2"])
                == ATTEMPT_2_INPUTS
                and all(
                    len(item["post_step_observed_inputs"][str(index)])
                    == len(expected_attempt_inputs(index))
                    and all(
                        not value
                        & ~(
                            INPUT_UP
                            | INPUT_DOWN
                            | INPUT_LEFT
                            | INPUT_RIGHT
                            | INPUT_A
                            | INPUT_B
                            | INPUT_C
                        )
                        for value in item[
                            "post_step_observed_inputs"
                        ][str(index)]
                    )
                    for index in range(3)
                )
                for item in sessions
            ),
            "all 720/720/292 commanded masks, pre-step INPUT_HELD/d0 reads, "
            "and post-step ENGINE_INPUT_HELD bytes match; bounded post-step "
            "polls are retained as non-authoritative observations",
        )
    )
    checks.append(
        _check(
            "automatic_timeout_commit_transitions",
            all(
                len(item["timeout_commits"]) == 2
                and [
                    transition["attempt"]
                    for transition in item["timeout_commits"]
                ]
                == [0, 1]
                and all(
                    transition["method"]
                    == "automatic 720-authoritative-step timeout"
                    and transition["observed_post_step_attempt"]
                    == transition["attempt"] + 1
                    and transition["observed_post_step_tick"] == 0
                    and transition["authoritative_frames_consumed"] == 720
                    and transition["consumed_input_source"].startswith(
                        "read-only d0"
                    )
                    and not transition[
                        "guest_memory_register_pc_writes"
                    ]
                    for transition in item["timeout_commits"]
                )
                for item in sessions
            ),
            "the timeout frame is represented by the next attempt/tick-zero "
            "post-step in both fresh parity sessions",
        )
    )
    checks.append(
        _check(
            "actual_zero_echo_negative_input_schedule",
            bytes(negative_session["negative_inputs"]) == NEGATIVE_INPUTS
            and bytes(negative_session["negative_commanded_inputs"])
            == NEGATIVE_INPUTS
            and bytes(
                negative_session["negative_pre_step_polled_inputs"]
            )
            == NEGATIVE_INPUTS
            and len(
                negative_session["negative_post_step_observed_inputs"]
            )
            == len(NEGATIVE_INPUTS)
            and all(
                not value
                & ~(
                    INPUT_UP
                    | INPUT_DOWN
                    | INPUT_LEFT
                    | INPUT_RIGHT
                    | INPUT_A
                    | INPUT_B
                    | INPUT_C
                )
                for value in negative_session[
                    "negative_post_step_observed_inputs"
                ]
            ),
            "all 240 commanded, pre-step INPUT_HELD/d0, and post-step "
            "ENGINE_INPUT_HELD bytes match in the fresh zero-echo route",
        )
    )

    facts = [_scenario_facts(item) for item in sessions]
    required_scenario = (
        "timed_switch_activated",
        "first_timeout_echo_committed",
        "security_node_exposed",
        "pressure_echo_holds_switch",
        "two_timeout_echoes_committed",
        "node_damage_owned_by_echo_replay",
        "current_blocked_west_until_open",
        "timed_door_and_commit_unlock_opened",
        "current_dashes_through_open_gate",
        "current_only_completion",
    )
    checks.append(
        _check(
            "live_two_echo_b03_scenario",
            all(
                all(session_facts[name] for name in required_scenario)
                for session_facts in facts
            ),
            "two exact 720-step timeout commits create the echoes; echo-owned "
            "node hits, closed-leaf blocking, open-leaf traversal, and "
            "current-only completion are observed",
        )
    )
    negative_ok = _negative_zero_echo_ok(negative_session)
    checks.append(
        _check(
            "zero_echo_completion_rejected",
            negative_ok,
            "a fresh zero-echo run reached B03_COMMIT and made one real B "
            "attempt, but the locked dependency gate left state/timer $0000 "
            "and completion remained absent",
        )
    )

    checkpoint_comparison = []
    parity = True
    for key in CHECKPOINT_KEYS:
        left = sessions[0]["snapshots"][key]
        right = sessions[1]["snapshots"][key]
        matches = _snapshot_raw_bundle(left) == _snapshot_raw_bundle(right)
        parity = parity and matches
        checkpoint_comparison.append(
            {
                "label": key,
                "attempt": left["engine"]["attempt"],
                "tick": left["engine"]["tick"],
                "projection_sha256": left["projection"]["sha256"],
                "raw_bundle_sha256": _sha256_bytes(
                    _snapshot_raw_bundle(left)
                ),
                "bytes_match": matches,
            }
        )
    checks.append(
        _check(
            "two_session_deterministic_parity",
            parity,
            "engine/gameplay/vocabulary/descriptor/rect/pool/actor/input/"
            "replay/projection bytes match at every bounded checkpoint",
        )
    )

    terminal_replays = [
        item["snapshots"]["terminal"]["_replay_bytes"] for item in sessions
    ]
    replay_ok = terminal_replays[0] == terminal_replays[1]
    for item in sessions:
        terminal = item["snapshots"]["terminal"]
        replay_ok = (
            replay_ok
            and _descriptor_for_bank(terminal, 6)["length"]
            == len(ATTEMPT_0_INPUTS)
            and _descriptor_for_bank(terminal, 0)["length"]
            == len(ATTEMPT_1_INPUTS)
            and _descriptor_for_bank(terminal, 6)["crc32_iso_hdlc"]
            == crc32_iso_hdlc(ATTEMPT_0_INPUTS)
            and _descriptor_for_bank(terminal, 0)["crc32_iso_hdlc"]
            == crc32_iso_hdlc(ATTEMPT_1_INPUTS)
            and _descriptor_for_bank(terminal, 1)["length"]
            == len(ATTEMPT_2_INPUTS)
            and _descriptor_for_bank(terminal, 1)["flags"]
            & REPLAY_FLAG_CURRENT
            and _replay_bank_bytes(terminal, 6) == ATTEMPT_0_INPUTS
            and _replay_bank_bytes(terminal, 0) == ATTEMPT_1_INPUTS
            and _replay_bank_bytes(terminal, 1)[
                : len(ATTEMPT_2_INPUTS)
            ]
            == ATTEMPT_2_INPUTS
            and _replay_bank_bytes(terminal, 1)[
                len(ATTEMPT_2_INPUTS) :
            ]
            == bytes(720 - len(ATTEMPT_2_INPUTS))
        )
    checks.append(
        _check(
            "guest_replay_ram_binds_all_three_attempts",
            bool(replay_ok),
            "terminal replay RAM contains two 720-byte timeout echoes plus "
            "the 292-byte current attempt and matches across sessions",
        )
    )

    primary_passed = all(item["status"] == "pass" for item in checks)
    acceptance = [
        {
            "claim": name,
            "status": (
                "pass"
                if all(session_facts[name] for session_facts in facts)
                else "incomplete"
            ),
        }
        for name in (
            "timed_switch_activated",
            "first_timeout_echo_committed",
            "two_timeout_echoes_committed",
            "security_node_exposed",
            "pressure_echo_holds_switch",
            "node_damage_owned_by_echo_replay",
            "current_blocked_west_until_open",
            "timed_door_and_commit_unlock_opened",
            "current_dashes_through_open_gate",
            "current_only_completion",
        )
    ]
    acceptance.append(
        {
            "claim": "zero_echo_completion_rejected",
            "status": "pass" if negative_ok else "incomplete",
        }
    )
    complete_acceptance = all(
        item["status"] == "pass" for item in acceptance
    )

    terminal_replay_sha = (
        _sha256_bytes(terminal_replays[0]) if terminal_replays else ""
    )
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone5/B03",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if primary_passed else "fail",
        "acceptance_status": (
            "pass" if complete_acceptance else "incomplete"
        ),
        "rom_sha256": rom_sha256,
        "symbol_map_sha256": symbol_sha256,
        "checks": checks,
        "acceptance": acceptance,
        "checkpoint_comparison": checkpoint_comparison,
        "schedule": {
            "input_observation_semantics": (
                "At PRE step N, read-only d0 and INPUT_HELD must match the "
                "commanded mask N. PRE is removed while d0 is frozen, then "
                "targeted PostMessageW releases removed keys, reasserts "
                "each desired held key for N+1, and settles before N "
                "executes into POST; PRE is reinserted there. At POST N, "
                "ENGINE_INPUT_HELD must corroborate N. Post-step INPUT_STATE "
                "is retained only as an observation because a VBlank during "
                "N may already poll N+1."
            ),
            "attempt_lengths": [
                len(ATTEMPT_0_INPUTS),
                len(ATTEMPT_1_INPUTS),
                len(ATTEMPT_2_INPUTS),
            ],
            "attempt_0": (
                "Up x46, Right x21 onto B03_SWITCH_A, neutral x653, "
                "automatic authoritative timeout commit"
            ),
            "attempt_1": (
                "Down x43, Right x11, B on B03_SWITCH_B, route around the "
                "lower wall, A at ticks 183/188/193, neutral to automatic "
                "authoritative timeout commit"
            ),
            "attempt_2": (
                "Down x3, Right to closed leaf, neutral through echo node "
                "damage, Right+C at tick 198 through the open 16x32 leaf, "
                "fully clear the leaf under sustained dash at tick 201, "
                "Right to B03_COMMIT, then uninterrupted B on ticks 273..292"
            ),
            "zero_echo_negative": (
                "route around the lower wall to B03_COMMIT, B at tick 231, "
                "neutral through tick 240"
            ),
            "guest_memory_register_pc_writes": False,
        },
        "guest_replay_ram": {
            "path": replay_path,
            "bytes": REPLAY_TIMELINE_BYTES,
            "sha256": terminal_replay_sha,
            "classification": (
                "exact terminal $FF0000-$FF13FF replay/timeline RAM from "
                "session 1; session 2 matched byte-for-byte"
            ),
        },
        "sessions": [_public(item) for item in sessions],
        "negative_session": _public(negative_session),
        "provenance": provenance,
        "debugger_write_packets": write_packets,
        "evidence_limits": [
            "The primary pass proves the already-built Debug B03 fixture "
            "boots directory 2/stage 103 with the generated ABI-v3 profile, "
            "canonical authored state, valid initial projection, real-pad "
            "replays, and deterministic two-session parity.",
            "Node destruction is accepted only when all three owner-tagged "
            "echo projectiles are observed immediately before their exact "
            "3-to-2-to-1-to-0 health transitions in both parity sessions.",
            "Gate traversal is accepted only when the current actor is "
            "observed blocked at x=156 while vertically overlapping the "
            "closed 16x32 leaf, then inside and fully east of that same leaf "
            "while its live state is open.",
            "The completion claim additionally requires the final B edge to "
            "come from physical current-actor slot 6 and target B03_COMMIT.",
            "The separate zero-echo run proves merely reaching B03_COMMIT "
            "and making a one-step B attempt cannot begin the locked hold or "
            "satisfy completion value $1007.",
            "The positive route completes at tick 292 after a proven "
            "20-step uninterrupted current-only B hold and does not exercise "
            "B03_SHUTTER_FIELD's later warning, closing, or damage tail; no "
            "claim about those transitions is made here.",
            "No visual, audio, performance, PAL, save, Release-ROM, or full "
            "campaign claim is made.",
        ],
    }


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
        gameplay_raw=_read_region(
            remote, GAMEPLAY_CONTROL_BASE, GAMEPLAY_CONTROL_USED_BYTES
        ),
        vocabulary_raw=_read_region(
            remote, VOCAB_STATE_BASE, VOCAB_STATE_BYTES
        ),
        descriptor_raw=_read_region(
            remote, ENGINE_STAGING_DESCRIPTOR, ENCOUNTER_DESCRIPTOR_BYTES
        ),
        rectangle_raw=_read_region(
            remote,
            CANONICAL_RECTS,
            len(B03_RECTS) * STATIC_RECT_BYTES,
        ),
        device_raw=_read_region(
            remote, DEVICE_POOL, DEVICE_CAPACITY * OBJECT_BYTES
        ),
        projectile_raw=_read_region(
            remote,
            PROJECTILE_POOL,
            PROJECTILE_CAPACITY * OBJECT_BYTES,
        ),
        hazard_raw=_read_region(
            remote, HAZARD_POOL, HAZARD_CAPACITY * OBJECT_BYTES
        ),
        actor_raw=_read_region(
            remote, ENGINE_ACTOR_BASE, ACTOR_CAPACITY * ACTOR_BYTES
        ),
        input_raw=_read_region(
            remote, INPUT_STATE_BASE, INPUT_STATE_BYTES
        ),
        replay_raw=_read_region(
            remote, REPLAY_BANK_BASE, REPLAY_TIMELINE_BYTES
        ),
        projection_raw=_read_region(
            remote, ENGINE_PROJECTION_BASE, CAMPAIGN_PROJECTION_BYTES
        ),
        symbols=symbols,
    )


def _post_key(
    window: int,
    key: tuple[str, int, int, bool],
    *,
    pressed: bool,
) -> None:
    _name, virtual_key, scan_code, extended = key
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=pressed,
        extended=extended,
    )


def _set_gameplay_mask(
    window: int,
    desired: int,
    pressed: set[str],
    events: list[str],
) -> None:
    """Stage the next real-pad mask once while the guest is stopped.

    Reassert desired held keys because a host-side release can invalidate our
    last-posted set. This is preventative delivery, never a retry after the
    authoritative PRE/POST proof has failed. At most seven key messages are
    posted: releases first, then one key-down for each desired key.
    """
    allowed = INPUT_UP | INPUT_DOWN | INPUT_LEFT | INPUT_RIGHT | INPUT_A | INPUT_B | INPUT_C
    if desired & ~allowed:
        raise EvidenceFailure(f"invalid gameplay input mask ${desired:02X}")
    changed = False
    for name, virtual_key, scan_code, extended, bit in KEYS:
        if name in pressed and not desired & bit:
            _post_key(
                window,
                (name, virtual_key, scan_code, extended),
                pressed=False,
            )
            pressed.remove(name)
            events.append(f"{name}:up")
            changed = True
    for name, virtual_key, scan_code, extended, bit in KEYS:
        if desired & bit:
            event = "reassert" if name in pressed else "down"
            _post_key(
                window,
                (name, virtual_key, scan_code, extended),
                pressed=True,
            )
            pressed.add(name)
            events.append(f"{name}:{event}")
            changed = True
    if changed:
        # The 68000 is stopped at a debugger boundary.  This bounded host-only
        # settle lets BlastEm's window thread consume the posted transitions
        # and held-key reassertions before execution resumes toward PRE.
        time.sleep(INPUT_EDGE_SETTLE_SECONDS)


def _read_register_u32(
    remote: GdbRemote,
    register: int,
    *,
    label: str,
) -> int:
    response = remote.request(f"p{register:x}")
    if len(response) != 8:
        raise EvidenceFailure(
            f"{label} register p{register:x} returned {response!r}; "
            "8 hexadecimal digits required"
        )
    try:
        return int(response, 16)
    except ValueError as error:
        raise EvidenceFailure(
            f"{label} register p{register:x} returned non-hex {response!r}"
        ) from error


def _continue_to_pre_step(
    remote: GdbRemote,
    *,
    pre_step: int,
    attempt: int,
    expected_tick: int,
    desired: int,
    session_id: str,
) -> int:
    """Prove the exact input at the instruction before GameplayStep."""
    require_stop(remote.request("c"))
    label = (
        f"{session_id} attempt {attempt} pre-step tick {expected_tick}"
    )
    pc = _read_register_u32(remote, 0x11, label=label)
    d0 = _read_register_u32(remote, 0, label=label)
    control = parse_engine_control(
        _read_region(
            remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
        )
    )
    input_state = parse_input_state(
        _read_region(remote, INPUT_STATE_BASE, INPUT_STATE_BYTES)
    )
    if pc != pre_step:
        raise EvidenceFailure(
            f"{label} stopped at PC ${pc:08X}; ${pre_step:08X} required"
        )
    if (
        control["state"] != ENGINE_STATE_ACTIVE
        or control["attempt"] != attempt
        or control["tick"] != expected_tick - 1
    ):
        raise EvidenceFailure(
            f"{label} control is state {control['state']} "
            f"{control['attempt']}/{control['tick']}; active "
            f"{attempt}/{expected_tick - 1} required"
        )
    if input_state["held"] != desired or d0 != desired:
        raise EvidenceFailure(
            f"{label} d0/INPUT_HELD are ${d0:08X}/"
            f"${input_state['held']:02X}; ${desired:02X} required"
        )
    # Remove PRE before resuming.  If a VBlank arrives before the stopped bsr
    # executes, BlastEm would otherwise re-trigger this same software
    # breakpoint instead of reaching POST even though no step was consumed.
    if remote.request(f"z0,{pre_step:x},2") != "OK":
        raise EvidenceFailure(f"{label} PRE breakpoint removal failed")
    return d0


def _continue_to_post_step(
    remote: GdbRemote,
    *,
    pre_step: int,
    post_step: int,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require the matching post-step stop and return its read-only state."""
    require_stop(remote.request("c"))
    pc = _read_register_u32(remote, 0x11, label=label)
    if pc != post_step:
        raise EvidenceFailure(
            f"{label} stopped at PC ${pc:08X}; ${post_step:08X} required"
        )
    if remote.request(f"Z0,{pre_step:x},2") != "OK":
        raise EvidenceFailure(f"{label} PRE breakpoint reinsertion failed")
    control = parse_engine_control(
        _read_region(
            remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
        )
    )
    input_state = parse_input_state(
        _read_region(remote, INPUT_STATE_BASE, INPUT_STATE_BYTES)
    )
    return control, input_state


def audit_dual_breakpoint_transcript(
    lines: list[str],
    *,
    pre_step: int,
    post_step: int,
    expected_steps: int,
) -> dict[str, Any]:
    """Verify every PRE/POST stop and breakpoint toggle in transcript order."""
    register_reads = [
        (index, lines[index + 1].upper())
        for index, line in enumerate(lines[:-1])
        if line == "> p11" and lines[index + 1].startswith("< ")
    ]
    expected_registers = [
        value
        for _step in range(expected_steps)
        for value in (f"< {pre_step:08X}", f"< {post_step:08X}")
    ]
    alternating = [
        value for _index, value in register_reads
    ] == expected_registers
    toggle_order = alternating
    pre_remove = f"> z0,{pre_step:x},2"
    pre_insert = f"> Z0,{pre_step:x},2"
    if alternating:
        for step in range(expected_steps):
            pre_index = register_reads[step * 2][0]
            post_index = register_reads[step * 2 + 1][0]
            pre_segment = lines[pre_index:post_index]
            next_pre_index = (
                register_reads[step * 2 + 2][0]
                if step + 1 < expected_steps
                else len(lines)
            )
            post_segment = lines[post_index:next_pre_index]
            remove_positions = [
                index
                for index, line in enumerate(pre_segment)
                if line == pre_remove
            ]
            continue_positions = [
                index
                for index, line in enumerate(pre_segment)
                if line == "> c"
            ]
            insert_positions = [
                index
                for index, line in enumerate(post_segment)
                if line == pre_insert
            ]
            if (
                pre_index == 0
                or lines[pre_index - 1] not in {"< S05"}
                or post_index == 0
                or lines[post_index - 1] not in {"< S05"}
                or pre_segment.count("> p0") != 1
                or len(remove_positions) != 1
                or len(continue_positions) != 1
                or remove_positions[0] >= continue_positions[0]
                or remove_positions[0] + 1 >= len(pre_segment)
                or pre_segment[remove_positions[0] + 1] != "< OK"
                or len(insert_positions) != 1
                or insert_positions[0] + 1 >= len(post_segment)
                or post_segment[insert_positions[0] + 1] != "< OK"
            ):
                toggle_order = False
                break
    return {
        "status": (
            "pass"
            if alternating
            and toggle_order
            and len(register_reads) == expected_steps * 2
            else "fail"
        ),
        "expected_steps": expected_steps,
        "pre_stops": sum(
            value == f"< {pre_step:08X}"
            for _index, value in register_reads
        ),
        "post_stops": sum(
            value == f"< {post_step:08X}"
            for _index, value in register_reads
        ),
        "alternating_exact_pc_order": alternating,
        "remove_pre_then_continue_reinsert_pre": toggle_order,
    }


def _observe_attempt(
    remote: GdbRemote,
    *,
    window: int,
    attempt: int,
    schedule: bytes,
    pressed: set[str],
    events: list[str],
    symbols: dict[str, int],
    snapshots: dict[str, dict[str, Any]],
    session_id: str,
) -> tuple[list[int], list[int], list[int]]:
    consumed: list[int] = []
    pre_step_polled: list[int] = []
    post_step_observed: list[int] = []
    capture_ticks = set(ATTEMPT_CAPTURE_TICKS[attempt])
    for expected_tick, desired in enumerate(schedule, start=1):
        consumed_input = _continue_to_pre_step(
            remote,
            pre_step=symbols["MainLoopGameplayStepCall"],
            attempt=attempt,
            expected_tick=expected_tick,
            desired=desired,
            session_id=session_id,
        )
        consumed.append(consumed_input)
        pre_step_polled.append(desired)

        # d0 is already frozen for tick N at the pre-step breakpoint, so the
        # next mask can be delivered now without changing tick N.  Doing this
        # before executing N guarantees a VBlank sampled during N observes the
        # mask intended for N+1 rather than making a queued token stale.
        next_desired = (
            schedule[expected_tick]
            if expected_tick < len(schedule)
            else 0
        )
        _set_gameplay_mask(window, next_desired, pressed, events)
        label = (
            f"{session_id} attempt {attempt} post-step "
            f"tick {expected_tick}"
        )
        control, input_state = _continue_to_post_step(
            remote,
            pre_step=symbols["MainLoopGameplayStepCall"],
            post_step=symbols["MainLoopPostGameplayStep"],
            label=label,
        )
        if (
            control["attempt"] != attempt
            or control["tick"] != expected_tick
            or (
                expected_tick < len(schedule)
                and control["state"] != ENGINE_STATE_ACTIVE
            )
            or (
                expected_tick == len(schedule)
                and control["state"] != ENGINE_STATE_TERMINAL
            )
        ):
            raise EvidenceFailure(
                f"{label} observed state/attempt/tick "
                f"{control['state']}/{control['attempt']}/"
                f"{control['tick']}; expected terminal only on final "
                f"{attempt}/{expected_tick}"
            )
        if control["input_held"] != desired:
            raise EvidenceFailure(
                f"{session_id} attempt {attempt} tick {expected_tick} "
                "post-step ENGINE_INPUT_HELD "
                f"${control['input_held']:02X}; ${desired:02X} required"
            )
        post_step_observed.append(input_state["held"])
        if expected_tick in capture_ticks:
            key = (
                "terminal"
                if attempt == 2 and expected_tick == len(schedule)
                else f"attempt_{attempt}_tick_{expected_tick}"
            )
            snapshots[key] = _capture_snapshot(
                remote,
                label=key,
                symbols=symbols,
            )
    return consumed, pre_step_polled, post_step_observed


def _observe_timeout_attempt(
    remote: GdbRemote,
    *,
    window: int,
    attempt: int,
    schedule: bytes,
    next_attempt_first_input: int,
    pressed: set[str],
    events: list[str],
    symbols: dict[str, int],
    snapshots: dict[str, dict[str, Any]],
    session_id: str,
) -> tuple[list[int], list[int], list[int], dict[str, Any]]:
    """Prove 720 pre-steps and the final timeout/reset post-step."""
    if len(schedule) != 720:
        raise EvidenceFailure(
            f"{session_id} timeout attempt {attempt} has "
            f"{len(schedule)} bytes; 720 required"
        )
    consumed: list[int] = []
    pre_step_polled: list[int] = []
    post_step_observed: list[int] = []
    capture_ticks = set(ATTEMPT_CAPTURE_TICKS[attempt])
    for expected_tick in range(1, len(schedule) + 1):
        desired = schedule[expected_tick - 1]
        consumed_input = _continue_to_pre_step(
            remote,
            pre_step=symbols["MainLoopGameplayStepCall"],
            attempt=attempt,
            expected_tick=expected_tick,
            desired=desired,
            session_id=session_id,
        )
        consumed.append(consumed_input)
        pre_step_polled.append(desired)

        next_desired = (
            schedule[expected_tick]
            if expected_tick < len(schedule)
            else next_attempt_first_input
        )
        _set_gameplay_mask(window, next_desired, pressed, events)
        label = (
            f"{session_id} timeout attempt {attempt} post-step "
            f"tick {expected_tick}"
        )
        control, input_state = _continue_to_post_step(
            remote,
            pre_step=symbols["MainLoopGameplayStepCall"],
            post_step=symbols["MainLoopPostGameplayStep"],
            label=label,
        )
        post_step_observed.append(input_state["held"])

        if expected_tick < len(schedule):
            if (
                control["state"] != ENGINE_STATE_ACTIVE
                or control["attempt"] != attempt
                or control["tick"] != expected_tick
                or control["input_held"] != desired
            ):
                raise EvidenceFailure(
                    f"{label} control state/attempt/tick/input is "
                    f"{control['state']}/{control['attempt']}/"
                    f"{control['tick']}/${control['input_held']:02X}; "
                    f"{ENGINE_STATE_ACTIVE}/{attempt}/{expected_tick}/"
                    f"${desired:02X} required"
                )
        else:
            if (
                control["state"] != ENGINE_STATE_ACTIVE
                or control["attempt"] != attempt + 1
                or control["tick"] != 0
            ):
                raise EvidenceFailure(
                    f"{label} reset to state {control['state']} "
                    f"{control['attempt']}/{control['tick']}; active "
                    f"{attempt + 1}/0 required"
                )

        if expected_tick in capture_ticks:
            key = f"attempt_{attempt}_tick_{expected_tick}"
            snapshots[key] = _capture_snapshot(
                remote,
                label=key,
                symbols=symbols,
            )

    key = f"post_timeout_{attempt + 1}"
    snapshots[key] = _capture_snapshot(
        remote,
        label=key,
        symbols=symbols,
    )
    reset = snapshots[key]
    timeline = reset["replay_timeline"]["timeline"]
    committed_bank = 6 if attempt == 0 else 0
    descriptor = _descriptor_for_bank(reset, committed_bank)
    expected_order = [6] if attempt == 0 else [6, 0]
    if (
        timeline["echo_count"] != attempt + 1
        or timeline["echo_order"][: attempt + 1] != expected_order
        or descriptor["length"] != 720
        or descriptor["crc32_iso_hdlc"] != crc32_iso_hdlc(schedule)
        or _replay_bank_bytes(reset, committed_bank) != schedule
        or descriptor["flags"] & (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
        != (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
    ):
        raise EvidenceFailure(
            f"{session_id} timeout attempt {attempt} did not publish the "
            "expected 720-byte echo"
        )
    return consumed, pre_step_polled, post_step_observed, {
        "attempt": attempt,
        "method": "automatic 720-authoritative-step timeout",
        "observed_post_step_attempt": attempt + 1,
        "observed_post_step_tick": 0,
        "authoritative_frames_consumed": 720,
        "consumed_input_source": (
            "read-only d0 and INPUT_HELD at MainLoopGameplayStepCall for "
            "steps 1..720; ENGINE_INPUT_HELD corroborates steps 1..719 and "
            "the finalized replay bank corroborates all 720"
        ),
        "guest_memory_register_pc_writes": False,
    }


def _observe_negative(
    remote: GdbRemote,
    *,
    window: int,
    schedule: bytes,
    pressed: set[str],
    events: list[str],
    symbols: dict[str, int],
    snapshots: dict[str, dict[str, Any]],
    session_id: str,
) -> tuple[list[int], list[int], list[int]]:
    consumed: list[int] = []
    pre_step_polled: list[int] = []
    post_step_observed: list[int] = []
    capture_ticks = set(NEGATIVE_CAPTURE_TICKS)
    for expected_tick, desired in enumerate(schedule, start=1):
        consumed_input = _continue_to_pre_step(
            remote,
            pre_step=symbols["MainLoopGameplayStepCall"],
            attempt=0,
            expected_tick=expected_tick,
            desired=desired,
            session_id=session_id,
        )
        consumed.append(consumed_input)
        pre_step_polled.append(desired)
        next_desired = (
            schedule[expected_tick]
            if expected_tick < len(schedule)
            else 0
        )
        _set_gameplay_mask(window, next_desired, pressed, events)
        label = f"{session_id} negative post-step tick {expected_tick}"
        control, input_state = _continue_to_post_step(
            remote,
            pre_step=symbols["MainLoopGameplayStepCall"],
            post_step=symbols["MainLoopPostGameplayStep"],
            label=label,
        )
        if (
            control["attempt"] != 0
            or control["tick"] != expected_tick
            or control["state"] != ENGINE_STATE_ACTIVE
            or control["input_held"] != desired
        ):
            raise EvidenceFailure(
                f"{label} control state/attempt/tick/input is "
                f"{control['state']}/{control['attempt']}/"
                f"{control['tick']}/${control['input_held']:02X}; "
                f"{ENGINE_STATE_ACTIVE}/0/{expected_tick}/"
                f"${desired:02X} required"
            )
        post_step_observed.append(input_state["held"])
        if expected_tick in capture_ticks:
            key = f"negative_tick_{expected_tick}"
            snapshots[key] = _capture_snapshot(
                remote,
                label=key,
                symbols=symbols,
            )
    return consumed, pre_step_polled, post_step_observed


def _launch_owned_emulator(
    command: list[str],
    *,
    cwd: Path,
    transcript: list[str],
    attempts: int = 3,
) -> subprocess.Popen[bytes]:
    """Start the owned runtime, tolerating only transient execute denial."""
    if attempts < 1:
        raise ValueError("launch attempts must be positive")
    for attempt in range(1, attempts + 1):
        try:
            return subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except PermissionError as error:
            transcript.append(
                "# host launch permission retry "
                f"{attempt}/{attempts}: "
                f"{getattr(error, 'winerror', None) or error.errno}"
            )
            if attempt == attempts:
                raise
            time.sleep(0.25 * attempt)
    raise AssertionError("unreachable launch retry state")


def _shutdown_owned_emulator(
    remote: GdbRemote | None,
    process: subprocess.Popen[bytes] | None,
    transcript: list[str],
) -> None:
    """Stop only the process launched for this session and its child tree."""
    shutdown_emulator(remote, process, transcript)
    if process is None or process.poll() is not None:
        return
    if os.name != "nt":
        raise EvidenceFailure(
            f"owned emulator process {process.pid} survived shutdown"
        )

    try:
        result = subprocess.run(
            [
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
        )
    except Exception as error:
        raise EvidenceFailure(
            f"owned emulator process {process.pid} cleanup failed: "
            f"{type(error).__name__}: {error}"
        ) from error
    transcript.append(
        "# host cleanup taskkill exact owned pid "
        f"{process.pid} rc={result.returncode}"
    )
    try:
        process.wait(timeout=10.0)
    except subprocess.TimeoutExpired as error:
        raise EvidenceFailure(
            f"owned emulator process {process.pid} survived taskkill"
        ) from error
    if process.poll() is None:
        raise EvidenceFailure(
            f"owned emulator process {process.pid} remained live"
        )


def _remove_owned_session_tree(
    path: Path,
    transcript: list[str],
    *,
    attempts: int = 30,
) -> None:
    """Remove one exact owned session tree after transient Windows locks."""
    if attempts < 1:
        raise ValueError("cleanup attempts must be positive")

    def make_writable(
        function: Any, item: str, _error: tuple[Any, Any, Any]
    ) -> None:
        os.chmod(item, stat.S_IWRITE)
        function(item)

    for attempt in range(1, attempts + 1):
        try:
            shutil.rmtree(path, onerror=make_writable)
            if attempt > 1:
                transcript.append(
                    "# host cleanup removed exact owned session tree "
                    f"after {attempt} attempts"
                )
            return
        except FileNotFoundError:
            return
        except OSError as error:
            if attempt == attempts:
                raise EvidenceFailure(
                    "could not remove exact owned session tree "
                    f"{path}: {type(error).__name__}: {error}"
                ) from error
            transcript.append(
                "# host cleanup transient tree retry "
                f"{attempt}/{attempts}: {type(error).__name__}"
            )
            time.sleep(min(0.1 * attempt, 0.5))


@contextmanager
def _owned_session_directory(
    *,
    prefix: str,
    transcript: list[str],
) -> Any:
    profile = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield profile
    finally:
        _remove_owned_session_tree(profile, transcript)


def _run_live_session(
    *,
    session_id: str,
    mode: str,
    rom: Path,
    symbol_path: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    if mode not in {"positive", "negative"}:
        raise ValueError(f"unsupported live-session mode {mode!r}")
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: set[str] = set()
    events: list[str] = []
    session: dict[str, Any] | None = None
    with _owned_session_directory(
        prefix=f"recursion94-m5-b03-{session_id}-",
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
            *LAUNCH_FLAGS,
            str(rom),
        ]
        try:
            process = _launch_owned_emulator(
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
            pre_step = symbols["MainLoopGameplayStepCall"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            window = find_process_window(process.pid, 20.0)
            first_input = (
                ATTEMPT_0_INPUTS[0]
                if mode == "positive"
                else NEGATIVE_INPUTS[0]
            )
            _set_gameplay_mask(
                window, first_input, pressed, events
            )
            snapshots = {
                "initial": _capture_snapshot(
                    remote,
                    label="initial",
                    symbols=symbols,
                )
            }
            if not initialization_ok(snapshots["initial"]):
                raise EvidenceFailure(
                    f"{session_id} did not initialize canonical B03"
                )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{pre_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} pre-step breakpoint insertion failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-step breakpoint insertion failed"
                )

            attempt_inputs: dict[str, list[int]] = {}
            pre_step_polled_inputs: dict[str, list[int]] = {}
            post_step_observed_inputs: dict[str, list[int]] = {}
            timeout_commits: list[dict[str, Any]] = []
            negative_inputs: list[int] = []
            negative_pre_step_polled_inputs: list[int] = []
            negative_post_step_observed_inputs: list[int] = []
            if mode == "positive":
                (
                    attempt_inputs["0"],
                    pre_step_polled_inputs["0"],
                    post_step_observed_inputs["0"],
                    timeout_0,
                ) = _observe_timeout_attempt(
                    remote,
                    window=window,
                    attempt=0,
                    schedule=ATTEMPT_0_INPUTS,
                    next_attempt_first_input=ATTEMPT_1_INPUTS[0],
                    pressed=pressed,
                    events=events,
                    symbols=symbols,
                    snapshots=snapshots,
                    session_id=session_id,
                )
                timeout_commits.append(timeout_0)
                (
                    attempt_inputs["1"],
                    pre_step_polled_inputs["1"],
                    post_step_observed_inputs["1"],
                    timeout_1,
                ) = _observe_timeout_attempt(
                    remote,
                    window=window,
                    attempt=1,
                    schedule=ATTEMPT_1_INPUTS,
                    next_attempt_first_input=ATTEMPT_2_INPUTS[0],
                    pressed=pressed,
                    events=events,
                    symbols=symbols,
                    snapshots=snapshots,
                    session_id=session_id,
                )
                timeout_commits.append(timeout_1)
                (
                    attempt_inputs["2"],
                    pre_step_polled_inputs["2"],
                    post_step_observed_inputs["2"],
                ) = _observe_attempt(
                    remote,
                    window=window,
                    attempt=2,
                    schedule=ATTEMPT_2_INPUTS,
                    pressed=pressed,
                    events=events,
                    symbols=symbols,
                    snapshots=snapshots,
                    session_id=session_id,
                )
                terminal = snapshots["terminal"]
                if (
                    terminal["engine"]["state"] != ENGINE_STATE_TERMINAL
                    or terminal["engine"]["terminal_reason"]
                    != GAMEPLAY_TERMINAL_PROGRAM_STABLE
                ):
                    raise EvidenceFailure(
                        f"{session_id} did not reach current-only "
                        "PROGRAM_STABLE"
                    )
            else:
                (
                    negative_inputs,
                    negative_pre_step_polled_inputs,
                    negative_post_step_observed_inputs,
                ) = _observe_negative(
                    remote,
                    window=window,
                    schedule=NEGATIVE_INPUTS,
                    pressed=pressed,
                    events=events,
                    symbols=symbols,
                    snapshots=snapshots,
                    session_id=session_id,
                )
            if remote.request(f"z0,{pre_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} pre-step breakpoint removal failed"
                )
            if remote.request(f"z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-step breakpoint removal failed"
                )

            session_lines = transcript[start_index:]
            expected_drive_steps = (
                sum(
                    len(expected_attempt_inputs(index))
                    for index in range(3)
                )
                if mode == "positive"
                else len(NEGATIVE_INPUTS)
            )
            dual_breakpoint_transcript = audit_dual_breakpoint_transcript(
                session_lines,
                pre_step=pre_step,
                post_step=post_step,
                expected_steps=expected_drive_steps,
            )
            if dual_breakpoint_transcript["status"] != "pass":
                raise EvidenceFailure(
                    f"{session_id} dual-breakpoint transcript order failed: "
                    f"{dual_breakpoint_transcript}"
                )
            session = {
                "session_id": session_id,
                "metadata": {
                    "process_id": process.pid,
                    "launch_flags": list(LAUNCH_FLAGS),
                    "initial_breakpoint_symbol": "VBlankInterrupt",
                    "initial_breakpoint": f"{vblank:08X}",
                    "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
                    "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
                    "pre_step_breakpoint_symbol": (
                        "MainLoopGameplayStepCall"
                    ),
                    "pre_step_breakpoint": f"{pre_step:08X}",
                    "step_breakpoint_symbol": "MainLoopPostGameplayStep",
                    "step_breakpoint": f"{post_step:08X}",
                    "drive_protocol": (
                        "validate read-only d0 and INPUT_HELD at PRE N; "
                        "remove PRE; release removed keys, reassert each "
                        "desired held key and settle mask N+1 while d0 is "
                        "frozen; execute N into POST; reinsert PRE; validate "
                        "ENGINE_INPUT_HELD"
                    ),
                    "dual_breakpoint_transcript": (
                        dual_breakpoint_transcript
                    ),
                    "input_edge_settle_milliseconds": (
                        INPUT_EDGE_SETTLE_SECONDS * 1000
                    ),
                    "session_mode": mode,
                    "symbol_map_sha256": sha256_file(symbol_path),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "input_transport": "targeted_PostMessageW",
                    "input_events": events,
                    "transcript_sha256": _sha256_bytes(
                        (
                            "\n".join(session_lines)
                            + ("\n" if session_lines else "")
                        ).encode("utf-8")
                    ),
                },
                "attempt_inputs": attempt_inputs,
                "commanded_inputs": (
                    {
                        "0": list(ATTEMPT_0_INPUTS),
                        "1": list(ATTEMPT_1_INPUTS),
                        "2": list(ATTEMPT_2_INPUTS),
                    }
                    if mode == "positive"
                    else {}
                ),
                "pre_step_polled_inputs": pre_step_polled_inputs,
                "post_step_observed_inputs": post_step_observed_inputs,
                "negative_inputs": negative_inputs,
                "negative_commanded_inputs": (
                    list(NEGATIVE_INPUTS) if mode == "negative" else []
                ),
                "negative_pre_step_polled_inputs": (
                    negative_pre_step_polled_inputs
                ),
                "negative_post_step_observed_inputs": (
                    negative_post_step_observed_inputs
                ),
                "timeout_commits": timeout_commits,
                "snapshots": snapshots,
                "_transcript_lines": session_lines,
            }
        finally:
            if window is not None:
                for name, virtual_key, scan_code, extended, _bit in KEYS:
                    if name in pressed:
                        try:
                            _post_key(
                                window,
                                (name, virtual_key, scan_code, extended),
                                pressed=False,
                            )
                            events.append(f"{name}:emergency-up")
                        except Exception as error:
                            transcript.append(
                                "! emergency key release failed: "
                                f"{type(error).__name__}: {error}"
                            )
            pressed.clear()
            _shutdown_owned_emulator(remote, process, transcript)
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
        "scope": "Milestone5/B03",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "acceptance_status": "incomplete",
        "rom_sha256": rom_sha256,
        "symbol_map_sha256": symbol_sha256,
        "checks": [_check("m5_b03_runtime_evidence_session", False, detail)],
        "error": detail,
        "provenance": provenance,
        "debugger_write_packets": debugger_write_packets(transcript),
        "evidence_limits": [
            "A failed capture proves no B03 runtime claim."
        ],
    }


def report_exit_code(report: dict[str, Any]) -> int:
    """Return success only for both evidence checks and full acceptance."""
    return (
        0
        if report.get("status") == "pass"
        and report.get("acceptance_status") == "pass"
        else 1
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", type=Path)
    parser.add_argument("--symbols", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--replay-ram", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-symbol-sha256")
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
        else root / "artifacts/reports/m5-b03-runtime.json"
    )
    trace_path = (
        args.trace.resolve()
        if args.trace
        else root / "artifacts/traces/m5-b03-runtime-gdb-transcript.log"
    )
    replay_path = (
        args.replay_ram.resolve()
        if args.replay_ram
        else root / "artifacts/replays/m5-b03-live-replay-ram.bin"
    )
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    sessions: list[dict[str, Any]] = []
    negative_session: dict[str, Any] | None = None
    provenance: dict[str, Any] = {}
    rom_sha256 = ""
    symbol_sha256 = ""
    report: dict[str, Any]

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live B03 runtime evidence requires native Windows BlastEm"
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
            (rom, "B03 Debug fixture ROM"),
            (symbol_path, "B03 Debug symbol map"),
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
                f"B03 ROM SHA-256 {rom_sha256} does not match "
                f"{args.expected_sha256.strip().lower()}"
            )
        if args.expected_symbol_sha256 and (
            symbol_sha256
            != args.expected_symbol_sha256.strip().lower()
        ):
            raise EvidenceFailure(
                f"B03 symbol SHA-256 {symbol_sha256} does not match "
                f"{args.expected_symbol_sha256.strip().lower()}"
            )

        controller_bindings = verify_controller_bindings(default_config)
        if controller_bindings["status"] != "pass":
            raise EvidenceFailure(
                "pinned default.cfg lacks standard three-button bindings"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"B03 symbol map is missing required symbols {missing}"
            )
        if symbols["DEBUG_START_ENCOUNTER"] != B03_DIRECTORY_INDEX:
            raise EvidenceFailure(
                "B03 fixture does not define DEBUG_START_ENCOUNTER=2"
            )
        pre_step_rom_binding = verify_pre_step_rom_binding(rom, symbols)
        user_config = verify_no_user_blastem_override()
        source_bindings = _source_bindings(root)
        provenance = {
            "fixture": {
                "debug_start_encounter": symbols["DEBUG_START_ENCOUNTER"],
                "expected_directory_index": B03_DIRECTORY_INDEX,
                "expected_numeric_stage_id": B03_STAGE_ID,
            },
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
                "hash_policy": "verified before and after all three sessions",
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
                **controller_bindings,
            },
            "user_config_preflight": user_config,
            "source_bindings": source_bindings,
            "pre_step_rom_binding": pre_step_rom_binding,
            "serialized_lock": lock_path.relative_to(root).as_posix(),
            "launch_flags": list(LAUNCH_FLAGS),
        }

        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            negative_session = _run_live_session(
                session_id="zero_echo_negative",
                mode="negative",
                rom=rom,
                symbol_path=symbol_path,
                emulator_directory=emulator_directory,
                default_config=default_config,
                symbols=symbols,
                timeout_seconds=args.timeout_seconds,
                transcript=transcript,
            )
            for number in (1, 2):
                sessions.append(
                    _run_live_session(
                        session_id=f"positive_session_{number}",
                        mode="positive",
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
            raise EvidenceFailure("B03 Debug ROM changed during capture")
        if sha256_file(symbol_path) != symbol_sha256:
            raise EvidenceFailure("B03 symbol map changed during capture")
        if sha256_file(emulator) != provenance["emulator"]["sha256"]:
            raise EvidenceFailure("pinned BlastEm changed during capture")
        if (
            sha256_file(default_config)
            != provenance["default_config"]["source_sha256"]
        ):
            raise EvidenceFailure("pinned default.cfg changed during capture")
        if _source_bindings(root) != provenance["source_bindings"]:
            raise EvidenceFailure(
                "B03 runtime/source binding changed during capture"
            )
        if negative_session is None:
            raise EvidenceFailure("zero-echo negative session is missing")

        report = build_runtime_report(
            rom_sha256=rom_sha256,
            symbol_sha256=symbol_sha256,
            sessions=sessions,
            negative_session=negative_session,
            provenance=provenance,
            replay_path=replay_path.relative_to(root).as_posix(),
        )
        if report_exit_code(report) == 0:
            replay_bytes = sessions[0]["snapshots"]["terminal"][
                "_replay_bytes"
            ]
            if replay_bytes != sessions[1]["snapshots"]["terminal"][
                "_replay_bytes"
            ]:
                raise EvidenceFailure(
                    "terminal replay RAM differs between sessions"
                )
            replay_path.parent.mkdir(parents=True, exist_ok=True)
            replay_path.write_bytes(replay_bytes)
            if sha256_file(replay_path) != report["guest_replay_ram"]["sha256"]:
                raise EvidenceFailure("written replay RAM hash mismatch")
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
        f"M5 B03 runtime evidence: {report['status']} "
        f"(acceptance {report['acceptance_status']}; "
        f"{report_path.as_posix()})"
    )
    exit_code = report_exit_code(report)
    if exit_code:
        print(
            report.get(
                "error",
                "runtime evidence checks or B03 acceptance failed",
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
