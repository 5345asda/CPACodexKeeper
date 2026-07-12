import pathlib
import sys
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.maintainer import CPACodexKeeper
from src.openai_client import parse_usage_info
from src.settings import Settings


class MaintainerTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
            quota_threshold=100,
            expiry_threshold_days=3,
        )
        self.maintainer = CPACodexKeeper(settings=self.settings, dry_run=True)

    def test_filter_tokens_keeps_only_codex_type(self):
        tokens = [
            {"name": "a", "type": "codex"},
            {"name": "b", "type": "oauth"},
            {"name": "c", "type": "codex"},
            {"name": "d"},
        ]
        filtered = self.maintainer.filter_tokens(tokens)
        self.assertEqual([token["name"] for token in filtered], ["a", "c"])

    def test_parse_error_type_from_status_message_json(self):
        token = {
            "status": "error",
            "status_message": '{"error":{"type":"usage_limit_reached","code":"rate_limit","message":"The usage limit has been reached"}}',
        }

        self.assertEqual(self.maintainer.get_list_error_type(token), "usage_limit_reached")
        self.assertEqual(self.maintainer.get_list_error_code(token), "rate_limit")

    def test_parse_error_type_from_flat_status_message_json(self):
        token = {
            "status": "error",
            "status_message": '{"type":"usage_limit_reached","message":"The usage limit has been reached"}',
        }

        self.assertEqual(self.maintainer.get_list_error_type(token), "usage_limit_reached")

    def test_parse_error_type_returns_none_for_bad_message(self):
        token = {"status": "error", "status_message": "not json"}

        self.assertIsNone(self.maintainer.get_list_error_type(token))

    def test_get_list_error_message_returns_flat_xai_error_string(self):
        token = {
            "status": "error",
            "status_message": (
                '{"code":"permission-denied","error":"Access to the chat endpoint is denied. '
                'Please ensure you are using the correct credentials."}'
            ),
        }

        self.assertEqual(
            self.maintainer.get_list_error_message(token),
            "Access to the chat endpoint is denied. Please ensure you are using the correct credentials.",
        )

    def test_get_list_error_message_keeps_nested_error_message(self):
        token = {
            "status": "error",
            "status_message": '{"error":{"message":"Nested error message","code":"auth_unavailable"}}',
        }

        self.assertEqual(self.maintainer.get_list_error_message(token), "Nested error message")

    def test_should_delete_xai_chat_permission_denied_when_enabled(self):
        self.settings.xai_permission_denied_delete_enabled = True
        token = {
            "type": "xai",
            "status": "error",
            "status_message": (
                '{"code":"permission-denied","error":"Access to the chat endpoint is denied. '
                'Please ensure you are using the correct credentials."}'
            ),
        }

        self.assertTrue(self.maintainer.should_delete_for_list_error(token))

    def test_should_not_delete_xai_chat_permission_denied_when_conditions_are_incomplete(self):
        matching_status_message = (
            '{"code":"permission-denied","error":"Access to the chat endpoint is denied. '
            'Please ensure you are using the correct credentials."}'
        )
        matching_token = {
            "type": "xai",
            "status": "error",
            "status_message": matching_status_message,
        }
        cases = (
            ("feature disabled", False, matching_token),
            ("non-xai type", True, {**matching_token, "type": "vertex"}),
            (
                "wrong code",
                True,
                {
                    **matching_token,
                    "status_message": matching_status_message.replace(
                        "permission-denied", "permission-denied-other"
                    ),
                },
            ),
            (
                "missing chat endpoint phrase",
                True,
                {
                    **matching_token,
                    "status_message": '{"code":"permission-denied","error":"Access is denied."}',
                },
            ),
            ("active status", True, {**matching_token, "status": "active"}),
            ("malformed status message", True, {**matching_token, "status_message": "not json"}),
            (
                "non-string flat error",
                True,
                {
                    **matching_token,
                    "status_message": (
                        '{"code":"permission-denied","error":["Access to the chat endpoint is denied."]}'
                    ),
                },
            ),
        )

        for label, enabled, token in cases:
            with self.subTest(label=label):
                self.settings.xai_permission_denied_delete_enabled = enabled

                self.assertFalse(self.maintainer.should_delete_for_list_error(token))

    def test_should_delete_for_list_error_keeps_legacy_codex_invalidated_matching(self):
        token = {
            "type": "codex",
            "status": "error",
            "status_message": (
                '{"error":{"message":"Your authentication token has been invalidated.",'
                '"type":"authentication_error","code":"auth_unavailable"}}'
            ),
        }

        self.assertTrue(self.maintainer.should_delete_for_list_error(token))

    def test_sweep_disables_usage_limit_error_tokens(self):
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[
            {
                "name": "codex-a.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"usage_limit_reached"}}',
            },
            {
                "name": "codex-b.json",
                "type": "codex",
                "disabled": False,
                "status": "active",
                "status_message": "",
            },
        ])
        self.maintainer.set_disabled_status = Mock(return_value=True)
        self.maintainer.log = Mock()

        result = self.maintainer.sweep_error_status_once()

        self.assertEqual(result, {
            "scanned": 2,
            "delete_matched": 0,
            "disable_matched": 1,
            "deleted": 0,
            "disabled": 1,
            "failed": 0,
            "skipped_busy": 0,
        })
        self.maintainer.set_disabled_status.assert_called_once_with("codex-a.json", disabled=True)

    def test_sweep_skips_disabled_or_unconfigured_error_tokens(self):
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[
            {
                "name": "codex-disabled.json",
                "type": "codex",
                "disabled": True,
                "status": "error",
                "status_message": '{"error":{"type":"usage_limit_reached"}}',
            },
            {
                "name": "codex-auth.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"authentication_error"}}',
            },
            {
                "name": "xai-error.json",
                "type": "xai",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"usage_limit_reached"}}',
            },
        ])
        self.maintainer.set_disabled_status = Mock(return_value=True)
        self.maintainer.log = Mock()

        result = self.maintainer.sweep_error_status_once()

        self.assertEqual(result, {
            "scanned": 2,
            "delete_matched": 0,
            "disable_matched": 0,
            "deleted": 0,
            "disabled": 0,
            "failed": 0,
            "skipped_busy": 0,
        })
        self.maintainer.set_disabled_status.assert_not_called()

    def test_sweep_deletes_auth_unavailable_error_tokens(self):
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[
            {
                "name": "codex-invalidated.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": (
                    '{"error":{"message":"Your authentication token has been invalidated. '
                    'Please try signing in again.","type":"authentication_error","code":"auth_unavailable"}}'
                ),
            },
            {
                "name": "codex-disabled-invalidated.json",
                "type": "codex",
                "disabled": True,
                "status": "error",
                "status_message": (
                    '{"error":{"message":"Your authentication token has been invalidated.",'
                    '"type":"authentication_error","code":"auth_unavailable"}}'
                ),
            },
        ])
        self.maintainer.delete_token = Mock(return_value=True)
        self.maintainer.set_disabled_status = Mock(return_value=True)
        self.maintainer.log = Mock()

        result = self.maintainer.sweep_error_status_once()

        self.assertEqual(result, {
            "scanned": 2,
            "delete_matched": 2,
            "disable_matched": 0,
            "deleted": 2,
            "disabled": 0,
            "failed": 0,
            "skipped_busy": 0,
        })
        self.maintainer.delete_token.assert_any_call("codex-invalidated.json")
        self.maintainer.delete_token.assert_any_call("codex-disabled-invalidated.json")
        self.maintainer.set_disabled_status.assert_not_called()

    def test_sweep_does_not_delete_auth_unavailable_without_invalidated_message(self):
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[
            {
                "name": "codex-pool-unavailable.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"authentication_error","code":"auth_unavailable","message":"no auth available"}}',
            },
            {
                "name": "codex-wrong-type.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"rate_limit_error","code":"auth_unavailable","message":"invalidated token"}}',
            },
        ])
        self.maintainer.delete_token = Mock(return_value=True)
        self.maintainer.set_disabled_status = Mock(return_value=True)
        self.maintainer.log = Mock()

        result = self.maintainer.sweep_error_status_once()

        self.assertEqual(result, {
            "scanned": 2,
            "delete_matched": 0,
            "disable_matched": 0,
            "deleted": 0,
            "disabled": 0,
            "failed": 0,
            "skipped_busy": 0,
        })
        self.maintainer.delete_token.assert_not_called()
        self.maintainer.set_disabled_status.assert_not_called()

    def test_sweep_runs_while_full_maintenance_round_is_running(self):
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[
            {
                "name": "codex-a.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"usage_limit_reached"}}',
            },
        ])
        self.maintainer.set_disabled_status = Mock(return_value=True)
        self.maintainer.log = Mock()

        self.assertTrue(self.maintainer._maintenance_lock.acquire(blocking=False))
        self.addCleanup(self.maintainer._maintenance_lock.release)

        result = self.maintainer.sweep_error_status_once()

        self.assertEqual(result, {
            "scanned": 1,
            "delete_matched": 0,
            "disable_matched": 1,
            "deleted": 0,
            "disabled": 1,
            "failed": 0,
            "skipped_busy": 0,
        })
        self.maintainer.set_disabled_status.assert_called_once_with("codex-a.json", disabled=True)

    def test_sweep_skips_reserved_token_without_mutating(self):
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[
            {
                "name": "codex-busy.json",
                "type": "codex",
                "disabled": False,
                "status": "error",
                "status_message": '{"error":{"type":"usage_limit_reached"}}',
            },
        ])
        self.assertTrue(self.maintainer._reserve_token_name("codex-busy.json"))
        self.addCleanup(self.maintainer._release_token_name, "codex-busy.json")
        self.maintainer.set_disabled_status = Mock(return_value=True)
        self.maintainer.log = Mock()

        result = self.maintainer.sweep_error_status_once()

        self.assertEqual(result, {
            "scanned": 1,
            "delete_matched": 0,
            "disable_matched": 1,
            "deleted": 0,
            "disabled": 0,
            "failed": 0,
            "skipped_busy": 1,
        })
        self.maintainer.set_disabled_status.assert_not_called()

    def test_process_token_skips_token_mutated_by_sweep(self):
        self.maintainer._mark_sweep_mutated("codex-swept.json")
        self.maintainer.get_token_detail = Mock(side_effect=AssertionError("should not download detail"))
        self.maintainer.check_token_live = Mock(side_effect=AssertionError("should not check usage"))

        result = self.maintainer.process_token({
            "name": "codex-swept.json",
            "type": "codex",
            "disabled": False,
            "status": "active",
            "status_message": "",
        }, 1, 1)

        self.assertEqual(result, "skipped")
        self.assertEqual(self.maintainer.stats.skipped, 1)

    def test_process_token_disables_list_usage_error_without_detail_download(self):
        self.maintainer.get_token_detail = Mock(side_effect=AssertionError("should not download detail"))
        self.maintainer.check_token_live = Mock(side_effect=AssertionError("should not check usage"))
        self.maintainer.set_disabled_status = Mock(return_value=True)

        result = self.maintainer.process_token({
            "name": "codex-error.json",
            "type": "codex",
            "disabled": False,
            "status": "error",
            "status_message": '{"error":{"type":"usage_limit_reached"}}',
        }, 1, 1)

        self.assertEqual(result, "disabled")
        self.maintainer.set_disabled_status.assert_called_once_with("codex-error.json", disabled=True, logger=ANY)
        self.assertEqual(self.maintainer.stats.disabled, 1)

    def test_process_token_deletes_list_auth_unavailable_without_detail_download(self):
        self.maintainer.get_token_detail = Mock(side_effect=AssertionError("should not download detail"))
        self.maintainer.check_token_live = Mock(side_effect=AssertionError("should not check usage"))
        self.maintainer.delete_token = Mock(return_value=True)
        self.maintainer.set_disabled_status = Mock(return_value=True)

        result = self.maintainer.process_token({
            "name": "codex-invalidated.json",
            "type": "codex",
            "disabled": False,
            "status": "error",
            "status_message": (
                '{"error":{"message":"Your authentication token has been invalidated. '
                'Please try signing in again.","type":"authentication_error","code":"auth_unavailable"}}'
            ),
        }, 1, 1)

        self.assertEqual(result, "dead")
        self.maintainer.delete_token.assert_called_once_with("codex-invalidated.json", logger=ANY)
        self.maintainer.set_disabled_status.assert_not_called()
        self.assertEqual(self.maintainer.stats.dead, 1)

    def test_process_token_does_not_delete_auth_unavailable_without_invalidated_message(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "free",
                "rate_limit": {
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                    "secondary_window": None,
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.delete_token = Mock(return_value=True)

        result = self.maintainer.process_token({
            "name": "codex-pool-unavailable.json",
            "type": "codex",
            "disabled": False,
            "status": "error",
            "status_message": '{"error":{"type":"authentication_error","code":"auth_unavailable","message":"no auth available"}}',
        }, 1, 1)

        self.assertEqual(result, "alive")
        self.maintainer.delete_token.assert_not_called()

    def test_parse_usage_info_reads_team_primary_and_secondary_windows(self):
        usage = parse_usage_info({
            "plan_type": "team",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 15,
                    "limit_window_seconds": 18000,
                    "reset_at": 1,
                },
                "secondary_window": {
                    "used_percent": 80,
                    "limit_window_seconds": 604800,
                    "reset_at": 2,
                },
            },
            "credits": {"has_credits": False, "balance": None},
        })
        self.assertEqual(usage.plan_type, "team")
        self.assertEqual(usage.primary_used_percent, 15)
        self.assertEqual(usage.secondary_used_percent, 80)
        self.assertEqual(usage.quota_check_percent, 80)
        self.assertEqual(usage.quota_check_label, "Week")

    def test_parse_usage_info_falls_back_to_primary_when_secondary_missing(self):
        usage = parse_usage_info({
            "plan_type": "free",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 30,
                    "limit_window_seconds": 604800,
                },
                "secondary_window": None,
            },
        })
        self.assertEqual(usage.secondary_used_percent, None)
        self.assertEqual(usage.quota_check_percent, 30)
        self.assertEqual(usage.quota_check_label, "Week")

    def test_process_token_deletes_invalid_token_on_401(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(401, {"brief": "unauthorized"}))
        result = self.maintainer.process_token({"name": "t1"}, 1, 1)
        self.assertEqual(result, "dead")
        self.assertEqual(self.maintainer.stats.dead, 1)

    def test_process_token_deletes_invalid_token_on_402(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(402, {"brief": "deactivated_workspace"}))
        result = self.maintainer.process_token({"name": "t402"}, 1, 1)
        self.assertEqual(result, "dead")
        self.assertEqual(self.maintainer.stats.dead, 1)

    def test_process_token_disables_when_weekly_quota_reaches_threshold(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 10, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 100, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.set_disabled_status = Mock(return_value=True)
        result = self.maintainer.process_token({"name": "t2"}, 1, 1)
        self.assertEqual(result, "alive")
        self.maintainer.set_disabled_status.assert_called_once()
        args, kwargs = self.maintainer.set_disabled_status.call_args
        self.assertEqual(args, ("t2",))
        self.assertEqual(kwargs["disabled"], True)
        self.assertEqual(self.maintainer.stats.disabled, 1)

    def test_process_token_disables_when_primary_quota_reaches_threshold_even_if_weekly_is_below(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 100, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 28, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.set_disabled_status = Mock(return_value=True)

        result = self.maintainer.process_token({"name": "t2-primary"}, 1, 1)

        self.assertEqual(result, "alive")
        self.maintainer.set_disabled_status.assert_called_once()
        args, kwargs = self.maintainer.set_disabled_status.call_args
        self.assertEqual(args, ("t2-primary",))
        self.assertEqual(kwargs["disabled"], True)
        self.assertEqual(self.maintainer.stats.disabled, 1)

    def test_process_token_enables_when_disabled_and_weekly_quota_below_threshold(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": True,
            "access_token": "token",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 90, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.set_disabled_status = Mock(return_value=True)
        result = self.maintainer.process_token({"name": "t3"}, 1, 1)
        self.assertEqual(result, "alive")
        self.maintainer.set_disabled_status.assert_called_once()
        args, kwargs = self.maintainer.set_disabled_status.call_args
        self.assertEqual(args, ("t3",))
        self.assertEqual(kwargs["disabled"], False)
        self.assertEqual(self.maintainer.stats.enabled, 1)

    def test_process_token_keeps_disabled_when_primary_quota_still_reaches_threshold(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": True,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 100, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 95, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.set_disabled_status = Mock(return_value=True)

        result = self.maintainer.process_token({"name": "t3-still-disabled"}, 1, 1)

        self.assertEqual(result, "alive")
        self.maintainer.set_disabled_status.assert_not_called()
        self.assertEqual(self.maintainer.stats.enabled, 0)

    def test_process_token_refreshes_disabled_token_when_near_expiry(self):
        self.maintainer.settings.enable_refresh = True
        near_expiry = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": True,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": near_expiry,
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 100, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 95, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.try_refresh = Mock(return_value=(True, {
            "access_token": "new-token",
            "refresh_token": "new-rt",
            "expired": "2099-03-01T00:00:00Z",
        }, "刷新成功"))
        self.maintainer.upload_updated_token = Mock(return_value=True)
        self.maintainer.set_disabled_status = Mock(return_value=True)
        result = self.maintainer.process_token({"name": "t4"}, 1, 1)
        self.assertEqual(result, "alive")
        self.maintainer.upload_updated_token.assert_called_once()
        self.maintainer.set_disabled_status.assert_called_once_with("t4", disabled=True, logger=ANY)
        self.assertEqual(self.maintainer.stats.refreshed, 1)

    def test_process_token_logs_week_label_when_primary_window_is_weekly(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "free",
                "rate_limit": {
                    "primary_window": {"used_percent": 100, "limit_window_seconds": 604800},
                    "secondary_window": None,
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.set_disabled_status = Mock(return_value=True)
        captured_lines = []
        self.maintainer.logger.emit_lines = Mock(side_effect=lambda lines: captured_lines.append(list(lines)))

        result = self.maintainer.process_token({"name": "t-week-primary"}, 1, 1)

        self.assertEqual(result, "alive")
        self.assertTrue(captured_lines)
        emitted = "\n".join(captured_lines[0])
        self.assertIn("Week: 100%", emitted)
        self.assertIn("Week额度 100% >= 100%，准备禁用", emitted)
        self.assertNotIn("5h: 100%", emitted)

    def test_process_token_does_not_refresh_when_refresh_disabled(self):
        settings = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
            quota_threshold=100,
            expiry_threshold_days=3,
            enable_refresh=False,
        )
        maintainer = CPACodexKeeper(settings=settings, dry_run=True)
        near_expiry = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": near_expiry,
        })
        maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        maintainer.try_refresh = Mock(return_value=(True, {
            "access_token": "new-token",
            "refresh_token": "new-rt",
            "expired": "2099-03-01T00:00:00Z",
        }, "刷新成功"))
        maintainer.set_disabled_status = Mock(return_value=True)
        maintainer.upload_updated_token = Mock(return_value=True)

        result = maintainer.process_token({"name": "t4-disabled"}, 1, 1)

        self.assertEqual(result, "alive")
        maintainer.try_refresh.assert_not_called()
        maintainer.upload_updated_token.assert_not_called()
        self.assertEqual(maintainer.stats.refreshed, 0)

    def test_process_token_does_not_refresh_enabled_token_even_when_refresh_enabled(self):
        settings = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
            quota_threshold=100,
            expiry_threshold_days=3,
            enable_refresh=True,
        )
        maintainer = CPACodexKeeper(settings=settings, dry_run=True)
        near_expiry = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": near_expiry,
        })
        maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        maintainer.try_refresh = Mock(return_value=(True, {
            "access_token": "new-token",
            "refresh_token": "new-rt",
            "expired": "2099-03-01T00:00:00Z",
        }, "刷新成功"))
        maintainer.set_disabled_status = Mock(return_value=True)
        maintainer.upload_updated_token = Mock(return_value=True)

        result = maintainer.process_token({"name": "t4-enabled"}, 1, 1)

        self.assertEqual(result, "alive")
        maintainer.try_refresh.assert_not_called()
        maintainer.upload_updated_token.assert_not_called()
        self.assertEqual(maintainer.stats.refreshed, 0)

    def test_process_token_does_not_refresh_token_reenabled_by_quota_policy(self):
        settings = Settings(
            cpa_endpoint="https://example.com",
            cpa_token="secret",
            quota_threshold=100,
            expiry_threshold_days=3,
            enable_refresh=True,
        )
        maintainer = CPACodexKeeper(settings=settings, dry_run=True)
        near_expiry = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": True,
            "access_token": "token",
            "refresh_token": "rt",
            "account_id": "acc",
            "expired": near_expiry,
        })
        maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "team",
                "rate_limit": {
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                },
                "credits": {"has_credits": False},
            }
        }))
        maintainer.try_refresh = Mock(return_value=(True, {
            "access_token": "new-token",
            "refresh_token": "new-rt",
            "expired": "2099-03-01T00:00:00Z",
        }, "刷新成功"))
        maintainer.set_disabled_status = Mock(return_value=True)
        maintainer.upload_updated_token = Mock(return_value=True)

        result = maintainer.process_token({"name": "t4-enabled-disabled"}, 1, 1)

        self.assertEqual(result, "alive")
        maintainer.set_disabled_status.assert_called_once()
        args, kwargs = maintainer.set_disabled_status.call_args
        self.assertEqual(args, ("t4-enabled-disabled",))
        self.assertEqual(kwargs["disabled"], False)
        maintainer.try_refresh.assert_not_called()
        maintainer.upload_updated_token.assert_not_called()
        self.assertEqual(maintainer.stats.refreshed, 0)

    def test_process_token_deletes_expired_token_without_refresh_token(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "",
            "account_id": "acc",
            "expired": "2000-01-01T00:00:00Z",
        })
        self.maintainer.delete_token = Mock(return_value=True)
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "free",
                "rate_limit": {
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                    "secondary_window": None,
                },
                "credits": {"has_credits": False},
            }
        }))

        result = self.maintainer.process_token({"name": "t-expired"}, 1, 1)

        self.assertEqual(result, "dead")
        self.assertEqual(self.maintainer.stats.dead, 1)
        self.maintainer.check_token_live.assert_not_called()
        args, kwargs = self.maintainer.delete_token.call_args
        self.assertEqual(args, ("t-expired",))
        self.assertIn("logger", kwargs)

    def test_process_token_deletes_quota_exhausted_token_without_refresh_token(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "token",
            "refresh_token": "",
            "account_id": "acc",
            "expired": "2099-01-01T00:00:00Z",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "free",
                "rate_limit": {
                    "primary_window": {"used_percent": 100, "limit_window_seconds": 604800},
                    "secondary_window": None,
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.delete_token = Mock(return_value=True)
        self.maintainer.set_disabled_status = Mock(return_value=True)

        result = self.maintainer.process_token({"name": "t-no-rt"}, 1, 1)

        self.assertEqual(result, "dead")
        self.assertEqual(self.maintainer.stats.dead, 1)
        self.maintainer.set_disabled_status.assert_not_called()
        args, kwargs = self.maintainer.delete_token.call_args
        self.assertEqual(args, ("t-no-rt",))
        self.assertIn("logger", kwargs)

    def test_process_token_keeps_non_refreshable_token_when_expiry_is_unknown(self):
        self.maintainer.get_token_detail = Mock(return_value={
            "email": "a@example.com",
            "disabled": False,
            "access_token": "not-a-jwt",
            "refresh_token": "",
            "account_id": "acc",
            "expired": "",
        })
        self.maintainer.check_token_live = Mock(return_value=(200, {
            "json": {
                "plan_type": "free",
                "rate_limit": {
                    "primary_window": {"used_percent": 0, "limit_window_seconds": 604800},
                    "secondary_window": None,
                },
                "credits": {"has_credits": False},
            }
        }))
        self.maintainer.delete_token = Mock(return_value=True)

        result = self.maintainer.process_token({"name": "t-unknown-expiry"}, 1, 1)

        self.assertEqual(result, "alive")
        self.assertEqual(self.maintainer.stats.alive, 1)
        self.maintainer.delete_token.assert_not_called()
        self.maintainer.check_token_live.assert_called_once()

    @patch("src.maintainer.random.shuffle", side_effect=lambda seq: None)
    @patch("src.maintainer.as_completed")
    @patch("src.maintainer.ThreadPoolExecutor")
    def test_run_uses_configured_worker_threads_and_processes_all_tokens(self, executor_cls, as_completed_mock, _shuffle_mock):
        tokens = [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}]
        self.maintainer.settings.worker_threads = 6
        self.maintainer.get_token_list = Mock(return_value=tokens)
        self.maintainer.log_startup = Mock()

        futures = []

        def submit_side_effect(fn, token_info, idx, total):
            future = Future()
            future.set_result(fn(token_info, idx, total))
            futures.append(future)
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)
        self.maintainer.process_token = Mock(side_effect=["alive", "alive", "alive"])

        self.maintainer.run()

        executor_cls.assert_called_once_with(max_workers=6)
        self.assertEqual(executor.submit.call_count, 3)
        self.maintainer.process_token.assert_any_call({"name": "t1"}, 1, 3)
        self.maintainer.process_token.assert_any_call({"name": "t2"}, 2, 3)
        self.maintainer.process_token.assert_any_call({"name": "t3"}, 3, 3)

    @patch("src.maintainer.random.shuffle", side_effect=lambda seq: None)
    @patch("src.maintainer.as_completed")
    @patch("src.maintainer.ThreadPoolExecutor")
    def test_run_logs_task_exception_and_continues(self, executor_cls, as_completed_mock, _shuffle_mock):
        tokens = [{"name": "ok-1"}, {"name": "boom"}, {"name": "ok-2"}]
        self.maintainer.get_token_list = Mock(return_value=tokens)
        self.maintainer.log_startup = Mock()
        self.maintainer.log = Mock()

        futures = []

        def submit_side_effect(fn, token_info, idx, total):
            future = Future()
            try:
                future.set_result(fn(token_info, idx, total))
            except Exception as exc:
                future.set_exception(exc)
            futures.append(future)
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)

        def process_side_effect(token_info, idx, total):
            if token_info["name"] == "boom":
                raise RuntimeError("unexpected boom")
            self.maintainer.stats.alive += 1
            return "alive"

        self.maintainer.process_token = Mock(side_effect=process_side_effect)

        self.maintainer.run()

        self.assertEqual(self.maintainer.process_token.call_count, 3)
        self.assertEqual(self.maintainer.stats.alive, 2)
        self.maintainer.log.assert_any_call("ERROR", "Token 任务异常 (boom): unexpected boom", indent=1)

    @patch("src.maintainer.random.shuffle", side_effect=lambda seq: None)
    @patch("src.maintainer.as_completed")
    @patch("src.maintainer.ThreadPoolExecutor")
    def test_run_preserves_total_stat_with_threaded_execution(self, executor_cls, as_completed_mock, _shuffle_mock):
        tokens = [{"name": "t1"}, {"name": "t2"}]
        self.maintainer.get_token_list = Mock(return_value=tokens)
        self.maintainer.log_startup = Mock()

        def submit_side_effect(fn, token_info, idx, total):
            future = Future()
            future.set_result(fn(token_info, idx, total))
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)

        def process_side_effect(token_info, idx, total):
            if token_info["name"] == "t1":
                self.maintainer.stats.alive += 1
            else:
                self.maintainer.stats.skipped += 1
            return token_info["name"]

        self.maintainer.process_token = Mock(side_effect=process_side_effect)

        self.maintainer.run()

        self.assertEqual(self.maintainer.stats.total, 2)
        self.assertEqual(self.maintainer.stats.alive, 1)
        self.assertEqual(self.maintainer.stats.skipped, 1)

    @patch("src.maintainer.random.shuffle", side_effect=lambda seq: None)
    @patch("src.maintainer.as_completed")
    @patch("src.maintainer.ThreadPoolExecutor")
    def test_run_logs_configured_worker_threads(self, executor_cls, as_completed_mock, _shuffle_mock):
        tokens = [{"name": "t1"}]
        self.maintainer.settings.worker_threads = 5
        self.maintainer.get_token_list = Mock(return_value=tokens)
        self.maintainer.log_startup = Mock()
        self.maintainer.log = Mock()

        def submit_side_effect(fn, token_info, idx, total):
            future = Future()
            future.set_result(fn(token_info, idx, total))
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)
        self.maintainer.process_token = Mock(return_value="alive")

        self.maintainer.run()

        self.maintainer.log.assert_any_call("INFO", "线程数: 5")

    def test_run_forever_starts_error_sweep_thread_before_full_loop(self):
        self.maintainer.start_error_sweep_thread = Mock()
        self.maintainer.run = Mock(side_effect=KeyboardInterrupt)

        with self.assertRaises(KeyboardInterrupt):
            self.maintainer.run_forever(interval_seconds=1)

        self.maintainer.start_error_sweep_thread.assert_called_once()
