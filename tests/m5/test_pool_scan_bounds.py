"""Contract tests for using pool high-water marks as exclusive scan bounds.

These tests bind the allocator/reset facts that make the implemented ten-loop
optimization legal, prove the invariant in a host state model, and enforce the
exact gameplay scan sites used by the first high-water-bound pass.

The assembly interface is uniform at every site:

* read the pool's ``ENGINE_POOL_HIGH_WATER`` byte as an unsigned exclusive
  slot bound;
* clamp it to that pool's compile-time capacity before address arithmetic;
* test zero before subtracting one for a ``dbf`` loop; and
* take the site-specific zero exit in ``SCAN_SITES`` so required setup,
  publication, and later independent work still happen.

Actor loops are intentionally excluded.  Echoes occupy a prefix, but the
current actor is fixed at physical slot 6, so ``ENGINE_ACTIVE_ACTOR_COUNT`` is
not an exclusive physical-slot bound.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import re
import unittest
from pathlib import Path

from tools.m3_reference.contracts import parse_layout_constants


ROOT = Path(__file__).resolve().parents[2]
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")
C = parse_layout_constants(ROOT / "src/game/engine_layout.asm")


@dataclass(frozen=True)
class PoolSpec:
    name: str
    type_symbol: str
    capacity_symbol: str

    @property
    def pool_type(self) -> int:
        return C[self.type_symbol]

    @property
    def capacity(self) -> int:
        return C[self.capacity_symbol]


POOL_SPECS = (
    PoolSpec("enemy", "POOL_TYPE_ENEMY", "ENEMY_CAPACITY"),
    PoolSpec("projectile", "POOL_TYPE_PROJECTILE", "PROJECTILE_CAPACITY"),
    PoolSpec("device", "POOL_TYPE_DEVICE", "DEVICE_CAPACITY"),
    PoolSpec("carryable", "POOL_TYPE_CARRYABLE", "CARRYABLE_CAPACITY"),
    PoolSpec("hazard", "POOL_TYPE_HAZARD", "HAZARD_CAPACITY"),
    PoolSpec("boss", "POOL_TYPE_BOSS", "BOSS_COMPONENT_CAPACITY"),
    PoolSpec("effect", "POOL_TYPE_EFFECT", "EFFECT_CAPACITY"),
)


@dataclass
class PoolHistory:
    """Legal allocator history, mirroring the assembly's count/high-water ABI."""

    slots: list[bool]
    live_count: int = 0
    high_water: int = 0
    maximum_live_count: int = 0

    @classmethod
    def empty(cls, capacity: int) -> "PoolHistory":
        if capacity <= 0:
            raise ValueError("pool capacity must be positive")
        return cls([False] * capacity)

    @property
    def capacity(self) -> int:
        return len(self.slots)

    def copy(self) -> "PoolHistory":
        return PoolHistory(
            self.slots.copy(),
            self.live_count,
            self.high_water,
            self.maximum_live_count,
        )

    def allocate_lowest(self) -> int:
        try:
            slot = self.slots.index(False)
        except ValueError:
            return 0xFFFF
        self.slots[slot] = True
        self.live_count += 1
        self.maximum_live_count = max(
            self.maximum_live_count, self.live_count
        )
        self.high_water = max(self.high_water, self.live_count)
        self.assert_invariant()
        return slot

    def free(self, slot: int) -> None:
        if not 0 <= slot < self.capacity:
            raise ValueError("slot outside pool")
        if not self.slots[slot]:
            raise ValueError("legal histories only free active slots")
        self.slots[slot] = False
        self.live_count -= 1
        self.assert_invariant()

    def reset(self) -> None:
        self.slots[:] = [False] * self.capacity
        self.live_count = 0
        self.high_water = 0
        self.maximum_live_count = 0
        self.assert_invariant()

    def assert_invariant(self) -> None:
        assert self.live_count == sum(self.slots)
        assert self.high_water == self.maximum_live_count
        assert 0 <= self.live_count <= self.high_water <= self.capacity
        assert all(
            not active or slot < self.high_water
            for slot, active in enumerate(self.slots)
        )


def clamped_scan_bound(raw_high_water: int, capacity: int) -> int:
    """Decode the byte ledger defensively without allowing an OOB scan."""

    if not 0 <= raw_high_water <= 0xFF:
        raise ValueError("high-water storage is one unsigned byte")
    if capacity <= 0:
        raise ValueError("pool capacity must be positive")
    return min(raw_high_water, capacity)


def bounded_active_slots(history: PoolHistory) -> tuple[int, ...]:
    bound = clamped_scan_bound(history.high_water, history.capacity)
    return tuple(slot for slot in range(bound) if history.slots[slot])


def full_active_slots(history: PoolHistory) -> tuple[int, ...]:
    return tuple(slot for slot, active in enumerate(history.slots) if active)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _routine(relative: str, label: str) -> str:
    source = _source(relative)
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


@dataclass(frozen=True)
class RoutineSource:
    path: str
    body: str


CONTROL_TRANSFER_RE = re.compile(
    r"^\s*("
    r"(?:bsr|jsr|jmp|bra|bcc|bcs|beq|bne|bge|bgt|bhi|ble|bls|blt|"
    r"bmi|bpl|bvc|bvs|dbf|dbra)(?:\.[a-z]+)?"
    r")\s+(.+?)\s*$",
    re.IGNORECASE,
)
SYMBOLIC_TARGET_RES = (
    re.compile(r"^([A-Z][A-Za-z0-9_]*)$"),
    re.compile(r"^\(([A-Z][A-Za-z0-9_]*)\)\.(?:l|w)$", re.IGNORECASE),
    re.compile(r"^([A-Z][A-Za-z0-9_]*)\(pc\)$", re.IGNORECASE),
    re.compile(r"^\(([A-Z][A-Za-z0-9_]*),pc\)$", re.IGNORECASE),
)


