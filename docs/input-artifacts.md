# Declared input artifacts

Submit historical databases, archives or prior evidence as explicit controller inputs. Stage files under the deployment's `artifacts/` directory, then use:

```sh
./scripts/factoryctl submit my-project accepted.md --task migration-v2 --inputs inputs.json --run
```

`inputs.json` is a JSON array:

```json
[{"name":"v1.db","reference":"release-v1/database.db","sha256":"<64 lowercase hex characters>","size":8192,"producer":"owner/repository@<full commit>; creation command or receipt"}]
```

`reference` is relative to the controller's `/projects/artifacts`, not the current directory. The controller does not fetch URLs or regenerate files. A producer is a declared identity, not a signature or proof that a producer ran. The accepted specification should identify which inputs are authoritative.

Submission captures the declaration in the native automation's `job.json`. Before implementation, the controller verifies each source through link-rejecting file descriptors, freezes its bytes in a content-addressed directory outside the writable job mount, and retains `input-artifacts.json` with the task, approved bases, contract digest and verification outcome. Failed verification also leaves a receipt. A retained task binds its input declaration: changing or removing inputs requires a new task ID. Restart verifies both original sources and retained copies; missing originals fail explicitly.

Implementation and independent review use the same `/factory-inputs/<name>` paths, mounted read-only. The job's test daemon also sees only that input set, read-only, so a test container can bind `/factory-inputs:/factory-inputs:ro`. Copy to scratch for mutable database tests. The controller's artifact collection and GitHub credentials are not mounted in workers. A manifest is available at `/factory-inputs/manifest.json`; it is rechecked before each worker starts.

Limits: 64 flat, unique names, 256 MiB per file, 1 GiB total. Absolute/traversing references, symlinks, hardlinks, non-regular files, unexpected frozen files and mismatched bytes are rejected. Artifact retention follows the existing durable task storage policy; no automatic input garbage collection is performed. Existing requests without inputs remain valid.

For native controller use, `configure.py submit ... --inputs-json '<array>'` accepts the same declaration. Python callers can pass `input_artifacts_declared` to `build_group`.
