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
    def test_environment_profiles_supply_images_and_default_timeout(self) -> None:
        values = {
            "FORGEJO_API_URL": "https://forgejo.example",
            "FORGEJO_API_TOKEN": "fake",
            "FORGEJO_SCOPE": "instance",
            "JOB_PROFILES": '{"node":{"image":"node:24","memory":"512m"}}',
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.job_profiles["node"].image, "node:24")
        self.assertEqual(settings.job_profiles["node"].timeout, settings.job_timeout)

    def test_profiles_select_images_and_resource_limits(self) -> None:
        profiles = parse_profiles(
            '{"node-small":{"image":"node:24-bookworm-slim","memory":"512m","cpus":0.5,"pids":128,"timeout":600}}',
            10800,
        )
        profile = profiles["node-small"]
        self.assertEqual(
            profile.limits,
            "--memory=512m --memory-swap=512m --cpus=0.5 --pids-limit=128",
        )
        self.assertEqual(profile.timeout, 600)
        self.assertIs(select_profile(["node-small"], profiles), profile)
        self.assertEqual(
            profile.runner_labels(["node-small"]),
            ["node-small:docker://node:24-bookworm-slim"],
        )

    def test_every_label_must_name_a_profile(self) -> None:
        profiles = {"a": JobProfile("node:lts"), "b": JobProfile("node:lts")}
        self.assertIs(select_profile(["a", "b"], profiles), profiles["a"])
        for labels in ([], ["other"], ["a", "other"]):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                select_profile(labels, profiles)

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

    def test_dispatch_passes_selected_profile_to_runner(self) -> None:
        profile = JobProfile("node:lts", memory="512m", cpus=1, timeout=600)
        settings = Settings(
            url="https://forgejo.example",
            repos=(),
            api_token="fake",
            scope="org:team",
            job_profiles={"node-small": profile},
        )
        state = State(":memory:", settings.url)
        self.addCleanup(state.db.close)
        manager = Runners(settings, Mock(), Mock(), state)
        with patch.object(threading, "Thread") as thread:
            self.assertEqual(
                manager.dispatch(
                    "team/new-project", {"handle": "h", "runs_on": ["node-small"]}
                ),
                "Runner dispatched",
            )
        self.assertEqual(
            thread.call_args.kwargs["args"][2:],
            (["node-small:docker://node:lts"], profile),
        )

    def test_jobs_wait_when_profile_memory_exceeds_the_remaining_budget(self) -> None:
        settings = Settings(
            url="https://forgejo.example",
            repos=(),
            api_token="fake",
            scope="org:team",
            max_memory=6 * 2**30,
            job_profiles={
                "big": JobProfile("node:lts", memory="4g"),
                "small": JobProfile("node:lts", memory="2g"),
            },
        )
        state = State(":memory:", settings.url)
        self.addCleanup(state.db.close)
        manager = Runners(settings, Mock(), Mock(), state)

        def dispatch(handle: str, label: str) -> str:
            return manager.dispatch(
                "team/project", {"handle": handle, "runs_on": [label]}
            )

        with patch.object(threading, "Thread"):
            self.assertEqual(dispatch("1", "big"), "Runner dispatched")
            self.assertIn("memory capacity", dispatch("2", "big"))
            self.assertEqual(dispatch("3", "small"), "Runner dispatched")
            self.assertIn("memory capacity", dispatch("4", "small"))
            manager.claims.release("team/project#1")
            self.assertEqual(dispatch("2", "big"), "Runner dispatched")

    def test_unknown_labels_and_out_of_scope_delivery_never_reserve_capacity(
        self,
    ) -> None:
        settings = Settings(
            url="https://forgejo.example",
            repos=(),
            api_token="fake",
            scope="org:team",
            job_profiles={"node": JobProfile("node:lts")},
        )
        state = State(":memory:", settings.url)
        self.addCleanup(state.db.close)
        manager = Runners(settings, Mock(), Mock(), state)
        self.assertEqual(
            manager.dispatch("other/project", {"handle": "h", "runs_on": ["node"]}),
            "Repository not served",
        )
        self.assertIn(
            "No job profile",
            manager.dispatch("team/project", {"handle": "h", "runs_on": ["gpu"]}),
        )
        self.assertEqual(len(manager.claims), 0)
