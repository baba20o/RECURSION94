"""Adversarial host tests for the objective M5 B03 visual collector."""

from __future__ import annotations

import copy
import hashlib
import re
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.m2_evidence import EvidenceFailure
from tools import m5_b03_runtime_evidence as b03
from tools import m5_visual_evidence as visual
from tests.emulator import test_m5_b03_runtime_evidence as b03_fixture


ROOT = Path(__file__).resolve().parents[2]
TEST_SYMBOLS = dict(b03_fixture.SYMBOLS)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _region_payload(label: str, name: str, size: int) -> bytes:
    seed = hashlib.sha256(f"{label}:{name}".encode("ascii")).digest()
    return (seed * ((size + len(seed) - 1) // len(seed)))[:size]


def _put16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = (value & 0xFFFF).to_bytes(2, "big")


def _put32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "big")


def _recompute_raw_bundles(state: dict) -> None:
    regions = state["raw_regions"]
    authoritative = b"".join(
        bytes.fromhex(regions[name]["raw_hex"])
        for name in visual.AUTHORITATIVE_REGION_NAMES
    )
    complete = b"".join(
        bytes.fromhex(regions[name]["raw_hex"])
        for name, _address, _size in visual.STATE_READ_REGION_SPECS
    )
    state["authoritative_bundle_sha256"] = hashlib.sha256(
        authoritative
    ).hexdigest()
    state["complete_read_bundle_sha256"] = hashlib.sha256(
        complete
    ).hexdigest()
    state["complete_read_bundle_bytes"] = len(complete)


def _renderer_bytes(
    tick: int,
    *,
    attempt: int,
    sprite_count: int = 0,
    status_text: str = "",
    status_palette: int = 0,
    checkpoint_count: int = 0,
    result: int = 0,
) -> bytes:
    raw = bytearray(visual.RENDERER_DECLARED_BYTES)
    _put32(raw, 0x00, visual.RENDERER_MAGIC)
    _put16(raw, 0x04, visual.RENDERER_VERSION)
    _put16(raw, 0x06, visual.RENDERER_DECLARED_BYTES)
    _put16(raw, 0x08, visual.RENDERER_FLAG_INITIALIZED)
    _put16(raw, 0x0A, attempt)
    _put16(raw, 0x0E, 0xFFFF)
    _put16(raw, 0x10, 0xFFFF)
    _put16(raw, 0x12, 0xFFFF)
    _put16(raw, 0x14, sprite_count)
    _put16(raw, 0x16, 8)
    _put16(raw, 0x18, 128)
    _put16(raw, 0x20, tick)
    _put16(raw, visual.RENDERER_LAST_RESULT_OFFSET, result)
    _put16(
        raw,
        visual.RENDERER_LAST_CHECKPOINT_COUNT_OFFSET,
        checkpoint_count,
    )
    status = status_text.ljust(visual.RENDERER_HUD_ROW_WORDS)
    for index, character in enumerate(status):
        tile = visual._hud_tile(character)  # noqa: SLF001
        word = 0 if tile == 0 else 0x8000 | status_palette << 13 | tile
        _put16(
            raw,
            visual.RENDERER_HUD_BUFFER_OFFSET
            + 2 * visual.RENDERER_HUD_ROW_BYTES
            + index * 2,
            word,
        )
    return bytes(raw)


def _sat_entry(
    *,
    x: int,
    y: int,
    width_tiles: int,
    height_tiles: int,
    link: int,
    tile: int,
    palette: int,
) -> bytes:
    size_link = (
        ((width_tiles - 1) << 10)
        | ((height_tiles - 1) << 8)
        | link
    )
    attribute = 0x8000 | (palette << 13) | tile
    return b"".join(
        (
            (y + visual.RENDERER_SPRITE_COORD_BIAS).to_bytes(2, "big"),
            size_link.to_bytes(2, "big"),
            attribute.to_bytes(2, "big"),
            (x + visual.RENDERER_SPRITE_COORD_BIAS).to_bytes(2, "big"),
        )
    )


def _renderer_output_bytes(
    spec: visual.CaptureSpec,
    *,
    current_x_16_8: int,
    current_y_16_8: int,
    node_state: int,
) -> tuple[bytes, bytes]:
    status = visual.EXPECTED_STATUS_PRESENTATION[spec.name]
    actor_x = current_x_16_8 >> 8
    actor_y = current_y_16_8 >> 8
    dash = spec.name == "dash_route_open"
    body_palette = 3 if dash else 1
    badge_tile = (
        visual.RENDERER_DASH_BADGE_TILE
        if dash
        else visual.RENDERER_CURRENT_BADGE_TILE
    )
    node_live = bool(node_state & b03.VOCAB_DEVICE_STATE_ACTIVE) and not bool(
        node_state & b03.VOCAB_DEVICE_STATE_DISABLED
    )
    node_tile = (
        visual.RENDERER_TILE_TERMINAL_STABLE
        if node_live
        else visual.RENDERER_TILE_TERMINAL
    )
    node_palette = 2 if node_live else 0
    entries = (
        _sat_entry(
            x=actor_x - 12,
            y=actor_y - 12,
            width_tiles=3,
            height_tiles=3,
            link=1,
            tile=visual.RENDERER_TILE_CURRENT_0,
            palette=body_palette,
        ),
        _sat_entry(
            x=actor_x + 17,
            y=actor_y - 24,
            width_tiles=1,
            height_tiles=1,
            link=2,
            tile=badge_tile,
            palette=body_palette,
        ),
        _sat_entry(
            x=224 - 8,
            y=176 - 8,
            width_tiles=2,
            height_tiles=2,
            link=0,
            tile=node_tile,
            palette=node_palette,
        ),
    )
    sat = b"".join(entries).ljust(visual.SAT_SHADOW_BYTES, b"\x00")
    renderer = _renderer_bytes(
        spec.tick,
        attempt=spec.attempt,
        sprite_count=len(entries),
        status_text=status["text"],
        status_palette=status["palette"],
        checkpoint_count=(
            visual.TERMINAL_CHECKPOINT_COUNT
            if spec.name == "terminal_complete_20"
            else (spec.tick - 1) // 60 + 1
        ),
    )
    return renderer, sat


def _raw_map_from_manifest(state: dict) -> dict[str, bytes]:
    return {
        name: bytes.fromhex(item["raw_hex"])
        for name, item in state["raw_regions"].items()
    }


def _with_status_row(
    state: dict,
    *,
    label: str,
    text: str,
    palette: int,
) -> dict:
    raw_map = _raw_map_from_manifest(state)
    renderer = bytearray(raw_map["renderer_state"])
    start = (
        visual.RENDERER_HUD_BUFFER_OFFSET
        + 2 * visual.RENDERER_HUD_ROW_BYTES
    )
    renderer[start : start + visual.RENDERER_HUD_ROW_BYTES] = bytes(
        visual.RENDERER_HUD_ROW_BYTES
    )
    for index, character in enumerate(
        text.ljust(visual.RENDERER_HUD_ROW_WORDS)
    ):
        tile = visual._hud_tile(character)  # noqa: SLF001
        word = 0 if tile == 0 else 0x8000 | palette << 13 | tile
        _put16(renderer, start + index * 2, word)
    raw_map["renderer_state"] = bytes(renderer)
    return _manifest_from_raw(label, raw_map)


def _with_sat_entry(
    state: dict,
    *,
    label: str,
    index: int,
    x_delta: int = 0,
    tile: int | None = None,
    palette: int | None = None,
) -> dict:
    raw_map = _raw_map_from_manifest(state)
    sat = bytearray(raw_map["sat_shadow"])
    offset = index * visual.SAT_ENTRY_BYTES
    if x_delta:
        x_word = int.from_bytes(sat[offset + 6 : offset + 8], "big")
        _put16(sat, offset + 6, x_word + x_delta)
    if tile is not None or palette is not None:
        attribute = int.from_bytes(sat[offset + 4 : offset + 6], "big")
        if tile is not None:
            attribute = (
                attribute & ~visual.RENDERER_TILE_MASK
            ) | tile
        if palette is not None:
            attribute = (attribute & ~0x6000) | palette << 13
        _put16(sat, offset + 4, attribute)
    raw_map["sat_shadow"] = bytes(sat)
    return _manifest_from_raw(label, raw_map)


def _manifest_from_raw(label: str, raw_map: dict[str, bytes]) -> dict:
    regions = {
        name: {
            "bytes": len(raw_map[name]),
            "sha256": hashlib.sha256(raw_map[name]).hexdigest(),
            "raw_hex": raw_map[name].hex().upper(),
        }
        for name, _address, _size in visual.STATE_READ_REGION_SPECS
    }
    envelope = {"label": label, "raw_regions": regions}
    _recompute_raw_bundles(envelope)
    return visual.reconstruct_state_manifest_from_raw(
        envelope, symbols=TEST_SYMBOLS
    )


def _base_raw_map() -> dict[str, bytes]:
    snapshot = b03_fixture._initial_snapshot()  # noqa: SLF001
    raw_map = {
        name: bytes(snapshot[key])
        for key, name in visual.RAW_REGION_NAMES.items()
        if key not in {"_renderer_bytes", "_sat_bytes"}
    }
    raw_map["renderer_state"] = _renderer_bytes(0, attempt=0)
    raw_map["sat_shadow"] = bytes(visual.SAT_SHADOW_BYTES)
    return raw_map


def _initial_manifest(label: str = "initial") -> dict:
    return _manifest_from_raw(label, _base_raw_map())


def _retag_raw_region(state: dict, name: str, label: str) -> None:
    size = next(
        size
        for region_name, _address, size in visual.STATE_READ_REGION_SPECS
        if region_name == name
    )
    raw = _region_payload(label, name, size)
    state["raw_regions"][name] = {
        "bytes": size,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw_hex": raw.hex().upper(),
    }
    if name == "renderer_state":
        state["renderer"]["raw_sha256"] = state["raw_regions"][name][
            "sha256"
        ]
    _recompute_raw_bundles(state)


def _rewrite_engine_coordinate(
    state: dict,
    *,
    label: str,
    engine_state: int,
    attempt: int,
    tick: int,
) -> dict:
    raw_map = _raw_map_from_manifest(state)
    control = bytearray(raw_map["engine_control"])
    _put16(control, 0x08, engine_state)
    _put16(control, 0x0E, attempt)
    _put16(control, 0x10, tick)
    raw_map["engine_control"] = bytes(control)
    raw_map["renderer_state"] = _renderer_bytes(tick, attempt=attempt)
    return _manifest_from_raw(label, raw_map)


def _rewrite_engine_only(
    state: dict,
    *,
    label: str,
    engine_state: int,
    attempt: int,
    tick: int,
) -> dict:
    raw_map = _raw_map_from_manifest(state)
    control = bytearray(raw_map["engine_control"])
    _put16(control, 0x08, engine_state)
    _put16(control, 0x0E, attempt)
    _put16(control, 0x10, tick)
    raw_map["engine_control"] = bytes(control)
    return _manifest_from_raw(label, raw_map)


def _legacy_state_fixture(spec: visual.CaptureSpec) -> dict:
    devices = [
        {
            "active": 1,
            "logical_id": slot,
            "state": 0,
            "x_16_8": 0,
            "y_16_8": 0,
            "vx_16_8": 0,
            "vy_16_8": 0,
            "timer": 0,
            "param": 0,
        }
        for slot in range(5)
    ]
    actors = [
        {
            "slot": slot,
            "active": 0,
            "role": slot,
            "flags": 0,
            "x_16_8": 0,
            "y_16_8": 0,
            "vx_16_8": 0,
            "vy_16_8": 0,
            "half_width": 4,
            "half_height": 4,
            "input": 0,
            "previous_input": 0,
            "health": 1,
            "logical_id": 0,
        }
        for slot in range(b03.ACTOR_CAPACITY)
    ]
    actors[0].update(active=1, logical_id=6)
    actors[1].update(active=1, logical_id=0)
    actors[b03.CURRENT_ACTOR_SLOT].update(active=1, logical_id=1)
    extensions = [
        {
            "slot": slot,
            "flags": 0,
            "dash_timer": 0,
            "dash_cooldown": 0,
            "invulnerability_timer": 0,
            "carry_slot": 0xFF,
            "shield_timer": 0,
            "last_action": 0,
        }
        for slot in range(b03.ACTOR_CAPACITY)
    ]
    campaign = {
        "flags": (
            b03.VOCAB_CAMPAIGN_FLAG_ACTIVE
            | b03.VOCAB_CAMPAIGN_FLAG_PROFILE_VALID
        ),
        "last_interaction_actor": 0xFF,
        "last_interaction_object": 0xFF,
    }
    engine_state = b03.ENGINE_STATE_ACTIVE
    terminal_reason = 0
    gameplay_result = 0
    vocabulary_result = 0

    devices[3].update(
        logical_id=3,
        x_16_8=160 * 256,
        y_16_8=96 * 256,
        param=0x0302,
    )
    devices[4].update(
        logical_id=4,
        x_16_8=288 * 256,
        y_16_8=112 * 256,
        param=0x0903,
    )
    if spec.name == "two_echo_armed":
        devices[0]["state"] = b03.VOCAB_DEVICE_STATE_ACTIVE
        devices[1]["state"] = b03.VOCAB_DEVICE_STATE_ACTIVE
        devices[2]["state"] = (
            0x0300 | b03.VOCAB_DEVICE_STATE_ACTIVE
        )
    elif spec.name == "node_disabled_door_open":
        devices[2]["state"] = b03.VOCAB_DEVICE_STATE_DISABLED
        devices[3].update(
            state=b03.VOCAB_DEVICE_STATE_OPEN,
            timer=150,
        )
        devices[4]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
    elif spec.name == "dash_route_open":
        devices[3]["state"] = b03.VOCAB_DEVICE_STATE_OPEN
        devices[4]["state"] = (
            b03.VOCAB_DEVICE_STATE_OPEN
            | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
        )
        actors[b03.CURRENT_ACTOR_SLOT].update(
            x_16_8=180 * 256,
            y_16_8=112 * 256,
        )
        extensions[b03.CURRENT_ACTOR_SLOT].update(
            flags=(
                b03.VOCAB_ACTOR_FLAG_DASHING
                | b03.VOCAB_ACTOR_FLAG_PHASED
            ),
            dash_timer=4,
        )
    elif spec.name == "terminal_hold_19":
        devices[4].update(
            state=(
                b03.VOCAB_DEVICE_STATE_OPEN
                | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
                | b03.VOCAB_DEVICE_STATE_ACTIVE
            ),
            timer=19,
            vx_16_8=b03.CURRENT_ACTOR_SLOT + 1,
            vy_16_8=291,
        )
        campaign.update(
            last_interaction_actor=b03.CURRENT_ACTOR_SLOT,
            last_interaction_object=b03.B03_COMPLETION_OBJECT,
        )
        actors[b03.CURRENT_ACTOR_SLOT]["input"] = b03.INPUT_B
    elif spec.name == "terminal_complete_20":
        engine_state = b03.ENGINE_STATE_TERMINAL
        terminal_reason = b03.GAMEPLAY_TERMINAL_PROGRAM_STABLE
        gameplay_result = b03.GAMEPLAY_RESULT_PROGRAM_STABLE
        vocabulary_result = b03.VOCAB_RESULT_STAGE_COMPLETE
        devices[4].update(
            state=b03.B03_COMPLETION_VALUE,
            timer=20,
            vx_16_8=b03.CURRENT_ACTOR_SLOT + 1,
            vy_16_8=292,
        )
        campaign.update(
            flags=(
                campaign["flags"]
                | b03.VOCAB_CAMPAIGN_FLAG_COMPLETE
            ),
            last_interaction_actor=b03.CURRENT_ACTOR_SLOT,
            last_interaction_object=b03.B03_COMPLETION_OBJECT,
        )

    state = {
        "label": spec.name,
        "engine": {
            "state": engine_state,
            "flags": 0,
            "encounter": b03.B03_DIRECTORY_INDEX,
            "attempt": spec.attempt,
            "tick": spec.tick,
            "terminal_reason": terminal_reason,
            "last_error": 0,
            "overflow_flags": 0,
            "input_held": (
                b03.INPUT_B
                if spec.name.startswith("terminal_")
                else 0
            ),
            "input_pressed": 0,
            "input_previous": 0,
        },
        "gameplay": {
            "result": gameplay_result,
            "current_pressed": 0,
        },
        "vocabulary": {
            "stage_id": b03.B03_STAGE_ID,
            "campaign": campaign,
            "actor_extensions": extensions,
            "result": vocabulary_result,
        },
        "devices": devices,
        "actors": actors,
        "current_actor": copy.deepcopy(
            actors[b03.CURRENT_ACTOR_SLOT]
        ),
        "hazards": [
            {
                "active": 1,
                "logical_id": 5,
                "state": 0,
                "x_16_8": 152 * 256,
                "y_16_8": 80 * 256,
                "param": 0x0120,
            }
        ],
        "projectiles": [],
        "projection": {"published_crc_matches": True},
        "replay_timeline": {
            "timeline": {
                "echo_count": 2,
                "echo_order": [6, 0, 0xFF, 0xFF, 0xFF, 0xFF],
            }
        },
        "renderer": _renderer(spec.tick),
    }
    _install_raw_regions(state, f"target-{spec.name}")
    return state


def _legacy_successor_fixture(
    spec: visual.CaptureSpec, source: dict
) -> dict:
    result = copy.deepcopy(source)
    if spec.name == "terminal_complete_20":
        return result
    engine_state, attempt, tick = visual._expected_successor(spec)
    result["engine"].update(
        state=engine_state,
        attempt=attempt,
        tick=tick,
    )
    result["renderer"] = _renderer(tick, attempt=attempt)
    _install_raw_regions(result, f"successor-{spec.name}")
    return result


def _state(spec: visual.CaptureSpec) -> dict:
    raw_map = _base_raw_map()
    control = bytearray(raw_map["engine_control"])
    gameplay = bytearray(raw_map["gameplay_control"])
    vocabulary = bytearray(raw_map["vocabulary_state"])
    devices = bytearray(raw_map["device_pool"])
    actors = bytearray(raw_map["actor_pool"])
    input_raw = bytearray(raw_map["input_state"])
    replay = bytearray(raw_map["replay_timeline"])

    def set_device(
        slot: int,
        *,
        state: int | None = None,
        timer: int | None = None,
        vx: int | None = None,
        vy: int | None = None,
    ) -> None:
        offset = slot * b03.OBJECT_BYTES
        if state is not None:
            _put16(devices, offset + 0x02, state)
        if vx is not None:
            _put32(devices, offset + 0x0C, vx)
        if vy is not None:
            _put32(devices, offset + 0x10, vy)
        if timer is not None:
            _put16(devices, offset + 0x14, timer)

    def set_actor(
        slot: int,
        *,
        logical_id: int,
        x_16_8: int,
        y_16_8: int,
        held: int = 0,
    ) -> None:
        offset = slot * b03.ACTOR_BYTES
        actors[offset : offset + b03.ACTOR_BYTES] = bytes(b03.ACTOR_BYTES)
        actors[offset] = 1
        actors[offset + 1] = slot
        _put32(actors, offset + 0x04, x_16_8)
        _put32(actors, offset + 0x08, y_16_8)
        _put16(actors, offset + 0x14, 4)
        _put16(actors, offset + 0x16, 4)
        actors[offset + 0x18] = held
        _put16(actors, offset + 0x1A, 1)
        _put16(actors, offset + 0x1C, logical_id)

    engine_state = b03.ENGINE_STATE_ACTIVE
    terminal_reason = 0
    gameplay_result = 0
    vocabulary_result = 0
    campaign_flags = (
        b03.VOCAB_CAMPAIGN_FLAG_ACTIVE
        | b03.VOCAB_CAMPAIGN_FLAG_PROFILE_VALID
    )
    last_actor = 0xFF
    last_object = 0xFF
    current_x = 156 * 256
    current_y = 116 * 256 + 128
    current_input = 0
    extension_flags = 0
    dash_timer = 0

    if spec.name == "two_echo_armed":
        set_device(0, state=b03.VOCAB_DEVICE_STATE_ACTIVE)
        set_device(1, state=b03.VOCAB_DEVICE_STATE_ACTIVE)
        set_device(2, state=0x0300 | b03.VOCAB_DEVICE_STATE_ACTIVE)
    elif spec.name == "node_disabled_door_open":
        set_device(2, state=b03.VOCAB_DEVICE_STATE_DISABLED)
        set_device(3, state=b03.VOCAB_DEVICE_STATE_OPEN, timer=150)
        set_device(4, state=b03.VOCAB_DEVICE_STATE_OPEN)
    elif spec.name == "dash_route_open":
        set_device(3, state=b03.VOCAB_DEVICE_STATE_OPEN)
        set_device(
            4,
            state=(
                b03.VOCAB_DEVICE_STATE_OPEN
                | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
            ),
        )
        current_x = 180 * 256
        extension_flags = (
            b03.VOCAB_ACTOR_FLAG_DASHING
            | b03.VOCAB_ACTOR_FLAG_PHASED
        )
        dash_timer = 4
    elif spec.name == "terminal_hold_19":
        set_device(
            4,
            state=(
                b03.VOCAB_DEVICE_STATE_OPEN
                | b03.VOCAB_DEVICE_STATE_DASH_ROUTE
                | b03.VOCAB_DEVICE_STATE_ACTIVE
            ),
            timer=19,
            vx=b03.CURRENT_ACTOR_SLOT + 1,
            vy=291,
        )
        last_actor = b03.CURRENT_ACTOR_SLOT
        last_object = b03.B03_COMPLETION_OBJECT
        current_input = b03.INPUT_B
        current_x = 295 * 256 + 128
    elif spec.name == "terminal_complete_20":
        engine_state = b03.ENGINE_STATE_TERMINAL
        terminal_reason = b03.GAMEPLAY_TERMINAL_PROGRAM_STABLE
        gameplay_result = b03.GAMEPLAY_RESULT_PROGRAM_STABLE
        vocabulary_result = b03.VOCAB_RESULT_STAGE_COMPLETE
        set_device(
            4,
            state=b03.B03_COMPLETION_VALUE,
            timer=20,
            vx=b03.CURRENT_ACTOR_SLOT + 1,
            vy=292,
        )
        campaign_flags |= b03.VOCAB_CAMPAIGN_FLAG_COMPLETE
        last_actor = b03.CURRENT_ACTOR_SLOT
        last_object = b03.B03_COMPLETION_OBJECT
        current_input = b03.INPUT_B
        current_x = 295 * 256 + 128
    else:
        raise AssertionError(f"unhandled visual fixture {spec.name}")

    actors[:] = bytes(len(actors))
    set_actor(0, logical_id=6, x_16_8=63 * 256, y_16_8=43 * 256)
    set_actor(1, logical_id=0, x_16_8=204 * 256, y_16_8=169 * 256)
    set_actor(
        b03.CURRENT_ACTOR_SLOT,
        logical_id=1,
        x_16_8=current_x,
        y_16_8=current_y,
        held=current_input,
    )

    _put16(control, 0x08, engine_state)
    _put16(control, 0x0C, b03.B03_DIRECTORY_INDEX)
    _put16(control, 0x0E, spec.attempt)
    _put16(control, 0x10, spec.tick)
    _put16(control, 0x12, terminal_reason)
    _put16(
        control,
        0x20,
        (
            visual.TERMINAL_CHECKPOINT_COUNT
            if spec.name == "terminal_complete_20"
            else (spec.tick - 1) // 60 + 1
        ),
    )
    control[0x22] = current_input
    _put16(gameplay, 0x0A, gameplay_result)
    gameplay[0x12] = current_input
    gameplay[0x13] = 0
    _put16(vocabulary, 0x0C, vocabulary_result)
    campaign = 0xC8
    _put16(vocabulary, campaign, campaign_flags)
    vocabulary[campaign + 0x30] = last_actor
    vocabulary[campaign + 0x31] = last_object
    extension = (
        b03.VOCAB_ACTOR_BASE_OFFSET
        + b03.CURRENT_ACTOR_SLOT * b03.VOCAB_ACTOR_BYTES
    )
    _put16(vocabulary, extension, extension_flags)
    vocabulary[extension + 2] = dash_timer
    input_raw[0] = current_input
    timeline = 7 * 720 + 7 * 8
    replay[timeline + 8] = 2
    replay[timeline + 9] = 1
    replay[timeline + 10 : timeline + 16] = bytes(
        (6, 0, 0xFF, 0xFF, 0xFF, 0xFF)
    )
    node_offset = 2 * b03.OBJECT_BYTES
    node_state = int.from_bytes(
        devices[node_offset + 0x02 : node_offset + 0x04], "big"
    )
    renderer_raw, sat_raw = _renderer_output_bytes(
        spec,
        current_x_16_8=current_x,
        current_y_16_8=current_y,
        node_state=node_state,
    )
    raw_map.update(
        {
            "engine_control": bytes(control),
            "gameplay_control": bytes(gameplay),
            "vocabulary_state": bytes(vocabulary),
            "device_pool": bytes(devices),
            "actor_pool": bytes(actors),
            "input_state": bytes(input_raw),
            "replay_timeline": bytes(replay),
            "sat_shadow": sat_raw,
            "renderer_state": renderer_raw,
        }
    )
    return _manifest_from_raw(spec.name, raw_map)


def _successor(spec: visual.CaptureSpec, source: dict) -> dict:
    if spec.name == "terminal_complete_20":
        return copy.deepcopy(source)
    if spec.name == "terminal_hold_19":
        terminal = next(
            item
            for item in visual.CAPTURE_PLAN
            if item.name == "terminal_complete_20"
        )
        return _state(terminal)
    engine_state, attempt, tick = visual._expected_successor(spec)
    return _rewrite_engine_coordinate(
        source,
        label=f"successor-{spec.name}",
        engine_state=engine_state,
        attempt=attempt,
        tick=tick,
    )


def _dma_boundary(
    frame: int,
    *,
    pending: int,
    commit_words: int,
    depth: int = 0,
    build_words: int = 0,
) -> dict:
    dma_raw = b"".join(
        (
            depth.to_bytes(2, "big"),
            depth.to_bytes(2, "big"),
            build_words.to_bytes(4, "big"),
            commit_words.to_bytes(2, "big"),
            pending.to_bytes(2, "big"),
            b"\x00\x00\x00\x00",
        )
    )
    return {
        "debug_frame_counter": frame,
        "dma_queue": {
            "published_depth": depth,
            "build_depth": depth,
            "build_words": build_words,
            "commit_words": commit_words,
            "frame_pending": pending,
            "reserved_0": 0,
            "reserved_1": 0,
            "raw_hex": dma_raw.hex().upper(),
            "raw_sha256": hashlib.sha256(dma_raw).hexdigest(),
        },
    }


def _raw_read_lines(address: int, raw: bytes) -> list[str]:
    lines: list[str] = []
    offset = 0
    while offset < len(raw):
        amount = min(0xFF, len(raw) - offset)
        chunk = raw[offset : offset + amount]
        lines.extend(
            [
                f"> m{address + offset:x},{amount:x}",
                f"< {chunk.hex().upper()}",
            ]
        )
        offset += amount
    return lines


def _observation_read_lines(manifest: dict, boundary: dict) -> list[str]:
    lines: list[str] = []
    for name, address, _size in visual.STATE_READ_REGION_SPECS:
        raw = bytes.fromhex(manifest["raw_regions"][name]["raw_hex"])
        lines.extend(_raw_read_lines(address, raw))
    frame_raw = boundary["debug_frame_counter"].to_bytes(4, "big")
    dma_raw = bytes.fromhex(boundary["dma_queue"]["raw_hex"])
    for address, raw in (
        (visual.DEBUG_FRAME_COUNTER, frame_raw),
        (visual.DMA_QUEUE_CONTROL_BASE, dma_raw),
    ):
        lines.extend(
            [f"> m{address:x},{len(raw):x}", f"< {raw.hex().upper()}"]
        )
    return lines


def _engine_control_bytes(
    *, state: int, attempt: int, tick: int, input_held: int
) -> bytes:
    raw = bytearray(b03.ENGINE_CONTROL_USED_BYTES)
    raw[0x08:0x0A] = state.to_bytes(2, "big")
    raw[0x0E:0x10] = attempt.to_bytes(2, "big")
    raw[0x10:0x12] = tick.to_bytes(2, "big")
    raw[0x22] = input_held
    return bytes(raw)


def _protocol_session_lines(
    name: str, event_order: list[str], record: dict
) -> list[str]:
    binding = record["frame_binding"]
    spec = next(item for item in visual.CAPTURE_PLAN if item.name == name)
    initial = _initial_manifest(f"visual_{name}_initial")
    vblank = 0x200
    lines = [
        f"# visual_{name} begin",
        f"> Z0,{vblank:x},2",
        "< OK",
        "> c",
        "< S05",
        "> p11",
        f"< {vblank:08X}",
    ]
    for region_name, address, _size in visual.STATE_READ_REGION_SPECS:
        raw = bytes.fromhex(
            initial["raw_regions"][region_name]["raw_hex"]
        )
        lines.extend(_raw_read_lines(address, raw))
    lines.extend(
        [
            f"> z0,{vblank:x},2",
            "< OK",
            "> Z0,2f2,2",
            "< OK",
            "> Z0,336,2",
            "< OK",
        ]
    )
    schedules = visual.canonical_schedules()
    for attempt, schedule in enumerate(schedules):
        if attempt > spec.attempt:
            break
        limit = spec.tick if attempt == spec.attempt else len(schedule)
        for tick, desired in enumerate(schedule[:limit], start=1):
            pre_control = _engine_control_bytes(
                state=b03.ENGINE_STATE_ACTIVE,
                attempt=attempt,
                tick=tick - 1,
                input_held=desired,
            )
            input_raw = bytes((desired, 0, 0, 0, 0, 1, 0))
            lines.extend(
                [
                    "> c",
                    "< S05",
                    "> p11",
                    "< 000002F2",
                    "> p0",
                    f"< {desired:08X}",
                    *_raw_read_lines(b03.ENGINE_CONTROL_BASE, pre_control),
                    *_raw_read_lines(b03.INPUT_STATE_BASE, input_raw),
                    "> z0,2f2,2",
                    "< OK",
                    "> c",
                    "< S05",
                    "> p11",
                    "< 00000336",
                    "> Z0,2f2,2",
                    "< OK",
                ]
            )
            if attempt < 2 and tick == 720:
                post_state = b03.ENGINE_STATE_ACTIVE
                post_attempt = attempt + 1
                post_tick = 0
            else:
                post_state = (
                    b03.ENGINE_STATE_TERMINAL
                    if attempt == 2 and tick == 292
                    else b03.ENGINE_STATE_ACTIVE
                )
                post_attempt = attempt
                post_tick = tick
            post_control = _engine_control_bytes(
                state=post_state,
                attempt=post_attempt,
                tick=post_tick,
                input_held=desired,
            )
            lines.extend(
                [
                    *_raw_read_lines(b03.ENGINE_CONTROL_BASE, post_control),
                    *_raw_read_lines(b03.INPUT_STATE_BASE, input_raw),
                ]
            )
    lines.extend(
        [
            f"# visual_{name} capture_protocol begin",
            *_observation_read_lines(
                record["target_post_step"],
                binding["target_post_step_boundary"],
            ),
            "> z0,2f2,2",
            "< OK",
            "> z0,336,2",
            "< OK",
            "> Z0,33c,2",
            "< OK",
            "> c",
            "< S05",
            "> p11",
            "< 0000033C",
        ]
    )
    if name == "terminal_complete_20":
        lines.extend(
            [
                *_observation_read_lines(
                    record["terminal_checkpoint_hold"],
                    binding["terminal_checkpoint_hold_boundary"],
                ),
                "> z0,33c,2",
                "< OK",
                "> Z0,336,2",
                "< OK",
                "> c",
                "< S05",
                "> p11",
                "< 00000336",
                *_observation_read_lines(
                    record["terminal_service_post"],
                    binding["terminal_service_post_boundary"],
                ),
                "> z0,336,2",
                "< OK",
                "> Z0,33c,2",
                "< OK",
                "> c",
                "< S05",
                "> p11",
                "< 0000033C",
            ]
        )
    lines.extend(
        [
            *_observation_read_lines(
                record["target_post_renderer"],
                binding["publication_after_renderer"],
            ),
            "> z0,33c,2",
            "< OK",
            "> Z0,336,2",
            "< OK",
            "> Z0,124c,2",
            "< OK",
        ]
    )
    idle_index = 0
    png_consume_event_kind = binding["png_consume_event_kind"]
    for kind in event_order:
        address = 0x336 if kind == "successor_post" else 0x124C
        if kind == "successor_post":
            manifest = record["successor_post_step"]
            boundary = binding["successor_post_step_boundary"]
        elif kind == "pre_odd_empty_dma":
            idle = record["ui_service_idle_boundaries"][idle_index]
            idle_index += 1
            manifest = idle["state"]
            boundary = idle["publication_boundary"]
        else:
            manifest = record["png_precommit_state"]
            boundary = binding["successor_boundary"]
        lines.extend(
            [
                "> c",
                "< S05",
                "> p11",
                f"< {address:08X}",
                *_observation_read_lines(manifest, boundary),
            ]
        )
        if kind == "successor_post":
            lines.extend(["> z0,336,2", "< OK"])
        if kind == png_consume_event_kind:
            lines.extend(
                [
                    f"# visual_{name} png_consume begin",
                    (
                        f"# visual_{name} png_consume end sha256="
                        f"{record['screenshot']['sha256']}"
                    ),
                ]
            )
    lines.extend(
        [
            "> z0,124c,2",
            "< OK",
            f"# visual_{name} capture_protocol end",
            "> k",
            f"# visual_{name} end",
        ]
    )
    return lines


def _capture(
    spec: visual.CaptureSpec,
    index: int,
    *,
    event_order: list[str] | None = None,
    odd_queue_mode: str | None = None,
) -> dict:
    order = event_order or ["successor_post", "odd_dma_precommit"]
    legal = [
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
    if order not in legal:
        raise ValueError(f"invalid fixture event order {order}")

    target = _state(spec)
    target_post_step = copy.deepcopy(target)
    terminal_checkpoint_hold: dict | None = None
    terminal_service_post: dict | None = None
    if spec.name == "terminal_complete_20":
        hold_spec = next(
            item
            for item in visual.CAPTURE_PLAN
            if item.name == "terminal_hold_19"
        )
        previous_presentation = _state(hold_spec)
        target_post_raw = _raw_map_from_manifest(target)
        previous_raw = _raw_map_from_manifest(previous_presentation)
        target_post_raw["renderer_state"] = previous_raw["renderer_state"]
        target_post_raw["sat_shadow"] = previous_raw["sat_shadow"]
        target_post_step = _manifest_from_raw(
            "terminal-complete-target-post", target_post_raw
        )
        hold_raw = _raw_map_from_manifest(target_post_step)
        held_renderer = bytearray(hold_raw["renderer_state"])
        _put16(
            held_renderer,
            visual.RENDERER_LAST_RESULT_OFFSET,
            visual.RENDERER_RESULT_CHECKPOINT_HELD,
        )
        _put16(
            held_renderer,
            visual.RENDERER_LAST_CHECKPOINT_COUNT_OFFSET,
            visual.TERMINAL_CHECKPOINT_COUNT,
        )
        hold_raw["renderer_state"] = bytes(held_renderer)
        terminal_checkpoint_hold = _manifest_from_raw(
            "terminal-complete-checkpoint-hold", hold_raw
        )
        terminal_service_post = copy.deepcopy(terminal_checkpoint_hold)
        terminal_service_post["label"] = "terminal-complete-service-post"
    target_renderer_sha256 = target["raw_regions"]["renderer_state"][
        "sha256"
    ]
    target_sat_sha256 = target["raw_regions"]["sat_shadow"]["sha256"]
    post = _successor(spec, target)
    post["renderer"] = copy.deepcopy(target["renderer"])
    post["raw_regions"]["renderer_state"] = copy.deepcopy(
        target["raw_regions"]["renderer_state"]
    )
    _recompute_raw_bundles(post)

    has_idle = "pre_odd_empty_dma" in order
    target_frame = 101 if has_idle else 100
    before_frame = target_frame - 1
    publication_before_renderer = _dma_boundary(
        before_frame,
        pending=(0 if spec.name == "terminal_complete_20" else 1),
        commit_words=0,
        depth=(0 if spec.name == "terminal_complete_20" else 4),
        build_words=(0 if spec.name == "terminal_complete_20" else 440),
    )
    target_post_step_boundary = publication_before_renderer
    terminal_checkpoint_hold_boundary: dict | None = None
    terminal_service_post_boundary: dict | None = None
    if spec.name == "terminal_complete_20":
        target_post_step_boundary = _dma_boundary(
            before_frame - 1,
            pending=0,
            commit_words=visual.TARGET_RENDER_COMMIT_WORDS,
        )
        terminal_checkpoint_hold_boundary = copy.deepcopy(
            target_post_step_boundary
        )
        terminal_service_post_boundary = publication_before_renderer
    presentation = visual.expected_odd_presentation(target_frame)
    odd_frame = presentation["expected_odd_presentation_frame"]
    target_commit_words = 440
    current_frame = target_frame
    empty_dma_commits = 0
    post_seen = False
    post_boundary: dict | None = None
    png_state: dict | None = None
    png_boundary: dict | None = None
    odd_classification: dict | None = None
    idle_records: list[dict] = []
    events: list[dict] = []

    for event_index, kind in enumerate(order):
        if kind == "successor_post":
            post_boundary = _dma_boundary(
                current_frame,
                pending=0,
                commit_words=(
                    0 if empty_dma_commits else target_commit_words
                ),
            )
            events.append(
                {
                    "index": event_index,
                    "kind": kind,
                    "frame": current_frame,
                    "state_complete_read_sha256": post[
                        "complete_read_bundle_sha256"
                    ],
                    "authoritative_sha256": post[
                        "authoritative_bundle_sha256"
                    ],
                    "renderer_sha256": target_renderer_sha256,
                    "dma_queue": post_boundary["dma_queue"],
                }
            )
            post_seen = True
            continue

        if kind == "pre_odd_empty_dma":
            current_frame = target_frame + 1
            torn = (
                copy.deepcopy(target)
                if spec.name == "terminal_complete_20"
                else _rewrite_engine_only(
                    target,
                    label=f"diagnostic-torn-{spec.name}",
                    engine_state=target["engine"]["state"],
                    attempt=spec.attempt,
                    tick=spec.tick + 1,
                )
            )
            boundary = _dma_boundary(
                current_frame,
                pending=0,
                commit_words=target_commit_words,
            )
            classification = visual.classify_ui_service_boundary(
                spec,
                torn,
                boundary,
                previous_frame=target_frame,
                target_authoritative_sha256=target[
                    "authoritative_bundle_sha256"
                ],
                target_renderer_sha256=target_renderer_sha256,
                expected_phase="idle",
                target_commit_words=target_commit_words,
            )
            idle_records.append(
                {
                    "index": 0,
                    "diagnostic_torn_snapshot": True,
                    "state": torn,
                    "publication_boundary": boundary,
                    "classification": classification,
                }
            )
            events.append(
                {
                    "index": event_index,
                    "kind": kind,
                    "frame": current_frame,
                    "diagnostic_torn_snapshot": True,
                    "authoritative_sha256": torn[
                        "authoritative_bundle_sha256"
                    ],
                    "renderer_sha256": target_renderer_sha256,
                    "dma_queue": boundary["dma_queue"],
                }
            )
            empty_dma_commits += 1
            continue

        current_frame = odd_frame
        queue_mode = odd_queue_mode or (
            "successor_pending" if post_seen else "target_empty"
        )
        if queue_mode == "successor_pending":
            if not post_seen:
                raise ValueError("successor_pending requires prior POST")
            png_state = _successor(spec, target)
            png_boundary = _dma_boundary(
                odd_frame,
                pending=1,
                commit_words=(
                    0 if empty_dma_commits else target_commit_words
                ),
                depth=4,
                build_words=440,
            )
        elif queue_mode == "target_empty":
            png_state = copy.deepcopy(post if post_seen else target)
            if not post_seen and spec.name != "terminal_complete_20":
                png_state = _rewrite_engine_only(
                    png_state,
                    label=f"odd-torn-{spec.name}",
                    engine_state=png_state["engine"]["state"],
                    attempt=spec.attempt,
                    tick=spec.tick + 1,
                )
            png_boundary = _dma_boundary(
                odd_frame,
                pending=0,
                commit_words=(
                    0 if empty_dma_commits else target_commit_words
                ),
            )
        else:
            raise ValueError(f"invalid odd queue mode {queue_mode}")
        odd_classification = visual.classify_odd_dma_boundary(
            spec,
            png_state,
            png_boundary,
            expected_frame=odd_frame,
            successor_post=(post if post_seen else None),
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer_sha256,
            expected_commit_words=(
                0 if empty_dma_commits else target_commit_words
            ),
        )
        events.append(
            {
                "index": event_index,
                "kind": kind,
                "frame": odd_frame,
                "queue_mode": odd_classification["queue_mode"],
                "authoritative_sha256": png_state[
                    "authoritative_bundle_sha256"
                ],
                "renderer_sha256": png_state["raw_regions"][
                    "renderer_state"
                ]["sha256"],
                "dma_queue": png_boundary["dma_queue"],
            }
        )
        if not post_seen:
            empty_dma_commits += 1

    assert post_boundary is not None
    assert png_state is not None
    assert png_boundary is not None
    assert odd_classification is not None
    idle_summaries = [
        visual.presentation_boundary_summary(
            index=item["index"],
            phase="pre_odd_empty_dma",
            state=item["state"],
            boundary=item["publication_boundary"],
            classification=item["classification"],
        )
        for item in idle_records
    ]
    odd_summary = visual.presentation_boundary_summary(
        index=len(idle_records),
        phase="odd_dma_precommit",
        state=png_state,
        boundary=png_boundary,
        classification=odd_classification,
    )
    race_order = (
        "post_before_odd"
        if order.index("successor_post")
        < order.index("odd_dma_precommit")
        else "odd_before_post"
    )
    odd_before_post = race_order == "odd_before_post"
    odd_event_index = order.index("odd_dma_precommit")
    post_event_index = order.index("successor_post")
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
    events[odd_event_index].update(
        {
            "png_consumed_while_stopped": not odd_before_post,
            "verified_empty_commit_executed_before_png": odd_before_post,
        }
    )
    events[png_consume_event_index].update(
        {
            "png_consumed_while_stopped": True,
            "png_consume_point": png_consume_point,
        }
    )
    screenshot_sha = _digest(f"png-{spec.name}")
    pixel_sha = _digest(f"pixels-{spec.name}")
    prefix = visual._drive_prefix(spec)
    terminal_service_audit = visual.audit_terminal_presentation_service(
        spec,
        target_post_step=target_post_step,
        checkpoint_hold=terminal_checkpoint_hold,
        service_post=terminal_service_post,
        target_post_boundary=target_post_step_boundary,
        checkpoint_hold_boundary=terminal_checkpoint_hold_boundary,
        service_post_boundary=terminal_service_post_boundary,
    )
    record = {
        "name": spec.name,
        "objective": spec.objective,
        "target": {"attempt": spec.attempt, "tick": spec.tick},
        "session": {
            "session_id": f"visual_{spec.name}",
            "process_id": 1000 + index,
            "fresh_process": True,
            "launch_flags": list(b03.LAUNCH_FLAGS),
            "standard_three_button_pad": True,
            "input_transport": "targeted_PostMessageW",
        },
        "input_drive": {
            "source_module": "tools.m5_b03_runtime_evidence",
            "steps": len(prefix),
            "expected_sha256": hashlib.sha256(prefix).hexdigest(),
            "observed_sha256": hashlib.sha256(prefix).hexdigest(),
            "exact_match": True,
        },
        "gdb": {},
        "target_post_step": copy.deepcopy(target_post_step),
        "terminal_checkpoint_hold": copy.deepcopy(
            terminal_checkpoint_hold
        ),
        "terminal_service_post": copy.deepcopy(terminal_service_post),
        "target_post_renderer": copy.deepcopy(target),
        "published_post_dma": copy.deepcopy(target),
        "ui_service_idle_boundaries": idle_records,
        "successor_post_step": copy.deepcopy(post),
        "successor_post_renderer": None,
        "png_precommit_state": copy.deepcopy(png_state),
        "ui_service_successor": copy.deepcopy(post),
        "objective_state_facts": visual.capture_semantic_facts(
            spec.name, target
        ),
        "frame_binding": {
            "state_machine": "successor-post-vs-odd-dma-v1",
            "ui_screenshot_key": "F1",
            "ui_edge_queued_at": (
                "terminal_service_post_before_renderer"
                if spec.name == "terminal_complete_20"
                else "target_post_step_before_renderer"
            ),
            "target_committed_before_renderer_return": True,
            "target_publication_vblank_delta": 1,
            "target_publication_frame": target_frame,
            "target_renderer_sha256": target_renderer_sha256,
            "target_renderer_exact_at_publication": True,
            "target_sat_sha256": target_sat_sha256,
            "target_sat_exact_at_publication": True,
            "calculated_odd_presentation_frame": odd_frame,
            "publication_after_parity": presentation[
                "publication_after_parity"
            ],
            "expected_idle_preemption_boundary_count": presentation[
                "expected_idle_boundary_count"
            ],
            "expected_odd_delta": presentation["expected_odd_delta"],
            "expected_odd_presentation_frame": odd_frame,
            "competing_breakpoints_armed_once": True,
            "events": events,
            "event_order": order,
            "allowed_event_order": True,
            "pre_odd_empty_dma_count": len(idle_records),
            "pre_odd_nonempty_dma_count": 0,
            "nonempty_dma_commits_between_target_and_png": 0,
            "idle_preemption_boundary_count": len(idle_records),
            "idle_preemption_boundaries": idle_summaries,
            "presentation_boundaries": [*idle_summaries, odd_summary],
            "successor_post_manifest_sha256": post[
                "complete_read_bundle_sha256"
            ],
            "successor_post_coordinate": {
                "engine_state": post["engine"]["state"],
                "attempt": post["engine"]["attempt"],
                "tick": post["engine"]["tick"],
            },
            "successor_post_exact": True,
            "successor_post_step_boundary": post_boundary,
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
            "ui_event_indices": {"down": 10, "up": 11},
            "ui_screenshot_edge_count": 1,
            "ui_screenshot_rearm_count": 0,
            "ui_screenshot_edge_reused_after_noop_commit": odd_before_post,
            "screenshot_count": 1,
            "missing_or_extra_screenshots": False,
            "breakpoint_race_detected": False,
            "target_post_step_boundary": target_post_step_boundary,
            "terminal_checkpoint_hold_boundary": (
                terminal_checkpoint_hold_boundary
            ),
            "terminal_service_post_boundary": (
                terminal_service_post_boundary
            ),
            "terminal_presentation_service": terminal_service_audit,
            "publication_before_renderer": publication_before_renderer,
            "publication_after_renderer": _dma_boundary(
                target_frame,
                pending=0,
                commit_words=target_commit_words,
            ),
        },
        "screenshot": {
            "path": spec.output,
            "bytes": 4096,
            "sha256": screenshot_sha,
            "pixel_sha256": pixel_sha,
            "width": 320,
            "height": 224,
            "format": "PNG RGBA8 non-interlaced",
            "viewport": {"pixels": 320 * 224},
            "opaque_ratio": 1.0,
            "quantized_color_count": 16,
            "luminance_entropy_bits": 2.5,
            "content_bounds": {
                "left": 0,
                "top": 0,
                "right": 319,
                "bottom": 223,
            },
            "publication_verified": True,
            "window_ownership": {
                "process_id": 1000 + index,
                "resolved_owner_process_id": 1000 + index,
                "ui_screenshot_key": "F1",
                "isolated_source_removed_after_verified_copy": True,
            },
        },
    }
    session_lines = _protocol_session_lines(spec.name, order, record)
    audit = visual.audit_capture_breakpoint_order(
        session_lines,
        pre_step=0x2F2,
        post_step=0x336,
        renderer_return=0x33C,
        dma_commit_entry=0x124C,
        event_order=order,
    )
    transcript_sha256 = hashlib.sha256(
        ("\n".join(session_lines) + "\n").encode("utf-8")
    ).hexdigest()
    record["gdb"] = {
        "transcript_sha256": transcript_sha256,
        "guest_write_packets": [],
        "guest_memory_register_pc_writes": False,
        "all_stops_exact": True,
        "capture_breakpoint_order": audit,
    }
    return record


def _captures() -> list[dict]:
    return [
        _capture(spec, index)
        for index, spec in enumerate(visual.CAPTURE_PLAN)
    ]


def _report(captures: list[dict] | None = None) -> dict:
    selected = captures if captures is not None else _captures()
    trace_lines = [
        line
        for record in selected
        for line in _protocol_session_lines(
            str(record.get("name")),
            record.get("frame_binding", {}).get("event_order", []),
            record,
        )
    ]
    trace_sha256 = hashlib.sha256(
        ("\n".join(trace_lines) + ("\n" if trace_lines else "")).encode(
            "utf-8"
        )
    ).hexdigest()
    trace_audit = visual.audit_visual_trace(
        trace_lines,
        selected,
        pre_step=0x2F2,
        post_step=0x336,
        renderer_return=0x33C,
        dma_commit_entry=0x124C,
        vblank=0x200,
        symbols=TEST_SYMBOLS,
    )
    return visual.build_visual_report(
        rom_sha256=_digest("rom"),
        symbol_sha256=_digest("symbols"),
        symbols=TEST_SYMBOLS,
        captures=selected,
        provenance={
            "source": "unit",
            "transcript": {
                "path": "unit-trace.log",
                "sha256": trace_sha256,
                "commands": sum(
                    line.startswith("> ") for line in trace_lines
                ),
                "lines": len(trace_lines),
                "guest_write_packets": [],
            },
            "capture_protocol_audit": trace_audit,
        },
    )

class M5VisualEvidenceTests(unittest.TestCase):
    def test_schedule_is_imported_by_identity_and_plan_is_reviewed(self) -> None:
        schedules = visual.canonical_schedules()
        self.assertIs(schedules[0], b03.ATTEMPT_0_INPUTS)
        self.assertIs(schedules[1], b03.ATTEMPT_1_INPUTS)
        self.assertIs(schedules[2], b03.ATTEMPT_2_INPUTS)
        provenance = visual.schedule_provenance()
        self.assertEqual(provenance["lengths"], [720, 720, 292])
        self.assertEqual(
            [item["tick"] for item in provenance["capture_plan"]],
            [67, 196, 201, 291, 292],
        )

    def test_schedule_length_drift_fails_closed(self) -> None:
        original = b03.ATTEMPT_2_INPUTS
        try:
            b03.ATTEMPT_2_INPUTS = original[:-1]
            with self.assertRaisesRegex(
                EvidenceFailure, "720\\+720\\+292"
            ):
                visual.schedule_provenance()
        finally:
            b03.ATTEMPT_2_INPUTS = original

    def test_odd_presentation_plan_is_exactly_parity_derived(self) -> None:
        even = visual.expected_odd_presentation(100)
        self.assertEqual(even["status"], "pass")
        self.assertEqual(even["publication_after_parity"], "even")
        self.assertEqual(even["expected_idle_boundary_count"], 0)
        self.assertEqual(even["expected_odd_presentation_frame"], 101)
        odd = visual.expected_odd_presentation(101)
        self.assertEqual(odd["status"], "pass")
        self.assertEqual(odd["publication_after_parity"], "odd")
        self.assertEqual(odd["expected_idle_boundary_count"], 1)
        self.assertEqual(odd["expected_odd_presentation_frame"], 103)
        for malformed in (True, -1, 0xFFFFFFFF):
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    visual.expected_odd_presentation(malformed)["status"],
                    "fail",
                )

    def test_isolated_screenshot_binding_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "blastem.cfg"
            path.write_text(
                "bindings {\n"
                "\tp ui.screenshot\n"
                "\tup gamepads.1.up\n"
                "}\n",
                encoding="utf-8",
            )
            result = visual.install_screenshot_binding(path)
            text = path.read_text("utf-8")
            self.assertEqual(result["installed_config_key"], "f1")
            self.assertNotIn("p ui.screenshot", text)
            self.assertEqual(
                re.findall(r"(?m)^\s*(\S+)\s+ui\.screenshot$", text),
                ["f1"],
            )

    def test_source_bindings_cover_direct_evidence_dependencies(self) -> None:
        required = {
            "tools/m2_evidence.py",
            "tools/m3_runtime_evidence.py",
            "tools/m4_visual_evidence.py",
            "tools/m5_b03_runtime_evidence.py",
            "tools/m5_visual_evidence.py",
        }
        self.assertTrue(required.issubset(visual.SOURCE_BINDING_PATHS))
        bindings = visual._source_bindings(ROOT)  # noqa: SLF001
        self.assertTrue(required.issubset(bindings["sources"]))
        self.assertEqual(
            {
                path: item["observed_sha256"]
                for path, item in bindings[
                    "reviewed_renderer_sources"
                ].items()
            },
            visual.PINNED_RENDERER_SOURCE_SHA256,
        )

    def test_reviewed_output_and_renderer_source_pins_fail_closed(self) -> None:
        pin = visual.validate_requested_output_hashes(
            visual.PINNED_B03_ROM_SHA256,
            visual.PINNED_B03_SYMBOL_SHA256,
        )
        self.assertTrue(pin["exact_reviewed_outputs"])
        for rom, symbols in (
            (_digest("wrong-rom"), visual.PINNED_B03_SYMBOL_SHA256),
            (visual.PINNED_B03_ROM_SHA256, _digest("wrong-symbols")),
        ):
            with self.subTest(rom=rom, symbols=symbols):
                with self.assertRaises(EvidenceFailure):
                    visual.validate_requested_output_hashes(rom, symbols)

        real_sha256 = visual.sha256_file

        def drift_hud(path: Path) -> str:
            if path.as_posix().endswith(
                "src/render/gameplay_renderer_hud.asm"
            ):
                return _digest("drifted-hud")
            return real_sha256(path)

        with mock.patch.object(
            visual, "sha256_file", side_effect=drift_hud
        ):
            with self.assertRaisesRegex(
                EvidenceFailure, "reviewed renderer source pins"
            ):
                visual._source_bindings(ROOT)  # noqa: SLF001

    def test_screenshot_binding_rejects_collision_or_extra_action(self) -> None:
        for payload in (
            "p ui.screenshot\nf1 ui.menu\n",
            "p ui.screenshot\nf2 ui.screenshot\n",
        ):
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "blastem.cfg"
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(EvidenceFailure):
                        visual.install_screenshot_binding(path)

    def test_renderer_call_derivation_accepts_exact_jsr_and_bsr(self) -> None:
        jsr_rom = bytearray(0x200)
        jsr_rom[0x20:0x26] = b"\x4E\xB9\x00\x00\x01\x00"
        jsr = visual.derive_renderer_return_address(
            bytes(jsr_rom),
            {
                "MainLoopPostGameplayStep": 0x20,
                "GameplayRendererBuildAndQueue": 0x100,
            },
        )
        self.assertEqual(jsr["encoding"], "jsr_abs_l")
        self.assertEqual(jsr["return_address"], 0x26)
        bsr_rom = bytearray(0x200)
        displacement = 0x100 - 0x42
        bsr_rom[0x40:0x44] = (
            b"\x61\x00" + displacement.to_bytes(2, "big", signed=True)
        )
        bsr = visual.derive_renderer_return_address(
            bytes(bsr_rom),
            {
                "MainLoopPostGameplayStep": 0x40,
                "GameplayRendererBuildAndQueue": 0x100,
            },
        )
        self.assertEqual(bsr["encoding"], "bsr_w")
        self.assertEqual(bsr["return_address"], 0x44)

    def test_all_capture_state_predicates_are_objective_and_complete(self) -> None:
        for spec in visual.CAPTURE_PLAN:
            with self.subTest(capture=spec.name):
                facts = visual.capture_semantic_facts(
                    spec.name, _state(spec)
                )
                self.assertTrue(facts)
                self.assertTrue(all(facts.values()), facts)

    def test_exact_hud_priorities_fail_on_semantic_text_or_palette_drift(
        self,
    ) -> None:
        mutations = {
            "two_echo_armed": ("REPLACE  SELECT ECHO", 2),
            "terminal_hold_19": ("COMMIT HOLD 18/20 KEEP B HELD", 0),
            "terminal_complete_20": (
                "COMMIT HOLD 20/20 KEEP B HELD",
                0,
            ),
        }
        for name, (text, palette) in mutations.items():
            spec = next(item for item in visual.CAPTURE_PLAN if item.name == name)
            state = _state(spec)
            tampered = _with_status_row(
                state,
                label=f"tampered-{name}",
                text=text,
                palette=palette,
            )
            with self.subTest(name=name):
                facts = visual.capture_semantic_facts(name, tampered)
                self.assertFalse(facts["renderer_status_row_exact"], facts)

    def test_node_sat_requires_exact_live_and_disabled_presentations(self) -> None:
        cases = (
            (
                "two_echo_armed",
                visual.RENDERER_TILE_TERMINAL,
                0,
                "armed_node_solid_palette_2",
            ),
            (
                "node_disabled_door_open",
                visual.RENDERER_TILE_TERMINAL_STABLE,
                2,
                "disabled_node_hollow_palette_0",
            ),
        )
        for name, tile, palette, fact_name in cases:
            spec = next(item for item in visual.CAPTURE_PLAN if item.name == name)
            tampered = _with_sat_entry(
                _state(spec),
                label=f"tampered-{name}",
                index=2,
                tile=tile,
                palette=palette,
            )
            with self.subTest(name=name):
                facts = visual.capture_semantic_facts(name, tampered)
                self.assertFalse(facts[fact_name], facts)

    def test_dash_sat_requires_rust_D_badge_and_current_geometry(self) -> None:
        spec = next(
            item
            for item in visual.CAPTURE_PLAN
            if item.name == "dash_route_open"
        )
        variants = (
            (
                _with_sat_entry(
                    _state(spec),
                    label="dash-body-palette",
                    index=0,
                    palette=1,
                ),
                "dash_current_rust_palette_3",
            ),
            (
                _with_sat_entry(
                    _state(spec),
                    label="dash-badge",
                    index=1,
                    tile=visual.RENDERER_CURRENT_BADGE_TILE,
                    palette=1,
                ),
                "dash_current_D_badge_palette_3",
            ),
            (
                _with_sat_entry(
                    _state(spec),
                    label="dash-geometry",
                    index=0,
                    x_delta=1,
                ),
                "dash_current_geometry_preserved",
            ),
        )
        for state, fact_name in variants:
            with self.subTest(fact=fact_name):
                facts = visual.capture_semantic_facts(spec.name, state)
                self.assertFalse(facts[fact_name], facts)

    def test_pre_odd_boundary_treats_authoritative_bytes_as_diagnostic(self) -> None:
        spec = visual.CAPTURE_PLAN[1]
        target = _state(spec)
        target_renderer = target["raw_regions"]["renderer_state"][
            "sha256"
        ]
        torn = copy.deepcopy(target)
        torn["engine"]["state"] = b03.ENGINE_STATE_TERMINAL
        torn["engine"]["tick"] = spec.tick + 17
        torn["authoritative_bundle_sha256"] = _digest("torn")
        boundary = _dma_boundary(102, pending=0, commit_words=440)
        result = visual.classify_ui_service_boundary(
            spec,
            torn,
            boundary,
            previous_frame=101,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_phase="idle",
            target_commit_words=440,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["mode"], "pre_odd_empty_dma")
        self.assertFalse(result["target_authoritative_identity"])
        for field, value in (
            ("frame_pending", 1),
            ("published_depth", 1),
            ("build_depth", 1),
            ("build_words", 1),
            ("commit_words", 439),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(boundary)
                invalid["dma_queue"][field] = value
                classified = visual.classify_ui_service_boundary(
                    spec,
                    torn,
                    invalid,
                    previous_frame=101,
                    target_authoritative_sha256=target[
                        "authoritative_bundle_sha256"
                    ],
                    target_renderer_sha256=target_renderer,
                    expected_phase="idle",
                    target_commit_words=440,
                )
                self.assertEqual(classified["status"], "fail")

    def test_odd_dma_classifier_accepts_only_two_bound_modes(self) -> None:
        spec = visual.CAPTURE_PLAN[1]
        target = _state(spec)
        target_renderer = target["raw_regions"]["renderer_state"][
            "sha256"
        ]
        before_post = copy.deepcopy(target)
        before_post["authoritative_bundle_sha256"] = _digest("torn-odd")
        empty = visual.classify_odd_dma_boundary(
            spec,
            before_post,
            _dma_boundary(101, pending=0, commit_words=440),
            expected_frame=101,
            successor_post=None,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(empty["status"], "pass")
        self.assertEqual(empty["queue_mode"], "target_empty")

        post = _successor(spec, target)
        post["renderer"] = copy.deepcopy(target["renderer"])
        post["raw_regions"]["renderer_state"]["sha256"] = target_renderer
        rendered = _successor(spec, target)
        pending = visual.classify_odd_dma_boundary(
            spec,
            rendered,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=post,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(pending["status"], "pass")
        self.assertEqual(pending["queue_mode"], "successor_pending")
        torn_raw = _raw_map_from_manifest(rendered)
        torn_gameplay = bytearray(torn_raw["gameplay_control"])
        torn_gameplay[0x17] ^= 0x04
        torn_devices = bytearray(torn_raw["device_pool"])
        _put16(
            torn_devices,
            b03.OBJECT_BYTES + 0x14,
            int.from_bytes(
                torn_devices[
                    b03.OBJECT_BYTES + 0x14 : b03.OBJECT_BYTES + 0x16
                ],
                "big",
            )
            + 1,
        )
        torn_raw["gameplay_control"] = bytes(torn_gameplay)
        torn_raw["device_pool"] = bytes(torn_devices)
        torn_pending = _manifest_from_raw("active-next-step-torn", torn_raw)
        self.assertNotEqual(
            torn_pending["authoritative_bundle_sha256"],
            post["authoritative_bundle_sha256"],
        )
        diagnostic = visual.classify_odd_dma_boundary(
            spec,
            torn_pending,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=post,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(diagnostic["status"], "pass", diagnostic)
        self.assertFalse(diagnostic["successor_authoritative_identity"])
        self.assertEqual(
            diagnostic["successor_authoritative_bytes_role"],
            "diagnostic_next_step_preemption",
        )
        unproven = visual.classify_odd_dma_boundary(
            spec,
            rendered,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=None,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(unproven["status"], "fail")

        wrong_coordinate = _rewrite_engine_coordinate(
            rendered,
            label="wrong-renderer-coordinate",
            engine_state=b03.ENGINE_STATE_ACTIVE,
            attempt=spec.attempt,
            tick=spec.tick + 2,
        )
        invalid_renderer = visual.classify_odd_dma_boundary(
            spec,
            wrong_coordinate,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=post,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(invalid_renderer["status"], "fail")

        fault_raw = _raw_map_from_manifest(rendered)
        fault_renderer = bytearray(fault_raw["renderer_state"])
        _put16(
            fault_renderer,
            0x08,
            visual.RENDERER_FLAG_INITIALIZED
            | visual.RENDERER_FLAG_DMA_REJECTED,
        )
        fault_raw["renderer_state"] = bytes(fault_renderer)
        unhealthy = _manifest_from_raw("unhealthy-successor-renderer", fault_raw)
        invalid_health = visual.classify_odd_dma_boundary(
            spec,
            unhealthy,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=post,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(invalid_health["status"], "fail")

        terminal_spec = visual.CAPTURE_PLAN[3]
        terminal_target = _state(terminal_spec)
        terminal_post = _successor(terminal_spec, terminal_target)
        terminal_raw = _raw_map_from_manifest(terminal_post)
        terminal_gameplay = bytearray(terminal_raw["gameplay_control"])
        terminal_gameplay[0x17] ^= 0x04
        terminal_raw["gameplay_control"] = bytes(terminal_gameplay)
        terminal_torn = _manifest_from_raw("terminal-torn", terminal_raw)
        terminal_result = visual.classify_odd_dma_boundary(
            terminal_spec,
            terminal_torn,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=terminal_post,
            target_authoritative_sha256=terminal_target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=terminal_target["raw_regions"][
                "renderer_state"
            ]["sha256"],
            expected_commit_words=440,
        )
        self.assertEqual(terminal_result["status"], "fail")

    def test_completed_terminal_preodd_idle_rejects_authoritative_drift(
        self,
    ) -> None:
        spec = visual.CAPTURE_PLAN[-1]
        target = _state(spec)
        target_renderer = target["raw_regions"]["renderer_state"][
            "sha256"
        ]
        boundary = _dma_boundary(102, pending=0, commit_words=440)
        exact = visual.classify_ui_service_boundary(
            spec,
            target,
            boundary,
            previous_frame=101,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_phase="idle",
            target_commit_words=440,
        )
        self.assertEqual(exact["status"], "pass", exact)

        drift_raw = _raw_map_from_manifest(target)
        gameplay = bytearray(drift_raw["gameplay_control"])
        gameplay[0x17] ^= 0x04
        drift_raw["gameplay_control"] = bytes(gameplay)
        drift = _manifest_from_raw("terminal-preodd-auth-drift", drift_raw)
        invalid = visual.classify_ui_service_boundary(
            spec,
            drift,
            boundary,
            previous_frame=101,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_phase="idle",
            target_commit_words=440,
        )
        self.assertEqual(invalid["status"], "fail", invalid)
        self.assertFalse(invalid["target_authoritative_identity"])

    def test_completed_terminal_odd_before_post_rejects_authoritative_drift(
        self,
    ) -> None:
        spec = visual.CAPTURE_PLAN[-1]
        target = _state(spec)
        target_renderer = target["raw_regions"]["renderer_state"][
            "sha256"
        ]
        boundary = _dma_boundary(101, pending=0, commit_words=440)
        exact = visual.classify_odd_dma_boundary(
            spec,
            target,
            boundary,
            expected_frame=101,
            successor_post=None,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(exact["status"], "pass", exact)

        drift_raw = _raw_map_from_manifest(target)
        gameplay = bytearray(drift_raw["gameplay_control"])
        gameplay[0x17] ^= 0x04
        drift_raw["gameplay_control"] = bytes(gameplay)
        drift = _manifest_from_raw("terminal-odd-auth-drift", drift_raw)
        invalid = visual.classify_odd_dma_boundary(
            spec,
            drift,
            boundary,
            expected_frame=101,
            successor_post=None,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target_renderer,
            expected_commit_words=440,
        )
        self.assertEqual(invalid["status"], "fail", invalid)
        self.assertFalse(invalid["target_authoritative_identity"])

        captures = _captures()
        captures[-1] = _capture(
            spec,
            len(visual.CAPTURE_PLAN) - 1,
            event_order=["odd_dma_precommit", "successor_post"],
        )
        self.assertEqual(_report(captures)["status"], "pass")

    def test_terminal_checkpoint_hold_and_service_are_exact(self) -> None:
        spec = visual.CAPTURE_PLAN[-1]
        record = _capture(spec, len(visual.CAPTURE_PLAN) - 1)
        binding = record["frame_binding"]

        def audit(candidate: dict) -> dict:
            candidate_binding = candidate["frame_binding"]
            return visual.audit_terminal_presentation_service(
                spec,
                target_post_step=candidate["target_post_step"],
                checkpoint_hold=candidate["terminal_checkpoint_hold"],
                service_post=candidate["terminal_service_post"],
                target_post_boundary=candidate_binding[
                    "target_post_step_boundary"
                ],
                checkpoint_hold_boundary=candidate_binding[
                    "terminal_checkpoint_hold_boundary"
                ],
                service_post_boundary=candidate_binding[
                    "terminal_service_post_boundary"
                ],
            )

        exact = audit(record)
        self.assertEqual(exact["status"], "pass", exact)
        self.assertEqual(exact["held_renderer_tick"], 291)
        self.assertEqual(exact["held_renderer_result"], 4)
        self.assertEqual(exact["expected_checkpoint_count"], 6)
        self.assertEqual(exact["fresh_vblank_service_delta"], 1)

        mutations = []

        def mutate_hold_renderer(candidate: dict, offset: int, value: int) -> None:
            raw = _raw_map_from_manifest(
                candidate["terminal_checkpoint_hold"]
            )
            renderer = bytearray(raw["renderer_state"])
            _put16(renderer, offset, value)
            raw["renderer_state"] = bytes(renderer)
            candidate["terminal_checkpoint_hold"] = _manifest_from_raw(
                "mutated-terminal-hold", raw
            )

        mutations.extend(
            (
                (
                    "held_result",
                    lambda item: mutate_hold_renderer(
                        item, visual.RENDERER_LAST_RESULT_OFFSET, 0
                    ),
                ),
                (
                    "held_tick",
                    lambda item: mutate_hold_renderer(item, 0x20, 292),
                ),
                (
                    "checkpoint_ordinal",
                    lambda item: mutate_hold_renderer(
                        item,
                        visual.RENDERER_LAST_CHECKPOINT_COUNT_OFFSET,
                        5,
                    ),
                ),
                (
                    "hold_queue_depth",
                    lambda item: item["frame_binding"][
                        "terminal_checkpoint_hold_boundary"
                    ]["dma_queue"].update(
                        published_depth=1,
                        build_depth=1,
                    ),
                ),
                (
                    "service_frame",
                    lambda item: item["frame_binding"][
                        "terminal_service_post_boundary"
                    ].update(
                        debug_frame_counter=(
                            item["frame_binding"][
                                "terminal_checkpoint_hold_boundary"
                            ]["debug_frame_counter"]
                            + 2
                        )
                    ),
                ),
            )
        )
        for name, mutation in mutations:
            with self.subTest(case=name):
                candidate = copy.deepcopy(record)
                mutation(candidate)
                self.assertEqual(audit(candidate)["status"], "fail")

    def test_terminal_service_trace_is_required_and_raw_bound(self) -> None:
        spec = visual.CAPTURE_PLAN[-1]
        record = _capture(spec, len(visual.CAPTURE_PLAN) - 1)
        audit = record["gdb"]["capture_breakpoint_order"]
        self.assertTrue(audit["terminal_service_required"])
        self.assertEqual(
            audit["terminal_checkpoint_hold_read_binding"][
                "memory_pair_count"
            ],
            67,
        )
        self.assertEqual(
            audit["terminal_service_post_read_binding"][
                "memory_pair_count"
            ],
            67,
        )

        lines = _protocol_session_lines(
            spec.name,
            record["frame_binding"]["event_order"],
            record,
        )
        marker = lines.index(
            "# visual_terminal_complete_20 capture_protocol begin"
        )
        first_renderer = lines.index("< 0000033C", marker)
        service_post = lines.index("< 00000336", first_renderer + 1)
        del lines[service_post - 1 : service_post + 1]
        missing_stop = visual.audit_capture_breakpoint_order(
            lines,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            event_order=record["frame_binding"]["event_order"],
        )
        self.assertEqual(missing_stop["status"], "fail", missing_stop)

        captures = _captures()
        forged_raw = _raw_map_from_manifest(
            captures[-1]["terminal_service_post"]
        )
        forged_renderer = bytearray(forged_raw["renderer_state"])
        _put16(forged_renderer, 0x20, 292)
        forged_raw["renderer_state"] = bytes(forged_renderer)
        captures[-1]["terminal_service_post"] = _manifest_from_raw(
            "forged-service", forged_raw
        )
        self.assertFalse(
            visual._trace_observation_matches_report(  # noqa: SLF001
                audit["terminal_service_post_read_binding"],
                captures[-1]["terminal_service_post"],
                captures[-1]["frame_binding"][
                    "terminal_service_post_boundary"
                ],
                symbols=TEST_SYMBOLS,
            )
        )

    def test_all_five_legal_state_machine_orders_pass(self) -> None:
        orders = (
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
        )
        spec = visual.CAPTURE_PLAN[1]
        for order in orders:
            with self.subTest(order=order):
                captures = _captures()
                captures[1] = _capture(spec, 1, event_order=list(order))
                report = _report(captures)
                failures = [
                    check for check in report["checks"]
                    if check["status"] == "fail"
                ]
                self.assertEqual(failures, [])
                self.assertEqual(report["status"], "pass")

    def test_odd_before_post_consumes_at_guarded_post_after_empty_commit(
        self,
    ) -> None:
        spec = visual.CAPTURE_PLAN[2]
        order = ["odd_dma_precommit", "successor_post"]
        captures = _captures()
        record = _capture(spec, 2, event_order=order)
        captures[2] = record
        binding = record["frame_binding"]
        odd_event, post_event = binding["events"]

        self.assertEqual(binding["race_order"], "odd_before_post")
        self.assertEqual(
            binding["png_consume_point"],
            "successor_post_after_verified_empty_odd_commit",
        )
        self.assertEqual(binding["png_consume_event_kind"], "successor_post")
        self.assertEqual(binding["png_consume_event_index"], 1)
        self.assertTrue(binding["odd_dma_commit_executed_before_png"])
        self.assertTrue(binding["odd_dma_commit_before_png_verified_empty"])
        self.assertTrue(binding["png_consume_guarded_from_next_dma"])
        self.assertEqual(binding["ui_screenshot_edge_count"], 1)
        self.assertEqual(binding["ui_screenshot_rearm_count"], 0)
        self.assertFalse(odd_event["png_consumed_while_stopped"])
        self.assertTrue(
            odd_event["verified_empty_commit_executed_before_png"]
        )
        self.assertTrue(post_event["png_consumed_while_stopped"])

        audit = record["gdb"]["capture_breakpoint_order"]
        self.assertEqual(audit["status"], "pass", audit)
        self.assertEqual(audit["png_consume_event_kind"], "successor_post")
        self.assertEqual(audit["png_consume_event_index"], 1)
        consume_stop = audit["observed_stops"][1]
        self.assertEqual(consume_stop["kind"], "successor_post")
        self.assertEqual(
            consume_stop["png_consume"]["consume_point"],
            binding["png_consume_point"],
        )
        self.assertEqual(_report(captures)["status"], "pass")

    def test_native_screenshot_tool_output_names_exact_consume_stop(
        self,
    ) -> None:
        self.assertEqual(
            visual._native_screenshot_tool_output("odd_dma_precommit"),
            "BlastEm native ODD framebuffer consumed while stopped at the "
            "calculated precommit DMA entry",
        )
        self.assertEqual(
            visual._native_screenshot_tool_output(
                "successor_post_after_verified_empty_odd_commit"
            ),
            "BlastEm native ODD framebuffer consumed while stopped at the "
            "guarded same-frame successor POST after the verified empty "
            "ODD commit",
        )
        with self.assertRaisesRegex(
            visual.EvidenceFailure, "invalid PNG consume point"
        ):
            visual._native_screenshot_tool_output("unbound")

    def test_odd_before_post_runtime_reuses_prearmed_edge_at_post(self) -> None:
        spec = visual.CAPTURE_PLAN[2]
        record = _capture(
            spec,
            2,
            event_order=["odd_dma_precommit", "successor_post"],
        )
        binding = record["frame_binding"]

        timeline: list[str] = []
        consume_arguments: dict[str, object] = {}

        class FakeRemote:
            def __init__(self) -> None:
                self.commands: list[str] = []
                self.pcs = [0x124C, 0x336]

            def request(self, command: str) -> str:
                self.commands.append(command)
                timeline.append(f"remote:{command}")
                if command == "c":
                    return "S05"
                if command == "p11":
                    return f"{self.pcs.pop(0):08X}"
                return "OK"

        remote = FakeRemote()
        transcript: list[str] = []
        input_events = [
            "F1:ui.screenshot:down",
            "F1:ui.screenshot:up",
        ]

        def consume_prearmed_png(**kwargs: object) -> dict[str, str]:
            consume_arguments.update(kwargs)
            timeline.append("consume:prearmed-png")
            return {"sha256": record["screenshot"]["sha256"]}

        with tempfile.TemporaryDirectory() as directory:
            screenshot_directory = Path(directory)
            with (
                mock.patch.object(
                    visual,
                    "_capture_coherent_state",
                    side_effect=[
                        record["png_precommit_state"],
                        record["successor_post_step"],
                    ],
                ),
                mock.patch.object(
                    visual,
                    "_capture_publication_boundary",
                    side_effect=[
                        binding["successor_boundary"],
                        binding["successor_post_step_boundary"],
                    ],
                ),
                mock.patch.object(
                    visual, "_state_manifest", side_effect=lambda state: state
                ),
                mock.patch.object(
                    visual.m4_visual,
                    "_consume_native_screenshot",
                    side_effect=consume_prearmed_png,
                ) as consume,
                mock.patch.object(
                    visual.m4_visual,
                    "_screenshot_directory_state",
                    return_value={},
                ),
                mock.patch.object(visual.b03, "_post_key") as post_key,
            ):
                result = visual._capture_native_frame(
                    remote=remote,
                    root=ROOT,
                    process_id=record["session"]["process_id"],
                    window=1,
                    screenshot_directory=screenshot_directory,
                    output=screenshot_directory / "dash.png",
                    spec=spec,
                    symbols=TEST_SYMBOLS,
                    post_step=0x336,
                    renderer_return=0x33C,
                    dma_commit_entry=0x124C,
                    transcript=transcript,
                    events=input_events,
                    prearmed={
                        "before_files": {},
                        "capture_started_ns": 1,
                        "identity": {},
                        "event_indices": {"down": 0, "up": 1},
                    },
                    target_post_renderer=record["target_post_renderer"],
                    publication_before_renderer=binding[
                        "publication_before_renderer"
                    ],
                    publication_after_renderer=binding[
                        "publication_after_renderer"
                    ],
                )

        consume.assert_called_once()
        post_key.assert_not_called()
        self.assertEqual(remote.commands.count("c"), 2)
        self.assertEqual(
            result["frame_binding"]["png_consume_point"],
            "successor_post_after_verified_empty_odd_commit",
        )
        self.assertEqual(
            result["frame_binding"]["ui_screenshot_rearm_count"], 0
        )
        self.assertEqual(
            consume_arguments["tool_output"],
            "BlastEm native ODD framebuffer consumed while stopped at the "
            "guarded same-frame successor POST after the verified empty "
            "ODD commit",
        )
        self.assertLess(
            timeline.index("remote:z0,336,2"),
            timeline.index("consume:prearmed-png"),
        )
        self.assertLess(
            timeline.index("consume:prearmed-png"),
            timeline.index("remote:z0,124c,2"),
        )

    def test_odd_before_post_rejects_consume_and_empty_commit_tamper(self) -> None:
        spec = visual.CAPTURE_PLAN[2]
        order = ["odd_dma_precommit", "successor_post"]
        mutations = (
            ("odd_dma_commit_before_png_verified_empty", False),
            ("png_consume_event_kind", "odd_dma_precommit"),
            ("png_consume_event_index", 0),
            ("png_consume_point", "odd_dma_precommit"),
            ("ui_screenshot_rearm_count", 1),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                captures = _captures()
                captures[2] = _capture(spec, 2, event_order=order)
                captures[2]["frame_binding"][field] = value
                self.assertEqual(_report(captures)["status"], "fail")

        record = _capture(spec, 2, event_order=order)
        lines = _protocol_session_lines(record["name"], order, record)
        png_begin = lines.index(
            "# visual_dash_route_open png_consume begin"
        )
        png_markers = lines[png_begin : png_begin + 2]
        del lines[png_begin : png_begin + 2]
        protocol_start = lines.index(
            "# visual_dash_route_open capture_protocol begin"
        )
        odd_pc = lines.index("< 0000124C", protocol_start)
        next_continue = lines.index("> c", odd_pc)
        lines[next_continue:next_continue] = png_markers
        audit = visual.audit_capture_breakpoint_order(
            lines,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            event_order=order,
        )
        self.assertEqual(audit["status"], "fail", audit)

    def test_post_before_odd_accepts_target_empty_or_successor_pending(self) -> None:
        spec = visual.CAPTURE_PLAN[2]
        for mode in ("target_empty", "successor_pending"):
            with self.subTest(mode=mode):
                captures = _captures()
                captures[2] = _capture(
                    spec,
                    2,
                    event_order=[
                        "successor_post",
                        "odd_dma_precommit",
                    ],
                    odd_queue_mode=mode,
                )
                self.assertEqual(_report(captures)["status"], "pass")

    def test_report_rejects_invalid_missing_or_duplicate_events(self) -> None:
        variants = []
        captures = _captures()
        captures[0]["frame_binding"]["event_order"] = [
            "successor_post",
            "successor_post",
            "odd_dma_precommit",
        ]
        captures[0]["gdb"]["capture_breakpoint_order"]["event_order"] = (
            captures[0]["frame_binding"]["event_order"]
        )
        variants.append(captures)
        captures = _captures()
        captures[0]["frame_binding"]["event_order"] = [
            "odd_dma_precommit"
        ]
        captures[0]["gdb"]["capture_breakpoint_order"]["event_order"] = (
            captures[0]["frame_binding"]["event_order"]
        )
        variants.append(captures)
        captures = _captures()
        captures[0]["frame_binding"]["events"].append(
            copy.deepcopy(captures[0]["frame_binding"]["events"][0])
        )
        variants.append(captures)
        for captures in variants:
            with self.subTest(order=captures[0]["frame_binding"]["event_order"]):
                self.assertEqual(_report(captures)["status"], "fail")

    def test_report_rejects_preodd_queue_renderer_and_frame_drift(self) -> None:
        order = [
            "pre_odd_empty_dma",
            "successor_post",
            "odd_dma_precommit",
        ]
        spec = visual.CAPTURE_PLAN[1]
        variants = []
        captures = _captures()
        captures[1] = _capture(spec, 1, event_order=order)
        captures[1]["ui_service_idle_boundaries"][0][
            "publication_boundary"
        ]["dma_queue"]["frame_pending"] = 1
        variants.append(captures)
        captures = _captures()
        captures[1] = _capture(spec, 1, event_order=order)
        captures[1]["ui_service_idle_boundaries"][0]["state"][
            "raw_regions"
        ]["renderer_state"]["sha256"] = _digest("renderer-drift")
        variants.append(captures)
        captures = _captures()
        captures[1] = _capture(spec, 1, event_order=order)
        captures[1]["ui_service_idle_boundaries"][0][
            "publication_boundary"
        ]["debug_frame_counter"] += 1
        variants.append(captures)
        for captures in variants:
            self.assertEqual(_report(captures)["status"], "fail")

    def test_report_rejects_wrong_odd_frame_and_unguarded_o_first(self) -> None:
        spec = visual.CAPTURE_PLAN[1]
        captures = _captures()
        captures[1] = _capture(
            spec,
            1,
            event_order=["odd_dma_precommit", "successor_post"],
        )
        captures[1]["frame_binding"]["successor_boundary"][
            "debug_frame_counter"
        ] += 1
        self.assertEqual(_report(captures)["status"], "fail")
        captures = _captures()
        captures[1] = _capture(
            spec,
            1,
            event_order=["odd_dma_precommit", "successor_post"],
        )
        captures[1]["frame_binding"][
            "post_after_odd_guarded_from_next_dma"
        ] = False
        self.assertEqual(_report(captures)["status"], "fail")

    def test_report_requires_exact_target_engine_state(self) -> None:
        captures = _captures()
        for field in (
            "target_post_step",
            "target_post_renderer",
            "published_post_dma",
        ):
            captures[0][field]["engine"]["state"] = (
                b03.ENGINE_STATE_TERMINAL
            )
        self.assertEqual(_report(captures)["status"], "fail")

    def test_capture_breakpoint_transcript_all_five_orders(self) -> None:
        orders = (
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
        )
        for order in orders:
            record = _capture(
                visual.CAPTURE_PLAN[1],
                1,
                event_order=list(order),
            )
            lines = _protocol_session_lines(
                record["name"], list(order), record
            )
            with self.subTest(order=order):
                audit = visual.audit_capture_breakpoint_order(
                    lines,
                    pre_step=0x2F2,
                    post_step=0x336,
                    renderer_return=0x33C,
                    dma_commit_entry=0x124C,
                    event_order=list(order),
                )
                self.assertEqual(audit["status"], "pass", audit)
                self.assertEqual(audit["event_order"], list(order))
                self.assertTrue(audit["persistent_dma_breakpoint"])

        duplicate = lines.copy()
        duplicate.extend(["> Z0,124c,2", "< OK"])
        audit = visual.audit_capture_breakpoint_order(
            duplicate,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            event_order=list(orders[-1]),
        )
        self.assertEqual(audit["status"], "fail")

    def test_capture_grammar_rejects_grouped_reordered_or_extra_control(self) -> None:
        order = ["successor_post", "odd_dma_precommit"]
        record = _capture(visual.CAPTURE_PLAN[0], 0, event_order=order)
        base = _protocol_session_lines("two_echo_armed", order, record)
        protocol_start = base.index(
            "# visual_two_echo_armed capture_protocol begin"
        )
        variants = []

        grouped = base.copy()
        first_continue = grouped.index("> c", protocol_start)
        grouped[first_continue + 1], grouped[first_continue + 2] = (
            grouped[first_continue + 2],
            grouped[first_continue + 1],
        )
        variants.append(("grouped_control_before_stop", grouped))

        reordered = base.copy()
        post_insert = reordered.index("> Z0,336,2", protocol_start)
        dma_insert = reordered.index("> Z0,124c,2", protocol_start)
        post_pair = reordered[post_insert : post_insert + 2]
        dma_pair = reordered[dma_insert : dma_insert + 2]
        reordered[post_insert : post_insert + 2] = dma_pair
        reordered[dma_insert : dma_insert + 2] = post_pair
        variants.append(("reordered_breakpoint_inserts", reordered))

        extra_control = base.copy()
        final_dma_remove = extra_control.index(
            "# visual_two_echo_armed capture_protocol end"
        ) - 2
        extra_control[final_dma_remove:final_dma_remove] = [
            "> c",
            "< S05",
            "> p11",
            "< 0000124C",
        ]
        variants.append(("extra_control_stop", extra_control))

        split_response = base.copy()
        dma_insert = split_response.index("> Z0,124c,2", protocol_start)
        split_response[dma_insert + 1 : dma_insert + 1] = [
            "> mff1400,1",
            "< 00",
        ]
        variants.append(("read_between_command_response", split_response))

        extra_pc = base.copy()
        target_read = next(
            index
            for index, line in enumerate(extra_pc)
            if index > protocol_start and line.startswith("> m")
        )
        extra_pc[target_read:target_read] = [
            "> p11",
            "< 0000033C",
        ]
        variants.append(("extra_pc_read", extra_pc))

        for name, lines in variants:
            with self.subTest(case=name):
                audit = visual.audit_capture_breakpoint_order(
                    lines,
                    pre_step=0x2F2,
                    post_step=0x336,
                    renderer_return=0x33C,
                    dma_commit_entry=0x124C,
                    event_order=order,
                )
                self.assertEqual(audit["status"], "fail", audit)

    def test_capture_grammar_rejects_read_and_png_timing_tamper(self) -> None:
        order = ["successor_post", "odd_dma_precommit"]
        record = _capture(visual.CAPTURE_PLAN[0], 0, event_order=order)
        base = _protocol_session_lines("two_echo_armed", order, record)
        marker = base.index(
            "# visual_two_echo_armed capture_protocol begin"
        )
        first_read = next(
            index
            for index, line in enumerate(base)
            if index > marker and line.startswith("> m")
        )
        variants = []

        missing = base.copy()
        del missing[first_read : first_read + 2]
        variants.append(("missing_exact_read", missing))

        duplicate = base.copy()
        duplicate[first_read:first_read] = duplicate[
            first_read : first_read + 2
        ]
        variants.append(("duplicate_exact_read", duplicate))

        wrong_address = base.copy()
        command = wrong_address[first_read]
        address_text, size_text = command[3:].split(",")
        wrong_address[first_read] = (
            f"> m{int(address_text, 16) + 2:x},{size_text}"
        )
        variants.append(("wrong_exact_read_address", wrong_address))

        png_late = base.copy()
        png_begin = png_late.index(
            "# visual_two_echo_armed png_consume begin"
        )
        png_markers = png_late[png_begin : png_begin + 2]
        del png_late[png_begin : png_begin + 2]
        final_remove = png_late.index("> z0,124c,2", png_begin)
        png_late[final_remove + 2 : final_remove + 2] = png_markers
        variants.append(("png_markers_after_next_control", png_late))

        for name, lines in variants:
            with self.subTest(case=name):
                audit = visual.audit_capture_breakpoint_order(
                    lines,
                    pre_step=0x2F2,
                    post_step=0x336,
                    renderer_return=0x33C,
                    dma_commit_entry=0x124C,
                    event_order=order,
                )
                self.assertEqual(audit["status"], "fail", audit)

    def test_exact_observation_shape_includes_sat_shadow(self) -> None:
        self.assertEqual(
            sum(
                (size + 0xFE) // 0xFF
                for _name, _address, size in visual.STATE_READ_REGION_SPECS
            )
            + len(visual.BOUNDARY_READ_REGION_SPECS),
            67,
        )
        self.assertEqual(
            next(
                size
                for name, _address, size in visual.STATE_READ_REGION_SPECS
                if name == "renderer_state"
            ),
            0x500,
        )
        record = _capture(visual.CAPTURE_PLAN[0], 0)
        audit = record["gdb"]["capture_breakpoint_order"]
        self.assertEqual(
            audit["target_post_read_binding"]["memory_pair_count"], 67
        )
        self.assertEqual(
            audit["target_renderer_read_binding"]["memory_pair_count"], 67
        )
        self.assertTrue(
            all(
                item["memory_pair_count"] == 67
                for item in audit["observed_stops"]
            )
        )

    def test_successor_pending_requires_exact_four_record_queue(self) -> None:
        spec = visual.CAPTURE_PLAN[2]
        target = _state(spec)
        post = _successor(spec, target)
        rendered = _successor(spec, target)
        valid = visual.classify_odd_dma_boundary(
            spec,
            rendered,
            _dma_boundary(
                101,
                pending=1,
                commit_words=440,
                depth=4,
                build_words=440,
            ),
            expected_frame=101,
            successor_post=post,
            target_authoritative_sha256=target[
                "authoritative_bundle_sha256"
            ],
            target_renderer_sha256=target["raw_regions"]["renderer_state"][
                "sha256"
            ],
            expected_commit_words=440,
        )
        self.assertEqual(valid["status"], "pass")
        for depth, words in (
            (1, 440),
            (3, 440),
            (5, 440),
            (4, 1),
            (4, 439),
            (4, 441),
        ):
            with self.subTest(depth=depth, words=words):
                result = visual.classify_odd_dma_boundary(
                    spec,
                    rendered,
                    _dma_boundary(
                        101,
                        pending=1,
                        commit_words=440,
                        depth=depth,
                        build_words=words,
                    ),
                    expected_frame=101,
                    successor_post=post,
                    target_authoritative_sha256=target[
                        "authoritative_bundle_sha256"
                    ],
                    target_renderer_sha256=target["raw_regions"][
                        "renderer_state"
                    ]["sha256"],
                    expected_commit_words=440,
                )
                self.assertEqual(result["status"], "fail", result)

    def test_terminal_resolution_requires_exact_empty_or_republish_queue(self) -> None:
        spec = visual.CAPTURE_PLAN[-1]
        target = _state(spec)
        renderer_sha = target["raw_regions"]["renderer_state"]["sha256"]
        for pending, depth, words in ((0, 0, 0), (1, 4, 440)):
            result = visual.classify_odd_dma_boundary(
                spec,
                target,
                _dma_boundary(
                    101,
                    pending=pending,
                    commit_words=440,
                    depth=depth,
                    build_words=words,
                ),
                expected_frame=101,
                successor_post=target,
                target_authoritative_sha256=target[
                    "authoritative_bundle_sha256"
                ],
                target_renderer_sha256=renderer_sha,
                expected_commit_words=440,
            )
            self.assertEqual(result["status"], "pass", result)
        for pending, depth, words in (
            (0, 1, 0),
            (0, 0, 1),
            (1, 3, 440),
            (1, 4, 439),
        ):
            boundary = _dma_boundary(
                101,
                pending=pending,
                commit_words=440,
                depth=depth,
                build_words=words,
            )
            result = visual.classify_odd_dma_boundary(
                spec,
                target,
                boundary,
                expected_frame=101,
                successor_post=target,
                target_authoritative_sha256=target[
                    "authoritative_bundle_sha256"
                ],
                target_renderer_sha256=renderer_sha,
                expected_commit_words=440,
            )
            self.assertEqual(result["status"], "fail", result)

    def test_ui_terminal_resolution_also_requires_exact_queue_shape(self) -> None:
        spec = visual.CAPTURE_PLAN[-1]
        target = _state(spec)
        renderer_sha = target["raw_regions"]["renderer_state"]["sha256"]
        for pending, depth, words in ((0, 0, 0), (1, 4, 440)):
            result = visual.classify_ui_service_boundary(
                spec,
                target,
                _dma_boundary(
                    101,
                    pending=pending,
                    commit_words=440,
                    depth=depth,
                    build_words=words,
                ),
                previous_frame=100,
                target_authoritative_sha256=target[
                    "authoritative_bundle_sha256"
                ],
                target_renderer_sha256=renderer_sha,
                expected_phase="resolution",
                target_commit_words=440,
            )
            self.assertEqual(result["status"], "pass", result)
        for pending, depth, words in (
            (0, 1, 0),
            (0, 0, 1),
            (1, 3, 440),
            (1, 4, 439),
        ):
            result = visual.classify_ui_service_boundary(
                spec,
                target,
                _dma_boundary(
                    101,
                    pending=pending,
                    commit_words=440,
                    depth=depth,
                    build_words=words,
                ),
                previous_frame=100,
                target_authoritative_sha256=target[
                    "authoritative_bundle_sha256"
                ],
                target_renderer_sha256=renderer_sha,
                expected_phase="resolution",
                target_commit_words=440,
            )
            self.assertEqual(result["status"], "fail", result)

    def test_full_trace_reparse_binds_five_exact_sessions(self) -> None:
        captures = _captures()
        lines = [
            line
            for record in captures
            for line in _protocol_session_lines(
                record["name"],
                record["frame_binding"]["event_order"],
                record,
            )
        ]
        audit = visual.audit_visual_trace(
            lines,
            captures,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            vblank=0x200,
            symbols=TEST_SYMBOLS,
        )
        self.assertEqual(audit["status"], "pass", audit)
        self.assertEqual(audit["capture_count"], 5)
        self.assertTrue(audit["session_ranges_nonoverlapping"])

        tampered_lines = lines.copy()
        session_end = tampered_lines.index(
            "# visual_node_disabled_door_open end"
        )
        tampered_lines[session_end:session_end] = [
            "> c",
            "< S05",
        ]
        self.assertEqual(
            visual.audit_visual_trace(
                tampered_lines,
                captures,
                pre_step=0x2F2,
                post_step=0x336,
                renderer_return=0x33C,
                dma_commit_entry=0x124C,
                vblank=0x200,
                symbols=TEST_SYMBOLS,
            )["status"],
            "fail",
        )

        tampered_report = copy.deepcopy(captures)
        tampered_report[0]["gdb"]["transcript_sha256"] = _digest(
            "wrong-session-trace"
        )
        self.assertEqual(
            visual.audit_visual_trace(
                lines,
                tampered_report,
                pre_step=0x2F2,
                post_step=0x336,
                renderer_return=0x33C,
                dma_commit_entry=0x124C,
                vblank=0x200,
                symbols=TEST_SYMBOLS,
            )["status"],
            "fail",
        )

    def test_full_trace_rejects_prefix_suffix_and_raw_report_tamper(self) -> None:
        captures = _captures()
        lines = [
            line
            for record in captures
            for line in _protocol_session_lines(
                record["name"],
                record["frame_binding"]["event_order"],
                record,
            )
        ]
        variants = []
        prefix_control = lines.copy()
        protocol = prefix_control.index(
            "# visual_two_echo_armed capture_protocol begin"
        )
        prefix_control[protocol:protocol] = ["> c", "< S05"]
        variants.append(prefix_control)

        prefix_response = lines.copy()
        first_stop = prefix_response.index("< S05")
        prefix_response[first_stop] = "< S00"
        variants.append(prefix_response)

        suffix_control = lines.copy()
        first_end = suffix_control.index("# visual_two_echo_armed end")
        suffix_control[first_end:first_end] = ["> Z0,dead,2", "< OK"]
        variants.append(suffix_control)

        stray_response = lines.copy()
        stray_response[first_end:first_end] = ["< OK"]
        variants.append(stray_response)

        for candidate in variants:
            audit = visual.audit_visual_trace(
                candidate,
                captures,
                pre_step=0x2F2,
                post_step=0x336,
                renderer_return=0x33C,
                dma_commit_entry=0x124C,
                vblank=0x200,
                symbols=TEST_SYMBOLS,
            )
            self.assertEqual(audit["status"], "fail", audit)

        tampered_report = copy.deepcopy(captures)
        _retag_raw_region(
            tampered_report[0]["target_post_renderer"],
            "engine_control",
            "trace-report-mismatch",
        )
        audit = visual.audit_visual_trace(
            lines,
            tampered_report,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            vblank=0x200,
            symbols=TEST_SYMBOLS,
        )
        self.assertEqual(audit["status"], "fail", audit)

    def test_trace_rejects_fabricated_coherent_successor_manifest(self) -> None:
        captures = _captures()
        lines = [
            line
            for record in captures
            for line in _protocol_session_lines(
                record["name"],
                record["frame_binding"]["event_order"],
                record,
            )
        ]
        forged_raw = _raw_map_from_manifest(
            captures[0]["successor_post_step"]
        )
        gameplay = bytearray(forged_raw["gameplay_control"])
        gameplay[0x17] ^= 0x04
        forged_raw["gameplay_control"] = bytes(gameplay)
        forged = _manifest_from_raw("coherent-forged-successor", forged_raw)
        self.assertEqual(
            visual.audit_state_manifest_reconstruction(
                forged, symbols=TEST_SYMBOLS
            )["status"],
            "pass",
        )
        self.assertNotEqual(
            forged["authoritative_bundle_sha256"],
            captures[0]["successor_post_step"][
                "authoritative_bundle_sha256"
            ],
        )
        captures[0]["successor_post_step"] = forged
        captures[0]["ui_service_successor"] = copy.deepcopy(forged)
        audit = visual.audit_visual_trace(
            lines,
            captures,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            vblank=0x200,
            symbols=TEST_SYMBOLS,
        )
        self.assertEqual(audit["status"], "fail", audit)
        self.assertFalse(audit["captures"][0]["state_trace_matches"])

    def test_raw_manifest_and_target_commit_tamper_fail_report(self) -> None:
        captures = _captures()
        raw = captures[0]["published_post_dma"]["raw_regions"][
            "engine_control"
        ]
        raw["raw_hex"] = ("00" if raw["raw_hex"][:2] != "00" else "01") + raw[
            "raw_hex"
        ][2:]
        self.assertEqual(_report(captures)["status"], "fail")

        captures = _captures()
        captures[0]["frame_binding"]["publication_after_renderer"][
            "dma_queue"
        ]["commit_words"] = 0
        self.assertEqual(_report(captures)["status"], "fail")

    def test_parsed_state_cannot_be_co_mutated_away_from_bound_raw(self) -> None:
        captures = _captures()
        manifest = captures[0]["published_post_dma"]
        self.assertEqual(
            visual.audit_state_manifest_raw(manifest)["status"], "pass"
        )
        manifest["engine"]["magic"] ^= 1
        manifest["devices"][0]["timer"] += 1
        audit = visual.audit_state_manifest_reconstruction(
            manifest, symbols=TEST_SYMBOLS
        )
        self.assertEqual(audit["status"], "fail", audit)
        self.assertFalse(audit["exact_manifest_equality"])
        self.assertIn("engine", audit["mismatched_top_level_fields"])
        self.assertIn("devices", audit["mismatched_top_level_fields"])
        self.assertEqual(_report(captures)["status"], "fail")

    def test_prefix_initial_state_is_raw_reconstructed_and_exact(self) -> None:
        captures = _captures()
        lines = [
            line
            for record in captures
            for line in _protocol_session_lines(
                record["name"],
                record["frame_binding"]["event_order"],
                record,
            )
        ]
        audit = visual.audit_visual_trace(
            lines,
            captures,
            pre_step=0x2F2,
            post_step=0x336,
            renderer_return=0x33C,
            dma_commit_entry=0x124C,
            vblank=0x200,
            symbols=TEST_SYMBOLS,
        )
        prefix = audit["captures"][0]["prefix_audit"]
        self.assertTrue(prefix["initial_state_reconstructed_from_trace"])
        self.assertTrue(prefix["initialization_ok"])
        self.assertTrue(
            b03.initialization_ok(prefix["initial_state_manifest"])
        )
        first_control = lines.index(
            f"> m{b03.ENGINE_CONTROL_BASE:x},"
            f"{b03.ENGINE_CONTROL_USED_BYTES:x}"
        )
        response = lines[first_control + 1]
        raw = bytearray.fromhex(response[2:])
        _put16(raw, 0x10, 1)
        tampered = lines.copy()
        tampered[first_control + 1] = f"< {raw.hex().upper()}"
        self.assertEqual(
            visual.audit_visual_trace(
                tampered,
                captures,
                pre_step=0x2F2,
                post_step=0x336,
                renderer_return=0x33C,
                dma_commit_entry=0x124C,
                vblank=0x200,
                symbols=TEST_SYMBOLS,
            )["status"],
            "fail",
        )

    def test_valid_report_is_capture_only_and_critic_hash_ready(self) -> None:
        report = _report()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["acceptance_status"], "evidence_ready")
        self.assertEqual(visual.report_exit_code(report), 0)
        self.assertFalse(
            report["objective_only"]["subjective_scores_emitted"]
        )
        self.assertTrue(report["objective_only"]["critic_handoff_required"])
        handoff = report["critic_handoff"]
        self.assertEqual(
            handoff["reserved_schema"],
            "r94-m5-b03-independent-visual-review-v1",
        )
        self.assertRegex(
            handoff["capture_manifest_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertEqual(len(handoff["captures"]), 5)

    def test_missing_extra_duplicate_capture_and_duplicate_pid_fail(self) -> None:
        captures = _captures()
        variants = [
            captures[:-1],
            captures + [copy.deepcopy(captures[0])],
        ]
        duplicate_name = copy.deepcopy(captures)
        duplicate_name[1]["name"] = duplicate_name[0]["name"]
        variants.append(duplicate_name)
        duplicate_pid = copy.deepcopy(captures)
        duplicate_pid[1]["session"]["process_id"] = duplicate_pid[0][
            "session"
        ]["process_id"]
        variants.append(duplicate_pid)
        for candidate in variants:
            self.assertEqual(_report(candidate)["status"], "fail")

    def test_state_fault_write_packet_duplicate_png_and_blank_fail(self) -> None:
        variants = []
        captures = _captures()
        captures[0]["published_post_dma"]["devices"][2]["state"] = 0
        variants.append(captures)
        captures = _captures()
        captures[1]["published_post_dma"]["renderer"]["flags"] |= (
            visual.RENDERER_FLAG_INVALID_STATE
        )
        variants.append(captures)
        captures = _captures()
        captures[2]["gdb"]["guest_write_packets"] = ["> Mff0000,1:20"]
        variants.append(captures)
        captures = _captures()
        captures[2]["screenshot"]["sha256"] = captures[0]["screenshot"][
            "sha256"
        ]
        variants.append(captures)
        captures = _captures()
        captures[3]["screenshot"]["quantized_color_count"] = 1
        variants.append(captures)
        for candidate in variants:
            self.assertEqual(_report(candidate)["status"], "fail")

    def test_probe_hashes_no_guest_write_or_stale_envelope_api(self) -> None:
        probe = (ROOT / "tools/m5_visual_probe.ps1").read_text("utf-8")
        collector = (ROOT / "tools/m5_visual_evidence.py").read_text(
            "utf-8"
        )
        self.assertEqual(probe.count("[Parameter(Mandatory = $true)]"), 2)
        self.assertIn(visual.PINNED_B03_ROM_SHA256, probe)
        self.assertIn(visual.PINNED_B03_SYMBOL_SHA256, probe)
        self.assertIn('--expected-sha256", required=True', collector)
        self.assertIn('--expected-symbol-sha256", required=True', collector)
        self.assertNotRegex(
            collector,
            r'''remote\.request\(f?["'](?:G|M|P|X)''',
        )
        self.assertNotIn("authoritative_transition_envelope", collector)
        self.assertNotIn("_capture_native_frame_superseded", collector)
        self.assertEqual(collector.count("def _capture_native_frame("), 1)
        self.assertEqual(
            collector.count(
                'remote.request(f"Z0,{dma_commit_entry:x},2")'
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
