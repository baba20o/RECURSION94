#!/usr/bin/env python3
"""Collect strict Milestone 4 echo-layer audio evidence from the real ROM.

The live collector uses BlastEm's window/controller path for every gameplay
input and its VGM logger for chip-command output.  GDB is restricted to
breakpoint control and reads.  A single capture spans the zero-echo portion of
the canonical first attempt, its real Start+C commit, and multiple motif
boundaries with echo layer one active.
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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    DEBUG_BASE,
    DEBUG_BLOCK_BYTES,
    EvidenceFailure,
    GdbRemote,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    parse_debug_block,
    parse_vasm_symbols,
    parse_z80_status,
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
    ENGINE_CONTROL_BASE,
    ENGINE_CONTROL_USED_BYTES,
    ENGINE_MAGIC,
    ENGINE_STATE_ACTIVE,
    INPUT_STATE_BASE,
    INPUT_STATE_BYTES,
    crc32_iso_hdlc,
    debugger_write_packets,
    parse_engine_control,
    parse_input_state,
)


REPORT_SCHEMA = "r94-m4-audio-evidence-v2"
EVIDENCE_TYPE = "blastem_gdb_vgm_m4_echo_layer_transition"
STARTUP_PRIMING_ROLE = "stage_3_ack_only_priming_isr"
FIRST_RUNNING_VBLANK_ROLE = "next_stage_4_running_full_vblank"
VGM_SAMPLE_RATE = 44_100
NTSC_FRAME_SAMPLES = 735
CANONICAL_ATTEMPT_FRAMES = 40
CANONICAL_RIGHT_FRAMES = 32
POST_COMMIT_CAPTURE_FRAMES = 45
ACQUISITION_TIMING_TOLERANCE_FRAMES = 2
MIN_CAPTURE_SAMPLES = 70 * NTSC_FRAME_SAMPLES
MAX_CAPTURE_SAMPLES = 120 * NTSC_FRAME_SAMPLES
MAX_LAYER_PULSE_SAMPLES = 3 * NTSC_FRAME_SAMPLES
MAX_PSG_PAIR_GAP_SAMPLES = 32

INPUT_RIGHT = 0x08
INPUT_C = 0x40
CANONICAL_ATTEMPT_0 = bytes((INPUT_RIGHT,)) * 32 + bytes(8)

REPLAY_BANK_BYTES = 720
REPLAY_BANK_6 = 0x00FF0000 + 6 * REPLAY_BANK_BYTES
REPLAY_DESCRIPTOR_6 = 0x00FF13B0 + 6 * 8
REPLAY_DESCRIPTOR_BYTES = 8
TIMELINE_CONTROL_BASE = 0x00FF13E8
TIMELINE_CONTROL_BYTES = 24
GAMEPLAY_CONTROL_BASE = 0x00FF1500
GAMEPLAY_CONTROL_BYTES = 0x24
AUDIO_SHADOW_BASE = 0x00FF9900
AUDIO_SHADOW_EXTENDED_BYTES = 18

TIMELINE_MAGIC = 0x52393454
GAMEPLAY_MAGIC = 0x52393447
REPLAY_FLAG_VALID = 0x01
REPLAY_FLAG_ECHO = 0x02
REPLAY_FLAG_CURRENT = 0x04
TIMELINE_FLAG_INITIALIZED = 0x01
GAMEPLAY_FLAG_INITIALIZED = 0x0001
GAMEPLAY_COMMIT_MANUAL = 2
GAMEPLAY_RESULT_OK = 0
Z80_STATE_PLAYING = 2
Z80_REQUIRED_FLAGS = 0x07
Z80_FAULT_FLAGS = 0x30
Z80_QUEUE_CAPACITY = 16
BASE_LAYER_MASK = 0x01
FIRST_ECHO_LAYER_MASK = 0x03
PSG_NOISE_SHORT = 0xE4
PSG_BOOT_SFX_VOLUME = 0xFA
PSG_LAYER_PERCUSSION_VOLUME = 0xF8
PSG_MUTE_NOISE = 0xFF

LAUNCH_FLAGS = ("-D", "-g", "-m", "gen", "-r", "U")
REQUIRED_SYMBOLS = (
    "VBlankInterrupt",
    "MainLoopPostGameplayStep",
    "TimelineHandleMetaInput",
    "AudioPostEchoLayersIfChanged",
)
REQUIRED_CURRENT_CHECKS = frozenset(
    {
        "attempt_1_input_staged_before_commit_reset",
    }
)
SOURCE_BINDING_PATHS = (
    "src/audio/z80_driver.asm",
    "src/boot/startup.asm",
    "src/gameplay/gameplay_core.asm",
    "src/gameplay/gameplay_layout.asm",
    "src/gameplay/replay.asm",
    "src/hardware/audio_bridge.asm",
    "src/hardware/controllers.asm",
    "src/hardware/interrupts.asm",
    "src/hardware/memory_map.asm",
    "src/ui/timeline_control.asm",
)

# Name, virtual key, scan code, extended-key flag.
RIGHT_KEY = ("Right", 0x27, 0x4D, True)
START_KEY = ("Start", 0x0D, 0x1C, False)
C_KEY = ("C", 0x44, 0x20, False)
VGM_KEY = ("M", 0x4D, 0x32, False)


class VgmError(ValueError):
    """Malformed or unsupported VGM input."""


def _u16be(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 2], "big")


def _u32be(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "big")


def _u32le(raw: bytes, offset: int) -> int:
    return int.from_bytes(raw[offset : offset + 4], "little")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _hex32(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "check": name,
        "status": "pass" if passed else "fail",
        "detail": detail,
    }


def _valid_sha256(value: Any) -> bool:
    return bool(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    )


def source_binding(root: Path) -> dict[str, Any]:
    """Hash the exact human-authored sources relevant to this evidence."""
    sources: dict[str, str] = {}
    for relative in SOURCE_BINDING_PATHS:
        path = root / relative
        if not path.is_file():
            raise EvidenceFailure(f"audio evidence source is missing: {path}")
        sources[relative] = sha256_file(path)
    canonical = json.dumps(
        sources, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "algorithm": "SHA-256",
        "canonical_form": "sorted compact path-to-sha256 JSON",
        "bundle_sha256": _sha256_bytes(canonical),
        "sources": sources,
    }


def parse_gameplay_control(raw: bytes) -> dict[str, Any]:
    if len(raw) != GAMEPLAY_CONTROL_BYTES:
        raise ValueError(
            f"gameplay control must be {GAMEPLAY_CONTROL_BYTES} bytes"
        )
    return {
        "raw_sha256": _sha256_bytes(raw),
        "magic": _u32be(raw, 0),
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "version": _u16be(raw, 4),
        "declared_bytes": _u16be(raw, 6),
        "flags": _u16be(raw, 8),
        "result": _u16be(raw, 10),
        "door_state": _u16be(raw, 12),
        "switch_mask": _u16be(raw, 14),
        "current_actor_index": raw[16],
        "echo_actor_count": raw[17],
        "current_input": raw[18],
        "current_pressed": raw[19],
        "current_released": raw[20],
        "current_previous": raw[21],
        "phase": _u16be(raw, 22),
        "last_commit_reason": _u16be(raw, 24),
        "replacement_flags": _u16be(raw, 26),
        "last_bank_crc": _u32be(raw, 28),
        "last_bank_crc_hex": _hex32(_u32be(raw, 28)),
        "stable_tick": _u16be(raw, 32),
        "step_count": _u16be(raw, 34),
    }


def parse_timeline(raw: bytes) -> dict[str, Any]:
    if len(raw) != TIMELINE_CONTROL_BYTES:
        raise ValueError(
            f"timeline control must be {TIMELINE_CONTROL_BYTES} bytes"
        )
    return {
        "raw_sha256": _sha256_bytes(raw),
        "magic": _u32be(raw, 0),
        "magic_ascii": raw[:4].decode("ascii", errors="replace"),
        "version": _u16be(raw, 4),
        "declared_bytes": _u16be(raw, 6),
        "echo_count": raw[8],
        "current_bank": raw[9],
        "echo_order": list(raw[10:16]),
        "selected_ordinal": raw[16],
        "flags": raw[17],
        "pending_reason": _u16be(raw, 18),
        "attempt_length": _u16be(raw, 20),
        "last_result": _u16be(raw, 22),
    }


def parse_replay_descriptor(raw: bytes) -> dict[str, Any]:
    if len(raw) != REPLAY_DESCRIPTOR_BYTES:
        raise ValueError(
            f"replay descriptor must be {REPLAY_DESCRIPTOR_BYTES} bytes"
        )
    return {
        "raw_hex": raw.hex().upper(),
        "length": _u16be(raw, 0),
        "crc32_iso_hdlc": _u32be(raw, 2),
        "crc32_iso_hdlc_hex": _hex32(_u32be(raw, 2)),
        "flags": raw[6],
        "generation": raw[7],
    }


def parse_audio_shadow(raw: bytes) -> dict[str, Any]:
    if len(raw) != AUDIO_SHADOW_EXTENDED_BYTES:
        raise ValueError(
            "extended audio shadow must be "
            f"{AUDIO_SHADOW_EXTENDED_BYTES} bytes"
        )
    status = parse_z80_status(raw[:16])
    status.update(
        {
            "boot_smoke_latch": raw[16],
            "last_posted_layer_mask": raw[17],
            "extended_raw_hex": raw.hex().upper(),
        }
    )
    return status


def _require_vgm_bytes(payload: bytes, cursor: int, size: int) -> None:
    if cursor + size > len(payload):
        raise VgmError(
            f"truncated VGM command at ${cursor:X}: need {size} bytes"
        )


def parse_vgm(payload: bytes) -> dict[str, Any]:
    """Parse timing and the exact YM/PSG events needed by the M4 gate."""
    if len(payload) < 0x40 or payload[:4] != b"Vgm ":
        raise VgmError("capture is not a VGM file")
    version = _u32le(payload, 0x08)
    data_relative = _u32le(payload, 0x34) if version >= 0x150 else 0
    cursor = 0x40 if data_relative == 0 else 0x34 + data_relative
    if cursor < 0x40 or cursor >= len(payload):
        raise VgmError(f"invalid VGM data offset ${cursor:X}")

    sample = 0
    wait_commands = 0
    ym_events: list[dict[str, int]] = []
    psg_events: list[dict[str, int]] = []
    ended = False
    while cursor < len(payload):
        command = payload[cursor]
        if command == 0x66:
            cursor += 1
            ended = True
            break
        if command == 0x50:
            _require_vgm_bytes(payload, cursor, 2)
            psg_events.append(
                {
                    "sample": sample,
                    "value": payload[cursor + 1],
                    "offset": cursor,
                }
            )
            cursor += 2
            continue
        if command in {0x52, 0x53}:
            _require_vgm_bytes(payload, cursor, 3)
            ym_events.append(
                {
                    "sample": sample,
                    "port": command - 0x52,
                    "register": payload[cursor + 1],
                    "value": payload[cursor + 2],
                    "offset": cursor,
                }
            )
            cursor += 3
            continue
        if command == 0x61:
            _require_vgm_bytes(payload, cursor, 3)
            sample += int.from_bytes(
                payload[cursor + 1 : cursor + 3], "little"
            )
            wait_commands += 1
            cursor += 3
            continue
        if command == 0x62:
            sample += 735
            wait_commands += 1
            cursor += 1
            continue
        if command == 0x63:
            sample += 882
            wait_commands += 1
            cursor += 1
            continue
        if 0x70 <= command <= 0x7F:
            sample += (command & 0x0F) + 1
            wait_commands += 1
            cursor += 1
            continue
        if 0x80 <= command <= 0x8F:
            sample += command & 0x0F
            wait_commands += 1
            cursor += 1
            continue
        if command == 0x67:
            _require_vgm_bytes(payload, cursor, 7)
            if payload[cursor + 1] != 0x66:
                raise VgmError("VGM data block lacks compatibility byte")
            size = _u32le(payload, cursor + 3)
            _require_vgm_bytes(payload, cursor, 7 + size)
            cursor += 7 + size
            continue
        if command == 0x68:
            _require_vgm_bytes(payload, cursor, 12)
            cursor += 12
            continue
        if command == 0x64:
            _require_vgm_bytes(payload, cursor, 4)
            cursor += 4
            continue
        if 0x30 <= command <= 0x3F or command == 0x4F:
            _require_vgm_bytes(payload, cursor, 2)
            cursor += 2
            continue
        if 0x51 <= command <= 0x5F or 0xA0 <= command <= 0xBF:
            _require_vgm_bytes(payload, cursor, 3)
            cursor += 3
            continue
        if 0xC0 <= command <= 0xDF:
            _require_vgm_bytes(payload, cursor, 4)
            cursor += 4
            continue
        if 0xE0 <= command <= 0xFF:
            _require_vgm_bytes(payload, cursor, 5)
            cursor += 5
            continue
        stream_lengths = {
            0x90: 5,
            0x91: 5,
            0x92: 6,
            0x93: 11,
            0x94: 2,
            0x95: 5,
        }
        if command in stream_lengths:
            size = stream_lengths[command]
            _require_vgm_bytes(payload, cursor, size)
            cursor += size
            continue
        raise VgmError(
            f"unsupported VGM command ${command:02X} at ${cursor:X}"
        )
    if not ended:
        raise VgmError("VGM end command is missing")

    key_on_samples: list[int] = []
    key_off_samples: list[int] = []
    frequency_values: set[tuple[int, int, int]] = set()
    for event in ym_events:
        if (
            event["port"] == 0
            and event["register"] == 0x28
        ):
            if event["value"] & 0xF0:
                key_on_samples.append(event["sample"])
            else:
                key_off_samples.append(event["sample"])
        if 0xA0 <= event["register"] <= 0xA6:
            frequency_values.add(
                (event["port"], event["register"], event["value"])
            )

    layer_start_events: list[dict[str, int]] = []
    boot_sfx_start_events: list[dict[str, int]] = []
    layer_pulses: list[dict[str, Any]] = []
    psg_tone_writes = 0
    psg_audible_tone_volume_writes = 0
    noise_volume_events: list[dict[str, int]] = []
    for index, event in enumerate(psg_events):
        value = event["value"]
        if value & 0x80:
            channel = (value >> 5) & 0x03
            is_volume = bool(value & 0x10)
            if channel < 3:
                psg_tone_writes += int(not is_volume)
                if is_volume and (value & 0x0F) < 0x0F:
                    psg_audible_tone_volume_writes += 1
            elif is_volume:
                noise_volume_events.append(event)
        if value != PSG_NOISE_SHORT or index + 1 >= len(psg_events):
            continue
        following = psg_events[index + 1]
        pair_gap = following["sample"] - event["sample"]
        if not 0 <= pair_gap <= MAX_PSG_PAIR_GAP_SAMPLES:
            continue
        if following["value"] == PSG_LAYER_PERCUSSION_VOLUME:
            layer_start_events.append(event)
        elif following["value"] == PSG_BOOT_SFX_VOLUME:
            boot_sfx_start_events.append(event)

    for start_event in layer_start_events:
        start = start_event["sample"]
        mute_sample: int | None = None
        for event in noise_volume_events:
            if (
                event["offset"] > start_event["offset"]
                and event["sample"] >= start
                and event["value"] == PSG_MUTE_NOISE
            ):
                mute_sample = event["sample"]
                break
        layer_pulses.append(
            {
                "start_sample": start,
                "mute_sample": mute_sample,
                "duration_samples": (
                    None if mute_sample is None else mute_sample - start
                ),
                "bounded_and_muted": (
                    mute_sample is not None
                    and 0 < mute_sample - start <= MAX_LAYER_PULSE_SAMPLES
                ),
            }
        )

    return {
        "version_hex": f"{version:08X}",
        "sn76489_clock": _u32le(payload, 0x0C),
        "ym2612_clock": _u32le(payload, 0x2C),
        "header_total_samples": _u32le(payload, 0x18),
        "parsed_wait_samples": sample,
        "duration_seconds": sample / VGM_SAMPLE_RATE,
        "wait_commands": wait_commands,
        "ym2612_writes": len(ym_events),
        "ym_key_on_samples": key_on_samples,
        "ym_key_off_samples": key_off_samples,
        "ym_frequency_value_count": len(frequency_values),
        "psg_writes": len(psg_events),
        "psg_tone_writes": psg_tone_writes,
        "psg_audible_tone_volume_writes": (
            psg_audible_tone_volume_writes
        ),
        "psg_boot_sfx_starts": [
            event["sample"] for event in boot_sfx_start_events
        ],
        "psg_layer_pulse_starts": [
            event["sample"] for event in layer_start_events
        ],
        "psg_layer_pulses": layer_pulses,
        "psg_noise_volume_events": [
            {"sample": event["sample"], "value_hex": f"{event['value']:02X}"}
            for event in noise_volume_events
        ],
        "ended": ended,
    }


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if not key.startswith("_")
    }


def build_audio_report(
    *,
    root: Path,
    rom_sha256: str,
    capture_path: Path,
    session: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Purely evaluate a collected transition session and VGM capture."""
    payload = capture_path.read_bytes()
    facts = parse_vgm(payload)
    pre = session["pre_commit"]
    manual_entry = session["manual_commit_entry"]
    immediate = session["post_commit_immediate"]
    active = session["post_commit_active"]
    final = session["final"]
    observed_inputs = bytes(session["attempt_0_inputs"])
    attempt_1_inputs = bytes(session["attempt_1_inputs"])
    descriptor = immediate["descriptor_6"]
    acquisition_min_sample = (
        CANONICAL_ATTEMPT_FRAMES
        - ACQUISITION_TIMING_TOLERANCE_FRAMES
    ) * NTSC_FRAME_SAMPLES
    pulse_starts = facts["psg_layer_pulse_starts"]
    pre_acquisition_pulses = [
        value for value in pulse_starts if value < acquisition_min_sample
    ]
    post_acquisition_pulses = [
        value for value in pulse_starts if value >= acquisition_min_sample
    ]
    ym_before = [
        value
        for value in facts["ym_key_on_samples"]
        if value < acquisition_min_sample
    ]
    ym_after = [
        value
        for value in facts["ym_key_on_samples"]
        if value >= acquisition_min_sample
    ]
    writes = debugger_write_packets(session.get("_transcript_lines", []))
    source_before = provenance.get("source_binding_before", {})
    source_after = provenance.get("source_binding_after", {})
    source_current = source_binding(root)

    checks = [
        _check(
            "hash_bound_real_rom_and_sources",
            _valid_sha256(rom_sha256)
            and provenance.get("rom", {}).get("sha256") == rom_sha256
            and source_before == source_after == source_current,
            "ROM SHA-256 "
            f"{rom_sha256}; source bundle "
            f"{source_current.get('bundle_sha256')}",
        ),
        _check(
            "isolated_standard_three_button_real_pad",
            session["metadata"]["controller_profile"].get("status") == "pass"
            and session["metadata"]["controller_profile"].get(
                "port_1_assignment"
            )
            == "gamepad3.1"
            and session["metadata"]["controller_profile"]
            .get("session_isolation", {})
            .get("status")
            == "pass",
            "temporary profile explicitly assigns gamepad3.1 and redirects "
            "all writable paths",
        ),
        _check(
            "read_only_debugger_session",
            not writes,
            f"RAM/register/PC write packets {writes}",
        ),
        _check(
            "symbol_bound_observation",
            session["metadata"].get("initial_breakpoint_symbol")
            == "VBlankInterrupt"
            and session["metadata"].get("initial_breakpoint_role")
            == STARTUP_PRIMING_ROLE
            and session["metadata"].get("authoritative_origin")
            == FIRST_RUNNING_VBLANK_ROLE
            and session["metadata"].get(
                "counters_and_controller_poll_begin"
            )
            == FIRST_RUNNING_VBLANK_ROLE
            and session["metadata"].get("step_breakpoint_symbol")
            == "MainLoopPostGameplayStep",
            "tick 0 used the stage-3 acknowledgment-only priming ISR entry; "
            "controller polling and counters begin on the next stage-4 "
            "RUNNING full VBlank, and gameplay observations used "
            "MainLoopPostGameplayStep from the hash-bound symbol map",
        ),
        _check(
            "exact_real_pad_attempt_schedule",
            observed_inputs == CANONICAL_ATTEMPT_0
            and session["progression"] == list(range(1, 41)),
            "observed input bytes "
            f"{observed_inputs.hex().upper()} and ticks "
            f"{session['progression'][:2]}..{session['progression'][-2:]}",
        ),
        _check(
            "zero_echo_base_state",
            pre["engine"]["tick"] == 40
            and pre["engine"]["attempt"] == 0
            and pre["engine"]["state"] == ENGINE_STATE_ACTIVE
            and pre["timeline"]["magic"] == TIMELINE_MAGIC
            and pre["timeline"]["echo_count"] == 0
            and pre["timeline"]["attempt_length"] == 40
            and pre["audio"]["state"] == Z80_STATE_PLAYING
            and pre["audio"]["active_layer_mask"] == BASE_LAYER_MASK
            and pre["audio"]["last_posted_layer_mask"] == BASE_LAYER_MASK,
            "tick/attempt/echo/length/layers "
            f"{pre['engine']['tick']}/{pre['engine']['attempt']}/"
            f"{pre['timeline']['echo_count']}/"
            f"{pre['timeline']['attempt_length']}/"
            f"{pre['audio']['active_layer_mask']:02X}",
        ),
        _check(
            "real_start_c_manual_commit",
            manual_entry["input"]["connected"] == 1
            and manual_entry["input"]["start_held"] == 1
            and manual_entry["input"]["held"] == INPUT_C
            and manual_entry["input"]["pressed"] & INPUT_C
            and manual_entry["engine"]["tick"] == 40
            and manual_entry["engine"]["attempt"] == 0
            and immediate["engine"]["tick"] == 0
            and immediate["engine"]["attempt"] == 1
            and immediate["gameplay"]["last_commit_reason"]
            == GAMEPLAY_COMMIT_MANUAL
            and immediate["gameplay"]["result"] == GAMEPLAY_RESULT_OK,
            "meta-entry guest input Start/held/pressed/tick/attempt and "
            "reset tick/attempt/reason "
            f"{manual_entry['input']['start_held']}/"
            f"{manual_entry['input']['held']:02X}/"
            f"{manual_entry['input']['pressed']:02X}/"
            f"{manual_entry['engine']['tick']}/"
            f"{manual_entry['engine']['attempt']}/"
            f"{immediate['engine']['tick']}/"
            f"{immediate['engine']['attempt']}/"
            f"{immediate['gameplay']['last_commit_reason']}",
        ),
        _check(
            "attempt_1_input_staged_before_commit_reset",
            session["metadata"].get(
                "attempt_1_input_staging_breakpoint_symbol"
            )
            == "TimelineHandleMetaInput"
            and session["metadata"].get(
                "attempt_1_input_staging_breakpoint"
            )
            == provenance.get("symbol_map", {})
            .get("symbols", {})
            .get("TimelineHandleMetaInput")
            and session["metadata"].get("attempt_1_input_staging_role")
            == "after_controller_poll_before_commit_reset"
            and manual_entry["engine"]["attempt"] == 0
            and manual_entry["engine"]["tick"] == 40
            and manual_entry["input"]["connected"] == 1
            and manual_entry["input"]["start_held"] == 1
            and manual_entry["input"]["held"] == INPUT_C
            and manual_entry["input"]["pressed"] & INPUT_C
            and session["input_injection"].get(
                "next_attempt_events_staged_at"
            )
            == "TimelineHandleMetaInput after ControllerPoll"
            and session["input_injection"].get("staged_keyup_order")
            == ["C", "Start"]
            and attempt_1_inputs == bytes(POST_COMMIT_CAPTURE_FRAMES),
            "Start+C was latched at meta entry; C/Start releases were "
            "staged before the long reset and observed attempt 1 bytes were "
            f"{attempt_1_inputs.hex().upper()}",
        ),
        _check(
            "committed_bank_from_observed_simulation",
            descriptor["length"] == 40
            and (
                descriptor["flags"] & (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            )
            == (REPLAY_FLAG_VALID | REPLAY_FLAG_ECHO)
            and not (descriptor["flags"] & REPLAY_FLAG_CURRENT)
            and bytes(immediate["bank_6_inputs"]) == observed_inputs
            and descriptor["crc32_iso_hdlc"]
            == crc32_iso_hdlc(observed_inputs)
            and immediate["gameplay"]["last_bank_crc"]
            == crc32_iso_hdlc(observed_inputs),
            "bank 6 length/flags/published/computed CRC "
            f"{descriptor['length']}/{descriptor['flags']:02X}/"
            f"{descriptor['crc32_iso_hdlc_hex']}/"
            f"{_hex32(crc32_iso_hdlc(observed_inputs))}",
        ),
        _check(
            "first_echo_and_layer_mask_transition",
            immediate["timeline"]["echo_count"] == 1
            and immediate["timeline"]["echo_order"][0] == 6
            and immediate["timeline"]["current_bank"] == 0
            and immediate["audio"]["last_posted_layer_mask"]
            == FIRST_ECHO_LAYER_MASK
            and active["timeline"]["echo_count"] == 1
            and active["audio"]["active_layer_mask"]
            == FIRST_ECHO_LAYER_MASK
            and active["audio"]["processed_commands"]
            > pre["audio"]["processed_commands"],
            "echo/order/current and active/posted masks "
            f"{active['timeline']['echo_count']}/"
            f"{active['timeline']['echo_order'][0]}/"
            f"{active['timeline']['current_bank']}/"
            f"{active['audio']['active_layer_mask']:02X}/"
            f"{immediate['audio']['last_posted_layer_mask']:02X}",
        ),
        _check(
            "vgm_valid_timing_and_clocks",
            facts["sn76489_clock"] > 0
            and facts["ym2612_clock"] > 0
            and MIN_CAPTURE_SAMPLES
            <= facts["parsed_wait_samples"]
            <= MAX_CAPTURE_SAMPLES
            and facts["header_total_samples"]
            == facts["parsed_wait_samples"]
            and facts["ended"],
            "clocks/samples/header "
            f"{facts['sn76489_clock']}/{facts['ym2612_clock']}/"
            f"{facts['parsed_wait_samples']}/"
            f"{facts['header_total_samples']}",
        ),
        _check(
            "non_silent_base_and_post_acquisition_motif",
            len(ym_before) >= 2
            and len(ym_after) >= 2
            and len(facts["ym_key_off_samples"]) >= 4
            and facts["ym_frequency_value_count"] >= 3
            and facts["psg_tone_writes"] >= 4
            and facts["psg_audible_tone_volume_writes"] > 0,
            "YM key-ons before/after, key-offs, frequencies, PSG tone "
            f"{len(ym_before)}/{len(ym_after)}/"
            f"{len(facts['ym_key_off_samples'])}/"
            f"{facts['ym_frequency_value_count']}/"
            f"{facts['psg_tone_writes']}",
        ),
        _check(
            "percussion_signature_only_after_echo_acquisition",
            not pre_acquisition_pulses
            and len(post_acquisition_pulses) >= 2,
            "conservative acquisition guard sample "
            f"{acquisition_min_sample}; layer $E4+$F8 starts "
            f"{pulse_starts}",
        ),
        _check(
            "percussion_pulses_bounded_and_muted",
            bool(facts["psg_layer_pulses"])
            and all(
                pulse["bounded_and_muted"]
                for pulse in facts["psg_layer_pulses"]
            ),
            "layer pulse start/mute/duration "
            f"{facts['psg_layer_pulses']}",
        ),
        _check(
            "audio_queue_and_driver_faults_clear",
            all(
                item["audio"]["signature"] == 0x94
                and item["audio"]["abi_version"] == 1
                and item["audio"]["state"] == Z80_STATE_PLAYING
                and (
                    item["audio"]["flags"] & Z80_REQUIRED_FLAGS
                )
                == Z80_REQUIRED_FLAGS
                and not (item["audio"]["flags"] & Z80_FAULT_FLAGS)
                and 0
                <= item["audio"]["queue_high_water"]
                <= Z80_QUEUE_CAPACITY
                and item["audio"]["late_tick_count"] == 0
                for item in (pre, active, final)
            ),
            "status flags/high-water/late "
            f"{[(s['audio']['flags'], s['audio']['queue_high_water'], s['audio']['late_tick_count']) for s in (pre, active, final)]}",
        ),
        _check(
            "no_fatal_bus_timeout_or_gameplay_error",
            all(
                item["debug"]["fatal_code"] == 0
                and not (item["debug"]["hardware_flags"] & 0x01)
                and item["debug"]["z80_timeout_count"] == 0
                and item["engine"]["last_error"] == 0
                and item["engine"]["overflow_flags"] == 0
                and item["gameplay"]["magic"] == GAMEPLAY_MAGIC
                and (
                    item["gameplay"]["flags"] & GAMEPLAY_FLAG_INITIALIZED
                )
                == GAMEPLAY_FLAG_INITIALIZED
                for item in (pre, immediate, active, final)
            ),
            "fatal/hardware/Z80 timeout and engine errors remained clear",
        ),
        _check(
            "capture_and_provenance_hashes",
            _valid_sha256(_sha256_bytes(payload))
            and _valid_sha256(
                session["metadata"].get("transcript_sha256")
            )
            and _valid_sha256(
                provenance.get("emulator", {}).get("sha256")
            )
            and _valid_sha256(
                provenance.get("symbol_map", {}).get("sha256")
            ),
            "VGM, transcript, emulator, and symbol map have SHA-256 bindings",
        ),
    ]
    passed = bool(checks) and all(
        item["status"] == "pass" for item in checks
    )
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "capture": {
            "path": capture_path.relative_to(root).as_posix(),
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
            "format": "VGM",
            "facts": facts,
            "acquisition_guard_sample": acquisition_min_sample,
            "acquisition_guard_basis": (
                "40 real-pad authoritative frames minus a conservative "
                "two-frame VGM-toggle/event-order tolerance"
            ),
        },
        "session": {
            "metadata": session["metadata"],
            "initial": _public_snapshot(session["initial"]),
            "progression": session["progression"],
            "attempt_0_inputs": {
                "bytes": len(observed_inputs),
                "hex": observed_inputs.hex().upper(),
                "sha256": _sha256_bytes(observed_inputs),
                "crc32_iso_hdlc": _hex32(crc32_iso_hdlc(observed_inputs)),
            },
            "pre_commit": _public_snapshot(pre),
            "manual_commit_entry": _public_snapshot(manual_entry),
            "post_commit_immediate": _public_snapshot(immediate),
            "attempt_1_inputs": {
                "bytes": len(attempt_1_inputs),
                "hex": attempt_1_inputs.hex().upper(),
                "sha256": _sha256_bytes(attempt_1_inputs),
            },
            "post_commit_active": _public_snapshot(active),
            "final": _public_snapshot(final),
            "input_injection": session["input_injection"],
        },
        "provenance": provenance,
        "debugger_write_packets": writes,
        "limitations": [
            "VGM proves emitted YM2612/PSG chip commands and timing; it is not "
            "a subjective human-listening claim.",
            "The conservative sample guard accounts for window-event/VGM "
            "toggle ordering. Guest state itself is observed at exact "
            "symbol-bound frame boundaries.",
            "This gate covers the first echo-layer transition in B01. It does "
            "not claim Program Stable, later echo layers, or Release-ROM "
            "equivalence.",
        ],
    }


