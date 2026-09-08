"""Regression checks for policy, ownership, retained work and upstream publication."""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent
import common
import monitor
import policy
import run
import sandbox
from openhands.sdk import Message, TextContent
from openhands.sdk.event import MessageEvent

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "trunk",
    "issue_label": "factory:approved",
    "assignee": "factory-bot",
    "required_checks": ["unit", "integration"],
    "accepted_check_results": ["success", "neutral", "skipped"],
    "branch_prefix": "factory",
    "publish_draft": True,
}
ISSUE = {
    "number": 42,
    "title": "A feature",
    "body": "Approved specification",
    "state": "open",
    "labels": [{"name": "factory:approved"}],
    "assignees": [],
}


class PolicyTests(unittest.TestCase):
    def test_maximum_task_and_project_names_fit_internal_paths(self):
        name = common.identifier("a" * 80)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(common, "DATA", Path(directory)),
        ):
            with common.lock(name + ".build"):
                self.assertTrue((Path(directory) / "locks" / (name + ".build.lock")).exists())
        self.assertEqual(common.identifier("r" * 36 + "-" + name, 117), "r" * 36 + "-" + name)
        for invalid in ("a" * 81, "../repo", "repo/name"):
            with self.assertRaises(ValueError):
                common.identifier(invalid)

    def test_duplicate_check_name_cannot_hide_failure(self):
        data = {
            "check_runs": [
                {"name": "unit", "status": "completed", "conclusion": "failure"},
                {"name": "unit", "status": "completed", "conclusion": "success"},
            ]
        }
        with patch.object(policy, "github", side_effect=[data, {"statuses": []}]):
            self.assertEqual(policy.checks_for("token", "owner/repo", "sha")["unit"], "failure")

    def test_status_checks_are_paginated(self):
        first = [{"context": f"check-{i}", "state": "success"} for i in range(100)]
        with patch.object(
            policy,
            "github",
            side_effect=[
                {"check_runs": []},
                {"statuses": first},
                {"statuses": [{"context": "last", "state": "failure"}]},
            ],
        ):
            self.assertEqual(policy.checks_for("token", "owner/repo", "sha")["last"], "failure")

    def test_only_approved_unassigned_issues_are_eligible(self):
        self.assertTrue(policy.issue_eligible(ISSUE, CONFIG))
        for change in (
            {"assignees": [{"login": "human"}]},
            {"state": "closed"},
            {"labels": []},
            {"pull_request": {"url": "a-pr"}},
        ):
            with self.subTest(change=change):
                self.assertFalse(policy.issue_eligible({**ISSUE, **change}, CONFIG))

    def test_absent_pending_failed_and_incomplete_ci_do_not_pass(self):
        for checks in (
            {},
            {"unit": "success"},
            {"unit": "success", "integration": "pending"},
            {"unit": "success", "integration": "failure"},
            {"unit": "success", "integration": "cancelled"},
        ):
            with self.subTest(checks=checks):
                self.assertFalse(policy.checks_pass(checks, CONFIG))
        self.assertTrue(policy.checks_pass({"unit": "success", "integration": "success"}, CONFIG))

    def test_draft_or_closed_pr_does_not_even_query_ci(self):
        for pr in ({"state": "open", "draft": True}, {"state": "closed", "draft": False}):
            with patch.object(policy, "checks_for") as checks:
                self.assertFalse(policy.pr_eligible("secret", pr, CONFIG))
                checks.assert_not_called()

    def test_synthetic_merge_checks_and_head_failures(self):
        pr = {"state": "open", "draft": False, "head": {"sha": "head"}, "merge_commit_sha": "merge"}
        with patch.object(
            policy, "checks_for", side_effect=[{}, {"unit": "success", "integration": "success"}]
        ):
            self.assertTrue(policy.pr_eligible("secret", pr, CONFIG))
        with patch.object(
            policy,
            "checks_for",
            side_effect=[{"unit": "failure"}, {"unit": "success", "integration": "success"}],
        ):
            self.assertFalse(policy.pr_eligible("secret", pr, CONFIG))

    def test_ci_completion_is_rechecked_without_updated_at_change(self):
        pr = {"state": "open", "draft": False, "head": {"sha": "head"}, "updated_at": "unchanged"}
        with patch.object(
            policy,
            "checks_for",
            side_effect=[{"unit": "pending"}, {"unit": "success", "integration": "success"}],
        ):
            self.assertFalse(policy.pr_eligible("secret", pr, CONFIG))
            self.assertTrue(policy.pr_eligible("secret", pr, CONFIG))


