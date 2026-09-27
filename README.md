# OpenHands factory

A local software factory for Linux and Docker. Discuss ideas and approve
specifications in OpenHands Agent Canvas, then run implementation, tests and an
independent agent review in disposable containers. Passing changes are published
as draft pull requests. Scheduled agents triage GitHub issues and review PRs.

The agent uses Codex through OpenHands ACP with your subscription login. Canvas
provides chat, schedules, run history and logs. Each task gets a branch and Git
worktree in every selected repository, plus its own Docker daemon for tests.

## Setup

Requirements: a Linux host with Docker Engine and Compose, Git, Python 3, GitHub
CLI (`gh`) access to your repositories, and a Codex login for the agents.

Choose directories for configuration, test profiles and runtime data. They do
not need a separate Git repository. For example:

```text
openhands-factory/       # Tooling, generic examples and tests
my-factory/              # Operator-chosen directory
  config/               # Repository registrations, groups and scheduling policy
  profiles/             # Optional application test adapters
  .factory/             # Ignored credentials, catalogs, task branches and artifacts
```

From the tooling checkout, copy the examples into an empty directory and create
your local settings file:

```bash
mkdir -p ../my-factory
cp -R examples/. ../my-factory/
cp .env.example .env
```

Set `FACTORY_CONFIG_DIR`, `FACTORY_PROFILES_DIR`, and `FACTORY_DATA_DIR` in `.env`
to your chosen paths. All three are required; relative paths resolve from this
checkout. The example settings use the layout above. For an existing
installation, keep its current paths to preserve access to its data.

Replace the example repositories and groups with your own. Set each repository's
test command and required CI checks, and review `config/defaults.json`. Examples
have scheduling disabled; set `enabled: true` for repositories you want polled.

Authenticate `gh`, then initialize the deployment and start the services:

```bash
./scripts/factoryctl init
./scripts/factoryctl up
```

Connect the factory's Codex account, then install the configured automations:

```bash
./scripts/factoryctl codex-login
./scripts/factoryctl configure
```

`configure` imports your GitHub credential into native secret storage and
applies the configured schedules. It creates an approval label only if one is
configured.

`codex-login` shows a device code and saves the completed login directly to
Canvas's encrypted `CODEX_AUTH_JSON` secret. Its temporary CLI directory is
removed afterward. The **Settings → LLM → ChatGPT subscription** card connects
OpenHands' own LLM backend; it does not connect the factory's Codex ACP workers.

Choose the factory model in **Settings → Agent → factory-codex**. Use the native
custom model field and enter `gpt-6-astra/xhigh` for extra-high reasoning. The
**Default** badge identifies the profile used for new chats; the separate
profile named `default` does not need the same edit. Existing chats retain their
model selection.

Each new implementation, triage, or review worker reads the model from the saved
`factory-codex` profile, including its reasoning suffix. It keeps that selection
for its lifetime and retains its role's permissions (reviews stay read-only).
Editing the profile affects subsequent workers without rebuilding the image.
Rerunning `configure` preserves the existing profile and its model selection.

### Alibaba code and security review

