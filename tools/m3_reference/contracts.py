"""Parse and lock the assembly-facing Milestone 3 data contracts.

This intentionally is not a general Motorola assembler.  It accepts only the
small declarative subset used by ``engine_layout.asm`` and ``encounters.asm``.
Unknown syntax and semantic drift are errors rather than silently guessed
input.  The exact source hashes below remain the frozen M3 authority.  Later
milestones may load the declarative subset with ``verify_source_hashes=False``
for historical-model regression only; that mode is not a current-runtime
equivalence claim.
"""

from __future__ import annotations

import ast
import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


class ContractError(RuntimeError):
    """The runtime assembly no longer matches the bound reference contract."""


RUNTIME_SOURCE_SHA256 = {
    "src/game/collision.asm": "f926c4878409fa101d01274b77b41e63f3e946ebbf83a1c16d1e398f9904041f",
    "src/game/crc32.asm": "c99fbe1689e8448ca9b86ede56049aa5b7ca548665b8c02625d11b12823777e4",
    "src/game/encounters.asm": "f6c2d528ef928a10e4fe442d6347d5e2624a875f4d659a7b8f33b60c8841fc44",
    "src/game/engine.asm": "13088a9830d31671ff8b2cf15f05d44670052bfbd0dfc8a7ff76488ec0604d90",
    "src/game/engine_core.asm": "7d0b9a2daf017b514c81842985bcf205ded4689054cf57212f13bfce0d7afd05",
    "src/game/engine_layout.asm": "40e2876cc36a02057a9a2254d4c381e9a00bad4939c9fd5426d890cc70f409be",
    "src/game/object_pool.asm": "360e47bab4a3b25008e6aff6489e5fc61ed053c614b63ee6cd161e2ed5d72d2c",
    "src/game/projection.asm": "f863d0ea4730da3767144c6b5336c7b012754fb56adf4efb0651734f9663ee64",
}


