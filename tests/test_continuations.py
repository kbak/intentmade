"""Same-thread approvals extend retained PR work without granting report writes."""

import copy
import itertools
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import authorization
import common
import continuations
import followup
import httpx
import monitor
import replies
import reporting
import run
import sdlc
from pipeline_fixture import PipelineScenario, seed_repository

CONFIG = {"project": "example", "repository": "example/repo", "branch": "main", "enabled": True}
PR = {
    "number": 25,
    "state": "open",
    "head": {"sha": "a" * 40, "ref": "factory/task", "repo": {"full_name": "example/repo"}},
    "base": {"sha": "b" * 40, "ref": "main"},
}


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for target in (common, reporting, followup, replies):
            self.stack.enter_context(patch.object(target, "DATA", self.root))
        self.stack.enter_context(patch.object(authorization, "ARTIFACTS", self.root / "decisions"))
        self.stack.enter_context(patch.object(followup, "job_id", return_value="fixture-run"))
        self.post = self.stack.enter_context(patch.object(reporting, "post"))
        self.native = self.stack.enter_context(patch.object(reporting, "api", return_value={}))
        self.pr = copy.deepcopy(PR)
        self.github = self.stack.enter_context(
            patch.object(
                continuations, "github", side_effect=lambda *a, **kw: copy.deepcopy(self.pr)
            )
        )
        self.events = []
        self.api = self.stack.enter_context(
            patch.object(continuations, "api", side_effect=lambda *a, **kw: {"items": self.events})
        )
        self.report = {
            "conversation_id": "original-thread",
            "status": "PASSED",
            "updated_at": "2026-10-16T10:00:00+00:00",
            "snapshot": None,
            "result": {
                "repositories": {
                    "example": {"pull_request": "https://github.com/example/repo/pull/25"}
                }
            },
        }
        self.task = "feature-original"
        reporting.write_report(CONFIG, self.task, self.report)
        self.tracked = {
            "number": 25,
            "task": self.task,
            "head": PR["head"]["sha"],
            "branch": PR["head"]["ref"],
            "base_branch": "main",
            "request": "Original approved work",
            "status": "WATCHING",
        }
        followup.save(CONFIG, self.tracked)

    def event(self, text, *, role="user", identity="approval", when="2026-10-16T11:00:00Z"):
        if role == "assistant":
            return {
                "id": identity,
                "timestamp": when,
                "source": "agent",
                "kind": "ActionEvent",
                "action": {"kind": "FinishAction", "message": text},
                "thought": "private reasoning",
            }
        return {
            "id": identity,
            "timestamp": when,
            "source": "user",
            "kind": "MessageEvent",
            "llm_message": {"role": role, "content": [{"type": "text", "text": text}]},
        }

    def capture(self):
        return continuations.capture(CONFIG, self.task, self.report, "fixture-token")

    def test_deleted_reports_do_not_stop_scans_or_authorize_partial_history(self):
        # [utest~im-follow-up-deleted-report~1->req~im-thread-follow-up~1]
        response = httpx.Response(404, request=httpx.Request("GET", "http://canvas/events"))
        error = httpx.HTTPStatusError("Deleted", request=response.request, response=response)
        original = copy.deepcopy(self.report)
        for pages in (
            [error],
            [
                {
                    "items": [self.event("implement: unverified partial history")],
                    "next_page_id": "next",
                },
                error,
            ],
        ):
            with self.subTest(pages=len(pages)):
                self.api.side_effect = pages
                self.assertIsNone(self.capture())
                self.api.side_effect = error
                self.assertEqual(continuations.process(CONFIG, "fixture-token", 1), (0, 0))
                self.assertEqual(reporting.read_report(CONFIG, self.task), original)
                self.assertEqual(followup.read(CONFIG, 25), self.tracked)
                self.assertFalse(continuations.directory(CONFIG).exists())
        self.github.assert_not_called()
        self.post.assert_not_called()

    def test_auth_and_service_errors_still_block_follow_up_scans(self):
        for status in (401, 403, 429, 500):
            response = httpx.Response(status, request=httpx.Request("GET", "http://canvas/events"))
            error = httpx.HTTPStatusError(
                "Unavailable", request=response.request, response=response
            )
            with self.subTest(status=status):
                self.api.side_effect = error
                with self.assertRaises(httpx.HTTPStatusError):
                    continuations.process(CONFIG, "fixture-token", 1)
        self.github.assert_not_called()

    def test_only_fresh_explicit_user_scope_can_capture_context(self):
        # [utest~im-follow-up-capture~1->req~im-thread-follow-up~1]
        for event in (
            self.event("What do you think?"),
            self.event("go ahead"),
            self.event("implement: yes", role="assistant"),
            self.event("implement:"),
            self.event("Factory update\nimplement: yes"),
            self.event("implement: stale", when="2026-10-16T09:59:59Z"),
        ):
            self.events = [event]
            self.assertIsNone(self.capture())
        self.github.assert_not_called()
        self.events = [
            self.event(
                "Use sibling libraries",
                role="assistant",
                identity="proposal",
                when="2026-10-16T10:30:00Z",
            ),
            {
                "id": "tool",
                "source": "agent",
                "kind": "ActionEvent",
                "action": {"kind": "ExecuteBashAction", "command": "private command"},
            },
            self.event("implement: Apply that refactor; preserve proofs"),
        ]
        record = self.capture()
        self.assertEqual(record["package"]["scope"], "Apply that refactor; preserve proofs")
        self.assertEqual(
            [m["id"] for m in record["package"]["discussion"]], ["proposal", "approval"]
        )
        self.assertNotIn("private", json.dumps(record))
        self.assertEqual(record["package"]["head"], PR["head"]["sha"])
        self.assertEqual(record["package"]["conversation_id"], "original-thread")
        self.assertEqual(record["sha256"], continuations.digest(record["package"]))
        self.assertEqual(self.capture(), record)
        self.github.assert_called_once()

    def test_real_pr_identity_policy_and_untracked_reports_reject_scope(self):
        # [utest~im-follow-up-authority~1->req~im-thread-follow-up~1,impl~im-authorization-continuation-accept~1]
        for state, head, branch, base, repo in itertools.product(
            ("open", "closed"), (False, True), (False, True), (False, True), (False, True)
        ):
            facts = {
                "state": state,
                "head_matches": head,
                "branch_matches": branch,
                "base_matches": base,
                "repository_matches": repo,
            }
            self.assertEqual(
                authorization.permit(CONFIG, "continuation:accept", {}, facts),
                state == "open" and head and branch and base and repo,
            )
        self.events = [self.event("implement: refactor")]
        for location, key, value in (
            ("head", "sha", "changed"),
            ("head", "ref", "other"),
            ("base", "ref", "release"),
        ):
            self.pr = copy.deepcopy(PR)
            self.pr[location][key] = value
            self.assertIsNone(self.capture())
        self.pr = copy.deepcopy(PR)
        self.pr["head"]["repo"]["full_name"] = "fork/repo"
        self.assertIsNone(self.capture())
        followup.directory(CONFIG).joinpath("25.json").unlink()
        self.api.reset_mock()
        self.assertIsNone(self.capture())
        self.api.assert_not_called()

    def test_dispatch_is_once_and_execution_keeps_the_original_thread(self):
        # [utest~im-follow-up-lifecycle~1->req~im-thread-follow-up~1]
        self.events = [self.event("implement: Refactor the library roots")]
        captured = self.capture()
        with patch.object(followup, "maintain", return_value=True) as maintain:
            self.assertEqual(continuations.process(CONFIG, "fixture-token", 1), (1, 0))
            self.assertEqual(continuations.process(CONFIG, "fixture-token", 1), (0, 0))
        maintain.assert_called_once()
        action = maintain.call_args.args[2]
        self.assertEqual(action["continuation"]["package"], captured["package"])
        self.assertEqual(action["number"], 25)
        self.assertEqual(
            reporting.read_report(CONFIG, self.task)["conversation_id"], "original-thread"
        )
        self.assertFalse(reporting.report_path(CONFIG, "pr-25").exists())
        receipt = json.loads(next(continuations.directory(CONFIG).glob("*.json")).read_text())
        self.assertEqual(receipt["status"], "COMPLETED")

    def test_failed_scope_needs_a_new_resume_and_tampering_blocks_execution(self):
        # [utest~im-follow-up-recovery~1->req~im-thread-follow-up~1]
        self.events = [self.event("implement: Preserve proofs while refactoring")]
        self.capture()
        with patch.object(followup, "maintain", side_effect=RuntimeError("review blocked")):
            with self.assertRaisesRegex(RuntimeError, "review blocked"):
                continuations.process(CONFIG, "fixture-token", 1)
        with (
            patch.object(reporting, "resume_reply", return_value=None),
            patch.object(followup, "maintain") as maintain,
        ):
            self.assertEqual(continuations.process(CONFIG, "fixture-token", 1), (0, 0))
            maintain.assert_not_called()
        with (
            patch.object(
                reporting,
                "resume_reply",
                return_value={"id": "new-answer", "answer": "retry", "snapshot": None},
            ),
            patch.object(followup, "maintain", return_value=True) as maintain,
        ):
            self.assertEqual(continuations.process(CONFIG, "fixture-token", 1), (1, 0))
            self.assertEqual(maintain.call_args.args[2]["reply"]["id"], "new-answer")
        path = next(continuations.directory(CONFIG).glob("*.json"))
        record = json.loads(path.read_text())
        record["package"]["scope"] = "unapproved replacement"
        path.write_text(json.dumps(record))
        with (
            patch.object(followup, "maintain") as maintain,
            self.assertRaisesRegex(ValueError, "scope changed"),
        ):
            continuations.process(CONFIG, "fixture-token", 1)
        maintain.assert_not_called()

    def test_context_pagination_and_bounds_are_explicit(self):
        earlier = self.event(
            "x" * continuations.MAX_CONTEXT_BYTES,
            role="assistant",
            identity="old",
            when="2026-10-16T10:01:00Z",
        )
        approval = self.event("implement: Bounded fix")
        self.api.side_effect = [{"items": [earlier], "next_page_id": "next"}, {"items": [approval]}]
        record = self.capture()
        self.assertEqual(record["package"]["omitted_messages"], 1)
        self.assertEqual(
            record["package"]["discussion"],
            [
                {
                    "id": "approval",
                    "timestamp": approval["timestamp"],
                    "role": "user",
                    "text": "implement: Bounded fix",
                }
            ],
        )
        self.assertEqual(self.api.call_args.kwargs["params"]["page_id"], "next")
        self.api.side_effect = None
        self.api.return_value = {
            "items": [
                self.event(
                    "implement: " + "x" * (continuations.MAX_SCOPE_BYTES + 1), identity="large"
                )
            ]
        }
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            self.capture()

    def test_changed_revision_stops_before_any_worker_or_publication(self):
        self.events = [self.event("implement: Refactor the roots")]
        self.capture()
        self.pr["head"]["sha"] = "c" * 40
        artifact = self.root / "artifact"
        artifact.mkdir()
        with (
            patch.object(followup, "evidence", return_value=artifact),
            patch.object(followup, "github", return_value=self.pr),
            patch.object(run, "execute_build") as build,
            patch.object(run.issues, "_push_branch") as push,
        ):
            self.assertEqual(continuations.process(CONFIG, "fixture-token", 1), (1, 1))
            self.assertIn("new `implement:`", self.post.call_args.args[1])
        build.assert_not_called()
        push.assert_not_called()
        self.assertEqual(
            reporting.read_report(CONFIG, self.task)["conversation_id"], "original-thread"
        )

    def test_scope_uses_the_native_dispatch_bridge_and_respects_pause(self):
        self.events = [self.event("implement: Refactor the roots")]
        reporting.write_report(
            CONFIG, "reply-trigger", {"automation_id": "resume", "scheduler_id": "schedule"}
        )
        with (
            patch.object(replies, "projects", return_value={"example": CONFIG}),
            patch.object(continuations, "token", return_value="fixture-token"),
            patch.object(replies, "api", return_value={"enabled": False}) as native,
        ):
            self.assertFalse(replies.queue_reply("original-thread"))
            self.assertEqual([call.args[0] for call in native.call_args_list], ["GET"])
            native.side_effect = lambda method, *a, **kw: (
                {"enabled": True} if method == "GET" else {"id": "queued"}
            )
            self.assertTrue(replies.queue_reply("original-thread"))
            self.assertTrue(replies.queue_reply("original-thread"))
            self.assertEqual(
                len([call for call in native.call_args_list if call.args[0] == "POST"]), 1
            )

    def test_scheduler_records_the_attempt_before_work_and_reports_completion(self):
        def execute(config, credential, limit, started):
            started()
            self.assertEqual(persist.call_args.args[1]["count"], 1)
            return 1, 0

        with (
            patch.object(monitor.issues, "_kv_get", return_value={"done": {}, "triaged": {}}),
            patch.object(monitor.issues, "_kv_set") as persist,
            patch.object(followup, "adopt_reports"),
            patch.object(continuations, "process", side_effect=execute),
            patch.object(reporting, "ACTIVE", {"status": "SKIPPED", "summary": ""}),
        ):
            monitor.poll(
                {**CONFIG, "issue_intake": "manual", "max_tasks_per_poll": 1}, "fixture-token"
            )
            self.assertEqual(reporting.ACTIVE["status"], "COMPLETED")
            self.assertIn("1 follow-ups completed", reporting.ACTIVE["summary"])

    def test_real_pipeline_preserves_accepted_work_and_updates_the_existing_pr(self):
        # [utest~im-follow-up-pipeline~1->req~im-thread-follow-up~1]
        seed, repository, artifact = (
            self.root / "seed",
            self.root / "task.git",
            self.root / "artifact",
        )
        artifact.mkdir()
        state = seed_repository(
            seed, repository, files={"feature.txt": "existing proof"}, branch=PR["head"]["ref"]
        )
        head = state["base"]
        common.git(["--git-dir", str(repository), "config", "factory.base", head])
        self.pr["head"]["sha"] = self.pr["base"]["sha"] = head
        followup.save(CONFIG, {**self.tracked, "head": head})
        package = sdlc.propose(
            self.task,
            {"example": head},
            {
                "intent.md": "Original outcome",
                "spec.md": "Original approved work",
                "plan.md": "Original plan",
            },
        )
        sdlc.bind(repository, package)
        original_package = (repository / "factory-work-package.json").read_bytes()
        self.events = [self.event("implement: Add sibling library roots and preserve proofs")]
        self.capture()
        config = {
            **CONFIG,
            "repair_attempts": 0,
            "test_command": "test -f feature.txt && test -f roots.txt",
            "publish_draft": True,
        }
        scenario = PipelineScenario(self.root)

        def implement(workspace, prompt, *args, **kwargs):
            source = Path(workspace.working_dir)
            self.assertEqual((source / "feature.txt").read_text(), "existing proof")
            self.assertIn("Add sibling library roots and preserve proofs", prompt)
            (source / "roots.txt").write_text("DPGraph, FormalCD, Clafer")
            return run.ImplementationResult(
                status="IMPLEMENTED", summary="Refactor roots", title="refactor: sibling roots"
            )

        def review(workspace, prompt, **kwargs):
            self.assertEqual(
                (Path(workspace.working_dir) / "feature.txt").read_text(), "existing proof"
            )
            self.assertIn("Explicitly approved follow-up scope", prompt)
            return run.ReviewResult(verdict="PASS", summary="Independent review passed")

        def push(repo, branch, credential):
            self.pr["head"]["sha"] = common.git(
                ["--git-dir", str(repo), "rev-parse", branch]
            ).stdout.strip()

        def github(credential, method, path, **kwargs):
            return {**copy.deepcopy(self.pr), "html_url": "https://github.com/example/repo/pull/25"}

        with (
            patch.object(followup, "evidence", return_value=artifact),
            patch.object(followup, "github", side_effect=github),
            patch.object(followup, "failure_context", return_value=""),
            patch.object(run, "github", side_effect=github),
            patch.object(run, "task_repository", return_value=(repository, PR["head"]["ref"])),
            patch.object(run.issues, "_push_branch", side_effect=push) as pushed,
            patch.object(run.issues, "_open_pull_request") as opened,
            scenario.activate(implement=implement, review=review),
        ):
            self.assertEqual(continuations.process(config, "fixture-token", 1), (1, 0))
        pushed.assert_called_once()
        opened.assert_not_called()
        self.assertEqual((repository / "factory-work-package.json").read_bytes(), original_package)
        self.assertEqual(followup.read(CONFIG, 25)["request"], "Original approved work")
        self.assertEqual(len(followup.read(CONFIG, 25)["scope_extensions"]), 1)
        self.assertEqual(
            followup.report_target(CONFIG, followup.read(CONFIG, 25)), (self.task, None)
        )
        self.assertEqual(
            sdlc.source_file(repository, self.pr["head"]["sha"], package["directory"] + "/spec.md"),
            b"Original approved work",
        )
        self.assertEqual(reporting.read_report(CONFIG, self.task)["status"], "WATCHING")
        self.assertFalse(reporting.report_path(CONFIG, "pr-25").exists())
        self.assertTrue((artifact / "follow-up.json").exists())
