# Traceability checks

Enable traceability for selected factory repositories to check OFT references,
run tests, and review requirement changes before completing tasks. It uses
`versioned-traceability` and `openhands-traceability` installed in the runtime.
Registrations without `traceability_scope` use the usual test and review workflow.

## Workflow at a glance

The user discusses requirements and authorizes work with the Canvas coordinator.
For repositories with `traceability_scope`, traceability enters each stage as follows:

| Stage | Integration |
| --- | --- |
| **1. Requirements discussion** | The coordinator calls `configure.py discussion PROJECT`. [`discussion_context()`](../workflows/traceability/__init__.py) supplies the configured scope and the portable skill's `references/requirements.md`. The coordinator uses that guidance to discuss existing promises, requirement IDs and acceptance criteria. |
| **2. Approved handoff** | Once the user approves the specification and requests implementation, the coordinator carries agreed Markdown, IDs, acceptance criteria and intended documentation paths into the task specification. |
| **3. Implementation and repairs** | [`worker_agent()`](../workflows/agent.py) calls the adapter's `with_traceability()`. This injects the **full development SKILL.md plus its requirements reference**. The worker maintains requirements, implementation, tests and links, and runs checks for feedback. |
| **4. Validation and review** | The controller runs the portable checker independently and verifies evidence against the exported commit. Code Reviewer follows the separate [factory-review skill](../workflows/skills/factory-review/SKILL.md) to assess traceability and unauthorized requirement/test weakening. Checks and independent review gate completion and draft PR publication. |

`versioned-traceability` supplies the shared guidance, OFT validation and evidence.
`openhands-traceability` attaches that guidance to agent context and forwards commands.
The factory owns scope selection, authorization, handoffs, repairs and completion
gates. Its provisioned runtime supplies the selected tool versions to workers.

For an existing project, [recover and accept a baseline](https://github.com/kbak/versioned-traceability/blob/main/docs/recovery.md)
before this development loop, then enable `traceability_scope` in the factory
registration. Installing the skills or recovering a baseline does not enable that
policy automatically. Ordinary feature work uses the development skill.

## Configuration

A portable scope can live in the project repository or in separate
configuration. A project-owned scope travels with the project: standalone
`vt check` reads the root `scope.json` from the baseline commit. A separately
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

For opted-in repositories, Code Reviewer assesses changed behavior within the
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

The task result records `check_exit_code`, the matched commit, and the
independent review verdict/report. Exit 4 means automated checks passed with
review pending.

## Runtime setup

Build the two package wheels and collect their dependencies in an artifact
directory. The package versions installed by the image are specified in
[docker/traceability.Dockerfile](../docker/traceability.Dockerfile).

From each package checkout:

```sh
uv build --wheel --out-dir /path/to/wheels
```

With the portable package installed, download the wheel's Python dependencies
and provision its OFT JAR:

```sh
python -m pip download --only-binary=:all: --dest /path/to/wheels \
  /path/to/wheels/versioned_traceability-*.whl
vt install-oft --destination /path/to/wheels
```

Use an artifact directory containing one wheel per package. The OFT installer
verifies the JAR checksum. From the factory checkout:

```sh
docker build -f docker/runtime.Dockerfile -t openhands-factory:traceability-base .
docker build -f docker/traceability.Dockerfile \
  --build-arg BASE_IMAGE=openhands-factory:traceability-base \
  --build-context traceability_wheels=/path/to/wheels \
  -t openhands-factory:traceability-test .
```

Set `FACTORY_IMAGE=openhands-factory:traceability-test` in the test deployment's
shell or `.env`, then use `./scripts/factoryctl up`. Startup requires that custom
image to exist locally, preserves it, and transfers it to the worker daemon.
The default `openhands-factory:dev` image is built automatically. Rebuild custom
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
