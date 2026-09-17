"""Expose native role identity and final results through the pinned ACP bridge."""

import json
import subprocess
from pathlib import Path

ADAPTER = Path("/acp-node/lib/node_modules/@agentclientprotocol/codex-acp")
MARKER = "    const clearRecoveredSessionFailure = async (handler) => {\n"
REPLACEMENT = """    if (params.prompt.some((block) => block.type === "text" && block.text.startsWith("FACTORY_SPECIALIST_REVIEW_V2\\n"))) {
      sessionState.factorySpecialistReview = true;
    }
    const clearRecoveredSessionFailure = async (handler) => {
      if (sessionState.factorySpecialistReview) {
        const session = new ACPSessionConnection(this.connection, sessionState.sessionId);
        await emitFactoryReviewEvidence(this.codexAcpClient, (update) => session.update(update), sessionState.sessionId, sessionState.currentTurnId);
      }
"""


def patch_adapter(source):
    if source.count(MARKER) != 1 or source.count("#!/usr/bin/env node\n") != 1:
        raise RuntimeError(
            "Pinned ACP prompt lifecycle changed; review specialist evidence integration"
        )
    return source.replace(
        "#!/usr/bin/env node\n",
        '#!/usr/bin/env node\nimport { emitFactoryReviewEvidence } from "/opt/factory/factory-review.mjs";\n',
        1,
    ).replace(MARKER, REPLACEMENT, 1)


def main():
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "1.10.0":
        raise RuntimeError("Specialist review requires Codex ACP 1.10.0")
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
