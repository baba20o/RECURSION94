#!/usr/bin/env python3
"""Collect live, hash-bound Milestone 3 deterministic-runtime evidence.

The collector launches two independent portable BlastEm sessions.  It observes
guest state only through GDB memory reads at symbol-bound breakpoints, including
the real automatic timeout commit/reset path.  It never writes guest RAM,
registers, or the program counter.
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
    parse_vasm_symbols,
    require_gdb_port_available,
    require_stop,
    sha256_file,
    shutdown_emulator,
    verify_no_user_blastem_override,
    write_json,
    write_standard_pad_profile,
)


REPORT_SCHEMA = "r94-m3-runtime-evidence-v2"
EVIDENCE_TYPE = "blastem_gdb_m3_deterministic_engine"
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"

ENGINE_CONTROL_BASE = 0x00FF1400
ENGINE_CONTROL_REGION_BYTES = 0x0400
ENGINE_CONTROL_USED_BYTES = 0x0050
ENGINE_ORDINARY_BASE = 0x00FF1800
ENGINE_ORDINARY_BYTES = 0x4800
ENGINE_ACTOR_REGION_BYTES = 0x0800
ENGINE_RESETTABLE_REGION_OFFSET = ENGINE_ACTOR_REGION_BYTES
ENGINE_RESETTABLE_REGION_BYTES = (
    ENGINE_ORDINARY_BYTES - ENGINE_RESETTABLE_REGION_OFFSET
)
ENGINE_PROJECTION_BASE = 0x00FF6000
ENGINE_PROJECTION_BYTES = 3638
INPUT_STATE_BASE = 0x00FF9800
INPUT_STATE_BYTES = 7

ENGINE_MAGIC = 0x52393445
ENGINE_ABI_VERSION = 1
ENGINE_STATE_ACTIVE = 1
ENGINE_STATE_TERMINAL = 2
ENGINE_TERMINAL_NONE = 0
ENGINE_TERMINAL_TIMEOUT = 1
ENGINE_FLAG_LOADED = 0x0001
ENGINE_FLAG_PROJECTION_VALID = 0x0002
ENGINE_ATTEMPT_TICKS = 720
ENGINE_CHECKPOINT_INTERVAL = 60
ENGINE_CHECKPOINT_TICKS = tuple(range(0, 721, 60))

PROJECTION_MAGIC = 0x52393450
PROJECTION_SCHEMA_VERSION = 1
PROJECTION_HEADER_BYTES = 0x28
PROJECTION_ACTOR_BYTES = 0x1E
PROJECTION_POOL_HEADER_BYTES = 4

ACTOR_CAPACITY = 7
ACTOR_BYTES = 0x20
CURRENT_ACTOR_SLOT = 6
REPLAY_BANK_COUNT = 7
OBJECT_BYTES = 0x18
POOL_LAYOUT = (
    (1, "enemy", 0x00FF2000, 16),
    (2, "projectile", 0x00FF2200, 32),
    (3, "device", 0x00FF2600, 32),
    (4, "carryable", 0x00FF2A00, 8),
    (5, "hazard", 0x00FF2B00, 16),
    (6, "boss", 0x00FF2D00, 12),
    (7, "effect", 0x00FF2E80, 24),
)

REQUIRED_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopPostGameplayStepCall",
    "MainLoopPostCommitReset",
)
LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _hex32(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def crc32_iso_hdlc(payload: bytes) -> int:
    """Independently compute reflected CRC-32/ISO-HDLC."""
    crc = 0xFFFFFFFF
    for byte in payload:
        crc ^= byte
        for _bit in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def parse_engine_control(raw: bytes) -> dict[str, Any]:
    """Parse the stable, explicitly-sized engine control prefix."""
    if len(raw) < ENGINE_CONTROL_USED_BYTES:
        raise EvidenceFailure(
            f"engine control read is {len(raw)} bytes; "
            f"{ENGINE_CONTROL_USED_BYTES} required"
        )
    return {
        "magic": _u32(raw, 0x00),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "abi_version": _u16(raw, 0x04),
        "control_bytes": _u16(raw, 0x06),
        "state": _u16(raw, 0x08),
        "flags": _u16(raw, 0x0A),
        "encounter": _u16(raw, 0x0C),
        "attempt": _u16(raw, 0x0E),
        "tick": _u16(raw, 0x10),
        "terminal_reason": _u16(raw, 0x12),
        "last_error": _u16(raw, 0x14),
        "overflow_flags": _u16(raw, 0x16),
        "projection_length": _u16(raw, 0x18),
        "state_crc": _u32(raw, 0x1A),
        "checkpoint_tick": _u16(raw, 0x1E),
        "checkpoint_count": _u16(raw, 0x20),
        "input_held": raw[0x22],
        "input_pressed": raw[0x23],
        "input_released": raw[0x24],
        "input_previous": raw[0x25],
        "facing_x": int.from_bytes(raw[0x26:0x27], "big", signed=True),
        "active_actor_count": raw[0x27],
        "room_left": int.from_bytes(raw[0x28:0x2A], "big", signed=True),
        "room_top": int.from_bytes(raw[0x2A:0x2C], "big", signed=True),
        "room_right": int.from_bytes(raw[0x2C:0x2E], "big", signed=True),
        "room_bottom": int.from_bytes(raw[0x2E:0x30], "big", signed=True),
        "spawn_x": int.from_bytes(raw[0x30:0x34], "big", signed=True),
        "spawn_y": int.from_bytes(raw[0x34:0x38], "big", signed=True),
        "rect_count": _u16(raw, 0x38),
        "object_init_count": _u16(raw, 0x3A),
        "pool_counts": list(raw[0x40:0x47]),
        "pool_high_water": list(raw[0x48:0x4F]),
    }


def parse_input_state(raw: bytes) -> dict[str, int]:
    if len(raw) != INPUT_STATE_BYTES:
        raise EvidenceFailure(
            f"input-state read is {len(raw)} bytes; {INPUT_STATE_BYTES} required"
        )
    return {
        "held": raw[0],
        "pressed": raw[1],
        "released": raw[2],
        "raw_high": raw[3],
        "raw_low": raw[4],
        "connected": raw[5],
        "start_held": raw[6],
    }


def parse_projection(raw: bytes) -> dict[str, Any]:
    """Parse and structurally validate the complete canonical projection."""
    if len(raw) != ENGINE_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"projection is {len(raw)} bytes; {ENGINE_PROJECTION_BYTES} required"
        )
    magic = _u32(raw, 0x00)
    version = _u16(raw, 0x04)
    declared_bytes = _u16(raw, 0x06)
    if magic != PROJECTION_MAGIC:
        raise EvidenceFailure(
            f"projection magic is ${magic:08X}; ${PROJECTION_MAGIC:08X} required"
        )
    if version != PROJECTION_SCHEMA_VERSION:
        raise EvidenceFailure(
            f"projection schema is {version}; {PROJECTION_SCHEMA_VERSION} required"
        )
    if declared_bytes != ENGINE_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"projection declares {declared_bytes} bytes; "
            f"{ENGINE_PROJECTION_BYTES} required"
        )

    header = {
        "magic": magic,
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "schema_version": version,
        "bytes": declared_bytes,
        "encounter": _u16(raw, 0x08),
        "attempt": _u16(raw, 0x0A),
        "tick": _u16(raw, 0x0C),
        "state": _u16(raw, 0x0E),
        "terminal_reason": _u16(raw, 0x10),
        "flags": _u16(raw, 0x12),
        "overflow_flags": _u16(raw, 0x14),
        "last_error": _u16(raw, 0x16),
        "room_left": int.from_bytes(raw[0x18:0x1A], "big", signed=True),
        "room_top": int.from_bytes(raw[0x1A:0x1C], "big", signed=True),
        "room_right": int.from_bytes(raw[0x1C:0x1E], "big", signed=True),
        "room_bottom": int.from_bytes(raw[0x1E:0x20], "big", signed=True),
        "input_held": raw[0x20],
        "input_pressed": raw[0x21],
        "input_released": raw[0x22],
        "input_previous": raw[0x23],
        "facing_x": int.from_bytes(raw[0x24:0x25], "big", signed=True),
        "active_actor_count": raw[0x25],
        "rect_count": _u16(raw, 0x26),
    }

    cursor = PROJECTION_HEADER_BYTES
    actor_active_values: list[int] = []
    for _slot in range(ACTOR_CAPACITY):
        actor_active_values.append(raw[cursor])
        cursor += PROJECTION_ACTOR_BYTES

    pools: list[dict[str, Any]] = []
    invalid_active_values: list[dict[str, int]] = []
    for pool_type, name, _address, capacity in POOL_LAYOUT:
        observed_type = raw[cursor]
        observed_capacity = raw[cursor + 1]
        active_count = raw[cursor + 2]
        high_water = raw[cursor + 3]
        if observed_type != pool_type or observed_capacity != capacity:
            raise EvidenceFailure(
                f"projection pool header at ${cursor:04X} is "
                f"type/capacity {observed_type}/{observed_capacity}; "
                f"{pool_type}/{capacity} required"
            )
        cursor += PROJECTION_POOL_HEADER_BYTES
        active_values: list[int] = []
        active_ids: list[int] = []
        for slot in range(capacity):
            active = raw[cursor]
            logical_id = raw[cursor + 1]
            active_values.append(active)
            if active not in (0, 1):
                invalid_active_values.append(
                    {
                        "pool_type": pool_type,
                        "slot": slot,
                        "value": active,
                    }
                )
            if active == 1:
                active_ids.append(logical_id)
            cursor += OBJECT_BYTES
        pools.append(
            {
                "type": pool_type,
                "name": name,
                "capacity": capacity,
                "active_count": active_count,
                "observed_active_count": sum(
                    value == 1 for value in active_values
                ),
                "high_water": high_water,
                "active_logical_ids": active_ids,
            }
        )
    if cursor != ENGINE_PROJECTION_BYTES:
        raise EvidenceFailure(
            f"projection parser ended at {cursor}; "
            f"{ENGINE_PROJECTION_BYTES} required"
        )

    actor_active_count = sum(value == 1 for value in actor_active_values)
    valid = (
        not invalid_active_values
        and all(value in (0, 1) for value in actor_active_values)
        and actor_active_count == header["active_actor_count"]
        and header["active_actor_count"] <= ACTOR_CAPACITY
        and all(
            pool["active_count"] == pool["observed_active_count"]
            and pool["active_count"] <= pool["capacity"]
            and pool["high_water"] <= pool["capacity"]
            and len(pool["active_logical_ids"])
            == len(set(pool["active_logical_ids"]))
            for pool in pools
        )
    )
    return {
        "header": header,
        "actor_active_values": actor_active_values,
        "observed_active_actor_count": actor_active_count,
        "pools": pools,
        "invalid_active_values": invalid_active_values,
        "bounds_valid": valid,
    }


def analyze_ordinary_state(raw: bytes) -> dict[str, Any]:
    """Check every physical actor/pool slot and all statically unused padding."""
    if len(raw) != ENGINE_ORDINARY_BYTES:
        raise EvidenceFailure(
            f"ordinary state is {len(raw)} bytes; "
            f"{ENGINE_ORDINARY_BYTES} required"
        )
    owned = bytearray(ENGINE_ORDINARY_BYTES)
    actor_active_values: list[int] = []
    actor_records: list[dict[str, Any]] = []
    actor_reserved_nonzero: list[int] = []
    for slot in range(ACTOR_CAPACITY):
        offset = slot * ACTOR_BYTES
        record_raw = raw[offset : offset + ACTOR_BYTES]
        owned[offset : offset + ACTOR_BYTES] = b"\x01" * ACTOR_BYTES
        actor_active_values.append(raw[offset])
        actor_records.append(
            {
                "slot": slot,
                "active": record_raw[0],
                "role": record_raw[1],
                "flags": _u16(record_raw, 0x02),
                "x": int.from_bytes(
                    record_raw[0x04:0x08], "big", signed=True
                ),
                "y": int.from_bytes(
                    record_raw[0x08:0x0C], "big", signed=True
                ),
                "vx": int.from_bytes(
                    record_raw[0x0C:0x10], "big", signed=True
                ),
                "vy": int.from_bytes(
                    record_raw[0x10:0x14], "big", signed=True
                ),
                "half_width": _u16(record_raw, 0x14),
                "half_height": _u16(record_raw, 0x16),
                "input": record_raw[0x18],
                "previous_input": record_raw[0x19],
                "health": _u16(record_raw, 0x1A),
                "logical_id": _u16(record_raw, 0x1C),
                "reserved": _u16(record_raw, 0x1E),
                "all_zero": not any(record_raw),
            }
        )
        if raw[offset + 0x1E : offset + 0x20] != b"\x00\x00":
            actor_reserved_nonzero.append(slot)

    pool_counts: list[int] = []
    invalid_active_values: list[dict[str, int]] = []
    for pool_type, _name, address, capacity in POOL_LAYOUT:
        base = address - ENGINE_ORDINARY_BASE
        owned[base : base + capacity * OBJECT_BYTES] = (
            b"\x01" * (capacity * OBJECT_BYTES)
        )
        active_count = 0
        for slot in range(capacity):
            active = raw[base + slot * OBJECT_BYTES]
            if active == 1:
                active_count += 1
            elif active != 0:
                invalid_active_values.append(
                    {
                        "pool_type": pool_type,
                        "slot": slot,
                        "value": active,
                    }
                )
        pool_counts.append(active_count)

    unused_nonzero_offsets = [
        index
        for index, (is_owned, value) in enumerate(zip(owned, raw))
        if not is_owned and value
    ]
    actor_count = sum(value == 1 for value in actor_active_values)
    valid = (
        all(value in (0, 1) for value in actor_active_values)
        and actor_count <= ACTOR_CAPACITY
        and not actor_reserved_nonzero
        and not invalid_active_values
        and not unused_nonzero_offsets
        and all(
            count <= capacity
            for count, (_kind, _name, _address, capacity) in zip(
                pool_counts, POOL_LAYOUT
            )
        )
    )
    return {
        "valid": valid,
        "actor_active_values": actor_active_values,
        "actor_records": actor_records,
        "active_actor_count": actor_count,
        "pool_counts": pool_counts,
        "actor_reserved_nonzero_slots": actor_reserved_nonzero,
        "invalid_active_values": invalid_active_values,
        "unused_nonzero_count": len(unused_nonzero_offsets),
        "unused_nonzero_prefix": unused_nonzero_offsets[:16],
    }


def _projection_control_mismatches(
    control: dict[str, Any], projection: dict[str, Any]
) -> list[str]:
    header = projection["header"]
    fields = (
        "encounter",
        "attempt",
        "tick",
        "state",
        "terminal_reason",
        "flags",
        "overflow_flags",
        "last_error",
        "room_left",
        "room_top",
        "room_right",
        "room_bottom",
        "input_held",
        "input_pressed",
        "input_released",
        "input_previous",
        "facing_x",
        "active_actor_count",
        "rect_count",
    )
    return [
        name
        for name in fields
        if int(control[name]) != int(header[name])
    ]


def make_checkpoint_snapshot(
    *,
    label: str,
    expected_tick: int,
    control_raw: bytes,
    ordinary_raw: bytes,
    projection_raw: bytes,
) -> dict[str, Any]:
    if len(control_raw) != ENGINE_CONTROL_REGION_BYTES:
        raise EvidenceFailure(
            f"{label} control region is {len(control_raw)} bytes; "
            f"{ENGINE_CONTROL_REGION_BYTES} required"
        )
    control = parse_engine_control(control_raw)
    projection = parse_projection(projection_raw)
    ordinary = analyze_ordinary_state(ordinary_raw)
    computed_crc = crc32_iso_hdlc(projection_raw)
    mismatches = _projection_control_mismatches(control, projection)
    projection_pool_counts = [
        int(pool["active_count"]) for pool in projection["pools"]
    ]
    projection_pool_high_water = [
        int(pool["high_water"]) for pool in projection["pools"]
    ]
    count_match = (
        ordinary["active_actor_count"] == control["active_actor_count"]
        == projection["observed_active_actor_count"]
        and ordinary["pool_counts"] == control["pool_counts"]
        == projection_pool_counts
        and control["pool_high_water"] == projection_pool_high_water
    )
    consistent = (
        not mismatches
        and control["projection_length"] == ENGINE_PROJECTION_BYTES
        and control["state_crc"] == computed_crc
        and projection["bounds_valid"]
        and ordinary["valid"]
        and count_match
    )
    return {
        "label": label,
        "expected_tick": expected_tick,
        "control": control,
        "control_region": {
            "address": f"{ENGINE_CONTROL_BASE:08X}",
            "bytes": len(control_raw),
            "sha256": sha256_bytes(control_raw),
        },
        "ordinary": {
            "address": f"{ENGINE_ORDINARY_BASE:08X}",
            "bytes": len(ordinary_raw),
            "sha256": sha256_bytes(ordinary_raw),
            "bounds": ordinary,
        },
        "projection": {
            "address": f"{ENGINE_PROJECTION_BASE:08X}",
            "bytes": len(projection_raw),
            "sha256": sha256_bytes(projection_raw),
            "crc32_iso_hdlc": _hex32(computed_crc),
            "published_crc32": _hex32(control["state_crc"]),
            "header": projection["header"],
            "observed_active_actor_count": projection[
                "observed_active_actor_count"
            ],
            "pools": projection["pools"],
            "bounds_valid": projection["bounds_valid"],
        },
        "consistency": {
            "valid": consistent,
            "projection_control_mismatches": mismatches,
            "actor_pool_counts_match": count_match,
        },
        "_control_bytes": control_raw,
        "_ordinary_bytes": ordinary_raw,
        "_projection_bytes": projection_raw,
    }


def make_control_observation(
    *, label: str, expected_tick: int, control_raw: bytes
) -> dict[str, Any]:
    control = parse_engine_control(control_raw)
    return {
        "label": label,
        "expected_tick": expected_tick,
        "control": control,
        "control_prefix_bytes": len(control_raw),
        "control_prefix_sha256": sha256_bytes(control_raw),
    }


def verify_controller_bindings(default_config: Path) -> dict[str, Any]:
    payload = default_config.read_bytes()
    text = payload.decode("utf-8", errors="replace")
    expected = {
        "up": "gamepads.1.up",
        "down": "gamepads.1.down",
        "left": "gamepads.1.left",
        "right": "gamepads.1.right",
        "a": "gamepads.1.a",
        "s": "gamepads.1.b",
        "d": "gamepads.1.c",
        "enter": "gamepads.1.start",
    }
    bindings = {
        key: bool(
            re.search(
                rf"(?m)^\s*{re.escape(key)}\s+{re.escape(action)}\s*$",
                text,
            )
        )
        for key, action in expected.items()
    }
    return {
        "status": "pass" if all(bindings.values()) else "fail",
        "bindings": bindings,
        "source_sha256": sha256_bytes(payload),
        "keys": list(expected),
        "buttons": ["Up", "Down", "Left", "Right", "A", "B", "C", "Start"],
    }


def debugger_write_packets(transcript: list[str]) -> list[str]:
    """Return RSP packets capable of writing RAM or registers/PC."""
    return [
        line
        for line in transcript
        if re.match(r"^> (?:G|M|P|X)", line)
    ]


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if not key.startswith("_")
    }


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _is_canonical_spawn_actor(
    record: dict[str, Any],
    *,
    role: int,
    logical_id: int,
    control: dict[str, Any],
) -> bool:
    return (
        record["active"] == 1
        and record["role"] == role
        and record["flags"] == 0
        and record["x"] == control["spawn_x"]
        and record["y"] == control["spawn_y"]
        and record["vx"] == 0
        and record["vy"] == 0
        and record["half_width"] == 4
        and record["half_height"] == 4
        and record["input"] == 0
        and record["previous_input"] == 0
        and record["health"] == 1
        and record["logical_id"] == logical_id
        and record["reserved"] == 0
    )


def build_runtime_report(
    *,
    rom_sha256: str,
    sessions: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Purely evaluate two collected sessions and build the public report."""
    if len(sessions) != 2:
        raise EvidenceFailure(
            f"exactly two runtime sessions are required; got {len(sessions)}"
        )

    checks: list[dict[str, Any]] = []
    expected_ticks = list(ENGINE_CHECKPOINT_TICKS)
    checkpoint_ticks = [
        [int(item["control"]["tick"]) for item in session["checkpoints"]]
        for session in sessions
    ]
    checks.append(
        _check(
            "two_independent_fresh_sessions",
            len({session["metadata"]["process_id"] for session in sessions}) == 2
            and len(
                {
                    session["metadata"]["runtime_copy"]["temporary_directory"]
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
            "explicit_standard_three_button_profiles",
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
                for session in sessions
            ),
            "port-1 assignments "
            f"{[session['metadata']['controller_profile'].get('port_1_assignment') for session in sessions]}",
        )
    )
    all_transcript = [
        line
        for session in sessions
        for line in session.get("_transcript_lines", [])
    ]
    writes = debugger_write_packets(all_transcript)
    checks.append(
        _check(
            "read_only_debugger_session",
            not writes,
            f"RAM/register write packets {writes}",
        )
    )
    checks.append(
        _check(
            "checkpoint_tick_schedule",
            all(ticks == expected_ticks for ticks in checkpoint_ticks),
            f"ticks {checkpoint_ticks}",
        )
    )
    progressions = [session["progression"] for session in sessions]
    checks.append(
        _check(
            "every_authoritative_tick_observed",
            all(
                progression == list(range(ENGINE_ATTEMPT_TICKS + 1))
                for progression in progressions
            ),
            "observations "
            f"{[(len(values), values[:2], values[-2:]) for values in progressions]}",
        )
    )
    checks.append(
        _check(
            "stable_post_step_observation",
            all(
                session["metadata"]["initial_breakpoint_symbol"]
                == "VBlankInterrupt"
                and session["metadata"].get("initial_breakpoint_role")
                == STARTUP_PRIMING_ROLE
                and session["metadata"].get("authoritative_origin")
                == FIRST_RUNNING_VBLANK_ROLE
                and session["metadata"].get(
                    "counters_and_controller_poll_begin"
                )
                == FIRST_RUNNING_VBLANK_ROLE
                and session["metadata"]["step_breakpoint_label"]
                == "MainLoopPostGameplayStepCall"
                and session["metadata"]["step_breakpoint_source"]
                == "build/recursion94-debug.sym"
                for session in sessions
            ),
            "tick 0 was captured at the stage-3 acknowledgment-only priming "
            "ISR entry; counters and controller polling begin on the next "
            "stage-4 RUNNING full VBlank, and exact ticks 1..720 were "
            "captured immediately after GameplayStep and before the main-loop "
            "timeout commit/reset branch",
        )
    )

    checkpoints = [
        item for session in sessions for item in session["checkpoints"]
    ]
    controls = [item["control"] for item in checkpoints]
    checks.append(
        _check(
            "engine_control_abi",
            all(
                control["magic"] == ENGINE_MAGIC
                and control["abi_version"] == ENGINE_ABI_VERSION
                and control["control_bytes"] == ENGINE_CONTROL_REGION_BYTES
                and control["encounter"] == 0
                and control["attempt"] == 0
                for control in controls
            ),
            "magic/version/control/encounter/attempt were checked directly",
        )
    )
    checks.append(
        _check(
            "active_through_tick_719",
            all(
                session["tick_719"]["control"]["tick"] == 719
                and session["tick_719"]["control"]["state"]
                == ENGINE_STATE_ACTIVE
                and session["tick_719"]["control"]["terminal_reason"]
                == ENGINE_TERMINAL_NONE
                and session["tick_719"]["control"]["last_error"] == 0
                and session["tick_719"]["control"]["overflow_flags"] == 0
                and session["tick_719"]["control"]["checkpoint_tick"] == 660
                and session["tick_719"]["control"]["checkpoint_count"] == 12
                for session in sessions
            ),
            "tick/state/reason "
            f"{[(s['tick_719']['control']['tick'], s['tick_719']['control']['state'], s['tick_719']['control']['terminal_reason']) for s in sessions]}",
        )
    )
    terminal_controls = [
        session["checkpoints"][-1]["control"] for session in sessions
    ]
    checks.append(
        _check(
            "exact_tick_720_timeout_terminal",
            all(
                control["tick"] == ENGINE_ATTEMPT_TICKS
                and control["state"] == ENGINE_STATE_TERMINAL
                and control["terminal_reason"] == ENGINE_TERMINAL_TIMEOUT
                and control["last_error"] == 0
                and control["overflow_flags"] == 0
                for control in terminal_controls
            ),
            "terminal tick/state/reason "
            f"{[(c['tick'], c['state'], c['terminal_reason']) for c in terminal_controls]}",
        )
    )
    checks.append(
        _check(
            "checkpoint_publication_counts",
            all(
                item["control"]["checkpoint_tick"]
                == item["control"]["tick"]
                and item["control"]["checkpoint_count"]
                == item["control"]["tick"] // ENGINE_CHECKPOINT_INTERVAL + 1
                for item in checkpoints
            ),
            "checkpoint ticks/counts "
            f"{[[(i['control']['checkpoint_tick'], i['control']['checkpoint_count']) for i in s['checkpoints']] for s in sessions]}",
        )
    )
    checks.append(
        _check(
            "no_error_or_overflow",
            all(
                control["last_error"] == 0
                and control["overflow_flags"] == 0
                for control in controls
            ),
            "last_error and overflow flags remained zero",
        )
    )
    checks.append(
        _check(
            "projection_and_control_consistency",
            all(item["consistency"]["valid"] for item in checkpoints),
            "per-checkpoint projection/control mismatch lists "
            f"{[item['consistency']['projection_control_mismatches'] for item in checkpoints]}",
        )
    )
    recomputed_sequences: list[list[tuple[int, int]]] = []
    crc_publications_valid = True
    for session in sessions:
        sequence: list[tuple[int, int]] = []
        for item in session["checkpoints"]:
            computed = crc32_iso_hdlc(item["_projection_bytes"])
            sequence.append((int(item["control"]["tick"]), computed))
            crc_publications_valid &= (
                computed == int(item["control"]["state_crc"])
                and _hex32(computed)
                == item["projection"]["crc32_iso_hdlc"]
            )
        recomputed_sequences.append(sequence)
    checks.append(
        _check(
            "independent_crc32_matches_every_publication",
            crc_publications_valid,
            "independent sequences "
            f"{[[(tick, _hex32(crc)) for tick, crc in seq] for seq in recomputed_sequences]}",
        )
    )
    checks.append(
        _check(
            "checkpoint_crc_sequence_not_flat",
            all(
                len({crc for _tick, crc in sequence})
                == len(ENGINE_CHECKPOINT_TICKS)
                for sequence in recomputed_sequences
            ),
            "distinct CRC counts "
            f"{[len({crc for _tick, crc in seq}) for seq in recomputed_sequences]}",
        )
    )
    checks.append(
        _check(
            "actor_and_pool_bounds",
            all(
                item["ordinary"]["bounds"]["valid"]
                and item["projection"]["bounds_valid"]
                and item["control"]["active_actor_count"] <= ACTOR_CAPACITY
                and all(
                    count <= layout[3]
                    for count, layout in zip(
                        item["control"]["pool_counts"], POOL_LAYOUT
                    )
                )
                and all(
                    high <= layout[3]
                    for high, layout in zip(
                        item["control"]["pool_high_water"], POOL_LAYOUT
                    )
                )
                for item in checkpoints
            ),
            "all actor/pool counts, active bytes, reserved bytes, and "
            "ordinary-region padding were bounded",
        )
    )
    checks.append(
        _check(
            "repeated_checkpoint_sequences_match",
            recomputed_sequences[0] == recomputed_sequences[1],
            "session sequence equality "
            f"{recomputed_sequences[0] == recomputed_sequences[1]}",
        )
    )
    terminal_projection_equal = (
        sessions[0]["checkpoints"][-1]["_projection_bytes"]
        == sessions[1]["checkpoints"][-1]["_projection_bytes"]
    )
    checks.append(
        _check(
            "terminal_projection_bytes_and_hashes_match",
            terminal_projection_equal
            and sessions[0]["checkpoints"][-1]["projection"]["sha256"]
            == sessions[1]["checkpoints"][-1]["projection"]["sha256"],
            "terminal hashes "
            f"{[s['checkpoints'][-1]['projection']['sha256'] for s in sessions]}",
        )
    )
    initial_ordinary_equal = (
        sessions[0]["checkpoints"][0]["_ordinary_bytes"]
        == sessions[1]["checkpoints"][0]["_ordinary_bytes"]
    )
    checks.append(
        _check(
            "fresh_initial_ordinary_state_matches",
            initial_ordinary_equal,
            "initial ordinary hashes "
            f"{[s['checkpoints'][0]['ordinary']['sha256'] for s in sessions]}",
        )
    )

    checks.append(
        _check(
            "neutral_timeout_terminal_input",
            all(
                session["terminal_pre_input"]["connected"] == 1
                and session["terminal_pre_input"]["held"] == 0
                and session["terminal_pre_input"]["pressed"] == 0
                and session["terminal_pre_input"]["start_held"] == 0
                for session in sessions
            ),
            "terminal input held/pressed/start "
            f"{[(s['terminal_pre_input']['held'], s['terminal_pre_input']['pressed'], s['terminal_pre_input']['start_held']) for s in sessions]}",
        )
    )
    checks.append(
        _check(
            "automatic_timeout_reset_binding",
            all(
                session["metadata"].get("reset_breakpoint_label")
                == "MainLoopPostCommitReset"
                and session["metadata"].get("reset_breakpoint_source")
                == "build/recursion94-debug.sym"
                and session["transition"].get("status") == "pass"
                and session["transition"].get("cause") == "timeout"
                and session["transition"].get("guest_input_injection") is False
                and session["transition"].get("guest_ram_register_pc_writes")
                is False
                for session in sessions
            ),
            "the unmodified main-loop timeout result flowed through "
            "GameplayCommitAndReset to MainLoopPostCommitReset",
        )
    )
    reset_controls = [session["reset"]["control"] for session in sessions]
    checks.append(
        _check(
            "automatic_timeout_commit_reset_transition",
            all(
                control["tick"] == 0
                and control["attempt"] == 1
                and control["state"] == ENGINE_STATE_ACTIVE
                and control["terminal_reason"] == ENGINE_TERMINAL_NONE
                and control["last_error"] == 0
                and control["overflow_flags"] == 0
                and control["checkpoint_tick"] == 0
                and control["checkpoint_count"] == 1
                and (control["flags"] & (
                    ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID
                ))
                == (ENGINE_FLAG_LOADED | ENGINE_FLAG_PROJECTION_VALID)
                for control in reset_controls
            ),
            "reset tick/attempt/state "
            f"{[(c['tick'], c['attempt'], c['state']) for c in reset_controls]}",
        )
    )
    resettable_region_hashes: list[tuple[str, str]] = []
    resettable_region_equal = True
    actor_roster_valid = True
    actor_roster_details: list[dict[str, Any]] = []
    for session in sessions:
        initial = session["checkpoints"][0]
        reset = session["reset"]
        initial_region = initial["_ordinary_bytes"][
            ENGINE_RESETTABLE_REGION_OFFSET:
        ]
        reset_region = reset["_ordinary_bytes"][
            ENGINE_RESETTABLE_REGION_OFFSET:
        ]
        resettable_region_equal &= (
            len(initial_region) == ENGINE_RESETTABLE_REGION_BYTES
            and reset_region == initial_region
        )
        resettable_region_hashes.append(
            (sha256_bytes(initial_region), sha256_bytes(reset_region))
        )

        initial_actors = initial["ordinary"]["bounds"]["actor_records"]
        reset_actors = reset["ordinary"]["bounds"]["actor_records"]
        initial_control = initial["control"]
        reset_control = reset["control"]
        session_roster_valid = (
            len(initial_actors) == ACTOR_CAPACITY
            and len(reset_actors) == ACTOR_CAPACITY
            and all(initial_actors[slot]["all_zero"] for slot in range(6))
            and _is_canonical_spawn_actor(
                initial_actors[CURRENT_ACTOR_SLOT],
                role=CURRENT_ACTOR_SLOT,
                logical_id=CURRENT_ACTOR_SLOT,
                control=initial_control,
            )
            and _is_canonical_spawn_actor(
                reset_actors[0],
                role=0,
                logical_id=CURRENT_ACTOR_SLOT,
                control=reset_control,
            )
            and all(reset_actors[slot]["all_zero"] for slot in range(1, 6))
            and _is_canonical_spawn_actor(
                reset_actors[CURRENT_ACTOR_SLOT],
                role=CURRENT_ACTOR_SLOT,
                logical_id=0,
                control=reset_control,
            )
            and initial_control["active_actor_count"] == 1
            and reset_control["active_actor_count"] == 2
        )
        actor_roster_valid &= session_roster_valid
        actor_roster_details.append(
            {
                "valid": session_roster_valid,
                "initial_active": [
                    record["slot"]
                    for record in initial_actors
                    if record["active"] == 1
                ],
                "reset_active": [
                    record["slot"]
                    for record in reset_actors
                    if record["active"] == 1
                ],
                "reset_logical_ids": [
                    reset_actors[0]["logical_id"],
                    reset_actors[CURRENT_ACTOR_SLOT]["logical_id"],
                ],
            }
        )
    checks.append(
        _check(
            "reset_restores_canonical_pool_region",
            resettable_region_equal,
            f"{ENGINE_RESETTABLE_REGION_BYTES}-byte initial/reset hashes "
            f"{resettable_region_hashes}",
        )
    )
    checks.append(
        _check(
            "reset_reinstantiates_canonical_actor_roster",
            actor_roster_valid,
            "initial current bank 6 becomes echo 0/bank 6 while the new "
            f"current uses slot 6/bank 0: {actor_roster_details}",
        )
    )
    reset_projection_valid = all(
        session["reset"]["consistency"]["valid"]
        and crc32_iso_hdlc(session["reset"]["_projection_bytes"])
        == session["reset"]["control"]["state_crc"]
        for session in sessions
    )
    checks.append(
        _check(
            "reset_projection_recomputed",
            reset_projection_valid,
            "reset CRCs "
            f"{[s['reset']['projection']['crc32_iso_hdlc'] for s in sessions]}",
        )
    )
    checks.append(
        _check(
            "neutral_post_reset_continuation",
            all(
                session["reset_input"]["connected"] == 1
                and session["reset_input"]["held"] == 0
                and session["reset_input"]["pressed"] == 0
                and session["reset_input"]["start_held"] == 0
                and session["post_reset_input"]["connected"] == 1
                and session["post_reset_input"]["held"] == 0
                and session["post_reset_input"]["start_held"] == 0
                and session["post_reset"]["control"]["tick"] == 1
                and session["post_reset"]["control"]["attempt"] == 1
                for session in sessions
            ),
            "reset/post-reset held/start/tick "
            f"{[(s['reset_input']['held'], s['post_reset_input']['held'], s['post_reset']['control']['tick']) for s in sessions]}",
        )
    )
    provenance_hashes = [
        rom_sha256,
        provenance.get("emulator", {}).get("sha256"),
        provenance.get("symbol_map", {}).get("sha256"),
        provenance.get("default_config", {}).get("source_sha256"),
        provenance.get("transcript", {}).get("sha256"),
        *[
            session["metadata"]["controller_profile"].get("sha256")
            for session in sessions
        ],
        *[
            session["metadata"].get("transcript_sha256")
            for session in sessions
        ],
    ]
    checks.append(
        _check(
            "hash_bound_provenance",
            all(_valid_sha256(value) for value in provenance_hashes),
            "ROM, emulator, config, symbol map, and transcripts are "
            "SHA-256 bound",
        )
    )

    passed = bool(checks) and all(item["status"] == "pass" for item in checks)
    public_sessions: list[dict[str, Any]] = []
    for session in sessions:
        public_sessions.append(
            {
                "session_id": session["session_id"],
                "metadata": session["metadata"],
                "progression": {
                    "observations": len(session["progression"]),
                    "first": session["progression"][0],
                    "last": session["progression"][-1],
                    "sha256": sha256_bytes(
                        json.dumps(
                            session["progression"],
                            separators=(",", ":"),
                        ).encode("ascii")
                    ),
                },
                "checkpoints": [
                    _public_snapshot(item) for item in session["checkpoints"]
                ],
                "tick_719": session["tick_719"],
                "terminal_pre_input": session["terminal_pre_input"],
                "transition": session["transition"],
                "reset_input": session["reset_input"],
                "reset": _public_snapshot(session["reset"]),
                "post_reset_input": session["post_reset_input"],
                "post_reset": session["post_reset"],
                "terminal_projection_trace": session.get(
                    "terminal_projection_trace"
                ),
            }
        )
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone3",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "contract": {
            "attempt_ticks": ENGINE_ATTEMPT_TICKS,
            "checkpoint_interval": ENGINE_CHECKPOINT_INTERVAL,
            "checkpoint_ticks": expected_ticks,
            "control_region": {
                "address": f"{ENGINE_CONTROL_BASE:08X}",
                "bytes": ENGINE_CONTROL_REGION_BYTES,
            },
            "ordinary_region": {
                "address": f"{ENGINE_ORDINARY_BASE:08X}",
                "bytes": ENGINE_ORDINARY_BYTES,
                "actor_region_bytes": ENGINE_ACTOR_REGION_BYTES,
                "resettable_pool_region_offset": (
                    ENGINE_RESETTABLE_REGION_OFFSET
                ),
                "resettable_pool_region_bytes": (
                    ENGINE_RESETTABLE_REGION_BYTES
                ),
            },
            "projection": {
                "address": f"{ENGINE_PROJECTION_BASE:08X}",
                "bytes": ENGINE_PROJECTION_BYTES,
                "crc": "CRC-32/ISO-HDLC",
                "reflected_polynomial": "EDB88320",
            },
            "actor_capacity": ACTOR_CAPACITY,
            "pool_capacities": {
                name: capacity
                for _kind, name, _address, capacity in POOL_LAYOUT
            },
        },
        "checkpoint_sequences": [
            [
                {
                    "tick": tick,
                    "crc32_iso_hdlc": _hex32(crc),
                }
                for tick, crc in sequence
            ]
            for sequence in recomputed_sequences
        ],
        "terminal_projection": {
            "bytes_equal": terminal_projection_equal,
            "sha256": [
                session["checkpoints"][-1]["projection"]["sha256"]
                for session in sessions
            ],
            "crc32_iso_hdlc": [
                session["checkpoints"][-1]["projection"][
                    "crc32_iso_hdlc"
                ]
                for session in sessions
            ],
        },
        "sessions": public_sessions,
        "provenance": provenance,
        "debugger_write_packets": writes,
        "notes": [
            "Both sessions are fresh portable BlastEm processes with explicit "
            "gamepad3.1 profiles and redirected writable paths.",
            "Tick 0 was observed at the acknowledgment-only priming VBlank; "
            "ticks 1 through 720 were observed immediately after GameplayStep, "
            "before the timeout branch can reset tick 720.",
            "Projection CRCs are recomputed host-side with an independent "
            "bitwise CRC-32/ISO-HDLC implementation.",
            "The automatic timeout commit/reset is observed at "
            "MainLoopPostCommitReset with neutral input. No guest RAM, "
            "register, or PC write packet is permitted.",
            "Reset equality is exact across the 16,384-byte pool/world "
            "region. The actor region is checked against the intentional "
            "post-commit roster: one canonical echo plus one canonical current "
            "actor, rather than falsely requiring persistent echo state to "
            "vanish.",
        ],
    }


