"""Controller shutdown refuses active work even when schedules were paused."""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_guard  # noqa: E402


class RunGuardTests(unittest.TestCase):
    def test_unstarted_controller_needs_no_api(self):
        run = Mock(return_value=SimpleNamespace(stdout=""))
        run_guard.require_idle(run)
        self.assertEqual(run.call_count, 1)

    def test_live_controller_uses_authenticated_native_check(self):
        run = Mock(return_value=SimpleNamespace(stdout="controller\n"))
        run_guard.require_idle(run)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[-1], run_guard.CHECK)

    def test_paused_disabled_and_older_active_runs_block_shutdown(self):
        for status in ["PENDING", "RUNNING"]:
            with self.subTest(status=status):
                api = Mock(
                    side_effect=[
                        {"runs": [{"id": "done", "status": "COMPLETED"}] * 100},
                        {"runs": [{"id": "still-active", "status": status}]},
                    ]
                )
                configure = SimpleNamespace(
                    records=lambda: [{"id": "automation", "name": "Paused task", "enabled": False}],
                    api=api,
                )
                with (
                    patch.dict(sys.modules, {"configure": configure}),
                    contextlib.redirect_stderr(io.StringIO()) as error,
                ):
                    with self.assertRaises(SystemExit) as stopped:
                        exec(run_guard.CHECK, {})
                # [utest~im-interrupted-run-deploy-guard~1->req~im-interrupted-run~1]
                self.assertEqual(stopped.exception.code, 1)
                self.assertIn("still-active", error.getvalue())
                self.assertIn("Paused task", error.getvalue())

    def test_api_failure_does_not_allow_shutdown(self):
        configure = SimpleNamespace(records=Mock(side_effect=RuntimeError("unavailable")))
        with patch.dict(sys.modules, {"configure": configure}):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                exec(run_guard.CHECK, {})


if __name__ == "__main__":
    unittest.main()
