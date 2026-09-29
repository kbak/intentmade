"""Exercise DockerWorkspace credential copies; optional disposable-account refresh."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import sandbox
from agent import converse
from common import DATA, ROOT
from openhands.agent_server.persistence import FileSecretsStore
from openhands.sdk.utils.cipher import Cipher

NAME = "CODEX_AUTH_JSON"


def fixture_cases():
    # The parent store is outside the job mount. Only fixture values cross the API.
    with tempfile.TemporaryDirectory() as temporary:
        parent = FileSecretsStore(temporary, Cipher(secrets.token_urlsafe(32)))
        for outcome in ("refresh", "logout", "new-login"):
            original = json.dumps({"OPENAI_API_KEY": "fixture-original"})
            refreshed = json.dumps({"OPENAI_API_KEY": "fixture-refreshed"})
            newer = json.dumps({"OPENAI_API_KEY": "fixture-new-login"})
            parent.set_secret(NAME, original)
            with tempfile.TemporaryDirectory(dir=DATA, prefix="credential-check-") as job:
                root = Path(job)
                (root / "source").mkdir()
                with (
                    patch.object(sandbox, "FileSecretsStore", return_value=parent),
                    patch.object(sandbox, "api", return_value={"profile": {}}),
                    sandbox.worker(root, {}) as workspace,
                ):
                    delivered = workspace.client.get("/api/settings/secrets/" + NAME)
                    delivered.raise_for_status()
                    assert delivered.text == original
                    workspace.client.put(
                        "/api/settings/secrets", json={"name": NAME, "value": refreshed}
                    ).raise_for_status()
                    if outcome == "logout":
                        parent.delete_secret(NAME)
                    elif outcome == "new-login":
                        parent.set_secret(NAME, newer)
                expected = {"refresh": refreshed, "logout": None, "new-login": newer}[outcome]
                assert parent.get_secret(NAME) == expected
                print("PASS real worker credential " + outcome, flush=True)


def live_refresh():
    # Run only against a disposable Canvas with a separate login and no active jobs.
    # Never restore the old tokens: a successful provider refresh may rotate them.
    key = os.environ.get("OH_SECRET_KEY") or Path("/run/secrets/encryption-key").read_text().strip()
    parent = FileSecretsStore("/home/openhands/.openhands", Cipher(key))
    original, version = parent.load_versioned_secret(NAME)
    expired = json.loads(original)
    assert expired.get("auth_mode") == "chatgpt" and expired["tokens"]["refresh_token"]
    expired["last_refresh"] = "2000-01-01T00:00:00Z"
    parent.replace_versioned_secret(NAME, version, json.dumps(expired))
    _, expired_version = parent.load_versioned_secret(NAME)
    with tempfile.TemporaryDirectory(dir=DATA, prefix="oauth-check-") as job:
        root = Path(job)
        (root / "source").mkdir()
        with sandbox.worker(root, {}) as workspace:
            reply = converse(workspace, "Reply exactly ACCEPTANCE_OK. Do not use any tools.")
            assert "ACCEPTANCE_OK" in reply
        refreshed, current_version = parent.load_versioned_secret(NAME)
        current = json.loads(refreshed)
        assert current_version != expired_version
        assert current["last_refresh"] != expired["last_refresh"]
        assert current["tokens"]["access_token"] != expired["tokens"]["access_token"]
        print("PASS provider OAuth refresh persisted from VM to Canvas", flush=True)
        # Codex ACP credentials are separate from the native LLM subscription card.
        # Exercise the CLI's native secret deletion, including stale writeback.
        subprocess.run([sys.executable, str(ROOT / "configure.py"), "codex-logout"], check=True)
        assert parent.get_secret(NAME) is None
        sandbox.sync_credential(parent, current_version, refreshed, original)
        assert parent.get_secret(NAME) is None
        print("PASS native Canvas logout; stale worker cannot restore login", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-refresh-and-logout",
        action="store_true",
        help="Rotate and then log out this DISPOSABLE Canvas account; makes one model call",
    )
    args = parser.parse_args()
    if sandbox.docker_sandboxes.settings()["backend"] != "docker":
        parser.error("Use check_docker_sandboxes.py for native proxy credentials")
    fixture_cases()
    if args.live_refresh_and_logout:
        live_refresh()
