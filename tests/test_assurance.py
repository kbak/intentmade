"""Portable reports reuse existing controller verification and reviewer calls."""

import builtins
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review
import traceability
from pipeline_fixture import TraceabilityScenario


class OptionalBoundaryTests(unittest.TestCase):
    def test_unopted_record_review_does_not_import_intentbond(self):
        original = builtins.__import__

        def restricted(name, *args, **kwargs):
            if name.startswith("intentbond"):
                raise AssertionError("Ordinary runtime must not import IntentBond")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=restricted):
            # [utest~im-assurance-optional~1->req~im-portable-assurance~1]
            traceability.record_review(
                [{"project": "ordinary"}], {"ordinary": {}}, None, Path("unused.json")
            )


@unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional IntentBond runtime")
class AssuranceIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.scenario = TraceabilityScenario(self, Path(temp.name))

    def test_report_uses_one_existing_check_and_review_without_task_documents(self):
        from intentbond.assurance import assess as evaluate

        with patch("intentbond.assurance.assess", wraps=evaluate) as assessor:
            result = self.scenario.invoke()
        # [utest~im-assurance-current~1->req~im-portable-assurance~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(self.scenario.attempts), 1)
        self.assertEqual(len(self.scenario.review_prompts), 1)
        assessor.assert_called_once()
        state = self.scenario.states["pilot"]
        record = state["assurance"]
        self.assertEqual(record["status"], "satisfied")
        report = json.loads(Path(record["report"]).read_text())
        self.assertEqual(
            report["context"]["repositories"]["pilot"]["candidate"]["sha256"],
            state["traceability"]["candidate"]["sha256"],
        )
        self.assertEqual(report["context"]["artifacts"], [])
        self.assertNotIn("work_package", state)
        handoff = json.loads((self.scenario.artifact / "handoff.json").read_text())
        self.assertEqual(handoff["repositories"]["pilot"]["assurance"], record)
        checks = list(
            (self.scenario.artifact / "pilot").glob("traceability-invocations-*/index.json")
        )
        self.assertEqual(len(checks), 1)
        self.assertEqual(json.loads(checks[0].read_text())["controller_checks"], 1)

    def test_report_preserves_review_failure_and_does_not_publish(self):
        # [utest~im-assurance-failed-review~1->req~im-portable-assurance~1]
        def edit(root, attempt):
            path = root / "requirements.md"
            path.write_text(path.read_text().replace("30 minutes", "60 minutes"))

        self.scenario.config["repair_attempts"] = 0
        with self.assertRaises(RuntimeError):
            self.scenario.invoke(edit, verdict="CHANGES_REQUESTED")
        report = json.loads((self.scenario.artifact / "assurance-0/assessment.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["review"]["verdict"], "failed")
        self.assertFalse(self.scenario.states["pilot"].get("pull_request"))

    def test_shared_validator_is_the_existing_review_gate(self):
        from intentbond.assurance_review import validate_assessments

        with patch(
            "intentbond.assurance_review.validate_assessments", wraps=validate_assessments
        ) as validator:
            self.scenario.invoke()
        # [utest~im-assurance-shared-review~1->req~im-portable-assurance~1]
        self.assertTrue(validator.called)

    def test_accepted_package_keeps_original_identity_while_current_plan_evolves(self):
        import sdlc

        state = self.scenario.states["pilot"]
        package = sdlc.propose(
            "task",
            {"pilot": state["base"]},
            {
                "intent.md": "Preserve session expiry.\n",
                "spec.md": self.scenario.request,
                "plan.md": "Run the existing session checks.\n",
            },
        )
        sdlc.bind(Path(state["repository"]), package)
        state["work_package"] = {key: package[key] for key in ("directory", "sha256")}

        def edit(root, attempt):
            (root / package["directory"] / "plan.md").write_text(
                "Run the checks and inspect the expiry boundary.\n"
            )

        self.scenario.invoke(edit)
        # [utest~im-assurance-package~1->req~im-portable-assurance~1]
        report = json.loads(Path(state["assurance"]["report"]).read_text())
        self.assertEqual(report["status"], "satisfied")
        artifacts = {item["role"]: item for item in report["context"]["artifacts"]}
        for role in ("intent", "specification"):
            self.assertEqual(artifacts[role]["sha256"], artifacts[role]["accepted_sha256"])
        self.assertNotEqual(artifacts["plan"]["sha256"], artifacts["plan"]["accepted_sha256"])
        self.assertEqual(
            report["context"]["repositories"]["pilot"]["candidate"]["commit"], state["commit"]
        )

    def test_authorized_reflow_passes_the_full_assurance_gate_with_original_acceptance_retained(
        self,
    ):
        import sdlc
        from traceability.assurance import record

        state = self.scenario.states["pilot"]
        package = sdlc.propose(
            "task",
            {"pilot": state["base"]},
            {
                "intent.md": "Preserve session expiry.\n",
                "spec.md": self.scenario.request,
                "plan.md": "Run the existing session checks.\n",
            },
        )
        sdlc.bind(Path(state["repository"]), package)
        state["work_package"] = {key: package[key] for key in ("directory", "sha256")}
        state["formatting_amendment"] = {
            "package_sha256": sdlc.digest(sdlc.encoded(package)),
            "event_id": "explicit-user-event",
            "documents": ["spec.md"],
            "line_ranges": {"spec.md": [1, 1]},
        }
        original = (Path(state["repository"]) / "factory-work-package.json").read_bytes()

        def reflow(root, attempt):
            (root / package["directory"] / "spec.md").write_text(
                self.scenario.request.replace(" ", "\n", 1)
            )

        # [utest~im-assurance-formatting-amendment~1->req~im-portable-assurance~1]
        result = self.scenario.invoke(reflow)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(self.scenario.attempts), 1)
        self.assertEqual(len(self.scenario.review_prompts), 1)
        report_path = Path(state["assurance"]["report"])
        assessment = json.loads(report_path.read_text())
        self.assertEqual(assessment["status"], "satisfied")
        spec = next(a for a in assessment["context"]["artifacts"] if a["role"] == "specification")
        self.assertEqual(spec["sha256"], spec["accepted_sha256"])
        self.assertNotEqual(spec["sha256"], package["sha256"]["spec.md"])
        amendment = json.loads((report_path.parent / "formatting-amendments.json").read_text())[0]
        self.assertEqual(amendment["original_accepted_sha256"], package["sha256"]["spec.md"])
        self.assertEqual(amendment["amended_sha256"], spec["sha256"])
        self.assertEqual(assessment["decisions"][0]["actor"], "user-event:explicit-user-event")
        self.assertEqual(
            assessment["policy"]["profile"]["immutable_roles"],
            ["intent", "specification", "acceptance"],
        )
        self.assertEqual(
            (Path(state["repository"]) / "factory-work-package.json").read_bytes(), original
        )
        saved = review.ReviewResult.model_validate_json(
            (self.scenario.artifact / "review-0.json").read_text()
        )
        grant = state.pop("formatting_amendment")
        with self.assertRaisesRegex(ValueError, "Portable assurance remains failed"):
            record(
                [self.scenario.config],
                self.scenario.states,
                saved,
                self.scenario.artifact / "review-unapproved.json",
            )
        state["formatting_amendment"] = {**grant, "line_ranges": {"spec.md": [99, 99]}}
        with self.assertRaisesRegex(ValueError, "Portable assurance remains failed"):
            record(
                [self.scenario.config],
                self.scenario.states,
                saved,
                self.scenario.artifact / "review-wrong-range.json",
            )
        state["formatting_amendment"] = {**grant, "package_sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "another accepted package"):
            record(
                [self.scenario.config],
                self.scenario.states,
                saved,
                self.scenario.artifact / "review-wrong-package.json",
            )
        state["formatting_amendment"] = {k: v for k, v in grant.items() if k != "event_id"}
        with self.assertRaisesRegex(ValueError, "recorded user event"):
            record(
                [self.scenario.config],
                self.scenario.states,
                saved,
                self.scenario.artifact / "review-no-event.json",
            )
        state["formatting_amendment"] = grant
        manifest = Path(state["traceability"]["evidence"]).parent / "candidate-manifest.json"
        original_manifest = manifest.read_bytes()
        items = json.loads(original_manifest)
        next(item for item in items if item["path"] == package["directory"] + "/spec.md")[
            "sha256"
        ] = "0" * 64
        manifest.write_text(json.dumps(items))
        try:
            with self.assertRaisesRegex(ValueError, "checked candidate"):
                record(
                    [self.scenario.config],
                    self.scenario.states,
                    saved,
                    self.scenario.artifact / "review-wrong-manifest.json",
                )
        finally:
            manifest.write_bytes(original_manifest)
