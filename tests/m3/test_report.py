from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.m3_reference.report import (
    FROZEN_REPORT_SCHEMA,
    GOLDEN_TERMINAL_CRC,
    REPORT_SCHEMA,
    build_reference_report,
    compute_report_binding,
)


ROOT = Path(__file__).resolve().parents[2]
COMMITTED_REPORT = ROOT / "artifacts/reports/m3-reference.json"


class ReferenceReportTests(unittest.TestCase):
    def test_report_is_deterministic_and_self_bound(self) -> None:
        first = build_reference_report(ROOT)
        second = build_reference_report(ROOT)
        self.assertEqual(first, second)
        binding = first.pop("binding_sha256")
        self.assertEqual(binding, compute_report_binding(first))
        self.assertEqual(first["schema"], REPORT_SCHEMA)
        self.assertEqual(first["status"], "pass")
        self.assertEqual(
            first["classification"], "historical_model_regression"
        )
        self.assertFalse(first["current_runtime_equivalence_claimed"])
        self.assertFalse(first["exact_frozen_source_match"])
        self.assertEqual(first["projection"]["bytes"], 3638)
        self.assertEqual(
            first["canonical_script"]["terminal_crc32_iso_hdlc"],
            f"{GOLDEN_TERMINAL_CRC:08X}",
        )
        self.assertTrue(first["canonical_script"]["mutated_diverged"])

    def test_cli_writes_same_report_without_external_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "m3-reference.json"
            command = [
                sys.executable,
                str(ROOT / "tools/m3_reference/__main__.py"),
                "--root",
                str(ROOT),
                "--output",
                str(output),
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "M3 historical reference regression: PASS",
                completed.stdout,
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                build_reference_report(ROOT),
            )

    def test_committed_report_remains_self_bound_frozen_history(self) -> None:
        self.assertTrue(COMMITTED_REPORT.is_file())
        committed = json.loads(COMMITTED_REPORT.read_text(encoding="utf-8"))
        binding = committed.pop("binding_sha256")
        self.assertEqual(binding, compute_report_binding(committed))
        self.assertEqual(FROZEN_REPORT_SCHEMA, committed["schema"])
        self.assertEqual("pass", committed["status"])
        from tools.m3_reference.contracts import RUNTIME_SOURCE_SHA256

        self.assertEqual(
            dict(sorted(RUNTIME_SOURCE_SHA256.items())),
            committed["runtime_sources"],
        )


if __name__ == "__main__":
    unittest.main()
