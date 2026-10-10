"""Receipt writers delegate replacement and failure cleanup to the pinned SDK."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import finite
import followup
import measurements
import reporting
import review_requests
from openhands.sdk.utils import files

CONFIG = {"project": "fixture", "repository": "org/repo"}
PR = {"number": 7, "head": {"sha": "a" * 40}, "base": {"ref": "main", "sha": "b" * 40}}


class ReceiptStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for module in (followup, reporting, review_requests):
            stack.enter_context(patch.object(module, "DATA", self.root))
        stack.enter_context(patch.dict(os.environ, AUTOMATION_RUN_ID="fixture-run"))

    def writers(self):
        yield (
            "followup",
            followup.directory(CONFIG) / "7.json",
            lambda record: followup.save(CONFIG, record),
        )
        yield (
            "report",
            reporting.report_path(CONFIG, "issue-7"),
            lambda record: reporting.write_report(CONFIG, "issue-7", record),
        )
        yield (
            "review",
            review_requests.receipt_path(CONFIG, PR),
            lambda record: review_requests.remember(
                CONFIG, PR, record["status"], value=record["value"]
            ),
        )
        yield (
            "finite",
            self.root / "finite.json",
            lambda record: finite.save(self.root / "finite.json", record),
        )
        yield (
            "metrics",
            self.root / "metrics.json",
            lambda record: measurements.write(self.root / "metrics.json", record),
        )

    def test_existing_receipts_update_without_using_legacy_temporary_paths(self):
        for name, path, write in self.writers():
            with self.subTest(writer=name):
                write({"number": 7, "status": "FAILED", "value": "retained"})
                legacy = path.with_suffix(".tmp")
                legacy.write_text("previous interrupted write")
                write({"number": 7, "status": "PUBLICATION_FAILED", "value": "new café"})
                record = json.loads(path.read_text())
                self.assertEqual(record["status"], "PUBLICATION_FAILED")
                self.assertEqual(record["value"], "new café")
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                self.assertEqual(legacy.read_text(), "previous interrupted write")
                self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_failed_receipt_flush_retains_previous_state_and_cleans_partial_write(self):
        for name, path, write in self.writers():
            with self.subTest(writer=name):
                write({"number": 7, "status": "FAILED", "value": "retained"})
                original = path.read_bytes()
                with patch.object(files.os, "fsync", side_effect=OSError("fixture I/O failure")):
                    with self.assertRaises(OSError):
                        write({"number": 7, "status": "PUBLICATION_FAILED", "value": "partial"})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_measurement_operator_commands_need_no_installed_sdk(self):
        directory = self.root / "run"
        directory.mkdir()
        recorder = measurements.Recorder(directory, "fixture", "build", [])
        recorder.save()
        original = recorder.path.read_bytes()
        commands = [
            ["show", str(recorder.path)],
            ["report", str(self.root)],
            [
                "note",
                str(recorder.path),
                "--kind",
                "human_review",
                "--minutes",
                "1",
                "--by",
                "fixture",
                "--note",
                "Fixture observation",
            ],
        ]
        for command in commands:
            with self.subTest(command=command[0]):
                result = subprocess.run(
                    [sys.executable, "-S", measurements.__file__, *command],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(recorder.path.read_bytes(), original)
        observation = json.loads((directory / "observations.jsonl").read_text())
        self.assertEqual(observation["kind"], "human_review")
        self.assertEqual(observation["minutes"], 1)
