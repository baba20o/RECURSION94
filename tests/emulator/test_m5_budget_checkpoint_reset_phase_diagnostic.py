from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import io
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import m5_budget_checkpoint_phase_diagnostic as regular
from tools import m5_budget_checkpoint_reset_phase_diagnostic as reset
from tools.m2_evidence import parse_vasm_symbols


ROOT = Path(__file__).resolve().parents[2]
BASELINE_BUILD = ROOT / "artifacts/archive/m5-pre-timing-140039e3-20261010/level-B03"
LOOKUP_BUILD = ROOT / "artifacts/archive/m5-lookup-only-9a741642-20261010/level-B03"
ROM = BASELINE_BUILD / "recursion94-debug.bin"
SYMBOLS = BASELINE_BUILD / "recursion94-debug.sym"


def captured_prefix_records() -> tuple[dict, list[dict]]:
    """Independently parse the preserved native log, never a synthetic pass."""
    report_path = ROOT / (reset.PREFIX_CAPTURE_STEM + ".json")
    trace_path = ROOT / (reset.PREFIX_CAPTURE_STEM + ".log")
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == reset.PREFIX_CAPTURE_REPORT_SHA256
    assert hashlib.sha256(trace_path.read_bytes()).hexdigest() == reset.PREFIX_CAPTURE_TRACE_SHA256
    capture = json.loads(report_path.read_text("utf-8"))
    records = reset.parse_prefix_markers(trace_path.read_text("utf-8").split("----- CONSOLE TRANSCRIPT -----\n", 1)[1])
    assert records == capture["records"]
    return capture, records


