"""Deterministic regression generation for the historical M4 model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import (
    RUNTIME_SOURCE_SHA256,
    GameplayContract,
    load_gameplay_contract,
    sha256_file,
)
from .model import (
    GameplayModel,
    canonical_b01_first_attempt,
    crc32_iso_hdlc,
    run_b01_solution,
)


REPORT_SCHEMA = "r94-m4-historical-reference-regression-v2"
FROZEN_REPORT_SCHEMA = "r94-m4-reference-report-v1"
GOLDEN_B01_TERMINAL_CRC = 0x8762E745
GOLDEN_MUTATED_TERMINAL_CRC = 0xDFB092CD


class ReferenceFailure(RuntimeError):
    """A deterministic M4 reference acceptance condition failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceFailure(message)


def _hex32(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"


def _hash_reference_sources(root: Path) -> dict[str, str]:
    paths = sorted((root / "tools/m4_reference").glob("*.py"))
    paths.extend(sorted((root / "tests/m4").glob("*.py")))
    if not paths:
        raise ReferenceFailure("M4 reference source set is empty")
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in paths
    }


def _input_and_partial_evidence(contract: GameplayContract) -> dict[str, Any]:
    model = GameplayModel(contract)
    _require(model.step(0x8F) == 0, "sanitation fixture step")
    stored = model.replays.bank(6)[0]
    _require(stored == 0, "opposite axes/bit seven were not neutralized")
    _require(model.timeline_attempt_length == 1, "one-byte partial length")
    _require(model.descriptors[6].length == 1, "descriptor partial length")

    model = GameplayModel(contract)
    first_attempt = canonical_b01_first_attempt(contract.constants)
    for value in first_attempt:
        _require(model.step(value) == 0, "partial recording step")
    _require(model.timeline_attempt_length == 40, "40-byte partial length")
    _require(model.commit_and_reset(contract.constants["GAMEPLAY_COMMIT_MANUAL"]) == 0,
             "partial commit")
    descriptor = model.descriptors[6]
    expected_crc = crc32_iso_hdlc(bytes(first_attempt))
    _require(descriptor.length == 40, "committed partial descriptor length")
    _require(descriptor.crc == expected_crc, "committed partial CRC")
    _require(model.replays.canaries_intact(), "replay canaries after partial commit")
    return {
        "raw_input_hex": "8F",
        "sanitized_stored_hex": f"{stored:02X}",
        "reserved_bit_zero": True,
        "opposite_axes_neutral": True,
        "partial_length": descriptor.length,
        "partial_crc32_iso_hdlc": _hex32(descriptor.crc),
        "crc_covers_padding": False,
        "bank_canaries_intact": True,
    }


def _b01_evidence(contract: GameplayContract) -> dict[str, Any]:
    first = GameplayModel(contract)
    second = GameplayModel(contract)
    _require(run_b01_solution(first) == 1, "first B01 solution")
    _require(run_b01_solution(second) == 1, "second B01 solution")
    _require(first.projection == second.projection, "repeated terminal projection")
    _require(first.state_crc == second.state_crc, "repeated terminal signature")
    _require(first.state_crc == GOLDEN_B01_TERMINAL_CRC, "B01 golden signature")
    _require(first.tick == 166, "B01 terminal tick")
    _require(first.echo_order == [6] and first.current_bank == 0,
             "B01 replay roles")
    _require(first.actors[0].x == 80 << 8, "echo switch position")
    current = first.actors[contract.constants["GAMEPLAY_CURRENT_ACTOR_SLOT"]]
    _require(280 << 8 <= current.x < 296 << 8, "current terminal position")
    device = first.pools[contract.constants["POOL_TYPE_DEVICE"]].slots[0]
    _require(device.state & 7 == 7, "B01 projected device state")
    _require(device.param & 1, "echo occupancy mask")
    _require(not device.param & 0x40, "current is not switch occupant")

    mutated_inputs = list(canonical_b01_first_attempt(contract.constants))
    mutated_inputs[35] = contract.constants["INPUT_A"]
    mutated = GameplayModel(contract)
    _require(
        run_b01_solution(mutated, first_attempt=mutated_inputs) == 1,
        "mutated B01 solution",
    )
    _require(mutated.state_crc == GOLDEN_MUTATED_TERMINAL_CRC,
             "mutated B01 golden signature")
    _require(mutated.state_crc != first.state_crc, "mutated B01 did not diverge")
    return {
        "first_attempt": {
            "frames": 40,
            "script": "Right x32, neutral x8",
            "committed_bank": 6,
            "partial": True,
        },
        "second_attempt": {
            "right_frames_before_commit": 165,
            "terminal_input": "Right+B edge",
            "terminal_tick": first.tick,
        },
        "echo_switch_x_16_8": first.actors[0].x,
        "current_terminal_x_16_8": current.x,
        "device_state": device.state,
        "device_occupancy_mask": device.param,
        "terminal_reason_hex": f"{first.terminal_reason:04X}",
        "projection_bytes": len(first.projection),
        "terminal_crc32_iso_hdlc": _hex32(first.state_crc),
        "golden_terminal_crc32_iso_hdlc": _hex32(GOLDEN_B01_TERMINAL_CRC),
        "repeat_projection_equal": True,
        "repeat_signature_equal": True,
        "mutated_tick": 35,
        "mutated_input": "A",
        "mutated_terminal_crc32_iso_hdlc": _hex32(mutated.state_crc),
        "mutated_diverged": True,
    }


