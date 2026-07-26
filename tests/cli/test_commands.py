from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import logging
import unittest
from unittest.mock import patch

from cpa_keeper.application.results import FastScanResult, MaintenanceResult, RunStatus
from cpa_keeper.cli.commands import main


class FakeFastScan:
    def __init__(self) -> None:
        self.calls = 0

    def scan(self) -> FastScanResult:
        self.calls += 1
        return FastScanResult(status=RunStatus.SUCCESS, reports=())


class FakeScheduler:
    def __init__(self, *, run_status: RunStatus = RunStatus.SUCCESS) -> None:
        self.once_calls = 0
        self.forever_calls = 0
        self.run_status = run_status

    def run_once(self) -> MaintenanceResult:
        self.once_calls += 1
        return MaintenanceResult(status=self.run_status, reports=())

    def run_forever(self) -> None:
        self.forever_calls += 1


class CliCommandTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_scan_runs_the_single_global_fast_scan(self) -> None:
        fast_scan = FakeFastScan()
        scheduler = FakeScheduler()
        with (
            patch("cpa_keeper.cli.commands._load_config", return_value=object()),
            patch("cpa_keeper.cli.commands._build_services", return_value=(fast_scan, scheduler)),
        ):
            code, _stdout, stderr = self._run(["scan"])

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(fast_scan.calls, 1)
        self.assertEqual(scheduler.once_calls, 0)

    def test_run_executes_one_fast_scan_and_existing_inspection_callbacks(self) -> None:
        fast_scan = FakeFastScan()
        scheduler = FakeScheduler()
        with (
            patch("cpa_keeper.cli.commands._load_config", return_value=object()),
            patch("cpa_keeper.cli.commands._build_services", return_value=(fast_scan, scheduler)),
        ):
            code, _stdout, stderr = self._run(["run"])

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(fast_scan.calls, 0)
        self.assertEqual(scheduler.once_calls, 1)

    def test_run_returns_a_failure_exit_code_when_inspection_fails(self) -> None:
        fast_scan = FakeFastScan()
        scheduler = FakeScheduler(run_status=RunStatus.PARTIAL_FAILURE)
        with (
            patch("cpa_keeper.cli.commands._load_config", return_value=object()),
            patch("cpa_keeper.cli.commands._build_services", return_value=(fast_scan, scheduler)),
        ):
            code, _stdout, stderr = self._run(["run"])

        self.assertEqual((code, stderr), (1, ""))

    def test_daemon_hands_control_to_the_fast_scan_scheduler(self) -> None:
        fast_scan = FakeFastScan()
        scheduler = FakeScheduler()
        with (
            patch("cpa_keeper.cli.commands._load_config", return_value=object()),
            patch("cpa_keeper.cli.commands._build_services", return_value=(fast_scan, scheduler)),
        ):
            code, _stdout, stderr = self._run(["daemon"])

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(scheduler.forever_calls, 1)

    def test_config_validate_only_loads_the_configuration(self) -> None:
        with patch("cpa_keeper.cli.commands._load_config", return_value=object()):
            code, stdout, stderr = self._run(["config", "validate"])

        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("config valid", stdout)

    def test_unclassified_error_returns_safe_exit_code_and_log(self) -> None:
        sensitive_value = "sensitive-value-must-not-leak"
        with (
            patch(
                "cpa_keeper.cli.commands._load_config",
                side_effect=RuntimeError(sensitive_value),
            ),
            patch("cpa_keeper.cli.commands.LOGGER.error") as log_error,
        ):
            try:
                code, stdout, stderr = self._run(["doctor"])
            except RuntimeError as exc:
                self.fail(f"unclassified exception escaped: {exc}")

        self.assertEqual(code, 4)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "internal_error\n")
        log_error.assert_called_once_with("event=internal_error outcome=failed")
        self.assertNotIn(sensitive_value, stdout + stderr + repr(log_error.call_args))

    def test_main_configures_info_logging_for_daemon_events(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            patch("cpa_keeper.cli.commands._load_config", return_value=object()),
            patch("cpa_keeper.cli.commands.logging.basicConfig") as configure_logging,
        ):
            main(["config", "validate"])

        configure_logging.assert_called_once_with(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            stream=stdout,
        )

