import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cpa_client import CPAClient
from .logging_utils import ConsoleLogger, TokenLogger
from .models import MaintainerStats, format_window_label
from .openai_client import OpenAIClient, parse_usage_info
from .settings import Settings
from .utils import format_seconds, get_expired_remaining, get_expired_remaining_with_status


class PriorityCoordinator:
    PRIORITY_VALUE = {
        "full": 1,
        "log": 2,
        "timer": 3,
    }

    def __init__(self):
        self._condition = threading.Condition()
        self._pending = {name: 0 for name in self.PRIORITY_VALUE}
        self._active = {name: 0 for name in self.PRIORITY_VALUE}

    def _blocking_priority_locked(self, priority):
        value = self.PRIORITY_VALUE[priority]
        blocking = [
            name
            for name in self.PRIORITY_VALUE
            if self._pending[name] > 0 and self.PRIORITY_VALUE[name] > value
        ]
        if not blocking:
            return None
        return max(blocking, key=self.PRIORITY_VALUE.get)

    def request(self, priority):
        with self._condition:
            self._pending[priority] += 1
            self._condition.notify_all()

    def blocking_priority(self, priority):
        with self._condition:
            return self._blocking_priority_locked(priority)

    def has_pending(self, priority):
        with self._condition:
            return self._pending[priority] > 0

    def has_active(self, priority):
        with self._condition:
            return self._active[priority] > 0

    def has_lower_work(self, priority):
        with self._condition:
            value = self.PRIORITY_VALUE[priority]
            return any(
                self.PRIORITY_VALUE[name] < value and (self._pending[name] > 0 or self._active[name] > 0)
                for name in self.PRIORITY_VALUE
            )

    def can_start(self, priority):
        with self._condition:
            return self._blocking_priority_locked(priority) is None

    def acquire_next(self, priority):
        with self._condition:
            while self._blocking_priority_locked(priority) is not None:
                self._condition.wait()
            self._pending[priority] -= 1
            self._active[priority] += 1

    def release(self, priority):
        with self._condition:
            self._active[priority] -= 1
            self._condition.notify_all()


