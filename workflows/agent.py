"""Codex subscription adapter using native OpenHands conversations and secrets."""

import json
import os
from uuid import UUID

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.workspace import RemoteWorkspace
from pydantic import ValidationError


def canvas():
    return RemoteWorkspace(
        host="http://127.0.0.1:8000",
        api_key=os.environ["OH_SESSION_API_KEYS_0"],
        working_dir="/projects",
    )


def converse(
    workspace,
    prompt,
    mode="read-only",
    title="Factory discussion",
    conversation_id=None,
    response_model=None,
    transcript=None,
):
    conversation = Conversation(
        agent=ACPAgent(acp_command=["codex-acp"], acp_server="codex", acp_session_mode=mode),
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


def worktree(workspace):
    """Let Agent Server create the branch/worktree before any agent executes."""
    response = workspace.client.post(
        "/api/conversations",
        json={
            "workspace": {"working_dir": workspace.working_dir},
            "worktree": True,
            "agent": ACPAgent(
                acp_command=["codex-acp"], acp_server="codex", acp_session_mode="agent-full-access"
            ).model_dump(mode="json"),
        },
    )
    response.raise_for_status()
    data = response.json()
    workspace.working_dir = data["workspace"]["working_dir"]
    return str(data["id"])
