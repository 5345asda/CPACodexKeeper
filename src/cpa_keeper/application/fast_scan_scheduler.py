"""APScheduler wiring for one global fast scan and Codex inspection."""

from __future__ import annotations

from collections.abc import Callable
import logging
from threading import Lock

from apscheduler.schedulers.blocking import BlockingScheduler

from cpa_keeper.application.results import (
    FastScanResult,
    InspectionResult,
    MaintenanceResult,
    RunStatus,
)
from cpa_keeper.config.fast_scan import RuntimeConfig


LOGGER = logging.getLogger(__name__)


def _combined_status(statuses: list[RunStatus]) -> RunStatus:
    if RunStatus.UPSTREAM_FAILURE in statuses:
        return RunStatus.UPSTREAM_FAILURE
    if RunStatus.PARTIAL_FAILURE in statuses:
        return RunStatus.PARTIAL_FAILURE
    if RunStatus.SUCCESS in statuses:
        return RunStatus.SUCCESS
    return RunStatus.EMPTY


class FastScanScheduler:
    """Schedule a shared fast scan without serializing long-running inspections."""

    def __init__(
        self,
        fast_scan: object,
        config: RuntimeConfig,
        *,
        inspect: Callable[[str, FastScanResult], InspectionResult | None],
    ) -> None:
        self._fast_scan = fast_scan
        self._config = config
        self._inspect = inspect
        self._latest_snapshot: FastScanResult | None = None
        self._scan_lock = Lock()
        self._snapshot_lock = Lock()

    def run_fast_scan(self) -> FastScanResult:
        """Run one serialized list scan and atomically publish its usable snapshot."""
        with self._scan_lock:
            result = self._fast_scan.scan()
        with self._snapshot_lock:
            self._latest_snapshot = (
                None if result.status is RunStatus.UPSTREAM_FAILURE else result
            )
        return result

    def run_inspection(self, provider_id: str) -> InspectionResult | None:
        """Capture the latest snapshot before running inspection outside the snapshot lock."""
        with self._snapshot_lock:
            snapshot = self._latest_snapshot
        if snapshot is None:
            LOGGER.warning("event=inspection_skipped provider=%s reason=no_fast_scan_snapshot", provider_id)
            return None
        return self._inspect(provider_id, snapshot)

    def run_once(self) -> MaintenanceResult:
        """Run one fast scan and return its combined inspection outcome."""
        fast_scan = self.run_fast_scan()
        reports = list(fast_scan.reports)
        statuses = [fast_scan.status]
        if fast_scan.status is RunStatus.UPSTREAM_FAILURE:
            return MaintenanceResult(status=fast_scan.status, reports=tuple(reports))

        for provider_id, provider in self._config.providers.items():
            inspection = provider.inspection
            if not provider.enabled or inspection is None or not inspection.enabled:
                continue
            result = self.run_inspection(provider_id)
            if result is not None:
                reports.extend(result.reports)
                statuses.append(result.status)
        return MaintenanceResult(status=_combined_status(statuses), reports=tuple(reports))

    def build_scheduler(self) -> BlockingScheduler:
        """Create jobs without starting a blocking scheduler."""
        scheduler = BlockingScheduler()
        scheduler.add_job(
            self.run_fast_scan,
            "interval",
            seconds=self._config.fast_scan.interval_seconds,
            id="fast-scan",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        for provider_id, provider in self._config.providers.items():
            inspection = provider.inspection
            if not provider.enabled or inspection is None or not inspection.enabled:
                continue
            scheduler.add_job(
                self.run_inspection,
                "interval",
                args=(provider_id,),
                seconds=inspection.interval_seconds,
                id=f"inspect-{provider_id}",
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
        return scheduler

    def run_forever(self) -> None:
        """Seed the snapshot before APScheduler begins recurring jobs."""
        self.run_once()
        self.build_scheduler().start()
