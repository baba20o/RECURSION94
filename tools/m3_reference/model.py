"""Independent Python model of the Milestone 3 authoritative engine state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable

from .contracts import EngineContract, Encounter, ObjectInit


class ModelError(RuntimeError):
    """The reference model reached a state excluded by the assembly contract."""


def crc32_iso_hdlc(payload: bytes | bytearray | memoryview) -> int:
    """CRC-32/ISO-HDLC using the reflected $EDB88320 polynomial."""

    crc = 0xFFFFFFFF
    for value in payload:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _u8(value: int) -> bytes:
    return bytes((value & 0xFF,))


def _u16(value: int) -> bytes:
    return (value & 0xFFFF).to_bytes(2, "big")


def _u32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


@dataclass
class Actor:
    active: int = 0
    role: int = 0
    flags: int = 0
    x: int = 0
    y: int = 0
    vx: int = 0
    vy: int = 0
    half_width: int = 0
    half_height: int = 0
    input_held: int = 0
    previous_input: int = 0
    health: int = 0
    logical_id: int = 0
    reserved: int = 0


@dataclass
class ObjectSlot:
    active: int = 0
    logical_id: int = 0
    state: int = 0
    x: int = 0
    y: int = 0
    vx: int = 0
    vy: int = 0
    timer: int = 0
    param: int = 0


_POOL_CANARY_BEFORE: Final[object] = object()
_POOL_CANARY_AFTER: Final[object] = object()


class FixedPool:
    """A hard-capacity pool with explicit host-side boundary canaries."""

    def __init__(self, pool_type: int, capacity: int, overflow_mask: int):
        if capacity <= 0:
            raise ValueError("pool capacity must be positive")
        self.pool_type = pool_type
        self.capacity = capacity
        self.overflow_mask = overflow_mask
        self._storage: list[object] = [
            _POOL_CANARY_BEFORE,
            *(ObjectSlot() for _ in range(capacity)),
            _POOL_CANARY_AFTER,
        ]
        self.count = 0
        self.high_water = 0

    @property
    def slots(self) -> tuple[ObjectSlot, ...]:
        return tuple(self.slot(index) for index in range(self.capacity))

    def slot(self, index: int) -> ObjectSlot:
        if not 0 <= index < self.capacity:
            raise IndexError(index)
        value = self._storage[index + 1]
        if not isinstance(value, ObjectSlot):
            raise ModelError("pool slot storage was corrupted")
        return value

    def canaries_intact(self) -> bool:
        return (
            self._storage[0] is _POOL_CANARY_BEFORE
            and self._storage[-1] is _POOL_CANARY_AFTER
            and len(self._storage) == self.capacity + 2
        )

    def allocate_lowest(self) -> tuple[int, ObjectSlot] | None:
        for index in range(self.capacity):
            if not self.slot(index).active:
                slot = ObjectSlot(active=1)
                self._storage[index + 1] = slot
                self.count += 1
                self.high_water = max(self.high_water, self.count)
                if not self.canaries_intact():
                    raise ModelError("pool allocation damaged a canary")
                return index, slot
        if not self.canaries_intact():
            raise ModelError("pool overflow damaged a canary")
        return None

    def free(self, index: int) -> None:
        if self.slot(index).active:
            self._storage[index + 1] = ObjectSlot()
            if self.count:
                self.count -= 1
        if not self.canaries_intact():
            raise ModelError("pool free damaged a canary")


class EngineModel:
    """Model only the authoritative state implemented by the M3 assembly."""

    def __init__(self, contract: EngineContract):
        self.contract = contract
        self.c = contract.constants
        self.initialize()

    def initialize(self) -> int:
        self.state = self.c["ENGINE_STATE_UNLOADED"]
        self.flags = 0
        self.encounter_id = 0
        self.attempt = 0
        self.tick = 0
        self.terminal_reason = self.c["ENGINE_TERMINAL_NONE"]
        self.last_error = self.c["ENGINE_ERROR_NONE"]
        self.overflow_flags = 0
        self.projection_length = 0
        self.state_crc = 0
        self.checkpoint_tick = 0
        self.checkpoint_count = 0
        self.checkpoints: list[tuple[int, int]] = []
        self.input_held = 0
        self.input_pressed = 0
        self.input_released = 0
        self.input_previous = 0
        self.facing_x = 1
        self.active_actor_count = 0
        self.room_left = 0
        self.room_top = 0
        self.room_right = 0
        self.room_bottom = 0
        self.spawn_x = 0
        self.spawn_y = 0
        self.rects = ()
        self.object_init_count = 0
        self.encounter: Encounter | None = None
        self.actors = [Actor() for _ in range(self.c["ACTOR_CAPACITY"])]
        self.pools = self._new_pools()
        self.projection = b""
        return self.c["ENGINE_RESULT_OK"]

    def _new_pools(self) -> dict[int, FixedPool]:
        return {
            pool_type: FixedPool(
                pool_type,
                self.contract.pool_capacities[pool_type],
                self.contract.pool_overflow_masks[pool_type],
            )
            for pool_type in range(1, self.c["POOL_TYPE_COUNT"] + 1)
        }

    def load_encounter(self, encounter_id: int) -> int:
        encounter = self.contract.encounters.get(encounter_id & 0xFFFF)
        if encounter is None:
            self.last_error = self.c["ENGINE_ERROR_ENCOUNTER_NOT_FOUND"]
            return self.c["ENGINE_RESULT_NOT_FOUND"]
        self.encounter = encounter
        self.encounter_id = encounter.encounter_id
        self.attempt = 0
        self.room_left = encounter.room_left
        self.room_top = encounter.room_top
        self.room_right = encounter.room_right
        self.room_bottom = encounter.room_bottom
        self.spawn_x = encounter.spawn_x << 8
        self.spawn_y = encounter.spawn_y << 8
        self.rects = encounter.rects
        self.object_init_count = len(encounter.objects)
        return self._reconstruct_canonical()

    def _reconstruct_canonical(self, *, publish: bool = True) -> int:
        if self.encounter is None:
            raise ModelError("canonical reconstruction without an encounter")
        self.actors = [Actor() for _ in range(self.c["ACTOR_CAPACITY"])]
        self.pools = self._new_pools()
        self.flags = self.c["ENGINE_FLAG_LOADED"]
        self.state = self.c["ENGINE_STATE_ACTIVE"]
        self.tick = 0
        self.terminal_reason = self.c["ENGINE_TERMINAL_NONE"]
        self.last_error = self.c["ENGINE_ERROR_NONE"]
        self.overflow_flags = 0
        self.projection_length = 0
        self.state_crc = 0
        self.checkpoint_tick = 0
        self.checkpoint_count = 0
        self.checkpoints = []
        self.input_held = 0
        self.input_pressed = 0
        self.input_released = 0
        self.input_previous = 0
        self.facing_x = 1
        self.active_actor_count = 1
        self.projection = b""

        self.actors[0] = Actor(
            active=1,
            role=self.c["ACTOR_CURRENT_ROLE"],
            x=self.spawn_x,
            y=self.spawn_y,
            half_width=4,
            half_height=4,
            health=1,
        )
        for item in self.encounter.objects:
            if not self._instantiate_canonical_object(item):
                self.state = self.c["ENGINE_STATE_ERROR"]
                self.last_error = self.c["ENGINE_ERROR_POOL_OVERFLOW"]
                return self.c["ENGINE_RESULT_POOL_OVERFLOW"]
        if publish:
            self.build_projection()
        return self.c["ENGINE_RESULT_OK"]

    def reset(self) -> int:
        if not self.flags & self.c["ENGINE_FLAG_LOADED"]:
            self.last_error = self.c["ENGINE_ERROR_INVALID_STATE"]
            return self.c["ENGINE_RESULT_NOT_ACTIVE"]
        saved_attempt = min(self.attempt + 1, 0xFFFF)
        result = self._reconstruct_canonical(publish=False)
        if result:
            return result
        # Runtime reset trusts the validated canonical staging image, installs
        # the incremented attempt, and publishes exactly one reset checkpoint.
        self.attempt = saved_attempt
        self.build_projection()
        return self.c["ENGINE_RESULT_OK"]

    def _instantiate_canonical_object(self, item: ObjectInit) -> bool:
        allocated = self._allocate(item.pool_type)
        if allocated is None:
            return False
        _, slot = allocated
        slot.logical_id = item.logical_id
        slot.state = item.state
        slot.x = item.x << 8
        slot.y = item.y << 8
        slot.vx = 0
        slot.vy = 0
        slot.timer = item.param_0
        slot.param = item.param_1
        return True

    def _allocate(self, pool_type: int) -> tuple[int, ObjectSlot] | None:
        pool = self.pools[pool_type]
        allocated = pool.allocate_lowest()
        if allocated is None:
            self.overflow_flags |= pool.overflow_mask
            self.last_error = self.c["ENGINE_ERROR_POOL_OVERFLOW"]
        return allocated

    def sanitize_input(self, value: int) -> int:
        sanitized = value & self.c["INPUT_ALLOWED_MASK"]
        vertical = self.c["INPUT_UP"] | self.c["INPUT_DOWN"]
        horizontal = self.c["INPUT_LEFT"] | self.c["INPUT_RIGHT"]
        if sanitized & vertical == vertical:
            sanitized &= ~vertical
        if sanitized & horizontal == horizontal:
            sanitized &= ~horizontal
        return sanitized & 0xFF

    def step(self, raw_input: int) -> int:
        if self.state == self.c["ENGINE_STATE_TERMINAL"]:
            return self.terminal_reason
        if self.state != self.c["ENGINE_STATE_ACTIVE"]:
            self.last_error = self.c["ENGINE_ERROR_INVALID_STATE"]
            return self.c["ENGINE_RESULT_NOT_ACTIVE"]

        new_input = self.sanitize_input(raw_input)
        old_input = self.input_held
        self.input_previous = old_input
        self.input_held = new_input
        self.input_pressed = ((~old_input) & new_input) & 0xFF
        self.input_released = (old_input & (~new_input)) & 0xFF

        actor = self.actors[0]
        actor.previous_input = old_input
        actor.input_held = new_input
        actor.flags &= ~(
            self.c["ACTOR_FLAG_COLLISION_X"]
            | self.c["ACTOR_FLAG_COLLISION_Y"]
        )
        self.flags &= ~self.c["ENGINE_FLAG_COLLISION"]

        x_direction = 0
        if new_input & self.c["INPUT_LEFT"]:
            x_direction = -1
        if new_input & self.c["INPUT_RIGHT"]:
            x_direction = 1
        y_direction = 0
        if new_input & self.c["INPUT_UP"]:
            y_direction = -1
        if new_input & self.c["INPUT_DOWN"]:
            y_direction = 1

        speed = self.c["ENGINE_CARDINAL_SPEED"]
        if x_direction and y_direction:
            speed = self.c["ENGINE_DIAGONAL_SPEED"]
        actor.vx = _s32(speed * x_direction)
        actor.vy = _s32(speed * y_direction)
        if x_direction:
            self.facing_x = x_direction & 0xFF

        self._move_actor_axis_separated(actor)
        if self.input_pressed & self.c["INPUT_A"]:
            self._spawn_projectile(actor)
        self._update_all_pools()

        self.tick += 1
        if self.tick >= self.c["ENGINE_ATTEMPT_TICKS"]:
            self.tick = self.c["ENGINE_ATTEMPT_TICKS"]
            self.terminal_reason = self.c["ENGINE_TERMINAL_TIMEOUT"]
            self.state = self.c["ENGINE_STATE_TERMINAL"]
            self.build_projection()
            return self.terminal_reason
        if self.tick % self.c["ENGINE_CHECKPOINT_INTERVAL"] == 0:
            self.build_projection()
        return self.c["ENGINE_RESULT_OK"]

    def _move_actor_axis_separated(self, actor: Actor) -> None:
        proposed_x = _s32(actor.x + actor.vx)
        minimum_x = (self.room_left + actor.half_width) << 8
        maximum_x = (self.room_right - actor.half_width) << 8
        if proposed_x < minimum_x:
            proposed_x = minimum_x
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_X"]
        if proposed_x > maximum_x:
            proposed_x = maximum_x
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_X"]
        if actor.vx:
            actor_top = actor.y - (actor.half_height << 8)
            actor_bottom = actor.y + (actor.half_height << 8)
            for rect in self.rects:
                rect_top = rect.y << 8
                rect_bottom = (rect.y + rect.height) << 8
                if actor_bottom <= rect_top or actor_top >= rect_bottom:
                    continue
                actor_left = proposed_x - (actor.half_width << 8)
                actor_right = proposed_x + (actor.half_width << 8)
                rect_left = rect.x << 8
                rect_right = (rect.x + rect.width) << 8
                if actor_right <= rect_left or actor_left >= rect_right:
                    continue
                if actor.vx > 0:
                    proposed_x = (rect.x - actor.half_width) << 8
                else:
                    proposed_x = (
                        rect.x + rect.width + actor.half_width
                    ) << 8
                actor.flags |= self.c["ACTOR_FLAG_COLLISION_X"]
        actor.x = _s32(proposed_x)

        proposed_y = _s32(actor.y + actor.vy)
        minimum_y = (self.room_top + actor.half_height) << 8
        maximum_y = (self.room_bottom - actor.half_height) << 8
        if proposed_y < minimum_y:
            proposed_y = minimum_y
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_Y"]
        if proposed_y > maximum_y:
            proposed_y = maximum_y
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_Y"]
        if actor.vy:
            actor_left = actor.x - (actor.half_width << 8)
            actor_right = actor.x + (actor.half_width << 8)
            for rect in self.rects:
                rect_left = rect.x << 8
                rect_right = (rect.x + rect.width) << 8
                if actor_right <= rect_left or actor_left >= rect_right:
                    continue
                actor_top = proposed_y - (actor.half_height << 8)
                actor_bottom = proposed_y + (actor.half_height << 8)
                rect_top = rect.y << 8
                rect_bottom = (rect.y + rect.height) << 8
                if actor_bottom <= rect_top or actor_top >= rect_bottom:
                    continue
                if actor.vy > 0:
                    proposed_y = (rect.y - actor.half_height) << 8
                else:
                    proposed_y = (
                        rect.y + rect.height + actor.half_height
                    ) << 8
                actor.flags |= self.c["ACTOR_FLAG_COLLISION_Y"]
        actor.y = _s32(proposed_y)

        collision_mask = (
            self.c["ACTOR_FLAG_COLLISION_X"]
            | self.c["ACTOR_FLAG_COLLISION_Y"]
        )
        if actor.flags & collision_mask:
            self.flags |= self.c["ENGINE_FLAG_COLLISION"]

    def _spawn_projectile(self, actor: Actor) -> int | None:
        allocated = self._allocate(self.c["POOL_TYPE_PROJECTILE"])
        if allocated is None:
            return None
        index, slot = allocated
        slot.logical_id = index
        slot.state = 1
        slot.x = actor.x
        slot.y = actor.y
        projectile_speed = self.c["ENGINE_PROJECTILE_SPEED"]
        slot.vx = -projectile_speed if self.facing_x & 0x80 else projectile_speed
        slot.vy = 0
        slot.timer = self.c["ENGINE_PROJECTILE_TICKS"]
        slot.param = 0
        return index

    def _update_all_pools(self) -> None:
        for pool_type in range(1, self.c["POOL_TYPE_COUNT"] + 1):
            pool = self.pools[pool_type]
            for index in range(pool.capacity):
                slot = pool.slot(index)
                if not slot.active:
                    continue
                slot.x = _s32(slot.x + slot.vx)
                slot.y = _s32(slot.y + slot.vy)
                if slot.timer:
                    slot.timer = (slot.timer - 1) & 0xFFFF
                    if slot.timer == 0:
                        pool.free(index)

    def build_projection(self) -> tuple[bytes, int]:
        self.flags |= self.c["ENGINE_FLAG_PROJECTION_VALID"]
        output = bytearray()
        output += _u32(self.c["PROJECTION_MAGIC_VALUE"])
        output += _u16(self.c["PROJECTION_SCHEMA_VERSION"])
        output += b"\x00\x00"
        output += _u16(self.encounter_id)
        output += _u16(self.attempt)
        output += _u16(self.tick)
        output += _u16(self.state)
        output += _u16(self.terminal_reason)
        output += _u16(self.flags)
        output += _u16(self.overflow_flags)
        output += _u16(self.last_error)
        output += _u16(self.room_left)
        output += _u16(self.room_top)
        output += _u16(self.room_right)
        output += _u16(self.room_bottom)
        output += _u8(self.input_held)
        output += _u8(self.input_pressed)
        output += _u8(self.input_released)
        output += _u8(self.input_previous)
        output += _u8(self.facing_x)
        output += _u8(self.active_actor_count)
        output += _u16(len(self.rects))

        for actor in self.actors:
            output += _u8(actor.active)
            output += _u8(actor.role)
            output += _u16(actor.flags)
            output += _u32(actor.x)
            output += _u32(actor.y)
            output += _u32(actor.vx)
            output += _u32(actor.vy)
            output += _u16(actor.half_width)
            output += _u16(actor.half_height)
            output += _u8(actor.input_held)
            output += _u8(actor.previous_input)
            output += _u16(actor.health)
            output += _u16(actor.logical_id)

        for pool_type in range(1, self.c["POOL_TYPE_COUNT"] + 1):
            pool = self.pools[pool_type]
            output += _u8(pool_type)
            output += _u8(pool.capacity)
            output += _u8(pool.count)
            output += _u8(pool.high_water)
            for slot in pool.slots:
                output += _u8(slot.active)
                output += _u8(slot.logical_id)
                output += _u16(slot.state)
                output += _u32(slot.x)
                output += _u32(slot.y)
                output += _u32(slot.vx)
                output += _u32(slot.vy)
                output += _u16(slot.timer)
                output += _u16(slot.param)

        expected_size = self.c["ENGINE_PROJECTION_SCHEMA_BYTES"]
        if len(output) != expected_size:
            self.flags &= ~self.c["ENGINE_FLAG_PROJECTION_VALID"]
            self.last_error = self.c["ENGINE_ERROR_PROJECTION_SIZE"]
            self.state = self.c["ENGINE_STATE_ERROR"]
            self.projection_length = 0
            raise ModelError(
                f"projection is {len(output)} bytes, expected {expected_size}"
            )
        output[6:8] = _u16(expected_size)
        crc = crc32_iso_hdlc(output)
        self.projection = bytes(output)
        self.projection_length = expected_size
        self.state_crc = crc
        self.checkpoint_tick = self.tick
        if self.checkpoint_count < 0xFFFF:
            self.checkpoint_count += 1
        self.checkpoints.append((self.tick, crc))
        return self.projection, crc

    def ordinary_state_bytes(self) -> bytes:
        """Serialize the exact zero-based $FF1800-$FF5FFF ordinary RAM image."""

        base = self.c["ENGINE_ACTOR_BASE"]
        end = self.c["ENGINE_POOL_REGION_END"]
        image = bytearray(end - base)

        def put(address: int, payload: bytes) -> None:
            offset = address - base
            if offset < 0 or offset + len(payload) > len(image):
                raise ModelError(
                    f"ordinary RAM write ${address:X}+{len(payload)} out of range"
                )
            image[offset : offset + len(payload)] = payload

        for index, actor in enumerate(self.actors):
            address = base + index * self.c["ACTOR_BYTES"]
            raw = bytearray(self.c["ACTOR_BYTES"])

            def actor_put(offset_name: str, payload: bytes) -> None:
                offset = self.c[offset_name]
                raw[offset : offset + len(payload)] = payload

            actor_put("ACTOR_ACTIVE", _u8(actor.active))
            actor_put("ACTOR_ROLE", _u8(actor.role))
            actor_put("ACTOR_FLAGS", _u16(actor.flags))
            actor_put("ACTOR_X", _u32(actor.x))
            actor_put("ACTOR_Y", _u32(actor.y))
            actor_put("ACTOR_VX", _u32(actor.vx))
            actor_put("ACTOR_VY", _u32(actor.vy))
            actor_put("ACTOR_HALF_WIDTH", _u16(actor.half_width))
            actor_put("ACTOR_HALF_HEIGHT", _u16(actor.half_height))
            actor_put("ACTOR_INPUT", _u8(actor.input_held))
            actor_put("ACTOR_PREVIOUS_INPUT", _u8(actor.previous_input))
            actor_put("ACTOR_HEALTH", _u16(actor.health))
            actor_put("ACTOR_LOGICAL_ID", _u16(actor.logical_id))
            actor_put("ACTOR_RESERVED", _u16(actor.reserved))
            put(address, raw)

        for pool_type, pool in self.pools.items():
            pool_address = self.contract.pool_bases[pool_type]
            for index, slot in enumerate(pool.slots):
                raw = bytearray()
                raw += _u8(slot.active)
                raw += _u8(slot.logical_id)
                raw += _u16(slot.state)
                raw += _u32(slot.x)
                raw += _u32(slot.y)
                raw += _u32(slot.vx)
                raw += _u32(slot.vy)
                raw += _u16(slot.timer)
                raw += _u16(slot.param)
                if len(raw) != self.c["OBJECT_BYTES"]:
                    raise ModelError("object raw layout size mismatch")
                put(pool_address + index * self.c["OBJECT_BYTES"], raw)
        return bytes(image)


def canonical_script_input(tick: int, constants: dict[str, int] | None = None) -> int:
    """The deterministic 720-frame script used by M3 acceptance evidence."""

    if constants is None:
        up, down, left, right, action = 1, 2, 4, 8, 0x10
    else:
        up = constants["INPUT_UP"]
        down = constants["INPUT_DOWN"]
        left = constants["INPUT_LEFT"]
        right = constants["INPUT_RIGHT"]
        action = constants["INPUT_A"]
    phase = tick % 120
    if phase < 30:
        value = right
    elif phase < 60:
        value = down
    elif phase < 90:
        value = left
    else:
        value = up
    if tick % 67 == 0:
        value |= action
    return value


def run_script(
    model: EngineModel,
    inputs: Iterable[int],
    *,
    encounter_id: int = 0,
) -> tuple[tuple[int, int], ...]:
    if model.initialize() != model.c["ENGINE_RESULT_OK"]:
        raise ModelError("initialization failed")
    if model.load_encounter(encounter_id) != model.c["ENGINE_RESULT_OK"]:
        raise ModelError("encounter load failed")
    for value in inputs:
        model.step(value)
    return tuple(model.checkpoints)
