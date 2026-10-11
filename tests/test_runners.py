import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Never
from unittest.mock import Mock, patch

import docker

from awaker import cleanup as cleanup_module
from awaker import runners as runner_module
from awaker.config import Settings
from awaker.runners import MANAGED_LABEL, Runners
from awaker.state import State


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            url="https://forgejo.example",
            repos=("owner/repo",),
            api_token="fake",
            webhook_secret=b"secret",
            max_runners=1,
        )
        self.client = Mock()
        self.api = Mock()
        self.state = State(":memory:", self.settings.url)
        self.addCleanup(self.state.db.close)
        self.runners = Runners(self.settings, self.client, self.api, self.state)
        self.claims = self.runners.claims

    def container(self, status: str = "running") -> Mock:
        container = Mock()
        container.status = status
        container.name = f"runner-{status}"
        container.labels = {
            MANAGED_LABEL: self.settings.url,
            f"{MANAGED_LABEL}.repo": "owner/repo",
            f"{MANAGED_LABEL}.handle": "attempt",
            f"{MANAGED_LABEL}.runner-id": "42",
        }
        container.wait.return_value = {"StatusCode": 0}
        return container

    def test_claims_do_not_expire_and_recovery_can_exceed_new_capacity(self) -> None:
        self.assertTrue(self.claims.acquire("a"))
        self.assertFalse(self.claims.acquire("a"))
        self.claims.restore("b")
        self.assertEqual(len(self.claims), 2)
        self.assertFalse(self.claims.acquire("c"))
        self.claims.release("a")
        self.assertFalse(self.claims.acquire("c"))
        self.claims.release("b")
        self.assertTrue(self.claims.acquire("c"))

    def test_supervision_cleans_success_wait_and_log_failures(self) -> None:
        for failure in (None, "wait", "logs"):
            with self.subTest(failure=failure):
                container = self.container()
                if failure == "wait":
                    container.wait.side_effect = RuntimeError("connection lost")
                if failure == "logs":
                    container.wait.return_value = {"StatusCode": 1}
                    container.logs.side_effect = RuntimeError("logs unavailable")
                self.claims.acquire("owner/repo#attempt")
                name = self.state.begin("owner/repo", "attempt", 42, container.name)
                with self.assertLogs(runner_module.logger, level="INFO"):
                    self.runners.supervise_runner(
                        "owner/repo", "attempt", name, container
                    )
                container.remove.assert_called_once_with(force=True, v=True)
                self.api.delete_runner.assert_called_with("owner/repo", 42)
                self.assertEqual(len(self.claims), 0)

    def test_failed_cleanup_keeps_capacity(self) -> None:
        container = self.container()
        container.remove.side_effect = RuntimeError("daemon unavailable")
        self.claims.acquire("owner/repo#attempt")
        name = self.state.begin("owner/repo", "attempt", 42, container.name)
        with self.assertLogs(cleanup_module.logger, level="ERROR"):
            self.runners.cleanup.finish(name, container)
        self.assertEqual(len(self.claims), 1)
        self.api.delete_runner.assert_not_called()

    def test_already_removed_container_releases_capacity(self) -> None:
        container = self.container()
        container.remove.side_effect = docker.errors.NotFound("removed")
        self.claims.acquire("owner/repo#attempt")
        name = self.state.begin("owner/repo", "attempt", 42, container.name)
        self.runners.cleanup.finish(name, container)
        self.assertEqual(len(self.claims), 0)

    def test_failed_start_removes_container_and_registration(self) -> None:
        container = self.container("created")
        container.start.side_effect = RuntimeError("start failed")
        self.client.containers.create.return_value = container
        self.claims.acquire("owner/repo#attempt")
        with (
            patch.object(
                self.api,
                "create_runner",
                return_value={"id": 42, "uuid": "uuid", "token": "secret"},
            ),
            patch.object(
                runner_module, "stat", return_value=SimpleNamespace(st_gid=1000)
            ),
            self.assertLogs(runner_module.logger, level="ERROR"),
        ):
            self.runners.run_runner(
                "owner/repo", {"handle": "attempt"}, ["ubuntu-latest:docker://node:lts"]
            )
        container.remove.assert_called_once_with(force=True, v=True)
        self.assertEqual(len(self.claims), 0)
        labels = self.client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels[f"{MANAGED_LABEL}.runner-id"], "42")

    def test_registration_intent_precedes_a_lost_api_response(self) -> None:
        self.claims.acquire("owner/repo#attempt")
        self.client.containers.get.side_effect = docker.errors.NotFound("never created")

        def register(repo: str, name: str) -> Never:
            entry = self.state.get(name)
            assert entry is not None
            self.assertEqual(entry["repo"], repo)
            self.api.list_runners.return_value = [{"name": name, "id": 42}]
            raise TimeoutError("registration response lost")

        self.api.create_runner.side_effect = register
        with self.assertLogs(runner_module.logger):
            self.runners.run_runner(
                "owner/repo", {"handle": "attempt"}, ["node:docker://node:lts"]
            )
        self.api.delete_runner.assert_called_once_with("owner/repo", 42)
        self.client.containers.create.assert_not_called()
        self.assertEqual(self.state.entries(), [])
        self.assertEqual(len(self.claims), 0)

    def test_shared_runner_receives_profile_settings_and_cleans_original_scope(
        self,
    ) -> None:
        self.runners.settings = replace(self.settings, scope="org:owner", repos=())
        job_settings = replace(
            self.runners.settings,
            job_limits="--memory=512m --cpus=1 --pids-limit=128",
            job_timeout=600,
        )
        self.api.create_runner.return_value = {
            "id": 42,
            "uuid": "uuid",
            "token": "secret",
        }
        container = self.container()
        self.client.containers.create.return_value = container
        self.claims.acquire("owner/repo#attempt")
        with patch.object(
            runner_module, "stat", return_value=SimpleNamespace(st_gid=1000)
        ):
            self.runners.run_runner(
                "owner/repo",
                {"handle": "attempt"},
                ["node-small:docker://node:lts"],
                job_settings,
            )
        self.assertEqual(self.api.create_runner.call_args.args[0], "org:owner")
        script = self.client.containers.create.call_args.kwargs["command"][2]
        self.assertIn("--memory=512m --cpus=1 --pids-limit=128", script)
        self.assertIn("timeout: 600s", script)
        self.api.delete_runner.assert_called_once_with("org:owner", 42)
        self.assertEqual(self.state.entries(), [])

    def test_restart_restores_running_and_cleans_stopped_runners(self) -> None:
        running = self.container()
        stopped = self.container("exited")
        stopped.labels = dict(stopped.labels, **{f"{MANAGED_LABEL}.handle": "other"})
        self.client.containers.list.return_value = [running, stopped]
        with patch.object(
            runner_module, "threading", SimpleNamespace(Thread=Mock())
        ) as threads:
            self.runners.recover_runners()
        thread = threads.Thread
        self.assertEqual(len(self.claims), 1)
        self.assertFalse(self.claims.acquire("new"))
        thread.call_args.kwargs["target"](*thread.call_args.kwargs["args"])
        self.assertEqual(len(self.claims), 0)
        stopped.remove.assert_called_once_with(force=True, v=True)
        running.remove.assert_called_once_with(force=True, v=True)

    def test_recovery_uses_saved_job_identity_when_container_labels_differ(
        self,
    ) -> None:
        container = self.container()
        name = self.state.begin("owner/repo", "attempt", 42, container.name)
        container.labels[f"{MANAGED_LABEL}.handle"] = "stale-attempt"
        self.client.containers.list.return_value = [container]
        with patch.object(
            runner_module, "threading", SimpleNamespace(Thread=Mock())
        ) as threads:
            self.runners.recover_runners()
        self.assertFalse(self.claims.acquire("owner/repo#attempt"))
        thread = threads.Thread
        self.assertEqual(
            thread.call_args.kwargs["args"], ("owner/repo", "attempt", name, container)
        )
        thread.call_args.kwargs["target"](*thread.call_args.kwargs["args"])
        self.assertEqual(len(self.claims), 0)
