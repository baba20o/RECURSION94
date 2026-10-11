"""Adversarial cross-platform tests for the live B03 evidence collector."""

from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.m2_evidence import EvidenceFailure, parse_vasm_symbols
from tools.m3_runtime_evidence import crc32_iso_hdlc
from tools import m5_b03_runtime_evidence as b03


ROOT = Path(__file__).resolve().parents[2]
FAKE_SHA = "a" * 64
SYMBOLS = {
    "CampaignRuntimeProfileB03": 0x0001632C,
    "CampaignRuntimeSemanticsB03": 0x00016294,
    "CampaignRuntimeDependencyGroupsB03": 0x000162F4,
    "CampaignRuntimeDependencyEdgesB03": 0x00016314,
    "CampaignRuntimeRectsB03": 0x000161D4,
    "CampaignRuntimeObjectsB03": 0x00016234,
}


def _put16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = (value & 0xFFFF).to_bytes(2, "big")


def _put32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "big")


def _object_record(
    logical_id: int,
    state: int,
    x: int,
    y: int,
    timer: int,
    param: int,
) -> bytes:
    raw = bytearray(b03.OBJECT_BYTES)
    raw[0] = 1
    raw[1] = logical_id
    _put16(raw, 0x02, state)
    _put32(raw, 0x04, x << 8)
    _put32(raw, 0x08, y << 8)
    _put16(raw, 0x14, timer)
    _put16(raw, 0x16, param)
    return bytes(raw)


def _devices() -> bytes:
    raw = bytearray(b03.DEVICE_CAPACITY * b03.OBJECT_BYTES)
    for slot, values in enumerate(b03.B03_DEVICES):
        record = _object_record(*values)
        offset = slot * b03.OBJECT_BYTES
        raw[offset : offset + b03.OBJECT_BYTES] = record
    return bytes(raw)


def _hazards() -> bytes:
    raw = bytearray(b03.HAZARD_CAPACITY * b03.OBJECT_BYTES)
    record = _object_record(*b03.B03_HAZARDS[0])
    raw[: b03.OBJECT_BYTES] = record
    return bytes(raw)


def _actors() -> bytes:
    raw = bytearray(b03.ACTOR_CAPACITY * b03.ACTOR_BYTES)
    offset = b03.CURRENT_ACTOR_SLOT * b03.ACTOR_BYTES
    raw[offset] = 1
    raw[offset + 1] = b03.CURRENT_ACTOR_SLOT
    _put32(raw, offset + 0x04, b03.B03_SPAWN[0] << 8)
    _put32(raw, offset + 0x08, b03.B03_SPAWN[1] << 8)
    _put16(raw, offset + 0x14, 4)
    _put16(raw, offset + 0x16, 4)
    _put16(raw, offset + 0x1A, 1)
    _put16(raw, offset + 0x1C, 6)
    return bytes(raw)


def _vocabulary() -> bytes:
    raw = bytearray(b03.VOCAB_STATE_BYTES)
    raw[0:4] = b"R95V"
    _put16(raw, 0x04, b03.VOCAB_VERSION)
    _put16(raw, 0x06, b03.VOCAB_STATE_BYTES)
    _put16(raw, 0x08, b03.VOCAB_PROJECTION_BYTES)
    _put16(raw, 0x0A, b03.VOCAB_FLAG_INITIALIZED)
    _put16(raw, 0x16, b03.B03_STAGE_ID)

    campaign = 0xC8
    _put16(
        raw,
        campaign,
        b03.VOCAB_CAMPAIGN_FLAG_ACTIVE
        | b03.VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,
    )
    raw[campaign + 0x02] = b03.B03_DIRECTORY_INDEX
    raw[campaign + 0x03] = b03.B03_ECHO_CAP
    raw[campaign + 0x04] = b03.B03_SEMANTIC_COUNT
    raw[campaign + 0x05] = b03.B03_GROUP_COUNT
    raw[campaign + 0x06] = b03.B03_EDGE_COUNT
    raw[campaign + 0x07] = b03.B03_COMPLETION_OBJECT
    raw[campaign + 0x08] = b03.B03_COMPLETION_FIELD
    raw[campaign + 0x09] = b03.B03_COMPLETION_FLAGS
    _put16(raw, campaign + 0x0A, b03.B03_COMPLETION_MASK)
    _put16(raw, campaign + 0x0C, b03.B03_COMPLETION_VALUE)
    raw[campaign + 0x30 : campaign + 0x32] = b"\xFF\xFF"

    derived = 0x100
    _put32(raw, derived + 0x00, SYMBOLS["CampaignRuntimeProfileB03"])
    _put32(raw, derived + 0x04, SYMBOLS["CampaignRuntimeSemanticsB03"])
    _put32(
        raw,
        derived + 0x08,
        SYMBOLS["CampaignRuntimeDependencyGroupsB03"],
    )
    _put32(
        raw,
        derived + 0x0C,
        SYMBOLS["CampaignRuntimeDependencyEdgesB03"],
    )
    for slot in range(b03.ACTOR_CAPACITY):
        raw[
            b03.VOCAB_ACTOR_BASE_OFFSET
            + slot * b03.VOCAB_ACTOR_BYTES
            + 5
        ] = 0xFF
    return bytes(raw)


def _descriptor() -> bytes:
    raw = bytearray(b03.ENCOUNTER_DESCRIPTOR_BYTES)
    _put16(raw, 0x00, b03.B03_DIRECTORY_INDEX)
    _put16(raw, 0x04, 1)
    _put16(raw, 0x06, b03.ENCOUNTER_DESCRIPTOR_BYTES)
    for index, value in enumerate(b03.B03_ROOM):
        _put16(raw, 0x08 + index * 2, value)
    _put16(raw, 0x10, b03.B03_SPAWN[0])
    _put16(raw, 0x12, b03.B03_SPAWN[1])
    _put16(raw, 0x14, len(b03.B03_RECTS))
    _put16(raw, 0x16, 6)
    _put32(raw, 0x18, SYMBOLS["CampaignRuntimeRectsB03"])
    _put32(raw, 0x1C, SYMBOLS["CampaignRuntimeObjectsB03"])
    return bytes(raw)


