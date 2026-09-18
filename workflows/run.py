"""Approved task → native worktree → Codex/tests/review → upstream draft PR."""

import json
import shlex
import shutil
import signal
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Literal

import browser_qa
import measurements
import reporting
import traceability
from agent import StructuredResponseError, converse, worktree
from cleanup import job_directory
from common import DATA, api, evidence, git, github, identifier, issues, job_id, lock, token
from naming import branch_name, change_title, pull_request_title
from openhands.sdk.workspace.repo import RepoSource, clone_repos
from pydantic import BaseModel, Field, model_validator
from reporting import NeedsInput, outcome, phase, run_report
from review import ReviewResult, review_code  # noqa: F401 (ReviewResult remains a public import)
from sandbox import worker
from transfer import export_task, import_task, worker_git


class ImplementationResult(BaseModel):
    status: Literal["IMPLEMENTED", "NEEDS_INPUT"]
    summary: str
    title: str | None = None
    questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_questions(self):
        if self.status == "NEEDS_INPUT" and not any(q.strip() for q in self.questions):
            raise ValueError("NEEDS_INPUT requires a concrete question")
        if self.status == "IMPLEMENTED" and self.questions:
            raise ValueError("Unanswered questions require NEEDS_INPUT")
        return self


def task_repository(config, task, base, credential, request=""):
    from urllib.parse import urlsplit

    repository = DATA / "tasks" / config["project"] / (identifier(task) + ".git")
    branch = branch_name(task, request or task, config.get("branch_prefix"))
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
            git(["--git-dir", str(repository), "config", "factory.branch", branch])
        except BaseException:
            shutil.rmtree(repository, ignore_errors=True)
            raise

    args = ["--git-dir", str(repository)]
    expected = (
        issues.normalize_repo(config["repository"]).casefold() if config["repository"] else ""
    )
    identity = expected or "fixture:" + config["project"]
    stored = git([*args, "config", "--get", "factory.repository"], check=False)
    if stored.returncode == 0 and stored.stdout.strip() != identity:
        raise RuntimeError("Retained task belongs to a different repository; use a new task ID")
    # Legacy stores have no identity metadata. Validate their actual fetch AND
    # push destinations before adopting them; pushurl can override origin.url.
    for direction in ([], ["--push"]):
        urls = git([*args, "remote", "get-url", *direction, "--all", "origin"]).stdout.splitlines()
        for url in urls:
            parsed = urlsplit(url)
            if expected:
                if url.startswith("git@github.com:"):
                    remote = url.partition(":")[2]
                elif parsed.scheme in {"https", "ssh"} and parsed.hostname == "github.com":
                    remote = parsed.path
                else:
                    raise RuntimeError(
                        "Retained task origin is not the configured GitHub repository"
                    )
                if issues.normalize_repo(remote).casefold() != expected:
                    raise RuntimeError("Retained task origin changed; use a new task ID")
            elif not (Path(url).is_absolute() or parsed.scheme == "file"):
                raise RuntimeError("A retained GitHub task cannot be reused as a fixture")
    stored_branch = git([*args, "config", "--get", "factory.baseBranch"], check=False)
    if stored_branch.returncode == 0:
        matches = stored_branch.stdout.strip() == config["branch"]
    else:
        # The original single-branch bare clone retains its base branch ref.
        matches = (
            git(
                [*args, "show-ref", "--verify", "--quiet", "refs/heads/" + config["branch"]],
                check=False,
            ).returncode
            == 0
        )
    if not matches:
        raise RuntimeError("Retained task uses a different base branch; use a new task ID")
    stored_task_branch = git([*args, "config", "--get", "factory.branch"], check=False)
    if stored_task_branch.returncode == 0:
        branch = stored_task_branch.stdout.strip()
    else:
        # Preserve published branches when naming defaults or issue titles change.
        branch = git([*args, "symbolic-ref", "--short", "HEAD"]).stdout.strip()
        if branch == config["branch"]:
            legacy = (config.get("branch_prefix") or "factory") + "/" + task
            branch = legacy
    git(["check-ref-format", "--branch", branch])
    git([*args, "show-ref", "--verify", "refs/heads/" + branch])
    git([*args, "config", "factory.branch", branch])
    git([*args, "config", "factory.repository", identity])
    git([*args, "config", "factory.baseBranch", config["branch"]])
    git(["--git-dir", str(repository), "symbolic-ref", "HEAD", "refs/heads/" + branch])
    return repository, branch


