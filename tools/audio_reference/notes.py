"""Finite note-off and monophonic voice invariants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


MAX_NOTE_DURATION_TICKS = 384
VOICE_IDS = frozenset(
    (
        "ym_ch0",
        "psg_noise",
        "ym_ch1",
        "ym_ch2",
        "ym_ch3",
        "psg_tones",
        "ym_ch4",
        "ym_ch5",
    )
)


@dataclass(frozen=True)
class NoteEvent:
    """One note whose end is statically knowable."""

    voice: str
    start_tick: int
    duration_ticks: int
    explicit_key_off: bool = True

    @property
    def end_tick(self) -> int:
        return self.start_tick + self.duration_ticks


def validate_note_events(
    events: Iterable[NoteEvent],
    *,
    maximum_duration_ticks: int = MAX_NOTE_DURATION_TICKS,
) -> tuple[NoteEvent, ...]:
    """Reject open, oversized, invalid, or overlapping monophonic notes."""

    if (
        isinstance(maximum_duration_ticks, bool)
        or not isinstance(maximum_duration_ticks, int)
        or maximum_duration_ticks < 1
    ):
        raise ValueError("maximum duration must be a positive integer")
    normalized = tuple(events)
    by_voice: dict[str, list[NoteEvent]] = {}
    for index, event in enumerate(normalized):
        if not isinstance(event, NoteEvent):
            raise ValueError(f"event {index} is not a NoteEvent")
        if event.voice not in VOICE_IDS:
            raise ValueError(f"event {index} uses unknown voice {event.voice!r}")
        if (
            isinstance(event.start_tick, bool)
            or not isinstance(event.start_tick, int)
            or event.start_tick < 0
        ):
            raise ValueError(f"event {index} has invalid start tick")
        if (
            isinstance(event.duration_ticks, bool)
            or not isinstance(event.duration_ticks, int)
            or not 1 <= event.duration_ticks <= maximum_duration_ticks
        ):
            raise ValueError(
                f"event {index} duration must be within "
                f"1..{maximum_duration_ticks}"
            )
        if event.explicit_key_off is not True:
            raise ValueError(f"event {index} lacks explicit key-off")
        by_voice.setdefault(event.voice, []).append(event)

    for voice, voice_events in by_voice.items():
        ordered = sorted(voice_events, key=lambda event: (event.start_tick, event.end_tick))
        for earlier, later in zip(ordered, ordered[1:]):
            if later.start_tick < earlier.end_tick:
                raise ValueError(
                    f"voice {voice} overlaps notes ending at {earlier.end_tick} "
                    f"and starting at {later.start_tick}"
                )
    return normalized
