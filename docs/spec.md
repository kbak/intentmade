# IntentMade specification

The user outcomes and rationale are recorded in [intent.md](intent.md). This
technical contract defines the required behavior and constraints, with links to
implementation and tests. Each requirement declares which intent it supports through OFT `Covers`.
A document move or added intent link does not change a promise or its revision.

The [scope](../scope.json) is the maintained checking policy. The
[traceability guide](traceability.md) describes how IntentBond checks
this repository and what those checks establish.

## Feature map

Each short name below is a link to a requirement whose ID is `req~im-NAME~REVISION`.
The boundary column identifies areas needing more detailed requirements or checks.

| Capability | Requirements | Boundary / remaining work |
| --- | --- | --- |
| Authorization and scheduling | [deployment-authority](#keep-deployment-authority-separate), [approval-snapshot](#bind-issue-approval-to-content), [issue-ownership](#claim-eligible-issues-and-preserve-prior-ownership), [issue-deduplication](#retain-failed-issue-attempts-across-polls), [repository-locks](#serialize-work-for-each-repository), [explicit-resume](#resume-only-from-a-fresh-explicit-answer), [task-identity](#bind-retained-tasks-to-their-repository-and-base-branch) | Account/team review eligibility, poll priority, daily budgets and naming details need more detailed requirements. |
| Build, repair and publication | [publication-gate](#publish-only-validated-changes-as-drafts), [bounded-repair](#bound-repair-and-preserve-the-approved-request), [needs-input](#stop-on-unanswered-product-questions), [group-publication](#validate-the-whole-group-before-sequential-publication) | Scripted lifecycle; no live model or GitHub publication. |
| Custody and resource boundaries | [git-transfer](#import-commits-without-worker-git-configuration), [failed-work-retention](#preserve-work-when-export-or-retention-fails), [safe-controller-writes](#write-job-files-without-following-worker-links), [bounded-git](#bound-git-imports-and-retained-patches), [disk-admission](#reject-new-jobs-when-disk-is-low), [worker-credentials](#use-the-selected-backend-credential-boundary) | Archive expansion, kernel isolation, VM networking and resource enforcement need separate requirement/probe decomposition. |
| Independent review and maintenance | [review-evidence](#derive-review-verdicts-from-native-evidence-and-complete-coverage), [immutable-pr-comparison](#review-the-captured-base-and-head), [pr-publication](#recheck-review-identity-and-ci-before-posting), [review-retry](#reconcile-uncertain-review-publication), [review-report-integrity](#preserve-findings-through-report-consolidation), [feedback-authority](#accept-maintenance-feedback-only-from-eligible-sources), [feedback-lifecycle](#deduplicate-and-recheck-maintenance-feedback) | Native read-only enforcement, model finding quality and live API behavior are not established by scripted tests. |
| Browser acceptance | [browser-evidence](#require-browser-checks-screenshots-and-unchanged-source), [browser-gap-acceptance](#make-accepted-browser-gaps-explicit) | Live browser interaction and screenshot delivery probe not selected by the regression command. |
| Inputs and continuation | [input-bytes](#verify-and-freeze-declared-input-artifacts), [input-restart](#bind-input-declarations-across-task-restarts), [repair-context](#reuse-only-matching-retained-repair-context) | Input size/count combinatorics and bounded continuation record limits need further boundary tests. |
| Portable workflow | [portable-plan](#retain-an-explicitly-accepted-planning-package), [intent-consistency](#check-affected-product-and-task-intent-in-existing-review), [repository-policy](#capture-verification-policy-from-a-pinned-project-commit), [portable-handoff](#export-source-bound-continuation-and-review-evidence) | Explicit package gates are controller-enforced; request-only delegated planning remains agent guidance. No live model or GitHub qualification. |
| IntentBond integration | [trace-opt-in](#enable-traceability-through-explicit-configuration), [trace-source-gate](#match-controller-evidence-to-exported-source-and-policy), [trace-assessment](#require-semantic-accounting-beyond-a-green-trace-graph), [scope-identity](#distinguish-scope-bytes-from-canonical-policy), [trace-retention](#retain-bounded-invocation-evidence-including-failures) | Deployment activation requires explicit configuration; fixture annotations are excluded through the trusted scope. |
| Observability and operator automation | [execution-provenance](#keep-observed-environment-identity-distinct-from-intent), [finite-completion](#record-finite-execution-before-acknowledgement), [finite-timeout](#stop-timed-out-finite-recipes), [measurement-outcomes](#preserve-attempts-and-unknown-measurements), [startup-retry](#retry-only-a-typed-pre-work-startup-timeout) | Provider counter conversion, finite registration consistency, full CLI/setup and backup/restore promises remain deferred. |
| Runtime validation | [runtime-build-inputs](#runtime-test-image-matches-candidate-patch-inputs) | A matching build manifest checks consistency; it does not attest an untrusted image. |
| Deployment configuration repository, pilot applications and upstream projects | Outside this repository's product baseline | Examples and dependency pins are captured; sibling repositories and upstream internals have separate contracts. |

## Checking and verification limits

The scope imports maintained guides, core workflow/runtime/CLI/build mechanisms
and the full test tree. Both intent and specification are selected through `docs`;
the coverage floor requires intent → req and req → impl/utest. `trace_exclude` omits fixture annotations from the graph;
fixtures remain captured, available to tests and selected for review.

The runner uses an offline disposable container pinned by immutable image ID.
Candidate workflows, scripts, tests, runtime source and fixture configuration are
mounted read-only. Before executing tests it compares the image build manifest
with runtime/dependency inputs and refuses a mismatch. Rebuild and deliberately
update the trusted image pin when those inputs change. Load that image into the
job daemon for factory use; a local ID is not a registry distribution reference.

Public [source CI](../.github/workflows/ci.yml) runs locked lint and the host
CLI/Compose fixtures without model credentials. It does not replace this full
runtime check or the native isolation probes. A separate
[secret scan](../.github/workflows/secrets.yml) checks reachable history using
upstream rules and a synthetic detection self-check. Neither establishes that
operational evidence is safe to publish; follow the
[sharing procedure](portable-workflow.md#continue-elsewhere).

JUnit records named test-method outcomes, and explicit `oft_id` metadata connects
125 linked test artifacts to execution observations. The scope requires every
listed artifact to pass and rejects skipped cases. These associations identify
executed assertions; they do not establish that the assertions adequately verify
every clause. Approval, structural coverage and runtime evidence remain distinct.

## Properties and follow-up checks

Existing verification is example-based unittest with scripted service/agent
responses, subtest matrices, and selected real Git/OFT/subprocess operations.
The current suite does not use Hypothesis generators or formal models.

| Priority / requirement | Existing domain and evidence | Proposed follow-up after review |
| --- | --- | --- |
| High: approval-snapshot, deployment-authority | Finite edit-before/same-second/after cases and captured-policy rejection tests. | Generate event/order and policy-change sequences; publication must never occur under stale approval. Include successful reapproval as well as rejection. |
| High: publication-gate, group-publication, review-retry | Scripted failure, successful repair, partial group publication and lost-response cases. | State-machine properties over validation/retention/post/retry/crash transitions; valid final source required before every push; reconcile ambiguous receipts. No claim of exactly-once GitHub effects. |
| High: finite-timeout | Direct and descendant processes stop on timeout; the receipt reports FAILED. | Mutation validation should also reject direct-child-only termination. |
| High: trace-source-gate, scope-identity | Real Git/OFT with altered policy, missing evidence, failed current invocation, byte/canonical identity and export drift. | Generated stale/mixed invocation, source and policy permutations plus one bounded live factory task; retain producer trust assumptions. |
| Medium: input-bytes, input-restart | Fixed valid bytes, missing/digest/link corruption and changed declarations. | Boundary matrices for 0/64/65 names, exact/overflow size and total limits; successful valid sets must remain usable. |
| Medium: git-transfer, safe-controller-writes, bounded-git | Real bundle, link/FIFO, opened-directory replacement and compressed patch amplification cases. | Broaden path/ref/operation sequences and crash recovery; do not confuse process limits with hard storage quotas. |
| Medium: worker-credentials, review-evidence | Scripted credential calls and native event/coverage validation. | Run existing native credential, project-trust, read-only and network probes against the selected runtime/Kit. Measure actual reviewer accuracy separately. |

## Requirements

Per-item limitations qualify the evidence, not the promise. Documentation,
implementation links and assertion links collectively support review.

### Keep deployment authority separate
`req~im-deployment-authority~1`

Repository registrations must not override deployment runtime, resource limits or factory-wide authorization requirements. Current operator label-approval requirements apply to automatic issue jobs at scheduling, build and publication boundaries. Explicit operator submissions carry their own content-bound authority and do not require a repository label.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [workflow_settings](../workflows/deployment.py), [check_issue_authorization](../workflows/deployment.py).

Existing assertions: [test_repository_cannot_override_any_deployment_field](../tests/test_deployment.py), [test_saved_scheduler_cannot_bypass_new_requirement_or_start_triage](../tests/test_deployment.py), [test_captured_issue_build_cannot_reintroduce_unapproved_work](../tests/test_deployment.py), [test_personal_issue_snapshot_cannot_publish_after_operator_requires_approval](../tests/test_deployment.py).

### Select new issue intake explicitly
`req~im-issue-intake~1`

Issue intake defaults to `manual`; only `manual` and `automatic` are valid modes.
Manual mode admits new issue work only through an explicit operator submission,
without discovering or triaging new issues or creating approval labels. A saved
scheduler must use the current registration's mode; missing or repointed
registrations do not authorize automatic intake. Explicit `automatic` mode polls eligible issues with optional label policy,
ownership checks and deduplication.
PR maintenance, requested reviews and explicit replies to retained tasks remain
available under the configured schedule in either mode.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Implementation: [intake policy](../workflows/deployment.py),
[poll and implement_issue](../workflows/monitor.py),
[submit-issue](../scripts/configure.py).

Assertions: [manual intake regressions](../tests/test_manual_intake.py),
[automatic intake regressions](../tests/test_automatic_issues.py).

### Bind issue approval to content
`req~im-approval-snapshot~1`

Manual submissions capture the repository, issue number and title/body digest before dispatch. Build and publication must match that snapshot; missing snapshots or changed content stop work. Labels do not authorize or invalidate an explicit submission. In automatic intake with an approval label configured, capture the issue title, body and latest label event. Title/body edits at or after the approval timestamp, including the same second, require renewed approval; unavailable approval history must stop work. Publication must match the captured specification and approval.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [approved_issue](../workflows/approval.py), [publish](../workflows/run.py).

Existing assertions: [test_title_and_body_edits_after_or_during_approval_second_fail_closed](../tests/test_approval.py), [test_missing_or_partial_history_fails_closed](../tests/test_approval.py), [test_publication_requires_unchanged_snapshot_before_any_push](../tests/test_approval.py).

### Claim eligible issues and preserve prior ownership
`req~im-issue-ownership~1`

New issue work selects open, unassigned issues subject to the selected intake authority. Explicit resubmission may reuse a retained claim belonging to this factory; it cannot adopt another owner's assignment. Claim the configured assignee before building. A failed run releases only an assignment it acquired; a retained assignment is preserved.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [docs/workflows.md](../docs/workflows.md).

Implementation: [issue_eligible](../workflows/policy.py), [implement_issue](../workflows/monitor.py).

Existing assertions: [test_only_approved_unassigned_issues_are_eligible](../tests/test_factory.py), [test_success_claims_before_build_and_keeps_assignment](../tests/test_factory.py), [test_failure_releases_only_our_own_assignment](../tests/test_factory.py).

Evidence limit: NEEDS_INPUT pauses the owned task; it does not release the assignment like an implementation failure.

### Retain failed issue attempts across polls
`req~im-issue-deduplication~1`

Persist failed specification/approval attempts outside disposable workers so repeated polls and pruning recent automation history do not retry them. A new approval or eligible explicit continuation can permit another attempt.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [poll](../workflows/monitor.py).

Existing assertions: [test_failures_remain_deduplicated_after_history_pruning_and_metadata_changes](../tests/test_automatic_issues.py), [test_attempt_is_persisted_before_work_and_relabeling_permits_retry](../tests/test_scheduler.py).

### Serialize work for each repository
`req~im-repository-locks~1`

Acquire the repository build locks before preparing a grouped build. A busy repository must prevent the group from starting; unrelated repositories may be handled independently.

Covers:
- `intent~im-operate-reliably~1`

Needs: impl, utest

Documentation: [docs/configuration.md](../docs/configuration.md).

Implementation: [build_group](../workflows/run.py), [lock](../workflows/common.py).

Existing assertions: [test_group_acquires_all_repository_locks_before_preparing_work](../tests/test_factory.py).

Evidence limit: Existing test demonstrates lock contention within a process; cross-process fairness and crash recovery are not established here.

### Resume only from a fresh explicit answer
`req~im-explicit-resume~1`

Only recorded failed or waiting tasks with a new explicit user resume: answer may continue. Consumed answers and ordinary chat messages must not redispatch work; pausing a repository schedule disables immediate reply dispatch.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [docs/workflows.md](../docs/workflows.md).

Implementation: [resume_reply](../workflows/reporting.py), [queue_reply](../workflows/replies.py).

Existing assertions: [test_only_new_explicit_user_reply_can_resume_same_snapshot](../tests/test_recovery.py), [test_saved_answer_dispatches_immediately_and_duplicate_notification_is_idempotent](../tests/test_replies.py), [test_paused_schedule_disables_immediate_dispatch](../tests/test_replies.py).

### Bind retained tasks to their repository and base branch
`req~im-task-identity~1`

Before reusing a retained task store, validate its configured repository, actual fetch and push destinations and base branch. Changing the repository or base branch requires a new task ID; preserved task branches remain stable across naming-default changes.

Covers:
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [task_repository](../workflows/run.py).

Existing assertions: [test_repointed_registration_is_rejected_before_any_build_or_push](../tests/test_scheduler.py), [test_override_push_url_and_non_github_origins_are_rejected](../tests/test_scheduler.py), [test_changed_base_branch_requires_a_new_task_id](../tests/test_scheduler.py), [test_new_naming_defaults_preserve_a_retained_published_branch](../tests/test_scheduler.py).

### Publish only validated changes as drafts
`req~im-publication-gate~1`

Build publication requires the current configured tests, applicable browser QA, traceability checks when enabled, and independent review to pass. Failed validation retains work without publishing. Publication creates or updates the task PR; new PRs are drafts. Merge and deployment remain operator actions.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [README.md](../README.md).

Implementation: [_execute_build](../workflows/run.py), [publish](../workflows/run.py).

Existing assertions: [test_failed_tests_or_review_keep_branch_and_never_publish](../tests/test_factory.py), [test_actual_upstream_push_and_draft_payload_use_task_branch](../tests/test_factory.py), [test_accepted_gaps_reach_review_and_publication_but_do_not_waive_tests_or_review](../tests/test_browser_qa.py).

Evidence limit: Explicitly accepted infrastructure browser gaps use PASSED_WITH_GAPS. External GitHub CI triggered by a push remains outside factory isolation.

### Bound repair and preserve the approved request
`req~im-bounded-repair~1`

Run implementation and configured validation with at most the configured repair attempts after the initial attempt. Repairs retain the original approved request and failed-check context. Exhausted test failures stop before independent review; blocked review infrastructure does not spend a code repair attempt.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [README.md](../README.md).

Implementation: [_execute_build](../workflows/run.py), [stage_test_evidence](../workflows/run.py).

Existing assertions: [test_bounded_repair_uses_test_failure_and_original_spec_before_publication](../tests/test_recovery.py), [test_exhausted_test_failures_do_not_start_review](../tests/test_recovery.py), [test_blocked_reviewer_does_not_spend_a_code_repair_attempt](../tests/test_recovery.py).

### Stop on unanswered product questions
`req~im-needs-input~1`

Unanswered product questions produce NEEDS_INPUT before review or publication and retain the questions. An IMPLEMENTED response with unanswered questions is invalid.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [docs/workflows.md](../docs/workflows.md).

Implementation: [check_questions](../workflows/run.py), [implementation_attempt](../workflows/run.py).

Existing assertions: [test_questions_stop_before_review_and_publication_and_save_detail](../tests/test_recovery.py), [test_structured_implementation_cannot_hide_unanswered_questions](../tests/test_recovery.py).

### Validate the whole group before sequential publication
`req~im-group-publication~1`

Every selected repository must pass tests and the complete grouped change must pass review before any draft is published. Publish changed repositories sequentially and record successful PR URLs so partial publication remains visible and can be continued with the same task.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [docs/configuration.md](../docs/configuration.md).

Implementation: [_execute_build](../workflows/run.py).

Existing assertions: [test_group_validation_retention_and_partial_publication](../tests/test_factory.py).

Evidence limit: No atomic cross-repository GitHub transaction is promised; live lost-response behavior is not tested here.

### Import commits without worker Git configuration
`req~im-git-transfer~1`

Run Git commands consuming worker-controlled metadata in the worker. After teardown, import a bounded regular bundle containing only the expected task ref, validate objects and reject rewritten retained history. Independent review uses a fresh clone of retained commits.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [export_task](../workflows/transfer.py), [import_task](../workflows/transfer.py), [review_changes](../workflows/run.py).

Existing assertions: [test_reject_symlink_and_fifo_exports_without_reading_them](../tests/test_transfer.py), [test_reject_unexpected_refs_and_rewritten_history](../tests/test_transfer.py), [test_hostile_git_config_stays_in_worker_and_review_uses_fresh_clone](../tests/test_transfer.py).

### Preserve work when export or retention fails
`req~im-failed-work-retention~1`

If export or evidence retention fails, preserve the job workspace and record its recovery location. A cleanup failure after a completed attempt must be reported without replacing the completed result.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [docs/operations.md](../docs/operations.md).

Implementation: [job_directory](../workflows/cleanup.py), [implementation_attempt](../workflows/run.py).

Existing assertions: [test_failed_export_keeps_workspace_and_original_error](../tests/test_recovery.py), [test_cleanup_failure_does_not_abort_completed_attempt](../tests/test_recovery.py), [test_interrupted_retention_preserves_the_only_workspace_copy](../tests/test_traceability.py).

### Write job files without following worker links
`req~im-safe-controller-writes~1`

Controller writes into worker-visible job storage must reject linked ancestors and atomically replace file entries without following existing symlinks, hardlinks or FIFOs. Each grouped browser QA worker receives a fresh job root and source.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [parent](../workflows/job_files.py), [write_text](../workflows/job_files.py), [browser_checks](../workflows/run.py).

Existing assertions: [test_existing_symlink_hardlink_and_fifo_are_replaced_without_following](../tests/test_job_files.py), [test_ancestor_replacement_after_open_keeps_write_on_opened_directory](../tests/test_job_files.py), [test_grouped_browser_qa_uses_a_fresh_root_and_source_for_each_worker](../tests/test_job_files.py).

### Bound Git imports and retained patches
`req~im-bounded-git~1`

Bundle inspection, import and patch generation use bounded output, a timeout and a per-process address-space ceiling. Only a complete patch replaces the prior artifact; overflow fails the attempt while preserving imported task history. Limits come from deployment configuration.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [docs/configuration.md](../docs/configuration.md).

Implementation: [stream_git](../workflows/resource_limits.py), [retain_patch](../workflows/transfer.py).

Existing assertions: [test_git_children_have_memory_and_time_bounds_and_bounded_diagnostics](../tests/test_resource_limits.py), [test_amplified_patch_is_rejected_without_losing_imported_work](../tests/test_transfer.py).

Evidence limit: Process/address-space and patch limits are not a disk quota.

### Reject new jobs when disk is low
`req~im-disk-admission~1`

Before admitting new job work, reject available storage below the configured minimum without deleting retained work. This admission check is not a hard disk quota.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [docs/configuration.md](../docs/configuration.md).

Implementation: [require_disk_space](../workflows/resource_limits.py), [worker](../workflows/sandbox.py).

Existing assertions: [test_low_disk_prevents_job_admission_without_deleting_retained_work](../tests/test_resource_limits.py).

### Use the selected backend credential boundary
`req~im-worker-credentials~1`

Codex DockerWorkspace workers receive the Codex credential and may refresh it through the versioned controller store. Native OpenHands DockerWorkspace workers receive only the selected API-backed LLM configuration, without reading or copying the Codex credential or other Canvas secrets. Docker Sandboxes workers must not read, upload or synchronize that controller credential; they use host-managed Codex proxy access. Unsupported harness/runtime combinations must fail before worker creation. GitHub publication credentials remain in the parent workflow.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [worker](../workflows/sandbox.py), [sync_credential](../workflows/sandbox.py).

Existing assertions: [test_native_success_and_failure_never_read_upload_or_sync_real_login](../tests/test_worker_credentials_backend.py), [test_docker_workspace_retains_native_openhands_login_and_refresh](../tests/test_worker_credentials_backend.py), [native OpenHands credential separation](../tests/test_native_harness.py).

Evidence limit: Backend control flow is scripted. Live proxy behavior, native VM policy and host credential custody need the separate native probes; these tests do not establish all credential separation.

### Derive review verdicts from native evidence and complete coverage
`req~im-review-evidence~1`

Independent review must have the expected completed reviewer execution, valid structured findings and coverage for every selected file. Codex supplies native parent/child identity; native OpenHands uses a separate reviewer conversation with identity, completion and results captured by the controller and bound to its review run. Agent prose cannot supply an execution receipt. Missing or malformed evidence blocks publication. The controller computes blockers and verdicts rather than accepting a coordinator success claim.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [evaluate and validate_coverage](../workflows/review.py), [controller receipt capture](../workflows/agent.py), [review execution](../workflows/review.py).

Existing assertions: [test_incomplete_unverified_or_invalid_reviews_cannot_pass](../tests/test_specialist_review.py), [test_coordinator_cannot_override_native_results](../tests/test_specialist_review.py), [test_missing_duplicate_wrong_and_unavailable_coverage_block](../tests/test_ocr_review.py), [native OpenHands receipt validation](../tests/test_native_harness.py).

Evidence limit: Unit tests use synthetic events and controller receipts. The separate [native OpenHands protocol probe](../tests/check_native_harness.py) uses a scripted local provider and is not part of the required JUnit execution links. Reviewer accuracy and resistance to misleading source text remain unmeasured.

### Select the worker harness through an OpenHands profile
`req~im-agent-profile~1`

Resolve the operator-selected saved agent profile through OpenHands' profile/settings APIs. Support Codex ACP and API-backed native OpenHands on DockerWorkspace, retaining Codex as the default. Capture settings per worker; later profile edits apply to subsequent workers. Retain nonsecret profile identity and model in execution provenance. Workflow stages determine their permissions, skills and MCP servers; profile selection cannot widen review authority. Missing or unsupported profiles must fail without silently selecting another harness.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [agents](agents.md).

Implementation: [operator selection](../workflows/deployment.py), [profile activation](../scripts/configure.py), [profile resolution](../workflows/harness.py), [worker capture](../workflows/sandbox.py), [stage construction](../workflows/agent.py), [report selection](../workflows/reporting.py).

Existing assertions: [profile and stage boundaries](../tests/test_native_harness.py), [captured model and missing-profile rejection](../tests/test_agent_profile.py), [operator-only configuration](../tests/test_deployment.py).

### Restrict native OpenHands review tools
`req~im-native-read-only~1`

Native OpenHands review and report conversations expose only bounded source inspection and non-executing built-in tools. Source inspection cannot edit files, execute arbitrary commands, make network requests or delegate; resolved paths must remain within controller-selected roots, and Git inspection disables external diff/textconv. Ambient plugins must not add tools or hooks to factory workers or report readers. Missing or truncated evidence remains unavailable rather than establishing completed coverage.

Covers:
- `intent~im-protect-work~1`

Needs: impl, utest

Documentation: [agents](agents.md).

Implementation: [stage permissions](../workflows/agent.py), [report reader selection](../workflows/reporting.py), [reader](../workflows/factory_reader.py), [plugin policy](../runtime/patch_native_harness.py).

Existing assertions: [reader and plugin boundaries](../tests/test_native_harness.py).

Evidence limit: Bounded tools constrain available operations; they do not measure the model's accuracy or whether it truthfully reports inspected coverage. Native provider availability and billing are not established by offline tests.

### Review the captured base and head
`req~im-immutable-pr-comparison~1`

Standalone PR review obtains changed-file evidence from the captured immutable base/head comparison. Mismatched or incomplete inventories must stop review; the mutable PR-files endpoint must not substitute another comparison.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [docs/reviews.md](../docs/reviews.md).

Implementation: [comparison_files](../workflows/review_requests.py), [_review_pr](../workflows/monitor.py).

Existing assertions: [test_same_size_head_swap_cannot_change_captured_files](../tests/test_review_requests.py), [test_wrong_base_or_incomplete_inventory_fails_closed](../tests/test_review_requests.py).

Evidence limit: Count and base checks use scripted GitHub responses; real API inventory semantics remain an external dependency.

### Recheck review identity and CI before posting
`req~im-pr-publication~1`

Before publishing a standalone review, recheck current scope, PR head, base and required CI against retained review evidence. Stale or incomplete reviews cannot post approval. Saved protocol 2 or older reviews require fresh review. Operator-configured path overrides may select required CI only when every path in the complete immutable comparison matches, including both paths of a rename. Mixed changes retain default requirements; all reported failures and pending checks still block review.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [docs/reviews.md](../docs/reviews.md).

Implementation: [publish](../workflows/review_publication.py), [load_saved](../workflows/review_publication.py).

Existing assertions: [test_changed_head_or_failed_ci_never_posts](../tests/test_review_publication.py), [test_retargeted_or_advanced_base_cannot_publish_or_reuse_old_review](../tests/test_review_publication.py), [test_old_protocol_publication_retry_runs_a_fresh_review](../tests/test_review_publication.py).

### Reconcile uncertain review publication
`req~im-review-retry~1`

A publication retry may reuse a saved report only if its evidence still validates. Check already-submitted factory reviews before repeating the POST after a lost response, avoiding a duplicate verdict for the same comparison.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [docs/reviews.md](../docs/reviews.md).

Implementation: [publish](../workflows/review_publication.py), [load_saved](../workflows/review_publication.py).

Existing assertions: [test_lost_response_is_reconciled_without_duplicate_post](../tests/test_review_publication.py), [test_failed_publication_reuses_verified_artifacts_without_rerunning_specialists](../tests/test_review_publication.py).

### Preserve findings through report consolidation
`req~im-review-report-integrity~1`

Consolidated review reports must account for every source finding exactly once and remain bound to the original review evidence. Presentation edits cannot downgrade a blocker or change the factory-computed verdict.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [SECURITY.md](../SECURITY.md).

Implementation: [validate_report](../workflows/review_report.py).

Existing assertions: [test_missing_repeated_and_invented_source_ids_are_rejected](../tests/test_review_report.py), [test_merged_advisory_cannot_downgrade_a_blocker_or_its_severity](../tests/test_review_report.py), [test_report_cannot_be_reused_with_changed_native_evidence](../tests/test_review_report.py).

### Accept maintenance feedback only from eligible sources
`req~im-feedback-authority~1`

With PR feedback enabled, use eligible current reviews and unresolved inline feedback. General comments require the factory mention; humans require current write/maintain/admin permission and bots require explicit allowlisting. Exclude factory reviews and review bodies superseded by approval.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [docs/workflows.md](../docs/workflows.md).

Implementation: [collect](../workflows/feedback.py).

Existing assertions: [test_author_permission_bot_allowlist_and_own_generated_reviews](../tests/test_feedback.py), [test_current_submitted_reviews_and_unresolved_roots_only](../tests/test_feedback.py), [test_later_approval_supersedes_review_body](../tests/test_feedback.py), [test_general_comments_need_explicit_mention_and_edits_get_new_digest](../tests/test_feedback.py).

### Deduplicate and recheck maintenance feedback
`req~im-feedback-lifecycle~1`

Record each feedback revision before repair so repeated polls do not repeat it. Failed or interrupted repairs require explicit continuation. Recheck feedback before publication and withhold a stale repair.

Covers:
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [docs/workflows.md](../docs/workflows.md).

Implementation: [remember](../workflows/feedback.py), [verify_revision](../workflows/followup.py), [maintain](../workflows/followup.py).

Existing assertions: [test_green_ci_admits_feedback_once_and_interruption_requires_resume](../tests/test_feedback.py), [test_feedback_change_before_publication_is_rejected](../tests/test_feedback.py).

Evidence limit: Rolling repair budgets and verified GitHub artifact-service retries remain deferred detail.

### Require browser checks, screenshots and unchanged source
`req~im-browser-evidence~1`

When configured changed paths select browser QA, run it on the retained commit after tests. PASS requires passed named checks and valid retained screenshots; source changes, missing evidence or unavailable verification prevent ordinary PASS.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/configuration.md](../docs/configuration.md).

Implementation: [complete](../workflows/browser_qa.py), [run](../workflows/browser_qa.py), [retain](../workflows/browser_qa.py).

Existing assertions: [test_pass_requires_actual_checks_and_named_evidence](../tests/test_browser_qa.py), [test_qa_runs_on_retained_commit_and_rejects_source_mutation](../tests/test_browser_qa.py).

Evidence limit: Scripted checks do not establish UI correctness; the real Chromium evidence probe is separate.

### Make accepted browser gaps explicit
`req~im-browser-gap-acceptance~1`

Only explicit named infrastructure-gap directives from maintainer continuations can grant acceptance for the unchanged issue specification. The last directive replaces prior acceptance and {} revokes it. Accepted checks remain BLOCKED in evidence; startup/evidence/source failures, observed defects, failed tests and incomplete review still block publication.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/configuration.md](../docs/configuration.md).

Implementation: [accepted_gaps](../workflows/browser_qa.py), [accept_infrastructure_gaps](../workflows/browser_qa.py), [_execute_build](../workflows/run.py).

Existing assertions: [test_only_explicit_named_gap_directives_grant_and_replace_acceptance](../tests/test_browser_qa.py), [test_acceptance_preserves_unverified_checks_and_rejects_other_failures](../tests/test_browser_qa.py), [test_accepted_gaps_reach_review_and_publication_but_do_not_waive_tests_or_review](../tests/test_browser_qa.py).

### Verify and freeze declared input artifacts
`req~im-input-bytes~1`

Verify declared input sizes and SHA-256 through link-rejecting descriptors, freeze the bytes outside writable job storage, and reject unsafe names/references, links, non-regular files and changed bytes. Retain rejection receipts. Implementation and review receive the same verified read-only input set.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [docs/input-artifacts.md](../docs/input-artifacts.md).

Implementation: [validate](../workflows/input_artifacts.py), [copy_verified](../workflows/input_artifacts.py), [prepared](../workflows/input_artifacts.py), [mounts](../workflows/sandbox.py).

Existing assertions: [test_restarts_and_both_workers_share_verified_readonly_mount](../tests/test_input_artifacts.py), [test_bad_digest_missing_size_symlink_and_hardlink_rejected_with_receipt](../tests/test_input_artifacts.py), [test_parent_symlinks_unsafe_names_and_duplicates](../tests/test_input_artifacts.py).

Evidence limit: Read-only mount construction is asserted; actual kernel/VM mount enforcement is a separate probe. Limit boundary combinations are not exhaustively tested.

### Bind input declarations across task restarts
`req~im-input-restart~1`

A retained task must reject changed or removed input declarations. Restart verifies original sources and frozen copies; missing or corrupted originals/copies fail explicitly rather than silently reusing cached inputs.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [docs/input-artifacts.md](../docs/input-artifacts.md).

Implementation: [bind_task](../workflows/input_artifacts.py), [freeze](../workflows/input_artifacts.py), [verify_frozen](../workflows/input_artifacts.py).

Existing assertions: [test_corrupted_cache_and_changed_source_rejected_on_restart](../tests/test_input_artifacts.py), [test_task_binding_cannot_change_on_a_later_run](../tests/test_input_artifacts.py).

### Reuse only matching retained repair context
`req~im-repair-context~1`

Repair-context reuse requires matching task, repositories, approved contract/bases, candidate commits, policies, inputs and retained evidence bytes. Unknown or changed runtime identity falls back to full fresh context. Preserve the authoritative contract and continue current validation and fresh independent review.

Covers:
- `intent~im-deliver-changes~1`

Needs: impl, utest

Documentation: [docs/repair-context.md](../docs/repair-context.md).

Implementation: [previous](../workflows/repair_context.py), [staged](../workflows/repair_context.py), [prompt](../workflows/repair_context.py).

Existing assertions: [test_stale_contract_base_policy_candidate_and_artifact_are_not_reused](../tests/test_repair_context.py), [test_runtime_change_falls_back_without_weakening_current_contract](../tests/test_repair_context.py), [test_real_pipeline_repairs_with_verified_context_and_fresh_review](../tests/test_repair_context.py).

### Enable traceability through explicit configuration
`req~im-trace-opt-in~1`

Without traceability_scope, preserve ordinary test/context behavior without requiring optional IntentBond packages. An enabled scope must resolve to a valid file inside the configuration directory and becomes the sole test-command source; conflicting registration commands are errors.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/traceability.md](../docs/traceability.md).

Implementation: [projects](../workflows/common.py), [scope_for](../workflows/traceability/__init__.py).

Existing assertions: [test_absent_scope_keeps_ordinary_workflow_without_optional_packages](../tests/test_traceability_config.py), [test_reference_must_be_a_relative_path_inside_config](../tests/test_traceability_config.py), [test_scope_and_registration_cannot_define_two_test_commands](../tests/test_traceability_config.py), [test_scope_contents_are_resolved_from_config_and_own_the_test_command](../tests/test_traceability_config.py).

Evidence limit: Factory activation uses a deployment-owned scope; keep it aligned with the root checking policy.

### Match controller evidence to exported source and policy
`req~im-trace-source-gate~1`

For enabled repositories, only the current successful controller invocation, retained evidence, trusted frozen scope and matching exported candidate may satisfy the traceability gate. Missing/stale evidence, a failed current invocation, candidate policy tampering or exported source drift must prevent completion.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/traceability.md](../docs/traceability.md).

Implementation: [check](../workflows/traceability/__init__.py), [collect](../workflows/traceability/__init__.py), [checks_passed](../workflows/traceability/__init__.py).

Existing assertions: [test_candidate_cannot_weaken_the_frozen_floor](../tests/test_traceability.py), [test_exported_contents_must_match_tested_dirty_contents](../tests/test_traceability.py), [test_missing_evidence_cannot_complete_even_with_passing_review](../tests/test_traceability.py), [test_failed_current_invocation_cannot_reuse_a_passing_bundle](../tests/test_traceability.py), [test_modified_worker_scope_cannot_replace_controller_policy](../tests/test_traceability.py).

Evidence limit: Digests match contents; unsigned evidence still relies on trusted execution/custody. Passing structural checks do not prove semantic adequacy.

### Require semantic accounting beyond a green trace graph
`req~im-trace-assessment~1`

Independent review of an opted-in change must account for all selected changed paths using requirement identities from the relevant source index. Missing relationships require concrete remediation and block as CHANGES_REQUESTED; uncertain or absent assessments are incomplete and block. Existing links may suffice without inventing new IDs.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/traceability.md](../docs/traceability.md).

Implementation: [validate_assessments](../workflows/review.py), [evaluate](../workflows/review.py).

Existing assertions: [test_existing_relationships_pass_without_new_ids](../tests/test_traceability_review.py), [test_concrete_gap_blocks_even_if_reviewer_labels_overall_result_pass](../tests/test_traceability_review.py), [test_uncertain_is_incomplete_not_covered_or_a_fabricated_defect](../tests/test_traceability_review.py), [test_absent_required_assessment_cannot_pass](../tests/test_traceability_review.py).

Evidence limit: Path accounting is mechanical; relevance of cited implementation/assertions remains reviewer judgment.

### Distinguish scope bytes from canonical policy
`req~im-scope-identity~1`

New captured scopes must preserve and validate exact source bytes, size and digest against parsed policy before export. Formatting changes byte identity without changing canonical policy identity. Legacy parsed-only tasks retain unknown source-file identity, explicitly labeled as legacy serialization.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/scope-identity.md](../docs/scope-identity.md).

Implementation: [capture](../workflows/traceability/scope_identity.py), [export](../workflows/traceability/scope_identity.py).

Existing assertions: [test_exact_source_survives_native_payload_and_worker_freezing](../tests/test_traceability_config.py), [test_formatting_changes_byte_identity_but_values_change_policy_identity](../tests/test_traceability_config.py), [test_captured_source_tampering_fails_and_legacy_source_is_unknown](../tests/test_traceability_config.py).

### Retain bounded invocation evidence including failures
`req~im-trace-retention~1`

After worker teardown, retain designated agent/controller invocation directories, including incomplete checks, with hashes and byte counts. Reject links, special files, unknown artifacts and evidence exceeding the fixed retention bounds; on retention failure preserve the original workspace. The ledger itself does not grant acceptance.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/traceability-retention.md](../docs/traceability-retention.md).

Implementation: [retain](../workflows/traceability/retention.py), [copy_file](../workflows/traceability/retention.py).

Existing assertions: [test_incomplete_and_unregistered_checks_stay_visible](../tests/test_traceability_retention.py), [test_links_special_files_unknown_files_and_size_limits_are_not_exported](../tests/test_traceability_retention.py), [test_manifest_hashes_cover_preserved_rejected_bundle](../tests/test_traceability_retention.py).

Evidence limit: Current constants are 200 invocations, 32 MiB per file and 512 MiB per attempt. These are code constants, not deployment resource settings.

### Keep observed environment identity distinct from intent
`req~im-execution-provenance~1`

Observe DockerWorkspace image identity from the launched container and immutable image ID, not a retargeted tag or another daemon. Missing observations remain unknown. Required observed identities must block work when absent/mismatched; intended values cannot substitute.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [docs/execution-provenance.md](../docs/execution-provenance.md).

Implementation: [image_observation](../workflows/provenance.py), [required](../workflows/provenance.py), [worker](../workflows/sandbox.py).

Existing assertions: [test_image_comes_from_container_not_retargeted_tag_or_host](../tests/test_provenance.py), [test_unknown_required_identity_blocks_without_fallback_to_intent](../tests/test_provenance.py).

Evidence limit: Docker inspection responses are scripted. The pinned regression image does not qualify a deployed VM.

### Record finite execution before acknowledgement
`req~im-finite-completion~1`

Finite recipes record execution success/failure before native completion acknowledgement. Preserve completed execution when acknowledgement fails and refuse a second execution for the same native run ID. Distinct dispatched runs are not guaranteed exactly once.

Covers:
- `intent~im-operate-reliably~1`

Needs: impl, utest

Documentation: [docs/finite-automations.md](../docs/finite-automations.md).

Implementation: [run](../workflows/finite.py), [inspect_status](../workflows/finite.py).

Existing assertions: [test_success_and_failure_both_report_native_terminal_outcome](../tests/test_finite.py), [test_lost_callback_preserves_execution_and_never_reexecutes](../tests/test_finite.py).

### Stop timed-out finite recipes
`req~im-finite-timeout~1`

Kill the timed-out recipe process group and report failure before waiting for the native watchdog. An abrupt wrapper termination may leave an unfinished receipt requiring reconciliation.

Covers:
- `intent~im-operate-reliably~1`

Needs: impl, utest

Documentation: [docs/finite-automations.md](../docs/finite-automations.md).

Implementation: [execute](../workflows/finite.py).

Existing assertions: [test_timeout_is_reported_failed_without_waiting_for_native_watchdog](../tests/test_finite.py), [test_timeout_stops_parent_and_descendant](../tests/test_finite.py).

Evidence limit: The regression checks both direct and descendant process termination and failure reporting on Linux. It does not establish receipt reconciliation after abrupt wrapper termination.

### Preserve attempts and unknown measurements
`req~im-measurement-outcomes~1`

Metrics preserve failed attempts after later success and distinguish returned stages from successful validation. Missing/reset usage and zero or absent cost remain unknown. Measurement failure must not change authorization or replace the original workflow failure.

Covers:
- `intent~im-operate-reliably~1`

Needs: impl, utest

Documentation: [docs/measurements.md](../docs/measurements.md).

Implementation: [update](../workflows/measurements.py), [record_agent](../workflows/measurements.py), [task](../workflows/measurements.py).

Existing assertions: [test_repaired_build_retains_both_checks_and_source_versions](../tests/test_measurements.py), [test_metrics_write_failure_preserves_original_error](../tests/test_measurements.py), [test_usage_is_a_delta_and_zero_cost_is_unknown](../tests/test_measurements.py), [test_usage_reset_or_unavailable_baseline_is_not_fabricated](../tests/test_measurements.py).

Evidence limit: Measurement collection is not an independent spend ledger or a measure of prevented defects.

### Retry only a typed pre-work startup timeout
`req~im-startup-retry~1`

A typed ACP startup timeout may retry once before agent activity. Authentication, spawn, unknown initialization and prompt failures must not automatically replay work; timeout after agent activity must not retry.

Covers:
- `intent~im-operate-reliably~1`

Needs: impl, utest

Documentation: [docs/reviews.md](../docs/reviews.md).

Implementation: [run_with_startup_recovery](../workflows/agent.py).

Existing assertions: [test_temporary_startup_timeout_retries_once_and_retains_reason](../tests/test_startup_recovery.py), [test_repeated_timeout_is_bounded](../tests/test_startup_recovery.py), [test_auth_spawn_and_unknown_initialization_failures_are_not_retried](../tests/test_startup_recovery.py), [test_timeout_after_agent_activity_does_not_replay](../tests/test_startup_recovery.py).

### Runtime test image matches candidate patch inputs
`req~im-runtime-build-inputs~1`

The factory's container regression runner checks the selected immutable image's
build manifest against the captured candidate before running tests. Changed,
added, removed or executable-mode-changed runtime files, dependency locks, build
recipe or Docker ignore policy require a matching rebuild and a reviewed image
pin. A source overlay cannot establish evidence for stale installed patches.
This fingerprint is a consistency check, not an attestation of an untrusted image.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Implementation: [manifest and verify](../runtime/build_inputs.py), [regression runner](../scripts/check_factory.py).

Existing assertions: [test_patch_additions_removals_modes_and_lock_changes_require_rebuild](../tests/test_build_inputs.py).

### Retain an explicitly accepted planning package
`req~im-portable-plan~2`

Planning must freeze supplied intent, specification and plan documents with their
hashes and selected repository base commits without dispatching implementation.
Explicit submission accepts the package only for its matching task, specification
and bases. Documents must enter task source before implementation and be supplied
to implementation/review. Accepted intent, spec and acceptance metadata remain
unchanged in the candidate; plan.md may evolve but must remain a nonempty bounded
UTF-8 regular file. Retain the original plan for comparison by the existing reviewer
when changed, preserve the current plan on repair, and identify both in handoffs.
A retained task must not silently adopt a different accepted package. Existing
request-only and issue workflows retain their authorization and need no package.

Covers:
- `intent~im-deliver-changes~1`
- `intent~im-authorized-work~1`

Needs: impl, utest

Documentation: [portable workflow](portable-workflow.md).

Implementation: [sdlc.py](../workflows/sdlc.py), [configure.py](../scripts/configure.py), [run.py](../workflows/run.py).

Existing assertions: [planning, identity and lifecycle checks](../tests/test_sdlc.py).

Evidence limit: scripted agents and real Git exercise package custody and rejection;
they do not establish plan quality or the quality of delegated request-only planning.

### Check affected product and task intent in existing review
`req~im-intent-consistency~1`

Each factory build's existing independent review must return exactly one compact
intent-consistency assessment per selected repository, with references and rationale,
regardless of OFT enablement. Reported new/worsened conflicts must request changes;
missing, duplicate or uncertain assessments must leave review incomplete. Preserve
these judgments in saved/rendered review evidence and pass conflicts to repair.
Do not add a separate model pass or require task documents for bounded fixes.
Agent guidance should reuse one current product overview, link local task context
and update only affected living documents, without scanning task history.

Covers:
- `intent~im-deliver-changes~1`
- `intent~im-review-changes~1`

Needs: impl, utest

Documentation: [portable workflow](portable-workflow.md#keep-product-context-small-and-current).

Implementation: [review.py](../workflows/review.py), [run.py](../workflows/run.py),
[factory review skill](../workflows/skills/factory-review/SKILL.md).

Existing assertions: [intent assessment gates](../tests/test_intent_review.py).

Evidence limit: scripted judgments exercise gating and retention, not model ability
to detect semantic drift. Controller checks do not prove references are relevant.

### Capture verification policy from a pinned project commit
`req~im-repository-policy~1`

An operator may select a repository-owned scope using a full Git commit ID and
relative regular-file path. Capture its exact bytes and source identity into the
native job payload. Candidate edits and later catalog updates must not replace
that selected policy. Reject symbolic revisions, missing/linked files, conflicting
scope sources and a second registration test command. External scopes remain supported.

Covers:
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [scope configuration](traceability.md#configuration).

Implementation: [scope_identity.py](../workflows/traceability/scope_identity.py), [common.py](../workflows/common.py).

Existing assertions: [pinned policy and configuration checks](../tests/test_sdlc.py).

### Export source-bound continuation and review evidence
`req~im-portable-handoff~1`

Build execution must maintain readable and structured handoffs on success and
failure, identifying the task phase, retained source, accepted artifact versions
when supplied, verification outcomes, available review findings and next action.
Draft publication must include source-bound evidence and accepted-artifact links
without altering the validated commit; repair publication must preserve other PR
text while refreshing its evidence section. A stopped run's supported evidence
files must be exportable with hashes; missing checks cannot be represented as
passes. Do not introduce a separate REVIEW.md review policy.

Covers:
- `intent~im-deliver-changes~1`
- `intent~im-review-changes~1`
- `intent~im-trust-evidence~1`

Needs: impl, utest

Documentation: [portable workflow](portable-workflow.md#continue-elsewhere).

Implementation: [sdlc.py](../workflows/sdlc.py), [export_handoff.py](../scripts/export_handoff.py), [run.py](../workflows/run.py).

Existing assertions: [handoff, publication content and failed-run export](../tests/test_sdlc.py).

Evidence limit: local tests verify content and identity; live GitHub posting and
another tool's ability to interpret the exported artifacts are not established.
