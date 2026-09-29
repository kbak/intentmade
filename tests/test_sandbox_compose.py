"""Native controller wiring preserves Compose's UI and state boundaries."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "sandbox_compose", Path(__file__).resolve().parents[1] / "scripts/sandbox_compose.py"
)
sandbox_compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox_compose)

ORIGINAL = {
    "services": {
        "canvas": {
            "image": "factory:tested",
            "build": {"context": "."},
            "ports": [{"host_ip": "127.0.0.1", "published": "8000", "target": 8000}],
            "group_add": ["2375"],
            "environment": {"DOCKER_HOST": "unix:///run/factory-docker/docker.sock"},
            "volumes": [
                {
                    "type": "volume",
                    "source": "canvas-state",
                    "target": "/home/openhands/.openhands",
                },
                {"type": "volume", "source": "sandbox-control", "target": "/run/factory-docker"},
                {"type": "bind", "source": "/operator/workspaces", "target": "/workspaces"},
            ],
        },
        "sandboxes": {"privileged": True},
    },
    "volumes": {"canvas-state": {}, "sandbox-control": {}, "sandbox-images": {}},
}


class ComposeTests(unittest.TestCase):
    def render(self, host="192.168.20.1"):
        return sandbox_compose.render(
            ORIGINAL,
            {"kit": "/operator/kit", "profiles": "/operator/profiles", "publish_host": host},
            command="/usr/bin/sbx",
            socket="/run/operator/sbx.sock",
            auth_directory="/operator/sbx-auth",
            uid=1000,
            gid=1000,
        )

    def test_uses_native_socket_and_matching_paths_without_exposing_canvas(self):
        before = copy.deepcopy(ORIGINAL)
        result = self.render()
        self.assertEqual(ORIGINAL, before)
        self.assertEqual(set(result["services"]), {"canvas"})
        self.assertEqual(set(result["volumes"]), {"canvas-state"})
        canvas = result["services"]["canvas"]
        self.assertEqual(canvas["ports"], before["services"]["canvas"]["ports"])
        self.assertNotIn("network_mode", canvas)
        self.assertNotIn("DOCKER_HOST", canvas["environment"])
        mounts = {item["target"]: item for item in canvas["volumes"]}
        self.assertEqual(mounts["/home/openhands/.openhands"]["source"], "canvas-state")
        self.assertEqual(mounts["/operator/workspaces"]["source"], "/operator/workspaces")
        self.assertEqual(canvas["environment"]["FACTORY_DATA"], "/operator/workspaces")
        self.assertEqual(mounts["/run/sbx/sandboxd.sock"]["source"], "/run/operator/sbx.sock")
        self.assertTrue(mounts["/operator/kit"]["read_only"])
        self.assertTrue(mounts["/operator/profiles"]["read_only"])
        self.assertNotIn("/run/factory-docker", mounts)
        self.assertEqual(canvas["environment"]["XDG_CONFIG_HOME"], "/operator")
        for target in ("/operator/sbx-auth", "/operator/sbx", "/operator/sandboxes"):
            self.assertEqual(mounts[target]["source"], target)
        self.assertTrue(mounts["/operator/sbx"]["read_only"])
        self.assertFalse(mounts["/operator/sbx-auth"]["read_only"])
        self.assertNotIn("/operator", mounts)  # Never mount unrelated host configuration.

    def test_loopback_worker_address_is_rejected_before_controller_start(self):
        with self.assertRaisesRegex(ValueError, "bridge networking"):
            self.render("127.0.0.1")

    def test_missing_native_config_fails_before_rendering_a_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(sandbox_compose, "native_command", return_value="/usr/bin/sbx"),
                patch.object(
                    sandbox_compose.subprocess,
                    "run",
                    return_value=Mock(
                        stdout=json.dumps({"status": "running", "socket": "/native.sock"})
                    ),
                ),
                patch.dict("os.environ", {"XDG_CONFIG_HOME": temporary}),
                patch.object(sandbox_compose, "render") as render,
            ):
                for missing in ("com.docker.sandboxes", "sbx", "sandboxes"):
                    with self.assertRaisesRegex(
                        ValueError, "Native configuration directory missing"
                    ):
                        sandbox_compose.resolve(ORIGINAL, {})
                    render.assert_not_called()
                    (Path(temporary) / missing).mkdir()
                sandbox_compose.resolve(ORIGINAL, {})
                render.assert_called_once()


if __name__ == "__main__":
    unittest.main()
