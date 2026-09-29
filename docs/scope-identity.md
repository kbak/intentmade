# Scope identity

New native task payloads capture both the validated policy and its exact source-file bytes in `traceability_scope_source`. The source is read once during configuration. Execution validates the captured bytes, size and SHA-256 against the captured policy before exporting them unchanged to the worker. A later deployment-file edit cannot change an already uploaded task.

Every traced attempt retains `scope-identity-N.json` alongside the trusted scope. The implementation prompt, independent review context and result record distinguish:

- `source_file`: deployment-relative reference or `git:COMMIT:PATH` for a pinned repository scope, exact size and SHA-256.
- `worker_file`: exact exported size and SHA-256. New captures preserve the source bytes, including formatting.
- `canonical_policy`: SHA-256 of sorted, compact, ASCII-escaped JSON without a trailing newline. Array order is preserved. This is the portable checker's canonical representation, not a file-byte digest.

Formatting-only edits change byte identity but preserve canonical identity. Policy-value edits change canonical identity. These identities describe the controller-selected policy; supplying a candidate digest never authorizes a policy change. Source bytes inconsistent with the controller's parsed policy fail before implementation, and checker evidence is still verified against the controller's trusted scope and candidate commit.

Older uploaded tasks and direct callers may have only parsed policy values. Their export retains the previous serialization, labels it `legacy_value_serialization`, and records source identity as `null`. Do not infer the original byte digest. Scope-free repositories retain their existing lazy dependency behavior.