def publish(
    config,
    task,
    repo,
    branch,
    artifact,
    request,
    credential,
    issue=None,
    *,
    summary=None,
    title=None,
    browser_result=None,
):
    title = pull_request_title(title or request, config)
    body = (
        (summary or request.splitlines()[0]).strip()[:35000]
        + "\n\n### Validation\n\n- Tests passed.\n- Alibaba code and security review passed.\n"
        + browser_qa.limitations(browser_result or {})
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
    repair = config.get("repair_pr")
    if repair:
        from followup import verify_revision

        verify_revision(config, repair, credential)
    # Use upstream authenticated Git operations and idempotent draft PR creation.
    issues._push_branch(repo, branch, credential)
    result = (
        github(credential, "GET", f"/repos/{config['repository']}/pulls/{repair['number']}")
        if repair
        else issues._open_pull_request(
            credential, config["repository"], branch, config["branch"], title, body
        )
    )
    (artifact / "pull-request.json").write_text(json.dumps(result, indent=2))
    from followup import track

    track(config, task, repo, branch, result, request, issue)
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
            initial_review = not (DATA / "tasks" / project / (task + ".git")).exists()
            repository, branch = task_repository(config, task, bases[project], credential, request)
            states[project] = {
                "initial_review": initial_review,
                "branch": branch,
                "base": git(
                    ["--git-dir", str(repository), "config", "factory.base"]
                ).stdout.strip(),
                "repository": str(repository),
            }
            if config["repository"] and states[project]["base"] != bases[project]:
                git(
                    ["--git-dir", str(repository), "fetch", "--no-tags", "origin", bases[project]],
                    token=credential,
                )
                states[project].update(
                    retention_base=states[project]["base"],
                    base=bases[project],
                    merge_base=bases[project],
                )
        return execute_build(
            configs, task, request, credential, issue, publish_draft, artifact, states
        )


def git_identity():
    """Use the same application preferences as interactive Canvas workspaces."""
    preferences = api("GET", "/api/settings")["misc_settings"]["app_preferences"]
    name = (preferences.get("git_user_name") or "").strip()
    email = (preferences.get("git_user_email") or "").strip()
    if not name or not email:
        raise RuntimeError("Set your Git name and email in OpenHands Application settings")
    return name, email


def prepare_sources(root, states):
    """Clone trusted retained stores before any untrusted process starts."""
    name, email = git_identity()
    sources = []
    for state in states.values():
        for key in ("worktree", "source", "conversation"):
            state.pop(key, None)
        repository = Path(state["repository"])
        tip = git(["--git-dir", str(repository), "rev-parse", state["branch"]]).stdout.strip()
        sources.append(RepoSource(url=repository.as_uri(), ref=tip))
    cloned = clone_repos(sources, root / "source")
    if cloned.failed_repos:
        raise RuntimeError("OpenHands could not prepare all task repositories")
    for project, state in states.items():
        source = Path(cloned.repo_mappings[Path(state["repository"]).as_uri()].local_path)
        git(["checkout", "-B", "main", "HEAD"], cwd=source)
        if state.get("merge_base"):
            git(
                ["fetch", "--no-tags", Path(state["repository"]).as_uri(), state["merge_base"]],
                cwd=source,
            )
        git(["remote", "remove", "origin"], cwd=source)
        git(["config", "user.name", name], cwd=source)
        git(["config", "user.email", email], cwd=source)
        state["source"] = str(source)
    return root / "source"


def task_context(states, path_key="worktree"):
    return "\n".join(
        f"{project}: {state[path_key]}; branch {state['branch']}; review base {state['base']}"
        for project, state in states.items()
    )


def stage_test_evidence(configs, artifact, attempt, root):
    """Copy controller-retained logs into the next worker's visible workspace."""
    evidence = {}
    for config in configs:
        project = config["project"]
        source = artifact / project
        target = root / "test-evidence" / project
        logs = {}
        for name in (f"tests-{attempt}.log", f"traceability-check-{attempt}.log"):
            if (source / name).is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, target / name)
                logs[name] = str(target / name)
        metadata = source / f"test-command-{attempt}.json"
        command = (
            json.loads(metadata.read_text())
            if metadata.is_file()
            else {
                "command": config.get("test_command"),
            }
        )
        evidence[project] = {**command, "logs": logs}
    return (
        "\n\nController test evidence (read the complete logs when assessing failures):\n"
        + json.dumps(evidence)
        + "\nThese tests ran in the configured application environment. The agent host's "
        "Python environment is not the test environment; it need not have pytest installed.\n"
    )


