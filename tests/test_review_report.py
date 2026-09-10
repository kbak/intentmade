"""Readable consolidated reports retain provenance and cannot hide blockers."""

import copy
import unittest
from unittest.mock import Mock, patch

import review
import review_report
from test_specialist_review import evidence, finding, specialist


def overlapping_review():
    code = [
        finding(
            title="First writes can lose a key",
            category="code",
            severity="medium",
            file="app/crypto.py",
            line=80,
        ),
        finding(
            title="Imports leave encrypted rows unreadable",
            category="code",
            severity="medium",
            file="app/imports.py",
            line=42,
        ),
    ]
    security = [
        {
            **code[0],
            "title": "Concurrent initialization overwrites the encryption key",
            "evidence": "Both writers can overwrite the same key.",
        },
        {**code[1], "title": "Plaintext import retains the encryption flag", "line": 41},
    ]
    return review.evaluate(
        [
            evidence(
                code=specialist(blocking_findings=code),
                security=specialist(blocking_findings=security),
            )
        ]
    )


def merged_report(result):
    draft = review_report.draft_report(result)
    groups = []
    for index in range(2):
        group = copy.deepcopy(draft.findings[index])
        group.source_ids = [f"0:blocking:{index}", f"1:blocking:{index}"]
        groups.append(group)
    return review_report.ReviewReport(
        findings=groups,
        coverage=["In-memory checks passed; integration tests were not run locally."],
    )


