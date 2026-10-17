"""Lost execution is visible without replaying work or expiring quiet builds."""

import copy
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import common
import execution
import httpx
import reporting
import run_health


class RunHealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {"project": "app", "repository": "org/repo"}
        self.data_patch = patch.object(reporting, "DATA", self.root)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.lock_patch = patch.object(run_health, "lock", return_value=nullcontext())
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.report = {
            "task": "issue-15",
            "run_id": "run-1",
            "automation_id": "automation",
            "conversation_id": "thread",
            "status": "RUNNING",
            "snapshot": {"approved": "unchanged"},
            "answers": ["original approval"],
        }
        reporting.write_report(self.config, "issue-15", self.report)
        self.native = {
            "id": "run-1",
            "status": "RUNNING",
            "conversation_id": "thread",
            "bash_command_id": "command-1",
            "sandbox_id": None,
            "started_at": "2026-10-16T20:25:42Z",
        }
        self.events, self.calls, self.outputs = [], [], []
        self.start = 1792182430.0  # 20:27:10 UTC, after the interrupted dispatch.

    def test_active_task_exclusion_prevents_reconciliation_from_overwriting_its_report(self):
        # [utest~im-task-concurrency-metadata~1->req~im-repository-locks~1]
        with patch.object(common, "DATA", self.root), patch.object(run_health, "api") as api:
            with common.lock("task-" + execution.task_key(self.config, "issue-15")):
                run_health.reconcile(self.config)
            api.assert_not_called()
            self.assertEqual(reporting.read_report(self.config, "issue-15"), self.report)

    def api(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/runs"):
            return {"runs": [copy.deepcopy(self.native)]}
        if path == "/api/automation/v1":
            return {"automations": [{"id": "automation"}], "total": 1}
        if path == "/api/bash/bash_events/search":
            self.assertEqual(kwargs["params"]["command_id__eq"], "command-1")
            return {"items": self.outputs}
        if path.endswith("/events/search"):
            return {"items": self.events}
        if path.endswith("/complete"):
            self.native["status"] = kwargs["json"]["status"]
        return {}

    def post(self, conversation, message):
        self.assertEqual(conversation, "thread")
        self.events.append(
            {
                "kind": "MessageEvent",
                "source": "user",
                "llm_message": {"content": [{"type": "text", "text": message}]},
            }
        )

    def reconcile(self, post=None):
        with (
            patch.object(run_health, "api", side_effect=self.api),
            patch.object(run_health, "controller_started_at", return_value=self.start),
            patch.object(run_health, "retain_interruption") as retain,
            patch.object(reporting, "post", side_effect=post or self.post),
        ):
            run_health.reconcile(self.config)
        return retain

    def test_restart_reports_interruption_once_and_preserves_approval(self):
        self.reconcile()
        self.reconcile()
        record = reporting.read_report(self.config, "issue-15")
        # [utest~im-interrupted-run-notice~1->req~im-interrupted-run~1]
        self.assertEqual(record["status"], "FAILED")
        self.assertTrue(record["interruption"]["notified"])
        self.assertEqual(record["snapshot"], self.report["snapshot"])
        self.assertEqual(record["answers"], self.report["answers"])
        self.assertEqual(len(self.events), 1)
        self.assertIn(
            "**INTERRUPTED — action needed**", self.events[0]["llm_message"]["content"][0]["text"]
        )
        self.assertIn("resume: retry", self.events[0]["llm_message"]["content"][0]["text"])
        self.assertEqual(len([p for m, p, _ in self.calls if p.endswith("/complete")]), 1)
        self.assertFalse(any(p.endswith("/dispatch") for _, p, _ in self.calls))

    def test_quiet_live_cloud_pending_and_unknown_runs_are_not_failed(self):
        for change, controller in [
            ({}, 1792182200.0),
            ({"sandbox_id": "remote-worker"}, self.start),
            ({"status": "PENDING"}, self.start),
            ({"bash_command_id": None}, self.start),
            ({}, None),
        ]:
            with self.subTest(change=change, controller=controller):
                self.native.update(change)
                self.start = controller
                retain = self.reconcile()
                # [utest~im-interrupted-run-no-replay~1->req~im-interrupted-run~1]
                self.assertEqual(
                    reporting.read_report(self.config, "issue-15")["status"], "RUNNING"
                )
                retain.assert_not_called()
                self.assertFalse(self.events)
                self.native.update(status="RUNNING", bash_command_id="command-1", sandbox_id=None)

    def test_scoped_finished_command_is_detected_before_the_run_deadline(self):
        self.start = 1792182200.0
        self.outputs = [{"exit_code": 137}]
        self.reconcile()
        self.assertEqual(self.native["status"], "FAILED")
        self.assertIn("process exited", reporting.read_report(self.config, "issue-15")["failure"])

    def test_native_failure_is_mirrored_without_recompleting_or_replaying(self):
        for status in ["FAILED", "CANCELLED"]:
            with self.subTest(status=status):
                reporting.write_report(self.config, "issue-15", self.report)
                self.native["status"] = status
                self.calls.clear()
                self.reconcile()
                self.assertEqual(reporting.read_report(self.config, "issue-15")["status"], "FAILED")
                self.assertFalse(
                    any(
                        p.endswith("/complete") or p.endswith("/dispatch") for _, p, _ in self.calls
                    )
                )

    def test_lost_notification_response_is_reconciled_without_a_duplicate(self):
        def lost(conversation, message):
            self.post(conversation, message)
            raise httpx.ReadTimeout("response lost")

        self.reconcile(lost)
        self.assertFalse(reporting.read_report(self.config, "issue-15")["interruption"]["notified"])
        self.reconcile()
        self.assertTrue(reporting.read_report(self.config, "issue-15")["interruption"]["notified"])
        self.assertEqual(len(self.events), 1)

    def test_api_outage_does_not_fabricate_an_interruption(self):
        with patch.object(run_health, "api", side_effect=httpx.ConnectError("unavailable")):
            run_health.reconcile(self.config)
        self.assertEqual(reporting.read_report(self.config, "issue-15"), self.report)

    def test_late_task_completion_and_new_continuation_win(self):
        for status, run_id in [("PASSED", "run-1"), ("RUNNING", "new-run")]:
            with self.subTest(status=status, run_id=run_id):

                def lookup(*args, **kwargs):
                    reporting.write_report(
                        self.config, "issue-15", {**self.report, "status": status, "run_id": run_id}
                    )
                    return self.native

                reporting.write_report(self.config, "issue-15", self.report)
                with patch.object(run_health, "find_run", side_effect=lookup):
                    self.reconcile()
                record = reporting.read_report(self.config, "issue-15")
                self.assertEqual((record["status"], record["run_id"]), (status, run_id))
                self.assertFalse(self.events)

    def test_native_completion_race_preserves_success(self):
        original = self.api

        def api(method, path, **kwargs):
            if path.endswith("/complete"):
                self.native["status"] = "COMPLETED"
                response = httpx.Response(
                    409, request=httpx.Request(method, "http://fixture" + path)
                )
                raise httpx.HTTPStatusError(
                    "already completed", request=response.request, response=response
                )
            return original(method, path, **kwargs)

        with patch.object(self, "api", side_effect=api):
            self.reconcile()
        self.assertEqual(reporting.read_report(self.config, "issue-15"), self.report)
        self.assertFalse(self.events)

    def test_old_interruption_does_not_fail_a_healthy_continuation(self):
        self.start = 1792182200.0
        record = {
            **self.report,
            "interruption": {"run_id": "old-run", "reason": "Old interruption", "notified": False},
        }
        reporting.write_report(self.config, "issue-15", record)
        self.reconcile()
        self.assertEqual(reporting.read_report(self.config, "issue-15"), record)
        self.assertFalse(self.events)

    def test_legacy_report_can_find_its_exact_native_run(self):
        record = {k: v for k, v in self.report.items() if k != "automation_id"}
        reporting.write_report(self.config, "issue-15", record)
        self.reconcile()
        self.assertTrue(any(p == "/api/automation/v1" for _, p, _ in self.calls))
        self.assertEqual(reporting.read_report(self.config, "issue-15")["status"], "FAILED")

    def test_ended_progress_stops_claiming_work_is_running(self):
        with patch.object(
            run_health,
            "Path",
            side_effect=lambda path: self.root if path == "/projects/artifacts" else Path(path),
        ):
            artifact = self.root / "run-1-issue-15"
            artifact.mkdir()
            (artifact / "result.json").write_text(
                json.dumps({"status": "RUNNING", "phase": "IMPLEMENTING"})
            )
            progress = reporting.TaskProgress("issue-15", artifact, ["implementation", "tests"], 1)
            progress.stage("implementation")
            before = json.loads((artifact / "stage-progress.json").read_text())
            run_health.retain_interruption("issue-15", "run-1", "Inspect retained work and resume.")
        data = json.loads((artifact / "stage-progress.json").read_text())
        self.assertEqual(data["status"], "Interrupted")
        self.assertEqual(data["stages"]["implementation"]["status"], "Interrupted")
        self.assertEqual(data["elapsed_seconds"], before["elapsed_seconds"])
        self.assertIn("Interrupted", (artifact / "stage-progress.md").read_text())
        self.assertEqual(json.loads((artifact / "result.json").read_text())["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
