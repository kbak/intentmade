# Traceability invocation custody

Implementation instructions supply `python -m traceability.invocation ...` for
feedback. Use this command for **every** feedback check. It allocates a UUID and
records the command, requested base/worktree, scope bytes hash, UTC start/end,
duration and exit code beside a fresh portable evidence bundle. Finish candidate
documents/reports before checking; the portable checker freezes its snapshot and
rejects changes to the source during validation. A later pass does not relabel
an earlier rejection.

Controller checks use the same invocation ledger around the existing adapter.
After worker teardown, before job cleanup, the controller retains all
`agent-check-*` and `controller-check-*` directories under the designated check
root. Each gets a hash/size manifest and an entry in
`PROJECT/traceability-invocations-ATTEMPT/index.json`. Incomplete checks and older
unregistered directories remain visible with unknown metadata. Only the
separately selected, freshly verified **controller** bundle can satisfy acceptance.
The ledger contains observations and worker claims, not additional approval.

The index separates feedback iterations from controller checks and the native
controller attempt. External continuations and logical qualification cases remain
unknown unless separately recorded: repeated execution is not a new scenario.
Metrics reference these indexes. Early malformed agent responses still trigger
retention, even before any controller check has been allocated.

Retention accepts flat, allowlisted portable artifacts only, with SHA-256 and byte
counts. It rejects symlinks, linked parent directories, hardlinks, special files,
unknown filenames, over 200 invocations, files over 32 MiB or a retained attempt
over 512 MiB. It does not crawl arbitrary worker directories. If a checker version
adds a new artifact, update the explicit allowlist after review. A retention error
keeps the original workspace and writes the existing recovery receipt; investigate
it before retrying or removing that workspace. Partial copies are not completion.

Checks that bypass the supplied wrapper and write elsewhere cannot be discovered
reliably. They must not be claimed as retained evidence. This preserves the current
OpenHands lifecycle and evidence stores; it is not a new event service.