# These are the constants consumed by the reference model.  Source hashes bind
# instruction ordering; this semantic lock makes numeric drift intelligible.
EXPECTED_CONSTANTS = {
    "ENGINE_CONTROL_BASE": 0x00FF1400,
    "ENGINE_CONTROL_END": 0x00FF1800,
    "ENGINE_ACTOR_BASE": 0x00FF1800,
    "ENGINE_ACTOR_REGION_END": 0x00FF2000,
    "ENGINE_POOL_BASE": 0x00FF2000,
    "ENGINE_POOL_REGION_END": 0x00FF6000,
    "ENGINE_PROJECTION_BASE": 0x00FF6000,
    "ENGINE_PROJECTION_END": 0x00FF7000,
    "ENGINE_STAGING_BASE": 0x00FF7000,
    "ENGINE_STAGING_END": 0x00FF8000,
    "ENGINE_MAGIC_VALUE": 0x52393445,
    "ENGINE_ABI_VERSION_VALUE": 1,
    "ENGINE_CONTROL_BYTES_VALUE": 0x400,
    "ENGINE_STATE_UNLOADED": 0,
    "ENGINE_STATE_ACTIVE": 1,
    "ENGINE_STATE_TERMINAL": 2,
    "ENGINE_STATE_ERROR": 3,
    "ENGINE_FLAG_LOADED": 0x0001,
    "ENGINE_FLAG_PROJECTION_VALID": 0x0002,
    "ENGINE_FLAG_COLLISION": 0x0004,
    "ENGINE_TERMINAL_NONE": 0,
    "ENGINE_TERMINAL_TIMEOUT": 1,
    "ENGINE_ERROR_NONE": 0,
    "ENGINE_ERROR_ENCOUNTER_NOT_FOUND": 1,
    "ENGINE_ERROR_BAD_DESCRIPTOR": 2,
    "ENGINE_ERROR_POOL_OVERFLOW": 3,
    "ENGINE_ERROR_INVALID_STATE": 4,
    "ENGINE_ERROR_PROJECTION_SIZE": 5,
    "ENGINE_RESULT_OK": 0,
    "ENGINE_RESULT_NOT_FOUND": 1,
    "ENGINE_RESULT_BAD_DESCRIPTOR": 2,
    "ENGINE_RESULT_POOL_OVERFLOW": 3,
    "ENGINE_RESULT_NOT_ACTIVE": 4,
    "ENGINE_ATTEMPT_TICKS": 720,
    "ENGINE_CHECKPOINT_INTERVAL": 60,
    "INPUT_UP": 0x01,
    "INPUT_DOWN": 0x02,
    "INPUT_LEFT": 0x04,
    "INPUT_RIGHT": 0x08,
    "INPUT_A": 0x10,
    "INPUT_B": 0x20,
    "INPUT_C": 0x40,
    "INPUT_ALLOWED_MASK": 0x7F,
    "ENGINE_CARDINAL_SPEED": 0x180,
    "ENGINE_DIAGONAL_SPEED": 0x110,
    "ENGINE_PROJECTILE_SPEED": 0x300,
    "ENGINE_PROJECTILE_TICKS": 60,
    "ACTOR_ACTIVE": 0x00,
    "ACTOR_ROLE": 0x01,
    "ACTOR_FLAGS": 0x02,
    "ACTOR_X": 0x04,
    "ACTOR_Y": 0x08,
    "ACTOR_VX": 0x0C,
    "ACTOR_VY": 0x10,
    "ACTOR_HALF_WIDTH": 0x14,
    "ACTOR_HALF_HEIGHT": 0x16,
    "ACTOR_INPUT": 0x18,
    "ACTOR_PREVIOUS_INPUT": 0x19,
    "ACTOR_HEALTH": 0x1A,
    "ACTOR_LOGICAL_ID": 0x1C,
    "ACTOR_RESERVED": 0x1E,
    "ACTOR_BYTES": 0x20,
    "ACTOR_PROJECTION_BYTES": 0x1E,
    "ACTOR_CAPACITY": 7,
    "ACTOR_CURRENT_ROLE": 6,
    "ACTOR_FLAG_COLLISION_X": 0x0001,
    "ACTOR_FLAG_COLLISION_Y": 0x0002,
    "OBJECT_ACTIVE": 0x00,
    "OBJECT_LOGICAL_ID": 0x01,
    "OBJECT_STATE": 0x02,
    "OBJECT_X": 0x04,
    "OBJECT_Y": 0x08,
    "OBJECT_VX": 0x0C,
    "OBJECT_VY": 0x10,
    "OBJECT_TIMER": 0x14,
    "OBJECT_PARAM": 0x16,
    "OBJECT_BYTES": 0x18,
    "OBJECT_PROJECTION_BYTES": 0x18,
    "POOL_TYPE_ENEMY": 1,
    "POOL_TYPE_PROJECTILE": 2,
    "POOL_TYPE_DEVICE": 3,
    "POOL_TYPE_CARRYABLE": 4,
    "POOL_TYPE_HAZARD": 5,
    "POOL_TYPE_BOSS": 6,
    "POOL_TYPE_EFFECT": 7,
    "POOL_TYPE_COUNT": 7,
    "ENEMY_CAPACITY": 16,
    "PROJECTILE_CAPACITY": 32,
    "DEVICE_CAPACITY": 32,
    "CARRYABLE_CAPACITY": 8,
    "HAZARD_CAPACITY": 16,
    "BOSS_COMPONENT_CAPACITY": 12,
    "EFFECT_CAPACITY": 24,
    "TOTAL_POOL_CAPACITY": 140,
    "ENEMY_POOL": 0x00FF2000,
    "PROJECTILE_POOL": 0x00FF2200,
    "DEVICE_POOL": 0x00FF2600,
    "CARRYABLE_POOL": 0x00FF2A00,
    "HAZARD_POOL": 0x00FF2B00,
    "BOSS_COMPONENT_POOL": 0x00FF2D00,
    "EFFECT_POOL": 0x00FF2E80,
    "OVERFLOW_ENEMY": 0x0001,
    "OVERFLOW_PROJECTILE": 0x0002,
    "OVERFLOW_DEVICE": 0x0004,
    "OVERFLOW_CARRYABLE": 0x0008,
    "OVERFLOW_HAZARD": 0x0010,
    "OVERFLOW_BOSS": 0x0020,
    "OVERFLOW_EFFECT": 0x0040,
    "ENCOUNTER_DESCRIPTOR_ID": 0x00,
    "ENCOUNTER_DESCRIPTOR_FLAGS": 0x02,
    "ENCOUNTER_DESCRIPTOR_VERSION": 0x04,
    "ENCOUNTER_DESCRIPTOR_BYTES": 0x06,
    "ENCOUNTER_ROOM_LEFT": 0x08,
    "ENCOUNTER_ROOM_TOP": 0x0A,
    "ENCOUNTER_ROOM_RIGHT": 0x0C,
    "ENCOUNTER_ROOM_BOTTOM": 0x0E,
    "ENCOUNTER_SPAWN_X": 0x10,
    "ENCOUNTER_SPAWN_Y": 0x12,
    "ENCOUNTER_RECT_COUNT": 0x14,
    "ENCOUNTER_OBJECT_COUNT": 0x16,
    "ENCOUNTER_RECT_POINTER": 0x18,
    "ENCOUNTER_OBJECT_POINTER": 0x1C,
    "ENCOUNTER_DESCRIPTOR_SIZE": 0x20,
    "ENCOUNTER_DESCRIPTOR_VERSION_VALUE": 1,
    "STATIC_RECT_X": 0,
    "STATIC_RECT_Y": 2,
    "STATIC_RECT_WIDTH": 4,
    "STATIC_RECT_HEIGHT": 6,
    "STATIC_RECT_BYTES": 8,
    "STATIC_RECT_CAPACITY": 32,
    "OBJECT_INIT_TYPE": 0,
    "OBJECT_INIT_LOGICAL_ID": 1,
    "OBJECT_INIT_STATE": 2,
    "OBJECT_INIT_X": 4,
    "OBJECT_INIT_Y": 6,
    "OBJECT_INIT_PARAM_0": 8,
    "OBJECT_INIT_PARAM_1": 10,
    "OBJECT_INIT_BYTES": 12,
    "CANONICAL_DESCRIPTOR": 0x00FF7000,
    "CANONICAL_RECTS": 0x00FF7020,
    "CANONICAL_POOL_LAST_IDS": 0x00FF71E0,
    "CANONICAL_POOL_COUNTS": 0x00FF71F0,
    "CANONICAL_OBJECTS": 0x00FF7200,
    "PROJECTION_MAGIC_VALUE": 0x52393450,
    "PROJECTION_SCHEMA_VERSION": 1,
    "PROJECTION_HEADER_BYTES": 0x28,
    "PROJECTION_POOL_HEADER_BYTES": 4,
    "ENGINE_PROJECTION_SCHEMA_BYTES": 3638,
}


