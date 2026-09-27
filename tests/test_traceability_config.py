"""Deployment scope loading uses the portable contract before jobs are uploaded."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common
import traceability


class ScopeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "repositories").mkdir()
        (self.root / "traceability").mkdir()
        (self.root / "defaults.json").write_text(
            json.dumps({"required_checks": ["unit"], "test_command": "make test"})
        )
        self.registration = self.root / "repositories/pilot.json"
        self.scope = self.root / "traceability/pilot.json"
        self.scope.write_text(
            (Path(__file__).parent / "fixtures/traceability-scope.json").read_text()
        )
        self.register(traceability_scope="traceability/pilot.json")

    def register(self, **settings):
        self.registration.write_text(json.dumps({"repository": "", **settings}))

    def test_absent_scope_keeps_ordinary_workflow_without_optional_packages(self):
        self.register()
        with patch.dict(sys.modules, {"intentbond": None}):
            config = common.projects(self.root)["pilot"]
        self.assertNotIn("traceability_scope", config)
        self.assertEqual(config["test_command"], "make test")

    def test_discussion_without_traceability_needs_no_optional_packages(self):
        self.register()
        with patch.dict(sys.modules, {"intentbond": None}):
            context = traceability.discussion_context(list(common.projects(self.root).values()))
        self.assertEqual(context, {"repositories": {"pilot": {"catalog": "/projects/repos/pilot"}}})
        with patch.dict(sys.modules, {"intentbond": None}):
            self.assertEqual(traceability.review_context({}), "")

    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_mixed_discussion_loads_authoring_semantics_and_selected_scopes(self):
        from importlib.resources import files

        selected = common.projects(self.root)["pilot"]
        context = traceability.discussion_context([selected, {"project": "ordinary"}])
        self.assertEqual(
            context["repositories"]["pilot"]["traceability_scope"],
            json.loads(self.scope.read_text()),
        )
        self.assertEqual(
            context["repositories"]["ordinary"], {"catalog": "/projects/repos/ordinary"}
        )
        directory = files("intentbond").joinpath("skills/intentbond/references")
        expected = "\n\n".join(
            (directory / name).read_text(encoding="utf-8")
            for name in ("requirements.md", "semantics.md")
        )
        self.assertEqual(context["requirements_guidance"], expected)
        self.assertNotIn("pip install", context["requirements_guidance"])
        review_context = traceability.review_context({"pilot": {"changed_paths": ["session.py"]}})
        self.assertIn((directory / "semantics.md").read_text(encoding="utf-8"), review_context)

    def test_reference_must_be_a_relative_path_inside_config(self):
        for reference in (None, False, "", " ", {}, str(self.scope), "../outside.json"):
            with self.subTest(reference=reference):
                self.register(traceability_scope=reference)
                with self.assertRaisesRegex(ValueError, "traceability_scope"):
                    common.projects(self.root)
        self.scope.unlink()
        self.scope.symlink_to(self.root.parent / "outside.json")
        self.register(traceability_scope="traceability/pilot.json")
        with self.assertRaisesRegex(ValueError, "inside the config"):
            common.projects(self.root)

    def test_old_inline_setting_cannot_silently_disable_checks(self):
        self.register(traceability={"inputs": ["requirements.md"]})
        with self.assertRaisesRegex(ValueError, "replace inline traceability"):
            common.projects(self.root)

    def test_scope_and_registration_cannot_define_two_test_commands(self):
        self.register(traceability_scope="traceability/pilot.json", test_command="true")
        with self.assertRaisesRegex(ValueError, "only in the traceability scope"):
            common.projects(self.root)

    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_scope_contents_are_resolved_from_config_and_own_the_test_command(self):
        # The process cwd is unrelated to the deployment directory.
        config = common.projects(self.root)["pilot"]
        self.assertEqual(config["traceability_scope"], json.loads(self.scope.read_text()))
        self.assertNotIn("test_command", config)
        self.scope.unlink()
        self.assertEqual(config["traceability_scope"]["name"], "session-checks")

    @unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
    def test_deleted_or_invalid_scope_is_a_configuration_error(self):
        from intentbond.common import CheckError

        self.scope.unlink()
        with self.assertRaises((CheckError, OSError)):
            common.projects(self.root)
        for content in ("null", "{", '{"schema_version": 1}', '{"name":"a","name":"b"}'):
            with self.subTest(content=content):
                self.scope.write_text(content)
                with self.assertRaises(CheckError):
                    common.projects(self.root)


@unittest.skipUnless(importlib.util.find_spec("intentbond"), "Optional portable package")
class ScopeIdentityTests(unittest.TestCase):
    setUp = ScopeConfigurationTests.setUp
    register = ScopeConfigurationTests.register

    def test_exact_source_survives_native_payload_and_worker_freezing(self):
        from traceability.scope_identity import export

        raw = (
            json.dumps(
                json.loads(self.scope.read_text()), separators=(",", ":"), ensure_ascii=False
            ).encode()
            + b"\r\n"
        )
        self.scope.write_bytes(raw)
        config = json.loads(json.dumps(common.projects(self.root)["pilot"]))
        self.scope.unlink()  # Execution must not reread mutable deployment config.
        data, identity = export(config)
        self.assertEqual(data, raw)
        self.assertEqual(identity["source_file"]["sha256"], identity["worker_file"]["sha256"])
        artifact, worker = self.root / "artifacts", self.root / "worker"
        artifact.mkdir()
        worker.mkdir()
        states = {"pilot": {}}
        selected = traceability.prepare([config], states, worker, artifact, 0)["pilot"]
        self.assertEqual(selected["scope"].read_bytes(), raw)
        self.assertEqual(selected["worker_scope"].read_bytes(), raw)
        self.assertEqual(
            json.loads((artifact / "pilot/scope-identity-0.json").read_text()), identity
        )
        self.assertEqual(states["pilot"]["traceability"]["scope_identity"], identity)

    def test_formatting_changes_byte_identity_but_values_change_policy_identity(self):
        from traceability.scope_identity import export

        scope = json.loads(self.scope.read_text())
        first = export(common.projects(self.root)["pilot"])[1]
        self.scope.write_text(json.dumps(scope, sort_keys=True, separators=(",", ":")))
        second = export(common.projects(self.root)["pilot"])[1]
        self.assertNotEqual(first["source_file"], second["source_file"])
        self.assertEqual(first["canonical_policy"], second["canonical_policy"])
        scope["tests"]["timeout_seconds"] += 1
        self.scope.write_text(json.dumps(scope))
        third = export(common.projects(self.root)["pilot"])[1]
        self.assertNotEqual(second["canonical_policy"], third["canonical_policy"])

    def test_captured_source_tampering_fails_and_legacy_source_is_unknown(self):
        from traceability.scope_identity import export

        config = common.projects(self.root)["pilot"]
        config["traceability_scope"]["tests"]["timeout_seconds"] += 1
        with self.assertRaisesRegex(ValueError, "controller-selected"):
            export(config)
        config = common.projects(self.root)["pilot"]
        config["traceability_scope_source"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            export(config)
        del config["traceability_scope_source"]
        _, identity = export(config)
        self.assertIsNone(identity["source_file"])
        self.assertEqual(identity["export"], "legacy_value_serialization")
