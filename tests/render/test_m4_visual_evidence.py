from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from tools.m4_visual_evidence import (
    ACTOR_BYTES,
    ACTOR_COUNT,
    CANONICAL_ATTEMPT_0,
    EXPECTED_DEBUG_ROM_SHA256,
    FIRST_RUNNING_VBLANK_ROLE,
    GAMEPLAY_FLAG_DOOR_OPEN,
    GAMEPLAY_FLAG_INITIALIZED,
    GAMEPLAY_FLAG_PROGRAM_STABLE,
    GAMEPLAY_FLAG_SWITCH_HELD,
    GAMEPLAY_RESULT_PROGRAM_STABLE,
    GAMEPLAY_TERMINAL_PROGRAM_STABLE,
    INPUT_B,
    INPUT_C,
    OPEN_CAPTURE_CURRENT_X,
    OPEN_CAPTURE_TICK,
    RENDERER_FLAG_INITIALIZED,
    RENDERER_FLAG_PROGRAM_STABLE,
    RENDERER_FLAG_RESET_ACTIVE,
    REPORT_SCHEMA,
    RUBRIC_CATEGORIES,
    SCREENSHOT_FILES,
    SCREENSHOT_KEYS_BY_CAPTURE,
    SOURCE_BINDING_PATHS,
    STARTUP_PRIMING_ROLE,
    VISUAL_REVIEWS,
    _capture_emulator_screenshot,
    _capture_rendered_post_step,
    _review,
    analyze_png,
    build_playability_report,
    build_visual_report,
    decode_png_rgba,
    derive_post_dma_commit_address,
    install_evidence_screenshot_bindings,
    parse_actor,
    parse_device,
    parse_gameplay_control,
    parse_renderer_state,
    parse_replay_descriptor,
    parse_timeline,
    pixel_difference_ratio,
    source_binding,
)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + crc.to_bytes(4, "big")
    )


def rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    if len(pixels) != width * height * 4:
        raise ValueError("pixel fixture has the wrong size")
    filtered = b"".join(
        b"\x00" + pixels[row * width * 4 : (row + 1) * width * 4]
        for row in range(height)
    )
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 6, 0, 0, 0))
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(filtered))
        + png_chunk(b"IEND", b"")
    )


def indexed_png(
    width: int,
    height: int,
    indices: bytes,
    palette: bytes,
    transparency: bytes | None = None,
) -> bytes:
    if len(indices) != width * height:
        raise ValueError("indexed fixture has the wrong size")
    filtered = b"".join(
        b"\x00" + indices[row * width : (row + 1) * width]
        for row in range(height)
    )
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 3, 0, 0, 0))
    )
    chunks = png_chunk(b"IHDR", ihdr) + png_chunk(b"PLTE", palette)
    if transparency is not None:
        chunks += png_chunk(b"tRNS", transparency)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunks
        + png_chunk(b"IDAT", zlib.compress(filtered))
        + png_chunk(b"IEND", b"")
    )


def gameplay(
    *,
    flags: int = GAMEPLAY_FLAG_INITIALIZED,
    result: int = 0,
    door: int = 0,
    switch: int = 0,
    pressed: int = 0,
    commit: int = 0,
    stable_tick: int = 0,
) -> dict:
    return {
        "magic": 0x52393447,
        "magic_ascii": "R94G",
        "version": 1,
        "declared_bytes": 0x100,
        "flags": flags,
        "result": result,
        "door_state": door,
        "switch_mask": switch,
        "current_actor_index": 6,
        "echo_actor_count": 0,
        "current_input": 0,
        "current_pressed": pressed,
        "current_released": 0,
        "current_previous": 0,
        "phase": 0,
        "last_commit_reason": commit,
        "replacement_flags": 1,
        "last_bank_crc": 0,
        "last_bank_crc_hex": "00000000",
        "stable_tick": stable_tick,
        "step_count": stable_tick,
        "raw_sha256": "1" * 64,
    }


def engine(
    *,
    attempt: int,
    tick: int,
    state: int = 1,
    terminal_reason: int = 0,
) -> dict:
    return {
        "magic": 0x52393445,
        "magic_ascii": "R94E",
        "abi_version": 1,
        "control_bytes": 0x400,
        "state": state,
        "flags": 3,
        "encounter": 0,
        "attempt": attempt,
        "tick": tick,
        "terminal_reason": terminal_reason,
        "last_error": 0,
        "overflow_flags": 0,
        "projection_length": 3638,
        "state_crc": 0x12345678,
        "checkpoint_tick": tick,
        "checkpoint_count": 1,
        "input_held": 0,
        "input_pressed": 0,
        "input_released": 0,
        "input_previous": 0,
        "facing_x": 1,
        "active_actor_count": 1,
        "room_left": 0,
        "room_top": 0,
        "room_right": 320,
        "room_bottom": 224,
        "spawn_x": 32 << 8,
        "spawn_y": 112 << 8,
        "rect_count": 2,
        "object_init_count": 4,
        "pool_counts": [1, 0, 1, 1, 1, 0, 0],
        "pool_high_water": [1, 0, 1, 1, 1, 0, 0],
    }


