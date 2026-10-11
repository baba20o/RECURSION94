from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from tools.audio_compiler.compiler import (
    AUDIO_BANKS_OUTPUT,
    AUDIO_CHECKSUMS_OUTPUT,
    AUDIO_REGISTRY_OUTPUT,
    MAX_SCENE_COUNT,
    AudioValidationError,
    build_bank_binaries,
    build_artifacts,
    check_generated,
    compile_audio,
    load_and_validate,
)
from tools.audio_reference.bank import CompiledBank


ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"
GENERATED = ROOT / "artifacts" / "generated" / "audio"


class AudioCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = build_artifacts(CONTENT)
        cls.registry = json.loads(
            cls.bundle.files[AUDIO_REGISTRY_OUTPUT].decode("utf-8")
        )
        cls.banks = json.loads(cls.bundle.files[AUDIO_BANKS_OUTPUT].decode("utf-8"))

    def copy_content(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "content"
        shutil.copytree(CONTENT, destination)
        score_destination = (
            destination.parent / "assets" / "source" / "music"
        )
        shutil.copytree(
            ROOT / "assets" / "source" / "music",
            score_destination,
        )
        return destination

    def mutate_score(
        self,
        content: Path,
        change: Callable[[dict[str, Any]], None],
    ) -> None:
        path = content.parent / "assets" / "source" / "music" / "audio_score_v2.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def mutate(
        self,
        content: Path,
        relative: str,
        change: Callable[[dict[str, Any]], None],
    ) -> None:
        path = content / relative
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def test_source_projection_has_frozen_hash_and_counts(self) -> None:
        validated = load_and_validate(CONTENT)
        self.assertEqual(
            "a7c5bcfeec1a6a4ed3f7c5328f180953c80371b17191b91c859044ec301b9351",
            validated.audio_source_sha256,
        )
        self.assertEqual(
            "98df24e4ede3994afff41c253db65c78133686b01cba054ca79882f2c2246213",
            validated.score_source_sha256,
        )
        self.assertEqual(18, len(validated.music_ids))
        self.assertEqual(16, len(validated.phase_ids))
        self.assertEqual(20, len(validated.encounters))

    def test_scene_id_allocation_is_exact_and_bounded(self) -> None:
        scenes = self.registry["scenes"]
        by_key = {scene["scene_key"]: scene["scene_id"] for scene in scenes}
        expected = {
            "NONE": 0,
            "MUS_BOOT_PULSE": 1,
            "MUS_BOOT_WATCHDOG": 2,
            "MUS_VIDEO_MOTION": 3,
            "MUS_VIDEO_RELAY": 4,
            "MUS_VIDEO_PIPELINE": 5,
            "MUS_VIDEO_SCHEDULER": 6,
            "MUS_SOUND_NOISE_FLOOR": 7,
            "MUS_SOUND_PHASE_LOCK": 8,
            "MUS_SOUND_COUNTERPOINT": 9,
            "MUS_SOUND_COLLECTOR": 10,
            "MUS_MEMORY_WRITE": 11,
            "MUS_MEMORY_BAD_SECTOR": 12,
            "MUS_MEMORY_COMPACTION": 13,
            "MUS_MEMORY_FORK": 14,
            "MUS_INTERRUPT_PRIORITY": 15,
            "MUS_INTERRUPT_NON_MASKABLE": 16,
            "MUS_INTERRUPT_VECTOR_TABLE": 17,
            "MUS_INTERRUPT_ORACLE": 18,
            "B04_PHASE_SCAN_CALIBRATION": 19,
            "B04_PHASE_COUNTER_ROTATION": 20,
            "B04_PHASE_SHUTDOWN_WINDOW": 21,
            "V04_PHASE_STATIC_ALLOCATION": 22,
            "V04_PHASE_ROTATED_WINDOWS": 23,
            "V04_PHASE_GLOBAL_COMMIT": 24,
            "S04_PHASE_OLDEST_MARK": 25,
            "S04_PHASE_COMPACTION": 26,
            "S04_PHASE_CHECKSUM": 27,
            "M04_PHASE_PREVIEW": 28,
            "M04_PHASE_FORK_EXECUTION": 29,
            "M04_PHASE_TERMINATION": 30,
            "I04_PHASE_PROFILE": 31,
            "I04_PHASE_INFRASTRUCTURE": 32,
            "I04_PHASE_CONTAINMENT": 33,
            "I04_PHASE_TRANSMISSION": 34,
            "RESULT_STABLE": 35,
            "RESULT_OPTIMIZED": 36,
            "RESULT_TRANSCENDENT": 37,
            "PROGRAM_STABLE": 38,
            "SEED_TRANSMITTED": 39,
        }
        self.assertEqual(expected, by_key)
        self.assertEqual(40, len(scenes))
        self.assertLess(len(scenes), MAX_SCENE_COUNT)
        self.assertEqual(list(range(40)), [scene["scene_id"] for scene in scenes])

    def test_every_encounter_maps_to_one_track_and_declared_boss_phases(self) -> None:
        encounters = self.registry["encounters"]
        self.assertEqual(20, len(encounters))
        self.assertEqual(list(range(1, 21)), [item["order"] for item in encounters])
        by_id = {item["encounter_id"]: item for item in encounters}
        self.assertEqual(1, by_id["B01"]["scene_id"])
        self.assertEqual(1, by_id["B02"]["scene_id"])
        self.assertEqual(1, by_id["B03"]["scene_id"])
        self.assertEqual([19, 20, 21], by_id["B04"]["boss_phase_scene_ids"])
        self.assertEqual([22, 23, 24], by_id["V04"]["boss_phase_scene_ids"])
        self.assertEqual([25, 26, 27], by_id["S04"]["boss_phase_scene_ids"])
        self.assertEqual([28, 29, 30], by_id["M04"]["boss_phase_scene_ids"])
        self.assertEqual([31, 32, 33, 34], by_id["I04"]["boss_phase_scene_ids"])
        for encounter in encounters:
            if encounter["kind"] == "primary":
                self.assertEqual([], encounter["boss_phase_scene_ids"])

    def test_sector_tempo_caps_and_masks_are_frozen(self) -> None:
        expected = [
            ("BOOT", 80, 2, 0x07),
            ("VIDEO", 100, 3, 0x0F),
            ("SOUND", 120, 4, 0x1F),
            ("MEMORY", 140, 5, 0x3F),
            ("INTERRUPT", 160, 6, 0x7F),
        ]
        actual = [
            (
                item["sector"],
                item["tempo_bpm"],
                item["echo_cap"],
                item["layer_cap_mask"],
            )
            for item in self.registry["sector_contracts"]
        ]
        self.assertEqual(expected, actual)

    def test_registry_counts_and_wire_limits_match_bounded_contract(self) -> None:
        self.assertEqual(
            {
                "boss_phase_scenes": 16,
                "encounter_track_scenes": 18,
                "final_scenes": 2,
                "scenes_including_none": 40,
                "stage_result_scenes": 3,
            },
            self.registry["counts"],
        )
        limits = self.registry["limits"]
        self.assertEqual(24, limits["ppqn"])
        self.assertEqual(16, limits["ordinary_ring_entries"])
        self.assertEqual(4, limits["ordinary_service_per_vint"])
        self.assertEqual(3, limits["ordinary_commands_per_display_frame"])
        self.assertEqual(8, limits["sfx_inputs_per_display_frame"])
        self.assertEqual(2, limits["sfx_commands_per_display_frame"])
        wire = self.registry["wire_contract"]
        self.assertTrue(wire["non_cumulative_layer_masks_allowed"])
        self.assertFalse(wire["gameplay_may_read_z80_timing"])
        self.assertFalse(wire["mute_or_bank_failure_may_block_progression"])

    def test_scene_quantization_and_result_tempo_fallback_are_explicit(self) -> None:
        by_key = {
            scene["scene_key"]: scene for scene in self.registry["scenes"]
        }
        self.assertEqual(96, by_key["MUS_SOUND_COLLECTOR"]["scene_quantum_ticks"])
        self.assertEqual(6, by_key["S04_PHASE_COMPACTION"]["scene_quantum_ticks"])
        for scene_key in (
            "RESULT_STABLE",
            "RESULT_OPTIMIZED",
            "RESULT_TRANSCENDENT",
        ):
            scene = by_key[scene_key]
            self.assertEqual("inherit_previous", scene["tempo_policy"])
            self.assertIsNone(scene["tempo_bpm"])
            self.assertEqual(120, scene["tempo_fallback_bpm"])
        self.assertEqual(160, by_key["PROGRAM_STABLE"]["tempo_fallback_bpm"])
        self.assertEqual(0, by_key["RESULT_STABLE"]["pattern_start_row"])
        self.assertEqual(5, by_key["RESULT_OPTIMIZED"]["pattern_start_row"])
        self.assertEqual(10, by_key["RESULT_TRANSCENDENT"]["pattern_start_row"])
        self.assertEqual(0, by_key["PROGRAM_STABLE"]["pattern_start_row"])
        self.assertEqual(8, by_key["SEED_TRANSMITTED"]["pattern_start_row"])

    def test_voice_contract_covers_seven_layers_without_collision(self) -> None:
        voices = self.registry["voice_contracts"]
        self.assertEqual(list(range(7)), [voice["layer_bit"] for voice in voices])
        self.assertEqual(7, len({voice["voice"] for voice in voices}))
        self.assertTrue(all(voice["finite_note_max_ticks"] == 384 for voice in voices))

    def test_bank_manifest_is_honest_and_bounded(self) -> None:
        self.assertEqual(
            {
                "encounter_music_banks": 18,
                "system_banks": 2,
                "total_banks": 20,
            },
            self.banks["counts"],
        )
        self.assertTrue(self.banks["status"]["score_payloads_composed"])
        self.assertTrue(self.banks["status"]["score_payloads_validated"])
        self.assertFalse(
            self.banks["status"]["compiled_binaries_emitted_to_runtime_tree"]
        )
        self.assertFalse(self.banks["status"]["runtime_integrated"])
        for bank in self.banks["banks"]:
            self.assertEqual("composed_validated", bank["payload_status"])
            self.assertEqual(112, bank["payload_bytes"])
            self.assertEqual(16, bank["descriptor_bytes"])
            self.assertEqual(2032, bank["payload_max_bytes"])
            self.assertEqual(128, bank["compiled_bytes"])
            self.assertRegex(bank["compiled_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(bank["payload_crc32"], r"^[0-9a-f]{8}$")
            self.assertGreater(bank["note_events"], 0)
            self.assertLessEqual(bank["maximum_row_events"], 7)
            self.assertLessEqual(bank["maximum_row_ym_writes"], 32)
            self.assertLessEqual(bank["maximum_row_psg_writes"], 12)
            self.assertLessEqual(
                bank["max_note_duration_ticks"],
                self.banks["budgets"]["note_duration_ticks_max"],
            )

    def test_all_twenty_compiled_banks_are_exact_unique_and_decodable(self) -> None:
        binaries = build_bank_binaries(CONTENT)
        self.assertEqual(
            [f"audio_bank_{bank_id:02d}.bin" for bank_id in range(1, 21)],
            sorted(binaries),
        )
        self.assertEqual(20, len({hashlib.sha256(value).digest() for value in binaries.values()}))
        decoded = [CompiledBank.parse(binaries[name]) for name in sorted(binaries)]
        self.assertEqual(list(range(1, 21)), [bank.bank_id for bank in decoded])
        self.assertEqual(
            [80, 80, *([100] * 4), *([120] * 4), *([140] * 4), *([160] * 4), 120, 160],
            [bank.tempo_bpm for bank in decoded],
        )
        for bank in decoded:
            traces = bank.trace_pattern()
            self.assertEqual(16, len(traces))
            self.assertLessEqual(max(row.ym_writes for row in traces), 32)
            self.assertLessEqual(
                max(row.ym_writes for row in traces) + (2 * 4),
                32,
            )
            self.assertLessEqual(max(row.psg_writes for row in traces), 12)

    def test_z80_layout_is_exact_contiguous_8k_partition(self) -> None:
        regions = self.banks["z80_layout"]["regions"]
        self.assertEqual(0, regions[0]["start"])
        self.assertEqual(0x1FFF, regions[-1]["end"])
        self.assertEqual(
            list(range(0, 0x2000)),
            [
                address
                for region in regions
                for address in range(region["start"], region["end"] + 1)
            ],
        )
        by_name = {region["name"]: region for region in regions}
        self.assertEqual(2048, by_name["active_music_bank"]["bytes"])
        self.assertEqual(64, by_name["command_ring"]["bytes"])
        self.assertEqual(256, by_name["stack_reserve"]["bytes"])

    def test_sfx_ids_priorities_and_merge_policies_are_frozen(self) -> None:
        cues = self.registry["sfx_contract"]["cues"]
        self.assertEqual(list(range(16)), [cue["sfx_id"] for cue in cues])
        self.assertEqual(
            [
                "NONE",
                "SHOT",
                "DASH_PHASE",
                "INTERACT",
                "DOOR",
                "SWITCH",
                "PICKUP_DROP",
                "DAMAGE",
                "DEATH",
                "RESET",
                "ECHO_MATERIALIZE",
                "ANCHOR",
                "ALERT",
                "BOSS_PHASE",
                "PROGRAM_STABLE",
                "SEED_TRANSMITTED",
            ],
            [cue["sfx_key"] for cue in cues],
        )
        self.assertEqual([0, *([1] * 6), *([2] * 6), *([3] * 3)], [
            cue["priority"] for cue in cues
        ])
        self.assertEqual("ym_ch5", self.registry["sfx_contract"]["voice"])

    def test_bank_descriptor_and_payload_exactly_fill_active_region(self) -> None:
        budgets = self.banks["budgets"]
        self.assertEqual(
            budgets["active_music_bank_max_bytes"],
            budgets["bank_descriptor_bytes"]
            + budgets["music_payload_max_bytes"],
        )
        self.assertEqual(16, self.banks["bank_load_contract"]["descriptor_bytes"])
        self.assertIn("CRC-32", self.banks["bank_load_contract"]["checksum"])

    def test_music_id_change_fails_closed(self) -> None:
        content = self.copy_content()
        self.mutate(
            content,
            "encounters/boot/B01.json",
            lambda value: value["presentation"].__setitem__(
                "music_id", "MUS_BOOT_DIFFERENT"
            ),
        )
        with self.assertRaisesRegex(AudioValidationError, "MUS_BOOT_PULSE"):
            build_artifacts(content)

    def test_sector_tempo_change_fails_closed(self) -> None:
        content = self.copy_content()
        self.mutate(
            content,
            "campaign.json",
            lambda value: value["sectors"][2].__setitem__("tempo_bpm", 121),
        )
        with self.assertRaises(AudioValidationError):
            build_artifacts(content)

    def test_sector_echo_cap_change_fails_closed(self) -> None:
        content = self.copy_content()
        self.mutate(
            content,
            "campaign.json",
            lambda value: value["sectors"][4].__setitem__("echo_cap", 7),
        )
        with self.assertRaises(AudioValidationError):
            build_artifacts(content)

    def test_boss_phase_count_change_fails_closed(self) -> None:
        content = self.copy_content()
        self.mutate(
            content,
            "encounters/interrupt/I04.json",
            lambda value: value["boss_phases"].pop(),
        )
        with self.assertRaises(AudioValidationError):
            build_artifacts(content)

    def test_invalid_tracker_character_fails_closed(self) -> None:
        content = self.copy_content()

        def change(value: dict[str, Any]) -> None:
            value["banks"][0]["layers"]["base"][0] = "1X34"

        self.mutate_score(content, change)
        with self.assertRaisesRegex(AudioValidationError, "invalid note"):
            build_artifacts(content)

    def test_note_above_sector_echo_cap_fails_closed(self) -> None:
        content = self.copy_content()

        def change(value: dict[str, Any]) -> None:
            value["banks"][0]["layers"]["harmony"][0] = "1..."

        self.mutate_score(content, change)
        with self.assertRaisesRegex(AudioValidationError, "exceeds echo cap"):
            build_artifacts(content)

    def test_duplicate_score_payload_fails_closed(self) -> None:
        content = self.copy_content()

        def change(value: dict[str, Any]) -> None:
            value["banks"][1]["layers"] = value["banks"][0]["layers"]

        self.mutate_score(content, change)
        with self.assertRaisesRegex(AudioValidationError, "distinct"):
            build_artifacts(content)

    def test_source_object_key_order_does_not_change_outputs(self) -> None:
        content = self.copy_content()

        def reverse(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: reverse(child)
                    for key, child in reversed(tuple(value.items()))
                }
            if isinstance(value, list):
                return [reverse(child) for child in value]
            return value

        for relative in ("campaign.json", "encounters/interrupt/I04.json"):
            path = content / relative
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(
                json.dumps(reverse(value), ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        score = content.parent / "assets" / "source" / "music" / "audio_score_v2.json"
        score_value = json.loads(score.read_text(encoding="utf-8"))
        score.write_text(
            json.dumps(reverse(score_value), ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        self.assertEqual(self.bundle.files, build_artifacts(content).files)
        self.assertEqual(
            self.bundle.bank_binaries,
            build_artifacts(content).bank_binaries,
        )

    def test_generation_and_check_are_byte_identical(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "audio"
        first = compile_audio(CONTENT, output)
        first_files = {name: (output / name).read_bytes() for name in first.files}
        second = compile_audio(CONTENT, output)
        second_files = {name: (output / name).read_bytes() for name in second.files}
        self.assertEqual(first_files, second_files)
        checked = check_generated(CONTENT, output)
        self.assertEqual(first.registry_sha256, checked.registry_sha256)

    def test_check_rejects_corruption(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "audio"
        compile_audio(CONTENT, output)
        with (output / AUDIO_REGISTRY_OUTPUT).open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(AudioValidationError, "stale or corrupted"):
            check_generated(CONTENT, output)

    def test_checksum_file_names_and_hashes_both_json_outputs(self) -> None:
        checksums = self.bundle.files[AUDIO_CHECKSUMS_OUTPUT].decode("ascii").splitlines()
        self.assertEqual(2, len(checksums))
        for line, name in zip(
            checksums,
            (AUDIO_REGISTRY_OUTPUT, AUDIO_BANKS_OUTPUT),
            strict=True,
        ):
            digest, listed_name = line.split("  ")
            self.assertEqual(name, listed_name)
            self.assertEqual(
                hashlib.sha256(self.bundle.files[name]).hexdigest(),
                digest,
            )

    def test_checked_in_generated_metadata_is_current(self) -> None:
        checked = check_generated(CONTENT, GENERATED)
        self.assertEqual(self.bundle.registry_sha256, checked.registry_sha256)
        self.assertEqual(self.bundle.banks_sha256, checked.banks_sha256)


if __name__ == "__main__":
    unittest.main()
