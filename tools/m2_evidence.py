#!/usr/bin/env python3
"""Collect hash-bound Milestone 2 RAM evidence through BlastEm's GDB stub.

The live path is intentionally Windows-only because deterministic controller
injection targets the BlastEm window. Parsing and report construction remain
platform-neutral so they can be unit-tested without launching the emulator.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


DEBUG_BASE = 0x00FFF000
DEBUG_BLOCK_BYTES = 0x48
SAT_SHADOW = 0x00FF8000
SAT_ENTRY_BYTES = 8
Z80_STATUS_SHADOW = 0x00FF9900
Z80_STATUS_BYTES = 16
EXPECTED_DEBUG_MAGIC = 0x52393444
EXPECTED_DEBUG_ABI_VERSION = 1
EXPECTED_DEBUG_ABI_SIZE = 0x400
BOOT_STAGE_VIDEO_READY = 3
BOOT_STAGE_RUNNING = 4
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"
INPUT_UP = 0x01
INPUT_DOWN = 0x02
INPUT_LEFT = 0x04
INPUT_RIGHT = 0x08
M2_FLAG_INPUT_ACTIVE = 0x0001
M2_FLAG_SPRITE_ACTIVE = 0x0002
M2_FLAG_DMA_ACTIVE = 0x0004
M2_FLAG_Z80_UPLOADED = 0x0008
M2_FLAG_YM2612_ACTIVE = 0x0010
M2_FLAG_PSG_ACTIVE = 0x0020
HARDWARE_FLAG_Z80_TIMEOUT = 0x0001
DMA_QUEUE_CAPACITY = 32
DMA_NORMAL_SAT_WORDS = 320
DMA_ORDINARY_WORD_LIMIT = 2048
SPRITE_HARDWARE_LIMIT = 80
SPRITE_SCANLINE_LIMIT = 20
# A one-sprite hardware fixture uses 1x1. The integrated gameplay scene starts
# with the current actor's 3x3 body, encoded by the VDP as size nibble $A.
PRIMARY_GAMEPLAY_SPRITE_SIZE_CODE = 0x0A
Z80_STATUS_SIGNATURE = 0x94
Z80_STATUS_ABI_VERSION = 0x01
Z80_STATE_PLAYING = 0x02
Z80_FLAG_READY = 0x01
Z80_FLAG_YM_ACTIVE = 0x02
Z80_FLAG_PSG_ACTIVE = 0x04
Z80_FLAG_QUEUE_FAULT = 0x10
Z80_FLAG_YM_TIMEOUT = 0x20
Z80_COMMAND_RING_ENTRIES = 16
AUDIO_READY_MAX_FRAMES = 120
BREAKPOINT_SYMBOL = "VBlankInterrupt"

REPORT_FILENAMES = {
    "controller": "m2-controller.json",
    "video_dma": "m2-video-dma.json",
    "z80_audio_runtime": "m2-z80-audio-runtime.json",
}

KEYBOARD_BINDINGS = {
    "up": ("gamepads.1.up", 0x26, 0x48, True),
    "down": ("gamepads.1.down", 0x28, 0x50, True),
    "left": ("gamepads.1.left", 0x25, 0x4B, True),
    "right": ("gamepads.1.right", 0x27, 0x4D, True),
}


class EvidenceFailure(RuntimeError):
    """A bounded evidence-collection failure."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_debug_block(raw: bytes) -> dict[str, int | str]:
    if len(raw) < DEBUG_BLOCK_BYTES:
        raise ValueError(
            f"debug block is {len(raw)} bytes; at least {DEBUG_BLOCK_BYTES} required"
        )

    def u8(offset: int) -> int:
        return raw[offset]

    def u16(offset: int) -> int:
        return int.from_bytes(raw[offset : offset + 2], "big")

    def u32(offset: int) -> int:
        return int.from_bytes(raw[offset : offset + 4], "big")

    return {
        "raw_hex": raw[:DEBUG_BLOCK_BYTES].hex().upper(),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "magic": u32(0x00),
        "abi_version": u16(0x04),
        "abi_size": u16(0x06),
        "sequence": u32(0x08),
        "heartbeat": u32(0x0C),
        "frame_counter": u32(0x10),
        "frame_token": u16(0x14),
        "fatal_code": u16(0x16),
        "boot_stage": u16(0x18),
        "hardware_flags": u16(0x1A),
        "last_vdp_status": u16(0x1C),
        "input_held": u8(0x1E),
        "input_pressed": u8(0x1F),
        "input_released": u8(0x20),
        "input_connected": u8(0x21),
        "input_start_held": u8(0x22),
        "sprite_count": u16(0x24),
        "sprite_scanline_max": u16(0x26),
        "dma_words_frame": u16(0x28),
        "dma_words_max": u16(0x2A),
        "dma_queue_depth": u16(0x2C),
        "dma_queue_high_water": u16(0x2E),
        "dma_overflow_count": u16(0x30),
        "vblank_overrun_count": u16(0x32),
        "audio_state": u16(0x34),
        "z80_heartbeat": u16(0x36),
        "audio_command_count": u16(0x38),
        "m2_flags": u16(0x3A),
        "controller_samples": u32(0x3C),
        "z80_timeout_count": u16(0x40),
        "dma_deferred_count": u16(0x42),
        "dma_required_drop_count": u16(0x44),
        "dma_invalid_count": u16(0x46),
    }


def parse_sat_entry(raw: bytes) -> dict[str, int | str]:
    if len(raw) != SAT_ENTRY_BYTES:
        raise ValueError(f"SAT entry must be {SAT_ENTRY_BYTES} bytes, got {len(raw)}")
    y = int.from_bytes(raw[0:2], "big")
    size_link = int.from_bytes(raw[2:4], "big")
    tile = int.from_bytes(raw[4:6], "big")
    x = int.from_bytes(raw[6:8], "big")
    return {
        "raw_hex": raw.hex().upper(),
        "y": y,
        "size_code": (size_link >> 8) & 0x0F,
        "link": size_link & 0x7F,
        "tile": tile,
        "x": x,
    }


def parse_z80_status(raw: bytes) -> dict[str, int | str]:
    if len(raw) != Z80_STATUS_BYTES:
        raise ValueError(
            f"Z80 status must be {Z80_STATUS_BYTES} bytes, got {len(raw)}"
        )
    return {
        "raw_hex": raw.hex().upper(),
        "signature": raw[0x00],
        "abi_version": raw[0x01],
        "state": raw[0x02],
        "flags": raw[0x03],
        "heartbeat": int.from_bytes(raw[0x04:0x06], "little"),
        "read_sequence": raw[0x06],
        "queue_high_water": raw[0x07],
        "processed_commands": int.from_bytes(raw[0x08:0x0A], "little"),
        "late_tick_count": raw[0x0A],
        "ym_write_count": raw[0x0B],
        "psg_write_count": raw[0x0C],
        "motif_step": raw[0x0D],
        "active_layer_mask": raw[0x0E],
        "last_error_or_command": raw[0x0F],
    }


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def strictly_increasing(values: list[int]) -> bool:
    return bool(values) and all(
        later > earlier for earlier, later in zip(values, values[1:])
    )


def increments_once(values: list[int], mask: int = 0xFFFFFFFF) -> bool:
    return bool(values) and all(
        later == ((earlier + 1) & mask)
        for earlier, later in zip(values, values[1:])
    )


def vblank_entry_accounting(boot_stages: list[int]) -> dict[str, Any]:
    """Classify breakpoint stops without promoting startup priming to a frame."""
    stages = [int(value) for value in boot_stages]
    startup_stops = sum(
        value == BOOT_STAGE_VIDEO_READY for value in stages
    )
    running_stops = sum(value == BOOT_STAGE_RUNNING for value in stages)
    valid_sequence = (
        len(stages) >= 2
        and stages[0] == BOOT_STAGE_VIDEO_READY
        and all(value == BOOT_STAGE_RUNNING for value in stages[1:])
    )
    return {
        "status": "pass" if valid_sequence else "fail",
        "observed_vblank_entry_stops": len(stages),
        "startup_ack_only_entry_stops": startup_stops,
        "running_full_entry_stops": running_stops,
        "entry_boot_stages": stages,
        "initial_entry_role": STARTUP_PRIMING_ROLE,
        "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
        "counters_and_controller_poll_begin": FIRST_RUNNING_VBLANK_ROLE,
        "detail": (
            "the stage-3 entry acknowledges VDP status only; controller "
            "sampling, frame counters, and frame-token publication begin on "
            "the following stage-4 RUNNING full VBlank"
        ),
    }