def implementation_attempt(configs, states, task, prompt, artifact, attempt):
    results, test_output = {}, []
    with job_directory(DATA, artifact) as root:
        prepare_sources(root, states)
        if attempt:
            prompt += stage_test_evidence(configs, artifact, attempt - 1, root)
        trace_runs = traceability.prepare(configs, states, root, artifact, attempt)
        active = root / "active"
        active.mkdir()
        exports = {}
        try:
            with worker(root, configs) as workspace:
                try:
                    for project, state in states.items():
                        workspace.working_dir = state["source"]
                        state["conversation"] = (
                            worktree(workspace, traceability=True)
                            if project in trace_runs
                            else worktree(workspace)
                        )
                        state["worktree"] = workspace.working_dir
                        worker_git(workspace, ["branch", "-M", state["branch"]], state["worktree"])
                        if state.get("merge_base"):
                            merged = worker_git(
                                workspace,
                                [
                                    "-c",
                                    "core.hooksPath=/dev/null",
                                    "merge",
                                    "--no-edit",
                                    state["merge_base"],
                                ],
                                state["worktree"],
                                check=False,
                            )
                            if merged.exit_code not in (0, 1):
                                raise RuntimeError(
                                    "Could not merge the current base: " + merged.stderr[-2000:]
                                )
                            state["merge_output"] = (merged.stdout + merged.stderr)[-4000:]
                        (active / project).symlink_to(state["worktree"], target_is_directory=True)
                    workspace.working_dir = (
                        str(active) if len(states) > 1 else next(iter(states.values()))["worktree"]
                    )
                    conversation_id = (
                        None if len(states) > 1 else next(iter(states.values()))["conversation"]
                    )
                    environments = {}
                    for index, config in enumerate(configs):
                        project = config["project"]
                        checkout = states[project]["worktree"]
                        env = {
                            "PROJECT_DIR": checkout,
                            "FACTORY_WORKSPACE": str(active),
                            "COMPOSE_PROJECT_NAME": f"factory-tests-{index}",
                        }
                        if config.get("test_profile"):
                            env["FACTORY_TESTS"] = "/factory-tests/" + config["test_profile"]
                        if project in trace_runs:
                            env["TMPDIR"] = str(trace_runs[project]["scratch"])
                        environments[project] = env
                    implementation = converse(
                        workspace,
                        prompt
                        + "\n\nTask repositories:\n"
                        + task_context(states)
                        + traceability.instructions(trace_runs, states, environments)
                        + "\nBase-branch merge results (resolve any conflicts, preserving both sides' intended behavior):\n"
                        + "\n".join(s.get("merge_output", "") for s in states.values())
                        + "\nApply the factory-implementation skill to the approved specification.",
                        "agent-full-access",
                        "Implementation",
                        conversation_id,
                        response_model=ImplementationResult,
                        skill="factory-implementation",
                        transcript=artifact / f"implementation-{attempt}.jsonl",
                        traceability=bool(trace_runs),
                    )
                    (artifact / f"implementation-{attempt}.json").write_text(
                        implementation.model_dump_json(indent=2)
                    )
                    (artifact / f"implementation-{attempt}.md").write_text(implementation.summary)
                    for state in states.values():
                        state["summary"] = implementation.summary
                        if implementation.title:
                            state["title"] = change_title(implementation.title)
                            state["commit_message"] = state["title"]
                    if implementation.status == "NEEDS_INPUT":
                        raise NeedsInput(
                            implementation.summary
                            + "\n\n"
                            + "\n".join(f"- {q}" for q in implementation.questions)
                        )
                    for config in configs:
                        project = config["project"]
                        phase(f"{task}: testing {project} (attempt {attempt + 1})")
                        checkout = states[project]["worktree"]
                        repo_artifact = artifact / project
                        repo_artifact.mkdir(exist_ok=True)
                        env = environments[project]
                        command = " ".join(
                            key + "=" + shlex.quote(value) for key, value in env.items()
                        )
                        test_command = config.get("test_command")
                        if project in trace_runs:
                            test_command = config["traceability_scope"]["tests"]["command"]
                        (repo_artifact / f"test-command-{attempt}.json").write_text(
                            json.dumps(
                                {"command": test_command, "cwd": checkout, "environment": env},
                                indent=2,
                            )
                        )
                        with measurements.stage("tests", project):
                            if project in trace_runs:
                                result = traceability.check(
                                    workspace, states[project], trace_runs[project], env
                                )
                            else:
                                result = workspace.execute_command(
                                    command + " bash -c " + shlex.quote(config["test_command"]),
                                    cwd=checkout,
                                    timeout=5400,
                                )
                        results[project] = result.exit_code
                        output = result.stdout + result.stderr
                        test_output.append(project + ":\n" + output[-10000:])
                        log_name = (
                            f"traceability-check-{attempt}.log"
                            if project in trace_runs
                            else f"tests-{attempt}.log"
                        )
                        (repo_artifact / log_name).write_text(output)
                finally:
                    errors = []
                    for project, state in states.items():
                        if "worktree" not in state:
                            continue
                        try:
                            bundle = root / (project + ".bundle")
                            export_task(workspace, state, bundle, task)
                            exports[project] = bundle
                        except Exception as exc:
                            errors.append(f"{project}: {exc}")
                    if errors:
                        raise RuntimeError("Could not export task branches: " + "; ".join(errors))
        finally:
            # This runs after DockerWorkspace teardown. Only bundle bytes cross
            # back; no parent Git command ever opens worker-controlled metadata.
            retention_errors = []
            for project, bundle in exports.items():
                try:
                    import_task(states[project], bundle)
                    repo_artifact = artifact / project
                    repo_artifact.mkdir(exist_ok=True)
                    state = states[project]
                    patch = git(
                        [
                            "--git-dir",
                            state["repository"],
                            "diff",
                            "--no-ext-diff",
                            "--no-textconv",
                            "--binary",
                            state["base"],
                            state["commit"],
                        ]
                    ).stdout
                    (repo_artifact / "changes.patch").write_text(patch)
                except Exception as exc:
                    retention_errors.append(f"{project}: {exc}")
            # Preserve every check, including feedback before a malformed response.
            # A retention error escapes job_directory so its only copy survives.
            from traceability.retention import retain

            for project, paths in trace_runs.items():
                try:
                    retain(paths, attempt)
                except Exception as exc:
                    retention_errors.append(f"{project} check retention: {exc}")
            if retention_errors:
                raise RuntimeError("Could not retain task evidence: " + "; ".join(retention_errors))
            for project, paths in trace_runs.items():
                results[project] = traceability.collect(
                    states[project], paths, results.get(project)
                )
                details = states[project]["traceability"]
                log = paths["retained"] / "tests.log"
                if log.is_file():
                    shutil.copyfile(log, artifact / project / f"tests-{attempt}.log")
                    if results[project] != 0:
                        output = log.read_text(errors="replace")
                        test_output.append(project + ":\n" + output[-10000:])
                # Keep human-report changes and the scope in retained records.
                # Review receives its scope separately; repairs already have the
                # approved task, frozen scope and complete staged test logs.
                feedback = {
                    key: details[key]
                    for key in (
                        "status",
                        "check_exit_code",
                        "diagnostics",
                        "review",
                        "candidate",
                        "matched_commit",
                    )
                    if key in details
                }
                test_output.append(project + " traceability:\n" + json.dumps(feedback))
    return results, test_output


