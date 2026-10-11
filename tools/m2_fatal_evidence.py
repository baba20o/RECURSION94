#!/usr/bin/env python3
"""Prove the Debug fatal fixture through real controller input and guest code."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    DEBUG_BASE,
    DEBUG_BLOCK_BYTES,
    EvidenceFailure,
    GdbRemote,
    apply_blastem_session_isolation,
    check,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    parse_debug_block,
    parse_vasm_symbols,
    post_virtual_key,
    require_gdb_port_available,
    require_stop,
    sha256_file,
    shutdown_emulator,
    verify_default_bindings,
    verify_no_user_blastem_override,
    write_standard_pad_profile,
    write_json,
)


FATAL_BUTTONS = 0x70
TRIGGER_ADDRESS = 0x00FF980C
FATAL_CODE = 1
KEY_CHORD = (
    ("A", 0x41, 0x1E),
    ("B", 0x53, 0x1F),
    ("C", 0x44, 0x20),
    ("Start", 0x0D, 0x1C),
)


def png_facts(path: Path) -> dict[str, Any]:
    """Decode fatal captures with the shared strict M2 PNG implementation."""

    from tools.m2_screenshot_capture import normal_png_facts

    return normal_png_facts(path)


def fatal_report(
    *,
    rom_sha256: str,
    pre_exception: dict[str, Any],
    trigger_value: int,
    fatal_sample: dict[str, Any],
    halted_samples: list[dict[str, Any]],
    safe_exception_address: int,
    halt_address: int,
    halt_opcode_hex: str,
    stop_observation: dict[str, Any],
    screenshot: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    frozen_frames = [
        int(fatal_sample["debug"]["frame_counter"]),
        *[
            int(sample["debug"]["frame_counter"])
            for sample in halted_samples
        ],
    ]
    frozen_heartbeats = [
        int(fatal_sample["debug"]["heartbeat"]),
        *[int(sample["debug"]["heartbeat"]) for sample in halted_samples],
    ]
    halted_pcs = [str(sample["pc"]).upper() for sample in halted_samples]
    expected_halt_pc = f"{halt_address:08X}"
    checks = [
        check(
            "real_controller_chord",
            int(pre_exception["input_held"]) == FATAL_BUTTONS
            and int(pre_exception["input_start_held"]) == 1
            and int(pre_exception["input_connected"]) == 1,
            "held/start/connected "
            f"{pre_exception['input_held']:02X}/"
            f"{pre_exception['input_start_held']}/"
            f"{pre_exception['input_connected']}",
        ),
        check(
            "standard_three_button_profile",
            session.get("controller_profile", {}).get("status") == "pass"
            and session.get("controller_profile", {}).get(
                "port_1_assignment"
            )
            == "gamepad3.1",
            str(
                session.get(
                    "controller_profile",
                    {"detail": "controller profile evidence missing"},
                ).get("detail")
            ),
        ),
        check(
            "controller_key_bindings",
            session.get("bindings", {}).get("status") == "pass"
            and session.get("direction_bindings", {}).get("status") == "pass",
            "A/B/C/Start and D-pad bindings were checked in pinned config",
        ),
        check(
            "fixture_trigger_latched",
            trigger_value == 1,
            f"trigger byte ${trigger_value:02X}",
        ),
        check(
            "guest_exception_entry",
            str(fatal_sample["safe_entry_pc"]).upper()
            == f"{safe_exception_address:08X}",
            f"SafeException PC {fatal_sample['safe_entry_pc']}",
        ),
        check(
            "fatal_abi_published",
            int(fatal_sample["debug"]["fatal_code"]) == FATAL_CODE,
            f"fatal code {fatal_sample['debug']['fatal_code']}",
        ),
        check(
            "visible_fatal_capture",
            screenshot["width"] == 320
            and screenshot["height"] == 240
            and float(screenshot["red_pixel_ratio"]) >= 0.95
            and int(screenshot["white_pixel_count"]) >= 100
            and int(screenshot["quantized_color_count"]) == 2,
            "PNG "
            f"{screenshot['width']}x{screenshot['height']} "
            f"{screenshot['bytes']} bytes; red ratio "
            f"{screenshot['red_pixel_ratio']:.4f}, white pixels "
            f"{screenshot['white_pixel_count']}, quantized colors "
            f"{screenshot['quantized_color_count']}",
        ),
        check(
            "owned_native_fatal_capture",
            screenshot.get("window_ownership", {}).get("process_id")
            == session.get("process_id")
            and screenshot.get("window_ownership", {}).get(
                "resolved_owner_process_id"
            )
            == session.get("process_id")
            and screenshot.get("window_ownership", {}).get(
                "capture_method"
            )
            == "BlastEm ui.screenshot via isolated one-shot key binding"
            and screenshot.get("window_ownership", {}).get(
                "ui_screenshot_key"
            )
            == "P"
            and session.get("screenshot_binding", {}).get("status")
            == "pass"
            and session.get("screenshot_binding", {}).get("key") == "P",
            "PID-owned BlastEm framebuffer and exact isolated P "
            "ui.screenshot binding",
        ),
        check(
            "guest_halt_entry_stable",
            len(halted_pcs) >= 2
            and all(pc == expected_halt_pc for pc in halted_pcs),
            f"halt-entry PCs {halted_pcs}, expected {expected_halt_pc}",
        ),
        check(
            "halt_loop_encoding",
            halt_opcode_hex.upper() == "4E72270060FA",
            f"halt bytes {halt_opcode_hex.upper()} "
            "(stop #$2700; bra.s SafeExceptionHalt)",
        ),
        check(
            "masked_stop_quiescent",
            bool(stop_observation.get("timed_out"))
            and stop_observation.get("response") is None
            and int(stop_observation.get("next_breakpoint", -1))
            == halt_address + 4
            and float(stop_observation.get("observation_seconds", 0)) >= 1.0,
            "post-STOP breakpoint observation "
            f"{stop_observation}",
        ),
        check(
            "fatal_state_stable_at_halt_entry",
            len(set(frozen_frames)) == 1
            and len(set(frozen_heartbeats)) == 1,
            f"frames {frozen_frames}, heartbeats {frozen_heartbeats}",
        ),
        check(
            "halt_entry_fatal_persists",
            all(
                int(sample["debug"]["fatal_code"]) == FATAL_CODE
                for sample in halted_samples
            ),
            "fatal code remains nonzero in repeated halt-entry snapshots",
        ),
        check(
            "no_pc_or_ram_patch",
            not session.get("write_commands"),
            f"write commands {session.get('write_commands', [])}",
        ),
    ]
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "schema": 1,
        "scope": "Milestone2",
        "evidence_type": "blastem_real_input_guest_fatal_screen",
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "checks": checks,
        "pre_exception": pre_exception,
        "trigger_value": trigger_value,
        "fatal_sample": fatal_sample,
        "halted_samples": halted_samples,
        "halt_opcode_hex": halt_opcode_hex.upper(),
        "stop_observation": stop_observation,
        "screenshot": screenshot,
        "session": session,
        "notes": [
            "The collector injects Start+A+B+C only; it never patches PC or RAM.",
            "A breakpoint immediately after STOP remains unreachable for a bounded "
            "observation; no debugger write is used to wake the masked guest.",
        ],
    }


def verify_fatal_bindings(default_config: Path) -> dict[str, Any]:
    text = default_config.read_text("utf-8", errors="replace")
    expected = {
        "a": "gamepads.1.a",
        "s": "gamepads.1.b",
        "d": "gamepads.1.c",
        "enter": "gamepads.1.start",
    }
    found = {
        key: bool(
            re.search(
                rf"(?m)^\s*{key}\s+{re.escape(action)}\s*$",
                text,
            )
        )
        for key, action in expected.items()
    }
    return {
        "status": "pass" if all(found.values()) else "fail",
        "bindings": found,
        "sha256": sha256_file(default_config),
    }


def capture_window(
    *,
    root: Path,
    process_id: int,
    window: int,
    screenshot_directory: Path,
    output: Path,
    capture_started_ns: int,
) -> dict[str, Any]:
    # Use BlastEm's native framebuffer capture rather than screen pixels. This
    # remains PID/title-owned and avoids both desktop occlusion and foreground
    # focus restrictions in automated Windows sessions.
    from tools.m2_screenshot_capture import capture_native_screenshot

    return capture_native_screenshot(
        root=root,
        process_id=process_id,
        window=window,
        screenshot_directory=screenshot_directory,
        output=output,
        capture_started_ns=capture_started_ns,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    args = parser.parse_args()
    root = args.root.resolve()
    rom = args.rom.resolve()
    report_path = (
        args.report.resolve()
        if args.report
        else root / "artifacts/reports/m2-fatal.json"
    )
    screenshot_path = root / "artifacts/screenshots/m2-fatal.png"
    transcript_path = root / "artifacts/traces/m2-fatal-gdb.log"
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    transcript: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    remote: GdbRemote | None = None
    rom_sha256 = ""
    report: dict[str, Any]
    try:
        if os.name != "nt":
            raise EvidenceFailure("live fatal evidence requires Windows BlastEm")
        rom_sha256 = sha256_file(rom)
        if args.expected_sha256 and (
            rom_sha256 != args.expected_sha256.strip().lower()
        ):
            raise EvidenceFailure("Debug ROM does not match expected SHA-256")
        emulator_directory = root / ".toolcache/blastem"
        emulator = emulator_directory / "blastem.exe"
        default_config = emulator_directory / "default.cfg"
        user_config_evidence = verify_no_user_blastem_override()
        direction_bindings = verify_default_bindings(default_config)
        fatal_bindings = verify_fatal_bindings(default_config)
        if (
            direction_bindings["status"] != "pass"
            or fatal_bindings["status"] != "pass"
        ):
            raise EvidenceFailure("pinned controller bindings do not match")
        symbols = parse_vasm_symbols(root / "build/recursion94-debug.sym")
        required_symbols = (
            "VBlankInterrupt",
            "SafeException",
            "SafeExceptionHalt",
        )
        missing = [name for name in required_symbols if name not in symbols]
        if missing:
            raise EvidenceFailure(f"Debug symbol map is missing {missing}")
        vblank = symbols["VBlankInterrupt"]
        safe_exception = symbols["SafeException"]
        halt = symbols["SafeExceptionHalt"]
        launch_flags = [
            "-D",
            "-g",
            "-m",
            "gen",
            "-r",
            "U",
        ]
        with exclusive_file_lock(lock_path, 120.0) as lock:
            require_gdb_port_available("127.0.0.1", 1234)
            with tempfile.TemporaryDirectory(
                prefix="recursion94-m2-fatal-"
            ) as profile:
                isolated_runtime, runtime_evidence = clone_blastem_runtime(
                    emulator_directory, Path(profile)
                )
                standard_pad = write_standard_pad_profile(
                    default_config, Path(profile)
                )
                device_profile_sha = standard_pad["sha256"]
                standard_pad["session_isolation"] = (
                    apply_blastem_session_isolation(
                        Path(standard_pad["path"]),
                        Path(profile) / "session-data",
                    )
                )
                standard_pad["device_profile_sha256_before_paths"] = (
                    device_profile_sha
                )
                standard_pad["sha256"] = standard_pad[
                    "session_isolation"
                ]["config_sha256"]
                standard_pad["runtime_copy"] = runtime_evidence
                from tools.m2_screenshot_capture import (
                    verify_native_screenshot_binding,
                )

                screenshot_binding = verify_native_screenshot_binding(
                    Path(standard_pad["path"])
                )
                command = [
                    str(isolated_runtime / "blastem.exe"),
                    *launch_flags,
                    str(rom),
                ]
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=isolated_runtime,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    remote = GdbRemote(
                        "127.0.0.1",
                        1234,
                        args.timeout_seconds,
                        transcript,
                    )
                    if remote.request(f"Z0,{vblank:x},2") != "OK":
                        raise EvidenceFailure("VBlank breakpoint insertion failed")
                    for _index in range(4):
                        require_stop(remote.request("c"))
                    window = find_process_window(process.pid, 20.0)
                    for _name, virtual_key, scan_code in KEY_CHORD:
                        post_virtual_key(
                            window,
                            virtual_key=virtual_key,
                            scan_code=scan_code,
                            pressed=True,
                        )
                    if remote.request(f"Z0,{safe_exception:x},2") != "OK":
                        raise EvidenceFailure("SafeException breakpoint failed")
                    if remote.request(f"z0,{vblank:x},2") != "OK":
                        raise EvidenceFailure("VBlank breakpoint removal failed")
                    require_stop(remote.request("c"))
                    pre_exception = parse_debug_block(
                        remote.read_memory(DEBUG_BASE, DEBUG_BLOCK_BYTES)
                    )
                    trigger_value = remote.read_memory(TRIGGER_ADDRESS, 1)[0]
                    safe_pc = remote.request("p11").upper()

                    if remote.request(f"Z0,{halt:x},2") != "OK":
                        raise EvidenceFailure("fatal halt breakpoint failed")
                    if remote.request(f"z0,{safe_exception:x},2") != "OK":
                        raise EvidenceFailure("SafeException breakpoint removal failed")
                    require_stop(remote.request("c"))
                    fatal_debug = parse_debug_block(
                        remote.read_memory(DEBUG_BASE, DEBUG_BLOCK_BYTES)
                    )
                    halt_opcode_hex = remote.read_memory(halt, 6).hex().upper()
                    halted_samples = []
                    for _index in range(2):
                        if _index:
                            time.sleep(0.25)
                        halted_samples.append(
                            {
                                "observation": "halt-entry breakpoint",
                                "pc": remote.request("p11").upper(),
                                "debug": parse_debug_block(
                                    remote.read_memory(
                                        DEBUG_BASE, DEBUG_BLOCK_BYTES
                                    )
                                ),
                            }
                        )
                    next_instruction = halt + 4
                    if (
                        remote.request(
                            f"Z0,{next_instruction:x},2"
                        )
                        != "OK"
                    ):
                        raise EvidenceFailure(
                            "post-STOP breakpoint insertion failed"
                        )
                    if remote.request(f"z0,{halt:x},2") != "OK":
                        raise EvidenceFailure(
                            "fatal halt breakpoint removal failed"
                        )
                    remote.begin_continue()
                    time.sleep(0.75)
                    screenshot = capture_window(
                        root=root,
                        process_id=process.pid,
                        window=window,
                        screenshot_directory=Path(
                            standard_pad["session_isolation"][
                                "screenshot_path"
                            ]
                        ),
                        output=screenshot_path,
                        capture_started_ns=time.time_ns(),
                    )
                    stop_observation = remote.observe_stop_response(1.5)
                    stop_observation["next_breakpoint"] = next_instruction
                    write_commands = [
                        line
                        for line in transcript
                        if re.match(r"^> (?:M|X|P)", line)
                    ]
                    session = {
                        "process_id": process.pid,
                        "serialized_lock": lock,
                        "launch_flags": launch_flags,
                        "bindings": fatal_bindings,
                        "direction_bindings": direction_bindings,
                        "controller_profile": standard_pad,
                        "screenshot_binding": screenshot_binding,
                        "user_config_preflight": user_config_evidence,
                        "safe_exception": f"{safe_exception:08X}",
                        "halt": f"{halt:08X}",
                        "write_commands": write_commands,
                    }
                    report = fatal_report(
                        rom_sha256=rom_sha256,
                        pre_exception=pre_exception,
                        trigger_value=trigger_value,
                        fatal_sample={
                            "safe_entry_pc": safe_pc,
                            "halt_breakpoint_pc": f"{halt:08X}",
                            "debug": fatal_debug,
                        },
                        halted_samples=halted_samples,
                        safe_exception_address=safe_exception,
                        halt_address=halt,
                        halt_opcode_hex=halt_opcode_hex,
                        stop_observation=stop_observation,
                        screenshot=screenshot,
                        session=session,
                    )
                    if sha256_file(rom) != rom_sha256:
                        raise EvidenceFailure(
                            "Debug ROM changed during fatal capture"
                        )
                finally:
                    shutdown_emulator(remote, process, transcript)
                    remote = None
                    process = None
    except Exception as error:
        report = {
            "schema": 1,
            "scope": "Milestone2",
            "evidence_type": "blastem_real_input_guest_fatal_screen",
            "status": "fail",
            "rom_sha256": rom_sha256,
            "checks": [
                check(
                    "fatal_evidence_session",
                    False,
                    f"{type(error).__name__}: {error}",
                )
            ],
            "error": f"{type(error).__name__}: {error}",
        }
    finally:
        shutdown_emulator(remote, process, transcript)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(
            "\n".join(transcript) + ("\n" if transcript else ""),
            encoding="utf-8",
            newline="\n",
        )
        report["transcript"] = {
            "path": transcript_path.relative_to(root).as_posix(),
            "sha256": sha256_file(transcript_path),
        }
        write_json(report_path, report)
    passed = report["status"] == "pass"
    print(f"Milestone 2 fatal evidence: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
