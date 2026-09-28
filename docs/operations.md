# Operations

Run commands from the tooling checkout:

| Command | Purpose |
| --- | --- |
| `./scripts/factoryctl status` | Show service health. |
| `./scripts/factoryctl logs` | Read service logs. |
| `./scripts/factoryctl projects` | List repository configuration. |
| `./scripts/factoryctl factories` | List repository groups. |
| `./scripts/factoryctl up` | Build the default runtime or use the supplied image, then start. |
| `./scripts/factoryctl configure` | Apply configuration and workflow changes. |
| `./scripts/factoryctl down` | Stop services while retaining data. |

Use Canvas's **Automate** view to inspect runs and pause schedules. Let active
jobs finish before restarting services. The host and Docker must remain running
for polling; configure Docker to start at boot if unattended operation is
needed.

`FACTORY_DATA_DIR` selects the runtime data directory. Task branches live under
`workspaces/tasks/`; reports, test logs, patches and PR metadata are
under `artifacts/`. Back up this directory and the Compose native state volume
together. Keep runtime data and `.env` out of Git, and preserve volumes during
routine shutdowns (`down`, without `-v`).

Workers receive the Codex credential; the GitHub credential stays in the parent
workflow. Implementation, triage and independent review run in separate
disposable workers. Builders retain public internet access and Docker tests;
worker access to the management daemon, other jobs and the host's private
networks is blocked. See [SECURITY.md](../SECURITY.md) for the single-operator
trust model, retained state and update procedure. Containers share the Docker
host's Linux kernel. Normal completion and handled failures clean up job
resources; a host crash can require manual cleanup.

## Update the runtime and workflows

Pause schedules and let active jobs finish. Back up runtime data and the Compose
native state volume before replacing the image.

For the default image, `./scripts/factoryctl up` builds and starts the runtime.
If `FACTORY_IMAGE` selects a custom image, rebuild it explicitly first; see
[traceability runtime setup](traceability.md#runtime-setup).

After startup, refresh uploaded workflows while retaining the stored GitHub
credential:

```sh
docker compose exec -T canvas python /opt/factory/configure.py configure --paused
```

Check service health and enable the intended schedules in Canvas. New tasks
receive the updated workflow and skills. Existing conversations retain their
saved instructions. `factoryctl configure` also refreshes workflows, but first
imports the currently active host `gh` credential.

## Recover retained work

Failed tasks retain their branches and evidence. Continue a manual build with
the same `--task` ID, or use `retry-issue` for an issue after resolving the failure.
A changed repository registration or base branch requires a new task ID.

If export or evidence retention fails, the complete job workspace stays available.
Inspect `recovery-workspace.txt` in the run artifacts before retrying or removing
resources. `cleanup-warnings.log` identifies jobs whose cleanup was deferred.
Keep `workspaces/issue-attempts/` with backups so restored schedules preserve
failed-attempt deduplication.

## Observe runs

Canvas provides phase status, logs, persistent task conversations, and retained
reports. [Measurements](measurements.md) explain timing, usage, and outcome
records. [Repair context](repair-context.md) describes what carries into a retry;
[execution provenance](execution-provenance.md) describes recorded runtime identity.

## Dependency pins

The runtime base image and Docker test daemons use immutable registry digests.
The nested daemon pulls its pinned image only when that digest is absent from
its cache; a first startup needs Docker Hub access. Locally built factory images
still transfer through `docker save`/`load`.

Node tools use [`runtime/node/package-lock.json`](../runtime/node/package-lock.json).
Builds run `npm ci --ignore-scripts` to verify the complete dependency tree without
package lifecycle scripts. Saved ACP paths remain compatible with the locked
installation. Update `package.json` and regenerate the lock with `npm install
--package-lock-only --ignore-scripts` in `runtime/node`, using the runtime image's
Node/npm versions. Review the changes and run the native ACP/browser probes.

Update both Compose daemon references together, verifying the digest against
the official `docker` image. The optional traceability image also checks its
third-party wheels against [a hash lock](../docker/traceability-requirements.txt).
OS packages installed through apt retain the distribution's signed update path.
Pins prevent unreviewed dependency changes; review and test security updates
before advancing them.
