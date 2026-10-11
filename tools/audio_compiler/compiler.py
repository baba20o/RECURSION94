#!/usr/bin/env python3
"""Compile the frozen host-side audio scene metadata and original score banks.

The normal command deliberately emits metadata only. It does not assemble,
link, or modify the ROM. Fully composed fixed-size bank bytes are built in
memory for validation and standalone tests; a later, explicitly wired build
step will place them under ``assets/generated/audio``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.content_compiler.compiler import (
    ContentCompileError,
    ROSTER,
    load_and_validate as load_content,
)


FORMAT_VERSION = 1
AUDIO_REGISTRY_OUTPUT = "audio_scene_registry.json"
AUDIO_BANKS_OUTPUT = "audio_bank_manifest.json"
AUDIO_CHECKSUMS_OUTPUT = "audio_registry.sha256"
OUTPUT_NAMES = frozenset(
    (AUDIO_REGISTRY_OUTPUT, AUDIO_BANKS_OUTPUT, AUDIO_CHECKSUMS_OUTPUT)
)

MAX_SCENE_COUNT = 64
MAX_SCENE_ID = MAX_SCENE_COUNT - 1
MAX_ECHO_COUNT = 6
MAX_LAYER_MASK = 0x7F
PPQN = 24
ROW_TICKS = 6
BAR_TICKS = 96
COMMAND_BYTES = 4
COMMAND_RING_ENTRIES = 16
COMMAND_SERVICE_PER_VINT = 4
MAX_STATE_COMMANDS_PER_DISPLAY_FRAME = 1
MAX_SFX_INPUTS_PER_DISPLAY_FRAME = 8
MAX_SFX_COMMANDS_PER_DISPLAY_FRAME = 2
MAX_ORDINARY_COMMANDS_PER_DISPLAY_FRAME = (
    MAX_STATE_COMMANDS_PER_DISPLAY_FRAME + MAX_SFX_COMMANDS_PER_DISPLAY_FRAME
)
MAX_MUSIC_BANK_BYTES = 0x0800
BANK_DESCRIPTOR_BYTES = 16
MAX_MUSIC_PAYLOAD_BYTES = MAX_MUSIC_BANK_BYTES - BANK_DESCRIPTOR_BYTES
MAX_SFX_TABLE_BYTES = 0x0200
MAX_Z80_CODE_BYTES = 0x1000
MAX_NOTE_DURATION_TICKS = BAR_TICKS * 4
MAX_MUSIC_EVENTS_PER_TICK = 12
MAX_YM_WRITES_PER_VINT = 32
MAX_PSG_WRITES_PER_VINT = 12

BANK_MAGIC = b"R94A"
BANK_FORMAT_VERSION = 1
BANK_ROWS = 16
BANK_TICKS_PER_ROW = 6
BANK_LAYER_COUNT = 7
BANK_ROW_BYTES = BANK_LAYER_COUNT
BANK_PAYLOAD_BYTES = BANK_ROWS * BANK_ROW_BYTES
BANK_COMPILED_BYTES = BANK_DESCRIPTOR_BYTES + BANK_PAYLOAD_BYTES
NOTE_ALPHABET = ".123456789ABC"
NOTE_NAMES = (
    "REST",
    "C",
    "C_SHARP",
    "D",
    "D_SHARP",
    "E",
    "F",
    "F_SHARP",
    "G",
    "G_SHARP",
    "A",
    "A_SHARP",
    "B",
)
LAYER_NAMES = (
    "base",
    "percussion",
    "bass",
    "harmony",
    "lead",
    "texture",
    "resolution",
)
YM_MUSIC_LAYER_BITS = frozenset((0, 2, 3, 4, 6))
PSG_PERCUSSION_LAYER_BIT = 1
PSG_TEXTURE_LAYER_BIT = 5
SCORE_SOURCE_RELATIVE = Path("assets/source/music/audio_score_v2.json")

SECTOR_CONTRACTS = (
    ("BOOT", 80, 2, ("B01", "B02", "B03", "B04"), "B04"),
    ("VIDEO", 100, 3, ("V01", "V02", "V03", "V04"), "V04"),
    ("SOUND", 120, 4, ("S01", "S02", "S03", "S04"), "S04"),
    ("MEMORY", 140, 5, ("M01", "M02", "M03", "M04"), "M04"),
    ("INTERRUPT", 160, 6, ("I01", "I02", "I03", "I04"), "I04"),
)
SECTOR_BY_ID = {
    sector_id: {
        "order": order,
        "tempo_bpm": tempo_bpm,
        "echo_cap": echo_cap,
        "encounters": encounters,
        "boss": boss,
    }
    for order, (
        sector_id,
        tempo_bpm,
        echo_cap,
        encounters,
        boss,
    ) in enumerate(SECTOR_CONTRACTS, 1)
}

EXPECTED_MUSIC_BY_ENCOUNTER = {
    "B01": "MUS_BOOT_PULSE",
    "B02": "MUS_BOOT_PULSE",
    "B03": "MUS_BOOT_PULSE",
    "B04": "MUS_BOOT_WATCHDOG",
    "V01": "MUS_VIDEO_MOTION",
    "V02": "MUS_VIDEO_RELAY",
    "V03": "MUS_VIDEO_PIPELINE",
    "V04": "MUS_VIDEO_SCHEDULER",
    "S01": "MUS_SOUND_NOISE_FLOOR",
    "S02": "MUS_SOUND_PHASE_LOCK",
    "S03": "MUS_SOUND_COUNTERPOINT",
    "S04": "MUS_SOUND_COLLECTOR",
    "M01": "MUS_MEMORY_WRITE",
    "M02": "MUS_MEMORY_BAD_SECTOR",
    "M03": "MUS_MEMORY_COMPACTION",
    "M04": "MUS_MEMORY_FORK",
    "I01": "MUS_INTERRUPT_PRIORITY",
    "I02": "MUS_INTERRUPT_NON_MASKABLE",
    "I03": "MUS_INTERRUPT_VECTOR_TABLE",
    "I04": "MUS_INTERRUPT_ORACLE",
}

EXPECTED_BOSS_PHASE_COUNTS = {
    "B04": 3,
    "V04": 3,
    "S04": 3,
    "M04": 3,
    "I04": 4,
}

RESULT_SCENES = (
    ("RESULT_STABLE", "STABLE"),
    ("RESULT_OPTIMIZED", "OPTIMIZED"),
    ("RESULT_TRANSCENDENT", "TRANSCENDENT"),
)
FINAL_SCENES = (
    ("PROGRAM_STABLE", "PROGRAM STABLE"),
    ("SEED_TRANSMITTED", "SEED TRANSMITTED"),
)

# Shared boss/result/final banks use bounded phase offsets so distinct scene
# IDs do not collapse to an identical first cue.
SCENE_START_ROWS = (
    0,
    *(0 for _ in range(18)),
    0,
    5,
    10,
    0,
    5,
    10,
    0,
    5,
    10,
    0,
    5,
    10,
    0,
    4,
    8,
    12,
    0,
    5,
    10,
    0,
    8,
)

SFX_CONTRACTS = (
    (0, "NONE", 0, "none"),
    (1, "SHOT", 1, "by_sfx"),
    (2, "DASH_PHASE", 1, "by_sfx"),
    (3, "INTERACT", 1, "by_source"),
    (4, "DOOR", 1, "by_source"),
    (5, "SWITCH", 1, "by_source"),
    (6, "PICKUP_DROP", 1, "by_source"),
    (7, "DAMAGE", 2, "by_source"),
    (8, "DEATH", 2, "none"),
    (9, "RESET", 2, "none"),
    (10, "ECHO_MATERIALIZE", 2, "by_sfx"),
    (11, "ANCHOR", 2, "by_source"),
    (12, "ALERT", 2, "by_source"),
    (13, "BOSS_PHASE", 3, "none"),
    (14, "PROGRAM_STABLE", 3, "none"),
    (15, "SEED_TRANSMITTED", 3, "none"),
)

VOICE_CONTRACTS = (
    (0, "base", "ym2612", "ym_ch0"),
    (1, "echo_1_percussion", "psg", "psg_noise"),
    (2, "echo_2_bass", "ym2612", "ym_ch1"),
    (3, "echo_3_harmony", "ym2612", "ym_ch2"),
    (4, "echo_4_lead", "ym2612", "ym_ch3"),
    (5, "echo_5_texture", "psg", "psg_tones"),
    (6, "echo_6_resolution", "ym2612", "ym_ch4"),
)

Z80_LAYOUT = (
    ("driver_code", 0x0000, 0x0FFF, "immutable driver code and tables"),
    ("sequencer_state", 0x1000, 0x11FF, "voices, shadows, pending state"),
    ("active_music_bank", 0x1200, 0x19FF, "maximum 2 KiB score bank"),
    ("active_sfx_tables", 0x1A00, 0x1BFF, "SFX and instrument tables"),
    ("decode_scratch", 0x1C00, 0x1DFF, "bounded decoder scratch"),
    ("stack_reserve", 0x1E00, 0x1EFF, "stack, canary, high-water"),
    ("command_ring", 0x1F00, 0x1F3F, "16 four-byte ordinary commands"),
    ("extended_status", 0x1F40, 0x1F7F, "requested/applied scene status"),
    ("reserved", 0x1F80, 0x1FDF, "future bounded protocol state"),
    ("core_status", 0x1FE0, 0x1FEF, "backward-compatible core status"),
    ("urgent_mailbox", 0x1FF0, 0x1FFF, "one-deep urgent command mailbox"),
)

_MUSIC_ID_RE = re.compile(r"^MUS_[A-Z0-9]+(?:_[A-Z0-9]+)*$")
_PHASE_ID_RE = re.compile(r"^[BVSMI][0-9]{2}_PHASE_[A-Z0-9]+(?:_[A-Z0-9]+)*$")


class AudioCompileError(RuntimeError):
    """Base exception for audio metadata compilation."""


class AudioValidationError(AudioCompileError):
    """Raised when content or a generated audio contract is invalid."""


@dataclass(frozen=True)
class ValidatedAudio:
    """Normalized audio-facing projection of the campaign sources."""

    content_source_sha256: str
    audio_source_sha256: str
    score_source_sha256: str
    campaign: dict[str, Any]
    encounters: tuple[dict[str, Any], ...]
    music_ids: tuple[str, ...]
    phase_ids: tuple[tuple[str, str], ...]
    score_banks: tuple[ScoreBank, ...]


@dataclass(frozen=True)
class ScoreBank:
    """One fully validated compact tracker bank."""

    bank_id: int
    bank_key: str
    title: str
    tempo_bpm: int
    echo_cap: int
    flags: int
    payload: bytes
    binary: bytes
    crc32: int
    note_events: int
    maximum_row_events: int
    maximum_row_ym_writes: int
    maximum_row_psg_writes: int


@dataclass(frozen=True)
class AudioArtifactBundle:
    """All deterministic files produced by one compiler run."""

    files: Mapping[str, bytes]
    bank_binaries: Mapping[str, bytes]
    source_sha256: str
    registry_sha256: str
    banks_sha256: str
    scene_count: int
    bank_count: int


def _error(path: str, message: str) -> None:
    raise AudioValidationError(f"{path}: {message}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _strict_json_load(path: Path) -> dict[str, Any]:
    """Load strict JSON with duplicate-key and non-finite-number rejection."""

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _error(str(path), f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        _error(str(path), f"non-finite JSON number {token}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _error(str(path), f"cannot read strict JSON: {exc}")
    if not isinstance(value, dict):
        _error(str(path), "top-level JSON value must be an object")
    return value


def _expect_equal(path: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        _error(path, f"expected {expected!r}, got {actual!r}")


def _validate_sector_contracts(campaign: dict[str, Any]) -> None:
    sectors = campaign.get("sectors")
    if not isinstance(sectors, list):
        _error("campaign.sectors", "expected array")
    if len(sectors) != len(SECTOR_CONTRACTS):
        _error("campaign.sectors", f"expected {len(SECTOR_CONTRACTS)} sectors")
    for order, (
        expected_id,
        expected_tempo,
        expected_echo_cap,
        expected_encounters,
        expected_boss,
    ) in enumerate(SECTOR_CONTRACTS, 1):
        sector = sectors[order - 1]
        if not isinstance(sector, dict):
            _error(f"campaign.sectors[{order - 1}]", "expected object")
        path = f"campaign.sectors[{order - 1}]"
        _expect_equal(f"{path}.order", sector.get("order"), order)
        _expect_equal(f"{path}.id", sector.get("id"), expected_id)
        _expect_equal(f"{path}.tempo_bpm", sector.get("tempo_bpm"), expected_tempo)
        _expect_equal(f"{path}.echo_cap", sector.get("echo_cap"), expected_echo_cap)
        _expect_equal(
            f"{path}.encounters",
            sector.get("encounters"),
            list(expected_encounters),
        )
        _expect_equal(f"{path}.boss", sector.get("boss"), expected_boss)


def _validate_z80_layout() -> None:
    previous_end = -1
    names: set[str] = set()
    for name, start, end, _purpose in Z80_LAYOUT:
        if name in names:
            _error("z80_layout", f"duplicate region {name}")
        names.add(name)
        if not (_is_int(start) and _is_int(end) and 0 <= start <= end <= 0x1FFF):
            _error(f"z80_layout.{name}", "invalid 8 KiB address range")
        if start != previous_end + 1:
            _error(
                f"z80_layout.{name}",
                f"layout gap or overlap after ${previous_end:04X}",
            )
        previous_end = end
    if previous_end != 0x1FFF:
        _error("z80_layout", "layout must cover exactly $0000-$1FFF")
    sizes = {name: end - start + 1 for name, start, end, _ in Z80_LAYOUT}
    _expect_equal("z80_layout.driver_code", sizes["driver_code"], MAX_Z80_CODE_BYTES)
    _expect_equal(
        "z80_layout.active_music_bank",
        sizes["active_music_bank"],
        MAX_MUSIC_BANK_BYTES,
    )
    _expect_equal(
        "z80_layout.active_sfx_tables",
        sizes["active_sfx_tables"],
        MAX_SFX_TABLE_BYTES,
    )
    _expect_equal(
        "z80_layout.command_ring",
        sizes["command_ring"],
        COMMAND_RING_ENTRIES * COMMAND_BYTES,
    )
    if BANK_DESCRIPTOR_BYTES + MAX_MUSIC_PAYLOAD_BYTES != MAX_MUSIC_BANK_BYTES:
        _error("bank_budget", "descriptor plus payload must equal active bank region")
    music_voices = [voice for _bit, _role, _chip, voice in VOICE_CONTRACTS]
    if len(music_voices) != 7 or len(set(music_voices)) != 7:
        _error("voice_contracts", "seven music layers require unique voices")
    if "ym_ch5" in music_voices:
        _error("voice_contracts", "YM channel 5 must remain reserved for SFX")


def _expected_score_bank_contracts(
    music_ids: Sequence[str],
) -> tuple[tuple[int, str, int, int, int], ...]:
    """Return bank ID/key/tempo/cap/flags in the frozen load order."""

    result: list[tuple[int, str, int, int, int]] = []
    for bank_id, music_id in enumerate(music_ids, 1):
        encounter_id = next(
            encounter_id
            for encounter_id, expected_music in EXPECTED_MUSIC_BY_ENCOUNTER.items()
            if expected_music == music_id
        )
        roster = next(
            entry for entry in ROSTER if entry.encounter_id == encounter_id
        )
        sector = SECTOR_BY_ID[roster.sector]
        result.append(
            (
                bank_id,
                music_id.replace("MUS_", "BANK_", 1),
                sector["tempo_bpm"],
                sector["echo_cap"],
                0,
            )
        )
    result.append((19, "BANK_RESULTS", 120, MAX_ECHO_COUNT, 1))
    result.append((20, "BANK_FINAL", 160, MAX_ECHO_COUNT, 2))
    return tuple(result)


def _score_bank_payload(
    layers: Mapping[str, Any],
    *,
    path: str,
    echo_cap: int,
) -> tuple[bytes, int, int, int, int]:
    """Validate tracker strings and compile one row-major fixed payload."""

    if set(layers) != set(LAYER_NAMES):
        _error(
            f"{path}.layers",
            f"expected exact layer keys {list(LAYER_NAMES)}, got {sorted(layers)}",
        )
    layer_notes: list[bytes] = []
    for layer_bit, layer_name in enumerate(LAYER_NAMES):
        chunks = layers[layer_name]
        layer_path = f"{path}.layers.{layer_name}"
        if not isinstance(chunks, list) or len(chunks) != 4:
            _error(layer_path, "expected four four-character tracker chunks")
        joined = ""
        for chunk_index, chunk in enumerate(chunks):
            if not isinstance(chunk, str) or len(chunk) != 4:
                _error(
                    f"{layer_path}[{chunk_index}]",
                    "expected exactly four note characters",
                )
            invalid = sorted(set(chunk) - set(NOTE_ALPHABET))
            if invalid:
                _error(
                    f"{layer_path}[{chunk_index}]",
                    f"invalid note characters {invalid}",
                )
            joined += chunk
        if len(joined) != BANK_ROWS:
            _error(layer_path, f"expected {BANK_ROWS} compiled rows")
        encoded = bytes(NOTE_ALPHABET.index(character) for character in joined)
        if layer_bit > echo_cap and any(encoded):
            _error(
                layer_path,
                f"layer bit {layer_bit} exceeds echo cap {echo_cap}",
            )
        if layer_bit <= echo_cap and not any(encoded):
            _error(layer_path, "enabled layer must contain at least one note")
        layer_notes.append(encoded)

    payload = bytes(
        layer_notes[layer_bit][row]
        for row in range(BANK_ROWS)
        for layer_bit in range(BANK_LAYER_COUNT)
    )
    row_events: list[int] = []
    row_ym_writes: list[int] = []
    row_psg_writes: list[int] = []
    note_events = 0
    for row in range(BANK_ROWS):
        notes = payload[
            row * BANK_ROW_BYTES : (row + 1) * BANK_ROW_BYTES
        ]
        events = sum(note != 0 for note in notes)
        note_events += events
        row_events.append(events)
        ym_notes = sum(
            notes[layer_bit] != 0 for layer_bit in YM_MUSIC_LAYER_BITS
        )
        percussion = notes[PSG_PERCUSSION_LAYER_BIT] != 0
        texture = notes[PSG_TEXTURE_LAYER_BIT] != 0
        # Five fixed music key-offs, then three writes per sounding YM note.
        row_ym_writes.append(5 + 3 * ym_notes)
        # Noise/tone mute, two-write percussion, and three-write texture note.
        row_psg_writes.append(
            2 + (2 if percussion else 0) + (3 if texture else 0)
        )
    maximum_events = max(row_events)
    maximum_ym = max(row_ym_writes)
    maximum_psg = max(row_psg_writes)
    if maximum_events > MAX_MUSIC_EVENTS_PER_TICK:
        _error(path, "row event count exceeds compiler budget")
    if maximum_ym > MAX_YM_WRITES_PER_VINT:
        _error(path, "row YM writes exceed V-int budget")
    if maximum_psg > MAX_PSG_WRITES_PER_VINT:
        _error(path, "row PSG writes exceed V-int budget")
    return payload, note_events, maximum_events, maximum_ym, maximum_psg


def _load_and_validate_score(
    repository_root: Path,
    music_ids: Sequence[str],
) -> tuple[tuple[ScoreBank, ...], str]:
    score_path = repository_root / SCORE_SOURCE_RELATIVE
    source = _strict_json_load(score_path)
    expected_top = {"schema", "schema_version", "provenance", "format", "banks"}
    if set(source) != expected_top:
        _error(
            str(score_path),
            f"expected exact keys {sorted(expected_top)}, got {sorted(source)}",
        )
    _expect_equal(f"{score_path}.schema", source["schema"], "recursion94.audio_score")
    _expect_equal(f"{score_path}.schema_version", source["schema_version"], 1)

    provenance = source["provenance"]
    if not isinstance(provenance, dict):
        _error(f"{score_path}.provenance", "expected object")
    _expect_equal(
        f"{score_path}.provenance.external_samples",
        provenance.get("external_samples"),
        False,
    )
    for field in ("author", "license", "description"):
        if not isinstance(provenance.get(field), str) or not provenance[field]:
            _error(f"{score_path}.provenance.{field}", "expected non-empty string")

    score_format = source["format"]
    if not isinstance(score_format, dict):
        _error(f"{score_path}.format", "expected object")
    expected_format = {
        "rows",
        "ticks_per_row",
        "layers",
        "note_alphabet",
        "note_names",
    }
    if set(score_format) != expected_format:
        _error(f"{score_path}.format", "unexpected score format fields")
    _expect_equal(f"{score_path}.format.rows", score_format["rows"], BANK_ROWS)
    _expect_equal(
        f"{score_path}.format.ticks_per_row",
        score_format["ticks_per_row"],
        BANK_TICKS_PER_ROW,
    )
    _expect_equal(
        f"{score_path}.format.layers",
        score_format["layers"],
        list(LAYER_NAMES),
    )
    _expect_equal(
        f"{score_path}.format.note_alphabet",
        score_format["note_alphabet"],
        NOTE_ALPHABET,
    )
    note_names = score_format["note_names"]
    _expect_equal(
        f"{score_path}.format.note_names",
        note_names,
        list(NOTE_NAMES),
    )

    banks_source = source["banks"]
    contracts = _expected_score_bank_contracts(music_ids)
    if not isinstance(banks_source, list) or len(banks_source) != len(contracts):
        _error(
            f"{score_path}.banks",
            f"expected {len(contracts)} banks in exact order",
        )
    compiled: list[ScoreBank] = []
    payload_hashes: set[str] = set()
    for index, (bank_source, contract) in enumerate(
        zip(banks_source, contracts, strict=True)
    ):
        bank_id, bank_key, tempo_bpm, echo_cap, flags = contract
        path = f"{score_path}.banks[{index}]"
        if not isinstance(bank_source, dict):
            _error(path, "expected object")
        if set(bank_source) != {"bank_id", "bank_key", "title", "layers"}:
            _error(path, "unexpected score bank fields")
        _expect_equal(f"{path}.bank_id", bank_source["bank_id"], bank_id)
        _expect_equal(f"{path}.bank_key", bank_source["bank_key"], bank_key)
        title = bank_source["title"]
        if not isinstance(title, str) or not title:
            _error(f"{path}.title", "expected non-empty title")
        layers = bank_source["layers"]
        if not isinstance(layers, dict):
            _error(f"{path}.layers", "expected object")
        (
            payload,
            note_events,
            maximum_events,
            maximum_ym,
            maximum_psg,
        ) = _score_bank_payload(layers, path=path, echo_cap=echo_cap)
        payload_sha = hashlib.sha256(payload).hexdigest()
        if payload_sha in payload_hashes:
            _error(path, "score payload must be distinct from every prior bank")
        payload_hashes.add(payload_sha)
        crc32 = zlib.crc32(payload) & 0xFFFFFFFF
        descriptor = (
            BANK_MAGIC
            + bytes(
                (
                    BANK_FORMAT_VERSION,
                    bank_id,
                    tempo_bpm,
                    echo_cap,
                    BANK_ROWS,
                    BANK_TICKS_PER_ROW,
                    BANK_LAYER_COUNT,
                    flags,
                )
            )
            + struct.pack("<I", crc32)
        )
        if len(descriptor) != BANK_DESCRIPTOR_BYTES:
            raise AssertionError("audio bank descriptor size drifted")
        binary = descriptor + payload
        if len(binary) != BANK_COMPILED_BYTES:
            raise AssertionError("audio bank compiled size drifted")
        compiled.append(
            ScoreBank(
                bank_id=bank_id,
                bank_key=bank_key,
                title=title,
                tempo_bpm=tempo_bpm,
                echo_cap=echo_cap,
                flags=flags,
                payload=payload,
                binary=binary,
                crc32=crc32,
                note_events=note_events,
                maximum_row_events=maximum_events,
                maximum_row_ym_writes=maximum_ym,
                maximum_row_psg_writes=maximum_psg,
            )
        )

    score_source_sha256 = hashlib.sha256(_canonical_json(source)).hexdigest()
    return tuple(compiled), score_source_sha256


def load_and_validate(content_root: Path | str) -> ValidatedAudio:
    """Validate campaign audio declarations and return their canonical projection."""

    try:
        validated = load_content(content_root)
    except ContentCompileError as exc:
        raise AudioValidationError(f"content: {exc}") from exc

    campaign = validated.campaign
    encounters = validated.encounters
    _validate_sector_contracts(campaign)
    _validate_z80_layout()

    if len(encounters) != 20:
        _error("encounters", f"expected 20 manifests, got {len(encounters)}")

    music_ids: list[str] = []
    seen_music: set[str] = set()
    phase_ids: list[tuple[str, str]] = []
    seen_phases: set[str] = set()
    audio_projection_encounters: list[dict[str, Any]] = []

    for roster, encounter in zip(ROSTER, encounters, strict=True):
        path = f"encounters.{roster.encounter_id}"
        _expect_equal(f"{path}.id", encounter.get("id"), roster.encounter_id)
        _expect_equal(f"{path}.kind", encounter.get("kind"), roster.kind)
        sector = encounter.get("sector")
        if not isinstance(sector, dict):
            _error(f"{path}.sector", "expected object")
        sector_id = sector.get("id")
        _expect_equal(f"{path}.sector.id", sector_id, roster.sector)
        sector_contract = SECTOR_BY_ID[roster.sector]

        presentation = encounter.get("presentation")
        if not isinstance(presentation, dict):
            _error(f"{path}.presentation", "expected object")
        music_id = presentation.get("music_id")
        if not isinstance(music_id, str) or not _MUSIC_ID_RE.fullmatch(music_id):
            _error(f"{path}.presentation.music_id", "invalid music ID")
        _expect_equal(
            f"{path}.presentation.music_id",
            music_id,
            EXPECTED_MUSIC_BY_ENCOUNTER[roster.encounter_id],
        )
        _expect_equal(
            f"{path}.presentation.tempo_bpm",
            presentation.get("tempo_bpm"),
            sector_contract["tempo_bpm"],
        )
        if music_id not in seen_music:
            seen_music.add(music_id)
            music_ids.append(music_id)

        phases = encounter.get("boss_phases")
        phase_projection: list[str] = []
        if roster.kind == "boss":
            if not isinstance(phases, list):
                _error(f"{path}.boss_phases", "boss requires phase array")
            expected_phase_count = EXPECTED_BOSS_PHASE_COUNTS[roster.encounter_id]
            if len(phases) != expected_phase_count:
                _error(
                    f"{path}.boss_phases",
                    f"expected {expected_phase_count} phases, got {len(phases)}",
                )
            for phase_index, phase in enumerate(phases, 1):
                if not isinstance(phase, dict):
                    _error(
                        f"{path}.boss_phases[{phase_index - 1}]",
                        "expected object",
                    )
                phase_id = phase.get("phase_id")
                if not isinstance(phase_id, str) or not _PHASE_ID_RE.fullmatch(
                    phase_id
                ):
                    _error(
                        f"{path}.boss_phases[{phase_index - 1}].phase_id",
                        "invalid boss phase ID",
                    )
                if phase_id in seen_phases:
                    _error(
                        f"{path}.boss_phases[{phase_index - 1}].phase_id",
                        f"duplicate phase ID {phase_id}",
                    )
                seen_phases.add(phase_id)
                phase_ids.append((roster.encounter_id, phase_id))
                phase_projection.append(phase_id)
        elif phases != []:
            _error(
                f"{path}.boss_phases",
                "primary encounter must declare an empty phase array",
            )

        audio_projection_encounters.append(
            {
                "order": roster.order,
                "id": roster.encounter_id,
                "kind": roster.kind,
                "sector": roster.sector,
                "music_id": music_id,
                "tempo_bpm": sector_contract["tempo_bpm"],
                "echo_cap": sector_contract["echo_cap"],
                "boss_phase_ids": phase_projection,
            }
        )

    if len(music_ids) != 18:
        _error("music_ids", f"expected 18 unique IDs, got {len(music_ids)}")
    if len(phase_ids) != 16:
        _error("boss_phases", f"expected 16 phase IDs, got {len(phase_ids)}")

    repository_root = Path(content_root).resolve().parent
    score_banks, score_source_sha256 = _load_and_validate_score(
        repository_root,
        music_ids,
    )
    audio_projection = {
        "schema": "recursion94.audio_source_projection",
        "schema_version": FORMAT_VERSION,
        "score_source_sha256": score_source_sha256,
        "compiled_score_banks": [
            {
                "bank_id": bank.bank_id,
                "bank_key": bank.bank_key,
                "binary_sha256": hashlib.sha256(bank.binary).hexdigest(),
            }
            for bank in score_banks
        ],
        "sectors": [
            {
                "order": order,
                "id": sector_id,
                "tempo_bpm": tempo,
                "echo_cap": cap,
                "encounters": list(encounter_ids),
                "boss": boss,
            }
            for order, (
                sector_id,
                tempo,
                cap,
                encounter_ids,
                boss,
            ) in enumerate(SECTOR_CONTRACTS, 1)
        ],
        "encounters": audio_projection_encounters,
    }
    audio_source_sha256 = hashlib.sha256(
        _canonical_json(audio_projection)
    ).hexdigest()
    return ValidatedAudio(
        content_source_sha256=validated.source_sha256,
        audio_source_sha256=audio_source_sha256,
        score_source_sha256=score_source_sha256,
        campaign=campaign,
        encounters=encounters,
        music_ids=tuple(music_ids),
        phase_ids=tuple(phase_ids),
        score_banks=score_banks,
    )


def _layer_cap_mask(echo_cap: int) -> int:
    if not _is_int(echo_cap) or not 0 <= echo_cap <= MAX_ECHO_COUNT:
        _error("echo_cap", f"outside 0..{MAX_ECHO_COUNT}")
    return (1 << (echo_cap + 1)) - 1


def _music_sources(validated: ValidatedAudio, music_id: str) -> list[str]:
    return [
        roster.encounter_id
        for roster, encounter in zip(ROSTER, validated.encounters, strict=True)
        if encounter["presentation"]["music_id"] == music_id
    ]


def _build_banks(
    validated: ValidatedAudio,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, bytes]]:
    banks: list[dict[str, Any]] = []
    bank_id_by_music: dict[str, int] = {}
    binary_outputs: dict[str, bytes] = {}
    score_by_id = {bank.bank_id: bank for bank in validated.score_banks}

    def common_score_fields(score: ScoreBank) -> dict[str, Any]:
        binary_name = f"audio_bank_{score.bank_id:02d}.bin"
        binary_outputs[binary_name] = score.binary
        return {
            "title": score.title,
            "payload_status": "composed_validated",
            "payload_bytes": len(score.payload),
            "descriptor_bytes": BANK_DESCRIPTOR_BYTES,
            "payload_max_bytes": MAX_MUSIC_PAYLOAD_BYTES,
            "compiled_bytes": len(score.binary),
            "compiled_file_proposed": f"assets/generated/audio/{binary_name}",
            "compiled_sha256": hashlib.sha256(score.binary).hexdigest(),
            "payload_crc32": f"{score.crc32:08x}",
            "rows": BANK_ROWS,
            "ticks_per_row": BANK_TICKS_PER_ROW,
            "note_events": score.note_events,
            "maximum_row_events": score.maximum_row_events,
            "maximum_row_ym_writes": score.maximum_row_ym_writes,
            "maximum_row_psg_writes": score.maximum_row_psg_writes,
            "event_limit_per_tick": MAX_MUSIC_EVENTS_PER_TICK,
            "max_note_duration_ticks": BANK_TICKS_PER_ROW,
            "note_off_contract": "every row begins with bounded voice key-off",
        }

    for bank_id, music_id in enumerate(validated.music_ids, 1):
        encounter_ids = _music_sources(validated, music_id)
        first = next(entry for entry in ROSTER if entry.encounter_id == encounter_ids[0])
        sector = SECTOR_BY_ID[first.sector]
        for encounter_id in encounter_ids:
            roster = next(entry for entry in ROSTER if entry.encounter_id == encounter_id)
            if roster.sector != first.sector:
                _error(
                    f"banks.{music_id}",
                    "one score bank cannot span sectors with different tempo contracts",
                )
        score = score_by_id[bank_id]
        bank_id_by_music[music_id] = bank_id
        banks.append(
            {
                "bank_id": bank_id,
                "bank_key": score.bank_key,
                "kind": "encounter_music",
                "music_id": music_id,
                "source_encounters": encounter_ids,
                "sector": first.sector,
                "tempo_policy": "fixed",
                "tempo_bpm": sector["tempo_bpm"],
                "echo_cap": sector["echo_cap"],
                "layer_cap_mask": _layer_cap_mask(sector["echo_cap"]),
                **common_score_fields(score),
            }
        )

    result_bank_id = len(banks) + 1
    result_score = score_by_id[result_bank_id]
    banks.append(
        {
            "bank_id": result_bank_id,
            "bank_key": result_score.bank_key,
            "kind": "result_cues",
            "music_id": None,
            "source_encounters": [],
            "sector": None,
            "tempo_policy": "inherit_previous",
            "tempo_bpm": None,
            "echo_cap": MAX_ECHO_COUNT,
            "layer_cap_mask": MAX_LAYER_MASK,
            **common_score_fields(result_score),
        }
    )
    final_bank_id = len(banks) + 1
    final_score = score_by_id[final_bank_id]
    banks.append(
        {
            "bank_id": final_bank_id,
            "bank_key": final_score.bank_key,
            "kind": "final_cues",
            "music_id": None,
            "source_encounters": ["I04"],
            "sector": "INTERRUPT",
            "tempo_policy": "fixed",
            "tempo_bpm": 160,
            "echo_cap": MAX_ECHO_COUNT,
            "layer_cap_mask": MAX_LAYER_MASK,
            **common_score_fields(final_score),
        }
    )
    bank_id_by_music["__RESULTS__"] = result_bank_id
    bank_id_by_music["__FINAL__"] = final_bank_id
    return banks, bank_id_by_music, binary_outputs


def _scene(
    scene_id: int,
    scene_key: str,
    category: str,
    bank_id: int,
    *,
    music_id: str | None,
    sector: str | None,
    tempo_policy: str,
    tempo_bpm: int | None,
    echo_cap: int,
    scene_quantum_ticks: int,
    variant: str | None = None,
    tempo_fallback_bpm: int | None = None,
) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "scene_key": scene_key,
        "category": category,
        "bank_id": bank_id,
        "music_id": music_id,
        "sector": sector,
        "tempo_policy": tempo_policy,
        "tempo_bpm": tempo_bpm,
        "tempo_fallback_bpm": tempo_fallback_bpm,
        "echo_cap": echo_cap,
        "layer_cap_mask": _layer_cap_mask(echo_cap),
        "scene_quantum_ticks": scene_quantum_ticks,
        "layer_quantum_ticks": ROW_TICKS,
        "variant": variant,
    }


def _build_registry(
    validated: ValidatedAudio,
    bank_id_by_music: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, int]]:
    scenes: list[dict[str, Any]] = [
        {
            "scene_id": 0,
            "scene_key": "NONE",
            "category": "system",
            "bank_id": 0,
            "music_id": None,
            "sector": None,
            "tempo_policy": "stopped",
            "tempo_bpm": None,
            "tempo_fallback_bpm": None,
            "echo_cap": 0,
            "layer_cap_mask": 0,
            "scene_quantum_ticks": 1,
            "layer_quantum_ticks": 1,
            "variant": None,
        }
    ]
    scene_id_by_music: dict[str, int] = {}

    for music_id in validated.music_ids:
        sources = _music_sources(validated, music_id)
        first = next(entry for entry in ROSTER if entry.encounter_id == sources[0])
        contract = SECTOR_BY_ID[first.sector]
        scene_id = len(scenes)
        scene_id_by_music[music_id] = scene_id
        scenes.append(
            _scene(
                scene_id,
                music_id,
                "encounter_track",
                bank_id_by_music[music_id],
                music_id=music_id,
                sector=first.sector,
                tempo_policy="fixed",
                tempo_bpm=contract["tempo_bpm"],
                tempo_fallback_bpm=contract["tempo_bpm"],
                echo_cap=contract["echo_cap"],
                scene_quantum_ticks=BAR_TICKS,
            )
        )

    phase_scene_ids: dict[str, int] = {}
    for encounter_id, phase_id in validated.phase_ids:
        roster = next(entry for entry in ROSTER if entry.encounter_id == encounter_id)
        music_id = EXPECTED_MUSIC_BY_ENCOUNTER[encounter_id]
        contract = SECTOR_BY_ID[roster.sector]
        scene_id = len(scenes)
        phase_scene_ids[phase_id] = scene_id
        scenes.append(
            _scene(
                scene_id,
                phase_id,
                "boss_phase",
                bank_id_by_music[music_id],
                music_id=music_id,
                sector=roster.sector,
                tempo_policy="fixed",
                tempo_bpm=contract["tempo_bpm"],
                tempo_fallback_bpm=contract["tempo_bpm"],
                echo_cap=contract["echo_cap"],
                scene_quantum_ticks=ROW_TICKS,
                variant=phase_id,
            )
        )

    special_scene_ids: dict[str, int] = {}
    for scene_key, rank in RESULT_SCENES:
        scene_id = len(scenes)
        special_scene_ids[scene_key] = scene_id
        scenes.append(
            _scene(
                scene_id,
                scene_key,
                "stage_result",
                bank_id_by_music["__RESULTS__"],
                music_id=None,
                sector=None,
                tempo_policy="inherit_previous",
                tempo_bpm=None,
                tempo_fallback_bpm=120,
                echo_cap=MAX_ECHO_COUNT,
                scene_quantum_ticks=ROW_TICKS,
                variant=rank,
            )
        )
    for scene_key, message in FINAL_SCENES:
        scene_id = len(scenes)
        special_scene_ids[scene_key] = scene_id
        scenes.append(
            _scene(
                scene_id,
                scene_key,
                "final",
                bank_id_by_music["__FINAL__"],
                music_id=None,
                sector="INTERRUPT",
                tempo_policy="fixed",
                tempo_bpm=160,
                tempo_fallback_bpm=160,
                echo_cap=MAX_ECHO_COUNT,
                scene_quantum_ticks=ROW_TICKS,
                variant=message,
            )
        )

    if len(scenes) > MAX_SCENE_COUNT:
        _error("scenes", f"{len(scenes)} exceeds limit {MAX_SCENE_COUNT}")
    if scenes[-1]["scene_id"] > MAX_SCENE_ID:
        _error("scenes", f"last ID exceeds wire maximum {MAX_SCENE_ID}")
    if len(scenes) != len(SCENE_START_ROWS):
        _error("scenes", "pattern-start-row table size drifted")
    for scene, start_row in zip(scenes, SCENE_START_ROWS, strict=True):
        if not 0 <= start_row < BANK_ROWS:
            _error("scenes", f"invalid pattern start row {start_row}")
        scene["pattern_start_row"] = start_row

    encounter_entries: list[dict[str, Any]] = []
    for roster, encounter in zip(ROSTER, validated.encounters, strict=True):
        music_id = encounter["presentation"]["music_id"]
        phase_ids = [
            phase["phase_id"] for phase in encounter.get("boss_phases", [])
        ]
        contract = SECTOR_BY_ID[roster.sector]
        encounter_entries.append(
            {
                "order": roster.order,
                "encounter_id": roster.encounter_id,
                "kind": roster.kind,
                "sector": roster.sector,
                "music_id": music_id,
                "scene_id": scene_id_by_music[music_id],
                "bank_id": bank_id_by_music[music_id],
                "tempo_bpm": contract["tempo_bpm"],
                "echo_cap": contract["echo_cap"],
                "layer_cap_mask": _layer_cap_mask(contract["echo_cap"]),
                "boss_phase_scene_ids": [
                    phase_scene_ids[phase_id] for phase_id in phase_ids
                ],
            }
        )

    registry = {
        "schema": "recursion94.audio_scene_registry",
        "schema_version": FORMAT_VERSION,
        "source": {
            "campaign": "content/campaign.json",
            "score": SCORE_SOURCE_RELATIVE.as_posix(),
            "encounter_manifests": len(validated.encounters),
            "content_source_sha256": validated.content_source_sha256,
            "score_source_sha256": validated.score_source_sha256,
            "audio_source_sha256": validated.audio_source_sha256,
        },
        "limits": {
            "scene_count_max": MAX_SCENE_COUNT,
            "scene_id_max": MAX_SCENE_ID,
            "echo_count_max": MAX_ECHO_COUNT,
            "layer_mask_max": MAX_LAYER_MASK,
            "ppqn": PPQN,
            "row_ticks": ROW_TICKS,
            "bar_ticks": BAR_TICKS,
            "bank_header_bytes": BANK_DESCRIPTOR_BYTES,
            "bank_rows": BANK_ROWS,
            "bank_layer_count": BANK_LAYER_COUNT,
            "compiled_bank_bytes": BANK_COMPILED_BYTES,
            "sync_state_command_bytes": COMMAND_BYTES,
            "ordinary_ring_entries": COMMAND_RING_ENTRIES,
            "ordinary_service_per_vint": COMMAND_SERVICE_PER_VINT,
            "state_commands_per_display_frame": MAX_STATE_COMMANDS_PER_DISPLAY_FRAME,
            "sfx_inputs_per_display_frame": MAX_SFX_INPUTS_PER_DISPLAY_FRAME,
            "sfx_commands_per_display_frame": MAX_SFX_COMMANDS_PER_DISPLAY_FRAME,
            "ordinary_commands_per_display_frame": MAX_ORDINARY_COMMANDS_PER_DISPLAY_FRAME,
        },
        "wire_contract": {
            "proposed_opcodes": {
                "sfx": 3,
                "sync_state": 9,
            },
            "sync_state": [
                "opcode",
                "scene_id",
                "layer_mask_bits_0_6_or_muted_bit_7",
                "generation",
            ],
            "one_state_generation_in_flight": True,
            "requested_ack_is_distinct_from_musically_applied_state": True,
            "layer_mask_is_authoritative_snapshot_not_echo_count": True,
            "non_cumulative_layer_masks_allowed": True,
            "sector_cap_checked_by_both_cpus": True,
            "layer_removal_is_immediate": True,
            "layer_acquisition_is_quantized": True,
            "mute_is_immediate_and_phase_continues": True,
            "audio_state_is_presentation_only": True,
            "gameplay_may_read_z80_timing": False,
            "mute_or_bank_failure_may_block_progression": False,
            "terminal_transitions_also_use_retried_scene_state": True,
        },
        "voice_contracts": [
            {
                "layer_bit": layer_bit,
                "role": role,
                "chip": chip,
                "voice": voice,
                "finite_note_max_ticks": MAX_NOTE_DURATION_TICKS,
            }
            for layer_bit, role, chip, voice in VOICE_CONTRACTS
        ],
        "sfx_contract": {
            "voice": "ym_ch5",
            "priority_names": [
                "cosmetic",
                "routine",
                "critical",
                "terminal",
            ],
            "cues": [
                {
                    "sfx_id": sfx_id,
                    "sfx_key": sfx_key,
                    "priority": priority,
                    "merge_policy": merge_policy,
                }
                for sfx_id, sfx_key, priority, merge_policy in SFX_CONTRACTS
            ],
        },
        "sector_contracts": [
            {
                "order": order,
                "sector": sector_id,
                "tempo_bpm": tempo,
                "echo_cap": cap,
                "layer_cap_mask": _layer_cap_mask(cap),
                "encounters": list(encounters),
                "boss": boss,
            }
            for order, (
                sector_id,
                tempo,
                cap,
                encounters,
                boss,
            ) in enumerate(SECTOR_CONTRACTS, 1)
        ],
        "music_ids": list(validated.music_ids),
        "encounters": encounter_entries,
        "scenes": scenes,
        "counts": {
            "scenes_including_none": len(scenes),
            "encounter_track_scenes": len(validated.music_ids),
            "boss_phase_scenes": len(validated.phase_ids),
            "stage_result_scenes": len(RESULT_SCENES),
            "final_scenes": len(FINAL_SCENES),
        },
    }
    ids = {**scene_id_by_music, **phase_scene_ids, **special_scene_ids}
    ids["NONE"] = 0
    return registry, ids


def _build_bank_manifest(
    validated: ValidatedAudio,
    banks: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    regions = []
    for name, start, end, purpose in Z80_LAYOUT:
        regions.append(
            {
                "name": name,
                "start": start,
                "end": end,
                "bytes": end - start + 1,
                "purpose": purpose,
            }
        )
    for bank in banks:
        payload_bytes = bank["payload_bytes"]
        maximum = bank["payload_max_bytes"]
        if not _is_int(payload_bytes) or not 0 <= payload_bytes <= maximum:
            _error(
                f"banks.{bank['bank_key']}.payload_bytes",
                f"outside 0..{maximum}",
            )
        if bank["max_note_duration_ticks"] > MAX_NOTE_DURATION_TICKS:
            _error(
                f"banks.{bank['bank_key']}.max_note_duration_ticks",
                "exceeds finite note lifetime contract",
            )
    return {
        "schema": "recursion94.audio_bank_manifest",
        "schema_version": FORMAT_VERSION,
        "source": {
            "score": SCORE_SOURCE_RELATIVE.as_posix(),
            "audio_source_sha256": validated.audio_source_sha256,
            "content_source_sha256": validated.content_source_sha256,
            "score_source_sha256": validated.score_source_sha256,
        },
        "status": {
            "metadata_complete": True,
            "score_payloads_composed": True,
            "score_payloads_validated": True,
            "compiled_binaries_emitted_to_runtime_tree": False,
            "runtime_integrated": False,
            "note": (
                "compiled bank bytes are validated in memory; later integration "
                "must emit them under assets/generated/audio"
            ),
        },
        "z80_layout": {
            "address_space_bytes": 0x2000,
            "regions": regions,
        },
        "budgets": {
            "driver_code_max_bytes": MAX_Z80_CODE_BYTES,
            "active_music_bank_max_bytes": MAX_MUSIC_BANK_BYTES,
            "bank_descriptor_bytes": BANK_DESCRIPTOR_BYTES,
            "music_payload_max_bytes": MAX_MUSIC_PAYLOAD_BYTES,
            "compiled_bank_bytes": BANK_COMPILED_BYTES,
            "active_sfx_tables_max_bytes": MAX_SFX_TABLE_BYTES,
            "music_events_per_tick_max": MAX_MUSIC_EVENTS_PER_TICK,
            "ym_writes_per_vint_max": MAX_YM_WRITES_PER_VINT,
            "psg_writes_per_vint_max": MAX_PSG_WRITES_PER_VINT,
            "note_duration_ticks_max": BANK_TICKS_PER_ROW,
            "bank_upload_only_outside_gameplay_and_interrupts": True,
        },
        "bank_load_contract": {
            "pause_and_all_notes_off_before_bus_request": True,
            "bounded_bus_wait_required": True,
            "descriptor_bytes": BANK_DESCRIPTOR_BYTES,
            "descriptor_fields": [
                "magic",
                "version",
                "bank_id",
                "tempo_bpm",
                "echo_cap",
                "rows",
                "ticks_per_row",
                "layer_count",
                "flags",
                "crc32",
            ],
            "checksum": "CRC-32/ISO-HDLC over payload bytes",
            "invalid_or_timeout_fallback": "muted_playable",
            "z80_banked_rom_reads_during_gameplay": False,
        },
        "banks": list(banks),
        "counts": {
            "encounter_music_banks": len(validated.music_ids),
            "system_banks": len(banks) - len(validated.music_ids),
            "total_banks": len(banks),
        },
    }


def build_artifacts(content_root: Path | str) -> AudioArtifactBundle:
    """Build every deterministic audio metadata output in memory."""

    validated = load_and_validate(content_root)
    banks, bank_id_by_music, bank_binaries = _build_banks(validated)
    registry, _ids = _build_registry(validated, bank_id_by_music)
    bank_manifest = _build_bank_manifest(validated, banks)

    registry_bytes = _pretty_json(registry)
    banks_bytes = _pretty_json(bank_manifest)
    registry_sha = hashlib.sha256(registry_bytes).hexdigest()
    banks_sha = hashlib.sha256(banks_bytes).hexdigest()
    checksums = (
        f"{registry_sha}  {AUDIO_REGISTRY_OUTPUT}\n"
        f"{banks_sha}  {AUDIO_BANKS_OUTPUT}\n"
    ).encode("ascii")
    files = {
        AUDIO_REGISTRY_OUTPUT: registry_bytes,
        AUDIO_BANKS_OUTPUT: banks_bytes,
        AUDIO_CHECKSUMS_OUTPUT: checksums,
    }
    return AudioArtifactBundle(
        files=files,
        bank_binaries=bank_binaries,
        source_sha256=validated.audio_source_sha256,
        registry_sha256=registry_sha,
        banks_sha256=banks_sha,
        scene_count=len(registry["scenes"]),
        bank_count=len(banks),
    )


def build_bank_binaries(content_root: Path | str) -> Mapping[str, bytes]:
    """Compile score banks in memory for standalone tests/future integration."""

    return build_artifacts(content_root).bank_binaries


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def compile_audio(
    content_root: Path | str,
    output_dir: Path | str,
) -> AudioArtifactBundle:
    """Validate and atomically write the audio metadata artifacts."""

    bundle = build_artifacts(content_root)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    unexpected = sorted(
        path.name
        for path in destination.iterdir()
        if path.name not in OUTPUT_NAMES or not path.is_file()
    )
    if unexpected:
        _error(str(destination), f"unexpected generated entries {unexpected}")
    for name in sorted(bundle.files):
        _write_atomic(destination / name, bundle.files[name])
    return bundle


def check_generated(
    content_root: Path | str,
    output_dir: Path | str,
) -> AudioArtifactBundle:
    """Require generated files to be byte-identical to a fresh compile."""

    bundle = build_artifacts(content_root)
    destination = Path(output_dir)
    if not destination.is_dir():
        _error(str(destination), "generated output directory does not exist")
    actual_names = {path.name for path in destination.iterdir()}
    if actual_names != OUTPUT_NAMES:
        _error(
            str(destination),
            f"generated file set mismatch; expected={sorted(OUTPUT_NAMES)}, "
            f"actual={sorted(actual_names)}",
        )
    for name, expected in bundle.files.items():
        actual = (destination / name).read_bytes()
        if actual != expected:
            _error(str(destination / name), "generated bytes are stale or corrupted")
    return bundle


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile deterministic RECURSION 94 audio metadata"
    )
    parser.add_argument("--content-root", type=Path, default=Path("content"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/generated/audio"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="require generated files to match a fresh compile",
    )
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="validate and compile in memory without writing",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.check:
            bundle = check_generated(args.content_root, args.output_dir)
            action = "checked"
        elif args.validate_only:
            bundle = build_artifacts(args.content_root)
            action = "validated"
        else:
            bundle = compile_audio(args.content_root, args.output_dir)
            action = "generated"
    except AudioCompileError as exc:
        print(f"audio compiler: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "audio compiler: PASS: "
        f"{action} {bundle.scene_count} scenes/{bundle.bank_count} banks; "
        f"source_sha256={bundle.source_sha256}; "
        f"registry_sha256={bundle.registry_sha256}; "
        f"banks_sha256={bundle.banks_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
