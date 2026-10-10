# Security

## Reporting a vulnerability

Use the repository's **Security → Advisories → Report a vulnerability** option
when available. If private reporting is unavailable, open an issue requesting a
private contact channel without including vulnerability details. Do not post
credentials, private logs, or an unpatched exploit in a public issue.

Include the affected revision, a minimal reproducer, the deployment assumptions,
and practical impact.

Runtime logs and handoff exports are private evidence, not sanitized release
artifacts. See [evidence sharing](docs/portable-workflow.md#continue-elsewhere)
before attaching them to an issue or sending them outside the project.

## Single-operator factory

This setup assumes one trusted operator on a Linux host with Docker. Canvas is
bound to localhost by default. For remote access, restrict ingress to the
operator's authenticated devices, for example through Tailscale. Access to Canvas
grants factory control.

## Software-building capabilities

Implementation agents retain full access inside disposable workers. They can
edit the selected repositories, install public dependencies, and run Docker
integration tests. The parent keeps the GitHub credential and publishes draft
PRs only after the configured tests and independent review pass. Merge and
deployment remain operator actions.

Publishing a draft PR pushes a branch to the target repository and may trigger
GitHub Actions. Review that repository's workflows, token permissions, secrets,
runner isolation, and deployment environment approvals before enabling OSS work.
In particular, keep privileged `pull_request_target` jobs from executing PR code.
These controls belong to the target repository; factory review does not sandbox
GitHub-hosted workflows or protect a self-hosted runner after a push.

Standalone requested/manual PR reviews publish one consolidated report from the completed
Alibaba reviewer and its factory-computed verdict to GitHub. PASS submits APPROVE;
blocking findings submit REQUEST_CHANGES. Only the parent has GitHub credentials and can submit;
it rechecks the reviewed commit and CI before posting. Incomplete or stale
reviews cannot approve a PR. Publication does not merge or deploy the change.

New issue work defaults to explicit operator submission (`issue_intake: "manual"`).
The operator submits a repository issue through `factoryctl submit-issue`; the
factory captures its specification before dispatch and rejects changed content
at build and publication. GitHub labels cannot authorize work in this mode.
Scheduling PR follow-up and explicit retained-task replies is independent of
new-issue intake. Public examples start with scheduling disabled.

`issue_intake: "automatic"` explicitly delegates new-work selection to a trusted
repository. Without `issue_label`, open, unassigned issues can request work. With
a label, anyone able to apply it can authorize work; the label is not personal
approval by the factory operator. An automatic scheduler retains task limits,
repository locks and failed-attempt deduplication.

`authorization.require_issue_approval: true` in the operator's deployment settings
requires the label gate for automatic intake at scheduling, build and
publication. Manual operator submissions have their own content-bound authority.
Repository registrations cannot disable this factory-wide automatic-intake gate.
The scheduler checks the current intake registration on each scan. See [deployment choices](docs/deployments.md).

## Execution boundaries

The container and firewall details below describe the default DockerWorkspace
deployment. Docker Sandboxes uses its native VM and Kit policy instead; factory
approval, review, credential and source-transfer checks remain shared.

- The outer Docker daemon uses Unix sockets. Only Canvas has the management
  socket mount and its supplementary group. Job containers receive their own
  test daemon, without the outer socket.
- Firewall rules in the outer daemon's namespace reject worker connections to
  the gateway, other job networks, and private/LAN/link-local/Tailscale addresses
  outside their job. Public internet access and services within the job remain
  available. This means host-local development services must run in the job's
  test stack instead of being reached through the host's LAN address.
- Git commands that consume worker-controlled configuration execute in the
  worker. After teardown, the parent copies a regular Git bundle, validates
  objects and the expected branch, and imports it without force into its trusted
  task store. Git configuration and hooks do not cross that boundary.
- Independent review uses a fresh clone in a new worker after implementation
  exits. Triage also runs in a worker, with a copy of the catalog. Neither runs
  repository tools in the credential-bearing Canvas process.
- Codex review runs one Alibaba Reviewer under a read-only coordinator, using OCR
  delegation with the existing subscription. The adapter obtains role identities,
  parent/child relationships, completion and final messages from native Codex
  thread records. The factory validates its report and file coverage and computes a verdict
  from blocking findings; it does not trust a coordinator's claim of success.
  Minor findings are advisory. Missing or malformed review evidence prevents
  publication. A read-only editing pass groups duplicate findings for publication;
  every original finding must appear in exactly one group. Blocking status and
  severity are computed from the source findings, and saved presentation data is
  bound to the original evidence by a digest. The editor cannot change the verdict.
  Classification, grouping and review completeness still depend on model
  judgment; these reviews do not replace dedicated security scanning.
- Native OpenHands review runs a separate controller-launched Alibaba reviewer
  conversation. The controller records its identity, completed state and validated
  JSON, then applies the same findings and coverage gates. Its environment tool
  provides bounded source reads and fixed Git inspection without arbitrary shell,
  editing, network or delegation. Paths are restricted to controller-selected
  roots. Ambient plugins are disabled for factory workers and report readers;
  profiles cannot add tools or MCP servers to review stages. Native LLM profiles
  use API credentials and currently require DockerWorkspace. See [agents](docs/agents.md).
- The pinned adapter's read-only mode uses a read-only sandbox, no command
  network access, and no escalation. The SDK also refuses permission requests
  for these sessions. Building and interactive coordinator modes retain their
  capabilities. Image builds fail if an upstream version or patch target changes.
  The adapter does not automatically mark opened source directories trusted.
  Native Codex project trust gates repository `.codex` startup configuration;
  disposable workers start with a fresh credential home and no trusted-project
  entries. Repository files remain available to review. Factory roles in the
  native home and explicitly supplied MCP servers remain available. Operator
  home/system configuration is trusted input: explicitly trusting a project
  there permits its native configuration and is outside the untrusted-source
  boundary. Do not add such trust to disposable worker homes.
  Codex 0.160.1 uses its namespace sandbox with the legacy Landlock backend
  disabled. Canvas and disposable workers use a custom seccomp profile based on
  Moby's default, permitting `clone`, `unshare`, `mount`, `umount2`, and `pivot_root`
  for the unprivileged namespace sandbox. No extra Docker capabilities,
  `CAP_SYS_ADMIN`, or host namespaces are granted to Canvas or agent workers.
  The profile's source and exact changes are documented in
  [runtime/codex-seccomp.md](runtime/codex-seccomp.md). The real sandbox smoke
  verifies allowed coordinator edits, denied reviewer writes, protected Git
  metadata, and denied network sockets; rerun it when upgrading Docker, Codex,
  or the host kernel.

The default DockerWorkspace backend delivers the selected model credential into
workers (Codex login or the native LLM profile) and uses containers, including privileged Docker
test daemons, that share the Docker host's Linux kernel. The optional
[Docker Sandboxes backend](docs/docker-sandboxes.md) places each worker and its
test daemon in a VM; its native Kit owns VM resource and network policy.
That backend uses Docker’s host-side credential proxy: worker auth files contain
placeholders and their OpenHands secret store has no `CODEX_AUTH_JSON`. Canvas
keeps its separate credential. A worker can still use authorized model access
through the proxy while its sandbox is running; this does not impose a spend
quota or prevent prompt injection. The trusted operator must not configure
credential passthrough or mount a real credential into a worker.
Use explicit trusted network destinations for this backend: allowing every
hostname plus denying private CIDRs does not prevent private access through DNS.
The [network probe and limitations](docs/docker-sandboxes.md#network-policy)
apply to the effective Kit and global Docker policy.
Both use a trusted controller. Neither configuration provides a hostile
multi-tenant factory service. Keep the host, authorized devices, deployment
configuration and credentials trusted.

## Work authorization and retained state

The issue title and body are captured before work and checked again before
publication. For automatic tasks with a label gate configured, GitHub edit history must predate
the label event; edits require removing and reapplying the label after reviewing
the new specification. An edit in the same timestamp second is ambiguous and is
rejected. Comments are context and do not approve a different specification.

Failed attempts are recorded outside disposable workers under
`workspaces/issue-attempts/`. Pruning recent Canvas history does not retry the
same failed specification/approval. Preserve this directory with task branches
and the native state volume when backing up or moving a deployment.

Factory-wide `resource_limits` in `config/deployment.json` bound parent
bundle/archive imports, new-job disk admission and DockerWorkspace worker
resources. That backend applies Docker CPU, memory and process limits before
credentials or work are supplied; applying the limits must succeed. The VM
backend uses its native Kit for runtime resources. Import and admission settings
do not create a hard disk quota or prune failed jobs. Recover retained work
before manual cleanup. See the
[resource limits](docs/configuration.md#resource-limits) defaults and storage limitations.

Controller writes into worker-visible job directories walk ancestors without
following symlinks and atomically replace file entries. Each browser QA worker
receives a fresh job directory. Imported bundles and generated patches use
bounded Git processes; exceeding the patch limit fails the attempt and preserves
the task branch. Standalone PR reviews bind source and changed-file evidence to
the captured base/head commits, with incomplete inventories blocking publication.

Task stores validate the actual GitHub fetch and push destinations before reuse.
Changing a registration's repository or base branch requires a new task ID.

Configuration enumerates all automation pages before updating or retiring
schedules. Reconfiguring a disabled or removed repository therefore finds its
older scheduler even after many build jobs have accumulated.

## Applying an update

Follow the [update procedure](docs/operations.md#update-the-runtime-and-workflows).
The image, outer daemon policy, and uploaded automation code must be updated
together. Keep volumes and `.factory/`; do not use `down -v`.

See [tests/README.md](tests/README.md) for regression checks. The Docker isolation
smoke uses temporary containers and no production credentials, repositories or
volumes. Runtime patch changes require rebuilding the image before testing.
