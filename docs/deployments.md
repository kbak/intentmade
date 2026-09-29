# Deployment choices

IntentMade uses the same implementation, repair, review, evidence and publication
workflows across deployments. The operator chooses the execution environment and
minimum authorization requirements separately from repository workflow policy.

This shared core supports personal workflows and maintainer-operated OSS work
and provides a base for future enterprise integrations. The current controller
still assumes one trusted operator; stronger worker isolation does not make
Canvas safe to share between mutually untrusted users.

## Configuration ownership

| Configuration | Responsibility |
| --- | --- |
| `config/deployment.json` | Operator: worker agent profile, runtime, resource bounds, issue-approval requirement |
| Native Docker Sandbox Kit and credential bindings | Operator: VM resources, storage, network policy and host-managed worker credentials |
| Selected OpenHands agent and LLM profiles | Operator: harness/model selection and credentials (`factory-codex` by default) |
| `config/defaults.json`, `config/repositories/*.json` | Operator: repository workflow defaults and overrides |
| Repository contents, issues and comments | Task input; cannot override deployment settings or grant publication authority |

Repository registrations are operator-owned files. They may customize tests,
approval-label names and schedules, but cannot set `worker_runtime`,
`resource_limits`, `worker_agent_profile` or `authorization`. Deployment settings are excluded from
captured repository job configuration and are not mounted into workers.

The runtime adapter creates a worker and returns an OpenHands workspace. Native
OpenHands conversations and profiles remain in use. DockerWorkspace workers use
OpenHands credential delivery and versioned refresh; Docker Sandboxes workers use
Docker's host-side proxy with placeholders in the VM. Canvas keeps its separate
OpenHands login. IntentMade retains the checks that bind approval, review and
publication to a particular specification or commit.

## Personal use

The [example deployment](../examples/config/deployment.json) uses DockerWorkspace
workers and requires issue approval. Scheduling starts disabled, and the example
workflow defaults select `factory:approved` as the approval label.

For trusted personal intake, explicitly set
`authorization.require_issue_approval: false` in `deployment.json` and
`issue_label: null` in workflow defaults or the repository registration.
Existing installations without `deployment.json` retain their previous behavior;
updating the code does not rewrite operator configuration.

With `issue_label: null`, an enabled scheduler accepts open, unassigned issues
as work requests. Use this only where issue authors are trusted to request work.
Canvas access grants factory control.

## Public issue intake

New installations copied from the examples already require approval. To enable
it in an existing installation, set the following in `config/deployment.json`,
keeping your runtime and resource settings:

```json
{
  "authorization": {
    "require_issue_approval": true
  }
}
```

A minimal [OSS deployment example](../examples/deployments/oss.json) contains
this setting. In `defaults.json` or each enabled repository registration, also
set a nonempty `issue_label`, such as `factory:approved`. Run
`./scripts/factoryctl configure` to validate configuration and refresh the native
automations. Maintainers apply that label after reviewing the issue specification.

Enabled GitHub schedulers without a label are rejected. The current deployment
requirement is also checked when polling, starting issue work, checking approval
and publishing issue-derived changes. A captured configuration with
`issue_label: null` cannot disable the requirement. The existing approval check
still rejects specifications edited at or after label approval.

Repositories with scheduling disabled can keep `issue_label: null` for manual
tasks. Explicit operator submissions and PR reviews use their existing authority
and validation checks; this setting governs issue-derived implementation.

Worker isolation is a separate choice. Either deployment can use DockerWorkspace
or [Docker Sandboxes](docker-sandboxes.md). The VM backend takes its environment
policy from a native Kit; approval settings do not imply a particular runtime.
The controller remains a trusted single-operator service. See
[security assumptions](../SECURITY.md) for GitHub Actions and credential boundaries.

## Existing installations

Move `worker_runtime` and `resource_limits` from `defaults.json` into
`deployment.json` when convenient. Legacy definitions remain supported and are
excluded from job configuration. Defining the same section in both files is an
error, including identical definitions. `authorization` belongs only in
`deployment.json`.

After updating the factory code, rebuild and refresh workflows as described in
[operations](operations.md#update-the-runtime-and-workflows). Already uploaded
automation bundles contain their own workflow code; finish or pause old runs
before relying on newly installed enforcement. Changing JSON cannot update code
already running from an older bundle.

## Extending the shared factory

The shared workflows in `workflows/` own task approval, exact-commit validation,
repair loops, retained evidence and publication. `deployment.py` reads operator
configuration; `sandbox.py` prepares workers through the selected runtime adapter.
Keep runtime policy in native Kit/Compose configuration and agent configuration
in OpenHands rather than introducing another schema for either.

An enterprise deployment would additionally need authenticated organization
identity, authorization and appropriately isolated controller state. Those
integrations should supply verified authority to the shared workflow checks.
There is no enterprise mode or multi-tenant guarantee in these deployment examples.
