"""Independent deterministic model of the Milestone 4 echo vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .contracts import GameplayContract, ObjectInit, StaticRect


class ModelError(RuntimeError):
    """The model reached a state excluded by the bound runtime contract."""


class RecordingOverrun(ModelError):
    """A caller attempted to write outside a 720-byte replay bank."""


def crc32_iso_hdlc(payload: bytes | bytearray | memoryview) -> int:
    """CRC-32/ISO-HDLC with reflected polynomial ``0xEDB88320``."""

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
class ReplayDescriptor:
    length: int = 0
    crc: int = 0
    flags: int = 0
    generation: int = 0

    def to_bytes(self) -> bytes:
        return (
            _u16(self.length)
            + _u32(self.crc)
            + _u8(self.flags)
            + _u8(self.generation)
        )


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


class FixedPool:
    """Hard-capacity, lowest-free object pool used by the host model."""

    def __init__(self, pool_type: int, capacity: int):
        self.pool_type = pool_type
        self.capacity = capacity
        self.slots = [ObjectSlot() for _ in range(capacity)]
        self.count = 0
        self.high_water = 0

    def allocate_lowest(self) -> tuple[int, ObjectSlot] | None:
        for index, slot in enumerate(self.slots):
            if not slot.active:
                replacement = ObjectSlot(active=1)
                self.slots[index] = replacement
                self.count += 1
                self.high_water = max(self.high_water, self.count)
                return index, replacement
        return None

    def free(self, index: int) -> None:
        if not 0 <= index < self.capacity:
            raise IndexError(index)
        if self.slots[index].active:
            self.slots[index] = ObjectSlot()
            self.count = max(0, self.count - 1)


_REPLAY_CANARY_BEFORE = object()
_REPLAY_CANARY_AFTER = object()


class ReplayStorage:
    """Seven exact banks guarded by host-only boundary canaries."""

    def __init__(self, bank_count: int, bank_bytes: int):
        self.bank_count = bank_count
        self.bank_bytes = bank_bytes
        self._storage: list[object] = [
            _REPLAY_CANARY_BEFORE,
            *(bytearray(bank_bytes) for _ in range(bank_count)),
            _REPLAY_CANARY_AFTER,
        ]

    def bank(self, index: int) -> bytearray:
        if not 0 <= index < self.bank_count:
            raise IndexError(index)
        bank = self._storage[index + 1]
        if not isinstance(bank, bytearray) or len(bank) != self.bank_bytes:
            raise ModelError("replay bank storage was corrupted")
        return bank

    def clear(self, index: int) -> None:
        self._storage[index + 1] = bytearray(self.bank_bytes)

    def record(self, bank_index: int, tick: int, value: int) -> None:
        if not 0 <= tick < self.bank_bytes:
            raise RecordingOverrun(tick)
        self.bank(bank_index)[tick] = value & 0xFF

    def canaries_intact(self) -> bool:
        return (
            len(self._storage) == self.bank_count + 2
            and self._storage[0] is _REPLAY_CANARY_BEFORE
            and self._storage[-1] is _REPLAY_CANARY_AFTER
            and all(
                isinstance(self._storage[index + 1], bytearray)
                and len(self._storage[index + 1]) == self.bank_bytes
                for index in range(self.bank_count)
            )
        )

    def banks_bytes(self) -> bytes:
        return b"".join(bytes(self.bank(index)) for index in range(self.bank_count))


class GameplayModel:
    """Deterministic replay/echo/B01 model bound to the M4 source contract."""

    def __init__(self, contract: GameplayContract):
        self.contract = contract
        self.c = contract.constants
        self.initialize()

    def initialize(self) -> int:
        self.replays = ReplayStorage(
            self.c["REPLAY_BANK_COUNT"],
            self.c["REPLAY_BANK_BYTES"],
        )
        self.descriptors = [
            ReplayDescriptor() for _ in range(self.c["REPLAY_BANK_COUNT"])
        ]
        self.echo_order: list[int] = []
        self.current_bank = self.c["GAMEPLAY_CURRENT_ACTOR_SLOT"]
        self.selected_ordinal: int | None = None
        self.timeline_flags = self.c["TIMELINE_FLAG_INITIALIZED"]
        self.timeline_pending_reason = 0
        self.timeline_attempt_length = 0
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
        self.last_commit_reason = 0
        self.last_bank_crc = 0
        self.gameplay_flags = self.c["GAMEPLAY_FLAG_INITIALIZED"]
        self.gameplay_result = self.c["GAMEPLAY_RESULT_OK"]
        self.gameplay_replacement_flags = 0
        self.stable_tick = 0
        self.step_count = 0
        self.attempt = 0
        self._clear_current_bank(self.current_bank)
        self._reconstruct_ordinary()
        return self.c["GAMEPLAY_RESULT_OK"]

    def _new_pools(self) -> dict[int, FixedPool]:
        return {
            pool_type: FixedPool(pool_type, capacity)
            for pool_type, capacity in self.contract.pool_capacities.items()
        }

    def _instantiate_object(self, item: ObjectInit) -> None:
        allocated = self.pools[item.pool_type].allocate_lowest()
        if allocated is None:
            raise ModelError(f"canonical pool {item.pool_type} overflow")
        _, slot = allocated
        slot.logical_id = item.logical_id
        slot.state = item.state
        slot.x = item.x << 8
        slot.y = item.y << 8
        slot.timer = item.param_0
        slot.param = item.param_1

    def _new_actor(self, role: int, bank: int) -> Actor:
        spawn_x, spawn_y = self.contract.fixture.spawn
        return Actor(
            active=1,
            role=role,
            x=spawn_x << 8,
            y=spawn_y << 8,
            half_width=4,
            half_height=4,
            health=1,
            logical_id=bank,
        )

    def _reconstruct_ordinary(self) -> None:
        self.state = self.c["ENGINE_STATE_ACTIVE"]
        self.engine_flags = self.c["ENGINE_FLAG_LOADED"]
        self.tick = 0
        self.terminal_reason = self.c["ENGINE_TERMINAL_NONE"]
        self.last_error = self.c["ENGINE_ERROR_NONE"]
        self.overflow_flags = 0
        self.input_held = 0
        self.input_pressed = 0
        self.input_released = 0
        self.input_previous = 0
        self.facing_x = 1
        self.checkpoint_count = 0
        self.checkpoint_tick = 0
        self.checkpoints: list[tuple[int, int]] = []
        self.projection = b""
        self.state_crc = 0
        self.pools = self._new_pools()
        for item in self.contract.fixture.objects:
            self._instantiate_object(item)

        self.actors = [Actor() for _ in range(self.c["ACTOR_CAPACITY"])]
        for ordinal, bank in enumerate(self.echo_order):
            self.actors[ordinal] = self._new_actor(ordinal, bank)
        current_slot = self.c["GAMEPLAY_CURRENT_ACTOR_SLOT"]
        self.actors[current_slot] = self._new_actor(current_slot, self.current_bank)
        self.active_actor_count = len(self.echo_order) + 1

        self.current_input = 0
        self.current_pressed = 0
        self.current_released = 0
        self.current_previous = 0
        self.switch_mask = 0
        self.door_state = 0
        self.stable_tick = 0
        self.step_count = 0
        self.gameplay_result = self.c["GAMEPLAY_RESULT_OK"]
        self.gameplay_flags &= (
            self.c["GAMEPLAY_FLAG_INITIALIZED"]
            | self.c["GAMEPLAY_FLAG_REPLACEMENT_VISIBLE"]
        )
        self.timeline_flags &= ~self.c["TIMELINE_FLAG_PROGRAM_STABLE"]

        device = self.pools[self.c["POOL_TYPE_DEVICE"]].slots[0]
        device.active = 1
        device.state = 0
        device.vx = 0
        device.vy = 0
        device.timer = 0
        device.param = 0
        self.build_projection()

    def sanitize_input(self, value: int) -> int:
        value &= self.c["INPUT_ALLOWED_MASK"]
        vertical = self.c["INPUT_UP"] | self.c["INPUT_DOWN"]
        horizontal = self.c["INPUT_LEFT"] | self.c["INPUT_RIGHT"]
        if value & vertical == vertical:
            value &= ~vertical
        if value & horizontal == horizontal:
            value &= ~horizontal
        return value & 0x7F

    def _clear_current_bank(self, bank: int) -> None:
        if not 0 <= bank < self.c["REPLAY_BANK_COUNT"]:
            raise ModelError(f"invalid current bank {bank}")
        generation = (self.descriptors[bank].generation + 1) & 0xFF
        if generation == 0:
            generation = 1
        self.descriptors[bank] = ReplayDescriptor(
            flags=self.c["REPLAY_FLAG_CURRENT"],
            generation=generation,
        )
        self.replays.clear(bank)
        self.current_bank = bank

    def _finalize_current_bank(self) -> int:
        length = self.timeline_attempt_length
        if not 0 <= length <= self.c["REPLAY_BANK_BYTES"]:
            self.timeline_last_result = self.c[
                "GAMEPLAY_RESULT_RECORDING_OVERRUN"
            ]
            return self.timeline_last_result
        descriptor = self.descriptors[self.current_bank]
        descriptor.length = length
        descriptor.crc = crc32_iso_hdlc(
            self.replays.bank(self.current_bank)[:length]
        )
        descriptor.flags &= self.c["REPLAY_FLAG_PROTECTED"]
        descriptor.flags |= (
            self.c["REPLAY_FLAG_VALID"] | self.c["REPLAY_FLAG_ECHO"]
        )
        self.last_bank_crc = descriptor.crc
        return self.c["GAMEPLAY_RESULT_OK"]

    def _spawn_projectile(self, actor: Actor) -> None:
        projectile_type = self.c["POOL_TYPE_PROJECTILE"]
        pool = self.pools[projectile_type]
        allocated = pool.allocate_lowest()
        if allocated is None:
            self.overflow_flags |= self.c["OVERFLOW_PROJECTILE"]
            self.last_error = self.c["ENGINE_ERROR_POOL_OVERFLOW"]
            return
        index, slot = allocated
        slot.logical_id = index
        slot.state = 1
        slot.x = actor.x
        slot.y = actor.y
        speed = self.c["ENGINE_PROJECTILE_SPEED"]
        slot.vx = (
            -speed
            if actor.flags & self.c["GAMEPLAY_ACTOR_FLAG_FACING_LEFT"]
            else speed
        )
        slot.timer = self.c["ENGINE_PROJECTILE_TICKS"]

    def _move_actor(self, actor: Actor, rects: tuple[StaticRect, ...]) -> None:
        collision_mask = (
            self.c["ACTOR_FLAG_COLLISION_X"]
            | self.c["ACTOR_FLAG_COLLISION_Y"]
        )
        actor.flags &= ~collision_mask
        room_left, room_top, room_right, room_bottom = self.contract.fixture.room

        proposed_x = _s32(actor.x + actor.vx)
        minimum_x = (room_left + actor.half_width) << 8
        maximum_x = (room_right - actor.half_width) << 8
        if proposed_x < minimum_x:
            proposed_x = minimum_x
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_X"]
        if proposed_x > maximum_x:
            proposed_x = maximum_x
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_X"]
        if actor.vx:
            top = actor.y - (actor.half_height << 8)
            bottom = actor.y + (actor.half_height << 8)
            for rect in rects:
                rect_top = rect.y << 8
                rect_bottom = (rect.y + rect.height) << 8
                if bottom <= rect_top or top >= rect_bottom:
                    continue
                left = proposed_x - (actor.half_width << 8)
                right = proposed_x + (actor.half_width << 8)
                rect_left = rect.x << 8
                rect_right = (rect.x + rect.width) << 8
                if right <= rect_left or left >= rect_right:
                    continue
                proposed_x = (
                    (rect.x - actor.half_width) << 8
                    if actor.vx > 0
                    else (rect.x + rect.width + actor.half_width) << 8
                )
                actor.flags |= self.c["ACTOR_FLAG_COLLISION_X"]
        actor.x = _s32(proposed_x)

        proposed_y = _s32(actor.y + actor.vy)
        minimum_y = (room_top + actor.half_height) << 8
        maximum_y = (room_bottom - actor.half_height) << 8
        if proposed_y < minimum_y:
            proposed_y = minimum_y
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_Y"]
        if proposed_y > maximum_y:
            proposed_y = maximum_y
            actor.flags |= self.c["ACTOR_FLAG_COLLISION_Y"]
        if actor.vy:
            left = actor.x - (actor.half_width << 8)
            right = actor.x + (actor.half_width << 8)
            for rect in rects:
                rect_left = rect.x << 8
                rect_right = (rect.x + rect.width) << 8
                if right <= rect_left or left >= rect_right:
                    continue
                top = proposed_y - (actor.half_height << 8)
                bottom = proposed_y + (actor.half_height << 8)
                rect_top = rect.y << 8
                rect_bottom = (rect.y + rect.height) << 8
                if bottom <= rect_top or top >= rect_bottom:
                    continue
                proposed_y = (
                    (rect.y - actor.half_height) << 8
                    if actor.vy > 0
                    else (rect.y + rect.height + actor.half_height) << 8
                )
                actor.flags |= self.c["ACTOR_FLAG_COLLISION_Y"]
        actor.y = _s32(proposed_y)
        if actor.flags & collision_mask:
            self.engine_flags |= self.c["ENGINE_FLAG_COLLISION"]

    def _apply_actor_input(self, actor: Actor, raw_input: int) -> tuple[int, int, int]:
        held = self.sanitize_input(raw_input)
        previous = actor.input_held
        actor.previous_input = previous
        actor.input_held = held
        pressed = ((~previous) & held) & 0xFF
        released = (previous & (~held)) & 0xFF

        x_direction = 0
        if held & self.c["INPUT_LEFT"]:
            x_direction = -1
        if held & self.c["INPUT_RIGHT"]:
            x_direction = 1
        y_direction = 0
        if held & self.c["INPUT_UP"]:
            y_direction = -1
        if held & self.c["INPUT_DOWN"]:
            y_direction = 1
        speed = self.c["ENGINE_CARDINAL_SPEED"]
        if x_direction and y_direction:
            speed = self.c["ENGINE_DIAGONAL_SPEED"]
        actor.vx = _s32(speed * x_direction)
        actor.vy = _s32(speed * y_direction)
        if x_direction < 0:
            actor.flags |= self.c["GAMEPLAY_ACTOR_FLAG_FACING_LEFT"]
        elif x_direction > 0:
            actor.flags &= ~self.c["GAMEPLAY_ACTOR_FLAG_FACING_LEFT"]

        device = self.pools[self.c["POOL_TYPE_DEVICE"]].slots[0]
        door_open = bool(device.state & self.c["B01_DEVICE_STATE_DOOR_OPEN"])
        rects = self.contract.fixture.rects[1:] if door_open else self.contract.fixture.rects
        self._move_actor(actor, rects)
        if pressed & self.c["INPUT_A"]:
            self._spawn_projectile(actor)
        return held, pressed, released

    def _inside_switch(self, actor: Actor) -> bool:
        return (
            self.c["B01_SWITCH_MIN_X_16_8"]
            <= actor.x
            < self.c["B01_SWITCH_MAX_X_16_8"]
            and self.c["B01_SWITCH_MIN_Y_16_8"]
            <= actor.y
            < self.c["B01_SWITCH_MAX_Y_16_8"]
        )

    def _inside_terminal(self, actor: Actor) -> bool:
        return (
            self.c["B01_TERMINAL_MIN_X_16_8"]
            <= actor.x
            < self.c["B01_TERMINAL_MAX_X_16_8"]
            and self.c["B01_TERMINAL_MIN_Y_16_8"]
            <= actor.y
            < self.c["B01_TERMINAL_MAX_Y_16_8"]
        )

    def _resolve_b01(self) -> int:
        occupancy = 0
        for ordinal in range(len(self.echo_order)):
            actor = self.actors[ordinal]
            if actor.active and self._inside_switch(actor):
                occupancy |= 1 << ordinal
        current = self.actors[self.c["GAMEPLAY_CURRENT_ACTOR_SLOT"]]
        if current.active and self._inside_switch(current):
            occupancy |= 1 << self.c["GAMEPLAY_CURRENT_ACTOR_SLOT"]
        self.switch_mask = occupancy

        device = self.pools[self.c["POOL_TYPE_DEVICE"]].slots[0]
        state = device.state & self.c["B01_DEVICE_STATE_PROGRAM_STABLE"]
        self.gameplay_flags &= ~(
            self.c["GAMEPLAY_FLAG_DOOR_OPEN"]
            | self.c["GAMEPLAY_FLAG_SWITCH_HELD"]
        )
        if occupancy:
            state |= (
                self.c["B01_DEVICE_STATE_SWITCH_HELD"]
                | self.c["B01_DEVICE_STATE_DOOR_OPEN"]
            )
            self.gameplay_flags |= (
                self.c["GAMEPLAY_FLAG_DOOR_OPEN"]
                | self.c["GAMEPLAY_FLAG_SWITCH_HELD"]
            )
        device.state = state
        device.param = occupancy
        self.door_state = state

        echo_mask = occupancy & 0x3F
        if (
            echo_mask
            and self.current_pressed & self.c["INPUT_B"]
            and self._inside_terminal(current)
        ):
            device.state |= self.c["B01_DEVICE_STATE_PROGRAM_STABLE"]
            self.door_state = device.state
            self.gameplay_flags |= self.c["GAMEPLAY_FLAG_PROGRAM_STABLE"]
            self.timeline_flags |= self.c["TIMELINE_FLAG_PROGRAM_STABLE"]
            self.gameplay_result = self.c["GAMEPLAY_RESULT_PROGRAM_STABLE"]
            self.stable_tick = self.tick + 1
            return self.c["GAMEPLAY_RESULT_PROGRAM_STABLE"]
        return self.c["GAMEPLAY_RESULT_OK"]

    def _update_all_pools(self) -> None:
        for pool_type in range(1, self.c["POOL_TYPE_COUNT"] + 1):
            pool = self.pools[pool_type]
            for index in range(pool.capacity):
                slot = pool.slots[index]
                if not slot.active:
                    continue
                slot.x = _s32(slot.x + slot.vx)
                slot.y = _s32(slot.y + slot.vy)
                if slot.timer:
                    slot.timer = (slot.timer - 1) & 0xFFFF
                    if slot.timer == 0:
                        pool.free(index)

    def step(self, raw_input: int) -> int:
        if self.state == self.c["ENGINE_STATE_TERMINAL"]:
            if self.terminal_reason == self.c["GAMEPLAY_TERMINAL_PROGRAM_STABLE"]:
                return self.c["GAMEPLAY_RESULT_PROGRAM_STABLE"]
            return self.c["GAMEPLAY_RESULT_TIMEOUT"]
        if self.state != self.c["ENGINE_STATE_ACTIVE"]:
            self.gameplay_result = self.c["GAMEPLAY_RESULT_INVALID_STATE"]
            return self.gameplay_result
        if self.tick >= self.c["REPLAY_BANK_BYTES"]:
            self.gameplay_result = self.c["GAMEPLAY_RESULT_RECORDING_OVERRUN"]
            self.timeline_last_result = self.gameplay_result
            return self.gameplay_result

        current_input = self.sanitize_input(raw_input)
        self.replays.record(self.current_bank, self.tick, current_input)
        self.timeline_attempt_length = self.tick + 1
        self.descriptors[self.current_bank].length = self.timeline_attempt_length
        if self.timeline_attempt_length == self.c["REPLAY_BANK_BYTES"]:
            self.timeline_flags |= self.c["TIMELINE_FLAG_RECORDING_FULL"]
        self.engine_flags &= ~self.c["ENGINE_FLAG_COLLISION"]

        for ordinal, bank in enumerate(self.echo_order):
            descriptor = self.descriptors[bank]
            required = self.c["REPLAY_FLAG_VALID"] | self.c["REPLAY_FLAG_ECHO"]
            if descriptor.flags & required != required:
                raise ModelError(f"echo bank {bank} is not valid")
            playback = (
                self.replays.bank(bank)[self.tick]
                if self.tick < descriptor.length
                else 0
            )
            self._apply_actor_input(self.actors[ordinal], playback)

        current = self.actors[self.c["GAMEPLAY_CURRENT_ACTOR_SLOT"]]
        held, pressed, released = self._apply_actor_input(current, current_input)
        self.current_input = held
        self.current_pressed = pressed
        self.current_released = released
        self.current_previous = current.previous_input
        self.input_held = held
        self.input_pressed = pressed
        self.input_released = released
        self.input_previous = current.previous_input
        self.facing_x = (
            0xFF
            if current.flags & self.c["GAMEPLAY_ACTOR_FLAG_FACING_LEFT"]
            else 1
        )

        interaction_result = self._resolve_b01()
        self._update_all_pools()
        self.tick += 1
        self.step_count = self.tick

        if interaction_result == self.c["GAMEPLAY_RESULT_PROGRAM_STABLE"]:
            self.state = self.c["ENGINE_STATE_TERMINAL"]
            self.terminal_reason = self.c["GAMEPLAY_TERMINAL_PROGRAM_STABLE"]
            self.gameplay_result = interaction_result
            self.timeline_last_result = interaction_result
            self.build_projection()
            return interaction_result
        if self.tick >= self.c["ENGINE_ATTEMPT_TICKS"]:
            self.tick = self.c["ENGINE_ATTEMPT_TICKS"]
            self.state = self.c["ENGINE_STATE_TERMINAL"]
            self.terminal_reason = self.c["ENGINE_TERMINAL_TIMEOUT"]
            self.gameplay_result = self.c["GAMEPLAY_RESULT_TIMEOUT"]
            self.timeline_last_result = self.gameplay_result
            self.build_projection()
            return self.gameplay_result
        if self.tick % self.c["ENGINE_CHECKPOINT_INTERVAL"] == 0:
            self.build_projection()
        self.gameplay_result = self.c["GAMEPLAY_RESULT_OK"]
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
        return self.gameplay_result

    def _mirror_replacement_flags(self) -> None:
        self.gameplay_replacement_flags = self.timeline_flags & 0xFF
        self.gameplay_flags &= ~self.c["GAMEPLAY_FLAG_REPLACEMENT_VISIBLE"]
        if self.timeline_flags & (
            self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
            | self.c["TIMELINE_FLAG_COMMIT_BLOCKED"]
        ):
            self.gameplay_flags |= self.c["GAMEPLAY_FLAG_REPLACEMENT_VISIBLE"]

    def clear_replacement_selection(self) -> int:
        self.selected_ordinal = None
        self.timeline_flags &= 0xC1
        if len(self.echo_order) == self.c["GAMEPLAY_ECHO_LIMIT"]:
            self.timeline_flags |= self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
        self._mirror_replacement_flags()
        self.gameplay_result = self.c["GAMEPLAY_RESULT_OK"]
        return self.gameplay_result

    def select_replacement(self, ordinal: int) -> int:
        if (
            len(self.echo_order) != self.c["GAMEPLAY_ECHO_LIMIT"]
            or not 0 <= ordinal < len(self.echo_order)
        ):
            self.timeline_last_result = self.c[
                "GAMEPLAY_RESULT_INVALID_SELECTION"
            ]
            self.gameplay_result = self.timeline_last_result
            return self.gameplay_result
        bank = self.echo_order[ordinal]
        if self.descriptors[bank].flags & self.c["REPLAY_FLAG_PROTECTED"]:
            self.selected_ordinal = None
            self.timeline_flags &= 0xC3
            self.timeline_flags |= (
                self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
                | self.c["TIMELINE_FLAG_COMMIT_BLOCKED"]
            )
            self.timeline_last_result = self.c["GAMEPLAY_RESULT_PROTECTED"]
            self._mirror_replacement_flags()
            self.gameplay_result = self.timeline_last_result
            return self.gameplay_result
        self.selected_ordinal = ordinal
        self.timeline_flags &= 0xC3
        self.timeline_flags |= (
            self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
            | self.c["TIMELINE_FLAG_SELECTION_VALID"]
            | self.c["TIMELINE_FLAG_SELECTION_EXPLICIT"]
        )
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
        self._mirror_replacement_flags()
        self.gameplay_result = self.timeline_last_result
        return self.gameplay_result

    def select_oldest_replacement(self) -> int:
        if len(self.echo_order) != self.c["GAMEPLAY_ECHO_LIMIT"]:
            self.timeline_last_result = self.c[
                "GAMEPLAY_RESULT_INVALID_SELECTION"
            ]
            self.gameplay_result = self.timeline_last_result
            return self.gameplay_result
        for ordinal, bank in enumerate(self.echo_order):
            if not self.descriptors[bank].flags & self.c["REPLAY_FLAG_PROTECTED"]:
                self.selected_ordinal = ordinal
                self.timeline_flags &= 0xC3
                self.timeline_flags |= (
                    self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
                    | self.c["TIMELINE_FLAG_SELECTION_VALID"]
                    | self.c["TIMELINE_FLAG_SELECTION_OLDEST"]
                )
                self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
                self._mirror_replacement_flags()
                self.gameplay_result = self.timeline_last_result
                return self.gameplay_result
        self.selected_ordinal = None
        self.timeline_flags &= 0xC3
        self.timeline_flags |= (
            self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
            | self.c["TIMELINE_FLAG_COMMIT_BLOCKED"]
        )
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_PROTECTED"]
        self._mirror_replacement_flags()
        self.gameplay_result = self.timeline_last_result
        return self.gameplay_result

    def set_echo_protected(self, ordinal: int, protected: bool) -> int:
        if not 0 <= ordinal < len(self.echo_order):
            self.timeline_last_result = self.c[
                "GAMEPLAY_RESULT_INVALID_SELECTION"
            ]
            self.gameplay_result = self.timeline_last_result
            return self.gameplay_result
        descriptor = self.descriptors[self.echo_order[ordinal]]
        if protected:
            descriptor.flags |= self.c["REPLAY_FLAG_PROTECTED"]
            if self.selected_ordinal == ordinal:
                self.clear_replacement_selection()
        else:
            descriptor.flags &= ~self.c["REPLAY_FLAG_PROTECTED"]
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
        self.gameplay_result = self.timeline_last_result
        return self.gameplay_result

    def commit_and_reset(self, reason: int) -> int:
        count = len(self.echo_order)
        if count > self.c["GAMEPLAY_ECHO_LIMIT"]:
            self.timeline_last_result = self.c["GAMEPLAY_RESULT_INVALID_STATE"]
            self.gameplay_result = self.timeline_last_result
            return self.gameplay_result
        selected_bank: int | None = None
        if count == self.c["GAMEPLAY_ECHO_LIMIT"]:
            if self.selected_ordinal is None:
                self.timeline_pending_reason = reason & 0xFFFF
                self.timeline_flags &= 0xC3
                self.timeline_flags |= (
                    self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
                    | self.c["TIMELINE_FLAG_COMMIT_BLOCKED"]
                )
                self.timeline_last_result = self.c[
                    "GAMEPLAY_RESULT_REPLACEMENT_REQUIRED"
                ]
                self._mirror_replacement_flags()
                self.gameplay_result = self.timeline_last_result
                return self.gameplay_result
            selected_bank = self.echo_order[self.selected_ordinal]
            if (
                self.descriptors[selected_bank].flags
                & self.c["REPLAY_FLAG_PROTECTED"]
            ):
                self.timeline_pending_reason = reason & 0xFFFF
                self.selected_ordinal = None
                self.timeline_flags &= 0xC3
                self.timeline_flags |= (
                    self.c["TIMELINE_FLAG_REPLACEMENT_REQUIRED"]
                    | self.c["TIMELINE_FLAG_COMMIT_BLOCKED"]
                )
                self.timeline_last_result = self.c["GAMEPLAY_RESULT_PROTECTED"]
                self._mirror_replacement_flags()
                self.gameplay_result = self.timeline_last_result
                return self.gameplay_result

        result = self._finalize_current_bank()
        if result:
            self.gameplay_result = result
            return result
        completed_bank = self.current_bank
        if count < self.c["GAMEPLAY_ECHO_LIMIT"]:
            self.echo_order.append(completed_bank)
            free = [
                bank
                for bank in range(self.c["REPLAY_BANK_COUNT"])
                if bank not in self.echo_order
            ]
            if not free:
                raise ModelError("no free replay bank after append")
            next_current = free[0]
        else:
            if selected_bank is None or self.selected_ordinal is None:
                raise ModelError("replacement lost selected bank")
            del self.echo_order[self.selected_ordinal]
            self.echo_order.append(completed_bank)
            next_current = selected_bank

        self._clear_current_bank(next_current)
        self.selected_ordinal = None
        self.timeline_flags = self.c["TIMELINE_FLAG_INITIALIZED"]
        if len(self.echo_order) == self.c["GAMEPLAY_ECHO_LIMIT"]:
            self.timeline_flags |= self.c[
                "TIMELINE_FLAG_REPLACEMENT_REQUIRED"
            ]
        self.timeline_pending_reason = 0
        self.timeline_attempt_length = 0
        self.timeline_last_result = self.c["GAMEPLAY_RESULT_OK"]
        self.last_commit_reason = reason & 0xFFFF
        self._mirror_replacement_flags()

        self.attempt = min(self.attempt + 1, 0xFFFF)
        self._reconstruct_ordinary()
        self.gameplay_result = self.c["GAMEPLAY_RESULT_OK"]
        return self.gameplay_result

    def build_projection(self) -> tuple[bytes, int]:
        self.engine_flags |= self.c["ENGINE_FLAG_PROJECTION_VALID"]
        room_left, room_top, room_right, room_bottom = self.contract.fixture.room
        output = bytearray()
        output += _u32(self.c["PROJECTION_MAGIC_VALUE"])
        output += _u16(self.c["PROJECTION_SCHEMA_VERSION"])
        output += b"\x00\x00"
        output += _u16(0)
        output += _u16(self.attempt)
        output += _u16(self.tick)
        output += _u16(self.state)
        output += _u16(self.terminal_reason)
        output += _u16(self.engine_flags)
        output += _u16(self.overflow_flags)
        output += _u16(self.last_error)
        output += _u16(room_left)
        output += _u16(room_top)
        output += _u16(room_right)
        output += _u16(room_bottom)
        output += _u8(self.input_held)
        output += _u8(self.input_pressed)
        output += _u8(self.input_released)
        output += _u8(self.input_previous)
        output += _u8(self.facing_x)
        output += _u8(self.active_actor_count)
        output += _u16(len(self.contract.fixture.rects))

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

        expected = self.c["ENGINE_PROJECTION_SCHEMA_BYTES"]
        if len(output) != expected:
            raise ModelError(f"projection is {len(output)} bytes, expected {expected}")
        output[6:8] = _u16(expected)
        self.projection = bytes(output)
        self.state_crc = crc32_iso_hdlc(self.projection)
        self.checkpoint_tick = self.tick
        self.checkpoint_count = min(self.checkpoint_count + 1, 0xFFFF)
        self.checkpoints.append((self.tick, self.state_crc))
        return self.projection, self.state_crc

    def replay_region_bytes(self) -> bytes:
        output = bytearray(self.replays.banks_bytes())
        for descriptor in self.descriptors:
            output += descriptor.to_bytes()
        output += _u32(self.c["TIMELINE_MAGIC_VALUE"])
        output += _u16(self.c["TIMELINE_VERSION_VALUE"])
        output += _u16(self.c["TIMELINE_CONTROL_BYTES"])
        output += _u8(len(self.echo_order))
        output += _u8(self.current_bank)
        padded_order = self.echo_order + [
            0xFF
        ] * (self.c["GAMEPLAY_ECHO_LIMIT"] - len(self.echo_order))
        output += bytes(padded_order)
        output += _u8(
            self.c["TIMELINE_NO_SELECTION"]
            if self.selected_ordinal is None
            else self.selected_ordinal
        )
        output += _u8(self.timeline_flags)
        output += _u16(self.timeline_pending_reason)
        output += _u16(self.timeline_attempt_length)
        output += _u16(self.timeline_last_result)
        expected = self.c["TIMELINE_CONTROL_END"] - self.c["REPLAY_BANK_BASE"]
        if len(output) != expected:
            raise ModelError(f"replay region is {len(output)} bytes, expected {expected}")
        return bytes(output)

    def ordinary_state_bytes(self) -> bytes:
        base = self.c["ENGINE_ACTOR_BASE"]
        end = self.c["ENGINE_POOL_REGION_END"]
        output = bytearray(end - base)

        def put(address: int, payload: bytes) -> None:
            offset = address - base
            if offset < 0 or offset + len(payload) > len(output):
                raise ModelError("ordinary state serialization overflow")
            output[offset : offset + len(payload)] = payload

        for index, actor in enumerate(self.actors):
            raw = bytearray(self.c["ACTOR_BYTES"])
            raw[0:2] = _u8(actor.active) + _u8(actor.role)
            raw[2:4] = _u16(actor.flags)
            raw[4:8] = _u32(actor.x)
            raw[8:12] = _u32(actor.y)
            raw[12:16] = _u32(actor.vx)
            raw[16:20] = _u32(actor.vy)
            raw[20:22] = _u16(actor.half_width)
            raw[22:24] = _u16(actor.half_height)
            raw[24:26] = _u8(actor.input_held) + _u8(actor.previous_input)
            raw[26:28] = _u16(actor.health)
            raw[28:30] = _u16(actor.logical_id)
            raw[30:32] = _u16(actor.reserved)
            put(base + index * self.c["ACTOR_BYTES"], raw)

        for pool_type, pool in self.pools.items():
            pool_base = self.contract.pool_bases[pool_type]
            for index, slot in enumerate(pool.slots):
                raw = (
                    _u8(slot.active)
                    + _u8(slot.logical_id)
                    + _u16(slot.state)
                    + _u32(slot.x)
                    + _u32(slot.y)
                    + _u32(slot.vx)
                    + _u32(slot.vy)
                    + _u16(slot.timer)
                    + _u16(slot.param)
                )
                put(pool_base + index * self.c["OBJECT_BYTES"], raw)
        return bytes(output)


def canonical_b01_first_attempt(constants: Mapping[str, int]) -> tuple[int, ...]:
    """32 right frames then eight neutral frames: the echo holds X=80."""

    return (constants["INPUT_RIGHT"],) * 32 + (0,) * 8


def run_b01_solution(
    model: GameplayModel,
    *,
    first_attempt: Iterable[int] | None = None,
) -> int:
    model.initialize()
    inputs = (
        tuple(first_attempt)
        if first_attempt is not None
        else canonical_b01_first_attempt(model.c)
    )
    for value in inputs:
        result = model.step(value)
        if result != model.c["GAMEPLAY_RESULT_OK"]:
            raise ModelError(f"first attempt terminated with {result}")
    result = model.commit_and_reset(model.c["GAMEPLAY_COMMIT_MANUAL"])
    if result != model.c["GAMEPLAY_RESULT_OK"]:
        raise ModelError(f"first attempt commit failed with {result}")
    for _ in range(165):
        result = model.step(model.c["INPUT_RIGHT"])
        if result != model.c["GAMEPLAY_RESULT_OK"]:
            raise ModelError(f"second attempt terminated early with {result}")
    return model.step(model.c["INPUT_RIGHT"] | model.c["INPUT_B"])
