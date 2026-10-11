"""Deterministic bounded host reference for the unwired M5 vocabulary."""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass, field
from typing import Iterable


ACTOR_CAPACITY = 7
ENEMY_CAPACITY = 16
PROJECTILE_CAPACITY = 32
DEVICE_CAPACITY = 32
CARRYABLE_CAPACITY = 8
HAZARD_CAPACITY = 16
BEAM_LIMIT = 8
ANCHOR_LIMIT = 2
ATTEMPT_TICKS = 720
UPDATE_UNIT_LIMIT = 4096
PROJECTION_BYTES = 200
ECHO_LIMIT = 6

INPUT_UP = 0x01
INPUT_DOWN = 0x02
INPUT_LEFT = 0x04
INPUT_RIGHT = 0x08
INPUT_A = 0x10
INPUT_B = 0x20
INPUT_C = 0x40

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

STATE_ACTIVE = 0x0001
STATE_OPEN = 0x0002
STATE_HACKED = 0x0004
STATE_DISABLED = 0x0008
STATE_ANCHORED = 0x0010
STATE_OCCUPIED = 0x0020

ACTOR_DASHING = 0x0001
ACTOR_PHASED = 0x0002
ACTOR_INVULNERABLE = 0x0004
ACTOR_CARRYING = 0x0008
ACTOR_SHIELDED = 0x0010
ACTOR_HURT = 0x0020

PROJECTILE_PLAYER = 0x0001
PROJECTILE_ENEMY = 0x0002
PROJECTILE_REFLECTED = 0x0004

ANCHOR_DEVICE_STATE = 1
ANCHOR_SOCKETED_CELL = 2
ANCHOR_CONVEYOR_DIRECTION = 3
ANCHOR_SECURITY_NODE = 4
ANCHOR_ECHO_PROTECTION = 5

RANK_NONE = 0
RANK_STABLE = 1
RANK_OPTIMIZED = 2
RANK_TRANSCENDENT = 3

RESULT_OK = 0
RESULT_DASHED = 1
RESULT_PHASED = 2
RESULT_PICKED_UP = 3
RESULT_DROPPED = 4
RESULT_SOCKETED = 5
RESULT_ACTIVATED = 6
RESULT_HACKED = 7
RESULT_REPAIRED = 8
RESULT_NO_TARGET = 9
RESULT_COOLDOWN = 10
RESULT_INVALID = 11
RESULT_POOL_FULL = 12
RESULT_ANCHOR_FULL = 13
RESULT_ANCHOR_FORBIDDEN = 14
RESULT_STAGE_COMPLETE = 15


@dataclass
class Actor:
    slot: int
    role: str
    x: int
    y: int
    active: bool = True
    health: int = 3
    vx: int = 0
    vy: int = 0
    facing: int = 1
    held: int = 0
    previous: int = 0
    flags: int = 0
    dash_timer: int = 0
    dash_cooldown: int = 0
    invuln_timer: int = 0
    carry_slot: int | None = None
    shield_timer: int = 0
    last_action: int = RESULT_OK


@dataclass
class Entity:
    logical_id: int
    kind: int
    x: int
    y: int
    active: bool = True
    state: int = 0
    timer: int = 0
    link: int = 0
    direction: int = 0
    health: int = 1
    radius: int = 12
    owner: int | None = None
    damage: int = 1
    vx: int = 0
    vy: int = 0
    duration: int = 1


@dataclass(frozen=True)
class Anchor:
    anchor_type: int
    logical_id: int
    state: int
    param: int = 0


@dataclass(frozen=True)
class BeamSegment:
    x: int
    y: int
    direction: int
    length: int
    source: int
    reflected: bool = False


@dataclass(frozen=True)
class StageResult:
    stage_id: int
    attempts: int
    echoes: int
    time_remaining: int
    optional_metric: int
    rank: int


