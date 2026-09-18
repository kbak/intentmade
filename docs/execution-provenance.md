# Execution provenance

Each native DockerWorkspace start records the requested image tag separately from
its actual container ID, image ID, repository digests, platform, root filesystem
layers and Docker daemon ID. The observation inspects the **container**, then its
immutable image ID, on the daemon that launched it. A tag retarget cannot substitute
a newer image. The controller and worker each report their own Python, SQLite,
installed SDK/workspace/Agent Server/automation/checker/adapter package versions,
Codex/ACP versions, OFT JAR hash and installed factory workflow fingerprint.

Task metrics retain the controller's observation before worker execution. A copy
is available to the worker through `FACTORY_EXECUTION_MANIFEST`; invocation
receipts bind its ID and hash to check-time runtime and source observations.
Controller and feedback checks retain their own manifests, including failures and
continuations. The portable bundle additionally provides the frozen source and
policy identities used for acceptance. An observed working-tree hash is not a
replacement for the portable source-stability guard.

The host image is unknown when the worker's daemon cannot inspect it. A nested
worker image is never represented as the host image. Missing package, daemon or
runtime identities remain null. No full environment, Docker config or credential
store is serialized. Keep credentials out of command arguments, which are part of
the declared execution record.

A repository may require observed identities in its controller configuration:

```json
{"required_environment": {
  "observed.worker_image.image_id": true,
  "observed.worker_image.daemon_id": true,
  "observed.worker_runtime.python": "3.13.15"
}}
```

`true` means a non-null observation is required; any other value requires equality.
Failure blocks the worker before implementation and retains the observation in
metrics. Requirements cannot select `intended` fields. Different runtimes are
valid unless the accepted contract constrains them.

Finite native recipes automatically retain their wrapper runtime in the native
completion receipt. Their request accepts `source` and `required_environment`.
This observation describes the finite wrapper, **not** an arbitrary descendant.
For a Python qualification script, launch the supplied wrapper using the desired
interpreter inside the environment being qualified:

```sh
python -m provenance python --out /projects/artifacts/case-a/environment.json \
  --repo /path/to/repo -- qualification.py --case A
```

It records the manifest, checks optional `--require requirements.json`, then runs
the script in that exact Python process. `-- -m package` also works. A script that
fails keeps its original manifest, and reusing the same output path is refused.
Use distinct outputs for deliberately compared environments. This command can be
the argv command in a `configure.py finite` request; it uses the existing native
completion lifecycle and uploads the same bundled provenance module.

`provenance run --out ... -- command ...` instead describes the wrapper process
only. Applications launched in other containers/interpreters must capture there;
the controller cannot infer their Python/SQLite versions. For application-owned
container images, retain a daemon-side inspection at that launch boundary as
well. A wrapper outside that container must leave its identity unknown. Historical
pilot evidence is not changed or filled in from later observations.
