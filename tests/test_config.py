import os
import unittest
from unittest.mock import patch

from awaker.config import Settings


class ConfigTests(unittest.TestCase):
    def test_environment_is_parsed_once_and_repositories_are_deduplicated(self) -> None:
        values = {
            "FORGEJO_API_URL": "https://forgejo.example/",
            "FORGEJO_API_TOKEN": "fake",
            "FORGEJO_REPOS": "owner/repo, owner/repo owner/other",
            "JOB_IMAGES": '{"node":"node:lts"}',
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.url, "https://forgejo.example")
        self.assertEqual(settings.repos, ("owner/repo", "owner/other"))
        self.assertEqual(settings.max_runners, 4)
        self.assertEqual(settings.job_images, {"node": "node:lts"})

    def test_invalid_settings_fail_at_startup(self) -> None:
        values = {
            "FORGEJO_API_URL": "https://forgejo.example",
            "FORGEJO_API_TOKEN": "fake",
            "FORGEJO_REPOS": "owner/repo",
        }
        for name, value in (
            ("MAX_RUNNERS", "0"),
            ("POLL_INTERVAL", "nan"),
            ("RUNNER_JOB_TIMEOUT", "-1"),
            ("MAX_RUNNERS", "bad"),
            ("FORGEJO_REPOS", "invalid"),
            ("LOG_LEVEL", "invalid"),
            ("FORGEJO_API_URL", "relative"),
            ("FORGEJO_API_TOKEN", ""),
            ("JOB_IMAGES", "[]"),
            ("JOB_IMAGES", "invalid"),
            ("JOB_IMAGES", '{"ubuntu":false}'),
            ("JOB_IMAGES", '{"":"node:lts"}'),
            ("JOB_IMAGES", '{"node":"docker://node:lts"}'),
        ):
            with (
                self.subTest(name=name),
                patch.dict(os.environ, values | {name: value}, clear=True),
                self.assertRaises(ValueError),
            ):
                Settings.from_env()
