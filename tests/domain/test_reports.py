from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cpa_keeper.domain.reports import ProviderRunReport, RunPhase


class ProviderRunReportTests(unittest.TestCase):
    def test_report_is_frozen_and_counts_completed_outcomes(self) -> None:
        report = ProviderRunReport(
            provider_id="codex",
            phase=RunPhase.INSPECTION,
            scanned=2,
            matched=1,
            planned=1,
            applied=1,
            skipped=1,
        )

        self.assertEqual(report.total_outcomes, 2)
        with self.assertRaises(FrozenInstanceError):
            report.failed = 1  # type: ignore[misc]

    def test_report_rejects_inconsistent_counts(self) -> None:
        with self.assertRaises(ValueError):
            ProviderRunReport(
                provider_id="codex",
                phase=RunPhase.FAST_SCAN,
                scanned=1,
                matched=2,
            )


if __name__ == "__main__":
    unittest.main()
