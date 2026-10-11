#!/usr/bin/env python3
"""Capture a current, isolated normal visible frame for M1/M2 evidence."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.m2_evidence import (  # noqa: E402
    EvidenceFailure,
    apply_blastem_session_isolation,
    check,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    post_virtual_key,
    sha256_file,
    shutdown_emulator,
    verify_no_user_blastem_override,
    write_json,
    write_standard_pad_profile,
)


NATIVE_SCREENSHOT_KEY = ("P", 0x50, 0x19, False)


def launch_isolated_emulator(
    command: list[str], *, cwd: Path
) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    """Launch a fresh runtime after bounded transient scanner contention.

    Windows can briefly deny CreateProcess while a newly copied executable is
    still being inspected. Only WinError 5 is retried; every other launch
    failure remains immediate and fail-closed.
    """

    retry_delays = (0.0, 0.25, 0.5, 1.0, 2.0)
    failures: list[str] = []
    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except PermissionError as error:
            if getattr(error, "winerror", None) != 5:
                raise
            failures.append(f"{type(error).__name__}: {error}")
            if attempt == len(retry_delays):
                raise
            continue
        return process, {
            "status": "pass",
            "attempts": attempt,
            "access_denied_retries": len(failures),
            "retry_delays_seconds": list(retry_delays[:attempt]),
        }
    raise AssertionError("bounded launch loop exhausted without result")


def controlled_session_root(root: Path) -> Path:
    """Return the project-controlled parent for ephemeral emulator copies."""

    session_root = root / ".toolcache/evidence-sessions"
    session_root.mkdir(parents=True, exist_ok=True)
    return session_root


def verify_native_screenshot_binding(config_path: Path) -> dict[str, Any]:
    text = config_path.read_text("utf-8", errors="strict")
    bindings = re.findall(
        r"(?m)^\s*(\S+)\s+ui\.screenshot\s*$", text
    )
    if bindings != ["p"]:
        raise EvidenceFailure(
            "isolated screenshot profile must retain exactly P ui.screenshot"
        )
    return {
        "status": "pass",
        "key": NATIVE_SCREENSHOT_KEY[0],
        "virtual_key": NATIVE_SCREENSHOT_KEY[1],
        "scan_code": NATIVE_SCREENSHOT_KEY[2],
        "config_bindings": bindings,
    }


def normal_png_facts(path: Path) -> dict[str, Any]:
    """Decode RGBA or indexed BlastEm PNGs into the M2 palette metrics."""

    from tools.m4_visual_evidence import decode_png_rgba

    payload = path.read_bytes()
    try:
        width, height, rgba = decode_png_rgba(payload)
    except ValueError as error:
        raise EvidenceFailure(
            f"normal screenshot PNG is invalid: {error}"
        ) from error
    red_pixels = 0
    cyan_pixels = 0
    black_pixels = 0
    white_pixels = 0
    quantized_colors: set[tuple[int, int, int]] = set()
    for offset in range(0, len(rgba), 4):
        red, green, blue, alpha = rgba[offset : offset + 4]
        if alpha:
            quantized_colors.add((red // 16, green // 16, blue // 16))
        if alpha and red >= 128 and red >= green + 64 and red >= blue + 64:
            red_pixels += 1
        if (
            alpha
            and green >= 96
            and blue >= 96
            and red + 48 <= min(green, blue)
        ):
            cyan_pixels += 1
        if alpha and max(red, green, blue) <= 24:
            black_pixels += 1
        if (
            alpha
            and red >= 180
            and green >= 180
            and blue >= 180
            and max(red, green, blue) - min(red, green, blue) <= 32
        ):
            white_pixels += 1
    pixel_count = width * height
    return {
        "path": path.as_posix(),
        "bytes": len(payload),
        "sha256": sha256_file(path),
        "width": width,
        "height": height,
        "red_pixel_count": red_pixels,
        "red_pixel_ratio": red_pixels / pixel_count,
        "cyan_pixel_count": cyan_pixels,
        "cyan_pixel_ratio": cyan_pixels / pixel_count,
        "black_pixel_count": black_pixels,
        "black_pixel_ratio": black_pixels / pixel_count,
        "white_pixel_count": white_pixels,
        "quantized_color_count": len(quantized_colors),
    }


def capture_native_screenshot(
    *,
    root: Path,
    process_id: int,
    window: int,
    screenshot_directory: Path,
    output: Path,
    capture_started_ns: int,
) -> dict[str, Any]:
    """Request and consume one owned BlastEm-native framebuffer PNG."""

    from tools.m4_visual_evidence import (
        _consume_native_screenshot,
        _screenshot_directory_state,
        _window_identity,
    )

    identity = _window_identity(process_id, window)
    screenshot_directory.mkdir(parents=True, exist_ok=True)
    before = _screenshot_directory_state(screenshot_directory)
    _name, virtual_key, scan_code, extended = NATIVE_SCREENSHOT_KEY
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=True,
        extended=extended,
    )
    time.sleep(0.05)
    post_virtual_key(
        window,
        virtual_key=virtual_key,
        scan_code=scan_code,
        pressed=False,
        extended=extended,
    )
    native = _consume_native_screenshot(
        root=root,
        screenshot_directory=screenshot_directory,
        before_files=before,
        capture_started_ns=capture_started_ns,
        output=output,
        identity=identity,
        screenshot_key=NATIVE_SCREENSHOT_KEY,
        tool_output=(
            "Captured one fresh BlastEm-native framebuffer PNG via the "
            "isolated P ui.screenshot binding"
        ),
    )
    facts = normal_png_facts(output)
    facts["path"] = output.relative_to(root).as_posix()
    facts["tool_output"] = native["tool_output"]
    facts["window_ownership"] = native["window_ownership"]
    facts["mtime_ns"] = native["mtime_ns"]
    facts["captured_utc"] = native["captured_utc"]
    return facts


def normal_screenshot_report(
    *,
    evidence_type: str,
    rom_sha256: str,
    screenshot: dict[str, Any],
    capture_started_ns: int,
    expected_screenshot_path: str,
    session: dict[str, Any],
    rom_unchanged: bool,
    scope_limit: str,
) -> dict[str, Any]:
    red_ratio = float(screenshot.get("red_pixel_ratio", 1.0))
    cyan_ratio = float(screenshot.get("cyan_pixel_ratio", 0.0))
    black_ratio = float(screenshot.get("black_pixel_ratio", 0.0))
    white_pixels = int(screenshot.get("white_pixel_count", 0))
    color_count = int(screenshot.get("quantized_color_count", 0))
    legacy_diagnostic_palette = cyan_ratio >= 0.005
    gameplay_composition = (
        0.20 <= black_ratio < 0.90
        and white_pixels > 100
        and color_count >= 32
        and red_ratio < 0.10
    )
    checks = [
        check(
            "fully_decoded_png",
            screenshot.get("bytes", 0) > 1000
            and 320 <= int(screenshot.get("width", 0)) <= 1920
            and 224 <= int(screenshot.get("height", 0)) <= 1080,
            "decoded RGBA PNG "
            f"{screenshot.get('width')}x{screenshot.get('height')} "
            f"{screenshot.get('bytes')} bytes",
        ),
        check(
            "fresh_owned_capture",
            screenshot.get("path") == expected_screenshot_path
            and int(screenshot.get("mtime_ns", 0)) >= capture_started_ns,
            "path/mtime "
            f"{screenshot.get('path')}/"
            f"{screenshot.get('mtime_ns')} >= {capture_started_ns}",
        ),
        check(
            "owned_native_screenshot",
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
            == NATIVE_SCREENSHOT_KEY[0]
            and session.get("screenshot_binding", {}).get("status")
            == "pass",
            "PID-owned BlastEm ui.screenshot source and exact isolated "
            f"{NATIVE_SCREENSHOT_KEY[0]} binding",
        ),
        check(
            "normal_not_fatal",
            red_ratio < 0.10,
            f"red-dominant pixel ratio {red_ratio:.4f}; "
            "fatal rejection threshold 0.1000",
        ),
        check(
            "recognized_normal_palette",
            legacy_diagnostic_palette or gameplay_composition,
            "legacy cyan/gameplay-rich "
            f"{legacy_diagnostic_palette}/{gameplay_composition}; "
            f"cyan/red {cyan_ratio:.4f}/{red_ratio:.4f}",
        ),
        check(
            "nonblank_visible_composition",
            0.20 <= black_ratio < 0.98
            and white_pixels > 100
            and color_count >= 8,
            "black ratio/white pixels/quantized colors "
            f"{black_ratio:.4f}/{white_pixels}/{color_count}",
        ),
        check(
            "standard_three_button_profile",
            session.get("controller_profile", {}).get("status") == "pass"
            and session.get("controller_profile", {}).get(
                "port_1_assignment"
            )
            == "gamepad3.1",
            str(
                session.get("controller_profile", {}).get(
                    "detail", "controller profile evidence missing"
                )
            ),
        ),
        check(
            "portable_runtime_isolation",
            session.get("user_config_preflight", {}).get(
                "override_absent"
            )
            is True
            and session.get("controller_profile", {})
            .get("runtime_copy", {})
            .get("status")
            == "pass"
            and session.get("controller_profile", {})
            .get("session_isolation", {})
            .get("status")
            == "pass",
            "temporary bundled config and redirected writable paths",
        ),
        check(
            "rom_hash_stable",
            rom_unchanged,
            f"ROM remained SHA-256 {rom_sha256}",
        ),
    ]
    passed = all(item["status"] == "pass" for item in checks)
    return {
        "schema": 1,
        "scope": "Milestone2",
        "evidence_type": evidence_type,
        "status": "pass" if passed else "fail",
        "rom_sha256": rom_sha256,
        "rom": {
            "path": "build/recursion94-debug.bin",
            "sha256": rom_sha256,
        },
        "checks": checks,
        "screenshot": screenshot,
        "session": session,
        "scope_limit": scope_limit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--boot-report", type=Path)
    parser.add_argument("--m2-report", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--window-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--stabilize-seconds", type=float, default=2.0)
    args = parser.parse_args()

    root = args.root.resolve()
    rom = args.rom.resolve()
    screenshot_path = (
        args.screenshot.resolve()
        if args.screenshot
        else root / "artifacts/screenshots/m2-diagnostic.png"
    )
    boot_report_path = (
        args.boot_report.resolve()
        if args.boot_report
        else root / "artifacts/reports/boot-screenshot.json"
    )
    m2_report_path = (
        args.m2_report.resolve()
        if args.m2_report
        else root / "artifacts/reports/m2-screenshot.json"
    )
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    process: subprocess.Popen[bytes] | None = None
    transcript: list[str] = []
    rom_sha256 = ""
    reports: list[tuple[Path, dict[str, Any]]] = []
    try:
        if os.name != "nt":
            raise EvidenceFailure(
                "normal screenshot evidence requires Windows BlastEm"
            )
        if not 0.5 <= args.stabilize_seconds <= 10.0:
            raise ValueError("--stabilize-seconds must be within 0.5..10")
        rom_sha256 = sha256_file(rom)
        if args.expected_sha256 and (
            rom_sha256 != args.expected_sha256.strip().lower()
        ):
            raise EvidenceFailure("Debug ROM does not match expected SHA-256")
        emulator_directory = root / ".toolcache/blastem"
        default_config = emulator_directory / "default.cfg"
        user_config_evidence = verify_no_user_blastem_override()
        launch_flags = ["-m", "gen", "-r", "U"]
        with exclusive_file_lock(lock_path, 120.0) as lock:
            with ExitStack() as stack:
                profile = stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="recursion94-m2-screenshot-",
                        dir=controlled_session_root(root),
                    )
                )

                def stop_owned_process_before_profile_cleanup() -> None:
                    nonlocal process
                    if process is None:
                        return
                    try:
                        process.terminate()
                    except OSError as error:
                        transcript.append(
                            "! BlastEm pre-cleanup terminate failed: "
                            f"{type(error).__name__}: {error}"
                        )
                    shutdown_emulator(None, process, transcript)
                    process = None
                    # Windows may retain the terminated image section briefly
                    # after WaitForSingleObject reports exit.
                    time.sleep(0.5)

                # ExitStack is LIFO: stop the process before TemporaryDirectory
                # attempts to remove the owned executable.
                stack.callback(stop_owned_process_before_profile_cleanup)
                isolated_runtime, runtime_evidence = clone_blastem_runtime(
                    emulator_directory, Path(profile)
                )
                controller_profile = write_standard_pad_profile(
                    default_config, Path(profile)
                )
                device_profile_sha = controller_profile["sha256"]
                controller_profile["session_isolation"] = (
                    apply_blastem_session_isolation(
                        Path(controller_profile["path"]),
                        Path(profile) / "session-data",
                    )
                )
                controller_profile["device_profile_sha256_before_paths"] = (
                    device_profile_sha
                )
                controller_profile["sha256"] = controller_profile[
                    "session_isolation"
                ]["config_sha256"]
                controller_profile["runtime_copy"] = runtime_evidence
                screenshot_binding = verify_native_screenshot_binding(
                    Path(controller_profile["path"])
                )
                command = [
                    str(isolated_runtime / "blastem.exe"),
                    *launch_flags,
                    str(rom),
                ]
                process, launch_retry = launch_isolated_emulator(
                    command,
                    cwd=isolated_runtime,
                )
                window = find_process_window(
                    process.pid, args.window_timeout_seconds
                )
                time.sleep(args.stabilize_seconds)
                capture_started_ns = time.time_ns()
                screenshot = capture_native_screenshot(
                    root=root,
                    process_id=process.pid,
                    window=window,
                    screenshot_directory=Path(
                        controller_profile["session_isolation"][
                            "screenshot_path"
                        ]
                    ),
                    output=screenshot_path,
                    capture_started_ns=capture_started_ns,
                )
                screenshot["mtime_ns"] = screenshot_path.stat().st_mtime_ns
                screenshot["captured_utc"] = datetime.now(
                    timezone.utc
                ).isoformat()
                rom_unchanged = sha256_file(rom) == rom_sha256
                session = {
                    "process_id": process.pid,
                    "window_handle": window,
                    "launch_flags": launch_flags,
                    "launch_retry": launch_retry,
                    "screenshot_binding": screenshot_binding,
                    "serialized_lock": lock,
                    "user_config_preflight": user_config_evidence,
                    "controller_profile": controller_profile,
                }
                expected_path = screenshot_path.relative_to(root).as_posix()
                reports = [
                    (
                        boot_report_path,
                        normal_screenshot_report(
                            evidence_type=(
                                "isolated_blastem_current_visible_screenshot"
                            ),
                            rom_sha256=rom_sha256,
                            screenshot=screenshot,
                            capture_started_ns=capture_started_ns,
                            expected_screenshot_path=expected_path,
                            session=session,
                            rom_unchanged=rom_unchanged,
                            scope_limit=(
                                "This proves one current visible nonfatal boot "
                                "or gameplay frame, not release visual QA."
                            ),
                        ),
                    ),
                    (
                        m2_report_path,
                        normal_screenshot_report(
                            evidence_type=(
                                "isolated_blastem_m2_visible_screenshot"
                            ),
                            rom_sha256=rom_sha256,
                            screenshot=screenshot,
                            capture_started_ns=capture_started_ns,
                            expected_screenshot_path=expected_path,
                            session=session,
                            rom_unchanged=rom_unchanged,
                            scope_limit=(
                                "This proves the integrated M2/current gameplay "
                                "frame is visible and nonfatal; full gameplay "
                                "visual QA remains separate."
                            ),
                        ),
                    ),
                ]
    except Exception as error:
        failure = {
            "schema": 1,
            "scope": "Milestone2",
            "status": "fail",
            "rom_sha256": rom_sha256,
            "checks": [
                check(
                    "normal_screenshot_session",
                    False,
                    f"{type(error).__name__}: {error}",
                )
            ],
            "error": f"{type(error).__name__}: {error}",
        }
        reports = [
            (boot_report_path, {**failure, "evidence_type": "boot_screenshot"}),
            (m2_report_path, {**failure, "evidence_type": "m2_screenshot"}),
        ]
    finally:
        shutdown_emulator(None, process, transcript)
        for path, report in reports:
            write_json(path, report)
    passed = bool(reports) and all(
        report["status"] == "pass" for _path, report in reports
    )
    print(
        "Current normal screenshot evidence: "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
