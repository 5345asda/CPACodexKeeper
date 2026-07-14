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

    def __post_init__(self) -> None:
        if (
            type(self.quota_threshold_percent) is not int
            or self.quota_threshold_percent < 0
            or self.quota_threshold_percent > 100
        ):
            raise ValueError("quota_threshold_percent must be between 0 and 100")

    @staticmethod
    def _decision(
        facts: CodexInspectionFacts,
        *,
        policy_id: str,
        action: LifecycleAction,
        reason_code: str,
    ) -> LifecycleDecision:
        return LifecycleDecision(
            resource_name=facts.metadata.name,
            policy_id=policy_id,
            action=action,
            reason_code=reason_code,
        )

    def evaluate(self, facts: CodexInspectionFacts) -> LifecycleDecision | None:
        """Return the single fixed lifecycle action for one Codex credential."""
        if facts.http_status in {401, 402}:
            return self._decision(
                facts,
                policy_id="codex.inspection.http-401-402.delete",
                action=LifecycleAction.DELETE,
                reason_code=f"http_{facts.http_status}",
            )

        if facts.expiry_state is ExpiryState.EXPIRED and not facts.has_refresh_material:
            return self._decision(
                facts,
                policy_id="codex.inspection.expired-without-refresh.delete",
                action=LifecycleAction.DELETE,
                reason_code="expired_without_refresh",
            )

        if facts.primary_usage_percent is None:
            return None
        secondary_reached = (
            facts.secondary_usage_percent is not None
            and facts.secondary_usage_percent >= self.quota_threshold_percent
        )
        quota_reached = (
            facts.primary_usage_percent >= self.quota_threshold_percent or secondary_reached
        )
        quota_below_threshold = (
            facts.primary_usage_percent < self.quota_threshold_percent
            and (
                facts.secondary_usage_percent is None
                or facts.secondary_usage_percent < self.quota_threshold_percent
            )
        )

        if quota_reached and not facts.has_refresh_material:
            return self._decision(
                facts,
                policy_id="codex.inspection.quota-without-refresh.delete",
                action=LifecycleAction.DELETE,
                reason_code="quota_threshold_reached_without_refresh",
            )

        if (
            facts.metadata.disabled
            and facts.has_refresh_material
            and facts.refresh_enabled
            and facts.expiry_state is ExpiryState.BELOW_REFRESH_THRESHOLD
        ):
            return self._decision(
                facts,
                policy_id="codex.inspection.disabled-expiring.refresh",
                action=LifecycleAction.REFRESH_THEN_UPLOAD,
                reason_code="disabled_near_expiry",
            )

        if quota_below_threshold and facts.metadata.disabled:
            return self._decision(
                facts,
                policy_id="codex.inspection.quota-state.reconcile",
                action=LifecycleAction.ENABLE,
                reason_code="quota_below_threshold",
            )

        if quota_reached and not facts.metadata.disabled:
            return self._decision(
                facts,
                policy_id="codex.inspection.quota-state.reconcile",
                action=LifecycleAction.DISABLE,
                reason_code="quota_threshold_reached",
            )
        return None
