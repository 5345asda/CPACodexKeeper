from __future__ import annotations

from pathlib import Path
import sys
import tomllib
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cpa_keeper.application.fast_scan import FastScanService
from cpa_keeper.application.mutation_coordinator import AuthFileMutationCoordinator
from cpa_keeper.application.results import RunStatus
from cpa_keeper.config.fast_scan import parse_config_data
from cpa_keeper.infrastructure.cpa_api import CpaListResult, CpaOperationResult
from config.test_fast_scan_config import CONFIG


class FakeCpaApi:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows
        self.list_calls = 0
        self.calls: list[tuple[str, object]] = []
        self.delete_result = CpaOperationResult(ok=True, status_code=204)

    def list_auth_files(self) -> CpaListResult:
        self.list_calls += 1
        return CpaListResult(ok=True, auth_files=self.rows)

    def set_disabled(self, name: str, disabled: bool) -> CpaOperationResult:
        self.calls.append(("disable", (name, disabled)))
        return CpaOperationResult(ok=True, status_code=200)

    def delete_auth_file(self, name: str) -> CpaOperationResult:
        self.calls.append(("delete", name))
        return self.delete_result


def _rows() -> tuple[dict[str, object], ...]:
    return (
        {
            "name": "codex-usage.json",
            "type": "codex",
            "disabled": False,
            "status": "error",
            "status_message": '{"error":{"type":"usage_limit_reached"}}',
        },
        {
            "name": "codex-revoked.json",
            "type": "codex",
            "disabled": False,
            "status": "error",
            "status_message": (
                '{"type":"authentication_error","code":"auth_unavailable",'
                '"error":"credential revoked"}'
            ),
        },
        {
            "name": "xai-access.json",
            "type": "xai",
            "disabled": False,
            "status": "error",
            "status_message": '{"error":"Access denied for this account"}',
        },
        {
            "name": "xai-chat.json",
            "type": "xai",
            "disabled": False,
            "status": "error",
            "status_message": '{"code":"chat-permission-denied","error":"unused"}',
        },
        {
            "name": "codex-active.json",
            "type": "codex",
            "disabled": False,
            "status": "active",
            "status_message": '{"error":{"type":"usage_limit_reached"}}',
        },
    )


class FastScanServiceTests(unittest.TestCase):
    def _service(self, cpa_api: FakeCpaApi) -> FastScanService:
        return FastScanService(
            cpa_api,
            parse_config_data(tomllib.loads(CONFIG)),
            mutation_coordinator=AuthFileMutationCoordinator(),
        )

    def test_one_list_snapshot_handles_all_enabled_provider_rules(self) -> None:
        cpa_api = FakeCpaApi(_rows())

        result = self._service(cpa_api).scan()

        self.assertEqual(cpa_api.list_calls, 1)
        self.assertEqual(
            cpa_api.calls,
            [
                ("disable", ("codex-usage.json", True)),
                ("delete", "codex-revoked.json"),
                ("delete", "xai-access.json"),
                ("delete", "xai-chat.json"),
            ],
        )
        self.assertEqual(result.status, RunStatus.SUCCESS)
        self.assertEqual(
            [(report.provider_id, report.scanned, report.matched, report.applied) for report in result.reports],
            [("codex", 2, 2, 2), ("xai", 2, 2, 2)],
        )
        self.assertEqual(
            result.handled_resource_names,
            frozenset(
                {
                    "codex-usage.json",
                    "codex-revoked.json",
                    "xai-access.json",
                    "xai-chat.json",
                }
            ),
        )

    def test_provider_fast_scan_switch_does_not_run_its_rules(self) -> None:
        cpa_api = FakeCpaApi(_rows())
        data = tomllib.loads(CONFIG)
        data["providers"]["codex"]["fast_scan"]["enabled"] = False

        result = FastScanService(
            cpa_api,
            parse_config_data(data),
            mutation_coordinator=AuthFileMutationCoordinator(),
        ).scan()

        self.assertEqual(cpa_api.list_calls, 1)
        self.assertEqual(
            cpa_api.calls,
            [("delete", "xai-access.json"), ("delete", "xai-chat.json")],
        )
        self.assertEqual(result.reports[0].provider_id, "codex")
        self.assertEqual(result.reports[0].scanned, 0)

    def test_provider_switch_stops_fast_scan_actions(self) -> None:
        cpa_api = FakeCpaApi(_rows())
        data = tomllib.loads(CONFIG)
        data["providers"]["xai"]["enabled"] = False

        result = FastScanService(
            cpa_api,
            parse_config_data(data),
            mutation_coordinator=AuthFileMutationCoordinator(),
        ).scan()

        self.assertEqual(
            cpa_api.calls,
            [
                ("disable", ("codex-usage.json", True)),
                ("delete", "codex-revoked.json"),
            ],
        )
        self.assertEqual([(report.provider_id, report.scanned) for report in result.reports], [("codex", 2)])

    def test_already_disabled_fast_scan_match_remains_available_to_codex_inspection(self) -> None:
        cpa_api = FakeCpaApi(
            (
                {
                    "name": "codex-disabled.json",
                    "type": "codex",
                    "disabled": True,
                    "status": "error",
                    "status_message": '{"error":{"type":"usage_limit_reached"}}',
                },
            )
        )

        result = self._service(cpa_api).scan()

        self.assertEqual(cpa_api.calls, [])
        self.assertNotIn("codex-disabled.json", result.handled_resource_names)
        self.assertEqual(result.reports[0].skipped, 1)

    def test_action_failure_logs_safe_context_without_raw_message_or_name(self) -> None:
        cpa_api = FakeCpaApi(_rows())
        cpa_api.delete_result = CpaOperationResult(
            ok=False,
            status_code=503,
            error_code="http_503",
        )

        with self.assertLogs("cpa_keeper.application.fast_scan", level="INFO") as logs:
            result = self._service(cpa_api).scan()

        output = "\n".join(logs.output)
        self.assertEqual(result.status, RunStatus.PARTIAL_FAILURE)
        self.assertIn("event=fast_scan_action", output)
        self.assertIn("error_code=http_503", output)
        self.assertNotIn("credential revoked", output)
        self.assertNotIn("codex-revoked.json", output)
        self.assertNotIn("codex-revoked.json", result.handled_resource_names)

    def test_result_carries_the_resource_epochs_for_its_list_snapshot(self) -> None:
        cpa_api = FakeCpaApi(_rows())

        result = self._service(cpa_api).scan()

        self.assertEqual(
            result.resource_epochs,
            {
                "codex-usage.json": 1,
                "codex-revoked.json": 1,
                "xai-access.json": 1,
                "xai-chat.json": 1,
                "codex-active.json": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
