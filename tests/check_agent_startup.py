"""Exercise startup errors through the actual worker launcher and remote client.

Offline scripted ACP server: no account, model calls, or production state.
"""

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from agent import AgentStartupError, run_with_startup_recovery
from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.workspace import RemoteWorkspace

FAKE_ACP = """
import json, sys, time
from pathlib import Path
counter = Path(sys.argv[1])
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    response = {"jsonrpc": "2.0", "id": request["id"]}
    if request["method"] == "initialize":
        response["result"] = {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}
    elif request["method"] == "session/new":
        if sys.argv[2] == "timeout" and attempt == 1:
            time.sleep(15)
        response["error"] = {"code": -32000, "message": "Fixture login rejected"}
    else:
        raise RuntimeError("Unexpected work request: " + request["method"])
    print(json.dumps(response), flush=True)
"""


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fake = root / "fake-acp.py"
        fake.write_text(FAKE_ACP)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        key = "offline-startup-probe"
        env = {
            **os.environ,
            "OH_SESSION_API_KEYS_0": key,
            "OH_PERSISTENCE_DIR": str(root / "server"),
            "OH_CONVERSATIONS_PATH": str(root / "server/conversations"),
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen(
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
                url = f"http://127.0.0.1:{port}"
                with httpx.Client(base_url=url, headers={"X-Session-API-Key": key}) as client:
                    for _ in range(60):
                        try:
                            if client.get("/openapi.json").status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        if process.poll() is not None:
                            raise RuntimeError((root / "server.log").read_text()[-4000:])
                        time.sleep(0.5)
                    else:
                        raise RuntimeError("Agent server did not start")
                for scenario, attempts in (("auth", 1), ("timeout", 2)):
                    counter = root / f"{scenario}-attempts"
                    conversation = Conversation(
                        agent=ACPAgent(
                            acp_command=["python", str(fake), str(counter), scenario],
                            acp_startup_timeout=2,
                        ),
                        workspace=RemoteWorkspace(host=url, api_key=key, working_dir=str(root)),
                        visualizer=None,
                    )
                    try:
                        conversation.send_message("Do not perform any work; fixture startup only")
                        try:
                            run_with_startup_recovery(conversation, root / f"{scenario}.jsonl")
                        except AgentStartupError as exc:
                            assert exc.details["code"] == "ACPAuthRequired", exc.details
                            assert exc.details["attempt"] == attempts, exc.details
                        except Exception as exc:
                            print("Native startup error:", getattr(exc, "conversation_error", None))
                            print(
                                "Retained startup events:",
                                [e.model_dump(mode="json") for e in conversation.state.events],
                            )
                            raise
                        else:
                            raise AssertionError("Startup failure was lost")
                        assert int(counter.read_text()) == attempts
                    finally:
                        conversation.close()
                print(
                    "PASS: actual worker launcher, remote auth error, bounded timeout retry, no work replay"
                )
            except Exception:
                print((root / "server.log").read_text()[-12000:])
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
