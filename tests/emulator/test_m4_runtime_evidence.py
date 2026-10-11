"""Cross-platform adversarial tests for Milestone 4 live evidence tooling."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.m2_evidence import EvidenceFailure
from tools.m4_reference.contracts import load_gameplay_contract
from tools.m4_reference.model import (
    GameplayModel,
    canonical_b01_first_attempt,
    run_b01_solution,
)
from tools.replay_tools import decode_replay, encode_replay
from tools.m4_runtime_evidence import (
    CANONICAL_ATTEMPT_0,
    CANONICAL_ATTEMPT_1,
    ENGINE_CONTROL_REGION_BYTES,
    ENGINE_PROJECTION_BYTES,
    FIRST_RUNNING_VBLANK_ROLE,
    GAMEPLAY_CONTROL_USED_BYTES,
    GOLDEN_B01_PROJECTION_CRC,
    INPUT_B,
    INPUT_C,
    INPUT_RIGHT,
    REPLAY_TIMELINE_BYTES,
    STARTUP_PRIMING_ROLE,
    CompatibilityIds,
    _read_region,
    build_canonical_replay,
    build_runtime_report,
    completion_signature,
    crc32_iso_hdlc,
    derive_compatibility_ids,
    debugger_write_packets,
    input_summary,
    make_snapshot,
    parse_actor,
    parse_b01_device,
    parse_gameplay_control,
    parse_replay_timeline,
)


ROOT = Path(__file__).resolve().parents[2]
FAKE_SHA = "a" * 64
COMPATIBILITY = CompatibilityIds(
    engine_compat_id=0x01020304,
    state_schema_id=0x05060708,
    content_id=0x1122334455667788,
)


def _put16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = (value & 0xFFFF).to_bytes(2, "big")


def _put32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "big")


def _control(model: GameplayModel) -> bytes:
    c = model.c
    raw = bytearray(ENGINE_CONTROL_REGION_BYTES)
    raw[0:4] = b"R94E"
    _put16(raw, 4, 1)
    _put16(raw, 6, ENGINE_CONTROL_REGION_BYTES)
    _put16(raw, 8, model.state)
    _put16(raw, 10, model.engine_flags)
    _put16(raw, 12, 0)
    _put16(raw, 14, model.attempt)
    _put16(raw, 16, model.tick)
    _put16(raw, 18, model.terminal_reason)
    _put16(raw, 20, model.last_error)
    _put16(raw, 22, model.overflow_flags)
    _put16(raw, 24, ENGINE_PROJECTION_BYTES)
    _put32(raw, 26, model.state_crc)
    _put16(raw, 30, model.checkpoint_tick)
    _put16(raw, 32, model.checkpoint_count)
    raw[34] = model.input_held
    raw[35] = model.input_pressed
    raw[36] = model.input_released
    raw[37] = model.input_previous
    raw[38] = model.facing_x & 0xFF
    raw[39] = model.active_actor_count
    room = model.contract.fixture.room
    for index, value in enumerate(room):
        _put16(raw, 40 + index * 2, value)
    spawn = model.contract.fixture.spawn
    _put32(raw, 48, spawn[0] << 8)
    _put32(raw, 52, spawn[1] << 8)
    _put16(raw, 56, len(model.contract.fixture.rects))
    _put16(raw, 58, len(model.contract.fixture.objects))
    for pool_type in range(1, c["POOL_TYPE_COUNT"] + 1):
        pool = model.pools[pool_type]
        raw[64 + pool_type - 1] = pool.count
        raw[72 + pool_type - 1] = pool.high_water
    return bytes(raw)


def _gameplay(model: GameplayModel) -> bytes:
    raw = bytearray(GAMEPLAY_CONTROL_USED_BYTES)
    raw[0:4] = b"R94G"
    _put16(raw, 4, 1)
    _put16(raw, 6, 0x100)
    _put16(raw, 8, model.gameplay_flags)
    _put16(raw, 10, model.gameplay_result)
    _put16(raw, 12, model.door_state)
    _put16(raw, 14, model.switch_mask)
    raw[16] = 6
    raw[17] = len(model.echo_order)
    raw[18] = model.current_input
    raw[19] = model.current_pressed
    raw[20] = model.current_released
    raw[21] = model.current_previous
    _put16(raw, 22, 0)
    _put16(raw, 24, model.last_commit_reason)
    _put16(raw, 26, model.gameplay_replacement_flags)
    _put32(raw, 28, model.last_bank_crc)
    _put16(raw, 32, model.stable_tick)
    _put16(raw, 34, model.step_count)
    return bytes(raw)


def _input(
    held: int = 0,
    pressed: int = 0,
    released: int = 0,
    *,
    start: int = 0,
) -> bytes:
    return bytes((held, pressed, released, 0, 0, 1, start))


def _snapshot(
    label: str,
    model: GameplayModel,
    *,
    input_raw: bytes,
    full: bool,
) -> dict:
    return make_snapshot(
        label=label,
        control_raw=_control(model),
        gameplay_raw=_gameplay(model),
        replay_raw=model.replay_region_bytes(),
        input_raw=input_raw,
        ordinary_raw=model.ordinary_state_bytes() if full else None,
        projection_raw=model.projection if full else None,
    )


def _models() -> tuple[GameplayModel, GameplayModel, GameplayModel, GameplayModel]:
    contract = load_gameplay_contract(
        ROOT, verify_source_hashes=False
    )
    initial = GameplayModel(contract)
    pre = GameplayModel(contract)
    for value in canonical_b01_first_attempt(contract.constants):
        assert pre.step(value) == 0
    reset = GameplayModel(contract)
    for value in canonical_b01_first_attempt(contract.constants):
        assert reset.step(value) == 0
    assert reset.commit_and_reset(
        contract.constants["GAMEPLAY_COMMIT_MANUAL"]
    ) == 0
    terminal = GameplayModel(contract)
    assert run_b01_solution(terminal) == 1
    return initial, pre, reset, terminal


def _session(number: int) -> dict:
    initial, pre, reset, terminal = _models()
    return {
        "session_id": f"session_{number}",
        "metadata": {
            "process_id": 9000 + number,
            "launch_flags": ["-D", "-g", "-m", "gen", "-r", "U"],
            "initial_breakpoint_symbol": "VBlankInterrupt",
            "initial_breakpoint": "00001566",
            "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
            "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
            "counters_and_controller_poll_begin": (
                FIRST_RUNNING_VBLANK_ROLE
            ),
            "step_breakpoint_label": "MainLoopPostGameplayStep",
            "step_breakpoint": "000002EC",
            "step_breakpoint_source": "build/recursion94-debug.sym",
            "attempt_1_input_staging_breakpoint_symbol": (
                "TimelineHandleMetaInput"
            ),
            "attempt_1_input_staging_role": (
                "after_controller_poll_before_commit_reset"
            ),
            "runtime_copy": {
                "status": "pass",
                "temporary_directory": f"C:/Temp/m4-{number}/blastem",
                "executable_sha256": FAKE_SHA,
            },
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "sha256": f"{number}" * 64,
                "session_isolation": {"status": "pass"},
            },
            "input_events": [
                "Right:down",
                "Right:up",
                "Start:down",
                "C:down",
                "C:up",
                "Start:up",
                "Right:down",
                "B:down",
                "B:up",
                "Right:up",
            ],
            "transcript_sha256": "b" * 64,
        },
        "attempt_0_inputs": list(CANONICAL_ATTEMPT_0),
        "attempt_1_inputs": list(CANONICAL_ATTEMPT_1),
        "initial": _snapshot(
            "initial_attempt_0_tick_0",
            initial,
            input_raw=_input(),
            full=True,
        ),
        "pre_manual_commit": _snapshot(
            "attempt_0_tick_40_pre_manual_commit",
            pre,
            input_raw=_input(),
            full=False,
        ),
        "manual_commit_injection": {
            "status": "pass",
            "method": "targeted PostMessageW",
            "keys": ["Enter", "D"],
            "buttons": ["Start", "C"],
            "staged_keydown_order": ["Start", "C"],
            "next_attempt_events_staged_at": (
                "TimelineHandleMetaInput after ControllerPoll"
            ),
            "authoritative_frames_consumed": 0,
            "guest_ram_or_pc_writes": False,
        },
        "manual_commit_entry": _snapshot(
            "attempt_0_tick_40_start_c_meta_entry",
            pre,
            input_raw=_input(INPUT_C, INPUT_C, start=1),
            full=False,
        ),
        "post_manual_commit": _snapshot(
            "post_start_c_attempt_1_tick_0",
            reset,
            input_raw=_input(INPUT_RIGHT, INPUT_RIGHT, released=INPUT_C),
            full=True,
        ),
        "terminal": _snapshot(
            "program_stable_attempt_1_tick_166",
            terminal,
            input_raw=_input(INPUT_RIGHT | INPUT_B, INPUT_B),
            full=True,
        ),
        "_transcript_lines": [
            f"# session_{number} begin",
            "> Z0,2ec,2",
            "< OK",
            "> mff0000,ff",
            "< 00",
            "> z0,2ec,2",
            "< OK",
            "> k",
            f"# session_{number} end",
        ],
    }


def _provenance() -> dict:
    return {
        "rom": {"path": "build/recursion94-debug.bin", "sha256": FAKE_SHA},
        "emulator": {"path": ".toolcache/blastem/blastem.exe", "sha256": FAKE_SHA},
        "default_config": {
            "path": ".toolcache/blastem/default.cfg",
            "source_sha256": FAKE_SHA,
        },
        "symbol_map": {
            "path": "build/recursion94-debug.sym",
            "sha256": FAKE_SHA,
        },
        "transcript": {
            "path": "artifacts/traces/m4-runtime-gdb-transcript.log",
            "sha256": FAKE_SHA,
        },
    }


def _compatibility_documentation() -> dict:
    return {
        "engine_compat_id": {
            "id": COMPATIBILITY.engine_compat_id,
            "id_hex": "01020304",
            "bundle_sha256": FAKE_SHA,
            "sources": {},
        },
        "state_schema_id": {
            "id": COMPATIBILITY.state_schema_id,
            "id_hex": "05060708",
            "bundle_sha256": FAKE_SHA,
            "sources": {},
        },
        "content_id": {
            "id": COMPATIBILITY.content_id,
            "id_hex": "1122334455667788",
            "source_sha256": FAKE_SHA,
        },
    }


def _report(sessions: list[dict] | None = None) -> dict:
    values = sessions if sessions is not None else [_session(1), _session(2)]
    canonical = build_canonical_replay(
        sessions=values, compatibility=COMPATIBILITY
    )
    return build_runtime_report(
        rom_sha256=FAKE_SHA,
        sessions=values,
        provenance=_provenance(),
        compatibility_documentation=_compatibility_documentation(),
        canonical=canonical,
        replay_path="artifacts/replays/B01.r94r",
    )


def _status(report: dict, check: str) -> str:
    return next(
        item["status"] for item in report["checks"] if item["check"] == check
    )


def _private_keys(value: object, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and key.startswith("_"):
                found.append(child_path)
            found.extend(_private_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_private_keys(child, f"{path}[{index}]"))
    return found


class FakeRemote:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[tuple[int, int]] = []

    def read_memory(self, address: int, size: int) -> bytes:
        self.requests.append((address, size))
        return self.payload[address : address + size]


class M4RuntimeEvidenceTests(unittest.TestCase):
    def test_canonical_constants_are_exact(self) -> None:
        self.assertEqual(40, len(CANONICAL_ATTEMPT_0))
        self.assertEqual(
            bytes((INPUT_RIGHT,)) * 32 + bytes(8),
            CANONICAL_ATTEMPT_0,
        )
        self.assertEqual(166, len(CANONICAL_ATTEMPT_1))
        self.assertEqual(INPUT_RIGHT | INPUT_B, CANONICAL_ATTEMPT_1[-1])
        self.assertEqual(
            0x96501AB7, crc32_iso_hdlc(CANONICAL_ATTEMPT_0)
        )

    def test_replay_timeline_parser_rejects_both_boundaries(self) -> None:
        with self.assertRaisesRegex(EvidenceFailure, "5119"):
            parse_replay_timeline(bytes(REPLAY_TIMELINE_BYTES - 1))
        with self.assertRaisesRegex(EvidenceFailure, "5121"):
            parse_replay_timeline(bytes(REPLAY_TIMELINE_BYTES + 1))

    def test_live_model_serializations_parse_exactly(self) -> None:
        session = _session(1)
        reset = session["post_manual_commit"]
        replay = parse_replay_timeline(reset["_replay_bytes"])
        self.assertEqual(40, replay["descriptors"][6]["length"])
        self.assertEqual(0x96501AB7, replay["descriptors"][6]["crc32_iso_hdlc"])
        self.assertEqual([6, 255, 255, 255, 255, 255], replay["timeline"]["echo_order"])
        terminal = session["terminal"]
        self.assertEqual(7, terminal["ordinary"]["b01_device"]["state"])
        self.assertEqual(80 << 8, terminal["ordinary"]["echo_actor"]["x_16_8"])
        self.assertEqual(
            GOLDEN_B01_PROJECTION_CRC,
            int(terminal["projection"]["crc32_iso_hdlc"], 16),
        )

    def test_parsers_reject_truncation_and_bad_slots(self) -> None:
        with self.assertRaisesRegex(EvidenceFailure, "35 bytes"):
            parse_gameplay_control(bytes(GAMEPLAY_CONTROL_USED_BYTES - 1))
        with self.assertRaises(ValueError):
            parse_actor(bytes(0x4800), 7)
        with self.assertRaisesRegex(EvidenceFailure, "truncated"):
            parse_b01_device(bytes(1))

    def test_blastem_reads_are_never_larger_than_255(self) -> None:
        payload = bytes(index & 0xFF for index in range(700))
        remote = FakeRemote(payload)
        observed = _read_region(remote, 0, len(payload))
        self.assertEqual(payload, observed)
        self.assertTrue(remote.requests)
        self.assertTrue(all(size <= 0xFF for _address, size in remote.requests))
        with self.assertRaises(ValueError):
            _read_region(remote, 0, 1, chunk_size=0x100)

    def test_completion_signature_binds_every_observed_field(self) -> None:
        fields = {
            "stage_id": 101,
            "attempts_used": 2,
            "terminal_ticks": 166,
            "terminal_reason": 0x0100,
            "active_echoes": 1,
            "gameplay_result": 1,
            "state_crc32": 0x8762E745,
            "history_crc32": crc32_iso_hdlc(
                CANONICAL_ATTEMPT_0 + CANONICAL_ATTEMPT_1
            ),
        }
        baseline = completion_signature(**fields)
        for key in fields:
            changed = dict(fields)
            changed[key] ^= 1
            with self.subTest(key=key):
                self.assertNotEqual(
                    baseline, completion_signature(**changed)
                )

    def test_full_history_replay_is_truthful_and_round_trips(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        replay = canonical["replay"]
        self.assertEqual("FULL_HISTORY", replay.mode.name)
        self.assertEqual(1, replay.outcome.active_echoes)
        self.assertEqual(166, replay.outcome.terminal_ticks)
        self.assertEqual(GOLDEN_B01_PROJECTION_CRC, replay.outcome.state_crc32)
        self.assertEqual(CANONICAL_ATTEMPT_0, replay.attempts[0].inputs)
        self.assertEqual(CANONICAL_ATTEMPT_1, replay.attempts[1].inputs)
        self.assertEqual(replay, canonical["decoded"])
        self.assertEqual(canonical["encoded"], encode_replay(replay))

    def test_passing_report_has_no_private_payloads(self) -> None:
        report = _report()
        self.assertEqual("pass", report["status"])
        self.assertTrue(all(item["status"] == "pass" for item in report["checks"]))
        self.assertEqual([], _private_keys(report))
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("_projection_bytes", serialized)
        self.assertNotIn("_ordinary_bytes", serialized)
        self.assertNotIn("_replay_bytes", serialized)

    def test_mutated_actual_input_schedule_fails(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        sessions[1]["attempt_1_inputs"][10] = 0
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=_provenance(),
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail", _status(report, "actual_attempt_input_schedules")
        )
        self.assertEqual("fail", _status(report, "two_runs_are_byte_identical"))

    def test_mutated_bank_six_crc_or_tail_fails(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        raw = bytearray(sessions[1]["post_manual_commit"]["_replay_bytes"])
        raw[6 * 720 + 50] = 0x7F
        sessions[1]["post_manual_commit"]["_replay_bytes"] = bytes(raw)
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=_provenance(),
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail",
            _status(report, "bank_6_partial_length_crc_and_zero_tail"),
        )
        self.assertEqual("fail", _status(report, "two_runs_are_byte_identical"))

    def test_mutated_projection_cannot_self_authenticate(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        terminal = sessions[1]["terminal"]
        raw = bytearray(terminal["_projection_bytes"])
        raw[-1] ^= 1
        changed_crc = crc32_iso_hdlc(raw)
        terminal["_projection_bytes"] = bytes(raw)
        terminal["projection"]["crc32_iso_hdlc"] = f"{changed_crc:08X}"
        terminal["projection"]["published_crc32_iso_hdlc"] = f"{changed_crc:08X}"
        terminal["projection"]["published_crc_matches"] = True
        terminal["engine"]["state_crc"] = changed_crc
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=_provenance(),
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail",
            _status(
                report,
                "independent_terminal_projection_crc_8762e745",
            ),
        )
        self.assertEqual("fail", _status(report, "two_runs_are_byte_identical"))

    def test_debugger_ram_write_packet_fails_closed(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        sessions[0]["_transcript_lines"].append("> Mff0000,1:08")
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=_provenance(),
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail", _status(report, "read_only_debugger_session")
        )
        self.assertEqual(["> Mff0000,1:08"], report["debugger_write_packets"])

    def test_missing_startup_priming_role_fails_closed(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        sessions[0]["metadata"].pop("initial_breakpoint_role")
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=_provenance(),
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail",
            _status(report, "symbol_bound_post_gameplay_observation"),
        )

    def test_missing_attempt_input_pre_reset_staging_fails_closed(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        sessions[0]["metadata"].pop("attempt_1_input_staging_role")
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=_provenance(),
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail",
            _status(
                report,
                "attempt_1_input_staged_before_commit_reset",
            ),
        )

    def test_fresh_session_identity_and_hash_binding_fail_closed(self) -> None:
        sessions = [_session(1), _session(2)]
        canonical = build_canonical_replay(
            sessions=sessions, compatibility=COMPATIBILITY
        )
        sessions[1]["metadata"]["process_id"] = sessions[0]["metadata"]["process_id"]
        provenance = _provenance()
        provenance["symbol_map"]["sha256"] = "not-a-hash"
        report = build_runtime_report(
            rom_sha256=FAKE_SHA,
            sessions=sessions,
            provenance=provenance,
            compatibility_documentation=_compatibility_documentation(),
            canonical=canonical,
            replay_path="artifacts/replays/B01.r94r",
        )
        self.assertEqual(
            "fail", _status(report, "two_fresh_independent_sessions")
        )
        self.assertEqual(
            "fail", _status(report, "hash_bound_dynamic_provenance")
        )

    def test_replay_builder_rejects_ram_observation_disagreement(self) -> None:
        sessions = [_session(1), _session(2)]
        sessions[0]["attempt_0_inputs"][0] = 0
        with self.assertRaisesRegex(EvidenceFailure, "bank 6 differs"):
            build_canonical_replay(
                sessions=sessions, compatibility=COMPATIBILITY
            )

    def test_source_compatibility_derivation_is_deterministic(self) -> None:
        first, first_documentation = derive_compatibility_ids(ROOT)
        second, second_documentation = derive_compatibility_ids(ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first_documentation, second_documentation)
        self.assertNotEqual(0, first.engine_compat_id)
        self.assertNotEqual(0, first.state_schema_id)
        self.assertNotEqual(0, first.content_id)

    def test_input_summary_binds_full_bytes(self) -> None:
        summary = input_summary(CANONICAL_ATTEMPT_0)
        self.assertEqual(40, summary["bytes"])
        self.assertEqual("96501AB7", summary["crc32_iso_hdlc"])
        self.assertTrue(summary["reserved_bits_zero"])
        changed = bytearray(CANONICAL_ATTEMPT_0)
        changed[0] |= 0x80
        self.assertFalse(input_summary(bytes(changed))["reserved_bits_zero"])

    def test_committed_report_replay_and_trace_bind_frozen_history(
        self,
    ) -> None:
        report_path = ROOT / "artifacts/reports/m4-runtime.json"
        replay_path = ROOT / "artifacts/replays/B01.r94r"
        trace_path = (
            ROOT / "artifacts/traces/m4-runtime-gdb-transcript.log"
        )
        rom_path = (
            ROOT
            / "artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5"
            / "recursion94-debug.bin"
        )
        for path in (
            report_path,
            replay_path,
            trace_path,
            rom_path,
        ):
            self.assertTrue(path.is_file(), path)
        report = json.loads(report_path.read_text("utf-8"))
        self.assertEqual("pass", report["status"])
        self.assertEqual(
            hashlib.sha256(rom_path.read_bytes()).hexdigest(),
            report["rom_sha256"],
        )
        self.assertEqual(
            "c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460",
            report["rom_sha256"],
        )
        self.assertEqual(
            report["rom_sha256"],
            report["provenance"]["rom"]["sha256"],
        )
        self.assertRegex(
            report["provenance"]["symbol_map"]["sha256"],
            r"^[0-9a-f]{64}$",
        )
        replay_bytes = replay_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(replay_bytes).hexdigest(),
            report["canonical_replay"]["sha256"],
        )
        trace_bytes = trace_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(trace_bytes).hexdigest(),
            report["provenance"]["transcript"]["sha256"],
        )
        self.assertEqual([], debugger_write_packets(
            trace_bytes.decode("utf-8").splitlines()
        ))
        reported_ids = report["canonical_replay"]["compatibility_ids"]
        for name in ("engine_compat_id", "state_schema_id"):
            item = reported_ids[name]
            canonical = json.dumps(
                item["sources"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
            bundle_sha = hashlib.sha256(canonical).hexdigest()
            self.assertEqual(bundle_sha, item["bundle_sha256"])
            self.assertEqual(
                int.from_bytes(bytes.fromhex(bundle_sha)[:4], "big"),
                item["id"],
            )
        content = reported_ids["content_id"]
        self.assertRegex(content["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            int.from_bytes(
                bytes.fromhex(content["source_sha256"])[:8], "big"
            ),
            content["id"],
        )
        frozen_compatibility = CompatibilityIds(
            engine_compat_id=reported_ids["engine_compat_id"]["id"],
            state_schema_id=reported_ids["state_schema_id"]["id"],
            content_id=reported_ids["content_id"]["id"],
        )
        replay = decode_replay(replay_bytes, frozen_compatibility)
        self.assertEqual(2, len(replay.attempts))
        self.assertEqual(1, replay.outcome.active_echoes)
        self.assertEqual(166, replay.outcome.terminal_ticks)
        self.assertEqual(
            GOLDEN_B01_PROJECTION_CRC, replay.outcome.state_crc32
        )
        self.assertTrue(
            all(item["status"] == "pass" for item in report["checks"])
        )
        self.assertEqual([], _private_keys(report))

    def test_suite_is_host_platform_neutral(self) -> None:
        self.assertIn(os.name, {"nt", "posix"})
        # Live Windows APIs are outside these pure tests; every evaluated
        # parser, report check, and replay round trip is OS-independent.
        self.assertFalse(any(
            "os.name" in function.__code__.co_names
            for function in (
                parse_replay_timeline,
                parse_gameplay_control,
                completion_signature,
                build_canonical_replay,
                build_runtime_report,
            )
        ))


if __name__ == "__main__":
    unittest.main()
