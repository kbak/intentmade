"""Deterministic assessment gates; supplied judgments do not test model reasoning."""

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import review
import review_report
from review_fixture import EXPECTED, assessed, change, evidence, finding, specialist


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
    def test_unknown_and_stale_requirement_ids_block_review(self):
        for identifier in ("req~invented~1", "req~session-expiration~0", "req~session-expiration"):
            with self.subTest(identifier=identifier):
                reviewed = result([change(requirement_ids=[identifier])])
                self.assertEqual(reviewed.verdict, "BLOCKED")
                self.assertIn("unknown requirement ID or revision", reviewed.summary)

    def test_historical_citation_requires_an_explicit_available_snapshot(self):
        reviewed = result([change(requirement_ids=["base:req~session-expiration~0"])])
        self.assertEqual(reviewed.verdict, "PASS")
        self.assertEqual(reviewed.traceability_context, EXPECTED)
        unavailable = copy.deepcopy(EXPECTED)
        del unavailable["pilot"]["requirement_index"]["base"]
        reviewed = result(
            [change(requirement_ids=["base:req~session-expiration~0"])], expected=unavailable
        )
        self.assertEqual(reviewed.verdict, "BLOCKED")
        self.assertIn("snapshot not available", reviewed.summary)

    def test_missing_or_failed_index_cannot_validate_a_citation(self):
        for indexed in ({}, {"requirement_index": {"candidate": {"error": "OFT import failed"}}}):
            reviewed = result(expected={"pilot": {"changed_paths": ["session.py"], **indexed}})
            self.assertEqual(reviewed.verdict, "BLOCKED")
            self.assertIn("a source index is required", reviewed.summary)

    def test_malformed_id_list_cannot_match_by_substring(self):
        for ids in ("prefix-req~session-expiration~1-suffix", None, [1]):
            expected = copy.deepcopy(EXPECTED)
            expected["pilot"]["requirement_index"]["candidate"]["ids"] = ids
            reviewed = result(expected=expected)
            self.assertEqual(reviewed.verdict, "BLOCKED")
            self.assertIn("malformed requirement ID index", reviewed.summary)

    def test_ordinary_review_requires_no_assessment(self):
        ordinary = review.evaluate([evidence()])
        self.assertEqual(ordinary.verdict, "PASS")
        self.assertNotIn(
            "traceability_assessment", review.SpecialistReview.model_json_schema()["properties"]
        )
        self.assertNotIn("Traceability", ordinary.report())

    def test_existing_relationships_pass_without_new_ids(self):
        reviewed = result()
        # [utest~im-traceability_review-AssessmentGateTests-existing_relationships_pass_without_new_ids~1->req~im-trace-assessment~1]
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
        # [utest~im-traceability_review-AssessmentGateTests-concrete_gap_blocks_even_if_reviewer_labels_overall_result_pass~1->req~im-trace-assessment~1]
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
        # [utest~im-traceability_review-AssessmentGateTests-uncertain_is_incomplete_not_covered_or_a_fabricated_defect~1->req~im-trace-assessment~1]
        self.assertEqual(reviewed.verdict, "BLOCKED")
        self.assertEqual(reviewed.reviews[0].blocking_findings, [])
        self.assertIn("acceptance evidence is unavailable", reviewed.report())

    def test_absent_required_assessment_cannot_pass(self):
        reviewed = review.evaluate([evidence()], EXPECTED)
        # [utest~im-traceability_review-AssessmentGateTests-absent_required_assessment_cannot_pass~1->req~im-trace-assessment~1]
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

    def test_human_summary_exposes_gaps_without_changing_agent_inputs_or_repairs(self):
        reviewed = result(
            [
                change(behavior="Existing coverage"),
                change(
                    "missing", behavior="Logout", remediation="Link the approved logout promise."
                ),
                change("uncertain", behavior="Remote verification"),
                change("not_needed", behavior="Mechanical rename"),
            ]
        )
        original = reviewed.model_dump_json()
        repair = reviewed.repair_instructions()
        prompt = review.coordinator_prompt("Existing task", EXPECTED)
        with patch.object(
            review_report, "converse", side_effect=AssertionError("No new agent pass")
        ):
            text = reviewed.report()
        self.assertIn("**Review incomplete**", text)
        self.assertIn("Assessed changes: 4. Distinct changed paths: 1.", text)
        self.assertIn("1 missing · 1 uncertain · 1 covered · 1 needing no additional tracing", text)
        folded = text.index("<details>")
        self.assertLess(text.index("Missing traceability — Logout"), folded)
        self.assertLess(text.index("Uncertain — Remote verification"), folded)
        self.assertGreater(text.index("Covered — Existing coverage"), folded)
        self.assertIn("Verification references do not establish that tests ran", text)
        self.assertIn("test_expiration checks both sides of 1800", text)
        self.assertEqual(reviewed.model_dump_json(), original)
        self.assertEqual(reviewed.repair_instructions(), repair)
        self.assertEqual(review.coordinator_prompt("Existing task", EXPECTED), prompt)

    def test_saved_ordinary_reports_keep_their_original_digest(self):
        reviewed = review.evaluate([evidence()])
        legacy = [
            item.model_dump(exclude={"traceability_assessment", "intent_alignment"})
            for item in reviewed.reviews
        ]
        digest = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
        presentation = review_report.draft_report(reviewed).model_dump()
        presentation["source_digest"] = digest
        raw = copy.deepcopy(reviewed.model_dump())
        raw.update(reviews=legacy, presentation=presentation)
        self.assertIn("Approved", review.ReviewResult.model_validate(raw).report())
