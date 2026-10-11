from __future__ import annotations

import re
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.save_reference import model as save_model


ROOT = Path(__file__).resolve().parents[2]
WARNING_RE = re.compile(r"\bwarning\b", re.IGNORECASE)


class SaveStandaloneAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        vasm = ROOT / ".toolcache/bin/vasmm68k_mot.exe"
        if not vasm.is_file():
            raise AssertionError(f"missing pinned assembler: {vasm}")
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="save-contract-", dir=ROOT / "build"
        )
        output = Path(cls.temporary.name) / "save-standalone.bin"
        command = [
            str(vasm),
            "-Fbin",
            "-m68000",
            "-no-opt",
            "-x",
            "-Isrc",
            "-o",
            output.relative_to(ROOT).as_posix(),
            "tests/save/save_standalone.asm",
        ]
        cls.completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        cls.output_path = output

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_standalone_vasm_is_zero_warning_and_link_complete(self) -> None:
        self.assertEqual(
            self.completed.returncode, 0, self.completed.stdout
        )
        self.assertIsNone(
            WARNING_RE.search(self.completed.stdout), self.completed.stdout
        )
        self.assertTrue(self.output_path.is_file())
        payload = self.output_path.read_bytes()
        crc_tables = (
            ROOT / "assets/generated/crc32/crc32_tables.bin"
        ).read_bytes()
        self.assertEqual(payload.count(crc_tables), 1)
        code_and_abi_bytes = len(payload) - len(crc_tables)
        self.assertGreater(code_and_abi_bytes, 1800)
        self.assertLess(code_and_abi_bytes, 2200)

    def test_assembled_tail_freezes_the_exact_save_abi(self) -> None:
        self.assertEqual(
            self.completed.returncode, 0, self.completed.stdout
        )
        payload = self.output_path.read_bytes()
        marker = b"SABI"
        expected = (
            0x00200001,
            0x00200201,
            2,
            256,
            32,
            8,
            216,
            0x00,
            0x04,
            0x06,
            0x08,
            0x0C,
            0x0E,
            0x10,
            0x20,
            0xF8,
            0xFC,
            0xF8,
            0x1F8,
            0x52393453,
            1,
            1,
            0xA55A94C3,
            0x20,
            0x22,
            0x23,
            0x24,
            0x28,
            0x2C,
            0x2E,
            0x30,
            0xD0,
            20,
            0,
            1,
            2,
            4,
            5,
            6,
            8,
            7,
            7,
            0,
            1,
            2,
            3,
            0x00FFA000,
            0x00FFA100,
            0x00FFA200,
            0x00FFA208,
            0x00FFA20A,
            0x00FFA20C,
            0x00FFA20D,
            0x00FFA210,
            0x00FFA214,
            0x00FFA218,
            0x00FFA21A,
            0x00FFA21C,
            0x00FFA300,
        )
        abi_bytes = len(marker) + len(expected) * 4
        self.assertEqual(payload.count(marker), 1)
        self.assertEqual(payload[-abi_bytes : -abi_bytes + 4], marker)
        actual = struct.unpack(
            f">{len(expected)}I", payload[-len(expected) * 4 :]
        )
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], save_model.SRAM_SLOT_A)
        self.assertEqual(actual[1], save_model.SRAM_SLOT_B)
        self.assertEqual(actual[2], save_model.SRAM_ADDRESS_STRIDE)
        self.assertEqual(actual[3], save_model.RECORD_BYTES)
        self.assertEqual(actual[4], save_model.HEADER_BYTES)
        self.assertEqual(actual[5], save_model.TRAILER_BYTES)
        self.assertEqual(actual[6], save_model.PAYLOAD_BYTES)
        self.assertEqual(actual[15], save_model.CRC_OFFSET)
        self.assertEqual(actual[16], save_model.COMMIT_OFFSET)
        self.assertEqual(actual[19], int.from_bytes(save_model.MAGIC, "big"))
        self.assertEqual(actual[20], save_model.FORMAT_VERSION)
        self.assertEqual(actual[21], save_model.FLAG_VALID)
        self.assertEqual(actual[22], save_model.COMMIT_MARKER)
        self.assertEqual(actual[32], save_model.ENCOUNTER_COUNT)
        self.assertEqual(actual[39], save_model.STAGE_METRIC_BYTES)
        self.assertEqual(
            actual[40],
            save_model.SETTING_MUSIC
            | save_model.SETTING_SFX
            | save_model.SETTING_RESET_FLASH,
        )
        self.assertEqual(actual[41], save_model.DEFAULT_SETTINGS)
        self.assertEqual(actual[0] + (actual[3] - 1) * actual[2], 0x2001FF)
        self.assertEqual(actual[1] + (actual[3] - 1) * actual[2], 0x2003FF)


class SaveSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = (ROOT / "src/save/save_layout.asm").read_text(
            encoding="utf-8"
        )
        cls.core = (ROOT / "src/save/save_core.asm").read_text(
            encoding="utf-8"
        )

    def test_translation_units_are_standalone_and_runtime_unwired(self) -> None:
        save_unit = (ROOT / "src/save/save.asm").read_text(encoding="utf-8")
        fixture = (ROOT / "tests/save/save_standalone.asm").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            re.findall(r'include\s+"([^"]+)"', save_unit),
            ["save/save_layout.asm", "save/save_core.asm"],
        )
        self.assertEqual(
            re.findall(r'include\s+"([^"]+)"', fixture),
            [
                "game/crc32.asm",
                "save/save.asm",
                "../../assets/generated/crc32/crc32_tables.inc.asm",
            ],
        )
        for path in (ROOT / "src").rglob("*.asm"):
            if path.parent == ROOT / "src/save":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r'include\s+"(?:\./)?save/', text),
                f"{path.relative_to(ROOT)} wires the save unit",
            )
            self.assertIsNone(
                re.search(
                    r"\b(?:bsr|jsr|jmp)(?:\.(?:w|l))?\s+"
                    r"Save(?:Initialize|Load|Commit|MarkStageClear|SetSettings)",
                    text,
                ),
                f"{path.relative_to(ROOT)} calls the unwired save API",
            )

    def test_crc_and_record_bounds_are_exact(self) -> None:
        required = (
            "SAVE_RECORD_BYTES              equ     256",
            "SAVE_HEADER_BYTES              equ     32",
            "SAVE_TRAILER_BYTES             equ     8",
            "SAVE_RECORD_CRC                equ     $F8",
            "SAVE_RECORD_COMMIT             equ     $FC",
            "SAVE_CRC_BYTES                 equ     SAVE_RECORD_CRC",
            "SAVE_ENCOUNTER_COUNT           equ     20",
            "SAVE_METRIC_BYTES              equ     $08",
        )
        for line in required:
            self.assertIn(line, self.layout)
        self.assertEqual(
            self.core.count("move.w  #SAVE_CRC_BYTES,d0"), 2
        )
        self.assertIn(
            "cmp.l   (SAVE_RECORD_CRC,a2),d0", self.core
        )
        self.assertIn(
            "#(SAVE_RECORD_CRC-SAVE_PAYLOAD_USED_END)-1", self.core
        )

    def test_odd_lane_helpers_have_exact_static_transfer_bounds(self) -> None:
        read = self.core[
            self.core.index("SaveReadOddLane:") :
            self.core.index("SaveWriteOddLane:")
        ]
        write = self.core[
            self.core.index("SaveWriteOddLane:") :
            self.core.index("SaveCopyRamRecord:")
        ]
        copy = self.core[self.core.index("SaveCopyRamRecord:") :]
        self.assertIn("move.w  #SAVE_RECORD_BYTES-1,d1", read)
        self.assertIn("move.b  (a0),(a1)+", read)
        self.assertIn("move.w  #SAVE_RECORD_COMMIT-1,d1", write)
        self.assertIn("move.b  (a1)+,(a0)", write)
        self.assertEqual(read.count("addq.l  #2,a0"), 1)
        self.assertEqual(write.count("addq.l  #2,a0"), 1)
        self.assertIn("moveq   #(SAVE_RECORD_BYTES/4)-1,d1", copy)
        self.assertIn("move.l  (a0)+,(a1)+", copy)
        for helper in (read, write, copy):
            self.assertEqual(helper.count("dbf     d1,"), 1)

    def test_commit_physically_invalidates_then_writes_body_then_marker(self) -> None:
        start = self.core.index(".target_ready:")
        invalidate = self.core.index(".invalidate_marker:", start)
        body = self.core.index("bsr.w   SaveWriteOddLane", invalidate)
        publish = self.core.index(".commit_marker:", body)
        verify = self.core.index("bsr.w   SaveReadOddLane", publish)
        self.assertLess(start, invalidate)
        self.assertLess(invalidate, body)
        self.assertLess(body, publish)
        self.assertLess(publish, verify)
        commit_segment = self.core[start:verify]
        self.assertIn(
            "lea     (SAVE_COMMIT_PHYSICAL_OFFSET,a0),a0",
            commit_segment,
        )
        self.assertIn("clr.b   (a0)", commit_segment)
        self.assertEqual(commit_segment.count("moveq   #3,d1"), 2)
        self.assertIn(
            "move.l  #SAVE_COMMIT_MARKER,"
            "(SAVE_ACTIVE_BASE+SAVE_RECORD_COMMIT).l",
            commit_segment,
        )
        helper = self.core[self.core.index("SaveWriteOddLane:") :]
        helper = helper[: helper.index("SaveCopyRamRecord:")]
        self.assertIn(
            "move.w  #SAVE_RECORD_COMMIT-1,d1", helper
        )
        self.assertNotIn("#SAVE_RECORD_BYTES-1", helper)

    def test_wrapping_generation_selection_matches_reference_rule(self) -> None:
        selection = self.core[
            self.core.index(".slot_b:") :
            self.core.index(".selection_complete:")
        ]
        ordered = (
            "sub.l   (SAVE_ACTIVE_BASE+SAVE_RECORD_GENERATION).l,d0",
            "beq.s   .selection_complete",
            "cmpi.l  #$80000000,d0",
            "bhs.s   .selection_complete",
            ".select_b:",
        )
        cursor = 0
        for token in ordered:
            next_cursor = selection.find(token, cursor)
            self.assertGreaterEqual(next_cursor, 0, token)
            cursor = next_cursor + len(token)

    def test_validation_covers_payload_metrics_and_reserved_tail(self) -> None:
        required = (
            "cmpi.w  #SAVE_FORMAT_VERSION,(SAVE_PAYLOAD_VERSION,a2)",
            "cmpi.w  #5,d0",
            "cmpi.w  #SAVE_SETTINGS_MASK,d0",
            "cmpi.l  #$00100000,d4",
            "cmpi.w  #SAVE_ENCOUNTER_COUNT,d5",
            "moveq   #SAVE_ENCOUNTER_COUNT-1,d7",
            "cmpi.w  #SAVE_RANK_TRANSCENDENT,d0",
            "cmpi.w  #$FFFF,(SAVE_METRIC_ATTEMPTS,a3)",
            "cmpi.b  #$FF,(SAVE_METRIC_ECHOES,a3)",
            "cmpi.w  #720,d0",
            "tst.b   (SAVE_METRIC_RESERVED,a3)",
            "cmp.l   d3,d4",
            "cmp.w   d2,d5",
            ".tail_reserved:",
        )
        for token in required:
            self.assertIn(token, self.core)

    def test_load_clears_stale_errors_on_valid_and_default_recovery(self) -> None:
        load = self.core[
            self.core.index("SaveLoad:") :
            self.core.index("SaveBuildDefaults:")
        ]
        self.assertEqual(
            load.count("clr.w   (SAVE_CONTROL_LAST_ERROR).l"),
            2,
        )

    def test_callee_saved_register_sets_are_restored_exactly(self) -> None:
        pairs = {
            "SaveLoad": "d3-d7/a2-a4",
            "SaveBuildDefaults": "d3-d4/a2",
            "SaveValidateRecord": "d3-d7/a2-a3",
            "SaveCommit": "d3-d7/a2-a4",
            "SaveMarkStageClear": "d3-d7/a2-a4",
        }
        labels = list(pairs)
        for index, label in enumerate(labels):
            start = self.core.index(f"{label}:")
            end = (
                self.core.index(f"{labels[index + 1]}:", start)
                if index + 1 < len(labels)
                else self.core.index("SaveSetSettings:", start)
            )
            routine = self.core[start:end]
            registers = pairs[label]
            self.assertIn(
                f"movem.l {registers},-(a7)", routine, label
            )
            self.assertIn(
                f"movem.l (a7)+,{registers}", routine, label
            )

    def test_stage_metric_packed_abi_and_best_result_order_are_exact(self) -> None:
        routine = self.core[
            self.core.index("SaveMarkStageClear:") :
            self.core.index("SaveSetSettings:")
        ]
        ordered = (
            "move.l  d2,d5",
            "move.l  d5,d4",
            "swap    d4",
            "lsr.w   #8,d4",
            "andi.w  #$00FF,d4",
            "move.l  d5,d3",
            "swap    d3",
            "andi.w  #$00FF,d3",
            "andi.l  #$0000FFFF,d5",
            "cmp.b   (SAVE_METRIC_RANK,a2),d4",
            "bhi.s   .write_metric",
            "blo.s   .progress_only",
            "cmp.w   (SAVE_METRIC_ATTEMPTS,a2),d6",
            "blo.s   .write_metric",
            "bhi.s   .progress_only",
            "cmp.w   d0,d3",
            "blo.s   .write_metric",
            "bhi.s   .progress_only",
            "cmp.w   (SAVE_METRIC_TIME_REMAINING,a2),d5",
            "bhi.s   .write_metric",
        )
        cursor = 0
        for token in ordered:
            next_cursor = routine.find(token, cursor)
            self.assertGreaterEqual(next_cursor, 0, token)
            cursor = next_cursor + len(token)

    def test_all_immediate_shifts_encode_on_base_68000(self) -> None:
        shifts = [
            int(value)
            for value in re.findall(
                r"\b(?:asl|asr|lsl|lsr)\.[bwl]\s+#(\d+)", self.core
            )
        ]
        self.assertTrue(shifts)
        self.assertTrue(all(1 <= value <= 8 for value in shifts), shifts)
        self.assertIn("swap    d4", self.core)
        self.assertIn("swap    d3", self.core)
        self.assertEqual(
            re.findall(r"(?m)^\s+b[a-z]{2}\s+", self.core),
            [],
            "conditional and subroutine branches need explicit widths",
        )


class SaveFormatDocumentationTests(unittest.TestCase):
    def test_format_freezes_crc_commit_order_and_corruption_fallback(self) -> None:
        format_text = (ROOT / "tools/save_reference/FORMAT.md").read_text(
            encoding="utf-8"
        )
        required = (
            "currently standalone and\nunwired from startup and the main game",
            "do not\nclaim SRAM read/write proof",
            "`$200001-$2001FF`",
            "`$200201-$2003FF`",
            "| `$F8` | 4 | CRC-32/ISO-HDLC over bytes `$00-$F7` |",
            "| `$FC` | 4 | commit marker `$A55A94C3` |",
            "physically writes a zero commit marker\nbefore any body byte",
            "writes logical bytes `$00-$FB` in ascending order",
            "writes the four-byte commit marker last",
            "falls back to the previous committed generation",
            "`0 < ((B - A) & $FFFFFFFF) < $80000000`",
            "twenty fixed eight-byte",
            "attempts `1..$FFFE`, echoes `0..6`, time remaining `0..720`",
        )
        for token in required:
            self.assertIn(token, format_text)


if __name__ == "__main__":
    unittest.main()
