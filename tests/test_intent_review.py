"""Compact judgments share the existing review; fixtures do not test model reasoning."""

import copy
import unittest
from unittest.mock import Mock, patch

import review
import review_report
from review_fixture import EXPECTED, assessed, evidence, specialist


def alignment(status="aligned", **updates):
    return {
        "project": "pilot",
        "status": status,
        "references": ["docs/intent.md#reliable-retries", "supplied request"],
        "summary": "The retry fix preserves the promised form data.",
        **updates,
    }


class IntentReviewTests(unittest.TestCase):
    def test_exact_repository_assessments_are_required_with_and_without_oft(self):
        for traceability in (None, EXPECTED):
            extras = {"traceability_assessment": [assessed()]} if traceability else {}
            for entries in (
                [],
                [alignment(project="other")],
                [alignment(), alignment()],
                [alignment(references=[])],
                [alignment(summary=" ")],
                [alignment(status="uncertain")],
            ):
                with self.subTest(traceability=bool(traceability), entries=entries):
                    result = review.evaluate(
                        [evidence(specialist(intent_alignment=entries, **extras))],
                        traceability,
                        intent_projects=["pilot"],
                    )
                    # [utest~im-intent-review-completeness~1->req~im-intent-consistency~1]
                    self.assertEqual(result.verdict, "BLOCKED")
            valid = review.evaluate(
                [evidence(specialist(intent_alignment=[alignment()], **extras))],
                traceability,
                intent_projects=["pilot"],
            )
            self.assertEqual(valid.verdict, "PASS")
        # Standalone/legacy reviews have no new required assessment.
        self.assertEqual(review.evaluate([evidence()]).verdict, "PASS")
        grouped = review.evaluate(
            [evidence(specialist(intent_alignment=[alignment(), alignment(project="second")]))],
            intent_projects=["pilot", "second"],
        )
        self.assertEqual(grouped.verdict, "PASS")

    def test_conflict_survives_pass_label_consolidation_and_saved_evidence(self):
        conflict = alignment("conflict", summary="The new retry handler clears promised form data.")
        for traceability in (None, EXPECTED):
            extras = {"traceability_assessment": [assessed()]} if traceability else {}
            result = review.evaluate(
                [evidence(specialist(intent_alignment=[conflict], **extras))],
                traceability,
                intent_projects=["pilot"],
            )
            # [utest~im-intent-review-conflict~1->req~im-intent-consistency~1]
            self.assertEqual(result.verdict, "CHANGES_REQUESTED")
            self.assertEqual(result.reviews[0].blocking_findings, [])
            result.presentation = review_report.validate_report(
                result, review_report.draft_report(result)
            )
            restored = review.ReviewResult.model_validate_json(result.model_dump_json())
            self.assertIn("Changes requested", restored.report())
            self.assertIn(conflict["summary"], restored.report())
            self.assertIn(conflict["summary"], restored.repair_instructions())
            self.assertIn("NEEDS_INPUT", restored.repair_instructions())
            self.assertIn(conflict["references"][0], restored.report())
            changed = copy.deepcopy(restored)
            changed.reviews[0].intent_alignment[0].status = "aligned"
            with self.assertRaisesRegex(ValueError, "different specialist evidence"):
                review_report.validate_report(changed, restored.presentation)

    def test_same_reviewer_call_supplies_intent_assessment(self):
        def converse(workspace, prompt, **kwargs):
            self.assertIn('"pilot"', prompt)
            self.assertIn("intent_alignment", prompt)
            kwargs["event_log"].append(evidence(specialist(intent_alignment=[alignment()])))

        with (
            patch.object(review, "prepare", return_value={}),
            patch.object(review, "converse", side_effect=converse) as call,
            patch.object(
                review,
                "consolidate",
                side_effect=lambda workspace, result, **kwargs: review_report.validate_report(
                    result, review_report.draft_report(result)
                ),
            ),
        ):
            result = review.review_code(Mock(), "Approved request", intent_projects=["pilot"])
        # [utest~im-intent-review-existing-pass~1->req~im-intent-consistency~1]
        self.assertEqual(result.verdict, "PASS")
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs["skill"], "factory-review")
