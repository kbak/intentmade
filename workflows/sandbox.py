"""Native DockerWorkspace with one disposable Docker test daemon per job."""

import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import diagnostics
import input_artifacts
import measurements
import provenance
from common import ROOT, api
from openhands.agent_server.persistence import FileSecretsStore
from openhands.agent_server.persistence.store import _file_lock
from openhands.sdk.utils.cipher import Cipher
from openhands.workspace import DockerWorkspace
from openhands.workspace.docker.workspace import find_available_tcp_port
from pydantic import SecretStr


def load_credential(parent):
    if callable(getattr(parent, "load_versioned_secret", None)):
        try:
            return parent.load_versioned_secret("CODEX_AUTH_JSON")
        except KeyError as exc:
            if exc.args != ("CODEX_AUTH_JSON",):
                raise
            raise RuntimeError("Native Codex login is unavailable") from None
    value = parent.get_secret("CODEX_AUTH_JSON")
    if not value:
        raise RuntimeError("Native Codex login is unavailable")
    return value, None


def sync_credential(parent, version, previous, refreshed):
    if refreshed == previous:
        return
    try:
        if callable(getattr(parent, "replace_versioned_secret", None)):
            parent.replace_versioned_secret("CODEX_AUTH_JSON", version, refreshed)
        else:
            # Agent Server 1.27.1 in Canvas 1.20 has native locking/encryption,
            # but no versioned methods. Compare the original value under that
            # same lock before saving a refresh; preserve all unrelated secrets.
            with _file_lock(parent._lock_path):
                current = parent.load()
                if current is None:
                    raise RuntimeError(
                        "Native credential store could not be loaded; refusing overwrite"
                    )
                binding = current.custom_secrets.get("CODEX_AUTH_JSON")
                if (
                    binding is None
                    or binding.secret is None
                    or binding.secret.get_secret_value() != previous
                ):
                    raise ValueError("credential_version_conflict")
                updated = dict(current.custom_secrets)
                updated["CODEX_AUTH_JSON"] = binding.model_copy(
                    update={"secret": SecretStr(refreshed)}
                )
                parent.save(current.model_copy(update={"custom_secrets": updated}))
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


def mounts(root, config):
    volumes = [f"{root}:{root}"]
    if input_artifacts.CURRENT.get():
        volumes.append(f"{input_artifacts.directory(root)}:/factory-inputs:ro")
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
    env = dict(os.environ, JOB_WORKSPACE=str(root), JOB_INPUTS=input_artifacts.directory(root))
    encryption_key = (
        os.environ.get("OH_SECRET_KEY") or Path("/run/secrets/encryption-key").read_text().strip()
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
            configs = config if isinstance(config, list) else [config]
            manifest = provenance.capture(
                workspace,
                intended={"worker_image": workspace.server_image},
                source={"job": name},
            )
            Path(settings["FACTORY_EXECUTION_MANIFEST"]).write_text(
                json.dumps(manifest, indent=2) + "\n"
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
                primary_failure = sys.exc_info()[0] is not None
                credentials = [value, settings["OH_SESSION_API_KEYS_0"]]
                try:
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
        subprocess.run([*compose, "down", "-v", "--remove-orphans"], env=env, check=True)
