import json
import shlex
from collections.abc import Sequence

from .profiles import JobProfile


def runner_config(profile: JobProfile) -> str:
    return f"""runner:
  capacity: 1
  timeout: {profile.timeout}s
container:
  network: ""
  privileged: false
  docker_host: "-"
  valid_volumes: []
  force_pull: false
  options: {json.dumps(profile.limits)}
"""


def runner_script(
    profile: JobProfile, url: str, labels: Sequence[str], uuid: str, handle: str
) -> str:
    label_flags = " ".join(f"--label {shlex.quote(label)}" for label in labels)
    return f"""set -eu
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
printf '%s' "$RUNNER_TOKEN" > "$WORK_DIR/runner-token"
unset RUNNER_TOKEN
cat > "$WORK_DIR/config.yaml" <<'RUNNER_CONFIG'
{runner_config(profile)}RUNNER_CONFIG
timeout -k 30s {profile.timeout} forgejo-runner one-job \\
    --config "$WORK_DIR/config.yaml" \\
    --url {shlex.quote(url)} \\
    --uuid {shlex.quote(uuid)} \\
    --token-url "file:$WORK_DIR/runner-token" \\
    {label_flags} \\
    --handle {shlex.quote(handle)}
"""
