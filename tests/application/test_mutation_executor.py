from __future__ import annotations

import unittest

from cpa_keeper.providers.codex.lifecycle_policies import LifecycleAction, LifecycleDecision
from cpa_keeper.providers.codex.mutation import CodexMutationExecutor
from cpa_keeper.providers.codex.refresher import RefreshUpload


class RecordingCpaApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.delete_ok = True

    def delete_auth_file(self, name: str) -> bool:
        self.calls.append(("delete", name))
        return self.delete_ok

    def set_disabled(self, name: str, disabled: bool) -> bool:
        self.calls.append(("set_disabled", (name, disabled)))
        return True

    def upload_auth_file(self, name: str, payload: object) -> bool:
        self.calls.append(("upload", name))
        return True


def _decision(action: LifecycleAction, *, resource_name: str = "redacted.json") -> LifecycleDecision:
    policy_id = {
        LifecycleAction.DELETE: "codex.inspection.http-401-402.delete",
        LifecycleAction.DISABLE: "codex.inspection.quota-state.reconcile",
        LifecycleAction.ENABLE: "codex.inspection.quota-state.reconcile",
        LifecycleAction.REFRESH_THEN_UPLOAD: "codex.inspection.disabled-expiring.refresh",
    }[action]
    return LifecycleDecision(
        resource_name=resource_name,
        policy_id=policy_id,
        action=action,
        reason_code="fixture_reason",
    )


class CodexMutationExecutorTests(unittest.TestCase):
    def test_executes_the_supplied_lifecycle_action(self) -> None:
        api = RecordingCpaApi()

        result = CodexMutationExecutor(api).execute(_decision(LifecycleAction.DISABLE))

        self.assertTrue(result.applied)
        self.assertIsNone(result.error_code)
        self.assertEqual(api.calls, [("set_disabled", ("redacted.json", True))])

    def test_reports_a_failed_cpa_mutation(self) -> None:
        api = RecordingCpaApi()
        api.delete_ok = False

        result = CodexMutationExecutor(api).execute(_decision(LifecycleAction.DELETE))

        self.assertFalse(result.applied)
        self.assertEqual(result.error_code, "cpa_mutation_failed")
        self.assertEqual(api.calls, [("delete", "redacted.json")])

    def test_refresh_upload_restores_the_disabled_state(self) -> None:
        api = RecordingCpaApi()
        decision = _decision(LifecycleAction.REFRESH_THEN_UPLOAD)

        result = CodexMutationExecutor(api).execute(
            decision,
            refresh_callback=lambda _decision: RefreshUpload(
                payload={"opaque": "replacement"},
                restore_disabled=True,
            ),
        )

        self.assertTrue(result.applied)
        self.assertEqual(
            api.calls,
            [
                ("upload", "redacted.json"),
                ("set_disabled", ("redacted.json", True)),
            ],
        )

