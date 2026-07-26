from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace

from cpa_keeper.application.inspection_service import CodexInspectionService
from cpa_keeper.application.mutation_coordinator import AuthFileMutationCoordinator
from cpa_keeper.config.fast_scan import InspectionConfig
from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.infrastructure.cpa_api import CpaAuthFileResult, CpaOperationResult
from cpa_keeper.providers.codex.lifecycle_policies import LifecycleAction, LifecycleDecision
from cpa_keeper.providers.codex.refresher import RefreshUpload


def _inspection_config(*, workers: int = 2) -> InspectionConfig:
    return InspectionConfig(
        interval_seconds=1800,
        workers=workers,
        usage_timeout_seconds=15,
        quota_threshold_percent=100,
        refresh_enabled=True,
        refresh_before_expiry_days=3,
    )


class FakeCpaApi:
    def __init__(self) -> None:
        self.detail_calls: list[str] = []
        self.mutation_calls: list[tuple[str, object]] = []
        self.failed_details: set[str] = set()

    def get_auth_file(self, name: str) -> CpaAuthFileResult:
        self.detail_calls.append(name)
        if name in self.failed_details:
            return CpaAuthFileResult(ok=False, error_code="http_503")
        return CpaAuthFileResult(ok=True, payload={"opaque": name})

    def delete_auth_file(self, name: str) -> CpaOperationResult:
        self.mutation_calls.append(("delete", name))
        return CpaOperationResult(ok=True, status_code=204)

    def set_disabled(self, name: str, disabled: bool) -> CpaOperationResult:
        self.mutation_calls.append(("set_disabled", (name, disabled)))
        return CpaOperationResult(ok=True, status_code=200)

    def upload_auth_file(self, name: str, payload: object) -> CpaOperationResult:
        self.mutation_calls.append(("upload", name))
        return CpaOperationResult(ok=True, status_code=200)


class FakeInspector:
    def inspect(self, metadata: AuthFileMetadata, auth_file: object, **_settings: object) -> str:
        del auth_file
        return metadata.name


class FakeEvaluator:
    def evaluate(self, facts: str) -> LifecycleDecision | None:
        if facts == "unchanged.json":
            return None
        return LifecycleDecision(
            resource_name=facts,
            policy_id="codex.inspection.http-401-402.delete",
            action=LifecycleAction.DELETE,
            reason_code="http_401",
        )


class FakeRefresher:
    def refresh(self, metadata: AuthFileMetadata, auth_file: object) -> object:
        raise AssertionError(f"refresh should not run for {metadata.name}: {auth_file}")


class RecordingRefresher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def refresh(self, metadata: AuthFileMetadata, _auth_file: object) -> RefreshUpload:
        self.calls.append(metadata.name)
        return RefreshUpload(payload={"opaque": "replacement"}, restore_disabled=False)


class RefreshEvaluator:
    def evaluate(self, facts: str) -> LifecycleDecision:
        return LifecycleDecision(
            resource_name=facts,
            policy_id="codex.inspection.disabled-expiring.refresh",
            action=LifecycleAction.REFRESH_THEN_UPLOAD,
            reason_code="disabled_expiring",
        )


class DetailDisabledInspector:
    def inspect(self, metadata: AuthFileMetadata, _auth_file: object, **_settings: object) -> object:
        return SimpleNamespace(
            metadata=AuthFileMetadata(
                name=metadata.name,
                provider_id=metadata.provider_id,
                disabled=True,
                status=metadata.status,
            )
        )


class DetailRefreshEvaluator:
    def evaluate(self, facts: object) -> LifecycleDecision:
        return LifecycleDecision(
            resource_name=facts.metadata.name,
            policy_id="codex.inspection.disabled-expiring.refresh",
            action=LifecycleAction.REFRESH_THEN_UPLOAD,
            reason_code="disabled_near_expiry",
        )


class DisabledStateRefresher:
    def __init__(self) -> None:
        self.disabled_states: list[bool] = []

    def refresh(self, metadata: AuthFileMetadata, _auth_file: object) -> RefreshUpload:
        self.disabled_states.append(metadata.disabled)
        return RefreshUpload(payload={"opaque": "replacement"}, restore_disabled=metadata.disabled)


