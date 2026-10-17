"""Offline failure/cancellation probe for the actual optional managed ACP transport.

Run in a disposable standalone optional-image native sandbox. Provider responses
are scripted on loopback; no subscription request or login change occurs. The
caller-deadline case explicitly cancels after its wait expires. The surrounding
factory's own deadline and VM cleanup need a separate native worker exercise.
"""

import json
import os
import select
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COMMAND = "/opt/factory/opengauss-codex-acp"
RESULTS = []


class ACP:
    def __init__(self, root, home):
        self.log = tempfile.TemporaryFile(mode="w+")
        env = {**os.environ, "CODEX_HOME": str(home), "INITIAL_AGENT_MODE": "agent-full-access"}
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
            env.pop(key, None)
        self.process = subprocess.Popen(
            [COMMAND],
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            start_new_session=True,
        )
        self.buffer = b""
        self.index = 0
        self.events = []
        self.descendants = set()

    def send(self, method, params, notification=False):
        self.index += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            message["id"] = self.index
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()
        return self.index

    def read(self, identifier, timeout=45):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if b"\n" not in self.buffer:
                if not select.select(
                    [self.process.stdout], [], [], min(0.2, max(0.001, until - time.monotonic()))
                )[0]:
                    continue
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    break
                self.buffer += data
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                event = json.loads(line)
                self.events.append(event)
                if event.get("id") == identifier:
                    return event
        raise TimeoutError("ACP response deadline exceeded")

    def request(self, method, params, timeout=45):
        return self.read(self.send(method, params), timeout)

    def snapshot(self):
        parents = {}
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    stat = (entry / "stat").read_text()
                    tail = stat[stat.rindex(")") + 2 :].split()
                    parents[int(entry.name)] = int(tail[1])
                except (OSError, ValueError, IndexError):
                    pass
        current = {self.process.pid}
        while True:
            new = {pid for pid, parent in parents.items() if parent in current}
            if new <= current:
                break
            current |= new
        self.descendants |= current

    def close(self):
        self.snapshot()
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        alive = []
        while time.monotonic() < deadline:
            alive = []
            for pid in self.descendants:
                try:
                    stat = Path(f"/proc/{pid}/stat").read_text()
                    state = stat[stat.rindex(")") + 2 :].split()[0]
                    if state not in ("Z", "X"):
                        alive.append(pid)
                except FileNotFoundError:
                    pass
            if not alive:
                break
            time.sleep(0.1)
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.seek(0)
        self.diagnostics = self.log.read()[-5000:]
        self.log.close()
        assert not alive, ("Live ACP/MCP descendants after cleanup", alive)


def scenario(kind):
    received = threading.Event()
    release = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            received.set()
            if kind in ("cancel", "deadline"):
                release.wait(15)
            status = 401 if kind == "missing-auth" else 503
            payload = json.dumps(
                {
                    "error": {
                        "message": "Controlled qualification failure",
                        "type": "qualification_fixture",
                        "code": "qualification_fixture",
                    }
                }
            ).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    client = None
    try:
        with tempfile.TemporaryDirectory(prefix="opengauss-lifecycle-" + kind + "-") as temp:
            root = Path(temp)
            project = root / "source"
            home = root / "codex"
            project.mkdir()
            home.mkdir()
            (project / "lakefile.toml").write_text('name="qualification"\nversion="0.1.0"\n')
            (project / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
            (project / "Probe.lean").write_text("example : 1 + 1 = 2 := rfl\n")
            config = (
                'model="gpt-6-astra"\nmodel_reasoning_effort="xhigh"\nmodel_provider="fixture"\n[model_providers.fixture]\nname="Controlled offline failure"\nbase_url="http://127.0.0.1:'
                + str(server.server_port)
                + '/v1"\nwire_api="responses"\nrequest_max_retries=0\nstream_max_retries=0\nrequires_openai_auth='
                + ("true" if kind == "missing-auth" else "false")
                + "\n"
            )
            (home / "config.toml").write_text(config)
            if kind != "missing-auth":
                (home / "auth.json").write_text('{"OPENAI_API_KEY":"qualification-fixture"}\n')
            client = ACP(project, home)
            assert "error" not in client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "opengauss-lifecycle-probe", "version": "1"},
                },
            )
            new = client.request("session/new", {"cwd": str(project), "mcpServers": []})
            if kind == "missing-auth" and "error" in new:
                assert new["error"]["message"] == "Authentication required", new
                assert not requests, "Missing authentication reached the backend"
                outcome = json.dumps(new)
            else:
                assert "error" not in new, new
                session = new["result"]["sessionId"]
                prompt = client.send(
                    "session/prompt",
                    {
                        "sessionId": session,
                        "prompt": [
                            {
                                "type": "text",
                                "text": "Controlled offline runtime failure exercise. Do not edit files or use tools.",
                            }
                        ],
                    },
                )
                if kind in ("cancel", "deadline"):
                    assert received.wait(30), "Scripted backend was not reached"
                    client.snapshot()
                    if kind == "deadline":
                        try:
                            client.read(prompt, timeout=1)
                        except TimeoutError:
                            pass
                        else:
                            raise AssertionError(
                                "Stalled request did not exceed the bounded caller deadline"
                            )
                    client.send("session/cancel", {"sessionId": session}, notification=True)
                    response = client.read(prompt, timeout=15)
                    assert response.get("result", {}).get("stopReason") == "cancelled", response
                    outcome = (
                        "cancelled"
                        if kind == "cancel"
                        else "deadline observed; cancellation acknowledged"
                    )
                else:
                    response = client.read(prompt, timeout=30)
                    outcome = json.dumps(client.events)
                    expected = "401" if kind == "missing-auth" else "503"
                    assert (
                        expected in outcome
                        or kind == "missing-auth"
                        and any(s in outcome.lower() for s in ("auth", "login", "credential"))
                    ), outcome[-2000:]
                    assert not any('"text": "Done' in json.dumps(e) for e in client.events)
            if kind == "missing-auth":
                assert not (home / "auth.json").exists(), "Missing login silently restaged"
            assert (home / "config.toml").read_text() == config, (
                "Selected fixture configuration changed"
            )
            try:
                client.close()
            finally:
                client = None
            result = {
                "scenario": kind,
                "status": "passed",
                "seconds": round(time.monotonic() - started, 3),
                "requests": len(requests),
                "outcome": outcome[-2000:],
            }
            RESULTS.append(result)
            print(json.dumps(result), flush=True)
    finally:
        release.set()
        if client:
            client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    for kind in ("backend", "missing-auth", "cancel", "deadline"):
        scenario(kind)
    print(json.dumps({"status": "passed", "scenarios": RESULTS}), flush=True)


if __name__ == "__main__":
    main()
