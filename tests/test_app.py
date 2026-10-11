import asyncio
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import docker
from fastapi import FastAPI, HTTPException, Request

from awaker import app as app_module
from awaker.config import Settings
from awaker.runners import Runners


class AppTests(unittest.TestCase):
    def test_startup_refreshes_cached_tag_and_recovers_before_polling(self) -> None:
        settings = Settings(
            url="https://forgejo.example", repos=("owner/repo",), api_token="fake"
        )
        client = Mock()
        api = Mock()
        manager = Mock()
        events = []
        client.ping.side_effect = lambda: events.append("ping")
        api.check_access.side_effect = lambda: events.append("forgejo")
        client.images.pull.side_effect = lambda _: events.append("pull")
        manager.recover_runners.side_effect = lambda: events.append("recover")

        async def poll(runners: Runners) -> None:
            self.assertIs(runners, manager)
            events.append("poll")
            await asyncio.Event().wait()

        async def check() -> None:
            app = FastAPI()
            async with app_module.lifespan(app):
                self.assertIs(app.state.runners, manager)
                await asyncio.sleep(0)

        with (
            patch.object(Settings, "from_env", return_value=settings),
            patch.object(docker, "from_env", return_value=client),
            patch.object(app_module, "Forgejo", return_value=api),
            patch.object(app_module, "State"),
            patch.object(
                app_module,
                "list_environments",
                return_value={"node": "docker://node:lts"},
            ),
            patch.object(app_module, "Runners", return_value=manager),
            patch.object(app_module, "poll_forever", poll),
        ):
            asyncio.run(check())
        self.assertEqual(events, ["ping", "forgejo", "pull", "recover", "poll"])
        client.images.pull.assert_called_once_with("data.forgejo.org/forgejo/runner:13")
        client.images.get.assert_not_called()

    def test_digest_images_are_pulled_only_when_missing(self) -> None:
        image = "data.forgejo.org/forgejo/runner@sha256:" + "a" * 64
        for missing in (False, True):
            with self.subTest(missing=missing):
                client = Mock()
                if missing:
                    client.images.get.side_effect = docker.errors.ImageNotFound(
                        "missing"
                    )
                asyncio.run(app_module.prepare_runner_image(client, image))
                client.images.get.assert_called_once_with(image)
                if missing:
                    client.images.pull.assert_called_once_with(image)
                else:
                    client.images.pull.assert_not_called()

    def test_tag_refresh_failure_is_not_hidden_by_a_cached_image(self) -> None:
        client = Mock()
        client.images.pull.side_effect = docker.errors.APIError("registry unavailable")
        with self.assertRaises(docker.errors.APIError):
            asyncio.run(
                app_module.prepare_runner_image(
                    client, "data.forgejo.org/forgejo/runner:13"
                )
            )
        client.images.get.assert_not_called()

    def test_invalid_forgejo_access_prevents_dispatch_startup(self) -> None:
        settings = Settings(
            url="https://forgejo.example", repos=("owner/repo",), api_token="invalid"
        )
        api = Mock()
        api.check_access.side_effect = RuntimeError("unauthorized")

        async def check() -> None:
            async with app_module.lifespan(FastAPI()):
                self.fail("startup should fail")

        with (
            patch.object(Settings, "from_env", return_value=settings),
            patch.object(docker, "from_env", return_value=Mock()),
            patch.object(app_module, "Forgejo", return_value=api),
            patch.object(app_module, "Runners") as manager,
            self.assertRaises(RuntimeError),
        ):
            asyncio.run(check())
        manager.assert_not_called()

    def test_readiness_reports_dependency_failures(self) -> None:
        manager = SimpleNamespace(
            docker=Mock(), forgejo=Mock(), settings=SimpleNamespace(images={})
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(runners=manager))
        )
        with patch.object(
            app_module, "list_environments", return_value={"node": "docker://node:lts"}
        ):
            self.assertEqual(
                asyncio.run(app_module.readiness(cast(Request, request))),
                {"status": "ready"},
            )
            manager.forgejo.check_access.side_effect = RuntimeError("unavailable")
            with (
                self.assertLogs(app_module.logger),
                self.assertRaises(HTTPException) as error,
            ):
                asyncio.run(app_module.readiness(cast(Request, request)))
            self.assertEqual(error.exception.status_code, 503)

    def test_no_job_images_prevents_startup(self) -> None:
        settings = Settings(
            url="https://forgejo.example", repos=("owner/repo",), api_token="fake"
        )

        async def check() -> None:
            async with app_module.lifespan(FastAPI()):
                self.fail("startup should fail")

        with (
            patch.object(Settings, "from_env", return_value=settings),
            patch.object(docker, "from_env", return_value=Mock()),
            patch.object(app_module, "Forgejo", return_value=Mock()),
            patch.object(app_module, "list_environments", return_value={}),
            patch.object(app_module, "Runners") as manager,
            self.assertRaises(ValueError),
        ):
            asyncio.run(check())
        manager.assert_not_called()
