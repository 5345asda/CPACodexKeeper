from __future__ import annotations

import unittest

from cpa_keeper.infrastructure.cpa_api import CpaApi
from cpa_keeper.infrastructure.http import HttpResponse


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> HttpResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class CpaApiContractTests(unittest.TestCase):
    def test_list_auth_files_preserves_explicit_upstream_failure(self) -> None:
        transport = FakeTransport([HttpResponse(status_code=503, json_data={"ignored": "body"})])
        api = CpaApi(
            endpoint="https://cpa.example.test/",
            token="test-token",
            transport=transport,
            max_retries=0,
        )

        result = api.list_auth_files()

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "http_503")
        self.assertEqual(result.auth_files, ())
        self.assertEqual(transport.calls[0]["method"], "GET")
        self.assertEqual(
            transport.calls[0]["url"],
            "https://cpa.example.test/v0/management/auth-files",
        )

    def test_list_and_detail_requests_validate_cpa_response_shape(self) -> None:
        transport = FakeTransport(
            [
                HttpResponse(
                    status_code=200,
                    json_data={
                        "files": [
                            {
                                "name": "redacted-xai.json",
                                "type": "xai",
                                "disabled": False,
                                "status": "error",
                                "status_message": '{"error":"Access denied."}',
                            }
                        ]
                    },
                ),
                HttpResponse(status_code=200, json_data={"access_token": "opaque"}),
            ]
        )
        api = CpaApi(
            endpoint="https://cpa.example.test",
            token="test-token",
            transport=transport,
            max_retries=0,
        )

        listed = api.list_auth_files()
        detail = api.get_auth_file("redacted-xai.json")

        self.assertTrue(listed.ok)
        self.assertEqual(listed.auth_files[0]["type"], "xai")
        self.assertTrue(detail.ok)
        self.assertEqual(detail.payload, {"access_token": "opaque"})
        self.assertEqual(
            transport.calls[1]["url"],
            "https://cpa.example.test/v0/management/auth-files/download",
        )
        self.assertEqual(transport.calls[1]["params"], {"name": "redacted-xai.json"})

    def test_delete_status_patch_and_upload_use_the_cpa_management_contract(self) -> None:
        transport = FakeTransport(
            [
                HttpResponse(status_code=204),
                HttpResponse(status_code=200),
                HttpResponse(status_code=200),
            ]
        )
        api = CpaApi(
            endpoint="https://cpa.example.test",
            token="test-token",
            transport=transport,
            max_retries=0,
        )

        self.assertTrue(api.delete_auth_file("redacted.json").ok)
        self.assertTrue(api.set_disabled("redacted.json", True).ok)
        self.assertTrue(api.upload_auth_file("redacted.json", {"opaque": "replacement"}).ok)

        self.assertEqual(
            transport.calls,
            [
                {
                    "method": "DELETE",
                    "url": "https://cpa.example.test/v0/management/auth-files",
                    "headers": {
                        "Authorization": "Bearer test-token",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    "params": {"name": "redacted.json"},
                    "json_body": None,
                },
                {
                    "method": "PATCH",
                    "url": "https://cpa.example.test/v0/management/auth-files/status",
                    "headers": {
                        "Authorization": "Bearer test-token",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    "params": None,
                    "json_body": {"name": "redacted.json", "disabled": True},
                },
                {
                    "method": "POST",
                    "url": "https://cpa.example.test/v0/management/auth-files",
                    "headers": {
                        "Authorization": "Bearer test-token",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    "params": {"name": "redacted.json"},
                    "json_body": {"opaque": "replacement"},
                },
            ],
        )

