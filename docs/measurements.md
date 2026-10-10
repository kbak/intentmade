# Factory measurements

Feature builds, issue fixes, PR maintenance builds, and PR reviews retain
`metrics.json` and `metrics-summary.md` alongside their existing task artifacts.
The persistent Canvas task chat displays the same summary after completion,
failure, or a request for input. Measurement failures do not authorize or block
a change.

The JSON record identifies the task, native automation run, repositories, and
checked commits. It keeps every implementation/repair attempt, controller test
exit codes, review findings, reviewer traceability assessments, browser outcomes,
and references to the detailed evidence. Failed attempts remain in the record
after successful repairs. Each subsequent automation run has its own artifact
directory; the stable task name connects those runs.

Builds and reviews share task lifecycle handling for saved outcomes, measurements,
and terminal reports. Issue and maintenance adapters preserve an inner build's
`PUBLICATION_FAILED` outcome rather than changing it to a generic failure. Reports
render after the instrumented task finishes so their durations include teardown.
The original exception and detailed review evidence remain available for recovery.

## Timing and usage

UTC timestamps identify events; elapsed durations use a monotonic clock. Task
timing covers the instrumented build/review, including its worker setup and
teardown. It excludes earlier scheduler discovery and, for builds, waiting for
the repository lock. OpenHands retains the encompassing automation run timing.

Stage durations are inclusive: `implementation_and_tests` includes the nested
`tests` stage; review and browser stages include their agent sessions. Do not sum
these overlapping durations. A stage marked `COMPLETED` means the call returned,
not that its tests or review passed. Use the recorded outcomes for that conclusion.
Hard termination can leave an unfinished record with null completion/duration;
do not count that as success or zero elapsed time.

Agent measurements snapshot OpenHands conversation statistics before and after
each invocation. This avoids counting a resumed conversation's prior usage again.
The record preserves input, output, cache, and reasoning counts separately,
alongside model identity. Missing fields or decreasing counters remain unknown.
Provider accounting may omit delegated work; these are the counts OpenHands
received, not an independent measurement of every model call. Cost is recorded
only as a positive reported-or-estimated delta, never as billed spend. A zero
cost field does not establish a free run. Failed conversations retain whatever
usage was available before the worker disappeared.

Review findings and missing/uncertain traceability assessments are explicitly
reviewer reports. A cited requirement, passing test command, or coverage link
does not establish requirement satisfaction. Review-only tasks have no controller
test result unless the factory actually recorded one. Tests run by a reviewer
remain in its transcript and report, not invented controller results.

## Read and aggregate

Run these commands from the tooling checkout against the host's artifact paths:

```sh
python3 workflows/measurements.py show ../my-factory/.factory/artifacts/RUN-TASK/metrics.json
python3 workflows/measurements.py report ../my-factory/.factory/artifacts
```

Use `--since YYYY-MM-DD` to select runs by their start date in UTC. Summed
durations are work across runs and may overlap in wall time. The report does not estimate defects
prevented, escaped-defect rates, or the causal benefit of traceability. No
historical records are fabricated from old chat text.

## Record human effort and later outcomes

Human effort, onboarding effort, escaped violations, and incorrect assurance
claims start as unknown. Approval timestamps are not active review minutes, and
an absence of later reports is not evidence of zero defects.

An operator can append a small observation to an existing run:

```sh
python3 workflows/measurements.py note /path/to/metrics.json \
  --kind human_review --minutes 8 --by maintainer \
  --note 'Reviewed the changed authorization contract'

python3 workflows/measurements.py note /path/to/metrics.json \
  --kind escaped_violation --by maintainer \
  --reference 'https://github.com/org/repo/issues/123' \
  --note 'The released boundary behavior violated req~expiration~2'
```

Kinds are `human_review`, `onboarding`, `escaped_violation`,
`incorrect_assurance`, and `false_positive`. Effort entries require finite,
nonnegative minutes; later outcomes require an evidence reference. These are
operator-reported observations, not automatically verified facts; `--by` is
supplied attribution rather than authentication. Record effort once against its
associated run, not again against each repair attempt. Onboarding is separate
from recurring review effort.

Notes append to `observations.jsonl`. They do not rewrite the original verdict,
evidence, or completion summary. `show` displays them after that assessment.
Preserve this file with the rest of the artifact directory. The recorder
does not infer relationships to production incidents, automatically collect
human effort, or change approval policy.

## Runtime refresh

The recorder is included in the normal workflow bundle and runtime image.
Follow the [runtime update procedure](operations.md#update-the-runtime-and-workflows)
after active jobs finish. Existing runs keep their original workflow and
measurements; newly submitted/refreshed workflows produce these records.
