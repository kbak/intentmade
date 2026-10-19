"""Review outages reuse exact controller checks; changed or incomplete work does not."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import common
import run
import validation_checkpoint as checkpoint
from pipeline_fixture import PipelineScenario, TraceabilityScenario, seed_repository

ENVIRONMENT = {"image_id": "sha256:fixture", "profiles": {"fixture": "profile-digest"}}


class ValidationCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = seed_repository(
            self.root / "seed", self.root / "task.git", files={"value.txt": "accepted\n"}
        )
        self.states = {"app": self.state}
        self.counter = self.root / "checks"
        self.config = {
            "project": "app",
            "repository": "",
            "repair_attempts": 1,
            "publish_draft": False,
            "test_command": f"echo check >> {self.counter}",
        }
        self.scenario = PipelineScenario(self.root)
        self.implement = Mock(
            return_value=run.ImplementationResult(
                status="IMPLEMENTED", summary="Retained implementation"
            )
        )
        self.review = Mock(return_value=run.ReviewResult(verdict="BLOCKED", summary="Capacity"))
        env = patch.object(checkpoint, "environment", return_value=ENVIRONMENT)
        self.environment = env.start()
        self.addCleanup(env.stop)
        self.number = 0

    def invoke(self, *, retry=False, request="Approved request"):
        self.number += 1
        artifact = self.root / f"run-{self.number}"
        artifact.mkdir()
        with (
            self.scenario.activate(implement=self.implement),
            patch.object(run, "review_changes", self.review),
        ):
            return run.execute_build(
                [self.config],
                "task",
                request,
                "",
                None,
                False,
                artifact,
                self.states,
                resume_review=retry,
            )

    def blocked(self, **kwargs):
        with self.assertRaisesRegex(RuntimeError, "review infrastructure blocked"):
            self.invoke(**kwargs)

    def test_explicit_retry_reuses_tests_and_requires_fresh_review(self):
        self.blocked()
        original = (self.root / "run-1/app/tests-0.log").read_bytes()
        # Match the real restart: only task/base/branch survive in the new state.
        self.states = {"app": {key: self.state[key] for key in ("base", "branch", "repository")}}
        self.blocked(retry=True)
        self.review.return_value = run.ReviewResult(verdict="PASS", summary="Fresh review")
        result = self.invoke(retry=True)
        # [utest~im-review-validation-retry~1->req~im-review-validation-reuse~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(self.counter.read_text().splitlines(), ["check"])
        self.implement.assert_called_once()
        self.assertEqual(self.review.call_count, 3)
        self.assertEqual((self.root / "run-1/app/tests-0.log").read_bytes(), original)
        progress = json.loads((self.root / "run-3/stage-progress.json").read_text())
        self.assertEqual(progress["stages"]["implementation"]["status"], "Reused")
        self.assertEqual(progress["stages"]["tests"]["status"], "Reused")
        self.assertEqual(progress["stages"]["review"]["status"], "Passed")
        self.assertFalse((Path(self.state["repository"]) / checkpoint.POINTER).exists())

    def test_corrupt_evidence_falls_back_to_full_validation(self):
        self.blocked()
        (self.root / "run-1/app/tests-0.log").write_text("replaced evidence")
        self.review.return_value = run.ReviewResult(verdict="PASS", summary="Fresh review")
        result = self.invoke(retry=True)
        # [utest~im-review-validation-evidence~1->req~im-review-validation-reuse~1,req~im-trace-source-gate~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(self.counter.read_text().splitlines(), ["check", "check"])
        decision = json.loads((self.root / "run-2/validation-selection.json").read_text())
        self.assertEqual(decision["strategy"], "fresh_validation")
        self.assertIn("Checkpoint rejected", decision["reason"])

    def test_issue_build_passes_control_retry_to_checkpoint_selection(self):
        common.git(
            ["--git-dir", self.state["repository"], "config", "factory.base", self.state["base"]]
        )
        for answer, initial, retry in (
            ("retry", False, True),
            ("retry", True, False),
            ("Change the accepted behavior", False, False),
        ):
            with self.subTest(answer=answer, initial=initial):
                artifact = self.root / ("initial" if initial else "retry" if retry else "change")
                artifact.mkdir()
                with (
                    self.scenario.activate(),
                    patch.object(run, "job_id", return_value="fixture"),
                    patch.object(run, "evidence", return_value=artifact),
                    patch.object(
                        run,
                        "task_repository",
                        return_value=(Path(self.state["repository"]), self.state["branch"]),
                    ),
                    patch.object(
                        run, "execute_build", return_value={"status": "PASSED"}
                    ) as execute,
                ):
                    run.build(
                        self.config,
                        "issue-51",
                        "Approved request",
                        self.state["base"],
                        continuation={
                            "answers": [answer],
                            "failure": "Review unavailable",
                            "initial": initial,
                        },
                    )
                self.assertEqual(execute.call_args.kwargs["resume_review"], retry)
                expected = "Approved request"
                if answer != "retry":
                    expected += "\n\nMaintainer responses:\n" + answer
                self.assertEqual(execute.call_args.kwargs["validation_request"], expected)

    def test_request_policy_source_inputs_environment_and_group_must_match(self):
        self.blocked()
        for change in (
            "request",
            "policy",
            "base",
            "source",
            "inputs",
            "runtime",
            "group",
            "feedback",
        ):
            with self.subTest(change=change):
                configs, states = [copy.deepcopy(self.config)], copy.deepcopy(self.states)
                request, observed = "Approved request", ENVIRONMENT
                input_token = None
                if change == "request":
                    request += "; add a new feature"
                if change == "policy":
                    configs[0]["validation_baseline_manifest"] = True
                if change == "base":
                    states["app"]["base"] = "changed-base"
                if change == "source":
                    args = ["--git-dir", self.state["repository"]]
                    tree = common.git(
                        [*args, "rev-parse", self.state["commit"] + "^{tree}"]
                    ).stdout.strip()
                    other = common.git(
                        [
                            *args,
                            "-c",
                            "user.name=Fixture",
                            "-c",
                            "user.email=fixture@localhost",
                            "commit-tree",
                            tree,
                            "-p",
                            self.state["commit"],
                            "-m",
                            "changed commit",
                        ]
                    ).stdout.strip()
                    common.git([*args, "update-ref", "refs/heads/other", other])
                    states["app"]["branch"] = "other"
                if change == "inputs":
                    input_token = checkpoint.inputs.CURRENT.set({"manifest_sha256": "changed"})
                if change == "runtime":
                    observed = {**ENVIRONMENT, "image_id": "sha256:new"}
                if change == "group":
                    states["other"] = dict(states["app"])
                    configs.append({**configs[0], "project": "other"})
                if change == "feedback":
                    configs[0]["repair_pr"] = {"feedback": [{"id": "new-feedback"}]}
                try:
                    found, reason = checkpoint.previous(configs, states, "task", request, observed)
                finally:
                    if input_token is not None:
                        checkpoint.inputs.CURRENT.reset(input_token)
                # [utest~im-review-validation-identity~1->req~im-review-validation-reuse~1]
                self.assertIsNone(found)
                self.assertIn("Checkpoint rejected", reason)

    def test_non_retry_and_failed_new_check_cannot_reuse_older_success(self):
        self.blocked()
        self.config.update(repair_attempts=0, test_command="exit 1")
        with self.assertRaisesRegex(RuntimeError, "Configured tests failed"):
            self.invoke()  # New work consumes the old checkpoint before running.
        self.assertFalse((Path(self.state["repository"]) / checkpoint.POINTER).exists())
        self.config["test_command"] = f"echo check >> {self.counter}"
        self.review.return_value = run.ReviewResult(verdict="PASS", summary="Fresh review")
        self.invoke(retry=True)
        # [utest~im-review-validation-invalidation~1->req~im-review-validation-reuse~1,req~im-trace-source-gate~1]
        self.assertEqual(self.implement.call_count, 3)
        self.assertEqual(self.counter.read_text().splitlines(), ["check", "check"])

    def test_fresh_review_findings_trigger_normal_repair_and_validation(self):
        self.blocked()
        self.review.side_effect = [
            run.ReviewResult(verdict="CHANGES_REQUESTED", summary="Concrete defect"),
            run.ReviewResult(verdict="PASS", summary="Repair reviewed"),
        ]
        result = self.invoke(retry=True)
        # [utest~im-review-validation-repair~1->req~im-review-validation-reuse~1,req~im-bounded-repair~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(self.counter.read_text().splitlines(), ["check", "check"])
        self.assertEqual(self.implement.call_count, 2)

    def test_unknown_or_changed_environment_disables_checkpoint(self):
        for observations in ([None, None], [ENVIRONMENT, {"image_id": "changed"}]):
            with self.subTest(observations=observations):
                self.environment.side_effect = observations
                self.blocked()
                self.assertFalse((Path(self.state["repository"]) / checkpoint.POINTER).exists())
                self.environment.side_effect = None

    def test_retry_is_control_input_and_substantive_answers_remain_bound(self):
        # [utest~im-review-validation-request~1->req~im-review-validation-reuse~1,req~im-explicit-resume~1]
        self.assertEqual(
            checkpoint.responses(["retry", " keep all proofs ", " RETRY "]), [" keep all proofs "]
        )
        self.assertTrue(checkpoint.is_retry(" RETRY "))
        self.assertFalse(checkpoint.is_retry("retry and change the tests"))

    def test_traceability_restart_reverifies_evidence_without_rerunning_tests(self):
        fixture = TraceabilityScenario(self, self.root / "trace")
        with patch.object(run, "review_changes", self.review):
            with self.assertRaisesRegex(RuntimeError, "review infrastructure blocked"):
                fixture.invoke()
        old_commit = fixture.states["pilot"]["commit"]
        old_evidence = Path(fixture.states["pilot"]["traceability"]["evidence"]).read_bytes()
        fixture.artifact = self.root / "trace-retry"
        fixture.artifact.mkdir()
        with patch.object(
            run, "implementation_attempt", side_effect=AssertionError("Repeated checks")
        ):
            result = fixture.invoke(resume_review=True)
        # [utest~im-review-validation-traceability~1->req~im-review-validation-reuse~1,req~im-trace-source-gate~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(fixture.states["pilot"]["commit"], old_commit)
        receipt = fixture.states["pilot"]["traceability"]
        self.assertEqual(Path(receipt["evidence"]).read_bytes(), old_evidence)
        self.assertEqual(receipt["independent_review"]["verdict"], "PASS")
        self.assertEqual(fixture.states["pilot"]["assurance"]["status"], "satisfied")


class ValidationEnvironmentTests(unittest.TestCase):
    def test_profiles_bind_contents_and_modes_and_reject_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "check.sh"
            script.write_text("exit 0\n")
            first = checkpoint.tree_identity(root)
            script.chmod(0o755)
            executable = checkpoint.tree_identity(root)
            self.assertNotEqual(first, executable)
            script.write_text("exit 1\n")
            self.assertNotEqual(executable, checkpoint.tree_identity(root))
            (root / "linked").symlink_to(script)
            with self.assertRaisesRegex(ValueError, "regular files"):
                checkpoint.tree_identity(root)

    def test_kit_changes_and_retargeted_image_tags_change_identity(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kit = root / "spec.yaml"
            kit.write_text("sandbox:\n  image: factory:worker\n")
            profiles = root / "profiles"
            (profiles / "app").mkdir(parents=True)
            (profiles / "app/check.sh").write_text("exit 0\n")
            entry = {
                "repository": "docker.io/library/factory",
                "tag": "worker",
                "id": "0123456789ab",
                "created_at": "2026-10-18T00:00:00Z",
                "size": 1234,
            }

            def catalog(*args, **kwargs):
                self.assertEqual(args[0], ["sbx", "template", "ls", "--json"])
                return SimpleNamespace(stdout=json.dumps({"images": [entry]}))

            with (
                patch.object(
                    checkpoint.deployment,
                    "settings",
                    return_value={
                        "worker_runtime": {
                            "backend": "docker-sandboxes",
                            "kit": str(root),
                            "profiles": str(profiles),
                        }
                    },
                ),
                patch.object(checkpoint.provenance, "runtime", return_value={"code": "same"}),
                patch.object(
                    checkpoint.provenance,
                    "docker_json",
                    side_effect=AssertionError("No Docker socket"),
                ),
                patch.object(checkpoint.subprocess, "run", side_effect=catalog),
            ):
                first = checkpoint.environment([{"test_profile": "app"}])
                entry["id"] = "fedcba987654"
                self.assertNotEqual(first, checkpoint.environment([{"test_profile": "app"}]))
                entry["id"] = "0123456789ab"
                kit.write_text(kit.read_text() + "permissions: {}\n")
                self.assertNotEqual(first, checkpoint.environment([{"test_profile": "app"}]))
                self.assertIn("app", first["profiles"])

    def test_native_catalog_rejects_unknown_ambiguous_and_incomplete_images(self):
        entry = {
            "repository": "docker.io/library/factory",
            "tag": "worker",
            "id": "0123456789ab",
            "created_at": "2026-10-18T00:00:00Z",
            "size": 1234,
        }
        for images in ([], [entry, entry], [{**entry, "id": "unknown"}]):
            with self.subTest(images=images), self.assertRaises(ValueError):
                checkpoint.native_image({"images": images}, "factory:worker")
        for name in (
            "factory:worker",
            "docker.io/factory:worker",
            "docker.io/library/factory:worker",
        ):
            self.assertEqual(checkpoint.native_image({"images": [entry]}, name)["id"], entry["id"])


if __name__ == "__main__":
    unittest.main()
