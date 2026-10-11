from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.m4_reference.contracts import (
    ContractError,
    EXPECTED_B01_FIXTURE,
    EXPECTED_CONSTANTS,
    RUNTIME_SOURCE_SHA256,
    load_gameplay_contract,
)


ROOT = Path(__file__).resolve().parents[2]


class GameplayAssemblyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_gameplay_contract(
            ROOT, verify_source_hashes=False
        )

    def test_current_semantic_inputs_are_not_relabelled_frozen_m4(self) -> None:
        self.assertNotEqual(
            dict(self.contract.source_sha256),
            dict(sorted(RUNTIME_SOURCE_SHA256.items())),
        )
        with self.assertRaisesRegex(ContractError, "runtime source drift"):
            load_gameplay_contract(ROOT)

    def test_all_model_constants_match_assembly(self) -> None:
        for name, expected in EXPECTED_CONSTANTS.items():
            with self.subTest(name=name):
                self.assertEqual(self.contract.constants[name], expected)

    def test_exact_replay_descriptor_timeline_boundaries(self) -> None:
        c = self.contract.constants
        self.assertEqual(c["REPLAY_BANK_COUNT"] * c["REPLAY_BANK_BYTES"], 5040)
        self.assertEqual(c["REPLAY_BANK_REGION_END"], 0xFF13B0)
        self.assertEqual(
            c["REPLAY_DESCRIPTOR_BYTES"] * c["REPLAY_BANK_COUNT"],
            56,
        )
        self.assertEqual(c["REPLAY_DESCRIPTOR_REGION_END"], 0xFF13E8)
        self.assertEqual(c["TIMELINE_CONTROL_BYTES"], 24)
        self.assertEqual(c["TIMELINE_CONTROL_END"], 0xFF1400)
        self.assertEqual(
            c["TIMELINE_CONTROL_END"] - c["REPLAY_DESCRIPTOR_BASE"],
            0x50,
        )
        self.assertEqual(
            c["TIMELINE_CONTROL_END"] - c["REPLAY_BANK_BASE"],
            5120,
        )
        self.assertGreaterEqual(
            c["GAMEPLAY_CONTROL_BASE"],
            c["ENGINE_CONTROL_USED_END"],
        )
        self.assertLessEqual(
            c["GAMEPLAY_CONTROL_BASE"] + c["GAMEPLAY_CONTROL_BYTES_VALUE"],
            c["ENGINE_CONTROL_END"],
        )
        self.assertEqual(c["ENGINE_STAGING_END"], 0xFF8000)

    def test_b01_fixture_parses_to_locked_semantics(self) -> None:
        self.assertEqual(self.contract.fixture, EXPECTED_B01_FIXTURE)
        self.assertEqual(self.contract.fixture.spawn, (32, 112))
        self.assertEqual(
            (
                self.contract.fixture.rects[0].x,
                self.contract.fixture.rects[0].y,
                self.contract.fixture.rects[0].width,
                self.contract.fixture.rects[0].height,
            ),
            (112, 48, 16, 128),
        )
        device = self.contract.fixture.objects[1]
        self.assertEqual((device.pool_type, device.x, device.y), (3, 80, 112))

    def _copy_runtime(self, destination: Path) -> None:
        (destination / "src").mkdir(parents=True)
        shutil.copytree(ROOT / "src/game", destination / "src/game")
        shutil.copytree(ROOT / "src/gameplay", destination / "src/gameplay")

    def test_frozen_source_hash_guard_rejects_later_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            with self.assertRaisesRegex(ContractError, "runtime source drift"):
                load_gameplay_contract(root)

    def test_numeric_drift_fails_without_hash_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            layout = root / "src/gameplay/gameplay_layout.asm"
            text = layout.read_text(encoding="utf-8")
            self.assertIn("REPLAY_BANK_COUNT               equ     7", text)
            layout.write_text(
                text.replace(
                    "REPLAY_BANK_COUNT               equ     7",
                    "REPLAY_BANK_COUNT               equ     8",
                ),
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ContractError, "constant drift"):
                load_gameplay_contract(root, verify_source_hashes=False)

    def test_fixture_drift_fails_without_hash_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            encounters = root / "src/game/encounters.asm"
            text = encounters.read_text(encoding="utf-8")
            self.assertIn("dc.w    112,48,16,128", text)
            encounters.write_text(
                text.replace("dc.w    112,48,16,128", "dc.w    113,48,16,128"),
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(ContractError, "semantic fixture drift"):
                load_gameplay_contract(root, verify_source_hashes=False)

    def test_tick_behavior_order_drift_fails_without_hash_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            core = root / "src/gameplay/gameplay_core.asm"
            text = core.read_text(encoding="utf-8")
            echo_phase = "#GAMEPLAY_PHASE_ECHOES"
            current_phase = "#GAMEPLAY_PHASE_CURRENT"
            self.assertIn(echo_phase, text)
            self.assertIn(current_phase, text)
            text = text.replace(echo_phase, "#TEMP_PHASE", 1)
            text = text.replace(current_phase, echo_phase, 1)
            text = text.replace("#TEMP_PHASE", current_phase, 1)
            core.write_text(text, encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ContractError, "behavior order drift"):
                load_gameplay_contract(root, verify_source_hashes=False)

    def test_campaign_behavior_order_drift_fails_without_hash_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            core = root / "src/gameplay/gameplay_core.asm"
            text = core.read_text(encoding="utf-8")
            devices = "bsr.w   VocabularyUpdateDevices"
            dependencies = "bsr.w   CampaignEvaluateDependencies"
            device_index = text.index(devices)
            dependency_index = text.index(dependencies, device_index)
            self.assertLess(device_index, dependency_index)
            text = (
                text[:device_index]
                + dependencies
                + text[device_index + len(devices) : dependency_index]
                + devices
                + text[dependency_index + len(dependencies) :]
            )
            core.write_text(text, encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ContractError, "behavior order drift"):
                load_gameplay_contract(root, verify_source_hashes=False)

    def test_unreferenced_future_gameplay_source_is_outside_m4_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            (root / "src/gameplay/unbound.asm").write_text(
                "; future, intentionally unwired source\n",
                encoding="utf-8",
                newline="\n",
            )
            contract = load_gameplay_contract(root, verify_source_hashes=False)
            self.assertNotIn(
                "src/gameplay/unbound.asm",
                dict(contract.source_sha256),
            )

    def test_unbound_gameplay_include_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_runtime(root)
            (root / "src/gameplay/unbound.asm").write_text(
                "; unexpected included source\n",
                encoding="utf-8",
                newline="\n",
            )
            entry = root / "src/gameplay/gameplay.asm"
            entry.write_text(
                entry.read_text(encoding="utf-8")
                + 'include "gameplay/unbound.asm"\n',
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(
                ContractError,
                "included gameplay runtime source set drift",
            ):
                load_gameplay_contract(root, verify_source_hashes=False)


if __name__ == "__main__":
    unittest.main()
