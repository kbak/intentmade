"""Connect the Codex CLI account used by native ACP workers."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from common import api


def login():
    # Keep this login separate from the LLM subscription card and host accounts.
    # Only the native encrypted secret survives; temporary CLI files are removed.
    with tempfile.TemporaryDirectory(prefix="factory-codex-login-") as directory:
        env = {**os.environ, "CODEX_HOME": directory}
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_AUTH_JSON"):
            env.pop(key, None)
        subprocess.run(
            [
                "/acp-node/bin/codex",
                "-c",
                'cli_auth_credentials_store="file"',
                "login",
                "--device-auth",
            ],
            env=env,
            check=True,
            timeout=900,
        )
        value = (Path(directory) / "auth.json").read_text()
        auth = json.loads(value)
        tokens = auth.get("tokens") or {}
        if not all(
            isinstance(tokens.get(key), str) and tokens[key]
            for key in ("access_token", "refresh_token", "id_token")
        ):
            raise RuntimeError(
                "Codex did not save a complete subscription login; the factory login was not changed"
            )
        api("PUT", "/api/settings/secrets", json={"name": "CODEX_AUTH_JSON", "value": value})
    print("Factory Codex login saved. Retry the waiting task.", flush=True)


if __name__ == "__main__":
    login()
