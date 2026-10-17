"""Native child dispatch, ambiguity handling and delayed-task authority checks."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import execution
import monitor
import native_queue
import reporting
import review_requests
from lifecycle import NeedsInput

CONFIG = {"project": "app", "repository": "org/app", "branch": "main"}


class NativeQueueTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.jobs = []

        def install(definition, payload):
            self.records.append({**definition, "id": str(len(self.records))})
            return self.records[-1]

        self.native = SimpleNamespace(
            records=lambda: copy.deepcopy(self.records),
            files=lambda job: self.jobs.append(job) or {"job.json": job},
            install=Mock(side_effect=install),
        )
        p = patch.object(native_queue, "registration", return_value=self.native)
        p.start()
        self.addCleanup(p.stop)
        self.work = {
            "kind": "issue",
            "key": "issue:3:content-abc",
            "item": {"number": 3},
            "snapshot": {"content_sha256": "abc"},
            "answer_id": None,
        }

    def test_each_task_gets_an_independent_native_recipe_without_running_in_scan(self):
        # [utest~im-task-concurrency-dispatch~1->req~im-repository-locks~1]
        with (
            patch.object(native_queue, "api", return_value={"id": "run"}) as api,
            patch.object(native_queue.deployment, "run_timeout", return_value=36000),
        ):
            started = Mock()
            self.assertTrue(native_queue.enqueue(CONFIG, self.work, on_start=started))
            other = {**self.work, "key": "issue:4:content-def", "item": {"number": 4}}
            self.assertTrue(native_queue.enqueue(CONFIG, other))
            self.assertFalse(native_queue.enqueue(CONFIG, self.work, on_start=started))
            self.assertEqual(api.call_count, 2)
            started.assert_called_once()
            self.assertNotEqual(self.records[0]["name"], self.records[1]["name"])
            self.assertTrue(all(r["entrypoint"] == "python monitor.py" for r in self.records))
            self.assertTrue(all(r["timeout"] == 36000 for r in self.records))
            self.assertEqual(self.jobs[0]["scheduled_task"], self.work)

    def test_lost_dispatch_acknowledgement_does_not_replay_without_a_new_answer(self):
        # [utest~im-task-concurrency-no-replay~1->req~im-repository-locks~1]
        with (
            patch.object(
                native_queue, "api", side_effect=TimeoutError("lost acknowledgement")
            ) as api,
            patch.object(native_queue.deployment, "run_timeout", return_value=36000),
        ):
            with self.assertRaises(TimeoutError):
                native_queue.enqueue(CONFIG, self.work)
            self.assertFalse(native_queue.enqueue(CONFIG, self.work))
            api.assert_called_once()
        with (
            patch.object(native_queue, "api", return_value={"id": "retry"}),
            patch.object(native_queue.deployment, "run_timeout", return_value=36000),
        ):
            self.assertTrue(
                native_queue.enqueue(CONFIG, {**self.work, "answer_id": "fresh-user-event"})
            )

    def test_child_rechecks_issue_snapshot_before_claiming_or_building(self):
        # [utest~im-task-concurrency-authority~1->req~im-approval-snapshot~1]
        with (
            patch.object(execution, "task") as task,
            patch.object(
                monitor, "implement_issue", return_value={"status": "NEEDS_INPUT"}
            ) as issue,
        ):
            self.assertFalse(native_queue.execute(CONFIG, self.work, "credential"))
            self.assertEqual(issue.call_args.kwargs["expected"], self.work["snapshot"])
            self.assertTrue(task.call_args.kwargs["wait_task"])

    def test_queued_automatic_issue_respects_current_manual_intake(self):
        with (
            patch.object(execution, "task"),
            patch.object(native_queue.deployment, "current_issue_intake", return_value="manual"),
            patch.object(monitor, "implement_issue") as issue,
        ):
            with self.assertRaises(NeedsInput):
                native_queue.execute(
                    {**CONFIG, "issue_intake": "automatic"}, self.work, "credential"
                )
            issue.assert_not_called()

    def test_queued_issue_rejects_an_intervening_attempt_or_stale_answer(self):
        with (
            patch.object(execution, "task"),
            patch.object(reporting, "read_report", return_value={"run_id": "new-attempt"}),
            patch.object(reporting, "resume_reply", return_value=None),
            patch.object(monitor, "implement_issue") as issue,
        ):
            with self.assertRaises(NeedsInput):
                native_queue.execute(
                    CONFIG, {**self.work, "report_run_id": "old-attempt"}, "credential"
                )
            work = {
                **self.work,
                "item": {"number": 3, "factory_resume": {"id": "old-answer", "snapshot": {}}},
            }
            with self.assertRaises(NeedsInput):
                native_queue.execute(CONFIG, work, "credential")
            issue.assert_not_called()

    def test_changed_review_revision_never_starts_review(self):
        before = {"number": 3, "head": {"sha": "old"}}
        after = {"number": 3, "head": {"sha": "new"}}
        with (
            patch.object(execution, "task"),
            patch.object(monitor.reviews, "_get_pr", return_value=after),
            patch.object(review_requests, "ReviewRequests"),
            patch.object(review_requests, "snapshot", side_effect=lambda c, pr: pr["head"]["sha"]),
            patch.object(monitor, "review_pr") as review,
        ):
            with self.assertRaises(NeedsInput):
                native_queue.execute(CONFIG, {"kind": "review", "item": before}, "credential")
            review.assert_not_called()
