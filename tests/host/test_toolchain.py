"""Offline tests for the current pinned and bootstrapped toolchain."""

from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from tools.bootstrap_python_packages import provision


ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ToolchainTests(unittest.TestCase):
    def test_real_pinned_schema_validator_and_native_dependency(self) -> None:
        import attr
        import attrs
        import jsonschema
        import jsonschema_specifications
        import referencing
        import rpds
        import typing_extensions
        from importlib.metadata import version

        modules = (attr, attrs, jsonschema, jsonschema_specifications,
                   referencing, rpds, typing_extensions)
        install = (ROOT / ".toolcache/python").resolve()
        for module in modules:
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(install))
        lock = json.loads((ROOT / "tools/python-packages.lock.json").read_text("utf-8"))
        for package in lock["packages"]:
            self.assertEqual(version(package["name"]), package["version"])
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "$defs": {"positive": {"type": "integer", "minimum": 1}},
                  "type": "object", "properties": {"count": {"$ref": "#/$defs/positive"}},
                  "required": ["count"], "additionalProperties": False}
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        validator.validate({"count": 1})
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate({"count": 0})
        self.assertEqual(rpds.HashTrieMap({"count": 1})["count"], 1)
        # Normal real imports generate caches; all must verify on the next run.
        report = provision(ROOT, verify_only=True)
        self.assertEqual(report["changed_files"], 0)
        self.assertGreater(report["verified_bytecode_files"], 0)

    def test_pinned_python_validation_packages_verify_offline(self) -> None:
        report = provision(ROOT, verify_only=True)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["changed_files"], 0)
        self.assertGreater(report["verified_files"], 0)
        self.assertEqual(
            {package["name"] for package in report["packages"]},
            {
                "jsonschema", "attrs", "jsonschema-specifications",
                "referencing", "rpds-py", "typing-extensions",
            },
        )

    def test_lock_and_install_record_match(self) -> None:
        lock_path = ROOT / "tools" / "toolchain.lock.json"
        lock = json.loads(lock_path.read_text("utf-8"))
        installed = json.loads(
            (ROOT / ".toolcache" / "installed.json").read_text("utf-8")
        )
        self.assertEqual(lock["schema"], 1)
        self.assertEqual(
            set(lock["tools"]),
            {"python", "zig", "vasm", "sjasmplus", "blastem"},
        )
        self.assertEqual(installed["lock_sha256"], sha256(lock_path))
        for metadata in lock["tools"].values():
            self.assertEqual(len(metadata["sha256"]), 64)
            int(metadata["sha256"], 16)
            self.assertTrue(metadata["version"])
            self.assertTrue(metadata["url"])
            self.assertTrue(metadata["license"])

    def test_expected_executables_exist(self) -> None:
        for relative in (
            ".toolcache/python/python.exe",
            ".toolcache/bin/vasmm68k_mot.exe",
            ".toolcache/bin/sjasmplus.exe",
            ".toolcache/blastem/blastem.exe",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_assembler_versions(self) -> None:
        cases = (
            (
                ROOT / ".toolcache/bin/vasmm68k_mot.exe",
                ["-v"],
                "vasm 2.0f",
            ),
            (
                ROOT / ".toolcache/bin/sjasmplus.exe",
                ["--nologo", "--version"],
                "1.23.1",
            ),
        )
        for executable, arguments, token in cases:
            with self.subTest(executable=executable.name):
                result = subprocess.run(
                    [str(executable), *arguments],
                    cwd=ROOT,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn(token, result.stdout)

    def test_bootstrap_smoke_outputs(self) -> None:
        expected = {
            "smoke68k.bin": bytes.fromhex("70014E75"),
            "smokez80.bin": bytes.fromhex("3E9476"),
        }
        root = ROOT / "artifacts/reports/toolchain-smoke"
        for name, payload in expected.items():
            with self.subTest(name=name):
                self.assertEqual((root / name).read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
