#!/usr/bin/env python3
"""Record immutable metadata for manually inspected Milestone 1 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rom_tools.rom_image import validate_rom  # noqa: E402


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--screenshot", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--observation", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    rom = args.rom.resolve()
    screenshot = args.screenshot.resolve()
    payload = screenshot.read_bytes()
    if len(payload) < 24 or payload[:8] != PNG_SIGNATURE or payload[12:16] != b"IHDR":
        raise SystemExit(f"not a structurally recognizable PNG: {screenshot}")
    width, height = struct.unpack_from(">II", payload, 16)
    rom_report = validate_rom(rom.read_bytes(), require_all_vectors=True)
    report = {
        "schema": 1,
        "status": "pass",
        "evidence_type": "manually_inspected_boot_screenshot",
        "rom": {
            "path": rom.relative_to(root).as_posix(),
            "sha256": rom_report["facts"]["sha256"],
        },
        "screenshot": {
            "path": screenshot.relative_to(root).as_posix(),
            "sha256": sha256(screenshot),
            "bytes": len(payload),
            "width": width,
            "height": height,
        },
        "observations": args.observation,
        "scope_limit": (
            "This proves one visible Milestone 1 diagnostic frame only. It is "
            "not the later independent visual-QA rubric, gameplay, or determinism gate."
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Recorded screenshot evidence: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
