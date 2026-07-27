import docker
import subprocess

from os import getenv
from uuid import uuid4
from fastapi import FastAPI, Request, BackgroundTasks

FORGEJO_URL = getenv("FORGEJO_API_URL")
REG_TOKEN = getenv("REGISTRATION_TOKEN")
EXEC_MODE = getenv("EXEC_MODE", "docker").lower()

app = FastAPI()

if EXEC_MODE == "docker":
    client = docker.from_env()

def list_environments(): # TODO: map host configutarion, not only docker
    images = client.images.list(filters={"reference": "*-runner"})

    labels = {
        "ubuntu-latest": "ubuntu-latest:docker://node:lts"
    }

    for img in images:
        for image_tag in img.tags:
            repo_name, _tag = image_tag.split(":", 1)

            suffix = "-runner"
            if repo_name.endswith(suffix):
                label_name = repo_name[:-len(suffix)]

                labels[label_name] = f"{label_name}:docker://{image_tag}"

    return labels

def spawn_ephemeral_runner(label_config: str):
    job_id = uuid4().hex[:8] 

    script = f"""
    # Create an isolated workspace for this specific runner instance
    WORK_DIR=$(mktemp -d)
    cd $WORK_DIR

    forgejo-runner register --no-interactive \\
        --url {FORGEJO_URL} \\
        --token {REG_TOKEN} \\
        --ephemeral \\
        --name "on-demand-{job_id}" \\
        --labels "{label_config}"

    UUID=$(grep -o '"uuid": "[^"]*' .runner | cut -d'"' -f4)
    TOKEN=$(grep -o '"token": "[^"]*' .runner | cut -d'"' -f4)
    echo -n $TOKEN > token_file

    timeout 300 forgejo-runner one-job \\
        --url {FORGEJO_URL} \\
        --uuid $UUID \\
        --token-url file:token_file

    # Clean up the isolated workspace when finished
    cd ..
    rm -rf $WORK_DIR
    """

    if EXEC_MODE == "docker":
        client.containers.run(
            "code.forgejo.org/forgejo/runner:latest",
            command=["sh", "-c", script],
            detach=True,
            remove=True,
            volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}}
        )
    elif EXEC_MODE == "host":
        subprocess.Popen(["sh", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    event = request.headers.get("X-Forgejo-Event")

    if event != "workflow_job":
        return {"status": "Event ignored"}

    payload = await request.json()

    if payload.get("action") == "queued":
        job_labels = payload.get("workflow_job", {}).get("labels", [])
        requested_label = job_labels[0] if job_labels else "ubuntu-latest"

        runners = list_environments()
        if requested_label not in runners:
            return {"status": f"No runner available for label '{requested_label}'"}

        target_config = runners[requested_label]

        background_tasks.add_task(spawn_ephemeral_runner, target_config)

    return {"status": "Runner dispatched"}
