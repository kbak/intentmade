"""Exercise installed startup patches and the executable that workers actually launch."""

import inspect
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from openhands.sdk.agent.acp_agent import ACPAgent


class StartupRuntimeTests(unittest.TestCase):
    def test_headless_rejected_login_returns_auth_error_without_opening_browser(self):
        source = Path(
            "/acp-node/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js"
        ).read_text()
        method = re.search(r"  async authenticateWithChatGpt\(\) \{.*?\n  \}", source, re.S).group()
        script = """
const RequestError = {authRequired: (_data, message) => Object.assign(new Error(message), {code:-32000})};
let loginCalls = 0;
const open_default = async () => {};
class Client { METHOD }
const client = new Client();
client.codexClient = {accountRead: async () => ({account: ACCOUNT}), accountLogin: async () => {loginCalls++; return {type:'chatgpt', authUrl:'fixture'};}};
client.awaitNextLoginCompleted = () => Promise.resolve({success:true});
try { console.log(JSON.stringify({result:await client.authenticateWithChatGpt(),loginCalls})); }
catch(error) { console.log(JSON.stringify({code:error.code,message:error.message,loginCalls})); }
""".replace("METHOD", method)
        for headless, account, expected_logins in (
            ("1", None, 0),
            ("1", {"type": "chatgpt"}, 0),
            ("", None, 1),
        ):
            with self.subTest(headless=headless, account=account):
                result = json.loads(
                    subprocess.check_output(
                        [
                            "/acp-node/bin/node",
                            "--input-type=module",
                            "-e",
                            script.replace("ACCOUNT", json.dumps(account)),
                        ],
                        env={**os.environ, "FACTORY_HEADLESS": headless},
                        text=True,
                    )
                )
                self.assertEqual(result["loginCalls"], expected_logins)
                if headless and account is None:
                    self.assertEqual(result["code"], -32000)
                    self.assertIn("factoryctl codex-login", result["message"])
                else:
                    self.assertTrue(result["result"])

    def test_error_event_precedes_terminal_state(self):
        source = inspect.getsource(ACPAgent.init_state)
        self.assertLess(
            source.index("code=_classify_acp_init_error"),
            source.index("state.execution_status = ConversationExecutionStatus.ERROR"),
        )

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
