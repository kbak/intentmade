"""Deterministic assessment gates; supplied judgments do not test model reasoning."""

import copy
import hashlib
import json
import unittest

import review
import review_report
from test_specialist_review import evidence, finding, specialist

EXPECTED = {"pilot": {"changed_paths": ["session.py"]}}


def change(status="covered", **updates):
    return {
        "changed_paths": ["session.py"],
        "behavior": "Session inactivity expiration",
        "status": status,
        "requirement_ids": ["req~session-expiration~1"],
        "documentation": ["requirements.md: Session expiration"],
        "implementation": ["session.py: expired and its impl reference"],
        "verification": ["tests/test_session.py: test_expiration checks both sides of 1800"],
        "rationale": "The existing requirement and boundary assertions support the changed threshold expression.",
        **updates,
    }


def assessed(changes=None, project="pilot"):
    return {
        "project": project,
        "summary": "Scripted assessment fixture.",
        "changes": [change()] if changes is None else changes,
    }


def result(changes=None, expected=None, **specialist_fields):
    return review.evaluate(
        [
            evidence(
                code=specialist(traceability_assessment=[assessed(changes)], **specialist_fields)
            )
        ],
        EXPECTED if expected is None else expected,
    )


class AssessmentGateTests(unittest.TestCase):
    def test_ordinary_review_requires_no_assessment(self):
        ordinary = review.evaluate([evidence()])
        self.assertEqual(ordinary.verdict, "PASS")
        self.assertNotIn(
            "traceability_assessment", review.SpecialistReview.model_json_schema()["properties"]
        )
        self.assertNotIn("Traceability", ordinary.report())

    def test_existing_relationships_pass_without_new_ids(self):
        reviewed = result()
        self.assertEqual(reviewed.verdict, "PASS")
        self.assertEqual(
            reviewed.reviews[0].traceability_assessment[0].changes[0].requirement_ids,
            ["req~session-expiration~1"],
        )
        restored = review.ReviewResult.model_validate_json(reviewed.model_dump_json())
        self.assertIn("test_expiration", restored.report())

    def test_mechanical_change_needs_a_reason_but_not_new_references(self):
        reviewed = result(
            [
                change(
                    "not_needed",
                    requirement_ids=[],
                    documentation=[],
                    implementation=[],
                    verification=[],
                    rationale="Multiplying the same constants preserves the expiration threshold; existing tests cover the boundary.",
                )
            ]
        )
        self.assertEqual(reviewed.verdict, "PASS")
        self.assertIn("No additional tracing needed", reviewed.report())
        self.assertEqual(reviewed.repair_instructions(), "No blocking review findings.")

    def test_concrete_gap_blocks_even_if_reviewer_labels_overall_result_pass(self):
        reviewed = result(
            [
                change(
                    "missing",
                    rationale="Explicit logout is new behavior; the inactivity ID does not document logout or connect an assertion to it.",
                    remediation="Document the approved logout promise and link its implementation and assertion.",
                )
            ]
        )
        self.assertEqual(reviewed.verdict, "CHANGES_REQUESTED")
        self.assertEqual(reviewed.reviews[0].blocking_findings, [])
        reviewed.presentation = review_report.validate_report(
            reviewed, review_report.draft_report(reviewed)
        )
        self.assertIn("Document the approved logout promise", reviewed.repair_instructions())
        self.assertIn("Missing traceability", reviewed.report())
        self.assertNotIn("**Approved**", reviewed.report())

    def test_uncertain_is_incomplete_not_covered_or_a_fabricated_defect(self):
        reviewed = result(
            [
                change(
                    "uncertain",
                    rationale="The referenced acceptance evidence is unavailable; behavior coverage cannot be established.",
                )
            ]
        )
        self.assertEqual(reviewed.verdict, "BLOCKED")
        self.assertEqual(reviewed.reviews[0].blocking_findings, [])
        self.assertIn("acceptance evidence is unavailable", reviewed.report())

    def test_absent_required_assessment_cannot_pass(self):
        reviewed = review.evaluate([evidence()], EXPECTED)
        self.assertEqual(reviewed.verdict, "BLOCKED")
        self.assertIn("required traceability assessment", reviewed.summary)

    def test_incomplete_or_out_of_scope_accounting_cannot_pass(self):
        invalid = [
            [],
            [assessed([])],
            [assessed(project="opted-out")],
            [assessed(), assessed()],
            [assessed([change(verification=[])])],
            [assessed([change("missing")])],
            [assessed([change("not_needed", rationale=" ")])],
            [assessed([change(changed_paths=["outside.txt"])])],
        ]
        for assessment in invalid:
            with self.subTest(assessment=assessment):
                reviewed = review.evaluate(
                    [evidence(code=specialist(traceability_assessment=assessment))], EXPECTED
                )
                self.assertEqual(reviewed.verdict, "BLOCKED")

    def test_all_scoped_repositories_require_their_own_assessment(self):
        expected = {**EXPECTED, "second": {"changed_paths": []}}
        self.assertEqual(result(expected=expected).verdict, "BLOCKED")
        complete = review.evaluate(
            [
                evidence(
                    code=specialist(
                        traceability_assessment=[assessed(), assessed([], project="second")]
                    )
                )
            ],
            expected,
        )
        self.assertEqual(complete.verdict, "PASS")

    def test_no_scoped_changes_accepts_an_explicit_empty_assessment(self):
        self.assertEqual(result([], expected={"pilot": {"changed_paths": []}}).verdict, "PASS")

    def test_assessment_cannot_override_code_or_security_gates(self):
        for role in ("code", "security"):
            with self.subTest(role=role):
                code = specialist(traceability_assessment=[assessed()])
                security = specialist()
                (code if role == "code" else security)["blocking_findings"] = [
                    finding(category=role)
                ]
                self.assertEqual(
                    review.evaluate([evidence(code=code, security=security)], EXPECTED).verdict,
                    "CHANGES_REQUESTED",
                )

    def test_report_binds_assessment_and_cannot_hide_its_outcome(self):
        reviewed = result()
        reviewed.presentation = review_report.validate_report(
            reviewed, review_report.draft_report(reviewed)
        )
        reviewed.reviews[0].traceability_assessment[0].changes[0].status = "uncertain"
        with self.assertRaisesRegex(ValueError, "different specialist evidence"):
            reviewed.report()

    def test_saved_ordinary_reports_keep_their_original_digest(self):
        reviewed = review.evaluate([evidence()])
        legacy = [item.model_dump(exclude={"traceability_assessment"}) for item in reviewed.reviews]
        digest = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
        presentation = review_report.draft_report(reviewed).model_dump()
        presentation["source_digest"] = digest
        raw = copy.deepcopy(reviewed.model_dump())
        raw.update(reviews=legacy, presentation=presentation)
        self.assertIn("Approved", review.ReviewResult.model_validate(raw).report())
