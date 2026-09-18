"""Correct prompt usage in pinned ACP without replacing its lifecycle or SDK."""

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
        '#!/usr/bin/env node\nimport { beginFactoryUsage, observeFactoryUsage, factoryPromptUsage, factoryUsageSnapshot, endFactoryUsage } from "/opt/factory/factory-usage.mjs";\n',
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
        "this.buildPromptUsage(sessionState.lastTokenUsage)",
        "this.buildPromptUsage(factoryPromptUsage(sessionState))",
        4,
    )
    source = replace(
        source,
        "    const lastTokenUsage = sessionState.lastTokenUsage;\n    const modelName",
        "    const lastTokenUsage = factoryPromptUsage(sessionState);\n    const modelName",
    )
    source = replace(
        source,
        "        token_count: sessionState.lastTokenUsage,",
        "        token_count: lastTokenUsage,",
    )
    source = replace(
        source,
        "        model_usage: modelUsage\n",
        "        model_usage: modelUsage,\n        factory_usage: factoryUsageSnapshot(sessionState)\n",
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
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "1.10.0":
        raise RuntimeError("Usage integration requires Codex ACP 1.10.0")
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
