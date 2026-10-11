from collections.abc import Mapping, Sequence

from docker import DockerClient


def list_environments(
    client: DockerClient, configured: Mapping[str, str] | None = None
) -> dict[str, str]:
    configured = configured or {}
    environments = {label: f"docker://{image}" for label, image in configured.items()}
    for image in client.images.list():
        for tag in image.tags:
            repository, separator, _ = tag.rpartition(":")
            name = repository.rsplit("/", 1)[-1]
            if not separator or not name.endswith("-runner"):
                continue
            label = name.removesuffix("-runner")
            if not label or label in configured:
                continue
            target = f"docker://{tag}"
            if label in environments and environments[label] != target:
                raise ValueError(
                    f"Ambiguous runner image for label {label!r}; set JOB_IMAGES explicitly"
                )
            environments[label] = target
    return environments


def runner_labels(
    job_labels: Sequence[str], environments: Mapping[str, str]
) -> list[str] | None:
    if not job_labels or any(label not in environments for label in job_labels):
        return None
    images = {environments[label] for label in job_labels}
    if len(images) != 1:
        return None
    image = images.pop()
    return [f"{label}:{image}" for label in job_labels]