def _reset_evidence(contract: GameplayContract) -> dict[str, Any]:
    model = GameplayModel(contract)
    _require(model.step(contract.constants["INPUT_A"]) == 0, "reset A step")
    _require(model.pools[contract.constants["POOL_TYPE_PROJECTILE"]].count == 1,
             "projectile mutation")
    replay_before = bytes(model.replays.bank(6))
    _require(model.commit_and_reset(contract.constants["GAMEPLAY_COMMIT_MANUAL"]) == 0,
             "reset commit")
    _require(bytes(model.replays.bank(6)) == replay_before, "replay not preserved")
    _require(model.echo_order == [6] and model.current_bank == 0,
             "bank order not preserved")
    projectile_pool = model.pools[contract.constants["POOL_TYPE_PROJECTILE"]]
    device = model.pools[contract.constants["POOL_TYPE_DEVICE"]].slots[0]
    _require(projectile_pool.count == 0, "projectile pool not reset")
    _require(device.state == 0 and model.door_state == 0, "door not reset closed")
    _require(model.attempt == 1 and model.tick == 0, "reset attempt/tick")
    return {
        "real_reset_semantics_modeled": True,
        "attempt_after_reset": model.attempt,
        "tick_after_reset": model.tick,
        "replay_bytes_preserved": True,
        "echo_order_preserved": list(model.echo_order),
        "new_current_bank": model.current_bank,
        "projectile_pool_empty": True,
        "door_closed": True,
        "actors_at_canonical_spawn": all(
            actor.x == 32 << 8 and actor.y == 112 << 8
            for actor in (model.actors[0], model.actors[6])
        ),
    }


