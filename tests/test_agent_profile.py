"""Model selection crosses the worker boundary without changing role permissions."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import uuid4

import agent
import sandbox
from openhands.sdk.agent.acp_agent import _apply_acp_model


class WorkerProfileTests(unittest.TestCase):
    def test_each_worker_captures_the_saved_model_for_new_and_resumed_conversations(self):
        profile = {"acp_model": "gpt-6-astra/xhigh"}
        store = Mock()
        store.load_versioned_secret.return_value = ("worker-login", 1)
        workspace = Mock()
        workspace.client.get.return_value.text = "worker-login"
        conversation_id = str(uuid4())
        workspace.client.post.return_value.json.return_value = {
            "id": conversation_id,
            "workspace": {"working_dir": "/workspaces/profile-test/worktrees/task"},
        }
        conversation = Mock()
        conversation.state.events = []
        conversation.state.execution_status.value = "finished"

        def parent_profile(*args):
            self.assertEqual(os.environ["OH_SESSION_API_KEYS_0"], "parent-key")
            return {"profile": profile}

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"OH_SESSION_API_KEYS_0": "parent-key", "OH_SECRET_KEY": "key"}),
            patch.object(sandbox, "api", side_effect=parent_profile) as api,
            patch.object(sandbox, "Cipher"),
            patch.object(sandbox, "FileSecretsStore", return_value=store),
            patch.object(sandbox.subprocess, "run"),
            patch.object(sandbox, "find_available_tcp_port", return_value=12345),
            patch.object(sandbox, "DockerWorkspace") as docker,
            patch.object(sandbox.provenance, "capture", return_value={"id": "fixture"}),
            patch.object(agent, "Conversation", return_value=conversation) as create,
            patch.object(agent, "get_agent_final_response", return_value="Done"),
        ):
            docker.return_value.__enter__.return_value = workspace
            previous_model = os.environ.get("FACTORY_CODEX_MODEL")
            for selected in ("gpt-6-astra/xhigh", "gpt-6-astra/high", None):
                profile["acp_model"] = selected
                with sandbox.worker(Path(temporary), {}) as worker:
                    # A later UI edit must not alter a worker already in progress.
                    profile["acp_model"] = "gpt-5.5/low"
                    resumed = agent.worktree(worker)
                    body = workspace.client.post.call_args.kwargs["json"]
                    self.assertEqual(body["agent"]["acp_model"], selected)
                    self.assertEqual(body["agent"]["acp_session_mode"], "agent-full-access")
                    for mode, existing in (("agent-full-access", resumed), ("read-only", None)):
                        agent.converse(worker, "Task", mode=mode, conversation_id=existing)
                        requested = create.call_args.kwargs["agent"]
                        self.assertEqual(requested.acp_model, selected)
                        self.assertEqual(requested.acp_session_mode, mode)
                        self.assertEqual(requested.acp_server, "codex")
                        self.assertNotIn("OH_SECRET_KEY", docker.call_args.kwargs["forward_env"])
                self.assertEqual(os.environ["OH_SESSION_API_KEYS_0"], "parent-key")
                self.assertEqual(os.environ.get("FACTORY_CODEX_MODEL"), previous_model)
            self.assertEqual(
                api.call_args_list, [call("GET", "/api/agent-profiles/factory-codex")] * 3
            )

    def test_missing_profile_does_not_silently_start_with_another_model(self):
        with (
            patch.object(sandbox, "api", side_effect=RuntimeError("Profile unavailable")),
            patch.object(sandbox.subprocess, "run") as start,
        ):
            with self.assertRaisesRegex(RuntimeError, "Profile unavailable"):
                with sandbox.worker(Path("/workspaces/profile-test"), {}):
                    self.fail("Worker should not start")
            start.assert_not_called()

    def test_filtered_parent_env_uses_mounted_encryption_key_without_forwarding_it(self):
        store = Mock()
        store.load_versioned_secret.return_value = ("codex-login", 1)
        workspace = Mock()
        workspace.client.get.return_value.text = "codex-login"
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                sandbox, "api", return_value={"profile": {"acp_model": "gpt-6-astra/xhigh"}}
            ),
            patch.object(sandbox, "Path") as path,
            patch.object(sandbox, "Cipher") as cipher,
            patch.object(sandbox, "FileSecretsStore", return_value=store),
            patch.object(sandbox.subprocess, "run"),
            patch.object(sandbox, "find_available_tcp_port", return_value=12345),
            patch.object(sandbox, "DockerWorkspace") as docker,
            patch.object(sandbox.provenance, "capture", return_value={"id": "fixture"}),
        ):
            path.return_value.read_text.return_value = "mounted-encryption-key\n"
            docker.return_value.__enter__.return_value = workspace
            with sandbox.worker(Path(temporary), {}):
                cipher.assert_called_once_with("mounted-encryption-key")
                self.assertNotIn("OH_SECRET_KEY", docker.call_args.kwargs["forward_env"])
                self.assertNotIn("OH_SECRET_KEY", os.environ)
            self.assertNotIn("OH_SESSION_API_KEYS_0", os.environ)

    def test_installed_sdk_sends_astra_and_extra_high_as_separate_options(self):
        connection = AsyncMock()
        asyncio.run(
            _apply_acp_model(
                connection,
                "session",
                "gpt-6-astra/xhigh",
                agent_name="@agentclientprotocol/codex-acp",
                via_config_option=True,
            )
        )
        self.assertEqual(
            connection.set_config_option.await_args_list,
            [
                call(config_id="model", value="gpt-6-astra", session_id="session"),
                call(config_id="reasoning_effort", value="xhigh", session_id="session"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
