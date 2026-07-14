"""Codex-owned OpenAI usage and OAuth refresh HTTP boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import time
from types import MappingProxyType
from typing import Mapping

from cpa_keeper.infrastructure.http import CurlCffiHttpTransport, HttpTransport


@dataclass(frozen=True, slots=True)
class OpenAiResponse:
    """Safe status wrapper; JSON is intentionally hidden because it may be credential material."""

    status_code: int | None
    payload: Mapping[str, object] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.payload is not None:
            object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class OpenAiApi:
    """Provider-specific OpenAI endpoints; no CPA management calls belong here."""

    USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
    REFRESH_URL = "https://auth.openai.com/oauth/token"
    CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    REDIRECT_URI = "http://localhost:1455/auth/callback"

    def __init__(
        self,
        *,
        proxy: str | None,
        timeout_seconds: int,
        max_retries: int = 2,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self._transport = transport or CurlCffiHttpTransport(
            proxy=proxy,
            timeout_seconds=timeout_seconds,
        )
        self._max_retries = max_retries
        self._sleep = sleep

    @staticmethod
    def _response(response: object) -> OpenAiResponse:
        status_code = getattr(response, "status_code", None)
        payload = getattr(response, "json_data", None)
        return OpenAiResponse(
            status_code=status_code if type(status_code) is int else None,
            payload=payload if isinstance(payload, Mapping) else None,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None,
    ) -> object:
        for attempt in range(self._max_retries + 1):
            response = self._transport.request(
                method,
                url,
                headers=headers,
                params=None,
                json_body=json_body,
            )
            status_code = getattr(response, "status_code", None)
            if type(status_code) is int and status_code >= 500 and attempt < self._max_retries:
                self._sleep(1)
                continue
            return response
        raise AssertionError("retry loop must return a response")

    def check_usage(self, access_token: str, account_id: str | None) -> OpenAiResponse:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "codex_cli_rs/0.76.0",
        }
        if account_id:
            headers["Chatgpt-Account-Id"] = account_id
        return self._response(self._request("GET", self.USAGE_URL, headers=headers, json_body=None))

    def refresh_token(self, refresh_token: str) -> OpenAiResponse:
        return self._response(
            self._request(
                "POST",
                self.REFRESH_URL,
                headers={"Content-Type": "application/json"},
                json_body={
                    "redirect_uri": self.REDIRECT_URI,
                    "grant_type": "refresh_token",
                    "client_id": self.CLIENT_ID,
                    "refresh_token": refresh_token,
                },
            )
        )
