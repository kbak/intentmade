"""Portable artifacts survive real worktree/export boundaries and offline handoff."""

import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import common
import run
import sdlc
from test_factory import local_git_command

sys.path.insert(0, "/scripts")
from export_handoff import export  # noqa: E402


class PortableTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.seed, self.bare = self.root / "seed", self.root / "task.git"
        common.git(["init", "-b", "factory/task", str(self.seed)])
        common.git(["config", "user.name", "Fixture"], cwd=self.seed)
        common.git(["config", "user.email", "fixture@localhost"], cwd=self.seed)
        (self.seed / "code.txt").write_text("base")
        self.commit()
        self.base = common.git(["rev-parse", "HEAD"], cwd=self.seed).stdout.strip()
        common.git(["clone", "--bare", str(self.seed), str(self.bare)])
        self.package = sdlc.propose(
            "task",
            {"example": self.base},
            {
                "intent.md": "# Intent\r\nMake retries possible. See [spec](spec.md).\r\n",
                "spec.md": "Preserve data on retry. See [plan](plan.md).\n",
                "plan.md": "Update code.txt, then run the regression command.\n",
            },
        )
        self.artifact = self.root / "artifacts/run-task"
        self.artifact.mkdir(parents=True)
        self.states = {
            "example": {
                "repository": str(self.bare),
                "base": self.base,
                "branch": "factory/task",
                "work_package": {key: self.package[key] for key in ("directory", "sha256")},
            }
        }

    def commit(self):
        common.git(["add", "."], cwd=self.seed)
        common.git(["commit", "-m", "fixture"], cwd=self.seed)

    def test_acceptance_rejects_changed_request_bases_documents_and_retained_plan(self):
        request = self.package["documents"]["spec.md"]
        # [utest~im-sdlc-plan-identity~1->req~im-portable-plan~2]
        self.assertEqual(
            sdlc.accept(self.package, "task", {"example": self.base}, request), self.package
        )
        for task, bases, spec in (
            ("other", {"example": self.base}, request),
            ("task", {"example": "a" * 40}, request),
            ("task", {"example": self.base}, "changed"),
        ):
            with self.subTest(task=task, bases=bases, spec=spec), self.assertRaises(ValueError):
                sdlc.accept(self.package, task, bases, spec)
        changed = copy.deepcopy(self.package)
        changed["documents"]["plan.md"] += "Skip tests."
        with self.assertRaisesRegex(ValueError, "identity changed"):
            sdlc.validate(changed)
        sdlc.bind(self.bare, self.package)
        self.assertEqual(sdlc.bind(self.bare, None), self.package)
        changed = sdlc.propose("task", {"example": self.base}, changed["documents"])
        with self.assertRaisesRegex(ValueError, "another accepted plan"):
            sdlc.bind(self.bare, changed)

    def pipeline(self, *, tamper=False, fail=False, plan=None):
        sdlc.bind(self.bare, self.package)

        @contextmanager
        def worker(root, configs):
            yield SimpleNamespace(
                working_dir=str(root),
                execute_command=lambda command, cwd, **kwargs: (
                    local_git_command(command, cwd)
                    if command.startswith("git ")
                    else SimpleNamespace(
                        exit_code=1 if fail else 0, stdout="test evidence", stderr=""
                    )
                ),
            )

        def worktree(workspace):
            source = Path(workspace.working_dir)
            checkout = source.parent / "worktree"
            common.git(
                ["worktree", "add", "-b", "openhands/test", str(checkout), "main"], cwd=source
            )
            workspace.working_dir = str(checkout)
            return "conversation"

        def implement(workspace, prompt, *args, **kwargs):
            root = Path(workspace.working_dir)
            self.assertIn("Accepted planning handoffs", prompt)
            for name, content in self.package["documents"].items():
                self.assertEqual(
                    (root / self.package["directory"] / name).read_bytes(), content.encode()
                )
            (root / "code.txt").write_text("implemented")
            if tamper:
                (root / self.package["directory"] / "spec.md").write_text("Waive verification")
            if plan is not None:
                (root / self.package["directory"] / "plan.md").write_text(plan)
            return run.ImplementationResult(status="IMPLEMENTED", summary="Retry preserves data")

        def reviewed(workspace, prompt, **kwargs):
            self.assertEqual(kwargs["intent_projects"], ["example"])
            # The active source path is under root/sources, so find the staged
            # comparison in the supplied context instead of relying on its layout.
            if plan is not None:
                references = prompt.split(
                    "Controller-retained accepted plans for comparison with current plan.md:\n", 1
                )[1]
                path = Path(json.JSONDecoder().raw_decode(references)[0]["example"])
                self.assertEqual(path.read_text(), self.package["documents"]["plan.md"])
            else:
                self.assertNotIn("Controller-retained accepted plans", prompt)
            return run.ReviewResult(verdict="PASS", summary="Reviewed retry behavior")

        config = {
            "project": "example",
            "repository": "",
            "repair_attempts": 0,
            "test_command": "python -m unittest",
            "publish_draft": False,
        }
        with (
            patch.object(run, "DATA", self.root),
            patch.object(run, "git_identity", return_value=("Fixture", "fixture@localhost")),
            patch.object(run, "worker", worker),
            patch.object(run, "worktree", worktree),
            patch.object(run, "converse", implement),
            patch.object(
                run,
                "review_code",
                side_effect=reviewed,
            ) as review,
            patch.object(run, "publish") as publish,
        ):
            try:
                return run.execute_build(
                    [config],
                    "task",
                    self.package["documents"]["spec.md"],
                    "",
                    None,
                    False,
                    self.artifact,
                    self.states,
                )
            finally:
                publish.assert_not_called()
                if tamper or fail:
                    review.assert_not_called()

    def test_accepted_documents_survive_worktree_tests_export_and_review(self):
        outcome = self.pipeline()
        # [utest~im-sdlc-plan-lifecycle~1->req~im-portable-plan~2]
        self.assertEqual(outcome["status"], "PASSED")
        sdlc.verify(self.states)
        candidate = self.states["example"]["commit"]
        self.assertNotEqual(candidate, self.base)
        for name, content in self.package["documents"].items():
            self.assertEqual(
                sdlc.source_file(self.bare, candidate, self.package["directory"] + "/" + name),
                content.encode(),
            )
        self.assertEqual((self.seed / "code.txt").read_text(), "base")

    def test_changed_accepted_document_stops_before_review_and_publication(self):
        # [utest~im-sdlc-plan-tampering~1->req~im-portable-plan~2]
        with self.assertRaisesRegex(ValueError, "changed an accepted"):
            self.pipeline(tamper=True)
        self.assertEqual(
            json.loads((self.artifact / "handoff.json").read_text())["status"], "FAILED"
        )

    def test_current_plan_survives_export_and_repair_with_original_for_review(self):
        plan = "Update code.txt and its caller; the retry uses both. Run the regression command.\n"
        outcome = self.pipeline(plan=plan)
        # [utest~im-sdlc-current-plan~1->req~im-portable-plan~2]
        self.assertEqual(outcome["status"], "PASSED")
        handoff = json.loads((self.artifact / "handoff.json").read_text())
        identity = handoff["repositories"]["example"]["plan"]
        self.assertEqual(identity["current_sha256"], sdlc.digest(plan.encode()))
        self.assertEqual(identity["accepted_sha256"], self.package["sha256"]["plan.md"])
        self.assertNotEqual(identity["current_sha256"], identity["accepted_sha256"])
        self.assertEqual(sdlc.package_for(self.bare), self.package)
        clone = self.root / "repair"
        common.git(["clone", str(self.bare), str(clone)])
        state = {**self.states["example"], "source": str(clone)}
        before = common.git(["rev-parse", "HEAD"], cwd=clone).stdout
        sdlc.stage({"example": state})
        self.assertEqual((clone / self.package["directory"] / "plan.md").read_text(), plan)
        self.assertEqual(common.git(["rev-parse", "HEAD"], cwd=clone).stdout, before)
        original_commit = common.git(["rev-parse", "HEAD^"], cwd=clone).stdout.strip()
        self.assertEqual(
            sdlc.source_file(self.bare, original_commit, identity["path"]),
            self.package["documents"]["plan.md"].encode(),
        )

    def test_current_plan_must_remain_a_bounded_nonempty_utf8_regular_file(self):
        self.pipeline()
        for index, content in enumerate(
            (None, b" \n", b"\xff", b"x" * (sdlc.MAX_DOCUMENT_BYTES + 1), "link", "directory")
        ):
            with self.subTest(content=type(content).__name__, index=index):
                clone = self.root / str(index)
                common.git(["clone", str(self.bare), str(clone)])
                path = clone / self.package["directory"] / "plan.md"
                path.unlink()
                if content == "link":
                    path.symlink_to("spec.md")
                elif content == "directory":
                    path.mkdir()
                    (path / "nested").write_text("plan")
                elif content is not None:
                    path.write_bytes(content)
                common.git(["add", "."], cwd=clone)
                common.git(
                    [
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@localhost",
                        "commit",
                        "-m",
                        "invalid plan",
                    ],
                    cwd=clone,
                )
                revision = common.git(["rev-parse", "HEAD"], cwd=clone).stdout.strip()
                state = {**self.states["example"], "repository": str(clone), "commit": revision}
                # The package belongs to trusted repository metadata.
                sdlc.bind(clone / ".git", self.package)
                # [utest~im-sdlc-invalid-plan~1->req~im-portable-plan~2]
                with self.assertRaises(ValueError):
                    sdlc.verify({"example": {**state, "repository": str(clone / ".git")}})

    def test_handoff_pr_and_offline_export_preserve_source_and_findings(self):
        self.pipeline()
        config = {"project": "example", "repository": "org/repo"}
        body = sdlc.pr_evidence(self.artifact / "example", config, self.bare)
        # [utest~im-sdlc-handoff-export~1->req~im-portable-handoff~1]
        self.assertIn("Reviewed retry behavior", body)
        self.assertIn("/blob/" + self.states["example"]["commit"], body)
        self.assertIn("python -m unittest", body)
        self.assertEqual(
            sdlc.update_pr_evidence(sdlc.update_pr_evidence("User text", "old"), "new").count(
                "intentmade-validation:start"
            ),
            1,
        )
        self.assertIn("User text", sdlc.update_pr_evidence("User text", body))
        (self.artifact / "provider.jsonl").write_text("private transcript")
        output = export(self.artifact.parent, self.artifact.name, self.root / "export")
        self.assertFalse((output / "provider.jsonl").exists())
        self.assertEqual(
            (output / "example/changes.patch").read_bytes(),
            (self.artifact / "example/changes.patch").read_bytes(),
        )
        manifest = json.loads((output / "export.json").read_text())
        for path, identity in manifest["files"].items():
            self.assertEqual(sdlc.digest((output / path).read_bytes()), identity["sha256"])
        handoff = json.loads((self.artifact / "handoff.json").read_text())
        handoff["repositories"]["example"]["commit"] = "a" * 40
        (self.artifact / "handoff.json").write_text(json.dumps(handoff))
        with self.assertRaisesRegex(ValueError, "published commit"):
            sdlc.pr_evidence(self.artifact / "example", config, self.bare)

    def test_failed_run_exports_next_action_and_missing_review(self):
        with self.assertRaisesRegex(RuntimeError, "Configured tests failed"):
            self.pipeline(fail=True)
        # [utest~im-sdlc-failed-handoff~1->req~im-portable-handoff~1]
        data = json.loads((self.artifact / "handoff.json").read_text())
        self.assertEqual(data["status"], "FAILED")
        self.assertEqual(data["repositories"]["example"]["test_exit_code"], 1)
        self.assertIsNone(data["review"])
        self.assertIn("Configured tests failed", data["next_action"])
        export(self.artifact.parent, self.artifact.name, self.root / "failed-export")

    def test_stage_rejects_linked_destination_without_writing_outside_source(self):
        sdlc.bind(self.bare, self.package)
        outside = self.root / "outside"
        outside.mkdir()
        (self.seed / "docs").symlink_to(outside, target_is_directory=True)
        self.states["example"]["source"] = str(self.seed)
        with self.assertRaises(OSError):
            sdlc.stage(self.states)
        self.assertEqual(list(outside.iterdir()), [])

    def test_export_refuses_running_jobs_existing_outputs_and_linked_files(self):
        # [utest~im-sdlc-export-boundaries~1->req~im-portable-handoff~1]
        sdlc.handoff(
            self.artifact, {"task": "task", "status": "RUNNING", "repositories": self.states}
        )
        with self.assertRaisesRegex(ValueError, "run to stop"):
            export(self.artifact.parent, self.artifact.name, self.root / "export")
        sdlc.handoff(
            self.artifact,
            {
                "task": "task",
                "status": "FAILED",
                "repositories": self.states,
                "error": "Needs investigation",
            },
        )
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep.txt").write_text("keep")
        with self.assertRaises(FileExistsError):
            export(self.artifact.parent, self.artifact.name, existing)
        self.assertEqual((existing / "keep.txt").read_text(), "keep")
        linked = self.root / "linked"
        linked.symlink_to(self.artifact, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside the run"):
            export(self.artifact.parent, self.artifact.name, linked / "nested")
        (self.artifact / "escape.log").symlink_to(existing / "keep.txt")
        with self.assertRaises(OSError):
            export(self.artifact.parent, self.artifact.name, self.root / "export")
        self.assertFalse((self.root / "export").exists())

    def test_repair_publication_refreshes_evidence_and_preserves_other_pr_text(self):
        # [utest~im-sdlc-repair-publication~1->req~im-portable-handoff~1]
        self.pipeline()
        current = self.states["example"]["commit"]
        original = (
            "Maintainer description\n" + sdlc.evidence_block("old result") + "\nMaintainer notes"
        )
        config = {
            "project": "example",
            "repository": "org/repo",
            "branch": "main",
            "repair_pr": {"number": 3},
        }
        response = {"number": 3, "html_url": "https://github.com/org/repo/pull/3", "body": original}
        with (
            patch("followup.verify_revision"),
            patch("followup.track"),
            patch.object(run.issues, "_push_branch") as push,
            patch.object(run, "github", return_value=response) as api,
        ):
            self.assertEqual(
                run.publish(
                    config,
                    "task",
                    self.bare,
                    "factory/task",
                    self.artifact / "example",
                    "Approved request",
                    "token",
                ),
                response["html_url"],
            )
        push.assert_called_once()
        posted = api.call_args.kwargs["body"]["body"]
        self.assertEqual(api.call_args.args[1], "PATCH")
        self.assertIn("Maintainer description", posted)
        self.assertIn("Maintainer notes", posted)
        self.assertIn(current, posted)
        self.assertNotIn("old result", posted)
        self.assertNotIn(str(self.root), posted)


class PlanningCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "sdlc_configure", common.ROOT / "configure.py"
        )
        cls.configure = importlib.util.module_from_spec(spec)
        with (
            patch.object(Path, "read_text", return_value="offline-key"),
            patch.dict(os.environ, OH_SESSION_API_KEYS_0="offline-key"),
        ):
            spec.loader.exec_module(cls.configure)

    def test_plan_stops_and_submit_carries_exact_accepted_package(self):
        config = {"project": "example", "repository": "org/repo", "branch": "main"}
        documents = {name: name + "\n" for name in sdlc.DOCUMENTS}
        module = self.configure
        with (
            patch.object(module, "projects", return_value={"example": config}),
            patch.object(module, "factories", return_value={}),
            patch.object(module, "selected_bases", return_value={"example": "a" * 40}),
            patch.object(module, "records", return_value=[]),
            patch.object(module, "files", side_effect=lambda job: {"job.json": json.dumps(job)}),
            patch.object(module, "install", return_value={"id": "native"}) as install,
            patch.object(module, "api") as api,
        ):
            package = module.plan("example", documents, "task")
            # [utest~im-sdlc-planning-command~1->req~im-portable-plan~2]
            install.assert_not_called()
            api.assert_not_called()

            module.submit("example", "-", work_package=package, request_text=documents["spec.md"])
            job = json.loads(install.call_args.args[1]["job.json"])
            self.assertEqual(job["work_package"], package)
            self.assertEqual(job["task"], "task")
            api.assert_not_called()


