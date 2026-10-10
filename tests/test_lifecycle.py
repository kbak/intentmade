"""Task adapters share outcomes while keeping their original exceptions and evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import measurements
import run
from lifecycle import NeedsInput, PublicationError, TaskLifecycle


class LifecycleTests(unittest.TestCase):
    def test_publication_failure_survives_nested_task_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inner, outer = root / "build", root / "adapter"
            inner.mkdir()
            outer.mkdir()
            report = Mock()
            config = {
                "project": "app",
                "repository": "org/app",
                "repair_attempts": 0,
                "publish_draft": True,
            }
            states = {
                "app": {
                    "base": "base",
                    "commit": "head",
                    "branch": "task",
                    "repository": "/retained",
                }
            }
            error = RuntimeError("GitHub unavailable")
            with (
                patch.object(run, "implementation_attempt", return_value=({"app": 0}, [])),
                patch.object(run.repair_context, "retain"),
                patch.object(
                    run,
                    "review_changes",
                    return_value=run.ReviewResult(verdict="PASS", summary="Reviewed"),
                ),
                patch.object(run, "publish", side_effect=error),
                self.assertRaises(RuntimeError) as raised,
            ):
                with TaskLifecycle(
                    artifact=outer, task="issue", kind="issue", report=report
                ) as lifecycle:
                    lifecycle.save({"status": "RUNNING", "repositories": {}})
                    run.execute_build([config], "task", "spec", "", None, True, inner, states)
            self.assertIs(raised.exception, error)
            # [utest~im-task-outcome-propagation~1->req~im-measurement-outcomes~1]
            for directory in (inner, outer):
                self.assertEqual(
                    json.loads((directory / "result.json").read_text())["status"],
                    "PUBLICATION_FAILED",
                )
                self.assertEqual(
                    json.loads((directory / "metrics.json").read_text())["status"],
                    "PUBLICATION_FAILED",
                )
            self.assertEqual(report.call_count, 1)
            self.assertEqual(report.call_args.args[0], "PUBLICATION_FAILED")

    def test_terminal_report_reads_completed_measurements(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observed = []

            def report(status, message, **details):
                metrics = json.loads(details["metrics"].read_text())
                observed.append((status, metrics["status"], metrics["completed_at"]))

            with TaskLifecycle(
                artifact=root, task="task", kind="feature", report=report
            ) as lifecycle:
                lifecycle.save({"status": "RUNNING", "repositories": {}})
                measurements.begin_attempt(0, "initial")
                lifecycle.complete("PASSED", "Validated")
                self.assertEqual(observed, [])
            self.assertEqual(observed[0][:2], ("PASSED", "PASSED"))
            self.assertIsNotNone(observed[0][2])
            self.assertIsNone(measurements.CURRENT.get())

    def test_failure_reports_once_and_keeps_input_and_publication_distinct(self):
        class InheritedPublicationError(PublicationError):
            pass

        for error, expected in (
            (NeedsInput("Which behavior?"), "NEEDS_INPUT"),
            (InheritedPublicationError("Retry the POST"), "PUBLICATION_FAILED"),
            (InterruptedError("Cancelled"), "FAILED"),
        ):
            with self.subTest(status=expected):
                report = Mock()
                with self.assertRaises(type(error)) as raised:
                    with TaskLifecycle(report=report) as lifecycle:
                        lifecycle.fail(error)
                        raise error
                self.assertIs(raised.exception, error)
                report.assert_called_once()
                self.assertEqual(report.call_args.args[0], expected)


if __name__ == "__main__":
    unittest.main()
