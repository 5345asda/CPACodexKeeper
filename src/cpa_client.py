import json
import time
from typing import Any

from curl_cffi import requests

from .models import RequestResult
from .utils import brief_response_text


class CPAClient:
    def __init__(self, base_url: str, token: str, *, proxy: str | None = None, timeout: int = 30, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs) -> RequestResult:
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=f"{self.base_url}{path}",
                    headers=self.headers,
                    proxies=self.proxies,
                    impersonate="chrome",
                    timeout=self.timeout,
                    **kwargs,
                )
                json_data = None
                try:
                    json_data = response.json()
                except (ValueError, TypeError):
                    pass
                if response.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(1)
                    continue
                return RequestResult(
                    status_code=response.status_code,
                    body=response.text,
                    brief=brief_response_text(response),
                    json_data=json_data,
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
        return RequestResult(status_code=None, error=last_error or "request failed")

    def list_auth_files(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/v0/management/auth-files")
        if result.status_code != 200 or not result.json_data:
            return []
        return result.json_data.get("files", [])

    def get_auth_file(self, name: str) -> dict[str, Any] | None:
        result = self._request("GET", "/v0/management/auth-files/download", params={"name": name})
        if result.status_code != 200 or not result.json_data:
            return None
        return result.json_data

    def delete_auth_file(self, name: str) -> bool:
        result = self._request("DELETE", "/v0/management/auth-files", params={"name": name})
        return result.status_code in (200, 204)

    def set_disabled(self, name: str, disabled: bool) -> bool:
        result = self._request("PATCH", "/v0/management/auth-files/status", json={"name": name, "disabled": disabled})
        return result.status_code == 200

    def get_usage_log(self, *, lookback_seconds: int) -> dict[str, Any] | None:
        result = self._request("GET", "/v0/management/usage", params={"lookback_seconds": lookback_seconds})
        if result.status_code != 200 or not result.json_data:
            return None
        return result.json_data

    def upload_auth_file(self, name: str, token_data: dict[str, Any]) -> bool:
        result = self._request(
            "POST",
            f"/v0/management/auth-files?name={name}",
            data=json.dumps(token_data, ensure_ascii=False),
        )
        return result.status_code == 200
