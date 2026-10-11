#!/usr/bin/env python3
"""Pure-stdlib Sega Genesis ROM checksum, header, and vector handling."""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Iterable


VECTOR_COUNT = 64
VECTOR_TABLE_SIZE = 0x100
HEADER_OFFSET = 0x100
HEADER_SIZE = 0x100
CHECKSUM_OFFSET = 0x18E
CHECKSUM_DATA_OFFSET = 0x200
DEFAULT_ROM_SIZE = 1024 * 1024
EXPECTED_INITIAL_SSP = 0x00FFFFFC
MAIN_RAM_START = 0x00FF0000
MAIN_RAM_END = 0x00FFFFFF


class RomValidationError(ValueError):
    """Raised when a ROM cannot be constructed or fails static validation."""

    def __init__(self, issues: Iterable["Issue"]):
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


@dataclass(frozen=True)
class Issue:
    check: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _text(data: bytes, start: int, size: int) -> str:
    raw = data[start : start + size]
    return raw.rstrip(b" \0").decode("ascii", errors="replace")


def _printable_ascii(raw: bytes) -> bool:
    return all(byte == 0 or 0x20 <= byte <= 0x7E for byte in raw)


def calculate_checksum(
    rom: bytes | bytearray | memoryview, *, declared_end: int | None = None
) -> int:
    """Return the wrapping big-endian word sum from $200 through ROM end.

    A caller may provide the inclusive declared ROM end. An odd final byte is
    treated as the high byte of a final word with a zero low byte. Normal
    cartridge images, including the fixed 1 MiB project image, are even sized.
    """

    view = memoryview(rom)
    if len(view) < CHECKSUM_DATA_OFFSET:
        raise ValueError("ROM is shorter than the $200 checksum data offset")
    end = len(view) - 1 if declared_end is None else declared_end
    if end < CHECKSUM_DATA_OFFSET - 1 or end >= len(view):
        raise ValueError(
            f"declared ROM end ${end:08X} is outside {len(view)} available bytes"
        )
    total = 0
    offset = CHECKSUM_DATA_OFFSET
    while offset <= end:
        high = view[offset]
        low = view[offset + 1] if offset + 1 <= end else 0
        total = (total + ((high << 8) | low)) & 0xFFFF
        offset += 2
    return total


def patch_checksum(rom: bytearray, *, declared_end: int | None = None) -> int:
    """Patch and return the Genesis header checksum."""

    if len(rom) < CHECKSUM_DATA_OFFSET:
        raise ValueError("ROM is too short to contain a Genesis header")
    checksum = calculate_checksum(rom, declared_end=declared_end)
    struct.pack_into(">H", rom, CHECKSUM_OFFSET, checksum)
    return checksum


def inspect_rom(rom: bytes, *, assembled_bytes: int | None = None) -> dict[str, Any]:
    """Parse stable header/vector facts without deciding whether they pass."""

    if len(rom) < CHECKSUM_DATA_OFFSET:
        raise ValueError("ROM is too short to contain vectors and a cartridge header")

    vectors = [_u32(rom, index * 4) for index in range(VECTOR_COUNT)]
    rom_start = _u32(rom, 0x1A0)
    rom_end = _u32(rom, 0x1A4)
    ram_start = _u32(rom, 0x1A8)
    ram_end = _u32(rom, 0x1AC)
    declared_checksum: int | None = None
    if rom_start == 0 and rom_end < len(rom):
        declared_checksum = calculate_checksum(rom, declared_end=rom_end)

    save_signature = bytes(rom[0x1B0:0x1B2])
    save_present = save_signature == b"RA"
    save = {
        "present": save_present,
        "signature": save_signature.decode("ascii", errors="replace"),
        "type_flags_hex": bytes(rom[0x1B2:0x1B4]).hex().upper(),
        "start": _u32(rom, 0x1B4),
        "end": _u32(rom, 0x1B8),
    }

    return {
        "bytes": len(rom),
        "assembled_bytes": assembled_bytes,
        "sha256": hashlib.sha256(rom).hexdigest(),
        "crc32": f"{zlib.crc32(rom) & 0xFFFFFFFF:08x}",
        "vectors": {
            "initial_ssp": vectors[0],
            "reset_pc": vectors[1],
            "entries": vectors,
        },
        "header": {
            "console": _text(rom, 0x100, 16),
            "copyright": _text(rom, 0x110, 16),
            "domestic_title": _text(rom, 0x120, 48),
            "overseas_title": _text(rom, 0x150, 48),
            "product": _text(rom, 0x180, 14),
            "stored_checksum": _u16(rom, CHECKSUM_OFFSET),
            "calculated_checksum": declared_checksum,
            "io_support": _text(rom, 0x190, 16),
            "rom_start": rom_start,
            "rom_end": rom_end,
            "ram_start": ram_start,
            "ram_end": ram_end,
            "save": save,
            "region": _text(rom, 0x1F0, 16),
        },
    }


