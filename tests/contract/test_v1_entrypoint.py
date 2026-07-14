"""Contract tests for the side-effect-free v1 package entrypoint."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from setuptools import find_packages


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


class V1EntrypointContractTests(unittest.TestCase):
    def test_distribution_uses_the_v1_package_and_console_script(self) -> None:
        """The published distribution must expose only the new source package."""
        package_config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(package_config["project"]["name"], "cpa-provider-keeper")
        self.assertEqual(package_config["project"]["version"], "1.0.0rc1")
        self.assertEqual(
            package_config["project"]["scripts"],
            {"cpa-keeper": "cpa_keeper.cli.commands:main"},
        )
        self.assertEqual(package_config["tool"]["setuptools"]["package-dir"], {"": "src"})
        self.assertEqual(
            package_config["tool"]["setuptools"]["packages"]["find"],
            {"where": ["src"], "include": ["cpa_keeper*"]},
        )
        discovered_packages = set(
            find_packages(where=str(SOURCE_ROOT), include=["cpa_keeper*"])
        )
        self.assertIn("cpa_keeper", discovered_packages)
        self.assertIn("cpa_keeper.cli", discovered_packages)
        self.assertIn("cpa_keeper.domain", discovered_packages)
        self.assertIn("cpa_keeper.providers", discovered_packages)
        self.assertTrue(
            all(package == "cpa_keeper" or package.startswith("cpa_keeper.") for package in discovered_packages)
        )
        self.assertNotIn("src", discovered_packages)

    def test_module_help_is_side_effect_free(self) -> None:
        """`--help` must not read .env or establish a network connection."""
        result = self._run_module_with_side_effect_guards("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())
        self.assertIn("cpa-keeper", result.stdout)

    def test_module_version_is_side_effect_free(self) -> None:
        """`--version` must return the release candidate version without setup."""
        result = self._run_module_with_side_effect_guards("--version")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "cpa-keeper 1.0.0rc1")

    def _run_module_with_side_effect_guards(self, argument: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            (temporary_path / ".env").write_text("CPA_TOKEN=must-not-be-read\n", encoding="utf-8")
            guard_directory = temporary_path / "guards"
            guard_directory.mkdir()
            (guard_directory / "sitecustomize.py").write_text(
                """
import builtins
import os
import socket
import sys

_open = builtins.open
_import = builtins.__import__


def guarded_open(file, *args, **kwargs):
    if os.path.basename(os.fspath(file)) == \".env\":
        raise AssertionError(\"v1 entrypoint must not read .env\")
    return _open(file, *args, **kwargs)


def reject_network(*args, **kwargs):
    raise AssertionError(\"v1 entrypoint must not make network connections\")


class RejectRuntimeImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == \"src\" or fullname.startswith(\"cpa_keeper.providers\"):
            raise AssertionError(\"v1 entrypoint must not import provider runtime\")
        return None


builtins.open = guarded_open


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "src" or name.startswith("src."):
        raise AssertionError("v1 entrypoint must not import the legacy src runtime")
    if name == "cpa_keeper.providers" or name.startswith("cpa_keeper.providers."):
        raise AssertionError("v1 help and version must not initialize provider runtime")
    return _import(name, globals, locals, fromlist, level)


builtins.__import__ = guarded_import
socket.create_connection = reject_network
socket.socket.connect = reject_network
sys.meta_path.insert(0, RejectRuntimeImports())
""".lstrip(),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                [str(guard_directory), str(SOURCE_ROOT)]
            )

            return subprocess.run(
                [sys.executable, "-m", "cpa_keeper", argument],
                cwd=temporary_path,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
