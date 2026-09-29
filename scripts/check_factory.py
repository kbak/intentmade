"""Run the factory suite in an immutable, matching disposable test image."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from build_inputs import verify  # noqa: E402


# [impl->req~im-runtime-build-inputs~1]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", default=".local-validation/factory.xml")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image):
        parser.error("--image must be an immutable local image ID (sha256:...)")
    root = Path.cwd().resolve()
    report = (root / args.report).resolve()
    if not report.is_relative_to(root) or report.exists() or (root / args.report).is_symlink():
        parser.error("--report must be a fresh path within the captured candidate")
    docker = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    result = subprocess.run(
        [*docker, "--entrypoint", "cat", args.image, "/opt/factory/runtime-build.json"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Cannot read pinned runtime build manifest: " + result.stderr.strip())
    verify(root, json.loads(result.stdout))
    report.parent.mkdir(parents=True, exist_ok=True)
    mounts = {
        "workflows": "/opt/factory/workflows",
        "tests": "/tests",
        "tests/config": "/opt/factory/config",
        "examples": "/examples",
        "scripts": "/scripts",
        "scripts/configure.py": "/opt/factory/configure.py",
        "runtime": "/runtime",
        "tests/profiles": "/factory-tests",
    }
    command = [
        *docker,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--entrypoint",
        "python",
        "-e",
        "PYTHONPATH=/opt/factory/workflows:/runtime",
        "-e",
        "OPENHANDS_SUPPRESS_BANNER=1",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
    ]
    for source, target in mounts.items():
        command += ["-v", f"{root / source}:{target}:ro"]
    command += [
        "-v",
        f"{report.parent}:/results:rw",
        args.image,
        "-m",
        "intentbond.unittest_junit",
        "--start",
        "/tests",
        "--links",
        "/tests/oft-links.json",
        "--report",
        f"/results/{report.name}",
    ]
    return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
