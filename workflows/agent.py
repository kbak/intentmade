"""Codex subscription adapter using native OpenHands conversations and secrets."""

import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import measurements
from common import session_api_key
from openhands.sdk import AgentContext, Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.context import Skill
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.workspace import RemoteWorkspace
from pydantic import ValidationError


class AgentStartupError(RuntimeError):
    def __init__(self, details):
        self.details = details
        super().__init__(f"{details['code']}: {details['detail']}")


def run_with_startup_recovery(conversation, transcript=None):
    """Retry one proven startup timeout, before any agent turn can do work."""
    for attempt in range(2):
        before = {str(event.id) for event in conversation.state.events}
        try:
            conversation.run()
            return
        except Exception as exc:
            events = [
                event.model_dump(mode="json")
                for event in conversation.state.events
                if str(event.id) not in before
            ]
            errors = [e for e in events if e.get("kind") == "ConversationErrorEvent"]
            # The native exception carries the current run's error even when
            # websocket event delivery has not caught up with a quick retry.
            native_error = getattr(exc, "conversation_error", None)
            if native_error is not None:
                errors.append(native_error.model_dump(mode="json"))
            # Agent Server may append a generic environment error after the
            # agent's typed initialization error. Preserve the specific cause.
            errors = [
                e
                for e in errors
                if e.get("code")
                in {"ACPStartupTimeout", "ACPAuthRequired", "ACPSpawnError", "ACPInitError"}
            ]
            if not errors:
                raise
            error = errors[-1]
            code = error["code"]
            acted = any(
                e.get("source") == "agent"
                and e.get("kind") != "ConversationErrorEvent"
                or e.get("key") == "agent_state"
                and e.get("value", {}).get("acp_session_id")
                for e in events
            )
            details = {
                "code": code,
                "detail": error.get("detail") or "Agent initialization failed",
                "conversation_id": str(conversation.id),
                "attempt": attempt + 1,
                "retrying": code == "ACPStartupTimeout" and not acted and attempt == 0,
            }
            if transcript:
                path = transcript.with_name(f"{transcript.stem}-startup-{conversation.id}.jsonl")
                with path.open("a") as handle:
                    handle.write(json.dumps(details) + "\n")
            if not details["retrying"]:
                raise AgentStartupError(details) from exc
            print(
                "Agent startup timed out before any work; retrying once in 2 seconds.", flush=True
            )
            time.sleep(2)


class StructuredResponseError(RuntimeError):
    def __init__(self, details):
        self.details = details
        super().__init__(
            f"{details['stage']} returned invalid {details['schema_name']} after "
            f"{details['response_attempt']} response attempts; "
            f"diagnostics: {json.dumps(details['validation_errors'])}; "
            f"retained response: {details['response_artifact'] or 'see native conversation'}"
        )


def response_failure(exc, text, model, title, attempt, conversation_id, transcript):
    errors = [
        {"location": list(e["loc"]), "type": e["type"], "message": e["msg"][:240]}
        for e in exc.errors(include_input=False, include_context=False, include_url=False)[:8]
    ]
    artifact = (
        transcript.with_name(
            f"{transcript.stem}.response-{conversation_id}-{attempt + 1}-{uuid4().hex[:12]}.json"
        )
        if transcript
        else None
    )
    details = {
        "kind": "invalid_structured_response",
        "stage": title,
        "schema_name": model.__name__,
        "expected_schema": model.model_json_schema(),
        "response_attempt": attempt + 1,
        "validation_errors": errors,
        "response_artifact": str(artifact) if artifact else None,
        "conversation_id": str(conversation_id),
    }
    if artifact:
        # Raw model output stays in the retained artifact, never in the error UI.
        artifact.write_text(json.dumps({**details, "raw_response": text}, indent=2) + "\n")
    measurements.response_validation(details)
    return details


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
        from traceability.openhands import with_traceability

        context = with_traceability(context, provisioned=True)
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
    started = time.monotonic()
    usage_before = measurements.usage_snapshot(conversation) if measurements.CURRENT.get() else None
    usage_event_ids = {e["id"] for e in measurements.usage_evidence(conversation)}
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
            run_with_startup_recovery(conversation, transcript)
            if conversation.state.execution_status.value != "finished":
                raise RuntimeError(f"Agent stopped with {conversation.state.execution_status}")
            text = get_agent_final_response(conversation.state.events)
            if not response_model:
                return text
            try:
                result = response_model.model_validate_json(text)
                if attempt:
                    measurements.response_validation(
                        {
                            "kind": "structured_response_repaired",
                            "stage": title,
                            "schema_name": response_model.__name__,
                            "response_attempt": attempt + 1,
                            "conversation_id": str(conversation.id),
                        }
                    )
                return result
            except ValidationError as exc:
                details = response_failure(
                    exc, text, response_model, title, attempt, conversation.id, transcript
                )
                if attempt:
                    raise StructuredResponseError(details) from None
                # ACP can aggregate progress text with the final response. Ask the
                # same reviewer to restate its decision; never guess from prose.
                conversation.send_message(
                    "Restate your final response in the required JSON format. "
                    "Correct these validation errors: "
                    + json.dumps(details["validation_errors"])
                    + ". Preserve your actual decision; never invent a question or approval. "
                    "Do not use tools or add commentary." + schema
                )
    finally:
        try:
            measurements.record_agent(
                conversation, usage_before, started, skill, transcript, usage_event_ids
            )
        except Exception as exc:
            print(f"Agent metrics unavailable: {type(exc).__name__}", flush=True)
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
