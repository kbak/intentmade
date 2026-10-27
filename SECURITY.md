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
task exclusion, short scan locks, bounded worker capacity and failed-attempt deduplication.

`authorization.require_issue_approval: true` in the operator's deployment settings
requires the label gate for automatic intake at scheduling, build and
publication. Manual operator submissions have their own content-bound authority.
Repository registrations cannot disable this factory-wide automatic-intake gate.
The scheduler checks the current intake registration on each scan. See [deployment choices](docs/deployments.md).

## Execution boundaries

The [architecture](docs/architecture.md) shows both runtime layouts; the
[threat model](docs/threat-model.md) records assets, invariants, concrete mounts,
credential flows, attacker scenarios and verification obligations.

- **DockerWorkspace:** agent containers and privileged Docker test daemons share
  the host kernel. Only Canvas receives the outer management socket. Workers use
  their own test daemon; the outer firewall rejects gateway, cross-job and private
  network access while allowing public internet and same-job services.
- **Docker Sandboxes:** each worker and test daemon occupy a local VM. The native
  Kit and global Docker policy own resource/network enforcement. Review the
  [hostname/DNS limitations and network probe](docs/docker-sandboxes.md#network-policy).
  DockerWorkspace resource limits do not configure these VMs.
- **Credentials:** DockerWorkspace supplies the selected real Codex credential
  or API-backed native LLM profile. Docker Sandboxes supplies host-managed proxy
  access with placeholder worker authentication; Canvas keeps a separate login.
  Do not enable credential passthrough or mount real host credentials into sbx
  workers. Proxy use still grants model access and does not impose a spend quota.
- **Source and review:** mutable Git commands run in workers; bounded bundles
  are imported after teardown without worker Git configuration/hooks. Independent
  review starts from fresh retained sources. Controller-validated reviewer
  execution, findings and file coverage determine the verdict; report editing
  cannot weaken blockers. Model judgment is not a dedicated security audit.
- **Stage permissions:** Codex review is read-only, without command networking or
  escalation; native OpenHands review uses only bounded source/Git inspection.
  Profiles and ambient repository plugins cannot expand review tools. Do not add
  trusted-project entries to disposable Codex homes. The default agent-container
  [seccomp profile](runtime/codex-seccomp.md) enables Codex's namespace sandbox
  without granting host namespaces or `CAP_SYS_ADMIN` to those agent containers.

Keep the host, authorized devices, configuration, runtime images and credentials
trusted. Neither backend provides hostile multi-tenant controller isolation.
Rerun the applicable native probes after runtime, policy or host-kernel changes;
source inspection and image-build patch guards do not establish deployed isolation.

## Work authorization and retained state

Issue title/body snapshots are rechecked before publication. With automatic
label-gated intake, edits must precede the approval event; same-second ambiguity
or unavailable history fails closed. Comments do not approve a changed issue.
See [intake and continuation](docs/workflows.md) and
[the Cedar gates](docs/authorization.md). Cedar decides from controller-validated
facts; claims, locks, consumed answers and publication receipts remain separate
controller state.

Preserve `workspaces/issue-attempts/`, task stores, dispatch/retirement receipts,
artifact directories and the native state volume when backing up or restoring.
Deleting receipts can remove replay protection. Runtime limits and low-disk
admission are not hard disk quotas and do not prune failed work. A host crash
may require manual reconciliation before restarting work.

Controller writes into job directories do not follow worker-created links.
Imported Git data and patches use bounded processes. Retained task identity
checks actual GitHub destinations; changing repository or base branch requires a
new task ID. See [custody and recovery invariants](docs/threat-model.md#assets-and-invariants),
[resource limits](docs/configuration.md#resource-limits), and
[retained-work recovery](docs/operations.md#recover-retained-work).

## Applying an update

Follow the [update procedure](docs/operations.md#update-the-runtime-and-workflows).
The image, outer daemon policy, and uploaded automation code must be updated
together. Keep volumes and `.factory/`; do not use `down -v`.

See [tests/README.md](tests/README.md) for regression checks. The Docker isolation
smoke uses temporary containers and no production credentials, repositories or
volumes. Runtime patch changes require rebuilding the image before testing.
