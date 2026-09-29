"""Simple initial reports skip the editor without dropping evidence or history."""

import copy
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import monitor
import review
import review_publication
import review_report
import run
from test_specialist_review import evidence, finding, specialist
from test_traceability_review import EXPECTED, assessed, change


class DirectReportTests(unittest.TestCase):
    def test_report_editor_login_failure_keeps_reconnect_action_and_review_evidence(self):
        failure = {"code": "ACPAuthRequired", "detail": "Reconnect Codex"}

        def reviewer(workspace, prompt, **kwargs):
            kwargs["event_log"].append(evidence())

        with (
            patch.object(review, "prepare", return_value={}),
            patch.object(review, "converse", side_effect=reviewer),
            patch.object(review, "consolidate", side_effect=review.AgentStartupError(failure)),
        ):
            result = review.review_code(Mock(), "Review with discussion", initial_review=False)
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertEqual(result.startup_failure, failure)
        self.assertTrue(result.reviews)

    def test_initial_reviews_preserve_findings_coverage_and_traceability_without_editor(self):
        summary = (
            "Source inspected. Integration tests were not run; external behavior is unverified."
        )
        cases = [
            (specialist(summary=summary), None, "PASS"),
            (specialist(summary=summary, blocking_findings=[finding()]), None, "CHANGES_REQUESTED"),
            (
                specialist(summary=summary, non_blocking_findings=[finding(severity="low")]),
                None,
                "PASS",
            ),
            (
                specialist(
                    summary=summary,
                    traceability_assessment=[
                        assessed([change("missing", remediation="Add the missing assertion.")])
                    ],
                ),
                EXPECTED,
                "CHANGES_REQUESTED",
            ),
        ]
        for native, traceability, verdict in cases:
            with self.subTest(native=native):
                event = evidence(code=native)
                original = copy.deepcopy(event)

                def reviewer(workspace, prompt, **kwargs):
                    kwargs["event_log"].append(event)

                with (
                    patch.object(review, "prepare", return_value={}),
                    patch.object(review, "converse", side_effect=reviewer) as specialist_call,
                    patch.object(review_report, "converse") as editor,
                ):
                    result = review.review_code(
                        Mock(), "Initial review", initial_review=True, traceability=traceability
                    )
                specialist_call.assert_called_once()
                editor.assert_not_called()
                self.assertEqual(event, original)
                self.assertEqual(result.verdict, verdict)
                self.assertEqual(result.presentation.coverage, [summary])
                self.assertEqual(result.presentation.changes_since_previous_review, [])
                self.assertEqual(result.presentation.source_digest, review_report.digest(result))
                saved = review.ReviewResult.model_validate_json(result.model_dump_json())
                self.assertEqual(
                    review_report.validate_report(saved, saved.presentation), result.presentation
                )
                body = result.report("org/repo", "a" * 40)
                self.assertIn(summary, body)
                if traceability:
                    self.assertIn("Missing traceability", body)
                    self.assertIn("Add the missing assertion.", result.repair_instructions())
                    self.assertNotIn("**Approved**", body)
                for source, (original_finding, _) in review_report.sources(result).items():
                    rendered = result.presentation.findings[0]
                    self.assertEqual(rendered.source_ids, [source])
                    self.assertEqual(rendered.title, original_finding.title)
                    self.assertEqual(rendered.evidence, original_finding.evidence)
                    self.assertEqual(rendered.fix, original_finding.remediation)
                    self.assertIn(original_finding.scenario, rendered.description)
                    self.assertIn(original_finding.impact, rendered.description)
                    self.assertIn("/app/routes.py#L12", body)

    def test_follow_up_unknown_and_multiple_findings_keep_editor(self):
        for initial, count in ((False, 0), (None, 1), (True, 2)):
            with self.subTest(initial=initial, count=count):
                result = review.evaluate(
                    [evidence(code=specialist(blocking_findings=[finding()] * count))]
                )
                edited = review_report.draft_report(result)
                edited.changes_since_previous_review = ["Fixed — prior issue was reassessed."]
                with patch.object(review_report, "converse", return_value=edited) as editor:
                    report = review_report.consolidate(Mock(), result, initial_review=initial)
                editor.assert_called_once()
                self.assertEqual(
                    report.changes_since_previous_review, edited.changes_since_previous_review
                )

    def test_multiple_reviewers_keep_editor_even_without_findings(self):
        result = review.evaluate([evidence()])
        result.reviews.append(result.reviews[0].model_copy(deep=True))
        with patch.object(
            review_report, "converse", return_value=review_report.draft_report(result)
        ) as editor:
            review_report.consolidate(Mock(), result, initial_review=True)
        editor.assert_called_once()

    def test_source_prose_outside_report_schema_goes_to_editor_without_truncation(self):
        for field, value in (
            ("summary", "s" * 1401),
            ("title", "t" * 161),
            ("evidence", "e" * 1401),
            ("scenario", "s" * 1401),
            ("remediation", ""),
        ):
            with self.subTest(field=field):
                result = review.evaluate([evidence(code=specialist(blocking_findings=[finding()]))])
                edited = review_report.draft_report(result)
                target = (
                    result.reviews[0]
                    if field == "summary"
                    else result.reviews[0].blocking_findings[0]
                )
                setattr(target, field, value)
                with patch.object(review_report, "converse", return_value=edited) as editor:
                    review_report.consolidate(Mock(), result, initial_review=True)
                editor.assert_called_once()
                self.assertEqual(getattr(target, field), value)
                if value:
                    self.assertIn(value, editor.call_args.args[1])

    def test_direct_report_cannot_hide_or_reuse_changed_source_findings(self):
        result = review.evaluate([evidence(code=specialist(blocking_findings=[finding()]))])
        with patch.object(review_report, "converse") as editor:
            report = review_report.consolidate(Mock(), result, initial_review=True)
        editor.assert_not_called()
        missing = report.model_copy(deep=True)
        missing.findings.clear()
        with self.assertRaisesRegex(ValueError, "every source finding exactly once"):
            review_report.validate_report(result, missing)
        result.reviews[0].blocking_findings[0].evidence = "Changed evidence"
        with self.assertRaisesRegex(ValueError, "different specialist evidence"):
            review_report.validate_report(result, report)

    def test_direct_report_can_publish_with_coverage_and_original_verdict(self):
        summary = "Source inspected; integration tests were not run."
        for findings, event in (([], "APPROVE"), ([finding()], "REQUEST_CHANGES")):
            with self.subTest(event=event):
                result = review.evaluate(
                    [evidence(code=specialist(summary=summary, blocking_findings=findings))],
                    review_inputs={"app": {"base": "c" * 40, "candidate": "a" * 40, "files": []}},
                )
                with patch.object(review_report, "converse") as editor:
                    result.presentation = review_report.consolidate(
                        Mock(), result, initial_review=True
                    )
                pr = {
                    "number": 42,
                    "base": {"ref": "main", "sha": "c" * 40},
                    "head": {"sha": "a" * 40},
                }
                posted = {
                    "id": 1,
                    "html_url": "https://example.test/review/1",
                    "state": "APPROVED" if event == "APPROVE" else "CHANGES_REQUESTED",
                    "commit_id": pr["head"]["sha"],
                }
                github = Mock(side_effect=[{"login": "factory"}, pr, posted])
                with (
                    patch.object(review_publication, "lock", return_value=nullcontext()),
                    patch.object(review_publication, "pr_eligible", return_value=True),
                    patch.object(review_publication.issues, "_github_paginate", return_value=[]),
                    patch.object(review_publication, "github", github),
                ):
                    review_publication.publish(
                        {"project": "app", "repository": "org/repo"}, pr, result, "fixture"
                    )
                editor.assert_not_called()
                self.assertEqual(github.call_args.kwargs["body"]["event"], event)
                self.assertIn(summary, github.call_args.kwargs["body"]["body"])


