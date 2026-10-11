#!/usr/bin/env python3
"""Generate deterministic Motorola 68000 CRC-32 lookup data.

The T16 table implements two reflected CRC-32/ISO-HDLC byte updates at once.
Its 16-bit index is the native big-endian payload word XORed with the low word
of T(raw CRC), where T swaps bytes within each 16-bit halfword. T16 outputs use
that same representation; byte and zero-run tables retain ordinary raw CRC.
The byte table handles unaligned prefixes and odd tails.  The
projection tables implement the linear transition for power-of-two and common
composite runs of 24-byte all-zero object records. A transition record contains
four big-endian longs, one for each byte position of an arbitrary raw
(pre-final-XOR) CRC state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


POLYNOMIAL_REFLECTED = 0xEDB88320
BYTE_TABLE_ENTRIES = 256
T16_TABLE_ENTRIES = 65_536
TABLE_WORD_BYTES = 4
BYTE_TABLE_BYTES = BYTE_TABLE_ENTRIES * TABLE_WORD_BYTES
T16_TABLE_BYTES = T16_TABLE_ENTRIES * TABLE_WORD_BYTES
OBJECT_RECORD_BYTES = 24
ZERO_RECORD_POWERS = (1, 2, 4, 8, 16, 32)
ZERO_RECORD_DIRECT_RUNS = (7, 12, 15, 24, 31, 27)
ZERO_RECORD_TABLE_COUNTS = ZERO_RECORD_POWERS + ZERO_RECORD_DIRECT_RUNS
TRANSITION_TABLE_BYTES = BYTE_TABLE_ENTRIES * 4 * TABLE_WORD_BYTES

OUTPUT_BINARY = "crc32_tables.bin"
OUTPUT_INCLUDE = "crc32_tables.inc.asm"
OUTPUT_REPORT = "crc32_tables.json"
OUTPUT_NAMES = frozenset((OUTPUT_BINARY, OUTPUT_INCLUDE, OUTPUT_REPORT))
REPORT_SCHEMA = "r94-crc32-tables-v4"
T16_CRC_REPRESENTATION = "raw_crc_byte_swapped_within_each_16_bit_halfword"
NTSC_FRAME_CYCLES = 128_005
NTSC_65_PERCENT_CYCLES = 83_203


class Crc32TableError(RuntimeError):
    """Generated CRC data is missing, stale, or structurally invalid."""


@dataclass(frozen=True)
class Crc32TableBundle:
    files: Mapping[str, bytes]
    binary_sha256: str
    binary_bytes: int
    table_sha256: Mapping[str, str]


def crc32_update_byte(raw_crc: int, value: int) -> int:
    """Update an unfinalized reflected CRC with one byte."""
    crc = (raw_crc ^ value) & 0xFFFFFFFF
    for _bit in range(8):
        crc = (
            (crc >> 1) ^ POLYNOMIAL_REFLECTED
            if crc & 1
            else crc >> 1
        )
    return crc & 0xFFFFFFFF


def crc32_update_raw(raw_crc: int, payload: bytes) -> int:
    """Update an unfinalized reflected CRC with an arbitrary byte string."""
    crc = raw_crc & 0xFFFFFFFF
    for value in payload:
        crc = crc32_update_byte(crc, value)
    return crc


def crc32_iso_hdlc(payload: bytes) -> int:
    """Return CRC-32/ISO-HDLC using init/xorout $FFFFFFFF."""
    return crc32_update_raw(0xFFFFFFFF, payload) ^ 0xFFFFFFFF


def estimate_neutral_projection_cycles() -> dict[str, int | str]:
    """Retained conservative component-only bound for neutral B01 projection.

    This is a pre-optimization source-level bound, not emulator timing evidence.
    No transformed-T16, contiguous-copy or HWM-elision savings are credited;
    its integration contingency also covers the added short-stream conversion
    and direct-run dispatch overhead. It must not stand in for current native
    whole-frame measurements.
    Counts use original-68000 timings, charge every DBF as its slower terminal
    path, charge every conditional branch at its slower direction, and include
    a separate 4,096-cycle integration contingency.  The steady projection has
    four active and 136 already-zero object records in seven coalesced runs.
    Six composite runs use direct tables, while the 32-record power-of-two run
    uses the bounded fallback. Pool serialization classifies those runs while
    writing, so there is no second 140-slot CRC scan. This bound covers only
    EngineBuildProjection; it excludes gameplay, rendering, interrupts, and
    reset work.
    """

    serialization = {
        "header_and_actors": 2_714,
        "pool_control_calls_and_registers": 2_438,
        "inactive_record_elision_and_run_classification": 8_976,
        "active_records": 1_072,
        "size_validation_and_crc_finalize": 102,
    }
    accelerated_crc = {
        "t16_fixed_headers_and_active_records": 21_612,
        "twelve_stream_call_boundaries": 720,
        "seven_zero_transitions": 1_974,
        "seven_direct_match_dispatches_worst_case": 700,
        "one_power_fallback_dispatch_worst_case": 164,
    }
    serialization_cycles = sum(serialization.values())
    crc_cycles = sum(accelerated_crc.values())
    contingency_cycles = 4_096
    total_cycles = serialization_cycles + crc_cycles + contingency_cycles
    pre_t16_fused_bound_cycles = 69_532
    pre_direct_run_reported_bound_cycles = 48_632
    return {
        "method": (
            "retained pre-optimization conservative original-68000 upper bound; "
            "component-only, not whole-frame timing evidence"
        ),
        "candidate_optimization_savings_credited": False,
        "scope": (
            "EngineBuildProjection only; excludes gameplay, renderer, "
            "interrupt, and reset costs"
        ),
        "neutral_projection_bytes": 3_638,
        "neutral_active_object_records": 4,
        "neutral_zero_object_records": 136,
        "neutral_zero_run_lengths": "15,32,31,7,15,12,24",
        "neutral_direct_composite_runs": 6,
        "neutral_power_fallback_runs": 1,
        "neutral_zero_transition_applications": 7,
        "second_slot_scan_records": 0,
        "serialization_cycles": serialization_cycles,
        "crc_cycles": crc_cycles,
        "integration_contingency_cycles": contingency_cycles,
        "total_cycles": total_cycles,
        "pre_direct_run_reported_bound_cycles": (
            pre_direct_run_reported_bound_cycles
        ),
        "direct_run_bound_reduction_cycles": (
            pre_direct_run_reported_bound_cycles - total_cycles
        ),
        "pre_t16_fused_bound_cycles": pre_t16_fused_bound_cycles,
        "conservative_bound_reduction_cycles": (
            pre_t16_fused_bound_cycles - total_cycles
        ),
        "ntsc_65_percent_limit_cycles": NTSC_65_PERCENT_CYCLES,
        "headroom_to_65_percent_limit_cycles": (
            NTSC_65_PERCENT_CYCLES - total_cycles
        ),
        "ntsc_frame_cycles": NTSC_FRAME_CYCLES,
        "headroom_to_full_frame_cycles": NTSC_FRAME_CYCLES - total_cycles,
    }


def _u32be(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


def _byte_table() -> bytes:
    return b"".join(
        _u32be(crc32_update_byte(0, index))
        for index in range(BYTE_TABLE_ENTRIES)
    )


def swap_bytes_within_halfwords(value: int) -> int:
    """Linear involution T; commutes with XOR and an unsigned 16-bit shift."""
    return ((value & 0x00FF00FF) << 8) | ((value & 0xFF00FF00) >> 8)


def _t16_table() -> bytes:
    return b"".join(
        _u32be(
            swap_bytes_within_halfwords(
                crc32_update_raw(0, index.to_bytes(2, "big"))
            )
        )
        for index in range(T16_TABLE_ENTRIES)
    )


def _zero_transition_table(zero_bytes: int) -> bytes:
    zeros = bytes(zero_bytes)
    output = bytearray()
    for index in range(BYTE_TABLE_ENTRIES):
        for shift in (0, 8, 16, 24):
            output.extend(
                _u32be(crc32_update_raw(index << shift, zeros))
            )
    return bytes(output)


def _table_layout() -> tuple[bytes, list[dict[str, int | str]]]:
    binary = bytearray()
    layout: list[dict[str, int | str]] = []

    t16_table = _t16_table()
    layout.append(
        {
            "name": "t16",
            "entries": T16_TABLE_ENTRIES,
            "index_byte_order": "big",
            "crc_representation": T16_CRC_REPRESENTATION,
            "offset": len(binary),
            "bytes": len(t16_table),
            "sha256": hashlib.sha256(t16_table).hexdigest(),
        }
    )
    binary.extend(t16_table)

    byte_table = _byte_table()
    layout.append(
        {
            "name": "byte",
            "offset": len(binary),
            "bytes": len(byte_table),
            "sha256": hashlib.sha256(byte_table).hexdigest(),
        }
    )
    binary.extend(byte_table)

    for records in ZERO_RECORD_TABLE_COUNTS:
        zero_bytes = records * OBJECT_RECORD_BYTES
        table = _zero_transition_table(zero_bytes)
        layout.append(
            {
                "name": f"zero_records_{records}",
                "records": records,
                "zero_bytes": zero_bytes,
                "offset": len(binary),
                "bytes": len(table),
                "sha256": hashlib.sha256(table).hexdigest(),
            }
        )
        binary.extend(table)

    return bytes(binary), layout


def _render_include(binary_sha256: str, layout: list[dict[str, int | str]]) -> bytes:
    entries = {str(item["name"]): item for item in layout}
    lines = [
        "; Generated by tools/crc32_tables.py. DO NOT EDIT.",
        f"; crc32_tables.bin sha256: {binary_sha256}",
        "",
        f"CRC32_TABLE_BINARY_BYTES        equ     ${sum(int(item['bytes']) for item in layout):05X}",
        f"CRC32_T16_TABLE_BYTES           equ     ${int(entries['t16']['bytes']):05X}",
        f"CRC32_BYTE_TABLE_BYTES          equ     ${int(entries['byte']['bytes']):04X}",
        f"CRC32_ZERO_TABLE_BYTES          equ     ${TRANSITION_TABLE_BYTES:04X}",
        f"CRC32_ZERO_RECORD_BYTES         equ     ${OBJECT_RECORD_BYTES:04X}",
        "",
        "        even",
        "Crc32Tables:",
        f'        incbin "assets/generated/crc32/{OUTPUT_BINARY}"',
        "Crc32TablesEnd:",
        "",
        f"Crc32T16Table                   equ     Crc32Tables+${int(entries['t16']['offset']):05X}",
        f"Crc32ByteTable                  equ     Crc32Tables+${int(entries['byte']['offset']):05X}",
    ]
    for records in ZERO_RECORD_TABLE_COUNTS:
        item = entries[f"zero_records_{records}"]
        lines.append(
            "Crc32ZeroRecords"
            f"{records}Table"
            f"{' ' * max(1, 28 - len(f'Crc32ZeroRecords{records}Table'))}"
            f"equ     Crc32Tables+${int(item['offset']):05X}"
        )
    lines.extend(
        (
            "",
            "        assert  Crc32TablesEnd-Crc32Tables=CRC32_TABLE_BINARY_BYTES,\"CRC32 generated table size mismatch\"",
            "        assert  CRC32_T16_TABLE_BYTES=$40000,\"CRC32 T16 table size drift\"",
            "        assert  CRC32_BYTE_TABLE_BYTES=$0400,\"CRC32 byte table size drift\"",
            "        assert  CRC32_ZERO_TABLE_BYTES=$1000,\"CRC32 zero-transition table size drift\"",
            "",
        )
    )
    return "\n".join(lines).encode("ascii")


def build_tables() -> Crc32TableBundle:
    binary, layout = _table_layout()
    binary_sha256 = hashlib.sha256(binary).hexdigest()
    include = _render_include(binary_sha256, layout)
    table_hashes = {
        str(item["name"]): str(item["sha256"])
        for item in layout
    }
    report = {
        "schema": REPORT_SCHEMA,
        "status": "PASS",
        "algorithm": "CRC-32/ISO-HDLC",
        "polynomial_reflected": f"{POLYNOMIAL_REFLECTED:08X}",
        "init": "FFFFFFFF",
        "xorout": "FFFFFFFF",
        "object_record_bytes": OBJECT_RECORD_BYTES,
        "t16_entries": T16_TABLE_ENTRIES,
        "t16_index_byte_order": "big",
        "t16_crc_representation": T16_CRC_REPRESENTATION,
        "byte_and_zero_tables_crc_representation": "ordinary_raw_crc",
        "zero_record_powers": list(ZERO_RECORD_POWERS),
        "zero_record_direct_runs": list(ZERO_RECORD_DIRECT_RUNS),
        "zero_record_table_counts": list(ZERO_RECORD_TABLE_COUNTS),
        "binary_bytes": len(binary),
        "binary_sha256": binary_sha256,
        "known_vectors": {
            "empty": f"{crc32_iso_hdlc(b''):08X}",
            "123456789": f"{crc32_iso_hdlc(b'123456789'):08X}",
        },
        "neutral_b01_cycle_estimate": estimate_neutral_projection_cycles(),
        "tables": layout,
    }
    report_bytes = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    files = {
        OUTPUT_BINARY: binary,
        OUTPUT_INCLUDE: include,
        OUTPUT_REPORT: report_bytes,
    }
    return Crc32TableBundle(
        files=files,
        binary_sha256=binary_sha256,
        binary_bytes=len(binary),
        table_sha256=table_hashes,
    )


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".staged",
        dir=path.parent,
        delete=False,
    ) as staged:
        staged.write(data)
        staged_path = Path(staged.name)
    os.replace(staged_path, path)


def compile_tables(output_dir: Path | str) -> Crc32TableBundle:
    destination = Path(output_dir)
    bundle = build_tables()
    destination.mkdir(parents=True, exist_ok=True)
    unexpected = {
        path.name for path in destination.iterdir() if path.is_file()
    } - OUTPUT_NAMES
    if unexpected:
        raise Crc32TableError(
            f"unexpected generated CRC entries: {sorted(unexpected)}"
        )
    for name, data in bundle.files.items():
        _write_atomic(destination / name, data)
    return bundle


def check_generated(output_dir: Path | str) -> Crc32TableBundle:
    destination = Path(output_dir)
    if not destination.is_dir():
        raise Crc32TableError(
            f"generated CRC directory does not exist: {destination}"
        )
    expected = build_tables()
    actual_names = {
        path.name for path in destination.iterdir() if path.is_file()
    }
    if actual_names != OUTPUT_NAMES:
        raise Crc32TableError(
            "generated CRC file set mismatch; "
            f"expected={sorted(OUTPUT_NAMES)}, actual={sorted(actual_names)}"
        )
    for name, data in expected.files.items():
        if (destination / name).read_bytes() != data:
            raise Crc32TableError(
                f"generated CRC output is stale or corrupted: {destination / name}"
            )
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("assets/generated/crc32"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        bundle = (
            check_generated(args.output_dir)
            if args.check
            else compile_tables(args.output_dir)
        )
    except (Crc32TableError, OSError, ValueError) as error:
        print(f"CRC32 TABLES FAILED: {error}")
        return 1
    action = "current" if args.check else "generated"
    print(
        f"CRC32 tables {action}: {bundle.binary_bytes} bytes, "
        f"sha256 {bundle.binary_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
