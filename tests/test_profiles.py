import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

from awaker.config import Settings
from awaker.profiles import JobProfile, parse_profiles, select_profile
from awaker.runners import Runners
from awaker.state import State


class ProfileTests(unittest.TestCase):
    def test_environment_profiles_supply_images_and_reject_mapping_conflicts(
        self,
    ) -> None:
        values = {
            "FORGEJO_API_URL": "https://forgejo.example",
            "FORGEJO_API_TOKEN": "fake",
            "FORGEJO_SCOPE": "instance",
            "JOB_PROFILES": '{"node":{"image":"node:24","memory":"512m"}}',
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.images, {"node": "node:24"})
        self.assertEqual(settings.job_profiles["node"].timeout, settings.job_timeout)
        with (
            patch.dict(
                os.environ, values | {"JOB_IMAGES": '{"node":"node:22"}'}, clear=True
            ),
            self.assertRaisesRegex(ValueError, "disagree"),
        ):
            Settings.from_env()

    def test_profiles_select_images_and_resource_limits(self) -> None:
        profiles = parse_profiles(
            '{"node-small":{"image":"node:24-bookworm-slim","memory":"512m","cpus":0.5,"pids":128,"timeout":600}}',
            10800,
        )
        profile = profiles["node-small"]
        self.assertEqual(profile.limits, "--memory=512m --cpus=0.5 --pids-limit=128")
        self.assertEqual(profile.timeout, 600)
        self.assertIs(select_profile(["node-small"], profiles), profile)
        self.assertIsNone(select_profile(["other"], profiles))

    def test_invalid_profiles_fail_configuration(self) -> None:
        for changes in (
            {"memory": "garbage"},
            {"memory": "0g"},
            {"memory": "2 g"},
            {"cpus": 0},
            {"cpus": float("nan")},
            {"cpus": True},
            {"pids": -1},
            {"pids": 1.5},
            {"timeout": 10801},
            {"image": "docker://node:lts"},
            {"unexpected": "value"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_profiles(
                    json.dumps({"test": {"image": "node:lts"} | changes}), 10800
                )

    def test_conflicting_resources_for_one_image_are_rejected(self) -> None:
        profiles = {
            "small": JobProfile("node:lts", memory="512m"),
            "large": JobProfile("node:lts", memory="4g"),
        }
        with self.assertRaises(ValueError):
            select_profile(["small", "large"], profiles)

    def test_dispatch_passes_selected_profile_to_runner_without_mutating_defaults(
        self,
    ) -> None:
        settings = Settings(
            url="https://forgejo.example",
            repos=(),
            api_token="fake",
            scope="org:team",
            job_profiles={
                "node-small": JobProfile("node:lts", memory="512m", cpus=1, timeout=600)
            },
        )
        state = State(":memory:", settings.url)
        self.addCleanup(state.db.close)
        manager = Runners(settings, Mock(), Mock(), state)
        with patch.object(threading, "Thread") as thread:
            self.assertEqual(
                manager.dispatch(
                    "team/new-project",
                    {"handle": "h", "runs_on": ["node-small"]},
                    {"node-small": "docker://node:lts"},
                ),
                "Runner dispatched",
            )
        job_settings = thread.call_args.kwargs["args"][3]
        self.assertEqual(
            job_settings.job_limits, "--memory=512m --cpus=1 --pids-limit=512"
        )
        self.assertEqual(job_settings.job_timeout, 600)
        self.assertEqual(settings.job_timeout, 10800)
        self.assertEqual(settings.images, {"node-small": "node:lts"})

    def test_out_of_scope_delivery_never_reserves_capacity(self) -> None:
        settings = Settings(
            url="https://forgejo.example", repos=(), api_token="fake", scope="org:team"
        )
        state = State(":memory:", settings.url)
        self.addCleanup(state.db.close)
        manager = Runners(settings, Mock(), Mock(), state)
        self.assertEqual(
            manager.dispatch(
                "other/project",
                {"handle": "h", "runs_on": ["node"]},
                {"node": "docker://node:lts"},
            ),
            "Repository not served",
        )
        self.assertEqual(len(manager.claims), 0)