POOL_CAPACITY_NAMES = {
    1: "ENEMY_CAPACITY",
    2: "PROJECTILE_CAPACITY",
    3: "DEVICE_CAPACITY",
    4: "CARRYABLE_CAPACITY",
    5: "HAZARD_CAPACITY",
    6: "BOSS_COMPONENT_CAPACITY",
    7: "EFFECT_CAPACITY",
}

POOL_BASE_NAMES = {
    1: "ENEMY_POOL",
    2: "PROJECTILE_POOL",
    3: "DEVICE_POOL",
    4: "CARRYABLE_POOL",
    5: "HAZARD_POOL",
    6: "BOSS_COMPONENT_POOL",
    7: "EFFECT_POOL",
}

POOL_OVERFLOW_NAMES = {
    1: "OVERFLOW_ENEMY",
    2: "OVERFLOW_PROJECTILE",
    3: "OVERFLOW_DEVICE",
    4: "OVERFLOW_CARRYABLE",
    5: "OVERFLOW_HAZARD",
    6: "OVERFLOW_BOSS",
    7: "OVERFLOW_EFFECT",
}


@dataclass(frozen=True)
class StaticRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class ObjectInit:
    pool_type: int
    logical_id: int
    state: int
    x: int
    y: int
    param_0: int
    param_1: int


