"""Integer-only 24-PPQN timing reference for NTSC and PAL Z80 V-int ticks."""

from __future__ import annotations

from dataclasses import dataclass


PPQN = 24
SUPPORTED_REFRESH_HZ = frozenset((50, 60))
MAX_SUPPORTED_BPM = 160
MAX_TICKS_PER_VINT = 2


@dataclass
class TempoClock:
    """A deterministic integer accumulator clocked only by the Z80 V-int."""

    bpm: int
    refresh_hz: int
    ppqn: int = PPQN
    accumulator: int = 0
    total_ticks: int = 0
    vint_count: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.bpm, bool) or not isinstance(self.bpm, int):
            raise ValueError("bpm must be an integer")
        if not 1 <= self.bpm <= MAX_SUPPORTED_BPM:
            raise ValueError(f"bpm must be within 1..{MAX_SUPPORTED_BPM}")
        if self.refresh_hz not in SUPPORTED_REFRESH_HZ:
            raise ValueError("refresh_hz must be 50 or 60")
        if isinstance(self.ppqn, bool) or not isinstance(self.ppqn, int):
            raise ValueError("ppqn must be an integer")
        if self.ppqn != PPQN:
            raise ValueError(f"the frozen contract requires {PPQN} PPQN")
        if not 0 <= self.accumulator < self.threshold:
            raise ValueError("initial accumulator must be below the threshold")
        if self.total_ticks < 0 or self.vint_count < 0:
            raise ValueError("clock counters cannot be negative")

    @property
    def threshold(self) -> int:
        """Accumulator units required for one PPQN tick."""

        return 60 * self.refresh_hz

    @property
    def increment(self) -> int:
        """Accumulator units added by one Z80 V-int."""

        return self.bpm * self.ppqn

    def step_vint(self) -> int:
        """Advance one V-int and return the bounded number of PPQN ticks."""

        self.accumulator += self.increment
        ticks = 0
        while self.accumulator >= self.threshold:
            self.accumulator -= self.threshold
            ticks += 1
        if ticks > MAX_TICKS_PER_VINT:
            raise AssertionError(
                f"tempo advanced {ticks} ticks in one V-int; bound is "
                f"{MAX_TICKS_PER_VINT}"
            )
        self.total_ticks += ticks
        self.vint_count += 1
        return ticks

    def advance_vints(self, count: int) -> int:
        """Advance a non-negative number of V-ints and return new PPQN ticks."""

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("V-int count must be a non-negative integer")
        before = self.total_ticks
        for _ in range(count):
            self.step_vint()
        return self.total_ticks - before

    def advance_seconds(self, seconds: int) -> int:
        """Advance a whole number of display seconds."""

        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            raise ValueError("seconds must be a non-negative integer")
        return self.advance_vints(seconds * self.refresh_hz)

    def schedule(self, vint_count: int) -> tuple[tuple[int, int], ...]:
        """Return ``(absolute_vint, tick_count)`` entries that advance time."""

        if isinstance(vint_count, bool) or not isinstance(vint_count, int):
            raise ValueError("V-int count must be an integer")
        if vint_count < 0:
            raise ValueError("V-int count cannot be negative")
        result: list[tuple[int, int]] = []
        for _ in range(vint_count):
            ticks = self.step_vint()
            if ticks:
                result.append((self.vint_count, ticks))
        return tuple(result)
