// OpenHands relocates CODEX_HOME for each subscription credential binding.
// Run before the ACP adapter starts Codex so every session sees the image's roles.
import { constants, copyFileSync, mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export function installFactoryAgents(
  codexHome = process.env.CODEX_HOME || join(homedir(), ".codex"),
  source = "/opt/factory/agency-agents/agents",
  reviewers = "/opt/factory/reviewers/agents",
) {
  const destination = join(codexHome, "agents");
  mkdirSync(destination, { recursive: true });
  for (const file of readdirSync(source).filter((name) => name.endsWith(".toml"))) {
    try {
      copyFileSync(join(source, file), join(destination, file), constants.COPYFILE_EXCL);
    } catch (error) {
      // Keep any operator customization when a durable home is reused.
      if (error.code !== "EEXIST") throw error;
    }
  }
  // Factory-owned review identities are versioned with the runtime. Refresh
  // these even in a durable credential home so stale procedures cannot persist.
  for (const file of readdirSync(reviewers).filter((name) => name.endsWith(".toml"))) {
    copyFileSync(join(reviewers, file), join(destination, file));
  }
}

installFactoryAgents();
