"""Formal GitHub verdicts, duplicate prevention and recovery without another review."""

import copy
import json
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

import monitor
import review
import review_publication
import review_requests
from test_specialist_review import evidence, finding, specialist

CONFIG = {"project": "example", "repository": "org/repo"}
PR = {"number": 42, "head": {"sha": "a" * 40}, "state": "open", "draft": False}


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.calls = []
        self.pr = copy.deepcopy(PR)
        self.posted = []
        self.lost_response = False
        self.stack.enter_context(
            patch.object(review_publication, "lock", return_value=nullcontext())
        )
        self.stack.enter_context(
            patch.object(review_publication, "github", side_effect=self.github)
        )
        self.stack.enter_context(
            patch.object(
                review_publication.issues, "_github_paginate", side_effect=lambda *a: self.posted
            )
        )
        self.ci = self.stack.enter_context(
            patch.object(review_publication, "pr_eligible", return_value=True)
        )

    def github(self, credential, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/user":
            return {"login": "operator"}
        if method == "GET":
            return self.pr
        body = kwargs["body"]
        posted = {
            "id": 123,
            "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-123",
            "user": {"login": "operator"},
            "commit_id": body["commit_id"],
            "body": body["body"],
            "state": "APPROVED" if body["event"] == "APPROVE" else "CHANGES_REQUESTED",
            "submitted_at": "now",
        }
        self.posted.append(posted)
        if self.lost_response:
            raise TimeoutError("Lost response after GitHub accepted the review")
        return posted

    def test_clean_and_advisory_reviews_approve_with_both_reports(self):
        result = review.evaluate(
            [
                evidence(
                    code=specialist(
                        non_blocking_findings=[finding(category="style", severity="low")]
                    )
                )
            ]
        )
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["state"], "APPROVED")
        body = self.posted[0]["body"]
        for text in (
            "Verdict: APPROVE",
            "Code Reviewer",
            "Application Security Engineer",
            "Non-blocking findings",
            "Cross-account access",
            "a" * 40,
        ):
            self.assertIn(text, body)
        self.assertEqual(self.calls[-1][2]["body"]["event"], "APPROVE")

    def test_material_blockers_request_changes(self):
        result = review.evaluate([evidence(security=specialist(blocking_findings=[finding()]))])
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["state"], "CHANGES_REQUESTED")
        self.assertIn("Verdict: REQUEST_CHANGES", self.posted[0]["body"])
        self.assertEqual(self.calls[-1][2]["body"]["event"], "REQUEST_CHANGES")

    def test_prior_empty_review_body_does_not_prevent_publication(self):
        self.posted.append({"user": {"login": "operator"}, "commit_id": "a" * 40, "body": None})
        result = review.evaluate([evidence()])
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["state"], "APPROVED")

    def test_missing_specialist_or_inconsistent_verdict_cannot_post(self):
        good = review.evaluate([evidence()])
        bad_verdict = good.model_copy(update={"verdict": "CHANGES_REQUESTED"})
        missing = good.model_copy(update={"reviews": good.reviews[:1]})
        for result in (review.evaluate([]), bad_verdict, missing):
            with (
                self.subTest(verdict=result.verdict),
                self.assertRaises(review_publication.PublicationError),
            ):
                review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(self.calls, [])

    def test_changed_head_or_failed_ci_never_posts(self):
        result = review.evaluate([evidence()])
        self.pr["head"]["sha"] = "b" * 40
        with self.assertRaisesRegex(review_publication.PublicationError, "PR changed"):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.pr = copy.deepcopy(PR)
        self.ci.return_value = False
        with self.assertRaises(review_publication.PublicationError):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(self.posted, [])

    def test_lost_response_is_reconciled_without_duplicate_post(self):
        result = review.evaluate([evidence()])
        self.lost_response = True
        with self.assertRaises(TimeoutError):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.lost_response = False
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["id"], 123)
        self.assertEqual(len(self.posted), 1)

    def test_another_authors_marker_is_not_our_receipt_and_dismissal_is_not_approval(self):
        result = review.evaluate([evidence()])
        review_publication.publish(CONFIG, PR, result, "secret")
        self.posted[0]["user"]["login"] = "someone-else"
        review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(len(self.posted), 2)
        self.posted[1]["state"] = "DISMISSED"
        with self.assertRaisesRegex(review_publication.PublicationError, "dismissed"):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(len(self.posted), 2)


class PublicationRecoveryTests(unittest.TestCase):
    def test_failed_publication_reuses_verified_artifacts_without_rerunning_specialists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifacts"
            artifact.mkdir()
            events = [evidence()]
            result = review.evaluate(events)
            saved = {"repository": "org/repo", "pr": 42, "head": "a" * 40, "status": "REVIEWED"}
            (artifact / "result.json").write_text(json.dumps(saved))
            (artifact / "review.json").write_text(result.model_dump_json())
            (artifact / "review.md").write_text(result.report())
            (artifact / "review.jsonl").write_text(json.dumps(events[0]) + "\n")
            posted = {
                "id": 123,
                "state": "APPROVED",
                "html_url": "https://github.com/org/repo/pull/42#pullrequestreview-123",
            }
            with (
                patch.object(review_requests, "DATA", root),
                patch.object(review_requests, "job_id", return_value="run"),
                patch.object(monitor.reporting, "ACTIVE", None),
                patch.object(monitor, "_review_pr") as rerun,
                patch.object(
                    review_publication,
                    "publish",
                    side_effect=[TimeoutError("GitHub unavailable"), posted],
                ),
            ):
                with self.assertRaises(review_publication.PublicationError):
                    monitor.publish_review(CONFIG, PR, result, "secret", artifact)
                self.assertEqual(review_requests.read(CONFIG, PR)["status"], "PUBLICATION_FAILED")
                self.assertTrue(monitor.review_pr(CONFIG, PR, "secret"))
                rerun.assert_not_called()
                self.assertEqual(review_requests.read(CONFIG, PR)["github_review"], posted)
                self.assertEqual(json.loads((artifact / "result.json").read_text()), saved)
            self.assertEqual(review_publication.load_saved(CONFIG, PR, artifact), result)
            with self.assertRaises(review_publication.PublicationError):
                review_publication.load_saved({**CONFIG, "repository": "other/repo"}, PR, artifact)
            (artifact / "review.jsonl").write_text("")
            with self.assertRaisesRegex(review_publication.PublicationError, "native specialist"):
                review_publication.load_saved(CONFIG, PR, artifact)


if __name__ == "__main__":
    unittest.main()
