"""Codex-owned OpenAI usage and OAuth refresh HTTP boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import time

from cpa_keeper.infrastructure.http import (
    CurlCffiHttpTransport,
    HttpTransport,
    request_with_retry,
)


@dataclass(frozen=True, slots=True)
class OpenAiResponse:
    """Status plus payload; the payload may hold credential material, so no repr."""

    status_code: int | None
    payload: Mapping[str, object] | None = field(default=None, repr=False)


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
        self._transport = transport or CurlCffiHttpTransport(
            proxy=proxy,
            timeout_seconds=timeout_seconds,
        )
        self._max_retries = max_retries
        self._sleep = sleep

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None,
    ) -> OpenAiResponse:
        response = request_with_retry(
            self._transport,
            method,
            url,
            headers=headers,
            json_body=json_body,
            max_retries=self._max_retries,
            sleep=self._sleep,
        )
        payload = response.json_data
        return OpenAiResponse(
            status_code=response.status_code,
            payload=payload if isinstance(payload, Mapping) else None,
        )

    def check_usage(self, access_token: str, account_id: str | None) -> OpenAiResponse:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "codex_cli_rs/0.76.0",
        }
        if account_id:
            headers["Chatgpt-Account-Id"] = account_id
        return self._request("GET", self.USAGE_URL, headers=headers, json_body=None)

    def refresh_token(self, refresh_token: str) -> OpenAiResponse:
        return self._request(
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
