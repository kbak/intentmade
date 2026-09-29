# Portable development workflow

IntentMade uses ordinary Markdown and Git to carry intent, specification and
planning between tools. This follows the artifact roles in
[Anthropic's playbook](https://claude.com/blog/the-ai-native-sdlc-playbook);
these filenames are a convention, not an interoperability standard.

| Phase | Artifact and authority |
| --- | --- |
| Intent | `intent.md`: user outcome, design rationale, accepted decisions, useful conversation references and unresolved questions. |
| Specification | `spec.md`: scope, acceptance criteria, constraints and links to canonical requirements with their revisions. |
| Planning | `plan.md`: affected repositories/files, ordered steps, risks, verification commands and requirement references. Planning alone does not authorize implementation. |
| Implementation | Code, tests and living project specifications change together. Accepted task snapshots preserve the agreed starting point. |
| Verification and review | Controller-selected commands and independent review describe the retained candidate commit. Draft PRs include source links, commands and review findings. |
| Continuation | `handoff.md` and `handoff.json` record status, source identity, accepted document hashes, results, findings and the next action. |

## Keep product context small and current

Maintain one short product overview: purpose, users, outcomes, boundaries and key
decisions. Reuse the canonical `docs/intent.md`, root `intent.md`, or an existing
README overview. Create one from explicit user context and maintained documents
when useful; do not reconstruct user motives from code. Task intent explains the
local change and links to the overview and affected specification. Historical task
snapshots retain their original context; the product overview describes today.

Read the overview once per task, then only affected sections and linked requirements.
Update it with the implementation when approved work changes product direction.
Routine fixes usually need no product-intent edit and no new three-file folder.
Their approved issue/request plus a brief inline plan suffice, for example:

```text
Context: docs/intent.md#reliable-retries; spec.md#retry-preserves-data
Change: fix the retry button so it preserves the entered form data.
Plan: fix the handler; run the existing retry regression and add the failing case.
```

Existing `submit PROJECT SPEC` jobs and issue automations use this lightweight
path. The approved request remains authoritative; routine planning is delegated,
not separately human-reviewed. A clearly specified repair can proceed without a
product document. Missing context that prevents a product decision uses the
existing NEEDS_INPUT/reply process.

The existing independent review returns a compact intent-consistency assessment
for each build repository, including projects without OFT. It compares current
product context, affected spec, task and plan: concrete new/worsened contradictions
request changes; uncertainty or a missing assessment leaves review incomplete.
This adds no review pass or model call. It is a best-effort semantic judgment,
not a guarantee against drift. OFT links and revisions retain their existing role.

## Prepare and accept an explicit plan

Use a package for substantial work, an explicit handoff, or when requested.
Discuss the documents with the coordinator or prepare them yourself. A previously
accepted plan can be supplied directly; no second planning conversation is needed.

```sh
./scripts/factoryctl plan example-app ./spec.md \
  --intent ./intent.md --plan ./plan.md \
  --task retry-validation --output ./work-package.json
```

This freezes the exact documents and selected repository commit IDs in a proposed
package. It creates no automation, edits no project source and starts no worker.
The coordinator uses the equivalent `configure.py plan PROJECT DOCUMENTS.json`
command, where the input maps the three filenames to their Markdown text.
Each document may contain up to 64 KiB. A package covers one repository or a
configured group; its plan must identify work for every selected repository.

When implementation is authorized, accept that package by submitting it:

```sh
./scripts/factoryctl submit example-app ./spec.md \
  --work-package ./work-package.json --run
```

Submission requires the same specification and repository bases as planning.
If either changed, review the effect and prepare the package again. The supplied
task ID is reused automatically. Omitting `--run` registers the accepted work for
inspection in Automate. It does not dispatch it.

Before implementation, each repository receives:

```text
docs/changes/retry-validation/
  intent.md
  spec.md
  plan.md
  accepted.json
```

`accepted.json` records base commits, document hashes and package identity.
The accepted intent, spec and acceptance record stay unchanged. Keep `plan.md`
current in the same change as implementation, briefly explaining revised steps.
The original plan stays in the controller package and Git history; review receives
it for comparison only when the plan changes. Handoffs record both plan hashes.
Repairs preserve the current plan. Reusing a task with a different accepted package
is rejected; a continuation without the flag recovers the retained package.
Update affected living project intent/specification as agreed. Material behavior
changes still require a maintainer answer; editing a plan cannot authorize them
or waive verification.

Keep one declaration for each OFT requirement revision in the selected scope.
Task snapshots should cite canonical requirements and IDs, rather than copy their
OFT declarations. Preserve IDs for continuing promises and use the project's
revision policy when a promise changes. Git commits and document hashes identify
artifact versions; they do not replace requirement IDs/revisions. Link intent →
requirements → plan steps → implementation/assertions → execution evidence.

## Keep verification with the project

Prefer a repository-owned command, such as `python -m unittest discover` or
`./scripts/check`, and document its dependencies and environment in the project.
The factory can select a scope from an explicitly pinned project commit using
[`traceability_scope_git`](traceability.md#configuration). A candidate cannot
replace that trusted policy; adopting a new revision remains an operator action.
External deployment scopes and test adapters continue to work, but taking them
to another tool also requires supplying those adapters and their environment.

## Continue elsewhere

Every build that enters execution maintains a readable handoff beside its retained
evidence, including failed/no-publish runs. A completed PR embeds its test command
and independent review findings, with links to accepted artifacts at the validated
commit. Repairs refresh the factory's marked evidence section while preserving
other PR text. Long review reports are explicitly truncated in the PR; full
structured findings and logs stay in the artifact directory.

Export a stopped run using the directory name shown in its artifact paths:

```sh
./scripts/factoryctl export RUN_TASK ./handoff-export
```

The new folder contains Markdown/JSON reports, logs, test XML, patches and PNG
evidence where present, plus `export.json` with file sizes and SHA-256 hashes.
Provider JSONL transcripts are excluded. Exports are local and bounded to 256 MiB
and 2,048 files; a failed export removes its incomplete destination. Full browser
videos and other formats stay in the original run.

Exports are private evidence, not sanitized release artifacts. Test/startup logs,
raw structured model responses, patches and screenshots can contain credentials,
personal data or private source even though provider transcripts are excluded.
Worker diagnostic redaction does not cover all of these files.

Before sharing, keep the original bundle private and make a separate copy.
Remove unnecessary files, redact sensitive content in the copy, run
`gitleaks dir --redact /path/to/share-copy`, and inspect screenshots and remaining
text manually. A clean scanner result is not proof that every secret or private
detail was removed. Label the copy as sanitized: the original hashes and any
verification results no longer attest its changed files. Supply the intact
original only through an appropriately private channel when verification is
needed. Never upload raw evidence to a public issue or release by default.

Give the next tool the project at the recorded candidate commit and this folder.
For unpublished work, the retained task branch and `changes.patch` provide the
changes; apply the patch to the recorded base or recover the retained branch using
the [operations guide](operations.md#recover-retained-work). The export is not a
Git bundle or an environment image. `handoff.md` describes completed work and the
next action; `handoff.json` preserves structured findings and coverage limits.
Missing results remain missing, and a previous pass applies only to its recorded
source/policy, not to later edits.

There is no factory `REVIEW.md`. Review instructions remain in the existing
factory review skill and controller policy; findings live in PRs and evidence.
A destination tool can add its own review configuration when needed.
