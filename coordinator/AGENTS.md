# Local OpenHands factory

Use this native Canvas conversation to discuss ideas, tradeoffs, specifications,
and results. Discussion alone never authorizes implementation. A user can stay
in planning indefinitely. Read the selected repository's AGENTS.md, CLAUDE.md,
and referenced guidance before proposing a change. Repository catalogs under
/projects/repos are read-only snapshots; disclose when they need refreshing.

Project configuration is available with:
`python /opt/factory/configure.py projects`
Repository groups are listed with `python /opt/factory/configure.py factories`.

When the user explicitly approves a concrete specification and asks to implement,
save it under /projects/requests. Include acceptance criteria, scope, tradeoffs,
and verification expectations. Then use the existing native workflow:
`python /opt/factory/configure.py submit PROJECT /projects/requests/spec.md --run`
Do not request the same approval again. If the user only wants to inspect the job,
omit --run and let them select Run now in Automate. To continue a task, pass
`--task EXISTING_TASK_ID`; this preserves its branch and approved original base.
PROJECT may also be a configured factory group. Before submitting a grouped
task, include the selected repositories in the reviewed scope. Each receives its
own branch and native worktree in one disposable worker; all selected test commands
and the independent review must pass before any draft PR is published. A failed
GitHub publication may leave earlier drafts; inspect result.json before retrying.
Group membership selects task scope and does not enforce credential isolation.

Implementation runs in a disposable worker using the Codex harness. OpenHands
creates its worktree; the workflow runs configured Docker tests, an independent
review, and a bounded repair attempt. Passing work opens a draft PR by default.
Use --no-publish only when the user specifically wants local evidence first.
Never merge, mark a draft ready, approve a PR, or deploy without explicit direction.

The scheduler only implements open, unassigned issues with the configured
factory:approved label. Applying that label is an explicit implementation and
draft-publication instruction: ensure the issue contains the agreed specification.
An issue label approves work only in that issue's repository. If work needs other
repositories, discuss a grouped specification and obtain approval for that scope.
Unapproved issues produce proposals only. PR reviews skip drafts and wait for
configured CI checks; their reports stay in Canvas/artifacts and do not post
GitHub review comments automatically.

Native Automate owns run status and logs. Evidence is in /projects/artifacts;
task Git branches persist in /workspaces/tasks. Do not edit task stores manually,
modify immutable evidence, bypass a repository lock, or bypass test/review failure.
Never expose credentials or pass the parent settings key or GitHub token to workers.
