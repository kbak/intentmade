"""Exercise the persisted-message handoff to native automation dispatch."""

import asyncio
import copy
import importlib.util
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import common
import replies
import reporting

CONFIG = {"project": "example", "repository": "example/repo", "enabled": True}


class ReplyDispatchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.record = {
            "conversation_id": "conversation",
            "status": "NEEDS_INPUT",
            "updated_at": "question-time",
        }
        for target in (common, replies, reporting):
            self.stack.enter_context(patch.object(target, "DATA", self.root))
        self.stack.enter_context(
            patch.object(replies, "projects", return_value={"example": CONFIG})
        )
        self.api = self.stack.enter_context(patch.object(replies, "api", side_effect=self.native))
        self.post = self.stack.enter_context(patch.object(reporting, "post"))
        self.reply = self.stack.enter_context(
            patch.object(
                reporting,
                "resume_reply",
                return_value={"id": "answer", "answer": "Use explicit retry", "snapshot": {}},
            )
        )
        reporting.write_report(CONFIG, "issue-1406", self.record)
        reporting.write_report(
            CONFIG, "reply-trigger", {"automation_id": "replies", "scheduler_id": "schedule"}
        )

    def native(self, method, path, **kwargs):
        return {"enabled": True} if method == "GET" else {"id": "queued-run"}

    def test_local_fixture_report_survives_reload_without_colliding_with_remote(self):
        fixture = {**CONFIG, "repository": None}
        reporting.write_report(fixture, "issue-1406", {"status": "READY"})
        self.assertEqual(reporting.read_report(fixture, "issue-1406"), {"status": "READY"})
        self.assertEqual(reporting.read_report(CONFIG, "issue-1406"), self.record)

    def test_saved_answer_dispatches_immediately_and_duplicate_notification_is_idempotent(self):
        before = copy.deepcopy(reporting.read_report(CONFIG, "issue-1406"))
        # [utest~im-replies-ReplyDispatchTests-saved_answer_dispatches_immediately_and_duplicate_notification_is_idempotent~1->req~im-explicit-resume~1]
        self.assertTrue(replies.queue_reply("conversation"))
        self.assertTrue(replies.queue_reply("conversation"))
        dispatches = [call for call in self.api.call_args_list if call.args[0] == "POST"]
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(dispatches[0].args[1], "/api/automation/v1/replies/dispatch")
        self.assertEqual(reporting.read_report(CONFIG, "issue-1406"), before)
        self.post.assert_called_once()

    def test_unknown_conversation_or_missing_fresh_answer_never_dispatches(self):
        self.assertFalse(replies.queue_reply("unrelated-conversation"))
        self.reply.return_value = None
        self.assertFalse(replies.queue_reply("conversation"))
        self.api.assert_not_called()

    def test_paused_schedule_disables_immediate_dispatch(self):
        self.api.side_effect = None
        self.api.return_value = {"enabled": False}
        # [utest~im-replies-ReplyDispatchTests-paused_schedule_disables_immediate_dispatch~1->req~im-explicit-resume~1]
        self.assertFalse(replies.queue_reply("conversation"))
        self.assertEqual([call.args[0] for call in self.api.call_args_list], ["GET"])

    def test_failed_dispatch_keeps_answer_available_and_can_retry(self):
        self.api.side_effect = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            replies.queue_reply("conversation")
        self.assertEqual(list(self.root.glob("reply-dispatches/*.json")), [])
        self.assertEqual(reporting.read_report(CONFIG, "issue-1406"), self.record)
        self.api.side_effect = self.native
        self.assertTrue(replies.queue_reply("conversation"))


class RuntimeHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "factory_reply_hook_test", "/opt/agent-canvas/tools/factory_reply_hook.py"
        )
        cls.hook = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"FACTORY_REPLY_DISPATCH": "0"}):
            spec.loader.exec_module(cls.hook)

    def message(self, role="user", text="resume: yes"):
        return SimpleNamespace(role=role, content=[SimpleNamespace(text=text)])

    def test_message_hook_only_handles_explicit_user_answers_in_controller(self):
        process = SimpleNamespace(communicate=AsyncMock(return_value=(b"", b"")), returncode=0)
        with patch.object(
            self.hook.asyncio, "create_subprocess_exec", return_value=process
        ) as start:
            for enabled, role, text in (
                ("0", "user", "resume: yes"),
                ("1", "assistant", "resume: yes"),
                ("1", "user", "What happened?"),
                ("1", "user", "Factory update\nresume: yes"),
                ("1", "user", "resume:"),
            ):
                with patch.dict(os.environ, {"FACTORY_REPLY_DISPATCH": enabled}):
                    asyncio.run(
                        self.hook.message_received("conversation", self.message(role, text))
                    )
            start.assert_not_called()
            with patch.dict(os.environ, {"FACTORY_REPLY_DISPATCH": "1"}):
                asyncio.run(self.hook.message_received("conversation", self.message()))
            self.assertEqual(
                start.call_args.args,
                ("/usr/local/bin/python", "/opt/factory/workflows/replies.py", "conversation"),
            )

    def test_dispatch_failure_does_not_reject_already_saved_message(self):
        with (
            patch.dict(os.environ, {"FACTORY_REPLY_DISPATCH": "1"}),
            patch.object(
                self.hook.asyncio, "create_subprocess_exec", side_effect=OSError("offline")
            ),
            self.assertLogs(self.hook.logger, "WARNING") as logs,
        ):
            asyncio.run(self.hook.message_received("conversation", self.message()))
        self.assertIn("scan fallback retained", logs.output[0])

    def test_hung_helper_is_terminated_and_leaves_scheduled_fallback(self):
        process = SimpleNamespace(
            communicate=AsyncMock(side_effect=[TimeoutError(), (b"", b"")]),
            returncode=None,
            kill=Mock(),
        )
        with (
            patch.dict(os.environ, {"FACTORY_REPLY_DISPATCH": "1"}),
            patch.object(self.hook.asyncio, "create_subprocess_exec", return_value=process),
            self.assertLogs(self.hook.logger, "WARNING"),
        ):
            asyncio.run(self.hook.message_received("conversation", self.message()))
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.await_count, 2)

    def test_extension_preserves_message_semantics_and_only_installs_once(self):
        order = []

        class Service:
            stored = SimpleNamespace(id="conversation")

            async def send_message(self, message, run=False, _from_goal_loop=False):
                order.append(("persist", run, _from_goal_loop))
                return "persisted"

        async def notify(conversation_id, message):
            order.append(("notify", conversation_id))

        self.hook.install(Service)
        self.hook.install(Service)
        with patch.object(self.hook, "message_received", side_effect=notify):
            result = asyncio.run(Service().send_message(self.message(), True, _from_goal_loop=True))
        self.assertEqual(result, "persisted")
        self.assertEqual(order, [("persist", True, True), ("notify", "conversation")])
        with self.assertRaises(RuntimeError):
            self.hook.install(SimpleNamespace(send_message=lambda: None))
