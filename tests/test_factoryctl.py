"""Startup selection and image transfer, with all Docker operations mocked."""

import json
import runpy
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class StartupTests(unittest.TestCase):
    def start(self, image, missing=False, transfer_exit=0):
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
                }
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
            if missing:
                run.side_effect = subprocess.CalledProcessError(
                    1, ["docker", "image", "inspect", image]
                )
            runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/factoryctl"))
        self.commands = [call.args[0] for call in run.call_args_list]
        popen.assert_called_once_with(
            ["docker", "save", image, "docker:29.4.1-dind"], stdout=subprocess.PIPE
        )
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