def review_changes(configs, states, request, results, transcript=None, *, artifact=None, attempt=0):
    # Review a fresh clone of the retained commits after the implementation
    # worker has exited. Its processes and mutable Git configuration are absent.
    with job_directory(DATA) as root:
        review_states = {project: dict(state) for project, state in states.items()}
        prepare_sources(root, review_states)
        if artifact is not None:
            request += stage_test_evidence(configs, artifact, attempt, root)
        trace_review = traceability.prepare_review(configs, review_states, root)
        active = root / "active"
        active.mkdir()
        for project, state in review_states.items():
            (active / project).symlink_to(state["source"], target_is_directory=True)
        with worker(root, configs) as workspace:
            workspace.working_dir = (
                str(active) if len(states) > 1 else next(iter(review_states.values()))["source"]
            )
            return review_code(
                workspace,
                "Review ALL task changes against each listed base commit. "
                "Check compatibility between repositories.\n"
                + task_context(review_states, "source")
                + "\nTest exit codes: "
                + json.dumps(results)
                + "\nSpecification:\n"
                + request
                + traceability.review_context(trace_review),
                title="Independent review",
                transcript=transcript,
                initial_review=(
                    attempt == 0
                    and bool(states)
                    and all(state.get("initial_review") is True for state in states.values())
                    and not any(config.get("repair_pr") for config in configs)
                ),
                sources={
                    project: {
                        "source": state["source"],
                        "base": state["base"],
                        "candidate": state["commit"],
                    }
                    for project, state in review_states.items()
                },
                input_path=root / "ocr-review.json",
                **({"traceability": trace_review} if trace_review else {}),
            )


