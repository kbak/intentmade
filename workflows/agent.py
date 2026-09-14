"""Codex subscription adapter using native OpenHands conversations and secrets."""

import json
import os
from pathlib import Path
from uuid import UUID

from common import session_api_key
from openhands.sdk import AgentContext, Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.context import Skill
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.workspace import RemoteWorkspace
from pydantic import ValidationError


def canvas():
    return RemoteWorkspace(
        host="http://127.0.0.1:8000",
        api_key=session_api_key(),
        working_dir="/projects",
    )


def stage_context(name):
    """Load the selected procedure using OpenHands' native skill parser/context."""
    skill = Skill.load(Path(__file__).with_name("skills") / name / "SKILL.md")
    # Stages already know which skill they need. OpenHands' always-active form
    # carries its body across the remote boundary, including tool-free editing.
    return AgentContext(
        skills=[Skill(name=skill.name, content=skill.content, source=skill.source)],
        current_datetime=None,
    )


def worker_agent(mode, skill=None, mcp_config=None, traceability=False):
    """Use the model captured from factory-codex when this worker started."""
    context = stage_context(skill) if skill else None
    if traceability:
        from openhands_traceability import with_traceability

        context = with_traceability(context)
    return ACPAgent(
        acp_command=["codex-acp"],
        acp_server="codex",
        acp_session_mode=mode,
        acp_model=os.environ["FACTORY_CODEX_MODEL"] or None,
        agent_context=context,
        mcp_config=mcp_config or {},
    )


def converse(
    workspace,
    prompt,
    mode="read-only",
    title="Factory discussion",
    conversation_id=None,
    response_model=None,
    transcript=None,
    event_log=None,
    skill=None,
    mcp_config=None,
    traceability=False,
):
    conversation = Conversation(
        agent=worker_agent(mode, skill, mcp_config, traceability),
        workspace=workspace,
        delete_on_close=False,
        conversation_id=UUID(conversation_id) if conversation_id else None,
        secrets=workspace.get_secrets(names=["CODEX_AUTH_JSON"]),
        visualizer=None,
    )
    try:
        workspace.client.patch(
            f"/api/conversations/{conversation.id}", json={"title": title}
        ).raise_for_status()
        print(f"Conversation: {conversation.id}", flush=True)
        schema = (
            "\nReturn only a JSON object matching this schema, with no Markdown fences or progress messages:\n"
            + json.dumps(response_model.model_json_schema())
            if response_model
            else ""
        )
        conversation.send_message(prompt + schema)
        for attempt in range(2 if response_model else 1):
            conversation.run()
            if conversation.state.execution_status.value != "finished":
                raise RuntimeError(f"Agent stopped with {conversation.state.execution_status}")
            text = get_agent_final_response(conversation.state.events)
            if not response_model:
                return text
            try:
                return response_model.model_validate_json(text)
            except ValidationError:
                if attempt:
                    raise RuntimeError(
                        "Reviewer did not return a valid structured verdict"
                    ) from None
                # ACP can aggregate progress text with the final response. Ask the
                # same reviewer to restate its decision; never guess from prose.
                conversation.send_message(
                    "Restate your final response in the required JSON format. "
                    "Do not use tools or add commentary." + schema
                )
    finally:
        if event_log is not None:
            event_log.extend(event.model_dump(mode="json") for event in conversation.state.events)
        if transcript:
            # Save before the disposable agent server exits, including failures
            # and requests for input. This contains agent events, not its secrets.
            try:
                with transcript.open("w") as handle:
                    for event in conversation.state.events:
                        handle.write(event.model_dump_json() + "\n")
            except Exception as exc:
                print(f"Could not save conversation transcript: {type(exc).__name__}", flush=True)
        conversation.close()


def worktree(workspace, traceability=False):
    """Let Agent Server create the branch/worktree before any agent executes."""
    response = workspace.client.post(
        "/api/conversations",
        json={
            "workspace": {"working_dir": workspace.working_dir},
            "worktree": True,
            # Attaching later does not replace the server's saved agent context.
            "agent": worker_agent(
                "agent-full-access", "factory-implementation", traceability=traceability
            ).model_dump(mode="json"),
        },
    )
    response.raise_for_status()
    data = response.json()
    workspace.working_dir = data["workspace"]["working_dir"]
    return str(data["id"])
