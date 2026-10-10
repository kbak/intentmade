"""Native OpenHands profile, credentials, review receipts and read-only tools."""

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import agent
import harness
import reporting
import review
import sandbox
from factory_reader import ReadAction, Reader
from openhands.sdk.agent import Agent
from pydantic import ValidationError
from review_fixture import specialist

PROFILE = {
    "agent_kind": "openhands",
    "name": "factory-native",
    "llm_profile_ref": "fixture",
    "id": "00000000-0000-4000-8000-000000000001",
    "revision": 7,
    "condenser": {"type": "noop", "enabled": False},
    "enable_sub_agents": True,
    "enable_switch_llm_tool": True,
    "mcp_server_refs": ["operator-server"],
}
LLM = {"model": "openai/fixture", "api_key": "dummy-native-key", "base_url": "http://127.0.0.1:1"}


class NativeHarnessTests(unittest.TestCase):
    def select(self, roots=()):
        with patch.object(harness, "api", return_value={"config": LLM}):
            selected = harness.resolve(PROFILE, PROFILE["name"], roots)
        token = harness.CURRENT.set(selected)
        self.addCleanup(harness.CURRENT.reset, token)
        return selected

    def test_profile_resolution_preserves_model_but_stage_overrides_tools_and_credentials(self):
        selected = self.select(["/workspaces/job"])
        # [utest~im-native_harness-NativeHarnessTests-profile_resolution_preserves_model_but_stage_overrides_tools_and_credentials~1->req~im-agent-profile~1]
        builder = agent.worker_agent("agent-full-access", "factory-implementation")
        self.assertIsInstance(builder, Agent)
        self.assertEqual(builder.llm.model, LLM["model"])
        self.assertIn("terminal", [tool.name for tool in builder.tools])
        self.assertNotIn("SwitchLLMTool", builder.include_default_tools)
        self.assertNotIn("switch_llm", [tool.name for tool in builder.tools])
        reviewer = agent.worker_agent("read-only", "factory-review", {"untrusted": {}})
        # [utest~im-native_harness-NativeHarnessTests-profile_resolution_preserves_model_but_stage_overrides_tools_and_credentials-2~1->req~im-native-read-only~1]
        self.assertEqual([tool.name for tool in reviewer.tools], ["factory_reader"])
        self.assertEqual(reviewer.include_default_tools, ["FinishTool", "ThinkTool"])
        self.assertEqual(reviewer.mcp_config, {})
        self.assertFalse(reviewer.agent_context.load_project_skills)
        self.assertIn("alibaba-reviewer", [s.name for s in reviewer.agent_context.skills])
        self.assertNotIn("dummy-native-key", json.dumps(selected.identity))
        self.assertNotIn("CODEX_AUTH_JSON", str(reviewer.model_dump()))
        # Stage overrides must not mutate the captured profile for later builders.
        self.assertTrue(selected.settings.enable_sub_agents)

    def test_native_builder_disables_switching_for_default_and_explicit_tool_lists(self):
        from openhands.sdk import Tool

        selected = self.select()
        for tools in (None, [], [Tool(name="terminal"), Tool(name="SwitchLLMTool")]):
            with self.subTest(tools=tools):
                selected.settings.tools = tools
                builder = agent.worker_agent("agent-full-access")
                self.assertNotIn("SwitchLLMTool", builder.include_default_tools)
                self.assertNotIn("switch_llm", [tool.name for tool in builder.tools])
                self.assertEqual(selected.settings.tools, tools)
                if tools == []:
                    self.assertEqual(builder.tools, [])
                else:
                    self.assertIn("terminal", [tool.name for tool in builder.tools])

    def test_native_worker_never_opens_codex_store_and_captures_profile_once(self):
        workspace = Mock()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(harness, "profile_name", return_value="factory-native"),
            patch.object(sandbox, "api", return_value={"profile": PROFILE}) as profiles,
            patch.object(harness, "api", return_value={"config": LLM}) as llms,
            patch.object(sandbox, "require_disk_space"),
            patch.object(sandbox.docker_sandboxes, "settings", return_value={"backend": "docker"}),
            patch.object(sandbox, "docker_worker", return_value=nullcontext(workspace)),
            patch.object(sandbox, "FileSecretsStore") as secrets,
            patch.object(sandbox.provenance, "capture", return_value={"id": "fixture"}) as capture,
        ):
            # [utest~im-native_harness-NativeHarnessTests-native_worker_never_opens_codex_store_and_captures_profile_once~1->req~im-worker-credentials~1]
            with sandbox.worker(Path(directory), {}):
                selected = harness.CURRENT.get()
                self.assertTrue(selected.native)
                self.assertEqual(
                    selected.settings.llm.api_key.get_secret_value(), "dummy-native-key"
                )
                # [utest~im-native_harness-NativeHarnessTests-native_worker_never_opens_codex_store_and_captures_profile_once-2~1->req~im-agent-profile~1]
                self.assertEqual(
                    capture.call_args.kwargs["intended"]["agent_profile"]["revision"], 7
                )
                agent.worker_agent("read-only")
                agent.worker_agent("agent-full-access")
            secrets.assert_not_called()
            workspace.client.put.assert_not_called()
            workspace.client.get.assert_not_called()
            profiles.assert_called_once()
            llms.assert_called_once_with(
                "GET", "/api/profiles/fixture", headers={"X-Expose-Secrets": "plaintext"}
            )
            self.assertIsNone(harness.CURRENT.get())

    def test_unsupported_harness_or_runtime_stops_before_worker_creation(self):
        # [utest~im-native_harness-NativeHarnessTests-unsupported_harness_or_runtime_stops_before_worker_creation~1->req~im-agent-profile~1]
        with self.assertRaises(ValidationError):
            harness.resolve({}, "incomplete")
        with self.assertRaisesRegex(ValueError, "currently support"):
            harness.resolve({"agent_kind": "acp", "acp_server": "claude-code"}, "other")
        with patch.object(
            harness, "api", return_value={"config": {**LLM, "auth_type": "subscription"}}
        ):
            with self.assertRaisesRegex(ValueError, "API-backed LLM profile"):
                harness.resolve(PROFILE, PROFILE["name"])
        with (
            patch.object(harness, "profile_name", return_value="factory-native"),
            patch.object(sandbox, "api", return_value={"profile": PROFILE}),
            patch.object(harness, "api", return_value={"config": LLM}),
            patch.object(sandbox, "require_disk_space"),
            patch.object(
                sandbox.docker_sandboxes, "settings", return_value={"backend": "docker-sandboxes"}
            ),
            patch.object(sandbox.docker_sandboxes, "worker") as start,
            patch.object(sandbox, "FileSecretsStore") as store,
        ):
            with self.assertRaisesRegex(ValueError, "require the docker runtime"):
                with sandbox.worker(Path("/workspaces/job"), {}):
                    self.fail("Unsupported runtime started")
            start.assert_not_called()
            store.assert_not_called()

    def test_receipt_requires_finished_conversation_and_valid_json(self):
        self.select()
        conversation = Mock(id=uuid4())
        conversation.state.events = []
        receipt = {"status": "completed", "response": "stale"}
        workspace = Mock()
        with (
            patch.object(agent, "Conversation", return_value=conversation),
            patch.object(agent, "get_agent_final_response", return_value=json.dumps(specialist())),
        ):
            conversation.state.execution_status.value = "paused"
            # [utest~im-native_harness-NativeHarnessTests-receipt_requires_finished_conversation_and_valid_json~1->req~im-review-evidence~1]
            with self.assertRaisesRegex(RuntimeError, "Agent stopped"):
                agent.converse(
                    workspace,
                    "Review",
                    response_model=review.SpecialistReview,
                    execution_receipt=receipt,
                )
            self.assertEqual(receipt, {})
            conversation.state.execution_status.value = "finished"
            result = agent.converse(
                workspace,
                "Review",
                response_model=review.SpecialistReview,
                execution_receipt=receipt,
            )
            self.assertEqual(receipt["conversation_id"], str(conversation.id))
            self.assertEqual(json.loads(receipt["response"]), result.model_dump())
            workspace.get_secrets.assert_not_called()
        with (
            patch.object(agent, "Conversation", return_value=conversation),
            patch.object(agent, "get_agent_final_response", return_value="Review passed"),
        ):
            with self.assertRaises(agent.StructuredResponseError):
                agent.converse(
                    workspace,
                    "Review",
                    response_model=review.SpecialistReview,
                    execution_receipt=receipt,
                )
            self.assertEqual(receipt, {})

    def test_native_review_uses_controller_receipt_and_rejects_incomplete_evidence(self):
        self.select()

        def converse(workspace, prompt, **kwargs):
            self.assertIn("controller-launched", prompt)
            kwargs["execution_receipt"].update(
                status="completed", conversation_id=str(uuid4()), response=json.dumps(specialist())
            )
            return review.SpecialistReview(**specialist())

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(review, "prepare", return_value={}),
            patch.object(review, "converse", side_effect=converse),
        ):
            root = Path(directory)
            input_path = root / "input.json"
            input_path.write_text("{}")
            result = review.review_code(
                Mock(),
                "Review",
                input_path=input_path,
                transcript=root / "review.jsonl",
                initial_review=True,
            )
            # [utest~im-native_harness-NativeHarnessTests-native_review_uses_controller_receipt_and_rejects_incomplete_evidence~1->req~im-review-evidence~1]
            self.assertEqual(result.verdict, "PASS", result.summary)
            receipt = json.loads((root / "review-execution.json").read_text())
            self.assertEqual(receipt["conversation_id"], result.reviews[0].thread_id)
            for field in ("status", "conversation_id", "role", "review_run_id", "response"):
                bad = {**receipt, field: None}
                self.assertEqual(review.evaluate([], execution=bad).verdict, "BLOCKED")
            self.assertEqual(review.evaluate([receipt]).verdict, "BLOCKED")
            self.assertEqual(review.evaluate([], execution={}).verdict, "BLOCKED")

    def test_report_assistant_uses_selected_native_profile_without_codex(self):
        with (
            patch.object(harness, "profile_name", return_value="factory-native"),
            patch.object(reporting, "api", return_value={"profile": PROFILE}),
            patch.object(harness, "api", return_value={"config": LLM}),
        ):
            assistant = reporting.report_agent()
        # [utest~im-native_harness-NativeHarnessTests-report_assistant_uses_selected_native_profile_without_codex~1->req~im-native-read-only~1]
        self.assertIsInstance(assistant, Agent)
        self.assertEqual([tool.name for tool in assistant.tools], ["factory_reader"])
        self.assertIsNone(harness.CURRENT.get())

    def test_native_reader_does_not_load_ambient_executors(self):
        from openhands.sdk.conversation.impl import local_conversation

        with tempfile.TemporaryDirectory() as directory:
            self.select([directory])
            with patch.object(local_conversation, "load_available_plugins") as load:
                conversation = local_conversation.LocalConversation(
                    agent=agent.worker_agent("read-only"),
                    workspace=directory,
                    visualizer=None,
                    profile_store_dir=str(Path(directory) / "profiles"),
                )
                try:
                    conversation._ensure_plugins_loaded()
                    # [utest~im-native_harness-NativeHarnessTests-native_reader_does_not_load_ambient_executors~1->req~im-native-read-only~1]
                    load.assert_not_called()
                finally:
                    conversation.close()


