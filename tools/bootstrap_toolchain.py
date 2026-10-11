#!/usr/bin/env python3
"""Install and verify the pinned, project-local Windows toolchain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


VASM_CORE_SOURCES = (
    "vasm.c",
    "atom.c",
    "expr.c",
    "symtab.c",
    "symbol.c",
    "error.c",
    "parse.c",
    "reloc.c",
    "hugeint.c",
    "cond.c",
    "listing.c",
    "source.c",
    "supp.c",
    "dwarf.c",
    "osdep.c",
    "output_test.c",
    "output_elf.c",
    "output_bin.c",
    "output_vobj.c",
    "output_hunk.c",
    "output_aout.c",
    "output_tos.c",
    "output_xfile.c",
    "output_srec.c",
    "output_cdef.c",
    "output_ihex.c",
    "output_o65.c",
    "output_gst.c",
    "output_woz.c",
    "output_pap.c",
    "output_hans.c",
    "output_coff.c",
    "output_aof.c",
    "cpus/m68k/cpu.c",
    "syntax/mot/syntax.c",
)

VASM_OUTPUT_DEFINES = (
    "OUTAOF",
    "OUTAOUT",
    "OUTBIN",
    "OUTCOFF",
    "OUTELF",
    "OUTGST",
    "OUTHANS",
    "OUTHUNK",
    "OUTIHEX",
    "OUTO65",
    "OUTPAP",
    "OUTSREC",
    "OUTTOS",
    "OUTVOBJ",
    "OUTWOZ",
    "OUTXFIL",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, expected: str, destination: Path, force: bool) -> None:
    if destination.is_file() and not force and sha256(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    print(f"download: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "RECURSION94-bootstrap/1"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    actual = sha256(partial)
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"checksum mismatch for {destination.name}: expected {expected}, got {actual}"
        )
    os.replace(partial, destination)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (destination / info.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"unsafe ZIP member: {info.filename}")
        bundle.extractall(destination)


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"unsafe TAR member: {member.name}")
        bundle.extractall(destination, filter="data")


def replace_tree_from_archive(
    archive: Path, destination: Path, kind: str, force: bool, sentinel: str
) -> None:
    if not force and (destination / sentinel).is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="extract-", dir=destination.parent) as temp_name:
        temp = Path(temp_name)
        if kind == "zip":
            safe_extract_zip(archive, temp)
        else:
            safe_extract_tar(archive, temp)
        children = [entry for entry in temp.iterdir()]
        source = children[0] if len(children) == 1 and children[0].is_dir() else temp
        staged = destination.with_name(destination.name + ".staged")
        if staged.exists():
            shutil.rmtree(staged)
        shutil.copytree(source, staged)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staged, destination)
    if not (destination / sentinel).is_file():
        raise RuntimeError(f"archive did not provide {sentinel} in {destination}")


def run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}"
        )
    return result.stdout.strip()


def build_vasm(zig: Path, source: Path, output: Path, force: bool) -> str:
    if force or not output.is_file():
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(zig),
            "cc",
            "-O2",
            "-std=c90",
            f"-I{source}",
            f"-I{source / 'cpus' / 'm68k'}",
            f"-I{source / 'syntax' / 'mot'}",
        ]
        command.extend(f"-D{name}" for name in VASM_OUTPUT_DEFINES)
        command.extend(str(source / name) for name in VASM_CORE_SOURCES)
        command.extend(("-lm", "-o", str(output)))
        print("build: vasmm68k_mot.exe")
        run(command, cwd=source)
    version = run([str(output), "-v"])
    if "vasm 2.0f" not in version or "M68k" not in version:
        raise RuntimeError(f"unexpected vasm version:\n{version}")
    return version


def smoke_test(root: Path, bin_root: Path, report_root: Path) -> dict[str, object]:
    smoke_root = report_root / "toolchain-smoke"
    smoke_root.mkdir(parents=True, exist_ok=True)
    vasm = bin_root / "vasmm68k_mot.exe"
    sjasm = bin_root / "sjasmplus.exe"
    m68k_out = smoke_root / "smoke68k.bin"
    z80_out = smoke_root / "smokez80.bin"
    m68k_out_arg = m68k_out.relative_to(root).as_posix()
    z80_out_arg = z80_out.relative_to(root).as_posix()
    m68k_log = run(
        [
            str(vasm),
            "-Fbin",
            "-m68000",
            "-no-opt",
            "-o",
            m68k_out_arg,
            "tests/toolchain/smoke68k.asm",
        ],
        cwd=root,
    )
    z80_log = run(
        [
            str(sjasm),
            f"--raw={z80_out_arg}",
            "tests/toolchain/smokez80.asm",
        ],
        cwd=root,
    )
    expected_m68k = bytes.fromhex("70014E75")
    expected_z80 = bytes.fromhex("3E9476")
    if m68k_out.read_bytes() != expected_m68k:
        raise RuntimeError(f"unexpected 68000 smoke output: {m68k_out.read_bytes().hex()}")
    if z80_out.read_bytes() != expected_z80:
        raise RuntimeError(f"unexpected Z80 smoke output: {z80_out.read_bytes().hex()}")
    return {
        "m68k": {"sha256": sha256(m68k_out), "bytes": len(expected_m68k), "log": m68k_log},
        "z80": {"sha256": sha256(z80_out), "bytes": len(expected_z80), "log": z80_log},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    lock = json.loads((root / "tools" / "toolchain.lock.json").read_text("utf-8"))
    tools = lock["tools"]
    cache = root / ".toolcache"
    downloads = cache / "downloads"
    bin_root = cache / "bin"
    source_root = cache / "src"
    report_root = root / "artifacts" / "reports"
    bin_root.mkdir(parents=True, exist_ok=True)
    source_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)

    archive_names = {
        "zig": "zig-0.14.1-windows.zip",
        "vasm": "vasm-2.0f.tar.gz",
        "sjasmplus": "sjasmplus-1.23.1-win.zip",
        "blastem": "blastem-0.6.3-pre-732f5689d438-win64.zip",
    }
    archives: dict[str, Path] = {}
    for name, filename in archive_names.items():
        metadata = tools[name]
        archives[name] = downloads / filename
        download(metadata["url"], metadata["sha256"], archives[name], args.force)

    zig_root = cache / "zig"
    vasm_root = source_root / "vasm-2.0f"
    sjasm_root = cache / "sjasmplus"
    blastem_root = cache / "blastem"
    replace_tree_from_archive(archives["zig"], zig_root, "zip", args.force, "zig.exe")
    replace_tree_from_archive(archives["vasm"], vasm_root, "tar", args.force, "vasm.c")
    replace_tree_from_archive(
        archives["sjasmplus"], sjasm_root, "zip", args.force, "sjasmplus.exe"
    )
    replace_tree_from_archive(
        archives["blastem"], blastem_root, "zip", args.force, "blastem.exe"
    )

    sjasm_target = bin_root / "sjasmplus.exe"
    if args.force or not sjasm_target.is_file():
        shutil.copy2(sjasm_root / "sjasmplus.exe", sjasm_target)
    vasm_version = build_vasm(
        zig_root / "zig.exe", vasm_root, bin_root / "vasmm68k_mot.exe", args.force
    )
    sjasm_version = run([str(sjasm_target), "--version"])
    if "1.23.1" not in sjasm_version:
        raise RuntimeError(f"unexpected sjasmplus version:\n{sjasm_version}")
    zig_version = run([str(zig_root / "zig.exe"), "version"])
    if zig_version != "0.14.1":
        raise RuntimeError(f"unexpected Zig version: {zig_version}")
    smoke = smoke_test(root, bin_root, report_root)

    environment = {
        "schema": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(root),
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
            "powershell_expected": "5.1 or newer",
        },
        "tools": {
            "python": tools["python"]["version"],
            "zig": zig_version,
            "vasm": vasm_version.splitlines(),
            "sjasmplus": sjasm_version.splitlines(),
            "blastem": tools["blastem"]["version"],
        },
        "paths": {
            "vasm": str(bin_root / "vasmm68k_mot.exe"),
            "sjasmplus": str(sjasm_target),
            "blastem": str(blastem_root / "blastem.exe"),
        },
        "smoke": smoke,
    }
    (report_root / "environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )
    (cache / "installed.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "lock_sha256": sha256(root / "tools" / "toolchain.lock.json"),
                "installed_utc": environment["generated_utc"],
                "tools": {name: item["version"] for name, item in tools.items()},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("toolchain smoke: PASS")
    print(f"environment report: {report_root / 'environment.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
