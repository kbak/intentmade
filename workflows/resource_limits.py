"""Small, explicit resource bounds at the factory's worker/import boundaries."""

import gzip
import shutil
import tarfile
import tempfile
import urllib.request
from contextlib import contextmanager

import deployment

MIB = 1024 * 1024
DEFAULTS = {
    "worker_memory_mb": 4096,
    "worker_cpus": 2,
    "worker_pids": 512,
    "test_daemon_pids": 2048,
    "min_free_disk_mb": 5120,
    "max_bundle_mb": 256,
    "max_archive_download_mb": 256,
    "max_archive_expanded_mb": 1024,
    "max_archive_members": 100000,
}


def validate(overrides):
    if not isinstance(overrides, dict) or set(overrides) - DEFAULTS.keys():
        raise ValueError("resource_limits must contain supported limit names")
    for name, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"resource_limits.{name} must be a positive integer")
    return {**DEFAULTS, **overrides}


def settings():
    return deployment.settings()["resource_limits"]


def worker_docker_flags():
    config = settings()
    memory = f"{config['worker_memory_mb']}m"
    return [
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        str(config["worker_cpus"]),
        "--pids-limit",
        str(config["worker_pids"]),
    ]


def require_disk_space(path):
    minimum = settings()["min_free_disk_mb"] * MIB
    if shutil.disk_usage(path).free < minimum:
        raise RuntimeError(
            f"Factory disk headroom below {minimum // MIB} MiB; "
            "recover retained jobs or free disk space before starting more work"
        )


def copy_bounded(source, target, limit, label):
    """Reject oversize input, including a growing file; never silently truncate."""
    remaining = limit
    while chunk := source.read(min(MIB, remaining + 1)):
        if len(chunk) > remaining:
            raise RuntimeError(f"{label} exceeds the configured {limit}-byte limit")
        target.write(chunk)
        remaining -= len(chunk)


@contextmanager
def repository_archive(request):
    # Bound compressed bytes, decompressed tar bytes (including metadata), and
    # member count before the upstream safe extractor materializes any files.
    require_disk_space(tempfile.gettempdir())
    config = settings()
    with tempfile.TemporaryFile() as compressed, tempfile.TemporaryFile() as expanded:
        with urllib.request.urlopen(request, timeout=60) as response:
            copy_bounded(
                response,
                compressed,
                config["max_archive_download_mb"] * MIB,
                "Repository archive download",
            )
        compressed.seek(0)
        with gzip.GzipFile(fileobj=compressed) as stream:
            copy_bounded(
                stream,
                expanded,
                config["max_archive_expanded_mb"] * MIB,
                "Expanded repository archive",
            )
        expanded.seek(0)
        with tarfile.open(fileobj=expanded, mode="r:") as archive:
            yield archive


def archive_members(archive):
    config = settings()
    maximum = config["max_archive_members"]
    maximum_size = config["max_archive_expanded_mb"] * MIB
    size = 0
    members = []
    for member in archive:
        if len(members) >= maximum:
            raise RuntimeError("Repository archive exceeds the configured member limit")
        # Sparse members can expand far beyond the decompressed tar stream.
        size += member.size
        if member.size < 0 or size > maximum_size:
            raise RuntimeError("Repository archive exceeds the configured extracted size limit")
        members.append(member)
    return members
