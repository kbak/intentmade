# Docker Sandboxes (experimental, opt-in)

The optional backend runs each factory worker and its Docker test daemon inside
one local Docker Sandboxes VM. OpenHands still supplies `RemoteWorkspace`,
conversations, file APIs and the existing versioned Codex credential handling.
The default backend remains `DockerWorkspace`.

The adapter targets **sbx 0.46.0 and native v2 Kits**. A Kit describes the
workload; the Docker Sandboxes runtime supplies VM isolation. The tested v3
runtime does not apply the privileged capability needed by this nested Docker
workload. Do not substitute a v3 descriptor without repeating acceptance tests.

## Build and load the worker

Install the official local `sbx` runtime, enable KVM on Linux, sign in, and start
its daemon. Local operation requires a Docker account; this backend never uses
`--cloud`. Keep `SBX_NO_TELEMETRY=1` in the operator environment; the adapter also
sets it for its own CLI calls. No Docker subscription is provisioned here.

Build the normal factory image first, then its small Kit wrapper:

```bash
docker build -f docker/runtime.Dockerfile -t intentmade:dev .
docker build -f docker/sandbox-kit.Dockerfile \
  --build-arg FACTORY_IMAGE=intentmade:dev -t intentmade:sandbox-kit .
docker save -o /tmp/intentmade-sandbox-kit.tar intentmade:sandbox-kit
sbx template load /tmp/intentmade-sandbox-kit.tar
rm /tmp/intentmade-sandbox-kit.tar
sbx kit validate ./docker/sandbox-kit
```

No registry push is needed. The adapter uses `--pull never`; load the reviewed
image into the local runtime before selecting it. For reproducible deployment,
pin the Kit's image to an immutable digest available in that runtime.

The wrapper supplies Docker's required `agent` UID 1000, sudo configuration and
empty image entrypoint. The worker runs as this native agent user. OpenHands'
private persistence directories are under `/home/agent/.openhands`; controller
credentials and Docker's sign-in files are not mounted into the worker.

## Operator configuration

Copy [the Kit](../docker/sandbox-kit/spec.yaml) to an operator-owned directory
accessible to the controller. Adjust its native CPU, memory, Docker storage
and network settings there. The example allows common GitHub, OpenAI and Docker
endpoints; application-specific registries still need explicit configuration.
The effective policy also depends on the operator's global Docker policy.

Set `worker_runtime` in `config/deployment.json`, keeping any existing resource
and authorization settings:

```json
{
  "worker_runtime": {
    "backend": "docker-sandboxes",
    "command": "/usr/local/bin/sbx",
    "kit": "/absolute/host/path/to/sandbox-kit",
    "profiles": "/absolute/host/path/to/test-profiles",
    "publish_host": "192.168.1.10"
  }
}
```

`profiles` is optional when no external test profile is selected. Replace the
example `publish_host` with a private address of **your host** that is reachable
from a Docker bridge container. WSL may provide a private host address on `lo`;
verify connectivity on your machine. Loopback works for a host-run controller,
but `factoryctl up` rejects it for the containerized deployment. The worker API
retains its per-job session key. Canvas keeps its existing loopback-only UI port
and bridge network.
Repository registrations cannot override `worker_runtime`, and operator runtime
options are not copied into repository task policy.

VM CPU/memory/storage are configured in the Kit. The default backend's
`worker_memory_mb`, `worker_cpus`, `worker_pids` and `test_daemon_pids` settings do
not configure this VM. No whole-VM PID limit was found in sbx 0.46.0; Docker tests
can use native per-container limits. Import-size and free-disk checks remain
shared. Do not treat these two backends' resource guarantees as identical.

## Controller deployment requirements

After loading the worker image and selecting the backend, use the normal commands:

```bash
./scripts/factoryctl init
./scripts/factoryctl up
./scripts/factoryctl status
./scripts/factoryctl codex-login
```

`factoryctl up` asks Compose to resolve the existing `.env` and bind paths, then
applies the selected backend to that model. It validates the Kit and native
runtime access and builds a small controller image with the operator's UID/GID.
It stops this deployment's Canvas, adjusts ownership of its existing state
volume, and starts Canvas with a health check. The image keeps the upstream
OpenHands user and home directory. Build and prerequisite failures occur before
stopping Canvas. An existing Canvas login and task history remain in its volume.
The same ownership preparation applies when switching back to the default
backend, using that controller image's own OpenHands UID/GID.

