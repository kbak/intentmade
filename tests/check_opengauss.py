"""Offline real Codex ACP -> pinned OpenGauss guidance -> Lean MCP probe.

Use the optional image, no network or real provider credentials. Mount a pinned
Lean toolchain and use --lean to also inspect a live goal through the actual MCP.
"""

import argparse
import asyncio
import io
import json
import os
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from check_project_config import Model, adapter


async def lean_probe(project):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command="/opt/factory/opengauss/venv/bin/lean-lsp-mcp",
        args=[],
        env={**os.environ, "LEAN_PROJECT_PATH": str(project)},
    )
    (project / "Probe.lean").write_text("theorem probe (n : Nat) : n + 0 = n := by\n  exact ?_\n")
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool("lean_goal", {"file_path": "Probe.lean", "line": 2})
            assert not result.isError, result
            assert "n + 0" in str(result), result
            (project / "Probe.lean").write_text(
                "theorem probe (n : Nat) : n + 0 = n := by\n  rfl\n"
            )
            diagnostics = await session.call_tool(
                "lean_diagnostic_messages", {"file_path": "Probe.lean"}
            )
            assert not diagnostics.isError, diagnostics
    subprocess.run(["lake", "env", "lean", "Probe.lean"], cwd=project, check=True, timeout=60)
    print("PASS: real Lean MCP goal, repaired proof and pinned Lean compilation", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean", action="store_true")
    args = parser.parse_args()
    calls = []

    class Endpoint(Model):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(json.loads(body))
            self.rfile = io.BytesIO(body)
            super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="opengauss-probe-") as temporary:
            root = Path(temporary)
            project, home = root / "worktree", root / "home"
            project.mkdir()
            home.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            (project / "lakefile.toml").write_text('name = "Probe"\nversion = "0.1.0"\n')
            (project / "lean-toolchain").write_text("leanprover/lean4:v4.35.0\n")
            (home / "config.toml").write_text(
                'model="gpt-5.3-codex"\nmodel_provider="fixture"\n'
                '[model_providers.fixture]\nname="Offline fixture"\n'
                f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n'
            )
            auth = home / "auth.json"
            auth.write_text('{"OPENAI_API_KEY":"fixture-not-a-real-key"}\n')
            before_auth = auth.read_bytes()
            before_config = (home / "config.toml").read_bytes()
            command = "/opt/factory/opengauss-codex-acp"
            params = {"cwd": str(project), "mcpServers": []}
            with adapter(root, home, command, "agent-full-access") as request:
                result = request("session/new", params)
                assert result["modes"]["currentModeId"] == "agent-full-access", result
                session = result["sessionId"]
                request(
                    "session/prompt",
                    {
                        "sessionId": session,
                        "prompt": [
                            {"type": "text", "text": "Factory accepted task fixture. Say Done."}
                        ],
                    },
                )
            assert calls, "Scripted model was not reached"
            for call in calls:
                context = json.dumps(call)
                assert "- lean4:" in context, "Pinned Lean skill missing from native skill catalog"
                for required in (
                    str(project),
                    "/lean4:autoprove",
                    "--max-cycles=1",
                    "IntentBond",
                    "Factory accepted task fixture",
                ):
                    assert required in context, required
                assert "lean_goal" in json.dumps(call.get("tools", {})), (
                    f"Lean MCP tools unavailable; request keys={list(call)}, model={call.get('model')}, choice={call.get('tool_choice')}"
                )
            print(
                "PASS: actual worktree, managed autonomous workflow, factory task and Lean MCP reach native ACP",
                flush=True,
            )
            for method in ("session/load", "session/resume", "session/fork"):
                with adapter(root, home, command, "agent-full-access") as request:
                    request(method, {**params, "sessionId": session})
                print("PASS: OpenGauss " + method + " after process restart", flush=True)
            assert auth.read_bytes() == before_auth
            assert (home / "config.toml").read_bytes() == before_config
            assert not (project / ".gauss").exists()
            assert not list(project.glob("**/instructions.md"))
            print(
                "PASS: existing authentication/configuration retained; generated context outside source",
                flush=True,
            )
            if args.lean:
                asyncio.run(lean_probe(project))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