def _all_routines() -> dict[str, RoutineSource]:
    routines: dict[str, RoutineSource] = {}
    for path in sorted((ROOT / "src").rglob("*.asm")):
        source = path.read_text(encoding="utf-8")
        labels = list(GLOBAL_LABEL_RE.finditer(source))
        for index, match in enumerate(labels):
            label = match.group(1)
            if label in routines:
                raise AssertionError(f"duplicate global assembly label {label}")
            end = labels[index + 1].start() if index + 1 < len(labels) else len(source)
            routines[label] = RoutineSource(
                path.relative_to(ROOT).as_posix(), source[match.start():end]
            )
    return routines


def _symbolic_target(operand_text: str) -> str | None:
    operand_text = operand_text.strip()
    for pattern in SYMBOLIC_TARGET_RES:
        match = pattern.fullmatch(operand_text)
        if match:
            return match.group(1)
    return None


def _direct_callees(
    routine: RoutineSource,
    all_routines: dict[str, RoutineSource],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return symbolic callees and unresolved non-local control transfers.

    Local BSRs/branches stay inside the already captured global-routine body.
    Any indirect or unknown global transfer is returned as unresolved so a
    scoped closure cannot silently hide a transitive allocator.
    """

    callees: list[str] = []
    unresolved: list[tuple[str, str]] = []
    for raw_line in routine.body.splitlines():
        code = raw_line.split(";", 1)[0].rstrip()
        match = CONTROL_TRANSFER_RE.match(code)
        if not match:
            continue
        mnemonic, operands = match.groups()
        operand_parts = _split_operands(operands)
        target_text = operand_parts[-1]
        if target_text.startswith("."):
            continue
        target = _symbolic_target(target_text)
        if target is not None and target in all_routines:
            callees.append(target)
            continue
        unresolved.append((mnemonic.lower(), target_text))
    return tuple(callees), tuple(unresolved)


def _reachable_routines(
    root: str,
    all_routines: dict[str, RoutineSource],
) -> tuple[set[str], tuple[tuple[str, str, str], ...]]:
    reachable: set[str] = set()
    unresolved: list[tuple[str, str, str]] = []
    pending = [root]
    while pending:
        label = pending.pop()
        if label in reachable:
            continue
        if label not in all_routines:
            unresolved.append((label, "missing", label))
            continue
        reachable.add(label)
        callees, routine_unresolved = _direct_callees(
            all_routines[label], all_routines
        )
        unresolved.extend(
            (label, mnemonic, target)
            for mnemonic, target in routine_unresolved
        )
        pending.extend(reversed(callees))
    return reachable, tuple(unresolved)


def _ordered(source: str, fragments: tuple[str, ...]) -> list[int]:
    positions: list[int] = []
    cursor = 0
    for fragment in fragments:
        position = source.index(fragment, cursor)
        positions.append(position)
        cursor = position + len(fragment)
    return positions


def _split_operands(operand_text: str) -> list[str]:
    operands: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(operand_text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            operands.append(operand_text[start:index].strip())
            start = index + 1
    operands.append(operand_text[start:].strip())
    return operands


def _object_active_writers() -> tuple[tuple[str, str], ...]:
    """Inventory explicit symbolic writes to OBJECT_ACTIVE.

    This deliberately does not claim to prove writes through numeric offsets
    or aliased address registers.  It does reject every currently parseable
    instruction that names OBJECT_ACTIVE as a destination, including bit and
    read-modify-write opcodes rather than relying on a short writer allowlist.
    """

    writers: list[tuple[str, str]] = []
    read_only_destination_mnemonics = {
        "btst",
        "cmp",
        "cmpa",
        "cmpi",
        "lea",
        "pea",
        "tst",
    }
    for path in sorted((ROOT / "src").rglob("*.asm")):
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            code = raw_line.split(";", 1)[0].strip()
            if "OBJECT_ACTIVE" not in code or not code:
                continue
            pieces = code.split(None, 1)
            if len(pieces) != 2:
                continue
            mnemonic, operand_text = pieces
            operands = _split_operands(operand_text)
            mnemonic_base = mnemonic.lower().split(".", 1)[0]
            if (
                operands
                and "OBJECT_ACTIVE" in operands[-1]
                and mnemonic_base not in read_only_destination_mnemonics
            ):
                writers.append(
                    (path.relative_to(ROOT).as_posix(), " ".join(code.split()))
                )
    return tuple(writers)


@dataclass(frozen=True)
class ScanSite:
    path: str
    routine: str
    pool_base: str | None
    capacity_symbol: str | None
    loop_label: str
    counter: str
    zero_exit: str
    zero_exit_exists: bool
    required_before: tuple[str, ...] = ()
    required_after: tuple[str, ...] = ()


# This is intentionally the first, measured-B03-oriented pass rather than an
# indiscriminate rewrite of every rare interaction/beam/repair search.
SCAN_SITES = (
    ScanSite(
        "src/gameplay/campaign_collision.asm",
        "GameplayCampaignResolveDoorX",
        "DEVICE_POOL",
        "DEVICE_CAPACITY",
        ".door",
        "d7",
        ".done",
        True,
        required_before=("suba.l  a0,a0",),
        required_after=("move.l  d3,d0", "move.l  a0,d1"),
    ),
    ScanSite(
        "src/gameplay/campaign_collision.asm",
        "GameplayCampaignResolveDoorY",
        "DEVICE_POOL",
        "DEVICE_CAPACITY",
        ".door",
        "d7",
        ".done",
        True,
        required_before=("suba.l  a0,a0",),
        required_after=("move.l  d3,d0", "move.l  a0,d1"),
    ),
    ScanSite(
        "src/gameplay/vocabulary_combat.asm",
        "VocabularyUpdateProjectilesAndDamage",
        "PROJECTILE_POOL",
        "PROJECTILE_CAPACITY",
        ".projectile",
        "d7",
        ".count_ready",
        True,
        required_before=("moveq   #0,d6",),
        required_after=(
            "move.b  d6,(VOCAB_ACTIVE_PROJECTILE_COUNT).l",
        ),
    ),
    ScanSite(
        "src/gameplay/vocabulary_combat.asm",
        "VocabularyUpdateEnemies",
        "ENEMY_POOL",
        "ENEMY_CAPACITY",
        ".enemy",
        "d7",
        ".count_ready",
        True,
        required_before=("dbf     d1,.clear_targets", "moveq   #0,d6"),
        required_after=("move.b  d6,(VOCAB_ACTIVE_ENEMY_COUNT).l",),
    ),
    ScanSite(
        "src/gameplay/vocabulary_combat.asm",
        "VocabularyUpdateActorsAndHazards",
        "DEVICE_POOL",
        "DEVICE_CAPACITY",
        ".shield_device",
        "d6",
        ".hazards",
        True,
        required_before=(
            "andi.w  #$FFEF,(VOCAB_ACTOR_FLAGS,a3)",
            "clr.b   (VOCAB_ACTOR_SHIELD_TIMER,a3)",
        ),
        required_after=("lea     (HAZARD_POOL).l,a4",),
    ),
    ScanSite(
        "src/gameplay/vocabulary_combat.asm",
        "VocabularyUpdateActorsAndHazards",
        "HAZARD_POOL",
        "HAZARD_CAPACITY",
        ".hazard",
        "d6",
        ".beam_setup",
        True,
        required_before=(".hazards:",),
        required_after=("move.b  (VOCAB_BEAM_COUNT).l,d6",),
    ),
    ScanSite(
        "src/gameplay/vocabulary_interactions.asm",
        "VocabularyUpdateDevices",
        "DEVICE_POOL",
        "DEVICE_CAPACITY",
        ".device",
        "d7",
        ".done",
        True,
        required_before=("bsr.w   VocabularyFollowCarryables",),
    ),
    ScanSite(
        "src/gameplay/vocabulary_interactions.asm",
        "VocabularyBuildBeamCache",
        "DEVICE_POOL",
        "DEVICE_CAPACITY",
        ".emitter",
        "d7",
        ".done",
        True,
        required_before=(
            "dbf     d1,.clear_beams",
            "clr.b   (VOCAB_BEAM_COUNT).l",
            "moveq   #0,d6",
        ),
    ),
    ScanSite(
        "src/gameplay/campaign_runtime.asm",
        "CampaignUpdateHazardControllers",
        "HAZARD_POOL",
        "HAZARD_CAPACITY",
        ".hazard",
        "d7",
        ".ok",
        True,
        required_before=(
            "cmpi.w  #VOCAB_CAMPAIGN_FLAG_ACTIVE|VOCAB_CAMPAIGN_FLAG_PROFILE_VALID,d0",
        ),
        required_after=("moveq   #GAMEPLAY_RESULT_OK,d0",),
    ),
    ScanSite(
        "src/game/object_pool.asm",
        "EngineUpdatePool",
        None,
        None,
        ".next",
        "d3",
        ".done",
        True,
        required_before=("move.w  d0,d3", "movea.l a0,a2"),
    ),
)


# Exact symbolic closure for the nine distinct first-pass routine roots.
# Repeated callees are intentional and bind the current number/order of call
# sites, while the reachability test below independently reasons about typed
# allocator destinations.
EXPECTED_SCOPED_CALLS: dict[str, tuple[str, ...]] = {
    "CampaignFindSemantic": (),
    "CampaignUpdateHazardControllers": ("CampaignFindSemantic",),
    "EnginePoolAllocateLowest": (),
    "EnginePoolFreeSlot": (),
    "EnginePoolNoteAllocation": (),
    "EngineUpdatePool": ("EnginePoolFreeSlot",),
    "GameplayCampaignActorInHazard": ("VocabularyWithinRange",),
    "GameplayCampaignClipBeam": (),
    "GameplayCampaignLatchDashDoorRoute": (),
    "GameplayCampaignMoveActorByDelta": (
        "GameplayMoveActorAxisSeparated",
    ),
    "GameplayCampaignMoveObjectAxisSeparated": (
        "GameplayCampaignObjectBlocked",
        "GameplayCampaignObjectBlocked",
    ),
    "GameplayCampaignObjectBlocked": (),
    "GameplayCampaignProjectileHitsStatic": (
        "GameplayCampaignProjectileMayEnterNodeRect",
    ),
    "GameplayCampaignProjectileMayEnterNodeRect": (),
    "GameplayCampaignResolveDoorX": (
        "GameplayCampaignLatchDashDoorRoute",
    ),
    "GameplayCampaignResolveDoorY": (),
    "GameplayMoveActorAxisSeparated": (
        "GameplayCampaignResolveDoorX",
        "GameplayCampaignResolveDoorY",
    ),
    "VocabularyActorOnBeam": (),
    "VocabularyBuildBeamCache": (
        "GameplayCampaignClipBeam",
        "GameplayCampaignClipBeam",
    ),
    "VocabularyDamageActor": (
        "VocabularyGetActorExtension",
        "VocabularyDropActorCarryable",
    ),
    "VocabularyDamageEnemy": ("EnginePoolFreeSlot",),
    "VocabularyDamageSecurityNode": (),
    "VocabularyDropActorCarryable": (),
    "VocabularyFindEnemyTarget": (),
    "VocabularyFollowCarryables": (),
    "VocabularyGetActorExtension": (),
    "VocabularySpawnEnemyProjectile": (
        "EnginePoolAllocateLowest",
        "EnginePoolNoteAllocation",
    ),
    "VocabularyUpdateActorsAndHazards": (
        "VocabularyWithinRange",
        "GameplayCampaignActorInHazard",
        "GameplayCampaignMoveActorByDelta",
        "VocabularyDamageActor",
        "VocabularyActorOnBeam",
        "VocabularyDamageActor",
    ),
    "VocabularyUpdateDevices": (
        "VocabularyFollowCarryables",
        "VocabularyWithinRange",
        "VocabularyWithinRange",
        "GameplayCampaignMoveActorByDelta",
    ),
    "VocabularyUpdateEnemies": (
        "VocabularyFindEnemyTarget",
        "VocabularySpawnEnemyProjectile",
        "GameplayCampaignMoveObjectAxisSeparated",
    ),
    "VocabularyUpdateProjectilesAndDamage": (
        "GameplayCampaignProjectileHitsStatic",
        "EnginePoolFreeSlot",
        "EnginePoolFreeSlot",
        "VocabularyDamageEnemy",
        "EnginePoolFreeSlot",
        "VocabularyDamageSecurityNode",
        "EnginePoolFreeSlot",
        "VocabularyWithinRange",
        "VocabularyDamageActor",
        "EnginePoolFreeSlot",
    ),
    "VocabularyWithinRange": (),
}


SCOPED_ROOT_POOLS: dict[str, frozenset[str]] = {
    "GameplayCampaignResolveDoorX": frozenset(("DEVICE_POOL",)),
    "GameplayCampaignResolveDoorY": frozenset(("DEVICE_POOL",)),
    "VocabularyUpdateProjectilesAndDamage": frozenset(("PROJECTILE_POOL",)),
    "VocabularyUpdateEnemies": frozenset(("ENEMY_POOL",)),
    "VocabularyUpdateActorsAndHazards": frozenset(
        ("DEVICE_POOL", "HAZARD_POOL")
    ),
    "VocabularyUpdateDevices": frozenset(("DEVICE_POOL",)),
    "VocabularyBuildBeamCache": frozenset(("DEVICE_POOL",)),
    "CampaignUpdateHazardControllers": frozenset(("HAZARD_POOL",)),
    # d2 selects any one of the seven pool types for this generic routine.
    "EngineUpdatePool": frozenset(
        (
            "ENEMY_POOL",
            "PROJECTILE_POOL",
            "DEVICE_POOL",
            "CARRYABLE_POOL",
            "HAZARD_POOL",
            "BOSS_COMPONENT_POOL",
            "EFFECT_POOL",
        )
    ),
}


# Every routine that directly invokes the primitive allocator must have an
# audited destination.  Canonical instantiation is intentionally dynamic and
# is forbidden from the gameplay scan closures; the two runtime allocators are
# projectile-only.
ALLOCATOR_CALLER_DESTINATIONS: dict[str, str | None] = {
    "EngineInstantiateCanonicalObject": None,
    "EngineSpawnProjectileFromActor": "PROJECTILE_POOL",
    "VocabularySpawnEnemyProjectile": "PROJECTILE_POOL",
}


POOL_TYPE_BY_BASE = {
    "ENEMY_POOL": "POOL_TYPE_ENEMY",
    "PROJECTILE_POOL": "POOL_TYPE_PROJECTILE",
    "DEVICE_POOL": "POOL_TYPE_DEVICE",
    "CARRYABLE_POOL": "POOL_TYPE_CARRYABLE",
    "HAZARD_POOL": "POOL_TYPE_HAZARD",
    "BOSS_COMPONENT_POOL": "POOL_TYPE_BOSS",
    "EFFECT_POOL": "POOL_TYPE_EFFECT",
}
BOUND_LABELS = {
    ("GameplayCampaignResolveDoorX", ".door"): ".door_bound_ready",
    ("GameplayCampaignResolveDoorY", ".door"): ".door_bound_ready",
    (
        "VocabularyUpdateProjectilesAndDamage",
        ".projectile",
    ): ".projectile_bound_ready",
    ("VocabularyUpdateEnemies", ".enemy"): ".enemy_bound_ready",
    (
        "VocabularyUpdateActorsAndHazards",
        ".shield_device",
    ): ".shield_bound_ready",
    (
        "VocabularyUpdateActorsAndHazards",
        ".hazard",
    ): ".hazard_bound_ready",
    ("VocabularyUpdateDevices", ".device"): ".device_bound_ready",
    ("VocabularyBuildBeamCache", ".emitter"): ".emitter_bound_ready",
    (
        "CampaignUpdateHazardControllers",
        ".hazard",
    ): ".hazard_bound_ready",
    ("EngineUpdatePool", ".next"): ".bound_ready",
}


def _typed_allocation_violations(
    root: str,
    all_routines: dict[str, RoutineSource],
) -> tuple[tuple[str, str | None], ...]:
    reachable, unresolved = _reachable_routines(root, all_routines)
    if unresolved:
        raise AssertionError(f"unresolved scoped transfers: {unresolved}")
    scanned_pools = SCOPED_ROOT_POOLS[root]
    violations: list[tuple[str, str | None]] = []
    for caller in sorted(reachable):
        callees, _ = _direct_callees(all_routines[caller], all_routines)
        for _ in range(callees.count("EnginePoolAllocateLowest")):
            destination = ALLOCATOR_CALLER_DESTINATIONS.get(caller)
            if destination is None or destination in scanned_pools:
                violations.append((caller, destination))
    return tuple(violations)


class PoolHighWaterModelTests(unittest.TestCase):
    def test_real_pool_types_and_capacities_are_complete(self) -> None:
        self.assertEqual(
            tuple(spec.pool_type for spec in POOL_SPECS),
            tuple(range(1, C["POOL_TYPE_COUNT"] + 1)),
        )
        self.assertEqual(
            sum(spec.capacity for spec in POOL_SPECS),
            C["TOTAL_POOL_CAPACITY"],
        )
        self.assertTrue(all(spec.capacity > 0 for spec in POOL_SPECS))

    def test_lowest_free_allocation_and_hole_reuse(self) -> None:
        history = PoolHistory.empty(C["DEVICE_CAPACITY"])
        self.assertEqual(
            [history.allocate_lowest() for _ in range(6)],
            [0, 1, 2, 3, 4, 5],
        )
        self.assertEqual(history.high_water, 6)

        history.free(1)
        history.free(4)
        self.assertEqual(history.allocate_lowest(), 1)
        self.assertEqual(history.allocate_lowest(), 4)
        self.assertEqual(history.high_water, 6)
        self.assertEqual(bounded_active_slots(history), full_active_slots(history))

    def test_high_water_is_maximum_live_count_not_current_count(self) -> None:
        history = PoolHistory.empty(8)
        for expected_slot in range(5):
            self.assertEqual(history.allocate_lowest(), expected_slot)
        history.free(0)
        history.free(3)
        self.assertEqual(history.live_count, 3)
        self.assertEqual(history.high_water, 5)
        self.assertEqual(history.maximum_live_count, 5)

        self.assertEqual(history.allocate_lowest(), 0)
        self.assertEqual(history.live_count, 4)
        self.assertEqual(history.high_water, 5)
        self.assertEqual(bounded_active_slots(history), full_active_slots(history))

    def test_every_real_capacity_handles_zero_saturation_and_reset(self) -> None:
        for spec in POOL_SPECS:
            with self.subTest(pool=spec.name):
                history = PoolHistory.empty(spec.capacity)
                self.assertEqual(history.high_water, 0)
                self.assertEqual(bounded_active_slots(history), ())

                self.assertEqual(
                    [history.allocate_lowest() for _ in range(spec.capacity)],
                    list(range(spec.capacity)),
                )
                before = history.copy()
                self.assertEqual(history.allocate_lowest(), 0xFFFF)
                self.assertEqual(history.slots, before.slots)
                self.assertEqual(history.live_count, before.live_count)
                self.assertEqual(history.high_water, spec.capacity)
                self.assertEqual(
                    bounded_active_slots(history), full_active_slots(history)
                )

                history.reset()
                self.assertEqual(history.slots, [False] * spec.capacity)
                self.assertEqual(history.live_count, 0)
                self.assertEqual(history.high_water, 0)
                self.assertEqual(history.maximum_live_count, 0)

    def test_out_of_range_byte_is_clamped_to_each_capacity(self) -> None:
        for spec in POOL_SPECS:
            with self.subTest(pool=spec.name):
                self.assertEqual(clamped_scan_bound(0, spec.capacity), 0)
                self.assertEqual(
                    clamped_scan_bound(spec.capacity, spec.capacity),
                    spec.capacity,
                )
                self.assertEqual(
                    clamped_scan_bound(spec.capacity + 1, spec.capacity),
                    spec.capacity,
                )
                self.assertEqual(
                    clamped_scan_bound(0xFF, spec.capacity), spec.capacity
                )
        with self.assertRaises(ValueError):
            clamped_scan_bound(-1, C["DEVICE_CAPACITY"])
        with self.assertRaises(ValueError):
            clamped_scan_bound(0x100, C["DEVICE_CAPACITY"])

    def test_exhaustive_legal_histories_keep_live_slots_below_bound(self) -> None:
        capacity = 4
        initial = PoolHistory.empty(capacity)
        queue = deque([initial])
        visited: set[tuple[tuple[bool, ...], int, int]] = set()
        while queue:
            history = queue.popleft()
            key = (
                tuple(history.slots),
                history.live_count,
                history.high_water,
            )
            if key in visited:
                continue
            visited.add(key)
            history.assert_invariant()
            self.assertEqual(
                bounded_active_slots(history), full_active_slots(history)
            )

            allocated = history.copy()
            if allocated.allocate_lowest() != 0xFFFF:
                queue.append(allocated)
            for slot, active in enumerate(history.slots):
                if active:
                    freed = history.copy()
                    freed.free(slot)
                    queue.append(freed)

        self.assertGreater(len(visited), 1 << capacity)

    def test_bound_is_a_snapshot_and_same_pool_growth_would_be_unsafe(self) -> None:
        history = PoolHistory.empty(4)
        self.assertEqual(history.allocate_lowest(), 0)
        snapshot = clamped_scan_bound(
            history.high_water, history.capacity
        )

        # This is the forbidden shape for a bounded loop: allocation after the
        # entry snapshot can make a newly active slot visible to the original
        # capacity scan but not to the bounded scan.
        self.assertEqual(history.allocate_lowest(), 1)
        snapshot_active = tuple(
            slot for slot in range(snapshot) if history.slots[slot]
        )
        self.assertNotEqual(snapshot_active, full_active_slots(history))
        self.assertEqual(snapshot_active, (0,))
        self.assertEqual(full_active_slots(history), (0, 1))

    def test_different_pool_spawn_is_seen_when_target_snapshots_later(self) -> None:
        enemies = PoolHistory.empty(C["ENEMY_CAPACITY"])
        projectiles = PoolHistory.empty(C["PROJECTILE_CAPACITY"])
        self.assertEqual(enemies.allocate_lowest(), 0)

        # An enemy update may spawn into the different projectile pool.  The
        # projectile loop is safe only because it takes its own bound later.
        self.assertEqual(projectiles.allocate_lowest(), 0)
        projectile_snapshot = clamped_scan_bound(
            projectiles.high_water, projectiles.capacity
        )
        self.assertEqual(projectile_snapshot, 1)
        self.assertEqual(
            tuple(
                slot
                for slot in range(projectile_snapshot)
                if projectiles.slots[slot]
            ),
            full_active_slots(projectiles),
        )

    def test_actor_count_cannot_bound_the_fixed_current_slot(self) -> None:
        layout = _source("src/gameplay/gameplay_layout.asm")
        match = re.search(
            r"(?m)^GAMEPLAY_CURRENT_ACTOR_SLOT\s+equ\s+(\d+)\s*$",
            layout,
        )
        self.assertIsNotNone(match)
        current_slot = int(match.group(1))
        self.assertEqual(current_slot, C["ACTOR_CAPACITY"] - 1)

        actors = [False] * C["ACTOR_CAPACITY"]
        actors[current_slot] = True
        active_count = sum(actors)
        naive_prefix = tuple(range(active_count))
        self.assertNotIn(current_slot, naive_prefix)
        self.assertFalse(any(actors[slot] for slot in naive_prefix))


class PoolHighWaterSourceContractTests(unittest.TestCase):
    def test_allocator_scans_from_zero_and_returns_lowest_free_index(self) -> None:
        routine = _routine(
            "src/game/object_pool.asm", "EnginePoolAllocateLowest"
        )
        fragments = (
            "moveq   #0,d4",
            ".scan:",
            "tst.b   (a0)",
            "beq.s   .found",
            "adda.w  d1,a0",
            "addq.w  #1,d4",
            "dbf     d3,.scan",
            ".found:",
            "move.b  #$01,(OBJECT_ACTIVE,a0)",
            "move.l  d4,d0",
        )
        positions = _ordered(routine, fragments)
        self.assertEqual(positions, sorted(positions))

    def test_note_allocation_tracks_maximum_live_count(self) -> None:
        routine = _routine(
            "src/game/object_pool.asm", "EnginePoolNoteAllocation"
        )
        fragments = (
            "lea     (ENGINE_POOL_COUNTS).l,a1",
            "addq.b  #1,(a1)",
            "move.b  (a1),d1",
            "lea     (ENGINE_POOL_HIGH_WATER).l,a1",
            "cmp.b   (a1),d1",
            "bls.s   .done",
            "move.b  d1,(a1)",
        )
        positions = _ordered(routine, fragments)
        self.assertEqual(positions, sorted(positions))

    def test_every_allocator_call_notes_successful_allocation(self) -> None:
        callers = (
            (
                "src/game/object_pool.asm",
                "EngineInstantiateCanonicalObject",
            ),
            (
                "src/game/object_pool.asm",
                "EngineSpawnProjectileFromActor",
            ),
            (
                "src/gameplay/vocabulary_combat.asm",
                "VocabularySpawnEnemyProjectile",
            ),
        )
        for path, label in callers:
            with self.subTest(routine=label):
                routine = _routine(path, label)
                self.assertEqual(
                    routine.count("bsr.w   EnginePoolAllocateLowest"), 1
                )
                self.assertEqual(
                    routine.count("bsr.w   EnginePoolNoteAllocation"), 1
                )
                self.assertLess(
                    routine.index("bsr.w   EnginePoolAllocateLowest"),
                    routine.index("bsr.w   EnginePoolNoteAllocation"),
                )

        routines = _all_routines()
        actual_callers: list[str] = []
        for routine in routines.values():
            callees, _ = _direct_callees(routine, routines)
            actual_callers.extend(
                [routine.path] * callees.count("EnginePoolAllocateLowest")
            )
        self.assertEqual(
            Counter(actual_callers),
            Counter(
                {
                    "src/game/object_pool.asm": 2,
                    "src/gameplay/vocabulary_combat.asm": 1,
                }
            ),
        )

    def test_free_decrements_live_count_but_never_high_water(self) -> None:
        routine = _routine("src/game/object_pool.asm", "EnginePoolFreeSlot")
        self.assertNotIn("ENGINE_POOL_HIGH_WATER", routine)
        fragments = (
            "moveq   #(OBJECT_BYTES/4)-1,d1",
            ".clear:",
            "move.l  d0,(a1)+",
            "dbf     d1,.clear",
            "lea     (ENGINE_POOL_COUNTS).l,a1",
            "tst.b   (a1)",
            "beq.s   .done",
            "subq.b  #1,(a1)",
        )
        positions = _ordered(routine, fragments)
        self.assertEqual(positions, sorted(positions))

    def test_all_free_callers_operate_on_a_proven_active_slot(self) -> None:
        update_pool = _routine("src/game/object_pool.asm", "EngineUpdatePool")
        self.assertEqual(update_pool.count("bsr.w   EnginePoolFreeSlot"), 1)
        self.assertLess(
            update_pool.index("tst.b   (OBJECT_ACTIVE,a2)"),
            update_pool.index("bsr.w   EnginePoolFreeSlot"),
        )

        damage_enemy = _routine(
            "src/gameplay/vocabulary_combat.asm", "VocabularyDamageEnemy"
        )
        self.assertEqual(damage_enemy.count("bsr.w   EnginePoolFreeSlot"), 1)
        self.assertLess(
            damage_enemy.index("tst.b   (OBJECT_ACTIVE,a2)"),
            damage_enemy.index("bsr.w   EnginePoolFreeSlot"),
        )

        projectiles = _routine(
            "src/gameplay/vocabulary_combat.asm",
            "VocabularyUpdateProjectilesAndDamage",
        )
        self.assertEqual(projectiles.count("bsr.w   EnginePoolFreeSlot"), 5)
        active_guard = projectiles.index("tst.b   (OBJECT_ACTIVE,a2)")
        first_free = projectiles.index("bsr.w   EnginePoolFreeSlot")
        self.assertLess(active_guard, first_free)
        cursor = 0
        for _ in range(5):
            free = projectiles.index("bsr.w   EnginePoolFreeSlot", cursor)
            next_iteration = projectiles.index("bra", free)
            self.assertIn(".next", projectiles[next_iteration:next_iteration + 32])
            cursor = free + 1

        routines = _all_routines()
        actual_free_calls: list[str] = []
        for routine in routines.values():
            callees, _ = _direct_callees(routine, routines)
            actual_free_calls.extend(
                [routine.path] * callees.count("EnginePoolFreeSlot")
            )
        self.assertEqual(
            Counter(actual_free_calls),
            Counter(
                {
                    "src/game/object_pool.asm": 1,
                    "src/gameplay/vocabulary_combat.asm": 6,
                }
            ),
        )

    def test_reset_clears_counts_and_high_water_together(self) -> None:
        self.assertEqual(
            C["ENGINE_POOL_HIGH_WATER"] - C["ENGINE_POOL_COUNTS"], 8
        )
        self.assertEqual(
            C["ENGINE_CONTROL_USED_END"] - C["ENGINE_POOL_COUNTS"], 16
        )
        reconstruct = _routine(
            "src/game/engine_core.asm", "EngineReconstructCanonicalState"
        )
        fragments = (
            "lea     (ENGINE_POOL_COUNTS).l,a0",
            "moveq   #0,d0",
            "moveq   #3,d1",
            ".clear_pool_counters:",
            "move.l  d0,(a0)+",
            "dbf     d1,.clear_pool_counters",
        )
        positions = _ordered(reconstruct, fragments)
        self.assertEqual(positions, sorted(positions))

        reset = _routine(
            "src/game/engine_core.asm", "EngineResetCanonicalState"
        )
        self.assertEqual(
            reset.count("bsr.w   EngineReconstructCanonicalState"), 1
        )

    def test_only_non_allocator_activation_is_canonical_b01_slot_zero(self) -> None:
        self.assertEqual(
            _object_active_writers(),
            (
                (
                    "src/game/object_pool.asm",
                    "move.b #$01,(OBJECT_ACTIVE,a0)",
                ),
                (
                    "src/gameplay/vertical_slice.asm",
                    "move.b #$01,(B01_DEVICE+OBJECT_ACTIVE).l",
                ),
            ),
        )
        reset = _routine(
            "src/gameplay/vertical_slice.asm", "GameplayResetVerticalSlice"
        )
        self.assertIn(
            "move.b  #$01,(B01_DEVICE+OBJECT_ACTIVE).l", reset
        )
        encounter_zero = _source("src/game/encounters.asm").split(
            "Encounter0Objects:", 1
        )[1].split("Encounter0ObjectsEnd:", 1)[0]
        self.assertIn("dc.b    POOL_TYPE_DEVICE,$00", encounter_zero)

    def test_first_pass_scan_inventory_is_exact_and_source_bound(self) -> None:
        self.assertEqual(
            {site.path for site in SCAN_SITES},
            {
                "src/game/object_pool.asm",
                "src/gameplay/campaign_collision.asm",
                "src/gameplay/campaign_runtime.asm",
                "src/gameplay/vocabulary_combat.asm",
                "src/gameplay/vocabulary_interactions.asm",
            },
        )
        for site in SCAN_SITES:
            with self.subTest(routine=site.routine, loop=site.loop_label):
                routine = _routine(site.path, site.routine)
                loop = routine.index(f"{site.loop_label}:")
                bound_label = BOUND_LABELS[(site.routine, site.loop_label)]
                self.assertIn(
                    f"dbf     {site.counter},{site.loop_label}", routine
                )
                if site.pool_base is not None:
                    self.assertIn(f"({site.pool_base}).l", routine)
                if site.capacity_symbol is not None:
                    self.assertIn(site.capacity_symbol, routine)
                if site.zero_exit_exists:
                    self.assertIn(f"{site.zero_exit}:", routine)
                exit_position = routine.index(f"{site.zero_exit}:")
                for token in site.required_before:
                    self.assertLess(routine.index(token), loop)
                for token in site.required_after:
                    token_position = routine.index(token, exit_position)
                    self.assertGreater(token_position, loop)
                    self.assertLess(exit_position, token_position)

                if site.pool_base is None:
                    fragments = (
                        "move.w  d0,d3",
                        "movea.l a0,a2",
                        "lea     (ENGINE_POOL_HIGH_WATER-1).l,a1",
                        "moveq   #0,d0",
                        "move.b  (a1,d2.w),d0",
                        "cmp.w   d3,d0",
                        f"bls.s   {bound_label}",
                        "move.w  d3,d0",
                        f"{bound_label}:",
                        "tst.w   d0",
                        f"beq.s   {site.zero_exit}",
                        "move.w  d0,d3",
                        "subq.w  #1,d3",
                        f"{site.loop_label}:",
                    )
                    positions = _ordered(routine, fragments)
                    self.assertEqual(positions, sorted(positions))
                    bound_load = routine.index(
                        "lea     (ENGINE_POOL_HIGH_WATER-1).l,a1"
                    )
                else:
                    pool_type = POOL_TYPE_BY_BASE[site.pool_base]
                    setup_start = routine.rfind(
                        f"lea     ({site.pool_base}).l", 0, loop
                    )
                    self.assertGreaterEqual(setup_start, 0)
                    setup = routine[setup_start:loop + len(site.loop_label) + 1]
                    fragments = (
                        f"moveq   #0,{site.counter}",
                        (
                            "move.b  (ENGINE_POOL_HIGH_WATER+"
                            f"{pool_type}-1).l,{site.counter}"
                        ),
                        f"cmpi.w  #{site.capacity_symbol},{site.counter}",
                        f"bls.s   {bound_label}",
                        f"moveq   #{site.capacity_symbol},{site.counter}",
                        f"{bound_label}:",
                        f"tst.w   {site.counter}",
                        f"beq.w   {site.zero_exit}",
                        f"subq.w  #1,{site.counter}",
                        f"{site.loop_label}:",
                    )
                    positions = _ordered(setup, fragments)
                    self.assertEqual(positions, sorted(positions))
                    self.assertNotIn(
                        f"#{site.capacity_symbol}-1", setup
                    )
                    bound_load = routine.index(
                        "move.b  (ENGINE_POOL_HIGH_WATER+"
                        f"{pool_type}-1).l,{site.counter}",
                        setup_start,
                    )

                for token in site.required_before:
                    self.assertLess(routine.index(token), bound_load)

    def test_zero_exit_policy_preserves_non_scan_side_effects(self) -> None:
        exits = {
            (site.routine, site.loop_label): site.zero_exit
            for site in SCAN_SITES
        }
        self.assertEqual(
            exits[
                ("VocabularyUpdateProjectilesAndDamage", ".projectile")
            ],
            ".count_ready",
        )
        self.assertEqual(
            exits[("VocabularyUpdateEnemies", ".enemy")], ".count_ready"
        )
        self.assertEqual(
            exits[("VocabularyUpdateActorsAndHazards", ".shield_device")],
            ".hazards",
        )
        self.assertEqual(
            exits[("VocabularyUpdateActorsAndHazards", ".hazard")],
            ".beam_setup",
        )
        self.assertEqual(
            exits[("CampaignUpdateHazardControllers", ".hazard")], ".ok"
        )

        # Every formerly proposed label now exists in the implementation;
        # branching straight to a generic return would skip publication or
        # later independent work at the special exits below.
        self.assertTrue(all(site.zero_exit_exists for site in SCAN_SITES))
        self.assertEqual(
            {
                site.zero_exit
                for site in SCAN_SITES
                if site.zero_exit in {".beam_setup", ".ok"}
            },
            {".beam_setup", ".ok"},
        )

    def test_scoped_call_closures_are_resolved_and_exact(self) -> None:
        routines = _all_routines()
        self.assertEqual(
            set(SCOPED_ROOT_POOLS), {site.routine for site in SCAN_SITES}
        )

        actual_reachable: set[str] = set()
        for root in SCOPED_ROOT_POOLS:
            with self.subTest(root=root):
                reachable, unresolved = _reachable_routines(root, routines)
                self.assertEqual(unresolved, ())
                actual_reachable.update(reachable)
        self.assertEqual(actual_reachable, set(EXPECTED_SCOPED_CALLS))

        for label, expected_callees in EXPECTED_SCOPED_CALLS.items():
            with self.subTest(routine=label):
                actual_callees, unresolved = _direct_callees(
                    routines[label], routines
                )
                self.assertEqual(unresolved, ())
                self.assertEqual(actual_callees, expected_callees)

    def test_entry_snapshots_cannot_miss_same_pool_allocation(self) -> None:
        # There are exactly three allocation sites.  Canonical allocation runs
        # during reconstruction; the two runtime entry points allocate only
        # projectiles.  This is the source-side premise behind taking one
        # immutable bound at each proposed loop entry.
        canonical = _routine(
            "src/game/object_pool.asm", "EngineInstantiateCanonicalObject"
        )
        actor_spawn = _routine(
            "src/game/object_pool.asm", "EngineSpawnProjectileFromActor"
        )
        enemy_spawn = _routine(
            "src/gameplay/vocabulary_combat.asm",
            "VocabularySpawnEnemyProjectile",
        )
        self.assertIn("lea     (PROJECTILE_POOL).l,a0", actor_spawn)
        self.assertIn("moveq   #POOL_TYPE_PROJECTILE,d2", actor_spawn)
        self.assertIn("lea     (PROJECTILE_POOL).l,a0", enemy_spawn)
        self.assertIn("moveq   #POOL_TYPE_PROJECTILE,d2", enemy_spawn)
        self.assertIn("(EnginePoolDescriptorTable,pc)", canonical)

        routines = _all_routines()
        primitive_callers = {
            label
            for label, routine in routines.items()
            if "EnginePoolAllocateLowest"
            in _direct_callees(routine, routines)[0]
        }
        self.assertEqual(
            primitive_callers, set(ALLOCATOR_CALLER_DESTINATIONS)
        )
        for root in SCOPED_ROOT_POOLS:
            with self.subTest(root=root):
                self.assertEqual(
                    _typed_allocation_violations(root, routines), ()
                )

        # Regression for the independently identified fail-open: a projectile
        # helper that starts spawning projectiles after the outer loop's bound
        # snapshot must be rejected even though the root routine itself still
        # contains no direct allocator call.
        original = routines["VocabularyDamageEnemy"]
        mutated = dict(routines)
        mutated["VocabularyDamageEnemy"] = RoutineSource(
            original.path,
            original.body.replace(
                "VocabularyDamageEnemy:\n",
                "VocabularyDamageEnemy:\n"
                "        bsr.w   VocabularySpawnEnemyProjectile\n",
                1,
            ),
        )
        self.assertEqual(
            _typed_allocation_violations(
                "VocabularyUpdateProjectilesAndDamage", mutated
            ),
            (("VocabularySpawnEnemyProjectile", "PROJECTILE_POOL"),),
        )

        gameplay_step = _routine(
            "src/gameplay/gameplay_core.asm", "GameplayStep"
        )
        # Player/echo input (and therefore actor projectile spawning) finishes
        # before the campaign vocabulary pipeline.  Enemy projectile spawning
        # then precedes the projectile loop's high-water snapshot.
        positions = _ordered(
            gameplay_step,
            (
                "bsr.w   GameplayApplyActorInput",
                ".campaign_vocabulary:",
                "bsr.w   VocabularyUpdateEnemies",
                "bsr.w   VocabularyUpdateProjectilesAndDamage",
            ),
        )
        self.assertEqual(positions, sorted(positions))
        actor_input = _routine(
            "src/gameplay/actor_sim.asm", "GameplayApplyActorInput"
        )
        self.assertIn("bsr.w   EngineSpawnProjectileFromActor", actor_input)

        # Generic effect updates can expire/free their own slots but cannot
        # grow the scanned pool after EngineUpdatePool snapshots its bound.
        update_pool = _routine("src/game/object_pool.asm", "EngineUpdatePool")
        self.assertIn("bsr.w   EnginePoolFreeSlot", update_pool)
        self.assertNotIn("EnginePoolAllocateLowest", update_pool)


if __name__ == "__main__":
    unittest.main()
