# Forgejo Runner Awaker

Starts a Docker runner when a Forgejo job is waiting. Each runner handles one
job, then its containers and registration are cleaned up. The controller stays
running; runners don't idle between jobs.

Jobs choose a container image and resource limits through `runs-on` labels.
Polling works with Forgejo 16; Forgejo 17 also supports job webhooks. Registration
uses the current HTTP API, without deprecated registration tokens or commands.

## Deploy

On the deployment host, build the example job images and copy the configuration:

```sh
docker compose -f examples/compose.images.yaml build
cp examples/shared.env .env
```

Edit `.env` with your Forgejo URL, API token, and organization. Then start:

```sh
docker compose up --build -d
```

Copy a workflow into your repository's `.forgejo/workflows/` directory:

- [Node tests](examples/workflows/node-test.yaml): installs npm dependencies and runs tests.
- [Tauri build](examples/workflows/tauri-build.yaml): installs Linux dependencies and Rust, builds Debian packages, and saves workflow artifacts.

Both examples expect an npm lockfile. The Tauri example also expects an npm
`tauri` script and the standard `src-tauri` directory. Release publishing needs
another workflow step.

For custom images or individual repositories, start from [.env.example](.env.example).

## Scope

Set `FORGEJO_SCOPE` to serve an organization, user, or the whole instance.
New repositories within that scope work without changing configuration.

| Scope | Projects | API token requirements |
| --- | --- | --- |
| `org:my-team` | Organization repositories | Organization owner; `write:organization`, `read:repository` |
| `user:alice` | Alice's repositories | Alice's token; `write:user`, `read:repository` |
| `instance` | All repositories | Site administrator; `write:admin`, `read:repository` |
| Empty | Repositories in `FORGEJO_REPOS` | Repository owner; `write:repository` |

`FORGEJO_REPOS=owner/repo-one,owner/repo-two` can restrict dispatch within a shared
scope. It doesn't restrict API-token permissions or runner registrations.
[Repository-restricted tokens can lose administrative permissions](https://forgejo.org/docs/v17.0/user/authentication/token-scope/).
The API token stays in the controller; each runner gets its own ephemeral token.

## Job images and limits

`JOB_PROFILES` maps workflow labels to images and resource limits. The example
configuration uses:

```json
{
  "node-small": {"image": "awaker-node:24", "memory": "512m", "cpus": 1, "pids": 128, "timeout": 600},
  "ubuntu-build": {"image": "awaker-ubuntu:26.04", "memory": "4g", "cpus": 2, "pids": 512, "timeout": 3600}
}
```

Use `runs-on: node-small` for Node tests or `runs-on: ubuntu-build` for builds.

Only `image` is required. Defaults are 2 GiB of memory, 2 CPUs, 512 PIDs, and
`RUNNER_JOB_TIMEOUT` seconds (10800 by default). Profile timeouts cannot exceed
that global limit. A job's `runs-on` labels must all name profiles with identical
settings; other labels are rejected, so every job runs under a profile's limits.
There's no built-in `ubuntu-latest` mapping: add `"ubuntu-latest"` as a profile
label if you want it.

Memory is a hard cap: the profile sets both `--memory` and `--memory-swap`, and a
job that exceeds it is killed. Workflows cannot raise it. The memory is used only
as the job needs it.

`MAX_MEMORY` (default `8g`) is the total memory budget for running jobs. A job is
dispatched only if its profile's memory fits in what is left, so `MAX_MEMORY=12g`
runs three 4 GiB jobs or twenty-four 512 MiB jobs at once. Jobs that don't fit
stay waiting until another finishes. Each profile must fit within `MAX_MEMORY`.
The runner container that supervises each job is also limited to 512 MiB; that
is not counted in the budget.

Workflows can override the image with `container.image`. JavaScript actions need
Node.js, and checkout needs Git inside the job image. Both example images include
these tools; the Ubuntu workflow installs build dependencies as needed.

## Operation

Polling checks queues every `POLL_INTERVAL` seconds (default 5). While Forgejo is
unreachable, the delay doubles up to 60 seconds and each failure logs one line;
set `LOG_LEVEL=DEBUG` for tracebacks. For Forgejo 17 webhooks, set
`WEBHOOK_SECRET` and forward `workflow_job_waiting` events to `POST /webhook`
(directly on the Docker network or through your reverse proxy). Forgejo refuses
webhook targets on private addresses unless `[webhook] ALLOWED_HOST_LIST` allows
them. With webhooks working, raise `POLL_INTERVAL` (for example 60) so polling is
only a fallback. Polling and webhooks share dispatch claims, and each runner is
bound to one job attempt.

- Run one controller process per Forgejo instance and Docker daemon. Multiple replicas or Uvicorn workers don't share claims.
- Keep the `awaker-state` volume. It stores cleanup progress, recovers runners after restarts, and retries failed cleanup. Scope changes still require permission to clean up old registrations.
- The Forgejo URL must be reachable from the controller and runners. Runners use Docker's default bridge, outside the Compose service network.
- The controller and runner containers need the host Docker socket. Job containers receive neither that socket nor privileged mode; jobs needing Docker should use an isolated daemon.

Startup checks Docker and Forgejo access. `GET /health/ready` returns 503
when those checks fail; registration also requires the write permissions above.
The default runner image is `data.forgejo.org/forgejo/runner:13`. Tags refresh at
startup, and registry failures prevent startup. Set `RUNNER_IMAGE` to use another
tag or a digest.

## Verify

Tests use mocks and don't start Docker containers or contact Forgejo:

```sh
uv sync --frozen --no-install-project
uv run --no-sync mypy
uv run --no-sync ruff check main.py awaker tests
uv run --no-sync python -m unittest discover -s tests -v
```

On the deployment host, run a small workflow and check that its runner
registration, containers, and anonymous volumes disappear afterwards.

See Forgejo's [runner registration](https://forgejo.org/docs/v17.0/admin/actions/registration/)
and [workflow reference](https://forgejo.org/docs/v17.0/user/actions/reference/) for upstream details.
