import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class DockerComposeTests(unittest.TestCase):
    def test_compose_exposes_runtime_toggles(self):
        compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("CPA_ENABLE_REFRESH:", compose_text)
        self.assertIn("CPA_ENABLE_REFRESH: ${CPA_ENABLE_REFRESH:-true}", compose_text)
        self.assertIn("CPA_WORKER_THREADS:", compose_text)
        self.assertIn("CPA_ERROR_SWEEP_ENABLED: ${CPA_ERROR_SWEEP_ENABLED:-true}", compose_text)
        self.assertIn("CPA_ERROR_SWEEP_INTERVAL: ${CPA_ERROR_SWEEP_INTERVAL:-60}", compose_text)
        self.assertIn("CPA_ERROR_DISABLE_TYPES: ${CPA_ERROR_DISABLE_TYPES:-usage_limit_reached}", compose_text)
        self.assertIn("CPA_ERROR_DELETE_TYPES: ${CPA_ERROR_DELETE_TYPES:-authentication_error}", compose_text)
        self.assertIn("CPA_ERROR_DELETE_CODES: ${CPA_ERROR_DELETE_CODES:-auth_unavailable}", compose_text)
        self.assertIn("CPA_ERROR_DELETE_MESSAGE_KEYWORDS: ${CPA_ERROR_DELETE_MESSAGE_KEYWORDS:-invalidated}", compose_text)
