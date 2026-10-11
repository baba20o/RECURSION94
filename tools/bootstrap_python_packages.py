#!/usr/bin/env python3
"""Vendor hash-pinned wheels into the project-local embedded Python directory.

Only stdlib code is used, including on WSL when preparing Windows payloads.
No package code, pip, .pth file, global Python, or native extension is executed.
The deliberately bounded requirement parser supports this lock, not arbitrary
PyPI dependency resolution. New syntax fails closed and needs explicit review.
All wheels and destination collisions are checked before package files change.
An interrupted first install requires manual review of its unowned package
roots; this installer never deletes a Python directory or claims partial success.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile


LOCK_RELATIVE_PATH = "tools/python-packages.lock.json"
INSTALL_RELATIVE_PATH = ".toolcache/python"
CACHE_RELATIVE_PATH = ".toolcache/downloads/python-wheels"
RECORD_NAME = "recursion94-python-packages.json"
TARGET = {"implementation": "cpython", "python": "3.12", "platform": "win_amd64"}
PACKAGE_ROOTS = {
    "jsonschema": {"jsonschema"},
    "attrs": {"attr", "attrs"},
    "jsonschema-specifications": {"jsonschema_specifications"},
    "referencing": {"referencing"},
    "rpds-py": {"rpds"},
    "typing-extensions": {"typing_extensions.py"},
}
MAX_WHEEL_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_UNPACKED_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 4096
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.I)


class PackageError(RuntimeError):
    """The pinned package contract could not be established."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
        raise PackageError(f"unsupported version: {value!r}")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _satisfies(version: str, specifier: str) -> bool:
    actual = _version(version)
    for item in specifier.split(","):
        match = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*([0-9.]+)\s*", item)
        if not match:
            raise PackageError(f"unsupported version requirement: {specifier!r}")
        operator, value = match.groups()
        expected = _version(value)
        if not {">=": actual >= expected, "<=": actual <= expected,
                "==": actual == expected, "!=": actual != expected,
                ">": actual > expected, "<": actual < expected}[operator]:
            return False
    return True


def _runtime_requirements(requirements: list[str]) -> list[tuple[str, str]]:
    result = []
    for requirement in requirements:
        base, separator, marker = requirement.partition(";")
        if separator:
            marker = marker.strip()
            if re.fullmatch(r"extra\s*==\s*(['\"])[a-z0-9-]+\1", marker):
                continue  # No extras are enabled by this application.
            match = re.fullmatch(
                r"python_version\s*(>=|<=|==|!=|>|<)\s*(['\"])([0-9.]+)\2", marker
            )
            if not match:
                raise PackageError(f"unsupported dependency marker: {marker!r}")
            if not _satisfies(TARGET["python"], match[1] + match[3]):
                continue
        match = re.fullmatch(r"\s*([a-zA-Z0-9_.-]+)\s*([<>=!].+)\s*", base)
        if not match:
            raise PackageError(f"unsupported dependency requirement: {base!r}")
        result.append((_normalize(match[1]), match[2].strip()))
    return result


