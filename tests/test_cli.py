import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.cli import build_arg_parser, main
from src.settings import Settings


class CLITests(unittest.TestCase):
    def test_defaults_to_daemon_mode(self):
        parser = build_arg_parser()
        args = parser.parse_args([])

        self.assertTrue(args.daemon)
        self.assertFalse(args.xai_error_sweep_once)

    def test_once_disables_daemon_mode(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--once"])

        self.assertFalse(args.daemon)

    def test_xai_error_sweep_once_is_explicit_mode(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--xai-error-sweep-once"])

        self.assertTrue(args.xai_error_sweep_once)
        self.assertTrue(args.daemon)

    def test_xai_error_sweep_is_mutually_exclusive_with_daemon(self):
        parser = build_arg_parser()

        with self.assertRaises(SystemExit) as context:
            parser.parse_args(["--daemon", "--xai-error-sweep-once"])

        self.assertEqual(context.exception.code, 2)

    def test_xai_error_sweep_is_mutually_exclusive_with_once(self):
        parser = build_arg_parser()

        with self.assertRaises(SystemExit) as context:
            parser.parse_args(["--once", "--xai-error-sweep-once"])

        self.assertEqual(context.exception.code, 2)

    def test_xai_error_sweep_does_not_allow_abbreviated_flag(self):
        parser = build_arg_parser()

        with self.assertRaises(SystemExit) as context:
            parser.parse_args(["--xai"])

        self.assertEqual(context.exception.code, 2)

    def test_xai_error_sweep_help_requires_explicit_enable_flag(self):
        help_text = " ".join(build_arg_parser().format_help().split())

        self.assertIn("CPA_XAI_PERMISSION_DENIED_DELETE_ENABLED=true", help_text)
        self.assertIn("default false", help_text)

    @patch("src.cli.load_settings")
    @patch("src.cli.CPACodexKeeper")
    @patch("sys.argv", ["prog", "--once"])
    def test_main_runs_once(self, keeper_cls, load_settings_mock):
        load_settings_mock.return_value = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
        )
        keeper = keeper_cls.return_value

        exit_code = main()

        self.assertEqual(exit_code, 0)
        keeper.run.assert_called_once()
        keeper.run_forever.assert_not_called()
        keeper.sweep_error_status_once.assert_not_called()

    @patch("src.cli.load_settings")
    @patch("src.cli.CPACodexKeeper")
    @patch("sys.argv", ["prog"])
    def test_main_defaults_to_daemon(self, keeper_cls, load_settings_mock):
        settings = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
        )
        load_settings_mock.return_value = settings
        keeper = keeper_cls.return_value

        exit_code = main()

        self.assertEqual(exit_code, 0)
        keeper.run_forever.assert_called_once_with(interval_seconds=settings.interval_seconds)
        keeper.run.assert_not_called()
        keeper.sweep_error_status_once.assert_not_called()

    @patch("src.cli.load_settings")
    @patch("src.cli.CPACodexKeeper")
    @patch("sys.argv", ["prog", "--xai-error-sweep-once"])
    def test_main_runs_xai_error_sweep_once_without_full_maintenance(
        self, keeper_cls, load_settings_mock
    ):
        load_settings_mock.return_value = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
        )
        keeper = keeper_cls.return_value
        keeper.sweep_error_status_once.return_value = {"failed": 0}

        exit_code = main()

        self.assertEqual(exit_code, 0)
        keeper.sweep_error_status_once.assert_called_once_with(allowed_types={"xai"})
        keeper.run.assert_not_called()
        keeper.run_forever.assert_not_called()

    @patch("src.cli.load_settings")
    @patch("src.cli.CPACodexKeeper")
    @patch("sys.argv", ["prog", "--xai-error-sweep-once"])
    def test_xai_error_sweep_once_returns_nonzero_when_sweep_fails(
        self, keeper_cls, load_settings_mock
    ):
        load_settings_mock.return_value = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
        )
        keeper = keeper_cls.return_value
        keeper.sweep_error_status_once.return_value = {"failed": 1}

        exit_code = main()

        self.assertEqual(exit_code, 1)
        keeper.sweep_error_status_once.assert_called_once_with(allowed_types={"xai"})
        keeper.run.assert_not_called()
        keeper.run_forever.assert_not_called()

    @patch("src.cli.load_settings")
    @patch("src.cli.CPACodexKeeper")
    @patch("sys.argv", ["prog", "--dry-run", "--xai-error-sweep-once"])
    def test_xai_error_sweep_once_passes_dry_run_to_maintainer(
        self, keeper_cls, load_settings_mock
    ):
        settings = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
        )
        load_settings_mock.return_value = settings
        keeper = keeper_cls.return_value
        keeper.sweep_error_status_once.return_value = {"failed": 0}

        exit_code = main()

        self.assertEqual(exit_code, 0)
        keeper_cls.assert_called_once_with(settings=settings, dry_run=True)
        keeper.sweep_error_status_once.assert_called_once_with(allowed_types={"xai"})
        keeper.run.assert_not_called()
        keeper.run_forever.assert_not_called()
