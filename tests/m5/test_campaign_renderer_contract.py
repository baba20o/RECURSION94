from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.render.test_renderer_contract import parse_equ_contract


ROOT = Path(__file__).resolve().parents[2]
SPRITES_PATH = ROOT / "src/render/gameplay_renderer_sprites.asm"
HUD_PATH = ROOT / "src/render/gameplay_renderer_hud.asm"
CONTENT_REPORT_PATH = (
    ROOT / "assets/generated/content/campaign_content_report.json"
)
RUNTIME_REPORT_PATH = (
    ROOT / "assets/generated/campaign_runtime/campaign_runtime_report.json"
)
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def _routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


def _local_block(source: str, start_label: str, end_label: str) -> str:
    start = source.index(f"\n{start_label}:")
    end = source.index(f"\n{end_label}:", start)
    return source[start:end]


class CampaignRendererContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sprites = SPRITES_PATH.read_text(encoding="utf-8")
        cls.hud = HUD_PATH.read_text(encoding="utf-8")

    def test_campaign_sat_branch_precedes_and_excludes_legacy_b01_reads(
        self,
    ) -> None:
        build = _routine(self.sprites, "RendererBuildSat")
        gate = build.index("(VOCAB_CAMPAIGN_FLAGS).l")
        branch = build.index("bne.w   .campaign", gate)
        legacy = build.index("B01_SWITCH_CENTER_X", branch)
        campaign = build.index("\n.campaign:", legacy)
        publish = build.index("\n.publish:", campaign)

        self.assertLess(gate, branch)
        self.assertLess(branch, legacy)
        self.assertLess(legacy, campaign)
        self.assertNotIn("B01_", build[campaign:publish])

    def test_campaign_sat_scans_every_required_pool_with_static_bounds(
        self,
    ) -> None:
        build = _routine(self.sprites, "RendererBuildSat")
        campaign = build.split("\n.campaign:", 1)[1].split(
            "\n.publish:", 1
        )[0]

        self.assertIn("(GAMEPLAY_CURRENT_ACTOR).l", campaign)
        self.assertIn("(ENGINE_ACTOR_BASE).l", campaign)
        self.assertIn("#GAMEPLAY_ECHO_LIMIT-1,d7", campaign)
        self.assertIn("dbf     d7,.campaign_echo", campaign)

        pools = (
            ("DEVICE_POOL", "DEVICE_CAPACITY", "device"),
            ("ENEMY_POOL", "ENEMY_CAPACITY", "enemy"),
            ("CARRYABLE_POOL", "CARRYABLE_CAPACITY", "carryable"),
            ("HAZARD_POOL", "HAZARD_CAPACITY", "hazard"),
            ("PROJECTILE_POOL", "PROJECTILE_CAPACITY", "projectile"),
        )
        for pool, capacity, label in pools:
            with self.subTest(pool=pool):
                self.assertIn(f"lea     ({pool}).l,a2", campaign)
                self.assertIn(f"#{capacity}-1,d7", campaign)
                self.assertIn(
                    f"dbf     d7,.campaign_{label}",
                    campaign,
                )

        for start, end in (
            (".campaign_device", ".campaign_devices_done"),
            (".campaign_enemy", ".campaign_enemies_done"),
            (".campaign_carryable", ".campaign_carryables_done"),
            (".campaign_projectile", ".campaign_projectiles_done"),
        ):
            with self.subTest(block=start):
                block = _local_block(campaign, start, end)
                self.assertIn("(OBJECT_ACTIVE,a2)", block)
                self.assertIn("(OBJECT_X,a2)", block)
                self.assertIn("(OBJECT_Y,a2)", block)
                self.assertIn("RendererTryAppendSprite", block)

        hazards = _local_block(
            campaign, ".campaign_hazard", ".campaign_hazards_done"
        )
        self.assertIn("(OBJECT_ACTIVE,a2)", hazards)
        self.assertIn(".append_campaign_hazard_extent", hazards)
        hazard_extent = _local_block(
            build,
            ".append_campaign_hazard_extent",
            ".append_campaign_beam_segment",
        )
        self.assertIn("(OBJECT_X,a2)", hazard_extent)
        self.assertIn("(OBJECT_Y,a2)", hazard_extent)
        self.assertIn("VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK", hazard_extent)
        self.assertIn("VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK", hazard_extent)
        self.assertIn("VOCAB_HAZARD_STATE_WARNING", hazard_extent)
        self.assertIn("VOCAB_DEVICE_STATE_ACTIVE", hazard_extent)
        self.assertEqual(hazard_extent.count("RendererTryAppendSprite"), 6)
        self.assertEqual(
            hazard_extent.count("#RENDERER_SPRITE_SIZE_2X4,d3"), 2
        )

        self.assertIn("(VOCAB_BEAM_COUNT).l", campaign)
        self.assertIn("lea     (VOCAB_BEAM_BASE).l,a2", campaign)
        self.assertIn("dbf     d7,.campaign_beam", campaign)
        beam = _local_block(
            campaign, ".campaign_beam", ".campaign_beams_done"
        )
        self.assertIn(".append_campaign_beam_segment", beam)
        beam_segment = build.split(
            "\n.append_campaign_beam_segment:", 1
        )[1]
        self.assertIn("(VOCAB_BEAM_X,a2)", beam_segment)
        self.assertIn("(VOCAB_BEAM_Y,a2)", beam_segment)
        self.assertIn("(VOCAB_BEAM_DIRECTION,a2)", beam_segment)
        self.assertIn("(VOCAB_BEAM_LENGTH,a2)", beam_segment)
        self.assertIn("RENDERER_TILE_HAZARD", beam_segment)
        self.assertIn("RendererTryAppendSprite", beam_segment)

    def test_campaign_device_shapes_and_state_filters_are_explicit(
        self,
    ) -> None:
        build = _routine(self.sprites, "RendererBuildSat")
        campaign = build.split("\n.campaign:", 1)[1].split(
            "\n.publish:", 1
        )[0]
        devices = _local_block(
            campaign, ".campaign_device", ".campaign_devices_done"
        )

        for kind in (
            "VOCAB_DEVICE_PRESSURE_SWITCH",
            "VOCAB_DEVICE_TIMED_SWITCH",
            "VOCAB_DEVICE_DOOR",
            "VOCAB_DEVICE_HACK_TERMINAL",
            "VOCAB_DEVICE_REPAIR_TERMINAL",
            "VOCAB_DEVICE_SECURITY_NODE",
            "VOCAB_DEVICE_SOCKET",
            "VOCAB_DEVICE_CONVEYOR",
            "VOCAB_DEVICE_BEAM_EMITTER",
            "VOCAB_DEVICE_REFLECTOR",
            "VOCAB_DEVICE_SHIELD_EMITTER",
        ):
            self.assertIn(kind, devices)
        for state in (
            "VOCAB_DEVICE_STATE_ACTIVE",
            "VOCAB_DEVICE_STATE_OPEN",
            "VOCAB_DEVICE_STATE_HACKED",
            "VOCAB_DEVICE_STATE_DISABLED",
        ):
            self.assertIn(state, devices)
        for tile in (
            "RENDERER_TILE_SWITCH_IDLE",
            "RENDERER_TILE_SWITCH_HELD",
            "RENDERER_TILE_DOOR",
            "RENDERER_TILE_DOOR_OPEN",
            "RENDERER_TILE_TERMINAL",
            "RENDERER_TILE_TERMINAL_STABLE",
        ):
            self.assertIn(tile, devices)

        dispatcher = devices.split("\n.campaign_switch:", 1)[0]
        security_dispatch = dispatcher.index(
            "#VOCAB_DEVICE_SECURITY_NODE"
        )
        hack_dispatch = dispatcher.index(
            "#VOCAB_DEVICE_HACK_TERMINAL"
        )
        self.assertLess(security_dispatch, hack_dispatch)
        self.assertIn(
            "beq.w   .campaign_security_node", dispatcher
        )
        security = _local_block(
            devices, ".campaign_security_node", ".campaign_terminal"
        )
        disabled = security.index("#VOCAB_DEVICE_STATE_DISABLED")
        active = security.index("#VOCAB_DEVICE_STATE_ACTIVE", disabled)
        self.assertLess(disabled, active)
        self.assertIn(
            "#RENDERER_OBJECTIVE_ATTRIBUTE+RENDERER_TILE_TERMINAL,d2",
            security,
        )
        self.assertIn(
            "#RENDERER_DATA_ATTRIBUTE+RENDERER_TILE_TERMINAL_STABLE,d2",
            security,
        )
        self.assertIn(".campaign_terminal_position", security)

        enemies = _local_block(
            campaign, ".campaign_enemy", ".campaign_enemies_done"
        )
        hazards = _local_block(
            campaign, ".campaign_hazard", ".campaign_hazards_done"
        )
        self.assertIn("VOCAB_ENEMY_STATE_DISABLED", enemies)
        self.assertIn("VOCAB_DEVICE_STATE_DISABLED", hazards)

    def test_campaign_door_feedback_uses_live_door_center(self) -> None:
        predicate = _routine(self.sprites, "RendererDoorRejectionActive")
        gate = predicate.index("(VOCAB_CAMPAIGN_FLAGS).l")
        branch = predicate.index("bne.w   .campaign", gate)
        legacy = predicate.index("(B01_DEVICE+OBJECT_STATE).l", branch)
        campaign = predicate.index("\n.campaign:", legacy)

        self.assertLess(gate, branch)
        self.assertLess(branch, legacy)
        self.assertLess(legacy, campaign)
        campaign_block = predicate[campaign:]
        self.assertNotIn("B01_", campaign_block)
        self.assertIn("lea     (DEVICE_POOL).l,a1", campaign_block)
        self.assertIn("#DEVICE_CAPACITY-1,d2", campaign_block)
        self.assertIn("#VOCAB_DEVICE_DOOR", campaign_block)
        self.assertIn("#VOCAB_DEVICE_STATE_OPEN", campaign_block)
        self.assertIn("VOCAB_DEVICE_STATE_DISABLED", campaign_block)
        self.assertIn("(OBJECT_X,a1),d1", campaign_block)
        self.assertIn("addi.l  #8*256,d1", campaign_block)
        self.assertIn("sub.l   (OBJECT_Y,a1),d0", campaign_block)
        self.assertIn("subi.l  #16*256,d0", campaign_block)
        self.assertIn("dbf     d2,.campaign_door", campaign_block)

        actor_pair = _routine(self.sprites, "RendererAppendActorPair")
        self.assertIn("RendererDoorRejectionActive", actor_pair)
        self.assertIn("#VOCAB_CAMPAIGN_FLAG_ACTIVE", actor_pair)
        self.assertIn("cmp.l   d1,d0", actor_pair)

        build = _routine(self.sprites, "RendererBuildSat")
        campaign_sat = build.split("\n.campaign:", 1)[1].split(
            "\n.publish:", 1
        )[0]
        closed_door = _local_block(
            campaign_sat, ".campaign_door", ".campaign_door_open"
        )
        self.assertIn("move.l  (OBJECT_X,a2),d0", closed_door)
        self.assertIn("move.l  (OBJECT_Y,a2),d1", closed_door)
        position = closed_door.split(
            "andi.w  #VOCAB_DEVICE_STATE_OPEN|"
            "VOCAB_DEVICE_STATE_DISABLED,d0",
            1,
        )[1]
        position = position.split(
            "move.w  #RENDERER_OBJECTIVE_ATTRIBUTE+"
            "RENDERER_TILE_DOOR,d2",
            1,
        )[0]
        self.assertNotRegex(position, r"\b(?:add|sub)[iq]?\.[wl]\b")

    def test_campaign_hud_status_is_derived_from_live_device_state(
        self,
    ) -> None:
        build = _routine(self.hud, "RendererBuildHud")
        ordinary = _local_block(
            build, ".ordinary_status", ".legacy_ordinary_status"
        )
        self.assertIn("#VOCAB_CAMPAIGN_FLAG_ACTIVE", ordinary)
        self.assertIn("RendererHudCampaignStatus", ordinary)
        self.assertNotIn("GAMEPLAY_FLAG_SWITCH_HELD", ordinary)
        self.assertNotIn("B01_", ordinary)

        stable_gate = build.index("#VOCAB_CAMPAIGN_FLAG_ACTIVE")
        campaign_complete = build.index(
            "#VOCAB_CAMPAIGN_FLAG_COMPLETE", stable_gate
        )
        legacy_stable = build.index(
            "GAMEPLAY_FLAG_PROGRAM_STABLE", campaign_complete
        )
        self.assertLess(stable_gate, campaign_complete)
        self.assertLess(campaign_complete, legacy_stable)

        timeline = build.index("(VOCAB_TIMELINE_UI_FLAGS).l")
        not_timeline = build.index("\n.not_timeline:", timeline)
        engine_active = build.index("#ENGINE_STATE_ACTIVE", not_timeline)
        gameplay_replacement = build.index(
            "#GAMEPLAY_FLAG_REPLACEMENT_VISIBLE", engine_active
        )
        timeline_replacement = build.index(
            "#TIMELINE_FLAG_REPLACEMENT_REQUIRED", gameplay_replacement
        )
        ordinary = build.index("\n.ordinary_status:", timeline_replacement)
        self.assertLess(timeline, not_timeline)
        self.assertLess(not_timeline, engine_active)
        self.assertLess(engine_active, gameplay_replacement)
        self.assertLess(gameplay_replacement, timeline_replacement)
        self.assertLess(timeline_replacement, ordinary)
        self.assertRegex(
            build[engine_active:gameplay_replacement],
            r"beq\.[sw]\s+\.ordinary_status",
        )

        status = _routine(self.hud, "RendererHudCampaignStatus")
        self.assertNotIn("B01_", status)
        self.assertIn("lea     (DEVICE_POOL).l,a2", status)
        self.assertIn("#DEVICE_CAPACITY-1,d5", status)
        self.assertIn("dbf     d5,.device", status)
        for state in (
            "VOCAB_DEVICE_STATE_ACTIVE",
            "VOCAB_DEVICE_STATE_OPEN",
            "VOCAB_DEVICE_STATE_DISABLED",
        ):
            self.assertIn(state, status)

        self.assertIn("movem.l d3-d6/a2,-(a7)", status)
        self.assertIn("#VOCAB_DEVICE_HACK_TERMINAL,d4", status)
        self.assertIn("(VOCAB_CAMPAIGN_COMPLETION_OBJECT).l", status)
        self.assertIn("(VOCAB_DEVICE_HOLD_OWNER,a2)", status)
        self.assertIn("#GAMEPLAY_CURRENT_ACTOR_SLOT+1,d0", status)
        self.assertIn("(OBJECT_TIMER,a2),d4", status)
        self.assertIn("bsr.w   CampaignGetSemanticDuration", status)
        self.assertIn("cmpi.w  #99,d0", status)
        self.assertIn("cmp.w   d0,d4", status)
        self.assertIn("move.w  d0,d6", status)
        self.assertIn("swap    d6", status)
        self.assertIn("move.w  d4,d6", status)
        self.assertIn("move.l  d6,d1", status)

        progress = build.index("RendererHudCampaignCommitHold")
        progress_done = build.index("bra.w   .done", progress)
        progress_block = build[progress:progress_done]
        self.assertIn("(RENDERER_HUD_ROW_2+24).l", progress_block)
        self.assertIn("(RENDERER_HUD_ROW_2+30).l", progress_block)
        self.assertEqual(
            progress_block.count("bsr.w   RendererHudWriteTwoDigits"), 2
        )
        self.assertIn('"COMMIT HOLD 00/00 KEEP B HELD"', self.hud)
        self.assertLess(campaign_complete, progress)

    def test_current_dash_uses_rust_without_changing_actor_identity(self) -> None:
        actor_pair = _routine(self.sprites, "RendererAppendActorPair")

        self.assertIn(
            "#VOCAB_ACTOR_FLAG_DASHING|VOCAB_ACTOR_FLAG_PHASED,d0",
            actor_pair,
        )
        self.assertIn(
            "#DIAGNOSTIC_LETTER_A_TILE+3,d4 ; D", actor_pair
        )
        self.assertIn("#RENDERER_HAZARD_ATTRIBUTE,d5", actor_pair)
        self.assertIn("(GAMEPLAY_CURRENT_ACTOR).l,a0", actor_pair)
        self.assertEqual(actor_pair.count("cmpa.l  a0,a2"), 4)
        self.assertNotIn("cmpi.w  #RENDERER_ATTR_PALETTE_1", actor_pair)
        self.assertNotIn("cmpi.w  #RENDERER_ATTR_PALETTE_2", actor_pair)
        self.assertEqual(actor_pair.count("andi.w  #$6000"), 1)
        self.assertEqual(
            actor_pair.count("bsr.w   RendererTryAppendSprite"), 2
        )

    def test_campaign_renderer_limits_match_generated_content_maxima(
        self,
    ) -> None:
        report = json.loads(CONTENT_REPORT_PATH.read_text(encoding="utf-8"))
        runtime = json.loads(RUNTIME_REPORT_PATH.read_text(encoding="utf-8"))
        maxima = {
            key: max(
                encounter["budgets"][key]
                for encounter in report["encounters"]
            )
            for key in (
                "actors",
                "devices",
                "enemies",
                "carryables",
                "hazards",
                "projectiles",
                "hardware_sprites",
                "sprites_per_scanline",
            )
        }
        symbols = parse_equ_contract()

        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_ACTOR_SPRITES"],
            maxima["actors"] * 2,
        )
        categories = {
            "DEVICE": "devices",
            "ENEMY": "enemies",
            "CARRYABLE": "carryables",
            "HAZARD": "hazards",
            "PROJECTILE": "projectiles",
        }
        for symbol_category, report_category in categories.items():
            self.assertEqual(
                symbols[
                    f"RENDERER_CAMPAIGN_{symbol_category}_LIMIT"
                ],
                maxima[report_category],
            )
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_AUTHORED_SCANLINE_SPRITES"],
            maxima["sprites_per_scanline"],
        )
        emitter_encounters = {
            encounter["id"]
            for encounter in runtime["encounters"]
            if any(
                obj["category"] == "devices"
                and obj["vocab_kind"] == 0x0600
                for obj in encounter["objects"]
            )
        }
        self.assertEqual(emitter_encounters, {"V02"})
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_BEAM_SEGMENT_LIMIT"], 2
        )
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_BEAM_SPRITE_LIMIT"], 10
        )
        self.assertEqual(
            symbols["RENDERER_SPRITE_LIMIT"],
            maxima["hardware_sprites"],
        )

        def one_marker_base(encounter: dict[str, object]) -> int:
            budgets = encounter["budgets"]
            assert isinstance(budgets, dict)
            return (
                int(budgets["actors"]) * 2
                + int(budgets["devices"])
                + int(budgets["enemies"])
                + int(budgets["carryables"])
                + int(budgets["hazards"])
                + int(budgets["projectiles"])
            )

        hazard_sprites = symbols["RENDERER_CAMPAIGN_HAZARD_SPRITES"]
        non_beam_worst = max(
            one_marker_base(encounter)
            + int(encounter["budgets"]["hazards"]) * (hazard_sprites - 1)
            for encounter in report["encounters"]
            if encounter["id"] not in emitter_encounters
        )
        beam_worst = max(
            one_marker_base(encounter)
            + int(encounter["budgets"]["hazards"]) * (hazard_sprites - 1)
            + symbols["RENDERER_CAMPAIGN_BEAM_SPRITE_LIMIT"]
            for encounter in report["encounters"]
            if encounter["id"] in emitter_encounters
        )
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_NON_BEAM_WORST_SPRITES"],
            non_beam_worst,
        )
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_BEAM_ENCOUNTER_WORST_SPRITES"],
            beam_worst,
        )
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_WORST_SPRITES"],
            max(non_beam_worst, beam_worst),
        )
        self.assertEqual(
            symbols["RENDERER_CAMPAIGN_SAT_HEADROOM"],
            symbols["RENDERER_SPRITE_LIMIT"]
            - symbols["RENDERER_CAMPAIGN_WORST_SPRITES"],
        )


if __name__ == "__main__":
    unittest.main()
