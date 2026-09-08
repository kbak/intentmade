"""Automatic issue pickup without labels or a daily budget."""

import copy
import datetime
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import approval
import monitor
import policy
import run

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "main",
    "issue_label": None,
    "daily_tasks": None,
    "max_tasks_per_poll": 2,
    "assignee": "factory-bot",
}
ISSUE = {
    "number": 1402,
    "title": "Restore Telegram scanning",
    "body": "Implement recovery and test it",
    "state": "open",
    "labels": [{"name": "bug"}],
    "assignees": [],
}


class AutomaticSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.state = {
            "day": datetime.datetime.now(datetime.UTC).date().isoformat(),
            "count": 1000,
            "done": {},
            "triaged": {},
        }
        self.items = [copy.deepcopy(ISSUE)]
        self.build = Mock(return_value={"status": "PASSED"})
        for target, name, replacement in (
            (monitor, "DATA", self.root),
            (monitor, "job_id", lambda: "offline-run"),
            (monitor, "implement_issue", self.build),
            (monitor.issues, "_kv_get", lambda key: copy.deepcopy(self.state)),
            (monitor.issues, "_kv_set", self.save),
            (monitor.issues, "_github_paginate", lambda *args: copy.deepcopy(self.items)),
            (monitor.reviews, "_list_open_prs", lambda *args: []),
        ):
            self.stack.enter_context(patch.object(target, name, replacement))
        self.label_lookup = self.stack.enter_context(
            patch.object(monitor.issues, "_latest_trigger_label_event")
        )

    def save(self, key, value):
        self.state = copy.deepcopy(value)

    def test_unlabeled_issues_start_despite_exhausted_old_daily_budget(self):
        self.items += [{**ISSUE, "number": 1406}, {**ISSUE, "number": 1407}]
        monitor.poll(CONFIG, "offline-token")
        self.assertEqual(
            [call.args[1]["number"] for call in self.build.call_args_list], [1402, 1406]
        )
        self.assertEqual(self.state["count"], 1002)
        self.label_lookup.assert_not_called()

    def test_assigned_closed_issues_and_pull_requests_are_excluded(self):
        self.items = [
            {**ISSUE, "assignees": [{"login": "someone"}]},
            {**ISSUE, "state": "closed"},
            {**ISSUE, "pull_request": {"url": "https://example.test/pr"}},
        ]
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()

    def test_failures_remain_deduplicated_after_history_pruning_and_metadata_changes(self):
        self.build.side_effect = RuntimeError("tests failed")
        with self.assertRaisesRegex(RuntimeError, "tests failed"):
            monitor.poll(CONFIG, "offline-token")
        self.state["done"] = {}
        self.items[0].update(labels=[], updated_at="later", comments=3)
        self.build.reset_mock(side_effect=True)
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
        self.items[0]["body"] += "\nUpdated specification after inspecting the failure."
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_called_once()

    def test_busy_repository_does_not_consume_the_issue_attempt(self):
        self.build.side_effect = BlockingIOError("busy")
        monitor.poll(CONFIG, "offline-token")
        self.assertEqual(self.state["count"], 1000)
        self.assertEqual(list(self.root.glob("issue-attempts/**/*.json")), [])
        self.build.reset_mock(side_effect=True)
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_called_once()

    def test_explicit_daily_cap_still_applies_when_configured(self):
        monitor.poll({**CONFIG, "daily_tasks": 4}, "offline-token")
        self.build.assert_not_called()


class AutomaticPublicationTests(unittest.TestCase):
    def test_metadata_changes_preserve_snapshot_but_content_changes_withhold_publication(self):
        with patch.object(approval.issues, "_get_issue", return_value=ISSUE):
            content, snapshot = approval.approved_issue(CONFIG, 1402, "offline-token")
        self.assertEqual(content["body"], ISSUE["body"])
        self.assertNotIn("label_event", snapshot)
        with patch.object(
            approval.issues,
            "_get_issue",
            return_value={**ISSUE, "labels": [], "assignees": [{"login": "factory-bot"}]},
        ):
            self.assertEqual(
                approval.approved_issue(CONFIG, 1402, "offline-token", snapshot)[1], snapshot
            )
        with (
            patch.object(
                approval.issues, "_get_issue", return_value={**ISSUE, "body": "New scope"}
            ),
            self.assertRaisesRegex(RuntimeError, "specification changed"),
        ):
            approval.approved_issue(CONFIG, 1402, "offline-token", snapshot)

    def test_issue_can_publish_without_a_label_after_claiming(self):
        claimed = {**ISSUE, "assignees": [{"login": "factory-bot"}]}
        _, snapshot = policy.issue_snapshot(CONFIG, ISSUE)
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(run, "github", return_value=claimed),
            patch.object(approval.issues, "_get_issue", return_value=claimed),
            patch.object(run.issues, "_push_branch") as push,
            patch.object(run.issues, "_open_pull_request", return_value={"html_url": "draft-pr"}),
        ):
            result = run.publish(
                {**CONFIG, "issue_approval": snapshot},
                "issue-1402",
                Path(temp),
                "factory/issue-1402",
                Path(temp),
                ISSUE["title"],
                "offline-token",
                issue=1402,
            )
        self.assertEqual(result, "draft-pr")
        push.assert_called_once()

    def test_closed_issue_or_changed_owner_still_blocks_publication(self):
        _, snapshot = policy.issue_snapshot(CONFIG, ISSUE)
        for current in (
            {**ISSUE, "state": "closed", "assignees": [{"login": "factory-bot"}]},
            {**ISSUE, "assignees": [{"login": "someone-else"}]},
        ):
            with (
                patch.object(run, "github", return_value=current),
                patch.object(run.issues, "_push_branch") as push,
                self.assertRaisesRegex(RuntimeError, "publication withheld"),
            ):
                run.publish(
                    {**CONFIG, "issue_approval": snapshot},
                    "issue-1402",
                    Path("/unused"),
                    "factory/issue-1402",
                    Path("/unused"),
                    ISSUE["title"],
                    "offline-token",
                    issue=1402,
                )
            push.assert_not_called()
