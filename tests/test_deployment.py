"""Deployment authority across configuration, saved jobs and publication."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import approval
import common
import deployment
import docker_sandboxes
import monitor
import resource_limits
import run

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "main",
    "enabled": True,
    "issue_label": None,
    "assignee": "factory-bot",
    "test_command": "make test",
    "required_checks": ["unit"],
}
ISSUE = {
    "number": 42,
    "title": "Feature",
    "body": "Specification",
    "state": "open",
    "assignees": [],
    "labels": [{"name": "factory:approved"}],
}
OSS = {"authorization": {"require_issue_approval": True}}


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config"
        (self.config / "repositories").mkdir(parents=True)
        self.write("defaults.json", CONFIG)
        self.write("repositories/example.json", {})
        environment = patch.dict(os.environ, {"FACTORY_ROOT": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, name, value):
        (self.config / name).write_text(json.dumps(value))

    def test_absent_deployment_preserves_personal_configuration(self):
        self.assertEqual(common.projects(self.config)["example"], CONFIG)
        self.assertEqual(docker_sandboxes.settings(), {"backend": "docker"})
        self.assertEqual(resource_limits.settings(), resource_limits.DEFAULTS)
        deployment.check_issue_authorization(CONFIG)

    def test_public_examples_require_approval_when_scheduling_is_enabled(self):
        examples = Path(__file__).resolve().parents[1] / "examples/config"
        for name in ("defaults.json", "deployment.json"):
            self.write(name, json.loads((examples / name).read_text()))
        registration = {"repository": "example/repo", "test_command": "make test"}
        self.write("repositories/example.json", registration)
        self.assertFalse(common.projects(self.config)["example"]["enabled"])
        self.write("repositories/example.json", {**registration, "enabled": True})
        config = common.projects(self.config)["example"]
        self.assertEqual(config["issue_label"], "factory:approved")
        self.assertTrue(deployment.settings()["authorization"]["require_issue_approval"])
        self.write(
            "repositories/example.json", {**registration, "enabled": True, "issue_label": None}
        )
        with self.assertRaisesRegex(ValueError, "requires issue approval"):
            common.projects(self.config)

    def test_runtime_change_does_not_change_captured_workflow_policy(self):
        before = common.projects(self.config)
        runtime = {"backend": "docker-sandboxes", "kit": "/operator/kit"}
        self.write(
            "deployment.json",
            {"worker_runtime": runtime, "resource_limits": {"max_bundle_mb": 16}},
        )
        self.assertEqual(common.projects(self.config), before)
        self.assertEqual(docker_sandboxes.settings(), runtime)
        self.assertEqual(resource_limits.settings()["max_bundle_mb"], 16)
        self.assertEqual(resource_limits.settings()["worker_pids"], 512)

    def test_runtime_paths_follow_deployment_location(self):
        # [utest~im-runtime-relative-paths~1->req~im-deployment-authority~1]
        self.write(
            "deployment.json",
            {
                "worker_runtime": {
                    "backend": "docker-sandboxes",
                    "kit": "../runtimes/kit",
                    "profiles": "../profiles",
                }
            },
        )
        selected = deployment.settings(self.config)["worker_runtime"]
        self.assertEqual(selected["kit"], str(self.root / "runtimes/kit"))
        self.assertEqual(selected["profiles"], str(self.root / "profiles"))
        with patch.dict(os.environ, {"FACTORY_HOST_CONFIG_DIR": "/moved/deployment/config"}):
            selected = deployment.settings(self.config)["worker_runtime"]
        self.assertEqual(selected["kit"], "/moved/deployment/runtimes/kit")
        self.assertEqual(selected["profiles"], "/moved/deployment/profiles")

    def test_worker_profile_is_operator_controlled_and_excluded_from_jobs(self):
        # [utest~im-deployment-DeploymentTests-worker_profile_is_operator_controlled_and_excluded_from_jobs~1->req~im-deployment-authority~1]
        before = common.projects(self.config)
        self.assertEqual(deployment.settings()["worker_agent_profile"], "factory-codex")
        self.write("deployment.json", {"worker_agent_profile": "factory-native"})
        self.assertEqual(deployment.settings()["worker_agent_profile"], "factory-native")
        self.assertEqual(common.projects(self.config), before)
        for invalid in (None, "", "  ", 3):
            self.write("deployment.json", {"worker_agent_profile": invalid})
            with self.assertRaisesRegex(ValueError, "worker_agent_profile"):
                deployment.settings()
        self.write("deployment.json", {})
        self.write("defaults.json", {**CONFIG, "worker_agent_profile": "factory-native"})
        with self.assertRaisesRegex(ValueError, "deployment.json"):
            deployment.settings()

    def test_legacy_settings_survive_migration_and_never_enter_jobs(self):
        options = {
            "worker_runtime": {"backend": "docker-sandboxes", "kit": "/operator/kit"},
            "resource_limits": {"worker_cpus": 3},
        }
        self.write("defaults.json", {**CONFIG, **options})
        before = deployment.settings()
        self.assertEqual(common.projects(self.config)["example"], CONFIG)
        self.write("defaults.json", CONFIG)
        self.write("deployment.json", options)
        self.assertEqual(deployment.settings(), before)
        self.assertEqual(common.projects(self.config)["example"], CONFIG)

    def test_duplicate_settings_fail_instead_of_silently_selecting_a_policy(self):
        for name in ("worker_runtime", "resource_limits"):
            self.write("defaults.json", {**CONFIG, name: {}})
            self.write("deployment.json", {name: {}})
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "only once"):
                deployment.settings()

    def test_repository_cannot_override_any_deployment_field(self):
        for name, value in (
            ("worker_agent_profile", "untrusted-profile"),
            ("worker_runtime", {"backend": "docker"}),
            ("resource_limits", {"max_bundle_mb": 999999}),
            ("authorization", {"require_issue_approval": False}),
        ):
            self.write("repositories/example.json", {name: value})
            # [utest~im-deployment-DeploymentTests-repository_cannot_override_any_deployment_field~1->req~im-deployment-authority~1]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "factory-wide"):
                common.projects(self.config)

    def test_authorization_is_not_a_workflow_default(self):
        self.write("defaults.json", {**CONFIG, **OSS})
        with self.assertRaisesRegex(ValueError, "authorization in deployment.json"):
            common.projects(self.config)

    def test_invalid_deployment_fails_before_work(self):
        for value in (
            [],
            {"unknown": True},
            {"authorization": None},
            {"authorization": {"require_issue_approval": "false"}},
            {"authorization": {"require_issue_approval": 1}},
            {"authorization": {"typo": True}},
            {"resource_limits": {"worker_cpus": 0}},
            {"worker_runtime": {"backend": "docker-sandboxes", "kit": ""}},
        ):
            self.write("deployment.json", value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.projects(self.config)

    def test_oss_requires_label_for_enabled_schedulers_but_allows_manual_repositories(self):
        self.write("deployment.json", OSS)
        for label in (None, "", " ", True, []):
            self.write("repositories/example.json", {"issue_label": label})
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(ValueError, "requires issue approval"),
            ):
                common.projects(self.config)
        self.write("repositories/example.json", {"enabled": False})
        self.assertFalse(common.projects(self.config)["example"]["enabled"])
        self.write("repositories/example.json", {"issue_label": "factory:approved"})
        self.assertEqual(
            common.projects(self.config)["example"], {**CONFIG, "issue_label": "factory:approved"}
        )

    def test_saved_scheduler_cannot_bypass_new_requirement_or_start_triage(self):
        captured = common.projects(self.config)["example"]
        self.write("deployment.json", OSS)
        # [utest~im-deployment-DeploymentTests-saved_scheduler_cannot_bypass_new_requirement_or_start_triage~1->req~im-deployment-authority~1]
        with (
            patch.object(monitor.issues, "_kv_get") as state,
            patch.object(monitor, "worker") as worker,
            self.assertRaisesRegex(ValueError, "requires issue approval"),
        ):
            monitor.poll(captured, "fixture-token")
        state.assert_not_called()
        worker.assert_not_called()

    def test_unapproved_issue_cannot_be_claimed_or_built_in_oss_deployment(self):
        self.write("deployment.json", OSS)
        with (
            patch.object(monitor.issues, "_get_issue", return_value=ISSUE),
            patch.object(monitor, "github") as github,
            patch.object(monitor, "build") as build,
            self.assertRaisesRegex(ValueError, "requires issue approval"),
        ):
            monitor.implement_issue(CONFIG, ISSUE, "fixture-token")
        github.assert_not_called()
        build.assert_not_called()

    def test_captured_issue_build_cannot_reintroduce_unapproved_work(self):
        self.write("deployment.json", OSS)
        # [utest~im-deployment-DeploymentTests-captured_issue_build_cannot_reintroduce_unapproved_work~1->req~im-deployment-authority~1]
        with (
            patch.object(run, "evidence") as evidence,
            self.assertRaisesRegex(ValueError, "requires issue approval"),
        ):
            run.build(CONFIG, "issue-42", "Specification", "base", issue=42)
        evidence.assert_not_called()

    def test_personal_issue_snapshot_cannot_publish_after_operator_requires_approval(self):
        with patch.object(approval.issues, "_get_issue", return_value=ISSUE):
            _, snapshot = approval.approved_issue(CONFIG, 42, "fixture-token")
        self.write("deployment.json", OSS)
        # [utest~im-deployment-DeploymentTests-personal_issue_snapshot_cannot_publish_after_operator_requires_approval~1->req~im-deployment-authority~1]
        with (
            patch.object(
                run, "github", return_value={**ISSUE, "assignees": [{"login": "factory-bot"}]}
            ),
            patch.object(run.issues, "_push_branch") as push,
            self.assertRaisesRegex(ValueError, "requires issue approval"),
        ):
            run.publish(
                {**CONFIG, "issue_approval": snapshot},
                "issue-42",
                self.root,
                "factory/task",
                self.root,
                "Specification",
                "fixture-token",
                issue=42,
            )
        push.assert_not_called()

    def test_oss_reuses_existing_content_bound_approval(self):
        self.write("deployment.json", OSS)
        config = {**CONFIG, "issue_label": "factory:approved"}
        event = {
            "id": 1,
            "event": "labeled",
            "label": {"name": "factory:approved"},
            "created_at": "2026-01-01T00:00:01Z",
        }
        content = {
            "title": ISSUE["title"],
            "body": ISSUE["body"],
            "lastEditedAt": None,
            "timelineItems": {"nodes": []},
        }
        with (
            patch.object(approval.issues, "_github_paginate", return_value=[event]),
            patch.object(
                approval, "github", return_value={"data": {"repository": {"issue": content}}}
            ),
        ):
            _, snapshot = approval.approved_issue(config, 42, "fixture-token")
            self.assertEqual(snapshot["label_event"], "1")
            content["lastEditedAt"] = "2026-01-01T00:00:02Z"
            with self.assertRaisesRegex(RuntimeError, "edited at or after approval"):
                approval.approved_issue(config, 42, "fixture-token", expected=snapshot)


if __name__ == "__main__":
    unittest.main()
