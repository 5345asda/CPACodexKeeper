from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cpa_keeper.application.mutation_coordinator import AuthFileMutationCoordinator
from cpa_keeper.infrastructure.cpa_api import CpaOperationResult


def _success() -> CpaOperationResult:
    return CpaOperationResult(ok=True, status_code=200)


class AuthFileMutationCoordinatorTests(unittest.TestCase):
    def test_successful_fast_scan_invalidates_an_older_inspection_snapshot(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("shared.json")
        inspection_calls: list[str] = []

        fast_scan = coordinator.execute_fast_scan("shared.json", _success)
        inspection = coordinator.execute_inspection(
            "shared.json",
            expected_generation,
            lambda: inspection_calls.append("inspection") or _success(),
        )

        self.assertTrue(fast_scan.ok)
        self.assertIsNone(inspection)
        self.assertEqual(inspection_calls, [])

    def test_failed_fast_scan_invalidates_an_older_inspection_snapshot(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("shared.json")

        coordinator.execute_fast_scan(
            "shared.json",
            lambda: CpaOperationResult(ok=False, status_code=503, error_code="http_503"),
        )
        inspection_calls: list[str] = []
        inspection = coordinator.execute_inspection(
            "shared.json",
            expected_generation,
            lambda: inspection_calls.append("inspection") or _success(),
        )

        self.assertIsNone(inspection)
        self.assertEqual(inspection_calls, [])

    def test_unknown_fast_scan_outcome_invalidates_an_older_inspection_snapshot(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("shared.json")
        inspection_calls: list[str] = []

        coordinator.execute_fast_scan(
            "shared.json",
            lambda: CpaOperationResult(
                ok=False,
                status_code=None,
                error_code="transport_error",
            ),
        )
        inspection = coordinator.execute_inspection(
            "shared.json",
            expected_generation,
            lambda: inspection_calls.append("inspection") or _success(),
        )

        self.assertIsNone(inspection)
        self.assertEqual(inspection_calls, [])

    def test_fast_scan_exception_invalidates_an_older_inspection_snapshot(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("shared.json")
        inspection_calls: list[str] = []

        def fail() -> CpaOperationResult:
            raise RuntimeError("write failed")

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            coordinator.execute_fast_scan("shared.json", fail)

        inspection = coordinator.execute_inspection(
            "shared.json",
            expected_generation,
            lambda: inspection_calls.append("inspection") or _success(),
        )

        self.assertIsNone(inspection)
        self.assertEqual(inspection_calls, [])

    def test_same_resource_fast_scan_waits_for_an_inflight_inspection_write(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("shared.json")
        inspection_started = threading.Event()
        release_inspection = threading.Event()
        fast_scan_done = threading.Event()
        events: list[str] = []

        def inspection_action() -> CpaOperationResult:
            events.append("inspection-started")
            inspection_started.set()
            release_inspection.wait(timeout=1)
            events.append("inspection-finished")
            return _success()

        inspection_thread = threading.Thread(
            target=lambda: coordinator.execute_inspection(
                "shared.json", expected_generation, inspection_action
            )
        )
        inspection_thread.start()
        self.assertTrue(inspection_started.wait(timeout=1))

        def fast_scan_action() -> CpaOperationResult:
            events.append("fast-scan")
            fast_scan_done.set()
            return _success()

        fast_scan_thread = threading.Thread(
            target=lambda: coordinator.execute_fast_scan("shared.json", fast_scan_action)
        )
        fast_scan_thread.start()

        self.assertFalse(fast_scan_done.wait(timeout=0.1))
        release_inspection.set()
        inspection_thread.join(timeout=1)
        fast_scan_thread.join(timeout=1)

        self.assertTrue(fast_scan_done.is_set())
        self.assertEqual(events, ["inspection-started", "inspection-finished", "fast-scan"])

    def test_different_resources_do_not_share_a_mutation_lock(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("inspection.json")
        inspection_started = threading.Event()
        release_inspection = threading.Event()
        fast_scan_done = threading.Event()

        def inspection_action() -> CpaOperationResult:
            inspection_started.set()
            release_inspection.wait(timeout=1)
            return _success()

        inspection_thread = threading.Thread(
            target=lambda: coordinator.execute_inspection(
                "inspection.json", expected_generation, inspection_action
            )
        )
        inspection_thread.start()
        self.assertTrue(inspection_started.wait(timeout=1))

        fast_scan_thread = threading.Thread(
            target=lambda: (
                coordinator.execute_fast_scan("fast-scan.json", _success),
                fast_scan_done.set(),
            )
        )
        fast_scan_thread.start()

        self.assertTrue(fast_scan_done.wait(timeout=1))
        release_inspection.set()
        inspection_thread.join(timeout=1)
        fast_scan_thread.join(timeout=1)

    def test_snapshot_reads_default_epochs_without_allocating_resource_state(self) -> None:
        coordinator = AuthFileMutationCoordinator()

        snapshot = coordinator.snapshot_generations(("codex.json", "xai.json"))

        self.assertEqual(dict(snapshot), {"codex.json": 0, "xai.json": 0})
        self.assertEqual(coordinator.capture_generation("other.json"), 0)
        self.assertEqual(coordinator._states, {})

    def test_snapshot_generations_does_not_wait_for_an_inflight_inspection_write(self) -> None:
        coordinator = AuthFileMutationCoordinator()
        expected_generation = coordinator.capture_generation("inspection.json")
        inspection_started = threading.Event()
        release_inspection = threading.Event()
        snapshot_done = threading.Event()
        snapshots: list[dict[str, int]] = []

        def inspection_action() -> CpaOperationResult:
            inspection_started.set()
            release_inspection.wait(timeout=1)
            return _success()

        inspection_thread = threading.Thread(
            target=lambda: coordinator.execute_inspection(
                "inspection.json", expected_generation, inspection_action
            )
        )
        inspection_thread.start()
        self.assertTrue(inspection_started.wait(timeout=1))

        def capture_snapshot() -> None:
            snapshots.append(
                dict(coordinator.snapshot_generations(("inspection.json", "fast-scan.json")))
            )
            snapshot_done.set()

        snapshot_thread = threading.Thread(target=capture_snapshot)
        snapshot_thread.start()

        self.assertTrue(snapshot_done.wait(timeout=0.1))
        self.assertEqual(snapshots, [{"inspection.json": 0, "fast-scan.json": 0}])
        release_inspection.set()
        inspection_thread.join(timeout=1)
        snapshot_thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
