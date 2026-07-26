from __future__ import annotations

import unittest

from cpa_keeper.domain.auth_files import AuthFileMetadata
from cpa_keeper.providers.codex.inspector import CodexInspector, ExpiryState


class FakeUsageApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def check_usage(self, access_token: str, account_id: str | None) -> object:
        self.calls.append((access_token, account_id))
        return type(
            "UsageResponse",
            (),
            {
                "status_code": 200,
                "payload": {
                    "rate_limit": {
                        "primary_window": {"used_percent": 100},
                        "secondary_window": {"used_percent": 25},
                    }
                },
            },
        )()


class CodexInspectorContractTests(unittest.TestCase):
    def test_expired_detail_without_refresh_is_classified_before_usage_request(self) -> None:
        usage_api = FakeUsageApi()
        inspector = CodexInspector(usage_api, now_epoch_seconds=lambda: 100)
        metadata = AuthFileMetadata(
            name="redacted-expired",
            provider_id="codex",
            disabled=False,
            status="active",
        )

        facts = inspector.inspect(
            metadata,
            {
                "disabled": False,
                "access_token": "opaque-access",
                "expired": "1970-01-01T00:00:01Z",
            },
            refresh_before_expiry_days=3,
            refresh_enabled=True,
        )

        self.assertEqual(facts.expiry_state, ExpiryState.EXPIRED)
        self.assertFalse(facts.has_refresh_material)
        self.assertEqual(usage_api.calls, [])

    def test_usage_response_becomes_safe_numeric_facts_without_retaining_tokens(self) -> None:
        usage_api = FakeUsageApi()
        inspector = CodexInspector(usage_api, now_epoch_seconds=lambda: 100)
        metadata = AuthFileMetadata(
            name="redacted-usage",
            provider_id="codex",
            disabled=False,
            status="active",
        )

        facts = inspector.inspect(
            metadata,
            {
                "disabled": False,
                "access_token": "opaque-access",
                "account_id": "opaque-account",
                "refresh_token": "opaque-refresh",
                "expired": "2099-01-01T00:00:00Z",
            },
            refresh_before_expiry_days=3,
            refresh_enabled=True,
        )

        self.assertEqual(facts.primary_usage_percent, 100)
        self.assertEqual(facts.secondary_usage_percent, 25)
        self.assertEqual(usage_api.calls, [("opaque-access", "opaque-account")])
        self.assertNotIn("opaque-access", repr(facts))
        self.assertNotIn("opaque-refresh", repr(facts))

