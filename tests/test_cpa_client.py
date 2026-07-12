import pathlib
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.cpa_client import CPAClient
from src.models import RequestResult


class CPAClientTests(unittest.TestCase):
    def test_list_auth_files_with_error_returns_files_for_valid_response(self):
        client = CPAClient("https://example.com", "secret")
        expected_files = [{"name": "xai-permission-denied.json", "type": "xai"}]
        client._request = Mock(
            return_value=RequestResult(status_code=200, json_data={"files": expected_files})
        )

        files, error = client.list_auth_files_with_error()

        self.assertEqual(files, expected_files)
        self.assertIsNone(error)
        client._request.assert_called_once_with("GET", "/v0/management/auth-files")

    def test_list_auth_files_with_error_returns_safe_category_for_invalid_responses(self):
        client = CPAClient("https://example.com", "secret")
        cases = (
            (
                "non-200 status",
                RequestResult(
                    status_code=503,
                    body="secret response body must not be returned",
                    json_data={"files": []},
                ),
                "http_status=503",
            ),
            (
                "transport error",
                RequestResult(status_code=None, error="authorization header must not be returned"),
                "request_error",
            ),
            ("missing JSON", RequestResult(status_code=200, json_data=None), "invalid_json"),
            ("non-dict JSON", RequestResult(status_code=200, json_data=[]), "invalid_json"),
            ("missing files", RequestResult(status_code=200, json_data={}), "missing_files"),
            (
                "non-list files",
                RequestResult(status_code=200, json_data={"files": {}}),
                "invalid_files",
            ),
            (
                "non-dict file entry",
                RequestResult(
                    status_code=200,
                    json_data={"files": [{"name": "valid-xai.json", "type": "xai"}, None]},
                ),
                "invalid_file_entry",
            ),
            (
                "empty dictionary file entry",
                RequestResult(
                    status_code=200,
                    json_data={"files": [{"name": "valid-xai.json", "type": "xai"}, {}]},
                ),
                "invalid_file_entry",
            ),
            (
                "non-string name",
                RequestResult(status_code=200, json_data={"files": [{"name": 1, "type": "xai"}]}),
                "invalid_file_entry",
            ),
            (
                "missing type",
                RequestResult(status_code=200, json_data={"files": [{"name": "valid-xai.json"}]}),
                "invalid_file_entry",
            ),
            (
                "empty type",
                RequestResult(status_code=200, json_data={"files": [{"name": "valid-xai.json", "type": ""}]}),
                "invalid_file_entry",
            ),
        )

        for label, response, expected_error in cases:
            with self.subTest(label=label):
                client._request = Mock(return_value=response)

                files, error = client.list_auth_files_with_error()

                self.assertEqual(files, [])
                self.assertEqual(error, expected_error)
                self.assertNotIn("secret", error)
                self.assertNotIn("authorization", error)
                client._request.assert_called_once_with("GET", "/v0/management/auth-files")

    def test_list_auth_files_discards_structured_error_for_legacy_callers(self):
        client = CPAClient("https://example.com", "secret")
        client.list_auth_files_with_error = Mock(return_value=([], "request_error"))

        files = client.list_auth_files()

        self.assertEqual(files, [])
        client.list_auth_files_with_error.assert_called_once_with()

    def test_list_auth_files_returns_empty_for_invalid_file_entries(self):
        client = CPAClient("https://example.com", "secret")
        invalid_files_cases = (
            ("mixed valid and empty dictionary", [{"name": "valid-xai.json", "type": "xai"}, {}]),
            ("non-string name", [{"name": 1, "type": "xai"}]),
            ("missing type", [{"name": "valid-xai.json"}]),
            ("empty type", [{"name": "valid-xai.json", "type": ""}]),
        )

        for label, invalid_files in invalid_files_cases:
            with self.subTest(label=label):
                client._request = Mock(return_value=RequestResult(
                    status_code=200,
                    json_data={"files": invalid_files},
                ))

                files = client.list_auth_files()

                self.assertEqual(files, [])
                client._request.assert_called_once_with("GET", "/v0/management/auth-files")

    def test_upload_auth_file_passes_name_via_params(self):
        client = CPAClient("https://example.com", "secret")
        client._request = Mock(return_value=Mock(status_code=200))

        token_data = {
            "email": "jamessnyder20000630+89730080@outlook.com",
            "access_token": "token",
        }

        ok = client.upload_auth_file("jamessnyder20000630+89730080@outlook.com.json", token_data)

        self.assertTrue(ok)
        client._request.assert_called_once_with(
            "POST",
            "/v0/management/auth-files",
            params={"name": "jamessnyder20000630+89730080@outlook.com.json"},
            data='{"email": "jamessnyder20000630+89730080@outlook.com", "access_token": "token"}',
        )


if __name__ == "__main__":
    unittest.main()
