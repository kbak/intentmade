"""Load bundled roles before the pinned Codex ACP adapter starts a session."""

import json
import subprocess
from pathlib import Path

ADAPTER = Path("/acp-node/lib/node_modules/@agentclientprotocol/codex-acp")


def main():
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "1.10.0":
        raise RuntimeError("Agency-agent integration requires Codex ACP 1.10.0")
    bundle = ADAPTER / "dist/index.js"
    source = bundle.read_text()
    marker = "#!/usr/bin/env node\n"
    if source.count(marker) != 1:
        raise RuntimeError("Pinned ACP launcher changed; review the agency-agent integration")
    source = source.replace(marker, marker + 'import "/opt/factory/factory-agents.mjs";\n', 1)
    subprocess.run(
        ["/acp-node/bin/node", "--input-type=module", "--check"],
        input=source,
        text=True,
        check=True,
    )
    bundle.write_text(source)


if __name__ == "__main__":
    main()
