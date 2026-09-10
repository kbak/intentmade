"""Native DockerWorkspace with one disposable Docker test daemon per job."""

import os
import secrets
import subprocess
from contextlib import contextmanager
from pathlib import Path

from common import ROOT, api
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.utils.cipher import Cipher
from openhands.workspace import DockerWorkspace
from openhands.workspace.docker.workspace import find_available_tcp_port


def sync_credential(parent, version, previous, refreshed):
    if refreshed == previous:
        return
    try:
        parent.replace_versioned_secret("CODEX_AUTH_JSON", version, refreshed)
    except ValueError as exc:
        if str(exc) != "credential_version_conflict":
            raise
        # Another native conversation/worker saved a newer binding. Never roll
        # it back, and do not turn a successful build into a cleanup failure.
        print("Native credential store retained a newer login version.", flush=True)


def mounts(root, config):
    volumes = [f"{root}:{root}"]
    configs = config if isinstance(config, list) else [config]
    for profile in sorted({c["test_profile"] for c in configs if c.get("test_profile")}):
        volumes.append(f"/profiles/{profile}:/factory-tests/{profile}:ro")
    return volumes


@contextmanager
def worker(root, config):
    # Resolve against Canvas before replacing its API key with the worker's.
    # Only the model/effort crosses this boundary; permissions remain per role.
    profile = api("GET", "/api/agent-profiles/factory-codex")["profile"]
    name = root.name
    compose = ["docker", "compose", "-p", name, "-f", str(ROOT / "workflows/sandbox.yaml")]
    env = dict(os.environ, JOB_WORKSPACE=str(root))
    encryption_key = (
        os.environ.get("OH_SECRET_KEY") or Path("/run/secrets/encryption-key").read_text().strip()
    )
    parent = FileSecretsStore("/home/openhands/.openhands", Cipher(encryption_key))
    value, version = parent.load_versioned_secret("CODEX_AUTH_JSON")
    settings = {
        "OH_SESSION_API_KEYS_0": secrets.token_urlsafe(32),
        "OH_CONVERSATION_WORKTREE_ROOT": str(root / "worktrees"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FACTORY_CODEX_MODEL": profile.get("acp_model") or "",
    }
    old = {key: os.environ.get(key) for key in settings}
    try:
        subprocess.run([*compose, "up", "-d", "--wait"], env=env, check=True)
        os.environ.update(settings)
        port = find_available_tcp_port()
        with DockerWorkspace(
            server_image=os.environ.get("FACTORY_IMAGE", "openhands-factory:dev"),
            host_port=port,
            host=f"http://sandboxes:{port}",
            working_dir=str(root / "source"),
            network=name + "_default",
            detach_logs=False,
            forward_env=list(settings),
            volumes=mounts(root, config),
        ) as workspace:
            workspace.api_key = settings["OH_SESSION_API_KEYS_0"]
            workspace.reset_client()
            workspace.client.put(
                "/api/settings/secrets",
                json={
                    "name": "CODEX_AUTH_JSON",
                    "value": value,
                },
            ).raise_for_status()
            try:
                yield workspace
            finally:
                refreshed = workspace.client.get("/api/settings/secrets/CODEX_AUTH_JSON")
                refreshed.raise_for_status()
                sync_credential(parent, version, value, refreshed.text)
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], env=env, check=True)
