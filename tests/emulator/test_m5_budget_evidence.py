"""Frozen 140039e3 budget-contract fixtures, not current-candidate acceptance.

The synthetic reports below use the exact archived ROM/symbol pair that the
production collector pins. Current builds are independently rejected by that
frozen contract; their live budget and timing gates remain pending separately.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from tools import m5_b03_runtime_evidence as b03
from tools.m2_evidence import EvidenceFailure, parse_vasm_symbols
from tools.m3_runtime_evidence import (
    ENGINE_CONTROL_USED_BYTES,
    INPUT_STATE_BYTES,
    OBJECT_BYTES,
    POOL_LAYOUT,
)
from tools.m4_budget_evidence import (
    AUDIO_STATE_PLAYING,
    AUDIO_STATUS_ABI_VERSION,
    AUDIO_STATUS_FLAGS_HEALTHY,
    AUDIO_STATUS_SIGNATURE,
    DEBUG_ABI_SIZE,
    DEBUG_ABI_VERSION,
    DEBUG_BUDGET_BYTES,
    DEBUG_MAGIC,
    DMA_QUEUE_CONTROL_BYTES,
    M2_HEALTHY_FLAGS,
    RENDERER_BYTES,
    RENDERER_MAGIC,
    RENDERER_STATE_USED_BYTES,
    RENDERER_VERSION,
)
from tools.m5_budget_evidence import (
    CHECKPOINT_HOLD_TOTALS,
    CHECKPOINT_OBSERVATION_TOTALS,
    CHECKPOINT_DMA_BYTES,
    M5_RENDERER_PRIVATE_BYTES,
    RESET_TOTALS,
    ZERO_DMA_TOTALS,
    EXPECTED_PHYSICAL_FRAMES,
    checkpoint_renderer_errors,
    expected_physical_frame,
    expected_audio_shadow_age,
    expected_advance_events,
    BASE_BLOCK_SPECS,
    BLOCK_SPECS,
    CADENCE_PARSED_FIELD_CONTRACT,
    CAPTURE_TICKS_SHA256,
    CHECKPOINT_KEYS_SHA256,
    COMBINED_SCHEDULE_SHA256,
    DIAGNOSTIC_EVIDENCE_TYPE,
    DIAGNOSTIC_SCHEMA,
    OVERRUN_COUNTER_ADDRESS,
    OVERRUN_ORIGIN_DIAGNOSTIC_EVIDENCE_TYPE,
    OVERRUN_ORIGIN_DIAGNOSTIC_SCHEMA,
    OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
    OVERRUN_ORIGIN_SITES,
    EXPECTED_CAPTURE_LABELS,
    EXPECTED_AUTHORITATIVE_STEPS,
    EXPECTED_SESSION_IDS,
    BLASTEM_SOURCE_HASHES,
    BLASTEM_SOURCE_NODE,
    BLASTEM_VERSION,
    HASH_BINDING_NAMES,
    LIMITATIONS,
    PINNED_ROM_SHA256,
    PINNED_DEFAULT_CONFIG_SHA256,
    PINNED_EMULATOR_SHA256,
    PINNED_RUNTIME_REPLAY_SHA256,
    PINNED_RUNTIME_REPORT_SHA256,
    PINNED_RUNTIME_TRACE_SHA256,
    PINNED_SYMBOL_SHA256,
    POOL_BLOCK_SPECS,
    PROGRAM_STABLE_TERMINAL,
    REPORT_SCHEMA,
    REQUIRED_SYMBOLS,
    REQUIRED_CHECK_NAMES,
    SCENARIO_BLOCK_SPECS,
    SCHEDULE_SHA256,
    VOCAB_BYTES,
    VOCAB_CPU_WORST_UNITS,
    VOCAB_HEADER_BYTES,
    VOCAB_MAGIC,
    VOCAB_VERSION,
    M2_STARTUP_FLAGS,
    PAUSE_OVERWRITE_TIMEOUT_SECONDS,
    SET_TITLE_TIMEOUT_SECONDS,
    _sentinel_for,
    _block_evidence,
    _chunk_plan,
    _consume_read_plan,
    _trace_read_plan,
    audit_frame_advance_transcript,
    audit_overrun_origin_transcript,
    build_cadence_row_from_raw,
    cadence_row_errors,
    diagnostic_output_path_errors,
    expected_audio_for_total,
    expected_checkpoint_for_total,
    expected_controller_input_state,
    expected_engine_input_state,
    expected_input_for_total,
    expected_route_for_total,
    frame_advance_source_contract,
    overrun_counter_isolation,
    overrun_origin_site_contract,
    _runtime_prerequisite_errors,
    build_budget_report,
    build_observation_from_raw,
    parse_pool_budget,
    parse_vocabulary_budget,
    report_contract_errors,
    report_exit_code,
    schedule_contract,
    symbol_layout_errors,
)


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
CANONICAL_REPLAY = (
    ROOT / "artifacts/replays/m5-b03-live-replay-ram.bin"
).read_bytes()
FROZEN_B03_DIRECTORY = (
    ROOT / "artifacts/archive/m5-pre-timing-140039e3-20261010/level-B03"
)
FROZEN_B03_ROM = FROZEN_B03_DIRECTORY / "recursion94-debug.bin"
FROZEN_B03_SYMBOL_PATH = FROZEN_B03_DIRECTORY / "recursion94-debug.sym"
FROZEN_B03_SYMBOLS = parse_vasm_symbols(FROZEN_B03_SYMBOL_PATH)


def _canonical_scenario_evidence() -> list[dict]:
    lines = (
        ROOT / "artifacts/traces/m5-b03-runtime-gdb-transcript.log"
    ).read_text(encoding="utf-8").splitlines()
    begin = lines.index("# positive_session_1 begin")
    end = lines.index("# positive_session_1 end", begin + 1)
    segment = lines[begin + 1 : end]
    regions = _trace_read_plan("scenario")
    first_command = f"> m{regions[0][1]:x},{regions[0][2]:x}"
    captures: list[dict[str, bytes]] = []
    cursor = 0
    while cursor < len(segment):
        if segment[cursor] != first_command:
            cursor += 1
            continue
        try:
            next_cursor, raw = _consume_read_plan(
                segment, cursor, regions=regions
            )
        except ValueError:
            cursor += 1
            continue
        captures.append(raw)
        cursor = next_cursor
    if len(captures) != len(EXPECTED_CAPTURE_LABELS):
        raise AssertionError(
            f"canonical runtime trace exposed {len(captures)} B03 "
            f"snapshots; {len(EXPECTED_CAPTURE_LABELS)} required"
        )
    result = []
    for label, raw_by_name in zip(EXPECTED_CAPTURE_LABELS, captures):
        blocks = {}
        bundle = bytearray()
        for name, _private, address, _size in SCENARIO_BLOCK_SPECS:
            raw = raw_by_name[name]
            blocks[name] = _block_evidence(address, raw)
            bundle.extend(raw)
        result.append(
            {
                "label": label,
                "blocks": blocks,
                "raw_bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            }
        )
    return result


CANONICAL_SCENARIO_EVIDENCE = _canonical_scenario_evidence()


def _put_u16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = value.to_bytes(2, "big")


def _put_u32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = value.to_bytes(4, "big")


def _route(label: str) -> tuple[int, int, int]:
    if label == "initial":
        return (1, 0, 0)
    if label == "post_timeout_1":
        return (1, 1, 0)
    if label == "post_timeout_2":
        return (1, 2, 0)
    if label == "terminal":
        return (2, 2, 292)
    prefix, tick_text = label.rsplit("_tick_", 1)
    return (1, int(prefix[-1]), int(tick_text))


CANONICAL_SCENARIO_ENGINE_BY_TOTAL = {
    attempt * 720 + tick: bytes.fromhex(
        item["blocks"]["engine_control"]["raw_hex"]
    )
    for item in CANONICAL_SCENARIO_EVIDENCE
    for _state, attempt, tick in (_route(item["label"]),)
    if item["label"] != "initial"
}
CANONICAL_SCENARIO_CRC_BY_CHECKPOINT = {
    (
        int.from_bytes(raw[0x0E:0x10], "big"),
        int.from_bytes(raw[0x1E:0x20], "big"),
        int.from_bytes(raw[0x20:0x22], "big"),
    ): int.from_bytes(raw[0x1A:0x1E], "big")
    for item in CANONICAL_SCENARIO_EVIDENCE
    for raw in (
        bytes.fromhex(item["blocks"]["engine_control"]["raw_hex"]),
    )
}


def synthetic_raw_blocks(label: str) -> dict[str, bytes]:
    state, attempt, tick = _route(label)
    total = attempt * 720 + tick
    physical = expected_physical_frame(total)
    pool_counts = [0, 0, 5, 0, 1, 0, 0]

    engine = bytearray(ENGINE_CONTROL_USED_BYTES)
    _put_u32(engine, 0x00, 0x52393445)
    _put_u16(engine, 0x04, 1)
    _put_u16(engine, 0x06, 0x0400)
    _put_u16(engine, 0x08, state)
    _put_u16(engine, 0x0A, 0x0003)
    _put_u16(engine, 0x0C, b03.B03_STAGE_ID)
    _put_u16(engine, 0x0E, attempt)
    _put_u16(engine, 0x10, tick)
    _put_u16(
        engine,
        0x12,
        PROGRAM_STABLE_TERMINAL if state == 2 else 0,
    )
    _put_u16(engine, 0x18, b03.CAMPAIGN_PROJECTION_BYTES)
    _put_u32(engine, 0x1A, 0x12345678)
    engine[0x27] = min(attempt + 1, 3)
    engine[0x40:0x47] = bytes(pool_counts)
    engine[0x48:0x4F] = bytes(pool_counts)

    debug = bytearray(DEBUG_BUDGET_BYTES)
    _put_u32(debug, 0x00, DEBUG_MAGIC)
    _put_u16(debug, 0x04, DEBUG_ABI_VERSION)
    _put_u16(debug, 0x06, DEBUG_ABI_SIZE)
    _put_u32(debug, 0x08, physical * 2)
    _put_u32(debug, 0x0C, physical)
    _put_u32(debug, 0x10, physical)
    _put_u16(debug, 0x14, 2 if total in RESET_TOTALS else 0)
    _put_u16(debug, 0x18, 3 if label == "initial" else 4)
    _put_u16(debug, 0x24, 20)
    _put_u16(debug, 0x26, 5)
    _put_u16(debug, 0x28, 440)
    _put_u16(debug, 0x2A, 440)
    _put_u16(debug, 0x2E, 4)
    _put_u16(debug, 0x34, AUDIO_STATE_PLAYING)
    z80_heartbeat = 0 if label == "initial" else physical + 6 - expected_audio_shadow_age(total)
    _put_u16(debug, 0x36, z80_heartbeat)
    if label == "initial":
        _put_u16(debug, 0x38, 0)
        _put_u16(debug, 0x3A, M2_STARTUP_FLAGS)
    else:
        audio_expected = expected_audio_for_total(total)
        _put_u16(
            debug, 0x38, audio_expected["command_count"]
        )
        _put_u16(debug, 0x3A, M2_HEALTHY_FLAGS)

    renderer = bytearray(RENDERER_STATE_USED_BYTES)
    _put_u32(renderer, 0x00, RENDERER_MAGIC)
    _put_u16(renderer, 0x04, RENDERER_VERSION)
    _put_u16(renderer, 0x06, RENDERER_BYTES)
    _put_u16(renderer, 0x08, 0x0001)
    _put_u16(renderer, 0x0A, attempt)
    _put_u16(renderer, 0x0E, 0xFFFF)
    _put_u16(renderer, 0x10, 0xFFFF)
    _put_u16(renderer, 0x12, 0xFFFF)
    _put_u16(renderer, 0x14, 20)
    _put_u16(renderer, 0x16, 5)
    _put_u16(renderer, 0x18, 96)
    _put_u16(renderer, 0x20, max(tick - 1, 0))

    dma = bytearray(DMA_QUEUE_CONTROL_BYTES)
    _put_u16(dma, 0x08, 440)

    audio = bytearray(0x10)
    if label != "initial":
        audio_expected = expected_audio_for_total(total)
        audio[0x00] = AUDIO_STATUS_SIGNATURE
        audio[0x01] = AUDIO_STATUS_ABI_VERSION
        audio[0x02] = AUDIO_STATE_PLAYING
        audio[0x03] = AUDIO_STATUS_FLAGS_HEALTHY
        audio[0x04:0x06] = z80_heartbeat.to_bytes(2, "little")
        audio[0x06] = audio_expected["queue_read_sequence"]
        audio[0x07] = 1
        audio[0x08:0x0A] = audio_expected[
            "command_count"
        ].to_bytes(2, "little")
        audio[0x0B] = 10
        audio[0x0C] = 10
        audio[0x0D] = 2
        audio[0x0E] = audio_expected["active_layers"]
        audio[0x0F] = audio_expected["last_command"]

    vocabulary = bytearray(VOCAB_HEADER_BYTES)
    _put_u32(vocabulary, 0x00, VOCAB_MAGIC)
    _put_u16(vocabulary, 0x04, VOCAB_VERSION)
    _put_u16(vocabulary, 0x06, VOCAB_BYTES)
    _put_u16(vocabulary, 0x08, b03.VOCAB_PROJECTION_BYTES)
    _put_u16(vocabulary, 0x0A, 0x0001)
    units = (
        0
        if label in {"initial", "post_timeout_1", "post_timeout_2"}
        else VOCAB_CPU_WORST_UNITS
    )
    _put_u16(vocabulary, 0x10, units)
    _put_u16(vocabulary, 0x12, units)
    _put_u16(vocabulary, 0x16, b03.B03_STAGE_ID)
    _put_u16(vocabulary, 0x1A, attempt + 1)
    vocabulary[0x20] = attempt
    vocabulary[0x23] = 2

    result = {
        "engine_control": bytes(engine),
        "debug_budget": bytes(debug),
        "renderer_state": bytes(renderer),
        "dma_queue_control": bytes(dma),
        "audio_shadow": bytes(audio),
        "vocabulary_header": bytes(vocabulary),
    }
    for pool_type, name, _address, capacity in POOL_LAYOUT:
        raw = bytearray(capacity * OBJECT_BYTES)
        count = pool_counts[pool_type - 1]
        for slot in range(count):
            offset = slot * OBJECT_BYTES
            raw[offset] = 1
            raw[offset + 1] = slot
        result[f"pool_{name}"] = bytes(raw)
    assert set(result) == {spec[0] for spec in BLOCK_SPECS}
    return result


def synthetic_observation(label: str) -> dict:
    return build_observation_from_raw(
        label=label,
        raw_by_name=synthetic_raw_blocks(label),
    )


def synthetic_checkpoint_raw(total: int, *, terminal_hold: bool = False) -> dict[str, bytes]:
    hold = terminal_hold or total - 1 in CHECKPOINT_HOLD_TOTALS
    source = total if terminal_hold else total - (1 if hold else 2)
    route = expected_route_for_total(source)
    renderer = bytearray(M5_RENDERER_PRIVATE_BYTES)
    _put_u32(renderer, 0, RENDERER_MAGIC)
    _put_u16(renderer, 4, RENDERER_VERSION)
    _put_u16(renderer, 6, RENDERER_BYTES)
    _put_u16(renderer, 8, 1)
    _put_u16(renderer, 0x0A, route["attempt"])
    _put_u16(renderer, 0x12, 0xC180 if source in RESET_TOTALS else 0xFFFF)
    _put_u16(renderer, 0x20, (719 if source in RESET_TOTALS else route["tick"] - 1) if hold else expected_route_for_total(total)["tick"] - 1)
    _put_u16(renderer, 0x22, 4 if hold else 0)
    _put_u16(renderer, 0x24, expected_checkpoint_for_total(source)[1])
    dma = bytearray(CHECKPOINT_DMA_BYTES)
    words = (440 if terminal_hold else 0) if hold else (480 if source in RESET_TOTALS else 440)
    _put_u16(dma, 8, words)
    records = [(0xFF8000, 0xF000, 320, 0xC000), (0xFF8600, 0xC000, 40, 0xC001), (0xFF8650, 0xC080, 40, 0xC001), (0xFF86A0, 0xC100, 40, 0xC001), (0xFF8740, 0xC180, 40, 0xC002)]
    for index, (address, destination, count, flags) in enumerate(records):
        offset = 0x10 + index * 12
        _put_u32(dma, offset, address)
        _put_u16(dma, offset + 4, destination)
        _put_u16(dma, offset + 6, count)
        _put_u16(dma, offset + 8, flags)
    return {"renderer_private": bytes(renderer), "dma_recovery": bytes(dma)}


def synthetic_cadence_row(total: int, *, terminal_hold: bool = False) -> dict:
    physical = expected_physical_frame(total)
    route = expected_route_for_total(total)
    engine_input = expected_engine_input_state(total)
    controller_input = expected_controller_input_state(total)
    checkpoint_tick, checkpoint_count = expected_checkpoint_for_total(total)
    audio_expected = expected_audio_for_total(total)
    engine = bytearray(ENGINE_CONTROL_USED_BYTES)
    _put_u32(engine, 0x00, 0x52393445)
    _put_u16(engine, 0x04, 1)
    _put_u16(engine, 0x06, 0x0400)
    _put_u16(engine, 0x08, route["state"])
    _put_u16(engine, 0x0A, 0x0003)
    _put_u16(engine, 0x0C, b03.B03_DIRECTORY_INDEX)
    _put_u16(engine, 0x0E, route["attempt"])
    _put_u16(engine, 0x10, route["tick"])
    _put_u16(
        engine,
        0x12,
        PROGRAM_STABLE_TERMINAL
        if route["state"] == 2
        else 0,
    )
    _put_u16(engine, 0x18, b03.CAMPAIGN_PROJECTION_BYTES)
    _put_u16(engine, 0x1E, checkpoint_tick)
    _put_u16(engine, 0x20, checkpoint_count)
    checkpoint_key = (
        route["attempt"],
        checkpoint_tick,
        checkpoint_count,
    )
    synthetic_crc = (
        0xA5000000
        | (route["attempt"] << 20)
        | (checkpoint_tick << 4)
        | checkpoint_count
    )
    _put_u32(
        engine,
        0x1A,
        CANONICAL_SCENARIO_CRC_BY_CHECKPOINT.get(
            checkpoint_key, synthetic_crc
        ),
    )
    engine[0x22] = engine_input["held"]
    engine[0x23] = engine_input["pressed"]
    engine[0x24] = engine_input["released"]
    engine[0x25] = engine_input["previous"]
    engine[0x26] = engine_input["facing_x"] & 0xFF
    engine[0x27] = min(route["attempt"] + 1, 3)
    _put_u16(engine, 0x28, b03.B03_ROOM[0])
    _put_u16(engine, 0x2A, b03.B03_ROOM[1])
    _put_u16(engine, 0x2C, b03.B03_ROOM[2])
    _put_u16(engine, 0x2E, b03.B03_ROOM[3])
    _put_u32(engine, 0x30, b03.B03_SPAWN[0] << 8)
    _put_u32(engine, 0x34, b03.B03_SPAWN[1] << 8)
    _put_u16(engine, 0x38, len(b03.B03_RECTS))
    _put_u16(engine, 0x3A, len(b03.B03_DEVICES) + 1)
    engine[0x40:0x47] = bytes((0, 0, 5, 0, 1, 0, 0))
    engine[0x48:0x4F] = bytes((0, 0, 5, 0, 1, 0, 0))
    if total in CANONICAL_SCENARIO_ENGINE_BY_TOTAL:
        engine = bytearray(CANONICAL_SCENARIO_ENGINE_BY_TOTAL[total])

    debug = bytearray(DEBUG_BUDGET_BYTES)
    _put_u32(debug, 0x00, DEBUG_MAGIC)
    _put_u16(debug, 0x04, DEBUG_ABI_VERSION)
    _put_u16(debug, 0x06, DEBUG_ABI_SIZE)
    _put_u32(debug, 0x08, physical * 2)
    _put_u32(debug, 0x0C, physical)
    _put_u32(debug, 0x10, physical)
    _put_u16(debug, 0x14, 2 if total in RESET_TOTALS else 0)
    _put_u16(debug, 0x18, 4)
    _put_u16(debug, 0x1C, 0x0E08)
    _put_u16(debug, 0x24, 20)
    _put_u16(debug, 0x26, 5)
    _put_u16(debug, 0x28, 0 if total in ZERO_DMA_TOTALS else 480 if total - 2 in RESET_TOTALS else 440)
    _put_u16(debug, 0x2A, 520 if total >= 722 else 440)
    _put_u16(debug, 0x2E, 4)
    _put_u16(debug, 0x34, AUDIO_STATE_PLAYING)
    _put_u16(debug, 0x36, physical + 6 - expected_audio_shadow_age(total))
    _put_u16(debug, 0x38, audio_expected["command_count"])
    _put_u16(debug, 0x3A, M2_HEALTHY_FLAGS)
    debug[0x1E] = controller_input["held"]
    debug[0x1F] = controller_input["pressed"]
    debug[0x20] = controller_input["released"]
    debug[0x21] = controller_input["connected"]
    debug[0x22] = controller_input["start_held"]
    _put_u32(debug, 0x3C, physical)

    input_raw = bytearray(INPUT_STATE_BYTES)
    input_raw[0] = controller_input["held"]
    input_raw[1] = controller_input["pressed"]
    input_raw[2] = controller_input["released"]
    input_raw[3] = controller_input["raw_high"]
    input_raw[4] = controller_input["raw_low"]
    input_raw[5] = controller_input["connected"]
    input_raw[6] = controller_input["start_held"]
    return build_cadence_row_from_raw(
        authoritative_total=total,
        engine_raw=bytes(engine),
        debug_raw=bytes(debug),
        input_raw=bytes(input_raw),
        checkpoint_raw=synthetic_checkpoint_raw(total, terminal_hold=terminal_hold) if total in CHECKPOINT_OBSERVATION_TOTALS or terminal_hold else None,
    )


def synthetic_frame_audit() -> dict:
    return {
        "status": "pass",
        "expected_authoritative_steps": EXPECTED_AUTHORITATIVE_STEPS,
        "vblank_breakpoint_insertions": 1,
        "vblank_breakpoint_removals": 1,
        "post_breakpoint_insertions": 1,
        "post_breakpoint_removals": 1,
        "finalizer_breakpoint_insertions": 1,
        "finalizer_breakpoint_removals": 1,
        "wait_breakpoint_insertions": 1,
        "wait_breakpoint_removals": 1,
        "post_stops": EXPECTED_AUTHORITATIVE_STEPS,
        "finalizer_stops": 1,
        "wait_stops": 1,
        "exact_post_total_sequence": True,
        "exact_pc_sequence": True,
        "sentinel_handshakes": EXPECTED_PHYSICAL_FRAMES,
        "exact_sentinel_boundary_sequence": True,
        "pause_overwrites": EXPECTED_PHYSICAL_FRAMES,
        "ui_advance_pulses": EXPECTED_PHYSICAL_FRAMES + 1,
        "exact_ui_advance_sequence": True,
        "memory_read_sections": 1871,
        "memory_read_pairs": 8121,
        "memory_read_bundle_sha256": SHA,
        "trace_report_binding_errors": [],
        "trace_report_bindings_exact": True,
        "transcript_command_count": 11600,
        "exact_event_order": True,
        "protocol_event_count": 14031,
        "expected_protocol_event_count": 14031,
        "malformed_protocol_lines": [],
        "forbidden_control": [],
        "debugger_write_packets": [],
    }


def synthetic_session(number: int) -> dict:
    lines = [f"# synthetic session {number}", "> k"]
    transcript_payload = ("\n".join(lines) + "\n").encode()
    runtime = {
        "status": "pass",
        "temporary_directory": f"C:/Temp/m5-budget-{number}/blastem",
        "executable_sha256": PINNED_EMULATOR_SHA256,
        "default_config_sha256": PINNED_DEFAULT_CONFIG_SHA256,
        "temporary_copy_removed_after_session": True,
    }
    return {
        "session_id": EXPECTED_SESSION_IDS[number - 1],
        "metadata": {
            "process_id": 1000 + number,
            "launch_flags": list(b03.LAUNCH_FLAGS),
            "session_mode": "positive",
            "initial_breakpoint_symbol": "VBlankInterrupt",
            "initial_breakpoint": "00000100",
            "step_breakpoint_symbol": "MainLoopPostGameplayStep",
            "step_breakpoint": "00000336",
            "terminal_pre_post_symbol": "VocabularyFinalizeStage",
            "terminal_pre_post_breakpoint": "0000554A",
            "terminal_wait_symbol": "MainLoopWaitForVBlank",
            "terminal_wait_breakpoint": "00000340",
            "drive_protocol": "synthetic responsive pause protocol",
            "input_transport": "targeted_PostMessageW",
            "frame_advance_transport": (
                "targeted_PostMessageW_F8_ui.advance"
            ),
            "input_edge_settle_milliseconds": (
                b03.INPUT_EDGE_SETTLE_SECONDS * 1000
            ),
            "input_events": [],
            "frame_advance_audit": synthetic_frame_audit(),
            "origin_pause": {
                "status": "pass",
                "initial_title": "RECURSION 94",
                "observed_title": "RECURSION 94 - PAUSED",
                "elapsed_milliseconds": 1.0,
                "samples": 2,
                "timeout_seconds": PAUSE_OVERWRITE_TIMEOUT_SECONDS,
            },
            "sentinel_handshakes": [
                {
                    "boundary_total": boundary,
                    "sentinel": _sentinel_for(
                        EXPECTED_SESSION_IDS[number - 1], boundary
                    ),
                    "sentinel_readback": _sentinel_for(
                        EXPECTED_SESSION_IDS[number - 1], boundary
                    ),
                    "set_window_text": (
                        "SendMessageTimeoutW("
                        "WM_SETTEXT,SMTO_ABORTIFHUNG)"
                    ),
                    "set_timeout_seconds": SET_TITLE_TIMEOUT_SECONDS,
                    "set_elapsed_milliseconds": 0.5,
                    "status": "pass",
                    "observed_title": "RECURSION 94 - PAUSED",
                    "elapsed_milliseconds": 1.0,
                    "samples": 2,
                    "timeout_seconds": (
                        PAUSE_OVERWRITE_TIMEOUT_SECONDS
                    ),
                }
                for boundary in range(EXPECTED_PHYSICAL_FRAMES)
            ],
            "ui_advance_events": [
                {
                    "index": index,
                    "purpose": purpose,
                    "key": "F8",
                    "complete_down_up_pulse": True,
                }
                for index, purpose in expected_advance_events()
            ],
            "frame_advance_source_contract": {
                "version": BLASTEM_VERSION,
                "source_node": BLASTEM_SOURCE_NODE,
                "source_sha256": BLASTEM_SOURCE_HASHES,
            },
            "runtime_copy": runtime,
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "session_isolation": {"status": "pass"},
            },
            "symbol_map_sha256": PINNED_SYMBOL_SHA256,
            "transcript_sha256": hashlib.sha256(
                transcript_payload
            ).hexdigest(),
        },
        "route": {
            "status": "pass",
            "scenario_facts": {
                "timed_switch_activated": True,
                "first_timeout_echo_committed": True,
                "security_node_exposed": True,
                "pressure_echo_holds_switch": True,
                "two_timeout_echoes_committed": True,
                "node_damage_owned_by_echo_replay": True,
                "current_blocked_west_until_open": True,
                "timed_door_and_commit_unlock_opened": True,
                "current_dashes_through_open_gate": True,
                "current_only_completion": True,
            },
            "schedule_proof": {
                "status": "pass",
                "attempts": [
                    {
                        "attempt": index,
                        "steps": steps,
                        "sha256": SCHEDULE_SHA256[index],
                        "commanded_equals_expected": True,
                        "consumed_equals_expected": True,
                        "post_observed_nonreset_equals_expected": True,
                        "timeout_replay_proves_final_step": True,
                    }
                    for index, steps in enumerate((720, 720, 292))
                ],
                "total_steps": 1732,
                "combined_sha256": COMBINED_SCHEDULE_SHA256,
            },
            "checkpoint_labels": list(EXPECTED_CAPTURE_LABELS),
            "scenario_snapshots": copy.deepcopy(
                CANONICAL_SCENARIO_EVIDENCE
            ),
            "cadence_rows": [
                synthetic_cadence_row(total)
                for total in range(
                    1, EXPECTED_AUTHORITATIVE_STEPS + 1
                )
            ],
            "timeout_commits": [
                {
                    "status": "pass",
                    "attempt": attempt,
                    "authoritative_total": (attempt + 1) * 720,
                    "committed_bank": 6 if attempt == 0 else 0,
                    "descriptor_length": len(schedule),
                    "descriptor_crc32_iso_hdlc": (
                        b03.crc32_iso_hdlc(schedule)
                    ),
                    "descriptor_flags": (
                        b03.REPLAY_FLAG_VALID | b03.REPLAY_FLAG_ECHO
                    ),
                    "committed_input_sha256": SCHEDULE_SHA256[attempt],
                    "committed_input_raw_hex": schedule.hex().upper(),
                }
                for attempt, schedule in enumerate(
                    (
                        b03.ATTEMPT_0_INPUTS,
                        b03.ATTEMPT_1_INPUTS,
                    )
                )
            ],
            "terminal_wait_observation": synthetic_cadence_row(
                EXPECTED_AUTHORITATIVE_STEPS, terminal_hold=True
            ),
            "terminal_replay": {
                "bytes": len(CANONICAL_REPLAY),
                "sha256": hashlib.sha256(
                    CANONICAL_REPLAY
                ).hexdigest(),
                "raw_hex": CANONICAL_REPLAY.hex().upper(),
            },
        },
        "observations": [
            synthetic_observation(label)
            for label in EXPECTED_CAPTURE_LABELS
        ],
        "_transcript_lines": lines,
    }


def synthetic_provenance(sessions: list[dict]) -> dict:
    provenance = {
        name: {
            "path": f"synthetic/{name}",
            "sha256": SHA,
        }
        for name in HASH_BINDING_NAMES
    }
    provenance["rom"]["sha256"] = PINNED_ROM_SHA256
    provenance["symbol_map"]["sha256"] = PINNED_SYMBOL_SHA256
    provenance["emulator"]["sha256"] = PINNED_EMULATOR_SHA256
    provenance["default_config"]["sha256"] = (
        PINNED_DEFAULT_CONFIG_SHA256
    )
    provenance["runtime_report"]["sha256"] = (
        PINNED_RUNTIME_REPORT_SHA256
    )
    provenance["runtime_trace"]["sha256"] = (
        PINNED_RUNTIME_TRACE_SHA256
    )
    provenance["runtime_replay"]["sha256"] = (
        PINNED_RUNTIME_REPLAY_SHA256
    )
    source_hashes = {
        path: hashlib.sha256(path.encode("utf-8")).hexdigest()
        for path in b03.SOURCE_BINDING_PATHS
    }
    source_binding_payload = json.dumps(
        source_hashes,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    before_after = {
        name: {
            "before": provenance[name]["sha256"],
            "after": provenance[name]["sha256"],
        }
        for name in HASH_BINDING_NAMES
    }
    lines = [
        line
        for session in sessions
        for line in session["_transcript_lines"]
    ]
    payload = ("\n".join(lines) + "\n").encode()
    provenance["hash_verification"] = {
        "status": "pass",
        "drift": [],
        "before_after": before_after,
    }
    provenance["transcript"] = {
        "path": "synthetic/trace",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "commands": sum(line.startswith("> ") for line in lines),
    }
    provenance.update(
        {
            "fixture": {
                "directory_index": 2,
                "stage_id": b03.B03_STAGE_ID,
                "debug_start_encounter": 2,
            },
            "required_symbols": {
                name: f"{FROZEN_B03_SYMBOLS[name]:08X}"
                for name in REQUIRED_SYMBOLS
            },
            "default_config_contract": {"status": "pass"},
            "frame_advance_source_contract": {
                "status": "pass",
                "blastem_version": BLASTEM_VERSION,
                "source_node": BLASTEM_SOURCE_NODE,
                "source_sha256": BLASTEM_SOURCE_HASHES,
                "default_config_sha256": (
                    PINNED_DEFAULT_CONFIG_SHA256
                ),
                "bindings": {
                    "frame_advance": "f8 ui.advance",
                    "pause_forbidden_during_drive": "f7 ui.pause",
                    "frame_advance_matches": 1,
                    "pause_matches": 1,
                },
                "source_semantics": {
                    "ui_action_edge": "KEYUP",
                    "ui_advance": "synthetic pinned semantic",
                    "frame_end": "synthetic pinned semantic",
                    "pause_caption": "synthetic pinned semantic",
                    "gdb_stop": "synthetic pinned semantic",
                    "queue_bound": "synthetic pinned semantic",
                },
            },
            "user_config_preflight": {"status": "pass"},
            "pre_step_rom_binding": {"status": "pass"},
            "source_bindings": {
                "bundle_sha256": hashlib.sha256(
                    source_binding_payload
                ).hexdigest(),
                "sources": source_hashes,
            },
            "serialized_lock": "synthetic/lock",
            "launch_flags": list(b03.LAUNCH_FLAGS),
            "lock": {"status": "pass"},
        }
    )
    return provenance


def prerequisite() -> dict:
    return {
        "status": "pass",
        "report_sha256": PINNED_RUNTIME_REPORT_SHA256,
        "trace_sha256": PINNED_RUNTIME_TRACE_SHA256,
        "replay_sha256": PINNED_RUNTIME_REPLAY_SHA256,
    }


def build_report(
    sessions: list[dict] | None = None,
    provenance: dict | None = None,
) -> dict:
    if sessions is None:
        sessions = [synthetic_session(1), synthetic_session(2)]
    if provenance is None:
        provenance = synthetic_provenance(sessions)
    return build_budget_report(
        sessions=sessions,
        provenance=provenance,
        prerequisite=prerequisite(),
        schedule=schedule_contract(),
    )


def check_status(report: dict, name: str) -> str:
    return next(
        item["status"] for item in report["checks"]
        if item["check"] == name
    )


def _raw_blocks(item: dict) -> dict[str, bytes]:
    return {
        name: bytes.fromhex(block["raw_hex"])
        for name, block in item["blocks"].items()
    }


def _emit_memory_section(
    lines: list[str],
    *,
    kind: str,
    identity: str,
    blocks: dict[str, bytes],
) -> None:
    lines.append(f"# memory.begin kind={kind} id={identity}")
    consumed = {name: 0 for name in blocks}
    for name, address, amount in _chunk_plan(_trace_read_plan(kind)):
        start = consumed[name]
        raw = blocks[name][start : start + amount]
        if len(raw) != amount:
            raise AssertionError(
                f"{kind}/{identity}/{name} lacks {amount} bytes"
            )
        lines.extend(
            [
                f"> m{address:x},{amount:x}",
                f"< {raw.hex().upper()}",
            ]
        )
        consumed[name] += amount
    lines.append(f"# memory.end kind={kind} id={identity}")


def strict_session_transcript(
    session: dict,
    *,
    vblank: int,
    post: int,
    finalizer: int,
    wait_for_vblank: int,
) -> list[str]:
    session_id = session["session_id"]
    scenario = {
        item["label"]: item
        for item in session["route"]["scenario_snapshots"]
    }
    observations = {
        item["label"]: item for item in session["observations"]
    }
    lines = [
        f"# {session_id} begin",
        f"> Z0,{vblank:x},2",
        "< OK",
        "> c",
        "< S05",
    ]
    _emit_memory_section(
        lines,
        kind="scenario",
        identity="initial",
        blocks=_raw_blocks(scenario["initial"]),
    )
    _emit_memory_section(
        lines,
        kind="budget_first",
        identity="initial",
        blocks=_raw_blocks(observations["initial"]),
    )
    _emit_memory_section(
        lines,
        kind="budget_second",
        identity="initial",
        blocks=_raw_blocks(observations["initial"]),
    )
    lines.extend(
        [
            f"> z0,{vblank:x},2",
            "< OK",
            f"> Z0,{post:x},2",
            "< OK",
            f"> Z0,{finalizer:x},2",
            "< OK",
            "# ui.advance index=1 purpose=origin_prime",
            "> c",
            "# cadence.origin_pause title='RECURSION 94 - PAUSED'",
            "# cadence.sentinel "
            f"boundary=0 title={_sentinel_for(session_id, 0)!r}",
        ]
    )
    rows = session["route"]["cadence_rows"]
    for total in range(1, EXPECTED_AUTHORITATIVE_STEPS + 1):
        lines.extend(
            [
                "# ui.advance "
                f"index={expected_physical_frame(total - 1) + 2} "
                f"purpose=authoritative_total_{total}",
            ]
        )
        if total in RESET_TOTALS:
            for service in (1, 2):
                boundary = expected_physical_frame(total - 1) + service - 1
                lines.extend([
                    f"# cadence.pause boundary={boundary} title='RECURSION 94 - PAUSED'",
                    f"# cadence.sentinel boundary={boundary + 1} title={_sentinel_for(session_id, boundary + 1)!r}",
                    f"# ui.advance index={boundary + 3} purpose=reset_{total}_service_{service}",
                ])
        lines.append("< S05")
        if total < EXPECTED_AUTHORITATIVE_STEPS:
            lines.extend(
                [
                    "> p11",
                    f"< {post:08X}",
                    "# cadence.stop "
                    f"total={total} role=post pc={post:08X}",
                ]
            )
        else:
            lines.extend(
                [
                    "> p11",
                    f"< {finalizer:08X}",
                    "# cadence.stop "
                    f"total={total} role=finalizer "
                    f"pc={finalizer:08X}",
                    f"> z0,{finalizer:x},2",
                    "< OK",
                    "> c",
                    "< S05",
                    "> p11",
                    f"< {post:08X}",
                    "# cadence.stop "
                    f"total={total} role=terminal_post "
                    f"pc={post:08X}",
                ]
            )
        _emit_memory_section(
            lines,
            kind="cadence",
            identity=f"{total:04d}",
            blocks=_raw_blocks(rows[total - 1]),
        )
        if total in CHECKPOINT_OBSERVATION_TOTALS:
            _emit_memory_section(lines, kind="checkpoint_renderer", identity=f"{total:04d}", blocks=_raw_blocks(rows[total - 1]["checkpoint_renderer"]))
        label = None
        for candidate in EXPECTED_CAPTURE_LABELS:
            if candidate == "initial":
                continue
            state, attempt, tick = _route(candidate)
            candidate_total = attempt * 720 + tick
            if candidate_total == total:
                label = candidate
                break
        if label is not None:
            _emit_memory_section(
                lines,
                kind="scenario",
                identity=label,
                blocks=_raw_blocks(scenario[label]),
            )
            _emit_memory_section(
                lines,
                kind="budget_first",
                identity=label,
                blocks=_raw_blocks(observations[label]),
            )
            _emit_memory_section(
                lines,
                kind="budget_second",
                identity=label,
                blocks=_raw_blocks(observations[label]),
            )
        if total < EXPECTED_AUTHORITATIVE_STEPS:
            lines.extend(
                [
                    "> c",
                    "# cadence.pause "
                    f"boundary={expected_physical_frame(total) - 1} "
                    "title='RECURSION 94 - PAUSED'",
                    "# cadence.sentinel "
                    f"boundary={expected_physical_frame(total)} "
                    f"title={_sentinel_for(session_id, expected_physical_frame(total))!r}",
                ]
            )
        else:
            lines.extend(
                [
                    f"> Z0,{wait_for_vblank:x},2",
                    "< OK",
                    "> c",
                    "< S05",
                    "> p11",
                    f"< {wait_for_vblank:08X}",
                    "# cadence.stop "
                    f"total={total} role=terminal_wait "
                    f"pc={wait_for_vblank:08X}",
                ]
            )
            _emit_memory_section(
                lines,
                kind="terminal_wait",
                identity=f"{total:04d}",
                blocks=_raw_blocks(
                    session["route"]["terminal_wait_observation"]
                ),
            )
            _emit_memory_section(lines, kind="checkpoint_renderer", identity="terminal", blocks=_raw_blocks(session["route"]["terminal_wait_observation"]["checkpoint_renderer"]))
            lines.extend(
                [
                    f"> z0,{wait_for_vblank:x},2",
                    "< OK",
                    f"> z0,{post:x},2",
                    "< OK",
                    "> c",
                    "# cadence.pause "
                    f"boundary={expected_physical_frame(total) - 1} "
                    "title='RECURSION 94 - PAUSED'",
                ]
            )
    lines.extend(["> k", f"# {session_id} end"])
    return lines


def overrun_origin_transcript_fixture(
    *,
    site: str = "vblank_exit",
) -> tuple[list[str], dict, list[dict], dict]:
    session_id = "budget_overrun_origin_diagnostic"
    vblank = FROZEN_B03_SYMBOLS["VBlankInterrupt"]
    post = FROZEN_B03_SYMBOLS["MainLoopPostGameplayStep"]
    initial = synthetic_session(1)["route"]["scenario_snapshots"][0]
    rows = [synthetic_cadence_row(total) for total in (1, 2, 3)]
    third_blocks = _raw_blocks(rows[2])
    third_debug = bytearray(third_blocks["debug_budget"])
    _put_u16(third_debug, 0x32, 1)
    rows[2] = build_cadence_row_from_raw(
        authoritative_total=3,
        engine_raw=third_blocks["engine_control"],
        debug_raw=bytes(third_debug),
        input_raw=third_blocks["input_state"],
    )
    contract = OVERRUN_ORIGIN_SITES[site]
    address = contract["address"]
    d0 = contract["register_low_words"]["d0"]
    d1 = contract["register_low_words"]["d1"]
    origin_hit = {
        "authoritative_total": 3,
        "site": site,
        "pc": f"{address:08X}",
        "d0": d0,
        "d1": d1,
        "register_low_words": {"d0": d0, "d1": d1},
        "counter_before_increment": _block_evidence(
            OVERRUN_COUNTER_ADDRESS, bytes(2)
        ),
        "meaning": contract["meaning"],
    }
    lines = [
        f"# {session_id} begin",
        f"> Z0,{vblank:x},2",
        "< OK",
        "> c",
        "< S05",
    ]
    _emit_memory_section(
        lines,
        kind="scenario",
        identity="initial",
        blocks=_raw_blocks(initial),
    )
    lines.extend(
        [
            f"> z0,{vblank:x},2",
            "< OK",
            f"> Z0,{post:x},2",
            "< OK",
            "# ui.advance index=1 purpose=origin_prime",
            "> c",
            "# cadence.origin_pause title='RECURSION 94 - PAUSED'",
            "# cadence.sentinel "
            f"boundary=0 title={_sentinel_for(session_id, 0)!r}",
        ]
    )
    for total in (1, 2):
        lines.extend(
            [
                "# ui.advance "
                f"index={total + 1} "
                f"purpose=authoritative_total_{total}",
                "< S05",
                "> p11",
                f"< {post:08X}",
                "# cadence.stop "
                f"total={total} role=post pc={post:08X}",
            ]
        )
        _emit_memory_section(
            lines,
            kind="cadence",
            identity=f"{total:04d}",
            blocks=_raw_blocks(rows[total - 1]),
        )
        if total == 2:
            site_text = ",".join(
                f"{name}={item['address']:08X}"
                for name, item in OVERRUN_ORIGIN_SITES.items()
            )
            lines.append(
                "# overrun_origin.arm boundary=2 sites=" + site_text
            )
            for item in OVERRUN_ORIGIN_SITES.values():
                lines.extend([f"> Z0,{item['address']:x},2", "< OK"])
        lines.extend(
            [
                "> c",
                "# cadence.pause "
                f"boundary={total - 1} "
                "title='RECURSION 94 - PAUSED'",
                "# cadence.sentinel "
                f"boundary={total} "
                f"title={_sentinel_for(session_id, total)!r}",
            ]
        )
    lines.extend(
        [
            "# ui.advance index=4 purpose=authoritative_total_3",
            "< S05",
            "> p11",
            f"< {address:08X}",
            "# overrun_origin.hit total=3 "
            f"site={site} pc={address:08X}",
            "> p0",
            f"< {d0:08X}",
            "> p1",
            f"< {d1:08X}",
            "# memory.begin kind=overrun_counter id=0003",
            f"> m{OVERRUN_COUNTER_ADDRESS:x},2",
            "< 0000",
            "# memory.end kind=overrun_counter id=0003",
        ]
    )
    for item in OVERRUN_ORIGIN_SITES.values():
        lines.extend([f"> z0,{item['address']:x},2", "< OK"])
    lines.extend(
        [
            "> c",
            "< S05",
            "> p11",
            f"< {post:08X}",
            f"# cadence.stop total=3 role=post pc={post:08X}",
        ]
    )
    _emit_memory_section(
        lines,
        kind="cadence",
        identity="0003",
        blocks=_raw_blocks(rows[2]),
    )
    lines.extend(
        [
            f"> z0,{post:x},2",
            "< OK",
            "> c",
            "# cadence.pause boundary=2 "
            "title='RECURSION 94 - PAUSED'",
            "> k",
            f"# {session_id} end",
        ]
    )
    return lines, initial, rows, origin_hit


class M5BudgetEvidenceTests(unittest.TestCase):
    def test_checkpoint_private_proof_rejects_coherent_mutations(self) -> None:
        session = {"route": {
            "cadence_rows": [synthetic_cadence_row(total) for total in sorted(CHECKPOINT_OBSERVATION_TOTALS)],
            "terminal_wait_observation": synthetic_cadence_row(1732, terminal_hold=True),
        }}
        self.assertEqual(len(CHECKPOINT_HOLD_TOTALS), 29)
        self.assertEqual(len(CHECKPOINT_OBSERVATION_TOTALS), 56)
        self.assertEqual(checkpoint_renderer_errors(session), [])
        cases = (
            (61, "renderer_private", 0x22, 2, 0),
            (61, "renderer_private", 0x24, 2, 1),
            (61, "renderer_private", 0x20, 2, 60),
            (61, "renderer_private", 0x1E, 2, 1),
            (61, "renderer_private", 0x18, 2, 321),
            (61, "dma_recovery", 0x0A, 2, 1),
            (61, "dma_recovery", 0x08, 2, 440),
            (62, "dma_recovery", 0x08, 2, 480),
            (62, "dma_recovery", 0x10, 4, 0xFF8002),
            (62, "dma_recovery", 0x18, 2, 0x8000),
            (721, "renderer_private", 0x0A, 2, 0),
            (722, "dma_recovery", 0x4A, 2, 1),
            (1441, "renderer_private", 0x24, 2, 13),
            (1732, "renderer_private", 0x22, 2, 0),
        )
        for total, name, offset, width, value in cases:
            with self.subTest(total=total, field=name, offset=offset):
                changed = copy.deepcopy(session)
                terminal = total == 1732
                row = changed["route"]["terminal_wait_observation"] if terminal else next(row for row in changed["route"]["cadence_rows"] if row["authoritative_total"] == total)
                private = _raw_blocks(row["checkpoint_renderer"])
                raw = bytearray(private[name]); raw[offset:offset + width] = value.to_bytes(width, "big"); private[name] = bytes(raw)
                blocks = _raw_blocks(row)
                rebuilt = build_cadence_row_from_raw(authoritative_total=total, engine_raw=blocks["engine_control"], debug_raw=blocks["debug_budget"], input_raw=blocks["input_state"], checkpoint_raw=private)
                row.clear(); row.update(rebuilt)
                self.assertTrue(checkpoint_renderer_errors(changed))
        changed = copy.deepcopy(session)
        changed["route"]["cadence_rows"].pop()
        self.assertTrue(checkpoint_renderer_errors(changed))
        changed = copy.deepcopy(session)
        del changed["route"]["terminal_wait_observation"]["checkpoint_renderer"]
        self.assertTrue(checkpoint_renderer_errors(changed))

    def test_reset_physical_clocks_and_zero_dma_rows_are_exact(self) -> None:
        for total in (1, 60, 61, 62, 719, 720, 721, 722, 1439, 1440, 1441, 1442, 1732):
            row = synthetic_cadence_row(total)
            self.assertEqual(cadence_row_errors(row), [])
            for offset, width, value in ((0x10, 4, expected_physical_frame(total) + 1), (0x3C, 4, expected_physical_frame(total) - 1), (0x14, 2, 0 if total in RESET_TOTALS else 2), (0x28, 2, 440 if total in ZERO_DMA_TOTALS else 0)):
                with self.subTest(total=total, offset=offset):
                    blocks = _raw_blocks(row)
                    debug = bytearray(blocks["debug_budget"]); debug[offset:offset + width] = value.to_bytes(width, "big")
                    rebuilt = build_cadence_row_from_raw(authoritative_total=total, engine_raw=blocks["engine_control"], debug_raw=bytes(debug), input_raw=blocks["input_state"])
                    self.assertTrue(cadence_row_errors(rebuilt))

    def test_schema_and_block_contract_are_fixed(self) -> None:
        schema = json.loads(
            (ROOT / "tools/report_schemas/m5-budget-evidence.schema.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["properties"]["schema"]["const"], REPORT_SCHEMA
        )
        self.assertEqual(len(BASE_BLOCK_SPECS), 6)
        self.assertEqual(len(POOL_BLOCK_SPECS), 7)
        self.assertEqual(len(BLOCK_SPECS), 13)
        self.assertEqual(len(EXPECTED_CAPTURE_LABELS), 27)
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(build_report())),
            [],
        )
        truncated = build_report()
        del truncated["sessions"][0]["route"]["scenario_snapshots"]
        del truncated["sessions"][0]["metadata"][
            "frame_advance_audit"
        ]["memory_read_pairs"]
        messages = [
            error.message
            for error in Draft202012Validator(schema).iter_errors(
                truncated
            )
        ]
        self.assertTrue(
            any("scenario_snapshots" in message for message in messages)
        )
        self.assertTrue(
            any("memory_read_pairs" in message for message in messages)
        )

    def test_schedule_contract_matches_canonical_provider(self) -> None:
        contract = schedule_contract()
        self.assertEqual(contract["status"], "pass")
        self.assertEqual(contract["attempt_lengths"], [720, 720, 292])
        self.assertEqual(contract["attempt_sha256"], list(SCHEDULE_SHA256))
        self.assertEqual(
            contract["combined_sha256"], COMBINED_SCHEDULE_SHA256
        )
        self.assertEqual(
            contract["capture_ticks_sha256"], CAPTURE_TICKS_SHA256
        )
        self.assertEqual(
            contract["checkpoint_keys_sha256"],
            CHECKPOINT_KEYS_SHA256,
        )

    def test_schedule_drift_fails_closed(self) -> None:
        original = b03.ATTEMPT_2_INPUTS
        try:
            b03.ATTEMPT_2_INPUTS = original + b"\0"
            with self.assertRaises(EvidenceFailure):
                schedule_contract()
        finally:
            b03.ATTEMPT_2_INPUTS = original

    def test_checkpoint_drift_fails_closed(self) -> None:
        original = b03.CHECKPOINT_KEYS
        try:
            b03.CHECKPOINT_KEYS = original[:-1]
            with self.assertRaises(EvidenceFailure):
                schedule_contract()
        finally:
            b03.CHECKPOINT_KEYS = original

    def test_parsers_reject_wrong_sizes_and_invalid_active(self) -> None:
        with self.assertRaises(ValueError):
            parse_vocabulary_budget(bytes(VOCAB_HEADER_BYTES - 1))
        with self.assertRaises(ValueError):
            parse_pool_budget(
                bytes(OBJECT_BYTES - 1),
                pool_type=1,
                name="enemy",
                address=0xFF2000,
                capacity=1,
            )
        raw = bytearray(OBJECT_BYTES)
        raw[0] = 2
        parsed = parse_pool_budget(
            bytes(raw),
            pool_type=1,
            name="enemy",
            address=0xFF2000,
            capacity=1,
        )
        self.assertEqual(
            parsed["invalid_active_values"],
            [{"slot": 0, "value": 2}],
        )

    def test_archived_rom_and_symbols_match_frozen_collector_hashes(self) -> None:
        self.assertEqual(hashlib.sha256(FROZEN_B03_ROM.read_bytes()).hexdigest(), PINNED_ROM_SHA256)
        self.assertEqual(hashlib.sha256(FROZEN_B03_SYMBOL_PATH.read_bytes()).hexdigest(), PINNED_SYMBOL_SHA256)

    def test_symbol_layout_matches_frozen_b03_fixture(self) -> None:
        symbols = parse_vasm_symbols(FROZEN_B03_SYMBOL_PATH)
        self.assertEqual(symbol_layout_errors(symbols), [])
        symbols["VOCAB_UPDATE_PEAK"] += 2
        self.assertIn(
            "VOCAB_UPDATE_PEAK",
            " ".join(symbol_layout_errors(symbols)),
        )

    def test_pinned_runtime_prerequisite_is_semantically_bound(self) -> None:
        path = ROOT / "artifacts/reports/m5-b03-runtime.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = _runtime_prerequisite_errors(
            payload,
            report_sha256=PINNED_RUNTIME_REPORT_SHA256,
            trace_sha256=PINNED_RUNTIME_TRACE_SHA256,
            replay_sha256=PINNED_RUNTIME_REPLAY_SHA256,
        )
        self.assertEqual(errors, [])
        payload["status"] = "fail"
        self.assertTrue(
            _runtime_prerequisite_errors(
                payload,
                report_sha256=PINNED_RUNTIME_REPORT_SHA256,
                trace_sha256=PINNED_RUNTIME_TRACE_SHA256,
                replay_sha256=PINNED_RUNTIME_REPLAY_SHA256,
            )
        )

    def test_complete_synthetic_report_passes_public_contract(self) -> None:
        report = build_report()
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["acceptance_status"], "pass")
        self.assertEqual(
            tuple(item["check"] for item in report["checks"]),
            REQUIRED_CHECK_NAMES,
        )
        self.assertTrue(all(
            item["status"] == "pass" for item in report["checks"]
        ))
        self.assertEqual(report["limitations"], LIMITATIONS)
        self.assertEqual(report_contract_errors(report), [])
        self.assertEqual(report_exit_code(report), 0)

    def test_guest_write_packet_fails_drive(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["_transcript_lines"].append("> Mff0000,1:00")
        sessions[0]["metadata"]["transcript_sha256"] = hashlib.sha256(
            (
                "\n".join(sessions[0]["_transcript_lines"]) + "\n"
            ).encode()
        ).hexdigest()
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            check_status(
                report, "exact_read_only_frame_advance_post_drive"
            ),
            "fail",
        )
        self.assertEqual(
            report["debugger_write_packets"],
            ["> Mff0000,1:00"],
        )

    def test_renderer_dma_vblank_and_z80_faults_fail(self) -> None:
        field_paths = (
            ("renderer", "dropped_sprites"),
            ("debug", "dma_overflow_count"),
            ("debug", "vblank_overrun_count"),
            ("debug", "z80_timeout_count"),
        )
        for parent, field in field_paths:
            with self.subTest(parent=parent, field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observations"][4][parent][field] = 1
                report = build_report(
                    sessions=sessions,
                    provenance=synthetic_provenance(sessions),
                )
                self.assertEqual(
                    check_status(
                        report,
                        "all_cumulative_fault_counters_zero",
                    ),
                    "fail",
                )

    def test_pool_count_and_vocabulary_budget_faults_fail(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["observations"][5]["engine"]["pool_counts"][2] = 4
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "bounded_pool_and_vocabulary_budget"
            ),
            "fail",
        )

        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["observations"][5]["vocabulary"][
            "update_units"
        ] = 4097
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "bounded_pool_and_vocabulary_budget"
            ),
            "fail",
        )

    def test_renderer_dma_capacity_overruns_fail(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        observation = sessions[0]["observations"][8]
        observation["renderer"]["sprite_count"] = 65
        observation["debug"]["sprite_count"] = 65
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "renderer_and_dma_capacities_in_bounds"
            ),
            "fail",
        )

    def test_cross_clock_excess_fails(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        observation = sessions[0]["observations"][8]
        observation["debug"]["z80_heartbeat"] += 40
        observation["audio_shadow"]["heartbeat"] += 40
        observation["cross_clock"]["z80_vint_heartbeat"] += 40
        observation["cross_clock"]["z80_vint_excess"] += 40
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(report, "audio_z80_health_and_cross_clock"),
            "fail",
        )

    def test_missing_checkpoint_fails_stable_and_worst_case(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        del sessions[0]["observations"][-1]
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(report, "stable_route_aligned_observations"),
            "fail",
        )
        self.assertEqual(
            check_status(
                report, "meaningful_worst_case_and_terminal_coverage"
            ),
            "fail",
        )

    def test_duplicate_process_or_runtime_fails_freshness(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[1]["metadata"]["process_id"] = sessions[0]["metadata"][
            "process_id"
        ]
        sessions[1]["metadata"]["runtime_copy"][
            "temporary_directory"
        ] = sessions[0]["metadata"]["runtime_copy"][
            "temporary_directory"
        ]
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(report, "two_fresh_owned_route_sessions"),
            "fail",
        )

    def test_signature_mismatch_fails_even_with_in_bounds_values(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        observation = sessions[1]["observations"][10]
        observation["debug"]["dma_words_frame"] = 439
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "exact_two_session_budget_signatures"
            ),
            "fail",
        )

    def test_hash_drift_fails(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        provenance = synthetic_provenance(sessions)
        provenance["hash_verification"]["drift"] = ["collector"]
        provenance["hash_verification"]["status"] = "fail"
        report = build_report(
            sessions=sessions, provenance=provenance
        )
        self.assertEqual(
            check_status(report, "exact_hash_bindings_and_no_drift"),
            "fail",
        )

        sessions = [synthetic_session(1), synthetic_session(2)]
        provenance = synthetic_provenance(sessions)
        provenance["transcript"]["commands"] += 1
        report = build_report(sessions=sessions, provenance=provenance)
        self.assertEqual(
            check_status(report, "exact_hash_bindings_and_no_drift"),
            "fail",
        )

    def test_source_bindings_are_semantic_and_path_exact(self) -> None:
        report = build_report()
        binding = report["provenance"]["source_bindings"]
        sources = binding["sources"]
        first_path = next(iter(sources))
        digest = sources.pop(first_path)
        sources["content/not-the-bound-source.json"] = digest
        binding["bundle_sha256"] = hashlib.sha256(
            json.dumps(
                sources,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        errors = report_contract_errors(report)
        self.assertIn("source binding path set mismatch", errors)
        self.assertEqual(report_exit_code(report), 1)

    def test_live_symbols_defeat_report_controlled_pointer_comutation(
        self,
    ) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        symbol_name = "CampaignRuntimeProfileB03"
        old_address = FROZEN_B03_SYMBOLS[symbol_name]
        current_symbols = parse_vasm_symbols(
            ROOT / "build/level-B03/recursion94-debug.sym"
        )
        # Derive a wrong pointer independently of the report-controlled map,
        # distinct from both frozen raw bytes and the actual current root.
        new_address = max(old_address, current_symbols[symbol_name]) + 2
        self.assertNotEqual(new_address, current_symbols[symbol_name])
        old_bytes = old_address.to_bytes(4, "big")
        new_bytes = new_address.to_bytes(4, "big")
        for session in sessions:
            for item in session["route"]["scenario_snapshots"]:
                block = item["blocks"]["vocabulary_state"]
                raw = bytearray.fromhex(block["raw_hex"])
                self.assertEqual(raw[0x100:0x104], old_bytes)
                raw[0x100:0x104] = new_bytes
                block["raw_hex"] = raw.hex().upper()
                block["sha256"] = hashlib.sha256(raw).hexdigest()
                bundle = b"".join(
                    bytes.fromhex(item["blocks"][name]["raw_hex"])
                    for name, _private, _address, _size
                    in SCENARIO_BLOCK_SPECS
                )
                item["raw_bundle_sha256"] = hashlib.sha256(
                    bundle
                ).hexdigest()
        provenance = synthetic_provenance(sessions)
        provenance["required_symbols"][symbol_name] = (
            f"{new_address:08X}"
        )
        report = build_report(sessions=sessions, provenance=provenance)

        # Without a repository root this is internally coherent.  The
        # canonical root-backed gate must ignore the report-controlled map,
        # parse the actual root .sym, and fail the co-mutation. This is a
        # negative current-root check, never current-candidate acceptance.
        self.assertEqual(report_contract_errors(report), [])
        rooted_errors = report_contract_errors(report, root=ROOT)
        self.assertIn(
            "required symbols differ from live pinned symbol map",
            rooted_errors,
        )
        self.assertTrue(
            any("scenario initial state invalid" in item for item in rooted_errors)
        )

    def test_schema_structurally_closes_symbols_and_sources(self) -> None:
        schema = json.loads(
            (
                ROOT
                / "tools/report_schemas/m5-budget-evidence.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)
        symbol_schema = schema["$defs"]["requiredSymbols"]
        self.assertEqual(
            set(symbol_schema["propertyNames"]["enum"]),
            set(REQUIRED_SYMBOLS),
        )
        self.assertEqual(symbol_schema["minProperties"], len(REQUIRED_SYMBOLS))
        self.assertEqual(symbol_schema["maxProperties"], len(REQUIRED_SYMBOLS))
        source_schema = schema["$defs"]["sourceBindings"]["properties"][
            "sources"
        ]
        self.assertEqual(
            set(source_schema["propertyNames"]["enum"]),
            set(b03.SOURCE_BINDING_PATHS),
        )
        baseline = build_report()
        self.assertEqual(list(validator.iter_errors(baseline)), [])

        mutations = []
        missing_symbol = copy.deepcopy(baseline)
        missing_symbol["provenance"]["required_symbols"].pop(
            "CampaignRuntimeProfileB03"
        )
        mutations.append(missing_symbol)
        replaced_symbol = copy.deepcopy(baseline)
        symbols = replaced_symbol["provenance"]["required_symbols"]
        symbols["NotABoundRuntimeSymbol"] = symbols.pop(
            "CampaignRuntimeProfileB03"
        )
        mutations.append(replaced_symbol)
        malformed_symbol = copy.deepcopy(baseline)
        malformed_symbol["provenance"]["required_symbols"][
            "CampaignRuntimeProfileB03"
        ] = "xyz"
        mutations.append(malformed_symbol)
        missing_source = copy.deepcopy(baseline)
        missing_source["provenance"]["source_bindings"]["sources"].pop(
            b03.SOURCE_BINDING_PATHS[0]
        )
        mutations.append(missing_source)
        replaced_source = copy.deepcopy(baseline)
        sources = replaced_source["provenance"]["source_bindings"][
            "sources"
        ]
        sources["content/not-bound.json"] = sources.pop(
            b03.SOURCE_BINDING_PATHS[0]
        )
        mutations.append(replaced_source)
        malformed_source = copy.deepcopy(baseline)
        malformed_source["provenance"]["source_bindings"]["sources"][
            b03.SOURCE_BINDING_PATHS[0]
        ] = "A" * 64
        mutations.append(malformed_source)
        for index, mutated in enumerate(mutations):
            with self.subTest(index=index):
                self.assertTrue(list(validator.iter_errors(mutated)))

    def test_raw_block_tamper_is_detected_by_public_validator(self) -> None:
        report = build_report()
        block = report["sessions"][0]["observations"][3]["blocks"][
            "debug_budget"
        ]
        block["raw_hex"] = "00" + block["raw_hex"][2:]
        self.assertTrue(report_contract_errors(report))
        self.assertEqual(report_exit_code(report), 1)

        report = build_report()
        row = report["sessions"][0]["route"]["cadence_rows"][99]
        block = row["blocks"]["engine_control"]
        raw = bytearray.fromhex(block["raw_hex"])
        raw[0x10] ^= 1
        block["raw_hex"] = raw.hex().upper()
        block["sha256"] = hashlib.sha256(raw).hexdigest()
        errors = report_contract_errors(report)
        self.assertTrue(
            any("parsed cadence fields mismatch" in item for item in errors)
        )

    def test_scenario_raw_recomputes_coordinates_and_facts(self) -> None:
        report = build_report()
        initial = report["sessions"][0]["route"][
            "scenario_snapshots"
        ][0]
        engine = initial["blocks"]["engine_control"]
        raw = bytearray.fromhex(engine["raw_hex"])
        _put_u16(raw, 0x10, 1)
        engine["raw_hex"] = raw.hex().upper()
        engine["sha256"] = hashlib.sha256(raw).hexdigest()
        bundle = b"".join(
            bytes.fromhex(initial["blocks"][name]["raw_hex"])
            for name, _private, _address, _size in SCENARIO_BLOCK_SPECS
        )
        initial["raw_bundle_sha256"] = hashlib.sha256(bundle).hexdigest()
        errors = report_contract_errors(report)
        self.assertTrue(
            any("scenario coordinate mismatch" in item for item in errors)
        )
        self.assertTrue(
            any("scenario initial state invalid" in item for item in errors)
        )

    def test_failed_double_read_cannot_pass_public_validator(self) -> None:
        report = build_report()
        consistency = report["sessions"][0]["observations"][3][
            "read_consistency"
        ]
        consistency["second_block_sha256"]["debug_budget"] = "0" * 64
        consistency["status"] = "fail"
        errors = report_contract_errors(report)
        self.assertTrue(
            any("double-read consistency failed" in item for item in errors)
        )
        self.assertEqual(report_exit_code(report), 1)

    def test_spoofed_check_cannot_rescue_semantic_fault(self) -> None:
        report = build_report()
        report["sessions"][0]["observations"][4]["debug"][
            "vblank_overrun_count"
        ] = 1
        for check in report["checks"]:
            check["status"] = "pass"
        report["status"] = "pass"
        report["acceptance_status"] = "pass"
        errors = report_contract_errors(report)
        self.assertTrue(any("fault field" in error for error in errors))
        self.assertEqual(report_exit_code(report), 1)

    def test_v1_report_is_explicitly_rejected(self) -> None:
        report = build_report()
        report["schema"] = "r94-m5-b03-budget-evidence-v1"
        report["evidence_type"] = (
            "blastem_gdb_m5_b03_combined_room_budget"
        )
        errors = report_contract_errors(report)
        self.assertIn("schema mismatch", errors)
        self.assertIn("evidence type mismatch", errors)

    def test_diagnostic_paths_and_schema_are_never_canonical(self) -> None:
        self.assertTrue(
            diagnostic_output_path_errors(
                root=ROOT, report=None, trace=None
            )
        )
        self.assertTrue(
            diagnostic_output_path_errors(
                root=ROOT,
                report=ROOT / "artifacts/reports/m5-b03-budget.json",
                trace=(
                    ROOT
                    / "artifacts/traces/"
                    "m5-b03-budget-gdb-transcript.log"
                ),
            )
        )
        diagnostic_root = ROOT / "artifacts/diagnostics/m5-budget"
        self.assertEqual(
            diagnostic_output_path_errors(
                root=ROOT,
                report=diagnostic_root / "handshake-2.json",
                trace=diagnostic_root / "handshake-2.log",
            ),
            [],
        )
        diagnostic = {
            "schema": DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_pass",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
        }
        errors = report_contract_errors(diagnostic)
        self.assertIn("schema mismatch", errors)
        self.assertIn("evidence type mismatch", errors)
        schema = json.loads(
            (
                ROOT
                / "tools/report_schemas/m5-budget-evidence.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            list(Draft202012Validator(schema).iter_errors(diagnostic))
        )

        origin_diagnostic = {
            "schema": OVERRUN_ORIGIN_DIAGNOSTIC_SCHEMA,
            "scope": "Milestone5/B03/diagnostic",
            "evidence_type": OVERRUN_ORIGIN_DIAGNOSTIC_EVIDENCE_TYPE,
            "status": "diagnostic_pass",
            "acceptance_status": "incomplete",
            "canonical_acceptance_eligible": False,
        }
        self.assertIn("schema mismatch", report_contract_errors(origin_diagnostic))
        self.assertTrue(
            list(
                Draft202012Validator(schema).iter_errors(origin_diagnostic)
            )
        )

    def test_overrun_origin_site_bytes_are_exact_and_mutation_gated(
        self,
    ) -> None:
        rom_path = FROZEN_B03_ROM
        contract = overrun_origin_site_contract(rom_path)
        self.assertEqual(contract["status"], "pass")
        self.assertEqual(set(contract["sites"]), set(OVERRUN_ORIGIN_SITES))
        self.assertTrue(
            all(item["exact"] for item in contract["sites"].values())
        )
        mutated = bytearray(rom_path.read_bytes())
        mutated[OVERRUN_ORIGIN_SITES["vblank_exit"]["address"]] ^= 1
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "mutated.bin"
            candidate.write_bytes(mutated)
            failed = overrun_origin_site_contract(candidate)
        self.assertEqual(failed["status"], "fail")
        self.assertTrue(
            any("vblank_exit" in item for item in failed["errors"])
        )

    def test_current_candidate_is_rejected_by_frozen_rom_contract(self) -> None:
        rom_path = ROOT / "build/level-B03/recursion94-debug.bin"
        self.assertNotEqual(hashlib.sha256(rom_path.read_bytes()).hexdigest(), PINNED_ROM_SHA256)
        contract = overrun_origin_site_contract(rom_path)
        self.assertEqual(contract["status"], "fail")
        self.assertIn("overrun-origin diagnostic ROM SHA-256 mismatch", contract["errors"])

    def test_overrun_counter_isolation_requires_one_fault_only(self) -> None:
        _lines, _initial, rows, _origin = (
            overrun_origin_transcript_fixture()
        )
        isolated = overrun_counter_isolation(rows[2])
        self.assertEqual(isolated["status"], "pass")
        self.assertEqual(isolated["observed_counter"], 1)
        self.assertEqual(isolated["counter_cleared_row_errors"], [])

        blocks = _raw_blocks(rows[2])
        debug = bytearray(blocks["debug_budget"])
        _put_u16(debug, 0x46, 1)
        extra_fault = build_cadence_row_from_raw(
            authoritative_total=3,
            engine_raw=blocks["engine_control"],
            debug_raw=bytes(debug),
            input_raw=blocks["input_state"],
        )
        self.assertEqual(overrun_counter_isolation(extra_fault)["status"], "fail")

    def test_overrun_origin_transcript_is_strict_and_read_only(self) -> None:
        lines, initial, rows, origin = overrun_origin_transcript_fixture()
        kwargs = {
            "session_id": "budget_overrun_origin_diagnostic",
            "vblank": FROZEN_B03_SYMBOLS["VBlankInterrupt"],
            "post_step": FROZEN_B03_SYMBOLS["MainLoopPostGameplayStep"],
            "initial_scenario": initial,
            "cadence_rows": rows,
            "origin_hit": origin,
        }
        audit = audit_overrun_origin_transcript(lines, **kwargs)
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(
            audit["fixed_authoritative_steps"],
            OVERRUN_ORIGIN_DIAGNOSTIC_STEPS,
        )
        self.assertEqual(audit["memory_read_pairs"], 66)
        self.assertEqual(audit["transcript_command_count"], 87)
        self.assertEqual(audit["debugger_write_packets"], [])

        for name, mutate in (
            (
                "wrong hit pc",
                lambda candidate: candidate.__setitem__(
                    candidate.index(f"< {origin['pc']}") , "< 000014B8"
                ),
            ),
            (
                "missing removal",
                lambda candidate: candidate.pop(
                    candidate.index(f"> z0,{OVERRUN_ORIGIN_SITES['dma_busy_timeout']['address']:x},2")
                ),
            ),
            (
                "debugger memory write",
                lambda candidate: candidate.insert(-2, "> Mfff032,2:0000"),
            ),
        ):
            with self.subTest(name=name):
                changed = list(lines)
                mutate(changed)
                self.assertEqual(
                    audit_overrun_origin_transcript(changed, **kwargs)[
                        "status"
                    ],
                    "fail",
                )

    def test_published_limits_labels_and_freshness_are_semantic(self) -> None:
        report = build_report()
        report["published_limits"]["renderer_sprites"] = 65
        report["meaningful_worst_case_labels"] = ["terminal"]
        report["sessions"][1]["metadata"]["process_id"] = report[
            "sessions"
        ][0]["metadata"]["process_id"]
        report["sessions"][1]["metadata"]["runtime_copy"][
            "temporary_directory"
        ] = report["sessions"][0]["metadata"]["runtime_copy"][
            "temporary_directory"
        ]
        errors = report_contract_errors(report)
        self.assertIn("published limits drifted", errors)
        self.assertIn("meaningful worst-case labels drifted", errors)
        self.assertIn(
            "owned session process identities are not unique", errors
        )
        self.assertIn("owned runtime directories are not unique", errors)

    def test_all_cadence_rows_are_independently_reparsed(self) -> None:
        report = build_report()
        self.assertEqual(
            sum(
                len(session["route"]["cadence_rows"])
                for session in report["sessions"]
            ),
            EXPECTED_AUTHORITATIVE_STEPS * 2,
        )
        row = report["sessions"][0]["route"]["cadence_rows"][719]
        raw = bytearray.fromhex(
            row["blocks"]["debug_budget"]["raw_hex"]
        )
        _put_u32(raw, 0x0C, 721)
        rebuilt = build_cadence_row_from_raw(
            authoritative_total=720,
            engine_raw=bytes.fromhex(
                row["blocks"]["engine_control"]["raw_hex"]
            ),
            debug_raw=bytes(raw),
            input_raw=bytes.fromhex(
                row["blocks"]["input_state"]["raw_hex"]
            ),
        )
        report["sessions"][0]["route"]["cadence_rows"][719] = rebuilt
        errors = report_contract_errors(report)
        self.assertTrue(
            any("total 720 Debug cadence mismatch" in item for item in errors)
        )

    def test_every_parsed_cadence_field_has_a_mutation_gate(self) -> None:
        baseline = synthetic_cadence_row(100)
        for group in ("engine", "debug", "input"):
            self.assertEqual(
                set(baseline["observed"][group]),
                set(CADENCE_PARSED_FIELD_CONTRACT[group]),
            )

        # Each tuple is (covered public field(s), block, offset, width, value).
        # Rebuilding from the changed raw bytes keeps raw/hash/parsed evidence
        # coherent; the semantic cadence contract must still reject it.
        cases = [
            (("engine.magic", "engine.magic_ascii"), "engine_control", 0x00, 4, 0),
            (("engine.abi_version",), "engine_control", 0x04, 2, 0),
            (("engine.control_bytes",), "engine_control", 0x06, 2, 0),
            (("engine.state",), "engine_control", 0x08, 2, 0),
            (("engine.flags",), "engine_control", 0x0A, 2, 0),
            (("engine.encounter",), "engine_control", 0x0C, 2, 999),
            (("engine.attempt",), "engine_control", 0x0E, 2, 9),
            (("engine.tick",), "engine_control", 0x10, 2, 99),
            (("engine.terminal_reason",), "engine_control", 0x12, 2, 1),
            (("engine.last_error",), "engine_control", 0x14, 2, 1),
            (("engine.overflow_flags",), "engine_control", 0x16, 2, 1),
            (("engine.projection_length",), "engine_control", 0x18, 2, 0),
            (("engine.state_crc",), "engine_control", 0x1A, 4, 0),
            (("engine.checkpoint_tick",), "engine_control", 0x1E, 2, 61),
            (("engine.checkpoint_count",), "engine_control", 0x20, 2, 0),
            (("engine.input_held",), "engine_control", 0x22, 1, 1),
            (("engine.input_pressed",), "engine_control", 0x23, 1, 1),
            (("engine.input_released",), "engine_control", 0x24, 1, 1),
            (("engine.input_previous",), "engine_control", 0x25, 1, 1),
            (("engine.facing_x",), "engine_control", 0x26, 1, 0),
            (("engine.active_actor_count",), "engine_control", 0x27, 1, 255),
            (("engine.room_left",), "engine_control", 0x28, 2, 1),
            (("engine.room_top",), "engine_control", 0x2A, 2, 1),
            (("engine.room_right",), "engine_control", 0x2C, 2, 319),
            (("engine.room_bottom",), "engine_control", 0x2E, 2, 223),
            (("engine.spawn_x",), "engine_control", 0x30, 4, 0),
            (("engine.spawn_y",), "engine_control", 0x34, 4, 0),
            (("engine.rect_count",), "engine_control", 0x38, 2, 11),
            (("engine.object_init_count",), "engine_control", 0x3A, 2, 5),
            (("engine.pool_counts",), "engine_control", 0x41, 1, 33),
            (("engine.pool_high_water",), "engine_control", 0x49, 1, 33),
            (("debug.magic", "debug.magic_ascii"), "debug_budget", 0x00, 4, 0),
            (("debug.abi_version",), "debug_budget", 0x04, 2, 0),
            (("debug.abi_size",), "debug_budget", 0x06, 2, 0),
            (("debug.sequence",), "debug_budget", 0x08, 4, 0),
            (("debug.heartbeat",), "debug_budget", 0x0C, 4, 0),
            (("debug.frame_counter",), "debug_budget", 0x10, 4, 0),
            (("debug.frame_token",), "debug_budget", 0x14, 2, 1),
            (("debug.fatal_code",), "debug_budget", 0x16, 2, 1),
            (("debug.boot_stage",), "debug_budget", 0x18, 2, 0),
            (("debug.hardware_flags",), "debug_budget", 0x1A, 2, 1),
            (("debug.last_vdp_status",), "debug_budget", 0x1C, 2, 0x0400),
            (("debug.sprite_count",), "debug_budget", 0x24, 2, 65),
            (("debug.sprite_scanline_max",), "debug_budget", 0x26, 2, 21),
            (("debug.dma_words_frame",), "debug_budget", 0x28, 2, 441),
            (("debug.dma_words_max",), "debug_budget", 0x2A, 2, 2049),
            (("debug.dma_queue_depth",), "debug_budget", 0x2C, 2, 5),
            (("debug.dma_queue_high_water",), "debug_budget", 0x2E, 2, 33),
            (("debug.dma_overflow_count",), "debug_budget", 0x30, 2, 1),
            (("debug.vblank_overrun_count",), "debug_budget", 0x32, 2, 1),
            (("debug.audio_state",), "debug_budget", 0x34, 2, 0),
            (("debug.z80_heartbeat",), "debug_budget", 0x36, 2, 200),
            (("debug.audio_command_count",), "debug_budget", 0x38, 2, 0),
            (("debug.m2_flags",), "debug_budget", 0x3A, 2, 0),
            (("debug.z80_timeout_count",), "debug_budget", 0x40, 2, 1),
            (("debug.dma_deferred_count",), "debug_budget", 0x42, 2, 1),
            (("debug.dma_required_drop_count",), "debug_budget", 0x44, 2, 1),
            (("debug.dma_invalid_count",), "debug_budget", 0x46, 2, 1),
            (("input.held",), "input_state", 0x00, 1, 1),
            (("input.pressed",), "input_state", 0x01, 1, 1),
            (("input.released",), "input_state", 0x02, 1, 1),
            (("input.raw_high",), "input_state", 0x03, 1, 0),
            (("input.raw_low",), "input_state", 0x04, 1, 0),
            (("input.connected",), "input_state", 0x05, 1, 0),
            (("input.start_held",), "input_state", 0x06, 1, 1),
        ]
        covered = {field for fields, *_rest in cases for field in fields}
        expected_fields = {
            f"{group}.{field}"
            for group, fields in CADENCE_PARSED_FIELD_CONTRACT.items()
            for field in fields
        }
        self.assertEqual(covered, expected_fields)

        baseline_blocks = _raw_blocks(baseline)
        for fields, block_name, offset, width, value in cases:
            with self.subTest(fields=fields):
                blocks = dict(baseline_blocks)
                changed = bytearray(blocks[block_name])
                changed[offset : offset + width] = value.to_bytes(
                    width, "big"
                )
                blocks[block_name] = bytes(changed)
                mutated = build_cadence_row_from_raw(
                    authoritative_total=100,
                    engine_raw=blocks["engine_control"],
                    debug_raw=blocks["debug_budget"],
                    input_raw=blocks["input_state"],
                )
                self.assertTrue(cadence_row_errors(mutated))

        for block_name, offset in (
            ("engine_control", 0x3C),
            ("engine_control", 0x47),
            ("engine_control", 0x4F),
            ("debug_budget", 0x23),
            ("debug_budget", 0x3C),
        ):
            with self.subTest(reserved=f"{block_name}:{offset:02X}"):
                blocks = dict(baseline_blocks)
                changed = bytearray(blocks[block_name])
                changed[offset] ^= 1
                blocks[block_name] = bytes(changed)
                mutated = build_cadence_row_from_raw(
                    authoritative_total=100,
                    engine_raw=blocks["engine_control"],
                    debug_raw=blocks["debug_budget"],
                    input_raw=blocks["input_state"],
                )
                self.assertTrue(cadence_row_errors(mutated))

    def test_coherent_two_session_actor_overflow_cannot_pass(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        for session in sessions:
            row = session["route"]["cadence_rows"][99]
            blocks = _raw_blocks(row)
            engine = bytearray(blocks["engine_control"])
            engine[0x27] = 255
            session["route"]["cadence_rows"][99] = (
                build_cadence_row_from_raw(
                    authoritative_total=100,
                    engine_raw=bytes(engine),
                    debug_raw=blocks["debug_budget"],
                    input_raw=blocks["input_state"],
                )
            )
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(report["status"], "fail")
        errors = report_contract_errors(report)
        self.assertTrue(
            any("engine cadence mismatch" in item for item in errors)
        )

    def test_vdp_status_allows_only_open_bus_hblank_and_collision(self) -> None:
        baseline = synthetic_cadence_row(100)
        blocks = _raw_blocks(baseline)
        for status in (
            0x0208,
            0x020C,
            0x0228,
            0x022C,
            0x0608,
            0x0E2C,
            0xFE08,
            0xFE2C,
        ):
            with self.subTest(open_bus_status=f"{status:04X}"):
                debug = bytearray(blocks["debug_budget"])
                _put_u16(debug, 0x1C, status)
                row = build_cadence_row_from_raw(
                    authoritative_total=100,
                    engine_raw=blocks["engine_control"],
                    debug_raw=bytes(debug),
                    input_raw=blocks["input_state"],
                )
                self.assertEqual(cadence_row_errors(row), [])
        for status in (
            0x0008,  # FIFO empty lost
            0x0308,  # FIFO full
            0x0288,  # pending V-int
            0x0248,  # sprite overflow
            0x0218,  # interlace odd frame
            0x0200,  # VBlank lost
            0x020A,  # DMA busy
            0x0209,  # PAL
        ):
            with self.subTest(defined_status=f"{status:04X}"):
                debug = bytearray(blocks["debug_budget"])
                _put_u16(debug, 0x1C, status)
                row = build_cadence_row_from_raw(
                    authoritative_total=100,
                    engine_raw=blocks["engine_control"],
                    debug_raw=bytes(debug),
                    input_raw=blocks["input_state"],
                )
                self.assertTrue(
                    any(
                        "Debug cadence mismatch" in item
                        for item in cadence_row_errors(row)
                    )
                )

    def test_preserved_live_rows_separate_hblank_from_fault_counter(self) -> None:
        row = build_cadence_row_from_raw(
            authoritative_total=1,
            engine_raw=bytes.fromhex(
                "523934450001040000010003000200000001000000000000"
                "0F3697DED8B00000000101010000010100000000014000E000"
                "00200000007000000C000600000000000005000100000000"
                "00050001000000"
            ),
            debug_raw=bytes.fromhex(
                "523934440001040000000002000000010000000100000000"
                "000400000E08010100010000000B00050000000000000000"
                "00000000000200100000003F000000010000000000000000"
            ),
            input_raw=bytes.fromhex("0101007E320100"),
        )
        self.assertEqual(cadence_row_errors(row), [])

        second = build_cadence_row_from_raw(
            authoritative_total=2,
            engine_raw=bytes.fromhex(
                "523934450001040000010003000200000002000000000000"
                "0F3697DED8B00000000101000001010100000000014000E000"
                "00200000007000000C000600000000000005000100000000"
                "00050001000000"
            ),
            debug_raw=bytes.fromhex(
                "523934440001040000000004000000020000000200000000"
                "000400000E28010000010000000B000501B801B800000004"
                "00000000000200110001003F000000020000000000000000"
            ),
            input_raw=bytes.fromhex("0100007E320100"),
        )
        self.assertEqual(cadence_row_errors(second), [])

        third_debug = bytes.fromhex(
            "523934440001040000000006000000030000000300000000"
            "000400000E2C010000010000000B000501B801B800000004"
            "00000001000200120001003F000000030000000000000000"
        )
        third = build_cadence_row_from_raw(
            authoritative_total=3,
            engine_raw=bytes.fromhex(
                "523934450001040000010003000200000003000000000000"
                "0F3697DED8B00000000101000001010100000000014000E000"
                "00200000007000000C000600000000000005000100000000"
                "00050001000000"
            ),
            debug_raw=third_debug,
            input_raw=bytes.fromhex("0100007E320100"),
        )
        self.assertTrue(
            any(
                "Debug cadence mismatch" in item
                for item in cadence_row_errors(third)
            )
        )
        without_fault = bytearray(third_debug)
        _put_u16(without_fault, 0x32, 0)
        hblank_only = build_cadence_row_from_raw(
            authoritative_total=3,
            engine_raw=bytes.fromhex(
                "523934450001040000010003000200000003000000000000"
                "0F3697DED8B00000000101000001010100000000014000E000"
                "00200000007000000C000600000000000005000100000000"
                "00050001000000"
            ),
            debug_raw=bytes(without_fault),
            input_raw=bytes.fromhex("0100007E320100"),
        )
        self.assertEqual(cadence_row_errors(hblank_only), [])

    def test_reset_and_audio_boundaries_are_exact(self) -> None:
        self.assertEqual(
            expected_route_for_total(719),
            {"state": 1, "attempt": 0, "tick": 719},
        )
        self.assertEqual(
            expected_route_for_total(720),
            {"state": 1, "attempt": 1, "tick": 0},
        )
        self.assertEqual(
            expected_route_for_total(1440),
            {"state": 1, "attempt": 2, "tick": 0},
        )
        self.assertEqual(
            expected_route_for_total(1732),
            {"state": 2, "attempt": 2, "tick": 292},
        )
        self.assertEqual(
            expected_audio_for_total(1)["command_count"], 0
        )
        self.assertEqual(
            expected_audio_for_total(2)["command_count"], 1
        )
        self.assertEqual(
            expected_audio_for_total(720)["command_count"], 1
        )
        self.assertEqual(
            expected_audio_for_total(721)["command_count"], 2
        )
        self.assertEqual(
            expected_audio_for_total(1440)["active_layers"], 3
        )
        self.assertEqual(
            expected_audio_for_total(1441)["active_layers"], 7
        )
        row = synthetic_cadence_row(720)
        raw = bytearray.fromhex(
            row["blocks"]["debug_budget"]["raw_hex"]
        )
        _put_u16(raw, 0x38, 2)
        mutated = build_cadence_row_from_raw(
            authoritative_total=720,
            engine_raw=bytes.fromhex(
                row["blocks"]["engine_control"]["raw_hex"]
            ),
            debug_raw=bytes(raw),
            input_raw=bytes.fromhex(
                row["blocks"]["input_state"]["raw_hex"]
            ),
        )
        self.assertTrue(
            any(
                "Debug cadence mismatch" in item
                for item in cadence_row_errors(mutated)
            )
        )
        first = synthetic_cadence_row(1)
        first_raw = _raw_blocks(first)
        first_debug = bytearray(first_raw["debug_budget"])
        _put_u16(first_debug, 0x38, 1)
        early_mirror = build_cadence_row_from_raw(
            authoritative_total=1,
            engine_raw=first_raw["engine_control"],
            debug_raw=bytes(first_debug),
            input_raw=first_raw["input_state"],
        )
        self.assertTrue(
            any(
                "Debug cadence mismatch" in item
                for item in cadence_row_errors(early_mirror)
            )
        )

    def test_sentinel_and_replay_mutations_fail_drive(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["metadata"]["sentinel_handshakes"][99][
            "sentinel_readback"
        ] = "spoof"
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "exact_read_only_frame_advance_post_drive"
            ),
            "fail",
        )
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["metadata"]["sentinel_handshakes"][99][
            "set_elapsed_milliseconds"
        ] = 1001.0
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "exact_read_only_frame_advance_post_drive"
            ),
            "fail",
        )

        report = build_report()
        replay = report["sessions"][0]["route"]["terminal_replay"]
        raw = bytearray.fromhex(replay["raw_hex"])
        raw[-1] ^= 1
        replay["raw_hex"] = raw.hex().upper()
        replay["sha256"] = hashlib.sha256(raw).hexdigest()
        errors = report_contract_errors(report)
        self.assertTrue(
            any("terminal replay is not canonical" in item for item in errors)
        )
        report = build_report()
        report["sessions"][0]["route"]["timeout_commits"][0][
            "descriptor_length"
        ] = 719
        self.assertTrue(
            any(
                "timeout commit 0 mismatch" in item
                for item in report_contract_errors(report)
            )
        )

    def test_noncheckpoint_capacity_spike_fails_cadence(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        row = sessions[0]["route"]["cadence_rows"][99]
        debug = bytearray.fromhex(
            row["blocks"]["debug_budget"]["raw_hex"]
        )
        _put_u16(debug, 0x24, 65)
        sessions[0]["route"]["cadence_rows"][99] = (
            build_cadence_row_from_raw(
                authoritative_total=100,
                engine_raw=bytes.fromhex(
                    row["blocks"]["engine_control"]["raw_hex"]
                ),
                debug_raw=bytes(debug),
                input_raw=bytes.fromhex(
                    row["blocks"]["input_state"]["raw_hex"]
                ),
            )
        )
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "exact_read_only_frame_advance_post_drive"
            ),
            "fail",
        )

        sessions = [synthetic_session(1), synthetic_session(2)]
        row = sessions[0]["route"]["cadence_rows"][99]
        engine = bytearray.fromhex(
            row["blocks"]["engine_control"]["raw_hex"]
        )
        engine[0x48] = 17
        sessions[0]["route"]["cadence_rows"][99] = (
            build_cadence_row_from_raw(
                authoritative_total=100,
                engine_raw=bytes(engine),
                debug_raw=bytes.fromhex(
                    row["blocks"]["debug_budget"]["raw_hex"]
                ),
                input_raw=bytes.fromhex(
                    row["blocks"]["input_state"]["raw_hex"]
                ),
            )
        )
        report = build_report(
            sessions=sessions,
            provenance=synthetic_provenance(sessions),
        )
        self.assertEqual(
            check_status(
                report, "exact_read_only_frame_advance_post_drive"
            ),
            "fail",
        )

    def test_terminal_wait_requires_unchanged_engine_and_debug(self) -> None:
        report = build_report()
        terminal_wait = report["sessions"][0]["route"][
            "terminal_wait_observation"
        ]
        debug = bytearray.fromhex(
            terminal_wait["blocks"]["debug_budget"]["raw_hex"]
        )
        # Keep every explicitly bounded terminal counter valid while proving
        # that the same-frame WAIT guard rejects any Debug ABI drift at all.
        _put_u16(debug, 0x24, 19)
        terminal_wait = build_cadence_row_from_raw(
            authoritative_total=EXPECTED_AUTHORITATIVE_STEPS,
            engine_raw=bytes.fromhex(
                terminal_wait["blocks"]["engine_control"]["raw_hex"]
            ),
            debug_raw=bytes(debug),
            input_raw=bytes.fromhex(
                terminal_wait["blocks"]["input_state"]["raw_hex"]
            ),
        )
        report["sessions"][0]["route"][
            "terminal_wait_observation"
        ] = terminal_wait
        errors = report_contract_errors(report)
        self.assertTrue(
            any(
                "terminal WAIT debug_budget changed after terminal POST"
                in item
                for item in errors
            )
        )
        self.assertEqual(report_exit_code(report), 1)

    def _legacy_transcript_fixture_documentation(
        self,
    ) -> None:
        post = 0x00000336
        wait_for_vblank = 0x00000340
        finalizer = 0x0000554A
        session_id = EXPECTED_SESSION_IDS[0]
        lines = [
            f"> Z0,{post:x},2",
            f"> Z0,{finalizer:x},2",
            "# ui.advance index=1 purpose=origin_prime",
            "> c",
            "# cadence.origin_pause "
            "title='RECURSION 94 - PAUSED'",
            "# cadence.sentinel "
            f"boundary=0 title={_sentinel_for(session_id, 0)!r}",
        ]
        for total in range(1, EXPECTED_AUTHORITATIVE_STEPS + 1):
            lines.extend(
                [
                    "# ui.advance "
                    f"index={total + 1} "
                    f"purpose=authoritative_total_{total}",
                    "< S05",
                ]
            )
            if total < EXPECTED_AUTHORITATIVE_STEPS:
                lines.extend(
                    [
                        "> p11",
                        f"< {post:08X}",
                        "# cadence.stop "
                        f"total={total} role=post pc={post:08X}",
                        "> c",
                        "# cadence.pause "
                        f"boundary={total - 1} "
                        "title='RECURSION 94 - PAUSED'",
                        "# cadence.sentinel "
                        f"boundary={total} "
                        f"title={_sentinel_for(session_id, total)!r}",
                    ]
                )
            else:
                lines.extend(
                    [
                        "> p11",
                        f"< {finalizer:08X}",
                        "# cadence.stop "
                        f"total={total} role=finalizer "
                        f"pc={finalizer:08X}",
                        f"> z0,{finalizer:x},2",
                        "> c",
                        "< S05",
                        "> p11",
                        f"< {post:08X}",
                        "# cadence.stop "
                        f"total={total} role=terminal_post "
                        f"pc={post:08X}",
                        f"> Z0,{wait_for_vblank:x},2",
                        "> c",
                        "< S05",
                        "> p11",
                        f"< {wait_for_vblank:08X}",
                        "# cadence.stop "
                        f"total={total} role=terminal_wait "
                        f"pc={wait_for_vblank:08X}",
                        f"> z0,{wait_for_vblank:x},2",
                        f"> z0,{post:x},2",
                        "> c",
                        "# cadence.pause "
                        f"boundary={total - 1} "
                        "title='RECURSION 94 - PAUSED'",
                    ]
                )
        audit = audit_frame_advance_transcript(
            lines,
            session_id=session_id,
            post_step=post,
            finalizer=finalizer,
            wait_for_vblank=wait_for_vblank,
        )
        self.assertEqual(audit["status"], "pass")
        mutated = list(lines)
        mutated.remove(f"> z0,{post:x},2")
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        mutated.remove(
            "# ui.advance index=100 purpose=authoritative_total_99"
        )
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        first_response = mutated.index("> p11") + 1
        mutated[first_response] = f"< {finalizer:08X}"
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        sentinel_1 = (
            "# cadence.sentinel "
            f"boundary=1 title={_sentinel_for(session_id, 1)!r}"
        )
        sentinel_index = mutated.index(sentinel_1)
        mutated[sentinel_index], mutated[sentinel_index + 1] = (
            mutated[sentinel_index + 1],
            mutated[sentinel_index],
        )
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        pause_1 = (
            "# cadence.pause "
            "boundary=1 title='RECURSION 94 - PAUSED'"
        )
        mutated[mutated.index(pause_1)] = (
            "# cadence.pause "
            f"boundary=1 title={_sentinel_for(session_id, 1)!r}"
        )
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        mutated.remove(f"> Z0,{wait_for_vblank:x},2")
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        wait_pc_index = mutated.index(f"< {wait_for_vblank:08X}")
        mutated[wait_pc_index] = f"< {post:08X}"
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )
        mutated = list(lines)
        wait_remove_index = mutated.index(
            f"> z0,{wait_for_vblank:x},2"
        )
        post_remove_index = mutated.index(f"> z0,{post:x},2")
        mutated[wait_remove_index], mutated[post_remove_index] = (
            mutated[post_remove_index],
            mutated[wait_remove_index],
        )
        self.assertEqual(
            audit_frame_advance_transcript(
                mutated,
                session_id=session_id,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
            )["status"],
            "fail",
        )

    def test_strict_trace_cursor_binds_every_control_and_read(self) -> None:
        session = synthetic_session(1)
        vblank = FROZEN_B03_SYMBOLS["VBlankInterrupt"]
        post = FROZEN_B03_SYMBOLS["MainLoopPostGameplayStep"]
        finalizer = FROZEN_B03_SYMBOLS["VocabularyFinalizeStage"]
        wait_for_vblank = FROZEN_B03_SYMBOLS[
            "MainLoopWaitForVBlank"
        ]
        lines = strict_session_transcript(
            session,
            vblank=vblank,
            post=post,
            finalizer=finalizer,
            wait_for_vblank=wait_for_vblank,
        )

        def audit(candidate: list[str], bound: dict = session) -> dict:
            return audit_frame_advance_transcript(
                candidate,
                session_id=session["session_id"],
                vblank=vblank,
                post_step=post,
                finalizer=finalizer,
                wait_for_vblank=wait_for_vblank,
                session=bound,
            )

        baseline = audit(lines)
        self.assertEqual(baseline["status"], "pass")
        self.assertEqual(baseline["memory_read_pairs"], 8121)
        self.assertEqual(baseline["memory_read_sections"], 1871)
        self.assertEqual(baseline["transcript_command_count"], 11600)
        self.assertEqual(baseline["protocol_event_count"], 14031)
        self.assertEqual(baseline["trace_report_binding_errors"], [])

        mutations: list[tuple[str, list[str]]] = []
        missing_reset_service = list(lines)
        missing_reset_service.remove("# ui.advance index=722 purpose=reset_720_service_1")
        mutations.append(("missing reset-service advance", missing_reset_service))
        extra_reset_service = list(lines)
        extra_reset_service.insert(-2, "# ui.advance index=1738 purpose=reset_1440_service_3")
        mutations.append(("extra reset-service advance", extra_reset_service))
        private_read = list(lines)
        marker = private_read.index("# memory.begin kind=checkpoint_renderer id=0061")
        private_read[marker + 1] = "> mff8300,24"
        mutations.append(("historical public prefix cannot prove private count", private_read))
        missing_ok = list(lines)
        missing_ok.remove("< OK")
        mutations.append(("missing breakpoint OK", missing_ok))

        duplicate_ok = list(lines)
        duplicate_ok.insert(3, "< OK")
        mutations.append(("duplicate breakpoint OK", duplicate_ok))

        unknown_control = list(lines)
        unknown_control.insert(-2, "> R")
        mutations.append(("unknown reset control", unknown_control))

        wrong_address = list(lines)
        cadence_marker = wrong_address.index(
            "# memory.begin kind=cadence id=0100"
        )
        wrong_address[cadence_marker + 1] = "> mff1402,50"
        mutations.append(("wrong cadence address", wrong_address))

        wrong_size = list(lines)
        cadence_marker = wrong_size.index(
            "# memory.begin kind=cadence id=0100"
        )
        wrong_size[cadence_marker + 1] = "> mff1400,4f"
        mutations.append(("wrong cadence size", wrong_size))

        reassigned_marker = list(lines)
        cadence_marker = reassigned_marker.index(
            "# memory.begin kind=cadence id=0100"
        )
        reassigned_marker[cadence_marker] = (
            "# memory.begin kind=cadence id=0101"
        )
        mutations.append(("reassigned cadence marker", reassigned_marker))

        early_post_remove = list(lines)
        post_remove = early_post_remove.index(f"> z0,{post:x},2")
        post_remove_pair = early_post_remove[post_remove : post_remove + 2]
        del early_post_remove[post_remove : post_remove + 2]
        early_post_remove[20:20] = post_remove_pair
        mutations.append(("early permanent POST removal", early_post_remove))

        for name, mutated in mutations:
            with self.subTest(name=name):
                self.assertEqual(audit(mutated)["status"], "fail")

        trace_flip = list(lines)
        cadence_marker = trace_flip.index(
            "# memory.begin kind=cadence id=0100"
        )
        response_index = cadence_marker + 2
        response = trace_flip[response_index]
        trace_flip[response_index] = (
            response[:-1] + ("1" if response[-1] != "1" else "0")
        )
        trace_flip_audit = audit(trace_flip)
        self.assertEqual(trace_flip_audit["status"], "fail")
        self.assertTrue(trace_flip_audit["trace_report_binding_errors"])

        second_flip = list(lines)
        second_marker = second_flip.index(
            "# memory.begin kind=budget_second id=initial"
        )
        response_index = second_marker + 2
        response = second_flip[response_index]
        second_flip[response_index] = (
            response[:-1] + ("1" if response[-1] != "1" else "0")
        )
        second_audit = audit(second_flip)
        self.assertEqual(second_audit["status"], "fail")
        self.assertTrue(second_audit["trace_report_binding_errors"])

        report_flip_session = copy.deepcopy(session)
        row = report_flip_session["route"]["cadence_rows"][99]
        raw_hex = row["blocks"]["engine_control"]["raw_hex"]
        row["blocks"]["engine_control"]["raw_hex"] = (
            raw_hex[:-1] + ("1" if raw_hex[-1] != "1" else "0")
        )
        report_flip_audit = audit(lines, report_flip_session)
        self.assertEqual(report_flip_audit["status"], "fail")
        self.assertTrue(report_flip_audit["trace_report_binding_errors"])

    def test_pinned_frame_source_and_config_contract(self) -> None:
        contract = frame_advance_source_contract(
            ROOT / ".toolcache/blastem/default.cfg"
        )
        self.assertEqual(contract["status"], "pass")
        self.assertEqual(contract["source_node"], BLASTEM_SOURCE_NODE)
        self.assertEqual(contract["source_sha256"], BLASTEM_SOURCE_HASHES)
        self.assertEqual(
            contract["source_sha256"]["vdp.c"],
            "21fc02bca5ad4f2e16e2be364195e9f02907cc68e0538aa6be8c8e436a9498e2",
        )
        self.assertEqual(
            contract["default_config_sha256"],
            PINNED_DEFAULT_CONFIG_SHA256,
        )
        self.assertEqual(
            contract["bindings"]["frame_advance_matches"], 1
        )
        self.assertEqual(contract["bindings"]["pause_matches"], 1)

        report = build_report()
        source_contract = report["provenance"][
            "frame_advance_source_contract"
        ]
        source_contract["source_sha256"] = {
            **source_contract["source_sha256"],
            "vdp.c": "0" * 64,
        }
        self.assertIn(
            "frame-advance source contract mismatch",
            report_contract_errors(report),
        )
        schema = json.loads(
            (
                ROOT
                / "tools/report_schemas/m5-budget-evidence.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            list(Draft202012Validator(schema).iter_errors(report))
        )


if __name__ == "__main__":
    unittest.main()
