import json
import math
import os
from dataclasses import dataclass, field
from typing import Self
from urllib.parse import urlsplit

from .profiles import JobProfile, parse_profiles
from .scopes import accepts_repo, validate_scope

DEFAULT_RUNNER_IMAGE = "data.forgejo.org/forgejo/runner:13"


@dataclass(frozen=True)
class Settings:
    url: str
    repos: tuple[str, ...]
    api_token: str
    webhook_secret: bytes = b""
    poll_interval: float = 5
    max_runners: int = 4
    job_timeout: int = 10800
    job_limits: str = "--memory=2g --cpus=2 --pids-limit=512"
    runner_image: str = DEFAULT_RUNNER_IMAGE
    log_level: str = "INFO"
    state_path: str = "./state/awaker.sqlite3"
    job_images: dict[str, str] = field(default_factory=dict)
    scope: str = ""
    job_profiles: dict[str, JobProfile] = field(default_factory=dict)

    @property
    def poll_targets(self) -> tuple[str, ...]:
        return (self.scope,) if self.scope else self.repos

    @property
    def images(self) -> dict[str, str]:
        return self.job_images | {
            label: profile.image for label, profile in self.job_profiles.items()
        }

    def accepts_repo(self, repo: str) -> bool:
        return accepts_repo(repo, self.scope, self.repos)

    @classmethod
    def from_env(cls) -> Self:
        def value(name: str, default: str = "") -> str:
            return os.getenv(name) or default

        try:
            poll_interval = float(value("POLL_INTERVAL", "5"))
            max_runners = int(value("MAX_RUNNERS", "4"))
            job_timeout = int(value("RUNNER_JOB_TIMEOUT", "10800"))
        except ValueError as error:
            raise ValueError(f"Invalid numeric configuration: {error}") from error

        try:
            images = json.loads(value("JOB_IMAGES", "{}"))
            if not isinstance(images, dict):
                raise TypeError
        except (ValueError, TypeError):
            raise ValueError(
                "JOB_IMAGES must be a JSON object of labels and images"
            ) from None

        settings = cls(
            url=value("FORGEJO_API_URL").rstrip("/"),
            repos=tuple(
                dict.fromkeys(value("FORGEJO_REPOS").replace(",", " ").split())
            ),
            api_token=value("FORGEJO_API_TOKEN"),
            webhook_secret=value("WEBHOOK_SECRET").encode(),
            poll_interval=poll_interval,
            max_runners=max_runners,
            job_timeout=job_timeout,
            job_limits=value("JOB_LIMITS", cls.job_limits),
            runner_image=value("RUNNER_IMAGE", DEFAULT_RUNNER_IMAGE),
            log_level=value("LOG_LEVEL", "INFO").upper(),
            state_path=value("STATE_PATH", cls.state_path),
            scope=value("FORGEJO_SCOPE"),
            job_images=images,
            job_profiles=parse_profiles(value("JOB_PROFILES", "{}"), job_timeout),
        )
        settings.validate()
        if any(
            label in settings.job_images and settings.job_images[label] != profile.image
            for label, profile in settings.job_profiles.items()
        ):
            raise ValueError("JOB_IMAGES and JOB_PROFILES disagree on an image")
        return settings

    def validate(self) -> None:
        if any(
            not isinstance(label, str)
            or not label
            or ":" in label
            or not isinstance(image, str)
            or not image
            or any(c.isspace() for c in image)
            or "://" in image
            for label, image in self.job_images.items()
        ):
            raise ValueError(
                "JOB_IMAGES must map nonempty labels to Docker image references"
            )
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
            ("MAX_RUNNERS", self.max_runners),
            ("RUNNER_JOB_TIMEOUT", self.job_timeout),
        ):
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
