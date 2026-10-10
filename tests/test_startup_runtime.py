"""Exercise installed startup patches and the executable that workers actually launch."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    ConversationErrorEvent,
    ConversationExecutionStatus,
)


class StartupRuntimeTests(unittest.TestCase):
    def test_headless_rejected_login_returns_auth_error_without_opening_browser(self):
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        method = re.search(r"  async authenticateWithChatGpt\(\) \{.*?\n  \}", source, re.S).group()
        method += re.search(r"  async hasWorkingChatGptLogin\(\) \{.*?\n  \}", source, re.S).group()
        account_errors = source.split("var INTERNAL_ERROR_CODE =", 1)[1].split(
            "// src/SessionIndexMutations.ts", 1
        )[0]
        script = """
const RequestError = {authRequired: (_data, message) => Object.assign(new Error(message), {code:-32000})};
const logger = {log:()=>{}};
const errorText = error => error.message;
var INTERNAL_ERROR_CODE = ACCOUNT_ERRORS
let loginCalls = 0;
let refreshed = false;
const open_default = async () => {};
class Client { METHOD }
const client = new Client();
client.codexClient = {accountRead: async params => {refreshed=params.refreshToken; if(FIXTURE_ERROR) throw FIXTURE_ERROR; return {account:FIXTURE_ACCOUNT};}, accountLogin: async () => {loginCalls++; return {type:'chatgpt', authUrl:'fixture'};}};
client.awaitNextLoginCompleted = () => Promise.resolve({success:true});
try { console.log(JSON.stringify({result:await client.authenticateWithChatGpt(),loginCalls,refreshed})); }
catch(error) { console.log(JSON.stringify({code:error.code,message:error.message,loginCalls,refreshed})); }
""".replace("METHOD", method).replace("ACCOUNT_ERRORS", account_errors)
        unauthorized = {"code": -32603, "message": "workspace routing discovery unauthorized (401)"}
        unavailable = {"code": -32603, "message": "workspace routing discovery timed out"}
        for headless, account, error, expected_logins in (
            ("1", None, None, 0),
            ("1", {"type": "chatgpt"}, None, 0),
            ("", None, None, 1),
            ("1", None, unauthorized, 0),
            ("1", None, unavailable, 0),
        ):
            with self.subTest(headless=headless, account=account, error=error):
                result = json.loads(
                    subprocess.check_output(
                        [
                            "/acp-node/bin/node",
                            "--input-type=module",
                            "-e",
                            script.replace("FIXTURE_ACCOUNT", json.dumps(account)).replace(
                                "FIXTURE_ERROR", json.dumps(error)
                            ),
                        ],
                        env={**os.environ, "FACTORY_HEADLESS": headless},
                        text=True,
                    )
                )
                self.assertEqual(result["loginCalls"], expected_logins)
                self.assertTrue(result["refreshed"])
                if headless and account is None and error != unavailable:
                    self.assertEqual(result["code"], -32000)
                    self.assertIn("factoryctl codex-login", result["message"])
                else:
                    self.assertTrue(result["result"])

    def test_error_event_precedes_terminal_state(self):
        agent = ACPAgent(acp_command=["fixture-acp"])
        state = SimpleNamespace(
            agent_state={}, secret_registry=None, execution_status=ConversationExecutionStatus.IDLE
        )
        observed = []
        with (
            patch.object(ACPAgent, "_render_suffix", return_value=""),
            patch.object(ACPAgent, "_start_acp_server", side_effect=RuntimeError("startup failed")),
            self.assertRaisesRegex(RuntimeError, "startup failed"),
        ):
            agent.init_state(state, lambda event: observed.append((event, state.execution_status)))
        self.assertEqual(len(observed), 1)
        event, status_when_emitted = observed[0]
        self.assertIsInstance(event, ConversationErrorEvent)
        self.assertEqual(status_when_emitted, ConversationExecutionStatus.IDLE)
        self.assertEqual(state.execution_status, ConversationExecutionStatus.ERROR)

    def test_actual_launcher_uses_patched_python_module_and_preserves_arguments(self):
        launcher = Path("/usr/local/bin/openhands-agent-server")
        self.assertTrue(launcher.read_bytes().startswith(b"#!/bin/sh"))
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / "python"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            python.chmod(0o755)
            output = subprocess.check_output(
                [str(launcher), "--port", "12345"], env={"PATH": directory}, text=True
            )
        self.assertEqual(output.splitlines(), ["-m", "openhands.agent_server", "--port", "12345"])


if __name__ == "__main__":
    unittest.main()
