"""Exercise network policy in a disposable DinD instance with no deployment mounts.

Run with a local Docker daemon and the cached docker:29.4.1-dind image. All
containers, networks and volumes created here are uniquely named and removed.
"""

import signal
import subprocess
import time
import uuid
from pathlib import Path

IMAGE = "docker:29.4.1-dind"
HTTP = "while true; do printf 'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nOK' | nc -l -p 8080; done"


def command(*args, check=True, timeout=60, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {result.stderr.strip()}")
    return result


def main():
    prefix = "factory-isolation-" + uuid.uuid4().hex[:12]
    outer, parent, network, volume, control = (
        prefix + suffix for suffix in ("-dind", "-parent", "-net", "-data", "-control")
    )
    script = Path(__file__).resolve().parents[1] / "runtime/sandbox-daemon"

    def docker(*args, **kwargs):
        return command("docker", *args, **kwargs)

    def nested(*args, **kwargs):
        return docker("exec", outer, "docker", *args, **kwargs)

    def inside(*args, **kwargs):
        return nested("exec", "job-a", *args, **kwargs)

    def check_http(label, runner, address, expected):
        response = runner("wget", "-q", "-T", "5", "-O", "-", address, check=False)
        reached = response.returncode == 0 and response.stdout == "OK"
        if reached != expected:
            raise RuntimeError(f"{label}: expected reachable={expected}, got {reached}")
        print(f"PASS {label}", flush=True)

    try:
        docker("image", "inspect", IMAGE)
        docker("network", "create", network)
        docker("volume", "create", volume)
        docker("volume", "create", control)
        docker(
            "run",
            "--detach",
            "--pull=never",
            "--privileged",
            "--name",
            outer,
            "--network",
            network,
            "--env",
            "DOCKER_TLS_CERTDIR=",
            "--entrypoint",
            "sh",
            "--volume",
            f"{volume}:/var/lib/docker",
            "--volume",
            f"{control}:/run/factory-docker",
            "--volume",
            f"{script}:/opt/factory/sandbox-daemon:ro",
            IMAGE,
            "/opt/factory/sandbox-daemon",
            "dockerd",
            "--host=unix:///run/factory-docker/docker.sock",
            "--host=unix:///var/run/docker.sock",
            "--group=2375",
            "--storage-driver=overlay2",
        )
        deadline = time.monotonic() + 90
        while True:
            ready = docker(
                "exec",
                outer,
                "sh",
                "-c",
                "test -f /run/factory-network-ready && docker info >/dev/null",
                check=False,
            )
            if ready.returncode == 0:
                break
            running = docker("inspect", "--format", "{{.State.Running}}", outer).stdout.strip()
            if running != "true" or time.monotonic() >= deadline:
                logs = docker("logs", outer)
                raise RuntimeError(
                    "Isolated daemon did not become ready:\n" + (logs.stdout + logs.stderr)[-5000:]
                )
            time.sleep(1)
        print("PASS policy and isolated daemon become ready", flush=True)

        socket_group = docker(
            "exec", outer, "stat", "-c", "%g", "/run/factory-docker/docker.sock"
        ).stdout.strip()
        if socket_group != "2375":
            raise RuntimeError("Management socket has wrong group")
        docker(
            "exec",
            "--user",
            "65534:2375",
            outer,
            "docker",
            "--host=unix:///run/factory-docker/docker.sock",
            "version",
            "--format",
            "{{.Server.Version}}",
        )
        print("PASS unprivileged management client can use socket group 2375", flush=True)
        if (
            docker(
                "exec", outer, "nc", "-z", "-w", "2", "127.0.0.1", "2375", check=False
            ).returncode
            == 0
        ):
            raise RuntimeError("Outer Docker daemon exposes TCP port 2375")
        print("PASS outer Docker daemon has no TCP 2375 listener", flush=True)

        # Transfer only a cached public image; never mount the host Docker
        # socket, private repositories, or actual factory runtime data.
        with subprocess.Popen(["docker", "save", IMAGE], stdout=subprocess.PIPE) as saved:
            loaded = subprocess.run(
                ["docker", "exec", "-i", outer, "docker", "load"],
                stdin=saved.stdout,
                capture_output=True,
                text=True,
                timeout=180,
            )
            saved.stdout.close()
            if loaded.returncode or saved.wait(timeout=20):
                raise RuntimeError("Could not load cached fixture image: " + loaded.stderr)
        for name in ("job-a", "job-b"):
            nested("network", "create", name)
        nested(
            "run",
            "--detach",
            "--name",
            "job-a",
            "--network",
            "job-a",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            "sleep 600",
        )
        nested(
            "run",
            "--detach",
            "--name",
            "service-a",
            "--network",
            "job-a",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            HTTP,
        )
        nested(
            "run",
            "--detach",
            "--name",
            "service-b",
            "--network",
            "job-b",
            "--publish",
            "18080:8080",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            HTTP,
        )
        docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            parent,
            "--network",
            network,
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            HTTP,
        )
        parent_ip = docker(
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            parent,
        ).stdout.strip()
        gateway = nested(
            "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}", "job-a"
        ).stdout.strip()

        def in_outer(*args, **kwargs):
            return docker("exec", outer, *args, **kwargs)

        check_http(
            "parent fixture is reachable outside job networks",
            in_outer,
            f"http://{parent_ip}:8080",
            True,
        )
        check_http(
            "other-job published fixture is available to management",
            in_outer,
            "http://127.0.0.1:18080",
            True,
        )
        check_http("same-job service remains reachable", inside, "http://service-a:8080", True)
        check_http(
            "job cannot reach fake parent HTTP service", inside, f"http://{parent_ip}:8080", False
        )
        check_http(
            "job cannot reach another job's published endpoint",
            inside,
            f"http://{gateway}:18080",
            False,
        )
        inside("nslookup", "registry.npmjs.org")
        inside("wget", "-q", "-T", "20", "-O", "/dev/null", "https://registry.npmjs.org/npm/latest")
        print("PASS public DNS and package-registry HTTPS remain available", flush=True)
        print("Isolation smoke passed.", flush=True)
    finally:
        docker("rm", "--force", "--volumes", parent, outer, check=False)
        docker("network", "rm", network, check=False)
        docker("volume", "rm", volume, check=False)
        docker("volume", "rm", control, check=False)


if __name__ == "__main__":

    def cancelled(*_):
        raise InterruptedError("Isolation smoke cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    main()
