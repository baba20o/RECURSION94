"""Offline synthetic-wheel tests; no pip, network, package imports, or emulator."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import py_compile
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import warnings
import zipfile

from tools import bootstrap_python_packages as packages


ROOT = Path(__file__).resolve().parents[2]


def _synthetic_wheel(package: dict, changes: dict[str, bytes] | None = None,
                     extras: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
                     removed: tuple[str, ...] = ()) -> bytes:
    """Invented bytes for installer tests, never an actual third-party wheel."""
    dist = packages._dist_info(package)
    metadata = ["Metadata-Version: 2.4", f"Name: {package['name'].replace('-', '_')}",
                f"Version: {package['version']}", f"Requires-Python: {package['requires_python']}",
                f"License-Expression: {package['license']}"]
    metadata += [f"Requires-Dist: {item}" for item in package["requires_dist"]]
    metadata += [f"License-File: {item}" for item in package["license_files"]]
    files = {
        dist + "/METADATA": ("\n".join(metadata) + "\n\nSynthetic test fixture.\n").encode(),
        dist + "/WHEEL": ("Wheel-Version: 1.0\nRoot-Is-Purelib: true\n" + "".join(f"Tag: {tag}\n" for tag in package["tags"])).encode(),
        dist + "/RECORD": b"",
    }
    for root in packages.PACKAGE_ROOTS[package["name"]]:
        files[root if root.endswith(".py") else root + "/__init__.py"] = b"# synthetic unit-test payload only\n"
    for license_file in package["license_files"]:
        files[dist + "/licenses/" + license_file] = b"Synthetic license text retained byte-for-byte.\n"
    files.update(changes or {})
    for name in removed:
        files.pop(name)
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, payload in files.items():
                bundle.writestr(name, payload)
            for name, payload in extras or []:
                if isinstance(name, str):
                    # Windows ZipInfo normally normalizes backslashes during
                    # construction; preserve the adversarial raw ZIP spelling.
                    item = zipfile.ZipInfo("placeholder")
                    item.filename = item.orig_filename = name
                else:
                    item = name
                bundle.writestr(item, payload)
    payload = output.getvalue()
    package["sha256"] = hashlib.sha256(payload).hexdigest()
    package["size"] = len(payload)
    if dist + "/METADATA" in files:
        package["metadata_sha256"] = hashlib.sha256(files[dist + "/METADATA"]).hexdigest()
    return payload


class PythonPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="r94-wheel-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = json.loads((ROOT / packages.LOCK_RELATIVE_PATH).read_text("utf-8"))
        self.install = self.root / packages.INSTALL_RELATIVE_PATH
        self.install.mkdir(parents=True)
        (self.install / "python.exe").write_bytes(b"synthetic interpreter placeholder; never executed")
        (self.install / "python312.zip").write_bytes(b"stdlib sentinel")
        (self.install / "python312._pth").write_bytes(b"python312.zip\n.\n")
        self.payloads = {}
        for package in self.lock["packages"]:
            payload = _synthetic_wheel(package)
            self.payloads[package["name"]] = payload
            path = self.root / packages.CACHE_RELATIVE_PATH / package["filename"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self._save_lock()
        self.download = patch.object(packages, "_download", side_effect=AssertionError("offline test may not download"))
        self.download_mock = self.download.start()
        self.addCleanup(self.download.stop)

    def _save_lock(self) -> None:
        path = self.root / packages.LOCK_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.lock), encoding="utf-8")

    def _set_wheel(self, index: int, **kwargs) -> dict:
        package = self.lock["packages"][index]
        payload = _synthetic_wheel(package, **kwargs)
        self.payloads[package["name"]] = payload
        (self.root / packages.CACHE_RELATIVE_PATH / package["filename"]).write_bytes(payload)
        self._save_lock()
        return package

    def test_official_lock_has_exact_versions_platform_and_complete_dependencies(self) -> None:
        lock = packages.load_lock(ROOT / packages.LOCK_RELATIVE_PATH)
        self.assertEqual({item["name"]: item["version"] for item in lock["packages"]}, {
            "jsonschema": "4.26.0", "attrs": "26.1.0", "jsonschema-specifications": "2025.9.1",
            "referencing": "0.37.0", "rpds-py": "0.30.0", "typing-extensions": "4.15.0",
        })
        self.assertEqual(lock["target"], packages.TARGET)
        self.assertEqual(lock["extras"], [])
        self.assertEqual(sum(item["size"] for item in lock["packages"]), 488458)
        referencing = next(item for item in lock["packages"] if item["name"] == "referencing")
        self.assertIn(("typing-extensions", ">=4.4.0"), packages._runtime_requirements(referencing["requires_dist"]))
        self.assertEqual(packages._runtime_requirements(["not-installed; extra == 'format'"]), [])

    def test_install_verify_and_second_install_are_idempotent(self) -> None:
        first = packages.provision(self.root)
        self.assertEqual(first["status"], "pass")
        self.assertGreater(first["changed_files"], 0)
        paths = [path for path in self.install.rglob("*") if path.is_file()]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        for report in (packages.provision(self.root, verify_only=True), packages.provision(self.root)):
            self.assertEqual(report["changed_files"], 0)
            self.assertEqual(report["record_sha256"], first["record_sha256"])
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})
        self.download_mock.assert_not_called()

    def test_licenses_and_stdlib_are_preserved(self) -> None:
        sentinels = {name: (self.install / name).read_bytes() for name in ("python.exe", "python312.zip", "python312._pth")}
        report = packages.provision(self.root)
        record = json.loads((self.root / report["record_path"]).read_text("utf-8"))
        for package in self.lock["packages"]:
            for filename in package["license_files"]:
                relative = packages._dist_info(package) + "/licenses/" + filename
                self.assertEqual((self.install / relative).read_bytes(), b"Synthetic license text retained byte-for-byte.\n")
                self.assertIn(relative, record["files"])
        self.assertEqual(sentinels, {name: (self.install / name).read_bytes() for name in sentinels})

    def test_verify_only_does_not_install_or_download(self) -> None:
        with self.assertRaisesRegex(packages.PackageError, "record is missing"):
            packages.provision(self.root, verify_only=True)
        self.assertFalse((self.install / "jsonschema").exists())
        self.download_mock.assert_not_called()

    def test_corrupted_install_fails_verification_and_repairs_only_owned_files(self) -> None:
        packages.provision(self.root)
        path = self.install / "jsonschema/__init__.py"
        path.write_bytes(b"corruption")
        with self.assertRaisesRegex(packages.PackageError, "file missing/corrupted"):
            packages.provision(self.root, verify_only=True)
        repaired = packages.provision(self.root)
        self.assertEqual(repaired["changed_files"], 1)
        self.assertEqual(path.read_bytes(), b"# synthetic unit-test payload only\n")

    def test_missing_owned_file_is_detected(self) -> None:
        packages.provision(self.root)
        (self.install / "typing_extensions.py").unlink()
        with self.assertRaisesRegex(packages.PackageError, "missing/corrupted"):
            packages.provision(self.root, verify_only=True)

    def test_corrupted_or_forged_record_is_rejected_not_repaired(self) -> None:
        packages.provision(self.root)
        path = self.install / packages.RECORD_NAME
        original = path.read_bytes()
        for payload in (b"not json", original.replace(b'"version": "4.26.0"', b'"version": "0.0.0"')):
            with self.subTest(payload_length=len(payload)):
                path.write_bytes(payload)
                with self.assertRaisesRegex(packages.PackageError, "record"):
                    packages.provision(self.root)
                self.assertEqual(path.read_bytes(), payload)

    def test_cached_wheel_corruption_fails_offline_verification(self) -> None:
        packages.provision(self.root)
        package = self.lock["packages"][0]
        path = self.root / packages.CACHE_RELATIVE_PATH / package["filename"]
        path.write_bytes(b"broken wheel")
        with self.assertRaisesRegex(packages.PackageError, "cached wheel"):
            packages.provision(self.root, verify_only=True)
        self.download_mock.assert_not_called()

    def test_cache_refetch_accepts_only_hash_verified_payload(self) -> None:
        package = self.lock["packages"][0]
        path = self.root / packages.CACHE_RELATIVE_PATH / package["filename"]
        path.unlink()
        with patch.object(packages, "_download", return_value=self.payloads[package["name"]]) as fetch:
            self.assertEqual(packages.provision(self.root)["status"], "pass")
            fetch.assert_called_once_with(package)
        self.assertEqual(path.read_bytes(), self.payloads[package["name"]])

    def test_all_wheels_preflight_before_any_installed_package_write(self) -> None:
        package = self.lock["packages"][-1]
        self._set_wheel(-1, removed=(packages._dist_info(package) + "/licenses/LICENSE",))
        before = sorted(path.name for path in self.install.iterdir())
        with self.assertRaisesRegex(packages.PackageError, "license missing"):
            packages.provision(self.root)
        self.assertEqual(before, sorted(path.name for path in self.install.iterdir()))

    def test_nonfile_record_destination_fails_before_package_writes(self) -> None:
        (self.install / packages.RECORD_NAME).mkdir()
        before = sorted(path.name for path in self.install.iterdir())
        with self.assertRaisesRegex(packages.PackageError, "record destination"):
            packages.provision(self.root)
        self.assertEqual(before, sorted(path.name for path in self.install.iterdir()))

    def test_unowned_existing_roots_and_extra_owned_files_fail_closed(self) -> None:
        path = self.install / "jsonschema"
        path.mkdir()
        with self.assertRaisesRegex(packages.PackageError, "unowned existing"):
            packages.provision(self.root)
        path.rmdir()
        packages.provision(self.root)
        extra = path / "unowned.py"
        extra.write_bytes(b"user file")
        with self.assertRaisesRegex(packages.PackageError, "unowned file"):
            packages.provision(self.root)
        self.assertEqual(extra.read_bytes(), b"user file")

    def test_existing_old_distribution_version_is_rejected(self) -> None:
        for name in ("jsonschema-0.0.1.dist-info", "JSONSCHEMA-0.0.1.DIST-INFO",
                     "jsonschema.egg-info", "Jsonschema-0.0.1.EGG-INFO"):
            with self.subTest(name=name):
                path = self.install / name
                path.mkdir()
                with self.assertRaisesRegex(packages.PackageError, "another version"):
                    packages.provision(self.root)
                path.rmdir()

    def test_unowned_import_equivalent_package_or_extension_is_rejected(self) -> None:
        directory = self.install / "typing_extensions"
        directory.mkdir()
        (directory / "__init__.py").write_bytes(b"# unowned shadow package\n")
        with self.assertRaisesRegex(packages.PackageError, "import-equivalent"):
            packages.provision(self.root)
        (directory / "__init__.py").unlink()
        directory.rmdir()
        packages.provision(self.root)
        for name in ("typing_extensions.pyd", "typing_extensions.cp312-win_amd64.pyd",
                     "TYPING_EXTENSIONS.abi3.pyd", "typing_extensions.pyc", "jsonschema.py"):
            with self.subTest(alias=name):
                path = self.install / name
                path.write_bytes(b"unowned import alias")
                with self.assertRaisesRegex(packages.PackageError, "import-equivalent"):
                    packages.provision(self.root, verify_only=True)
                path.unlink()

    def test_exact_native_bytecode_cache_is_verified_or_foreign_runtime_rejected(self) -> None:
        packages.provision(self.root)
        source = self.install / "jsonschema/__init__.py"
        for optimize in (0, 1, 2):
            py_compile.compile(str(source), doraise=True, optimize=optimize,
                               invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        if sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12):
            report = packages.provision(self.root, verify_only=True)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["verified_bytecode_files"], 3)
        else:
            # This asserts cross-host refusal, not a skipped native-positive.
            with self.assertRaisesRegex(packages.PackageError, "bytecode"):
                packages.provision(self.root, verify_only=True)

    def test_forged_timestamp_and_unchecked_hash_caches_are_rejected(self) -> None:
        packages.provision(self.root)
        source = self.install / "jsonschema/__init__.py"
        pinned = source.read_bytes()
        for mode in (py_compile.PycInvalidationMode.TIMESTAMP, py_compile.PycInvalidationMode.UNCHECKED_HASH):
            with self.subTest(mode=mode):
                source.write_bytes(b"# different executable code\nraise RuntimeError('must never run')\n")
                cache = Path(py_compile.compile(str(source), doraise=True, invalidation_mode=mode))
                source.write_bytes(pinned)
                # Make a timestamp cache appear current to an ordinary loader;
                # the verifier must still reject its different compiled body.
                data = bytearray(cache.read_bytes())
                if mode == py_compile.PycInvalidationMode.TIMESTAMP:
                    data[8:16] = struct.pack("<II", int(source.stat().st_mtime) & 0xFFFFFFFF, len(pinned))
                    cache.write_bytes(data)
                with self.assertRaisesRegex(packages.PackageError, "bytecode"):
                    packages.provision(self.root, verify_only=True)
                cache.unlink()

    def test_orphan_and_arbitrary_extra_bytecode_are_rejected(self) -> None:
        packages.provision(self.root)
        directory = self.install / "jsonschema/__pycache__"
        directory.mkdir()
        for filename in ("orphan.cpython-312.pyc", "__init__.cpython-312.pyc", "__init__.cpython-312.opt-0.pyc"):
            with self.subTest(filename=filename):
                path = directory / filename
                path.write_bytes(b"untrusted bytecode")
                with self.assertRaisesRegex(packages.PackageError, "bytecode"):
                    packages.provision(self.root, verify_only=True)
                path.unlink()

    def test_root_module_native_bytecode_opts_are_verified(self) -> None:
        packages.provision(self.root)
        source = self.install / "typing_extensions.py"
        for optimize in (0, 1, 2):
            py_compile.compile(str(source), doraise=True, optimize=optimize,
                               invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        if sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12):
            report = packages.provision(self.root, verify_only=True)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["verified_bytecode_files"], 3)
        else:
            with self.assertRaisesRegex(packages.PackageError, "bytecode"):
                packages.provision(self.root, verify_only=True)

    def test_windows_loader_case_uses_only_same_resolved_source_candidates(self) -> None:
        packages.provision(self.root)
        source = self.install / "typing_extensions.py"
        loader_filename = str(Path(str(source.parent).swapcase()) / source.name)
        py_compile.compile(str(source), dfile=loader_filename, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        loader_directory = str(Path(loader_filename).parent)
        with patch.object(packages.sys, "path", [loader_directory]):
            candidates = packages._source_filename_spellings(source, self.install)
            if os.name == "nt" and sys.version_info[:2] == (3, 12):
                self.assertIn(loader_filename, candidates)
                self.assertEqual(packages.provision(self.root, verify_only=True)["verified_bytecode_files"], 1)
            else:
                self.assertNotIn(loader_filename, candidates)
                with self.assertRaisesRegex(packages.PackageError, "bytecode"):
                    packages.provision(self.root, verify_only=True)
        # A different directory never becomes an accepted filename source.
        other = self.root / "unrelated-loader"
        other.mkdir()
        with patch.object(packages.sys, "path", [str(other)]):
            self.assertEqual(packages._source_filename_spellings(source, self.install), [str(source)])

    def test_root_module_forged_current_timestamp_cache_is_rejected(self) -> None:
        packages.provision(self.root)
        source = self.install / "typing_extensions.py"
        pinned = source.read_bytes()
        source.write_bytes(b"raise RuntimeError('synthetic untrusted cache must never run')\n")
        cache = Path(py_compile.compile(str(source), doraise=True,
                                        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
        source.write_bytes(pinned)
        data = bytearray(cache.read_bytes())
        data[8:16] = struct.pack("<II", int(source.stat().st_mtime) & 0xFFFFFFFF, len(pinned))
        cache.write_bytes(data)
        expected = "bytecode cache differs" if sys.version_info[:2] == (3, 12) else "bytecode"
        with self.assertRaisesRegex(packages.PackageError, expected):
            packages.provision(self.root, verify_only=True)

    def test_root_bytecode_orphans_unknown_files_directories_and_links_fail(self) -> None:
        packages.provision(self.root)
        directory = self.install / "__pycache__"
        directory.mkdir()
        for name in ("orphan.cpython-312.pyc", "typing_extensions.cpython-312.opt-0.pyc", "notes.txt"):
            with self.subTest(name=name):
                path = directory / name
                path.write_bytes(b"untrusted file")
                with self.assertRaisesRegex(packages.PackageError, "bytecode"):
                    packages.provision(self.root, verify_only=True)
                path.unlink()
        nested = directory / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(packages.PackageError, "not a regular file"):
            packages.provision(self.root, verify_only=True)
        nested.rmdir()
        path = directory / "typing_extensions.cpython-312.pyc"
        path.write_bytes(b"synthetic link target")
        original = Path.is_symlink
        for linked in (directory, path):
            with self.subTest(link=linked.name), patch.object(
                Path, "is_symlink", lambda item: item == linked or original(item)
            ):
                with self.assertRaisesRegex(packages.PackageError, "symlink"):
                    packages.provision(self.root, verify_only=True)
        path.unlink()
        directory.rmdir()
        directory.write_bytes(b"not a directory")
        with self.assertRaisesRegex(packages.PackageError, "not a directory"):
            packages.provision(self.root, verify_only=True)

    def test_zip_path_traversal_windows_aliases_and_stdlib_overwrite_are_rejected(self) -> None:
        package = copy.deepcopy(self.lock["packages"][0])
        for name in ("../escape.py", "/absolute.py", "C:/drive.py", "jsonschema/../escape.py",
                     "jsonschema\\escape.py", "jsonschema//empty.py", "jsonschema/./dot.py",
                     "jsonschema/file.py:stream", "jsonschema/NUL.py", "jsonschema/COM1",
                     "jsonschema/CONIN$", "jsonschema/CONOUT$", "jsonschema/CON .py",
                     "jsonschema/LONGFI~1.py", "jsonschema/embedded space.py",
                     "jsonschema/trailing. ", "jsonschema/trailing.", "jsonschema/question?.py",
                     "os.py", "python312.zip", "sitecustomize.py", "jsonschema/__pycache__/evil.pyc"):
            with self.subTest(member=name):
                payload = _synthetic_wheel(package, extras=[(name, b"bad")])
                with self.assertRaises(packages.PackageError):
                    packages._wheel_files(package, payload)

    def test_zip_duplicate_case_alias_and_file_parent_conflicts_are_rejected(self) -> None:
        for extras in (
            [("jsonschema/__init__.py", b"duplicate")],
            [("jsonschema/CASE.py", b"a"), ("jsonschema/case.py", b"b")],
            [("jsonschema/Dir/a.py", b"a"), ("jsonschema/dir/b.py", b"b")],
            [("jsonschema/parent", b"a"), ("jsonschema/parent/child.py", b"b")],
        ):
            with self.subTest(entries=[name for name, _ in extras]):
                package = copy.deepcopy(self.lock["packages"][0])
                payload = _synthetic_wheel(package, extras=extras)
                with self.assertRaisesRegex(packages.PackageError, "collision|colliding|duplicate"):
                    packages._wheel_files(package, payload)

    def test_zip_symlink_and_special_file_are_rejected(self) -> None:
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR):
            with self.subTest(mode=mode):
                package = copy.deepcopy(self.lock["packages"][0])
                item = zipfile.ZipInfo("jsonschema/link")
                item.create_system = 3
                item.external_attr = (mode | 0o777) << 16
                payload = _synthetic_wheel(package, extras=[(item, b"../../elsewhere")])
                with self.assertRaisesRegex(packages.PackageError, "special"):
                    packages._wheel_files(package, payload)

    def test_payload_and_member_bounds_are_enforced(self) -> None:
        package = self.lock["packages"][0]
        payload = self.payloads[package["name"]]
        for constant in ("MAX_MEMBERS", "MAX_MEMBER_BYTES", "MAX_UNPACKED_BYTES"):
            with self.subTest(bound=constant), patch.object(packages, constant, 1):
                with self.assertRaisesRegex(packages.PackageError, "bound"):
                    packages._wheel_files(package, payload)

    def test_hash_version_dependency_tag_and_license_metadata_drift_are_rejected(self) -> None:
        original = self.lock["packages"][0]
        dist = packages._dist_info(original)
        raw = zipfile.ZipFile(io.BytesIO(self.payloads[original["name"]])).read(dist + "/METADATA")
        for old, new in ((b"Version: 4.26.0", b"Version: 4.26.1"),
                         (b"Requires-Python: >=3.10", b"Requires-Python: >=3.13"),
                         (b"attrs>=22.2.0", b"attrs>=99.0.0"),
                         (b"License-Expression: MIT", b"License-Expression: unknown")):
            with self.subTest(field=old.split(b":")[0]):
                package = copy.deepcopy(original)
                payload = _synthetic_wheel(package, changes={dist + "/METADATA": raw.replace(old, new)})
                with self.assertRaisesRegex(packages.PackageError, "metadata mismatch"):
                    packages._wheel_files(package, payload)
        package = copy.deepcopy(original)
        package["metadata_sha256"] = "0" * 64
        with self.assertRaisesRegex(packages.PackageError, "METADATA hash"):
            packages._wheel_files(package, self.payloads[original["name"]])
        with self.assertRaisesRegex(packages.PackageError, "size/hash"):
            packages._wheel_files(original, b"not the pinned wheel")
        payload = _synthetic_wheel(package, changes={dist + "/WHEEL": b"Wheel-Version: 1.0\nTag: cp313-cp313-win_arm64\n"})
        with self.assertRaisesRegex(packages.PackageError, "metadata mismatch"):
            packages._wheel_files(package, payload)

    def test_lock_incompatible_python_unsatisfied_transitive_and_unknown_marker_fail(self) -> None:
        original = copy.deepcopy(self.lock)
        mutations = (
            lambda lock: lock["packages"][0].update(requires_python=">=3.13"),
            lambda lock: lock["packages"][0].update(requires_dist=["attrs>=99.0.0"]),
            lambda lock: lock["packages"][0].update(requires_dist=["missing>=1.0.0"]),
            lambda lock: lock["packages"][0].update(requires_dist=["attrs>=1.0; os_name == 'nt'"]),
            lambda lock: lock["packages"].pop(),
            lambda lock: lock["packages"][0].update(roots=["os.py", "jsonschema-4.26.0.dist-info"]),
            lambda lock: lock["packages"][0].update(tags=["cp313-cp313-win_amd64"]),
            lambda lock: lock["packages"][0].update(url="https://example.com/package.whl"),
            lambda lock: lock["packages"][0].update(filename="../escape.whl"),
            lambda lock: lock.update(extras=["format"]),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                self.lock = copy.deepcopy(original)
                mutation(self.lock)
                self._save_lock()
                with self.assertRaises(packages.PackageError):
                    packages.load_lock(self.root / packages.LOCK_RELATIVE_PATH)

    def test_symlinked_cache_install_or_package_destination_is_rejected(self) -> None:
        original = Path.is_symlink
        for target in (self.root / ".toolcache", self.root / packages.CACHE_RELATIVE_PATH,
                       self.install, self.install / "jsonschema"):
            with self.subTest(target=target.name):
                with patch.object(Path, "is_symlink", lambda path: path == target or original(path)):
                    with self.assertRaisesRegex(packages.PackageError, "symlink"):
                        packages.provision(self.root)

    def test_only_official_https_urls_and_redirects_are_allowed(self) -> None:
        package = self.lock["packages"][0]
        for url in (package["url"].replace("https:", "http:"),
                    package["url"].replace("files.pythonhosted.org", "example.org"),
                    package["url"] + "?query=1", package["url"] + "#fragment",
                    package["url"].replace("files.pythonhosted.org", "user@files.pythonhosted.org")):
            with self.subTest(url_category=url.split(":")[0]):
                with self.assertRaisesRegex(packages.PackageError, "official"):
                    packages._wheel_url(url, package["filename"])
                with self.assertRaises(packages.PackageError):
                    packages._OfficialRedirects().redirect_request(
                        urllib.request.Request(package["url"]), None, 302, "redirect", {}, url
                    )

    def test_download_rejects_wrong_payload_without_installing(self) -> None:
        package = self.lock["packages"][0]
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = package["url"]
        response.read.return_value = b"wrong"
        opener = unittest.mock.MagicMock()
        opener.open.return_value = response
        # Stop only this test's offline stub to exercise the real bounded reader.
        self.download.stop()
        with patch.object(packages.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(packages.PackageError, "download size/hash"):
                packages._download(package)
        response.read.assert_called_once_with(package["size"] + 1)


if __name__ == "__main__":
    unittest.main()