def browser_checks(configs, states, request, artifact, attempt):
    selected = [
        c for c in configs if c.get("browser_qa") and browser_qa.selected(c, states[c["project"]])
    ]
    if not selected:
        return {}
    results = {}
    with job_directory(DATA, artifact) as root:
        qa_states = {project: dict(state) for project, state in states.items()}
        prepare_sources(root, qa_states)
        for config in selected:
            project = config["project"]
            output = root / "captures" / project
            output.mkdir(parents=True)
            # One fresh app/database/browser for each repository's QA session.
            with worker(root, configs) as workspace:
                phase(f"Browser acceptance QA: {project}")
                results[project] = browser_qa.run(
                    workspace,
                    config,
                    qa_states[project],
                    request,
                    output,
                    artifact / project / f"browser-{attempt}",
                )
    reporting.browser_evidence(results)
    return results


def execute_build(configs, task, request, credential, issue, publish_draft, artifact, states):
    local_report = None
    if reporting.ACTIVE and not reporting.ACTIVE.get("conversation_id"):
        local_report = reporting.TaskReport(configs[0], task)
        local_report.update("RUNNING", "Implementing and validating the approved task.")
    try:
        with measurements.task(
            artifact,
            task,
            "maintenance"
            if any(c.get("repair_pr") for c in configs)
            else "issue"
            if issue
            else "feature",
            {c["project"]: c.get("repository", "") for c in configs if "project" in c},
        ):
            result = _execute_build(
                configs, task, request, credential, issue, publish_draft, artifact, states
            )
    except BaseException as exc:
        if local_report:
            saved = json.loads((artifact / "result.json").read_text())
            local_report.update(saved["status"], str(exc), result=saved)
        raise
    if local_report:
        local_report.update(
            "PASSED",
            "\n".join(s["pull_request"] for s in states.values() if s.get("pull_request"))
            or "Validated; evidence retained.",
            result=result,
        )
    return result


