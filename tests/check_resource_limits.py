"""Verify the resource bridge against Docker and the active Linux cgroup limits."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("limits", ROOT / "workflows/resource_limits.py")
limits = importlib.util.module_from_spec(spec)
spec.loader.exec_module(limits)


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


config = limits.settings()
container = docker(
    "run",
    "-d",
    "--rm",
    "--network",
    "none",
    "--entrypoint",
    "python",
    os.environ.get("FACTORY_IMAGE", "intentmade:dev"),
    "-c",
    "import time; time.sleep(90)",
)
try:
    docker("update", *limits.worker_docker_flags(), container)
    actual = json.loads(docker("inspect", container))[0]["HostConfig"]
    memory = config["worker_memory_mb"] * limits.MIB
    assert actual["Memory"] == actual["MemorySwap"] == memory, actual
    assert actual["NanoCpus"] == config["worker_cpus"] * 1_000_000_000, actual
    assert actual["PidsLimit"] == config["worker_pids"], actual
    probe = """import json
from pathlib import Path
root=Path('/sys/fs/cgroup')
print(json.dumps({name:(root/name).read_text().strip()
                  for name in ('memory.max','cpu.max','pids.max')}))
"""
    cgroup = json.loads(docker("exec", container, "python", "-c", probe))
    assert int(cgroup["memory.max"]) == memory, cgroup
    assert int(cgroup["pids.max"]) == config["worker_pids"], cgroup
    quota, period = map(int, cgroup["cpu.max"].split())
    assert quota == period * config["worker_cpus"], cgroup
    print("PASS: Docker configured memory, swap, CPU and process limits; active cgroups match")
finally:
    docker("stop", "-t", "1", container)

compose = json.loads(
    subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "workflows/sandbox.yaml"),
            "config",
            "--format",
            "json",
        ],
        text=True,
        env={
            **os.environ,
            "JOB_WORKSPACE": "/tmp/factory-fixture",
            "JOB_INPUTS": "/tmp/factory-inputs",
            "JOB_TEST_DAEMON_PIDS": str(config["test_daemon_pids"]),
        },
    )
)
assert compose["services"]["docker"]["pids_limit"] == config["test_daemon_pids"]
print("PASS: job daemon Compose configuration receives the process limit")
