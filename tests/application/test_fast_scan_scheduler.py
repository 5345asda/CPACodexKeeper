from __future__ import annotations

import tomllib
import threading
import unittest

from cpa_keeper.application.fast_scan_scheduler import FastScanScheduler
from cpa_keeper.application.results import FastScanResult, InspectionResult, MaintenanceResult, RunStatus
from cpa_keeper.config.fast_scan import parse_config_data
from cpa_keeper.domain.reports import ProviderRunReport, RunPhase
from config.test_fast_scan_config import CONFIG


class FakeFastScan:
    def __init__(self) -> None:
        self.calls = 0
        self.snapshots: list[FastScanResult] = []

    def scan(self) -> FastScanResult:
        self.calls += 1
        snapshot = FastScanResult(status=RunStatus.SUCCESS, reports=())
        self.snapshots.append(snapshot)
        return snapshot


class FastScanSchedulerTests(unittest.TestCase):
    def _scheduler(self, config_data: dict[str, object] | None = None):
        config = parse_config_data(config_data or tomllib.loads(CONFIG))
        scanner = FakeFastScan()
        inspections: list[tuple[str, object]] = []
        scheduler = FastScanScheduler(
            scanner,
            config,
            inspect=lambda provider_id, snapshot: inspections.append((provider_id, snapshot)),
        )
        return scheduler, scanner, inspections

    def test_one_global_fast_scan_job_and_one_codex_inspection_job_are_registered(self) -> None:
        scheduler, _scanner, _inspections = self._scheduler()

        jobs = {job.id: job for job in scheduler.build_scheduler().get_jobs()}

        self.assertEqual(set(jobs), {"fast-scan", "inspect-codex"})
        self.assertEqual(jobs["fast-scan"].trigger.interval.total_seconds(), 60)
        self.assertEqual(jobs["inspect-codex"].trigger.interval.total_seconds(), 1800)
        self.assertEqual(jobs["fast-scan"].max_instances, 1)
        self.assertTrue(jobs["fast-scan"].coalesce)

    def test_disabling_fast_scan_does_not_remove_codex_inspection_job(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["codex"]["fast_scan"]["enabled"] = False
        scheduler, _scanner, _inspections = self._scheduler(data)

        job_ids = {job.id for job in scheduler.build_scheduler().get_jobs()}

        self.assertEqual(job_ids, {"fast-scan", "inspect-codex"})

    def test_disabling_provider_removes_its_inspection_job(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["codex"]["enabled"] = False
        scheduler, _scanner, _inspections = self._scheduler(data)

        job_ids = {job.id for job in scheduler.build_scheduler().get_jobs()}

        self.assertEqual(job_ids, {"fast-scan"})

    def test_inspection_uses_the_latest_fast_scan_result(self) -> None:
        scheduler, scanner, inspections = self._scheduler()

        scheduler.run_fast_scan()
        scheduler.run_inspection("codex")

        self.assertEqual(scanner.calls, 1)
        self.assertEqual(inspections, [("codex", scanner.snapshots[0])])

    def test_one_round_merges_fast_scan_and_inspection_failures(self) -> None:
        config = parse_config_data(tomllib.loads(CONFIG))
        scanner = FakeFastScan()
        inspection = InspectionResult(
            status=RunStatus.PARTIAL_FAILURE,
            reports=(
                ProviderRunReport(
                    provider_id="codex",
                    phase=RunPhase.INSPECTION,
                    scanned=1,
                    failed=1,
                ),
            ),
        )
        scheduler = FastScanScheduler(
            scanner,
            config,
            inspect=lambda _provider_id, _snapshot: inspection,
        )

        result = scheduler.run_once()

        self.assertIsInstance(result, MaintenanceResult)
        self.assertEqual(result.status, RunStatus.PARTIAL_FAILURE)
        self.assertEqual(result.reports, inspection.reports)

    def test_fast_scan_is_not_blocked_by_a_running_inspection(self) -> None:
        config = parse_config_data(tomllib.loads(CONFIG))
        scanner = FakeFastScan()
        inspection_started = threading.Event()
        release_inspection = threading.Event()
        inspection_done = threading.Event()
        scan_done = threading.Event()

        def inspect(_provider_id: str, _snapshot: object) -> None:
            inspection_started.set()
            release_inspection.wait(timeout=2)
            inspection_done.set()

        scheduler = FastScanScheduler(scanner, config, inspect=inspect)
        scheduler.run_fast_scan()
        inspection_thread = threading.Thread(target=lambda: scheduler.run_inspection("codex"))
        inspection_thread.start()
        self.assertTrue(inspection_started.wait(timeout=1))
        fast_scan_thread = threading.Thread(target=lambda: (scheduler.run_fast_scan(), scan_done.set()))
        fast_scan_thread.start()

        self.assertTrue(scan_done.wait(timeout=1))
        self.assertEqual(scanner.calls, 2)
        self.assertFalse(inspection_done.is_set())
        release_inspection.set()
        fast_scan_thread.join(timeout=1)
        inspection_thread.join(timeout=1)
        self.assertTrue(inspection_done.is_set())