class OwnershipTests(unittest.TestCase):
    def test_assigned_issue_never_reaches_assignment_or_build(self):
        with (
            patch.object(
                monitor.issues,
                "_get_issue",
                return_value={**ISSUE, "assignees": [{"login": "human"}]},
            ),
            patch.object(monitor, "github") as api,
            patch.object(monitor, "build") as build,
        ):
            self.assertIsNone(monitor.implement_issue(CONFIG, ISSUE, "secret"))
            api.assert_not_called()
            build.assert_not_called()

    def test_success_claims_before_build_and_keeps_assignment(self):
        order = []

        def api(token, method, path, **kwargs):
            order.append(method)
            if method == "POST":
                return {**ISSUE, "assignees": [{"login": "factory-bot"}]}
            return {"sha": "base"}

        def build(*args, **kwargs):
            self.assertEqual(order[0], "POST")
            self.assertTrue(args[0]["publish_draft"])
            return {"status": "PASSED"}

        with (
            patch.object(monitor.issues, "_get_issue", return_value=ISSUE),
            patch.object(monitor.issues, "_github_paginate", return_value=[]),
            patch.object(monitor, "github", side_effect=api),
            patch.object(monitor, "build", side_effect=build),
        ):
            monitor.implement_issue(CONFIG, ISSUE, "secret")
        self.assertNotIn("DELETE", order)

    def test_failure_releases_only_our_own_assignment(self):
        for current, deletes in (([{"login": "factory-bot"}], 1), ([{"login": "human"}], 0)):
            calls = []

            def api(token, method, path, **kwargs):
                calls.append(method)
                return (
                    {**ISSUE, "assignees": [{"login": "factory-bot"}]}
                    if method == "POST"
                    else {"sha": "base"}
                )

            with (
                patch.object(
                    monitor.issues,
                    "_get_issue",
                    side_effect=[ISSUE, {**ISSUE, "assignees": current}],
                ),
                patch.object(monitor.issues, "_github_paginate", return_value=[]),
                patch.object(monitor, "github", side_effect=api),
                patch.object(monitor, "build", side_effect=RuntimeError("tests failed")),
            ):
                with self.assertRaises(RuntimeError):
                    monitor.implement_issue(CONFIG, ISSUE, "secret")
            self.assertEqual(calls.count("DELETE"), deletes)


