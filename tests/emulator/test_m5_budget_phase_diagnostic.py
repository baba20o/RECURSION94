"""Historical diagnostic metadata and explicitly synthetic validator coverage.

The full 27284977 ROM and 3f0f172d symbol map are unavailable.  The archived
report/trace tests below verify captured metadata, not those missing files.
Synthetic opcode fixtures are not historical ROM evidence.  Current M5 native
gates remain independent and the live helper's pinned-hash checks stay strict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import m5_budget_phase_diagnostic as phase
from tools.m2_evidence import sha256_file


ROOT = Path(__file__).resolve().parents[2]
CAPTURE_REPORT_SHA256 = (
    "a84ebc1e936632cdfb391f83623057cad3bb4ff28766c9c436c3641b1fab458e"
)
CAPTURE_TRACE_SHA256 = (
    "597f16ad2ad82ad7a9b7c2eeeddc9e8d412676509fccd03d24c06d3b84cdd9f7"
)
CAPTURE_ROM_SHA256 = (
    "2728497799e1673369c81d3e5e12759e45d44c1c4666e0fb2cc555fd2d20fc6f"
)
CAPTURE_SYMBOL_SHA256 = (
    "3f0f172dceec267267c5f841e7011de4477a2fb04a082476c138ada4f4cfb526"
)
# Only the named RAM operands present in the hash-bound captured command script;
# this is deliberately not a substitute for the unavailable complete symbol map.
CAPTURE_COMMAND_SYMBOLS = {
    "DEBUG_FRAME_COUNTER": 0xFFF010,
    "ENGINE_TICK": 0xFF1410,
    "DEBUG_BOOT_STAGE": 0xFFF018,
    "DEBUG_FRAME_TOKEN": 0xFFF014,
    "DEBUG_SEQUENCE": 0xFFF008,
    "INPUT_HELD": 0xFF9800,
    "DEBUG_LAST_VDP_STATUS": 0xFFF01C,
    "DEBUG_VBLANK_OVERRUN_COUNT": 0xFFF032,
    "DEBUG_DMA_WORDS_FRAME": 0xFFF028,
    "DEBUG_DMA_QUEUE_DEPTH": 0xFFF02C,
    "DMA_QUEUE_FRAME_PENDING": 0xFF900A,
    "DMA_QUEUE_PUBLISHED_DEPTH": 0xFF9000,
    "DMA_QUEUE_BUILD_DEPTH": 0xFF9002,
    "DMA_QUEUE_BUILD_WORDS": 0xFF9004,
    "DMA_QUEUE_COMMIT_WORDS": 0xFF9008,
    "DMA_QUEUE_ENTRIES": 0xFF9010,
}


def _synthetic_pc_fixture() -> tuple[bytearray, dict[str, int]]:
    """Sparse invented bytes for unit tests only; not an assembled/captured ROM."""
    image = bytearray(max(phase.PC.values()) + 16)
    for address, opcode in (
        (phase.PC["renderer_masked_start"] - 4, "46FC2700"),
        (phase.PC["renderer_masked_end"], "46C7"),
        (phase.PC["vblank_overrun_compare"], "0C79FFFF00FFF0326706"),
        (phase.PC["vblank_rte"], "4E73"),
    ):
        payload = bytes.fromhex(opcode)
        image[address : address + len(payload)] = payload
    symbols = {
        symbol: phase.PC[name]
        for name, symbol in phase.PC_SYMBOL_BINDINGS.items()
    }
    symbols["GameplayRendererBuildAndQueue"] = (
        phase.PC["renderer_masked_start"] - 10
    )
    return image, symbols


class M5BudgetPhaseDiagnosticTests(unittest.TestCase):
    def test_archived_metadata_is_hash_bound_not_full_rom_verification(self) -> None:
        report_path = ROOT / phase.DEFAULT_REPORT_RELATIVE_PATH
        trace_path = ROOT / phase.DEFAULT_TRACE_RELATIVE_PATH
        self.assertEqual(sha256_file(report_path), CAPTURE_REPORT_SHA256)
        self.assertEqual(sha256_file(trace_path), CAPTURE_TRACE_SHA256)
        report = json.loads(report_path.read_text("utf-8"))
        trace = trace_path.read_text("utf-8")
        script_section, divider, console = trace.partition(
            "----- CONSOLE TRANSCRIPT -----\n"
        )
        self.assertTrue(divider)
        captured_script = script_section.split(
            "----- COMMAND SCRIPT -----\n", 1
        )[1]
        self.assertEqual(report["schema"], phase.REPORT_SCHEMA)
        self.assertEqual(report["evidence_type"], phase.EVIDENCE_TYPE)
        self.assertFalse(report["canonical"])
        self.assertTrue(report["diagnostic_only"])
        self.assertEqual(report["provenance"]["rom"]["sha256"], CAPTURE_ROM_SHA256)
        self.assertEqual(
            report["provenance"]["symbols"]["sha256"], CAPTURE_SYMBOL_SHA256
        )
        self.assertEqual(phase.PINNED_ROM_SHA256, CAPTURE_ROM_SHA256)
        self.assertEqual(phase.PINNED_SYMBOL_SHA256, CAPTURE_SYMBOL_SHA256)
        self.assertEqual(report["pc_contract"]["addresses"], phase.PC)
        self.assertEqual(
            report["pc_contract"]["symbol_bindings"], phase.PC_SYMBOL_BINDINGS
        )
        self.assertEqual(set(report["pc_contract"]["rom_windows"]), set(phase.PC))
        for name, address in phase.PC.items():
            self.assertEqual(
                report["pc_contract"]["rom_windows"][name]["address"], address
            )
        script = phase.build_command_script(CAPTURE_COMMAND_SYMBOLS)
        self.assertEqual(script, captured_script)
        self.assertEqual(script, report["command_script"])
        self.assertEqual(
            hashlib.sha256(script.encode()).hexdigest(),
            report["command_script_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(console.encode()).hexdigest(), report["console_sha256"]
        )
        self.assertEqual(phase._trace_text(script, console), trace)
        self.assertEqual(phase.command_policy_errors(script, CAPTURE_COMMAND_SYMBOLS), [])
        self.assertEqual(phase.console_contract_errors(console, script), [])
        for field in ("guest_memory_writes", "guest_register_writes", "guest_pc_writes"):
            self.assertEqual(report["read_only_contract"][field], 0)
        self.assertEqual(script.count("breakpoint $"), len(phase.PC))
        self.assertEqual(script.count(phase.READY_MARKER), 1)
        self.assertEqual(script.count(phase.COMPLETE_MARKER), 1)
        self.assertNotIn("\nset ", "\n" + script.lower())

    def test_synthetic_opcode_and_symbol_fixture_passes_pc_validator(self) -> None:
        image, symbols = _synthetic_pc_fixture()
        contract = phase._pc_contract(bytes(image), symbols)
        self.assertEqual(contract["addresses"], phase.PC)
        self.assertEqual(contract["symbol_bindings"], phase.PC_SYMBOL_BINDINGS)
        for name, address in phase.PC.items():
            self.assertEqual(
                contract["rom_windows"][name],
                {"address": address, "hex": image[address : address + 16].hex().upper()},
            )

    def test_synthetic_checked_opcode_mutations_are_rejected(self) -> None:
        image, symbols = _synthetic_pc_fixture()
        for start, length, error in (
            (phase.PC["renderer_masked_start"] - 4, 4, "renderer mask instruction drift"),
            (phase.PC["renderer_masked_end"], 2, "renderer SR-restore instruction drift"),
            (phase.PC["vblank_overrun_compare"], 10, "VBlank overrun compare bytes drift"),
            (phase.PC["vblank_rte"], 2, "VBlank rte opcode drift"),
        ):
            for address in range(start, start + length):
                with self.subTest(address=hex(address)):
                    mutated = bytearray(image)
                    mutated[address] ^= 1
                    with self.assertRaisesRegex(phase.EvidenceFailure, error):
                        phase._pc_contract(bytes(mutated), symbols)

    def test_synthetic_missing_or_shifted_symbols_are_rejected(self) -> None:
        image, symbols = _synthetic_pc_fixture()
        for name in symbols:
            for missing in (False, True):
                with self.subTest(symbol=name, missing=missing):
                    mutated = dict(symbols)
                    if missing:
                        del mutated[name]
                    else:
                        mutated[name] += 2
                    with self.assertRaisesRegex(phase.EvidenceFailure, name):
                        phase._pc_contract(bytes(image), mutated)

    def test_current_mismatched_build_cannot_launch_historical_diagnostic(self) -> None:
        # Native Windows exercises the real wrong-ROM hash preflight.  Other
        # hosts exercise only its explicit unsupported-platform rejection.
        self.assertNotEqual(
            sha256_file(ROOT / phase.ROM_RELATIVE_PATH), phase.PINNED_ROM_SHA256
        )
        args = argparse.Namespace(
            root=str(ROOT),
            report=str(ROOT / "artifacts/diagnostics/m5-budget/no-launch-test.json"),
            trace=str(ROOT / "artifacts/diagnostics/m5-budget/no-launch-test.log"),
            timeout_seconds=1.0,
        )
        expected = (
            "pinned B03 Debug ROM hash drift"
            if phase.os.name == "nt"
            else "live phase diagnostic requires native Windows Python"
        )
        with patch.object(
            phase.subprocess, "Popen", side_effect=AssertionError("must not launch")
        ) as launch:
            with self.assertRaisesRegex(phase.EvidenceFailure, expected):
                phase.collect(args)
            launch.assert_not_called()

    def test_pre_packing_artifacts_remain_hash_bound(self) -> None:
        report_path = ROOT / phase.BASELINE_REPORT_RELATIVE_PATH
        trace_path = ROOT / phase.BASELINE_TRACE_RELATIVE_PATH
        self.assertEqual(sha256_file(report_path), phase.BASELINE_REPORT_SHA256)
        self.assertEqual(sha256_file(trace_path), phase.BASELINE_TRACE_SHA256)
        report = json.loads(report_path.read_text("utf-8"))
        self.assertEqual(report["status"], "diagnostic_pass")
        self.assertEqual(
            report["provenance"]["rom"]["sha256"], phase.BASELINE_ROM_SHA256
        )
        self.assertEqual(report["analysis"]["origin_signature"]["frame"], 3)
        self.assertEqual(report["analysis"]["origin_signature"]["counter"], 0)

    def test_post_packing_artifact_recomputes_clean(self) -> None:
        report_path = ROOT / phase.DEFAULT_REPORT_RELATIVE_PATH
        trace_path = ROOT / phase.DEFAULT_TRACE_RELATIVE_PATH
        report = json.loads(report_path.read_text("utf-8"))
        trace = trace_path.read_text("utf-8")
        script_section, divider, console_text = trace.partition(
            "----- CONSOLE TRANSCRIPT -----\n"
        )
        self.assertTrue(divider)
        embedded_script = script_section.split(
            "----- COMMAND SCRIPT -----\n", 1
        )[1]
        self.assertEqual(embedded_script, report["command_script"])
        records = phase.parse_markers(console_text)
        self.assertEqual(records, report["records"])
        self.assertEqual(phase.console_contract_errors(console_text, embedded_script), [])
        self.assertEqual(phase.analyse(records), report["analysis"])
        self.assertEqual(report["status"], "diagnostic_pass")
        self.assertIsNone(report["analysis"]["origin_signature"])
        entry = report["analysis"]["entry_phase_comparison"]
        self.assertLessEqual(
            entry["consecutive_entry_delta_master_clocks"],
            entry["nominal_ntsc_frame_master_clocks"],
        )
        self.assertFalse(entry["renderer2_mask_contains_nominal_frame3_entry"])
        self.assertGreater(
            entry["renderer2_headroom_before_nominal_frame3_entry_master_clocks"],
            0,
        )
        self.assertEqual(
            report["baseline_comparison"]["counter_transition"],
            {"baseline_frame3_counter": 1, "post_packing_frame3_counter": 0},
        )
        self.assertTrue(report["baseline_comparison"]["same_queue_records"])
        self.assertTrue(
            report["baseline_comparison"]["same_dma_call_arguments"]
        )
        self.assertEqual(report["baseline_comparison"]["attribution_errors"], [])
        self.assertTrue(
            report["baseline_comparison"][
                "renderer_savings_exceeds_baseline_debt"
            ]
        )
        self.assertGreater(
            report["baseline_comparison"][
                "minimum_renderer_savings_cpu_cycles"
            ],
            phase.BASELINE_CADENCE_DEBT_CPU_CYCLES,
        )
        self.assertTrue(
            all(
                post < before
                for before, post in zip(
                    report["baseline_comparison"][
                        "baseline_sat_cpu_cycles"
                    ],
                    report["baseline_comparison"][
                        "post_packing_sat_cpu_cycles"
                    ],
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
