from __future__ import annotations

import hashlib
import binascii
import json
import random
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.m5_reference import model as reference_model
from tools.m5_reference.contracts import load_constants
from tools.m5_reference.manifest import (
    ADAPTER_FORMAT_VERSION,
    DEVICE_PRESSURE,
    DEVICE_STATE_ACTIVE,
    DEVICE_DIRECTION_UP,
    ENTITY_VOCABULARY_MANIFEST,
    OWNER_FUTURE_BOSS,
    PARAM_DEVICE_LINK,
    PARAM_HAZARD_RADIUS,
    REFERENCE_REGISTRY,
    SEMANTIC_TOKEN_REGISTRY,
    manifest_sha256,
    validate_manifest,
)
from tools.campaign_runtime.compiler import (
    FORMAT_VERSION as CAMPAIGN_RUNTIME_FORMAT_VERSION,
    OUTPUT_REPORT as CAMPAIGN_RUNTIME_REPORT,
    build_runtime_artifacts,
)


ROOT = Path(__file__).resolve().parents[2]
VOCAB_SOURCES = tuple(sorted((ROOT / "src/gameplay").glob("vocabulary*.asm")))
WARNING_RE = re.compile(r"(?i)(?:^|\s)warning(?:\s+\w+)?\s*(?::|\[)")


class VocabularyLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.c = load_constants()

    def test_extension_uses_only_the_proposed_frozen_gap(self) -> None:
        c = self.c
        self.assertEqual(c["VOCAB_STATE_BASE"], 0xFF1600)
        self.assertEqual(c["VOCAB_STATE_END"], 0xFF1800)
        self.assertEqual(c["VOCAB_STATE_BYTES_VALUE"], 0x200)
        self.assertEqual(
            c["VOCAB_STATE_BASE"],
            c["GAMEPLAY_CONTROL_BASE"] + c["GAMEPLAY_CONTROL_BYTES_VALUE"],
        )
        self.assertEqual(c["VOCAB_STATE_END"], c["ENGINE_ACTOR_BASE"])
        self.assertLessEqual(c["VOCAB_BEAM_END"], c["VOCAB_STATE_END"])

    def test_projection_tail_and_update_bounds_are_conservative(self) -> None:
        c = self.c
        self.assertEqual(c["VOCAB_PROJECTION_BYTES"], 256)
        self.assertLessEqual(c["VOCAB_PROJECTION_BYTES"], 304)
        self.assertLessEqual(
            c["VOCAB_PROJECTION_BYTES"], c["VOCAB_PROJECTION_LIMIT"]
        )
        self.assertLessEqual(
            c["ENGINE_PROJECTION_SCHEMA_BYTES"]
            + c["VOCAB_PROJECTION_BYTES"],
            0x1000,
        )
        self.assertEqual(c["VOCAB_ANCHOR_LIMIT"], 2)
        self.assertEqual(c["VOCAB_BEAM_LIMIT"], 8)
        self.assertLessEqual(
            c["VOCAB_CPU_WORST_UNITS"], c["VOCAB_CPU_UNIT_LIMIT"]
        )
        self.assertEqual(c["VOCAB_CPU_WORST_UNITS"], 2904)

    def test_historical_m4_rom_archive_is_intact(self) -> None:
        expected_roms = {
            "artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5/"
            "recursion94-debug.bin": (
                "c689adf93f646c34809649d103cc7759"
                "d4b5adf19f5315b3ab99bafe883d2460"
            ),
            "artifacts/archive/m4-frozen-roms-c689adf9-0cf94ab5/"
            "recursion94-release.bin": (
                "0cf94ab5a303bc077810f3d1fe2d4157"
                "070ffe6ca515ba0ae5568d3cb90f9d43"
            ),
        }
        for relative, expected in expected_roms.items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)