def _rectangles() -> bytes:
    raw = bytearray()
    for x, y, width, height in b03.B03_RECTS:
        raw.extend((x & 0xFFFF).to_bytes(2, "big"))
        raw.extend((y & 0xFFFF).to_bytes(2, "big"))
        raw.extend(width.to_bytes(2, "big"))
        raw.extend(height.to_bytes(2, "big"))
    return bytes(raw)


def _replay() -> bytes:
    raw = bytearray(b03.REPLAY_TIMELINE_BYTES)
    timeline = 7 * 720 + 7 * 8
    raw[timeline : timeline + 4] = b"R94T"
    _put16(raw, timeline + 4, 1)
    _put16(raw, timeline + 6, 24)
    raw[timeline + 8] = 0
    raw[timeline + 9] = 6
    raw[timeline + 10 : timeline + 16] = b"\xFF" * 6
    raw[timeline + 16] = 0xFF
    raw[timeline + 17] = 1
    return bytes(raw)


def _gameplay() -> bytes:
    raw = bytearray(b03.GAMEPLAY_CONTROL_USED_BYTES)
    raw[0:4] = b"R94G"
    _put16(raw, 4, 1)
    _put16(raw, 6, 0x100)
    _put16(raw, 8, 1)
    raw[16] = b03.CURRENT_ACTOR_SLOT
    return bytes(raw)


def _projection(
    *,
    vocabulary: bytes,
    devices: bytes,
    hazards: bytes,
    actors: bytes,
) -> bytes:
    raw = bytearray(b03.CAMPAIGN_PROJECTION_BYTES)
    raw[0:4] = b"R94P"
    _put16(raw, 0x04, b03.CAMPAIGN_PROJECTION_VERSION)
    _put16(raw, 0x06, b03.CAMPAIGN_PROJECTION_BYTES)
    _put16(raw, 0x08, b03.B03_DIRECTORY_INDEX)
    _put16(raw, 0x0E, b03.ENGINE_STATE_ACTIVE)
    _put16(
        raw,
        0x12,
        b03.ENGINE_FLAG_LOADED | b03.ENGINE_FLAG_PROJECTION_VALID,
    )
    for index, value in enumerate(b03.B03_ROOM):
        _put16(raw, 0x18 + index * 2, value)
    raw[0x24] = 1
    raw[0x25] = 1
    _put16(raw, 0x26, len(b03.B03_RECTS))

    cursor = b03.PROJECTION_HEADER_BYTES
    for slot in range(b03.ACTOR_CAPACITY):
        source = slot * b03.ACTOR_BYTES
        raw[cursor : cursor + 0x1E] = actors[source : source + 0x1E]
        cursor += 0x1E
    for pool_type, _name, _address, capacity in b03.POOL_LAYOUT:
        raw[cursor] = pool_type
        raw[cursor + 1] = capacity
        if pool_type == 3:
            raw[cursor + 2] = 5
            raw[cursor + 3] = 5
        elif pool_type == 5:
            raw[cursor + 2] = 1
            raw[cursor + 3] = 1
        cursor += 4
        if pool_type == 3:
            raw[cursor : cursor + len(devices)] = devices
        elif pool_type == 5:
            raw[cursor : cursor + len(hazards)] = hazards
        cursor += capacity * b03.OBJECT_BYTES
    if cursor != b03.ENGINE_LEGACY_PROJECTION_BYTES:
        raise AssertionError(f"projection cursor is {cursor}")
    raw[cursor:] = vocabulary[: b03.VOCAB_PROJECTION_BYTES]
    return bytes(raw)


def _control(projection: bytes) -> bytes:
    raw = bytearray(b03.ENGINE_CONTROL_USED_BYTES)
    raw[0:4] = b"R94E"
    _put16(raw, 0x04, 1)
    _put16(raw, 0x06, 0x400)
    _put16(raw, 0x08, b03.ENGINE_STATE_ACTIVE)
    _put16(
        raw,
        0x0A,
        b03.ENGINE_FLAG_LOADED | b03.ENGINE_FLAG_PROJECTION_VALID,
    )
    _put16(raw, 0x0C, b03.B03_DIRECTORY_INDEX)
    _put16(raw, 0x18, b03.CAMPAIGN_PROJECTION_BYTES)
    _put32(raw, 0x1A, crc32_iso_hdlc(projection))
    raw[0x22:0x26] = b"\x00\x00\x00\x00"
    raw[0x26] = 1
    raw[0x27] = 1
    for index, value in enumerate(b03.B03_ROOM):
        _put16(raw, 0x28 + index * 2, value)
    _put32(raw, 0x30, b03.B03_SPAWN[0] << 8)
    _put32(raw, 0x34, b03.B03_SPAWN[1] << 8)
    _put16(raw, 0x38, len(b03.B03_RECTS))
    _put16(raw, 0x3A, 6)
    raw[0x42] = 5
    raw[0x44] = 1
    raw[0x4A] = 5
    raw[0x4C] = 1
    return bytes(raw)


def _initial_snapshot() -> dict:
    vocabulary = _vocabulary()
    devices = _devices()
    hazards = _hazards()
    actors = _actors()
    projection = _projection(
        vocabulary=vocabulary,
        devices=devices,
        hazards=hazards,
        actors=actors,
    )
    return b03.make_snapshot(
        label="initial",
        control_raw=_control(projection),
        gameplay_raw=_gameplay(),
        vocabulary_raw=vocabulary,
        descriptor_raw=_descriptor(),
        rectangle_raw=_rectangles(),
        device_raw=devices,
        projectile_raw=bytes(
            b03.PROJECTILE_CAPACITY * b03.OBJECT_BYTES
        ),
        hazard_raw=hazards,
        actor_raw=actors,
        input_raw=b"\x00\x00\x00\x00\x00\x01\x00",
        replay_raw=_replay(),
        projection_raw=projection,
        symbols=SYMBOLS,
    )


def _set_actor(
    snapshot: dict,
    slot: int,
    *,
    logical_id: int,
    held: int,
    x: float,
    y: float,
) -> None:
    actor = snapshot["actors"][slot]
    actor.update(
        {
            "active": 1,
            "role": slot,
            "logical_id": logical_id,
            "input": held,
            "previous_input": 0,
            "x_16_8": int(x * 256),
            "y_16_8": int(y * 256),
            "half_width": 4,
            "half_height": 4,
            "health": 1,
        }
    )
    if slot == b03.CURRENT_ACTOR_SLOT:
        snapshot["current_actor"] = actor
        snapshot["gameplay"]["current_input"] = held
        snapshot["gameplay"]["current_pressed"] = 0