class CPACodexKeeper:
    PAUSE_MESSAGES = {
        ("full", "log"): "优先级协调：日志巡检正在等待，主巡检将在当前 Token 完成后暂停",
        ("full", "timer"): "优先级协调：定时复查正在等待，主巡检将在当前 Token 完成后暂停",
        ("log", "timer"): "优先级协调：定时复查正在等待，日志巡检将在当前 Token 完成后暂停",
    }
    def __init__(self, settings: Settings, dry_run: bool = False, coordinator: PriorityCoordinator | None = None):
        self.settings = settings
        self.dry_run = dry_run
        self.coordinator = coordinator or PriorityCoordinator()
        self.logger = ConsoleLogger()
        self.cpa_client = CPAClient(
            settings.cpa_endpoint,
            settings.cpa_token,
            proxy=settings.proxy,
            timeout=settings.cpa_timeout_seconds,
            max_retries=settings.max_retries,
        )
        self.openai_client = OpenAIClient(
            proxy=settings.proxy,
            timeout=settings.usage_timeout_seconds,
            max_retries=settings.max_retries,
        )
        self.stats = MaintainerStats()
        self._stats_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.disabled_accounts_path = Path(__file__).resolve().parents[1] / "disabled_accounts.json"
        self._tracked_disabled_accounts = self._load_disabled_accounts_state()
        self._tracked_recheck_timers: dict[str, threading.Timer] = {}
        self._tracked_rechecks_started = False
        self.last_usage_query_time: int | None = None

    def reset_stats(self):
        with self._stats_lock:
            self.stats = MaintainerStats()

    def blank_line(self):
        self.logger.blank_line()

    def _inc_stat(self, field_name, amount=1):
        with self._stats_lock:
            setattr(self.stats, field_name, getattr(self.stats, field_name) + amount)

    def _set_total(self, total):
        with self._stats_lock:
            self.stats.total = total

    def _stats_snapshot(self):
        with self._stats_lock:
            return self.stats.as_dict()

    def log(self, level, message, indent=0):
        self.logger.log(level, message, indent=indent)

    def log_token_header(self, idx, total, name):
        self.logger.token_header(idx, total, name)

    def filter_tokens(self, tokens):
        return [token for token in tokens if token.get("type") == "codex"]

    def get_token_list(self):
        return self.filter_tokens(self.cpa_client.list_auth_files())

    def get_token_detail(self, name):
        return self.cpa_client.get_auth_file(name)

    def delete_token(self, name, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"演练模式：将删除账号文件 {name}", indent=1)
            return True
        return self.cpa_client.delete_auth_file(name)

    def set_disabled_status(self, name, disabled=True, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"演练模式：将{'禁用' if disabled else '启用'}账号 {name}", indent=1)
            return True
        return self.cpa_client.set_disabled(name, disabled)

    def check_token_live(self, access_token, account_id=None):
        if not access_token:
            return None, "missing access_token"
        result = self.openai_client.check_usage(access_token, account_id)
        if result.status_code is None:
            return None, result.error or "request failed"
        return result.status_code, {
            "status_code": result.status_code,
            "body": result.body,
            "brief": result.brief or result.error or "",
            "json": result.json_data,
        }

    def parse_usage_info(self, resp_data):
        usage = parse_usage_info(resp_data)
        return {
            "plan_type": usage.plan_type,
            "primary_used_percent": usage.primary_used_percent,
            "primary_window_seconds": usage.primary_window.limit_window_seconds,
            "primary_reset_at": usage.primary_window.reset_at,
            "secondary_used_percent": usage.secondary_used_percent,
            "secondary_window_seconds": None if usage.secondary_window is None else usage.secondary_window.limit_window_seconds,
            "secondary_reset_at": None if usage.secondary_window is None else usage.secondary_window.reset_at,
            "used_percent": usage.primary_used_percent,
            "has_credits": usage.has_credits,
        }

    def get_usage_log(self):
        return self.cpa_client.get_usage_log(lookback_seconds=self.settings.usage_query_interval_seconds)

    def _load_disabled_accounts_state(self):
        if not self.disabled_accounts_path.exists():
            return {}
        try:
            data = json.loads(self.disabled_accounts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log("ERROR", f"加载禁用账号计划失败：{exc}")
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _save_disabled_accounts_state(self):
        payload = json.dumps(self._tracked_disabled_accounts, ensure_ascii=False, indent=2, sort_keys=True)
        self.disabled_accounts_path.write_text(payload + "\n", encoding="utf-8")

    def _cancel_tracked_recheck_timer(self, name):
        timer = self._tracked_recheck_timers.pop(name, None)
        if timer is not None:
            timer.cancel()

    def _schedule_tracked_recheck(self, name, ts):
        self._cancel_tracked_recheck_timer(name)
        delay_seconds = max(0, int(ts) - int(time.time()))
        delay_seconds = min(delay_seconds, int(threading.TIMEOUT_MAX))
        timer = threading.Timer(delay_seconds, self._run_tracked_recheck, args=(name,))
        timer.daemon = True
        self._tracked_recheck_timers[name] = timer
        timer.start()

    def _start_tracked_rechecks(self):
        with self._state_lock:
            if self._tracked_rechecks_started:
                return
            self._tracked_disabled_accounts = self._load_disabled_accounts_state()
            self._tracked_rechecks_started = True
            tracked_entries = list(self._tracked_disabled_accounts.items())
        for name, entry in tracked_entries:
            if not isinstance(entry, dict):
                continue
            next_check_at = entry.get("next_check_at")
            if isinstance(next_check_at, int):
                self._schedule_tracked_recheck(name, next_check_at)

    def _get_tracked_next_check_at(self, name):
        entry = self._tracked_disabled_accounts.get(name)
        if not isinstance(entry, dict):
            return None
        value = entry.get("next_check_at")
        return value if isinstance(value, int) else None

    def _format_tracked_next_check_at(self, ts):
        try:
            tz = timezone(timedelta(hours=8))
            return datetime.fromtimestamp(int(ts), tz=tz).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError, OverflowError):
            return str(ts)

    def _acquire_priority(self, priority):
        self.coordinator.request(priority)
        blocking = getattr(self.coordinator, "blocking_priority", lambda _priority: None)(priority)
        pause_message = self.PAUSE_MESSAGES.get((priority, blocking))
        if pause_message:
            self.log("INFO", pause_message)
        self.coordinator.acquire_next(priority)
        if priority == "timer":
            self.log("INFO", "定时复查已取得最高优先级，开始处理到期账号")

    def _release_priority(self, priority):
        self.coordinator.release(priority)
        has_pending = getattr(self.coordinator, "has_pending", None)
        has_active = getattr(self.coordinator, "has_active", None)
        has_lower_work = getattr(self.coordinator, "has_lower_work", None)
        if (
            priority == "timer"
            and (has_pending is None or not has_pending(priority))
            and (has_active is None or not has_active(priority))
            and has_lower_work is not None
            and has_lower_work(priority)
        ):
            self.log("INFO", "定时复查队列已清空，较低优先级任务可以继续执行")

    def _set_tracked_next_check_at(self, name, ts):
        ts_int = int(ts)
        with self._state_lock:
            self._tracked_disabled_accounts[name] = {"next_check_at": ts_int}
            self._save_disabled_accounts_state()
        self._schedule_tracked_recheck(name, ts_int)

    def _remove_tracked_account(self, name):
        with self._state_lock:
            if name in self._tracked_disabled_accounts:
                self._tracked_disabled_accounts.pop(name)
                self._save_disabled_accounts_state()
        self._cancel_tracked_recheck_timer(name)

    def _run_tracked_recheck(self, name):
        with self._state_lock:
            if name not in self._tracked_disabled_accounts:
                self._tracked_recheck_timers.pop(name, None)
                return
        self._tracked_recheck_timers.pop(name, None)
        self._acquire_priority("timer")
        try:
            self.logger.emit_lines([
                f"{self.logger.PREFIX_MAP['INFO']} 账号 {name} 到达计划复查时间，开始复查使用额度"
            ])
            self.process_token({"name": name}, 1, 1)
        except Exception as exc:
            self.log("ERROR", f"账号 {name} 定时复查异常: {exc}")
        finally:
            self._release_priority("timer")

    def _collect_threshold_reaching_reset_ats(self, body_info):
        reached_reset_ats = []
        primary_pct = body_info.get("primary_used_percent", 0)
        if primary_pct >= self.settings.quota_threshold:
            primary_reset_at = body_info.get("primary_reset_at")
            if isinstance(primary_reset_at, int):
                reached_reset_ats.append(primary_reset_at)
        secondary_pct = body_info.get("secondary_used_percent")
        if secondary_pct is not None and secondary_pct >= self.settings.quota_threshold:
            secondary_reset_at = body_info.get("secondary_reset_at")
            if isinstance(secondary_reset_at, int):
                reached_reset_ats.append(secondary_reset_at)
        return reached_reset_ats

    def _collect_threshold_reaching_window_seconds(self, body_info):
        window_seconds = []
        primary_pct = body_info.get("primary_used_percent", 0)
        primary_window_seconds = body_info.get("primary_window_seconds")
        if primary_pct >= self.settings.quota_threshold and isinstance(primary_window_seconds, int):
            window_seconds.append(primary_window_seconds)
        secondary_pct = body_info.get("secondary_used_percent")
        secondary_window_seconds = body_info.get("secondary_window_seconds")
        if secondary_pct is not None and secondary_pct >= self.settings.quota_threshold and isinstance(secondary_window_seconds, int):
            window_seconds.append(secondary_window_seconds)
        return window_seconds

    def _extract_usage_detail_entries(self, usage_data):
        entries = []
        try:
            apis = usage_data.get("usage", {}).get("apis", {})
            for api_data in apis.values():
                models = api_data.get("models", {})
                for model_data in models.values():
                    details = model_data.get("details", [])
                    for detail in details:
                        source = detail.get("source")
                        timestamp = detail.get("timestamp")
                        if source and timestamp:
                            entries.append((source, timestamp))
        except AttributeError:
            return []
        return entries

    def _parse_usage_detail_timestamp(self, value):
        if not value:
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dot_index = normalized.find(".")
        if dot_index != -1:
            tz_plus = normalized.rfind("+")
            tz_minus = normalized.rfind("-")
            tz_index = max(tz_plus, tz_minus)
            if tz_index > dot_index:
                fraction = normalized[dot_index + 1:tz_index]
                if len(fraction) > 6:
                    normalized = normalized[:dot_index + 1] + fraction[:6] + normalized[tz_index:]
        try:
            return int(datetime.fromisoformat(normalized).timestamp())
        except ValueError:
            return None

    def _latest_usage_timestamp_for_token(self, usage_data, token_detail):
        email = ((token_detail or {}).get("email") or "").strip().lower()
        if not email or not usage_data:
            return None
        latest_timestamp = None
        for source, raw_timestamp in self._extract_usage_detail_entries(usage_data):
            if (source or "").strip().lower() != email:
                continue
            parsed_timestamp = self._parse_usage_detail_timestamp(raw_timestamp)
            if parsed_timestamp is None:
                continue
            if latest_timestamp is None or parsed_timestamp > latest_timestamp:
                latest_timestamp = parsed_timestamp
        return latest_timestamp

    def _compute_next_check_at_from_usage(self, body_info, now, fallback_seconds, *, usage_data=None, token_detail=None):
        reached_reset_ats = self._collect_threshold_reaching_reset_ats(body_info)
        if reached_reset_ats:
            return max(reached_reset_ats)
        if usage_data is None and token_detail is not None:
            usage_data = self.get_usage_log()
        latest_usage_timestamp = self._latest_usage_timestamp_for_token(usage_data, token_detail)
        if latest_usage_timestamp is not None:
            window_seconds = self._collect_threshold_reaching_window_seconds(body_info)
            if window_seconds:
                return max(latest_usage_timestamp + seconds for seconds in window_seconds)
        return now + fallback_seconds

    def _latest_usage_timestamp_by_email(self, usage_data, *, after_timestamp=None):
        latest_by_email = {}
        for source, raw_timestamp in self._extract_usage_detail_entries(usage_data or {}):
            email = (source or "").strip().lower()
            parsed_timestamp = self._parse_usage_detail_timestamp(raw_timestamp)
            if not email or parsed_timestamp is None:
                continue
            if after_timestamp is not None and parsed_timestamp <= after_timestamp:
                continue
            current = latest_by_email.get(email)
            if current is None or parsed_timestamp > current:
                latest_by_email[email] = parsed_timestamp
        return latest_by_email

    def get_fill_token_map(self):
        email_map = {}
        for token in self.get_token_list():
            email = (token.get("email") or "").strip().lower()
            enriched_token = dict(token)
            if not email:
                detail = self.get_token_detail(token.get("name", ""))
                email = ((detail or {}).get("email") or "").strip().lower()
                if email:
                    enriched_token["email"] = email
            if email and email not in email_map:
                email_map[email] = enriched_token
        return email_map

    def _fill_quota_reached_summary(self, primary_pct, secondary_pct, primary_label, secondary_label):
        primary_reached = primary_pct >= self.settings.quota_threshold
        secondary_reached = secondary_pct is not None and secondary_pct >= self.settings.quota_threshold
        reached_parts = []
        if primary_reached:
            reached_parts.append(f"{primary_label}额度 {primary_pct}%")
        if secondary_reached:
            reached_parts.append(f"{secondary_label}额度 {secondary_pct}%")
        return "、".join(reached_parts), primary_reached or secondary_reached

    def process_fill_token(self, token_info, idx, total):
        name = token_info.get("name", "unknown")
        logger = TokenLogger(self.logger, idx, total, name)
        try:
            token_detail = self.get_token_detail(name)
            if not token_detail:
                return self._skip_token("获取账号详情失败", logger)

            disabled, _, _, _ = self._log_token_details(token_detail, logger)
            if disabled:
                logger.log("INFO", "当前账号已禁用，日志巡查跳过额度检查", indent=1)
                self._inc_stat("alive")
                logger.blank_line()
                return "alive"

            access_token = token_detail.get("access_token")
            account_id = token_detail.get("account_id")
            if not access_token:
                return self._skip_token("缺少 access_token", logger)

            status, resp_data = self.check_token_live(access_token, account_id)
            if status in (401, 402):
                return self._skip_token(f"在线状态异常 ({status})", logger)
            if status is None:
                detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
                msg = "在线状态检查失败"
                if detail:
                    msg += f" | {detail}"
                return self._skip_token(msg, logger, network_error=True)
            if status != 200:
                return self._handle_non_200_status(status, resp_data, logger)

            body_info = self.parse_usage_info(resp_data)
            primary_pct, secondary_pct, primary_label, secondary_label = self._log_usage_summary(body_info, logger)
            reached_summary, quota_reached = self._fill_quota_reached_summary(
                primary_pct,
                secondary_pct,
                primary_label,
                secondary_label,
            )
            if quota_reached:
                logger.log(
                    "WARN",
                    f"额度命中禁用阈值：{reached_summary} >= {self.settings.quota_threshold}%，准备禁用账号",
                    indent=1,
                )
                if self.set_disabled_status(name, disabled=True, logger=logger):
                    logger.log("DISABLE", "账号已禁用", indent=1)
                    self._inc_stat("disabled")
                    logger.blank_line()
                    return "disabled"
                return self._skip_token("禁用账号失败", logger)

            self._inc_stat("alive")
            logger.blank_line()
            return "alive"
        finally:
            logger.flush()

    def try_refresh(self, token_data):
        rt = token_data.get("refresh_token")
        if not rt:
            return False, None, "缺少 Refresh Token"
        result = self.openai_client.refresh_token(rt)
        if result.status_code != 200 or not result.json_data:
            return False, None, f"刷新被拒({result.status_code})" if result.status_code else (result.error or "刷新失败")
        new_tokens = result.json_data
        expires_in = new_tokens.get("expires_in", 864000)
        new_data = dict(token_data)
        new_data.update({
            "access_token": new_tokens["access_token"],
            "refresh_token": new_tokens.get("refresh_token", rt),
            "id_token": new_tokens.get("id_token"),
            "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + expires_in)),
        })
        return True, new_data, f"刷新成功，新有效期: {format_seconds(expires_in)}"

    def upload_updated_token(self, name, token_data, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"演练模式：将上传更新后的账号数据 {name}", indent=1)
            return True
        return self.cpa_client.upload_auth_file(name, token_data)

    def _skip_token(self, message, logger, *, network_error=False):
        logger.log("WARN" if network_error else "SKIP", message, indent=1)
        if network_error:
            self._inc_stat("network_error")
            logger.blank_line()
            return "network_error"
        self._inc_stat("skipped")
        logger.blank_line()
        return "skipped"

    def _log_token_details(self, token_detail, logger):
        email = token_detail.get("email", "unknown")
        disabled = token_detail.get("disabled", False)
        expired_str, remaining_seconds, expiry_known = get_expired_remaining_with_status(token_detail)
        remaining_str = format_seconds(remaining_seconds) if expiry_known else "未知"

        logger.log("INFO", "步骤: 获取账号详情", indent=1)
        logger.log("INFO", f"账号邮箱: {email}", indent=1)
        logger.log("INFO", f"当前状态: {'已禁用' if disabled else '正常'}", indent=1)
        logger.log("INFO", f"过期时间: {expired_str or '未知'}", indent=1)
        logger.log("INFO", f"剩余有效期: {remaining_str}", indent=1)
        return disabled, remaining_seconds, remaining_str, expiry_known

    def _has_refresh_token(self, token_detail):
        return bool((token_detail.get("refresh_token") or "").strip())

    def _delete_token_with_reason(self, name, reason, logger):
        logger.log("WARN", reason, indent=1)
        if self.delete_token(name, logger=logger):
            self._remove_tracked_account(name)
            logger.log("DELETE", "账号文件已删除", indent=1)
            self._inc_stat("dead")
            logger.blank_line()
            return "dead"
        return self._skip_token("删除失败", logger)

    def _handle_invalid_token(self, name, logger):
        return self._delete_token_with_reason(name, "Token 无效或 workspace 已停用，准备删除", logger)

    def _apply_non_refreshable_expiry_policy(self, name, token_detail, remaining_seconds, expiry_known, logger):
        if self._has_refresh_token(token_detail) or not expiry_known or remaining_seconds > 0:
            return None
        return self._delete_token_with_reason(name, "Token 已过期且无 Refresh Token，准备删除", logger)

    def _handle_non_200_status(self, status, resp_data, logger):
        detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
        msg = f"状态异常 ({status})"
        if detail:
            msg += f" | {detail}"
        return self._skip_token(msg, logger)

    def _log_usage_summary(self, body_info, logger):
        plan = body_info.get("plan_type", "unknown")
        primary_pct = body_info.get("primary_used_percent", 0)
        primary_seconds = body_info.get("primary_window_seconds")
        secondary_pct = body_info.get("secondary_used_percent")
        secondary_seconds = body_info.get("secondary_window_seconds")
        credits = body_info.get("has_credits", False)
        primary_label = format_window_label(primary_seconds, "primary_window")
        secondary_label = format_window_label(secondary_seconds, "secondary_window") if secondary_pct is not None else None

        quota_info = f"{primary_label}: {primary_pct}%"
        if secondary_pct is not None:
            quota_info += f" | {secondary_label}: {secondary_pct}%"
        quota_info += f" | Credits: {credits}"
        logger.log("INFO", "步骤: 检查在线状态与额度", indent=1)
        logger.log("OK", f"在线状态: 正常 | 套餐: {plan} | {quota_info}", indent=1)
        return primary_pct, secondary_pct, primary_label, secondary_label

    def _apply_quota_policy(
        self,
        name,
        disabled,
        primary_pct,
        secondary_pct,
        logger,
        *,
        has_refresh_token=True,
        primary_label="primary_window",
        secondary_label="secondary_window",
        body_info=None,
        token_detail=None,
        now=None,
    ):
        primary_reached = primary_pct >= self.settings.quota_threshold
        secondary_present = secondary_pct is not None
        secondary_reached = secondary_present and secondary_pct >= self.settings.quota_threshold
        effective_disabled = disabled
        tracked_next_check_at = self._get_tracked_next_check_at(name)

        if secondary_present:
            below_threshold = primary_pct < self.settings.quota_threshold and secondary_pct < self.settings.quota_threshold
            reached_parts = []
            if primary_reached:
                reached_parts.append(f"{primary_label}额度 {primary_pct}%")
            if secondary_reached:
                reached_parts.append(f"{secondary_label}额度 {secondary_pct}%")
            reached_summary = "、".join(reached_parts)
        else:
            below_threshold = primary_pct < self.settings.quota_threshold
            reached_summary = f"{primary_label}额度 {primary_pct}%"

        if disabled:
            if below_threshold:
                if tracked_next_check_at is None:
                    logger.log("INFO", "已禁用且未被 keeper 纳入自动复查，保持禁用", indent=1)
                    return None, effective_disabled
                if secondary_present:
                    logger.log(
                        "WARN",
                        f"已禁用且 {primary_label}/{secondary_label} 额度均已低于 {self.settings.quota_threshold}%，准备启用",
                        indent=1,
                    )
                else:
                    logger.log(
                        "WARN",
                        f"已禁用但{primary_label}额度已降至 {primary_pct}% < {self.settings.quota_threshold}%，准备启用",
                        indent=1,
                    )
                if self.set_disabled_status(name, disabled=False, logger=logger):
                    self._remove_tracked_account(name)
                    logger.log("ENABLE", "账号已重新启用", indent=1)
                    self._inc_stat("enabled")
                    effective_disabled = False
                else:
                    logger.log("ERROR", "启用失败", indent=1)
                return None, effective_disabled
            if not has_refresh_token and (primary_reached or secondary_reached):
                return self._delete_token_with_reason(
                    name,
                    f"无 Refresh Token，且{reached_summary} >= {self.settings.quota_threshold}%，准备删除",
                    logger,
                ), effective_disabled
            if tracked_next_check_at is not None and body_info is not None and now is not None:
                next_check_at = self._compute_next_check_at_from_usage(
                    body_info,
                    now,
                    self.settings.interval_seconds,
                    token_detail=token_detail,
                )
                self._set_tracked_next_check_at(name, next_check_at)
                logger.log(
                    "INFO",
                    f"已禁用，{reached_summary} >= {self.settings.quota_threshold}%，保持禁用并重排到 {self._format_tracked_next_check_at(next_check_at)}",
                    indent=1,
                )
                return None, effective_disabled
            logger.log(
                "INFO",
                f"已禁用，{reached_summary} >= {self.settings.quota_threshold}%，保持禁用",
                indent=1,
            )
            return None, effective_disabled

        if primary_reached or secondary_reached:
            if not has_refresh_token:
                return self._delete_token_with_reason(
                    name,
                    f"无 Refresh Token，且{reached_summary} >= {self.settings.quota_threshold}%，准备删除",
                    logger,
                ), effective_disabled
            logger.log(
                "WARN",
                f"{reached_summary} >= {self.settings.quota_threshold}%，准备禁用",
                indent=1,
            )
            if self.set_disabled_status(name, disabled=True, logger=logger):
                effective_disabled = True
                next_check_at = self._compute_next_check_at_from_usage(
                    body_info or {},
                    now if now is not None else int(time.time()),
                    self.settings.quota_reset_none_recheck_seconds,
                    token_detail=token_detail,
                )
                self._set_tracked_next_check_at(name, next_check_at)
                logger.log("DISABLE", "账号已禁用", indent=1)
                logger.log("INFO", f"已记录下次检查额度时间: {self._format_tracked_next_check_at(next_check_at)}", indent=1)
                self._inc_stat("disabled")
            else:
                logger.log("ERROR", "禁用失败", indent=1)
            return None, effective_disabled

        return None, effective_disabled

    def _apply_refresh_policy(self, name, token_detail, remaining_seconds, remaining_str, logger, *, disabled):
        expiry_threshold_seconds = self.settings.expiry_threshold_days * 86400
        if remaining_seconds > 0 and remaining_seconds < expiry_threshold_seconds:
            if not self.settings.enable_refresh:
                logger.log(
                    "INFO",
                    f"剩余 {remaining_str} < {self.settings.expiry_threshold_days} 天，但刷新功能已关闭",
                    indent=1,
                )
                return
            if not disabled:
                logger.log(
                    "INFO",
                    f"剩余 {remaining_str} < {self.settings.expiry_threshold_days} 天，但当前为启用状态，交给 CPA 自动刷新",
                    indent=1,
                )
                return
            logger.log("WARN", f"剩余 {remaining_str} < {self.settings.expiry_threshold_days} 天，准备刷新", indent=1)
            success, new_data, msg = self.try_refresh(token_detail)
            if success:
                if self.upload_updated_token(name, new_data, logger=logger):
                    if disabled:
                        if self.set_disabled_status(name, disabled=True, logger=logger):
                            logger.log("DISABLE", "刷新后继续保持禁用状态", indent=1)
                        else:
                            logger.log("ERROR", "刷新后回写禁用失败", indent=1)
                    _, new_remaining = get_expired_remaining(new_data)
                    logger.log("REFRESH", f"账号刷新成功，新剩余有效期: {format_seconds(new_remaining)}", indent=1)
                    self._inc_stat("refreshed")
                else:
                    logger.log("ERROR", "刷新成功但上传失败", indent=1)
            else:
                logger.log("ERROR", f"刷新失败: {msg}", indent=1)
        elif remaining_seconds > 0:
            logger.log("INFO", f"过期时间充足 ({remaining_str})", indent=1)

    def process_token(self, token_info, idx, total):
        name = token_info.get("name", "unknown")
        logger = TokenLogger(self.logger, idx, total, name)
        try:
            token_detail = self.get_token_detail(name)
            if not token_detail:
                return self._skip_token("获取账号详情失败", logger)

            disabled, remaining_seconds, remaining_str, expiry_known = self._log_token_details(token_detail, logger)
            tracked_next_check_at = self._get_tracked_next_check_at(name)
            now = int(time.time())
            cleanup_result = self._apply_non_refreshable_expiry_policy(name, token_detail, remaining_seconds, expiry_known, logger)
            if cleanup_result:
                return cleanup_result
            access_token = token_detail.get("access_token")
            account_id = token_detail.get("account_id")
            if not access_token:
                return self._skip_token("缺少 access_token", logger)
            if disabled and tracked_next_check_at is not None and now < tracked_next_check_at:
                logger.log(
                    "INFO",
                    f"当前账号已禁用，计划于 {self._format_tracked_next_check_at(tracked_next_check_at)} 后复查额度，当前轮次跳过",
                    indent=1,
                )
                self._inc_stat("alive")
                logger.blank_line()
                return "alive"

            status, resp_data = self.check_token_live(access_token, account_id)
            if status in (401, 402):
                return self._handle_invalid_token(name, logger)
            if status is None:
                detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
                msg = "在线状态检查失败"
                if detail:
                    msg += f" | {detail}"
                return self._skip_token(msg, logger, network_error=True)
            if status != 200:
                return self._handle_non_200_status(status, resp_data, logger)

            body_info = self.parse_usage_info(resp_data)
            primary_pct, secondary_pct, primary_label, secondary_label = self._log_usage_summary(body_info, logger)
            quota_result, refresh_disabled = self._apply_quota_policy(
                name,
                disabled,
                primary_pct,
                secondary_pct,
                logger,
                has_refresh_token=self._has_refresh_token(token_detail),
                primary_label=primary_label,
                secondary_label=secondary_label,
                body_info=body_info,
                token_detail=token_detail,
                now=now,
            )
            if quota_result:
                return quota_result
            self._apply_refresh_policy(
                name,
                token_detail,
                remaining_seconds,
                remaining_str,
                logger,
                disabled=refresh_disabled,
            )

            self._inc_stat("alive")
            logger.blank_line()
            return "alive"
        finally:
            logger.flush()

    def log_startup(self):
        info_prefix = self.logger.PREFIX_MAP["INFO"]
        dry_prefix = self.logger.PREFIX_MAP["DRY"]
        usage_query_interval_display = (
            "已禁用（CPA_USAGE_QUERY_INTERVAL=0）"
            if self.settings.usage_query_interval_seconds == 0
            else f"{self.settings.usage_query_interval_seconds} 秒"
        )
        lines = [
            "=" * 60,
            f"{info_prefix} 启动配置",
            f"    {info_prefix} CPA 接口: {self.settings.cpa_endpoint}",
            f"    {info_prefix} 配额阈值: {self.settings.quota_threshold}%",
            f"    {info_prefix} 过期刷新阈值: {self.settings.expiry_threshold_days} 天",
            f"    {info_prefix} 主巡检间隔: {self.settings.interval_seconds} 秒",
            f"    {info_prefix} 日志巡检间隔: {usage_query_interval_display}",
            f"    {info_prefix} 主巡检线程数: {self.settings.worker_threads}",
            f"    {info_prefix} 自动刷新: {'开启' if self.settings.enable_refresh else '关闭'}",
        ]
        if self.dry_run:
            lines.append(f"    {dry_prefix} 运行模式: 演练模式（不实际修改）")
        lines.append("=" * 60)
        self.logger.emit_lines(lines)

    def _process_tokens_with_priority(self, tokens):
        total = len(tokens)
        token_iter = iter(enumerate(tokens, 1))
        token_iter_lock = threading.Lock()

        def worker():
            while True:
                with token_iter_lock:
                    try:
                        idx, token_info = next(token_iter)
                    except StopIteration:
                        return
                self._acquire_priority("full")
                try:
                    try:
                        self.process_token(token_info, idx, total)
                    except Exception as exc:
                        token_name = token_info.get("name", "unknown")
                        self.log("ERROR", f"Token 任务异常 ({token_name}): {exc}", indent=1)
                        self.blank_line()
                finally:
                    self._release_priority("full")

        with ThreadPoolExecutor(max_workers=self.settings.worker_threads) as executor:
            worker_count = min(total, self.settings.worker_threads)
            futures = [executor.submit(worker) for _ in range(worker_count)]
            for future in as_completed(futures):
                future.result()

    def run(self):
        self.reset_stats()
        self.log_startup()
        tokens = self.get_token_list()
        if not tokens:
            self.log("WARN", "主巡检未发现任何可处理的 codex 账号")
            return

        self._set_total(len(tokens))
        random.shuffle(tokens)
        start_time = time.time()
        total = len(tokens)
        self.log("INFO", f"主巡检任务已就绪：本轮共 {total} 个 codex 账号 待处理")
        self.log("INFO", f"主巡检并发设置：{self.settings.worker_threads} 个工作线程")
        self.blank_line()

        self._process_tokens_with_priority(tokens)

        elapsed = time.time() - start_time
        stats = self._stats_snapshot()
        info_prefix = self.logger.PREFIX_MAP["INFO"]
        ok_prefix = self.logger.PREFIX_MAP["OK"]
        disable_prefix = self.logger.PREFIX_MAP["DISABLE"]
        enable_prefix = self.logger.PREFIX_MAP["ENABLE"]
        refresh_prefix = self.logger.PREFIX_MAP["REFRESH"]
        delete_prefix = self.logger.PREFIX_MAP["DELETE"]
        skip_prefix = self.logger.PREFIX_MAP["SKIP"]
        error_prefix = self.logger.PREFIX_MAP["ERROR"]
        self.logger.emit_lines([
            "=" * 60,
            f"{info_prefix} 执行总结",
            f"    {info_prefix} 总耗时: {elapsed:.1f} 秒",
            f"    {info_prefix} Token 总数: {stats['total']}",
            f"    {info_prefix} 工作线程: {self.settings.worker_threads}",
            f"{info_prefix} 状态统计",
            f"    {ok_prefix} 存活: {stats['alive']}",
            f"    {delete_prefix} 死号(已删除): {stats['dead']}",
            f"    {disable_prefix} 已禁用: {stats['disabled']}",
            f"    {enable_prefix} 已启用: {stats['enabled']}",
            f"    {refresh_prefix} 已刷新: {stats['refreshed']}",
            f"{info_prefix} 其他统计",
            f"    {skip_prefix} 跳过: {stats['skipped']}",
            f"    {error_prefix} 网络失败: {stats['network_error']}",
            "=" * 60,
        ])

    def run_forever(self, interval_seconds=1800):
        round_no = 0
        self._start_tracked_rechecks()
        self.log("INFO", f"主巡检守护进程已启动（轮询间隔: {interval_seconds} 秒）")
        while True:
            round_no += 1
            self.log("INFO", f"主巡检第 {round_no} 轮开始：准备扫描全部 codex 账号")
            try:
                self.run()
                self.log("INFO", f"主巡检第 {round_no} 轮结束：已完成本轮全部 codex 账号扫描")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log("ERROR", f"主巡检第 {round_no} 轮异常: {exc}")
            self.log("INFO", f"主巡检休眠中：{interval_seconds} 秒后开始下一轮全量扫描")
            time.sleep(interval_seconds)

    def run_fill_once(self):
        if self.settings.usage_query_interval_seconds == 0:
            self.log("INFO", "日志巡检已禁用：CPA_USAGE_QUERY_INTERVAL=0，跳过本轮CPA使用日志扫描")
            return "disabled"

        now = int(time.time())
        if self.last_usage_query_time is None:
            self.last_usage_query_time = now
            self.log("INFO", "日志巡检首次启动：已记录起始查询时间，下一轮开始比对新增日志")
            return "primed"

        query_started_at = now
        usage_data = self.get_usage_log()
        if not usage_data:
            self.last_usage_query_time = query_started_at
            self.log("WARN", "日志巡检未获取到CPA日志数据：本轮无法筛选新增调用账号")
            return "skipped"

        latest_by_email = self._latest_usage_timestamp_by_email(usage_data, after_timestamp=self.last_usage_query_time)
        token_map = self.get_fill_token_map()
        matched_tokens = [token_map[email] for email in latest_by_email if email in token_map]

        total = len(matched_tokens)
        if total:
            self.reset_stats()
            self._set_total(total)
            self.log("INFO", f"日志巡检命中 {total} 个账号：开始逐个校验额度并按需禁用")
            for idx, token_info in enumerate(matched_tokens, 1):
                self._acquire_priority("log")
                try:
                    self.process_fill_token(token_info, idx, total)
                finally:
                    self._release_priority("log")
        else:
            self.log("INFO", "日志巡检未命中新账号：本轮没有需要进一步检查的 usage 记录")

        self.last_usage_query_time = query_started_at
        return "processed"

    def run_fill_forever(self, interval_seconds=10):
        round_no = 0
        self.log("INFO", f"日志巡检守护进程已启动（轮询间隔: {interval_seconds} 秒）")
        while True:
            round_no += 1
            self.log("INFO", f"日志巡检第 {round_no} 轮开始：准备扫描新增CPA使用日志")
            try:
                self.run_fill_once()
                self.log("INFO", f"日志巡检第 {round_no} 轮结束：已完成本轮CPA使用日志扫描")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log("ERROR", f"日志巡检第 {round_no} 轮异常: {exc}")
            self.log("INFO", f"日志巡检休眠中：{interval_seconds} 秒后开始下一轮CPA使用日志扫描")
            time.sleep(interval_seconds)
