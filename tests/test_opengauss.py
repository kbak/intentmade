"""Proof workflow selection preserves stage/model/credential boundaries."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import deployment
import harness

runtime_path = Path(__file__).resolve().parents[1] / "runtime/opengauss_runtime.py"
spec = importlib.util.spec_from_file_location("opengauss_runtime", runtime_path)
opengauss_runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opengauss_runtime)
install_skill = opengauss_runtime.install_skill


class OpenGaussTests(unittest.TestCase):
    def test_skill_registration_is_idempotent_and_preserves_existing_login_and_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            source = assets / "lean4-skills/plugins/lean4/skills/lean4"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("Pinned skill fixture")
            home = root / "login"
            home.mkdir()
            auth = home / "auth.json"
            auth.write_text("fixture login marker")
            with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
                install_skill(assets)
                install_skill(assets)
                destination = home / "skills/lean4"
                # [utest~im-opengauss-skill-registration~1->req~im-opengauss-workflow~1]
                self.assertEqual(destination.resolve(), source)
                self.assertEqual(auth.read_text(), "fixture login marker")
                destination.unlink()
                destination.mkdir()
                marker = destination / "operator.txt"
                marker.write_text("existing operator skill")
                with self.assertRaises(FileExistsError):
                    install_skill(assets)
                self.assertEqual(marker.read_text(), "existing operator skill")
                self.assertEqual(auth.read_text(), "fixture login marker")

    def select(self):
        profile = {"agent_kind": "acp", "acp_server": "codex", "acp_model": "fixture/high"}
        return harness.resolve(profile, "fixture")

    def test_operator_selection_is_validated_and_never_part_of_repository_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "defaults.json").write_text("{}")
            path = directory / "deployment.json"
            path.write_text(json.dumps({"implementation_workflows": {"clafer": "opengauss"}}))
            # [utest~im-opengauss-selection~1->req~im-opengauss-workflow~1]
            self.assertEqual(
                deployment.settings(directory)["implementation_workflows"], {"clafer": "opengauss"}
            )
            with self.assertRaisesRegex(ValueError, "deployment.json"):
                deployment.workflow_settings({}, {"implementation_workflows": {}})
            for invalid in ([], None, {"../clafer": "opengauss"}, {"clafer": "other"}):
                path.write_text(json.dumps({"implementation_workflows": invalid}))
                with (
                    self.subTest(invalid=invalid),
                    self.assertRaisesRegex(ValueError, "implementation_workflows"),
                ):
                    deployment.settings(directory)
            (directory / "defaults.json").write_text(json.dumps({"implementation_workflows": {}}))
            path.write_text("{}")
            with self.assertRaisesRegex(ValueError, "deployment.json"):
                deployment.settings(directory)

    def test_only_selected_implementation_changes_and_model_and_context_survive(self):
        selected = self.select()
        with patch.object(
            deployment,
            "settings",
            return_value={"implementation_workflows": {"clafer": "opengauss"}},
        ):
            harness.select_implementation(selected, [{"project": "clafer"}])
        token = harness.CURRENT.set(selected)
        self.addCleanup(harness.CURRENT.reset, token)
        implementation = agent.worker_agent(
            "agent-full-access", "factory-implementation", traceability=True
        )
        # [utest~im-opengauss-stage-boundaries~1->req~im-opengauss-workflow~1]
        self.assertEqual(implementation.acp_command, ["/opt/factory/opengauss-codex-acp"])
        self.assertEqual(implementation.acp_model, "fixture/high")
        self.assertEqual(implementation.acp_server, "codex")
        self.assertIn(
            "factory-implementation", [s.name for s in implementation.agent_context.skills]
        )
        self.assertTrue(
            any("traceability" in s.content.lower() for s in implementation.agent_context.skills)
        )
        for skill in ("factory-review", "factory-review-report", None):
            reviewer = agent.worker_agent("read-only", skill)
            self.assertEqual(reviewer.acp_command, ["codex-acp"])
            self.assertEqual(reviewer.mcp_config, {})
        self.assertEqual(selected.settings.acp_command, ["codex-acp"])
        self.assertEqual(selected.identity["implementation_workflow"], "opengauss")
        with self.assertRaisesRegex(ValueError, "implementation-only"):
            agent.worker_agent("read-only", "factory-implementation")

    def test_tools_only_keeps_stage_boundaries_without_managed_guidance(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "defaults.json").write_text("{}")
            (directory / "deployment.json").write_text(
                json.dumps({"implementation_workflows": {"clafer": "lean-mcp"}})
            )
            self.assertEqual(
                deployment.settings(directory)["implementation_workflows"], {"clafer": "lean-mcp"}
            )
        selected = self.select()
        with patch.object(
            deployment,
            "settings",
            return_value={"implementation_workflows": {"clafer": "lean-mcp"}},
        ):
            harness.select_implementation(selected, [{"project": "clafer"}])
        token = harness.CURRENT.set(selected)
        self.addCleanup(harness.CURRENT.reset, token)
        implementation = agent.worker_agent("agent-full-access", "factory-implementation")
        # [utest~im-opengauss-tools-only~1->req~im-opengauss-workflow~1]
        self.assertEqual(
            implementation.acp_command, ["/opt/factory/opengauss-codex-acp", "--lean-mcp"]
        )
        self.assertEqual(implementation.acp_model, "fixture/high")
        self.assertEqual(selected.identity["implementation_workflow"], "lean-mcp")
        self.assertEqual(
            agent.worker_agent("read-only", "factory-review").acp_command, ["codex-acp"]
        )
        self.assertEqual(selected.settings.acp_command, ["codex-acp"])
        with self.assertRaisesRegex(ValueError, "implementation-only"):
            agent.worker_agent("read-only", "factory-implementation")

    def test_tools_only_validates_runtime_and_project_without_importing_gauss(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, assets = root / "project", root / "assets"
            project.mkdir()
            (project / "lakefile.toml").write_text('name = "fixture"\n')
            binary = assets / "venv/bin/lean-lsp-mcp"
            binary.parent.mkdir(parents=True)
            binary.touch()
            prepared = opengauss_runtime.prepare(project, assets, mode="lean-mcp")
            self.assertEqual(prepared["instructions"], "")
            self.assertEqual(prepared["skills"], [])
            self.assertEqual(prepared["mcp"]["env"], {"LEAN_PROJECT_PATH": str(project)})
            self.assertFalse((assets / "source").exists())
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                opengauss_runtime.prepare(project, assets, mode="unknown")
            binary.unlink()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                opengauss_runtime.prepare(project, assets, mode="lean-mcp")
            (project / "lakefile.toml").unlink()
            with self.assertRaisesRegex(ValueError, "Lean project"):
                opengauss_runtime.prepare(project, assets, mode="lean-mcp")

    def test_unsupported_harness_and_group_fail_before_worker_allocation(self):
        with patch.object(
            deployment,
            "settings",
            return_value={"implementation_workflows": {"clafer": "opengauss"}},
        ):
            # [utest~im-opengauss-unsupported~1->req~im-opengauss-workflow~1]
            with self.assertRaisesRegex(ValueError, "one repository"):
                harness.select_implementation(
                    self.select(), [{"project": "clafer"}, {"project": "other"}]
                )
            selected = self.select()
            selected.settings.agent_kind = "openhands"
            with self.assertRaisesRegex(ValueError, "Codex ACP"):
                harness.select_implementation(selected, [{"project": "clafer"}])
            other = self.select()
            harness.select_implementation(other, [{"project": "other"}])
            self.assertIsNone(other.implementation_workflow)

    def test_native_session_hook_preserves_transport_configuration_and_fails_closed(self):
        root = Path(__file__).resolve().parents[1]
        hook = root / "runtime/factory-context.mjs"
        if not hook.exists():
            hook = Path("/runtime/factory-context.mjs")
        program = """