def _set_projectile(snapshot: dict, *, x: int) -> None:
    projectile = snapshot["projectiles"][0]
    projectile.update(
        {
            "active": 1,
            "logical_id": 0,
            "state": 1,
            "x_16_8": x * 256,
            "y_16_8": 169 * 256,
            "vx_16_8": 3 * 256,
            "vy_16_8": 0,
            "timer": 30,
            "param": 0x0101,
        }
    )


def _scenario_session(number: int) -> dict:
    initial = _initial_snapshot()
    tick_keys = (
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
    )
    snapshots = {
        "initial": copy.deepcopy(initial),
        "post_timeout_1": copy.deepcopy(initial),
        "post_timeout_2": copy.deepcopy(initial),
        "terminal": copy.deepcopy(initial),
    }
    for tick in tick_keys:
        key = f"attempt_2_tick_{tick}"
        snapshots[key] = copy.deepcopy(initial)
        snapshots[key]["engine"]["attempt"] = 2
        snapshots[key]["engine"]["tick"] = tick
        _set_actor(
            snapshots[key],
            0,
            logical_id=6,
            held=0,
            x=63.5,
            y=43,
        )
        _set_actor(
            snapshots[key],
            1,
            logical_id=0,
            held=0,
            x=204,
            y=169,
        )
        _set_actor(
            snapshots[key],
            b03.CURRENT_ACTOR_SLOT,
            logical_id=1,
            held=0,
            x=156,
            y=116.5,
        )
        snapshots[key]["devices"][0]["state"] = 1
        snapshots[key]["devices"][0]["timer"] = 1
        snapshots[key]["devices"][1]["state"] = 1
        snapshots[key]["devices"][1]["timer"] = max(1, 234 - tick)
        snapshots[key]["devices"][2]["state"] = 0x0301

    post_1 = snapshots["post_timeout_1"]
    post_1["engine"]["attempt"] = 1
    post_1["replay_timeline"]["timeline"]["echo_count"] = 1
    post_1["replay_timeline"]["timeline"]["echo_order"][0] = 6
    post_1["replay_timeline"]["timeline"]["current_bank"] = 0
    post_1["replay_timeline"]["descriptors"][6]["length"] = 720
    post_1["replay_timeline"]["descriptors"][6]["flags"] = (
        b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO
    )
    post_1["replay_timeline"]["descriptors"][6]["crc32_iso_hdlc"] = (
        crc32_iso_hdlc(b03.ATTEMPT_0_INPUTS)
    )

    post_2 = snapshots["post_timeout_2"]
    post_2["engine"]["attempt"] = 2
    post_2["replay_timeline"]["timeline"]["echo_count"] = 2
    post_2["replay_timeline"]["timeline"]["echo_order"][:2] = [6, 0]
    post_2["replay_timeline"]["timeline"]["current_bank"] = 1
    for bank in (6, 0):
        post_2["replay_timeline"]["descriptors"][bank]["length"] = 720
        post_2["replay_timeline"]["descriptors"][bank]["flags"] = (
            b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO
        )
    post_2["replay_timeline"]["descriptors"][6]["crc32_iso_hdlc"] = (
        crc32_iso_hdlc(b03.ATTEMPT_0_INPUTS)
    )
    post_2["replay_timeline"]["descriptors"][0]["crc32_iso_hdlc"] = (
        crc32_iso_hdlc(b03.ATTEMPT_1_INPUTS)
    )

    timed = snapshots["attempt_2_tick_55"]
    timed["actors"][1]["input"] = b03.INPUT_B
    timed["current_actor"]["input"] = b03.INPUT_RIGHT
    timed["gameplay"]["current_input"] = b03.INPUT_RIGHT
    timed["devices"][1]["timer"] = 179
    timed["devices"][2]["state"] = 0x0301
    timed["vocabulary"]["campaign"]["last_interaction_actor"] = 1
    timed["vocabulary"]["campaign"]["last_interaction_object"] = 1

    pressure = snapshots["attempt_2_tick_67"]
    pressure["actors"][0]["input"] = b03.INPUT_RIGHT
    pressure["current_actor"]["input"] = b03.INPUT_RIGHT
    pressure["gameplay"]["current_input"] = b03.INPUT_RIGHT

    blocked_contact = snapshots["attempt_2_tick_86"]
    blocked_contact["current_actor"]["input"] = b03.INPUT_RIGHT
    blocked_contact["current_actor"]["flags"] = (
        b03.ACTOR_FLAG_COLLISION_X
    )
    blocked_contact["gameplay"]["current_input"] = b03.INPUT_RIGHT

    for tick, state in ((183, 0x0301), (188, 0x0201), (193, 0x0101)):
        spawn = snapshots[f"attempt_2_tick_{tick}"]
        spawn["actors"][1]["input"] = b03.INPUT_A
        spawn["devices"][2]["state"] = state
        _set_projectile(spawn, x=207)
    for tick, state in ((185, 0x0301), (190, 0x0201), (195, 0x0101)):
        pre_hit = snapshots[f"attempt_2_tick_{tick}"]
        pre_hit["devices"][2]["state"] = state
        _set_projectile(pre_hit, x=213)
    for tick, state in ((186, 0x0201), (191, 0x0101)):
        snapshots[f"attempt_2_tick_{tick}"]["devices"][2]["state"] = state

    opened = snapshots["attempt_2_tick_196"]
    opened["devices"][2]["state"] = b03.VOCAB_DEVICE_STATE_DISABLED
    opened["devices"][3]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
    opened["devices"][3]["timer"] = 150
    opened["devices"][4]["state"] = b03.VOCAB_DEVICE_STATE_OPEN

    snapshots["attempt_2_tick_197"]["devices"][2]["state"] = (
        b03.VOCAB_DEVICE_STATE_DISABLED
    )
    snapshots["attempt_2_tick_197"]["devices"][3]["state"] = (
        b03.VOCAB_DEVICE_STATE_OPEN
    )
    snapshots["attempt_2_tick_197"]["devices"][3]["timer"] = 149
    snapshots["attempt_2_tick_197"]["devices"][4]["state"] = (
        b03.VOCAB_DEVICE_STATE_OPEN
    )

    crossing = snapshots["attempt_2_tick_198"]
    crossing["devices"][2]["state"] = b03.VOCAB_DEVICE_STATE_DISABLED
    crossing["devices"][3]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
    crossing["devices"][3]["timer"] = 148
    crossing["devices"][4]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
    _set_actor(
        crossing,
        b03.CURRENT_ACTOR_SLOT,
        logical_id=1,
        held=b03.INPUT_RIGHT | b03.INPUT_C,
        x=162,
        y=116.5,
    )
    crossing["gameplay"]["current_pressed"] = b03.INPUT_C
    crossing_extension = crossing["vocabulary"]["actor_extensions"][
        b03.CURRENT_ACTOR_SLOT
    ]
    crossing_extension["flags"] = 7
    crossing_extension["dash_timer"] = 5

    east = snapshots["attempt_2_tick_201"]
    east["devices"][2]["state"] = b03.VOCAB_DEVICE_STATE_DISABLED
    east["devices"][3]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
    east["devices"][3]["timer"] = 145
    east["devices"][4]["state"] = (
        b03.VOCAB_DEVICE_STATE_OPEN
        | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
    )
    _set_actor(
        east,
        b03.CURRENT_ACTOR_SLOT,
        logical_id=1,
        held=b03.INPUT_RIGHT,
        x=180,
        y=116.5,
    )
    east_extension = east["vocabulary"]["actor_extensions"][
        b03.CURRENT_ACTOR_SLOT
    ]
    east_extension["flags"] = 7
    east_extension["dash_timer"] = 2

    for tick, timer in ((273, 1), (291, 19)):
        hold = snapshots[f"attempt_2_tick_{tick}"]
        hold["devices"][2]["state"] = b03.VOCAB_DEVICE_STATE_DISABLED
        hold["devices"][3]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
        hold["devices"][3]["timer"] = 346 - tick
        hold["devices"][4]["state"] = (
            b03.VOCAB_DEVICE_STATE_OPEN
            | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
            | b03.VOCAB_DEVICE_STATE_ACTIVE
        )
        hold["devices"][4]["timer"] = timer
        hold["devices"][4]["vx_16_8"] = b03.CURRENT_ACTOR_SLOT + 1
        hold["devices"][4]["vy_16_8"] = tick
        hold["vocabulary"]["result"] = b03.VOCAB_RESULT_ACTIVATED
        _set_actor(
            hold,
            b03.CURRENT_ACTOR_SLOT,
            logical_id=1,
            held=b03.INPUT_B,
            x=295.5,
            y=116.5,
        )
        hold["gameplay"]["current_pressed"] = (
            b03.INPUT_B if tick == 273 else 0
        )
        hold["engine"]["input_held"] = b03.INPUT_B
        hold["engine"]["input_pressed"] = (
            b03.INPUT_B if tick == 273 else 0
        )
        hold["vocabulary"]["campaign"][
            "last_interaction_actor"
        ] = b03.CURRENT_ACTOR_SLOT
        hold["vocabulary"]["campaign"][
            "last_interaction_object"
        ] = b03.B03_COMPLETION_OBJECT

    terminal = snapshots["terminal"]
    terminal["engine"]["state"] = b03.ENGINE_STATE_TERMINAL
    terminal["engine"]["terminal_reason"] = (
        b03.GAMEPLAY_TERMINAL_PROGRAM_STABLE
    )
    terminal["engine"]["attempt"] = 2
    terminal["engine"]["tick"] = len(b03.ATTEMPT_2_INPUTS)
    terminal["gameplay"]["result"] = b03.GAMEPLAY_RESULT_PROGRAM_STABLE
    _set_actor(
        terminal,
        0,
        logical_id=6,
        held=0,
        x=63.5,
        y=43,
    )
    _set_actor(
        terminal,
        1,
        logical_id=0,
        held=0,
        x=204,
        y=169,
    )
    _set_actor(
        terminal,
        b03.CURRENT_ACTOR_SLOT,
        logical_id=1,
        held=b03.INPUT_B,
        x=295.5,
        y=116.5,
    )
    terminal["gameplay"]["current_pressed"] = 0
    terminal["current_actor"]["previous_input"] = b03.INPUT_B
    terminal["gameplay"]["current_previous"] = b03.INPUT_B
    terminal["engine"]["input_held"] = b03.INPUT_B
    terminal["engine"]["input_pressed"] = 0
    terminal["engine"]["input_previous"] = b03.INPUT_B
    terminal["input"]["held"] = b03.INPUT_B
    terminal["vocabulary"]["campaign"]["flags"] |= (
        b03.VOCAB_CAMPAIGN_FLAG_COMPLETE
    )
    terminal["vocabulary"]["campaign"]["last_interaction_actor"] = 6
    terminal["vocabulary"]["campaign"]["last_interaction_object"] = 4
    terminal["devices"][2]["state"] = b03.VOCAB_DEVICE_STATE_DISABLED
    terminal["devices"][3]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
    terminal["devices"][3]["timer"] = 54
    terminal["devices"][4]["state"] = b03.B03_COMPLETION_VALUE
    terminal["devices"][4]["timer"] = 20
    terminal["devices"][4]["vx_16_8"] = b03.CURRENT_ACTOR_SLOT + 1
    terminal["devices"][4]["vy_16_8"] = 292
    terminal["vocabulary"]["result"] = b03.VOCAB_RESULT_STAGE_COMPLETE
    terminal["replay_timeline"]["timeline"]["echo_count"] = 2
    terminal["replay_timeline"]["timeline"]["echo_order"][:2] = [6, 0]
    for bank in (6, 0):
        terminal["replay_timeline"]["descriptors"][bank]["length"] = 720
        terminal["replay_timeline"]["descriptors"][bank]["flags"] = (
            b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO
        )
    terminal["replay_timeline"]["descriptors"][6]["crc32_iso_hdlc"] = (
        crc32_iso_hdlc(b03.ATTEMPT_0_INPUTS)
    )
    terminal["replay_timeline"]["descriptors"][0]["crc32_iso_hdlc"] = (
        crc32_iso_hdlc(b03.ATTEMPT_1_INPUTS)
    )
    terminal["replay_timeline"]["descriptors"][1]["length"] = len(
        b03.ATTEMPT_2_INPUTS
    )
    terminal["replay_timeline"]["descriptors"][1]["flags"] = (
        b03.REPLAY_FLAG_CURRENT
    )
    terminal_replay = bytearray(terminal["_replay_bytes"])
    terminal_replay[0:720] = b03.ATTEMPT_1_INPUTS
    terminal_replay[720 : 720 + len(b03.ATTEMPT_2_INPUTS)] = (
        b03.ATTEMPT_2_INPUTS
    )
    terminal_replay[6 * 720 : 7 * 720] = b03.ATTEMPT_0_INPUTS
    terminal["_replay_bytes"] = bytes(terminal_replay)

    return {
        "session_id": f"positive_session_{number}",
        "metadata": {
            "process_id": 9000 + number,
            "runtime_copy": {
                "temporary_directory": f"C:/Temp/m5-b03-positive-{number}"
            },
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "session_isolation": {"status": "pass"},
            },
            "input_transport": "targeted_PostMessageW",
            "symbol_map_sha256": FAKE_SHA,
            "pre_step_breakpoint_symbol": "MainLoopGameplayStepCall",
            "drive_protocol": (
                "validate read-only d0 and INPUT_HELD at PRE N"
            ),
            "dual_breakpoint_transcript": {"status": "pass"},
        },
        "attempt_inputs": {
            "0": list(b03.ATTEMPT_0_INPUTS),
            "1": list(b03.ATTEMPT_1_INPUTS),
            "2": list(b03.ATTEMPT_2_INPUTS),
        },
        "commanded_inputs": {
            "0": list(b03.ATTEMPT_0_INPUTS),
            "1": list(b03.ATTEMPT_1_INPUTS),
            "2": list(b03.ATTEMPT_2_INPUTS),
        },
        "pre_step_polled_inputs": {
            "0": list(b03.ATTEMPT_0_INPUTS),
            "1": list(b03.ATTEMPT_1_INPUTS),
            "2": list(b03.ATTEMPT_2_INPUTS),
        },
        "post_step_observed_inputs": {
            "0": list(b03.ATTEMPT_0_INPUTS),
            "1": list(b03.ATTEMPT_1_INPUTS),
            "2": list(b03.ATTEMPT_2_INPUTS),
        },
        "negative_inputs": [],
        "negative_commanded_inputs": [],
        "negative_pre_step_polled_inputs": [],
        "negative_post_step_observed_inputs": [],
        "timeout_commits": [
            {
                "attempt": attempt,
                "method": "automatic 720-authoritative-step timeout",
                "observed_post_step_attempt": attempt + 1,
                "observed_post_step_tick": 0,
                "authoritative_frames_consumed": 720,
                "consumed_input_source": (
                    "read-only d0 and INPUT_HELD at pre-step for all 720"
                ),
                "guest_memory_register_pc_writes": False,
            }
            for attempt in (0, 1)
        ],
        "snapshots": snapshots,
        "_transcript_lines": [
            f"# positive_session_{number} begin",
            "> Z0,312,2",
            "< OK",
            "> mff1400,50",
            "< 00",
            "> k",
            f"# positive_session_{number} end",
        ],
    }


