import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager, suppress
from typing import cast

import docker
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .cleanup import cleanup_forever
from .config import Settings
from .forgejo import Forgejo
from .images import list_environments
from .polling import poll_forever
from .runners import Runners
from .state import State
from .webhook import handle_webhook

logger = logging.getLogger(__name__)


async def check_dependencies(
    client: docker.DockerClient, forgejo: Forgejo, job_images: Mapping[str, str]
) -> None:
    await run_in_threadpool(client.ping)
    await run_in_threadpool(forgejo.check_access)
    environments = await run_in_threadpool(list_environments, client, job_images)
    if not environments:
        raise ValueError(
            "No job images available; set JOB_IMAGES, JOB_PROFILES, or tag an image <label>-runner"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    client = docker.from_env()
    forgejo = Forgejo(settings)
    await check_dependencies(client, forgejo, settings.images)
    await prepare_runner_image(client, settings.runner_image)
    state = State(settings.state_path, settings.url)
    runners = Runners(settings, client, forgejo, state)
    await run_in_threadpool(runners.recover_runners)
    app.state.runners = runners
    tasks = (
        asyncio.create_task(poll_forever(runners)),
        asyncio.create_task(cleanup_forever(runners.cleanup, settings.poll_interval)),
    )
    logger.info(
        "serving %s; capacity %s",
        ", ".join(settings.poll_targets),
        settings.max_runners,
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


async def prepare_runner_image(client: docker.DockerClient, image: str) -> None:
    if "@" in image:
        try:
            await run_in_threadpool(client.images.get, image)
            return
        except docker.errors.ImageNotFound:
            pass
    await run_in_threadpool(client.images.pull, image)


app = FastAPI(lifespan=lifespan)
app.add_api_route("/webhook", handle_webhook, methods=["POST"])


@app.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    runners = cast(Runners, request.app.state.runners)
    try:
        await check_dependencies(
            runners.docker, runners.forgejo, runners.settings.images
        )
    except Exception:
        logger.exception("readiness check failed")
        raise HTTPException(
            503, "Dependencies unavailable or configuration invalid"
        ) from None
    return {"status": "ready"}
