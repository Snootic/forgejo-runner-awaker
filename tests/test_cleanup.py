import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import docker

from awaker.claims import Claims
from awaker.cleanup import Cleanup
from awaker.config import Settings
from awaker.runners import Runners
from awaker.state import State


class CleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.url = "https://forgejo.example"
        self.state = State(":memory:", self.url)
        self.addCleanup(self.state.db.close)
        self.client = Mock()
        self.api = Mock()
        self.claims = Claims(1)
        self.cleanup = Cleanup(self.state, self.client, self.api, self.claims)

    def test_failed_docker_removal_is_retried_without_releasing_capacity(self) -> None:
        name = self.state.begin("owner/repo", "attempt", 42)
        self.claims.acquire("owner/repo#attempt")
        container = Mock()
        container.remove.side_effect = RuntimeError("unavailable")
        with self.assertLogs("awaker.cleanup"):
            self.cleanup.finish(name, container)
        entry = self.state.get(name)
        assert entry is not None
        self.assertTrue(entry["pending"])
        self.assertEqual(len(self.claims), 1)
        self.api.delete_runner.assert_not_called()
        container.remove.side_effect = None
        self.client.containers.get.return_value = container
        self.cleanup.retry()
        self.assertEqual(len(self.claims), 0)
        self.assertIsNone(self.state.get(name))
        self.api.delete_runner.assert_called_once_with("owner/repo", 42)

    def test_forgejo_retry_does_not_release_a_new_claim_for_the_same_job(self) -> None:
        name = self.state.begin("owner/repo", "attempt", 42)
        self.claims.acquire("owner/repo#attempt")
        self.api.delete_runner.side_effect = RuntimeError("unavailable")
        with self.assertLogs("awaker.cleanup"):
            self.cleanup.finish(name, Mock())
        self.assertEqual(len(self.claims), 0)
        entry = self.state.get(name)
        assert entry is not None
        self.assertTrue(entry["released"])
        self.claims.acquire("owner/repo#attempt")
        self.client.containers.get.side_effect = docker.errors.NotFound("gone")
        self.api.delete_runner.side_effect = None
        self.cleanup.retry()
        self.assertEqual(len(self.claims), 1)
        self.assertIsNone(self.state.get(name))

    def test_lost_registration_response_is_reconciled_by_unique_name(self) -> None:
        name = self.state.begin("owner/repo", "attempt")
        self.claims.acquire("owner/repo#attempt")
        self.client.containers.get.side_effect = docker.errors.NotFound("never created")
        self.api.list_runners.return_value = []
        self.cleanup.finish(name)
        self.assertEqual(len(self.claims), 0)
        self.assertIsNotNone(self.state.get(name))
        self.api.list_runners.return_value = [
            {"id": 42, "name": name},
            {"id": 43, "name": "someone-else"},
        ]
        self.cleanup.retry()
        self.api.delete_runner.assert_called_once_with("owner/repo", 42)
        self.assertIsNone(self.state.get(name))

    def test_restart_cleans_registration_created_before_container_creation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.sqlite3")
            first = State(path, self.url)
            name = first.begin("removed/repo", "attempt")
            first.registered(name, 42)
            first.db.close()
            restored = State(path, self.url)
            settings = Settings(url=self.url, repos=("owner/repo",), api_token="fake")
            manager = Runners(settings, self.client, self.api, restored)
            self.client.containers.list.return_value = []
            self.client.containers.get.side_effect = docker.errors.NotFound(
                "never created"
            )
            manager.recover_runners()
            self.api.delete_runner.assert_called_once_with("removed/repo", 42)
            self.assertEqual(restored.entries(), [])
            self.assertEqual(len(manager.claims), 0)
            restored.db.close()

    def test_state_is_scoped_to_the_forgejo_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.sqlite3")
            first = State(path, self.url)
            name = first.begin("owner/repo", "attempt", 42)
            second = State(path, "https://other.example")
            self.assertEqual(second.entries(), [])
            self.assertIsNone(second.get(name))
            second.db.close()
            first.db.close()

    def test_scope_changes_do_not_change_cleanup_target(self) -> None:
        name = self.state.begin("team/project", "attempt", 42, runner_scope="org:team")
        self.state.mark_pending(name)
        self.client.containers.get.side_effect = docker.errors.NotFound("gone")
        self.cleanup.retry()
        self.api.delete_runner.assert_called_once_with("org:team", 42)

    def test_existing_ledger_is_migrated_without_losing_runner_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.sqlite3")
            with sqlite3.connect(path) as db:
                db.execute(
                    "CREATE TABLE runners (name TEXT PRIMARY KEY, url TEXT, repo TEXT, handle TEXT, runner_id INTEGER, container_name TEXT, pending INTEGER, released INTEGER)"
                )
                db.execute(
                    "INSERT INTO runners VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("old", self.url, "owner/repo", "attempt", 42, "old", 1, 0),
                )
            restored = State(path, self.url)
            self.addCleanup(restored.db.close)
            cleanup = Cleanup(restored, self.client, self.api, self.claims)
            self.client.containers.get.side_effect = docker.errors.NotFound("gone")
            cleanup.retry()
            self.api.delete_runner.assert_called_once_with("owner/repo", 42)
            self.assertEqual(restored.entries(), [])
