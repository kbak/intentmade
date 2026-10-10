"""Exercise the worker/parent Git boundary and independent review lifecycle."""

import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import common
import resource_limits
import run
import transfer
from pipeline_fixture import PipelineScenario, execute_local, seed_repository


class TransferTests(unittest.TestCase):
    def setUp(self):
        identity = patch.object(run, "git_identity", return_value=("Fixture", "fixture@localhost"))
        identity.start()
        self.addCleanup(identity.stop)

    def seed(self, root):
        source, bare = root / "seed", root / "task.git"
        state = seed_repository(source, bare, files={"code.txt": "base\n"})
        return source, state

    def test_agent_branch_rename_retains_current_work_under_the_task_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = self.seed(root)
            state["worktree"] = str(source)
            common.git(["branch", "-m", "fix/issue-name-from-guidance"], cwd=source)
            (source / "code.txt").write_text("valuable uncommitted fix")

            execute = execute_local

            bundle = root / "export.bundle"
            transfer.export_task(SimpleNamespace(execute_command=execute), state, bundle, "task")
            transfer.import_task(state, bundle)
            self.assertEqual(
                common.git(
                    ["--git-dir", state["repository"], "show", "factory/task:code.txt"]
                ).stdout,
                "valuable uncommitted fix",
            )

    def test_unresolved_merge_cannot_be_committed_by_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = self.seed(root)
            common.git(["checkout", "-b", "upstream"], cwd=source)
            (source / "code.txt").write_text("upstream behavior")
            common.git(["commit", "-am", "upstream change"], cwd=source)
            common.git(["checkout", "factory/task"], cwd=source)
            (source / "code.txt").write_text("task behavior")
            common.git(["commit", "-am", "task change"], cwd=source)
            head = common.git(["rev-parse", "HEAD"], cwd=source).stdout
            self.assertEqual(
                common.git(["merge", "upstream"], cwd=source, check=False).returncode, 1
            )
            state["worktree"] = str(source)

            execute = execute_local

            with self.assertRaisesRegex(RuntimeError, "Unresolved merge conflicts"):
                transfer.export_task(
                    SimpleNamespace(execute_command=execute), state, root / "task.bundle", "task"
                )
            self.assertEqual(common.git(["rev-parse", "HEAD"], cwd=source).stdout, head)
            self.assertIn("<<<<<<<", (source / "code.txt").read_text())
            self.assertFalse((root / "task.bundle").exists())

    def test_amplified_patch_is_rejected_without_losing_imported_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = self.seed(root)
            # Git stores one compressed object; the patch repeats it per path.
            for index in range(32):
                (source / f"repeated-{index}.txt").write_text("repeated content\n" * 4096)
            common.git(["add", "."], cwd=source)
            common.git(["commit", "-m", "repeated blob"], cwd=source)
            bundle = root / "task.bundle"
            common.git(
                ["bundle", "create", str(bundle), "factory/task", "^" + state["base"]], cwd=source
            )
            # [utest~im-transfer-TransferTests-amplified_patch_is_rejected_without_losing_imported_work~1->req~im-bounded-git~1]
            self.assertLess(bundle.stat().st_size, 16 * 1024)
            transfer.import_task(state, bundle)
            artifact = root / "artifact"
            artifact.mkdir()
            destination = artifact / "changes.patch"
            with patch.object(
                transfer, "settings", return_value=resource_limits.validate({"max_patch_mb": 1})
            ):
                with self.assertRaisesRegex(RuntimeError, "Task patch exceeds"):
                    transfer.retain_patch(state, destination)
                self.assertEqual(list(artifact.iterdir()), [])
                destination.write_text("previous complete patch")
                with self.assertRaisesRegex(RuntimeError, "Task patch exceeds"):
                    transfer.retain_patch(state, destination)
                self.assertEqual(destination.read_text(), "previous complete patch")
                self.assertEqual(list(artifact.iterdir()), [destination])
            self.assertEqual(
                common.git(
                    ["--git-dir", state["repository"], "rev-parse", state["branch"]]
                ).stdout.strip(),
                state["commit"],
            )
            # A deliberate higher cap retains the entire patch, never a prefix.
            with patch.object(
                transfer, "settings", return_value=resource_limits.validate({"max_patch_mb": 4})
            ):
                transfer.retain_patch(state, destination)
            expected = common.git(
                [
                    "--git-dir",
                    state["repository"],
                    "diff",
                    "--binary",
                    state["base"],
                    state["commit"],
                ]
            ).stdout
            self.assertGreater(len(expected), resource_limits.MIB)
            self.assertEqual(destination.read_bytes(), expected.encode())

    def test_group_retains_later_valid_export_when_first_bundle_is_corrupt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifact"
            artifact.mkdir()
            configs, states = [], {}
            for project in ("first", "second"):
                directory = root / project
                directory.mkdir()
                _, states[project] = self.seed(directory)
                configs.append({"project": project, "test_command": "true"})

            @contextmanager
            def worker(job, configs):
                execute = execute_local

                yield SimpleNamespace(working_dir=str(job / "source"), execute_command=execute)

            worktree = PipelineScenario(root).worktree

            def converse(workspace, *args, **kwargs):
                for project in states:
                    (Path(workspace.working_dir) / project / "code.txt").write_text(
                        "retained change\n"
                    )
                return run.ImplementationResult(status="IMPLEMENTED", summary="Implemented")

            def export(workspace, state, destination, task):
                transfer.export_task(workspace, state, destination, task)
                if destination.name == "first.bundle":
                    destination.write_text("invalid bundle")

            with (
                patch.object(run, "DATA", root),
                patch.object(run, "worker", worker),
                patch.object(run, "worktree", worktree),
                patch.object(run, "converse", converse),
                patch.object(run, "export_task", export),
            ):
                with self.assertRaisesRegex(RuntimeError, "Could not retain task evidence: first"):
                    run.implementation_attempt(
                        configs, states, "task", "approved group", artifact, 0
                    )
            self.assertEqual(
                common.git(
                    ["--git-dir", states["second"]["repository"], "show", "factory/task:code.txt"]
                ).stdout,
                "retained change\n",
            )
            self.assertEqual(len(list(root.glob("job-*"))), 1)
            self.assertTrue((artifact / "recovery-workspace.txt").exists())

    def test_reject_symlink_and_fifo_exports_without_reading_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = self.seed(root)
            bundle = root / "untrusted.bundle"
            bundle.symlink_to(source / "code.txt")
            # [utest~im-transfer-TransferTests-reject_symlink_and_fifo_exports_without_reading_them~1->req~im-git-transfer~1]
            with self.assertRaises(OSError):
                transfer.import_task(state, bundle)
            bundle.unlink()
            os.mkfifo(bundle)
            with self.assertRaisesRegex(RuntimeError, "regular bundle"):
                transfer.import_task(state, bundle)

    def test_reject_unexpected_refs_and_rewritten_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, state = self.seed(root)
            bundle = root / "untrusted.bundle"
            common.git(["branch", "other"], cwd=source)
            common.git(["bundle", "create", str(bundle), "other"], cwd=source)
            # [utest~im-transfer-TransferTests-reject_unexpected_refs_and_rewritten_history~1->req~im-git-transfer~1]
            with self.assertRaisesRegex(RuntimeError, "unexpected refs"):
                transfer.import_task(state, bundle)
            common.git(["checkout", "--orphan", "unrelated"], cwd=source)
            common.git(["commit", "-m", "unrelated history"], cwd=source)
            common.git(["branch", "-M", "factory/task"], cwd=source)
            common.git(["bundle", "create", str(bundle), "factory/task"], cwd=source)
            with self.assertRaises(RuntimeError):
                transfer.import_task(state, bundle)
            self.assertEqual(
                common.git(
                    ["--git-dir", state["repository"], "rev-parse", "factory/task"]
                ).stdout.strip(),
                state["base"],
            )

    def test_hostile_git_config_stays_in_worker_and_review_uses_fresh_clone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, state = self.seed(root)
            artifact = root / "artifact"
            artifact.mkdir()
            parent_marker, worker_marker = root / "parent-marker", root / "worker-marker"
            active_workers, builder_roots = [], []

            @contextmanager
            def worker(job, configs):
                def execute(command, cwd, **kwargs):
                    result = subprocess.run(
                        ["bash", "-c", command],
                        cwd=cwd,
                        env={**os.environ, "FACTORY_BOUNDARY_FILE": str(worker_marker)},
                        capture_output=True,
                        text=True,
                    )
                    return SimpleNamespace(
                        exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr
                    )

                self.assertFalse(active_workers, "Implementation and review workers overlap")
                active_workers.append(job)
                try:
                    yield SimpleNamespace(working_dir=str(job / "source"), execute_command=execute)
                finally:
                    active_workers.remove(job)

            worktree = PipelineScenario(root).worktree

            def converse(workspace, prompt, mode="read-only", *args, **kwargs):
                checkout = Path(workspace.working_dir)
                if mode == "agent-full-access":
                    self.assertIn("Approved fixture", prompt)
                    if builder_roots:
                        self.assertIn("Fix the failed tests and blocking review findings", prompt)
                    builder_roots.append(active_workers[0])
                    (checkout / "code.txt").write_text("implemented\n")
                    subprocess.run(
                        [
                            "git",
                            "config",
                            "core.fsmonitor",
                            'printf worker-command > "$FACTORY_BOUNDARY_FILE"; false #',
                        ],
                        cwd=checkout,
                        check=True,
                    )
                    return run.ImplementationResult(status="IMPLEMENTED", summary="Implemented")
                self.assertNotEqual(active_workers[0], builder_roots[-1])
                self.assertFalse(builder_roots[-1].exists())
                self.assertEqual((checkout / "code.txt").read_text(), "implemented\n")
                self.assertEqual(
                    common.git(
                        ["config", "--get", "core.fsmonitor"], cwd=checkout, check=False
                    ).returncode,
                    1,
                )
                return run.ReviewResult(
                    verdict="CHANGES_REQUESTED" if len(builder_roots) == 1 else "PASS",
                    summary="Fresh immutable commit reviewed",
                )

            config = {
                "project": "example",
                "repository": "org/repo",
                "repair_attempts": 1,
                "test_command": "test -f code.txt",
                "publish_draft": False,
            }
            with (
                patch.dict(os.environ, {"FACTORY_BOUNDARY_FILE": str(parent_marker)}),
                patch.object(run, "DATA", root),
                patch.object(run, "worker", worker),
                patch.object(run, "worktree", worktree),
                patch.object(run, "converse", converse),
                patch.object(run, "review_code", converse),
            ):
                result = run.execute_build(
                    [config],
                    "task",
                    "Approved fixture",
                    "",
                    None,
                    False,
                    artifact,
                    {"example": state},
                )
            # [utest~im-transfer-TransferTests-hostile_git_config_stays_in_worker_and_review_uses_fresh_clone~1->req~im-git-transfer~1]
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(len(builder_roots), 2)
            self.assertTrue(worker_marker.exists())
            self.assertFalse(parent_marker.exists(), "Worker metadata executed in the parent")
            self.assertEqual(
                common.git(
                    ["--git-dir", state["repository"], "show", "factory/task:code.txt"]
                ).stdout,
                "implemented\n",
            )


if __name__ == "__main__":
    unittest.main()
