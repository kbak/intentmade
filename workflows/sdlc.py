"""Portable planning documents and source-bound continuation summaries."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import job_files
from common import git, identifier

DOCUMENTS = ("intent.md", "spec.md", "plan.md")
MAX_DOCUMENT_BYTES = 64 * 1024


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


# [impl->req~im-portable-plan~2]
def propose(task, bases, documents):
    """Discussion output, never implementation authorization by itself."""
    identifier(task)
    if (
        not isinstance(bases, dict)
        or not bases
        or any(
            not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)
            for revision in bases.values()
        )
    ):
        raise ValueError("Planning requires full repository commit IDs")
    for project in bases:
        identifier(project)
    if not isinstance(documents, dict) or set(documents) != set(DOCUMENTS):
        raise ValueError("Planning requires intent.md, spec.md and plan.md")
    for name, content in documents.items():
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content.encode()) > MAX_DOCUMENT_BYTES
        ):
            raise ValueError(f"{name} must contain 1–{MAX_DOCUMENT_BYTES} bytes of Markdown")
    return {
        "schema_version": 1,
        "task": task,
        "bases": bases,
        "directory": f"docs/changes/{task}",
        "documents": documents,
        "sha256": {name: digest(content.encode()) for name, content in documents.items()},
    }


def validate(package):
    if not isinstance(package, dict):
        raise ValueError("Work package must be a JSON object")
    expected = propose(
        package.get("task", ""), package.get("bases", {}), package.get("documents", {})
    )
    if package != expected:
        raise ValueError("Work package contents or identity changed; prepare it again")
    return expected


def accept(package, task, bases, request):
    validate(package)
    if package["task"] != task or package["bases"] != bases:
        raise ValueError("Plan task or repository bases changed; review and prepare a new package")
    if package["documents"]["spec.md"].strip() != request.strip():
        raise ValueError("Submitted specification differs from the planned specification")
    return package


def package_for(repository):
    path = Path(repository) / "factory-work-package.json"
    return validate(json.loads(path.read_text())) if path.exists() else None


# [impl->req~im-portable-plan~2]
def bind(repository, package):
    """Retain the accepted handoff outside worker-controlled Git metadata."""
    previous = package_for(repository)
    if previous is not None and package is not None and previous != package:
        raise ValueError("Retained task has another accepted plan; use a new task ID")
    if package is not None and previous is None:
        validate(package)
        path = Path(repository) / "factory-work-package.json"
        with path.open("xb") as handle:
            handle.write(encoded(package))
    return package if package is not None else previous


def files(package):
    return {
        **package["documents"],
        "accepted.json": encoded(
            {
                "schema_version": 1,
                "task": package["task"],
                "bases": package["bases"],
                "sha256": package["sha256"],
                "work_package_sha256": digest(encoded(package)),
                "acceptance": "Explicit submission of this work package for implementation",
            }
        ).decode(),
    }


def source_file(repository, revision, path):
    prefix = ["git", "--git-dir", str(repository)]
    entry = subprocess.run(
        [*prefix, "ls-tree", revision, "--", path], check=True, capture_output=True, timeout=30
    ).stdout
    if not entry:
        return None
    metadata = entry.split(b"\t", 1)[0].split()
    if len(metadata) != 3 or metadata[0] not in {b"100644", b"100755"} or metadata[1] != b"blob":
        raise ValueError("Accepted document must be a regular Git file: " + path)
    oid = metadata[2].decode()
    size = int(
        subprocess.run(
            [*prefix, "cat-file", "-s", oid], check=True, capture_output=True, timeout=30
        ).stdout
    )
    if size > MAX_DOCUMENT_BYTES:
        raise ValueError("Accepted document exceeds size limit: " + path)
    return subprocess.run(
        [*prefix, "cat-file", "blob", oid], check=True, capture_output=True, timeout=30
    ).stdout


# [impl->req~im-portable-plan~2]
def stage(states):
    """Write accepted artifacts before entering the worker, without following links."""
    for state in states.values():
        package = package_for(state["repository"])
        if package is None:
            continue
        root = Path(state["source"])
        directory = root
        for part in Path(package["directory"]).parts:
            directory /= part
            try:
                job_files.mkdir(root, directory)
            except FileExistsError:
                # The following mkdir/write opens each ancestor with O_NOFOLLOW.
                pass
        for name, content in files(package).items():
            path = directory / name
            # Never replace existing different task documentation on a retry.
            prior = source_file(
                state["repository"], state["branch"], package["directory"] + "/" + name
            )
            if name == "plan.md" and prior is not None:
                # Preserve the current plan on repair; the accepted version stays
                # in the controller package and the original staging commit.
                continue
            if prior is not None and prior != content.encode():
                raise ValueError("Accepted task document conflicts with retained source: " + name)
            job_files.write_text(root, path, content)
        # Native worktrees start from HEAD. Commit only these documents in this
        # disposable clone, before worktree creation; the normal export retains it.
        git(["-C", str(root), "add", "--", package["directory"]])
        if git(["-C", str(root), "diff", "--cached", "--quiet"], check=False).returncode:
            git(
                [
                    "-C",
                    str(root),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "commit",
                    "-m",
                    "docs: retain accepted intent, specification and plan",
                ]
            )


def verify(states):
    for state in states.values():
        package = package_for(state["repository"])
        if package is None:
            continue
        for name, content in files(package).items():
            actual = source_file(
                state["repository"], state["commit"], package["directory"] + "/" + name
            )
            if name == "plan.md":
                if actual is None or not actual.decode("utf-8").strip():
                    raise ValueError("The current implementation plan is missing or empty")
                state["plan"] = {
                    "path": package["directory"] + "/" + name,
                    "accepted_sha256": package["sha256"][name],
                    "current_sha256": digest(actual),
                }
                continue
            if actual != content.encode():
                raise ValueError("Implementation changed an accepted task document: " + name)


def instructions(states):
    packages = {
        name: state["work_package"]["directory"]
        for name, state in states.items()
        if state.get("work_package")
    }
    if not packages:
        return ""
    return (
        "\n\nAccepted planning handoffs (repository-relative directories):\n"
        + json.dumps(packages, indent=2)
        + "\nRead these intent.md, spec.md and plan.md documents before implementation or review. "
        "Preserve intent.md, spec.md and accepted.json. Keep plan.md current with the implementation; "
        "briefly explain changed steps there. Its accepted version is retained separately. "
        "Update the project's living intent/spec only where this change affects them; "
        "material changes to agreed behavior require NEEDS_INPUT."
    )


def accepted_plan_context(states, root):
    """Stage only changed plans' accepted versions for the existing reviewer."""
    references = {}
    for project, state in states.items():
        package = package_for(state["repository"])
        if package is None:
            continue
        current = source_file(
            state["repository"], state["commit"], package["directory"] + "/plan.md"
        )
        original = package["documents"]["plan.md"]
        if current == original.encode():
            continue
        directory = root / "accepted-plans"
        if not directory.exists():
            job_files.mkdir(root, directory)
        path = directory / (project + ".md")
        job_files.write_text(root, path, original)
        references[project] = str(path)
    return (
        (
            "\nController-retained accepted plans for comparison with current plan.md:\n"
            + json.dumps(references)
        )
        if references
        else ""
    )


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