def _captured_source_binding_errors(binding: Any) -> list[str]:
    """Validate a capture-time source bundle without consulting live files."""

    if not isinstance(binding, dict):
        return ["captured source binding is missing"]
    sources = binding.get("sources")
    if not isinstance(sources, dict) or not sources:
        return ["captured source binding sources are missing"]
    if not all(
        isinstance(path, str)
        and path
        and _valid_sha256(digest)
        for path, digest in sources.items()
    ):
        return ["captured source binding contains invalid source hashes"]
    canonical = json.dumps(
        sources,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    expected_bundle = _sha256_bytes(canonical)
    if binding.get("algorithm") != "SHA-256":
        return ["captured source binding algorithm mismatch"]
    if (
        binding.get("canonical_form")
        != "sorted compact path-to-sha256 JSON"
    ):
        return ["captured source binding canonical form mismatch"]
    if binding.get("bundle_sha256") != expected_bundle:
        return ["captured source binding bundle SHA-256 mismatch"]
    return []


def verify_saved_report(
    *,
    root: Path,
    rom: Path,
    report: dict[str, Any],
    source_policy: str = "current",
) -> list[str]:
    """Return every ROM/source/artifact drift found in a saved report.

    ``source_policy="current"`` is the live gate and compares the capture to
    current source files. ``"captured"`` is exclusively for an explicitly
    archived ROM: it validates the report's before/after source bundle
    internally without pretending those historical hashes describe the live
    tree.
    """
    failures: list[str] = []
    if source_policy not in {"current", "captured"}:
        raise ValueError("source_policy must be 'current' or 'captured'")
    if report.get("schema") != REPORT_SCHEMA:
        failures.append("schema mismatch")
    if report.get("status") != "pass":
        failures.append("report status is not pass")
    if not rom.is_file():
        failures.append("bound ROM is missing")
    elif report.get("rom_sha256") != sha256_file(rom):
        failures.append("ROM SHA-256 drift")
    provenance_value = report.get("provenance")
    provenance = (
        provenance_value if isinstance(provenance_value, dict) else {}
    )
    report_rom_sha = report.get("rom_sha256")
    provenance_rom = provenance.get("rom")
    if (
        not isinstance(provenance_value, dict)
        or not isinstance(provenance_rom, dict)
        or provenance_rom.get("sha256") != report_rom_sha
    ):
        failures.append("provenance ROM SHA-256 mismatch")
    if source_policy == "current":
        try:
            expected_binding = source_binding(root)
        except Exception as error:
            failures.append(f"source binding unavailable: {error}")
        else:
            if provenance.get("source_binding_after") != expected_binding:
                failures.append("source binding drift")
    else:
        before = provenance.get("source_binding_before")
        after = provenance.get("source_binding_after")
        failures.extend(_captured_source_binding_errors(before))
        failures.extend(_captured_source_binding_errors(after))
        if before != after:
            failures.append("captured source binding changed during capture")
    capture = report.get("capture", {})
    path_value = capture.get("path")
    if not isinstance(path_value, str):
        failures.append("capture path is missing")
    else:
        capture_path = root / path_value
        if not capture_path.is_file():
            failures.append("capture file is missing")
        elif capture.get("sha256") != sha256_file(capture_path):
            failures.append("capture SHA-256 drift")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        failures.append("checks are missing")
    else:
        if any(
            not isinstance(item, dict) or item.get("status") != "pass"
            for item in checks
        ):
            failures.append("one or more checks did not pass")
        check_names = {
            item.get("check")
            for item in checks
            if isinstance(item, dict)
        }
        if not REQUIRED_CURRENT_CHECKS.issubset(check_names):
            failures.append("required current checks are missing")
    return failures


def reconstruct_public_session(
    public: dict[str, Any], transcript_lines: list[str]
) -> dict[str, Any]:
    """Reconstruct the evaluator input from a previously emitted public report."""
    try:
        input_hex = public["attempt_0_inputs"]["hex"]
        attempt_inputs = list(bytes.fromhex(input_hex))
        attempt_1_hex = public["attempt_1_inputs"]["hex"]
        attempt_1_inputs = list(bytes.fromhex(attempt_1_hex))
        return {
            "metadata": public["metadata"],
            "initial": public["initial"],
            "progression": public["progression"],
            "attempt_0_inputs": attempt_inputs,
            "pre_commit": public["pre_commit"],
            "manual_commit_entry": public["manual_commit_entry"],
            "post_commit_immediate": public["post_commit_immediate"],
            "attempt_1_inputs": attempt_1_inputs,
            "post_commit_active": public["post_commit_active"],
            "final": public["final"],
            "input_injection": public["input_injection"],
            "_transcript_lines": transcript_lines,
        }
    except (KeyError, TypeError, ValueError) as error:
        raise EvidenceFailure(
            f"saved M4 audio session is incomplete: {error}"
        ) from error


def recheck_existing_report(
    *,
    root: Path,
    rom: Path,
    report_path: Path,
    trace_path: Path,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Re-evaluate an immutable live capture after a pure parser correction."""
    try:
        saved = json.loads(report_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceFailure(
            f"cannot load saved M4 audio report: {error}"
        ) from error
    if not isinstance(saved, dict) or saved.get("schema") != REPORT_SCHEMA:
        raise EvidenceFailure("saved M4 audio report schema does not match")
    rom_sha256 = sha256_file(rom)
    if expected_sha256 and rom_sha256 != expected_sha256.strip().lower():
        raise EvidenceFailure("current ROM does not match expected SHA-256")
    if saved.get("rom_sha256") != rom_sha256:
        raise EvidenceFailure("saved report ROM SHA-256 drift")
    provenance = saved.get("provenance")
    if not isinstance(provenance, dict):
        raise EvidenceFailure("saved report provenance is missing")
    current_binding = source_binding(root)
    if (
        provenance.get("source_binding_before")
        != provenance.get("source_binding_after")
        or provenance.get("source_binding_after") != current_binding
    ):
        raise EvidenceFailure("saved report source binding drift")
    capture_relative = saved.get("capture", {}).get("path")
    capture_sha256 = saved.get("capture", {}).get("sha256")
    if not isinstance(capture_relative, str):
        raise EvidenceFailure("saved report capture path is missing")
    capture_path = root / capture_relative
    if (
        not capture_path.is_file()
        or sha256_file(capture_path) != capture_sha256
    ):
        raise EvidenceFailure("saved VGM capture is missing or hash-drifted")
    if not trace_path.is_file():
        raise EvidenceFailure("saved GDB transcript is missing")
    trace_payload = trace_path.read_bytes()
    trace_sha256 = _sha256_bytes(trace_payload)
    if (
        provenance.get("transcript", {}).get("sha256") != trace_sha256
        or saved.get("session", {})
        .get("metadata", {})
        .get("transcript_sha256")
        != trace_sha256
    ):
        raise EvidenceFailure("saved GDB transcript hash drift")
    transcript_lines = trace_payload.decode(
        "utf-8", errors="strict"
    ).splitlines()
    session = reconstruct_public_session(
        saved["session"], transcript_lines
    )
    provenance = copy_json(provenance)
    provenance["offline_recheck"] = {
        "performed_utc": datetime.now(timezone.utc).isoformat(),
        "reason": (
            "pure VGM parser correction: cycle-timed consecutive PSG "
            "writes may be 1-2 samples apart"
        ),
        "no_emulator_rerun": True,
        "capture_sha256_reverified": capture_sha256,
        "transcript_sha256_reverified": trace_sha256,
    }
    return build_audio_report(
        root=root,
        rom_sha256=rom_sha256,
        capture_path=capture_path,
        session=session,
        provenance=provenance,
    )


def copy_json(value: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-domain deep copy without sharing nested mappings."""
    return json.loads(json.dumps(value))


def configure_m4_audio_profile(
    config_path: Path, capture_dir: Path
) -> dict[str, Any]:
    """Bind M to a uniquely named, task-owned M4 VGM destination."""
    text = config_path.read_text("utf-8", errors="strict").replace(
        "\r\n", "\n"
    )
    if not re.search(r"(?m)^\s*m\s+ui\.vgm_log\s*$", text):
        raise EvidenceFailure("pinned profile has no M -> ui.vgm_log binding")
    template = "recursion94-m4-audio-%Y%m%d-%H%M%S.vgm"
    text, path_count = re.subn(
        r"(?m)^\s*vgm_path\s+.*$",
        f"\tvgm_path {capture_dir.resolve().as_posix()}",
        text,
        count=1,
    )
    text, template_count = re.subn(
        r"(?m)^\s*vgm_template\s+.*$",
        f"\tvgm_template {template}",
        text,
        count=1,
    )
    if path_count != 1 or template_count != 1:
        raise EvidenceFailure(
            "isolated BlastEm profile lacks unique VGM path/template settings"
        )
    config_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "status": "pass",
        "path": config_path.as_posix(),
        "sha256": sha256_file(config_path),
        "vgm_path": capture_dir.resolve().as_posix(),
        "vgm_template": template,
        "toggle_binding": "M -> ui.vgm_log",
    }


def _read_region(
    remote: GdbRemote, address: int, size: int, chunk_size: int = 0xFF
) -> bytes:
    if size <= 0 or not 0 < chunk_size <= 0xFF:
        raise ValueError("BlastEm memory reads require positive <=255 chunks")
    result = bytearray()
    while len(result) < size:
        amount = min(chunk_size, size - len(result))
        result.extend(remote.read_memory(address + len(result), amount))
    if len(result) != size:
        raise EvidenceFailure(
            f"assembled read is {len(result)} bytes; {size} required"
        )
    return bytes(result)


def _capture_snapshot(
    remote: GdbRemote,
    *,
    label: str,
    include_committed_bank: bool = False,
) -> dict[str, Any]:
    engine = parse_engine_control(
        _read_region(remote, ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES)
    )
    gameplay = parse_gameplay_control(
        _read_region(
            remote, GAMEPLAY_CONTROL_BASE, GAMEPLAY_CONTROL_BYTES
        )
    )
    timeline = parse_timeline(
        _read_region(
            remote, TIMELINE_CONTROL_BASE, TIMELINE_CONTROL_BYTES
        )
    )
    snapshot: dict[str, Any] = {
        "label": label,
        "engine": engine,
        "gameplay": gameplay,
        "timeline": timeline,
        "input": parse_input_state(
            _read_region(remote, INPUT_STATE_BASE, INPUT_STATE_BYTES)
        ),
        "audio": parse_audio_shadow(
            _read_region(
                remote, AUDIO_SHADOW_BASE, AUDIO_SHADOW_EXTENDED_BYTES
            )
        ),
        "debug": parse_debug_block(
            _read_region(remote, DEBUG_BASE, DEBUG_BLOCK_BYTES)
        ),
    }
    if include_committed_bank:
        descriptor = parse_replay_descriptor(
            _read_region(
                remote, REPLAY_DESCRIPTOR_6, REPLAY_DESCRIPTOR_BYTES
            )
        )
        length = descriptor["length"]
        if not 0 <= length <= REPLAY_BANK_BYTES:
            raise EvidenceFailure(
                f"committed bank length {length} exceeds {REPLAY_BANK_BYTES}"
            )
        bank = _read_region(remote, REPLAY_BANK_6, length)
        snapshot["descriptor_6"] = descriptor
        snapshot["bank_6_inputs"] = list(bank)
        snapshot["bank_6_sha256"] = _sha256_bytes(bank)
    return snapshot


def _post_key(
    window: int, key: tuple[str, int, int, bool], pressed: bool
) -> None:
    _name, virtual_key, scan_code, extended = key
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=pressed,
        extended=extended,
    )