def synthetic_prefix_records() -> tuple[list[dict], dict]:
    """Explicit parser/analyser fixture, NOT a native capture or gameplay model.

    Zero replay RAM and artificial short spans exercise proof structure only.
    The production route loader can never admit this synthetic reference.
    """
    records = []
    reference = {"engine": {"state_crc": 1234}, "gameplay": {"last_bank_crc": 5678},
                 "replay_timeline": {"sha256": hashlib.sha256(bytes(5120)).hexdigest()}}
    route = {"reset_snapshots": {"1": reference, "2": reference}, "projectile_counts": {}}
    stages = {item["total"]: item for item in reset.prefix_input_stages()}

    def add(phase: str, **values: int) -> dict:
        record = {"phase": phase, **dict.fromkeys(reset.PREFIX_MARKER_FIELDS[phase], 0), **values}
        records.append(record)
        return record

    for index, mask in enumerate(reset.prefix_schedule()):
        attempt, pre_tick = divmod(index, 720)
        tick, frame = pre_tick + 1, index + 1 + attempt
        base = frame * reset.NTSC_FRAME_MASTER_CLOCKS
        sample = attempt == 2 and tick in reset.PREFIX_SAMPLE_POST_TICKS
        ident = {"attempt": attempt, "tick": tick, "frame": frame}
        pre_ident = {**ident, "tick": pre_tick}
        if attempt == 2 and any(first <= pre_tick < last for first, last in reset.PREFIX_IRQ_TICK_RANGES):
            queue = () if pre_tick % 60 == 0 else ((0xFF8000, 0xF000, 320, 0x8000), (0xFF8600, 0xC000, 40, 0x8001), (0xFF8650, 0xC080, 40, 0x8001), (0xFF86A0, 0xC100, 40, 0x8001))
            words = sum(item[2] for item in queue)
            add("WW", cycle=base - 50, **{**pre_ident, "frame": frame - 1}, sr=0x2700)
            add("VE", cycle=base, **{**pre_ident, "frame": frame - 1}, sr=0x2600)
            add("VA", cycle=base + 7, frame=frame - 1, entry_status=8, sequence=2 * frame - 1)
            add("VC", cycle=base + 14, **pre_ident, pending=int(bool(queue)), published_depth=len(queue), build_depth=len(queue), build_words=words)
            for slot, (source, dest, size, flags) in enumerate(queue):
                add("Q", frame=frame, index=slot, source=source, destination=dest, words=size, flags=flags)
            cumulative = 0
            for slot, (source, dest, size, _flags) in enumerate(queue):
                cumulative += size
                add("DS", cycle=base + 21 + slot * 14, frame=frame, source=source, destination=dest, words=size, commit_words=cumulative)
                add("DE", cycle=base + 28 + slot * 14, frame=frame, commit_words=cumulative)
            add("VP", cycle=base + 100, frame=frame, commit_words=words, debug_words=words)
            add("VS", cycle=base + 107, frame=frame, entry_status=8, exit_status=8)
            add("VK", cycle=base + 114, frame=frame, d0=8, d1=8)
            add("VX", cycle=base + 121, **pre_ident, sequence=2 * frame, token=1, debug_words=words)
        if sample:
            add("MB", cycle=base + 1000, **pre_ident, sr=0x2700)
        add("MS", cycle=base + 1100, **pre_ident, input=mask, d0=mask, checkpoint_count=1 + pre_tick // 60)
        if index + 1 in stages:
            add("HI", cycle=base + 1100, **pre_ident, current=mask, desired=stages[index + 1]["desired"])
        if sample and tick % 60 == 0:
            for offset, phase in enumerate(("PS", "ES", "EE", "TS", "TE", "CS", "CE", "PE")):
                item = add(phase, cycle=base + 1200 + offset * 100, **ident)
                if "checkpoint_count" in item:
                    item["checkpoint_count"] = int(phase in {"EE", "PE"}) + tick // 60
                if phase == "PS":
                    item["engine_state"] = 1
                if phase == "EE":
                    item["length"] = 0x620E36
                if phase == "TS":
                    item["destination"] = 0xFF6E36
                if phase == "TE":
                    item.update(destination=0xFF6F36, length=0x620100)
                if phase == "CS":
                    item.update(source=0xFF6E36, length=0x620100)
                if phase == "CE":
                    item["raw_crc"] = 0xEDCBA987
                if phase == "PE":
                    item.update(length=0xFFFF0F36, crc=0x12345678, state_crc=0x12345678)
        add("ME", cycle=base + 3000, **ident, result=8 if tick == 720 else 0,
            checkpoint_count=1 + tick // 60, engine_state=2 if tick == 720 else 1,
            engine_input=mask, actor_count=attempt + 1)
        if sample:
            add("RM", cycle=base + 3300, **ident, sr=0x2500)
            add("RG", cycle=base + 3400, **ident, sr=0x2700, campaign_flags=3, checkpoint_count=1 + tick // 60, renderer_checkpoint_count=1 + pre_tick // 60, renderer_attempt=2)
            if tick % 60:
                for offset, phase in enumerate(("HB", "HE", "SE", "OE")):
                    add(phase, cycle=base + 3500 + offset * 100, **ident, sr=0x2700)
            add("RR", cycle=base + 3900, **ident, sr=0x2700, result=4 if tick % 60 == 0 else 0,
                checkpoint_count=1 + tick // 60, renderer_checkpoint_count=1 + tick // 60, renderer_attempt=2, pending=int(tick % 60 != 0))
            add("RX", cycle=base + 4000, **ident, sr=0x2500)
            add("MD", cycle=base + 4100, **ident, sr=0x2500)
        if tick == 720:
            new = {"attempt": attempt + 1, "tick": 0, "frame": frame + 1}
            end = base + reset.NTSC_FRAME_MASTER_CLOCKS + 3000
            add("MR", cycle=end, **new, checkpoint_count=1, state_crc=1234, replay_crc=5678)
            for address in range(0xFF0000, 0xFF1400, 16):
                add("RB", **new, address=address)
            add("BC", cycle=end + 100, **new, snapshot_bank=6 if attempt == 0 else 0,
                current_bank=attempt, token=1, sr=0x2500)
            add("BM", cycle=end + 200, **new, token=1, sr=0x2700)
            add("BZ", cycle=end + 300, **new, sr=0x2700)
            add("BW", cycle=end + 400, **new, sr=0x2700)
    return records, route


def exact_records(*, extended: bool = False, late: bool = False) -> list[dict[str, int | str]]:
    records: list[dict[str, int | str]] = []
    cycle = 7

    def add(phase: str, **values: int) -> dict[str, int | str]:
        nonlocal cycle
        item: dict[str, int | str] = {"phase": phase}
        fields = {**reset.MARKER_FIELDS, **reset.EXTENDED_MARKER_FIELDS}
        if "cycle" in fields[phase]:
            item["cycle"] = cycle
            cycle += 7
        item.update(values)
        missing = set(fields[phase]) - item.keys()
        if missing:
            raise AssertionError(f"fixture {phase} missing {sorted(missing)}")
        records.append(item)
        return item

    add("HI", frame=46, attempt=0, tick=46, current=1, desired=8)
    add("HI", frame=67, attempt=0, tick=67, current=8, desired=0)

    queue4 = (
        (0x00FF8000, 0xF000, 0x0140, 0x8000, 0),
        (0x00FF8600, 0xC000, 0x0028, 0x8001, 0),
        (0x00FF8650, 0xC080, 0x0028, 0x8001, 0),
        (0x00FF86A0, 0xC100, 0x0028, 0x8001, 0),
    )
    queues = {
        719: queue4,
        720: queue4,
        721: (),
        722: (),
        723: (),
        724: queue4 + ((0x00FF8740, 0xC180, 0x28, 0x8002, 0),),
    }
    identities = {
        719: (0, 718),
        720: (0, 719),
        721: (0, 720),
        722: (1, 0),
        723: (1, 0),
        724: (1, 1),
        725: (1, 2),
        726: (1, 3),
    }
    queues[725] = queue4 + ((0xFF86F0, 0xC180, 40, 0x8002, 0), (0xFF8740, 0xC280, 40, 0x8002, 0))
    queues[726] = queue4 + ((0xFF86F0, 0xC280, 40, 0x8002, 0), (0xFF8740, 0xC380, 40, 0x8002, 0))

    def boundary(phase: str, frame: int, attempt: int, tick: int, sr: int = 0x2700, token: int = 0) -> None:
        add(phase, frame=frame, attempt=attempt, tick=tick, sr=sr, token=token)

    def vblank(frame: int) -> None:
        nonlocal cycle
        cycle = (frame - 718) * reset.NTSC_FRAME_MASTER_CLOCKS
        attempt, tick = identities[frame]
        queue = queues[frame]
        words = sum(item[2] for item in queue)
        token_before = 1 if frame == 722 else 0
        token_after = 2 if frame == 722 else 1
        add(
            "VE",
            frame=frame - 1,
            attempt=attempt,
            tick=tick,
            sr=0x2700,
            token=token_before,
        )
        add(
            "VA",
            frame=frame - 1,
            entry_status=0 if late and frame == 725 else 8,
            sequence=frame * 2 - 1,
        )
        add(
            "VC",
            frame=frame,
            attempt=attempt,
            tick=tick,
            pending=1 if queue else 0,
            published_depth=len(queue),
            build_depth=len(queue),
            build_words=words,
        )
        for index, (source, destination, count, flags, age) in enumerate(queue):
            add(
                "Q",
                frame=frame,
                index=index,
                source=source,
                destination=destination,
                words=count,
                flags=flags,
                age=age,
            )
        cumulative = 0
        for source, destination, count, flags, age in queue:
            cumulative += count
            add(
                "DS",
                frame=frame,
                source=source,
                destination=destination,
                words=count,
                commit_words=cumulative,
            )
            add(
                "DE",
                frame=frame,
                commit_words=cumulative,
                d0=1,
                d1=8,
            )
        add(
            "VP",
            frame=frame,
            commit_words=words,
            debug_words=words,
            depth=0,
            pending=0,
        )
        status = 0 if late and frame == 725 else 8
        add("VS", frame=frame, exit_status=status, entry_status=status, counter=0)
        add("VK", frame=frame, d0=status, d1=status, counter=0)
        add(
            "VX",
            frame=frame,
            attempt=attempt,
            tick=tick,
            sequence=frame * 2,
            counter=0,
            token=token_after,
            debug_words=words,
        )

    def gameplay(
        frame: int,
        attempt: int,
        pre_tick: int,
        result: int,
        pre_count: int,
        post_count: int,
    ) -> None:
        held = 0 if attempt == 0 else 2
        if extended:
            boundary("MB", frame, attempt, pre_tick)
        add(
            "MS",
            frame=frame,
            attempt=attempt,
            tick=pre_tick,
            input=held,
            d0=held,
            checkpoint_count=pre_count,
        )
        add(
            "ME",
            frame=frame,
            attempt=attempt,
            tick=pre_tick + 1,
            result=result,
            checkpoint_count=post_count,
        )

    def renderer(
        frame: int,
        attempt: int,
        tick: int,
        checkpoint: int,
        prior_attempt: int,
        prior_checkpoint: int,
        result: int,
        pending: int,
        reset_phase: int,
        token: int,
    ) -> None:
        if extended:
            boundary("RM", frame, attempt, tick, 0x2500, token)
        add(
            "RG",
            frame=frame,
            attempt=attempt,
            tick=tick,
            sr=0x2700,
            token=token,
            pending=0,
            published_depth=0,
            build_depth=0,
            campaign_flags=1,
            checkpoint_count=checkpoint,
            renderer_attempt=prior_attempt,
            renderer_checkpoint_count=prior_checkpoint,
            backlog_frames=0,
        )
        if extended and result != 4:
            for phase in ("HB", "HE", "SE", "OE"):
                boundary(phase, frame, attempt, tick, token=token)
        add(
            "RR",
            frame=frame,
            attempt=attempt,
            tick=tick,
            sr=0x2700,
            token=token,
            pending=pending,
            result=result,
            checkpoint_count=checkpoint,
            renderer_attempt=attempt,
            renderer_checkpoint_count=checkpoint,
            reset_phase=reset_phase,
            backlog_frames=0,
        )
        if extended:
            for base, size in reset.BUFFER_RANGES:
                for address in range(base, base + size, 16):
                    add("BT", frame=frame, attempt=attempt, tick=tick, address=address, long0=0, long1=0, long2=0, long3=0)
            boundary("RX", frame, attempt, tick, 0x2500, token)
            boundary("MD", frame, attempt, tick, 0x2500, token)
            if result != 4 and tick != 4:
                boundary("WW", frame, attempt, tick)

    def projection(
        frame: int, attempt: int, tick: int, before: int, crc: int
    ) -> None:
        after = before + 1
        add(
            "PS",
            frame=frame,
            attempt=attempt,
            tick=tick,
            checkpoint_count=before,
            engine_state=2 if attempt == 0 else 1,
        )
        add(
            "ES",
            frame=frame,
            attempt=attempt,
            tick=tick,
            checkpoint_count=before,
        )
        add(
            "EE",
            frame=frame,
            attempt=attempt,
            tick=tick,
            length=0x0E36,
            crc=0x10203040 + after,
            checkpoint_count=after,
        )
        add(
            "TS",
            frame=frame,
            attempt=attempt,
            tick=tick,
            destination=0x00FF6E36,
        )
        add(
            "TE",
            frame=frame,
            attempt=attempt,
            tick=tick,
            destination=0x00FF6F36,
            length=0x100,
        )
        add(
            "CS",
            frame=frame,
            attempt=attempt,
            tick=tick,
            source=0x00FF6E36,
            length=0x100,
        )
        add(
            "CE",
            frame=frame,
            attempt=attempt,
            tick=tick,
            raw_crc=(~crc) & 0xFFFFFFFF,
        )
        add(
            "PE",
            frame=frame,
            attempt=attempt,
            tick=tick,
            length=0x0F36,
            crc=crc,
            checkpoint_count=after,
            state_crc=crc,
        )

    vblank(719)
    gameplay(719, 0, 718, 0, 12, 12)
    renderer(719, 0, 719, 12, 0, 12, 0, 1, 0, 0)
    vblank(720)
    if extended:
        boundary("MB", 720, 0, 719)
    add(
        "MS",
        frame=720,
        attempt=0,
        tick=719,
        input=0,
        d0=0,
        checkpoint_count=12,
    )
    add("HI", frame=720, attempt=0, tick=719, current=0, desired=2)
    projection(720, 0, 720, 12, 0x12345678)
    add(
        "ME",
        frame=720,
        attempt=0,
        tick=720,
        result=8,
        checkpoint_count=13,
    )
    vblank(721)
    vblank(722)
    projection(722, 1, 0, 0, reset.EXPECTED_RESET_PROJECTION_CRC)
    add(
        "MR",
        frame=722,
        attempt=1,
        tick=0,
        result=0,
        checkpoint_count=1,
        state_crc=reset.EXPECTED_RESET_PROJECTION_CRC,
        replay_crc=reset.EXPECTED_ATTEMPT_0_REPLAY_CRC,
    )
    renderer(722, 1, 0, 1, 0, 12, 4, 0, 12, 2)
    add(
        "BC",
        frame=722,
        attempt=1,
        tick=0,
        snapshot_bank=6,
        current_bank=0,
        token=2,
        sr=0x2500,
    )
    add("BM", frame=722, attempt=1, tick=0, token=2, sr=0x2700)
    add("BZ", frame=722, attempt=1, tick=0, token=0, sr=0x2704)
    if extended:
        boundary("WW", 722, 1, 0, 0x2704)
        cycle -= 7
    add("BW", frame=722, attempt=1, tick=0, token=0, sr=0x2704)
    vblank(723)
    gameplay(723, 1, 0, 0, 1, 1)
    renderer(723, 1, 1, 1, 1, 1, 0, 1, 11, 0)
    vblank(724)
    if extended:
        for frame, tick in ((724, 2), (725, 3), (726, 4)):
            gameplay(frame, 1, tick - 1, 0, 1, 1)
            renderer(frame, 1, tick, 1, 1, 1, 0, 1, 12 - tick, 0)
            if frame < 726:
                vblank(frame + 1)
    return records


class ResetPhaseDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.symbols = parse_vasm_symbols(SYMBOLS)
        cls.script = reset.build_command_script(cls.symbols)

    def test_pins_pc_and_read_only_script_are_exact(self) -> None:
        self.assertEqual(
            reset.PINNED_ROM_SHA256,
            "140039e336ca8e1bc1a6042025d41a4190596c244e6a4cce98f1a3a325e7265e",
        )
        self.assertEqual(
            reset.PINNED_SYMBOL_SHA256,
            "b1010589d79ac0594d146001833185f1f4747a480e3929c80b71f9c6dd5fa8b0",
        )
        self.assertEqual(reset.PINNED_ROM_SHA256, regular.PINNED_ROM_SHA256)
        self.assertEqual(
            reset.PINNED_SYMBOL_SHA256, regular.PINNED_SYMBOL_SHA256
        )
        self.assertEqual(len(reset.PC), 28)
        reset.pc_contract(ROM.read_bytes(), self.symbols)
        capture = json.loads(
            (ROOT / reset.CAPTURE_REPORT_RELATIVE_PATH).read_text("utf-8")
        )
        self.assertEqual(
            capture["provenance"]["rom"]["sha256"], reset.CAPTURE_ROM_SHA256
        )
        self.assertEqual(
            capture["provenance"]["symbols"]["sha256"],
            reset.CAPTURE_SYMBOL_SHA256,
        )
        self.assertEqual(capture["pc_contract"]["addresses"], reset.CAPTURE_PC)
        self.assertNotEqual(
            reset.DEFAULT_REPORT_RELATIVE_PATH,
            reset.CAPTURE_REPORT_RELATIVE_PATH,
        )
        pc_digest = hashlib.sha256(
            json.dumps(
                capture["pc_contract"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(pc_digest, reset.CAPTURE_PC_CONTRACT_SHA256)
        self.assertFalse(
            reset.offline_capture_command_policy_errors(
                capture["command_script"]
            )
        )
        lowered = f" {capture['command_script'].lower()} "
        for token in (" set ", " write ", " x ", " display "):
            self.assertNotIn(token, lowered)

    def test_generated_blastem_blocks_are_balanced(self) -> None:
        lines = self.script.splitlines()
        starts = [i for i, line in enumerate(lines) if line.startswith("commands ")]
        self.assertEqual(len(starts), len(reset.PC))
        for ordinal, start in enumerate(starts):
            stop = starts[ordinal + 1] if ordinal + 1 < len(starts) else len(lines) - 1
            depth = 1
            for line in lines[start + 1 : stop]:
                if line.startswith("if "):
                    depth += 1
                elif line == "end":
                    depth -= 1
                self.assertGreaterEqual(depth, 0)
            self.assertEqual(depth, 0)

    def test_exact_clean_reset_fixture_passes(self) -> None:
        analysis = reset.analyse(exact_records())
        self.assertEqual(analysis["status"], "pass", analysis["errors"])
        self.assertFalse(
            analysis["fault_observation"]["caught_up_tick2_without_vblank"]
        )

    def test_uncleared_reset_tokens_fail_closed(self) -> None:
        records = exact_records()
        cleared = next(
            item
            for item in records
            if item["phase"] == "BZ"
        )
        wait = next(item for item in records if item["phase"] == "BW")
        fresh_entry = next(
            item
            for item in records
            if item["phase"] == "VE" and item["frame"] == 722
        )
        fresh_exit = next(
            item
            for item in records
            if item["phase"] == "VX" and item["frame"] == 723
        )
        cleared["token"] = 1
        wait["token"] = 1
        fresh_entry["token"] = 1
        fresh_exit["token"] = 2
        analysis = reset.analyse(records)
        self.assertEqual(analysis["status"], "fail")
        self.assertTrue(
            any("coalesce" in error or "token" in error for error in analysis["errors"])
        )

    def test_projection_crc_and_count_mutations_fail(self) -> None:
        records = exact_records()
        reset_pe = [item for item in records if item["phase"] == "PE"][1]
        reset_pe["crc"] ^= 1
        reset_pe["checkpoint_count"] = 2
        analysis = reset.analyse(records)
        self.assertEqual(analysis["status"], "fail")
        self.assertTrue(any("projection 1" in error for error in analysis["errors"]))

    def test_zero_dma_and_recovery_queue_mutations_fail(self) -> None:
        records = exact_records()
        zero = next(
            item for item in records if item["phase"] == "VP" and item["frame"] == 721
        )
        recovery = next(
            item for item in records if item["phase"] == "VC" and item["frame"] == 724
        )
        zero["debug_words"] = 1
        recovery["build_words"] = 440
        analysis = reset.analyse(records)
        self.assertEqual(analysis["status"], "fail")
        self.assertTrue(any("frame 721" in error for error in analysis["errors"]))
        self.assertTrue(any("frame 724" in error for error in analysis["errors"]))

    def test_offline_reanalysis_binds_capture_and_rejects_mutation(self) -> None:
        capture_report = ROOT / reset.CAPTURE_REPORT_RELATIVE_PATH
        capture_trace = ROOT / reset.CAPTURE_TRACE_RELATIVE_PATH
        report = reset.derive_offline_reanalysis(
            ROOT,
            capture_report,
            capture_trace,
            completed_utc="2026-08-31T00:00:00+00:00",
        )
        self.assertEqual(report["status"], "diagnostic_pass")
        self.assertEqual(
            report["derivation"], "offline_reanalysis_no_emulator_rerun"
        )
        self.assertEqual(report["analysis"]["status"], "pass")
        self.assertEqual(report["analysis"]["errors"], [])
        self.assertEqual(report["source_capture"]["console_sha256"], reset.CAPTURE_CONSOLE_SHA256)
        self.assertEqual(report["source_capture"]["records_sha256"], reset.CAPTURE_RECORDS_SHA256)
        self.assertEqual(
            report["read_only_contract"]["emulator_processes_started"], 0
        )
        self.assertEqual(reset.offline_reanalysis_errors(report, ROOT), [])

        mutations = []
        wrong_derivation = copy.deepcopy(report)
        wrong_derivation["derivation"] = "emulator_rerun"
        mutations.append(wrong_derivation)
        wrong_console = copy.deepcopy(report)
        wrong_console["source_capture"]["console_sha256"] = "0" * 64
        mutations.append(wrong_console)
        wrong_fact = copy.deepcopy(report)
        wrong_fact["analysis"]["fault_observation"]["reset_hold_token"] = 1
        mutations.append(wrong_fact)
        missing_check = copy.deepcopy(report)
        missing_check["checks"].pop("capture_pc_contract_exact")
        mutations.append(missing_check)
        wrong_rom = copy.deepcopy(report)
        wrong_rom["provenance"]["captured_b03_rom"]["sha256"] = "f" * 64
        mutations.append(wrong_rom)
        wrong_correction = copy.deepcopy(report)
        wrong_correction["corrections"]["bank_epoch"][
            "observed_canonical_transition"
        ] = "physical bank 0 -> 1"
        mutations.append(wrong_correction)
        for mutated in mutations:
            self.assertTrue(reset.offline_reanalysis_errors(mutated, ROOT))
        collision = argparse.Namespace(
            root=str(ROOT),
            capture_report=str(capture_report),
            capture_trace=str(capture_trace),
            offline_output=str(capture_report),
        )
        with self.assertRaises(reset.EvidenceFailure):
            reset.write_offline_reanalysis(collision)

    def test_parser_console_and_write_policy_reject_adversarial_input(self) -> None:
        line = "R94R VX 1 2 1 2 4 0 1 1e0"
        self.assertEqual(reset.parse_markers(line)[0]["debug_words"], 0x1E0)
        self.assertTrue(
            reset.console_contract_errors("R94R FORGED 1\n", self.script)
        )
        self.assertTrue(
            reset.command_policy_errors(self.script + "set d0 0\n", self.symbols)
        )

    def test_extended_script_pc_bounds_and_legacy_isolation(self) -> None:
        script = reset.build_command_script(self.symbols, extended_overlay=True)
        self.assertEqual(len([line for line in script.splitlines() if line.startswith("breakpoint ")]), 36)
        self.assertEqual(reset.command_policy_errors(script, self.symbols, extended_overlay=True), [])
        self.assertTrue(reset.command_policy_errors(script, self.symbols))
        reset.extended_pc_contract(ROM.read_bytes(), self.symbols)
        bad_rom = bytearray(ROM.read_bytes())
        bad_rom[reset.EXTENDED_PC["sat_end"]] ^= 1
        with self.assertRaises(reset.EvidenceFailure):
            reset.extended_pc_contract(bytes(bad_rom), self.symbols)
        for phase in reset.EXTENDED_MARKER_FIELDS:
            self.assertIn(f"R94R {phase} ", script)
            self.assertNotIn(f"R94R {phase} ", self.script)
        self.assertIn(f"if [${self.symbols['DEBUG_FRAME_COUNTER']:X}].l < $2DA", script)
        line = "R94R BT 2d4 1 2 ff8000 12345678 0 abcdef00 ffffffff"
        self.assertEqual(reset.parse_markers(line, extended_overlay=True)[0]["long3"], 0xFFFFFFFF)
        with self.assertRaises(reset.EvidenceFailure):
            reset.parse_markers(line)
        depth = 0
        for line in script.splitlines():
            if line.startswith(("commands ", "if ")):
                depth += 1
            elif line == "end":
                depth -= 1
            self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)

    def test_extended_clean_protocol_and_late_budget_are_distinct(self) -> None:
        clean = reset.analyse_extended(exact_records(extended=True))
        self.assertEqual(clean["protocol_status"], "pass", clean["errors"])
        self.assertEqual(clean["budget_status"], "pass", clean["budget_errors"])
        self.assertEqual(len(clean["renderer_buffers"]), 6)
        self.assertEqual(len(clean["phase_spans"]), 6)
        late = reset.analyse_extended(exact_records(extended=True, late=True))
        self.assertEqual(late["protocol_status"], "pass", late["errors"])
        self.assertEqual(late["budget_status"], "fail")
        self.assertIn("frame 725 VBlank entered outside blank", late["budget_errors"])

    def test_extended_queue_input_buffer_and_mask_mutations_fail_closed(self) -> None:
        for phase, field, value, frame in (("VC", "build_words", 480, 725), ("Q", "source", 0xFF8740, 725), ("MS", "input", 0, 725), ("BT", "address", 0xFF8004, 724), ("SE", "sr", 0x2500, 724)):
            with self.subTest(phase=phase, field=field):
                records = exact_records(extended=True)
                item = next(item for item in records if item["phase"] == phase and item["frame"] == frame)
                item[field] = value
                self.assertEqual(reset.analyse_extended(records)["protocol_status"], "fail")

    def test_preserved_live_baseline_reanalysis_exposes_late_irq_without_waiver(self) -> None:
        report = reset.derive_extended_baseline(ROOT)
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["cycle_blank_budget_status"], "fail")
        self.assertEqual(report["status"], "diagnostic_fail")
        self.assertEqual(report["analysis"]["errors"], [])
        self.assertEqual(report["source_capture"]["report"]["sha256"], reset.EXTENDED_BASELINE_REPORT_SHA256)
        self.assertEqual(report["read_only_contract"]["emulator_processes_started"], 0)
        timings = {item["frame"]: item for item in report["analysis"]["irq_phase_inference"]}
        self.assertEqual(timings[725]["actual_lateness_master_clocks"], 127841)
        self.assertEqual(timings[726]["actual_lateness_master_clocks"], 408013)
        self.assertTrue(timings[725]["overlapping_renderer_masks"][0]["nominal_inside_mask"])
        self.assertEqual(report["analysis"]["phase_spans"]["722"]["sat"]["delta_cpu_cycles_ceil"], 55148)
        self.assertIn("727", report["analysis"]["queue_by_frame"])
        self.assertEqual(len(report["analysis"]["renderer_buffers"]), 6)

    def test_lookup_profile_is_separate_and_exact(self) -> None:
        symbols = parse_vasm_symbols(LOOKUP_BUILD / "recursion94-debug.sym")
        rom = (LOOKUP_BUILD / "recursion94-debug.bin").read_bytes()
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.LOOKUP_ROM_SHA256)
        contract = reset.extended_pc_contract(rom, symbols, profile="lookup")
        self.assertEqual(contract["addresses"]["renderer_start"], 0x8C26)
        self.assertEqual(contract["extended_addresses"]["sat_end"], 0x8CE6)
        script = reset.build_command_script(symbols, extended_overlay=True, profile="lookup")
        self.assertEqual(reset.command_policy_errors(script, symbols, extended_overlay=True, profile="lookup"), [])
        baseline_script = reset.build_command_script(self.symbols, extended_overlay=True)
        self.assertEqual([line for line in script.splitlines() if not line.startswith("breakpoint ")], [line for line in baseline_script.splitlines() if not line.startswith("breakpoint ")])
        with self.assertRaises(reset.EvidenceFailure):
            reset.extended_pc_contract(rom, symbols)
        with self.assertRaises(reset.EvidenceFailure):
            reset.extended_pc_contract(ROM.read_bytes(), self.symbols, profile="lookup")

    def test_lookup_raw_comparison_preserves_parity_but_fails_budgets(self) -> None:
        report = reset.derive_lookup_comparison(ROOT)
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["parity_status"], "pass")
        self.assertEqual(report["cycle_blank_budget_status"], "fail")
        self.assertEqual(report["tick60_simulation_budget"]["saved_cpu_cycles"], 6096)
        self.assertEqual(report["tick60_simulation_budget"]["remaining_excess_cpu_cycles"], 18129)
        self.assertTrue(all(report["parity_checks"].values()))
        self.assertEqual(report["analyses"]["lookup_reset"]["protocol_status"], "pass")
        self.assertIn("frame 725 VBlank exited outside blank", report["analyses"]["lookup_reset"]["budget_errors"])
        with patch.object(reset, "sha256_file", return_value="0" * 64):
            with self.assertRaises(reset.EvidenceFailure):
                reset.derive_lookup_comparison(ROOT)

    def test_sprite_profile_exact_archived_windows_and_unchanged_marker_program(self) -> None:
        archive = ROOT / "artifacts/archive/m5-sprite-loop-09be6696-20261010/level-B03"
        rom = (archive / "recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.SPRITE_ROM_SHA256)
        self.assertEqual(hashlib.sha256((archive / "recursion94-debug.sym").read_bytes()).hexdigest(), reset.SPRITE_SYMBOL_SHA256)
        reset.extended_pc_contract(rom, symbols, profile="sprite")
        regular.repair_pc_contract(rom, symbols, profile="sprite")
        for helper, policy in ((reset.build_command_script, reset.command_policy_errors),):
            script = helper(symbols, extended_overlay=True, profile="sprite")
            self.assertEqual(policy(script, symbols, extended_overlay=True, profile="sprite"), [])
            baseline = helper(self.symbols, extended_overlay=True)
            self.assertEqual([line for line in script.splitlines() if not line.startswith("breakpoint ")], [line for line in baseline.splitlines() if not line.startswith("breakpoint ")])
        regular_script = regular.build_repair_command_script(symbols, profile="sprite")
        self.assertEqual(regular.repair_command_policy_errors(regular_script, symbols, profile="sprite"), [])
        with self.assertRaises(reset.EvidenceFailure):
            reset.extended_pc_contract(rom, symbols, profile="lookup")

    def test_sprite_isolated_raw_comparison_records_savings_and_remaining_failure(self) -> None:
        report = reset.derive_lookup_comparison(ROOT, candidate_profile="sprite")
        self.assertEqual(report["comparison_profiles"], {"before": "lookup", "after": "sprite"})
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["parity_status"], "pass")
        self.assertEqual(report["cycle_blank_budget_status"], "fail")
        self.assertTrue(all(report["parity_checks"].values()))
        before = report["analyses"]["lookup_reset"]["phase_spans"]["722"]["sat"]["delta_cpu_cycles_ceil"]
        after = report["analyses"]["sprite_reset"]["phase_spans"]["722"]["sat"]["delta_cpu_cycles_ceil"]
        self.assertEqual(before - after, 5072)
        self.assertEqual(report["tick60_simulation_budget"]["saved_cpu_cycles"], 0)

    def test_indexed_crc_profile_binds_exact_loop_and_unchanged_markers(self) -> None:
        archive = ROOT / "artifacts/archive/m5-crc-index-c051995c-20261010/level-B03"
        rom = (archive / "recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.INDEXED_ROM_SHA256)
        self.assertEqual(hashlib.sha256((archive / "recursion94-debug.sym").read_bytes()).hexdigest(), reset.INDEXED_SYMBOL_SHA256)
        for helper in (reset.extended_pc_contract, regular.repair_pc_contract):
            contract = helper(rom, symbols, profile="indexed")
            self.assertIn("crc_indexed_base_loop", contract["exact_windows"])
            bad = bytearray(rom)
            bad[0x1C3C] ^= 1
            with self.assertRaises(reset.EvidenceFailure):
                helper(bytes(bad), symbols, profile="indexed")
        self.assertEqual(reset.build_command_script(symbols, extended_overlay=True, profile="indexed"), reset.build_command_script(symbols, extended_overlay=True, profile="sprite"))
        self.assertEqual(regular.build_repair_command_script(symbols, profile="indexed"), regular.build_repair_command_script(symbols, profile="sprite"))

    def test_indexed_crc_comparison_preserves_raw_parity_and_remaining_failures(self) -> None:
        report = reset.derive_lookup_comparison(ROOT, candidate_profile="indexed")
        self.assertEqual(report["comparison_profiles"], {"before": "sprite", "after": "indexed"})
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["parity_status"], "pass")
        self.assertTrue(all(report["parity_checks"].values()))
        self.assertEqual(report["cycle_blank_budget_status"], "fail")
        self.assertEqual(report["tick60_simulation_budget"]["saved_cpu_cycles"], 4638)
        self.assertEqual(report["tick60_simulation_budget"]["remaining_excess_cpu_cycles"], 13491)
        self.assertEqual(report["analyses"]["indexed_regular"]["phase_spans"]["campaign_raw_tail_crc"]["delta_cpu_cycles_ceil"], 12374)
        self.assertIn("frame 726 VBlank exited outside blank", report["analyses"]["indexed_reset"]["budget_errors"])

    def test_pool_profile_binds_relocated_anchors_and_exact_empty_branch(self) -> None:
        archive = ROOT / "artifacts/archive/m5-empty-pool-25a74bd2-20261010/level-B03"
        rom = (archive / "recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.POOL_ROM_SHA256)
        self.assertEqual(hashlib.sha256((archive / "recursion94-debug.sym").read_bytes()).hexdigest(), reset.POOL_SYMBOL_SHA256)
        for helper in (reset.extended_pc_contract, regular.repair_pc_contract):
            contract = helper(rom, symbols, profile="pool")
            self.assertEqual(contract["addresses"]["tail_start"], 0x5636)
            for address in (0x1C2F, 0x24DC, 0x24F2, 0x2516, 0x2522):
                bad = bytearray(rom)
                bad[address] ^= 1
                with self.assertRaises(reset.EvidenceFailure):
                    helper(bytes(bad), symbols, profile="pool")
        indexed_archive = ROOT / "artifacts/archive/m5-crc-index-c051995c-20261010/level-B03"
        indexed_symbols = parse_vasm_symbols(indexed_archive / "recursion94-debug.sym")
        for builder, policy, kwargs in ((reset.build_command_script, reset.command_policy_errors, {"extended_overlay": True}), (regular.build_repair_command_script, regular.repair_command_policy_errors, {})):
            script = builder(symbols, profile="pool", **kwargs)
            old = builder(indexed_symbols, profile="indexed", **kwargs)
            self.assertEqual(policy(script, symbols, profile="pool", **kwargs), [])
            strip_breakpoints = lambda text: [line for line in text.splitlines() if not line.startswith("breakpoint ")]
            self.assertEqual(strip_breakpoints(script), strip_breakpoints(old))

    def test_native_failure_retains_full_console_without_passing_or_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "failed.json"
            trace_path = Path(directory) / "failed.log"
            console = "early line\n" * 80 + "R94R READY\n>  y\n"
            payload = {"error": "READY timeout", "console_text": console, "full_console_available": True}
            reset.preserve_native_failure(report_path, trace_path, script="continue\n", helper_payload=payload, metadata={"timeout_seconds": 240})
            report = json.loads(report_path.read_text("utf-8"))
            self.assertEqual(report["status"], "diagnostic_fail")
            self.assertFalse(report["timing_evidence_complete"])
            self.assertEqual(report["failure"]["console_text"], console)
            self.assertIn(console, trace_path.read_text("utf-8"))
            original = report_path.read_bytes()
            with self.assertRaises(reset.EvidenceFailure):
                reset.preserve_native_failure(report_path, trace_path, script="continue\n", helper_payload=payload, metadata={})
            self.assertEqual(report_path.read_bytes(), original)

    def test_pool_comparison_binds_raw_parity_savings_and_unwaived_budget(self) -> None:
        report = reset.derive_lookup_comparison(ROOT, candidate_profile="pool")
        self.assertEqual(report["comparison_profiles"], {"before": "indexed", "after": "pool"})
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["parity_status"], "pass")
        self.assertTrue(all(report["parity_checks"].values()))
        self.assertEqual(report["status"], "diagnostic_fail")
        self.assertEqual(report["cycle_blank_budget_status"], "fail")
        self.assertEqual(report["tick60_simulation_budget"]["saved_cpu_cycles"], 6782)
        self.assertEqual(report["tick60_simulation_budget"]["remaining_excess_cpu_cycles"], 6709)
        self.assertEqual(report["analyses"]["pool_regular"]["phase_spans"]["engine_projection"]["delta_cpu_cycles_ceil"], 37980)
        self.assertEqual(report["analyses"]["pool_reset"]["phase_spans"]["720"]["main_token_to_caller_return"]["delta_cpu_cycles_ceil"], 258376)
        self.assertIn("frame 726 VBlank exited outside blank", report["analyses"]["pool_reset"]["budget_errors"])

    def test_prefix_preparation_binds_canonical_bytes_and_next_mask_boundaries(self) -> None:
        contract = reset.prefix_route_contract(ROOT)
        schedule = reset.prefix_schedule()
        self.assertEqual(len(schedule), 1681)
        self.assertEqual(contract["schedule_sha256"], "0215ce39982f901b2c29ea4a32405fa568aecb4227958f9a19d6e1093f8cabff")
        stages = reset.prefix_input_stages()
        self.assertEqual(len(stages), 21)
        self.assertEqual([(item["total"], item["pre_tick"]) for item in stages[:3]], [(46, 45), (67, 66), (720, 719)])
        for stage in stages:
            index = stage["total"] - 1
            self.assertEqual((stage["attempt"], stage["pre_tick"]), divmod(index, 720))
            self.assertEqual((stage["current"], stage["desired"]), (schedule[index], schedule[index + 1]))
        self.assertEqual(reset.PREFIX_PHYSICAL_FAIL_STOP, 2193)
        self.assertEqual(len(reset.PREFIX_SAMPLE_POST_TICKS), 26)
        with patch.object(reset.b03, "ATTEMPT_2_INPUTS", bytes(292)):
            with self.assertRaises(reset.EvidenceFailure):
                reset.prefix_schedule()

    def test_highwater_profile_binds_actual_anchors_without_changing_marker_program(self) -> None:
        archive = ROOT / "artifacts/archive/m5-high-water-96dec577-20261010/level-B03"
        rom = (archive / "recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.HIGH_WATER_ROM_SHA256)
        self.assertEqual(hashlib.sha256((archive / "recursion94-debug.sym").read_bytes()).hexdigest(), reset.HIGH_WATER_SYMBOL_SHA256)
        for helper in (reset.extended_pc_contract, regular.repair_pc_contract):
            contract = helper(rom, symbols, profile="highwater")
            self.assertEqual(contract["addresses"]["engine_projection_start"], 0x2162)
            self.assertEqual(contract["addresses"]["tail_start"], 0x56D2)
            for address in (0x1C2E, 0x24F4, 0x252E, 0x6B64, 0x8EF2):
                bad = bytearray(rom)
                bad[address] ^= 1
                with self.assertRaises(reset.EvidenceFailure):
                    helper(bytes(bad), symbols, profile="highwater")
        old = parse_vasm_symbols(ROOT / "artifacts/archive/m5-empty-pool-25a74bd2-20261010/level-B03/recursion94-debug.sym")
        for builder, kwargs in ((reset.build_command_script, {"extended_overlay": True}), (regular.build_repair_command_script, {})):
            strip_breakpoints = lambda text: [line for line in text.splitlines() if not line.startswith("breakpoint ")]
            self.assertEqual(strip_breakpoints(builder(symbols, profile="highwater", **kwargs)), strip_breakpoints(builder(old, profile="pool", **kwargs)))

    def test_highwater_comparison_admits_only_the_sampled_cycle_blank_pass(self) -> None:
        report = reset.derive_lookup_comparison(ROOT, candidate_profile="highwater")
        self.assertEqual(report["comparison_profiles"], {"before": "pool", "after": "highwater"})
        self.assertEqual(report["status"], "diagnostic_pass")
        self.assertFalse(report["canonical"])
        self.assertTrue(report["diagnostic_only"])
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["parity_status"], "pass")
        self.assertTrue(all(report["parity_checks"].values()))
        self.assertEqual(report["cycle_blank_budget_status"], "pass")
        self.assertEqual(report["tick60_simulation_budget"]["after_cpu_cycles"], 80828)
        self.assertEqual(report["tick60_simulation_budget"]["saved_cpu_cycles"], 9084)
        self.assertEqual(report["tick60_simulation_budget"]["remaining_excess_cpu_cycles"], 0)
        self.assertEqual(report["analyses"]["highwater_reset"]["phase_spans"]["722"]["gameplay"]["delta_cpu_cycles_ceil"], 34070)
        self.assertEqual(report["analyses"]["highwater_reset"]["budget_errors"], [])

    def test_renderer_hwm_profile_binds_exact_calls_and_preserves_all_marker_programs(self) -> None:
        archive = ROOT / "artifacts/archive/m5-renderer-hwm-e6778f90-20261010/level-B03"
        rom = (archive / "recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.PROFILE_ROM_PINS["renderer_hwm"])
        self.assertEqual(hashlib.sha256((archive / "recursion94-debug.sym").read_bytes()).hexdigest(), reset.PROFILE_SYMBOL_PINS["renderer_hwm"])
        contract = reset.extended_pc_contract(rom, symbols, profile="renderer_hwm")
        self.assertEqual(contract["addresses"], {**reset.PROFILE_PC["highwater"], "renderer_start": 0x8DA4, "renderer_end": 0x8F82})
        self.assertEqual(contract["extended_addresses"], {
            "main_begin": 0x2D6, "main_done": 0x344, "renderer_mask": 0x8DA0,
            "renderer_unmasked": 0x8F84, "hud_begin": 0x8E5C, "hud_end": 0x8E60,
            "sat_end": 0x8E64, "overlay_end": 0x8E68})
        for address in (0x1C2E, 0x3A12, 0x3DB6, 0x6B64, 0x8DA0, 0x8E5C, 0x8E60, 0x8E64, 0x8E68, 0x8F82):
            changed = bytearray(rom)
            changed[address] ^= 1
            with self.subTest(address=address), self.assertRaises(reset.EvidenceFailure):
                reset.extended_pc_contract(bytes(changed), symbols, profile="renderer_hwm")
        with self.assertRaises(reset.EvidenceFailure):
            reset.extended_pc_contract(rom, symbols, profile="highwater")
        old = parse_vasm_symbols(ROOT / "artifacts/archive/m5-high-water-96dec577-20261010/level-B03/recursion94-debug.sym")
        for builder, kwargs in ((reset.build_command_script, {"extended_overlay": True}),
                                (regular.build_repair_command_script, {}), (reset.build_prefix_command_script, {})):
            strip_breakpoints = lambda text: [line for line in text.splitlines() if not line.startswith("breakpoint ")]
            self.assertEqual(strip_breakpoints(builder(symbols, profile="renderer_hwm", **kwargs)),
                             strip_breakpoints(builder(old, profile="highwater", **kwargs)))
        self.assertEqual(reset.command_policy_errors(reset.build_command_script(symbols, extended_overlay=True, profile="renderer_hwm"), symbols, extended_overlay=True, profile="renderer_hwm"), [])
        self.assertEqual(reset.prefix_command_policy_errors(reset.build_prefix_command_script(symbols, profile="renderer_hwm"), symbols, profile="renderer_hwm"), [])
        self.assertEqual(reset._parser().parse_args(["--profile", "renderer_hwm"]).__dict__["profile"], "renderer_hwm")
        source = inspect.getsource(reset.collect)
        self.assertLess(source.index("regular.renderer_hwm_source_contract(root)"), source.index("subprocess.Popen("))
        # No dependency on the new mutable runtime sources enters the pinned
        # highwater offline derivation or changes its timing failures.
        with patch.object(regular, "renderer_hwm_source_contract", side_effect=AssertionError("offline must use archive only")):
            report = reset.derive_prefix_reanalysis(ROOT)
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(len(report["analysis"]["budget_errors"]), 312)

    def test_renderer_hwm_comparison_binds_6240_bytes_and_only_first_attempt_budget(self) -> None:
        with patch.object(regular, "renderer_hwm_source_contract", side_effect=AssertionError("offline must use archive only")), \
                patch.object(reset.subprocess, "Popen", side_effect=AssertionError("offline must never launch")):
            report = reset.derive_lookup_comparison(ROOT, candidate_profile="renderer_hwm")
        self.assertEqual(report["schema"], "r94-m5-b03-renderer-hwm-timing-parity-v1")
        self.assertEqual(report["comparison_profiles"], {"before": "highwater", "after": "renderer_hwm"})
        self.assertEqual(report["status"], "diagnostic_pass")
        self.assertFalse(report["canonical"])
        self.assertTrue(report["diagnostic_only"])
        self.assertEqual((report["diagnostic_protocol_status"], report["parity_status"], report["cycle_blank_budget_status"]), ("pass", "pass", "pass"))
        self.assertTrue(all(report["parity_checks"].values()))
        self.assertEqual(report["cycle_blank_budget_scope"], {
            "regular_attempt": 0, "regular_ticks": [59, 60, 61],
            "reset_total_steps": [719, 720, 721, 722, 723, 724],
            "later_attempt_timing_certified": False, "full_route_timing_certified": False})
        self.assertIn("not a later-attempt or full-route timing pass", report["conclusion"])
        expected = {
            "highwater_reset": ("04011af7c1f75c4c518bbf169a560ec8368d589ff440199dc258393d6a808c77", "4f47773376fb623b6f525648b9974980b4541b9c69c1add023360ecbe568ea3b"),
            "highwater_regular": ("c852a44a1bc768cd22392381e1644b8916b3467e6922e5fb0d9aa250bda96462", "2243c934a1f5554e729afb2325f3ee3b4f1d9a7b7cec7a05d8e8413dc356b15c"),
            "renderer_hwm_reset": ("b1ffcd6d09667845a27b367b33be87df45bab6b11c7539b4d495080c01c67357", "feac204cabf748233953b55840830b964f87c741213caad127293f8f43024bac"),
            "renderer_hwm_regular": ("6fc3cebc759f71ff8e9298177a8b6817f83fd41e8a05d461b85fc67fe50cad47", "e2b0152acbbe6d4b841380e507ca748ed5a66841338b9190be54e8c61baab453"),
        }
        self.assertEqual({key: (value["report"]["sha256"], value["trace"]["sha256"]) for key, value in report["sources"].items()}, expected)
        samples = report["renderer_byte_comparisons"]
        self.assertEqual(len(samples), 6)
        self.assertEqual(sum(region["bytes"] for sample in samples for region in sample["regions"]), 6240)
        self.assertTrue(all(sample["before_frame"] == sample["after_frame"] for sample in samples))
        self.assertEqual(report["tick60_simulation_budget"]["after_cpu_cycles"], 80828)
        self.assertEqual(report["tick60_simulation_budget"]["saved_cpu_cycles"], 0)
        analyses = report["analyses"]
        for tick, saving in ((59, 6460), (61, 6462)):
            field = f"renderer_tick{tick}"
            self.assertEqual(analyses["highwater_regular"]["phase_spans"][field]["delta_cpu_cycles_ceil"] - analyses["renderer_hwm_regular"]["phase_spans"][field]["delta_cpu_cycles_ceil"], saving)
        after_reset = analyses["renderer_hwm_reset"]
        self.assertEqual(after_reset["phase_spans"]["720"]["main_token_to_caller_return"]["delta_cpu_cycles_ceil"], 250444)
        self.assertEqual(after_reset["phase_spans"]["722"]["main_token_to_caller_return"]["delta_cpu_cycles_ceil"], 102138)
        self.assertEqual(after_reset["phase_spans"]["722"]["renderer_mask_to_restore"]["delta_cpu_cycles_ceil"], 66118)
        self.assertEqual(after_reset["budget_errors"], [])
        native_sha256 = reset.sha256_file
        with patch.object(reset, "sha256_file", side_effect=lambda path: "0" * 64 if path.name == "reset-overlay-renderer-hwm-e6778f90-total719-total724-20261010.log" else native_sha256(path)):
            with self.assertRaisesRegex(reset.EvidenceFailure, "immutable source drift"):
                reset.derive_lookup_comparison(ROOT, candidate_profile="renderer_hwm")

    def test_crc_projection_reset_and_prefix_profiles_preserve_marker_semantics_and_bounds(self) -> None:
        archive = ROOT / "artifacts/archive/m5-crc-projection-8b69d4bf-20261010/level-B03"
        rom = (archive / "recursion94-debug.bin").read_bytes()
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        self.assertEqual(hashlib.sha256(rom).hexdigest(), reset.PROFILE_ROM_PINS["crc_projection"])
        self.assertEqual(hashlib.sha256((archive / "recursion94-debug.sym").read_bytes()).hexdigest(), reset.PROFILE_SYMBOL_PINS["crc_projection"])
        contract = reset.extended_pc_contract(rom, symbols, profile="crc_projection")
        self.assertEqual(contract["addresses"], {
            **reset.PROFILE_PC["renderer_hwm"], "timeout_projection_call": 0x3DFC,
            "reset_projection_call": 0x3A58, "campaign_projection_end": 0x6C66,
            "engine_projection_start": 0x2228, "engine_projection_end": 0x23B8,
            "tail_start": 0x5718, "tail_end": 0x57A2,
            "raw_crc_start": 0x6C20, "raw_crc_end": 0x6C24,
            "renderer_start": 0x8E60, "renderer_end": 0x903E})
        self.assertEqual(contract["extended_addresses"], {
            "main_begin": 0x2D6, "main_done": 0x344, "renderer_mask": 0x8E5C,
            "renderer_unmasked": 0x9040, "hud_begin": 0x8F18, "hud_end": 0x8F1C,
            "sat_end": 0x8F20, "overlay_end": 0x8F24})
        for window in contract["exact_windows"].values():
            for offset in (0, len(bytes.fromhex(window["hex"])) - 1):
                changed = bytearray(rom)
                changed[window["address"] + offset] ^= 1
                with self.subTest(address=window["address"], offset=offset), self.assertRaises(reset.EvidenceFailure):
                    reset.extended_pc_contract(bytes(changed), symbols, profile="crc_projection")
        for name, address in contract["extended_addresses"].items():
            changed = bytearray(rom)
            changed[address] ^= 1
            with self.subTest(extended=name), self.assertRaises(reset.EvidenceFailure):
                reset.extended_pc_contract(bytes(changed), symbols, profile="crc_projection")
        for name in (*reset.PC_SYMBOL_BINDINGS.values(), *regular.CRC_PROJECTION_SYMBOL_BINDINGS,
                     "GameplayBuildProjection", "GameplayCommitAndReset", "Crc32UpdateRaw", "RendererPrepareResetOverlay", "MainLoop"):
            changed = {**symbols, name: symbols[name] + 2}
            with self.subTest(symbol=name), self.assertRaises(reset.EvidenceFailure):
                reset.extended_pc_contract(rom, changed, profile="crc_projection")
        old = parse_vasm_symbols(ROOT / "artifacts/archive/m5-renderer-hwm-e6778f90-20261010/level-B03/recursion94-debug.sym")
        strip = lambda text: [line for line in text.splitlines() if not line.startswith("breakpoint ")]
        for builder, kwargs in ((reset.build_command_script, {"extended_overlay": True}), (reset.build_prefix_command_script, {})):
            self.assertEqual(strip(builder(symbols, profile="crc_projection", **kwargs)), strip(builder(old, profile="renderer_hwm", **kwargs)))
        script = reset.build_prefix_command_script(symbols, profile="crc_projection")
        self.assertEqual(reset.prefix_command_policy_errors(script, symbols, profile="crc_projection"), [])
        self.assertEqual(reset.command_policy_errors(reset.build_command_script(symbols, extended_overlay=True, profile="crc_projection"), symbols, extended_overlay=True, profile="crc_projection"), [])
        self.assertEqual(reset._parser().parse_args(["--canonical-prefix", "--profile", "crc_projection"]).profile, "crc_projection")
        source = inspect.getsource(reset.collect)
        self.assertLess(source.index("regular.crc_projection_source_contract(root)"), source.index("subprocess.Popen("))
        with patch.object(regular, "crc_projection_source_contract", side_effect=AssertionError("offline must stay archive-only")):
            report = reset.derive_lookup_comparison(ROOT, candidate_profile="renderer_hwm")
        self.assertEqual(report["cycle_blank_budget_scope"]["full_route_timing_certified"], False)

    def test_renderer_hwm_comparison_cli_is_exclusive_and_never_overwrites_or_launches(self) -> None:
        report = {"diagnostic_protocol_status": "pass", "parity_status": "pass", "cycle_blank_budget_status": "pass"}
        with tempfile.TemporaryDirectory() as directory, patch.object(reset.subprocess, "Popen", side_effect=AssertionError("offline must never launch")), \
                patch.object(reset, "derive_lookup_comparison", return_value=report) as derive:
            output = Path(directory) / "new.json"
            arguments = ["--root", str(ROOT), "--renderer-hwm-compare", str(output)]
            with patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(reset.main(arguments), 0)
            derive.assert_called_once_with(ROOT, candidate_profile="renderer_hwm")
            original = output.read_bytes()
            with self.assertRaises(reset.EvidenceFailure):
                reset.main(arguments)
            self.assertEqual(output.read_bytes(), original)
            for conflict in ("--canonical-prefix", "--extended-overlay", "--ready-console-only", "--offline-reanalyse"):
                with self.subTest(conflict=conflict), self.assertRaisesRegex(reset.EvidenceFailure, "combined with another mode"):
                    reset.main(arguments + [conflict])
            for conflict in ("--highwater-compare", "--prefix-reanalyse", "--extended-baseline-reanalyse"):
                with self.subTest(conflict=conflict), self.assertRaises(reset.EvidenceFailure):
                    reset.main(arguments + [conflict, str(Path(directory) / "other.json")])
            self.assertEqual(output.read_bytes(), original)

    def test_prefix_preparation_program_is_bounded_read_only_and_balanced(self) -> None:
        archive = ROOT / "artifacts/archive/m5-empty-pool-25a74bd2-20261010/level-B03"
        symbols = parse_vasm_symbols(archive / "recursion94-debug.sym")
        script = reset.build_prefix_command_script(symbols, profile="pool")
        self.assertEqual(reset.prefix_command_policy_errors(script, symbols, profile="pool"), [])
        self.assertTrue(reset.prefix_command_policy_errors(script + "set d0 0\n", symbols, profile="pool"))
        lines = script.splitlines()
        self.assertEqual(sum(line.startswith("breakpoint ") for line in lines), 37)
        self.assertEqual(sum(line.startswith("commands ") for line in lines), 37)
        self.assertEqual(sum(line.startswith('printf "R94C RB ') for line in lines), 320)
        self.assertEqual(sum(line.startswith('printf "R94C INPUT ') for line in lines), 21)
        depth = 0
        for line in lines:
            if line.startswith(("commands ", "if ")):
                depth += 1
            elif line == "end":
                depth -= 1
                self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)
        self.assertIn("if [$FFF010].l < $891", script)
        self.assertEqual(script.count('printf "R94C MS '), 1)
        self.assertEqual(script.count('printf "R94C ME '), 1)

    def test_prefix_synthetic_complete_and_partial_input_proof(self) -> None:
        records, route = synthetic_prefix_records()
        self.assertEqual(reset.prefix_input_errors(records), [])
        for stage in reset.prefix_input_stages():
            pre = [item for item in records if item["phase"] == "MS"][stage["total"] - 1]
            partial = records[:records.index(pre) + 1]
            self.assertEqual(reset.prefix_input_errors(partial, pre_count=stage["total"], complete=False), [])
        analysis = reset.analyse_prefix(records, route)
        self.assertEqual(analysis["errors"], [])
        self.assertEqual(analysis["budget_errors"], [])
        self.assertEqual(len(analysis["phase_spans"]), 26)
        self.assertEqual([item["service_tokens"] for item in analysis["natural_resets"]], [1, 1])

    def test_prefix_parser_rejects_unknown_truncated_oversized_and_limit_markers(self) -> None:
        records, _ = synthetic_prefix_records()
        console = "\n".join("R94C " + item["phase"] + " " + " ".join(f"{item[field]:X}" for field in reset.PREFIX_MARKER_FIELDS[item["phase"]]) for item in records)
        self.assertEqual(reset.parse_prefix_markers(console), records)
        for bad in ("R94C MS 1", "R94C ZZ 1", "R94C LIMIT", "R94R MS 1", "R94C MS 100000000 1 0 0 0 0 1", "R94C MS -1", "R94CINPUT"):
            with self.subTest(marker=bad), self.assertRaises(reset.EvidenceFailure):
                reset.parse_prefix_markers(console + "\n" + bad)
        with patch.object(reset, "PREFIX_MAX_RECORDS", 1), self.assertRaises(reset.EvidenceFailure):
            reset.parse_prefix_markers(console)
        with patch.object(reset, "PREFIX_MAX_CONSOLE_BYTES", 1), self.assertRaises(reset.EvidenceFailure):
            reset.parse_prefix_markers(console)

    def test_prefix_synthetic_mutations_fail_protocol_and_budget_remains_separate(self) -> None:
        original, route = synthetic_prefix_records()
        mutations = (("MS", "d0", 0x80), ("ME", "engine_input", 0x80), ("ME", "actor_count", 9),
                     ("HI", "current", 99), ("RB", "long0", 1), ("MR", "state_crc", 9),
                     ("BC", "token", 0), ("BM", "sr", 0x2500), ("WW", "token", 1),
                     ("VA", "entry_status", 0), ("VK", "d1", 0x1234), ("VS", "entry_status", 0),
                     ("VX", "sequence", 0), ("PE", "crc", 1), ("CS", "source", 1))
        for phase, field, value in mutations:
            changed = copy.deepcopy(original)
            next(item for item in changed if item["phase"] == phase)[field] = value
            with self.subTest(phase=phase, field=field):
                self.assertEqual(reset.analyse_prefix(changed, route)["protocol_status"], "fail")
        changed = copy.deepcopy(original)
        first_irq = next(item["frame"] for item in changed if item["phase"] == "VX")
        names = {"VE", "VA", "VC", "VP", "VS", "VK", "VX"}
        changed = [item for item in changed if not (item["phase"] in names and item["frame"] + int(item["phase"] in {"VE", "VA"}) == first_irq)]
        self.assertEqual(reset.analyse_prefix(changed, route)["protocol_status"], "fail")
        changed = copy.deepcopy(original)
        checkpoint = next(item for item in changed if item["phase"] == "MS" and item["attempt"] == 2 and item["tick"] == 59)
        end = next(item for item in changed if item["phase"] == "MD" and item["attempt"] == 2 and item["tick"] == 60)
        for item in changed[changed.index(checkpoint) + 1:changed.index(end) + 1]:
            if "cycle" in item:
                item["cycle"] += 83204 * 7
        analysis = reset.analyse_prefix(changed, route)
        self.assertEqual(analysis["protocol_status"], "pass", analysis["errors"])
        self.assertEqual(analysis["budget_status"], "fail")
        self.assertTrue(any("GameplayStep exceeds" in error for error in analysis["budget_errors"]))

    def test_prefix_console_capacity_and_host_key_transport_are_exact(self) -> None:
        symbols = parse_vasm_symbols(ROOT / "artifacts/archive/m5-high-water-96dec577-20261010/level-B03/recursion94-debug.sym")
        script = reset.build_prefix_command_script(symbols, profile="highwater")
        capacity = reset.prefix_console_capacity(script)
        self.assertEqual(capacity, {"width": 120, "height": 32760, "maximum_records": 24000,
                                   "maximum_marker_columns": 89, "script_echo_rows": 2521,
                                   "other_console_rows_allowance": 512, "required_rows_upper_bound": 27033,
                                   "maximum_utf8_console_bytes": 4194304})
        self.assertLess(capacity["required_rows_upper_bound"], capacity["height"])
        for text in (script + 'printf "R94C ZZ ' + "%x " * 30 + '\\n" ' + "d0 " * 30,
                     script + 'printf "R94C ZZ %x %x\\n" d0\n'):
            with self.assertRaises(reset.EvidenceFailure):
                reset.prefix_console_capacity(text)
        with patch.object(reset, "PREFIX_CONSOLE_ROWS", 9001), self.assertRaises(reset.EvidenceFailure):
            reset.prefix_console_capacity(script)
        # Compare the event builder to the real transport's read-only mocked
        # host-call path, not a second copy of its event algorithm.
        expected, pressed = [], set()
        with patch.object(reset.b03, "_post_key"), patch.object(reset.b03.time, "sleep"):
            for desired in (reset.prefix_schedule()[0], *(item["desired"] for item in reset.prefix_input_stages()), 0):
                reset.b03._set_gameplay_mask(123, desired, pressed, expected)
        self.assertEqual(reset.prefix_expected_input_events(), expected)
        self.assertIn("Right:reassert", expected)
        self.assertEqual(pressed, set())

    def test_prefix_native_path_is_opt_in_and_checks_input_before_next_mask(self) -> None:
        args = reset._parser().parse_args(["--canonical-prefix", "--profile", "highwater"])
        self.assertTrue(args.canonical_prefix)
        self.assertFalse(args.extended_overlay)
        self.assertEqual(args.timeout_seconds, 240)
        source = inspect.getsource(reset._staged_console_helper_payload)
        loop = source[source.index("for marker, desired, pre_count in stages:"):source.index("transcript, geometry = wait_for(COMPLETE_MARKER)")]
        self.assertLess(loop.index("verify_prefix_input("), loop.index("b03._set_gameplay_mask("))
        self.assertLess(loop.index("b03._set_gameplay_mask("), loop.index('send_console("continue'))
        self.assertIn("SetConsoleScreenBufferSize", source)
        self.assertIn("info.cursor.Y >= info.size.Y - 1", source)
        self.assertIn('"R94C LIMIT" in lines', source)
        self.assertIn("prefix parse/analysis failure", inspect.getsource(reset.collect))

    def test_prefix_console_lifecycle_has_exact_21_transitions_and_no_unknown_lines(self) -> None:
        symbols = parse_vasm_symbols(ROOT / "artifacts/archive/m5-high-water-96dec577-20261010/level-B03/recursion94-debug.sym")
        script = reset.build_prefix_command_script(symbols, profile="highwater")
        lines = []
        for index, command in enumerate(line for line in script.splitlines() if line.startswith("breakpoint ")):
            lines.extend(("> " + command, f"68K Breakpoint {index} set at ${command.split('$')[1]}"))
        lines.extend(("> continue", reset.READY_MARKER, "68K Breakpoint 21 hit", "169A: synthetic instruction", "> continue"))
        for stage in reset.prefix_input_stages():
            lines.extend((stage["marker"], "68K Breakpoint 0 hit", "2FA: synthetic instruction", "> continue"))
        lines.extend((reset.COMPLETE_MARKER, "68K Breakpoint 29 hit", "344: synthetic instruction", ">"))
        console = "\n".join(lines)
        self.assertEqual(reset.prefix_console_contract_errors(console, script), [])
        for changed in (console.replace("2FA:", "2FE:", 1), console.replace("> continue", "> continue\n> continue", 1), console + "\nR94C LIMIT", console + "\nFailed to eval", console.replace(reset.READY_MARKER, ""), console + " quit"):
            with self.subTest(change=changed[-80:]):
                self.assertTrue(reset.prefix_console_contract_errors(changed, script))

    def test_prefix_finish_preserves_exact_observed_console_without_teardown_fallback(self) -> None:
        transcript = reset.COMPLETE_MARKER + "\n68K Breakpoint 29 hit\n344: observed instruction\n>\n"
        geometry = {"width": 120, "height": 32760, "raw_screen_buffer": "observed"}
        send = Mock(return_value=5)
        result = reset._finish_prefix_console(transcript, geometry, send)
        self.assertEqual(result, (transcript, geometry, 5))
        self.assertIs(result[1], geometry)
        self.assertNotIn("> quit", result[0])
        send.assert_called_once_with("quit\n")
        send.side_effect = reset.EvidenceFailure("quit delivery failed")
        with self.assertRaisesRegex(reset.EvidenceFailure, "quit delivery failed"):
            reset._finish_prefix_console(transcript, geometry, send)
        self.assertEqual(send.side_effect.console_text, transcript)
        self.assertIs(send.side_effect.console_geometry, geometry)
        source = inspect.getsource(reset._staged_console_helper_payload)
        finish = source[source.index("if canonical_prefix:\n        final, geometry, quit_written"):source.index("    kernel32.FreeConsole()", source.index("if canonical_prefix:\n        final, geometry, quit_written"))]
        self.assertNotIn("read_console", finish.split("    else:", 1)[0])
        self.assertIn('final = transcript + "> quit\\n"', finish.split("    else:", 1)[1])

    def test_prefix_nonzero_emulator_exit_preserves_complete_evidence_before_raising(self) -> None:
        # Execute only the actual bounded exit branch with all I/O mocked;
        # never construct a native process or admit a synthetic live report.
        tree = ast.parse(inspect.getsource(reset.collect))
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and
                      ast.unparse(node.test) == "process.returncode != 0")
        program = compile(ast.fix_missing_locations(ast.Module(body=branch.body, type_ignores=[])), "<actual exit branch>", "exec")
        capture = Mock()
        namespace = {"canonical_prefix": True, "preserve_native_failure": capture,
                     "report_path": Path("new.json"), "trace_path": Path("new.log"), "script": "observed script",
                     "helper_result": {"console_text": "exact observed complete console", "console": {"width": 120}},
                     "process": argparse.Namespace(returncode=7), "helper": argparse.Namespace(returncode=0),
                     "profile": "highwater", "route": {"schedule_sha256": reset.PREFIX_SCHEDULE_SHA256},
                     "rom_path": Path("rom"), "symbols_path": Path("sym"), "collector_path": Path("collector"),
                     "emulator": Path("emulator"), "default_config": Path("cfg"), "root": ROOT,
                     "regular": regular, "bound_pc": {"exact": True}, "_file_record": lambda path, root: {"path": str(path)},
                     "EvidenceFailure": reset.EvidenceFailure}
        with self.assertRaisesRegex(reset.EvidenceFailure, "BlastEm exit code 7"):
            exec(program, namespace)
        capture.assert_called_once()
        self.assertEqual(capture.call_args.kwargs["helper_payload"]["console_text"], "exact observed complete console")
        self.assertTrue(capture.call_args.kwargs["helper_payload"]["full_console_available"])
        self.assertEqual(capture.call_args.kwargs["metadata"]["emulator_returncode"], 7)
        capture.reset_mock()
        namespace["canonical_prefix"] = False
        with self.assertRaises(reset.EvidenceFailure):
            exec(program, namespace)
        capture.assert_not_called()

    def test_prefix_review_mutations_bind_windows_resets_rendering_and_masks(self) -> None:
        original, route = synthetic_prefix_records()
        for phase, field, value in (("MB", "sr", 0x2500), ("ES", "checkpoint_count", 99), ("EE", "length", 3894),
                                    ("RM", "frame", 999), ("PE", "frame", 999), ("MR", "frame", 999),
                                    ("RG", "renderer_attempt", 0), ("RG", "checkpoint_count", 99), ("RR", "pending", 0),
                                    ("DS", "words", 1)):
            changed = copy.deepcopy(original)
            next(item for item in changed if item["phase"] == phase)[field] = value
            with self.subTest(phase=phase, field=field):
                self.assertEqual(reset.analyse_prefix(changed, route)["protocol_status"], "fail")
        for lower, _upper in reset.PREFIX_IRQ_TICK_RANGES:
            first = next(item for item in original if item["phase"] == "VE" and item["tick"] == lower)
            last = next(item for item in original if item["phase"] == "VX" and item["frame"] == first["frame"] + 1)
            changed = original[:original.index(first)] + original[original.index(last) + 1:]
            with self.subTest(window=lower):
                self.assertEqual(reset.analyse_prefix(changed, route)["protocol_status"], "fail")
        changed = copy.deepcopy(original)
        wait = next(item for item in changed if item["phase"] == "WW")
        changed.insert(changed.index(wait) + 1, {**wait, "cycle": wait["cycle"] + 7})
        self.assertEqual(reset.analyse_prefix(changed, route)["protocol_status"], "fail")
        changed = copy.deepcopy(original)
        ve = next(item for item in changed if item["phase"] == "VE")
        vx = next(item for item in changed if item["phase"] == "VX")
        ve["token"], vx["token"] = 5, 6
        analysis = reset.analyse_prefix(changed, route)
        self.assertEqual(analysis["protocol_status"], "pass", analysis["errors"])
        self.assertEqual(analysis["budget_status"], "fail")

    def test_renderer_hwm_nonzero_exit_archives_complete_helper_then_fails_without_overwrite(self) -> None:
        for helper_module, profile in ((helper, profile) for helper in (regular, reset)
                                       for profile in ("renderer_hwm", "crc_projection")):
            tree = ast.parse(inspect.getsource(helper_module.collect))
            branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and
                          ast.unparse(node.test) == "process.returncode != 0")
            program = compile(ast.fix_missing_locations(ast.Module(body=branch.body, type_ignores=[])), "<candidate actual exit branch>", "exec")
            with self.subTest(helper=helper_module.__name__, profile=profile), tempfile.TemporaryDirectory() as directory:
                report_path, trace_path = Path(directory) / "failed.json", Path(directory) / "failed.log"
                result = {"console_text": "exact complete console\n>\n", "console": {"width": 120, "cursor_x": 2},
                          "input_events": [{"edge": "Up:down"}], "script_input_records": 123}
                namespace = {"canonical_prefix": False, "extended_overlay": True, "profile": profile,
                             "preserve_native_failure": reset.preserve_native_failure,
                             "report_path": report_path, "trace_path": trace_path, "script": "observed script\n",
                             "helper_result": result, "process": argparse.Namespace(returncode=7),
                             "helper": argparse.Namespace(returncode=0), "stderr": "native helper stderr",
                             "args": argparse.Namespace(timeout_seconds=240), "runtime_sources": {"approved": "exact hash"},
                             "rom_path": Path("rom"), "symbols_path": Path("sym"), "collector_path": Path("collector"),
                             "console_helper_path": Path("console_helper"), "emulator": Path("emulator"),
                             "default_config": Path("cfg"), "root": ROOT, "regular": regular, "bound_pc": {"exact": True},
                             "_file_record": lambda path, root: {"path": str(path)}, "EvidenceFailure": reset.EvidenceFailure}
                with patch.object(reset.subprocess, "Popen", side_effect=AssertionError("no retry")):
                    with self.assertRaisesRegex(reset.EvidenceFailure, "BlastEm exit code 7"):
                        exec(program, namespace)
                report = json.loads(report_path.read_text("utf-8"))
                self.assertEqual(report["status"], "diagnostic_fail")
                self.assertFalse(report["timing_evidence_complete"])
                self.assertEqual(report["failure"], {**result, "error": "BlastEm exit code 7", "full_console_available": True})
                self.assertEqual(report["metadata"]["emulator_returncode"], 7)
                self.assertEqual(report["metadata"]["helper_returncode"], 0)
                self.assertEqual(report["metadata"]["helper_stderr"], "native helper stderr")
                self.assertEqual(report["metadata"]["candidate_runtime_sources"], namespace["runtime_sources"])
                self.assertIn(result["console_text"], trace_path.read_text("utf-8"))
                frozen = (report_path.read_bytes(), trace_path.read_bytes())
                with self.assertRaisesRegex(reset.EvidenceFailure, "distinct new paths"):
                    exec(program, namespace)
                self.assertEqual((report_path.read_bytes(), trace_path.read_bytes()), frozen)
                # Preserve historical non-prefix modes exactly; only this
                # approved candidates gain the new failure-only archive path.
                namespace["profile"] = "highwater"
                namespace["preserve_native_failure"] = Mock(side_effect=AssertionError("old mode changed"))
                with self.assertRaisesRegex(reset.EvidenceFailure, "BlastEm exit code 7"):
                    exec(program, namespace)
                namespace["preserve_native_failure"].assert_not_called()

    def test_prefix_final_review_token_ordinal_and_startup_anchor_mutations(self) -> None:
        original, route = synthetic_prefix_records()
        for phase in ("MB", "RM", "RG", "RR", "RX", "HB", "HE", "SE", "OE"):
            changed = copy.deepcopy(original)
            next(item for item in changed if item["phase"] == phase)["token"] = 7
            with self.subTest(phase=phase):
                self.assertEqual(reset.analyse_prefix(changed, route)["budget_status"], "fail")
        changed = copy.deepcopy(original)
        next(item for item in changed if item["phase"] == "MD")["token"] = 1
        result = reset.analyse_prefix(changed, route)
        self.assertEqual(result["protocol_status"], "fail")
        self.assertEqual(result["budget_status"], "fail")
        self.assertIn("prefix nonempty-token MD cannot reach same-frame/identity WAIT", result["errors"])
        changed = copy.deepcopy(original)
        rr = next(item for item in changed if item["phase"] == "RR")
        rr["checkpoint_count"] = rr["renderer_checkpoint_count"] = 99
        self.assertEqual(reset.analyse_prefix(changed, route)["protocol_status"], "fail")
        changed = copy.deepcopy(original)
        for item in changed:
            item["frame"] += 10
            if item["phase"] in {"VA", "VX"}:
                item["sequence"] += 20
        result = reset.analyse_prefix(changed, route)
        self.assertEqual(result["protocol_status"], "fail")
        self.assertTrue(any("exact physical frame1" in error for error in result["errors"]))
        # An actual first-step VBlank crossing remains an observed budget
        # failure, not an invented startup identity or silently shifted trace.
        changed = copy.deepcopy(original)
        next(item for item in changed if item["phase"] == "ME")["frame"] = 2
        result = reset.analyse_prefix(changed, route)
        self.assertEqual(result["protocol_status"], "pass", result["errors"])
        self.assertEqual(result["budget_status"], "fail")
        self.assertIn("unsupported", result["protocol_domain_limitations"][0])

    def test_prefix_real_capture_directional_coverage_preserves_every_budget_failure(self) -> None:
        capture, records = captured_prefix_records()
        analysis = reset.analyse_prefix(records, capture["prefix_route"])
        self.assertEqual(analysis["protocol_status"], "pass", analysis["errors"])
        self.assertEqual(analysis["budget_status"], "fail")
        self.assertEqual(analysis["budget_errors"], capture["analysis"]["budget_errors"])
        self.assertEqual(len(analysis["budget_errors"]), 312)
        for field in ("phase_spans", "natural_resets", "marker_counts"):
            self.assertEqual(analysis[field], capture["analysis"][field])
        coverage = analysis["irq_observation_coverage"]
        expected = [*range(1508, 1512), *range(1570, 1574), *range(1631, 1650), *range(1695, 1698)]
        self.assertEqual(coverage["required_frames"], expected)
        self.assertEqual(coverage["observed_frames"], expected)
        self.assertEqual(coverage["missing_frames"], [])
        self.assertEqual(coverage["unanchored_frames"], [])
        self.assertTrue({1507, 1630, 1694}.isdisjoint(expected))
        commits = {item["frame"]: item for item in analysis["sampled_irqs"]}
        self.assertEqual(commits[1634]["renderer_publication_binding"], "first_commit_of_sampled_renderer")
        self.assertEqual(commits[1639]["words"], 440)
        self.assertEqual(commits[1640]["words"], 0)
        self.assertEqual(commits[1640]["renderer_publication_binding"], "already_consumed_exact_empty")
        self.assertIsNone(analysis["observed_renderer_consumption"][-1]["first_later_commit"])

    def test_prefix_real_capture_rejects_every_whole_irq_deletion_including_boundaries(self) -> None:
        capture, original = captured_prefix_records()
        for vx in (item for item in original if item["phase"] == "VX"):
            ve = next(item for item in original if item["phase"] == "VE" and item["frame"] == vx["frame"] - 1)
            changed = original[:original.index(ve)] + original[original.index(vx) + 1:]
            with self.subTest(frame=vx["frame"]):
                analysis = reset.analyse_prefix(changed, capture["prefix_route"])
                self.assertEqual(analysis["protocol_status"], "fail")
                self.assertIn("prefix sampled engine counters require missing IRQ(s)", analysis["errors"])

    def test_prefix_real_capture_rejects_complete_second_commit_of_consumed_publication(self) -> None:
        capture, original = captured_prefix_records()
        changed = copy.deepcopy(original)
        vc = next(item for item in changed if item["phase"] == "VC" and item["frame"] == 1640)
        vc.update(pending=1, published_depth=4, build_depth=4, build_words=440)
        additions = []
        for item in original:
            if item["frame"] != 1639 or item["phase"] not in {"Q", "DS", "DE"}:
                continue
            added = {**item, "frame": 1640}
            if "cycle" in added:
                added["cycle"] = vc["cycle"] + len(additions) + 1
            additions.append(added)
        changed[changed.index(vc) + 1:changed.index(vc) + 1] = additions
        for item in changed:
            if item["frame"] == 1640 and item["phase"] == "VP":
                item.update(commit_words=440, debug_words=440)
            if item["frame"] == 1640 and item["phase"] == "VX":
                item["debug_words"] = 440
        analysis = reset.analyse_prefix(changed, capture["prefix_route"])
        self.assertEqual(analysis["errors"], ["prefix IRQ 1640 does not consume the sampled renderer publication exactly once"])
        # RR181 is bound to its first commit even though the engine has
        # advanced to tick182 before that IRQ; no RR182 is invented.
        changed = copy.deepcopy(original)
        next(item for item in changed if item["phase"] == "RR" and item["tick"] == 181)["pending"] = 0
        self.assertIn("prefix IRQ 1634 does not consume the sampled renderer publication exactly once",
                      reset.analyse_prefix(changed, capture["prefix_route"])["errors"])

    def test_prefix_offline_derivation_reparses_raw_log_and_keeps_original_failure(self) -> None:
        capture, records = captured_prefix_records()
        with patch.object(reset.subprocess, "Popen", side_effect=AssertionError("offline must not launch")), \
                patch.object(reset, "parse_prefix_markers", wraps=reset.parse_prefix_markers) as parse:
            report = reset.derive_prefix_reanalysis(ROOT)
        self.assertEqual(parse.call_count, 2)  # record derivation and console audit
        self.assertIn("R94C MS ", parse.call_args_list[0].args[0])
        self.assertEqual(report["status"], "diagnostic_fail")
        self.assertEqual(report["offline_reanalysis_status"], "pass")
        self.assertEqual(report["diagnostic_protocol_status"], "pass")
        self.assertEqual(report["cycle_blank_budget_status"], "fail")
        self.assertEqual(report["source_capture"]["original_analysis_errors"], capture["analysis"]["errors"])
        self.assertEqual(report["analysis"]["budget_errors"], capture["analysis"]["budget_errors"])
        with patch.object(reset, "parse_prefix_markers", return_value=records[:-1]):
            with self.assertRaisesRegex(reset.EvidenceFailure, "independently parsed raw records"):
                reset.derive_prefix_reanalysis(ROOT)
        altered = copy.deepcopy(report["analysis"])
        altered["budget_errors"].pop()
        with patch.object(reset, "analyse_prefix", return_value=altered):
            with self.assertRaisesRegex(reset.EvidenceFailure, "changed original budget/timing facts"):
                reset.derive_prefix_reanalysis(ROOT)
        for drift in ("PREFIX_CAPTURE_REPORT_SHA256", "PREFIX_CAPTURE_TRACE_SHA256", "PREFIX_CAPTURE_COLLECTOR_SHA256"):
            with self.subTest(pin=drift), patch.object(reset, drift, "0" * 64):
                with self.assertRaisesRegex(reset.EvidenceFailure, "immutable source hash drift"):
                    reset.derive_prefix_reanalysis(ROOT)

    def test_prefix_offline_cli_is_exclusive_non_overwriting_and_never_launches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new-reanalysis.json"
            arguments = ["--root", str(ROOT), "--prefix-reanalyse", str(output)]
            with patch.object(reset.subprocess, "Popen", side_effect=AssertionError("offline must not launch")):
                self.assertEqual(reset.main(arguments), 0)
                before = output.read_bytes()
                with self.assertRaisesRegex(reset.EvidenceFailure, "already exists"):
                    reset.main(arguments)
                self.assertEqual(output.read_bytes(), before)
                with self.assertRaisesRegex(reset.EvidenceFailure, "combined"):
                    reset.main(arguments + ["--canonical-prefix"])

    def test_prefix_nonzero_done_token_without_matching_wait_keeps_later_waits(self) -> None:
        original, route = synthetic_prefix_records()
        done = next(item for item in original if item["phase"] == "MD")
        done["token"] = 1
        changed = [item for item in original if not (item["phase"] == "WW" and all(item[key] == done[key] for key in ("frame", "attempt", "tick")))]
        self.assertTrue(any(item["phase"] == "WW" for item in changed))
        analysis = reset.analyse_prefix(changed, route)
        self.assertEqual(analysis["protocol_status"], "pass", analysis["errors"])
        self.assertEqual(analysis["budget_status"], "fail")

    def test_native_helper_timeout_serializes_partial_evidence_as_failure(self) -> None:
        failure = reset.EvidenceFailure("READY timeout")
        failure.console_text = "R94R READY\n>  y\n"
        failure.console_geometry = {"width": 120}
        args = argparse.Namespace(command_file="unused", process_id=42, timeout_seconds=240)
        for helper in (reset, reset.native):
            with self.subTest(helper=helper.__name__):
                stdout = io.StringIO()
                with patch.object(Path, "read_text", return_value="continue\n"), patch.object(helper, "_staged_console_helper_payload", side_effect=failure), patch.object(helper.sys, "stdout", stdout):
                    self.assertEqual(helper._console_helper_main(args), 1)
                payload = json.loads(stdout.getvalue())
                self.assertTrue(payload["full_console_available"])
                self.assertEqual(payload["console_text"], failure.console_text)

    def test_native_helper_attach_failure_explicitly_lacks_console(self) -> None:
        args = argparse.Namespace(command_file="unused", process_id=42, timeout_seconds=240)
        for helper in (reset, reset.native):
            stdout = io.StringIO()
            with patch.object(Path, "read_text", return_value="continue\n"), patch.object(helper, "_staged_console_helper_payload", side_effect=reset.EvidenceFailure("AttachConsole failed")), patch.object(helper.sys, "stdout", stdout):
                self.assertEqual(helper._console_helper_main(args), 1)
            payload = json.loads(stdout.getvalue())
            self.assertFalse(payload["full_console_available"])
            self.assertEqual(payload["console_text"], "")

    def test_ready_console_only_is_explicit_and_keeps_raw_cursor_and_peek(self) -> None:
        failure = reset.EvidenceFailure("READY-only diagnostic")
        failure.console_text = "R94R READY\n> oug)\n"
        failure.console_geometry = {"cursor_x": 2, "current_row_utf16": list(map(ord, "> oug)"))}
        failure.console_input_peek = {"available_count": 0, "records": [], "consumed_records": 0}
        stdout = io.StringIO()
        args = argparse.Namespace(command_file="unused", process_id=42, timeout_seconds=240, ready_console_only=True)
        with patch.object(Path, "read_text", return_value="continue\n"), patch.object(reset, "_staged_console_helper_payload", side_effect=failure) as staged, patch.object(reset.sys, "stdout", stdout):
            self.assertEqual(reset._console_helper_main(args), 1)
        staged.assert_called_once_with(42, "continue\n", 240, ready_console_only=True)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["console"], failure.console_geometry)
        self.assertEqual(payload["console_input_peek"]["consumed_records"], 0)

    def test_lookup_overrun_origin_and_extra_queue_are_exact_not_waived(self) -> None:
        capture = json.loads((ROOT / "artifacts/diagnostics/m5-budget/reset-overlay-lookup-9a741642-total719-total724-20261010.json").read_text("utf-8"))
        for phase, field, value in (("VO", "counter", 42), ("Q", "destination", 0xC180)):
            records = copy.deepcopy(capture["records"])
            item = next(item for item in records if item["phase"] == phase and (phase == "VO" or item["frame"] == 727))
            item[field] = value
            self.assertEqual(reset.analyse_extended(records)["protocol_status"], "fail")

    def test_live_wait_anchor_and_late_irq_cross_phase_mutations_fail(self) -> None:
        captures = [json.loads((ROOT / f"artifacts/diagnostics/m5-budget/{stem}.json").read_text("utf-8")) for stem in ("reset-overlay-baseline-140039e3-total719-total724-20261010", "reset-overlay-lookup-9a741642-total719-total724-20261010", "reset-overlay-sprite-09be6696-total719-total724-20261010", "reset-overlay-indexed-c051995c-total719-total724-20261010")]
        for capture in captures:
            self.assertEqual(reset.analyse_extended(capture["records"])["protocol_status"], "pass")
            for mutation in ("delete_anchor", "delete_reset_wait", "wait_fields", "vk_predicates", "vs_entry", "swap_vs_vk", "anchor_period"):
                with self.subTest(mutation=mutation, rom=capture["provenance"]["rom"]["sha256"]):
                    records = copy.deepcopy(capture["records"])
                    if mutation in {"delete_anchor", "delete_reset_wait"}:
                        index = next(index for index, item in enumerate(records) if item["phase"] == "WW" and item["frame"] == (719 if mutation == "delete_anchor" else 722))
                        records.pop(index)
                    elif mutation == "wait_fields":
                        for item in records:
                            if item["phase"] == "WW":
                                item.update(frame=0, attempt=9, tick=9, sr=0, token=9)
                    elif mutation == "vk_predicates":
                        for item in records:
                            if item["phase"] == "VK" and item["frame"] >= 725:
                                item.update(d0=0x1234, d1=0x1234)
                    elif mutation == "vs_entry":
                        next(item for item in records if item["phase"] == "VS" and item["frame"] == 727)["entry_status"] = 0
                    elif mutation == "swap_vs_vk":
                        first = next(index for index, item in enumerate(records) if item["phase"] == "VS" and item["frame"] == 727)
                        second = next(index for index, item in enumerate(records) if item["phase"] == "VK" and item["frame"] == 727)
                        records[first]["cycle"], records[second]["cycle"] = records[second]["cycle"], records[first]["cycle"]
                        records[first], records[second] = records[second], records[first]
                    else:
                        next(item for item in records if item["phase"] == "VE" and item["frame"] == 718)["cycle"] -= 500
                    self.assertEqual(reset.analyse_extended(records)["protocol_status"], "fail")


if __name__ == "__main__":
    unittest.main()
