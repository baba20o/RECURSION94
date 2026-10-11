from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--expected-hits", type=int, default=12)
    parser.add_argument("--cap", type=int, default=16)
    args = parser.parse_args()
    if not 1 <= args.expected_hits <= args.cap <= 16:
        raise RuntimeError("require 1 <= expected hits <= cap <= 16")

    root = args.root.resolve()
    sys.path.insert(0, str(root))

    from tools.m2_evidence import (
        GdbRemote,
        apply_blastem_session_isolation,
        clone_blastem_runtime,
        exclusive_file_lock,
        parse_vasm_symbols,
        require_gdb_port_available,
        require_stop,
        shutdown_emulator,
        write_standard_pad_profile,
    )
    from tools.m3_runtime_evidence import debugger_write_packets
    from tools.m4_budget_evidence import (
        FORBIDDEN_BREAKPOINT_SYMBOLS,
        TERMINAL_BREAKPOINT_SYMBOL,
        analyze_transcript,
        capture_stopped_snapshot,
        symbol_layout_errors,
    )
    from tools.m4_audio_evidence import (
        AUDIO_SHADOW_BASE,
        AUDIO_SHADOW_EXTENDED_BYTES,
        parse_audio_shadow,
    )

    if os.name != "nt":
        raise RuntimeError("native Windows Python required")
    rom = root / "build/recursion94-debug.bin"
    symbols_path = root / "build/recursion94-debug.sym"
    listing_path = root / "build/recursion94-debug.lst"
    emulator_directory = root / ".toolcache/blastem"
    default_config = emulator_directory / "default.cfg"
    expected = args.expected_sha256.lower()
    if sha256(rom) != expected:
        raise RuntimeError("ROM hash mismatch")

    symbols = parse_vasm_symbols(symbols_path)
    errors = symbol_layout_errors(symbols)
    if errors:
        raise RuntimeError(f"state-symbol layout drift: {errors}")

    listing_lines = listing_path.read_text(
        "utf-8", errors="strict"
    ).splitlines()
    backlog_address = None
    for index, line in enumerate(listing_lines):
        if line.rstrip().endswith("162: .backlog:"):
            for following in listing_lines[index + 1 : index + 5]:
                match = re.match(r"^00:([0-9A-F]{8})\s", following)
                if match:
                    backlog_address = int(match.group(1), 16)
                    break
            break
    if backlog_address is None:
        raise RuntimeError("cannot derive renderer .backlog address")

    transcript: list[str] = []
    sessions: list[dict] = []
    lock_path = root / ".toolcache/locks/blastem-gdb.lock"
    with exclusive_file_lock(lock_path, 600):
        for number in (1,):
            start = len(transcript)
            session_id = f"session_{number}"
            transcript.append(f"# {session_id} begin")
            process = None
            remote = None
            observations = []
            with tempfile.TemporaryDirectory(
                prefix=f"r94-backlog-diagnostic-{session_id}-"
            ) as profile_text:
                profile = Path(profile_text)
                isolated_runtime, runtime_copy = clone_blastem_runtime(
                    emulator_directory, profile
                )
                controller_profile = write_standard_pad_profile(
                    default_config, profile
                )
                controller_profile["session_isolation"] = (
                    apply_blastem_session_isolation(
                        Path(controller_profile["path"]),
                        profile / "session-data",
                    )
                )
                require_gdb_port_available("127.0.0.1", 1234)
                try:
                    process = subprocess.Popen(
                        [
                            str(isolated_runtime / "blastem.exe"),
                            "-D",
                            "-g",
                            "-m",
                            "gen",
                            "-r",
                            "U",
                            str(rom),
                        ],
                        cwd=isolated_runtime,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    remote = GdbRemote(
                        "127.0.0.1", 1234, 90.0, transcript
                    )
                    if remote.request(
                        f"Z0,{backlog_address:x},2"
                    ) != "OK":
                        raise RuntimeError(
                            "backlog breakpoint insertion failed"
                        )
                    for hit_index in range(args.expected_hits):
                        require_stop(remote.request("c"))
                        observation = capture_stopped_snapshot(remote)
                        audio_first = remote.read_memory(
                            AUDIO_SHADOW_BASE,
                            AUDIO_SHADOW_EXTENDED_BYTES,
                        )
                        audio_second = remote.read_memory(
                            AUDIO_SHADOW_BASE,
                            AUDIO_SHADOW_EXTENDED_BYTES,
                        )
                        if audio_first != audio_second:
                            raise RuntimeError(
                                f"audio shadow drift at hit {hit_index + 1}"
                            )
                        observation["audio"] = parse_audio_shadow(audio_first)
                        observation["audio"]["read_consistency"] = "pass"
                        observation["hit_index"] = hit_index + 1
                        observation[
                            "frame_token_before_main_decrement_derived"
                        ] = observation["debug"]["frame_token"] + 1
                        observations.append(observation)
                finally:
                    shutdown_emulator(remote, process, transcript)
                    transcript.append(f"# {session_id} end")
            if len(observations) != args.expected_hits:
                raise RuntimeError("missing diagnostic observations")
            lines = transcript[start:]
            sessions.append(
                {
                    "session_id": session_id,
                    "process_id": process.pid,
                    "runtime_copy": runtime_copy,
                    "controller_profile": controller_profile,
                    "transcript_policy": analyze_transcript(
                        lines,
                        terminal_address=backlog_address,
                        forbidden_addresses={
                            name: symbols[name]
                            for name in (
                                *FORBIDDEN_BREAKPOINT_SYMBOLS,
                                TERMINAL_BREAKPOINT_SYMBOL,
                            )
                        },
                    ),
                    "observations_before_increment": observations,
                    "_lines": lines,
                }
            )

    args.trace.write_text(
        "\n".join(transcript) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "status": "pass",
        "scope": "read-only backlog cadence diagnostic",
        "rom_sha256": sha256(rom),
        "symbol_map_sha256": sha256(symbols_path),
        "listing_sha256": sha256(listing_path),
        "emulator_sha256": sha256(
            emulator_directory / "blastem.exe"
        ),
        "default_config_sha256": sha256(default_config),
        "backlog_address": f"{backlog_address:08X}",
        "expected_hits": args.expected_hits,
        "cap": args.cap,
        "sessions": [
            {
                key: value
                for key, value in session.items()
                if not key.startswith("_")
            }
            for session in sessions
        ],
        "debugger_write_packets": debugger_write_packets(transcript),
    }
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
