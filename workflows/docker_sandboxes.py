"""Optional local sbx lifecycle; OpenHands still owns commands, files and secrets."""

import ipaddress
import json
import os
import shlex
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("worker_runtime must be an object")
    backend = value.get("backend", "docker")
    allowed = (
        {"backend"}
        if backend == "docker"
        else {"backend", "command", "kit", "profiles", "publish_host"}
    )
    if backend not in ("docker", "docker-sandboxes") or set(value) - allowed:
        raise ValueError("Unsupported worker_runtime backend or settings")
    if backend == "docker-sandboxes":
        for key in ("kit", "profiles"):
            if key == "profiles" and key not in value:
                continue
            path = value.get(key)
            if not isinstance(path, str) or not Path(path).is_absolute() or ":" in path:
                raise ValueError(f"worker_runtime.{key} must be an absolute local path")
        command = value.get("command", "sbx")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("worker_runtime.command must name the local sbx executable")
        host = ipaddress.IPv4Address(value.get("publish_host", "127.0.0.1"))
        if host.is_unspecified or host.is_multicast or not (host.is_private or host.is_loopback):
            raise ValueError(
                "worker_runtime.publish_host must be a private or loopback IPv4 address"
            )
    return {**value, "backend": backend}


def settings():
    path = Path(os.environ.get("FACTORY_ROOT", "/opt/factory")) / "config/defaults.json"
    defaults = json.loads(path.read_text()) if path.exists() else {}
    return validate(defaults.get("worker_runtime", {}))


def mount_arguments(volumes, options):
    """sbx mounts host paths unchanged; aliases retain existing profile/input paths."""
    paths, aliases = [], []
    for volume in volumes:
        source, target, *mode = volume.split(":")
        if source.startswith("/profiles/") and options.get("profiles"):
            source = str(Path(options["profiles"]) / Path(source).relative_to("/profiles"))
        if not Path(source).is_absolute() or not Path(target).is_absolute():
            raise ValueError("Docker Sandboxes mounts require absolute paths")
        paths.append(source + (":ro" if mode == ["ro"] else ""))
        if source != target:
            aliases.append((source, target))
    return paths, aliases


@contextmanager
def worker(root, volumes, environment, options):
    import httpx
    from diagnostics import redact
    from openhands.sdk.workspace import RemoteWorkspace

    name = "intentmade-" + uuid4().hex
    command = options.get("command", "sbx")
    host = options.get("publish_host", "127.0.0.1")
    environment = {
        **environment,
        "OH_PERSISTENCE_DIR": "/home/agent/.openhands",
        "OH_CONVERSATIONS_PATH": "/home/agent/.openhands/conversations",
        "OH_BASH_EVENTS_DIR": "/home/agent/.openhands/bash_events",
    }
    env = {**os.environ, **environment, "SBX_NO_TELEMETRY": "1"}
    paths, aliases = mount_arguments(volumes, options)
    session = None
    created = False

    def run(*args, timeout=180):
        result = subprocess.run(
            [command, *args], env=env, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode:
            detail = redact(result.stderr, [environment["OH_SESSION_API_KEYS_0"]])[:2000]
            raise RuntimeError(f"sbx {args[0]} failed: {detail}")
        return result.stdout

    # Keep logs outside the worker-writable job. Never put credential values in argv.
    with tempfile.TemporaryFile(mode="w+") as log:
        try:
            run(
                "create",
                "--name",
                name,
                "--pull",
                "never",
                "--skills",
                "off",
                "--publish",
                "8000",
                options["kit"],
                *paths,
            )
            created = True
            setup = []
            for source, target in aliases:
                setup.append("mkdir -p " + shlex.quote(str(Path(target).parent)))
                setup.append("ln -s -- " + shlex.join([source, target]))
            # Existing test/browser profiles address the private daemon as 'docker'.
            setup.append("printf '\\n127.0.0.1 docker\\n' >> /etc/hosts")
            run("exec", "-u", "root", name, "sh", "-ec", "\n".join(setup))
            flags = [item for key in environment for item in ("--env", key)]
            session = subprocess.Popen(
                [
                    command,
                    "exec",
                    "-u",
                    "agent",
                    "-w",
                    "/home/agent",
                    *flags,
                    name,
                    "/usr/local/bin/openhands-agent-server",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            ports = json.loads(run("ports", name, "--json"))
            port = next(p["host_port"] for p in ports if p["sandbox_port"] == 8000)
            if host != "127.0.0.1":
                run("ports", name, "--publish", f"{host}:{port}:8000")
                run("ports", name, "--unpublish", f"127.0.0.1:{port}:8000")
            url = f"http://{host}:{port}"
            with RemoteWorkspace(
                host=url,
                api_key=environment["OH_SESSION_API_KEYS_0"],
                working_dir=str(root / "source"),
            ) as workspace:
                workspace._factory_log = log
                deadline = time.monotonic() + 90
                readiness = "no response"
                while time.monotonic() < deadline:
                    if session.poll() is not None:
                        raise RuntimeError("Docker Sandboxes worker exited during startup")
                    try:
                        # The SDK client retries; use a bounded readiness request here.
                        response = httpx.get(
                            url + "/openapi.json",
                            timeout=2,
                            headers={"X-Session-API-Key": environment["OH_SESSION_API_KEYS_0"]},
                        )
                        readiness = f"HTTP {response.status_code}"
                        if response.status_code == 200:
                            break
                        if response.status_code in (401, 403):
                            raise RuntimeError("Docker Sandboxes worker authentication failed")
                    except httpx.TransportError as exc:
                        readiness = type(exc).__name__
                    time.sleep(0.25)
                else:
                    raise TimeoutError(f"Docker Sandboxes worker did not become ready: {readiness}")
                yield workspace
        finally:
            try:
                if created or any(
                    item["name"] == name for item in json.loads(run("ls", "--json"))["sandboxes"]
                ):
                    run("rm", "--force", name)
            finally:
                if session is not None:
                    try:
                        session.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        session.terminate()
                        try:
                            session.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            session.kill()
                            session.wait(timeout=5)
