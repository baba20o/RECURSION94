"""Final hash-bound BlastEm GDB RAM audit for Milestone 1."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from pathlib import Path


def packet(payload: str) -> bytes:
    raw = payload.encode("ascii")
    return b"$" + raw + f"#{sum(raw) & 0xFF:02x}".encode("ascii")


class Remote:
    def __init__(self, timeout: float, transcript: list[str]) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                self.sock = socket.create_connection(("127.0.0.1", 1234), timeout=1.0)
                self.sock.settimeout(timeout)
                break
            except OSError as error:
                last_error = error
                time.sleep(0.1)
        else:
            raise TimeoutError(f"could not connect to BlastEm: {last_error}")
        self.buffer = bytearray()
        self.transcript = transcript

    def _byte(self) -> int:
        if not self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("BlastEm closed the GDB socket")
            self.buffer.extend(chunk)
        return self.buffer.pop(0)

    def response(self) -> str:
        # BlastEm acknowledges with '+' before sending the response packet.
        while self._byte() != ord("$"):
            pass
        payload = bytearray()
        while True:
            byte = self._byte()
            if byte == ord("#"):
                break
            payload.append(byte)
        claimed = bytes((self._byte(), self._byte()))
        actual = f"{sum(payload) & 0xFF:02x}".encode("ascii")
        if claimed.lower() != actual:
            self.sock.sendall(b"-")
            raise RuntimeError(f"bad RSP checksum {claimed!r}, expected {actual!r}")
        self.sock.sendall(b"+")
        return payload.decode("ascii")

    def request(self, payload: str) -> str:
        self.transcript.append(f"> {payload}")
        self.sock.sendall(packet(payload))
        result = self.response()
        self.transcript.append(f"< {result}")
        return result

    def kill(self) -> None:
        self.transcript.append("> k")
        self.sock.sendall(packet("k"))

    def close(self) -> None:
        self.sock.close()


def decode(raw: bytes) -> dict[str, int | str]:
    u16 = lambda offset: int.from_bytes(raw[offset : offset + 2], "big")
    u32 = lambda offset: int.from_bytes(raw[offset : offset + 4], "big")
    return {
        "raw_hex": raw.hex().upper(),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "magic_hex": f"{u32(0):08X}",
        "abi_version": u16(0x04),
        "abi_size": u16(0x06),
        "sequence": u32(0x08),
        "heartbeat": u32(0x0C),
        "frame_counter": u32(0x10),
        "frame_token": u16(0x14),
        "fatal_code": u16(0x16),
        "boot_stage": u16(0x18),
        "hardware_flags": u16(0x1A),
        "last_vdp_status": u16(0x1C),
    }


def strictly_increasing(values: list[int]) -> bool:
    return all(later > earlier for earlier, later in zip(values, values[1:]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    transcript: list[str] = []
    rom_bytes = args.rom.read_bytes()
    rom_sha256 = hashlib.sha256(rom_bytes).hexdigest()
    report: dict[str, object] = {
        "schema": 1,
        "status": "error",
        "scope": "Windows BlastEm GDB RAM audit only; screenshot, headless, and version helpers excluded",
        "rom": {
            "path": "build/recursion94-debug.bin",
            "sha256": rom_sha256,
            "header_checksum_hex": rom_bytes[0x18E:0x190].hex().upper(),
        },
        "emulator": {
            "name": "BlastEm",
            "pinned_version": "0.6.3-pre-732f5689d438",
            "interface": "Windows GDB remote at 127.0.0.1:1234",
            "launch_flags": ["-D", "-g", "-m", "gen", "-r", "U"],
            "pid": args.pid,
        },
        "breakpoint_hex": "000003B6",
        "debug_block_hex": "00FFF000",
        "sample_count": 3,
    }
    remote: Remote | None = None
    try:
        if rom_sha256 != args.expected_sha256.lower():
            raise RuntimeError(
                f"ROM hash mismatch: {rom_sha256} != {args.expected_sha256.lower()}"
            )
        remote = Remote(100.0, transcript)
        inserted = remote.request("Z0,3b6,2")
        if inserted != "OK":
            raise RuntimeError(f"breakpoint insertion returned {inserted!r}")
        remote.sock.settimeout(20.0)

        samples: list[dict[str, int | str]] = []
        for index in range(3):
            stop = remote.request("c")
            if stop != "S05" and not stop.startswith("T05"):
                raise RuntimeError(f"unexpected stop packet {stop!r}")
            registers = remote.request("g")
            memory_hex = remote.request("mfff000,20")
            sample = decode(bytes.fromhex(memory_hex))
            sample["index"] = index
            sample["stop"] = stop
            sample["pc_hex"] = registers[-8:].upper()
            samples.append(sample)

        removed = remote.request("z0,3b6,2")
        if removed != "OK":
            raise RuntimeError(f"breakpoint removal returned {removed!r}")

        sequences = [int(sample["sequence"]) for sample in samples]
        heartbeats = [int(sample["heartbeat"]) for sample in samples]
        frames = [int(sample["frame_counter"]) for sample in samples]
        tokens = [int(sample["frame_token"]) for sample in samples]
        checks = {
            "rom_sha256": rom_sha256 == args.expected_sha256.lower(),
            "header_checksum": report["rom"]["header_checksum_hex"] == "1FE4",
            "sample_count": len(samples) == 3,
            "stops": all(sample["stop"] == "S05" for sample in samples),
            "pc": all(sample["pc_hex"] == "000003B6" for sample in samples),
            "magic": all(sample["magic_ascii"] == "R94D" for sample in samples),
            "abi": all(
                sample["abi_version"] == 1 and sample["abi_size"] == 0x400
                for sample in samples
            ),
            "boot_stage": all(sample["boot_stage"] == 4 for sample in samples),
            "fatal_clear": all(sample["fatal_code"] == 0 for sample in samples),
            "hardware_flags_clear": all(sample["hardware_flags"] == 0 for sample in samples),
            "seqlock_even": all(sequence % 2 == 0 for sequence in sequences),
            "sequence_strict": strictly_increasing(sequences),
            "heartbeat_strict": strictly_increasing(heartbeats),
            "frame_strict": strictly_increasing(frames),
            "token_strict": strictly_increasing(tokens),
            "counter_equality": all(
                heartbeat == frame == token
                for heartbeat, frame, token in zip(heartbeats, frames, tokens)
            ),
            "sequence_relation": all(
                sequence == heartbeat * 2
                for sequence, heartbeat in zip(sequences, heartbeats)
            ),
            "expected_progression": heartbeats == [0, 1, 2],
        }
        report["samples"] = samples
        report["final_sample"] = samples[-1]
        report["checks"] = checks
        report["status"] = "pass" if all(checks.values()) else "fail"
        return 0 if report["status"] == "pass" else 2
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        if remote is not None:
            try:
                remote.kill()
            except Exception as error:
                transcript.append(f"! kill failed: {type(error).__name__}: {error}")
            remote.close()
        transcript_path = args.out / "gdb_transcript.log"
        transcript_path.write_text("\n".join(transcript) + "\n", encoding="utf-8")
        report["transcript"] = "artifacts/m1_final/gdb_transcript.log"
        report["transcript_sha256"] = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
        (args.out / "runtime_heartbeat_pass.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    raise SystemExit(main())
