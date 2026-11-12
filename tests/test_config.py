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
            "JOB_PROFILES": '{"node":{"image":"node:lts","memory":"4g"}}',
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.url, "https://forgejo.example")
        self.assertEqual(settings.repos, ("owner/repo", "owner/other"))
        self.assertEqual(settings.max_memory, 8 * 2**30)
        self.assertEqual(settings.job_profiles["node"].image, "node:lts")

    def test_invalid_settings_fail_at_startup(self) -> None:
        values = {
            "FORGEJO_API_URL": "https://forgejo.example",
            "FORGEJO_API_TOKEN": "fake",
            "FORGEJO_REPOS": "owner/repo",
            "JOB_PROFILES": '{"node":{"image":"node:lts","memory":"4g"}}',
        }
        for name, value in (
            ("MAX_MEMORY", "0"),
            ("MAX_MEMORY", "garbage"),
            ("MAX_MEMORY", "3g"),
            ("JOB_PROFILES", ""),
            ("JOB_PROFILES", "{}"),
            ("POLL_INTERVAL", "nan"),
            ("RUNNER_JOB_TIMEOUT", "-1"),
            ("FORGEJO_REPOS", "invalid"),
            ("LOG_LEVEL", "invalid"),
            ("FORGEJO_API_URL", "relative"),
            ("FORGEJO_API_TOKEN", ""),
        ):
            with (
                self.subTest(name=name),
                patch.dict(os.environ, values | {name: value}, clear=True),
                self.assertRaises(ValueError),
            ):
                Settings.from_env()
