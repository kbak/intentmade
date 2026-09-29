# Models, roles, and skills

## Model selection

Workers use the saved OpenHands agent profile named by `worker_agent_profile`
in the operator's `config/deployment.json`. The default is `factory-codex`,
preserving existing installations. Codex ACP and native OpenHands are supported;
other ACP harnesses are rejected before a worker starts.

Choose the model in Canvas **Settings → Agent** and its referenced LLM profile
for native OpenHands, or the model/reasoning field for Codex. Each new worker
captures the profile and model settings for its lifetime. Changing them affects
subsequent workers without rebuilding. Existing report chats retain their saved
agent. `configure` preserves the selected profile and activates it for new chats.

The **Default** badge identifies the profile used for new chats; the profile
named `default` is a separate profile. Existing chats retain their saved model.
Selecting another provider for a Canvas chat does not change automated workers;
the deployment's named profile determines their harness.

Connect the workers with `./scripts/factoryctl codex-login`. The **Settings →
LLM → ChatGPT subscription** card connects OpenHands' own LLM backend, not the
factory's ACP workers. With Docker Sandboxes, use `codex-login --canvas`
for the separate coordinator login. See [setup](getting-started.md).

### Native OpenHands workers

1. Save an API-backed LLM profile in Canvas with the desired provider, model and
   credential. Native subscription login is not transferred to disposable workers;
   use Codex ACP for the subscription path.
2. Save an OpenHands agent profile, for example `factory-native`, referencing
   that LLM profile.
3. Set `"worker_agent_profile": "factory-native"` in `config/deployment.json`
   and use `"worker_runtime": {"backend": "docker"}`. Native OpenHands with
   Docker Sandboxes is not supported by the current Codex proxy Kit and fails
   before worker creation.
4. Rebuild the runtime and run `./scripts/factoryctl configure` as described in
   [operations](operations.md#update-the-runtime-and-workflows).

The factory resolves profiles with OpenHands' native resolver. Only the selected
LLM credential is sent with native agents; Canvas secrets and the Codex credential
are not copied. Worker provenance records profile identity, revision and model,
without credentials. Revert `worker_agent_profile` to `factory-codex` to select
the existing harness for subsequent workers.

Implementation uses the native profile's execution tools. Workflow skills and
explicit browser QA MCP configuration are supplied by the controller. Profiles
cannot add MCP servers or change permissions for review stages. Automatic model
switching and critics are disabled in factory stages so the captured model and
existing validation/review policy remain authoritative.

Native reviewers run as separate OpenHands conversations, with the pinned Alibaba
procedure and controller-recorded completion/results. Their only environment tool
reads, lists and searches source or inspects captured Git commits. It cannot run
shell commands, edit files, contact services or delegate. Git inspection disables
external diff/textconv; paths are restricted to the job and declared inputs.
Inspection is bounded to 2 MiB per file/diff, 10,000 entries and 64 MiB per search, and paged
output. Unavailable evidence must be reported and prevents a complete review.
Ambient repository/user plugins are disabled for factory workers and report
readers so they cannot introduce hooks or additional executors.

## Roles

For Codex, the runtime installs roles from
[agency-agents](https://github.com/msitarzewski/agency-agents). Request one in
Canvas or task workers, for example: “Use the Frontend Developer agent to inspect
this component.” Roles inherit the parent session's model and permissions.

Source revisions and checksums are pinned in
[docker/runtime.Dockerfile](../docker/runtime.Dockerfile). Uncached builds need
GitHub access. Rebuilding after a pin change updates roles for new sessions.
Automatic review uses the separate [Alibaba Reviewer procedure](reviews.md).

## Workflow skills

The procedures in [workflows/skills](../workflows/skills) guide implementation,
review, browser QA, and report writing. Edit those files to change the procedures,
then [rebuild and refresh workflows](operations.md#update-the-runtime-and-workflows).
Existing conversations retain their saved instructions.

[IntentBond integration](traceability.md) adds requirements guidance and checks
only for opted-in repositories. Additional skills do not independently authorize
work or change the role's execution permissions.
