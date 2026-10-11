from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from tools.m4_budget_evidence import (
    AUDIO_ACTIVE_LAYERS_EXPECTED,
    AUDIO_COMMAND_COUNT_EXPECTED,
    AUDIO_HEALTH_CHECK,
    AUDIO_LAST_COMMAND_EXPECTED,
    AUDIO_QUEUE_READ_EXPECTED,
    AUDIO_SHADOW_BYTES,
    AUDIO_STATUS_ABI_VERSION,
    AUDIO_STATUS_FLAGS_HEALTHY,
    AUDIO_STATUS_SIGNATURE,
    AUDIO_STATE_PLAYING,
    BLOCK_SPECS,
    CROSS_CLOCK_CHECK,
    DEBUG_ABI_SIZE,
    DEBUG_ABI_VERSION,
    DEBUG_BUDGET_BYTES,
    DEBUG_MAGIC,
    DMA_ORDINARY_WORD_LIMIT,
    DMA_QUEUE_CAPACITY,
    DMA_QUEUE_CONTROL_BYTES,
    ENGINE_ABI_VERSION,
    ENGINE_ATTEMPT_TICKS,
    ENGINE_CONTROL_USED_BYTES,
    ENGINE_MAGIC,
    ENGINE_STATE_TERMINAL,
    ENGINE_TERMINAL_TIMEOUT,
    EVIDENCE_TYPE,
    EXPECTED_STATE_SYMBOLS,
    FORBIDDEN_BREAKPOINT_SYMBOLS,
    M2_HEALTHY_FLAGS,
    RENDERER_BYTES,
    RENDERER_MAGIC,
    RENDERER_STATE_USED_BYTES,
    RENDERER_VERSION,
    REPORT_SCHEMA,
    REQUIRED_CHECK_NAMES,
    SCHEMA_RELATIVE_PATH,
    Z80_VINT_STARTUP_ALLOWANCE,
    analyze_transcript,
    build_budget_report,
    parse_debug_budget,
    parse_dma_queue_control,
    parse_audio_shadow,
    parse_engine_control,
    parse_renderer_state,
    report_contract_errors,
    symbol_layout_errors,
)


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64


def _put_u16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = value.to_bytes(2, "big")


def _put_u32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = value.to_bytes(4, "big")


