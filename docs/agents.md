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
external diff/textconv; paths are restricted to the job, declared inputs and the
configured read-only test-profile directories. Review context identifies those
profile directories; their optional README can point to locally provisioned
project-pinned skills and references. Provision required guidance before review;
the reviewer cannot download it or install another tool version. These references
remain task context and cannot change factory policy.
Inspection is bounded to 2 MiB per file/diff, 10,000 entries and 64 MiB per search, and paged
output. Unavailable evidence must be reported and prevents a complete review.
Ambient repository/user plugins are disabled for factory workers and report
readers so they cannot introduce hooks or additional executors.

## Optional OpenGauss proof workflow

Keep the ordinary workflow as the default. Select `lean-mcp` when an accepted
proof task benefits from local goal, diagnostic or candidate feedback; tool use
is optional. Select `opengauss` when the task calls for its managed proof loop.
Choose the mode before dispatch and retain that choice with the task evidence.

Build the optional image on top of a matching factory image. Record the immutable
image ID behind a local tag before building, or use a registry digest. BuildKit
accepts a local image tag in `FROM`; a bare local `sha256:...` image ID is not a
registry reference.

```sh
docker image inspect intentmade:traceability-matched --format '{{.Id}}'
docker build -f docker/opengauss.Dockerfile \
  --build-arg BASE_IMAGE=intentmade:traceability-matched \
  -t intentmade:opengauss .
```

It includes pinned OpenGauss and `lean4-skills` sources with their MIT licenses,
and an isolated hash-locked `lean-lsp-mcp` environment. The ordinary factory image
does not install these optional dependencies. Select the optional worker image
through the existing DockerWorkspace image setting or Docker Sandboxes Kit, then
add an operator-owned mapping to `config/deployment.json`:

```json
{"implementation_workflows": {"your-lean-project": "opengauss"}}
```

Provision the project's pinned Lean/Lake toolchain on the worker's PATH before
the first LSP call. Keep this project-specific bootstrap in the deployment image;
the optional generic image does not select a Lean version for every project.

This supplements the selected Codex ACP profile; keep its model and reasoning
settings there. It is supported for a single repository on either Codex runtime.
Grouped tasks and native OpenHands profiles fail before worker allocation. Other
repositories keep their usual workflow. Remove the mapping to revert subsequent
workers; refresh workflows after changing deployment settings. Do not enable a
mapping until the optional image is available in that worker runtime. Update the
controller to the matching tooling revision too, so it validates and captures the
new deployment setting before refreshing workflows.

Select `lean-mcp` instead of `opengauss` in the same mapping to provide only the
pinned Lean MCP server. This mode retains the factory's instructions and skills,
without installing the Lean skill or adding managed `/autoprove` guidance. It
uses the same Codex profile, authentication and stage boundaries. Both modes
require the optional runtime. Neither mode changes validation or review gates.

The `opengauss` adapter invokes OpenGauss's pinned managed `/autoprove` instruction generators
at Codex's session boundary, after the actual worktree is known. It supplies the
matching skill and `factory-lean-lsp` MCP server, while retaining OpenHands' native
ACP transport, Codex model/reasoning selection, login/proxy, usage events and
cancellation. New, loaded, resumed and forked sessions use their actual cwd.
It registers an immutable Lean skill link in the existing Codex home, without
replacing an operator skill or modifying authentication/configuration files.
It does not run the interactive Gauss frontend or create another credential home.
The adapter uses pinned internal OpenGauss helpers; pin upgrades require its
protocol probe. This adapted configuration is not a reproduction of FormalQualBench.

The adapter equips one implementation worker; concurrent proof scheduling remains
the coordinator's job. For parallel experiments, freeze common definitions and
exact theorem interfaces, then assign complementary proof obligations and explicit
file ownership. Plan ownership of shared exports, module inventory and canonical
documentation before starting workers. Preserve the supplied per-worker checks and
run full validation of the integrated package; include integration, conflicts and
validation in the comparison. Multiple implementations of one ticket are
alternatives, not cumulative progress.

Only writable implementation uses this workflow. Independent review, reports and
browser QA use their existing stage configuration. The proof workflow preserves
target statements, definitions and assumptions; use accepted proof obligations
and request input when new semantic choices are needed. IntentBond context,
controller checks, independent review and publication rules still apply.
Generated nonsecret context lives in the worker's temporary directory, outside
source, and is removed with the worker. Upstream skills can create project-local
scratch state; the accepted work package and review must keep runtime artifacts
out of source commits.

Run `python /tests/check_opengauss.py` in a disposable optional-image container
with `tests/` mounted read-only at `/tests`, `--network none`, and
`PYTHONPATH=/opt/factory/workflows:/tests`. Add `--lean` with the project
Lean toolchain on PATH to exercise real goal inspection and proof compilation.
The probe checks actual worktree selection, MCP registration, loaded/resumed/forked
sessions and preservation of fixture credentials/configuration. It uses a scripted
local provider and proves protocol behavior, not model quality or subscription access.
Run `python /tests/check_opengauss_lifecycle.py` in the standalone native sandbox
to exercise a scripted backend HTTP failure, missing authentication, explicit
cancellation, and a caller deadline followed by cancellation. It checks that the
managed ACP/MCP descendants terminate, without changing native host credentials.
These controlled probes make no external provider requests. Separately exercise the
factory run deadline and VM cleanup, then one bounded live smoke in a separate
test deployment. Record all
attempts, actual tool use, accepted proofs, elapsed time, available usage and review
effort. Separate proof development, validation and integration costs; account for
coordinator preparation when comparing complete workflows. Shared model/profile
settings and a few successful runs do not establish a parallel speedup. Missing
usage and billed subscription cost remain unknown.

## Conversation activity

Canvas groups consecutive routine Codex/ACP calls, including successful Guardian
approval checks, into collapsed activity rows. Running groups show a short current
action and completion count. Command titles are limited to 80 characters and one
line; expanding the group and then an individual row reveals its complete command
and output. Messages, failed calls, pending approvals and unknown statuses remain
outside these groups. Guardian checks still run and the full event history is
retained. This display change does not reduce the number of tool calls.

The display patch is installed when the runtime image is built. Editing its source
does not update an existing factory; activate it during a separately authorized
runtime update.

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
