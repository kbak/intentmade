"""Approved task → native worktree → Codex/tests/review → upstream draft PR."""

import json
import os
import shlex
import shutil
import signal
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Literal

from agent import converse, worktree
from common import DATA, evidence, git, github, identifier, issues, job_id, lock, token
from openhands.sdk.workspace import LocalWorkspace
from openhands.sdk.workspace.repo import RepoSource, clone_repos
from pydantic import BaseModel
from sandbox import worker


class ReviewResult(BaseModel):
    verdict: Literal["PASS", "CHANGES_REQUESTED"]
    summary: str


def task_repository(config, task, base, credential):
    repository = DATA / "tasks" / config["project"] / (identifier(task) + ".git")
    branch = config["branch_prefix"] + "/" + task
    if not repository.exists():
        repository.parent.mkdir(parents=True, exist_ok=True)
        try:
            if config["repository"]:
                git(
                    [
                        "clone",
                        "--bare",
                        "--single-branch",
                        "--branch",
                        config["branch"],
                        "https://github.com/" + config["repository"] + ".git",
                        str(repository),
                    ],
                    token=credential,
                )
            else:
                # Fixture catalogs are a read-only bind owned by the WSL user.
                with tempfile.TemporaryDirectory(dir=DATA) as temp:
                    source = Path(temp) / "source"
                    shutil.copytree(Path("/projects/repos") / config["project"], source)
                    git(["clone", "--bare", str(source), str(repository)])
            git(["--git-dir", str(repository), "update-ref", "refs/heads/" + branch, base])
            git(["--git-dir", str(repository), "config", "factory.base", base])
        except BaseException:
            shutil.rmtree(repository, ignore_errors=True)
            raise
    git(["--git-dir", str(repository), "symbolic-ref", "HEAD", "refs/heads/" + branch])
    return repository, branch


def publish(config, task, repo, branch, artifact, request, credential, issue=None):
    title = (f"[#{issue}] " if issue else "") + request.splitlines()[0].lstrip("# ")[:180]
    body = (
        "Implemented by the local OpenHands factory.\n\n"
        + request[:35000]
        + "\n\n### Validation\n\nTests passed; independent review returned PASS.\n\n"
        + f"Factory task: `{task}`. Evidence: `{artifact.parent.name}/{artifact.name}` in local Canvas.\n"
        + (f"\nCloses #{issue}\n" if issue else "")
    )
    if issue:
        from approval import approved_issue

        current = github(credential, "GET", f"/repos/{config['repository']}/issues/{issue}")
        if (
            current["state"] != "open"
            or (config.get("issue_label") and config["issue_label"] not in issues._labels(current))
            or {a["login"] for a in current.get("assignees", [])} != {config["assignee"]}
        ):
            raise RuntimeError(
                "Issue closed, ownership changed or required label removed; publication withheld"
            )
        if not config.get("issue_approval"):
            raise RuntimeError("Issue approval snapshot is missing; publication withheld")
        approved_issue(config, issue, credential, expected=config["issue_approval"])
    # Use upstream authenticated Git operations and idempotent draft PR creation.
    issues._push_branch(repo, branch, credential)
    result = issues._open_pull_request(
        credential, config["repository"], branch, config["branch"], title, body
    )
    (artifact / "pull-request.json").write_text(json.dumps(result, indent=2))
    return result["html_url"]


def build(config, task, request, base, credential="", issue=None, publish_draft=None):
    return build_group(
        [config],
        task,
        request,
        {config["project"]: base},
        credential,
        issue=issue,
        publish_draft=publish_draft,
    )


def build_group(configs, task, request, bases, credential="", issue=None, publish_draft=None):
    """Single-repository and grouped tasks share this pipeline and native workspaces."""
    identifier(task)
    if not configs or len({c["project"] for c in configs}) != len(configs):
        raise ValueError("A task needs unique repositories")
    if issue is not None and len(configs) != 1:
        raise ValueError("Issue approval applies only to its own repository")
    artifact = evidence(job_id() + "-" + task)
    with ExitStack() as locks:
        for config in sorted(configs, key=lambda c: c["project"]):
            locks.enter_context(lock(config["project"] + ".build"))
        states = {}
        for config in configs:
            project = config["project"]
            repository, branch = task_repository(config, task, bases[project], credential)
            states[project] = {
                "branch": branch,
                "base": git(
                    ["--git-dir", str(repository), "config", "factory.base"]
                ).stdout.strip(),
                "repository": str(repository),
            }
        return execute_build(
            configs, task, request, credential, issue, publish_draft, artifact, states
        )


