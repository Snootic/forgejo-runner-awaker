import json
import urllib.request
from typing import cast
from urllib.error import HTTPError

from .config import Settings
from .models import Job, JSONValue, RegisteredRunner, RunnerRegistration
from .scopes import runner_path


class Forgejo:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def request(self, method: str, path: str, body: JSONValue = None) -> JSONValue:
        request = urllib.request.Request(
            f"{self.settings.url}/api/v1{path}", method=method
        )
        request.add_header("Authorization", f"token {self.settings.api_token}")
        request.add_header("Accept", "application/json")
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, data, timeout=30) as response:
            payload = response.read()
        return cast(JSONValue, json.loads(payload)) if payload else None

    def create_runner(self, scope: str, name: str) -> RunnerRegistration:
        return cast(
            RunnerRegistration,
            self.request(
                "POST",
                runner_path(scope),
                {"name": name, "ephemeral": True},
            ),
        )

    def list_runners(self, scope: str) -> list[RegisteredRunner]:
        runners: list[RegisteredRunner] = []
        page = 1
        while True:
            batch = self.request(
                "GET",
                f"{runner_path(scope)}?visible=false&page={page}&limit=50",
            )
            if not isinstance(batch, list):
                raise TypeError(f"Unexpected runner list for {scope}")
            if not batch:
                return runners
            runners.extend(cast(list[RegisteredRunner], batch))
            page += 1

    def check_access(self) -> None:
        if self.settings.scope.startswith("user:"):
            account = cast(dict[str, JSONValue], self.request("GET", "/user"))
            login = account["login"]
            if (
                not isinstance(login, str)
                or login.casefold() != self.settings.scope.partition(":")[2].casefold()
            ):
                raise ValueError("FORGEJO_SCOPE user must match the API token account")
        for scope in self.settings.poll_targets:
            self.list_runners(scope)
            self.waiting_jobs(scope)

    def delete_runner(self, scope: str, runner_id: int) -> None:
        try:
            self.request("DELETE", f"{runner_path(scope)}/{runner_id}")
        except HTTPError as error:
            if error.code != 404:
                raise

    def waiting_jobs(self, scope: str) -> list[Job]:
        jobs = cast(list[Job] | None, self.request("GET", f"{runner_path(scope)}/jobs"))
        waiting = [job for job in jobs or [] if job.get("status") == "waiting"]
        if "/" in scope:
            return waiting
        repositories: dict[int, str] = {}
        for job in waiting:
            repo_id = job["repo_id"]
            if repo_id not in repositories:
                repository = cast(
                    dict[str, JSONValue],
                    self.request("GET", f"/repositories/{repo_id}"),
                )
                name = repository["full_name"]
                if not isinstance(name, str):
                    raise TypeError("Repository name must be a string")
                repositories[repo_id] = name
            job["repository"] = repositories[repo_id]
        return waiting
