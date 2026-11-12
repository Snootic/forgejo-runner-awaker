import asyncio
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from awaker import polling
from awaker.config import Settings
from awaker.runners import Runners


class PollingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runners = SimpleNamespace(
            settings=Settings(
                url="https://forgejo.example",
                api_token="fake",
                repos=("owner/a", "owner/b"),
            ),
            forgejo=Mock(),
            docker=Mock(),
            dispatch=Mock(),
        )

    def cycle(self) -> None:
        with (
            patch.object(
                asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)
            ),
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(polling.poll_forever(cast(Runners, self.runners)))

    def test_idle_cycle_does_not_scan_images(self) -> None:
        self.runners.forgejo.waiting_jobs.return_value = []
        with patch.object(polling, "list_environments") as images:
            self.cycle()
        images.assert_not_called()
        self.runners.dispatch.assert_not_called()

    def test_images_are_scanned_once_for_multiple_repositories(self) -> None:
        self.runners.forgejo.waiting_jobs.return_value = [{"handle": "attempt"}]
        with patch.object(polling, "list_environments", return_value={}) as images:
            self.cycle()
        images.assert_called_once_with(self.runners.docker, {})
        self.assertEqual(self.runners.dispatch.call_count, 2)

    def test_one_repository_failure_does_not_block_the_next(self) -> None:
        self.runners.forgejo.waiting_jobs.side_effect = [
            RuntimeError("API unavailable"),
            [{"handle": "attempt"}],
        ]
        with (
            patch.object(polling, "list_environments", return_value={}),
            self.assertLogs(polling.logger),
        ):
            self.cycle()
        self.runners.dispatch.assert_called_once_with(
            "owner/b", {"handle": "attempt"}, {}
        )

    def test_shared_queue_dispatches_to_the_jobs_repository(self) -> None:
        self.runners.settings = Settings(
            url="https://forgejo.example", api_token="fake", repos=(), scope="org:team"
        )
        job = {"handle": "attempt", "repository": "team/new-project"}
        self.runners.forgejo.waiting_jobs.return_value = [job]
        with patch.object(polling, "list_environments", return_value={}):
            self.cycle()
        self.runners.forgejo.waiting_jobs.assert_called_once_with("org:team")
        self.runners.dispatch.assert_called_once_with("team/new-project", job, {})

    def test_failures_back_off_and_log_one_line(self) -> None:
        self.runners.forgejo.waiting_jobs.side_effect = RuntimeError("down")
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError])
        with (
            patch.object(asyncio, "sleep", sleep),
            self.assertLogs(polling.logger, level="ERROR") as logs,
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(polling.poll_forever(cast(Runners, self.runners)))
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [10, 20, 40])
        self.assertTrue(all(not record.exc_info for record in logs.records))

    def test_recovery_restores_the_poll_interval(self) -> None:
        self.runners.forgejo.waiting_jobs.side_effect = [
            RuntimeError("down"),
            RuntimeError("down"),
            [],
            [],
            [],
            [],
        ]
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError])
        with (
            patch.object(asyncio, "sleep", sleep),
            self.assertLogs(polling.logger) as logs,
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(polling.poll_forever(cast(Runners, self.runners)))
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [10, 5, 5])
        self.assertIn("recovered", logs.output[-1])

    def test_backoff_is_capped(self) -> None:
        self.assertEqual(polling.retry_delay(5, 0), 5)
        self.assertEqual(polling.retry_delay(5, 20), polling.MAX_BACKOFF)
        self.assertEqual(polling.retry_delay(120, 3), 120)