class ConsolidationTests(unittest.TestCase):
    def test_semantic_groups_merge_different_titles_and_nearby_lines_without_losing_sources(self):
        result = overlapping_review()
        raw = result.model_dump()
        result.presentation = review_report.validate_report(result, merged_report(result))
        report = result.report("org/repo", "a" * 40)
        self.assertIn("2 issues to address", report)
        self.assertEqual(report.count("### "), 2)
        self.assertIn("/blob/" + "a" * 40 + "/app/crypto.py#L80", report)
        self.assertIn("**Evidence:**", report)
        self.assertIn("**Fix:**", report)
        self.assertIn("<summary>Validation and scope</summary>", report)
        for text in (
            "None.",
            "Non-blocking findings",
            "Code Reviewer —",
            "4 blocking",
            "CHANGES_REQUESTED",
        ):
            self.assertNotIn(text, report)
        self.assertEqual(
            result.model_dump(exclude={"presentation"}),
            {k: v for k, v in raw.items() if k != "presentation"},
        )
        self.assertEqual(result.repair_instructions().count("Fix:"), 2)

    def test_missing_repeated_and_invented_source_ids_are_rejected(self):
        result = overlapping_review()
        for change in (
            lambda p: p.findings.pop(),
            lambda p: p.findings[0].source_ids.append("0:blocking:0"),
            lambda p: p.findings[0].source_ids.append("unknown"),
        ):
            report = merged_report(result)
            change(report)
            with self.assertRaisesRegex(ValueError, "every source finding exactly once"):
                review_report.validate_report(result, report)

    def test_report_cannot_be_reused_with_changed_native_evidence(self):
        result = overlapping_review()
        report = review_report.validate_report(result, merged_report(result))
        result.reviews[0].blocking_findings[0].evidence = "Different defect"
        with self.assertRaisesRegex(ValueError, "different specialist evidence"):
            review_report.validate_report(result, report)

    def test_unrelated_findings_on_the_same_line_stay_separate(self):
        result = review.evaluate(
            [
                evidence(
                    code=specialist(
                        blocking_findings=[
                            finding(title="Missing authorization"),
                            finding(
                                title="Destructive retry",
                                scenario="A retry deletes another record.",
                            ),
                        ]
                    )
                )
            ]
        )
        report = review_report.draft_report(result)
        with patch.object(review_report, "converse", return_value=report) as editor:
            result.presentation = review_report.consolidate(Mock(), result)
        self.assertEqual(len(result.presentation.findings), 2)
        self.assertIn("Same file or nearby lines alone", editor.call_args.args[1])
        self.assertIn("Do not use tools, delegate, or publish", editor.call_args.args[1])

    def test_merged_advisory_cannot_downgrade_a_blocker_or_its_severity(self):
        result = review.evaluate(
            [
                evidence(
                    code=specialist(blocking_findings=[finding()]),
                    security=specialist(non_blocking_findings=[finding(severity="low")]),
                )
            ]
        )
        draft = review_report.draft_report(result)
        draft.findings[0].source_ids = ["0:blocking:0", "1:advisory:0"]
        draft.findings = draft.findings[:1]
        result.presentation = review_report.validate_report(result, draft)
        text = result.report()
        self.assertIn("Changes requested", text)
        self.assertIn("Blocking · High", text)
        self.assertIn("1 issue to address", text)

    def test_clean_and_advisory_reports_have_no_empty_sections(self):
        clean = review.evaluate([evidence()])
        clean.presentation = review_report.validate_report(clean, review_report.draft_report(clean))
        self.assertEqual(
            clean.report(),
            "## Code and security review\n\n**Approved** — no blocking issues found.",
        )
        advisory = review.evaluate(
            [
                evidence(
                    code=specialist(
                        non_blocking_findings=[finding(category="style", severity="low")]
                    )
                )
            ]
        )
        advisory.presentation = review_report.validate_report(
            advisory, review_report.draft_report(advisory)
        )
        self.assertIn("1 optional improvement", advisory.report())
        self.assertNotIn("Blocking", advisory.report())
        self.assertNotIn("None", advisory.report())

    def test_follow_up_credits_fixes_without_hiding_current_blockers(self):
        result = review.evaluate(
            [
                evidence(
                    code=specialist(
                        summary="Import flag fixed: shared encryption writer now updates the flag. "
                        "Key race partially fixed: atomic envelope insert; OS-keychain still races.",
                        blocking_findings=[finding(title="OS-keychain still loses the key")],
                    )
                )
            ]
        )
        report = review_report.draft_report(result)
        report.changes_since_previous_review = [
            "**Fixed — Import flag:** the shared writer keeps imported answers readable.",
            "**Partially fixed — Key race:** envelope storage is atomic; OS-keychain still races.",
        ]
        with patch.object(review_report, "converse", return_value=report) as editor:
            result.presentation = review_report.consolidate(Mock(), result)
        prompt = editor.call_args.args[1]
        self.assertIn(result.reviews[0].summary, prompt)
        self.assertIn("Never infer resolution merely from an absent", prompt)
        body = result.report()
        self.assertLess(body.index("Since the previous review"), body.index("### 1."))
        self.assertIn("Fixed — Import flag", body)
        self.assertIn("Partially fixed — Key race", body)
        self.assertIn("1 issue to address", body)
        self.assertIn("### 1. OS-keychain still loses the key", body)
        self.assertNotIn("Import flag", result.repair_instructions())

    def test_resolved_follow_up_can_approve_and_old_reports_remain_readable(self):
        result = review.evaluate([evidence()])
        # Existing saved reports predate the optional progress field.
        legacy = {"findings": [], "coverage": []}
        result.presentation = review_report.validate_report(result, legacy)
        self.assertNotIn("Since the previous review", result.report())
        result.presentation.changes_since_previous_review = [
            "**Fixed — Import flag:** the actual importer now preserves answer readability."
        ]
        self.assertIn("Approved", result.report())
        self.assertIn("Fixed — Import flag", result.report())
        self.assertNotIn("### 1.", result.report())

    def test_native_specialists_are_asked_to_verify_prior_fixes(self):
        prompt = review.coordinator_prompt("Prior reviews are in review-context.json")
        self.assertIn("Give both the full context, factory policy", prompt)
        self.assertIn("reassess the earlier actionable findings", prompt)
        self.assertIn("Keep resolved issues out of the current findings lists", prompt)

    def test_new_review_consolidates_only_after_native_specialists_finish(self):
        native = evidence(code=specialist(blocking_findings=[finding()]))
        order = []

        def converse(workspace, prompt, **kwargs):
            order.append("specialists")
            kwargs["event_log"].append(native)

        def consolidate(workspace, result, **kwargs):
            order.append("consolidation")
            self.assertEqual(len(result.reviews), 2)
            return review_report.validate_report(result, review_report.draft_report(result))

        with (
            patch.object(review, "converse", side_effect=converse),
            patch.object(review, "consolidate", side_effect=consolidate),
        ):
            result = review.review_code(Mock(), "Review this change")
        self.assertEqual(order, ["specialists", "consolidation"])
        self.assertEqual(result.verdict, "CHANGES_REQUESTED")
        self.assertIsNotNone(result.presentation)

    def test_failed_editor_does_not_publish_an_unsummarized_review(self):
        def converse(workspace, prompt, **kwargs):
            kwargs["event_log"].append(evidence())

        with (
            patch.object(review, "converse", side_effect=converse),
            patch.object(review, "consolidate", side_effect=ValueError("missing source")),
        ):
            result = review.review_code(Mock(), "Review")
        self.assertEqual(result.verdict, "BLOCKED")
        self.assertIn("Report consolidation failed", result.report())
        self.assertNotIn("Approved", result.report())


if __name__ == "__main__":
    unittest.main()
