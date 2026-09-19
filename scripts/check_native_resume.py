"""Run only in a disposable Codex/ACP container; uses a local scripted provider."""

import argparse
import http.server
import json
import os
import tempfile
import threading
from pathlib import Path

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import get_agent_final_response
from openhands.sdk.workspace import LocalWorkspace
from openhands_traceability import with_recovery, with_traceability


def main(recovery=False):
    context = with_recovery() if recovery else with_traceability()
    procedure = context.skills[0].content
    observations = []

    class Endpoint(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            texts = [
                part.get("text", "")
                for message in payload.get("input", [])
                if message.get("type") == "message"
                for part in message.get("content", [])
            ]
            observations.append(
                {
                    "procedure_present": any(procedure in text for text in texts),
                    "repair_present": any(
                        "Repair the failing boundary test" in text for text in texts
                    ),
                }
            )
            answer = "Scripted response: traceability instructions received."
            item = {
                "type": "message",
                "id": "msg_fixture",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": answer}],
            }
            events = [
                {
                    "type": "response.created",
                    "response": {"id": "fixture", "status": "in_progress", "output": []},
                },
                {"type": "response.output_item.added", "output_index": 0, "item": item},
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg_fixture",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": answer,
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "fixture",
                        "status": "completed",
                        "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
            ]
            data = "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # ACP's authentication discovery reads native provider configuration. Only
    # create it in a fresh disposable container, never overwrite user settings.
    if not Path("/.dockerenv").exists():
        raise RuntimeError("Run this probe in a fresh disposable Docker container")
    config = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    with config.open("x") as handle:
        handle.write(f"""model = "gpt-6-astra"
model_provider = "fixture"
[model_providers.fixture]
name = "Local scripted provider"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
""")
    try:
        with tempfile.TemporaryDirectory(prefix="traceability-acp-") as root:
            conversation = Conversation(
                agent=ACPAgent(
                    acp_command=["codex-acp"],
                    acp_server="codex",
                    acp_session_mode="read-only",
                    acp_model="gpt-6-astra/high",
                    agent_context=context,
                ),
                persistence_dir=Path(root) / "native-state",
                delete_on_close=False,
                workspace=LocalWorkspace(working_dir=root),
                visualizer=None,
            )
            try:
                prior_session = None
                original_id = conversation.id
                for index, prompt in enumerate(
                    (
                        "Implement the authorized fixture task. Do not use tools.",
                        "Repair the failing boundary test within that task. Do not use tools.",
                    )
                ):
                    if index:
                        conversation = Conversation(
                            agent=ACPAgent(
                                acp_command=["codex-acp"],
                                acp_server="codex",
                                acp_session_mode="read-only",
                                acp_model="gpt-6-astra/high",
                                agent_context=context,
                            ),
                            persistence_dir=Path(root) / "native-state",
                            delete_on_close=False,
                            conversation_id=original_id,
                            workspace=LocalWorkspace(working_dir=root),
                            visualizer=None,
                        )
                    conversation.send_message(prompt)
                    conversation.run()
                    assert "instructions received" in get_agent_final_response(
                        conversation.state.events
                    )
                    session = conversation.state.agent_state.get("acp_session_id")
                    assert session
                    if index:
                        assert session == prior_session, "Native provider session did not resume"
                    prior_session = session
                    if not index:
                        conversation.close()
                import measurements

                events = measurements.usage_evidence(conversation)
                assert len(events) >= 2, events
                assert events[-1]["delta"] is None, (
                    "Restart without a provider baseline must remain unknown"
                )
                assert len({e["id"] for e in events}) == len(events)
                print(
                    json.dumps(
                        {
                            "native_usage_events": len(events),
                            "observed_output_deltas": [
                                e["delta"]["outputTokens"] if e["delta"] else None for e in events
                            ],
                            "delegated_usage": [e["delegated_usage"] for e in events],
                        }
                    )
                )
            finally:
                conversation.close()
        assert len(observations) >= 2, observations
        assert all(value["procedure_present"] for value in observations), observations
        # The pinned client may also request background session metadata.
        assert any(value["repair_present"] for value in observations), observations
        print(
            "PASS: native OpenHands conversation and ACP session resumed after closing/recreating the conversation with retained provider state and unchanged cwd; local scripted provider, no live LLM."
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        config.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery", action="store_true", help="Probe baseline recovery context")
    main(parser.parse_args().recovery)
