# Testing

Run these commands from the tooling repository root.

## Lint and regression checks

Local Python checks use the pinned development environment:

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Build the default runtime image and run the regression suite:

```bash
docker compose build canvas
docker run --rm --network none --entrypoint python \
  -e PYTHONPATH=/opt/factory/workflows -e OPENHANDS_SUPPRESS_BANNER=1 \
  -v "$PWD/tests:/tests:ro" -v "$PWD/examples/config:/opt/factory/config:ro" \
  openhands-factory:dev -m unittest discover -s /tests -p 'test_*.py' -v
```

The suite checks issue eligibility and ownership, CI gating, branch retention,
review decisions, draft publication, credential refresh and repository groups.
Security regressions cover hostile Git configuration, unsafe bundle exports,
fresh review workers, strict read-only permissions, approval history, scheduler
pagination and durable deduplication. It uses local Git repositories and mocked
GitHub and agent responses. No model calls, GitHub mutations, subscription login
or private deployment are required.

Recovery regressions cover root-owned cleanup failures, retained work after a
branch rename or failed export, bounded test/review repair, structured questions,
explicit Canvas replies, and SKIPPED callbacks with persistent conversation links.
They also distinguish blocked review infrastructure from actionable code defects.
Verify the pinned Codex sandbox against the real kernel and Docker defaults:

```bash
python3 tests/check_review_sandbox.py
```

This uses no model or credentials. Reads must succeed; writes and network socket
creation must be denied. The pinned runtime selects Codex's Landlock backend
because Docker's default seccomp blocks the user namespaces needed by bubblewrap.

Check the real Docker network boundary separately:

```bash
python3 tests/check_isolation.py
```

This creates a temporary privileged DinD instance and fake job/parent services,
using the cached `docker:29.4.1-dind` image. It checks management socket access,
blocked parent/cross-job traffic, and working job-local services and public npm
HTTPS. It does not mount deployment state or credentials, and removes its own
containers, networks and volumes when finished. It requires local Docker access
and public internet access for the registry check.

## Live smoke checks

The fixtures in `tests/config/` use `main` and `trunk` branches and Docker tests.
Live checks run real agents with a Codex login in a separate Canvas instance.
Scheduling and publication are disabled in the fixture configuration.

In a dedicated shell, select the test deployment:

```bash
export COMPOSE_PROJECT_NAME=openhands-factory-test
export CANVAS_PORT=8001
export FACTORY_DATA_DIR="$PWD/.factory/test-instance"
export FACTORY_CONFIG_DIR="$PWD/tests/config"
export FACTORY_PROFILES_DIR="$PWD/tests/profiles"

./scripts/factoryctl init
./scripts/factoryctl up
```

Complete Codex onboarding in [the test Canvas](http://localhost:8001/canvas), then
submit the fixture specification:

```bash
./scripts/factoryctl submit factory-smoke ./tests/fixtures/smoke-spec.md --run
```

Submit `factory-smoke-alt` to check the second base branch and concurrent jobs.
Use `--task TASK_ID` to check continuation. For a task across both repositories,
submit the `smoke-pair` group with a specification covering both fixtures.
Inspect run results in Canvas and artifacts under `.factory/test-instance/`.

Stop the test deployment with `./scripts/factoryctl down` from the same shell.
