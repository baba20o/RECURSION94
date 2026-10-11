from __future__ import annotations

import inspect
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools import build_rom


ROOT = Path(__file__).resolve().parents[2]
BUILD_ROM_PATH = ROOT / "tools/build_rom.py"
BUILD_PS1_PATH = ROOT / "tools/build.ps1"
RUN_PS1_PATH = ROOT / "tools/run.ps1"
GAMEPLAY_PATH = ROOT / "src/gameplay/gameplay_core.asm"
RUNTIME_REPORT_PATH = (
    ROOT / "assets/generated/campaign_runtime/campaign_runtime_report.json"
)


def _runtime_content(report: dict[str, object]) -> dict[str, object]:
    """Wrap a generated runtime report like compile_campaign_content does."""
    return {"runtime": {"compiler_report": report}}


def _positions(source: str, fragments: tuple[str, ...]) -> list[int]:
    positions: list[int] = []
    start = 0
    for fragment in fragments:
        position = source.index(fragment, start)
        positions.append(position)
        start = position + len(fragment)
    return positions


class LevelEntryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build_source = BUILD_ROM_PATH.read_text(encoding="utf-8")
        cls.build_ps1 = BUILD_PS1_PATH.read_text(encoding="utf-8")
        cls.run_ps1 = RUN_PS1_PATH.read_text(encoding="utf-8")
        cls.gameplay = GAMEPLAY_PATH.read_text(encoding="utf-8")
        cls.runtime_report = json.loads(
            RUNTIME_REPORT_PATH.read_text(encoding="utf-8")
        )

    def test_cli_exposes_optional_start_encounter_without_building(self) -> None:
        signature = inspect.signature(build_rom.build)
        parameter = signature.parameters["start_encounter"]
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)

        result = subprocess.run(
            [sys.executable, str(BUILD_ROM_PATH), "--help"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--start-encounter START_ENCOUNTER", result.stdout)
        self.assertIn(
            "start_encounter=args.start_encounter",
            self.build_source,
        )

    def test_resolver_uses_generated_runtime_roster_and_directory_order(
        self,
    ) -> None:
        encounters = self.runtime_report["encounters"]
        self.assertEqual(
            [item["directory_index"] for item in encounters],
            list(range(len(encounters))),
        )
        content = _runtime_content(self.runtime_report)
        for item in encounters:
            encounter_id = item["id"]
            with self.subTest(encounter=encounter_id):
                self.assertEqual(
                    build_rom.resolve_start_encounter(
                        content,
                        f"  {encounter_id.lower()}  ",
                    ),
                    (encounter_id, item["directory_index"]),
                )

        synthetic = _runtime_content(
            {
                "encounters": [
                    {"id": "Z99", "directory_index": 0},
                    {"id": "A00", "directory_index": 1},
                ]
            }
        )
        self.assertEqual(
            build_rom.resolve_start_encounter(synthetic, "a00"),
            ("A00", 1),
        )
        self.assertEqual(
            build_rom.resolve_start_encounter(content, None),
            (None, 0),
        )

    def test_resolver_rejects_unknown_empty_and_ambiguous_ids(self) -> None:
        content = _runtime_content(self.runtime_report)
        with self.assertRaisesRegex(
            build_rom.BuildFailure,
            r"unknown start encounter 'NOPE'.*B01.*I04",
        ):
            build_rom.resolve_start_encounter(content, "nope")
        with self.assertRaisesRegex(
            build_rom.BuildFailure,
            "start encounter must not be empty",
        ):
            build_rom.resolve_start_encounter(content, "   ")
        with self.assertRaisesRegex(
            build_rom.BuildFailure,
            "duplicate encounter IDs",
        ):
            build_rom.resolve_start_encounter(
                _runtime_content(
                    {
                        "encounters": [
                            {"id": "B01", "directory_index": 0},
                            {"id": "B01", "directory_index": 1},
                        ]
                    }
                ),
                "B01",
            )
        with self.assertRaisesRegex(
            build_rom.BuildFailure,
            "directory order is inconsistent",
        ):
            build_rom.resolve_start_encounter(
                _runtime_content(
                    {
                        "encounters": [
                            {"id": "B01", "directory_index": 1},
                        ]
                    }
                ),
                "B01",
            )

    def test_release_rejects_debug_start_before_touching_build_inputs(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            build_rom.BuildFailure,
            "Debug instrumentation feature",
        ):
            build_rom.build(
                ROOT / "path-that-does-not-exist",
                "Release",
                start_encounter="B03",
            )

    def test_variant_outputs_and_reports_are_confined_to_level_directory(
        self,
    ) -> None:
        build = inspect.getsource(build_rom.build)
        required = (
            'canonical_build_root = root / "build"',
            "encounter_id, encounter_index = resolve_start_encounter(",
            "canonical_build_root / f\"level-{encounter_id}\"",
            'raw_path = build_root / f"{stem}.raw"',
            'listing_path = build_root / f"{stem}.lst"',
            'symbol_path = build_root / f"{stem}.sym"',
            'map_path = build_root / f"{stem}.map"',
            'log_path = build_root / f"{stem}.build.log"',
            'dependency_path = build_root / f"{stem}.d"',
            'output_path = build_root / f"{stem}.bin"',
            'shutil.copyfile(listing_path, build_root / "recursion94.lst")',
            'shutil.copyfile(map_path, build_root / "recursion94.map")',
            "build_root=build_root",
        )
        positions = _positions(build, required)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(
            'output_path = canonical_build_root / f"{stem}.bin"',
            build,
        )
        self.assertNotIn(
            "build_root=canonical_build_root",
            build,
        )

    def test_variant_passes_numeric_directory_index_to_vasm_only(self) -> None:
        build = inspect.getsource(build_rom.build)
        required = (
            'defines = [f"BUILD_{configuration.upper()}=1"]',
            "if encounter_id is not None:",
            'defines.append(f"DEBUG_START_ENCOUNTER={encounter_index}")',
            '*(f"-D{define}" for define in defines)',
        )
        positions = _positions(build, required)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("DEBUG_START_ENCOUNTER={encounter_id}", build)
        self.assertIn('"debug_start_encounter": encounter_id', build)
        self.assertIn(
            '"debug_start_encounter_index": (',
            build,
        )

    def test_gameplay_initialize_defaults_to_zero_then_applies_debug_define(
        self,
    ) -> None:
        initialize = self.gameplay.split(
            "GameplayInitialize:",
            1,
        )[1].split(
            "\nGameplayReinstantiateActors:",
            1,
        )[0]
        required = (
            "moveq   #0,d0",
            "ifd     DEBUG_START_ENCOUNTER",
            "move.w  #DEBUG_START_ENCOUNTER,d0",
            "endif",
            "bsr.w   CampaignLoadEncounter",
        )
        positions = _positions(initialize, required)
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(initialize.count("ifd     DEBUG_START_ENCOUNTER"), 1)
        self.assertEqual(
            initialize.count("move.w  #DEBUG_START_ENCOUNTER,d0"),
            1,
        )

    def test_powershell_build_and_run_forward_level_to_variant_rom(
        self,
    ) -> None:
        for source in (self.build_ps1, self.run_ps1):
            self.assertIn("[string]$Level", source)
            self.assertIn("$Level.Trim().ToUpperInvariant()", source)
            self.assertIn(
                '-Level is Debug-only; the canonical Release ROM always boots',
                source,
            )

        build_required = (
            '$Arguments = @($BuildTool, "--root", $ProjectRoot, '
            '"--configuration", $Configuration)',
            'if ($NormalizedLevel) {',
            '$Arguments += @("--start-encounter", $NormalizedLevel)',
            "Start-Process",
        )
        build_positions = _positions(self.build_ps1, build_required)
        self.assertEqual(build_positions, sorted(build_positions))

        run_required = (
            '"build\\level-" + $NormalizedLevel + '
            '"\\recursion94-debug.bin"',
            'if (-not $NoBuild) {',
            '$BuildArguments += @("-Level", $NormalizedLevel)',
            'if (-not (Test-Path -LiteralPath $Rom -PathType Leaf))',
            "$EmulatorArguments += $Rom",
            "Start-Process @ProcessArguments",
        )
        run_positions = _positions(self.run_ps1, run_required)
        self.assertEqual(run_positions, sorted(run_positions))
        self.assertNotIn(
            "-Level is reserved for the future runtime level-entry ABI",
            self.run_ps1,
        )


if __name__ == "__main__":
    unittest.main()