def timeline(*, echoes: int, bank: int, length: int) -> dict:
    return {
        "magic": 0x52393454,
        "magic_ascii": "R94T",
        "version": 1,
        "declared_bytes": 24,
        "echo_count": echoes,
        "current_bank": bank,
        "echo_order": (
            [6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
            if echoes
            else [0xFF] * 6
        ),
        "selected_ordinal": 0xFF,
        "flags": 1,
        "pending_reason": 0,
        "attempt_length": length,
        "last_result": 0,
        "raw_sha256": "2" * 64,
    }


def actor(slot: int, x: int, *, flags: int = 0, logical: int = 0) -> dict:
    return {
        "slot": slot,
        "active": 1,
        "role": slot,
        "flags": flags,
        "x_16_8": x << 8,
        "y_16_8": 112 << 8,
        "vx_16_8": 0,
        "vy_16_8": 0,
        "half_width": 4,
        "half_height": 4,
        "input": 0,
        "previous_input": 0,
        "health": 1,
        "logical_id": logical,
    }


def renderer(
    *,
    flags: int = RENDERER_FLAG_INITIALIZED,
    attempt: int = 0,
    tick: int = 0,
    reset_phase: int = 0,
) -> dict:
    return {
        "magic": 0x52393456,
        "magic_ascii": "R94V",
        "version": 1,
        "declared_bytes": 0x500,
        "flags": flags,
        "last_attempt": attempt,
        "reset_phase": reset_phase,
        "reset_previous_row": 0xFFFF,
        "overlay_clear_destination": 0xFFFF,
        "overlay_trace_destination": 0xFFFF,
        "sprite_count": 13,
        "scanline_sprite_max": 5,
        "scanline_pixel_max": 64,
        "dropped_sprites": 0,
        "dma_rejects": 0,
        "backlog_frames": 0,
        "last_tick": tick,
        "last_result": 0,
        "raw_sha256": "3" * 64,
    }


def debug() -> dict:
    return {
        "fatal_code": 0,
        "vblank_overrun_count": 0,
        "dma_overflow_count": 0,
        "dma_required_drop_count": 0,
    }


def state(
    *,
    attempt: int,
    tick: int,
    echoes: int,
    current_x: int,
    echo_x: int = 32,
    engine_state: int = 1,
    terminal_reason: int = 0,
    game: dict | None = None,
    render: dict | None = None,
    occupancy: int = 0,
) -> dict:
    descriptor = {
        "length": 0,
        "crc32_iso_hdlc": 0,
        "crc32_iso_hdlc_hex": "00000000",
        "flags": 0x04,
        "generation": 0,
        "raw_hex": "0000000000000400",
    }
    return {
        "label": "fixture",
        "engine": engine(
            attempt=attempt,
            tick=tick,
            state=engine_state,
            terminal_reason=terminal_reason,
        ),
        "gameplay": game if game is not None else gameplay(),
        "timeline": timeline(
            echoes=echoes, bank=0 if echoes else 6, length=tick
        ),
        "actors": {
            "echo_1": actor(0, echo_x, logical=6),
            "current": actor(6, current_x),
            "region_sha256": "4" * 64,
        },
        "b01_device": {
            "active": 1,
            "logical_id": 0,
            "state": 0,
            "x_16_8": 80 << 8,
            "y_16_8": 112 << 8,
            "vx_16_8": 0,
            "vy_16_8": 0,
            "timer": 0,
            "occupancy_mask": occupancy,
            "raw_sha256": "5" * 64,
        },
        "renderer": (
            render
            if render is not None
            else renderer(attempt=attempt, tick=tick)
        ),
        "debug": debug(),
        "input": {
            "held": 0,
            "pressed": 0,
            "released": 0,
            "raw_high": 0,
            "raw_low": 0,
            "connected": 1,
            "start_held": 0,
        },
        "replay_bank_6": {
            "descriptor": descriptor,
            "bank_sha256": "6" * 64,
            "committed_prefix_sha256": "7" * 64,
            "committed_prefix_crc32_iso_hdlc": "00000000",
            "zero_tail": True,
            "first_40_hex": "00" * 40,
        },
        "raw_bundle_sha256": f"{tick % 16:x}" * 64,
        "raw_bundle_bytes": 1200,
    }


def screenshot(name: str, index: int) -> dict:
    screenshot_root = f"C:/tmp/{name}/screenshots"
    reviewed_sha256 = VISUAL_REVIEWS[name][
        "reviewed_screenshot_sha256"
    ]
    return {
        "path": SCREENSHOT_FILES[name],
        "bytes": 38_000 + index,
        "sha256": reviewed_sha256,
        "width": 656,
        "height": 519,
        "format": "PNG indexed8 non-interlaced",
        "source_color_type": 3,
        "viewport": {
            "left": 8,
            "top": 31,
            "right": 648,
            "bottom": 511,
            "pixels": 307_200,
        },
        "opaque_ratio": 1.0,
        "dark_pixel_ratio": 0.40,
        "light_pixel_ratio": 0.02,
        "red_dominant_ratio": 0.04,
        "cyan_dominant_ratio": 0.005,
        "rust_dominant_ratio": 0.06,
        "cobalt_dominant_ratio": 0.02,
        "quantized_color_count": 64,
        "luminance_entropy_bits": 2.6,
        "horizontal_edge_ratio": 0.02,
        "hud_light_pixel_ratio": 0.08,
        "content_bounds": {
            "left": 8,
            "top": 31,
            "right": 647,
            "bottom": 510,
        },
        "pixel_sha256": f"{index + 6:x}" * 64,
        "tool_output": "Captured owned window",
        "mtime_ns": 200,
        "captured_utc": "2026-07-26T00:00:00+00:00",
        "window_ownership": {
            "process_id": 100 + index,
            "window_handle": 123,
            "resolved_owner_process_id": 100 + index,
            "title": (
                "BlastEm"
                if name == "zero_echo"
                else "RECURSION 94: THE LAST CARTRIDGE - BlastEm - 60.0 fps"
            ),
            "title_matches": True,
            "title_is_rom_specific": name != "zero_echo",
            "title_policy": "fixture explicit generic-title fallback",
            "capture_method": (
                "BlastEm ui.screenshot via isolated one-shot key binding"
            ),
            "isolated_screenshot_path": screenshot_root,
            "isolated_source_name": f"fixture-{name}.png",
            "source_sha256": reviewed_sha256,
            "isolated_source_removed_after_verified_copy": True,
            "ui_screenshot_key": SCREENSHOT_KEYS_BY_CAPTURE[name][0],
        },
    }


def capture(
    name: str,
    index: int,
    before: dict,
    live: dict,
    published: dict | None = None,
) -> dict:
    terminal = before["engine"]["state"] == 2
    expected_delta = 0 if terminal else 2
    after_down = copy.deepcopy(before)
    if not terminal:
        after_down["engine"]["tick"] += 1
    after_down["raw_bundle_sha256"] = "e" * 64
    value = {
        "label": name,
        "screenshot": screenshot(name, index),
        "capture_started_ns": 100,
        "state_before_ui_request": before,
        "state_after_ui_key_down": after_down,
        "state": live,
        "post_capture_state_sha256": live["raw_bundle_sha256"],
        "ui_screenshot_progression_valid": True,
        "ui_key_release_serviced_before_capture_stop": True,
        "emulator_resumes_during_ui_capture": 3 if terminal else 2,
        "native_frame_presented_after_ui_key_release": terminal,
        "ui_screenshot_key": SCREENSHOT_KEYS_BY_CAPTURE[name][0],
        "authoritative_ticks_during_ui_capture": expected_delta,
    }
    if published is not None:
        value["published_state"] = published
        value["observation_boundary"] = {
            "symbol": "MainLoopPostGameplayStep",
            "post_commit_address": "00001670",
            "capture_address": (
                "00001670" if terminal else "000002F6"
            ),
        }
    return value


def screenshot_binding_fixture() -> dict:
    return {
        "status": "pass",
        "removed_default_key": "p",
        "base_profile_keys_unmapped": True,
        "keys_unique": True,
        "shared_key_across_isolated_sessions": True,
        "installed_config_keys": ["f1"],
        "capture_to_config_key": {
            name: key[0].lower()
            for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
        },
        "capture_to_windows_key": {
            name: {
                "name": key[0],
                "virtual_key": key[1],
                "scan_code": key[2],
                "extended": key[3],
            }
            for name, key in SCREENSHOT_KEYS_BY_CAPTURE.items()
        },
        "config_sha256": "8" * 64,
    }


def session_metadata_fixture(name: str, process_id: int) -> dict:
    key = SCREENSHOT_KEYS_BY_CAPTURE[name][0]
    return {
        "process_id": process_id,
        "initial_breakpoint_symbol": "VBlankInterrupt",
        "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
        "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
        "counters_and_controller_poll_begin": FIRST_RUNNING_VBLANK_ROLE,
        "step_breakpoint_symbol": "MainLoopPostGameplayStep",
        "attempt_1_input_staging_breakpoint_symbol": (
            "TimelineHandleMetaInput"
        ),
        "attempt_1_input_staging_role": (
            "after_controller_poll_before_commit_reset"
        ),
        "controller_profile": {
            "status": "pass",
            "port_1_assignment": "gamepad3.1",
            "screenshot_bindings": screenshot_binding_fixture(),
            "session_isolation": {
                "status": "pass",
                "screenshot_path": f"C:/tmp/{name}/screenshots",
            },
        },
        "input_events": [
            f"{key}:ui.screenshot:down",
            f"{key}:ui.screenshot:up",
        ],
        "transcript_sha256": f"{process_id % 16:x}" * 64,
    }


def evidence_fixture() -> tuple[dict, dict, dict, list[str]]:
    zero_published = state(
        attempt=0,
        tick=8,
        echoes=0,
        current_x=44,
    )
    zero_before = copy.deepcopy(zero_published)
    zero_before["engine"]["tick"] = 9
    zero_before["raw_bundle_sha256"] = "8" * 64
    zero_before["input"]["held"] = 0x08
    zero_live = copy.deepcopy(zero_before)
    zero_live["engine"]["tick"] = 11
    zero_live["raw_bundle_sha256"] = "9" * 64
    reset_published = state(
        attempt=1,
        tick=0,
        echoes=1,
        current_x=32,
        render=renderer(
            flags=RENDERER_FLAG_INITIALIZED | RENDERER_FLAG_RESET_ACTIVE,
            attempt=1,
            tick=0,
            reset_phase=11,
        ),
        game=gameplay(commit=2),
    )
    reset_published["replay_bank_6"]["descriptor"].update(
        {
            "length": 40,
            "crc32_iso_hdlc": 0x96501AB7,
            "crc32_iso_hdlc_hex": "96501AB7",
            "flags": 0x03,
            "generation": 1,
        }
    )
    reset_published["replay_bank_6"]["first_40_hex"] = (
        CANONICAL_ATTEMPT_0.hex().upper()
    )
    reset_frame = copy.deepcopy(reset_published)
    reset_frame["engine"]["tick"] = 3
    reset_frame["renderer"]["last_tick"] = 3
    reset_frame["renderer"]["reset_phase"] = 8
    reset_before = copy.deepcopy(reset_frame)
    reset_before["engine"]["tick"] = 4
    reset_before["raw_bundle_sha256"] = "9" * 64
    reset_before["input"]["held"] = 0
    reset_live = copy.deepcopy(reset_before)
    reset_live["engine"]["tick"] = 6
    reset_live["raw_bundle_sha256"] = "a" * 64
    reset_live["input"]["held"] = 0

    open_game = gameplay(
        flags=(
            GAMEPLAY_FLAG_INITIALIZED
            | GAMEPLAY_FLAG_DOOR_OPEN
            | GAMEPLAY_FLAG_SWITCH_HELD
        ),
        door=3,
        switch=1,
        commit=2,
    )
    open_published = state(
        attempt=1,
        tick=OPEN_CAPTURE_TICK,
        echoes=1,
        current_x=OPEN_CAPTURE_CURRENT_X,
        echo_x=80,
        occupancy=1,
        game=open_game,
    )
    open_live = copy.deepcopy(open_published)
    open_before = copy.deepcopy(open_published)
    open_before["engine"]["tick"] = OPEN_CAPTURE_TICK + 1
    open_before["raw_bundle_sha256"] = "a" * 64
    open_before["input"]["held"] = 0x08
    open_live["engine"]["tick"] = OPEN_CAPTURE_TICK + 3
    open_live["raw_bundle_sha256"] = "b" * 64
    open_live["input"]["held"] = 0x08

    stable_game = gameplay(
        flags=(
            GAMEPLAY_FLAG_INITIALIZED
            | GAMEPLAY_FLAG_PROGRAM_STABLE
            | GAMEPLAY_FLAG_DOOR_OPEN
            | GAMEPLAY_FLAG_SWITCH_HELD
        ),
        result=GAMEPLAY_RESULT_PROGRAM_STABLE,
        door=7,
        switch=1,
        commit=2,
        stable_tick=166,
    )
    stable_published = state(
        attempt=1,
        tick=166,
        echoes=1,
        current_x=281,
        echo_x=80,
        engine_state=2,
        terminal_reason=GAMEPLAY_TERMINAL_PROGRAM_STABLE,
        occupancy=1,
        game=stable_game,
        render=renderer(
            flags=(
                RENDERER_FLAG_INITIALIZED | RENDERER_FLAG_PROGRAM_STABLE
            ),
            attempt=1,
            tick=166,
        ),
    )
    stable_live = copy.deepcopy(stable_published)
    stable_live["raw_bundle_sha256"] = "c" * 64
    stable_before = copy.deepcopy(stable_live)

    failed_game = gameplay(pressed=INPUT_B)
    failed_published = state(
        attempt=0,
        tick=101,
        echoes=0,
        current_x=108,
        game=failed_game,
    )
    failed_published["actors"]["current"]["flags"] = 1
    failed_live = copy.deepcopy(failed_published)
    failed_before = copy.deepcopy(failed_published)
    failed_before["engine"]["tick"] = 102
    failed_before["raw_bundle_sha256"] = "c" * 64
    failed_live["engine"]["tick"] = 104
    failed_live["raw_bundle_sha256"] = "d" * 64

    recovered = state(
        attempt=1,
        tick=0,
        echoes=1,
        current_x=32,
        game=gameplay(commit=2),
    )
    manual_commit_entry = state(
        attempt=0,
        tick=40,
        echoes=0,
        current_x=80,
    )
    manual_commit_entry["input"].update(
        {
            "held": INPUT_C,
            "pressed": INPUT_C,
            "released": 0,
            "start_held": 1,
        }
    )
    fresh_metadata = {
        name: session_metadata_fixture(name, 100 + index)
        for index, name in enumerate(SCREENSHOT_FILES)
    }
    canonical = {
        "session_id": "canonical_visual",
        "metadata": {
            "process_id": 100,
            "process_ids": [100, 101, 102, 103],
            "controller_profile": fresh_metadata["zero_echo"][
                "controller_profile"
            ],
            "capture_sessions": {
                name: fresh_metadata[name]
                for name in tuple(SCREENSHOT_FILES)[:4]
            },
            "input_events": [
                event
                for name in tuple(SCREENSHOT_FILES)[:4]
                for event in fresh_metadata[name]["input_events"]
            ],
            "transcript_sha256": "e" * 64,
        },
        "attempt_0_inputs": list(CANONICAL_ATTEMPT_0),
        "attempt_1_inputs": [0x08] * 165 + [0x28],
        "manual_commit_entries": {
            name: copy.deepcopy(manual_commit_entry)
            for name in (
                "reset_trace",
                "echo_door_open",
                "program_stable",
            )
        },
        "reset_state": reset_published,
        "input_injection": {
            "status": "pass",
            "guest_ram_register_or_pc_writes": False,
        },
        "captures": {
            "zero_echo": capture(
                "zero_echo",
                0,
                zero_before,
                zero_live,
                zero_published,
            ),
            "reset_trace": capture(
                "reset_trace",
                1,
                reset_before,
                reset_live,
                reset_frame,
            ),
            "echo_door_open": capture(
                "echo_door_open",
                2,
                open_before,
                open_live,
                open_published,
            ),
            "program_stable": capture(
                "program_stable",
                3,
                stable_before,
                stable_live,
                stable_published,
            ),
        },
    }
    blocked = {
        "session_id": "blocked_then_recover",
        "metadata": {
            **fresh_metadata["blocked_path"],
            "capture_sessions": {
                "blocked_path": fresh_metadata["blocked_path"]
            },
            "transcript_sha256": "f" * 64,
        },
        "input_injection": {
            "status": "pass",
            "guest_ram_register_or_pc_writes": False,
        },
        "blocked_capture": capture(
            "blocked_path",
            4,
            failed_before,
            failed_live,
            failed_published,
        ),
        "recovered_state": recovered,
    }
    provenance = {
        "rom": {
            "path": "build/recursion94-debug.bin",
            "sha256": EXPECTED_DEBUG_ROM_SHA256,
        },
        "emulator": {
            "path": ".toolcache/blastem/blastem.exe",
            "sha256": "1" * 64,
        },
        "launch_flags": ["-D", "-g", "-m", "gen", "-r", "U"],
        "symbol_map": {"sha256": "2" * 64},
        "default_config": {"source_sha256": "3" * 64},
        "source_binding": {"bundle_sha256": "4" * 64},
        "transcript": {"sha256": "5" * 64},
        "post_dma_commit": {
            "post_commit_address_hex": "00001670",
            "derivation": "fixture symbol derivation",
        },
        "image_differences": [
            {
                "first": first,
                "second": second,
                "difference_ratio": 0.01,
            }
            for first, second in zip(
                list(SCREENSHOT_FILES), list(SCREENSHOT_FILES)[1:]
            )
        ],
    }
    return canonical, blocked, provenance, ["> mfff000,78", "< 00"]


class PngDecoderTests(unittest.TestCase):
    def test_strict_rgba_round_trip(self) -> None:
        pixels = bytes(
            (
                0,
                0,
                0,
                255,
                255,
                255,
                255,
                255,
                0,
                64,
                128,
                255,
                128,
                32,
                0,
                255,
            )
        )
        width, height, decoded = decode_png_rgba(rgba_png(2, 2, pixels))
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(decoded, pixels)

    def test_strict_indexed_round_trip_with_bounded_trns(self) -> None:
        payload = indexed_png(
            2,
            2,
            bytes((0, 1, 2, 1)),
            bytes((0, 0, 0, 255, 255, 255, 128, 32, 0)),
            bytes((0, 128)),
        )
        width, height, decoded = decode_png_rgba(payload)
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(
            decoded,
            bytes(
                (
                    0,
                    0,
                    0,
                    0,
                    255,
                    255,
                    255,
                    128,
                    128,
                    32,
                    0,
                    255,
                    255,
                    255,
                    255,
                    128,
                )
            ),
        )

    def test_indexed_png_requires_valid_palette(self) -> None:
        payload = indexed_png(
            1, 1, bytes((1,)), bytes((0, 0, 0))
        )
        with self.assertRaisesRegex(ValueError, "exceeds PLTE"):
            decode_png_rgba(payload)
        missing_plte = payload.replace(
            png_chunk(b"PLTE", bytes((0, 0, 0))), b""
        )
        with self.assertRaisesRegex(ValueError, "requires PLTE"):
            decode_png_rgba(missing_plte)

    def test_indexed_png_rejects_oversized_trns(self) -> None:
        payload = indexed_png(
            1,
            1,
            bytes((0,)),
            bytes((0, 0, 0)),
            bytes((255, 255)),
        )
        with self.assertRaisesRegex(ValueError, "tRNS"):
            decode_png_rgba(payload)

    def test_invalid_chunk_crc_is_rejected(self) -> None:
        payload = bytearray(rgba_png(1, 1, bytes((0, 0, 0, 255))))
        payload[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            decode_png_rgba(bytes(payload))

    def test_trailing_payload_is_rejected(self) -> None:
        payload = rgba_png(1, 1, bytes((0, 0, 0, 255))) + b"x"
        with self.assertRaisesRegex(ValueError, "trailing"):
            decode_png_rgba(payload)

    def test_analysis_and_pixel_difference_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.png"
            second = Path(temporary) / "second.png"
            black = bytes((0, 0, 0, 255)) * (32 * 32)
            changed = bytearray(black)
            center = ((16 * 32) + 16) * 4
            changed[center : center + 4] = bytes((255, 255, 255, 255))
            first.write_bytes(rgba_png(32, 32, black))
            second.write_bytes(rgba_png(32, 32, bytes(changed)))
            facts = analyze_png(first)
            difference = pixel_difference_ratio(first, second)
        self.assertEqual(facts["format"], "PNG RGBA8 non-interlaced")
        self.assertGreater(difference, 0)


class RuntimeParserTests(unittest.TestCase):
    def test_gameplay_parser_reads_explicit_widths(self) -> None:
        raw = bytearray(0x24)
        raw[0:4] = b"R94G"
        raw[4:6] = (1).to_bytes(2, "big")
        raw[6:8] = (0x100).to_bytes(2, "big")
        raw[8:10] = (0x19).to_bytes(2, "big")
        raw[12:14] = (3).to_bytes(2, "big")
        raw[14:16] = (1).to_bytes(2, "big")
        parsed = parse_gameplay_control(bytes(raw))
        self.assertEqual(parsed["magic_ascii"], "R94G")
        self.assertEqual(parsed["flags"], 0x19)
        self.assertEqual(parsed["door_state"], 3)
        self.assertEqual(parsed["switch_mask"], 1)

    def test_timeline_parser_rejects_truncation(self) -> None:
        with self.assertRaises(ValueError):
            parse_timeline(bytes(23))

    def test_actor_parser_is_bounded(self) -> None:
        raw = bytearray(ACTOR_COUNT * ACTOR_BYTES)
        raw[0] = 1
        raw[4:8] = (80 << 8).to_bytes(4, "big")
        self.assertEqual(parse_actor(bytes(raw), 0)["x_16_8"], 80 << 8)
        with self.assertRaises(ValueError):
            parse_actor(bytes(raw), ACTOR_COUNT)

    def test_device_renderer_and_descriptor_sizes_are_strict(self) -> None:
        with self.assertRaises(ValueError):
            parse_device(bytes(23))
        with self.assertRaises(ValueError):
            parse_renderer_state(bytes(35))
        with self.assertRaises(ValueError):
            parse_replay_descriptor(bytes(7))


class BindingTests(unittest.TestCase):
    def test_terminal_ui_release_gets_one_full_native_frame(self) -> None:
        class Remote:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def request(self, command: str) -> str:
                self.commands.append(command)
                return "S05"

        states = []
        for digest in "abcd":
            states.append(
                {
                    "engine": {
                        "attempt": 1,
                        "tick": 166,
                        "state": 2,
                    },
                    "raw_bundle_sha256": digest * 64,
                }
            )
        events: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch(
                    "tools.m4_visual_evidence._capture_state",
                    side_effect=states,
                ),
                patch(
                    "tools.m4_visual_evidence._window_identity",
                    return_value={"process_id": 7},
                ),
                patch(
                    "tools.m4_visual_evidence._screenshot_directory_state",
                    return_value={},
                ),
                patch("tools.m4_visual_evidence._post_key"),
                patch(
                    "tools.m4_visual_evidence._consume_native_screenshot",
                    return_value={"path": "terminal.png"},
                ),
            ):
                remote = Remote()
                capture = _capture_emulator_screenshot(
                    remote=remote,  # type: ignore[arg-type]
                    root=root,
                    process_id=7,
                    window=9,
                    screenshot_directory=root / "native",
                    screenshot_key=("F1", 0x70, 0x3B, False),
                    label="program_stable",
                    output=root / "terminal.png",
                    input_events=events,
                    terminal_full_frame_after_release=True,
                )
        self.assertEqual(remote.commands, ["c", "c", "c"])
        self.assertEqual(capture["emulator_resumes_during_ui_capture"], 3)
        self.assertTrue(
            capture["native_frame_presented_after_ui_key_release"]
        )
        self.assertEqual(
            capture["state_after_ui_key_up"]["raw_bundle_sha256"],
            "c" * 64,
        )
        self.assertEqual(capture["state"]["raw_bundle_sha256"], "d" * 64)

    def test_terminal_prearm_waits_for_second_post_dma_presentation(
        self,
    ) -> None:
        class Remote:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def request(self, command: str) -> str:
                self.commands.append(command)
                return "S05" if command == "c" else "OK"

        terminal = {
            "engine": {
                "attempt": 1,
                "tick": 166,
                "state": 2,
                "terminal_reason": GAMEPLAY_TERMINAL_PROGRAM_STABLE,
            },
            "raw_bundle_sha256": "a" * 64,
        }
        committed = copy.deepcopy(terminal)
        committed["raw_bundle_sha256"] = "b" * 64
        presented = copy.deepcopy(terminal)
        presented["raw_bundle_sha256"] = "c" * 64
        remote = Remote()
        events: list[str] = []
        identity = {
            "process_id": 7,
            "resolved_owner_process_id": 7,
            "window_handle": 9,
        }
        screenshot = {"path": "terminal.png", "sha256": "d" * 64}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch(
                    "tools.m4_visual_evidence._capture_state",
                    side_effect=(terminal, committed, presented),
                ),
                patch(
                    "tools.m4_visual_evidence._window_identity",
                    return_value=identity,
                ),
                patch(
                    "tools.m4_visual_evidence._screenshot_directory_state",
                    return_value={},
                ),
                patch("tools.m4_visual_evidence._post_key"),
                patch(
                    "tools.m4_visual_evidence._consume_native_screenshot",
                    return_value=screenshot,
                ),
            ):
                capture = _capture_rendered_post_step(
                    remote=remote,  # type: ignore[arg-type]
                    root=root,
                    process_id=7,
                    window=9,
                    screenshot_directory=root / "native",
                    screenshot_key=("F1", 0x70, 0x3B, False),
                    input_events=events,
                    label="program_stable",
                    output=root / "terminal.png",
                    post_step=0x2F6,
                    post_dma_commit=0x1670,
                    expected_attempt=1,
                    expected_tick=166,
                    terminal_prearmed_ui=True,
                )
        self.assertEqual(
            remote.commands,
            [
                "z0,2f6,2",
                "Z0,1670,2",
                "c",
                "c",
                "z0,1670,2",
            ],
        )
        self.assertEqual(
            capture["observation_boundary"]["capture_address"],
            "00001670",
        )
        self.assertEqual(
            capture["observation_boundary"]["ui_service_boundary"],
            "pre-armed key edge, then two successive post-DMA stops",
        )
        self.assertEqual(capture["emulator_resumes_during_ui_capture"], 2)
        self.assertEqual(capture["authoritative_ticks_during_ui_capture"], 0)
        self.assertEqual(
            events,
            [
                "F1:ui.screenshot:down",
                "F1:ui.screenshot:up",
            ],
        )

    def test_one_proven_key_is_reused_only_across_isolated_profiles(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "blastem.cfg"
            config.write_text(
                "bindings {\n"
                "\tkeys {\n"
                "\t\tp ui.screenshot\n"
                "\t\tf2 cassette.play\n"
                "\t}\n"
                "}\n",
                encoding="utf-8",
            )
            installed = install_evidence_screenshot_bindings(config)
            text = config.read_text("utf-8")
        self.assertEqual(installed["status"], "pass")
        self.assertTrue(installed["base_profile_keys_unmapped"])
        self.assertTrue(installed["keys_unique"])
        self.assertTrue(installed["shared_key_across_isolated_sessions"])
        self.assertEqual(installed["installed_config_keys"], ["f1"])
        self.assertEqual(
            installed["capture_to_config_key"],
            {
                "zero_echo": "f1",
                "reset_trace": "f1",
                "echo_door_open": "f1",
                "program_stable": "f1",
                "blocked_path": "f1",
            },
        )
        self.assertNotIn("\tp ui.screenshot", text)
        self.assertEqual(text.count("ui.screenshot"), 1)

    def test_preexisting_evidence_key_mapping_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "blastem.cfg"
            config.write_text(
                "bindings {\n"
                "\tkeys {\n"
                "\t\tp ui.screenshot\n"
                "\t\tf1 ui.pause\n"
                "\t}\n"
                "}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "already mapped"):
                install_evidence_screenshot_bindings(config)

    def test_derives_unique_post_dma_fallthrough_from_rom(self) -> None:
        rom = bytearray(0x200)
        call = 0x30
        target = 0x80
        displacement = target - (call + 2)
        rom[call : call + 4] = (
            b"\x61\x00" + displacement.to_bytes(2, "big", signed=True)
        )
        derived = derive_post_dma_commit_address(
            bytes(rom),
            {
                "VBlankInterrupt": 0x20,
                "DmaCommitPreparedQueue": target,
            },
        )
        self.assertEqual(derived["call_address"], call)
        self.assertEqual(derived["post_commit_address"], call + 4)

    def test_missing_dma_call_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            derive_post_dma_commit_address(
                bytes(0x200),
                {
                    "VBlankInterrupt": 0x20,
                    "DmaCommitPreparedQueue": 0x80,
                },
            )

    def test_source_binding_is_deterministic_and_drift_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, relative in enumerate(SOURCE_BINDING_PATHS):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{index}\n", encoding="utf-8")
            first = source_binding(root)
            second = source_binding(root)
            changed = root / SOURCE_BINDING_PATHS[0]
            changed.write_text("changed\n", encoding="utf-8")
            third = source_binding(root)
        self.assertEqual(first, second)
        self.assertNotEqual(
            first["bundle_sha256"], third["bundle_sha256"]
        )


class ReviewContractTests(unittest.TestCase):
    def test_all_review_tables_cover_exact_prompt_categories(self) -> None:
        expected_hashes = {
            "zero_echo": (
                "71a72a4c319d343f78fd4221232f83344ba39bd45c27d243a7814f2127dc4ea7"
            ),
            "reset_trace": (
                "f1cadd8a55d78857966d67be5bda50d6f06db36d697c68f1ac577ad1f522c9f0"
            ),
            "echo_door_open": (
                "e4cdfbd346e7d43532980277b89e457d55f88b584435e35bc2b45a2584394f93"
            ),
            "program_stable": (
                "4b1f348c571bad1e6a917f156e758a0765e983b88772bfb0cef7a133da85bcd0"
            ),
            "blocked_path": (
                "56dfca4582867266007ea681ae2d80ec880feef836040fa3638b712b5562ff77"
            ),
        }
        for name, review in VISUAL_REVIEWS.items():
            self.assertEqual(
                [item["category"] for item in review["categories"]],
                list(RUBRIC_CATEGORIES),
            )
            self.assertEqual(review["status"], "pass")
            self.assertGreaterEqual(review["minimum_score"], 4)
            self.assertGreaterEqual(review["average_score"], 4.2)
            self.assertFalse(
                review["critical_gameplay_readability_defects"]
            )
            self.assertFalse(review["severe_sprite_dropout_or_corruption"])
            self.assertTrue(review["capture_available"])
            self.assertEqual(
                review["reviewed_screenshot_sha256"],
                expected_hashes[name],
            )
            self.assertEqual(
                review["review_round"],
                "final_hash_reset_silhouette_round",
            )

    def test_sub_four_category_fails_review(self) -> None:
        scores = {category: 5 for category in RUBRIC_CATEGORIES}
        evidence = {
            category: f"concrete {category}" for category in RUBRIC_CATEGORIES
        }
        scores["hazard_legibility"] = 3
        review = _review(scores, evidence)
        self.assertEqual(review["status"], "fail")
        self.assertEqual(review["minimum_score"], 3)

    def test_missing_category_is_rejected(self) -> None:
        scores = {category: 5 for category in RUBRIC_CATEGORIES[:-1]}
        evidence = {
            category: "evidence" for category in RUBRIC_CATEGORIES
        }
        with self.assertRaisesRegex(ValueError, "mismatch"):
            _review(scores, evidence)


class VisualReportTests(unittest.TestCase):
    def make_report(self) -> tuple[dict, dict, dict, list[str], dict]:
        canonical, blocked, provenance, transcript = evidence_fixture()
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        return canonical, blocked, provenance, transcript, report

    def test_complete_owned_capture_fixture_passes_final_rubric(self) -> None:
        _canonical, _blocked, _provenance, _transcript, report = (
            self.make_report()
        )
        self.assertEqual(report["schema"], REPORT_SCHEMA)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(
            report["startup_boundary"]["initial_breakpoint_role"],
            STARTUP_PRIMING_ROLE,
        )
        self.assertEqual(
            report["startup_boundary"]["authoritative_origin"],
            FIRST_RUNNING_VBLANK_ROLE,
        )
        self.assertEqual(
            {
                item["check"]
                for item in report["checks"]
                if item["status"] == "fail"
            },
            set(),
        )

    def test_missing_startup_priming_role_fails_closed(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["metadata"]["capture_sessions"]["zero_echo"].pop(
            "initial_breakpoint_role"
        )
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "startup_priming_and_running_vblank_origin_explicit", failures
        )

    def test_missing_pre_reset_input_staging_fails_closed(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["metadata"]["capture_sessions"]["program_stable"].pop(
            "attempt_1_input_staging_role"
        )
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "attempt_1_input_staged_before_commit_reset", failures
        )

    def test_unrelated_desktop_pixels_and_title_fail_closed(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        image = canonical["captures"]["zero_echo"]["screenshot"]
        image["dark_pixel_ratio"] = 0.0437
        image["light_pixel_ratio"] = 0.3227
        image["quantized_color_count"] = 754
        image["window_ownership"].update(
            {
                "title": "Unrelated video player",
                "title_matches": False,
                "resolved_owner_process_id": 999,
            }
        )
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "all_pngs_fresh_owned_and_structurally_decoded", failures
        )
        self.assertIn("nonblank_nonfatal_readable_image_set", failures)

    def test_missing_authored_rust_landmark_fails_image_gate(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["captures"]["zero_echo"]["screenshot"][
            "rust_dominant_ratio"
        ] = 0.0039
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("nonblank_nonfatal_readable_image_set", failures)

    def test_visual_review_hash_must_match_captured_png(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["captures"]["zero_echo"]["screenshot"]["sha256"] = (
            "0" * 64
        )
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "visual_reviews_bind_exact_captured_png_hashes", failures
        )

    def test_debugger_memory_write_packet_is_rejected(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        transcript.append("> Mff0000,1:00")
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("controller_window_input_only", failures)

    def test_unbound_ui_screenshot_progression_is_rejected(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["captures"]["zero_echo"][
            "post_capture_state_sha256"
        ] = "0" * 64
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "all_captures_bind_exact_ui_screenshot_progression", failures
        )

    def test_active_and_terminal_capture_deltas_are_strict(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["captures"]["zero_echo"][
            "authoritative_ticks_during_ui_capture"
        ] = 0
        canonical["captures"]["program_stable"][
            "authoritative_ticks_during_ui_capture"
        ] = 1
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "all_captures_bind_exact_ui_screenshot_progression", failures
        )

    def test_echo_door_causality_mutation_is_rejected(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["captures"]["echo_door_open"]["published_state"][
            "b01_device"
        ]["occupancy_mask"] = 0
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn("echo_alone_holds_switch_and_opens_door", failures)

    def test_renderer_drop_counter_is_rejected(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        canonical["captures"]["program_stable"]["state"]["renderer"][
            "dropped_sprites"
        ] = 1
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "renderer_capacity_and_current_capture_frame_have_no_fault",
            failures,
        )

    def test_blocked_path_false_completion_is_rejected(self) -> None:
        canonical, blocked, provenance, transcript = evidence_fixture()
        failed = blocked["blocked_capture"]["published_state"]
        failed["engine"]["state"] = 2
        failed["engine"]["terminal_reason"] = GAMEPLAY_TERMINAL_PROGRAM_STABLE
        report = build_visual_report(
            rom_sha256=EXPECTED_DEBUG_ROM_SHA256,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
            transcript_lines=transcript,
        )
        failures = {
            item["check"]
            for item in report["checks"]
            if item["status"] == "fail"
        }
        self.assertIn(
            "plausible_current_only_failure_is_blocked_not_solved", failures
        )

    def test_playability_report_is_explicitly_not_literal_human(self) -> None:
        canonical, blocked, provenance, transcript, visual = (
            self.make_report()
        )
        qa = build_playability_report(
            visual_report=visual,
            canonical=canonical,
            blocked=blocked,
            provenance=provenance,
        )
        self.assertEqual(qa["status"], "pass")
        self.assertIn("not literal human", qa["classification"].lower())


if __name__ == "__main__":
    unittest.main()
