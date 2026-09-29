"""Shared worker credentials and provenance with an operator-selected runtime."""

import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import diagnostics
import docker_sandboxes
import input_artifacts
import job_files
import measurements
import provenance
from common import ROOT, api
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.utils.cipher import Cipher
from openhands.workspace import DockerWorkspace
from openhands.workspace.docker.workspace import find_available_tcp_port
from resource_limits import require_disk_space, worker_docker_flags
from resource_limits import settings as resource_settings


def load_credential(parent):
    try:
        return parent.load_versioned_secret("CODEX_AUTH_JSON")
    except KeyError as exc:
        if exc.args != ("CODEX_AUTH_JSON",):
            raise
        raise RuntimeError("Native Codex login is unavailable") from None


# [impl->req~im-worker-credentials~1]
def sync_credential(parent, version, previous, refreshed):
    if refreshed == previous:
        return
    try:
        parent.replace_versioned_secret("CODEX_AUTH_JSON", version, refreshed)
    except KeyError as exc:
        if exc.args != ("CODEX_AUTH_JSON",):
            raise
        # A logout during execution must not restore the worker's old login.
        print("Native credential store retained a concurrent logout.", flush=True)
    except ValueError as exc:
        if str(exc) != "credential_version_conflict":
            raise
        # Another native conversation/worker saved a newer binding. Never roll
        # it back, and do not turn a successful build into a cleanup failure.
        print("Native credential store retained a newer login version.", flush=True)


# [impl->req~im-input-bytes~1]
def mounts(root, config):
    volumes = [f"{root}:{root}"]
    if input_artifacts.CURRENT.get():
        volumes.append(f"{input_artifacts.directory(root)}:/factory-inputs:ro")
    configs = config if isinstance(config, list) else [config]
    for profile in sorted({c["test_profile"] for c in configs if c.get("test_profile")}):
        volumes.append(f"/profiles/{profile}:/factory-tests/{profile}:ro")
    return volumes


# [impl->req~im-disk-admission~1]
# [impl->req~im-execution-provenance~1]
# [impl->req~im-worker-credentials~1]
@contextmanager
def worker(root, config):
    # Resolve against Canvas before replacing its API key with the worker's.
    # Only the model/effort crosses this boundary; permissions remain per role.
    profile = api("GET", "/api/agent-profiles/factory-codex")["profile"]
    require_disk_space(root)
    name = root.name
    options = docker_sandboxes.settings()
    parent = None
    if options["backend"] == "docker":
        encryption_key = (
            os.environ.get("OH_SECRET_KEY")
            or Path("/run/secrets/encryption-key").read_text().strip()
        )
        parent = FileSecretsStore("/home/openhands/.openhands", Cipher(encryption_key))
        value, version = load_credential(parent)
    settings = {
        "OH_SESSION_API_KEYS_0": secrets.token_urlsafe(32),
        "OH_CONVERSATION_WORKTREE_ROOT": str(root / "worktrees"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FACTORY_CODEX_MODEL": profile.get("acp_model") or "",
        "FACTORY_EXECUTION_MANIFEST": str(root / ".factory-execution.json"),
        "FACTORY_HEADLESS": "1",
    }
    old = {key: os.environ.get(key) for key in settings}
    try:
        os.environ.update(settings)
        runtime = (
            docker_sandboxes.worker(root, mounts(root, config), settings, options)
            if options["backend"] == "docker-sandboxes"
            else docker_worker(root, config, settings)
        )
        with runtime as workspace:
            workspace.api_key = settings["OH_SESSION_API_KEYS_0"]
            workspace.reset_client()
            configs = config if isinstance(config, list) else [config]
            manifest = provenance.capture(
                workspace,
                intended={
                    "worker_image": getattr(workspace, "server_image", None),
                    "worker_runtime": options,
                },
                source={"job": name},
            )
            job_files.write_text(
                root,
                Path(settings["FACTORY_EXECUTION_MANIFEST"]),
                json.dumps(manifest, indent=2) + "\n",
            )
            recorder = measurements.CURRENT.get()
            if recorder:
                # Store controller observations before the worker can edit its copy.
                retained = recorder.path.parent / f"execution-environment-{manifest['id']}.json"
                with retained.open("x") as handle:
                    json.dump(manifest, handle, indent=2)
                recorder.data.setdefault("execution_environments", []).append(manifest)
                recorder.save()
            for selected in configs:
                provenance.required(manifest, selected.get("required_environment", {}))
            # Native sbx owns OAuth on the host. Leaving this store empty also
            # prevents OpenHands from automatically binding a real auth.json.
            if parent is not None:
                workspace.client.put(
                    "/api/settings/secrets",
                    json={"name": "CODEX_AUTH_JSON", "value": value},
                ).raise_for_status()
            try:
                yield workspace
            finally:
                primary_failure = sys.exc_info()[0] is not None
                credentials = [settings["OH_SESSION_API_KEYS_0"]]
                try:
                    if parent is not None:
                        credentials.append(value)
                        refreshed = workspace.client.get("/api/settings/secrets/CODEX_AUTH_JSON")
                        refreshed.raise_for_status()
                        credentials.append(refreshed.text)
                        sync_credential(parent, version, value, refreshed.text)
                except Exception as exc:
                    if not primary_failure:
                        raise
                    print(f"Credential cleanup failed: {type(exc).__name__}", flush=True)
                finally:
                    if recorder:
                        try:
                            path = diagnostics.retain_worker(
                                workspace, recorder.path.parent, credentials
                            )
                            if path:
                                recorder.data.setdefault("worker_logs", []).append(str(path))
                                recorder.save()
                        except Exception as exc:
                            print(
                                f"Worker diagnostics unavailable: {type(exc).__name__}", flush=True
                            )
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def docker_worker(root, config, settings):
    name = root.name
    compose = ["docker", "compose", "-p", name, "-f", str(ROOT / "workflows/sandbox.yaml")]
    env = dict(
        os.environ,
        JOB_WORKSPACE=str(root),
        JOB_INPUTS=input_artifacts.directory(root),
        JOB_TEST_DAEMON_PIDS=str(resource_settings()["test_daemon_pids"]),
    )
    try:
        subprocess.run([*compose, "up", "-d", "--wait"], env=env, check=True)
        port = find_available_tcp_port()
        with DockerWorkspace(
            server_image=os.environ.get("FACTORY_IMAGE", "intentmade:dev"),
            host_port=port,
            host=f"http://sandboxes:{port}",
            working_dir=str(root / "source"),
            network=name + "_default",
            detach_logs=False,
            forward_env=list(settings),
            volumes=mounts(root, config),
        ) as workspace:
            # This SDK has no resource kwargs. Use Docker's native update before
            # provisioning credentials, running probes, or exposing the worker.
            # Failure exits the workspace context and stops the new container.
            subprocess.run(
                ["docker", "update", *worker_docker_flags(), workspace._container_id],
                check=True,
                capture_output=True,
                timeout=30,
            )
            yield workspace
    finally:
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], env=env, check=True)
