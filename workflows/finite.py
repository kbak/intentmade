"""Run finite operator recipes through the existing native reporting lifecycle."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

import provenance
from common import api
from reporting import outcome, run_report


def now():
    return datetime.now(UTC).isoformat()


def validate(request):
    command = request.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
    ):
        raise ValueError("Finite command must be a nonempty argument list, not shell text")
    timeout = request.get("timeout", 1200)
    if type(timeout) is not int or not 1 <= timeout <= 7000:
        raise ValueError("Finite timeout must be 1–7000 seconds")
    for name in request.get("files", {}):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name:
            raise ValueError("Finite payload names must be normalized relative paths")
    return timeout


def receipt_path(run_id, root=Path("/projects/artifacts")):
    return root / (str(UUID(run_id)) + "-finite") / "result.json"


def save(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(path)


# [impl->req~im-finite-timeout~1]
def execute(command, timeout):
    # A timed-out recipe must stop its child processes before reporting failure.
    with subprocess.Popen(command, start_new_session=True) as process:
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


# [impl->req~im-finite-completion~1]
def run(request, root=Path("/projects/artifacts")):
    run_id = os.environ["AUTOMATION_RUN_ID"]
    path = receipt_path(run_id, root)
    # A lost callback must never turn a second launch into a second execution.
    path.parent.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": now(),
        "execution_status": "RUNNING",
        "acknowledged_at": None,
        "execution_environment": provenance.boundary(request.get("source"), request.get("command")),
    }
    save(path, record)
    started = time.monotonic()
    try:
        with run_report() as report:
            try:
                timeout = validate(request)
                provenance.required(
                    record["execution_environment"], request.get("required_environment", {})
                )
                for name, expected in request.get("digests", {}).items():
                    if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
                        raise ValueError("Finite uploaded input changed: " + name)
                code = execute(request["command"], timeout)
                record.update(
                    exit_code=code, execution_status="COMPLETED" if code == 0 else "FAILED"
                )
            except BaseException as exc:
                code = 1
                record.update(
                    exit_code=None, execution_status="FAILED", error_type=type(exc).__name__
                )
            record.update(execution_finished_at=now(), execution_seconds=time.monotonic() - started)
            save(path, record)
            outcome(
                record["execution_status"], "Finite recipe execution " + record["execution_status"]
            )
            report["receipt"] = str(path)
            report["execution_seconds"] = record["execution_seconds"]
            if code:
                report["error"] = "Recipe failed; inspect native logs and " + str(path)
            callback_started = time.monotonic()
        record.update(acknowledged_at=now(), callback_seconds=time.monotonic() - callback_started)
        save(path, record)
        return code
    except BaseException as exc:
        record["acknowledgement_error_type"] = type(exc).__name__
        save(path, record)
        raise


# [impl->req~im-finite-completion~1]
def inspect_status(automation_id, run_id, root=Path("/projects/artifacts")):
    UUID(automation_id)
    path = receipt_path(run_id, root)
    record = json.loads(path.read_text()) if path.exists() else None
    offset = 0
    selected = None
    while True:
        runs = api(
            "GET",
            f"/api/automation/v1/{automation_id}/runs",
            params={"limit": 100, "offset": offset},
        )["runs"]
        selected = next((item for item in runs if item["id"] == run_id), None)
        if selected or len(runs) < 100:
            break
        offset += len(runs)
    native = selected["status"] if selected else "MISSING"
    mismatch = bool(
        record and record.get("execution_finished_at") and native in {"PENDING", "RUNNING"}
    )
    return {
        "run_id": run_id,
        "native_status": native,
        "execution": record,
        "diagnostic": "Execution ended but native completion is unacknowledged; reconcile the callback, do not rerun the recipe."
        if mismatch
        else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    raise SystemExit(run(json.loads(args.request.read_text())))
