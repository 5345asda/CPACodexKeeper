"""Convert Codex credential detail and usage responses into secret-free inspection facts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from cpa_keeper.domain.auth_files import AuthFileMetadata


class ExpiryState(StrEnum):
    """Safe expiry buckets used by lifecycle policies."""

    UNKNOWN = "unknown"
    EXPIRED = "expired"
    BELOW_REFRESH_THRESHOLD = "below_refresh_threshold"
    SUFFICIENT = "sufficient"


@dataclass(frozen=True, slots=True)
class CodexInspectionFacts:
    """Typed observation facts with no credential body fields."""

    metadata: AuthFileMetadata
    http_status: int | None
    primary_usage_percent: int | None
    secondary_usage_percent: int | None
    has_refresh_material: bool
    expiry_state: ExpiryState
    refresh_enabled: bool


class UsageApi(Protocol):
    """Minimal OpenAI usage surface used by Codex inspection."""

    def check_usage(self, access_token: str, account_id: str | None) -> object: ...


def _parse_expiry_state(
    value: object,
    *,
    now_epoch_seconds: float,
    refresh_before_expiry_days: int,
) -> ExpiryState:
    if not isinstance(value, str) or not value.strip():
        return ExpiryState.UNKNOWN
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ExpiryState.UNKNOWN
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    remaining_seconds = parsed.timestamp() - now_epoch_seconds
    if remaining_seconds <= 0:
        return ExpiryState.EXPIRED
    if remaining_seconds < refresh_before_expiry_days * 86400:
        return ExpiryState.BELOW_REFRESH_THRESHOLD
    return ExpiryState.SUFFICIENT


def _usage_percent(window: object) -> int | None:
    value = window.get("used_percent") if isinstance(window, Mapping) else None
    if type(value) is int and 0 <= value <= 100:
        return value
    return None


class CodexInspector:
    """Inspect one downloaded Codex auth file without exposing its credential contents."""

    def __init__(
        self,
        usage_api: UsageApi,
        *,
        now_epoch_seconds: Callable[[], float],
    ) -> None:
        self._usage_api = usage_api
        self._now_epoch_seconds = now_epoch_seconds

    def inspect(
        self,
        metadata: AuthFileMetadata,
        auth_file: Mapping[str, object],
        *,
        refresh_before_expiry_days: int,
        refresh_enabled: bool,
    ) -> CodexInspectionFacts:
        """Return only lifecycle facts, querying usage only when it is still meaningful."""
        detail_disabled = auth_file.get("disabled")
        if type(detail_disabled) is bool and detail_disabled != metadata.disabled:
            metadata = replace(metadata, disabled=detail_disabled)

        refresh_token = auth_file.get("refresh_token")
        has_refresh_material = isinstance(refresh_token, str) and bool(refresh_token.strip())
        facts = CodexInspectionFacts(
            metadata=metadata,
            http_status=None,
            primary_usage_percent=None,
            secondary_usage_percent=None,
            has_refresh_material=has_refresh_material,
            expiry_state=_parse_expiry_state(
                auth_file.get("expired"),
                now_epoch_seconds=self._now_epoch_seconds(),
                refresh_before_expiry_days=refresh_before_expiry_days,
            ),
            refresh_enabled=refresh_enabled,
        )

        # An expired credential without refresh material is already terminal;
        # skip the usage request instead of probing a dead token.
        if facts.expiry_state is ExpiryState.EXPIRED and not has_refresh_material:
            return facts

        access_token = auth_file.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return facts
        account_id = auth_file.get("account_id")
        response = self._usage_api.check_usage(
            access_token,
            account_id if isinstance(account_id, str) and account_id else None,
        )
        status_code = getattr(response, "status_code", None)
        if type(status_code) is not int:
            return facts
        if status_code != 200:
            return replace(facts, http_status=status_code)

        payload = getattr(response, "payload", None)
        rate_limit = payload.get("rate_limit") if isinstance(payload, Mapping) else None
        if not isinstance(rate_limit, Mapping):
            return replace(facts, http_status=status_code)
        return replace(
            facts,
            http_status=status_code,
            primary_usage_percent=_usage_percent(rate_limit.get("primary_window")),
            secondary_usage_percent=_usage_percent(rate_limit.get("secondary_window")),
        )