def validate_rom(
    rom: bytes,
    *,
    expected_size: int | None = DEFAULT_ROM_SIZE,
    assembled_bytes: int | None = None,
    require_all_vectors: bool = True,
    expected_initial_ssp: int | None = EXPECTED_INITIAL_SSP,
) -> dict[str, Any]:
    """Validate vectors, header, bounds, and checksum independently.

    The function returns a report on success and raises ``RomValidationError``
    containing every detected error on failure. Optional SRAM is parsed and
    checked when declared; save behavior remains a later runtime gate.
    """

    issues: list[Issue] = []
    if len(rom) < CHECKSUM_DATA_OFFSET:
        raise RomValidationError(
            [Issue("minimum_size", "ROM is shorter than vectors plus header ($200)")]
        )

    facts = inspect_rom(rom, assembled_bytes=assembled_bytes)
    header = facts["header"]
    vectors = facts["vectors"]["entries"]

    if expected_size is not None and len(rom) != expected_size:
        issues.append(
            Issue(
                "image_size",
                f"expected {expected_size} bytes, found {len(rom)} bytes",
            )
        )
    if len(rom) & 1:
        issues.append(Issue("image_alignment", "ROM byte length must be even"))

    initial_ssp = vectors[0]
    if initial_ssp & 1:
        issues.append(Issue("initial_ssp_alignment", "initial SSP is not even"))
    if not MAIN_RAM_START <= initial_ssp <= MAIN_RAM_END:
        issues.append(
            Issue(
                "initial_ssp_range",
                f"initial SSP ${initial_ssp:08X} is outside 64 KiB work RAM",
            )
        )
    if expected_initial_ssp is not None and initial_ssp != expected_initial_ssp:
        issues.append(
            Issue(
                "initial_ssp_contract",
                f"initial SSP must be ${expected_initial_ssp:08X}, "
                f"found ${initial_ssp:08X}",
            )
        )

    reset_pc = vectors[1]
    vector_limit = assembled_bytes if assembled_bytes is not None else len(rom)
    if reset_pc & 1:
        issues.append(Issue("reset_vector_alignment", "reset PC is not even"))
    if not CHECKSUM_DATA_OFFSET <= reset_pc < vector_limit:
        issues.append(
            Issue(
                "reset_vector_range",
                f"reset PC ${reset_pc:08X} is outside assembled ROM code "
                f"$00000200-${max(vector_limit - 1, 0):08X}",
            )
        )

    if require_all_vectors:
        for index, address in enumerate(vectors[2:], start=2):
            if address == 0:
                issues.append(
                    Issue(
                        "exception_vector_coverage",
                        f"vector {index} is zero instead of a defined handler",
                    )
                )
            elif address & 1 or not CHECKSUM_DATA_OFFSET <= address < vector_limit:
                issues.append(
                    Issue(
                        "exception_vector_range",
                        f"vector {index} target ${address:08X} is not an even "
                        "address inside assembled ROM",
                    )
                )

    console_raw = rom[0x100:0x110]
    if not console_raw.startswith(b"SEGA"):
        issues.append(
            Issue("console_name", "header console field does not begin with ASCII SEGA")
        )
    for check, start, size in (
        ("console_ascii", 0x100, 16),
        ("copyright_ascii", 0x110, 16),
        ("domestic_title_ascii", 0x120, 48),
        ("overseas_title_ascii", 0x150, 48),
        ("product_ascii", 0x180, 14),
        ("io_ascii", 0x190, 16),
        ("region_ascii", 0x1F0, 16),
    ):
        if not _printable_ascii(rom[start : start + size]):
            issues.append(Issue(check, f"header bytes ${start:03X} contain non-ASCII data"))

    if not header["domestic_title"]:
        issues.append(Issue("domestic_title", "domestic title is blank"))
    if not header["overseas_title"]:
        issues.append(Issue("overseas_title", "overseas title is blank"))
    if not header["product"]:
        issues.append(Issue("product_code", "product/type field is blank"))
    if "J" not in header["io_support"].upper():
        issues.append(
            Issue("controller_metadata", "I/O support field does not declare J joypad")
        )

    rom_start = header["rom_start"]
    rom_end = header["rom_end"]
    if rom_start != 0:
        issues.append(
            Issue("rom_start", f"declared ROM start must be $00000000, found ${rom_start:08X}")
        )
    if rom_end != len(rom) - 1:
        issues.append(
            Issue(
                "rom_end",
                f"declared inclusive ROM end ${rom_end:08X} does not match "
                f"image end ${len(rom) - 1:08X}",
            )
        )

    ram_start = header["ram_start"]
    ram_end = header["ram_end"]
    if (
        ram_start > ram_end
        or ram_start < MAIN_RAM_START
        or ram_end > MAIN_RAM_END
        or not ram_start <= initial_ssp <= ram_end
    ):
        issues.append(
            Issue(
                "ram_bounds",
                f"declared RAM ${ram_start:08X}-${ram_end:08X} does not "
                "validly cover the initial SSP in physical work RAM",
            )
        )

    if 0 <= rom_end < len(rom):
        calculated = calculate_checksum(rom, declared_end=rom_end)
        if header["stored_checksum"] != calculated:
            issues.append(
                Issue(
                    "checksum",
                    f"stored checksum ${header['stored_checksum']:04X} does not "
                    f"match calculated ${calculated:04X}",
                )
            )
    else:
        issues.append(
            Issue("checksum_bounds", "checksum cannot be evaluated through declared ROM end")
        )

    normalized_region = "".join(header["region"].upper().split())
    if not normalized_region or not set(normalized_region) & set("JUE"):
        issues.append(
            Issue(
                "region_metadata",
                f"region field {header['region']!r} does not declare J, U, or E",
            )
        )

    save = header["save"]
    if save["present"]:
        if save["type_flags_hex"] != "F820":
            issues.append(
                Issue(
                    "save_type_flags",
                    "declared SRAM type/flags must be $F820 for byte-wide "
                    "backup RAM on odd addresses",
                )
            )
        if save["start"] > save["end"]:
            issues.append(
                Issue("save_bounds", "declared save-memory start is after its end")
            )
        if not (save["start"] & 1 and save["end"] & 1):
            issues.append(
                Issue(
                    "save_lane",
                    "project byte-wide SRAM start/end must both select odd addresses",
                )
            )
        if save["start"] != 0x00200001 or save["end"] != 0x00203FFF:
            issues.append(
                Issue(
                    "save_contract",
                    "declared SRAM does not match project range "
                    "$00200001-$00203FFF",
                )
            )

    checks = [
        {
            "check": name,
            "status": "pass",
        }
        for name in (
            "image_size",
            "vector_table",
            "header_ascii",
            "header_fields",
            "rom_ram_bounds",
            "checksum",
            "region_metadata",
            "save_metadata",
        )
        if not any(
            issue.check == name
            or (name == "vector_table" and "vector" in issue.check)
            or (name == "header_ascii" and issue.check.endswith("_ascii"))
            or (
                name == "header_fields"
                and issue.check
                in {"console_name", "domestic_title", "overseas_title", "product_code"}
            )
            or (
                name == "rom_ram_bounds"
                and issue.check in {"rom_start", "rom_end", "ram_bounds"}
            )
            or (name == "save_metadata" and issue.check.startswith("save_"))
            for issue in issues
        )
    ]
    report = {
        "schema": 1,
        "status": "pass" if not issues else "fail",
        "scope": "independent-static-rom",
        "facts": facts,
        "checks": checks,
        "issues": [issue.as_dict() for issue in issues],
        "pending_runtime_gates": [
            "reset execution and debug heartbeat",
            "exception-handler execution",
            "save-memory read/write and corruption recovery",
            "controller behavior",
            "video/audio behavior",
        ],
    }
    if issues:
        error = RomValidationError(issues)
        error.report = report  # type: ignore[attr-defined]
        raise error
    return report
