"""Task progress reports observed outcomes without scheduling or validation effects."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import progress
import reporting
import run
from openhands.automation.schemas import RunPhaseRequest


class ProgressTests(unittest.TestCase):
    def test_timing_heartbeat_and_stopped_clock(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(progress.time, "monotonic") as clock,
        ):
            clock.return_value = 10
            emit = Mock()
            tracker = progress.TaskProgress("task", temp, ("implementation", "tests"), 1, emit)
            tracker.stage("implementation")
            clock.return_value = 135
            tracker.stop = Mock()
            tracker.stop.wait.side_effect = [False, True]
            tracker.stop.is_set.return_value = False
            tracker.heartbeat()
            saved = tracker.snapshot()
            # [utest~im-canvas-progress-timing~1->req~im-canvas-progress~1]
            self.assertEqual(saved["stages"]["implementation"]["elapsed_seconds"], 125)
            self.assertEqual(saved["stages"]["tests"]["status"], "Waiting")
            self.assertIn("2m 5s in stage", emit.call_args.args[0])
            self.assertIsNone(emit.call_args.args[1])
            tracker.stop.wait.assert_called_with(30)
            tracker.finish("Which login flow?", needs_input=True)
            clock.return_value = 500
            saved = tracker.snapshot()
            self.assertEqual(saved["status"], "Waiting for input")
            self.assertEqual(saved["elapsed_seconds"], 125)
            self.assertIn("Which login flow?", saved["next_action"])
            self.assertIn("resume: YOUR ANSWER", saved["next_action"])

    def test_current_task_routing_parent_auth_and_no_agent_wakeup(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(reporting, "ACTIVE", {"run_id": "run", "conversation_id": "first"}),
            patch.object(reporting, "_RUN_KEY", "parent-fixture"),
            patch.object(reporting, "read_report", return_value={"conversation_id": "second"}),
            patch.object(reporting, "write_report"),
            patch.object(reporting, "api") as api,
        ):
            reporting.TaskReport({"project": "example", "repository": "org/repo"}, "issue-2")
            api.reset_mock()
            with reporting.task_progress("issue-2", temp, ("review",)) as tracker:
                with patch.dict(os.environ, {"OH_SESSION_API_KEYS_0": "worker-fixture"}):
                    reporting.progress_stage("review")
                    reporting.phase("Reviewing")
                    reporting.progress_stage("review", "Passed")
            # [utest~im-canvas-progress-routing~1->req~im-canvas-progress~1]
            self.assertTrue(tracker.stop.is_set())
            self.assertIsNone(reporting._PROGRESS.get())
            for call in api.call_args_list:
                path, body = call.args[1], call.kwargs["json"]
                if path.endswith("/phase"):
                    RunPhaseRequest(**body)
                    self.assertEqual(body["conversation_id"], "second")
                    self.assertEqual(call.kwargs["headers"]["X-Session-API-Key"], "parent-fixture")
                else:
                    self.assertEqual(path, "/api/conversations/second/events")
                    self.assertFalse(body["run"])
                    self.assertEqual(call.kwargs["headers"]["X-Session-API-Key"], "parent-fixture")
                    self.assertEqual(call.kwargs["timeout"], 5)

    def test_reporting_outages_preserve_success_and_original_failure(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(reporting, "ACTIVE", {"run_id": "run", "conversation_id": "report"}),
            patch.object(reporting, "api", side_effect=OSError("Canvas unavailable")),
            patch.object(progress, "atomic_write_text", side_effect=PermissionError("storage")),
        ):
            # [utest~im-canvas-progress-outage~1->req~im-canvas-progress~1]
            with self.assertRaisesRegex(ValueError, "original failure"):
                with reporting.task_progress("task", temp, ("tests",)):
                    reporting.progress_stage("tests")
                    raise ValueError("original failure")
            with reporting.task_progress("task", temp, ("tests",)):
                reporting.progress_stage("tests")
                reporting.progress_stage("tests", "Passed")
            self.assertIsNone(reporting._PROGRESS.get())

    def test_repair_pipeline_resets_checks_and_reports_real_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {
                "project": "example",
                "repository": "org/repo",
                "repair_attempts": 1,
                "publish_draft": True,
            }
            states = {
                "example": {
                    "base": "base",
                    "commit": "head",
                    "branch": "factory/task",
                    "repository": "/retained/task.git",
                }
            }
            with (
                patch.object(reporting, "ACTIVE", None),
                patch.object(
                    run,
                    "implementation_attempt",
                    side_effect=[({"example": 1}, []), ({"example": 0}, [])],
                ),
                patch.object(run.repair_context, "retain"),
                patch.object(
                    run,
                    "review_changes",
                    return_value=run.ReviewResult(verdict="PASS", summary="ok"),
                ),
                patch.object(run, "publish", return_value="https://example.test/pr") as publish,
            ):
                run.execute_build([config], "task", "spec", "", None, True, Path(temp), states)
            saved = json.loads((Path(temp) / "stage-progress.json").read_text())
            # [utest~im-canvas-progress-pipeline~1->req~im-canvas-progress~1]
            self.assertEqual(saved["status"], "Done")
            self.assertEqual(saved["attempt"], 1)
            self.assertEqual(saved["previous_attempts"][0]["stages"]["tests"]["status"], "Failed")
            self.assertEqual(saved["previous_attempts"][0]["stages"]["review"]["status"], "Waiting")
            self.assertEqual(saved["stages"]["tests"]["status"], "Passed")
            self.assertEqual(saved["stages"]["browser_qa"]["status"], "Not required")
            self.assertEqual(saved["stages"]["review"]["status"], "Passed")
            self.assertEqual(saved["stages"]["publication"]["status"], "Published")
            self.assertIn("Repair 1 of 1", (Path(temp) / "stage-progress.md").read_text())
            publish.assert_called_once()

    def test_question_keeps_review_and_publication_waiting(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(reporting, "ACTIVE", None),
            patch.object(
                run, "implementation_attempt", side_effect=reporting.NeedsInput("Choose login flow")
            ),
            patch.object(run, "review_changes") as review,
            patch.object(run, "publish") as publish,
        ):
            with self.assertRaises(reporting.NeedsInput):
                run.execute_build(
                    [{"repair_attempts": 1}], "task", "spec", "", None, True, Path(temp), {}
                )
            saved = json.loads((Path(temp) / "stage-progress.json").read_text())
            # [utest~im-canvas-progress-question~1->req~im-canvas-progress~1]
            self.assertEqual(saved["status"], "Waiting for input")
            self.assertEqual(saved["stages"]["implementation"]["status"], "Waiting for input")
            self.assertEqual(saved["stages"]["review"]["status"], "Waiting")
            self.assertEqual(saved["stages"]["publication"]["status"], "Waiting")
            review.assert_not_called()
            publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
