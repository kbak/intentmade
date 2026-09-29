# Getting started

Requirements: a Linux host with Docker Engine and Compose, Git, Python 3, GitHub
CLI (`gh`) access to your repositories, and a Codex login for the agents.

Clone the repository and enter the checkout:

```sh
git clone https://github.com/kbak/intentmade.git
cd intentmade
```

Choose directories for configuration, test profiles and runtime data. They do
not need a separate Git repository. For example:

```text
intentmade/              # Tooling, generic examples and tests
my-factory/             # Operator-chosen directory
  config/               # Deployment settings, repositories and workflow policy
  profiles/             # Optional application test adapters
  .factory/             # Ignored credentials, catalogs, task branches and artifacts
```

From the tooling checkout, copy the examples into an empty directory and create
your local settings file:

```bash
mkdir -p ../my-factory
cp -R examples/. ../my-factory/
cp .env.example .env
```

Set `FACTORY_CONFIG_DIR`, `FACTORY_PROFILES_DIR`, and `FACTORY_DATA_DIR` in `.env`
to your chosen paths. All three are required; relative paths resolve from this
checkout. The example settings use the layout above. For an existing
installation, keep its current paths to preserve access to its data.

Replace the example repositories and groups with your own. Set each repository's
test command and required CI checks, and review `config/defaults.json`. Choose
the runtime and issue-approval requirement in `config/deployment.json`; see
[deployment choices](deployments.md) before accepting public issues. Examples
have scheduling disabled and require the `factory:approved` label for issue
work. Set `enabled: true` for repositories you want polled; maintainers apply
the label after reviewing an issue's current specification.

Authenticate `gh`, then initialize the deployment and start the services:

```bash
./scripts/factoryctl init
./scripts/factoryctl up
```

Connect the factory's Codex account, then install the configured automations:

```bash
./scripts/factoryctl codex-login
./scripts/factoryctl configure
```

`configure` imports your GitHub credential into native secret storage and
applies the configured schedules. It creates the configured approval label.

With the default DockerWorkspace backend, `codex-login` shows a device code and saves the completed login directly to
Canvas's encrypted `CODEX_AUTH_JSON` secret. Its temporary CLI directory is
removed afterward. The **Settings → LLM → ChatGPT subscription** card connects
OpenHands' own LLM backend; it does not connect the factory's Codex ACP workers.
Use `./scripts/factoryctl codex-logout` to remove the factory login through native
secret storage. Active workers are not cancelled, but their later refreshes
cannot restore a deleted login or overwrite a newer one.

The optional [Docker Sandboxes backend](docker-sandboxes.md#credentials) uses
host-managed worker OAuth instead. Its `codex-login` opens Docker’s browser
flow; `--canvas` manages the separate Canvas coordinator login.

Choose the factory model in **Settings → Agent → factory-codex**. Use the native
custom model field for the model and reasoning level you want. The
**Default** badge identifies the profile used for new chats; the separate
profile named `default` does not need the same edit. Existing chats retain their
model selection.

Each new implementation, triage, or review worker reads the model from the saved
`factory-codex` profile, including its reasoning suffix. It keeps that selection
for its lifetime and retains its role's permissions (reviews stay read-only).
Editing the profile affects subsequent workers without rebuilding the image.
Rerunning `configure` preserves the existing profile and its model selection.

Open Canvas with `./scripts/factoryctl open`. Set the Git name and email in
**Application settings** before starting a build. Then follow [feature work](workflows.md#feature-work)
or configure [GitHub scheduling](workflows.md#github-scheduling).

Read the [security model](../SECURITY.md) before enabling work from public issues.
See [configuration](configuration.md) for tests, browser QA, groups, and resource limits.