def _negative_session() -> dict:
    initial = _initial_snapshot()
    snapshots = {
        "initial": copy.deepcopy(initial),
        "negative_tick_231": copy.deepcopy(initial),
        "negative_tick_240": copy.deepcopy(initial),
    }
    for tick in (231, 240):
        snapshot = snapshots[f"negative_tick_{tick}"]
        snapshot["engine"]["attempt"] = 0
        snapshot["engine"]["tick"] = tick
        _set_actor(
            snapshot,
            b03.CURRENT_ACTOR_SLOT,
            logical_id=6,
            held=b03.INPUT_B if tick == 231 else 0,
            x=272,
            y=112,
        )
        if tick == 231:
            snapshot["gameplay"]["current_pressed"] = b03.INPUT_B
            snapshot["input"]["held"] = b03.INPUT_B
        snapshot["devices"][3]["state"] = 0
        snapshot["devices"][4]["state"] = 0
        snapshot["devices"][4]["timer"] = 0
        if tick == 231:
            snapshot["vocabulary"]["result"] = (
                b03.VOCAB_RESULT_NO_TARGET
            )
    return {
        "session_id": "zero_echo_negative",
        "metadata": {
            "process_id": 9900,
            "runtime_copy": {
                "temporary_directory": "C:/Temp/m5-b03-negative"
            },
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "session_isolation": {"status": "pass"},
            },
            "input_transport": "targeted_PostMessageW",
            "symbol_map_sha256": FAKE_SHA,
            "pre_step_breakpoint_symbol": "MainLoopGameplayStepCall",
            "drive_protocol": (
                "validate read-only d0 and INPUT_HELD at PRE N"
            ),
            "dual_breakpoint_transcript": {"status": "pass"},
        },
        "attempt_inputs": {},
        "commanded_inputs": {},
        "pre_step_polled_inputs": {},
        "post_step_observed_inputs": {},
        "negative_inputs": list(b03.NEGATIVE_INPUTS),
        "negative_commanded_inputs": list(b03.NEGATIVE_INPUTS),
        "negative_pre_step_polled_inputs": list(b03.NEGATIVE_INPUTS),
        "negative_post_step_observed_inputs": list(b03.NEGATIVE_INPUTS),
        "timeout_commits": [],
        "snapshots": snapshots,
        "_transcript_lines": [
            "# zero_echo_negative begin",
            "> Z0,312,2",
            "< OK",
            "> mff1400,50",
            "< 00",
            "> k",
            "# zero_echo_negative end",
        ],
    }


