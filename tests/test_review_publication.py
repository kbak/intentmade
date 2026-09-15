"""Formal GitHub verdicts, duplicate prevention and recovery without another review."""

import copy
import importlib.util
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
from review_report import draft_report, validate_report
from test_specialist_review import evidence, finding, specialist
from test_traceability_review import assessed, change

CONFIG = {"project": "example", "repository": "org/repo"}
PR = {"number": 42, "head": {"sha": "a" * 40}, "state": "open", "draft": False}


def present(events):
    result = review.evaluate(events)
    result.presentation = validate_report(result, draft_report(result))
    return result


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

    def test_clean_and_advisory_reviews_approve_with_one_report(self):
        result = present(
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
            "**Approved**",
            "Suggestion",
            "Cross-account access",
            "a" * 40,
        ):
            self.assertIn(text, body)
        self.assertEqual(self.calls[-1][2]["body"]["event"], "APPROVE")

    def test_unconsolidated_or_incomplete_source_coverage_cannot_post(self):
        raw = review.evaluate([evidence(code=specialist(blocking_findings=[finding()]))])
        with self.assertRaisesRegex(review_publication.PublicationError, "consolidated report"):
            review_publication.publish(CONFIG, PR, raw, "secret")
        raw.presentation = validate_report(raw, draft_report(raw))
        raw.presentation.findings.clear()
        with self.assertRaisesRegex(ValueError, "every source finding"):
            review_publication.publish(CONFIG, PR, raw, "secret")
        self.assertEqual(self.calls, [])

    def test_material_blockers_request_changes(self):
        result = present([evidence(security=specialist(blocking_findings=[finding()]))])
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["state"], "CHANGES_REQUESTED")
        self.assertIn("**Changes requested**", self.posted[0]["body"])
        self.assertEqual(self.calls[-1][2]["body"]["event"], "REQUEST_CHANGES")

    @unittest.skipUnless(
        importlib.util.find_spec("versioned_traceability"), "Optional portable package"
    )
    def test_traceability_gap_requests_changes_without_a_code_or_security_finding(self):
        scope = json.loads((Path(__file__).parent / "fixtures/traceability-scope.json").read_text())
        expected = {
            "example": {
                "scope": scope,
                "changed_paths": ["session.py"],
                "requirement_index": {"candidate": {"ids": ["req~session-expiration~1"]}},
            }
        }
        native = evidence(
            code=specialist(
                traceability_assessment=[
                    assessed(
                        [
                            change(
                                "missing",
                                remediation="Connect the new behavior to its approved requirement and assertion.",
                            )
                        ],
                        project="example",
                    )
                ]
            )
        )
        result = review.evaluate([native], expected)
        result.presentation = validate_report(result, draft_report(result))
        config = {**CONFIG, "traceability_scope": scope}
        posted = review_publication.publish(config, PR, result, "secret")
        self.assertEqual(posted["state"], "CHANGES_REQUESTED")
        self.assertEqual(result.reviews[0].blocking_findings, [])
        self.assertIn("Missing traceability", self.posted[0]["body"])
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory)
            (artifact / "result.json").write_text(
                json.dumps(
                    {
                        "repository": CONFIG["repository"],
                        "pr": PR["number"],
                        "head": PR["head"]["sha"],
                        "status": "REVIEWED",
                    }
                )
            )
            (artifact / "review.json").write_text(result.model_dump_json())
            (artifact / "review.jsonl").write_text(json.dumps(native) + "\n")
            self.assertEqual(review_publication.load_saved(config, PR, artifact), result)
            changed_scope = copy.deepcopy(scope)
            changed_scope["policy"]["allow_skipped_tests"] = False
            with self.assertRaisesRegex(review_publication.PublicationError, "fresh review"):
                review_publication.load_saved(
                    {**CONFIG, "traceability_scope": changed_scope}, PR, artifact
                )
            legacy = result.model_dump()
            del legacy["traceability_context"]["example"]["requirement_index"]
            (artifact / "review.json").write_text(json.dumps(legacy))
            with self.assertRaisesRegex(review_publication.PublicationError, "source index"):
                review_publication.load_saved(config, PR, artifact)

    @unittest.skipUnless(
        importlib.util.find_spec("versioned_traceability"), "Optional portable package"
    )
    def test_legacy_review_cannot_approve_when_a_scope_is_now_required(self):
        scope = json.loads((Path(__file__).parent / "fixtures/traceability-scope.json").read_text())
        with self.assertRaisesRegex(review_publication.PublicationError, "fresh review"):
            review_publication.publish(
                {**CONFIG, "traceability_scope": scope}, PR, present([evidence()]), "secret"
            )
        self.assertEqual(self.calls, [])

    def test_prior_empty_review_body_does_not_prevent_publication(self):
        self.posted.append({"user": {"login": "operator"}, "commit_id": "a" * 40, "body": None})
        result = present([evidence()])
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["state"], "APPROVED")

    def test_missing_specialist_or_inconsistent_verdict_cannot_post(self):
        good = present([evidence()])
        bad_verdict = good.model_copy(update={"verdict": "CHANGES_REQUESTED"})
        missing = good.model_copy(update={"reviews": good.reviews[:1]})
        for result in (present([]), bad_verdict, missing):
            with (
                self.subTest(verdict=result.verdict),
                self.assertRaises(review_publication.PublicationError),
            ):
                review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(self.calls, [])

    def test_changed_head_or_failed_ci_never_posts(self):
        result = present([evidence()])
        self.pr["head"]["sha"] = "b" * 40
        with self.assertRaisesRegex(review_publication.PublicationError, "PR changed"):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.pr = copy.deepcopy(PR)
        self.ci.return_value = False
        with self.assertRaises(review_publication.PublicationError):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(self.posted, [])

    def test_lost_response_is_reconciled_without_duplicate_post(self):
        result = present([evidence()])
        self.lost_response = True
        with self.assertRaises(TimeoutError):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.lost_response = False
        posted = review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(posted["id"], 123)
        self.assertEqual(len(self.posted), 1)

    def test_another_authors_marker_is_not_our_receipt_and_dismissal_is_not_approval(self):
        result = present([evidence()])
        review_publication.publish(CONFIG, PR, result, "secret")
        self.posted[0]["user"]["login"] = "someone-else"
        review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(len(self.posted), 2)
        self.posted[1]["state"] = "DISMISSED"
        with self.assertRaisesRegex(review_publication.PublicationError, "dismissed"):
            review_publication.publish(CONFIG, PR, result, "secret")
        self.assertEqual(len(self.posted), 2)


