from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tools.campaign_runtime.compiler import (
    RuntimeCompileError,
    _flatten_entities,
    build_runtime_artifacts,
)
from tools.content_compiler.compiler import load_and_validate


ROOT = Path(__file__).resolve().parents[2]


class BootContentRuntimeTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        artifacts = build_runtime_artifacts(ROOT / "content")
        cls.report = json.loads(
            artifacts.files["campaign_runtime_report.json"].decode("utf-8")
        )

    def _objects(self, encounter_id: str) -> dict[str, dict[str, object]]:
        encounter = next(
            item
            for item in self.report["encounters"]
            if item["id"] == encounter_id
        )
        return {
            item["authored_logical_id"]: item
            for item in encounter["objects"]
        }

    def test_b01_metadata_matches_immediate_door_and_authored_commit_hold(
        self,
    ) -> None:
        authored = json.loads(
            (ROOT / "content/encounters/boot/B01.json").read_text(
                encoding="utf-8"
            )
        )
        door = authored["entities"]["devices"][1]
        terminal = authored["entities"]["devices"][2]
        self.assertEqual(door["state"]["states"], ["closed", "open"])
        self.assertEqual(
            door["parameters"],
            {"opening_frames": 0, "close_delay_frames": 0},
        )
        self.assertEqual(
            terminal["state"]["states"], ["locked", "armed", "committed"]
        )
        self.assertEqual(terminal["parameters"]["interact_frames"], 24)
        objects = self._objects("B01")
        self.assertEqual(
            objects["B01_DOOR_A"]["semantic_record"]["duration_frames"], 0
        )
        self.assertEqual(
            objects["B01_COMMIT"]["semantic_record"]["duration_frames"], 24
        )

    def test_b03_metadata_names_only_live_states_and_authored_commit_hold(
        self,
    ) -> None:
        authored = json.loads(
            (ROOT / "content/encounters/boot/B03.json").read_text(
                encoding="utf-8"
            )
        )
        devices = {
            item["logical_id"]: item
            for item in authored["entities"]["devices"]
        }
        self.assertEqual(
            devices["B03_SWITCH_B"]["state"]["states"], ["idle", "active"]
        )
        self.assertEqual(
            devices["B03_DOOR"]["state"]["states"], ["closed", "open"]
        )
        self.assertEqual(
            devices["B03_COMMIT"]["parameters"]["interact_frames"], 20
        )
        objects = self._objects("B03")
        self.assertEqual(
            objects["B03_SWITCH_B"]["semantic_record"]["duration_frames"],
            180,
        )
        self.assertEqual(
            objects["B03_DOOR"]["semantic_record"]["duration_frames"], 150
        )
        self.assertEqual(
            objects["B03_COMMIT"]["semantic_record"]["duration_frames"], 20
        )

    def test_open_frames_cannot_drift_from_live_dependency_duration(
        self,
    ) -> None:
        validated = load_and_validate(ROOT / "content")
        b03 = copy.deepcopy(
            next(item for item in validated.encounters if item["id"] == "B03")
        )
        door = next(
            item
            for item in b03["entities"]["devices"]
            if item["logical_id"] == "B03_DOOR"
        )
        door["parameters"]["open_frames"] = 149
        with self.assertRaisesRegex(
            RuntimeCompileError, "does not match authored open_frames"
        ):
            _flatten_entities(b03)


if __name__ == "__main__":
    unittest.main()
