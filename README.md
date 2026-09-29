# IntentMade

A shared software factory core powered by [OpenHands](https://github.com/OpenHands/OpenHands),
with configurable deployment policies and isolated worker execution.
Discuss and approve a specification in Agent Canvas, then let agents implement,
test, and independently review it in disposable Docker workers. Passing changes
become draft pull requests.

IntentMade runs locally on Linux. It uses Codex through OpenHands ACP with your
subscription login; Canvas provides chat, schedules, run history, and logs.

## How it works

1. **Specify** — discuss a feature and approve its scope and acceptance criteria.
2. **Build** — implement it in a dedicated branch and disposable workspace.
3. **Validate** — run project tests, optional browser QA, and independent review;
   repair within the configured attempt limit.
4. **Publish** — open a draft PR with validation results. Merge and deploy manually.

Scheduled workflows can also pick up approved GitHub issues, maintain the
factory's PRs, and respond to review requests. Each task has its own Docker daemon
for integration tests. [IntentBond](https://github.com/kbak/intentbond) optionally
connects requirements, code, and verification throughout the workflow.

## Get started

You need Linux, Docker Engine with Compose, Git, Python 3, GitHub CLI (`gh`)
access to your repositories, and a Codex login.

```sh
git clone https://github.com/kbak/intentmade.git
cd intentmade
mkdir -p ../my-factory
cp -R examples/. ../my-factory/
cp .env.example .env
```

Set the paths in `.env`, replace the example registrations under
`../my-factory/config/`, and configure your test commands. Examples have
scheduling disabled. Authenticate `gh`, then run:

```sh
./scripts/factoryctl init
./scripts/factoryctl up
./scripts/factoryctl codex-login
./scripts/factoryctl configure
./scripts/factoryctl open
```

In Canvas, select the model under **Settings → Agent → factory-codex** and set
your Git name and email in **Application settings**. The
[setup guide](docs/getting-started.md) covers authentication and configuration.

## Run a task

Discuss the change in Canvas and explicitly approve implementation, or submit an
approved specification from the command line:

```sh
./scripts/factoryctl submit example-app ./approved-spec.md --run
```

Omit `--run` to inspect the prepared job first. Add `--no-publish` to keep results
local, or `--task TASK_ID` to continue an existing task. Review an existing PR with:

```sh
./scripts/factoryctl review example-app 123
```

See [workflows](docs/workflows.md) for scheduling, replies, and PR maintenance.

## Deployment model

The same factory core supports personal workflows and maintainer-operated OSS
work. Runtime isolation, credential handling and issue approval are deployment
choices; implementation, repair, review, evidence and publication checks stay
shared. See [deployment choices](docs/deployments.md).

The current controller assumes one trusted operator. Canvas listens on localhost
by default; access to it grants factory control. DockerWorkspace workers receive the Codex
credential; Docker Sandboxes workers use host-managed proxy credentials.
GitHub publication credentials stay in the parent workflow. The default containers
share the host kernel, including privileged Docker test daemons. Docker Sandboxes
places each worker and its test daemon in a separate VM.

New installations require a maintainer-controlled `factory:approved` label for
issue work; scheduling starts disabled. Review the target repository's CI permissions before enabling
publication. Read the [security model](SECURITY.md) and
[resource limits](docs/configuration.md#resource-limits) before unattended use.

The shared core can underpin future enterprise integrations, but the repository
does not provide enterprise identity, organization-level authorization or a
multi-tenant controller boundary.

## Documentation

- [Intent](docs/intent.md) and [specification](docs/spec.md): outcomes, requirements and supporting code/tests.
- [Configuration](docs/configuration.md): repositories, test profiles, browser QA, and groups.
- [Reviews](docs/reviews.md): review policy, evidence, and GitHub verdicts.
- [Agents](docs/agents.md): models, roles, and skills.
- [Operations](docs/operations.md): updates, backups, and recovery.
- [Traceability](docs/traceability.md): optional IntentBond integration.
- [All documentation](docs/README.md): guides and technical references.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and testing, and
[SECURITY.md](SECURITY.md#reporting-a-vulnerability) for vulnerability reports.

## License

[MIT](LICENSE). Bundled third-party files retain their own licenses; see
[third-party notices](THIRD_PARTY_NOTICES.md).
