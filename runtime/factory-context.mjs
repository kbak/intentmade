// Apply at the ACP boundary: Canvas's frozen server has its own embedded SDK.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

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
  const directory = request[selection];
  if (!directory) return config;
  const project = directory.slice("/projects/repos/".length).split("/")[0];
  const guidance = readFileSync("/opt/factory/coordinator.md", "utf8");
  const instructions = `${config.developer_instructions ?? ""}\n\n<factory-coordinator>
${guidance}
Selected factory project: ${JSON.stringify(project)}.
The selected directory ${JSON.stringify(directory)} is its read-only catalog.
Read the selected repository's AGENTS.md and CLAUDE.md as reference guidance.
For implementation, submit this project through the factory workflow described above.
Do not implement in the catalog or in a temporary copy. Preserve the user's existing
specification and authorization when handing off. Coordination runs from /projects;
use the selected catalog's absolute path when inspecting repository files or Git.
</factory-coordinator>`;
  return { ...config, developer_instructions: instructions.trim() };
}
