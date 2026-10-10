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

Pull requests run [source checks](.github/workflows/ci.yml): locked Ruff plus the
host CLI and Compose fixtures, with Docker/model calls mocked. The full runtime
suite and isolation probes still require the test image. Follow the
[local image setup](tests/README.md#use-a-locally-built-test-image) to run the
canonical suite against your own immutable image ID.

The [secret scan](.github/workflows/secrets.yml) runs checksum-pinned Gitleaks over
all fetched history on pushes and pull requests. It first checks detection with
a synthetic token and uses upstream rules without candidate config, ignore files
or inline suppressions. To check locally with Gitleaks 8.30.1:

```sh
gitleaks git --redact --ignore-gitleaks-allow --log-opts="--all" .
```

Also scan a directory containing the exact files you intend to release with
`gitleaks dir --redact /path/to/release`; Git mode does not inspect uncommitted
files. Use obvious dummy fixture values instead of realistic tokens. If a real
secret is found, revoke or rotate it before arranging any history cleanup.
See [evidence sharing](docs/portable-workflow.md#continue-elsewhere) before
attaching logs or handoff exports.

## Make a change

- Read the [intent](docs/intent.md) and affected [specification requirements](docs/spec.md).
  Follow their links to code and assertions; keep intent, promises, links and
  evidence consistent. Preserve IDs for continuing promises, including document
  moves. Use the matching packaged IntentBond guidance and the root
  [checking scope](scope.json); ordinary development does not repeat recovery.
- Prefer upstream mechanisms and shared helpers. Check both the pinned implementation
  and released upstream replacements before adding glue or a runtime patch; retain
  custom behavior only for a concrete requirement gap, and remove it when a
  compatible upstream implementation is adopted.
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
