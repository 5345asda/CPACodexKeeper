import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class DockerComposeTests(unittest.TestCase):
    def test_compose_mounts_behavior_file_and_injects_only_connection_secrets(self):
        compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("./config.toml:/app/config.toml:ro", compose_text)
        self.assertIn('command: ["daemon", "--config", "/app/config.toml"]', compose_text)
        self.assertIn("CPA_ENDPOINT:", compose_text)
        self.assertIn("CPA_TOKEN:", compose_text)
        self.assertIn("CPA_PROXY:", compose_text)

    def test_compose_preserves_live_network_topology(self):
        compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("host.docker.internal:host-gateway", compose_text)
        self.assertIn("networks:", compose_text)
        self.assertIn("shared:", compose_text)
        self.assertIn("external: true", compose_text)


class DockerfileTests(unittest.TestCase):
    def test_runtime_image_ships_the_package_and_cli_entrypoint(self):
        dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("COPY src/cpa_keeper ./src/cpa_keeper", dockerfile_text)
        self.assertIn('ENTRYPOINT ["cpa-keeper"]', dockerfile_text)
        self.assertIn('CMD ["daemon"]', dockerfile_text)
