"""Real process contention for isolated tasks and bounded worker admission."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import common
import deployment
import execution

CONFIG = {"project": "app", "repository": "org/app"}
CHILD = """
import json, sys, time
from pathlib import Path
import execution
import os
root, name, configs, wait = sys.argv[1:]
os.environ["FACTORY_ROOT"] = root
try:
    with execution.task(json.loads(configs), name, wait=wait == 'yes'):
        (Path(root) / (name + '.ready')).write_text('ready')
        while not (Path(root) / (name + '.release')).exists():
            time.sleep(0.02)
except BlockingIOError:
    (Path(root) / (name + '.busy')).write_text('busy')
"""


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config/defaults.json").write_text("{}")
        self.options = {"max_tasks": 2, "max_tasks_per_repository": 2, "repositories": {}}
        self.write_options()
        self.children = []
        self.addCleanup(self.stop_children)
        self.environment = {
            **os.environ,
            "FACTORY_ROOT": str(common.ROOT),
            "FACTORY_DATA": str(self.root),
            "PYTHONPATH": os.pathsep.join(sys.path),
        }

    def write_options(self):
        (self.root / "config/deployment.json").write_text(json.dumps({"concurrency": self.options}))

    def start(self, name, configs=None, *, wait=False):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                CHILD,
                str(self.root),
                name,
                json.dumps(configs or [CONFIG]),
                "yes" if wait else "no",
            ],
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.children.append(child)
        return child

    def ready(self, name, status="ready"):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if (self.root / (name + "." + status)).exists():
                return
            for child in self.children:
                if child.poll() not in (None, 0):
                    self.fail(child.communicate()[1].decode())
            time.sleep(0.02)
        self.fail(f"{name} did not become {status}")

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_independent_tasks_overlap_and_capacity_waits_then_recovers_after_crash(self):
        # [utest~im-task-concurrency-processes~1->req~im-repository-locks~1]
        first = self.start("one")
        self.ready("one")
        self.start("two")
        self.ready("two")
        waiting = self.start("three", wait=True)
        time.sleep(0.2)
        self.assertFalse((self.root / "three.ready").exists())
        first.kill()
        first.communicate(timeout=5)
        self.children.remove(first)
        self.ready("three")
        self.assertIsNone(waiting.poll())

    def test_same_task_is_exclusive_even_under_repository_alias(self):
        # [utest~im-task-concurrency-identity~1->req~im-repository-locks~1]
        self.start("same")
        self.ready("same")
        alias = {**CONFIG, "project": "alias", "repository": "ORG/APP"}
        self.start("same", [alias])
        self.ready("same", "busy")

    def test_repository_override_preserves_capacity_for_other_repositories(self):
        # [utest~im-task-concurrency-limits~1->req~im-repository-locks~1]
        self.options["repositories"] = {"app": 1}
        self.write_options()
        self.start("one")
        self.ready("one")
        self.start("blocked")
        self.ready("blocked", "busy")
        self.start("other", [{"project": "other", "repository": "org/other"}])
        self.ready("other")

    def test_group_failure_releases_earlier_task_locks_and_capacity(self):
        # [utest~im-task-concurrency-group~1->req~im-repository-locks~1]
        other = {"project": "other", "repository": "org/other"}
        self.start("shared", [other])
        self.ready("shared")
        with (
            patch.object(common, "DATA", self.root),
            patch.dict(os.environ, {**self.environment, "FACTORY_ROOT": str(self.root)}),
        ):
            with self.assertRaises(BlockingIOError):
                with execution.task([CONFIG, other], "shared", wait=False):
                    self.fail("A group cannot start without every task lock")
            with execution.task([CONFIG], "independent", wait=False):
                pass
            with common.lock("task-" + execution.task_key(CONFIG, "shared")):
                pass

    def test_nested_pipeline_reuses_slots_and_releases_after_exception(self):
        with (
            patch.object(common, "DATA", self.root),
            patch.dict(os.environ, {**self.environment, "FACTORY_ROOT": str(self.root)}),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with execution.task([CONFIG], "same"):
                    with execution.task([CONFIG], "same"):
                        with self.assertRaises(ValueError):
                            with execution.task([CONFIG], "different"):
                                pass
                    raise RuntimeError("fixture")
            with execution.task([CONFIG], "same", wait=False):
                pass

    def test_lowered_global_limit_counts_all_existing_active_slots(self):
        self.start("one")
        self.ready("one")
        self.start("two")
        self.ready("two")
        self.options["max_tasks"] = 1
        self.write_options()
        (self.root / "one.release").touch()
        self.children[0].wait(timeout=5)
        self.start("blocked")
        self.ready("blocked", "busy")

    def test_configuration_is_operator_only_and_rejects_invalid_limits(self):
        for invalid in [True, 0, -1, 129, "2", None]:
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    deployment.validate_concurrency({"max_tasks": invalid})
                with self.assertRaises(ValueError):
                    deployment.validate_concurrency({"repositories": {"app": invalid}})
        for invalid in [{"unknown": 2}, {"repositories": []}, {"repositories": {"../app": 2}}]:
            with self.assertRaises(ValueError):
                deployment.validate_concurrency(invalid)
        with self.assertRaisesRegex(ValueError, "deployment.json"):
            deployment.workflow_settings({}, {**CONFIG, "concurrency": {"max_tasks": 100}})
