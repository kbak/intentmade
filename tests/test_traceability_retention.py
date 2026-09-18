"""Custody failures preserve the source; only allowed regular evidence is exported."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traceability import retention
from traceability.invocation import begin


class RetentionTests(unittest.TestCase):
    def test_incomplete_and_unregistered_checks_stay_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checks = root / "checks"
            checks.mkdir()
            scope = root / "scope.json"
            scope.write_text("{}")
            first, _ = begin(checks, "feedback", root, scope, "base")
            (first / "evidence").mkdir()
            (first / "evidence/tests.log").write_text("interrupted checker output")
            (checks / "agent-check-legacy").mkdir()
            paths = {
                "root": root,
                "scratch": checks / "scratch",
                "retained": root / "traceability-0",
            }
            index = retention.retain(paths, 0)
            self.assertEqual(len(index["invocations"]), 2)
            self.assertTrue(
                all(i["bundle"]["status"] == "incomplete" for i in index["invocations"])
            )
            self.assertEqual(index["feedback_iterations"], 1)
            self.assertTrue((root / "traceability-invocations-0/index.json").is_file())

    def test_links_special_files_unknown_files_and_size_limits_are_not_exported(self):
        for kind in ("symlink", "parent_link", "hardlink", "unknown", "size", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                import os

                root = Path(temporary)
                checks = root / "checks"
                checks.mkdir()
                scope = root / "scope.json"
                scope.write_text("{}")
                invocation, _ = begin(checks, "feedback", root, scope, "base")
                evidence = invocation / "evidence"
                evidence.mkdir()
                secret = root / "private"
                secret.write_text("DO_NOT_EXPORT")
                item = evidence / "tests.log"
                if kind == "symlink":
                    item.symlink_to(secret)
                elif kind == "parent_link":
                    evidence.rmdir()
                    evidence.symlink_to(root, target_is_directory=True)
                elif kind == "hardlink":
                    os.link(secret, item)
                elif kind == "unknown":
                    (evidence / "credentials.json").write_text("DO_NOT_EXPORT")
                elif kind == "fifo":
                    os.mkfifo(item)
                else:
                    item.write_text("x" * 20)
                paths = {
                    "root": root,
                    "scratch": checks / "scratch",
                    "retained": root / "traceability-0",
                }
                with patch.object(retention, "MAX_FILE_BYTES", 10 if kind == "size" else 100000):
                    with self.assertRaises((RuntimeError, OSError)):
                        retention.retain(paths, 0)
                self.assertTrue(secret.exists())
                self.assertTrue(invocation.exists())
                self.assertFalse(
                    any(
                        "DO_NOT_EXPORT" in p.read_text()
                        for p in (root / "traceability-invocations-0").rglob("*")
                        if p.is_file()
                    )
                )

    def test_manifest_hashes_cover_preserved_rejected_bundle(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checks = root / "checks"
            checks.mkdir()
            scope = root / "scope.json"
            scope.write_text("{}")
            invocation, _ = begin(checks, "feedback", root, scope, "base")
            evidence = invocation / "evidence"
            evidence.mkdir()
            data = json.dumps(
                {"predicate": {"status": "rejected", "diagnostics": ["source changed"]}}
            )
            (evidence / "evidence.json").write_text(data)
            index = retention.retain(
                {"root": root, "scratch": checks / "scratch", "retained": root / "traceability-0"},
                0,
            )
            retained = index["invocations"][0]
            self.assertEqual(retained["bundle"]["status"], "rejected")
            self.assertEqual(
                retained["artifacts"]["evidence/evidence.json"]["sha256"],
                hashlib.sha256(data.encode()).hexdigest(),
            )
            self.assertIsNone(retained["exit_code"])


if __name__ == "__main__":
    unittest.main()
