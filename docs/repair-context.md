# Repair context

Each failed implementation attempt saves an immutable `continuation-N.json` and
its complete `approved-request.md`. The record binds the task, repositories,
approved bases, retained candidate commits, contract, test/scope policy, declared
inputs, and observed worker image/code/model. It includes reported completed work,
changed paths, a bounded failure excerpt, remaining actions, and evidence hashes.
Reported progress is not proof of correctness.

## Reuse and fallback

A controller-owned pointer in each retained bare task repository locates the
record, including across native runs. All group pointers must agree. Changed
contracts, bases, candidates, policies, inputs, or artifact bytes reject reuse.
Unknown or changed image/code/model identity uses the full fresh-context prompt.
`continuation-selection-N.json` records the decision and fallback reason.

For a matching repair, the controller stages the complete contract, record, and
selected evidence through the verified [input-artifact mechanism](input-artifacts.md).
The worker receives read-only paths and must read the authoritative contract.
At most 24 evidence files and a 64 KiB record carry forward; omitted evidence
remains in the prior run and is listed in the record.

Every attempt starts a fresh native implementation conversation. Provider state,
transcripts, credential stores, and ACP session IDs are not copied between workers.
Current tests, source verification, and fresh independent review still run;
review does not inherit implementation memory.

Each attempt has distinct artifacts. Repeating an executed attempt within the
same native run is rejected; dispatch a new run to retry. Failure to save a repair
record is reported as `continuation_error`. See [operations](operations.md#recover-retained-work)
for retained workspace recovery.

## Validate the mechanism

`scripts/check_repair_context.py` runs paired scripted repairs against matching
base/candidate commits, the same contract, a deliberately broken boundary, real
Git/OFT/tests, and independent scripted review. It retains full evidence and
source bundles. Use the traceability image with `workflows`, `tests`, and `runtime`
on `PYTHONPATH`, and supply `--output` in a fresh directory.

`scripts/check_native_resume.py` exercises the SDK's native resume path in a
fresh disposable container with a scripted provider and the factory seccomp
profile. It requires no model credentials. These probes validate mechanisms;
they do not establish live-model quality, latency, token savings, or cost.