def _replacement_evidence(contract: GameplayContract) -> dict[str, Any]:
    model = GameplayModel(contract)
    for ordinal in range(6):
        _require(model.step(contract.constants["INPUT_A"] if ordinal & 1 else 0) == 0,
                 "capacity recording")
        _require(model.commit_and_reset(contract.constants["GAMEPLAY_COMMIT_MANUAL"]) == 0,
                 "capacity commit")
    _require(model.echo_order == [6, 0, 1, 2, 3, 4], "capacity order")
    _require(model.current_bank == 5, "capacity current bank")
    _require(model.timeline_flags & 2, "replacement not immediately visible")
    _require(model.step(0) == 0, "full-capacity recording")
    order_before = tuple(model.echo_order)
    banks_before = model.replays.banks_bytes()
    descriptors_before = tuple(
        (item.length, item.crc, item.flags, item.generation)
        for item in model.descriptors
    )
    attempt_before = model.attempt
    _require(
        model.commit_and_reset(contract.constants["GAMEPLAY_COMMIT_MANUAL"])
        == contract.constants["GAMEPLAY_RESULT_REPLACEMENT_REQUIRED"],
        "unselected full-capacity commit did not block",
    )
    _require(tuple(model.echo_order) == order_before, "blocked order changed")
    _require(model.replays.banks_bytes() == banks_before, "blocked banks changed")
    _require(
        tuple(
            (item.length, item.crc, item.flags, item.generation)
            for item in model.descriptors
        )
        == descriptors_before,
        "blocked descriptors changed",
    )
    _require(model.attempt == attempt_before, "blocked commit reset attempt")

    _require(model.set_echo_protected(0, True) == 0, "protect oldest")
    _require(model.select_oldest_replacement() == 0, "oldest selection")
    _require(model.selected_ordinal == 1, "oldest did not skip protected")
    _require(
        model.select_replacement(0)
        == contract.constants["GAMEPLAY_RESULT_PROTECTED"],
        "protected explicit selection accepted",
    )
    _require(model.set_echo_protected(0, False) == 0, "unprotect")
    _require(model.select_replacement(2) == 0, "explicit selection")
    _require(model.commit_and_reset(contract.constants["GAMEPLAY_COMMIT_MANUAL"]) == 0,
             "selected replacement commit")
    _require(model.echo_order == [6, 0, 2, 3, 4, 5], "replacement order")
    _require(model.current_bank == 1, "removed bank not reused")
    return {
        "capacity": 6,
        "order_at_capacity": list(order_before),
        "current_at_capacity": 5,
        "replacement_visible": True,
        "unselected_commit_result": "REPLACEMENT_REQUIRED",
        "blocked_banks_unchanged": True,
        "blocked_descriptors_unchanged": True,
        "blocked_attempt_unchanged": True,
        "protected_oldest_skipped_to_ordinal": 1,
        "protected_explicit_rejected": True,
        "explicit_replaced_ordinal": 2,
        "order_after_replacement": list(model.echo_order),
        "reused_current_bank": model.current_bank,
        "no_silent_discard": True,
    }


def _timeout_evidence(contract: GameplayContract) -> dict[str, Any]:
    model = GameplayModel(contract)
    result = 0
    for _ in range(contract.constants["ENGINE_ATTEMPT_TICKS"]):
        result = model.step(0x80)
    _require(result == contract.constants["GAMEPLAY_RESULT_TIMEOUT"], "timeout result")
    _require(model.tick == 720 and model.timeline_attempt_length == 720,
             "exact timeout length")
    bank = model.replays.bank(model.current_bank)
    _require(len(bank) == 720 and all(value & 0x80 == 0 for value in bank),
             "recorded reserved bit")
    region_before = model.replay_region_bytes()
    _require(model.step(0) == contract.constants["GAMEPLAY_RESULT_TIMEOUT"],
             "post-timeout result")
    _require(model.replay_region_bytes() == region_before, "post-timeout overrun")
    _require(model.replays.canaries_intact(), "timeout canaries")
    return {
        "processed_ticks": model.tick,
        "recorded_bytes": model.timeline_attempt_length,
        "terminal_result": "TIMEOUT",
        "recording_full_visible": bool(model.timeline_flags & 0x80),
        "post_terminal_write": False,
        "reserved_bits_zero": True,
        "bank_canaries_intact": True,
    }


