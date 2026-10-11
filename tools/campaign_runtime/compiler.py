"""Compile validated campaign manifests into bounded 68000 runtime tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.content_compiler.compiler import (
    ENTITY_CATEGORIES,
    ROSTER,
    SECTOR_CODES,
    load_and_validate,
)
from tools.m5_reference.manifest import (
    ADAPTER_FORMAT_VERSION,
    DEPENDENCY_ALL,
    DEPENDENCY_ANY,
    ENTITY_VOCABULARY_MANIFEST,
    OWNER_FUTURE_BOSS,
    PARAM_DEVICE_LINK,
    PARAM_DEVICE_TARGET,
    PARAM_HAZARD_RADIUS,
    PARAM_UNUSED,
    REFERENCE_ALIASES,
    REFERENCE_FIELD_REGISTRY,
    REFERENCE_REGISTRY,
    REFERENCE_SOURCE_TO_TARGET,
    SEMANTIC_TOKEN_REGISTRY,
    manifest_sha256,
    validate_manifest,
)


FORMAT_VERSION = ADAPTER_FORMAT_VERSION
OUTPUT_ASSEMBLY = "campaign_runtime.inc.asm"
OUTPUT_REPORT = "campaign_runtime_report.json"

ENGINE_DESCRIPTOR_BYTES = 0x20
STATIC_RECT_BYTES = 8
STATIC_RECT_CAPACITY = 32
OBJECT_INIT_BYTES = 12
OBJECT_METADATA_BYTES = 4
PROFILE_BYTES = 64
SEMANTIC_RECORD_BYTES = 16
DEPENDENCY_GROUP_BYTES = 16
DEPENDENCY_EDGE_BYTES = 8
SEMANTIC_TOKEN_RECORD_BYTES = 4
REFERENCE_FIELD_RECORD_BYTES = 4
SEMANTIC_FLAG_GEOMETRY_WIDTH_SHIFT = 5
SEMANTIC_FLAG_GEOMETRY_WIDTH_MASK = 0x07E0
SEMANTIC_FLAG_GEOMETRY_HEIGHT_SHIFT = 11
SEMANTIC_FLAG_GEOMETRY_HEIGHT_MASK = 0xF800
SEMANTIC_GEOMETRY_UNIT_PX = 8
MAX_ENTITIES = 140
MAX_DEPENDENCY_GROUPS = 0xFF
MAX_DEPENDENCY_EDGES = 0xFF

METADATA_OWNER_VOCABULARY = 1
METADATA_OWNER_FUTURE_BOSS = 2
METADATA_OWNER_CODE = {
    "vocabulary_device": METADATA_OWNER_VOCABULARY,
    "vocabulary_enemy": METADATA_OWNER_VOCABULARY,
    "vocabulary_hazard": METADATA_OWNER_VOCABULARY,
    "vocabulary_carryable": METADATA_OWNER_VOCABULARY,
    OWNER_FUTURE_BOSS: METADATA_OWNER_FUTURE_BOSS,
}

ENTITY_REFERENCE_RE = re.compile(r"\b[A-Z][0-9]{2}_[A-Z0-9_]+\b")
HAZARD_RADIUS_KEYS = (
    "radius_px",
    "handoff_radius_px",
    "hearing_radius_px",
    "movement_noise_radius_px",
    "range_px",
)

POOL_TYPE_BY_CATEGORY = {
    "enemies": 1,
    "devices": 3,
    "carryables": 4,
    "hazards": 5,
    "boss_components": 6,
}
POOL_CAPACITY_BY_TYPE = {
    1: 16,
    3: 32,
    4: 8,
    5: 16,
    6: 12,
}
KIND_CODES = {"primary": 0, "boss": 1}


class RuntimeCompileError(RuntimeError):
    """The validated campaign cannot fit the frozen cartridge runtime ABI."""


@dataclass(frozen=True)
class RuntimeRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class RuntimeObject:
    pool_type: int
    logical_id: int
    authored_logical_id: str
    category: str
    entity_type: str
    initial_state_name: str
    state_base_bits: int
    initial_state: int
    x: int
    y: int
    timer: int
    param: int
    param_low: int
    param_semantic: str
    original_type_code: int
    metadata_owner: str
    metadata_owner_code: int
    link_group: int
    references: tuple[str, ...]
    kind_token_id: int
    duration_frames: int
    duration_parameter: str | None
    parameter_count: int
    semantic_flags: int
    geometry_width: int
    geometry_height: int


@dataclass(frozen=True)
class RuntimeLink:
    source: str
    authored_reference: str
    target: str
    parameter_path: str
    reference_id: int
    direction: str
    dependency_policy: str
    provider: str
    consumer: str


@dataclass(frozen=True)
class RuntimeDependencyEdge:
    provider_index: int
    provider: str
    field_id: int
    field: str
    mask: int
    value: int
    flags: int


@dataclass(frozen=True)
class RuntimeDependencyGroup:
    group_id: int
    consumer_index: int
    consumer: str
    policy: str
    policy_code: int
    first_edge: int
    edges: tuple[RuntimeDependencyEdge, ...]
    result_field_id: int
    result_field: str
    result_mask: int
    result_value: int
    duration_frames: int
    flags: int


@dataclass(frozen=True)
class RuntimeCompletion:
    object_index: int
    authored_logical_id: str
    field_id: int
    field: str
    mask: int
    value: int


@dataclass(frozen=True)
class RuntimeEncounter:
    encounter_id: str
    directory_index: int
    numeric_id: int
    sector_code: int
    kind_code: int
    echo_cap: int
    tempo_bpm: int
    room: tuple[int, int, int, int]
    spawn: tuple[int, int]
    exit: tuple[int, int]
    rects: tuple[RuntimeRect, ...]
    objects: tuple[RuntimeObject, ...]
    references: tuple[RuntimeLink, ...]
    dependency_groups: tuple[RuntimeDependencyGroup, ...]
    dependency_edges: tuple[RuntimeDependencyEdge, ...]
    completion: RuntimeCompletion | None
    objective_count: int
    role_count: int
    phase_count: int
    flags: int
    chunk_offset: int


@dataclass(frozen=True)
class RuntimeArtifacts:
    files: Mapping[str, bytes]
    source_sha256: str
    contract_sha256: str
    assembly_sha256: str
    encounter_count: int


def _symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _require_record(payload: bytes, expected: int, name: str) -> None:
    if len(payload) != expected:
        raise RuntimeCompileError(
            f"{name} record is {len(payload)} bytes; expected {expected}"
        )


def _semantic_record_bytes(item: RuntimeObject) -> bytes:
    payload = struct.pack(
        ">BBHHHHBBHH",
        item.logical_id,
        item.kind_token_id,
        item.original_type_code,
        item.initial_state,
        item.timer,
        item.param,
        item.parameter_count,
        item.metadata_owner_code,
        item.duration_frames,
        item.semantic_flags,
    )
    _require_record(payload, SEMANTIC_RECORD_BYTES, "semantic")
    return payload


def decode_semantic_record(payload: bytes) -> dict[str, int]:
    _require_record(payload, SEMANTIC_RECORD_BYTES, "semantic")
    values = struct.unpack(">BBHHHHBBHH", payload)
    keys = (
        "object_index",
        "kind_token_id",
        "original_type_code",
        "initial_state",
        "initial_timer",
        "object_param",
        "parameter_count",
        "owner_code",
        "duration_frames",
        "flags",
    )
    return dict(zip(keys, values))


def _dependency_group_record_bytes(
    group: RuntimeDependencyGroup,
) -> bytes:
    payload = struct.pack(
        ">BBBBBBHHHHH",
        group.consumer_index,
        group.policy_code,
        group.first_edge,
        len(group.edges),
        group.result_field_id,
        0,
        group.result_mask,
        group.result_value,
        group.duration_frames,
        group.flags,
        0,
    )
    _require_record(payload, DEPENDENCY_GROUP_BYTES, "dependency group")
    return payload


def decode_dependency_group_record(payload: bytes) -> dict[str, int]:
    _require_record(payload, DEPENDENCY_GROUP_BYTES, "dependency group")
    values = struct.unpack(">BBBBBBHHHHH", payload)
    if values[5] or values[10]:
        raise RuntimeCompileError(
            "dependency group reserved fields must be zero"
        )
    keys = (
        "consumer_index",
        "policy_code",
        "first_edge",
        "edge_count",
        "result_field_id",
        "reserved_byte",
        "result_mask",
        "result_value",
        "duration_frames",
        "flags",
        "reserved_word",
    )
    return dict(zip(keys, values))


def _dependency_edge_record_bytes(edge: RuntimeDependencyEdge) -> bytes:
    payload = struct.pack(
        ">BBHHH",
        edge.provider_index,
        edge.field_id,
        edge.mask,
        edge.value,
        edge.flags,
    )
    _require_record(payload, DEPENDENCY_EDGE_BYTES, "dependency edge")
    return payload


def decode_dependency_edge_record(payload: bytes) -> dict[str, int]:
    _require_record(payload, DEPENDENCY_EDGE_BYTES, "dependency edge")
    values = struct.unpack(">BBHHH", payload)
    keys = (
        "provider_index",
        "field_id",
        "mask",
        "value",
        "flags",
    )
    return dict(zip(keys, values))


def decode_profile_record(payload: bytes) -> dict[str, int]:
    _require_record(payload, PROFILE_BYTES, "profile")
    values = struct.unpack(
        ">BBBBHHHHBBBBIHHHBBHHIIIIHHH10s", payload
    )
    if values[-1] != bytes(10):
        raise RuntimeCompileError("profile reserved tail must be zero")
    keys = (
        "directory_index",
        "sector_code",
        "kind_code",
        "echo_cap",
        "numeric_id",
        "tempo_bpm",
        "exit_x",
        "exit_y",
        "objective_count",
        "role_count",
        "phase_count",
        "flags",
        "chunk_pointer",
        "semantic_count",
        "dependency_group_count",
        "dependency_edge_count",
        "completion_object",
        "completion_field",
        "completion_mask",
        "completion_value",
        "semantic_pointer",
        "dependency_group_pointer",
        "dependency_edge_pointer",
        "semantic_token_pointer",
        "semantic_token_count",
        "reference_field_count",
        "completion_flags",
        "reserved_tail",
    )
    return dict(zip(keys, values))


def merge_collision_rectangles(
    grid: Sequence[str], *, cell_pixels: int, room_width: int, room_height: int
) -> tuple[RuntimeRect, ...]:
    """Merge solid metatile cells into deterministic vertical run rectangles."""

    if cell_pixels <= 0:
        raise RuntimeCompileError("collision metatile size must be positive")
    if not grid or not grid[0]:
        raise RuntimeCompileError("collision grid must not be empty")
    columns = len(grid[0])
    if any(len(row) != columns for row in grid):
        raise RuntimeCompileError("collision grid rows have inconsistent widths")
    if columns * cell_pixels != room_width or len(grid) * cell_pixels != room_height:
        raise RuntimeCompileError(
            "visual-metatile grid does not exactly cover the declared room"
        )

    active: dict[tuple[int, int], list[int]] = {}
    completed: list[list[int]] = []
    for row_index, row in enumerate(grid):
        spans: list[tuple[int, int]] = []
        column = 0
        while column < columns:
            if row[column] != "#":
                column += 1
                continue
            start = column
            while column < columns and row[column] == "#":
                column += 1
            spans.append((start, column))

        next_active: dict[tuple[int, int], list[int]] = {}
        for span in spans:
            if span in active:
                rectangle = active[span]
                rectangle[3] += 1
            else:
                rectangle = [
                    span[0],
                    row_index,
                    span[1] - span[0],
                    1,
                ]
            next_active[span] = rectangle
        for span, rectangle in active.items():
            if span not in next_active:
                completed.append(rectangle)
        active = next_active
    completed.extend(active.values())

    rectangles = tuple(
        RuntimeRect(
            x=item[0] * cell_pixels,
            y=item[1] * cell_pixels,
            width=item[2] * cell_pixels,
            height=item[3] * cell_pixels,
        )
        for item in completed
    )
    if len(rectangles) > STATIC_RECT_CAPACITY:
        raise RuntimeCompileError(
            f"merged collision geometry uses {len(rectangles)} rectangles; "
            f"limit is {STATIC_RECT_CAPACITY}"
        )
    for rectangle in rectangles:
        if (
            rectangle.x < 0
            or rectangle.y < 0
            or rectangle.width <= 0
            or rectangle.height <= 0
            or rectangle.x + rectangle.width > room_width
            or rectangle.y + rectangle.height > room_height
        ):
            raise RuntimeCompileError(f"invalid merged rectangle: {rectangle}")
    return rectangles


def _reference_strings(value: Any, path: str) -> Iterable[tuple[str, str]]:
    """Yield authored entity-like reference strings and their parameter paths."""

    if isinstance(value, str):
        for match in ENTITY_REFERENCE_RE.finditer(value):
            yield match.group(0), path
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _reference_strings(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key in sorted(value):
            yield from _reference_strings(value[key], f"{path}.{key}")


def _resolve_reference(
    authored_reference: str,
    *,
    indices_by_id: Mapping[str, tuple[int, ...]],
    encounter_id: str,
    parameter_path: str,
) -> int:
    resolved_reference = REFERENCE_ALIASES.get(
        authored_reference, authored_reference
    )
    exact = indices_by_id.get(resolved_reference, ())
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RuntimeCompileError(
            f"{encounter_id} {parameter_path} reference "
            f"{authored_reference!r} is ambiguous"
        )

    raise RuntimeCompileError(
        f"{encounter_id} {parameter_path} reference "
        f"{authored_reference!r} is unresolved"
    )


def _logical_indices(
    encounter_id: str,
    authored: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, int]:
    indices_by_id_mutable: dict[str, list[int]] = {}
    for index, (_category, entity) in enumerate(authored):
        indices_by_id_mutable.setdefault(str(entity["logical_id"]), []).append(index)
    duplicates = sorted(
        logical_id
        for logical_id, indices in indices_by_id_mutable.items()
        if len(indices) != 1
    )
    if duplicates:
        raise RuntimeCompileError(
            f"{encounter_id} has ambiguous entity logical IDs: {duplicates}"
        )
    return {
        logical_id: indices[0]
        for logical_id, indices in indices_by_id_mutable.items()
    }


def _compile_references(
    encounter_id: str,
    authored: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[tuple[RuntimeLink, ...], dict[int, tuple[str, ...]]]:
    logical_indices = _logical_indices(encounter_id, authored)
    indices_by_id = {
        logical_id: (index,) for logical_id, index in logical_indices.items()
    }
    links: list[RuntimeLink] = []
    references_by_source: dict[int, list[str]] = {
        index: [] for index in range(len(authored))
    }
    for source_index, (_category, entity) in enumerate(authored):
        parameters = entity.get("parameters", {})
        for parameter_key in sorted(parameters):
            discovered = tuple(
                _reference_strings(
                    parameters[parameter_key],
                    f"parameters.{parameter_key}",
                )
            )
            if not discovered:
                continue
            try:
                contract = REFERENCE_REGISTRY[parameter_key]
            except KeyError as error:
                raise RuntimeCompileError(
                    f"{encounter_id} reference-bearing parameter "
                    f"{parameter_key!r} has no direction contract"
                ) from error
            for authored_reference, parameter_path in discovered:
                target_index = _resolve_reference(
                    authored_reference,
                    indices_by_id=indices_by_id,
                    encounter_id=encounter_id,
                    parameter_path=(
                        f"entity {entity['logical_id']} {parameter_path}"
                    ),
                )
                if target_index == source_index:
                    raise RuntimeCompileError(
                        f"{encounter_id} entity {entity['logical_id']} has "
                        f"a self-reference at {parameter_path}"
                    )
                source = str(entity["logical_id"])
                target = str(authored[target_index][1]["logical_id"])
                if contract.direction == REFERENCE_SOURCE_TO_TARGET:
                    provider, consumer = source, target
                else:
                    provider, consumer = target, source
                links.append(
                    RuntimeLink(
                        source=source,
                        authored_reference=authored_reference,
                        target=target,
                        parameter_path=parameter_path,
                        reference_id=contract.reference_id,
                        direction=contract.direction,
                        dependency_policy=contract.dependency_policy,
                        provider=provider,
                        consumer=consumer,
                    )
                )
                references_by_source[source_index].append(target)
    return (
        tuple(links),
        {
            index: tuple(dict.fromkeys(targets))
            for index, targets in references_by_source.items()
        },
    )


def _compile_dependency_groups(
    encounter: Mapping[str, Any],
    authored: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[
    dict[int, int],
    tuple[RuntimeDependencyGroup, ...],
    tuple[RuntimeDependencyEdge, ...],
]:
    encounter_id = str(encounter["id"])
    logical_indices = _logical_indices(encounter_id, authored)
    policies = encounter.get("dependency_policy", ())
    if len(policies) > MAX_DEPENDENCY_GROUPS:
        raise RuntimeCompileError(
            f"{encounter_id} uses {len(policies)} dependency groups; "
            f"limit is {MAX_DEPENDENCY_GROUPS}"
        )
    group_by_consumer = {index: 0 for index in range(len(authored))}
    groups: list[RuntimeDependencyGroup] = []
    all_edges: list[RuntimeDependencyEdge] = []
    for group_id, policy in enumerate(policies, start=1):
        try:
            consumer_index = logical_indices[str(policy["consumer"])]
        except KeyError as error:
            raise RuntimeCompileError(
                f"{encounter_id} dependency consumer is unresolved"
            ) from error
        if group_by_consumer[consumer_index]:
            raise RuntimeCompileError(
                f"{encounter_id} dependency consumer "
                f"{policy['consumer']} has multiple groups"
            )
        group_by_consumer[consumer_index] = group_id
        consumer_parameters = authored[consumer_index][1].get(
            "parameters", {}
        )
        open_frames = consumer_parameters.get("open_frames")
        if (
            isinstance(open_frames, int)
            and not isinstance(open_frames, bool)
            and open_frames != int(policy["duration_frames"])
        ):
            raise RuntimeCompileError(
                f"{encounter_id} dependency duration for "
                f"{policy['consumer']} does not match authored open_frames"
            )
        policy_name = str(policy["policy"])
        if policy_name == DEPENDENCY_ALL:
            policy_code = 1
        elif policy_name == DEPENDENCY_ANY:
            policy_code = 2
        else:
            raise RuntimeCompileError(
                f"{encounter_id} dependency policy must be ALL or ANY"
            )
        first_edge = len(all_edges)
        edges: list[RuntimeDependencyEdge] = []
        for condition in policy["conditions"]:
            provider_name = str(condition["provider"])
            try:
                provider_index = logical_indices[provider_name]
            except KeyError as error:
                raise RuntimeCompileError(
                    f"{encounter_id} dependency provider "
                    f"{provider_name!r} is unresolved"
                ) from error
            state = condition["completion_state"]
            try:
                field = REFERENCE_FIELD_REGISTRY[str(state["field"])]
            except KeyError as error:
                raise RuntimeCompileError(
                    f"{encounter_id} dependency field is unsupported"
                ) from error
            edge = RuntimeDependencyEdge(
                provider_index=provider_index,
                provider=provider_name,
                field_id=field.field_id,
                field=field.name,
                mask=int(state["mask"]),
                value=int(state["value"]),
                flags=0,
            )
            edges.append(edge)
            all_edges.append(edge)
        if len(all_edges) > MAX_DEPENDENCY_EDGES:
            raise RuntimeCompileError(
                f"{encounter_id} uses {len(all_edges)} dependency edges; "
                f"limit is {MAX_DEPENDENCY_EDGES}"
            )
        result_state = policy["completion_state"]
        try:
            result_field = REFERENCE_FIELD_REGISTRY[
                str(result_state["field"])
            ]
        except KeyError as error:
            raise RuntimeCompileError(
                f"{encounter_id} dependency result field is unsupported"
            ) from error
        groups.append(
            RuntimeDependencyGroup(
                group_id=group_id,
                consumer_index=consumer_index,
                consumer=str(policy["consumer"]),
                policy=policy_name,
                policy_code=policy_code,
                first_edge=first_edge,
                edges=tuple(edges),
                result_field_id=result_field.field_id,
                result_field=result_field.name,
                result_mask=int(result_state["mask"]),
                result_value=int(result_state["value"]),
                duration_frames=int(policy["duration_frames"]),
                flags=1,
            )
        )
    return (
        group_by_consumer,
        tuple(groups),
        tuple(all_edges),
    )


def _hazard_radius(entity: Mapping[str, Any], encounter_id: str) -> int:
    parameters = entity.get("parameters", {})
    radius: Any = None
    for key in HAZARD_RADIUS_KEYS:
        if key in parameters:
            radius = parameters[key]
            break
    if radius is None:
        dimensions = [
            parameters[key]
            for key in ("width", "height")
            if key in parameters
        ]
        if dimensions:
            if any(isinstance(value, bool) or not isinstance(value, int)
                   for value in dimensions):
                raise RuntimeCompileError(
                    f"{encounter_id} hazard {entity['logical_id']} has "
                    "non-integer dimensions"
                )
            radius = (max(dimensions) + 1) // 2
        else:
            # Zero invokes the frozen vocabulary's explicit 12-pixel default.
            radius = 0
    if isinstance(radius, bool) or not isinstance(radius, int):
        raise RuntimeCompileError(
            f"{encounter_id} hazard {entity['logical_id']} radius "
            "must be an integer"
        )
    if not 0 <= radius <= 0xFF:
        raise RuntimeCompileError(
            f"{encounter_id} hazard {entity['logical_id']} radius "
            f"{radius} does not fit one byte"
        )
    return radius


def _hazard_geometry(
    entity: Mapping[str, Any], encounter_id: str
) -> tuple[int, int, int]:
    """Encode optional rectangular hazard bounds in unused semantic flags."""

    parameters = entity.get("parameters", {})
    width = parameters.get("width")
    height = parameters.get("height")
    if width is None and height is None:
        return 0, 0, 0
    if width is None or height is None:
        raise RuntimeCompileError(
            f"{encounter_id} hazard {entity['logical_id']} rectangular "
            "geometry requires both width and height"
        )
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width % SEMANTIC_GEOMETRY_UNIT_PX
        or height % SEMANTIC_GEOMETRY_UNIT_PX
    ):
        raise RuntimeCompileError(
            f"{encounter_id} hazard {entity['logical_id']} width/height "
            f"must be positive multiples of {SEMANTIC_GEOMETRY_UNIT_PX}"
        )
    width_units = width // SEMANTIC_GEOMETRY_UNIT_PX
    height_units = height // SEMANTIC_GEOMETRY_UNIT_PX
    if width_units > (
        SEMANTIC_FLAG_GEOMETRY_WIDTH_MASK
        >> SEMANTIC_FLAG_GEOMETRY_WIDTH_SHIFT
    ) or height_units > (
        SEMANTIC_FLAG_GEOMETRY_HEIGHT_MASK
        >> SEMANTIC_FLAG_GEOMETRY_HEIGHT_SHIFT
    ):
        raise RuntimeCompileError(
            f"{encounter_id} hazard {entity['logical_id']} rectangular "
            "geometry exceeds semantic flag capacity"
        )
    flags = (
        width_units << SEMANTIC_FLAG_GEOMETRY_WIDTH_SHIFT
    ) | (
        height_units << SEMANTIC_FLAG_GEOMETRY_HEIGHT_SHIFT
    )
    return width, height, flags


def _object_health(entity: Mapping[str, Any], encounter_id: str) -> int:
    health = entity.get("parameters", {}).get("health", 1)
    if isinstance(health, bool) or not isinstance(health, int):
        raise RuntimeCompileError(
            f"{encounter_id} object {entity['logical_id']} health "
            "must be an integer"
        )
    if not 1 <= health <= 0xFF:
        raise RuntimeCompileError(
            f"{encounter_id} object {entity['logical_id']} health "
            f"{health} does not fit the vocabulary high byte"
        )
    return health


def _kind_token_id(category: str, vocab_kind: int) -> int:
    domain_by_category = {
        "devices": "device_kind",
        "enemies": "enemy_kind",
        "hazards": "hazard_kind",
        "carryables": "carryable_kind",
    }
    domain = domain_by_category.get(category)
    if domain is None:
        return 0
    matches = [
        token.token_id
        for token in SEMANTIC_TOKEN_REGISTRY.values()
        if token.domain == domain and token.value == vocab_kind
    ]
    if len(matches) != 1:
        raise RuntimeCompileError(
            f"{category} vocabulary kind ${vocab_kind:04X} "
            f"has {len(matches)} semantic tokens"
        )
    return matches[0]


def _primary_duration(
    entity: Mapping[str, Any],
) -> tuple[int, str | None]:
    parameters = entity.get("parameters", {})
    for key in (
        "active_frames",
        "open_frames",
        "opening_frames",
        "interact_frames",
        "duration_frames",
        "occupancy_frames",
        "charge_frames",
        "fire_period_frames",
        "warning_frames",
    ):
        value = parameters.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            if not 0 <= value <= 0xFFFF:
                raise RuntimeCompileError(
                    f"semantic duration {key} does not fit one word"
                )
            return value, key
    return 0, None


def _compile_completion(
    encounter: Mapping[str, Any],
    authored: Sequence[tuple[str, Mapping[str, Any]]],
) -> RuntimeCompletion | None:
    stage_completion = encounter.get("stage_completion")
    if stage_completion is None:
        return None
    encounter_id = str(encounter["id"])
    logical_indices = _logical_indices(encounter_id, authored)
    logical_id = str(stage_completion["object"])
    try:
        object_index = logical_indices[logical_id]
    except KeyError as error:
        raise RuntimeCompileError(
            f"{encounter_id} completion object {logical_id!r} is unresolved"
        ) from error
    state = stage_completion["completion_state"]
    try:
        field = REFERENCE_FIELD_REGISTRY[str(state["field"])]
    except KeyError as error:
        raise RuntimeCompileError(
            f"{encounter_id} completion field is unsupported"
        ) from error
    return RuntimeCompletion(
        object_index=object_index,
        authored_logical_id=logical_id,
        field_id=field.field_id,
        field=field.name,
        mask=int(state["mask"]),
        value=int(state["value"]),
    )


def _flatten_entities(
    encounter: Mapping[str, Any],
) -> tuple[
    tuple[RuntimeObject, ...],
    tuple[RuntimeLink, ...],
    tuple[RuntimeDependencyGroup, ...],
    tuple[RuntimeDependencyEdge, ...],
    RuntimeCompletion | None,
]:
    encounter_id = str(encounter["id"])
    authored = tuple(
        (category, entity)
        for category in ENTITY_CATEGORIES
        for entity in encounter["entities"][category]
    )
    if len(authored) > MAX_ENTITIES:
        raise RuntimeCompileError(
            f"{encounter_id} uses {len(authored)} entities; limit is {MAX_ENTITIES}"
        )
    references, references_by_source = _compile_references(
        encounter_id, authored
    )
    group_by_index, dependency_groups, dependency_edges = (
        _compile_dependency_groups(encounter, authored)
    )
    completion = _compile_completion(encounter, authored)

    objects: list[RuntimeObject] = []
    pool_counts = {pool_type: 0 for pool_type in POOL_CAPACITY_BY_TYPE}
    for logical_id, (category, entity) in enumerate(authored):
        if not 0 <= logical_id <= 0xFF:
            raise RuntimeCompileError(
                f"{encounter_id} entity logical ID exceeds one byte"
            )
        entity_type = str(entity["type"])
        try:
            contract = ENTITY_VOCABULARY_MANIFEST[entity_type]
        except KeyError as error:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} type "
                f"{entity_type!r} has no vocabulary contract"
            ) from error
        if contract.category != category:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} category "
                "does not match its vocabulary contract"
            )
        pool_type = POOL_TYPE_BY_CATEGORY[category]
        if contract.pool_type != pool_type:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} pool "
                "does not match its vocabulary contract"
            )

        state_map = contract.state_map()
        authored_states = tuple(entity["state"]["states"])
        missing_states = sorted(set(authored_states) - set(state_map))
        if missing_states:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} has "
                f"unmapped named states: {missing_states}"
            )
        initial_state_name = str(entity["state"]["initial"])
        try:
            state_base_bits = state_map[initial_state_name]
        except KeyError as error:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} initial "
                f"state {initial_state_name!r} has no vocabulary mapping"
            ) from error
        initial_state = state_base_bits
        has_health = "health" in entity.get("parameters", {})
        if category == "enemies" or (category == "devices" and has_health):
            initial_state |= _object_health(entity, encounter_id) << 8
        if not 0 <= initial_state <= 0xFFFF:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} state "
                "bitfield does not fit one word"
            )

        link_group = group_by_index[logical_id]
        if contract.param_semantic == PARAM_DEVICE_LINK:
            param_low = link_group
        elif contract.param_semantic == PARAM_DEVICE_TARGET:
            # The repair interaction searches live pools by the globally
            # unique authored logical byte.  Until content authors expose a
            # distinct repair target, the repair terminal targets itself.
            param_low = logical_id
        elif contract.param_semantic == PARAM_HAZARD_RADIUS:
            param_low = _hazard_radius(entity, encounter_id)
        elif contract.param_semantic == PARAM_UNUSED:
            param_low = 0
        else:
            raise RuntimeCompileError(
                f"{encounter_id} entity {entity['logical_id']} has "
                f"unsupported parameter semantic {contract.param_semantic!r}"
            )
        if contract.vocab_kind & 0x00FF:
            raise RuntimeCompileError(
                f"{entity_type} vocabulary kind is not high-byte aligned"
            )
        if not 0 <= contract.vocab_kind <= 0xFF00:
            raise RuntimeCompileError(
                f"{entity_type} vocabulary kind does not fit one word"
            )
        param = contract.vocab_kind | param_low
        kind_token_id = _kind_token_id(category, contract.vocab_kind)
        duration_frames, duration_parameter = _primary_duration(entity)
        try:
            owner_code = METADATA_OWNER_CODE[contract.owner]
        except KeyError as error:
            raise RuntimeCompileError(
                f"{entity_type} has unsupported metadata owner "
                f"{contract.owner!r}"
            ) from error
        if contract.deferred and (
            contract.owner != OWNER_FUTURE_BOSS
            or contract.vocab_kind != 0
            or state_base_bits != 0
        ):
            raise RuntimeCompileError(
                f"{encounter_id} deferred boss {entity['logical_id']} "
                "violates future-controller ownership"
            )

        geometry_width = 0
        geometry_height = 0
        geometry_flags = 0
        if category == "hazards":
            (
                geometry_width,
                geometry_height,
                geometry_flags,
            ) = _hazard_geometry(entity, encounter_id)

        semantic_flags = geometry_flags
        if contract.deferred:
            semantic_flags |= 0x0001
        if link_group:
            semantic_flags |= 0x0002
        if completion is not None and completion.object_index == logical_id:
            semantic_flags |= 0x0004
        if entity["parameters"]:
            semantic_flags |= 0x0008
        if has_health:
            semantic_flags |= 0x0010
        objects.append(
            RuntimeObject(
                pool_type=pool_type,
                logical_id=logical_id,
                authored_logical_id=str(entity["logical_id"]),
                category=category,
                entity_type=entity_type,
                initial_state_name=initial_state_name,
                state_base_bits=state_base_bits,
                initial_state=initial_state,
                x=int(entity["x"]),
                y=int(entity["y"]),
                timer=0,
                param=param,
                param_low=param_low,
                param_semantic=contract.param_semantic,
                original_type_code=contract.original_type_code,
                metadata_owner=contract.owner,
                metadata_owner_code=owner_code,
                link_group=link_group,
                references=references_by_source[logical_id],
                kind_token_id=kind_token_id,
                duration_frames=duration_frames,
                duration_parameter=duration_parameter,
                parameter_count=len(entity["parameters"]),
                semantic_flags=semantic_flags,
                geometry_width=geometry_width,
                geometry_height=geometry_height,
            )
        )
        pool_counts[pool_type] += 1

    for pool_type, count in pool_counts.items():
        capacity = POOL_CAPACITY_BY_TYPE[pool_type]
        if count > capacity:
            raise RuntimeCompileError(
                f"{encounter_id} pool {pool_type} uses {count}; "
                f"capacity is {capacity}"
            )
    return (
        tuple(objects),
        references,
        dependency_groups,
        dependency_edges,
        completion,
    )


def _build_encounters(
    content_root: Path,
) -> tuple[tuple[RuntimeEncounter, ...], str]:
    validate_manifest()
    validated = load_and_validate(content_root)
    encounters: list[RuntimeEncounter] = []
    encountered_types: set[str] = set()
    for directory_index, (roster, encounter) in enumerate(
        zip(ROSTER, validated.encounters)
    ):
        room = encounter["room"]
        width = int(room["width_px"])
        height = int(room["height_px"])
        rects = merge_collision_rectangles(
            room["tile_grid"],
            cell_pixels=int(room["visual_metatile_px"]),
            room_width=width,
            room_height=height,
        )
        (
            objects,
            references,
            dependency_groups,
            dependency_edges,
            completion,
        ) = _flatten_entities(encounter)
        encountered_types.update(item.entity_type for item in objects)
        flags = 0
        if encounter["kind"] == "boss":
            flags |= 0x01
        if any(
            entity["anchor_policy"] != "forbidden"
            for category in ENTITY_CATEGORIES
            for entity in encounter["entities"][category]
        ):
            flags |= 0x02
        if encounter.get("deterministic_response_policy"):
            flags |= 0x04

        # Offsets are exported by the authoritative compiler.  Recompute their
        # stable label reference at assembly time instead of duplicating binary
        # packing logic here; the numeric report uses zero until integration
        # resolves the generated CONTENT_ENCOUNTER_* constant.
        encounters.append(
            RuntimeEncounter(
                encounter_id=roster.encounter_id,
                directory_index=directory_index,
                numeric_id=roster.numeric_id,
                sector_code=SECTOR_CODES[roster.sector],
                kind_code=KIND_CODES[roster.kind],
                echo_cap=int(encounter["echoes"]["cap"]),
                tempo_bpm=int(encounter["presentation"]["tempo_bpm"]),
                room=(0, 0, width, height),
                spawn=(int(room["spawn"]["x"]), int(room["spawn"]["y"])),
                exit=(int(room["exit"]["x"]), int(room["exit"]["y"])),
                rects=rects,
                objects=objects,
                references=references,
                dependency_groups=dependency_groups,
                dependency_edges=dependency_edges,
                completion=completion,
                objective_count=len(encounter["objective_graph"]["nodes"]),
                role_count=len(encounter["canonical_role_schedule"]),
                phase_count=len(encounter["boss_phases"]),
                flags=flags,
                chunk_offset=0,
            )
        )
    expected_types = set(ENTITY_VOCABULARY_MANIFEST)
    if encountered_types != expected_types:
        missing = sorted(expected_types - encountered_types)
        extra = sorted(encountered_types - expected_types)
        raise RuntimeCompileError(
            "campaign runtime type coverage drift: "
            f"missing={missing}, extra={extra}"
        )
    return tuple(encounters), validated.source_sha256


def _assembly(
    encounters: Sequence[RuntimeEncounter],
    source_sha256: str,
    contract_sha256: str,
) -> bytes:
    lines = [
        "; Generated by tools.campaign_runtime. Do not edit.",
        f"; authoritative content source sha256: {source_sha256}",
        f"; vocabulary adapter contract sha256: {contract_sha256}",
        "",
        f"CAMPAIGN_RUNTIME_FORMAT_VERSION equ ${FORMAT_VERSION:04X}",
        f"CAMPAIGN_RUNTIME_ENCOUNTER_COUNT equ {len(encounters)}",
        f"CAMPAIGN_RUNTIME_PROFILE_BYTES equ {PROFILE_BYTES}",
        f"CAMPAIGN_RUNTIME_SEMANTIC_BYTES equ {SEMANTIC_RECORD_BYTES}",
        f"CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES equ {DEPENDENCY_GROUP_BYTES}",
        f"CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES equ {DEPENDENCY_EDGE_BYTES}",
        f"CAMPAIGN_RUNTIME_OBJECT_METADATA_BYTES equ {OBJECT_METADATA_BYTES}",
        f"CAMPAIGN_RUNTIME_SEMANTIC_TOKEN_COUNT equ "
        f"{len(SEMANTIC_TOKEN_REGISTRY)}",
        f"CAMPAIGN_RUNTIME_REFERENCE_FIELD_COUNT equ "
        f"{len(REFERENCE_FIELD_REGISTRY)}",
        "CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_UNIT_PX equ 8",
        "CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_WIDTH_SHIFT equ 5",
        "CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_WIDTH_MASK equ $07E0",
        "CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_HEIGHT_SHIFT equ 11",
        "CAMPAIGN_RUNTIME_SEMANTIC_GEOMETRY_HEIGHT_MASK equ $F800",
        "CAMPAIGN_RUNTIME_TYPE_CLOSING_DOOR_CRUSH_ZONE equ "
        f"${ENTITY_VOCABULARY_MANIFEST['closing_door_crush_zone'].original_type_code:04X}",
        "CAMPAIGN_RUNTIME_METADATA_OWNER_VOCABULARY equ $01",
        "CAMPAIGN_RUNTIME_METADATA_OWNER_FUTURE_BOSS equ $02",
        "CAMPAIGN_RUNTIME_PROFILE_INDEX equ 0",
        "CAMPAIGN_RUNTIME_PROFILE_SECTOR equ 1",
        "CAMPAIGN_RUNTIME_PROFILE_KIND equ 2",
        "CAMPAIGN_RUNTIME_PROFILE_ECHO_CAP equ 3",
        "CAMPAIGN_RUNTIME_PROFILE_NUMERIC_ID equ 4",
        "CAMPAIGN_RUNTIME_PROFILE_TEMPO equ 6",
        "CAMPAIGN_RUNTIME_PROFILE_EXIT_X equ 8",
        "CAMPAIGN_RUNTIME_PROFILE_EXIT_Y equ 10",
        "CAMPAIGN_RUNTIME_PROFILE_OBJECTIVES equ 12",
        "CAMPAIGN_RUNTIME_PROFILE_ROLES equ 13",
        "CAMPAIGN_RUNTIME_PROFILE_PHASES equ 14",
        "CAMPAIGN_RUNTIME_PROFILE_FLAGS equ 15",
        "CAMPAIGN_RUNTIME_PROFILE_CHUNK_PTR equ 16",
        "CAMPAIGN_RUNTIME_PROFILE_SEMANTIC_COUNT equ 20",
        "CAMPAIGN_RUNTIME_PROFILE_GROUP_COUNT equ 22",
        "CAMPAIGN_RUNTIME_PROFILE_EDGE_COUNT equ 24",
        "CAMPAIGN_RUNTIME_PROFILE_COMPLETION_OBJECT equ 26",
        "CAMPAIGN_RUNTIME_PROFILE_COMPLETION_FIELD equ 27",
        "CAMPAIGN_RUNTIME_PROFILE_COMPLETION_MASK equ 28",
        "CAMPAIGN_RUNTIME_PROFILE_COMPLETION_VALUE equ 30",
        "CAMPAIGN_RUNTIME_PROFILE_SEMANTIC_PTR equ 32",
        "CAMPAIGN_RUNTIME_PROFILE_GROUP_PTR equ 36",
        "CAMPAIGN_RUNTIME_PROFILE_EDGE_PTR equ 40",
        "CAMPAIGN_RUNTIME_PROFILE_TOKEN_PTR equ 44",
        "CAMPAIGN_RUNTIME_PROFILE_TOKEN_COUNT equ 48",
        "CAMPAIGN_RUNTIME_PROFILE_REFERENCE_FIELD_COUNT equ 50",
        "CAMPAIGN_RUNTIME_PROFILE_COMPLETION_FLAGS equ 52",
        "CAMPAIGN_RUNTIME_SEMANTIC_OBJECT equ 0",
        "CAMPAIGN_RUNTIME_SEMANTIC_KIND_TOKEN equ 1",
        "CAMPAIGN_RUNTIME_SEMANTIC_TYPE_CODE equ 2",
        "CAMPAIGN_RUNTIME_SEMANTIC_INITIAL_STATE equ 4",
        "CAMPAIGN_RUNTIME_SEMANTIC_INITIAL_TIMER equ 6",
        "CAMPAIGN_RUNTIME_SEMANTIC_OBJECT_PARAM equ 8",
        "CAMPAIGN_RUNTIME_SEMANTIC_PARAMETER_COUNT equ 10",
        "CAMPAIGN_RUNTIME_SEMANTIC_OWNER equ 11",
        "CAMPAIGN_RUNTIME_SEMANTIC_DURATION equ 12",
        "CAMPAIGN_RUNTIME_SEMANTIC_FLAGS equ 14",
        "CAMPAIGN_RUNTIME_GROUP_CONSUMER equ 0",
        "CAMPAIGN_RUNTIME_GROUP_POLICY equ 1",
        "CAMPAIGN_RUNTIME_GROUP_FIRST_EDGE equ 2",
        "CAMPAIGN_RUNTIME_GROUP_EDGE_COUNT equ 3",
        "CAMPAIGN_RUNTIME_GROUP_RESULT_FIELD equ 4",
        "CAMPAIGN_RUNTIME_GROUP_RESULT_MASK equ 6",
        "CAMPAIGN_RUNTIME_GROUP_RESULT_VALUE equ 8",
        "CAMPAIGN_RUNTIME_GROUP_DURATION equ 10",
        "CAMPAIGN_RUNTIME_GROUP_FLAGS equ 12",
        "CAMPAIGN_RUNTIME_EDGE_PROVIDER equ 0",
        "CAMPAIGN_RUNTIME_EDGE_FIELD equ 1",
        "CAMPAIGN_RUNTIME_EDGE_MASK equ 2",
        "CAMPAIGN_RUNTIME_EDGE_VALUE equ 4",
        "CAMPAIGN_RUNTIME_EDGE_FLAGS equ 6",
        "CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ALL equ $01",
        "CAMPAIGN_RUNTIME_DEPENDENCY_POLICY_ANY equ $02",
        "CAMPAIGN_RUNTIME_NO_COMPLETION_OBJECT equ $FF",
        "CAMPAIGN_RUNTIME_FLAG_BOSS equ $01",
        "CAMPAIGN_RUNTIME_FLAG_ANCHORS equ $02",
        "CAMPAIGN_RUNTIME_FLAG_RESPONSE_POLICY equ $04",
    ]
    for token in sorted(
        SEMANTIC_TOKEN_REGISTRY.values(), key=lambda item: item.token_id
    ):
        lines.append(
            f"CAMPAIGN_RUNTIME_TOKEN_{token.name} equ ${token.token_id:02X}"
        )
    for field in sorted(
        REFERENCE_FIELD_REGISTRY.values(), key=lambda item: item.field_id
    ):
        lines.append(
            f"CAMPAIGN_RUNTIME_REFERENCE_{_symbol(field.name)} "
            f"equ ${field.field_id:02X}"
        )
    lines.extend(
        (
            "",
            "    even",
            "CampaignRuntimeSemanticTokenTable:",
        )
    )
    domain_codes = {
        "device_kind": 1,
        "enemy_kind": 2,
        "hazard_kind": 3,
        "carryable_kind": 4,
        "object_state": 5,
        "actor_state": 6,
        "projectile_state": 7,
        "anchor_type": 8,
        "stage_rank": 9,
        "result": 10,
    }
    for token in sorted(
        SEMANTIC_TOKEN_REGISTRY.values(), key=lambda item: item.token_id
    ):
        lines.append(
            f"    dc.b {token.token_id},{domain_codes[token.domain]}"
        )
        lines.append(f"    dc.w ${token.value:04X}")
    lines.extend(
        (
            "CampaignRuntimeSemanticTokenTableEnd:",
            "",
            "CampaignRuntimeReferenceFieldTable:",
        )
    )
    for field in sorted(
        REFERENCE_FIELD_REGISTRY.values(), key=lambda item: item.field_id
    ):
        lines.append(
            f"    dc.b {field.field_id},{field.width_bytes},"
            f"{field.object_offset},{field.shift}"
        )
    lines.extend(
        (
            "CampaignRuntimeReferenceFieldTableEnd:",
            "",
            "CampaignRuntimeDescriptorTable:",
        )
    )
    for encounter in encounters:
        lines.append(f"    dc.l CampaignRuntimeDescriptor{encounter.encounter_id}")
    lines.extend(
        (
            "CampaignRuntimeDescriptorTableEnd:",
            "",
            "CampaignRuntimeProfileTable:",
        )
    )
    for encounter in encounters:
        lines.append(f"    dc.l CampaignRuntimeProfile{encounter.encounter_id}")
    lines.extend(
        (
            "CampaignRuntimeProfileTableEnd:",
            "",
            "CampaignRuntimeObjectMetadataTable:",
        )
    )
    for encounter in encounters:
        lines.append(
            f"    dc.l CampaignRuntimeObjectMetadata{encounter.encounter_id}"
        )
    lines.extend(("CampaignRuntimeObjectMetadataTableEnd:", ""))

    for encounter in encounters:
        encounter_id = encounter.encounter_id
        room_left, room_top, room_right, room_bottom = encounter.room
        lines.extend(
            (
                "    even",
                f"CampaignRuntimeDescriptor{encounter_id}:",
                f"    dc.w {encounter.directory_index},$0000",
                "    dc.w ENCOUNTER_DESCRIPTOR_VERSION_VALUE",
                "    dc.w ENCOUNTER_DESCRIPTOR_SIZE",
                f"    dc.w {room_left},{room_top},{room_right},{room_bottom}",
                f"    dc.w {encounter.spawn[0]},{encounter.spawn[1]}",
                f"    dc.w {len(encounter.rects)},{len(encounter.objects)}",
                f"    dc.l CampaignRuntimeRects{encounter_id}",
                f"    dc.l CampaignRuntimeObjects{encounter_id}",
                f"CampaignRuntimeDescriptor{encounter_id}End:",
                "",
                f"CampaignRuntimeRects{encounter_id}:",
            )
        )
        for rectangle in encounter.rects:
            lines.append(
                f"    dc.w {rectangle.x},{rectangle.y},"
                f"{rectangle.width},{rectangle.height}"
            )
        lines.extend((f"CampaignRuntimeRects{encounter_id}End:", ""))
        lines.append(f"CampaignRuntimeObjects{encounter_id}:")
        for item in encounter.objects:
            lines.append(
                f"    ; {item.logical_id}: {item.authored_logical_id} "
                f"{item.entity_type} state={item.initial_state_name}"
            )
            lines.extend(
                (
                    f"    dc.b {item.pool_type},{item.logical_id}",
                    f"    dc.w ${item.initial_state:04X},{item.x},{item.y},"
                    f"${item.timer:04X},${item.param:04X}",
                )
            )
        lines.extend(
            (
                f"CampaignRuntimeObjects{encounter_id}End:",
                "",
                f"CampaignRuntimeObjectMetadata{encounter_id}:",
            )
        )
        for item in encounter.objects:
            lines.append(
                f"    ; {item.logical_id}: {item.authored_logical_id} "
                f"owner={item.metadata_owner}"
            )
            lines.extend(
                (
                    f"    dc.w ${item.original_type_code:04X}",
                    f"    dc.b {item.metadata_owner_code},{item.link_group}",
                )
            )
        lines.extend(
            (
                f"CampaignRuntimeObjectMetadata{encounter_id}End:",
                "",
                f"CampaignRuntimeSemantics{encounter_id}:",
            )
        )
        for item in encounter.objects:
            lines.append(
                f"    ; {item.logical_id}: {item.authored_logical_id} "
                f"duration={item.duration_parameter or 'none'}"
            )
            lines.extend(
                (
                    f"    dc.b {item.logical_id},{item.kind_token_id}",
                    f"    dc.w ${item.original_type_code:04X},"
                    f"${item.initial_state:04X},${item.timer:04X},"
                    f"${item.param:04X}",
                    f"    dc.b {item.parameter_count},"
                    f"{item.metadata_owner_code}",
                    f"    dc.w {item.duration_frames},"
                    f"${item.semantic_flags:04X}",
                )
            )
        lines.extend(
            (
                f"CampaignRuntimeSemantics{encounter_id}End:",
                "",
                f"CampaignRuntimeDependencyGroups{encounter_id}:",
            )
        )
        for group in encounter.dependency_groups:
            lines.append(
                f"    ; group {group.group_id}: {group.policy} -> "
                f"{group.consumer}"
            )
            lines.extend(
                (
                    f"    dc.b {group.consumer_index},{group.policy_code},"
                    f"{group.first_edge},{len(group.edges)}",
                    f"    dc.b {group.result_field_id},0",
                    f"    dc.w ${group.result_mask:04X},"
                    f"${group.result_value:04X},{group.duration_frames},"
                    f"${group.flags:04X},$0000",
                )
            )
        lines.extend(
            (
                f"CampaignRuntimeDependencyGroups{encounter_id}End:",
                "",
                f"CampaignRuntimeDependencyEdges{encounter_id}:",
            )
        )
        for edge in encounter.dependency_edges:
            lines.append(
                f"    ; {edge.provider} {edge.field} & "
                f"${edge.mask:04X} == ${edge.value:04X}"
            )
            lines.extend(
                (
                    f"    dc.b {edge.provider_index},{edge.field_id}",
                    f"    dc.w ${edge.mask:04X},${edge.value:04X},"
                    f"${edge.flags:04X}",
                )
            )
        lines.extend(
            (f"CampaignRuntimeDependencyEdges{encounter_id}End:", "")
        )
        chunk_symbol = f"CONTENT_ENCOUNTER_{encounter_id}_CHUNK_OFFSET"
        completion = encounter.completion
        completion_object = completion.object_index if completion else 0xFF
        completion_field = completion.field_id if completion else 0
        completion_mask = completion.mask if completion else 0
        completion_value = completion.value if completion else 0
        completion_flags = 1 if completion else 0
        lines.extend(
            (
                f"CampaignRuntimeProfile{encounter_id}:",
                f"    dc.b {encounter.directory_index},{encounter.sector_code},"
                f"{encounter.kind_code},{encounter.echo_cap}",
                f"    dc.w {encounter.numeric_id},{encounter.tempo_bpm},"
                f"{encounter.exit[0]},{encounter.exit[1]}",
                f"    dc.b {encounter.objective_count},{encounter.role_count},"
                f"{encounter.phase_count},${encounter.flags:02X}",
                f"    dc.l ContentCampaignBinary+{chunk_symbol}",
                f"    dc.w {len(encounter.objects)},"
                f"{len(encounter.dependency_groups)},"
                f"{len(encounter.dependency_edges)}",
                f"    dc.b {completion_object},{completion_field}",
                f"    dc.w ${completion_mask:04X},${completion_value:04X}",
                f"    dc.l CampaignRuntimeSemantics{encounter_id},"
                f"CampaignRuntimeDependencyGroups{encounter_id},"
                f"CampaignRuntimeDependencyEdges{encounter_id},"
                "CampaignRuntimeSemanticTokenTable",
                f"    dc.w {len(SEMANTIC_TOKEN_REGISTRY)},"
                f"{len(REFERENCE_FIELD_REGISTRY)},${completion_flags:04X}",
                "    dcb.b 10,0",
                f"CampaignRuntimeProfile{encounter_id}End:",
                "",
                f"    assert CampaignRuntimeDescriptor{encounter_id}End-"
                f"CampaignRuntimeDescriptor{encounter_id}=ENCOUNTER_DESCRIPTOR_SIZE,"
                f'"{encounter_id} descriptor size mismatch"',
                f"    assert CampaignRuntimeRects{encounter_id}End-"
                f"CampaignRuntimeRects{encounter_id}<="
                f"STATIC_RECT_CAPACITY*STATIC_RECT_BYTES,"
                f'"{encounter_id} rectangle overflow"',
                f"    assert CampaignRuntimeObjects{encounter_id}End-"
                f"CampaignRuntimeObjects{encounter_id}="
                f"{len(encounter.objects)}*OBJECT_INIT_BYTES,"
                f'"{encounter_id} object table size mismatch"',
                f"    assert CampaignRuntimeObjects{encounter_id}End-"
                f"CampaignRuntimeObjects{encounter_id}<="
                f"TOTAL_POOL_CAPACITY*OBJECT_INIT_BYTES,"
                f'"{encounter_id} object overflow"',
                f"    assert CampaignRuntimeObjectMetadata{encounter_id}End-"
                f"CampaignRuntimeObjectMetadata{encounter_id}="
                f"{len(encounter.objects)}*"
                "CAMPAIGN_RUNTIME_OBJECT_METADATA_BYTES,"
                f'"{encounter_id} object metadata size mismatch"',
                f"    assert CampaignRuntimeSemantics{encounter_id}End-"
                f"CampaignRuntimeSemantics{encounter_id}="
                f"{len(encounter.objects)}*CAMPAIGN_RUNTIME_SEMANTIC_BYTES,"
                f'"{encounter_id} semantic table size mismatch"',
                f"    assert CampaignRuntimeDependencyGroups{encounter_id}End-"
                f"CampaignRuntimeDependencyGroups{encounter_id}="
                f"{len(encounter.dependency_groups)}*"
                "CAMPAIGN_RUNTIME_DEPENDENCY_GROUP_BYTES,"
                f'"{encounter_id} dependency group size mismatch"',
                f"    assert CampaignRuntimeDependencyEdges{encounter_id}End-"
                f"CampaignRuntimeDependencyEdges{encounter_id}="
                f"{len(encounter.dependency_edges)}*"
                "CAMPAIGN_RUNTIME_DEPENDENCY_EDGE_BYTES,"
                f'"{encounter_id} dependency edge size mismatch"',
                f"    assert CampaignRuntimeProfile{encounter_id}End-"
                f"CampaignRuntimeProfile{encounter_id}=CAMPAIGN_RUNTIME_PROFILE_BYTES,"
                f'"{encounter_id} profile size mismatch"',
                "",
            )
        )
    lines.extend(
        (
            "    assert CampaignRuntimeSemanticTokenTableEnd-"
            "CampaignRuntimeSemanticTokenTable="
            "CAMPAIGN_RUNTIME_SEMANTIC_TOKEN_COUNT*4,"
            '"semantic token table size mismatch"',
            "    assert CampaignRuntimeReferenceFieldTableEnd-"
            "CampaignRuntimeReferenceFieldTable="
            "CAMPAIGN_RUNTIME_REFERENCE_FIELD_COUNT*4,"
            '"reference field table size mismatch"',
            "    assert CampaignRuntimeDescriptorTableEnd-"
            "CampaignRuntimeDescriptorTable="
            "CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"
            '"campaign descriptor table size mismatch"',
            "    assert CampaignRuntimeProfileTableEnd-CampaignRuntimeProfileTable="
            "CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"
            '"campaign profile table size mismatch"',
            "    assert CampaignRuntimeObjectMetadataTableEnd-"
            "CampaignRuntimeObjectMetadataTable="
            "CAMPAIGN_RUNTIME_ENCOUNTER_COUNT*4,"
            '"campaign object metadata table size mismatch"',
            "",
        )
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _report(
    encounters: Sequence[RuntimeEncounter],
    *,
    source_sha256: str,
    contract_sha256: str,
    assembly: bytes,
) -> bytes:
    encounter_rows = []
    for encounter in encounters:
        pool_counts: dict[str, int] = {}
        for item in encounter.objects:
            key = str(item.pool_type)
            pool_counts[key] = pool_counts.get(key, 0) + 1
        object_rows = [
            {
                "object_index": item.logical_id,
                "authored_logical_id": item.authored_logical_id,
                "category": item.category,
                "entity_type": item.entity_type,
                "pool_type": item.pool_type,
                "initial_state_name": item.initial_state_name,
                "state_base_bits": item.state_base_bits,
                "object_state": item.initial_state,
                "object_timer": item.timer,
                "vocab_kind": item.param & 0xFF00,
                "param_low": item.param_low,
                "param_semantic": item.param_semantic,
                "object_param": item.param,
                "dependency_group": item.link_group,
                "references": list(item.references),
                "semantic_record": {
                    "kind_token_id": item.kind_token_id,
                    "duration_frames": item.duration_frames,
                    "duration_parameter": item.duration_parameter,
                    "authored_parameter_count": item.parameter_count,
                    "flags": item.semantic_flags,
                    "geometry_width": item.geometry_width,
                    "geometry_height": item.geometry_height,
                    "encoded_hex": _semantic_record_bytes(item).hex(),
                },
                "metadata": {
                    "original_type_code": item.original_type_code,
                    "owner": item.metadata_owner,
                    "owner_code": item.metadata_owner_code,
                    "dependency_group": item.link_group,
                },
            }
            for item in encounter.objects
        ]
        reference_rows = [
            {
                "source": link.source,
                "authored_reference": link.authored_reference,
                "target": link.target,
                "parameter_path": link.parameter_path,
                "reference_id": link.reference_id,
                "direction": link.direction,
                "dependency_policy": link.dependency_policy,
                "provider": link.provider,
                "consumer": link.consumer,
            }
            for link in encounter.references
        ]
        dependency_rows = [
            {
                "group_id": group.group_id,
                "consumer_index": group.consumer_index,
                "consumer": group.consumer,
                "policy": group.policy,
                "policy_code": group.policy_code,
                "first_edge": group.first_edge,
                "result": {
                    "field_id": group.result_field_id,
                    "field": group.result_field,
                    "mask": group.result_mask,
                    "value": group.result_value,
                },
                "duration_frames": group.duration_frames,
                "flags": group.flags,
                "encoded_hex": _dependency_group_record_bytes(group).hex(),
                "edges": [
                    {
                        "provider_index": edge.provider_index,
                        "provider": edge.provider,
                        "field_id": edge.field_id,
                        "field": edge.field,
                        "mask": edge.mask,
                        "value": edge.value,
                        "flags": edge.flags,
                        "encoded_hex": _dependency_edge_record_bytes(
                            edge
                        ).hex(),
                    }
                    for edge in group.edges
                ],
            }
            for group in encounter.dependency_groups
        ]
        completion = (
            {
                "object_index": encounter.completion.object_index,
                "object": encounter.completion.authored_logical_id,
                "field_id": encounter.completion.field_id,
                "field": encounter.completion.field,
                "mask": encounter.completion.mask,
                "value": encounter.completion.value,
            }
            if encounter.completion is not None
            else None
        )
        encounter_rows.append(
            {
                "id": encounter.encounter_id,
                "directory_index": encounter.directory_index,
                "numeric_id": encounter.numeric_id,
                "rectangles": len(encounter.rects),
                "entities": len(encounter.objects),
                "pool_counts": pool_counts,
                "objectives": encounter.objective_count,
                "roles": encounter.role_count,
                "boss_phases": encounter.phase_count,
                "echo_cap": encounter.echo_cap,
                "tempo_bpm": encounter.tempo_bpm,
                "flags": encounter.flags,
                "metadata_entries": len(encounter.objects),
                "semantic_entries": len(encounter.objects),
                "objects": object_rows,
                "authored_references": reference_rows,
                "dependency_groups": dependency_rows,
                "dependency_edges": len(encounter.dependency_edges),
                "stage_completion": completion,
                "status": "pass",
            }
        )
    contracts = []
    for entity_type in sorted(ENTITY_VOCABULARY_MANIFEST):
        contract = ENTITY_VOCABULARY_MANIFEST[entity_type]
        contracts.append(
            {
                "entity_type": entity_type,
                "category": contract.category,
                "pool_type": contract.pool_type,
                "vocab_kind": contract.vocab_kind,
                "param_semantic": contract.param_semantic,
                "owner": contract.owner,
                "deferred": contract.deferred,
                "original_type_code": contract.original_type_code,
                "state_bits": {
                    name: bits for name, bits in contract.state_bits
                },
            }
        )
    value = {
        "schema": "recursion94.campaign-runtime-report",
        "schema_version": 3,
        "format_version": FORMAT_VERSION,
        "status": "pass",
        "source_sha256": source_sha256,
        "adapter_contract_sha256": contract_sha256,
        "assembly_sha256": hashlib.sha256(assembly).hexdigest(),
        "assembly_bytes": len(assembly),
        "encounter_count": len(encounters),
        "entity_type_count": len(ENTITY_VOCABULARY_MANIFEST),
        "semantic_token_count": len(SEMANTIC_TOKEN_REGISTRY),
        "semantic_tokens": [
            {
                "id": token.token_id,
                "name": token.name,
                "domain": token.domain,
                "value": token.value,
            }
            for token in sorted(
                SEMANTIC_TOKEN_REGISTRY.values(),
                key=lambda item: item.token_id,
            )
        ],
        "reference_contracts": [
            {
                "id": contract.reference_id,
                "parameter_key": contract.parameter_key,
                "direction": contract.direction,
                "dependency_policy": contract.dependency_policy,
            }
            for contract in sorted(
                REFERENCE_REGISTRY.values(),
                key=lambda item: item.reference_id,
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
            for field in sorted(
                REFERENCE_FIELD_REGISTRY.values(),
                key=lambda item: item.field_id,
            )
        ],
        "translation_contracts": contracts,
        "rom_metadata": {
            "entry_bytes": OBJECT_METADATA_BYTES,
            "entry_fields": [
                "original_type_code.word",
                "owner_code.byte",
                "dependency_group.byte",
            ],
            "ordering": (
                "one entry per canonical object in identical encounter order"
            ),
            "owner_codes": {
                str(METADATA_OWNER_VOCABULARY): "vocabulary",
                str(METADATA_OWNER_FUTURE_BOSS): OWNER_FUTURE_BOSS,
            },
        },
        "deferred_boss": {
            "vocab_kind": 0,
            "owner": OWNER_FUTURE_BOSS,
            "entity_types": sorted(
                entity_type
                for entity_type, contract
                in ENTITY_VOCABULARY_MANIFEST.items()
                if contract.deferred
            ),
        },
        "limits": {
            "static_rectangles": STATIC_RECT_CAPACITY,
            "total_entities": MAX_ENTITIES,
            "dependency_groups": MAX_DEPENDENCY_GROUPS,
            "dependency_edges": MAX_DEPENDENCY_EDGES,
            "metadata_entry_bytes": OBJECT_METADATA_BYTES,
            "profile_bytes": PROFILE_BYTES,
            "semantic_record_bytes": SEMANTIC_RECORD_BYTES,
            "dependency_group_bytes": DEPENDENCY_GROUP_BYTES,
            "dependency_edge_bytes": DEPENDENCY_EDGE_BYTES,
            "pool_capacities": {
                str(key): value for key, value in POOL_CAPACITY_BY_TYPE.items()
            },
        },
        "encounters": encounter_rows,
    }
    return _json_bytes(value)


def build_runtime_artifacts(content_root: Path | str) -> RuntimeArtifacts:
    content_path = Path(content_root).resolve()
    encounters, source_sha256 = _build_encounters(content_path)
    if len(encounters) != len(ROSTER):
        raise RuntimeCompileError("runtime adapter did not cover the frozen roster")
    contract_sha256 = manifest_sha256()
    assembly = _assembly(encounters, source_sha256, contract_sha256)
    report = _report(
        encounters,
        source_sha256=source_sha256,
        contract_sha256=contract_sha256,
        assembly=assembly,
    )
    return RuntimeArtifacts(
        files={
            OUTPUT_ASSEMBLY: assembly,
            OUTPUT_REPORT: report,
        },
        source_sha256=source_sha256,
        contract_sha256=contract_sha256,
        assembly_sha256=hashlib.sha256(assembly).hexdigest(),
        encounter_count=len(encounters),
    )


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".staged",
        delete=False,
    ) as staged:
        staged.write(payload)
        staged_path = Path(staged.name)
    os.replace(staged_path, path)


def compile_runtime(
    content_root: Path | str, output_dir: Path | str
) -> RuntimeArtifacts:
    artifacts = build_runtime_artifacts(content_root)
    destination = Path(output_dir).resolve()
    for name, payload in artifacts.files.items():
        _write_atomic(destination / name, payload)
    return artifacts


def check_generated(
    content_root: Path | str, output_dir: Path | str
) -> RuntimeArtifacts:
    artifacts = build_runtime_artifacts(content_root)
    destination = Path(output_dir).resolve()
    stale: list[str] = []
    expected_names = set(artifacts.files)
    try:
        actual_names = {
            path.name for path in destination.iterdir() if path.is_file()
        }
    except OSError:
        actual_names = set()
    stale.extend(f"unexpected:{name}" for name in sorted(actual_names - expected_names))
    for name, expected in artifacts.files.items():
        path = destination / name
        try:
            actual = path.read_bytes()
        except OSError:
            stale.append(name)
            continue
        if actual != expected:
            stale.append(name)
    if stale:
        raise RuntimeCompileError(
            "generated campaign runtime files are missing or stale: "
            + ", ".join(stale)
        )
    return artifacts


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        artifacts = (
            check_generated(args.content_root, args.output_dir)
            if args.check
            else compile_runtime(args.content_root, args.output_dir)
        )
    except RuntimeCompileError as error:
        print(f"campaign runtime compile failed: {error}")
        return 1
    print(
        "campaign runtime adapter: PASS "
        f"(format v{FORMAT_VERSION}, {artifacts.encounter_count} encounters, "
        f"assembly sha256 {artifacts.assembly_sha256})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
