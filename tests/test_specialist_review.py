"""Publication requires the native Alibaba reviewer and material blocking findings only."""

import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import monitor
import review
import run
from review_report import draft_report, validate_report


def finding(**changes):
    return {
        "title": "Cross-account access",
        "category": "security",
        "severity": "high",
        "file": "app/routes.py",
        "line": 12,
        "evidence": "The account lookup uses the request ID without checking ownership.",
        "scenario": "A signed-in user supplies another account's ID.",
        "impact": "The response discloses that account's private records.",
        "remediation": "Check ownership before reading the records.",
        "introduced_or_worsened": True,
        "demonstrated_exploitability": True,
        "material_impact": True,
        **changes,
    }


def specialist(**changes):
    return {
        "verdict": "PASS",
        "summary": "Source reviewed.",
        "blocking_findings": [],
        "non_blocking_findings": [],
        "infrastructure_error": None,
        **changes,
    }


def evidence(code=None, security=None):
    # Assemble correctness and security fixtures into the single Alibaba result.
    result = copy.deepcopy(code or specialist())
    if security:
        for name in ("blocking_findings", "non_blocking_findings"):
            result[name].extend(security[name])
        if security["verdict"] != "PASS":
            result["verdict"] = security["verdict"]
        if security["infrastructure_error"]:
            result["infrastructure_error"] = security["infrastructure_error"]
    return {
        "kind": "ACPToolCallEvent",
        "title": "Factory specialist review",
        "status": "completed",
        "raw_input": {"version": 2, "threadId": "parent", "turnId": "turn"},
        "raw_output": {
            "agents": [
                {
                    "thread_id": "child-0",
                    "parent_thread_id": "parent",
                    "role": review.ROLES[0],
                    "status": "completed",
                    "message": json.dumps(result),
                }
            ]
        },
    }


class SpecialistVerdictTests(unittest.TestCase):
    def test_clean_review_passes_without_finding_quota(self):
        result = review.evaluate([evidence()])
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual([r.role for r in result.reviews], list(review.ROLES))

    def test_minor_findings_cannot_trigger_repairs_even_if_specialist_rejects(self):
        for category, severity in (
            ("security", "low"),
            ("security", "informational"),
            ("hardening", "high"),
            ("style", "medium"),
        ):
            with self.subTest(category=category, severity=severity):
                advisory = finding(
                    title="Optional improvement", category=category, severity=severity
                )
                result = review.evaluate(
                    [
                        evidence(
                            security=specialist(
                                verdict="CHANGES_REQUESTED", blocking_findings=[advisory]
                            )
                        )
                    ]
                )
                self.assertEqual(result.verdict, "PASS")
                self.assertEqual(result.reviews[0].verdict, "PASS")
                self.assertIn("Optional improvement", result.report())
                self.assertNotIn("Optional improvement", result.repair_instructions())

    def test_high_security_issue_cannot_be_hidden_by_a_pass_or_advisory_label(self):
        for severity in ("high", "critical"):
            result = review.evaluate(
                [evidence(security=specialist(non_blocking_findings=[finding(severity=severity)]))]
            )
            self.assertEqual(result.verdict, "CHANGES_REQUESTED")
            self.assertIn("Cross-account access", result.repair_instructions())

    def test_unrelated_existing_vulnerability_is_advisory(self):
        result = review.evaluate(
            [
                evidence(
                    security=specialist(blocking_findings=[finding(introduced_or_worsened=False)])
                )
            ]
        )
        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.reviews[0].non_blocking_findings[0].severity, "high")

    def test_medium_security_requires_exploitability_and_material_impact(self):
        for exploitability in (False, True):
            for material in (False, True):
                result = review.evaluate(
                    [
                        evidence(
                            security=specialist(
                                blocking_findings=[
                                    finding(
                                        severity="medium",
                                        demonstrated_exploitability=exploitability,
                                        material_impact=material,
                                    )
                                ]
                            )
                        )
                    ]
                )
                expected = "CHANGES_REQUESTED" if exploitability and material else "PASS"
                self.assertEqual(result.verdict, expected)

    def test_material_code_defect_blocks_in_combined_review(self):
        result = review.evaluate(
            [
                evidence(
                    code=specialist(blocking_findings=[finding(category="code", severity="medium")])
                )
            ]
        )
        self.assertEqual(result.verdict, "CHANGES_REQUESTED")
        self.assertEqual(result.reviews[0].verdict, "CHANGES_REQUESTED")

    def test_material_code_failure_is_not_downgraded_by_a_low_security_severity_label(self):
        result = review.evaluate(
            [
                evidence(
                    code=specialist(blocking_findings=[finding(category="code", severity="low")])
                )
            ]
        )
        self.assertEqual(result.verdict, "CHANGES_REQUESTED")

    def test_incomplete_unverified_or_invalid_reviews_cannot_pass(self):
        mutations = [
            lambda e: e.update(kind="MessageEvent"),
            lambda e: e.update(status="failed"),
            lambda e: e["raw_output"]["agents"].pop(),
            lambda e: e["raw_output"]["agents"][0].update(role="default"),
            lambda e: e["raw_output"]["agents"][0].update(parent_thread_id="other"),
            lambda e: e["raw_output"]["agents"][0].update(thread_id=""),
            lambda e: e["raw_output"]["agents"][0].update(status="inProgress"),
            lambda e: e["raw_output"]["agents"][0].update(message="PASS"),
            lambda e: e["raw_output"]["agents"][0].update(
                message=json.dumps(
                    specialist(verdict="BLOCKED", infrastructure_error="Source unreadable")
                )
            ),
            lambda e: e["raw_output"]["agents"][0].update(
                message=json.dumps(specialist(blocking_findings=[finding(scenario="")]))
            ),
        ]
        self.assertEqual(review.evaluate([]).verdict, "BLOCKED")
        for mutate in mutations:
            event = evidence()
            mutate(event)
            self.assertEqual(review.evaluate([event]).verdict, "BLOCKED")

    def test_latest_evidence_replaces_previous_success(self):
        failed = copy.deepcopy(evidence())
        failed["status"] = "failed"
        self.assertEqual(review.evaluate([evidence(), failed]).verdict, "BLOCKED")

    def test_coordinator_cannot_override_native_results(self):
        def converse(workspace, prompt, **kwargs):
            kwargs["event_log"].append(evidence(security=specialist(blocking_findings=[finding()])))
            return "Everything passed!"

        with (
            patch.object(review, "prepare", return_value={}),
            patch.object(review, "converse", converse),
            patch.object(
                review,
                "consolidate",
                side_effect=lambda workspace, result, **kwargs: validate_report(
                    result, draft_report(result)
                ),
            ),
        ):
            self.assertEqual(review.review_code(Mock(), "Context").verdict, "CHANGES_REQUESTED")
        with patch.object(review, "converse", side_effect=RuntimeError("timed out")):
            self.assertEqual(review.review_code(Mock(), "Context").verdict, "BLOCKED")


