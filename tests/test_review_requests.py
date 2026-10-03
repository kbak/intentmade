"""Review request scope, current-head completion, and scheduler deduplication."""

import copy
import json
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import monitor
import review_requests

CONFIG = {
    "project": "example",
    "repository": "org/repo",
    "issue_intake": "automatic",
    "issue_label": None,
    "max_tasks_per_poll": 2,
    "daily_tasks": None,
    "required_checks": ["unit"],
    "accepted_check_results": ["success", "neutral", "skipped"],
}
PR = {
    "number": 42,
    "title": "External change",
    "state": "open",
    "draft": False,
    "head": {"sha": "a" * 40},
    "base": {"ref": "main", "sha": "c" * 40},
    "user": {"login": "author", "type": "User"},
    "requested_reviewers": [],
    "requested_teams": [{"slug": "engineers"}],
}
POSTED = {
    "id": 101,
    "state": "CHANGES_REQUESTED",
    "commit_id": PR["head"]["sha"],
    "submitted_at": "2026-09-10T07:00:00Z",
    "user": {"login": "operator", "type": "User"},
}


class ImmutableComparisonTests(unittest.TestCase):
    def test_same_size_head_swap_cannot_change_captured_files(self):
        captured = {**copy.deepcopy(PR), "changed_files": 1}
        live = copy.deepcopy(captured)
        files_a = [{"filename": "a.py", "status": "added", "patch": "+original"}]
        files_b = [{"filename": "b.py", "status": "added", "patch": "+replacement"}]
        live["head"]["sha"] = "b" * 40

        def github(token, method, path):
            # The old mutable endpoint would return B's same-sized inventory.
            if path.endswith("/files"):
                return files_b
            self.assertEqual(path, f"/repos/org/repo/compare/{'c' * 40}...{'a' * 40}")
            live["head"]["sha"] = "a" * 40  # Back to A before final recheck.
            return {"base_commit": {"sha": "c" * 40}, "files": files_a}

        with patch.object(review_requests, "github", side_effect=github):
            files = review_requests.comparison_files(CONFIG, captured, "fixture")
        # [utest~im-review_requests-ImmutableComparisonTests-same_size_head_swap_cannot_change_captured_files~1->req~im-immutable-pr-comparison~1]
        self.assertEqual(
            review_requests.snapshot(CONFIG, captured), review_requests.snapshot(CONFIG, live)
        )
        self.assertEqual(files, files_a)

    def test_wrong_base_or_incomplete_inventory_fails_closed(self):
        files = [{"filename": "a.py", "status": "added"}]
        for response in (
            {"base_commit": {"sha": "d" * 40}, "files": files},
            {"base_commit": {"sha": "c" * 40}, "files": []},
            {"base_commit": {"sha": "c" * 40}},
        ):
            with patch.object(review_requests, "github", return_value=response):
                # [utest~im-review_requests-ImmutableComparisonTests-wrong_base_or_incomplete_inventory_fails_closed~1->req~im-immutable-pr-comparison~1]
                with self.assertRaises(ValueError):
                    review_requests.comparison_files(CONFIG, {**PR, "changed_files": 1}, "fixture")
        # The compare API's 300-file cap must never lead to a partial approval.
        with patch.object(
            review_requests,
            "github",
            return_value={
                "base_commit": {"sha": "c" * 40},
                "files": files * 300,
            },
        ):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                review_requests.comparison_files(CONFIG, {**PR, "changed_files": 301}, "fixture")