class UpstreamTests(unittest.TestCase):
    def test_concurrent_login_refresh_never_overwrites_newer_binding(self):
        parent = Mock()
        parent.replace_versioned_secret.side_effect = ValueError("credential_version_conflict")
        sandbox.sync_credential(parent, 1, "old", "refreshed")
        parent.replace_versioned_secret.assert_called_once_with("CODEX_AUTH_JSON", 1, "refreshed")
        parent.replace_versioned_secret.side_effect = ValueError("unexpected storage error")
        with self.assertRaises(ValueError):
            sandbox.sync_credential(parent, 1, "old", "refreshed")

    def test_upstream_files_match_lock(self):
        import hashlib

        manifest = json.loads((common.ROOT / "upstream.lock.json").read_text())
        for name, item in manifest["files"].items():
            self.assertEqual(
                hashlib.sha256((common.ROOT / "upstream" / name).read_bytes()).hexdigest(),
                item["sha256"],
            )

    def test_repositories_share_profile_but_have_independent_configuration(self):
        projects = common.projects(Path(__file__).parent / "config")
        self.assertEqual(
            projects["factory-smoke"]["test_profile"], projects["factory-smoke-alt"]["test_profile"]
        )
        self.assertNotEqual(
            projects["factory-smoke"]["branch"], projects["factory-smoke-alt"]["branch"]
        )
        self.assertFalse(projects["factory-smoke"]["enabled"])

    def test_example_configuration_is_disabled_and_excludes_opt_in_fixtures(self):
        configured = common.projects()
        self.assertTrue(configured)
        self.assertTrue(all(item["repository"] for item in configured.values()))
        self.assertTrue(all(not item["enabled"] for item in configured.values()))
        self.assertNotIn("factory-smoke", configured)
        self.assertNotIn("factory-smoke-alt", configured)

    def test_repository_command_does_not_require_a_test_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "repositories").mkdir()
            (directory / "defaults.json").write_text('{"required_checks": ["*"]}')
            (directory / "repositories/example.json").write_text(
                json.dumps({"repository": "owner/repo", "test_command": "make test"})
            )
            config = common.projects(directory)["example"]
        self.assertEqual(
            sandbox.mounts(Path("/workspaces/job-example"), config),
            ["/workspaces/job-example:/workspaces/job-example"],
        )
        self.assertEqual(
            sandbox.mounts(Path("/workspaces/job-example"), {**config, "test_profile": "shared"})[
                -1
            ],
            "/profiles/shared:/factory-tests/shared:ro",
        )

    def test_factory_groups_reference_existing_repository_settings(self):
        directory = Path(__file__).parent / "config"
        self.assertEqual(
            common.factories(directory)["smoke-pair"], ["factory-smoke", "factory-smoke-alt"]
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "factories").mkdir()
            for members in ([], ["missing"], ["one", "one"], "one"):
                (root / "factories/group.json").write_text(json.dumps({"repositories": members}))
                with patch.object(
                    common, "projects", return_value={"one": {"repository": "org/one"}}
                ):
                    with self.assertRaises(ValueError):
                        common.factories(root)

    def test_group_profiles_are_mounted_once_and_separately(self):
        configs = [{"test_profile": "app"}, {"test_profile": "web"}, {"test_profile": "app"}, {}]
        self.assertEqual(
            sandbox.mounts(Path("/workspaces/job"), configs),
            [
                "/workspaces/job:/workspaces/job",
                "/profiles/app:/factory-tests/app:ro",
                "/profiles/web:/factory-tests/web:ro",
            ],
        )

    def test_actual_upstream_push_and_draft_payload_use_task_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, remote, bare = root / "source", root / "remote.git", root / "task.git"
            common.git(["init", "-b", "trunk", str(source)])
            common.git(["config", "user.name", "Fixture"], cwd=source)
            common.git(["config", "user.email", "fixture@localhost"], cwd=source)
            (source / "code.txt").write_text("base")
            common.git(["add", "."], cwd=source)
            common.git(["commit", "-m", "base"], cwd=source)
            base = common.git(["rev-parse", "HEAD"], cwd=source).stdout.strip()
            common.git(["checkout", "-b", "factory/feature"], cwd=source)
            (source / "code.txt").write_text("implemented")
            common.git(["commit", "-am", "implemented"], cwd=source)
            head = common.git(["rev-parse", "HEAD"], cwd=source).stdout.strip()
            common.git(["init", "--bare", str(remote)])
            common.git(["clone", "--bare", str(source), str(bare)])
            common.git(["--git-dir", str(bare), "remote", "set-url", "origin", str(remote)])
            captured = []

            def github(token, method, path, params=None, body=None):
                captured.append(body)
                return {"html_url": "https://example.test/draft/1", "draft": body["draft"]}, {}

            with patch.object(common.issues, "_github_request", side_effect=github):
                url = run.publish(
                    CONFIG,
                    "feature",
                    bare,
                    "factory/feature",
                    root,
                    "Approved feature",
                    "fixture-token",
                )
            self.assertTrue(captured[0]["draft"])
            self.assertEqual(captured[0]["base"], "trunk")
            self.assertEqual(captured[0]["head"], "factory/feature")
            pushed = common.git(
                ["--git-dir", str(remote), "rev-parse", "refs/heads/factory/feature"]
            ).stdout.strip()
            self.assertEqual(pushed, head)
            self.assertNotEqual(pushed, base)
            self.assertIn("draft", url)

    def test_revoked_approval_or_changed_owner_prevents_any_push(self):
        for issue in (
            {**ISSUE, "labels": []},
            {**ISSUE, "assignees": [{"login": "human"}]},
            {**ISSUE, "state": "closed"},
        ):
            with (
                patch.object(run, "github", return_value=issue),
                patch.object(run.issues, "_push_branch") as push,
            ):
                with self.assertRaises(RuntimeError):
                    run.publish(
                        CONFIG,
                        "task",
                        Path("/unused"),
                        "factory/task",
                        Path("/unused"),
                        "spec",
                        "secret",
                        issue=42,
                    )
                push.assert_not_called()


