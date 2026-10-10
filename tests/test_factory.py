"""Regression checks for policy, ownership, retained work and upstream publication."""

import json
import os
import tempfile
import unittest
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
from pipeline_fixture import PipelineScenario, seed_repository
from pipeline_fixture import execute_local as local_git_command

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "trunk",
    "issue_intake": "automatic",
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
        # [utest~im-factory-PolicyTests-only_approved_unassigned_issues_are_eligible~1->req~im-issue-ownership~1]
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
            patch("approval.approved_issue", return_value=(ISSUE, {"label_event": "fixture"})),
            patch.object(monitor, "github", side_effect=api),
            patch.object(monitor, "build", side_effect=build),
        ):
            monitor.implement_issue(CONFIG, ISSUE, "secret")
        # [utest~im-factory-OwnershipTests-success_claims_before_build_and_keeps_assignment~1->req~im-issue-ownership~1]
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
                patch("approval.approved_issue", return_value=(ISSUE, {"label_event": "fixture"})),
                patch.object(monitor, "github", side_effect=api),
                patch.object(monitor, "build", side_effect=RuntimeError("tests failed")),
            ):
                # [utest~im-factory-OwnershipTests-failure_releases_only_our_own_assignment~1->req~im-issue-ownership~1]
                with self.assertRaises(RuntimeError):
                    monitor.implement_issue(CONFIG, ISSUE, "secret")
            self.assertEqual(calls.count("DELETE"), deletes)


