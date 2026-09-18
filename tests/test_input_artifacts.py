"""Artifact custody checks use real files; no network or model calls."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import input_artifacts as inputs
import sandbox


class DeclaredInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "artifacts"
        self.source.mkdir()
        (self.source / "release.db").write_bytes(b"historical database")
        self.declared = [
            {
                "name": "v1.db",
                "reference": "release.db",
                "sha256": hashlib.sha256(b"historical database").hexdigest(),
                "size": 19,
                "producer": "owner/repo@" + "a" * 40,
            }
        ]
        self.artifact = self.root / "run"
        self.artifact.mkdir()
        for attribute, value in [("SOURCE_ROOT", self.source), ("DATA", self.root / "data")]:
            context = patch.object(inputs, attribute, value)
            context.start()
            self.addCleanup(context.stop)

    def prepared(self, declared=None):
        return inputs.prepared(
            self.declared if declared is None else declared,
            self.artifact,
            task="task",
            bases={"repo": "a" * 40},
            request="accepted contract",
        )

    def test_restarts_and_both_workers_share_verified_readonly_mount(self):
        directories = []
        for _ in range(2):
            with self.prepared() as record:
                directories.append(record["directory"])
                for role in ("implementation", "review"):
                    root = self.root / role
                    root.mkdir(exist_ok=True)
                    mounts = sandbox.mounts(root, {})
                    self.assertIn(record["directory"] + ":/factory-inputs:ro", mounts)
                    self.assertNotIn(str(self.source), "\n".join(mounts))
                    self.assertEqual(inputs.directory(root), record["directory"])
                    self.assertIn("/factory-inputs", inputs.instructions())
                self.assertEqual(
                    (Path(record["directory"]) / "v1.db").read_bytes(), b"historical database"
                )
                self.assertEqual(
                    json.loads((Path(record["directory"]) / "manifest.json").read_text()),
                    self.declared,
                )
        self.assertEqual(directories[0], directories[1])
        self.assertIsNone(inputs.CURRENT.get())
        self.assertEqual(
            json.loads((self.artifact / "input-artifacts.json").read_text())["status"], "verified"
        )

    def test_bad_digest_missing_size_symlink_and_hardlink_rejected_with_receipt(self):
        for mode in ("digest", "missing", "size", "symlink", "hardlink"):
            with self.subTest(mode=mode):
                source = self.source / "release.db"
                source.unlink(missing_ok=True)
                source.write_bytes(b"historical database")
                declared = [dict(self.declared[0])]
                if mode == "digest":
                    declared[0]["sha256"] = "b" * 64
                elif mode == "missing":
                    source.unlink()
                elif mode == "size":
                    declared[0]["size"] = 4
                elif mode == "symlink":
                    source.unlink()
                    source.symlink_to("/etc/hosts")
                elif mode == "hardlink":
                    (self.source / "alias").hardlink_to(source)
                with self.assertRaises((ValueError, OSError)):
                    with self.prepared(declared):
                        self.fail("Rejected input became active")
                receipt = json.loads((self.artifact / "input-artifacts.json").read_text())
                self.assertEqual(receipt["status"], "rejected")
                self.assertIsNone(inputs.CURRENT.get())

    def test_parent_symlinks_unsafe_names_and_duplicates(self):
        (self.source / "linked").symlink_to(self.source, target_is_directory=True)
        bad = [[self.declared[0], self.declared[0]]]
        for key, value in [
            ("name", "../outside"),
            ("name", "manifest.json"),
            ("reference", "/etc/passwd"),
            ("reference", "a/../release.db"),
            ("reference", "linked/release.db"),
            ("size", True),
        ]:
            bad.append([{**self.declared[0], key: value}])
        for declared in bad:
            with self.subTest(declared=declared), self.assertRaises((ValueError, OSError)):
                with self.prepared(declared):
                    self.fail("Invalid declaration was accepted")

    def test_corrupted_cache_and_changed_source_rejected_on_restart(self):
        with self.prepared() as record:
            frozen = Path(record["directory"]) / "v1.db"
        frozen.write_bytes(b"corruption")
        with self.assertRaises(ValueError):
            with self.prepared():
                self.fail("Corrupted cache reused")
        frozen.write_bytes(b"historical database")
        (self.source / "release.db").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            with self.prepared():
                self.fail("Changed source silently replaced")

    def test_task_binding_cannot_change_on_a_later_run(self):
        repository = self.root / "task.git"
        repository.mkdir()
        with self.prepared() as record:
            inputs.bind_task(repository, record)
            inputs.bind_task(repository, record)
            with self.assertRaisesRegex(ValueError, "changed"):
                inputs.bind_task(repository, {"inputs": []})
        self.assertEqual(
            json.loads((repository / "factory-inputs.json").read_text()), self.declared
        )
