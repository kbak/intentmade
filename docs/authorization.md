# Controller authorization with Cedar

Cedar 4.13.0 is the sole decision maker for ten controller gates over current facts.
Docker Sandboxes, agent conversations, evidence validation, repair and GitHub publication
continue through the existing runtime. No temporal engine or event store is needed.

## Current-fact policies

The [policy](../runtime/cedar/policy.cedar) and
[schema](../runtime/cedar/schema.cedarschema) cover:

| Action | Facts that authorize progression |
| --- | --- |
| [`issue:eligible`](#issue-eligibility-rule) | Open, unassigned issue; manual intake or configured approval label |
| [`issue:approval`](#issue-approval-rule) | Known title/body edits precede approval, at the original timestamp precision |
| [`pr:inspect`](#pr-inspection-rule) | Open, non-draft PR |
| [`ci:accept`](#ci-acceptance-rule) | Observed checks exist; required patterns occur; head and merge results are accepted |
| [`feedback:accept`](#feedback-acceptance-rule) | Maintainer permission or allowed bot; generated reviews excluded; discussion comments mention the factory |
| [`resume:task`](#continuation-task-rule) | Task awaits input or has failed |
| [`resume:event`](#continuation-event-rule) | Persisted user message differs from the consumed answer |
| [`resume:answer`](#continuation-answer-rule) | Explicit, nonempty answer is newer than the question |
| [`resume:dispatch`](#continuation-dispatch-rule) | Native continuation scheduler is enabled |
| [`validation:complete`](#validation-completion-rule) | Validated tests, review, browser QA and traceability permit publication |

All gates share [authorization.py](../workflows/authorization.py), one process
bridge, response validation and an atomic receipt writer from the pinned OpenHands
SDK. Review and maintenance share the CI gate. Python collects and validates
GitHub/Canvas facts, timestamps and text, and preserves configured glob matching.
It handles denial and performs authorized actions. Cedar evaluates the policy.

Source snapshots, current-head/thread identity, claims, locks, consumed answers,
feedback deduplication, publication idempotency and rolling repair budgets remain
with existing Python evidence and orchestration. None becomes engine history.
Optional browser QA, explicitly accepted browser infrastructure gaps and
traceability's `review_required` status retain their existing evidence checks.
Blocked independent-review infrastructure and failed tests stop earlier.

## Decision and evidence

The [Rust bridge](../runtime/cedar/src/main.rs) embeds the official
[`cedar-policy` SDK](https://github.com/cedar-policy/cedar), pinned to 4.13.0 with
locked dependencies and no experimental features. It strictly validates the
policy and each request against the same schema using the SDK's public APIs.
It has no duplicate Python or Rust list of action-specific fact types.
A policy change using existing facts needs no Python edit; adding a fact requires
its schema declaration and a controller source for that fact.

One bounded JSON request enters a private stdin pipe. A fresh process evaluates
it and exits, exposing no worker socket or control plane. No store or service is
required. The image bundles the compiled executable, schema, policy and license;
Rust is needed only when building the image. The policy digest covers both schema
and policy and must match the controller's bundled files.

Each evaluated build attempt retains `authorization/attempt-N.json` in its
controller run artifacts. Admission and continuation receipts are retained under
`authorization-decisions-RUN/authorization/REQUEST.json`. Receipts identify the
request, task, attempt, project/repository or complete group sources, current
facts, action, verdict, engine version, policy digest and latency. Local fixture
builds identify their retained repository. No feedback body or reply text is
included. The controller keeps these paths outside worker mounts, uses private
file permissions, and refuses to overwrite a prior receipt.

Receipts report `ALLOW`, `DENY` or `ERROR`. Final-validation denial enters existing
bounded repair; admission and continuation denials withhold the associated action.
Missing, extra or mistyped facts, incomplete group identity, unavailable bridge,
timeout, malformed response, identity/version/digest mismatch, invalid policy,
**any evaluation error**, or unavailable receipt blocks progression. Cedar can
otherwise allow when a different policy errors; this bridge deliberately treats
that case as infrastructure failure. Errors retain failure evidence without
spending code repair attempts. There is no Python fallback or shadow comparison.

## Qualification and deployment

The canonical [test suite](../tests/README.md) runs real compiled policy matrices,
strict request/error probes and the existing issue, approval, review, feedback,
continuation and grouped-publication boundaries. Rust build tests check invalid
policies and an evaluation error alongside another permit. IntentBond binds
required assertions to their executed outcomes. Scripted facts do not establish
live provider behavior or production operation.

Rebuild and qualify the runtime image before following the deployment's normal
drain, backup and update procedure. Do not experiment against an active factory.
Inspect ordinary runs' receipts after an authorized rollout. These gates always
use the bundled policy: repository content and captured settings cannot select
an engine, mode or project bypass. Engine mode settings (`cedar` or `dogwood`) in operator/default configuration
are rejected. Roll back a complete image and its matching configuration if needed.
No sibling deployment changes are needed just to build and test this source.

## Rule traceability

Each implementation artifact below identifies one `@id` in
[policy.cedar](../runtime/cedar/policy.cedar), its action in
[schema.cedarschema](../runtime/cedar/schema.cedarschema), and the existing
behavioral requirement it contributes to. Its `Needs: utest` obligation is
covered by annotations beside the real-engine assertions. The existing
[execution mapping](../tests/oft-links.json) and required execution artifacts
retain those test identities; IntentBond can follow requirement → rule →
executed assertion in either direction.

IntentBond imports the Markdown artifacts below, which explicitly bind each ID
to its Cedar policy annotation and schema action. This mapping is maintained
with the source, rather than extracted by a custom policy parser. Engine-neutral
`im-authorization-*` IDs can retain their lineage across future engine changes.
Review changes to the rule, this mapping and its assertions together. Neither
a complete graph nor a passing test establishes semantic equivalence by itself.
The shared [engine requirement](spec.md#authorize-controller-actions-with-cedar)
continues to cover authority, errors, receipts and source identity. A rule covers
only its decision within a broader promise; evidence validation and action
orchestration remain separately linked Python implementations.

### Validation completion rule
`impl~im-authorization-validation-complete~1`

Policy: `intentmade_validation_complete`, action `validation:complete`.
Contributes the final authorization decision to
[validated publication](spec.md#publish-only-validated-changes-as-drafts).
Assertions: `utest~im-authorization-matrix~1` in [test_authorization.py](../tests/test_authorization.py).

Covers:
- `req~im-publication-gate~1`

Needs: utest

### Issue eligibility rule
`impl~im-authorization-issue-eligible~1`

Policy: `intentmade_issue_eligible`, action `issue:eligible`.
Contributes issue admission to [ownership](spec.md#claim-eligible-issues-and-preserve-prior-ownership)
and [intake authority](spec.md#select-new-issue-intake-explicitly); it does not claim the issue or validate a manual submission snapshot.
Assertions: `utest~im-authorization-issue-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-issue-ownership~1`
- `req~im-issue-intake~1`

Needs: utest

### Issue approval rule
`impl~im-authorization-issue-approval~1`

Policy: `intentmade_issue_approval`, action `issue:approval`.
Contributes edit-versus-approval chronology to
[content-bound approval](spec.md#bind-issue-approval-to-content); history retrieval, timestamp parsing and publication snapshot checks remain in Python.
Assertions: `utest~im-authorization-issue-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-approval-snapshot~1`

Needs: utest

### PR inspection rule
`impl~im-authorization-pr-inspect~1`

Policy: `intentmade_pr_inspect`, action `pr:inspect`.
Contributes the open, non-draft prerequisite to
[PR review and publication checks](spec.md#recheck-review-identity-and-ci-before-posting); it does not validate the captured comparison or authorize posting on its own.
Assertions: `utest~im-authorization-ci-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-pr-publication~1`

Needs: utest

### CI acceptance rule
`impl~im-authorization-ci-accept~1`

Policy: `intentmade_ci_accept`, action `ci:accept`.
Contributes required CI acceptance to
[PR publication checks](spec.md#recheck-review-identity-and-ci-before-posting); review and maintenance share this rule. Check retrieval, glob matching and retained source validation remain in Python.
Assertions: `utest~im-authorization-ci-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-pr-publication~1`

Needs: utest

### Feedback acceptance rule
`impl~im-authorization-feedback-accept~1`

Policy: `intentmade_feedback_accept`, action `feedback:accept`.
Contributes author authority, generated-review exclusion and comment mentions to
[eligible feedback](spec.md#accept-maintenance-feedback-only-from-eligible-sources); current-head, unresolved-thread and superseded-review checks remain in Python.
Assertions: `utest~im-authorization-reply-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-feedback-authority~1`

Needs: utest

### Continuation task rule
`impl~im-authorization-resume-task~1`

Policy: `intentmade_resume_task`, action `resume:task`.
Contributes waiting/failed task eligibility to
[explicit continuation](spec.md#resume-only-from-a-fresh-explicit-answer).
Assertions: `utest~im-authorization-reply-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-explicit-resume~1`

Needs: utest

### Continuation event rule
`impl~im-authorization-resume-event~1`

Policy: `intentmade_resume_event`, action `resume:event`.
Contributes user-message and unconsumed-event eligibility to
[explicit continuation](spec.md#resume-only-from-a-fresh-explicit-answer); retrieving events and persisting consumption remain in Python.
Assertions: `utest~im-authorization-reply-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-explicit-resume~1`

Needs: utest

### Continuation answer rule
`impl~im-authorization-resume-answer~1`

Policy: `intentmade_resume_answer`, action `resume:answer`.
Contributes fresh, explicit, nonempty answer eligibility to
[explicit continuation](spec.md#resume-only-from-a-fresh-explicit-answer); timestamp and answer parsing remain in Python.
Assertions: `utest~im-authorization-reply-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-explicit-resume~1`

Needs: utest

### Continuation dispatch rule
`impl~im-authorization-resume-dispatch~1`

Policy: `intentmade_resume_dispatch`, action `resume:dispatch`.
Contributes the enabled-schedule decision to
[explicit continuation](spec.md#resume-only-from-a-fresh-explicit-answer); reading the current schedule and performing deduplicated dispatch remain in Python.
Assertions: `utest~im-authorization-reply-admission~1` in [test_authorization_admission.py](../tests/test_authorization_admission.py).

Covers:
- `req~im-explicit-resume~1`

Needs: utest
