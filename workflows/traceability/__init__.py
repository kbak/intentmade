"""IntentMade policy and lifecycle bindings for the portable OFT workflow.

Imports of the optional packages are lazy so repositories without opt-in keep
their existing runtime, context, test commands, and completion gates.
"""

import json
import shlex
import shutil
import tempfile
from pathlib import Path


def scope_for(config):
    if "traceability_scope" not in config:
        return None
    from versioned_traceability.config import validate_scope

    return validate_scope(config["traceability_scope"])


def discussion_context(configs):
    """Supply validated scopes and requirements guidance, without execution steps."""
    result = {"repositories": {}}
    for config in configs:
        repository = {"catalog": "/projects/repos/" + config["project"]}
        scope = scope_for(config)
        if scope is not None:
            repository["traceability_scope"] = scope
        result["repositories"][config["project"]] = repository
    if any("traceability_scope" in repo for repo in result["repositories"].values()):
        from importlib.resources import files

        directory = files("versioned_traceability") / "skills/versioned-traceability/references"
        result["requirements_guidance"] = "\n\n".join(
            (directory / name).read_text(encoding="utf-8")
            for name in ("requirements.md", "semantics.md")
        )
    return result


def prepare(configs, states, root, artifact, attempt):
    """Freeze controller-selected policy before entering the implementation worker."""
    selected = {}
    for config in configs:
        project = config["project"]
        states[project].pop("traceability", None)
        scope = scope_for(config)
        if scope is None:
            continue
        output = artifact / project
        output.mkdir(exist_ok=True)
        trusted = output / "traceability-scope.json"
        trusted.write_text(json.dumps(scope, indent=2) + "\n")
        worker_root = root / "traceability" / project
        worker_root.mkdir(parents=True)
        worker_scope = worker_root / "scope.json"
        worker_scope.write_bytes(trusted.read_bytes())
        # Test snapshots must be visible to the job's Docker daemon via /workspaces.
        scratch = worker_root / "scratch"
        scratch.mkdir()
        selected[project] = {
            "root": root.resolve(),
            "scope": trusted,
            "worker_scope": worker_scope,
            "retained": output / f"traceability-{attempt}",
            "scratch": scratch,
            "timeout": scope["tests"]["timeout_seconds"] + 600,
        }
        states[project]["traceability"] = {"status": "not_run"}
    return selected


def instructions(selected, states, environments):
    if not selected:
        return ""
    sections = [
        "\n\nFor the traceability repositories listed below, persist or update the agreed "
        "Markdown requirements from the approved specification in its intended repository "
        "documentation paths, within the frozen scope. Preserve their IDs and acceptance "
        "criteria. Unapproved proposals and unresolved questions are not implementation scope. "
        "If the handoff conflicts with the frozen scope or existing promises without "
        "authorizing that change, use the existing NEEDS_INPUT process.\n\n"
        "Traceability checks: run the following command for feedback after edits. "
        "Each run creates fresh evidence outside the repository. The controller repeats "
        "the check before completion."
    ]
    for project, paths in selected.items():
        state = states[project]
        command = shlex.join(
            ["env"]
            + [f"{key}={value}" for key, value in environments[project].items()]
            + [
                "python",
                "-m",
                "versioned_traceability",
                "check",
                "--repo",
                state["worktree"],
                "--scope",
                str(paths["worker_scope"]),
                "--base",
                state["base"],
                "--candidate",
                "worktree",
            ]
        )
        temporary = shlex.quote(str(paths["scratch"].parent / "agent-check-XXXXXX"))
        sections.append(
            f"{project}:\n```sh\n"
            f"VT_FEEDBACK=$(mktemp -d {temporary}) &&\n"
            f'{command} --out "$VT_FEEDBACK/evidence"\n```'
        )
    return "\n\n".join(sections)


def check(workspace, state, paths, env):
    from openhands_traceability import check as portable_check

    # Allocate only when the controller invokes the check, after implementation.
    # The checker itself requires a nonexistent output directory. Agent feedback
    # and previous invocations must never supply this invocation's bundle.
    invocation = Path(tempfile.mkdtemp(prefix="controller-check-", dir=paths["scratch"].parent))
    paths["out"] = invocation / "evidence"
    return portable_check(
        workspace,
        repo=state["worktree"],
        scope=paths["worker_scope"],
        base=state["base"],
        out=paths["out"],
        env=env,
        timeout=paths["timeout"],
    )


def collect(state, paths, check_exit_code):
    """Retain after worker teardown, then match against a fresh trusted Git clone."""
    from common import git
    from versioned_traceability.common import read_json
    from versioned_traceability.evidence import EXIT_CODES, read_statement
    from versioned_traceability.runner import verify

    target = paths["retained"]
    target.mkdir()
    record = {
        "status": "error",
        "evidence": str(target / "evidence.json"),
        "check_exit_code": check_exit_code,
    }
    state["traceability"] = record
    try:
        source = paths.get("out")
        if (
            source is None
            or source.is_symlink()
            or not source.is_dir()
            or not source.resolve().is_relative_to(paths["root"])
        ):
            raise RuntimeError("Portable evidence directory is missing or replaced")
        # Portable bundles are flat regular files. No worker symlinks or special
        # files are followed into the controller's artifact storage.
        for path in source.iterdir():
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Portable evidence contains a non-regular artifact")
            shutil.copyfile(path, target / path.name)
        evidence = read_statement(read_json(target / "evidence.json"))
        record.update(
            status=evidence["status"],
            diagnostics=evidence["diagnostics"],
            review=evidence.get("review"),
            candidate=evidence.get("candidate"),
            scope=read_json(paths["scope"]),
            changes=read_json(target / "review.json")["changes"]
            if (target / "review.json").is_file()
            else [],
        )
        expected_exit = EXIT_CODES.get(evidence["status"])
        if check_exit_code is None or check_exit_code != expected_exit:
            raise RuntimeError(
                f"Current checker exit {check_exit_code} does not support retained "
                f"bundle status {evidence['status']}"
            )
        if evidence["status"] in {"passed", "review_required"}:
            with tempfile.TemporaryDirectory(prefix="factory-traceability-") as directory:
                repo = Path(directory) / "source"
                git(["clone", "--no-checkout", state["repository"], str(repo)])
                verify(
                    repo,
                    paths["scope"],
                    state["base"],
                    state["commit"],
                    target / "evidence.json",
                    allow_pending_review=True,
                )
            record["matched_commit"] = state["commit"]
            return 0
        return check_exit_code
    except Exception as exc:
        record.update(status="error", diagnostics=[str(exc)])
        (target / "collection-error.txt").write_text(str(exc) + "\n")
        return check_exit_code if check_exit_code not in (None, 0, 4) else 2


