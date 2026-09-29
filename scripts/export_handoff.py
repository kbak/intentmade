"""Copy a completed run's portable evidence, excluding provider transcripts."""

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path


# [impl->req~im-portable-handoff~1]
def export(artifacts, run, destination):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,116}", run):
        raise ValueError("Use a run artifact directory name, not a path")
    source = artifacts / run
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Run artifact directory is missing or linked")
    before = (source / "handoff.json").read_bytes()
    handoff = json.loads(before)
    status = handoff["status"]
    if status == "RUNNING" or (handoff["phase"] != "DONE" and status == "PASSED"):
        raise ValueError("Wait for the run to stop before exporting evidence")
    destination = Path(destination).resolve()
    if destination.is_relative_to(source.resolve()):
        raise ValueError("Export destination must be outside the run")
    destination.mkdir(parents=True, exist_ok=False)
    entries, total = {}, 0
    try:
        for directory, dirs, names in os.walk(source, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix not in {".md", ".json", ".log", ".xml", ".patch", ".png"}:
                    continue
                relative = path.relative_to(source)
                with os.fdopen(
                    os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
                ) as handle:
                    metadata = os.fstat(handle.fileno())
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError("Evidence contains a non-regular file: " + str(relative))
                    total += metadata.st_size
                    if total > 256 * 1024 * 1024 or len(entries) >= 2048:
                        raise ValueError("Handoff export exceeds 256 MiB or 2048 files")
                    data = handle.read(metadata.st_size + 1)
                    if len(data) != metadata.st_size:
                        raise ValueError("Evidence changed during export")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                entries[str(relative)] = {
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
        if (source / "handoff.json").read_bytes() != before:
            raise ValueError("Run changed during export; retry after it stops")
        (destination / "export.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run": run,
                    "files": entries,
                    "source": "Git base/candidate IDs and changes.patch; provider transcripts excluded",
                },
                indent=2,
            )
            + "\n"
        )
    except BaseException:
        shutil.rmtree(destination)
        raise
    print("Handoff exported:", destination / "handoff.md")
    return destination
