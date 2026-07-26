"""Codex inspection driven by fixed lifecycle behavior and runtime parameters."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from cpa_keeper.config.fast_scan import InspectionConfig
from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.domain.identifiers import resource_hash
from cpa_keeper.domain.reports import ProviderRunReport, RunPhase
from cpa_keeper.infrastructure.cpa_api import CpaApi
from cpa_keeper.providers.codex.inspector import CodexInspector
from cpa_keeper.providers.codex.lifecycle_policies import (
    CodexInspectionPolicyEvaluator,
    LifecycleDecision,
)
from cpa_keeper.providers.codex.mutation import CodexMutationExecutor, RefreshCallback
from cpa_keeper.providers.codex.openai_api import OpenAiApi
from cpa_keeper.providers.codex.refresher import CodexRefresher

from .mutation_coordinator import AuthFileMutationCoordinator
from .results import InspectionResult, RunStatus

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Evaluation:
    decision: LifecycleDecision | None = None
    refresh_callback: RefreshCallback | None = None
    failed: bool = False


class CodexInspectionService:
    """Inspect a Codex snapshot and apply its fixed lifecycle actions."""

    def __init__(
        self,
        cpa_api: CpaApi,
        *,
        config: InspectionConfig,
        inspector: object,
        evaluator: object,
        refresher: object,
        mutation_coordinator: AuthFileMutationCoordinator,
    ) -> None:
        self._cpa_api = cpa_api
        self._config = config
        self._inspector = inspector
        self._evaluator = evaluator
        self._refresher = refresher
        self._executor = CodexMutationExecutor(cpa_api)
        self._mutation_coordinator = mutation_coordinator

    def _evaluate(self, metadata: AuthFileMetadata) -> _Evaluation:
        detail = self._cpa_api.get_auth_file(metadata.name)
        if not detail.ok:
            LOGGER.warning(
                "event=inspection_detail provider=codex outcome=failed resource_hash=%s error_code=%s",
                resource_hash(metadata.name),
                detail.error_code or "cpa_detail_failed",
            )
            return _Evaluation(failed=True)

        facts = self._inspector.inspect(
            metadata,
            detail.payload,
            refresh_before_expiry_days=self._config.refresh_before_expiry_days,
            refresh_enabled=self._config.refresh_enabled,
        )
        decision = self._evaluator.evaluate(facts)
        if decision is None:
            return _Evaluation()
        return _Evaluation(
            decision=decision,
            refresh_callback=lambda _decision: self._refresher.refresh(facts.metadata, detail.payload),
        )

    def inspect(
        self,
        metadata_items: Iterable[AuthFileMetadata],
        *,
        expected_epochs: Mapping[str, int],
    ) -> InspectionResult:
        """Download and inspect Codex credential details with configured concurrency."""
        metadata = tuple(metadata_items)
        counts = {"scanned": len(metadata), "matched": 0, "applied": 0, "skipped": 0, "failed": 0}
        with ThreadPoolExecutor(max_workers=self._config.workers) as executor:
            evaluations = tuple(executor.map(self._evaluate, metadata))

        for item, evaluation in zip(metadata, evaluations, strict=True):
            if evaluation.failed:
                counts["failed"] += 1
                continue
            decision = evaluation.decision
            if decision is None:
                counts["skipped"] += 1
                continue

            counts["matched"] += 1
            log_context = (decision.policy_id, decision.reason_code, decision.action, resource_hash(item.name))
            mutation = self._mutation_coordinator.execute_inspection(
                item.name,
                expected_epochs[item.name],
                lambda decision=decision, callback=evaluation.refresh_callback: (
                    self._executor.execute(decision, refresh_callback=callback)
                ),
            )
            if mutation is None:
                counts["skipped"] += 1
                LOGGER.info(
                    "event=inspection_action provider=codex policy_id=%s reason_code=fast_scan_superseded action=%s outcome=skipped resource_hash=%s",
                    decision.policy_id,
                    decision.action,
                    resource_hash(item.name),
                )
            elif mutation.error_code is not None:
                counts["failed"] += 1
                LOGGER.warning(
                    "event=inspection_action provider=codex policy_id=%s reason_code=%s action=%s outcome=failed resource_hash=%s error_code=%s",
                    *log_context,
                    mutation.error_code,
                )
            elif mutation.applied:
                counts["applied"] += 1
                LOGGER.info(
                    "event=inspection_action provider=codex policy_id=%s reason_code=%s action=%s outcome=applied resource_hash=%s",
                    *log_context,
                )
            else:
                counts["skipped"] += 1

        report = ProviderRunReport(provider_id="codex", phase=RunPhase.INSPECTION, **counts)
        if report.failed:
            status = RunStatus.PARTIAL_FAILURE
        elif metadata:
            status = RunStatus.SUCCESS
        else:
            status = RunStatus.EMPTY
        return InspectionResult(status=status, reports=(report,))


def build_codex_inspection_service(
    cpa_api: CpaApi,
    config: InspectionConfig,
    *,
    proxy: str | None,
    max_retries: int,
    mutation_coordinator: AuthFileMutationCoordinator,
) -> CodexInspectionService:
    """Bind Codex HTTP clients to the supplied inspection runtime parameters."""
    openai_api = OpenAiApi(
        proxy=proxy,
        timeout_seconds=config.usage_timeout_seconds,
        max_retries=max_retries,
    )
    return CodexInspectionService(
        cpa_api,
        config=config,
        inspector=CodexInspector(openai_api, now_epoch_seconds=time.time),
        evaluator=CodexInspectionPolicyEvaluator(
            quota_threshold_percent=config.quota_threshold_percent,
        ),
        refresher=CodexRefresher(openai_api),
        mutation_coordinator=mutation_coordinator,
    )
