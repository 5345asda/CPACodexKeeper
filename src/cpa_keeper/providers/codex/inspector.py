"""Convert Codex credential detail and usage responses into secret-free inspection facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Protocol

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

    def __post_init__(self) -> None:
        if self.metadata.provider_id != "codex":
            raise ValueError("Codex inspection facts require Codex metadata")
        if self.http_status is not None and (
            type(self.http_status) is not int or self.http_status < 100 or self.http_status > 599
        ):
            raise ValueError("http_status must be an HTTP status or None")
        for field_name in ("primary_usage_percent", "secondary_usage_percent"):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not int or value < 0 or value > 100):
                raise ValueError(f"{field_name} must be a percentage or None")
        if self.primary_usage_percent is None and self.secondary_usage_percent is not None:
            raise ValueError("secondary usage requires a primary usage value")
        if type(self.has_refresh_material) is not bool or type(self.refresh_enabled) is not bool:
            raise ValueError("refresh flags must be booleans")
        if not isinstance(self.expiry_state, ExpiryState):
            raise ValueError("expiry_state must be an ExpiryState")


class UsageApi(Protocol):
    """Minimal OpenAI usage surface used by Codex inspection."""

    def check_usage(self, access_token: str, account_id: str | None) -> object:
        """Return an object with status_code and a mapping payload."""


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


def _usage_percent(value: object) -> int | None:
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
        if metadata.provider_id != "codex":
            raise ValueError("Codex inspector cannot inspect another provider")
        if type(refresh_before_expiry_days) is not int or refresh_before_expiry_days < 0:
            raise ValueError("refresh_before_expiry_days must be non-negative")
        if type(refresh_enabled) is not bool:
            raise ValueError("refresh_enabled must be a boolean")
        if not isinstance(auth_file, Mapping):
            raise ValueError("auth_file must be a mapping")

        detail_disabled = auth_file.get("disabled")
        if type(detail_disabled) is bool and detail_disabled != metadata.disabled:
            metadata = AuthFileMetadata(
                name=metadata.name,
                provider_id=metadata.provider_id,
                disabled=detail_disabled,
                status=metadata.status,
            )
        refresh_token = auth_file.get("refresh_token")
        has_refresh_material = isinstance(refresh_token, str) and bool(refresh_token.strip())
        expiry_state = _parse_expiry_state(
            auth_file.get("expired"),
            now_epoch_seconds=self._now_epoch_seconds(),
            refresh_before_expiry_days=refresh_before_expiry_days,
        )
        if expiry_state is ExpiryState.EXPIRED and not has_refresh_material:
            return CodexInspectionFacts(
                metadata=metadata,
                http_status=None,
                primary_usage_percent=None,
                secondary_usage_percent=None,
                has_refresh_material=False,
                expiry_state=expiry_state,
                refresh_enabled=refresh_enabled,
            )

        access_token = auth_file.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return CodexInspectionFacts(
                metadata=metadata,
                http_status=None,
                primary_usage_percent=None,
                secondary_usage_percent=None,
                has_refresh_material=has_refresh_material,
                expiry_state=expiry_state,
                refresh_enabled=refresh_enabled,
            )
        account_id_value = auth_file.get("account_id")
        account_id = account_id_value if isinstance(account_id_value, str) and account_id_value else None
        response = self._usage_api.check_usage(access_token, account_id)
        status_code = getattr(response, "status_code", None)
        if type(status_code) is not int or status_code < 100 or status_code > 599:
            status_code = None
        if status_code != 200:
            return CodexInspectionFacts(
                metadata=metadata,
                http_status=status_code,
                primary_usage_percent=None,
                secondary_usage_percent=None,
                has_refresh_material=has_refresh_material,
                expiry_state=expiry_state,
                refresh_enabled=refresh_enabled,
            )
        payload = getattr(response, "payload", None)
        rate_limit = payload.get("rate_limit") if isinstance(payload, Mapping) else None
        primary = rate_limit.get("primary_window") if isinstance(rate_limit, Mapping) else None
        secondary = rate_limit.get("secondary_window") if isinstance(rate_limit, Mapping) else None
        primary_usage = _usage_percent(primary.get("used_percent")) if isinstance(primary, Mapping) else None
        secondary_usage = (
            _usage_percent(secondary.get("used_percent"))
            if isinstance(secondary, Mapping)
            else None
        )
        return CodexInspectionFacts(
            metadata=metadata,
            http_status=status_code,
            primary_usage_percent=primary_usage,
            secondary_usage_percent=secondary_usage,
            has_refresh_material=has_refresh_material,
            expiry_state=expiry_state,
            refresh_enabled=refresh_enabled,
        )
