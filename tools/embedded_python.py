"""Attest the unmodified, pinned embedded Windows Python before provisioning."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

from tools.bootstrap_python_packages import load_lock


ARCHIVE_NAME = "python-3.12.10-embed-amd64.zip"
ARCHIVE_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
VERSION = (3, 12, 10)


def _regular(path: Path) -> None:
    if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
            or not path.is_file()):
        raise RuntimeError(f"embedded Python baseline is not a regular file: {path.name}")


def verify_baseline(root: Path) -> dict:
    """Read-only archive/member verification, including the exact isolated _pth."""
    root = root.resolve()
    for path in (root / ".toolcache", root / ".toolcache/python",
                 root / ".toolcache/downloads"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise RuntimeError("embedded Python cache must not use links/junctions")
    metadata = json.loads((root / "tools/toolchain.lock.json").read_text("utf-8"))["tools"]["python"]
    if (metadata["sha256"] != ARCHIVE_SHA256
            or metadata["version"] != "3.12.10-embed-amd64"):
        raise RuntimeError("embedded Python lock differs from reviewed baseline")
    archive = root / ".toolcache/downloads" / ARCHIVE_NAME
    _regular(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise RuntimeError("embedded Python archive checksum mismatch")
    install = root / ".toolcache/python"
    if {path.name for path in install.glob("*._pth")} != {"python312._pth"}:
        raise RuntimeError("unexpected embedded Python path configuration")
    verified = {}
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            # The reviewed archive has only flat regular files.
            if item.is_dir() or Path(item.filename).name != item.filename:
                raise RuntimeError("unexpected embedded Python archive member")
            path = install / item.filename
            _regular(path)
            expected = hashlib.sha256(bundle.read(item)).hexdigest()
            if path.stat().st_size != item.file_size or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"embedded Python baseline mismatch: {item.filename}")
            verified[item.filename] = expected
    lock = load_lock(root / "tools/python-packages.lock.json")
    extra_files = {"typing_extensions.py", "recursion94-python-packages.json"}
    extra_dirs = {"__pycache__"} | {
        name for package in lock["packages"] for name in package["roots"]
        if name not in extra_files
    }
    for path in install.iterdir():
        if path.name in verified:
            continue
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise RuntimeError("embedded Python extra entry must not be a link/junction")
        if ((path.name in extra_files and path.is_file())
                or (path.name in extra_dirs and path.is_dir())):
            continue
        raise RuntimeError(f"unexpected embedded Python top-level entry: {path.name}")
    return {"status": "pass", "archive_sha256": ARCHIVE_SHA256,
            "verified_files": len(verified), "files": verified}


def attest(root: Path) -> dict:
    """Require the actual CPython ABI/executable before package or tool writes."""
    expected_exe = (root / ".toolcache/python/python.exe").resolve()
    if (sys.implementation.name != "cpython" or sys.version_info[:3] != VERSION
            or sys.platform != "win32" or struct.calcsize("P") != 8
            or Path(sys.executable).resolve() != expected_exe
            or not sys.flags.isolated or not sys.flags.no_site):
        raise RuntimeError("requires isolated project-local CPython 3.12.10 Windows x64")
    report = verify_baseline(root)
    report["runtime"] = {"implementation": "cpython", "version": "3.12.10",
                         "platform": "win32", "pointer_bits": 64,
                         "executable": ".toolcache/python/python.exe",
                         "isolated": True, "site_disabled": True}
    return report
