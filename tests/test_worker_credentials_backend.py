"""Credential authority at the shared boundary used by every factory worker."""

import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import sandbox


class WorkerCredentialTests(unittest.TestCase):
    def exercise(self, backend, failure=False):
        workspace = Mock()
        workspace.client.get.return_value.text = "refreshed-login"
        parent = Mock()
        parent.load_versioned_secret.return_value = ("canvas-login", 7)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(sandbox, "api", return_value={"profile": {"acp_model": "model/low"}}),
            patch.object(sandbox, "require_disk_space"),
            patch.object(sandbox, "FileSecretsStore", return_value=parent) as store,
            patch.object(sandbox, "Cipher") as cipher,
            patch.object(sandbox.docker_sandboxes, "settings", return_value={"backend": backend}),
            patch.object(sandbox.docker_sandboxes, "worker", return_value=nullcontext(workspace)),
            patch.object(sandbox, "docker_worker", return_value=nullcontext(workspace)),
            patch.object(sandbox.provenance, "capture", return_value={"id": "fixture"}),
            patch.object(sandbox.provenance, "required"),
            patch.object(sandbox.measurements, "CURRENT", Mock(get=Mock(return_value=None))),
            patch.dict(os.environ, {"OH_SECRET_KEY": "controller-only"}),
        ):
            if backend == "docker-sandboxes":
                os.environ.pop("OH_SECRET_KEY")
                store.side_effect = AssertionError("Native VM must not open Canvas secrets")
                cipher.side_effect = AssertionError("Native VM must not use Canvas encryption")
            try:
                with sandbox.worker(Path(directory), {}) as active:
                    self.assertIs(active, workspace)
                    self.assertEqual(os.environ["FACTORY_CODEX_MODEL"], "model/low")
                    if failure:
                        raise LookupError("workflow failed")
            except LookupError:
                if not failure:
                    raise
            self.assertNotIn("FACTORY_CODEX_MODEL", os.environ)
        return workspace, parent, store

    def test_native_success_and_failure_never_read_upload_or_sync_real_login(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                workspace, parent, store = self.exercise("docker-sandboxes", failure)
                store.assert_not_called()
                workspace.client.put.assert_not_called()
                workspace.client.get.assert_not_called()
                parent.replace_versioned_secret.assert_not_called()

    def test_docker_workspace_retains_native_openhands_login_and_refresh(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                workspace, parent, _ = self.exercise("docker", failure)
                workspace.client.put.assert_called_once_with(
                    "/api/settings/secrets",
                    json={"name": "CODEX_AUTH_JSON", "value": "canvas-login"},
                )
                parent.replace_versioned_secret.assert_called_once_with(
                    "CODEX_AUTH_JSON", 7, "refreshed-login"
                )
