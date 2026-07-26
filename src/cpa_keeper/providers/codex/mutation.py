"""CPA mutations for fixed Codex lifecycle decisions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .lifecycle_policies import LifecycleAction, LifecycleDecision
from .refresher import RefreshUpload


class CpaMutationApi(Protocol):
    """CPA write methods used by Codex lifecycle actions."""

    def delete_auth_file(self, name: str) -> object: ...

    def set_disabled(self, name: str, disabled: bool) -> object: ...

    def upload_auth_file(self, name: str, payload: Mapping[str, object]) -> object: ...


@dataclass(frozen=True, slots=True)
class MutationResult:
    """Safe CPA mutation outcome."""

    applied: bool
    error_code: str | None = None


RefreshCallback = Callable[[LifecycleDecision], RefreshUpload]


def _ok(result: object) -> bool:
    return result is True or getattr(result, "ok", False) is True


class CodexMutationExecutor:
    """Execute the one lifecycle action selected for a Codex auth file."""

    def __init__(self, cpa_api: CpaMutationApi) -> None:
        self._cpa_api = cpa_api

    def execute(
        self,
        decision: LifecycleDecision,
        *,
        refresh_callback: RefreshCallback | None = None,
    ) -> MutationResult:
        """Write the selected action and return a safe error code on failure."""
        name = decision.resource_name
        if decision.action is LifecycleAction.REFRESH_THEN_UPLOAD:
            return self._refresh_then_upload(decision, refresh_callback)
        if decision.action is LifecycleAction.DELETE:
            operation = self._cpa_api.delete_auth_file(name)
        else:
            operation = self._cpa_api.set_disabled(name, decision.action is LifecycleAction.DISABLE)
        if _ok(operation):
            return MutationResult(applied=True)
        return MutationResult(applied=False, error_code="cpa_mutation_failed")

    def _refresh_then_upload(
        self,
        decision: LifecycleDecision,
        refresh_callback: RefreshCallback | None,
    ) -> MutationResult:
        if refresh_callback is None:
            return MutationResult(applied=False, error_code="refresh_callback_required")
        try:
            refreshed = refresh_callback(decision)
        except Exception:
            return MutationResult(applied=False, error_code="refresh_failed")
        if not _ok(self._cpa_api.upload_auth_file(decision.resource_name, refreshed.payload)):
            return MutationResult(applied=False, error_code="cpa_upload_failed")
        if refreshed.restore_disabled and not _ok(
            self._cpa_api.set_disabled(decision.resource_name, True)
        ):
            return MutationResult(applied=False, error_code="cpa_restore_disabled_failed")
        return MutationResult(applied=True)
