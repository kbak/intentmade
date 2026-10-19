"""Retirement preserves unfinished work, native history and dispatch identity."""

import copy
import datetime
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import automation_retention as retention
import common
import deployment
import native_queue

CONFIG = {"project": "app", "repository": "org/app"}
NOW = datetime.datetime(2026, 10, 19, tzinfo=datetime.UTC)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = {"kind": "issue", "key": "issue:3:abc", "item": {"number": 3}}
        digest = hashlib.sha256(
            json.dumps(["org/app", "issue", "issue:3:abc", None], sort_keys=True).encode()
        ).hexdigest()
        self.definition = {
            "id": "automation",
            "name": "Task — app — 3 — " + digest[:24],
            "enabled": True,
            "trigger": {
                "type": "event",
                "source": "custom",
                "on": "factory.scheduled-task",
                "filter": "`false`",
            },
            "entrypoint": "python monitor.py",
            "timeout": 7200,
            "tarball_path": "oh-internal://uploads/payload",
        }
        self.runs = [
            {
                "id": "run",
                "status": "COMPLETED",
                "created_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
            }
        ]
        self.reports = [{"automation_id": "automation", "run_id": "run", "status": "PASSED"}]
        self.actions = []
        self.deleted = False
        self.options = {"automation_retention": {"days": 10, "repositories": {}}}
        directory = self.root / "config/repositories"
        directory.mkdir(parents=True)
        (directory / "app.json").write_text(json.dumps(CONFIG))
        self.native = SimpleNamespace(
            records=lambda: [] if self.deleted else [copy.deepcopy(self.definition)]
        )
        for target, name, value in (
            (common, "ROOT", self.root),
            (common, "DATA", self.root / "data"),
            (deployment, "settings", lambda: self.options),
            (retention, "reports_for", lambda config: copy.deepcopy(self.reports)),
            (native_queue, "registration", lambda: self.native),
            (common, "api", self.api),
        ):
            context = patch.object(target, name, value)
            context.start()
            self.addCleanup(context.stop)

    def api(self, method, path, **kwargs):
        if method == "GET" and path.endswith("/runs"):
            offset = kwargs["params"]["offset"]
            return {
                "runs": copy.deepcopy(self.runs[offset : offset + 100]),
                "total": len(self.runs),
            }
        if method == "GET":
            return copy.deepcopy(self.definition)
        self.actions.append(method)
        if method == "PATCH":
            self.definition.update(kwargs["json"])
        elif method == "DELETE":
            self.deleted = True

    def sweep(self, days=0, **kwargs):
        return retention.sweep(
            CONFIG, now=NOW + datetime.timedelta(days=days), dry_run=False, **kwargs
        )

    def test_preview_disable_and_exact_ten_day_boundary_preserve_archive(self):
        # [utest~im-retention-lifecycle~1->req~im-automation-retention~1]
        preview = retention.sweep(CONFIG, now=NOW)
        self.assertEqual(preview[0]["action"], "disable")
        self.assertFalse(retention.archive_path(self.definition).exists())
        self.assertFalse(self.actions)
        self.sweep()
        self.assertEqual(self.actions, ["PATCH"])
        self.assertFalse(self.definition["enabled"])
        self.sweep(days=10 - 1 / 86400)
        self.assertFalse(self.deleted)
        self.sweep(days=10)
        self.assertEqual(self.actions, ["PATCH", "DELETE"])
        record = json.loads(retention.archive_path(self.definition).read_text())
        self.assertEqual(record["runs"], self.runs)
        self.assertEqual(record["definition"]["tarball_path"], self.definition["tarball_path"])
        self.assertTrue(record["delete_requested"])

    def test_unresolved_or_ambiguous_work_never_changes(self):
        # [utest~im-retention-unresolved~1->req~im-automation-retention~1]
        for status in ("PENDING", "RUNNING", "FAILED", "CANCELLED", "UNKNOWN"):
            with self.subTest(run=status):
                self.runs[0]["status"] = status
                self.assertEqual(self.sweep(days=100), [])
        self.runs[0]["status"] = "COMPLETED"
        for status in (
            "NEEDS_INPUT",
            "FAILED",
            "PUBLICATION_FAILED",
            "RUNNING",
            "REVIEWING",
            "STALE",
            "UNKNOWN",
            None,
        ):
            with self.subTest(report=status):
                self.reports[0]["status"] = status
                self.assertEqual(self.sweep(days=100), [])
        self.reports = []
        self.runs[0]["status"] = "SKIPPED"
        self.assertEqual(self.sweep(days=100), [])
        self.runs[0]["status"] = "COMPLETED"
        self.definition["name"] = "Continue — app — issue 3"
        self.definition["trigger"]["on"] = "factory.continue-issue"
        self.assertEqual(self.sweep(days=100), [])
        self.runs = []
        self.assertEqual(self.sweep(days=100), [])
        self.assertFalse(self.actions)

    def test_permanent_unmanaged_and_other_repository_definitions_are_untouched(self):
        # [utest~im-retention-selection~1->req~im-automation-retention~1]
        for name in (
            "Factory — app",
            "Resume — app",
            "Build — app — task",
            "Review — app — PR 3",
            "Task — other — 3 — " + "a" * 24,
            "Task — app — custom",
        ):
            self.definition["name"] = name
            self.assertEqual(self.sweep(days=100), [])
        self.definition["name"] = "Task — app — 3 — " + "a" * 24
        self.definition["trigger"]["on"] = "custom-event"
        self.assertEqual(self.sweep(days=100), [])
        self.assertFalse(self.actions)

    def test_current_policy_override_opt_out_and_repointed_registration(self):
        # [utest~im-retention-current-policy~1->req~im-automation-retention~1]
        self.options["automation_retention"]["repositories"] = {"app": None}
        self.assertEqual(self.sweep(), [])
        self.options["automation_retention"] = {"days": None, "repositories": {"app": 20}}
        self.sweep()
        self.sweep(days=10)
        self.assertFalse(self.deleted)
        self.options["automation_retention"]["repositories"]["app"] = 10
        (self.root / "config/repositories/app.json").write_text('{"repository":"org/different"}')
        self.assertEqual(self.sweep(days=20), [])
        self.assertFalse(self.deleted)
        (self.root / "config/repositories/app.json").write_text(json.dumps(CONFIG))
        self.sweep(days=20)
        self.assertTrue(self.deleted)

    def test_history_pagination_sees_active_run_on_later_page(self):
        # [utest~im-retention-pagination~1->req~im-automation-retention~1]
        self.runs = [{**self.runs[0], "id": str(i)} for i in range(101)]
        self.runs[-1]["status"] = "PENDING"
        self.assertEqual(self.sweep(), [])
        self.assertFalse(self.actions)
        self.runs[-1]["status"] = "COMPLETED"
        self.sweep()
        self.sweep(days=10)
        record = json.loads(retention.archive_path(self.definition).read_text())
        self.assertEqual(len(record["runs"]), 101)
        self.assertTrue(self.deleted)

    def test_failed_archival_prevents_mutation_and_changed_run_restarts_retention(self):
        # [utest~im-retention-evidence~1->req~im-automation-retention~1]
        with patch.object(retention, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.sweep()
        self.assertFalse(self.actions)
        self.sweep()
        with patch.object(retention, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.sweep(days=10)
        self.assertFalse(self.deleted)
        self.runs.append(
            {
                **self.runs[0],
                "id": "rerun",
                "created_at": (NOW + datetime.timedelta(days=9)).isoformat(),
            }
        )
        self.sweep(days=10)
        self.assertFalse(self.deleted)
        self.sweep(days=20)
        self.assertTrue(self.deleted)

    def test_late_dispatch_or_report_change_blocks_delete(self):
        # [utest~im-retention-recheck~1->req~im-automation-retention~1]
        self.sweep()
        original = self.api

        def changed(method, path, **kwargs):
            if method == "GET" and not path.endswith("/runs"):
                self.runs.append({**self.runs[0], "id": "late", "status": "PENDING"})
            return original(method, path, **kwargs)

        with patch.object(common, "api", changed):
            self.sweep(days=10)
        self.assertFalse(self.deleted)
        self.runs.pop()
        with patch.object(
            retention,
            "reports_for",
            side_effect=[self.reports, [{**self.reports[0], "status": "NEEDS_INPUT"}]],
        ):
            self.sweep(days=10)
        self.assertFalse(self.deleted)

    def test_retirement_and_lost_delete_acknowledgement_cannot_replay_recipe(self):
        # [utest~im-retention-no-replay~1->req~im-automation-retention~1]
        # [utest~im-retention-no-replay-queue~1->req~im-repository-locks~1]
        self.sweep()
        original = self.api

        def timeout(method, path, **kwargs):
            result = original(method, path, **kwargs)
            if method == "DELETE":
                raise TimeoutError("acknowledgement lost")
            return result

        with patch.object(common, "api", timeout):
            with self.assertRaises(TimeoutError):
                self.sweep(days=10)
        with patch.object(native_queue, "api") as dispatch:
            self.assertFalse(native_queue.enqueue(CONFIG, self.work))
            dispatch.assert_not_called()
        # A fresh explicit answer still has a new immutable identity.
        self.native.files = lambda job: job
        self.native.install = Mock(return_value={"id": "new"})
        with (
            patch.object(native_queue, "api", return_value={"id": "new-run"}),
            patch.object(deployment, "run_timeout", return_value=7200),
        ):
            self.assertTrue(native_queue.enqueue(CONFIG, {**self.work, "answer_id": "new-answer"}))

    def test_corrupt_dispatch_receipt_cannot_allow_replay(self):
        # [utest~im-retention-corrupt-receipt~1->req~im-automation-retention~1]
        self.sweep()
        self.sweep(days=10)
        path = retention.receipt_path(self.definition["name"])
        path.write_text(json.dumps({"name": self.definition["name"]}))
        with patch.object(native_queue, "api") as dispatch:
            with self.assertRaises(ValueError):
                native_queue.enqueue(CONFIG, self.work)
            dispatch.assert_not_called()

    def test_reenabling_or_replacing_recipe_restarts_retention(self):
        # [utest~im-retention-reset~1->req~im-automation-retention~1]
        self.sweep()
        self.definition["enabled"] = True
        self.sweep(days=10)
        self.assertFalse(self.deleted)
        self.definition["tarball_path"] = "oh-internal://uploads/replacement"
        self.sweep(days=20)
        self.assertFalse(self.deleted)
        self.sweep(days=30)
        self.assertTrue(self.deleted)

    def test_reused_continuation_name_keeps_each_archived_definition(self):
        # [utest~im-retention-reused-name~1->req~im-automation-retention~1]
        self.definition["name"] = "Continue — app — issue 3"
        self.definition["trigger"]["on"] = "factory.continue-issue"
        self.sweep()
        self.sweep(days=10)
        old_archive = retention.archive_path(self.definition)
        original_bytes = old_archive.read_bytes()
        self.deleted = False
        self.definition.update(id="new-automation", enabled=True)
        self.reports[0]["automation_id"] = "new-automation"
        self.sweep(days=20)
        self.sweep(days=30)
        self.assertEqual(old_archive.read_bytes(), original_bytes)
        self.assertTrue(retention.archive_path(self.definition).exists())

    def test_daily_schedule_and_locking_do_not_block_tasks(self):
        # [utest~im-retention-scheduling~1->req~im-automation-retention~1]
        self.sweep(scheduled=True)
        with patch.object(self.native, "records") as records:
            self.sweep(days=0.5, scheduled=True)
            records.assert_not_called()
        with common.lock("app.queue"):
            retention.scheduled_cleanup(CONFIG)
        self.assertFalse(self.deleted)

    def test_incomplete_inventory_and_unavailable_reports_preserve_definition(self):
        # [utest~im-retention-incomplete~1->req~im-automation-retention~1]
        with patch.object(common, "api", return_value={"runs": [], "total": 2}):
            with self.assertRaises(ValueError):
                self.sweep()
        with patch.object(retention, "reports_for", side_effect=ValueError("corrupt report")):
            with self.assertRaises(ValueError):
                self.sweep()
        self.runs[0]["completed_at"] = None
        self.assertEqual(self.sweep(), [])
        self.assertFalse(self.actions)


class PolicyTests(unittest.TestCase):
    def test_policy_validation_and_operator_authority(self):
        # [utest~im-retention-policy~1->req~im-automation-retention~1]
        # [utest~im-retention-authority~1->req~im-deployment-authority~1]
        self.assertEqual(
            deployment.validate_automation_retention({}), {"days": 10, "repositories": {}}
        )
        for value in (True, 0, -1, "10", 1.5, 3651):
            for policy in ({"days": value}, {"repositories": {"app": value}}):
                with self.subTest(policy=policy), self.assertRaises(ValueError):
                    deployment.validate_automation_retention(policy)
        for policy in (
            None,
            {"unknown": 3},
            {"repositories": []},
            {"repositories": {"../app": 10}},
        ):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                deployment.validate_automation_retention(policy)
        with self.assertRaisesRegex(ValueError, "deployment.json"):
            deployment.workflow_settings({}, {"automation_retention": {"days": 1}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "defaults.json").write_text('{"automation_retention":{"days":1}}')
            with self.assertRaisesRegex(ValueError, "deployment.json"):
                deployment.settings(root)
