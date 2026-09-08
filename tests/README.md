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
docker run --rm --entrypoint python \
  -e PYTHONPATH=/opt/factory/workflows -e OPENHANDS_SUPPRESS_BANNER=1 \
  -v "$PWD/tests:/tests:ro" -v "$PWD/examples/config:/opt/factory/config:ro" \
  openhands-factory:dev -m unittest discover -s /tests -p test_factory.py -v
```

The suite checks issue eligibility and ownership, CI gating, branch retention,
review decisions, draft publication, credential refresh and repository groups.
It uses local Git repositories and mocked GitHub and agent responses. No model
calls, GitHub mutations, subscription login or private deployment are required.

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
