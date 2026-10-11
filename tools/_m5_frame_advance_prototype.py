#!/usr/bin/env python3
"""Temporary native prototype for the M5 UI frame-advance cadence."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import m5_b03_runtime_evidence as b03
from tools.m2_evidence import (
    GdbRemote,
    apply_blastem_session_isolation,
    clone_blastem_runtime,
    exclusive_file_lock,
    find_process_window,
    parse_vasm_symbols,
    post_virtual_key,
    require_gdb_port_available,
    require_stop,
    write_standard_pad_profile,
)
from tools.m3_runtime_evidence import (
    ENGINE_CONTROL_BASE,
    ENGINE_CONTROL_USED_BYTES,
    parse_engine_control,
)
from tools.m4_budget_evidence import (
    DEBUG_BASE,
    DEBUG_BUDGET_BYTES,
    parse_debug_budget,
)


F8_VIRTUAL_KEY = 0x77
F8_SCAN_CODE = 0x42


def window_title(window: int) -> str:
    user32 = ctypes.windll.user32
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    )
    user32.GetWindowTextW.restype = ctypes.c_int
    length = user32.GetWindowTextLengthW(window)
    buffer = ctypes.create_unicode_buffer(max(length + 1, 2))
    copied = user32.GetWindowTextW(window, buffer, len(buffer))
    if copied <= 0:
        return ""
    return buffer.value


def set_window_sentinel(
    window: int, sentinel: str, transcript: list[str]
) -> None:
    user32 = ctypes.windll.user32
    user32.SetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    user32.SetWindowTextW.restype = wintypes.BOOL
    if not user32.SetWindowTextW(window, sentinel):
        raise RuntimeError(f"SetWindowTextW failed for {sentinel!r}")
    observed = window_title(window)
    if observed != sentinel:
        raise RuntimeError(
            f"sentinel readback {observed!r} != {sentinel!r}"
        )
    transcript.append(f"# ui.sentinel set title={sentinel!r}")


def wait_emulator_pause_overwrite(
    window: int,
    sentinel: str,
    timeout: float,
    transcript: list[str],
) -> str:
    started = time.monotonic()
    samples = 0
    last = ""
    while time.monotonic() - started < timeout:
        samples += 1
        last = window_title(window)
        if last != sentinel and last.upper().endswith(" - PAUSED"):
            elapsed = time.monotonic() - started
            transcript.append(
                "# ui.pause_overwrite pass "
                f"sentinel={sentinel!r} elapsed={elapsed:.6f} "
                f"samples={samples} title={last!r}"
            )
            return last
        time.sleep(0.002)
    raise TimeoutError(
        "BlastEm did not overwrite sentinel with PAUSED in "
        f"{timeout}s; sentinel={sentinel!r}, last={last!r}"
    )


def tap_f8(window: int, transcript: list[str], purpose: str) -> None:
    transcript.append(f"# ui.advance purpose={purpose}")
    post_virtual_key(
        window,
        virtual_key=F8_VIRTUAL_KEY,
        scan_code=F8_SCAN_CODE,
        pressed=True,
    )
    post_virtual_key(
        window,
        virtual_key=F8_VIRTUAL_KEY,
        scan_code=F8_SCAN_CODE,
        pressed=False,
    )


def main() -> int:
    root = Path.cwd()
    transcript: list[str] = []
    rom = root / "build/level-B03/recursion94-debug.bin"
    symbol_path = root / "build/level-B03/recursion94-debug.sym"
    symbols = parse_vasm_symbols(symbol_path)
    process = None
    remote = None
    window = None
    pressed: set[str] = set()
    events: list[str] = []
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    with exclusive_file_lock(lock_path, 120.0):
        with b03._owned_session_directory(
            prefix="recursion94-m5-budget-ui-prototype-",
            transcript=transcript,
        ) as profile:
            isolated_runtime, _runtime_copy = clone_blastem_runtime(
                root / ".toolcache/blastem", profile
            )
            controller = write_standard_pad_profile(
                root / ".toolcache/blastem/default.cfg", profile
            )
            apply_blastem_session_isolation(
                Path(controller["path"]), profile / "session-data"
            )
            require_gdb_port_available("127.0.0.1", 1234)
            command = [
                str(isolated_runtime / "blastem.exe"),
                *b03.LAUNCH_FLAGS,
                str(rom),
            ]
            try:
                process = b03._launch_owned_emulator(
                    command, cwd=isolated_runtime, transcript=transcript
                )
                print(
                    "launched "
                    f"pid={process.pid} poll={process.poll()!r} "
                    f"command={command!r}",
                    flush=True,
                )
                remote = GdbRemote(
                    "127.0.0.1", 1234, 120.0, transcript
                )
                vblank = symbols["VBlankInterrupt"]
                post_step = symbols["MainLoopPostGameplayStep"]
                if remote.request(f"Z0,{vblank:x},2") != "OK":
                    raise RuntimeError("VBlank breakpoint insert failed")
                require_stop(remote.request("c"))
                window = find_process_window(process.pid, 20.0)
                initial_title = window_title(window)
                b03._set_gameplay_mask(
                    window, b03.ATTEMPT_0_INPUTS[0], pressed, events
                )
                if remote.request(f"z0,{vblank:x},2") != "OK":
                    raise RuntimeError("VBlank breakpoint remove failed")
                if remote.request(f"Z0,{post_step:x},2") != "OK":
                    raise RuntimeError("POST breakpoint insert failed")

                origin_sentinel = "R94-BUDGET-PROTOTYPE-ORIGIN"
                set_window_sentinel(
                    window, origin_sentinel, transcript
                )
                tap_f8(window, transcript, "prime_frame_end_pause")
                remote.begin_continue()
                first_pause = wait_emulator_pause_overwrite(
                    window, origin_sentinel, 5.0, transcript
                )
                print(
                    f"initial_title={initial_title!r} "
                    f"first_pause={first_pause!r}",
                    flush=True,
                )

                for total in range(1, 726):
                    attempt = 0 if total <= 720 else 1
                    expected_tick = total if total <= 719 else (
                        0 if total == 720 else total - 720
                    )
                    tap_f8(
                        window,
                        transcript,
                        f"authoritative_total_{total}",
                    )
                    observed = remote.observe_stop_response(5.0)
                    if observed["timed_out"]:
                        raise TimeoutError(
                            f"POST {total} timed out; "
                            f"title={window_title(window)!r}"
                        )
                    require_stop(str(observed["response"]))
                    pc = b03._read_register_u32(
                        remote, 0x11, label=f"total {total}"
                    )
                    if pc != post_step:
                        raise RuntimeError(
                            f"POST {total} PC ${pc:08X} != ${post_step:08X}"
                        )
                    control = parse_engine_control(
                        b03._read_region(
                            remote,
                            ENGINE_CONTROL_BASE,
                            ENGINE_CONTROL_USED_BYTES,
                        )
                    )
                    debug = parse_debug_budget(
                        b03._read_region(
                            remote, DEBUG_BASE, DEBUG_BUDGET_BYTES
                        )
                    )
                    expected_attempt = 0 if total < 720 else 1
                    if total > 720:
                        expected_attempt = 1
                    if total < 720:
                        expected_tick = total
                    elif total == 720:
                        expected_tick = 0
                    else:
                        expected_tick = total - 720
                    title_at_post = window_title(window)
                    row = (
                        total,
                        control["attempt"],
                        control["tick"],
                        debug["frame_counter"],
                        debug["frame_token"],
                        debug["vblank_overrun_count"],
                        control["input_held"],
                        title_at_post,
                    )
                    if (
                        control["attempt"] != expected_attempt
                        or control["tick"] != expected_tick
                        or debug["frame_counter"] != total
                        or debug["frame_token"] != 0
                        or debug["vblank_overrun_count"] != 0
                    ):
                        raise AssertionError(
                            "cadence invariant failed: "
                            f"row={row!r}, expected attempt/tick="
                            f"{expected_attempt}/{expected_tick}"
                        )
                    if total in {
                        1,
                        45,
                        46,
                        47,
                        67,
                        719,
                        720,
                        721,
                        725,
                    }:
                        print(f"row={row!r}", flush=True)

                    if total < 725:
                        if total < 720:
                            desired_next = b03.ATTEMPT_0_INPUTS[total]
                        elif total == 720:
                            desired_next = b03.ATTEMPT_1_INPUTS[0]
                        else:
                            desired_next = b03.ATTEMPT_1_INPUTS[
                                total - 720
                            ]
                        b03._set_gameplay_mask(
                            window, desired_next, pressed, events
                        )
                        sentinel = (
                            f"R94-BUDGET-PROTOTYPE-POST-{total:04d}"
                        )
                        set_window_sentinel(
                            window, sentinel, transcript
                        )
                        remote.begin_continue()
                        wait_emulator_pause_overwrite(
                            window, sentinel, 5.0, transcript
                        )

                print(
                    "prototype_pass "
                    "authoritative_advances=725 ui_advance_pulses=726 "
                    "sentinel_handshakes=725 "
                    f"input_events={events!r}",
                    flush=True,
                )
                if remote.request(f"z0,{post_step:x},2") != "OK":
                    raise RuntimeError("POST breakpoint remove failed")
            finally:
                if window is not None:
                    for name, virtual_key, scan_code, extended, _bit in b03.KEYS:
                        if name in pressed:
                            try:
                                b03._post_key(
                                    window,
                                    (name, virtual_key, scan_code, extended),
                                    pressed=False,
                                )
                            except Exception:
                                pass
                b03._shutdown_owned_emulator(remote, process, transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
