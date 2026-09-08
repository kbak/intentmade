"""Adapt pinned Canvas launchers to the factory's sandbox and catalog workflow."""

import ast
import json
import subprocess
from importlib.metadata import version
from pathlib import Path

ADAPTER = Path("/acp-node/lib/node_modules/@agentclientprotocol/codex-acp")


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Pinned workspace runtime changed; review the upstream update")
    return source.replace(old, new, 1)


def main():
    if version("openhands-sdk") != "1.44.0":
        raise RuntimeError("Workspace runtime patch requires OpenHands SDK 1.44.0")
    if json.loads((ADAPTER / "package.json").read_text())["version"] != "1.10.0":
        raise RuntimeError("Workspace runtime patch requires Codex ACP 1.10.0")
    from openhands.workspace.docker import workspace

    docker = Path(workspace.__file__)
    source = replace_once(
        docker.read_text(),
        "        # Run container\n        run_cmd = [\n",
        "        # The profile is read by this Docker client and sent to the daemon.\n"
        '        if profile := os.environ.get("FACTORY_SECCOMP_PROFILE"):\n'
        '            flags += ["--security-opt", f"seccomp={profile}"]\n\n'
        "        # Run container\n        run_cmd = [\n",
    )
    ast.parse(source)
    docker.write_text(source)

    bundle = ADAPTER / "dist/index.js"
    source = replace_once(
        bundle.read_text(),
        "#!/usr/bin/env node\n",
        "#!/usr/bin/env node\n"
        'import { factorySessionRequest, factorySessionConfig } from "/opt/factory/factory-context.mjs";\n',
    )
    for method in ("newSession", "resumeSession", "loadSession", "forkSession"):
        # Map at the RPC boundary too: the server retains this cwd for later
        # turns and file-change reports, independently of thread creation.
        source = replace_once(
            source,
            f"  async {method}(params) {{\n",
            f"  async {method}(params) {{\n    params = factorySessionRequest(params);\n",
        )
        source = replace_once(
            source,
            f"  async {method}(request"
            + (", onSubscribed" if method in ("resumeSession", "loadSession") else "")
            + ") {\n    const additionalDirectories = readAdditionalDirectories(",
            f"  async {method}(request"
            + (", onSubscribed" if method in ("resumeSession", "loadSession") else "")
            + ") {\n    request = factorySessionRequest(request);\n    const additionalDirectories = readAdditionalDirectories(",
        )
    for call, count in (
        (
            "this.createSessionConfig(request.cwd, additionalDirectories, request.mcpServers ?? [])",
            2,
        ),
        ("this.createSessionConfig(request.cwd, additionalDirectories, request.mcpServers)", 1),
    ):
        old = f"config: await {call},"
        if source.count(old) != count:
            raise RuntimeError("Pinned ACP session configuration changed")
        source = source.replace(old, f"config: factorySessionConfig(await {call}, request),")
    source = replace_once(
        source,
        "createSessionConfig: (cwd, directories, mcpServers) => this.createSessionConfig(cwd, directories, mcpServers),",
        "createSessionConfig: async (cwd, directories, mcpServers) => factorySessionConfig(await this.createSessionConfig(cwd, directories, mcpServers), request),",
    )
    subprocess.run(
        ["/acp-node/bin/node", "--input-type=module", "--check"],
        input=source,
        text=True,
        check=True,
    )
    bundle.write_text(source)


if __name__ == "__main__":
    main()