def _tap_key(window: int, key: tuple[str, int, int, bool]) -> None:
    _post_key(window, key, True)
    _post_key(window, key, False)


def _capture_directory_state(directory: Path) -> dict[Path, tuple[int, int]]:
    return {
        path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in directory.glob("recursion94-m4-audio-*.vgm")
        if path.is_file()
    }


def _find_fresh_capture(
    directory: Path,
    before: dict[Path, tuple[int, int]],
    started_ns: int,
) -> Path:
    candidates: list[Path] = []
    for path in directory.glob("recursion94-m4-audio-*.vgm"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        state = (path.stat().st_mtime_ns, path.stat().st_size)
        if (
            before.get(resolved) != state
            and path.stat().st_mtime_ns >= started_ns - 2_000_000_000
        ):
            candidates.append(path)
    if len(candidates) != 1:
        raise EvidenceFailure(
            f"expected exactly one fresh M4 VGM, found {len(candidates)}: "
            f"{[path.name for path in candidates]}"
        )
    return candidates[0]


def _verify_default_m4_bindings(default_config: Path) -> dict[str, Any]:
    text = default_config.read_text("utf-8", errors="replace")
    expected = {
        "right": "gamepads.1.right",
        "d": "gamepads.1.c",
        "enter": "gamepads.1.start",
        "m": "ui.vgm_log",
    }
    checks = {
        key: bool(
            re.search(
                rf"(?m)^\s*{re.escape(key)}\s+{re.escape(action)}\s*$",
                text,
            )
        )
        for key, action in expected.items()
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "path": default_config.as_posix(),
        "source_sha256": sha256_file(default_config),
        "bindings": checks,
    }


def _run_live_session(
    *,
    root: Path,
    rom: Path,
    emulator_directory: Path,
    default_config: Path,
    symbols: dict[str, int],
    timeout_seconds: float,
    capture_dir: Path,
    transcript: list[str],
) -> tuple[dict[str, Any], Path]:
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    window: int | None = None
    pressed: list[tuple[str, int, int, bool]] = []
    capture_started_ns = 0
    capture_before = _capture_directory_state(capture_dir)
    session: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(
        prefix="recursion94-m4-audio-"
    ) as profile_text:
        profile = Path(profile_text)
        isolated_runtime, runtime_copy = clone_blastem_runtime(
            emulator_directory, profile
        )
        controller_profile = write_standard_pad_profile(
            default_config, profile
        )
        controller_profile["session_isolation"] = (
            apply_blastem_session_isolation(
                Path(controller_profile["path"]),
                profile / "session-data",
                vgm_path=capture_dir,
            )
        )
        controller_profile["audio_profile"] = configure_m4_audio_profile(
            Path(controller_profile["path"]), capture_dir
        )
        controller_profile["sha256"] = controller_profile[
            "audio_profile"
        ]["sha256"]
        controller_profile["runtime_copy"] = runtime_copy
        require_gdb_port_available("127.0.0.1", 1234)
        command = [
            str(isolated_runtime / "blastem.exe"),
            *LAUNCH_FLAGS,
            str(rom),
        ]
        start_index = len(transcript)
        transcript.append("# m4 audio transition session begin")
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
                    f"BlastEm exited early with {process.returncode}"
                )
            vblank = symbols["VBlankInterrupt"]
            post_step = symbols["MainLoopPostGameplayStep"]
            if remote.request(f"Z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("VBlank breakpoint insertion failed")
            # This first stop is the stage-3 acknowledgment-only priming ISR.
            # Input sampling and frame counters begin on the next stage-4
            # RUNNING full VBlank.
            require_stop(remote.request("c"))
            initial = _capture_snapshot(remote, label="tick_000")
            if (
                initial["engine"]["tick"] != 0
                or initial["timeline"]["echo_count"] != 0
            ):
                raise EvidenceFailure(
                    "fresh session did not stop at zero-echo tick 0"
                )
            window = find_process_window(process.pid, 30.0)
            if remote.request(f"z0,{vblank:x},2") != "OK":
                raise EvidenceFailure("VBlank breakpoint removal failed")
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    "post-gameplay breakpoint insertion failed"
                )

            # Queue capture enable and Right before the first authoritative
            # frame. VGM logging may begin one host-event turn later; the
            # report uses a conservative two-frame sample tolerance.
            capture_started_ns = time.time_ns()
            _tap_key(window, VGM_KEY)
            _post_key(window, RIGHT_KEY, True)
            pressed.append(RIGHT_KEY)
            progression: list[int] = []
            attempt_inputs: list[int] = []
            for expected_tick in range(1, CANONICAL_ATTEMPT_FRAMES + 1):
                require_stop(remote.request("c"))
                if expected_tick == CANONICAL_RIGHT_FRAMES:
                    _post_key(window, RIGHT_KEY, False)
                    pressed.remove(RIGHT_KEY)
                engine = parse_engine_control(
                    remote.read_memory(
                        ENGINE_CONTROL_BASE, ENGINE_CONTROL_USED_BYTES
                    )
                )
                input_state = parse_input_state(
                    remote.read_memory(INPUT_STATE_BASE, INPUT_STATE_BYTES)
                )
                observed_tick = int(engine["tick"])
                if observed_tick != expected_tick:
                    raise EvidenceFailure(
                        f"observed tick {observed_tick}; "
                        f"{expected_tick} required"
                    )
                progression.append(observed_tick)
                attempt_inputs.append(int(input_state["held"]))

            pre_commit = _capture_snapshot(remote, label="pre_commit_tick_040")
            if bytes(attempt_inputs) != CANONICAL_ATTEMPT_0:
                raise EvidenceFailure(
                    "real-pad first-attempt input schedule drifted"
                )

            # Start+C is posted together between authoritative boundaries.
            # Stop after ControllerPoll has latched the chord but before the
            # long commit/reset begins. Reset can admit another VBlank and
            # queue attempt 1's first token, so releases must be staged here
            # instead of at the later post-reset main-loop stop.
            for key in (START_KEY, C_KEY):
                _post_key(window, key, True)
                pressed.append(key)
            meta_input = symbols["TimelineHandleMetaInput"]
            if remote.request(f"z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    "post-gameplay breakpoint removal failed before "
                    "manual-commit entry"
                )
            if remote.request(f"Z0,{meta_input:x},2") != "OK":
                raise EvidenceFailure(
                    "meta-input breakpoint insertion failed"
                )
            require_stop(remote.request("c"))
            manual_commit_entry = _capture_snapshot(
                remote,
                label="attempt_0_tick_040_start_c_meta_entry",
            )
            if (
                manual_commit_entry["engine"]["attempt"] != 0
                or manual_commit_entry["engine"]["tick"] != 40
                or manual_commit_entry["input"]["connected"] != 1
                or manual_commit_entry["input"]["start_held"] != 1
                or manual_commit_entry["input"]["held"] != INPUT_C
                or not (
                    manual_commit_entry["input"]["pressed"] & INPUT_C
                )
            ):
                raise EvidenceFailure(
                    "Start+C was not latched at attempt 0 tick 40 "
                    "TimelineHandleMetaInput entry"
                )
            for key in (C_KEY, START_KEY):
                _post_key(window, key, False)
                pressed.remove(key)
            if remote.request(f"z0,{meta_input:x},2") != "OK":
                raise EvidenceFailure(
                    "meta-input breakpoint removal failed"
                )
            if remote.request(f"Z0,{post_step:x},2") != "OK":
                raise EvidenceFailure(
                    "post-gameplay breakpoint reinsertion failed"
                )
            require_stop(remote.request("c"))
            post_commit = _capture_snapshot(
                remote,
                label="post_manual_commit_tick_000",
                include_committed_bank=True,
            )
            if (
                post_commit["engine"]["attempt"] != 1
                or post_commit["engine"]["tick"] != 0
            ):
                raise EvidenceFailure(
                    "Start+C did not commit/reset to attempt 1 tick 0"
                )

            post_snapshots: list[dict[str, Any]] = []
            attempt_1_inputs: list[int] = []
            active_snapshot: dict[str, Any] | None = None
            for expected_tick in range(1, POST_COMMIT_CAPTURE_FRAMES + 1):
                require_stop(remote.request("c"))
                snapshot = _capture_snapshot(
                    remote,
                    label=f"post_commit_tick_{expected_tick:03d}",
                )
                if snapshot["engine"]["tick"] != expected_tick:
                    raise EvidenceFailure(
                        f"post-commit tick {snapshot['engine']['tick']}; "
                        f"{expected_tick} required"
                    )
                post_snapshots.append(snapshot)
                attempt_1_inputs.append(int(snapshot["input"]["held"]))
                if (
                    active_snapshot is None
                    and snapshot["audio"]["active_layer_mask"]
                    == FIRST_ECHO_LAYER_MASK
                    and snapshot["timeline"]["echo_count"] == 1
                ):
                    active_snapshot = snapshot
            if active_snapshot is None:
                raise EvidenceFailure(
                    "Z80 never published active layer mask $03"
                )
            if bytes(attempt_1_inputs) != bytes(POST_COMMIT_CAPTURE_FRAMES):
                raise EvidenceFailure(
                    "attempt 1 audio capture input was not neutral for all "
                    f"{POST_COMMIT_CAPTURE_FRAMES} authoritative frames"
                )

            # Queue capture disable, then run exactly one more frame so the UI
            # event is serviced and the VGM end command/file close is emitted.
            _tap_key(window, VGM_KEY)
            require_stop(remote.request("c"))
            final_snapshot = _capture_snapshot(
                remote, label="post_capture_final"
            )
            time.sleep(0.5)
            session_lines = transcript[start_index:]
            session = {
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
                    "step_breakpoint_symbol": "MainLoopPostGameplayStep",
                    "step_breakpoint": f"{post_step:08X}",
                    "attempt_1_input_staging_breakpoint_symbol": (
                        "TimelineHandleMetaInput"
                    ),
                    "attempt_1_input_staging_breakpoint": (
                        f"{meta_input:08X}"
                    ),
                    "attempt_1_input_staging_role": (
                        "after_controller_poll_before_commit_reset"
                    ),
                    "symbol_source": "build/recursion94-debug.sym",
                    "controller_profile": controller_profile,
                    "runtime_copy": runtime_copy,
                    "transcript_sha256": _sha256_bytes(
                        (
                            "\n".join(session_lines)
                            + ("\n" if session_lines else "")
                        ).encode("utf-8")
                    ),
                },
                "initial": initial,
                "progression": progression,
                "attempt_0_inputs": attempt_inputs,
                "pre_commit": pre_commit,
                "manual_commit_entry": manual_commit_entry,
                "post_commit_immediate": post_commit,
                "attempt_1_inputs": attempt_1_inputs,
                "post_commit_active": active_snapshot,
                "post_commit_observations": post_snapshots,
                "final": final_snapshot,
                "input_injection": {
                    "status": "pass",
                    "method": "targeted PostMessageW window key transitions",
                    "attempt": "Right x32, neutral x8",
                    "manual_commit": (
                        "Start then C keydown; chord observed at "
                        "TimelineHandleMetaInput before commit/reset"
                    ),
                    "attempt_1": "neutral x45",
                    "keys": ["Right", "Enter", "D"],
                    "buttons": ["Right", "Start", "C"],
                    "staged_keydown_order": ["Start", "C"],
                    "staged_keyup_order": ["C", "Start"],
                    "next_attempt_events_staged_at": (
                        "TimelineHandleMetaInput after ControllerPoll"
                    ),
                    "manual_commit_authoritative_frames_consumed": 0,
                    "guest_ram_register_or_pc_writes": False,
                    "all_keys_released": True,
                },
                "_transcript_lines": session_lines,
            }
        finally:
            if window is not None:
                for key in reversed(pressed):
                    try:
                        _post_key(window, key, False)
                    except Exception as error:
                        transcript.append(
                            "! emergency key release failed: "
                            f"{type(error).__name__}: {error}"
                        )
            shutdown_emulator(remote, process, transcript)
            transcript.append("# m4 audio transition session end")
    if session is None:
        raise EvidenceFailure("audio transition session produced no result")
    session_lines = transcript[start_index:]
    session["_transcript_lines"] = session_lines
    session["metadata"]["transcript_sha256"] = _sha256_bytes(
        (
            "\n".join(session_lines)
            + ("\n" if session_lines else "")
        ).encode("utf-8")
    )
    capture = _find_fresh_capture(
        capture_dir, capture_before, capture_started_ns
    )
    return session, capture


