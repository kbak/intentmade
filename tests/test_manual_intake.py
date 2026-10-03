"""Explicit intake keeps repository labels from authorizing operator resources."""

import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import approval
import common
import deployment
import monitor
import policy
import run

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "main",
    "assignee": "factory-bot",
    "max_tasks_per_poll": 2,
    "issue_label": "factory:approved",
}
ISSUE = {
    "number": 42,
    "title": "Implement feature",
    "body": "Reviewed specification",
    "state": "open",
    "labels": [],
    "assignees": [],
}


def submission():
    return {**policy.issue_snapshot(CONFIG, ISSUE)[1], "authorization": "operator"}


class ManualIntakeTests(unittest.TestCase):
    def test_default_and_live_registration_control_intake(self):
        # [utest~im-manual-intake-policy~1->req~im-issue-intake~1]
        self.assertEqual(deployment.issue_intake(CONFIG), "manual")
        for invalid in (None, True, "auto", [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                deployment.workflow_settings({}, {"issue_intake": invalid})
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"FACTORY_ROOT": temp}):
            root = Path(temp) / "config"
            (root / "repositories").mkdir(parents=True)
            (root / "defaults.json").write_text("{}")
            registration = root / "repositories/example.json"
            captured = {**CONFIG, "issue_intake": "automatic"}
            self.assertEqual(deployment.current_issue_intake(captured), "manual")
            registration.write_text(json.dumps({"repository": CONFIG["repository"]}))
            self.assertEqual(deployment.current_issue_intake(captured), "manual")
            registration.write_text(
                json.dumps({"repository": CONFIG["repository"], "issue_intake": "automatic"})
            )
            self.assertEqual(deployment.current_issue_intake(captured), "automatic")
            self.assertEqual(
                deployment.current_issue_intake({**captured, "repository": "other/repo"}), "manual"
            )

    def test_manual_poll_never_discovers_or_triages_issues_but_keeps_pr_followup(self):
        # [utest~im-manual-intake-poll~1->req~im-issue-intake~1]
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(monitor, "DATA", Path(temp)),
            patch.object(deployment, "current_issue_intake", return_value="manual"),
            patch.object(monitor.issues, "_kv_get", return_value={"done": {}, "triaged": {}}),
            patch.object(monitor.issues, "_kv_set"),
            patch.object(monitor.issues, "_list_labeled_issues") as labeled,
            patch.object(monitor.issues, "_github_paginate") as listing,
            patch.object(monitor, "waiting_replies", return_value=[]) as replies,
            patch.object(monitor.reviews, "_list_open_prs", return_value=[]) as prs,
            patch.object(monitor.followup, "adopt_reports") as followup,
            patch.object(monitor, "worker") as worker,
            patch.object(monitor, "implement_issue") as build,
        ):
            monitor.poll({**CONFIG, "issue_intake": "automatic"}, "offline-token")
        labeled.assert_not_called()
        listing.assert_not_called()
        worker.assert_not_called()
        build.assert_not_called()
        prs.assert_called_once()
        replies.assert_called_once()
        followup.assert_called_once()

    def test_submission_snapshot_is_required_and_edits_block_before_claim(self):
        # [utest~im-manual-intake-snapshot~1->req~im-approval-snapshot~1]
        with (
            patch.object(approval.issues, "_get_issue", return_value=ISSUE) as fetch,
            patch.object(approval.issues, "_github_paginate") as history,
            patch.object(monitor, "github") as github,
            patch.object(monitor, "build") as build,
        ):
            with self.assertRaisesRegex(RuntimeError, "submission snapshot required"):
                monitor.implement_issue(CONFIG, ISSUE, "offline-token")
            self.assertEqual(
                approval.approved_issue(CONFIG, 42, "offline-token", submission())[1], submission()
            )
            for field in ("title", "body"):
                fetch.return_value = {**ISSUE, field: "Edited after queuing"}
                with self.assertRaisesRegex(RuntimeError, "specification changed"):
                    monitor.implement_issue(
                        CONFIG,
                        ISSUE,
                        "offline-token",
                        resume={"snapshot": submission(), "answer": "Run"},
                    )
            history.assert_not_called()
            github.assert_not_called()
            build.assert_not_called()

    def test_explicit_submission_ignores_labels_and_preserves_snapshot_during_build(self):
        # [utest~im-manual-intake-build~1->req~im-issue-intake~1]
        with (
            patch.object(monitor.issues, "_get_issue", return_value=ISSUE),
            patch.object(monitor.issues, "_github_paginate", return_value=[]),
            patch.object(
                monitor,
                "github",
                side_effect=[{**ISSUE, "assignees": [{"login": "factory-bot"}]}, {"sha": "base"}],
            ),
            patch.object(monitor, "build", return_value={"status": "PASSED"}) as build,
        ):
            monitor.implement_issue(
                {**CONFIG, "issue_intake": "automatic"},
                ISSUE,
                "offline-token",
                resume={"snapshot": submission(), "answer": "Run"},
            )
        self.assertEqual(build.call_args.args[0]["issue_approval"], submission())
        self.assertEqual(build.call_args.args[0]["issue_intake"], "manual")
        self.assertIn(ISSUE["body"], build.call_args.args[2])

    def test_publication_rejects_changed_manual_specification_before_push(self):
        # [utest~im-manual-intake-publication~1->req~im-approval-snapshot~1]
        owned = {**ISSUE, "assignees": [{"login": "factory-bot"}]}
        with (
            patch.object(run, "github", return_value=owned),
            patch.object(
                approval.issues, "_get_issue", return_value={**owned, "body": "New request"}
            ),
            patch.object(run.issues, "_push_branch") as push,
            self.assertRaisesRegex(RuntimeError, "specification changed"),
        ):
            run.publish(
                {**CONFIG, "issue_approval": submission()},
                "issue-42",
                Path("/unused"),
                "branch",
                Path("/unused"),
                "spec",
                "offline-token",
                issue=42,
            )
        push.assert_not_called()

    def test_resubmission_reuses_only_this_factory_retained_claim(self):
        # [utest~im-manual-intake-resubmission~1->req~im-issue-ownership~1]
        owned = {**ISSUE, "assignees": [{"login": "factory-bot"}]}
        record = {
            "assignee": "factory-bot",
            "status": "NEEDS_INPUT",
            "snapshot": {"old": "specification"},
        }
        resume = {"snapshot": submission(), "submitted": True}
        with patch.object(monitor.reporting, "read_report", return_value=record):
            self.assertTrue(monitor.issue_ready(CONFIG, owned, resume))
            self.assertFalse(monitor.issue_ready(CONFIG, owned, {**resume, "submitted": False}))
            self.assertFalse(
                monitor.issue_ready(
                    CONFIG, {**owned, "assignees": [{"login": "someone-else"}]}, resume
                )
            )
            self.assertFalse(monitor.issue_ready(CONFIG, {**owned, "body": "Later edit"}, resume))
        with patch.object(monitor.reporting, "read_report", return_value=None):
            self.assertFalse(monitor.issue_ready(CONFIG, owned, resume))


class SubmissionCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "manual_configure", common.ROOT / "configure.py"
        )
        cls.configure = importlib.util.module_from_spec(spec)
        with patch.object(Path, "read_text", return_value="offline-test-key"):
            spec.loader.exec_module(cls.configure)

    def test_submission_freezes_content_in_dispatched_job_without_github_writes(self):
        # [utest~im-manual-intake-command~1->req~im-issue-intake~1]
        with (
            patch.object(self.configure, "projects", return_value={"example": CONFIG}),
            patch.object(self.configure, "records", return_value=[]),
            patch.object(self.configure, "token", return_value="offline-token"),
            patch.object(self.configure.issues, "_get_issue", return_value=ISSUE),
            patch.object(self.configure, "github") as github,
            patch.object(common, "lock", return_value=nullcontext()),
            patch.object(monitor.reporting, "read_report", return_value=None),
            patch.object(self.configure, "files", side_effect=lambda job: job) as files,
            patch.object(self.configure, "install", return_value={"id": "automation"}),
            patch.object(self.configure, "api", return_value={"id": "run"}) as api,
        ):
            self.configure.retry_issue("example", 42, initial=True)
        job = files.call_args.args[0]
        self.assertEqual(job["resume"]["snapshot"], submission())
        self.assertEqual(job["config"]["issue_intake"], "manual")
        self.assertEqual(job["retry_issue"], 42)
        api.assert_called_once_with("POST", "/api/automation/v1/automation/dispatch")
        github.assert_not_called()

    def test_manual_configuration_never_creates_labels(self):
        config = {**CONFIG, "enabled": True, "schedule": "*/10 * * * *", "timezone": "UTC"}
        with (
            patch.object(self.configure, "projects", return_value={"example": config}),
            patch.object(self.configure, "records", return_value=[]),
            patch.object(self.configure, "token", return_value="offline-token"),
            patch.object(self.configure, "github") as github,
            patch.object(self.configure, "files", return_value={}),
            patch.object(self.configure, "install", return_value={"id": "automation"}) as install,
            patch.object(self.configure, "api", return_value={"profile": {"id": "factory-codex"}}),
            patch.object(monitor.reporting, "write_report"),
        ):
            self.configure.configure()
        github.assert_not_called()
        self.assertEqual(install.call_count, 2)
        self.assertTrue(all(call.args[0]["enabled"] for call in install.call_args_list))
