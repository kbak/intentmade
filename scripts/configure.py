"""One-shot setup and task submission through native OpenHands APIs."""

import argparse
import json
import os
import sys
import urllib.error
import uuid
from pathlib import Path

os.environ.setdefault("OH_SESSION_API_KEYS_0", Path("/run/secrets/canvas-key").read_text().strip())
sys.path.insert(0, "/opt/factory/workflows")
from common import ROOT, api, factories, git, github, identifier, projects, token  # noqa: E402


def records():
    result = []
    while True:
        data = api("GET", "/api/automation/v1", params={"limit": 100, "offset": len(result)})
        if isinstance(data, list):
            return result + data
        page = data.get("items", data.get("automations", []))
        result.extend(page)
        if not page or (len(result) >= data["total"] if "total" in data else len(page) < 100):
            return result


def install(definition, files, existing=None):
    from openhands.automation.execution import build_tarball

    upload = api(
        "POST",
        "/api/automation/v1/uploads?name=openhands-factory",
        content=build_tarball(files),
        headers={"Content-Type": "application/gzip"},
    )
    definition = {**definition, "tarball_path": upload["tarball_path"]}
    if not existing:
        initial = {key: value for key, value in definition.items() if key != "enabled"}
        initial["trigger"] = {
            "type": "event",
            "source": "custom",
            "on": "factory.setup",
            "filter": "`false`",
        }
        existing = api("POST", "/api/automation/v1", json=initial)["id"]
    result = api("PATCH", "/api/automation/v1/" + existing, json=definition)
    print("Native automation:", result["id"], "—", result["name"])
    return result


def files(job):
    result = {path.name: path.read_bytes() for path in (ROOT / "workflows").glob("*.py")}
    result["job.json"] = json.dumps(job)
    return result


