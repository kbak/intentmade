"""Published drafts are maintained by receipt, with bounded and revision-safe repair."""

import copy
import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import common
import followup
import monitor
import naming
import policy
import run

CONFIG = {
    "project": "example",
    "repository": "org/repo",
    "branch": "main",
    "branch_prefix": None,
    "required_checks": ["unit"],
    "accepted_check_results": ["success", "neutral", "skipped"],
    "ci_repair_attempts": 3,
    "repair_attempts": 0,
    "test_command": "true",
    "publish_draft": True,
}
PR = {
    "number": 42,
    "state": "open",
    "draft": True,
    "title": "fix: restore scanning",
    "html_url": "https://github.com/org/repo/pull/42",
    "head": {"sha": "head", "ref": "fix/42-restore-scanning", "repo": {"full_name": "org/repo"}},
    "base": {"sha": "base", "ref": "main"},
}


class NamingTests(unittest.TestCase):
    def test_repository_tracker_rule_preserves_existing_keys_and_is_idempotent(self):
        config = {"pr_title_subject_prefix": "NOSTORY"}
        expected = "fix(telegram): NOSTORY: Restore scanning"
        self.assertEqual(
            naming.pull_request_title("bug(telegram): Restore scanning", config), expected
        )
        self.assertEqual(naming.pull_request_title(expected, config), expected)
        tracked = "feat: CDA-123: Add retry"
        self.assertEqual(naming.pull_request_title(tracked, config), tracked)
        self.assertEqual(
            naming.pull_request_title("fix: Restore scanning", {}), "fix: Restore scanning"
        )

    def test_conventional_title_and_descriptive_branch(self):
        title = "[#1402] bug(telegram): restore scanning after /stop"
        self.assertEqual(naming.change_title(title), "fix(telegram): restore scanning after /stop")
        self.assertEqual(
            naming.branch_name("issue-1402", title), "fix/1402-telegram-restore-scanning-after-stop"
        )
        self.assertEqual(naming.branch_name("issue-15", "feat: Add retries"), "feat/15-add-retries")
        self.assertEqual(
            naming.branch_name("feature-abc", "docs: Explain retries"),
            "docs/feature-abc-explain-retries",
        )
        branch = naming.branch_name("issue-9", "fix: ../../ $(bad) café @{} ?* [] ")
        common.git(["check-ref-format", "--branch", branch])
        self.assertEqual(branch, "fix/9-bad-cafe")

    def test_newer_same_provider_result_supersedes_failure_across_pages(self):
        old = {
            "id": 1,
            "app": {"id": 5},
            "name": "unit",
            "status": "completed",
            "conclusion": "failure",
        }
        new = {**old, "id": 3, "conclusion": "success"}
        with patch.object(
            policy, "github", side_effect=[{"check_runs": [new, old]}, {"statuses": []}]
        ):
            self.assertEqual(policy.checks_for("secret", "org/repo", "sha"), {"unit": "success"})
        other = {**old, "id": 2, "app": {"id": 6}}
        with patch.object(
            policy, "github", side_effect=[{"check_runs": [new, old, other]}, {"statuses": []}]
        ):
            self.assertEqual(policy.checks_for("secret", "org/repo", "sha"), {"unit": "failure"})


class FollowupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, name, value in (
            (common, "DATA", self.root),
            (monitor, "DATA", self.root),
            (followup, "DATA", self.root),
            (run, "DATA", self.root),
            (followup, "resume_reply", lambda *args: None),
            (followup, "artifact_retries", lambda *args: []),
        ):
            replacement = patch.object(target, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.record = {
            "number": 42,
            "task": "issue-42",
            "head": "head",
            "branch": PR["head"]["ref"],
            "base_branch": "main",
            "attempts": 0,
            "status": "WATCHING",
            "request": "fix: restore scanning",
        }

    def plan(self, checks=None, behind=0, pr=None):
        with (
            patch.object(followup, "checks_for", return_value=checks or {"unit": "failure"}),
            patch.object(followup, "github", return_value={"behind_by": behind}),
        ):
            return followup.plan(CONFIG, pr or PR, "secret")

    def test_only_recorded_prs_are_repaired_even_if_names_match(self):
        self.assertIsNone(self.plan())
        followup.save(CONFIG, self.record)
        self.assertEqual(self.plan()["failures"], {"unit": "failure"})
        pr = copy.deepcopy(PR)
        pr["head"]["sha"] = "human-update"
        self.assertIsNone(self.plan(pr=pr))
        self.assertEqual(followup.read(CONFIG, 42)["status"], "NEEDS_INPUT")

    def test_draft_base_updates_are_eligible_but_pending_ci_alone_waits(self):
        followup.save(CONFIG, self.record)
        self.assertIsNone(self.plan({"unit": "pending"}))
        action = self.plan({"unit": "success"}, behind=16)
        self.assertTrue(action["behind"])
        self.assertEqual(action["failures"], {})
        self.record["last_attempt"] = action["key"]
        followup.save(CONFIG, self.record)
        self.assertIsNone(self.plan({"unit": "success"}, behind=16))

    def test_ci_success_resets_budget_and_repeated_failure_requires_explicit_reply(self):
        followup.save(CONFIG, {**self.record, "attempts": 3})
        self.assertIsNone(self.plan({"unit": "success"}))
        self.assertEqual(followup.read(CONFIG, 42)["attempts"], 0)
        followup.save(CONFIG, {**self.record, "attempts": 3})
        self.assertIsNone(self.plan())
        self.assertEqual(followup.read(CONFIG, 42)["status"], "NEEDS_INPUT")
        self.assertIsNone(self.plan())
        reply = {
            "id": "answer",
            "answer": "retry",
            "snapshot": followup.snapshot(CONFIG, self.record),
        }
        with patch.object(followup, "resume_reply", return_value=reply):
            self.assertEqual(self.plan()["reply"], reply)

    def test_revision_changes_prevent_publication(self):
        expected = {"number": 42, "branch": PR["head"]["ref"], "head": "head", "base": "base"}
        for location, key, value in (
            ("head", "sha", "human-commit"),
            ("base", "sha", "new-main"),
            ("head", "ref", "different-branch"),
        ):
            pr = copy.deepcopy(PR)
            pr[location][key] = value
            with (
                self.subTest(location=location, key=key),
                patch.object(followup, "github", return_value=pr),
                patch.object(run.issues, "_push_branch") as push,
            ):
                with self.assertRaisesRegex(RuntimeError, "changed during repair"):
                    run.publish(
                        {**CONFIG, "repair_pr": expected},
                        "task",
                        self.root,
                        expected["branch"],
                        self.root,
                        "fix: repair",
                        "secret",
                    )
                push.assert_not_called()

    def test_artifact_failure_retries_while_other_checks_run_without_starting_a_worker(self):
        followup.save(CONFIG, self.record)
        retry = {"run": 99, "check": 123}
        with patch.object(followup, "artifact_retries", return_value=[retry]):
            action = self.plan({"unit": "pending", "security": "failure"})
        self.assertEqual(action["reruns"], [retry])

        def github(token, method, path, **kwargs):
            if path.endswith("/pulls/42"):
                return PR
            if method == "POST":
                self.assertEqual(followup.read(CONFIG, 42)["artifact_retries"]["99"]["check"], 123)
                return {}
            return {"head_sha": "head", "status": "completed"}

        with (
            patch.object(followup, "evidence", return_value=self.root),
            patch.object(followup, "job_id", return_value="run"),
            patch.object(followup, "github", side_effect=github) as calls,
            patch.object(run, "execute_build") as build,
        ):
            self.assertTrue(followup.maintain(CONFIG, PR, action, "secret"))
        build.assert_not_called()
        calls.assert_any_call("secret", "POST", "/repos/org/repo/actions/runs/99/rerun-failed-jobs")
        self.assertEqual(followup.read(CONFIG, 42)["attempts"], 1)
        self.assertNotIn("last_attempt", followup.read(CONFIG, 42))

    def test_scheduler_prioritizes_its_published_draft_over_new_issues(self):
        issue = {
            "number": 7,
            "title": "fix: another bug",
            "body": "Fix it",
            "state": "open",
            "assignees": [],
            "labels": [],
        }
        action = {"key": "revision"}
        with (
            patch.object(monitor.issues, "_kv_get", return_value={"done": {}, "triaged": {}}),
            patch.object(monitor.issues, "_kv_set"),
            patch.object(monitor.issues, "_github_paginate", return_value=[issue]),
            patch.object(monitor.reviews, "_list_open_prs", return_value=[PR]),
            patch.object(monitor.reviews, "_get_pr", return_value=PR),
            patch.object(monitor, "job_id", return_value="run"),
            patch.object(monitor, "resume_reply", return_value=None),
            patch.object(monitor, "implement_issue") as implement,
            patch.object(followup, "adopt_reports"),
            patch.object(followup, "read", return_value=self.record),
            patch.object(followup, "plan", return_value=action),
            patch.object(followup, "maintain", return_value=True) as maintain,
        ):
            monitor.poll({**CONFIG, "issue_label": None, "max_tasks_per_poll": 1}, "secret")
        maintain.assert_called_once()
        implement.assert_not_called()

    def test_base_merge_is_tested_reviewed_and_pushed_to_the_existing_pr(self):
        seed, repository = self.root / "seed", self.root / "task.git"
        artifact = self.root / "artifact"
        artifact.mkdir()
        common.git(["init", "-b", "main", str(seed)])
        common.git(["config", "user.name", "Fixture"], cwd=seed)
        common.git(["config", "user.email", "fixture@example.test"], cwd=seed)
        (seed / "base.txt").write_text("original")
        common.git(["add", "."], cwd=seed)
        common.git(["commit", "-m", "base"], cwd=seed)
        original = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        common.git(["checkout", "-b", self.record["branch"]], cwd=seed)
        (seed / "feature.txt").write_text("retained task change")
        common.git(["add", "."], cwd=seed)
        common.git(["commit", "-m", "feature"], cwd=seed)
        head = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        common.git(["checkout", "main"], cwd=seed)
        (seed / "base.txt").write_text("new main behavior")
        common.git(["commit", "-am", "advance main"], cwd=seed)
        base = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
        common.git(["checkout", self.record["branch"]], cwd=seed)
        common.git(["clone", "--bare", str(seed), str(repository)])
        common.git(["--git-dir", str(repository), "config", "factory.base", original])
        pr = copy.deepcopy(PR)
        pr["head"]["sha"], pr["base"]["sha"] = head, base
        followup.save(CONFIG, {**self.record, "head": head, "issue": None})
        action = {
            "number": 42,
            "branch": self.record["branch"],
            "head": head,
            "base": base,
            "behind": True,
            "failures": {},
            "key": "one-attempt",
            "title": None,
            "reply": None,
        }

        def execute(command, cwd, **kwargs):
            result = subprocess.run(
                ["bash", "-c", command], cwd=cwd, capture_output=True, text=True
            )
            return SimpleNamespace(
                exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr
            )

        @contextmanager
        def worker(root, configs):
            yield SimpleNamespace(working_dir=str(root), execute_command=execute)

        def worktree(workspace):
            source = Path(workspace.working_dir)
            checkout = source.parent / "worktree"
            common.git(
                ["worktree", "add", "-b", "openhands/test", str(checkout), "main"], cwd=source
            )
            workspace.working_dir = str(checkout)
            return "fixture"

        def implement(workspace, *args, **kwargs):
            self.assertEqual(
                (Path(workspace.working_dir) / "base.txt").read_text(), "new main behavior"
            )
            self.assertEqual(
                (Path(workspace.working_dir) / "feature.txt").read_text(), "retained task change"
            )
            return run.ImplementationResult(
                status="IMPLEMENTED",
                summary="Preserve scanning on the updated base.",
                title="fix: restore scanning",
            )

        def review(configs, states, request, results):
            self.assertEqual(states["example"]["base"], base)
            self.assertEqual(results, {"example": 0})
            return run.ReviewResult(verdict="PASS", summary="Reviewed against current main")

        def push(repo, branch, credential):
            pr["head"]["sha"] = common.git(
                ["--git-dir", str(repo), "rev-parse", branch]
            ).stdout.strip()

        with (
            patch.object(followup, "evidence", return_value=artifact),
            patch.object(followup, "job_id", return_value="run"),
            patch.object(followup, "github", side_effect=lambda *args, **kwargs: copy.deepcopy(pr)),
            patch.object(followup, "failure_context", return_value=""),
            patch.object(run, "github", side_effect=lambda *args, **kwargs: copy.deepcopy(pr)),
            patch.object(run, "task_repository", return_value=(repository, self.record["branch"])),
            patch.object(run, "git_identity", return_value=("Fixture", "fixture@example.test")),
            patch.object(run, "worker", worker),
            patch.object(run, "worktree", worktree),
            patch.object(run, "converse", implement),
            patch.object(run, "review_changes", side_effect=review),
            patch.object(run.issues, "_push_branch", side_effect=push) as pushed,
            patch.object(run.issues, "_open_pull_request") as opened,
        ):
            self.assertTrue(followup.maintain(CONFIG, copy.deepcopy(pr), action, ""))
        pushed.assert_called_once()
        opened.assert_not_called()
        common.git(
            ["--git-dir", str(repository), "merge-base", "--is-ancestor", base, pr["head"]["sha"]]
        )
        self.assertEqual(followup.read(CONFIG, 42)["head"], pr["head"]["sha"])
        self.assertEqual(json.loads((artifact / "result.json").read_text())["validation"], "PASSED")


class ArtifactRetryTests(unittest.TestCase):
    def test_only_artifact_service_failures_on_the_current_revision_can_be_retried(self):
        check = {
            "id": 123,
            "name": "security",
            "conclusion": "failure",
            "details_url": "https://github.com/org/repo/actions/runs/99/job/100",
        }
        annotations = [{"message": "Failed to FinalizeArtifact: 403 Forbidden"}]
        for failed_step, head, previous, expected in (
            ("Upload Semgrep SARIF as workflow artifact", "head", {}, [{"run": 99, "check": 123}]),
            ("Run Semgrep", "head", {}, []),
            ("Upload Semgrep SARIF as workflow artifact", "old-head", {}, []),
            (
                "Upload Semgrep SARIF as workflow artifact",
                "head",
                {"artifact_retries": {"99": {"check": 123}}},
                [],
            ),
        ):
            with (
                self.subTest(step=failed_step, head=head, previous=previous),
                patch.object(followup, "latest_check_runs", return_value=[check]),
                patch.object(followup.issues, "_github_paginate", return_value=annotations),
                patch.object(
                    followup,
                    "github",
                    side_effect=[
                        {"steps": [{"name": failed_step, "conclusion": "failure"}]},
                        {"head_sha": head, "status": "completed"},
                    ],
                ),
            ):
                self.assertEqual(
                    followup.artifact_retries(CONFIG, PR, "secret", previous), expected
                )
