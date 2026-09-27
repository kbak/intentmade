"""Attach traceability instructions to OpenHands agents and run checks in their workspace."""

import shlex
from importlib.resources import files
from pathlib import PurePosixPath


def with_traceability(context=None, *, provisioned=False):
    """Attach before conversation/worktree creation, including continuation sessions.

    provisioned omits standalone installation guidance when the caller supplies
    the checker runtime. Development, authoring and semantics remain inline.
    """
    references = ["requirements.md"]
    if not provisioned:
        references.append("setup.md")
    return _with_skill(context, "intentbond", references)


def with_recovery(context=None):
    """Attach instructions for documenting existing requirements, links, and missing tests."""
    return _with_skill(context, "recover-baseline", ["recovery.md"])


def with_property_testing(context=None, *, framework=None):
    """Attach the portable property workflow and one optional framework guide.

    Normal with_traceability contexts already include the compact workflow.
    Use this entry point for a dedicated testing task or to supply a framework
    reference to a worker that cannot read the caller's package resources.
    """
    frameworks = {
        "hypothesis": "python",
        "fast-check": "typescript",
        "quickcheck": "haskell",
        "hegel": "hegel",
    }
    if framework is not None and framework not in frameworks:
        raise ValueError(f"Unknown property-testing framework: {framework}")
    references = [frameworks[framework] + ".md"] if framework else []
    return _with_skill(context, "property-testing", references)


def with_model_checking(context=None, *, language=None, backend="alloy"):
    """Attach native Alloy, Z3 SMT or CHC/Spacer guidance and an optional implementation guide."""
    if language not in {None, "python", "daml"}:
        raise ValueError(f"Unknown model-checking language: {language}")
    if backend not in {"alloy", "z3", "chc"}:
        raise ValueError(f"Unknown model-checking backend: {backend}")
    references = [{"alloy": "execution.md", "z3": "smt.md", "chc": "chc.md"}[backend]]
    if language:
        references.append(language + ".md")
    return _with_skill(context, "model-checking", references)


def _with_skill(context, name, references):
    from openhands.sdk import AgentContext
    from openhands.sdk.context import Skill

    directory = files("intentbond") / "skills" / name
    loaded = Skill.load(directory / "SKILL.md")
    # ACP receives prompt text, without a skill-relative file lookup. Carry the
    # reference too, so workers need not resolve a path from the caller's install.
    content = (
        loaded.content
        + "\n\nThe selected references are included below.\n"
        + "\n\n".join(
            f'<skill-reference path="references/{reference}">\n'
            + (directory / "references" / reference).read_text(encoding="utf-8")
            + "\n</skill-reference>"
            for reference in references
        )
        + '\n\n<skill-reference path="skills/intentbond/references/semantics.md">\n'
        + files("intentbond")
        .joinpath("skills/intentbond/references/semantics.md")
        .read_text(encoding="utf-8")
        + "\n</skill-reference>"
    )
    if name in {"intentbond", "recover-baseline"}:
        property_skill = Skill.load(files("intentbond") / "skills/property-testing/SKILL.md")
        content += (
            '\n\n<skill-reference path="skills/property-testing/SKILL.md">\n'
            + property_skill.content
            + "\n</skill-reference>"
        )
    skill = Skill(name=loaded.name, content=content, source=loaded.source)
    context = context or AgentContext(current_datetime=None)
    return context.model_copy(
        update={"skills": [s for s in context.skills if s.name != skill.name] + [skill]}
    )


def check(workspace, *, repo, scope, base, out, env=None, timeout=5600):
    """Run in the workspace. Exit 4 means automated checks passed, review pending.

    The caller provisions ib/OFT and supplies frozen scope outside candidate
    source. Preserve the output directory using the workspace's existing file
    transport. Completion additionally requires the caller's review lifecycle.

    repo, scope, and out must be absolute POSIX paths in the workspace's
    filesystem, including for remote workspaces. Relative paths raise ValueError.
    """
    for name, path in (("repo", repo), ("scope", scope), ("out", out)):
        if not PurePosixPath(str(path)).is_absolute():
            raise ValueError(f"{name} must be an absolute path in the workspace filesystem")
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
        str(out),
    ]
    prefix = ["env", *[f"{key}={value}" for key, value in (env or {}).items()]]
    return workspace.execute_command(shlex.join(prefix + command), cwd=str(repo), timeout=timeout)


def prepare_recovery(
    workspace,
    *,
    repo,
    out=None,
    candidate="HEAD",
    inputs=None,
    isolated=False,
    env=None,
    timeout=600,
):
    """Save original source and prepare records for a documentation proposal."""
    paths = {"repo": repo}
    if out is not None:
        paths["out"] = out
    return _recovery_command(
        workspace,
        "recover",
        paths,
        [
            *(["--isolated"] if isolated else []),
            "--candidate",
            candidate,
            *[arg for p in (inputs or []) for arg in ("--input", p)],
        ],
        cwd=repo,
        env=env,
        timeout=timeout,
    )


def check_recovery(
    workspace,
    *,
    recovery=None,
    repo=None,
    out=None,
    scope=None,
    preflight=False,
    env=None,
    timeout=5600,
):
    """Validate the draft. Exit 4 requires review; preflight exit 5 leaves tests unrun."""
    if (recovery is None) == (repo is None):
        raise ValueError("Supply either recovery or repo")
    paths = {"recovery": recovery} if recovery is not None else {"repo": repo}
    if out is not None:
        paths["out"] = out
    if scope is not None:
        paths["scope"] = scope
    return _recovery_command(
        workspace,
        "recover-check",
        paths,
        ["--preflight"] if preflight else [],
        cwd=recovery if recovery is not None else repo,
        env=env,
        timeout=timeout,
    )


def _recovery_command(workspace, action, paths, extra, *, cwd, env, timeout):
    for name, path in paths.items():
        if not PurePosixPath(str(path)).is_absolute():
            raise ValueError(f"{name} must be an absolute path in the workspace filesystem")
    command = ["python", "-m", "intentbond", action]
    for name, path in paths.items():
        command.extend([f"--{name}", str(path)])
    command.extend(str(value) for value in extra)
    prefix = ["env", *[f"{key}={value}" for key, value in (env or {}).items()]]
    return workspace.execute_command(shlex.join(prefix + command), cwd=str(cwd), timeout=timeout)
