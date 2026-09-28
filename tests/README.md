# Testing

Run commands from the repository root. Regression tests use local fixtures and
scripted GitHub/agent responses. The live agent smoke test below uses a separate
Canvas deployment and a Codex login.

## Lint and regressions

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .

docker build -f docker/runtime.Dockerfile -t intentmade:dev .
docker run --rm --network none --entrypoint python \
  -e PYTHONPATH=/opt/factory/workflows -e OPENHANDS_SUPPRESS_BANNER=1 \
  -v "$PWD/tests:/tests:ro" -v "$PWD/examples/config:/opt/factory/config:ro" \
  -v "$PWD/examples:/examples:ro" \
  -v "$PWD/scripts:/scripts:ro" -v "$PWD/tests/profiles:/factory-tests:ro" \
  intentmade:dev -m unittest discover -s /tests -p 'test_*.py' -v
```

Coverage includes scheduling and deduplication, task authorization, Git
transfer, repair and recovery, review verdicts, publication retries, browser
evidence, maintainer replies, and skill loading. Tests also check credential
separation, read-only review permissions, and hostile Git configuration.
Resource tests exercise JSON configuration, Docker update failure before
credential delivery, low-disk admission and bounded imports through the installed
upstream archive extractor, including compressed input and sparse-file sizes.

## Traceability checks

Build the [traceability image](../docs/traceability.md#runtime-setup), then run
the regression command above with `intentmade:traceability-test`. The
suite requires IntentBond and its OFT JAR to run the opt-in cases. The
ordinary image skips those cases.

Traceability tests run Git, OFT, and unittest through implementation, repair,
export, and review. They also check that discussion context selects only opted-in
scopes, submission preserves the approved Markdown handoff, and a scripted
worker persists existing and new requirements in the exported commit. Cases
include altered scope, missing or stale evidence,
failed controller invocations, source drift, scope loading and upload refresh,
and opt-out behavior. A profile fixture executes the exact command supplied in
the agent instructions and then the controller's check. Agent and review
responses are scripted; startup image selection and transfer use mocked Docker
commands. Assessment tests exercise missing/incomplete results, existing links,
mechanical changes, mixed scopes, uncertain coverage, report retention, and repair.
A fixture keeps OFT green while a scripted reviewer identifies untraced logout
behavior, then verifies a fresh assessment after repair. These cases validate
the gate and handoff, not an actual agent's ability to detect semantic gaps.

The OpenHands helper tests cover serialized context, selected property/formal
guidance, command quoting, absolute workspace paths, and recovery storage. They
run in the same regression suite. Pure command-forwarding tests also run without
IntentBond installed.

For native ACP instruction delivery and conversation resumption, run
`/scripts/check_native_resume.py` in a fresh disposable traceability container
using the mounts above. Add `--recovery` to check recovery guidance. It uses a
local scripted provider and requires no model credentials.

## Review protocol probe

Run `/tests/check_project_config.py` with the offline container command below
after changing Codex/ACP pins or project-trust handling. It uses the real adapter,
a local scripted provider, and fresh credential homes: repository MCP startup
must stay disabled for Git and archive inputs, nested/symlinked/additional roots,
and load/resume/fork after process restart. Explicit factory MCP servers must
start even when ignored repository configuration declares the same name.
The specialist probe also checks that repository MCP/role declarations cannot
replace the factory reviewer during native delegation.

After changing the runtime launcher or authentication handling, run
`/tests/check_agent_startup.py` with the same offline container command below.
It launches the actual worker server and verifies remote authentication errors
and a single startup-timeout retry using a scripted ACP endpoint. It requires
no model credentials. The server executable must launch the installed Python
module so the runtime patches are used.

```bash
docker run --rm --network none --entrypoint python \
  -e PYTHONPATH=/opt/factory/workflows -e OPENHANDS_SUPPRESS_BANNER=1 \
  -v "$PWD/tests:/tests:ro" \
  intentmade:dev /tests/check_specialist_review.py
```

This probe uses a local scripted model endpoint to check Codex/ACP instruction
delivery, Alibaba reviewer selection, inherited model/permissions, and
retained reports. Offline OCR tests cover Git ranges, source archives, and required
file accounting. It checks the protocol, not model review quality. Rebuild and
rerun after changing the runtime, Codex/ACP pins, or bundled roles.

## Sandbox and network probes

```bash
python3 tests/check_review_sandbox.py
python3 tests/check_resource_limits.py
python3 tests/check_isolation.py
```

The sandbox probe checks allowed coordinator edits and denied reviewer writes,
Git metadata writes, unrelated writes, and network sockets against the actual
kernel. See [codex-seccomp.md](../runtime/codex-seccomp.md) for the container
profile.

The resource probe uses a disposable offline container, applies the bridge's
Docker flags and verifies both Docker's configuration and active Linux cgroup v2
limits. It also checks the job daemon's rendered Compose process limit.

The isolation probe creates a temporary privileged Docker test daemon and fake
services. It checks management-socket isolation, blocked parent/cross-job
traffic, and access to job-local services and public npm HTTPS. It requires
local Docker access and internet access. It removes its test containers,
networks, and volumes.

## Browser evidence probe

```bash
docker run --rm --network none --entrypoint python \
  -e PYTHONPATH=/opt/factory/workflows -e OPENHANDS_SUPPRESS_BANNER=1 \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True -v "$PWD/tests:/tests:ro" \
  intentmade:dev /tests/check_browser_evidence.py
```

This uses Chromium, a local HTML fixture, and an isolated Agent Server to check
Playwright interaction, screenshot capture, file download, and Canvas attachment
persistence after worker teardown. It requires no model credentials.

Test application startup profiles separately in a disposable job daemon. Mount
the copied checkout under `/workspaces`; the job daemon owns `/tmp`.

## Live agent smoke test

The fixtures use `main` and `trunk` branches with Docker tests. Their
configuration disables scheduling and publication. In a dedicated shell:

```bash
export COMPOSE_PROJECT_NAME=intentmade-test
export CANVAS_PORT=8001
export FACTORY_DATA_DIR="$PWD/.factory/test-instance"
export FACTORY_CONFIG_DIR="$PWD/tests/config"
export FACTORY_PROFILES_DIR="$PWD/tests/profiles"

./scripts/factoryctl init
./scripts/factoryctl up
```

Connect the test factory's Codex account, then submit the fixture specification:

```bash
./scripts/factoryctl codex-login
./scripts/factoryctl submit factory-smoke ./tests/fixtures/smoke-spec.md --run
```

Submit `factory-smoke-alt` to check the second base branch and concurrent jobs.
Use `--task TASK_ID` to check continuation. Submit `smoke-pair` with a
specification covering both repositories to check a grouped task.

Inspect results in Canvas and artifacts under `.factory/test-instance/`. Stop
the test deployment with `./scripts/factoryctl down` from the same shell.
