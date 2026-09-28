# Traceability checks

Enable traceability for selected repositories to check requirement links, run
tests, and review requirement changes before completing factory tasks.
Requirements and code/test references use
[OpenFastTrace (OFT)](https://github.com/itsallcode/openfasttrace) syntax.

A **scope** file selects the files to trace, coverage rules, and test command.
Each task compares its changes with a **baseline** Git commit and saves results
for the source it checked. The runtime needs `intentbond` and the OFT JAR.
Repositories without `traceability_scope` keep their
usual test and review workflow.

## Workflow at a glance

The user discusses requirements and authorizes work with the Canvas coordinator.
For repositories with `traceability_scope`, traceability enters each stage as follows:

| Stage | What happens |
| --- | --- |
| **1. Discuss requirements** | The coordinator reads the project's scope and existing requirements, then discusses the requested behavior and acceptance criteria. |
| **2. Specify the task** | Once implementation is authorized, the task carries the agreed requirements, IDs, acceptance criteria, and intended documentation paths. |
| **3. Implement and repair** | The worker maintains requirements, code, tests, and their links, and runs checks for feedback. |
| **4. Validate and review** | The controller independently runs checks and matches the evidence to the exported commit. The reviewer checks behavior and requirement/test changes before completion and draft PR publication. |

`intentbond` supplies guidance, OFT checks, and saved evidence. The factory's
[OpenHands helpers](traceability-agent-api.md) add that guidance to agent context
and run commands in the workspace. The factory selects the scope and manages
authorization, repairs, review, and completion. Its runtime supplies the tools
to workers.

For implementation details, [`discussion_context()`](../workflows/traceability/__init__.py)
supplies requirements guidance to the coordinator, and
[`worker_agent()`](../workflows/agent.py) attaches development instructions with
`with_traceability(provisioned=True)`. This includes the procedure and references
while omitting tool-installation instructions. Alibaba Reviewer uses the
[factory-review skill](../workflows/skills/factory-review/SKILL.md).

Workers can follow the portable
[property-testing workflow](https://github.com/kbak/intentbond/blob/main/docs/property-testing.md)
to add or maintain properties for selected requirements using the project's
test library and configured command. Review generators, assumptions and search
budgets with assertions; passing searches do not prove the linked requirements.

Discussion, implementation, and reviewer contexts include the same portable
[concepts and result meanings](https://github.com/kbak/intentbond/blob/main/intentbond/skills/intentbond/references/semantics.md).
It defines coverage, identity, provenance, authorization, and the conclusions
supported by execution evidence; the factory does not maintain a separate vocabulary.

For a project without reviewed requirements and links,
[document the existing behavior and review the proposal](https://github.com/kbak/intentbond/blob/main/docs/recovery.md)
first. Commit and validate that starting point, then enable `traceability_scope`
in the factory registration. Preparing documentation or installing skills does
not enable the factory's checks automatically.

## Configuration

A portable scope can live in the project repository or in separate
configuration. A project-owned scope travels with the project: standalone
`ib check` reads the root `scope.json` from the baseline commit. A separately
maintained scope can also be used standalone with `--scope`, provided its test
dependencies and environment are available.

The factory currently loads scopes from `FACTORY_CONFIG_DIR`, the configuration
directory mounted at `/opt/factory/config`. It does not yet load scopes from
project Git commits. The config directory can live wherever the operator
chooses; it does not require a particular repository or directory name.

For factory use, place a scope under that directory, for example at
`traceability/my-project.json`. In `repositories/my-project.json`, reference it
relative to the config directory:

```json
{
  "traceability_scope": "traceability/my-project.json"
}
```

The reference enables the workflow. Missing or invalid files are configuration
errors. Without a reference, the repository keeps its usual workflow. Paths
must stay inside the config directory. The location restriction belongs to the
current factory loader, not the portable scope format. Maintain one authoritative
scope source; the task's frozen copy is an input snapshot.

The scope uses the portable format. For example,
`config/traceability/my-project.json`:

```json
{
  "schema_version": 1,
  "name": "my-project",
  "inputs": ["requirements.md", "src", "tests"],
  "specification_paths": ["requirements.md"],
  "test_paths": ["tests"],
  "required_coverage": {"req": ["impl", "utest"]},
  "policy": {
    "require_revision_increase": false,
    "allow_skipped_tests": true
  },
  "tests": {
    "format": "command",
    "command": ["bash", "-c", "bash \"$FACTORY_TESTS/run.sh\" qa lint unit frontend"],
    "timeout_seconds": 5400
  }
}
```

This example uses the repository's `test_profile`. For a direct test command,
use an argument list such as `["python", "-m", "unittest", "discover"]`.
The scope is the sole source of the test command: remove `test_command` from
an opted-in registration. Any inherited default test command is ignored.

Set `required_coverage` to match the project's OFT artifact chain; for example
`{"req": ["dsn"], "dsn": ["impl", "utest"]}`. Include test runners, fixtures,
and configuration in `test_paths`. To consume JUnit XML, use `format: "junit"`
and a repository-relative `report` path in `tests`. `allow_skipped_tests`
applies to JUnit results.

After changing a scope or registration, run `./scripts/factoryctl configure`.
Configuration validates scopes with the portable validator and uploads their
contents with the scheduler and reply workflows. Already submitted tasks keep
their captured configuration; resubmit a manual task to update it. Changes to
a deployment file alone do not update uploaded workflows.

Start with a reviewed, tested baseline containing valid requirements and
references. Both base and candidate must satisfy the configured coverage. An
empty requirements scope cannot pass.

## Design discussion and handoff

In Canvas, select the repository or group before drafting requirements. The
coordinator reads its current configuration and requirements guidance with:

```sh
python /opt/factory/configure.py discussion PROJECT
```

This read-only command resolves the same scope as task submission. The
coordinator inspects existing requirements in the catalog, reuses their IDs,
and proposes IDs for concrete new requirements using the repository's OFT
conventions and revision policy. Exploratory ideas, proposals, agreed work,
and open questions remain distinct. In a group, this applies only to opted-in
repositories; the others keep their usual specification workflow.

When implementation is authorized, the existing specification file carries
the agreed Markdown, IDs, acceptance criteria, intended documentation paths,
and any authorized changes to existing promises. The worker updates those
files in its worktree. The coordinator never writes to the read-only catalog.
Discussion and drafting do not authorize implementation or add another approval
step. Conflicts with existing promises need an explicit decision through the
existing question process.

For example, a discussion can preserve `req~session-expiration~1` and propose
`req~explicit-logout~1`. Once logout is agreed, both requirements and their
acceptance criteria go into the same handoff, with `requirements.md` as their
destination. See the [handoff fixture](../tests/fixtures/traceability-handoff.md).
Links help the agent compare documentation, implementation, and assertions;
a passing automated check does not prove they agree semantically.

## Task behavior

The controller saves a trusted scope before starting the worker. Agents receive
the traceability skill and a runnable check command in implementation and
repair sessions. The command uses the controller's `FACTORY_TESTS` (when a
profile is selected), `FACTORY_WORKSPACE`, `COMPOSE_PROJECT_NAME`, and `TMPDIR`.
It allocates a fresh evidence directory on each run, separate from the
controller's output. Evidence and scratch directories sit outside the source
checkout, under the job workspace. Candidate edits cannot replace the trusted
scope.

After implementation, the controller allocates a fresh output path and runs the
checker. Tests execute once on the captured source. `PROJECT_DIR` points to that
snapshot; `FACTORY_TESTS`, `FACTORY_WORKSPACE`, and the job's Docker context
remain available. Snapshots omit Git metadata and ignored local environments,
reject symlinks/submodules, and leave LFS pointers unexpanded. Test dependencies
must be available in the worker or installed by its test command.

After export and worker teardown, evidence is verified against the retained
commit and trusted scope. Completion requires a successful current invocation,
matching source, passing tests, independent review, and applicable browser QA. A
failed invocation cannot consume an earlier passing bundle. Changed source
suppresses the test attestation even when the subprocess succeeded.

Specification and test edits enter the existing review stage. Reviewers receive
the authorized task, baseline, scope, and changes. They assess changed
requirements and assertions; unauthorized weakening is a blocking finding.
Contradictions that need a maintainer decision use `NEEDS_INPUT`. Ordinary
authorized edits proceed to review without another confirmation. Repairs rerun
the checks.

## Review assessment

For opted-in repositories, Alibaba Reviewer assesses changed behavior within the
configured `inputs`, even when requirements and tests were not edited. It follows
relevant requirements, implementation references, and test assertions or other
configured verification, including links through existing design or architecture
artifacts. An unrelated ID in a changed file does not establish coverage.

The assessment is part of the existing review JSON and Markdown report:

| Assessment | Required explanation | Outcome |
| --- | --- | --- |
| Covered | Relevant requirement IDs and documentation, implementation, and verification references. | Satisfies this obligation. |
| No additional tracing needed | Concrete reason, such as a mechanical edit or a refactor supported by existing relationships. | Satisfies this obligation. |
| Missing traceability | Changed behavior, the missing connection, and a concrete repair. | `CHANGES_REQUESTED`; enters the task's repair loop. |
| Uncertain | What could not be established and why. | `BLOCKED`; review remains incomplete. |

The controller requires an assessment for every opted-in repository and accounting
for each changed path within its scope. Missing entries or required references
cannot pass. The reviewer judges which behaviors changed and whether their links
make sense; these structural checks do not prove semantic relevance or completeness.
Existing requirements can suffice without new IDs or documentation edits. Changes
outside the selected scope and opted-out repositories keep ordinary review rules.

The controller also resolves every cited `requirement_ids` entry against an OFT
import of the reviewed source snapshot. Bare complete IDs such as
`req~session-expiration~1` select the candidate; `base:req~session-expiration~1`
explicitly selects the baseline. Unknown IDs, wrong revisions, or unavailable
source indexes leave review incomplete. The index includes specification items
within the configured specification paths, including intermediate design items.
Documentation, implementation, and verification explanations are still assessed
by the reviewer; their relevance is not established by ID existence.

Task review indexes both retained Git commits. PR review indexes the supplied
candidate archive without requiring Git metadata; a baseline archive is not
available in that flow, so historical IDs cannot be resolved there. Imports use
the pinned OFT runtime and do not run project tests. Source identities and ID
inventories are retained with the review for publication retries; the complete
inventory is not injected into agent prompts. An older saved review without the
index cannot validate its requirement citations and needs a fresh review.

Retained checker evidence is copied into the read-only review workspace alongside
the fresh source clones. Repairs receive concrete traceability gaps separately
from code/security findings, then get new checks and a fresh assessment. Ordinary
code and security blockers still apply; no additional approval step is introduced.
The same conditional assessment applies to factory PR reviews using their existing
source and patch context. Saved review retries require the same configured scope
and a matching native assessment; they cannot reuse an older review that lacks it.

Artifacts are retained under the project's task output:

| Path | Contents |
| --- | --- |
| `traceability-scope.json` | Trusted scope. |
| `traceability-N/` | Complete evidence bundle for attempt N. |
| `traceability-check-N.log` | Checker stdout/stderr. |
| `traceability-N/tests.log` | Test command output. |

The existing task-level `review-N.json` and `review-N.md` retain the assessment
with the code and security review; no separate assessment report is created.
The Markdown report summarizes assessment outcomes and shows missing or uncertain
connections before collapsible details for covered/mechanical changes. Verification
references remain review judgments, not claims that individual tests executed.
Each portable check also retains `summary.md` in its evidence directory, with
changed specification IDs, recorded test outcomes, and source/policy identity.
Both summaries are generated from the existing check and review results.

The task result records `check_exit_code`, the matched commit, and the
independent review verdict/report. Exit 4 means automated checks passed with
review pending.

Review and repair prompts retain the checker's concise output and collection
status. Successful raw test logs, the duplicate scope, and change records used
for human summaries stay in retained artifacts. Failed runs still include their
test-log tail. Complete logs are staged in the next worker, and reviewers receive
the full scope and accessible evidence bundle. Agents receive the result summary
first and can read the complete records when needed.

## Runtime setup

Build the IntentBond wheel and collect its dependencies in an artifact directory.
Use the version pinned in
[docker/traceability.Dockerfile](../docker/traceability.Dockerfile).
From the IntentBond checkout:

```sh
python -m pip install --require-hashes --only-binary=:all: -r requirements/ci.txt
python -m build --no-isolation --wheel --outdir /path/to/wheels
```

Download the locked runtime dependencies and provision the checksum-pinned OFT
JAR from the same checkout:

```sh
python -m pip download --require-hashes --only-binary=:all: --dest /path/to/wheels \
  -r requirements/runtime.txt
python -m intentbond install-oft --destination /path/to/wheels
```

Use an artifact directory containing one wheel per package. The factory verifies
third-party wheels against `docker/traceability-requirements.txt`; keep it aligned
with the selected IntentBond release's `requirements/runtime.txt`. The IntentBond
wheel is a trusted local build input. From the factory checkout:

```sh
docker build -f docker/runtime.Dockerfile -t intentmade:traceability-base .
docker build -f docker/traceability.Dockerfile \
  --build-arg BASE_IMAGE=intentmade:traceability-base \
  --build-context traceability_wheels=/path/to/wheels \
  -t intentmade:traceability-test .
```

Set `FACTORY_IMAGE=intentmade:traceability-test` in the test deployment's
shell or `.env`, then use `./scripts/factoryctl up`. Startup requires that custom
image to exist locally, preserves it, and transfers it to the worker daemon.
The default `intentmade:dev` image is built automatically. Rebuild custom
images explicitly before startup after changing runtime code or dependencies,
then refresh uploaded workflows with `./scripts/factoryctl configure`.

## Validation

Run the [factory regression suite](../tests/README.md#traceability-checks)
against the traceability image. The fixtures exercise Git, OFT, and test commands
with scripted implementation and review responses. They cover discussion
context, approved requirement handoff and persistence, scope loading,
configuration uploads, agent/controller invocation, assessment gates and repairs,
test edits,
coverage reduction, altered scope, missing or stale evidence, export drift,
review rejection, and opt-out behavior. Startup tests mock Docker to check
runtime selection and image transfer.

Before enabling a project, validate its baseline and test setup in a disposable
worker. Run one bounded task with `publish_draft: false`, then inspect the
requirement/test diff, review, repair behavior, and retained evidence.
Repository CI and merge rules remain outside this integration.

## Source and revision impact reports

Use `ib impact --evidence PATH` to inspect changes to declarations, links, code,
and tests. Check evidence and `ib explain` distinguish the complete source from
the files selected for tracing and semantic review. Review covers the complete
candidate and continued assertion coverage; a revision update is not semantic
approval.
