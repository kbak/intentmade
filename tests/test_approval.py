"""Approval history and immutable specification checks; all GitHub calls are mocked."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import approval
import monitor
import run

CONFIG = {
    "repository": "example/repo",
    "issue_label": "factory:approved",
    "assignee": "factory-bot",
    "branch": "main",
}
ISSUE = {
    "number": 42,
    "title": "An earlier REST snapshot",
    "body": "Stale REST body",
    "state": "open",
    "assignees": [],
    "labels": [{"name": "factory:approved"}],
}
EVENT = {
    "id": 100,
    "event": "labeled",
    "label": {"name": "factory:approved"},
    "created_at": "2026-09-08T01:00:10Z",
}
CONTENT = {
    "title": "Approved title",
    "body": "Approved specification",
    "lastEditedAt": None,
    "timelineItems": {"nodes": []},
}


class ApprovalHistoryTests(unittest.TestCase):
    def capture(self, content=None, events=None, expected=None):
        with (
            patch.object(approval.issues, "_github_paginate", return_value=events or [EVENT]),
            patch.object(
                approval,
                "github",
                return_value={"data": {"repository": {"issue": content or CONTENT}}},
            ),
        ):
            return approval.approved_issue(CONFIG, 42, "dummy-token", expected)

    def test_unedited_issue_has_content_and_label_bound_snapshot(self):
        content, snapshot = self.capture()
        self.assertEqual(content, {"title": CONTENT["title"], "body": CONTENT["body"]})
        self.assertEqual(snapshot["label_event"], "100")
        self.assertEqual(len(snapshot["content_sha256"]), 64)
        self.assertEqual(self.capture(expected=snapshot), (content, snapshot))

    def test_edits_before_approval_are_allowed(self):
        content = {
            **CONTENT,
            "lastEditedAt": "2026-09-08T01:00:09Z",
            "timelineItems": {"nodes": [{"createdAt": "2026-09-08T01:00:08Z"}]},
        }
        self.assertEqual(self.capture(content)[0]["body"], CONTENT["body"])

    def test_title_and_body_edits_after_or_during_approval_second_fail_closed(self):
        for edited_at in ("2026-09-08T01:00:10Z", "2026-09-08T01:00:11Z"):
            for change in (
                {"lastEditedAt": edited_at},
                {"timelineItems": {"nodes": [{"createdAt": edited_at}]}},
            ):
                # [utest~im-approval-ApprovalHistoryTests-title_and_body_edits_after_or_during_approval_second_fail_closed~1->req~im-approval-snapshot~1]
                with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "reapply"):
                    self.capture({**CONTENT, **change})

    def test_reapplying_label_after_edit_authorizes_new_specification(self):
        events = [EVENT, {**EVENT, "id": 101, "created_at": "2026-09-08T01:00:12Z"}]
        _, snapshot = self.capture({**CONTENT, "lastEditedAt": "2026-09-08T01:00:11Z"}, events)
        self.assertEqual(snapshot["label_event"], "101")

    def test_metadata_updates_do_not_invalidate_specification(self):
        original = self.capture()
        self.assertEqual(self.capture({**CONTENT, "updatedAt": "2026-09-08T02:00:00Z"}), original)

    def test_changed_content_or_replaced_label_cannot_publish_old_task(self):
        _, snapshot = self.capture()
        for change in ({"title": "Substituted title"}, {"body": "Substituted body"}):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "withheld"):
                self.capture({**CONTENT, **change}, expected=snapshot)
        with self.assertRaisesRegex(RuntimeError, "withheld"):
            self.capture(events=[{**EVENT, "id": 101}], expected=snapshot)

    def test_missing_or_partial_history_fails_closed(self):
        missing_edit = copy.deepcopy(CONTENT)
        del missing_edit["lastEditedAt"]
        for content in (
            missing_edit,
            {**CONTENT, "timelineItems": {"nodes": None}},
            {**CONTENT, "timelineItems": {"nodes": [None]}},
            {**CONTENT, "lastEditedAt": "not a timestamp"},
        ):
            # [utest~im-approval-ApprovalHistoryTests-missing_or_partial_history_fails_closed~1->req~im-approval-snapshot~1]
            with self.subTest(content=content), self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.capture(content)
        with (
            patch.object(approval.issues, "_github_paginate", return_value=[EVENT]),
            patch.object(approval, "github", return_value={"errors": [{"message": "denied"}]}),
            self.assertRaisesRegex(RuntimeError, "unavailable"),
        ):
            approval.approved_issue(CONFIG, 42, "dummy-token")
        with (
            patch.object(approval.issues, "_github_paginate", return_value=[]),
            self.assertRaisesRegex(RuntimeError, "No approval-label"),
        ):
            approval.approved_issue(CONFIG, 42, "dummy-token")


class ApprovalWorkflowTests(unittest.TestCase):
    def test_build_uses_verified_content_instead_of_earlier_rest_response(self):
        snapshot = {"label_event": "100", "content_sha256": "fixture-hash"}
        with (
            patch.object(monitor.issues, "_get_issue", return_value=ISSUE),
            patch.object(monitor.issues, "_github_paginate", return_value=[]),
            patch.object(approval, "approved_issue", return_value=(CONTENT, snapshot)),
            patch.object(
                monitor,
                "github",
                side_effect=[{**ISSUE, "assignees": [{"login": "factory-bot"}]}, {"sha": "base"}],
            ),
            patch.object(monitor, "build") as build,
        ):
            monitor.implement_issue(CONFIG, ISSUE, "dummy-token")
        self.assertEqual(build.call_args.args[0]["issue_approval"], snapshot)
        self.assertTrue(
            build.call_args.args[2].startswith("Approved title\n\nApproved specification")
        )
        self.assertNotIn("Stale REST body", build.call_args.args[2])

    def test_invalid_approval_never_claims_issue_or_builds(self):
        with (
            patch.object(monitor.issues, "_get_issue", return_value=ISSUE),
            patch.object(approval, "approved_issue", side_effect=RuntimeError("reapply label")),
            patch.object(monitor, "github") as api,
            patch.object(monitor, "build") as build,
            self.assertRaisesRegex(RuntimeError, "reapply"),
        ):
            monitor.implement_issue(CONFIG, ISSUE, "dummy-token")
        api.assert_not_called()
        build.assert_not_called()

    def test_publication_requires_unchanged_snapshot_before_any_push(self):
        for config in (CONFIG, {**CONFIG, "issue_approval": {"label_event": "100"}}):
            # [utest~im-approval-ApprovalWorkflowTests-publication_requires_unchanged_snapshot_before_any_push~1->req~im-approval-snapshot~1]
            with (
                patch.object(
                    run, "github", return_value={**ISSUE, "assignees": [{"login": "factory-bot"}]}
                ),
                patch.object(
                    approval, "approved_issue", side_effect=RuntimeError("approval changed")
                ),
                patch.object(run.issues, "_push_branch") as push,
                self.assertRaises(RuntimeError),
            ):
                run.publish(
                    config,
                    "task",
                    Path("/unused"),
                    "factory/task",
                    Path("/unused"),
                    "spec",
                    "dummy-token",
                    issue=42,
                )
            push.assert_not_called()

    def test_unchanged_snapshot_allows_draft_publication(self):
        snapshot = {"label_event": "100"}
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(
                run, "github", return_value={**ISSUE, "assignees": [{"login": "factory-bot"}]}
            ),
            patch.object(approval, "approved_issue", return_value=(CONTENT, snapshot)) as verify,
            patch.object(run.issues, "_push_branch") as push,
            patch.object(
                run.issues,
                "_open_pull_request",
                return_value={"html_url": "https://example.test/pr/1"},
            ),
        ):
            config = {**CONFIG, "issue_approval": snapshot}
            run.publish(
                config,
                "task",
                Path(temp),
                "factory/task",
                Path(temp),
                "spec",
                "dummy-token",
                issue=42,
            )
        verify.assert_called_once_with(config, 42, "dummy-token", expected=snapshot)
        push.assert_called_once()
