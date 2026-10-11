from __future__ import annotations

import hashlib
import json
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any, Callable

from tools.content_compiler.compiler import (
    CHUNK_HEADER_SIZE,
    DIRECTORY_ENTRY_SIZE,
    ENTITY_RECORD_SIZE,
    FILE_HEADER_SIZE,
    FORMAT_VERSION,
    OBJECTIVE_RECORD_SIZE,
    OUTPUT_ASSEMBLY,
    OUTPUT_BINARY,
    OUTPUT_CHECKSUMS,
    OUTPUT_REPORT,
    PHASE_RECORD_SIZE,
    POLICY_RECORD_SIZE,
    ROLE_RECORD_SIZE,
    ContentValidationError,
    build_artifacts,
    check_generated,
    compile_content,
    load_and_validate,
    verify_binary,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONTENT = ROOT / "content"


class ContentCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference_bundle = build_artifacts(SOURCE_CONTENT)

    def make_content_copy(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "content"
        shutil.copytree(SOURCE_CONTENT, destination)
        return destination

    def mutate_json(
        self,
        content: Path,
        relative: str,
        mutation: Callable[[dict[str, Any]], None],
    ) -> Path:
        path = content / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        mutation(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def assert_invalid(
        self,
        content: Path,
        pattern: str,
    ) -> None:
        with self.assertRaisesRegex(ContentValidationError, pattern):
            build_artifacts(content)

    def test_current_campaign_validates_exact_roster_and_composition(self) -> None:
        validated = load_and_validate(SOURCE_CONTENT)
        self.assertEqual(20, len(validated.encounters))
        self.assertEqual(
            15, sum(item["kind"] == "primary" for item in validated.encounters)
        )
        self.assertEqual(
            5, sum(item["kind"] == "boss" for item in validated.encounters)
        )
        self.assertEqual(
            "0789aa5c727a25de3b8d6b04996e5a7c8eac366cd56b5e3c4d9b9303eb80b872",
            validated.source_sha256,
        )

    def test_binary_header_directory_and_collision_decode_big_endian(self) -> None:
        binary = self.reference_bundle.files[OUTPUT_BINARY]
        (
            magic,
            version,
            header_size,
            encounter_count,
            directory_size,
            directory_offset,
            data_offset,
            file_size,
            payload_crc,
            flags,
        ) = struct.unpack_from(">4sHHHHIIIII", binary, 0)
        self.assertEqual(b"R94C", magic)
        self.assertEqual(FORMAT_VERSION, version)
        self.assertEqual(FILE_HEADER_SIZE, header_size)
        self.assertEqual(20, encounter_count)
        self.assertEqual(DIRECTORY_ENTRY_SIZE, directory_size)
        self.assertEqual(FILE_HEADER_SIZE, directory_offset)
        self.assertEqual(
            FILE_HEADER_SIZE + 20 * DIRECTORY_ENTRY_SIZE, data_offset
        )
        self.assertEqual(len(binary), file_size)
        self.assertEqual(1, flags)
        self.assertEqual(
            payload_crc, zlib.crc32(binary[directory_offset:]) & 0xFFFFFFFF
        )

        (
            encounter_id,
            numeric_id,
            order,
            sector,
            kind,
            _chunk_flags,
            entity_count,
            chunk_offset,
            chunk_size,
            chunk_crc,
            objective_count,
            role_count,
            phase_count,
            policy_count,
        ) = struct.unpack_from(">4sHBBBBHIIIHHHH", binary, directory_offset)
        self.assertEqual(b"B01\x00", encounter_id)
        self.assertEqual(b"\x00e", binary[directory_offset + 4 : directory_offset + 6])
        self.assertEqual(101, numeric_id)
        self.assertEqual((1, 1, 0), (order, sector, kind))
        self.assertEqual(3, entity_count)
        self.assertEqual(3, objective_count)
        self.assertEqual(2, role_count)
        self.assertEqual(0, phase_count)
        self.assertEqual(0, policy_count)
        chunk = binary[chunk_offset : chunk_offset + chunk_size]
        self.assertEqual(chunk_crc, zlib.crc32(chunk) & 0xFFFFFFFF)
        self.assertEqual(b"ENCT", chunk[:4])
        self.assertEqual(CHUNK_HEADER_SIZE, struct.unpack_from(">H", chunk, 6)[0])
        collision_offset, collision_size = struct.unpack_from(">II", chunk, 62)
        self.assertEqual(35, collision_size)
        collision = chunk[collision_offset : collision_offset + collision_size]
        decoded: list[str] = []
        for cell_index in range(20 * 14):
            solid = collision[cell_index // 8] & (1 << (7 - cell_index % 8))
            decoded.append("#" if solid else ".")
        rows = ["".join(decoded[row * 20 : (row + 1) * 20]) for row in range(14)]
        source = json.loads(
            (SOURCE_CONTENT / "encounters/boot/B01.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(source["room"]["tile_grid"], rows)
        verify_binary(binary)

    def test_chunk_record_sizes_and_oracle_policy_are_declared(self) -> None:
        binary = self.reference_bundle.files[OUTPUT_BINARY]
        directory_offset = FILE_HEADER_SIZE
        i04_entry = directory_offset + 19 * DIRECTORY_ENTRY_SIZE
        unpacked = struct.unpack_from(">4sHBBBBHIIIHHHH", binary, i04_entry)
        chunk_offset, chunk_size = unpacked[7], unpacked[8]
        policy_count = unpacked[13]
        chunk = binary[chunk_offset : chunk_offset + chunk_size]
        (
            entity_size,
            objective_size,
            role_size,
            phase_size,
            header_policy_count,
            policy_size,
            policy_offset,
            detail_offset,
            reserved,
        ) = struct.unpack_from(">HHHHHHIII", chunk, 120)
        self.assertEqual(ENTITY_RECORD_SIZE, entity_size)
        self.assertEqual(OBJECTIVE_RECORD_SIZE, objective_size)
        self.assertEqual(ROLE_RECORD_SIZE, role_size)
        self.assertEqual(PHASE_RECORD_SIZE, phase_size)
        self.assertEqual(POLICY_RECORD_SIZE, policy_size)
        self.assertEqual(4, policy_count)
        self.assertEqual(policy_count, header_policy_count)
        self.assertLess(policy_offset, detail_offset)
        self.assertEqual(0, reserved)

    def test_generation_rerun_and_check_are_byte_identical(self) -> None:
        content = self.make_content_copy()
        output = content.parent / "generated"
        first = compile_content(content, output)
        first_bytes = {
            name: (output / name).read_bytes() for name in sorted(first.files)
        }
        second = compile_content(content, output)
        second_bytes = {
            name: (output / name).read_bytes() for name in sorted(second.files)
        }
        self.assertEqual(first_bytes, second_bytes)
        checked = check_generated(content, output)
        self.assertEqual(first.binary_sha256, checked.binary_sha256)
        self.assertEqual(
            hashlib.sha256(first_bytes[OUTPUT_BINARY]).hexdigest(),
            first.binary_sha256,
        )

    def test_json_object_key_order_does_not_change_any_artifact(self) -> None:
        content = self.make_content_copy()

        def reverse_objects(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: reverse_objects(child)
                    for key, child in reversed(tuple(value.items()))
                }
            if isinstance(value, list):
                return [reverse_objects(child) for child in value]
            return value

        for relative in ("campaign.json", "encounters/boot/B01.json"):
            path = content / relative
            original = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(
                json.dumps(reverse_objects(original), ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        reordered = build_artifacts(content)
        self.assertEqual(self.reference_bundle.files, reordered.files)

    def test_campaign_encounter_array_order_is_semantic_and_fail_closed(self) -> None:
        content = self.make_content_copy()

        def swap_entries(campaign: dict[str, Any]) -> None:
            campaign["encounters"][0], campaign["encounters"][1] = (
                campaign["encounters"][1],
                campaign["encounters"][0],
            )

        self.mutate_json(content, "campaign.json", swap_entries)
        self.assert_invalid(content, "outside 1\\.\\.1")

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "campaign.json",
            lambda value: value["encounters"][0].__setitem__("order", True),
        )
        self.assert_invalid(content, "Boolean is not an integer")

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self) -> None:
        content = self.make_content_copy()
        path = content / "encounters/boot/B01.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                '"schema": "recursion94.encounter",',
                '"schema": "wrong",\n  "schema": "recursion94.encounter",',
                1,
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.assert_invalid(content, "duplicate object key")

        content = self.make_content_copy()
        path = content / "encounters/boot/B01.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace('"cpu_percent": 18', '"cpu_percent": NaN', 1),
            encoding="utf-8",
            newline="\n",
        )
        self.assert_invalid(content, "non-finite number")

    def test_unknown_field_and_extra_manifest_fail_closed(self) -> None:
        content = self.make_content_copy()
        self.mutate_json(
            content,
            "encounters/boot/B01.json",
            lambda value: value.__setitem__("runtime_outcome", "PASS"),
        )
        self.assert_invalid(content, "unknown keys")

        content = self.make_content_copy()
        extra = content / "encounters/boot/B99.json"
        shutil.copyfile(content / "encounters/boot/B01.json", extra)
        self.assert_invalid(content, "manifest set mismatch")

    def test_duplicate_entity_and_replay_ids_are_rejected(self) -> None:
        content = self.make_content_copy()

        def duplicate_entity(value: dict[str, Any]) -> None:
            value["entities"]["devices"][1]["logical_id"] = value["entities"][
                "devices"
            ][0]["logical_id"]

        self.mutate_json(content, "encounters/boot/B01.json", duplicate_entity)
        self.assert_invalid(content, "duplicate entity ID")

        content = self.make_content_copy()

        def duplicate_replay(value: dict[str, Any]) -> None:
            value["replays"]["failure"] = value["replays"]["canonical"]

        self.mutate_json(content, "encounters/boot/B01.json", duplicate_replay)
        self.assert_invalid(content, "duplicate replay declaration ID")

    def test_out_of_range_and_boolean_coordinates_are_rejected(self) -> None:
        content = self.make_content_copy()

        def out_of_range(value: dict[str, Any]) -> None:
            value["entities"]["devices"][0]["x"] = 320

        self.mutate_json(content, "encounters/boot/B01.json", out_of_range)
        self.assert_invalid(content, "outside 0\\.\\.319")

        content = self.make_content_copy()

        def boolean_coordinate(value: dict[str, Any]) -> None:
            value["room"]["spawn"]["x"] = True

        self.mutate_json(content, "encounters/boot/B01.json", boolean_coordinate)
        self.assert_invalid(content, "Boolean is not an integer")

    def test_objective_cycle_and_unknown_action_target_are_rejected(self) -> None:
        content = self.make_content_copy()

        def cycle(value: dict[str, Any]) -> None:
            nodes = value["objective_graph"]["nodes"]
            nodes[0]["depends_on"] = [nodes[-1]["objective_id"]]

        self.mutate_json(content, "encounters/boot/B01.json", cycle)
        self.assert_invalid(content, "cycle includes")

        content = self.make_content_copy()

        def invalid_target(value: dict[str, Any]) -> None:
            value["canonical_role_schedule"][0]["actions"][0][
                "target"
            ] = "B01_MISSING_DEVICE"

        self.mutate_json(content, "encounters/boot/B01.json", invalid_target)
        self.assert_invalid(content, "unknown entity B01_MISSING_DEVICE")

    def test_structured_runtime_conditions_are_strict_and_directed(self) -> None:
        validated = load_and_validate(SOURCE_CONTENT)
        b03 = next(item for item in validated.encounters if item["id"] == "B03")
        self.assertEqual(b03["stage_completion"]["object"], "B03_COMMIT")
        self.assertEqual(
            [item["policy"] for item in b03["dependency_policy"]],
            ["ALL", "ALL", "ALL"],
        )
        self.assertEqual(
            [
                condition["provider"]
                for condition in b03["dependency_policy"][1]["conditions"]
            ],
            ["B03_SWITCH_A", "B03_NODE"],
        )
        self.assertEqual(
            [
                condition["provider"]
                for condition in b03["dependency_policy"][2]["conditions"]
            ],
            ["B03_DOOR"],
        )
        self.assertEqual(
            b03["stage_completion"]["completion_state"],
            {"field": "state", "mask": 4103, "value": 4103},
        )

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "encounters/boot/B03.json",
            lambda value: value["dependency_policy"][0].__setitem__(
                "policy", "OR"
            ),
        )
        self.assert_invalid(content, "expected ALL or ANY")

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "encounters/boot/B03.json",
            lambda value: value["dependency_policy"][0]["conditions"][0].__setitem__(
                "provider", "B03_MISSING"
            ),
        )
        self.assert_invalid(content, "unknown entity B03_MISSING")

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "encounters/boot/B03.json",
            lambda value: value["stage_completion"]["completion_state"].__setitem__(
                "value", 8
            ),
        )
        self.assert_invalid(content, "outside the comparison mask")

    def test_anchor_whitelist_loop_and_budget_caps_are_enforced(self) -> None:
        content = self.make_content_copy()

        def invalid_anchor(value: dict[str, Any]) -> None:
            entity = value["entities"]["devices"][0]
            entity["anchor_policy"] = {
                "allowed": True,
                "record_type": "raw_state_byte",
                "max_records": 1,
                "idempotent_state": entity["state"]["initial"],
            }

        self.mutate_json(content, "encounters/boot/B01.json", invalid_anchor)
        self.assert_invalid(content, "unknown typed anchor record")

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "encounters/boot/B01.json",
            lambda value: value["loop"].__setitem__("authoritative_frames", 719),
        )
        self.assert_invalid(content, "outside 720\\.\\.720")

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "encounters/interrupt/I04.json",
            lambda value: value["budgets"].__setitem__("dma_words", 2049),
        )
        self.assert_invalid(content, "outside 0\\.\\.2048")

    def test_unknown_parameter_and_dangling_parameter_reference_are_rejected(self) -> None:
        content = self.make_content_copy()

        def unknown_parameter(value: dict[str, Any]) -> None:
            value["entities"]["devices"][0]["parameters"]["raw_byte"] = 7

        self.mutate_json(content, "encounters/boot/B01.json", unknown_parameter)
        self.assert_invalid(content, "unknown parameter key")

        content = self.make_content_copy()

        def dangling_reference(value: dict[str, Any]) -> None:
            value["entities"]["devices"][0]["parameters"]["controls"] = [
                "B01_DOES_NOT_EXIST"
            ]

        self.mutate_json(content, "encounters/boot/B01.json", dangling_reference)
        self.assert_invalid(content, "unknown stable reference")

    def test_reachability_and_composition_claims_are_recomputed(self) -> None:
        content = self.make_content_copy()
        self.mutate_json(
            content,
            "campaign.json",
            lambda value: value["reachability"].__setitem__("edge_count", 18),
        )
        self.assert_invalid(content, "outside 19\\.\\.19")

        content = self.make_content_copy()
        self.mutate_json(
            content,
            "campaign.json",
            lambda value: value["declared_counts"].__setitem__(
                "primary_encounters", 14
            ),
        )
        self.assert_invalid(content, "outside 15\\.\\.15")

    def test_binary_corruption_is_detected_even_when_outer_crc_is_repaired(self) -> None:
        binary = bytearray(self.reference_bundle.files[OUTPUT_BINARY])
        directory_offset = FILE_HEADER_SIZE
        chunk_offset = struct.unpack_from(">I", binary, directory_offset + 12)[0]
        chunk_size = struct.unpack_from(">I", binary, directory_offset + 16)[0]
        struct.pack_into(">I", binary, chunk_offset + 8, chunk_size - 2)
        chunk_crc = zlib.crc32(binary[chunk_offset : chunk_offset + chunk_size]) & 0xFFFFFFFF
        struct.pack_into(">I", binary, directory_offset + 20, chunk_crc)
        payload_crc = zlib.crc32(binary[directory_offset:]) & 0xFFFFFFFF
        struct.pack_into(">I", binary, 24, payload_crc)
        with self.assertRaisesRegex(ContentValidationError, "invalid encounter header"):
            verify_binary(bytes(binary))

        binary = bytearray(self.reference_bundle.files[OUTPUT_BINARY])
        chunk_offset = struct.unpack_from(">I", binary, directory_offset + 12)[0]
        chunk_size = struct.unpack_from(">I", binary, directory_offset + 16)[0]
        entity_offset = struct.unpack_from(">I", binary, chunk_offset + 70)[0]
        struct.pack_into(
            ">I",
            binary,
            chunk_offset + entity_offset + 20,
            chunk_size + 2,
        )
        chunk_crc = (
            zlib.crc32(binary[chunk_offset : chunk_offset + chunk_size]) & 0xFFFFFFFF
        )
        struct.pack_into(">I", binary, directory_offset + 20, chunk_crc)
        payload_crc = zlib.crc32(binary[directory_offset:]) & 0xFFFFFFFF
        struct.pack_into(">I", binary, 24, payload_crc)
        with self.assertRaisesRegex(ContentValidationError, "outside detail section"):
            verify_binary(bytes(binary))

    def test_check_generated_detects_stale_bytes_and_unexpected_files(self) -> None:
        content = self.make_content_copy()
        output = content.parent / "generated"
        compile_content(content, output)
        binary_path = output / OUTPUT_BINARY
        corrupted = bytearray(binary_path.read_bytes())
        corrupted[-1] ^= 0x01
        binary_path.write_bytes(corrupted)
        with self.assertRaisesRegex(ContentValidationError, "stale or corrupted"):
            check_generated(content, output)

        compile_content(content, output)
        (output / "unexpected.bin").write_bytes(b"x")
        with self.assertRaisesRegex(ContentValidationError, "file set mismatch"):
            check_generated(content, output)
        with self.assertRaisesRegex(ContentValidationError, "unexpected generated"):
            compile_content(content, output)

    def test_report_checksums_symbols_and_no_replay_outcomes(self) -> None:
        files = self.reference_bundle.files
        report = json.loads(files[OUTPUT_REPORT])
        self.assertEqual("big", report["format"]["byte_order"])
        self.assertEqual(20, report["composition"]["reachable_encounters"])
        self.assertFalse(report["runtime_contract"]["replay_bytes_embedded"])
        self.assertFalse(report["runtime_contract"]["replay_outcomes_embedded"])
        self.assertEqual(
            hashlib.sha256(files[OUTPUT_BINARY]).hexdigest(),
            report["outputs"]["binary"]["sha256"],
        )
        assembly = files[OUTPUT_ASSEMBLY].decode("ascii")
        self.assertIn("CONTENT_FORMAT_VERSION equ $0001", assembly)
        self.assertIn("CONTENT_ENTITY_TYPE_PRESSURE_SWITCH equ $D8AD", assembly)
        self.assertIn("CONTENT_ENCOUNTER_I04_DIRECTORY_INDEX equ 19", assembly)
        self.assertIn("ContentCampaignBinary:", assembly)
        checksum_lines = files[OUTPUT_CHECKSUMS].decode("ascii").splitlines()
        self.assertEqual(3, len(checksum_lines))
        self.assertTrue(checksum_lines[0].endswith(f"  {OUTPUT_BINARY}"))
        self.assertTrue(checksum_lines[1].endswith(f"  {OUTPUT_ASSEMBLY}"))
        self.assertTrue(checksum_lines[2].endswith(f"  {OUTPUT_REPORT}"))


if __name__ == "__main__":
    unittest.main()
