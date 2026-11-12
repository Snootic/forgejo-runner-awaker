import asyncio
import logging

from fastapi.concurrency import run_in_threadpool

from .images import list_environments
from .runners import Runners

logger = logging.getLogger(__name__)
MAX_BACKOFF = 60.0


def retry_delay(interval: float, failures: int) -> float:
    """Poll interval, doubled per consecutive failed cycle, capped at one minute."""
    if not failures:
        return interval
    return min(interval * 2.0**failures, max(interval, MAX_BACKOFF))


async def poll_forever(runners: Runners) -> None:
    failures = 0
    while True:
        environments = None
        failed = False
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
            except Exception as error:
                failed = True
                logger.error(
                    "polling %s failed: %r",
                    scope,
                    error,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
        if failed:
            failures += 1
        elif failures:
            logger.info("polling recovered after %s failed cycles", failures)
            failures = 0
        await asyncio.sleep(retry_delay(runners.settings.poll_interval, failures))
