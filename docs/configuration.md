# Configuration

Factory behavior is configured in JSON. `.env` selects deployment paths, the
image, port, and Compose instance name; it does not replace repository policy.

`config/deployment.json` contains installation-wide agent profile, runtime, resource and
authorization settings. `config/defaults.json` contains workflow defaults that
repository registrations can override. See [deployment choices](deployments.md)
for personal use, OSS issue intake and migration from the combined configuration.
Set `worker_agent_profile` there to select an existing Canvas agent profile;
see [native OpenHands and Codex configuration](agents.md).

Configure [traceability](traceability.md) to use existing requirements and
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

Optional `required_check_overrides` select a different required check set when
**every** changed path matches a rule's `paths` patterns. For example:

```json
"required_check_overrides": [
  {"paths": ["mobile/ios/**"], "required_checks": ["Swift tests", "JS tests"]}
]
```

Selection uses the complete immutable base/head comparison, including old paths
for renames. Mixed changes retain the default requirements. Matching rules combine
their requirements; empty rules are rejected. Missing/incomplete file inventories
block review. Every reported check must still have an accepted result, including
checks outside the selected required set.

`test_command` runs in the task worktree with `PROJECT_DIR` set to that path and
access to the job's Docker daemon. Use the application's own test command and
containers where available.

After configuration changes, run `./scripts/factoryctl configure` to refresh
uploaded workflows. Already submitted tasks keep their captured workflow
settings; deployment requirements are read from the current operator configuration.

For an external test adapter, put its files under `profiles/NAME/` and set
`test_profile` to `NAME`. A command such as `bash "$FACTORY_TESTS/run.sh" unit`
uses that profile's read-only mount; escape the quotes in JSON. Multiple
repositories can share a profile. A profile is optional.

For portability, prefer project-owned test scripts that also run outside the
factory. Traceability repositories can select their scope from a reviewed Git
commit with `traceability_scope_git`; see [scope configuration](traceability.md#configuration).
Accepted intent/spec/plan packages and evidence exports are described in the
[portable workflow](portable-workflow.md).

### Resource limits

An optional `worker_runtime` object in `deployment.json` selects the experimental
[Docker Sandboxes VM backend](docker-sandboxes.md). Omit it to retain the existing
DockerWorkspace backend. Its `kit` and `profiles` paths may be relative to
the directory containing `deployment.json`; startup resolves their host locations
and passes that config directory to the controller. Absolute paths remain supported.
VM resources belong in its native Kit YAML; repository
registrations cannot select or override the runtime.

The optional `resource_limits` object in `config/deployment.json` sets limits for
the whole installation; repository registrations cannot override it. Omitted
values use the defaults shown in [the example](../examples/config/deployment.json).
Values are positive integers, with sizes in MiB. They take effect for subsequent
jobs without rebuilding the image; `factoryctl configure` validates the settings.

Docker enforces each agent worker's memory (4096 MiB, with no extra swap), CPU
(2 cores) and process (512) limits. The pinned OpenHands `DockerWorkspace` does
not expose resource options, so the factory uses `docker update` before sending
credentials or running work. A failed update aborts the worker. The job test
daemon retains its Compose CPU/memory limits and receives a process limit of
2048. Canvas and the outer daemon retain their limits in [compose.yaml](../compose.yaml).

The controller also bounds incoming Git bundles (256 MiB), retained patches
(`max_patch_mb`, 64 MiB), compressed review archives (256 MiB), expanded archives
(1024 MiB), and archive members (100000). Git bundle inspection, import and patch
generation have a per-process address-space ceiling (`git_memory_mb`, 1024 MiB),
a 600-second timeout and bounded output. Patch generation streams to disk; only
a complete patch replaces the previous artifact. An oversized patch fails the
attempt while preserving its imported task branch for recovery. These limits
can be raised deliberately for unusually large repositories. A
5120 MiB free-disk admission check stops new jobs when storage is low. This check
is not a filesystem quota: running jobs, Git object expansion, Docker images and
retained artifacts can still consume disk. A hard disk ceiling requires a quota
on the deployment's storage; inspect recovery receipts before removing failed work.

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
accept-browser-gaps: {"Live notification delivery": "No disposable test account available; publish a draft with this limitation documented."}
```

Acceptance belongs only to that issue's unchanged specification and survives
retries. A later directive replaces it; `{}` revokes it. Ordinary issue text and
worker claims cannot grant acceptance. The pause report supplies the exact
`resume: accept-browser-gaps: {...}` reply when only eligible infrastructure gaps
remain. Plain `resume: go ahead` and `resume: retry` restart the task without
recording a new acceptance; an assistant acknowledgement does not change the gate.
Available checks must still pass and
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
adapters. Defaults and paths are listed in [.env.example](../.env.example).

Groups select task scope. To restrict repository access, use separate instances
with appropriately scoped GitHub credentials. Keep one enabled scheduler owner
per repository across instances.
