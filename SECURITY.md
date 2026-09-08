# Single-operator factory

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

An enabled scheduler with `issue_label: null` treats open, unassigned issues as
work requests. Use that mode only for repositories whose issue authors you trust
to request work. A configured `issue_label` provides an optional approval gate
for repositories that accept issues from other contributors. Neither mode adds
an approval prompt to every implementation command. A null daily task limit
removes the daily cap; per-poll batching and repository locks still apply.

## Execution boundaries

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
- The pinned adapter's read-only mode uses a read-only sandbox, no command
  network access, and no escalation. The SDK also refuses permission requests
  for these sessions. Building and interactive coordinator modes retain their
  capabilities. Image builds fail if an upstream version or patch target changes.
  Codex 0.153.4 uses its supported Landlock backend here because Docker's default
  seccomp blocks bubblewrap's user namespaces. No Docker capabilities or seccomp
  exceptions are added. The real sandbox smoke checks reads, denied writes and
  denied network sockets; rerun it when upgrading Codex or the host kernel.

Workers still receive the Codex subscription credential needed to run agents.
Containers, including privileged Docker test daemons, share the Docker host's
Linux kernel.
This is not a hostile multi-tenant execution service or a VM security boundary.
Keep the host, authorized devices, deployment configuration and credentials trusted.

## Work authorization and retained state

The issue title and body are captured before work and checked again before
publication. With a label gate configured, GitHub edit history must predate
the label event; edits require removing and reapplying the label after reviewing
the new specification. An edit in the same timestamp second is ambiguous and is
rejected. Comments are context and do not approve a different specification.

Failed attempts are recorded outside disposable workers under
`workspaces/issue-attempts/`. Pruning recent Canvas history does not retry the
same failed specification/approval. Preserve this directory with task branches
and the native state volume when backing up or moving a deployment.

Task stores validate the actual GitHub fetch and push destinations before reuse.
Changing a registration's repository or base branch requires a new task ID.
Verified existing stores are migrated without discarding retained work.

Configuration enumerates all automation pages before updating or retiring
schedules. Reconfiguring a disabled or removed repository therefore finds its
older scheduler even after many build jobs have accumulated.

## Applying an update

Let active jobs finish before changing the runtime. Then run `./scripts/factoryctl
up` from this checkout, followed by `./scripts/factoryctl configure`. The image,
outer daemon policy and uploaded automation code must be updated together.
Keep volumes and `.factory/`; do not use `down -v`.

See [tests/README.md](tests/README.md) for regression checks. The Docker isolation
smoke uses temporary containers and no production credentials, repositories or
volumes. Runtime patch changes require rebuilding the image before testing.
