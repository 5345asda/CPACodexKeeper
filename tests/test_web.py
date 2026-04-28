import json
import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    HAS_FASTAPI = False

if HAS_FASTAPI:
    from src.maintainer import CPACodexKeeper
    from src.reports import TokenReport
    from src.settings import load_runtime_settings
    from src.web import create_app


@unittest.skipUnless(HAS_FASTAPI, "fastapi not installed")
class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmpdir = Path(self.tmp.name)
        env_file = tmpdir / ".env"
        env_file.write_text("CPA_ENDPOINT=https://example.com\nCPA_TOKEN=secret\n", encoding="utf-8")
        overrides = tmpdir / "runtime.json"
        with patch.dict(os.environ, {}, clear=True):
            self.runtime = load_runtime_settings(env_file=env_file, runtime_overrides_file=overrides)
        self.maintainer = CPACodexKeeper(self.runtime, dry_run=False)
        # Stub out the CPA client so no real HTTP happens.
        self.maintainer.cpa_client = Mock()
        self.maintainer.cpa_client.set_disabled = Mock(return_value=True)
        self.maintainer.cpa_client.delete_auth_file = Mock(return_value=True)
        self.maintainer.cpa_client.get_auth_file = Mock(return_value=None)
        self.maintainer.cpa_client.upload_auth_file = Mock(return_value=True)
        self.maintainer.cpa_client.list_auth_files = Mock(return_value=[])
        # Pre-load a report so /api/state has something to return.
        self.maintainer.reports.upsert(TokenReport(
            name="codex-alpha",
            email="alpha@example.com",
            disabled=False,
            plan_type="team",
            primary_used_percent=12,
            secondary_used_percent=80,
            last_outcome="alive",
            last_actions=["INFO: 获取详情..."],
            last_log_lines=["[1/1] codex-alpha", "    [*] 获取详情..."],
            checked_at=1700000000.0,
        ))
        self.app = create_app(self.maintainer, self.runtime)
        self.client = TestClient(self.app)

    def _enable_write_auth(self):
        self.runtime.update({"ui_token": "shh"})
        return {"Authorization": "Bearer shh"}

    def test_state_returns_redacted_settings_and_reports(self):
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("stats", body)
        self.assertIn("reports", body)
        self.assertEqual(body["settings"]["cpa_token"], "***")
        self.assertEqual(body["settings"]["cpa_endpoint"], "https://example.com")
        names = [r["name"] for r in body["reports"]]
        self.assertIn("codex-alpha", names)
        self.assertIn("policy_fields", body)
        self.assertIn("quota_threshold", body["policy_fields"])
        self.assertFalse(body["write_auth_configured"])

    def test_get_config_returns_sources(self):
        res = self.client.get("/api/config")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("sources", body)
        self.assertEqual(body["sources"]["cpa_token"], "env")

    def test_put_config_updates_policy_field(self):
        headers = self._enable_write_auth()
        res = self.client.put("/api/config", json={"quota_threshold": 42}, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.runtime.snapshot().quota_threshold, 42)
        # Restart-required is empty for policy fields.
        self.assertEqual(res.json()["restart_required_fields"], [])

    def test_put_config_flags_transport_field_as_restart_required(self):
        headers = self._enable_write_auth()
        res = self.client.put("/api/config", json={"cpa_endpoint": "https://other.example.com"}, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("cpa_endpoint", res.json()["restart_required_fields"])

    def test_put_config_rejects_invalid_value(self):
        headers = self._enable_write_auth()
        res = self.client.put("/api/config", json={"quota_threshold": 999}, headers=headers)
        self.assertEqual(res.status_code, 400)

    def test_patch_token_disable_calls_cpa(self):
        headers = self._enable_write_auth()
        res = self.client.patch("/api/tokens/codex-alpha", json={"disabled": True}, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.maintainer.cpa_client.set_disabled.assert_called_once_with("codex-alpha", True)
        report = self.maintainer.reports.get("codex-alpha")
        self.assertTrue(report.disabled)
        self.assertEqual(report.last_outcome, "disabled")

    def test_patch_token_requires_disabled_field(self):
        headers = self._enable_write_auth()
        res = self.client.patch("/api/tokens/codex-alpha", json={}, headers=headers)
        self.assertEqual(res.status_code, 400)

    def test_patch_token_rejects_non_boolean_disabled(self):
        headers = self._enable_write_auth()
        res = self.client.patch("/api/tokens/codex-alpha", json={"disabled": "false"}, headers=headers)
        self.assertEqual(res.status_code, 400)

    def test_patch_token_respects_dry_run(self):
        headers = self._enable_write_auth()
        self.maintainer.dry_run = True
        res = self.client.patch("/api/tokens/codex-alpha", json={"disabled": True}, headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["dry_run"])
        self.maintainer.cpa_client.set_disabled.assert_not_called()
        report = self.maintainer.reports.get("codex-alpha")
        self.assertFalse(report.disabled)
        self.assertEqual(report.last_outcome, "skipped")

    def test_delete_token_removes_from_registry(self):
        headers = self._enable_write_auth()
        res = self.client.delete("/api/tokens/codex-alpha", headers=headers)
        self.assertEqual(res.status_code, 200)
        self.maintainer.cpa_client.delete_auth_file.assert_called_once_with("codex-alpha")
        self.assertIsNone(self.maintainer.reports.get("codex-alpha"))

    def test_delete_token_respects_dry_run(self):
        headers = self._enable_write_auth()
        self.maintainer.dry_run = True
        res = self.client.delete("/api/tokens/codex-alpha", headers=headers)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["dry_run"])
        self.maintainer.cpa_client.delete_auth_file.assert_not_called()
        report = self.maintainer.reports.get("codex-alpha")
        self.assertIsNotNone(report)
        self.assertEqual(report.last_outcome, "skipped")

    def test_refresh_token_respects_dry_run(self):
        headers = self._enable_write_auth()
        self.maintainer.dry_run = True
        self.maintainer.cpa_client.get_auth_file.return_value = {
            "access_token": "old",
            "refresh_token": "rt",
        }
        self.maintainer.try_refresh = Mock(return_value=(True, {"access_token": "new"}, "刷新成功"))

        res = self.client.post("/api/tokens/codex-alpha/refresh", headers=headers)

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["dry_run"])
        self.maintainer.try_refresh.assert_not_called()
        self.maintainer.cpa_client.upload_auth_file.assert_not_called()
        report = self.maintainer.reports.get("codex-alpha")
        self.assertIsNotNone(report)
        self.assertEqual(report.last_outcome, "skipped")

    def test_state_reports_scan_in_progress_from_keeper_lock(self):
        acquired = self.maintainer._run_lock.acquire(blocking=False)
        self.addCleanup(lambda: self.maintainer._run_lock.release() if acquired else None)
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["scan_in_progress"])

    def test_scan_all_rejects_when_keeper_already_running(self):
        headers = self._enable_write_auth()
        acquired = self.maintainer._run_lock.acquire(blocking=False)
        self.addCleanup(lambda: self.maintainer._run_lock.release() if acquired else None)
        res = self.client.post("/api/scan", headers=headers)
        self.assertEqual(res.status_code, 409)

    def test_manual_mutations_reject_when_keeper_already_running(self):
        headers = self._enable_write_auth()
        acquired = self.maintainer._run_lock.acquire(blocking=False)
        self.addCleanup(lambda: self.maintainer._run_lock.release() if acquired else None)

        scan_one_res = self.client.post("/api/scan/codex-alpha", headers=headers)
        patch_res = self.client.patch("/api/tokens/codex-alpha", json={"disabled": True}, headers=headers)
        delete_res = self.client.delete("/api/tokens/codex-alpha", headers=headers)
        refresh_res = self.client.post("/api/tokens/codex-alpha/refresh", headers=headers)

        self.assertEqual(scan_one_res.status_code, 409)
        self.assertEqual(patch_res.status_code, 409)
        self.assertEqual(delete_res.status_code, 409)
        self.assertEqual(refresh_res.status_code, 409)
        self.maintainer.cpa_client.list_auth_files.assert_not_called()
        self.maintainer.cpa_client.set_disabled.assert_not_called()
        self.maintainer.cpa_client.delete_auth_file.assert_not_called()
        self.maintainer.cpa_client.get_auth_file.assert_not_called()

    def test_scan_one_returns_404_for_unknown_token(self):
        headers = self._enable_write_auth()
        self.maintainer.cpa_client.list_auth_files.return_value = []
        res = self.client.post("/api/scan/no-such-token", headers=headers)
        self.assertEqual(res.status_code, 404)

    def test_write_operations_require_configured_ui_token(self):
        responses = [
            self.client.post("/api/scan"),
            self.client.post("/api/scan/codex-alpha"),
            self.client.patch("/api/tokens/codex-alpha", json={"disabled": True}),
            self.client.delete("/api/tokens/codex-alpha"),
            self.client.post("/api/tokens/codex-alpha/refresh"),
            self.client.put("/api/config", json={"quota_threshold": 42}),
        ]

        self.assertTrue(all(res.status_code == 403 for res in responses))
        self.maintainer.cpa_client.list_auth_files.assert_not_called()
        self.maintainer.cpa_client.set_disabled.assert_not_called()
        self.maintainer.cpa_client.delete_auth_file.assert_not_called()
        self.maintainer.cpa_client.get_auth_file.assert_not_called()
        self.assertEqual(self.runtime.snapshot().quota_threshold, 100)

    def test_authorization_required_when_token_set(self):
        self.runtime.update({"ui_token": "shh"})
        res = self.client.get("/api/state")
        self.assertEqual(res.status_code, 401)
        res2 = self.client.get("/api/state", headers={"Authorization": "Bearer shh"})
        self.assertEqual(res2.status_code, 200)

    def test_authorization_rejects_non_ascii_bearer_as_unauthorized(self):
        self.runtime.update({"ui_token": "shh"})
        res = self.client.get("/api/state", headers=[(b"authorization", b"Bearer \xff")])
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
