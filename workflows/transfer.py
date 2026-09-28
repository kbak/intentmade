"""Transfer task commits without running parent Git on worker-owned metadata."""

import os
import shlex
import stat
import tempfile
from pathlib import Path

from common import git
from resource_limits import MIB, copy_bounded, require_disk_space, settings


def worker_git(workspace, args, cwd, check=True):
    result = workspace.execute_command(shlex.join(["git", *args]), cwd=str(cwd), timeout=300)
    if check and result.exit_code:
        raise RuntimeError(f"Worker Git failed: {result.stderr[-1000:]}")
    return result


def export_task(workspace, state, destination, task):
    """All commands consuming mutable Git configuration execute in the worker."""
    checkout = state["worktree"]
    conflicts = worker_git(workspace, ["ls-files", "--unmerged"], checkout)
    if conflicts.stdout.strip():
        raise RuntimeError("Unresolved merge conflicts; retain the workspace for recovery")
    worker_git(workspace, ["add", "-A"], checkout)
    changed = worker_git(workspace, ["diff", "--cached", "--quiet"], checkout, check=False)
    if changed.exit_code not in (0, 1):
        raise RuntimeError("Could not inspect worker changes")
    if changed.exit_code:
        worker_git(
            workspace,
            [
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-m",
                state.get("commit_message") or "Implement " + task,
            ],
            checkout,
        )
    branch = "refs/heads/" + state["branch"]
    head = worker_git(workspace, ["rev-parse", "HEAD"], checkout).stdout.strip()
    # Repository guidance can rename the branch. Export the current work under
    # the task ref; the parent still enforces a fast-forward of retained history.
    worker_git(
        workspace,
        ["merge-base", "--is-ancestor", state.get("retention_base", state["base"]), head],
        checkout,
    )
    worker_git(workspace, ["update-ref", branch, head], checkout)
    worker_git(workspace, ["bundle", "create", str(destination), branch], checkout)


def import_task(state, bundle):
    """Copy a regular bundle after worker teardown; never import its Git config."""
    # A worker can replace its export with a symlink, FIFO or device. Open without
    # following links or blocking, and consume only a regular file.
    require_disk_space(tempfile.gettempdir())
    fd = os.open(bundle, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source, tempfile.TemporaryDirectory() as temp:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise RuntimeError("Worker export is not a regular bundle")
        trusted_bundle = Path(temp) / "task.bundle"
        with trusted_bundle.open("wb") as target:
            copy_bounded(
                source,
                target,
                settings()["max_bundle_mb"] * MIB,
                "Worker bundle",
            )
        branch = "refs/heads/" + state["branch"]
        heads = git(["bundle", "list-heads", str(trusted_bundle)]).stdout.splitlines()
        if len(heads) != 1 or heads[0].split()[1:] != [branch]:
            raise RuntimeError("Worker bundle contains unexpected refs")
        # Fetch only the expected branch, without force. Git validates object
        # integrity and rejects a rewritten/unrelated retained task history.
        git(
            [
                "-c",
                "fetch.fsckObjects=true",
                "-c",
                "transfer.fsckObjects=true",
                "--git-dir",
                state["repository"],
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                str(trusted_bundle),
                f"{branch}:{branch}",
            ]
        )
    state["commit"] = git(["--git-dir", state["repository"], "rev-parse", branch]).stdout.strip()
