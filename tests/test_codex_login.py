"""A dedicated device login updates only the native ACP credential on success."""

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_login


class CodexLoginTests(unittest.TestCase):
    def test_completed_login_saves_native_secret_and_removes_temporary_files(self):
        paths = []
        value = json.dumps({"tokens": {"access_token": "a", "refresh_token": "r", "id_token": "i"}})

        def complete(command, **kwargs):
            root = Path(kwargs["env"]["CODEX_HOME"])
            paths.append(root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertIn("--device-auth", command)
            (root / "auth.json").write_text(value)

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "unrelated"}),
            patch.object(codex_login.subprocess, "run", side_effect=complete),
            patch.object(codex_login, "api") as api,
        ):
            codex_login.login()
        api.assert_called_once_with(
            "PUT", "/api/settings/secrets", json={"name": "CODEX_AUTH_JSON", "value": value}
        )
        self.assertFalse(paths[0].exists())

    def test_cancelled_login_preserves_existing_native_credential(self):
        with (
            patch.object(
                codex_login.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "codex")
            ),
            patch.object(codex_login, "api") as api,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                codex_login.login()
        api.assert_not_called()

    def test_incomplete_login_is_not_saved(self):
        def incomplete(command, **kwargs):
            (Path(kwargs["env"]["CODEX_HOME"]) / "auth.json").write_text("{}")

        with (
            patch.object(codex_login.subprocess, "run", side_effect=incomplete),
            patch.object(codex_login, "api") as api,
        ):
            with self.assertRaisesRegex(RuntimeError, "complete subscription login"):
                codex_login.login()
        api.assert_not_called()
