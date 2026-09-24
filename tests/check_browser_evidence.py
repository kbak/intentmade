"""Real Chromium/MCP -> native Agent Server download -> persisted image message.

Run in the test image with --network none. No model, login or production state.
"""

import asyncio
import base64
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import browser_qa
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openhands.sdk.agent import ACPAgent
from openhands.sdk.workspace import RemoteWorkspace


async def capture(root, url):
    config = browser_qa.mcp_config(root)["playwright"]
    env = {**os.environ, "PATH": "/acp-node/bin:" + os.environ["PATH"]}
    server = StdioServerParameters(command=config.command, args=config.args, env=env)
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        navigated = await session.call_tool("browser_navigate", {"url": url})
        assert not navigated.isError, navigated
        snapshot = max(root.glob("page-*.yml"), key=lambda path: path.stat().st_mtime).read_text()
        match = re.search(r'button "Retry" \[ref=([^\]]+)\]', snapshot)
        assert match, navigated
        clicked = await session.call_tool(
            "browser_click", {"target": match[1], "element": "Retry button"}
        )
        assert not clicked.isError, clicked
        snapshot = max(root.glob("page-*.yml"), key=lambda path: path.stat().st_mtime).read_text()
        assert "Recovered" in snapshot, snapshot
        image = await session.call_tool(
            "browser_take_screenshot",
            {"filename": str(root / "recovered.png"), "type": "png", "scale": "css"},
        )
        assert not image.isError, image
        assert (root / "recovered.png").is_file(), [
            c.text for c in image.content if c.type == "text"
        ]
        await session.call_tool("browser_close", {})


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        app = root / "app"
        app.mkdir()
        (app / "index.html").write_text("""<!doctype html><html><body>
          <h1>Factory browser QA</h1><p id="status">Failed request</p>
          <button onclick="document.querySelector('#status').textContent='Recovered'">Retry</button>
          </body></html>""")
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(app))
        )
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        worker = root / "worker"
        worker.mkdir()
        try:
            asyncio.run(capture(worker, f"http://127.0.0.1:{httpd.server_port}"))
        finally:
            httpd.shutdown()
            httpd.server_close()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        key = "isolated-browser-evidence-smoke"
        env = {
            **os.environ,
            "OH_SESSION_API_KEYS_0": key,
            "OH_PERSISTENCE_DIR": str(root / "server"),
            "OH_CONVERSATIONS_PATH": str(root / "server/conversations"),
            "OPENHANDS_SUPPRESS_BANNER": "1",
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
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}",
                    headers={"X-Session-API-Key": key},
                    timeout=15,
                ) as client:
                    for _ in range(60):
                        try:
                            response = client.get("/openapi.json")
                            if response.status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        if process.poll() is not None:
                            raise RuntimeError((root / "server.log").read_text()[-4000:])
                        time.sleep(0.5)
                    else:
                        raise RuntimeError("Isolated Agent Server did not start")
                    retained = root / "retained"
                    workspace = RemoteWorkspace(
                        host=str(client.base_url), api_key=key, working_dir=str(worker)
                    )
                    images = browser_qa.retain(
                        workspace,
                        worker,
                        retained,
                        [
                            browser_qa.Screenshot(
                                filename="recovered.png",
                                caption="Retry recovers",
                                url="http://local-fixture",
                            )
                        ],
                    )
                    shutil.rmtree(worker)
                    created = client.post(
                        "/api/conversations",
                        json={
                            "workspace": {"working_dir": str(retained)},
                            "agent": ACPAgent(
                                acp_command=["codex-acp"],
                                acp_server="codex",
                                acp_session_mode="read-only",
                            ).model_dump(mode="json"),
                        },
                    )
                    created.raise_for_status()
                    conversation = created.json()["id"]
                    image_url = (
                        "data:image/png;base64,"
                        + base64.b64encode(Path(images[0]["path"]).read_bytes()).decode()
                    )
                    response = client.post(
                        f"/api/conversations/{conversation}/events",
                        json={
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Factory browser evidence"},
                                {"type": "image", "image_urls": [image_url]},
                            ],
                            "run": False,
                        },
                    )
                    response.raise_for_status()
                    events = client.get(
                        f"/api/conversations/{conversation}/events/search", params={"limit": 100}
                    )
                    events.raise_for_status()
                    assert image_url in events.text, "Native image message was not persisted"
                    preview = client.get(f"/api/conversations/{conversation}/workspace/1.png")
                    preview.raise_for_status()
                    assert preview.headers["content-type"].startswith("image/png")
                    assert preview.content == Path(images[0]["path"]).read_bytes()
                    print(
                        "PASS: Chromium interaction, MCP screenshot, native remote download, worker deletion, persisted image message and workspace preview"
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
