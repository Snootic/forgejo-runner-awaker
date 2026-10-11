import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from awaker.config import Settings
from awaker.forgejo import Forgejo


class ForgejoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = Forgejo(
            Settings(
                url="https://forgejo.example", repos=("owner/repo",), api_token="fake"
            )
        )

    def test_registration_requests_ephemeral_credentials(self) -> None:
        credentials = {"id": 42, "uuid": "uuid", "token": "token"}
        with patch.object(self.api, "request", return_value=credentials) as request:
            self.assertEqual(
                self.api.create_runner("owner/repo", "attempt"), credentials
            )
        request.assert_called_once_with(
            "POST",
            "/repos/owner/repo/actions/runners",
            {"name": "attempt", "ephemeral": True},
        )

    def test_only_waiting_jobs_are_returned(self) -> None:
        with patch.object(
            self.api,
            "request",
            return_value=[
                {"status": "running", "handle": "a"},
                {"status": "waiting", "handle": "b"},
            ],
        ):
            self.assertEqual(
                self.api.waiting_jobs("owner/repo"),
                [{"status": "waiting", "handle": "b"}],
            )

    def test_runner_reconciliation_reads_all_pages(self) -> None:
        with patch.object(
            self.api, "request", side_effect=[[{"id": 1}], [{"id": 2}], []]
        ) as request:
            self.assertEqual(
                self.api.list_runners("owner/repo"), [{"id": 1}, {"id": 2}]
            )
        self.assertIn("page=3", request.call_args.args[1])

    def test_access_check_checks_queue_and_runner_management(self) -> None:
        with (
            patch.object(self.api, "list_runners") as runners,
            patch.object(self.api, "waiting_jobs") as jobs,
        ):
            self.api.check_access()
        runners.assert_called_once_with("owner/repo")
        jobs.assert_called_once_with("owner/repo")

    def test_registration_deletion_ignores_only_not_found(self) -> None:
        for code in (404, 403, 500):
            error = HTTPError(
                "https://forgejo.example", code, "failure", Message(), None
            )
            with (
                self.subTest(code=code),
                patch.object(self.api, "request", side_effect=error),
            ):
                if code == 404:
                    self.api.delete_runner("owner/repo", 42)
                else:
                    with self.assertRaises(HTTPError):
                        self.api.delete_runner("owner/repo", 42)
