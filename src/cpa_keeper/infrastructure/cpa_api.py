"""Provider-neutral CPA management API with explicit failure outcomes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import time

from .http import CurlCffiHttpTransport, HttpResponse, HttpTransport, request_with_retry


@dataclass(frozen=True, slots=True)
class CpaOperationResult:
    """Outcome of one CPA write; carries an error code, never response text."""

    ok: bool
    status_code: int | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CpaListResult:
    """CPA auth-file list outcome; rows are hidden from repr because of status text."""

    ok: bool
    auth_files: tuple[Mapping[str, object], ...] = field(default_factory=tuple, repr=False)
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CpaAuthFileResult:
    """Credential-body download outcome; the body never appears in repr output."""

    ok: bool
    payload: Mapping[str, object] | None = field(default=None, repr=False)
    error_code: str | None = None


def _failure_code(response: HttpResponse) -> str:
    if response.status_code is None:
        return response.error_code or "transport_error"
    return f"http_{response.status_code}"


class CpaApi:
    """CPA management client; retries 5xx and reports failures as stable codes."""

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
        return request_with_retry(
            self._transport,
            method,
            f"{self._endpoint}{path}",
            headers=self._headers,
            params=params,
            json_body=json_body,
            max_retries=self._max_retries,
            sleep=self._sleep,
        )

    def _write(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        ok_statuses: frozenset[int] = frozenset({200}),
    ) -> CpaOperationResult:
        response = self._request(method, path, params=params, json_body=json_body)
        ok = response.status_code in ok_statuses
        return CpaOperationResult(
            ok=ok,
            status_code=response.status_code,
            error_code=None if ok else _failure_code(response),
        )

    def list_auth_files(self) -> CpaListResult:
        response = self._request("GET", "/v0/management/auth-files")
        if response.status_code != 200:
            return CpaListResult(ok=False, error_code=_failure_code(response))
        payload = response.json_data
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            return CpaListResult(ok=False, error_code="invalid_list_response")
        if not all(
            isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and row["name"].strip()
            and isinstance(row.get("type"), str)
            and row["type"].strip()
            for row in files
        ):
            return CpaListResult(ok=False, error_code="invalid_list_entry")
        return CpaListResult(ok=True, auth_files=tuple(files))

    def get_auth_file(self, name: str) -> CpaAuthFileResult:
        response = self._request(
            "GET",
            "/v0/management/auth-files/download",
            params={"name": name},
        )
        if response.status_code != 200:
            return CpaAuthFileResult(ok=False, error_code=_failure_code(response))
        if not isinstance(response.json_data, dict):
            return CpaAuthFileResult(ok=False, error_code="invalid_auth_file_response")
        return CpaAuthFileResult(ok=True, payload=response.json_data)

    def delete_auth_file(self, name: str) -> CpaOperationResult:
        return self._write(
            "DELETE",
            "/v0/management/auth-files",
            params={"name": name},
            ok_statuses=frozenset({200, 204}),
        )

    def set_disabled(self, name: str, disabled: bool) -> CpaOperationResult:
        return self._write(
            "PATCH",
            "/v0/management/auth-files/status",
            json_body={"name": name, "disabled": disabled},
        )

    def upload_auth_file(self, name: str, payload: Mapping[str, object]) -> CpaOperationResult:
        return self._write(
            "POST",
            "/v0/management/auth-files",
            params={"name": name},
            json_body=payload,
        )