class VocabularySourceTests(unittest.TestCase):
    def test_translation_unit_wires_campaign_vocabulary_in_dependency_order(self) -> None:
        source = (ROOT / "src/main.asm").read_text(encoding="utf-8")
        includes = re.findall(r'include\s+"([^"]+)"', source)
        ordered = [
            "game/engine.asm",
            "gameplay/gameplay.asm",
            "gameplay/vocabulary.asm",
            "gameplay/campaign_runtime.asm",
            "gameplay/campaign_collision.asm",
            "gameplay/campaign_projection.asm",
            "render/gameplay_renderer.asm",
            "ui/timeline_control.asm",
            "../assets/generated/content/campaign_content.inc.asm",
            "../assets/generated/campaign_runtime/campaign_runtime.inc.asm",
        ]
        positions = [includes.index(path) for path in ordered]
        self.assertEqual(positions, sorted(positions))

        unit = (ROOT / "src/gameplay/vocabulary.asm").read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r'include\s+"([^"]+)"', unit),
            [
                "gameplay/vocabulary_layout.asm",
                "gameplay/vocabulary_interactions.asm",
                "gameplay/vocabulary_combat.asm",
                "gameplay/vocabulary_core.asm",
            ],
        )

    def test_public_primitives_cover_the_requested_vocabulary(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in VOCAB_SOURCES)
        labels = (
            "VocabularyInitialize",
            "VocabularyResetOrdinary",
            "VocabularyApplyDashPhase",
            "VocabularyResolveInteraction",
            "VocabularyUpdateProjectilesAndDamage",
            "VocabularyUpdateDevices",
            "VocabularyBuildBeamCache",
            "VocabularyUpdateEnemies",
            "VocabularyUpdateActorsAndHazards",
            "VocabularyStep",
            "VocabularySetAnchor",
            "VocabularyApplyAnchors",
            "VocabularyRequestEchoReplacement",
            "VocabularyFinalizeStage",
            "VocabularyBuildProjectionTail",
        )
        for label in labels:
            self.assertEqual(source.count(f"\n{label}:"), 1, label)
        for token in (
            "VOCAB_DEVICE_PRESSURE_SWITCH",
            "VOCAB_DEVICE_TIMED_SWITCH",
            "VOCAB_DEVICE_DOOR",
            "VOCAB_DEVICE_SOCKET",
            "VOCAB_DEVICE_CONVEYOR",
            "VOCAB_DEVICE_BEAM_EMITTER",
            "VOCAB_DEVICE_REFLECTOR",
            "VOCAB_DEVICE_HACK_TERMINAL",
            "VOCAB_DEVICE_REPAIR_TERMINAL",
            "VOCAB_ENEMY_TURRET",
            "VOCAB_ENEMY_PURSUER",
            "VOCAB_ENEMY_GUARD",
            "VOCAB_HAZARD_DAMAGE_FIELD",
            "VOCAB_HAZARD_SHIELD_FIELD",
        ):
            self.assertIn(token, source)

    def test_every_runtime_loop_has_a_static_capacity_source(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in VOCAB_SOURCES)
        for bound in (
            "ACTOR_CAPACITY-1",
            "ENEMY_CAPACITY-1",
            "DEVICE_CAPACITY-1",
            "CARRYABLE_CAPACITY-1",
            "VOCAB_ANCHOR_LIMIT",
            "VOCAB_BEAM_LIMIT",
            "VOCAB_BEAM_EMITTER_LIMIT",
        ):
            self.assertIn(bound, source)
        for pool, capacity, register, ready_label in (
            ("PROJECTILE", "PROJECTILE_CAPACITY", "d7", ".projectile_bound_ready"),
            ("ENEMY", "ENEMY_CAPACITY", "d7", ".enemy_bound_ready"),
            ("DEVICE", "DEVICE_CAPACITY", "d7", ".device_bound_ready"),
            ("HAZARD", "HAZARD_CAPACITY", "d6", ".hazard_bound_ready"),
        ):
            high_water_bound = "\n".join(
                (
                    f"        moveq   #0,{register}",
                    f"        move.b  (ENGINE_POOL_HIGH_WATER+POOL_TYPE_{pool}-1).l,{register}",
                    f"        cmpi.w  #{capacity},{register}",
                    f"        bls.s   {ready_label}",
                    f"        moveq   #{capacity},{register}",
                    f"{ready_label}:",
                )
            )
            self.assertIn(high_water_bound, source)
        self.assertNotIn("malloc", source.lower())
        self.assertNotIn("VDP_", source)
        self.assertNotIn("Z80_", source)

    def test_projection_tail_is_fixed_copy_not_hidden_world_state(self) -> None:
        core = (ROOT / "src/gameplay/vocabulary_core.asm").read_text(
            encoding="utf-8"
        )
        routine = core.split("VocabularyBuildProjectionTail:", 1)[1]
        self.assertIn(
            "        rept    VOCAB_PROJECTION_BYTES/4\n"
            "        move.l  (a1)+,(a0)+\n"
            "        endr",
            routine,
        )
        self.assertIn(
            'assert  VOCAB_PROJECTION_BYTES=256,"vocabulary fixed copy size drift"',
            routine,
        )
        self.assertIn("#VOCAB_PROJECTION_BYTES,d0", routine)
        self.assertNotIn("dbf", routine)
        self.assertNotIn("bsr", routine)
        self.assertNotIn("jsr", routine)
        self.assertNotIn("EngineBuildProjection", routine)

    def test_campaign_projection_continues_the_base_crc_without_full_rehash(
        self,
    ) -> None:
        source = (ROOT / "src/gameplay/campaign_projection.asm").read_text(
            encoding="utf-8"
        )
        routine = source.split("GameplayBuildProjection:", 1)[1].split(
            ".invalid:", 1
        )[0]
        self.assertIn("move.l  d1,d5", routine)
        self.assertIn(
            "eori.l  #CAMPAIGN_PROJECTION_HEADER_CRC_XOR,d1",
            routine,
        )
        self.assertIn("move.w  #VOCAB_PROJECTION_BYTES,d0", routine)
        self.assertEqual(routine.count("bsr.w   Crc32UpdateRaw"), 1)
        self.assertNotIn("Crc32IsoHdlc", routine)

        match = re.search(
            r"CAMPAIGN_PROJECTION_HEADER_CRC_XOR\s+equ\s+\$([0-9A-Fa-f]+)",
            source,
        )
        self.assertIsNotNone(match)
        correction = int(match.group(1), 16)
        constants = load_constants()
        base_bytes = constants["ENGINE_PROJECTION_SCHEMA_BYTES"]
        tail_bytes = constants["VOCAB_PROJECTION_BYTES"]
        self.assertEqual(base_bytes, 0x0E36)
        self.assertEqual(tail_bytes, 0x0100)
        old_header = b"R94P\x00\x01\x0E\x36"
        new_header = b"R94P\x00\x02\x0F\x36"

        zero_payload = bytes(base_bytes - len(old_header))
        derived = (
            binascii.crc32(old_header + zero_payload)
            ^ binascii.crc32(new_header + zero_payload)
        ) & 0xFFFFFFFF
        self.assertEqual(derived, correction)

        generator = random.Random(0x523934)
        for _ in range(16):
            payload = generator.randbytes(base_bytes - len(old_header))
            tail = generator.randbytes(tail_bytes)
            old_crc = binascii.crc32(old_header + payload) & 0xFFFFFFFF
            continued = binascii.crc32(tail, old_crc ^ correction) & 0xFFFFFFFF
            expected = binascii.crc32(new_header + payload + tail) & 0xFFFFFFFF
            self.assertEqual(continued, expected)


