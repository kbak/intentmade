"""Native command environments can omit the parent's credential variables."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common
import httpx
import reporting


class NativeCredentialsTests(unittest.TestCase):
    def test_phase_and_completion_auth_survive_filtered_env_and_worker_key_changes(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={})

        client = httpx.Client
        with tempfile.TemporaryDirectory() as temp:
            secret = Path(temp) / "canvas-key"
            secret.write_text("parent-key\n")
            with (
                patch.dict(os.environ, {"AUTOMATION_RUN_ID": "run"}, clear=True),
                patch.object(common, "Path", return_value=secret),
                patch.object(
                    common.httpx,
                    "Client",
                    side_effect=lambda **kwargs: client(
                        transport=httpx.MockTransport(respond), **kwargs
                    ),
                ),
            ):
                with reporting.run_report():
                    reporting.phase("Starting scan")
                    with patch.dict(os.environ, {"OH_SESSION_API_KEYS_0": "worker-key"}):
                        reporting.phase("Checking worker")
                    reporting.outcome("SKIPPED", "No eligible work")
            self.assertEqual(len(requests), 3)
            self.assertEqual(
                [request.headers["X-Session-API-Key"] for request in requests],
                ["parent-key"] * 3,
            )
            self.assertEqual(requests[-1].url.path, "/api/automation/v1/runs/run/complete")
            self.assertEqual(json.loads(requests[-1].content)["status"], "SKIPPED")
            self.assertTrue(all(b"parent-key" not in request.content for request in requests))

    def test_explicit_session_keys_take_precedence_over_parent_mount(self):
        for environment, expected in (
            ({"OH_SESSION_API_KEYS_0": "worker", "SESSION_API_KEY": "legacy"}, "worker"),
            ({"SESSION_API_KEY": "native"}, "native"),
        ):
            with (
                self.subTest(environment=list(environment)),
                patch.dict(os.environ, environment, clear=True),
                patch.object(common, "Path") as path,
            ):
                self.assertEqual(common.session_api_key(), expected)
                path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
