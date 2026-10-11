#!/usr/bin/env python3
"""Validate and compile the fixed RECURSION 94 campaign.

The compiler intentionally uses only the Python standard library.  Validation
is fail-closed: object keys, the campaign roster, type registries, anchor
records, parameter shapes, budgets, references, and graph structure are all
checked before a byte is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


FORMAT_VERSION = 1
FILE_MAGIC = b"R94C"
CHUNK_MAGIC = b"ENCT"
FILE_HEADER_SIZE = 32
DIRECTORY_ENTRY_SIZE = 32
CHUNK_HEADER_SIZE = 144
ENTITY_RECORD_SIZE = 32
OBJECTIVE_RECORD_SIZE = 16
ROLE_RECORD_SIZE = 16
ACTION_RECORD_SIZE = 8
PHASE_RECORD_SIZE = 20
ATTACK_RECORD_SIZE = 12
POLICY_RECORD_SIZE = 8

OUTPUT_BINARY = "campaign_content.bin"
OUTPUT_ASSEMBLY = "campaign_content.inc.asm"
OUTPUT_REPORT = "campaign_content_report.json"
OUTPUT_CHECKSUMS = "campaign_content.sha256"
OUTPUT_NAMES = frozenset(
    (OUTPUT_BINARY, OUTPUT_ASSEMBLY, OUTPUT_REPORT, OUTPUT_CHECKSUMS)
)

FILE_FLAG_COLLISION_ONE_IS_SOLID = 1
CHUNK_FLAG_BOSS = 1
CHUNK_FLAG_HAS_ANCHORS = 2
CHUNK_FLAG_HAS_RESPONSE_POLICY = 4

KIND_CODES = {"primary": 0, "boss": 1}
SECTOR_CODES = {"BOOT": 1, "VIDEO": 2, "SOUND": 3, "MEMORY": 4, "INTERRUPT": 5}
ENTITY_CATEGORY_CODES = {
    "devices": 1,
    "enemies": 2,
    "hazards": 3,
    "carryables": 4,
    "boss_components": 5,
}
ENTITY_CATEGORIES = tuple(ENTITY_CATEGORY_CODES)
ANCHOR_CODES = {
    "socket_powered": 1,
    "terminal_repaired": 2,
    "conveyor_direction": 3,
    "lock_destroyed": 4,
    "bridge_state": 5,
    "route_open": 6,
}

PARAM_UINT = 1
PARAM_FALSE = 2
PARAM_TRUE = 3
PARAM_STRING = 4
PARAM_LIST = 5

ATTACK_FRAMES = 1
ATTACK_PERIOD = 2
ATTACK_SOURCE = 3

BUDGET_FIELDS = (
    "cpu_percent",
    "dma_words",
    "dma_queue_entries",
    "hardware_sprites",
    "sprites_per_scanline",
    "actors",
    "enemies",
    "projectiles",
    "devices",
    "carryables",
    "hazards",
    "boss_components",
    "effects",
)
BUDGET_CAPS = {
    "cpu_percent": 65,
    "dma_words": 2048,
    "dma_queue_entries": 32,
    "hardware_sprites": 64,
    "sprites_per_scanline": 16,
    "actors": 7,
    "enemies": 12,
    "projectiles": 24,
    "devices": 24,
    "carryables": 8,
    "hazards": 16,
    "boss_components": 12,
    "effects": 24,
}


@dataclass(frozen=True)
class RosterEntry:
    order: int
    encounter_id: str
    numeric_id: int
    name: str
    kind: str
    sector: str
    relative_file: str


ROSTER = (
    RosterEntry(1, "B01", 101, "POWER-ON SELF", "primary", "BOOT", "encounters/boot/B01.json"),
    RosterEntry(2, "B02", 102, "DEAD LETTER", "primary", "BOOT", "encounters/boot/B02.json"),
    RosterEntry(3, "B03", 103, "TWO-PHASE COMMIT", "primary", "BOOT", "encounters/boot/B03.json"),
    RosterEntry(4, "B04", 104, "THE WATCHDOG", "boss", "BOOT", "encounters/boot/B04.json"),
    RosterEntry(5, "V01", 201, "BUS ARBITRATION", "primary", "VIDEO", "encounters/video/V01.json"),
    RosterEntry(6, "V02", 202, "RASTER RELAY", "primary", "VIDEO", "encounters/video/V02.json"),
    RosterEntry(7, "V03", 203, "DOUBLE BUFFER", "primary", "VIDEO", "encounters/video/V03.json"),
    RosterEntry(8, "V04", 204, "THE SCHEDULER", "boss", "VIDEO", "encounters/video/V04.json"),
    RosterEntry(9, "S01", 301, "NOISE FLOOR", "primary", "SOUND", "encounters/sound/S01.json"),
    RosterEntry(10, "S02", 302, "PHASE LOCK", "primary", "SOUND", "encounters/sound/S02.json"),
    RosterEntry(11, "S03", 303, "COUNTERPOINT", "primary", "SOUND", "encounters/sound/S03.json"),
    RosterEntry(12, "S04", 304, "THE GARBAGE COLLECTOR", "boss", "SOUND", "encounters/sound/S04.json"),
    RosterEntry(13, "M01", 401, "WRITE ENABLE", "primary", "MEMORY", "encounters/memory/M01.json"),
    RosterEntry(14, "M02", 402, "BAD SECTOR", "primary", "MEMORY", "encounters/memory/M02.json"),
    RosterEntry(15, "M03", 403, "COMPACTION", "primary", "MEMORY", "encounters/memory/M03.json"),
    RosterEntry(16, "M04", 404, "THE FORK BOMB", "boss", "MEMORY", "encounters/memory/M04.json"),
    RosterEntry(17, "I01", 501, "PRIORITY INVERSION", "primary", "INTERRUPT", "encounters/interrupt/I01.json"),
    RosterEntry(18, "I02", 502, "NON-MASKABLE", "primary", "INTERRUPT", "encounters/interrupt/I02.json"),
    RosterEntry(19, "I03", 503, "VECTOR TABLE", "primary", "INTERRUPT", "encounters/interrupt/I03.json"),
    RosterEntry(20, "I04", 504, "ORACLE", "boss", "INTERRUPT", "encounters/interrupt/I04.json"),
)
ROSTER_BY_ID = {entry.encounter_id: entry for entry in ROSTER}
ROSTER_IDS = tuple(entry.encounter_id for entry in ROSTER)

SECTOR_SPECS = (
    ("BOOT", 80, 2, ("B01", "B02", "B03", "B04"), "B04"),
    ("VIDEO", 100, 3, ("V01", "V02", "V03", "V04"), "V04"),
    ("SOUND", 120, 4, ("S01", "S02", "S03", "S04"), "S04"),
    ("MEMORY", 140, 5, ("M01", "M02", "M03", "M04"), "M04"),
    ("INTERRUPT", 160, 6, ("I01", "I02", "I03", "I04"), "I04"),
)
SECTOR_SPEC_BY_ID = {
    sector_id: {
        "order": index,
        "tempo_bpm": tempo,
        "echo_cap": echo_cap,
        "encounters": encounters,
        "boss": boss,
    }
    for index, (sector_id, tempo, echo_cap, encounters, boss) in enumerate(
        SECTOR_SPECS, 1
    )
}

DEVICE_TYPES = frozenset(
    """
    attack_reflector beam_emitter beam_reflector blackout_gate
    boss_commit_terminal boss_core_lock boss_shutdown_port boss_upload_port
    breakable_lock breakable_security_node bridge_switch burst_reflector
    conveyor_direction_switch door echo_buffer_terminal exit_receiver
    exit_terminal final_seed_transmitter hack_terminal interrupt_port
    local_shield_emitter moving_platform overlap_door payload_pad
    phase_hold_terminal phase_stabilizer phase_terminal power_socket
    powered_door pressure_switch pulse_door redirectable_prism
    repair_terminal route_terminal scheduled_channel_terminal seal_terminal
    seed_handoff_pad seed_socket shield_emitter timed_door timed_switch
    transport_pad_control uplink_power_terminal visibility_beacon
    """.split()
)
ENEMY_TYPES = frozenset("mirror pursuer scrubber sentry warden".split())
HAZARD_TYPES = frozenset(
    """
    closing_door_crush_zone conveyor dense_fire_lane energy_bridge
    guard_barrier hostile_replay_collision_zone moving_hazard noise_amplifier
    non_maskable_raster_burst oracle_projectile_field phase_rejection_field
    redirected_laser reflected_laser replay_disable_zone
    replay_interference_zone rotating_observation_cone scheduled_echo_disable
    scheduled_raster_hazard scheduled_scanline_field sentry_fire_lane
    turret_fire_lane
    """.split()
)
CARRYABLE_TYPES = frozenset("power_cell seed_payload".split())
BOSS_COMPONENT_TYPES = frozenset(
    """
    boss_channel_arbiter boss_checksum_ring boss_clock boss_core boss_lock
    boss_policy_display boss_scrubber boss_seal_ring boss_sensor
    boss_transformer boss_weapon
    """.split()
)
ENTITY_TYPES_BY_CATEGORY = {
    "devices": DEVICE_TYPES,
    "enemies": ENEMY_TYPES,
    "hazards": HAZARD_TYPES,
    "carryables": CARRYABLE_TYPES,
    "boss_components": BOSS_COMPONENT_TYPES,
}
ALL_ENTITY_TYPES = frozenset().union(*ENTITY_TYPES_BY_CATEGORY.values())

PARAM_BOOL_KEYS = frozenset(
    """
    active_while_held alternative_anchor_target creates_occluder_gap damageable
    drop_on_damage feedback_only must_remain_pressed non_damage_objective
    protects_progress_not_actor transform_is_displayed unique
    """.split()
)
PARAM_INT_KEYS = frozenset(
    """
    active_after_frame attack_period_frames beam_charge_required_frames
    call_latency_frames capacity_actors capacity_carryables
    carry_speed_percent charge_frames chase_speed_px close_delay_frames
    contact_damage cycle_frames damage damage_period_frames
    display_before_apply_frames duration_frames extends_gate_frames
    fire_period_frames handoff_radius_px health hearing_radius_px height
    interact_frames latch_frames lock_frames max_segments
    movement_noise_radius_px must_complete_before_frame
    observed_actor_reset_delay_frames occupancy_frames open_frames
    opening_frames opens_after_frame overload_after_frames payload_capacity
    phase_cycle_frames power_delay_frames power_frames preview_frames
    priority_bonus projectile_cap projectile_speed_px range_px record_frames
    redirects_burst_frame repair_frames rotation_frames
    rotation_step_frames seal_frames segment_limit segment_px service_frames
    speed_px_per_frame stall_frames suppressed_frames suppression_hits
    telegraph_frames transmit_frames travel_frames unshielded_damage
    upload_frames visual_lead_frames warning_frames width write_frames
    zone_frames
    """.split()
)
PARAM_INT_OR_LIST_INT_KEYS = frozenset(("active_frames",))
PARAM_LIST_INT_KEYS = frozenset(
    """
    activation_frames active_modulo_120 burst_frames coverage_rect open_window
    open_window_modulo_120 path_y phase_window_modulo_90 scrub_frames
    window_modulo_120
    """.split()
)
PARAM_LIST_LIST_INT_KEYS = frozenset("active_windows visible_windows".split())
PARAM_LIST_STRING_KEYS = frozenset(
    """
    affected_slots controls opens_after patterns powers protects reads
    release_requires seal_sources
    """.split()
)
PARAM_STRING_OR_LIST_STRING_KEYS = frozenset(("requires",))
PARAM_STRING_KEYS = frozenset(
    """
    accepts authoritative_source covers damage_arc disable_duration effect
    exposed_by guard_target interaction miss_policy opens_from pattern
    phase_source power_source progress_source redirect_target redirects
    required_state safe_direction safe_state selection source
    source_selection stabilizes suppressed_by target_order target_policy
    transform unlock_source
    """.split()
)
ALL_PARAMETER_KEYS = frozenset().union(
    PARAM_BOOL_KEYS,
    PARAM_INT_KEYS,
    PARAM_INT_OR_LIST_INT_KEYS,
    PARAM_LIST_INT_KEYS,
    PARAM_LIST_LIST_INT_KEYS,
    PARAM_LIST_STRING_KEYS,
    PARAM_STRING_OR_LIST_STRING_KEYS,
    PARAM_STRING_KEYS,
)

NON_ENTITY_REFERENCE_IDS = frozenset(("B03_NODE_ARM",))
RUNTIME_REFERENCE_FIELDS = frozenset(("state", "timer", "param", "health"))
STRUCTURED_RUNTIME_ENCOUNTERS = frozenset(("B01", "B02", "B03"))

CAMPAIGN_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "campaign_id",
        "title",
        "start_encounter",
        "terminal_encounter",
        "declared_counts",
        "sectors",
        "encounters",
        "reachability",
        "invariants",
        "completion_messages",
    )
)
ENCOUNTER_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "id",
        "numeric_id",
        "name",
        "kind",
        "campaign_order",
        "sector",
        "room",
        "loop",
        "echoes",
        "presentation",
        "mechanics",
        "entities",
        "objective_graph",
        "canonical_role_schedule",
        "alternative_routes",
        "failure_signals",
        "boss_phases",
        "budgets",
        "replays",
        "progression",
        "narrative",
    )
)

STABLE_ID_RE = re.compile(r"^[BVSMI][0-9]{2}_[A-Z0-9_]+$")
REPLAY_ID_RE = re.compile(r"^[BVSMI][0-9]{2}_[A-Z0-9_]+$")


class ContentCompileError(RuntimeError):
    """Base error for content compilation failures."""


class ContentValidationError(ContentCompileError):
    """Raised when source content or compiled bytes violate the contract."""


@dataclass(frozen=True)
class ValidatedContent:
    content_root: Path
    campaign: dict[str, Any]
    encounters: tuple[dict[str, Any], ...]
    source_sha256: str


@dataclass(frozen=True)
class ChunkResult:
    encounter: dict[str, Any]
    data: bytes
    source_crc32: int
    source_sha256: str
    collision_bytes: int
    entity_count: int
    objective_count: int
    role_count: int
    phase_count: int
    policy_count: int


@dataclass(frozen=True)
class ArtifactBundle:
    files: Mapping[str, bytes]
    binary_sha256: str
    source_sha256: str
    encounter_count: int


def _error(path: str, message: str) -> None:
    raise ContentValidationError(f"{path}: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _expect_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(path, "expected object")
    return value


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _error(path, "expected array")
    return value


def _expect_string(
    value: Any, path: str, *, min_length: int = 1, max_bytes: int = 4096
) -> str:
    if not isinstance(value, str):
        _error(path, "expected string")
    if "\x00" in value:
        _error(path, "NUL is not permitted")
    encoded = value.encode("utf-8")
    if len(value) < min_length or len(encoded) > max_bytes:
        _error(path, f"string length must be {min_length}..{max_bytes} UTF-8 bytes")
    return value


def _expect_int(
    value: Any, path: str, minimum: int = 0, maximum: int = 0xFFFF
) -> int:
    if not _is_int(value):
        _error(path, "expected integer (Boolean is not an integer)")
    if value < minimum or value > maximum:
        _error(path, f"integer {value} outside {minimum}..{maximum}")
    return value


def _exact_keys(value: Any, expected: Iterable[str], path: str) -> dict[str, Any]:
    obj = _expect_dict(value, path)
    expected_set = frozenset(expected)
    actual = frozenset(obj)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        unknown = sorted(actual - expected_set)
        details = []
        if missing:
            details.append(f"missing keys {missing}")
        if unknown:
            details.append(f"unknown keys {unknown}")
        _error(path, "; ".join(details))
    return obj


def _unique_strings(
    value: Any,
    path: str,
    *,
    minimum_items: int = 0,
    maximum_items: int = 255,
) -> list[str]:
    items = _expect_list(value, path)
    if len(items) < minimum_items or len(items) > maximum_items:
        _error(path, f"array length must be {minimum_items}..{maximum_items}")
    result = [
        _expect_string(item, f"{path}[{index}]")
        for index, item in enumerate(items)
    ]
    if len(result) != len(set(result)):
        _error(path, "duplicate string")
    return result


def _strict_json_load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _error(str(path), "file does not exist")
    if path.stat().st_size > 1_048_576:
        _error(str(path), "JSON file exceeds 1 MiB source limit")

    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite number {token}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"duplicate object key {key!r}")
            obj[key] = value
        return obj

    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(
                stream,
                object_pairs_hook=reject_duplicates,
                parse_constant=reject_constant,
            )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _error(str(path), f"invalid strict JSON: {exc}")
    return _expect_dict(value, str(path))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _source_bundle_digest(
    campaign: dict[str, Any], encounters: Sequence[dict[str, Any]]
) -> str:
    digest = hashlib.sha256()
    digest.update(b"RECURSION94-CONTENT-SOURCE-V1\x00")
    entries = [("campaign.json", campaign)]
    entries.extend(
        (entry.relative_file, encounter)
        for entry, encounter in zip(ROSTER, encounters)
    )
    for relative, value in entries:
        name = relative.encode("utf-8")
        canonical = _canonical_json(value)
        digest.update(struct.pack(">H", len(name)))
        digest.update(name)
        digest.update(struct.pack(">I", len(canonical)))
        digest.update(canonical)
    return digest.hexdigest()


def _validate_campaign(campaign: dict[str, Any], path: str) -> None:
    _exact_keys(campaign, CAMPAIGN_KEYS, path)
    _expect_string(campaign["schema"], f"{path}.schema", max_bytes=64)
    if campaign["schema"] != "recursion94.campaign":
        _error(f"{path}.schema", "expected recursion94.campaign")
    _expect_int(campaign["schema_version"], f"{path}.schema_version", 1, 1)
    if campaign["schema_version"] != 1:
        _error(f"{path}.schema_version", "expected 1")
    _expect_string(campaign["campaign_id"], f"{path}.campaign_id", max_bytes=64)
    if campaign["campaign_id"] != "RECURSION94_MAIN":
        _error(f"{path}.campaign_id", "unexpected campaign ID")
    _expect_string(campaign["title"], f"{path}.title")
    _expect_string(
        campaign["start_encounter"], f"{path}.start_encounter", max_bytes=3
    )
    if campaign["start_encounter"] != "B01":
        _error(f"{path}.start_encounter", "expected B01")
    _expect_string(
        campaign["terminal_encounter"], f"{path}.terminal_encounter", max_bytes=3
    )
    if campaign["terminal_encounter"] != "I04":
        _error(f"{path}.terminal_encounter", "expected I04")

    counts = _exact_keys(
        campaign["declared_counts"],
        ("sectors", "primary_encounters", "boss_encounters", "total_encounters"),
        f"{path}.declared_counts",
    )
    expected_counts = {
        "sectors": 5,
        "primary_encounters": 15,
        "boss_encounters": 5,
        "total_encounters": 20,
    }
    for field, expected_value in expected_counts.items():
        _expect_int(
            counts[field],
            f"{path}.declared_counts.{field}",
            expected_value,
            expected_value,
        )
    if counts != expected_counts:
        _error(f"{path}.declared_counts", f"expected {expected_counts}")

    sectors = _expect_list(campaign["sectors"], f"{path}.sectors")
    if len(sectors) != len(SECTOR_SPECS):
        _error(f"{path}.sectors", "expected exactly five sectors")
    for index, (raw, expected) in enumerate(zip(sectors, SECTOR_SPECS), 1):
        sector_path = f"{path}.sectors[{index - 1}]"
        sector = _exact_keys(
            raw,
            (
                "order",
                "id",
                "theme",
                "tempo_bpm",
                "echo_cap",
                "encounters",
                "boss",
                "entry_requires",
                "completion_unlocks",
            ),
            sector_path,
        )
        sector_id, tempo, echo_cap, ids, boss = expected
        _expect_int(sector["order"], f"{sector_path}.order", index, index)
        _expect_string(sector["id"], f"{sector_path}.id", max_bytes=16)
        if sector["order"] != index or sector["id"] != sector_id:
            _error(sector_path, f"expected sector {index} {sector_id}")
        _expect_string(sector["theme"], f"{sector_path}.theme")
        _expect_int(
            sector["tempo_bpm"], f"{sector_path}.tempo_bpm", tempo, tempo
        )
        _expect_int(
            sector["echo_cap"], f"{sector_path}.echo_cap", echo_cap, echo_cap
        )
        if sector["tempo_bpm"] != tempo or sector["echo_cap"] != echo_cap:
            _error(sector_path, f"expected tempo/cap {tempo}/{echo_cap}")
        if sector["encounters"] != list(ids):
            _error(f"{sector_path}.encounters", f"expected {list(ids)}")
        if sector["boss"] != boss:
            _error(f"{sector_path}.boss", f"expected {boss}")
        _unique_strings(sector["entry_requires"], f"{sector_path}.entry_requires")
        _unique_strings(
            sector["completion_unlocks"],
            f"{sector_path}.completion_unlocks",
            minimum_items=1,
        )

    entries = _expect_list(campaign["encounters"], f"{path}.encounters")
    if len(entries) != len(ROSTER):
        _error(f"{path}.encounters", "expected exactly twenty encounters")
    for index, (raw, expected) in enumerate(zip(entries, ROSTER)):
        entry_path = f"{path}.encounters[{index}]"
        entry = _exact_keys(
            raw,
            (
                "order",
                "id",
                "numeric_id",
                "name",
                "kind",
                "file",
                "requires_encounter",
                "unlocks_encounter",
            ),
            entry_path,
        )
        prior_id = ROSTER[index - 1].encounter_id if index else None
        next_id = ROSTER[index + 1].encounter_id if index + 1 < len(ROSTER) else None
        expected_values = {
            "order": expected.order,
            "id": expected.encounter_id,
            "numeric_id": expected.numeric_id,
            "name": expected.name,
            "kind": expected.kind,
            "file": expected.relative_file,
            "requires_encounter": prior_id,
            "unlocks_encounter": next_id,
        }
        _expect_int(entry["order"], f"{entry_path}.order", expected.order, expected.order)
        _expect_string(entry["id"], f"{entry_path}.id", max_bytes=3)
        _expect_int(
            entry["numeric_id"],
            f"{entry_path}.numeric_id",
            expected.numeric_id,
            expected.numeric_id,
        )
        _expect_string(entry["name"], f"{entry_path}.name", max_bytes=64)
        _expect_string(entry["kind"], f"{entry_path}.kind", max_bytes=16)
        _expect_string(entry["file"], f"{entry_path}.file", max_bytes=128)
        for link_field in ("requires_encounter", "unlocks_encounter"):
            link = entry[link_field]
            if link is not None:
                _expect_string(link, f"{entry_path}.{link_field}", max_bytes=3)
        if entry != expected_values:
            _error(entry_path, f"does not match frozen roster entry {expected_values}")

    edges = {
        entry["id"]: (
            (entry["unlocks_encounter"],)
            if entry["unlocks_encounter"] is not None
            else ()
        )
        for entry in entries
    }
    reachable: set[str] = set()
    active: set[str] = set()

    def walk_campaign(encounter_id: str) -> None:
        if encounter_id in active:
            _error(f"{path}.encounters", f"campaign cycle includes {encounter_id}")
        if encounter_id in reachable:
            return
        if encounter_id not in edges:
            _error(f"{path}.encounters", f"edge targets unknown encounter {encounter_id}")
        active.add(encounter_id)
        for target in edges[encounter_id]:
            walk_campaign(target)
        active.remove(encounter_id)
        reachable.add(encounter_id)

    walk_campaign(campaign["start_encounter"])
    if reachable != set(ROSTER_IDS) or campaign["terminal_encounter"] not in reachable:
        _error(f"{path}.encounters", "not every encounter reaches the campaign chain")

    reachability = _exact_keys(
        campaign["reachability"],
        (
            "entry",
            "terminal",
            "linear_proof",
            "edge_count",
            "all_encounters_reachable",
            "cycles_permitted",
        ),
        f"{path}.reachability",
    )
    expected_reachability = {
        "entry": "B01",
        "terminal": "I04",
        "linear_proof": list(ROSTER_IDS),
        "edge_count": 19,
        "all_encounters_reachable": True,
        "cycles_permitted": False,
    }
    _expect_string(reachability["entry"], f"{path}.reachability.entry", max_bytes=3)
    _expect_string(
        reachability["terminal"], f"{path}.reachability.terminal", max_bytes=3
    )
    _unique_strings(
        reachability["linear_proof"],
        f"{path}.reachability.linear_proof",
        minimum_items=20,
        maximum_items=20,
    )
    _expect_int(
        reachability["edge_count"], f"{path}.reachability.edge_count", 19, 19
    )
    if type(reachability["all_encounters_reachable"]) is not bool:
        _error(
            f"{path}.reachability.all_encounters_reachable",
            "expected Boolean",
        )
    if type(reachability["cycles_permitted"]) is not bool:
        _error(f"{path}.reachability.cycles_permitted", "expected Boolean")
    if reachability != expected_reachability:
        _error(f"{path}.reachability", "does not prove the frozen acyclic chain")

    invariants = _exact_keys(
        campaign["invariants"],
        (
            "authoritative_loop_frames",
            "encounters_per_sector",
            "primary_per_sector",
            "boss_per_sector",
            "runtime_outcomes_embedded",
            "replay_ids_are_declarations_only",
        ),
        f"{path}.invariants",
    )
    expected_invariants = {
        "authoritative_loop_frames": 720,
        "encounters_per_sector": 4,
        "primary_per_sector": 3,
        "boss_per_sector": 1,
        "runtime_outcomes_embedded": False,
        "replay_ids_are_declarations_only": True,
    }
    for field in (
        "authoritative_loop_frames",
        "encounters_per_sector",
        "primary_per_sector",
        "boss_per_sector",
    ):
        _expect_int(
            invariants[field],
            f"{path}.invariants.{field}",
            expected_invariants[field],
            expected_invariants[field],
        )
    for field in ("runtime_outcomes_embedded", "replay_ids_are_declarations_only"):
        if type(invariants[field]) is not bool:
            _error(f"{path}.invariants.{field}", "expected Boolean")
    if invariants != expected_invariants:
        _error(f"{path}.invariants", f"expected {expected_invariants}")
    _unique_strings(
        campaign["completion_messages"],
        f"{path}.completion_messages",
        minimum_items=2,
        maximum_items=2,
    )
    if campaign["completion_messages"] != ["PROGRAM STABLE", "SEED TRANSMITTED"]:
        _error(
            f"{path}.completion_messages",
            "expected PROGRAM STABLE then SEED TRANSMITTED",
        )


def _validate_parameter_value(value: Any, allowed: frozenset[str], path: str) -> None:
    def shape(item: Any) -> str:
        if isinstance(item, bool):
            return "bool"
        if _is_int(item):
            return "int"
        if isinstance(item, str):
            return "str"
        if isinstance(item, list):
            if not item:
                return "list_empty"
            element_shapes = {shape(element) for element in item}
            if len(element_shapes) != 1:
                return "list_mixed"
            return f"list_{next(iter(element_shapes))}"
        return "unsupported"

    actual_shape = shape(value)
    if actual_shape not in allowed:
        _error(path, f"parameter shape {actual_shape!r} not in {sorted(allowed)}")

    def validate_leaf(item: Any, leaf_path: str, depth: int = 0) -> None:
        if depth > 2:
            _error(leaf_path, "parameter arrays may nest at most two levels")
        if isinstance(item, bool):
            return
        if _is_int(item):
            _expect_int(item, leaf_path, 0, 0xFFFF)
            return
        if isinstance(item, str):
            _expect_string(item, leaf_path, max_bytes=1024)
            return
        if isinstance(item, list):
            if not item or len(item) > 255:
                _error(leaf_path, "parameter array length must be 1..255")
            for index, child in enumerate(item):
                validate_leaf(child, f"{leaf_path}[{index}]", depth + 1)
            return
        _error(leaf_path, "unsupported parameter value")

    validate_leaf(value, path)


def _validate_parameters(parameters: Any, path: str) -> dict[str, Any]:
    values = _expect_dict(parameters, path)
    if not values or len(values) > 255:
        _error(path, "parameter object must contain 1..255 entries")
    for key, value in values.items():
        _expect_string(key, f"{path}.<key>", max_bytes=64)
        if key not in ALL_PARAMETER_KEYS:
            _error(path, f"unknown parameter key {key!r}")
        if key in PARAM_BOOL_KEYS:
            allowed = frozenset(("bool",))
        elif key in PARAM_INT_KEYS:
            allowed = frozenset(("int",))
        elif key in PARAM_INT_OR_LIST_INT_KEYS:
            allowed = frozenset(("int", "list_int"))
        elif key in PARAM_LIST_INT_KEYS:
            allowed = frozenset(("list_int",))
        elif key in PARAM_LIST_LIST_INT_KEYS:
            allowed = frozenset(("list_list_int",))
        elif key in PARAM_LIST_STRING_KEYS:
            allowed = frozenset(("list_str",))
        elif key in PARAM_STRING_OR_LIST_STRING_KEYS:
            allowed = frozenset(("str", "list_str"))
        else:
            allowed = frozenset(("str",))
        _validate_parameter_value(value, allowed, f"{path}.{key}")
    return values


def _validate_actor(actor: Any, echo_cap: int, path: str) -> str:
    actor_name = _expect_string(actor, path, max_bytes=32)
    if actor_name in {"current", "prelude_current", "player"}:
        return actor_name
    match = re.fullmatch(r"echo_([1-9][0-9]*)", actor_name)
    if not match or int(match.group(1)) > echo_cap:
        _error(path, f"actor {actor_name!r} exceeds echo cap {echo_cap}")
    return actor_name


def _validate_encounter(
    encounter: dict[str, Any],
    expected: RosterEntry,
    path: str,
    global_ids: dict[str, str],
    replay_ids: set[str],
    layouts: set[tuple[str, ...]],
) -> None:
    expected_top_keys = ENCOUNTER_KEYS | (
        frozenset(("deterministic_response_policy",))
        if expected.encounter_id == "I04"
        else frozenset()
    )
    if expected.encounter_id in STRUCTURED_RUNTIME_ENCOUNTERS:
        expected_top_keys |= frozenset(("stage_completion", "dependency_policy"))
    _exact_keys(encounter, expected_top_keys, path)
    _expect_string(encounter["schema"], f"{path}.schema", max_bytes=64)
    if encounter["schema"] != "recursion94.encounter":
        _error(f"{path}.schema", "expected recursion94.encounter")
    _expect_int(encounter["schema_version"], f"{path}.schema_version", 1, 1)
    if encounter["schema_version"] != 1:
        _error(f"{path}.schema_version", "expected 1")
    _expect_string(encounter["id"], f"{path}.id", max_bytes=3)
    _expect_int(
        encounter["numeric_id"],
        f"{path}.numeric_id",
        expected.numeric_id,
        expected.numeric_id,
    )
    _expect_string(encounter["name"], f"{path}.name", max_bytes=64)
    _expect_string(encounter["kind"], f"{path}.kind", max_bytes=16)
    _expect_int(
        encounter["campaign_order"],
        f"{path}.campaign_order",
        expected.order,
        expected.order,
    )
    frozen = {
        "id": expected.encounter_id,
        "numeric_id": expected.numeric_id,
        "name": expected.name,
        "kind": expected.kind,
        "campaign_order": expected.order,
    }
    for key, value in frozen.items():
        if encounter[key] != value:
            _error(f"{path}.{key}", f"expected {value!r}")

    sector = _exact_keys(
        encounter["sector"], ("id", "name", "theme"), f"{path}.sector"
    )
    _expect_string(sector["id"], f"{path}.sector.id", max_bytes=16)
    _expect_string(sector["name"], f"{path}.sector.name", max_bytes=16)
    if sector["id"] != expected.sector or sector["name"] != expected.sector:
        _error(f"{path}.sector", f"expected {expected.sector}")
    _expect_string(sector["theme"], f"{path}.sector.theme")
    sector_spec = SECTOR_SPEC_BY_ID[expected.sector]

    room = _exact_keys(
        encounter["room"],
        (
            "width_px",
            "height_px",
            "collision_cell_px",
            "visual_metatile_px",
            "collision_bounds",
            "tile_grid",
            "legend",
            "spawn",
            "exit",
        ),
        f"{path}.room",
    )
    expected_room = {
        "width_px": 320,
        "height_px": 224,
        "collision_cell_px": 8,
        "visual_metatile_px": 16,
    }
    for key, value in expected_room.items():
        _expect_int(room[key], f"{path}.room.{key}", value, value)
        if room[key] != value:
            _error(f"{path}.room.{key}", f"expected {value}")
    bounds = _exact_keys(
        room["collision_bounds"],
        ("min_x", "min_y", "max_x", "max_y"),
        f"{path}.room.collision_bounds",
    )
    for field, expected_value in (
        ("min_x", 0),
        ("min_y", 0),
        ("max_x", 319),
        ("max_y", 223),
    ):
        _expect_int(
            bounds[field],
            f"{path}.room.collision_bounds.{field}",
            expected_value,
            expected_value,
        )
    if bounds != {"min_x": 0, "min_y": 0, "max_x": 319, "max_y": 223}:
        _error(f"{path}.room.collision_bounds", "expected exact 320x224 bounds")
    legend = _exact_keys(room["legend"], ("#", "."), f"{path}.room.legend")
    if legend != {"#": "solid", ".": "walkable"}:
        _error(f"{path}.room.legend", "expected # solid and . walkable")
    grid = _expect_list(room["tile_grid"], f"{path}.room.tile_grid")
    if len(grid) != 14:
        _error(f"{path}.room.tile_grid", "expected 14 rows")
    checked_grid: list[str] = []
    for row_index, row in enumerate(grid):
        row = _expect_string(
            row, f"{path}.room.tile_grid[{row_index}]", min_length=20, max_bytes=20
        )
        if len(row) != 20 or frozenset(row) - frozenset(("#", ".")):
            _error(
                f"{path}.room.tile_grid[{row_index}]",
                "expected exactly twenty #/. cells",
            )
        checked_grid.append(row)
    grid_key = tuple(checked_grid)
    if grid_key in layouts:
        _error(f"{path}.room.tile_grid", "layout duplicates another encounter")
    layouts.add(grid_key)
    for point_name in ("spawn", "exit"):
        point = _exact_keys(
            room[point_name], ("x", "y"), f"{path}.room.{point_name}"
        )
        x = _expect_int(point["x"], f"{path}.room.{point_name}.x", 0, 319)
        y = _expect_int(point["y"], f"{path}.room.{point_name}.y", 0, 223)
        if x % 8 or y % 8:
            _error(f"{path}.room.{point_name}", "coordinates must be 8-pixel aligned")

    loop = _exact_keys(
        encounter["loop"],
        ("authoritative_frames", "clear_window_frames", "reset_policy"),
        f"{path}.loop",
    )
    _expect_int(
        loop["authoritative_frames"],
        f"{path}.loop.authoritative_frames",
        720,
        720,
    )
    if loop["authoritative_frames"] != 720:
        _error(f"{path}.loop.authoritative_frames", "expected exactly 720")
    clear_window = _expect_list(
        loop["clear_window_frames"], f"{path}.loop.clear_window_frames"
    )
    if len(clear_window) != 2:
        _error(f"{path}.loop.clear_window_frames", "expected [start, end]")
    clear_start = _expect_int(
        clear_window[0], f"{path}.loop.clear_window_frames[0]", 0, 719
    )
    clear_end = _expect_int(
        clear_window[1], f"{path}.loop.clear_window_frames[1]", 0, 719
    )
    if clear_start > clear_end:
        _error(f"{path}.loop.clear_window_frames", "start exceeds end")
    _expect_string(loop["reset_policy"], f"{path}.loop.reset_policy")

    echoes = _exact_keys(
        encounter["echoes"],
        ("cap", "minimum", "first_clear_range", "first_clear_range_unit"),
        f"{path}.echoes",
    )
    _expect_int(
        echoes["cap"],
        f"{path}.echoes.cap",
        sector_spec["echo_cap"],
        sector_spec["echo_cap"],
    )
    if echoes["cap"] != sector_spec["echo_cap"]:
        _error(
            f"{path}.echoes.cap",
            f"expected sector cap {sector_spec['echo_cap']}",
        )
    _expect_int(echoes["minimum"], f"{path}.echoes.minimum", 1, echoes["cap"])
    first_clear = _expect_list(
        echoes["first_clear_range"], f"{path}.echoes.first_clear_range"
    )
    if len(first_clear) != 2:
        _error(f"{path}.echoes.first_clear_range", "expected [minimum, maximum]")
    first_min = _expect_int(first_clear[0], f"{path}.echoes.first_clear_range[0]", 1)
    first_max = _expect_int(first_clear[1], f"{path}.echoes.first_clear_range[1]", 1)
    if first_min > first_max:
        _error(f"{path}.echoes.first_clear_range", "minimum exceeds maximum")
    if echoes["first_clear_range_unit"] != "authored_attempts":
        _error(
            f"{path}.echoes.first_clear_range_unit",
            "expected authored_attempts",
        )

    presentation_keys = {
        "palette_id",
        "music_id",
        "tempo_bpm",
        "visual_layers",
    }
    if expected.sector == "SOUND":
        presentation_keys.add("authoritative_phase_source")
    presentation = _exact_keys(
        encounter["presentation"], presentation_keys, f"{path}.presentation"
    )
    _expect_string(
        presentation["palette_id"], f"{path}.presentation.palette_id", max_bytes=64
    )
    _expect_string(
        presentation["music_id"], f"{path}.presentation.music_id", max_bytes=64
    )
    _expect_int(
        presentation["tempo_bpm"],
        f"{path}.presentation.tempo_bpm",
        sector_spec["tempo_bpm"],
        sector_spec["tempo_bpm"],
    )
    if presentation["tempo_bpm"] != sector_spec["tempo_bpm"]:
        _error(
            f"{path}.presentation.tempo_bpm",
            f"expected {sector_spec['tempo_bpm']}",
        )
    _unique_strings(
        presentation["visual_layers"],
        f"{path}.presentation.visual_layers",
        minimum_items=1,
    )
    if expected.sector == "SOUND":
        phase_source = _expect_string(
            presentation["authoritative_phase_source"],
            f"{path}.presentation.authoritative_phase_source",
        )
        if not phase_source.startswith("68000") or "Z80" in phase_source.upper():
            _error(
                f"{path}.presentation.authoritative_phase_source",
                "must name a 68000 authoritative source, never Z80 playback",
            )

    _unique_strings(
        encounter["mechanics"], f"{path}.mechanics", minimum_items=1, maximum_items=16
    )

    entity_groups = _exact_keys(
        encounter["entities"], ENTITY_CATEGORIES, f"{path}.entities"
    )
    entities: list[dict[str, Any]] = []
    entity_ids: set[str] = set()
    for category in ENTITY_CATEGORIES:
        raw_entities = _expect_list(
            entity_groups[category], f"{path}.entities.{category}"
        )
        if len(raw_entities) > BUDGET_CAPS[category]:
            _error(
                f"{path}.entities.{category}",
                f"count exceeds cap {BUDGET_CAPS[category]}",
            )
        for index, raw_entity in enumerate(raw_entities):
            entity_path = f"{path}.entities.{category}[{index}]"
            entity = _exact_keys(
                raw_entity,
                (
                    "logical_id",
                    "type",
                    "x",
                    "y",
                    "state",
                    "reset_policy",
                    "anchor_policy",
                    "parameters",
                ),
                entity_path,
            )
            logical_id = _expect_string(
                entity["logical_id"], f"{entity_path}.logical_id", max_bytes=64
            )
            if not STABLE_ID_RE.fullmatch(logical_id) or not logical_id.startswith(
                f"{expected.encounter_id}_"
            ):
                _error(
                    f"{entity_path}.logical_id",
                    f"must be a stable {expected.encounter_id}_ identifier",
                )
            if logical_id in entity_ids:
                _error(f"{entity_path}.logical_id", "duplicate entity ID")
            if logical_id in global_ids:
                _error(
                    f"{entity_path}.logical_id",
                    f"duplicates {global_ids[logical_id]}",
                )
            entity_ids.add(logical_id)
            global_ids[logical_id] = f"{entity_path}.logical_id"
            entity_type = _expect_string(
                entity["type"], f"{entity_path}.type", max_bytes=64
            )
            if entity_type not in ENTITY_TYPES_BY_CATEGORY[category]:
                _error(
                    f"{entity_path}.type",
                    f"unknown or wrong-category type {entity_type!r}",
                )
            x = _expect_int(entity["x"], f"{entity_path}.x", 0, 319)
            y = _expect_int(entity["y"], f"{entity_path}.y", 0, 223)
            if x % 8 or y % 8:
                _error(entity_path, "entity coordinates must be 8-pixel aligned")
            state = _exact_keys(
                entity["state"], ("initial", "states"), f"{entity_path}.state"
            )
            states = _unique_strings(
                state["states"],
                f"{entity_path}.state.states",
                minimum_items=1,
                maximum_items=255,
            )
            initial = _expect_string(
                state["initial"], f"{entity_path}.state.initial", max_bytes=64
            )
            if initial not in states:
                _error(f"{entity_path}.state.initial", "not present in states")
            _expect_string(
                entity["reset_policy"], f"{entity_path}.reset_policy", max_bytes=512
            )
            anchor = entity["anchor_policy"]
            if anchor != "forbidden":
                anchor_obj = _exact_keys(
                    anchor,
                    ("allowed", "record_type", "max_records", "idempotent_state"),
                    f"{entity_path}.anchor_policy",
                )
                if anchor_obj["allowed"] is not True:
                    _error(f"{entity_path}.anchor_policy.allowed", "expected true")
                _expect_string(
                    anchor_obj["record_type"],
                    f"{entity_path}.anchor_policy.record_type",
                    max_bytes=32,
                )
                if anchor_obj["record_type"] not in ANCHOR_CODES:
                    _error(
                        f"{entity_path}.anchor_policy.record_type",
                        "unknown typed anchor record",
                    )
                _expect_int(
                    anchor_obj["max_records"],
                    f"{entity_path}.anchor_policy.max_records",
                    1,
                    1,
                )
                if anchor_obj["max_records"] != 1:
                    _error(
                        f"{entity_path}.anchor_policy.max_records",
                        "typed anchors must be bounded to one record",
                    )
                _expect_string(
                    anchor_obj["idempotent_state"],
                    f"{entity_path}.anchor_policy.idempotent_state",
                    max_bytes=64,
                )
                if anchor_obj["idempotent_state"] not in states:
                    _error(
                        f"{entity_path}.anchor_policy.idempotent_state",
                        "must name a declared entity state",
                    )
            _validate_parameters(
                entity["parameters"], f"{entity_path}.parameters"
            )
            entities.append(entity)

    objective_graph = _exact_keys(
        encounter["objective_graph"],
        ("nodes", "completion"),
        f"{path}.objective_graph",
    )
    nodes = _expect_list(
        objective_graph["nodes"], f"{path}.objective_graph.nodes"
    )
    if not nodes or len(nodes) > 255:
        _error(f"{path}.objective_graph.nodes", "expected 1..255 objectives")
    objective_ids: set[str] = set()
    checked_nodes: list[dict[str, Any]] = []
    for index, raw_node in enumerate(nodes):
        node_path = f"{path}.objective_graph.nodes[{index}]"
        node = _exact_keys(
            raw_node,
            (
                "objective_id",
                "type",
                "depends_on",
                "condition",
                "deadline_frame",
                "role",
            ),
            node_path,
        )
        objective_id = _expect_string(
            node["objective_id"], f"{node_path}.objective_id", max_bytes=64
        )
        if not STABLE_ID_RE.fullmatch(objective_id) or not objective_id.startswith(
            f"{expected.encounter_id}_"
        ):
            _error(
                f"{node_path}.objective_id",
                f"must be a stable {expected.encounter_id}_ identifier",
            )
        if objective_id in objective_ids:
            _error(f"{node_path}.objective_id", "duplicate objective ID")
        if objective_id in global_ids:
            _error(
                f"{node_path}.objective_id", f"duplicates {global_ids[objective_id]}"
            )
        objective_ids.add(objective_id)
        global_ids[objective_id] = f"{node_path}.objective_id"
        _expect_string(node["type"], f"{node_path}.type", max_bytes=64)
        _unique_strings(node["depends_on"], f"{node_path}.depends_on")
        _expect_string(node["condition"], f"{node_path}.condition")
        _expect_int(
            node["deadline_frame"], f"{node_path}.deadline_frame", 0, 719
        )
        _validate_actor(node["role"], echoes["cap"], f"{node_path}.role")
        checked_nodes.append(node)
    for index, node in enumerate(checked_nodes):
        for dependency in node["depends_on"]:
            if dependency not in objective_ids:
                _error(
                    f"{path}.objective_graph.nodes[{index}].depends_on",
                    f"unknown objective {dependency}",
                )
            if dependency == node["objective_id"]:
                _error(
                    f"{path}.objective_graph.nodes[{index}].depends_on",
                    "objective cannot depend on itself",
                )
    visiting: set[str] = set()
    visited: set[str] = set()
    node_by_id = {node["objective_id"]: node for node in checked_nodes}

    def visit(objective_id: str) -> None:
        if objective_id in visiting:
            _error(f"{path}.objective_graph", f"cycle includes {objective_id}")
        if objective_id in visited:
            return
        visiting.add(objective_id)
        for dependency in node_by_id[objective_id]["depends_on"]:
            visit(dependency)
        visiting.remove(objective_id)
        visited.add(objective_id)

    for objective_id in sorted(objective_ids):
        visit(objective_id)
    _expect_string(
        objective_graph["completion"], f"{path}.objective_graph.completion"
    )

    if expected.encounter_id in STRUCTURED_RUNTIME_ENCOUNTERS:
        def validate_completion_state(value: Any, state_path: str) -> None:
            state = _exact_keys(
                value, ("field", "mask", "value"), state_path
            )
            field = _expect_string(
                state["field"], f"{state_path}.field", max_bytes=16
            )
            if field not in RUNTIME_REFERENCE_FIELDS:
                _error(
                    f"{state_path}.field",
                    f"expected one of {sorted(RUNTIME_REFERENCE_FIELDS)}",
                )
            maximum = 0xFF if field == "health" else 0xFFFF
            mask = _expect_int(
                state["mask"], f"{state_path}.mask", 1, maximum
            )
            result = _expect_int(
                state["value"], f"{state_path}.value", 0, maximum
            )
            if result & ~mask:
                _error(
                    f"{state_path}.value",
                    "value contains bits outside the comparison mask",
                )

        stage_completion = _exact_keys(
            encounter["stage_completion"],
            ("object", "completion_state"),
            f"{path}.stage_completion",
        )
        completion_object = _expect_string(
            stage_completion["object"],
            f"{path}.stage_completion.object",
            max_bytes=64,
        )
        if completion_object not in entity_ids:
            _error(
                f"{path}.stage_completion.object",
                f"unknown entity {completion_object}",
            )
        validate_completion_state(
            stage_completion["completion_state"],
            f"{path}.stage_completion.completion_state",
        )

        dependency_policies = _expect_list(
            encounter["dependency_policy"], f"{path}.dependency_policy"
        )
        if len(dependency_policies) > 255:
            _error(f"{path}.dependency_policy", "expected at most 255 groups")
        consumers: set[str] = set()
        for policy_index, raw_policy in enumerate(dependency_policies):
            policy_path = f"{path}.dependency_policy[{policy_index}]"
            policy = _exact_keys(
                raw_policy,
                (
                    "consumer",
                    "policy",
                    "conditions",
                    "completion_state",
                    "duration_frames",
                ),
                policy_path,
            )
            consumer = _expect_string(
                policy["consumer"], f"{policy_path}.consumer", max_bytes=64
            )
            if consumer not in entity_ids:
                _error(
                    f"{policy_path}.consumer", f"unknown entity {consumer}"
                )
            if consumer in consumers:
                _error(
                    f"{policy_path}.consumer",
                    "consumer has more than one dependency group",
                )
            consumers.add(consumer)
            policy_name = _expect_string(
                policy["policy"], f"{policy_path}.policy", max_bytes=3
            )
            if policy_name not in {"ALL", "ANY"}:
                _error(f"{policy_path}.policy", "expected ALL or ANY")
            conditions = _expect_list(
                policy["conditions"], f"{policy_path}.conditions"
            )
            if not conditions or len(conditions) > 255:
                _error(
                    f"{policy_path}.conditions",
                    "expected 1..255 provider conditions",
                )
            providers: set[str] = set()
            for condition_index, raw_condition in enumerate(conditions):
                condition_path = (
                    f"{policy_path}.conditions[{condition_index}]"
                )
                condition = _exact_keys(
                    raw_condition,
                    ("provider", "completion_state"),
                    condition_path,
                )
                provider = _expect_string(
                    condition["provider"],
                    f"{condition_path}.provider",
                    max_bytes=64,
                )
                if provider not in entity_ids:
                    _error(
                        f"{condition_path}.provider",
                        f"unknown entity {provider}",
                    )
                if provider == consumer:
                    _error(
                        f"{condition_path}.provider",
                        "dependency cannot refer to its consumer",
                    )
                if provider in providers:
                    _error(
                        f"{condition_path}.provider",
                        "duplicate provider in dependency group",
                    )
                providers.add(provider)
                validate_completion_state(
                    condition["completion_state"],
                    f"{condition_path}.completion_state",
                )
            validate_completion_state(
                policy["completion_state"],
                f"{policy_path}.completion_state",
            )
            _expect_int(
                policy["duration_frames"],
                f"{policy_path}.duration_frames",
                0,
                720,
            )

    for entity_index, entity in enumerate(entities):
        def inspect_reference(value: Any, ref_path: str) -> None:
            if isinstance(value, str):
                if (
                    re.fullmatch(
                        rf"{re.escape(expected.encounter_id)}_[A-Z0-9_]+", value
                    )
                    and value not in entity_ids
                    and value not in objective_ids
                    and value not in NON_ENTITY_REFERENCE_IDS
                ):
                    _error(ref_path, f"unknown stable reference {value}")
            elif isinstance(value, list):
                for child_index, child in enumerate(value):
                    inspect_reference(child, f"{ref_path}[{child_index}]")
            elif isinstance(value, dict):
                for key, child in value.items():
                    inspect_reference(child, f"{ref_path}.{key}")

        inspect_reference(
            entity["parameters"], f"{path}.entities.<flat>[{entity_index}].parameters"
        )

    roles = _expect_list(
        encounter["canonical_role_schedule"],
        f"{path}.canonical_role_schedule",
    )
    if not roles or len(roles) > 255:
        _error(f"{path}.canonical_role_schedule", "expected 1..255 roles")
    role_ids: set[str] = set()
    role_actors: set[str] = set()
    for role_index, raw_role in enumerate(roles):
        role_path = f"{path}.canonical_role_schedule[{role_index}]"
        role = _exact_keys(
            raw_role,
            ("role_id", "actor", "start_frame", "end_frame", "actions"),
            role_path,
        )
        role_id = _expect_string(
            role["role_id"], f"{role_path}.role_id", max_bytes=64
        )
        if not STABLE_ID_RE.fullmatch(role_id) or not role_id.startswith(
            f"{expected.encounter_id}_"
        ):
            _error(f"{role_path}.role_id", "invalid stable role ID")
        if role_id in role_ids or role_id in global_ids:
            _error(f"{role_path}.role_id", "duplicate stable ID")
        role_ids.add(role_id)
        global_ids[role_id] = f"{role_path}.role_id"
        actor = _validate_actor(role["actor"], echoes["cap"], f"{role_path}.actor")
        if actor in role_actors:
            _error(f"{role_path}.actor", "actor has more than one canonical role")
        role_actors.add(actor)
        start = _expect_int(role["start_frame"], f"{role_path}.start_frame", 0, 719)
        end = _expect_int(role["end_frame"], f"{role_path}.end_frame", 0, 719)
        if start > end:
            _error(role_path, "role start exceeds end")
        actions = _expect_list(role["actions"], f"{role_path}.actions")
        if not actions or len(actions) > 255:
            _error(f"{role_path}.actions", "expected 1..255 actions")
        prior_frame = -1
        action_keys: set[tuple[int, str, str]] = set()
        for action_index, raw_action in enumerate(actions):
            action_path = f"{role_path}.actions[{action_index}]"
            action = _exact_keys(
                raw_action, ("frame", "action", "target"), action_path
            )
            frame = _expect_int(action["frame"], f"{action_path}.frame", start, end)
            if frame < prior_frame:
                _error(f"{role_path}.actions", "actions must be frame ordered")
            prior_frame = frame
            action_name = _expect_string(
                action["action"], f"{action_path}.action", max_bytes=64
            )
            target = _expect_string(
                action["target"], f"{action_path}.target", max_bytes=64
            )
            if target not in entity_ids:
                _error(f"{action_path}.target", f"unknown entity {target}")
            action_key = (frame, action_name, target)
            if action_key in action_keys:
                _error(action_path, "duplicate action")
            action_keys.add(action_key)

    alternatives = _expect_list(
        encounter["alternative_routes"], f"{path}.alternative_routes"
    )
    if not alternatives or len(alternatives) > 255:
        _error(f"{path}.alternative_routes", "expected 1..255 routes")
    for index, raw_alternative in enumerate(alternatives):
        alternative_path = f"{path}.alternative_routes[{index}]"
        alternative = _exact_keys(
            raw_alternative,
            ("id", "role_changes", "constraints", "echo_cost"),
            alternative_path,
        )
        alternative_id = _expect_string(
            alternative["id"], f"{alternative_path}.id", max_bytes=64
        )
        if not STABLE_ID_RE.fullmatch(alternative_id) or alternative_id in global_ids:
            _error(f"{alternative_path}.id", "invalid or duplicate stable ID")
        global_ids[alternative_id] = f"{alternative_path}.id"
        _expect_string(
            alternative["role_changes"], f"{alternative_path}.role_changes"
        )
        _unique_strings(
            alternative["constraints"],
            f"{alternative_path}.constraints",
            minimum_items=1,
        )
        _expect_int(
            alternative["echo_cost"],
            f"{alternative_path}.echo_cost",
            0,
            echoes["cap"],
        )

    failures = _expect_list(encounter["failure_signals"], f"{path}.failure_signals")
    if not failures or len(failures) > 255:
        _error(f"{path}.failure_signals", "expected 1..255 signals")
    for index, raw_failure in enumerate(failures):
        failure_path = f"{path}.failure_signals[{index}]"
        failure = _exact_keys(
            raw_failure,
            ("id", "condition", "visual", "audio", "timeline"),
            failure_path,
        )
        failure_id = _expect_string(
            failure["id"], f"{failure_path}.id", max_bytes=64
        )
        if not STABLE_ID_RE.fullmatch(failure_id) or failure_id in global_ids:
            _error(f"{failure_path}.id", "invalid or duplicate stable ID")
        global_ids[failure_id] = f"{failure_path}.id"
        for field in ("condition", "visual", "audio", "timeline"):
            _expect_string(failure[field], f"{failure_path}.{field}")

    phases = _expect_list(encounter["boss_phases"], f"{path}.boss_phases")
    if expected.kind == "primary" and phases:
        _error(f"{path}.boss_phases", "primary encounters cannot have boss phases")
    if expected.kind == "boss" and not (3 <= len(phases) <= 8):
        _error(f"{path}.boss_phases", "boss encounters require 3..8 phases")
    expected_window_start = 0
    for phase_index, raw_phase in enumerate(phases):
        phase_path = f"{path}.boss_phases[{phase_index}]"
        phase = _exact_keys(
            raw_phase,
            (
                "phase_id",
                "entry_condition",
                "frame_window",
                "attacks",
                "required_progress",
                "exit_condition",
            ),
            phase_path,
        )
        phase_id = _expect_string(
            phase["phase_id"], f"{phase_path}.phase_id", max_bytes=64
        )
        if not STABLE_ID_RE.fullmatch(phase_id) or phase_id in global_ids:
            _error(f"{phase_path}.phase_id", "invalid or duplicate stable ID")
        global_ids[phase_id] = f"{phase_path}.phase_id"
        _expect_string(
            phase["entry_condition"], f"{phase_path}.entry_condition"
        )
        window = _expect_list(phase["frame_window"], f"{phase_path}.frame_window")
        if len(window) != 2:
            _error(f"{phase_path}.frame_window", "expected [start, end]")
        start = _expect_int(window[0], f"{phase_path}.frame_window[0]", 0, 719)
        end = _expect_int(window[1], f"{phase_path}.frame_window[1]", 0, 719)
        if start != expected_window_start or start > end:
            _error(
                f"{phase_path}.frame_window",
                f"expected contiguous start {expected_window_start}",
            )
        expected_window_start = end + 1
        attacks = _expect_list(phase["attacks"], f"{phase_path}.attacks")
        if not attacks or len(attacks) > 255:
            _error(f"{phase_path}.attacks", "expected 1..255 attacks")
        attack_keys: set[tuple[Any, ...]] = set()
        for attack_index, raw_attack in enumerate(attacks):
            attack_path = f"{phase_path}.attacks[{attack_index}]"
            attack = _expect_dict(raw_attack, attack_path)
            variants = frozenset(attack) - frozenset(("pattern",))
            if variants not in (
                frozenset(("frames",)),
                frozenset(("period_frames",)),
                frozenset(("source",)),
            ):
                _error(
                    attack_path,
                    "expected pattern plus exactly one of frames/period_frames/source",
                )
            pattern = _expect_string(
                attack.get("pattern"), f"{attack_path}.pattern"
            )
            if "frames" in attack:
                frames = _expect_list(attack["frames"], f"{attack_path}.frames")
                if not frames or len(frames) > 255:
                    _error(f"{attack_path}.frames", "expected 1..255 frames")
                checked_frames = [
                    _expect_int(
                        frame, f"{attack_path}.frames[{i}]", start, end
                    )
                    for i, frame in enumerate(frames)
                ]
                if checked_frames != sorted(set(checked_frames)):
                    _error(f"{attack_path}.frames", "frames must be sorted and unique")
                key = (pattern, "frames", tuple(checked_frames))
            elif "period_frames" in attack:
                period = _expect_int(
                    attack["period_frames"],
                    f"{attack_path}.period_frames",
                    1,
                    720,
                )
                key = (pattern, "period", period)
            else:
                source = _expect_string(
                    attack["source"], f"{attack_path}.source"
                )
                key = (pattern, "source", source)
            if key in attack_keys:
                _error(attack_path, "duplicate phase attack")
            attack_keys.add(key)
        _expect_string(
            phase["required_progress"], f"{phase_path}.required_progress"
        )
        _expect_string(phase["exit_condition"], f"{phase_path}.exit_condition")
    if phases and expected_window_start != 720:
        _error(f"{path}.boss_phases", "phase windows must cover frames 0..719")

    policy = encounter.get("deterministic_response_policy", [])
    if expected.encounter_id == "I04":
        policy_items = _expect_list(policy, f"{path}.deterministic_response_policy")
        if not policy_items or len(policy_items) > 255:
            _error(
                f"{path}.deterministic_response_policy",
                "ORACLE requires 1..255 deterministic policies",
            )
        for index, raw_policy in enumerate(policy_items, 1):
            policy_path = f"{path}.deterministic_response_policy[{index - 1}]"
            item = _exact_keys(
                raw_policy, ("priority", "condition", "response"), policy_path
            )
            _expect_int(
                item["priority"], f"{policy_path}.priority", index, index
            )
            if item["priority"] != index:
                _error(f"{policy_path}.priority", f"expected priority {index}")
            _expect_string(item["condition"], f"{policy_path}.condition")
            _expect_string(item["response"], f"{policy_path}.response")

    budgets = _exact_keys(encounter["budgets"], BUDGET_FIELDS, f"{path}.budgets")
    for field in BUDGET_FIELDS:
        value = _expect_int(
            budgets[field], f"{path}.budgets.{field}", 0, BUDGET_CAPS[field]
        )
        if field in {"cpu_percent", "dma_words"} and value == 0:
            _error(f"{path}.budgets.{field}", "must be nonzero")
    if budgets["actors"] < echoes["cap"] + 1:
        _error(f"{path}.budgets.actors", "must cover current plus every echo slot")
    if budgets["sprites_per_scanline"] > budgets["hardware_sprites"]:
        _error(
            f"{path}.budgets.sprites_per_scanline",
            "cannot exceed total hardware sprites",
        )
    for category in ENTITY_CATEGORIES:
        if budgets[category] < len(entity_groups[category]):
            _error(
                f"{path}.budgets.{category}",
                "budget does not cover declared entities",
            )

    replays = _exact_keys(
        encounter["replays"],
        ("canonical", "failure", "stress", "phase_regression"),
        f"{path}.replays",
    )
    for field in ("canonical", "failure", "stress"):
        replay_id = _expect_string(
            replays[field], f"{path}.replays.{field}", max_bytes=64
        )
        if not REPLAY_ID_RE.fullmatch(replay_id) or not replay_id.startswith(
            f"{expected.encounter_id}_"
        ):
            _error(f"{path}.replays.{field}", "invalid replay declaration ID")
        if replay_id in replay_ids or replay_id in global_ids:
            _error(
                f"{path}.replays.{field}",
                "duplicate replay declaration ID or stable ID",
            )
        replay_ids.add(replay_id)
        global_ids[replay_id] = f"{path}.replays.{field}"
    phase_replay = replays["phase_regression"]
    if expected.kind == "boss":
        phase_replay = _expect_string(
            phase_replay, f"{path}.replays.phase_regression", max_bytes=64
        )
        if (
            not REPLAY_ID_RE.fullmatch(phase_replay)
            or phase_replay in replay_ids
            or phase_replay in global_ids
        ):
            _error(
                f"{path}.replays.phase_regression",
                "invalid or duplicate boss replay declaration",
            )
        replay_ids.add(phase_replay)
        global_ids[phase_replay] = f"{path}.replays.phase_regression"
    elif phase_replay is not None:
        _error(
            f"{path}.replays.phase_regression",
            "primary phase replay must be null",
        )

    progression = _exact_keys(
        encounter["progression"], ("requires", "unlocks"), f"{path}.progression"
    )
    requires = _unique_strings(
        progression["requires"], f"{path}.progression.requires"
    )
    unlocks = _unique_strings(
        progression["unlocks"], f"{path}.progression.unlocks", minimum_items=1
    )
    prior_id = ROSTER[expected.order - 2].encounter_id if expected.order > 1 else None
    next_id = (
        ROSTER[expected.order].encounter_id
        if expected.order < len(ROSTER)
        else None
    )
    if prior_id is None:
        if requires:
            _error(f"{path}.progression.requires", "B01 must have no prerequisite")
    elif prior_id not in requires:
        _error(
            f"{path}.progression.requires",
            f"must include prior encounter {prior_id}",
        )
    if next_id is not None and next_id not in unlocks:
        _error(
            f"{path}.progression.unlocks",
            f"must include next encounter {next_id}",
        )

    narrative = _exact_keys(
        encounter["narrative"],
        ("briefing", "success", "failure", "echo_hint"),
        f"{path}.narrative",
    )
    for field in ("briefing", "success", "failure", "echo_hint"):
        text = _expect_string(narrative[field], f"{path}.narrative.{field}")
        if "TODO" in text.upper() or "PLACEHOLDER" in text.upper():
            _error(f"{path}.narrative.{field}", "placeholder text is forbidden")


def load_and_validate(content_root: Path | str) -> ValidatedContent:
    """Load and validate the entire fixed campaign, returning normalized inputs."""

    root = Path(content_root).resolve()
    campaign_path = root / "campaign.json"
    campaign = _strict_json_load(campaign_path)
    _validate_campaign(campaign, str(campaign_path))

    encounter_dir = root / "encounters"
    actual_files = {
        path.relative_to(root).as_posix()
        for path in encounter_dir.rglob("*.json")
        if path.is_file()
    }
    expected_files = {entry.relative_file for entry in ROSTER}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        _error(
            str(encounter_dir),
            f"manifest set mismatch; missing={missing}, extra={extra}",
        )

    encounters: list[dict[str, Any]] = []
    global_ids: dict[str, str] = {}
    replay_ids: set[str] = set()
    layouts: set[tuple[str, ...]] = set()
    for entry in ROSTER:
        source_path = root / entry.relative_file
        resolved = source_path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            _error(str(source_path), "manifest path escapes content root")
        encounter = _strict_json_load(source_path)
        _validate_encounter(
            encounter,
            entry,
            str(source_path),
            global_ids,
            replay_ids,
            layouts,
        )
        encounters.append(encounter)

    kinds = [encounter["kind"] for encounter in encounters]
    if kinds.count("primary") != 15 or kinds.count("boss") != 5:
        _error(str(root), "campaign composition must be 15 primary plus 5 bosses")
    if len(replay_ids) != 65:
        _error(str(root), "expected 65 unique replay ID declarations")
    source_sha256 = _source_bundle_digest(campaign, encounters)
    return ValidatedContent(root, campaign, tuple(encounters), source_sha256)


def _entity_type_code(entity_type: str) -> int:
    return zlib.crc32(entity_type.encode("ascii")) & 0xFFFF


_TYPE_CODE_TO_NAMES: dict[int, list[str]] = {}
for _type_name in ALL_ENTITY_TYPES:
    _TYPE_CODE_TO_NAMES.setdefault(_entity_type_code(_type_name), []).append(_type_name)
_TYPE_COLLISIONS = {
    code: names for code, names in _TYPE_CODE_TO_NAMES.items() if len(names) > 1
}
if _TYPE_COLLISIONS:
    raise RuntimeError(f"entity type code collision: {_TYPE_COLLISIONS}")


def _align2(value: int) -> int:
    return (value + 1) & ~1


def _collect_parameter_strings(value: Any, strings: set[str]) -> None:
    if isinstance(value, str):
        strings.add(value)
    elif isinstance(value, list):
        for child in value:
            _collect_parameter_strings(child, strings)


class _StringTable:
    def __init__(self, strings: Iterable[str]):
        self._offsets: dict[str, int] = {}
        data = bytearray(b"\x00")
        for value in sorted(set(strings), key=lambda item: item.encode("utf-8")):
            encoded = value.encode("utf-8")
            if b"\x00" in encoded:
                raise ContentValidationError("string table input contains NUL")
            offset = len(data)
            if offset > 0xFFFF:
                raise ContentValidationError("encounter string table offset exceeds u16")
            self._offsets[value] = offset
            data.extend(encoded)
            data.append(0)
        if len(data) > 0x10000:
            raise ContentValidationError("encounter string table exceeds 64 KiB")
        self.data = bytes(data)

    def offset(self, value: str | None) -> int:
        if value is None:
            return 0
        try:
            return self._offsets[value]
        except KeyError as exc:
            raise ContentCompileError(f"string was not collected: {value!r}") from exc


def _encounter_strings(encounter: dict[str, Any]) -> set[str]:
    strings = {
        encounter["presentation"]["palette_id"],
        encounter["presentation"]["music_id"],
        encounter["objective_graph"]["completion"],
    }
    for category in ENTITY_CATEGORIES:
        for entity in encounter["entities"][category]:
            strings.add(entity["logical_id"])
            strings.add(entity["type"])
            strings.add(entity["reset_policy"])
            strings.add(entity["state"]["initial"])
            strings.update(entity["state"]["states"])
            if isinstance(entity["anchor_policy"], dict):
                strings.add(entity["anchor_policy"]["record_type"])
                strings.add(entity["anchor_policy"]["idempotent_state"])
            for key, value in entity["parameters"].items():
                strings.add(key)
                _collect_parameter_strings(value, strings)
    for node in encounter["objective_graph"]["nodes"]:
        strings.update(
            (
                node["objective_id"],
                node["type"],
                node["condition"],
                node["role"],
            )
        )
    for role in encounter["canonical_role_schedule"]:
        strings.update((role["role_id"], role["actor"]))
        for action in role["actions"]:
            strings.add(action["action"])
    for phase in encounter["boss_phases"]:
        strings.update(
            (
                phase["phase_id"],
                phase["entry_condition"],
                phase["required_progress"],
                phase["exit_condition"],
            )
        )
        for attack in phase["attacks"]:
            strings.add(attack["pattern"])
            if "source" in attack:
                strings.add(attack["source"])
    for policy in encounter.get("deterministic_response_policy", []):
        strings.update((policy["condition"], policy["response"]))
    return strings


def _pack_parameter_value(value: Any, strings: _StringTable) -> bytes:
    if isinstance(value, bool):
        return bytes((PARAM_TRUE if value else PARAM_FALSE,))
    if _is_int(value):
        return struct.pack(">BI", PARAM_UINT, value)
    if isinstance(value, str):
        return struct.pack(">BH", PARAM_STRING, strings.offset(value))
    if isinstance(value, list):
        result = bytearray(struct.pack(">BH", PARAM_LIST, len(value)))
        for child in value:
            result.extend(_pack_parameter_value(child, strings))
        return bytes(result)
    raise ContentCompileError(f"cannot pack parameter value {value!r}")


def _pack_parameter_map(
    parameters: Mapping[str, Any], strings: _StringTable
) -> bytes:
    result = bytearray(struct.pack(">H", len(parameters)))
    for key in sorted(parameters, key=lambda item: item.encode("utf-8")):
        result.extend(struct.pack(">H", strings.offset(key)))
        result.extend(_pack_parameter_value(parameters[key], strings))
    return bytes(result)


class _DetailBuilder:
    def __init__(self, absolute_base: int):
        self.absolute_base = absolute_base
        self.data = bytearray()

    def append(self, payload: bytes, *, alignment: int = 2) -> int:
        if alignment not in (1, 2, 4):
            raise ValueError("unsupported detail alignment")
        while (self.absolute_base + len(self.data)) % alignment:
            self.data.append(0)
        offset = self.absolute_base + len(self.data)
        self.data.extend(payload)
        return offset

    def reserve(self, size: int, *, alignment: int = 2) -> int:
        return self.append(bytes(size), alignment=alignment)

    def pack_into(self, absolute_offset: int, format_string: str, *values: Any) -> None:
        relative = absolute_offset - self.absolute_base
        struct.pack_into(format_string, self.data, relative, *values)


def _pack_collision(grid: Sequence[str]) -> bytes:
    result = bytearray((len(grid) * len(grid[0]) + 7) // 8)
    bit_index = 0
    for row in grid:
        for cell in row:
            if cell == "#":
                result[bit_index // 8] |= 1 << (7 - bit_index % 8)
            bit_index += 1
    return bytes(result)


def _compile_chunk(encounter: dict[str, Any]) -> ChunkResult:
    strings = _StringTable(_encounter_strings(encounter))
    collision = _pack_collision(encounter["room"]["tile_grid"])

    entities_with_category = [
        (category, entity)
        for category in ENTITY_CATEGORIES
        for entity in sorted(
            encounter["entities"][category], key=lambda item: item["logical_id"]
        )
    ]
    objectives = sorted(
        encounter["objective_graph"]["nodes"], key=lambda item: item["objective_id"]
    )
    roles = sorted(
        encounter["canonical_role_schedule"], key=lambda item: item["role_id"]
    )
    phases = sorted(
        encounter["boss_phases"],
        key=lambda item: (item["frame_window"][0], item["phase_id"]),
    )
    policies = sorted(
        encounter.get("deterministic_response_policy", []),
        key=lambda item: item["priority"],
    )

    collision_offset = CHUNK_HEADER_SIZE
    cursor = _align2(collision_offset + len(collision))
    entity_offset = cursor
    cursor += len(entities_with_category) * ENTITY_RECORD_SIZE
    objective_offset = cursor
    cursor += len(objectives) * OBJECTIVE_RECORD_SIZE
    role_offset = cursor
    cursor += len(roles) * ROLE_RECORD_SIZE
    phase_offset = cursor
    cursor += len(phases) * PHASE_RECORD_SIZE
    policy_offset = cursor
    cursor += len(policies) * POLICY_RECORD_SIZE
    detail_offset = _align2(cursor)
    details = _DetailBuilder(detail_offset)

    entity_index = {
        entity["logical_id"]: index
        for index, (_, entity) in enumerate(entities_with_category)
    }
    entity_records: list[bytes] = []
    for category, entity in entities_with_category:
        state_names = entity["state"]["states"]
        states_payload = b"".join(
            struct.pack(">H", strings.offset(state)) for state in state_names
        )
        states_offset = details.append(states_payload)
        parameter_payload = _pack_parameter_map(entity["parameters"], strings)
        parameters_offset = details.append(parameter_payload)
        anchor = entity["anchor_policy"]
        if anchor == "forbidden":
            flags = 0
            anchor_code = 0
            anchor_max = 0
            anchor_state = 0
        else:
            flags = 1
            anchor_code = ANCHOR_CODES[anchor["record_type"]]
            anchor_max = anchor["max_records"]
            anchor_state = strings.offset(anchor["idempotent_state"])
        record = struct.pack(
            ">HHBBBBHHHBBHHIIHH",
            strings.offset(entity["logical_id"]),
            _entity_type_code(entity["type"]),
            ENTITY_CATEGORY_CODES[category],
            flags,
            len(state_names),
            state_names.index(entity["state"]["initial"]),
            entity["x"],
            entity["y"],
            strings.offset(entity["reset_policy"]),
            anchor_code,
            anchor_max,
            anchor_state,
            len(entity["parameters"]),
            states_offset,
            parameters_offset,
            len(parameter_payload),
            strings.offset(entity["type"]),
        )
        if len(record) != ENTITY_RECORD_SIZE:
            raise ContentCompileError("internal entity record size error")
        entity_records.append(record)

    objective_index = {
        node["objective_id"]: index for index, node in enumerate(objectives)
    }
    objective_records: list[bytes] = []
    for node in objectives:
        dependencies = sorted(
            (objective_index[dependency] for dependency in node["depends_on"])
        )
        dependency_payload = b"".join(
            struct.pack(">H", index) for index in dependencies
        )
        dependency_offset = (
            details.append(dependency_payload) if dependency_payload else 0
        )
        record = struct.pack(
            ">HHHHHHI",
            strings.offset(node["objective_id"]),
            strings.offset(node["type"]),
            strings.offset(node["condition"]),
            strings.offset(node["role"]),
            node["deadline_frame"],
            len(dependencies),
            dependency_offset,
        )
        if len(record) != OBJECTIVE_RECORD_SIZE:
            raise ContentCompileError("internal objective record size error")
        objective_records.append(record)

    role_records: list[bytes] = []
    for role in roles:
        actions = sorted(
            role["actions"],
            key=lambda item: (item["frame"], item["action"], item["target"]),
        )
        action_payload = b"".join(
            struct.pack(
                ">HHHH",
                action["frame"],
                strings.offset(action["action"]),
                entity_index[action["target"]],
                0,
            )
            for action in actions
        )
        action_offset = details.append(action_payload)
        record = struct.pack(
            ">HHHHHIH",
            strings.offset(role["role_id"]),
            strings.offset(role["actor"]),
            role["start_frame"],
            role["end_frame"],
            len(actions),
            action_offset,
            0,
        )
        if len(record) != ROLE_RECORD_SIZE:
            raise ContentCompileError("internal role record size error")
        role_records.append(record)

    phase_records: list[bytes] = []
    for phase in phases:
        attacks = sorted(
            phase["attacks"],
            key=lambda item: (
                item["pattern"],
                "frames"
                if "frames" in item
                else "period"
                if "period_frames" in item
                else "source",
            ),
        )
        attacks_offset = details.reserve(len(attacks) * ATTACK_RECORD_SIZE)
        for index, attack in enumerate(attacks):
            if "frames" in attack:
                frames_payload = b"".join(
                    struct.pack(">H", frame) for frame in attack["frames"]
                )
                frame_offset = details.append(frames_payload)
                mode = ATTACK_FRAMES
                count = len(attack["frames"])
                value = 0
                data_offset = frame_offset
            elif "period_frames" in attack:
                mode = ATTACK_PERIOD
                count = 0
                value = attack["period_frames"]
                data_offset = 0
            else:
                mode = ATTACK_SOURCE
                count = 0
                value = strings.offset(attack["source"])
                data_offset = 0
            details.pack_into(
                attacks_offset + index * ATTACK_RECORD_SIZE,
                ">HBBHIH",
                strings.offset(attack["pattern"]),
                mode,
                count,
                value,
                data_offset,
                0,
            )
        record = struct.pack(
            ">HHHHHHHIH",
            strings.offset(phase["phase_id"]),
            strings.offset(phase["entry_condition"]),
            phase["frame_window"][0],
            phase["frame_window"][1],
            strings.offset(phase["required_progress"]),
            strings.offset(phase["exit_condition"]),
            len(attacks),
            attacks_offset,
            0,
        )
        if len(record) != PHASE_RECORD_SIZE:
            raise ContentCompileError("internal phase record size error")
        phase_records.append(record)

    policy_records = [
        struct.pack(
            ">HHHH",
            policy["priority"],
            strings.offset(policy["condition"]),
            strings.offset(policy["response"]),
            0,
        )
        for policy in policies
    ]

    string_offset = _align2(detail_offset + len(details.data))
    string_padding = string_offset - (detail_offset + len(details.data))
    total_size = _align2(string_offset + len(strings.data))
    chunk = bytearray(total_size)
    chunk[collision_offset : collision_offset + len(collision)] = collision
    for index, record in enumerate(entity_records):
        offset = entity_offset + index * ENTITY_RECORD_SIZE
        chunk[offset : offset + ENTITY_RECORD_SIZE] = record
    for index, record in enumerate(objective_records):
        offset = objective_offset + index * OBJECTIVE_RECORD_SIZE
        chunk[offset : offset + OBJECTIVE_RECORD_SIZE] = record
    for index, record in enumerate(role_records):
        offset = role_offset + index * ROLE_RECORD_SIZE
        chunk[offset : offset + ROLE_RECORD_SIZE] = record
    for index, record in enumerate(phase_records):
        offset = phase_offset + index * PHASE_RECORD_SIZE
        chunk[offset : offset + PHASE_RECORD_SIZE] = record
    for index, record in enumerate(policy_records):
        offset = policy_offset + index * POLICY_RECORD_SIZE
        chunk[offset : offset + POLICY_RECORD_SIZE] = record
    chunk[detail_offset : detail_offset + len(details.data)] = details.data
    if string_padding:
        chunk[
            detail_offset + len(details.data) : string_offset
        ] = bytes(string_padding)
    chunk[string_offset : string_offset + len(strings.data)] = strings.data

    canonical = _canonical_json(encounter)
    source_crc = zlib.crc32(canonical) & 0xFFFFFFFF
    flags = CHUNK_FLAG_BOSS if encounter["kind"] == "boss" else 0
    if any(
        entity["anchor_policy"] != "forbidden"
        for _, entity in entities_with_category
    ):
        flags |= CHUNK_FLAG_HAS_ANCHORS
    if policies:
        flags |= CHUNK_FLAG_HAS_RESPONSE_POLICY

    header = bytearray(CHUNK_HEADER_SIZE)
    struct.pack_into(">4sHHII", header, 0, CHUNK_MAGIC, FORMAT_VERSION, CHUNK_HEADER_SIZE, total_size, source_crc)
    struct.pack_into(
        ">HBBBB",
        header,
        16,
        encounter["numeric_id"],
        encounter["campaign_order"],
        SECTOR_CODES[encounter["sector"]["id"]],
        KIND_CODES[encounter["kind"]],
        flags,
    )
    struct.pack_into(
        ">HHBBBB",
        header,
        22,
        encounter["room"]["width_px"],
        encounter["room"]["height_px"],
        encounter["room"]["collision_cell_px"],
        encounter["room"]["visual_metatile_px"],
        len(encounter["room"]["tile_grid"][0]),
        len(encounter["room"]["tile_grid"]),
    )
    struct.pack_into(
        ">HHHH",
        header,
        30,
        encounter["room"]["spawn"]["x"],
        encounter["room"]["spawn"]["y"],
        encounter["room"]["exit"]["x"],
        encounter["room"]["exit"]["y"],
    )
    struct.pack_into(
        ">HHHBBH",
        header,
        38,
        encounter["loop"]["authoritative_frames"],
        encounter["loop"]["clear_window_frames"][0],
        encounter["loop"]["clear_window_frames"][1],
        encounter["echoes"]["cap"],
        encounter["echoes"]["minimum"],
        encounter["presentation"]["tempo_bpm"],
    )
    struct.pack_into(
        ">HHHHHHH",
        header,
        48,
        strings.offset(encounter["presentation"]["palette_id"]),
        strings.offset(encounter["presentation"]["music_id"]),
        strings.offset(encounter["objective_graph"]["completion"]),
        len(entities_with_category),
        len(objectives),
        len(roles),
        len(phases),
    )
    struct.pack_into(
        ">IIIIIIII",
        header,
        62,
        collision_offset,
        len(collision),
        entity_offset,
        objective_offset,
        role_offset,
        phase_offset,
        string_offset,
        len(strings.data),
    )
    struct.pack_into(
        ">" + "H" * len(BUDGET_FIELDS),
        header,
        94,
        *(encounter["budgets"][field] for field in BUDGET_FIELDS),
    )
    struct.pack_into(
        ">HHHHHHIII",
        header,
        120,
        ENTITY_RECORD_SIZE,
        OBJECTIVE_RECORD_SIZE,
        ROLE_RECORD_SIZE,
        PHASE_RECORD_SIZE,
        len(policies),
        POLICY_RECORD_SIZE,
        policy_offset,
        detail_offset,
        0,
    )
    chunk[:CHUNK_HEADER_SIZE] = header
    return ChunkResult(
        encounter=encounter,
        data=bytes(chunk),
        source_crc32=source_crc,
        source_sha256=hashlib.sha256(canonical).hexdigest(),
        collision_bytes=len(collision),
        entity_count=len(entities_with_category),
        objective_count=len(objectives),
        role_count=len(roles),
        phase_count=len(phases),
        policy_count=len(policies),
    )


def _build_binary(
    validated: ValidatedContent,
) -> tuple[bytes, list[dict[str, Any]]]:
    chunks = [_compile_chunk(encounter) for encounter in validated.encounters]
    directory_offset = FILE_HEADER_SIZE
    data_offset = directory_offset + len(chunks) * DIRECTORY_ENTRY_SIZE
    cursor = data_offset
    directory = bytearray()
    report_entries: list[dict[str, Any]] = []
    chunk_bytes = bytearray()
    for roster, result in zip(ROSTER, chunks):
        if cursor % 2:
            raise ContentCompileError("internal unaligned chunk offset")
        chunk_crc = zlib.crc32(result.data) & 0xFFFFFFFF
        flags = result.data[21]
        id_bytes = roster.encounter_id.encode("ascii") + b"\x00"
        directory.extend(
            struct.pack(
                ">4sHBBBBHIIIHHHH",
                id_bytes,
                roster.numeric_id,
                roster.order,
                SECTOR_CODES[roster.sector],
                KIND_CODES[roster.kind],
                flags,
                result.entity_count,
                cursor,
                len(result.data),
                chunk_crc,
                result.objective_count,
                result.role_count,
                result.phase_count,
                result.policy_count,
            )
        )
        chunk_bytes.extend(result.data)
        report_entries.append(
            {
                "id": roster.encounter_id,
                "numeric_id": roster.numeric_id,
                "order": roster.order,
                "sector": roster.sector,
                "kind": roster.kind,
                "offset": cursor,
                "bytes": len(result.data),
                "crc32": f"{chunk_crc:08x}",
                "source_crc32": f"{result.source_crc32:08x}",
                "source_sha256": result.source_sha256,
                "collision_bytes": result.collision_bytes,
                "entities": result.entity_count,
                "objectives": result.objective_count,
                "roles": result.role_count,
                "boss_phases": result.phase_count,
                "response_policies": result.policy_count,
                "structured_runtime": (
                    {
                        "stage_completion": result.encounter[
                            "stage_completion"
                        ],
                        "dependency_groups": len(
                            result.encounter["dependency_policy"]
                        ),
                    }
                    if roster.encounter_id in STRUCTURED_RUNTIME_ENCOUNTERS
                    else None
                ),
                "budgets": {
                    field: result.encounter["budgets"][field]
                    for field in BUDGET_FIELDS
                },
            }
        )
        cursor += len(result.data)
    if len(directory) != len(chunks) * DIRECTORY_ENTRY_SIZE:
        raise ContentCompileError("internal directory size error")
    file_size = FILE_HEADER_SIZE + len(directory) + len(chunk_bytes)
    payload = bytes(directory + chunk_bytes)
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = struct.pack(
        ">4sHHHHIIIII",
        FILE_MAGIC,
        FORMAT_VERSION,
        FILE_HEADER_SIZE,
        len(chunks),
        DIRECTORY_ENTRY_SIZE,
        directory_offset,
        data_offset,
        file_size,
        payload_crc,
        FILE_FLAG_COLLISION_ONE_IS_SOLID,
    )
    binary = header + payload
    if len(binary) != file_size:
        raise ContentCompileError("internal file size error")
    verify_binary(binary)
    return binary, report_entries


def _assembly_source(
    binary: bytes, report_entries: Sequence[Mapping[str, Any]]
) -> bytes:
    binary_sha = hashlib.sha256(binary).hexdigest()
    lines = [
        "; Generated by tools.content_compiler. Do not edit.",
        f"; campaign_content.bin sha256: {binary_sha}",
        "",
        f"CONTENT_FORMAT_VERSION equ ${FORMAT_VERSION:04X}",
        f"CONTENT_ENCOUNTER_COUNT equ {len(ROSTER)}",
        f"CONTENT_FILE_HEADER_SIZE equ {FILE_HEADER_SIZE}",
        f"CONTENT_DIRECTORY_ENTRY_SIZE equ {DIRECTORY_ENTRY_SIZE}",
        f"CONTENT_CHUNK_HEADER_SIZE equ {CHUNK_HEADER_SIZE}",
        f"CONTENT_ENTITY_RECORD_SIZE equ {ENTITY_RECORD_SIZE}",
        f"CONTENT_OBJECTIVE_RECORD_SIZE equ {OBJECTIVE_RECORD_SIZE}",
        f"CONTENT_ROLE_RECORD_SIZE equ {ROLE_RECORD_SIZE}",
        f"CONTENT_ACTION_RECORD_SIZE equ {ACTION_RECORD_SIZE}",
        f"CONTENT_PHASE_RECORD_SIZE equ {PHASE_RECORD_SIZE}",
        f"CONTENT_ATTACK_RECORD_SIZE equ {ATTACK_RECORD_SIZE}",
        f"CONTENT_POLICY_RECORD_SIZE equ {POLICY_RECORD_SIZE}",
        f"CONTENT_BINARY_SIZE equ {len(binary)}",
        "",
        "CONTENT_FILE_MAGIC_OFFSET equ 0",
        "CONTENT_FILE_VERSION_OFFSET equ 4",
        "CONTENT_FILE_HEADER_SIZE_OFFSET equ 6",
        "CONTENT_FILE_ENCOUNTER_COUNT_OFFSET equ 8",
        "CONTENT_FILE_DIRECTORY_ENTRY_SIZE_OFFSET equ 10",
        "CONTENT_FILE_DIRECTORY_OFFSET equ 12",
        "CONTENT_FILE_DATA_OFFSET equ 16",
        "CONTENT_FILE_SIZE_OFFSET equ 20",
        "CONTENT_FILE_PAYLOAD_CRC32_OFFSET equ 24",
        "CONTENT_FILE_FLAGS_OFFSET equ 28",
        "CONTENT_DIR_ID_OFFSET equ 0",
        "CONTENT_DIR_NUMERIC_ID_OFFSET equ 4",
        "CONTENT_DIR_ORDER_OFFSET equ 6",
        "CONTENT_DIR_SECTOR_OFFSET equ 7",
        "CONTENT_DIR_KIND_OFFSET equ 8",
        "CONTENT_DIR_FLAGS_OFFSET equ 9",
        "CONTENT_DIR_ENTITY_COUNT_OFFSET equ 10",
        "CONTENT_DIR_CHUNK_OFFSET equ 12",
        "CONTENT_DIR_CHUNK_SIZE_OFFSET equ 16",
        "CONTENT_DIR_CHUNK_CRC32_OFFSET equ 20",
        "CONTENT_DIR_OBJECTIVE_COUNT_OFFSET equ 24",
        "CONTENT_DIR_ROLE_COUNT_OFFSET equ 26",
        "CONTENT_DIR_PHASE_COUNT_OFFSET equ 28",
        "CONTENT_DIR_POLICY_COUNT_OFFSET equ 30",
        "CONTENT_CHUNK_MAGIC_OFFSET equ 0",
        "CONTENT_CHUNK_VERSION_OFFSET equ 4",
        "CONTENT_CHUNK_HEADER_SIZE_OFFSET equ 6",
        "CONTENT_CHUNK_TOTAL_SIZE_OFFSET equ 8",
        "CONTENT_CHUNK_SOURCE_CRC32_OFFSET equ 12",
        "CONTENT_CHUNK_NUMERIC_ID_OFFSET equ 16",
        "CONTENT_CHUNK_ORDER_OFFSET equ 18",
        "CONTENT_CHUNK_SECTOR_OFFSET equ 19",
        "CONTENT_CHUNK_KIND_OFFSET equ 20",
        "CONTENT_CHUNK_FLAGS_OFFSET equ 21",
        "CONTENT_CHUNK_WIDTH_OFFSET equ 22",
        "CONTENT_CHUNK_HEIGHT_OFFSET equ 24",
        "CONTENT_CHUNK_COLLISION_CELL_OFFSET equ 26",
        "CONTENT_CHUNK_METATILE_OFFSET equ 27",
        "CONTENT_CHUNK_GRID_COLUMNS_OFFSET equ 28",
        "CONTENT_CHUNK_GRID_ROWS_OFFSET equ 29",
        "CONTENT_CHUNK_SPAWN_X_OFFSET equ 30",
        "CONTENT_CHUNK_SPAWN_Y_OFFSET equ 32",
        "CONTENT_CHUNK_EXIT_X_OFFSET equ 34",
        "CONTENT_CHUNK_EXIT_Y_OFFSET equ 36",
        "CONTENT_CHUNK_LOOP_FRAMES_OFFSET equ 38",
        "CONTENT_CHUNK_CLEAR_START_OFFSET equ 40",
        "CONTENT_CHUNK_CLEAR_END_OFFSET equ 42",
        "CONTENT_CHUNK_ECHO_CAP_OFFSET equ 44",
        "CONTENT_CHUNK_ECHO_MINIMUM_OFFSET equ 45",
        "CONTENT_CHUNK_TEMPO_BPM_OFFSET equ 46",
        "CONTENT_CHUNK_PALETTE_STRING_OFFSET equ 48",
        "CONTENT_CHUNK_MUSIC_STRING_OFFSET equ 50",
        "CONTENT_CHUNK_COMPLETION_STRING_OFFSET equ 52",
        "CONTENT_CHUNK_ENTITY_COUNT_OFFSET equ 54",
        "CONTENT_CHUNK_OBJECTIVE_COUNT_OFFSET equ 56",
        "CONTENT_CHUNK_ROLE_COUNT_OFFSET equ 58",
        "CONTENT_CHUNK_PHASE_COUNT_OFFSET equ 60",
        "CONTENT_CHUNK_COLLISION_OFFSET_OFFSET equ 62",
        "CONTENT_CHUNK_COLLISION_SIZE_OFFSET equ 66",
        "CONTENT_CHUNK_ENTITY_OFFSET_OFFSET equ 70",
        "CONTENT_CHUNK_OBJECTIVE_OFFSET_OFFSET equ 74",
        "CONTENT_CHUNK_ROLE_OFFSET_OFFSET equ 78",
        "CONTENT_CHUNK_PHASE_OFFSET_OFFSET equ 82",
        "CONTENT_CHUNK_STRING_OFFSET_OFFSET equ 86",
        "CONTENT_CHUNK_STRING_SIZE_OFFSET equ 90",
        "CONTENT_CHUNK_BUDGETS_OFFSET equ 94",
        "CONTENT_CHUNK_ENTITY_RECORD_SIZE_OFFSET equ 120",
        "CONTENT_CHUNK_OBJECTIVE_RECORD_SIZE_OFFSET equ 122",
        "CONTENT_CHUNK_ROLE_RECORD_SIZE_OFFSET equ 124",
        "CONTENT_CHUNK_PHASE_RECORD_SIZE_OFFSET equ 126",
        "CONTENT_CHUNK_POLICY_COUNT_OFFSET equ 128",
        "CONTENT_CHUNK_POLICY_RECORD_SIZE_OFFSET equ 130",
        "CONTENT_CHUNK_POLICY_OFFSET_OFFSET equ 132",
        "CONTENT_CHUNK_DETAIL_OFFSET_OFFSET equ 136",
        "",
        "CONTENT_ENTITY_LOGICAL_ID_OFFSET equ 0",
        "CONTENT_ENTITY_TYPE_CODE_OFFSET equ 2",
        "CONTENT_ENTITY_CATEGORY_OFFSET equ 4",
        "CONTENT_ENTITY_FLAGS_OFFSET equ 5",
        "CONTENT_ENTITY_STATE_COUNT_OFFSET equ 6",
        "CONTENT_ENTITY_INITIAL_STATE_OFFSET equ 7",
        "CONTENT_ENTITY_X_OFFSET equ 8",
        "CONTENT_ENTITY_Y_OFFSET equ 10",
        "CONTENT_ENTITY_RESET_STRING_OFFSET equ 12",
        "CONTENT_ENTITY_ANCHOR_TYPE_OFFSET equ 14",
        "CONTENT_ENTITY_ANCHOR_MAX_OFFSET equ 15",
        "CONTENT_ENTITY_ANCHOR_STATE_OFFSET equ 16",
        "CONTENT_ENTITY_PARAMETER_COUNT_OFFSET equ 18",
        "CONTENT_ENTITY_STATES_OFFSET_OFFSET equ 20",
        "CONTENT_ENTITY_PARAMETERS_OFFSET_OFFSET equ 24",
        "CONTENT_ENTITY_PARAMETERS_SIZE_OFFSET equ 28",
        "CONTENT_ENTITY_TYPE_STRING_OFFSET equ 30",
        "CONTENT_OBJECTIVE_ID_OFFSET equ 0",
        "CONTENT_OBJECTIVE_TYPE_OFFSET equ 2",
        "CONTENT_OBJECTIVE_CONDITION_OFFSET equ 4",
        "CONTENT_OBJECTIVE_ROLE_OFFSET equ 6",
        "CONTENT_OBJECTIVE_DEADLINE_OFFSET equ 8",
        "CONTENT_OBJECTIVE_DEPENDENCY_COUNT_OFFSET equ 10",
        "CONTENT_OBJECTIVE_DEPENDENCIES_OFFSET_OFFSET equ 12",
        "CONTENT_ROLE_ID_OFFSET equ 0",
        "CONTENT_ROLE_ACTOR_OFFSET equ 2",
        "CONTENT_ROLE_START_OFFSET equ 4",
        "CONTENT_ROLE_END_OFFSET equ 6",
        "CONTENT_ROLE_ACTION_COUNT_OFFSET equ 8",
        "CONTENT_ROLE_ACTIONS_OFFSET_OFFSET equ 10",
        "CONTENT_ACTION_FRAME_OFFSET equ 0",
        "CONTENT_ACTION_NAME_OFFSET equ 2",
        "CONTENT_ACTION_TARGET_ENTITY_INDEX_OFFSET equ 4",
        "CONTENT_PHASE_ID_OFFSET equ 0",
        "CONTENT_PHASE_ENTRY_OFFSET equ 2",
        "CONTENT_PHASE_START_OFFSET equ 4",
        "CONTENT_PHASE_END_OFFSET equ 6",
        "CONTENT_PHASE_PROGRESS_OFFSET equ 8",
        "CONTENT_PHASE_EXIT_OFFSET equ 10",
        "CONTENT_PHASE_ATTACK_COUNT_OFFSET equ 12",
        "CONTENT_PHASE_ATTACKS_OFFSET_OFFSET equ 14",
        "CONTENT_ATTACK_PATTERN_OFFSET equ 0",
        "CONTENT_ATTACK_MODE_OFFSET equ 2",
        "CONTENT_ATTACK_VALUE_COUNT_OFFSET equ 3",
        "CONTENT_ATTACK_VALUE_OFFSET equ 4",
        "CONTENT_ATTACK_DATA_OFFSET_OFFSET equ 6",
        "CONTENT_POLICY_PRIORITY_OFFSET equ 0",
        "CONTENT_POLICY_CONDITION_OFFSET equ 2",
        "CONTENT_POLICY_RESPONSE_OFFSET equ 4",
        "",
        "CONTENT_KIND_PRIMARY equ 0",
        "CONTENT_KIND_BOSS equ 1",
        f"CONTENT_FILE_FLAG_COLLISION_ONE_IS_SOLID equ ${FILE_FLAG_COLLISION_ONE_IS_SOLID:08X}",
        f"CONTENT_CHUNK_FLAG_BOSS equ {CHUNK_FLAG_BOSS}",
        f"CONTENT_CHUNK_FLAG_HAS_ANCHORS equ {CHUNK_FLAG_HAS_ANCHORS}",
        f"CONTENT_CHUNK_FLAG_HAS_RESPONSE_POLICY equ {CHUNK_FLAG_HAS_RESPONSE_POLICY}",
    ]
    for budget_index, budget_name in enumerate(BUDGET_FIELDS):
        lines.append(
            f"CONTENT_CHUNK_BUDGET_{budget_name.upper()}_OFFSET equ "
            f"{94 + budget_index * 2}"
        )
    for name, code in SECTOR_CODES.items():
        lines.append(f"CONTENT_SECTOR_{name} equ {code}")
    lines.append("")
    for name, code in ENTITY_CATEGORY_CODES.items():
        symbol = name.upper()
        lines.append(f"CONTENT_ENTITY_CATEGORY_{symbol} equ {code}")
    lines.extend(
        (
            "",
            "CONTENT_ANCHOR_FORBIDDEN equ 0",
        )
    )
    for name, code in ANCHOR_CODES.items():
        lines.append(f"CONTENT_ANCHOR_{name.upper()} equ {code}")
    lines.extend(
        (
            "",
            f"CONTENT_PARAM_UINT equ {PARAM_UINT}",
            f"CONTENT_PARAM_FALSE equ {PARAM_FALSE}",
            f"CONTENT_PARAM_TRUE equ {PARAM_TRUE}",
            f"CONTENT_PARAM_STRING equ {PARAM_STRING}",
            f"CONTENT_PARAM_LIST equ {PARAM_LIST}",
            f"CONTENT_ATTACK_FRAMES equ {ATTACK_FRAMES}",
            f"CONTENT_ATTACK_PERIOD equ {ATTACK_PERIOD}",
            f"CONTENT_ATTACK_SOURCE equ {ATTACK_SOURCE}",
            "",
        )
    )
    for entity_type in sorted(ALL_ENTITY_TYPES):
        symbol = re.sub(r"[^A-Z0-9]+", "_", entity_type.upper()).strip("_")
        lines.append(
            f"CONTENT_ENTITY_TYPE_{symbol} equ ${_entity_type_code(entity_type):04X}"
        )
    lines.append("")
    for item in report_entries:
        prefix = f"CONTENT_ENCOUNTER_{item['id']}"
        lines.extend(
            (
                f"{prefix}_DIRECTORY_INDEX equ {item['order'] - 1}",
                f"{prefix}_NUMERIC_ID equ {item['numeric_id']}",
                f"{prefix}_CHUNK_OFFSET equ ${item['offset']:08X}",
                f"{prefix}_CHUNK_SIZE equ {item['bytes']}",
                f"{prefix}_ENTITY_COUNT equ {item['entities']}",
                f"{prefix}_OBJECTIVE_COUNT equ {item['objectives']}",
                f"{prefix}_ROLE_COUNT equ {item['roles']}",
                f"{prefix}_PHASE_COUNT equ {item['boss_phases']}",
            )
        )
    lines.extend(
        (
            "",
            "    even",
            "ContentCampaignBinary:",
            f'    incbin "assets/generated/content/{OUTPUT_BINARY}"',
            "ContentCampaignBinaryEnd:",
            "",
        )
    )
    return "\n".join(lines).encode("ascii")


def build_artifacts(content_root: Path | str) -> ArtifactBundle:
    """Validate sources and build every deterministic output in memory."""

    validated = load_and_validate(content_root)
    binary, report_entries = _build_binary(validated)
    assembly = _assembly_source(binary, report_entries)
    binary_sha = hashlib.sha256(binary).hexdigest()
    assembly_sha = hashlib.sha256(assembly).hexdigest()
    report = {
        "schema": "recursion94.content_compile_report",
        "schema_version": 1,
        "format": {
            "magic": FILE_MAGIC.decode("ascii"),
            "version": FORMAT_VERSION,
            "byte_order": "big",
            "file_header_bytes": FILE_HEADER_SIZE,
            "directory_entry_bytes": DIRECTORY_ENTRY_SIZE,
            "encounter_header_bytes": CHUNK_HEADER_SIZE,
            "entity_record_bytes": ENTITY_RECORD_SIZE,
            "objective_record_bytes": OBJECTIVE_RECORD_SIZE,
            "role_record_bytes": ROLE_RECORD_SIZE,
            "action_record_bytes": ACTION_RECORD_SIZE,
            "phase_record_bytes": PHASE_RECORD_SIZE,
            "attack_record_bytes": ATTACK_RECORD_SIZE,
            "policy_record_bytes": POLICY_RECORD_SIZE,
            "collision_encoding": "row-major, MSB first, 1=solid, 0=walkable",
        },
        "source": {
            "campaign": "content/campaign.json",
            "encounter_manifests": 20,
            "manifest_files": 21,
            "canonical_sha256": validated.source_sha256,
        },
        "composition": {
            "encounters": 20,
            "primary": 15,
            "bosses": 5,
            "campaign_start": "B01",
            "campaign_terminal": "I04",
            "reachable_encounters": 20,
        },
        "outputs": {
            "binary": {
                "file": OUTPUT_BINARY,
                "bytes": len(binary),
                "sha256": binary_sha,
                "payload_crc32": f"{struct.unpack_from('>I', binary, 24)[0]:08x}",
            },
            "assembly": {
                "file": OUTPUT_ASSEMBLY,
                "bytes": len(assembly),
                "sha256": assembly_sha,
            },
        },
        "encounters": report_entries,
        "runtime_contract": {
            "authoritative_loop_frames": 720,
            "all_offsets_relative_to": "start of campaign binary or encounter chunk as documented",
            "all_fixed_width_integers": "unsigned big-endian",
            "all_sections_word_aligned": True,
            "collision_grid_cells": [20, 14],
            "replay_bytes_embedded": False,
            "replay_outcomes_embedded": False,
        },
    }
    report_bytes = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    checksums = (
        f"{binary_sha}  {OUTPUT_BINARY}\n"
        f"{assembly_sha}  {OUTPUT_ASSEMBLY}\n"
        f"{report_sha}  {OUTPUT_REPORT}\n"
    ).encode("ascii")
    files = {
        OUTPUT_BINARY: binary,
        OUTPUT_ASSEMBLY: assembly,
        OUTPUT_REPORT: report_bytes,
        OUTPUT_CHECKSUMS: checksums,
    }
    return ArtifactBundle(files, binary_sha, validated.source_sha256, len(ROSTER))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def compile_content(
    content_root: Path | str, output_dir: Path | str
) -> ArtifactBundle:
    """Validate, compile, verify, and atomically write generated artifacts."""

    bundle = build_artifacts(content_root)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(
        path.name
        for path in destination.iterdir()
        if path.name not in OUTPUT_NAMES or not path.is_file()
    )
    if unexpected:
        _error(str(destination), f"unexpected generated entries {unexpected}")
    for name in sorted(bundle.files):
        _write_atomic(destination / name, bundle.files[name])
    return bundle


def check_generated(content_root: Path | str, output_dir: Path | str) -> ArtifactBundle:
    """Rebuild in memory and require byte identity with the generated directory."""

    bundle = build_artifacts(content_root)
    destination = Path(output_dir)
    if not destination.is_dir():
        _error(str(destination), "generated output directory does not exist")
    actual_names = {path.name for path in destination.iterdir()}
    if actual_names != OUTPUT_NAMES:
        _error(
            str(destination),
            f"generated file set mismatch; expected={sorted(OUTPUT_NAMES)}, "
            f"actual={sorted(actual_names)}",
        )
    for name, expected in bundle.files.items():
        actual = (destination / name).read_bytes()
        if actual != expected:
            _error(str(destination / name), "generated bytes are stale or corrupted")
    return bundle


def verify_binary(binary: bytes) -> None:
    """Fail closed on structural or checksum corruption in a compiled binary."""

    if len(binary) < FILE_HEADER_SIZE:
        _error("<binary>", "shorter than file header")
    try:
        (
            magic,
            version,
            header_size,
            encounter_count,
            directory_entry_size,
            directory_offset,
            data_offset,
            file_size,
            payload_crc,
            flags,
        ) = struct.unpack_from(">4sHHHHIIIII", binary, 0)
    except struct.error as exc:
        _error("<binary>", f"cannot unpack file header: {exc}")
    if magic != FILE_MAGIC:
        _error("<binary>.magic", "invalid file magic")
    if version != FORMAT_VERSION:
        _error("<binary>.version", f"unsupported format {version}")
    if (
        header_size != FILE_HEADER_SIZE
        or directory_entry_size != DIRECTORY_ENTRY_SIZE
        or encounter_count != len(ROSTER)
    ):
        _error("<binary>", "invalid fixed file/header/count sizes")
    if directory_offset != FILE_HEADER_SIZE:
        _error("<binary>.directory_offset", "directory must follow header")
    expected_data_offset = directory_offset + encounter_count * directory_entry_size
    if data_offset != expected_data_offset:
        _error("<binary>.data_offset", "invalid directory extent")
    if file_size != len(binary):
        _error("<binary>.file_size", "does not equal byte length")
    if flags != FILE_FLAG_COLLISION_ONE_IS_SOLID:
        _error("<binary>.flags", "unknown file flags")
    if zlib.crc32(binary[directory_offset:]) & 0xFFFFFFFF != payload_crc:
        _error("<binary>.payload_crc32", "checksum mismatch")

    previous_end = data_offset
    for index, roster in enumerate(ROSTER):
        entry_offset = directory_offset + index * DIRECTORY_ENTRY_SIZE
        try:
            (
                raw_id,
                numeric_id,
                order,
                sector,
                kind,
                chunk_flags,
                entity_count,
                chunk_offset,
                chunk_size,
                chunk_crc,
                objective_count,
                role_count,
                phase_count,
                policy_count,
            ) = struct.unpack_from(">4sHBBBBHIIIHHHH", binary, entry_offset)
        except struct.error as exc:
            _error(f"<binary>.directory[{index}]", f"cannot unpack: {exc}")
        expected_id = roster.encounter_id.encode("ascii") + b"\x00"
        if raw_id != expected_id:
            _error(f"<binary>.directory[{index}].id", "roster mismatch")
        if (
            numeric_id != roster.numeric_id
            or order != roster.order
            or sector != SECTOR_CODES[roster.sector]
            or kind != KIND_CODES[roster.kind]
        ):
            _error(f"<binary>.directory[{index}]", "frozen roster fields mismatch")
        if chunk_offset != previous_end or chunk_offset % 2 or chunk_size % 2:
            _error(
                f"<binary>.directory[{index}]",
                "chunks must be contiguous and word aligned",
            )
        chunk_end = chunk_offset + chunk_size
        if chunk_offset < data_offset or chunk_end > len(binary):
            _error(f"<binary>.directory[{index}]", "chunk outside file")
        chunk = binary[chunk_offset:chunk_end]
        if zlib.crc32(chunk) & 0xFFFFFFFF != chunk_crc:
            _error(f"<binary>.directory[{index}].crc32", "chunk checksum mismatch")
        if len(chunk) < CHUNK_HEADER_SIZE:
            _error(f"<binary>.directory[{index}]", "short chunk header")
        (
            chunk_magic,
            chunk_version,
            chunk_header_size,
            chunk_total_size,
            _source_crc,
        ) = struct.unpack_from(">4sHHII", chunk, 0)
        if (
            chunk_magic != CHUNK_MAGIC
            or chunk_version != FORMAT_VERSION
            or chunk_header_size != CHUNK_HEADER_SIZE
            or chunk_total_size != chunk_size
        ):
            _error(f"<binary>.directory[{index}]", "invalid encounter header")
        (
            chunk_numeric_id,
            chunk_order,
            chunk_sector,
            chunk_kind,
            header_flags,
        ) = struct.unpack_from(">HBBBB", chunk, 16)
        if (
            chunk_numeric_id != numeric_id
            or chunk_order != order
            or chunk_sector != sector
            or chunk_kind != kind
            or header_flags != chunk_flags
        ):
            _error(f"<binary>.directory[{index}]", "directory/chunk mismatch")
        if chunk_flags & ~(
            CHUNK_FLAG_BOSS
            | CHUNK_FLAG_HAS_ANCHORS
            | CHUNK_FLAG_HAS_RESPONSE_POLICY
        ):
            _error(f"<binary>.directory[{index}].flags", "unknown chunk flags")
        if bool(chunk_flags & CHUNK_FLAG_BOSS) != (kind == KIND_CODES["boss"]):
            _error(f"<binary>.directory[{index}].flags", "boss flag mismatch")
        width, height, cell, metatile, cols, rows = struct.unpack_from(
            ">HHBBBB", chunk, 22
        )
        if (width, height, cell, metatile, cols, rows) != (320, 224, 8, 16, 20, 14):
            _error(f"<binary>.directory[{index}]", "invalid room geometry")
        spawn_x, spawn_y, exit_x, exit_y = struct.unpack_from(">HHHH", chunk, 30)
        if (
            spawn_x > 319
            or exit_x > 319
            or spawn_y > 223
            or exit_y > 223
            or spawn_x % 8
            or spawn_y % 8
            or exit_x % 8
            or exit_y % 8
        ):
            _error(f"<binary>.directory[{index}]", "invalid spawn/exit coordinates")
        loop_frames, clear_start, clear_end, echo_cap, echo_min, tempo = (
            struct.unpack_from(">HHHBBH", chunk, 38)
        )
        sector_spec = SECTOR_SPEC_BY_ID[roster.sector]
        if (
            loop_frames != 720
            or clear_start > clear_end
            or clear_end > 719
            or echo_cap != sector_spec["echo_cap"]
            or not 1 <= echo_min <= echo_cap
            or tempo != sector_spec["tempo_bpm"]
        ):
            _error(f"<binary>.directory[{index}]", "loop/echo invariant mismatch")
        header_counts = struct.unpack_from(">HHHH", chunk, 54)
        if header_counts != (entity_count, objective_count, role_count, phase_count):
            _error(f"<binary>.directory[{index}]", "record count mismatch")
        (
            collision_offset,
            collision_size,
            entity_offset,
            objective_offset,
            role_offset,
            phase_offset,
            string_offset,
            string_size,
        ) = struct.unpack_from(">IIIIIIII", chunk, 62)
        expected_collision_size = (cols * rows + 7) // 8
        if collision_size != expected_collision_size:
            _error(f"<binary>.directory[{index}]", "invalid collision byte count")
        (
            entity_record_size,
            objective_record_size,
            role_record_size,
            phase_record_size,
            header_policy_count,
            policy_record_size,
            policy_offset,
            detail_offset,
            reserved,
        ) = struct.unpack_from(">HHHHHHIII", chunk, 120)
        if (
            entity_record_size,
            objective_record_size,
            role_record_size,
            phase_record_size,
            policy_record_size,
        ) != (
            ENTITY_RECORD_SIZE,
            OBJECTIVE_RECORD_SIZE,
            ROLE_RECORD_SIZE,
            PHASE_RECORD_SIZE,
            POLICY_RECORD_SIZE,
        ):
            _error(f"<binary>.directory[{index}]", "record size mismatch")
        if header_policy_count != policy_count or reserved != 0:
            _error(f"<binary>.directory[{index}]", "policy/reserved mismatch")
        expected_offsets = (
            CHUNK_HEADER_SIZE,
            _align2(CHUNK_HEADER_SIZE + collision_size),
        )
        if collision_offset != expected_offsets[0] or entity_offset != expected_offsets[1]:
            _error(f"<binary>.directory[{index}]", "invalid leading section offsets")
        if objective_offset != entity_offset + entity_count * ENTITY_RECORD_SIZE:
            _error(f"<binary>.directory[{index}]", "invalid objective offset")
        if role_offset != objective_offset + objective_count * OBJECTIVE_RECORD_SIZE:
            _error(f"<binary>.directory[{index}]", "invalid role offset")
        if phase_offset != role_offset + role_count * ROLE_RECORD_SIZE:
            _error(f"<binary>.directory[{index}]", "invalid phase offset")
        if policy_offset != phase_offset + phase_count * PHASE_RECORD_SIZE:
            _error(f"<binary>.directory[{index}]", "invalid policy offset")
        if detail_offset != _align2(policy_offset + policy_count * POLICY_RECORD_SIZE):
            _error(f"<binary>.directory[{index}]", "invalid detail offset")
        if (
            string_offset < detail_offset
            or string_offset % 2
            or string_offset + string_size > chunk_size
            or not string_size
            or chunk[string_offset] != 0
        ):
            _error(f"<binary>.directory[{index}]", "invalid string table")
        string_end = string_offset + string_size
        if chunk_size - string_end not in (0, 1) or any(chunk[string_end:]):
            _error(f"<binary>.directory[{index}]", "invalid trailing alignment bytes")
        string_data = chunk[string_offset:string_end]

        def local_string(
            string_reference: int, field: str, *, allow_zero: bool = False
        ) -> str | None:
            if string_reference == 0:
                if allow_zero:
                    return None
                _error(field, "required string reference is zero")
            if string_reference >= len(string_data):
                _error(field, "string reference outside local table")
            if string_reference > 1 and string_data[string_reference - 1] != 0:
                _error(field, "string reference does not start at a string boundary")
            terminator = string_data.find(b"\x00", string_reference)
            if terminator < 0:
                _error(field, "unterminated local string")
            encoded = string_data[string_reference:terminator]
            if not encoded:
                _error(field, "empty local string")
            try:
                return encoded.decode("utf-8")
            except UnicodeDecodeError:
                _error(field, "invalid UTF-8 local string")

        def detail_range(offset: int, size: int, field: str) -> None:
            if (
                offset < detail_offset
                or size < 0
                or offset + size < offset
                or offset + size > string_offset
            ):
                _error(field, "variable record points outside detail section")

        palette_ref, music_ref, completion_ref = struct.unpack_from(">HHH", chunk, 48)
        local_string(palette_ref, f"<binary>.directory[{index}].palette")
        local_string(music_ref, f"<binary>.directory[{index}].music")
        local_string(completion_ref, f"<binary>.directory[{index}].completion")

        def parse_parameter_value(
            cursor: int, limit: int, field: str, depth: int = 0
        ) -> int:
            if cursor >= limit:
                _error(field, "truncated tagged parameter")
            tag = chunk[cursor]
            cursor += 1
            if tag == PARAM_UINT:
                if cursor + 4 > limit:
                    _error(field, "truncated integer parameter")
                value = struct.unpack_from(">I", chunk, cursor)[0]
                if value > 0xFFFF:
                    _error(field, "parameter integer exceeds bounded u16 source range")
                return cursor + 4
            if tag in (PARAM_FALSE, PARAM_TRUE):
                return cursor
            if tag == PARAM_STRING:
                if cursor + 2 > limit:
                    _error(field, "truncated string parameter")
                reference = struct.unpack_from(">H", chunk, cursor)[0]
                local_string(reference, field)
                return cursor + 2
            if tag == PARAM_LIST:
                if depth >= 2 or cursor + 2 > limit:
                    _error(field, "invalid parameter list nesting")
                count = struct.unpack_from(">H", chunk, cursor)[0]
                cursor += 2
                if not 1 <= count <= 255:
                    _error(field, "parameter list count outside 1..255")
                for child_index in range(count):
                    cursor = parse_parameter_value(
                        cursor,
                        limit,
                        f"{field}[{child_index}]",
                        depth + 1,
                    )
                return cursor
            _error(field, f"unknown parameter tag {tag}")

        category_by_code = {
            code: category for category, code in ENTITY_CATEGORY_CODES.items()
        }
        category_counts = {category: 0 for category in ENTITY_CATEGORIES}
        compiled_has_anchors = False
        for entity_index in range(entity_count):
            record_offset = entity_offset + entity_index * ENTITY_RECORD_SIZE
            (
                logical_ref,
                type_code,
                category_code,
                entity_flags,
                state_count,
                initial_state,
                x,
                y,
                reset_ref,
                anchor_code,
                anchor_max,
                anchor_state_ref,
                parameter_count,
                states_offset,
                parameters_offset,
                parameters_size,
                type_ref,
            ) = struct.unpack_from(">HHBBBBHHHBBHHIIHH", chunk, record_offset)
            field = f"<binary>.directory[{index}].entities[{entity_index}]"
            logical_id = local_string(logical_ref, f"{field}.logical_id")
            entity_type = local_string(type_ref, f"{field}.type")
            if (
                logical_id is None
                or not STABLE_ID_RE.fullmatch(logical_id)
                or not logical_id.startswith(f"{roster.encounter_id}_")
            ):
                _error(f"{field}.logical_id", "invalid compiled stable ID")
            if entity_type not in ALL_ENTITY_TYPES:
                _error(f"{field}.type", "unknown compiled entity type")
            if type_code != _entity_type_code(entity_type):
                _error(f"{field}.type_code", "does not match entity type string")
            if category_code not in category_by_code:
                _error(f"{field}.category", "unknown entity category")
            category = category_by_code[category_code]
            if entity_type not in ENTITY_TYPES_BY_CATEGORY[category]:
                _error(f"{field}.type", "compiled type/category mismatch")
            category_counts[category] += 1
            if entity_flags & ~1:
                _error(f"{field}.flags", "unknown entity flags")
            if not state_count or initial_state >= state_count:
                _error(f"{field}.states", "invalid state count or initial index")
            if x > 319 or y > 223 or x % 8 or y % 8:
                _error(f"{field}.coordinates", "outside aligned room bounds")
            local_string(reset_ref, f"{field}.reset_policy")
            detail_range(states_offset, state_count * 2, f"{field}.states")
            for state_index in range(state_count):
                state_ref = struct.unpack_from(
                    ">H", chunk, states_offset + state_index * 2
                )[0]
                local_string(state_ref, f"{field}.states[{state_index}]")
            if entity_flags & 1:
                compiled_has_anchors = True
                if anchor_code not in ANCHOR_CODES.values() or anchor_max != 1:
                    _error(f"{field}.anchor", "invalid bounded typed anchor")
                local_string(anchor_state_ref, f"{field}.anchor_state")
            elif anchor_code or anchor_max or anchor_state_ref:
                _error(f"{field}.anchor", "forbidden anchor has nonzero fields")
            if not parameter_count or parameters_size < 2:
                _error(f"{field}.parameters", "invalid parameter count/size")
            detail_range(
                parameters_offset, parameters_size, f"{field}.parameters"
            )
            parameter_limit = parameters_offset + parameters_size
            encoded_count = struct.unpack_from(">H", chunk, parameters_offset)[0]
            if encoded_count != parameter_count:
                _error(f"{field}.parameters", "parameter count mismatch")
            parameter_cursor = parameters_offset + 2
            prior_key: bytes | None = None
            parameter_keys: set[str] = set()
            for parameter_index in range(parameter_count):
                if parameter_cursor + 2 > parameter_limit:
                    _error(f"{field}.parameters", "truncated parameter key")
                key_ref = struct.unpack_from(">H", chunk, parameter_cursor)[0]
                parameter_cursor += 2
                key = local_string(
                    key_ref, f"{field}.parameters[{parameter_index}].key"
                )
                if key is None or key not in ALL_PARAMETER_KEYS:
                    _error(f"{field}.parameters", "unknown compiled parameter key")
                if key in parameter_keys:
                    _error(f"{field}.parameters", "duplicate parameter key")
                parameter_keys.add(key)
                encoded_key = key.encode("utf-8")
                if prior_key is not None and encoded_key <= prior_key:
                    _error(f"{field}.parameters", "parameter keys are not sorted")
                prior_key = encoded_key
                parameter_cursor = parse_parameter_value(
                    parameter_cursor,
                    parameter_limit,
                    f"{field}.parameters.{key}",
                )
            if parameter_cursor != parameter_limit:
                _error(f"{field}.parameters", "unreferenced parameter bytes")

        objective_dependencies: list[list[int]] = []
        for objective_index in range(objective_count):
            record_offset = objective_offset + objective_index * OBJECTIVE_RECORD_SIZE
            (
                objective_ref,
                objective_type_ref,
                condition_ref,
                role_ref,
                deadline,
                dependency_count,
                dependency_offset,
            ) = struct.unpack_from(">HHHHHHI", chunk, record_offset)
            field = f"<binary>.directory[{index}].objectives[{objective_index}]"
            local_string(objective_ref, f"{field}.id")
            local_string(objective_type_ref, f"{field}.type")
            local_string(condition_ref, f"{field}.condition")
            local_string(role_ref, f"{field}.role")
            if deadline > 719:
                _error(f"{field}.deadline", "outside authoritative loop")
            dependencies: list[int] = []
            if dependency_count:
                detail_range(
                    dependency_offset,
                    dependency_count * 2,
                    f"{field}.dependencies",
                )
                for dependency_index in range(dependency_count):
                    dependency = struct.unpack_from(
                        ">H", chunk, dependency_offset + dependency_index * 2
                    )[0]
                    if dependency >= objective_count or dependency == objective_index:
                        _error(f"{field}.dependencies", "invalid objective index")
                    dependencies.append(dependency)
                if dependencies != sorted(set(dependencies)):
                    _error(f"{field}.dependencies", "indices not sorted and unique")
            elif dependency_offset:
                _error(f"{field}.dependencies", "zero count has nonzero offset")
            objective_dependencies.append(dependencies)

        objective_visiting: set[int] = set()
        objective_visited: set[int] = set()

        def visit_compiled_objective(objective_index: int) -> None:
            if objective_index in objective_visiting:
                _error(
                    f"<binary>.directory[{index}].objectives",
                    "compiled dependency cycle",
                )
            if objective_index in objective_visited:
                return
            objective_visiting.add(objective_index)
            for dependency in objective_dependencies[objective_index]:
                visit_compiled_objective(dependency)
            objective_visiting.remove(objective_index)
            objective_visited.add(objective_index)

        for objective_index in range(objective_count):
            visit_compiled_objective(objective_index)

        for role_index in range(role_count):
            record_offset = role_offset + role_index * ROLE_RECORD_SIZE
            (
                role_ref,
                actor_ref,
                role_start,
                role_end,
                action_count,
                actions_offset,
                role_reserved,
            ) = struct.unpack_from(">HHHHHIH", chunk, record_offset)
            field = f"<binary>.directory[{index}].roles[{role_index}]"
            local_string(role_ref, f"{field}.id")
            local_string(actor_ref, f"{field}.actor")
            if role_start > role_end or role_end > 719 or role_reserved:
                _error(field, "invalid role bounds/reserved field")
            if not action_count:
                _error(f"{field}.actions", "role has no actions")
            detail_range(
                actions_offset, action_count * ACTION_RECORD_SIZE, f"{field}.actions"
            )
            prior_frame = -1
            for action_index in range(action_count):
                (
                    frame,
                    action_ref,
                    target_index,
                    action_reserved,
                ) = struct.unpack_from(
                    ">HHHH",
                    chunk,
                    actions_offset + action_index * ACTION_RECORD_SIZE,
                )
                local_string(action_ref, f"{field}.actions[{action_index}].action")
                if (
                    frame < role_start
                    or frame > role_end
                    or frame < prior_frame
                    or target_index >= entity_count
                    or action_reserved
                ):
                    _error(f"{field}.actions[{action_index}]", "invalid action record")
                prior_frame = frame

        expected_phase_start = 0
        for phase_index in range(phase_count):
            record_offset = phase_offset + phase_index * PHASE_RECORD_SIZE
            (
                phase_ref,
                entry_ref,
                phase_start,
                phase_end,
                progress_ref,
                exit_ref,
                attack_count,
                attacks_offset,
                phase_reserved,
            ) = struct.unpack_from(">HHHHHHHIH", chunk, record_offset)
            field = f"<binary>.directory[{index}].phases[{phase_index}]"
            local_string(phase_ref, f"{field}.id")
            local_string(entry_ref, f"{field}.entry")
            local_string(progress_ref, f"{field}.progress")
            local_string(exit_ref, f"{field}.exit")
            if (
                phase_start != expected_phase_start
                or phase_start > phase_end
                or phase_end > 719
                or phase_reserved
                or not attack_count
            ):
                _error(field, "invalid phase window/count/reserved field")
            expected_phase_start = phase_end + 1
            detail_range(
                attacks_offset,
                attack_count * ATTACK_RECORD_SIZE,
                f"{field}.attacks",
            )
            for attack_index in range(attack_count):
                (
                    pattern_ref,
                    attack_mode,
                    value_count,
                    attack_value,
                    attack_data_offset,
                    attack_reserved,
                ) = struct.unpack_from(
                    ">HBBHIH",
                    chunk,
                    attacks_offset + attack_index * ATTACK_RECORD_SIZE,
                )
                attack_field = f"{field}.attacks[{attack_index}]"
                local_string(pattern_ref, f"{attack_field}.pattern")
                if attack_reserved:
                    _error(attack_field, "nonzero reserved field")
                if attack_mode == ATTACK_FRAMES:
                    if not value_count or attack_value:
                        _error(attack_field, "invalid explicit-frame attack")
                    detail_range(
                        attack_data_offset,
                        value_count * 2,
                        f"{attack_field}.frames",
                    )
                    frames = [
                        struct.unpack_from(
                            ">H", chunk, attack_data_offset + frame_index * 2
                        )[0]
                        for frame_index in range(value_count)
                    ]
                    if (
                        frames != sorted(set(frames))
                        or frames[0] < phase_start
                        or frames[-1] > phase_end
                    ):
                        _error(attack_field, "invalid explicit attack frames")
                elif attack_mode == ATTACK_PERIOD:
                    if (
                        value_count
                        or attack_data_offset
                        or not 1 <= attack_value <= 720
                    ):
                        _error(attack_field, "invalid periodic attack")
                elif attack_mode == ATTACK_SOURCE:
                    if value_count or attack_data_offset:
                        _error(attack_field, "invalid sourced attack")
                    local_string(attack_value, f"{attack_field}.source")
                else:
                    _error(attack_field, "unknown attack mode")
        if phase_count:
            if expected_phase_start != 720 or kind != KIND_CODES["boss"]:
                _error(
                    f"<binary>.directory[{index}].phases",
                    "phases do not cover boss loop",
                )
        elif kind == KIND_CODES["boss"]:
            _error(f"<binary>.directory[{index}].phases", "boss has no phases")

        for policy_index in range(policy_count):
            priority, condition_ref, response_ref, policy_reserved = struct.unpack_from(
                ">HHHH", chunk, policy_offset + policy_index * POLICY_RECORD_SIZE
            )
            field = f"<binary>.directory[{index}].policies[{policy_index}]"
            if priority != policy_index + 1 or policy_reserved:
                _error(field, "invalid policy priority/reserved field")
            local_string(condition_ref, f"{field}.condition")
            local_string(response_ref, f"{field}.response")
        if bool(chunk_flags & CHUNK_FLAG_HAS_ANCHORS) != compiled_has_anchors:
            _error(f"<binary>.directory[{index}].flags", "anchor flag mismatch")
        if bool(chunk_flags & CHUNK_FLAG_HAS_RESPONSE_POLICY) != bool(policy_count):
            _error(f"<binary>.directory[{index}].flags", "policy flag mismatch")

        budgets = struct.unpack_from(">" + "H" * len(BUDGET_FIELDS), chunk, 94)
        for field, value in zip(BUDGET_FIELDS, budgets):
            if value > BUDGET_CAPS[field]:
                _error(
                    f"<binary>.directory[{index}].budgets.{field}",
                    "compiled cap exceeded",
                )
        budget_by_name = dict(zip(BUDGET_FIELDS, budgets))
        if budget_by_name["actors"] < echo_cap + 1:
            _error(f"<binary>.directory[{index}].budgets.actors", "actor underflow")
        if (
            budget_by_name["sprites_per_scanline"]
            > budget_by_name["hardware_sprites"]
        ):
            _error(
                f"<binary>.directory[{index}].budgets.sprites_per_scanline",
                "exceeds total sprites",
            )
        for category, count in category_counts.items():
            if budget_by_name[category] < count:
                _error(
                    f"<binary>.directory[{index}].budgets.{category}",
                    "does not cover compiled entities",
                )
        previous_end = chunk_end
    if previous_end != len(binary):
        _error("<binary>", "trailing or unreferenced bytes")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and compile deterministic RECURSION 94 content"
    )
    parser.add_argument("--content-root", type=Path, default=Path("content"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("assets/generated/content"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="require generated files to be byte-identical to a fresh compile",
    )
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="validate and compile in memory without writing outputs",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.check:
            bundle = check_generated(args.content_root, args.output_dir)
            action = "checked"
        elif args.validate_only:
            bundle = build_artifacts(args.content_root)
            action = "validated"
        else:
            bundle = compile_content(args.content_root, args.output_dir)
            action = "generated"
    except ContentCompileError as exc:
        print(f"content compiler: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "content compiler: PASS: "
        f"{action} {bundle.encounter_count} encounters; "
        f"source_sha256={bundle.source_sha256}; "
        f"binary_sha256={bundle.binary_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
