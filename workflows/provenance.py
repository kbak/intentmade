"""Allowlisted observations at execution boundaries; never substitute image tags."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import runpy
import shlex
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PACKAGES = (
    "openhands-sdk",
    "openhands-workspace",
    "openhands-agent-server",
    "openhands-automation",
    "intentbond",
    "openhands-traceability",
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime():
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    tools = {}
    for name, package in (
        ("codex-acp", "@agentclientprotocol/codex-acp"),
        ("codex", "@openai/codex"),
    ):
        path = Path("/acp-node/lib/node_modules") / package / "package.json"
        try:
            tools[name] = json.loads(path.read_text())["version"]
        except (OSError, ValueError, KeyError):
            tools[name] = None
    jar = Path(
        os.environ.get(
            "INTENTBOND_OFT_JAR", "/opt/factory/traceability-tools/openfasttrace-4.9.0.jar"
        )
    )
    tools["oft_jar_sha256"] = sha256(jar) if jar.is_file() else None
    for name, location in {
        "codex_acp_bundle_sha256": "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js",
        "usage_patch_sha256": "/opt/factory/factory-usage.mjs",
    }.items():
        path = Path(location)
        tools[name] = sha256(path) if path.is_file() else None
    # Fingerprint installed factory code, not a guessed Git revision or mutable tag.
    root = Path(__file__).resolve().parent
    sources = {
        str(p.relative_to(root)): sha256(p)
        for p in sorted(root.rglob("*.py"))
        if not p.is_symlink()
    }
    return {
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "sqlite": sqlite3.sqlite_version,
        "system": platform.system(),
        "machine": platform.machine(),
        "packages": packages,
        "tools": tools,
        "factory_workflow_sha256": hashlib.sha256(
            json.dumps(sources, sort_keys=True).encode()
        ).hexdigest(),
    }


def docker_json(arguments, template):
    result = subprocess.run(
        ["docker", *arguments, "--format", template],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


def image_observation(container_id):
    result = {
        "container_id": container_id,
        "daemon_id": None,
        "image_id": None,
        "repo_digests": None,
        "platform": None,
        "rootfs_layers": None,
        "error_type": None,
    }
    if not container_id:
        result["error_type"] = "container_identity_unavailable"
        return result
    try:
        # Inspect the container first. A retargeted tag cannot change this image ID.
        container = docker_json(["container", "inspect", container_id], "{{json .Image}}")
        image = docker_json(
            ["image", "inspect", container],
            '{"id":{{json .Id}},"digests":{{json .RepoDigests}},"os":{{json .Os}},"architecture":{{json .Architecture}},"layers":{{json .RootFS.Layers}}}',
        )
        result.update(
            image_id=image["id"],
            repo_digests=image["digests"],
            platform=image["os"] + "/" + image["architecture"],
            rootfs_layers=image["layers"],
        )
        result["daemon_id"] = docker_json(["info"], "{{json .ID}}")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        result["error_type"] = type(exc).__name__
    return result


def required(record, expectations):
    if not isinstance(expectations, dict):
        raise ValueError("required_environment must map observed paths to expected values or true")
    for path, expected in expectations.items():
        if not isinstance(path, str) or not path.startswith("observed."):
            raise ValueError("Environment requirements must select observed fields")
        value = record
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None or (expected is not True and value != expected):
            raise RuntimeError(f"Required execution identity unavailable or mismatched: {path}")


def capture(workspace=None, intended=None, source=None):
    observed = {
        "controller_runtime": runtime(),
        "worker_runtime": None,
        "worker_image": image_observation(getattr(workspace, "_container_id", None)),
        "host_image": None,
        "application_runtime": None,
    }
    if workspace is not None:
        try:
            result = workspace.execute_command("python -m provenance runtime", timeout=60)
            if result.exit_code == 0:
                observed["worker_runtime"] = json.loads(result.stdout)
        except Exception:
            pass
    return {
        "schema_version": 1,
        "id": str(uuid4()),
        "recorded_at": datetime.now(UTC).isoformat(),
        "intended": intended or {},
        "observed": observed,
        "source": source,
        "limits": "Controller and worker are separate observations. Application/container descendants require their own boundary capture. Unknown identities are not inferred from tags.",
    }


def boundary(repo=None, command=None):
    inherited = None
    reference = os.environ.get("FACTORY_EXECUTION_MANIFEST")
    if reference:
        try:
            path = Path(reference)
            inherited = {
                "path": str(path),
                "sha256": sha256(path),
                "id": json.loads(path.read_text())["id"],
            }
        except (OSError, ValueError, KeyError):
            pass
    source = {
        "requested_path": str(repo) if repo else None,
        "head": None,
        "tree": None,
        "worktree_sha256": None,
    }
    if repo:
        for key, revision in (("head", "HEAD"), ("tree", "HEAD^{tree}")):
            try:
                source[key] = subprocess.check_output(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-C",
                        str(repo),
                        "rev-parse",
                        revision,
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                ).strip()
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            # This digest is observational. Portable checks additionally freeze and
            # verify the full snapshot, including changes during test execution.
            names = (
                subprocess.check_output(
                    ["git", "-C", str(repo), "ls-files", "-c", "-o", "--exclude-standard", "-z"],
                    timeout=15,
                )
                .decode()
                .split("\0")
            )
            entries = []
            for name in sorted(set(n for n in names if n)):
                path = Path(repo) / name
                if path.is_symlink():
                    entries.append([name, "symlink", os.readlink(path)])
                elif path.is_file():
                    entries.append([name, path.stat().st_mode & 0o777, sha256(path)])
                else:
                    entries.append([name, "missing", None])
            source["worktree_sha256"] = hashlib.sha256(
                json.dumps(entries, sort_keys=True).encode()
            ).hexdigest()
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return {
        "schema_version": 1,
        "id": str(uuid4()),
        "recorded_at": datetime.now(UTC).isoformat(),
        "intended": {"command": command},
        "observed": {"execution_runtime": runtime(), "execution_image": None},
        "source": source,
        "worker_manifest": inherited,
        "limits": "Runtime describes this wrapper process. Descendant containers or interpreters need their own capture; command runtime is not inferred.",
    }


def remote_boundary(workspace, repo):
    try:
        result = workspace.execute_command(
            shlex.join(["python", "-m", "provenance", "boundary", "--repo", str(repo)]),
            cwd=str(repo),
            timeout=60,
        )
        if result.exit_code == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {"observed": {"execution_runtime": None}, "source": None, "worker_manifest": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("runtime")
    sub.add_parser("boundary").add_argument("--repo", type=Path)
    for name in ("run", "python"):
        run = sub.add_parser(
            name, help="Capture before running a command or a script in this Python"
        )
        run.add_argument("--out", type=Path, required=True)
        run.add_argument("--repo", type=Path)
        run.add_argument(
            "--require", type=Path, help="JSON mapping of required observed identities"
        )
        run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.operation == "runtime":
        print(json.dumps(runtime()))
        return
    if args.operation == "boundary":
        print(json.dumps(boundary(args.repo)))
        return
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command argument list is required")
    record = boundary(args.repo, command)
    if args.operation == "python":
        record["observed"]["application_runtime"] = record["observed"]["execution_runtime"]
        record["limits"] = (
            "Script/module starts in this exact Python process. Spawned interpreters or containers require separate capture."
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # A previously captured observation is never overwritten by a later runtime.
    with args.out.open("x") as handle:
        json.dump(record, handle, indent=2)
    required(record, json.loads(args.require.read_text()) if args.require else {})
    print("Execution manifest: " + str(args.out), flush=True)
    if args.operation == "python":
        if command[0] == "-m":
            if len(command) < 2:
                parser.error("-m requires a module name")
            sys.argv = command[1:]
            runpy.run_module(command[1], run_name="__main__", alter_sys=True)
        else:
            sys.argv = command
            sys.path.insert(0, str(Path(command[0]).resolve().parent))
            runpy.run_path(command[0], run_name="__main__")
        return
    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