def synthetic_observation() -> dict:
    engine_raw = bytearray(ENGINE_CONTROL_USED_BYTES)
    _put_u32(engine_raw, 0x00, ENGINE_MAGIC)
    _put_u16(engine_raw, 0x04, ENGINE_ABI_VERSION)
    _put_u16(engine_raw, 0x06, 0x0400)
    _put_u16(engine_raw, 0x08, ENGINE_STATE_TERMINAL)
    _put_u16(engine_raw, 0x0A, 1)
    _put_u16(engine_raw, 0x0C, 1)
    _put_u16(engine_raw, 0x0E, 0)
    _put_u16(engine_raw, 0x10, ENGINE_ATTEMPT_TICKS)
    _put_u16(engine_raw, 0x12, ENGINE_TERMINAL_TIMEOUT)
    _put_u16(engine_raw, 0x18, 3638)
    _put_u32(engine_raw, 0x1A, 0x12345678)
    _put_u16(engine_raw, 0x1E, 660)
    _put_u16(engine_raw, 0x20, 11)
    engine_raw[0x26] = 1
    engine_raw[0x27] = 1
    engine_raw[0x40:0x47] = bytes((1, 1, 1, 1, 0, 0, 0))
    engine_raw[0x48:0x4F] = bytes((1, 1, 1, 1, 0, 0, 0))

    debug_raw = bytearray(DEBUG_BUDGET_BYTES)
    _put_u32(debug_raw, 0x00, DEBUG_MAGIC)
    _put_u16(debug_raw, 0x04, DEBUG_ABI_VERSION)
    _put_u16(debug_raw, 0x06, DEBUG_ABI_SIZE)
    _put_u32(debug_raw, 0x08, ENGINE_ATTEMPT_TICKS * 2)
    _put_u32(debug_raw, 0x0C, ENGINE_ATTEMPT_TICKS)
    _put_u32(debug_raw, 0x10, ENGINE_ATTEMPT_TICKS)
    _put_u16(debug_raw, 0x18, 4)
    _put_u16(debug_raw, 0x1C, 8)
    _put_u16(debug_raw, 0x24, 12)
    _put_u16(debug_raw, 0x26, 3)
    _put_u16(debug_raw, 0x28, 440)
    _put_u16(debug_raw, 0x2A, 440)
    _put_u16(debug_raw, 0x2E, 4)
    _put_u16(debug_raw, 0x34, AUDIO_STATE_PLAYING)
    _put_u16(debug_raw, 0x36, ENGINE_ATTEMPT_TICKS + 25)
    _put_u16(debug_raw, 0x38, AUDIO_COMMAND_COUNT_EXPECTED)
    _put_u16(debug_raw, 0x3A, M2_HEALTHY_FLAGS)

    renderer_raw = bytearray(RENDERER_STATE_USED_BYTES)
    _put_u32(renderer_raw, 0x00, RENDERER_MAGIC)
    _put_u16(renderer_raw, 0x04, RENDERER_VERSION)
    _put_u16(renderer_raw, 0x06, RENDERER_BYTES)
    _put_u16(renderer_raw, 0x08, 1)
    _put_u16(renderer_raw, 0x0E, 0xFFFF)
    _put_u16(renderer_raw, 0x10, 0xFFFF)
    _put_u16(renderer_raw, 0x12, 0xFFFF)
    _put_u16(renderer_raw, 0x14, 12)
    _put_u16(renderer_raw, 0x16, 3)
    _put_u16(renderer_raw, 0x18, 48)
    _put_u16(renderer_raw, 0x20, ENGINE_ATTEMPT_TICKS - 1)

    dma_raw = bytearray(DMA_QUEUE_CONTROL_BYTES)
    _put_u16(dma_raw, 0x08, 440)

    audio_raw = bytearray(AUDIO_SHADOW_BYTES)
    audio_raw[0x00] = AUDIO_STATUS_SIGNATURE
    audio_raw[0x01] = AUDIO_STATUS_ABI_VERSION
    audio_raw[0x02] = AUDIO_STATE_PLAYING
    audio_raw[0x03] = AUDIO_STATUS_FLAGS_HEALTHY
    audio_raw[0x04:0x06] = (
        ENGINE_ATTEMPT_TICKS + 25
    ).to_bytes(2, "little")
    audio_raw[0x06] = AUDIO_QUEUE_READ_EXPECTED
    audio_raw[0x07] = 1
    audio_raw[0x08:0x0A] = AUDIO_COMMAND_COUNT_EXPECTED.to_bytes(
        2, "little"
    )
    audio_raw[0x0B] = 10
    audio_raw[0x0C] = 10
    audio_raw[0x0D] = 2
    audio_raw[0x0E] = AUDIO_ACTIVE_LAYERS_EXPECTED
    audio_raw[0x0F] = AUDIO_LAST_COMMAND_EXPECTED

    raw_by_name = {
        "engine_control": bytes(engine_raw),
        "debug_budget": bytes(debug_raw),
        "renderer_state": bytes(renderer_raw),
        "dma_queue_control": bytes(dma_raw),
        "audio_shadow": bytes(audio_raw),
    }
    blocks = {}
    for name, _key, address, size, _parser in BLOCK_SPECS:
        raw = raw_by_name[name]
        assert len(raw) == size
        blocks[name] = {
            "address": f"{address:08X}",
            "bytes": size,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "raw_hex": raw.hex().upper(),
        }
    hashes = {name: block["sha256"] for name, block in blocks.items()}
    debug = parse_debug_budget(bytes(debug_raw))
    return {
        "engine": parse_engine_control(bytes(engine_raw)),
        "debug": debug,
        "cross_clock": {
            "m68k_frame_counter": debug["frame_counter"],
            "z80_vint_heartbeat": debug["z80_heartbeat"],
            "z80_vint_excess": (
                debug["z80_heartbeat"] - debug["frame_counter"]
            ),
            "startup_allowance_vints": Z80_VINT_STARTUP_ALLOWANCE,
        },
        "renderer": parse_renderer_state(bytes(renderer_raw)),
        "dma_queue": parse_dma_queue_control(bytes(dma_raw)),
        "audio_shadow": parse_audio_shadow(bytes(audio_raw)),
        "blocks": blocks,
        "read_consistency": {
            "status": "pass",
            "first_block_sha256": hashes,
            "second_block_sha256": hashes,
            "cpu_state": "stopped",
        },
    }


def set_z80_heartbeat(observation: dict, heartbeat: int) -> None:
    frame_counter = observation["debug"]["frame_counter"]
    observation["debug"]["z80_heartbeat"] = heartbeat
    observation["audio_shadow"]["heartbeat"] = heartbeat
    observation["cross_clock"] = {
        "m68k_frame_counter": frame_counter,
        "z80_vint_heartbeat": heartbeat,
        "z80_vint_excess": heartbeat - frame_counter,
        "startup_allowance_vints": Z80_VINT_STARTUP_ALLOWANCE,
    }


def mutate_raw_block(
    session: dict, block_name: str, offset: int, payload: bytes
) -> None:
    observation = session["observation"]
    block = observation["blocks"][block_name]
    raw = bytearray.fromhex(block["raw_hex"])
    raw[offset : offset + len(payload)] = payload
    block["raw_hex"] = raw.hex().upper()
    block["sha256"] = hashlib.sha256(raw).hexdigest()
    for name, observation_key, _address, _size, parser in BLOCK_SPECS:
        if name == block_name:
            observation[observation_key] = parser(bytes(raw))
            break
    hashes = {
        name: value["sha256"]
        for name, value in observation["blocks"].items()
    }
    observation["read_consistency"]["first_block_sha256"] = dict(hashes)
    observation["read_consistency"]["second_block_sha256"] = dict(hashes)
    if block_name == "debug_budget":
        debug = observation["debug"]
        observation["cross_clock"] = {
            "m68k_frame_counter": debug["frame_counter"],
            "z80_vint_heartbeat": debug["z80_heartbeat"],
            "z80_vint_excess": (
                debug["z80_heartbeat"] - debug["frame_counter"]
            ),
            "startup_allowance_vints": Z80_VINT_STARTUP_ALLOWANCE,
        }


