from __future__ import annotations

from pathlib import Path
import sys
import tomllib
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cpa_keeper.config.fast_scan import ConfigError, parse_config_data


CONFIG = """
[fast_scan]
interval_seconds = 60

[providers.codex]
enabled = true

[providers.codex.fast_scan]
enabled = true

[[providers.codex.fast_scan.rules]]
id = "usage-limit-disable"
enabled = true
action = "disable"
priority = 60
when = { all = [
  { field = "error.type", op = "eq", value = "usage_limit_reached" },
] }

[[providers.codex.fast_scan.rules]]
id = "invalidated-auth-delete"
enabled = true
action = "delete"
priority = 100
when = { all = [
  { field = "error.type", op = "eq", value = "authentication_error" },
  { field = "error.code", op = "eq", value = "auth_unavailable" },
  { any = [
    { field = "error.message", op = "contains", value = "invalidated", ignore_case = true },
    { field = "error.message", op = "contains", value = "revoked", ignore_case = true },
  ] },
] }

[providers.codex.inspection]
enabled = true
interval_seconds = 1800
workers = 8
usage_timeout_seconds = 15
quota_threshold_percent = 100
refresh_enabled = true
refresh_before_expiry_days = 3

[providers.xai]
enabled = true

[providers.xai.fast_scan]
enabled = true

[[providers.xai.fast_scan.rules]]
id = "access-denied-delete"
enabled = true
action = "delete"
priority = 100
when = { all = [
  { field = "error.message", op = "contains", value = "access denied", ignore_case = true },
] }

[[providers.xai.fast_scan.rules]]
id = "chat-permission-denied-delete"
enabled = true
action = "delete"
priority = 100
when = { all = [
  { field = "error.code", op = "eq", value = "chat-permission-denied" },
] }
"""


class FastScanConfigTests(unittest.TestCase):
    def test_documented_example_is_an_executable_target_config(self) -> None:
        example = Path(__file__).resolve().parents[2] / "docs" / "reference" / "config.example.toml"

        config = parse_config_data(tomllib.loads(example.read_text(encoding="utf-8")))

        self.assertEqual(config.fast_scan.interval_seconds, 60)
        self.assertIn("codex", config.providers)
        self.assertIn("xai", config.providers)

    def test_user_contract_loads_with_nested_fast_scan_and_inspection(self) -> None:
        config = parse_config_data(tomllib.loads(CONFIG))

        self.assertEqual(config.fast_scan.interval_seconds, 60)
        self.assertTrue(config.providers["codex"].enabled)
        self.assertEqual(
            [rule.id for rule in config.providers["codex"].fast_scan.rules],
            ["usage-limit-disable", "invalidated-auth-delete"],
        )
        inspection = config.providers["codex"].inspection
        self.assertIsNotNone(inspection)
        assert inspection is not None
        self.assertEqual((inspection.interval_seconds, inspection.workers), (1800, 8))
        self.assertEqual(
            config.providers["xai"].fast_scan.rules[1].action,
            "delete",
        )

    def test_dynamic_provider_can_define_fast_scan_without_python_policy(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["future"] = {
            "enabled": True,
            "fast_scan": {
                "enabled": True,
                "rules": [
                    {
                        "id": "future-disable",
                        "enabled": True,
                        "action": "disable",
                        "priority": 1,
                        "when": {
                            "all": [
                                {
                                    "field": "error.code",
                                    "op": "eq",
                                    "value": "temporary",
                                }
                            ]
                        },
                    }
                ],
            },
        }

        config = parse_config_data(data)

        self.assertEqual(config.providers["future"].fast_scan.rules[0].id, "future-disable")

    def test_only_codex_can_configure_deep_inspection(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["xai"]["inspection"] = {
            "enabled": True,
            "interval_seconds": 60,
            "workers": 1,
            "usage_timeout_seconds": 15,
            "quota_threshold_percent": 100,
            "refresh_enabled": True,
            "refresh_before_expiry_days": 3,
        }

        with self.assertRaises(ConfigError):
            parse_config_data(data)

    def test_invalid_rule_action_is_rejected_at_configuration_boundary(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["xai"]["fast_scan"]["rules"][0]["action"] = "enable"

        with self.assertRaises(ConfigError):
            parse_config_data(data)

    def test_duplicate_rule_id_is_rejected_per_provider(self) -> None:
        data = tomllib.loads(CONFIG)
        rules = data["providers"]["xai"]["fast_scan"]["rules"]
        rules[1]["id"] = rules[0]["id"]

        with self.assertRaises(ConfigError):
            parse_config_data(data)

    def test_unknown_error_operator_is_rejected_at_configuration_boundary(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["codex"]["fast_scan"]["rules"][0]["when"]["all"][0]["op"] = "in"

        with self.assertRaises(ConfigError):
            parse_config_data(data)

    def test_blank_rule_value_is_rejected_at_configuration_boundary(self) -> None:
        data = tomllib.loads(CONFIG)
        data["providers"]["xai"]["fast_scan"]["rules"][0]["when"]["all"][0]["value"] = " "

        with self.assertRaises(ConfigError):
            parse_config_data(data)

    def test_provider_and_rule_ids_are_safe_log_identifiers(self) -> None:
        invalid_provider = tomllib.loads(CONFIG)
        invalid_provider["providers"]["bad/provider"] = invalid_provider["providers"].pop("xai")
        with self.assertRaises(ConfigError):
            parse_config_data(invalid_provider)

        invalid_rule = tomllib.loads(CONFIG)
        invalid_rule["providers"]["xai"]["fast_scan"]["rules"][0]["id"] = "bad\nrule"
        with self.assertRaises(ConfigError):
            parse_config_data(invalid_rule)

    def test_validation_error_never_echoes_unknown_configuration_values(self) -> None:
        data = tomllib.loads(CONFIG)
        data["unexpected"] = "token-like-value-must-not-be-echoed"

        with self.assertRaises(ConfigError) as raised:
            parse_config_data(data)

        self.assertNotIn("token-like-value-must-not-be-echoed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