def checks_passed(configs, states):
    for config in configs:
        if "traceability_scope" not in config:
            continue
        state = states.get(config["project"], {})
        result = state.get("traceability", {})
        if (
            result.get("status") not in {"passed", "review_required"}
            or result.get("check_exit_code") not in {0, 4}
            or not result.get("matched_commit")
            or result["matched_commit"] != state.get("commit")
        ):
            return False
    return True


def review_scope(config, changed_paths):
    scope = scope_for(config)
    if scope is None:
        return None
    from versioned_traceability.common import within

    return {
        "scope": scope,
        "changed_paths": sorted({path for path in changed_paths if within(path, scope["inputs"])}),
    }


def requirement_index(source, scope, candidate, base=None, *, archive=False):
    """Resolve review citations through OFT before an agent sees the source."""
    from versioned_traceability.common import CheckError, within
    from versioned_traceability.oft import default_jar, export_items, validate_jar
    from versioned_traceability.snapshot import archive_snapshot, snapshot

    versions = {"candidate": candidate}
    if base is not None:
        versions["base"] = base
    indexed = {}
    with tempfile.TemporaryDirectory(prefix="factory-review-ids-") as directory:
        root = Path(directory)
        for label, commit in versions.items():
            try:
                jar = default_jar().resolve()
                validate_jar(jar)
                capture = archive_snapshot if archive else snapshot
                snap = capture(Path(source), commit, root / label)
                items, _, _ = export_items(snap, scope["inputs"], jar, "java", root, label)
                ids = [
                    item["id"]
                    for item in items
                    if within(item["path"], scope["specification_paths"])
                ]
                if len(ids) != len(set(ids)):
                    raise CheckError("Duplicate specification IDs in review snapshot")
                indexed[label] = {"source": snap.identity(), "ids": sorted(ids)}
            except (CheckError, OSError, ValueError) as exc:
                indexed[label] = {"error": str(exc)}
    return indexed


def prepare_review(configs, states, root):
    """Make the checked scope, diff inventory and retained evidence accessible to review."""
    from common import git

    selected = {}
    for config in configs:
        if "traceability_scope" not in config:
            continue
        project = config["project"]
        state = states[project]
        changed = git(
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-only",
                "-z",
                state["base"],
                state["commit"],
                "--",
            ],
            cwd=state["source"],
        ).stdout.split("\0")
        context = review_scope(config, [path for path in changed if path])
        context.update(source=state["source"], base=state["base"], candidate=state["commit"])
        context["requirement_index"] = requirement_index(
            state["source"], context["scope"], state["commit"], state["base"]
        )
        record = state.get("traceability", {})
        context["check"] = {
            key: record.get(key) for key in ("status", "check_exit_code", "diagnostics")
        }
        context["evidence_directory"] = None
        if record.get("evidence"):
            source = Path(record["evidence"]).parent
            if source.is_dir():
                target = root / "traceability" / project / "evidence"
                shutil.copytree(source, target)
                context["evidence_directory"] = str(target)
        selected[project] = context
    return selected


def review_context(selected):
    if not selected:
        return ""
    from importlib.resources import files

    semantics = (
        files("versioned_traceability")
        .joinpath("skills/versioned-traceability/references/semantics.md")
        .read_text(encoding="utf-8")
    )
    # Keep full ID inventories in controller validation and saved review records,
    # rather than spending agent context on every requirement in each snapshot.
    visible = {
        project: {
            **{key: value for key, value in context.items() if key != "requirement_index"},
            "reference_snapshots": {
                label: {key: value for key, value in entry.items() if key != "ids"}
                for label, entry in context.get("requirement_index", {}).items()
            },
        }
        for project, context in selected.items()
    }
    return (
        "\n\nRequired Alibaba Reviewer traceability assessment (only these repositories and changed paths):\n"
        + json.dumps(visible)
        + "\nApply the factory-review skill's traceability assessment to this context. "
        "Unauthorized promise weakening is blocking even when checks pass; task-authorized "
        "specification/test edits need no extra approval. "
        "Evidence directories listed above are accessible in this workspace. Null means no "
        "retained checker bundle is available; do not claim to have read one."
        "\n\nThe shared semantic contract is included below. Apply it when interpreting "
        "traceability artifacts and reporting conclusions.\n\n" + semantics
    )


def record_review(configs, states, review, report_path):
    for config in configs:
        if "traceability_scope" in config:
            states[config["project"]].setdefault("traceability", {})["independent_review"] = {
                "verdict": review.verdict,
                "report": str(report_path),
                "commit": states[config["project"]].get("commit"),
            }
