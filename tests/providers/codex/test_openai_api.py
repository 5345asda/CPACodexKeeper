from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from cpa_keeper.infrastructure.http import HttpResponse
from cpa_keeper.providers.codex.openai_api import OpenAiApi


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: object) -> HttpResponse:
        del kwargs
        self.calls.append((method, url))
        return self.responses.pop(0)


class OpenAiApiContractTests(unittest.TestCase):
    def test_usage_retries_transient_5xx_without_retaining_response_body(self) -> None:
        transport = FakeTransport(
            [
                HttpResponse(status_code=503, json_data={"detail": "temporary-body"}),
                HttpResponse(
                    status_code=200,
                    json_data={"rate_limit": {"primary_window": {"used_percent": 0}}},
                ),
            ]
        )
        sleeps: list[float] = []
        api = OpenAiApi(
            proxy=None,
            timeout_seconds=10,
            max_retries=1,
            transport=transport,
            sleep=sleeps.append,
        )

        response = api.check_usage("opaque-access", None)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(sleeps, [1])
        self.assertNotIn("temporary-body", repr(response))


if __name__ == "__main__":
    unittest.main()