import { factorySessionConfig } from URL_PLACEHOLDER;
const original = { developer_instructions: 'factory-guidance', approval_policy: 'never',
                   sandbox_mode: 'workspace-write', openai_base_url: 'http://fixture.invalid' };
if (process.env.FACTORY_OPENGAUSS) {
  try { factorySessionConfig(original, { cwd: '/missing-lean-project' }); process.exit(3); }
  catch (error) { if (!error.message.startsWith('OpenGauss preparation failed')) throw error; }
} else {
  if (factorySessionConfig(original, { cwd: '/worktree' }) !== original) process.exit(4);
}
""".replace("URL_PLACEHOLDER", json.dumps(hook.as_uri()))
        # [utest~im-opengauss-hook-failure~1->req~im-opengauss-workflow~1]
        for mode in (None, "autoprove", "lean-mcp", "unknown"):
            environment = dict(os.environ)
            environment.pop("FACTORY_COORDINATOR", None)
            environment.pop("FACTORY_OPENGAUSS", None)
            if mode:
                environment["FACTORY_OPENGAUSS"] = mode
            subprocess.run(
                ["/acp-node/bin/node", "--input-type=module", "-e", program],
                env=environment,
                check=True,
                capture_output=True,
                timeout=20,
            )


if __name__ == "__main__":
    unittest.main()