class RequestScopeTests(unittest.TestCase):
    def test_direct_request_matches_case_insensitively_without_team_lookup(self):
        with patch.object(review_requests, "github", return_value={"login": "operator"}) as api:
            requests = review_requests.ReviewRequests(CONFIG, "secret")
            self.assertTrue(
                requests.matches({**PR, "requested_reviewers": [{"login": "Operator"}]})
            )
            self.assertEqual(api.call_count, 1)

    def test_active_team_membership_is_cached_per_scan(self):
        with patch.object(
            review_requests, "github", side_effect=[{"login": "operator"}, {"state": "active"}]
        ) as api:
            requests = review_requests.ReviewRequests(CONFIG, "secret")
            self.assertTrue(requests.matches(PR))
            self.assertTrue(requests.matches(PR))
            api.assert_called_with(
                "secret", "GET", "/orgs/org/teams/engineers/memberships/operator"
            )
            self.assertEqual(api.call_count, 2)

    def test_pending_absent_and_unreadable_membership_does_not_authorize_review(self):
        for result in (
            {"state": "pending"},
            urllib.error.HTTPError("url", 404, "missing", {}, None),
            urllib.error.HTTPError("url", 403, "forbidden", {}, None),
        ):
            with (
                self.subTest(result=str(result)),
                patch.object(
                    review_requests, "github", side_effect=[{"login": "operator"}, result]
                ),
            ):
                requests = review_requests.ReviewRequests(CONFIG, "secret")
                if isinstance(result, urllib.error.HTTPError) and result.code == 403:
                    with self.assertRaises(urllib.error.HTTPError):
                        requests.matches(PR)
                else:
                    self.assertFalse(requests.matches(PR))

    def test_closed_draft_and_unrequested_prs_do_not_query_memberships(self):
        for change in ({"state": "closed"}, {"draft": True}, {"requested_teams": []}):
            with patch.object(review_requests, "github") as api:
                self.assertFalse(
                    review_requests.ReviewRequests(CONFIG, "secret").matches({**PR, **change})
                )
                api.assert_not_called()

    def test_own_pr_is_not_reviewed(self):
        with patch.object(review_requests, "github", return_value={"login": "AUTHOR"}) as api:
            self.assertFalse(review_requests.ReviewRequests(CONFIG, "secret").matches(PR))
            self.assertEqual(api.call_count, 1)

    def test_only_submitted_human_reviews_of_current_commit_count(self):
        review = {
            "commit_id": "a" * 40,
            "state": "APPROVED",
            "user": {"login": "human", "type": "User"},
        }
        for change, expected in (
            ({}, True),
            ({"state": "CHANGES_REQUESTED"}, True),
            ({"state": "COMMENTED"}, True),
            ({"state": "PENDING"}, False),
            ({"body": "<!-- factory-review:v1:org/repo:42:head -->"}, False),
            ({"state": "DISMISSED"}, False),
            ({"commit_id": "b" * 40}, False),
            ({"user": {"login": "bot", "type": "Bot"}}, False),
            ({"user": PR["user"]}, False),
        ):
            with (
                self.subTest(change=change),
                patch.object(
                    review_requests.issues, "_github_paginate", return_value=[{**review, **change}]
                ),
            ):
                self.assertEqual(
                    review_requests.ReviewRequests(CONFIG, "secret").human_reviewed(PR), expected
                )


class RequestedReviewSchedulerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = {"done": {}, "triaged": {}}
        self.pr = copy.deepcopy(PR)
        self.review = Mock(return_value=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, name, value in (
            (monitor, "DATA", self.root),
            (review_requests, "DATA", self.root),
            (monitor.reporting, "DATA", self.root),
            (monitor, "job_id", lambda: "run"),
            (review_requests, "job_id", lambda: "run"),
            (monitor, "review_pr", self.review),
            (monitor, "resume_reply", lambda *args: None),
            (monitor, "waiting_replies", lambda *args: []),
            (monitor.followup, "adopt_reports", lambda *args: None),
            (monitor.followup, "read", lambda *args: None),
            (monitor.issues, "_kv_get", lambda *args: copy.deepcopy(self.state)),
            (monitor.issues, "_kv_set", self.save),
            (monitor.issues, "_github_paginate", lambda *args: []),
            (monitor.reviews, "_list_open_prs", lambda *args: [self.pr]),
            (monitor.reviews, "_get_pr", lambda *args: copy.deepcopy(self.pr)),
            (
                review_requests,
                "github",
                lambda token, method, path: (
                    {"login": "operator"} if path == "/user" else {"state": "active"}
                ),
            ),
        ):
            self.stack.enter_context(patch.object(target, name, value))
        self.eligible = self.stack.enter_context(
            patch.object(monitor, "pr_eligible", return_value=True)
        )

    def save(self, key, value):
        self.state = copy.deepcopy(value)

    def test_ready_team_request_is_reviewed_once_even_after_kv_pruning(self):
        monitor.poll(CONFIG, "secret")
        self.review.assert_called_once()
        record = json.loads(review_requests.receipt_path(CONFIG, PR).read_text())
        self.assertEqual(record["status"], "REVIEWED")
        self.state["done"] = {}
        monitor.poll(CONFIG, "secret")
        self.review.assert_called_once()
        self.pr["head"]["sha"] = "b" * 40
        monitor.poll(CONFIG, "secret")
        self.assertEqual(self.review.call_count, 2)

    def test_manual_completed_receipt_suppresses_automatic_review(self):
        review_requests.remember(CONFIG, PR, "REVIEWED")
        monitor.poll(CONFIG, "secret")
        self.review.assert_not_called()

    def test_base_change_with_same_head_requires_another_review(self):
        monitor.poll(CONFIG, "secret")
        self.pr["base"]["ref"] = "release"
        monitor.poll(CONFIG, "secret")
        self.assertEqual(self.review.call_count, 2)
        self.pr["base"]["sha"] = "d" * 40
        monitor.poll(CONFIG, "secret")
        self.assertEqual(self.review.call_count, 3)
        monitor.poll(CONFIG, "secret")
        self.assertEqual(self.review.call_count, 3)

    def test_failed_receipt_or_reply_cannot_cross_base_changes(self):
        reply = self.failed_reply(snapshot=review_requests.snapshot(CONFIG, PR))
        for base in ({"ref": "release", "sha": "c" * 40}, {"ref": "main", "sha": "d" * 40}):
            changed = {**PR, "base": base}
            self.assertFalse(review_requests.attempted(CONFIG, changed))
            self.assertFalse(review_requests.retryable(CONFIG, changed, reply))

    def test_ci_is_rechecked_on_next_poll_without_pr_timestamp_change(self):
        self.eligible.return_value = False
        monitor.poll(CONFIG, "secret")
        self.review.assert_not_called()
        self.assertFalse(review_requests.attempted(CONFIG, PR))
        self.eligible.return_value = True
        monitor.poll(CONFIG, "secret")
        self.review.assert_called_once()

    def follow_up(self):
        review_requests.remember(CONFIG, PR, "REVIEWED", github_review=POSTED)
        self.pr.update(requested_teams=[], head={"sha": "b" * 40})
        return patch.object(
            monitor.issues,
            "_github_paginate",
            side_effect=lambda credential, path, *args: (
                [POSTED] if path.endswith("/reviews") else []
            ),
        )

    def test_unrequested_follow_up_waits_for_ci_and_reviews_new_commit_once(self):
        with self.follow_up():
            self.eligible.return_value = False
            monitor.poll(CONFIG, "secret")
            self.review.assert_not_called()
            self.assertFalse(review_requests.attempted(CONFIG, self.pr))
            self.eligible.return_value = True
            monitor.poll(CONFIG, "secret")
            self.review.assert_called_once()
            self.assertEqual(self.review.call_args.args[1]["head"]["sha"], "b" * 40)
            self.state["done"] = {}
            monitor.poll(CONFIG, "secret")
            self.review.assert_called_once()

    def test_follow_up_rechecks_outstanding_verdict_before_execution(self):
        with (
            self.follow_up(),
            patch.object(
                review_requests.ReviewRequests, "follows_up", side_effect=[True, True, False]
            ),
        ):
            monitor.poll(CONFIG, "secret")
        self.review.assert_not_called()
        self.assertFalse(review_requests.attempted(CONFIG, self.pr))

    def test_follow_up_still_defers_to_current_commit_human_review(self):
        with (
            self.follow_up(),
            patch.object(review_requests.ReviewRequests, "human_reviewed", return_value=True),
        ):
            monitor.poll(CONFIG, "secret")
        self.review.assert_not_called()

    def test_failed_follow_up_is_held_for_explicit_retry(self):
        with self.follow_up():
            self.review.side_effect = RuntimeError("Review failed")
            with self.assertRaisesRegex(RuntimeError, "Review failed"):
                monitor.poll(CONFIG, "secret")
            self.state["done"] = {}
            monitor.poll(CONFIG, "secret")
        self.review.assert_called_once()

    def test_request_or_head_change_before_execution_defers_review_without_receipt(self):
        for change in (
            {"requested_teams": []},
            {"head": {"sha": "b" * 40}},
            {"base": {"ref": "release", "sha": "c" * 40}},
            {"base": {"ref": "main", "sha": "d" * 40}},
            {"draft": True},
            {"state": "closed"},
        ):
            with (
                self.subTest(change=change),
                patch.object(monitor.reviews, "_get_pr", side_effect=[PR, {**PR, **change}]),
            ):
                monitor.poll(CONFIG, "secret")
                self.review.assert_not_called()
                self.assertFalse(review_requests.attempted(CONFIG, PR))

    def test_human_review_before_execution_prevents_duplicate(self):
        with patch.object(
            review_requests.ReviewRequests, "human_reviewed", side_effect=[False, True]
        ):
            monitor.poll(CONFIG, "secret")
        self.review.assert_not_called()
        self.assertFalse(review_requests.attempted(CONFIG, PR))

    def test_busy_or_stale_review_can_be_retried(self):
        for result in (BlockingIOError("busy"), False):
            with self.subTest(result=result):
                self.review.side_effect = [result, True]
                monitor.poll(CONFIG, "secret")
                self.assertFalse(review_requests.attempted(CONFIG, PR))
                monitor.poll(CONFIG, "secret")
                self.assertTrue(review_requests.attempted(CONFIG, PR))
                review_requests.receipt_path(CONFIG, PR).unlink()
                self.state["done"] = {}

    def test_failed_attempt_is_reported_without_claiming_review_completion_or_looping(self):
        self.review.side_effect = RuntimeError("Security reviewer unavailable")
        with self.assertRaisesRegex(RuntimeError, "Security reviewer unavailable"):
            monitor.poll(CONFIG, "secret")
        self.assertEqual(
            json.loads(review_requests.receipt_path(CONFIG, PR).read_text())["status"], "FAILED"
        )
        self.state["done"] = {}
        monitor.poll(CONFIG, "secret")
        self.review.assert_called_once()

    def test_rejected_login_waits_for_explicit_retry_without_repeated_scans(self):
        self.review.side_effect = monitor.NeedsInput("Reconnect Codex")
        monitor.poll(CONFIG, "secret")
        self.assertEqual(review_requests.read(CONFIG, PR)["status"], "NEEDS_INPUT")
        self.state["done"] = {}
        monitor.poll(CONFIG, "secret")
        self.review.assert_called_once()
        reply = {
            "id": "reconnected",
            "answer": "retry",
            "snapshot": review_requests.snapshot(CONFIG, PR),
        }
        self.review.side_effect = None
        with patch.object(monitor, "resume_reply", return_value=reply):
            monitor.poll(CONFIG, "secret", replies_only=True)
            monitor.poll(CONFIG, "secret", replies_only=True)
        self.assertEqual(self.review.call_count, 2)
        self.assertEqual(review_requests.read(CONFIG, PR)["status"], "REVIEWED")

    def test_reply_only_scan_and_published_pr_do_not_start_review(self):
        monitor.poll(CONFIG, "secret", replies_only=True)
        with (
            patch.object(monitor.followup, "read", return_value={"owned": True}),
            patch.object(monitor.followup, "plan", return_value=None),
        ):
            monitor.poll(CONFIG, "secret")
        self.review.assert_not_called()

    def failed_reply(self, status="FAILED", snapshot=None):
        review_requests.remember(CONFIG, self.pr, status)
        key = f"pr:{self.pr['number']}:{review_requests.revision(self.pr)}"
        self.state["done"][key] = "failed:previous-run"
        return {"id": "answer-1", "answer": "retry", "snapshot": snapshot}

    def test_explicit_retry_runs_once_in_reply_and_scheduled_scans(self):
        for replies_only, snapshot in (
            (True, None),  # Reports saved before PR snapshots were introduced.
            (False, review_requests.snapshot(CONFIG, PR)),
        ):
            with self.subTest(replies_only=replies_only):
                reply = self.failed_reply(snapshot=snapshot)
                self.review.reset_mock()
                with patch.object(monitor, "resume_reply", return_value=reply):
                    monitor.poll(CONFIG, "secret", replies_only=replies_only)
                    self.review.assert_called_once()
                    self.assertEqual(review_requests.read(CONFIG, PR)["answer_id"], "answer-1")
                    self.state["done"] = {}
                    monitor.poll(CONFIG, "secret", replies_only=replies_only)
                    self.review.assert_called_once()
                review_requests.receipt_path(CONFIG, PR).unlink()

    def test_failed_retry_consumes_answer_and_requires_another_explicit_reply(self):
        reply = self.failed_reply()
        self.review.side_effect = RuntimeError("Reviewer unavailable")
        with patch.object(monitor, "resume_reply", return_value=reply):
            with self.assertRaisesRegex(RuntimeError, "Reviewer unavailable"):
                monitor.poll(CONFIG, "secret", replies_only=True)
            self.state["done"] = {}
            monitor.poll(CONFIG, "secret", replies_only=True)
            self.review.assert_called_once()
            reply["id"] = "answer-2"
            self.review.side_effect = None
            monitor.poll(CONFIG, "secret", replies_only=True)
            self.assertEqual(self.review.call_count, 2)

    def test_retry_preserves_publication_failure_evidence_until_review_starts(self):
        reply = self.failed_reply("PUBLICATION_FAILED")

        def review(*args):
            record = review_requests.read(CONFIG, PR)
            self.assertEqual(record["status"], "PUBLICATION_FAILED")
            self.assertEqual(record["answer_id"], reply["id"])
            return True

        self.review.side_effect = review
        with patch.object(monitor, "resume_reply", return_value=reply):
            monitor.poll(CONFIG, "secret", replies_only=True)
        self.review.assert_called_once()

    def test_retry_does_not_waive_revision_scope_ci_or_human_review_checks(self):
        reply = self.failed_reply(snapshot=review_requests.snapshot(CONFIG, PR))
        with patch.object(monitor, "resume_reply", return_value=reply):
            self.eligible.return_value = False
            monitor.poll(CONFIG, "secret", replies_only=True)
            self.eligible.return_value = True
            with patch.object(review_requests.ReviewRequests, "human_reviewed", return_value=True):
                monitor.poll(CONFIG, "secret", replies_only=True)
            for change in (
                {"requested_teams": []},
                {"head": {"sha": "b" * 40}},
                {"base": {"ref": "release", "sha": "c" * 40}},
                {"base": {"ref": "main", "sha": "d" * 40}},
                {"draft": True},
                {"state": "closed"},
            ):
                with patch.object(monitor.reviews, "_get_pr", side_effect=[PR, {**PR, **change}]):
                    monitor.poll(CONFIG, "secret", replies_only=True)
            reply["snapshot"]["head"] = "b" * 40
            monitor.poll(CONFIG, "secret", replies_only=True)
        self.review.assert_not_called()
        self.assertNotIn("answer_id", review_requests.read(CONFIG, PR))

    def test_busy_retry_preserves_failure_receipt_and_unconsumed_report(self):
        reply = self.failed_reply()
        record = {"status": "FAILED", "snapshot": None, "updated_at": "before-reply"}
        monitor.reporting.write_report(CONFIG, "pr-42", record)
        before = review_requests.read(CONFIG, PR)

        def busy(*args):
            monitor.reporting.write_report(CONFIG, "pr-42", {"status": "FAILED"})
            raise BlockingIOError("busy")

        self.review.side_effect = busy
        with patch.object(monitor, "resume_reply", return_value=reply):
            monitor.poll(CONFIG, "secret", replies_only=True)
            self.assertEqual(review_requests.read(CONFIG, PR), before)
            self.assertEqual(monitor.reporting.read_report(CONFIG, "pr-42"), record)
            self.review.side_effect = None
            monitor.poll(CONFIG, "secret", replies_only=True)
            self.assertEqual(self.review.call_count, 2)

    def test_requested_review_runs_before_issue_with_shared_poll_budget(self):
        issue = {"number": 7, "title": "Issue", "body": "Build", "state": "open", "assignees": []}
        with (
            patch.object(
                monitor.issues,
                "_github_paginate",
                side_effect=lambda token, path, *args: [issue] if path.endswith("/issues") else [],
            ),
            patch.object(monitor.reporting, "read_report", return_value=None),
            patch.object(monitor, "implement_issue") as build,
        ):
            monitor.poll({**CONFIG, "max_tasks_per_poll": 1}, "secret")
        self.review.assert_called_once()
        build.assert_not_called()

    def test_receipts_are_scoped_to_repository(self):
        review_requests.remember(CONFIG, PR, "REVIEWED")
        self.assertFalse(review_requests.attempted({**CONFIG, "repository": "org/other"}, PR))

    def test_disappearing_pr_is_deferred_and_permission_failure_is_reported(self):
        for code in (404, 403):
            error = urllib.error.HTTPError("url", code, "unavailable", {}, None)
            with (
                self.subTest(code=code),
                patch.object(monitor.reviews, "_get_pr", side_effect=error),
            ):
                if code == 404:
                    monitor.poll(CONFIG, "secret")
                else:
                    with self.assertRaisesRegex(RuntimeError, "review discovery failed"):
                        monitor.poll(CONFIG, "secret")
                self.review.assert_not_called()
                self.assertFalse(review_requests.attempted(CONFIG, PR))


class FollowUpScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(review_requests, "DATA", Path(temporary.name)))
        self.stack.enter_context(patch.object(review_requests, "job_id", return_value="run"))
        self.stack.enter_context(
            patch.object(review_requests, "github", return_value={"login": "operator"})
        )
        self.reviews = self.stack.enter_context(
            patch.object(review_requests.issues, "_github_paginate", return_value=[POSTED])
        )
        self.pr = {**PR, "requested_teams": [], "head": {"sha": "b" * 40}}
        review_requests.remember(CONFIG, PR, "REVIEWED", github_review=POSTED)

    def eligible(self, pr=None, config=None):
        return review_requests.ReviewRequests(config or CONFIG, "secret").eligible(pr or self.pr)

    def test_own_published_changes_request_carries_over_to_a_new_commit(self):
        self.assertTrue(self.eligible())

    def test_changes_request_carries_over_to_retargeted_same_head(self):
        self.assertTrue(
            self.eligible(
                {**self.pr, "head": PR["head"], "base": {"ref": "release", "sha": "c" * 40}}
            )
        )

    def test_unrelated_pr_repository_author_and_unchanged_head_are_excluded(self):
        for change in (
            {"number": 43},
            {"draft": True},
            {"state": "closed"},
            {"head": PR["head"]},
            {"user": {"login": "OPERATOR"}},
        ):
            with self.subTest(change=change):
                self.assertFalse(self.eligible({**self.pr, **change}))
        self.assertFalse(self.eligible(config={**CONFIG, "repository": "org/other"}))

    def test_live_review_without_factory_receipt_does_not_establish_scope(self):
        review_requests.receipt_path(CONFIG, PR).unlink()
        self.assertFalse(self.eligible())
        self.reviews.assert_not_called()

    def test_failed_unpublished_or_approved_receipts_do_not_establish_scope(self):
        for status, posted in (
            ("FAILED", POSTED),
            ("PUBLICATION_FAILED", POSTED),
            ("REVIEWED", {}),
            ("REVIEWED", {**POSTED, "state": "APPROVED"}),
            ("REVIEWED", {**POSTED, "commit_id": "c" * 40}),
        ):
            with self.subTest(status=status, posted=posted):
                review_requests.remember(CONFIG, PR, status, github_review=posted)
                self.assertFalse(self.eligible())

    def test_dismissal_changed_account_and_missing_live_review_end_scope(self):
        for reviews in (
            [],
            [{**POSTED, "state": "DISMISSED"}],
            [{**POSTED, "user": {"login": "another-operator"}}],
            [{**POSTED, "commit_id": "c" * 40}],
        ):
            with self.subTest(reviews=reviews):
                self.reviews.return_value = reviews
                self.assertFalse(self.eligible())

    def test_later_approval_or_untracked_verdict_supersedes_old_factory_request(self):
        for state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            later = {**POSTED, "id": 102, "submitted_at": "2026-09-10T08:00:00Z", "state": state}
            # Do not rely on pagination order when deciding the latest verdict.
            self.reviews.return_value = [later, POSTED]
            with self.subTest(state=state):
                self.assertFalse(self.eligible())
        self.assertTrue(self.eligible({**self.pr, "requested_reviewers": [{"login": "operator"}]}))

    def test_comments_pending_reviews_and_other_reviewers_do_not_clear_our_verdict(self):
        for change in (
            {"state": "COMMENTED"},
            {"state": "PENDING", "submitted_at": None},
            {"state": "APPROVED", "user": {"login": "another-reviewer"}},
        ):
            later = {**POSTED, "id": 102, "submitted_at": "2026-09-10T08:00:00Z", **change}
            self.reviews.return_value = [POSTED, later]
            with self.subTest(change=change):
                self.assertTrue(self.eligible())


if __name__ == "__main__":
    unittest.main()
