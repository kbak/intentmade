"""Configuration authority and failure cleanup at the native runtime boundary."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import docker_sandboxes as runtime
from common import projects


class RuntimeConfigTests(unittest.TestCase):
    def test_native_image_identity_is_bound_to_sandbox_and_excludes_secrets(self):
        digest = "sha256:" + "a" * 64
        inspect = Mock(
            return_value=json.dumps(
                {"name": "worker", "image_digest": digest, "secrets": [{"value": "DO_NOT_RETAIN"}]}
            )
        )
        result = runtime.image_observation(inspect, "worker")
        self.assertEqual(result["image_id"], digest)
        self.assertEqual(result["sandbox_name"], "worker")
        self.assertIsNone(result["daemon_id"])
        self.assertNotIn("DO_NOT_RETAIN", json.dumps(result))
        inspect.assert_called_once_with("inspect", "worker", "--json")

    def test_unknown_or_wrong_native_identity_is_not_replaced_by_a_tag(self):
        for record in (
            {"name": "other", "image_digest": "sha256:" + "a" * 64},
            {"name": "worker", "image": "known:tag"},
            {"name": "worker", "image_digest": None},
        ):
            with self.subTest(record=record):
                result = runtime.image_observation(Mock(return_value=json.dumps(record)), "worker")
                self.assertIsNone(result["image_id"])
                self.assertIsNotNone(result["error_type"])

    def test_optional_backend_and_invalid_configuration(self):
        self.assertEqual(runtime.validate({}), {"backend": "docker"})
        for value in (
            None,
            {"backend": "cloud"},
            {"backend": "docker", "kit": "/kit"},
            {"backend": "docker-sandboxes", "kit": "./repo-kit"},
            {"backend": "docker-sandboxes", "kit": "/kit", "publish_host": "0.0.0.0"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.validate(value)

    def test_repository_cannot_select_runtime_or_carry_operator_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "repositories").mkdir()
            (root / "defaults.json").write_text(
                json.dumps(
                    {
                        "worker_runtime": {"backend": "docker-sandboxes", "kit": "/trusted/kit"},
                        "repository": None,
                        "test_command": "true",
                        "required_checks": ["tests"],
                    }
                )
            )
            registration = root / "repositories/fixture.json"
            registration.write_text("{}")
            self.assertNotIn("worker_runtime", projects(root)["fixture"])
            registration.write_text('{"worker_runtime":{"backend":"docker"}}')
            with self.assertRaisesRegex(ValueError, "factory-wide worker_runtime"):
                projects(root)

    def test_native_read_only_paths_and_quoted_aliases(self):
        paths, aliases = runtime.mount_arguments(
            [
                "/jobs/a:/jobs/a",
                "/inputs/frozen:/factory-inputs:ro",
                "/profiles/web:/factory-tests/web:ro",
            ],
            {"profiles": "/trusted profiles"},
        )
        self.assertEqual(paths, ["/jobs/a", "/inputs/frozen:ro", "/trusted profiles/web:ro"])
        self.assertEqual(
            aliases,
            [
                ("/inputs/frozen", "/factory-inputs"),
                ("/trusted profiles/web", "/factory-tests/web"),
            ],
        )


class RuntimeLifecycleTests(unittest.TestCase):
    def exercise(
        self,
        status=200,
        create_failure=False,
        body_failure=False,
        cleanup_failure=False,
        partial_create=False,
    ):
        self.commands = []
        self.session = Mock()
        self.session.poll.return_value = None
        workspace = Mock()
        workspace.client.get.return_value.status_code = status

        def invoke(command, **kwargs):
            self.commands.append(command)
            action = command[1]
            code = int(
                (action == "create" and create_failure) or (action == "rm" and cleanup_failure)
            )
            output = (
                json.dumps([{"sandbox_port": 8000, "host_port": 34567}])
                if action == "ports"
                else ""
            )
            if action == "ls":
                created = [{"name": self.commands[0][3]}] if partial_create else []
                output = json.dumps({"sandboxes": created})
            return subprocess.CompletedProcess(command, code, output, "fixture failure")

        with (
            patch.object(runtime.subprocess, "run", side_effect=invoke),
            patch.object(runtime.subprocess, "Popen", return_value=self.session) as spawn,
            patch("openhands.sdk.workspace.RemoteWorkspace") as remote,
            patch("httpx.get", return_value=Mock(status_code=status)),
            patch.dict(os.environ, {"OH_SECRET_KEY": "controller-only"}),
        ):
            remote.return_value.__enter__.return_value = workspace
            with runtime.worker(
                Path("/job"),
                ["/job:/job"],
                {"OH_SESSION_API_KEYS_0": "worker-only"},
                {"kit": "/kit"},
            ):
                self.assertNotIn("worker-only", spawn.call_args.args[0])
                self.assertNotIn("OH_SECRET_KEY", spawn.call_args.args[0])
                self.assertNotIn("--cloud", self.commands[0])
                self.assertIn("off", self.commands[0])
                if body_failure:
                    raise LookupError("workflow failed")

    def test_session_is_held_until_vm_deletion(self):
        self.exercise()
        self.assertEqual(self.commands[-1][1:3], ["rm", "--force"])
        self.session.wait.assert_called_once_with(timeout=10)

    def test_failed_create_attempts_cleanup_without_starting_worker(self):
        with self.assertRaisesRegex(RuntimeError, "sbx create failed"):
            self.exercise(create_failure=True)
        self.assertEqual([c[1] for c in self.commands], ["create", "ls"])
        self.session.wait.assert_not_called()

    def test_bad_worker_authentication_fails_before_yield_and_removes_vm(self):
        with self.assertRaisesRegex(RuntimeError, "authentication failed"):
            self.exercise(status=401)
        self.assertEqual(self.commands[-1][1], "rm")

    def test_partial_creation_failure_removes_only_the_new_vm(self):
        with self.assertRaisesRegex(RuntimeError, "sbx create failed"):
            self.exercise(create_failure=True, partial_create=True)
        self.assertEqual([c[1] for c in self.commands], ["create", "ls", "rm"])
        self.assertEqual(self.commands[-1][-1], self.commands[0][3])

    def test_workflow_failure_removes_vm_and_propagates(self):
        with self.assertRaisesRegex(LookupError, "workflow failed"):
            self.exercise(body_failure=True)
        self.assertEqual(self.commands[-1][1], "rm")

    def test_cleanup_failure_blocks_success_but_reaps_cli_session(self):
        with self.assertRaisesRegex(RuntimeError, "sbx rm failed"):
            self.exercise(cleanup_failure=True)
        self.session.wait.assert_called_once_with(timeout=10)


if __name__ == "__main__":
    unittest.main()
