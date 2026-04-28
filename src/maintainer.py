import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .cpa_client import CPAClient
from .logging_utils import ConsoleLogger, TokenLogger
from .models import MaintainerStats, format_window_label
from .openai_client import OpenAIClient, parse_usage_info
from .reports import ReportRegistry, TokenReport
from .settings import Settings
from .utils import format_seconds, get_expired_remaining, get_expired_remaining_with_status


class CPACodexKeeper:
    def __init__(self, settings: Settings, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self.logger = ConsoleLogger()
        snap = settings.snapshot()
        self.cpa_client = CPAClient(
            snap.cpa_endpoint,
            snap.cpa_token,
            proxy=snap.proxy,
            timeout=snap.cpa_timeout_seconds,
            max_retries=snap.max_retries,
        )
        self.openai_client = OpenAIClient(
            proxy=snap.proxy,
            timeout=snap.usage_timeout_seconds,
            max_retries=snap.max_retries,
        )
        self.stats = MaintainerStats()
        self._stats_lock = threading.Lock()
        self.reports = ReportRegistry()
        self._run_lock = threading.Lock()
        self.last_run_started_at: float | None = None
        self.last_run_finished_at: float | None = None
        if hasattr(settings, "add_listener"):
            settings.add_listener(self._handle_settings_update)

    def _handle_settings_update(self, snapshot, changed):
        if "max_retries" in changed:
            self.cpa_client.max_retries = snapshot.max_retries
            self.openai_client.max_retries = snapshot.max_retries

    def is_running(self) -> bool:
        return self._run_lock.locked()

    def try_acquire_operation_lock(self) -> bool:
        return self._run_lock.acquire(blocking=False)

    def release_operation_lock(self) -> None:
        self._run_lock.release()

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
            (logger or self).log("DRY", f"将删除: {name}", indent=1)
            return True
        return self.cpa_client.delete_auth_file(name)

    def set_disabled_status(self, name, disabled=True, logger=None):
        if self.dry_run:
            (logger or self).log("DRY", f"将{'禁用' if disabled else '启用'}: {name}", indent=1)
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
            (logger or self).log("DRY", f"将上传更新: {name}", indent=1)
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

        logger.log("INFO", f"Email: {email}", indent=1)
        logger.log("INFO", f"状态: {'已禁用' if disabled else '正常'}", indent=1)
        logger.log("INFO", f"过期时间: {expired_str or '未知'}", indent=1)
        logger.log("INFO", f"剩余有效期: {remaining_str}", indent=1)
        return disabled, remaining_seconds, remaining_str, expiry_known

    def _has_refresh_token(self, token_detail):
        return bool((token_detail.get("refresh_token") or "").strip())

    def _delete_token_with_reason(self, name, reason, logger):
        logger.log("WARN", reason, indent=1)
        if self.delete_token(name, logger=logger):
            logger.log("DELETE", "已删除", indent=1)
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
        logger.log("OK", f"存活 | Plan: {plan} | {quota_info}", indent=1)
        return primary_pct, secondary_pct, primary_label, secondary_label

    def _apply_quota_policy(
        self,
        name,
        disabled,
        primary_pct,
        secondary_pct,
        logger,
        *,
        snap,
        has_refresh_token=True,
        primary_label="primary_window",
        secondary_label="secondary_window",
    ):
        primary_reached = primary_pct >= snap.quota_threshold
        secondary_present = secondary_pct is not None
        secondary_reached = secondary_present and secondary_pct >= snap.quota_threshold
        effective_disabled = disabled

        if secondary_present:
            below_threshold = primary_pct < snap.quota_threshold and secondary_pct < snap.quota_threshold
            reached_parts = []
            if primary_reached:
                reached_parts.append(f"{primary_label}额度 {primary_pct}%")
            if secondary_reached:
                reached_parts.append(f"{secondary_label}额度 {secondary_pct}%")
            reached_summary = "、".join(reached_parts)
        else:
            below_threshold = primary_pct < snap.quota_threshold
            reached_summary = f"{primary_label}额度 {primary_pct}%"

        if disabled:
            if below_threshold:
                if secondary_present:
                    logger.log(
                        "WARN",
                        f"已禁用且 {primary_label}/{secondary_label} 额度均已低于 {snap.quota_threshold}%，准备启用",
                        indent=1,
                    )
                else:
                    logger.log(
                        "WARN",
                        f"已禁用但{primary_label}额度已降至 {primary_pct}% < {snap.quota_threshold}%，准备启用",
                        indent=1,
                    )
                if self.set_disabled_status(name, disabled=False, logger=logger):
                    logger.log("ENABLE", "已重新启用", indent=1)
                    self._inc_stat("enabled")
                    effective_disabled = False
                else:
                    logger.log("ERROR", "启用失败", indent=1)
                return None, effective_disabled
            if not has_refresh_token and (primary_reached or secondary_reached):
                return self._delete_token_with_reason(
                    name,
                    f"无 Refresh Token，且{reached_summary} >= {snap.quota_threshold}%，准备删除",
                    logger,
                ), effective_disabled
            logger.log(
                "INFO",
                f"已禁用，{reached_summary} >= {snap.quota_threshold}%，保持禁用",
                indent=1,
            )
            return None, effective_disabled

        if primary_reached or secondary_reached:
            if not has_refresh_token:
                return self._delete_token_with_reason(
                    name,
                    f"无 Refresh Token，且{reached_summary} >= {snap.quota_threshold}%，准备删除",
                    logger,
                ), effective_disabled
            logger.log(
                "WARN",
                f"{reached_summary} >= {snap.quota_threshold}%，准备禁用",
                indent=1,
            )
            if self.set_disabled_status(name, disabled=True, logger=logger):
                logger.log("DISABLE", "已禁用", indent=1)
                self._inc_stat("disabled")
                effective_disabled = True
            else:
                logger.log("ERROR", "禁用失败", indent=1)
            return None, effective_disabled

        return None, effective_disabled

    def _apply_refresh_policy(self, name, token_detail, remaining_seconds, remaining_str, logger, *, snap, disabled):
        expiry_threshold_seconds = snap.expiry_threshold_days * 86400
        if remaining_seconds > 0 and remaining_seconds < expiry_threshold_seconds:
            if not snap.enable_refresh:
                logger.log(
                    "INFO",
                    f"剩余 {remaining_str} < {snap.expiry_threshold_days} 天，但刷新功能已关闭",
                    indent=1,
                )
                return
            if not disabled:
                logger.log(
                    "INFO",
                    f"剩余 {remaining_str} < {snap.expiry_threshold_days} 天，但当前为启用状态，交给 CPA 自动刷新",
                    indent=1,
                )
                return
            logger.log("WARN", f"剩余 {remaining_str} < {snap.expiry_threshold_days} 天，准备刷新", indent=1)
            success, new_data, msg = self.try_refresh(token_detail)
            if success:
                if self.upload_updated_token(name, new_data, logger=logger):
                    if disabled:
                        if self.set_disabled_status(name, disabled=True, logger=logger):
                            logger.log("DISABLE", "刷新后保持禁用", indent=1)
                        else:
                            logger.log("ERROR", "刷新后回写禁用失败", indent=1)
                    _, new_remaining = get_expired_remaining(new_data)
                    logger.log("REFRESH", f"{msg}，新剩余: {format_seconds(new_remaining)}", indent=1)
                    self._inc_stat("refreshed")
                else:
                    logger.log("ERROR", "刷新成功但上传失败", indent=1)
            else:
                logger.log("ERROR", f"刷新失败: {msg}", indent=1)
        elif remaining_seconds > 0:
            logger.log("INFO", f"过期时间充足 ({remaining_str})", indent=1)

    def process_token(self, token_info, idx, total, *, snap=None):
        if snap is None:
            snap = self.settings.snapshot()
        name = token_info.get("name", "unknown")
        logger = TokenLogger(self.logger, idx, total, name)
        report = self.reports.touch(name)
        outcome = "skipped"
        try:
            logger.log("INFO", "获取详情...", indent=1)
            token_detail = self.get_token_detail(name)
            if not token_detail:
                outcome = self._skip_token("获取详情失败", logger)
                return outcome

            disabled, remaining_seconds, remaining_str, expiry_known = self._log_token_details(token_detail, logger)
            report.email = token_detail.get("email")
            report.disabled = bool(disabled)
            report.expiry = token_detail.get("expired") or None
            report.expiry_remaining_seconds = int(remaining_seconds) if expiry_known else None

            cleanup_result = self._apply_non_refreshable_expiry_policy(name, token_detail, remaining_seconds, expiry_known, logger)
            if cleanup_result:
                outcome = cleanup_result
                return outcome
            access_token = token_detail.get("access_token")
            account_id = token_detail.get("account_id")
            if not access_token:
                outcome = self._skip_token("缺少 access_token", logger)
                return outcome

            logger.log("INFO", "检测在线状态...", indent=1)
            status, resp_data = self.check_token_live(access_token, account_id)
            if status in (401, 402):
                outcome = self._handle_invalid_token(name, logger)
                return outcome
            if status is None:
                detail = resp_data.get("brief", "") if isinstance(resp_data, dict) else str(resp_data)
                msg = "网络检测失败"
                if detail:
                    msg += f" | {detail}"
                outcome = self._skip_token(msg, logger, network_error=True)
                return outcome
            if status != 200:
                outcome = self._handle_non_200_status(status, resp_data, logger)
                return outcome

            body_info = self.parse_usage_info(resp_data)
            primary_pct, secondary_pct, primary_label, secondary_label = self._log_usage_summary(body_info, logger)
            report.plan_type = body_info.get("plan_type")
            report.primary_used_percent = primary_pct
            report.secondary_used_percent = secondary_pct
            report.primary_window_seconds = body_info.get("primary_window_seconds")
            report.secondary_window_seconds = body_info.get("secondary_window_seconds")
            report.has_credits = body_info.get("has_credits")
            quota_result, refresh_disabled = self._apply_quota_policy(
                name,
                disabled,
                primary_pct,
                secondary_pct,
                logger,
                snap=snap,
                has_refresh_token=self._has_refresh_token(token_detail),
                primary_label=primary_label,
                secondary_label=secondary_label,
            )
            report.disabled = bool(refresh_disabled)
            if quota_result:
                outcome = quota_result
                return outcome
            self._apply_refresh_policy(
                name,
                token_detail,
                remaining_seconds,
                remaining_str,
                logger,
                snap=snap,
                disabled=refresh_disabled,
            )

            self._inc_stat("alive")
            logger.blank_line()
            outcome = "alive"
            return outcome
        finally:
            report.last_outcome = self._report_outcome(outcome, logger.entries)
            report.last_actions = [f"{level}: {msg}" for level, msg in logger.entries]
            report.last_log_lines = logger.lines()
            report.disabled = bool(getattr(report, "disabled", False))
            self.reports.upsert(report)
            logger.flush()

    def _report_outcome(self, default_outcome: str, entries: list[tuple[str, str]]) -> str:
        if default_outcome in {"dead", "network_error", "skipped"}:
            return default_outcome
        levels = [level for level, _ in entries]
        if "REFRESH" in levels:
            return "refreshed"
        if "DISABLE" in levels:
            return "disabled"
        if "ENABLE" in levels:
            return "enabled"
        return default_outcome

    def process_one(self, name: str) -> TokenReport:
        """Run process_token for a single token, identified by name. Refreshes
        the registry entry and returns it. Raises ValueError if not in the codex
        token list.
        """
        tokens = self.get_token_list()
        match = next((t for t in tokens if t.get("name") == name), None)
        if match is None:
            raise ValueError(f"token not found: {name}")
        snap = self.settings.snapshot()
        self.process_token(match, 1, 1, snap=snap)
        report = self.reports.get(name)
        if report is None:
            raise RuntimeError(f"report missing after scan: {name}")
        return report

    def log_startup(self):
        snap = self.settings.snapshot()
        self.logger.divider()
        self.log("INFO", "CPACodexKeeper 启动")
        self.log("INFO", f"API: {snap.cpa_endpoint}")
        self.log("INFO", f"Quota threshold: {snap.quota_threshold}% (disable when reached)")
        self.log("INFO", f"Expiry threshold: {snap.expiry_threshold_days} days (refresh disabled auth when below)")
        self.log("INFO", f"Refresh enabled: {snap.enable_refresh}")
        if self.dry_run:
            self.log("DRY", "演练模式 (不实际修改)")
        self.logger.divider()

    def run(self):
        if not self._run_lock.acquire(blocking=False):
            self.log("WARN", "已有巡检正在运行，跳过本次触发")
            return
        try:
            self.last_run_started_at = time.time()
            self.reset_stats()
            self.log_startup()
            snap = self.settings.snapshot()
            tokens = self.get_token_list()
            if not tokens:
                self.log("WARN", "未获取到任何 codex Token")
                # Drop reports for tokens that no longer exist on CPA side.
                self.reports.replace_all([])
                return

            self._set_total(len(tokens))
            random.shuffle(tokens)
            start_time = time.time()
            total = len(tokens)
            self.log("INFO", f"共计: {total} 个 codex Token")
            self.log("INFO", f"线程数: {snap.worker_threads}")
            self.blank_line()

            current_names = {t.get("name") for t in tokens}
            for existing in [r.name for r in self.reports.all()]:
                if existing not in current_names:
                    self.reports.remove(existing)

            future_map = {}
            with ThreadPoolExecutor(max_workers=snap.worker_threads) as executor:
                for idx, token_info in enumerate(tokens, 1):
                    future = executor.submit(self.process_token, token_info, idx, total, snap=snap)
                    future_map[future] = token_info

                for future in as_completed(future_map):
                    try:
                        future.result()
                    except Exception as exc:
                        token_name = future_map[future].get("name", "unknown")
                        self.log("ERROR", f"Token 任务异常 ({token_name}): {exc}", indent=1)
                        self.blank_line()

            elapsed = time.time() - start_time
            stats = self._stats_snapshot()
            self.logger.divider()
            self.log("INFO", "执行完成")
            self.log("INFO", f"耗时: {elapsed:.1f} 秒")
            self.log("INFO", "统计:")
            self.log("INFO", f"- 总计: {stats['total']}", indent=1)
            self.log("INFO", f"- 存活: {stats['alive']}", indent=1)
            self.log("INFO", f"- 死号(已删除): {stats['dead']}", indent=1)
            self.log("INFO", f"- 已禁用: {stats['disabled']}", indent=1)
            self.log("INFO", f"- 已启用: {stats['enabled']}", indent=1)
            self.log("INFO", f"- 已刷新: {stats['refreshed']}", indent=1)
            self.log("INFO", f"- 跳过: {stats['skipped']}", indent=1)
            self.log("INFO", f"- 网络失败: {stats['network_error']}", indent=1)
            self.logger.divider()
        finally:
            self.last_run_finished_at = time.time()
            self._run_lock.release()

    def run_forever(self, interval_seconds=None):
        round_no = 0
        snap = self.settings.snapshot()
        initial_interval = interval_seconds if interval_seconds is not None else snap.interval_seconds
        self.log("INFO", f"守护模式启动，执行间隔: {initial_interval} 秒")
        while True:
            round_no += 1
            self.log("INFO", f"开始第 {round_no} 轮巡检")
            try:
                self.run()
                self.log("INFO", f"第 {round_no} 轮巡检结束")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.log("ERROR", f"第 {round_no} 轮巡检异常: {exc}")
            wait = interval_seconds if interval_seconds is not None else self.settings.snapshot().interval_seconds
            self.log("INFO", f"等待 {wait} 秒后开始下一轮")
            time.sleep(wait)
