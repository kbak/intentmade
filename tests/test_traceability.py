"""Real Git/OFT/tests through the factory lifecycle; implementation/review are scripted."""

import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import common
import review
import review_report
import run
import traceability
from openhands.sdk.workspace import LocalWorkspace
from test_specialist_review import evidence, specialist
from test_traceability_review import assessed, change


@unittest.skipUnless(
    importlib.util.find_spec("intentbond"),
    "Optional traceability packages require the pilot test image",
)
class TraceabilityPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="factory-traceability-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        seed = self.root / "seed"
        shutil.copytree(Path(__file__).parent / "fixtures/traceability", seed)
        common.git(["init", "-b", "factory/task"], cwd=seed)
        common.git(["config", "user.name", "Fixture"], cwd=seed)
        common.git(["config", "user.email", "fixture@localhost"], cwd=seed)
        common.git(["add", "."], cwd=seed)
        common.git(["commit", "-m", "baseline"], cwd=seed)
        base = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        bare = self.root / "task.git"
        common.git(["clone", "--bare", str(seed), str(bare)])
        self.states = {"pilot": {"repository": str(bare), "base": base, "branch": "factory/task"}}
        self.config = {
            "project": "pilot",
            "repository": "",
            "repair_attempts": 0,
            "publish_draft": False,
            "traceability_scope": json.loads(
                (Path(__file__).parent / "fixtures/traceability-scope.json").read_text()
            ),
        }
        self.artifact = self.root / "artifacts"
        self.artifact.mkdir()
        self.attempts = []
        self.contexts = []
        self.review_prompts = []
        self.request = "Preserve the 30 minute session promise; improve tests."

    def invoke(
        self,
        edit=lambda root, attempt: None,
        verdict="PASS",
        export=None,
        feedback=False,
        assessment=None,
        implementation_error=None,
    ):
        @contextmanager
        def worker(root, configs):
            yield LocalWorkspace(working_dir=str(root / "source"))

        def worktree(workspace, traceability=False):
            self.contexts.append(traceability)
            source = Path(workspace.working_dir)
            checkout = source.parent / "worktree"
            common.git(
                ["worktree", "add", "-b", "openhands/test", str(checkout), "main"], cwd=source
            )
            workspace.working_dir = str(checkout)
            return "scripted-agent"

        def implement(workspace, prompt, *args, **kwargs):
            self.attempts.append(prompt)
            self.assertEqual(kwargs.get("traceability"), "traceability_scope" in self.config)
            edit(Path(workspace.working_dir), len(self.attempts))
            if feedback:
                command = re.search(r"```sh\n(.*?)\n```", prompt, re.DOTALL).group(1)
                for _ in range(2):
                    result = workspace.execute_command(command, timeout=120)
                    self.assertEqual(result.exit_code, 0, result.stdout + result.stderr)
                bundles = list(
                    self.root.glob("job-*/traceability/pilot/agent-check-*/evidence/evidence.json")
                )
                self.assertEqual(len(bundles), 2)
                self.assertTrue(
                    all(
                        json.loads(p.read_text())["predicate"]["status"] == "passed"
                        for p in bundles
                    )
                )
            if implementation_error:
                raise implementation_error
            return run.ImplementationResult(status="IMPLEMENTED", summary="Fixture edit")

        def review_stage(workspace, prompt, **kwargs):
            self.review_prompts.append(prompt)
            expected = kwargs.get("traceability", {})
            code = specialist()
            if expected:
                if assessment:
                    code = assessment(expected)
                else:
                    # These pre-existing cases test tracing/export, not semantic
                    # judgment. Supply explicit scripted review data to the gate.
                    paths = expected["pilot"]["changed_paths"]
                    status = "missing" if verdict == "CHANGES_REQUESTED" else "covered"
                    changes = (
                        [
                            change(
                                status,
                                changed_paths=paths,
                                remediation="Restore the approved session promise."
                                if status == "missing"
                                else None,
                            )
                        ]
                        if paths
                        else []
                    )
                    code = specialist(traceability_assessment=[assessed(changes)])

            def converse(workspace, prompt, **options):
                inputs = json.loads(Path(kwargs["input_path"]).read_text())["projects"]
                code["coverage"] = [
                    {
                        "project": project,
                        **item,
                        "outcome": "reviewed",
                        "evidence": "Scripted fixture review.",
                    }
                    for project, spec in inputs.items()
                    for item in spec["files"]
                ]
                options["event_log"].append(evidence(code=code))

            with (
                patch.object(review, "converse", converse),
                patch.object(
                    review,
                    "consolidate",
                    side_effect=lambda workspace, result, **options: review_report.validate_report(
                        result, review_report.draft_report(result)
                    ),
                ),
            ):
                return review.review_code(workspace, prompt, **kwargs)

        with (
            patch.object(run, "DATA", self.root),
            patch.object(run, "git_identity", return_value=("Fixture", "fixture@localhost")),
            patch.object(run, "worker", worker),
            patch.object(run, "worktree", worktree),
            patch.object(run, "converse", implement),
            patch.object(run, "review_code", side_effect=review_stage) as reviewer,
            patch.object(run, "publish") as publish,
            patch.object(run, "export_task", export or run.export_task),
            patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}),
        ):
            try:
                result = run.execute_build(
                    [self.config],
                    "task",
                    self.request,
                    "",
                    None,
                    False,
                    self.artifact,
                    self.states,
                )
                if "traceability_scope" in self.config:
                    self.assertEqual(set(reviewer.call_args.kwargs["traceability"]), {"pilot"})
                return result
            finally:
                publish.assert_not_called()

    def test_scripted_gap_keeps_green_graph_from_completing_and_repair_is_reassessed(self):
        self.config["repair_attempts"] = 1
        self.request = "Preserve session expiration and add explicit logout, including its documented promise and verification."
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
                self.assertIn("Document and verify explicit logout", self.attempts[-1])
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
            self.assertTrue(bundle.is_relative_to(self.root))
            self.assertNotEqual(
                bundle, self.artifact / "pilot" / f"traceability-{len(observed) - 1}"
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
                json.loads((bundle / "scope.json").read_text()), self.config["traceability_scope"]
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

        result = self.invoke(edit, assessment=assessment)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(observed), 2)
        self.assertNotEqual(observed[0]["candidate"], observed[1]["candidate"])
        before = json.loads((self.artifact / "review-0.json").read_text())
        after = json.loads((self.artifact / "review-1.json").read_text())
        self.assertEqual(before["verdict"], "CHANGES_REQUESTED")
        self.assertEqual(before["reviews"][0]["blocking_findings"], [])
        self.assertEqual(after["verdict"], "PASS")
        self.assertIn("Missing traceability", (self.artifact / "review-0.md").read_text())

    def test_missing_assessment_blocks_completion_even_when_checks_pass(self):
        with self.assertRaisesRegex(RuntimeError, "required traceability assessment"):
            self.invoke(assessment=lambda expected: specialist())
        self.assertEqual(self.states["pilot"]["traceability"]["status"], "passed")
        self.assertEqual(
            json.loads((self.artifact / "result.json").read_text())["status"], "FAILED"
        )

    def test_scripted_mechanical_change_uses_existing_relationships(self):
        def edit(root, attempt):
            path = root / "session.py"
            path.write_text(path.read_text().replace("1800", "30 * 60"))

        def assessment(expected):
            return specialist(
                traceability_assessment=[
                    assessed(
                        [
                            change(
                                "not_needed",
                                requirement_ids=[],
                                documentation=[],
                                implementation=[],
                                verification=[],
                                rationale="The threshold expression is the same value; the existing expiration relationships and boundary tests remain sufficient.",
                            )
                        ]
                    )
                ]
            )

        result = self.invoke(edit, assessment=assessment)
        self.assertEqual(result["status"], "PASSED")
        state = result["repositories"]["pilot"]
        requirements = common.git(
            ["--git-dir", state["repository"], "show", state["commit"] + ":requirements.md"]
        ).stdout
        self.assertEqual(requirements, (self.root / "seed/requirements.md").read_text())

    def test_changes_outside_scope_do_not_acquire_traceability_obligations(self):
        def edit(root, attempt):
            (root / "unrelated.txt").write_text("Documentation outside the configured inputs.\n")

        def assessment(expected):
            self.assertEqual(expected["pilot"]["changed_paths"], [])
            return specialist(traceability_assessment=[assessed([])])

        self.assertEqual(self.invoke(edit, assessment=assessment)["status"], "PASSED")

    def test_mixed_review_uses_config_enablement_and_filters_the_git_diff(self):
        seed = self.root / "seed"
        (seed / "session.py").write_text(
            (seed / "session.py").read_text() + "\n# mechanical edit\n"
        )
        (seed / "outside.txt").write_text("Outside traceability scope.\n")
        common.git(["add", "."], cwd=seed)
        common.git(["commit", "-m", "fixture changes"], cwd=seed)
        commit = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        states = {
            "pilot": {**self.states["pilot"], "source": str(seed), "commit": commit},
            "ordinary": {"traceability": {"status": "passed"}},
        }
        selected = traceability.prepare_review(
            [self.config, {"project": "ordinary"}], states, self.root / "review"
        )
        self.assertEqual(set(selected), {"pilot"})
        self.assertEqual(selected["pilot"]["changed_paths"], ["session.py"])
        self.assertEqual(selected["pilot"]["scope"], self.config["traceability_scope"])
        self.assertIsNone(selected["pilot"]["evidence_directory"])
        indexed = selected["pilot"]["requirement_index"]
        self.assertIn("req~session-expiration~1", indexed["candidate"]["ids"])
        self.assertEqual(indexed["candidate"]["source"]["commit"], commit)
        self.assertEqual(indexed["base"]["source"]["commit"], self.states["pilot"]["base"])

    def test_archive_requirement_index_needs_no_git_and_rejects_absolute_symlinks(self):
        source = self.root / "archive"
        shutil.copytree(self.root / "seed", source, ignore=shutil.ignore_patterns(".git"))
        indexed = traceability.requirement_index(
            source, self.config["traceability_scope"], "archive-sha", archive=True
        )
        self.assertEqual(set(indexed), {"candidate"})
        self.assertIn("req~session-expiration~1", indexed["candidate"]["ids"])
        self.assertEqual(indexed["candidate"]["source"]["kind"], "archive")
        (source / "escape").symlink_to(self.root / "seed", target_is_directory=True)
        failed = traceability.requirement_index(
            source, self.config["traceability_scope"], "archive-sha", archive=True
        )
        # Portable releases differ in whether all symlinks or only unsafe
        # targets are rejected; an absolute target must never become evidence.
        self.assertIn("Symlink", failed["candidate"]["error"])

    def test_ordinary_test_edit_uses_existing_review_without_new_human_approval(self):
        def edit(root, attempt):
            path = root / "tests/test_session.py"
            path.write_text(
                path.read_text()
                + "\n    def test_fresh_session(self):\n        self.assertFalse(expired(0))\n"
            )

        result = self.invoke(edit)
        record = result["repositories"]["pilot"]["traceability"]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(record["status"], "review_required")
        self.assertEqual(record["independent_review"]["verdict"], "PASS")
        self.assertEqual(record["matched_commit"], result["repositories"]["pilot"]["commit"])
        self.assertEqual(self.contexts, [True])

    def test_approved_design_requirements_are_persisted_in_the_exported_task(self):
        self.request = (
            (Path(__file__).parent / "fixtures/traceability-handoff.md").read_text().strip()
        )
        requirements = (
            re.search(r"```markdown\n(.*?)\n```", self.request, re.DOTALL).group(1) + "\n"
        )
        baseline = (self.root / "seed/requirements.md").read_text()

        def implement(root, attempt):
            self.assertIn(self.request, self.attempts[-1])
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

        result = self.invoke(implement)
        state = result["repositories"]["pilot"]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(state["traceability"]["status"], "review_required")
        exported = common.git(
            ["--git-dir", state["repository"], "show", state["commit"] + ":requirements.md"]
        ).stdout
        self.assertEqual(exported, requirements)
        self.assertEqual((self.root / "seed/requirements.md").read_text(), baseline)
        self.assertTrue(requirements.startswith(baseline))
        record = state["traceability"]
        self.assertEqual(record["matched_commit"], state["commit"])
        self.assertEqual(record["independent_review"]["verdict"], "PASS")

    def test_failing_test_is_repaired_and_rechecked_with_context(self):
        self.config["repair_attempts"] = 1

        def edit(root, attempt):
            path = root / "session.py"
            text = path.read_text()
            path.write_text(
                text.replace("1800", "3600") if attempt == 1 else text.replace("3600", "1800")
            )

        self.assertEqual(self.invoke(edit)["status"], "PASSED")
        self.assertEqual(self.contexts, [True, True])

        self.assertIn("AssertionError", self.attempts[1])
        self.assertTrue((self.artifact / "pilot/traceability-0/test-result.json").is_file())
        self.assertTrue((self.artifact / "pilot/traceability-1/test-result.json").is_file())
        statuses = []
        for attempt in (0, 1):
            index = json.loads(
                (self.artifact / f"pilot/traceability-invocations-{attempt}/index.json").read_text()
            )
            statuses.append(index["invocations"][0]["bundle"]["status"])
        self.assertEqual(statuses, ["rejected", "passed"])

    def test_required_execution_rejects_skipped_linked_test_before_review_or_publication(self):
        scope = self.config["traceability_scope"]
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
            self.invoke(edit)
        self.assertEqual(self.review_prompts, [])
        retained = list(self.artifact.rglob("evidence.json"))
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
            tests.write_text(tests.read_text() + f'\nprint("{marker}" * 800)\n')

        result = self.invoke(edit)
        record = result["repositories"]["pilot"]["traceability"]
        prompt = self.review_prompts[0]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(len(self.attempts), 1)
        self.assertEqual(len(self.review_prompts), 1)
        self.assertIn(marker, (self.artifact / "pilot/tests-0.log").read_text())
        self.assertNotIn(marker, prompt)
        self.assertTrue(record["changes"])
        self.assertNotIn(json.dumps(record["changes"]), prompt)
        self.assertIn(record["candidate"]["sha256"], prompt)
        self.assertIn("review_required", prompt)
        self.assertIn("tests-0.log", prompt)

    def test_agent_and_controller_run_the_same_profile_environment(self):
        self.config["test_profile"] = "traceability"
        self.config["traceability_scope"]["tests"]["command"] = [
            "bash",
            "-c",
            'bash "$FACTORY_TESTS/run.sh"',
        ]
        result = self.invoke(feedback=True)
        self.assertEqual(result["status"], "PASSED")
        frozen = json.loads((self.artifact / "pilot/traceability-scope.json").read_text())
        self.assertEqual(frozen, self.config["traceability_scope"])
        record = result["repositories"]["pilot"]["traceability"]
        self.assertEqual(record["check_exit_code"], 0)
        index = json.loads(
            (self.artifact / "pilot/traceability-invocations-0/index.json").read_text()
        )
        self.assertEqual(index["feedback_iterations"], 2)
        self.assertEqual(index["controller_checks"], 1)
        self.assertEqual(len({i["id"] for i in index["invocations"]}), 3)
        self.assertIsNone(index["logical_qualification_cases"])
        self.assertFalse(list(self.root.glob("job-*")))
        for invocation in index["invocations"]:
            self.assertIn("evidence/evidence.json", invocation["artifacts"])
            self.assertTrue(invocation["completed_at"])

    def test_feedback_survives_a_malformed_response_before_controller_check(self):
        with self.assertRaisesRegex(RuntimeError, "malformed response"):
            self.invoke(feedback=True, implementation_error=RuntimeError("malformed response"))
        index = json.loads(
            (self.artifact / "pilot/traceability-invocations-0/index.json").read_text()
        )
        self.assertEqual(index["feedback_iterations"], 2)
        self.assertEqual(index["controller_checks"], 0)
        self.assertTrue(all(i["bundle"]["status"] == "passed" for i in index["invocations"]))

    def test_interrupted_retention_preserves_the_only_workspace_copy(self):
        from traceability import retention

        with patch.object(retention, "copy_file", side_effect=OSError("interrupted write")):
            # [utest~im-traceability-TraceabilityPipelineTests-interrupted_retention_preserves_the_only_workspace_copy~1->req~im-failed-work-retention~1]
            with self.assertRaisesRegex(RuntimeError, "Could not retain task evidence"):
                self.invoke(feedback=True)
        recovery = Path((self.artifact / "recovery-workspace.txt").read_text())
        self.assertTrue(recovery.is_dir())
        self.assertEqual(
            len(list(recovery.glob("traceability/pilot/agent-check-*/evidence/evidence.json"))), 2
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
            self.invoke(edit)
        self.assertEqual(self.states["pilot"]["traceability"]["status"], "rejected")

    def test_requirement_change_can_be_rejected_by_existing_review(self):
        def edit(root, attempt):
            path = root / "requirements.md"
            path.write_text(path.read_text().replace("30 minutes", "60 minutes"))

        with self.assertRaises(RuntimeError):
            self.invoke(edit, verdict="CHANGES_REQUESTED")
        record = self.states["pilot"]["traceability"]
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
            self.invoke(export=export)
        self.assertIn(
            "Candidate contents differ", str(self.states["pilot"]["traceability"]["diagnostics"])
        )

    def test_missing_evidence_cannot_complete_even_with_passing_review(self):
        from types import SimpleNamespace

        with patch.object(
            traceability, "check", return_value=SimpleNamespace(exit_code=0, stdout="", stderr="")
        ):
            # [utest~im-traceability-TraceabilityPipelineTests-missing_evidence_cannot_complete_even_with_passing_review~1->req~im-trace-source-gate~1]
            with self.assertRaises(RuntimeError):
                self.invoke()
        self.assertEqual(self.states["pilot"]["traceability"]["status"], "error")

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
                self.invoke()
        record = self.states["pilot"]["traceability"]
        self.assertEqual(record["check_exit_code"], 2)
        self.assertEqual(record["status"], "error")
        self.assertNotIn("matched_commit", record)
        retained = json.loads(Path(record["evidence"]).read_text())["predicate"]
        self.assertEqual(retained["status"], "passed")

    def test_controller_allocates_new_output_for_each_invocation(self):
        original = traceability.check

        def twice(workspace, state, paths, env):
            first = original(workspace, state, paths, env)
            first_output = paths["out"]
            self.assertEqual(first.exit_code, 0)
            second = original(workspace, state, paths, env)
            self.assertEqual(second.exit_code, 0)
            self.assertNotEqual(paths["out"], first_output)
            self.assertTrue((first_output / "evidence.json").is_file())
            return second

        with patch.object(traceability, "check", twice):
            self.assertEqual(self.invoke()["status"], "PASSED")

    def test_opted_out_repository_keeps_its_existing_execution_and_context(self):
        self.config.pop("traceability_scope")
        self.config["test_command"] = "python -m unittest discover -s tests -v"
        result = self.invoke()
        self.assertEqual(result["status"], "PASSED")
        self.assertNotIn("traceability", self.states["pilot"])
        self.assertEqual(self.contexts, [False])

    def test_modified_worker_scope_cannot_replace_controller_policy(self):
        original = traceability.check

        def changed(workspace, state, paths, env):
            value = json.loads(paths["worker_scope"].read_text())
            value["policy"]["allow_skipped_tests"] = False
            paths["worker_scope"].write_text(json.dumps(value))
            return original(workspace, state, paths, env)

        # [utest~im-traceability-TraceabilityPipelineTests-modified_worker_scope_cannot_replace_controller_policy~1->req~im-trace-source-gate~1]
        with patch.object(traceability, "check", changed), self.assertRaises(RuntimeError):
            self.invoke()
        self.assertIn("Scope differs", str(self.states["pilot"]["traceability"]["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