def _read_region(
    remote: GdbRemote, address: int, size: int, chunk_size: int = 0xFF
) -> bytes:
    # Pinned BlastEm truncates an RSP memory reply to 255 bytes without
    # returning an error.  Never issue a larger request.
    if size <= 0 or chunk_size <= 0:
        raise ValueError("memory read sizes must be positive")
    if chunk_size > 0xFF:
        raise ValueError("BlastEm memory-read chunks may not exceed 255 bytes")
    output = bytearray()
    while len(output) < size:
        offset = len(output)
        amount = min(chunk_size, size - offset)
        output.extend(remote.read_memory(address + offset, amount))
    if len(output) != size:
        raise EvidenceFailure(
            f"assembled memory read is {len(output)} bytes; {size} required"
        )
    return bytes(output)


def _capture_checkpoint(
    remote: GdbRemote, *, label: str, expected_tick: int
) -> dict[str, Any]:
    return make_checkpoint_snapshot(
        label=label,
        expected_tick=expected_tick,
        control_raw=_read_region(
            remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_REGION_BYTES
        ),
        ordinary_raw=_read_region(
            remote, ENGINE_ORDINARY_BASE, ENGINE_ORDINARY_BYTES
        ),
        projection_raw=_read_region(
            remote, ENGINE_PROJECTION_BASE, ENGINE_PROJECTION_BYTES
        ),
    )


