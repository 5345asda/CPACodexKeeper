import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.settings import SettingsError, load_settings


class SettingsTests(unittest.TestCase):
    def _make_env_file(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        env_path = Path(temp_dir.name) / ".env"
        env_path.write_text(content, encoding="utf-8")
        return env_path

    def test_load_settings_reads_required_values(self):
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret"}, clear=True):
            settings = load_settings()
        self.assertEqual(settings.cpa_endpoint, "https://example.com")
        self.assertEqual(settings.cpa_token, "secret")
        self.assertEqual(settings.interval_seconds, 1800)
        self.assertEqual(settings.worker_threads, 8)
        self.assertTrue(settings.enable_refresh)

    def test_load_settings_reads_from_project_env_file(self):
        env_file = self._make_env_file("CPA_ENDPOINT=https://env-file.example.com\nCPA_TOKEN=file-secret\nCPA_INTERVAL=120\nCPA_WORKER_THREADS=6\n")
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertEqual(settings.cpa_endpoint, "https://env-file.example.com")
        self.assertEqual(settings.cpa_token, "file-secret")
        self.assertEqual(settings.interval_seconds, 120)
        self.assertEqual(settings.worker_threads, 6)

    def test_environment_variables_override_project_env_file(self):
        env_file = self._make_env_file("CPA_ENDPOINT=https://env-file.example.com\nCPA_TOKEN=file-secret\nCPA_WORKER_THREADS=4\n")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://shell.example.com", "CPA_TOKEN": "shell-secret", "CPA_WORKER_THREADS": "12"}, clear=True):
            settings = load_settings(env_file=env_file)
        self.assertEqual(settings.cpa_endpoint, "https://shell.example.com")
        self.assertEqual(settings.cpa_token, "shell-secret")
        self.assertEqual(settings.worker_threads, 12)

    def test_load_settings_rejects_missing_endpoint(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_TOKEN": "secret"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_bad_integer(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret", "CPA_INTERVAL": "abc"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_non_integer_worker_threads(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret", "CPA_WORKER_THREADS": "abc"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)

    def test_load_settings_rejects_zero_worker_threads(self):
        env_file = Path("does-not-exist.env")
        with patch.dict(os.environ, {"CPA_ENDPOINT": "https://example.com", "CPA_TOKEN": "secret", "CPA_WORKER_THREADS": "0"}, clear=True):
            with self.assertRaises(SettingsError):
                load_settings(env_file=env_file)
