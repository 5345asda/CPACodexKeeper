"""CPA mutations for fixed Codex lifecycle decisions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from .lifecycle_policies import LifecycleAction, LifecycleDecision
from .refresher import RefreshUpload


class CpaMutationApi(Protocol):
    """CPA write methods used by Codex lifecycle actions."""

    def delete_auth_file(self, name: str) -> object:
        """Delete one CPA auth file."""

    def set_disabled(self, name: str, disabled: bool) -> object:
        """Set one CPA auth-file disabled flag."""

    def upload_auth_file(self, name: str, payload: Mapping[str, object]) -> object:
        """Upload one replacement CPA auth-file payload."""


@dataclass(frozen=True, slots=True)
class MutationResult:
    """Safe CPA mutation outcome."""

    applied: bool
    error_code: str | None = None


RefreshCallback = Callable[[LifecycleDecision], RefreshUpload]


class CodexMutationExecutor:
    """Execute the one lifecycle action selected for a Codex auth file."""

    def __init__(self, cpa_api: CpaMutationApi) -> None:
        self._cpa_api = cpa_api

    @staticmethod
    def _ok(result: object) -> bool:
        return result if type(result) is bool else getattr(result, "ok", False) is True

    @staticmethod
    def _failed(error_code: str) -> MutationResult:
        return MutationResult(applied=False, error_code=error_code)

    def execute(
        self,
        decision: LifecycleDecision,
        *,
        refresh_callback: RefreshCallback | None = None,
    ) -> MutationResult:
        """Write the selected action and return a safe error code on failure."""
        if decision.action is LifecycleAction.DELETE:
            operation = self._cpa_api.delete_auth_file(decision.resource_name)
        elif decision.action is LifecycleAction.DISABLE:
            operation = self._cpa_api.set_disabled(decision.resource_name, True)
        elif decision.action is LifecycleAction.ENABLE:
            operation = self._cpa_api.set_disabled(decision.resource_name, False)
        else:
            return self._refresh_then_upload(decision, refresh_callback)

        return MutationResult(applied=True) if self._ok(operation) else self._failed("cpa_mutation_failed")

    def _refresh_then_upload(
        self,
        decision: LifecycleDecision,
        refresh_callback: RefreshCallback | None,
    ) -> MutationResult:
        if refresh_callback is None:
            return self._failed("refresh_callback_required")
        try:
            refreshed = refresh_callback(decision)
        except Exception:
            return self._failed("refresh_failed")
        upload = self._cpa_api.upload_auth_file(decision.resource_name, refreshed.payload)
        if not self._ok(upload):
            return self._failed("cpa_upload_failed")
        if refreshed.restore_disabled and not self._ok(
            self._cpa_api.set_disabled(decision.resource_name, True)
        ):
            return self._failed("cpa_restore_disabled_failed")
        return MutationResult(applied=True)
