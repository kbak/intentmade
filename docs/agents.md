# Models, roles, and skills

## Model selection

Choose the model in Canvas **Settings → Agent → factory-codex**, including any
supported reasoning suffix. Each new implementation, triage, or review worker
reads that saved profile and keeps its selection for its lifetime. Changing the
profile affects subsequent workers without rebuilding. `configure` preserves it.

The **Default** badge identifies the profile used for new chats; the profile
named `default` is a separate profile. Existing chats retain their saved model.
Selecting another provider for a Canvas chat does not change automated workers,
which use Codex through ACP.

Connect the workers with `./scripts/factoryctl codex-login`. The **Settings →
LLM → ChatGPT subscription** card connects OpenHands' own LLM backend, not the
factory's ACP workers. With Docker Sandboxes, use `codex-login --canvas`
for the separate coordinator login. See [setup](getting-started.md).

## Roles

The runtime installs roles from
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