The generated deployment mounts the native daemon socket, CLI and Docker sign-in
state into the trusted controller. It uses Docker's native `DOCKER_SANDBOXES_API`
setting and disables sbx telemetry. Kit and profile paths are read-only. Job
storage is mounted at the same absolute host/controller path, using the existing
`FACTORY_DATA` setting. The separate privileged Docker daemon service is omitted;
each VM supplies its own test daemon. Use `factoryctl` for lifecycle operations;
plain `docker compose up` still selects the original deployment.

Use persistent job storage rather than `/tmp`, which Codex's workspace mode can
write outside the repository. Only each job directory, selected read-only
profiles and frozen read-only inputs are passed to the worker. Controller
credentials, native Docker sign-in files and the host Docker socket are excluded.
The adapter supplies the existing `/factory-tests/NAME`, `/factory-inputs` and
`docker` hostname conventions. Kit privilege applies inside the VM.

## Worker identity

The controller queries native `sbx inspect` before handing the worker to an agent.
Its `image_digest` is retained as `observed.worker_image.image_id`, with the
sandbox name and backend. A `required_environment` rule can require that digest.
Missing or malformed native identity fails such a requirement closed. The
controller also retains requested runtime configuration and observed package/code
identities through the existing provenance path.

Docker daemon ID, registry digests and rootfs layer lists remain unavailable for
this backend; they are not fabricated from image tags. Requirements for those
Docker-specific fields still fail closed. Use the observed native image digest
when pinning a Docker Sandboxes worker.

## Acceptance probe

Copy the probes into the prepared controller and use its installed factory
Python environment. Substitute your configured Kit, CLI and shared paths:

```bash
docker compose cp tests canvas:/tmp/factory-acceptance-tests
docker compose exec -T canvas python /tmp/factory-acceptance-tests/check_docker_sandboxes.py \
  --kit /absolute/host/path/to/sandbox-kit \
  --publish-host 192.168.1.10 \
  --workspaces /absolute/shared/workspaces --native-probes
```

The probe uses dummy worker credentials and a scripted parent profile. It
creates a disposable VM through the real shared worker context and checks
credential delivery, shared edits, read-only inputs/profiles, tests, Git bundle
export, offline nested Docker and a worker session longer than the native idle
grace period. The adapter deletes the VM before the retained bundle is consumed.
It also reuses the existing Codex kernel permission assertions in the native
agent's home. `--native-probes` adds the existing project-trust, specialist-review,
startup-recovery and browser-evidence checks. No model call or GitHub publication
occurs. Native session logs use the existing bounded, redacted retention path.

## Credential and workflow acceptance

`tests/check_worker_credentials.py` runs real disposable workers against separate
native encrypted fixture stores. It checks refresh writeback, concurrent logout
and replacement-login protection. It does not touch the controller's real login
unless explicitly given `--live-refresh-and-logout`.

Run that flag **only in a disposable Canvas with a separate account login and no
active jobs**. It ages the credential's refresh timestamp, makes one real Codex
request, verifies refreshed tokens reached Canvas, and logs that test Canvas out
through the native OpenHands endpoint. It never prints tokens or restores an old
refresh token. Logout prevents stale writeback; it does not forcibly cancel a
worker already holding a credential.

`factoryctl codex-logout` removes the factory's `CODEX_AUTH_JSON` through the
native secret API. This login is separate from Canvas's LLM subscription card;
logging out of that card does not log out Codex ACP workers.

```bash
docker compose exec -T canvas python /tmp/factory-acceptance-tests/check_worker_credentials.py
# Only against the disposable deployment and its separate login:
docker compose exec -T canvas python /tmp/factory-acceptance-tests/check_worker_credentials.py \
  --live-refresh-and-logout
```

For the complete workflow, submit the included fixture through Canvas:

```bash
./scripts/factoryctl submit factory-smoke tests/fixtures/smoke-spec.md \
  --task vm-check --no-publish --run
```

The disposable deployment must use `tests/config` and its matching fixtures and
profiles. Check the retained task result, tests, exact-commit independent review,
execution manifests and final native automation callback. Factory runs disable
the SDK's optional per-workspace completion callback while the shared workflow
owns its final result; an intermediate workspace cannot finish the automation.
A GitHub publication check additionally needs an operator-selected disposable
repository and permission to push a branch and create a draft PR.

The runtime remains opt-in. Host-specific networking and a GitHub publication
cycle must be checked before adopting it for a live deployment.

[Docker v2 Kit contract](https://docs.docker.com/ai/sandboxes/customize/kits-v2/),
[native workspace semantics](https://docs.docker.com/ai/sandboxes/usage/).