def compute_report_binding(report_without_binding: dict[str, Any]) -> str:
    canonical = json.dumps(
        report_without_binding,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def build_reference_report(root: Path | str) -> dict[str, Any]:
    root_path = Path(root).resolve()
    # Do not rebind the frozen M4 behavioral model to the M5 implementation.
    # Current sources provide only declarations and explicitly parsed retained
    # ordering invariants for this historical regression.
    contract = load_gameplay_contract(root_path, verify_source_hashes=False)
    c = contract.constants
    _require(crc32_iso_hdlc(b"") == 0, "empty CRC")
    _require(crc32_iso_hdlc(b"123456789") == 0xCBF43926, "CRC known vector")
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "status": "pass",
        "deterministic_generation": True,
        "classification": "historical_model_regression",
        "current_runtime_equivalence_claimed": False,
        "runtime_sources": dict(sorted(contract.source_sha256.items())),
        "frozen_runtime_sources": dict(sorted(RUNTIME_SOURCE_SHA256.items())),
        "exact_frozen_source_match": (
            dict(sorted(contract.source_sha256.items()))
            == dict(sorted(RUNTIME_SOURCE_SHA256.items()))
        ),
        "reference_sources": _hash_reference_sources(root_path),
        "contract": {
            "bank_base_hex": f"{c['REPLAY_BANK_BASE']:06X}",
            "bank_count": c["REPLAY_BANK_COUNT"],
            "bank_bytes": c["REPLAY_BANK_BYTES"],
            "bank_region_end_hex": f"{c['REPLAY_BANK_REGION_END']:06X}",
            "descriptor_base_hex": f"{c['REPLAY_DESCRIPTOR_BASE']:06X}",
            "descriptor_count": c["REPLAY_BANK_COUNT"],
            "descriptor_bytes": c["REPLAY_DESCRIPTOR_BYTES"],
            "descriptor_region_end_hex": f"{c['REPLAY_DESCRIPTOR_REGION_END']:06X}",
            "timeline_base_hex": f"{c['TIMELINE_CONTROL_BASE']:06X}",
            "timeline_bytes": c["TIMELINE_CONTROL_BYTES"],
            "timeline_end_hex": f"{c['TIMELINE_CONTROL_END']:06X}",
            "descriptor_control_remainder_bytes": (
                c["TIMELINE_CONTROL_END"] - c["REPLAY_DESCRIPTOR_BASE"]
            ),
            "total_replay_timeline_bytes": (
                c["TIMELINE_CONTROL_END"] - c["REPLAY_BANK_BASE"]
            ),
            "gameplay_control_base_hex": f"{c['GAMEPLAY_CONTROL_BASE']:06X}",
            "gameplay_control_declared_bytes": c["GAMEPLAY_CONTROL_BYTES_VALUE"],
            "actor_slots": c["ACTOR_CAPACITY"],
            "echo_limit": c["GAMEPLAY_ECHO_LIMIT"],
            "current_actor_slot": c["GAMEPLAY_CURRENT_ACTOR_SLOT"],
            "attempt_ticks": c["ENGINE_ATTEMPT_TICKS"],
            "projection_bytes": c["ENGINE_PROJECTION_SCHEMA_BYTES"],
            "b01_switch": [
                c["B01_SWITCH_CENTER_X"],
                c["B01_SWITCH_CENTER_Y"],
            ],
            "b01_door_rect": [
                contract.fixture.rects[0].x,
                contract.fixture.rects[0].y,
                contract.fixture.rects[0].width,
                contract.fixture.rects[0].height,
            ],
            "b01_terminal": [
                c["B01_TERMINAL_CENTER_X"],
                c["B01_TERMINAL_CENTER_Y"],
            ],
        },
        "crc32_iso_hdlc": {
            "empty": "00000000",
            "123456789": "CBF43926",
            "polynomial_reflected": "EDB88320",
        },
        "input_recording": _input_and_partial_evidence(contract),
        "reset": _reset_evidence(contract),
        "b01": _b01_evidence(contract),
        "replacement": _replacement_evidence(contract),
        "timeout": _timeout_evidence(contract),
        "adversarial_coverage": [
            "reserved input bit and opposing axes",
            "partial replay CRC excludes zero padding",
            "post-terminal bank overrun",
            "solo/current-only terminal activation",
            "echo B cannot commit for current",
            "held B without a current edge",
            "full-capacity unselected commit",
            "protected replacement target",
            "oldest selection skips protection",
            "mutated replay signature divergence",
            "source hash, numeric, fixture, and ordered-behavior drift",
        ],
        "limitations": [
            (
                "This is an independent Python behavioral model; it does not "
                "execute Motorola 68000 instructions or prove cycle timing."
            ),
            (
                "It verifies the M4 authoritative replay/echo/B01 slice and "
                "frozen M3 state projection, not renderer, HUD, reset visuals, "
                "controller integration, audio, or music."
            ),
            (
                "No human-play session, emulator run, canonical .r94r file, "
                "visual capture, or audio capture is claimed by this report."
            ),
            (
                "The exact M4 source-hash manifest remains frozen separately. "
                "Current declarations and retained ordering markers feed this "
                "historical model only; this report does not bind the current "
                "M5 runtime or ROM."
            ),
        ],
    }
    report["binding_sha256"] = compute_report_binding(report)
    return report


def write_reference_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
