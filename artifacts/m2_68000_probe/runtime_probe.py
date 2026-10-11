"""Independent Windows BlastEm GDB probe for the in-progress 68000 stream."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.m2_evidence import (
    GdbRemote,
    find_process_window,
    parse_debug_block,
    parse_sat_entry,
    post_window_key,
    require_stop,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--breakpoint", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--inject-right", action="store_true")
    args = parser.parse_args()

    root = ROOT
    emulator = root / ".toolcache" / "blastem" / "blastem.exe"
    profile = root / "artifacts" / "m2_68000_probe" / "localappdata"
    profile.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(profile)
    process = subprocess.Popen(
        [
            str(emulator),
            "-D",
            "-g",
            "-m",
            "gen",
            "-r",
            "U",
            str(args.rom.resolve()),
        ],
        cwd=emulator.parent,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    transcript: list[str] = []
    remote: GdbRemote | None = None
    samples: list[dict[str, object]] = []
    right_held = False
    try:
        remote = GdbRemote("127.0.0.1", 1234, 60.0, transcript)
        inserted = remote.request(f"Z0,{args.breakpoint:x},2")
        if inserted != "OK":
            raise RuntimeError(f"breakpoint insertion returned {inserted!r}")
        window = (
            find_process_window(process.pid, timeout=20.0)
            if args.inject_right
            else 0
        )
        for index in range(args.samples):
            if args.inject_right and index == 2:
                post_window_key(window, "right", True)
                right_held = True
            if args.inject_right and index == 4:
                post_window_key(window, "right", False)
                right_held = False
            require_stop(remote.request("c"))
            debug = parse_debug_block(remote.read_memory(0x00FFF000, 0x48))
            sat = parse_sat_entry(remote.read_memory(0x00FF8000, 8))
            samples.append({"index": index, "debug": debug, "sat": sat})
        remote.request(f"z0,{args.breakpoint:x},2")
        result = {
            "status": "pass",
            "breakpoint": args.breakpoint,
            "samples": samples,
            "transcript": transcript,
        }
        return_code = 0
    except Exception as error:
        result = {
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
            "samples": samples,
            "transcript": transcript,
        }
        return_code = 1
    finally:
        if right_held:
            try:
                post_window_key(window, "right", False)
            except Exception:
                pass
        if remote is not None:
            try:
                remote.kill()
            except Exception:
                pass
            remote.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
