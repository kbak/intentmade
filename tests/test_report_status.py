"""Report reader failures stay separate from durable task state and continuation."""

import unittest
from unittest.mock import patch

import reporting


class ReportStatusTests(unittest.TestCase):
    def setUp(self):
        self.report = reporting.TaskReport.__new__(reporting.TaskReport)
        self.report.config, self.report.task = {"project": "app"}, "task"
        self.report.record = {"conversation_id": "report", "answers": ["accepted answer"]}

    def test_updates_keep_task_and_assistant_states_separate(self):
        for status in ("RUNNING", "NEEDS_INPUT", "FAILED", "PASSED"):
            with (
                self.subTest(status=status),
                patch.object(reporting, "api", return_value={"execution_status": "error"}),
                patch.object(reporting, "write_report") as write,
                patch.object(reporting, "phase"),
                patch.object(reporting, "post") as post,
            ):
                self.report.update(status, "Original result or question")
            # [utest~im-report-task-status~1->req~im-report-status~1]
            record = write.call_args.args[2]
            self.assertEqual(record["status"], status)
            self.assertEqual(record["assistant_execution_status"], "error")
            self.assertEqual(record["answers"], ["accepted answer"])
            message = post.call_args.args[1]
            self.assertTrue(message.startswith(f"**{status}**\n\nOriginal result or question"))
            self.assertIn("report assistant is unavailable", message)
            self.assertIn("worker runs independently", message)
            self.assertIn("`resume:`", message)
            self.assertFalse(post.call_args.kwargs["run"])

    def test_unavailable_assistant_state_does_not_lose_report(self):
        def api(method, *args, **kwargs):
            if method == "GET":
                self.assertEqual(saved[0]["status"], "NEEDS_INPUT")
                raise RuntimeError("Canvas state unavailable SECRET_SENTINEL")

        saved = []
        with (
            patch.object(reporting, "api", side_effect=api),
            patch.object(
                reporting,
                "write_report",
                side_effect=lambda config, task, record: saved.append(dict(record)),
            ) as write,
            patch.object(reporting, "phase"),
            patch.object(reporting, "post") as post,
        ):
            self.report.update("NEEDS_INPUT", "Which policy applies?")
        # [utest~im-report-unavailable-state~1->req~im-report-status~1]
        self.assertEqual(write.call_args.args[2]["status"], "NEEDS_INPUT")
        self.assertEqual(write.call_args.args[2]["assistant_execution_status"], "unknown")
        self.assertEqual(post.call_args.args[1], "**NEEDS_INPUT**\n\nWhich policy applies?")
        self.assertFalse(post.call_args.kwargs["run"])

    def test_assistant_status_storage_failure_keeps_original_task_and_posts_update(self):
        saved = []

        def write(config, task, record):
            if saved:
                raise OSError("status metadata unavailable")
            saved.append(dict(record))

        with (
            patch.object(reporting, "api", return_value={"execution_status": "error"}),
            patch.object(reporting, "write_report", side_effect=write),
            patch.object(reporting, "phase"),
            patch.object(reporting, "post") as post,
        ):
            self.report.update("PASSED", "Validated")
        self.assertEqual(saved[0]["status"], "PASSED")
        self.assertIn("report assistant is unavailable", post.call_args.args[1])
        self.assertFalse(post.call_args.kwargs["run"])

    def test_generated_questions_do_not_start_a_healthy_assistant(self):
        with (
            patch.object(reporting, "api", return_value={"execution_status": "finished"}),
            patch.object(reporting, "write_report"),
            patch.object(reporting, "phase"),
            patch.object(reporting, "post") as post,
        ):
            self.report.update("NEEDS_INPUT", "Which policy applies?")
        # [utest~im-report-no-auto-start~1->req~im-report-status~1]
        self.assertFalse(post.call_args.kwargs["run"])
        self.assertNotIn("unavailable", post.call_args.args[1])
