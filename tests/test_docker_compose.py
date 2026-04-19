import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class DockerComposeTests(unittest.TestCase):
    def test_compose_exposes_runtime_toggles(self):
        compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("CPA_ENABLE_REFRESH:", compose_text)
        self.assertIn("CPA_ENABLE_REFRESH: ${CPA_ENABLE_REFRESH:-true}", compose_text)
        self.assertIn("CPA_WORKER_THREADS:", compose_text)