def synthetic_transcript(
    number: int, observation: dict | None = None
) -> list[str]:
    terminal_address = 0x1234
    observation = observation or synthetic_observation()
    lines = [
        f"# session_{number} begin",
        f"> Z0,{terminal_address:x},2",
        "< OK",
        "> c",
        "< S05",
    ]
    for _round in range(2):
        for name, _key, address, size, _parser in BLOCK_SPECS:
            lines.extend(
                [
                    f"> m{address:x},{size:x}",
                    f"< {observation['blocks'][name]['raw_hex']}",
                ]
            )
    lines.extend(["> k", f"# session_{number} end"])
    return lines


def synthetic_session(number: int) -> dict:
    terminal_address = 0x1234
    observation = synthetic_observation()
    lines = synthetic_transcript(number, observation)
    policy = analyze_transcript(
        lines,
        terminal_address=terminal_address,
        forbidden_addresses={
            symbol: 0x2000 + index * 0x100
            for index, symbol in enumerate(
                FORBIDDEN_BREAKPOINT_SYMBOLS
            )
        },
    )
    return {
        "session_id": f"session_{number}",
        "metadata": {
            "process_id": 1000 + number,
            "started_utc": "2026-07-26T00:00:00+00:00",
            "launch_flags": ["-D", "-g", "-m", "gen", "-r", "U"],
            "runtime_copy": {
                "temporary_directory": f"C:/Temp/session-{number}",
                "executable_sha256": SHA,
                "default_config_sha256": SHA,
            },
            "controller_profile": {
                "status": "pass",
                "sha256": SHA,
                "source_sha256": SHA,
                "port_1_assignment": "gamepad3.1",
                "session_isolation": {
                    "status": "pass",
                    "config_sha256": SHA,
                },
            },
            "profile_cleanup_verified": True,
            "terminal_breakpoint_symbol": "GameplayCommitAndReset",
            "terminal_breakpoint_address": f"{terminal_address:08X}",
            "input_injection": "none",
            "resume_count": 1,
            "transcript_policy": policy,
            "transcript_sha256": hashlib.sha256(
                ("\n".join(lines) + "\n").encode("utf-8")
            ).hexdigest(),
        },
        "observation": observation,
        "_transcript_lines": lines,
    }


def synthetic_provenance() -> dict:
    transcript = synthetic_transcript(1) + synthetic_transcript(2)
    symbols = {
        name: f"{address:08X}"
        for name, address in EXPECTED_STATE_SYMBOLS.items()
    }
    symbols.update(
        {
            "GameplayCommitAndReset": "00001234",
            "VBlankInterrupt": "00002000",
            "MainLoopPostGameplayStep": "00002100",
            "DmaCommitPreparedQueue": "00002200",
        }
    )
    before_after = {
        name: {"before": SHA, "after": SHA}
        for name in (
            "rom",
            "emulator",
            "default_config",
            "symbol_map",
            "report_schema",
            "collector",
        )
    }
    return {
        "rom": {
            "path": "build/recursion94-debug.bin",
            "sha256": SHA,
            "expected_sha256": SHA,
        },
        "emulator": {
            "path": ".toolcache/blastem/blastem.exe",
            "sha256": SHA,
        },
        "default_config": {
            "path": ".toolcache/blastem/default.cfg",
            "sha256": SHA,
        },
        "symbol_map": {
            "path": "build/recursion94-debug.sym",
            "sha256": SHA,
            "symbols": symbols,
        },
        "report_schema": {
            "path": SCHEMA_RELATIVE_PATH,
            "sha256": SHA,
            "id": REPORT_SCHEMA,
        },
        "collector": {
            "path": "tools/m4_budget_evidence.py",
            "sha256": SHA,
        },
        "transcript": {
            "path": (
                "artifacts/traces/"
                "m4-renderer-budget-gdb-transcript.log"
            ),
            "sha256": hashlib.sha256(
                ("\n".join(transcript) + "\n").encode("utf-8")
            ).hexdigest(),
            "commands": sum(line.startswith("> ") for line in transcript),
        },
        "hash_verification": {
            "status": "pass",
            "drift": [],
            "before_after": before_after,
        },
    }


def synthetic_report() -> dict:
    return build_budget_report(
        rom_sha256=SHA,
        sessions=[synthetic_session(1), synthetic_session(2)],
        provenance=synthetic_provenance(),
    )


def check_status(report: dict, name: str) -> str:
    return next(
        item["status"]
        for item in report["checks"]
        if item["check"] == name
    )


