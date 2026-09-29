# IntentMade intent

IntentMade helps an operator turn approved software work into tested changes and
reviewable pull requests, with enough retained context and evidence to understand
what happened and continue when work stops. The factory coordinates implementation,
repair, independent review and publication across registered repositories.

This document describes the desired outcomes and their rationale. The
[specification](spec.md) defines the concrete behavior and constraints, with links
to implementation and checks.

## Direction and conversations

Keep desired outcomes and reasons here, and concrete behavior and constraints in
the specification. Trace links connect each requirement to the outcome it supports.

As the design evolves, update the current problem, decisions, reasons, useful
alternatives and open questions. Keep proposals distinguishable from agreed
changes, then follow the affected requirements into implementation, assertions
and evidence.

The factory currently assumes one trusted operator. Deployment choices determine
runtime isolation, credentials and approval policy. Enterprise identity and a
multi-tenant controller boundary remain outside the current contract.

## Intended outcomes

### Keep the operator in control of work
`intent~im-authorized-work~1`

Operators need the factory to act on eligible, approved work for the intended
repository. A later edit, retry or reply should not silently change what was
authorized or cause the same failed issue to be picked up repeatedly.

Needs: req

Rationale: automation remains useful only while its authority and retained task
identity stay understandable to the person responsible for the repository.

Source: [work authorization](../SECURITY.md#work-authorization-and-retained-state)
and [GitHub scheduling](workflows.md#github-scheduling).

### Turn approved requests into validated changes
`intent~im-deliver-changes~1`

People requesting changes need implementation, bounded repair and validation to
preserve the agreed request and its inputs. Unanswered product questions should
return to the person who can resolve them, and publication should expose a
reviewable draft only after the required checks succeed.

Needs: req

Rationale: carrying the request and verified inputs through repair and continuation
avoids losing the original task while recovering from an unsuccessful attempt.

Agreed direction: projects should remain understandable and usable when people
leave IntentMade for another development tool. Keep intent, specification and
planning in ordinary project artifacts, preserve accepted versions and trace
links, and export continuation/evidence. Add artifacts only when they serve the
factory: a separate REVIEW.md would duplicate its existing review policy and is
not part of this workflow.

Source: [feature work](workflows.md#feature-work), [declared inputs](input-artifacts.md)
and [repair context](repair-context.md).

### Protect credentials, work and execution boundaries
`intent~im-protect-work~1`

Operators need worker activity and imported changes to respect the selected
credential and execution boundaries. Failed work should remain recoverable, and
resource pressure or hostile file structures should not compromise the controller
or silently discard the only usable copy of an attempt.

Needs: req

Rationale: running repository-controlled code makes custody, bounded operations
and recovery part of the factory's usefulness.

Source: [execution boundaries](../SECURITY.md#execution-boundaries),
[resource limits](configuration.md#resource-limits) and
[retained work](operations.md#recover-retained-work).

### Make review and maintenance trustworthy
`intent~im-review-changes~1`

Maintainers need review conclusions grounded in the exact changes and independent
evidence, with findings preserved through reporting. Publishing or retrying a
review and acting on later feedback should respect current source identity,
eligibility and validation results.

Needs: req

Rationale: a review is useful only when maintainers can tell what was reviewed,
which findings remain, and whether later actions still concern that work.

Source: [reviews](reviews.md) and [PR maintenance](workflows.md#pr-maintenance).

### Understand what evidence supports a result
`intent~im-trust-evidence~1`

Operators and reviewers need to connect promises to implementation, assertions
and actual execution, while seeing missing checks and accepted limitations.
Evidence should describe the source, policy and runtime that were checked, and
remain inspectable after the worker is gone.

Needs: req

Rationale: successful commands, complete links and plausible reports alone do
not establish that the intended behavior was checked or achieved.

Source: [traceability](traceability.md), [browser acceptance](configuration.md#browser-acceptance-evidence)
and [execution provenance](execution-provenance.md).

### Run and recover automation predictably
`intent~im-operate-reliably~1`

Operators need concurrent work, startup retries and finite recipes to have bounded,
understandable behavior. Completion records and measurements should preserve
failures and uncertainty so an operator can recover without accidentally replaying
work or mistaking an incomplete attempt for success.

Needs: req

Rationale: unattended operation needs reliable continuation and observable limits,
including when acknowledgement, startup or measurement fails.

Source: [operations](operations.md), [finite automations](finite-automations.md)
and [measurements](measurements.md).
