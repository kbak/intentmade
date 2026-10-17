// Apply at the ACP boundary: Canvas's frozen server has its own embedded SDK.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";

const selection = Symbol("factory-selected-directory");

export function factorySessionRequest(request) {
  const directory = resolve(request.cwd);
  if (process.env.FACTORY_COORDINATOR !== "1" || !directory.startsWith("/projects/repos/")) {
    return request;
  }
  // The catalog stays selected in Canvas. Codex coordinates from a writable
  // directory so its protected-path placeholders never touch the read-only mount.
  return { ...request, cwd: "/projects", [selection]: directory };
}

export function factorySessionConfig(config, request) {
  if (process.env.FACTORY_OPENGAUSS) {
    const mode = process.env.FACTORY_OPENGAUSS;
    if (!["autoprove", "lean-mcp"].includes(mode)) {
      throw new Error("OpenGauss preparation failed; unsupported proof workflow");
    }
    const prepared = spawnSync("python", ["/opt/factory/opengauss_runtime.py", request.cwd, mode], {
      encoding: "utf8", timeout: 15000, maxBuffer: 1024 * 1024,
    });
    if (prepared.error || prepared.status !== 0) {
      throw new Error("OpenGauss preparation failed; check the pinned proof runtime and Lean project");
    }
    const workflow = JSON.parse(prepared.stdout);
    // Use the upstream session hook: cwd is the actual new/resumed worktree,
    // not the worker server's cwd or the repository used to allocate a worktree.
    return {
      ...config,
      developer_instructions: `${config.developer_instructions ?? ""}\n\n${workflow.instructions}`,
      skills: { ...(config.skills ?? {}), config: [...(config.skills?.config ?? []), ...workflow.skills] },
      mcp_servers: { ...(config.mcp_servers ?? {}), "factory-lean-lsp": workflow.mcp },
    };
  }
  const directory = request[selection];
  if (!directory) return config;
  const project = directory.slice("/projects/repos/".length).split("/")[0];
  const guidance = readFileSync("/opt/factory/coordinator.md", "utf8");
  const instructions = `${config.developer_instructions ?? ""}\n\n<factory-coordinator>
${guidance}
Selected factory project: ${JSON.stringify(project)}.
The selected directory ${JSON.stringify(directory)} is its read-only catalog.
Read the selected repository's AGENTS.md and CLAUDE.md as reference guidance.
Before design work, read its current discussion context with:
python /opt/factory/configure.py discussion ${JSON.stringify(project)}
If the discussion selects a different repository or group, read that selection's context.
For implementation, submit this project through the factory workflow described above.
Do not implement in the catalog or in a temporary copy. Preserve the user's existing
specification and authorization when handing off. Coordination runs from /projects;
use the selected catalog's absolute path when inspecting repository files or Git.
</factory-coordinator>`;
  return { ...config, developer_instructions: instructions.trim() };
}
