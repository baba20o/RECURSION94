from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.build_rom import parse_sjasmplus_symbols
from tools.audio_compiler.compiler import AUDIO_REGISTRY_OUTPUT, build_artifacts


ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "artifacts" / "generated" / "audio"
Z80_SOURCE = ROOT / "src" / "audio" / "z80_driver_v2.asm"
BRIDGE_SOURCE = ROOT / "src" / "hardware" / "audio_bridge_v2.asm"
WARNING_RE = re.compile(r"(?:warning:|warnings:\s*[1-9])", re.IGNORECASE)


def asm_bytes(source: str, start: str, end: str) -> list[int]:
    block = source.split(f"{start}:", 1)[1].split(f"{end}:", 1)[0]
    values: list[int] = []
    for line in block.splitlines():
        statement = line.split(";", 1)[0]
        match = re.search(r"\b(?:db|dc\.b)\s+(.+)", statement)
        if match is None:
            continue
        for token in match.group(1).split(","):
            token = token.strip()
            values.append(
                int(token[1:], 16) if token.startswith("$") else int(token)
            )
    return values


class AudioV2StandaloneAssemblyTests(unittest.TestCase):
    def test_z80_driver_assembles_warning_free_inside_four_kib(self) -> None:
        assembler = ROOT / ".toolcache" / "bin" / "sjasmplus.exe"
        self.assertTrue(assembler.is_file(), f"missing pinned assembler: {assembler}")
        GENERATED.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".z80-v2-test-",
            dir=GENERATED,
        ) as temporary:
            destination = Path(temporary)
            output = destination / "driver.bin"
            listing = destination / "driver.lst"
            symbols_path = destination / "driver.sym"
            sld = destination / "driver.sld"
            relative = lambda path: path.relative_to(ROOT).as_posix()
            command = [
                str(assembler),
                "--nologo",
                "--fullpath",
                "--dirbol",
                "--syntax=abFw",
                "-Wall",
                "--cleanonerror",
                f"--raw={relative(output)}",
                f"--lst={relative(listing)}",
                "--lstlab=sort",
                f"--sym={relative(symbols_path)}",
                f"--sld={relative(sld)}",
                "src/audio/z80_driver_v2.asm",
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
            self.assertLessEqual(output.stat().st_size, 0x1000)
            symbols = parse_sjasmplus_symbols(symbols_path)
            self.assertEqual(output.stat().st_size, symbols["DriverV2ImageEnd"])
            self.assertEqual(0x1000, symbols["DRIVER_STATE_BASE"])
            self.assertEqual((0x1200, 0x1280), (
                symbols["ActiveBankStart"],
                symbols["ActiveBankEnd"],
            ))
            self.assertEqual((0x1F00, 0x1F40), (
                symbols["CommandRingStart"],
                symbols["CommandRingDataEnd"],
            ))
            self.assertEqual((0x1F40, 0x1F80), (
                symbols["ExtendedStatusStart"],
                symbols["ExtendedStatusEnd"],
            ))
            self.assertEqual(0x1FE0, symbols["DriverStatusStart"])
            self.assertEqual(0x1FF0, symbols["UrgentMailboxStart"])

    def test_68000_bridge_assembles_warning_free_as_standalone_source(self) -> None:
        assembler = ROOT / ".toolcache" / "bin" / "vasmm68k_mot.exe"
        self.assertTrue(assembler.is_file(), f"missing pinned assembler: {assembler}")
        GENERATED.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".bridge-v2-test-",
            dir=GENERATED,
        ) as temporary:
            output = Path(temporary) / "bridge.bin"
            command = [
                str(assembler),
                "-Fbin",
                "-m68000",
                "-no-opt",
                "-x",
                "-o",
                output.relative_to(ROOT).as_posix(),
                "src/hardware/audio_bridge_v2.asm",
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
            self.assertGreater(output.stat().st_size, 1024)
            self.assertLess(output.stat().st_size, 4096)

    def test_driver_source_exposes_bounded_tick_and_safety_contracts(self) -> None:
        source = Z80_SOURCE.read_text(encoding="utf-8")
        self.assertIn("COMMANDS_PER_VINT               equ     4", source)
        self.assertIn("MAX_PPQN_TICKS_PER_VINT         equ     2", source)
        self.assertIn("STATE_SFX_COMMAND_BUDGET", source)
        self.assertIn("STATE_SAFETY_COMMAND", source)
        self.assertIn("call    ServiceUrgentMailboxV2", source)
        self.assertIn("call    ServiceCommandQueueV2", source)
        self.assertIn("call    TickSequencerV2", source)
        self.assertIn("call    AllNotesOffIfAudibleV2", source)
        self.assertIn("call    KeyOffMusicRowV2", source)
        self.assertIn("STATUS_FLAG_BANK_INVALID", source)
        self.assertIn("ERROR_GENERATION_COLLISION", source)
        self.assertIn("SceneBankTableV2End-SceneBankTableV2=40", source)

    def test_bridge_source_retries_generations_and_guards_bank_upload(self) -> None:
        source = BRIDGE_SOURCE.read_text(encoding="utf-8")
        service = source.split("AudioV2Service:", 1)[1].split(
            "AudioV2PollStatus:",
            1,
        )[0]
        self.assertIn("AUDIO_V2_IN_FLIGHT_ACTIVE", service)
        self.assertIn("AUDIO_V2_MIRROR_REQUESTED_GEN", service)
        self.assertIn("AUDIO_V2_DESIRED_DIRTY", service)
        self.assertIn("bsr.w   AudioV2PostCommand", service)
        bank = source.split("AudioV2LoadBank:", 1)[1].split(
            "AudioV2AcquireBus:",
            1,
        )[0]
        self.assertIn("#AUDIO_V2_BANK_BYTES,d1", bank)
        self.assertIn("bsr.w   AudioV2Crc32", bank)
        self.assertIn("#AUDIO_V2_STATE_PAUSED", bank)
        self.assertIn("#AUDIO_V2_UNSAFE_BANK_LOAD", bank)
        self.assertIn("dbf     d1,.copy", bank)

    def test_both_cpu_tables_exactly_match_the_generated_scene_registry(self) -> None:
        registry = json.loads(
            build_artifacts(ROOT / "content").files[AUDIO_REGISTRY_OUTPUT]
        )
        scenes = registry["scenes"]
        driver = Z80_SOURCE.read_text(encoding="utf-8")
        bridge = BRIDGE_SOURCE.read_text(encoding="utf-8")
        self.assertEqual(
            [scene["bank_id"] for scene in scenes],
            asm_bytes(
                driver,
                "SceneBankTableV2",
                "SceneBankTableV2End",
            ),
        )
        self.assertEqual(
            [scene["echo_cap"] for scene in scenes],
            asm_bytes(
                driver,
                "SceneCapTableV2",
                "SceneCapTableV2End",
            ),
        )
        self.assertEqual(
            [scene["pattern_start_row"] for scene in scenes],
            asm_bytes(
                driver,
                "SceneStartRowTableV2",
                "SceneStartRowTableV2End",
            ),
        )
        self.assertEqual(
            [scene["layer_cap_mask"] for scene in scenes],
            asm_bytes(
                bridge,
                "AudioV2SceneCapMaskTable",
                "AudioV2SceneCapMaskTableEnd",
            ),
        )

    def test_v2_sources_are_not_wired_into_current_translation_units(self) -> None:
        main = (ROOT / "src" / "main.asm").read_text(encoding="utf-8")
        build = (ROOT / "tools" / "build_rom.py").read_text(encoding="utf-8")
        self.assertNotIn("audio_bridge_v2.asm", main)
        self.assertNotIn("z80_driver_v2.asm", main)
        self.assertNotIn("z80_driver_v2.asm", build)


if __name__ == "__main__":
    unittest.main()
