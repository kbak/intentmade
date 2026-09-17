"""Verify role discovery across OpenHands' isolated Codex homes."""

import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


class AgencyAgentsTests(unittest.TestCase):
    def test_image_contains_valid_native_roles_without_runtime_overrides(self):
        directory = Path("/opt/factory/agency-agents")
        agents = [tomllib.loads(p.read_text()) for p in (directory / "agents").glob("*.toml")]
        self.assertEqual(len(agents), 273)
        self.assertEqual(len({agent["name"] for agent in agents}), len(agents))
        self.assertTrue((directory / "LICENSE").is_file())
        for agent in agents:
            self.assertEqual(set(agent), {"name", "description", "developer_instructions"})
            self.assertTrue(
                all(isinstance(value, str) and value.strip() for value in agent.values())
            )

    def test_acp_launch_seeds_isolated_home_and_preserves_existing_files(self):
        # Exercise the real patched launcher without starting a model turn.
        script = """
import { pathToFileURL } from 'node:url';
process.argv = ['node', 'codex-acp', '--help'];
await import(pathToFileURL('/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js'));
"""
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            roles = home / "agents"
            roles.mkdir()
            custom = roles / "code-reviewer.toml"
            custom.write_text('name = "Custom Reviewer"\n')
            reviewer = roles / "alibaba-reviewer.toml"
            reviewer.write_text('name = "Stale Factory Reviewer"\n')
            auth = home / "auth.json"
            auth.write_text(json.dumps({"test": "preserve credential file"}))
            before = auth.read_bytes()
            for _ in range(2):
                result = subprocess.run(
                    ["/acp-node/bin/node", "--input-type=module", "-e", script],
                    env={**os.environ, "CODEX_HOME": temp},
                    input="",
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(list(roles.glob("*.toml"))), 274)
                self.assertEqual(custom.read_text(), 'name = "Custom Reviewer"\n')
                self.assertEqual(auth.read_bytes(), before)
                self.assertEqual(tomllib.loads(reviewer.read_text())["name"], "Alibaba Reviewer")
                frontend = tomllib.loads((roles / "frontend-developer.toml").read_text())
                self.assertEqual(frontend["name"], "Frontend Developer")

    def test_cloudflare_bundle_has_executable_validators_and_companion_files(self):
        skill = Path("/opt/factory/reviewers/cloudflare/skills/security-audit")
        for name in (
            "SKILL.md",
            "RECONNAISSANCE.md",
            "HUNTING.md",
            "VALIDATION-AND-REPORTING.md",
            "report-schema.json",
        ):
            self.assertTrue((skill / name).is_file())
        subprocess.run(
            [
                "node",
                "--test",
                str(skill / "validate-findings.test.cjs"),
                str(skill / "validate-coverage-ledger.test.cjs"),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