class ReviewResponseTests(unittest.TestCase):
    def test_native_conversation_restates_malformed_output_and_fails_closed(self):
        for final in ('{"verdict":"PASS","summary":"No defects"}', "PASS somewhere in prose"):
            with self.subTest(final=final):
                conversation = Mock()
                conversation.state.execution_status.value = "finished"
                workspace = Mock()
                workspace.get_secrets.return_value = {"CODEX_AUTH_JSON": "fixture"}
                responses = iter(["Progress text. PASS followed by more text", final])
                conversation.state.events = []

                def respond():
                    conversation.state.events.append(
                        MessageEvent(
                            source="agent",
                            llm_message=Message(
                                role="assistant", content=[TextContent(text=next(responses))]
                            ),
                        )
                    )

                conversation.run.side_effect = respond
                with patch.object(agent, "Conversation", return_value=conversation):
                    if final.startswith("{"):
                        result = agent.converse(
                            workspace, "Review", response_model=run.ReviewResult
                        )
                        self.assertEqual(result.verdict, "PASS")
                    else:
                        with self.assertRaisesRegex(RuntimeError, "valid structured verdict"):
                            agent.converse(workspace, "Review", response_model=run.ReviewResult)
                self.assertEqual(conversation.run.call_count, 2)
                conversation.close.assert_called_once()