def _execute_build(configs, task, request, credential, issue, publish_draft, artifact, states):
    for state in states.values():
        state["commit_message"] = change_title(request)
    outcome = {
        "task": task,
        "status": "RUNNING",
        "repositories": states,
        "metrics": str(artifact / "metrics.json"),
    }

    def save():
        (artifact / "result.json").write_text(json.dumps(outcome, indent=2))
        measurements.update(outcome)

    prompt = request
    repair_reason = "pr_maintenance" if any(c.get("repair_pr") for c in configs) else "initial"
    try:
        for attempt in range(min(c["repair_attempts"] for c in configs) + 1):
            measurements.begin_attempt(attempt, repair_reason)
            outcome.update(
                phase="IMPLEMENTING" if attempt == 0 else "REPAIRING",
                attempt=attempt,
                browser_qa={},
                tests={},
            )
            save()
            phase(f"{task}: {outcome['phase'].lower()} (attempt {attempt + 1})")
            with measurements.stage("implementation_and_tests"):
                results, test_output = implementation_attempt(
                    configs, states, task, prompt, artifact, attempt
                )
            outcome["tests"] = results
            if any(code != 0 for code in results.values()):
                outcome["phase"] = "TESTS_FAILED"
                save()
                measurements.end_attempt("TESTS_FAILED")
                repair_reason = (
                    "traceability"
                    if any(
                        state.get("traceability", {}).get("status")
                        not in {None, "passed", "review_required"}
                        for state in states.values()
                    )
                    else "tests"
                )
                details = (
                    "\nTest exit codes: "
                    + json.dumps(results)
                    + "\nTest output:\n"
                    + "\n".join(test_output)[-30000:]
                )
                if attempt == min(c["repair_attempts"] for c in configs):
                    raise RuntimeError(
                        "Configured tests failed; branches and full test logs retained." + details
                    )
                prompt = (
                    request
                    + "\n\nThe controller's configured tests failed. Diagnose the retained command "
                    "and full logs, then fix the cause within this specification. Do not waive "
                    "failed tests or treat earlier agent-run checks as the controller result."
                    + details
                )
                continue
            with measurements.stage("browser_qa"):
                outcome["browser_qa"] = (
                    browser_checks(configs, states, request, artifact, attempt)
                    if all(code == 0 for code in results.values())
                    and traceability.checks_passed(configs, states)
                    else {}
                )
            blocked = [
                r["summary"] for r in outcome["browser_qa"].values() if r["status"] == "BLOCKED"
            ]
            if blocked:
                raise NeedsInput("Browser verification could not complete: " + "\n".join(blocked))
            outcome.update(phase="REVIEWING", tests=results)
            save()
            phase(f"{task}: independent review (attempt {attempt + 1})")
            with measurements.stage("review"):
                review = review_changes(
                    configs,
                    states,
                    request
                    + "\nController test output:\n"
                    + "\n".join(test_output)[-30000:]
                    + (
                        "\nBrowser QA evidence:\n" + json.dumps(outcome["browser_qa"])
                        if outcome["browser_qa"]
                        else ""
                    ),
                    results,
                    artifact / f"review-{attempt}.jsonl",
                    artifact=artifact,
                    attempt=attempt,
                )
            report = review.report()
            (artifact / f"review-{attempt}.md").write_text(report)
            (artifact / f"review-{attempt}.json").write_text(review.model_dump_json(indent=2))
            measurements.review(review, artifact / f"review-{attempt}.json")
            traceability.record_review(configs, states, review, artifact / f"review-{attempt}.json")
            if review.verdict == "BLOCKED":
                raise RuntimeError("Independent review infrastructure blocked: " + review.summary)
            browser_passed = all(browser_qa.passed(r) for r in outcome["browser_qa"].values())
            if (
                all(code == 0 for code in results.values())
                and review.verdict == "PASS"
                and browser_passed
                and traceability.checks_passed(configs, states)
            ):
                outcome["validation"] = (
                    "PASSED_WITH_GAPS"
                    if any(r["status"] == "ACCEPTED_GAPS" for r in outcome["browser_qa"].values())
                    else "PASSED"
                )
                save()
                measurements.end_attempt(outcome["validation"])
                break
            repair_reason = "review" if browser_passed else "browser_qa"
            save()
            measurements.end_attempt("CHANGES_REQUESTED")
            prompt = (
                request
                + "\n\nFix the failed tests and blocking review findings within this specification. "
                "Non-blocking findings do not require changes.\n"
                + review.repair_instructions()
                + "\nTest exit codes: "
                + json.dumps(results)
                + "\nTest output:\n"
                + "\n".join(test_output)[-30000:]
                + "\nBrowser QA failures (fix only observed in-scope defects):\n"
                + json.dumps(outcome["browser_qa"])
            )
        else:
            raise RuntimeError(
                "Tests, traceability, browser QA or review require attention; branches and evidence retained.\n\n"
                + "Test exit codes: "
                + json.dumps(results)
                + "\n\n"
                + review.summary
                + (
                    "\nTraceability:\n"
                    + json.dumps(
                        {
                            project: state["traceability"].get("diagnostics", [])
                            for project, state in states.items()
                            if "traceability" in state
                        }
                    )
                    if any("traceability" in state for state in states.values())
                    else ""
                )
            )
    except BaseException as exc:
        outcome.update(
            status="NEEDS_INPUT" if isinstance(exc, NeedsInput) else "FAILED",
            error=f"{type(exc).__name__}: {exc}",
        )
        if isinstance(exc, StructuredResponseError):
            outcome["response_failure"] = exc.details
        raise
    finally:
        save()
        for state in states.values():
            if state.get("merge_base") and state.get("commit"):
                args = ["--git-dir", state["repository"]]
                if (
                    git(
                        [*args, "merge-base", "--is-ancestor", state["base"], state["commit"]],
                        check=False,
                    ).returncode
                    == 0
                ):
                    git([*args, "config", "factory.base", state["base"]])
    outcome["status"] = "PASSED"
    try:
        outcome["phase"] = "PUBLISHING"
        save()
        phase(f"{task}: publishing validated draft PR")
        # Validate the complete group before publishing any draft PRs.
        # GitHub has no atomic multi-repository publish; save each successful URL.
        for config in configs:
            state = states[config["project"]]
            should_publish = config["publish_draft"] if publish_draft is None else publish_draft
            if should_publish and config["repository"] and state["commit"] != state["base"]:
                repo_artifact = artifact / config["project"]
                with measurements.stage("publication", config["project"]):
                    state["pull_request"] = publish(
                        config,
                        task,
                        Path(state["repository"]),
                        state["branch"],
                        repo_artifact,
                        request,
                        credential,
                        issue,
                        summary=state.get("summary"),
                        title=state.get("title"),
                        browser_result=outcome["browser_qa"].get(config["project"]),
                    )
                save()
    except BaseException as exc:
        outcome["status"] = "PUBLICATION_FAILED"
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        save()
        raise
    outcome["phase"] = "DONE"
    save()
    print(json.dumps(outcome), flush=True)
    return outcome


if __name__ == "__main__":

    def cancelled(*_):
        raise InterruptedError("Cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    with run_report():
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
        outcome("COMPLETED", "Task validated; inspect its result for draft PR URLs")