class M4BudgetParserTests(unittest.TestCase):
    def test_current_symbol_map_matches_every_parsed_state_address(
        self,
    ) -> None:
        symbols: dict[str, int] = {}
        for line in (
            ROOT / "build/recursion94-debug.sym"
        ).read_text("utf-8").splitlines():
            fields = line.split()
            if len(fields) == 2:
                try:
                    symbols[fields[1]] = int(fields[0], 16)
                except ValueError:
                    pass
        self.assertEqual([], symbol_layout_errors(symbols))
        for required in (
            "DEBUG_Z80_TIMEOUT_COUNT",
            "DMA_QUEUE_RESERVED_0",
            "DMA_QUEUE_RESERVED_1",
            "AUDIO_SHADOW_BASE",
        ):
            self.assertIn(required, EXPECTED_STATE_SYMBOLS)
        moved = dict(EXPECTED_STATE_SYMBOLS)
        moved["DEBUG_VBLANK_OVERRUN_COUNT"] += 2
        self.assertTrue(symbol_layout_errors(moved))

    def test_parse_debug_budget_exact_fields(self) -> None:
        raw = bytearray(DEBUG_BUDGET_BYTES)
        _put_u32(raw, 0x00, DEBUG_MAGIC)
        _put_u16(raw, 0x04, DEBUG_ABI_VERSION)
        _put_u16(raw, 0x06, DEBUG_ABI_SIZE)
        _put_u32(raw, 0x08, 1442)
        _put_u32(raw, 0x0C, 721)
        _put_u32(raw, 0x10, 721)
        _put_u16(raw, 0x24, 12)
        _put_u16(raw, 0x26, 3)
        _put_u16(raw, 0x28, 440)
        _put_u16(raw, 0x2A, 520)
        _put_u16(raw, 0x2E, 6)
        _put_u16(raw, 0x34, AUDIO_STATE_PLAYING)
        _put_u16(raw, 0x36, 746)
        _put_u16(raw, 0x38, AUDIO_COMMAND_COUNT_EXPECTED)
        _put_u16(raw, 0x3A, M2_HEALTHY_FLAGS)
        _put_u16(raw, 0x40, 7)
        _put_u16(raw, 0x42, 8)
        _put_u16(raw, 0x44, 9)
        _put_u16(raw, 0x46, 10)
        parsed = parse_debug_budget(bytes(raw))
        self.assertEqual(DEBUG_MAGIC, parsed["magic"])
        self.assertEqual(721, parsed["frame_counter"])
        self.assertEqual(440, parsed["dma_words_frame"])
        self.assertEqual(520, parsed["dma_words_max"])
        self.assertEqual(6, parsed["dma_queue_high_water"])
        self.assertEqual(AUDIO_STATE_PLAYING, parsed["audio_state"])
        self.assertEqual(746, parsed["z80_heartbeat"])
        self.assertEqual(
            AUDIO_COMMAND_COUNT_EXPECTED,
            parsed["audio_command_count"],
        )
        self.assertEqual(M2_HEALTHY_FLAGS, parsed["m2_flags"])
        self.assertEqual(7, parsed["z80_timeout_count"])
        self.assertEqual(8, parsed["dma_deferred_count"])
        self.assertEqual(9, parsed["dma_required_drop_count"])
        self.assertEqual(10, parsed["dma_invalid_count"])

    def test_parse_renderer_state_exact_fields(self) -> None:
        raw = bytearray(RENDERER_STATE_USED_BYTES)
        _put_u32(raw, 0x00, RENDERER_MAGIC)
        _put_u16(raw, 0x04, RENDERER_VERSION)
        _put_u16(raw, 0x06, RENDERER_BYTES)
        _put_u16(raw, 0x08, 1)
        _put_u16(raw, 0x14, 12)
        _put_u16(raw, 0x16, 3)
        _put_u16(raw, 0x18, 48)
        _put_u16(raw, 0x20, 719)
        parsed = parse_renderer_state(bytes(raw))
        self.assertEqual(RENDERER_MAGIC, parsed["magic"])
        self.assertEqual(12, parsed["sprite_count"])
        self.assertEqual(3, parsed["scanline_max"])
        self.assertEqual(48, parsed["scanline_pixel_max"])
        self.assertEqual(719, parsed["last_tick"])
        _put_u16(raw, 0x1A, 7)
        _put_u16(raw, 0x1C, 8)
        _put_u16(raw, 0x1E, 9)
        _put_u16(raw, 0x22, 10)
        parsed = parse_renderer_state(bytes(raw))
        self.assertEqual(7, parsed["dropped_sprites"])
        self.assertEqual(8, parsed["dma_rejects"])
        self.assertEqual(9, parsed["backlog_frames"])
        self.assertEqual(10, parsed["last_result"])

    def test_parse_audio_shadow_exact_fields(self) -> None:
        raw = bytearray(AUDIO_SHADOW_BYTES)
        raw[0x00:0x04] = bytes((0x94, 1, 2, 7))
        raw[0x04:0x06] = (745).to_bytes(2, "little")
        raw[0x06:0x10] = bytes((1, 2, 1, 0, 0, 9, 10, 3, 1, 3))
        parsed = parse_audio_shadow(bytes(raw))
        self.assertEqual(0x94, parsed["signature"])
        self.assertEqual(745, parsed["heartbeat"])
        self.assertEqual(2, parsed["queue_high_water"])
        self.assertEqual(1, parsed["processed_commands"])
        self.assertEqual(0, parsed["late_tick_count"])
        self.assertEqual(9, parsed["ym_write_count"])
        self.assertEqual(10, parsed["psg_write_count"])
        self.assertEqual(3, parsed["last_error_or_command"])

    def test_parse_dma_queue_control_exact_fields(self) -> None:
        raw = bytearray(DMA_QUEUE_CONTROL_BYTES)
        _put_u16(raw, 0x00, 4)
        _put_u16(raw, 0x02, 5)
        _put_u32(raw, 0x04, 480)
        _put_u16(raw, 0x08, 440)
        _put_u16(raw, 0x0A, 1)
        parsed = parse_dma_queue_control(bytes(raw))
        self.assertEqual(4, parsed["published_depth"])
        self.assertEqual(5, parsed["build_depth"])
        self.assertEqual(480, parsed["build_words"])
        self.assertEqual(440, parsed["commit_words"])
        self.assertEqual(1, parsed["frame_pending"])

    def test_parsers_reject_wrong_sizes(self) -> None:
        for parser, size in (
            (parse_debug_budget, DEBUG_BUDGET_BYTES),
            (parse_renderer_state, RENDERER_STATE_USED_BYTES),
            (parse_dma_queue_control, DMA_QUEUE_CONTROL_BYTES),
            (parse_audio_shadow, AUDIO_SHADOW_BYTES),
        ):
            with self.subTest(parser=parser.__name__):
                with self.assertRaises(ValueError):
                    parser(bytes(size - 1))
                with self.assertRaises(ValueError):
                    parser(bytes(size + 1))


