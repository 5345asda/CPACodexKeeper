from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cpa_keeper.config.loader import load_runtime_config
from config.test_fast_scan_config import CONFIG


class FastScanLoaderTests(unittest.TestCase):
    def test_loader_combines_target_toml_with_connection_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = directory / "config.toml"
            env_path = directory / ".env"
            config_path.write_text(CONFIG, encoding="utf-8")
            env_path.write_text(
                "CPA_ENDPOINT=https://cpa.example.test\n"
                "CPA_TOKEN=file-token\n"
                "CPA_PROXY=https://proxy.example.test\n",
                encoding="utf-8",
            )

            runtime = load_runtime_config(
                config_path=config_path,
                env_file=env_path,
                environ={"CPA_TOKEN": "process-token"},
            )

        self.assertEqual(runtime.behavior.fast_scan.interval_seconds, 60)
        self.assertEqual(runtime.connection.endpoint, "https://cpa.example.test")
        self.assertEqual(runtime.connection.token, "process-token")
        self.assertEqual(runtime.connection.proxy, "https://proxy.example.test")
        self.assertNotIn("process-token", repr(runtime))

    def test_an_explicit_empty_proxy_overrides_the_dotenv_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = directory / "config.toml"
            env_path = directory / ".env"
            config_path.write_text(CONFIG, encoding="utf-8")
            env_path.write_text(
                "CPA_ENDPOINT=https://cpa.example.test\n"
                "CPA_TOKEN=file-token\n"
                "CPA_PROXY=https://proxy.example.test\n",
                encoding="utf-8",
            )

            runtime = load_runtime_config(
                config_path=config_path,
                env_file=env_path,
                environ={"CPA_PROXY": ""},
            )

        self.assertIsNone(runtime.connection.proxy)