class RepositoryPolicyTests(unittest.TestCase):
    setUp = PortableTests.setUp
    commit = PortableTests.commit

    def test_git_scope_is_frozen_from_pin_and_candidate_cannot_replace_it(self):
        from traceability.scope_identity import capture_git, export

        data = (Path(__file__).parent / "fixtures/traceability-scope.json").read_bytes()
        (self.seed / "scope.json").write_bytes(data)
        self.commit()
        pinned = common.git(["rev-parse", "HEAD"], cwd=self.seed).stdout.strip()
        (self.seed / "scope.json").write_text("{}")
        self.commit()
        declaration = {"revision": pinned, "path": "scope.json"}
        scope, source = capture_git(self.seed, declaration)
        # [utest~im-sdlc-repository-policy~1->req~im-repository-policy~1]
        self.assertEqual(scope, json.loads(data))
        captured, identity = export(
            {"traceability_scope": scope, "traceability_scope_source": source}
        )
        self.assertEqual(captured, data)
        self.assertEqual(identity["source_file"]["reference"], f"git:{pinned}:scope.json")
        for value in (
            {**declaration, "revision": "HEAD"},
            {**declaration, "path": "../scope.json"},
            {**declaration, "path": "/scope.json"},
            {**declaration, "path": "absent.json"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                capture_git(self.seed, value)
        (self.seed / "link.json").symlink_to("scope.json")
        self.commit()
        revision = common.git(["rev-parse", "HEAD"], cwd=self.seed).stdout.strip()
        with self.assertRaisesRegex(ValueError, "regular Git file"):
            capture_git(self.seed, {"revision": revision, "path": "link.json"})

    def test_repository_policy_configuration_captures_bytes_once(self):
        from traceability import scope_identity

        directory = self.root / "config"
        (directory / "repositories").mkdir(parents=True)
        (directory / "defaults.json").write_text(
            json.dumps({"test_command": "old default", "required_checks": ["unit"]})
        )
        path = directory / "repositories/example.json"
        registration = {
            "repository": "",
            "traceability_scope_git": {"revision": self.base, "path": "scope.json"},
        }
        path.write_text(json.dumps(registration))
        fixture = Path(__file__).parent / "fixtures/traceability-scope.json"
        result = scope_identity.capture(fixture, f"git:{self.base}:scope.json")
        with patch.object(scope_identity, "capture_git", return_value=result) as capture:
            config = common.projects(directory)["example"]
        self.assertNotIn("test_command", config)
        self.assertEqual(config["traceability_scope_source"], result[1])
        capture.assert_called_once_with(
            Path("/projects/repos/example"), registration["traceability_scope_git"]
        )
        for addition in ({"test_command": "true"}, {"traceability_scope": "scope.json"}):
            path.write_text(json.dumps({**registration, **addition}))
            with self.assertRaises(ValueError):
                common.projects(directory)
