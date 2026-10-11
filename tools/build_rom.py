#!/usr/bin/env python3
"""Assemble and construct deterministic RECURSION 94 ROM images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rom_tools.rom_image import (  # noqa: E402
    DEFAULT_ROM_SIZE,
    RomValidationError,
    patch_checksum,
    validate_rom,
)
from tools.content_compiler.compiler import (  # noqa: E402
    ContentCompileError,
    OUTPUT_BINARY as CONTENT_OUTPUT_BINARY,
    OUTPUT_REPORT as CONTENT_OUTPUT_REPORT,
    check_generated as check_generated_content,
    compile_content,
    verify_binary as verify_content_binary,
)
from tools.campaign_runtime.compiler import (  # noqa: E402
    OUTPUT_ASSEMBLY as CAMPAIGN_RUNTIME_OUTPUT_ASSEMBLY,
    OUTPUT_REPORT as CAMPAIGN_RUNTIME_OUTPUT_REPORT,
    RuntimeCompileError,
    check_generated as check_generated_campaign_runtime,
    compile_runtime,
)
from tools.asset_compiler.compiler import (  # noqa: E402
    OUTPUT_BUNDLE as ART_OUTPUT_BUNDLE,
    OUTPUT_REPORT as ART_OUTPUT_REPORT,
    check_generated as check_generated_art,
    compile_assets,
)
from tools.crc32_tables import (  # noqa: E402
    Crc32TableError,
    OUTPUT_BINARY as CRC32_OUTPUT_BINARY,
    check_generated as check_generated_crc32,
    compile_tables,
)


VASM_VERSION_TOKEN = "vasm 2.0f"
SJASMPLUS_VERSION_TOKEN = "1.23.1"
MAX_Z80_DRIVER_BYTES = 0x1F00
Z80_DRIVER_STATE_BASE = 0x1000
Z80_RAM_BYTES = 0x2000
Z80_LAYOUT_POINTS = (
    ("DRIVER_STATE_BASE", Z80_DRIVER_STATE_BASE),
    ("CommandRingStart", 0x1F00),
    ("CommandRingDataEnd", 0x1F40),
    ("CommandRingRegionEnd", 0x1FE0),
    ("DriverStatusStart", 0x1FE0),
    ("DriverStatusEnd", 0x1FF0),
    ("UrgentMailboxStart", 0x1FF0),
    ("UrgentMailboxEnd", 0x2000),
)
Z80_RESERVED_REGIONS = (
    {
        "name": "command_ring",
        "start": 0x1F00,
        "end_exclusive": 0x1FE0,
        "live_data_end_exclusive": 0x1F40,
    },
    {
        "name": "driver_status",
        "start": 0x1FE0,
        "end_exclusive": 0x1FF0,
    },
    {
        "name": "urgent_mailbox",
        "start": 0x1FF0,
        "end_exclusive": 0x2000,
    },
)
SJASMPLUS_ASSEMBLY_FLAGS = (
    "--dirbol",
    "--syntax=abFw",
    "-Wall",
    "--cleanonerror",
)
SYMBOL_LINE = re.compile(r"^([0-9A-Fa-f]{8,16})\s+(\S.*?)\s*$")
SJASM_SYMBOL_LINE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z_.$@?][A-Za-z0-9_.$@?]*)
    \s*:?\s*
    (?:(?:equ|=)\s*)?
    (?P<value>
        0x[0-9A-Fa-f]+
        |\$[0-9A-Fa-f]+
        |[0-9A-Fa-f]+[Hh]
        |[0-9]+
    )
    (?:\s*;\s*.*)?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class BuildFailure(RuntimeError):
    """A deterministic build-stage failure with a useful diagnostic."""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=path.name + ".", suffix=".staged", dir=path.parent, delete=False
    ) as staged:
        staged.write(data)
        staged_path = Path(staged.name)
    os.replace(staged_path, path)


def find_tool_warning(output: str) -> str | None:
    """Return the first assembler warning diagnostic or nonzero warning count."""
    warning_line = re.compile(
        r"(?i)(?:^|\s)warning(?:\s+[A-Za-z0-9_-]+)?\s*(?::|\[)"
    )
    warning_count = re.compile(
        r"(?i)(?:warnings?\s*:\s*|[=:]\s*)([1-9][0-9]*)\s+warnings?\b"
    )
    for line in output.splitlines():
        stripped = line.strip()
        if warning_line.search(stripped) or warning_count.search(stripped):
            return stripped
        summary = re.search(r"(?i)\bwarnings?\s*:\s*([0-9]+)\b", stripped)
        if summary and int(summary.group(1), 10) != 0:
            return stripped
    return None


def run_tool(command: list[str], *, root: Path, log_path: Path) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout, encoding="utf-8", newline="\n")
    if result.returncode:
        quoted = subprocess.list2cmdline(command)
        raise BuildFailure(
            f"tool failed with exit code {result.returncode}: {quoted}\n"
            f"full log: {log_path}"
        )
    warning = find_tool_warning(result.stdout)
    if warning:
        raise BuildFailure(
            f"tool emitted an assembler warning, which is fatal: "
            f"{warning}; full log: {log_path}"
        )
    return result.stdout


def verify_executable(path: Path, command: list[str], expected: str, root: Path) -> str:
    if not path.is_file():
        raise BuildFailure(
            f"missing project-local tool: {path}; run .\\tools\\bootstrap.ps1"
        )
    result = subprocess.run(
        [str(path), *command],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode or expected not in result.stdout:
        raise BuildFailure(
            f"unexpected tool identity for {path} (exit {result.returncode}):\n"
            f"{result.stdout}"
        )
    return result.stdout.strip()


def parse_symbols(listing_path: Path) -> list[tuple[int, str]]:
    symbols: list[tuple[int, str]] = []
    in_value_table = False
    for line in listing_path.read_text("utf-8", errors="replace").splitlines():
        if line.strip() == "Symbols by value:":
            in_value_table = True
            continue
        if not in_value_table:
            continue
        if not line.strip():
            if symbols:
                break
            continue
        match = SYMBOL_LINE.match(line)
        if match:
            symbols.append((int(match.group(1), 16), match.group(2)))
    return symbols


def write_symbol_outputs(
    symbols: list[tuple[int, str]],
    *,
    sym_path: Path,
    map_path: Path,
    assembled_bytes: int,
    configuration: str,
) -> None:
    symbol_lines = [
        f"{address:08X} {name}" for address, name in sorted(symbols, key=lambda item: (item[0], item[1]))
    ]
    sym_path.write_text(
        "\n".join(symbol_lines) + ("\n" if symbol_lines else ""),
        encoding="utf-8",
        newline="\n",
    )
    map_lines = [
        f"RECURSION 94 {configuration} flat ROM symbol map",
        "Format: ADDRESS SYMBOL (extracted from the vasm listing)",
        f"Assembled bytes: {assembled_bytes}",
        "",
    ]
    map_lines.extend(symbol_lines)
    map_path.write_text("\n".join(map_lines) + "\n", encoding="utf-8", newline="\n")


def parse_sjasmplus_symbols(path: Path) -> dict[str, int]:
    """Parse the stable scalar subset written by sjasmplus ``--sym``."""
    parsed: dict[str, int] = {}
    for line in path.read_text("utf-8", errors="replace").splitlines():
        match = SJASM_SYMBOL_LINE.match(line)
        if not match:
            continue
        value_text = match.group("value")
        if value_text.lower().startswith("0x"):
            value = int(value_text[2:], 16)
        elif value_text.startswith("$"):
            value = int(value_text[1:], 16)
        elif value_text[-1:].lower() == "h":
            value = int(value_text[:-1], 16)
        else:
            value = int(value_text, 10)
        parsed[match.group("name")] = value
    if not parsed:
        raise BuildFailure(f"sjasmplus symbol output is empty or unrecognized: {path}")
    return parsed


def inspect_z80_layout(
    *, payload_size: int, symbols: dict[str, int]
) -> dict[str, Any]:
    """Validate the assembled payload and fixed command/status/mailbox regions."""
    if payload_size < 0 or payload_size > MAX_Z80_DRIVER_BYTES:
        raise BuildFailure(
            f"Z80 driver is {payload_size} bytes, exceeding "
            f"${MAX_Z80_DRIVER_BYTES:04X}"
        )
    if payload_size > Z80_DRIVER_STATE_BASE:
        raise BuildFailure(
            f"Z80 driver is {payload_size} bytes and overlaps runtime state "
            f"at ${Z80_DRIVER_STATE_BASE:04X}"
        )

    folded_symbols = {
        name.casefold(): (name, value) for name, value in symbols.items()
    }
    matched_symbols: dict[str, dict[str, Any]] = {}
    for expected_name, expected_value in Z80_LAYOUT_POINTS:
        match = folded_symbols.get(expected_name.casefold())
        if match is None:
            raise BuildFailure(
                f"Z80 layout symbol {expected_name} is missing"
            )
        actual_name, actual_value = match
        if actual_value != expected_value:
            raise BuildFailure(
                f"Z80 layout symbol {actual_name} must be "
                f"${expected_value:04X}, found ${actual_value:04X}"
            )
        matched_symbols[expected_name] = {
            "export": actual_name,
            "value": actual_value,
            "status": "pass",
        }
    image_end_match = folded_symbols.get("driverimageend")
    if image_end_match is None:
        raise BuildFailure("Z80 layout symbol DriverImageEnd is missing")
    image_end_name, image_end_value = image_end_match
    if image_end_value != payload_size:
        raise BuildFailure(
            f"Z80 layout symbol {image_end_name} is ${image_end_value:04X}, "
            f"but raw payload is ${payload_size:04X} bytes"
        )
    matched_symbols["DriverImageEnd"] = {
        "export": image_end_name,
        "value": image_end_value,
        "status": "pass",
    }

    regions: list[dict[str, Any]] = []
    for contract in Z80_RESERVED_REGIONS:
        region = {
            "name": contract["name"],
            "start": contract["start"],
            "end_exclusive": contract["end_exclusive"],
            "bytes": contract["end_exclusive"] - contract["start"],
            "status": "pass",
        }
        if "live_data_end_exclusive" in contract:
            live_end = contract["live_data_end_exclusive"]
            region["live_data_end_exclusive"] = live_end
            region["live_data_bytes"] = live_end - contract["start"]
            region["live_entry_count"] = region["live_data_bytes"] // 4
        regions.append(
            region
        )

    for previous, current in zip(regions, regions[1:]):
        if previous["end_exclusive"] != current["start"]:
            raise BuildFailure(
                f"Z80 reserved layout has a gap or overlap between "
                f"{previous['name']} and {current['name']}"
            )
    if regions[0]["start"] != MAX_Z80_DRIVER_BYTES:
        raise BuildFailure("Z80 payload limit does not meet the reserved layout")
    if regions[-1]["end_exclusive"] != Z80_RAM_BYTES:
        raise BuildFailure("Z80 reserved layout does not end at the RAM boundary")

    return {
        "status": "pass",
        "ram_bytes": Z80_RAM_BYTES,
        "payload_bytes": payload_size,
        "payload_limit": MAX_Z80_DRIVER_BYTES,
        "payload_headroom": MAX_Z80_DRIVER_BYTES - payload_size,
        "runtime_payload_limit": Z80_DRIVER_STATE_BASE,
        "runtime_payload_headroom": Z80_DRIVER_STATE_BASE - payload_size,
        "layout_symbols": matched_symbols,
        "reserved_regions": regions,
    }


def sjasmplus_command(
    *,
    executable: Path,
    root: Path,
    source: Path,
    output: Path,
    listing: Path,
    symbols: Path,
    sld: Path,
    define: str,
) -> list[str]:
    """Construct the frozen sjasmplus 1.23.1 assembly contract."""

    def relative_argument(path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError as error:
            raise BuildFailure(
                f"sjasmplus path must be inside the frozen build root: {path}"
            ) from error

    return [
        relative_argument(executable),
        "--nologo",
        "--fullpath=rel",
        "--color=off",
        f"-D{define}",
        *SJASMPLUS_ASSEMBLY_FLAGS,
        f"--raw={relative_argument(output)}",
        f"--lst={relative_argument(listing)}",
        "--lstlab=sort",
        f"--sym={relative_argument(symbols)}",
        f"--sld={relative_argument(sld)}",
        relative_argument(source),
    ]


def validate_ranges(
    ranges: list[dict[str, Any]], *, capacity: int, unit: str
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ordered = sorted(ranges, key=lambda item: item["start"])
    for item in ordered:
        end = item["start"] + item["size"]
        status = "pass" if item["start"] >= 0 and end <= capacity else "fail"
        checks.append(
            {
                "name": item["name"],
                "start": item["start"],
                "size": item["size"],
                "end_exclusive": end,
                "unit": unit,
                "status": status,
            }
        )
    for previous, current in zip(ordered, ordered[1:]):
        if previous["start"] + previous["size"] > current["start"]:
            checks.append(
                {
                    "name": f"overlap:{previous['name']}:{current['name']}",
                    "status": "fail",
                }
            )
    return checks


def preliminary_reports(
    *,
    root: Path,
    build_root: Path,
    configuration: str,
    rom_report: dict[str, Any],
    z80: dict[str, Any],
    art: dict[str, Any],
    content: dict[str, Any],
) -> None:
    main_ram = [
        {"name": "replay_banks", "start": 0x0000, "size": 0x1400},
        {"name": "timeline_anchors", "start": 0x1400, "size": 0x0400},
        {"name": "actors_intents", "start": 0x1800, "size": 0x0800},
        {"name": "encounter_state", "start": 0x2000, "size": 0x4000},
        {"name": "hash_projection", "start": 0x6000, "size": 0x1000},
        {"name": "reset_staging", "start": 0x7000, "size": 0x1000},
        {"name": "render_sat", "start": 0x8000, "size": 0x1000},
        {"name": "transfer_queue", "start": 0x9000, "size": 0x0800},
        {"name": "scheduler_io_audio", "start": 0x9800, "size": 0x0800},
        {"name": "save_staging", "start": 0xA000, "size": 0x1000},
        {"name": "scratch", "start": 0xB000, "size": 0x2000},
        {"name": "debug_trace", "start": 0xD000, "size": 0x1000},
        {"name": "contingency", "start": 0xE000, "size": 0x1000},
        {"name": "debug_abi", "start": 0xF000, "size": 0x0400},
        {"name": "stack", "start": 0xF400, "size": 0x0C00},
    ]
    memory_checks = validate_ranges(main_ram, capacity=0x10000, unit="byte")
    write_json(
        build_root / "memory-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": "preliminary_contract_pass",
            "address_base": 0x00FF0000,
            "capacity_bytes": 0x10000,
            "regions": memory_checks,
            "measured_high_water": None,
            "pending": [
                "assembled runtime allocation-to-symbol verification",
                "stack low-water measurement",
                "pool high-water measurement",
            ],
        },
    )

    vram = [
        {"name": "font", "start": 0x0000, "size": 0x0800},
        {"name": "ui_shared", "start": 0x0800, "size": 0x1800},
        {"name": "actors", "start": 0x2000, "size": 0x2000},
        {"name": "level_tiles", "start": 0x4000, "size": 0x4000},
        {"name": "effects_boss", "start": 0x8000, "size": 0x2000},
        {"name": "dynamic_tiles", "start": 0xA000, "size": 0x2000},
        {"name": "plane_a", "start": 0xC000, "size": 0x1000},
        {"name": "window", "start": 0xD000, "size": 0x1000},
        {"name": "plane_b", "start": 0xE000, "size": 0x1000},
        {"name": "sat", "start": 0xF000, "size": 0x0280},
        {"name": "transfer_scratch", "start": 0xF280, "size": 0x0980},
        {"name": "hscroll", "start": 0xFC00, "size": 0x0400},
    ]
    write_json(
        build_root / "vram-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": "preliminary_contract_pass",
            "capacity_bytes": 0x10000,
            "regions": validate_ranges(vram, capacity=0x10000, unit="byte"),
            "measured_high_water": None,
            "compiled_art": {
                "source_sha256": art["source_sha256"],
                "bundle_sha256": art["binary_sha256"],
                "bundle_bytes": art["binary_bytes"],
                "tile_bytes": art["compiler_report"]["tiles"]["bytes"],
                "unique_tiles": art["compiler_report"]["tiles"][
                    "unique_tiles_including_transparent"
                ],
                "palette_banks": art["compiler_report"]["palettes"][
                    "emitted_banks"
                ],
                "rom_embedded": art["rom_embedded"],
            },
            "pending": ["asset allocation", "runtime overwrite checks", "scene high water"],
        },
    )

    write_json(
        build_root / "dma-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": "pending_runtime_measurement",
            "queue_capacity_entries": 32,
            "ordinary_word_threshold": 2048,
            "loading_word_threshold": 3200,
            "measured_max_words": None,
            "measured_queue_high_water": None,
            "gate_passed": False,
        },
    )
    write_json(
        build_root / "sprite-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": "pending_runtime_measurement",
            "ordinary_allocation_threshold": 64,
            "hardware_frame_limit": 80,
            "designed_scanline_limit": 18,
            "hardware_scanline_limit": 20,
            "measured_frame_max": None,
            "measured_scanline_max": None,
            "compiled_art": {
                "frame_count": art["compiler_report"]["sprites"]["frame_count"],
                "piece_count": art["compiler_report"]["sprites"]["piece_count"],
                "maximum_pieces_per_frame": art["compiler_report"]["sprites"][
                    "maximum_pieces_per_frame"
                ],
                "animation_count": art["compiler_report"]["animations"]["count"],
            },
            "gate_passed": False,
        },
    )
    write_json(
        build_root / "asset-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": "compiled_and_rom_embedded_pending_runtime_consumption",
            "source_sha256": art["source_sha256"],
            "bundle_sha256": art["binary_sha256"],
            "bundle_bytes": art["binary_bytes"],
            "generated_files_current": art["generated_files_current"],
            "rom_embedded": art["rom_embedded"],
            "rom_offset": art["rom_offset"],
            "rom_end_exclusive": art["rom_end_exclusive"],
            "gate_passed": False,
            "pending": [
                "runtime renderer consumption of compiled bundle",
                "emulator screenshot and scanline validation",
                "independent visual review",
            ],
            "compiler_report": art["compiler_report"],
        },
    )
    write_json(
        build_root / "audio-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": (
                "assembled_driver_pending_runtime"
                if z80["source_present"]
                else "pending_z80_source"
            ),
            "z80": z80,
            "ym2612_smoke": "pending",
            "psg_smoke": "pending",
            "queue_runtime_measurement": "pending",
            "gate_passed": False,
        },
    )

    composition = content["compiler_report"]["composition"]
    write_json(
        build_root / "content-report.json",
        {
            "schema": 1,
            "configuration": configuration,
            "status": "compiled_campaign_pending_runtime_replays",
            "required_primary_stages": 15,
            "required_bosses": 5,
            "compiled_primary_stages": composition["primary"],
            "compiled_bosses": composition["bosses"],
            "compiled_encounters": composition["encounters"],
            "reachable_encounters": composition["reachable_encounters"],
            "campaign_start": composition["campaign_start"],
            "campaign_terminal": composition["campaign_terminal"],
            "source_sha256": content["source_sha256"],
            "binary_sha256": content["binary_sha256"],
            "binary_bytes": content["binary_bytes"],
            "generated_files_current": content["generated_files_current"],
            "runtime_adapter": content["runtime"],
            "canonical_replays_verified": 0,
            "gate_passed": False,
            "pending": [
                "runtime encounter loader",
                "twenty runtime-backed canonical completion replays",
            ],
            "compiler_report": content["compiler_report"],
        },
    )

    write_json(build_root / "rom-report.json", rom_report)


def assemble_z80(
    *, root: Path, build_root: Path, sjasmplus: Path, configuration: str
) -> dict[str, Any]:
    source = root / "src" / "audio" / "z80_driver.asm"
    if not source.is_file():
        return {
            "source_present": False,
            "status": "not_built_optional_milestone_source_absent",
            "bytes": 0,
            "sha256": None,
            "maximum_driver_bytes": MAX_Z80_DRIVER_BYTES,
        }

    generated_root = root / "assets" / "generated"
    generated_root.mkdir(parents=True, exist_ok=True)
    output = generated_root / "z80_driver.bin"
    listing = build_root / f"z80-driver-{configuration.lower()}.lst"
    symbols = build_root / f"z80-driver-{configuration.lower()}.sym"
    sld = build_root / f"z80-driver-{configuration.lower()}.sld"
    log = build_root / f"z80-driver-{configuration.lower()}.build.log"
    define = f"BUILD_{configuration.upper()}=1"
    command = sjasmplus_command(
        executable=sjasmplus,
        root=root,
        source=source,
        output=output,
        listing=listing,
        symbols=symbols,
        sld=sld,
        define=define,
    )
    run_tool(command, root=root, log_path=log)
    for expected_output in (output, listing, symbols, sld):
        if not expected_output.is_file():
            raise BuildFailure(
                f"sjasmplus did not create required output: {expected_output}"
            )
    payload = output.read_bytes()
    layout = inspect_z80_layout(
        payload_size=len(payload),
        symbols=parse_sjasmplus_symbols(symbols),
    )
    return {
        "source_present": True,
        "status": "assembled",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "maximum_driver_bytes": MAX_Z80_DRIVER_BYTES,
        "output": output.relative_to(root).as_posix(),
        "listing": listing.relative_to(root).as_posix(),
        "symbols": symbols.relative_to(root).as_posix(),
        "sld": sld.relative_to(root).as_posix(),
        "layout": layout,
    }


def compile_campaign_content(*, root: Path) -> dict[str, Any]:
    """Generate and recheck both campaign source and cartridge-runtime data."""
    content_root = root / "content"
    output_root = root / "assets" / "generated" / "content"
    bundle = compile_content(content_root, output_root)
    checked = check_generated_content(content_root, output_root)
    if checked.files != bundle.files:
        raise BuildFailure("campaign compiler is not byte-deterministic")
    binary = bundle.files[CONTENT_OUTPUT_BINARY]
    verify_content_binary(binary)
    try:
        compiler_report = json.loads(
            bundle.files[CONTENT_OUTPUT_REPORT].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildFailure(
            f"campaign compiler emitted an invalid report: {error}"
        ) from error
    composition = compiler_report.get("composition")
    if not isinstance(composition, dict) or (
        composition.get("encounters"),
        composition.get("primary"),
        composition.get("bosses"),
        composition.get("reachable_encounters"),
    ) != (20, 15, 5, 20):
        raise BuildFailure(
            "campaign compiler did not emit the required reachable 15+5 roster"
        )

    runtime_output_root = root / "assets" / "generated" / "campaign_runtime"
    runtime = compile_runtime(content_root, runtime_output_root)
    runtime_checked = check_generated_campaign_runtime(
        content_root,
        runtime_output_root,
    )
    if runtime_checked.files != runtime.files:
        raise BuildFailure("campaign runtime adapter is not byte-deterministic")
    try:
        runtime_report = json.loads(
            runtime.files[CAMPAIGN_RUNTIME_OUTPUT_REPORT].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildFailure(
            f"campaign runtime compiler emitted an invalid report: {error}"
        ) from error
    if (
        runtime_report.get("status") != "pass"
        or runtime.encounter_count != 20
        or runtime_report.get("encounter_count") != 20
    ):
        raise BuildFailure(
            "campaign runtime compiler did not emit the required 20 encounters"
        )
    runtime_assembly = runtime.files[CAMPAIGN_RUNTIME_OUTPUT_ASSEMBLY]
    return {
        "status": "pass",
        "binary_bytes": len(binary),
        "binary_sha256": bundle.binary_sha256,
        "source_sha256": bundle.source_sha256,
        "generated_files_current": True,
        "compiler_report": compiler_report,
        "runtime": {
            "format_version": runtime_report.get("format_version"),
            "assembly_bytes": len(runtime_assembly),
            "assembly_sha256": runtime.assembly_sha256,
            "contract_sha256": runtime.contract_sha256,
            "encounter_count": runtime.encounter_count,
            "generated_files_current": True,
            "compiler_report": runtime_report,
        },
    }


def compile_art_assets(*, root: Path) -> dict[str, Any]:
    """Generate, recheck, and structurally verify the original Genesis art."""
    art_root = root / "assets" / "source" / "art"
    map_root = root / "assets" / "source" / "maps"
    output_root = root / "assets" / "generated" / "art"
    evidence_path = root / "artifacts" / "reports" / "asset-compile.json"
    bundle = compile_assets(art_root, map_root, output_root, evidence_path)
    checked = check_generated_art(
        art_root,
        map_root,
        output_root,
        evidence_path,
    )
    if checked.files != bundle.files:
        raise BuildFailure("art compiler is not byte-deterministic")
    try:
        compiler_report = json.loads(
            bundle.files[ART_OUTPUT_REPORT].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildFailure(f"art compiler emitted an invalid report: {error}") from error
    if compiler_report.get("status") != "PASS":
        raise BuildFailure("art compiler did not pass its declared budgets")
    palettes = compiler_report.get("palettes", {})
    tilemaps = compiler_report.get("tilemaps", {})
    sprites = compiler_report.get("sprites", {})
    animations = compiler_report.get("animations", {})
    if (
        palettes.get("emitted_banks") != 4
        or int(tilemaps.get("count", 0)) < 1
        or int(sprites.get("frame_count", 0)) < 7
        or int(animations.get("count", 0)) < 1
    ):
        raise BuildFailure(
            "art compiler omitted required CRAM, room, echo, or animation assets"
        )
    binary = bundle.files[ART_OUTPUT_BUNDLE]
    return {
        "status": "pass",
        "binary_bytes": len(binary),
        "binary_sha256": bundle.bundle_sha256,
        "source_sha256": bundle.source_sha256,
        "generated_files_current": True,
        "compiler_report": compiler_report,
    }


def compile_crc32_lookup_tables(*, root: Path) -> dict[str, Any]:
    """Generate and byte-recheck the ROM-resident CRC acceleration tables."""
    output_root = root / "assets" / "generated" / "crc32"
    bundle = compile_tables(output_root)
    checked = check_generated_crc32(output_root)
    if checked.files != bundle.files:
        raise BuildFailure("CRC32 table generator is not byte-deterministic")
    try:
        compiler_report = json.loads(
            bundle.files["crc32_tables.json"].decode("ascii")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BuildFailure(
            f"CRC32 table generator emitted an invalid report: {error}"
        ) from error
    if (
        compiler_report.get("status") != "PASS"
        or compiler_report.get("binary_bytes") != bundle.binary_bytes
        or compiler_report.get("binary_sha256") != bundle.binary_sha256
    ):
        raise BuildFailure("CRC32 table generator report is inconsistent")
    return {
        "status": "pass",
        "binary_bytes": bundle.binary_bytes,
        "binary_sha256": bundle.binary_sha256,
        "table_sha256": dict(bundle.table_sha256),
        "generated_files_current": True,
        "compiler_report": compiler_report,
    }


def resolve_start_encounter(
    content: dict[str, Any], requested: str | None
) -> tuple[str | None, int]:
    """Resolve an optional debug boot encounter against the compiled roster."""
    if requested is None:
        return None, 0
    encounter_id = requested.strip().upper()
    if not encounter_id:
        raise BuildFailure("start encounter must not be empty")
    runtime_report = content.get("runtime", {}).get("compiler_report", {})
    encounters = runtime_report.get("encounters", [])
    if not isinstance(encounters, list):
        raise BuildFailure("campaign runtime report has no encounter roster")
    roster: list[str] = []
    for directory_index, item in enumerate(encounters):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise BuildFailure("campaign runtime report has an invalid encounter roster")
        if item.get("directory_index") != directory_index:
            raise BuildFailure(
                "campaign runtime report encounter directory order is inconsistent"
            )
        roster.append(item["id"])
    if len(roster) != len(set(roster)):
        raise BuildFailure("campaign runtime report has duplicate encounter IDs")
    try:
        index = roster.index(encounter_id)
    except ValueError as error:
        valid = ", ".join(roster)
        raise BuildFailure(
            f"unknown start encounter {encounter_id!r}; expected one of: {valid}"
        ) from error
    return encounter_id, index


def build(
    root: Path,
    configuration: str,
    *,
    start_encounter: str | None = None,
) -> Path:
    configuration = configuration.capitalize()
    if configuration not in {"Debug", "Release"}:
        raise BuildFailure(f"unsupported configuration: {configuration}")
    if start_encounter is not None and configuration != "Debug":
        raise BuildFailure(
            "--start-encounter is a Debug instrumentation feature; "
            "the canonical Release ROM always boots the campaign entry"
        )

    source = root / "src" / "main.asm"
    if not source.is_file():
        raise BuildFailure(f"missing 68000 translation unit: {source}")

    canonical_build_root = root / "build"
    canonical_build_root.mkdir(parents=True, exist_ok=True)
    vasm = root / ".toolcache" / "bin" / "vasmm68k_mot.exe"
    sjasmplus = root / ".toolcache" / "bin" / "sjasmplus.exe"
    vasm_version = verify_executable(vasm, ["-v"], VASM_VERSION_TOKEN, root)
    sjasm_version = verify_executable(
        sjasmplus, ["--nologo", "--version"], SJASMPLUS_VERSION_TOKEN, root
    )

    print(
        "NOTICE: pinned vasm is licensed for non-commercial use; this build is "
        "not evidence of commercial distribution clearance."
    )
    crc32_tables = compile_crc32_lookup_tables(root=root)
    art = compile_art_assets(root=root)
    content = compile_campaign_content(root=root)
    encounter_id, encounter_index = resolve_start_encounter(
        content, start_encounter
    )
    build_root = (
        canonical_build_root
        if encounter_id is None
        else canonical_build_root / f"level-{encounter_id}"
    )
    build_root.mkdir(parents=True, exist_ok=True)
    z80 = assemble_z80(
        root=root,
        build_root=build_root,
        sjasmplus=sjasmplus,
        configuration=configuration,
    )

    stem = f"recursion94-{configuration.lower()}"
    raw_path = build_root / f"{stem}.raw"
    listing_path = build_root / f"{stem}.lst"
    symbol_path = build_root / f"{stem}.sym"
    map_path = build_root / f"{stem}.map"
    log_path = build_root / f"{stem}.build.log"
    dependency_path = build_root / f"{stem}.d"
    output_path = build_root / f"{stem}.bin"
    defines = [f"BUILD_{configuration.upper()}=1"]
    if encounter_id is not None:
        defines.append(f"DEBUG_START_ENCOUNTER={encounter_index}")
    command = [
        str(vasm),
        "-Fbin",
        "-m68000",
        "-no-opt",
        "-x",
        *(f"-D{define}" for define in defines),
        "-Isrc",
        "-dependall=make",
        "-depfile",
        str(dependency_path.relative_to(root)),
        "-L",
        str(listing_path.relative_to(root)),
        "-Lall",
        "-o",
        str(raw_path.relative_to(root)),
        str(source.relative_to(root)),
    ]
    run_tool(command, root=root, log_path=log_path)
    raw = raw_path.read_bytes()
    if len(raw) < 0x200:
        raise BuildFailure(
            f"assembled image is {len(raw)} bytes and does not contain a full header"
        )
    if len(raw) > DEFAULT_ROM_SIZE:
        raise BuildFailure(
            f"assembled image is {len(raw)} bytes, exceeding fixed 1 MiB cartridge"
        )

    rom = bytearray(raw)
    rom.extend(b"\xFF" * (DEFAULT_ROM_SIZE - len(rom)))
    checksum = patch_checksum(rom, declared_end=DEFAULT_ROM_SIZE - 1)
    write_bytes_atomic(output_path, rom)

    try:
        static_report = validate_rom(
            bytes(rom),
            expected_size=DEFAULT_ROM_SIZE,
            assembled_bytes=len(raw),
            require_all_vectors=True,
        )
    except RomValidationError as error:
        report = getattr(error, "report", None)
        if report is not None:
            write_json(build_root / "rom-report.json", report)
        raise BuildFailure(f"constructed ROM failed independent validation: {error}") from error

    symbols = parse_symbols(listing_path)
    write_symbol_outputs(
        symbols,
        sym_path=symbol_path,
        map_path=map_path,
        assembled_bytes=len(raw),
        configuration=configuration,
    )
    symbol_values = {name: address for address, name in symbols}
    try:
        art_start = symbol_values["ArtBundle"]
        art_end = symbol_values["ArtBundleEnd"]
    except KeyError as error:
        raise BuildFailure(
            f"assembled ROM omitted generated art symbol: {error.args[0]}"
        ) from error
    art_binary = (root / "assets/generated/art" / ART_OUTPUT_BUNDLE).read_bytes()
    if (
        art_end - art_start != len(art_binary)
        or raw[art_start:art_end] != art_binary
    ):
        raise BuildFailure("assembled ROM art bundle is missing, truncated, or altered")
    art["rom_embedded"] = True
    art["rom_offset"] = art_start
    art["rom_end_exclusive"] = art_end
    try:
        crc32_start = symbol_values["Crc32Tables"]
        crc32_end = symbol_values["Crc32TablesEnd"]
    except KeyError as error:
        raise BuildFailure(
            f"assembled ROM omitted generated CRC32 symbol: {error.args[0]}"
        ) from error
    crc32_binary = (
        root / "assets/generated/crc32" / CRC32_OUTPUT_BINARY
    ).read_bytes()
    if (
        crc32_end - crc32_start != len(crc32_binary)
        or raw[crc32_start:crc32_end] != crc32_binary
    ):
        raise BuildFailure(
            "assembled ROM CRC32 tables are missing, duplicated, truncated, or altered"
        )
    crc32_tables["rom_embedded"] = True
    crc32_tables["rom_offset"] = crc32_start
    crc32_tables["rom_end_exclusive"] = crc32_end
    shutil.copyfile(listing_path, build_root / "recursion94.lst")
    shutil.copyfile(map_path, build_root / "recursion94.map")

    rom_report = {
        **static_report,
        "configuration": configuration,
        "debug_start_encounter": encounter_id,
        "debug_start_encounter_index": (
            encounter_index if encounter_id is not None else None
        ),
        "artifact": output_path.relative_to(root).as_posix(),
        "assembled_artifact": raw_path.relative_to(root).as_posix(),
        "listing": listing_path.relative_to(root).as_posix(),
        "symbols": symbol_path.relative_to(root).as_posix(),
        "map": map_path.relative_to(root).as_posix(),
        "padded_fill_byte": 0xFF,
        "patched_checksum": checksum,
        "assembler": {
            "vasm": vasm_version.splitlines(),
            "sjasmplus": sjasm_version.splitlines(),
            "license_limitation": (
                "vasm upstream license is non-commercial for this Genesis target; "
                "commercial distribution clearance remains unresolved"
            ),
        },
        "z80": z80,
        "art": art,
        "crc32_tables": crc32_tables,
    }
    preliminary_reports(
        root=root,
        build_root=build_root,
        configuration=configuration,
        rom_report=rom_report,
        z80=z80,
        art=art,
        content=content,
    )

    print(
        f"{configuration} ROM: {output_path} "
        f"({len(rom)} bytes, checksum ${checksum:04X}, "
        f"sha256 {hashlib.sha256(rom).hexdigest()})"
    )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--configuration", required=True, choices=("Debug", "Release", "debug", "release")
    )
    parser.add_argument(
        "--start-encounter",
        help=(
            "Debug-only encounter ID to boot through the real campaign loader; "
            "writes an isolated build/level-<ID>/ fixture"
        ),
    )
    args = parser.parse_args()
    try:
        build(
            args.root.resolve(),
            args.configuration,
            start_encounter=args.start_encounter,
        )
    except (
        BuildFailure,
        ContentCompileError,
        RuntimeCompileError,
        Crc32TableError,
        OSError,
        ValueError,
    ) as error:
        print(f"BUILD FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
