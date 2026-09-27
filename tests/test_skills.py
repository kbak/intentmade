"""Required skills survive native serialization, worktree attachment and bundle delivery."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import agent
from openhands.sdk.agent import ACPAgent
from openhands.sdk.context import Skill, SkillValidationError


class FactorySkillTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Requires the pilot test image")
    def test_opted_in_context_is_saved_before_worktree_creation_and_on_repair(self):
        workspace = Mock()
        identity = str(uuid4())
        workspace.working_dir = "/workspaces/source"
        workspace.client.post.return_value.json.return_value = {
            "id": identity,
            "workspace": {"working_dir": "/workspaces/worktrees/task"},
        }
        conversation = Mock()
        conversation.state.execution_status.value = "finished"
        conversation.state.events = []
        with (
            patch.dict(os.environ, FACTORY_CODEX_MODEL="test/high"),
            patch.object(agent, "Conversation", return_value=conversation) as create,
            patch.object(agent, "get_agent_final_response", return_value="Done"),
        ):
            saved_id = agent.worktree(workspace, traceability=True)
            saved = ACPAgent.model_validate(workspace.client.post.call_args.kwargs["json"]["agent"])
            self.assertEqual(
                [s.name for s in saved.agent_context.skills],
                ["factory-implementation", "intentbond"],
            )
            self.assertNotIn("pip install", saved.agent_context.to_acp_prompt_context())
            for identity in (saved_id, None):
                agent.converse(
                    workspace,
                    "Continue or repair this task",
                    mode="agent-full-access",
                    conversation_id=identity,
                    skill="factory-implementation",
                    traceability=True,
                )
                self.assertEqual(
                    create.call_args.kwargs["agent"].agent_context, saved.agent_context
                )

    def test_native_acp_context_contains_selected_body_without_worker_file_access(self):
        for name in (
            "factory-implementation",
            "factory-review",
            "factory-review-report",
            "factory-browser-qa",
        ):
            with self.subTest(skill=name), patch.dict(os.environ, FACTORY_CODEX_MODEL="test/high"):
                original = agent.worker_agent("read-only", name)
                # Model the JSON boundary between the automation and worker.
                serialized = original.model_dump_json()
                with patch.object(Skill, "load", side_effect=AssertionError("Worker file lookup")):
                    restored = ACPAgent.model_validate_json(serialized)
                    rendered = restored.agent_context.to_acp_prompt_context()
                self.assertEqual(len(restored.agent_context.skills), 1)
                selected = restored.agent_context.skills[0]
                self.assertEqual(selected.name, name)
                self.assertIn(selected.content, rendered)
                self.assertEqual(restored.acp_model, "test/high")
                self.assertEqual(restored.acp_session_mode, "read-only")
                self.assertFalse(restored.agent_context.load_public_skills)
                self.assertFalse(restored.agent_context.load_user_skills)

    def test_implementation_skill_is_saved_at_worktree_creation_and_used_for_grouped_jobs(self):
        workspace = Mock()
        identity = str(uuid4())
        workspace.working_dir = "/workspaces/source"
        workspace.client.post.return_value.json.return_value = {
            "id": identity,
            "workspace": {"working_dir": "/workspaces/worktrees/task"},
        }
        conversation = Mock()
        conversation.state.execution_status.value = "finished"
        conversation.state.events = []
        with (
            patch.dict(os.environ, FACTORY_CODEX_MODEL="test/high"),
            patch.object(agent, "Conversation", return_value=conversation) as create,
            patch.object(agent, "get_agent_final_response", return_value="Done"),
        ):
            resumed = agent.worktree(workspace)
            saved = ACPAgent.model_validate(workspace.client.post.call_args.kwargs["json"]["agent"])
            self.assertEqual(saved.agent_context.skills[0].name, "factory-implementation")
            for existing in (resumed, None):
                agent.converse(
                    workspace,
                    "Implement this task",
                    mode="agent-full-access",
                    conversation_id=existing,
                    skill="factory-implementation",
                )
                requested = create.call_args.kwargs["agent"]
                self.assertEqual(requested.agent_context, saved.agent_context)

    def test_missing_or_invalid_skill_stops_before_starting_a_conversation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "skills/invalid/SKILL.md")
            path.parent.mkdir(parents=True)
            path.write_text("---\nname: Different Name\ndescription: Invalid fixture\n---\nBody")
            with (
                patch.object(agent, "__file__", str(Path(temp, "agent.py"))),
                patch.dict(os.environ, FACTORY_CODEX_MODEL="test/high"),
                patch.object(agent, "Conversation") as create,
            ):
                for name in ("missing", "invalid"):
                    with (
                        self.subTest(skill=name),
                        self.assertRaises((FileNotFoundError, SkillValidationError)),
                    ):
                        agent.converse(Mock(), "Task", skill=name)
                create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
