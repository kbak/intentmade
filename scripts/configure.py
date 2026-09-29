"""One-shot setup and task submission through native OpenHands APIs."""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import uuid
from pathlib import Path

import httpx

os.environ.setdefault("OH_SESSION_API_KEYS_0", Path("/run/secrets/canvas-key").read_text().strip())
sys.path.insert(0, "/opt/factory/workflows")
from common import ROOT, api, factories, git, github, identifier, projects, token  # noqa: E402


def records():
    result = []
    while True:
        data = api("GET", "/api/automation/v1", params={"limit": 100, "offset": len(result)})
        page = data["automations"]
        result.extend(page)
        if not page or len(result) >= data["total"]:
            return result


def persisted(path, expected):
    # The native FastAPI dependency commits after returning its response. Wait
    # for readback before dependent writes; never repeat a creation or dispatch.
    deadline = time.monotonic() + 10
    while True:
        try:
            record = api("GET", path)
            if all(record.get(key) == value for key, value in expected.items()):
                return record
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        if time.monotonic() >= deadline:
            raise RuntimeError("Native automation write is not visible yet: " + path)
        time.sleep(0.1)


def install(definition, files, existing=None):
    from openhands.automation.execution import build_tarball

    upload = api(
        "POST",
        "/api/automation/v1/uploads?name=intentmade",
        content=build_tarball(files),
        headers={"Content-Type": "application/gzip"},
    )
    persisted("/api/automation/v1/uploads/" + upload["id"], {"status": "COMPLETED"})
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
        persisted("/api/automation/v1/" + existing, {"id": existing})
    result = api("PATCH", "/api/automation/v1/" + existing, json=definition)
    persisted("/api/automation/v1/" + existing, {key: result[key] for key in definition})
    print("Native automation:", result["id"], "—", result["name"])
    return result


def files(job):
    result = {path.name: path.read_bytes() for path in (ROOT / "workflows").glob("*.py")}
    result.update(
        (str(path.relative_to(ROOT / "workflows")), path.read_bytes())
        for path in (ROOT / "workflows/skills").rglob("*")
        if path.is_file()
    )
    result.update(
        (str(path.relative_to(ROOT / "workflows")), path.read_bytes())
        for path in (ROOT / "workflows/traceability").rglob("*.py")
    )
    result["job.json"] = json.dumps(job)
    return result


def finite(request_path, run=False):
    """Register a finite recipe with mandatory native completion reporting."""
    from finite import validate

    request_path = Path(request_path)
    request = json.loads(request_path.read_text())
    timeout = validate(request)
    if not isinstance(request.get("name"), str) or not request["name"].strip():
        raise ValueError("Finite automation requires a name")
    payload = files({})
    reserved = set(payload) | {"finite-request.json"}
    declared = request.get("files", {})
    if not isinstance(declared, dict) or not declared:
        raise ValueError("Finite automation requires explicit payload files")
    digests = {}
    for name, source in declared.items():
        if name in reserved:
            raise ValueError("Finite payload cannot replace factory lifecycle code: " + name)
        data = (request_path.parent / source).read_bytes()
        payload[name] = data
        digests[name] = hashlib.sha256(data).hexdigest()
    payload["finite-request.json"] = json.dumps(
        {
            "command": request["command"],
            "timeout": timeout,
            "digests": digests,
            "source": request.get("source"),
            "required_environment": request.get("required_environment", {}),
        }
    )
    existing = next((a for a in records() if a["name"] == request["name"]), None)
    if existing:
        raise ValueError(
            "Finite automation name already exists; inspect its runs before creating another"
        )
    result = install(
        {
            "name": request["name"],
            "enabled": True,
            "trigger": {
                "type": "event",
                "source": "custom",
                "on": "factory.finite",
                "filter": "`false`",
            },
            "entrypoint": "python finite.py finite-request.json",
            "timeout": timeout + 120,
        },
        payload,
    )
    if run:
        result = api("POST", "/api/automation/v1/" + result["id"] + "/dispatch")
        print("Native finite run:", result["id"])
    return result


# [impl->req~im-agent-profile~1]
def configure(paused=False):
    factories()  # Validate group references before changing native configuration.
    from urllib.parse import quote

    from harness import profile_name

    selected_profile = profile_name()
    profile_path = "/api/agent-profiles/" + quote(selected_profile, safe="")
    try:
        profile = api("GET", profile_path)["profile"]
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404 or selected_profile != "factory-codex":
            raise
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
        scheduler = install(definition, files({"config": config}), previous)
        from reporting import write_report

        reply_name = "Resume — " + project
        continuation = install(
            {
                "name": reply_name,
                "enabled": definition["enabled"],
                "trigger": {
                    "type": "event",
                    "source": "custom",
                    "on": "factory.reply",
                    "filter": "`false`",
                },
                "entrypoint": "python monitor.py",
                "timeout": 7200,
            },
            files({"config": config, "resume_replies": True}),
            existing.get(reply_name, {}).get("id"),
        )
        write_report(
            config,
            "reply-trigger",
            {"automation_id": continuation["id"], "scheduler_id": scheduler["id"]},
        )
    # Old scans cannot run alongside their replacement and duplicate work.
    for item in existing.values():
        if (
            item["name"].startswith("GitHub triage — ")
            or (item["name"].startswith("Factory — ") and item["name"][10:] not in configured)
            or (item["name"].startswith("Resume — ") and item["name"][9:] not in configured)
        ):
            api("PATCH", "/api/automation/v1/" + item["id"], json={"enabled": False})


