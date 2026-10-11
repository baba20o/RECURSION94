from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from tools.build_rom import compile_art_assets


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BUNDLE_SHA256 = (
    "4992b3cb89e7ad2ac1b9d82a7eac859831cdd2f95c01b7d9dc82dd294a1a84e9"
)


def symbols(path: Path) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for line in path.read_text("utf-8").splitlines():
        address, name = line.split(maxsplit=1)
        parsed[name] = int(address, 16)
    return parsed


class ArtBuildIntegrationTests(unittest.TestCase):
    def test_build_stage_regenerates_and_rechecks_art(self) -> None:
        result = compile_art_assets(root=ROOT)

        self.assertTrue(result["generated_files_current"])
        self.assertEqual(result["binary_sha256"], EXPECTED_BUNDLE_SHA256)
        self.assertEqual(result["compiler_report"]["status"], "PASS")
        self.assertEqual(
            result["compiler_report"]["palettes"]["emitted_banks"],
            4,
        )
        self.assertGreaterEqual(
            result["compiler_report"]["sprites"]["frame_count"],
            7,
        )

    def test_current_debug_rom_contains_generated_bundle_exactly(self) -> None:
        raw_path = ROOT / "build/recursion94-debug.raw"
        symbol_path = ROOT / "build/recursion94-debug.sym"
        bundle_path = ROOT / "assets/generated/art/recursion94_art.bin"
        self.assertTrue(raw_path.is_file(), "Debug raw image must be built")
        self.assertTrue(symbol_path.is_file(), "Debug symbols must be built")
        self.assertTrue(bundle_path.is_file(), "generated art bundle must exist")

        raw = raw_path.read_bytes()
        bundle = bundle_path.read_bytes()
        table = symbols(symbol_path)
        start = table["ArtBundle"]
        end = table["ArtBundleEnd"]

        self.assertEqual(end - start, len(bundle))
        self.assertEqual(raw[start:end], bundle)
        self.assertEqual(
            hashlib.sha256(bundle).hexdigest(),
            EXPECTED_BUNDLE_SHA256,
        )

    def test_build_asset_report_is_honest_about_runtime_gate(self) -> None:
        report_path = ROOT / "build/asset-report.json"
        self.assertTrue(report_path.is_file(), "asset report must be built")
        report = json.loads(report_path.read_text("utf-8"))

        self.assertTrue(report["generated_files_current"])
        self.assertTrue(report["rom_embedded"])
        self.assertEqual(report["bundle_sha256"], EXPECTED_BUNDLE_SHA256)
        self.assertFalse(report["gate_passed"])
        self.assertIn("runtime renderer consumption of compiled bundle", report["pending"])


if __name__ == "__main__":
    unittest.main()
