#!/usr/bin/env python3
"""Strict, deterministic Sega Genesis art compiler for RECURSION 94.

Only the Python standard library is used. The compiler consumes repository
owned JSON pixel matrices and emits CRAM words, 4bpp tiles, VDP tile maps,
hardware-sprite mappings, animation metadata, an aggregate container, a vasm
include, checksums, and a machine-readable budget/provenance report.
"""

from __future__ import annotations

import argparse
import collections
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
from typing import Any, Iterable, Mapping, Sequence


FORMAT_VERSION = 1
COMPILER_VERSION = "1.0.0"

OUTPUT_BUNDLE = "recursion94_art.bin"
OUTPUT_PALETTES = "recursion94_art_palettes.bin"
OUTPUT_TILES = "recursion94_art_tiles.bin"
OUTPUT_TILEMAPS = "recursion94_art_tilemaps.bin"
OUTPUT_SPRITES = "recursion94_art_sprites.bin"
OUTPUT_ANIMATIONS = "recursion94_art_animations.bin"
OUTPUT_ASSEMBLY = "recursion94_art.inc.asm"
OUTPUT_REPORT = "recursion94_art_report.json"
OUTPUT_CHECKSUMS = "recursion94_art.sha256"
OUTPUT_NAMES = frozenset(
    (
        OUTPUT_BUNDLE,
        OUTPUT_PALETTES,
        OUTPUT_TILES,
        OUTPUT_TILEMAPS,
        OUTPUT_SPRITES,
        OUTPUT_ANIMATIONS,
        OUTPUT_ASSEMBLY,
        OUTPUT_REPORT,
        OUTPUT_CHECKSUMS,
    )
)

TRANSPARENT = None
RGB = tuple[int, int, int]
Pixel = RGB | None

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,14}$")
SYMBOL_RE = re.compile(r"^[!-~]$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
SAFE_PATH_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

MANIFEST_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "product",
        "identity",
        "art_direction",
        "provenance",
        "palette_file",
        "asset_files",
        "map_files",
        "budgets",
    )
)
PROVENANCE_KEYS = frozenset(
    ("origin", "creator", "method", "rights", "external_sources")
)
BUDGET_KEYS = frozenset(
    (
        "rom_bytes_max",
        "vram_tile_bytes_max",
        "vram_map_bytes_max",
        "cram_words_max",
        "unique_tiles_max",
        "sprite_pieces_per_frame_max",
        "sprite_frames_max",
        "animations_max",
    )
)
PALETTE_FILE_KEYS = frozenset(("schema", "schema_version", "palettes"))
PALETTE_KEYS = frozenset(("id", "slot", "mode", "symbols"))
ASSET_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "asset_id",
        "kind",
        "palette",
        "provenance_id",
        "frames",
        "animations",
    )
)
FRAME_KEYS = frozenset(
    (
        "id",
        "width",
        "height",
        "origin_x",
        "origin_y",
        "priority",
        "pixels",
    )
)
ANIMATION_KEYS = frozenset(("id", "loop", "steps"))
ANIMATION_STEP_KEYS = frozenset(("frame", "ticks"))
MAP_KEYS = frozenset(
    (
        "schema",
        "schema_version",
        "map_id",
        "width_tiles",
        "height_tiles",
        "provenance_id",
        "operations",
    )
)
COMMON_OPERATION_KEYS = frozenset(
    ("op", "palette", "priority", "hflip", "vflip")
)
OPERATION_KEYS = {
    "fill": COMMON_OPERATION_KEYS | {"tile"},
    "rect": COMMON_OPERATION_KEYS | {"tile", "x", "y", "width", "height"},
    "border": COMMON_OPERATION_KEYS | {"tile", "x", "y", "width", "height"},
    "cell": COMMON_OPERATION_KEYS | {"tile", "x", "y"},
    "stamp_grid": COMMON_OPERATION_KEYS
    | {"tile", "x", "y", "columns", "rows", "step_x", "step_y"},
    "checker": COMMON_OPERATION_KEYS
    | {"tile_a", "tile_b", "x", "y", "width", "height"},
}

LOOP_CODES = {"once": 0, "loop": 1, "pingpong": 2}
ASSET_KINDS = frozenset(("font", "ui", "tileset", "sprite"))
PALETTE_MODES = frozenset(("exact", "reduce"))
SECTION_TYPES = (
    (b"CRAM", OUTPUT_PALETTES),
    (b"TILE", OUTPUT_TILES),
    (b"TMAP", OUTPUT_TILEMAPS),
    (b"SPRT", OUTPUT_SPRITES),
    (b"ANIM", OUTPUT_ANIMATIONS),
)


class AssetCompileError(RuntimeError):
    """Base error for a failed asset compilation."""


class AssetValidationError(AssetCompileError):
    """A source asset violates the fail-closed contract."""


@dataclass(frozen=True)
class PaletteSource:
    palette_id: str
    slot: int
    mode: str
    symbols: tuple[tuple[str, RGB], ...]


@dataclass(frozen=True)
class CompiledPalette:
    palette_id: str
    slot: int
    mode: str
    source_colors: tuple[RGB, ...]
    words: tuple[int, ...]
    display_colors: tuple[RGB, ...]
    color_to_index: Mapping[RGB, int]


@dataclass(frozen=True)
class RawFrame:
    frame_id: str
    asset_id: str
    kind: str
    palette_id: str
    width: int
    height: int
    origin_x: int
    origin_y: int
    priority: bool
    pixels: tuple[Pixel, ...]
    source_file: str


@dataclass(frozen=True)
class RawAnimation:
    animation_id: str
    loop: str
    steps: tuple[tuple[str, int], ...]
    source_file: str


@dataclass(frozen=True)
class TileReference:
    index: int
    hflip: bool
    vflip: bool


@dataclass(frozen=True)
class CompiledFrame:
    raw: RawFrame
    cells_wide: int
    cells_high: int
    cells: tuple[TileReference, ...]
    blank_cells: tuple[bool, ...]


@dataclass(frozen=True)
class CompiledMap:
    map_id: str
    width: int
    height: int
    entries: tuple[int, ...]
    source_file: str


@dataclass(frozen=True)
class SpritePiece:
    x: int
    y: int
    attribute: int


@dataclass(frozen=True)
class CompiledSpriteFrame:
    frame_id: str
    width: int
    height: int
    pieces: tuple[SpritePiece, ...]
    source_file: str


@dataclass(frozen=True)
class CompiledAnimation:
    animation_id: str
    loop_code: int
    steps: tuple[tuple[int, int], ...]
    source_file: str


@dataclass(frozen=True)
class BuildBundle:
    files: Mapping[str, bytes]
    source_sha256: str
    bundle_sha256: str
    report: Mapping[str, Any]


def _duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssetValidationError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise AssetValidationError(f"non-finite number {value!r} is not permitted")


