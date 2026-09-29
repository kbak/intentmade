import json
import shlex
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

import traceability
from traceability.openhands import check


class CheckTests(unittest.TestCase):
    def test_controller_allocates_new_output_for_each_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope = root / "scope.json"
            scope.write_text("{}")
            paths = {
                "root": root,
                "scratch": root / "scratch",
                "worker_scope": scope,
                "timeout": 60,
            }
            state = {"worktree": str(root / "source"), "base": "approved-commit"}
            outputs = []

            def checker(workspace, **kwargs):
                output = kwargs["out"]
                self.assertFalse(output.exists())
                output.mkdir()
                (output / "evidence.json").write_text("retained fixture")
                outputs.append(output)
                return SimpleNamespace(exit_code=0, stdout="", stderr="")

            with (
                patch("provenance.remote_boundary", return_value={}),
                patch("traceability.openhands.check", side_effect=checker),
            ):
                for _ in range(2):
                    self.assertEqual(traceability.check(Mock(), state, paths, {}).exit_code, 0)
            self.assertNotEqual(outputs[0], outputs[1])
            self.assertEqual(paths["out"], outputs[1])
            for output in outputs:
                self.assertEqual((output / "evidence.json").read_text(), "retained fixture")
                record = json.loads((output.parent / "invocation.json").read_text())
                self.assertEqual(record["exit_code"], 0)
                self.assertTrue(record["completed_at"])

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
