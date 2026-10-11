from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from tools.build_rom import compile_campaign_content


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BINARY_SHA256 = (
    "cd1381f609962e315d11b34f4cdb21b8b7c43c4507eaca1abc5f85dc78170b56"
)


def symbols(path: Path) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for line in path.read_text("utf-8").splitlines():
        address, name = line.split(maxsplit=1)
        parsed[name] = int(address, 16)
    return parsed


class CampaignBuildIntegrationTests(unittest.TestCase):
    def test_build_stage_regenerates_and_rechecks_exact_roster(self) -> None:
        result = compile_campaign_content(root=ROOT)
        composition = result["compiler_report"]["composition"]

        self.assertEqual(
            (
                composition["encounters"],
                composition["primary"],
                composition["bosses"],
                composition["reachable_encounters"],
            ),
            (20, 15, 5, 20),
        )
        self.assertTrue(result["generated_files_current"])
        self.assertEqual(result["binary_sha256"], EXPECTED_BINARY_SHA256)

    def test_current_debug_rom_contains_the_generated_blob_exactly(self) -> None:
        raw_path = ROOT / "build/recursion94-debug.raw"
        symbol_path = ROOT / "build/recursion94-debug.sym"
        binary_path = ROOT / "assets/generated/content/campaign_content.bin"
        self.assertTrue(raw_path.is_file(), "Debug raw image must be built")
        self.assertTrue(symbol_path.is_file(), "Debug symbols must be built")
        self.assertTrue(binary_path.is_file(), "generated campaign blob must exist")

        raw = raw_path.read_bytes()
        binary = binary_path.read_bytes()
        table = symbols(symbol_path)
        start = table["ContentCampaignBinary"]
        end = table["ContentCampaignBinaryEnd"]

        self.assertEqual(end - start, len(binary))
        self.assertEqual(raw[start:end], binary)
        self.assertEqual(hashlib.sha256(binary).hexdigest(), EXPECTED_BINARY_SHA256)

    def test_build_content_report_is_explicitly_not_replay_complete(self) -> None:
        report_path = ROOT / "build/content-report.json"
        self.assertTrue(report_path.is_file(), "content report must be built")
        report = json.loads(report_path.read_text("utf-8"))

        self.assertEqual(report["compiled_encounters"], 20)
        self.assertEqual(report["compiled_primary_stages"], 15)
        self.assertEqual(report["compiled_bosses"], 5)
        self.assertEqual(report["reachable_encounters"], 20)
        self.assertEqual(report["canonical_replays_verified"], 0)
        self.assertFalse(report["gate_passed"])
        self.assertIn("pending", report)


if __name__ == "__main__":
    unittest.main()
