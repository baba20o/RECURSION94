"""Fail-closed assembly contracts for the Milestone 4 reference model.

This module intentionally parses only ``equ`` expressions and the small
declarative encounter-zero fixture consumed by M4.  Runtime instruction
semantics are independently implemented in :mod:`tools.m4_reference.model`;
exact frozen source hashes and ordered behavior markers distinguish historical
runtime identity from later compatible declarative inputs.  Loading with
``verify_source_hashes=False`` is suitable only for historical-model
regression; it is not evidence that a later ROM executes the M4 model.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ContractError(RuntimeError):
    """The bound assembly contract is missing, malformed, or has drifted."""


RUNTIME_SOURCE_SHA256 = {
    "src/game/collision.asm": "f926c4878409fa101d01274b77b41e63f3e946ebbf83a1c16d1e398f9904041f",
    "src/game/crc32.asm": "c99fbe1689e8448ca9b86ede56049aa5b7ca548665b8c02625d11b12823777e4",
    "src/game/encounters.asm": "f6c2d528ef928a10e4fe442d6347d5e2624a875f4d659a7b8f33b60c8841fc44",
    "src/game/engine.asm": "13088a9830d31671ff8b2cf15f05d44670052bfbd0dfc8a7ff76488ec0604d90",
    "src/game/engine_core.asm": "7d0b9a2daf017b514c81842985bcf205ded4689054cf57212f13bfce0d7afd05",
    "src/game/engine_layout.asm": "40e2876cc36a02057a9a2254d4c381e9a00bad4939c9fd5426d890cc70f409be",
    "src/game/object_pool.asm": "360e47bab4a3b25008e6aff6489e5fc61ed053c614b63ee6cd161e2ed5d72d2c",
    "src/game/projection.asm": "f863d0ea4730da3767144c6b5336c7b012754fb56adf4efb0651734f9663ee64",
    "src/gameplay/actor_sim.asm": "9726a1add2796e0e16bf07478e83aa1cf0877ae869aeaf4a179ad7c5ceabcf53",
    "src/gameplay/gameplay.asm": "e8b48b999d924d6162cf0da46e7a051eba72f046efb970c26957bb2e62b49fd9",
    "src/gameplay/gameplay_core.asm": "b211c6a529ff13c3a34cbd4fca8132e7fd8fffd8c9614b134174517c4feb089d",
    "src/gameplay/gameplay_layout.asm": "41c23b178e425fa723a5889a31e4a6fa6c89477f2b1d94a77f26d471d7fdeaa7",
    "src/gameplay/replay.asm": "e56a1b52c6828fbb614f9d6248d06637d5e6c01dd48cdefb37d842e9da39e9ae",
    "src/gameplay/vertical_slice.asm": "38fc69f2d214dcaa700defb677093adfdb1aea6228d97f5b7b99edc052514090",
}


# Numeric values directly consumed by the M4 model or its RAM/projection locks.
EXPECTED_CONSTANTS = {
    "ENGINE_CONTROL_BASE": 0x00FF1400,
    "ENGINE_CONTROL_END": 0x00FF1800,
    "ENGINE_CONTROL_USED_END": 0x00FF1450,
    "ENGINE_ACTOR_BASE": 0x00FF1800,
    "ENGINE_ACTOR_REGION_END": 0x00FF2000,
    "ENGINE_POOL_BASE": 0x00FF2000,
    "ENGINE_POOL_REGION_END": 0x00FF6000,
    "ENGINE_PROJECTION_BASE": 0x00FF6000,
    "ENGINE_PROJECTION_END": 0x00FF7000,
    "ENGINE_STAGING_END": 0x00FF8000,
    "ENGINE_STATE_ACTIVE": 1,
    "ENGINE_STATE_TERMINAL": 2,
    "ENGINE_FLAG_LOADED": 1,
    "ENGINE_FLAG_PROJECTION_VALID": 2,
    "ENGINE_FLAG_COLLISION": 4,
    "ENGINE_TERMINAL_NONE": 0,
    "ENGINE_TERMINAL_TIMEOUT": 1,
    "ENGINE_ERROR_NONE": 0,
    "ENGINE_ERROR_POOL_OVERFLOW": 3,
    "ENGINE_ERROR_INVALID_STATE": 4,
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
    "ACTOR_BYTES": 0x20,
    "ACTOR_PROJECTION_BYTES": 0x1E,
    "ACTOR_CAPACITY": 7,
    "ACTOR_CURRENT_ROLE": 6,
    "ACTOR_FLAG_COLLISION_X": 1,
    "ACTOR_FLAG_COLLISION_Y": 2,
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
    "OVERFLOW_PROJECTILE": 0x0002,
    "PROJECTION_MAGIC_VALUE": 0x52393450,
    "PROJECTION_SCHEMA_VERSION": 1,
    "PROJECTION_HEADER_BYTES": 0x28,
    "PROJECTION_POOL_HEADER_BYTES": 4,
    "ENGINE_PROJECTION_SCHEMA_BYTES": 3638,
    "REPLAY_BANK_BASE": 0x00FF0000,
    "REPLAY_BANK_BYTES": 720,
    "REPLAY_BANK_COUNT": 7,
    "REPLAY_BANK_REGION_END": 0x00FF13B0,
    "REPLAY_DESCRIPTOR_BASE": 0x00FF13B0,
    "REPLAY_DESCRIPTOR_LENGTH": 0,
    "REPLAY_DESCRIPTOR_CRC": 2,
    "REPLAY_DESCRIPTOR_FLAGS": 6,
    "REPLAY_DESCRIPTOR_GENERATION": 7,
    "REPLAY_DESCRIPTOR_BYTES": 8,
    "REPLAY_DESCRIPTOR_REGION_END": 0x00FF13E8,
    "REPLAY_FLAG_VALID": 1,
    "REPLAY_FLAG_ECHO": 2,
    "REPLAY_FLAG_CURRENT": 4,
    "REPLAY_FLAG_PROTECTED": 8,
    "TIMELINE_CONTROL_BASE": 0x00FF13E8,
    "TIMELINE_ECHO_COUNT": 0x00FF13F0,
    "TIMELINE_CURRENT_BANK": 0x00FF13F1,
    "TIMELINE_ECHO_ORDER": 0x00FF13F2,
    "TIMELINE_SELECTED_ORDINAL": 0x00FF13F8,
    "TIMELINE_FLAGS": 0x00FF13F9,
    "TIMELINE_ATTEMPT_LENGTH": 0x00FF13FC,
    "TIMELINE_LAST_RESULT": 0x00FF13FE,
    "TIMELINE_CONTROL_BYTES": 0x18,
    "TIMELINE_CONTROL_END": 0x00FF1400,
    "TIMELINE_MAGIC_VALUE": 0x52393454,
    "TIMELINE_VERSION_VALUE": 1,
    "TIMELINE_NO_SELECTION": 0xFF,
    "TIMELINE_FLAG_INITIALIZED": 0x01,
    "TIMELINE_FLAG_REPLACEMENT_REQUIRED": 0x02,
    "TIMELINE_FLAG_SELECTION_VALID": 0x04,
    "TIMELINE_FLAG_SELECTION_OLDEST": 0x08,
    "TIMELINE_FLAG_SELECTION_EXPLICIT": 0x10,
    "TIMELINE_FLAG_COMMIT_BLOCKED": 0x20,
    "TIMELINE_FLAG_PROGRAM_STABLE": 0x40,
    "TIMELINE_FLAG_RECORDING_FULL": 0x80,
    "GAMEPLAY_ECHO_LIMIT": 6,
    "GAMEPLAY_CURRENT_ACTOR_SLOT": 6,
    "GAMEPLAY_CURRENT_ACTOR": 0x00FF18C0,
    "GAMEPLAY_CONTROL_BASE": 0x00FF1500,
    "GAMEPLAY_CONTROL_USED_END": 0x00FF1524,
    "GAMEPLAY_CONTROL_BYTES_VALUE": 0x0100,
    "GAMEPLAY_FLAG_INITIALIZED": 0x0001,
    "GAMEPLAY_FLAG_PROGRAM_STABLE": 0x0002,
    "GAMEPLAY_FLAG_REPLACEMENT_VISIBLE": 0x0004,
    "GAMEPLAY_FLAG_DOOR_OPEN": 0x0008,
    "GAMEPLAY_FLAG_SWITCH_HELD": 0x0010,
    "GAMEPLAY_RESULT_OK": 0,
    "GAMEPLAY_RESULT_PROGRAM_STABLE": 1,
    "GAMEPLAY_RESULT_NOT_INITIALIZED": 2,
    "GAMEPLAY_RESULT_INVALID_STATE": 3,
    "GAMEPLAY_RESULT_INVALID_SELECTION": 4,
    "GAMEPLAY_RESULT_REPLACEMENT_REQUIRED": 5,
    "GAMEPLAY_RESULT_PROTECTED": 6,
    "GAMEPLAY_RESULT_RECORDING_OVERRUN": 7,
    "GAMEPLAY_RESULT_TIMEOUT": 8,
    "GAMEPLAY_COMMIT_TIMEOUT": 1,
    "GAMEPLAY_COMMIT_MANUAL": 2,
    "GAMEPLAY_COMMIT_PROGRAM_STABLE": 3,
    "GAMEPLAY_TERMINAL_PROGRAM_STABLE": 0x0100,
    "GAMEPLAY_ACTOR_FLAG_FACING_LEFT": 0x8000,
    "B01_DEVICE": 0x00FF2600,
    "B01_DEVICE_STATE_SWITCH_HELD": 1,
    "B01_DEVICE_STATE_DOOR_OPEN": 2,
    "B01_DEVICE_STATE_PROGRAM_STABLE": 4,
    "B01_SWITCH_CENTER_X": 80,
    "B01_SWITCH_CENTER_Y": 112,
    "B01_SWITCH_HALF_EXTENT": 8,
    "B01_SWITCH_MIN_X_16_8": 72 << 8,
    "B01_SWITCH_MAX_X_16_8": 88 << 8,
    "B01_SWITCH_MIN_Y_16_8": 104 << 8,
    "B01_SWITCH_MAX_Y_16_8": 120 << 8,
    "B01_TERMINAL_CENTER_X": 288,
    "B01_TERMINAL_CENTER_Y": 112,
    "B01_TERMINAL_HALF_EXTENT": 8,
    "B01_TERMINAL_MIN_X_16_8": 280 << 8,
    "B01_TERMINAL_MAX_X_16_8": 296 << 8,
    "B01_TERMINAL_MIN_Y_16_8": 104 << 8,
    "B01_TERMINAL_MAX_Y_16_8": 120 << 8,
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


REQUIRED_LABELS = {
    "src/gameplay/gameplay_core.asm": (
        "GameplayInitialize",
        "GameplayReinstantiateActors",
        "GameplayStep",
    ),
    "src/gameplay/replay.asm": (
        "GameplayClearReplacementSelection",
        "GameplaySelectReplacement",
        "GameplaySelectOldestReplacement",
        "GameplaySetEchoProtected",
        "GameplayCommitAndReset",
    ),
}

REQUIRED_ORDERED_FRAGMENTS = {
    "src/gameplay/gameplay.asm": (
        'include "gameplay/gameplay_layout.asm"',
        'include "gameplay/actor_sim.asm"',
        'include "gameplay/vertical_slice.asm"',
        'include "gameplay/replay.asm"',
        'include "gameplay/gameplay_core.asm"',
    ),
    "src/gameplay/gameplay_core.asm": (
        "GameplayStep:",
        "#GAMEPLAY_PHASE_RECORD_CURRENT",
        "#GAMEPLAY_PHASE_ECHOES",
        ".simulate_current:",
        "#GAMEPLAY_PHASE_INTERACTIONS",
        "bne.s   .campaign_vocabulary",
        "bsr.w   GameplayResolveVerticalSlice",
        "#GAMEPLAY_PHASE_POOLS",
        "bsr.w   EngineUpdateAllPools",
        ".campaign_vocabulary:",
        "bsr.w   VocabularyUpdateDevices",
        "bsr.w   CampaignEvaluateDependencies",
        "bsr.w   VocabularyBuildBeamCache",
        "bsr.w   VocabularyUpdateEnemies",
        "bsr.w   VocabularyUpdateProjectilesAndDamage",
        "bsr.w   CampaignEvaluateDependencies",
        "bsr.w   CampaignUpdateHazardControllers",
        "bsr.w   VocabularyUpdateActorsAndHazards",
        "bsr.w   VocabularyTickActorExtensions",
        "bsr.w   EngineUpdateTransientPools",
        "bsr.w   CampaignCheckCompletion",
        ".simulation_ready:",
        "addq.w  #1,d6",
        "bsr.w   GameplayBuildProjection",
        ".program_stable:",
        "#GAMEPLAY_TERMINAL_PROGRAM_STABLE",
        "bsr.w   GameplayBuildProjection",
    ),
    "src/gameplay/replay.asm": (
        "GameplayCommitAndReset:",
        "bsr.w   GameplayFinalizeCurrentBank",
        ".new_current:",
        "bsr.w   GameplayClearCurrentBank",
        "bsr.w   EngineResetCanonicalState",
        "bsr.w   GameplayReinstantiateActors",
    ),
    "src/gameplay/vertical_slice.asm": (
        "GameplayResolveVerticalSlice:",
        "andi.w  #$003F,d0",
        "(GAMEPLAY_CURRENT_PRESSED).l",
        "#INPUT_B,d0",
        "#B01_TERMINAL_MIN_X_16_8",
        "#B01_DEVICE_STATE_PROGRAM_STABLE",
    ),
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
class B01Fixture:
    room: tuple[int, int, int, int]
    spawn: tuple[int, int]
    rects: tuple[StaticRect, ...]
    objects: tuple[ObjectInit, ...]


EXPECTED_B01_FIXTURE = B01Fixture(
    room=(0, 0, 320, 224),
    spawn=(32, 112),
    rects=(
        StaticRect(112, 48, 16, 128),
        StaticRect(208, 80, 24, 16),
    ),
    objects=(
        ObjectInit(1, 0, 1, 160, 64, 0, 3),
        ObjectInit(3, 0, 1, 80, 112, 0, 0),
        ObjectInit(4, 0, 1, 48, 152, 0, 0),
        ObjectInit(5, 0, 1, 176, 176, 0, 1),
    ),
)


@dataclass(frozen=True)
class GameplayContract:
    root: Path
    constants: Mapping[str, int]
    fixture: B01Fixture
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
            if node.id not in symbols:
                raise ContractError(
                    f"unknown symbol {node.id!r} in expression {expression!r}"
                )
            return symbols[node.id]
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


def _parse_equ_file(path: Path, symbols: dict[str, int]) -> None:
    equ_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+equ\s+(.+)$", re.I)
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = _strip_comment(raw_line)
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


def _extract_block(text: str, start: str, end: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(start)}:\s*$" rf"(.*?)" rf"^\s*{re.escape(end)}:\s*$"
    )
    match = pattern.search(text)
    if not match:
        raise ContractError(f"missing declarative block {start}..{end}")
    return match.group(1)


def _data_events(
    block: str,
    symbols: Mapping[str, int],
    *,
    limit: int | None = None,
) -> list[tuple[str, tuple[int, ...]]]:
    pattern = re.compile(r"^dc\.(b|w|l)\s+(.+)$", re.I)
    events: list[tuple[str, tuple[int, ...]]] = []
    for raw_line in block.splitlines():
        line = _strip_comment(raw_line)
        match = pattern.match(line)
        if not match:
            continue
        if limit is not None and len(events) >= limit:
            break
        width, operands = match.groups()
        values = tuple(
            _evaluate_expression(operand.strip(), symbols)
            for operand in operands.split(",")
        )
        events.append((width.lower(), values))
    return events


def _parse_b01_fixture(path: Path, symbols: Mapping[str, int]) -> B01Fixture:
    text = path.read_text(encoding="utf-8")
    descriptor_events = _data_events(
        _extract_block(text, "EncounterDescriptor0", "EncounterDescriptor0End"),
        symbols,
        limit=6,
    )
    if len(descriptor_events) < 6:
        raise ContractError("encounter zero descriptor is incomplete")
    expected_prefix = (
        ("w", (0,)),
        ("w", (0,)),
        ("w", (1,)),
        ("w", (0x20,)),
    )
    if tuple(descriptor_events[:4]) != expected_prefix:
        raise ContractError("encounter zero descriptor header drift")
    room = descriptor_events[4][1]
    spawn = descriptor_events[5][1]
    if len(room) != 4 or len(spawn) != 2:
        raise ContractError("encounter zero room/spawn shape drift")

    rect_events = _data_events(
        _extract_block(text, "Encounter0Rects", "Encounter0RectsEnd"),
        symbols,
    )
    rects: list[StaticRect] = []
    for width, values in rect_events:
        if width != "w" or len(values) != 4:
            raise ContractError("encounter zero rectangle shape drift")
        rects.append(StaticRect(*values))

    object_events = _data_events(
        _extract_block(text, "Encounter0Objects", "Encounter0ObjectsEnd"),
        symbols,
    )
    if len(object_events) % 2:
        raise ContractError("encounter zero object record is incomplete")
    objects: list[ObjectInit] = []
    for index in range(0, len(object_events), 2):
        byte_event, word_event = object_events[index : index + 2]
        if byte_event[0] != "b" or len(byte_event[1]) != 2:
            raise ContractError("encounter zero object byte header drift")
        if word_event[0] != "w" or len(word_event[1]) != 5:
            raise ContractError("encounter zero object word body drift")
        pool_type, logical_id = byte_event[1]
        state, x, y, param_0, param_1 = word_event[1]
        objects.append(
            ObjectInit(pool_type, logical_id, state, x, y, param_0, param_1)
        )

    fixture = B01Fixture(
        room=tuple(room),  # type: ignore[arg-type]
        spawn=tuple(spawn),  # type: ignore[arg-type]
        rects=tuple(rects),
        objects=tuple(objects),
    )
    if fixture != EXPECTED_B01_FIXTURE:
        raise ContractError("B01 semantic fixture drift")
    return fixture


def _verify_behavior_markers(root: Path) -> None:
    for relative, labels in REQUIRED_LABELS.items():
        text = (root / relative).read_text(encoding="utf-8")
        for label in labels:
            if not re.search(rf"(?m)^\s*{re.escape(label)}:\s*$", text):
                raise ContractError(f"missing required public label {label}")
    for relative, fragments in REQUIRED_ORDERED_FRAGMENTS.items():
        text = (root / relative).read_text(encoding="utf-8")
        cursor = 0
        for fragment in fragments:
            position = text.find(fragment, cursor)
            if position < 0:
                raise ContractError(
                    f"behavior order drift in {relative}: missing/out-of-order "
                    f"{fragment!r}"
                )
            cursor = position + len(fragment)


def load_gameplay_contract(
    root: Path | str,
    *,
    verify_source_hashes: bool = True,
) -> GameplayContract:
    """Load the frozen M4 model's declarative and ordered contracts.

    Exact source verification identifies only the frozen M4 implementation.
    Disabling it still checks the retained constants, B01 fixture, include
    boundary, labels, and ordered behavior markers, but must not be treated as
    a current-runtime equivalence proof.
    """
    root_path = Path(root).resolve()
    expected_gameplay_files = {
        Path(relative).name
        for relative in RUNTIME_SOURCE_SHA256
        if relative.startswith("src/gameplay/")
    }
    gameplay_entry = root_path / "src/gameplay/gameplay.asm"
    if not gameplay_entry.is_file():
        raise ContractError("missing runtime source src/gameplay/gameplay.asm")
    gameplay_text = gameplay_entry.read_text(encoding="utf-8")
    included_gameplay_files = {
        Path(match.group(1)).name
        for match in re.finditer(
            r'(?mi)^\s*include\s+"gameplay/([^"]+\.asm)"\s*(?:;.*)?$',
            gameplay_text,
        )
    }
    actual_gameplay_files = {"gameplay.asm", *included_gameplay_files}
    if actual_gameplay_files != expected_gameplay_files:
        raise ContractError(
            "included gameplay runtime source set drift: "
            f"{sorted(actual_gameplay_files)} != {sorted(expected_gameplay_files)}"
        )

    actual_hashes: dict[str, str] = {}
    for relative, expected_hash in RUNTIME_SOURCE_SHA256.items():
        path = root_path / relative
        if not path.is_file():
            raise ContractError(f"missing runtime source {relative}")
        actual_hash = sha256_file(path)
        actual_hashes[relative] = actual_hash
        if verify_source_hashes and actual_hash != expected_hash:
            raise ContractError(
                f"runtime source drift: {relative} is {actual_hash}, "
                f"expected {expected_hash}"
            )

    symbols: dict[str, int] = {}
    _parse_equ_file(root_path / "src/game/engine_layout.asm", symbols)
    _parse_equ_file(root_path / "src/gameplay/gameplay_layout.asm", symbols)
    missing = sorted(set(EXPECTED_CONSTANTS) - set(symbols))
    if missing:
        raise ContractError(f"layout is missing required constants: {missing}")
    for name, expected in EXPECTED_CONSTANTS.items():
        actual = symbols[name]
        if actual != expected:
            raise ContractError(
                f"constant drift: {name} is ${actual:X}, expected ${expected:X}"
            )

    if symbols["REPLAY_BANK_REGION_END"] != symbols["REPLAY_DESCRIPTOR_BASE"]:
        raise ContractError("replay bank/descriptor boundary mismatch")
    if symbols["REPLAY_DESCRIPTOR_REGION_END"] != symbols["TIMELINE_CONTROL_BASE"]:
        raise ContractError("descriptor/timeline boundary mismatch")
    if symbols["TIMELINE_CONTROL_END"] - symbols["REPLAY_BANK_BASE"] != 5120:
        raise ContractError("replay/timeline allocation is not exactly 5 KiB")
    if symbols["TIMELINE_CONTROL_END"] - symbols["REPLAY_DESCRIPTOR_BASE"] != 0x50:
        raise ContractError("descriptor/control remainder is not exactly $50")
    if symbols["GAMEPLAY_CONTROL_BASE"] < symbols["ENGINE_CONTROL_USED_END"]:
        raise ContractError("gameplay control overlaps frozen engine control")
    if (
        symbols["GAMEPLAY_CONTROL_BASE"] + symbols["GAMEPLAY_CONTROL_BYTES_VALUE"]
        > symbols["ENGINE_CONTROL_END"]
    ):
        raise ContractError("gameplay control ABI exceeds FF1400-17FF")
    if symbols["ENGINE_STAGING_END"] != 0x00FF8000:
        raise ContractError("gameplay/runtime ownership reaches renderer RAM")

    _verify_behavior_markers(root_path)
    fixture = _parse_b01_fixture(
        root_path / "src/game/encounters.asm",
        symbols,
    )
    return GameplayContract(
        root=root_path,
        constants=symbols,
        fixture=fixture,
        source_sha256=dict(sorted(actual_hashes.items())),
    )
