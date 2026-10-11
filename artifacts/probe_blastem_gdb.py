#!/usr/bin/env python3
"""Small BlastEm GDB-remote probe used for Milestone 1 evidence."""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path


def packet(payload: str) -> bytes:
    encoded = payload.encode("ascii")
    checksum = sum(encoded) & 0xFF
    return b"$" + encoded + b"#" + f"{checksum:02x}".encode("ascii")


class Remote:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                self.sock = socket.create_connection((host, port), timeout=1.0)
                self.sock.settimeout(timeout)
                break
            except OSError as error:
                last_error = error
                time.sleep(0.1)
        else:
            raise RuntimeError(f"could not connect to {host}:{port}: {last_error}")
        self.buffer = bytearray()

    def close(self) -> None:
        self.sock.close()

    def _read_byte(self) -> int:
        if not self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("BlastEm closed the GDB connection")
            self.buffer.extend(chunk)
        return self.buffer.pop(0)

    def response(self) -> str:
        while self._read_byte() != ord("$"):
            pass
        payload = bytearray()
        while True:
            byte = self._read_byte()
            if byte == ord("#"):
                break
            payload.append(byte)
        claimed = bytes((self._read_byte(), self._read_byte()))
        actual = f"{sum(payload) & 0xFF:02x}".encode("ascii")
        if claimed.lower() != actual:
            raise RuntimeError(
                f"bad GDB packet checksum: claimed {claimed!r}, actual {actual!r}"
            )
        self.sock.sendall(b"+")
        return payload.decode("ascii")

    def request(self, payload: str) -> str:
        self.sock.sendall(packet(payload))
        return self.response()

    def continue_to_break(self) -> str:
        return self.request("c")

    def kill(self) -> None:
        self.sock.sendall(packet("k"))


def decode_debug_block(raw: bytes) -> dict[str, int | str]:
    def u16(offset: int) -> int:
        return int.from_bytes(raw[offset : offset + 2], "big")

    def u32(offset: int) -> int:
        return int.from_bytes(raw[offset : offset + 4], "big")

    return {
        "raw_hex": raw.hex(),
        "magic_ascii": raw[0:4].decode("ascii", errors="replace"),
        "magic": u32(0x00),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--breakpoint", type=lambda value: int(value, 0), default=0x3B6)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    remote = Remote(args.host, args.port, args.timeout)
    samples: list[dict[str, int | str]] = []
    try:
        inserted = remote.request(f"Z0,{args.breakpoint:x},2")
        if inserted != "OK":
            raise RuntimeError(f"breakpoint insertion returned {inserted!r}")
        for index in range(args.samples):
            stop = remote.continue_to_break()
            if stop not in {"S05"} and not stop.startswith("T05"):
                raise RuntimeError(f"unexpected stop packet {stop!r}")
            registers = remote.request("g")
            memory_hex = remote.request("mfff000,20")
            decoded = decode_debug_block(bytes.fromhex(memory_hex))
            decoded["sample"] = index
            decoded["stop"] = stop
            decoded["pc"] = int(registers[-8:], 16)
            samples.append(decoded)
        removed = remote.request(f"z0,{args.breakpoint:x},2")
        if removed != "OK":
            raise RuntimeError(f"breakpoint removal returned {removed!r}")
        remote.kill()
    finally:
        remote.close()

    report = {
        "schema": 1,
        "emulator_interface": "BlastEm GDB remote",
        "breakpoint": args.breakpoint,
        "samples": samples,
        "checks": {
            "magic": all(sample["magic_ascii"] == "R94D" for sample in samples),
            "abi": all(sample["abi_version"] == 1 for sample in samples),
            "running_stage": all(sample["boot_stage"] == 4 for sample in samples),
            "no_fatal": all(sample["fatal_code"] == 0 for sample in samples),
            "heartbeat_advances": all(
                int(later["heartbeat"]) > int(earlier["heartbeat"])
                for earlier, later in zip(samples, samples[1:])
            ),
            "frame_advances": all(
                int(later["frame_counter"]) > int(earlier["frame_counter"])
                for earlier, later in zip(samples, samples[1:])
            ),
        },
    }
    report["status"] = (
        "pass" if all(report["checks"].values()) else "fail"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
