# Build and maintain changes

## Feature work

Select a repository under `/projects/repos` in Canvas to browse its read-only
catalog. Discuss the scope, tradeoffs, and acceptance criteria, then explicitly
approve the specification and ask to implement it. The coordinator can submit
an intent/spec/plan package for substantial work using the [portable workflow](portable-workflow.md).
For bounded changes, the approved issue/request and brief inline planning suffice.
You can supply an approved file directly:

```sh
./scripts/factoryctl submit example-app ./approved-spec.md --run
```

Omit `--run` to inspect the job in **Automate** first. Submit a
[group name](configuration.md#repository-groups) to change multiple repositories.
For declared databases, archives, or evidence, see [input artifacts](input-artifacts.md).

Each task implements the specification in a disposable worker, runs the configured
tests, performs applicable [browser QA](configuration.md#browser-acceptance-evidence),
and requests an independent [review](reviews.md). Failed validation enters a
bounded repair loop, with one repair attempt by default. Passing changes become
draft PRs; merging and deployment remain manual.

Continue with `--task TASK_ID` to preserve the task branch and original base in a
fresh worktree. Add `--no-publish` to retain results locally. Failed tasks also
retain their branches. [Traceability](traceability.md) optionally carries agreed
requirements through implementation, tests, and review.

## See task progress in Canvas

Open the run in **Automate** to see its current stage, time in that stage, total
elapsed time and repair attempt. The progress line refreshes every 30 seconds
while the task is active. Open its linked task conversation for a table of stages
and their results; the table is posted on stage changes, with elapsed times as of
that update. Timer refreshes do not add chat messages or start an assistant.

Builds show implementation, project tests, applicable browser QA, independent
review and publication. Standalone reviews show review and publication. Checks
that are not needed say **Not required**; stages not reached remain **Waiting**.
Repairs reset the current table, retaining the previous attempt in the conversation
and task artifacts. When work stops for a question, the summary identifies the
needed input and reminds you to reply `resume: YOUR ANSWER`.

Each task retains `stage-progress.json` and `stage-progress.md` alongside its
other artifacts. These are progress summaries; validation and publication still
use the existing controller gates. An unavailable Canvas update does not change
the task result. Previously uploaded workflows need the normal `factoryctl configure`
refresh to receive this reporting code.

## GitHub scheduling

GitHub transport, pagination, issue/PR discovery, source downloads, pushes and
draft PR creation reuse the checksum-pinned OpenHands Extensions helpers in
`upstream.lock.json`. Both issue and review helpers import the same upstream
`github_client` module. Native OpenHands provides scheduling and run tracking;
the factory controller applies authorization, validation and publication policy.
The complete prebuilt Issue-to-PR workflow gives the implementation conversation
publication access and reviews the resulting PR afterward. The factory instead
keeps that credential in the parent and validates before pushing.

Durable task, review, continuation and maintenance receipts keep their existing
filesystem locations and JSON records. Writes reuse the SDK's atomic file writer
for complete replacement, unique temporary files, flush-before-replace and failure
cleanup. Receipt/report files retain operator-readable permissions. See the
[component reuse decisions](runtime-patches.md#workflow-reuse-decisions) for the
remaining conversation, publication and native-KV integration tradeoffs.

An enabled repository has a **Factory — NAME** automation. Defaults poll every
ten minutes, allow two task attempts per poll, and have no daily cap. Set
`daily_tasks` to a positive integer for a UTC-day limit, or `null` for no cap.
Work is serialized per repository; unrelated repositories can run concurrently.

| Work | Eligibility |
| --- | --- |
| Published PR maintenance | PRs with this instance's publication receipt, including drafts. Handles eligible feedback, CI failures, and updated base branches. |
| Requested PR review | Open, non-draft PRs requested from the connected GitHub account or its active teams, with passing CI and no submitted human review of the current commit. |
| Follow-up review | A new eligible commit after this factory's outstanding changes request. Approval or dismissal ends automatic follow-up. |
| Issue implementation | Automatic intake only: open, unassigned issues, oldest first, subject to any configured label policy. |
| Issue proposals | Automatic intake only: with an approval label configured, changed unassigned issues without the label may receive Canvas proposals when capacity is available. |

Maintenance takes priority over requested reviews and new issues. The scheduler
does not independently review its own published PRs; repairs still pass the
normal tests and independent review before pushing.

### Issue authorization

New issue intake defaults to `issue_intake: "manual"`. Submit a reviewed issue:

```sh
./scripts/factoryctl submit-issue PROJECT NUMBER
```

This captures the current title and body before dispatching the task. No label is
created or checked. An edit before execution or publication blocks the task;
resubmit to authorize the updated specification. `retry-issue` continues only the
same specification. `enabled` controls scheduled PR follow-up and retained-task
replies independently of manual submission.

Set `issue_intake: "automatic"` explicitly for trusted repositories. With
`issue_label: null` and the deployment's `require_issue_approval: false`, an enabled
scheduler accepts open, unassigned issues. Optional labels provide maintainer-controlled filtering and approval-history
checks. They delegate
selection to repository label editors, not exclusively to the factory operator.
See the [deployment choices](deployments.md).

The workflow claims the issue for the configured assignee and captures its title
and body as the specification. Publication requires that specification to remain
unchanged. On failure, an assignment acquired by that run is released; an existing
assignment is retained. Failed specifications are recorded to prevent retries on
every poll. Empty or busy scans report **Skipped**.

### Questions and retries

Unanswered product questions stop work with **NEEDS_INPUT**. Each task has a
persistent Canvas conversation with the result, questions, and evidence. Reply:

```text
resume: YOUR ANSWER
```

The native **Resume** automation queues the continuation immediately, waiting for
any active repository run. It consumes the reply once under the repository lock;
the scheduled scan provides a fallback. Pausing the repository schedule also
prevents new reply dispatches. Ordinary chat messages do not restart work.

Only recorded tasks are eligible: assignment to the same GitHub account alone
does not authorize continuation. An edited specification invalidates a pending
continuation and must satisfy the ordinary eligibility rules again.

To retry after resolving a failure or to provide an answer from a file:

```sh
./scripts/factoryctl retry-issue example-app 123
./scripts/factoryctl retry-issue example-app 123 --answer-file ./answer.md
```

For retained workspaces and cleanup failures, see [recovery](operations.md#recover-retained-work).

## PR maintenance

### CI repairs

Published PRs are monitored for CI failures. Repairs receive annotations and
available job logs, update against the current base, and run configured tests
and independent review before pushing. Each worker and reviewer can read the
retained test command and complete logs. Concurrent changes to the PR head,
branch ownership, or base withhold publication.

`ci_repair_attempts` defaults to three consecutive repairs per PR, independently
of the daily cap. Successful CI resets the counter. Verified GitHub artifact-service
failures are retried without code edits and recorded to avoid duplicate retries.
Unresolved failures return to the task conversation for an explicit `resume:`.

### Review feedback

With `pr_feedback: true`, maintenance collects submitted `CHANGES_REQUESTED` and
`COMMENTED` reviews on the current commit, plus unresolved, non-outdated inline
comments and replies. General discussion comments require `@openhands` or
`@openhands-agent`. Humans need current write, maintain, or admin permission;
bot logins must appear in `pr_feedback_bots`, whose default is empty.

Factory reviews and review bodies superseded by approval are excluded. Feedback
is coalesced per poll and rechecked before work and publication. Each feedback
revision is recorded before work; repeated polls do not repeat a repair. Failed
or interrupted repairs require `resume:`. New requirements outside the approved
task produce **NEEDS_INPUT**.

`pr_feedback_attempts` defaults to three repair runs per PR in a rolling 24-hour
window, separately from CI repairs. Normal validation and scheduler budgets
still apply. GitHub threads remain available for human review.

## Branches and publication

Workers read the Git name and email from Canvas **Application settings** at
startup. Set both before a build.

Branches use `<type>/<issue-number>-<description>`, or the task ID for manual
work. `branch_prefix: null` derives the type from the title; a configured prefix
overrides it. Stored branch names remain stable across retries. PR titles and
commits follow Conventional Commits, with `bug` normalized to `fix`.
`pr_title_subject_prefix` supplies a required tracker prefix when a title lacks one.

PR descriptions summarize the change and validation. Detailed logs and artifact
paths stay in Canvas. Group publication creates one draft PR per changed
repository, sequentially; if interrupted, inspect recorded PR URLs and continue
with the same group and task ID.
