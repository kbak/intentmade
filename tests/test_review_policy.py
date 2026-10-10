"""Exercise the installed permission bridge and adapter without starting any agents."""

import ast
import asyncio
import inspect
import json
import os
import re
import subprocess
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

from openhands.sdk.agent.acp_agent import ACPAgent, _OpenHandsACPBridge


class InstalledReviewPolicyTests(unittest.TestCase):
    def test_launcher_uses_native_explicit_mcp_precedence_even_with_an_inherited_override(self):
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        native = re.search(r"function shouldDeduplicateMcpConflicts\(\).*?\n}", source, re.S)
        self.assertIsNotNone(native)
        launcher = Path("/opt/factory/codex-acp").read_text().rsplit("\nexec ", 1)[0]
        subprocess.run(
            [
                "/bin/sh",
                "-c",
                launcher + '\nexec /acp-node/bin/node --input-type=module -e "$1"',
                "mcp-policy-probe",
                native.group()
                + "\nimport assert from 'node:assert/strict';"
                + "\nassert.equal(shouldDeduplicateMcpConflicts(), false);",
            ],
            env={**os.environ, "DISABLE_MCP_CONFIG_FILTERING": "false"},
            check=True,
        )

    def mode(self, name, coordinator=False):
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        matches = re.findall(
            rf"  static {name} = new _AgentMode\((.*?)\n  \);", source, flags=re.DOTALL
        )
        self.assertEqual(len(matches), 1, "Pinned adapter mode source changed")
        # Evaluate only the data arguments, without loading the adapter, reading
        # credentials, starting a session, or making a model/network request.
        result = subprocess.run(
            [
                "/acp-node/bin/node",
                "-e",
                "process.stdout.write(JSON.stringify([" + matches[0] + "]));",
            ],
            check=True,
            capture_output=True,
            text=True,
            env={"FACTORY_COORDINATOR": "1" if coordinator else ""},
        )
        return json.loads(result.stdout)

    def test_installed_read_only_mode_has_no_writes_network_or_escalation(self):
        mode = self.mode("ReadOnly")
        self.assertEqual(mode[0], "read-only")
        self.assertEqual(mode[4], "never")
        self.assertEqual(mode[6], {"type": "readOnly", "networkAccess": False})
        self.assertEqual(mode[7], "read-only")

    def test_build_and_coordinator_modes_keep_native_capabilities(self):
        builder = self.mode("AgentFullAccess")
        self.assertEqual(builder[4], "never")
        self.assertEqual(builder[6], {"type": "dangerFullAccess"})
        coordinator = self.mode("Agent")
        self.assertEqual(coordinator[4], "on-request")
        self.assertEqual(coordinator[5], "auto_review")
        self.assertEqual(coordinator[6]["type"], "workspaceWrite")

    def test_factory_marker_does_not_expand_review_or_agent_permissions(self):
        self.assertEqual(self.mode("Agent", coordinator=True)[6]["writableRoots"], [])
        self.assertEqual(self.mode("Agent")[6]["writableRoots"], [])
        self.assertEqual(
            self.mode("ReadOnly", coordinator=True)[6], {"type": "readOnly", "networkAccess": False}
        )

    def test_read_only_bridge_refuses_even_when_an_allow_option_is_first(self):
        client = _OpenHandsACPBridge()
        client.factory_read_only = True
        for options in ([SimpleNamespace(option_id="allow_once")], []):
            with self.subTest(options=options):
                result = asyncio.run(
                    client.request_permission(
                        options=options, session_id="fixture", tool_call="write file"
                    )
                )
                self.assertEqual(result.model_dump()["outcome"], {"outcome": "cancelled"})

    def test_builder_bridge_keeps_approval_behavior(self):
        client = _OpenHandsACPBridge()
        client.factory_read_only = False
        result = asyncio.run(
            client.request_permission(
                options=[SimpleNamespace(option_id="allow_once")],
                session_id="fixture",
                tool_call="build",
            )
        )
        self.assertEqual(
            result.model_dump(exclude_none=True)["outcome"],
            {"outcome": "selected", "option_id": "allow_once"},
        )

    def test_bridge_policy_is_set_from_each_conversations_requested_mode(self):
        # Stop startup as soon as the bridge is attached, before secret binding
        # or process creation. Execute the installed method's actual prefix.
        source = inspect.getsource(ACPAgent._start_acp_server)
        tree = ast.parse(textwrap.dedent(source))
        method = tree.body[0]
        prefix = []
        for statement in method.body:
            prefix.append(statement)
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Attribute) and target.attr == "_client"
                for target in statement.targets
            ):
                break
        else:
            self.fail("Pinned ACP bridge initialization changed")
        for mode in ("read-only", "agent", "agent-full-access"):
            with self.subTest(mode=mode):
                agent = SimpleNamespace(acp_session_mode=mode)
                namespace = {**ACPAgent._start_acp_server.__globals__, "self": agent}
                exec(
                    compile(ast.Module(body=prefix, type_ignores=[]), "bridge-prefix", "exec"),
                    namespace,
                )
                self.assertEqual(agent._client.factory_read_only, mode == "read-only")
