# Docker Sandboxes (experimental, opt-in)

The optional backend runs each factory worker and its Docker test daemon inside
one local Docker Sandboxes VM. OpenHands still supplies `RemoteWorkspace`,
conversations and file APIs. Docker owns worker OAuth and credential refresh.
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

## Network policy

Use explicit trusted destinations and verify the effective policy on your host.
Docker's [network-policy contract](https://docs.docker.com/reference/cli/sbx/policy/deny/network/)
does not check an allowed hostname's resolved IP against CIDR denies. Broad
`allow: ["**"]` plus private-address denies therefore permits private services
through DNS names. The v2 Kit reference also lists enforcement limitations for
some pattern types. A valid Kit is not proof of private-network isolation.

The example Kit limits destinations by hostname. Global allow rules can broaden
it, and an allowed hostname resolving to a private address remains a limitation.
Trust the selected hosts and DNS resolution. Add required registries explicitly;
arbitrary web browsing needs a broader policy and accepts a wider boundary.

Run the network probe on the host, using an otherwise unused fixture hostname
that resolves only to a local private IPv4 address. For example, with a DNS name
you control (a public wildcard DNS service resolving encoded IPs also works):

```bash
python3 tests/check_sandbox_network.py --command /absolute/path/to/sbx \
  --kit /absolute/path/to/sandbox-kit \
  --host 192.168.1.10 --private-name sandbox-probe.example.org
```

The probe creates a temporary HTTP server on that address and a fresh VM with
no workspaces or agent credentials. It tests private IP and DNS access with and
without proxy environment handling, and allowed GitHub access. A final explicit
allow, scoped only to that VM, verifies the fixture was reachable; the VM is
then removed. A failure blocks adoption of that policy. Recheck after changing
the Kit, global Docker rules or runtime. This probe does not establish denial
for every protocol, address range or DNS-rebinding scenario.

## Runtime selection

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
./scripts/factoryctl codex-login
./scripts/factoryctl up
./scripts/factoryctl status
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
state into the trusted controller. Native configuration directories retain their
absolute host paths through `XDG_CONFIG_HOME`; only `com.docker.sandboxes`,
`sbx` (read-only bindings), and `sandboxes` are mounted, not the whole host home. It uses Docker's native `DOCKER_SANDBOXES_API`
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

The probe uses Docker’s host login and a scripted parent profile. It
creates a disposable VM through the real shared worker context and checks
placeholder-only authentication, an empty worker secret store, shared edits, read-only inputs/profiles, tests, Git bundle
export, offline nested Docker and a worker session longer than the native idle
grace period. The adapter deletes the VM before the retained bundle is consumed.
It also reuses the existing Codex kernel permission assertions in the native
agent's home. `--native-probes` adds the existing project-trust, specialist-review,
startup-recovery and browser-evidence checks. Add `--model gpt-6-astra/low`
to verify a real ACP subscription request. The default makes no model calls;
neither mode publishes to GitHub. Native session logs use the existing bounded, redacted retention path.

## Credentials

The Kit extends Docker's built-in `codex` Kit, which configures a proxy provider
and placeholder `auth.json`. Real OAuth tokens and refresh stay on the host.
IntentMade never loads Canvas's credential store for this backend and never
uploads `CODEX_AUTH_JSON` into the worker. OpenHands therefore leaves the native
Codex home in place. A missing placeholder fails worker startup with login/Kit
guidance; there is no fallback to copying Canvas tokens.

Before startup, authorize the custom Kit using Docker's native
`~/.config/sbx/credentials.yaml` (or `$XDG_CONFIG_HOME/sbx/credentials.yaml`):

```yaml
bindings:
  openai:
    oauth:
      domains: [auth.openai.com, chatgpt.com]
```

Merge this into existing bindings; do not replace unrelated services. This is
Docker configuration, not an IntentMade schema. Avoid OAuth `passthrough`, which
puts real tokens inside the sandbox. The inherited Kit adds its own explicit
network destinations; review the effective policy when customizing it.

`factoryctl codex-login` runs native `sbx secret set openai --oauth` on the host.
This uses the ChatGPT subscription even though the worker's placeholder file is
API-key shaped. Complete the localhost callback on the machine running the CLI,
or use an SSH tunnel to its callback port when using a remote browser.

`factoryctl codex-logout` runs `sbx secret rm openai --force`. This removes the
host's **global** OpenAI sandbox login, including access from other sandboxes
using that global credential. Native Docker owns propagation to running VMs and
concurrent refresh coordination; IntentMade stores no worker token copy.
Requests already completed or in flight are not undone by logout.

Canvas coordinator chats retain their separate OpenHands login. Use
`factoryctl codex-login --canvas` or `factoryctl codex-logout --canvas` to manage
it. Logging workers out does not delete this coordinator credential. The Canvas
**LLM → ChatGPT subscription** card is a third, separate native LLM connection.

The default `DockerWorkspace` backend still uses OpenHands' encrypted credential
store, real-token delivery and versioned refresh writeback. Its existing
`tests/check_worker_credentials.py` probe checks that lifecycle;
`--live-refresh-and-logout` is destructive and belongs only in a disposable
Canvas with its own login and no active work. Use `check_docker_sandboxes.py`
for the VM backend instead.

Native host refresh across actual expiry is a long-running runtime acceptance
check. A worker POST using a refresh sentinel is not equivalent: the built-in
Codex provider relies on host refresh, not on an in-VM OAuth client.

## Workflow acceptance

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
