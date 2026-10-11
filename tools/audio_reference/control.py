"""Reference desired-state, acknowledgement, quantization, and mute models."""

from __future__ import annotations

from dataclasses import dataclass


SYNC_STATE_OPCODE = 0x09
MAX_SCENE_ID = 63
MAX_LAYER_MASK = 0x7F
MUTED_BIT = 0x80
DEFAULT_LAYER_QUANTUM_TICKS = 6


def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be within {minimum}..{maximum}")
    return value


def next_boundary(tick: int, quantum: int) -> int:
    """Return the first strictly later positive musical boundary."""

    _bounded_int("tick", tick, 0, 0x7FFFFFFF)
    _bounded_int("quantum", quantum, 1, 0x7FFFFFFF)
    return ((tick // quantum) + 1) * quantum


@dataclass(frozen=True)
class SyncState:
    """Decoded four-byte desired audio snapshot."""

    scene_id: int
    layer_mask: int
    muted: bool
    generation: int

    def __post_init__(self) -> None:
        _bounded_int("scene_id", self.scene_id, 0, MAX_SCENE_ID)
        _bounded_int("layer_mask", self.layer_mask, 0, MAX_LAYER_MASK)
        if not isinstance(self.muted, bool):
            raise ValueError("muted must be Boolean")
        _bounded_int("generation", self.generation, 0, 0xFF)

    @property
    def state_tuple(self) -> tuple[int, int, bool]:
        return self.scene_id, self.layer_mask, self.muted

    def wire_bytes(self, opcode: int = SYNC_STATE_OPCODE) -> bytes:
        """Encode exactly one ordinary-ring command."""

        _bounded_int("opcode", opcode, 0, 0xFF)
        mask_and_mute = self.layer_mask | (MUTED_BIT if self.muted else 0)
        return bytes((opcode, self.scene_id, mask_and_mute, self.generation))

    @classmethod
    def from_wire(cls, command: bytes, opcode: int = SYNC_STATE_OPCODE) -> SyncState:
        """Decode one exact four-byte command and reject reserved mask bits."""

        if len(command) != 4:
            raise ValueError("SYNC_STATE command must contain exactly four bytes")
        if command[0] != opcode:
            raise ValueError(f"unexpected opcode {command[0]:#04x}")
        mask_and_mute = command[2]
        return cls(
            scene_id=command[1],
            layer_mask=mask_and_mute & MAX_LAYER_MASK,
            muted=bool(mask_and_mute & MUTED_BIT),
            generation=command[3],
        )


class DesiredStateController:
    """68000-side coalescer with at most one wire generation in flight."""

    def __init__(
        self,
        *,
        initial_scene_id: int = 0,
        initial_layer_mask: int = 0,
        initial_muted: bool = False,
        first_generation: int = 1,
    ) -> None:
        initial = SyncState(
            initial_scene_id,
            initial_layer_mask,
            initial_muted,
            generation=0,
        )
        self._acked = initial.state_tuple
        self._desired = initial.state_tuple
        self._in_flight: SyncState | None = None
        self._next_generation = _bounded_int(
            "first_generation", first_generation, 0, 0xFF
        )
        self.requests_coalesced = 0
        self.retries = 0
        self.acks_ignored = 0

    @property
    def acknowledged(self) -> tuple[int, int, bool]:
        return self._acked

    @property
    def desired(self) -> tuple[int, int, bool]:
        return self._desired

    @property
    def in_flight(self) -> SyncState | None:
        return self._in_flight

    @property
    def pending(self) -> bool:
        return self._in_flight is not None or self._desired != self._acked

    def request(self, scene_id: int, layer_mask: int, muted: bool) -> bool:
        """Publish the latest state, coalescing any unsent intermediate state."""

        candidate = SyncState(scene_id, layer_mask, muted, generation=0).state_tuple
        if candidate == self._desired:
            return False
        if self._desired != self._acked:
            self.requests_coalesced += 1
        self._desired = candidate
        return True

    def next_command(self) -> SyncState | None:
        """Return a retry or allocate the next single in-flight generation."""

        if self._in_flight is not None:
            self.retries += 1
            return self._in_flight
        if self._desired == self._acked:
            return None
        scene_id, layer_mask, muted = self._desired
        state = SyncState(
            scene_id=scene_id,
            layer_mask=layer_mask,
            muted=muted,
            generation=self._next_generation,
        )
        self._next_generation = (self._next_generation + 1) & 0xFF
        self._in_flight = state
        return state

    def acknowledge(self, state: SyncState) -> bool:
        """Accept only an exact acknowledgement of the one in-flight state."""

        if self._in_flight != state:
            self.acks_ignored += 1
            return False
        self._acked = state.state_tuple
        self._in_flight = None
        return True


@dataclass(frozen=True)
class ApplyEvent:
    """One observable reference-model transition."""

    tick: int
    kind: str
    value: int
    audible_write: bool


class AudioApplyModel:
    """Z80-side reference for immediate safety and quantized acquisitions.

    `requested` acknowledgement occurs when ``receive`` accepts a command.
    Musical application may happen at a later row or scene boundary.  The
    model intentionally tracks audible writes separately so muted fixtures can
    prove silence after their one safety transition.
    """

    def __init__(
        self,
        *,
        initial_scene_id: int = 0,
        initial_layer_mask: int = 0,
        initial_muted: bool = False,
    ) -> None:
        _bounded_int("initial_scene_id", initial_scene_id, 0, MAX_SCENE_ID)
        _bounded_int("initial_layer_mask", initial_layer_mask, 0, MAX_LAYER_MASK)
        if not isinstance(initial_muted, bool):
            raise ValueError("initial_muted must be Boolean")
        self.current_tick = 0
        self.requested: SyncState | None = None
        self.applied_scene_id = initial_scene_id
        self.applied_layer_mask = initial_layer_mask
        self.audible_layer_mask = 0 if initial_muted else initial_layer_mask
        self.muted = initial_muted
        self._pending_scene_id: int | None = None
        self._pending_scene_tick: int | None = None
        self._pending_layer_mask: int | None = None
        self._pending_layer_tick: int | None = None
        self._pending_resume_tick: int | None = None
        self.events: list[ApplyEvent] = []

    def _event(
        self,
        kind: str,
        value: int = 0,
        *,
        audible_write: bool = False,
    ) -> None:
        self.events.append(
            ApplyEvent(self.current_tick, kind, value, audible_write)
        )

    def _remove_layers_immediately(self, target_mask: int) -> None:
        removed = self.applied_layer_mask & ~target_mask
        if not removed:
            return
        for bit in range(7):
            bit_mask = 1 << bit
            if removed & bit_mask:
                self._event(
                    "layer_off",
                    bit,
                    audible_write=bool(self.audible_layer_mask & bit_mask),
                )
        self.applied_layer_mask &= target_mask
        self.audible_layer_mask &= target_mask

    def receive(
        self,
        state: SyncState,
        *,
        scene_quantum_ticks: int,
        layer_quantum_ticks: int = DEFAULT_LAYER_QUANTUM_TICKS,
    ) -> SyncState:
        """Accept a snapshot and schedule its non-safety changes."""

        _bounded_int("scene_quantum_ticks", scene_quantum_ticks, 1, 0x7FFFFFFF)
        _bounded_int("layer_quantum_ticks", layer_quantum_ticks, 1, 0x7FFFFFFF)

        if self.requested is not None and state.generation == self.requested.generation:
            if state != self.requested:
                raise ValueError("one generation cannot name two different states")
            return state
        if self.requested is not None:
            generation_delta = (
                state.generation - self.requested.generation
            ) & 0xFF
            if generation_delta == 0 or generation_delta >= 0x80:
                self._event("stale_generation", state.generation)
                return self.requested

        self.requested = state
        self._remove_layers_immediately(state.layer_mask)

        scene_changes = state.scene_id != self.applied_scene_id
        if scene_changes:
            self._pending_scene_id = state.scene_id
            self._pending_scene_tick = next_boundary(
                self.current_tick, scene_quantum_ticks
            )
        else:
            self._pending_scene_id = None
            self._pending_scene_tick = None

        if state.layer_mask != self.applied_layer_mask or scene_changes:
            layer_tick = next_boundary(self.current_tick, layer_quantum_ticks)
            if self._pending_scene_tick is not None:
                layer_tick = max(layer_tick, self._pending_scene_tick)
            self._pending_layer_mask = state.layer_mask
            self._pending_layer_tick = layer_tick
        else:
            self._pending_layer_mask = None
            self._pending_layer_tick = None

        if state.muted and not self.muted:
            if self.audible_layer_mask:
                self._event(
                    "all_notes_off",
                    self.audible_layer_mask,
                    audible_write=True,
                )
            self.audible_layer_mask = 0
            self.muted = True
            self._pending_resume_tick = None
            self._event("mute")
        elif not state.muted and self.muted:
            self.muted = False
            resume_tick = next_boundary(self.current_tick, layer_quantum_ticks)
            for pending_tick in (
                self._pending_scene_tick,
                self._pending_layer_tick,
            ):
                if pending_tick is not None:
                    resume_tick = max(resume_tick, pending_tick)
            self._pending_resume_tick = resume_tick
            self._event("unmute_pending", resume_tick)
        return state

    def _next_pending_tick(self) -> int | None:
        pending = [
            tick
            for tick in (
                self._pending_scene_tick,
                self._pending_layer_tick,
                self._pending_resume_tick,
            )
            if tick is not None
        ]
        return min(pending) if pending else None

    def _apply_pending_at_current_tick(self) -> None:
        scene_due = self._pending_scene_tick == self.current_tick
        layer_due = self._pending_layer_tick == self.current_tick
        resume_due = self._pending_resume_tick == self.current_tick

        if scene_due:
            if self.audible_layer_mask and not self.muted:
                self._event(
                    "all_notes_off",
                    self.audible_layer_mask,
                    audible_write=True,
                )
                self.audible_layer_mask = 0
            assert self._pending_scene_id is not None
            self.applied_scene_id = self._pending_scene_id
            self._event("scene_apply", self.applied_scene_id)
            self._pending_scene_id = None
            self._pending_scene_tick = None

        if layer_due:
            assert self._pending_layer_mask is not None
            previous = self.applied_layer_mask
            self.applied_layer_mask = self._pending_layer_mask
            added = self.applied_layer_mask & ~previous
            self._event("logical_layer_apply", self.applied_layer_mask)
            if not self.muted and self._pending_resume_tick is None:
                self.audible_layer_mask = self.applied_layer_mask
                if scene_due and self.applied_layer_mask:
                    self._event(
                        "audible_restart",
                        self.applied_layer_mask,
                        audible_write=True,
                    )
                else:
                    for bit in range(7):
                        if added & (1 << bit):
                            self._event("layer_on", bit, audible_write=True)
            self._pending_layer_mask = None
            self._pending_layer_tick = None

        if scene_due and not layer_due and not self.muted:
            self.audible_layer_mask = self.applied_layer_mask
            if self.audible_layer_mask:
                self._event(
                    "audible_restart",
                    self.audible_layer_mask,
                    audible_write=True,
                )

        if resume_due:
            self._pending_resume_tick = None
            if not self.muted:
                self.audible_layer_mask = self.applied_layer_mask
                if self.audible_layer_mask:
                    self._event(
                        "audible_resume",
                        self.audible_layer_mask,
                        audible_write=True,
                    )

    def advance_to(self, tick: int) -> None:
        """Advance musical phase and apply every due bounded transition."""

        _bounded_int("tick", tick, self.current_tick, 0x7FFFFFFF)
        while True:
            pending_tick = self._next_pending_tick()
            if pending_tick is None or pending_tick > tick:
                break
            if pending_tick < self.current_tick:
                raise AssertionError("pending transition moved backwards")
            self.current_tick = pending_tick
            self._apply_pending_at_current_tick()
        self.current_tick = tick
