"""CLI for deterministic historical M3 host-reference regression."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.m3_reference.report import (  # noqa: E402
        build_reference_report,
        write_reference_report,
    )
else:
    from .report import build_reference_report, write_reference_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the historical M3 model against compatible declarative "
            "inputs; this does not emit current-ROM evidence."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: inferred from this module)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/reports/m3-reference-regression.json"),
        help="report path, relative to --root unless absolute",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="also print the complete JSON report",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    root = arguments.root.resolve()
    output = arguments.output
    if not output.is_absolute():
        output = root / output
    report = build_reference_report(root)
    write_reference_report(output, report)
    if arguments.stdout:
        print(json.dumps(report, indent=2, sort_keys=True))
    print(
        "M3 historical reference regression: PASS "
        f"binding={report['binding_sha256']} "
        f"terminal={report['canonical_script']['terminal_crc32_iso_hdlc']} "
        f"report={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