# [impl->req~im-portable-handoff~1]
def handoff(artifact, outcome):
    """Controller-owned, readable at every phase, including failure and no-publish."""
    attempt = outcome.get("attempt", 0)
    review = read_json(artifact / f"review-{attempt}.json")
    record = {
        "schema_version": 1,
        "task": outcome["task"],
        "status": outcome["status"],
        "phase": outcome.get("phase", "PREPARING"),
        "attempt": attempt,
        "approved_request": "approved-request.md",
        "repositories": {},
        "review": {"path": f"review-{attempt}.json", "result": review} if review else None,
        "browser_qa": outcome.get("browser_qa", {}),
        "error": outcome.get("error"),
        "next_action": (
            "Inspect the validated changes and any draft PR; merge/deploy remains a separate decision."
            if outcome.get("phase") == "DONE"
            else outcome.get("error")
            or "Work is in progress; do not treat partial evidence as completion."
        ),
    }
    for project, state in outcome["repositories"].items():
        record["repositories"][project] = {
            key: state[key]
            for key in (
                "base",
                "commit",
                "branch",
                "summary",
                "work_package",
                "plan",
                "pull_request",
                "traceability",
            )
            if key in state
        }
        record["repositories"][project].update(
            test_exit_code=outcome.get("tests", {}).get(project),
            test_command=read_json(artifact / project / f"test-command-{attempt}.json"),
            test_log=f"{project}/tests-{attempt}.log",
        )
    (artifact / "handoff.json").write_bytes(encoded(record))
    lines = [
        f"# {outcome['task']} handoff",
        f"Status: {record['status']} / {record['phase']}",
        "Approved request: [approved-request.md](approved-request.md)",
        "This is an execution summary, not new authorization or a review policy.",
    ]
    for project, data in record["repositories"].items():
        lines += [
            f"## {project}",
            f"Base: `{data['base']}`",
            f"Candidate: `{data.get('commit', 'not retained yet')}`",
            f"Branch: `{data['branch']}`",
            data.get("summary", "No implementation summary yet."),
            f"Test exit code: `{data['test_exit_code']}`; [log]({data['test_log']}).",
        ]
        if data.get("work_package"):
            lines += [
                "Accepted documents and versions:",
                "```json",
                json.dumps(data["work_package"], indent=2),
                "```",
            ]
        if data.get("plan"):
            lines += [
                "Current plan identity (compare with its accepted version):",
                "```json",
                json.dumps(data["plan"], indent=2),
                "```",
            ]
        if not data.get("work_package"):
            lines += [
                "No separately accepted planning package was supplied; see the approved request and repository task documents."
            ]
        if data["test_command"]:
            lines += [
                "Recorded verification command:",
                "```json",
                json.dumps(data["test_command"], indent=2),
                "```",
            ]
        if data.get("pull_request"):
            lines += [f"[Pull request]({data['pull_request']})"]
    if review:
        lines += [
            f"## Review\n\n[Structured findings and coverage](review-{attempt}.json)",
            review.get("summary", ""),
        ]
    lines += [
        "## Next action",
        record["next_action"],
        "See handoff.json for structured results and browser/traceability limitations.",
    ]
    (artifact / "handoff.md").write_text("\n\n".join(lines) + "\n")


