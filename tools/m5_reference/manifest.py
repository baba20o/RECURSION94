"""Exhaustive campaign-entity to M5 vocabulary adapter contract.

The adapter consumes this manifest directly.  Every authored state name is
mapped to an explicit vocabulary bitfield; no ordinal state value is part of
the cartridge ABI.  Boss components remain deliberately deferred to the
future boss-phase controller and are tagged as such in generated ROM metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from tools.content_compiler.compiler import (
    ALL_ENTITY_TYPES,
    BOSS_COMPONENT_TYPES,
    CARRYABLE_TYPES,
    DEVICE_TYPES,
    ENEMY_TYPES,
    ENTITY_TYPES_BY_CATEGORY,
    HAZARD_TYPES,
    _entity_type_code,
)


ADAPTER_FORMAT_VERSION = 3

POOL_BY_CATEGORY = {
    "enemies": 1,
    "devices": 3,
    "carryables": 4,
    "hazards": 5,
    "boss_components": 6,
}

DEVICE_PRESSURE = 0x0100
DEVICE_TIMED = 0x0200
DEVICE_DOOR = 0x0300
DEVICE_SOCKET = 0x0400
DEVICE_CONVEYOR = 0x0500
DEVICE_BEAM = 0x0600
DEVICE_REFLECTOR = 0x0700
DEVICE_SHIELD = 0x0800
DEVICE_HACK = 0x0900
DEVICE_REPAIR = 0x0A00
DEVICE_SECURITY = 0x0B00

ENEMY_TURRET = 0x0100
ENEMY_PURSUER = 0x0200
ENEMY_GUARD = 0x0300

HAZARD_DAMAGE = 0x0100
HAZARD_SHIELD = 0x0200
HAZARD_CONVEYOR = 0x0300
CARRYABLE_CELL = 0x0100
DEFERRED_BOSS_KIND = 0x0000

DEVICE_STATE_ACTIVE = 0x0001
DEVICE_STATE_OPEN = 0x0002
DEVICE_STATE_HACKED = 0x0004
DEVICE_STATE_DISABLED = 0x0008
DEVICE_STATE_ANCHORED = 0x0010
DEVICE_STATE_OCCUPIED = 0x0020
HAZARD_STATE_WARNING = 0x0010
DEVICE_DIRECTION_RIGHT = 0x0000
DEVICE_DIRECTION_DOWN = 0x0040
DEVICE_DIRECTION_LEFT = 0x0080
DEVICE_DIRECTION_UP = 0x00C0

ENEMY_STATE_ALERT = 0x0001
ENEMY_STATE_DISABLED = 0x0002
CARRY_STATE_CARRIED = 0x0001
CARRY_STATE_SOCKETED = 0x0002

OWNER_VOCABULARY_DEVICE = "vocabulary_device"
OWNER_VOCABULARY_ENEMY = "vocabulary_enemy"
OWNER_VOCABULARY_HAZARD = "vocabulary_hazard"
OWNER_VOCABULARY_CARRYABLE = "vocabulary_carryable"
OWNER_FUTURE_BOSS = "future_boss_phase_controller"

PARAM_DEVICE_LINK = "device_link_group"
PARAM_DEVICE_TARGET = "device_target_logical_id"
PARAM_HAZARD_RADIUS = "hazard_radius"
PARAM_UNUSED = "unused_zero"

REFERENCE_SOURCE_TO_TARGET = "source_to_target"
REFERENCE_TARGET_TO_SOURCE = "target_to_source"
DEPENDENCY_ALL = "ALL"
DEPENDENCY_ANY = "ANY"


@dataclass(frozen=True)
class EntityVocabularyContract:
    entity_type: str
    category: str
    pool_type: int
    vocab_kind: int
    owner: str
    original_type_code: int
    state_bits: tuple[tuple[str, int], ...]
    param_semantic: str
    deferred: bool
    adapter_format: int = ADAPTER_FORMAT_VERSION

    def state_map(self) -> dict[str, int]:
        """Return a fresh named-state map for compiler-side validation."""

        return dict(self.state_bits)


@dataclass(frozen=True)
class SemanticToken:
    token_id: int
    name: str
    domain: str
    value: int


@dataclass(frozen=True)
class ReferenceContract:
    reference_id: int
    parameter_key: str
    direction: str
    dependency_policy: str


@dataclass(frozen=True)
class ReferenceField:
    field_id: int
    name: str
    width_bytes: int
    object_offset: int
    shift: int


# The host reference model currently exposes exactly 58 gameplay-semantic
# tokens after storage limits and raw controller bits are excluded.  Stable
# numeric IDs let generated records refer to a semantic without conflating
# equal values from different domains (for example, ACTIVE and DASHING).
_SEMANTIC_TOKEN_SPECS = (
    ("DEVICE_PRESSURE", "device_kind", DEVICE_PRESSURE),
    ("DEVICE_TIMED", "device_kind", DEVICE_TIMED),
    ("DEVICE_DOOR", "device_kind", DEVICE_DOOR),
    ("DEVICE_SOCKET", "device_kind", DEVICE_SOCKET),
    ("DEVICE_CONVEYOR", "device_kind", DEVICE_CONVEYOR),
    ("DEVICE_BEAM", "device_kind", DEVICE_BEAM),
    ("DEVICE_REFLECTOR", "device_kind", DEVICE_REFLECTOR),
    ("DEVICE_SHIELD", "device_kind", DEVICE_SHIELD),
    ("DEVICE_HACK", "device_kind", DEVICE_HACK),
    ("DEVICE_REPAIR", "device_kind", DEVICE_REPAIR),
    ("DEVICE_SECURITY", "device_kind", DEVICE_SECURITY),
    ("ENEMY_TURRET", "enemy_kind", ENEMY_TURRET),
    ("ENEMY_PURSUER", "enemy_kind", ENEMY_PURSUER),
    ("ENEMY_GUARD", "enemy_kind", ENEMY_GUARD),
    ("HAZARD_DAMAGE", "hazard_kind", HAZARD_DAMAGE),
    ("HAZARD_SHIELD", "hazard_kind", HAZARD_SHIELD),
    ("HAZARD_CONVEYOR", "hazard_kind", HAZARD_CONVEYOR),
    ("CARRYABLE_CELL", "carryable_kind", CARRYABLE_CELL),
    ("STATE_ACTIVE", "object_state", DEVICE_STATE_ACTIVE),
    ("STATE_OPEN", "object_state", DEVICE_STATE_OPEN),
    ("STATE_HACKED", "object_state", DEVICE_STATE_HACKED),
    ("STATE_DISABLED", "object_state", DEVICE_STATE_DISABLED),
    ("STATE_ANCHORED", "object_state", DEVICE_STATE_ANCHORED),
    ("STATE_OCCUPIED", "object_state", DEVICE_STATE_OCCUPIED),
    ("ACTOR_DASHING", "actor_state", 0x0001),
    ("ACTOR_PHASED", "actor_state", 0x0002),
    ("ACTOR_INVULNERABLE", "actor_state", 0x0004),
    ("ACTOR_CARRYING", "actor_state", 0x0008),
    ("ACTOR_SHIELDED", "actor_state", 0x0010),
    ("ACTOR_HURT", "actor_state", 0x0020),
    ("PROJECTILE_PLAYER", "projectile_state", 0x0001),
    ("PROJECTILE_ENEMY", "projectile_state", 0x0002),
    ("PROJECTILE_REFLECTED", "projectile_state", 0x0004),
    ("ANCHOR_DEVICE_STATE", "anchor_type", 1),
    ("ANCHOR_SOCKETED_CELL", "anchor_type", 2),
    ("ANCHOR_CONVEYOR_DIRECTION", "anchor_type", 3),
    ("ANCHOR_SECURITY_NODE", "anchor_type", 4),
    ("ANCHOR_ECHO_PROTECTION", "anchor_type", 5),
    ("RANK_NONE", "stage_rank", 0),
    ("RANK_STABLE", "stage_rank", 1),
    ("RANK_OPTIMIZED", "stage_rank", 2),
    ("RANK_TRANSCENDENT", "stage_rank", 3),
    ("RESULT_OK", "result", 0),
    ("RESULT_DASHED", "result", 1),
    ("RESULT_PHASED", "result", 2),
    ("RESULT_PICKED_UP", "result", 3),
    ("RESULT_DROPPED", "result", 4),
    ("RESULT_SOCKETED", "result", 5),
    ("RESULT_ACTIVATED", "result", 6),
    ("RESULT_HACKED", "result", 7),
    ("RESULT_REPAIRED", "result", 8),
    ("RESULT_NO_TARGET", "result", 9),
    ("RESULT_COOLDOWN", "result", 10),
    ("RESULT_INVALID", "result", 11),
    ("RESULT_POOL_FULL", "result", 12),
    ("RESULT_ANCHOR_FULL", "result", 13),
    ("RESULT_ANCHOR_FORBIDDEN", "result", 14),
    ("RESULT_STAGE_COMPLETE", "result", 15),
)
SEMANTIC_TOKEN_REGISTRY = {
    name: SemanticToken(index, name, domain, value)
    for index, (name, domain, value) in enumerate(
        _SEMANTIC_TOKEN_SPECS, start=1
    )
}
SEMANTIC_TOKEN_BY_ID = {
    token.token_id: token for token in SEMANTIC_TOKEN_REGISTRY.values()
}


# All parameter keys that currently carry encounter-local stable references
# are explicit here.  Direction describes the authored relationship, never an
# undirected connected component.  The runtime adapter rejects a new
# reference-bearing key until it is classified.
_REFERENCE_SPECS = (
    ("accepts", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("controls", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("covers", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("exposed_by", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("guard_target", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("opens_after", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("opens_from", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("power_source", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("powers", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("progress_source", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("protects", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("redirect_target", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("redirects", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("release_requires", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("requires", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("seal_sources", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("source", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("stabilizes", REFERENCE_SOURCE_TO_TARGET, DEPENDENCY_ANY),
    ("suppressed_by", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
    ("unlock_source", REFERENCE_TARGET_TO_SOURCE, DEPENDENCY_ALL),
)
REFERENCE_REGISTRY = {
    key: ReferenceContract(index, key, direction, policy)
    for index, (key, direction, policy) in enumerate(
        _REFERENCE_SPECS, start=1
    )
}

# Fields addressable by dependency/completion records.  Health is a virtual
# byte extracted from the high byte of OBJECT_STATE; the other offsets are
# byte offsets in the frozen 12-byte canonical object record.
REFERENCE_FIELD_REGISTRY = {
    "state": ReferenceField(1, "state", 2, 2, 0),
    "timer": ReferenceField(2, "timer", 2, 8, 0),
    "param": ReferenceField(3, "param", 2, 10, 0),
    "health": ReferenceField(4, "health", 1, 2, 8),
}
REFERENCE_ALIASES = {
    "B03_NODE_ARM": "B03_NODE",
}


DEVICE_GROUPS = {
    DEVICE_PRESSURE: frozenset(
        {
            "payload_pad",
            "phase_hold_terminal",
            "pressure_switch",
            "seed_handoff_pad",
        }
    ),
    DEVICE_TIMED: frozenset(
        {
            "bridge_switch",
            "phase_terminal",
            "scheduled_channel_terminal",
            "timed_switch",
        }
    ),
    DEVICE_DOOR: frozenset(
        {
            "blackout_gate",
            "boss_core_lock",
            "door",
            "interrupt_port",
            "overlap_door",
            "powered_door",
            "pulse_door",
            "timed_door",
        }
    ),
    DEVICE_SOCKET: frozenset({"power_socket", "seed_socket"}),
    DEVICE_CONVEYOR: frozenset(
        {
            "conveyor_direction_switch",
            "moving_platform",
            "transport_pad_control",
        }
    ),
    DEVICE_BEAM: frozenset({"beam_emitter"}),
    DEVICE_REFLECTOR: frozenset(
        {
            "attack_reflector",
            "beam_reflector",
            "burst_reflector",
            "redirectable_prism",
        }
    ),
    DEVICE_SHIELD: frozenset(
        {"local_shield_emitter", "phase_stabilizer", "shield_emitter"}
    ),
    DEVICE_REPAIR: frozenset({"repair_terminal"}),
    DEVICE_SECURITY: frozenset(
        {"breakable_lock", "breakable_security_node"}
    ),
    DEVICE_HACK: frozenset(
        {
            "boss_commit_terminal",
            "boss_shutdown_port",
            "boss_upload_port",
            "echo_buffer_terminal",
            "exit_receiver",
            "exit_terminal",
            "final_seed_transmitter",
            "hack_terminal",
            "route_terminal",
            "seal_terminal",
            "uplink_power_terminal",
            "visibility_beacon",
        }
    ),
}

ENEMY_GROUPS = {
    ENEMY_TURRET: frozenset({"sentry"}),
    ENEMY_PURSUER: frozenset({"pursuer"}),
    ENEMY_GUARD: frozenset({"mirror", "scrubber", "warden"}),
}

HAZARD_GROUPS = {
    HAZARD_CONVEYOR: frozenset({"conveyor"}),
    HAZARD_SHIELD: frozenset({"energy_bridge"}),
    HAZARD_DAMAGE: HAZARD_TYPES - frozenset({"conveyor", "energy_bridge"}),
}


# Frozen authored-state vocabulary.  New or renamed states must receive an
# explicit semantic mapping here before the adapter will compile them.
AUTHORED_STATES_BY_TYPE = {
    "attack_reflector": ("east", "north", "west"),
    "beam_emitter": ("active_east", "blocked"),
    "beam_reflector": ("east", "north", "south", "west"),
    "blackout_gate": ("closed", "open"),
    "boss_channel_arbiter": ("final", "rotated", "strict"),
    "boss_checksum_ring": ("closed", "matched", "open"),
    "boss_clock": ("zone_a", "zone_b", "zone_c"),
    "boss_commit_terminal": ("committed", "open", "sealed"),
    "boss_core": (
        "allocating",
        "checksum_exposed",
        "collecting",
        "contained",
        "desynchronized",
        "forking",
        "guarding",
        "halted",
        "overcommitted",
        "profiling",
        "responding",
        "shutdown",
        "terminated",
        "transcended",
        "verified",
    ),
    "boss_core_lock": ("closed", "open"),
    "boss_lock": ("exposed", "open", "released", "sealed", "verified"),
    "boss_policy_display": ("locked", "preview", "resolved"),
    "boss_scrubber": ("armed", "cooldown", "sweeping"),
    "boss_seal_ring": ("half_sealed", "open", "sealed"),
    "boss_sensor": ("active", "dark", "observing", "redirected", "stalled"),
    "boss_shutdown_port": ("exposed", "sealed", "shutdown", "writing"),
    "boss_transformer": ("executing", "locked", "previewing"),
    "boss_upload_port": ("ready", "sealed", "uploading", "verified", "writing"),
    "boss_weapon": ("armed", "disabled", "redirected"),
    "breakable_lock": ("damaged", "destroyed", "intact"),
    "breakable_security_node": (
        "armored_front",
        "damaged",
        "destroyed",
        "exposed",
        "exposed_rear",
        "intact",
        "locked",
        "shielded",
        "unlocked",
    ),
    "bridge_switch": ("off", "on"),
    "burst_reflector": ("north", "west"),
    "closing_door_crush_zone": ("closing", "safe", "warning"),
    "conveyor": ("eastbound", "westbound"),
    "conveyor_direction_switch": ("forward", "reverse"),
    "dense_fire_lane": ("armed", "shielded"),
    "door": ("closed", "open"),
    "echo_buffer_terminal": ("empty", "recording", "sealed"),
    "energy_bridge": ("off", "on", "warning"),
    "exit_receiver": ("armed", "complete", "locked"),
    "exit_terminal": ("armed", "committed", "locked"),
    "final_seed_transmitter": ("ready", "sealed", "transmitting", "verified"),
    "guard_barrier": ("active", "suppressed"),
    "hack_terminal": ("restarting", "running", "stalled"),
    "hostile_replay_collision_zone": ("armed", "sealed"),
    "interrupt_port": ("closed", "open"),
    "local_shield_emitter": ("active", "charging", "idle"),
    "mirror": ("active", "contained", "expired", "preview", "replaying"),
    "moving_hazard": ("north", "south"),
    "moving_platform": ("falling", "lower", "rising", "upper"),
    "noise_amplifier": ("quiet", "ringing"),
    "non_maskable_raster_burst": ("active", "counting", "warning"),
    "oracle_projectile_field": ("cardinal", "final", "mirrored", "profile"),
    "overlap_door": ("closed", "open", "opening"),
    "payload_pad": ("empty", "occupied"),
    "phase_hold_terminal": ("held", "released"),
    "phase_rejection_field": ("early", "late", "safe"),
    "phase_stabilizer": ("drifting", "locked", "stable"),
    "phase_terminal": ("expired", "idle", "latched", "window"),
    "power_cell": (
        "carried",
        "carried_upper",
        "dropped",
        "feed_buffer",
        "grounded",
        "lift_buffer",
        "socketed",
    ),
    "power_socket": ("empty", "occupied", "powered"),
    "powered_door": ("closed", "open", "opening"),
    "pressure_switch": ("pressed", "released"),
    "pulse_door": ("closed", "open", "visual_warning"),
    "pursuer": ("chasing", "investigating", "listening", "returning"),
    "redirectable_prism": ("east", "north", "south", "west"),
    "redirected_laser": (
        "blocked",
        "east_path",
        "north_path",
        "south_path",
        "west_path",
    ),
    "reflected_laser": ("vertical", "west_contained"),
    "repair_terminal": ("damaged", "repaired", "repairing"),
    "replay_disable_zone": ("blackout", "visible"),
    "replay_interference_zone": ("active", "dormant", "stabilized"),
    "rotating_observation_cone": ("east", "north", "south", "stalled", "west"),
    "route_terminal": ("closed", "open"),
    "scheduled_channel_terminal": ("idle", "latched", "missed", "window"),
    "scheduled_echo_disable": ("counting", "scrubbing", "warning"),
    "scheduled_raster_hazard": ("active", "safe"),
    "scheduled_scanline_field": ("active", "safe", "warning"),
    "scrubber": ("attacking_replay", "destroyed", "seeking", "stunned"),
    "seal_terminal": ("open", "sealed"),
    "seed_handoff_pad": ("empty", "occupied"),
    "seed_payload": (
        "carried_leg_1",
        "carried_leg_2",
        "carried_leg_3",
        "handoff",
        "handoff_a",
        "handoff_b",
        "loaded",
        "socketed",
        "vault",
    ),
    "seed_socket": ("empty", "seeded"),
    "sentry": (
        "charging",
        "cooldown",
        "disabled",
        "exposed_tracking",
        "firing",
        "redirected",
        "shielded_tracking",
        "tracking",
    ),
    "sentry_fire_lane": ("armed", "occupied"),
    "shield_emitter": (
        "active",
        "charging",
        "off",
        "overloaded",
        "restarting",
        "suppressed",
    ),
    "timed_door": ("closed", "open"),
    "timed_switch": ("active", "expired", "idle"),
    "transport_pad_control": ("called", "idle", "locked"),
    "turret_fire_lane": ("armed", "suppressed"),
    "uplink_power_terminal": ("charging", "off", "powered"),
    "visibility_beacon": ("dark", "visible"),
    "warden": ("chasing", "guarding", "investigating", "returning"),
}


DEVICE_STATE_BITS_BY_NAME = {
    "active": DEVICE_STATE_ACTIVE,
    "active_east": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_RIGHT,
    "armed": DEVICE_STATE_ACTIVE,
    "armored_front": 0,
    "blocked": DEVICE_STATE_DISABLED,
    "called": DEVICE_STATE_ACTIVE,
    "charging": DEVICE_STATE_ACTIVE,
    "closed": 0,
    "closing": DEVICE_STATE_ACTIVE,
    "committed": DEVICE_STATE_ACTIVE | DEVICE_STATE_HACKED,
    "complete": DEVICE_STATE_ACTIVE | DEVICE_STATE_HACKED,
    "damaged": DEVICE_STATE_DISABLED,
    "dark": 0,
    "destroyed": DEVICE_STATE_DISABLED,
    "drifting": 0,
    "east": DEVICE_DIRECTION_RIGHT,
    "empty": 0,
    "expired": DEVICE_STATE_DISABLED,
    "exposed": DEVICE_STATE_ACTIVE,
    "exposed_rear": DEVICE_STATE_ACTIVE,
    "falling": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_DOWN,
    "forward": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_RIGHT,
    "held": DEVICE_STATE_ACTIVE,
    "idle": 0,
    "intact": 0,
    "latched": DEVICE_STATE_ACTIVE,
    "locked": 0,
    "lower": 0,
    "missed": DEVICE_STATE_DISABLED,
    "north": DEVICE_DIRECTION_UP,
    "occupied": DEVICE_STATE_OCCUPIED,
    "off": 0,
    "on": DEVICE_STATE_ACTIVE,
    "open": DEVICE_STATE_OPEN,
    "opening": DEVICE_STATE_ACTIVE,
    "overloaded": DEVICE_STATE_DISABLED,
    "powered": DEVICE_STATE_ACTIVE | DEVICE_STATE_OCCUPIED,
    "pressed": DEVICE_STATE_ACTIVE,
    "ready": DEVICE_STATE_ACTIVE,
    "recording": DEVICE_STATE_ACTIVE,
    "released": 0,
    "repaired": DEVICE_STATE_ACTIVE,
    "repairing": DEVICE_STATE_ACTIVE,
    "restarting": DEVICE_STATE_DISABLED,
    "reverse": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_LEFT,
    "rising": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_UP,
    "running": DEVICE_STATE_ACTIVE,
    "sealed": 0,
    "seeded": DEVICE_STATE_OCCUPIED,
    "shielded": 0,
    "shutdown": DEVICE_STATE_DISABLED | DEVICE_STATE_HACKED,
    "south": DEVICE_DIRECTION_DOWN,
    "stable": DEVICE_STATE_ACTIVE,
    "stalled": DEVICE_STATE_DISABLED,
    "suppressed": DEVICE_STATE_DISABLED,
    "transmitting": DEVICE_STATE_ACTIVE,
    "unlocked": DEVICE_STATE_OPEN,
    "uploading": DEVICE_STATE_ACTIVE,
    "upper": DEVICE_STATE_ACTIVE,
    "verified": DEVICE_STATE_ACTIVE | DEVICE_STATE_HACKED,
    "visible": DEVICE_STATE_ACTIVE,
    "visual_warning": DEVICE_STATE_ACTIVE,
    "west": DEVICE_DIRECTION_LEFT,
    "window": DEVICE_STATE_ACTIVE,
    "writing": DEVICE_STATE_ACTIVE,
}

ENEMY_STATE_BITS_BY_NAME = {
    "active": ENEMY_STATE_ALERT,
    "attacking_replay": ENEMY_STATE_ALERT,
    "charging": ENEMY_STATE_ALERT,
    "chasing": ENEMY_STATE_ALERT,
    "contained": ENEMY_STATE_DISABLED,
    "cooldown": ENEMY_STATE_ALERT,
    "destroyed": ENEMY_STATE_DISABLED,
    "disabled": ENEMY_STATE_DISABLED,
    "expired": ENEMY_STATE_DISABLED,
    "exposed_tracking": ENEMY_STATE_ALERT,
    "firing": ENEMY_STATE_ALERT,
    "guarding": 0,
    "investigating": ENEMY_STATE_ALERT,
    "listening": 0,
    "preview": 0,
    "redirected": ENEMY_STATE_ALERT,
    "replaying": ENEMY_STATE_ALERT,
    "returning": 0,
    "seeking": ENEMY_STATE_ALERT,
    "shielded_tracking": ENEMY_STATE_ALERT,
    "stunned": ENEMY_STATE_DISABLED,
    "tracking": ENEMY_STATE_ALERT,
}

HAZARD_STATE_BITS_BY_NAME = {
    "active": DEVICE_STATE_ACTIVE,
    "armed": DEVICE_STATE_ACTIVE,
    "blackout": DEVICE_STATE_ACTIVE,
    "blocked": DEVICE_STATE_DISABLED,
    "cardinal": DEVICE_STATE_ACTIVE,
    "closing": DEVICE_STATE_ACTIVE,
    "counting": 0,
    "dormant": 0,
    "early": DEVICE_STATE_ACTIVE,
    "east": DEVICE_DIRECTION_RIGHT,
    "east_path": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_RIGHT,
    "eastbound": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_RIGHT,
    "final": DEVICE_STATE_ACTIVE,
    "late": DEVICE_STATE_ACTIVE,
    "mirrored": DEVICE_STATE_ACTIVE,
    "north": DEVICE_DIRECTION_UP,
    "north_path": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_UP,
    "occupied": DEVICE_STATE_ACTIVE,
    "off": 0,
    "on": DEVICE_STATE_ACTIVE,
    "profile": 0,
    "quiet": 0,
    "ringing": DEVICE_STATE_ACTIVE,
    "safe": 0,
    "scrubbing": DEVICE_STATE_ACTIVE,
    "sealed": DEVICE_STATE_DISABLED,
    "shielded": DEVICE_STATE_ACTIVE,
    "south": DEVICE_DIRECTION_DOWN,
    "south_path": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_DOWN,
    "stabilized": DEVICE_STATE_DISABLED,
    "stalled": DEVICE_STATE_DISABLED,
    "suppressed": DEVICE_STATE_DISABLED,
    "vertical": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_UP,
    "visible": 0,
    "warning": HAZARD_STATE_WARNING,
    "west": DEVICE_DIRECTION_LEFT,
    "west_contained": DEVICE_STATE_DISABLED | DEVICE_DIRECTION_LEFT,
    "west_path": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_LEFT,
    "westbound": DEVICE_STATE_ACTIVE | DEVICE_DIRECTION_LEFT,
}

CARRY_STATE_BITS_BY_NAME = {
    "carried": CARRY_STATE_CARRIED,
    "carried_leg_1": CARRY_STATE_CARRIED,
    "carried_leg_2": CARRY_STATE_CARRIED,
    "carried_leg_3": CARRY_STATE_CARRIED,
    "carried_upper": CARRY_STATE_CARRIED,
    "dropped": 0,
    "feed_buffer": 0,
    "grounded": 0,
    "handoff": 0,
    "handoff_a": 0,
    "handoff_b": 0,
    "lift_buffer": 0,
    "loaded": CARRY_STATE_SOCKETED,
    "socketed": CARRY_STATE_SOCKETED,
    "vault": 0,
}


def _group_lookup(
    entity_type: str, groups: dict[int, frozenset[str]]
) -> int:
    matches = [kind for kind, members in groups.items() if entity_type in members]
    if len(matches) != 1:
        raise ValueError(
            f"{entity_type} must have exactly one vocabulary kind, got {matches}"
        )
    return matches[0]


def _state_map_for(category: str, entity_type: str) -> tuple[tuple[str, int], ...]:
    names = AUTHORED_STATES_BY_TYPE[entity_type]
    if category == "devices":
        table = DEVICE_STATE_BITS_BY_NAME
    elif category == "enemies":
        table = ENEMY_STATE_BITS_BY_NAME
    elif category == "hazards":
        table = HAZARD_STATE_BITS_BY_NAME
    elif category == "carryables":
        table = CARRY_STATE_BITS_BY_NAME
    elif category == "boss_components":
        # Deferred states are still explicit: every known name maps to zero
        # until the future boss-phase controller defines its own ABI.
        return tuple((name, 0) for name in names)
    else:  # pragma: no cover - compiler registry is closed
        raise ValueError(f"unknown entity category: {category}")
    missing = [name for name in names if name not in table]
    if missing:
        raise ValueError(f"{entity_type} has unmapped named states: {missing}")
    return tuple((name, table[name]) for name in names)


def _build_manifest() -> dict[str, EntityVocabularyContract]:
    manifest: dict[str, EntityVocabularyContract] = {}
    for category, types in ENTITY_TYPES_BY_CATEGORY.items():
        for entity_type in types:
            if category == "devices":
                kind = _group_lookup(entity_type, DEVICE_GROUPS)
                owner = OWNER_VOCABULARY_DEVICE
                param_semantic = (
                    PARAM_DEVICE_TARGET
                    if entity_type == "repair_terminal"
                    else PARAM_DEVICE_LINK
                )
            elif category == "enemies":
                kind = _group_lookup(entity_type, ENEMY_GROUPS)
                owner = OWNER_VOCABULARY_ENEMY
                param_semantic = PARAM_UNUSED
            elif category == "hazards":
                kind = _group_lookup(entity_type, HAZARD_GROUPS)
                owner = OWNER_VOCABULARY_HAZARD
                param_semantic = PARAM_HAZARD_RADIUS
            elif category == "carryables":
                kind = CARRYABLE_CELL
                owner = OWNER_VOCABULARY_CARRYABLE
                param_semantic = PARAM_UNUSED
            elif category == "boss_components":
                kind = DEFERRED_BOSS_KIND
                owner = OWNER_FUTURE_BOSS
                param_semantic = PARAM_UNUSED
            else:  # pragma: no cover - compiler registry is closed
                raise ValueError(f"unknown entity category: {category}")
            manifest[entity_type] = EntityVocabularyContract(
                entity_type=entity_type,
                category=category,
                pool_type=POOL_BY_CATEGORY[category],
                vocab_kind=kind,
                owner=owner,
                original_type_code=_entity_type_code(entity_type),
                state_bits=_state_map_for(category, entity_type),
                param_semantic=param_semantic,
                deferred=category == "boss_components",
            )
    if set(manifest) != set(ALL_ENTITY_TYPES):
        missing = sorted(set(ALL_ENTITY_TYPES) - set(manifest))
        extra = sorted(set(manifest) - set(ALL_ENTITY_TYPES))
        raise ValueError(f"vocabulary manifest drift: missing={missing}, extra={extra}")
    return manifest


ENTITY_VOCABULARY_MANIFEST = _build_manifest()


def manifest_sha256() -> str:
    """Hash the semantic adapter contract, independent of Python formatting."""

    projection = {
        "entities": [
            {
                "entity_type": item.entity_type,
                "category": item.category,
                "pool_type": item.pool_type,
                "vocab_kind": item.vocab_kind,
                "owner": item.owner,
                "original_type_code": item.original_type_code,
                "state_bits": list(item.state_bits),
                "param_semantic": item.param_semantic,
                "deferred": item.deferred,
                "adapter_format": item.adapter_format,
            }
            for item in (
                ENTITY_VOCABULARY_MANIFEST[name]
                for name in sorted(ENTITY_VOCABULARY_MANIFEST)
            )
        ],
        "semantic_tokens": [
            {
                "id": token.token_id,
                "name": token.name,
                "domain": token.domain,
                "value": token.value,
            }
            for token in (
                SEMANTIC_TOKEN_REGISTRY[name]
                for name in sorted(SEMANTIC_TOKEN_REGISTRY)
            )
        ],
        "references": [
            {
                "id": contract.reference_id,
                "parameter_key": contract.parameter_key,
                "direction": contract.direction,
                "dependency_policy": contract.dependency_policy,
            }
            for contract in (
                REFERENCE_REGISTRY[name]
                for name in sorted(REFERENCE_REGISTRY)
            )
        ],
        "reference_fields": [
            {
                "id": field.field_id,
                "name": field.name,
                "width_bytes": field.width_bytes,
                "object_offset": field.object_offset,
                "shift": field.shift,
            }
            for field in (
                REFERENCE_FIELD_REGISTRY[name]
                for name in sorted(REFERENCE_FIELD_REGISTRY)
            )
        ],
        "reference_aliases": dict(sorted(REFERENCE_ALIASES.items())),
    }
    payload = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def validate_manifest() -> None:
    if len(SEMANTIC_TOKEN_REGISTRY) != 58:
        raise ValueError("frozen semantic token count changed")
    if set(SEMANTIC_TOKEN_BY_ID) != set(range(1, 59)):
        raise ValueError("semantic token IDs must be contiguous 1..58")
    if any(
        token.name != name or not 0 <= token.value <= 0xFFFF
        for name, token in SEMANTIC_TOKEN_REGISTRY.items()
    ):
        raise ValueError("invalid semantic token registry entry")
    if set(REFERENCE_REGISTRY) != {
        "accepts",
        "controls",
        "covers",
        "exposed_by",
        "guard_target",
        "opens_after",
        "opens_from",
        "power_source",
        "powers",
        "progress_source",
        "protects",
        "redirect_target",
        "redirects",
        "release_requires",
        "requires",
        "seal_sources",
        "source",
        "stabilizes",
        "suppressed_by",
        "unlock_source",
    }:
        raise ValueError("authored reference registry is not exhaustive")
    if {
        contract.reference_id for contract in REFERENCE_REGISTRY.values()
    } != set(range(1, len(REFERENCE_REGISTRY) + 1)):
        raise ValueError("reference registry IDs must be contiguous")
    if any(
        contract.direction
        not in {REFERENCE_SOURCE_TO_TARGET, REFERENCE_TARGET_TO_SOURCE}
        or contract.dependency_policy not in {DEPENDENCY_ALL, DEPENDENCY_ANY}
        for contract in REFERENCE_REGISTRY.values()
    ):
        raise ValueError("invalid authored reference contract")
    if set(REFERENCE_FIELD_REGISTRY) != {"state", "timer", "param", "health"}:
        raise ValueError("runtime reference field registry is not exhaustive")
    if {
        field.field_id for field in REFERENCE_FIELD_REGISTRY.values()
    } != {1, 2, 3, 4}:
        raise ValueError("runtime reference field IDs must be 1..4")
    if REFERENCE_ALIASES != {"B03_NODE_ARM": "B03_NODE"}:
        raise ValueError("runtime reference alias registry drift")
    if set().union(*DEVICE_GROUPS.values()) != set(DEVICE_TYPES):
        raise ValueError("device vocabulary mapping is not exhaustive")
    if sum(len(group) for group in DEVICE_GROUPS.values()) != len(DEVICE_TYPES):
        raise ValueError("device vocabulary mapping overlaps")
    if set().union(*ENEMY_GROUPS.values()) != set(ENEMY_TYPES):
        raise ValueError("enemy vocabulary mapping is not exhaustive")
    if sum(len(group) for group in ENEMY_GROUPS.values()) != len(ENEMY_TYPES):
        raise ValueError("enemy vocabulary mapping overlaps")
    if set().union(*HAZARD_GROUPS.values()) != set(HAZARD_TYPES):
        raise ValueError("hazard vocabulary mapping is not exhaustive")
    if sum(len(group) for group in HAZARD_GROUPS.values()) != len(HAZARD_TYPES):
        raise ValueError("hazard vocabulary mapping overlaps")
    if set(AUTHORED_STATES_BY_TYPE) != set(ALL_ENTITY_TYPES):
        raise ValueError("authored state registry is not exhaustive")
    if set(ENTITY_VOCABULARY_MANIFEST) != set(ALL_ENTITY_TYPES):
        raise ValueError("entity vocabulary manifest is not exhaustive")
    if len(ENTITY_VOCABULARY_MANIFEST) != 83:
        raise ValueError("frozen entity vocabulary count changed")
    type_codes: set[int] = set()
    for entity_type, contract in ENTITY_VOCABULARY_MANIFEST.items():
        if contract.adapter_format != ADAPTER_FORMAT_VERSION:
            raise ValueError(f"adapter format drift: {entity_type}")
        if contract.category != next(
            category
            for category, types in ENTITY_TYPES_BY_CATEGORY.items()
            if entity_type in types
        ):
            raise ValueError(f"entity category drift: {entity_type}")
        names = tuple(name for name, _bits in contract.state_bits)
        if names != AUTHORED_STATES_BY_TYPE[entity_type]:
            raise ValueError(f"named-state map drift: {entity_type}")
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate named state: {entity_type}")
        if any(not 0 <= bits <= 0xFFFF for _name, bits in contract.state_bits):
            raise ValueError(f"state bitfield out of range: {entity_type}")
        if contract.original_type_code in type_codes:
            raise ValueError(f"original type-code collision: {entity_type}")
        type_codes.add(contract.original_type_code)
    for entity_type in CARRYABLE_TYPES:
        if ENTITY_VOCABULARY_MANIFEST[entity_type].vocab_kind != CARRYABLE_CELL:
            raise ValueError(f"carryable kind drift: {entity_type}")
    for entity_type in BOSS_COMPONENT_TYPES:
        contract = ENTITY_VOCABULARY_MANIFEST[entity_type]
        if (
            contract.owner != OWNER_FUTURE_BOSS
            or contract.vocab_kind != DEFERRED_BOSS_KIND
            or not contract.deferred
        ):
            raise ValueError(f"boss ownership drift: {entity_type}")
        if any(bits != 0 for _name, bits in contract.state_bits):
            raise ValueError(f"deferred boss state must remain zero: {entity_type}")


validate_manifest()
