"""Real offline ACP regression for repository startup code and session lifecycles.

Uses a fresh native credential home, as disposable factory workers do. Only
trusted home settings and explicit ACP MCP servers may supply startup code.
"""

import http.server
import json
import os
import select
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

MCP = """import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text("started")
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        result = {"protocolVersion":"2024-11-05","capabilities":{"tools":{}},
                  "serverInfo":{"name":"fixture","version":"1"}}
    elif msg["method"] == "tools/list":
        result = {"tools":[]}
    else:
        result = {}
    print(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":result}), flush=True)
"""


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        item = {
            "id": "fixture-message",
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "Done."}],
        }
        events = [
            {"type": "response.created", "response": {"id": "fixture", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {
                "type": "response.output_text.delta",
                "item_id": item["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": "Done.",
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
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def adapter(cwd, home):
    with tempfile.TemporaryFile(mode="w+") as errors:
        process = subprocess.Popen(
            ["/opt/factory/codex-acp"],
            cwd=cwd,
            env={
                **os.environ,
                "CODEX_HOME": str(home),
                "INITIAL_AGENT_MODE": "read-only",
                # The factory launcher must enforce explicit MCP precedence.
                "DISABLE_MCP_CONFIG_FILTERING": "false",
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            start_new_session=True,
        )
        pending = b""
        next_id = 0

        def request(method, params):
            nonlocal pending, next_id
            next_id += 1
            process.stdin.write(
                (
                    json.dumps(
                        {"jsonrpc": "2.0", "id": next_id, "method": method, "params": params}
                    )
                    + "\n"
                ).encode()
            )
            process.stdin.flush()
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if b"\n" not in pending:
                    if not select.select([process.stdout], [], [], 0.2)[0]:
                        continue
                    data = os.read(process.stdout.fileno(), 65536)
                    if not data:
                        break
                    pending += data
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    response = json.loads(line)
                    if response.get("id") == next_id:
                        assert "error" not in response, (method, response)
                        return response["result"]
            errors.seek(0)
            raise RuntimeError(f"{method} failed: {errors.read()[-4000:]}")

        try:
            request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "security-fixture", "version": "1"},
                },
            )
            yield request
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            # The adapter can exit before its native app-server and MCP children.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.stdin.close()
            process.stdout.close()


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for kind in ("clean", "clone", "archive", "nested", "symlink", "additional"):
            with tempfile.TemporaryDirectory(prefix="factory-config-test-") as temp:
                root = Path(temp)
                repo, home = root / "repository", root / "home"
                repo.mkdir()
                home.mkdir()
                if kind != "archive":
                    subprocess.run(["git", "init", "-q", str(repo)], check=True)
                (home / "config.toml").write_text(
                    'model="gpt-6-astra"\nmodel_provider="fixture"\n'
                    '[model_providers.fixture]\nname="Offline fixture"\n'
                    f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
                    'wire_api="responses"\nrequires_openai_auth=false\n'
                )
                script = root / "mcp.py"
                script.write_text(MCP)
                bad, good = root / "repository-started", root / "factory-started"
                (repo / ".codex").mkdir()
                config = repo / ".codex/config.toml"
                if kind != "clean":
                    config.write_text(
                        '[mcp_servers.playwright]\ncommand="python"\n'
                        f"args={json.dumps([str(script), str(bad)])}\n"
                    )
                cwd = repo
                extra = {}
                if kind == "nested":
                    cwd = repo / "src"
                    cwd.mkdir()
                elif kind == "symlink":
                    cwd = root / "linked"
                    cwd.symlink_to(repo, target_is_directory=True)
                elif kind == "additional":
                    cwd = root / "working"
                    cwd.mkdir()
                    extra = {"_meta": {"additionalRoots": [str(repo)]}}
                params = {"cwd": str(cwd), "mcpServers": [], **extra}
                with adapter(cwd, home) as request:
                    result = request("session/new", params)
                    assert result["modes"]["currentModeId"] == "read-only", result
                    session = result["sessionId"]
                    if kind == "clone":
                        # Persist a real turn to exercise reload after app-server restart.
                        request(
                            "session/prompt",
                            {
                                "sessionId": session,
                                "prompt": [{"type": "text", "text": "Say Done."}],
                            },
                        )
                    time.sleep(3)
                    assert not bad.exists(), f"{kind}: repository MCP started"
                    # A same-name factory server must not be suppressed by an ignored layer.
                    explicit = {
                        **params,
                        "mcpServers": [
                            {
                                "name": "playwright",
                                "command": "python",
                                "args": [str(script), str(good)],
                                "env": [],
                            }
                        ],
                    }
                    request("session/new", explicit)
                    deadline = time.monotonic() + 5
                    while not good.exists() and time.monotonic() < deadline:
                        time.sleep(0.1)
                    assert good.exists(), f"{kind}: explicit factory MCP did not start"
                    assert not bad.exists(), f"{kind}: repository MCP replaced factory server"
                if kind == "clone":
                    for method in ("session/load", "session/resume", "session/fork"):
                        with adapter(cwd, home) as request:
                            request(method, {**params, "sessionId": session})
                            time.sleep(2)
                            assert not bad.exists(), f"{method}: repository MCP started"
                        print(f"PASS: {method} after process restart", flush=True)
                if kind != "clean":
                    assert config.is_file(), "Review source was removed"
                print(
                    f"PASS: {kind} project config ignored; explicit factory MCP starts", flush=True
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
