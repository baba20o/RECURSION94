from __future__ import annotations

import copy
import hashlib
import json
import re
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.campaign_runtime.compiler import (
    DEPENDENCY_EDGE_BYTES,
    DEPENDENCY_GROUP_BYTES,
    FORMAT_VERSION,
    METADATA_OWNER_FUTURE_BOSS,
    METADATA_OWNER_VOCABULARY,
    OBJECT_METADATA_BYTES,
    OUTPUT_ASSEMBLY,
    OUTPUT_REPORT,
    POOL_CAPACITY_BY_TYPE,
    PROFILE_BYTES,
    SEMANTIC_RECORD_BYTES,
    STATIC_RECT_CAPACITY,
    RuntimeCompileError,
    _flatten_entities,
    build_runtime_artifacts,
    check_generated,
    compile_runtime,
    decode_dependency_edge_record,
    decode_dependency_group_record,
    decode_profile_record,
    decode_semantic_record,
    merge_collision_rectangles,
)
from tools.content_compiler.compiler import (
    ENTITY_CATEGORIES,
    load_and_validate,
)
from tools.m5_reference.manifest import (
    ADAPTER_FORMAT_VERSION,
    AUTHORED_STATES_BY_TYPE,
    ENTITY_VOCABULARY_MANIFEST,
    OWNER_FUTURE_BOSS,
    PARAM_DEVICE_LINK,
    PARAM_DEVICE_TARGET,
    PARAM_HAZARD_RADIUS,
    PARAM_UNUSED,
    REFERENCE_FIELD_REGISTRY,
    REFERENCE_REGISTRY,
    SEMANTIC_TOKEN_REGISTRY,
    manifest_sha256,
)


ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"
WARNING_RE = re.compile(
    r"(?i)(?:^|\s)warning(?:\s+[A-Za-z0-9_-]+)?\s*(?::|\[)"
)


class CampaignRuntimeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validated = load_and_validate(CONTENT)
        cls.artifacts = build_runtime_artifacts(CONTENT)
        cls.assembly = cls.artifacts.files[OUTPUT_ASSEMBLY].decode("utf-8")
        cls.report = json.loads(
            cls.artifacts.files[OUTPUT_REPORT].decode("utf-8")
        )
        cls.report_by_id = {
            item["id"]: item for item in cls.report["encounters"]
        }

    def test_v3_adapter_covers_campaign_and_exhaustive_registries(self) -> None:
        self.assertEqual(FORMAT_VERSION, ADAPTER_FORMAT_VERSION)
        self.assertEqual(FORMAT_VERSION, 3)
        self.assertEqual(self.report["schema_version"], 3)
        self.assertEqual(self.report["format_version"], 3)
        self.assertEqual(PROFILE_BYTES, 64)
        self.assertEqual(SEMANTIC_RECORD_BYTES, 16)
        self.assertEqual(DEPENDENCY_GROUP_BYTES, 16)
        self.assertEqual(DEPENDENCY_EDGE_BYTES, 8)
        self.assertEqual(self.artifacts.encounter_count, 20)
        self.assertEqual(self.report["entity_type_count"], 83)
        self.assertEqual(self.report["semantic_token_count"], 58)
        self.assertEqual(len(SEMANTIC_TOKEN_REGISTRY), 58)
        self.assertEqual(len(self.report["reference_contracts"]), 20)
        self.assertEqual(
            {
                item["parameter_key"]
                for item in self.report["reference_contracts"]
            },
            set(REFERENCE_REGISTRY),
        )
        self.assertEqual(
            {
                item["name"] for item in self.report["reference_fields"]
            },
            set(REFERENCE_FIELD_REGISTRY),
        )
        self.assertEqual(
            self.report["adapter_contract_sha256"], manifest_sha256()
        )
        self.assertEqual(
            self.artifacts.contract_sha256,
            self.report["adapter_contract_sha256"],
        )
        expected = (
            "B01", "B02", "B03", "B04",
            "V01", "V02", "V03", "V04",
            "S01", "S02", "S03", "S04",
            "M01", "M02", "M03", "M04",
            "I01", "I02", "I03", "I04",
        )
        self.assertEqual(
            tuple(item["id"] for item in self.report["encounters"]),
            expected,
        )
        contract_types = {
            item["entity_type"]
            for item in self.report["translation_contracts"]
        }
        self.assertEqual(contract_types, set(ENTITY_VOCABULARY_MANIFEST))
        self.assertEqual(contract_types, set(AUTHORED_STATES_BY_TYPE))
        for encounter_id in expected:
            self.assertIn(
                f"CampaignRuntimeDescriptor{encounter_id}:",
                self.assembly,
            )
            self.assertIn(
                f"CampaignRuntimeProfile{encounter_id}:",
                self.assembly,
            )
            self.assertIn(
                f"CampaignRuntimeObjectMetadata{encounter_id}:",
                self.assembly,
            )

    def test_every_authored_named_state_emits_explicit_vocab_bits(self) -> None:
        observed_states: dict[str, set[str]] = {
            entity_type: set()
            for entity_type in ENTITY_VOCABULARY_MANIFEST
        }
        non_ordinal_count = 0
        for encounter in self.validated.encounters:
            authored = [
                entity
                for category in ENTITY_CATEGORIES
                for entity in encounter["entities"][category]
            ]
            emitted = self.report_by_id[encounter["id"]]["objects"]
            self.assertEqual(len(authored), len(emitted))
            for source, output in zip(authored, emitted):
                contract = ENTITY_VOCABULARY_MANIFEST[source["type"]]
                state_map = contract.state_map()
                observed_states[source["type"]].update(
                    source["state"]["states"]
                )
                self.assertLessEqual(
                    set(source["state"]["states"]), set(state_map)
                )
                self.assertEqual(
                    output["initial_state_name"], source["state"]["initial"]
                )
                expected_base = state_map[source["state"]["initial"]]
                expected = expected_base
                if output["category"] == "enemies" or (
                    output["category"] == "devices"
                    and "health" in source.get("parameters", {})
                ):
                    expected |= source.get("parameters", {}).get("health", 1) << 8
                self.assertEqual(output["state_base_bits"], expected_base)
                self.assertEqual(output["object_state"], expected)
                self.assertEqual(
                    output["semantic_record"]["authored_parameter_count"],
                    len(source["parameters"]),
                )
                ordinal = source["state"]["states"].index(
                    source["state"]["initial"]
                )
                if expected != ordinal:
                    non_ordinal_count += 1
        self.assertGreater(non_ordinal_count, 40)
        for entity_type, states in observed_states.items():
            with self.subTest(entity_type=entity_type):
                self.assertEqual(
                    states, set(AUTHORED_STATES_BY_TYPE[entity_type])
                )

    def test_param_high_byte_timer_and_semantic_low_byte_are_exact(self) -> None:
        objects = {
            item["authored_logical_id"]: item
            for encounter in self.report["encounters"]
            for item in encounter["objects"]
        }
        for item in objects.values():
            contract = ENTITY_VOCABULARY_MANIFEST[item["entity_type"]]
            self.assertEqual(item["object_timer"], 0)
            self.assertEqual(item["vocab_kind"], contract.vocab_kind)
            self.assertEqual(item["object_param"] & 0xFF00, contract.vocab_kind)
            self.assertEqual(item["object_param"] & 0x00FF, item["param_low"])
            self.assertEqual(item["param_semantic"], contract.param_semantic)
            if contract.param_semantic == PARAM_DEVICE_LINK:
                self.assertEqual(
                    item["param_low"], item["dependency_group"]
                )
            elif contract.param_semantic == PARAM_DEVICE_TARGET:
                self.assertEqual(
                    item["param_low"], item["object_index"]
                )
            elif contract.param_semantic == PARAM_UNUSED:
                self.assertEqual(item["param_low"], 0)
            else:
                self.assertEqual(contract.param_semantic, PARAM_HAZARD_RADIUS)
                self.assertLessEqual(item["param_low"], 0xFF)
        self.assertEqual(objects["B01_PLATE_A"]["param_low"], 0)
        self.assertEqual(objects["B01_DOOR_A"]["param_low"], 1)
        self.assertEqual(objects["B02_EXIT"]["param_low"], 1)
        self.assertEqual(objects["B03_NODE"]["param_low"], 1)
        self.assertEqual(objects["B03_DOOR"]["param_low"], 2)
        self.assertEqual(objects["M01_REPAIR"]["param_low"], 2)
        self.assertEqual(objects["I01_RASTER_CUT"]["param_low"], 160)
        self.assertEqual(objects["V03_SWEEPER"]["param_low"], 0)
        self.assertEqual(objects["S01_PURSUER"]["object_state"], 0x0100)
        self.assertEqual(objects["M02_SCRUBBER"]["object_state"], 0x0401)
        self.assertEqual(objects["B02_REAR_NODE"]["object_state"], 0x0200)
        self.assertEqual(objects["B03_NODE"]["object_state"], 0x0300)

    def test_parallel_metadata_is_one_to_one_and_preserves_full_type(self) -> None:
        metadata = self.report["rom_metadata"]
        self.assertEqual(metadata["entry_bytes"], OBJECT_METADATA_BYTES)
        self.assertEqual(OBJECT_METADATA_BYTES, 4)
        total_objects = 0
        deferred = []
        for encounter in self.report["encounters"]:
            self.assertEqual(
                encounter["metadata_entries"], encounter["entities"]
            )
            total_objects += encounter["entities"]
            for item in encounter["objects"]:
                contract = ENTITY_VOCABULARY_MANIFEST[item["entity_type"]]
                record = item["metadata"]
                self.assertEqual(
                    record["original_type_code"],
                    contract.original_type_code,
                )
                self.assertEqual(
                    record["dependency_group"],
                    item["dependency_group"],
                )
                if contract.deferred:
                    deferred.append(item)
                    self.assertEqual(record["owner"], OWNER_FUTURE_BOSS)
                    self.assertEqual(
                        record["owner_code"], METADATA_OWNER_FUTURE_BOSS
                    )
                    self.assertEqual(item["vocab_kind"], 0)
                    self.assertEqual(item["object_state"], 0)
                else:
                    self.assertEqual(
                        record["owner_code"], METADATA_OWNER_VOCABULARY
                    )
        self.assertEqual(total_objects, 166)
        self.assertGreater(len(deferred), 10)
        self.assertEqual(
            set(self.report["deferred_boss"]["entity_types"]),
            {
                entity_type
                for entity_type, contract
                in ENTITY_VOCABULARY_MANIFEST.items()
                if contract.deferred
            },
        )
        self.assertIn(
            "CampaignRuntimeObjectMetadataTable:", self.assembly
        )
        self.assertIn(
            "CAMPAIGN_RUNTIME_METADATA_OWNER_FUTURE_BOSS equ $02",
            self.assembly,
        )

    def test_authored_references_are_explicit_and_directed(self) -> None:
        observed_keys = set()
        for encounter in self.report["encounters"]:
            for link in encounter["authored_references"]:
                parameter_key = (
                    link["parameter_path"].split(".")[1].split("[")[0]
                )
                observed_keys.add(parameter_key)
                contract = REFERENCE_REGISTRY[parameter_key]
                self.assertEqual(link["reference_id"], contract.reference_id)
                self.assertEqual(link["direction"], contract.direction)
                self.assertEqual(
                    link["dependency_policy"],
                    contract.dependency_policy,
                )
                self.assertNotEqual(link["provider"], link["consumer"])
        self.assertEqual(observed_keys, set(REFERENCE_REGISTRY))
        b03 = self.report_by_id["B03"]
        alias = [
            link
            for link in b03["authored_references"]
            if link["authored_reference"] == "B03_NODE_ARM"
        ]
        self.assertEqual(len(alias), 1)
        self.assertEqual(alias[0]["target"], "B03_NODE")
        i04_links = [
            link
            for link in self.report_by_id["I04"]["authored_references"]
            if link["parameter_path"] == "parameters.opens_from"
        ]
        self.assertEqual(len(i04_links), 1)
        self.assertEqual(
            i04_links[0]["authored_reference"], "I04_TRANSMITTER"
        )
        self.assertEqual(i04_links[0]["target"], "I04_TRANSMITTER")

    def test_b01_and_b02_dependencies_preserve_stage_playability(self) -> None:
        b01_group = self.report_by_id["B01"]["dependency_groups"]
        self.assertEqual(len(b01_group), 1)
        self.assertEqual(
            (
                b01_group[0]["consumer"],
                b01_group[0]["policy"],
                b01_group[0]["result"]["mask"],
                b01_group[0]["result"]["value"],
            ),
            ("B01_DOOR_A", "ALL", 0x0002, 0x0002),
        )
        self.assertEqual(
            [
                (
                    edge["provider"],
                    edge["field"],
                    edge["mask"],
                    edge["value"],
                )
                for edge in b01_group[0]["edges"]
            ],
            [("B01_PLATE_A", "state", 0x0001, 0x0001)],
        )

        b02_group = self.report_by_id["B02"]["dependency_groups"]
        self.assertEqual(len(b02_group), 1)
        self.assertEqual(
            (
                b02_group[0]["consumer"],
                b02_group[0]["policy"],
                b02_group[0]["result"]["mask"],
                b02_group[0]["result"]["value"],
            ),
            ("B02_EXIT", "ALL", 0x0001, 0x0001),
        )
        self.assertEqual(
            [
                (
                    edge["provider"],
                    edge["field"],
                    edge["mask"],
                    edge["value"],
                )
                for edge in b02_group[0]["edges"]
            ],
            [("B02_REAR_NODE", "health", 0x00FF, 0x0000)],
        )

    def test_b03_directed_all_dependencies_and_completion_are_exact(self) -> None:
        b03 = self.report_by_id["B03"]
        self.assertEqual(
            b03["stage_completion"],
            {
                "object_index": 4,
                "object": "B03_COMMIT",
                "field_id": 1,
                "field": "state",
                "mask": 0x1007,
                "value": 0x1007,
            },
        )
        self.assertEqual(b03["dependency_edges"], 4)
        groups = b03["dependency_groups"]
        self.assertEqual([item["group_id"] for item in groups], [1, 2, 3])
        exposure, door, commit = groups
        self.assertEqual(
            (exposure["consumer"], exposure["policy"]),
            ("B03_NODE", "ALL"),
        )
        self.assertEqual(
            [
                (
                    edge["provider"],
                    edge["field"],
                    edge["mask"],
                    edge["value"],
                )
                for edge in exposure["edges"]
            ],
            [("B03_SWITCH_B", "state", 0x0001, 0x0001)],
        )
        self.assertEqual(
            (door["consumer"], door["policy"], door["duration_frames"]),
            ("B03_DOOR", "ALL", 150),
        )
        self.assertEqual(
            [
                (
                    edge["provider"],
                    edge["field"],
                    edge["mask"],
                    edge["value"],
                )
                for edge in door["edges"]
            ],
            [
                ("B03_SWITCH_A", "state", 0x0001, 0x0001),
                ("B03_NODE", "health", 0x00FF, 0x0000),
            ],
        )
        self.assertEqual(
            (commit["consumer"], commit["policy"], commit["duration_frames"]),
            ("B03_COMMIT", "ALL", 0),
        )
        self.assertEqual(
            [
                (
                    edge["provider"],
                    edge["field"],
                    edge["mask"],
                    edge["value"],
                )
                for edge in commit["edges"]
            ],
            [("B03_DOOR", "state", 0x0002, 0x0002)],
        )
        objects = {
            item["authored_logical_id"]: item for item in b03["objects"]
        }
        self.assertEqual(objects["B03_NODE"]["object_state"], 0x0300)
        self.assertEqual(
            objects["B03_SWITCH_B"]["semantic_record"]["duration_frames"],
            180,
        )
        self.assertEqual(
            objects["B03_DOOR"]["semantic_record"]["duration_frames"],
            150,
        )

    def test_v3_big_endian_record_decoders_and_reserved_fields_fail_closed(
        self,
    ) -> None:
        b03 = self.report_by_id["B03"]
        objects = {
            item["authored_logical_id"]: item for item in b03["objects"]
        }
        node = decode_semantic_record(
            bytes.fromhex(
                objects["B03_NODE"]["semantic_record"]["encoded_hex"]
            )
        )
        self.assertEqual(node["object_index"], 2)
        self.assertEqual(node["initial_state"], 0x0300)
        self.assertEqual(node["object_param"], 0x0B01)
        self.assertEqual(node["parameter_count"], 2)

        group = decode_dependency_group_record(
            bytes.fromhex(b03["dependency_groups"][1]["encoded_hex"])
        )
        self.assertEqual(
            (
                group["consumer_index"],
                group["policy_code"],
                group["first_edge"],
                group["edge_count"],
                group["duration_frames"],
            ),
            (3, 1, 1, 2, 150),
        )
        health_edge = decode_dependency_edge_record(
            bytes.fromhex(
                b03["dependency_groups"][1]["edges"][1]["encoded_hex"]
            )
        )
        self.assertEqual(
            (
                health_edge["provider_index"],
                health_edge["field_id"],
                health_edge["mask"],
                health_edge["value"],
            ),
            (2, 4, 0x00FF, 0),
        )

        profile_payload = struct.pack(
            ">BBBBHHHHBBBBIHHHBBHHIIIIHHH10s",
            2,
            1,
            0,
            2,
            103,
            80,
            288,
            112,
            5,
            3,
            0,
            0,
            0x00123456,
            6,
            2,
            3,
            4,
            1,
            5,
            5,
            0x1000,
            0x1100,
            0x1200,
            0x1300,
            58,
            4,
            1,
            bytes(10),
        )
        profile = decode_profile_record(profile_payload)
        self.assertEqual(profile["completion_object"], 4)
        self.assertEqual(profile["completion_mask"], 5)
        self.assertEqual(profile["semantic_count"], 6)
        self.assertEqual(profile["dependency_edge_count"], 3)

        with self.assertRaisesRegex(RuntimeCompileError, "expected 16"):
            decode_semantic_record(bytes(15))
        bad_group = bytearray(bytes.fromhex(
            b03["dependency_groups"][1]["encoded_hex"]
        ))
        bad_group[5] = 1
        with self.assertRaisesRegex(RuntimeCompileError, "reserved"):
            decode_dependency_group_record(bytes(bad_group))
        bad_profile = bytearray(profile_payload)
        bad_profile[-1] = 1
        with self.assertRaisesRegex(RuntimeCompileError, "reserved"):
            decode_profile_record(bytes(bad_profile))

    def test_link_state_and_radius_fail_closed(self) -> None:
        encounters = {
            encounter["id"]: encounter
            for encounter in self.validated.encounters
        }

        unresolved = copy.deepcopy(encounters["B01"])
        unresolved["entities"]["devices"][0]["parameters"]["controls"] = [
            "B01_MISSING_DEVICE"
        ]
        with self.assertRaisesRegex(RuntimeCompileError, "unresolved"):
            _flatten_entities(unresolved)

        duplicate = copy.deepcopy(encounters["B01"])
        duplicate["entities"]["devices"][1]["logical_id"] = "B01_PLATE_A"
        with self.assertRaisesRegex(RuntimeCompileError, "ambiguous entity logical IDs"):
            _flatten_entities(duplicate)

        ambiguous_alias = copy.deepcopy(encounters["B01"])
        ambiguous_alias["entities"]["devices"][0]["parameters"]["controls"] = [
            "B01_DOOR_A_ARM"
        ]
        ambiguous_alias["entities"]["devices"][2]["logical_id"] = "B01_DOOR"
        with self.assertRaisesRegex(RuntimeCompileError, "unresolved"):
            _flatten_entities(ambiguous_alias)

        bad_radius = copy.deepcopy(encounters["B02"])
        bad_radius["entities"]["hazards"][0]["parameters"]["width"] = 600
        with self.assertRaisesRegex(RuntimeCompileError, "does not fit one byte"):
            _flatten_entities(bad_radius)

        unknown_state = copy.deepcopy(encounters["B01"])
        unknown_state["entities"]["devices"][0]["state"]["states"].append(
            "future_state"
        )
        with self.assertRaisesRegex(RuntimeCompileError, "unmapped named states"):
            _flatten_entities(unknown_state)

        unresolved_provider = copy.deepcopy(encounters["B03"])
        unresolved_provider["dependency_policy"][0]["conditions"][0][
            "provider"
        ] = "B03_MISSING"
        with self.assertRaisesRegex(RuntimeCompileError, "provider.*unresolved"):
            _flatten_entities(unresolved_provider)

        duplicate_consumer = copy.deepcopy(encounters["B03"])
        duplicate_consumer["dependency_policy"][1]["consumer"] = "B03_NODE"
        with self.assertRaisesRegex(RuntimeCompileError, "multiple groups"):
            _flatten_entities(duplicate_consumer)

    def test_every_geometry_and_pool_count_fits_frozen_engine_caps(self) -> None:
        for encounter in self.report["encounters"]:
            with self.subTest(encounter=encounter["id"]):
                self.assertLessEqual(
                    encounter["rectangles"], STATIC_RECT_CAPACITY
                )
                for pool_type, count in encounter["pool_counts"].items():
                    self.assertLessEqual(
                        count, POOL_CAPACITY_BY_TYPE[int(pool_type)]
                    )
                self.assertEqual(encounter["status"], "pass")

    def test_profiles_preserve_sector_caps_tempos_bosses_and_anchors(self) -> None:
        expected = {
            "B01": (2, 80, 0),
            "B04": (2, 80, 1),
            "V04": (3, 100, 1),
            "S04": (4, 120, 1),
            "M01": (5, 140, 2),
            "M04": (5, 140, 1),
            "I01": (6, 160, 2),
            "I04": (6, 160, 7),
        }
        for encounter_id, (echo_cap, tempo, flags) in expected.items():
            with self.subTest(encounter=encounter_id):
                row = self.report_by_id[encounter_id]
                self.assertEqual(row["echo_cap"], echo_cap)
                self.assertEqual(row["tempo_bpm"], tempo)
                self.assertEqual(row["flags"], flags)

    def test_generation_is_byte_exact_and_stale_check_fails_closed(self) -> None:
        first = build_runtime_artifacts(CONTENT)
        second = build_runtime_artifacts(CONTENT)
        self.assertEqual(first.files, second.files)
        self.assertEqual(
            first.assembly_sha256,
            hashlib.sha256(first.files[OUTPUT_ASSEMBLY]).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            compile_runtime(CONTENT, output)
            checked = check_generated(CONTENT, output)
            self.assertEqual(checked.files, first.files)
            path = output / OUTPUT_ASSEMBLY
            path.write_bytes(path.read_bytes() + b"; drift\n")
            with self.assertRaises(RuntimeCompileError):
                check_generated(CONTENT, output)
            compile_runtime(CONTENT, output)
            (output / "obsolete.inc.asm").write_text(
                "; stale\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeCompileError, "unexpected"):
                check_generated(CONTENT, output)

    def test_merge_extends_identical_runs_and_rejects_bad_room_shape(self) -> None:
        grid = (
            "####",
            "#..#",
            "#..#",
            "####",
        )
        rectangles = merge_collision_rectangles(
            grid,
            cell_pixels=16,
            room_width=64,
            room_height=64,
        )
        self.assertEqual(
            [(r.x, r.y, r.width, r.height) for r in rectangles],
            [
                (0, 0, 64, 16),
                (0, 16, 16, 32),
                (48, 16, 16, 32),
                (0, 48, 64, 16),
            ],
        )
        with self.assertRaises(RuntimeCompileError):
            merge_collision_rectangles(
                grid,
                cell_pixels=8,
                room_width=64,
                room_height=64,
            )

    def test_generated_adapter_assembles_with_frozen_engine_and_content(self) -> None:
        vasm = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(vasm.is_file(), f"missing pinned assembler: {vasm}")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="campaign-runtime-", dir=ROOT / "build"
        ) as temporary:
            output = Path(temporary) / "campaign-runtime.bin"
            command = [
                str(vasm),
                "-Fbin",
                "-m68000",
                "-no-opt",
                "-x",
                "-Isrc",
                "-I.",
                "-o",
                output.relative_to(ROOT).as_posix(),
                "tests/campaign_runtime/runtime_standalone.asm",
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIsNone(WARNING_RE.search(completed.stdout), completed.stdout)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 50 * 1024)
            self.assertLess(output.stat().st_size, 58 * 1024)


if __name__ == "__main__":
    unittest.main()
