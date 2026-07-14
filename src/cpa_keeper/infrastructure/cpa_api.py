"""Provider-neutral CPA management API with explicit failure outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from types import MappingProxyType
from typing import Callable, Mapping

from .http import CurlCffiHttpTransport, HttpResponse, HttpTransport


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class CpaOperationResult:
    """Safe result of a CPA write operation without upstream response text."""

    ok: bool
    status_code: int | None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise ValueError("ok must be a boolean")
        if self.ok and self.error_code is not None:
            raise ValueError("successful CPA operation cannot have an error code")


@dataclass(frozen=True, slots=True)
class CpaListResult:
    """CPA auth-file list outcome; list rows are hidden from repr due to status text."""

    ok: bool
    auth_files: tuple[Mapping[str, object], ...] = field(default_factory=tuple, repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        auth_files = tuple(_freeze_mapping(item) for item in self.auth_files)
        if type(self.ok) is not bool:
            raise ValueError("ok must be a boolean")
        if self.ok and self.error_code is not None:
            raise ValueError("successful CPA list cannot have an error code")
        if not self.ok and self.auth_files:
            raise ValueError("failed CPA list cannot include auth-file rows")
        object.__setattr__(self, "auth_files", auth_files)


@dataclass(frozen=True, slots=True)
class CpaAuthFileResult:
    """Credential-body download outcome; the body is never included in repr output."""

    ok: bool
    payload: Mapping[str, object] | None = field(default=None, repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise ValueError("ok must be a boolean")
        if self.payload is not None:
            object.__setattr__(self, "payload", _freeze_mapping(self.payload))
        if self.ok and self.payload is None:
            raise ValueError("successful auth-file download requires a payload")
        if self.ok and self.error_code is not None:
            raise ValueError("successful auth-file download cannot have an error code")


class CpaApi:
    """Shared CPA management client with retry-only transport behavior."""

    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        proxy: str | None = None,
        timeout_seconds: int = 30,
        max_retries: int = 2,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty URL")
        if not isinstance(token, str) or not token.strip():
            raise ValueError("token must be non-empty")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self._endpoint = endpoint.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._max_retries = max_retries
        self._sleep = sleep
        self._transport = transport or CurlCffiHttpTransport(
            proxy=proxy,
            timeout_seconds=timeout_seconds,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> HttpResponse:
        for attempt in range(self._max_retries + 1):
            response = self._transport.request(
                method,
                f"{self._endpoint}{path}",
                headers=dict(self._headers),
                params=dict(params) if params is not None else None,
                json_body=dict(json_body) if json_body is not None else None,
            )
            if response.status_code is not None and response.status_code >= 500 and attempt < self._max_retries:
                self._sleep(1)
                continue
            return response
        raise AssertionError("retry loop must return a response")

    @staticmethod
    def _failure_code(response: HttpResponse) -> str:
        if response.status_code is None:
            return response.error_code or "transport_error"
        return f"http_{response.status_code}"

    @staticmethod
    def _require_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("auth-file name must be a non-empty string")

    def list_auth_files(self) -> CpaListResult:
        response = self._request("GET", "/v0/management/auth-files")
        if response.status_code != 200:
            return CpaListResult(ok=False, error_code=self._failure_code(response))
        payload = response.json_data
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
            return CpaListResult(ok=False, error_code="invalid_list_response")
        files = payload["files"]
        if not all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and bool(item["name"].strip())
            and isinstance(item.get("type"), str)
            and bool(item["type"].strip())
            for item in files
        ):
            return CpaListResult(ok=False, error_code="invalid_list_entry")
        if len({item["name"] for item in files}) != len(files):
            return CpaListResult(ok=False, error_code="duplicate_auth_file_name")
        return CpaListResult(ok=True, auth_files=tuple(files))

    def get_auth_file(self, name: str) -> CpaAuthFileResult:
        self._require_name(name)
        response = self._request(
            "GET",
            "/v0/management/auth-files/download",
            params={"name": name},
        )
        if response.status_code != 200:
            return CpaAuthFileResult(ok=False, error_code=self._failure_code(response))
        if not isinstance(response.json_data, dict):
            return CpaAuthFileResult(ok=False, error_code="invalid_auth_file_response")
        return CpaAuthFileResult(ok=True, payload=response.json_data)

    def delete_auth_file(self, name: str) -> CpaOperationResult:
        self._require_name(name)
        response = self._request(
            "DELETE",
            "/v0/management/auth-files",
            params={"name": name},
        )
        ok = response.status_code in {200, 204}
        return CpaOperationResult(
            ok=ok,
            status_code=response.status_code,
            error_code=None if ok else self._failure_code(response),
        )

    def set_disabled(self, name: str, disabled: bool) -> CpaOperationResult:
        self._require_name(name)
        if type(disabled) is not bool:
            raise ValueError("disabled must be a boolean")
        response = self._request(
            "PATCH",
            "/v0/management/auth-files/status",
            json_body={"name": name, "disabled": disabled},
        )
        ok = response.status_code == 200
        return CpaOperationResult(
            ok=ok,
            status_code=response.status_code,
            error_code=None if ok else self._failure_code(response),
        )

    def upload_auth_file(
        self, name: str, payload: Mapping[str, object]
    ) -> CpaOperationResult:
        self._require_name(name)
        if not isinstance(payload, Mapping):
            raise ValueError("auth-file upload payload must be a mapping")
        response = self._request(
            "POST",
            "/v0/management/auth-files",
            params={"name": name},
            json_body=payload,
        )
        ok = response.status_code == 200
        return CpaOperationResult(
            ok=ok,
            status_code=response.status_code,
            error_code=None if ok else self._failure_code(response),
        )
