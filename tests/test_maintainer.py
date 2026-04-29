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
        report = self.maintainer.reports.get("t2")
        self.assertIsNotNone(report)
        self.assertTrue(report.disabled)
        self.assertEqual(report.last_outcome, "disabled")

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
        report = self.maintainer.reports.get("t3")
        self.assertIsNotNone(report)
        self.assertFalse(report.disabled)
        self.assertEqual(report.last_outcome, "enabled")

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
        report = self.maintainer.reports.get("t4")
        self.assertIsNotNone(report)
        self.assertTrue(report.disabled)
        self.assertEqual(report.last_outcome, "refreshed")

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

        def submit_side_effect(fn, token_info, idx, total, **kwargs):
            future = Future()
            future.set_result(fn(token_info, idx, total, **kwargs))
            futures.append(future)
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)
        self.maintainer.process_token = Mock(side_effect=["alive", "alive", "alive"])

        self.maintainer.run()

        executor_cls.assert_called_once_with(max_workers=6)
        self.assertEqual(executor.submit.call_count, 3)
        self.maintainer.process_token.assert_any_call({"name": "t1"}, 1, 3, snap=ANY)
        self.maintainer.process_token.assert_any_call({"name": "t2"}, 2, 3, snap=ANY)
        self.maintainer.process_token.assert_any_call({"name": "t3"}, 3, 3, snap=ANY)

    @patch("src.maintainer.random.shuffle", side_effect=lambda seq: None)
    @patch("src.maintainer.as_completed")
    @patch("src.maintainer.ThreadPoolExecutor")
    def test_run_passes_same_settings_snapshot_to_all_workers(self, executor_cls, as_completed_mock, _shuffle_mock):
        tokens = [{"name": "t1"}, {"name": "t2"}]
        self.maintainer.get_token_list = Mock(return_value=tokens)
        self.maintainer.log_startup = Mock()
        seen_snapshots = []

        def submit_side_effect(fn, token_info, idx, total, **kwargs):
            future = Future()
            seen_snapshots.append(kwargs["snap"])
            future.set_result(fn(token_info, idx, total, **kwargs))
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)
        self.maintainer.process_token = Mock(return_value="alive")

        self.maintainer.run()

        self.assertEqual(len(seen_snapshots), 2)
        self.assertIs(seen_snapshots[0], seen_snapshots[1])

    @patch("src.maintainer.random.shuffle", side_effect=lambda seq: None)
    @patch("src.maintainer.as_completed")
    @patch("src.maintainer.ThreadPoolExecutor")
    def test_run_logs_task_exception_and_continues(self, executor_cls, as_completed_mock, _shuffle_mock):
        tokens = [{"name": "ok-1"}, {"name": "boom"}, {"name": "ok-2"}]
        self.maintainer.get_token_list = Mock(return_value=tokens)
        self.maintainer.log_startup = Mock()
        self.maintainer.log = Mock()

        futures = []

        def submit_side_effect(fn, token_info, idx, total, **kwargs):
            future = Future()
            try:
                future.set_result(fn(token_info, idx, total, **kwargs))
            except Exception as exc:
                future.set_exception(exc)
            futures.append(future)
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)

        def process_side_effect(token_info, idx, total, **kwargs):
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

        def submit_side_effect(fn, token_info, idx, total, **kwargs):
            future = Future()
            future.set_result(fn(token_info, idx, total, **kwargs))
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)

        def process_side_effect(token_info, idx, total, **kwargs):
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

        def submit_side_effect(fn, token_info, idx, total, **kwargs):
            future = Future()
            future.set_result(fn(token_info, idx, total, **kwargs))
            return future

        executor = executor_cls.return_value.__enter__.return_value
        executor.submit.side_effect = submit_side_effect
        as_completed_mock.side_effect = lambda items: list(items)
        self.maintainer.process_token = Mock(return_value="alive")

        self.maintainer.run()

        self.maintainer.log.assert_any_call("INFO", "线程数: 5")
