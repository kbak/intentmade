import shlex
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock

from traceability.openhands import check


class CheckTests(unittest.TestCase):
    def test_relative_paths_are_rejected_before_dispatch(self):
        for name in ("repo", "scope", "out"):
            with self.subTest(path=name):
                workspace = Mock()
                paths = {
                    "repo": "/workspaces/project",
                    "scope": "/task-inputs/scope.json",
                    "out": "/task-output/check-1",
                }
                paths[name] = "project"
                with self.assertRaisesRegex(ValueError, f"{name} must be an absolute"):
                    check(workspace, **paths, base="approved-commit")
                workspace.execute_command.assert_not_called()

    def test_absolute_workspace_paths_and_failed_outcome_are_preserved(self):
        workspace = Mock()
        expected = SimpleNamespace(exit_code=2, stdout="", stderr="Check could not run")
        workspace.execute_command.return_value = expected
        repo = PurePosixPath("/remote workspaces/project")
        scope = "/remote task's inputs/scope.json"
        out = Path("/remote task-output/check-1")
        result = check(
            workspace,
            repo=repo,
            scope=scope,
            base="approved-commit",
            out=out,
            env={"INTENTBOND_OFT_JAR": "/remote tools/oft.jar"},
            timeout=90,
        )
        self.assertIs(result, expected)
        workspace.execute_command.assert_called_once()
        args, kwargs = workspace.execute_command.call_args
        self.assertEqual(kwargs, {"cwd": str(repo), "timeout": 90})
        self.assertEqual(
            shlex.split(args[0]),
            [
                "env",
                "INTENTBOND_OFT_JAR=/remote tools/oft.jar",
                "python",
                "-m",
                "intentbond",
                "check",
                "--repo",
                str(repo),
                "--scope",
                scope,
                "--base",
                "approved-commit",
                "--candidate",
                "worktree",
                "--out",
                str(out),
            ],
        )


if __name__ == "__main__":
    unittest.main()