# [impl->req~im-portable-handoff~1]
def pr_evidence(artifact, config, repo):
    """Embed a durable review record in GitHub without modifying validated source."""
    handoff_path = artifact.parent / "handoff.json"
    if not handoff_path.exists():
        return ""
    record = read_json(handoff_path)
    state = record["repositories"][config["project"]]
    commit = git(["--git-dir", str(repo), "rev-parse", "HEAD"]).stdout.strip()
    if state.get("commit") != commit:
        raise ValueError("PR evidence does not describe the published commit")
    base_url = f"https://github.com/{config['repository']}"
    result = f"\n### Validation evidence\n\nSource: [`{commit}`]({base_url}/commit/{commit}).\n"
    if state.get("work_package"):
        directory = state["work_package"]["directory"]
        result += (
            "\nTask artifacts (accepted intent/spec, current plan, acceptance record): "
            + ", ".join(
                f"[{name}]({base_url}/blob/{commit}/{directory}/{name})"
                for name in (*DOCUMENTS, "accepted.json")
            )
            + ".\n"
        )
    result += f"\nController test exit code: `{state['test_exit_code']}`.\n"
    if state.get("test_command"):
        result += "\n```json\n" + json.dumps(state["test_command"]["command"], indent=2) + "\n```\n"
    review = record.get("review")
    if review:
        # Full structured findings stay in the local export; preserve the actual
        # human review report here, including advisory findings, with a size bound.
        report_path = artifact.parent / review["path"].replace(".json", ".md")
        report = (
            review["result"].get("summary", "")
            + "\n\n"
            + (
                report_path.read_text()
                if report_path.exists()
                else json.dumps(review["result"], indent=2)
            )
        )
        result += (
            "\n<details><summary>Independent review findings</summary>\n\n"
            + report[:16000]
            + "\n\n</details>\n"
        )
        if len(report) > 16000:
            result += "\nReview excerpt truncated; full report is in the retained handoff export.\n"
    # Snapshot the publication record: handoff.json gains PR URLs after publish.
    snapshot = artifact / "published-evidence.json"
    snapshot.write_bytes(handoff_path.read_bytes())
    result += f"\nRetained evidence: `{artifact.parent.name}/{artifact.name}/published-evidence.json` (SHA-256 `{digest(snapshot.read_bytes())}`). "
    result += "Full logs and structured findings are available in the operator's handoff export.\n"
    return result


def evidence_block(text):
    return (
        "\n<!-- intentmade-validation:start -->\n" + text + "\n<!-- intentmade-validation:end -->\n"
        if text
        else ""
    )


def update_pr_evidence(body, text):
    start, end = "<!-- intentmade-validation:start -->", "<!-- intentmade-validation:end -->"
    if start in body and end in body.split(start, 1)[1]:
        before, rest = body.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + evidence_block(text) + after
    return body.rstrip() + evidence_block(text)
