#!/usr/bin/env python3
"""Verify the pinned project-local toolchain without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.bootstrap_python_packages import provision  # noqa: E402
from tools.embedded_python import attest  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], root: Path) -> str:
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): "
            f"{subprocess.list2cmdline(command)}\n{result.stdout}"
        )
    return result.stdout.strip()


def verify(root: Path, *, verify_archives: bool) -> dict[str, Any]:
    lock_path = root / "tools" / "toolchain.lock.json"
    installed_path = root / ".toolcache" / "installed.json"
    lock = json.loads(lock_path.read_text("utf-8"))
    installed = json.loads(installed_path.read_text("utf-8"))
    issues: list[str] = []
    tools = lock.get("tools", {})
    expected_names = {"python", "zig", "vasm", "sjasmplus", "blastem"}
    if lock.get("schema") != 1 or set(tools) != expected_names:
        issues.append("toolchain lock schema/tool set is not the frozen Milestone 0 set")
    if installed.get("lock_sha256") != sha256(lock_path):
        issues.append("installed toolchain was not produced from the current lock file")
    for name, metadata in tools.items():
        if not metadata.get("version") or not metadata.get("url"):
            issues.append(f"{name} is missing version or source URL")
        checksum = metadata.get("sha256", "")
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            issues.append(f"{name} has an invalid pinned SHA-256")
        if not metadata.get("license"):
            issues.append(f"{name} is missing license metadata")

    bin_root = root / ".toolcache" / "bin"
    vasm = bin_root / "vasmm68k_mot.exe"
    sjasmplus = bin_root / "sjasmplus.exe"
    blastem = root / ".toolcache" / "blastem" / "blastem.exe"
    python = root / ".toolcache" / "python" / "python.exe"
    for path in (vasm, sjasmplus, blastem, python):
        if not path.is_file():
            issues.append(f"missing installed executable: {path}")

    python_packages: dict[str, Any] = {}
    python_baseline: dict[str, Any] = {}
    try:
        python_baseline = attest(root)
        python_packages = provision(root, verify_only=True)
    except (OSError, ValueError, RuntimeError) as error:
        issues.append(f"pinned Python validation packages: {error}")

    versions: dict[str, list[str] | str] = {}
    if not issues:
        versions["python"] = run([str(python), "--version"], root)
        versions["vasm"] = run([str(vasm), "-v"], root).splitlines()
        versions["sjasmplus"] = run(
            [str(sjasmplus), "--nologo", "--version"], root
        ).splitlines()
        if "Python 3.12.10" not in versions["python"]:
            issues.append(f"unexpected portable Python identity: {versions['python']}")
        if not any("vasm 2.0f" in line for line in versions["vasm"]):
            issues.append("unexpected vasm identity")
        if not any("1.23.1" in line for line in versions["sjasmplus"]):
            issues.append("unexpected sjasmplus identity")

    smoke_root = root / "artifacts" / "reports" / "toolchain-smoke"
    expected_smoke = {
        smoke_root / "smoke68k.bin": bytes.fromhex("70014E75"),
        smoke_root / "smokez80.bin": bytes.fromhex("3E9476"),
    }
    for path, expected in expected_smoke.items():
        if not path.is_file() or path.read_bytes() != expected:
            issues.append(f"toolchain smoke fixture is missing or stale: {path}")

    archive_results: dict[str, Any] = {}
    if verify_archives:
        archive_names = {
            "python": "python-3.12.10-embed-amd64.zip",
            "zig": "zig-0.14.1-windows.zip",
            "vasm": "vasm-2.0f.tar.gz",
            "sjasmplus": "sjasmplus-1.23.1-win.zip",
            "blastem": "blastem-0.6.3-pre-732f5689d438-win64.zip",
        }
        for name, filename in archive_names.items():
            path = root / ".toolcache" / "downloads" / filename
            actual = sha256(path) if path.is_file() else None
            expected = tools[name]["sha256"]
            archive_results[name] = {
                "path": path.relative_to(root).as_posix(),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "status": "pass" if actual == expected else "fail",
            }
            if actual != expected:
                issues.append(f"cached {name} archive checksum mismatch")

    return {
        "schema": 1,
        "status": "pass" if not issues else "fail",
        "lock_sha256": sha256(lock_path),
        "versions": versions,
        "archives": archive_results,
        "python_packages": python_packages,
        "python_baseline": python_baseline,
        "smoke": {
            path.relative_to(root).as_posix(): {
                "bytes": len(expected),
                "sha256": hashlib.sha256(expected).hexdigest(),
                "status": "pass" if path.is_file() and path.read_bytes() == expected else "fail",
            }
            for path, expected in expected_smoke.items()
        },
        "license_limitations": [
            "vasm 2.0f is pinned under its upstream non-commercial use notice; "
            "commercial Genesis distribution clearance is not established"
        ],
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--verify-archives", action="store_true")
    args = parser.parse_args()
    try:
        report = verify(args.root.resolve(), verify_archives=args.verify_archives)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        report = {"schema": 1, "status": "fail", "issues": [str(error)]}
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8", newline="\n")
    if report["status"] == "pass":
        print("Pinned toolchain verification: PASS")
        return 0
    print(text, file=sys.stderr, end="")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
