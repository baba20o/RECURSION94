from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any, Callable

from tools.asset_compiler.compiler import (
    FORMAT_VERSION,
    OUTPUT_ANIMATIONS,
    OUTPUT_ASSEMBLY,
    OUTPUT_BUNDLE,
    OUTPUT_CHECKSUMS,
    OUTPUT_NAMES,
    OUTPUT_PALETTES,
    OUTPUT_REPORT,
    OUTPUT_SPRITES,
    OUTPUT_TILEMAPS,
    OUTPUT_TILES,
    AssetCompileError,
    AssetValidationError,
    build_artifacts,
    check_generated,
    compile_assets,
    genesis_color_word,
    pack_tile_4bpp,
    reduce_palette,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ART = ROOT / "assets/source/art"
SOURCE_MAPS = ROOT / "assets/source/maps"


class AssetCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = build_artifacts(SOURCE_ART, SOURCE_MAPS)

    def make_source_copy(self) -> tuple[Path, Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        art = root / "art"
        maps = root / "maps"
        shutil.copytree(SOURCE_ART, art)
        shutil.copytree(SOURCE_MAPS, maps)
        return root, art, maps

    def mutate(
        self,
        path: Path,
        mutation: Callable[[dict[str, Any]], None],
    ) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutation(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def assert_invalid(
        self,
        art: Path,
        maps: Path,
        pattern: str,
    ) -> None:
        with self.assertRaisesRegex(AssetValidationError, pattern):
            build_artifacts(art, maps)

    def test_current_sources_compile_with_original_provenance_and_pass_budgets(self) -> None:
        report = self.reference.report
        self.assertEqual("PASS", report["status"])
        self.assertEqual("BOOT", report["identity"])
        self.assertEqual("original", report["provenance"]["origin"])
        self.assertEqual([], report["provenance"]["external_sources"])
        self.assertEqual(9, len(self.reference.files))
        self.assertEqual(OUTPUT_NAMES, frozenset(self.reference.files))
        self.assertFalse(report["budget_failures"])
        self.assertTrue(
            all(item["status"] == "PASS" for item in report["budgets"].values())
        )
        source_paths = {
            item["path"] for item in report["provenance"]["source_files"]
        }
        self.assertEqual(
            {
                "art/manifest.json",
                "art/palettes.json",
                "art/font_ui.json",
                "art/ui.json",
                "art/industrial.json",
                "art/actor_current.json",
                "art/echoes.json",
                "art/devices.json",
                "maps/boot_chamber.json",
            },
            source_paths,
        )

    def test_genesis_color_conversion_has_correct_channel_layout_and_rounding(self) -> None:
        self.assertEqual(0x0000, genesis_color_word((0, 0, 0)))
        self.assertEqual(0x0EEE, genesis_color_word((255, 255, 255)))
        self.assertEqual(0x000E, genesis_color_word((255, 0, 0)))
        self.assertEqual(0x00E0, genesis_color_word((0, 255, 0)))
        self.assertEqual(0x0E00, genesis_color_word((0, 0, 255)))
        self.assertEqual(0x0000, genesis_color_word((18, 18, 18)))
        self.assertEqual(0x0222, genesis_color_word((19, 19, 19)))
        with self.assertRaisesRegex(ValueError, "three-integer tuple"):
            genesis_color_word((256, 0, 0))
        with self.assertRaisesRegex(ValueError, "three-integer tuple"):
            genesis_color_word((True, 0, 0))

    def test_palette_reduction_is_weighted_bounded_and_order_independent(self) -> None:
        colors = {
            (index * 11 % 256, index * 29 % 256, index * 47 % 256): index + 1
            for index in range(31)
        }
        forward = reduce_palette(colors, 15)
        backward = reduce_palette(dict(reversed(tuple(colors.items()))), 15)
        self.assertEqual(forward, backward)
        self.assertLessEqual(len(forward), 15)
        self.assertEqual(
            ((3, 3, 3),),
            reduce_palette({(0, 0, 0): 3, (10, 10, 10): 1}, 1),
        )
        with self.assertRaisesRegex(ValueError, "at least one"):
            reduce_palette([], 15)
        with self.assertRaisesRegex(ValueError, "positive"):
            reduce_palette({(0, 0, 0): 0}, 15)
        with self.assertRaisesRegex(ValueError, "1..15"):
            reduce_palette([(0, 0, 0)], 16)

    def test_foundational_reduce_palette_exercises_real_reduction(self) -> None:
        definitions = self.reference.report["palettes"]["definitions"]
        boot = next(item for item in definitions if item["id"] == "boot_world")
        self.assertEqual("reduce", boot["mode"])
        self.assertEqual(18, boot["source_opaque_colors"])
        self.assertLessEqual(boot["emitted_opaque_colors"], 15)
        self.assertGreater(boot["emitted_opaque_colors"], 1)
        for item in definitions:
            self.assertLessEqual(item["emitted_opaque_colors"], 15)
            self.assertEqual("$0000", item["words_hex"][0])

    def test_tile_packing_is_left_nibble_first_and_rejects_bad_input(self) -> None:
        pixels = tuple(range(16)) * 4
        self.assertEqual(bytes.fromhex("0123456789ABCDEF") * 4, pack_tile_4bpp(pixels))
        with self.assertRaisesRegex(ValueError, "exactly 64"):
            pack_tile_4bpp([0] * 63)
        with self.assertRaisesRegex(ValueError, "0..15"):
            pack_tile_4bpp([0] * 63 + [16])
        with self.assertRaisesRegex(ValueError, "0..15"):
            pack_tile_4bpp([0] * 63 + [True])

    def test_tile_output_reserves_zero_and_records_exact_and_flip_reuse(self) -> None:
        tiles = self.reference.files[OUTPUT_TILES]
        report = self.reference.report["tiles"]
        self.assertEqual(b"\0" * 32, tiles[:32])
        self.assertEqual(report["unique_tiles_including_transparent"] * 32, len(tiles))
        self.assertGreater(report["reuse"]["exact"], 0)
        self.assertGreater(report["reuse"]["horizontal"], 0)
        self.assertGreater(report["reuse"]["vertical"], 0)
        self.assertLess(
            report["unique_tiles_including_transparent"],
            report["input_cells"] + 1,
        )

    def test_palette_binary_is_four_complete_big_endian_cram_banks(self) -> None:
        value = self.reference.files[OUTPUT_PALETTES]
        self.assertEqual(128, len(value))
        words = struct.unpack(">64H", value)
        self.assertEqual((0, 0, 0, 0), tuple(words[index * 16] for index in range(4)))
        self.assertTrue(all(word & 0xF111 == 0 for word in words))
        definitions = self.reference.report["palettes"]["definitions"]
        for definition in definitions:
            slot = definition["slot"]
            expected = tuple(
                int(text[1:], 16) for text in definition["words_hex"]
            )
            self.assertEqual(expected, words[slot * 16 : slot * 16 + len(expected)])

    def test_tilemap_header_directory_and_vdp_attributes_decode(self) -> None:
        value = self.reference.files[OUTPUT_TILEMAPS]
        magic, version, count, directory_offset, data_offset = struct.unpack_from(
            ">4sHHII", value, 0
        )
        self.assertEqual((b"R94M", FORMAT_VERSION, 1, 16, 48), (
            magic, version, count, directory_offset, data_offset
        ))
        raw_id, width, height, offset, size, reserved = struct.unpack_from(
            ">16sHHIII", value, directory_offset
        )
        self.assertEqual(b"boot_chamber", raw_id.rstrip(b"\0"))
        self.assertEqual((40, 28, 48, 2240, 0), (width, height, offset, size, reserved))
        self.assertEqual(len(value), offset + size)
        entries = struct.unpack_from(f">{width * height}H", value, offset)
        self.assertEqual(width * height, len(entries))
        self.assertEqual(2, sum(((entry >> 13) & 3) == 3 for entry in entries))
        self.assertGreater(sum(bool(entry & 0x8000) for entry in entries), 0)
        self.assertGreater(sum(bool(entry & 0x0800) for entry in entries), 0)
        self.assertGreater(sum(bool(entry & 0x1000) for entry in entries), 0)

    def test_sprite_mapping_header_piece_bounds_and_attributes_decode(self) -> None:
        value = self.reference.files[OUTPUT_SPRITES]
        magic, version, count, directory_offset, data_offset = struct.unpack_from(
            ">4sHHII", value, 0
        )
        self.assertEqual(b"R94S", magic)
        self.assertEqual(FORMAT_VERSION, version)
        self.assertEqual(19, count)
        self.assertEqual(16 + count * 32, data_offset)
        records = [
            struct.unpack_from(">16sHHHHII", value, directory_offset + index * 32)
            for index in range(count)
        ]
        door = next(
            item for item in records if item[0].rstrip(b"\0") == b"door_closed"
        )
        _, width, height, pieces, reserved, offset, size = door
        self.assertEqual((16, 32, 8, 0, 64), (width, height, pieces, reserved, size))
        decoded = [
            struct.unpack_from(">bbBBHH", value, offset + index * 8)
            for index in range(pieces)
        ]
        self.assertTrue(all(item[2:4] == (0, 0) for item in decoded))
        self.assertTrue(all(item[5] == 0 for item in decoded))
        self.assertTrue(all(((item[4] >> 13) & 3) == 3 for item in decoded))
        self.assertTrue(all(item[4] & 0x8000 for item in decoded))
        self.assertTrue(all(-128 <= item[0] <= 127 for item in decoded))
        self.assertTrue(all(-128 <= item[1] <= 127 for item in decoded))

    def test_numbered_echo_frames_and_distinct_animation_cadences_are_compiled(self) -> None:
        sprite_ids = {
            item["id"] for item in self.reference.report["sprites"]["frames"]
        }
        self.assertTrue({f"echo_{number}" for number in range(1, 7)} <= sprite_ids)
        animations = {
            item["id"]: item
            for item in self.reference.report["animations"]["entries"]
        }
        totals = [
            animations[f"echo_{number}_anim"]["total_ticks"]
            for number in range(1, 7)
        ]
        self.assertEqual(6, len(set(totals)))
        self.assertEqual([19, 21, 25, 31, 33, 39], totals)

    def test_animation_binary_uses_sprite_indices_and_declared_total_ticks(self) -> None:
        value = self.reference.files[OUTPUT_ANIMATIONS]
        magic, version, count, directory_offset, data_offset = struct.unpack_from(
            ">4sHHII", value, 0
        )
        self.assertEqual((b"R94N", FORMAT_VERSION, 13, 16, 16 + 13 * 32), (
            magic, version, count, directory_offset, data_offset
        ))
        records = [
            struct.unpack_from(">16sHHIIHH", value, 16 + index * 32)
            for index in range(count)
        ]
        echo6 = next(item for item in records if item[0].rstrip(b"\0") == b"echo_6_anim")
        _, loop_code, step_count, offset, size, total_ticks, reserved = echo6
        self.assertEqual((1, 2, 8, 39, 0), (
            loop_code, step_count, size, total_ticks, reserved
        ))
        steps = [
            struct.unpack_from(">HH", value, offset + index * 4)
            for index in range(step_count)
        ]
        self.assertEqual(39, sum(ticks for _, ticks in steps))
        self.assertTrue(all(frame_index < 19 for frame_index, _ in steps))

    def test_aggregate_bundle_sections_match_components_and_crc32(self) -> None:
        bundle = self.reference.files[OUTPUT_BUNDLE]
        magic, version, count, directory_offset, file_size = struct.unpack_from(
            ">4sHHII", bundle, 0
        )
        self.assertEqual((b"R94A", FORMAT_VERSION, 5, 16, len(bundle)), (
            magic, version, count, directory_offset, file_size
        ))
        expected = (
            (b"CRAM", OUTPUT_PALETTES),
            (b"TILE", OUTPUT_TILES),
            (b"TMAP", OUTPUT_TILEMAPS),
            (b"SPRT", OUTPUT_SPRITES),
            (b"ANIM", OUTPUT_ANIMATIONS),
        )
        previous_end = 16 + 5 * 16
        for index, (expected_type, name) in enumerate(expected):
            section_type, offset, size, crc = struct.unpack_from(
                ">4sIII", bundle, 16 + index * 16
            )
            payload = bundle[offset : offset + size]
            self.assertEqual(expected_type, section_type)
            self.assertEqual(self.reference.files[name], payload)
            self.assertEqual(zlib.crc32(payload) & 0xFFFFFFFF, crc)
            self.assertEqual(0, offset & 1)
            self.assertGreaterEqual(offset, previous_end)
            previous_end = offset + size

    def test_report_output_hashes_checksum_manifest_and_assembly_are_consistent(self) -> None:
        files = self.reference.files
        report = json.loads(files[OUTPUT_REPORT])
        for name, metadata in report["outputs"].items():
            self.assertEqual(len(files[name]), metadata["bytes"])
            self.assertEqual(
                hashlib.sha256(files[name]).hexdigest(), metadata["sha256"]
            )
        checksum_lines = files[OUTPUT_CHECKSUMS].decode("ascii").splitlines()
        self.assertEqual(len(files) - 1, len(checksum_lines))
        self.assertEqual(sorted(checksum_lines, key=lambda line: line.split("  ", 1)[1]), checksum_lines)
        for line in checksum_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256(files[name]).hexdigest(), digest)
        assembly = files[OUTPUT_ASSEMBLY].decode("ascii")
        self.assertIn("ART_FORMAT_VERSION equ 1", assembly)
        self.assertIn("ART_TILEMAP_COUNT equ 1", assembly)
        self.assertIn("ART_SPRITE_ECHO_6_INDEX equ", assembly)
        self.assertIn("ART_ANIM_ECHO_6_ANIM_INDEX equ", assembly)
        self.assertIn('incbin "assets/generated/art/recursion94_art.bin"', assembly)

    def test_report_does_not_claim_runtime_rendering_or_subjective_review(self) -> None:
        claims = self.reference.report["claims"]
        self.assertTrue(claims["source_validation"])
        self.assertTrue(claims["binary_format_validation"])
        self.assertTrue(claims["deterministic_generation"])
        self.assertFalse(claims["runtime_integration"])
        self.assertFalse(claims["emulator_rendering"])
        self.assertFalse(claims["subjective_visual_review"])

    def test_repeated_generation_and_semantically_reordered_json_are_identical(self) -> None:
        root, art, maps = self.make_source_copy()
        first = build_artifacts(art, maps)

        def reverse_objects(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: reverse_objects(child)
                    for key, child in reversed(tuple(value.items()))
                }
            if isinstance(value, list):
                return [reverse_objects(child) for child in value]
            return value

        for path in (*art.rglob("*.json"), *maps.rglob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(
                json.dumps(reverse_objects(value), ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        reordered = build_artifacts(art, maps)
        self.assertEqual(first.source_sha256, reordered.source_sha256)
        self.assertEqual(first.files, reordered.files)

        output = root / "generated"
        report = root / "asset-report.json"
        compile_assets(art, maps, output, report)
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        compile_assets(art, maps, output, report)
        after = {path.name: path.read_bytes() for path in output.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(before[OUTPUT_REPORT], report.read_bytes())
        check_generated(art, maps, output, report)

    def test_check_generated_detects_corruption_missing_extra_and_stale_evidence(self) -> None:
        root, art, maps = self.make_source_copy()
        output = root / "generated"
        report = root / "asset-report.json"
        compile_assets(art, maps, output, report)
        tiles = output / OUTPUT_TILES
        value = bytearray(tiles.read_bytes())
        value[-1] ^= 1
        tiles.write_bytes(value)
        with self.assertRaisesRegex(AssetCompileError, "stale or corrupt"):
            check_generated(art, maps, output, report)

        compile_assets(art, maps, output, report)
        (output / "unexpected.bin").write_bytes(b"x")
        with self.assertRaisesRegex(AssetCompileError, "file set mismatch"):
            check_generated(art, maps, output, report)

        (output / "unexpected.bin").unlink()
        report.write_bytes(b"{}")
        with self.assertRaisesRegex(AssetCompileError, "evidence report is stale"):
            check_generated(art, maps, output, report)

        compile_assets(art, maps, output, report)
        (output / OUTPUT_BUNDLE).unlink()
        with self.assertRaisesRegex(AssetCompileError, "file set mismatch"):
            check_generated(art, maps, output, report)

    def test_cli_compiles_and_checks_without_nonstandard_dependencies(self) -> None:
        root, art, maps = self.make_source_copy()
        output = root / "generated"
        report = root / "asset-report.json"
        base = [
            sys.executable,
            str(ROOT / "tools/asset_compiler/__main__.py"),
            "--art-root",
            str(art),
            "--map-root",
            str(maps),
            "--output",
            str(output),
            "--report",
            str(report),
        ]
        compiled = subprocess.run(
            base,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, compiled.returncode, compiled.stderr)
        self.assertIn("status=PASS", compiled.stdout)
        checked = subprocess.run(
            [*base, "--check"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertIn("verified", checked.stdout)
        self.assertEqual(self.reference.bundle_sha256, hashlib.sha256((output / OUTPUT_BUNDLE).read_bytes()).hexdigest())

    def test_strict_json_rejects_duplicate_keys_nonfinite_and_bom(self) -> None:
        _, art, maps = self.make_source_copy()
        path = art / "ui.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                '"asset_id": "boot_ui",',
                '"asset_id": "wrong",\n  "asset_id": "boot_ui",',
                1,
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.assert_invalid(art, maps, "duplicate object key")

        _, art, maps = self.make_source_copy()
        path = art / "manifest.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace('"rom_bytes_max": 65536', '"rom_bytes_max": NaN'),
            encoding="utf-8",
            newline="\n",
        )
        self.assert_invalid(art, maps, "non-finite")

        _, art, maps = self.make_source_copy()
        path = art / "palettes.json"
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        self.assert_invalid(art, maps, "BOM")

    def test_unknown_keys_undeclared_files_and_unsafe_paths_fail_closed(self) -> None:
        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "manifest.json",
            lambda value: value.__setitem__("runtime_result", "PASS"),
        )
        self.assert_invalid(art, maps, "unknown keys")

        _, art, maps = self.make_source_copy()
        shutil.copyfile(art / "ui.json", art / "undeclared.json")
        self.assert_invalid(art, maps, "manifest set mismatch")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "manifest.json",
            lambda value: value["asset_files"].__setitem__(0, "../font_ui.json"),
        )
        self.assert_invalid(art, maps, "unsafe relative path")

    def test_palette_slot_id_color_and_exact_capacity_invariants_are_enforced(self) -> None:
        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "palettes.json",
            lambda value: value["palettes"][1].__setitem__("slot", 0),
        )
        self.assert_invalid(art, maps, "duplicate palette slot")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "palettes.json",
            lambda value: value["palettes"][0].__setitem__("mode", "exact"),
        )
        self.assert_invalid(art, maps, "exact palette has 18")

        _, art, maps = self.make_source_copy()

        def collapse(value: dict[str, Any]) -> None:
            value["palettes"][1]["symbols"]["b"] = "#010101"
            value["palettes"][1]["symbols"]["c"] = "#020202"

        self.mutate(art / "palettes.json", collapse)
        self.assert_invalid(art, maps, "duplicate Genesis word")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "palettes.json",
            lambda value: value["palettes"][3]["symbols"].__setitem__(".", "#123456"),
        )
        self.assert_invalid(art, maps, "invalid one-byte symbol")

    def test_pixel_dimensions_symbols_boolean_widths_and_empty_sprites_are_rejected(self) -> None:
        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["frames"][0]["pixels"].__setitem__(0, "..............."),
        )
        self.assert_invalid(art, maps, "expected 16 symbols")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["frames"][0]["pixels"].__setitem__(0, ".......?........"),
        )
        self.assert_invalid(art, maps, "is not declared")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["frames"][0].__setitem__("width", 15),
        )
        self.assert_invalid(art, maps, "multiples of eight")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["frames"][0].__setitem__("priority", 1),
        )
        self.assert_invalid(art, maps, "expected Boolean")

        _, art, maps = self.make_source_copy()

        def empty_sprite(value: dict[str, Any]) -> None:
            value["frames"][0]["pixels"] = ["." * 16] * 16

        self.mutate(art / "actor_current.json", empty_sprite)
        self.assert_invalid(art, maps, "sprite frame is empty")

    def test_duplicate_frame_and_animation_reference_invariants_are_enforced(self) -> None:
        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["frames"][1].__setitem__(
                "id", value["frames"][0]["id"]
            ),
        )
        self.assert_invalid(art, maps, "duplicate frame ID")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["animations"][0]["steps"][0].__setitem__(
                "frame", "echo_1"
            ),
        )
        self.assert_invalid(art, maps, "is not in asset")

        _, art, maps = self.make_source_copy()
        self.mutate(
            art / "actor_current.json",
            lambda value: value["animations"][0]["steps"][0].__setitem__(
                "ticks", True
            ),
        )
        self.assert_invalid(art, maps, "expected integer")

    def test_map_references_initialization_bounds_and_operation_schema_are_enforced(self) -> None:
        _, art, maps = self.make_source_copy()

        def leave_uninitialized(value: dict[str, Any]) -> None:
            value["operations"] = [
                {
                    "op": "cell",
                    "tile": "bg_void",
                    "x": 0,
                    "y": 0,
                    "palette": 0,
                    "priority": False,
                    "hflip": False,
                    "vflip": False,
                }
            ]

        self.mutate(maps / "boot_chamber.json", leave_uninitialized)
        self.assert_invalid(art, maps, "was never initialized")

        _, art, maps = self.make_source_copy()
        self.mutate(
            maps / "boot_chamber.json",
            lambda value: value["operations"][3].__setitem__("width", 39),
        )
        self.assert_invalid(art, maps, "rectangle exceeds map bounds")

        _, art, maps = self.make_source_copy()
        self.mutate(
            maps / "boot_chamber.json",
            lambda value: value["operations"][0].__setitem__("tile", "cur_idle0"),
        )
        self.assert_invalid(art, maps, "not a single 8x8 tile")

        _, art, maps = self.make_source_copy()
        self.mutate(
            maps / "boot_chamber.json",
            lambda value: value["operations"][0].__setitem__("priority", 1),
        )
        self.assert_invalid(art, maps, "expected Boolean")

        _, art, maps = self.make_source_copy()
        self.mutate(
            maps / "boot_chamber.json",
            lambda value: value["operations"][0].__setitem__("blend", "add"),
        )
        self.assert_invalid(art, maps, "unknown keys")

    def test_all_budget_classes_fail_compilation_when_tightened(self) -> None:
        budget_cases = {
            "rom_bytes_max": (1024, "rom_bundle_bytes"),
            "vram_tile_bytes_max": (32, "vram_tile_bytes"),
            "vram_map_bytes_max": (2, "vram_largest_map_bytes"),
            "cram_words_max": (63, "cram_words"),
            "unique_tiles_max": (1, "unique_tiles"),
            "sprite_pieces_per_frame_max": (1, "sprite_pieces_per_frame"),
            "sprite_frames_max": (1, "sprite_frames"),
            "animations_max": (1, "animations"),
        }
        for key, (limit, report_key) in budget_cases.items():
            with self.subTest(key=key):
                _, art, maps = self.make_source_copy()
                self.mutate(
                    art / "manifest.json",
                    lambda value, key=key, limit=limit: value["budgets"].__setitem__(
                        key, limit
                    ),
                )
                self.assert_invalid(art, maps, report_key)


if __name__ == "__main__":
    unittest.main()
