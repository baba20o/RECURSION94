"""Adversarial, cross-platform tests for the live M5 evidence collector."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.m2_evidence import EvidenceFailure
from tools import m5_runtime_evidence as m5


ROOT = Path(__file__).resolve().parents[2]
FAKE_SHA = "a" * 64
SYMBOLS = {
    "CampaignRuntimeProfileB01": 0x00016038,
    "CampaignRuntimeSemanticsB01": 0x00015FF0,
    "CampaignRuntimeDependencyGroupsB01": 0x00016020,
    "CampaignRuntimeDependencyEdgesB01": 0x00016030,
    "CampaignRuntimeRectsB01": 0x00015F90,
    "CampaignRuntimeObjectsB01": 0x00015FC0,
}


def _put16(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 2] = (value & 0xFFFF).to_bytes(2, "big")


def _put32(raw: bytearray, offset: int, value: int) -> None:
    raw[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "big")


def _input(
    held: int = 0,
    pressed: int = 0,
    released: int = 0,
) -> bytes:
    return bytes((held, pressed, released, 0, 0, 1, 0))


def _vocabulary() -> bytes:
    raw = bytearray(m5.VOCAB_STATE_BYTES)
    raw[0:4] = b"R95V"
    _put16(raw, 0x04, m5.VOCAB_VERSION)
    _put16(raw, 0x06, m5.VOCAB_STATE_BYTES)
    _put16(raw, 0x08, m5.VOCAB_PROJECTION_BYTES)
    _put16(raw, 0x0A, m5.VOCAB_FLAG_INITIALIZED)
    _put16(raw, 0x16, m5.B01_STAGE_ID)

    campaign = m5.VOCAB_CAMPAIGN_OFFSET
    _put16(
        raw,
        campaign,
        m5.VOCAB_CAMPAIGN_FLAG_ACTIVE
        | m5.VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,
    )
    raw[campaign + 0x02] = m5.B01_DIRECTORY_INDEX
    raw[campaign + 0x03] = m5.B01_ECHO_CAP
    raw[campaign + 0x04] = m5.B01_SEMANTIC_COUNT
    raw[campaign + 0x05] = m5.B01_GROUP_COUNT
    raw[campaign + 0x06] = m5.B01_EDGE_COUNT
    raw[campaign + 0x07] = m5.B01_COMPLETION_OBJECT
    raw[campaign + 0x08] = m5.B01_COMPLETION_FIELD
    raw[campaign + 0x09] = m5.B01_COMPLETION_FLAGS
    _put16(raw, campaign + 0x0A, m5.B01_COMPLETION_MASK)
    _put16(raw, campaign + 0x0C, m5.B01_COMPLETION_VALUE)
    raw[campaign + 0x30 : campaign + 0x32] = b"\xFF\xFF"

    derived = m5.VOCAB_DERIVED_OFFSET
    _put32(raw, derived + 0x00, SYMBOLS["CampaignRuntimeProfileB01"])
    _put32(raw, derived + 0x04, SYMBOLS["CampaignRuntimeSemanticsB01"])
    _put32(
        raw,
        derived + 0x08,
        SYMBOLS["CampaignRuntimeDependencyGroupsB01"],
    )
    _put32(
        raw,
        derived + 0x0C,
        SYMBOLS["CampaignRuntimeDependencyEdgesB01"],
    )
    return bytes(raw)


def _descriptor() -> bytes:
    raw = bytearray(m5.ENCOUNTER_DESCRIPTOR_BYTES)
    _put16(raw, 0x00, m5.B01_DIRECTORY_INDEX)
    _put16(raw, 0x04, 1)
    _put16(raw, 0x06, m5.ENCOUNTER_DESCRIPTOR_BYTES)
    for index, value in enumerate(m5.B01_ROOM):
        _put16(raw, 0x08 + index * 2, value)
    _put16(raw, 0x10, m5.B01_SPAWN[0])
    _put16(raw, 0x12, m5.B01_SPAWN[1])
    _put16(raw, 0x14, m5.B01_RECTS)
    _put16(raw, 0x16, m5.B01_OBJECTS)
    _put32(raw, 0x18, SYMBOLS["CampaignRuntimeRectsB01"])
    _put32(raw, 0x1C, SYMBOLS["CampaignRuntimeObjectsB01"])
    return bytes(raw)


def _devices() -> bytes:
    raw = bytearray(m5.DEVICE_CAPACITY * m5.OBJECT_BYTES)
    for slot, (
        logical_id,
        state,
        x,
        y,
        timer,
        param,
    ) in enumerate(m5.B01_DEVICES):
        offset = slot * m5.OBJECT_BYTES
        raw[offset] = 1
        raw[offset + 1] = logical_id
        _put16(raw, offset + 0x02, state)
        _put32(raw, offset + 0x04, x << 8)
        _put32(raw, offset + 0x08, y << 8)
        _put16(raw, offset + 0x14, timer)
        _put16(raw, offset + 0x16, param)
    return bytes(raw)


def _actor(*, x: int, held: int, previous: int) -> bytes:
    raw = bytearray(m5.ACTOR_BYTES)
    raw[0] = 1
    raw[1] = 6
    _put32(raw, 0x04, x)
    _put32(raw, 0x08, m5.B01_SPAWN[1] << 8)
    _put16(raw, 0x14, 4)
    _put16(raw, 0x16, 4)
    raw[0x18] = held
    raw[0x19] = previous
    _put16(raw, 0x1A, 1)
    _put16(raw, 0x1C, 6)
    return bytes(raw)


def _projection(
    *,
    tick: int,
    input_raw: bytes,
    actor_raw: bytes,
    vocabulary_raw: bytes,
    device_raw: bytes,
) -> bytes:
    raw = bytearray(m5.CAMPAIGN_PROJECTION_BYTES)
    raw[0:4] = b"R94P"
    _put16(raw, 0x04, m5.CAMPAIGN_PROJECTION_VERSION)
    _put16(raw, 0x06, m5.CAMPAIGN_PROJECTION_BYTES)
    _put16(raw, 0x08, m5.B01_DIRECTORY_INDEX)
    _put16(raw, 0x0A, 0)
    _put16(raw, 0x0C, tick)
    _put16(raw, 0x0E, m5.ENGINE_STATE_ACTIVE)
    _put16(
        raw,
        0x12,
        m5.ENGINE_FLAG_LOADED | m5.ENGINE_FLAG_PROJECTION_VALID,
    )
    for index, value in enumerate(m5.B01_ROOM):
        _put16(raw, 0x18 + index * 2, value)
    raw[0x20:0x24] = input_raw[:4]
    raw[0x24] = 1
    raw[0x25] = 1
    _put16(raw, 0x26, m5.B01_RECTS)

    cursor = m5.PROJECTION_HEADER_BYTES
    cursor += 6 * m5.PROJECTION_ACTOR_BYTES
    raw[cursor : cursor + m5.PROJECTION_ACTOR_BYTES] = actor_raw[
        : m5.PROJECTION_ACTOR_BYTES
    ]
    cursor = (
        m5.PROJECTION_HEADER_BYTES
        + m5.ACTOR_CAPACITY * m5.PROJECTION_ACTOR_BYTES
    )
    for pool_type, _name, _address, capacity in m5.POOL_LAYOUT:
        raw[cursor] = pool_type
        raw[cursor + 1] = capacity
        if pool_type == 3:
            raw[cursor + 2] = m5.B01_OBJECTS
            raw[cursor + 3] = m5.B01_OBJECTS
        cursor += m5.PROJECTION_POOL_HEADER_BYTES
        if pool_type == 3:
            raw[cursor : cursor + len(device_raw)] = device_raw
        cursor += capacity * m5.OBJECT_BYTES
    if cursor != m5.ENGINE_LEGACY_PROJECTION_BYTES:
        raise AssertionError(f"synthetic projection cursor is {cursor}")
    raw[cursor:] = vocabulary_raw[: m5.VOCAB_PROJECTION_BYTES]
    return bytes(raw)


def _control(
    *,
    tick: int,
    input_raw: bytes,
    projection_raw: bytes,
) -> bytes:
    raw = bytearray(m5.ENGINE_CONTROL_USED_BYTES)
    raw[0:4] = b"R94E"
    _put16(raw, 0x04, 1)
    _put16(raw, 0x06, 0x0400)
    _put16(raw, 0x08, m5.ENGINE_STATE_ACTIVE)
    _put16(
        raw,
        0x0A,
        m5.ENGINE_FLAG_LOADED | m5.ENGINE_FLAG_PROJECTION_VALID,
    )
    _put16(raw, 0x0C, m5.B01_DIRECTORY_INDEX)
    _put16(raw, 0x0E, 0)
    _put16(raw, 0x10, tick)
    _put16(raw, 0x18, m5.CAMPAIGN_PROJECTION_BYTES)
    _put32(raw, 0x1A, m5.crc32_iso_hdlc(projection_raw))
    _put16(raw, 0x1E, tick - tick % 60)
    _put16(raw, 0x20, tick // 60 + 1)
    raw[0x22:0x26] = input_raw[:4]
    raw[0x26] = 1
    raw[0x27] = 1
    for index, value in enumerate(m5.B01_ROOM):
        _put16(raw, 0x28 + index * 2, value)
    _put32(raw, 0x30, m5.B01_SPAWN[0] << 8)
    _put32(raw, 0x34, m5.B01_SPAWN[1] << 8)
    _put16(raw, 0x38, m5.B01_RECTS)
    _put16(raw, 0x3A, m5.B01_OBJECTS)
    raw[0x42] = m5.B01_OBJECTS
    raw[0x4A] = m5.B01_OBJECTS
    return bytes(raw)


def _snapshot(
    tick: int,
    *,
    x: int,
    held: int = 0,
    pressed: int = 0,
    released: int = 0,
    previous: int = 0,
) -> dict:
    vocabulary = _vocabulary()
    devices = _devices()
    actor = _actor(x=x, held=held, previous=previous)
    input_raw = _input(held, pressed, released)
    projection = _projection(
        tick=tick,
        input_raw=input_raw,
        actor_raw=actor,
        vocabulary_raw=vocabulary,
        device_raw=devices,
    )
    return m5.make_snapshot(
        label=f"attempt_0_tick_{tick}",
        control_raw=_control(
            tick=tick,
            input_raw=input_raw,
            projection_raw=projection,
        ),
        vocabulary_raw=vocabulary,
        descriptor_raw=_descriptor(),
        device_raw=devices,
        actor_raw=actor,
        input_raw=input_raw,
        projection_raw=projection,
        symbols=SYMBOLS,
    )


def _x_at_tick(tick: int) -> int:
    if tick < m5.RIGHT_FIRST_TICK:
        return m5.B01_SPAWN[0] << 8
    moved = min(tick, m5.RIGHT_LAST_TICK) - m5.RIGHT_FIRST_TICK + 1
    return (m5.B01_SPAWN[0] + moved * 2) << 8


def _input_for_tick(tick: int) -> tuple[int, int, int, int]:
    held = m5.expected_held(tick)
    pressed = m5.expected_pressed(tick)
    released = m5.expected_released(tick)
    previous = m5.expected_held(tick - 1) if tick > 0 else 0
    return held, pressed, released, previous


def _session(number: int) -> dict:
    captures = {}
    for tick in m5.CAPTURE_TICKS:
        held, pressed, released, previous = _input_for_tick(tick)
        captures[str(tick)] = _snapshot(
            tick,
            x=_x_at_tick(tick),
            held=held,
            pressed=pressed,
            released=released,
            previous=previous,
        )
    observations = []
    for tick in range(1, m5.SCHEDULE_FINAL_TICK + 1):
        held, pressed, released, _previous = _input_for_tick(tick)
        observations.append(
            {
                "tick": tick,
                **m5.parse_input_state(_input(held, pressed, released)),
            }
        )
    return {
        "session_id": f"session_{number}",
        "metadata": {
            "process_id": 5000 + number,
            "launch_flags": list(m5.LAUNCH_FLAGS),
            "initial_breakpoint_symbol": "VBlankInterrupt",
            "initial_breakpoint_role": m5.STARTUP_PRIMING_ROLE,
            "authoritative_origin": m5.FIRST_RUNNING_VBLANK_ROLE,
            "step_breakpoint_symbol": "MainLoopPostGameplayStep",
            "symbol_map_sha256": FAKE_SHA,
            "runtime_copy": {
                "status": "pass",
                "temporary_copy_removed_after_session": True,
                "temporary_directory": f"C:/Temp/m5-{number}",
            },
            "controller_profile": {
                "status": "pass",
                "port_1_assignment": "gamepad3.1",
                "session_isolation": {"status": "pass"},
            },
            "input_transport": "targeted_PostMessageW",
        },
        "initial": _snapshot(0, x=m5.B01_SPAWN[0] << 8),
        "captures": captures,
        "observed_inputs": observations,
        "_transcript_lines": [
            f"# session_{number} begin",
            "> Z0,312,2",
            "< OK",
            "> mff1400,50",
            "< 00",
            "> z0,312,2",
            "< OK",
            "> k",
            f"# session_{number} end",
        ],
    }


def _report(sessions: list[dict] | None = None) -> dict:
    return m5.build_runtime_report(
        rom_sha256=FAKE_SHA,
        symbol_sha256=FAKE_SHA,
        sessions=sessions or [_session(1), _session(2)],
        provenance={"boot_contract": "directory index 0 / B01"},
    )


def _status(report: dict, check: str) -> str:
    return next(
        item["status"] for item in report["checks"] if item["check"] == check
    )


class M5RuntimeEvidenceTests(unittest.TestCase):
    def test_valid_two_session_b01_slice_passes(self) -> None:
        report = _report()
        self.assertEqual(report["status"], "pass")
        self.assertTrue(
            all(item["bytes_match"] for item in report["checkpoint_comparison"])
        )
        self.assertFalse(report["schedule"]["guest_memory_register_pc_writes"])

    def test_projection_rejects_legacy_schema_and_wrong_length(self) -> None:
        snapshot = _snapshot(0, x=m5.B01_SPAWN[0] << 8)
        projection = bytearray(snapshot["_projection_bytes"])
        _put16(projection, 0x04, 1)
        with self.assertRaisesRegex(EvidenceFailure, "schema is 1"):
            m5.parse_extended_projection(bytes(projection))

        projection = bytearray(snapshot["_projection_bytes"])
        _put16(projection, 0x06, m5.CAMPAIGN_PROJECTION_BYTES - 1)
        with self.assertRaisesRegex(EvidenceFailure, "projection declares"):
            m5.parse_extended_projection(bytes(projection))

    def test_abi_corruption_and_checkpoint_drift_fail(self) -> None:
        sessions = [_session(1), _session(2)]
        sessions[0]["initial"]["vocabulary"]["campaign"][
            "semantic_count"
        ] = 4
        report = _report(sessions)
        self.assertEqual(
            _status(report, "b01_campaign_abi_v3_initialized"),
            "fail",
        )

        sessions = [_session(1), _session(2)]
        drift = bytearray(sessions[1]["captures"]["60"]["_projection_bytes"])
        drift[-1] ^= 1
        sessions[1]["captures"]["60"]["_projection_bytes"] = bytes(drift)
        report = _report(sessions)
        self.assertEqual(
            _status(report, "two_session_checkpoint_bytes_match"),
            "fail",
        )

    def test_repeated_press_edge_and_debugger_write_are_detected(self) -> None:
        sessions = [_session(1), _session(2)]
        sessions[0]["observed_inputs"][5]["pressed"] = m5.INPUT_RIGHT
        report = _report(sessions)
        self.assertEqual(
            _status(report, "right_press_hold_release_edges"),
            "fail",
        )

        sessions = [_session(1), _session(2)]
        sessions[0]["_transcript_lines"].append("> Mff1600,1:00")
        report = _report(sessions)
        self.assertEqual(_status(report, "read_only_debugger"), "fail")
        self.assertEqual(report["debugger_write_packets"], ["> Mff1600,1:00"])

    def test_unplanned_b_edge_cannot_be_mistaken_for_neutral_schedule(
        self,
    ) -> None:
        sessions = [_session(1), _session(2)]
        sessions[0]["observed_inputs"][59]["pressed"] = m5.INPUT_B
        report = _report(sessions)
        self.assertEqual(
            _status(report, "bounded_real_pad_schedule_observed"),
            "fail",
        )

    def test_collector_source_has_no_guest_write_callsite(self) -> None:
        source = (ROOT / "tools/m5_runtime_evidence.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".write_memory(", source)
        self.assertIsNone(
            re.search(
                r"""remote\.request\(\s*f?["'](?:G|M|P|X)""",
                source,
            )
        )

    def test_source_binding_covers_authoritative_include_graph(self) -> None:
        declared = set(m5.SOURCE_BINDING_PATHS)
        reachable = set(m5._discover_translation_unit_inputs(ROOT))
        required_authoritative = {
            "content/campaign.json",
            "content/encounters/boot/B01.json",
            "content/encounters/boot/B03.json",
            "src/game/engine.asm",
            "src/game/engine_core.asm",
            "src/game/engine_layout.asm",
            "src/game/object_pool.asm",
            "src/game/projection.asm",
            "src/gameplay/actor_sim.asm",
            "src/gameplay/campaign_collision.asm",
            "src/gameplay/campaign_projection.asm",
            "src/gameplay/campaign_runtime.asm",
            "src/gameplay/gameplay_core.asm",
            "src/gameplay/gameplay_layout.asm",
            "src/gameplay/replay.asm",
            "src/gameplay/vocabulary_combat.asm",
            "src/gameplay/vocabulary_core.asm",
            "src/gameplay/vocabulary_interactions.asm",
            "src/gameplay/vocabulary_layout.asm",
            "src/hardware/controllers.asm",
            "src/hardware/interrupts.asm",
            "src/ui/timeline_control.asm",
        }
        content_manifests = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "content").rglob("*.json")
        }
        self.assertEqual(reachable - declared, set())
        self.assertEqual(required_authoritative - declared, set())
        self.assertEqual(content_manifests - declared, set())
        self.assertEqual(
            len(m5.SOURCE_BINDING_PATHS),
            len(declared),
            "source binding paths must be unique",
        )

        without_actor_sim = tuple(
            path
            for path in m5.SOURCE_BINDING_PATHS
            if path != "src/gameplay/actor_sim.asm"
        )
        with mock.patch.object(
            m5,
            "SOURCE_BINDING_PATHS",
            without_actor_sim,
        ):
            with self.assertRaisesRegex(
                EvidenceFailure,
                "omits translation-unit inputs.*actor_sim",
            ):
                m5._source_bindings(ROOT)


if __name__ == "__main__":
    unittest.main()
