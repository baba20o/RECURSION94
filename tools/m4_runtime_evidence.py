#!/usr/bin/env python3
"""Collect live, replay-producing Milestone 4 B01 runtime evidence.

The collector launches two independent portable BlastEm sessions.  Inputs are
delivered only as window key messages to an explicitly configured standard
three-button pad.  Guest state is observed only with GDB memory reads at a
symbol-bound post-gameplay breakpoint; no RAM, register, or PC write packet is
permitted.

The resulting FULL_HISTORY R94R is assembled from input bytes read back from
guest replay RAM and from the observed terminal outcome.  Expected replay
fields remain assertions: this collector never writes them into the guest or
uses them to force simulation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
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
    ENGINE_CONTROL_REGION_BYTES,
    ENGINE_MAGIC,
    ENGINE_PROJECTION_BYTES,
    ENGINE_PROJECTION_BASE,
    ENGINE_STATE_ACTIVE,
    ENGINE_STATE_TERMINAL,
    INPUT_STATE_BASE,
    INPUT_STATE_BYTES,
    _read_region,
    crc32_iso_hdlc,
    debugger_write_packets,
    parse_engine_control,
    parse_input_state,
    parse_projection,
)
from tools.replay_tools import (  # noqa: E402
    Attempt,
    AttemptOperation,
    Checkpoint,
    CompatibilityIds,
    InputEncoding,
    NaturalEndReason,
    Outcome,
    Replay,
    ReplayMode,
    TimingMode,
    content_id_from_sha256,
    decode_replay,
    encode_replay,
    replay_report,
    validate_replay,
)
from tools.replay_tools.codec import NO_TARGET  # noqa: E402


REPORT_SCHEMA = "r94-m4-runtime-evidence-v1"
EVIDENCE_TYPE = "blastem_gdb_m4_b01_full_history"
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"

REPLAY_BANK_BASE = 0x00FF0000
REPLAY_BANK_BYTES = 720
REPLAY_BANK_COUNT = 7
REPLAY_BANK_REGION_BYTES = REPLAY_BANK_BYTES * REPLAY_BANK_COUNT
REPLAY_DESCRIPTOR_BASE = 0x00FF13B0
REPLAY_DESCRIPTOR_BYTES = 8
REPLAY_DESCRIPTOR_REGION_BYTES = REPLAY_DESCRIPTOR_BYTES * REPLAY_BANK_COUNT
TIMELINE_CONTROL_BASE = 0x00FF13E8
TIMELINE_CONTROL_BYTES = 24
REPLAY_TIMELINE_BYTES = 5120

ENGINE_CONTROL_BASE = 0x00FF1400
ENGINE_CONTROL_USED_BYTES = 0x50
ENGINE_ORDINARY_BASE = 0x00FF1800
ENGINE_ORDINARY_BYTES = 0x4800
ENGINE_ACTOR_BASE = 0x00FF1800
ACTOR_BYTES = 0x20
ACTOR_COUNT = 7
CURRENT_ACTOR_SLOT = 6
B01_DEVICE_BASE = 0x00FF2600
OBJECT_BYTES = 0x18

GAMEPLAY_CONTROL_BASE = 0x00FF1500
GAMEPLAY_CONTROL_USED_BYTES = 0x24
GAMEPLAY_CONTROL_DECLARED_BYTES = 0x100

ENGINE_ABI_VERSION = 1
ENGINE_TERMINAL_NONE = 0
GAMEPLAY_TERMINAL_PROGRAM_STABLE = 0x0100
GAMEPLAY_RESULT_OK = 0
GAMEPLAY_RESULT_PROGRAM_STABLE = 1
GAMEPLAY_COMMIT_MANUAL = 2

REPLAY_FLAG_VALID = 0x01
REPLAY_FLAG_ECHO = 0x02
REPLAY_FLAG_CURRENT = 0x04
TIMELINE_FLAG_INITIALIZED = 0x01
TIMELINE_FLAG_PROGRAM_STABLE = 0x40
GAMEPLAY_FLAG_INITIALIZED = 0x0001
GAMEPLAY_FLAG_PROGRAM_STABLE = 0x0002
GAMEPLAY_FLAG_DOOR_OPEN = 0x0008
GAMEPLAY_FLAG_SWITCH_HELD = 0x0010

INPUT_RIGHT = 0x08
INPUT_B = 0x20
INPUT_C = 0x40
CANONICAL_ATTEMPT_0 = bytes((INPUT_RIGHT,)) * 32 + bytes(8)
CANONICAL_ATTEMPT_1 = bytes((INPUT_RIGHT,)) * 165 + bytes(
    (INPUT_RIGHT | INPUT_B,)
)
CANONICAL_BANK_6_CRC = 0x96501AB7
GOLDEN_B01_PROJECTION_CRC = 0x8762E745

TIMELINE_MAGIC = 0x52393454
GAMEPLAY_MAGIC = 0x52393447
PROJECTION_MAGIC = 0x52393450
STAGE_ID_B01 = 101

REQUIRED_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopPostGameplayStep",
    "GameplayStep",
    "GameplayCommitAndReset",
    "TimelineHandleMetaInput",
)
LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")

# Name, virtual-key, scan code, extended-key flag.
RIGHT_KEY = ("Right", 0x27, 0x4D, True)
START_KEY = ("Start", 0x0D, 0x1C, False)
C_KEY = ("C", 0x44, 0x20, False)
B_KEY = ("B", 0x53, 0x1F, False)

ENGINE_COMPAT_PATHS = (
    "src/boot/startup.asm",
    "src/game/collision.asm",
    "src/game/crc32.asm",
    "src/game/encounters.asm",
    "src/game/engine.asm",
    "src/game/engine_core.asm",
    "src/game/engine_layout.asm",
    "src/game/object_pool.asm",
    "src/game/projection.asm",
    "src/gameplay/actor_sim.asm",
    "src/gameplay/gameplay.asm",
    "src/gameplay/gameplay_core.asm",
    "src/gameplay/gameplay_layout.asm",
    "src/gameplay/replay.asm",
    "src/gameplay/vertical_slice.asm",
    "src/hardware/controllers.asm",
    "src/ui/timeline_control.asm",
)
STATE_SCHEMA_PATHS = (
    "src/game/engine_layout.asm",
    "src/game/projection.asm",
    "src/gameplay/gameplay_layout.asm",
)


def _u16(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


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


def parse_replay_timeline(raw: bytes) -> dict[str, Any]:
    """Parse the exact seven-bank/descriptor/timeline 5 KiB allocation."""
    if len(raw) != REPLAY_TIMELINE_BYTES:
        raise EvidenceFailure(
            f"replay/timeline read is {len(raw)} bytes; "
            f"{REPLAY_TIMELINE_BYTES} required"
        )
    banks = tuple(
        raw[index * REPLAY_BANK_BYTES : (index + 1) * REPLAY_BANK_BYTES]
        for index in range(REPLAY_BANK_COUNT)
    )
    descriptor_offset = REPLAY_BANK_REGION_BYTES
    descriptors: list[dict[str, Any]] = []
    for index in range(REPLAY_BANK_COUNT):
        offset = descriptor_offset + index * REPLAY_DESCRIPTOR_BYTES
        descriptors.append(
            {
                "bank": index,
                "length": _u16(raw, offset),
                "crc32_iso_hdlc": _u32(raw, offset + 2),
                "crc32_iso_hdlc_hex": _hex32(_u32(raw, offset + 2)),
                "flags": raw[offset + 6],
                "generation": raw[offset + 7],
                "bank_sha256": _sha256_bytes(banks[index]),
            }
        )
    timeline_offset = (
        REPLAY_BANK_REGION_BYTES + REPLAY_DESCRIPTOR_REGION_BYTES
    )
    timeline_raw = raw[
        timeline_offset : timeline_offset + TIMELINE_CONTROL_BYTES
    ]
    magic = _u32(timeline_raw, 0)
    return {
        "bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "crc32_iso_hdlc": crc32_iso_hdlc(raw),
        "crc32_iso_hdlc_hex": _hex32(crc32_iso_hdlc(raw)),
        "banks": banks,
        "descriptors": descriptors,
        "timeline": {
            "magic": magic,
            "magic_ascii": timeline_raw[:4].decode(
                "ascii", errors="replace"
            ),
            "version": _u16(timeline_raw, 4),
            "bytes": _u16(timeline_raw, 6),
            "echo_count": timeline_raw[8],
            "current_bank": timeline_raw[9],
            "echo_order": list(timeline_raw[10:16]),
            "selected_ordinal": timeline_raw[16],
            "flags": timeline_raw[17],
            "pending_reason": _u16(timeline_raw, 18),
            "attempt_length": _u16(timeline_raw, 20),
            "last_result": _u16(timeline_raw, 22),
        },
    }


def parse_gameplay_control(raw: bytes) -> dict[str, Any]:
    if len(raw) < GAMEPLAY_CONTROL_USED_BYTES:
        raise EvidenceFailure(
            f"gameplay control read is {len(raw)} bytes; "
            f"{GAMEPLAY_CONTROL_USED_BYTES} required"
        )
    return {
        "magic": _u32(raw, 0),
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "version": _u16(raw, 4),
        "bytes": _u16(raw, 6),
        "flags": _u16(raw, 8),
        "result": _u16(raw, 10),
        "door_state": _u16(raw, 12),
        "switch_mask": _u16(raw, 14),
        "current_actor_index": raw[16],
        "echo_actor_count": raw[17],
        "current_input": raw[18],
        "current_pressed": raw[19],
        "current_released": raw[20],
        "current_previous": raw[21],
        "phase": _u16(raw, 22),
        "last_commit_reason": _u16(raw, 24),
        "replacement_flags": _u16(raw, 26),
        "last_bank_crc": _u32(raw, 28),
        "last_bank_crc_hex": _hex32(_u32(raw, 28)),
        "stable_tick": _u16(raw, 32),
        "step_count": _u16(raw, 34),
    }


def parse_actor(raw: bytes, slot: int) -> dict[str, Any]:
    if not 0 <= slot < ACTOR_COUNT:
        raise ValueError(f"actor slot outside 0..{ACTOR_COUNT - 1}: {slot}")
    offset = slot * ACTOR_BYTES
    if len(raw) < offset + ACTOR_BYTES:
        raise EvidenceFailure("ordinary-state actor region is truncated")
    actor = raw[offset : offset + ACTOR_BYTES]
    return {
        "slot": slot,
        "active": actor[0],
        "role": actor[1],
        "flags": _u16(actor, 2),
        "x_16_8": _s32(actor, 4),
        "y_16_8": _s32(actor, 8),
        "vx_16_8": _s32(actor, 12),
        "vy_16_8": _s32(actor, 16),
        "half_width": _u16(actor, 20),
        "half_height": _u16(actor, 22),
        "input": actor[24],
        "previous_input": actor[25],
        "health": _u16(actor, 26),
        "logical_id": _u16(actor, 28),
        "reserved": _u16(actor, 30),
    }


def parse_b01_device(ordinary: bytes) -> dict[str, Any]:
    offset = B01_DEVICE_BASE - ENGINE_ORDINARY_BASE
    if len(ordinary) < offset + OBJECT_BYTES:
        raise EvidenceFailure("ordinary-state B01 device slot is truncated")
    raw = ordinary[offset : offset + OBJECT_BYTES]
    return {
        "active": raw[0],
        "logical_id": raw[1],
        "state": _u16(raw, 2),
        "x_16_8": _s32(raw, 4),
        "y_16_8": _s32(raw, 8),
        "vx_16_8": _s32(raw, 12),
        "vy_16_8": _s32(raw, 16),
        "timer": _u16(raw, 20),
        "occupancy_mask": _u16(raw, 22),
    }


def _public_replay(parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in parsed.items()
        if key != "banks"
    }


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if not key.startswith("_")
    }


def input_summary(payload: bytes) -> dict[str, Any]:
    return {
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "crc32_iso_hdlc": _hex32(crc32_iso_hdlc(payload)),
        "hex": payload.hex().upper(),
        "reserved_bits_zero": all((value & 0x80) == 0 for value in payload),
    }


def make_snapshot(
    *,
    label: str,
    control_raw: bytes,
    gameplay_raw: bytes,
    replay_raw: bytes,
    input_raw: bytes,
    ordinary_raw: bytes | None = None,
    projection_raw: bytes | None = None,
) -> dict[str, Any]:
    replay = parse_replay_timeline(replay_raw)
    snapshot: dict[str, Any] = {
        "label": label,
        "engine": parse_engine_control(control_raw),
        "gameplay": parse_gameplay_control(gameplay_raw),
        "replay_timeline": _public_replay(replay),
        "input": parse_input_state(input_raw),
        "_control_bytes": bytes(control_raw),
        "_gameplay_bytes": bytes(gameplay_raw),
        "_replay_bytes": bytes(replay_raw),
        "_banks": replay["banks"],
        "_input_bytes": bytes(input_raw),
    }
    if ordinary_raw is not None:
        if len(ordinary_raw) != ENGINE_ORDINARY_BYTES:
            raise EvidenceFailure(
                f"ordinary-state read is {len(ordinary_raw)} bytes; "
                f"{ENGINE_ORDINARY_BYTES} required"
            )
        snapshot["ordinary"] = {
            "address": f"{ENGINE_ORDINARY_BASE:08X}",
            "bytes": len(ordinary_raw),
            "sha256": _sha256_bytes(ordinary_raw),
            "echo_actor": parse_actor(ordinary_raw, 0),
            "current_actor": parse_actor(
                ordinary_raw, CURRENT_ACTOR_SLOT
            ),
            "b01_device": parse_b01_device(ordinary_raw),
        }
        snapshot["_ordinary_bytes"] = bytes(ordinary_raw)
    if projection_raw is not None:
        projection = parse_projection(projection_raw)
        computed = crc32_iso_hdlc(projection_raw)
        snapshot["projection"] = {
            "address": f"{ENGINE_PROJECTION_BASE:08X}",
            "bytes": len(projection_raw),
            "sha256": _sha256_bytes(projection_raw),
            "crc32_iso_hdlc": _hex32(computed),
            "header": projection["header"],
            "bounds_valid": projection["bounds_valid"],
            "published_crc32_iso_hdlc": _hex32(
                snapshot["engine"]["state_crc"]
            ),
            "published_crc_matches": (
                computed == snapshot["engine"]["state_crc"]
            ),
        }
        snapshot["_projection_bytes"] = bytes(projection_raw)
    return snapshot


def _source_binding(
    root: Path, relative_paths: Iterable[str], purpose: str
) -> dict[str, Any]:
    hashes: dict[str, str] = {}
    for relative in relative_paths:
        path = root / relative
        if not path.is_file():
            raise EvidenceFailure(
                f"{purpose} compatibility source is missing: {path}"
            )
        hashes[relative] = sha256_file(path)
    canonical = json.dumps(
        hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    digest = hashlib.sha256(canonical).hexdigest()
    identifier = int(digest[:8], 16)
    if identifier == 0:
        raise EvidenceFailure(f"{purpose} compatibility ID resolved to zero")
    return {
        "id": identifier,
        "id_hex": f"{identifier:08X}",
        "derivation": (
            "big-endian first 32 bits of SHA-256 over the canonical "
            "path-to-source-SHA-256 JSON map"
        ),
        "bundle_sha256": digest,
        "sources": hashes,
    }


def derive_compatibility_ids(root: Path) -> tuple[CompatibilityIds, dict[str, Any]]:
    """Derive and document nonzero replay compatibility identifiers."""
    engine = _source_binding(root, ENGINE_COMPAT_PATHS, "engine")
    state = _source_binding(root, STATE_SCHEMA_PATHS, "state schema")
    content_report_path = root / "build/content-report.json"
    if not content_report_path.is_file():
        raise EvidenceFailure(
            f"content report is missing: {content_report_path}"
        )
    try:
        content_report = json.loads(content_report_path.read_text("utf-8"))
        content_sha256 = str(content_report["source_sha256"]).lower()
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise EvidenceFailure(
            f"content report has no valid source_sha256: {error}"
        ) from error
    try:
        content_id = content_id_from_sha256(content_sha256)
    except ValueError as error:
        raise EvidenceFailure(
            f"content report source_sha256 is invalid: {error}"
        ) from error
    if content_id == 0:
        raise EvidenceFailure("content compatibility ID resolved to zero")
    compatibility = CompatibilityIds(
        engine_compat_id=engine["id"],
        state_schema_id=state["id"],
        content_id=content_id,
    )
    documentation = {
        "engine_compat_id": engine,
        "state_schema_id": state,
        "content_id": {
            "id": content_id,
            "id_hex": f"{content_id:016X}",
            "derivation": (
                "big-endian first 64 bits of the compiled campaign "
                "canonical source SHA-256"
            ),
            "source_sha256": content_sha256,
            "content_report_path": "build/content-report.json",
            "content_report_sha256": sha256_file(content_report_path),
        },
    }
    return compatibility, documentation


def completion_signature(
    *,
    stage_id: int,
    attempts_used: int,
    terminal_ticks: int,
    terminal_reason: int,
    active_echoes: int,
    gameplay_result: int,
    state_crc32: int,
    history_crc32: int,
) -> int:
    """Derive an evidence-side signature solely from observed outcome fields."""
    payload = struct.pack(
        ">HHHHHHII",
        stage_id,
        attempts_used,
        terminal_ticks,
        terminal_reason,
        active_echoes,
        gameplay_result,
        state_crc32,
        history_crc32,
    )
    return crc32_iso_hdlc(payload)


def build_canonical_replay(
    *,
    sessions: list[dict[str, Any]],
    compatibility: CompatibilityIds,
) -> dict[str, Any]:
    """Build and round-trip the B01 FULL_HISTORY replay from live RAM."""
    if len(sessions) != 2:
        raise EvidenceFailure("canonical replay requires exactly two sessions")
    source = sessions[0]
    terminal = source["terminal"]
    reset = source["post_manual_commit"]
    initial = source["initial"]
    terminal_replay = parse_replay_timeline(terminal["_replay_bytes"])
    reset_replay = parse_replay_timeline(reset["_replay_bytes"])
    descriptor_6 = reset_replay["descriptors"][6]
    descriptor_0 = terminal_replay["descriptors"][0]
    attempt_0 = reset_replay["banks"][6][: descriptor_6["length"]]
    attempt_1 = terminal_replay["banks"][0][: descriptor_0["length"]]
    if attempt_0 != bytes(source["attempt_0_inputs"]):
        raise EvidenceFailure(
            "session 1 committed bank 6 differs from observed attempt 0 inputs"
        )
    if attempt_1 != bytes(source["attempt_1_inputs"]):
        raise EvidenceFailure(
            "session 1 current bank 0 differs from observed attempt 1 inputs"
        )

    state_crc = int(terminal["engine"]["state_crc"])
    history_crc = crc32_iso_hdlc(attempt_0 + attempt_1)
    signature = completion_signature(
        stage_id=STAGE_ID_B01,
        attempts_used=2,
        terminal_ticks=int(terminal["engine"]["tick"]),
        terminal_reason=int(terminal["engine"]["terminal_reason"]),
        active_echoes=int(
            terminal["replay_timeline"]["timeline"]["echo_count"]
        ),
        gameplay_result=int(terminal["gameplay"]["result"]),
        state_crc32=state_crc,
        history_crc32=history_crc,
    )
    replay = Replay(
        mode=ReplayMode.FULL_HISTORY,
        engine_compat_id=compatibility.engine_compat_id,
        state_schema_id=compatibility.state_schema_id,
        content_id=compatibility.content_id,
        stage_id=STAGE_ID_B01,
        timing_mode=TimingMode.NTSC_60,
        initial_echo_count=0,
        maximum_active_echoes=6,
        anchor_capacity=0,
        initial_persistent_crc32=crc32_iso_hdlc(
            initial["_replay_bytes"]
        ),
        expected_final_state_crc32=state_crc,
        expected_completion_signature=signature,
        attempts=(
            Attempt(
                ordinal=0,
                inputs=attempt_0,
                encoding=InputEncoding.RLE,
                natural_end_reason=NaturalEndReason.MANUAL_ABORT,
                operation=AttemptOperation.APPEND,
                target_slot=NO_TARGET,
            ),
            Attempt(
                ordinal=1,
                inputs=attempt_1,
                encoding=InputEncoding.RLE,
                natural_end_reason=NaturalEndReason.SOLVED,
                operation=AttemptOperation.APPEND,
                target_slot=NO_TARGET,
            ),
        ),
        timeline=(),
        anchors=(),
        checkpoints=(
            Checkpoint(
                attempt=1,
                completed_ticks=int(terminal["engine"]["tick"]),
                state_crc32=state_crc,
            ),
        ),
        outcome=Outcome(
            terminal_reason=NaturalEndReason.SOLVED,
            attempts_used=2,
            terminal_ticks=int(terminal["engine"]["tick"]),
            active_echoes=int(
                terminal["replay_timeline"]["timeline"]["echo_count"]
            ),
            anchors_used=0,
            score=0,
            state_crc32=state_crc,
            completion_signature=signature,
        ),
    )
    validate_replay(replay)
    encoded = encode_replay(replay)
    decoded = decode_replay(encoded, compatibility)
    if decoded != replay:
        raise EvidenceFailure("encoded B01 replay did not decode identically")
    validate_replay(decoded)
    inspected = replay_report(decoded, encoded)
    return {
        "replay": replay,
        "encoded": encoded,
        "decoded": decoded,
        "report": inspected,
        "attempt_0": attempt_0,
        "attempt_1": attempt_1,
        "history_crc32": history_crc,
        "completion_signature": signature,
    }


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def _same_session_payloads(
    sessions: list[dict[str, Any]], key: str, raw_key: str
) -> bool:
    return sessions[0][key][raw_key] == sessions[1][key][raw_key]


def build_runtime_report(
    *,
    rom_sha256: str,
    sessions: list[dict[str, Any]],
    provenance: dict[str, Any],
    compatibility_documentation: dict[str, Any],
    canonical: dict[str, Any],
    replay_path: str,
) -> dict[str, Any]:
    """Purely evaluate collected sessions and construct the public report."""
    if len(sessions) != 2:
        raise EvidenceFailure(
            f"exactly two runtime sessions are required; got {len(sessions)}"
        )
    checks: list[dict[str, Any]] = []
    all_transcript = [
        line
        for session in sessions
        for line in session.get("_transcript_lines", [])
    ]
    write_packets = debugger_write_packets(all_transcript)

    checks.append(
        _check(
            "two_fresh_independent_sessions",
            len({item["metadata"]["process_id"] for item in sessions}) == 2
            and len(
                {
                    item["metadata"]["runtime_copy"][
                        "temporary_directory"
                    ]
                    for item in sessions
                }
            )
            == 2,
            "process IDs "
            f"{[item['metadata']['process_id'] for item in sessions]}",
        )
    )
    checks.append(
        _check(
            "isolated_standard_three_button_real_pad",
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
                for item in sessions
            ),
            "each temporary profile explicitly assigns port 1 gamepad3.1",
        )
    )
    checks.append(
        _check(
            "read_only_debugger_session",
            not write_packets,
            f"RAM/register write packets {write_packets}",
        )
    )
    checks.append(
        _check(
            "symbol_bound_post_gameplay_observation",
            all(
                item["metadata"]["initial_breakpoint_symbol"]
                == "VBlankInterrupt"
                and item["metadata"].get("initial_breakpoint_role")
                == STARTUP_PRIMING_ROLE
                and item["metadata"].get("authoritative_origin")
                == FIRST_RUNNING_VBLANK_ROLE
                and item["metadata"].get(
                    "counters_and_controller_poll_begin"
                )
                == FIRST_RUNNING_VBLANK_ROLE
                and item["metadata"]["step_breakpoint_label"]
                == "MainLoopPostGameplayStep"
                and item["metadata"]["step_breakpoint_source"]
                == "build/recursion94-debug.sym"
                for item in sessions
            ),
            "tick 0 captured at the stage-3 acknowledgment-only priming ISR "
            "entry; counters and controller polling begin on the next "
            "stage-4 RUNNING full VBlank, and all consumed/meta frames were "
            "captured after TimelineHandleMetaInput/GameplayStep",
        )
    )
    checks.append(
        _check(
            "attempt_1_input_staged_before_commit_reset",
            all(
                item["metadata"].get(
                    "attempt_1_input_staging_breakpoint_symbol"
                )
                == "TimelineHandleMetaInput"
                and item["metadata"].get("attempt_1_input_staging_role")
                == "after_controller_poll_before_commit_reset"
                and item["manual_commit_entry"]["engine"]["attempt"] == 0
                and item["manual_commit_entry"]["engine"]["tick"] == 40
                and item["manual_commit_entry"]["engine"]["state"]
                == ENGINE_STATE_ACTIVE
                and item["manual_commit_entry"]["input"]["start_held"]
                == 1
                and item["manual_commit_entry"]["input"]["held"] == INPUT_C
                and item["manual_commit_entry"]["input"]["pressed"] & INPUT_C
                and item["manual_commit_injection"].get(
                    "next_attempt_events_staged_at"
                )
                == "TimelineHandleMetaInput after ControllerPoll"
                for item in sessions
            ),
            "Start+C was already latched at TimelineHandleMetaInput; its "
            "releases and attempt 1's Right hold were staged before the "
            "long commit/reset could admit another VBlank",
        )
    )

    attempt_0_lists = [bytes(item["attempt_0_inputs"]) for item in sessions]
    attempt_1_lists = [bytes(item["attempt_1_inputs"]) for item in sessions]
    checks.append(
        _check(
            "actual_attempt_input_schedules",
            all(value == CANONICAL_ATTEMPT_0 for value in attempt_0_lists)
            and all(value == CANONICAL_ATTEMPT_1 for value in attempt_1_lists),
            "guest INPUT_HELD observations were Right x32 + neutral x8, "
            "then Right x165 + Right+B edge",
        )
    )
    checks.append(
        _check(
            "initial_zero_echo_current_bank_6",
            all(
                item["initial"]["engine"]["magic"] == ENGINE_MAGIC
                and item["initial"]["engine"]["abi_version"]
                == ENGINE_ABI_VERSION
                and item["initial"]["engine"]["attempt"] == 0
                and item["initial"]["engine"]["tick"] == 0
                and item["initial"]["engine"]["state"]
                == ENGINE_STATE_ACTIVE
                and item["initial"]["replay_timeline"]["timeline"]["magic"]
                == TIMELINE_MAGIC
                and item["initial"]["replay_timeline"]["timeline"][
                    "echo_count"
                ]
                == 0
                and item["initial"]["replay_timeline"]["timeline"][
                    "current_bank"
                ]
                == 6
                for item in sessions
            ),
            "both fresh sessions began attempt 0/tick 0 with bank 6 current",
        )
    )
    checks.append(
        _check(
            "first_attempt_exactly_40_steps",
            all(
                item["pre_manual_commit"]["engine"]["tick"] == 40
                and item["pre_manual_commit"]["input"]["held"] == 0
                and item["pre_manual_commit"]["replay_timeline"][
                    "timeline"
                ]["attempt_length"]
                == 40
                for item in sessions
            ),
            "pre-commit tick/attempt lengths "
            f"{[(item['pre_manual_commit']['engine']['tick'], item['pre_manual_commit']['replay_timeline']['timeline']['attempt_length']) for item in sessions]}",
        )
    )
    checks.append(
        _check(
            "staged_start_c_manual_commit",
            all(
                item["manual_commit_injection"]["status"] == "pass"
                and item["manual_commit_injection"]["keys"]
                == ["Enter", "D"]
                and item["manual_commit_injection"]["buttons"]
                == ["Start", "C"]
                and item["manual_commit_injection"][
                    "staged_keydown_order"
                ]
                == ["Start", "C"]
                and item["manual_commit_entry"]["input"]["start_held"] == 1
                and item["manual_commit_entry"]["input"]["held"] == INPUT_C
                and item["manual_commit_entry"]["input"]["pressed"]
                & INPUT_C
                for item in sessions
            ),
            "Start keydown preceded C keydown; the next pad poll latched "
            "Start+C at meta entry and no authoritative step was consumed",
        )
    )

    committed_valid = True
    committed_details: list[tuple[int, str, int, int]] = []
    for item in sessions:
        reset = item["post_manual_commit"]
        replay = parse_replay_timeline(reset["_replay_bytes"])
        descriptor = replay["descriptors"][6]
        bank = replay["banks"][6]
        computed = crc32_iso_hdlc(bank[: descriptor["length"]])
        committed_valid &= (
            descriptor["length"] == 40
            and bank[:40] == CANONICAL_ATTEMPT_0
            and bank[40:] == bytes(REPLAY_BANK_BYTES - 40)
            and computed == descriptor["crc32_iso_hdlc"]
            and computed == CANONICAL_BANK_6_CRC
            and descriptor["flags"]
            & (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            == (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            and descriptor["generation"] == 1
        )
        committed_details.append(
            (
                descriptor["length"],
                _hex32(computed),
                descriptor["flags"],
                descriptor["generation"],
            )
        )
    checks.append(
        _check(
            "bank_6_partial_length_crc_and_zero_tail",
            committed_valid,
            f"length/independent CRC/flags/generation {committed_details}",
        )
    )

    reset_valid = True
    for item in sessions:
        reset = item["post_manual_commit"]
        timeline = reset["replay_timeline"]["timeline"]
        gameplay = reset["gameplay"]
        ordinary = reset["ordinary"]
        descriptor_0 = reset["replay_timeline"]["descriptors"][0]
        reset_valid &= (
            reset["engine"]["attempt"] == 1
            and reset["engine"]["tick"] == 0
            and reset["engine"]["state"] == ENGINE_STATE_ACTIVE
            and reset["engine"]["terminal_reason"] == ENGINE_TERMINAL_NONE
            and timeline["echo_count"] == 1
            and timeline["echo_order"] == [6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
            and timeline["current_bank"] == 0
            and timeline["attempt_length"] == 0
            and descriptor_0["length"] == 0
            and descriptor_0["flags"] & REPLAY_FLAG_CURRENT
            and gameplay["last_commit_reason"] == GAMEPLAY_COMMIT_MANUAL
            and gameplay["echo_actor_count"] == 1
            and gameplay["current_actor_index"] == CURRENT_ACTOR_SLOT
            and ordinary["echo_actor"]["active"] == 1
            and ordinary["echo_actor"]["logical_id"] == 6
            and ordinary["echo_actor"]["x_16_8"] == 32 << 8
            and ordinary["current_actor"]["active"] == 1
            and ordinary["current_actor"]["logical_id"] == 0
            and ordinary["current_actor"]["x_16_8"] == 32 << 8
            and ordinary["b01_device"]["state"] == 0
            and ordinary["b01_device"]["occupancy_mask"] == 0
        )
    checks.append(
        _check(
            "real_commit_reset_rotation_and_reinstantiation",
            reset_valid,
            "bank 6 became echo 1, bank 0 became current, attempt 1/tick 0 "
            "and canonical actors/device were rebuilt",
        )
    )
    checks.append(
        _check(
            "reset_projection_is_live_and_independently_checked",
            all(
                item["post_manual_commit"]["projection"][
                    "published_crc_matches"
                ]
                and item["post_manual_commit"]["projection"]["bounds_valid"]
                and item["post_manual_commit"]["projection"]["header"][
                    "attempt"
                ]
                == 1
                and item["post_manual_commit"]["projection"]["header"]["tick"]
                == 0
                for item in sessions
            ),
            "post-reset projection CRCs "
            f"{[item['post_manual_commit']['projection']['crc32_iso_hdlc'] for item in sessions]}",
        )
    )

    terminal_bank_valid = True
    for item in sessions:
        replay = parse_replay_timeline(item["terminal"]["_replay_bytes"])
        descriptor = replay["descriptors"][0]
        terminal_bank_valid &= (
            descriptor["length"] == 166
            and descriptor["flags"] == REPLAY_FLAG_CURRENT
            and descriptor["generation"] == 1
            and replay["banks"][0][:166] == CANONICAL_ATTEMPT_1
            and replay["banks"][0][166:]
            == bytes(REPLAY_BANK_BYTES - 166)
            and replay["banks"][0][:166]
            == bytes(item["attempt_1_inputs"])
        )
    checks.append(
        _check(
            "current_bank_0_records_actual_166_inputs",
            terminal_bank_valid,
            "bank 0 length is 166 with Right x165 + Right+B and a zero tail",
        )
    )

    checks.append(
        _check(
            "program_stable_exactly_tick_166",
            all(
                item["terminal"]["engine"]["attempt"] == 1
                and item["terminal"]["engine"]["tick"] == 166
                and item["terminal"]["engine"]["state"]
                == ENGINE_STATE_TERMINAL
                and item["terminal"]["engine"]["terminal_reason"]
                == GAMEPLAY_TERMINAL_PROGRAM_STABLE
                and item["terminal"]["engine"]["last_error"] == 0
                and item["terminal"]["engine"]["overflow_flags"] == 0
                and item["terminal"]["gameplay"]["result"]
                == GAMEPLAY_RESULT_PROGRAM_STABLE
                and item["terminal"]["gameplay"]["stable_tick"] == 166
                and item["terminal"]["gameplay"]["step_count"] == 166
                for item in sessions
            ),
            "terminal tick/state/reason "
            f"{[(item['terminal']['engine']['tick'], item['terminal']['engine']['state'], item['terminal']['engine']['terminal_reason']) for item in sessions]}",
        )
    )
    checks.append(
        _check(
            "terminal_b_edge_is_observed_not_inferred",
            all(
                item["terminal"]["input"]["held"]
                == INPUT_RIGHT | INPUT_B
                and item["terminal"]["input"]["pressed"] & INPUT_B
                and item["terminal"]["gameplay"]["current_input"]
                == INPUT_RIGHT | INPUT_B
                and item["terminal"]["gameplay"]["current_pressed"]
                == INPUT_B
                and item["terminal"]["gameplay"]["current_previous"]
                == INPUT_RIGHT
                for item in sessions
            ),
            "live input/gameplay ABIs both show Right+B with a B edge",
        )
    )
    checks.append(
        _check(
            "echo_switch_causality_at_x_80",
            all(
                item["terminal"]["ordinary"]["echo_actor"]["x_16_8"]
                == 80 << 8
                and item["terminal"]["ordinary"]["echo_actor"]["input"] == 0
                and item["terminal"]["ordinary"]["echo_actor"]["logical_id"]
                == 6
                and item["terminal"]["ordinary"]["b01_device"][
                    "occupancy_mask"
                ]
                == 0x0001
                and item["terminal"]["gameplay"]["switch_mask"] == 0x0001
                for item in sessions
            ),
            "physical echo slot 0 alone occupies the switch at X=80",
        )
    )
    checks.append(
        _check(
            "current_actor_at_terminal_and_not_switch",
            all(
                280 << 8
                <= item["terminal"]["ordinary"]["current_actor"]["x_16_8"]
                < 296 << 8
                and item["terminal"]["ordinary"]["current_actor"][
                    "logical_id"
                ]
                == 0
                and not (
                    item["terminal"]["ordinary"]["b01_device"][
                        "occupancy_mask"
                    ]
                    & (1 << CURRENT_ACTOR_SLOT)
                )
                for item in sessions
            ),
            "current actor occupies the east terminal and bit 6 is absent "
            "from switch occupancy",
        )
    )
    checks.append(
        _check(
            "switch_door_and_device_program_stable",
            all(
                item["terminal"]["ordinary"]["b01_device"]["state"] == 7
                and item["terminal"]["gameplay"]["door_state"] == 7
                and item["terminal"]["gameplay"]["flags"]
                & (
                    GAMEPLAY_FLAG_INITIALIZED
                    | GAMEPLAY_FLAG_PROGRAM_STABLE
                    | GAMEPLAY_FLAG_DOOR_OPEN
                    | GAMEPLAY_FLAG_SWITCH_HELD
                )
                == (
                    GAMEPLAY_FLAG_INITIALIZED
                    | GAMEPLAY_FLAG_PROGRAM_STABLE
                    | GAMEPLAY_FLAG_DOOR_OPEN
                    | GAMEPLAY_FLAG_SWITCH_HELD
                )
                and item["terminal"]["replay_timeline"]["timeline"]["flags"]
                & (
                    TIMELINE_FLAG_INITIALIZED
                    | TIMELINE_FLAG_PROGRAM_STABLE
                )
                == (
                    TIMELINE_FLAG_INITIALIZED
                    | TIMELINE_FLAG_PROGRAM_STABLE
                )
                for item in sessions
            ),
            "device state, gameplay mirror, and timeline stable flag agree",
        )
    )
    projection_valid = all(
        item["terminal"]["projection"]["published_crc_matches"]
        and item["terminal"]["projection"]["bounds_valid"]
        and item["terminal"]["projection"]["header"]["magic"]
        == PROJECTION_MAGIC
        and item["terminal"]["projection"]["header"]["tick"] == 166
        and item["terminal"]["projection"]["header"]["attempt"] == 1
        and int(
            item["terminal"]["projection"]["crc32_iso_hdlc"], 16
        )
        == GOLDEN_B01_PROJECTION_CRC
        for item in sessions
    )
    checks.append(
        _check(
            "independent_terminal_projection_crc_8762e745",
            projection_valid,
            "bitwise host CRCs "
            f"{[item['terminal']['projection']['crc32_iso_hdlc'] for item in sessions]}",
        )
    )

    deterministic_raw_keys = (
        ("initial", "_replay_bytes"),
        ("manual_commit_entry", "_replay_bytes"),
        ("manual_commit_entry", "_control_bytes"),
        ("manual_commit_entry", "_gameplay_bytes"),
        ("post_manual_commit", "_replay_bytes"),
        ("post_manual_commit", "_ordinary_bytes"),
        ("post_manual_commit", "_projection_bytes"),
        ("terminal", "_replay_bytes"),
        ("terminal", "_ordinary_bytes"),
        ("terminal", "_projection_bytes"),
        ("terminal", "_control_bytes"),
        ("terminal", "_gameplay_bytes"),
    )
    deterministic_equal = (
        attempt_0_lists[0] == attempt_0_lists[1]
        and attempt_1_lists[0] == attempt_1_lists[1]
        and all(
            _same_session_payloads(sessions, key, raw_key)
            for key, raw_key in deterministic_raw_keys
        )
    )
    checks.append(
        _check(
            "two_runs_are_byte_identical",
            deterministic_equal,
            "input histories, replay RAM, ordinary state, projections, and "
            "terminal control ABIs match byte-for-byte",
        )
    )

    replay_object: Replay = canonical["replay"]
    replay_encoded: bytes = canonical["encoded"]
    replay_decoded: Replay = canonical["decoded"]
    checks.append(
        _check(
            "full_history_replay_from_actual_ram",
            replay_object.mode is ReplayMode.FULL_HISTORY
            and replay_object.attempts[0].inputs == CANONICAL_ATTEMPT_0
            and replay_object.attempts[1].inputs == CANONICAL_ATTEMPT_1
            and replay_object.outcome.active_echoes == 1
            and replay_object.outcome.terminal_ticks == 166
            and replay_object.outcome.state_crc32
            == GOLDEN_B01_PROJECTION_CRC,
            "R94R contains both live RAM streams and the observed one-echo "
            "PROGRAM_STABLE outcome",
        )
    )
    checks.append(
        _check(
            "r94r_validate_encode_decode_round_trip",
            replay_decoded == replay_object
            and encode_replay(replay_decoded) == replay_encoded,
            f"encoded bytes {len(replay_encoded)}, "
            f"SHA-256 {_sha256_bytes(replay_encoded)}",
        )
    )

    provenance_hashes = [
        rom_sha256,
        provenance.get("emulator", {}).get("sha256"),
        provenance.get("symbol_map", {}).get("sha256"),
        provenance.get("default_config", {}).get("source_sha256"),
        provenance.get("transcript", {}).get("sha256"),
        compatibility_documentation.get("engine_compat_id", {}).get(
            "bundle_sha256"
        ),
        compatibility_documentation.get("state_schema_id", {}).get(
            "bundle_sha256"
        ),
        *[
            item["metadata"]["controller_profile"].get("sha256")
            for item in sessions
        ],
        *[
            item["metadata"].get("transcript_sha256")
            for item in sessions
        ],
    ]
    checks.append(
        _check(
            "hash_bound_dynamic_provenance",
            all(_valid_sha256(value) for value in provenance_hashes),
            "ROM/symbol/config/emulator/source manifests/session "
            "transcripts are SHA-256 bound; no ROM hash is hard-coded",
        )
    )

    passed = bool(checks) and all(
        item["status"] == "pass" for item in checks
    )
    public_sessions = []
    for item in sessions:
        public_sessions.append(
            {
                "session_id": item["session_id"],
                "metadata": item["metadata"],
                "attempt_0_inputs": input_summary(
                    bytes(item["attempt_0_inputs"])
                ),
                "attempt_1_inputs": input_summary(
                    bytes(item["attempt_1_inputs"])
                ),
                "initial": _public_snapshot(item["initial"]),
                "pre_manual_commit": _public_snapshot(
                    item["pre_manual_commit"]
                ),
                "manual_commit_injection": item[
                    "manual_commit_injection"
                ],
                "manual_commit_entry": _public_snapshot(
                    item["manual_commit_entry"]
                ),
                "post_manual_commit": _public_snapshot(
                    item["post_manual_commit"]
                ),
                "terminal": _public_snapshot(item["terminal"]),
            }
        )

    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "sessions": public_sessions,
        "canonical_replay": {
            "path": replay_path,
            "bytes": len(replay_encoded),
            "sha256": _sha256_bytes(replay_encoded),
            "mode": "FULL_HISTORY",
            "source": (
                "attempt bytes copied from live guest replay banks after "
                "two byte-identical emulator runs"
            ),
            "compatibility_ids": compatibility_documentation,
            "initial_persistent_crc32": _hex32(
                replay_object.initial_persistent_crc32
            ),
            "history_crc32": _hex32(canonical["history_crc32"]),
            "completion_signature": {
                "value_hex": _hex32(
                    canonical["completion_signature"]
                ),
                "derivation": (
                    "CRC-32/ISO-HDLC of big-endian stage, attempt count, "
                    "terminal ticks/reason, active echoes, gameplay result, "
                    "state CRC, and concatenated live-input history CRC"
                ),
                "runtime_assertion_source": "observed terminal RAM fields",
            },
            "decoded_validation": canonical["report"],
            "classification": (
                "canonical B01 acceptance replay verified against two live "
                "real-pad simulations; file outcome fields remain assertions"
            ),
        },
        "provenance": provenance,
        "debugger_write_packets": write_packets,
        "evidence_limits": [
            "BlastEm GDB is used only for breakpoint control and memory reads.",
            "Keyboard messages exercise the emulator's configured standard "
            "three-button pad path; they are not guest-memory injection.",
            "This report proves deterministic B01 gameplay/replay behavior; "
            "it makes no visual, audio, renderer, performance, or release-ROM "
            "equivalence claim.",
            "The completion signature is a documented evidence-side digest "
            "of observed outcome fields; the runtime-published canonical "
            "state assertion is the independent projection CRC.",
        ],
    }


def verify_input_bindings(default_config: Path) -> dict[str, Any]:
    payload = default_config.read_bytes()
    text = payload.decode("utf-8", errors="replace")
    expected = {
        "right": "gamepads.1.right",
        "enter": "gamepads.1.start",
        "d": "gamepads.1.c",
        "s": "gamepads.1.b",
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
        "source_sha256": _sha256_bytes(payload),
        "required_keys": ["Right", "Enter", "D", "S"],
        "required_buttons": ["Right", "Start", "C", "B"],
    }


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


def _press_key(
    window: int,
    key: tuple[str, int, int, bool],
    pressed_keys: list[tuple[str, int, int, bool]],
    events: list[str],
) -> None:
    if key in pressed_keys:
        raise EvidenceFailure(f"{key[0]} is already pressed")
    _post_key(window, key, pressed=True)
    pressed_keys.append(key)
    events.append(f"{key[0]}:down")


def _release_key(
    window: int,
    key: tuple[str, int, int, bool],
    pressed_keys: list[tuple[str, int, int, bool]],
    events: list[str],
) -> None:
    if key not in pressed_keys:
        raise EvidenceFailure(f"{key[0]} is not pressed")
    _post_key(window, key, pressed=False)
    pressed_keys.remove(key)
    events.append(f"{key[0]}:up")


def _capture_live_snapshot(
    remote: GdbRemote,
    *,
    label: str,
    full_state: bool,
) -> dict[str, Any]:
    ordinary = (
        _read_region(remote, ENGINE_ORDINARY_BASE, ENGINE_ORDINARY_BYTES)
        if full_state
        else None
    )
    projection = (
        _read_region(
            remote, ENGINE_PROJECTION_BASE, ENGINE_PROJECTION_BYTES
        )
        if full_state
        else None
    )
    return make_snapshot(
        label=label,
        control_raw=_read_region(
            remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_REGION_BYTES
        ),
        gameplay_raw=_read_region(
            remote, GAMEPLAY_CONTROL_BASE, GAMEPLAY_CONTROL_USED_BYTES
        ),
        replay_raw=_read_region(
            remote, REPLAY_BANK_BASE, REPLAY_TIMELINE_BYTES
        ),
        input_raw=remote.read_memory(
            INPUT_STATE_BASE, INPUT_STATE_BYTES
        ),
        ordinary_raw=ordinary,
        projection_raw=projection,
    )


def _observe_step(
    remote: GdbRemote,
    *,
    expected_tick: int,
    expected_attempt: int,
    session_id: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    require_stop(remote.request("c"))
    control = parse_engine_control(
        remote.read_memory(
            ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
        )
    )
    input_state = parse_input_state(
        remote.read_memory(INPUT_STATE_BASE, INPUT_STATE_BYTES)
    )
    if (
        control["tick"] != expected_tick
        or control["attempt"] != expected_attempt
    ):
        raise EvidenceFailure(
            f"{session_id} observed attempt {control['attempt']} tick "
            f"{control['tick']}; attempt {expected_attempt} tick "
            f"{expected_tick} required"
        )
    return control, input_state


def _run_live_session(
    *,
    session_id: str,
    root: Path,
    rom: Path,
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
    pressed_keys: list[tuple[str, int, int, bool]] = []
    input_events: list[str] = []
    session: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix=f"recursion94-m4-{session_id}-"
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
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            initial = _capture_live_snapshot(
                remote, label="initial_attempt_0_tick_0", full_state=True
            )
            if (
                initial["engine"]["attempt"] != 0
                or initial["engine"]["tick"] != 0
            ):
                raise EvidenceFailure(
                    f"{session_id} initial attempt/tick is "
                    f"{initial['engine']['attempt']}/"
                    f"{initial['engine']['tick']}; 0/0 required"
                )
            window = find_process_window(process.pid, 20.0)

            # This stop is the stage-3 acknowledgment-only priming ISR. Queue
            # Right now so the following stage-4 RUNNING full VBlank performs
            # the first controller poll and publishes the first gameplay token.
            _press_key(
                window, RIGHT_KEY, pressed_keys, input_events
            )
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} VBlank breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-gameplay breakpoint insertion failed"
                )

            attempt_0_inputs = bytearray()
            for expected_tick in range(1, 41):
                _control, input_state = _observe_step(
                    remote,
                    expected_tick=expected_tick,
                    expected_attempt=0,
                    session_id=session_id,
                )
                attempt_0_inputs.append(input_state["held"])
                if expected_tick == 32:
                    _release_key(
                        window, RIGHT_KEY, pressed_keys, input_events
                    )
            pre_manual_commit = _capture_live_snapshot(
                remote,
                label="attempt_0_tick_40_pre_manual_commit",
                full_state=False,
            )

            # Stage the chord in an explicit order while the CPU is stopped.
            # Both events are consumed by the next controller poll, so the
            # meta frame commits without recording an extra gameplay byte.
            _press_key(
                window, START_KEY, pressed_keys, input_events
            )
            _press_key(window, C_KEY, pressed_keys, input_events)

            # Stop at the meta routine after ControllerPoll has latched
            # Start+C but before the long commit/reset begins. Reset exceeds
            # one frame on the frozen ROM, so it may admit a VBlank and queue
            # attempt 1's first token before returning to the main-loop
            # post-gameplay site. Stage C/Start releases and the new Right
            # hold here so any such VBlank polls the correct next-attempt
            # input without changing the already-latched meta chord.
            meta_input = symbols["TimelineHandleMetaInput"]
            if remote.request(f"z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-gameplay breakpoint removal failed "
                    "before manual-commit entry"
                )
            if remote.request(f"Z0,{meta_input:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} meta-input breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            manual_commit_entry = _capture_live_snapshot(
                remote,
                label="attempt_0_tick_40_start_c_meta_entry",
                full_state=False,
            )
            _release_key(window, C_KEY, pressed_keys, input_events)
            _release_key(
                window, START_KEY, pressed_keys, input_events
            )
            _press_key(
                window, RIGHT_KEY, pressed_keys, input_events
            )
            if remote.request(f"z0,{meta_input:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} meta-input breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    f"{session_id} post-gameplay breakpoint reinsertion "
                    "failed"
                )
            require_stop(remote.request("c"))
            post_manual_commit = _capture_live_snapshot(
                remote,
                label="post_start_c_attempt_1_tick_0",
                full_state=True,
            )
            if (
                post_manual_commit["engine"]["attempt"] != 1
                or post_manual_commit["engine"]["tick"] != 0
            ):
                raise EvidenceFailure(
                    f"{session_id} Start+C did not commit/reset to attempt "
                    "1 tick 0"
                )
            manual_commit_injection = {
                "status": "pass",
                "method": (
                    "targeted PostMessageW key events; chord observed at "
                    "TimelineHandleMetaInput entry before commit/reset"
                ),
                "keys": ["Enter", "D"],
                "buttons": ["Start", "C"],
                "staged_keydown_order": ["Start", "C"],
                "next_attempt_events_staged_at": (
                    "TimelineHandleMetaInput after ControllerPoll"
                ),
                "authoritative_frames_consumed": 0,
                "guest_ram_or_pc_writes": False,
            }

            attempt_1_inputs = bytearray()
            for expected_tick in range(1, 166):
                _control, input_state = _observe_step(
                    remote,
                    expected_tick=expected_tick,
                    expected_attempt=1,
                    session_id=session_id,
                )
                attempt_1_inputs.append(input_state["held"])
            _press_key(window, B_KEY, pressed_keys, input_events)
            terminal_control, terminal_input = _observe_step(
                remote,
                expected_tick=166,
                expected_attempt=1,
                session_id=session_id,
            )
            attempt_1_inputs.append(terminal_input["held"])
            if (
                terminal_control["state"] != ENGINE_STATE_TERMINAL
                or terminal_control["terminal_reason"]
                != GAMEPLAY_TERMINAL_PROGRAM_STABLE
            ):
                raise EvidenceFailure(
                    f"{session_id} did not reach PROGRAM_STABLE at tick 166"
                )
            terminal = _capture_live_snapshot(
                remote,
                label="program_stable_attempt_1_tick_166",
                full_state=True,
            )
            _release_key(window, B_KEY, pressed_keys, input_events)
            _release_key(
                window, RIGHT_KEY, pressed_keys, input_events
            )
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
                    "counters_and_controller_poll_begin": (
                        FIRST_RUNNING_VBLANK_ROLE
                    ),
                    "step_breakpoint_label": "MainLoopPostGameplayStep",
                    "step_breakpoint": f"{post_step:08X}",
                    "step_breakpoint_source": (
                        "build/recursion94-debug.sym"
                    ),
                    "attempt_1_input_staging_breakpoint_symbol": (
                        "TimelineHandleMetaInput"
                    ),
                    "attempt_1_input_staging_role": (
                        "after_controller_poll_before_commit_reset"
                    ),
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "input_events": input_events,
                    "transcript_sha256": _sha256_bytes(
                        (
                            "\n".join(session_lines)
                            + ("\n" if session_lines else "")
                        ).encode("utf-8")
                    ),
                },
                "attempt_0_inputs": list(attempt_0_inputs),
                "attempt_1_inputs": list(attempt_1_inputs),
                "initial": initial,
                "pre_manual_commit": pre_manual_commit,
                "manual_commit_injection": manual_commit_injection,
                "manual_commit_entry": manual_commit_entry,
                "post_manual_commit": post_manual_commit,
                "terminal": terminal,
                "_transcript_lines": session_lines,
            }
        finally:
            if window is not None:
                for key in reversed(pressed_keys):
                    try:
                        _post_key(window, key, pressed=False)
                        input_events.append(f"{key[0]}:emergency-up")
                    except Exception as error:
                        transcript.append(
                            "! emergency key release failed: "
                            f"{type(error).__name__}: {error}"
                        )
            pressed_keys.clear()
            shutdown_emulator(remote, process, transcript)
            transcript.append(f"# {session_id} end")
    if session is None:
        raise EvidenceFailure(f"{session_id} produced no evidence")
    # Include cleanup packets and the explicit end marker in the session hash.
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
    error: Exception,
    provenance: dict[str, Any],
    transcript: list[str],
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "rom_sha256": rom_sha256,
        "checks": [_check("m4_runtime_evidence_session", False, detail)],
        "error": detail,
        "provenance": provenance,
        "debugger_write_packets": debugger_write_packets(transcript),
        "canonical_replay": {
            "status": "not-written",
            "reason": "runtime evidence did not pass",
        },
    }


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m4-runtime.json"
    )
    replay_path = (
        args.replay.resolve()
        if args.replay
        else root / "artifacts/replays/B01.r94r"
    )
    transcript_path = (
        args.trace.resolve()
        if args.trace
        else root / "artifacts/traces/m4-runtime-gdb-transcript.log"
    )
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    sessions: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    rom_sha256 = ""
    report: dict[str, Any]

    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M4 runtime evidence requires native Windows BlastEm"
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
        bindings = verify_input_bindings(default_config)
        if bindings["status"] != "pass":
            raise EvidenceFailure(
                "pinned default.cfg does not bind the B01 probe keys"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"Debug symbol map is missing required symbols {missing}"
            )
        compatibility, compatibility_documentation = (
            derive_compatibility_ids(root)
        )
        provenance = {
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
                "hash_policy": (
                    "resolved at invocation and verified unchanged after "
                    "capture"
                ),
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
                    name: f"{symbols[name]:08X}"
                    for name in REQUIRED_SYMBOLS
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
                        timeout_seconds=args.timeout_seconds,
                        transcript=transcript,
                    )
                )

        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        provenance["transcript"] = {
            "path": transcript_path.relative_to(root).as_posix(),
            "sha256": sha256_file(transcript_path),
            "commands": sum(
                line.startswith("> ") for line in transcript
            ),
        }

        if sha256_file(rom) != rom_sha256:
            raise EvidenceFailure("Debug ROM changed during M4 capture")
        if sha256_file(emulator) != provenance["emulator"]["sha256"]:
            raise EvidenceFailure("pinned BlastEm changed during M4 capture")
        if (
            sha256_file(symbol_path)
            != provenance["symbol_map"]["sha256"]
        ):
            raise EvidenceFailure(
                "Debug symbol map changed during M4 capture"
            )
        if (
            sha256_file(default_config)
            != provenance["default_config"]["source_sha256"]
        ):
            raise EvidenceFailure(
                "pinned default.cfg changed during M4 capture"
            )
        compatibility_after, documentation_after = derive_compatibility_ids(
            root
        )
        if (
            compatibility_after != compatibility
            or documentation_after != compatibility_documentation
        ):
            raise EvidenceFailure(
                "authoritative compatibility sources changed during capture"
            )

        canonical = build_canonical_replay(
            sessions=sessions, compatibility=compatibility
        )
        relative_replay = replay_path.relative_to(root).as_posix()
        report = build_runtime_report(
            rom_sha256=rom_sha256,
            sessions=sessions,
            provenance=provenance,
            compatibility_documentation=compatibility_documentation,
            canonical=canonical,
            replay_path=relative_replay,
        )
        if report["status"] != "pass":
            raise EvidenceFailure(
                "one or more M4 runtime acceptance checks failed"
            )
        _write_bytes_atomic(replay_path, canonical["encoded"])
        if sha256_file(replay_path) != report["canonical_replay"]["sha256"]:
            raise EvidenceFailure(
                "written canonical replay hash does not match report"
            )
    except Exception as error:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        provenance["transcript"] = {
            "path": (
                transcript_path.relative_to(root).as_posix()
                if transcript_path.is_relative_to(root)
                else transcript_path.as_posix()
            ),
            "sha256": sha256_file(transcript_path),
            "commands": sum(
                line.startswith("> ") for line in transcript
            ),
        }
        report = _failure_report(
            rom_sha256=rom_sha256,
            error=error,
            provenance=provenance,
            transcript=transcript,
        )

    write_json(report_path, report)
    if report["status"] != "pass":
        print(
            f"M4 runtime evidence: FAIL ({report.get('error', 'checks')})",
            file=sys.stderr,
        )
        return 1
    print(
        "M4 runtime evidence: PASS; "
        f"ROM {report['rom_sha256']}; "
        f"replay {report['canonical_replay']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
