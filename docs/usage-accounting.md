# Provider usage accounting

The pinned Codex ACP 1.10.0 bridge used the last model request as the usage for a
whole prompt. The factory runtime patch now computes the difference between the
root thread's cumulative counters before and after each ACP prompt. Multiple
model requests, tool cycles and provider retries represented in those counters
are included once. The SDK receives this delta, not the cumulative session total.
Normal responses, command responses, typed failures and cancellations share the
same conversion. Context-window usage updates retain their upstream meaning.

Each prompt emits a native `Factory provider usage` event with its baseline,
latest and last counters, notification count, delta, cancellation flag and
missing/reset reason. The transcript and `metrics.json` retain it. Measurement
uses these events when available, deduplicates prompt IDs, and excludes events
already present when a conversation resumes. SDK counters remain separately
available as `sdk_usage` for comparison. A response failure still emits observed
counters during cleanup; it does not fabricate an SDK response.

A new session starts with a known zero baseline. A session loaded, resumed or
forked into a new adapter process needs an observed baseline; without one its
first prompt's usage is **unknown**, never the whole historical total. Subsequent
prompts use the newly observed counters. A counter reset or malformed numeric
field makes that prompt unknown; later prompts can recover with a valid baseline.
No notification means unknown rather than reuse of the last prompt's usage.

Raw provider input includes cached input, and output includes reasoning output.
The ACP conversion separates uncached input and cache reads; the factory's
provider-evidence measurement keeps input inclusive. Do not add cache reads to
input or reasoning to output. Old records remain unchanged and their counters
are unverified; the patch cannot recover their missing history.

These are **observed provider counters**, not billed spend. Cancelled or broken
transports can omit final notifications. A late counter at cleanup is recorded
separately rather than silently changing an already-returned response. Delegated
thread accounting is unknown unless independently collected; child notifications
are not attributed to the parent. Subscription cost and billed cost remain null.
Do not use these records to assert complete experiment cost.

The patch verifies the pinned package version and exact integration points, and
fails the image build if the upstream lifecycle changes. Regression tests run the
actual adapter conversion and response functions with synthetic counters; they
make no provider requests and are not pilot consumption measurements.
