"""Capture a guided feedback invocation; controller retention supplies custody."""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import job_files
import provenance


def now():
    return datetime.now(UTC).isoformat()


def write(path, record, *, trusted_root=None):
    job_files.write_text(
        trusted_root if trusted_root is not None else path.parent,
        path,
        json.dumps(record, indent=2) + "\n",
    )


def begin(root, phase, repo, scope, base, *, trusted_root=None):
    identity = str(uuid4())
    prefix = "agent-check-" if phase == "feedback" else "controller-check-"
    directory = Path(root) / (prefix + identity)
    job_files.mkdir(trusted_root if trusted_root is not None else root, directory)
    command = [
        "python",
        "-m",
        "intentbond",
        "check",
        "--repo",
        str(repo),
        "--scope",
        str(scope),
        "--base",
        base,
        "--candidate",
        "worktree",
        "--out",
        str(directory / "evidence"),
    ]
    record = {
        "schema_version": 1,
        "id": identity,
        "phase": phase,
        "command": command,
        "started_at": now(),
        "completed_at": None,
        "elapsed_seconds": None,
        "exit_code": None,
        "status": "incomplete",
        "base": base,
        "candidate_requested": "worktree",
        "source_path": str(repo),
        "scope_sha256": hashlib.sha256(Path(scope).read_bytes()).hexdigest(),
        "execution_environment": provenance.boundary(repo, command)
        if phase == "feedback"
        else None,
    }
    write(directory / "invocation.json", record, trusted_root=trusted_root)
    return directory, record


def finish(directory, record, started, exit_code, error_type=None, *, trusted_root=None):
    record.update(
        completed_at=now(),
        elapsed_seconds=round(time.monotonic() - started, 3),
        exit_code=exit_code,
        status="finished" if exit_code is not None else "incomplete",
        error_type=error_type,
    )
    write(directory / "invocation.json", record, trusted_root=trusted_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("root", "repo", "scope", "base"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    directory, record = begin(args.root, "feedback", args.repo, args.scope, args.base)
    print(f"Traceability invocation: {directory}", flush=True)
    started, code, error = time.monotonic(), None, None
    try:
        # The portable checker freezes the candidate and rejects source changes.
        with (directory / "checker.log").open("w") as log:
            result = subprocess.run(
                record["command"], check=False, stdout=log, stderr=subprocess.STDOUT
            )
        with (directory / "checker.log").open("rb") as log:
            log.seek(max(0, log.seek(0, 2) - 12000))
            print(log.read().decode(errors="replace"), flush=True)
        code = result.returncode
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        finish(directory, record, started, code, error)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
