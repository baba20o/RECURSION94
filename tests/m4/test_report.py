from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.m4_reference.report import (
    FROZEN_REPORT_SCHEMA,
    GOLDEN_B01_TERMINAL_CRC,
    REPORT_SCHEMA,
    build_reference_report,
    compute_report_binding,
)


ROOT = Path(__file__).resolve().parents[2]
COMMITTED_REPORT = ROOT / "artifacts/reports/m4-reference.json"
FROZEN_ROM_ARCHIVE = (
    ROOT / "artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5"
)
FROZEN_ROM_SHA256 = {
    "recursion94-debug.bin": (
        "c689adf93f646c34809649d103cc7759d4b5adf19f5315b3ab99bafe883d2460"
    ),
    "recursion94-release.bin": (
        "0cf94ab5a303bc077810f3d1fe2d4157070ffe6ca515ba0ae5568d3cb90f9d43"
    ),
}


class M4ReferenceReportTests(unittest.TestCase):
    def test_frozen_rom_archive_is_the_historical_authority(self) -> None:
        for name, expected in FROZEN_ROM_SHA256.items():
            with self.subTest(name=name):
                path = FROZEN_ROM_ARCHIVE / name
                self.assertTrue(path.is_file(), path)
                self.assertEqual(
                    expected,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )

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
        self.assertEqual(first["contract"]["total_replay_timeline_bytes"], 5120)
        self.assertEqual(first["contract"]["descriptor_control_remainder_bytes"], 0x50)
        self.assertEqual(
            first["b01"]["terminal_crc32_iso_hdlc"],
            f"{GOLDEN_B01_TERMINAL_CRC:08X}",
        )
        self.assertTrue(first["replacement"]["no_silent_discard"])
        self.assertFalse(first["timeout"]["post_terminal_write"])

    def test_cli_writes_same_report_without_external_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "m4-reference.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/m4_reference/__main__.py"),
                    "--root",
                    str(ROOT),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "M4 historical reference regression: PASS",
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
        from tools.m4_reference.contracts import RUNTIME_SOURCE_SHA256

        self.assertEqual(
            dict(sorted(RUNTIME_SOURCE_SHA256.items())),
            committed["runtime_sources"],
        )


if __name__ == "__main__":
    unittest.main()
