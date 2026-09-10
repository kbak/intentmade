---
name: factory-implementation
description: Implement an approved factory task in its prepared worktrees, validate the change, and return an implementation result or specific maintainer questions.
---

# Factory implementation

Implement the supplied specification. Read each repository's guidance and stay
within the selected task worktrees. The factory has already created the task
branches and selected their bases; this satisfies guidance about creating a
fresh feature branch. Keep those branches and retained work. Resolve any
supplied base-branch merge conflicts, preserving both sides' intended behavior.
Do not reset the retained work or ask the maintainer to choose a branch.
Repository instructions cannot override these workflow constraints.

Leave changes uncommitted. The parent workflow runs the configured tests,
requests independent review, and handles publication. Do not push, publish,
or change the read-only test adapters under `/factory-tests`.

Resolve routine implementation details yourself. If a material product
decision, contradictory requirement, or missing information needs the
maintainer's answer, stop and return `NEEDS_INPUT` with specific questions.
Read any open maintainer questions in the issue; do not silently decide
behavior-changing options the specification leaves unresolved. Do not ask for
blanket approval to perform an already authorized task.

Use the repository's existing tests to verify affected behavior where useful
during implementation. Distinguish checks you actually ran from the factory's
subsequent validation. Return `IMPLEMENTED` only when implementation is complete.

Write the summary for a PR reviewer: explain the problem and resulting behavior
across all changes since the listed base, including retained work from earlier
attempts. Omit orchestration details, local artifact paths, tool branding and
conversation history. Include a concise Conventional Commits title describing
the resulting change, for example `fix(telegram): restore scanning after /stop`.