def _failure_report(
    *,
    rom_sha256: str,
    error: Exception,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    detail = f"{type(error).__name__}: {error}"
    return {
        "schema": REPORT_SCHEMA,
        "scope": "Milestone4",
        "evidence_type": EVIDENCE_TYPE,
        "status": "fail",
        "rom_sha256": rom_sha256,
        "checks": [_check("m4_audio_evidence_session", False, detail)],
        "error": detail,
        "provenance": provenance,
        "debugger_write_packets": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--recheck-existing", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--lock-timeout-seconds", type=float, default=600.0)
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m4-audio-capture.json"
    )
    trace_path = (
        args.trace.resolve()
        if args.trace
        else root / "artifacts/traces/m4-audio-gdb-transcript.log"
    )
    capture_dir = root / "artifacts/audio"
    capture_dir.mkdir(parents=True, exist_ok=True)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    if args.recheck_existing:
        try:
            report = recheck_existing_report(
                root=root,
                rom=rom,
                report_path=report_path,
                trace_path=trace_path,
                expected_sha256=args.expected_sha256,
            )
        except Exception as error:
            report = _failure_report(
                rom_sha256=sha256_file(rom) if rom.is_file() else "",
                error=error,
                provenance={},
            )
        write_json(report_path, report)
        passed = report.get("status") == "pass"
        print(
            "Milestone 4 echo-layer audio evidence recheck: "
            f"{'PASS' if passed else 'FAIL'}"
        )
        return 0 if passed else 1
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    provenance: dict[str, Any] = {}
    rom_sha256 = ""
    report: dict[str, Any]
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "live M4 audio evidence requires native Windows BlastEm"
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
        bindings = _verify_default_m4_bindings(default_config)
        if bindings["status"] != "pass":
            raise EvidenceFailure(
                f"pinned input/VGM bindings failed: {bindings['bindings']}"
            )
        symbols = parse_vasm_symbols(symbol_path)
        missing = [name for name in REQUIRED_SYMBOLS if name not in symbols]
        if missing:
            raise EvidenceFailure(
                f"Debug symbol map is missing required symbols {missing}"
            )
        user_config = verify_no_user_blastem_override()
        binding_before = source_binding(root)
        provenance = {
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "rom": {
                "path": rom.relative_to(root).as_posix(),
                "sha256": rom_sha256,
            },
            "emulator": {
                "path": emulator.relative_to(root).as_posix(),
                "sha256": sha256_file(emulator),
            },
            "default_config": bindings,
            "symbol_map": {
                "path": symbol_path.relative_to(root).as_posix(),
                "sha256": sha256_file(symbol_path),
                "symbols": {
                    name: f"{symbols[name]:08X}" for name in REQUIRED_SYMBOLS
                },
            },
            "user_config_preflight": user_config,
            "source_binding_before": binding_before,
            "serialized_lock": lock_path.relative_to(root).as_posix(),
            "launch_flags": list(LAUNCH_FLAGS),
        }
        with exclusive_file_lock(
            lock_path, args.lock_timeout_seconds
        ) as lock:
            provenance["lock"] = lock
            session, capture = _run_live_session(
                root=root,
                rom=rom,
                emulator_directory=emulator_directory,
                default_config=default_config,
                symbols=symbols,
                timeout_seconds=args.timeout_seconds,
                capture_dir=capture_dir,
                transcript=transcript,
            )
        if sha256_file(rom) != rom_sha256:
            raise EvidenceFailure("Debug ROM changed during audio capture")
        binding_after = source_binding(root)
        provenance["source_binding_after"] = binding_after
        if binding_after != binding_before:
            raise EvidenceFailure("audio-relevant sources changed during capture")
        report = build_audio_report(
            root=root,
            rom_sha256=rom_sha256,
            capture_path=capture,
            session=session,
            provenance=provenance,
        )
    except Exception as error:
        report = _failure_report(
            rom_sha256=rom_sha256,
            error=error,
            provenance=provenance,
        )
    finally:
        trace_payload = (
            "\n".join(transcript) + ("\n" if transcript else "")
        )
        trace_path.write_text(
            trace_payload, encoding="utf-8", newline="\n"
        )
        provenance.setdefault(
            "transcript",
            {
                "path": trace_path.relative_to(root).as_posix(),
                "sha256": _sha256_bytes(trace_payload.encode("utf-8")),
            },
        )
        report["provenance"] = provenance
        write_json(report_path, report)

    passed = report.get("status") == "pass"
    print(f"Milestone 4 echo-layer audio evidence: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
