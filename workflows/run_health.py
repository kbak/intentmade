"""Reconcile lost local execution without treating quiet work as failure."""

import datetime
import json
import os
import time
from pathlib import Path

import execution
import httpx
import reporting
from common import api, lock
from openhands.sdk.utils.files import atomic_write_text

ACTIVE = {"RUNNING", "REVIEWING"}


def controller_started_at():
    # Only a local run in this controller's Docker PID namespace can be proved
    # lost by its restart. Cloud workers may outlive a controller restart.
    if not Path("/.dockerenv").exists():
        return None
    fields = Path("/proc/1/stat").read_text().rpartition(")")[2].split()
    started = int(fields[19]) / os.sysconf("SC_CLK_TCK")
    return time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) + started


def find_run(run_id, automation_id=None):
    if automation_id:
        automations = [{"id": automation_id}]
    else:
        # Legacy task reports did not retain the automation id.
        automations, offset = [], 0
        while True:
            page = api("GET", "/api/automation/v1", params={"limit": 100, "offset": offset})
            automations.extend(page["automations"])
            if len(automations) >= page["total"] or not page["automations"]:
                break
            offset = len(automations)
    for automation in automations:
        offset = 0
        while True:
            runs = api(
                "GET",
                f"/api/automation/v1/{automation['id']}/runs",
                params={"limit": 100, "offset": offset},
            )["runs"]
            for run in runs:
                if run["id"] == run_id:
                    return run
            if len(runs) < 100:
                break
            offset += len(runs)
    return None


def stopped_reason(run):
    if run["status"] in {"FAILED", "CANCELLED"}:
        return "The native automation stopped before the task reported completion."
    if run["status"] != "RUNNING" or run.get("sandbox_id"):
        return None
    if not run.get("bash_command_id") or not run.get("started_at"):
        return None
    page = None
    while True:
        data = api(
            "GET",
            "/api/bash/bash_events/search",
            params={
                "command_id__eq": run["bash_command_id"],
                "kind__eq": "BashOutput",
                "limit": 100,
                **({"page_id": page} if page else {}),
            },
        )
        if any(event.get("exit_code") is not None for event in data["items"]):
            return "The workflow process exited before the task reported completion."
        page = data.get("next_page_id")
        if not page:
            break
    started = datetime.datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
    controller = controller_started_at()
    if controller is not None and started.timestamp() < controller - 1:
        return "The controller restarted and the previous local workflow process was lost."
    return None


def notice_exists(conversation, marker):
    page = None
    while True:
        data = api(
            "GET",
            f"/api/conversations/{conversation}/events/search",
            params={"limit": 100, **({"page_id": page} if page else {})},
        )
        for event in data["items"]:
            if event.get("kind") == "MessageEvent" and event.get("source") == "user":
                message = event.get("llm_message") or {}
                if any(marker in part.get("text", "") for part in message.get("content", [])):
                    return True
        page = data.get("next_page_id")
        if not page:
            return False


def retain_interruption(task, run_id, message):
    artifact = Path("/projects/artifacts") / (run_id + "-" + task)
    result = artifact / "result.json"
    if result.exists():
        data = json.loads(result.read_text())
        if data.get("status") == "RUNNING":
            data.update(status="FAILED", error=message)
            atomic_write_text(result, json.dumps(data, indent=2) + "\n", mode=0o644)
    path = artifact / "stage-progress.json"
    if path.exists():
        data = json.loads(path.read_text())
        if data["status"] == "Running":
            data.update(status="Interrupted", next_action=message, updated_at=reporting.now())
            for stage in data["stages"].values():
                if stage["status"] == "Running":
                    stage["status"] = "Interrupted"
            # Preserve the last observed elapsed time; the stop time is unknown.
            progress = reporting.TaskProgress.__new__(reporting.TaskProgress)
            progress.data = data
            atomic_write_text(path, json.dumps(data, indent=2) + "\n", mode=0o644)
            atomic_write_text(path.with_suffix(".md"), progress.render(data) + "\n", mode=0o644)


# [impl->req~im-interrupted-run~1]
def reconcile(config):
    try:
        with lock(config["project"] + ".health"):
            _reconcile(config)
    except BlockingIOError:
        pass


def _reconcile(config):
    for path in reporting.report_path(config, "unused").parent.glob("*.json"):
        try:
            with execution.state(config, path.stem):
                _reconcile_task(config, path)
        except BlockingIOError:
            continue  # An active task owns its report; it is not lost execution.


def _reconcile_task(config, path):
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"Run health report unavailable: {type(exc).__name__}", flush=True)
        return
    pending = record.get("interruption", {})
    if pending.get("run_id") != record.get("run_id"):
        pending = {}
    if not record.get("run_id") or (
        record.get("status") not in ACTIVE and (not pending or pending.get("notified"))
    ):
        return
    try:
        run = find_run(record["run_id"], record.get("automation_id"))
        if not run or run.get("conversation_id") not in {None, record.get("conversation_id")}:
            return
        reason = pending.get("reason") or stopped_reason(run)
        if not reason:
            return
        # A late completion/new continuation must win over this old snapshot.
        fresh = json.loads(path.read_text())
        if fresh != record:
            return
        marker = f"Run `{run['id']}` was interrupted."
        message = (
            marker + " " + reason + "\n\nWorkspaces, task branches and evidence are preserved. "
            "Inspect the retained work, then reply `resume: retry` to continue the same task. "
            "No automatic retry was started."
        )
        if run["status"] == "RUNNING":
            try:
                api(
                    "POST",
                    f"/api/automation/v1/runs/{run['id']}/complete",
                    json={
                        "status": "FAILED",
                        "error": reason,
                        "conversation_id": record.get("conversation_id"),
                    },
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
                # The normal completion callback may have won the race.
                return
        elif run["status"] not in {"FAILED", "CANCELLED"}:
            return
        record.update(
            status="FAILED",
            updated_at=reporting.now(),
            failure=message,
            interruption={"run_id": run["id"], "reason": reason, "notified": False},
        )
        reporting.write_report(config, path.stem, record)
        try:
            retain_interruption(path.stem, run["id"], message)
        except (OSError, ValueError, KeyError) as exc:
            print(f"Interrupted progress retention unavailable: {type(exc).__name__}", flush=True)
        conversation = record.get("conversation_id")
        if conversation:
            if not notice_exists(conversation, marker):
                reporting.post(conversation, "**INTERRUPTED — action needed**\n\n" + message)
            api(
                "PATCH",
                f"/api/conversations/{conversation}",
                json={"title": f"{config['project']} — {path.stem} — INTERRUPTED"},
            )
            record["interruption"]["notified"] = True
            reporting.write_report(config, path.stem, record)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        # Unavailable evidence is not proof of a dead worker. Failed notice
        # delivery stays pending so the next scan can finish it.
        print(f"Run health reconciliation deferred: {type(exc).__name__}", flush=True)
