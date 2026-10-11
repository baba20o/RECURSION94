from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import m5_budget_checkpoint_phase_diagnostic as diagnostic
from tools.m2_evidence import parse_vasm_symbols, sha256_file


ROOT = Path(__file__).resolve().parents[2]
BASELINE_BUILD = ROOT / "artifacts/archive/m5-pre-timing-140039e3-20261010/level-B03"
LOOKUP_BUILD = ROOT / "artifacts/archive/m5-lookup-only-9a741642-20261010/level-B03"
REPORT = (
    ROOT
    / "artifacts/diagnostics/m5-budget/"
    "overrun-checkpoint-phase-tick59-tick60-strict-20260830.json"
)
TRACE = (
    ROOT
    / "artifacts/diagnostics/m5-budget/"
    "overrun-checkpoint-phase-tick59-tick60-strict-20260830.log"
)
REPORT_SHA256 = (
    "42d291d7a94235dc36eaff1318585aab9d3d604d6fe9d6039e42bab1a28ff24e"
)
TRACE_SHA256 = (
    "f184ce3fb6522432c3991cb4f0f348d0842cf601c760c77dc3ea26d17dde906f"
)
REPAIR_REPORT = (
    ROOT
    / "artifacts/diagnostics/m5-budget/"
    "overrun-checkpoint-phase-post-repair-tick59-tick61-20260830.json"
)
REPAIR_TRACE = (
    ROOT
    / "artifacts/diagnostics/m5-budget/"
    "overrun-checkpoint-phase-post-repair-tick59-tick61-20260830.log"
)
REPAIR_REPORT_SHA256 = (
    "c86fb885e45849f16ca2bafa27889699d0c2d9dfa51a9a0203bb8245a7c31e6f"
)
REPAIR_TRACE_SHA256 = (
    "cd8318b626ad2b1c932c62262baf0da5bd2ed884589850edce8c0ff02c6cbda0"
)
REPAIR_CAPTURE_ROM_SHA256 = (
    "58454e9e63a423c77a9c44fca8066f9a43321365aebcf8a24f43594641495804"
)
REPAIR_CAPTURE_SYMBOL_SHA256 = (
    "d7db12feb50430ce955ddc6b7a0a45b300daa3b2f42a8d46e70752a1d5c25e1d"
)
REPAIR_CAPTURE_COLLECTOR_SHA256 = (
    "a549032c23841ca231c132806cc8f0bc91de6cabdf9f202006c7588aa063eaa8"
)
FINAL_ROM_SHA256 = (
    "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e"
)
FINAL_SYMBOL_SHA256 = (
    "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0"
)
FINAL_REPAIR_PC = {
    "gameplay_call": 0x000002FA,
    "gameplay_return": 0x000002FE,
    "raw_crc_start": 0x00006A62,
    "raw_crc_end": 0x00006A66,
    "engine_projection_start": 0x0000214A,
    "engine_projection_end": 0x00002362,
    "tail_start": 0x0000561E,
    "tail_end": 0x00005632,
    "campaign_projection_start": 0x000069F8,
    "campaign_projection_end": 0x00006AA8,
    "renderer_start": 0x00008C1A,
    "renderer_end": 0x00008DF8,
    "dma_commit": 0x0000126A,
    "dma_issue_start": 0x00001440,
    "dma_issue_end": 0x000014E6,
    "vblank_entry": 0x0000169A,
    "vblank_ack": 0x000016BC,
    "vblank_post_dma": 0x000016E0,
    "vblank_status": 0x0000171A,
    "vblank_origin": 0x0000172C,
    "vblank_common": 0x0000173C,
    "vblank_rte": 0x00001850,
}


class M5BudgetCheckpointPhaseDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPORT.read_text("utf-8"))
        trace_text = TRACE.read_text("utf-8")
        cls.console = trace_text.split(
            "----- CONSOLE TRANSCRIPT -----\n", 1
        )[1]
        cls.records = diagnostic.parse_markers(cls.console)

    def test_preserved_strict_fault_artifact_recomputes(self) -> None:
        self.assertEqual(sha256_file(REPORT), REPORT_SHA256)
        self.assertEqual(sha256_file(TRACE), TRACE_SHA256)
        self.assertEqual(self.report["status"], "diagnostic_pass")
        self.assertEqual(self.report["read_only_contract"]["console_contract_errors"], [])
        self.assertEqual(self.report["analysis"], diagnostic.analyse(self.records))
        symbols = parse_vasm_symbols(
            ROOT / diagnostic.SYMBOL_RELATIVE_PATH
        )
        self.assertEqual(
            self.report["provenance"]["rom"]["sha256"],
            "2728497799e1673369c81d3e5e12759e45d44c1c4666e0fb2cc555fd2d20fc6f",
        )
        self.assertEqual(
            self.report["pc_contract"]["addresses"], diagnostic.PC
        )
        script = diagnostic.build_command_script(symbols)
        self.assertEqual(diagnostic.command_policy_errors(script, symbols), [])
        self.assertNotIn("set ", script.lower())

    def test_preserved_repair_rom_and_read_only_script_contract(self) -> None:
        symbol_path = BASELINE_BUILD / "recursion94-debug.sym"
        rom_path = BASELINE_BUILD / "recursion94-debug.bin"
        self.assertEqual(diagnostic.PINNED_ROM_SHA256, FINAL_ROM_SHA256)
        self.assertEqual(
            diagnostic.PINNED_SYMBOL_SHA256, FINAL_SYMBOL_SHA256
        )
        self.assertEqual(sha256_file(rom_path), FINAL_ROM_SHA256)
        self.assertEqual(sha256_file(symbol_path), FINAL_SYMBOL_SHA256)
        symbols = parse_vasm_symbols(symbol_path)
        rom = rom_path.read_bytes()
        self.assertEqual(diagnostic.REPAIR_PC, FINAL_REPAIR_PC)
        contract = diagnostic.repair_pc_contract(rom, symbols)
        self.assertEqual(contract["addresses"], FINAL_REPAIR_PC)
        script = diagnostic.build_repair_command_script(symbols)
        self.assertEqual(
            diagnostic.repair_command_policy_errors(script, symbols), []
        )
        self.assertEqual(
            sum(line.startswith("breakpoint ") for line in script.splitlines()),
            len(diagnostic.REPAIR_PC),
        )
        self.assertLessEqual(
            sum(line.strip() == "continue" for line in script.splitlines()),
            96,
        )
        self.assertNotIn("set ", script.lower())

    def test_queue_crc_and_origin_mutations_fail(self) -> None:
        queue = copy.deepcopy(self.records)
        first_queue = next(record for record in queue if record["phase"] == "Q")
        first_queue["words"] += 1
        self.assertEqual(diagnostic.analyse(queue)["status"], "fail")

        crc = copy.deepcopy(self.records)
        crc_end = next(record for record in crc if record["phase"] == "CE")
        crc_end["crc"] ^= 1
        self.assertEqual(diagnostic.analyse(crc)["status"], "fail")

        origin = copy.deepcopy(self.records)
        origin_record = next(
            record for record in origin if record["phase"] == "VO"
        )
        origin_record["d0"] = 8
        self.assertEqual(diagnostic.analyse(origin)["status"], "fail")

    def test_lookup_profile_binds_new_rom_without_changing_baseline(self) -> None:
        symbols = parse_vasm_symbols(LOOKUP_BUILD / "recursion94-debug.sym")
        rom_path = LOOKUP_BUILD / "recursion94-debug.bin"
        self.assertEqual(sha256_file(rom_path), diagnostic.LOOKUP_ROM_SHA256)
        contract = diagnostic.repair_pc_contract(rom_path.read_bytes(), symbols, profile="lookup")
        self.assertEqual(contract["addresses"], diagnostic.LOOKUP_REPAIR_PC)
        script = diagnostic.build_repair_command_script(symbols, profile="lookup")
        self.assertEqual(diagnostic.repair_command_policy_errors(script, symbols, profile="lookup"), [])
        with self.assertRaises(diagnostic.EvidenceFailure):
            diagnostic.repair_pc_contract(rom_path.read_bytes(), symbols)

    def test_raw_queue_order_and_interrupt_subtraction_are_exact(self) -> None:
        reordered = copy.deepcopy(self.records)
        queue_positions = [
            index
            for index, record in enumerate(reordered)
            if record["phase"] == "Q" and record["frame"] == 59
        ]
        left, right = queue_positions[:2]
        reordered[left], reordered[right] = reordered[right], reordered[left]
        self.assertEqual(diagnostic.analyse(reordered)["status"], "fail")

        shifted = copy.deepcopy(self.records)
        vblank_exit = next(
            record
            for record in shifted
            if record["phase"] == "VX" and record["frame"] == 61
        )
        next_entry = next(
            record
            for record in shifted
            if record["phase"] == "VE" and record["frame"] == 61
        )
        vblank_exit["cycle"] = next_entry["cycle"] + 1
        self.assertEqual(diagnostic.analyse(shifted)["status"], "fail")

    def test_renderer_hwm_profile_binds_archived_rom_symbols_and_unchanged_gameplay(self) -> None:
        archive = ROOT / "artifacts/archive/m5-renderer-hwm-e6778f90-20261010/level-B03"
        rom_path, sym_path = archive / "recursion94-debug.bin", archive / "recursion94-debug.sym"
        rom, symbols = rom_path.read_bytes(), parse_vasm_symbols(sym_path)
        self.assertEqual(sha256_file(rom_path), "e6778f907da9f308cb587de695cdba5dc32371538e92ccd34676821428bf3c65")
        self.assertEqual(sha256_file(sym_path), "a67cba2c05ca4b1920c51e00c4022b2d993c408b3c5b9899e511bfc0d90dc6f6")
        contract = diagnostic.repair_pc_contract(rom, symbols, profile="renderer_hwm")
        self.assertEqual(contract["addresses"], {**diagnostic.PROFILE_REPAIR_PC["highwater"], "renderer_start": 0x8DA4, "renderer_end": 0x8F82})
        script = diagnostic.build_repair_command_script(symbols, profile="renderer_hwm")
        self.assertEqual(diagnostic.repair_command_policy_errors(script, symbols, profile="renderer_hwm"), [])
        for address in (0x1C2E, 0x24F4, 0x252E, 0x6B64, 0x8DA0, 0x8F82):
            changed = bytearray(rom)
            changed[address] ^= 1
            with self.subTest(address=address), self.assertRaises(diagnostic.EvidenceFailure):
                diagnostic.repair_pc_contract(bytes(changed), symbols, profile="renderer_hwm")
        with self.assertRaises(diagnostic.EvidenceFailure):
            diagnostic.repair_pc_contract(rom, symbols, profile="highwater")

    def test_renderer_hwm_candidate_source_and_full_rom_drift_stop_before_launch(self) -> None:
        sources = diagnostic.renderer_hwm_source_contract(ROOT)
        self.assertEqual({key: value["sha256"] for key, value in sources.items()}, diagnostic.RENDERER_HWM_SOURCE_PINS)
        self.assertEqual(len(sources), 2)
        with tempfile.TemporaryDirectory() as directory:
            args = diagnostic._parser().parse_args([
                "--root", str(ROOT), "--profile", "renderer_hwm",
                "--report", str(Path(directory) / "unused.json"), "--trace", str(Path(directory) / "unused.log")])
            for relative in (*diagnostic.RENDERER_HWM_SOURCE_PINS, diagnostic.ROM_RELATIVE_PATH, diagnostic.SYMBOL_RELATIVE_PATH):
                def changed_hash(path: Path) -> str:
                    return "0" * 64 if path == ROOT / relative else sha256_file(path)
                with self.subTest(path=relative), patch.object(diagnostic, "os", SimpleNamespace(name="nt")), \
                        patch.object(diagnostic, "sha256_file", side_effect=changed_hash), \
                        patch.object(diagnostic.subprocess, "Popen", side_effect=AssertionError("drift must stop before launch")):
                    with self.assertRaisesRegex(diagnostic.EvidenceFailure, "hash drift"):
                        diagnostic.collect(args)
            self.assertEqual(list(Path(directory).iterdir()), [])


class M5BudgetCheckpointRepairPhaseDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPAIR_REPORT.read_text("utf-8"))
        trace_text = REPAIR_TRACE.read_text("utf-8")
        command_and_console = trace_text.split(
            "----- COMMAND SCRIPT -----\n", 1
        )[1]
        cls.trace_script, cls.console = command_and_console.split(
            "----- CONSOLE TRANSCRIPT -----\n", 1
        )
        cls.records = diagnostic.parse_markers(cls.console)

    def test_strict_repair_artifact_recomputes(self) -> None:
        self.assertEqual(sha256_file(REPAIR_REPORT), REPAIR_REPORT_SHA256)
        self.assertEqual(sha256_file(REPAIR_TRACE), REPAIR_TRACE_SHA256)
        self.assertEqual(self.report["status"], "diagnostic_pass")
        self.assertEqual(
            self.report["read_only_contract"]["console_contract_errors"], []
        )
        self.assertEqual(
            self.report["analysis"], diagnostic.analyse_repair(self.records)
        )
        self.assertEqual(
            diagnostic.console_contract_errors(
                self.console, self.trace_script
            ),
            [],
        )
        self.assertEqual(
            self.report["provenance"]["rom"]["sha256"],
            REPAIR_CAPTURE_ROM_SHA256,
        )
        self.assertEqual(
            self.report["provenance"]["symbols"]["sha256"],
            REPAIR_CAPTURE_SYMBOL_SHA256,
        )
        self.assertEqual(
            self.report["provenance"]["collector"]["sha256"],
            REPAIR_CAPTURE_COLLECTOR_SHA256,
        )
        self.assertEqual(
            self.report["command_script_sha256"],
            diagnostic._sha256_bytes(
                self.report["command_script"].encode("utf-8")
            ),
        )
        self.assertEqual(self.trace_script, self.report["command_script"])

    def test_defer_queue_and_origin_mutations_fail(self) -> None:
        wrong_result = copy.deepcopy(self.records)
        tick60 = next(
            record
            for record in wrong_result
            if record["phase"] == "RR" and record["tick"] == 60
        )
        tick60["result"] = 0
        self.assertEqual(diagnostic.analyse_repair(wrong_result)["status"], "fail")

        wrong_queue = copy.deepcopy(self.records)
        frame61 = next(
            record
            for record in wrong_queue
            if record["phase"] == "VC" and record["frame"] == 61
        )
        frame61["pending"] = 1
        self.assertEqual(diagnostic.analyse_repair(wrong_queue)["status"], "fail")

        origin = copy.deepcopy(self.records)
        origin.insert(
            -1,
            {
                "phase": "VO",
                "cycle": origin[-1]["cycle"] - 1,
                "frame": 62,
                "d0": 0,
                "d1": 8,
                "counter": 0,
                "sequence": 123,
            },
        )
        self.assertEqual(diagnostic.analyse_repair(origin)["status"], "fail")

    def test_projection_parity_and_headroom_mutations_fail(self) -> None:
        parity = copy.deepcopy(self.records)
        publication = next(
            record for record in parity if record["phase"] == "PV"
        )
        publication["state_crc"] ^= 1
        self.assertEqual(diagnostic.analyse_repair(parity)["status"], "fail")

        debug_parity = copy.deepcopy(self.records)
        frame61 = next(
            record
            for record in debug_parity
            if record["phase"] == "DP" and record["frame"] == 61
        )
        frame61["debug_crc"] ^= 1
        self.assertEqual(
            diagnostic.analyse_repair(debug_parity)["status"], "fail"
        )

        no_headroom = copy.deepcopy(self.records)
        prior_entry = next(
            record
            for record in no_headroom
            if record["phase"] == "VE" and record["frame"] == 59
        )
        renderer_end = next(
            record
            for record in no_headroom
            if record["phase"] == "RR" and record["tick"] == 60
        )
        renderer_end["cycle"] = (
            prior_entry["cycle"] + diagnostic.NTSC_FRAME_MASTER_CLOCKS + 1
        )
        self.assertEqual(
            diagnostic.analyse_repair(no_headroom)["status"], "fail"
        )


if __name__ == "__main__":
    unittest.main()
