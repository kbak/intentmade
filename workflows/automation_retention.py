"""Retire resolved one-shot recipes while preserving evidence and dispatch identity."""

import datetime
import hashlib
import json
import os
import re

import common
import deployment
import httpx
import reporting
from openhands.sdk.utils.files import atomic_write_text

TERMINAL = {"COMPLETED", "SKIPPED", "FAILED", "CANCELLED"}
RESOLVED = {"PASSED", "REVIEWED", "WATCHING", "CI_PASSED"}


def receipt_path(name):
    return (
        common.DATA
        / "automation-retirement"
        / (hashlib.sha256(name.encode()).hexdigest() + ".json")
    )


def archive_path(definition):
    return (
        common.DATA
        / "automation-retirement"
        / "archives"
        / (common.identifier(definition["id"]) + ".json")
    )


def retired(name):
    """Keep immutable recipe identity even if DELETE's acknowledgement is lost."""
    path = receipt_path(name)
    if not path.exists():
        return False
    record = json.loads(path.read_text())
    if record["name"] != name:
        raise ValueError("Automation retirement receipt identity mismatch")
    if record.get("delete_requested") is not True:
        raise ValueError("Automation retirement dispatch receipt is incomplete")
    return True


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_text(path, json.dumps(value, indent=2) + "\n", mode=0o600)
    # Deletion must not outrun its evidence or durable duplicate-dispatch guard.
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def timestamp(value):
    result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Retirement timestamps must include a timezone")
    return result


def managed(definition, project):
    trigger = definition.get("trigger", {})
    if (
        definition.get("entrypoint") != "python monitor.py"
        or trigger.get("type") != "event"
        or trigger.get("source") != "custom"
        or trigger.get("filter") != "`false`"
    ):
        return False
    name = definition.get("name", "")
    return bool(
        (
            trigger.get("on") == "factory.scheduled-task"
            and re.fullmatch(r"Task — " + re.escape(project) + r" — .+ — [0-9a-f]{24}", name)
        )
        or (
            trigger.get("on") == "factory.continue-issue"
            and re.fullmatch(r"Continue — " + re.escape(project) + r" — issue [1-9][0-9]*", name)
        )
    )


def runs_for(automation_id):
    runs = []
    while True:
        page = common.api(
            "GET",
            f"/api/automation/v1/{automation_id}/runs",
            params={"limit": 100, "offset": len(runs)},
        )
        runs.extend(page["runs"])
        if len(runs) >= page["total"]:
            if len(runs) != page["total"] or len({r["id"] for r in runs}) != len(runs):
                raise ValueError("Native run inventory changed during pagination")
            return runs
        if not page["runs"]:
            raise ValueError("Native run inventory is incomplete")


def reports_for(config):
    return [
        json.loads(path.read_text())
        for path in reporting.report_path(config, "unused").parent.glob("*.json")
    ]


def resolved(definition, runs, reports):
    if not runs or any(r.get("status") not in TERMINAL or not r.get("completed_at") for r in runs):
        return False
    latest = max(runs, key=lambda r: timestamp(r["created_at"]))
    if latest["status"] not in {"COMPLETED", "SKIPPED"}:
        return False
    ids = {r["id"] for r in runs}
    linked = [
        r for r in reports if r.get("automation_id") == definition["id"] or r.get("run_id") in ids
    ]
    if any(r.get("status") not in RESOLVED for r in linked):
        return False
    # A skipped run or continuation without its task report is ambiguous.
    return bool(linked) or (
        latest["status"] == "COMPLETED" and definition["name"].startswith("Task — ")
    )


def identity(definition, runs):
    # Native state/updated_at change when disabled; recipe contents must not.
    return {
        "definition": {
            k: definition.get(k)
            for k in (
                "id",
                "name",
                "trigger",
                "entrypoint",
                "tarball_path",
                "timeout",
                "setup_script_path",
                "prompt",
                "model",
                "agent_profile_id",
                "keep_alive",
            )
        },
        "runs": sorted([r["id"], r["status"], r["completed_at"]] for r in runs),
    }


# [impl->req~im-automation-retention~1]
def sweep(config, *, dry_run=True, scheduled=False, now=None):
    """Operator policy is read live; saved recipes cannot shorten retention."""
    options = deployment.settings()["automation_retention"]
    days = options["repositories"].get(config["project"], options["days"])
    if days is None:
        return []
    # Repointed or removed registrations cannot retire the previous repository.
    directory = common.ROOT / "config"
    current = deployment.read(
        directory / "repositories" / (common.identifier(config["project"]) + ".json")
    )
    repository = current.get("repository")
    if (
        not repository
        or common.issues.normalize_repo(repository).casefold() != config["repository"].casefold()
    ):
        return []
    now = now or datetime.datetime.now(datetime.UTC)
    from native_queue import registration

    with common.lock(config["project"] + ".poll"), common.lock(config["project"] + ".queue"):
        marker = common.DATA / "automation-retirement" / (config["project"] + ".last-scan")
        if (
            scheduled
            and marker.exists()
            and now - timestamp(marker.read_text()) < datetime.timedelta(days=1)
        ):
            return []
        actions = []
        for definition in registration().records():
            if not managed(definition, config["project"]):
                continue
            action = retire_one(config, definition, days, now, dry_run=dry_run)
            if action:
                actions.append(action)
        if scheduled and not dry_run:
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_text(marker, now.isoformat(), mode=0o600)
        return actions


def retire_one(config, definition, days, now, *, dry_run):
    base = "/api/automation/v1/" + definition["id"]
    runs = runs_for(definition["id"])
    if not resolved(definition, runs, reports_for(config)):
        return None
    path = archive_path(definition)
    record = json.loads(path.read_text()) if path.exists() else {}
    unchanged = record.get("identity") == identity(definition, runs)
    if not unchanged or definition["enabled"]:
        record = {
            "definition": definition,
            "runs": runs,
            "identity": identity(definition, runs),
            "retention_started_at": now.isoformat(),
            "delete_requested": False,
        }
    due = now >= timestamp(record["retention_started_at"]) + datetime.timedelta(days=days)
    action = {
        "id": definition["id"],
        "name": definition["name"],
        "action": "delete" if due else "disable" if definition["enabled"] else "retain",
    }
    if dry_run:
        return action
    # Always archive before disabling/deleting. Unknown or failed writes stop here.
    save(path, record)
    if definition["enabled"]:
        common.api("PATCH", base, json={"enabled": False})
        return action
    if not due:
        return action
    # Check again after archiving; explicit factory dispatch uses the same locks.
    fresh = common.api("GET", base)
    fresh_runs = runs_for(definition["id"])
    if (
        fresh["enabled"]
        or identity(fresh, fresh_runs) != record["identity"]
        or not resolved(fresh, fresh_runs, reports_for(config))
    ):
        return None
    record.update(definition=fresh, runs=fresh_runs, delete_requested=True)
    save(path, record)
    save(
        receipt_path(definition["name"]),
        {
            "name": definition["name"],
            "automation_id": definition["id"],
            "delete_requested": True,
        },
    )
    common.api("DELETE", base)
    return action


def scheduled_cleanup(config):
    try:
        actions = sweep(config, dry_run=False, scheduled=True)
        changed = [action for action in actions if action["action"] != "retain"]
        if changed:
            print("Automation retirement: " + json.dumps(changed), flush=True)
    except BlockingIOError:
        pass  # A scan/submission owns the repository; try on a later scan.
    except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError) as exc:
        print(f"Automation retirement deferred: {type(exc).__name__}", flush=True)
