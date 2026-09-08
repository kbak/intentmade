"""Exercise real Codex/ACP roles against a local scripted model, without credentials.

Run inside the factory image as documented in tests/README.md. This verifies
orchestration and native evidence, not the quality of a model's code review.
"""

import http.server
import json
import os
import tempfile
import threading
import tomllib
from pathlib import Path

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.workspace import LocalWorkspace
from review import ROLES, coordinator_prompt, evaluate


def main():
    barrier = threading.Barrier(2, timeout=15)
    finished, verified, failures = set(), set(), []
    parent_requests = 0
    instructions = {
        key: tomllib.loads(Path(f"/opt/factory/agency-agents/agents/{slug}.toml").read_text())[
            "developer_instructions"
        ]
        for key, slug in (
            ("code", "code-reviewer"),
            ("security", "application-security-engineer"),
        )
    }

    def function(name, key, args):
        return {
            "type": "function_call",
            "id": f"fc_{key}",
            "call_id": f"call_{key}",
            "namespace": "collaboration",
            "name": name,
            "arguments": json.dumps(args),
        }

    class Endpoint(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal parent_requests
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = payload.get("input", [])
            recipients = {m.get("recipient") for m in messages if m.get("type") == "agent_message"}
            key = next((k for k in instructions if f"/root/{k}" in recipients), None)
            items = []
            if key:
                try:
                    texts = [
                        part.get("text", "")
                        for message in messages
                        if message.get("type") == "message"
                        for part in message.get("content", [])
                    ]
                    assert any(instructions[key] in text for text in texts), "Role prompt missing"
                    assert payload["model"] == "gpt-6-astra", "Model was not inherited"
                    environment = next(
                        text for text in texts if text.startswith("<environment_context>")
                    )
                    assert 'access="write"' not in environment, "Child has write permissions"
                    assert 'access="read"' in environment, "Child sandbox was not advertised"
                    # Neither specialist responds until both model requests arrive.
                    barrier.wait()
                    verified.add(key)
                except Exception as exc:
                    failures.append(f"{key}: {type(exc).__name__}: {exc}")
                report = {
                    "verdict": "CHANGES_REQUESTED" if key == "security" else "PASS",
                    "summary": "Scripted native specialist result.",
                    "blocking_findings": [],
                    "non_blocking_findings": [],
                    "infrastructure_error": None,
                }
                if key == "security":
                    report["blocking_findings"].append(
                        {
                            "title": "Optional header hardening",
                            "category": "security",
                            "severity": "low",
                            "file": "fixture.py",
                            "line": 1,
                            "evidence": "An optional header is absent.",
                            "scenario": "No material attack path demonstrated.",
                            "impact": "Low impact.",
                            "remediation": "Consider adding the header later.",
                            "introduced_or_worsened": True,
                            "demonstrated_exploitability": False,
                            "material_impact": False,
                        }
                    )
                answer = json.dumps(report)
                finished.add(key)
            else:
                parent_requests += 1
                if parent_requests == 1:
                    items = [
                        function(
                            "spawn_agent",
                            key,
                            {
                                "task_name": key,
                                "agent_type": role,
                                "fork_turns": "none",
                                "message": "Return the scripted specialist JSON for the local fixture.",
                            },
                        )
                        for key, role in zip(("code", "security"), ROLES)
                    ]
                elif parent_requests == 2 or (len(finished) < 2 and parent_requests < 6):
                    items = [
                        function("wait_agent", f"wait_{parent_requests}", {"timeout_ms": 10000})
                    ]
                answer = '{"summary":"Both specialists completed."}'
            if not items:
                items = [
                    {
                        "type": "message",
                        "id": f"msg_{key or 'root'}",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": [{"type": "output_text", "text": answer}],
                    }
                ]
            events = [
                {
                    "type": "response.created",
                    "response": {"id": "fixture", "status": "in_progress", "output": []},
                }
            ]
            for index, item in enumerate(items):
                events.append(
                    {"type": "response.output_item.added", "output_index": index, "item": item}
                )
                if item["type"] == "message":
                    events.append(
                        {
                            "type": "response.output_text.delta",
                            "item_id": item["id"],
                            "output_index": index,
                            "content_index": 0,
                            "delta": answer,
                        }
                    )
                events.append(
                    {"type": "response.output_item.done", "output_index": index, "item": item}
                )
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "fixture",
                        "status": "completed",
                        "output": items,
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                }
            )
            body = "".join(
                f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with tempfile.TemporaryDirectory() as temp:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        # The smoke runs in its own process/container; no host home or login is used.
        os.environ["CODEX_HOME"] = temp
        Path(temp, "config.toml").write_text(f"""model = "gpt-6-astra"
model_provider = "fixture"
[model_providers.fixture]
name = "Local scripted model"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
""")
        conversation = Conversation(
            agent=ACPAgent(
                acp_command=["codex-acp"],
                acp_server="codex",
                acp_session_mode="read-only",
                acp_model="gpt-6-astra",
            ),
            workspace=LocalWorkspace(working_dir=temp),
            visualizer=None,
        )
        try:
            conversation.send_message(coordinator_prompt("Review the local fixture."))
            conversation.run()
            result = evaluate(
                [event.model_dump(mode="json") for event in conversation.state.events]
            )
            assert not failures, failures
            assert verified == {"code", "security"}, "Both native roles must execute concurrently"
            assert result.verdict == "PASS", result.model_dump_json(indent=2)
            assert len(result.reviews) == 2
            assert len(result.reviews[1].non_blocking_findings) == 1
            assert not result.reviews[1].blocking_findings
            print(
                "PASS: both native roles, role prompts, concurrent execution, inherited model/read-only policy, and advisory verdict."
            )
        finally:
            conversation.close()
            server.shutdown()
            server.server_close()
            serving.join()


if __name__ == "__main__":
    main()
