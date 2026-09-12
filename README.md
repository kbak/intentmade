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

Keep the tooling and your private deployment configuration in sibling directories:

```text
openhands-factory/      # Tooling, generic examples and tests
factory-deployment/     # Private repository
  config/               # Repository registrations, groups and scheduling policy
  profiles/             # Optional application test adapters
  .factory/             # Ignored credentials, catalogs, task branches and artifacts
```

Clone your private deployment there, or create one from the examples. From the
tooling checkout, copy these into an empty directory:

```bash
mkdir -p ../factory-deployment
cp -R examples/. ../factory-deployment/
```

Replace the example repositories and groups with your own. Set each repository's
test command and required CI checks, and review `config/defaults.json`. Examples
have scheduling disabled; set `enabled: true` for repositories you want polled.

Authenticate `gh`, then initialize the deployment and start the services:

```bash
./scripts/factoryctl init
./scripts/factoryctl up
```

Open [Agent Canvas](http://localhost:8000/canvas), complete Codex onboarding, then
install the configured automations:

```bash
./scripts/factoryctl configure
```

`configure` imports your GitHub credential into native secret storage and applies
the configured schedules. It creates an approval label only if one is configured.

Choose the factory model in **Settings → Agent → factory-codex**. Use the native
custom model field and enter `gpt-6-astra/xhigh` for extra-high reasoning.
Canvas passes the model and reasoning effort separately through its native Codex
integration; no model catalog or frontend patch is required.
The **Default** badge identifies
the profile used for new chats; the separate profile named `default` does not
need the same edit. Existing chats retain their model selection.

Each new implementation, triage, or review worker reads the model from the saved
`factory-codex` profile, including its reasoning suffix. It keeps that selection
for its lifetime and retains its role's permissions (reviews stay read-only).
Editing the profile affects subsequent workers without rebuilding the image.
Rerunning `configure` preserves the existing profile and its model selection.

### Independent code and security review

Automatic reviews and `factoryctl review` use a coordinator that starts the
bundled **Code Reviewer** and **Application Security Engineer** as two native
Codex subagents in parallel. Both inspect the same change in a fresh read-only
worker and inherit the selected model and reasoning effort. The factory verifies
their native role identities, completion and final reports before calculating
the overall verdict; a coordinator summary cannot substitute for either review.

`PASS` means both reviews completed with no blocking findings. Low/informational
security findings, hardening suggestions and style preferences remain in the
report and do not trigger repairs. Material code defects and high/critical
security vulnerabilities block publication. Medium security findings block only
when supported by demonstrated exploitability and material impact. A review with
no findings is valid. Reviewers must cite evidence and a concrete failure or
attack scenario for every blocker.

`CHANGES_REQUESTED` sends only blocking findings back to the builder, alongside
any failed tests. `BLOCKED` means a required review or its native execution
evidence is incomplete and stops publication without spending code repair
attempts. Each specialist's findings are retained in the review JSON; the
transcript preserves their original responses. The Markdown report separates
blockers from advisory findings.
Follow-up reviews reassess earlier findings against the current code. The report
credits verified fixes, identifies partial or unresolved fixes and additional
findings, and states when resolution could not be verified. Author claims alone
do not establish that an issue is fixed; the consolidation pass uses the current
specialists' assessments and cannot remove blockers or change the verdict.
These are source reviews using available test/CI evidence, not a guarantee that
SAST, DAST, secret scanning or current dependency vulnerability scans ran.

### Factory skills

Factory procedures are maintained as standard `SKILL.md` files under
[`workflows/skills/`](workflows/skills/): implementation and validation guidance,
independent code/security review, browser acceptance QA, and report writing. Edit the relevant skill
to change a procedure. Task data and response schemas remain in the workflows;
OpenHands continues to own conversations, worktrees, scheduling and run history.
The factory still enforces tests, specialist completion and publication gates.

The workflow selects a skill for each stage. OpenHands' native `Skill.load` and
`AgentContext` include its full instructions in the saved agent configuration
and deliver them through ACP. This also works for the report editor, which does
not use tools, and for workers whose working directories differ from the
automation's. Implementation skills are attached when OpenHands creates the
worktree conversation, because attaching later preserves its saved context.
These skills do not need a separate Codex installation or discovery patch.

The runtime image and native automation uploads both include the skill files.
After changing Python or skill files under `workflows/`, rebuild with
`./scripts/factoryctl up` and refresh the installed workflow bundles with:

```bash
docker compose exec -T canvas python /opt/factory/configure.py configure
```

Let active jobs finish before rebuilding/restarting. Existing conversations
retain their saved instructions; subsequent tasks receive the installed skills.
The refresh command retains the existing native GitHub connection.

### Agency agents

The runtime includes 273 native Codex custom agents from
[agency-agents](https://github.com/msitarzewski/agency-agents/tree/647c8baa42b6842afb4a97bf2c0950d45ba88e8b),
pinned by revision and archive checksum in `docker/runtime.Dockerfile`. Both
Canvas conversations and disposable workers receive the roles. For example:
"Use the Frontend Developer agent to review this component."

Following the setup above with `./scripts/factoryctl up` automatically downloads
and installs these roles while building the image. An uncached build needs access
to GitHub. No separate agency-agents installation is required for the factory.
Cloning this repository alone does not install roles into a developer's local
Codex, Claude Code, or other coding tools.

OpenHands gives subscription sessions an isolated `CODEX_HOME`, so installing
agents in the host's `~/.codex/agents` alone does not expose them to OpenHands.
The ACP launcher copies the bundled TOML files into each session's `agents/`
directory before Codex starts, preserving existing files. The definitions contain
only names, descriptions and instructions; model selection and permissions inherit
from the parent session. The host's login and configuration are not mounted.

Run `./scripts/factoryctl up` after changing the pinned source to rebuild Canvas
and refresh the worker image. Newly started Codex sessions receive the updated roles.

#### Claude Code and other agents

This factory currently provisions agency roles for Codex only. Its automated
workers explicitly launch `codex-acp`; selecting another provider for a Canvas
chat does not switch those workers or install that provider's role format.

For a local coding tool outside the factory, use the
[upstream installer](https://github.com/msitarzewski/agency-agents/blob/647c8baa42b6842afb4a97bf2c0950d45ba88e8b/scripts/install.sh)
with that tool's target. For example, from an agency-agents checkout:

```bash
./scripts/install.sh --tool claude-code --no-interactive
```

The upstream project also provides integrations for other tools, each with its
own installation location and supported behavior. Adding another provider to
this factory requires its native role installation, session setup, credentials,
and worker integration; the bundled Codex TOML files do not provide that support.

## Feature work

You can select a repository under `/projects/repos` in the Canvas workspace
picker. This is a read-only catalog for discussion; the chat receives the factory
instructions even when the selected repository has its own Git root. Approved
implementation requests are submitted to a disposable worker with a writable
worktree. Selecting a catalog does not require changing ownership or permissions.

Discuss an idea in Canvas until its scope, tradeoffs and acceptance criteria are
clear. Explicitly approve the specification and ask to implement it. The
coordinator can submit the job from chat, or you can submit an approved file:

```bash
./scripts/factoryctl submit example-app ./approved-spec.md --run
```

Omit `--run` to inspect the prepared job in **Automate** before starting it.
A job implements the specification, runs the configured tests and requests an
independent review. It allows one repair attempt by default. Tests and review
must pass before draft publication; merging and deployment remain manual.

Continue a task with `--task TASK_ID` to preserve its branch and original base
while allocating a fresh worktree. Add `--no-publish` to retain the branch and
results locally. Failed tasks also retain their branches for inspection or retry.

## Repository configuration

Each `config/repositories/NAME.json` in the private deployment inherits
`config/defaults.json`. For example:

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
when ready. It receives `PROJECT_DIR`, `FACTORY_TESTS` when a profile is selected,
and `FACTORY_QA_OUTPUT`. Services can use the job's disposable Docker daemon;
ports published there are available to the browser at `http://docker:PORT`.
Use development fixtures and local services in this profile.

The adapted OpenHands `qa-changes` procedure uses pinned Playwright MCP through
native ACP configuration. It exercises the affected flows and returns named
checks and PNG screenshots. The parent downloads and verifies the images before
teardown, retaining their hashes, captions, page URLs and tested commit under
`artifacts/RUN-TASK/PROJECT/browser-ATTEMPT/`. Canvas receives native image
attachments; result JSON records an attachment failure if Canvas is unavailable,
while the files remain retained. Images stay within the private factory.

`PASS` requires passed checks and valid retained screenshots. An observed defect
enters the normal bounded repair loop. Missing infrastructure or evidence,
unverified behavior, or source modifications during QA produce `NEEDS_INPUT`.
The final commit must pass tests, applicable browser QA and independent review
before publication. `browser_qa: null` disables this stage; path patterns define
its scope, so include shared files that affect your UI as needed.

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
`FACTORY_WORKSPACE/PROJECT` provides access to each repository for integration tests.
Every repository's tests and the review of the complete change must pass before
publication. Each changed repository receives its own draft PR.

PR creation is sequential. If publication stops partway through, inspect the
recorded PR URLs and continue with the same group and task ID. Scheduled issue
work remains scoped to the issue's own repository.

### Separate instances

One Canvas can manage multiple repositories and groups. Work is serialized per
repository; unrelated repositories can run concurrently.

For separate instances, give each distinct `COMPOSE_PROJECT_NAME`, `CANVAS_PORT`,
`FACTORY_CONFIG_DIR` and `FACTORY_DATA_DIR` values using `.env` or exported
environment variables. `FACTORY_PROFILES_DIR` selects the test adapters. Defaults
and paths are listed in [.env.example](.env.example).

Groups select task scope. To restrict repository access, use separate instances
with appropriately scoped GitHub credentials. Keep one enabled scheduler owner
per repository across instances.

## GitHub scheduling

Each enabled repository has a **Factory — NAME** automation. Defaults allow a
poll every ten minutes and two task attempts per poll, with no daily cap.
`daily_tasks: null` disables the daily cap; a positive number sets a UTC-day limit.

Commits use the Git name and email from Canvas **Application settings**, read
when each worker starts. Set both before running a build; use an email linked
to your GitHub account for attribution. PR descriptions summarize the complete
change and validation; run details and artifact paths stay in Canvas.

New branches use `<type>/<issue-number>-<description>`, for example
`fix/1402-telegram-restore-scanning-after-stop`. Manual tasks use their task ID
in place of the issue number. `branch_prefix: null` derives the type from the
task title; an explicit prefix overrides that category. The chosen branch is
stored with the task, so changing defaults or titles preserves existing PRs.
PR titles and commits use Conventional Commits, with `bug` normalized to `fix`.
A repository can set `pr_title_subject_prefix` for a stricter title check, such
as `NOSTORY`. Existing tracker keys are preserved; the prefix is added only
when a title lacks one.

| Work | Eligibility and result |
| --- | --- |
| Issue implementation | Open, unassigned issues, oldest first. The workflow claims the issue for the configured assignee, implements, tests, reviews and opens a draft PR. |
| Issue proposals | When an optional approval label is configured, changed, unassigned issues without that label may receive proposals in Canvas when polling capacity is available. |
| Requested PR review | Open, non-draft PRs requested from the connected GitHub account or one of its active teams, with passing CI and no submitted human review of the current commit. Publishes code and security reports plus a formal verdict to GitHub; also retains Canvas reports and local artifacts. |
| Follow-up PR review | A new commit after this factory's outstanding changes request, even without another review request. Uses the same readiness, human-review and per-commit deduplication checks. Stops after approval or dismissal of the connected account's latest verdict. |
| Manual PR review | Explicitly requested through `factoryctl review`. Open, non-draft PRs with passing required CI checks; publishes the same reports and verdict to GitHub. |
| Published PR maintenance | PRs recorded as published by this instance, including drafts. Address eligible review feedback, check CI, repair failures and merge newer base commits into the existing task branch. |

Tests and independent review must pass before creating a PR or pushing a repair.
The scheduler does not launch another agent review of its own PRs after publication, including
when a draft becomes ready or CI turns green.
Publication starts CI monitoring. Recorded PR updates take priority over new
issues. Repairs receive CI annotations and available job logs, then run the
configured tests and independent review against the current base before pushing.
Verified GitHub artifact-service failures are retried without code edits, even
while other workflows are still running. The failed check is recorded before
the retry request, preventing duplicate retries on subsequent scans.
The PR head, branch ownership and base are rechecked immediately before push;
concurrent changes withhold publication. Successful CI resets the consecutive
repair count. `ci_repair_attempts` defaults to three consecutive CI repairs per
PR; it is independent of daily task limits. Unresolved failures or product
questions are reported in the PR's Canvas conversation and accept an explicit
`resume:` reply. A branch name or shared GitHub login alone never grants repair
scope over someone else's PR.

`issue_label: null` makes open, unassigned issues eligible without a label. Enabling
the repository's schedule authorizes that work; the issue title and body provide
the specification. A running task must still match that specification before
publication. On failure, the workflow releases an assignment acquired by that run;
an assignment retained from an earlier run stays in place. It records the attempted
specification so it is not retried on every poll. Each issue gets
a persistent Canvas conversation with its result, questions and evidence path.
The run links to that conversation; live phases distinguish implementation,
tests, independent review, repair and publication. Empty or busy scans report
**Skipped**, not a successful implementation.

Material unanswered questions stop implementation with **NEEDS_INPUT**, before
tests or publication, and wake the read-only report assistant to ask them in Canvas.
The issue stays assigned while waiting. The scheduler also checks recorded,
assigned tasks for answers; an issue assigned to the same GitHub user without
a matching task record is not eligible for automatic continuation.
Reply in the task conversation with `resume: YOUR ANSWER`. Once Canvas saves the
message, it immediately queues a native **Resume** automation. It starts without
waiting for the next scan, or waits for the active repository run to finish.
This continuation handles answered tasks only and rechecks each reply under the
repository lock before consuming it once. Duplicate notifications and a concurrent
scheduled scan cannot apply an answer twice. The scan remains a recovery fallback
if immediate dispatch is unavailable. Pausing the repository schedule also prevents
new reply dispatches. A normal discussion message does not restart work.
No blanket approval or extra label is required. To retry immediately after fixing
a failure, or supply an answer from a file:

```bash
./scripts/factoryctl retry-issue example-app 123
./scripts/factoryctl retry-issue example-app 123 --answer-file ./answer.md
```

Changing an issue's specification invalidates a pending continuation. A fresh
scan can pick up the changed issue under the ordinary eligibility rules. Failed
exports keep the complete job workspace and write `recovery-workspace.txt` in
the evidence directory. Successfully retained work continues through review even
if cleanup must be deferred; `cleanup-warnings.log` identifies any leftover job.

The pinned Canvas callback needs a small compatibility patch to accept its
existing **SKIPPED** status and save task outcome metadata. The factory uses the
native completion and phase APIs; it does not rewrite automation database rows.

To opt into label-based approval, set `issue_label` to a label name. In that mode,
applying the label authorizes implementation and draft publication; remove and
reapply it after reviewing a failed run or editing the approved specification.

### Automatic PR feedback repair

With `pr_feedback: true`, maintenance collects submitted `CHANGES_REQUESTED` and
`COMMENTED` review bodies on the current commit, plus unresolved, non-outdated
inline comments and replies. General discussion comments need `@openhands` or
`@openhands-agent` to distinguish requests from conversation. Humans need current
write/maintain/admin permission; bot logins must be explicitly listed in
`pr_feedback_bots`. Factory-generated reviews and review bodies superseded by
approval are excluded. The default bot allowlist is empty.

Only PRs with this factory's publication receipt are eligible. Feedback is
coalesced per poll and checked again before implementation and publication.
The existing tests, browser QA and independent review gates apply to repairs.
The builder explains feedback that is already satisfied or unsupported; new
requirements outside the approved task produce `NEEDS_INPUT`.

Receipts record each feedback ID and revision before work begins. Repeated polls
do not repeat a repair; edited feedback gets a new revision. Failed/interrupted
repairs require an explicit `resume:` reply. `pr_feedback_attempts` defaults to
three repair runs per PR in a rolling 24-hour window, independently of the CI
repair counter; each run also uses the normal repair-attempt and scheduler
budgets. Reaching the limit asks for maintainer input. GitHub threads remain
available for human review after repair.

### Requested reviews

Requested and follow-up reviews run after published PR maintenance and before new issues,
within the existing poll and daily budgets. Team membership is checked through
GitHub, including inherited membership through child teams; the connection needs
organization membership read access. Each current commit is checked for submitted
human reviews (`APPROVED`, `CHANGES_REQUESTED`, or `COMMENTED`); bot reviews,
author self-comments, pending/dismissed reviews and reviews of older commits do
not suppress a factory review. Review scope, commit and CI are rechecked before work.

A fresh review request is optional after this factory requests changes: a new,
CI-ready commit can trigger a follow-up automatically. The factory requires its
own durable publication receipt and verifies that the connected account's latest
submitted verdict is still that changes request. Approval or dismissal ends this
automatic follow-up; an explicit account/team request continues to work as before.
Author comments claiming a fix and reviews posted outside this factory do not
establish automatic follow-up scope. The reviewers receive the PR's discussion and
previous reviews through the same review pipeline.

Completed factory reviews have durable per-repository, PR and commit receipts,
including manual reviews. New commits can receive a new review. Failed or
interrupted automatic attempts are reported and held for an explicit
`factoryctl review` retry, avoiding repeated agent runs on every poll. A stale
result does not count as a completed review.

Both specialists still review independently. An editing pass combines findings
about the same defect into one human-facing report, preserving complementary
evidence and fixes. Every original finding must be accounted for exactly once;
blocking status, severity and code locations come from the original reports.
The published report uses concise findings, commit-pinned code links and an
expandable validation summary, with no empty sections or repeated role reports.
Original specialist reports and the editing transcript remain in the artifacts.

Completed standalone reviews publish that report with a formal GitHub verdict:
`PASS` becomes **Approve**, and blocking findings become
**Request changes**. Advisory findings remain in an approval's body. Incomplete
or stale reviews never approve a PR. Publication runs in the parent process,
rechecks the current commit and CI, and anchors the review to the reviewed SHA.
Canvas links to the GitHub review. A failed publication is reported as
`PUBLICATION_FAILED`; `factoryctl review` retries that saved, verified report
without rerunning the specialists. Existing submitted factory reviews are
detected before retrying a POST, preventing duplicates after a lost response.

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
| `./scripts/factoryctl up` | Build and start the runtime. |
| `./scripts/factoryctl configure` | Apply configuration and workflow changes. |
| `./scripts/factoryctl down` | Stop services while retaining data. |

Use Canvas's **Automate** view to inspect runs and pause schedules. Let active jobs
finish before restarting services. The host and Docker must remain running for
polling; configure Docker to start at boot if unattended operation is needed.

`FACTORY_DATA_DIR` defaults to `../factory-deployment/.factory`. Task branches live
under `workspaces/tasks/`; reports, test logs, patches and PR metadata are under
`artifacts/`. Back up this directory and the Compose native state volume together.
Keep runtime data and `.env` out of Git, and preserve volumes during routine
shutdowns (`down`, without `-v`).

Workers receive the Codex credential; the GitHub credential stays in the parent
workflow. Implementation, triage and independent review run in separate disposable
workers. Builders retain public internet access and Docker tests; worker access
to the management daemon, other jobs and the host's private networks is blocked.
See [SECURITY.md](SECURITY.md) for the single-operator trust model, retained state
and update procedure. Containers share the Docker host's Linux kernel. Normal
completion and handled failures clean up job resources; a host crash can require
manual cleanup.

## Development

See [tests/README.md](tests/README.md) for lint, regression and live smoke checks.
Runtime dependencies are pinned in `docker/runtime.Dockerfile` and
`upstream.lock.json`; run the checks when changing workflows or those pins.
Local builds use the `openhands-factory:dev` image tag.
