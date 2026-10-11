"""Build deterministic regression evidence for the historical M3 model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import (
    RUNTIME_SOURCE_SHA256,
    EngineContract,
    load_engine_contract,
    sha256_file,
)
from .model import (
    EngineModel,
    FixedPool,
    canonical_script_input,
    crc32_iso_hdlc,
    run_script,
)


REPORT_SCHEMA = "r94-m3-historical-reference-regression-v2"
FROZEN_REPORT_SCHEMA = "r94-m3-reference-report-v1"
GOLDEN_TERMINAL_CRC = 0xF92B572A


class ReferenceFailure(RuntimeError):
    """A deterministic reference acceptance condition failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceFailure(message)


def _hex32(value: int) -> str:
    return f"{value & 0xFFFFFFFF:08X}"


def _hash_reference_sources(root: Path) -> dict[str, str]:
    paths = sorted((root / "tools/m3_reference").glob("*.py"))
    paths.extend(sorted((root / "tests/m3").glob("*.py")))
    if not paths:
        raise ReferenceFailure("reference source set is empty")
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in paths
    }


def _pool_evidence(contract: EngineContract) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for pool_type, capacity in contract.pool_capacities.items():
        pool = FixedPool(
            pool_type,
            capacity,
            contract.pool_overflow_masks[pool_type],
        )
        allocated = [pool.allocate_lowest() for _ in range(capacity)]
        indices = [item[0] for item in allocated if item is not None]
        _require(indices == list(range(capacity)), f"pool {pool_type} order")
        _require(pool.allocate_lowest() is None, f"pool {pool_type} overflow")
        _require(pool.canaries_intact(), f"pool {pool_type} overflow canaries")
        pool.free(0)
        reused = pool.allocate_lowest()
        _require(reused is not None and reused[0] == 0, f"pool {pool_type} reuse")
        _require(pool.canaries_intact(), f"pool {pool_type} reuse canaries")
        evidence[str(pool_type)] = {
            "capacity": capacity,
            "allocation_indices": indices,
            "overflow_reported": True,
            "lowest_free_reused": reused[0],
            "canaries_intact": True,
        }
    return evidence


def _collision_evidence(contract: EngineContract) -> dict[str, Any]:
    model = EngineModel(contract)
    _require(model.load_encounter(0) == 0, "collision fixture load")
    actor = model.actors[0]
    actor.x = (106 << 8) + 0x40
    actor.y = 112 << 8
    model.step(contract.constants["INPUT_RIGHT"])
    first_x = actor.x
    _require(first_x == (107 << 8) + 0xC0, "fractional movement retained")
    _require(
        not actor.flags & contract.constants["ACTOR_FLAG_COLLISION_X"],
        "premature fractional collision",
    )
    model.step(contract.constants["INPUT_RIGHT"])
    second_x = actor.x
    _require(second_x == 108 << 8, "wall collision edge")
    _require(
        bool(actor.flags & contract.constants["ACTOR_FLAG_COLLISION_X"]),
        "collision flag missing",
    )

    corner = EngineModel(contract)
    _require(corner.load_encounter(0) == 0, "axis-order fixture load")
    corner_actor = corner.actors[0]
    corner_actor.x = 107 << 8
    corner_actor.y = 43 << 8
    corner.step(
        contract.constants["INPUT_RIGHT"] | contract.constants["INPUT_DOWN"]
    )
    _require(
        corner_actor.x == (108 << 8) + 0x10,
        "X-first corner retained wrong fraction",
    )
    _require(corner_actor.y == 44 << 8, "Y-second corner resolution")
    _require(
        not corner_actor.flags & contract.constants["ACTOR_FLAG_COLLISION_X"]
        and corner_actor.flags & contract.constants["ACTOR_FLAG_COLLISION_Y"],
        "axis-order collision flags",
    )
    return {
        "start_x_16_8": (106 << 8) + 0x40,
        "fractional_x_16_8": first_x,
        "resolved_x_16_8": second_x,
        "resolved_x_pixels": second_x >> 8,
        "x_then_y_corner": {
            "x_16_8": corner_actor.x,
            "y_16_8": corner_actor.y,
            "x_collision": False,
            "y_collision": True,
        },
    }


def _reset_evidence(contract: EngineContract) -> dict[str, Any]:
    model = EngineModel(contract)
    _require(model.load_encounter(0) == 0, "reset fixture load")
    canonical = model.ordinary_state_bytes()
    for tick in range(100):
        model.step(canonical_script_input(tick, dict(contract.constants)))
    mutated = model.ordinary_state_bytes()
    _require(mutated != canonical, "reset fixture did not mutate")
    _require(model.reset() == 0, "reset failed")
    restored = model.ordinary_state_bytes()
    _require(restored == canonical, "ordinary RAM did not restore byte-for-byte")
    return {
        "ordinary_bytes": len(canonical),
        "mutated_before_reset": True,
        "byte_equal_after_reset": True,
        "attempt_after_reset": model.attempt,
        "tick_after_reset": model.tick,
    }


