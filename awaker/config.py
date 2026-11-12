import math
import os
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import urlsplit

from docker.errors import DockerException
from docker.utils import parse_bytes

from .profiles import JobProfile, parse_profiles
from .scopes import accepts_repo, validate_scope

DEFAULT_RUNNER_IMAGE = "data.forgejo.org/forgejo/runner:13"
DEFAULT_MAX_MEMORY = "8g"


@dataclass(frozen=True)
class Settings:
    url: str
    repos: tuple[str, ...]
    api_token: str
    webhook_secret: bytes = b""
    poll_interval: float = 5
    max_memory: int = int(parse_bytes(DEFAULT_MAX_MEMORY))
    job_timeout: int = 10800
    runner_image: str = DEFAULT_RUNNER_IMAGE
    log_level: str = "INFO"
    state_path: str = "./state/awaker.sqlite3"
    scope: str = ""
    job_profiles: dict[str, JobProfile] = field(default_factory=dict)

    @property
    def poll_targets(self) -> tuple[str, ...]:
        return (self.scope,) if self.scope else self.repos

    def accepts_repo(self, repo: str) -> bool:
        return accepts_repo(repo, self.scope, self.repos)

    @classmethod
    def from_env(cls) -> Self:
        def value(name: str, default: str = "") -> str:
            return os.getenv(name) or default

        try:
            poll_interval = float(value("POLL_INTERVAL", "5"))
            job_timeout = int(value("RUNNER_JOB_TIMEOUT", "10800"))
        except ValueError as error:
            raise ValueError(f"Invalid numeric configuration: {error}") from error

        try:
            max_memory = int(parse_bytes(value("MAX_MEMORY", DEFAULT_MAX_MEMORY)))
        except DockerException as error:
            raise ValueError(f"Invalid MAX_MEMORY: {error}") from error

        settings = cls(
            url=value("FORGEJO_API_URL").rstrip("/"),
            repos=tuple(
                dict.fromkeys(value("FORGEJO_REPOS").replace(",", " ").split())
            ),
            api_token=value("FORGEJO_API_TOKEN"),
            webhook_secret=value("WEBHOOK_SECRET").encode(),
            poll_interval=poll_interval,
            max_memory=max_memory,
            job_timeout=job_timeout,
            runner_image=value("RUNNER_IMAGE", DEFAULT_RUNNER_IMAGE),
            log_level=value("LOG_LEVEL", "INFO").upper(),
            state_path=value("STATE_PATH", cls.state_path),
            scope=value("FORGEJO_SCOPE"),
            job_profiles=parse_profiles(value("JOB_PROFILES", "{}"), job_timeout),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.job_profiles:
            raise ValueError("JOB_PROFILES must define at least one profile")
        if any(
            profile.memory_bytes > self.max_memory
            for profile in self.job_profiles.values()
        ):
            raise ValueError("A JOB_PROFILES memory limit exceeds MAX_MEMORY")
        url = urlsplit(self.url)
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("FORGEJO_API_URL must be an absolute HTTP(S) URL")
        if not self.api_token:
            raise ValueError("FORGEJO_API_TOKEN is required")
        validate_scope(self.scope)
        if (not self.repos and not self.scope) or any(
            repo.count("/") != 1 or not all(repo.split("/")) for repo in self.repos
        ):
            raise ValueError("FORGEJO_REPOS must list owner/repo entries")
        for name, number in (
            ("POLL_INTERVAL", self.poll_interval),
            ("MAX_MEMORY", self.max_memory),
            ("RUNNER_JOB_TIMEOUT", self.job_timeout),
        ):
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
