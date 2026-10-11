"""Pinned baseline and real runtime attestation regressions (no downloads)."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import bootstrap_toolchain, embedded_python, verify_toolchain


ROOT = Path(__file__).resolve().parents[2]


class EmbeddedPythonTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="r94-python-baseline-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / ".toolcache/python"
        self.install.mkdir(parents=True)
        archive = self.root / ".toolcache/downloads" / embedded_python.ARCHIVE_NAME
        archive.parent.mkdir()
        files = {"python.exe": b"synthetic-exe", "python312.dll": b"synthetic-dll",
                 "python312._pth": b"python312.zip\r\n.\r\n#import site\r\n"}
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, payload in files.items():
                bundle.writestr(name, payload)
                (self.install / name).write_bytes(payload)
        self.archive = archive
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.root / "tools").mkdir()
        (self.root / "tools/python-packages.lock.json").write_bytes(
            (ROOT / "tools/python-packages.lock.json").read_bytes())
        (self.root / "tools/toolchain.lock.json").write_text(json.dumps({"tools": {
            "python": {"version": "3.12.10-embed-amd64", "sha256": digest}}}), "utf-8")
        self.addCleanup(patch.stopall)
        patch.object(embedded_python, "ARCHIVE_SHA256", digest).start()

    def runtime(self, **overrides):
        fields = dict(implementation=SimpleNamespace(name="cpython"),
                      version_info=(3, 12, 10), platform="win32",
                      executable=str(self.install / "python.exe"),
                      flags=SimpleNamespace(isolated=1, no_site=1))
        fields.update(overrides)
        stack = ExitStack()
        for name, value in fields.items():
            stack.enter_context(patch.object(embedded_python.sys, name, value))
        stack.enter_context(patch.object(embedded_python.struct, "calcsize", return_value=8))
        return stack

    def test_reviewed_fixture_passes_without_writes(self) -> None:
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with self.runtime():
            report = embedded_python.attest(self.root)
        self.assertEqual(report["verified_files"], 3)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_wrong_runtime_rejected_before_baseline(self) -> None:
        cases = [dict(implementation=SimpleNamespace(name="pypy")),
                 dict(version_info=(3, 11, 10)), dict(version_info=(3, 12, 9)),
                 dict(platform="linux"), dict(executable=str(self.root / "python.exe")),
                 dict(flags=SimpleNamespace(isolated=0, no_site=1)),
                 dict(flags=SimpleNamespace(isolated=1, no_site=0))]
        for values in cases:
            with self.subTest(values=values), self.runtime(**values), patch.object(embedded_python, "verify_baseline") as baseline:
                with self.assertRaisesRegex(RuntimeError, "requires isolated"):
                    embedded_python.attest(self.root)
                baseline.assert_not_called()

    def test_wrong_pointer_width_rejected(self) -> None:
        with self.runtime(), patch.object(embedded_python.struct, "calcsize", return_value=4):
            with self.assertRaisesRegex(RuntimeError, "Windows x64"):
                embedded_python.attest(self.root)

    def test_modified_pth_rejected(self) -> None:
        (self.install / "python312._pth").write_bytes(b"import site\n")
        with self.assertRaisesRegex(RuntimeError, "baseline mismatch: python312._pth"):
            embedded_python.verify_baseline(self.root)

    def test_additional_pth_rejected(self) -> None:
        (self.install / "python._pth").write_bytes(b".\n")
        with self.assertRaisesRegex(RuntimeError, "path configuration"):
            embedded_python.verify_baseline(self.root)

    def test_dll_corruption_rejected(self) -> None:
        (self.install / "python312.dll").write_bytes(b"bad")
        with self.assertRaisesRegex(RuntimeError, "baseline mismatch"):
            embedded_python.verify_baseline(self.root)

    def test_additional_executable_or_import_shadow_rejected(self) -> None:
        for name in ("version.dll", "sitecustomize.py", "typing.py", "bad.pyd", "bad.exe"):
            with self.subTest(name=name):
                path = self.install / name
                path.write_bytes(b"unexpected")
                with self.assertRaisesRegex(RuntimeError, "top-level entry"):
                    embedded_python.verify_baseline(self.root)
                path.unlink()

    def test_reviewed_package_roots_allowed_with_exact_types(self) -> None:
        (self.install / "attrs").mkdir()
        (self.install / "typing_extensions.py").write_bytes(b"verified separately")
        self.assertEqual(embedded_python.verify_baseline(self.root)["status"], "pass")
        (self.install / "typing_extensions.py").unlink()
        (self.install / "typing_extensions.py").mkdir()
        with self.assertRaisesRegex(RuntimeError, "top-level entry"):
            embedded_python.verify_baseline(self.root)

    def test_archive_corruption_rejected(self) -> None:
        self.archive.write_bytes(self.archive.read_bytes() + b"bad")
        with self.assertRaisesRegex(RuntimeError, "archive checksum"):
            embedded_python.verify_baseline(self.root)

    def test_missing_baseline_rejected(self) -> None:
        (self.install / "python.exe").unlink()
        with self.assertRaisesRegex(RuntimeError, "not a regular file"):
            embedded_python.verify_baseline(self.root)

    def test_bootstrap_attests_before_provision_or_download(self) -> None:
        with patch.object(bootstrap_toolchain.sys, "argv", ["bootstrap", "--root", str(self.root)]), \
                patch.object(bootstrap_toolchain, "attest", side_effect=RuntimeError("bad runtime")), \
                patch.object(bootstrap_toolchain, "provision") as provision, \
                patch.object(bootstrap_toolchain, "download") as download:
            with self.assertRaisesRegex(RuntimeError, "bad runtime"):
                bootstrap_toolchain.main()
            provision.assert_not_called()
            download.assert_not_called()

    def test_verifier_attests_before_package_verification(self) -> None:
        with patch.object(verify_toolchain, "attest", side_effect=RuntimeError("bad runtime")), \
                patch.object(verify_toolchain, "provision") as provision:
            report = verify_toolchain.verify(ROOT, verify_archives=False)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("bad runtime" in issue for issue in report["issues"]))
            provision.assert_not_called()

    def test_powershell_checks_before_interpreter_launch(self) -> None:
        source = (ROOT / "tools/bootstrap.ps1").read_text("utf-8")
        self.assertLess(source.index("$BaselineZip ="), source.index("Start-Process"))
        self.assertLess(source.index("Portable Python baseline mismatch"), source.index("Start-Process"))
        self.assertIn("$BaselineEntry.Length", source)
        self.assertIn("$PathConfigs.Count -ne 1", source)

    def test_native_powershell_rejects_tampering_before_python_launch(self) -> None:
        # Exercise the real pre-execution boundary with the actual pinned ZIP.
        # No altered interpreter is ever launched and no network is needed.
        with tempfile.TemporaryDirectory(prefix="r94-python-prelaunch-") as folder:
            root = Path(folder)
            install = root / ".toolcache/python"
            install.mkdir(parents=True)
            archive = root / ".toolcache/downloads" / embedded_python.ARCHIVE_NAME
            archive.parent.mkdir()
            shutil.copyfile(ROOT / ".toolcache/downloads" / embedded_python.ARCHIVE_NAME, archive)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(install)
            (root / "tools").mkdir()
            script = root / "tools/bootstrap.ps1"
            shutil.copyfile(ROOT / "tools/bootstrap.ps1", script)
            for name, message in (("version.dll", "Unexpected portable Python top-level entry"),
                                  ("python312._pth", "Portable Python baseline mismatch")):
                with self.subTest(name=name):
                    path = install / name
                    original = path.read_bytes() if path.exists() else None
                    path.write_bytes(b"tampered-fixture")
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                        capture_output=True, text=True, timeout=30,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stdout + result.stderr)
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
