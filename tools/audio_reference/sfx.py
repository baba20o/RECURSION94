"""Bounded deterministic per-display-frame SFX aggregation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum


class SfxPriority(IntEnum):
    """Frozen four-level presentation priority."""

    COSMETIC = 0
    ROUTINE = 1
    CRITICAL = 2
    TERMINAL = 3


def _byte(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be an unsigned byte")
    return value


@dataclass(frozen=True)
class SfxEvent:
    """One normalized presentation event before aggregation."""

    sfx_id: int
    priority: SfxPriority
    sequence: int
    source_id: int
    merge_group: int | None = None
    multiplicity: int = 1

    def __post_init__(self) -> None:
        _byte("sfx_id", self.sfx_id)
        if not isinstance(self.priority, SfxPriority):
            raise ValueError("priority must be SfxPriority")
        _byte("sequence", self.sequence)
        _byte("source_id", self.source_id)
        if self.merge_group is not None:
            _byte("merge_group", self.merge_group)
        if (
            isinstance(self.multiplicity, bool)
            or not isinstance(self.multiplicity, int)
            or not 1 <= self.multiplicity <= 15
        ):
            raise ValueError("multiplicity must be within 1..15")

    @property
    def merge_key(self) -> tuple[int, int]:
        return (
            self.sfx_id,
            self.sfx_id if self.merge_group is None else self.merge_group,
        )

    @property
    def rank_key(self) -> tuple[int, int, int, int]:
        """Smaller tuple is selected first."""

        return (-int(self.priority), self.sequence, self.source_id, self.sfx_id)

    def wire_bytes(self, opcode: int = 0x03) -> bytes:
        """Encode priority/multiplicity into one four-byte SFX command."""

        _byte("opcode", opcode)
        packed = (int(self.priority) << 4) | self.multiplicity
        return bytes((opcode, self.sfx_id, packed, self.sequence))


class BoundedSfxCollector:
    """Keep the best bounded events and emit at most two commands."""

    def __init__(self, capacity: int = 8, emit_limit: int = 2) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if (
            isinstance(emit_limit, bool)
            or not isinstance(emit_limit, int)
            or not 1 <= emit_limit <= capacity
        ):
            raise ValueError("emit_limit must be within 1..capacity")
        self.capacity = capacity
        self.emit_limit = emit_limit
        self._events: dict[tuple[int, int], SfxEvent] = {}
        self.merged = 0
        self.dropped = [0, 0, 0, 0]

    @property
    def count(self) -> int:
        return len(self._events)

    def add(self, event: SfxEvent) -> bool:
        """Merge, retain, replace, or deterministically drop one event."""

        key = event.merge_key
        existing = self._events.get(key)
        if existing is not None:
            selected = min((existing, event), key=lambda item: item.rank_key)
            merged_priority = max(existing.priority, event.priority)
            merged_multiplicity = min(
                15, existing.multiplicity + event.multiplicity
            )
            self._events[key] = replace(
                selected,
                priority=merged_priority,
                multiplicity=merged_multiplicity,
            )
            self.merged += 1
            return True

        if len(self._events) < self.capacity:
            self._events[key] = event
            return True

        worst_key, worst = max(
            self._events.items(), key=lambda item: item[1].rank_key
        )
        if event.rank_key < worst.rank_key:
            del self._events[worst_key]
            self.dropped[int(worst.priority)] += 1
            self._events[key] = event
            return True
        self.dropped[int(event.priority)] += 1
        return False

    def flush(self) -> tuple[SfxEvent, ...]:
        """Emit the best events, count the bounded remainder as dropped, and clear."""

        ordered = sorted(self._events.values(), key=lambda event: event.rank_key)
        emitted = tuple(ordered[: self.emit_limit])
        for event in ordered[self.emit_limit :]:
            self.dropped[int(event.priority)] += 1
        self._events.clear()
        return emitted
