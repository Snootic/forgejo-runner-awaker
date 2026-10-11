import asyncio
import logging

from fastapi.concurrency import run_in_threadpool

from .images import list_environments
from .runners import Runners

logger = logging.getLogger(__name__)


async def poll_forever(runners: Runners) -> None:
    while True:
        environments = None
        for scope in runners.settings.poll_targets:
            try:
                jobs = await run_in_threadpool(runners.forgejo.waiting_jobs, scope)
                if not jobs:
                    continue
                if environments is None:
                    environments = await run_in_threadpool(
                        list_environments, runners.docker, runners.settings.images
                    )
                for job in jobs:
                    repo = job.get("repository", scope)
                    result = runners.dispatch(repo, job, environments)
                    logger.debug(
                        "%s job %s: %s",
                        repo,
                        job.get("id"),
                        result,
                    )
            except Exception:
                logger.exception("polling %s failed", scope)
        await asyncio.sleep(runners.settings.poll_interval)