def startup_priming_check(session: dict[str, Any]) -> dict[str, Any]:
    accounting = session.get("vblank_entry_accounting", {})
    running_stops = accounting.get("running_full_entry_stops")
    passed = (
        session.get("initial_breakpoint_role") == STARTUP_PRIMING_ROLE
        and session.get("authoritative_origin")
        == FIRST_RUNNING_VBLANK_ROLE
        and session.get("counters_and_controller_poll_begin")
        == FIRST_RUNNING_VBLANK_ROLE
        and accounting.get("status") == "pass"
        and accounting.get("startup_ack_only_entry_stops") == 1
        and isinstance(running_stops, int)
        and running_stops >= 1
        and accounting.get("initial_entry_role") == STARTUP_PRIMING_ROLE
        and accounting.get("authoritative_origin")
        == FIRST_RUNNING_VBLANK_ROLE
    )
    return check(
        "startup_priming_and_running_vblank_origin",
        passed,
        (
            f"initial role {session.get('initial_breakpoint_role')}; "
            f"authoritative origin {session.get('authoritative_origin')}; "
            f"entry accounting {accounting}"
        ),
    )


def common_runtime_checks(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    magic = [int(sample["debug"]["magic"]) for sample in samples]
    versions = [
        (
            int(sample["debug"]["abi_version"]),
            int(sample["debug"]["abi_size"]),
        )
        for sample in samples
    ]
    sequences = [int(sample["debug"]["sequence"]) for sample in samples]
    heartbeats = [int(sample["debug"]["heartbeat"]) for sample in samples]
    frames = [int(sample["debug"]["frame_counter"]) for sample in samples]
    return [
        check(
            "debug_magic",
            bool(samples) and all(value == EXPECTED_DEBUG_MAGIC for value in magic),
            f"magic values {[f'{value:08X}' for value in magic]}",
        ),
        check(
            "debug_abi",
            bool(samples)
            and all(
                version == (EXPECTED_DEBUG_ABI_VERSION, EXPECTED_DEBUG_ABI_SIZE)
                for version in versions
            ),
            f"version/size {versions}",
        ),
        check(
            "running_stage",
            bool(samples)
            and all(
                int(sample["debug"]["boot_stage"]) == BOOT_STAGE_RUNNING
                for sample in samples
            ),
            f"boot stages {[sample['debug']['boot_stage'] for sample in samples]}",
        ),
        check(
            "ordinary_boot_fatal_clear",
            bool(samples)
            and all(int(sample["debug"]["fatal_code"]) == 0 for sample in samples),
            f"fatal codes {[sample['debug']['fatal_code'] for sample in samples]}",
        ),
        check(
            "debug_seqlock_stable",
            bool(samples)
            and all(sequence % 2 == 0 for sequence in sequences)
            and strictly_increasing(sequences),
            f"sequences {sequences}",
        ),
        check(
            "authoritative_frame_progression",
            increments_once(heartbeats)
            and increments_once(frames)
            and all(
                heartbeat == frame
                for heartbeat, frame in zip(heartbeats, frames)
            ),
            f"heartbeat/frame {heartbeats}/{frames}",
        ),
    ]


def make_report(
    *,
    evidence_type: str,
    rom_sha256: str,
    checks: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    session: dict[str, Any],
    notes: list[str],
) -> dict[str, Any]:
    checks = [startup_priming_check(session), *checks]
    passed = bool(checks) and all(item["status"] == "pass" for item in checks)
    return {
        "schema": 1,
        "scope": "Milestone2",
        "evidence_type": evidence_type,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "samples": samples,
        "session": session,
        "notes": notes,
    }


def controller_report(
    *,
    samples: list[dict[str, Any]],
    disconnected_samples: list[dict[str, Any]],
    rom_sha256: str,
    session: dict[str, Any],
    injection: dict[str, Any],
    connected_config: dict[str, Any],
    disconnect_config: dict[str, Any],
) -> dict[str, Any]:
    by_label = {str(sample["label"]): sample for sample in samples}
    required = (
        "baseline",
        "right_first",
        "right_second",
        "right_release",
        "left_right_neutral",
        "horizontal_release",
        "up_down_neutral",
        "final_neutral",
    )
    have_sequence = all(label in by_label for label in required)
    ordered = [by_label[label] for label in required if label in by_label]
    controller_counts = [
        int(sample["debug"]["controller_samples"]) for sample in ordered
    ]
    checks = common_runtime_checks(ordered)
    disconnected_debug = [
        sample["debug"] for sample in disconnected_samples
    ]
    disconnected_accounting = session.get(
        "disconnected_vblank_entry_accounting", {}
    )
    disconnected_running_stops = disconnected_accounting.get(
        "running_full_entry_stops"
    )
    checks.extend(
        [
            check(
                "deterministic_host_injection",
                injection.get("status") == "pass",
                str(injection.get("detail", "injection metadata missing")),
            ),
            check(
                "standard_three_button_profile",
                connected_config.get("status") == "pass"
                and connected_config.get("port_1_assignment")
                == "gamepad3.1",
                str(
                    connected_config.get(
                        "detail", "standard-pad profile evidence missing"
                    )
                ),
            ),
            check(
                "required_observations",
                have_sequence,
                f"labels {[sample['label'] for sample in samples]}",
            ),
            check(
                "controller_connected",
                bool(ordered)
                and all(int(sample["debug"]["input_connected"]) == 1 for sample in ordered),
                f"connected {[sample['debug']['input_connected'] for sample in ordered]}",
            ),
            check(
                "sample_once_per_running_vblank",
                have_sequence and increments_once(controller_counts),
                (
                    f"RUNNING full-VBlank sample counters {controller_counts}; "
                    "the stage-3 acknowledgment-only priming entry is excluded"
                ),
            ),
            check(
                "input_flag",
                bool(ordered)
                and all(
                    int(sample["debug"]["m2_flags"]) & M2_FLAG_INPUT_ACTIVE
                    for sample in ordered
                ),
                f"M2 flags {[sample['debug']['m2_flags'] for sample in ordered]}",
            ),
            check(
                "disconnected_profile",
                disconnect_config.get("status") == "pass",
                str(disconnect_config.get("detail", "profile evidence missing")),
            ),
            check(
                "disconnected_startup_priming_accounted",
                disconnected_accounting.get("status") == "pass"
                and disconnected_accounting.get(
                    "startup_ack_only_entry_stops"
                )
                == 1
                and isinstance(disconnected_running_stops, int)
                and disconnected_running_stops >= 1,
                f"entry accounting {disconnected_accounting}",
            ),
            *common_runtime_checks(disconnected_samples),
            check(
                "disconnected_port_detected",
                bool(disconnected_debug)
                and all(
                    int(item["input_connected"]) == 0
                    and int(item["input_held"]) == 0
                    and int(item["input_pressed"]) == 0
                    and int(item["input_released"]) == 0
                    and int(item["input_start_held"]) == 0
                    for item in disconnected_debug
                ),
                "connected/held/pressed/released/start "
                f"{[(item['input_connected'], item['input_held'], item['input_pressed'], item['input_released'], item['input_start_held']) for item in disconnected_debug]}",
            ),
            check(
                "disconnected_sample_progression",
                increments_once(
                    [
                        int(item["controller_samples"])
                        for item in disconnected_debug
                    ]
                ),
                "sample counters "
                f"{[item['controller_samples'] for item in disconnected_debug]}",
            ),
        ]
    )
    if have_sequence:
        baseline = by_label["baseline"]["debug"]
        first = by_label["right_first"]["debug"]
        second = by_label["right_second"]["debug"]
        released = by_label["right_release"]["debug"]
        left_right = by_label["left_right_neutral"]["debug"]
        up_down = by_label["up_down_neutral"]["debug"]
        final = by_label["final_neutral"]["debug"]
        checks.extend(
            [
                check(
                    "baseline_neutral",
                    int(baseline["input_held"]) == 0
                    and int(baseline["input_pressed"]) == 0
                    and int(baseline["input_released"]) == 0,
                    "baseline held/pressed/released "
                    f"{baseline['input_held']:02X}/{baseline['input_pressed']:02X}/"
                    f"{baseline['input_released']:02X}",
                ),
                check(
                    "right_first_edge",
                    int(first["input_held"]) == INPUT_RIGHT
                    and int(first["input_pressed"]) == INPUT_RIGHT
                    and not (int(first["input_released"]) & INPUT_RIGHT),
                    "first held/pressed/released "
                    f"{first['input_held']:02X}/{first['input_pressed']:02X}/"
                    f"{first['input_released']:02X}",
                ),
                check(
                    "right_held_no_repeat_edge",
                    int(second["input_held"]) == INPUT_RIGHT
                    and not (int(second["input_pressed"]) & INPUT_RIGHT),
                    "second held/pressed "
                    f"{second['input_held']:02X}/{second['input_pressed']:02X}",
                ),
                check(
                    "right_release_edge",
                    not (int(released["input_held"]) & INPUT_RIGHT)
                    and bool(int(released["input_released"]) & INPUT_RIGHT),
                    "release held/released "
                    f"{released['input_held']:02X}/{released['input_released']:02X}",
                ),
                check(
                    "opposite_horizontal_neutral",
                    not (
                        (
                            int(left_right["input_held"])
                            | int(left_right["input_pressed"])
                        )
                        & (INPUT_LEFT | INPUT_RIGHT)
                    ),
                    "Left+Right held/pressed "
                    f"{left_right['input_held']:02X}/"
                    f"{left_right['input_pressed']:02X}",
                ),
                check(
                    "opposite_vertical_neutral",
                    not (
                        (
                            int(up_down["input_held"])
                            | int(up_down["input_pressed"])
                        )
                        & (INPUT_UP | INPUT_DOWN)
                    ),
                    "Up+Down held/pressed "
                    f"{up_down['input_held']:02X}/"
                    f"{up_down['input_pressed']:02X}",
                ),
                check(
                    "bit7_and_start_clear",
                    all(
                        not (int(sample["debug"]["input_held"]) & 0x80)
                        and int(sample["debug"]["input_start_held"]) == 0
                        for sample in ordered
                    ),
                    "normalized held bit 7 and Start remained clear",
                ),
                check(
                    "final_neutral",
                    int(final["input_held"]) == 0,
                    f"final held {final['input_held']:02X}",
                ),
            ]
        )
    report = make_report(
        evidence_type="blastem_gdb_controller_ram",
        rom_sha256=rom_sha256,
        checks=checks,
        samples=ordered,
        session={
            **session,
            "injection": injection,
            "connected_config": connected_config,
            "disconnect_config": disconnect_config,
        },
        notes=[
            "Targeted WM_KEYDOWN/WM_KEYUP messages use a hash-recorded default.cfg.",
            "This RAM report does not constitute screenshot evidence.",
        ],
    )
    report["disconnected_samples"] = disconnected_samples
    return report


def video_dma_report(
    *,
    samples: list[dict[str, Any]],
    rom_sha256: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    stable = [sample for sample in samples if sample["label"] != "baseline"]
    debug = [sample["debug"] for sample in stable]
    sat = [sample["sat"] for sample in stable]
    flags = M2_FLAG_SPRITE_ACTIVE | M2_FLAG_DMA_ACTIVE
    deferred_counts = [int(item["dma_deferred_count"]) for item in debug]
    x_by_label = {
        str(sample["label"]): int(sample["sat"]["x"]) for sample in samples
    }
    movement_labels = {"baseline", "right_first", "right_second", "right_release"}
    movement_available = movement_labels.issubset(x_by_label)
    checks = common_runtime_checks(stable)
    checks.extend(
        [
            check(
                "sprite_dma_flags",
                bool(debug)
                and all((int(item["m2_flags"]) & flags) == flags for item in debug),
                f"M2 flags {[item['m2_flags'] for item in debug]}",
            ),
            check(
                "required_sprite_set_present",
                bool(debug)
                and all(
                    0 < int(item["sprite_count"]) <= SPRITE_HARDWARE_LIMIT
                    for item in debug
                ),
                f"sprite counts {[item['sprite_count'] for item in debug]}",
            ),
            check(
                "sprite_hardware_limits",
                bool(debug)
                and all(
                    0 < int(item["sprite_count"]) <= SPRITE_HARDWARE_LIMIT
                    and 0 < int(item["sprite_scanline_max"]) <= SPRITE_SCANLINE_LIMIT
                    for item in debug
                ),
                "frame/scanline "
                f"{[(item['sprite_count'], item['sprite_scanline_max']) for item in debug]}",
            ),
            check(
                "sprite_scanline_budget",
                bool(debug)
                and all(
                    0 < int(item["sprite_scanline_max"]) <= SPRITE_SCANLINE_LIMIT
                    for item in debug
                ),
                f"scanline maxima {[item['sprite_scanline_max'] for item in debug]}",
            ),
            check(
                "sat_first_entry_chain_and_size",
                bool(sat)
                and all(
                    int(sat_item["size_code"])
                    == (
                        0
                        if int(debug_item["sprite_count"]) == 1
                        else PRIMARY_GAMEPLAY_SPRITE_SIZE_CODE
                    )
                    and int(sat_item["link"])
                    == (0 if int(debug_item["sprite_count"]) == 1 else 1)
                    for sat_item, debug_item in zip(sat, debug)
                ),
                "first size/link/count "
                f"{[(s['size_code'], s['link'], d['sprite_count']) for s, d in zip(sat, debug)]}",
            ),
            check(
                "sat_safe_rectangle",
                bool(sat)
                and all(
                    128 <= int(item["x"]) <= 447
                    and 128 <= int(item["y"]) <= 351
                    for item in sat
                ),
                f"SAT x/y {[(item['x'], item['y']) for item in sat]}",
            ),
            check(
                "right_moves_sprite",
                movement_available
                and x_by_label["right_first"] > x_by_label["baseline"]
                and x_by_label["right_second"] > x_by_label["right_first"]
                and x_by_label["right_release"] == x_by_label["right_second"],
                f"x by phase {x_by_label}",
            ),
            check(
                "required_scene_dma_words",
                bool(debug)
                and all(
                    DMA_NORMAL_SAT_WORDS
                    <= int(item["dma_words_frame"])
                    <= DMA_ORDINARY_WORD_LIMIT
                    for item in debug
                ),
                f"DMA words/frame {[item['dma_words_frame'] for item in debug]}",
            ),
            check(
                "dma_ordinary_budget",
                bool(debug)
                and all(
                    DMA_NORMAL_SAT_WORDS
                    <= int(item["dma_words_max"])
                    <= DMA_ORDINARY_WORD_LIMIT
                    for item in debug
                ),
                f"DMA maxima {[item['dma_words_max'] for item in debug]}",
            ),
            check(
                "dma_queue_bounded",
                bool(debug)
                and all(
                    0 <= int(item["dma_queue_depth"]) <= DMA_QUEUE_CAPACITY
                    and 1 <= int(item["dma_queue_high_water"]) <= DMA_QUEUE_CAPACITY
                    for item in debug
                ),
                "depth/high-water "
                f"{[(item['dma_queue_depth'], item['dma_queue_high_water']) for item in debug]}",
            ),
            check(
                "dma_no_overflow_or_late_vblank",
                bool(debug)
                and all(
                    int(item["dma_overflow_count"]) == 0
                    and int(item["vblank_overrun_count"]) == 0
                    for item in debug
                ),
                "overflow/late "
                f"{[(item['dma_overflow_count'], item['vblank_overrun_count']) for item in debug]}",
            ),
            check(
                "dma_deferrals_tracked",
                bool(deferred_counts)
                and all(
                    later >= earlier
                    for earlier, later in zip(
                        deferred_counts, deferred_counts[1:]
                    )
                ),
                f"deferred counts {deferred_counts}",
            ),
            check(
                "dma_required_entries_not_dropped",
                bool(debug)
                and all(
                    int(item["dma_required_drop_count"]) == 0
                    for item in debug
                ),
                "required-drop counts "
                f"{[item['dma_required_drop_count'] for item in debug]}",
            ),
            check(
                "dma_invalid_entries_clear",
                bool(debug)
                and all(int(item["dma_invalid_count"]) == 0 for item in debug),
                f"invalid-entry counts {[item['dma_invalid_count'] for item in debug]}",
            ),
        ]
    )
    return make_report(
        evidence_type="blastem_gdb_sprite_sat_dma_ram",
        rom_sha256=rom_sha256,
        checks=checks,
        samples=samples,
        session=session,
        notes=[
            "The fixed SAT shadow entry and published runtime counters are checked.",
            "A separate hash-bound screenshot is still required to prove visible output.",
        ],
    )


def z80_audio_runtime_report(
    *,
    samples: list[dict[str, Any]],
    rom_sha256: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    debug = [sample["debug"] for sample in samples]
    status_shadow = [sample["audio_shadow"] for sample in samples]
    flags = M2_FLAG_Z80_UPLOADED | M2_FLAG_YM2612_ACTIVE | M2_FLAG_PSG_ACTIVE
    heartbeats = [int(item["z80_heartbeat"]) for item in debug]
    shadow_heartbeats = [int(item["heartbeat"]) for item in status_shadow]
    commands = [int(item["audio_command_count"]) for item in debug]
    shadow_commands = [int(item["processed_commands"]) for item in status_shadow]
    mirror_values = [
        (
            debug_item["audio_state"],
            debug_item["z80_heartbeat"],
            status_item["state"],
            status_item["heartbeat"],
        )
        for debug_item, status_item in zip(debug, status_shadow)
    ]
    required_status_flags = Z80_FLAG_READY | Z80_FLAG_YM_ACTIVE | Z80_FLAG_PSG_ACTIVE
    checks = common_runtime_checks(samples)
    checks.extend(
        [
            check(
                "z80_ym2612_psg_flags",
                bool(debug)
                and all((int(item["m2_flags"]) & flags) == flags for item in debug),
                f"M2 flags {[item['m2_flags'] for item in debug]}",
            ),
            check(
                "audio_shadow_identity",
                bool(status_shadow)
                and all(
                    int(item["signature"]) == Z80_STATUS_SIGNATURE
                    and int(item["abi_version"]) == Z80_STATUS_ABI_VERSION
                    for item in status_shadow
                ),
                "signature/ABI "
                f"{[(item['signature'], item['abi_version']) for item in status_shadow]}",
            ),
            check(
                "audio_shadow_playing_and_active",
                bool(status_shadow)
                and all(
                    int(item["state"]) == Z80_STATE_PLAYING
                    and (int(item["flags"]) & required_status_flags)
                    == required_status_flags
                    for item in status_shadow
                ),
                "state/flags "
                f"{[(item['state'], item['flags']) for item in status_shadow]}",
            ),
            check(
                "audio_shadow_faults_clear",
                bool(status_shadow)
                and all(
                    not (
                        int(item["flags"])
                        & (Z80_FLAG_QUEUE_FAULT | Z80_FLAG_YM_TIMEOUT)
                    )
                    and int(item["late_tick_count"]) == 0
                    for item in status_shadow
                ),
                "flags/late ticks "
                f"{[(item['flags'], item['late_tick_count']) for item in status_shadow]}",
            ),
            check(
                "debug_status_mirror",
                bool(debug)
                and all(
                    int(debug_item["audio_state"]) == int(status_item["state"])
                    and int(debug_item["z80_heartbeat"])
                    == int(status_item["heartbeat"])
                    and int(debug_item["audio_command_count"])
                    == int(status_item["processed_commands"])
                    for debug_item, status_item in zip(debug, status_shadow)
                ),
                f"debug/shadow state+heartbeat {mirror_values}",
            ),
            check(
                "z80_heartbeat_advances",
                strictly_increasing(heartbeats)
                and strictly_increasing(shadow_heartbeats),
                f"debug/shadow heartbeats {heartbeats}/{shadow_heartbeats}",
            ),
            check(
                "audio_commands_observed",
                bool(commands)
                and commands[-1] > 0
                and shadow_commands[-1] > 0
                and all(later >= earlier for earlier, later in zip(commands, commands[1:]))
                and all(
                    later >= earlier
                    for earlier, later in zip(shadow_commands, shadow_commands[1:])
                ),
                f"debug/shadow command counts {commands}/{shadow_commands}",
            ),
            check(
                "command_ring_high_water_bounded",
                bool(status_shadow)
                and all(
                    1
                    <= int(item["queue_high_water"])
                    <= Z80_COMMAND_RING_ENTRIES
                    for item in status_shadow
                ),
                "queue high-water "
                f"{[item['queue_high_water'] for item in status_shadow]}",
            ),
            check(
                "ym2612_and_psg_writes_observed",
                bool(status_shadow)
                and all(
                    int(item["ym_write_count"]) > 0
                    and int(item["psg_write_count"]) > 0
                    for item in status_shadow
                ),
                "YM/PSG writes "
                f"{[(item['ym_write_count'], item['psg_write_count']) for item in status_shadow]}",
            ),
            check(
                "z80_bus_timeouts_clear",
                bool(debug)
                and all(
                    not (int(item["hardware_flags"]) & HARDWARE_FLAG_Z80_TIMEOUT)
                    and int(item["z80_timeout_count"]) == 0
                    for item in debug
                ),
                "hardware flags/timeouts "
                f"{[(item['hardware_flags'], item['z80_timeout_count']) for item in debug]}",
            ),
        ]
    )
    return make_report(
        evidence_type="blastem_gdb_z80_audio_status_ram",
        rom_sha256=rom_sha256,
        checks=checks,
        samples=samples,
        session=session,
        notes=[
            "The 68000 copies the complete status block to $FF9900 while BUSREQ is held.",
            "No unowned direct read of live Z80 RAM is used as gate evidence.",
            "Audible YM2612/PSG output requires a distinct capture or event-trace report.",
        ],
    )


def parse_vasm_symbols(path: Path) -> dict[str, int]:
    symbols: dict[str, int] = {}
    pattern = re.compile(r"^\s*([0-9A-Fa-f]{8,16})\s+(\S+)\s*$")
    for line in path.read_text("utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            symbols[match.group(2)] = int(match.group(1), 16)
    return symbols


def verify_default_bindings(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    text = payload.decode("utf-8", errors="replace")
    checks: dict[str, bool] = {}
    for key, (action, _virtual_key, _scan, _extended) in KEYBOARD_BINDINGS.items():
        checks[key] = bool(
            re.search(
                rf"(?m)^\s*{re.escape(key)}\s+{re.escape(action)}\s*$",
                text,
            )
        )
    passed = all(checks.values())
    return {
        "status": "pass" if passed else "fail",
        "detail": (
            "isolated BlastEm default.cfg maps arrow keys to port-1 D-pad"
            if passed
            else f"default.cfg binding checks failed: {checks}"
        ),
        "path": path.as_posix(),
        "sha256": sha256_bytes(payload),
        "bindings": checks,
    }


def write_disconnected_profile(
    default_config: Path, profile_root: Path
) -> dict[str, Any]:
    """Create an isolated BlastEm config with Genesis port 1 explicitly empty."""
    source = default_config.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    attached = (
        "io {\n"
        "\tdevices {\n"
        "\t\t1 gamepad6.1\n"
        "\t\t2 gamepad6.2\n"
        "\t}\n"
    )
    detached = (
        "io {\n"
        "\tdevices {\n"
        "\t\t1 none\n"
        "\t\t2 gamepad6.2\n"
        "\t}\n"
    )
    if source.count(attached) != 1:
        raise EvidenceFailure(
            "pinned default.cfg Genesis IO assignment is not the expected block"
        )
    modified = source.replace(attached, detached, 1)
    destination = profile_root / "blastem" / "blastem.cfg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(modified, encoding="utf-8", newline="\n")
    return {
        "status": "pass",
        "detail": (
            "isolated config explicitly assigns port 1 to unknown type "
            "'none'; BlastEm leaves its zero-initialized IO_NONE device "
            "unchanged instead of applying the omitted-key gamepad6.1 default"
        ),
        "path": destination.as_posix(),
        "sha256": sha256_file(destination),
        "source_sha256": sha256_file(default_config),
        "port_1_assignment": "none",
        "port_2_assignment": "gamepad6.2",
    }


def write_standard_pad_profile(
    default_config: Path, profile_root: Path
) -> dict[str, Any]:
    """Create an isolated config with a standard three-button pad on port 1."""
    source = default_config.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    original = (
        "io {\n"
        "\tdevices {\n"
        "\t\t1 gamepad6.1\n"
        "\t\t2 gamepad6.2\n"
        "\t}\n"
    )
    standard = (
        "io {\n"
        "\tdevices {\n"
        "\t\t1 gamepad3.1\n"
        "\t\t2 gamepad6.2\n"
        "\t}\n"
    )
    if source.count(original) != 1:
        raise EvidenceFailure(
            "pinned default.cfg Genesis IO assignment is not the expected block"
        )
    modified = source.replace(original, standard, 1)
    destination = profile_root / "blastem" / "blastem.cfg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(modified, encoding="utf-8", newline="\n")
    return {
        "status": "pass",
        "detail": "isolated Genesis port 1 is explicitly gamepad3.1",
        "path": destination.as_posix(),
        "sha256": sha256_file(destination),
        "source_sha256": sha256_file(default_config),
        "port_1_assignment": "gamepad3.1",
    }


def windows_local_appdata() -> Path:
    """Resolve FOLDERID_LocalAppData without trusting environment overrides."""
    if os.name != "nt":
        raise EvidenceFailure(
            "Windows Known Folder lookup requires a native Windows process"
        )

    class Guid(ctypes.Structure):
        _fields_ = (
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        )

    folder_id = Guid.from_buffer_copy(
        uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    )
    resolved = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    shell32.SHGetKnownFolderPath.argtypes = (
        ctypes.POINTER(Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    result = shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id), 0, None, ctypes.byref(resolved)
    )
    if result != 0 or not resolved.value:
        raise EvidenceFailure(
            f"SHGetKnownFolderPath(FOLDERID_LocalAppData) failed: ${result & 0xFFFFFFFF:08X}"
        )
    try:
        return Path(resolved.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(resolved)


def verify_no_user_blastem_override(
    local_appdata: Path | None = None,
) -> dict[str, Any]:
    """Fail before launch rather than reading or replacing a user config."""
    base = (
        local_appdata.resolve()
        if local_appdata is not None
        else windows_local_appdata().resolve()
    )
    override = base / "blastem" / "blastem.cfg"
    if override.exists():
        raise EvidenceFailure(
            "real user BlastEm override exists and will not be read or "
            f"overwritten: {override}"
        )
    return {
        "status": "pass",
        "known_folder": base.as_posix(),
        "override_path": override.as_posix(),
        "override_absent": True,
        "lookup": "SHGetKnownFolderPath(FOLDERID_LocalAppData)",
    }


def clone_blastem_runtime(
    pinned_directory: Path, sandbox_root: Path
) -> tuple[Path, dict[str, Any]]:
    """Copy the pinned runtime so a bundled blastem.cfg owns the session."""
    pinned = pinned_directory.resolve()
    destination = sandbox_root.resolve() / "blastem"
    if destination.exists():
        raise EvidenceFailure(
            f"isolated BlastEm destination already exists: {destination}"
        )
    executable = pinned / "blastem.exe"
    default_config = pinned / "default.cfg"
    if not executable.is_file() or not default_config.is_file():
        raise EvidenceFailure(
            f"pinned BlastEm runtime is incomplete: {pinned}"
        )
    shutil.copytree(pinned, destination)
    copied_executable = destination / "blastem.exe"
    copied_default = destination / "default.cfg"
    source_executable_sha = sha256_file(executable)
    copied_executable_sha = sha256_file(copied_executable)
    source_default_sha = sha256_file(default_config)
    copied_default_sha = sha256_file(copied_default)
    if (
        source_executable_sha != copied_executable_sha
        or source_default_sha != copied_default_sha
    ):
        raise EvidenceFailure("isolated BlastEm runtime copy hash mismatch")
    return destination, {
        "status": "pass",
        "source_directory": pinned.as_posix(),
        "temporary_directory": destination.as_posix(),
        "executable_sha256": copied_executable_sha,
        "default_config_sha256": copied_default_sha,
        "bundled_override": (destination / "blastem.cfg").as_posix(),
        "temporary_copy_removed_after_session": True,
    }


def apply_blastem_session_isolation(
    config_path: Path,
    owned_data_root: Path,
    *,
    vgm_path: Path | None = None,
) -> dict[str, Any]:
    """Redirect every known writable BlastEm path into an owned location."""
    data_root = owned_data_root.resolve()
    save_root = data_root / "saves"
    screenshot_root = data_root / "screenshots"
    vgm_root = (vgm_path or (data_root / "vgm")).resolve()
    for directory in (save_root, screenshot_root, vgm_root):
        directory.mkdir(parents=True, exist_ok=True)

    text = config_path.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    if re.search(r"(?m)^\s*config_in_exe_dir\s+", text):
        text, count = re.subn(
            r"(?m)^\s*config_in_exe_dir\s+.*$",
            "\tconfig_in_exe_dir on",
            text,
            count=1,
        )
    else:
        text, count = re.subn(
            r"(?m)^ui\s*\{\s*$",
            "ui {\n\tconfig_in_exe_dir on",
            text,
            count=1,
        )
    if count != 1:
        raise EvidenceFailure(
            "isolated BlastEm config has no unique top-level ui block"
        )

    replacements = {
        "screenshot_path": screenshot_root.as_posix(),
        "vgm_path": vgm_root.as_posix(),
        "save_path": f"{save_root.as_posix()}/$ROMNAME",
    }
    for key, value in replacements.items():
        text, count = re.subn(
            rf"(?m)^\s*{key}\s+.*$",
            f"\t{key} {value}",
            text,
            count=1,
        )
        if count != 1:
            raise EvidenceFailure(
                f"isolated BlastEm config has no unique ui.{key}"
            )
    config_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "status": "pass",
        "config_path": config_path.as_posix(),
        "config_sha256": sha256_file(config_path),
        "config_in_exe_dir": True,
        "save_path": replacements["save_path"],
        "screenshot_path": replacements["screenshot_path"],
        "vgm_path": replacements["vgm_path"],
    }


def rsp_packet(payload: str) -> bytes:
    raw = payload.encode("ascii")
    return b"$" + raw + f"#{sum(raw) & 0xFF:02x}".encode("ascii")


class GdbRemote:
    def __init__(
        self, host: str, port: int, timeout: float, transcript: list[str]
    ) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                self.socket = socket.create_connection((host, port), timeout=1.0)
                self.socket.settimeout(timeout)
                break
            except OSError as error:
                last_error = error
                time.sleep(0.1)
        else:
            raise TimeoutError(f"BlastEm GDB socket did not open: {last_error}")
        self.buffer = bytearray()
        self.transcript = transcript

    def _byte(self) -> int:
        if not self.buffer:
            chunk = self.socket.recv(4096)
            if not chunk:
                raise EOFError("BlastEm closed the GDB connection")
            self.buffer.extend(chunk)
        return self.buffer.pop(0)

    def response(self) -> str:
        while self._byte() != ord("$"):
            pass
        payload = bytearray()
        while True:
            byte = self._byte()
            if byte == ord("#"):
                break
            payload.append(byte)
        claimed = bytes((self._byte(), self._byte()))
        actual = f"{sum(payload) & 0xFF:02x}".encode("ascii")
        if claimed.lower() != actual:
            self.socket.sendall(b"-")
            raise EvidenceFailure(
                f"GDB response checksum {claimed!r} does not match {actual!r}"
            )
        self.socket.sendall(b"+")
        return payload.decode("ascii")

    def request(self, payload: str) -> str:
        self.transcript.append(f"> {payload}")
        self.socket.sendall(rsp_packet(payload))
        response = self.response()
        self.transcript.append(f"< {response}")
        if response.startswith("E"):
            raise EvidenceFailure(f"GDB command {payload!r} returned {response}")
        return response

    def read_memory(self, address: int, size: int) -> bytes:
        response = self.request(f"m{address:x},{size:x}")
        try:
            payload = bytes.fromhex(response)
        except ValueError as error:
            raise EvidenceFailure(
                f"GDB returned non-hex memory for ${address:08X}: {response!r}"
            ) from error
        if len(payload) != size:
            raise EvidenceFailure(
                f"GDB returned {len(payload)} bytes for ${address:08X}; "
                f"{size} required"
            )
        return payload

    def continue_then_interrupt(self, run_seconds: float) -> str:
        self.transcript.append("> c")
        self.socket.sendall(rsp_packet("c"))
        time.sleep(run_seconds)
        self.transcript.append("> <interrupt>")
        self.socket.sendall(b"\x03")
        response = self.response()
        self.transcript.append(f"< {response}")
        return response

    def begin_continue(self) -> None:
        """Resume without waiting so independent host evidence can be captured."""
        self.transcript.append("> c")
        self.socket.sendall(rsp_packet("c"))

    def observe_stop_response(
        self, observation_seconds: float
    ) -> dict[str, Any]:
        """Report whether a breakpoint stop arrives after begin_continue."""
        previous_timeout = self.socket.gettimeout()
        started = time.monotonic()
        self.socket.settimeout(observation_seconds)
        try:
            response = self.response()
        except TimeoutError:
            elapsed = time.monotonic() - started
            self.transcript.append(
                f"< no stop response within {elapsed:.3f} seconds"
            )
            return {
                "timed_out": True,
                "response": None,
                "observation_seconds": elapsed,
            }
        finally:
            self.socket.settimeout(previous_timeout)
        elapsed = time.monotonic() - started
        self.transcript.append(f"< {response}")
        return {
            "timed_out": False,
            "response": response,
            "observation_seconds": elapsed,
        }

    def continue_observe_stop(self, observation_seconds: float) -> dict[str, Any]:
        self.begin_continue()
        return self.observe_stop_response(observation_seconds)

    def kill(self) -> None:
        self.transcript.append("> k")
        self.socket.sendall(rsp_packet("k"))

    def close(self) -> None:
        self.socket.close()


@contextlib.contextmanager
def exclusive_file_lock(path: Path, timeout: float) -> Iterator[dict[str, Any]]:
    """Hold a one-byte advisory lock across a complete BlastEm GDB session."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while time.monotonic() < deadline:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                acquired = True
                break
            except (OSError, BlockingIOError):
                time.sleep(0.1)
        if not acquired:
            raise TimeoutError(
                f"timed out waiting for serialized BlastEm lock {path}"
            )
        yield {
            "path": path.as_posix(),
            "acquired_utc": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def find_process_window(process_id: int, timeout: float) -> int:
    if os.name != "nt":
        raise EvidenceFailure("window discovery is supported only on Windows")
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        @callback_type
        def callback(window: int, _parameter: int) -> bool:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value == process_id and user32.IsWindowVisible(window):
                matches.append(int(window))
            return True

        user32.EnumWindows(callback, 0)
        if matches:
            return matches[0]
        time.sleep(0.1)
    raise TimeoutError(f"BlastEm process {process_id} did not create a window")


def post_virtual_key(
    window: int,
    *,
    virtual_key: int,
    scan_code: int,
    pressed: bool,
    extended: bool = False,
) -> None:
    if os.name != "nt":
        raise EvidenceFailure("key injection is supported only on Windows")
    message = 0x0100 if pressed else 0x0101
    parameter = 1 | (scan_code << 16)
    if extended:
        parameter |= 1 << 24
    if not pressed:
        parameter |= (1 << 30) | (1 << 31)
    post_message = ctypes.windll.user32.PostMessageW
    post_message.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    post_message.restype = wintypes.BOOL
    if not post_message(window, message, virtual_key, parameter):
        raise EvidenceFailure(
            "PostMessageW failed for virtual key "
            f"${virtual_key:02X} {'down' if pressed else 'up'}"
        )


def post_window_key(window: int, key: str, pressed: bool) -> None:
    try:
        _action, virtual_key, scan_code, extended = KEYBOARD_BINDINGS[key]
    except KeyError as error:
        raise ValueError(f"unsupported injected key: {key}") from error
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=pressed,
        extended=extended,
    )


def tap_virtual_key(
    window: int, *, virtual_key: int, scan_code: int, extended: bool = False
) -> None:
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=True,
        extended=extended,
    )
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=False,
        extended=extended,
    )


def require_stop(response: str) -> None:
    if response != "S05" and not response.startswith("T05"):
        raise EvidenceFailure(f"unexpected GDB stop response: {response!r}")


def require_gdb_port_available(host: str, port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.2)
    try:
        if probe.connect_ex((host, port)) == 0:
            raise EvidenceFailure(
                f"GDB endpoint {host}:{port} is already occupied; "
                "refusing to attach to an unowned process"
            )
    finally:
        probe.close()


def shutdown_emulator(
    remote: GdbRemote | None,
    process: subprocess.Popen[bytes] | None,
    transcript: list[str],
) -> None:
    if remote is not None:
        try:
            remote.kill()
        except Exception as error:
            transcript.append(
                f"! GDB kill failed: {type(error).__name__}: {error}"
            )
        try:
            remote.close()
        except Exception as error:
            transcript.append(
                f"! GDB close failed: {type(error).__name__}: {error}"
            )
    if process is None:
        return
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        except Exception as error:
            transcript.append(
                f"! BlastEm cleanup failed: {type(error).__name__}: {error}"
            )


def read_sample(remote: GdbRemote, label: str) -> dict[str, Any]:
    debug = parse_debug_block(
        remote.read_memory(DEBUG_BASE, DEBUG_BLOCK_BYTES)
    )
    if int(debug["sequence"]) & 1:
        raise EvidenceFailure(
            f"debug seqlock is odd at {label}: {debug['sequence']}"
        )
    sat = parse_sat_entry(remote.read_memory(SAT_SHADOW, SAT_ENTRY_BYTES))
    audio_shadow = parse_z80_status(
        remote.read_memory(Z80_STATUS_SHADOW, Z80_STATUS_BYTES)
    )
    return {
        "label": label,
        "debug": debug,
        "sat": sat,
        "audio_shadow": audio_shadow,
    }


def continue_to_sample(
    remote: GdbRemote,
    label: str,
    *,
    entry_boot_stages: list[int] | None = None,
) -> dict[str, Any]:
    require_stop(remote.request("c"))
    sample = read_sample(remote, label)
    if entry_boot_stages is not None:
        entry_boot_stages.append(int(sample["debug"]["boot_stage"]))
    return sample


def audio_readiness_facts(sample: dict[str, Any]) -> dict[str, Any]:
    debug = sample["debug"]
    status = sample["audio_shadow"]
    required_debug_flags = (
        M2_FLAG_Z80_UPLOADED | M2_FLAG_YM2612_ACTIVE | M2_FLAG_PSG_ACTIVE
    )
    required_status_flags = (
        Z80_FLAG_READY | Z80_FLAG_YM_ACTIVE | Z80_FLAG_PSG_ACTIVE
    )
    facts = {
        "debug_state": int(debug["audio_state"]),
        "status_state": int(status["state"]),
        "debug_flags": int(debug["m2_flags"]),
        "status_flags": int(status["flags"]),
        "debug_command_count": int(debug["audio_command_count"]),
        "status_command_count": int(status["processed_commands"]),
        "queue_high_water": int(status["queue_high_water"]),
        "ym_write_count": int(status["ym_write_count"]),
        "psg_write_count": int(status["psg_write_count"]),
    }
    facts["ready"] = (
        facts["debug_state"] == Z80_STATE_PLAYING
        and facts["status_state"] == Z80_STATE_PLAYING
        and (facts["debug_flags"] & required_debug_flags)
        == required_debug_flags
        and (facts["status_flags"] & required_status_flags)
        == required_status_flags
        and facts["debug_command_count"] > 0
        and facts["status_command_count"] > 0
        and facts["queue_high_water"] > 0
        and facts["ym_write_count"] > 0
        and facts["psg_write_count"] > 0
    )
    return facts


def collect_samples(
    *,
    remote: GdbRemote,
    process_id: int,
    inject_input: bool,
    warmup_frames: int,
    audio_ready_max_frames: int = AUDIO_READY_MAX_FRAMES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not 0 < warmup_frames <= audio_ready_max_frames:
        raise ValueError(
            "warmup_frames must be positive and no greater than "
            "audio_ready_max_frames"
        )
    entry_boot_stages: list[int] = []
    baseline: dict[str, Any] | None = None
    for index in range(warmup_frames):
        baseline = continue_to_sample(
            remote,
            f"warmup_entry_{index + 1}",
            entry_boot_stages=entry_boot_stages,
        )
    assert baseline is not None
    readiness = audio_readiness_facts(baseline)
    while not readiness["ready"]:
        if len(entry_boot_stages) >= audio_ready_max_frames:
            raise EvidenceFailure(
                "audio did not become ready within "
                f"{audio_ready_max_frames} VBlank entry stops: {readiness}"
            )
        baseline = continue_to_sample(
            remote,
            "audio_readiness",
            entry_boot_stages=entry_boot_stages,
        )
        readiness = audio_readiness_facts(baseline)
    readiness_accounting = vblank_entry_accounting(entry_boot_stages)
    baseline["label"] = "baseline"
    baseline["audio_readiness"] = {
        **readiness,
        # Retained for schema compatibility. This is a count of physical
        # breakpoint entries and includes the stage-3 priming ISR.
        "observed_vblanks": len(entry_boot_stages),
        "observed_vblanks_semantics": (
            "VBlank breakpoint entry stops including startup priming"
        ),
        **readiness_accounting,
        "maximum_vblanks": audio_ready_max_frames,
        "maximum_vblanks_semantics": "maximum VBlank breakpoint entry stops",
    }
    samples = [baseline]

    if not inject_input:
        for index in range(6):
            samples.append(
                continue_to_sample(
                    remote,
                    f"uninjected_{index + 1}",
                    entry_boot_stages=entry_boot_stages,
                )
            )
        return samples, {
            "status": "fail",
            "detail": "controller injection was explicitly skipped",
            "audio_readiness": baseline["audio_readiness"],
            "vblank_entry_accounting": vblank_entry_accounting(
                entry_boot_stages
            ),
        }

    window = find_process_window(process_id, timeout=20.0)
    post_window_key(window, "right", True)
    samples.append(
        continue_to_sample(
            remote,
            "right_first",
            entry_boot_stages=entry_boot_stages,
        )
    )
    samples.append(
        continue_to_sample(
            remote,
            "right_second",
            entry_boot_stages=entry_boot_stages,
        )
    )
    post_window_key(window, "right", False)
    samples.append(
        continue_to_sample(
            remote,
            "right_release",
            entry_boot_stages=entry_boot_stages,
        )
    )

    post_window_key(window, "left", True)
    post_window_key(window, "right", True)
    samples.append(
        continue_to_sample(
            remote,
            "left_right_neutral",
            entry_boot_stages=entry_boot_stages,
        )
    )
    post_window_key(window, "left", False)
    post_window_key(window, "right", False)
    samples.append(
        continue_to_sample(
            remote,
            "horizontal_release",
            entry_boot_stages=entry_boot_stages,
        )
    )

    post_window_key(window, "up", True)
    post_window_key(window, "down", True)
    samples.append(
        continue_to_sample(
            remote,
            "up_down_neutral",
            entry_boot_stages=entry_boot_stages,
        )
    )
    post_window_key(window, "up", False)
    post_window_key(window, "down", False)
    samples.append(
        continue_to_sample(
            remote,
            "final_neutral",
            entry_boot_stages=entry_boot_stages,
        )
    )
    return samples, {
        "status": "pass",
        "detail": (
            "targeted BlastEm window messages injected Right hold/release and "
            "opposite-axis chords"
        ),
        "window_handle": window,
        "method": "PostMessageW WM_KEYDOWN/WM_KEYUP",
        "audio_readiness": baseline["audio_readiness"],
        "vblank_entry_accounting": vblank_entry_accounting(
            entry_boot_stages
        ),
    }


def failure_reports(
    *,
    rom_sha256: str,
    error: Exception,
    session: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    detail = f"{type(error).__name__}: {error}"
    reports: dict[str, dict[str, Any]] = {}
    for key in REPORT_FILENAMES:
        reports[key] = make_report(
            evidence_type=f"blastem_gdb_{key}",
            rom_sha256=rom_sha256,
            checks=[check("evidence_session", False, detail)],
            samples=[],
            session=session,
            notes=[
                "Collection failed boundedly; no missing observation was promoted.",
                "Screenshot and audible audio evidence remain separate gates.",
            ],
        )
        reports[key]["error"] = detail
    return reports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--warmup-frames", type=int, default=4)
    parser.add_argument("--skip-input-injection", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    report_dir = (
        args.report_dir.resolve()
        if args.report_dir
        else root / "artifacts" / "reports"
    )
    trace_dir = root / "artifacts" / "traces"
    transcript_path = trace_dir / "m2-gdb-transcript.log"
    lock_path = root / ".toolcache" / "locks" / "blastem-gdb.lock"
    reports: dict[str, dict[str, Any]] = {}
    transcript: list[str] = []
    remote: GdbRemote | None = None
    process: subprocess.Popen[bytes] | None = None
    runtime_stack = contextlib.ExitStack()
    rom_sha256 = ""
    heartbeat_evidence: dict[str, Any] = {}
    session: dict[str, Any] = {
        "status": "fail",
        "serialized_lock": lock_path.relative_to(root).as_posix(),
        "gdb_endpoint": "127.0.0.1:1234",
        "breakpoint_symbol": BREAKPOINT_SYMBOL,
        "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
        "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
        "counters_and_controller_poll_begin": FIRST_RUNNING_VBLANK_ROLE,
    }

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M2 capture requires Windows BlastEm and targeted window input"
            )
        if args.warmup_frames < 2 or args.warmup_frames > 120:
            raise ValueError("--warmup-frames must be within 2..120")
        rom_payload = rom.read_bytes()
        rom_sha256 = sha256_bytes(rom_payload)
        if args.expected_sha256 and (
            rom_sha256 != args.expected_sha256.strip().lower()
        ):
            raise EvidenceFailure(
                f"ROM SHA-256 {rom_sha256} does not match "
                f"{args.expected_sha256.strip().lower()}"
            )

        emulator_directory = root / ".toolcache" / "blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        if not emulator.is_file():
            raise EvidenceFailure(f"pinned BlastEm is missing: {emulator}")
        user_config_evidence = verify_no_user_blastem_override()
        binding_evidence = verify_default_bindings(default_config)
        if binding_evidence["status"] != "pass":
            raise EvidenceFailure(str(binding_evidence["detail"]))

        symbols = parse_vasm_symbols(root / "build" / "recursion94-debug.sym")
        if BREAKPOINT_SYMBOL not in symbols:
            raise EvidenceFailure(
                f"{BREAKPOINT_SYMBOL} is absent from the Debug symbol map"
            )
        breakpoint = symbols[BREAKPOINT_SYMBOL]
        launch_flags = [
            "-D",
            "-g",
            "-m",
            "gen",
            "-r",
            "U",
        ]
        session.update(
            {
                "rom_path": rom.relative_to(root).as_posix(),
                "rom_sha256": rom_sha256,
                "breakpoint": breakpoint,
                "breakpoint_hex": f"{breakpoint:08X}",
                "launch_flags": launch_flags,
                "default_config": binding_evidence,
                "user_config_preflight": user_config_evidence,
            }
        )

        lock = runtime_stack.enter_context(
            exclusive_file_lock(lock_path, args.lock_timeout_seconds)
        )
        session["lock"] = lock
        require_gdb_port_available("127.0.0.1", 1234)
        profile = runtime_stack.enter_context(
            tempfile.TemporaryDirectory(prefix="recursion94-m2-blastem-")
        )
        connected_runtime, connected_runtime_evidence = clone_blastem_runtime(
            emulator_directory, Path(profile)
        )
        connected_config = write_standard_pad_profile(
            default_config, Path(profile)
        )
        connected_device_profile_sha = connected_config["sha256"]
        connected_config["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(connected_config["path"]),
                Path(profile) / "session-data",
            )
        )
        connected_config["device_profile_sha256_before_paths"] = (
            connected_device_profile_sha
        )
        connected_config["sha256"] = connected_config[
            "session_isolation"
        ]["config_sha256"]
        connected_config["runtime_copy"] = connected_runtime_evidence
        command = [
            str(connected_runtime / "blastem.exe"),
            *launch_flags,
            str(rom),
        ]
        process = subprocess.Popen(
            command,
            cwd=connected_runtime,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        session["process_id"] = process.pid
        session["portable_bundled_config"] = True
        remote = GdbRemote(
            "127.0.0.1",
            1234,
            args.timeout_seconds,
            transcript,
        )
        if process.poll() is not None:
            raise EvidenceFailure(
                f"spawned BlastEm exited early with code {process.returncode}"
            )
        inserted = remote.request(f"Z0,{breakpoint:x},2")
        if inserted != "OK":
            raise EvidenceFailure(
                f"GDB breakpoint insertion returned {inserted!r}"
            )
        samples, injection = collect_samples(
            remote=remote,
            process_id=process.pid,
            inject_input=not args.skip_input_injection,
            warmup_frames=args.warmup_frames,
        )
        session["audio_readiness"] = injection["audio_readiness"]
        session["vblank_entry_accounting"] = injection[
            "vblank_entry_accounting"
        ]
        removed = remote.request(f"z0,{breakpoint:x},2")
        if removed != "OK":
            raise EvidenceFailure(
                f"GDB breakpoint removal returned {removed!r}"
            )
        shutdown_emulator(remote, process, transcript)
        remote = None
        process = None

        require_gdb_port_available("127.0.0.1", 1234)
        disconnected_profile = runtime_stack.enter_context(
            tempfile.TemporaryDirectory(
                prefix="recursion94-m2-disconnected-"
            )
        )
        disconnected_runtime, disconnected_runtime_evidence = (
            clone_blastem_runtime(
                emulator_directory, Path(disconnected_profile)
            )
        )
        disconnect_config = write_disconnected_profile(
            default_config, Path(disconnected_profile)
        )
        disconnected_device_profile_sha = disconnect_config["sha256"]
        disconnect_config["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(disconnect_config["path"]),
                Path(disconnected_profile) / "session-data",
            )
        )
        disconnect_config["device_profile_sha256_before_paths"] = (
            disconnected_device_profile_sha
        )
        disconnect_config["sha256"] = disconnect_config[
            "session_isolation"
        ]["config_sha256"]
        disconnect_config["runtime_copy"] = disconnected_runtime_evidence
        disconnected_command = [
            str(disconnected_runtime / "blastem.exe"),
            *launch_flags,
            str(rom),
        ]
        process = subprocess.Popen(
            disconnected_command,
            cwd=disconnected_runtime,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        session["disconnected_process_id"] = process.pid
        remote = GdbRemote(
            "127.0.0.1",
            1234,
            args.timeout_seconds,
            transcript,
        )
        if process.poll() is not None:
            raise EvidenceFailure(
                "disconnected-profile BlastEm exited early with code "
                f"{process.returncode}"
            )
        inserted = remote.request(f"Z0,{breakpoint:x},2")
        if inserted != "OK":
            raise EvidenceFailure(
                "disconnected-profile breakpoint insertion returned "
                f"{inserted!r}"
            )
        disconnected_entry_boot_stages: list[int] = []
        disconnected_first: dict[str, Any] | None = None
        for index in range(args.warmup_frames):
            disconnected_first = continue_to_sample(
                remote,
                f"disconnected_warmup_entry_{index + 1}",
                entry_boot_stages=disconnected_entry_boot_stages,
            )
        assert disconnected_first is not None
        disconnected_first["label"] = "disconnected_first"
        disconnected_samples = [
            disconnected_first,
            continue_to_sample(
                remote,
                "disconnected_second",
                entry_boot_stages=disconnected_entry_boot_stages,
            ),
        ]
        session["disconnected_vblank_entry_accounting"] = (
            vblank_entry_accounting(disconnected_entry_boot_stages)
        )
        removed = remote.request(f"z0,{breakpoint:x},2")
        if removed != "OK":
            raise EvidenceFailure(
                "disconnected-profile breakpoint removal returned "
                f"{removed!r}"
            )
        shutdown_emulator(remote, process, transcript)
        remote = None
        process = None

        if sha256_file(rom) != rom_sha256:
            raise EvidenceFailure("Debug ROM changed during evidence capture")
        session["status"] = "pass"
        reports = {
            "controller": controller_report(
                samples=samples,
                disconnected_samples=disconnected_samples,
                rom_sha256=rom_sha256,
                session=session,
                injection=injection,
                connected_config=connected_config,
                disconnect_config=disconnect_config,
            ),
            "video_dma": video_dma_report(
                samples=samples,
                rom_sha256=rom_sha256,
                session=session,
            ),
            "z80_audio_runtime": z80_audio_runtime_report(
                samples=samples,
                rom_sha256=rom_sha256,
                session=session,
            ),
        }
    except Exception as error:
        reports = failure_reports(
            rom_sha256=rom_sha256,
            error=error,
            session=session,
        )
    finally:
        shutdown_emulator(remote, process, transcript)
        remote = None
        process = None
        try:
            runtime_stack.close()
        except Exception as error:
            transcript.append(
                f"! runtime context cleanup failed: {type(error).__name__}: {error}"
            )

        trace_dir.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        transcript_sha256 = sha256_file(transcript_path)
        for key, report in reports.items():
            report["transcript"] = {
                "path": transcript_path.relative_to(root).as_posix(),
                "sha256": transcript_sha256,
                "commands": sum(line.startswith("> ") for line in transcript),
            }
            write_json(report_dir / REPORT_FILENAMES[key], report)
        try:
            from tools.m2_heartbeat_report import heartbeat_report

            controller_path = report_dir / REPORT_FILENAMES["controller"]
            heartbeat_evidence = heartbeat_report(
                root=root,
                rom_sha256=rom_sha256,
                source_report=reports["controller"],
                source_report_path=controller_path,
                transcript_path=transcript_path,
            )
        except Exception as error:
            heartbeat_evidence = {
                "schema": 1,
                "scope": "Milestone2",
                "evidence_type": "current_m2_blastem_gdb_heartbeat_derivative",
                "status": "fail",
                "rom_sha256": rom_sha256,
                "checks": [
                    check(
                        "heartbeat_derivation",
                        False,
                        f"{type(error).__name__}: {error}",
                    )
                ],
            }
        write_json(
            report_dir / "emulator-heartbeat.json",
            heartbeat_evidence,
        )

    passed = bool(reports) and all(
        report.get("status") == "pass" for report in reports.values()
    ) and heartbeat_evidence.get("status") == "pass"
    print(
        "Milestone 2 GDB RAM evidence: "
        f"{'PASS' if passed else 'FAIL'}; screenshot, fatal-screen, and "
        "audible audio evidence remain distinct reports"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
