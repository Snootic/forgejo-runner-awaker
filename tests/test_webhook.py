import asyncio
import hashlib
import hmac
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

from fastapi import HTTPException, Request

from awaker import runners as runner_module
from awaker import webhook as triggers
from awaker.config import Settings
from awaker.runners import Runners
from awaker.state import State


class WebhookTests(unittest.TestCase):
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
        self.app = SimpleNamespace(state=SimpleNamespace(runners=self.runners))

    def test_v17_waiting_webhook_accepts_both_actions_and_rejects_bad_signature(
        self,
    ) -> None:
        async def deliver(
            action: str, signature_override: str | None = None, repo: str = "owner/repo"
        ) -> dict[str, str]:
            body = json.dumps(
                {
                    "action": action,
                    "run": {"repository": {"full_name": repo}},
                    "job": {
                        "status": "waiting",
                        "handle": "attempt",
                        "runs_on": ["ubuntu-latest"],
                    },
                }
            ).encode()

            async def read_body() -> bytes:
                return body

            signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
            request = SimpleNamespace(
                app=self.app,
                body=read_body,
                headers={
                    "X-Forgejo-Event": triggers.WAITING_EVENT,
                    "X-Forgejo-Signature": signature_override or signature,
                },
            )
            return await triggers.handle_webhook(cast(Request, request))

        with (
            patch.object(triggers, "list_environments", return_value={}),
            patch.object(
                self.runners, "dispatch", return_value="Runner dispatched"
            ) as dispatch,
        ):
            for action in ("new_job_attempt", "job_status_changed"):
                self.assertEqual(
                    asyncio.run(deliver(action))["status"], "Runner dispatched"
                )
                self.assertEqual(dispatch.call_args.args[0], "owner/repo")
            for signature in ("invalid", "é"):
                with self.assertRaises(HTTPException) as error:
                    asyncio.run(deliver("new_job_attempt", signature))
                self.assertEqual(error.exception.status_code, 401)
            self.assertEqual(dispatch.call_count, 2)
            self.runners.settings = replace(self.settings, repos=(), scope="org:owner")
            self.assertEqual(
                asyncio.run(deliver("new_job_attempt", repo="owner/new-project"))[
                    "status"
                ],
                "Runner dispatched",
            )
            self.assertEqual(dispatch.call_args.args[0], "owner/new-project")
            self.assertEqual(
                asyncio.run(deliver("new_job_attempt", repo="other/project"))["status"],
                "Repository not served",
            )
            self.assertEqual(dispatch.call_count, 3)

    def test_poll_and_webhook_share_dispatch_claim(self) -> None:
        body = json.dumps(
            {
                "action": "new_job_attempt",
                "run": {"repository": {"full_name": "owner/repo"}},
                "job": {
                    "status": "waiting",
                    "handle": "attempt",
                    "runs_on": ["ubuntu-latest"],
                },
            }
        ).encode()
        request = Mock()
        request.app = self.app

        async def read_body() -> bytes:
            return body

        request.body = read_body
        request.headers = {
            "X-Forgejo-Event": triggers.WAITING_EVENT,
            "X-Forgejo-Signature": hmac.new(
                b"secret", body, hashlib.sha256
            ).hexdigest(),
        }
        environments = {"ubuntu-latest": "docker://node:lts"}
        with (
            patch.object(
                runner_module, "threading", SimpleNamespace(Thread=Mock())
            ) as threads,
            patch.object(triggers, "list_environments", return_value=environments),
        ):
            self.assertEqual(
                self.runners.dispatch(
                    "owner/repo", json.loads(body)["job"], environments
                ),
                "Runner dispatched",
            )
            result = asyncio.run(triggers.handle_webhook(cast(Request, request)))
            self.assertIn("Already dispatched", result["status"])
            threads.Thread.assert_called_once()


class PayloadTests(unittest.TestCase):
    def test_malformed_nested_fields_return_bad_request(self) -> None:
        payload: object
        for payload in (
            [],
            {"action": "new_job_attempt", "job": []},
            {
                "action": [],
                "job": {"handle": "h", "runs_on": []},
                "run": {"repository": {"full_name": "owner/repo"}},
            },
            {
                "action": "new_job_attempt",
                "job": {"handle": "h", "runs_on": "linux"},
                "run": {"repository": {"full_name": "owner/repo"}},
            },
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaises(HTTPException) as error,
            ):
                triggers.waiting_delivery(json.dumps(payload).encode())
            self.assertEqual(error.exception.status_code, 400)
