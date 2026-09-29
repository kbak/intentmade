"""Startup selection and image transfer, with all Docker operations mocked."""

import io
import json
import runpy
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

DAEMON_IMAGE = (
    "docker:29.4.1-dind@sha256:c77e5d7912f9b137cc67051fdc2991d8f5ae22c55ddf532bb836dcb693a04940"
)


class ConfigurationTests(unittest.TestCase):
    def projects(self, *, require_approval, label):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            (config / "repositories").mkdir()
            (config / "defaults.json").write_text(
                json.dumps({"enabled": True, "issue_label": label})
            )
            (config / "repositories/example.json").write_text('{"repository":"example/repo"}')
            (config / "deployment.json").write_text(
                json.dumps(
                    {
                        "worker_runtime": {"backend": "docker-sandboxes", "kit": "/operator/kit"},
                        "authorization": {"require_issue_approval": require_approval},
                    }
                )
            )
            compose = {
                "services": {
                    "canvas": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": "/tmp/factory/workspaces",
                                "target": "/workspaces",
                            },
                            {
                                "type": "bind",
                                "source": str(config),
                                "target": "/opt/factory/config",
                            },
                        ]
                    }
                }
            }
            with (
                patch("sys.argv", ["factoryctl", "projects"]),
                patch("subprocess.check_output", return_value=json.dumps(compose)),
                redirect_stdout(io.StringIO()) as output,
            ):
                runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/factoryctl"))
            return json.loads(output.getvalue())

    def test_host_project_listing_excludes_operator_settings(self):
        self.assertEqual(
            self.projects(require_approval=True, label="factory:approved"),
            {
                "example": {
                    "enabled": True,
                    "issue_label": "factory:approved",
                    "repository": "example/repo",
                }
            },
        )

    def test_host_project_loading_rejects_unapproved_issue_intake(self):
        with self.assertRaisesRegex(ValueError, "requires issue approval"):
            self.projects(require_approval=True, label=None)


class StartupTests(unittest.TestCase):
    def start(self, image, missing=False, transfer_exit=0, daemon_cached=True, pull_failed=False):
        deployment = {
            "services": {
                "canvas": {
                    "image": image,
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/tmp/factory/workspaces",
                            "target": "/workspaces",
                        },
                        {"type": "bind", "source": "/tmp/config", "target": "/opt/factory/config"},
                    ],
                },
                "sandboxes": {"image": DAEMON_IMAGE},
            }
        }
        source = Mock()
        source.wait.return_value = transfer_exit
        with (
            patch("sys.argv", ["factoryctl", "up"]),
            patch("subprocess.check_output", return_value=json.dumps(deployment)),
            patch("subprocess.Popen") as popen,
            patch("subprocess.run") as run,
        ):
            self.run = run
            self.popen = popen
            popen.return_value.__enter__.return_value = source

            def invoke(args, **kwargs):
                if (
                    missing
                    or (not daemon_cached and args[-3:] == ("image", "inspect", DAEMON_IMAGE))
                    or (pull_failed and args[-2:] == ("pull", DAEMON_IMAGE))
                ):
                    raise subprocess.CalledProcessError(1, args)

            run.side_effect = invoke
            runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/factoryctl"))
        self.commands = [call.args[0] for call in run.call_args_list]
        popen.assert_called_once_with(["docker", "save", image], stdout=subprocess.PIPE)
        load = next(c for c in run.call_args_list if c.args[0][-2:] == ("docker", "load"))
        self.assertIs(load.kwargs["stdin"], source.stdout)
        self.assertIn("--no-build", self.commands[-1])

    def test_default_image_is_built_and_transferred(self):
        self.start("intentmade:dev")
        self.assertEqual(self.commands[0], ("docker", "compose", "build", "canvas"))

    def test_supplied_runtime_is_preserved_and_transferred(self):
        self.start("intentmade:traceability")
        self.assertEqual(
            self.commands[0], ("docker", "image", "inspect", "intentmade:traceability")
        )
        self.assertFalse(any("build" in c for c in self.commands))

    def test_missing_supplied_image_fails_before_startup(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.start("missing:runtime", missing=True)
        self.assertEqual(self.run.call_count, 1)
        self.popen.assert_not_called()

    def test_failed_transfer_does_not_start_canvas(self):
        with self.assertRaisesRegex(RuntimeError, "transfer"):
            self.start("custom:runtime", transfer_exit=1)
        self.assertFalse(any("--remove-orphans" in c.args[0] for c in self.run.call_args_list))

    def test_cached_daemon_digest_needs_no_registry_access(self):
        self.start("custom:runtime")
        self.assertFalse(any("pull" in c for c in self.commands))
        self.assertTrue(any(c[-3:] == ("image", "inspect", DAEMON_IMAGE) for c in self.commands))

    def test_missing_daemon_digest_is_pulled_before_starting_canvas(self):
        self.start("custom:runtime", daemon_cached=False)
        pull = next(i for i, c in enumerate(self.commands) if c[-2:] == ("pull", DAEMON_IMAGE))
        self.assertLess(pull, len(self.commands) - 1)

    def test_daemon_pull_failure_prevents_canvas_start_and_image_transfer(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.start("custom:runtime", daemon_cached=False, pull_failed=True)
        self.popen.assert_not_called()
        self.assertFalse(any("--remove-orphans" in c.args[0] for c in self.run.call_args_list))