@dataclass
class VocabularyWorld:
    stage_id: int = 0
    tick: int = 0
    attempt: int = 1
    result: int = RESULT_OK
    flags: int = 0x0001
    overflow_flags: int = 0
    update_units: int = 0
    update_peak: int = 0
    interaction_result: int = RESULT_OK
    anchor_capacity: int = 1
    anchors: list[Anchor] = field(default_factory=list)
    actors: list[Actor] = field(default_factory=list)
    enemies: list[Entity | None] = field(
        default_factory=lambda: [None] * ENEMY_CAPACITY
    )
    projectiles: list[Entity | None] = field(
        default_factory=lambda: [None] * PROJECTILE_CAPACITY
    )
    devices: list[Entity | None] = field(
        default_factory=lambda: [None] * DEVICE_CAPACITY
    )
    carryables: list[Entity | None] = field(
        default_factory=lambda: [None] * CARRYABLE_CAPACITY
    )
    hazards: list[Entity | None] = field(
        default_factory=lambda: [None] * HAZARD_CAPACITY
    )
    beams: list[BeamSegment] = field(default_factory=list)
    enemy_targets: list[int | None] = field(
        default_factory=lambda: [None] * ENEMY_CAPACITY
    )
    echo_recordings: list[tuple[int, ...]] = field(default_factory=list)
    echo_protected: list[bool] = field(default_factory=list)
    replacement_ordinal: int | None = None
    stage_result: StageResult | None = None
    action_log: list[str] = field(default_factory=list)
    _canonical: (
        tuple[
            list[Actor],
            list[Entity | None],
            list[Entity | None],
            list[Entity | None],
            list[Entity | None],
        ]
        | None
    ) = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.actors:
            self.actors = [
                Actor(slot=index, role="inactive", x=0, y=0, active=False)
                for index in range(ACTOR_CAPACITY)
            ]
        self._check_bounds()

    def _check_bounds(self) -> None:
        if len(self.actors) != ACTOR_CAPACITY:
            raise ValueError("actor array must contain exactly seven slots")
        expected = (
            (self.enemies, ENEMY_CAPACITY, "enemy"),
            (self.projectiles, PROJECTILE_CAPACITY, "projectile"),
            (self.devices, DEVICE_CAPACITY, "device"),
            (self.carryables, CARRYABLE_CAPACITY, "carryable"),
            (self.hazards, HAZARD_CAPACITY, "hazard"),
        )
        for pool, capacity, name in expected:
            if len(pool) != capacity:
                raise ValueError(f"{name} pool must contain exactly {capacity} slots")
        if len(self.anchors) > min(self.anchor_capacity, ANCHOR_LIMIT):
            raise ValueError("anchor capacity exceeded")
        if len(self.beams) > BEAM_LIMIT:
            raise ValueError("beam cache exceeded")

    def _unit(self, count: int = 1) -> None:
        self.update_units += count
        self.update_peak = max(self.update_peak, self.update_units)
        if self.update_units > UPDATE_UNIT_LIMIT:
            self.flags |= 0x0010
            self.overflow_flags |= 0x0010
            raise RuntimeError("M5 abstract update-unit limit exceeded")

    @staticmethod
    def _within(actor: Actor, entity: Entity, radius: int = 16) -> bool:
        return (
            abs(actor.x - entity.x) <= radius
            and abs(actor.y - entity.y) <= radius
        )

    @staticmethod
    def _lowest_free(pool: list[Entity | None]) -> int | None:
        return next(
            (
                index
                for index, entity in enumerate(pool)
                if entity is None or not entity.active
            ),
            None,
        )

    def add(self, pool_name: str, entity: Entity) -> int:
        pool = getattr(self, pool_name)
        index = self._lowest_free(pool)
        if index is None:
            self.result = RESULT_POOL_FULL
            return -1
        pool[index] = copy.deepcopy(entity)
        return index

    def set_actor(self, slot: int, actor: Actor) -> None:
        if not 0 <= slot < ACTOR_CAPACITY or actor.slot != slot:
            raise ValueError("actor slot mismatch")
        self.actors[slot] = copy.deepcopy(actor)

    def freeze_canonical(self) -> None:
        self._canonical = (
            copy.deepcopy(self.actors),
            copy.deepcopy(self.enemies),
            copy.deepcopy(self.devices),
            copy.deepcopy(self.carryables),
            copy.deepcopy(self.hazards),
        )

    def sanitize_input(self, held: int) -> int:
        held &= 0x7F
        if held & (INPUT_UP | INPUT_DOWN) == INPUT_UP | INPUT_DOWN:
            held &= ~(INPUT_UP | INPUT_DOWN)
        if held & (INPUT_LEFT | INPUT_RIGHT) == INPUT_LEFT | INPUT_RIGHT:
            held &= ~(INPUT_LEFT | INPUT_RIGHT)
        return held

    def apply_dash_phase(self, slot: int, held: int, pressed: int) -> int:
        actor = self.actors[slot]
        if not actor.active or not pressed & INPUT_C:
            return RESULT_OK
        if actor.dash_cooldown:
            return RESULT_COOLDOWN
        actor.flags |= ACTOR_DASHING | ACTOR_PHASED | ACTOR_INVULNERABLE
        actor.dash_timer = 6
        actor.dash_cooldown = 24
        actor.invuln_timer = 6
        actor.last_action = RESULT_DASHED
        x_direction = bool(held & INPUT_RIGHT) - bool(held & INPUT_LEFT)
        y_direction = bool(held & INPUT_DOWN) - bool(held & INPUT_UP)
        if not x_direction and not y_direction:
            x_direction = actor.facing
        speed = 4 if x_direction and y_direction else 6
        actor.vx = x_direction * speed
        actor.vy = y_direction * speed
        self.action_log.append(f"actor:{slot}:dash_phase")
        return RESULT_DASHED

    def _tick_actor_extensions(self) -> None:
        for actor in self.actors:
            self._unit()
            if actor.dash_cooldown:
                actor.dash_cooldown -= 1
            if actor.dash_timer:
                actor.dash_timer -= 1
                if not actor.dash_timer:
                    actor.flags &= ~(ACTOR_DASHING | ACTOR_PHASED)
            if actor.invuln_timer:
                actor.invuln_timer -= 1
                if not actor.invuln_timer:
                    actor.flags &= ~ACTOR_INVULNERABLE
            if actor.shield_timer:
                actor.shield_timer -= 1
                if not actor.shield_timer:
                    actor.flags &= ~ACTOR_SHIELDED

    def _carried_entity(self, actor: Actor) -> Entity | None:
        if actor.carry_slot is None:
            return None
        if not 0 <= actor.carry_slot < CARRYABLE_CAPACITY:
            raise ValueError("invalid carry slot")
        entity = self.carryables[actor.carry_slot]
        if entity is None or not entity.active:
            raise ValueError("actor references an inactive carryable")
        return entity

    @staticmethod
    def _terminal_duration(device: Entity) -> int:
        return device.duration if 1 <= device.duration <= 0xFFFF else 1

    @staticmethod
    def _reset_terminal_hold(device: Entity) -> None:
        if device.kind != DEVICE_HACK or device.state & STATE_HACKED:
            return
        device.timer = 0
        device.owner = None
        device.state &= ~STATE_ACTIVE

    def _reset_terminal_holds_for_actor(
        self, slot: int, keep: Entity | None = None
    ) -> None:
        for device in self.devices:
            self._unit()
            if (
                device is not None
                and device is not keep
                and device.kind == DEVICE_HACK
                and device.owner == slot
                and not device.state & STATE_HACKED
            ):
                self._reset_terminal_hold(device)

    def _advance_terminal_hold(self, actor: Actor, device: Entity) -> int:
        if (
            not actor.active
            or not device.active
            or device.kind != DEVICE_HACK
            or device.state & STATE_HACKED
        ):
            return RESULT_NO_TARGET
        self._reset_terminal_holds_for_actor(actor.slot, keep=device)
        if device.owner != actor.slot:
            self._reset_terminal_hold(device)
            device.owner = actor.slot
        duration = self._terminal_duration(device)
        device.timer = min(duration, device.timer + 1)
        device.state |= STATE_ACTIVE
        if device.timer < duration:
            return RESULT_ACTIVATED
        device.state |= STATE_HACKED
        return RESULT_HACKED

    def sustain_terminal_hold(self, slot: int) -> int:
        actor = self.actors[slot]
        if not actor.active:
            self._reset_terminal_holds_for_actor(slot)
            self.interaction_result = RESULT_INVALID
            return RESULT_INVALID
        for device in self.devices:
            self._unit()
            if (
                device is None
                or device.kind != DEVICE_HACK
                or device.owner != slot
                or device.timer == 0
                or device.state & STATE_HACKED
            ):
                continue
            if (
                not device.active
                or not actor.held & INPUT_B
                or not self._within(actor, device)
            ):
                self._reset_terminal_hold(device)
                self.interaction_result = RESULT_NO_TARGET
                return RESULT_NO_TARGET
            result = self._advance_terminal_hold(actor, device)
            actor.last_action = result
            self.interaction_result = result
            self.action_log.append(
                f"actor:{slot}:device:{device.logical_id}:{result}"
            )
            return result
        self.interaction_result = RESULT_NO_TARGET
        return RESULT_NO_TARGET

    def _validate_terminal_holds(self) -> None:
        for device in self.devices:
            self._unit()
            if (
                device is None
                or device.kind != DEVICE_HACK
                or device.timer == 0
                or device.state & STATE_HACKED
            ):
                continue
            if device.owner is None or not 0 <= device.owner < ACTOR_CAPACITY:
                self._reset_terminal_hold(device)
                continue
            actor = self.actors[device.owner]
            if (
                not actor.active
                or not actor.held & INPUT_B
                or not self._within(actor, device)
            ):
                self._reset_terminal_hold(device)

    def resolve_interaction(self, slot: int) -> int:
        actor = self.actors[slot]
        if not actor.active:
            return RESULT_INVALID
        carried = self._carried_entity(actor)
        if carried is not None:
            for device in self.devices:
                self._unit()
                if (
                    device is not None
                    and device.active
                    and device.kind == DEVICE_SOCKET
                    and not device.state & STATE_OCCUPIED
                    and self._within(actor, device)
                ):
                    carried.state = STATE_OCCUPIED
                    carried.owner = device.logical_id
                    carried.x, carried.y = device.x, device.y
                    device.state |= STATE_ACTIVE | STATE_OCCUPIED
                    actor.carry_slot = None
                    actor.flags &= ~ACTOR_CARRYING
                    actor.last_action = RESULT_SOCKETED
                    self.interaction_result = RESULT_SOCKETED
                    self.action_log.append(
                        f"actor:{slot}:socket:{carried.logical_id}:{device.logical_id}"
                    )
                    return RESULT_SOCKETED
            carried.state = 0
            carried.owner = None
            carried.x, carried.y = actor.x, actor.y
            actor.carry_slot = None
            actor.flags &= ~ACTOR_CARRYING
            actor.last_action = RESULT_DROPPED
            self.interaction_result = RESULT_DROPPED
            return RESULT_DROPPED

        for index, carryable in enumerate(self.carryables):
            self._unit()
            if (
                carryable is not None
                and carryable.active
                and carryable.kind == CARRYABLE_CELL
                and not carryable.state & STATE_OCCUPIED
                and carryable.owner is None
                and self._within(actor, carryable)
            ):
                carryable.state = STATE_ACTIVE
                carryable.owner = slot
                actor.carry_slot = index
                actor.flags |= ACTOR_CARRYING
                actor.last_action = RESULT_PICKED_UP
                self.interaction_result = RESULT_PICKED_UP
                self.action_log.append(
                    f"actor:{slot}:pickup:{carryable.logical_id}"
                )
                return RESULT_PICKED_UP

        priority = {
            DEVICE_HACK: 1,
            DEVICE_REPAIR: 1,
            DEVICE_SECURITY: 1,
            DEVICE_TIMED: 2,
            DEVICE_REFLECTOR: 3,
            DEVICE_CONVEYOR: 4,
        }
        candidates: list[tuple[int, int, Entity]] = []
        for index, device in enumerate(self.devices):
            self._unit()
            if (
                device is not None
                and device.active
                and device.kind in priority
                and self._within(actor, device)
            ):
                candidates.append((priority[device.kind], index, device))
        if not candidates:
            self.interaction_result = RESULT_NO_TARGET
            return RESULT_NO_TARGET
        _priority, _index, device = min(candidates)
        if device.kind == DEVICE_HACK:
            result = self._advance_terminal_hold(actor, device)
        elif device.kind == DEVICE_SECURITY:
            device.state |= STATE_ACTIVE | STATE_HACKED
            result = RESULT_HACKED
        elif device.kind == DEVICE_REPAIR:
            device.state = (device.state | STATE_ACTIVE) & ~STATE_DISABLED
            for target in (*self.devices, *self.enemies):
                self._unit()
                if (
                    target is not None
                    and target.active
                    and target.logical_id == device.link
                ):
                    target.state &= ~STATE_DISABLED
                    target.health = max(target.health, 1)
            result = RESULT_REPAIRED
        elif device.kind == DEVICE_TIMED:
            device.state |= STATE_ACTIVE
            device.timer = 120
            result = RESULT_ACTIVATED
        else:
            device.direction = (device.direction + 1) & 3
            device.state |= STATE_ACTIVE
            result = RESULT_ACTIVATED
        actor.last_action = result
        self.interaction_result = result
        self.action_log.append(f"actor:{slot}:device:{device.logical_id}:{result}")
        return result

    def _follow_carryables(self) -> None:
        for actor in self.actors:
            self._unit()
            if actor.active and actor.carry_slot is not None:
                carryable = self._carried_entity(actor)
                assert carryable is not None
                carryable.x, carryable.y = actor.x, actor.y
                carryable.owner = actor.slot

    def update_devices(self) -> None:
        self._follow_carryables()
        for device in self.devices:
            self._unit()
            if device is None or not device.active:
                continue
            if device.kind == DEVICE_PRESSURE:
                occupied = any(
                    actor.active and self._within(actor, device, 8)
                    for actor in self.actors
                )
                self._unit(ACTOR_CAPACITY)
                if occupied:
                    device.state |= STATE_ACTIVE
                else:
                    device.state &= ~STATE_ACTIVE
            elif device.kind == DEVICE_TIMED:
                if device.timer:
                    device.timer -= 1
                    device.state |= STATE_ACTIVE
                else:
                    device.state &= ~STATE_ACTIVE
            elif device.kind == DEVICE_SOCKET:
                occupied = any(
                    carryable is not None
                    and carryable.active
                    and carryable.state & STATE_OCCUPIED
                    and carryable.owner == device.logical_id
                    for carryable in self.carryables
                )
                self._unit(CARRYABLE_CAPACITY)
                if occupied:
                    device.state |= STATE_ACTIVE | STATE_OCCUPIED
                else:
                    device.state &= ~(STATE_ACTIVE | STATE_OCCUPIED)
            elif device.kind == DEVICE_DOOR:
                source_active = any(
                    source is not None
                    and source.active
                    and source.link == device.link
                    and source.kind
                    in (DEVICE_PRESSURE, DEVICE_TIMED, DEVICE_SOCKET)
                    and source.state & STATE_ACTIVE
                    for source in self.devices
                )
                self._unit(DEVICE_CAPACITY)
                if source_active:
                    device.state |= STATE_OPEN
                else:
                    device.state &= ~STATE_OPEN
            elif device.kind == DEVICE_CONVEYOR and device.state & STATE_ACTIVE:
                for actor in self.actors:
                    self._unit()
                    if actor.active and self._within(actor, device):
                        self._apply_direction(actor, device.direction, 1)

    @staticmethod
    def _apply_direction(actor: Actor, direction: int, speed: int) -> None:
        if direction == 0:
            actor.x += speed
        elif direction == 1:
            actor.y += speed
        elif direction == 2:
            actor.x -= speed
        else:
            actor.y -= speed

    def build_beams(self) -> None:
        self.beams = []
        emitter_count = 0
        for emitter in self.devices:
            self._unit()
            if (
                emitter is None
                or not emitter.active
                or emitter.kind != DEVICE_BEAM
                or not emitter.state & STATE_ACTIVE
            ):
                continue
            if emitter_count == 4:
                self.overflow_flags |= 0x0002
                break
            emitter_count += 1
            direction = emitter.direction & 3
            segment = BeamSegment(
                emitter.x, emitter.y, direction, 160, emitter.logical_id
            )
            reflectors: list[tuple[int, int, Entity]] = []
            for index, reflector in enumerate(self.devices):
                self._unit()
                if (
                    reflector is not None
                    and reflector.active
                    and reflector.kind == DEVICE_REFLECTOR
                ):
                    distance = self._ray_distance(
                        emitter.x,
                        emitter.y,
                        direction,
                        reflector.x,
                        reflector.y,
                    )
                    if distance is not None and distance <= segment.length:
                        reflectors.append((distance, index, reflector))
            if reflectors:
                distance, _index, reflector = min(reflectors)
                segment = BeamSegment(
                    segment.x,
                    segment.y,
                    segment.direction,
                    distance,
                    segment.source,
                    True,
                )
                if len(self.beams) + 2 > BEAM_LIMIT:
                    self.overflow_flags |= 0x0002
                    break
                self.beams.append(segment)
                redirected = (direction + reflector.direction + 1) & 3
                self.beams.append(
                    BeamSegment(
                        reflector.x,
                        reflector.y,
                        redirected,
                        160,
                        emitter.logical_id,
                        True,
                    )
                )
            else:
                if len(self.beams) == BEAM_LIMIT:
                    self.overflow_flags |= 0x0002
                    break
                self.beams.append(segment)

    @staticmethod
    def _ray_distance(
        x: int, y: int, direction: int, target_x: int, target_y: int
    ) -> int | None:
        if direction == 0 and target_y == y and target_x >= x:
            return target_x - x
        if direction == 1 and target_x == x and target_y >= y:
            return target_y - y
        if direction == 2 and target_y == y and target_x <= x:
            return x - target_x
        if direction == 3 and target_x == x and target_y <= y:
            return y - target_y
        return None

    def select_target(self, enemy: Entity) -> int | None:
        choices: list[tuple[int, int]] = []
        for actor in self.actors:
            self._unit()
            if actor.active:
                choices.append(
                    (abs(actor.x - enemy.x) + abs(actor.y - enemy.y), actor.slot)
                )
        return min(choices)[1] if choices else None

    def spawn_projectile(
        self,
        *,
        owner: int,
        x: int,
        y: int,
        target_x: int,
        target_y: int,
        hostile: bool,
    ) -> int:
        slot = self._lowest_free(self.projectiles)
        if slot is None:
            self.result = RESULT_POOL_FULL
            return -1
        dx = (target_x > x) - (target_x < x)
        dy = (target_y > y) - (target_y < y)
        speed = 2
        self.projectiles[slot] = Entity(
            logical_id=slot,
            kind=PROJECTILE_ENEMY if hostile else PROJECTILE_PLAYER,
            x=x,
            y=y,
            timer=60,
            owner=owner,
            damage=1,
            vx=dx * speed,
            vy=dy * speed,
        )
        return slot

    def update_enemies(self) -> None:
        self.enemy_targets = [None] * ENEMY_CAPACITY
        for index, enemy in enumerate(self.enemies):
            self._unit()
            if enemy is None or not enemy.active or enemy.state & STATE_DISABLED:
                continue
            target_slot = self.select_target(enemy)
            self.enemy_targets[index] = target_slot
            if target_slot is None:
                continue
            target = self.actors[target_slot]
            distance = abs(target.x - enemy.x) + abs(target.y - enemy.y)
            if enemy.kind == ENEMY_GUARD and distance > 96:
                enemy.state &= ~STATE_ACTIVE
                enemy.vx = enemy.vy = 0
                continue
            if enemy.kind in (ENEMY_PURSUER, ENEMY_GUARD):
                enemy.state |= STATE_ACTIVE
                enemy.vx = (target.x > enemy.x) - (target.x < enemy.x)
                enemy.vy = (target.y > enemy.y) - (target.y < enemy.y)
                enemy.x += enemy.vx
                enemy.y += enemy.vy
            if enemy.kind in (ENEMY_TURRET, ENEMY_GUARD):
                if enemy.timer:
                    enemy.timer -= 1
                else:
                    self.spawn_projectile(
                        owner=enemy.logical_id,
                        x=enemy.x,
                        y=enemy.y,
                        target_x=target.x,
                        target_y=target.y,
                        hostile=True,
                    )
                    enemy.timer = 45

    def damage_actor(self, slot: int, damage: int = 1) -> bool:
        actor = self.actors[slot]
        if (
            not actor.active
            or actor.flags
            & (ACTOR_INVULNERABLE | ACTOR_PHASED | ACTOR_SHIELDED)
        ):
            return False
        actor.health = max(0, actor.health - max(1, damage))
        actor.flags |= ACTOR_HURT | ACTOR_INVULNERABLE
        actor.invuln_timer = 30
        if actor.health == 0:
            actor.active = False
            self._reset_terminal_holds_for_actor(slot)
            if slot == 6:
                self.flags |= 0x0020
        return True

    def damage_enemy(self, index: int, damage: int = 1) -> bool:
        enemy = self.enemies[index]
        if enemy is None or not enemy.active:
            return False
        enemy.health = max(0, enemy.health - max(1, damage))
        if enemy.health == 0:
            enemy.active = False
        return True

    def update_projectiles(self) -> None:
        for index, projectile in enumerate(self.projectiles):
            self._unit()
            if projectile is None or not projectile.active:
                continue
            projectile.x += projectile.vx
            projectile.y += projectile.vy
            projectile.timer -= 1
            if projectile.timer <= 0:
                projectile.active = False
                continue
            if projectile.kind & (PROJECTILE_PLAYER | PROJECTILE_REFLECTED):
                for enemy_index, enemy in enumerate(self.enemies):
                    self._unit()
                    if (
                        enemy is not None
                        and enemy.active
                        and abs(enemy.x - projectile.x) <= 8
                        and abs(enemy.y - projectile.y) <= 8
                    ):
                        self.damage_enemy(enemy_index, projectile.damage)
                        projectile.active = False
                        break
            elif projectile.kind & PROJECTILE_ENEMY:
                for actor in self.actors:
                    self._unit()
                    if (
                        actor.active
                        and abs(actor.x - projectile.x) <= 8
                        and abs(actor.y - projectile.y) <= 8
                        and self.damage_actor(actor.slot, projectile.damage)
                    ):
                        projectile.active = False
                        break

    @staticmethod
    def _actor_on_beam(actor: Actor, beam: BeamSegment) -> bool:
        distance = VocabularyWorld._ray_distance(
            beam.x, beam.y, beam.direction, actor.x, actor.y
        )
        if distance is not None:
            return distance <= beam.length
        if beam.direction in (0, 2):
            return (
                abs(actor.y - beam.y) <= 4
                and (
                    beam.x <= actor.x <= beam.x + beam.length
                    if beam.direction == 0
                    else beam.x - beam.length <= actor.x <= beam.x
                )
            )
        return (
            abs(actor.x - beam.x) <= 4
            and (
                beam.y <= actor.y <= beam.y + beam.length
                if beam.direction == 1
                else beam.y - beam.length <= actor.y <= beam.y
            )
        )

    def update_hazards(self) -> None:
        for actor in self.actors:
            self._unit()
            if not actor.active:
                continue
            actor.flags &= ~ACTOR_SHIELDED
            actor.shield_timer = 0
            for device in self.devices:
                self._unit()
                if (
                    device is not None
                    and device.active
                    and device.kind == DEVICE_SHIELD
                    and device.state & STATE_ACTIVE
                    and self._within(actor, device)
                ):
                    actor.flags |= ACTOR_SHIELDED
                    actor.shield_timer = 2
                    break
            for hazard in self.hazards:
                self._unit()
                if (
                    hazard is None
                    or not hazard.active
                    or not self._within(actor, hazard, hazard.radius)
                ):
                    continue
                if hazard.kind == HAZARD_SHIELD:
                    actor.flags |= ACTOR_SHIELDED
                    actor.shield_timer = 2
                elif hazard.kind == HAZARD_CONVEYOR:
                    self._apply_direction(actor, hazard.direction, 1)
                elif hazard.kind == HAZARD_DAMAGE:
                    self.damage_actor(actor.slot, hazard.damage)
            for beam in self.beams:
                self._unit()
                if self._actor_on_beam(actor, beam):
                    self.damage_actor(actor.slot, 1)

    def _apply_input(self, actor: Actor, held: int) -> None:
        held = self.sanitize_input(held)
        pressed = held & ~actor.held
        actor.previous = actor.held
        actor.held = held
        self.apply_dash_phase(actor.slot, held, pressed)
        if not actor.flags & ACTOR_DASHING:
            dx = bool(held & INPUT_RIGHT) - bool(held & INPUT_LEFT)
            dy = bool(held & INPUT_DOWN) - bool(held & INPUT_UP)
            actor.vx, actor.vy = dx, dy
        if actor.vx:
            actor.facing = 1 if actor.vx > 0 else -1
        actor.x += actor.vx
        actor.y += actor.vy
        if pressed & INPUT_A:
            self.spawn_projectile(
                owner=actor.slot,
                x=actor.x,
                y=actor.y,
                target_x=actor.x + actor.facing * 8,
                target_y=actor.y,
                hostile=False,
            )
        if pressed & INPUT_B:
            self.resolve_interaction(actor.slot)
        elif held & INPUT_B:
            self.sustain_terminal_hold(actor.slot)
        else:
            self._reset_terminal_holds_for_actor(actor.slot)

    def step(self, inputs: dict[int, int] | None = None) -> int:
        self.update_units = 0
        inputs = inputs or {}
        for actor in self.actors:
            self._unit()
            if actor.active:
                self._apply_input(actor, inputs.get(actor.slot, 0))
        self._tick_actor_extensions()
        self.update_devices()
        self.build_beams()
        self.update_enemies()
        self.update_projectiles()
        self.update_hazards()
        self._validate_terminal_holds()
        self.tick = min(ATTEMPT_TICKS, self.tick + 1)
        self.update_peak = max(self.update_peak, self.update_units)
        self._check_bounds()
        return self.result

    def set_anchor(
        self, anchor_type: int, logical_id: int, state: int, param: int = 0
    ) -> int:
        if not ANCHOR_DEVICE_STATE <= anchor_type <= ANCHOR_ECHO_PROTECTION:
            return RESULT_ANCHOR_FORBIDDEN
        for index, anchor in enumerate(self.anchors):
            if (
                anchor.anchor_type == anchor_type
                and anchor.logical_id == logical_id
            ):
                self.anchors[index] = Anchor(anchor_type, logical_id, state, param)
                return RESULT_OK
        if len(self.anchors) >= min(self.anchor_capacity, ANCHOR_LIMIT):
            return RESULT_ANCHOR_FULL
        self.anchors.append(Anchor(anchor_type, logical_id, state, param))
        return RESULT_OK

    def apply_anchors(self) -> None:
        for anchor in self.anchors:
            if anchor.anchor_type == ANCHOR_ECHO_PROTECTION:
                if 0 <= anchor.logical_id < len(self.echo_protected):
                    self.echo_protected[anchor.logical_id] = bool(anchor.state)
                continue
            if anchor.anchor_type == ANCHOR_SOCKETED_CELL:
                cell = next(
                    (
                        item
                        for item in self.carryables
                        if item is not None
                        and item.active
                        and item.logical_id == anchor.logical_id
                    ),
                    None,
                )
                socket = next(
                    (
                        item
                        for item in self.devices
                        if item is not None
                        and item.active
                        and item.kind == DEVICE_SOCKET
                        and item.logical_id == anchor.state
                    ),
                    None,
                )
                if cell is not None and socket is not None:
                    cell.state = STATE_OCCUPIED
                    cell.owner = socket.logical_id
                    cell.x, cell.y = socket.x, socket.y
                    socket.state |= STATE_ACTIVE | STATE_OCCUPIED | STATE_ANCHORED
                continue
            target = next(
                (
                    item
                    for item in self.devices
                    if item is not None
                    and item.active
                    and item.logical_id == anchor.logical_id
                ),
                None,
            )
            if target is not None:
                if anchor.anchor_type == ANCHOR_CONVEYOR_DIRECTION:
                    target.direction = anchor.state & 3
                    target.state |= STATE_ACTIVE | STATE_ANCHORED
                elif anchor.anchor_type == ANCHOR_SECURITY_NODE:
                    target.state = (
                        anchor.state | STATE_HACKED | STATE_ANCHORED
                    )
                else:
                    target.state = anchor.state | STATE_ANCHORED

    def reset_attempt(self) -> None:
        if self._canonical is None:
            raise ValueError("canonical state has not been frozen")
        echoes = copy.deepcopy(self.echo_recordings)
        protection = copy.deepcopy(self.echo_protected)
        anchors = copy.deepcopy(self.anchors)
        stage_id = self.stage_id
        (
            self.actors,
            self.enemies,
            self.devices,
            self.carryables,
            self.hazards,
        ) = copy.deepcopy(self._canonical)
        self.projectiles = [None] * PROJECTILE_CAPACITY
        self.beams = []
        self.enemy_targets = [None] * ENEMY_CAPACITY
        self.echo_recordings = echoes
        self.echo_protected = protection
        self.anchors = anchors
        self.stage_id = stage_id
        self.tick = 0
        self.attempt = min(0xFFFF, self.attempt + 1)
        self.result = RESULT_OK
        self.interaction_result = RESULT_OK
        self.update_units = 0
        self.flags &= 0x0001
        self.apply_anchors()
        self._check_bounds()

    def request_replacement(self, ordinal: int) -> int:
        if (
            len(self.echo_recordings) < ECHO_LIMIT
            or not 0 <= ordinal < ECHO_LIMIT
        ):
            return RESULT_INVALID
        if ordinal < len(self.echo_protected) and self.echo_protected[ordinal]:
            return RESULT_INVALID
        self.replacement_ordinal = ordinal
        return RESULT_OK

    def commit_echo(self, recording: Iterable[int]) -> int:
        sanitized = tuple(self.sanitize_input(value) for value in recording)
        if len(sanitized) > ATTEMPT_TICKS:
            return RESULT_INVALID
        if len(self.echo_recordings) < ECHO_LIMIT:
            self.echo_recordings.append(sanitized)
            self.echo_protected.append(False)
        else:
            if self.replacement_ordinal is None:
                return RESULT_INVALID
            ordinal = self.replacement_ordinal
            if self.echo_protected[ordinal]:
                return RESULT_INVALID
            self.echo_recordings.pop(ordinal)
            self.echo_protected.pop(ordinal)
            self.echo_recordings.append(sanitized)
            self.echo_protected.append(False)
            self.replacement_ordinal = None
        return RESULT_OK

    def finalize_stage(self, optional_metric: int = 0) -> StageResult:
        echoes = len(self.echo_recordings)
        remaining = ATTEMPT_TICKS - min(self.tick, ATTEMPT_TICKS)
        rank = RANK_STABLE
        if self.attempt <= 3 and echoes <= 2:
            rank = RANK_OPTIMIZED
        if self.attempt <= 2 and echoes <= 1 and remaining >= 240:
            rank = RANK_TRANSCENDENT
        self.stage_result = StageResult(
            self.stage_id,
            self.attempt,
            echoes,
            remaining,
            optional_metric,
            rank,
        )
        self.result = RESULT_STAGE_COMPLETE
        self.flags |= 0x0008
        return self.stage_result

    def projection_tail(self) -> bytes:
        data = bytearray(PROJECTION_BYTES)
        struct.pack_into(
            ">IHHHHHHHHHHHHHH",
            data,
            0,
            0x52393556,
            1,
            0x0200,
            PROJECTION_BYTES,
            self.flags & 0xFFFF,
            self.result & 0xFFFF,
            self.overflow_flags & 0xFFFF,
            self.update_units & 0xFFFF,
            self.update_peak & 0xFFFF,
            self.interaction_result & 0xFFFF,
            self.stage_id & 0xFFFF,
            0x0001 if self.stage_result else 0,
            self.attempt & 0xFFFF,
            self.tick & 0xFFFF,
            self.stage_result.optional_metric if self.stage_result else 0,
        )
        data[0x20] = len(self.echo_recordings) & 0xFF
        data[0x21] = (
            self.stage_result.rank if self.stage_result is not None else RANK_NONE
        )
        data[0x22] = len(self.anchors) & 0xFF
        data[0x23] = self.anchor_capacity & 0xFF
        data[0x24] = len(self.beams) & 0xFF
        data[0x25] = sum(
            enemy is not None and enemy.active for enemy in self.enemies
        )
        data[0x26] = sum(
            projectile is not None and projectile.active
            for projectile in self.projectiles
        )
        for index, anchor in enumerate(self.anchors[:ANCHOR_LIMIT]):
            struct.pack_into(
                ">BBHI",
                data,
                0x28 + index * 8,
                anchor.anchor_type,
                anchor.logical_id,
                anchor.state & 0xFFFF,
                anchor.param & 0xFFFFFFFF,
            )
        for index, actor in enumerate(self.actors):
            struct.pack_into(
                ">HBBBBBB",
                data,
                0x40 + index * 8,
                actor.flags & 0xFFFF,
                actor.dash_timer,
                actor.dash_cooldown,
                actor.invuln_timer,
                0xFF if actor.carry_slot is None else actor.carry_slot,
                actor.shield_timer,
                actor.last_action,
            )
        for index, target in enumerate(self.enemy_targets):
            data[0x78 + index] = 0xFF if target is None else target
        for index, beam in enumerate(self.beams[:BEAM_LIMIT]):
            struct.pack_into(
                ">hhBBBB",
                data,
                0x88 + index * 8,
                beam.x,
                beam.y,
                beam.direction,
                beam.length,
                beam.source,
                1 if beam.reflected else 0,
            )
        if len(data) != PROJECTION_BYTES:
            raise AssertionError("projection tail size drift")
        return bytes(data)


