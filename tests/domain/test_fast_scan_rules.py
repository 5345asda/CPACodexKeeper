from __future__ import annotations

import tomllib
import unittest

from cpa_keeper.application.fast_scan import normalize_error, select_rule
from cpa_keeper.config.fast_scan import parse_config_data
from config.test_fast_scan_config import CONFIG


class FastScanRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = parse_config_data(tomllib.loads(CONFIG))

    def test_nested_and_flat_errors_project_to_the_same_fields(self) -> None:
        nested = normalize_error(
            {
                "status_message": (
                    '{"error":{"type":"authentication_error",'
                    '"code":"auth_unavailable","message":"token revoked"}}'
                )
            }
        )
        flat = normalize_error(
            {
                "status_message": (
                    '{"type":"authentication_error","code":"auth_unavailable",'
                    '"error":"token revoked"}'
                )
            }
        )

        self.assertEqual(nested, flat)
        self.assertEqual(
            nested,
            {
                "error.type": "authentication_error",
                "error.code": "auth_unavailable",
                "error.message": "token revoked",
            },
        )

    def test_codex_delete_rule_matches_revoked_case_insensitively(self) -> None:
        rules = self.config.providers["codex"].fast_scan.rules

        selected = select_rule(
            rules,
            {
                "error.type": "authentication_error",
                "error.code": "auth_unavailable",
                "error.message": "Credential REVOKED by upstream",
            },
        )

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual((selected.id, selected.action), ("invalidated-auth-delete", "delete"))

    def test_xai_rules_match_error_message_and_error_code(self) -> None:
        rules = self.config.providers["xai"].fast_scan.rules

        message_rule = select_rule(
            rules,
            {
                "error.type": None,
                "error.code": None,
                "error.message": "Access Denied by xAI",
            },
        )
        code_rule = select_rule(
            rules,
            {
                "error.type": None,
                "error.code": "chat-permission-denied",
                "error.message": "different error",
            },
        )

        self.assertEqual(message_rule.id if message_rule else None, "access-denied-delete")
        self.assertEqual(code_rule.id if code_rule else None, "chat-permission-denied-delete")

    def test_higher_priority_rule_wins_and_disabled_rule_is_ignored(self) -> None:
        data = tomllib.loads(CONFIG)
        rules = data["providers"]["codex"]["fast_scan"]["rules"]
        rules[1]["enabled"] = False
        rules.append(
            {
                "id": "lower-priority-disable",
                "enabled": True,
                "action": "disable",
                "priority": 10,
                "when": {
                    "all": [
                        {
                            "field": "error.type",
                            "op": "eq",
                            "value": "authentication_error",
                        }
                    ]
                },
            }
        )
        parsed_rules = parse_config_data(data).providers["codex"].fast_scan.rules

        selected = select_rule(
            parsed_rules,
            {
                "error.type": "authentication_error",
                "error.code": "auth_unavailable",
                "error.message": "invalidated",
            },
        )

        self.assertEqual(selected.id if selected else None, "lower-priority-disable")

