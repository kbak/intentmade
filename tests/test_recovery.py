"""Exercise cleanup, repair, human handoff, and native outcome contracts."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cleanup
import httpx
import reporting
import run
from openhands.automation.schemas import RunCompleteRequest, RunPhaseRequest


class RecoveryTests(unittest.TestCase):
    def test_failed_export_keeps_workspace_and_original_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, "export failed"):
                with cleanup.job_directory(root, root) as job:
                    (job / "valuable-code.txt").write_text("retained")
                    raise RuntimeError("export failed")
            self.assertEqual((job / "valuable-code.txt").read_text(), "retained")
            self.assertEqual((root / "recovery-workspace.txt").read_text(), str(job))

    def test_cleanup_failure_does_not_abort_completed_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(cleanup, "remove_job", side_effect=PermissionError("root output")):
                with cleanup.job_directory(root, root) as job:
                    (job / "test-output").write_text("test results")
            self.assertIn(str(job), (root / "cleanup-warnings.log").read_text())

    def test_bounded_repair_uses_test_failure_and_original_spec_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
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
            reviews = [run.ReviewResult(verdict="PASS", summary="No defects")] * 2
            with (
                patch.object(
                    run,
                    "implementation_attempt",
                    side_effect=[
                        ({"example": 1}, ["failing test: expected retry button"]),
                        ({"example": 0}, ["all passed"]),
                    ],
                ) as implement,
                patch.object(run, "review_changes", side_effect=reviews) as review,
                patch.object(run, "publish", return_value="https://example.test/pr") as publish,
            ):
                result = run.execute_build(
                    [config], "task", "Original specification", "token", 42, True, root, states
                )
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(review.call_count, 2)
            self.assertIn("Original specification", implement.call_args.args[3])
            self.assertIn("expected retry button", implement.call_args.args[3])
            publish.assert_called_once()

    def test_questions_stop_before_review_and_publication_and_save_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(
                    run,
                    "implementation_attempt",
                    side_effect=reporting.NeedsInput(
                        "Should /start restore all notifications or reminders only?"
                    ),
                ),
                patch.object(run, "review_changes") as review,
                patch.object(run, "publish") as publish,
            ):
                with self.assertRaises(reporting.NeedsInput):
                    run.execute_build(
                        [{"repair_attempts": 1}], "task", "spec", "", 42, True, root, {}
                    )
            result = json.loads((root / "result.json").read_text())
            self.assertEqual(result["status"], "NEEDS_INPUT")
            self.assertIn("reminders", result["error"])
            review.assert_not_called()
            publish.assert_not_called()

    def test_blocked_reviewer_does_not_spend_a_code_repair_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(
                    run, "implementation_attempt", return_value=({"example": 0}, [])
                ) as implement,
                patch.object(
                    run,
                    "review_changes",
                    return_value=run.ReviewResult(
                        verdict="BLOCKED", summary="Sandbox cannot read source"
                    ),
                ),
                patch.object(run, "publish") as publish,
            ):
                with self.assertRaisesRegex(RuntimeError, "infrastructure blocked"):
                    run.execute_build(
                        [{"repair_attempts": 1}], "task", "spec", "", None, True, Path(temp), {}
                    )
            implement.assert_called_once()
            publish.assert_not_called()

    def test_only_new_explicit_user_reply_can_resume_same_snapshot(self):
        record = {
            "status": "NEEDS_INPUT",
            "conversation_id": "conversation",
            "updated_at": "2026-09-08T12:00:00+00:00",
            "snapshot": {"content": "hash"},
        }

        def event(text, role="user", timestamp="2026-09-08T13:00:00Z"):
            return {
                "id": "reply",
                "kind": "MessageEvent",
                "source": "user",
                "timestamp": timestamp,
                "llm_message": {"role": role, "content": [{"text": text}]},
            }

        cases = [
            event("what happened?"),
            event("resume: yes", "assistant"),
            event("resume: yes", timestamp="2026-09-08T11:00:00Z"),
            event("resume:"),
        ]
        with patch.object(reporting, "read_report", return_value=record):
            for item in cases:
                with patch.object(reporting, "api", return_value={"items": [item]}):
                    self.assertIsNone(reporting.resume_reply({}, "task"))
            with patch.object(
                reporting,
                "api",
                side_effect=[
                    {"items": cases, "next_page_id": "next"},
                    {"items": [event("resume: reminders only", timestamp="2026-09-08T13:00:00")]},
                ],
            ) as api:
                result = reporting.resume_reply({}, "task")
                self.assertEqual(result["answer"], "reminders only")
                self.assertEqual(result["snapshot"], record["snapshot"])
                self.assertEqual(api.call_args.kwargs["params"]["page_id"], "next")

    def test_deleted_conversation_has_no_resume_reply_without_losing_task_record(self):
        record = {"status": "NEEDS_INPUT", "conversation_id": "deleted"}
        response = httpx.Response(
            404, request=httpx.Request("GET", "http://canvas/conversations/deleted/events/search")
        )
        error = httpx.HTTPStatusError(
            "Conversation not found", request=response.request, response=response
        )
        for pages in ([error], [{"items": [], "next_page_id": "next"}, error]):
            with (
                self.subTest(pages=len(pages)),
                patch.object(reporting, "read_report", return_value=record),
                patch.object(reporting, "api", side_effect=pages),
                patch.object(reporting, "write_report") as write,
            ):
                self.assertIsNone(reporting.resume_reply({}, "task"))
                write.assert_not_called()
                self.assertEqual(record, {"status": "NEEDS_INPUT", "conversation_id": "deleted"})

    def test_reply_auth_rate_limit_and_server_errors_are_not_hidden(self):
        for status in (401, 403, 429, 500):
            response = httpx.Response(status, request=httpx.Request("GET", "http://canvas/events"))
            error = httpx.HTTPStatusError(
                "Unavailable", request=response.request, response=response
            )
            with (
                self.subTest(status=status),
                patch.object(
                    reporting,
                    "read_report",
                    return_value={"status": "FAILED", "conversation_id": "report"},
                ),
                patch.object(reporting, "api", side_effect=error),
                self.assertRaises(httpx.HTTPStatusError),
            ):
                reporting.resume_reply({}, "task")

    def test_skipped_and_failed_callbacks_are_distinct_and_link_real_conversation(self):
        with (
            patch.dict(os.environ, {"AUTOMATION_RUN_ID": "run"}),
            patch.object(reporting, "api") as api,
        ):
            with reporting.run_report() as report:
                report["conversation_id"] = "durable-conversation"
                reporting.outcome("SKIPPED", "Repository busy")
            body = api.call_args.kwargs["json"]
            RunCompleteRequest.model_validate(body)
            self.assertEqual(body["status"], "SKIPPED")
            self.assertEqual(body["conversation_id"], "durable-conversation")
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                with reporting.run_report():
                    raise RuntimeError("test failure")
            self.assertEqual(api.call_args.kwargs["json"]["status"], "FAILED")

    def test_structured_implementation_cannot_hide_unanswered_questions(self):
        with self.assertRaises(ValueError):
            run.ImplementationResult(
                status="IMPLEMENTED", summary="Maybe", questions=["Which option?"]
            )
        with self.assertRaises(ValueError):
            run.ImplementationResult(status="NEEDS_INPUT", summary="Blocked")

    def test_live_phase_links_report_and_keeps_parent_auth_during_worker_execution(self):
        with (
            patch.dict(os.environ, {"AUTOMATION_RUN_ID": "run", "OH_SESSION_API_KEYS_0": "parent"}),
            patch.object(reporting, "api") as api,
        ):
            with reporting.run_report() as report:
                report["conversation_id"] = "persistent-conversation"
                with patch.dict(os.environ, {"OH_SESSION_API_KEYS_0": "worker"}):
                    reporting.phase("Running tests")
                payload = api.call_args.kwargs
                RunPhaseRequest.model_validate(payload["json"])
                self.assertEqual(payload["headers"]["X-Session-API-Key"], "parent")
                self.assertEqual(payload["json"]["conversation_id"], "persistent-conversation")
            self.assertNotIn("parent", json.dumps(api.call_args.kwargs["json"]))


if __name__ == "__main__":
    unittest.main()
