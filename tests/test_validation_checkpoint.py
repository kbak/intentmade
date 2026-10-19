"""Review outages reuse exact controller checks; changed or incomplete work does not."""

import copy
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
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
                status="IMPLEMENTED",
                summary="Retained implementation; review pending",
                change_summary="Preserve accepted values across retries.",
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
        # [utest->req~im-publication-gate~1]
        self.assertEqual(
            self.states["app"]["change_summary"], "Preserve accepted values across retries."
        )
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

    def test_cached_profile_changes_invalidate_an_otherwise_reusable_checkpoint(self):
        profile = self.root / "profile"
        profile.mkdir()
        library = profile / "libLean.a"
        library.write_bytes(b"accepted library")
        self.environment.side_effect = lambda configs: {
            **ENVIRONMENT,
            "profiles": {"fixture": checkpoint.tree_identity(profile)},
        }
        self.blocked()
        self.blocked(retry=True)
        # [utest~im-review-validation-profile-reuse~1->req~im-review-validation-reuse~1]
        self.assertEqual(self.counter.read_text().splitlines(), ["check"])
        library.write_bytes(b"modified library")
        self.review.return_value = run.ReviewResult(verdict="PASS", summary="Fresh review")
        result = self.invoke(retry=True)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(self.counter.read_text().splitlines(), ["check", "check"])
        self.assertEqual(self.implement.call_count, 2)
        decision = json.loads((self.root / "run-3/validation-selection.json").read_text())
        self.assertEqual(decision["strategy"], "fresh_validation")
        self.assertIn("environment changed", decision["reason"])

    def test_profile_failure_logs_the_reason_and_preserves_fresh_validation(self):
        self.environment.side_effect = PermissionError("Cannot read profile/libLean.a")
        self.review.return_value = run.ReviewResult(verdict="PASS", summary="Fresh review")
        with self.assertLogs("validation_checkpoint", level="WARNING") as messages:
            result = self.invoke(retry=True)
        # [utest~im-review-validation-profile-diagnostics~1->req~im-review-validation-reuse~1]
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(self.counter.read_text().splitlines(), ["check"])
        self.assertIn("PermissionError: Cannot read profile/libLean.a", messages.output[0])
        self.assertFalse((Path(self.state["repository"]) / checkpoint.POINTER).exists())

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
    def test_toolchain_files_larger_than_the_upload_limit_are_streamed_and_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "libLean.a"
            # A sparse file exercises the real 256 MiB boundary without a large fixture.
            with library.open("wb") as handle:
                handle.truncate(checkpoint.inputs.MAX_FILE + 1)
            with patch.object(Path, "read_bytes", side_effect=AssertionError("Unbounded read")):
                first = checkpoint.tree_identity(root)
                with library.open("r+b") as handle:
                    handle.seek(-1, os.SEEK_END)
                    handle.write(b"x")
                changed = checkpoint.tree_identity(root)
            # [utest~im-review-validation-large-profile~1->req~im-review-validation-reuse~1]
            self.assertNotEqual(first, changed)
            self.assertEqual(library.stat().st_size, checkpoint.inputs.MAX_FILE + 1)
            self.assertEqual(checkpoint.inputs.MAX_FILE, 256 * 1024 * 1024)

    def test_profile_file_total_entry_and_elapsed_budgets_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "libLean.a").write_bytes(b"12345678")
            (root / "other").write_bytes(b"12345678")
            cases = [
                ("MAX_PROFILE_FILE", 7, "file exceeds 7 bytes"),
                ("MAX_PROFILE_TOTAL", 15, "exceeds 15 total bytes"),
                ("MAX_PROFILE_ENTRIES", 2, "exceeds 2 entries"),
                ("PROFILE_TIMEOUT", -1, "observation exceeded"),
            ]
            for name, limit, reason in cases:
                with self.subTest(name=name), patch.object(checkpoint, name, limit):
                    # [utest~im-review-validation-profile-bounds~1->req~im-review-validation-reuse~1]
                    with self.assertRaisesRegex(ValueError, reason) as failure:
                        checkpoint.tree_identity(root)
                    self.assertIn(str(root), str(failure.exception))

    def test_profile_changes_during_and_after_reads_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "libLean.a"
            library.write_bytes(b"original")
            original_open = os.fdopen

            @contextmanager
            def changing_read(*args, **kwargs):
                with original_open(*args, **kwargs) as source:

                    def read(size):
                        self.assertLessEqual(size, checkpoint.HASH_CHUNK)
                        data = source.read(size)
                        if data:
                            before = library.stat()
                            library.write_bytes(b"modified")
                            os.utime(library, ns=(before.st_atime_ns, before.st_mtime_ns))
                        return data

                    yield SimpleNamespace(read=read, fileno=source.fileno)

            with patch.object(checkpoint.os, "fdopen", side_effect=changing_read):
                # [utest~im-review-validation-profile-mutation~1->req~im-review-validation-reuse~1]
                with self.assertRaisesRegex(ValueError, "changed while hashing.*libLean.a"):
                    checkpoint.tree_identity(root)

            inventory = checkpoint.profile_inventory
            calls = 0

            def changed_inventory(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    (root / "new-directory").mkdir()
                return inventory(*args)

            with patch.object(checkpoint, "profile_inventory", side_effect=changed_inventory):
                with self.assertRaisesRegex(ValueError, "inventory or metadata changed"):
                    checkpoint.tree_identity(root)

    def test_profile_directory_links_special_files_and_replaced_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "regular files.*link"):
                checkpoint.tree_identity(root)
            with self.assertRaisesRegex(ValueError, "regular files.*link"):
                checkpoint.tree_identity(link)
            link.unlink()
            os.mkfifo(link)
            with self.assertRaisesRegex(ValueError, "regular files.*link"):
                checkpoint.tree_identity(root)
            link.unlink()
            library = root / "libLean.a"
            library.write_bytes(b"accepted")
            original_open = os.open

            def replaced_open(path, flags):
                library.unlink()
                library.symlink_to(target / "outside")
                return original_open(path, flags)

            with patch.object(checkpoint.os, "open", side_effect=replaced_open):
                with self.assertRaises(OSError):
                    checkpoint.tree_identity(root)

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
            current = checkpoint.tree_identity(root)
            empty = root / "empty"
            empty.mkdir()
            self.assertNotEqual(current, checkpoint.tree_identity(root))
            current = checkpoint.tree_identity(root)
            empty.chmod(0o700)
            self.assertNotEqual(current, checkpoint.tree_identity(root))
            current = checkpoint.tree_identity(root)
            (root / "ignored.pyc").write_bytes(b"bytecode")
            self.assertEqual(current, checkpoint.tree_identity(root))
            (root / "data.pyc").mkdir()
            (root / "data.pyc/input").write_bytes(b"validation input")
            self.assertNotEqual(current, checkpoint.tree_identity(root))
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
