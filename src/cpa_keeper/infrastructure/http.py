"""Small HTTP transport boundary that keeps raw response bodies out of reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One transport response with payload intentionally hidden from repr output."""

    status_code: int | None
    json_data: object | None = field(default=None, repr=False)
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status_code is not None and (
            type(self.status_code) is not int or self.status_code < 100 or self.status_code > 599
        ):
            raise ValueError("status_code must be an HTTP status or None")
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code
        ):
            raise ValueError("error_code must be a non-empty string when set")


class HttpTransport(Protocol):
    """Minimal transport surface used by CPA infrastructure and fake tests."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        json_body: Mapping[str, object] | None,
    ) -> HttpResponse:
        """Issue one HTTP request without logging headers or response bodies."""


class CurlCffiHttpTransport:
    """Production curl-cffi transport with a deliberately narrow safe error surface."""

    def __init__(self, *, proxy: str | None = None, timeout_seconds: int = 30) -> None:
        self._proxy = proxy
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
        try:
            from curl_cffi import requests

            response = requests.request(
                method,
                url,
                headers=dict(headers),
                params=dict(params) if params is not None else None,
                json=dict(json_body) if json_body is not None else None,
                proxies=(
                    {"http": self._proxy, "https": self._proxy}
                    if self._proxy is not None
                    else None
                ),
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
