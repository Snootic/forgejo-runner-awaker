import asyncio
import logging
import threading

import docker
from docker.models.containers import Container
from fastapi.concurrency import run_in_threadpool

from .claims import Claims, claim_key
from .forgejo import Forgejo
from .state import State

logger = logging.getLogger(__name__)


class Cleanup:
    def __init__(
        self,
        state: State,
        client: docker.DockerClient,
        forgejo: Forgejo,
        claims: Claims,
    ) -> None:
        self.state = state
        self.docker = client
        self.forgejo = forgejo
        self.claims = claims
        self.lock = threading.Lock()

    def finish(self, name: str, container: Container | None = None) -> None:
        with self.lock:
            entry = self.state.get(name)
            if entry is None:
                return
            self.state.mark_pending(name)
            try:
                if container is None:
                    container = self.docker.containers.get(entry["container_name"])
                container.remove(force=True, v=True)
            except docker.errors.NotFound:
                pass
            except Exception:
                logger.exception("%s: Docker cleanup failed; will retry", name)
                return
            if not entry["released"]:
                self.state.mark_released(name)
                self.claims.release(claim_key(entry["repo"], entry["handle"]))
            try:
                scope = entry["runner_scope"] or entry["repo"]
                if entry["runner_id"] is None:
                    runner_ids = [
                        runner["id"]
                        for runner in self.forgejo.list_runners(scope)
                        if runner["name"] == name
                    ]
                    if not runner_ids:
                        return
                else:
                    runner_ids = [entry["runner_id"]]
                for runner_id in runner_ids:
                    self.forgejo.delete_runner(scope, runner_id)
                self.state.forget(name)
            except Exception:
                logger.exception("%s: Forgejo cleanup failed; will retry", name)

    def retry(self) -> None:
        for entry in self.state.entries():
            if entry["pending"]:
                self.finish(entry["name"])


async def cleanup_forever(cleanup: Cleanup, interval: float) -> None:
    while True:
        try:
            await run_in_threadpool(cleanup.retry)
        except Exception:
            logger.exception("cleanup retry failed")
        await asyncio.sleep(interval)
