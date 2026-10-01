# Code and security review

Automatic reviews and `factoryctl review` use one **Alibaba Reviewer**, backed by
[Open Code Review](https://github.com/alibaba/open-code-review)'s delegation
procedure. OCR prepares the file inventory and rules; the selected Codex or native
OpenHands agent performs the review. No separate OCR endpoint
or API key is required. Runtime revisions and checksums are pinned in the
[Dockerfile](../docker/runtime.Dockerfile).

Codex review uses native subagent records. Native OpenHands review uses a separate
controller-launched conversation and a retained `*-execution.json` receipt of its
identity, completion and structured result. Both use the same findings and
coverage gates. See [harness selection and permissions](agents.md).

The reviewer runs in a fresh, read-only worker. Implementation reviews use Git
ranges. Standalone PR reviews use GitHub's complete changed-file inventory,
exact-commit source archives, and OCR rule resolution. The controller owns the
rule configuration; repository files cannot waive required coverage or replace
the built-in rules.

| Verdict | Meaning |
| --- | --- |
| `PASS` | Review completed with no blockers and complete required coverage. |
| `CHANGES_REQUESTED` | Blocking findings enter the repair loop for factory builds. |
| `BLOCKED` | Missing source, coverage, native execution evidence, or reports prevent publication without spending a repair attempt. |

Material code defects and high/critical security findings block publication.
Medium security findings block when demonstrated exploitability and material
impact support them. Other findings are advisory. Blockers must cite source
evidence and a concrete failure or attack scenario. Every selected file requires
a coverage record, including files omitted by OCR's preview.

For opted-in repositories, the reviewer also performs a
[traceability assessment](traceability.md#review-assessment). Review JSON, native
transcripts, coverage, and OCR inputs remain in task artifacts. These checks
depend on model judgment and do not establish an absence of defects.

## Review a pull request

```sh
./scripts/factoryctl review example-app 123
```

The PR must be open and non-draft, with passing required CI. Accepted conclusions
default to `success`, `neutral`, and `skipped`; explicit `required_checks` names
detect missing jobs. [Path overrides](configuration.md) select the appropriate
required jobs for changes wholly inside a configured component. Standalone reviews rely on CI rather than rerunning the
application's tests. The reviewed source is the exact PR head archive.

Scheduled reviews additionally require an account/team review request or this
factory's outstanding changes request. Team lookup includes inherited membership
and needs organization membership read access. Submitted human reviews of the
current commit suppress automatic review; bot reviews, author self-comments,
pending/dismissed reviews, and older-commit reviews do not.

A follow-up after this factory requests changes needs a durable publication
receipt and the connected account's latest verdict to remain `CHANGES_REQUESTED`.
Approval or dismissal ends it. Author comments alone do not authorize another
review. Follow-ups reassess prior findings and distinguish verified, partial,
unresolved, and additional findings.

## Publication and retries

The parent rechecks review scope, head, base, and CI before publishing a report
and formal GitHub verdict: `PASS` submits **Approve**; blockers submit
**Request changes**. Advisory findings appear in the report. Incomplete or stale
reviews cannot approve a PR. Publication does not merge or deploy the change.

PR source archives and changed-file patches are fetched using the captured
commit SHAs. The changed-file inventory comes from the immutable base/head
comparison, so temporary changes to a PR cannot substitute another revision's
files. GitHub's [comparison API](https://docs.github.com/en/rest/commits/commits#compare-two-commits)
returns at most 300 files. If its inventory does not match the PR's changed-file
count, the review stops without publication; larger PRs need to be split.

Reports combine duplicate findings and link code to the reviewed commit.
The factory retains the original findings and any editing transcript, validates
their correspondence, and computes the verdict from the source findings.
Simple initial reports can render directly from validated fields; other reports
use a read-only editing pass.

Completed reviews have receipts bound to the repository, PR, head, and base.
New commits or retargeted PRs can receive a new review. Failed automatic attempts
wait for an explicit retry instead of running on every poll. `PUBLICATION_FAILED`
can be retried with `factoryctl review`, reusing a saved report only when its
source and evidence still validate. Submitted factory reviews are checked before
retrying a POST to avoid duplicates after a lost response.

Saved results from before immutable comparisons were introduced (review protocol
2 or earlier) require a fresh review. Current results also validate their
captured base and head before reuse.

A rejected Codex login produces `NEEDS_INPUT`. Run `factoryctl codex-login`, then
reply `resume: retry` or run `factoryctl review` again. A typed startup timeout
gets one retry before agent work begins; other startup failures require an
explicit retry. Bounded, redacted diagnostics remain in the run artifacts.

## Optional security audits

For a separate in-depth audit, explicitly ask Canvas to use the pinned
[Cloudflare security-audit skill](https://github.com/cloudflare/security-audit-skill):

> Use the Cloudflare security-audit skill to audit authentication in
> /projects/repos/example-app. Save the reports to
> /projects/requests/security-audits/example-auth.

The coordinator uses the existing subscription, keeps source read-only, and saves
upstream reports and a coverage ledger externally. This skill is not part of
automatic PR review and does not publish GitHub verdicts. Target-code execution
requires the skill's sandbox controls; otherwise relevant candidates remain
`needs_validation`. Report unresolved checks as limitations.