class PipelineTests(unittest.TestCase):
    def test_failed_tests_or_review_keep_branch_and_never_publish(self):
        for exit_code, verdict in ((1, "PASS"), (0, "CHANGES_REQUESTED")):
            with (
                self.subTest(exit_code=exit_code, verdict=verdict),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                seed, bare, artifact = root / "seed", root / "task.git", root / "artifact"
                artifact.mkdir()
                common.git(["init", "-b", "factory/task", str(seed)])
                common.git(["config", "user.name", "Fixture"], cwd=seed)
                common.git(["config", "user.email", "fixture@localhost"], cwd=seed)
                (seed / "code.txt").write_text("base")
                common.git(["add", "."], cwd=seed)
                common.git(["commit", "-m", "base"], cwd=seed)
                base = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
                common.git(["clone", "--bare", str(seed), str(bare)])

                @contextmanager
                def worker(job, config):
                    yield SimpleNamespace(
                        working_dir=str(job / "source"),
                        execute_command=lambda *a, **k: SimpleNamespace(
                            exit_code=exit_code, stdout="test evidence", stderr=""
                        ),
                    )

                def worktree(workspace):
                    source = Path(workspace.working_dir)
                    checkout = source.parent / "worktree"
                    common.git(
                        ["worktree", "add", "-b", "openhands/test", str(checkout), "main"],
                        cwd=source,
                    )
                    workspace.working_dir = str(checkout)
                    return "unused-by-mocked-agent"

                def converse(workspace, prompt, mode="read-only", *args, **kwargs):
                    if mode == "agent-full-access":
                        (Path(workspace.working_dir) / "code.txt").write_text("implemented")
                        return "Implemented"
                    return run.ReviewResult(verdict=verdict, summary="Review findings")

                config = {**CONFIG, "repair_attempts": 0, "test_command": "unused"}
                with (
                    patch.object(run, "DATA", root),
                    patch.object(run, "worker", worker),
                    patch.object(run, "worktree", worktree),
                    patch.object(run, "converse", converse),
                    patch.object(run, "publish") as publish,
                ):
                    with self.assertRaises(RuntimeError):
                        run.execute_build(
                            [config],
                            "task",
                            "approved spec",
                            "token",
                            None,
                            True,
                            artifact,
                            {
                                "example": {
                                    "repository": str(bare),
                                    "base": base,
                                    "branch": "factory/task",
                                }
                            },
                        )
                    publish.assert_not_called()
                self.assertEqual(
                    common.git(["--git-dir", str(bare), "show", "factory/task:code.txt"]).stdout,
                    "implemented",
                )
                self.assertEqual(list(root.glob("job-*")), [])
                self.assertEqual(
                    json.loads((artifact / "result.json").read_text())["status"], "FAILED"
                )

    def test_group_validation_retention_and_partial_publication(self):
        scenarios = (
            (0, "PASS", None),
            (1, "PASS", None),
            (0, "CHANGES_REQUESTED", None),
            (0, "PASS", "second"),
        )
        for test_exit, verdict, publication_failure in scenarios:
            with (
                self.subTest(
                    test_exit=test_exit, verdict=verdict, publication_failure=publication_failure
                ),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                artifact = root / "artifact"
                artifact.mkdir()
                configs, states = [], {}
                for project in ("first", "second"):
                    seed, bare = root / project, root / (project + ".git")
                    common.git(["init", "-b", "factory/task", str(seed)])
                    common.git(["config", "user.name", "Fixture"], cwd=seed)
                    common.git(["config", "user.email", "fixture@localhost"], cwd=seed)
                    (seed / "code.txt").write_text("base")
                    common.git(["add", "."], cwd=seed)
                    common.git(["commit", "-m", "base"], cwd=seed)
                    base = common.git(["rev-parse", "HEAD"], cwd=seed).stdout.strip()
                    common.git(["clone", "--bare", str(seed), str(bare)])
                    configs.append(
                        {
                            **CONFIG,
                            "project": project,
                            "repository": "org/" + project,
                            "repair_attempts": 0,
                            "test_command": "test -f code.txt",
                        }
                    )
                    states[project] = {
                        "repository": str(bare),
                        "base": base,
                        "branch": "factory/task",
                    }
                tested = []

                def execute(command, cwd, **kwargs):
                    tested.append(Path(cwd).name)
                    return SimpleNamespace(
                        exit_code=test_exit if Path(cwd).name == "second" else 0,
                        stdout="test evidence",
                        stderr="",
                    )

                @contextmanager
                def worker(job, config):
                    yield SimpleNamespace(working_dir=str(job / "source"), execute_command=execute)

                def worktree(workspace):
                    source = Path(workspace.working_dir)
                    checkout = source.parent.parent / "worktrees" / source.name
                    checkout.parent.mkdir(exist_ok=True)
                    common.git(
                        ["worktree", "add", "-b", "openhands/test", str(checkout), "main"],
                        cwd=source,
                    )
                    workspace.working_dir = str(checkout)
                    return "unused"

                def converse(workspace, prompt, mode="read-only", *args, **kwargs):
                    if mode == "agent-full-access":
                        for project in states:
                            target = Path(workspace.working_dir) / project / "code.txt"
                            # On continuation the previous task change must be present.
                            target.write_text(target.read_text() + " implemented")
                        return "Implemented both"
                    self.assertIn("first", prompt)
                    self.assertIn("second", prompt)
                    return run.ReviewResult(verdict=verdict, summary="Review findings")

                def publish(config, *args, **kwargs):
                    self.assertEqual(tested[-2:], ["first", "second"])
                    if config["project"] == publication_failure:
                        raise RuntimeError("GitHub unavailable")
                    return "https://example.test/" + config["project"]

                with (
                    patch.object(run, "DATA", root),
                    patch.object(run, "worker", worker),
                    patch.object(run, "worktree", worktree),
                    patch.object(run, "converse", converse),
                    patch.object(run, "publish", side_effect=publish) as published,
                ):

                    def invoke():
                        return run.execute_build(
                            configs,
                            "task",
                            "approved group spec",
                            "token",
                            None,
                            True,
                            artifact,
                            states,
                        )

                    if test_exit or verdict != "PASS" or publication_failure:
                        with self.assertRaises(RuntimeError):
                            invoke()
                    else:
                        self.assertEqual(invoke()["status"], "PASSED")
                        # Reuse the same bare branches with a fresh workspace and full history.
                        self.assertEqual(invoke()["status"], "PASSED")
                    if test_exit or verdict != "PASS":
                        published.assert_not_called()
                    elif publication_failure:
                        result = json.loads((artifact / "result.json").read_text())
                        self.assertEqual(result["status"], "PUBLICATION_FAILED")
                        self.assertEqual(
                            result["repositories"]["first"]["pull_request"],
                            "https://example.test/first",
                        )
                    for state in states.values():
                        content = common.git(
                            ["--git-dir", state["repository"], "show", "factory/task:code.txt"]
                        ).stdout
                        self.assertIn("implemented", content)
                    self.assertEqual(list(root.glob("job-*")), [])

    def test_group_acquires_all_repository_locks_before_preparing_work(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(common, "DATA", Path(temp)),
            patch.object(run, "evidence", return_value=Path(temp)),
            patch.object(run, "job_id", return_value="run"),
            patch.object(run, "task_repository") as prepare,
        ):
            configs = [{**CONFIG, "project": p} for p in ("first", "second")]
            with common.lock("second.build"):
                with self.assertRaises(BlockingIOError):
                    run.build_group(configs, "task", "spec", {"first": "sha", "second": "sha"})
            prepare.assert_not_called()
            with common.lock("first.build"):
                pass  # The earlier lock was released after the second acquisition failed.


if __name__ == "__main__":
    unittest.main()