def configure(paused=False):
    factories()  # Validate group references before changing native configuration.
    api(
        "POST",
        "/api/agent-profiles/factory-codex",
        json={
            "agent_kind": "acp",
            "acp_server": "codex",
            "acp_command": "codex-acp",
            "acp_session_mode": "agent",
            "mcp_server_refs": [],
        },
    )
    profile = api("GET", "/api/agent-profiles/factory-codex")["profile"]
    api("POST", "/api/agent-profiles/" + profile["id"] + "/activate")
    api(
        "PATCH",
        "/api/settings",
        json={"conversation_settings_diff": {"workspace": {"working_dir": "/projects"}}},
    )
    existing = {item["name"]: item for item in records()}
    configured = projects()
    credential = token()
    for project, config in configured.items():
        if not config["repository"]:
            continue
        label = config.get("issue_label")
        if label:
            try:
                from urllib.parse import quote

                github(
                    credential,
                    "GET",
                    f"/repos/{config['repository']}/labels/{quote(label, safe='')}",
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                github(
                    credential,
                    "POST",
                    f"/repos/{config['repository']}/labels",
                    body={
                        "name": label,
                        "color": "0E8A16",
                        "description": "Approved specification: implement, test, review, and open a draft PR",
                    },
                )
        definition = {
            "name": "Factory — " + project,
            "enabled": config["enabled"] and not paused,
            "trigger": {
                "type": "cron",
                "schedule": config["schedule"],
                "timezone": config["timezone"],
            },
            "entrypoint": "python monitor.py",
            "timeout": 7200,
        }
        previous = existing.get(definition["name"], {}).get("id")
        install(definition, files({"config": config}), previous)
    # Old scans cannot run alongside their replacement and duplicate work.
    for item in existing.values():
        if item["name"].startswith("GitHub triage — ") or (
            item["name"].startswith("Factory — ") and item["name"][10:] not in configured
        ):
            api("PATCH", "/api/automation/v1/" + item["id"], json={"enabled": False})


def submit(project, spec, task=None, run=False, publish=None):
    configured = projects()
    members = factories().get(project, [project])
    configs = [configured[member] for member in members]
    request = (sys.stdin.read() if spec == "-" else Path(spec).read_text()).strip()
    if not request:
        raise ValueError("The reviewed specification is empty")
    task = identifier(task or "feature-" + uuid.uuid4().hex[:12])
    name = "Build — " + project + " — " + task
    previous = next((x for x in records() if x["name"] == name), None)
    bases = {}
    credential = token() if any(c["repository"] for c in configs) else ""
    for config in configs:
        if config["repository"]:
            base = github(
                credential, "GET", f"/repos/{config['repository']}/commits/{config['branch']}"
            )["sha"]
        else:
            source = "/projects/repos/" + config["project"]
            base = git(
                ["-c", "safe.directory=" + source, "-C", source, "rev-parse", "HEAD"]
            ).stdout.strip()
        bases[config["project"]] = base
    job = {"configs": configs, "task": task, "bases": bases, "publish_draft": publish}
    bundle = files(job)
    bundle["request.md"] = request
    definition = {
        "name": name,
        "trigger": {
            "type": "event",
            "source": "custom",
            "on": "factory.manual-build",
            "filter": "`false`",
        },
        "entrypoint": "python run.py",
        "timeout": 7200,
    }
    result = install(definition, bundle, previous["id"] if previous else None)
    print("Task:", task, "— approved bases:", json.dumps(bases))
    if run:
        result = api("POST", "/api/automation/v1/" + result["id"] + "/dispatch")
        print("Native run:", result["id"])
    else:
        print("Inspect the native automation and select Run now to start.")


def review(project, number):
    config = projects()[project]
    name = f"Review — {project} — PR {number}"
    previous = next((x for x in records() if x["name"] == name), None)
    definition = {
        "name": name,
        "trigger": {
            "type": "event",
            "source": "custom",
            "on": "factory.manual-review",
            "filter": "`false`",
        },
        "entrypoint": "python monitor.py",
        "timeout": 7200,
    }
    result = install(
        definition,
        files({"config": config, "review_pr": number}),
        previous["id"] if previous else None,
    )
    result = api("POST", "/api/automation/v1/" + result["id"] + "/dispatch")
    print("Native review run:", result["id"])


def retry_issue(project, number, answer=None):
    """Resume one issue without clearing scheduler history or changing its spec."""
    from approval import approved_issue
    from common import lock
    from reporting import read_report

    config = projects()[project]
    # Serialize submissions, but let a different issue queue behind a running
    # build. Execution takes the repository locks before touching task stores.
    with lock(project + ".queue"):
        record = read_report(config, "issue-" + str(number))
        _, snapshot = approved_issue(
            config, number, token(), expected=record.get("snapshot") if record else None
        )
        response = (
            (sys.stdin.read() if answer == "-" else Path(answer).read_text())
            if answer
            else "Retry the retained task after the factory repair."
        )
        name = f"Continue — {project} — issue {number}"
        previous = next((x for x in records() if x["name"] == name), None)
        if previous:
            active = api(
                "GET", "/api/automation/v1/" + previous["id"] + "/runs", params={"limit": 100}
            )["runs"]
            if any(r["status"] in {"PENDING", "RUNNING"} for r in active):
                raise RuntimeError("This issue already has an active continuation")
        result = install(
            {
                "name": name,
                "trigger": {
                    "type": "event",
                    "source": "custom",
                    "on": "factory.continue-issue",
                    "filter": "`false`",
                },
                "entrypoint": "python monitor.py",
                "timeout": 7200,
            },
            files(
                {
                    "config": config,
                    "retry_issue": number,
                    "resume": {"snapshot": snapshot, "answer": response},
                }
            ),
            previous["id"] if previous else None,
        )
        result = api("POST", "/api/automation/v1/" + result["id"] + "/dispatch")
        print("Native continuation run:", result["id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("configure").add_argument("--paused", action="store_true")
    sub.add_parser("github-login")
    sub.add_parser("projects")
    sub.add_parser("factories")
    review_command = sub.add_parser("review")
    review_command.add_argument("project")
    review_command.add_argument("number", type=int)
    retry = sub.add_parser("retry-issue")
    retry.add_argument("project")
    retry.add_argument("number", type=int)
    retry.add_argument("--answer-file")
    command = sub.add_parser("submit")
    command.add_argument("project")
    command.add_argument("spec")
    command.add_argument("--task")
    command.add_argument("--run", action="store_true")
    command.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    if args.action == "github-login":
        api(
            "PUT",
            "/api/settings/secrets",
            json={"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "value": sys.stdin.read().strip()},
        )
    elif args.action == "projects":
        print(json.dumps(projects(), indent=2))
    elif args.action == "factories":
        print(json.dumps(factories(), indent=2))
    elif args.action == "configure":
        configure(args.paused)
    elif args.action == "review":
        review(args.project, args.number)
    elif args.action == "retry-issue":
        retry_issue(args.project, args.number, args.answer_file)
    else:
        submit(args.project, args.spec, args.task, args.run, False if args.no_publish else None)