@dataclass(frozen=True)
class Encounter:
    encounter_id: int
    flags: int
    version: int
    room_left: int
    room_top: int
    room_right: int
    room_bottom: int
    spawn_x: int
    spawn_y: int
    rects: tuple[StaticRect, ...]
    objects: tuple[ObjectInit, ...]
    descriptor_label: str
    rect_label: str
    object_label: str


@dataclass(frozen=True)
class EngineContract:
    root: Path
    constants: Mapping[str, int]
    encounters: Mapping[int, Encounter]
    encounter_order: tuple[int, ...]
    source_sha256: Mapping[str, str]

    @property
    def pool_capacities(self) -> dict[int, int]:
        return {
            pool_type: self.constants[name]
            for pool_type, name in POOL_CAPACITY_NAMES.items()
        }

    @property
    def pool_bases(self) -> dict[int, int]:
        return {
            pool_type: self.constants[name]
            for pool_type, name in POOL_BASE_NAMES.items()
        }

    @property
    def pool_overflow_masks(self) -> dict[int, int]:
        return {
            pool_type: self.constants[name]
            for pool_type, name in POOL_OVERFLOW_NAMES.items()
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strip_comment(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _normalize_expression(expression: str) -> str:
    return re.sub(r"\$([0-9A-Fa-f]+)", r"0x\1", expression.strip())


def _evaluate_expression(expression: str, symbols: Mapping[str, int]) -> int:
    try:
        tree = ast.parse(_normalize_expression(expression), mode="eval")
    except SyntaxError as exc:
        raise ContractError(f"invalid assembly expression {expression!r}") from exc

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            try:
                return symbols[node.id]
            except KeyError as exc:
                raise ContractError(
                    f"unknown symbol {node.id!r} in expression {expression!r}"
                ) from exc
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, (ast.Div, ast.FloorDiv)):
                if right == 0 or left % right:
                    raise ContractError(
                        f"non-integral division in expression {expression!r}"
                    )
                return left // right
        raise ContractError(
            f"unsupported syntax in assembly expression {expression!r}"
        )

    return visit(tree)


def parse_layout_constants(path: Path) -> dict[str, int]:
    symbols: dict[str, int] = {}
    equ_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+equ\s+(.+)$", re.I)
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = _strip_comment(raw_line)
        if not line:
            continue
        match = equ_re.match(line)
        if not match:
            continue
        name, expression = match.groups()
        if name in symbols:
            raise ContractError(f"{path}:{line_number}: duplicate equ {name}")
        try:
            symbols[name] = _evaluate_expression(expression, symbols)
        except ContractError as exc:
            raise ContractError(f"{path}:{line_number}: {exc}") from exc
    missing = sorted(set(EXPECTED_CONSTANTS) - set(symbols))
    if missing:
        raise ContractError(f"layout is missing required constants: {missing}")
    for name, expected in EXPECTED_CONSTANTS.items():
        actual = symbols[name]
        if actual != expected:
            raise ContractError(
                f"constant drift: {name} is ${actual:X}, expected ${expected:X}"
            )
    return symbols


def _split_operands(text: str) -> list[str]:
    operands: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise ContractError(f"unbalanced operand list {text!r}")
        elif character == "," and depth == 0:
            operands.append(text[start:index].strip())
            start = index + 1
    if depth:
        raise ContractError(f"unbalanced operand list {text!r}")
    operands.append(text[start:].strip())
    if not operands or any(not operand for operand in operands):
        raise ContractError(f"empty data operand in {text!r}")
    return operands


@dataclass(frozen=True)
class _DataEvent:
    offset: int
    width: int
    operands: tuple[str, ...]
    line_number: int


@dataclass(frozen=True)
class _DataImage:
    data: bytes
    symbols: Mapping[str, int]
    labels_by_offset: Mapping[int, tuple[str, ...]]


def _assemble_declarative_data(path: Path, base_symbols: Mapping[str, int]) -> _DataImage:
    lines = path.read_text(encoding="utf-8").splitlines()
    symbols = dict(base_symbols)
    labels: dict[str, int] = {}
    pending_equ: dict[str, tuple[str, int]] = {}
    events: list[_DataEvent] = []
    offset = 0
    label_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s*(.*))?$")
    equ_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+equ\s+(.+)$", re.I)
    data_re = re.compile(r"^dc\.(b|w|l)\s+(.+)$", re.I)

    for line_number, raw_line in enumerate(lines, 1):
        line = _strip_comment(raw_line)
        if not line:
            continue
        label_match = label_re.match(line)
        if label_match:
            name, remainder = label_match.groups()
            if name in labels or name in symbols:
                raise ContractError(f"{path}:{line_number}: duplicate label {name}")
            labels[name] = offset
            symbols[name] = offset
            line = (remainder or "").strip()
            if not line:
                continue
        equ_match = equ_re.match(line)
        if equ_match:
            name, expression = equ_match.groups()
            if name in symbols or name in pending_equ:
                raise ContractError(f"{path}:{line_number}: duplicate equ {name}")
            pending_equ[name] = (expression, line_number)
            continue
        if line.lower() == "even":
            offset = (offset + 1) & ~1
            continue
        data_match = data_re.match(line)
        if data_match:
            suffix, operand_text = data_match.groups()
            width = {"b": 1, "w": 2, "l": 4}[suffix.lower()]
            operands = tuple(_split_operands(operand_text))
            if width > 1 and offset & 1:
                raise ContractError(
                    f"{path}:{line_number}: unaligned {suffix}-sized data"
                )
            events.append(_DataEvent(offset, width, operands, line_number))
            offset += width * len(operands)
            continue
        if line.lower().startswith("assert "):
            continue
        raise ContractError(
            f"{path}:{line_number}: unsupported declarative syntax {line!r}"
        )

    unresolved = dict(pending_equ)
    while unresolved:
        made_progress = False
        for name, (expression, line_number) in tuple(unresolved.items()):
            try:
                value = _evaluate_expression(expression, symbols)
            except ContractError as exc:
                if "unknown symbol" in str(exc):
                    continue
                raise ContractError(f"{path}:{line_number}: {exc}") from exc
            symbols[name] = value
            del unresolved[name]
            made_progress = True
        if not made_progress:
            details = ", ".join(
                f"{name}={expression!r}"
                for name, (expression, _) in sorted(unresolved.items())
            )
            raise ContractError(f"unresolved encounter equ expressions: {details}")

    image = bytearray(offset)
    for event in events:
        cursor = event.offset
        for operand in event.operands:
            try:
                value = _evaluate_expression(operand, symbols)
            except ContractError as exc:
                raise ContractError(
                    f"{path}:{event.line_number}: {exc}"
                ) from exc
            mask = (1 << (event.width * 8)) - 1
            minimum = -(1 << (event.width * 8 - 1))
            if value < minimum or value > mask:
                raise ContractError(
                    f"{path}:{event.line_number}: value {value} does not fit "
                    f"{event.width} byte(s)"
                )
            image[cursor : cursor + event.width] = (value & mask).to_bytes(
                event.width, "big"
            )
            cursor += event.width

    labels_by_offset_lists: dict[int, list[str]] = {}
    for name, label_offset in labels.items():
        labels_by_offset_lists.setdefault(label_offset, []).append(name)
    labels_by_offset = {
        label_offset: tuple(names)
        for label_offset, names in labels_by_offset_lists.items()
    }
    return _DataImage(bytes(image), symbols, labels_by_offset)


