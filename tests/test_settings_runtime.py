import json
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.settings import (
    POLICY_FIELDS,
    SettingsError,
    load_runtime_settings,
    load_settings,
)


class SettingsRuntimeOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)
        self.env_file = self.tmpdir / ".env"
        self.overrides_file = self.tmpdir / "runtime.json"

    def _write_env(self, content: str) -> None:
        self.env_file.write_text(content, encoding="utf-8")

    def _write_overrides(self, payload: dict) -> None:
        self.overrides_file.write_text(json.dumps(payload), encoding="utf-8")

    def test_load_settings_applies_runtime_overrides_on_top_of_env(self):
        self._write_env(
            "CPA_ENDPOINT=https://example.com\n"
            "CPA_TOKEN=secret\n"
            "CPA_QUOTA_THRESHOLD=80\n"
        )
        self._write_overrides({"quota_threshold": 60, "enable_refresh": False})
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
        self.assertEqual(settings.quota_threshold, 60)
        self.assertFalse(settings.enable_refresh)
        self.assertEqual(settings.cpa_endpoint, "https://example.com")

    def test_load_settings_ignores_unknown_override_keys(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        self._write_overrides({"who_knows": "what"})
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
        self.assertEqual(settings.cpa_token, "secret")

    def test_load_settings_rejects_bad_override_value(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        self._write_overrides({"quota_threshold": "abc"})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)

    def test_load_settings_rejects_malformed_runtime_overrides(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        self.overrides_file.write_text("{not-json", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)

    def test_load_settings_rejects_non_object_runtime_overrides(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        self.overrides_file.write_text("[1, 2, 3]", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)

    def test_load_settings_rejects_fractional_integer_override(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        self._write_overrides({"quota_threshold": 50.5})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)

    def test_runtime_settings_update_persists_round_trip(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        with patch.dict(os.environ, {}, clear=True):
            runtime = load_runtime_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
            runtime.update({"quota_threshold": 50, "interval_seconds": 600})
            self.assertEqual(runtime.snapshot().quota_threshold, 50)
            persisted = json.loads(self.overrides_file.read_text(encoding="utf-8"))
            self.assertEqual(persisted["quota_threshold"], 50)
            self.assertEqual(persisted["interval_seconds"], 600)
            # Reload — overrides should apply over env.
            reloaded = load_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
            self.assertEqual(reloaded.quota_threshold, 50)
            self.assertEqual(reloaded.interval_seconds, 600)

    @unittest.skipUnless(os.name == "posix", "POSIX file modes only")
    def test_runtime_settings_update_persists_private_permissions(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        with patch.dict(os.environ, {}, clear=True):
            runtime = load_runtime_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
            runtime.update({"cpa_token": "new-secret", "ui_token": "ui-secret"})

        mode = stat.S_IMODE(self.overrides_file.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_runtime_settings_update_validates_constraints(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        with patch.dict(os.environ, {}, clear=True):
            runtime = load_runtime_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
            with self.assertRaises(SettingsError):
                runtime.update({"quota_threshold": 150})
            with self.assertRaises(SettingsError):
                runtime.update({"unknown_field": 1})
            with self.assertRaises(SettingsError):
                runtime.update({})

    def test_runtime_settings_update_does_not_change_memory_when_persist_fails(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        with patch.dict(os.environ, {}, clear=True):
            runtime = load_runtime_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
        with patch.object(runtime, "_persist_overrides_locked", side_effect=SettingsError("disk full")):
            with self.assertRaises(SettingsError):
                runtime.update({"quota_threshold": 50})
        self.assertEqual(runtime.snapshot().quota_threshold, 100)

    def test_field_sources_marks_env_and_override(self):
        self._write_env(
            "CPA_ENDPOINT=https://example.com\n"
            "CPA_TOKEN=secret\n"
            "CPA_WORKER_THREADS=4\n"
        )
        self._write_overrides({"quota_threshold": 70})
        with patch.dict(os.environ, {}, clear=True):
            runtime = load_runtime_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
        sources = runtime.field_sources()
        self.assertEqual(sources["worker_threads"], "env")
        self.assertEqual(sources["quota_threshold"], "override")
        self.assertEqual(sources["interval_seconds"], "default")

    def test_listeners_invoked_on_update(self):
        self._write_env("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n")
        with patch.dict(os.environ, {}, clear=True):
            runtime = load_runtime_settings(env_file=self.env_file, runtime_overrides_file=self.overrides_file)
        seen = []
        runtime.add_listener(lambda snap, changed: seen.append(changed))
        runtime.update({"quota_threshold": 33})
        self.assertEqual(seen, [{"quota_threshold": 33}])

    def test_policy_fields_match_expected(self):
        # Sanity-check the contract that the UI relies on.
        self.assertIn("quota_threshold", POLICY_FIELDS)
        self.assertIn("interval_seconds", POLICY_FIELDS)
        self.assertIn("enable_refresh", POLICY_FIELDS)


if __name__ == "__main__":
    unittest.main()
