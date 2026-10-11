"""Static RAM, bank, chip-write, queue, and mailbox budget models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class MemoryRegion:
    name: str
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


def validate_memory_layout(
    regions: Iterable[MemoryRegion],
    *,
    address_space_bytes: int = 0x2000,
) -> tuple[MemoryRegion, ...]:
    """Require a unique, contiguous, exact cover of bounded Z80 RAM."""

    if (
        isinstance(address_space_bytes, bool)
        or not isinstance(address_space_bytes, int)
        or address_space_bytes < 1
    ):
        raise ValueError("address space must be a positive integer")
    normalized = tuple(regions)
    if not normalized:
        raise ValueError("memory layout cannot be empty")
    names: set[str] = set()
    expected_start = 0
    for region in normalized:
        if not isinstance(region, MemoryRegion):
            raise ValueError("layout contains a non-region value")
        if not region.name or region.name in names:
            raise ValueError(f"invalid or duplicate region name {region.name!r}")
        names.add(region.name)
        if (
            isinstance(region.start, bool)
            or isinstance(region.end, bool)
            or not isinstance(region.start, int)
            or not isinstance(region.end, int)
            or region.start != expected_start
            or region.end < region.start
        ):
            raise ValueError(
                f"region {region.name} does not begin at contiguous address "
                f"{expected_start:#06x}"
            )
        expected_start = region.end + 1
    if expected_start != address_space_bytes:
        raise ValueError(
            f"layout covers {expected_start} bytes, expected {address_space_bytes}"
        )
    return normalized


@dataclass(frozen=True)
class BankBudget:
    """One active score payload budget."""

    payload_bytes: int
    maximum_bytes: int = 0x07F0
    descriptor_bytes: int = 16
    region_bytes: int = 0x0800
    event_count_per_tick: int = 0
    maximum_events_per_tick: int = 12

    def validate(self) -> BankBudget:
        for name, value in (
            ("payload_bytes", self.payload_bytes),
            ("maximum_bytes", self.maximum_bytes),
            ("descriptor_bytes", self.descriptor_bytes),
            ("region_bytes", self.region_bytes),
            ("event_count_per_tick", self.event_count_per_tick),
            ("maximum_events_per_tick", self.maximum_events_per_tick),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if (
            self.maximum_bytes < 1
            or self.descriptor_bytes < 1
            or self.region_bytes < 1
            or self.maximum_bytes + self.descriptor_bytes > self.region_bytes
            or not 0 <= self.payload_bytes <= self.maximum_bytes
        ):
            raise ValueError("score bank payload exceeds its bounded region")
        if (
            self.maximum_events_per_tick < 0
            or not 0 <= self.event_count_per_tick <= self.maximum_events_per_tick
        ):
            raise ValueError("score bank event count exceeds its per-tick bound")
        return self


@dataclass(frozen=True)
class ChipWriteBudget:
    ym_writes: int
    psg_writes: int
    ym_limit: int = 32
    psg_limit: int = 12

    def validate(self) -> ChipWriteBudget:
        for name, value, limit in (
            ("YM", self.ym_writes, self.ym_limit),
            ("PSG", self.psg_writes, self.psg_limit),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit < 0
                or not 0 <= value <= limit
            ):
                raise ValueError(f"{name} write count exceeds the V-int bound")
        return self


@dataclass(frozen=True)
class FrameCommandBudget:
    state_commands: int
    sfx_commands: int
    state_limit: int = 1
    sfx_limit: int = 2
    service_limit: int = 4

    @property
    def total(self) -> int:
        return self.state_commands + self.sfx_commands

    def validate(self) -> FrameCommandBudget:
        values = (
            self.state_commands,
            self.sfx_commands,
            self.state_limit,
            self.sfx_limit,
            self.service_limit,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("command budgets must be integers")
        if (
            min(values) < 0
            or self.state_commands > self.state_limit
            or self.sfx_commands > self.sfx_limit
            or self.total > self.service_limit
        ):
            raise ValueError("display-frame commands exceed the Z80 service budget")
        return self


class OrdinaryQueueModel:
    """A bounded 16-entry ring occupancy model without payload assumptions."""

    def __init__(self, capacity: int = 16, service_per_vint: int = 4) -> None:
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity must be a positive integer")
        if (
            isinstance(service_per_vint, bool)
            or not isinstance(service_per_vint, int)
            or service_per_vint < 1
        ):
            raise ValueError("service rate must be a positive integer")
        self.capacity = capacity
        self.service_per_vint = service_per_vint
        self.occupancy = 0
        self.high_water = 0
        self.dropped = 0

    def post(self, count: int) -> int:
        """Post up to available capacity and return accepted commands."""

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("posted count must be a non-negative integer")
        accepted = min(count, self.capacity - self.occupancy)
        self.occupancy += accepted
        self.dropped += count - accepted
        self.high_water = max(self.high_water, self.occupancy)
        return accepted

    def service(self) -> int:
        """Service one V-int and return removed commands."""

        removed = min(self.occupancy, self.service_per_vint)
        self.occupancy -= removed
        return removed

    def display_frame(self, posted: int) -> tuple[int, int]:
        """Model post-then-service ordering for one display frame."""

        accepted = self.post(posted)
        serviced = self.service()
        return accepted, serviced


class UrgentMailboxModel:
    """One-deep mailbox that never overwrites an unacknowledged command."""

    def __init__(self) -> None:
        self.pending: int | None = None
        self.rejected = 0

    def post(self, command: int) -> bool:
        if isinstance(command, bool) or not isinstance(command, int):
            raise ValueError("urgent command must be an integer")
        if not 0 <= command <= 0xFF:
            raise ValueError("urgent command must be an unsigned byte")
        if self.pending is not None:
            self.rejected += 1
            return False
        self.pending = command
        return True

    def service(self) -> int | None:
        command = self.pending
        self.pending = None
        return command
