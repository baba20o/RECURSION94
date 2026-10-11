#!/usr/bin/env python3
"""Observe foreground changes during the unchanged B03 input transport.

Diagnostic only: does not repair, retry, reassert, or change guest state.
Use explicit diagnostic output paths for the imported runtime collector.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import m5_b03_runtime_evidence as runtime


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("Native Windows diagnostic required")
    output = Path(sys.argv[1]).resolve()
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    transitions: list[dict[str, object]] = []
    calls = 0
    previous: tuple[int, int] | None = None
    original = runtime._set_gameplay_mask

    def observe(window, desired, pressed, events):
        nonlocal calls, previous
        calls += 1
        foreground = int(user32.GetForegroundWindow() or 0)
        current = (window, foreground)
        if current != previous:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(owner))
            item = {
                "mask_call": calls,
                "utc": datetime.now(timezone.utc).isoformat(),
                "owned_window": window,
                "foreground_window": foreground,
                "foreground_pid": owner.value,
                "owned_is_foreground": window == foreground,
                "desired_mask": desired,
                "tracked_pressed": sorted(pressed),
            }
            transitions.append(item)
            print("Foreground observation: " + json.dumps(item), flush=True)
            previous = current
        return original(window, desired, pressed, events)

    runtime._set_gameplay_mask = observe
    try:
        return runtime.main()
    finally:
        runtime._set_gameplay_mask = original
        runtime.write_json(output, {
            "schema": "r94-m5-input-focus-diagnostic-v1",
            "evidence_type": "passive_host_foreground_observation",
            "guest_writes": False,
            "input_transport_changed": False,
            "mask_calls": calls,
            "transitions": transitions,
            "script_sha256": runtime.sha256_file(Path(__file__)),
            "collector_sha256": runtime.sha256_file(Path(runtime.__file__)),
        })


if __name__ == "__main__":
    raise SystemExit(main())
