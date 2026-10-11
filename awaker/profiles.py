import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from docker.errors import DockerException
from docker.utils import parse_bytes


@dataclass(frozen=True)
class JobProfile:
    image: str
    memory: str = "2g"
    cpus: float = 2
    pids: int = 512
    timeout: int = 10800

    @property
    def limits(self) -> str:
        return f"--memory={self.memory} --cpus={self.cpus:g} --pids-limit={self.pids}"

    def validate(self, ceiling: int) -> None:
        if (
            not self.image
            or "://" in self.image
            or any(c.isspace() for c in self.image)
        ):
            raise ValueError("Profile image must be a Docker image reference")
        if (
            any(c.isspace() for c in self.memory)
            or parse_bytes(self.memory) <= 0
            or not math.isfinite(self.cpus)
            or self.cpus <= 0
        ):
            raise ValueError("Profile memory and CPUs must be positive")
        if self.pids <= 0 or self.timeout <= 0 or self.timeout > ceiling:
            raise ValueError(
                "Profile PIDs must be positive and timeout must fit RUNNER_JOB_TIMEOUT"
            )


def parse_profiles(raw: str, ceiling: int) -> dict[str, JobProfile]:
    try:
        payload: object = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError
        profiles: dict[str, JobProfile] = {}
        for label, values in payload.items():
            if (
                not isinstance(label, str)
                or not label
                or ":" in label
                or not isinstance(values, dict)
            ):
                raise TypeError
            if set(values) - {"image", "memory", "cpus", "pids", "timeout"}:
                raise ValueError("Unknown profile fields")
            image = values["image"]
            memory = values.get("memory", "2g")
            cpus = values.get("cpus", 2)
            pids = values.get("pids", 512)
            timeout = values.get("timeout", ceiling)
            if (
                not isinstance(image, str)
                or not isinstance(memory, str)
                or type(cpus) not in (int, float)
                or type(pids) is not int
                or type(timeout) is not int
            ):
                raise TypeError
            profile = JobProfile(image, memory, cpus, pids, timeout)
            profile.validate(ceiling)
            profiles[label] = profile
        return profiles
    except (ValueError, TypeError, KeyError, DockerException) as error:
        raise ValueError(f"Invalid JOB_PROFILES: {error}") from error


def select_profile(
    labels: Sequence[str], profiles: dict[str, JobProfile]
) -> JobProfile | None:
    selected = {profiles[label] for label in labels if label in profiles}
    if len(selected) > 1:
        raise ValueError("Job labels select conflicting resource profiles")
    return selected.pop() if selected else None
