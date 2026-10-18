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

Every attempt needing implementation starts a fresh native conversation. A clean
base-only PR update can merge without an implementation agent; conflicts and
feedback use the [focused maintenance path](workflows.md#github-scheduling).
The complete authoritative request remains available outside candidate source.
Maintenance assigns the full checker to the controller instead of duplicating
the full check inside the repair conversation. Provider state,
transcripts, credential stores, and ACP session IDs are not copied between workers.
Current tests, source verification, and fresh independent review still run;
review does not inherit implementation memory.

## Retry an incomplete review

When controller validation succeeded but independent review could not finish,
`resume: retry` can reuse a retained validation checkpoint. The controller checks
the complete source/request/input/policy identity, current validation environment,
and retained evidence bytes, and verifies IntentBond source agreement when enabled.
Matching retries label implementation and project tests `Reused` and run fresh
browser QA when configured, followed by fresh independent review. Review model
selection is independent of the saved test results.

The new run records its decision in `validation-selection.json`, copies the
verified test evidence, and links the original `validation-checkpoint-N.json`.
Changing source, base, substantive answers, inputs, checking configuration or
environment invalidates reuse. Missing/corrupt checkpoints and runs produced
before checkpoint support follow ordinary validation. A checkpoint is consumed
before new work, so a later failure cannot reuse an earlier passing check.
Repeated review outages can retain a new checkpoint; concrete review findings
still trigger bounded repair and new tests. Publication always uses the current
authorization and fresh review result.

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
