"""Regression checks for configuration, task identity and durable scheduling policy."""

import copy
import datetime
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import agent
import common
import httpx
import monitor
import run

CONFIG = {
    "project": "example",
    "repository": "example/repo",
    "branch": "main",
    "branch_prefix": "factory",
    "issue_label": "factory:approved",
    "assignee": "factory-bot",
    "enabled": False,
    "schedule": "*/10 * * * *",
    "timezone": "UTC",
    "max_tasks_per_poll": 2,
    "daily_tasks": 1000,
}
ISSUE = {
    "number": 42,
    "title": "Approved feature",
    "body": "Implement the reviewed specification",
    "state": "open",
    "labels": [{"name": "factory:approved"}],
    "assignees": [],
}


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "factory_configure", common.ROOT / "configure.py"
        )
        cls.configure = importlib.util.module_from_spec(spec)
        # Configuration normally runs inside Canvas, which supplies this file.
        with (
            patch.object(Path, "read_text", return_value="offline-test-key"),
            patch.dict(os.environ, {"OH_SESSION_API_KEYS_0": "offline-test-key"}),
        ):
            spec.loader.exec_module(cls.configure)

    def test_registration_waits_for_native_commits_without_repeating_writes(self):
        trigger = {"type": "event", "source": "custom", "on": "fixture", "filter": "`false`"}
        saved = {
            "id": "automation",
            "name": "Fixture",
            "trigger": trigger,
            "tarball_path": "oh-internal://uploads/upload",
        }
        missing = httpx.Response(404, request=httpx.Request("GET", "http://fixture"))
        pending = httpx.HTTPStatusError("pending", request=missing.request, response=missing)
        responses = [
            {"id": "upload", "tarball_path": saved["tarball_path"]},
            pending,
            {"status": "COMPLETED"},
            {"id": "automation"},
            pending,
            {"id": "automation"},
            saved,
            {**saved, "trigger": {"on": "old"}},
            saved,
        ]
        with (
            patch.object(self.configure, "api", side_effect=responses) as api,
            patch.object(self.configure.time, "sleep"),
        ):
            self.configure.install({"name": "Fixture", "trigger": trigger}, {"job.py": b"pass"})
        self.assertEqual(
            [c.args[0] for c in api.call_args_list],
            ["POST", "GET", "GET", "POST", "GET", "GET", "PATCH", "GET", "GET"],
        )

    def test_native_readback_is_bounded_and_does_not_hide_permission_errors(self):
        for status in (404, 403):
            response = httpx.Response(status, request=httpx.Request("GET", "http://fixture"))
            error = httpx.HTTPStatusError(
                "unavailable", request=response.request, response=response
            )
            with (
                self.subTest(status=status),
                patch.object(self.configure, "api", side_effect=error) as api,
                patch.object(self.configure.time, "monotonic", side_effect=[0, 11]),
                self.assertRaises(RuntimeError if status == 404 else httpx.HTTPStatusError),
            ):
                self.configure.persisted("/api/automation/v1/fixture", {"id": "fixture"})
            api.assert_called_once_with("GET", "/api/automation/v1/fixture")

    def test_native_automation_bundle_loads_its_own_skill_files(self):
        from openhands.automation.execution import build_tarball

        job = {"project": "example", "request": "Test skill packaging"}
        payload = self.configure.files(job)
        with tempfile.TemporaryDirectory() as temp:
            with tarfile.open(fileobj=io.BytesIO(build_tarball(payload))) as archive:
                archive.extractall(temp, filter="data")
            self.assertEqual(json.loads(Path(temp, "job.json").read_text()), job)
            spec = importlib.util.spec_from_file_location(
                "bundled_traceability", Path(temp, "traceability/__init__.py")
            )
            bundled = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bundled)
            self.assertIsNone(bundled.scope_for({"project": "opted-out"}))
            with patch.object(agent, "__file__", str(Path(temp, "agent.py"))):
                for name in (
                    "factory-implementation",
                    "factory-review",
                    "factory-review-report",
                    "factory-browser-qa",
                ):
                    selected = agent.stage_context(name).skills[0]
                    self.assertEqual(selected.name, name)
                    self.assertTrue(selected.content.strip())
                    self.assertTrue(selected.source.startswith(temp))

    def test_configuration_finds_and_disables_a_schedule_after_the_first_page(self):
        old = {"id": "original-scan", "name": "Factory — example", "enabled": True}
        reply = {"id": "original-replies", "name": "Resume — example", "enabled": True}
        automations = [{"id": str(i), "name": f"Build — feature-{i}"} for i in range(105)] + [
            old,
            reply,
        ]

        for configured in ({"example": CONFIG}, {}):
            with self.subTest(removed=not configured):
                pages = []

                def api(method, path, **kwargs):
                    if path == "/api/automation/v1":
                        params = kwargs["params"]
                        pages.append(params)
                        offset, limit = params["offset"], params["limit"]
                        return {
                            "automations": automations[offset : offset + limit],
                            "total": len(automations),
                        }
                    if method == "GET" and path == "/api/agent-profiles/factory-codex":
                        return {"profile": {"id": "profile", "acp_model": "gpt-6-astra/xhigh"}}
                    return {}

                with (
                    patch.object(self.configure, "api", side_effect=api) as calls,
                    patch.object(self.configure, "projects", return_value=configured),
                    patch.object(self.configure, "factories", return_value={}),
                    patch.object(self.configure, "token", return_value="offline-token"),
                    patch.object(self.configure, "github", return_value={}),
                    patch.object(self.configure, "files", return_value={}),
                    patch.object(
                        self.configure, "install", side_effect=lambda d, f, i: {"id": i}
                    ) as install,
                    patch("reporting.write_report") as route,
                ):
                    self.configure.configure()
                self.assertFalse(
                    any(
                        c.args == ("POST", "/api/agent-profiles/factory-codex")
                        for c in calls.call_args_list
                    ),
                    "Reconfiguring must preserve the operator's saved model and profile edits",
                )
                self.assertEqual(
                    pages, [{"limit": 100, "offset": 0}, {"limit": 100, "offset": 100}]
                )
                if configured:
                    definition, _, previous = install.call_args_list[0].args
                    self.assertFalse(definition["enabled"])
                    self.assertEqual(previous, old["id"])
                    continuation, _, previous = install.call_args_list[1].args
                    self.assertFalse(continuation["enabled"])
                    self.assertEqual(previous, reply["id"])
                    route.assert_called_once_with(
                        CONFIG,
                        "reply-trigger",
                        {"automation_id": reply["id"], "scheduler_id": old["id"]},
                    )
                else:
                    install.assert_not_called()
                    calls.assert_any_call(
                        "PATCH", "/api/automation/v1/original-scan", json={"enabled": False}
                    )
                    calls.assert_any_call(
                        "PATCH", "/api/automation/v1/original-replies", json={"enabled": False}
                    )

    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_reconfigure_uploads_changed_scope_to_existing_automations(self):
        from intentbond.common import CheckError

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "config"
            (directory / "repositories").mkdir(parents=True)
            (directory / "traceability").mkdir()
            (directory / "defaults.json").write_text(
                json.dumps({**CONFIG, "required_checks": ["unit"]})
            )
            (directory / "repositories/example.json").write_text(
                json.dumps({"traceability_scope": "traceability/example.json"})
            )
            scope_path = directory / "traceability/example.json"
            scope = json.loads(
                (Path(__file__).parent / "fixtures/traceability-scope.json").read_text()
            )
            existing = [
                {"name": "Factory — example", "id": "scan"},
                {"name": "Resume — example", "id": "replies"},
            ]
            with (
                patch.object(common, "ROOT", root),
                patch.object(
                    self.configure, "api", return_value={"profile": {"id": "profile"}}
                ) as api,
                patch.object(self.configure, "records", return_value=existing),
                patch.object(self.configure, "token", return_value="offline-token"),
                patch.object(self.configure, "github", return_value={}),
                patch.object(
                    self.configure, "install", side_effect=lambda d, f, i: {"id": i}
                ) as install,
                patch("reporting.write_report"),
            ):
                for command in (
                    ["python", "-m", "unittest"],
                    ["bash", "-c", 'bash "$FACTORY_TESTS/run.sh"'],
                ):
                    scope["tests"]["command"] = command
                    scope_path.write_text(json.dumps(scope))
                    self.configure.configure()
                    for call, expected_id in zip(install.call_args_list[-2:], ("scan", "replies")):
                        _, payload, previous = call.args
                        uploaded = json.loads(payload["job.json"])["config"]
                        self.assertEqual(previous, expected_id)
                        self.assertEqual(uploaded["traceability_scope"], scope)
                        self.assertNotIn("test_command", uploaded)
                original = json.loads(install.call_args_list[0].args[1]["job.json"])
                self.assertEqual(
                    original["config"]["traceability_scope"]["tests"]["command"],
                    ["python", "-m", "unittest"],
                )
                scope_path.unlink()
                api.reset_mock()
                install.reset_mock()
                with self.assertRaises(CheckError):
                    self.configure.configure()
                api.assert_not_called()
                install.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_discussion_and_approved_handoff_use_the_same_repository_selection(self):
        from intentbond.common import CheckError

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / "config"
            for name in ("repositories", "traceability", "factories"):
                (directory / name).mkdir(parents=True)
            (directory / "defaults.json").write_text(
                json.dumps({**CONFIG, "required_checks": ["unit"]})
            )
            (directory / "repositories/pilot.json").write_text(
                json.dumps({"traceability_scope": "traceability/pilot.json"})
            )
            (directory / "repositories/ordinary.json").write_text(
                json.dumps({"repository": "example/ordinary", "test_command": "make test"})
            )
            (directory / "factories/pair.json").write_text(
                json.dumps({"repositories": ["pilot", "ordinary"]})
            )
            scope = directory / "traceability/pilot.json"
            scope.write_text(
                (Path(__file__).parent / "fixtures/traceability-scope.json").read_text()
            )
            request = Path(__file__).parent / "fixtures/traceability-handoff.md"
            with (
                patch.object(common, "ROOT", root),
                patch.object(self.configure, "api") as api,
                patch.object(self.configure, "records", return_value=[]),
                patch.object(self.configure, "token", return_value="offline-token") as token,
                patch.object(self.configure, "github", return_value={"sha": "approved-base"}),
                patch.object(self.configure, "install", return_value={"id": "build"}) as install,
            ):
                context = self.configure.discussion("pair")
                self.assertEqual(set(context["repositories"]), {"pilot", "ordinary"})
                self.assertNotIn("traceability_scope", context["repositories"]["ordinary"])
                api.assert_not_called()
                install.assert_not_called()
                token.assert_not_called()
                # Discussion supplies context only. An explicit submit carries
                # the reviewed specification through the existing task bundle.
                self.configure.submit(
                    "pair",
                    str(request),
                    task="approved-design",
                    run=True,
                    publish=False,
                    inputs=[
                        {
                            "name": "baseline.db",
                            "reference": "release/baseline.db",
                            "sha256": "a" * 64,
                            "size": 0,
                            "producer": "fixture@commit",
                        }
                    ],
                )
                _, payload, _ = install.call_args.args
                self.assertEqual(payload["request.md"], request.read_text().strip())
                job = json.loads(payload["job.json"])
                self.assertEqual(job["input_artifacts"][0]["name"], "baseline.db")
                self.assertEqual([c["project"] for c in job["configs"]], ["pilot", "ordinary"])
                self.assertEqual(
                    job["configs"][0]["traceability_scope"],
                    context["repositories"]["pilot"]["traceability_scope"],
                )
                self.assertNotIn("traceability_scope", job["configs"][1])
                api.assert_called_once_with("POST", "/api/automation/v1/build/dispatch")
                api.reset_mock()
                install.reset_mock()
                scope.unlink()
                with self.assertRaises(CheckError):
                    self.configure.discussion("pair")
                api.assert_not_called()
                install.assert_not_called()

    def test_profile_is_created_only_when_missing(self):
        for status in (404, 500):
            with self.subTest(status=status):
                response = httpx.Response(
                    status, request=httpx.Request("GET", "http://canvas/profile")
                )
                error = httpx.HTTPStatusError(
                    "Unavailable", request=response.request, response=response
                )
                with (
                    patch.object(
                        self.configure,
                        "api",
                        side_effect=[error, {}, {"profile": {"id": "new"}}, {}, {}],
                    ) as api,
                    patch.object(self.configure, "projects", return_value={}),
                    patch.object(self.configure, "factories", return_value={}),
                    patch.object(self.configure, "records", return_value=[]),
                    patch.object(self.configure, "token", return_value="offline-token"),
                ):
                    if status == 404:
                        self.configure.configure()
                        self.assertEqual(
                            api.call_args_list[1].args,
                            ("POST", "/api/agent-profiles/factory-codex"),
                        )
                        api.assert_any_call("POST", "/api/agent-profiles/new/activate")
                    else:
                        with self.assertRaises(httpx.HTTPStatusError):
                            self.configure.configure()
                        self.assertEqual(api.call_count, 1)


class TaskIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        data = patch.object(run, "DATA", self.root)
        data.start()
        self.addCleanup(data.stop)
        self.seed = self.root / "seed"
        self.store = self.root / "tasks/example/issue-42.git"
        self.store.parent.mkdir(parents=True)
        common.git(["init", "-b", "main", str(self.seed)])
        common.git(["config", "user.name", "Offline Test"], cwd=self.seed)
        common.git(["config", "user.email", "test@localhost"], cwd=self.seed)
        (self.seed / "code.txt").write_text("base")
        common.git(["add", "."], cwd=self.seed)
        common.git(["commit", "-m", "base"], cwd=self.seed)
        self.base = common.git(["rev-parse", "HEAD"], cwd=self.seed).stdout.strip()
        common.git(["checkout", "-b", "factory/issue-42"], cwd=self.seed)
        (self.seed / "code.txt").write_text("retained implementation")
        common.git(["commit", "-am", "retained task"], cwd=self.seed)
        self.tip = common.git(["rev-parse", "HEAD"], cwd=self.seed).stdout.strip()
        common.git(["clone", "--bare", str(self.seed), str(self.store)])
        self.git("remote", "set-url", "origin", "https://github.com/Example/Repo.git")
        self.git("config", "factory.base", self.base)

    def git(self, *args):
        return common.git(["--git-dir", str(self.store), *args]).stdout.strip()

    def test_legacy_task_adopts_verified_identity_and_preserves_base_and_changes(self):
        repository, branch = run.task_repository(CONFIG, "issue-42", "new-approved-base", "")
        self.assertEqual(repository, self.store)
        self.assertEqual(branch, "factory/issue-42")
        self.assertEqual(self.git("config", "factory.repository"), "example/repo")
        self.assertEqual(self.git("config", "factory.baseBranch"), "main")
        self.assertEqual(self.git("config", "factory.base"), self.base)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.tip)
        self.assertEqual(self.git("show", "HEAD:code.txt"), "retained implementation")
        # The migrated store remains usable on later continuations.
        self.assertEqual(run.task_repository(CONFIG, "issue-42", "another-base", "")[0], self.store)

    def test_repointed_registration_is_rejected_before_any_build_or_push(self):
        with (
            patch.object(common, "DATA", self.root),
            patch.object(run, "evidence", return_value=self.root),
            patch.object(run, "job_id", return_value="offline-run"),
            patch.object(run, "execute_build") as execute,
        ):
            with self.assertRaisesRegex(RuntimeError, "origin changed"):
                run.build(
                    {**CONFIG, "repository": "example/new"}, "issue-42", "new repo spec", self.base
                )
        execute.assert_not_called()
        self.assertEqual(
            self.git("remote", "get-url", "origin"), "https://github.com/Example/Repo.git"
        )
        self.assertEqual(self.git("rev-parse", "HEAD"), self.tip)

    def test_override_push_url_and_non_github_origins_are_rejected(self):
        for setting, value in (
            ("remote.origin.pushurl", "https://github.com/example/other.git"),
            ("remote.origin.url", "https://other.example/Example/Repo.git"),
        ):
            with self.subTest(setting=setting):
                self.git("config", setting, value)
                with self.assertRaisesRegex(RuntimeError, "origin"):
                    run.task_repository(CONFIG, "issue-42", self.base, "")
                self.git("config", "--unset", setting)
                self.git("config", "remote.origin.url", "https://github.com/Example/Repo.git")

    def test_changed_base_branch_requires_a_new_task_id(self):
        with self.assertRaisesRegex(RuntimeError, "different base branch"):
            run.task_repository({**CONFIG, "branch": "trunk"}, "issue-42", self.base, "")

    def test_new_naming_defaults_preserve_a_retained_published_branch(self):
        _, branch = run.task_repository(
            {**CONFIG, "branch_prefix": None},
            "issue-42",
            self.base,
            "",
            "fix: different descriptive title",
        )
        self.assertEqual(branch, "factory/issue-42")
        self.assertEqual(self.git("config", "factory.branch"), branch)
        self.assertEqual(self.git("rev-parse", branch), self.tip)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = {
            "day": datetime.datetime.now(datetime.UTC).date().isoformat(),
            "count": 0,
            "done": {},
            "triaged": {},
        }
        self.items = [ISSUE]
        self.event = 100
        self.build = Mock(return_value={"status": "PASSED"})
        self.snapshots = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target, name, replacement in (
            (monitor, "DATA", self.root),
            (monitor, "job_id", lambda: "offline-run"),
            (monitor, "implement_issue", self.build),
            (monitor.issues, "_kv_get", lambda key: copy.deepcopy(self.state)),
            (monitor.issues, "_kv_set", self.save),
            (monitor.issues, "_list_labeled_issues", lambda *args: self.items),
            (monitor.issues, "_latest_trigger_label_event", lambda *args: {"id": self.event}),
            (monitor.reviews, "_list_open_prs", lambda *args: []),
        ):
            self.stack.enter_context(patch.object(target, name, replacement))
        self.triage = self.stack.enter_context(
            patch.object(monitor.issues, "_github_paginate", return_value=[])
        )

    def save(self, key, state):
        self.state = copy.deepcopy(state)
        self.snapshots.append(copy.deepcopy(state))

    def receipts(self):
        return list((self.root / "issue-attempts").glob("**/*.json"))

    def test_failed_approval_stays_deduplicated_after_history_pruning(self):
        self.state["done"] = {
            "issue:42:100": "failed:old-run",
            **{f"pr:{i}:sha": "reviewed" for i in range(150)},
        }
        self.items = [ISSUE, {**ISSUE, "number": 43}]
        monitor.poll(CONFIG, "offline-token")
        self.assertEqual(self.build.call_args.args[1]["number"], 43)
        self.assertNotIn("issue:42:100", self.state["done"])
        self.assertEqual(len(self.receipts()), 2)
        self.build.reset_mock()
        self.items = [ISSUE]
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
        self.assertLess(len(json.dumps({"factory": self.state}).encode()), 64 * 1024)

    def test_attempt_is_persisted_before_work_and_relabeling_permits_retry(self):
        def fail(*args):
            records = [json.loads(path.read_text()) for path in self.receipts()]
            self.assertEqual(records, [{"event": 100, "status": "started:offline-run"}])
            raise RuntimeError("tests failed")

        self.build.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "tests failed"):
            monitor.poll(CONFIG, "offline-token")
        self.build.reset_mock(side_effect=True)
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
        self.event += 1
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_called_once()
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(json.loads(self.receipts()[0].read_text())["event"], 101)

    def test_busy_repository_is_deferred_without_consuming_approval(self):
        self.build.side_effect = BlockingIOError("busy")
        monitor.poll(CONFIG, "offline-token")
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.state["count"], 0)
        self.build.reset_mock(side_effect=True)
        monitor.poll(CONFIG, "offline-token")
        self.build.assert_called_once()

    def test_failed_candidate_does_not_starve_issue_proposal_discovery(self):
        self.state["done"]["issue:42:100"] = "failed:previous"
        proposal = {**ISSUE, "number": 7, "labels": [], "updated_at": "current"}
        self.triage.return_value = [proposal]
        artifact = self.root / "artifacts"
        artifact.mkdir()
        workspaces = []

        @contextmanager
        def worker(root, config):
            self.assertEqual(root.parent, self.root)
            self.assertTrue(root.name.startswith("job-"))
            workspace = Mock(working_dir=str(root / "source"))
            workspaces.append(workspace)
            yield workspace

        def converse(workspace, prompt, **kwargs):
            self.assertIs(workspace, workspaces[0])
            self.assertTrue(Path(workspace.working_dir).is_relative_to(self.root))
            self.assertIn('"number": 7', prompt)
            return "Reviewable proposal"

        with (
            patch.object(monitor.shutil, "copytree") as copytree,
            patch.object(monitor, "worker", worker),
            patch.object(monitor, "converse", side_effect=converse),
            patch.object(monitor, "evidence", return_value=artifact),
        ):
            monitor.poll(CONFIG, "offline-token")
        self.build.assert_not_called()
        self.triage.assert_called_once_with(
            "offline-token", "/repos/example/repo/issues", {"state": "open", "assignee": "none"}
        )
        self.assertEqual(copytree.call_args.args[0], Path("/projects/repos/example"))
        self.assertEqual(copytree.call_args.args[1], Path(workspaces[0].working_dir))
        self.assertEqual(
            copytree.call_args.kwargs["ignore"]("unused", [".git", "source.py"]), {".git"}
        )
        self.assertEqual((artifact / "proposals.md").read_text(), "Reviewable proposal")
        self.assertEqual(self.state["triaged"], {"7": "current"})
        self.assertEqual(list(self.root.glob("job-*")), [])

    def test_watermarks_are_scoped_to_repository_identity(self):
        monitor.poll(CONFIG, "offline-token")
        self.build.reset_mock()
        monitor.poll({**CONFIG, "repository": "example/other"}, "offline-token")
        self.build.assert_called_once()
        self.assertEqual(len(self.receipts()), 2)


if __name__ == "__main__":
    unittest.main()