def discussion(project):
    """Read repository configuration and stage-specific guidance without dispatch."""
    from traceability import discussion_context

    configured = projects()
    members = factories().get(project, [project])
    return discussion_context([configured[member] for member in members])


def selected_bases(configs):
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
    return bases


def plan(project, documents, task=None):
    """Freeze discussion documents and source identity; never dispatch a build."""
    from sdlc import propose

    configured = projects()
    configs = [configured[member] for member in factories().get(project, [project])]
    return propose(task or "feature-" + uuid.uuid4().hex[:12], selected_bases(configs), documents)


def submit(
    project,
    spec,
    task=None,
    run=False,
    publish=None,
    inputs=None,
    work_package=None,
    request_text=None,
):
    from input_artifacts import validate
    from sdlc import accept

    declared = validate([] if inputs is None else inputs)
    configured = projects()
    members = factories().get(project, [project])
    configs = [configured[member] for member in members]
    request = (
        request_text
        if request_text is not None
        else (sys.stdin.read() if spec == "-" else Path(spec).read_text())
    ).strip()
    if not request:
        raise ValueError("The reviewed specification is empty")
    task = identifier(
        task or (work_package or {}).get("task") or "feature-" + uuid.uuid4().hex[:12]
    )
    name = "Build — " + project + " — " + task
    previous = next((x for x in records() if x["name"] == name), None)
    bases = selected_bases(configs)
    job = {"configs": configs, "task": task, "bases": bases, "publish_draft": publish}
    if work_package is not None:
        job["work_package"] = accept(work_package, task, bases, request)
    if declared:
        job["input_artifacts"] = declared
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
    sub.add_parser("codex-login")
    sub.add_parser("codex-logout")
    sub.add_parser("projects")
    sub.add_parser("factories")
    finite_command = sub.add_parser("finite")
    finite_command.add_argument("request")
    finite_command.add_argument("--run", action="store_true")
    finite_status = sub.add_parser("finite-status")
    finite_status.add_argument("automation_id")
    finite_status.add_argument("run_id")
    sub.add_parser("discussion").add_argument("project")
    planning = sub.add_parser("plan")
    planning.add_argument("project")
    planning.add_argument(
        "documents", help="JSON object containing intent.md, spec.md, plan.md text; - for stdin"
    )
    planning.add_argument("--task")
    planning.add_argument("--output", help="Save proposed work package here instead of stdout")
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
    command.add_argument(
        "--work-package", help="Previously prepared JSON package accepted by this submission"
    )
    command.add_argument("--submission-stdin", action="store_true", help=argparse.SUPPRESS)
    command.add_argument(
        "--inputs-json", help="JSON array of declared controller artifact references"
    )
    command.add_argument("--run", action="store_true")
    command.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    if args.action == "github-login":
        api(
            "PUT",
            "/api/settings/secrets",
            json={"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "value": sys.stdin.read().strip()},
        )
    elif args.action == "codex-login":
        from codex_login import login

        login()
    elif args.action == "codex-logout":
        api("DELETE", "/api/settings/secrets/CODEX_AUTH_JSON")
        print("Factory Codex login removed. Existing workers are not cancelled.", flush=True)
    elif args.action == "projects":
        print(json.dumps(projects(), indent=2))
    elif args.action == "factories":
        print(json.dumps(factories(), indent=2))
    elif args.action == "finite":
        finite(args.request, args.run)
    elif args.action == "finite-status":
        from finite import inspect_status

        print(json.dumps(inspect_status(args.automation_id, args.run_id), indent=2))
    elif args.action == "discussion":
        print(json.dumps(discussion(args.project), indent=2))
    elif args.action == "plan":
        documents = json.loads(
            sys.stdin.read() if args.documents == "-" else Path(args.documents).read_text()
        )
        package = json.dumps(plan(args.project, documents, args.task), indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(package)
        else:
            print(package, end="")
    elif args.action == "configure":
        configure(args.paused)
    elif args.action == "review":
        review(args.project, args.number)
    elif args.action == "retry-issue":
        retry_issue(args.project, args.number, args.answer_file)
    else:
        envelope = json.loads(sys.stdin.read()) if args.submission_stdin else {}
        submit(
            args.project,
            args.spec,
            args.task,
            args.run,
            False if args.no_publish else None,
            json.loads(args.inputs_json) if args.inputs_json else None,
            json.loads(Path(args.work_package).read_text())
            if args.work_package
            else (envelope.get("work_package")),
            envelope.get("request"),
        )
