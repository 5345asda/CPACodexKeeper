"""HTTP transport that keeps response bodies out of logs and reprs."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One transport response; the payload never appears in repr output."""

    status_code: int | None
    json_data: object | None = field(default=None, repr=False)
    error_code: str | None = None


class HttpTransport(Protocol):
    """Transport surface shared by production HTTP and fake test doubles."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        json_body: Mapping[str, object] | None,
    ) -> HttpResponse: ...


class CurlCffiHttpTransport:
    """curl-cffi transport; failures collapse to a safe transport_error code."""

    def __init__(self, *, proxy: str | None = None, timeout_seconds: int = 30) -> None:
        self._proxies = {"http": proxy, "https": proxy} if proxy else None
        self._timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        json_body: Mapping[str, object] | None,
    ) -> HttpResponse:
        from curl_cffi import requests

        try:
            response = requests.request(
                method,
                url,
                headers=dict(headers),
                params=dict(params) if params else None,
                json=dict(json_body) if json_body else None,
                proxies=self._proxies,
                impersonate="chrome",
                timeout=self._timeout_seconds,
            )
        except Exception:
            return HttpResponse(status_code=None, error_code="transport_error")

        try:
            json_data = response.json()
        except (TypeError, ValueError):
            json_data = None
        return HttpResponse(status_code=response.status_code, json_data=json_data)


def request_with_retry(
    transport: HttpTransport,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None = None,
    json_body: Mapping[str, object] | None = None,
    max_retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> HttpResponse:
    """Retry 5xx responses with a one-second pause; anything else returns immediately."""
    response = transport.request(method, url, headers=headers, params=params, json_body=json_body)
    for _ in range(max_retries):
        if response.status_code is None or response.status_code < 500:
            break
        sleep(1)
        response = transport.request(method, url, headers=headers, params=params, json_body=json_body)
    return response
