"""Native Git preferences must reach exported commits, including resumed work."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import common
import run
import test_transfer
import transfer


class GitIdentityTests(unittest.TestCase):
    def test_settings_apply_to_commits_and_refresh_on_the_next_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, state = test_transfer.TransferTests().seed(root)

            def execute(command, cwd, **kwargs):
                result = subprocess.run(
                    ["bash", "-c", command], cwd=cwd, capture_output=True, text=True
                )
                return SimpleNamespace(
                    exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr
                )

            for attempt, (name, email) in enumerate(
                [("kbak", "290936+kbak@users.noreply.github.com"), ("New Name", "new@example.test")]
            ):
                with self.subTest(attempt=attempt):
                    job = root / str(attempt)
                    job.mkdir()
                    settings = {
                        "misc_settings": {
                            "app_preferences": {"git_user_name": name, "git_user_email": email}
                        }
                    }
                    with patch.object(run, "api", return_value=settings) as api:
                        run.prepare_sources(job, {"example": state})
                    api.assert_called_once_with("GET", "/api/settings")
                    checkout = job / "worktree"
                    common.git(
                        ["worktree", "add", "-b", "openhands/test", str(checkout), "main"],
                        cwd=state["source"],
                    )
                    common.git(["branch", "-M", state["branch"]], cwd=checkout)
                    state["worktree"] = str(checkout)
                    state["commit_message"] = "Fix invitation validation retries"
                    (checkout / "code.txt").write_text(f"attempt {attempt}")
                    bundle = job / "export.bundle"
                    transfer.export_task(
                        SimpleNamespace(execute_command=execute), state, bundle, "task"
                    )
                    transfer.import_task(state, bundle)
                    metadata = common.git(
                        [
                            "--git-dir",
                            state["repository"],
                            "show",
                            "-s",
                            "--format=%an%n%ae%n%cn%n%ce%n%s",
                            state["commit"],
                        ]
                    ).stdout.splitlines()
                    self.assertEqual(
                        metadata, [name, email, name, email, "Fix invitation validation retries"]
                    )

    def test_incomplete_identity_fails_before_preparing_a_worker(self):
        for preferences in ({}, {"git_user_name": "kbak"}, {"git_user_email": "a@b.test"}):
            with (
                self.subTest(preferences=preferences),
                patch.object(
                    run, "api", return_value={"misc_settings": {"app_preferences": preferences}}
                ),
                patch.object(run, "clone_repos") as clone,
            ):
                with self.assertRaisesRegex(RuntimeError, "Application settings"):
                    run.prepare_sources(Path("/unused"), {})
                clone.assert_not_called()