def _load_json(path: Path) -> Any:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AssetValidationError(f"cannot read {path}: {exc}") from exc
    if data.startswith(b"\xef\xbb\xbf"):
        raise AssetValidationError(f"{path}: UTF-8 BOM is not permitted")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssetValidationError(f"{path}: source is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_object,
            parse_constant=_reject_constant,
        )
    except AssetValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise AssetValidationError(
            f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssetValidationError(f"{context}: expected object")
    return value


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise AssetValidationError(f"{context}: expected array")
    return value


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise AssetValidationError(f"{context}: expected string")
    return value


def _boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise AssetValidationError(f"{context}: expected Boolean")
    return value


def _integer(
    value: Any,
    context: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssetValidationError(f"{context}: expected integer")
    if not minimum <= value <= maximum:
        raise AssetValidationError(
            f"{context}: {value} outside {minimum}..{maximum}"
        )
    return value


def _keys(value: Mapping[str, Any], allowed: frozenset[str], context: str) -> None:
    actual = frozenset(value)
    missing = allowed - actual
    unknown = actual - allowed
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing keys {sorted(missing)!r}")
        if unknown:
            details.append(f"unknown keys {sorted(unknown)!r}")
        raise AssetValidationError(f"{context}: {'; '.join(details)}")


def _identifier(value: Any, context: str) -> str:
    result = _string(value, context)
    if not ID_RE.fullmatch(result):
        raise AssetValidationError(
            f"{context}: ID must match {ID_RE.pattern!r}, got {result!r}"
        )
    return result


def _relative_json_path(value: Any, context: str) -> str:
    result = _string(value, context)
    if "\\" in result or result.startswith("/") or ":" in result:
        raise AssetValidationError(f"{context}: unsafe relative path {result!r}")
    parts = result.split("/")
    if (
        not parts
        or any(part in ("", ".", "..") for part in parts)
        or any(not SAFE_PATH_PART_RE.fullmatch(part) for part in parts)
        or not result.endswith(".json")
    ):
        raise AssetValidationError(f"{context}: unsafe relative path {result!r}")
    return result


def _parse_rgb(value: Any, context: str) -> RGB:
    text = _string(value, context)
    if not COLOR_RE.fullmatch(text):
        raise AssetValidationError(
            f"{context}: color must be #RRGGBB, got {text!r}"
        )
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def _rgb_text(color: RGB) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def genesis_color_word(rgb: RGB) -> int:
    """Convert an RGB888 color to a Genesis `0000 BBB0 GGG0 RRR0` word."""

    if (
        not isinstance(rgb, tuple)
        or len(rgb) != 3
        or any(isinstance(channel, bool) or not isinstance(channel, int) for channel in rgb)
        or any(channel < 0 or channel > 255 for channel in rgb)
    ):
        raise ValueError("RGB value must be a three-integer tuple in 0..255")
    red, green, blue = ((channel * 7 + 127) // 255 for channel in rgb)
    return (blue << 9) | (green << 5) | (red << 1)


def _genesis_word_rgb(word: int) -> RGB:
    red = (word >> 1) & 7
    green = (word >> 5) & 7
    blue = (word >> 9) & 7
    return tuple((channel * 255 + 3) // 7 for channel in (red, green, blue))  # type: ignore[return-value]


def _color_distance(left: RGB, right: RGB) -> int:
    # Green receives a mild perceptual weight while keeping integer behavior.
    return (
        2 * (left[0] - right[0]) ** 2
        + 3 * (left[1] - right[1]) ** 2
        + (left[2] - right[2]) ** 2
    )


def reduce_palette(
    colors: Sequence[RGB] | Mapping[RGB, int],
    maximum_colors: int = 15,
) -> tuple[RGB, ...]:
    """Deterministic weighted median-cut reduction.

    The result is sorted by integer luma and RGB, independent of mapping order.
    Duplicate input colors are weighted; invalid colors and weights fail.
    """

    if isinstance(maximum_colors, bool) or not isinstance(maximum_colors, int):
        raise ValueError("maximum_colors must be an integer")
    if not 1 <= maximum_colors <= 15:
        raise ValueError("maximum_colors must be in 1..15")
    histogram: collections.Counter[RGB] = collections.Counter()
    items: Iterable[tuple[RGB, int]]
    if isinstance(colors, Mapping):
        items = colors.items()
    else:
        items = ((color, 1) for color in colors)
    for color, weight in items:
        # genesis_color_word performs complete RGB shape/range validation.
        genesis_color_word(color)
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            raise ValueError("palette weights must be positive integers")
        histogram[color] += weight
    if not histogram:
        raise ValueError("at least one opaque color is required")

    boxes: list[tuple[RGB, ...]] = [tuple(sorted(histogram))]
    while len(boxes) < maximum_colors:
        candidates: list[tuple[tuple[int, int, int, tuple[RGB, ...]], int]] = []
        for index, box in enumerate(boxes):
            if len(box) < 2:
                continue
            ranges = tuple(
                max(color[channel] for color in box)
                - min(color[channel] for color in box)
                for channel in range(3)
            )
            candidates.append(
                (
                    (
                        max(ranges),
                        sum(histogram[color] for color in box),
                        len(box),
                        box,
                    ),
                    index,
                )
            )
        if not candidates:
            break
        _, chosen_index = max(candidates)
        chosen = boxes.pop(chosen_index)
        ranges = tuple(
            max(color[channel] for color in chosen)
            - min(color[channel] for color in chosen)
            for channel in range(3)
        )
        channel = max(range(3), key=lambda item: (ranges[item], -item))
        ordered = tuple(
            sorted(
                chosen,
                key=lambda color: (
                    color[channel],
                    color[(channel + 1) % 3],
                    color[(channel + 2) % 3],
                ),
            )
        )
        total = sum(histogram[color] for color in ordered)
        running = 0
        split = 1
        for split in range(1, len(ordered)):
            running += histogram[ordered[split - 1]]
            if running * 2 >= total:
                break
        boxes.append(ordered[:split])
        boxes.append(ordered[split:])

    reduced: set[RGB] = set()
    for box in boxes:
        total = sum(histogram[color] for color in box)
        reduced.add(
            tuple(
                (
                    sum(color[channel] * histogram[color] for color in box)
                    + total // 2
                )
                // total
                for channel in range(3)
            )  # type: ignore[arg-type]
        )
    return tuple(
        sorted(
            reduced,
            key=lambda color: (
                54 * color[0] + 183 * color[1] + 19 * color[2],
                color,
            ),
        )
    )


def pack_tile_4bpp(indices: Sequence[int]) -> bytes:
    """Pack one 8×8, 0..15 indexed tile into Genesis 4bpp order."""

    if len(indices) != 64:
        raise ValueError(f"a tile must contain exactly 64 pixels, got {len(indices)}")
    packed = bytearray()
    for offset in range(0, 64, 2):
        left, right = indices[offset], indices[offset + 1]
        for value in (left, right):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 15
            ):
                raise ValueError("tile pixels must be integer palette indices 0..15")
        packed.append((left << 4) | right)
    return bytes(packed)


def _flip_tile(tile: bytes, horizontal: bool, vertical: bool) -> bytes:
    if len(tile) != 64:
        raise ValueError("unpacked tile must contain 64 pixels")
    result = bytearray(64)
    for y in range(8):
        for x in range(8):
            source_x = 7 - x if horizontal else x
            source_y = 7 - y if vertical else y
            result[y * 8 + x] = tile[source_y * 8 + source_x]
    return bytes(result)


class _TileDeduper:
    def __init__(self) -> None:
        self.tiles: list[bytes] = []
        self.lookup: dict[bytes, TileReference] = {}
        self.reuse = collections.Counter(
            {"exact": 0, "horizontal": 0, "vertical": 0, "both": 0}
        )
        self.add(bytes(64))

    def add(self, unpacked: bytes) -> TileReference:
        existing = self.lookup.get(unpacked)
        if existing is not None:
            category = (
                "both"
                if existing.hflip and existing.vflip
                else "horizontal"
                if existing.hflip
                else "vertical"
                if existing.vflip
                else "exact"
            )
            self.reuse[category] += 1
            return existing
        index = len(self.tiles)
        if index >= 2048:
            raise AssetValidationError("unique tile count exceeds VDP index capacity")
        self.tiles.append(unpacked)
        variants = (
            (False, False, unpacked),
            (True, False, _flip_tile(unpacked, True, False)),
            (False, True, _flip_tile(unpacked, False, True)),
            (True, True, _flip_tile(unpacked, True, True)),
        )
        for horizontal, vertical, variant in variants:
            self.lookup.setdefault(
                variant, TileReference(index, horizontal, vertical)
            )
        return TileReference(index, False, False)


def _schema(
    value: Mapping[str, Any],
    schema_name: str,
    context: str,
) -> None:
    if value.get("schema") != schema_name:
        raise AssetValidationError(
            f"{context}.schema: expected {schema_name!r}, got {value.get('schema')!r}"
        )
    if value.get("schema_version") != FORMAT_VERSION:
        raise AssetValidationError(
            f"{context}.schema_version: expected {FORMAT_VERSION}"
        )


def _parse_manifest(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _object(_load_json(path), str(path))
    _keys(manifest, MANIFEST_KEYS, str(path))
    _schema(manifest, "recursion94.art_manifest", str(path))
    if manifest["product"] != "RECURSION 94: The Last Cartridge":
        raise AssetValidationError(f"{path}.product: unexpected product identity")
    identity = _string(manifest["identity"], f"{path}.identity")
    if identity != "BOOT":
        raise AssetValidationError(f"{path}.identity: expected 'BOOT'")
    art_direction = _string(manifest["art_direction"], f"{path}.art_direction")
    if not 20 <= len(art_direction) <= 512:
        raise AssetValidationError(f"{path}.art_direction: expected 20..512 characters")

    provenance = _object(manifest["provenance"], f"{path}.provenance")
    _keys(provenance, PROVENANCE_KEYS, f"{path}.provenance")
    required_provenance = {
        "origin": "original",
        "creator": "RECURSION 94 project",
        "method": "repository-authored JSON pixel matrices",
        "rights": "Original project work; no third-party art incorporated",
    }
    for key, expected in required_provenance.items():
        if provenance[key] != expected:
            raise AssetValidationError(
                f"{path}.provenance.{key}: expected {expected!r}"
            )
    external = _array(
        provenance["external_sources"], f"{path}.provenance.external_sources"
    )
    if external:
        raise AssetValidationError(
            f"{path}.provenance.external_sources: foundational set must be original"
        )

    budgets = _object(manifest["budgets"], f"{path}.budgets")
    _keys(budgets, BUDGET_KEYS, f"{path}.budgets")
    budget_ranges = {
        "rom_bytes_max": (1024, 1_048_576),
        "vram_tile_bytes_max": (32, 65536),
        "vram_map_bytes_max": (2, 65536),
        "cram_words_max": (1, 64),
        "unique_tiles_max": (1, 2048),
        "sprite_pieces_per_frame_max": (1, 80),
        "sprite_frames_max": (1, 65535),
        "animations_max": (1, 65535),
    }
    parsed_budgets = {
        key: _integer(budgets[key], f"{path}.budgets.{key}", minimum, maximum)
        for key, (minimum, maximum) in budget_ranges.items()
    }
    palette_file = _relative_json_path(
        manifest["palette_file"], f"{path}.palette_file"
    )
    asset_files = [
        _relative_json_path(value, f"{path}.asset_files[{index}]")
        for index, value in enumerate(
            _array(manifest["asset_files"], f"{path}.asset_files")
        )
    ]
    map_files = [
        _relative_json_path(value, f"{path}.map_files[{index}]")
        for index, value in enumerate(
            _array(manifest["map_files"], f"{path}.map_files")
        )
    ]
    if not asset_files or not map_files:
        raise AssetValidationError(f"{path}: asset_files and map_files must be nonempty")
    for name, values in (("asset_files", asset_files), ("map_files", map_files)):
        if len(values) != len(set(values)):
            raise AssetValidationError(f"{path}.{name}: duplicate path")
    return manifest, {
        "palette_file": palette_file,
        "asset_files": tuple(asset_files),
        "map_files": tuple(map_files),
        "budgets": parsed_budgets,
        "provenance": provenance,
    }


def _validate_declared_files(
    art_root: Path,
    map_root: Path,
    palette_file: str,
    asset_files: Sequence[str],
    map_files: Sequence[str],
) -> None:
    expected_art = {"manifest.json", palette_file, *asset_files}
    actual_art = {
        path.relative_to(art_root).as_posix()
        for path in art_root.rglob("*.json")
        if path.is_file()
    }
    expected_maps = set(map_files)
    actual_maps = {
        path.relative_to(map_root).as_posix()
        for path in map_root.rglob("*.json")
        if path.is_file()
    }
    if actual_art != expected_art:
        raise AssetValidationError(
            "art JSON manifest set mismatch: "
            f"missing={sorted(expected_art - actual_art)!r}, "
            f"extra={sorted(actual_art - expected_art)!r}"
        )
    if actual_maps != expected_maps:
        raise AssetValidationError(
            "map JSON manifest set mismatch: "
            f"missing={sorted(expected_maps - actual_maps)!r}, "
            f"extra={sorted(actual_maps - expected_maps)!r}"
        )


def _parse_palettes(path: Path) -> tuple[PaletteSource, ...]:
    root = _object(_load_json(path), str(path))
    _keys(root, PALETTE_FILE_KEYS, str(path))
    _schema(root, "recursion94.palettes", str(path))
    result: list[PaletteSource] = []
    seen_ids: set[str] = set()
    seen_slots: set[int] = set()
    for index, raw_value in enumerate(_array(root["palettes"], f"{path}.palettes")):
        context = f"{path}.palettes[{index}]"
        value = _object(raw_value, context)
        _keys(value, PALETTE_KEYS, context)
        palette_id = _identifier(value["id"], f"{context}.id")
        slot = _integer(value["slot"], f"{context}.slot", 0, 3)
        mode = _string(value["mode"], f"{context}.mode")
        if mode not in PALETTE_MODES:
            raise AssetValidationError(
                f"{context}.mode: expected one of {sorted(PALETTE_MODES)!r}"
            )
        symbols_value = _object(value["symbols"], f"{context}.symbols")
        if not 1 <= len(symbols_value) <= 64:
            raise AssetValidationError(
                f"{context}.symbols: expected 1..64 opaque symbols"
            )
        symbols: list[tuple[str, RGB]] = []
        seen_colors: set[RGB] = set()
        # JSON object member order is deliberately non-semantic.
        for symbol, color_value in sorted(symbols_value.items()):
            if (
                not SYMBOL_RE.fullmatch(symbol)
                or symbol == "."
                or symbol.isspace()
            ):
                raise AssetValidationError(
                    f"{context}.symbols: invalid one-byte symbol {symbol!r}"
                )
            color = _parse_rgb(color_value, f"{context}.symbols[{symbol!r}]")
            if color in seen_colors:
                raise AssetValidationError(
                    f"{context}.symbols: duplicate RGB color {_rgb_text(color)}"
                )
            seen_colors.add(color)
            symbols.append((symbol, color))
        if mode == "exact" and len(symbols) > 15:
            raise AssetValidationError(
                f"{context}: exact palette has {len(symbols)} opaque colors; max is 15"
            )
        if palette_id in seen_ids:
            raise AssetValidationError(f"{context}: duplicate palette ID {palette_id!r}")
        if slot in seen_slots:
            raise AssetValidationError(f"{context}: duplicate palette slot {slot}")
        seen_ids.add(palette_id)
        seen_slots.add(slot)
        result.append(PaletteSource(palette_id, slot, mode, tuple(symbols)))
    if not result:
        raise AssetValidationError(f"{path}.palettes: at least one palette is required")
    return tuple(result)


def _parse_assets(
    art_root: Path,
    asset_files: Sequence[str],
    palettes: Sequence[PaletteSource],
) -> tuple[tuple[RawFrame, ...], tuple[RawAnimation, ...], tuple[tuple[str, Any], ...]]:
    palette_symbols = {
        palette.palette_id: dict(palette.symbols) for palette in palettes
    }
    frames: list[RawFrame] = []
    animations: list[RawAnimation] = []
    parsed: list[tuple[str, Any]] = []
    seen_assets: set[str] = set()
    seen_frames: set[str] = set()
    seen_animations: set[str] = set()
    for relative in asset_files:
        path = art_root / relative
        root = _object(_load_json(path), str(path))
        parsed.append((f"art/{relative}", root))
        _keys(root, ASSET_KEYS, str(path))
        _schema(root, "recursion94.pixel_asset", str(path))
        asset_id = _identifier(root["asset_id"], f"{path}.asset_id")
        if asset_id in seen_assets:
            raise AssetValidationError(f"{path}: duplicate asset ID {asset_id!r}")
        seen_assets.add(asset_id)
        kind = _string(root["kind"], f"{path}.kind")
        if kind not in ASSET_KINDS:
            raise AssetValidationError(
                f"{path}.kind: expected one of {sorted(ASSET_KINDS)!r}"
            )
        palette_id = _identifier(root["palette"], f"{path}.palette")
        if palette_id not in palette_symbols:
            raise AssetValidationError(f"{path}.palette: unknown palette {palette_id!r}")
        if root["provenance_id"] != "original_project":
            raise AssetValidationError(
                f"{path}.provenance_id: expected 'original_project'"
            )
        symbols = palette_symbols[palette_id]
        frame_values = _array(root["frames"], f"{path}.frames")
        if not frame_values:
            raise AssetValidationError(f"{path}.frames: must not be empty")
        local_frames: set[str] = set()
        for index, raw_frame in enumerate(frame_values):
            context = f"{path}.frames[{index}]"
            value = _object(raw_frame, context)
            _keys(value, FRAME_KEYS, context)
            frame_id = _identifier(value["id"], f"{context}.id")
            if frame_id in seen_frames:
                raise AssetValidationError(f"{context}: duplicate frame ID {frame_id!r}")
            seen_frames.add(frame_id)
            local_frames.add(frame_id)
            width = _integer(value["width"], f"{context}.width", 8, 64)
            height = _integer(value["height"], f"{context}.height", 8, 64)
            if width % 8 or height % 8:
                raise AssetValidationError(
                    f"{context}: dimensions must be multiples of eight"
                )
            origin_x = _integer(value["origin_x"], f"{context}.origin_x", 0, width)
            origin_y = _integer(value["origin_y"], f"{context}.origin_y", 0, height)
            priority = _boolean(value["priority"], f"{context}.priority")
            rows = _array(value["pixels"], f"{context}.pixels")
            if len(rows) != height:
                raise AssetValidationError(
                    f"{context}.pixels: expected {height} rows, got {len(rows)}"
                )
            pixels: list[Pixel] = []
            for y, raw_row in enumerate(rows):
                row = _string(raw_row, f"{context}.pixels[{y}]")
                if len(row) != width:
                    raise AssetValidationError(
                        f"{context}.pixels[{y}]: expected {width} symbols, "
                        f"got {len(row)}"
                    )
                for x, symbol in enumerate(row):
                    if symbol == ".":
                        pixels.append(TRANSPARENT)
                    elif symbol in symbols:
                        pixels.append(symbols[symbol])
                    else:
                        raise AssetValidationError(
                            f"{context}.pixels[{y}][{x}]: symbol {symbol!r} "
                            f"is not declared by palette {palette_id!r}"
                        )
            if all(pixel is None for pixel in pixels):
                # Blank tiles are useful, but a completely empty sprite is not.
                if kind == "sprite":
                    raise AssetValidationError(f"{context}: sprite frame is empty")
            frames.append(
                RawFrame(
                    frame_id,
                    asset_id,
                    kind,
                    palette_id,
                    width,
                    height,
                    origin_x,
                    origin_y,
                    priority,
                    tuple(pixels),
                    relative,
                )
            )

        animation_values = _array(root["animations"], f"{path}.animations")
        if kind != "sprite" and animation_values:
            raise AssetValidationError(
                f"{path}.animations: only sprite assets may declare animations"
            )
        for index, raw_animation in enumerate(animation_values):
            context = f"{path}.animations[{index}]"
            value = _object(raw_animation, context)
            _keys(value, ANIMATION_KEYS, context)
            animation_id = _identifier(value["id"], f"{context}.id")
            if animation_id in seen_animations:
                raise AssetValidationError(
                    f"{context}: duplicate animation ID {animation_id!r}"
                )
            seen_animations.add(animation_id)
            loop = _string(value["loop"], f"{context}.loop")
            if loop not in LOOP_CODES:
                raise AssetValidationError(
                    f"{context}.loop: expected one of {sorted(LOOP_CODES)!r}"
                )
            steps: list[tuple[str, int]] = []
            raw_steps = _array(value["steps"], f"{context}.steps")
            if not 1 <= len(raw_steps) <= 255:
                raise AssetValidationError(f"{context}.steps: expected 1..255 steps")
            total_ticks = 0
            for step_index, raw_step in enumerate(raw_steps):
                step_context = f"{context}.steps[{step_index}]"
                step = _object(raw_step, step_context)
                _keys(step, ANIMATION_STEP_KEYS, step_context)
                frame = _identifier(step["frame"], f"{step_context}.frame")
                if frame not in local_frames:
                    raise AssetValidationError(
                        f"{step_context}.frame: {frame!r} is not in asset {asset_id!r}"
                    )
                ticks = _integer(step["ticks"], f"{step_context}.ticks", 1, 255)
                total_ticks += ticks
                steps.append((frame, ticks))
            if total_ticks > 65535:
                raise AssetValidationError(
                    f"{context}: animation exceeds 65535 ticks"
                )
            animations.append(
                RawAnimation(animation_id, loop, tuple(steps), relative)
            )
    return tuple(frames), tuple(animations), tuple(parsed)


def _compile_palettes(
    sources: Sequence[PaletteSource],
    frames: Sequence[RawFrame],
) -> tuple[CompiledPalette, ...]:
    histograms: dict[str, collections.Counter[RGB]] = {
        source.palette_id: collections.Counter() for source in sources
    }
    for frame in frames:
        histograms[frame.palette_id].update(
            pixel for pixel in frame.pixels if pixel is not None
        )
    compiled: list[CompiledPalette] = []
    for source in sources:
        declared = tuple(color for _, color in source.symbols)
        histogram = histograms[source.palette_id]
        # Every declared source color participates at least once, so reduction
        # remains stable even when an authored color is temporarily unused.
        weighted = collections.Counter(histogram)
        for color in declared:
            weighted[color] += 1
        if source.mode == "exact":
            candidates = declared
        else:
            candidates = reduce_palette(weighted, 15)
        words: list[int] = []
        display: list[RGB] = []
        seen_words: set[int] = set()
        for candidate in candidates:
            word = genesis_color_word(candidate)
            if word in seen_words:
                if source.mode == "exact":
                    raise AssetValidationError(
                        f"palette {source.palette_id!r}: colors collapse to duplicate "
                        f"Genesis word ${word:04X}"
                    )
                continue
            seen_words.add(word)
            words.append(word)
            display.append(_genesis_word_rgb(word))
        if not words:
            raise AssetValidationError(
                f"palette {source.palette_id!r}: no opaque colors after conversion"
            )
        if len(words) > 15:
            raise AssetValidationError(
                f"palette {source.palette_id!r}: {len(words)} opaque Genesis colors"
            )
        mapping: dict[RGB, int] = {}
        for color in declared:
            nearest = min(
                range(len(display)),
                key=lambda index: (_color_distance(color, display[index]), index),
            )
            mapping[color] = nearest + 1
        compiled.append(
            CompiledPalette(
                source.palette_id,
                source.slot,
                source.mode,
                declared,
                tuple(words),
                tuple(display),
                mapping,
            )
        )
    return tuple(compiled)


def _compile_frames(
    frames: Sequence[RawFrame],
    palettes: Sequence[CompiledPalette],
) -> tuple[tuple[CompiledFrame, ...], _TileDeduper]:
    palette_by_id = {palette.palette_id: palette for palette in palettes}
    deduper = _TileDeduper()
    result: list[CompiledFrame] = []
    for frame in frames:
        palette = palette_by_id[frame.palette_id]
        indexed = tuple(
            0 if pixel is None else palette.color_to_index[pixel]
            for pixel in frame.pixels
        )
        cells_wide = frame.width // 8
        cells_high = frame.height // 8
        references: list[TileReference] = []
        blanks: list[bool] = []
        for cell_y in range(cells_high):
            for cell_x in range(cells_wide):
                tile_pixels = bytearray()
                for local_y in range(8):
                    row_start = (
                        (cell_y * 8 + local_y) * frame.width + cell_x * 8
                    )
                    tile_pixels.extend(indexed[row_start : row_start + 8])
                pixels = bytes(tile_pixels)
                references.append(deduper.add(pixels))
                blanks.append(not any(pixels))
        result.append(
            CompiledFrame(
                frame, cells_wide, cells_high, tuple(references), tuple(blanks)
            )
        )
    return tuple(result), deduper


def _tile_attribute(
    reference: TileReference,
    palette_slot: int,
    priority: bool,
    hflip: bool,
    vflip: bool,
) -> int:
    if not 0 <= reference.index <= 0x7FF:
        raise AssetValidationError(f"tile index {reference.index} exceeds 11 bits")
    return (
        reference.index
        | ((int(reference.hflip) ^ int(hflip)) << 11)
        | ((int(reference.vflip) ^ int(vflip)) << 12)
        | (palette_slot << 13)
        | (int(priority) << 15)
    )


def _operation_bool(
    operation: Mapping[str, Any],
    key: str,
    context: str,
) -> bool:
    return _boolean(operation[key], f"{context}.{key}")


def _parse_and_compile_maps(
    map_root: Path,
    map_files: Sequence[str],
    frames: Sequence[CompiledFrame],
    palettes: Sequence[CompiledPalette],
) -> tuple[tuple[CompiledMap, ...], tuple[tuple[str, Any], ...]]:
    frame_by_id = {frame.raw.frame_id: frame for frame in frames}
    palette_slots = {palette.palette_id: palette.slot for palette in palettes}
    result: list[CompiledMap] = []
    parsed: list[tuple[str, Any]] = []
    seen_ids: set[str] = set()

    def tile_reference(tile_value: Any, context: str) -> tuple[TileReference, int]:
        tile_id = _identifier(tile_value, context)
        frame = frame_by_id.get(tile_id)
        if frame is None:
            raise AssetValidationError(f"{context}: unknown frame {tile_id!r}")
        if frame.cells_wide != 1 or frame.cells_high != 1:
            raise AssetValidationError(
                f"{context}: frame {tile_id!r} is not a single 8x8 tile"
            )
        return frame.cells[0], palette_slots[frame.raw.palette_id]

    for relative in map_files:
        path = map_root / relative
        root = _object(_load_json(path), str(path))
        parsed.append((f"maps/{relative}", root))
        _keys(root, MAP_KEYS, str(path))
        _schema(root, "recursion94.tilemap", str(path))
        map_id = _identifier(root["map_id"], f"{path}.map_id")
        if map_id in seen_ids:
            raise AssetValidationError(f"{path}: duplicate map ID {map_id!r}")
        seen_ids.add(map_id)
        width = _integer(root["width_tiles"], f"{path}.width_tiles", 1, 128)
        height = _integer(root["height_tiles"], f"{path}.height_tiles", 1, 128)
        if width * height > 16384:
            raise AssetValidationError(f"{path}: map exceeds 16384 entries")
        if root["provenance_id"] != "original_project":
            raise AssetValidationError(
                f"{path}.provenance_id: expected 'original_project'"
            )
        operations = _array(root["operations"], f"{path}.operations")
        if not operations:
            raise AssetValidationError(f"{path}.operations: must not be empty")
        entries: list[int | None] = [None] * (width * height)

        def set_cell(
            x: int,
            y: int,
            reference: TileReference,
            source_palette: int,
            operation: Mapping[str, Any],
            context: str,
        ) -> None:
            if not 0 <= x < width or not 0 <= y < height:
                raise AssetValidationError(
                    f"{context}: cell ({x},{y}) outside {width}x{height}"
                )
            palette = _integer(
                operation["palette"], f"{context}.palette", 0, 3
            )
            # Explicit attributes are required, making palette intent visible
            # even when it equals the source asset's palette.
            if palette != source_palette:
                # Alternate banks are allowed because Genesis tile pixels are
                # palette-independent; report coverage makes this auditable.
                pass
            entries[y * width + x] = _tile_attribute(
                reference,
                palette,
                _operation_bool(operation, "priority", context),
                _operation_bool(operation, "hflip", context),
                _operation_bool(operation, "vflip", context),
            )

        for index, raw_operation in enumerate(operations):
            context = f"{path}.operations[{index}]"
            operation = _object(raw_operation, context)
            op = _string(operation.get("op"), f"{context}.op")
            allowed = OPERATION_KEYS.get(op)
            if allowed is None:
                raise AssetValidationError(
                    f"{context}.op: unknown operation {op!r}"
                )
            _keys(operation, frozenset(allowed), context)
            if op == "checker":
                first, first_palette = tile_reference(
                    operation["tile_a"], f"{context}.tile_a"
                )
                second, second_palette = tile_reference(
                    operation["tile_b"], f"{context}.tile_b"
                )
                x = _integer(operation["x"], f"{context}.x", 0, width - 1)
                y = _integer(operation["y"], f"{context}.y", 0, height - 1)
                area_width = _integer(
                    operation["width"], f"{context}.width", 1, width
                )
                area_height = _integer(
                    operation["height"], f"{context}.height", 1, height
                )
                if x + area_width > width or y + area_height > height:
                    raise AssetValidationError(f"{context}: checker exceeds map bounds")
                for local_y in range(area_height):
                    for local_x in range(area_width):
                        use_second = bool((local_x + local_y) & 1)
                        set_cell(
                            x + local_x,
                            y + local_y,
                            second if use_second else first,
                            second_palette if use_second else first_palette,
                            operation,
                            context,
                        )
                continue
            reference, source_palette = tile_reference(
                operation["tile"], f"{context}.tile"
            )
            if op == "fill":
                coordinates = (
                    (x, y) for y in range(height) for x in range(width)
                )
            elif op == "cell":
                coordinates = (
                    (
                        _integer(operation["x"], f"{context}.x", 0, width - 1),
                        _integer(operation["y"], f"{context}.y", 0, height - 1),
                    ),
                )
            elif op in ("rect", "border"):
                x = _integer(operation["x"], f"{context}.x", 0, width - 1)
                y = _integer(operation["y"], f"{context}.y", 0, height - 1)
                area_width = _integer(
                    operation["width"], f"{context}.width", 1, width
                )
                area_height = _integer(
                    operation["height"], f"{context}.height", 1, height
                )
                if x + area_width > width or y + area_height > height:
                    raise AssetValidationError(f"{context}: rectangle exceeds map bounds")
                if op == "rect":
                    coordinates = (
                        (x + dx, y + dy)
                        for dy in range(area_height)
                        for dx in range(area_width)
                    )
                else:
                    coordinate_list = []
                    for dx in range(area_width):
                        coordinate_list.append((x + dx, y))
                        if area_height > 1:
                            coordinate_list.append(
                                (x + dx, y + area_height - 1)
                            )
                    for dy in range(1, max(1, area_height - 1)):
                        coordinate_list.append((x, y + dy))
                        if area_width > 1:
                            coordinate_list.append(
                                (x + area_width - 1, y + dy)
                            )
                    coordinates = iter(coordinate_list)
            else:
                x = _integer(operation["x"], f"{context}.x", 0, width - 1)
                y = _integer(operation["y"], f"{context}.y", 0, height - 1)
                columns = _integer(
                    operation["columns"], f"{context}.columns", 1, width
                )
                rows = _integer(operation["rows"], f"{context}.rows", 1, height)
                step_x = _integer(
                    operation["step_x"], f"{context}.step_x", 1, width
                )
                step_y = _integer(
                    operation["step_y"], f"{context}.step_y", 1, height
                )
                if x + (columns - 1) * step_x >= width or y + (rows - 1) * step_y >= height:
                    raise AssetValidationError(f"{context}: stamp grid exceeds map bounds")
                coordinates = (
                    (x + column * step_x, y + row * step_y)
                    for row in range(rows)
                    for column in range(columns)
                )
            for cell_x, cell_y in coordinates:
                set_cell(
                    cell_x,
                    cell_y,
                    reference,
                    source_palette,
                    operation,
                    context,
                )
        if any(entry is None for entry in entries):
            first = entries.index(None)
            raise AssetValidationError(
                f"{path}: map cell ({first % width},{first // width}) was never initialized"
            )
        result.append(
            CompiledMap(
                map_id,
                width,
                height,
                tuple(entry for entry in entries if entry is not None),
                relative,
            )
        )
    return tuple(result), tuple(parsed)


def _compile_sprites(
    frames: Sequence[CompiledFrame],
    palettes: Sequence[CompiledPalette],
) -> tuple[CompiledSpriteFrame, ...]:
    slots = {palette.palette_id: palette.slot for palette in palettes}
    result: list[CompiledSpriteFrame] = []
    for frame in frames:
        if frame.raw.kind != "sprite":
            continue
        pieces: list[SpritePiece] = []
        for cell_y in range(frame.cells_high):
            for cell_x in range(frame.cells_wide):
                cell_index = cell_y * frame.cells_wide + cell_x
                if frame.blank_cells[cell_index]:
                    continue
                x = cell_x * 8 - frame.raw.origin_x
                y = cell_y * 8 - frame.raw.origin_y
                if not -128 <= x <= 127 or not -128 <= y <= 127:
                    raise AssetValidationError(
                        f"frame {frame.raw.frame_id!r}: sprite offset outside s8"
                    )
                reference = frame.cells[cell_index]
                attribute = _tile_attribute(
                    reference,
                    slots[frame.raw.palette_id],
                    frame.raw.priority,
                    False,
                    False,
                )
                pieces.append(SpritePiece(x, y, attribute))
        if not pieces:
            raise AssetValidationError(
                f"frame {frame.raw.frame_id!r}: compiled sprite has no pieces"
            )
        if len(pieces) > 16:
            raise AssetValidationError(
                f"frame {frame.raw.frame_id!r}: {len(pieces)} pieces exceeds per-frame "
                "format limit 16"
            )
        result.append(
            CompiledSpriteFrame(
                frame.raw.frame_id,
                frame.raw.width,
                frame.raw.height,
                tuple(pieces),
                frame.raw.source_file,
            )
        )
    return tuple(result)


def _compile_animations(
    animations: Sequence[RawAnimation],
    sprites: Sequence[CompiledSpriteFrame],
) -> tuple[CompiledAnimation, ...]:
    indices = {frame.frame_id: index for index, frame in enumerate(sprites)}
    result: list[CompiledAnimation] = []
    for animation in animations:
        steps: list[tuple[int, int]] = []
        for frame, ticks in animation.steps:
            if frame not in indices:
                raise AssetValidationError(
                    f"animation {animation.animation_id!r}: frame {frame!r} "
                    "does not compile as a sprite"
                )
            steps.append((indices[frame], ticks))
        result.append(
            CompiledAnimation(
                animation.animation_id,
                LOOP_CODES[animation.loop],
                tuple(steps),
                animation.source_file,
            )
        )
    return tuple(result)


def _encode_palettes(palettes: Sequence[CompiledPalette]) -> bytes:
    result = bytearray(4 * 16 * 2)
    for palette in palettes:
        words = (0, *palette.words)
        for index, word in enumerate(words):
            struct.pack_into(">H", result, (palette.slot * 16 + index) * 2, word)
    return bytes(result)


def _encode_tiles(deduper: _TileDeduper) -> bytes:
    return b"".join(pack_tile_4bpp(tile) for tile in deduper.tiles)


def _binary_id(identifier: str) -> bytes:
    encoded = identifier.encode("ascii")
    if len(encoded) > 15:
        raise AssetValidationError(f"binary ID {identifier!r} exceeds 15 bytes")
    return encoded.ljust(16, b"\0")


def _encode_tilemaps(maps: Sequence[CompiledMap]) -> tuple[bytes, Mapping[str, int]]:
    header_size = 16
    directory_size = 32 * len(maps)
    data_offset = header_size + directory_size
    data = bytearray()
    directory = bytearray()
    offsets: dict[str, int] = {}
    for item in maps:
        offset = data_offset + len(data)
        payload = b"".join(struct.pack(">H", entry) for entry in item.entries)
        offsets[item.map_id] = offset
        directory.extend(
            struct.pack(
                ">16sHHIII",
                _binary_id(item.map_id),
                item.width,
                item.height,
                offset,
                len(payload),
                0,
            )
        )
        data.extend(payload)
    header = struct.pack(">4sHHII", b"R94M", FORMAT_VERSION, len(maps), 16, data_offset)
    return header + bytes(directory) + bytes(data), offsets


def _encode_sprites(
    sprites: Sequence[CompiledSpriteFrame],
) -> tuple[bytes, Mapping[str, int]]:
    header_size = 16
    directory_size = 32 * len(sprites)
    data_offset = header_size + directory_size
    data = bytearray()
    directory = bytearray()
    offsets: dict[str, int] = {}
    for item in sprites:
        offset = data_offset + len(data)
        payload = b"".join(
            struct.pack(">bbBBHH", piece.x, piece.y, 0, 0, piece.attribute, 0)
            for piece in item.pieces
        )
        offsets[item.frame_id] = offset
        directory.extend(
            struct.pack(
                ">16sHHHHII",
                _binary_id(item.frame_id),
                item.width,
                item.height,
                len(item.pieces),
                0,
                offset,
                len(payload),
            )
        )
        data.extend(payload)
    header = struct.pack(
        ">4sHHII", b"R94S", FORMAT_VERSION, len(sprites), 16, data_offset
    )
    return header + bytes(directory) + bytes(data), offsets


def _encode_animations(
    animations: Sequence[CompiledAnimation],
) -> tuple[bytes, Mapping[str, int]]:
    header_size = 16
    directory_size = 32 * len(animations)
    data_offset = header_size + directory_size
    data = bytearray()
    directory = bytearray()
    offsets: dict[str, int] = {}
    for item in animations:
        offset = data_offset + len(data)
        payload = b"".join(
            struct.pack(">HH", frame_index, ticks)
            for frame_index, ticks in item.steps
        )
        total_ticks = sum(ticks for _, ticks in item.steps)
        offsets[item.animation_id] = offset
        directory.extend(
            struct.pack(
                ">16sHHIIHH",
                _binary_id(item.animation_id),
                item.loop_code,
                len(item.steps),
                offset,
                len(payload),
                total_ticks,
                0,
            )
        )
        data.extend(payload)
    header = struct.pack(
        ">4sHHII", b"R94N", FORMAT_VERSION, len(animations), 16, data_offset
    )
    return header + bytes(directory) + bytes(data), offsets


def _align_even(value: bytearray) -> None:
    if len(value) & 1:
        value.append(0)


def _encode_bundle(
    component_files: Mapping[str, bytes],
) -> tuple[bytes, Mapping[str, int]]:
    section_count = len(SECTION_TYPES)
    directory_offset = 16
    payload_offset = directory_offset + 16 * section_count
    payload = bytearray()
    directory = bytearray()
    offsets: dict[str, int] = {}
    for section_type, name in SECTION_TYPES:
        _align_even(payload)
        absolute_offset = payload_offset + len(payload)
        value = component_files[name]
        offsets[section_type.decode("ascii")] = absolute_offset
        directory.extend(
            struct.pack(
                ">4sIII",
                section_type,
                absolute_offset,
                len(value),
                zlib.crc32(value) & 0xFFFFFFFF,
            )
        )
        payload.extend(value)
    file_size = payload_offset + len(payload)
    header = struct.pack(
        ">4sHHII", b"R94A", FORMAT_VERSION, section_count, directory_offset, file_size
    )
    return header + bytes(directory) + bytes(payload), offsets


def _asm_name(identifier: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", identifier.upper())


def _encode_assembly(
    bundle_offsets: Mapping[str, int],
    frames: Sequence[CompiledFrame],
    palettes: Sequence[CompiledPalette],
    maps: Sequence[CompiledMap],
    map_offsets: Mapping[str, int],
    sprites: Sequence[CompiledSpriteFrame],
    sprite_offsets: Mapping[str, int],
    animations: Sequence[CompiledAnimation],
    animation_offsets: Mapping[str, int],
    unique_tiles: int,
) -> bytes:
    lines = [
        "; Generated by tools/asset_compiler. DO NOT EDIT.",
        f"ART_FORMAT_VERSION equ {FORMAT_VERSION}",
        f"ART_PALETTE_COUNT equ {len(palettes)}",
        f"ART_TILE_COUNT equ {unique_tiles}",
        f"ART_TILEMAP_COUNT equ {len(maps)}",
        f"ART_SPRITE_FRAME_COUNT equ {len(sprites)}",
        f"ART_ANIMATION_COUNT equ {len(animations)}",
    ]
    for section, offset in bundle_offsets.items():
        lines.append(f"ART_SECTION_{section}_OFFSET equ ${offset:08X}")
    for palette in palettes:
        lines.append(
            f"ART_PALETTE_{_asm_name(palette.palette_id)}_SLOT equ {palette.slot}"
        )
    for frame in frames:
        if frame.cells_wide == 1 and frame.cells_high == 1:
            reference = frame.cells[0]
            lines.extend(
                (
                    f"ART_TILE_{_asm_name(frame.raw.frame_id)} equ {reference.index}",
                    f"ART_TILE_{_asm_name(frame.raw.frame_id)}_HFLIP equ {int(reference.hflip)}",
                    f"ART_TILE_{_asm_name(frame.raw.frame_id)}_VFLIP equ {int(reference.vflip)}",
                )
            )
    for index, item in enumerate(maps):
        lines.extend(
            (
                f"ART_MAP_{_asm_name(item.map_id)}_INDEX equ {index}",
                f"ART_MAP_{_asm_name(item.map_id)}_OFFSET equ ${map_offsets[item.map_id]:08X}",
            )
        )
    for index, item in enumerate(sprites):
        lines.extend(
            (
                f"ART_SPRITE_{_asm_name(item.frame_id)}_INDEX equ {index}",
                f"ART_SPRITE_{_asm_name(item.frame_id)}_OFFSET equ ${sprite_offsets[item.frame_id]:08X}",
            )
        )
    for index, item in enumerate(animations):
        lines.extend(
            (
                f"ART_ANIM_{_asm_name(item.animation_id)}_INDEX equ {index}",
                f"ART_ANIM_{_asm_name(item.animation_id)}_OFFSET equ ${animation_offsets[item.animation_id]:08X}",
            )
        )
    lines.extend(
        (
            "",
            "ArtBundle:",
            '    incbin "assets/generated/art/recursion94_art.bin"',
            "ArtBundleEnd:",
            "ART_BUNDLE_SIZE equ ArtBundleEnd-ArtBundle",
            "",
        )
    )
    return "\n".join(lines).encode("ascii")


def _source_hash(parsed_sources: Sequence[tuple[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    digest = hashlib.sha256()
    records: list[dict[str, str]] = []
    for relative, value in sorted(parsed_sources):
        canonical = _canonical_json(value)
        semantic_sha = hashlib.sha256(canonical).hexdigest()
        encoded_name = relative.encode("utf-8")
        digest.update(struct.pack(">I", len(encoded_name)))
        digest.update(encoded_name)
        digest.update(struct.pack(">I", len(canonical)))
        digest.update(canonical)
        records.append({"path": relative, "semantic_sha256": semantic_sha})
    return digest.hexdigest(), records


def _budget_record(limit: int, used: int, unit: str) -> dict[str, Any]:
    return {
        "limit": limit,
        "used": used,
        "headroom": limit - used,
        "unit": unit,
        "status": "PASS" if used <= limit else "FAIL",
    }


def _build_report(
    manifest: Mapping[str, Any],
    metadata: Mapping[str, Any],
    source_sha: str,
    source_records: Sequence[Mapping[str, str]],
    palettes: Sequence[CompiledPalette],
    frames: Sequence[CompiledFrame],
    deduper: _TileDeduper,
    maps: Sequence[CompiledMap],
    sprites: Sequence[CompiledSpriteFrame],
    animations: Sequence[CompiledAnimation],
    payload_files: Mapping[str, bytes],
) -> dict[str, Any]:
    budgets = metadata["budgets"]
    maximum_map_bytes = max((len(item.entries) * 2 for item in maps), default=0)
    maximum_pieces = max((len(item.pieces) for item in sprites), default=0)
    budget_results = {
        "rom_bundle_bytes": _budget_record(
            budgets["rom_bytes_max"], len(payload_files[OUTPUT_BUNDLE]), "bytes"
        ),
        "vram_tile_bytes": _budget_record(
            budgets["vram_tile_bytes_max"],
            len(payload_files[OUTPUT_TILES]),
            "bytes",
        ),
        "vram_largest_map_bytes": _budget_record(
            budgets["vram_map_bytes_max"], maximum_map_bytes, "bytes"
        ),
        "cram_words": _budget_record(
            budgets["cram_words_max"], len(payload_files[OUTPUT_PALETTES]) // 2, "words"
        ),
        "unique_tiles": _budget_record(
            budgets["unique_tiles_max"], len(deduper.tiles), "tiles"
        ),
        "sprite_pieces_per_frame": _budget_record(
            budgets["sprite_pieces_per_frame_max"], maximum_pieces, "pieces"
        ),
        "sprite_frames": _budget_record(
            budgets["sprite_frames_max"], len(sprites), "frames"
        ),
        "animations": _budget_record(
            budgets["animations_max"], len(animations), "animations"
        ),
    }
    failures = [name for name, value in budget_results.items() if value["status"] != "PASS"]
    palette_usage = collections.Counter(
        (entry >> 13) & 3 for item in maps for entry in item.entries
    )
    priority_entries = sum(
        bool(entry & 0x8000) for item in maps for entry in item.entries
    )
    hflip_entries = sum(
        bool(entry & 0x0800) for item in maps for entry in item.entries
    )
    vflip_entries = sum(
        bool(entry & 0x1000) for item in maps for entry in item.entries
    )
    sprite_palette_usage = collections.Counter(
        (piece.attribute >> 13) & 3
        for item in sprites
        for piece in item.pieces
    )
    sprite_priority = sum(
        bool(piece.attribute & 0x8000)
        for item in sprites
        for piece in item.pieces
    )
    input_tiles = sum(frame.cells_wide * frame.cells_high for frame in frames)
    output_hashes = {
        name: {
            "bytes": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
        for name, value in sorted(payload_files.items())
        if name not in (OUTPUT_REPORT, OUTPUT_CHECKSUMS)
    }
    return {
        "schema": "recursion94.asset_compile_report",
        "schema_version": FORMAT_VERSION,
        "status": "FAIL" if failures else "PASS",
        "compiler": {
            "name": "RECURSION 94 deterministic Genesis art compiler",
            "version": COMPILER_VERSION,
            "runtime": "Python standard library only",
        },
        "identity": manifest["identity"],
        "art_direction": manifest["art_direction"],
        "provenance": {
            **metadata["provenance"],
            "source_files": list(source_records),
            "source_semantic_sha256": source_sha,
        },
        "palettes": {
            "format": "Genesis 9-bit 0000 BBB0 GGG0 RRR0",
            "transparent_index": 0,
            "bank_words": 16,
            "emitted_banks": 4,
            "definitions": [
                {
                    "id": palette.palette_id,
                    "slot": palette.slot,
                    "mode": palette.mode,
                    "source_opaque_colors": len(palette.source_colors),
                    "emitted_opaque_colors": len(palette.words),
                    "words_hex": ["$0000"]
                    + [f"${word:04X}" for word in palette.words],
                    "source_to_index": {
                        _rgb_text(color): palette.color_to_index[color]
                        for color in palette.source_colors
                    },
                }
                for palette in sorted(palettes, key=lambda item: item.slot)
            ],
        },
        "tiles": {
            "format": "8x8 4bpp, 32 bytes",
            "input_cells": input_tiles,
            "unique_tiles_including_transparent": len(deduper.tiles),
            "transparent_tile_index": 0,
            "reuse": dict(sorted(deduper.reuse.items())),
            "bytes": len(payload_files[OUTPUT_TILES]),
        },
        "tilemaps": {
            "count": len(maps),
            "maps": [
                {
                    "id": item.map_id,
                    "width_tiles": item.width,
                    "height_tiles": item.height,
                    "entries": len(item.entries),
                    "bytes": len(item.entries) * 2,
                    "source_file": item.source_file,
                }
                for item in maps
            ],
            "attribute_coverage": {
                "palette_slots": {
                    str(slot): palette_usage.get(slot, 0) for slot in range(4)
                },
                "priority_entries": priority_entries,
                "horizontal_flip_entries": hflip_entries,
                "vertical_flip_entries": vflip_entries,
            },
        },
        "sprites": {
            "mapping_model": "bounded 1x1-cell hardware-sprite pieces",
            "frame_count": len(sprites),
            "piece_count": sum(len(item.pieces) for item in sprites),
            "maximum_pieces_per_frame": maximum_pieces,
            "priority_pieces": sprite_priority,
            "palette_slots": {
                str(slot): sprite_palette_usage.get(slot, 0) for slot in range(4)
            },
            "frames": [
                {
                    "id": item.frame_id,
                    "width": item.width,
                    "height": item.height,
                    "pieces": len(item.pieces),
                    "source_file": item.source_file,
                }
                for item in sprites
            ],
        },
        "animations": {
            "count": len(animations),
            "entries": [
                {
                    "id": item.animation_id,
                    "loop_code": item.loop_code,
                    "steps": len(item.steps),
                    "total_ticks": sum(ticks for _, ticks in item.steps),
                    "source_file": item.source_file,
                }
                for item in animations
            ],
        },
        "budgets": budget_results,
        "budget_failures": failures,
        "outputs": output_hashes,
        "claims": {
            "source_validation": True,
            "binary_format_validation": True,
            "deterministic_generation": True,
            "runtime_integration": False,
            "emulator_rendering": False,
            "subjective_visual_review": False,
        },
    }


def _verify_component_headers(files: Mapping[str, bytes]) -> None:
    palettes = files[OUTPUT_PALETTES]
    if len(palettes) != 128:
        raise AssetCompileError("internal error: CRAM image is not 128 bytes")
    tiles = files[OUTPUT_TILES]
    if not tiles or len(tiles) % 32:
        raise AssetCompileError("internal error: tile image is not 32-byte aligned")
    for name, magic in (
        (OUTPUT_TILEMAPS, b"R94M"),
        (OUTPUT_SPRITES, b"R94S"),
        (OUTPUT_ANIMATIONS, b"R94N"),
    ):
        value = files[name]
        if len(value) < 16:
            raise AssetCompileError(f"internal error: {name} is truncated")
        actual_magic, version, count, directory_offset, data_offset = struct.unpack_from(
            ">4sHHII", value, 0
        )
        if actual_magic != magic or version != FORMAT_VERSION:
            raise AssetCompileError(f"internal error: {name} header mismatch")
        if directory_offset != 16 or data_offset != 16 + count * 32:
            raise AssetCompileError(f"internal error: {name} directory mismatch")
        if data_offset > len(value):
            raise AssetCompileError(f"internal error: {name} data offset out of range")
    bundle = files[OUTPUT_BUNDLE]
    magic, version, count, directory_offset, file_size = struct.unpack_from(
        ">4sHHII", bundle, 0
    )
    if (
        magic != b"R94A"
        or version != FORMAT_VERSION
        or count != len(SECTION_TYPES)
        or directory_offset != 16
        or file_size != len(bundle)
    ):
        raise AssetCompileError("internal error: aggregate bundle header mismatch")
    for index, (expected_type, component_name) in enumerate(SECTION_TYPES):
        section_type, offset, size, crc = struct.unpack_from(
            ">4sIII", bundle, 16 + index * 16
        )
        payload = bundle[offset : offset + size]
        if (
            section_type != expected_type
            or payload != files[component_name]
            or zlib.crc32(payload) & 0xFFFFFFFF != crc
        ):
            raise AssetCompileError(
                f"internal error: aggregate section {expected_type!r} mismatch"
            )


def build_artifacts(art_root: Path, map_root: Path) -> BuildBundle:
    """Validate all sources and construct every generated artifact in memory."""

    art_root = Path(art_root)
    map_root = Path(map_root)
    manifest_path = art_root / "manifest.json"
    manifest, metadata = _parse_manifest(manifest_path)
    _validate_declared_files(
        art_root,
        map_root,
        metadata["palette_file"],
        metadata["asset_files"],
        metadata["map_files"],
    )
    palette_path = art_root / metadata["palette_file"]
    palette_json = _load_json(palette_path)
    palettes_source = _parse_palettes(palette_path)
    frames_raw, animations_raw, asset_json = _parse_assets(
        art_root, metadata["asset_files"], palettes_source
    )
    palettes = _compile_palettes(palettes_source, frames_raw)
    frames, deduper = _compile_frames(frames_raw, palettes)
    maps, map_json = _parse_and_compile_maps(
        map_root, metadata["map_files"], frames, palettes
    )
    sprites = _compile_sprites(frames, palettes)
    animations = _compile_animations(animations_raw, sprites)

    parsed_sources = (
        (("art/manifest.json", manifest), ("art/" + metadata["palette_file"], palette_json))
        + asset_json
        + map_json
    )
    source_sha, source_records = _source_hash(parsed_sources)

    components: dict[str, bytes] = {
        OUTPUT_PALETTES: _encode_palettes(palettes),
        OUTPUT_TILES: _encode_tiles(deduper),
    }
    tilemaps_binary, map_offsets = _encode_tilemaps(maps)
    sprites_binary, sprite_offsets = _encode_sprites(sprites)
    animations_binary, animation_offsets = _encode_animations(animations)
    components[OUTPUT_TILEMAPS] = tilemaps_binary
    components[OUTPUT_SPRITES] = sprites_binary
    components[OUTPUT_ANIMATIONS] = animations_binary
    bundle_binary, bundle_offsets = _encode_bundle(components)
    components[OUTPUT_BUNDLE] = bundle_binary
    components[OUTPUT_ASSEMBLY] = _encode_assembly(
        bundle_offsets,
        frames,
        palettes,
        maps,
        map_offsets,
        sprites,
        sprite_offsets,
        animations,
        animation_offsets,
        len(deduper.tiles),
    )
    _verify_component_headers(components)
    report = _build_report(
        manifest,
        metadata,
        source_sha,
        source_records,
        palettes,
        frames,
        deduper,
        maps,
        sprites,
        animations,
        components,
    )
    if report["status"] != "PASS":
        raise AssetValidationError(
            "asset budgets exceeded: " + ", ".join(report["budget_failures"])
        )
    components[OUTPUT_REPORT] = (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    checksum_lines = [
        f"{hashlib.sha256(value).hexdigest()}  {name}"
        for name, value in sorted(components.items())
    ]
    components[OUTPUT_CHECKSUMS] = ("\n".join(checksum_lines) + "\n").encode(
        "ascii"
    )
    return BuildBundle(
        files=dict(sorted(components.items())),
        source_sha256=source_sha,
        bundle_sha256=hashlib.sha256(bundle_binary).hexdigest(),
        report=report,
    )


def _write_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def compile_assets(
    art_root: Path,
    map_root: Path,
    output_root: Path,
    report_path: Path | None = None,
) -> BuildBundle:
    bundle = build_artifacts(art_root, map_root)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    for name, value in bundle.files.items():
        _write_atomic(output_root / name, value)
    if report_path is not None:
        _write_atomic(Path(report_path), bundle.files[OUTPUT_REPORT])
    return bundle


def check_generated(
    art_root: Path,
    map_root: Path,
    output_root: Path,
    report_path: Path | None = None,
) -> BuildBundle:
    bundle = build_artifacts(art_root, map_root)
    output_root = Path(output_root)
    actual_names = {
        path.name for path in output_root.iterdir() if path.is_file()
    } if output_root.is_dir() else set()
    if actual_names != OUTPUT_NAMES:
        raise AssetCompileError(
            "generated art file set mismatch: "
            f"missing={sorted(OUTPUT_NAMES - actual_names)!r}, "
            f"extra={sorted(actual_names - OUTPUT_NAMES)!r}"
        )
    for name, expected in bundle.files.items():
        path = output_root / name
        try:
            actual = path.read_bytes()
        except OSError as exc:
            raise AssetCompileError(f"cannot read generated file {path}: {exc}") from exc
        if actual != expected:
            raise AssetCompileError(
                f"generated file is stale or corrupt: {path}; "
                f"expected sha256={hashlib.sha256(expected).hexdigest()}, "
                f"actual sha256={hashlib.sha256(actual).hexdigest()}"
            )
    if report_path is not None:
        report = Path(report_path)
        try:
            actual_report = report.read_bytes()
        except OSError as exc:
            raise AssetCompileError(f"cannot read evidence report {report}: {exc}") from exc
        if actual_report != bundle.files[OUTPUT_REPORT]:
            raise AssetCompileError(f"evidence report is stale or corrupt: {report}")
    return bundle


def _default_paths() -> tuple[Path, Path, Path, Path]:
    root = Path(__file__).resolve().parents[2]
    return (
        root / "assets/source/art",
        root / "assets/source/maps",
        root / "assets/generated/art",
        root / "artifacts/reports/asset-compile.json",
    )


def _parser() -> argparse.ArgumentParser:
    art, maps, output, report = _default_paths()
    parser = argparse.ArgumentParser(
        description="Compile strict JSON art into deterministic Genesis assets."
    )
    parser.add_argument("--art-root", type=Path, default=art)
    parser.add_argument("--map-root", type=Path, default=maps)
    parser.add_argument("--output", type=Path, default=output)
    parser.add_argument("--report", type=Path, default=report)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless all generated files and the evidence report are current",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.check:
            result = check_generated(
                args.art_root, args.map_root, args.output, args.report
            )
            verb = "verified"
        else:
            result = compile_assets(
                args.art_root, args.map_root, args.output, args.report
            )
            verb = "compiled"
    except AssetCompileError as exc:
        print(f"asset compiler: ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"asset compiler: {verb} {len(result.files)} files; "
        f"source={result.source_sha256}; bundle={result.bundle_sha256}; "
        f"status={result.report['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
