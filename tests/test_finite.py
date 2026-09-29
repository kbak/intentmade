"""Finite scripts execute once; native callbacks and execution remain distinct."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import finite
import reporting


class FiniteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_id = str(uuid4())
        self.environment = patch.dict(os.environ, {"AUTOMATION_RUN_ID": self.run_id})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_recipe(self, source, callback_error=None):
        with (
            patch.object(reporting, "session_api_key", return_value="fixture"),
            patch.object(reporting, "api", side_effect=callback_error) as callback,
        ):
            result = finite.run(
                {"command": [sys.executable, "-c", source], "timeout": 5}, self.root
            )
        return result, callback.call_args.kwargs["json"]

    def test_success_and_failure_both_report_native_terminal_outcome(self):
        for code in (0, 7):
            with self.subTest(code=code):
                self.run_id = str(uuid4())
                os.environ["AUTOMATION_RUN_ID"] = self.run_id
                result, body = self.run_recipe(f"raise SystemExit({code})")
                # [utest~im-finite-FiniteTests-success_and_failure_both_report_native_terminal_outcome~1->req~im-finite-completion~1]
                self.assertEqual(result, code)
                self.assertEqual(body["status"], "COMPLETED" if code == 0 else "FAILED")
                record = json.loads(finite.receipt_path(self.run_id, self.root).read_text())
                self.assertIsNotNone(record["acknowledged_at"])
                self.assertGreaterEqual(record["execution_seconds"], 0)
                self.assertGreaterEqual(record["callback_seconds"], 0)

    def test_lost_callback_preserves_execution_and_never_reexecutes(self):
        marker = self.root / "ran"
        # [utest~im-finite-FiniteTests-lost_callback_preserves_execution_and_never_reexecutes~1->req~im-finite-completion~1]
        with self.assertRaises(ConnectionError):
            self.run_recipe(
                f"from pathlib import Path; Path({str(marker)!r}).write_text('once')",
                ConnectionError(),
            )
        self.assertEqual(marker.read_text(), "once")
        record = json.loads(finite.receipt_path(self.run_id, self.root).read_text())
        self.assertEqual(record["execution_status"], "COMPLETED")
        self.assertIsNone(record["acknowledged_at"])
        with patch.object(finite, "execute") as execute, self.assertRaises(FileExistsError):
            self.run_recipe("raise SystemExit(0)")
        execute.assert_not_called()
        with patch.object(
            finite, "api", return_value={"runs": [{"id": self.run_id, "status": "RUNNING"}]}
        ):
            status = finite.inspect_status(str(uuid4()), self.run_id, self.root)
        self.assertIn("do not rerun", status["diagnostic"])

    def test_timeout_is_reported_failed_without_waiting_for_native_watchdog(self):
        with (
            patch.object(reporting, "session_api_key", return_value="fixture"),
            patch.object(reporting, "api") as callback,
        ):
            code = finite.run(
                {"command": [sys.executable, "-c", "import time; time.sleep(20)"], "timeout": 1},
                self.root,
            )
        # [utest~im-finite-FiniteTests-timeout_is_reported_failed_without_waiting_for_native_watchdog~1->req~im-finite-timeout~1]
        self.assertEqual(code, 1)
        self.assertEqual(callback.call_args.kwargs["json"]["status"], "FAILED")
        record = json.loads(finite.receipt_path(self.run_id, self.root).read_text())
        self.assertEqual(record["error_type"], subprocess.TimeoutExpired.__name__)

    def test_invalid_command_and_changed_upload_do_not_execute(self):
        for request in (
            {"command": "python recipe.py"},
            {"command": ["true"], "digests": {"missing.py": "wrong"}},
        ):
            os.environ["AUTOMATION_RUN_ID"] = str(uuid4())
            with (
                patch.object(reporting, "session_api_key", return_value="fixture"),
                patch.object(reporting, "api"),
                patch.object(finite, "execute") as execute,
            ):
                self.assertEqual(finite.run(request, self.root), 1)
            execute.assert_not_called()

    def test_timeout_stops_parent_and_descendant(self):
        pids = self.root / "processes.json"
        source = (
            "import json, os, subprocess, sys, time\n"
            "from pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"Path({str(pids)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "time.sleep(60)\n"
        )

        def alive(pid):
            try:
                # Orphans may remain zombies until the container init reaps them.
                return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0] != "Z"
            except FileNotFoundError:
                return False

        try:
            with (
                patch.object(reporting, "session_api_key", return_value="fixture"),
                patch.object(reporting, "api") as callback,
            ):
                code = finite.run(
                    {"command": [sys.executable, "-c", source], "timeout": 2}, self.root
                )
            self.assertTrue(pids.exists(), "Child did not start before the timeout")
            processes = json.loads(pids.read_text())
            deadline = time.monotonic() + 2
            while any(alive(pid) for pid in processes) and time.monotonic() < deadline:
                time.sleep(0.01)
            # [utest~im-finite-FiniteTests-timeout_stops_parent_and_descendant~1->req~im-finite-timeout~1]
            self.assertFalse(any(alive(pid) for pid in processes), "Timeout left a process running")
            self.assertEqual(code, 1)
            self.assertEqual(callback.call_args.kwargs["json"]["status"], "FAILED")
            record = json.loads(finite.receipt_path(self.run_id, self.root).read_text())
            self.assertEqual(record["error_type"], subprocess.TimeoutExpired.__name__)
        finally:
            if pids.exists():
                for pid in json.loads(pids.read_text()):
                    if alive(pid):
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_payload_names_cannot_escape_or_replace_relative_paths(self):
        for name in ("../recipe.py", "/recipe.py", "./recipe.py", "a/../recipe.py"):
            with self.assertRaises(ValueError):
                finite.validate({"command": ["true"], "files": {name: "source"}})

    def test_registration_mandates_wrapper_and_refuses_code_override_or_duplicate(self):
        specification = importlib.util.spec_from_file_location(
            "finite_configure", "/scripts/configure.py"
        )
        configure = importlib.util.module_from_spec(specification)
        original = Path.read_text

        def read_text(path, *args, **kwargs):
            return (
                "fixture"
                if str(path) == "/run/secrets/canvas-key"
                else original(path, *args, **kwargs)
            )

        with patch.object(Path, "read_text", read_text):
            specification.loader.exec_module(configure)
        script = self.root / "recipe.py"
        script.write_text("print('finished')")
        request = self.root / "request.json"
        data = {
            "name": "Recipe",
            "command": ["python", "recipe.py"],
            "files": {"recipe.py": "recipe.py"},
        }
        request.write_text(json.dumps(data))
        with (
            patch.object(configure, "files", side_effect=lambda job: {"finite.py": b"wrapper"}),
            patch.object(configure, "records", return_value=[]),
            patch.object(configure, "install", return_value={"id": "new"}) as install,
        ):
            configure.finite(request)
            definition, payload = install.call_args.args
            self.assertEqual(definition["entrypoint"], "python finite.py finite-request.json")
            self.assertEqual(payload["recipe.py"], script.read_bytes())
            self.assertIn("recipe.py", json.loads(payload["finite-request.json"])["digests"])
            install.reset_mock()
            request.write_text(json.dumps({**data, "files": {"finite.py": "recipe.py"}}))
            with self.assertRaisesRegex(ValueError, "replace factory"):
                configure.finite(request)
            install.assert_not_called()
            request.write_text(json.dumps(data))
            with (
                patch.object(configure, "records", return_value=[{"name": "Recipe"}]),
                self.assertRaisesRegex(ValueError, "already exists"),
            ):
                configure.finite(request)
            install.assert_not_called()