class UpstreamTests(unittest.TestCase):
    def test_shared_github_transport_handles_requests_pages_and_api_errors(self):
        import io
        import urllib.error

        self.assertIs(common.issues._github_request, common.github_client.github_request)
        self.assertIs(common.reviews._github_request, common.github_client.github_request)
        self.assertIs(common.issues._github_paginate, common.github_client.github_paginate)
        self.assertIs(common.reviews._github_paginate, common.github_client.github_paginate)
        requests = []
        responses = iter([{"id": 7}, [{"id": 1}, {"id": 2}], [{"id": 3}]])

        def respond(request, timeout):
            requests.append(request)
            response = io.BytesIO(json.dumps(next(responses)).encode())
            response.headers = {"fixture": "response"}
            return response

        with patch.object(common.github_client, "urlopen", side_effect=respond):
            self.assertEqual(
                common.github(
                    "fixture-token", "POST", "/repos/org/repo/issues", body={"title": "A"}
                ),
                {"id": 7},
            )
            self.assertEqual(
                common.issues._github_paginate(
                    "fixture-token", "/repos/org/repo/issues", {"per_page": 2, "state": "open"}
                ),
                [{"id": 1}, {"id": 2}, {"id": 3}],
            )
        self.assertEqual(json.loads(requests[0].data), {"title": "A"})
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer fixture-token")
        self.assertIn("page=1", requests[1].full_url)
        self.assertIn("page=2", requests[2].full_url)
        self.assertTrue(all("state=open" in request.full_url for request in requests[1:]))
        error = urllib.error.HTTPError("https://api.github.com/fixture", 403, "denied", {}, None)
        with patch.object(common.github_client, "urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                common.github("fixture-token", "GET", "/repos/org/repo/issues")

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
        configured = common.projects(Path(__file__).resolve().parents[1] / "examples/config")
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
                    summary="Allow users to retry failed invitation validation.",
                    browser_result={
                        "accepted_gaps": {"Live Telegram": "No disposable session"},
                        "checks": [
                            {
                                "name": "Live Telegram",
                                "status": "BLOCKED",
                                "observed": "Reaction delivery unavailable",
                            }
                        ],
                    },
                )
            # [utest~im-factory-UpstreamTests-actual_upstream_push_and_draft_payload_use_task_branch~1->req~im-publication-gate~1]
            self.assertTrue(captured[0]["draft"])
            self.assertEqual(captured[0]["base"], "trunk")
            self.assertEqual(captured[0]["head"], "factory/feature")
            self.assertTrue(
                captured[0]["body"].startswith("Allow users to retry failed invitation validation.")
            )
            self.assertNotIn("Approved feature", captured[0]["body"])
            self.assertNotIn("Factory", captured[0]["body"])
            self.assertNotIn("Canvas", captured[0]["body"])
            self.assertNotIn(str(root), captured[0]["body"])
            self.assertIn("Accepted verification gaps", captured[0]["body"])
            self.assertIn("Not verified: Reaction delivery unavailable", captured[0]["body"])
            self.assertIn("Maintainer acceptance: No disposable session", captured[0]["body"])
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
                with (
                    patch.object(agent, "Conversation", return_value=conversation),
                    patch.dict(os.environ, {"FACTORY_CODEX_MODEL": "gpt-6-astra/xhigh"}),
                ):
                    if final.startswith("{"):
                        result = agent.converse(
                            workspace, "Review", response_model=run.ReviewResult
                        )
                        self.assertEqual(result.verdict, "PASS")
                    else:
                        with self.assertRaisesRegex(
                            agent.StructuredResponseError,
                            "invalid ReviewResult after 2 response attempts",
                        ):
                            agent.converse(workspace, "Review", response_model=run.ReviewResult)
                self.assertEqual(conversation.run.call_count, 2)
                conversation.close.assert_called_once()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        identity = patch.object(run, "git_identity", return_value=("Fixture", "fixture@localhost"))
        identity.start()
        self.addCleanup(identity.stop)

    def test_failed_tests_or_review_keep_branch_and_never_publish(self):
        for exit_code, verdict in ((1, "PASS"), (0, "CHANGES_REQUESTED")):
            with (
                self.subTest(exit_code=exit_code, verdict=verdict),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                seed, bare, artifact = root / "seed", root / "task.git", root / "artifact"
                artifact.mkdir()
                state = seed_repository(seed, bare, files={"code.txt": "base"})
                scenario = PipelineScenario(
                    root,
                    executor=lambda command, cwd, **kwargs: (
                        local_git_command(command, cwd)
                        if command.startswith("git ")
                        else SimpleNamespace(exit_code=exit_code, stdout="test evidence", stderr="")
                    ),
                )

                def converse(workspace, prompt, mode="read-only", *args, **kwargs):
                    if mode == "agent-full-access":
                        (Path(workspace.working_dir) / "code.txt").write_text("implemented")
                        return run.ImplementationResult(status="IMPLEMENTED", summary="Implemented")
                    evidence = json.loads(
                        prompt.split(
                            "Controller test evidence (read the complete logs when assessing failures):\n",
                            1,
                        )[1].splitlines()[0]
                    )
                    self.assertEqual(evidence["example"]["command"], "unused")
                    self.assertEqual(
                        Path(evidence["example"]["logs"]["tests-0.log"]).read_text(),
                        "test evidence",
                    )
                    return run.ReviewResult(verdict=verdict, summary="Review findings")

                config = {**CONFIG, "repair_attempts": 0, "test_command": "unused"}
                with (
                    scenario.activate(implement=converse, review=converse),
                    patch.object(run, "publish") as publish,
                ):
                    # [utest~im-factory-PipelineTests-failed_tests_or_review_keep_branch_and_never_publish~1->req~im-publication-gate~1]
                    with self.assertRaises(RuntimeError):
                        run.execute_build(
                            [config],
                            "task",
                            "approved spec",
                            "token",
                            None,
                            True,
                            artifact,
                            {"example": state},
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
            (0, "PASS", None, False),
            (1, "PASS", None, False),
            (0, "CHANGES_REQUESTED", None, False),
            (0, "PASS", "second", False),
            (0, "PASS", None, True),
        )
        for test_exit, verdict, publication_failure, engine_error in scenarios:
            with (
                self.subTest(
                    test_exit=test_exit,
                    verdict=verdict,
                    publication_failure=publication_failure,
                    engine_error=engine_error,
                ),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp)
                artifact = root / "artifact"
                artifact.mkdir()
                configs, states = [], {}
                for project in ("first", "second"):
                    seed, bare = root / project, root / (project + ".git")
                    state = seed_repository(seed, bare, files={"code.txt": "base"})
                    configs.append(
                        {
                            **CONFIG,
                            "project": project,
                            "repository": "org/" + project,
                            "repair_attempts": 3 if engine_error else 0,
                            "test_command": "test -f code.txt",
                        }
                    )
                    states[project] = state
                tested = []

                def execute(command, cwd, **kwargs):
                    if command.startswith("git "):
                        return local_git_command(command, cwd)
                    tested.append(Path(cwd).name)
                    return SimpleNamespace(
                        exit_code=test_exit if Path(cwd).name == "second" else 0,
                        stdout="test evidence",
                        stderr="",
                    )

                scenario = PipelineScenario(root, executor=execute)

                def converse(workspace, prompt, mode="read-only", *args, **kwargs):
                    if mode == "agent-full-access":
                        for project in states:
                            target = Path(workspace.working_dir) / project / "code.txt"
                            # On continuation the previous task change must be present.
                            target.write_text(target.read_text() + " implemented")
                        return run.ImplementationResult(
                            status="IMPLEMENTED", summary="Implemented both"
                        )
                    self.assertIn("first", prompt)
                    self.assertIn("second", prompt)
                    return run.ReviewResult(verdict=verdict, summary="Review findings")

                def publish(config, *args, **kwargs):
                    self.assertEqual(tested[-2:], ["first", "second"])
                    if config["project"] == publication_failure:
                        raise RuntimeError("GitHub unavailable")
                    return "https://example.test/" + config["project"]

                with (
                    scenario.activate(implement=converse, review=converse),
                    patch.object(run, "publish", side_effect=publish) as published,
                    patch.object(
                        run.authorization,
                        "BRIDGE",
                        str(root / "missing") if engine_error else run.authorization.BRIDGE,
                    ),
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

                    if test_exit or verdict != "PASS" or publication_failure or engine_error:
                        # [utest~im-factory-PipelineTests-group_validation_retention_and_partial_publication~1->req~im-group-publication~1]
                        with self.assertRaises(RuntimeError):
                            invoke()
                    else:
                        self.assertEqual(invoke()["status"], "PASSED")
                        # A new native run retains new immutable evidence, reusing the bare branches.
                        artifact = root / "next-run-artifact"
                        artifact.mkdir()
                        self.assertEqual(invoke()["status"], "PASSED")
                    if test_exit or verdict != "PASS" or engine_error:
                        published.assert_not_called()
                        if engine_error:
                            self.assertEqual(tested, ["first", "second"])
                            result = json.loads((artifact / "result.json").read_text())
                            self.assertEqual(result["status"], "FAILED")
                            self.assertIn("Cedar authorization unavailable", result["error"])
                    elif publication_failure:
                        result = json.loads((artifact / "result.json").read_text())
                        self.assertEqual(result["status"], "PUBLICATION_FAILED")
                        self.assertEqual(
                            result["repositories"]["first"]["pull_request"],
                            "https://example.test/first",
                        )
                    # [utest~im-authorization-pipeline~1->req~im-authorization~1]
                    receipt_path = artifact / "authorization" / "attempt-0.json"
                    if test_exit:
                        self.assertFalse(receipt_path.exists())
                    else:
                        receipt = json.loads(receipt_path.read_text())
                        self.assertEqual(
                            receipt["status"],
                            "ERROR" if engine_error else ("ALLOW" if verdict == "PASS" else "DENY"),
                            receipt,
                        )
                        self.assertNotIn("python_allowed", receipt)
                        self.assertEqual(
                            set(json.loads(receipt["request"]["source_identity"])),
                            {"first", "second"},
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
                # [utest~im-factory-PipelineTests-group_acquires_all_repository_locks_before_preparing_work~1->req~im-repository-locks~1]
                with self.assertRaises(BlockingIOError):
                    run.build_group(configs, "task", "spec", {"first": "sha", "second": "sha"})
            prepare.assert_not_called()
            with common.lock("first.build"):
                pass  # The earlier lock was released after the second acquisition failed.


if __name__ == "__main__":
    unittest.main()
