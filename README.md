# Forgejo Runner Awaker

A webhook listener that starts an ephemeral Forgejo runner when a workflow job is queued.

## Run

Set `FORGEJO_API_URL` and `REGISTRATION_TOKEN`, then start the service:

```sh
docker compose up --build -d
```

Configure Forgejo to send `workflow_job` webhooks to `POST /webhook`. The service needs access to `/var/run/docker.sock` to start runner containers.