def execute_build(configs, task, request, credential, issue, publish_draft, artifact, states):
    outcome = {"task": task, "status": "FAILED", "repositories": states}

    def save():
        (artifact / "result.json").write_text(json.dumps(outcome, indent=2))

    with tempfile.TemporaryDirectory(dir=DATA, prefix="job-") as temp:
        root = Path(temp)
        sources = []
        for state in states.values():
            repository = Path(state["repository"])
            tip = git(["--git-dir", str(repository), "rev-parse", state["branch"]]).stdout.strip()
            # SHA refs ask the native helper for full history, needed on continuation.
            sources.append(RepoSource(url=repository.as_uri(), ref=tip))
        cloned = clone_repos(sources, root / "source")
        if cloned.failed_repos:
            save()
            raise RuntimeError("OpenHands could not prepare all task repositories")
        active = root / "active"
        active.mkdir()
        try:
            with worker(root, configs) as workspace:
                for project, state in states.items():
                    source = Path(
                        cloned.repo_mappings[Path(state["repository"]).as_uri()].local_path
                    )
                    git(["checkout", "-B", "main", "HEAD"], cwd=source)
                    git(["remote", "remove", "origin"], cwd=source)
                    git(["config", "user.name", "OpenHands Factory"], cwd=source)
                    git(["config", "user.email", "factory@localhost"], cwd=source)
                    workspace.working_dir = str(source)
                    state["conversation"] = worktree(workspace)
                    checkout = Path(workspace.working_dir)
                    state["worktree"] = str(checkout)
                    state["source"] = str(source)
                    # The full native clone also contains this branch. Replace that
                    # unused ref only in the disposable clone, at the same revision.
                    git(["branch", "-M", state["branch"]], cwd=checkout)
                    (active / project).symlink_to(checkout, target_is_directory=True)
                workspace.working_dir = (
                    str(active) if len(states) > 1 else next(iter(states.values()))["worktree"]
                )
                conversation_id = (
                    None if len(states) > 1 else next(iter(states.values()))["conversation"]
                )
                context = "\n".join(
                    f"{project}: {state['worktree']}; branch {state['branch']}; review base {state['base']}"
                    for project, state in states.items()
                )
                prompt = (
                    request
                    + "\n\nTask repositories:\n"
                    + context
                    + "\n\nImplement this approved specification. Read guidance in EACH repository. "
                    "Keep the listed branches. Change only these task worktrees. "
                    "Do not publish, push, or change /factory-tests. Leave changes uncommitted."
                )
                for attempt in range(min(c["repair_attempts"] for c in configs) + 1):
                    implementation = converse(
                        workspace, prompt, "agent-full-access", "Implementation", conversation_id
                    )
                    (artifact / f"implementation-{attempt}.md").write_text(implementation)
                    results, test_output = {}, []
                    for index, config in enumerate(configs):
                        project = config["project"]
                        checkout = states[project]["worktree"]
                        repo_artifact = artifact / project
                        repo_artifact.mkdir(exist_ok=True)
                        env = {
                            "PROJECT_DIR": checkout,
                            "FACTORY_WORKSPACE": str(active),
                            "COMPOSE_PROJECT_NAME": f"factory-tests-{index}",
                        }
                        if config.get("test_profile"):
                            env["FACTORY_TESTS"] = "/factory-tests/" + config["test_profile"]
                        command = " ".join(
                            key + "=" + shlex.quote(value) for key, value in env.items()
                        )
                        result = workspace.execute_command(
                            command + " bash -c " + shlex.quote(config["test_command"]),
                            cwd=checkout,
                            timeout=5400,
                        )
                        results[project] = result.exit_code
                        output = result.stdout + result.stderr
                        test_output.append(project + ":\n" + output[-10000:])
                        (repo_artifact / f"tests-{attempt}.log").write_text(output)
                        git(["add", "-A"], cwd=checkout)
                        patch = git(
                            ["diff", "--binary", states[project]["base"]], cwd=checkout
                        ).stdout
                        (repo_artifact / "changes.patch").write_text(patch)
                    review = converse(
                        workspace,
                        "Independently review ALL task changes against each listed base commit. "
                        "Read each repository's guidance. Check compatibility between repositories. "
                        "Do not edit files. Identify actionable defects in the summary and set the verdict to PASS or CHANGES_REQUESTED.\n"
                        + context
                        + "\nTest exit codes: "
                        + json.dumps(results)
                        + "\nSpecification:\n"
                        + request,
                        title="Independent review",
                        response_model=ReviewResult,
                    )
                    report = review.summary + "\n\n" + review.verdict
                    (artifact / f"review-{attempt}.md").write_text(report)
                    (artifact / f"review-{attempt}.json").write_text(
                        review.model_dump_json(indent=2)
                    )
                    passed = (
                        all(code == 0 for code in results.values()) and review.verdict == "PASS"
                    )
                    if passed:
                        outcome["validation"] = "PASSED"
                        break
                    prompt = (
                        "Fix the failed tests and review findings within the approved specification.\n"
                        + report
                        + "\nTest exit codes: "
                        + json.dumps(results)
                        + "\nTest output:\n"
                        + "\n".join(test_output)[-30000:]
                    )
                if not passed:
                    raise RuntimeError(
                        "Tests or review require attention; branches and evidence retained"
                    )
        finally:
            errors = []
            for project, state in states.items():
                if "worktree" not in state:
                    continue
                checkout = Path(state["worktree"])
                try:
                    git(["add", "-A"], cwd=checkout)
                    if git(["diff", "--cached", "--quiet"], cwd=checkout, check=False).returncode:
                        git(
                            [
                                "-c",
                                "core.hooksPath=/dev/null",
                                "commit",
                                "-m",
                                "Factory task " + task,
                            ],
                            cwd=checkout,
                        )
                    state["commit"] = git(["rev-parse", "HEAD"], cwd=checkout).stdout.strip()
                    git(
                        [
                            "--git-dir",
                            state["repository"],
                            "fetch",
                            state["source"],
                            f"{state['branch']}:refs/heads/{state['branch']}",
                        ]
                    )
                except Exception as exc:
                    errors.append(f"{project}: {exc}")
            save()
            if errors:
                raise RuntimeError("Could not retain task branches: " + "; ".join(errors))
    outcome["status"] = "PASSED"
    try:
        # Validate the complete group before publishing any draft PRs.
        # GitHub has no atomic multi-repository publish; save each successful URL.
        for config in configs:
            state = states[config["project"]]
            should_publish = config["publish_draft"] if publish_draft is None else publish_draft
            if should_publish and config["repository"] and state["commit"] != state["base"]:
                repo_artifact = artifact / config["project"]
                state["pull_request"] = publish(
                    config,
                    task,
                    Path(state["repository"]),
                    state["branch"],
                    repo_artifact,
                    request,
                    credential,
                    issue,
                )
                save()
    except BaseException:
        outcome["status"] = "PUBLICATION_FAILED"
        save()
        raise
    save()
    print(json.dumps(outcome), flush=True)
    return outcome


if __name__ == "__main__":

    def cancelled(*_):
        raise InterruptedError("Cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    with LocalWorkspace(working_dir=os.getcwd()):
        job = json.loads(Path("job.json").read_text())
        # Existing single-repository automation bundles remain readable.
        configs = job.get("configs") or [job["config"]]
        bases = job.get("bases") or {configs[0]["project"]: job["base"]}
        build_group(
            configs,
            job["task"],
            Path("request.md").read_text(),
            bases,
            token() if any(c["repository"] for c in configs) else "",
            publish_draft=job.get("publish_draft"),
        )