@dataclass
class TimelineUiReference:
    """Bounded behavioral reference for the active timeline controls."""

    cursor: int = 0
    open: bool = False
    wait_release: bool = False
    start_previous: bool = False

    def step(
        self,
        world: VocabularyWorld,
        *,
        start_held: bool,
        held: int,
        pressed: int,
        recording: Iterable[int] = (),
    ) -> tuple[bool, int | None]:
        """Return ``(meta_frame_consumed, commit_result)`` for one frame."""
        held = world.sanitize_input(held)
        pressed = world.sanitize_input(pressed)
        if self.wait_release:
            if not held & INPUT_C:
                self.wait_release = False
                self.open = False
            return True, None

        start_edge = start_held and not self.start_previous
        self.start_previous = start_held
        if not self.open:
            if not start_held:
                return False, None
            if world.echo_recordings:
                self.cursor %= min(len(world.echo_recordings), ECHO_LIMIT)
            else:
                self.cursor = 0
            if not start_edge and not pressed & INPUT_C:
                return True, None
            self.open = True
        elif start_edge:
            self.open = False
            return True, None

        echo_count = min(len(world.echo_recordings), ECHO_LIMIT)
        direction = pressed & (INPUT_LEFT | INPUT_RIGHT)
        if direction and echo_count:
            if direction & INPUT_LEFT:
                self.cursor = (self.cursor - 1) % echo_count
            else:
                self.cursor = (self.cursor + 1) % echo_count
            world.action_log.append(f"timeline:cursor:{self.cursor}")

        if not pressed & INPUT_C:
            return True, None
        if echo_count == ECHO_LIMIT:
            selection = world.request_replacement(self.cursor)
            if selection != RESULT_OK:
                return True, selection
        result = world.commit_echo(recording)
        if result == RESULT_OK:
            self.open = False
            self.wait_release = True
            world.action_log.append("timeline:commit")
        return True, result
