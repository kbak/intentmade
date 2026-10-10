"""Real current-fact policies and their existing controller boundaries."""

import copy
import itertools
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import approval
import authorization
import common
import feedback
import monitor
import policy
import replies
import reporting
import test_approval


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(authorization, "ARTIFACTS", self.root))
        self.config = {"project": "example", "repository": "example/repo"}

    def decide(self, action, facts, expected):
        before = set(self.root.rglob("*.json"))
        result = authorization.permit(self.config, action, {"fixture": "current"}, facts)
        (receipt_path,) = set(self.root.rglob("*.json")) - before
        receipt = json.loads(receipt_path.read_text())
        with self.subTest(action=action, facts=facts):
            self.assertEqual(result, expected, receipt)
            self.assertEqual(receipt["decision"]["allowed"], expected)
            self.assertEqual(receipt["status"], "ALLOW" if expected else "DENY")
            self.assertEqual(receipt["decision"]["action"], action)
            self.assertEqual(receipt["decision"]["policy_sha256"], authorization.policy_digest())
            self.assertTrue(all(p.suffix == ".json" for p in receipt_path.parent.iterdir()))

    def test_issue_and_approval_policy_equivalence(self):
        # [utest~im-authorization-issue-admission~1->req~im-authorization~1,req~im-issue-ownership~1,req~im-approval-snapshot~1,impl~im-authorization-issue-eligible~1,impl~im-authorization-issue-approval~1]
        for state, pull, assigned, intake, label in itertools.product(
            ("open", "closed"),
            (False, True),
            (0, 1),
            ("manual", "automatic"),
            ("absent", "required-missing", "required-present"),
        ):
            facts = {
                "state": state,
                "is_pull_request": pull,
                "assignees": assigned,
                "intake": intake,
                "label_required": label != "absent",
                "label_present": label == "required-present",
            }
            expected = (
                state == "open"
                and not pull
                and assigned == 0
                and (intake == "manual" or label in {"absent", "required-present"})
            )
            self.decide("issue:eligible", facts, expected)
        for changes, edited in itertools.product((False, True), (99, 100, 101)):
            self.decide(
                "issue:approval",
                {"has_changes": changes, "latest_change_at": edited, "approved_at": 100},
                not changes or edited < 100,
            )

    def test_pr_and_ci_policy_equivalence(self):
        # [utest~im-authorization-ci-admission~1->req~im-authorization~1,req~im-pr-publication~1,impl~im-authorization-pr-inspect~1,impl~im-authorization-ci-accept~1]
        for state, draft in itertools.product(("open", "closed"), (False, True)):
            self.decide(
                "pr:inspect", {"state": state, "draft": draft}, state == "open" and not draft
            )
        for result, head, accepted, covered in itertools.product(
            (None, "success", "pending", "failure", "neutral", "skipped"),
            (None, "success", "failure"),
            (False, True),
            (False, True),
        ):
            allowed = ["success", "neutral", "skipped"] if accepted else ["success"]
            expected = result in allowed and (head is None or head in allowed) and covered
            self.decide(
                "ci:accept",
                {
                    "results": [] if result is None else [result],
                    "head_results": [] if head is None else [head],
                    "accepted_results": allowed,
                    "required_patterns": ["test*"],
                    "matched_patterns": ["test*"] if covered else [],
                },
                expected,
            )

    def test_feedback_and_continuation_policy_equivalence(self):
        # [utest~im-authorization-reply-admission~1->req~im-authorization~1,req~im-feedback-authority~1,req~im-explicit-resume~1,impl~im-authorization-feedback-accept~1,impl~im-authorization-resume-task~1,impl~im-authorization-resume-event~1,impl~im-authorization-resume-answer~1,impl~im-authorization-resume-dispatch~1]
        for permission, bot, generated, comment, mention in itertools.product(
            ("", "read", "write", "maintain", "admin"),
            (False, True),
            (False, True),
            (False, True),
            (False, True),
        ):
            facts = {
                "login": "reviewer",
                "permission": permission,
                "bot": bot,
                "generated": generated,
                "allowed_bots": ["reviewer"],
                "kind": "comment" if comment else "review",
                "mentioned": mention,
            }
            self.decide(
                "feedback:accept",
                facts,
                not generated
                and (not comment or mention)
                and (bot or permission in {"write", "maintain", "admin"}),
            )
        self.decide(
            "feedback:accept", {**facts, "generated": False, "bot": True, "allowed_bots": []}, False
        )
        self.decide("feedback:accept", {**facts, "login": "", "generated": False}, False)
        for status in ("NEEDS_INPUT", "FAILED", "PUBLICATION_FAILED", "READY", "RUNNING"):
            self.decide(
                "resume:task",
                {"status": status},
                status in {"NEEDS_INPUT", "FAILED", "PUBLICATION_FAILED"},
            )
        for source, kind, role, consumed in itertools.product(
            ("user", "agent"),
            ("MessageEvent", "ActionEvent"),
            ("user", "assistant"),
            (False, True),
        ):
            self.decide(
                "resume:event",
                {
                    "source": source,
                    "kind": kind,
                    "role": role,
                    "event_id": "answer",
                    "answer_id": "answer" if consumed else "old",
                },
                source == "user" and kind == "MessageEvent" and role == "user" and not consumed,
            )
        for event, explicit, answer in itertools.product(
            (99, 100, 101), (False, True), (False, True)
        ):
            self.decide(
                "resume:answer",
                {
                    "event_at": event,
                    "question_at": 100,
                    "explicit": explicit,
                    "answer_present": answer,
                },
                event > 100 and explicit and answer,
            )
        for enabled in (False, True):
            self.decide("resume:dispatch", {"scheduler_enabled": enabled}, enabled)

    def test_invalid_action_facts_and_unavailable_engine_block_each_gate(self):
        # [utest~im-authorization-admission-errors~1->req~im-authorization~1]
        valid = {
            "issue:eligible": {
                "state": "open",
                "is_pull_request": False,
                "assignees": 0,
                "intake": "manual",
                "label_required": False,
                "label_present": False,
            },
            "issue:approval": {"has_changes": False, "latest_change_at": 0, "approved_at": 100},
            "pr:inspect": {"state": "open", "draft": False},
            "ci:accept": {
                "results": ["success"],
                "head_results": [],
                "accepted_results": ["success"],
                "required_patterns": [],
                "matched_patterns": [],
            },
            "feedback:accept": {
                "login": "author",
                "generated": False,
                "bot": False,
                "permission": "write",
                "allowed_bots": [],
                "kind": "review",
                "mentioned": False,
            },
            "resume:task": {"status": "NEEDS_INPUT"},
            "resume:event": {
                "source": "user",
                "kind": "MessageEvent",
                "role": "user",
                "event_id": "new",
                "answer_id": "old",
            },
            "resume:answer": {
                "event_at": 101,
                "question_at": 100,
                "explicit": True,
                "answer_present": True,
            },
            "resume:dispatch": {"scheduler_enabled": True},
        }
        for action, facts in valid.items():
            for broken in ({}, {**facts, "extra": True}, {name: None for name in facts}):
                with (
                    self.subTest(action=action, facts=broken),
                    self.assertRaisesRegex(RuntimeError, "blocked"),
                ):
                    authorization.permit(self.config, action, {}, broken)
            with (
                patch.object(authorization, "BRIDGE", "/missing/bridge"),
                self.assertRaisesRegex(RuntimeError, "blocked"),
            ):
                authorization.permit(self.config, action, {}, facts)
        for facts in ([True], [1], [None]):
            broken = {**valid["ci:accept"], "results": facts}
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                authorization.permit(self.config, "ci:accept", {}, broken)
        with self.assertRaisesRegex(RuntimeError, "blocked"):
            authorization.permit(self.config, "unknown:action", {}, {})
        self.assertFalse(list(self.root.rglob("*.redb")))
        self.assertTrue(
            all(json.loads(p.read_text())["status"] == "ERROR" for p in self.root.rglob("*.json"))
        )

    def test_existing_issue_approval_and_ci_boundaries_use_engine(self):
        # [utest~im-authorization-admission-boundaries~1->req~im-authorization~1]
        # [utest~im-authorization-admission-ownership~1->req~im-issue-ownership~1]
        # [utest~im-authorization-admission-approval~1->req~im-approval-snapshot~1]
        # [utest~im-authorization-admission-ci~1->req~im-pr-publication~1]
        config = {**test_approval.CONFIG, **self.config}
        with (
            patch.object(
                monitor.issues,
                "_get_issue",
                return_value={**test_approval.ISSUE, "state": "closed"},
            ),
            patch.object(monitor, "github") as github,
            patch.object(monitor, "build") as build,
        ):
            monitor.implement_issue(config, test_approval.ISSUE, "dummy-token")
        github.assert_not_called()
        build.assert_not_called()
        with patch.object(approval.issues, "_github_paginate", return_value=[test_approval.EVENT]):
            for edited, expected in (("09.999999", True), ("10", False), ("10.000001", False)):
                content = {
                    **test_approval.CONTENT,
                    "lastEditedAt": "2026-09-08T01:00:" + edited + "Z",
                }
                with patch.object(
                    approval, "github", return_value={"data": {"repository": {"issue": content}}}
                ):
                    if expected:
                        self.assertEqual(
                            approval.approved_issue(config, 42, "dummy-token")[0]["title"],
                            content["title"],
                        )
                    else:
                        with self.assertRaisesRegex(RuntimeError, "reapply"):
                            approval.approved_issue(config, 42, "dummy-token")
        pr = {
            "number": 42,
            "state": "open",
            "draft": False,
            "head": {"sha": "head"},
            "merge_commit_sha": "merge",
        }
        config.update(required_checks=["test[0-9]*", "lint?"], accepted_check_results=["success"])
        with patch.object(
            policy,
            "checks_for",
            side_effect=[{"test1": "failure"}, {"test1": "success", "lint1": "success"}],
        ):
            self.assertFalse(policy.pr_eligible("dummy-token", pr, config))
        with patch.object(
            policy, "checks_for", side_effect=[{}, {"test1": "success", "lint1": "success"}]
        ):
            self.assertTrue(policy.pr_eligible("dummy-token", pr, config))
        with patch.object(policy, "checks_for") as checks:
            self.assertFalse(policy.pr_eligible("dummy-token", {**pr, "draft": True}, config))
            checks.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                policy.pr_eligible(
                    "dummy-token", {k: v for k, v in pr.items() if k != "draft"}, config
                )
            checks.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "blocked"):
            policy.issue_eligible({"state": "open"}, config)

    def test_existing_feedback_resume_and_dispatch_boundaries_use_engine(self):
        # [utest~im-authorization-continuation-boundaries~1->req~im-authorization~1]
        # [utest~im-authorization-continuation-feedback~1->req~im-feedback-authority~1]
        # [utest~im-authorization-continuation-resume~1->req~im-explicit-resume~1]
        item = {
            "id": 1,
            "state": "CHANGES_REQUESTED",
            "commit_id": "head",
            "user": {"login": "reviewer", "type": "User"},
            "body": "Handle an empty response",
            "submitted_at": "today",
        }
        pr = {"number": 42, "head": {"sha": "head"}}
        config = {**self.config, "pr_feedback": True}
        with (
            patch.object(
                feedback.issues,
                "_github_paginate",
                side_effect=lambda token, path: [item] if path.endswith("/reviews") else [],
            ),
            patch.object(feedback, "unresolved_roots", return_value=set()),
            patch.object(feedback, "github", return_value={"permission": "read"}) as access,
        ):
            self.assertEqual(feedback.collect(config, pr, "dummy-token", {}), [])
            access.return_value = {"permission": "write"}
            self.assertEqual(
                [entry["id"] for entry in feedback.collect(config, pr, "dummy-token", {})],
                ["review:1"],
            )
            with (
                patch.object(authorization, "BRIDGE", "/missing/bridge"),
                self.assertRaisesRegex(RuntimeError, "blocked"),
            ):
                feedback.collect(config, pr, "dummy-token", {})
        record = {
            "status": "NEEDS_INPUT",
            "conversation_id": "conversation",
            "updated_at": "2026-09-08T12:00:00+00:00",
            "snapshot": {"content": "hash"},
            "answer_id": "old",
        }
        event = {
            "id": "new",
            "kind": "MessageEvent",
            "source": "user",
            "timestamp": "2026-09-08T12:00:00.000001Z",
            "llm_message": {"role": "user", "content": [{"text": "resume: yes"}]},
        }
        before = copy.deepcopy(record)
        with patch.object(reporting, "read_report", return_value=record):
            for change in (
                {"id": "old"},
                {"timestamp": "2026-09-08T12:00:00Z"},
                {"source": "agent"},
                {"source": "agent", "kind": "ActionEvent", "llm_message": None},
                {"llm_message": {"role": "user", "content": [{"text": "resume:"}]}},
            ):
                with patch.object(reporting, "api", return_value={"items": [{**event, **change}]}):
                    self.assertIsNone(reporting.resume_reply(self.config, "task"))
            with patch.object(reporting, "api", return_value={"items": [event]}):
                self.assertEqual(reporting.resume_reply(self.config, "task")["answer"], "yes")
            with (
                patch.object(reporting, "api", return_value={"items": [{**event, "id": None}]}),
                self.assertRaisesRegex(RuntimeError, "blocked"),
            ):
                reporting.resume_reply(self.config, "task")
        self.assertEqual(record, before)
        config = {**self.config, "enabled": True}
        with (
            patch.object(common, "DATA", self.root),
            patch.object(replies, "DATA", self.root),
            patch.object(reporting, "DATA", self.root),
            patch.object(replies, "projects", return_value={"example": config}),
            patch.object(
                reporting,
                "resume_reply",
                return_value={"id": "new", "answer": "yes", "snapshot": {}},
            ),
            patch.object(reporting, "post"),
            patch.object(replies, "api", return_value={"enabled": False}) as api,
        ):
            reporting.write_report(config, "task", record)
            reporting.write_report(
                config, "reply-trigger", {"automation_id": "replies", "scheduler_id": "schedule"}
            )
            self.assertFalse(replies.queue_reply("conversation"))
            api.return_value = {"enabled": True}
            with (
                patch.object(authorization, "BRIDGE", "/missing/bridge"),
                self.assertRaisesRegex(RuntimeError, "blocked"),
            ):
                replies.queue_reply("conversation")
            self.assertEqual([call.args[0] for call in api.call_args_list], ["GET", "GET"])
            api.side_effect = lambda method, path, **kwargs: (
                {"enabled": True} if method == "GET" else {"id": "queued"}
            )
            self.assertTrue(replies.queue_reply("conversation"))
            self.assertTrue(replies.queue_reply("conversation"))
            self.assertEqual(sum(call.args[0] == "POST" for call in api.call_args_list), 1)
            self.assertEqual(reporting.read_report(config, "task"), record)
