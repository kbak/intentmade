"""One human-facing report, with every finding traced to the original specialists."""

import hashlib
import json
from typing import Annotated
from urllib.parse import quote

from agent import converse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Prose = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1400)]


class ReportFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(min_length=1)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    description: Prose
    evidence: Prose
    fix: Prose


class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[ReportFinding]
    coverage: list[Prose] = Field(max_length=6)
    changes_since_previous_review: list[Prose] = Field(default_factory=list, max_length=12)
    source_digest: str | None = None


def sources(review):
    return {
        f"{index}:{kind}:{number}": (finding, kind == "blocking")
        for index, specialist in enumerate(review.reviews)
        for kind, findings in (
            ("blocking", specialist.blocking_findings),
            ("advisory", specialist.non_blocking_findings),
        )
        for number, finding in enumerate(findings)
    }


def digest(review):
    return hashlib.sha256(
        json.dumps(
            [
                item.model_dump(
                    exclude={"traceability_assessment"}
                    if item.traceability_assessment is None
                    else set()
                )
                for item in review.reviews
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def traceability_items(review):
    return [
        (assessment.project, change)
        for specialist in review.reviews
        for assessment in specialist.traceability_assessment or []
        for change in assessment.changes
    ]


def validate_report(review, report):
    report = ReviewReport.model_validate(report)
    seen = [source for finding in report.findings for source in finding.source_ids]
    if len(seen) != len(set(seen)) or set(seen) != set(sources(review)):
        raise ValueError("Consolidated report must cover every source finding exactly once")
    if report.source_digest is not None and report.source_digest != digest(review):
        raise ValueError("Consolidated report belongs to different specialist evidence")
    return report.model_copy(update={"source_digest": digest(review)})


def draft_report(review):
    """Lossless fallback for incomplete/local reports; publication requires consolidation."""
    groups = {}
    for source, (finding, _) in sources(review).items():
        key = finding.model_dump_json()
        if key in groups:
            groups[key].source_ids.append(source)
        else:
            groups[key] = ReportFinding.model_construct(
                source_ids=[source],
                title=finding.title,
                description=" ".join(value for value in (finding.scenario, finding.impact) if value)
                or finding.evidence,
                evidence=finding.evidence,
                fix=finding.remediation,
            )
    return ReviewReport(findings=list(groups.values()), coverage=[])


def consolidate(workspace, review, transcript=None):
    payload = {
        "findings": {
            source: {"blocking": blocking, **finding.model_dump()}
            for source, (finding, blocking) in sources(review).items()
        },
        "coverage": [item.summary for item in review.reviews],
    }
    report = converse(
        workspace,
        "Apply the factory-review-report skill to these completed reviews:\n" + json.dumps(payload),
        title="Consolidate code and security review",
        skill="factory-review-report",
        response_model=ReviewReport,
        transcript=transcript,
    )
    return validate_report(review, report)


def ordered_findings(review, report):
    original = sources(review)
    ranks = {
        name: rank
        for rank, name in enumerate(("critical", "high", "medium", "low", "informational"))
    }
    rows = []
    for group in report.findings:
        members = [original[source] for source in group.source_ids]
        blocking = any(value for _, value in members)
        primary, _ = min(members, key=lambda row: (not row[1], ranks[row[0].severity]))
        severity = min((finding.severity for finding, _ in members), key=ranks.get)
        rows.append((group, primary, blocking, severity))
    return sorted(rows, key=lambda row: (not row[2], ranks[row[3]]))


def render(review, repository=None, sha=None):
    report = (
        validate_report(review, review.presentation)
        if review.presentation
        else draft_report(review)
    )
    rows = ordered_findings(review, report)
    blockers = sum(blocking for _, _, blocking, _ in rows)
    advisory = len(rows) - blockers
    gaps = sum(change.status == "missing" for _, change in traceability_items(review))
    if review.verdict == "BLOCKED":
        lead = "**Review incomplete** — a complete verdict is not available."
    elif blockers or gaps:
        count = blockers + gaps
        lead = f"**Changes requested** — {count} {'issue' if count == 1 else 'issues'} to address."
    elif advisory:
        lead = f"**Approved** — {advisory} optional {'improvement' if advisory == 1 else 'improvements'}."
    else:
        lead = "**Approved** — no blocking issues found."
    sections = ["## Code and security review", lead]
    if repository and sha:
        sections.append(f"Reviewed [{sha[:8]}](https://github.com/{repository}/commit/{sha}).")
    if report.changes_since_previous_review:
        sections.append(
            "### Since the previous review\n\n"
            + "\n".join("- " + item for item in report.changes_since_previous_review)
        )
    for number, (group, primary, blocking, severity) in enumerate(rows, 1):
        location = f"{primary.file}:{primary.line}"
        if repository and sha:
            location = f"[{location}](https://github.com/{repository}/blob/{sha}/{quote(primary.file, safe='/')}#L{primary.line})"
        else:
            location = f"`{location}`"
        sections.append(
            f"### {number}. {group.title}\n\n"
            f"**{'Blocking' if blocking else 'Suggestion'} · {severity.title()}** · {location}\n\n"
            f"{group.description}\n\n**Evidence:** {group.evidence}\n\n**Fix:** {group.fix}"
        )
    if report.coverage:
        sections.append(
            "<details>\n<summary>Validation and scope</summary>\n\n"
            + "\n".join("- " + item for item in report.coverage)
            + "\n\n</details>"
        )
    labels = {
        "covered": "Covered",
        "not_needed": "No additional tracing needed",
        "missing": "Missing traceability",
        "uncertain": "Uncertain",
    }
    for specialist in review.reviews:
        for assessment in specialist.traceability_assessment or []:
            details = [f"### Traceability — {assessment.project}", assessment.summary]
            for change in assessment.changes:
                references = [
                    f"{label}: {', '.join(values)}"
                    for label, values in (
                        ("Requirements", change.requirement_ids),
                        ("Documentation", change.documentation),
                        ("Implementation", change.implementation),
                        ("Verification", change.verification),
                    )
                    if values
                ]
                details.append(
                    f"**{labels[change.status]} — {change.behavior}**\n\n"
                    f"Changed paths: {', '.join(change.changed_paths)}\n\n{change.rationale}"
                    + ("\n\n" + "\n\n".join(references) if references else "")
                    + ("\n\nFix: " + change.remediation if change.remediation else "")
                )
            sections.append("\n\n".join(details))
    if review.infrastructure_errors:
        sections.append(
            "### Review could not finish\n\n"
            + "\n".join("- " + item for item in review.infrastructure_errors)
        )
    return "\n\n".join(sections)
