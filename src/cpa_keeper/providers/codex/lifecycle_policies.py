"""Fixed Codex lifecycle decisions derived from inspection facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .inspector import CodexInspectionFacts, ExpiryState


class LifecycleAction(StrEnum):
    """CPA mutations emitted by the Codex lifecycle."""

    DELETE = "delete"
    DISABLE = "disable"
    ENABLE = "enable"
    REFRESH_THEN_UPLOAD = "refresh_then_upload"


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    """One fixed action for a Codex auth file."""

    resource_name: str
    policy_id: str
    action: LifecycleAction
    reason_code: str


@dataclass(frozen=True, slots=True)
class CodexInspectionPolicyEvaluator:
    """Select one fixed lifecycle action from secret-free inspection facts."""

    quota_threshold_percent: int = 100

    def evaluate(self, facts: CodexInspectionFacts) -> LifecycleDecision | None:
        """Return the single fixed lifecycle action for one Codex credential."""

        def decision(policy_id: str, action: LifecycleAction, reason_code: str) -> LifecycleDecision:
            return LifecycleDecision(
                resource_name=facts.metadata.name,
                policy_id=policy_id,
                action=action,
                reason_code=reason_code,
            )

        if facts.http_status in {401, 402}:
            return decision(
                "codex.inspection.http-401-402.delete",
                LifecycleAction.DELETE,
                f"http_{facts.http_status}",
            )

        if facts.expiry_state is ExpiryState.EXPIRED and not facts.has_refresh_material:
            return decision(
                "codex.inspection.expired-without-refresh.delete",
                LifecycleAction.DELETE,
                "expired_without_refresh",
            )

        if facts.primary_usage_percent is None:
            return None
        threshold = self.quota_threshold_percent
        quota_reached = facts.primary_usage_percent >= threshold or (
            facts.secondary_usage_percent is not None
            and facts.secondary_usage_percent >= threshold
        )

        if quota_reached and not facts.has_refresh_material:
            return decision(
                "codex.inspection.quota-without-refresh.delete",
                LifecycleAction.DELETE,
                "quota_threshold_reached_without_refresh",
            )

        if (
            facts.metadata.disabled
            and facts.has_refresh_material
            and facts.refresh_enabled
            and facts.expiry_state is ExpiryState.BELOW_REFRESH_THRESHOLD
        ):
            return decision(
                "codex.inspection.disabled-expiring.refresh",
                LifecycleAction.REFRESH_THEN_UPLOAD,
                "disabled_near_expiry",
            )

        if not quota_reached and facts.metadata.disabled:
            return decision(
                "codex.inspection.quota-state.reconcile",
                LifecycleAction.ENABLE,
                "quota_below_threshold",
            )

        if quota_reached and not facts.metadata.disabled:
            return decision(
                "codex.inspection.quota-state.reconcile",
                LifecycleAction.DISABLE,
                "quota_threshold_reached",
            )
        return None
