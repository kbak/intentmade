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

## Manual intake (default)

New issue work requires an explicit operator submission:

```sh
./scripts/factoryctl submit-issue PROJECT NUMBER
```

`issue_intake` defaults to `manual` when omitted. Submission captures the issue's
current title and body; later edits require resubmission. Manual intake ignores
`issue_label` and does not create GitHub labels. Use `submit` for file-based
specifications. Canvas access grants factory control.

`enabled: true` schedules PR maintenance, requested reviews and explicit
replies to retained tasks. It does not enable automatic new-issue intake.

## Automatic intake for trusted repositories

Set `"issue_intake": "automatic"` in the chosen repository registration. For
unlabeled intake, set `authorization.require_issue_approval: false` in the
operator's `deployment.json` and omit `issue_label` or set it to `null`.
This authorizes eligible open, unassigned issues to request work. Only enable it
where you trust issue authors to spend your factory's resources.

Automatic intake supports a label gate. With
`authorization.require_issue_approval: true`, each enabled automatic repository
must configure a nonempty `issue_label`. Maintainers apply that label after
reviewing the specification; edits at or after that approval require relabeling.
This delegates authorization to repository label editors. Manual submissions do
not require this label, even when the factory-wide automatic-intake gate is on.

Worker isolation is a separate choice. Either deployment can use DockerWorkspace
or [Docker Sandboxes](docker-sandboxes.md). The VM backend takes its environment
policy from a native Kit; approval settings do not imply a particular runtime.
The controller remains a trusted single-operator service. See
[security assumptions](../SECURITY.md) for GitHub Actions and credential boundaries.

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