def _run_live_session(
    *,
    session_id: str,
    root: Path,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    post_step_address: int,
    post_reset_address: int,
    timeout_seconds: float,
    transcript: list[str],
) -> dict[str, Any]:
    start_index = len(transcript)
    transcript.append(f"# {session_id} begin")
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    session: dict[str, Any]
    with tempfile.TemporaryDirectory(
        prefix=f"recursion94-m3-{session_id}-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        device_profile_sha = controller_profile["sha256"]
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
            )
        )
        controller_profile["device_profile_sha256_before_paths"] = (
            device_profile_sha
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
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint insertion failed"
                )
            # This first entry is the deliberate stage-3 acknowledgment-only
            # startup ISR. It does not poll the controller, advance counters,
            # or publish a frame token. The next stage-4 full VBlank does.
            require_stop(remote.request("c"))
            initial = _capture_checkpoint(
                remote, label="tick_000", expected_tick=0
            )
            if initial["control"]["tick"] != 0:
                raise EvidenceFailure(
                    f"{session_id} initial tick is "
                    f"{initial['control']['tick']}; 0 required"
                )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step_address:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-step breakpoint insertion failed"
                )
            checkpoints = [initial]
            progression = [0]
            tick_719: dict[str, Any] | None = None

            for expected_tick in range(1, ENGINE_ATTEMPT_TICKS + 1):
                require_stop(remote.request("c"))
                control_raw = remote.read_memory(
                    ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
                )
                observation = make_control_observation(
                    label=f"tick_{expected_tick:03d}",
                    expected_tick=expected_tick,
                    control_raw=control_raw,
                )
                observed_tick = int(observation["control"]["tick"])
                if observed_tick != expected_tick:
                    raise EvidenceFailure(
                        f"{session_id} observed tick {observed_tick} "
                        f"where {expected_tick} was required"
                    )
                progression.append(observed_tick)
                if expected_tick == 719:
                    tick_719 = observation
                if expected_tick % ENGINE_CHECKPOINT_INTERVAL == 0:
                    checkpoint = _capture_checkpoint(
                        remote,
                        label=f"tick_{expected_tick:03d}",
                        expected_tick=expected_tick,
                    )
                    if checkpoint["control"]["tick"] != expected_tick:
                        raise EvidenceFailure(
                            f"{session_id} checkpoint drifted while stopped"
                        )
                    checkpoints.append(checkpoint)

            if tick_719 is None:
                raise EvidenceFailure(f"{session_id} did not capture tick 719")
            terminal = checkpoints[-1]
            if terminal["control"]["tick"] != ENGINE_ATTEMPT_TICKS:
                raise EvidenceFailure(
                    f"{session_id} terminal checkpoint is not tick 720"
                )
            terminal_pre_input = parse_input_state(
                remote.read_memory(INPUT_STATE_BASE, INPUT_STATE_BYTES)
            )
            if remote.request(f"z0,{post_step_address:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-step breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_reset_address:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-reset breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            reset_input = parse_input_state(
                remote.read_memory(INPUT_STATE_BASE, INPUT_STATE_BYTES)
            )
            reset = _capture_checkpoint(
                remote,
                label="automatic_timeout_reset_tick_000",
                expected_tick=0,
            )

            if remote.request(f"z0,{post_reset_address:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-reset breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step_address:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} continuation breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            post_reset_input = parse_input_state(
                remote.read_memory(INPUT_STATE_BASE, INPUT_STATE_BYTES)
            )
            post_reset = make_control_observation(
                label="post_reset_tick_001",
                expected_tick=1,
                control_raw=remote.read_memory(
                    ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
                ),
            )
            if remote.request(f"z0,{post_step_address:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} continuation breakpoint removal failed"
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
                    "counters_and_controller_poll_begin": (
                        FIRST_RUNNING_VBLANK_ROLE
                    ),
                    "step_breakpoint_label": (
                        "MainLoopPostGameplayStepCall"
                    ),
                    "step_breakpoint": f"{post_step_address:08X}",
                    "step_breakpoint_source": "build/recursion94-debug.sym",
                    "reset_breakpoint_label": "MainLoopPostCommitReset",
                    "reset_breakpoint": f"{post_reset_address:08X}",
                    "reset_breakpoint_source": (
                        "build/recursion94-debug.sym"
                    ),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "transcript_sha256": sha256_bytes(
                        (
                            "\n".join(session_lines)
                            + ("\n" if session_lines else "")
                        ).encode("utf-8")
                    ),
                },
                "progression": progression,
                "checkpoints": checkpoints,
                "tick_719": tick_719,
                "terminal_pre_input": terminal_pre_input,
                "transition": {
                    "status": "pass",
                    "method": (
                        "GameplayStep timeout result followed the "
                        "unmodified MainLoopCommitResetCall branch"
                    ),
                    "cause": "timeout",
                    "guest_input_injection": False,
                    "guest_ram_register_pc_writes": False,
                },
                "reset_input": reset_input,
                "reset": reset,
                "post_reset_input": post_reset_input,
                "post_reset": post_reset,
                "_transcript_lines": session_lines,
            }
        finally:
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    # Include cleanup packets and the explicit end marker in the session hash.
    session_lines = transcript[start_index:]
    session["_transcript_lines"] = session_lines
    session["metadata"]["transcript_sha256"] = sha256_bytes(
        (
            "\n".join(session_lines)
            + ("\n" if session_lines else "")
        ).encode("utf-8")
    )
    return session


