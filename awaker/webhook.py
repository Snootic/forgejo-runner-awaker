import hashlib
import hmac
import json
from typing import cast

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from .images import list_environments
from .models import Job
from .runners import Runners

WAITING_EVENT = "workflow_job_waiting"


def waiting_delivery(body: bytes) -> tuple[str, Job] | None:
    try:
        payload = json.loads(body)
        action = payload["action"]
        job = payload["job"]
        repo = payload["run"]["repository"]["full_name"]
        if (
            not isinstance(action, str)
            or not isinstance(job, dict)
            or not isinstance(repo, str)
        ):
            raise TypeError
        if not isinstance(job.get("handle"), str) or not job["handle"]:
            raise ValueError
        labels = job.get("runs_on")
        if not isinstance(labels, list) or any(
            not isinstance(label, str) for label in labels
        ):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Malformed workflow job payload") from None

    if (
        action not in {"new_job_attempt", "job_status_changed"}
        or job.get("status") != "waiting"
    ):
        return None
    return repo, cast(Job, job)


async def handle_webhook(request: Request) -> dict[str, str]:
    runners = cast(Runners, request.app.state.runners)
    secret = runners.settings.webhook_secret
    if not secret:
        raise HTTPException(503, "WEBHOOK_SECRET is required for deliveries")
    body = await request.body()
    signature = request.headers.get("X-Forgejo-Signature", "")
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise HTTPException(401, "Invalid signature")
    if request.headers.get("X-Forgejo-Event") != WAITING_EVENT:
        return {"status": "Event ignored"}
    delivery = waiting_delivery(body)
    if delivery is None:
        return {"status": "Job ignored"}
    repo, job = delivery
    if not runners.settings.accepts_repo(repo):
        return {"status": "Repository not served"}
    environments = await run_in_threadpool(
        list_environments, runners.docker, runners.settings.images
    )
    return {"status": runners.dispatch(repo, job, environments)}