class CodexInspectionServiceTests(unittest.TestCase):
    def _service(
        self,
        cpa_api: FakeCpaApi,
        *,
        workers: int = 2,
        mutation_coordinator: AuthFileMutationCoordinator | None = None,
        evaluator: object | None = None,
        refresher: object | None = None,
        inspector: object | None = None,
    ) -> CodexInspectionService:
        return CodexInspectionService(
            cpa_api,
            config=_inspection_config(workers=workers),
            inspector=inspector or FakeInspector(),
            evaluator=evaluator or FakeEvaluator(),
            refresher=refresher or FakeRefresher(),
            mutation_coordinator=mutation_coordinator or AuthFileMutationCoordinator(),
        )

    @staticmethod
    def _metadata(name: str) -> AuthFileMetadata:
        return AuthFileMetadata(name=name, provider_id="codex", disabled=False, status="active")

    @staticmethod
    def _epochs(metadata: tuple[AuthFileMetadata, ...]) -> dict[str, int]:
        return {item.name: 0 for item in metadata}

    def test_downloads_codex_details_and_applies_the_fixed_lifecycle_action(self) -> None:
        cpa_api = FakeCpaApi()
        metadata = (self._metadata("delete.json"), self._metadata("unchanged.json"))

        with self.assertLogs("cpa_keeper.application.inspection_service", level="INFO") as logs:
            result = self._service(cpa_api).inspect(metadata, expected_epochs=self._epochs(metadata))

        self.assertEqual(cpa_api.detail_calls, ["delete.json", "unchanged.json"])
        self.assertEqual(cpa_api.mutation_calls, [("delete", "delete.json")])
        output = "\n".join(logs.output)
        self.assertIn("reason_code=http_401", output)
        self.assertNotIn("delete.json", output)
        report = result.reports[0]
        self.assertEqual(
            (report.provider_id, report.scanned, report.matched, report.applied, report.skipped),
            ("codex", 2, 1, 1, 1),
        )

    def test_failed_detail_is_counted_and_logs_only_a_hash(self) -> None:
        cpa_api = FakeCpaApi()
        cpa_api.failed_details.add("sensitive-name.json")
        metadata = (self._metadata("sensitive-name.json"),)

        with self.assertLogs("cpa_keeper.application.inspection_service", level="WARNING") as logs:
            result = self._service(cpa_api).inspect(metadata, expected_epochs=self._epochs(metadata))

        self.assertEqual(cpa_api.mutation_calls, [])
        self.assertEqual(result.reports[0].failed, 1)
        output = "\n".join(logs.output)
        self.assertIn("event=inspection_detail", output)
        self.assertIn("error_code=http_503", output)
        self.assertNotIn("sensitive-name.json", output)

    def test_uses_the_configured_worker_limit_for_detail_evaluation(self) -> None:
        cpa_api = FakeCpaApi()
        service = self._service(cpa_api, workers=8)

        with patch("cpa_keeper.application.inspection_service.ThreadPoolExecutor") as executor_type:
            executor = executor_type.return_value.__enter__.return_value
            executor.map.side_effect = lambda function, values: tuple(map(function, values))
            metadata = (self._metadata("delete.json"),)
            service.inspect(metadata, expected_epochs=self._epochs(metadata))

        executor_type.assert_called_once_with(max_workers=8)

    def test_skips_a_lifecycle_write_superseded_by_a_later_fast_scan(self) -> None:
        cpa_api = FakeCpaApi()
        coordinator = AuthFileMutationCoordinator()
        metadata = (self._metadata("delete.json"),)
        expected_epochs = coordinator.snapshot_generations(item.name for item in metadata)
        coordinator.execute_fast_scan("delete.json", lambda: CpaOperationResult(ok=True, status_code=204))

        with self.assertLogs("cpa_keeper.application.inspection_service", level="INFO") as logs:
            result = self._service(cpa_api, mutation_coordinator=coordinator).inspect(
                metadata,
                expected_epochs=expected_epochs,
            )

        self.assertEqual(cpa_api.mutation_calls, [])
        self.assertEqual(result.reports[0].skipped, 1)
        output = "\n".join(logs.output)
        self.assertIn("reason_code=fast_scan_superseded", output)
        self.assertNotIn("delete.json", output)

    def test_superseded_snapshot_does_not_refresh_or_upload(self) -> None:
        cpa_api = FakeCpaApi()
        coordinator = AuthFileMutationCoordinator()
        metadata = (self._metadata("refresh.json"),)
        expected_epochs = coordinator.snapshot_generations(item.name for item in metadata)
        coordinator.execute_fast_scan("refresh.json", lambda: CpaOperationResult(ok=True, status_code=204))
        refresher = RecordingRefresher()

        result = self._service(
            cpa_api,
            mutation_coordinator=coordinator,
            evaluator=RefreshEvaluator(),
            refresher=refresher,
        ).inspect(metadata, expected_epochs=expected_epochs)

        self.assertEqual(refresher.calls, [])
        self.assertEqual(cpa_api.mutation_calls, [])
        self.assertEqual(result.reports[0].skipped, 1)

    def test_refresh_restores_the_disabled_state_from_auth_file_detail(self) -> None:
        cpa_api = FakeCpaApi()
        metadata = (self._metadata("refresh.json"),)
        refresher = DisabledStateRefresher()

        result = self._service(
            cpa_api,
            inspector=DetailDisabledInspector(),
            evaluator=DetailRefreshEvaluator(),
            refresher=refresher,
        ).inspect(metadata, expected_epochs=self._epochs(metadata))

        self.assertEqual(refresher.disabled_states, [True])
        self.assertEqual(
            cpa_api.mutation_calls,
            [("upload", "refresh.json"), ("set_disabled", ("refresh.json", True))],
        )
        self.assertEqual(result.reports[0].applied, 1)

