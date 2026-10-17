"""Worker observations describe activity without supplying validation authority."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from traceability.activity import feedback_activity


class FeedbackActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "traceability/app"
        self.base.mkdir(parents=True)
        self.selected = {"app": {"root": self.root, "scratch": self.base / "scratch"}}

    def invocation(self, index=1, *, completed=None):
        folder = self.base / f"agent-check-00000000-0000-0000-0000-{index:012d}"
        folder.mkdir()
        (folder / "invocation.json").write_text(
            json.dumps(
                {
                    "phase": "feedback",
                    "started_at": f"2026-10-17T20:14:{index:02d}+00:00",
                    "completed_at": completed,
                }
            )
        )
        (folder / "evidence").mkdir()
        (folder / "evidence/tests.log").write_text(
            "[check] Library and module build: exit 0, 316s\n"
            "[check] Kernel checks (all discovered modules): starting\n"
        )
        return folder

    def test_latest_feedback_reports_step_and_completion_does_not_revive_old_check(self):
        self.invocation()
        latest = self.invocation(2)
        # [utest~im-canvas-feedback-observation~1->req~im-canvas-progress~1]
        self.assertEqual(
            feedback_activity(self.selected),
            "Worker validation: app / Kernel checks (all discovered modules)",
        )
        path = latest / "invocation.json"
        record = json.loads(path.read_text())
        record["completed_at"] = "2026-10-17T20:21:00+00:00"
        path.write_text(json.dumps(record))
        self.assertEqual(feedback_activity(self.selected), "")

    def test_malformed_receipts_links_and_special_files_are_ignored(self):
        folder = self.invocation()
        receipt = folder / "invocation.json"
        for contents in ("not json", "[]", "{}", '{"started_at": null}', "x" * 20000):
            with self.subTest(contents=contents[:30]):
                receipt.write_text(contents)
                self.assertEqual(feedback_activity(self.selected), "")
        receipt.unlink()
        outside = self.root / "outside.json"
        outside.write_text('{"phase":"feedback","started_at":"2026-10-17T20:14:01+00:00"}')
        receipt.symlink_to(outside)
        self.assertEqual(feedback_activity(self.selected), "")
        receipt.unlink()
        os.mkfifo(receipt)
        self.assertEqual(feedback_activity(self.selected), "")
        folder.rename(self.root / "moved")
        folder.symlink_to(self.root / "moved", target_is_directory=True)
        self.assertEqual(feedback_activity(self.selected), "")

    def test_log_is_bounded_and_missing_or_linked_log_never_supplies_a_step(self):
        folder = self.invocation()
        log = folder / "evidence/tests.log"
        log.write_text("x" * 100000 + "\n[check] Axiom audit: starting\n")
        self.assertEqual(feedback_activity(self.selected), "Worker validation: app / Axiom audit")
        log.unlink()
        self.assertEqual(feedback_activity(self.selected), "Worker validation: app")
        os.mkfifo(log)
        self.assertEqual(feedback_activity(self.selected), "Worker validation: app")
        log.unlink()
        outside = self.root / "outside.log"
        outside.write_text("[check] Private content: starting\n")
        log.symlink_to(outside)
        self.assertEqual(feedback_activity(self.selected), "Worker validation: app")

    def test_excessive_directory_entries_are_not_scanned(self):
        self.invocation()
        for index in range(201):
            (self.base / str(index)).touch()
        self.assertEqual(feedback_activity(self.selected), "")
        self.assertEqual(feedback_activity({}), "")
