# Bounded implementation repair context

A failed implementation attempt now leaves an immutable `continuation-N.json` and the complete `approved-request.md` in its run artifacts. The record binds repository identities, task, approved bases, retained candidate commits, contract digest, test/scope policy digest, declared inputs and observed worker image/code/model. It records reported completed work, changed paths, a bounded failure excerpt, remaining action and hashes of retained test/review evidence. Reported work is not proof of correctness.

A controller-owned pointer in each retained bare task repository permits the next attempt, including a separate native run, to locate the record. All group pointers must agree. Changed contracts, bases, candidates, policies, inputs or artifact bytes reject reuse. Unknown or changed image/code/model identity uses the full fresh-context prompt. Selection and fallback reasons remain in `continuation-selection-N.json`.

For a matching repair, the controller stages the complete contract, record and selected prior evidence through the verified input-artifact mechanism. The implementation worker receives a short repair prompt with usable read-only paths. It must read the complete authoritative contract. This avoids repeating historical narrative in the active handoff; it does **not** establish fewer total model input tokens, since the contract still has to be read. At most 24 selected evidence files and a 64 KiB continuation record are carried forward; larger/extra evidence remains in the prior run and is listed as omitted. Current tests, source verification and fresh independent review still run. Review does not inherit implementation memory. No transcript, credential store or ACP session ID is copied into these records.

Every attempt has distinct artifacts. Repeating an already executed attempt in the same native run is rejected before work; dispatch a new native run to retry. Historical input bytes and prior evidence are referenced by digest, never regenerated. Existing error reporting retains a `continuation_error` if an exceptional failed attempt cannot save its repair record.

## Native session resume evaluation

The installed OpenHands SDK 1.49.1 has native durable conversation persistence, `ACPAgent.acp_resume_session_id`, and a working-directory guard for IDs restored from conversation state. It can restart the ACP process and load the provider session; a protocol-level unknown session falls back to a new one. The explicit resume field is secret-bearing and assumes the caller has checked workspace compatibility. The SDK also offers per-conversation provider data directories with `acp_isolate_data_dir`.

`scripts/check_native_resume.py` verified actual OpenHands → Codex/ACP resume after closing and recreating a conversation, using retained state, unchanged working directory and a local scripted provider in a disposable container. It made no paid model calls and used no user credentials. The resumed provider session matched the original. Post-restart usage correctly remained unknown until a new provider counter baseline; it was not reported as zero.

Our production workers discard their server/provider state and recreate workspaces at different paths. Therefore this change retains **fresh native implementation conversations with verified context**. Full cross-worker session reuse is not enabled. A safe native resume implementation would need all of the following before promotion:

1. A private per-task provider/conversation state volume, isolated from review and other tasks, plus stable workspace paths or a supported rebinding contract.
2. Controller verification of task, source, approved contract, model and runtime before attaching state; no raw session identifiers in issue comments or artifacts.
3. Native secret injection/refresh without archiving authentication files, with cancellation, cleanup and concurrent-run locking tested.
4. A tested fresh-conversation fallback when state is unavailable or incompatible, followed by current validation and independent review.
5. A matched live-model comparison that establishes correctness and useful latency or usage improvement. Native support alone is not evidence of a speedup.

The evaluation found no need to replace OpenHands. The missing lifecycle work is in our factory; an upstream bug is not established by this probe.

## Bounded comparison

`scripts/check_repair_context.py` runs three counterbalanced pairs of scripted repairs against identical base/candidate commits, the same complete contract, a deliberately broken 30-minute boundary, real Git/OFT/tests and fresh scripted independent review. It retains each run's full evidence and source bundle. Use the pinned traced image with `workflows`, `tests` and `runtime` on `PYTHONPATH`; supply `--output` in a fresh writable directory. `scripts/check_native_resume.py` additionally requires the factory's Codex seccomp profile and a fresh container home.

The September 18 qualification is recorded in `docs/measurements/repair-context-2026-09-18.json`. All six repairs passed current checks and separate review. The injected identities and agents are explicitly scripted. Timings measure mechanical workflow overhead only; repeated agent discovery, real first useful edit latency, human review effort, delegated tokens and billed cost remain unmeasured. Do not infer production ROI or token savings from prompt byte counts. A live-model trial belongs in the separately predeclared comparison protocol.
