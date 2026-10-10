"""Real Git/OFT/tests through the factory lifecycle; implementation/review are scripted."""

import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common
import run
import traceability
from pipeline_fixture import TraceabilityScenario
from review_fixture import assessed, change, specialist


@unittest.skipUnless(
    importlib.util.find_spec("intentbond"),
    "Optional traceability packages require the pilot test image",
)
class TraceabilityPipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="factory-traceability-test-")
        self.addCleanup(temp.cleanup)
        self.scenario = TraceabilityScenario(self, Path(temp.name))

    def test_scripted_gap_keeps_green_graph_from_completing_and_repair_is_reassessed(self):
        self.scenario.config["repair_attempts"] = 1
        self.scenario.request = "Preserve session expiration and add explicit logout, including its documented promise and verification."
        observed = []

        def edit(root, attempt):
            path = root / "session.py"
            if attempt == 1:
                # The existing ID remains in the file, but says nothing about logout.
                path.write_text(
                    path.read_text()
                    .replace("def expired(seconds):", "def expired(seconds, logged_out=False):")
                    .replace("return seconds >= 1800", "return logged_out or seconds >= 1800")
                )
            else:
                self.assertIn("Document and verify explicit logout", self.scenario.attempts[-1])
                requirements = root / "requirements.md"
                requirements.write_text(
                    requirements.read_text()
                    + "\n### Explicit logout\n`req~explicit-logout~1`\n\nAn explicitly logged-out session expires immediately.\n\nNeeds: impl, utest\n"
                )
                path.write_text(
                    path.read_text().replace(
                        "def expired", "# [impl->req~explicit-logout~1]\ndef expired"
                    )
                )
                tests = root / "tests/test_session.py"
                tests.write_text(
                    tests.read_text()
                    + "\n    # [utest->req~explicit-logout~1]\n    def test_logout(self):\n        self.assertTrue(expired(0, logged_out=True))\n"
                )

        def assessment(expected):
            context = expected["pilot"]
            observed.append(context)
            bundle = Path(context["evidence_directory"])
            self.assertTrue(bundle.is_relative_to(self.scenario.root))
            self.assertNotEqual(
                bundle, self.scenario.artifact / "pilot" / f"traceability-{len(observed) - 1}"
            )
            for name in (
                "evidence.json",
                "scope.json",
                "tests.log",
                "candidate-items.xml",
                "candidate-trace.log",
            ):
                self.assertTrue((bundle / name).is_file(), name)
            self.assertEqual(
                json.loads((bundle / "scope.json").read_text()),
                self.scenario.config["traceability_scope"],
            )
            self.assertTrue((Path(context["source"]) / "requirements.md").is_file())
            if len(observed) == 1:
                self.assertEqual(context["check"]["status"], "passed")
                self.assertEqual(context["changed_paths"], ["session.py"])
                item = change(
                    "missing",
                    behavior="Explicit logout",
                    rationale="The inactivity requirement ID is unrelated to the new logout branch; no logout promise or assertion connects to it.",
                    remediation="Document and verify explicit logout, with relevant implementation and test references.",
                )
            else:
                self.assertEqual(context["check"]["status"], "review_required")
                item = change(
                    changed_paths=context["changed_paths"],
                    behavior="Explicit logout",
                    requirement_ids=["req~session-expiration~1", "req~explicit-logout~1"],
                    documentation=["requirements.md: Explicit logout and Session expiration"],
                    verification=["tests/test_session.py: test_logout and test_expiration"],
                    rationale="The approved logout promise and its assertion now connect to the new branch; inactivity behavior is preserved.",
                )
            return specialist(traceability_assessment=[assessed([item])])

        result = self.scenario.invoke(edit, assessment=assessment)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(observed), 2)
        self.assertNotEqual(observed[0]["candidate"], observed[1]["candidate"])
        before = json.loads((self.scenario.artifact / "review-0.json").read_text())
        after = json.loads((self.scenario.artifact / "review-1.json").read_text())
        self.assertEqual(before["verdict"], "CHANGES_REQUESTED")
        self.assertEqual(before["reviews"][0]["blocking_findings"], [])
        self.assertEqual(after["verdict"], "PASS")
        self.assertIn("Missing traceability", (self.scenario.artifact / "review-0.md").read_text())

    def test_missing_assessment_blocks_completion_even_when_checks_pass(self):
        with self.assertRaisesRegex(RuntimeError, "required traceability assessment"):
            self.scenario.invoke(assessment=lambda expected: specialist())
        self.assertEqual(self.scenario.states["pilot"]["traceability"]["status"], "passed")
        self.assertEqual(
            json.loads((self.scenario.artifact / "result.json").read_text())["status"], "FAILED"
        )

    def test_mixed_review_uses_config_enablement_and_filters_the_git_diff(self):
        seed = self.scenario.root / "seed"
        (seed / "session.py").write_text(
            (seed / "session.py").read_text() + "\n# mechanical edit\n"
        )
        (seed / "outside.txt").write_text("Outside traceability scope.\n")
        common.git(["add", "."], cwd=seed)
        common.git(["commit", "-m", "fixture changes"], cwd=seed)
        commit = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        states = {
            "pilot": {**self.scenario.states["pilot"], "source": str(seed), "commit": commit},
            "ordinary": {"traceability": {"status": "passed"}},
        }
        selected = traceability.prepare_review(
            [self.scenario.config, {"project": "ordinary"}], states, self.scenario.root / "review"
        )
        self.assertEqual(set(selected), {"pilot"})
        self.assertEqual(selected["pilot"]["changed_paths"], ["session.py"])
        self.assertEqual(
            traceability.review_scope(self.scenario.config, ["outside.txt"])["changed_paths"], []
        )
        self.assertEqual(selected["pilot"]["scope"], self.scenario.config["traceability_scope"])
        self.assertIsNone(selected["pilot"]["evidence_directory"])
        indexed = selected["pilot"]["requirement_index"]
        self.assertIn("req~session-expiration~1", indexed["candidate"]["ids"])
        self.assertEqual(indexed["candidate"]["source"]["commit"], commit)
        self.assertEqual(indexed["base"]["source"]["commit"], self.scenario.states["pilot"]["base"])

    def test_archive_requirement_index_needs_no_git_and_rejects_absolute_symlinks(self):
        source = self.scenario.root / "archive"
        shutil.copytree(self.scenario.root / "seed", source, ignore=shutil.ignore_patterns(".git"))
        indexed = traceability.requirement_index(
            source, self.scenario.config["traceability_scope"], "archive-sha", archive=True
        )
        self.assertEqual(set(indexed), {"candidate"})
        self.assertIn("req~session-expiration~1", indexed["candidate"]["ids"])
        self.assertEqual(indexed["candidate"]["source"]["kind"], "archive")
        (source / "escape").symlink_to(self.scenario.root / "seed", target_is_directory=True)
        failed = traceability.requirement_index(
            source, self.scenario.config["traceability_scope"], "archive-sha", archive=True
        )
        # Portable releases differ in whether all symlinks or only unsafe
        # targets are rejected; an absolute target must never become evidence.
        self.assertIn("Symlink", failed["candidate"]["error"])

    def test_approved_design_requirements_are_persisted_in_the_exported_task(self):
        self.scenario.request = (
            (Path(__file__).parent / "fixtures/traceability-handoff.md").read_text().strip()
        )
        requirements = (
            re.search(r"```markdown\n(.*?)\n```", self.scenario.request, re.DOTALL).group(1) + "\n"
        )
        baseline = (self.scenario.root / "seed/requirements.md").read_text()

        def implement(root, attempt):
            self.assertIn(self.scenario.request, self.scenario.attempts[-1])
            (root / "requirements.md").write_text(requirements)
            source = root / "session.py"
            source.write_text(
                source.read_text()
                .replace(
                    "def expired(seconds):",
                    "# [impl->req~explicit-logout~1]\ndef expired(seconds, logged_out=False):",
                )
                .replace("return seconds >= 1800", "return logged_out or seconds >= 1800")
            )
            tests = root / "tests/test_session.py"
            tests.write_text(
                tests.read_text()
                + "\n    # [utest->req~explicit-logout~1]\n    def test_explicit_logout(self):\n        self.assertTrue(expired(0, logged_out=True))\n"
            )

        result = self.scenario.invoke(implement)
        state = result["repositories"]["pilot"]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(state["traceability"]["status"], "review_required")
        exported = common.git(
            ["--git-dir", state["repository"], "show", state["commit"] + ":requirements.md"]
        ).stdout
        self.assertEqual(exported, requirements)
        self.assertEqual((self.scenario.root / "seed/requirements.md").read_text(), baseline)
        self.assertTrue(requirements.startswith(baseline))
        record = state["traceability"]
        self.assertEqual(record["matched_commit"], state["commit"])
        self.assertEqual(record["independent_review"]["verdict"], "PASS")

    def test_required_execution_rejects_skipped_linked_test_before_review_or_publication(self):
        scope = self.scenario.config["traceability_scope"]
        scope["tests"] = {
            "format": "junit",
            "command": [
                "python",
                "-m",
                "intentbond.unittest_junit",
                "--start",
                "tests",
                "--links",
                "tests/oft-links.json",
                "--report",
                ".results/tests.xml",
            ],
            "report": ".results/tests.xml",
            "timeout_seconds": 60,
            "execution_links": {
                "format": "junit-properties-v1",
                "artifact_types": ["utest"],
                "required_artifacts": ["utest~expiration"],
            },
        }

        def edit(root, attempt):
            path = root / "tests/test_session.py"
            path.write_text(
                path.read_text().replace(
                    "# [utest->req~session-expiration~1]",
                    "# [utest~expiration~1->req~session-expiration~1]\n    @unittest.skip('not run')",
                )
                + "\n    def test_unlinked(self): self.assertTrue(True)\n"
            )
            (root / "tests/oft-links.json").write_text(
                json.dumps(
                    {
                        "test_session.SessionTests.test_expiration": ["utest~expiration~1"],
                    }
                )
            )

        # [utest~im-required-execution-gate~1->req~im-trace-source-gate~1]
        with self.assertRaisesRegex(RuntimeError, "Configured tests failed"):
            self.scenario.invoke(edit)
        self.assertEqual(self.scenario.review_prompts, [])
        retained = list(self.scenario.artifact.rglob("evidence.json"))
        self.assertTrue(retained)
        outcomes = [json.loads(path.read_text())["predicate"] for path in retained]
        self.assertTrue(
            any(
                "expected passed observations; got skipped" in str(record["diagnostics"])
                for record in outcomes
            )
        )

    def test_successful_test_log_and_report_changes_stay_out_of_review_prompt(self):
        marker = "SUCCESSFUL_TEST_LOG_DETAIL"

        def edit(root, attempt):
            tests = root / "tests/test_session.py"
            tests.write_text(
                tests.read_text()
                + "\n    def test_fresh_session(self):\n        self.assertFalse(expired(0))\n"
                + f'\nprint("{marker}" * 800)\n'
            )

        result = self.scenario.invoke(edit)
        record = result["repositories"]["pilot"]["traceability"]
        prompt = self.scenario.review_prompts[0]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(self.scenario.attempts), 1)
        self.assertEqual(len(self.scenario.review_prompts), 1)
        self.assertEqual(record["status"], "review_required")
        self.assertEqual(record["independent_review"]["verdict"], "PASS")
        self.assertEqual(record["matched_commit"], result["repositories"]["pilot"]["commit"])
        self.assertEqual(self.scenario.contexts, [True])
        self.assertIn(marker, (self.scenario.artifact / "pilot/tests-0.log").read_text())
        self.assertNotIn(marker, prompt)
        self.assertTrue(record["changes"])
        self.assertNotIn(json.dumps(record["changes"]), prompt)
        self.assertIn(record["candidate"]["sha256"], prompt)
        self.assertIn("review_required", prompt)
        self.assertIn("tests-0.log", prompt)

    def test_agent_and_controller_run_the_same_profile_environment(self):
        self.scenario.config["test_profile"] = "traceability"
        self.scenario.config["traceability_scope"]["tests"]["command"] = [
            "bash",
            "-c",
            'bash "$FACTORY_TESTS/run.sh"',
        ]
        result = self.scenario.invoke(feedback=2)
        self.assertEqual(result["status"], "PASSED")
        frozen = json.loads((self.scenario.artifact / "pilot/traceability-scope.json").read_text())
        self.assertEqual(frozen, self.scenario.config["traceability_scope"])
        record = result["repositories"]["pilot"]["traceability"]
        self.assertEqual(record["check_exit_code"], 0)
        index = json.loads(
            (self.scenario.artifact / "pilot/traceability-invocations-0/index.json").read_text()
        )
        self.assertEqual(index["feedback_iterations"], 2)
        self.assertEqual(index["controller_checks"], 1)
        self.assertEqual(len({i["id"] for i in index["invocations"]}), 3)
        self.assertIsNone(index["logical_qualification_cases"])
        self.assertFalse(list(self.scenario.root.glob("job-*")))
        for invocation in index["invocations"]:
            self.assertIn("evidence/evidence.json", invocation["artifacts"])
            self.assertTrue(invocation["completed_at"])

    def test_feedback_survives_a_malformed_response_before_controller_check(self):
        with self.assertRaisesRegex(RuntimeError, "malformed response"):
            self.scenario.invoke(
                feedback=1, implementation_error=RuntimeError("malformed response")
            )
        index = json.loads(
            (self.scenario.artifact / "pilot/traceability-invocations-0/index.json").read_text()
        )
        self.assertEqual(index["feedback_iterations"], 1)
        self.assertEqual(index["controller_checks"], 0)
        self.assertTrue(all(i["bundle"]["status"] == "passed" for i in index["invocations"]))

    def test_interrupted_retention_preserves_the_only_workspace_copy(self):
        from traceability import retention

        with patch.object(retention, "copy_file", side_effect=OSError("interrupted write")):
            # [utest~im-traceability-TraceabilityPipelineTests-interrupted_retention_preserves_the_only_workspace_copy~1->req~im-failed-work-retention~1]
            with self.assertRaisesRegex(RuntimeError, "Could not retain task evidence"):
                self.scenario.invoke(feedback=1)
        recovery = Path((self.scenario.artifact / "recovery-workspace.txt").read_text())
        self.assertTrue(recovery.is_dir())
        self.assertEqual(
            len(list(recovery.glob("traceability/pilot/agent-check-*/evidence/evidence.json"))), 1
        )

    def test_candidate_cannot_weaken_the_frozen_floor(self):
        def edit(root, attempt):
            path = root / "requirements.md"
            path.write_text(path.read_text().replace("impl, utest", "impl"))
            path = root / "tests/test_session.py"
            path.write_text(
                path.read_text().replace("# [utest->req~session-expiration~1]", "# removed")
            )
            (root / "scope.json").write_text('{"required_coverage":{"req":["impl"]}}')

        # [utest~im-traceability-TraceabilityPipelineTests-candidate_cannot_weaken_the_frozen_floor~1->req~im-trace-source-gate~1]
        with self.assertRaises(RuntimeError):
            self.scenario.invoke(edit)
        self.assertEqual(self.scenario.states["pilot"]["traceability"]["status"], "rejected")

    def test_requirement_change_can_be_rejected_by_existing_review(self):
        def edit(root, attempt):
            path = root / "requirements.md"
            path.write_text(path.read_text().replace("30 minutes", "60 minutes"))

        with self.assertRaises(RuntimeError):
            self.scenario.invoke(edit, verdict="CHANGES_REQUESTED")
        record = self.scenario.states["pilot"]["traceability"]
        self.assertEqual(record["status"], "review_required")
        self.assertEqual(record["independent_review"]["verdict"], "CHANGES_REQUESTED")

    def test_exported_contents_must_match_tested_dirty_contents(self):
        original = run.export_task

        def export(workspace, state, destination, task):
            path = Path(state["worktree"]) / "session.py"
            path.write_text(path.read_text() + "\n# changed after checks\n")
            original(workspace, state, destination, task)

        # [utest~im-traceability-TraceabilityPipelineTests-exported_contents_must_match_tested_dirty_contents~1->req~im-trace-source-gate~1]
        with self.assertRaises(RuntimeError):
            self.scenario.invoke(export=export)
        self.assertIn(
            "Candidate contents differ",
            str(self.scenario.states["pilot"]["traceability"]["diagnostics"]),
        )

    def test_missing_evidence_cannot_complete_even_with_passing_review(self):
        from types import SimpleNamespace

        with patch.object(
            traceability, "check", return_value=SimpleNamespace(exit_code=0, stdout="", stderr="")
        ):
            # [utest~im-traceability-TraceabilityPipelineTests-missing_evidence_cannot_complete_even_with_passing_review~1->req~im-trace-source-gate~1]
            with self.assertRaises(RuntimeError):
                self.scenario.invoke()
        self.assertEqual(self.scenario.states["pilot"]["traceability"]["status"], "error")

    def test_failed_current_invocation_cannot_reuse_a_passing_bundle(self):
        from traceability.openhands import check as portable_check

        original = traceability.check

        def fail_with_existing_bundle(workspace, state, paths, env):
            previous = original(workspace, state, paths, env)
            self.assertEqual(previous.exit_code, 0)
            result = portable_check(
                workspace,
                repo=state["worktree"],
                scope=paths["worker_scope"],
                base=state["base"],
                out=paths["out"],
                env=env,
            )
            self.assertEqual(result.exit_code, 2)
            return result

        with patch.object(traceability, "check", fail_with_existing_bundle):
            # [utest~im-traceability-TraceabilityPipelineTests-failed_current_invocation_cannot_reuse_a_passing_bundle~1->req~im-trace-source-gate~1]
            with self.assertRaises(RuntimeError):
                self.scenario.invoke()
        record = self.scenario.states["pilot"]["traceability"]
        self.assertEqual(record["check_exit_code"], 2)
        self.assertEqual(record["status"], "error")
        self.assertNotIn("matched_commit", record)
        retained = json.loads(Path(record["evidence"]).read_text())["predicate"]
        self.assertEqual(retained["status"], "passed")

    def test_opted_out_repository_keeps_its_existing_execution_and_context(self):
        self.scenario.config.pop("traceability_scope")
        self.scenario.config["test_command"] = "python -m unittest discover -s tests -v"
        result = self.scenario.invoke()
        self.assertEqual(result["status"], "PASSED")
        self.assertNotIn("traceability", self.scenario.states["pilot"])
        self.assertEqual(self.scenario.contexts, [False])

    def test_modified_worker_scope_cannot_replace_controller_policy(self):
        original = traceability.check

        def changed(workspace, state, paths, env):
            value = json.loads(paths["worker_scope"].read_text())
            value["policy"]["allow_skipped_tests"] = False
            paths["worker_scope"].write_text(json.dumps(value))
            return original(workspace, state, paths, env)

        # [utest~im-traceability-TraceabilityPipelineTests-modified_worker_scope_cannot_replace_controller_policy~1->req~im-trace-source-gate~1]
        with patch.object(traceability, "check", changed), self.assertRaises(RuntimeError):
            self.scenario.invoke()
        self.assertIn(
            "Scope differs", str(self.scenario.states["pilot"]["traceability"]["diagnostics"])
        )


if __name__ == "__main__":
    unittest.main()