def _slice(data: bytes, offset: int, size: int, description: str) -> bytes:
    if offset < 0 or offset + size > len(data):
        raise ContractError(
            f"{description} at ${offset:X}..${offset + size:X} is outside data"
        )
    return data[offset : offset + size]


def _parse_encounters(path: Path, constants: Mapping[str, int]) -> tuple[
    dict[int, Encounter], tuple[int, ...]
]:
    image = _assemble_declarative_data(path, constants)
    symbols = image.symbols
    try:
        table_start = symbols["EncounterDescriptorTable"]
        table_end = symbols["EncounterDescriptorTableEnd"]
    except KeyError as exc:
        raise ContractError("encounter descriptor table labels are missing") from exc
    if table_end <= table_start or (table_end - table_start) % 4:
        raise ContractError("encounter descriptor table size is invalid")
    table_data = _slice(
        image.data,
        table_start,
        table_end - table_start,
        "encounter descriptor table",
    )
    descriptor_offsets = [
        int.from_bytes(table_data[index : index + 4], "big")
        for index in range(0, len(table_data), 4)
    ]
    if symbols.get("ENCOUNTER_DESCRIPTOR_COUNT") != len(descriptor_offsets):
        raise ContractError("ENCOUNTER_DESCRIPTOR_COUNT does not match table")

    encounters: dict[int, Encounter] = {}
    order: list[int] = []
    descriptor_size = constants["ENCOUNTER_DESCRIPTOR_SIZE"]
    descriptor_format = ">HHHHhhhhhhHHII"
    if struct.calcsize(descriptor_format) != descriptor_size:
        raise ContractError("reference descriptor struct size mismatch")

    for descriptor_offset in descriptor_offsets:
        descriptor_candidates = image.labels_by_offset.get(descriptor_offset, ())
        descriptor_label = next(
            (
                label
                for label in descriptor_candidates
                if re.fullmatch(r"EncounterDescriptor\d+", label)
            ),
            None,
        )
        if descriptor_label is None:
            raise ContractError(
                f"descriptor pointer ${descriptor_offset:X} has no label"
            )
        values = struct.unpack(
            descriptor_format,
            _slice(
                image.data,
                descriptor_offset,
                descriptor_size,
                descriptor_label,
            ),
        )
        (
            encounter_id,
            flags,
            version,
            encoded_size,
            room_left,
            room_top,
            room_right,
            room_bottom,
            spawn_x,
            spawn_y,
            rect_count,
            object_count,
            rect_pointer,
            object_pointer,
        ) = values
        if version != constants["ENCOUNTER_DESCRIPTOR_VERSION_VALUE"]:
            raise ContractError(f"{descriptor_label}: unsupported version {version}")
        if encoded_size != descriptor_size:
            raise ContractError(
                f"{descriptor_label}: encoded size {encoded_size}, expected {descriptor_size}"
            )
        rect_label = next(
            (
                label
                for label in image.labels_by_offset.get(rect_pointer, ())
                if re.fullmatch(r"Encounter\d+Rects", label)
            ),
            None,
        )
        object_label = next(
            (
                label
                for label in image.labels_by_offset.get(object_pointer, ())
                if re.fullmatch(r"Encounter\d+Objects", label)
            ),
            None,
        )
        if rect_count and rect_label is None:
            raise ContractError(f"{descriptor_label}: rectangle pointer has no label")
        if object_count and object_label is None:
            raise ContractError(f"{descriptor_label}: object pointer has no label")

        rects: list[StaticRect] = []
        for index in range(rect_count):
            rect_offset = rect_pointer + index * constants["STATIC_RECT_BYTES"]
            x, y, width, height = struct.unpack(
                ">hhhh",
                _slice(
                    image.data,
                    rect_offset,
                    constants["STATIC_RECT_BYTES"],
                    f"{descriptor_label} rectangle {index}",
                ),
            )
            rects.append(StaticRect(x, y, width, height))

        objects: list[ObjectInit] = []
        for index in range(object_count):
            object_offset = object_pointer + index * constants["OBJECT_INIT_BYTES"]
            pool_type, logical_id, state, x, y, param_0, param_1 = struct.unpack(
                ">BBHhhHH",
                _slice(
                    image.data,
                    object_offset,
                    constants["OBJECT_INIT_BYTES"],
                    f"{descriptor_label} object {index}",
                ),
            )
            objects.append(
                ObjectInit(
                    pool_type,
                    logical_id,
                    state,
                    x,
                    y,
                    param_0,
                    param_1,
                )
            )

        encounter = Encounter(
            encounter_id,
            flags,
            version,
            room_left,
            room_top,
            room_right,
            room_bottom,
            spawn_x,
            spawn_y,
            tuple(rects),
            tuple(objects),
            descriptor_label,
            rect_label or "",
            object_label or "",
        )
        _validate_encounter(encounter, constants)
        if encounter_id in encounters:
            raise ContractError(f"duplicate encounter ID {encounter_id}")
        encounters[encounter_id] = encounter
        order.append(encounter_id)

    return encounters, tuple(order)