def _failure_report(
    *,
    rom_sha256: str,
    error: Exception,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone3",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "rom_sha256": rom_sha256,
        "checks": [_check("m3_runtime_evidence_session", False, detail)],
        "error": detail,
        "provenance": provenance,
        "debugger_write_packets": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace-dir", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m3-runtime.json"
    )
    trace_dir = (
        args.trace_dir.resolve()
        if args.trace_dir
        else root / "artifacts/traces"
    )
    transcript_path = trace_dir / "m3-runtime-gdb-transcript.log"
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    sessions: list[dict[str, Any]] = []
    rom_sha256 = ""
    provenance: dict[str, Any] = {}
    report: dict[str, Any]

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M3 runtime evidence requires native Windows BlastEm"
            )
        if not 5.0 <= args.timeout_seconds <= 600.0:
            raise ValueError("--timeout-seconds must be within 5..600")
        rom_sha256 = sha256_file(rom)
        if args.expected_sha256 and (
            rom_sha256 != args.expected_sha256.strip().lower()
        ):
            raise EvidenceFailure(
                f"Debug ROM SHA-256 {rom_sha256} does not match "
                f"{args.expected_sha256.strip().lower()}"
            )

        emulator_directory = root / ".toolcache/blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        symbol_path = root / "build/recursion94-debug.sym"
        for path, description in (
            (rom, "Debug ROM"),
            (emulator, "pinned BlastEm"),
            (default_config, "pinned default.cfg"),
            (symbol_path, "Debug symbol map"),
        ):
            if not path.is_file():
                raise EvidenceFailure(f"{description} is missing: {path}")
        user_config = verify_no_user_blastem_override()
        bindings = verify_controller_bindings(default_config)
        if bindings["status"] != "pass":
            raise EvidenceFailure(
                "pinned default.cfg does not provide the complete standard "
                "three-button controller mapping"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"Debug symbol map is missing required symbols {missing}"
            )

        provenance = {
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
            },
            "emulator": {
                "path": emulator.relative_to(root).as_posix(),
                "sha256": sha256_file(emulator),
            },
            "default_config": {
                "path": default_config.relative_to(root).as_posix(),
                **bindings,
            },
            "symbol_map": {
                "path": symbol_path.relative_to(root).as_posix(),
                "sha256": sha256_file(symbol_path),
                "symbols": {
                    name: f"{symbols[name]:08X}" for name in REQUIRED_SYMBOLS
                },
            },
            "user_config_preflight": user_config,
            "serialized_lock": lock_path.relative_to(root).as_posix(),
            "launch_flags": list(LAUNCH_FLAGS),
        }

        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            for session_number in (1, 2):
                sessions.append(
                    _run_live_session(
                        session_id=f"session_{session_number}",
                        root=root,
                        rom=rom,
                        emulator_directory=emulator_directory,
                        default_config=default_config,
                        symbols=symbols,
                        post_step_address=symbols[
                            "MainLoopPostGameplayStepCall"
                        ],
                        post_reset_address=symbols[
                            "MainLoopPostCommitReset"
                        ],
                        timeout_seconds=args.timeout_seconds,
                        transcript=transcript,
                    )
                )

        trace_dir.mkdir(parents=True, exist_ok=True)
        for session_number, session in enumerate(sessions, start=1):
            terminal_path = (
                trace_dir
                / f"m3-session-{session_number}-terminal-projection.bin"
            )
            terminal_payload = session["checkpoints"][-1][
                "_projection_bytes"
            ]
            terminal_path.write_bytes(terminal_payload)
            session["terminal_projection_trace"] = {
                "path": terminal_path.relative_to(root).as_posix(),
                "bytes": len(terminal_payload),
                "sha256": sha256_file(terminal_path),
            }

        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        provenance["transcript"] = {
            "path": transcript_path.relative_to(root).as_posix(),
            "sha256": sha256_file(transcript_path),
            "commands": sum(line.startswith("> ") for line in transcript),
        }
        if sha256_file(rom) != rom_sha256:
            raise EvidenceFailure("Debug ROM changed during M3 capture")
        if sha256_file(emulator) != provenance["emulator"]["sha256"]:
            raise EvidenceFailure("pinned BlastEm changed during M3 capture")
        if sha256_file(symbol_path) != provenance["symbol_map"]["sha256"]:
            raise EvidenceFailure("Debug symbol map changed during M3 capture")
        if (
            sha256_file(default_config)
            != provenance["default_config"]["source_sha256"]
        ):
            raise EvidenceFailure("pinned default.cfg changed during M3 capture")
        report = build_runtime_report(
            rom_sha256=rom_sha256,
            sessions=sessions,
            provenance=provenance,
        )
    except Exception as error:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        provenance["transcript"] = {
            "path": transcript_path.relative_to(root).as_posix(),
            "sha256": sha256_file(transcript_path),
            "commands": sum(line.startswith("> ") for line in transcript),
        }
        report = _failure_report(
            rom_sha256=rom_sha256,
            error=error,
            provenance=provenance,
        )
        report["debugger_write_packets"] = debugger_write_packets(transcript)

    write_json(report_path, report)
    passed = report["status"] == "pass"
    print(
        "Milestone 3 live runtime evidence: "
        f"{'PASS' if passed else 'FAIL'}; {report_path}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
