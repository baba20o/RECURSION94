from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.render.test_renderer_contract import parse_equ_contract


ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = ROOT / "src/render/gameplay_renderer_core.asm"
SPRITES_PATH = ROOT / "src/render/gameplay_renderer_sprites.asm"
ASSETS_PATH = ROOT / "src/render/gameplay_renderer_assets.asm"
CONTENT_REPORT_PATH = (
    ROOT / "assets/generated/content/campaign_content_report.json"
)
RUNTIME_REPORT_PATH = (
    ROOT / "assets/generated/campaign_runtime/campaign_runtime_report.json"
)
RUNTIME_ASSEMBLY_PATH = (
    ROOT / "assets/generated/campaign_runtime/campaign_runtime.inc.asm"
)
VOCABULARY_LAYOUT_PATH = ROOT / "src/gameplay/vocabulary_layout.asm"
V02_PATH = ROOT / "content/encounters/video/V02.json"
GLOBAL_LABEL_RE = re.compile(r"(?m)^([A-Z][A-Za-z0-9_]*):\s*$")


def routine(source: str, label: str) -> str:
    marker = f"{label}:"
    start = source.index(marker)
    following = GLOBAL_LABEL_RE.search(source, start + len(marker))
    return source[start : following.start() if following else len(source)]


def local_block(source: str, start: str, end: str) -> str:
    first = source.index(f"\n{start}:")
    last = source.index(f"\n{end}:", first)
    return source[first:last]


def overlaps_cell(rect: dict[str, int], column: int, row: int) -> bool:
    left = column * 8
    top = row * 8
    return (
        left + 8 > rect["x"]
        and left < rect["x"] + rect["width"]
        and top + 8 > rect["y"]
        and top < rect["y"] + rect["height"]
    )


def beam_chunks(length: int, chunk_pixels: int) -> tuple[int, ...]:
    remaining = length + 1
    chunks: list[int] = []
    while remaining:
        span = min(remaining, chunk_pixels)
        chunks.append(span)
        remaining -= span
    return tuple(chunks)


def generated_rectangles(encounter_id: str) -> tuple[dict[str, int], ...]:
    source = RUNTIME_ASSEMBLY_PATH.read_text(encoding="utf-8")
    block = source.split(
        f"CampaignRuntimeRects{encounter_id}:", 1
    )[1].split(f"CampaignRuntimeRects{encounter_id}End:", 1)[0]
    rectangles = []
    for x, y, width, height in re.findall(
        r"dc\.w\s+(-?\d+),(-?\d+),(\d+),(\d+)", block
    ):
        rectangles.append(
            {
                "x": int(x),
                "y": int(y),
                "width": int(width),
                "height": int(height),
            }
        )
    return tuple(rectangles)


class CampaignPlaneTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.core = CORE_PATH.read_text(encoding="utf-8")
        cls.runtime = json.loads(
            RUNTIME_REPORT_PATH.read_text(encoding="utf-8")
        )

    def test_campaign_gate_preserves_the_legacy_b01_builder(self) -> None:
        entry = routine(self.core, "RendererBuildPlaneB")
        gate = entry.index("(VOCAB_CAMPAIGN_FLAGS).l")
        branch = entry.index("bne.w   RendererBuildCampaignPlaneB")
        legacy_geometry = entry.index("cmpi.w  #6,d6")
        self.assertLess(gate, branch)
        self.assertLess(branch, legacy_geometry)
        self.assertIn("RENDERER_TILE_SWITCH_PAD", entry)
        self.assertIn("RENDERER_TILE_TERMINAL_PAD", entry)
        self.assertIn("RENDERER_TILE_HAZARD_PAD", entry)

    def test_campaign_plane_scans_live_bounds_and_rectangles_only(self) -> None:
        campaign = routine(self.core, "RendererBuildCampaignPlaneB")
        self.assertNotIn("B01_", campaign)
        for symbol in (
            "ENGINE_ROOM_LEFT",
            "ENGINE_ROOM_TOP",
            "ENGINE_ROOM_RIGHT",
            "ENGINE_ROOM_BOTTOM",
            "ENGINE_RECT_COUNT",
            "CANONICAL_RECTS",
            "STATIC_RECT_X",
            "STATIC_RECT_Y",
            "STATIC_RECT_WIDTH",
            "STATIC_RECT_HEIGHT",
            "STATIC_RECT_CAPACITY",
        ):
            self.assertIn(symbol, campaign)
        self.assertIn("dbf     d7,.campaign_rectangle", campaign)
        self.assertIn("#PLANE_WIDTH_CELLS,d5", campaign)
        self.assertIn("cmpi.w  #32,d6", campaign)
        self.assertEqual(campaign.count("(VDP_DATA).l"), 1)

    def test_b03_runtime_rectangles_reconstruct_its_visible_collision_grid(
        self,
    ) -> None:
        encounter = next(
            item for item in self.runtime["encounters"] if item["id"] == "B03"
        )
        authored = json.loads(
            (ROOT / "content/encounters/boot/B03.json").read_text(
                encoding="utf-8"
            )
        )
        rectangles = generated_rectangles("B03")
        self.assertEqual(len(rectangles), encounter["rectangles"])
        grid = authored["room"]["tile_grid"]

        for row in range(3, 28):
            for column in range(40):
                projected_wall = any(
                    overlaps_cell(rect, column, row) for rect in rectangles
                )
                metatile_wall = grid[row // 2][column // 2] == "#"
                self.assertEqual(
                    projected_wall,
                    metatile_wall,
                    f"B03 cell ({column},{row})",
                )

        b01 = next(
            item for item in self.runtime["encounters"] if item["id"] == "B01"
        )
        b01_rectangles = generated_rectangles("B01")
        self.assertEqual(len(b01_rectangles), b01["rectangles"])
        b03_walls = {
            (column, row)
            for row in range(3, 28)
            for column in range(40)
            if any(
                overlaps_cell(rect, column, row) for rect in rectangles
            )
        }
        b01_walls = {
            (column, row)
            for row in range(3, 28)
            for column in range(40)
            if any(
                overlaps_cell(rect, column, row)
                for rect in b01_rectangles
            )
        }
        self.assertNotEqual(b03_walls, b01_walls)


class CampaignSpriteTruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sprites = SPRITES_PATH.read_text(encoding="utf-8")
        cls.symbols = parse_equ_contract()

    def test_hazard_markers_use_semantic_rectangles_with_radius_fallback(
        self,
    ) -> None:
        build = routine(self.sprites, "RendererBuildSat")
        extent = local_block(
            build,
            ".append_campaign_hazard_extent",
            ".append_campaign_beam_segment",
        )
        for token in (
            "VOCAB_CAMPAIGN_SEMANTIC_PTR",
            "VOCAB_CAMPAIGN_SEMANTIC_FLAGS",
            "VOCAB_CAMPAIGN_GEOMETRY_WIDTH_MASK",
            "VOCAB_CAMPAIGN_GEOMETRY_HEIGHT_MASK",
            "VOCAB_HAZARD_STATE_WARNING",
            "VOCAB_DEVICE_STATE_ACTIVE",
        ):
            self.assertIn(token, extent)
        self.assertIn(".campaign_hazard_radius:", extent)
        self.assertIn("move.b  (OBJECT_PARAM+1,a2),d3", extent)
        self.assertIn("#VOCAB_HAZARD_RADIUS_DEFAULT,d3", extent)
        semantic_extent, radius_extent = extent.split(
            "\n.campaign_hazard_radius:", 1
        )
        radius_extent = radius_extent.split(
            "\n.campaign_hazard_extent_ready:", 1
        )[0]
        self.assertEqual(semantic_extent.count("subq.w  #4,d5"), 1)
        self.assertEqual(radius_extent.count("subq.w  #4,d5"), 1)
        corner_path = extent.split(
            "\n.campaign_hazard_attribute_ready:", 1
        )[1].split("\n.campaign_hazard_filled_shutter:", 1)[0]
        filled_path = extent.split(
            "\n.campaign_hazard_filled_shutter:", 1
        )[1].split("\n.campaign_hazard_done:", 1)[0]
        self.assertEqual(corner_path.count("RendererTryAppendSprite"), 4)
        self.assertEqual(filled_path.count("RendererTryAppendSprite"), 2)
        self.assertEqual(
            filled_path.count("#RENDERER_SPRITE_SIZE_2X4,d3"), 2
        )
        self.assertEqual(filled_path.count("move.w  d4,d0"), 2)
        self.assertEqual(filled_path.count("move.w  d6,d1"), 1)
        self.assertEqual(filled_path.count("move.w  d7,d1"), 1)
        self.assertNotIn("move.w  d5,d0", filled_path)
        self.assertIn("cmpi.w  #16,d3", semantic_extent)
        self.assertIn("cmpi.w  #64,d7", semantic_extent)
        self.assertIn("addq.w  #8,d5", semantic_extent)
        self.assertIn("addi.w  #32,d7", semantic_extent)
        for offset in ("", "+1", "+2", "+3"):
            self.assertIn(f"RENDERER_TILE_HAZARD{offset}", extent)

    def test_beam_cache_is_rendered_in_inclusive_bounded_chunks(self) -> None:
        build = routine(self.sprites, "RendererBuildSat")
        campaign = build.split("\n.campaign:", 1)[1].split(
            "\n.publish:", 1
        )[0]
        beam = local_block(
            campaign, ".campaign_beam", ".campaign_beams_done"
        )
        projectiles = campaign.index("\n.campaign_projectile:")
        self.assertLess(campaign.index("\n.campaign_beam:"), projectiles)
        self.assertIn(".append_campaign_beam_segment", beam)

        helper = build.split("\n.append_campaign_beam_segment:", 1)[1]
        self.assertIn("addq.w  #1,d4", helper)
        self.assertIn("RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS", helper)
        self.assertIn("RENDERER_CAMPAIGN_BEAM_SPRITE_LIMIT", helper)
        self.assertIn("RENDERER_FLAG_BEAM_TRUNCATED", helper)
        self.assertIn("RendererTryAppendSprite", helper)
        self.assertNotIn("lsr.w   #1", beam)

        v02 = json.loads(V02_PATH.read_text(encoding="utf-8"))
        emitter = next(
            device
            for device in v02["entities"]["devices"]
            if device["type"] == "beam_emitter"
        )
        reflector = next(
            device
            for device in v02["entities"]["devices"]
            if device["type"] == "redirectable_prism"
        )
        incident_length = reflector["x"] - emitter["x"]
        chunk_pixels = self.symbols[
            "RENDERER_CAMPAIGN_BEAM_CHUNK_PIXELS"
        ]
        incident = beam_chunks(incident_length, chunk_pixels)
        vocabulary = VOCABULARY_LAYOUT_PATH.read_text(encoding="utf-8")
        step = int(
            re.search(
                r"(?m)^VOCAB_BEAM_STEP_PIXELS\s+equ\s+(\d+)",
                vocabulary,
            ).group(1)
        )
        steps = int(
            re.search(
                r"(?m)^VOCAB_BEAM_MAX_STEPS\s+equ\s+(\d+)",
                vocabulary,
            ).group(1)
        )
        reflected = beam_chunks(step * steps, chunk_pixels)
        self.assertEqual(incident, (32, 32, 32, 1))
        self.assertEqual(reflected, (32, 32, 32, 32, 32, 1))
        self.assertEqual(
            len(incident) + len(reflected),
            self.symbols["RENDERER_CAMPAIGN_BEAM_SPRITE_LIMIT"],
        )

    def test_beam_lattice_art_is_continuous_in_both_orientations(self) -> None:
        assets = ASSETS_PATH.read_text(encoding="utf-8")
        tile_source = assets.split("RendererTileData:", 1)[1].split(
            "RendererTileDataEnd:", 1
        )[0]
        words = [
            int(value, 16)
            for value in re.findall(r"\$([0-9A-Fa-f]{8})", tile_source)
        ]
        base = self.symbols["RENDERER_TILE_BASE"]
        hazard_index = self.symbols["RENDERER_TILE_HAZARD"] - base
        hazard_tiles = [
            words[(hazard_index + index) * 8 : (hazard_index + index + 1) * 8]
            for index in range(4)
        ]
        self.assertTrue(all(len(tile) == 8 for tile in hazard_tiles))
        for tile in hazard_tiles:
            pixels = [f"{row:08X}" for row in tile]
            self.assertTrue(all(row[3] != "0" and row[4] != "0" for row in pixels))
            self.assertTrue(all(pixel != "0" for pixel in pixels[3]))
            self.assertTrue(all(pixel != "0" for pixel in pixels[4]))

    def test_campaign_door_center_matches_the_16_by_32_leaf(self) -> None:
        predicate = routine(self.sprites, "RendererDoorRejectionActive")
        campaign = predicate.split("\n.campaign:", 1)[1]
        self.assertIn("addi.l  #8*256,d1", campaign)
        self.assertIn("subi.l  #16*256,d0", campaign)

        build = routine(self.sprites, "RendererBuildSat")
        campaign_sat = build.split("\n.campaign:", 1)[1].split(
            "\n.publish:", 1
        )[0]
        closed = local_block(
            campaign_sat, ".campaign_door", ".campaign_door_open"
        )
        self.assertIn("#RENDERER_SPRITE_SIZE_2X4,d3", closed)
        self.assertNotRegex(
            closed.split("(OBJECT_X,a2)", 1)[1],
            r"\b(?:add|sub)[iq]?\.[wl]\s+[^,\n]+,d[01]",
        )
        opened = local_block(
            campaign_sat,
            ".campaign_door_open",
            ".campaign_terminal",
        )
        self.assertIn("addq.w  #4,d0", opened)
        self.assertIn("addi.w  #12,d1", opened)

    def test_authored_per_encounter_budget_remains_below_h40_sat(self) -> None:
        content = json.loads(
            CONTENT_REPORT_PATH.read_text(encoding="utf-8")
        )
        runtime = json.loads(
            RUNTIME_REPORT_PATH.read_text(encoding="utf-8")
        )
        emitters = {
            encounter["id"]
            for encounter in runtime["encounters"]
            if any(
                obj["category"] == "devices"
                and obj["vocab_kind"] == 0x0600
                for obj in encounter["objects"]
            )
        }
        totals: dict[str, int] = {}
        for encounter in content["encounters"]:
            budget = encounter["budgets"]
            total = (
                budget["actors"] * 2
                + budget["devices"]
                + budget["enemies"]
                + budget["carryables"]
                + budget["hazards"]
                * self.symbols["RENDERER_CAMPAIGN_HAZARD_SPRITES"]
                + budget["projectiles"]
            )
            if encounter["id"] in emitters:
                total += self.symbols[
                    "RENDERER_CAMPAIGN_BEAM_SPRITE_LIMIT"
                ]
            totals[encounter["id"]] = total

        self.assertEqual(max(totals.values()), 59)
        self.assertEqual(
            self.symbols["RENDERER_CAMPAIGN_WORST_SPRITES"],
            max(totals.values()),
        )
        self.assertLessEqual(
            max(totals.values()),
            self.symbols["RENDERER_SPRITE_LIMIT"],
        )
        self.assertEqual(
            self.symbols["RENDERER_CAMPAIGN_SAT_HEADROOM"],
            self.symbols["RENDERER_SPRITE_LIMIT"] - max(totals.values()),
        )


if __name__ == "__main__":
    unittest.main()
