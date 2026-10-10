"""Keep factory evidence and unknown guards around native ACP prompt accounting."""

import json
import subprocess
from pathlib import Path

ADAPTER = Path("/acp-node/lib/node_modules/@agentclientprotocol/codex-acp")


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise RuntimeError("Pinned ACP usage lifecycle changed; review usage integration")
    return source.replace(old, new)


def patch_adapter(source):
    source = replace(
        source,
        "#!/usr/bin/env node\n",
        '#!/usr/bin/env node\nimport { beginFactoryUsage, observeFactoryUsage, factoryUsageSnapshot, endFactoryUsage } from "/opt/factory/factory-usage.mjs";\n',
    )
    source = replace(
        source,
        '      sessionTitleSource: operation === "resume" ? "unknown" : "unset",',
        '      factoryFreshSession: operation === "new",\n      sessionTitleSource: operation === "resume" ? "unknown" : "unset",',
    )
    source = replace(
        source,
        "    const activePrompt = this.trackActivePrompt(params.sessionId);",
        "    const activePrompt = this.trackActivePrompt(params.sessionId);\n    beginFactoryUsage(sessionState);",
    )
    source = replace(
        source,
        "  handleTokenUsageUpdated(params) {\n",
        "  handleTokenUsageUpdated(params) {\n    observeFactoryUsage(this.sessionState, params);\n",
    )
    source = replace(
        source,
        "  buildPromptUsage(sessionState) {\n",
        """  buildPromptUsage(sessionState) {
    const snapshot = factoryUsageSnapshot(sessionState);
    if (sessionState.factoryUsage) sessionState.factoryUsage.response = snapshot;
    if (snapshot.status !== "observed") return null;
""",
    )
    source = replace(
        source,
        "        model_usage: modelUsage\n",
        "        model_usage: modelUsage,\n        factory_usage: factoryUsageSnapshot(sessionState)\n",
    )
    source = replace(
        source,
        "    const promptTokenUsage = sessionState.promptTokenUsage?.usage() ?? null;",
        '    const promptTokenUsage = factoryUsageSnapshot(sessionState).status === "observed"\n'
        "      ? sessionState.promptTokenUsage?.usage() ?? null : null;",
    )
    source = replace(
        source,
        '      logger.log("Prompt completed", { sessionId: params.sessionId });',
        """      try {
        const usageSession = new ACPSessionConnection(this.connection, sessionState.sessionId);
        await endFactoryUsage(sessionState, update => usageSession.update(update), promptWasCancelled || activePrompt.signal.aborted);
      } catch (error) {
        logger.error("Factory usage evidence unavailable", error);
      }
      logger.log("Prompt completed", { sessionId: params.sessionId });""",
    )
    return source


def main():
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "2.2.2":
        raise RuntimeError("Usage integration requires Codex ACP 2.2.2")
    bundle = ADAPTER / "dist/index.js"
    patched = patch_adapter(bundle.read_text())
    subprocess.run(
        ["/acp-node/bin/node", "--input-type=module", "--check"],
        input=patched,
        text=True,
        check=True,
    )
    bundle.write_text(patched)


if __name__ == "__main__":
    main()