class M4BudgetTranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.terminal = 0x1234
        self.forbidden = {
            "VBlankInterrupt": 0x2000,
            "MainLoopPostGameplayStep": 0x2100,
            "DmaCommitPreparedQueue": 0x2200,
        }

    def test_one_breakpoint_one_continue_is_uninterrupted(self) -> None:
        policy = analyze_transcript(
            synthetic_transcript(1)[1:-1],
            terminal_address=self.terminal,
            forbidden_addresses=self.forbidden,
        )
        self.assertEqual("pass", policy["status"])
        self.assertEqual(1, policy["continue_packets"])
        self.assertEqual([], policy["debugger_write_packets"])
        self.assertEqual(["> c", "< S05"], policy[
            "measured_interval_transcript"
        ])

    def test_extra_execution_or_restart_command_fails(self) -> None:
        for command in ("s", "vCont;s", "c1234", "C05", "R"):
            with self.subTest(command=command):
                lines = synthetic_transcript(1)[1:-1]
                continue_index = lines.index("> c")
                lines[continue_index:continue_index] = [
                    f"> {command}",
                    "< S05",
                ]
                policy = analyze_transcript(
                    lines,
                    terminal_address=self.terminal,
                    forbidden_addresses=self.forbidden,
                )
                self.assertEqual("fail", policy["status"])

    def test_forbidden_vblank_breakpoint_fails(self) -> None:
        policy = analyze_transcript(
            [
                "> Z0,1234,2",
                "< OK",
                "> Z0,2000,2",
                "< OK",
                "> c",
                "< S05",
            ],
            terminal_address=self.terminal,
            forbidden_addresses=self.forbidden,
        )
        self.assertEqual("fail", policy["status"])
        self.assertEqual(
            ["VBlankInterrupt"],
            policy["forbidden_breakpoint_symbols_observed"],
        )

    def test_post_step_stop_or_interrupt_fails(self) -> None:
        for extra in (
            ["> Z0,2100,2", "< OK"],
            ["> <interrupt>", "< S05"],
            ["> c", "< S05"],
        ):
            with self.subTest(extra=extra):
                policy = analyze_transcript(
                    [
                        "> Z0,1234,2",
                        "< OK",
                        *extra,
                        "> c",
                        "< S05",
                    ],
                    terminal_address=self.terminal,
                    forbidden_addresses=self.forbidden,
                )
                self.assertEqual("fail", policy["status"])

    def test_guest_write_packet_fails(self) -> None:
        for packet in (
            "G" + "00" * 8,
            "Mff0000,1:08",
            "P11=00001234",
            "Xff0000,1:x",
        ):
            with self.subTest(packet=packet):
                policy = analyze_transcript(
                    [
                        "> Z0,1234,2",
                        "< OK",
                        "> c",
                        "< S05",
                        f"> {packet}",
                        "< OK",
                    ],
                    terminal_address=self.terminal,
                    forbidden_addresses=self.forbidden,
                )
                self.assertEqual("fail", policy["status"])
                self.assertEqual(
                    [f"> {packet}"],
                    policy["debugger_write_packets"],
                )


