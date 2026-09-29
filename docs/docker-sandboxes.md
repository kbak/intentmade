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
    "kit": "/opt/factory/config/sandbox-kit",
    "profiles": "/absolute/host/path/to/test-profiles",
    "publish_host": "127.0.0.1"
  }
}
```

`profiles` is optional when no external test profile is selected. `publish_host`
defaults to loopback. A containerized controller can reach the loopback worker
API using native Docker `--network host`. For private-bridge publishing, verify
that the controller can reach the configured host address; this depends on the
host's networking, particularly under WSL.
The worker API retains its per-job session key.
Repository registrations cannot override `worker_runtime`, and operator runtime
options are not copied into repository task policy.

VM CPU/memory/storage are configured in the Kit. The default backend's
`worker_memory_mb`, `worker_cpus`, `worker_pids` and `test_daemon_pids` settings do
not configure this VM. No whole-VM PID limit was found in sbx 0.46.0; Docker tests
can use native per-container limits. Import-size and free-disk checks remain
shared. Do not treat these two backends' resource guarantees as identical.

## Controller deployment requirements

Enabling this backend requires updating the controller deployment;
`compose.yaml` does not configure it automatically:

- Install the same sbx CLI in the controller. Mount its local daemon socket and
  select it with Docker's native `DOCKER_SANDBOXES_API=unix:///run/sbx/sandboxd.sock`.
- The controller needs Docker sign-in state and a writable native configuration
  directory for refresh coordination. Keep this state in the trusted controller,
  never in a worker or job mount.
- Match the controller/daemon filesystem permissions. The native worker uses
  UID 1000 and the default Canvas image uses UID 10001. Configure shared volume
  permissions to accommodate both identities.
- Mount the job storage at the **same absolute path** on host and controller,
  and set the existing `FACTORY_DATA` deployment setting to that path. Native
  sbx workspace mounts preserve absolute paths. Sharing only `/workspaces`
  inside the controller when it is a different path on the host is insufficient.
  Use normal persistent job storage rather than `/tmp`, which Codex's workspace
  permission mode ordinarily permits writing outside the repository too.
- Expose selected profiles at their configured host paths inside the controller.
  Only each job directory, selected read-only profiles and frozen read-only
  inputs are passed to the worker. The adapter provides `/factory-tests/NAME`
  and `/factory-inputs` aliases and the existing `docker` service hostname.

These are deployment paths, identity and access settings, not a new controller
service. No host Docker socket is passed to the worker. The v2 privileged
workload setting operates inside its VM.

## Acceptance probe

Run in the prepared controller with the installed factory Python environment:

```bash
PYTHONPATH=/opt/factory/workflows python /tests/check_docker_sandboxes.py \
  --kit /opt/factory/config/sandbox-kit \
  --publish-host 127.0.0.1 \
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

## Current limitations

Real Codex OAuth refresh/logout, a full Canvas task/publication cycle, and a
production Compose rollout remain separate acceptance work. Worker image identity
is currently recorded as unavailable in Docker-specific provenance fields;
`required_environment` requirements for those fields fail closed. The requested
runtime configuration and observed worker package/code identities are retained.

[Docker v2 Kit contract](https://docs.docker.com/ai/sandboxes/customize/kits-v2/),
[native workspace semantics](https://docs.docker.com/ai/sandboxes/usage/).
