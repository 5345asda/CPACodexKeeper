"""Provider-neutral matching for configured fast-scan rules."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence

from cpa_keeper.application.mutation_coordinator import AuthFileMutationCoordinator
from cpa_keeper.application.results import FastScanResult, RunStatus
from cpa_keeper.config.fast_scan import FastScanRule, RuleCondition, RuntimeConfig
from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.domain.identifiers import resource_hash
from cpa_keeper.domain.reports import ProviderRunReport, RunPhase
from cpa_keeper.infrastructure.cpa_api import CpaApi

LOGGER = logging.getLogger(__name__)


def normalize_error(row: Mapping[str, object]) -> dict[str, str | None]:
    """Project nested or flat CPA status JSON into the rule field contract."""
    status_message = row.get("status_message")
    if isinstance(status_message, str):
        try:
            payload = json.loads(status_message)
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = status_message

    if not isinstance(payload, Mapping):
        payload = {}
    error = payload.get("error")
    fields = error if isinstance(error, Mapping) else payload
    message = fields.get("message")
    if not isinstance(message, str) and isinstance(error, str):
        message = error
    return {
        "error.type": fields.get("type") if isinstance(fields.get("type"), str) else None,
        "error.code": fields.get("code") if isinstance(fields.get("code"), str) else None,
        "error.message": message if isinstance(message, str) else None,
    }


def _matches(condition: RuleCondition, values: Mapping[str, str | None]) -> bool:
    if condition.all is not None:
        return all(_matches(child, values) for child in condition.all)
    if condition.any is not None:
        return any(_matches(child, values) for child in condition.any)

    actual = values.get(condition.field or "")
    if actual is None or condition.value is None:
        return False
    expected = condition.value
    if condition.ignore_case:
        actual, expected = actual.casefold(), expected.casefold()
    if condition.op == "eq":
        return actual == expected
    return expected in actual


def select_rule(
    rules: Sequence[FastScanRule], values: Mapping[str, str | None]
) -> FastScanRule | None:
    """Return the first matching enabled rule after priority ordering."""
    for rule in sorted((rule for rule in rules if rule.enabled), key=lambda rule: -rule.priority):
        if _matches(rule.when, values):
            return rule
    return None


def _metadata(row: Mapping[str, object], provider_id: str) -> AuthFileMetadata | None:
    name = row.get("name")
    disabled = row.get("disabled", False)
    status = row.get("status", "unknown")
    if not isinstance(name, str) or not isinstance(disabled, bool) or not isinstance(status, str):
        return None
    return AuthFileMetadata(
        name=name,
        provider_id=provider_id,
        disabled=disabled,
        status=status.lower(),
    )


class FastScanService:
    """Run all configured provider rules from one CPA auth-file list."""

    def __init__(
        self,
        cpa_api: CpaApi,
        config: RuntimeConfig,
        *,
        mutation_coordinator: AuthFileMutationCoordinator,
    ) -> None:
        self._cpa_api = cpa_api
        self._config = config
        self._mutation_coordinator = mutation_coordinator

    def _apply_rule(self, rule: FastScanRule, name: str) -> object:
        def action() -> object:
            if rule.action == "disable":
                return self._cpa_api.set_disabled(name, True)
            return self._cpa_api.delete_auth_file(name)

        return self._mutation_coordinator.execute_fast_scan(name, action)

    def scan(self) -> FastScanResult:
        """Read the list once, evaluate error rows, and execute matching actions."""
        started = time.monotonic()
        list_result = self._cpa_api.list_auth_files()
        if not list_result.ok:
            error_code = list_result.error_code or "list_request_failed"
            LOGGER.error("event=fast_scan_list outcome=failed error_code=%s", error_code)
            return FastScanResult(
                status=RunStatus.UPSTREAM_FAILURE,
                reports=(),
                error_code=error_code,
            )

        counts = {
            provider_id: {"scanned": 0, "matched": 0, "applied": 0, "skipped": 0, "failed": 0}
            for provider_id, provider in self._config.providers.items()
            if provider.enabled
        }
        metadata: list[AuthFileMetadata] = []
        handled_names: set[str] = set()
        for row in list_result.auth_files:
            provider_id = row.get("type")
            provider = self._config.providers.get(provider_id)
            if provider is None or not provider.enabled:
                continue

            item = _metadata(row, provider_id)
            if item is not None:
                metadata.append(item)
            if not provider.fast_scan.enabled or item is None or item.status != "error":
                continue

            provider_counts = counts[provider_id]
            provider_counts["scanned"] += 1
            rule = select_rule(provider.fast_scan.rules, normalize_error(row))
            if rule is None:
                continue
            provider_counts["matched"] += 1
            log_context = (provider_id, rule.id, rule.action, resource_hash(item.name))
            if rule.action == "disable" and item.disabled:
                provider_counts["skipped"] += 1
                LOGGER.info(
                    "event=fast_scan_action provider=%s rule_id=%s action=%s outcome=skipped resource_hash=%s",
                    *log_context,
                )
                continue

            operation = self._apply_rule(rule, item.name)
            if operation.ok:
                provider_counts["applied"] += 1
                handled_names.add(item.name)
                LOGGER.info(
                    "event=fast_scan_action provider=%s rule_id=%s action=%s outcome=applied resource_hash=%s",
                    *log_context,
                )
            else:
                provider_counts["failed"] += 1
                LOGGER.error(
                    "event=fast_scan_action provider=%s rule_id=%s action=%s outcome=failed resource_hash=%s error_code=%s",
                    *log_context,
                    getattr(operation, "error_code", None) or "cpa_mutation_failed",
                )

        reports = tuple(
            ProviderRunReport(provider_id=provider_id, phase=RunPhase.FAST_SCAN, **provider_counts)
            for provider_id, provider_counts in counts.items()
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        for report in reports:
            LOGGER.info(
                "event=fast_scan_summary provider=%s scanned=%s matched=%s applied=%s skipped=%s failed=%s duration_ms=%s",
                report.provider_id,
                report.scanned,
                report.matched,
                report.applied,
                report.skipped,
                report.failed,
                elapsed_ms,
            )
        if any(report.failed for report in reports):
            status = RunStatus.PARTIAL_FAILURE
        elif any(report.scanned for report in reports):
            status = RunStatus.SUCCESS
        else:
            status = RunStatus.EMPTY
        return FastScanResult(
            status=status,
            reports=reports,
            metadata=tuple(metadata),
            handled_resource_names=frozenset(handled_names),
            resource_epochs=self._mutation_coordinator.snapshot_generations(
                item.name for item in metadata
            ),
        )
