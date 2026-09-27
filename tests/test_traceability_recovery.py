import shlex
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from traceability.openhands import check_recovery, prepare_recovery


class RecoveryAdapterTests(unittest.TestCase):
    def test_prepare_forwards_pinned_source_scope_and_quoted_workspace_paths(self):
        workspace = Mock()
        expected = SimpleNamespace(exit_code=0, stdout="prepared", stderr="")
        workspace.execute_command.return_value = expected
        result = prepare_recovery(
            workspace,
            repo="/source repo",
            out="/task's recovery",
            candidate="fixed-commit",
            isolated=True,
            inputs=["README.md", "src/module name"],
            env={"INTENTBOND_OFT_JAR": "/tools/oft.jar"},
            timeout=90,
        )
        self.assertIs(result, expected)
        args, kwargs = workspace.execute_command.call_args
        self.assertEqual(kwargs, {"cwd": "/source repo", "timeout": 90})
        self.assertEqual(
            shlex.split(args[0]),
            [
                "env",
                "INTENTBOND_OFT_JAR=/tools/oft.jar",
                "python",
                "-m",
                "intentbond",
                "recover",
                "--repo",
                "/source repo",
                "--out",
                "/task's recovery",
                "--isolated",
                "--candidate",
                "fixed-commit",
                "--input",
                "README.md",
                "--input",
                "src/module name",
            ],
        )

    def test_check_preserves_pending_review_and_errors(self):
        for code in (1, 2, 3, 4, 5):
            with self.subTest(exit_code=code):
                workspace = Mock()
                expected = SimpleNamespace(exit_code=code, stdout="result", stderr="diagnostic")
                workspace.execute_command.return_value = expected
                result = check_recovery(
                    workspace, recovery="/recovery", scope="/scope.json", out="/result"
                )
                self.assertIs(result, expected)
                args, kwargs = workspace.execute_command.call_args
                self.assertEqual(kwargs["cwd"], "/recovery")
                self.assertEqual(
                    shlex.split(args[0]),
                    [
                        "env",
                        "python",
                        "-m",
                        "intentbond",
                        "recover-check",
                        "--recovery",
                        "/recovery",
                        "--out",
                        "/result",
                        "--scope",
                        "/scope.json",
                    ],
                )

    def test_default_mode_uses_the_checkout_and_tool_managed_storage(self):
        workspace = Mock()
        prepare_recovery(workspace, repo="/project")
        args, kwargs = workspace.execute_command.call_args
        self.assertEqual(
            shlex.split(args[0]),
            [
                "env",
                "python",
                "-m",
                "intentbond",
                "recover",
                "--repo",
                "/project",
                "--candidate",
                "HEAD",
            ],
        )
        self.assertEqual(kwargs["cwd"], "/project")
        check_recovery(workspace, repo="/project")
        args, kwargs = workspace.execute_command.call_args
        self.assertEqual(
            shlex.split(args[0]),
            [
                "env",
                "python",
                "-m",
                "intentbond",
                "recover-check",
                "--repo",
                "/project",
            ],
        )
        self.assertEqual(kwargs["cwd"], "/project")

    def test_check_requires_one_recovery_target(self):
        workspace = Mock()
        for targets in ({}, {"recovery": "/bundle", "repo": "/project"}):
            with (
                self.subTest(targets=targets),
                self.assertRaisesRegex(ValueError, "either recovery or repo"),
            ):
                check_recovery(workspace, **targets)
        workspace.execute_command.assert_not_called()

    def test_preflight_is_forwarded_without_changing_default_checks(self):
        workspace = Mock()
        check_recovery(workspace, repo="/project", preflight=True)
        args, _ = workspace.execute_command.call_args
        self.assertEqual(shlex.split(args[0])[-1], "--preflight")

    def test_relative_remote_paths_fail_before_dispatch(self):
        workspace = Mock()
        with self.assertRaisesRegex(ValueError, "repo must be an absolute"):
            prepare_recovery(workspace, repo="relative", out="/recovery")
        with self.assertRaisesRegex(ValueError, "recovery must be an absolute"):
            check_recovery(workspace, recovery="relative", out="/result")
        with self.assertRaisesRegex(ValueError, "scope must be an absolute"):
            check_recovery(workspace, recovery="/recovery", out="/result", scope="scope.json")
        workspace.execute_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