class M4BudgetEvaluatorTests(unittest.TestCase):
    def test_nominal_two_session_report_passes(self) -> None:
        report = synthetic_report()
        self.assertEqual(REPORT_SCHEMA, report["schema"])
        self.assertEqual(EVIDENCE_TYPE, report["evidence_type"])
        self.assertEqual("pass", report["status"])
        self.assertEqual([], report["debugger_write_packets"])
        self.assertEqual([], report_contract_errors(report))
        self.assertTrue(
            all(item["status"] == "pass" for item in report["checks"])
        )

    def test_sorted_json_round_trip_preserves_recomputed_checks(self) -> None:
        report = synthetic_report()
        loaded = json.loads(json.dumps(report, sort_keys=True))
        self.assertEqual(report["checks"], loaded["checks"])
        self.assertEqual([], report_contract_errors(loaded))

    def test_cross_clock_accepts_exact_startup_allowance_boundary(
        self,
    ) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        for session in sessions:
            observation = session["observation"]
            set_z80_heartbeat(
                observation,
                observation["debug"]["frame_counter"]
                + Z80_VINT_STARTUP_ALLOWANCE,
            )
        report = build_budget_report(
            rom_sha256=SHA,
            sessions=sessions,
            provenance=synthetic_provenance(),
        )
        self.assertEqual(
            "pass",
            check_status(report, CROSS_CLOCK_CHECK),
        )
        self.assertEqual("pass", report["status"])

    def test_cross_clock_underflow_or_excess_fails_closed(self) -> None:
        for delta in (-1, Z80_VINT_STARTUP_ALLOWANCE + 1):
            with self.subTest(delta=delta):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                observation = sessions[0]["observation"]
                set_z80_heartbeat(
                    observation,
                    observation["debug"]["frame_counter"] + delta,
                )
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual(
                    "fail",
                    check_status(report, CROSS_CLOCK_CHECK),
                )
                self.assertEqual("fail", report["status"])

    def test_cross_clock_derived_record_cannot_hide_debug_drift(
        self,
    ) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["observation"]["cross_clock"][
            "z80_vint_excess"
        ] = 0
        report = build_budget_report(
            rom_sha256=SHA,
            sessions=sessions,
            provenance=synthetic_provenance(),
        )
        self.assertEqual(
            "fail",
            check_status(report, CROSS_CLOCK_CHECK),
        )
        self.assertEqual("fail", report["status"])

    def test_audio_health_mutations_fail_closed(self) -> None:
        mutations = (
            ("audio_state", AUDIO_STATE_PLAYING - 1),
            (
                "audio_command_count",
                AUDIO_COMMAND_COUNT_EXPECTED + 1,
            ),
            ("m2_flags", M2_HEALTHY_FLAGS & ~0x20),
            ("z80_timeout_count", 1),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observation"]["debug"][field] = value
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual(
                    "fail",
                    check_status(report, AUDIO_HEALTH_CHECK),
                )
                self.assertEqual("fail", report["status"])

    def test_every_required_fault_counter_fails_closed(self) -> None:
        faults = (
            ("renderer", "backlog_frames"),
            ("renderer", "dropped_sprites"),
            ("renderer", "dma_rejects"),
            ("debug", "vblank_overrun_count"),
            ("debug", "dma_overflow_count"),
            ("debug", "dma_deferred_count"),
            ("debug", "dma_required_drop_count"),
            ("debug", "dma_invalid_count"),
            ("debug", "z80_timeout_count"),
        )
        for section, field in faults:
            with self.subTest(field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observation"][section][field] = 1
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual("fail", report["status"])
                self.assertEqual(
                    "fail",
                    check_status(
                        report, "all_cumulative_fault_counters_zero"
                    ),
                )

    def test_pending_or_nonempty_terminal_queue_fails_closed(self) -> None:
        for field, value in (
            ("frame_pending", 1),
            ("published_depth", 1),
            ("build_depth", 1),
            ("build_words", 440),
        ):
            with self.subTest(field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observation"]["dma_queue"][field] = value
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual("fail", report["status"])
                self.assertEqual(
                    "fail",
                    check_status(
                        report, "terminal_queue_fully_committed"
                    ),
                )

    def test_dma_capacity_overruns_fail_closed(self) -> None:
        mutations = (
            ("debug", "dma_words_frame", DMA_ORDINARY_WORD_LIMIT + 1),
            ("debug", "dma_words_max", DMA_ORDINARY_WORD_LIMIT + 1),
            ("debug", "dma_queue_high_water", DMA_QUEUE_CAPACITY + 1),
            ("dma_queue", "commit_words", DMA_ORDINARY_WORD_LIMIT + 1),
        )
        for section, field, value in mutations:
            with self.subTest(field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observation"][section][field] = value
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual(
                    "fail",
                    check_status(report, "dma_capacities_in_bounds"),
                )
                self.assertEqual("fail", report["status"])

    def test_renderer_capacity_overruns_fail_closed(self) -> None:
        mutations = (
            ("sprite_count", 65),
            ("scanline_max", 21),
            ("scanline_pixel_max", 321),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observation"]["renderer"][field] = value
                if field == "sprite_count":
                    sessions[0]["observation"]["debug"][
                        "sprite_count"
                    ] = value
                if field == "scanline_max":
                    sessions[0]["observation"]["debug"][
                        "sprite_scanline_max"
                    ] = value
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual(
                    "fail",
                    check_status(
                        report, "renderer_capacities_in_bounds"
                    ),
                )
                self.assertEqual("fail", report["status"])

    def test_wrong_terminal_or_renderer_alignment_fails(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[0]["observation"]["engine"]["tick"] = 719
        sessions[1]["observation"]["renderer"]["last_tick"] = 718
        report = build_budget_report(
            rom_sha256=SHA,
            sessions=sessions,
            provenance=synthetic_provenance(),
        )
        self.assertEqual(
            "fail",
            check_status(report, "natural_tick_720_timeout_boundary"),
        )
        self.assertEqual(
            "fail",
            check_status(
                report, "renderer_abi_and_terminal_alignment"
            ),
        )
        self.assertEqual("fail", report["status"])

    def test_missing_fresh_session_or_hash_drift_fails(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        sessions[1]["metadata"]["process_id"] = sessions[0][
            "metadata"
        ]["process_id"]
        provenance = synthetic_provenance()
        provenance["hash_verification"]["status"] = "fail"
        provenance["hash_verification"]["drift"] = ["rom"]
        report = build_budget_report(
            rom_sha256=SHA,
            sessions=sessions,
            provenance=provenance,
        )
        self.assertEqual(
            "fail",
            check_status(report, "two_fresh_owned_sessions"),
        )
        self.assertEqual(
            "fail",
            check_status(
                report, "exact_hash_bindings_and_no_drift"
            ),
        )
        self.assertEqual("fail", report["status"])

    def test_session_hash_inconsistency_fails_closed(self) -> None:
        mutations = (
            ("runtime_copy", "executable_sha256"),
            ("runtime_copy", "default_config_sha256"),
            ("controller_profile", "source_sha256"),
            ("metadata", "transcript_sha256"),
        )
        for section, field in mutations:
            with self.subTest(section=section, field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                if section == "metadata":
                    sessions[0]["metadata"][field] = "b" * 64
                else:
                    sessions[0]["metadata"][section][field] = "b" * 64
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual(
                    "fail",
                    check_status(
                        report, "exact_hash_bindings_and_no_drift"
                    ),
                )
                self.assertEqual("fail", report["status"])

    def test_report_contract_rejects_false_pass(self) -> None:
        report = synthetic_report()
        report["checks"][0]["status"] = "fail"
        self.assertIn(
            "passing report contains a failed check",
            report_contract_errors(report),
        )
        report = synthetic_report()
        report["debugger_write_packets"] = ["> Mff0000,1:00"]
        self.assertIn(
            "passing report contains debugger writes",
            report_contract_errors(report),
        )
        report = synthetic_report()
        report["checks"] = [
            item
            for item in report["checks"]
            if item["check"] != CROSS_CLOCK_CHECK
        ]
        self.assertIn(
            "passing report check set/order does not match the exact "
            "required contract",
            report_contract_errors(report),
        )
        report = synthetic_report()
        report["sessions"][0]["observation"]["cross_clock"][
            "z80_vint_excess"
        ] = 0
        self.assertIn(
            "session 1 cross-clock derivation does not match the debug "
            "block",
            report_contract_errors(report),
        )

    def test_report_contract_rejects_raw_and_parsed_mismatch(self) -> None:
        report = synthetic_report()
        report["sessions"][0]["observation"]["renderer"][
            "backlog_frames"
        ] = 123
        self.assertTrue(
            any(
                "does not match renderer" in error
                for error in report_contract_errors(report)
            )
        )

        report = synthetic_report()
        report["sessions"][0]["observation"]["blocks"][
            "debug_budget"
        ]["sha256"] = "0" * 64
        self.assertTrue(
            any(
                "debug_budget SHA-256 mismatch" in error
                for error in report_contract_errors(report)
            )
        )

    def test_consistent_raw_backlog_cannot_keep_pass_rows(self) -> None:
        report = synthetic_report()
        mutate_raw_block(
            report["sessions"][0],
            "renderer_state",
            0x1E,
            (1).to_bytes(2, "big"),
        )
        errors = report_contract_errors(report)
        self.assertIn(
            "reported semantic checks do not match recomputed "
            "observations",
            errors,
        )

    def test_omitted_checks_duplicate_sessions_and_provenance_fail(self) -> None:
        report = synthetic_report()
        report["checks"] = report["checks"][:2]
        self.assertTrue(report_contract_errors(report))

        report = synthetic_report()
        report["sessions"][1]["session_id"] = "session_1"
        report["sessions"][1]["metadata"]["process_id"] = report[
            "sessions"
        ][0]["metadata"]["process_id"]
        self.assertIn(
            "passing report session IDs/order are invalid",
            report_contract_errors(report),
        )

        report = synthetic_report()
        report["provenance"]["rom"]["sha256"] = "b" * 64
        self.assertIn(
            "provenance ROM hashes do not match report ROM",
            report_contract_errors(report),
        )

    def test_audio_shadow_faults_and_queue_bounds_fail_live_evaluator(
        self,
    ) -> None:
        mutations = (
            ("flags", AUDIO_STATUS_FLAGS_HEALTHY | 0x10),
            ("late_tick_count", 1),
            ("queue_high_water", 17),
            ("ym_write_count", 0),
            ("psg_write_count", 0),
            ("last_error_or_command", 0xE1),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                sessions = [
                    synthetic_session(1),
                    synthetic_session(2),
                ]
                sessions[0]["observation"]["audio_shadow"][field] = value
                report = build_budget_report(
                    rom_sha256=SHA,
                    sessions=sessions,
                    provenance=synthetic_provenance(),
                )
                self.assertEqual(
                    "fail", check_status(report, AUDIO_HEALTH_CHECK)
                )
                self.assertEqual("fail", report["status"])

    def test_terminal_frame_must_be_exact_and_pre_wrap(self) -> None:
        sessions = [synthetic_session(1), synthetic_session(2)]
        observation = sessions[0]["observation"]
        observation["debug"]["frame_counter"] = (
            ENGINE_ATTEMPT_TICKS + 1
        )
        observation["debug"]["heartbeat"] = ENGINE_ATTEMPT_TICKS + 1
        set_z80_heartbeat(
            observation, ENGINE_ATTEMPT_TICKS + 26
        )
        report = build_budget_report(
            rom_sha256=SHA,
            sessions=sessions,
            provenance=synthetic_provenance(),
        )
        self.assertEqual(
            "fail", check_status(report, "runtime_debug_health")
        )
        self.assertEqual("fail", check_status(report, CROSS_CLOCK_CHECK))
        self.assertEqual("fail", report["status"])

    def test_public_report_does_not_emit_private_transcript_lines(
        self,
    ) -> None:
        report = synthetic_report()
        self.assertTrue(
            all(
                "_transcript_lines" not in session
                for session in report["sessions"]
            )
        )


class M4BudgetSchemaAndLauncherTests(unittest.TestCase):
    def test_schema_is_strictly_bound_to_report_identity(self) -> None:
        schema_path = ROOT / SCHEMA_RELATIVE_PATH
        schema = json.loads(schema_path.read_text("utf-8"))
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema",
            schema["$schema"],
        )
        self.assertEqual(
            REPORT_SCHEMA,
            schema["properties"]["schema"]["const"],
        )
        self.assertEqual(
            EVIDENCE_TYPE,
            schema["properties"]["evidence_type"]["const"],
        )
        self.assertIn("allOf", schema)
        self.assertEqual(
            2,
            schema["allOf"][0]["then"]["properties"]["sessions"][
                "minItems"
            ],
        )
        self.assertEqual(
            0,
            schema["allOf"][0]["then"]["properties"][
                "debugger_write_packets"
            ]["maxItems"],
        )
        pass_check_refs = schema["allOf"][0]["then"]["properties"][
            "checks"
        ]["prefixItems"]
        required_pass_checks = {
            schema["$defs"][item["$ref"].rsplit("/", 1)[-1]][
                "allOf"
            ][1]["properties"]["check"]["const"]
            for item in pass_check_refs
        }
        self.assertEqual(
            set(REQUIRED_CHECK_NAMES),
            required_pass_checks,
        )
        self.assertEqual(15, len(pass_check_refs))
        debug_schema = schema["$defs"]["debug_observation"]
        self.assertTrue(
            {
                "audio_state",
                "z80_heartbeat",
                "audio_command_count",
                "m2_flags",
            }.issubset(debug_schema["required"])
        )
        cross_clock = schema["$defs"]["cross_clock_observation"]
        self.assertEqual(
            Z80_VINT_STARTUP_ALLOWANCE,
            cross_clock["properties"]["startup_allowance_vints"][
                "const"
            ],
        )
        passing_cross_clock = schema["$defs"]["passing_session"][
            "allOf"
        ][1]["properties"]["observation"]["properties"][
            "cross_clock"
        ]["properties"]["z80_vint_excess"]
        self.assertEqual(0, passing_cross_clock["minimum"])
        self.assertEqual(
            Z80_VINT_STARTUP_ALLOWANCE,
            passing_cross_clock["maximum"],
        )
        observation = schema["$defs"]["session"]["properties"][
            "observation"
        ]
        self.assertIn("audio_shadow", observation["required"])
        self.assertIn(
            "audio_shadow",
            observation["properties"]["blocks"]["required"],
        )
        self.assertEqual(
            "00FF9900",
            schema["$defs"]["audio_hash_block"]["allOf"][1][
                "properties"
            ]["address"]["const"],
        )
        passing_observation = schema["$defs"]["passing_session"][
            "allOf"
        ][1]["properties"]["observation"]["properties"]
        self.assertEqual(
            0,
            passing_observation["renderer"]["properties"][
                "backlog_frames"
            ]["const"],
        )
        self.assertEqual(
            0,
            schema["$defs"]["audio_shadow_observation"][
                "properties"
            ]["late_tick_count"]["const"],
        )

    def test_launcher_requires_exact_rom_hash(self) -> None:
        launcher = (
            ROOT / "tools/m4_budget_probe.ps1"
        ).read_text("utf-8")
        self.assertIn("[Parameter(Mandatory = $true)]", launcher)
        self.assertIn(
            '[ValidatePattern("^[0-9A-Fa-f]{64}$")]',
            launcher,
        )
        self.assertIn(
            '"--expected-sha256", $ExpectedSha256.ToLowerInvariant()',
            launcher,
        )
        self.assertNotIn("m4_visual_evidence.py", launcher)
        self.assertIn(
            "foreach ($Artifact in @($Report, $Trace))", launcher
        )
        invalidation = launcher.index(
            "foreach ($Artifact in @($Report, $Trace))"
        )
        self.assertLess(
            invalidation,
            launcher.index(
                'if (-not (Test-Path -LiteralPath $PythonExe'
            ),
        )

    def test_limitations_are_explicit_and_nonempty(self) -> None:
        report = synthetic_report()
        joined = " ".join(report["limitations"])
        self.assertIn("not a CPU-cycle profiler", joined)
        self.assertIn("at most 32 more Z80 hardware V-ints", joined)
        self.assertIn("observed 25-V-int", joined)
        self.assertIn("PAL double-step", joined)
        self.assertIn("Release-ROM equivalence", joined)
        self.assertIn("cannot waive", joined)


if __name__ == "__main__":
    unittest.main()