def _report(
    sessions: list[dict] | None = None,
    negative: dict | None = None,
) -> dict:
    return b03.build_runtime_report(
        rom_sha256=FAKE_SHA,
        symbol_sha256=FAKE_SHA,
        sessions=sessions or [_scenario_session(1), _scenario_session(2)],
        negative_session=negative or _negative_session(),
        provenance={
            "fixture": {"debug_start_encounter": 2},
            "pre_step_rom_binding": {"status": "pass"},
        },
        replay_path="artifacts/replays/fake.bin",
    )


def _status(report: dict, name: str) -> str:
    return next(
        item["status"] for item in report["checks"] if item["check"] == name
    )


class M5B03InputTransportTests(unittest.TestCase):
    def test_reassertions_preserve_transitions_and_guest_edges(self) -> None:
        tracked: set[str] = set()
        delivered: set[str] = set()
        events: list[str] = []
        messages: list[tuple[str, bool]] = []

        def post(_window, key, *, pressed):
            name = key[0]
            messages.append((name, pressed))
            if pressed:
                delivered.add(name)
            else:
                delivered.discard(name)

        masks = (b03.INPUT_RIGHT | b03.INPUT_C,) * 2 + (b03.INPUT_RIGHT, 0, 0)
        previous = 0
        edges = []
        with (
            mock.patch.object(b03, "_post_key", side_effect=post),
            mock.patch.object(b03.time, "sleep") as sleep,
        ):
            for desired in masks:
                b03._set_gameplay_mask(123, desired, tracked, events)
                held = sum(bit for name, *_rest, bit in b03.KEYS if name in delivered)
                edges.append((held & ~previous, previous & ~held))
                self.assertEqual(desired, held)
                self.assertEqual(tracked, delivered)
                previous = held
        self.assertEqual(messages, [
            ("Right", True), ("C", True),
            ("Right", True), ("C", True),
            ("C", False), ("Right", True),
            ("Right", False),
        ])
        self.assertEqual(events, [
            "Right:down", "C:down", "Right:reassert", "C:reassert",
            "C:up", "Right:reassert", "Right:up",
        ])
        self.assertEqual(edges, [
            (b03.INPUT_RIGHT | b03.INPUT_C, 0), (0, 0),
            (0, b03.INPUT_C), (0, b03.INPUT_RIGHT), (0, 0),
        ])
        self.assertEqual(
            sleep.call_args_list,
            [mock.call(b03.INPUT_EDGE_SETTLE_SECONDS)] * 4,
        )

    def test_delivery_is_bounded_and_releases_precede_downs(self) -> None:
        for before, desired in ((0, 0x7F), (0x7F, 0), (0x7F, 0x7F), (0x55, 0x2A)):
            with self.subTest(before=before, desired=desired):
                tracked = {name for name, *_rest, bit in b03.KEYS if before & bit}
                events: list[str] = []
                with (
                    mock.patch.object(b03, "_post_key") as post,
                    mock.patch.object(b03.time, "sleep"),
                ):
                    b03._set_gameplay_mask(123, desired, tracked, events)
                self.assertLessEqual(post.call_count, len(b03.KEYS))
                states = [call.kwargs["pressed"] for call in post.call_args_list]
                self.assertEqual(states, sorted(states))
                self.assertEqual(
                    tracked,
                    {name for name, *_rest, bit in b03.KEYS if desired & bit},
                )

    def test_invalid_masks_post_nothing_and_preserve_tracking(self) -> None:
        for desired in (-1, 0x80, 0x100):
            with self.subTest(desired=desired):
                tracked = {"Right"}
                events: list[str] = []
                with (
                    mock.patch.object(b03, "_post_key") as post,
                    mock.patch.object(b03.time, "sleep") as sleep,
                    self.assertRaisesRegex(EvidenceFailure, "invalid gameplay input"),
                ):
                    b03._set_gameplay_mask(123, desired, tracked, events)
                post.assert_not_called()
                sleep.assert_not_called()
                self.assertEqual(tracked, {"Right"})
                self.assertEqual(events, [])

    def test_failed_pre_step_aborts_without_input_repair_or_retry(self) -> None:
        initial = _initial_snapshot()
        remote = mock.Mock()
        remote.request.side_effect = ["S05", "00000300", "00000000"]
        with (
            mock.patch.object(b03, "_read_region", side_effect=[
                initial["_control_bytes"], initial["_input_bytes"],
            ]),
            mock.patch.object(b03, "_set_gameplay_mask") as deliver,
            mock.patch.object(b03, "_continue_to_post_step") as post,
            self.assertRaisesRegex(EvidenceFailure, "d0/INPUT_HELD"),
        ):
            b03._observe_attempt(
                remote,
                window=123,
                attempt=0,
                schedule=bytes((b03.INPUT_RIGHT,)),
                pressed={"Right"},
                events=[],
                symbols={"MainLoopGameplayStepCall": 0x300},
                snapshots={},
                session_id="mismatch_fixture",
            )
        deliver.assert_not_called()
        post.assert_not_called()
        self.assertEqual(
            remote.request.call_args_list,
            [mock.call("c"), mock.call("p11"), mock.call("p0")],
        )