Automatic reviews and `factoryctl review` use one native **Alibaba Reviewer**,
backed by [Open Code Review](https://github.com/alibaba/open-code-review)'s
`open-code-review-delegate` procedure. OCR prepares the file inventory and review
rules; the existing Codex subscription and selected model perform the review.
There is no separate OCR model endpoint or API key. The agency Code Reviewer and
Application Security Engineer are no longer used by the review pipeline.

The runtime pins OCR **1.12.4**, its matching delegation skill, and the optional
Cloudflare audit skill by checksum in `docker/runtime.Dockerfile`. The controller
runs OCR preparation before the read-only reviewer starts. Implementation reviews
use Git ranges; standalone PR reviews use GitHub's complete changed-file inventory
and exact-commit source archives with OCR's rule resolution. Archive reviews do
not invent Git history. Missing required patches/source, incomplete file inventory,
or missing native reviewer evidence prevent approval. Every selected file must
have a coverage record, including files that OCR's preview would exclude.
A controller-owned rule configuration preserves Alibaba's built-in rules;
repository rule files cannot replace them or waive required coverage.

| Verdict | Effect |
| --- | --- |
| `PASS` | Alibaba review completed with no blockers and complete required coverage. |
| `CHANGES_REQUESTED` | Blocking findings enter the repair loop with any failed tests. |
| `BLOCKED` | Missing source, coverage, execution evidence, or reports stop publication without spending a repair attempt. |

Material code defects and high/critical security findings block publication.
Medium security findings block when demonstrated exploitability and material
impact support them. Other findings remain advisory. Each blocker must cite
source evidence and a concrete failure or attack scenario. For repositories with
[traceability enabled](docs/traceability.md#review-assessment), Alibaba Reviewer
also assesses scoped behavior changes against relevant requirements and verification.
Concrete traceability gaps request changes; missing or uncertain required
assessments leave review incomplete. They remain separate from ordinary findings.

Follow-up reviews reassess previous findings against current code. Reports identify
verified fixes, partial fixes, unresolved issues, and additional findings. Review
JSON, native transcripts, file coverage, and OCR inputs are retained with task
artifacts. Existing publication-failure artifacts from the former two-reviewer
protocol require a fresh review; they cannot satisfy the new execution contract.

### Optional Cloudflare security audits

For an in-depth security review, explicitly ask Canvas to apply the
**Cloudflare security-audit skill**, for example:

> Use the Cloudflare security-audit skill to audit the authentication and tenant isolation
> in /projects/repos/example. Write the audit to
> /projects/requests/security-audits/example-auth-01.

The Canvas coordinator applies the pinned
[Cloudflare security-audit skill](https://github.com/cloudflare/security-audit-skill)
with the existing subscription. It coordinates independent hunting and validation,
keeps source read-only, and writes the upstream reports and coverage ledger to the
specified external directory. It is not invoked by automatic PR reviews and does
not publish GitHub verdicts. The skill permits execution of target code only with
its required sandbox controls; otherwise affected candidates remain
`needs_validation` and source inspection can continue. Report unresolved checks
as limitations, not as confirmed vulnerabilities or proof of safety.

### Factory skills

Builds and reviews retain [task measurements](docs/measurements.md) with their
artifacts and show a concise summary in the persistent Canvas chat. Records
include attempt history, durations, available OpenHands usage, validation
outcomes and reviewer-reported traceability gaps. A local CLI displays and
aggregates them, and can append human effort or later outcome observations.

Edit the procedures under [workflows/skills/](workflows/skills/) to change
implementation, review, browser QA, or report-writing instructions.

After changing Python or skill files under `workflows/`, let active jobs finish,
rebuild the runtime and refresh workflow uploads. `./scripts/factoryctl up`
builds the default image; when `FACTORY_IMAGE` selects a custom image, rebuild
it explicitly before running `up` (see [runtime setup](docs/traceability.md#runtime-setup)).
Then refresh:

```bash
docker compose exec -T canvas python /opt/factory/configure.py configure
```

Existing conversations retain their saved instructions; new tasks receive the
updated skills. The refresh command preserves the existing GitHub connection.

### Agency agents

The runtime includes Codex roles from
[agency-agents](https://github.com/msitarzewski/agency-agents). Use them in
Canvas or task workers, for example: "Use the Frontend Developer agent to review
this component." Roles inherit the parent session's model and permissions.

The runtime image build installs the roles. An uncached build needs GitHub
access. The source revision and checksum are pinned in
[docker/runtime.Dockerfile](docker/runtime.Dockerfile). Rebuild after updating
the pin; new sessions receive the updated roles.

Automated workers use Codex/ACP. Choosing another provider for a Canvas chat
does not change the provider or role definitions used by those workers.

## Feature work

Select a repository under `/projects/repos` in the Canvas workspace picker to
browse its read-only catalog. Implementation tasks receive a separate writable
worktree.

Discuss an idea in Canvas until its scope, tradeoffs and acceptance criteria are
clear. Explicitly approve the specification and ask to implement it. The
coordinator can submit the job from chat, or you can submit an approved file:

```bash
./scripts/factoryctl submit example-app ./approved-spec.md --run
```

For traceability-enabled repositories, see the
[four-stage workflow summary](docs/traceability.md#workflow-at-a-glance) for where
shared requirements guidance, the development skill, and independent checks/review
enter this process.

Omit `--run` to inspect the prepared job in **Automate** before starting it. A
job implements the specification, runs the configured tests and requests an
independent review. It allows one repair attempt by default. Tests and review
must pass before draft publication; merging and deployment remain manual.

Continue a task with `--task TASK_ID` to preserve its branch and original base
while allocating a fresh worktree. Add `--no-publish` to retain the branch and
results locally. Failed tasks also retain their branches for inspection or
retry.

## Repository configuration

Configure [traceability](docs/traceability.md) to use existing requirements and
OFT IDs during design discussions, carry agreed requirements into implementation,
and check references, tests, and requirement changes before completing tasks.

Each `repositories/NAME.json` under `FACTORY_CONFIG_DIR` inherits
`defaults.json` from the same config directory. For example:

```json
{
  "repository": "your-org/your-project",
  "branch": "main",
  "test_command": "docker compose -f compose.test.yaml run --rm tests",
  "required_checks": ["Unit tests", "Integration tests"]
}
```

`test_command` runs in the task worktree with `PROJECT_DIR` set to that path and
access to the job's Docker daemon. Use the application's own test command and
containers where available.

For an external test adapter, put its files under `profiles/NAME/` and set
`test_profile` to `NAME`. A command such as `bash "$FACTORY_TESTS/run.sh" unit`
uses that profile's read-only mount; escape the quotes in JSON. Multiple
repositories can share a profile. A profile is optional.

### Browser acceptance evidence

Configure `browser_qa` on repositories that need rendered UI verification:

```json
{
  "browser_qa": {
    "paths": ["frontend/src/**", "frontend/public/**"],
    "exclude": ["**/*.test.*", "**/*.spec.*"],
    "start_command": "bash \"$FACTORY_TESTS/browser.sh\"",
    "url": "http://docker:8001",
    "instructions": "Describe disposable test accounts and application-specific QA here."
  }
}
```

After tests pass, matching changes receive a fresh QA worker at the retained
commit. The startup command must leave the app running and exit successfully
when ready. It receives `PROJECT_DIR`, `FACTORY_TESTS` when a profile is
selected, and `FACTORY_QA_OUTPUT`. Services can use the job's disposable Docker
daemon; ports published there are available to the browser at
`http://docker:PORT`. Use development fixtures and local services in this
profile.

Browser QA exercises the affected flows and records named checks and PNG
screenshots. Images, hashes, captions, page URLs, and the tested commit are
saved under `artifacts/RUN-TASK/PROJECT/browser-ATTEMPT/`. Screenshots appear as
Canvas attachments. If attachment delivery fails, the result records the failure
and the image files remain in the artifacts.

`PASS` requires passed checks and valid retained screenshots. An observed defect
enters the normal bounded repair loop. Missing infrastructure or evidence,
unverified behavior, or source modifications during QA produce `NEEDS_INPUT`.
The final commit must pass tests, applicable browser QA and independent review
before publication. `browser_qa: null` disables this stage; path patterns define
its scope, so include shared files that affect your UI as needed.

For a paused issue, a maintainer can explicitly accept named infrastructure gaps
in a `resume:` reply or `retry-issue --answer-file` response. Include this line
(use the exact check names from the browser report):

```text
accept-browser-gaps: {"Live Telegram integration": "No disposable session available; publish a draft with this limitation documented."}
```

Acceptance belongs only to that issue's unchanged specification and survives
retries. A later directive replaces it; `{}` revokes it. Ordinary issue text and
worker claims cannot grant acceptance. Available checks must still pass and
screenshots and the unchanged checkout must validate. Startup failures, missing
evidence, observed defects, failed tests and incomplete independent review still
block publication. Accepted checks remain `BLOCKED` in the evidence; the parent
records browser status `ACCEPTED_GAPS` and validation `PASSED_WITH_GAPS`, supplies
the limitations to independent review, and lists them in the draft PR.

After adding repositories, run `init` and `configure` from the tooling checkout.
Use `refresh NAME` to update the local code catalog used in discussions. Build
submissions resolve the remote base commit independently of that catalog.

### Repository groups

A `config/factories/NAME.json` groups registered repositories for one task:

```json
{
  "repositories": ["example-app", "example-web"]
}
```

Submit the group name instead of a repository name. The approved specification
must cover all selected repositories. Their worktrees share one agent workspace;
`FACTORY_WORKSPACE/PROJECT` provides access to each repository for integration
tests. Every repository's tests and the review of the complete change must pass
before publication. Each changed repository receives its own draft PR.

PR creation is sequential. If publication stops partway through, inspect the
recorded PR URLs and continue with the same group and task ID. Scheduled issue
work remains scoped to the issue's own repository.

### Separate instances

One Canvas can manage multiple repositories and groups. Work is serialized per
repository; unrelated repositories can run concurrently.

For separate instances, give each distinct `COMPOSE_PROJECT_NAME`,
`CANVAS_PORT`, `FACTORY_CONFIG_DIR` and `FACTORY_DATA_DIR` values using `.env`
or exported environment variables. `FACTORY_PROFILES_DIR` selects the test
adapters. Defaults and paths are listed in [.env.example](.env.example).

Groups select task scope. To restrict repository access, use separate instances
with appropriately scoped GitHub credentials. Keep one enabled scheduler owner
per repository across instances.

## GitHub scheduling

Each enabled repository has a **Factory — NAME** automation. Defaults allow a
poll every ten minutes and two task attempts per poll, with no daily cap.
`daily_tasks: null` disables the daily cap; a positive number sets a UTC-day
limit.

Commits use the Git name and email from Canvas **Application settings**, read
when each worker starts. Set both before running a build; use an email linked to
your GitHub account for attribution. PR descriptions summarize the complete
change and validation; run details and artifact paths stay in Canvas.

New branches use `<type>/<issue-number>-<description>`, for example
`fix/1402-telegram-restore-scanning-after-stop`. Manual tasks use their task ID
in place of the issue number. `branch_prefix: null` derives the type from the
task title; an explicit prefix overrides that category. The chosen branch is
stored with the task, so changing defaults or titles preserves existing PRs. PR
titles and commits use Conventional Commits, with `bug` normalized to `fix`. A
repository can set `pr_title_subject_prefix` for a stricter title check, such as
`NOSTORY`. Existing tracker keys are preserved; the prefix is added only when a
title lacks one.

| Work | Eligibility and result |
| --- | --- |
| Issue implementation | Open, unassigned issues, oldest first. The workflow claims the issue for the configured assignee, implements, tests, reviews and opens a draft PR. |
| Issue proposals | When an optional approval label is configured, changed, unassigned issues without that label may receive proposals in Canvas when polling capacity is available. |
| Requested PR review | Open, non-draft PRs requested from the connected GitHub account or one of its active teams, with passing CI and no submitted human review of the current commit. Publishes code and security reports plus a formal verdict to GitHub; also retains Canvas reports and local artifacts. |
| Follow-up PR review | A new commit after this factory's outstanding changes request, even without another review request. Uses the same readiness, human-review and per-commit deduplication checks. Stops after approval or dismissal of the connected account's latest verdict. |
| Manual PR review | Explicitly requested through `factoryctl review`. Open, non-draft PRs with passing required CI checks; publishes the same reports and verdict to GitHub. |
| Published PR maintenance | PRs recorded as published by this instance, including drafts. Address eligible review feedback, check CI, repair failures and merge newer base commits into the existing task branch. |

Tests and independent review must pass before creating a PR or pushing a repair.
Failed configured tests enter the bounded repair loop before independent review.
Each repair worker and reviewer receives the retained test command and complete
logs in its own workspace; the review host does not need application test tools.
The scheduler does not launch another agent review of its own PRs after
publication, including when a draft becomes ready or CI turns green. Publication
starts CI monitoring. Recorded PR updates take priority over new issues. Repairs
receive CI annotations and available job logs, then run the configured tests and
independent review against the current base before pushing. Verified GitHub
artifact-service failures are retried without code edits, even while other
workflows are still running. The failed check is recorded before the retry
request, preventing duplicate retries on subsequent scans. The PR head, branch
ownership and base are rechecked immediately before push; concurrent changes
withhold publication. Successful CI resets the consecutive repair count.
`ci_repair_attempts` defaults to three consecutive CI repairs per PR; it is
independent of daily task limits. Unresolved failures or product questions are
reported in the PR's Canvas conversation and accept an explicit `resume:` reply.
A branch name or shared GitHub login alone never grants repair scope over
someone else's PR.

`issue_label: null` makes open, unassigned issues eligible without a label.
Enabling the repository's schedule authorizes that work; the issue title and
body provide the specification. A running task must still match that
specification before publication. On failure, the workflow releases an
assignment acquired by that run; an assignment retained from an earlier run
stays in place. It records the attempted specification so it is not retried on
every poll. Each issue gets a persistent Canvas conversation with its result,
questions and evidence path. The run links to that conversation; live phases
distinguish implementation, tests, independent review, repair and publication.
Empty or busy scans report **Skipped**, not a successful implementation.

Material unanswered questions stop implementation with **NEEDS_INPUT**, before
tests or publication, and wake the read-only report assistant to ask them in
Canvas. The issue stays assigned while waiting. The scheduler also checks
recorded, assigned tasks for answers; an issue assigned to the same GitHub user
without a matching task record is not eligible for automatic continuation. Reply
in the task conversation with `resume: YOUR ANSWER`. Once Canvas saves the
message, it immediately queues a native **Resume** automation. It starts without
waiting for the next scan, or waits for the active repository run to finish.
This continuation handles answered tasks only and rechecks each reply under the
repository lock before consuming it once. Duplicate notifications and a
concurrent scheduled scan cannot apply an answer twice. The scan remains a
recovery fallback if immediate dispatch is unavailable. Pausing the repository
schedule also prevents new reply dispatches. A normal discussion message does
not restart work. No blanket approval or extra label is required. To retry
immediately after fixing a failure, or supply an answer from a file:

```bash
./scripts/factoryctl retry-issue example-app 123
./scripts/factoryctl retry-issue example-app 123 --answer-file ./answer.md
```

Changing an issue's specification invalidates a pending continuation. A fresh
scan can pick up the changed issue under the ordinary eligibility rules. Failed
exports keep the complete job workspace and write `recovery-workspace.txt` in
the evidence directory. Successfully retained work continues through review even
if cleanup must be deferred; `cleanup-warnings.log` identifies any leftover job.

To opt into label-based approval, set `issue_label` to a label name. In that
mode, applying the label authorizes implementation and draft publication; remove
and reapply it after reviewing a failed run or editing the approved
specification.

### Automatic PR feedback repair

With `pr_feedback: true`, maintenance collects submitted `CHANGES_REQUESTED` and
`COMMENTED` review bodies on the current commit, plus unresolved, non-outdated
inline comments and replies. General discussion comments need `@openhands` or
`@openhands-agent` to distinguish requests from conversation. Humans need
current write/maintain/admin permission; bot logins must be explicitly listed in
`pr_feedback_bots`. Factory-generated reviews and review bodies superseded by
approval are excluded. The default bot allowlist is empty.

Only PRs with this factory's publication receipt are eligible. Feedback is
coalesced per poll and checked again before implementation and publication. The
existing tests, browser QA and independent review gates apply to repairs. The
builder explains feedback that is already satisfied or unsupported; new
requirements outside the approved task produce `NEEDS_INPUT`.

Receipts record each feedback ID and revision before work begins. Repeated polls
do not repeat a repair; edited feedback gets a new revision. Failed/interrupted
repairs require an explicit `resume:` reply. `pr_feedback_attempts` defaults to
three repair runs per PR in a rolling 24-hour window, independently of the CI
repair counter; each run also uses the normal repair-attempt and scheduler
budgets. Reaching the limit asks for maintainer input. GitHub threads remain
available for human review after repair.

### Requested reviews

Requested and follow-up reviews run after published PR maintenance and before
new issues, within the existing poll and daily budgets. Team membership is
checked through GitHub, including inherited membership through child teams; the
connection needs organization membership read access. Each current commit is
checked for submitted human reviews (`APPROVED`, `CHANGES_REQUESTED`, or
`COMMENTED`); bot reviews, factory-generated reports, author self-comments,
pending/dismissed reviews and reviews of older commits do not suppress a factory
review. Review scope, head commit, base branch and commit, and CI are rechecked
before work and publication.

A fresh review request is optional after this factory requests changes: a new,
CI-ready head or base can trigger a follow-up automatically. The factory requires its
own durable publication receipt and verifies that the connected account's latest
submitted verdict is still that changes request. Approval or dismissal ends this
automatic follow-up; an explicit account/team request continues to work as
before. Author comments claiming a fix and reviews posted outside this factory
do not establish automatic follow-up scope. The reviewers receive the PR's
discussion and previous reviews through the same review pipeline.

Completed factory reviews have durable receipts bound to the repository, PR,
head commit, base branch and base commit, including manual reviews. New commits
and retargeted PRs can receive a new review. Saved reviews without a pinned base
require a fresh review before publication. Failed or
interrupted automatic attempts are reported and held for an explicit `factoryctl
review` retry, avoiding repeated agent runs on every poll. A stale result does
not count as a completed review.

A rejected Codex login puts the review in `NEEDS_INPUT` with reconnect
instructions. Run `factoryctl codex-login`, complete the device sign-in, then
reply `resume: retry` on the report or rerun `factoryctl review`. No GitHub
verdict is published until the review completes. A typed startup timeout gets
one retry before the agent has performed work; other failures require an
explicit retry. Startup details and bounded, redacted worker logs are retained
in the run artifacts before the disposable worker is removed.

Published reports combine duplicate findings and include code links pinned to
the reviewed commit and a validation summary. Original specialist reports and
any report-editing transcript remain in the artifacts. Initial reviews with one
reviewer and zero or one finding render directly from the reviewed fields,
including the full reviewer summary and traceability assessment, without a
separate report-editing agent session. The source fields must fit the report
schema; otherwise the editor shortens them. Repairs, resumed tasks, PRs with
discussion or review history, and reports with multiple findings keep the editor.
Legacy publication retries with unknown review history also keep the editor
when a presentation has not already been saved. Existing provenance validation
and publication checks apply to both paths.

Completed standalone reviews publish that report with a formal GitHub verdict:
`PASS` becomes **Approve**, and blocking findings become **Request changes**.
Advisory findings remain in an approval's body. Incomplete or stale reviews
never approve a PR. Publication runs in the parent process, rechecks the current
commit and CI, and anchors the review to the reviewed SHA. Canvas links to the
GitHub review. A failed publication is reported as `PUBLICATION_FAILED`;
`factoryctl review` retries that saved, verified report without rerunning the
specialists. Existing submitted factory reviews are detected before retrying a
POST, preventing duplicates after a lost response.

Missing, pending or failed CI blocks PR review. Accepted results default to
`success`, `neutral` and `skipped`; explicit check names detect missing jobs.
Reviews use the exact PR head's source archive and recheck eligibility before
recording completion. They rely on CI rather than rerunning application tests.
To request a review manually:

```bash
./scripts/factoryctl review example-app 123
```

## Operations

Run commands from the tooling checkout:

| Command | Purpose |
| --- | --- |
| `./scripts/factoryctl status` | Show service health. |
| `./scripts/factoryctl logs` | Read service logs. |
| `./scripts/factoryctl projects` | List repository configuration. |
| `./scripts/factoryctl factories` | List repository groups. |
| `./scripts/factoryctl up` | Build the default runtime or use the supplied image, then start. |
| `./scripts/factoryctl configure` | Apply configuration and workflow changes. |
| `./scripts/factoryctl down` | Stop services while retaining data. |

Use Canvas's **Automate** view to inspect runs and pause schedules. Let active
jobs finish before restarting services. The host and Docker must remain running
for polling; configure Docker to start at boot if unattended operation is
needed.

`FACTORY_DATA_DIR` selects the runtime data directory. Task branches live under
`workspaces/tasks/`; reports, test logs, patches and PR metadata are
under `artifacts/`. Back up this directory and the Compose native state volume
together. Keep runtime data and `.env` out of Git, and preserve volumes during
routine shutdowns (`down`, without `-v`).

Workers receive the Codex credential; the GitHub credential stays in the parent
workflow. Implementation, triage and independent review run in separate
disposable workers. Builders retain public internet access and Docker tests;
worker access to the management daemon, other jobs and the host's private
networks is blocked. See [SECURITY.md](SECURITY.md) for the single-operator
trust model, retained state and update procedure. Containers share the Docker
host's Linux kernel. Normal completion and handled failures clean up job
resources; a host crash can require manual cleanup.

## Development

See [tests/README.md](tests/README.md) for lint, regression and live smoke
checks. Runtime dependencies are pinned in `docker/runtime.Dockerfile` and
`upstream.lock.json`; run the checks when changing workflows or those pins.
Local builds use the `openhands-factory:dev` image tag.
