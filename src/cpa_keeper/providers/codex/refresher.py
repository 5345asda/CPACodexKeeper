"""Codex credential refresh implementation owned by the Codex provider."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Protocol

from cpa_keeper.domain.auth_files import AuthFileMetadata


class RefreshApi(Protocol):
    """Minimal provider-owned OAuth refresh interface."""

    def refresh_token(self, refresh_token: str) -> object:
        """Return an object with status_code and a payload mapping."""


@dataclass(frozen=True, slots=True)
class RefreshUpload:
    """Opaque replacement body returned by Codex OAuth refresh."""

    payload: Mapping[str, object] = field(repr=False, compare=False)
    restore_disabled: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class CodexRefresher:
    """Refresh a downloaded Codex auth body and return an opaque upload payload."""

    def __init__(
        self,
        refresh_api: RefreshApi,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._refresh_api = refresh_api
        self._now = now

    def refresh(
        self,
        metadata: AuthFileMetadata,
        auth_file: Mapping[str, object],
    ) -> RefreshUpload:
        """Return a replacement payload or fail without emitting credential text."""
        if metadata.provider_id != "codex":
            raise ValueError("Codex refresher cannot refresh another provider")
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
        expires_in = refreshed.get("expires_in", 864000)
        if type(expires_in) is not int or expires_in <= 0:
            expires_in = 864000
        now = self._now().astimezone(timezone.utc)
        replacement = dict(auth_file)
        replacement.update(
            {
                "access_token": access_token,
                "refresh_token": refreshed.get("refresh_token", refresh_token),
                "id_token": refreshed.get("id_token"),
                "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expired": (now + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        return RefreshUpload(payload=replacement, restore_disabled=metadata.disabled)
