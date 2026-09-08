"""Keep paused issue ownership across real report and scheduler transitions."""

import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import monitor
import reporting

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "main",
    "issue_label": None,
    "assignee": "@token-owner",
    "daily_tasks": None,
    "max_tasks_per_poll": 2,
}
ISSUE = {
    "number": 1406,
    "title": "Fix invitation validation",
    "body": "Ask whether validation should retry automatically.",
    "state": "open",
    "labels": [],
    "assignees": [],
}


class PausedOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.issue = copy.deepcopy(ISSUE)
        self.state = None
        self.events = []
        self.mutations = []
        self.login = "kbak"
        self.build = Mock(side_effect=reporting.NeedsInput("Retry explicitly or automatically?"))
        for target, name, value in (
            (monitor, "DATA", root),
            (reporting, "DATA", root),
            (reporting, "ACTIVE", {"run_id": "offline-run", "tasks": []}),
            (reporting, "api", self.native),
            (monitor, "github", self.github),
            (monitor, "job_id", lambda: "offline-run"),
            (monitor, "build", self.build),
            (monitor.issues, "_get_issue", lambda *args: copy.deepcopy(self.issue)),
            (monitor.issues, "_github_paginate", self.paginate),
            (monitor.issues, "_kv_get", lambda *args: copy.deepcopy(self.state)),
            (monitor.issues, "_kv_set", self.save),
            (monitor.reviews, "_list_open_prs", lambda *args: []),
        ):
            self.stack.enter_context(patch.object(target, name, value))

    def native(self, method, path, **kwargs):
        if path.endswith("/events/search"):
            return {"items": copy.deepcopy(self.events)}
        return {"id": "conversation"}

    def github(self, credential, method, path, **kwargs):
        if path == "/user":
            return {"login": self.login}
        if method in {"POST", "DELETE"}:
            self.mutations.append(method)
            self.issue["assignees"] = (
                [{"login": name} for name in kwargs["body"]["assignees"]]
                if method == "POST"
                else []
            )
            return copy.deepcopy(self.issue)
        return {"sha": "base"}

    def paginate(self, credential, path, params=None):
        if path.endswith("/issues"):
            # GitHub's ordinary discovery excludes every assigned issue.
            return [] if self.issue["assignees"] else [copy.deepcopy(self.issue)]
        return []

    def save(self, key, value):
        self.state = copy.deepcopy(value)

    def reply(self, text="resume: Explicit Retry button only"):
        self.events.append(
            {
                "id": str(len(self.events)),
                "timestamp": reporting.now(),
                "kind": "MessageEvent",
                "source": "user",
                "llm_message": {"role": "user", "content": [{"text": text}]},
            }
        )

    def pause(self):
        monitor.poll(CONFIG, "offline-token")
        self.assertEqual(self.issue["assignees"], [{"login": "kbak"}])
        record = reporting.read_report(CONFIG, "issue-1406")
        self.assertEqual(record["status"], "NEEDS_INPUT")
        self.assertEqual(record["assignee"], "kbak")
        self.assertEqual(self.mutations, ["POST"])
        self.build.reset_mock()

    def test_pause_retains_claim_and_only_explicit_answer_resumes_it_once(self):
        self.pause()
        monitor.poll(CONFIG, "offline-token")
        self.reply("Why is it waiting?")
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
        self.reply()
        # Another product question still keeps the existing claim and consumes
        # this answer once, without assigning/unassigning on every continuation.
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_called_once()
        self.assertIn("Explicit Retry button only", self.build.call_args.args[2])
        self.assertEqual(self.mutations, ["POST"])
        self.assertEqual(self.issue["assignees"], [{"login": "kbak"}])
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_called_once()

    def test_manual_continuation_accepts_the_recorded_claim(self):
        self.pause()
        self.build.side_effect = None
        self.build.return_value = {"status": "PASSED", "repositories": {}}
        record = reporting.read_report(CONFIG, "issue-1406")
        result = monitor.implement_issue(
            CONFIG,
            self.issue,
            "offline-token",
            resume={"snapshot": record["snapshot"], "answer": "Explicit Retry button only"},
        )
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(self.mutations, ["POST"])

    def test_reply_job_skips_new_work_and_consumes_answer_once_across_scheduler_race(self):
        monitor.poll(CONFIG, "offline-token", replies_only=True)
        self.build.assert_not_called()
        self.pause()
        self.reply()
        monitor.poll(CONFIG, "offline-token", replies_only=True)
        self.build.assert_called_once()
        monitor.poll(CONFIG, "offline-token")
        monitor.poll(CONFIG, "offline-token", replies_only=True)
        self.build.assert_called_once()
        self.assertEqual(self.mutations, ["POST"])

    def test_same_login_without_record_does_not_authorize_implementation(self):
        self.issue["assignees"] = [{"login": "kbak"}]
        resume = {"snapshot": monitor.issue_snapshot(CONFIG, self.issue)[1], "answer": "retry"}
        self.assertIsNone(monitor.implement_issue(CONFIG, self.issue, "offline-token", resume))
        self.build.assert_not_called()
        self.assertEqual(self.mutations, [])

    def test_changed_specification_does_not_resume_or_restart_assigned_task(self):
        self.pause()
        self.reply()
        self.issue["body"] = "Changed product requirements"
        monitor.poll(CONFIG, "offline-token")
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
        self.assertEqual(self.mutations, ["POST"])

    def test_other_owner_closed_issue_and_changed_token_owner_block_continuation(self):
        self.pause()
        self.reply()
        for changes, login in (
            ({"assignees": [{"login": "someone-else"}]}, "kbak"),
            ({"assignees": [{"login": "kbak"}, {"login": "someone-else"}]}, "kbak"),
            ({"state": "closed", "assignees": [{"login": "kbak"}]}, "kbak"),
            ({"state": "open", "assignees": [{"login": "kbak"}]}, "new-token-owner"),
        ):
            with self.subTest(changes=changes, login=login):
                self.issue.update(changes)
                self.login = login
                monitor.poll(CONFIG, "offline-token")
                self.build.assert_not_called()
                self.assertEqual(self.mutations, ["POST"])

    def test_failed_continuation_keeps_preexisting_claim(self):
        self.pause()
        self.reply()
        self.build.side_effect = RuntimeError("tests failed")
        with self.assertRaisesRegex(RuntimeError, "tests failed"):
            monitor.poll(CONFIG, "offline-token")
        self.assertEqual(self.issue["assignees"], [{"login": "kbak"}])
        self.assertEqual(self.mutations, ["POST"])

    def test_missing_claim_record_does_not_turn_stale_answer_into_fresh_work(self):
        self.pause()
        record = reporting.read_report(CONFIG, "issue-1406")
        record.pop("assignee")
        reporting.write_report(CONFIG, "issue-1406", record)
        self.reply()
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
