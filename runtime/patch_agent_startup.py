"""Keep unattended authentication noninteractive and startup failures observable."""

import ast
import json
import subprocess
from importlib.metadata import version
from pathlib import Path

ADAPTER = Path("/acp-node/lib/node_modules/@agentclientprotocol/codex-acp")
AUTH_OLD = """    if (await this.hasWorkingChatGptLogin()) {
      return true;
    }
    const loginCompletedPromise = this.awaitNextLoginCompleted();"""
AUTH_NEW = """    if (await this.hasWorkingChatGptLogin()) {
      return true;
    }
    if (process.env.FACTORY_HEADLESS === "1") {
      throw RequestError.authRequired(undefined,
        "The factory's saved Codex login was rejected. Run factoryctl codex-login, then resume the task.");
    }
    const loginCompletedPromise = this.awaitNextLoginCompleted();"""
TIMEOUT_OLD = """                        if method_id == "chat-gpt":
                            raise ACPFileCredentialNeedsReauthError(
                                "ChatGPT authentication did not complete in time. "
                                "Please sign in again."
                            ) from exc
"""
EVENT_OLD = """                state.execution_status = ConversationExecutionStatus.ERROR
                on_event(
                    ConversationErrorEvent(
                        source="agent",
                        code=_classify_acp_init_error(e),
                        detail=_acp_error_detail(e, state.secret_registry),
                    )
                )"""
EVENT_NEW = """                on_event(
                    ConversationErrorEvent(
                        source="agent",
                        code=_classify_acp_init_error(e),
                        detail=_acp_error_detail(e, state.secret_registry),
                    )
                )
                state.execution_status = ConversationExecutionStatus.ERROR"""


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise RuntimeError("Pinned agent startup changed; review the upstream update")
    return source.replace(old, new)


def patch_adapter(source):
    return replace(source, AUTH_OLD, AUTH_NEW)


def patch_sdk(source):
    # A slow endpoint is not evidence that the saved login is invalid.
    source = replace(source, TIMEOUT_OLD, "")
    # Remote clients must receive the reason before seeing a terminal state.
    source = replace(source, EVENT_OLD, EVENT_NEW)
    ast.parse(source)
    return source


def main():
    if version("openhands-sdk") != "1.54.0":
        raise RuntimeError("Startup patch requires OpenHands SDK 1.54.0")
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "2.2.2":
        raise RuntimeError("Startup patch requires Codex ACP 2.2.2")
    from openhands.sdk.agent import acp_agent

    sdk = Path(acp_agent.__file__)
    bundle = ADAPTER / "dist/index.js"
    source = patch_adapter(bundle.read_text())
    patched_sdk = patch_sdk(sdk.read_text())
    subprocess.run(
        ["/acp-node/bin/node", "--input-type=module", "--check"],
        input=source,
        text=True,
        check=True,
    )
    sdk.write_text(patched_sdk)
    bundle.write_text(source)


if __name__ == "__main__":
    main()