class PublicationRecoveryTests(unittest.TestCase):
    def test_legacy_retry_consolidates_once_and_keeps_original_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifacts"
            artifact.mkdir()
            job = root / "job"
            job.mkdir()
            events = [evidence(code=specialist(blocking_findings=[finding()]))]
            result = review.evaluate(events)
            saved = {"repository": "org/repo", "pr": 42, "head": "a" * 40, "status": "REVIEWED"}
            originals = {
                "result.json": json.dumps(saved),
                "review.json": result.model_dump_json(),
                "review.md": "Original report",
                "review.jsonl": json.dumps(events[0]) + "\n",
            }
            for name, text in originals.items():
                (artifact / name).write_text(text)
            presentation = validate_report(result, draft_report(result))
            with (
                patch.object(
                    monitor.review_requests,
                    "read",
                    return_value={"status": "PUBLICATION_FAILED", "artifact": str(artifact)},
                ),
                patch.object(monitor.reporting, "ACTIVE", None),
                patch.object(monitor, "_review_pr") as rerun,
                patch.object(monitor, "publish_review") as publish,
                patch.object(monitor, "lock", return_value=nullcontext()),
                patch.object(monitor, "job_directory", return_value=nullcontext(job)),
                patch.object(monitor, "worker", return_value=nullcontext(object())),
                patch.object(monitor, "consolidate", return_value=presentation) as editor,
            ):
                self.assertTrue(monitor.review_pr(CONFIG, PR, "secret"))
                self.assertTrue(monitor.review_pr(CONFIG, PR, "secret"))
                editor.assert_called_once()
                rerun.assert_not_called()
                self.assertEqual(publish.call_count, 2)
                self.assertEqual(publish.call_args.args[2].presentation, presentation)
            for name, text in originals.items():
                self.assertEqual((artifact / name).read_text(), text)
            presentation.source_digest = "wrong evidence"
            (artifact / "review-presentation.json").write_text(presentation.model_dump_json())
            with self.assertRaisesRegex(ValueError, "different specialist evidence"):
                review_publication.load_saved(CONFIG, PR, artifact)

    def test_failed_publication_reuses_verified_artifacts_without_rerunning_specialists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifacts"
            artifact.mkdir()
            events = [evidence()]
            result = present(events)
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
