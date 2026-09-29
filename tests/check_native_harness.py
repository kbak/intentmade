"""Offline native model endpoint -> real remote Agent Server -> factory stages.

Run inside the test image with --network none. No provider or production login.
"""

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import agent
import harness
import httpx
import review
from openhands.sdk.workspace import RemoteWorkspace


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        for name, value in (("user.name", "Fixture"), ("user.email", "fixture@example.test")):
            subprocess.run(["git", "-C", str(source), "config", name, value], check=True)
        (source / "base.txt").write_text("base\n")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"], check=True)
        phase = {"name": "build", "calls": 0, "target": None}
        specialist = {
            "verdict": "PASS",
            "summary": "Inspected scripted fixture.",
            "blocking_findings": [],
            "non_blocking_findings": [],
            "infrastructure_error": None,
            "coverage": [],
            "intent_alignment": [],
        }

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                names = {tool["function"]["name"] for tool in request["tools"]}
                stage = phase["name"]
                count = phase["calls"]
                phase["calls"] += 1
                if stage == "review":
                    assert names == {"factory_reader", "think", "finish"}, names
                    if count == 0:
                        name, args = (
                            "factory_reader",
                            {"operation": "read", "path": phase["target"]},
                        )
                    else:
                        assert "repaired" in json.dumps(request["messages"])
                        name, args = "finish", {"message": json.dumps(specialist)}
                else:
                    assert "file_editor" in names, names
                    if count == 0:
                        name = "file_editor"
                        args = {
                            "command": "create",
                            "path": phase["target"],
                            "file_text": "built\n",
                        }
                        if stage == "repair":
                            args = {
                                "command": "str_replace",
                                "path": phase["target"],
                                "old_str": "built",
                                "new_str": "repaired",
                            }
                    else:
                        name, args = "finish", {"message": "Done"}
                response = {
                    "id": "offline-" + str(uuid4()),
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_" + str(uuid4()),
                                        "type": "function",
                                        "function": {"name": name, "arguments": json.dumps(args)},
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                }
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        key = "offline-native-probe"
        env = {
            **os.environ,
            "OH_SESSION_API_KEYS_0": key,
            "FACTORY_HEADLESS": "1",
            "OH_PERSISTENCE_DIR": str(root / "server"),
            "OH_CONVERSATIONS_PATH": str(root / "server/conversations"),
            "OH_CONVERSATION_WORKTREE_ROOT": str(root / "worktrees"),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        profile = {
            "name": "fixture",
            "agent_kind": "openhands",
            "llm_profile_ref": "fixture",
            "condenser": {"type": "noop", "enabled": False},
        }
        config = {
            "model": "openai/gpt-4o",
            "api_key": "offline-fixture-key",
            "base_url": f"http://127.0.0.1:{model.server_port}/v1",
            "stream": False,
        }
        with patch.object(harness, "api", return_value={"config": config}):
            selected = harness.resolve(profile, "fixture", [str(root)])
        token = harness.CURRENT.set(selected)
        try:
            with (root / "server.log").open("w") as log:
                server = subprocess.Popen(
                    [
                        "/usr/local/bin/openhands-agent-server",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                try:
                    with httpx.Client(base_url=url, headers={"X-Session-API-Key": key}) as client:
                        for _ in range(80):
                            try:
                                if client.get("/openapi.json").status_code == 200:
                                    break
                            except httpx.ConnectError:
                                pass
                            if server.poll() is not None:
                                raise RuntimeError("Native agent server exited")
                            time.sleep(0.25)
                        else:
                            raise RuntimeError("Native agent server did not start")
                    workspace = RemoteWorkspace(host=url, api_key=key, working_dir=str(source))
                    conversation_id = agent.worktree(workspace)
                    phase["target"] = str(Path(workspace.working_dir) / "result.txt")
                    assert (
                        agent.converse(
                            workspace,
                            "Build fixture",
                            mode="agent-full-access",
                            conversation_id=conversation_id,
                        )
                        == "Done"
                    )
                    assert Path(phase["target"]).read_text() == "built\n"
                    phase.update(name="repair", calls=0)
                    agent.converse(
                        workspace,
                        "Repair fixture",
                        mode="agent-full-access",
                        conversation_id=conversation_id,
                    )
                    assert Path(phase["target"]).read_text() == "repaired\n"
                    phase.update(name="review", calls=0)
                    receipt = {}
                    agent.converse(
                        workspace,
                        "Inspect the fixture",
                        skill="factory-review",
                        response_model=review.SpecialistReview,
                        execution_receipt=receipt,
                        transcript=root / "review.jsonl",
                    )
                    receipt.update(review_run_id=str(uuid4()), role=review.ROLES[0])
                    assert review.evaluate([], execution=receipt).verdict == "PASS"
                    assert receipt["conversation_id"] != conversation_id
                    assert phase["calls"] == 2
                    assert Path(phase["target"]).read_text() == "repaired\n"
                    print(
                        "PASS: native remote worktree, implementation, resumed repair, read-only reviewer, controller receipt"
                    )
                except Exception:
                    print((root / "server.log").read_text()[-12000:])
                    raise
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
        finally:
            harness.CURRENT.reset(token)
            model.shutdown()
            model.server_close()


if __name__ == "__main__":
    main()