class ReaderTests(unittest.TestCase):
    def test_search_supports_single_files_and_bounds_dense_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "many.txt"
            path.write_text(("needle " + "x" * 200 + "\n") * 2100)
            reader = Reader([directory])
            result = reader.inspect(ReadAction(operation="search", path=str(path), text="needle"))
            # [utest~im-native_harness-ReaderTests-search_supports_single_files_and_bounds_dense_results~1->req~im-native-read-only~1]
            self.assertIn("many.txt:1:", result)
            self.assertIn("truncated", result)
            self.assertLess(len(result), 64100)

    def test_reader_blocks_escape_special_files_and_write_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "code.py").write_text("original\n")
            (root / "secret").write_text("not source")
            (source / "escape").symlink_to(root / "secret")
            os.mkfifo(source / "pipe")
            reader = Reader([source])
            # [utest~im-native_harness-ReaderTests-reader_blocks_escape_special_files_and_write_actions~1->req~im-native-read-only~1]
            self.assertIn(
                "original",
                reader.inspect(ReadAction(operation="read", path=str(source / "code.py"))),
            )
            for path in (source / "escape", source / "pipe", root / "secret"):
                self.assertTrue(reader(ReadAction(operation="read", path=str(path))).is_error)
            with self.assertRaises(ValidationError):
                ReadAction(operation="write", path=str(source / "code.py"))
            self.assertEqual((source / "code.py").read_text(), "original\n")

    def test_git_diff_disables_external_execution_and_checks_revision_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()

            git("init", "-q")
            git("config", "user.email", "fixture@example.test")
            git("config", "user.name", "Fixture")
            (root / "code.py").write_text("before\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "code.py").write_text("after\n")
            git("commit", "-qam", "candidate")
            head = git("rev-parse", "HEAD")
            git("config", "diff.external", "touch SHOULD_NOT_EXIST")
            reader = Reader([root])
            action = ReadAction(operation="diff", path=directory, base=base, candidate=head)
            # [utest~im-native_harness-ReaderTests-git_diff_disables_external_execution_and_checks_revision_inputs~1->req~im-native-read-only~1]
            self.assertIn("+after", reader.inspect(action))
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())
            self.assertIn(
                "before",
                reader.inspect(
                    ReadAction(operation="show", path=directory, base=base, text="code.py")
                ),
            )
            with self.assertRaisesRegex(ValueError, "commit IDs"):
                reader.inspect(action.model_copy(update={"base": "--output=owned"}))


if __name__ == "__main__":
    unittest.main()
