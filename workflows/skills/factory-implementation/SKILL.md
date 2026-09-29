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

Read the canonical product overview once (usually docs/intent.md or intent.md;
reuse an existing README overview), then only affected spec sections. Task intent
describes the local change and links to product context; do not repeat that context
or scan the history of task intents. Update living intent/spec and trace links in
the same change only when approved work affects them. Establish a missing overview
from explicit user context and maintained docs when useful; never infer rationale
from code. Missing product docs alone need not block a clearly specified repair.

Read supplied accepted intent.md, spec.md and plan.md. Keep intent/spec/accepted.json
unchanged; keep plan.md current with implementation and briefly explain revised
steps there. The original plan remains in Git history and the controller package.
For a bounded issue/request without a package, a brief inline plan and references
in the implementation summary suffice; no new three-file folder is required.
Record only useful task context for larger work, linking canonical requirements
instead of copying OFT declarations. Distinguish delegated planning from human
acceptance and unresolved questions from authorized scope. Material changes to
agreed behavior use NEEDS_INPUT. Do not repeat approval for accepted work.

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