def _validate_encounter(
    encounter: Encounter, constants: Mapping[str, int]
) -> None:
    if not encounter.room_left < encounter.room_right:
        raise ContractError(f"{encounter.descriptor_label}: invalid horizontal room")
    if not encounter.room_top < encounter.room_bottom:
        raise ContractError(f"{encounter.descriptor_label}: invalid vertical room")
    if not encounter.room_left <= encounter.spawn_x < encounter.room_right:
        raise ContractError(f"{encounter.descriptor_label}: spawn X outside room")
    if not encounter.room_top <= encounter.spawn_y < encounter.room_bottom:
        raise ContractError(f"{encounter.descriptor_label}: spawn Y outside room")
    if len(encounter.rects) > constants["STATIC_RECT_CAPACITY"]:
        raise ContractError(f"{encounter.descriptor_label}: too many rectangles")
    if len(encounter.objects) > constants["TOTAL_POOL_CAPACITY"]:
        raise ContractError(f"{encounter.descriptor_label}: too many objects")

    for index, rect in enumerate(encounter.rects):
        if rect.width <= 0 or rect.height <= 0:
            raise ContractError(
                f"{encounter.descriptor_label}: rectangle {index} has empty extent"
            )
        if (
            rect.x < encounter.room_left
            or rect.y < encounter.room_top
            or rect.x + rect.width > encounter.room_right
            or rect.y + rect.height > encounter.room_bottom
        ):
            raise ContractError(
                f"{encounter.descriptor_label}: rectangle {index} outside room"
            )

    counts = {pool_type: 0 for pool_type in POOL_CAPACITY_NAMES}
    last_ids: dict[int, int] = {}
    for index, item in enumerate(encounter.objects):
        if item.pool_type not in POOL_CAPACITY_NAMES:
            raise ContractError(
                f"{encounter.descriptor_label}: object {index} has invalid type"
            )
        counts[item.pool_type] += 1
        capacity = constants[POOL_CAPACITY_NAMES[item.pool_type]]
        if counts[item.pool_type] > capacity:
            raise ContractError(
                f"{encounter.descriptor_label}: pool {item.pool_type} overflow"
            )
        if item.logical_id <= last_ids.get(item.pool_type, -1):
            raise ContractError(
                f"{encounter.descriptor_label}: pool {item.pool_type} IDs "
                f"are not strictly ascending"
            )
        last_ids[item.pool_type] = item.logical_id
        if not encounter.room_left <= item.x < encounter.room_right:
            raise ContractError(
                f"{encounter.descriptor_label}: object {index} X outside room"
            )
        if not encounter.room_top <= item.y < encounter.room_bottom:
            raise ContractError(
                f"{encounter.descriptor_label}: object {index} Y outside room"
            )


