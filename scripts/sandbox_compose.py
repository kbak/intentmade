"""Apply native sbx host wiring to Docker Compose's resolved deployment model."""

import copy
import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
from pathlib import Path


def native_command(options):
    command = shutil.which(options.get("command", "sbx"))
    if command is None:
        raise ValueError("Install sbx or set worker_runtime.command to its executable path")
    return command


def render(original, options, *, command, socket, auth_directory, uid, gid):
    if ipaddress.ip_address(options.get("publish_host", "127.0.0.1")).is_loopback:
        raise ValueError(
            "Canvas uses bridge networking: set worker_runtime.publish_host to a private "
            "host address reachable from Docker containers"
        )
    result = copy.deepcopy(original)
    canvas = result["services"]["canvas"]
    base_image = canvas["image"]
    suffix = hashlib.sha256(f"{base_image}:{uid}:{gid}".encode()).hexdigest()[:16]
    canvas["image"] = "intentmade-controller:" + suffix
    canvas.pop("build", None)
    canvas.pop("group_add", None)
    canvas.pop("user", None)
    volumes = canvas["volumes"]
    workspaces = next(v["source"] for v in volumes if v["target"] == "/workspaces")
    canvas["volumes"] = [v for v in volumes if v["target"] != "/run/factory-docker"]

    def mount(source, target, readonly=False):
        canvas["volumes"].append(
            {
                "type": "bind",
                "source": str(source),
                "target": str(target),
                "read_only": readonly,
                "bind": {"create_host_path": False},
            }
        )

    mount(
        command,
        options.get("command")
        if Path(options.get("command", "sbx")).is_absolute()
        else "/usr/local/bin/sbx",
        True,
    )
    # The daemon replaces its socket on restart. A directory bind follows the
    # new inode; binding the socket file leaves a running controller disconnected.
    mount(Path(socket).parent, "/run/sbx", True)
    # Native credential authorization refers to host paths. Rewriting these
    # paths inside the controller makes an otherwise valid binding ineffective.
    config_home = Path(auth_directory).parent
    mount(auth_directory, auth_directory)
    mount(config_home / "sbx", config_home / "sbx", True)
    mount(config_home / "sandboxes", config_home / "sandboxes")
    mount(options["kit"], options["kit"], True)
    mount(workspaces, workspaces)
    if options.get("profiles"):
        mount(options["profiles"], options["profiles"], True)
    environment = canvas.setdefault("environment", {})
    environment.pop("DOCKER_HOST", None)
    environment.update(
        DOCKER_SANDBOXES_API="unix:///run/sbx/sandboxd.sock",
        SBX_NO_TELEMETRY="1",
        XDG_CONFIG_HOME=str(config_home),
        FACTORY_DATA=workspaces,
        FACTORY_HOST_CONFIG_DIR=next(
            v["source"] for v in volumes if v["target"] == "/opt/factory/config"
        ),
    )
    result["services"].pop("sandboxes", None)
    for name in ("sandbox-images", "sandbox-control"):
        result.get("volumes", {}).pop(name, None)
    return result


def resolve(original, options):
    command = native_command(options)
    result = subprocess.run(
        [command, "daemon", "status", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "SBX_NO_TELEMETRY": "1"},
    )
    status = json.loads(result.stdout)
    if status.get("status") != "running":
        raise ValueError("Start the local sbx daemon before factoryctl up")
    socket = status["socket"]
    auth = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "com.docker.sandboxes"
    for directory in (auth, auth.parent / "sbx", auth.parent / "sandboxes"):
        if not directory.is_dir():
            raise ValueError(
                f"Native configuration directory missing: {directory}; "
                "complete sbx login and configure the Kit's native credential bindings "
                "before factoryctl up (see docs/docker-sandboxes.md#credentials)"
            )
    return render(
        original,
        options,
        command=command,
        socket=socket,
        auth_directory=auth,
        uid=os.getuid(),
        gid=os.getgid(),
    )


def prepare(root, original, resolved, options):
    """Validate native prerequisites and build the controller before stopping it."""
    command = native_command(options)
    env = {**os.environ, "SBX_NO_TELEMETRY": "1"}
    subprocess.run([command, "kit", "validate", options["kit"]], check=True, env=env)
    subprocess.run([command, "ls", "--json"], check=True, env=env, stdout=subprocess.DEVNULL)
    base_image = original["services"]["canvas"]["image"]
    if base_image == "intentmade:dev":
        subprocess.run(["docker", "compose", "build", "canvas"], cwd=root, check=True)
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            "docker/sandbox-controller.Dockerfile",
            "--build-arg",
            "FACTORY_IMAGE=" + base_image,
            "--build-arg",
            f"CONTROLLER_UID={os.getuid()}",
            "--build-arg",
            f"CONTROLLER_GID={os.getgid()}",
            "-t",
            resolved["services"]["canvas"]["image"],
            ".",
        ],
        cwd=root,
        check=True,
    )
