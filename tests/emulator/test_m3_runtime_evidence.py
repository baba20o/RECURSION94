"""Pure adversarial tests for the live Milestone 3 runtime evidence report."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.m2_evidence import EvidenceFailure
from tools.m3_runtime_evidence import (
    ACTOR_CAPACITY,
    ENGINE_CHECKPOINT_TICKS,
    ENGINE_CONTROL_REGION_BYTES,
    ENGINE_MAGIC,
    ENGINE_ORDINARY_BYTES,
    ENGINE_PROJECTION_BYTES,
    ENGINE_RESETTABLE_REGION_OFFSET,
    ENGINE_STATE_ACTIVE,
    ENGINE_STATE_TERMINAL,
    ENGINE_TERMINAL_TIMEOUT,
    FIRST_RUNNING_VBLANK_ROLE,
    POOL_LAYOUT,
    PROJECTION_HEADER_BYTES,
    STARTUP_PRIMING_ROLE,
    _read_region,
    build_runtime_report,
    crc32_iso_hdlc,
    debugger_write_packets,
    make_checkpoint_snapshot,
    make_control_observation,
    parse_engine_control,
    parse_input_state,
    parse_projection,
)


def _put16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = (value & 0xFFFF).to_bytes(2, "big")


def _put32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "big")


def _actor_specs(attempt: int) -> list[tuple[int, int, int]]:
    if attempt == 0:
        return [(6, 6, 6)]
    if attempt == 1:
        return [(0, 0, 6), (6, 6, 0)]
    raise ValueError("fixture supports attempts 0 and 1")


def _write_actor(
    raw: bytearray,
    offset: int,
    *,
    role: int,
    logical_id: int,
) -> None:
    raw[offset] = 1
    raw[offset + 1] = role
    _put32(raw, offset + 0x04, 32 << 8)
    _put32(raw, offset + 0x08, 112 << 8)
    _put16(raw, offset + 0x14, 4)
    _put16(raw, offset + 0x16, 4)
    _put16(raw, offset + 0x1A, 1)
    _put16(raw, offset + 0x1C, logical_id)


def _projection(*, tick: int, attempt: int, state: int, terminal: int) -> bytes:
    raw = bytearray(ENGINE_PROJECTION_BYTES)
    raw[0:4] = b"R94P"
    _put16(raw, 0x04, 1)
    _put16(raw, 0x06, ENGINE_PROJECTION_BYTES)
    _put16(raw, 0x08, 0)
    _put16(raw, 0x0A, attempt)
    _put16(raw, 0x0C, tick)
    _put16(raw, 0x0E, state)
    _put16(raw, 0x10, terminal)
    _put16(raw, 0x12, 0x0003)
    _put16(raw, 0x14, 0)
    _put16(raw, 0x16, 0)
    _put16(raw, 0x18, 0)
    _put16(raw, 0x1A, 0)
    _put16(raw, 0x1C, 320)
    _put16(raw, 0x1E, 224)
    raw[0x24] = 1
    raw[0x25] = len(_actor_specs(attempt))
    _put16(raw, 0x26, 0)

    cursor = PROJECTION_HEADER_BYTES
    for slot, role, logical_id in _actor_specs(attempt):
        _write_actor(
            raw,
            cursor + slot * 0x1E,
            role=role,
            logical_id=logical_id,
        )
    cursor += ACTOR_CAPACITY * 0x1E
    for pool_type, _name, _address, capacity in POOL_LAYOUT:
        raw[cursor] = pool_type
        raw[cursor + 1] = capacity
        raw[cursor + 2] = 0
        raw[cursor + 3] = 0
        cursor += 4 + capacity * 0x18
    assert cursor == ENGINE_PROJECTION_BYTES
    return bytes(raw)


def _ordinary(attempt: int) -> bytes:
    raw = bytearray(ENGINE_ORDINARY_BYTES)
    for slot, role, logical_id in _actor_specs(attempt):
        _write_actor(
            raw,
            slot * 0x20,
            role=role,
            logical_id=logical_id,
        )
    return bytes(raw)


def _control(
    *,
    tick: int,
    attempt: int,
    state: int,
    terminal: int,
    checkpoint_tick: int,
    checkpoint_count: int,
    projection: bytes,
    full: bool = True,
) -> bytes:
    raw = bytearray(ENGINE_CONTROL_REGION_BYTES if full else 0x50)
    raw[0:4] = b"R94E"
    _put16(raw, 0x04, 1)
    _put16(raw, 0x06, ENGINE_CONTROL_REGION_BYTES)
    _put16(raw, 0x08, state)
    _put16(raw, 0x0A, 0x0003)
    _put16(raw, 0x0C, 0)
    _put16(raw, 0x0E, attempt)
    _put16(raw, 0x10, tick)
    _put16(raw, 0x12, terminal)
    _put16(raw, 0x14, 0)
    _put16(raw, 0x16, 0)
    _put16(raw, 0x18, ENGINE_PROJECTION_BYTES)
    _put32(raw, 0x1A, crc32_iso_hdlc(projection))
    _put16(raw, 0x1E, checkpoint_tick)
    _put16(raw, 0x20, checkpoint_count)
    raw[0x26] = 1
    raw[0x27] = len(_actor_specs(attempt))
    _put16(raw, 0x28, 0)
    _put16(raw, 0x2A, 0)
    _put16(raw, 0x2C, 320)
    _put16(raw, 0x2E, 224)
    _put32(raw, 0x30, 32 << 8)
    _put32(raw, 0x34, 112 << 8)
    return bytes(raw)


def _snapshot(tick: int, *, attempt: int = 0) -> dict:
    terminal = ENGINE_TERMINAL_TIMEOUT if tick == 720 else 0
    state = ENGINE_STATE_TERMINAL if tick == 720 else ENGINE_STATE_ACTIVE
    projection = _projection(
        tick=tick,
        attempt=attempt,
        state=state,
        terminal=terminal,
    )
    return make_checkpoint_snapshot(
        label=f"tick_{tick:03d}",
        expected_tick=tick,
        control_raw=_control(
            tick=tick,
            attempt=attempt,
            state=state,
            terminal=terminal,
            checkpoint_tick=tick,
            checkpoint_count=tick // 60 + 1,
            projection=projection,
        ),
        ordinary_raw=_ordinary(attempt),
        projection_raw=projection,
    )


def _session(number: int) -> dict:
    checkpoints = [_snapshot(tick) for tick in ENGINE_CHECKPOINT_TICKS]
    checkpoint_660 = checkpoints[-2]
    tick_719_raw = bytearray(
        _control(
            tick=719,
            attempt=0,
            state=ENGINE_STATE_ACTIVE,
            terminal=0,
            checkpoint_tick=660,
            checkpoint_count=12,
            projection=checkpoint_660["_projection_bytes"],
            full=False,
        )
    )
    reset = _snapshot(0, attempt=1)
    post_reset_projection = reset["_projection_bytes"]
    post_reset = make_control_observation(
        label="post_reset_tick_001",
        expected_tick=1,
        control_raw=_control(
            tick=1,
            attempt=1,
            state=ENGINE_STATE_ACTIVE,
            terminal=0,
            checkpoint_tick=0,
            checkpoint_count=1,
            projection=post_reset_projection,
            full=False,
        ),
    )
    lines = [
        f"# session_{number} begin",
        "> Z0,1566,2",
        "< OK",
        "> c",
        "< S05",
        "> z0,1566,2",
        "< OK",
        "> k",
        f"# session_{number} end",
    ]
    return {
        "session_id": f"session_{number}",
        "metadata": {
            "process_id": 1000 + number,
            "launch_flags": ["-D", "-g", "-m", "gen", "-r", "U"],
            "initial_breakpoint_symbol": "VBlankInterrupt",
            "initial_breakpoint": "00001566",
            "initial_breakpoint_role": STARTUP_PRIMING_ROLE,
            "authoritative_origin": FIRST_RUNNING_VBLANK_ROLE,
            "counters_and_controller_poll_begin": (
                FIRST_RUNNING_VBLANK_ROLE
            ),
            "step_breakpoint_label": "MainLoopPostGameplayStepCall",
            "step_breakpoint": "000002E0",
            "step_breakpoint_source": "build/recursion94-debug.sym",
            "reset_breakpoint_label": "MainLoopPostCommitReset",
            "reset_breakpoint": "000002EC",
            "reset_breakpoint_source": "build/recursion94-debug.sym",
            "runtime_copy": {
                "status": "pass",
                "temporary_directory": f"C:/Temp/m3-{number}/blastem",
                "executable_sha256": "b" * 64,
            },
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "sha256": f"{number}" * 64,
                "session_isolation": {"status": "pass"},
            },
            "transcript_sha256": "c" * 64,
        },
        "progression": list(range(721)),
        "checkpoints": checkpoints,
        "tick_719": make_control_observation(
            label="tick_719",
            expected_tick=719,
            control_raw=bytes(tick_719_raw),
        ),
        "terminal_pre_input": {
            "held": 0,
            "pressed": 0,
            "released": 0,
            "raw_high": 0,
            "raw_low": 0,
            "connected": 1,
            "start_held": 0,
        },
        "transition": {
            "status": "pass",
            "method": "unmodified timeout commit/reset branch",
            "cause": "timeout",
            "guest_input_injection": False,
            "guest_ram_register_pc_writes": False,
        },
        "reset_input": {
            "held": 0,
            "pressed": 0,
            "released": 0,
            "raw_high": 0,
            "raw_low": 0,
            "connected": 1,
            "start_held": 0,
        },
        "reset": reset,
        "post_reset_input": {
            "held": 0,
            "pressed": 0,
            "released": 0,
            "raw_high": 0,
            "raw_low": 0,
            "connected": 1,
            "start_held": 0,
        },
        "post_reset": post_reset,
        "_transcript_lines": lines,
    }


def _provenance() -> dict:
    return {
        "rom": {"sha256": "a" * 64},
        "emulator": {"sha256": "b" * 64},
        "default_config": {"source_sha256": "d" * 64},
        "symbol_map": {"sha256": "e" * 64},
        "transcript": {"sha256": "f" * 64},
    }


def _report(sessions: list[dict]) -> dict:
    return build_runtime_report(
        rom_sha256="a" * 64,
        sessions=sessions,
        provenance=_provenance(),
    )


def _status(report: dict, name: str) -> str:
    return next(
        item["status"] for item in report["checks"] if item["check"] == name
    )


class RuntimeParsersTests(unittest.TestCase):
    def test_crc32_iso_hdlc_known_vectors(self) -> None:
        self.assertEqual(crc32_iso_hdlc(b""), 0)
        self.assertEqual(crc32_iso_hdlc(b"123456789"), 0xCBF43926)

    def test_control_and_projection_parse_explicit_big_endian_shape(self) -> None:
        projection = _projection(
            tick=720,
            attempt=0,
            state=ENGINE_STATE_TERMINAL,
            terminal=ENGINE_TERMINAL_TIMEOUT,
        )
        control = parse_engine_control(
            _control(
                tick=720,
                attempt=0,
                state=ENGINE_STATE_TERMINAL,
                terminal=ENGINE_TERMINAL_TIMEOUT,
                checkpoint_tick=720,
                checkpoint_count=13,
                projection=projection,
            )
        )
        parsed_projection = parse_projection(projection)
        self.assertEqual(control["magic"], ENGINE_MAGIC)
        self.assertEqual(control["tick"], 720)
        self.assertEqual(control["checkpoint_count"], 13)
        self.assertEqual(
            parsed_projection["header"]["magic_ascii"], "R94P"
        )
        self.assertEqual(parsed_projection["header"]["bytes"], 3638)
        self.assertTrue(parsed_projection["bounds_valid"])

    def test_malformed_reads_and_pool_headers_fail_closed(self) -> None:
        with self.assertRaises(EvidenceFailure):
            parse_engine_control(bytes(0x4F))
        with self.assertRaises(EvidenceFailure):
            parse_input_state(bytes(6))
        with self.assertRaises(EvidenceFailure):
            parse_projection(bytes(ENGINE_PROJECTION_BYTES - 1))
        malformed = bytearray(
            _projection(
                tick=0,
                attempt=0,
                state=ENGINE_STATE_ACTIVE,
                terminal=0,
            )
        )
        pool_header = PROJECTION_HEADER_BYTES + ACTOR_CAPACITY * 0x1E
        malformed[pool_header + 1] = 15
        with self.assertRaises(EvidenceFailure):
            parse_projection(bytes(malformed))

    def test_write_packet_classifier_catches_ram_and_pc_writes(self) -> None:
        lines = [
            "> mff1800,20",
            "> Z0,1566,2",
            "> Mff1800,1:00",
            "> P11=00000000",
            "> G0011",
            "> Xff1800,1:x",
        ]
        self.assertEqual(
            debugger_write_packets(lines),
            lines[2:],
        )

    def test_large_regions_are_assembled_from_bounded_255_byte_reads(
        self,
    ) -> None:
        class Remote:
            def __init__(self) -> None:
                self.requests: list[tuple[int, int]] = []

            def read_memory(self, address: int, size: int) -> bytes:
                self.requests.append((address, size))
                if size > 0xFF:
                    raise AssertionError("BlastEm request exceeded 255 bytes")
                return bytes(
                    (address + offset) & 0xFF for offset in range(size)
                )

        remote = Remote()
        payload = _read_region(remote, 0xFF1800, 0x4800)
        self.assertEqual(len(payload), 0x4800)
        self.assertEqual(max(size for _address, size in remote.requests), 0xFF)
        self.assertEqual(
            sum(size for _address, size in remote.requests), 0x4800
        )
        self.assertEqual(
            payload,
            bytes((0xFF1800 + offset) & 0xFF for offset in range(0x4800)),
        )
        with self.assertRaises(ValueError):
            _read_region(remote, 0xFF1800, 1, chunk_size=0x100)


class RuntimeReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = [_session(1), _session(2)]

    def test_complete_two_session_fixture_passes_and_is_json_safe(self) -> None:
        report = _report(self.sessions)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["terminal_projection"]["bytes_equal"])
        self.assertEqual(len(report["checkpoint_sequences"]), 2)
        self.assertEqual(len(report["checkpoint_sequences"][0]), 13)
        serialized = json.dumps(report)
        self.assertGreater(len(serialized), 1000)

        def keys(value: object) -> list[str]:
            if isinstance(value, dict):
                return [
                    *value.keys(),
                    *[
                        nested
                        for child in value.values()
                        for nested in keys(child)
                    ],
                ]
            if isinstance(value, list):
                return [
                    nested for child in value for nested in keys(child)
                ]
            return []

        self.assertNotIn("_projection_bytes", keys(report))
        self.assertNotIn("_ordinary_bytes", keys(report))

    def test_flat_checkpoint_crcs_fail(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        for session in sessions:
            first = session["checkpoints"][0]["_projection_bytes"]
            for checkpoint in session["checkpoints"]:
                checkpoint["_projection_bytes"] = first
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "checkpoint_crc_sequence_not_flat"), "fail"
        )

    def test_divergent_session_crc_sequence_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        target = sessions[1]["checkpoints"][5]
        changed = bytearray(target["_projection_bytes"])
        changed[PROJECTION_HEADER_BYTES + 8] ^= 1
        changed_bytes = bytes(changed)
        changed_crc = crc32_iso_hdlc(changed_bytes)
        target["_projection_bytes"] = changed_bytes
        target["control"]["state_crc"] = changed_crc
        target["projection"]["crc32_iso_hdlc"] = f"{changed_crc:08X}"
        target["projection"]["sha256"] = __import__("hashlib").sha256(
            changed_bytes
        ).hexdigest()
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "repeated_checkpoint_sequences_match"), "fail"
        )

    def test_checkpoint_tick_off_by_one_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["checkpoints"][-1]["control"]["tick"] = 719
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(_status(report, "checkpoint_tick_schedule"), "fail")
        self.assertEqual(
            _status(report, "exact_tick_720_timeout_terminal"), "fail"
        )

    def test_unbound_post_step_observation_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["metadata"]["step_breakpoint_label"] = (
            ".untrusted_local_label"
        )
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "stable_post_step_observation"), "fail"
        )

    def test_missing_startup_priming_role_fails_closed(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["metadata"].pop("initial_breakpoint_role")
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "stable_post_step_observation"), "fail"
        )

    def test_unbound_automatic_reset_transition_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["metadata"]["reset_breakpoint_label"] = (
            "UntrustedResetSite"
        )
        sessions[0]["transition"]["guest_input_injection"] = True
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "automatic_timeout_reset_binding"), "fail"
        )

    def test_fake_reset_attempt_or_ordinary_state_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["reset"]["control"]["attempt"] = 0
        ordinary = bytearray(sessions[1]["reset"]["_ordinary_bytes"])
        ordinary[ENGINE_RESETTABLE_REGION_OFFSET + 4] ^= 1
        sessions[1]["reset"]["_ordinary_bytes"] = bytes(ordinary)
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(
                report,
                "automatic_timeout_commit_reset_transition",
            ),
            "fail",
        )
        self.assertEqual(
            _status(report, "reset_restores_canonical_pool_region"),
            "fail",
        )

    def test_noncanonical_post_commit_actor_roster_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["reset"]["ordinary"]["bounds"]["actor_records"][0][
            "logical_id"
        ] = 5
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "reset_reinstantiates_canonical_actor_roster"),
            "fail",
        )

    def test_debugger_write_packet_fails_read_only_gate(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["_transcript_lines"].append("> Mff1800,1:00")
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "read_only_debugger_session"), "fail"
        )
        self.assertEqual(report["debugger_write_packets"], ["> Mff1800,1:00"])

    def test_projection_corruption_fails_independent_crc_gate(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        target = sessions[0]["checkpoints"][3]
        changed = bytearray(target["_projection_bytes"])
        changed[PROJECTION_HEADER_BYTES + 12] ^= 0x80
        target["_projection_bytes"] = bytes(changed)
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "independent_crc32_matches_every_publication"),
            "fail",
        )

    def test_overflow_and_pool_bound_violation_fail(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        sessions[0]["checkpoints"][4]["control"]["overflow_flags"] = 1
        sessions[1]["checkpoints"][4]["control"]["pool_counts"][1] = 33
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(_status(report, "no_error_or_overflow"), "fail")
        self.assertEqual(_status(report, "actor_and_pool_bounds"), "fail")

    def test_terminal_projection_byte_divergence_fails(self) -> None:
        sessions = copy.deepcopy(self.sessions)
        target = sessions[1]["checkpoints"][-1]
        changed = bytearray(target["_projection_bytes"])
        changed[PROJECTION_HEADER_BYTES + 4] ^= 1
        changed_bytes = bytes(changed)
        changed_crc = crc32_iso_hdlc(changed_bytes)
        target["_projection_bytes"] = changed_bytes
        target["control"]["state_crc"] = changed_crc
        target["projection"]["crc32_iso_hdlc"] = f"{changed_crc:08X}"
        target["projection"]["sha256"] = __import__("hashlib").sha256(
            changed_bytes
        ).hexdigest()
        report = _report(sessions)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            _status(report, "terminal_projection_bytes_and_hashes_match"),
            "fail",
        )


if __name__ == "__main__":
    unittest.main()