EXPECTED_ENCOUNTER_SUMMARY = {
    0: {
        "room": (0, 0, 320, 224),
        "spawn": (32, 112),
        "rects": ((112, 48, 16, 128), (208, 80, 24, 16)),
        "objects": (
            (1, 0, 1, 160, 64, 0, 3),
            (3, 0, 1, 80, 112, 0, 0),
            (4, 0, 1, 48, 152, 0, 0),
            (5, 0, 1, 176, 176, 0, 1),
        ),
    },
    1: {
        "room": (0, 0, 320, 224),
        "spawn": (288, 112),
        "rects": (
            (64, 96, 80, 16),
            (176, 48, 16, 112),
            (240, 144, 40, 16),
        ),
        "objects": (
            (1, 0, 1, 224, 64, 0, 4),
            (1, 1, 1, 256, 96, 0, 4),
            (1, 2, 1, 224, 176, 0, 4),
            (3, 0, 2, 48, 48, 0, 0),
            (3, 1, 2, 288, 176, 0, 0),
            (5, 0, 1, 120, 72, 0, 2),
            (5, 1, 1, 208, 136, 0, 2),
            (6, 0, 1, 272, 48, 0, 6),
        ),
    },
}


def _encounter_summary(encounter: Encounter) -> dict[str, object]:
    return {
        "room": (
            encounter.room_left,
            encounter.room_top,
            encounter.room_right,
            encounter.room_bottom,
        ),
        "spawn": (encounter.spawn_x, encounter.spawn_y),
        "rects": tuple(
            (rect.x, rect.y, rect.width, rect.height)
            for rect in encounter.rects
        ),
        "objects": tuple(
            (
                item.pool_type,
                item.logical_id,
                item.state,
                item.x,
                item.y,
                item.param_0,
                item.param_1,
            )
            for item in encounter.objects
        ),
    }


