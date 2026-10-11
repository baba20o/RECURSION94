from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
B03_PATH = ROOT / "content/encounters/boot/B03.json"


class B03GateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.encounter = json.loads(B03_PATH.read_text(encoding="utf-8"))

    def test_completion_requires_terminal_interaction_and_door_unlock_bit(
        self,
    ) -> None:
        completion = self.encounter["stage_completion"]
        self.assertEqual(completion["object"], "B03_COMMIT")
        self.assertEqual(
            completion["completion_state"],
            {"field": "state", "mask": 4103, "value": 4103},
        )

        gate = next(
            group
            for group in self.encounter["dependency_policy"]
            if group["consumer"] == "B03_COMMIT"
        )
        self.assertEqual(gate["policy"], "ALL")
        self.assertEqual(gate["duration_frames"], 0)
        self.assertEqual(
            gate["completion_state"],
            {"field": "state", "mask": 2, "value": 2},
        )
        self.assertEqual(len(gate["conditions"]), 1)
        self.assertEqual(gate["conditions"][0]["provider"], "B03_DOOR")
        self.assertEqual(
            gate["conditions"][0]["completion_state"],
            {"field": "state", "mask": 2, "value": 2},
        )

    def test_authored_door_and_renderer_share_a_16_by_32_leaf(self) -> None:
        collision = (
            ROOT / "src/gameplay/campaign_collision.asm"
        ).read_text(encoding="utf-8")
        renderer = (
            ROOT / "src/render/gameplay_renderer_sprites.asm"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "CAMPAIGN_DOOR_WIDTH_16_8        equ     16*256", collision
        )
        self.assertIn(
            "CAMPAIGN_DOOR_HEIGHT_16_8       equ     32*256", collision
        )
        door = renderer.split(".campaign_door:", 1)[1].split(
            ".campaign_door_open:", 1
        )[0]
        self.assertIn("#RENDERER_SPRITE_SIZE_2X4,d3", door)


if __name__ == "__main__":
    unittest.main()
