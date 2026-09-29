"""Worker links cannot redirect controller writes into protected storage."""

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import job_files
import run
import traceability


class JobFileTests(unittest.TestCase):
    def test_existing_symlink_hardlink_and_fifo_are_replaced_without_following(self):
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                job = root / "job"
                job.mkdir()
                sentinel = root / "controller-only"
                sentinel.write_text("protected")
                target = job / ".factory-execution.json"
                if kind == "symlink":
                    target.symlink_to(sentinel)
                elif kind == "hardlink":
                    os.link(sentinel, target)
                else:
                    os.mkfifo(target)
                job_files.write_text(job, target, "new manifest")
                # [utest~im-job_files-JobFileTests-existing_symlink_hardlink_and_fifo_are_replaced_without_following~1->req~im-safe-controller-writes~1]
                self.assertEqual(sentinel.read_text(), "protected")
                self.assertEqual(target.read_text(), "new manifest")
                self.assertFalse(target.is_symlink())
                self.assertEqual(target.stat().st_nlink, 1)

    def test_linked_ancestor_and_escaping_path_cannot_receive_controller_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job, outside = root / "job", root / "outside"
            job.mkdir()
            outside.mkdir()
            (job / "checks").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                job_files.write_text(job, job / "checks/log", "unsafe")
            with self.assertRaises(OSError):
                job_files.mkdir(job, job / "checks/invocation")
            with self.assertRaises(ValueError):
                job_files.write_text(job, job / "../outside/log", "unsafe")
            self.assertEqual(list(outside.iterdir()), [])

    def test_ancestor_replacement_after_open_keeps_write_on_opened_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job, outside = root / "job", root / "outside"
            job.mkdir()
            outside.mkdir()
            checks = job / "checks"
            checks.mkdir()
            original_open = os.open

            def replace_after_open(path, flags, *args, **kwargs):
                fd = original_open(path, flags, *args, **kwargs)
                if path == "checks":
                    checks.rename(job / "moved")
                    checks.symlink_to(outside, target_is_directory=True)
                return fd

            with patch.object(job_files.os, "open", side_effect=replace_after_open):
                job_files.write_text(job, checks / "log", "safe")
            # [utest~im-job_files-JobFileTests-ancestor_replacement_after_open_keeps_write_on_opened_directory~1->req~im-safe-controller-writes~1]
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual((job / "moved/log").read_text(), "safe")

    def test_traceability_controller_check_does_not_follow_worker_links(self):
        for linked_parent in (False, True):
            with (
                self.subTest(linked_parent=linked_parent),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                job = root / "job"
                checks = job / "traceability/sample"
                scratch = checks / "scratch"
                scratch.mkdir(parents=True)
                scope = checks / "scope.json"
                scope.write_text("{}")
                outside = root / "controller-only"
                outside.mkdir()
                for name in ("checker.log", "invocation.json", "invocation.tmp"):
                    (outside / name).write_text("protected")

                def worker_check(*args, **kwargs):
                    invocation = kwargs["out"].parent
                    if linked_parent:
                        invocation.rename(checks / "moved")
                        invocation.symlink_to(outside, target_is_directory=True)
                    else:
                        for name in ("checker.log", "invocation.json", "invocation.tmp"):
                            (invocation / name).unlink(missing_ok=True)
                            (invocation / name).symlink_to(outside / name)
                    return SimpleNamespace(exit_code=0, stdout="worker output", stderr="")

                paths = {"root": job, "scratch": scratch, "worker_scope": scope, "timeout": 10}
                state = {"worktree": str(job / "source"), "base": "a" * 40}
                with (
                    patch("provenance.remote_boundary", return_value={}),
                    patch("traceability.openhands.check", side_effect=worker_check),
                ):
                    if linked_parent:
                        with self.assertRaises(OSError):
                            traceability.check(object(), state, paths, {})
                    else:
                        self.assertEqual(
                            traceability.check(object(), state, paths, {}).exit_code, 0
                        )
                        invocation = paths["out"].parent
                        self.assertEqual((invocation / "checker.log").read_text(), "worker output")
                        self.assertEqual(
                            json.loads((invocation / "invocation.json").read_text())["status"],
                            "finished",
                        )
                self.assertTrue(all(p.read_text() == "protected" for p in outside.iterdir()))

    def test_grouped_browser_qa_uses_a_fresh_root_and_source_for_each_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configs = [
                {"project": project, "browser_qa": {"url": "fixture"}} for project in ("one", "two")
            ]
            states = {config["project"]: {} for config in configs}
            roots = []

            def prepare(job, selected):
                (job / "source").mkdir()
                (job / "source/code").write_text("pristine")

            @contextmanager
            def worker(job, configs):
                self.assertNotIn(job, roots)
                roots.append(job)
                self.assertEqual((job / "source/code").read_text(), "pristine")
                self.assertFalse((job / ".factory-execution.json").exists())
                (job / "source/code").write_text("first worker changed it")
                (job / ".factory-execution.json").symlink_to(root / "protected")
                yield object()

            with (
                patch.object(run, "DATA", root),
                patch.object(run, "prepare_sources", side_effect=prepare),
                patch.object(run, "worker", worker),
                patch.object(run.browser_qa, "selected", return_value=True),
                patch.object(run.browser_qa, "run", return_value={"status": "PASS"}),
                patch.object(run.reporting, "browser_evidence"),
            ):
                result = run.browser_checks(configs, states, "request", root, 0)
            # [utest~im-job_files-JobFileTests-grouped_browser_qa_uses_a_fresh_root_and_source_for_each_worker~1->req~im-safe-controller-writes~1]
            self.assertEqual(set(result), {"one", "two"})
            self.assertEqual(len(roots), 2)
            self.assertFalse((root / "protected").exists())