class SpecialistRepairTests(unittest.TestCase):
    def test_advisory_findings_are_retained_without_repair_and_excluded_from_test_repairs(self):
        result = review.evaluate(
            [
                evidence(
                    security=specialist(
                        non_blocking_findings=[
                            finding(title="Optional header hardening", severity="low")
                        ]
                    )
                )
            ]
        )
        for fail_tests in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                attempts = ([({"example": 1}, ["failed assertion"])] if fail_tests else []) + [
                    ({"example": 0}, ["passed"])
                ]
                with (
                    patch.object(run, "implementation_attempt", side_effect=attempts) as implement,
                    patch.object(run.repair_context, "retain"),
                    patch.object(run, "review_changes", return_value=result),
                    patch.object(run, "publish", return_value="https://example.test/pr") as publish,
                ):
                    output = run.execute_build(
                        [{"project": "example", "repository": "org/repo", "repair_attempts": 1}],
                        "task",
                        "Original specification",
                        "token",
                        None,
                        True,
                        root,
                        {
                            "example": {
                                "base": "base",
                                "commit": "head",
                                "branch": "task",
                                "repository": "/retained/task.git",
                            }
                        },
                    )
                self.assertEqual(output["status"], "PASSED")
                self.assertEqual(implement.call_count, 2 if fail_tests else 1)
                publish.assert_called_once()
                self.assertIn(
                    "Optional header hardening",
                    (root / f"review-{1 if fail_tests else 0}.md").read_text(),
                )
                self.assertNotIn("Optional header hardening", implement.call_args.args[3])
                if fail_tests:
                    self.assertIn("failed assertion", implement.call_args.args[3])


class ManualSpecialistReviewTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_opted_in_pr_review_receives_scope_and_accessible_patch_context(self):
        from test_traceability_review import assessed, change

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifacts"
            artifact.mkdir()
            scope = json.loads(
                (Path(__file__).parent / "fixtures/traceability-scope.json").read_text()
            )
            config = {"project": "example", "repository": "org/repo", "traceability_scope": scope}
            pr = {
                "number": 42,
                "head": {"sha": "a" * 40},
                "base": {"ref": "main", "sha": "c" * 40},
                "changed_files": 2,
            }
            files = [
                {"filename": "session.py", "patch": "fixture patch"},
                {"filename": "outside.txt"},
            ]

            def checkout(*args):
                source = root / "checkout"
                shutil.copytree(Path(__file__).parent / "fixtures/traceability", source)
                return source

            def reviewed(workspace, prompt, **kwargs):
                expected = kwargs["traceability"]
                context = expected["example"]
                self.assertEqual(context["scope"], scope)
                self.assertEqual(context["changed_paths"], ["session.py"])
                self.assertTrue((Path(context["source"]) / "session.py").is_file())
                self.assertEqual(
                    json.loads(Path(context["pr_context"]).read_text())["files"], files
                )
                self.assertIsNone(context["evidence_directory"])
                return review.evaluate(
                    [
                        evidence(
                            code=specialist(
                                traceability_assessment=[assessed([change()], project="example")]
                            )
                        )
                    ],
                    expected,
                )

            with (
                patch.object(monitor, "DATA", root),
                patch.object(monitor, "job_id", return_value="fixture"),
                patch.object(monitor, "evidence", return_value=artifact),
                patch.object(monitor, "lock", return_value=nullcontext()),
                patch.object(monitor.reviews, "_prepare_repository", side_effect=checkout),
                patch.object(monitor.reviews, "_load_repo_review_guide", return_value=""),
                patch.object(
                    monitor.issues,
                    "_github_paginate",
                    return_value=[],
                ),
                patch.object(
                    monitor.review_requests,
                    "github",
                    return_value={
                        "base_commit": {"sha": "c" * 40},
                        "files": files if pr["changed_files"] else [],
                    },
                ),
                patch.object(monitor, "worker", return_value=nullcontext(Mock())),
                patch.object(monitor, "review_code", side_effect=reviewed),
                patch.object(monitor.reviews, "_get_pr", return_value=pr),
                patch.object(monitor, "pr_eligible", return_value=True),
                patch.object(monitor, "publish_review") as publish,
            ):
                self.assertTrue(monitor._review_pr(config, pr, "offline-token"))
                self.assertEqual(publish.call_args.args[2].verdict, "PASS")
            retained = json.loads((artifact / "review.json").read_text())
            self.assertEqual(retained["traceability_context"]["example"]["scope"], scope)

    def test_manual_reviews_save_reports_and_recheck_pr_revision(self):
        for stale, blocked in (
            (None, False),
            ("head", False),
            ("base_ref", False),
            ("base_sha", False),
            (None, True),
        ):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                artifact = root / "artifacts"
                artifact.mkdir()
                result = review.evaluate([] if blocked else [evidence()])
                pr = {
                    "number": 42,
                    "head": {"sha": "a" * 40},
                    "base": {"ref": "main", "sha": "c" * 40},
                    "changed_files": 0,
                }
                changed = {
                    **pr,
                    "head": {"sha": ("b" if stale == "head" else "a") * 40},
                    "base": {
                        "ref": "release" if stale == "base_ref" else "main",
                        "sha": ("d" if stale == "base_sha" else "c") * 40,
                    },
                }

                def checkout(*args):
                    source = root / "checkout"
                    source.mkdir()
                    (source / "file.py").write_text("pass\n")
                    return source

                with (
                    patch.object(monitor, "DATA", root),
                    patch.object(monitor, "job_id", return_value="fixture"),
                    patch.object(monitor, "evidence", return_value=artifact),
                    patch.object(monitor, "lock", return_value=nullcontext()),
                    patch.object(monitor.reviews, "_prepare_repository", side_effect=checkout),
                    patch.object(monitor.reviews, "_load_repo_review_guide", return_value=""),
                    patch.object(monitor.issues, "_github_paginate", return_value=[]),
                    patch.object(
                        monitor.review_requests,
                        "github",
                        return_value={
                            "base_commit": {"sha": "c" * 40},
                            "files": [],
                        },
                    ),
                    patch.object(monitor, "worker", return_value=nullcontext(Mock())),
                    patch.object(monitor, "review_code", return_value=result) as reviewers,
                    patch.object(
                        monitor.reviews,
                        "_get_pr",
                        return_value=changed,
                    ) as fresh,
                    patch.object(monitor, "pr_eligible", return_value=True),
                    patch.object(monitor.review_requests, "remember") as receipt,
                    patch.object(monitor, "publish_review") as publication,
                ):
                    args = (
                        {"project": "example", "repository": "org/repo"},
                        pr,
                        "token",
                    )
                    if blocked:
                        with self.assertRaisesRegex(RuntimeError, "infrastructure blocked"):
                            monitor._review_pr(*args)
                        fresh.assert_not_called()
                        receipt.assert_not_called()
                        publication.assert_not_called()
                    else:
                        self.assertEqual(monitor._review_pr(*args), not stale)
                        saved = json.loads((artifact / "result.json").read_text())
                        self.assertEqual(saved["status"], "STALE" if stale else "REVIEWED")
                        self.assertEqual(saved["verdict"], "PASS")
                        self.assertEqual(saved["base"], pr["base"])
                        if stale:
                            receipt.assert_called_once_with(args[0], args[1], "STALE")
                            publication.assert_not_called()
                        else:
                            publication.assert_called_once_with(
                                args[0], args[1], result, "token", artifact
                            )
                self.assertIn("exact commit " + pr["head"]["sha"], reviewers.call_args.args[1])
                source = reviewers.call_args.kwargs["sources"]["example"]
                self.assertEqual(source["base"], pr["base"]["sha"])
                self.assertEqual(source["candidate"], pr["head"]["sha"])
                self.assertEqual(
                    reviewers.call_args.kwargs["transcript"], artifact / "review.jsonl"
                )
                self.assertEqual(
                    json.loads((artifact / "review.json").read_text())["verdict"], result.verdict
                )
                self.assertIn(
                    "Review incomplete" if blocked else "Approved",
                    (artifact / "review.md").read_text(),
                )


if __name__ == "__main__":
    unittest.main()
