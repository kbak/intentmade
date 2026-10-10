"""Bind installed runtime patches/dependencies to the source used to build them."""

import argparse
import hashlib
import json
from pathlib import Path

FILES = (
    "docker/runtime.Dockerfile",
    "docker/openhands-requirements.txt",
    "docker/traceability.Dockerfile",
    "docker/intentbond-requirements.txt",
    "docker/traceability-requirements.txt",
    "upstream.lock.json",
    ".dockerignore",
)


# [impl->req~im-runtime-build-inputs~1]
def manifest(root):
    root = Path(root)
    selected = [root / name for name in FILES]
    runtime_paths = sorted((root / "runtime").rglob("*"))
    for path in runtime_paths:
        if path.is_symlink():
            raise ValueError(f"Runtime build input must be a regular file: {path}")
    selected += sorted(
        path
        for path in runtime_paths
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    entries = {}
    for path in selected:
        if path.is_symlink():
            raise ValueError(f"Runtime build input must be a regular file: {path}")
        entries[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "executable": bool(path.stat().st_mode & 0o111),
        }
    return {"schema_version": 1, "files": entries}


def verify(root, recorded):
    expected = manifest(root)
    if recorded != expected:
        old = recorded.get("files", {})
        changed = sorted(
            name
            for name in old.keys() | expected["files"].keys()
            if old.get(name) != expected["files"].get(name)
        )
        raise ValueError(
            "Runtime build inputs differ; rebuild and pin the test image: "
            + ", ".join(changed or ["manifest schema"])
        )
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(manifest(args.root), indent=2) + "\n")
