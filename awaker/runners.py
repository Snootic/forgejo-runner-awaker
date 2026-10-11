import logging
import threading
from collections.abc import Mapping, Sequence
from dataclasses import replace
from os import stat
from urllib.error import HTTPError

from docker import DockerClient
from docker.models.containers import Container

from .claims import Claims, claim_key
from .cleanup import Cleanup
from .config import Settings
from .execution import runner_script
from .forgejo import Forgejo
from .images import runner_labels
from .models import Job, StateEntry
from .profiles import select_profile
from .state import State

logger = logging.getLogger(__name__)
DOCKER_SOCKET = "/var/run/docker.sock"
MANAGED_LABEL = "org.forgejo.awaker"


class Runners:
    def __init__(
        self, settings: Settings, client: DockerClient, forgejo: Forgejo, state: State
    ) -> None:
        self.settings = settings
        self.docker = client
        self.forgejo = forgejo
        self.claims = Claims(settings.max_runners)
        self.state = state
        self.cleanup = Cleanup(state, client, forgejo, self.claims)

    def supervise_runner(
        self, repo: str, handle: str, name: str, container: Container
    ) -> None:
        try:
            status = container.wait(timeout=self.settings.job_timeout + 60)[
                "StatusCode"
            ]
            if status:
                logger.error(
                    "%s job %s: runner exited %s\n%s",
                    repo,
                    handle,
                    status,
                    container.logs().decode(errors="replace")[-4000:],
                )
            else:
                logger.info("%s job %s: runner finished", repo, handle)
        except Exception:
            logger.exception("%s job %s: runner supervision failed", repo, handle)
        finally:
            self.cleanup.finish(name, container)

    def run_runner(
        self,
        repo: str,
        job: Job,
        labels: Sequence[str],
        job_settings: Settings | None = None,
    ) -> None:
        job_settings = job_settings or self.settings
        handle = job["handle"]
        name = None
        runner = None
        container = None
        try:
            scope = self.settings.scope or repo
            name = self.state.begin(repo, handle, runner_scope=scope)
            runner = self.forgejo.create_runner(scope, name)
            self.state.registered(name, runner["id"])
            container = self.docker.containers.create(
                self.settings.runner_image,
                name=name,
                command=[
                    "sh",
                    "-c",
                    runner_script(job_settings, labels, runner["uuid"], handle),
                ],
                environment={"RUNNER_TOKEN": runner["token"]},
                labels={
                    MANAGED_LABEL: self.settings.url,
                    f"{MANAGED_LABEL}.repo": repo,
                    f"{MANAGED_LABEL}.handle": handle,
                    f"{MANAGED_LABEL}.runner-id": str(runner["id"]),
                    f"{MANAGED_LABEL}.scope": scope,
                },
                group_add=[str(stat(DOCKER_SOCKET).st_gid)],
                security_opt=["label:disable"],
                volumes={DOCKER_SOCKET: {"bind": DOCKER_SOCKET, "mode": "rw"}},
            )
            container.start()
        except Exception as error:
            logger.exception("%s job %s: runner could not start", repo, handle)
            if (
                name
                and not runner
                and isinstance(error, HTTPError)
                and 400 <= error.code < 500
            ):
                self.state.forget(name)
                self.claims.release(claim_key(repo, handle))
            elif name:
                self.cleanup.finish(name, container)
            else:
                self.claims.release(claim_key(repo, handle))
            return
        logger.info("%s job %s: %s started", repo, handle, name)
        self.supervise_runner(repo, handle, name, container)

    def recover_runners(self) -> None:
        containers = self.docker.containers.list(
            all=True, filters={"label": f"{MANAGED_LABEL}={self.settings.url}"}
        )
        entries = {entry["container_name"]: entry for entry in self.state.entries()}
        recovered: list[tuple[StateEntry, Container]] = []
        for container in containers:
            container_name = container.name
            if container_name is None:
                raise ValueError("Managed runner container has no name")
            entry = entries.pop(container_name, None)
            if entry is None:
                labels = container.labels
                repo = labels[f"{MANAGED_LABEL}.repo"]
                name = self.state.begin(
                    repo,
                    labels[f"{MANAGED_LABEL}.handle"],
                    int(labels[f"{MANAGED_LABEL}.runner-id"]),
                    container_name,
                    labels.get(f"{MANAGED_LABEL}.scope", repo),
                )
                entry = self.state.get(name)
                assert entry is not None
            if not entry["released"]:
                self.claims.restore(claim_key(entry["repo"], entry["handle"]))
            recovered.append((entry, container))
        for entry in entries.values():
            if not entry["released"]:
                self.claims.restore(claim_key(entry["repo"], entry["handle"]))
            self.state.mark_pending(entry["name"])
        for entry, container in recovered:
            if entry["pending"] or container.status in {"created", "exited", "dead"}:
                self.cleanup.finish(entry["name"], container)
            else:
                threading.Thread(
                    target=self.supervise_runner,
                    args=(entry["repo"], entry["handle"], entry["name"], container),
                    daemon=True,
                ).start()
        self.cleanup.retry()
        logger.info("recovered %s runner containers", len(recovered))

    def dispatch(self, repo: str, job: Job, environments: Mapping[str, str]) -> str:
        if not self.settings.accepts_repo(repo):
            return "Repository not served"
        requested_labels = job.get("runs_on") or []
        labels = runner_labels(requested_labels, environments)
        if labels is None:
            return f"No runner image for labels {job.get('runs_on')}"
        try:
            profile = select_profile(requested_labels, self.settings.job_profiles)
        except ValueError as error:
            return str(error)
        job_settings = self.settings
        if profile is not None:
            job_settings = replace(
                self.settings, job_limits=profile.limits, job_timeout=profile.timeout
            )
        key = claim_key(repo, job["handle"])
        if not self.claims.acquire(key):
            return "Already dispatched, or at runner capacity"
        try:
            threading.Thread(
                target=self.run_runner,
                args=(repo, job, labels, job_settings),
                daemon=True,
            ).start()
        except Exception:
            self.claims.release(key)
            raise
        return "Runner dispatched"
