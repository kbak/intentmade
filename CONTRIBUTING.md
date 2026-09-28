# Contributing to IntentMade

Bug reports, documentation improvements, and focused pull requests are welcome.
For larger changes, open an issue describing the problem and proposed behavior
before building a new subsystem. Report vulnerabilities through
[SECURITY.md](SECURITY.md#reporting-a-vulnerability).

## Development setup

Use Linux with Docker Engine, Compose, Python, and [uv](https://docs.astral.sh/uv/).
From the checkout:

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Runtime dependencies come from the pinned OpenHands image. Run regressions and
runtime probes using [tests/README.md](tests/README.md). Most use scripted agents
and require no model credentials; live smoke tests need a separate test deployment.
Do not run experiments against an active factory.

## Make a change

- Check the pinned upstream implementation before adding glue or a runtime patch.
- Keep the single-operator deployment model explicit. Preserve existing authorization,
  credential, source-transfer, and evidence boundaries.
- Add a regression for changed behavior and run the checks relevant to the affected
  path. Runtime patches also need a rebuilt image and the applicable native probe.
- Keep operator policy in configuration and application-specific test adapters in
  the deployment. Examples should work without personal paths or credentials.
- Update the relevant guide when commands, configuration, or behavior change.

Useful entry points are [workflows](workflows/), [runtime adapters](runtime/),
[operator commands](scripts/), and the [documentation index](docs/README.md).

## Submit a pull request

Describe the problem, resulting behavior, and validation performed. Include any
untested runtime paths or migration requirements. Keep unrelated refactors separate.
Never commit credentials, `.env`, runtime state, private logs, or dated local
qualification reports. Retain reproducible checks and document current behavior.

Contributions are provided under the repository's [MIT license](LICENSE).
Keep existing third-party copyright and license notices.
