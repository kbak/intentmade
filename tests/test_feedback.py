"""External feedback author scope, resolved threads, receipts and repair admission."""

import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import common
import feedback
import followup
import run
from test_followup import CONFIG, PR


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.config = {**CONFIG, "pr_feedback": True, "pr_feedback_attempts": 3}
        self.reviews = [
            {
                "id": 1,
                "state": "CHANGES_REQUESTED",
                "commit_id": "head",
                "user": {"login": "reviewer", "type": "User"},
                "body": "Handle the empty response",
                "submitted_at": "today",
            }
        ]
        self.comments = []
        self.discussion = []
        self.permission = "write"
        self.roots = set()

    def collect(self, record=None, retry=False):
        def paginate(_credential, path):
            if path.endswith("/reviews"):
                return self.reviews
            return self.comments if "/pulls/" in path else self.discussion

        with (
            patch.object(feedback.issues, "_github_paginate", side_effect=paginate),
            patch.object(feedback, "github", return_value={"permission": self.permission}),
            patch.object(feedback, "unresolved_roots", return_value=self.roots),
        ):
            return feedback.collect(self.config, PR, "credential", record or {}, retry=retry)

    def test_author_permission_bot_allowlist_and_own_generated_reviews(self):
        # [utest~im-feedback-FeedbackTests-author_permission_bot_allowlist_and_own_generated_reviews~1->req~im-feedback-authority~1]
        self.assertEqual(len(self.collect()), 1)
        self.permission = "read"
        self.assertEqual(self.collect(), [])
        self.reviews[0]["user"] = {"login": "review-bot[bot]", "type": "Bot"}
        self.assertEqual(self.collect(), [])
        self.config["pr_feedback_bots"] = ["review-bot[bot]"]
        self.assertEqual(len(self.collect()), 1)
        self.reviews[0]["body"] += "\n<!-- factory-review:v1:org/repo:42:head -->"
        self.assertEqual(self.collect(), [])

    def test_current_submitted_reviews_and_unresolved_roots_only(self):
        for state, commit in (
            ("APPROVED", "head"),
            ("DISMISSED", "head"),
            ("PENDING", "head"),
            ("CHANGES_REQUESTED", "old"),
        ):
            self.reviews[0].update(state=state, commit_id=commit)
            # [utest~im-feedback-FeedbackTests-current_submitted_reviews_and_unresolved_roots_only~1->req~im-feedback-authority~1]
            self.assertEqual(self.collect(), [])
        self.comments = [
            {**self.reviews[0], "id": 2, "path": "app.py", "line": 8},
            {**self.reviews[0], "id": 3, "in_reply_to_id": 2},
        ]
        self.roots = {2}
        self.assertEqual([r["id"] for r in self.collect()], ["inline:2", "inline:3"])
        self.roots = set()
        self.assertEqual(self.collect(), [])

    def test_later_approval_supersedes_review_body(self):
        self.reviews.append({**self.reviews[0], "id": 2, "state": "APPROVED"})
        # [utest~im-feedback-FeedbackTests-later_approval_supersedes_review_body~1->req~im-feedback-authority~1]
        self.assertEqual(self.collect(), [])

    def test_feedback_receipt_brackets_the_existing_build_pipeline(self):
        entries = self.collect()
        for failure in (False, True):
            with (
                tempfile.TemporaryDirectory() as temp,
                patch.object(common, "DATA", Path(temp)),
                patch.object(followup, "DATA", Path(temp)),
                patch.object(followup, "evidence", return_value=Path(temp)),
                patch.object(followup, "job_id", return_value="run"),
                patch.object(followup, "github", return_value=PR),
                patch.object(feedback, "collect", return_value=entries),
                patch.object(followup, "failure_context", return_value=""),
                patch.object(
                    followup, "git", return_value=SimpleNamespace(stdout="base", returncode=0)
                ),
                patch.object(run, "task_repository", return_value=(Path(temp), PR["head"]["ref"])),
            ):
                record = {
                    "number": 42,
                    "task": "task",
                    "head": "head",
                    "branch": PR["head"]["ref"],
                    "base_branch": "main",
                    "request": "Approved spec",
                }
                followup.save(self.config, record)
                action = {
                    "number": 42,
                    "head": "head",
                    "base": "base",
                    "branch": record["branch"],
                    "feedback": entries,
                    "failures": {},
                    "behind": False,
                    "title": None,
                    "key": "feedback-attempt",
                }

                def build(configs, task, request, credential, issue, publish, artifact, states):
                    self.assertEqual(
                        followup.read(self.config, 42)["feedback"]["review:1"]["status"], "STARTED"
                    )
                    self.assertIn("Approved spec", request)
                    self.assertIn("Handle the empty response", request)
                    self.assertEqual(configs[0]["repair_pr"], action)
                    if failure:
                        raise RuntimeError("Validation failed")
                    states["example"]["commit"] = "head"
                    return {"status": "PASSED", "repositories": states}

                with patch.object(run, "execute_build", side_effect=build) as pipeline:
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, "Validation failed"):
                            followup.maintain(self.config, PR, action, "credential")
                    else:
                        self.assertTrue(followup.maintain(self.config, PR, action, "credential"))
                    pipeline.assert_called_once()
                receipt = followup.read(self.config, 42)
                self.assertEqual(
                    receipt["feedback"]["review:1"]["status"], "FAILED" if failure else "COMPLETED"
                )
                self.assertEqual(len(receipt["feedback_runs"]), 1)

    def test_general_comments_need_explicit_mention_and_edits_get_new_digest(self):
        self.reviews = []
        self.discussion = [{"id": 4, "body": "Thanks!", "user": {"login": "reviewer"}}]
        # [utest~im-feedback-FeedbackTests-general_comments_need_explicit_mention_and_edits_get_new_digest~1->req~im-feedback-authority~1]
        self.assertEqual(self.collect(), [])
        self.discussion[0]["body"] = "@openhands please fix the empty result"
        record = {"head": "head"}
        entries = self.collect()
        feedback.remember(record, entries, "STARTED")
        self.assertTrue(feedback.pending(record))
        self.assertEqual(self.collect(record), [])
        self.assertEqual(self.collect(record, retry=True), entries)
        feedback.remember(record, entries, "COMPLETED")
        self.assertEqual(self.collect(record, retry=True), [])
        self.discussion[0]["body"] += " and preserve sorting"
        self.assertNotEqual(self.collect(record)[0]["digest"], entries[0]["digest"])

    def test_thread_resolution_paginates_and_does_not_treat_errors_as_empty(self):
        def page(nodes, more=False):
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": nodes,
                                "pageInfo": {"hasNextPage": more, "endCursor": "next"},
                            }
                        }
                    }
                }
            }

        def node(number, resolved=False, outdated=False):
            return {
                "isResolved": resolved,
                "isOutdated": outdated,
                "comments": {"nodes": [{"databaseId": number}]},
            }

        with patch.object(
            feedback,
            "github",
            side_effect=[
                page([node(1), node(2, True)], True),
                page([node(3), node(4, outdated=True)]),
            ],
        ) as api:
            self.assertEqual(feedback.unresolved_roots(self.config, 42, "credential"), {1, 3})
            self.assertEqual(api.call_args.kwargs["body"]["variables"]["cursor"], "next")
        with patch.object(feedback, "github", return_value={"errors": ["Unavailable"]}):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                feedback.unresolved_roots(self.config, 42, "credential")

    def test_green_ci_admits_feedback_once_and_interruption_requires_resume(self):
        entries = self.collect()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(followup, "DATA", Path(temp)),
            patch.object(followup, "resume_reply", return_value=None),
            patch.object(followup, "checks_for", return_value={"unit": "success"}),
            patch.object(followup, "github", return_value={"behind_by": 0}),
            patch.object(feedback, "collect", return_value=entries),
        ):
            record = {
                "number": 42,
                "head": "head",
                "branch": PR["head"]["ref"],
                "base_branch": "main",
                "status": "WATCHING",
            }
            followup.save(self.config, record)
            action = followup.plan(self.config, PR, "credential")
            # [utest~im-feedback-FeedbackTests-green_ci_admits_feedback_once_and_interruption_requires_resume~1->req~im-feedback-lifecycle~1]
            self.assertEqual(action["feedback"], entries)
            feedback.remember(record, entries, "STARTED")
            followup.save(self.config, record)
            self.assertIsNone(followup.plan(self.config, PR, "credential"))
            self.assertEqual(followup.read(self.config, 42)["status"], "NEEDS_INPUT")
            record = {
                **record,
                "feedback": {},
                "feedback_runs": [time.time()] * 3,
                "status": "WATCHING",
            }
            followup.save(self.config, record)
            self.assertIsNone(followup.plan(self.config, PR, "credential"))

    def test_feedback_change_before_publication_is_rejected(self):
        expected = {
            "number": 42,
            "head": "head",
            "base": "base",
            "branch": PR["head"]["ref"],
            "feedback": self.collect(),
        }
        changed = copy.deepcopy(expected["feedback"])
        changed[0]["digest"] = "edited"
        # [utest~im-feedback-FeedbackTests-feedback_change_before_publication_is_rejected~1->req~im-feedback-lifecycle~1]
        with (
            patch.object(followup, "github", return_value=PR),
            patch.object(followup, "read", return_value={}),
            patch.object(feedback, "collect", return_value=changed),
            self.assertRaisesRegex(RuntimeError, "publication withheld"),
        ):
            followup.verify_revision(self.config, expected, "credential")
