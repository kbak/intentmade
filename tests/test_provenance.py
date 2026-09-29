"""Execution observations must not borrow identities from another boundary."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import provenance


class ProvenanceTests(unittest.TestCase):
    def test_native_runtime_identity_does_not_inspect_a_host_docker_container(self):
        image = {
            "backend": "docker-sandboxes",
            "sandbox_name": "worker",
            "image_id": "sha256:" + "a" * 64,
            "daemon_id": None,
        }
        workspace = SimpleNamespace(_factory_image_observation=image)
        with patch.object(provenance, "docker_json") as inspect:
            record = provenance.capture(workspace)
        inspect.assert_not_called()
        provenance.required(record, {"observed.worker_image.image_id": image["image_id"]})
        with self.assertRaisesRegex(RuntimeError, "Required execution identity"):
            provenance.required(record, {"observed.worker_image.daemon_id": True})

    def test_image_comes_from_container_not_retargeted_tag_or_host(self):
        calls = []

        def inspect(args, template):
            calls.append(args)
            if args[:2] == ["container", "inspect"]:
                return "sha256:actual-worker"
            if args[:2] == ["image", "inspect"]:
                self.assertEqual(args[2], "sha256:actual-worker")
                return {
                    "id": args[2],
                    "digests": [],
                    "os": "linux",
                    "architecture": "amd64",
                    "layers": ["layer"],
                }
            return "nested-daemon"

        workspace = SimpleNamespace(
            _container_id="running-container",
            execute_command=lambda *a, **k: SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {"python": "different-worker-python", "sqlite": "different-worker-sqlite"}
                ),
            ),
        )
        with patch.object(provenance, "docker_json", side_effect=inspect):
            manifest = provenance.capture(workspace, intended={"worker_image": "mutable:tag"})
        self.assertEqual(manifest["observed"]["worker_image"]["image_id"], "sha256:actual-worker")
        self.assertEqual(manifest["observed"]["worker_image"]["daemon_id"], "nested-daemon")
        self.assertEqual(manifest["intended"]["worker_image"], "mutable:tag")
        self.assertNotEqual(
            manifest["observed"]["controller_runtime"]["python"],
            manifest["observed"]["worker_runtime"]["python"],
        )
        self.assertIsNone(manifest["observed"]["host_image"])
        self.assertFalse(any("mutable:tag" in args for args in calls))

    def test_unknown_required_identity_blocks_without_fallback_to_intent(self):
        with patch.object(provenance, "docker_json", side_effect=OSError("daemon unavailable")):
            manifest = provenance.capture(
                SimpleNamespace(_container_id="container"), intended={"worker_image": "known:tag"}
            )
        self.assertIsNone(manifest["observed"]["worker_image"]["image_id"])
        provenance.required(manifest, {})
        with self.assertRaisesRegex(RuntimeError, "Required execution identity"):
            provenance.required(manifest, {"observed.worker_image.image_id": True})
        with self.assertRaises(ValueError):
            provenance.required(manifest, {"intended.worker_image": "known:tag"})

    def test_python_wrapper_records_the_process_that_runs_the_script_and_keeps_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "case.py"
            output = root / "observed.json"
            manifest = root / "manifest.json"
            script.write_text(
                "import json, platform, sqlite3, sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'executable':sys.executable}))\nraise SystemExit(7)\n"
            )
            command = [
                sys.executable,
                "-m",
                "provenance",
                "python",
                "--out",
                str(manifest),
                "--",
                str(script),
                str(output),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            record = json.loads(manifest.read_text())
            actual = json.loads(output.read_text())
            self.assertEqual(record["observed"]["application_runtime"]["python"], actual["python"])
            self.assertEqual(record["observed"]["application_runtime"]["sqlite"], actual["sqlite"])
            self.assertEqual(
                record["observed"]["application_runtime"]["python_executable"], actual["executable"]
            )
            original = manifest.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(manifest.read_bytes(), original)

    def test_requirement_failure_retains_manifest_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            requirement = root / "require.json"
            manifest = root / "manifest.json"
            sentinel = root / "executed"
            requirement.write_text(
                json.dumps({"observed.application_runtime.sqlite": "unsupported-fixture"})
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "provenance",
                    "python",
                    "--out",
                    str(manifest),
                    "--require",
                    str(requirement),
                    "--",
                    str(sentinel),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Required execution identity", result.stderr)
            self.assertEqual(
                json.loads(manifest.read_text())["observed"]["application_runtime"]["sqlite"],
                sqlite3.sqlite_version,
            )
            self.assertFalse(sentinel.exists())

    def test_allowlisted_capture_never_serializes_environment_secrets(self):
        with patch.dict(
            os.environ, {"SECRET_FIXTURE": "NEVER_CAPTURE_THIS", "GH_TOKEN": "TOKEN_SENTINEL"}
        ):
            record = provenance.boundary()
        text = json.dumps(record)
        self.assertNotIn("NEVER_CAPTURE_THIS", text)
        self.assertNotIn("TOKEN_SENTINEL", text)
        self.assertIsNone(record["worker_manifest"])
        self.assertIsNone(record["source"]["head"])

    def test_source_binding_changes_with_dirty_contents_and_keeps_worker_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            file = repo / "code.py"
            file.write_text("first")
            worker = root / "worker.json"
            worker.write_text(json.dumps({"id": "worker-run"}))
            with patch.dict(os.environ, {"FACTORY_EXECUTION_MANIFEST": str(worker)}):
                before = provenance.boundary(repo)
                file.write_text("second")
                after = provenance.boundary(repo)
            self.assertNotEqual(
                before["source"]["worktree_sha256"], after["source"]["worktree_sha256"]
            )
            self.assertEqual(after["worker_manifest"]["id"], "worker-run")
            self.assertEqual(after["worker_manifest"]["sha256"], provenance.sha256(worker))


if __name__ == "__main__":
    unittest.main()
