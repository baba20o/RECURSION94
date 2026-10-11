#!/usr/bin/env python3
"""Command-line independent static Genesis ROM validator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rom_tools.rom_image import RomValidationError, validate_rom  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rom", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-size", type=lambda value: int(value, 0), default=0x100000)
    parser.add_argument("--assembled-bytes", type=lambda value: int(value, 0))
    args = parser.parse_args()

    report: dict[str, object]
    try:
        payload = args.rom.read_bytes()
        report = validate_rom(
            payload,
            expected_size=args.expected_size,
            assembled_bytes=args.assembled_bytes,
            require_all_vectors=True,
        )
        code = 0
    except RomValidationError as error:
        report = getattr(
            error,
            "report",
            {
                "schema": 1,
                "status": "fail",
                "issues": [issue.as_dict() for issue in error.issues],
            },
        )
        code = 1
    except OSError as error:
        report = {
            "schema": 1,
            "status": "fail",
            "issues": [
                {"check": "rom_read", "severity": "error", "message": str(error)}
            ],
        }
        code = 1

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8", newline="\n")
    if code:
        print(text, file=sys.stderr, end="")
    else:
        print(
            f"ROM static validation: PASS ({args.rom}, "
            f"sha256 {report['facts']['sha256']})"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