def _script_evidence(contract: EngineContract) -> dict[str, Any]:
    constants = dict(contract.constants)
    inputs = [
        canonical_script_input(tick, constants)
        for tick in range(constants["ENGINE_ATTEMPT_TICKS"])
    ]
    first = EngineModel(contract)
    second = EngineModel(contract)
    first_checkpoints = run_script(first, inputs)
    second_checkpoints = run_script(second, inputs)
    expected_ticks = tuple(range(0, 721, 60))
    _require(
        tuple(tick for tick, _ in first_checkpoints) == expected_ticks,
        "checkpoint tick schedule",
    )
    _require(first_checkpoints == second_checkpoints, "repeated script mismatch")
    _require(first.tick == 720 and first.state == constants["ENGINE_STATE_TERMINAL"],
             "tick 720 terminal state")
    _require(first.state_crc == GOLDEN_TERMINAL_CRC, "golden terminal CRC")
    _require(len(first.projection) == constants["ENGINE_PROJECTION_SCHEMA_BYTES"],
             "terminal projection size")

    mutated_inputs = list(inputs)
    mutated_inputs[718] ^= constants["INPUT_RIGHT"]
    mutated = EngineModel(contract)
    mutated_checkpoints = run_script(mutated, mutated_inputs)
    _require(mutated_checkpoints != first_checkpoints, "mutated script did not diverge")
    _require(mutated.state_crc != first.state_crc, "mutated terminal CRC did not diverge")

    return {
        "algorithm": (
            "tick%120 quarters: Right,Down,Left,Up; "
            "A added when tick%67==0"
        ),
        "frames": len(inputs),
        "checkpoint_interval": constants["ENGINE_CHECKPOINT_INTERVAL"],
        "checkpoints": [
            {"tick": tick, "crc32_iso_hdlc": _hex32(crc)}
            for tick, crc in first_checkpoints
        ],
        "repeat_equal": True,
        "terminal_crc32_iso_hdlc": _hex32(first.state_crc),
        "golden_terminal_crc32_iso_hdlc": _hex32(GOLDEN_TERMINAL_CRC),
        "mutated_tick": 718,
        "mutated_terminal_crc32_iso_hdlc": _hex32(mutated.state_crc),
        "mutated_diverged": True,
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
    # M3's exact implementation hashes intentionally remain frozen.  The
    # current tree is parsed only for retained ABI constants and encounter
    # fixtures so the independent historical model remains useful without
    # falsely rebinding it to later runtime behavior.
    contract = load_engine_contract(root_path, verify_source_hashes=False)
    constants = contract.constants
    known_crc = crc32_iso_hdlc(b"123456789")
    empty_crc = crc32_iso_hdlc(b"")
    _require(known_crc == 0xCBF43926, "CRC known vector")
    _require(empty_crc == 0, "CRC empty vector")

    model = EngineModel(contract)
    _require(model.load_encounter(0) == 0, "projection fixture load")
    projection = model.projection
    _require(len(projection) == 3638, "projection byte length")
    _require(projection[0:4] == b"R94P", "projection magic endian")
    _require(projection[4:8] == b"\x00\x01\x0E\x36", "projection header endian")
    _require(
        int.from_bytes(projection[44:48], "big") == 32 << 8,
        "actor X projection endian",
    )
    _require(crc32_iso_hdlc(projection) == model.state_crc,
             "projection CRC publication")

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
            "attempt_ticks": constants["ENGINE_ATTEMPT_TICKS"],
            "checkpoint_interval": constants["ENGINE_CHECKPOINT_INTERVAL"],
            "projection_bytes": constants["ENGINE_PROJECTION_SCHEMA_BYTES"],
            "actor_capacity": constants["ACTOR_CAPACITY"],
            "pool_capacities": {
                str(key): value
                for key, value in contract.pool_capacities.items()
            },
            "encounter_ids": list(contract.encounter_order),
        },
        "crc32_iso_hdlc": {
            "empty": _hex32(empty_crc),
            "123456789": _hex32(known_crc),
            "polynomial_reflected": "EDB88320",
        },
        "projection": {
            "bytes": len(projection),
            "prefix_hex": projection[:48].hex().upper(),
            "frame_zero_crc32_iso_hdlc": _hex32(model.state_crc),
            "big_endian": True,
            "explicit_schema": True,
        },
        "collision": _collision_evidence(contract),
        "pools": _pool_evidence(contract),
        "reset": _reset_evidence(contract),
        "canonical_script": _script_evidence(contract),
        "limitations": [
            (
                "This is an independent behavioral model; it does not execute "
                "68000 opcodes or prove instruction/cycle timing."
            ),
            (
                "It covers the M3 authoritative core only, excluding interrupts, "
                "renderer, audio, debug ABI publication, and later echo/gameplay systems."
            ),
            (
                "The exact M3 source-hash manifest remains frozen separately. "
                "This generated report parses current declarations only to "
                "exercise the historical model and does not bind the current "
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