class InitialReviewContextTests(unittest.TestCase):
    def test_build_marks_existing_task_stores_as_noninitial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"project": "app", "repository": "org/repo"}
            (root / "artifacts").mkdir()

            def task_store(*args):
                path = root / "tasks" / "app" / "task.git"
                path.mkdir(parents=True, exist_ok=True)
                return path, "task"

            with (
                patch.object(run, "DATA", root),
                patch.object(run, "evidence", return_value=root / "artifacts"),
                patch.object(run, "job_id", return_value="fixture"),
                patch.object(run, "lock", return_value=nullcontext()),
                patch.object(run, "git", return_value=Mock(stdout="base\n")),
                patch.object(run, "task_repository", side_effect=task_store),
                patch.object(run, "execute_build") as execute,
            ):
                for expected in (True, False):
                    run.build_group([config], "task", "spec", {"app": "base"})
                    self.assertIs(execute.call_args.args[-1]["app"]["initial_review"], expected)

    def test_repairs_resumed_unknown_and_mixed_group_builds_keep_editor(self):
        cases = [
            ([True], 0, False, True),
            ([True], 1, False, False),
            ([True], 0, True, False),
            ([False], 0, False, False),
            ([None], 0, False, False),
            ([True, True], 0, False, True),
            ([True, False], 0, False, False),
        ]
        for initial_states, attempt, maintenance, expected in cases:
            with (
                self.subTest(initial=initial_states, attempt=attempt, maintenance=maintenance),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                states = {}
                for index, initial in enumerate(initial_states):
                    source = root / str(index)
                    source.mkdir()
                    states[str(index)] = {
                        "source": str(source),
                        "repository": str(root / (str(index) + ".git")),
                        "base": "base",
                        "commit": "head",
                        "branch": "task",
                    }
                    if initial is not None:
                        states[str(index)]["initial_review"] = initial
                configs = [
                    {"project": project, **({"repair_pr": {"number": 42}} if maintenance else {})}
                    for project in states
                ]
                with (
                    patch.object(run, "job_directory", return_value=nullcontext(root)),
                    patch.object(run, "prepare_sources"),
                    patch.object(run.traceability, "prepare_review", return_value={}),
                    patch.object(run, "worker", return_value=nullcontext(Mock())),
                    patch.object(run, "review_code") as reviewer,
                ):
                    run.review_changes(configs, states, "spec", {}, attempt=attempt)
                self.assertIs(reviewer.call_args.kwargs["initial_review"], expected)

    def test_pr_history_disables_direct_reports(self):
        for history in (None, "discussion", "reviews", "inline_comments"):
            with self.subTest(history=history), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                artifact = root / "artifacts"
                artifact.mkdir()
                checkout = root / "checkout"
                checkout.mkdir()
                pr = {
                    "number": 42,
                    "base": {"ref": "main", "sha": "c" * 40},
                    "head": {"sha": "a" * 40},
                    "changed_files": 0,
                }

                def paginate(token, path):
                    if history is None:
                        return []
                    inline = "/pulls/" in path and path.endswith("/comments")
                    selected = (
                        inline
                        if history == "inline_comments"
                        else "/issues/" in path
                        if history == "discussion"
                        else path.endswith("/reviews")
                    )
                    return [{"body": "Previous finding or fix claim"}] if selected else []

                with (
                    patch.object(monitor, "DATA", root),
                    patch.object(monitor, "job_id", return_value="fixture"),
                    patch.object(monitor, "evidence", return_value=artifact),
                    patch.object(monitor, "lock", return_value=nullcontext()),
                    patch.object(monitor.reviews, "_prepare_repository", return_value=checkout),
                    patch.object(monitor.reviews, "_load_repo_review_guide", return_value=""),
                    patch.object(monitor.issues, "_github_paginate", side_effect=paginate),
                    patch.object(
                        monitor.review_requests,
                        "github",
                        return_value={
                            "base_commit": {"sha": "c" * 40},
                            "files": [],
                        },
                    ),
                    patch.object(monitor, "worker", return_value=nullcontext(Mock())),
                    patch.object(
                        monitor, "review_code", return_value=review.evaluate([evidence()])
                    ) as reviewer,
                    patch.object(monitor.reviews, "_get_pr", return_value=pr),
                    patch.object(monitor, "pr_eligible", return_value=True),
                    patch.object(monitor, "publish_review"),
                ):
                    self.assertTrue(
                        monitor._review_pr(
                            {"project": "app", "repository": "org/repo"}, pr, "fixture"
                        )
                    )
                self.assertIs(reviewer.call_args.kwargs["initial_review"], history is None)


if __name__ == "__main__":
    unittest.main()
