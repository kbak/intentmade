"""Declared local artifacts, frozen by the controller and shared read-only."""

import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from common import DATA

SOURCE_ROOT = Path("/projects/artifacts")
CURRENT = ContextVar("factory_inputs", default=None)
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def validate(declared):
    if not isinstance(declared, list) or len(declared) > 64:
        raise ValueError("Declare at most 64 input artifacts")
    names, total = set(), 0
    for item in declared:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "reference",
            "sha256",
            "size",
            "producer",
        }:
            raise ValueError("Each input needs name, reference, sha256, size and producer")
        name = item["name"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name)
            or name == "manifest.json"
        ):
            raise ValueError("Unsafe or reserved input name")
        if name in names:
            raise ValueError("Duplicate input name")
        names.add(name)
        reference = item["reference"]
        if (
            not isinstance(reference, str)
            or len(reference) > 1024
            or any(not p or p in {".", ".."} for p in reference.split("/"))
            or reference.startswith("/")
            or "\\" in reference
        ):
            raise ValueError("Input reference must be a relative artifact path")
        if not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("Input needs a SHA-256 digest")
        size = item["size"]
        if type(size) is not int or not 0 <= size <= MAX_FILE:
            raise ValueError("Input size exceeds the per-file limit")
        total += size
        if (
            not isinstance(item["producer"], str)
            or not item["producer"].strip()
            or len(item["producer"]) > 2048
        ):
            raise ValueError("Input needs a bounded producer identity")
    if total > MAX_TOTAL:
        raise ValueError("Input set exceeds the total size limit")
    return sorted(declared, key=lambda item: item["name"])


def copy_verified(root, relative, item, destination=None):
    # Open every component relative to a trusted root, never following links.
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != item["size"]:
                raise ValueError("Input must be a regular, unlinked file of the declared size")
            checksum, count = hashlib.sha256(), 0
            target = destination.open("xb") if destination else None
            try:
                while block := source.read(1024 * 1024):
                    count += len(block)
                    if count > item["size"]:
                        raise ValueError("Input grew during verification")
                    checksum.update(block)
                    if target:
                        target.write(block)
            finally:
                if target:
                    target.close()
            if count != item["size"] or checksum.hexdigest() != item["sha256"]:
                raise ValueError("Input digest or size mismatch: " + item["name"])
    finally:
        os.close(directory)


def verify_frozen(record):
    root = Path(record["directory"])
    manifest = encoded(record["inputs"])
    copy_verified(
        root,
        "manifest.json",
        {"name": "manifest.json", "size": len(manifest), "sha256": digest(manifest)},
    )
    if set(p.name for p in root.iterdir()) != {
        "manifest.json",
        *(i["name"] for i in record["inputs"]),
    }:
        raise ValueError("Frozen input inventory changed")
    for item in record["inputs"]:
        copy_verified(root, item["name"], item)


@contextmanager
def prepared(declared, artifact, *, task, bases, request):
    record = {
        "schema_version": 1,
        "task": task,
        "bases": bases,
        "contract_sha256": digest(request.encode()),
        "status": "verifying",
    }
    receipt = artifact / "input-artifacts.json"
    try:
        selected = validate([] if declared is None else declared)
        record["inputs"] = selected
        record["manifest_sha256"] = digest(encoded(selected))
        if selected:
            cache = DATA / "input-artifacts"
            cache.mkdir(parents=True, exist_ok=True)
            destination = cache / record["manifest_sha256"]
            with tempfile.TemporaryDirectory(dir=cache, prefix=".stage-") as temp:
                staged = Path(temp) / "inputs"
                staged.mkdir()
                for item in selected:
                    copy_verified(SOURCE_ROOT, item["reference"], item, staged / item["name"])
                (staged / "manifest.json").write_bytes(encoded(selected))
                if not destination.exists():
                    try:
                        staged.rename(destination)
                    except OSError:
                        if not destination.is_dir():
                            raise
                record["directory"] = str(destination)
            verify_frozen(record)
        record["status"] = "verified"
    except Exception as exc:
        record.update(status="rejected", error=f"{type(exc).__name__}: {exc}")
        receipt.write_bytes(encoded(record))
        raise
    receipt.write_bytes(encoded(record))
    token = CURRENT.set(record if selected else None)
    try:
        yield record
    finally:
        CURRENT.reset(token)


def bind_task(repository, record):
    """Task retries cannot silently change the accepted fixture set."""
    path = Path(repository) / "factory-inputs.json"
    data = encoded(record["inputs"])
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Retained task input artifacts changed; use a new task ID")
    else:
        with path.open("xb") as handle:
            handle.write(data)


def directory(root):
    record = CURRENT.get()
    if record:
        verify_frozen(record)
        return record["directory"]
    empty = root / ".factory-empty-inputs"
    empty.mkdir(parents=True, exist_ok=True)
    return str(empty)


def instructions():
    record = CURRENT.get()
    if not record:
        return ""
    return (
        "\n\nDeclared input artifacts are mounted read-only at /factory-inputs. "
        "The controller verified their sizes and hashes; producer labels are declarations, not attestations. "
        "Copy an input to your workspace if a test must mutate it. Docker test containers can bind "
        "/factory-inputs:/factory-inputs:ro from the test daemon. Do not regenerate historical inputs.\n"
        + json.dumps({"manifest_sha256": record["manifest_sha256"], "inputs": record["inputs"]})
    )