class M5B03RuntimeEvidenceTests(unittest.TestCase):
    def test_dual_breakpoint_transcript_order_is_fail_closed(self) -> None:
        pre = 0x2F2
        post = 0x336
        lines = ["# begin"]
        for _step in range(2):
            lines.extend(
                [
                    "> c",
                    "< S05",
                    "> p11",
                    f"< {pre:08X}",
                    "> p0",
                    "< 00000000",
                    f"> z0,{pre:x},2",
                    "< OK",
                    "> c",
                    "< S05",
                    "> p11",
                    f"< {post:08X}",
                    f"> Z0,{pre:x},2",
                    "< OK",
                ]
            )
        lines.append("# end")
        audit = b03.audit_dual_breakpoint_transcript(
            lines,
            pre_step=pre,
            post_step=post,
            expected_steps=2,
        )
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["pre_stops"], 2)
        self.assertEqual(audit["post_stops"], 2)

        corrupt = list(lines)
        corrupt[corrupt.index(f"> z0,{pre:x},2")] = "> qSupported"
        audit = b03.audit_dual_breakpoint_transcript(
            corrupt,
            pre_step=pre,
            post_step=post,
            expected_steps=2,
        )
        self.assertEqual(audit["status"], "fail")

    def test_owned_host_launch_and_cleanup_are_bounded(self) -> None:
        process = mock.Mock()
        denied = PermissionError(13, "transient execute denial")
        transcript: list[str] = []
        with (
            mock.patch.object(
                b03.subprocess,
                "Popen",
                side_effect=[denied, process],
            ) as launch,
            mock.patch.object(b03.time, "sleep") as sleep,
        ):
            observed = b03._launch_owned_emulator(
                ["blastem.exe", "fixture.bin"],
                cwd=Path("C:/owned/blastem"),
                transcript=transcript,
            )
        self.assertIs(observed, process)
        self.assertEqual(launch.call_count, 2)
        sleep.assert_called_once_with(0.25)
        self.assertTrue(
            any("host launch permission retry 1/3" in line for line in transcript)
        )

        owned = Path("C:/owned/session")
        transcript = []
        with (
            mock.patch.object(
                b03.shutil,
                "rmtree",
                side_effect=[PermissionError(13, "locked"), None],
            ) as remove,
            mock.patch.object(b03.time, "sleep") as sleep,
        ):
            b03._remove_owned_session_tree(
                owned,
                transcript,
                attempts=2,
            )
        self.assertEqual(remove.call_count, 2)
        sleep.assert_called_once_with(0.1)
        self.assertTrue(
            any("removed exact owned session tree" in line for line in transcript)
        )

    def test_owned_windows_process_tree_cleanup_is_exact(self) -> None:
        process = mock.Mock()
        process.pid = 4242
        process.poll.side_effect = [None, 0]
        result = subprocess_result = mock.Mock(returncode=0)
        transcript: list[str] = []
        with (
            mock.patch.object(b03, "shutdown_emulator") as shared_shutdown,
            mock.patch.object(b03.os, "name", "nt"),
            mock.patch.object(
                b03.subprocess,
                "run",
                return_value=subprocess_result,
            ) as taskkill,
        ):
            b03._shutdown_owned_emulator(None, process, transcript)
        shared_shutdown.assert_called_once_with(None, process, transcript)
        taskkill.assert_called_once()
        self.assertEqual(
            taskkill.call_args.args[0],
            ["taskkill.exe", "/PID", "4242", "/T", "/F"],
        )
        process.wait.assert_called_once_with(timeout=10.0)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            any("exact owned pid 4242" in line for line in transcript)
        )

    def test_canonical_initial_snapshot_parses_and_passes(self) -> None:
        snapshot = _initial_snapshot()
        self.assertTrue(b03.initialization_ok(snapshot))
        self.assertEqual(snapshot["rectangles"], list(b03.B03_RECTS))
        self.assertEqual(snapshot["projectiles"], [
            b03.parse_fixed_pool(
                bytes(b03.PROJECTILE_CAPACITY * b03.OBJECT_BYTES),
                capacity=b03.PROJECTILE_CAPACITY,
            )[index]
            for index in range(b03.PROJECTILE_CAPACITY)
        ])

    def test_rectangle_and_pool_parsers_are_bounded(self) -> None:
        raw = bytearray(8)
        _put16(raw, 0, 0xFFFE)
        _put16(raw, 2, 3)
        _put16(raw, 4, 4)
        _put16(raw, 6, 5)
        self.assertEqual(
            b03.parse_rectangles(bytes(raw), 1), [(-2, 3, 4, 5)]
        )
        with self.assertRaisesRegex(EvidenceFailure, "outside 0..32"):
            b03.parse_rectangles(b"", 33)
        with self.assertRaisesRegex(EvidenceFailure, "8 required"):
            b03.parse_rectangles(b"\0" * 7, 1)
        with self.assertRaisesRegex(EvidenceFailure, "768 required"):
            b03.parse_fixed_pool(b"", capacity=b03.PROJECTILE_CAPACITY)

    def test_abi_pointer_and_authored_geometry_corruption_fail(self) -> None:
        snapshot = _initial_snapshot()
        snapshot["vocabulary"]["campaign"]["semantic_count"] = 5
        self.assertFalse(b03.initialization_ok(snapshot))

        snapshot = _initial_snapshot()
        snapshot["pointer_matches"]["profile"] = False
        self.assertFalse(b03.initialization_ok(snapshot))

        snapshot = _initial_snapshot()
        snapshot["rectangles"][7] = (208, 160, 32, 32)
        self.assertFalse(b03.initialization_ok(snapshot))

    def test_complete_causal_scenario_and_negative_pass(self) -> None:
        report = _report()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["acceptance_status"], "pass")
        self.assertEqual(b03.report_exit_code(report), 0)
        self.assertTrue(
            all(item["status"] == "pass" for item in report["acceptance"])
        )
        self.assertEqual(
            _status(report, "zero_echo_completion_rejected"), "pass"
        )
        self.assertFalse(report["schedule"]["guest_memory_register_pc_writes"])

    def test_incomplete_acceptance_forces_nonzero_exit(self) -> None:
        report = _report()
        report["status"] = "pass"
        report["acceptance_status"] = "incomplete"
        self.assertEqual(b03.report_exit_code(report), 1)

    def test_echo_owner_or_health_transition_corruption_fails(self) -> None:
        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["snapshots"]["attempt_2_tick_183"]["projectiles"][0][
            "param"
        ] = 0x0201
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        claims = {
            item["claim"]: item["status"] for item in report["acceptance"]
        }
        self.assertEqual(
            claims["node_damage_owned_by_echo_replay"], "incomplete"
        )

        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[1]["snapshots"]["attempt_2_tick_191"]["devices"][2][
            "state"
        ] = 0x0201
        report = _report(sessions)
        self.assertEqual(
            _status(report, "live_two_echo_b03_scenario"), "fail"
        )

    def test_closed_leaf_geometry_and_dash_latch_are_required(self) -> None:
        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["snapshots"]["attempt_2_tick_195"]["current_actor"][
            "x_16_8"
        ] = 150 * 256
        report = _report(sessions)
        self.assertEqual(report["acceptance_status"], "incomplete")

        sessions = [_scenario_session(1), _scenario_session(2)]
        east = sessions[1]["snapshots"]["attempt_2_tick_201"]
        east["vocabulary"]["actor_extensions"][
            b03.CURRENT_ACTOR_SLOT
        ]["flags"] = 0
        report = _report(sessions)
        self.assertEqual(
            _status(report, "live_two_echo_b03_scenario"), "fail"
        )

        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["snapshots"]["attempt_2_tick_201"]["devices"][4][
            "state"
        ] &= ~b03.VOCAB_DEVICE_STATE_DASH_ROUTE
        report = _report(sessions)
        self.assertEqual(report["acceptance_status"], "incomplete")

    def test_uninterrupted_twenty_step_current_hold_is_required(self) -> None:
        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["snapshots"]["attempt_2_tick_291"]["devices"][4][
            "timer"
        ] = 18
        report = _report(sessions)
        self.assertEqual(report["acceptance_status"], "incomplete")

        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[1]["snapshots"]["terminal"]["devices"][4]["timer"] = 19
        report = _report(sessions)
        self.assertEqual(
            _status(report, "live_two_echo_b03_scenario"), "fail"
        )

        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["attempt_inputs"]["2"][280] = 0
        report = _report(sessions)
        claims = {
            item["claim"]: item["status"] for item in report["acceptance"]
        }
        self.assertEqual(claims["current_only_completion"], "incomplete")

    def test_no_c_zero_echo_run_must_remain_incomplete(self) -> None:
        negative = _negative_session()
        negative["snapshots"]["negative_tick_240"]["devices"][4][
            "state"
        ] = b03.B03_COMPLETION_VALUE
        report = _report(negative=negative)
        self.assertEqual(
            _status(report, "zero_echo_completion_rejected"), "fail"
        )
        self.assertEqual(report["acceptance_status"], "incomplete")

    def test_checkpoint_drift_and_debugger_write_fail_primary(self) -> None:
        sessions = [_scenario_session(1), _scenario_session(2)]
        drift = bytearray(
            sessions[1]["snapshots"]["terminal"]["_projection_bytes"]
        )
        drift[-1] ^= 1
        sessions[1]["snapshots"]["terminal"]["_projection_bytes"] = bytes(
            drift
        )
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "two_session_deterministic_parity"), "fail"
        )

        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["_transcript_lines"].append("> Mff1600,1:00")
        report = _report(sessions)
        self.assertEqual(_status(report, "read_only_debugger"), "fail")
        self.assertEqual(report["debugger_write_packets"], ["> Mff1600,1:00"])

    def test_timeout_transition_metadata_is_fail_closed(self) -> None:
        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[0]["timeout_commits"][0][
            "observed_post_step_attempt"
        ] = 0
        report = _report(sessions)
        self.assertEqual(
            _status(report, "automatic_timeout_commit_transitions"),
            "fail",
        )
        self.assertEqual(report["status"], "fail")

        sessions = [_scenario_session(1), _scenario_session(2)]
        sessions[1]["snapshots"]["attempt_2_tick_196"]["vocabulary"][
            "overflow_flags"
        ] = 1
        report = _report(sessions)
        self.assertEqual(
            _status(report, "no_fatal_contract_or_pool_overflow"),
            "fail",
        )

    def test_commanded_polled_and_consumed_inputs_fail_closed(self) -> None:
        for field in (
            "commanded_inputs",
            "pre_step_polled_inputs",
            "attempt_inputs",
        ):
            with self.subTest(positive_layer=field):
                sessions = [_scenario_session(1), _scenario_session(2)]
                sessions[0][field]["0"][67] ^= b03.INPUT_RIGHT
                report = _report(sessions)
                self.assertEqual(
                    _status(report, "actual_guest_input_schedules"),
                    "fail",
                )

        for field in (
            "negative_commanded_inputs",
            "negative_pre_step_polled_inputs",
            "negative_inputs",
        ):
            with self.subTest(negative_layer=field):
                negative = _negative_session()
                negative[field][0] ^= b03.INPUT_DOWN
                report = _report(negative=negative)
                self.assertEqual(
                    _status(
                        report,
                        "actual_zero_echo_negative_input_schedule",
                    ),
                    "fail",
                )

    def test_schedules_are_static_bounded_and_role_specific(self) -> None:
        self.assertEqual(
            tuple(
                len(b03.expected_attempt_inputs(index))
                for index in range(3)
            ),
            (720, 720, 292),
        )
        self.assertEqual(len(b03.NEGATIVE_INPUTS), 240)
        self.assertFalse(any(value & b03.INPUT_C for value in b03.NEGATIVE_INPUTS))
        self.assertTrue(any(value & b03.INPUT_C for value in b03.ATTEMPT_2_INPUTS))
        self.assertFalse(
            any(value & b03.INPUT_A for value in b03.ATTEMPT_2_INPUTS)
        )
        self.assertEqual(
            [
                index + 1
                for index, value in enumerate(b03.ATTEMPT_1_INPUTS)
                if value & b03.INPUT_A
            ],
            [183, 188, 193],
        )
        self.assertEqual(b03.ATTEMPT_1_INPUTS[54], b03.INPUT_B)
        self.assertEqual(
            b03.ATTEMPT_2_INPUTS[197],
            b03.INPUT_RIGHT | b03.INPUT_C,
        )
        self.assertEqual(
            b03.ATTEMPT_2_INPUTS[272:292],
            bytes((b03.INPUT_B,)) * 20,
        )
        self.assertEqual(set(b03.ATTEMPT_2_INPUTS[86:197]), {0})
        with self.assertRaisesRegex(ValueError, "outside 0..2"):
            b03.expected_attempt_inputs(3)

    def test_fixture_symbol_and_collector_read_only_contract(self) -> None:
        symbol_path = ROOT / "build/level-B03/recursion94-debug.sym"
        if symbol_path.is_file():
            symbols = parse_vasm_symbols(symbol_path)
            self.assertEqual(symbols["DEBUG_START_ENCOUNTER"], 2)

        source = (ROOT / "tools/m5_b03_runtime_evidence.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".write_memory(", source)
        self.assertIsNone(
            re.search(
                r"""remote\.request\(\s*f?["'](?:G|M|P|X)""",
                source,
            )
        )
        wrapper = (ROOT / "tools/m5_b03_probe.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "build\\level-B03\\recursion94-debug.bin", wrapper
        )
        self.assertIn(
            '$Evidence.acceptance_status -ne "pass"', wrapper
        )

    def test_dash_preserves_pressed_edge_before_extension_lookup(self) -> None:
        source = (
            ROOT / "src/gameplay/vocabulary_core.asm"
        ).read_text(encoding="utf-8")
        routine = source.split(
            "VocabularyApplyDashPhase:", 1
        )[1].split(".edge:", 1)[0]
        preserve = routine.index("move.b  d1,d6")
        extension_lookup = routine.index(
            "bsr.w   VocabularyGetActorExtension"
        )
        c_edge_test = routine.index("andi.b  #INPUT_C,d6")
        self.assertLess(preserve, extension_lookup)
        self.assertLess(extension_lookup, c_edge_test)


if __name__ == "__main__":
    unittest.main()