def _verify_source_hashes(root: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    for relative, expected in RUNTIME_SOURCE_SHA256.items():
        path = root / relative
        if not path.is_file():
            raise ContractError(f"required runtime source is missing: {relative}")
        digest = sha256_file(path)
        actual[relative] = digest
        if digest != expected:
            raise ContractError(
                f"runtime source drift: {relative} is {digest}, expected {expected}"
            )
    return actual


def load_engine_contract(
    root: Path | str, *, verify_source_hashes: bool = True
) -> EngineContract:
    """Load and validate the assembly constants and fixture records.

    ``verify_source_hashes=True`` binds to the exact frozen M3 implementation.
    ``False`` still fail-closes on every parsed numeric and encounter semantic,
    but only supplies current declarative inputs to the historical reference
    model.  Callers using the latter mode must not present it as live runtime
    evidence.
    """

    root_path = Path(root).resolve()
    if verify_source_hashes:
        source_hashes = _verify_source_hashes(root_path)
    else:
        source_hashes = {
            relative: sha256_file(root_path / relative)
            for relative in RUNTIME_SOURCE_SHA256
        }
    constants = parse_layout_constants(root_path / "src/game/engine_layout.asm")
    encounters, order = _parse_encounters(
        root_path / "src/game/encounters.asm", constants
    )
    if order != (0, 1):
        raise ContractError(f"encounter table order drifted: {order!r}")
    if set(encounters) != set(EXPECTED_ENCOUNTER_SUMMARY):
        raise ContractError(f"encounter IDs drifted: {sorted(encounters)}")
    for encounter_id, expected in EXPECTED_ENCOUNTER_SUMMARY.items():
        actual = _encounter_summary(encounters[encounter_id])
        if actual != expected:
            raise ContractError(
                f"encounter {encounter_id} semantic fixture drift: "
                f"{actual!r} != {expected!r}"
            )
    return EngineContract(
        root_path,
        constants,
        encounters,
        order,
        source_hashes,
    )


def iter_runtime_paths(root: Path) -> Iterable[Path]:
    for relative in sorted(RUNTIME_SOURCE_SHA256):
        yield root / relative
