import os
import unittest
from unittest.mock import patch

from awaker.config import Settings
from awaker.forgejo import Forgejo
from awaker.scopes import accepts_repo, runner_path


class ScopeTests(unittest.TestCase):
    def test_paths_use_current_scope_specific_endpoints(self) -> None:
        for scope, expected in (
            ("owner/repo", "/repos/owner/repo/actions/runners"),
            ("org:team", "/orgs/team/actions/runners"),
            ("user:alice", "/user/actions/runners"),
            ("instance", "/admin/actions/runners"),
        ):
            with self.subTest(scope=scope):
                self.assertEqual(runner_path(scope), expected)

    def test_shared_scope_accepts_new_projects_and_enforces_boundaries(self) -> None:
        self.assertTrue(accepts_repo("team/new-project", "org:team", ()))
        self.assertFalse(accepts_repo("other/project", "org:team", ()))
        self.assertTrue(accepts_repo("alice/new-project", "user:alice", ()))
        self.assertFalse(accepts_repo("bob/project", "user:alice", ()))
        self.assertTrue(accepts_repo("any/project", "instance", ()))
        self.assertFalse(accepts_repo("malformed", "instance", ()))
        self.assertFalse(accepts_repo("team/other", "org:team", ("team/allowed",)))
        self.assertTrue(accepts_repo("team/allowed", "org:team", ("team/allowed",)))

    def test_scope_configuration_does_not_require_repository_enumeration(self) -> None:
        values = {
            "FORGEJO_API_URL": "https://forgejo.example",
            "FORGEJO_API_TOKEN": "fake",
        }
        with patch.dict(os.environ, values | {"FORGEJO_SCOPE": "org:team"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.poll_targets, ("org:team",))
        self.assertEqual(settings.repos, ())
        for scope in ("org:", "organization:team", "user:a/b", "org:team other", "*"):
            with (
                self.subTest(scope=scope),
                patch.dict(os.environ, values | {"FORGEJO_SCOPE": scope}, clear=True),
                self.assertRaises(ValueError),
            ):
                Settings.from_env()

    def test_shared_queue_resolves_repository_ids_once_per_poll(self) -> None:
        api = Forgejo(
            Settings(
                url="https://forgejo.example",
                api_token="fake",
                repos=(),
                scope="org:team",
            )
        )
        with patch.object(
            api,
            "request",
            side_effect=[
                [
                    {"handle": "a", "status": "waiting", "repo_id": 7},
                    {"handle": "b", "status": "waiting", "repo_id": 7},
                    {"handle": "c", "status": "running", "repo_id": 8},
                ],
                {"full_name": "team/new-project"},
            ],
        ) as request:
            jobs = api.waiting_jobs("org:team")
        self.assertEqual(
            [job["repository"] for job in jobs],
            ["team/new-project", "team/new-project"],
        )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[0].args, ("GET", "/orgs/team/actions/runners/jobs")
        )
        self.assertEqual(request.call_args_list[1].args, ("GET", "/repositories/7"))

    def test_user_scope_must_match_the_authenticated_account(self) -> None:
        api = Forgejo(
            Settings(
                url="https://forgejo.example",
                api_token="fake",
                repos=(),
                scope="user:alice",
            )
        )
        with (
            patch.object(api, "request", return_value={"login": "bob"}),
            self.assertRaises(ValueError),
        ):
            api.check_access()
        with patch.object(
            api, "request", side_effect=[{"login": "Alice"}, [], []]
        ) as request:
            api.check_access()
        self.assertEqual(request.call_count, 3)

    def test_shared_registration_is_ephemeral(self) -> None:
        api = Forgejo(
            Settings(
                url="https://forgejo.example",
                api_token="fake",
                repos=(),
                scope="org:team",
            )
        )
        with patch.object(
            api, "request", return_value={"id": 1, "uuid": "u", "token": "t"}
        ) as request:
            api.create_runner("org:team", "unique-name")
        request.assert_called_once_with(
            "POST",
            "/orgs/team/actions/runners",
            {"name": "unique-name", "ephemeral": True},
        )
