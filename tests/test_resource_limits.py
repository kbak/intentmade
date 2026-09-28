"""Exercise resource bounds using the actual import and upstream extraction paths."""

import gzip
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cleanup
import resource_limits as limits
import transfer
from common import reviews


def archive_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in entries:
            member = tarfile.TarInfo("repository/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class ResourceLimitTests(unittest.TestCase):
    def configure(self, **overrides):
        return patch.object(limits, "settings", return_value=limits.validate(overrides))

    def test_limits_are_loaded_from_operator_json_with_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config"
            config.mkdir()
            (config / "defaults.json").write_text(
                json.dumps({"resource_limits": {"worker_memory_mb": 2048, "worker_cpus": 3}})
            )
            with patch.dict(os.environ, {"FACTORY_ROOT": temp}):
                flags = limits.worker_docker_flags()
                self.assertEqual(flags[flags.index("--memory") + 1], "2048m")
                self.assertEqual(flags[flags.index("--cpus") + 1], "3")
                self.assertEqual(flags[flags.index("--pids-limit") + 1], "512")

    def test_copy_accepts_exact_limit_and_rejects_overflow_without_truncating_successfully(self):
        target = io.BytesIO()
        limits.copy_bounded(io.BytesIO(b"1234"), target, 4, "Fixture")
        self.assertEqual(target.getvalue(), b"1234")
        with self.assertRaisesRegex(RuntimeError, "Fixture exceeds"):
            limits.copy_bounded(io.BytesIO(b"12345"), io.BytesIO(), 4, "Fixture")

    def test_low_disk_prevents_job_admission_without_deleting_retained_work(self):
        with tempfile.TemporaryDirectory() as temp:
            retained = Path(temp) / "job-recovery"
            retained.mkdir()
            with (
                patch.object(limits.shutil, "disk_usage", return_value=SimpleNamespace(free=0)),
                self.assertRaisesRegex(RuntimeError, "disk headroom"),
                cleanup.job_directory(temp),
            ):
                self.fail("Admitted a job with no free disk")
            self.assertEqual(list(Path(temp).iterdir()), [retained])

    def test_worker_limits_reject_disabled_or_invalid_values(self):
        for value in (0, -1, True, "unlimited"):
            with self.assertRaises(ValueError):
                limits.validate({"worker_pids": value})
        with self.assertRaises(ValueError):
            limits.validate({"typo": 10})

    def test_large_bundle_is_rejected_before_parent_git(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "export.bundle"
            with bundle.open("wb") as stream:
                stream.truncate(limits.MIB + 1)
            with (
                patch.object(
                    transfer, "settings", return_value=limits.validate({"max_bundle_mb": 1})
                ),
                patch.object(transfer, "git") as git,
                self.assertRaisesRegex(RuntimeError, "Worker bundle exceeds"),
            ):
                transfer.import_task({"branch": "factory/task"}, bundle)
            git.assert_not_called()

    def checkout(self, payload, root):
        with (
            patch.dict(os.environ, {"WORKSPACE_BASE": str(root)}),
            patch.object(limits.urllib.request, "urlopen", return_value=io.BytesIO(payload)),
        ):
            return reviews._prepare_repository("fixture", "owner/repo", 1, "a" * 40)

    def test_normal_archive_and_upstream_path_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            checkout = self.checkout(archive_bytes([("src/main.py", b"print('ok')")]), temp)
            self.assertEqual((checkout / "src/main.py").read_bytes(), b"print('ok')")
            with self.assertRaisesRegex(RuntimeError, "path traversal"):
                self.checkout(archive_bytes([("../escape", b"no")]), temp)
            self.assertFalse(checkout.exists())

    def test_download_decompression_and_member_limits(self):
        scenarios = [
            ({"max_archive_download_mb": 1}, b"x" * (limits.MIB + 1), "download"),
            (
                {"max_archive_expanded_mb": 1},
                gzip.compress(b"0" * (limits.MIB + 1)),
                "Expanded",
            ),
            (
                {"max_archive_members": 1},
                archive_bytes([("one", b"a"), ("two", b"b")]),
                "member limit",
            ),
        ]
        for env, payload, error in scenarios:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp:
                with self.configure(**env), self.assertRaisesRegex(RuntimeError, error):
                    self.checkout(payload, temp)
                self.assertFalse(list(Path(temp).rglob("pr-*")))

    def test_sparse_declared_size_is_bounded_before_extraction(self):
        member = tarfile.TarInfo("repository/large")
        member.size = 2 * limits.MIB
        with self.configure(max_archive_expanded_mb=1):
            with self.assertRaisesRegex(RuntimeError, "extracted size"):
                limits.archive_members([member])