class CampaignTranslationContractTests(unittest.TestCase):
    def test_manifest_exhaustively_classifies_current_registry(self) -> None:
        validate_manifest()
        self.assertEqual(len(ENTITY_VOCABULARY_MANIFEST), 83)
        self.assertEqual(len(SEMANTIC_TOKEN_REGISTRY), 58)
        self.assertEqual(len(REFERENCE_REGISTRY), 20)
        self.assertEqual(ADAPTER_FORMAT_VERSION, 3)
        self.assertEqual(CAMPAIGN_RUNTIME_FORMAT_VERSION, 3)
        self.assertEqual(
            ENTITY_VOCABULARY_MANIFEST["pressure_switch"].vocab_kind,
            DEVICE_PRESSURE,
        )
        pressure = ENTITY_VOCABULARY_MANIFEST["pressure_switch"]
        self.assertEqual(pressure.original_type_code, 0xD8AD)
        self.assertEqual(
            pressure.state_map(), {"pressed": DEVICE_STATE_ACTIVE, "released": 0}
        )
        self.assertEqual(pressure.param_semantic, PARAM_DEVICE_LINK)
        reflector = ENTITY_VOCABULARY_MANIFEST["beam_reflector"]
        self.assertEqual(reflector.state_map()["north"], DEVICE_DIRECTION_UP)
        hazard = ENTITY_VOCABULARY_MANIFEST["scheduled_raster_hazard"]
        self.assertEqual(hazard.param_semantic, PARAM_HAZARD_RADIUS)

        non_semantic = {
            "ACTOR_CAPACITY",
            "ENEMY_CAPACITY",
            "PROJECTILE_CAPACITY",
            "DEVICE_CAPACITY",
            "CARRYABLE_CAPACITY",
            "HAZARD_CAPACITY",
            "BEAM_LIMIT",
            "ANCHOR_LIMIT",
            "ECHO_LIMIT",
            "ATTEMPT_TICKS",
            "UPDATE_UNIT_LIMIT",
            "PROJECTION_BYTES",
            "INPUT_UP",
            "INPUT_DOWN",
            "INPUT_LEFT",
            "INPUT_RIGHT",
            "INPUT_A",
            "INPUT_B",
            "INPUT_C",
        }
        model_tokens = {
            name: value
            for name, value in vars(reference_model).items()
            if name.isupper()
            and isinstance(value, int)
            and name not in non_semantic
        }
        self.assertEqual(len(model_tokens), 58)
        self.assertEqual(set(SEMANTIC_TOKEN_REGISTRY), set(model_tokens))
        self.assertEqual(
            {
                name: token.value
                for name, token in SEMANTIC_TOKEN_REGISTRY.items()
            },
            model_tokens,
        )

    def test_v3_compiler_emits_vocab_words_and_semantic_metadata(self) -> None:
        artifacts = build_runtime_artifacts(ROOT / "content")
        report = json.loads(
            artifacts.files[CAMPAIGN_RUNTIME_REPORT].decode("utf-8")
        )
        self.assertEqual(report["adapter_contract_sha256"], manifest_sha256())
        objects = {
            item["authored_logical_id"]: item
            for encounter in report["encounters"]
            for item in encounter["objects"]
        }
        pressure = objects["B01_PLATE_A"]
        door = objects["B01_DOOR_A"]
        self.assertEqual(pressure["object_param"], DEVICE_PRESSURE)
        self.assertEqual(pressure["dependency_group"], 0)
        self.assertEqual(door["dependency_group"], 1)
        self.assertEqual(door["param_low"], 1)
        self.assertEqual(pressure["object_timer"], 0)
        self.assertEqual(
            pressure["metadata"]["original_type_code"],
            ENTITY_VOCABULARY_MANIFEST["pressure_switch"].original_type_code,
        )
        self.assertNotEqual(
            pressure["object_param"], pressure["metadata"]["original_type_code"]
        )

    def test_deferred_boss_kind_zero_has_explicit_future_owner(self) -> None:
        artifacts = build_runtime_artifacts(ROOT / "content")
        report = json.loads(
            artifacts.files[CAMPAIGN_RUNTIME_REPORT].decode("utf-8")
        )
        bosses = [
            item
            for encounter in report["encounters"]
            for item in encounter["objects"]
            if item["category"] == "boss_components"
        ]
        self.assertGreater(len(bosses), 10)
        for item in bosses:
            self.assertEqual(item["vocab_kind"], 0)
            self.assertEqual(item["object_state"], 0)
            self.assertEqual(item["metadata"]["owner"], OWNER_FUTURE_BOSS)
            self.assertEqual(item["metadata"]["owner_code"], 2)


class VocabularyAssemblyTests(unittest.TestCase):
    def test_standalone_vocabulary_assembles_without_warning(self) -> None:
        vasm = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        self.assertTrue(vasm.is_file(), f"missing pinned assembler: {vasm}")
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="m5-vocabulary-", dir=ROOT / "build") as temp:
            output = Path(temp) / "vocabulary.bin"
            command = [
                str(vasm),
                "-Fbin",
                "-m68000",
                "-no-opt",
                "-x",
                "-Isrc",
                "-o",
                output.relative_to(ROOT).as_posix(),
                "tests/m5/vocabulary_standalone.asm",
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
            self.assertGreater(output.stat().st_size, 1024)
            self.assertLess(output.stat().st_size, 32768)


if __name__ == "__main__":
    unittest.main()
