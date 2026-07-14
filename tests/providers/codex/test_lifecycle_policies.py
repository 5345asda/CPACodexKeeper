from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.providers.codex.inspector import CodexInspectionFacts, ExpiryState
from cpa_keeper.providers.codex.lifecycle_policies import CodexInspectionPolicyEvaluator


class CodexLifecyclePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = CodexInspectionPolicyEvaluator(quota_threshold_percent=100)

    def test_fixed_lifecycle_cases_emit_the_expected_action(self) -> None:
        cases = (
            ("http-401", False, 401, None, None, True, ExpiryState.SUFFICIENT, True, "delete", "codex.inspection.http-401-402.delete", "http_401"),
            ("http-402", False, 402, None, None, True, ExpiryState.SUFFICIENT, True, "delete", "codex.inspection.http-401-402.delete", "http_402"),
            ("expired", False, None, None, None, False, ExpiryState.EXPIRED, True, "delete", "codex.inspection.expired-without-refresh.delete", "expired_without_refresh"),
            ("quota-no-refresh", False, 200, 100, None, False, ExpiryState.SUFFICIENT, True, "delete", "codex.inspection.quota-without-refresh.delete", "quota_threshold_reached_without_refresh"),
            ("quota-disable", False, 200, 100, None, True, ExpiryState.SUFFICIENT, True, "disable", "codex.inspection.quota-state.reconcile", "quota_threshold_reached"),
            ("quota-enable", True, 200, 99, None, True, ExpiryState.SUFFICIENT, True, "enable", "codex.inspection.quota-state.reconcile", "quota_below_threshold"),
            ("refresh", True, 200, 100, None, True, ExpiryState.BELOW_REFRESH_THRESHOLD, True, "refresh_then_upload", "codex.inspection.disabled-expiring.refresh", "disabled_near_expiry"),
        )
        for (
            name,
            disabled,
            http_status,
            primary_usage,
            secondary_usage,
            has_refresh,
            expiry,
            refresh_enabled,
            action,
            policy_id,
            reason,
        ) in cases:
            with self.subTest(case=name):
                facts = CodexInspectionFacts(
                    metadata=AuthFileMetadata(
                        name=f"{name}.json",
                        provider_id="codex",
                        disabled=disabled,
                        status="active",
                    ),
                    http_status=http_status,
                    primary_usage_percent=primary_usage,
                    secondary_usage_percent=secondary_usage,
                    has_refresh_material=has_refresh,
                    expiry_state=expiry,
                    refresh_enabled=refresh_enabled,
                )

                decision = self.evaluator.evaluate(facts)

                self.assertIsNotNone(decision)
                assert decision is not None
                self.assertEqual((decision.action.value, decision.policy_id, decision.reason_code), (action, policy_id, reason))

    def test_disabled_near_expiry_refreshes_when_quota_is_below_threshold(self) -> None:
        facts = CodexInspectionFacts(
            metadata=AuthFileMetadata(
                name="refresh.json",
                provider_id="codex",
                disabled=True,
                status="active",
            ),
            http_status=200,
            primary_usage_percent=99,
            secondary_usage_percent=None,
            has_refresh_material=True,
            expiry_state=ExpiryState.BELOW_REFRESH_THRESHOLD,
            refresh_enabled=True,
        )

        decision = self.evaluator.evaluate(facts)

        self.assertIsNotNone(decision)
        assert decision is not None

        self.assertEqual(
            (decision.action.value, decision.policy_id, decision.reason_code),
            ("refresh_then_upload", "codex.inspection.disabled-expiring.refresh", "disabled_near_expiry"),
        )

    def test_unknown_usage_does_not_change_credential_state(self) -> None:
        facts = CodexInspectionFacts(
            metadata=AuthFileMetadata(
                name="unknown.json",
                provider_id="codex",
                disabled=False,
                status="active",
            ),
            http_status=None,
            primary_usage_percent=None,
            secondary_usage_percent=None,
            has_refresh_material=True,
            expiry_state=ExpiryState.UNKNOWN,
            refresh_enabled=True,
        )

        self.assertIsNone(self.evaluator.evaluate(facts))


if __name__ == "__main__":
    unittest.main()
