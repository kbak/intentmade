# Documentation

## Understand the system

- [Architecture](architecture.md) — current components, control/execution planes, Docker and VM layouts, lifecycle and future scaling boundaries.
- [Threat model](threat-model.md) — protected assets, invariants, source-backed controls, attacker scenarios and proposed tenant test properties.
- [Security policy](../SECURITY.md) — operator responsibilities, deployment assumptions and vulnerability reporting.

Keep requirements in [spec.md](spec.md), intent in [intent.md](intent.md), and
commands/configuration in the relevant guide below. Architecture explains how
the pieces fit; the threat model explains what each boundary must protect.
Link to those authoritative homes rather than copying their full contracts.

## Use the factory

- [Getting started](getting-started.md) — install, authenticate, and open Canvas.
- [Configuration](configuration.md) — repositories, tests, browser QA, groups, and resource limits.
- [Deployment choices](deployments.md) — shared core, personal and OSS deployments, and enterprise extension boundaries.
- [Workflows](workflows.md) — feature builds, issue scheduling, replies, and PR maintenance.
- [Portable workflow](portable-workflow.md) — accepted intent/spec/plan artifacts, source-bound review and exportable continuation.
- [Reviews](reviews.md) — independent review, GitHub verdicts, and optional security audits.
- [Agents](agents.md) — model selection, roles, and workflow skills.
- [Operations](operations.md) — updates, backups, recovery, and observability.

## Requirements and evidence

- [Intent](intent.md) — intended outcomes, rationale and conversation guidance.
- [Specification](spec.md) — maintained requirements and their code/test links.
- [Authorization](authorization.md) — Cedar rules, controller facts and decision receipts.
- [Traceability](traceability.md) — configure IntentBond and understand the validation stages.
- [Traceability agent API](traceability-agent-api.md) — guidance and command helpers.
- [Scope identity](scope-identity.md) — capture and verify the selected policy.
- [Traceability retention](traceability-retention.md) — retain feedback and controller checks.
- [Input artifacts](input-artifacts.md) — provide explicit task inputs.
- [Repair context](repair-context.md) — carry verified context into fresh attempts.
- [Execution provenance](execution-provenance.md) — record image, source, and runtime identity.
- [Measurements](measurements.md) — inspect task timing, usage, and outcomes.
- [Usage accounting](usage-accounting.md) — interpret provider counters and their limits.
- [Finite automations](finite-automations.md) — register trusted one-shot operator recipes.

## Contribute

- [Contributing](../CONTRIBUTING.md) — development setup and change guidelines.
- [Testing](../tests/README.md) — regressions and runtime probes.
- [Container sandbox](../runtime/codex-seccomp.md) — seccomp configuration and upstream attribution.
- [Docker Sandboxes](docker-sandboxes.md) — optional native v2 Kit and VM worker adapter.
