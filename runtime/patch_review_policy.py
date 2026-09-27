"""Make the pinned Codex read-only mode non-escalating for factory reviews.

The adapter's mode name otherwise means "ask for approval", and the SDK's
default permission bridge automatically grants those requests. Preserve the
native builder/coordinator modes and fail the image build on upstream drift.
"""

import ast
import json
import subprocess
from importlib.metadata import version
from pathlib import Path

ADAPTER = Path("/acp-node/lib/node_modules/@agentclientprotocol/codex-acp")
ADAPTER_OLD = """  static ReadOnly = new _AgentMode(
    "read-only",
    "Ask for approval",
    "Always ask to edit external files and use the internet",
    "standard",
    "on-request",
    "user",
    {
      type: "workspaceWrite",
      writableRoots: [],
      networkAccess: false,
      excludeTmpdirEnvVar: false,
      excludeSlashTmp: false
    },
    "workspace-write"
  );"""
ADAPTER_NEW = """  static ReadOnly = new _AgentMode(
    "read-only",
    "Read-only",
    "Read files without edits, network access, or permission escalation",
    "standard",
    "never",
    "user",
    { type: "readOnly", networkAccess: false },
    "read-only"
  );"""
BRIDGE_OLD = '''        """Auto-approve all permission requests from the ACP server."""
        # Pick the first option (usually "allow once")'''
BRIDGE_NEW = '''        """Deny escalation in read-only conversations; retain native builder behavior."""
        if getattr(self, "factory_read_only", False):
            return RequestPermissionResponse(outcome={"outcome": "cancelled"})
        # Pick the first option (usually "allow once")'''
CLIENT_OLD = "        client = _OpenHandsACPBridge()\n        self._client = client"
CLIENT_NEW = (
    "        client = _OpenHandsACPBridge()\n"
    '        client.factory_read_only = self.acp_session_mode == "read-only"\n'
    "        self._client = client"
)


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Pinned review-policy source changed; review the upstream update")
    return source.replace(old, new, 1)


def patch_adapter(source):
    return replace_once(source, ADAPTER_OLD, ADAPTER_NEW)


def patch_sdk(source):
    source = replace_once(source, BRIDGE_OLD, BRIDGE_NEW)
    source = replace_once(source, CLIENT_OLD, CLIENT_NEW)
    ast.parse(source)
    return source


def main():
    if version("openhands-sdk") != "1.49.6":
        raise RuntimeError("Review-policy patch requires the pinned OpenHands SDK 1.49.6")
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "1.10.0":
        raise RuntimeError("Review-policy patch requires the pinned Codex ACP 1.10.0")
    from openhands.sdk.agent import acp_agent

    sdk = Path(acp_agent.__file__)
    bundle = ADAPTER / "dist/index.js"
    patched_sdk = patch_sdk(sdk.read_text())
    patched_adapter = patch_adapter(bundle.read_text())
    subprocess.run(
        ["/acp-node/bin/node", "--input-type=module", "--check"],
        input=patched_adapter,
        text=True,
        check=True,
    )
    sdk.write_text(patched_sdk)
    bundle.write_text(patched_adapter)


if __name__ == "__main__":
    main()
