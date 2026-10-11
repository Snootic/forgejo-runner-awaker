import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from awaker.config import Settings
from awaker.execution import runner_script
from awaker.images import list_environments, runner_labels


class ImageTests(unittest.TestCase):
    def test_registry_tags_map_to_labels(self) -> None:
        client = Mock()
        client.images.list.return_value = [
            SimpleNamespace(
                tags=["registry.example:5000/team/python-runner:latest", "node:24"]
            )
        ]
        self.assertEqual(
            list_environments(client)["python"],
            "docker://registry.example:5000/team/python-runner:latest",
        )
        self.assertNotIn("ubuntu-latest", list_environments(client))

    def test_conflicting_tags_require_explicit_mapping(self) -> None:
        client = Mock()
        client.images.list.return_value = [
            SimpleNamespace(tags=["team/python-runner:3", "other/python-runner:4"])
        ]
        with self.assertRaises(ValueError):
            list_environments(client)
        self.assertEqual(
            list_environments(client, {"python": "team/python-runner:3"}),
            {"python": "docker://team/python-runner:3"},
        )

    def test_labels_must_all_be_supported_by_the_same_image(self) -> None:
        images = {
            "ubuntu-latest": "docker://node:lts",
            "linux": "docker://node:lts",
            "python": "docker://python:3",
        }
        self.assertEqual(
            runner_labels(["ubuntu-latest", "linux"], images),
            ["ubuntu-latest:docker://node:lts", "linux:docker://node:lts"],
        )
        for labels in ([], ["ubuntu-latest", "arm64"], ["ubuntu-latest", "python"]):
            self.assertIsNone(runner_labels(labels, images))

    def test_script_quotes_arguments_and_binds_the_job_attempt(self) -> None:
        settings = Settings(
            url="https://forgejo.example", repos=("owner/repo",), api_token="fake"
        )
        script = runner_script(
            settings,
            ["ubuntu-latest:docker://node:lts"],
            "uuid",
            "attempt'; touch /tmp/x",
        )
        self.assertIn("--handle 'attempt'", script)
        self.assertIn("unset RUNNER_TOKEN", script)
        self.assertIn('docker_host: "-"', script)
        self.assertNotIn("forgejo-runner register", script)