def _safe_member(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise PackageError("unsafe ZIP/path spelling")
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    if any(not part or part in {".", ".."} or part[-1:] in {".", " "}
           or not re.fullmatch(r"[A-Za-z0-9_.+-]+", part)
           or _RESERVED.fullmatch(part) for part in parts):
        raise PackageError(f"unsafe ZIP/path member: {name!r}")
    return path


def _wheel_url(url: str, filename: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org"
            or parsed.query or parsed.fragment
            or not parsed.path.startswith("/packages/")
            or parsed.path.rsplit("/", 1)[-1] != filename):
        raise PackageError("wheel URL is not the exact official PyPI file URL")


def _compatible_tag(tag: str) -> bool:
    if tag == "py3-none-any" or tag == "cp312-cp312-win_amd64":
        return True
    match = re.fullmatch(r"cp3([0-9]+)-abi3-win_amd64", tag)
    return bool(match and 8 <= int(match[1]) <= 12)


def _dist_info(package: dict) -> str:
    return package["name"].replace("-", "_") + "-" + package["version"] + ".dist-info"


def load_lock(path: Path) -> dict:
    try:
        lock = json.loads(path.read_text("utf-8"))
        if lock["schema"] != 1 or lock["target"] != TARGET or lock["extras"] != []:
            raise PackageError("unsupported Python package lock target/schema/extras")
        packages = lock["packages"]
        names = [package["name"] for package in packages]
        if len(names) != len(set(names)) or set(names) != set(PACKAGE_ROOTS):
            raise PackageError("lock must contain the exact six runtime distributions")
        by_name = {package["name"]: package for package in packages}
        roots = set()
        for package in packages:
            name, version = package["name"], package["version"]
            _version(version)
            if not _satisfies(TARGET["python"], package["requires_python"]):
                raise PackageError(f"Python 3.12 is incompatible with {name}")
            expected_roots = PACKAGE_ROOTS[name] | {_dist_info(package)}
            if set(package["roots"]) != expected_roots or len(package["roots"]) != len(expected_roots):
                raise PackageError(f"unexpected installation roots for {name}")
            for member in package["roots"]:
                if _safe_member(member) != member or "/" in member or member.casefold() in roots:
                    raise PackageError("unsafe or colliding installation root")
                roots.add(member.casefold())
            tags = package["tags"]
            if len(tags) != 1 or not _compatible_tag(tags[0]):
                raise PackageError(f"incompatible wheel tag for {name}")
            expected_filename = f"{name.replace('-', '_')}-{version}-{tags[0]}.whl"
            if package["filename"] != expected_filename:
                raise PackageError("wheel filename/version/tag mismatch")
            _wheel_url(package["url"], package["filename"])
            if package["metadata_url"] != f"https://pypi.org/pypi/{name}/{version}/json":
                raise PackageError("nonofficial release metadata URL")
            if not _HASH.fullmatch(package["sha256"]) or not _HASH.fullmatch(package["metadata_sha256"]):
                raise PackageError("invalid pinned SHA-256")
            if type(package["size"]) is not int or not 0 < package["size"] <= MAX_WHEEL_BYTES:
                raise PackageError("wheel size exceeds bounded payload")
            if package["license"] not in {"MIT", "PSF-2.0"} or not package["license_files"]:
                raise PackageError("unsupported or missing license pin")
            for license_file in package["license_files"]:
                if _safe_member(license_file) != license_file:
                    raise PackageError("invalid license filename")
            for dependency, specifier in _runtime_requirements(package["requires_dist"]):
                if dependency not in by_name or not _satisfies(by_name[dependency]["version"], specifier):
                    raise PackageError(f"unsatisfied locked dependency: {name} -> {dependency}")
        return lock
    except (KeyError, TypeError, ValueError) as error:
        raise PackageError(f"malformed Python package lock: {error}") from error


def _scoped(root: Path, relative: str) -> Path:
    _safe_member(relative)
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise PackageError(f"symlink/junction is not an owned destination: {relative}")
    if root != target.resolve() and root not in target.resolve().parents:
        raise PackageError("destination escaped project root")
    return target


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".r94-wheel-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _wheel_url(newurl, request.full_url.rsplit("/", 1)[-1])
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _download(package: dict) -> bytes:
    _wheel_url(package["url"], package["filename"])
    request = urllib.request.Request(
        package["url"], headers={"User-Agent": "RECURSION94-pinned-wheels/1"}
    )
    opener = urllib.request.build_opener(_OfficialRedirects())
    with opener.open(request, timeout=30) as response:
        _wheel_url(response.geturl(), package["filename"])
        payload = response.read(package["size"] + 1)
    if len(payload) != package["size"] or _sha(payload) != package["sha256"]:
        raise PackageError(f"download size/hash mismatch: {package['filename']}")
    return payload


def _wheel_files(package: dict, payload: bytes) -> dict[str, bytes]:
    if len(payload) != package["size"] or _sha(payload) != package["sha256"]:
        raise PackageError(f"wheel size/hash mismatch: {package['filename']}")
    files: dict[str, bytes] = {}
    seen = set()
    spellings = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
            members = bundle.infolist()
            if len(members) > MAX_MEMBERS or sum(item.file_size for item in members) > MAX_UNPACKED_BYTES:
                raise PackageError("wheel exceeds bounded extraction limits")
            for item in members:
                name = _safe_member(item.orig_filename)
                if item.orig_filename != item.filename:
                    raise PackageError("ZIP filename was truncated")
                if name.casefold() in seen:
                    raise PackageError("duplicate/case-colliding ZIP member")
                seen.add(name.casefold())
                parts = name.split("/")
                for index in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:index])
                    previous = spellings.setdefault(prefix.casefold(), prefix)
                    if previous != prefix:
                        raise PackageError("case-colliding ZIP directory spelling")
                if name.split("/")[0] not in package["roots"]:
                    raise PackageError("wheel member is outside allowed package roots")
                if name.endswith((".pyc", ".pyo")) or "__pycache__" in parts:
                    raise PackageError("wheel-supplied bytecode cache is not allowed")
                kind = stat.S_IFMT(item.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                    raise PackageError("ZIP link/special/encrypted member rejected")
                if item.file_size > MAX_MEMBER_BYTES or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise PackageError("ZIP member exceeds bounded supported payload")
                if item.is_dir():
                    if item.file_size or kind == stat.S_IFREG:
                        raise PackageError("malformed ZIP directory")
                    continue
                if kind == stat.S_IFDIR:
                    raise PackageError("ZIP directory/file type mismatch")
                files[name] = bundle.read(item)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise PackageError(f"invalid wheel archive: {error}") from error
    lower_files = {path.casefold() for path in files}
    for name in files:
        if any(str(parent).casefold() in lower_files
               for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise PackageError("ZIP file/directory ancestor collision")
    if {name.split("/")[0] for name in files} != set(package["roots"]):
        raise PackageError("wheel package root coverage mismatch")
    dist = _dist_info(package)
    try:
        metadata_bytes = files[dist + "/METADATA"]
        if _sha(metadata_bytes) != package["metadata_sha256"]:
            raise PackageError("wheel METADATA hash mismatch")
        metadata = email.parser.BytesParser().parsebytes(metadata_bytes)
        wheel = email.parser.BytesParser().parsebytes(files[dist + "/WHEEL"])
        if (len(metadata.get_all("Name", [])) != 1
                or _normalize(metadata.get("Name", "")) != package["name"]
                or metadata.get_all("Version") != [package["version"]]
                or metadata.get_all("Requires-Python") != [package["requires_python"]]
                or metadata.get_all("Requires-Dist", []) != package["requires_dist"]
                or metadata.get_all("License-Expression") != [package["license"]]
                or metadata.get_all("License-File", []) != package["license_files"]
                or wheel.get_all("Wheel-Version") != ["1.0"]
                or wheel.get_all("Tag", []) != package["tags"]):
            raise PackageError("wheel version/dependency/license/tag metadata mismatch")
        for license_file in package["license_files"]:
            if not files[dist + "/licenses/" + license_file]:
                raise PackageError("empty package license")
    except KeyError as error:
        raise PackageError(f"required wheel metadata/license missing: {error}") from error
    return files


def _source_filename_spellings(source: Path, install: Path) -> list[str]:
    """Finite loader spellings of the same source, independent of cache bytes.

    Windows imports preserve the spelling of the embedded runtime's sys.path;
    a caller may supply a differently cased project root for that same file.
    No filename or code object is ever recovered from an untrusted cache.
    """
    spellings = {str(source)}
    bases = [Path(sys.executable).parent]
    bases.extend(Path(entry) for entry in sys.path if isinstance(entry, str) and entry)
    for base in bases:
        if base.is_dir() and base.resolve() == install.resolve():
            candidate = base / source.relative_to(install)
            if candidate.resolve() == source.resolve():
                spellings.add(str(candidate))
    return sorted(spellings)


def _verify_bytecode(path: Path, install: Path, files: dict[str, bytes]) -> None:
    """Compare cache bytes to a fresh compile; never unmarshal or execute them.

    Cross-host wheel preparation remains possible with no cache. Once native
    caches exist, verification requires the matching CPython 3.12 interpreter.
    Only the timestamp format ordinary imports write is accepted. Hash-mode,
    orphaned, foreign-tag and additional optimization spellings fail closed.
    """
    match = re.fullmatch(r"(.+)\.cpython-312(?:\.opt-([12]))?\.pyc", path.name)
    if path.parent.name != "__pycache__" or not match:
        raise PackageError("unknown bytecode cache spelling")
    source = path.parent.parent / (match[1] + ".py")
    relative = source.relative_to(install).as_posix()
    if relative not in files or not source.is_file():
        raise PackageError("orphan bytecode cache has no pinned source")
    optimization = match[2] or ""
    if Path(importlib.util.cache_from_source(str(source), optimization=optimization)) != path:
        raise PackageError("bytecode cache path does not match this interpreter")
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise PackageError("cached bytecode verification requires CPython 3.12")
    payload = files[relative]
    header = importlib.util.MAGIC_NUMBER + struct.pack(
        "<III", 0, int(source.stat().st_mtime) & 0xFFFFFFFF, len(payload) & 0xFFFFFFFF
    )
    for filename in _source_filename_spellings(source, install):
        # Retain the code object: marshal's reference flag differs for an
        # unreferenced temporary, unlike CPython's normal import-cache writer.
        code = compile(payload, filename, "exec", dont_inherit=True, optimize=int(optimization or 0))
        expected = header + marshal.dumps(code)
        if path.stat().st_size == len(expected) and path.read_bytes() == expected:
            return
    raise PackageError("bytecode cache differs from freshly compiled pinned source")


def _audit_existing(root: Path, install: Path, roots: set[str], files: dict[str, bytes], owned: bool) -> int:
    lower_roots = {name.casefold() for name in roots}
    import_names = {name.removesuffix(".py").casefold()
                    for names in PACKAGE_ROOTS.values() for name in names}
    verified_bytecode = 0
    for entry in install.iterdir():
        lowered = entry.name.casefold()
        if lowered == "__pycache__":
            if entry.name != "__pycache__":
                raise PackageError("case-colliding root bytecode cache directory")
            _scoped(root, INSTALL_RELATIVE_PATH + "/__pycache__")
            if not entry.is_dir():
                raise PackageError("root bytecode cache is not a directory")
            for cached in entry.iterdir():
                relative = cached.relative_to(install).as_posix()
                _scoped(root, INSTALL_RELATIVE_PATH + "/" + relative)
                if not cached.is_file():
                    raise PackageError("root bytecode cache entry is not a regular file")
                _verify_bytecode(cached, install, files)
                verified_bytecode += 1
            continue
        if lowered in lower_roots and entry.name not in roots:
            raise PackageError("case-colliding existing package root")
        if entry.name not in roots and any(
            lowered in {name, name + ".py", name + ".pyc"}
            or (lowered.startswith(name + ".") and lowered.endswith(".pyd"))
            for name in import_names
        ):
            raise PackageError("unowned import-equivalent package/module alias")
        metadata = re.fullmatch(r"(.+)\.(?:dist|egg)-info", lowered)
        if metadata:
            distribution = _normalize(metadata[1])
            if entry.name not in roots and any(
                distribution == name or distribution.startswith(name + "-")
                for name in PACKAGE_ROOTS
            ):
                raise PackageError("another version of an owned distribution exists")
    for name in roots:
        target = _scoped(root, INSTALL_RELATIVE_PATH + "/" + name)
        if not target.exists():
            continue
        if not owned:
            raise PackageError(f"unowned existing package root: {name}")
        entries = [target] if target.is_file() else list(target.rglob("*"))
        for entry in entries:
            relative = entry.relative_to(install).as_posix()
            _scoped(root, INSTALL_RELATIVE_PATH + "/" + relative)
            if entry.is_dir():
                continue
            if relative in files:
                continue
            if entry.suffix == ".pyc" and entry.parent.name == "__pycache__":
                _verify_bytecode(entry, install, files)
                verified_bytecode += 1
                continue
            raise PackageError(f"unowned file in package root: {relative}")
    return verified_bytecode


def provision(root: Path | str, *, verify_only: bool = False) -> dict:
    """Install or verify all six pinned wheels without importing target packages."""
    root = Path(root).resolve()
    lock_path = _scoped(root, LOCK_RELATIVE_PATH)
    lock = load_lock(lock_path)
    install = _scoped(root, INSTALL_RELATIVE_PATH)
    if not install.is_dir() or not _scoped(root, INSTALL_RELATIVE_PATH + "/python.exe").is_file():
        raise PackageError("project-local embedded Python must be bootstrapped first")
    _scoped(root, CACHE_RELATIVE_PATH)
    files: dict[str, bytes] = {}
    roots = set()
    for package in lock["packages"]:
        path = _scoped(root, CACHE_RELATIVE_PATH + "/" + package["filename"])
        payload = path.read_bytes() if path.is_file() and path.stat().st_size <= MAX_WHEEL_BYTES else b""
        if len(payload) != package["size"] or _sha(payload) != package["sha256"]:
            if verify_only:
                raise PackageError(f"missing/corrupted cached wheel: {package['filename']}")
            payload = _download(package)
            # Cache installation is separate from the all-package write barrier.
            _atomic_write(path, payload)
        planned = _wheel_files(package, payload)
        if {name.casefold() for name in files} & {name.casefold() for name in planned}:
            raise PackageError("cross-wheel path collision")
        files.update(planned)
        roots.update(package["roots"])
    if len(files) > MAX_MEMBERS or sum(map(len, files.values())) > MAX_UNPACKED_BYTES:
        raise PackageError("combined wheel payload exceeds install bounds")
    record = {
        "schema": 1, "target": TARGET, "lock_sha256": _sha(lock_path.read_bytes()),
        "packages": [{key: item[key] for key in ("name", "version", "filename", "sha256", "license", "license_files")}
                     for item in lock["packages"]],
        "files": {name: {"sha256": _sha(payload), "bytes": len(payload)}
                  for name, payload in sorted(files.items())},
    }
    record_path = _scoped(root, INSTALL_RELATIVE_PATH + "/" + RECORD_NAME)
    if record_path.exists() and not record_path.is_file():
        raise PackageError("installed package record destination is not a regular file")
    owned = record_path.is_file()
    if owned:
        try:
            existing = json.loads(record_path.read_text("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise PackageError("installed package record is corrupt") from error
        if existing != record:
            raise PackageError("installed package record differs from pinned wheel manifest")
    elif verify_only:
        raise PackageError("installed package record is missing")
    verified_bytecode = _audit_existing(root, install, roots, files, owned)
    # Resolve every destination before touching any installed package payload.
    destinations = {name: _scoped(root, INSTALL_RELATIVE_PATH + "/" + name) for name in files}
    changed = []
    for name, path in destinations.items():
        if path.exists() and not path.is_file():
            raise PackageError(f"file destination is not a regular file: {name}")
        if not path.is_file() or path.stat().st_size != len(files[name]) or _sha(path.read_bytes()) != record["files"][name]["sha256"]:
            changed.append(name)
    if verify_only and changed:
        raise PackageError(f"installed package file missing/corrupted: {changed[0]}")
    for name in changed:
        _atomic_write(destinations[name], files[name])
    if not owned:
        _atomic_write(record_path, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
    return {
        "status": "pass", "target": TARGET, "lock_sha256": record["lock_sha256"],
        "record_path": record_path.relative_to(root).as_posix(),
        "record_sha256": _sha(record_path.read_bytes()), "verified_files": len(files),
        "verified_bytecode_files": verified_bytecode,
        "changed_files": len(changed), "packages": record["packages"],
        "verification": "pinned wheel hashes, METADATA versions/dependency closure, licenses, installed file hashes, and exact recompiled timestamp caches; target packages not imported",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = provision(args.root, verify_only=args.verify_only)
    except (PackageError, OSError, ValueError) as error:
        print(f"Python package provisioning failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
