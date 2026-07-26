"""Codex credential refresh owned by the Codex provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cpa_keeper.domain.auth_files import AuthFileMetadata

DEFAULT_EXPIRES_IN_SECONDS = 864000
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class RefreshApi(Protocol):
    """Minimal provider-owned OAuth refresh interface."""

    def refresh_token(self, refresh_token: str) -> object: ...


@dataclass(frozen=True, slots=True)
class RefreshUpload:
    """Opaque replacement body returned by Codex OAuth refresh."""

    payload: Mapping[str, object] = field(repr=False)
    restore_disabled: bool


class CodexRefresher:
    """Refresh a downloaded Codex auth body and return an opaque upload payload."""

    def __init__(
        self,
        refresh_api: RefreshApi,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._refresh_api = refresh_api
        self._now = now

    def refresh(
        self,
        metadata: AuthFileMetadata,
        auth_file: Mapping[str, object],
    ) -> RefreshUpload:
        """Return a replacement payload or fail without emitting credential text."""
        refresh_token = auth_file.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise ValueError("refresh_material_missing")
        response = self._refresh_api.refresh_token(refresh_token)
        if getattr(response, "status_code", None) != 200:
            raise RuntimeError("refresh_rejected")
        refreshed = getattr(response, "payload", None)
        if not isinstance(refreshed, Mapping):
            raise RuntimeError("refresh_response_invalid")
        access_token = refreshed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("refresh_response_invalid")
        expires_in = refreshed.get("expires_in")
        if type(expires_in) is not int or expires_in <= 0:
            expires_in = DEFAULT_EXPIRES_IN_SECONDS
        now = self._now().astimezone(UTC)
        replacement = dict(auth_file)
        replacement.update(
            {
                "access_token": access_token,
                "refresh_token": refreshed.get("refresh_token", refresh_token),
                "id_token": refreshed.get("id_token"),
                "last_refresh": now.strftime(_TIMESTAMP_FORMAT),
                "expired": (now + timedelta(seconds=expires_in)).strftime(_TIMESTAMP_FORMAT),
            }
        )
        return RefreshUpload(payload=replacement, restore_disabled=metadata.disabled)
