"""Alibaba subscription review, with coverage and verdict computed by the factory."""

import json
import shutil
from typing import Annotated, Literal

from agent import AgentStartupError, converse
from ocr_review import prepare
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from review_report import (
    ReviewReport,
    consolidate,
    ordered_findings,
    render,
    traceability_items,
    validate_report,
)

ROLES = ("Alibaba Reviewer",)
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Verdict = Literal["PASS", "CHANGES_REQUESTED", "BLOCKED"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Text
    category: Literal["code", "security", "hardening", "style"]
    severity: Literal["critical", "high", "medium", "low", "informational"]
    file: Text
    line: int = Field(ge=1)
    evidence: Text
    scenario: str
    impact: str
    remediation: Text
    introduced_or_worsened: bool
    demonstrated_exploitability: bool
    material_impact: bool

    def blocks(self, proposed):
        if not self.introduced_or_worsened:
            return False
        if self.category in {"hardening", "style"}:
            return False
        if self.category == "security":
            if self.severity in {"low", "informational"}:
                return False
            return self.severity in {"high", "critical"} or (
                proposed and self.demonstrated_exploitability and self.material_impact
            )
        return proposed and self.material_impact

    def describe(self):
        return (
            f"{self.file}:{self.line} — {self.title} ({self.severity})\n"
            f"Evidence: {self.evidence}\nScenario: {self.scenario}\n"
            f"Impact: {self.impact}\nRemediation: {self.remediation}"
        )


class FileCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: Text
    path: Text
    status: Text
    outcome: Literal["reviewed", "unavailable"]
    evidence: Text


class SpecialistReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    summary: Text
    blocking_findings: list[Finding]
    non_blocking_findings: list[Finding]
    infrastructure_error: str | None
    coverage: list[FileCoverage] = Field(default_factory=list)


class TraceabilityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_paths: list[Text] = Field(min_length=1)
    behavior: Text
    status: Literal["covered", "not_needed", "missing", "uncertain"]
    requirement_ids: list[Text] = Field(default_factory=list)
    documentation: list[Text] = Field(default_factory=list)
    implementation: list[Text] = Field(default_factory=list)
    verification: list[Text] = Field(default_factory=list)
    rationale: Text
    remediation: Text | None = None


class TraceabilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: Text
    summary: Text
    changes: list[TraceabilityChange]


class TraceableSpecialistReview(SpecialistReview):
    traceability_assessment: list[TraceabilityAssessment] = Field(min_length=1)


class RoleReview(SpecialistReview):
    role: str
    thread_id: str
    traceability_assessment: list[TraceabilityAssessment] | None = None


class ReviewResult(BaseModel):
    review_protocol: Literal[3] = 3
    verdict: Verdict
    summary: str
    reviews: list[RoleReview] = Field(default_factory=list)
    infrastructure_errors: list[str] = Field(default_factory=list)
    presentation: ReviewReport | None = None
    traceability_context: dict[str, dict] = Field(default_factory=dict)
    review_inputs: dict[str, dict] = Field(default_factory=dict)
    startup_failure: dict | None = None

    def report(self, repository=None, sha=None):
        return render(self, repository, sha)

    def repair_instructions(self):
        if self.presentation:
            report = validate_report(self, self.presentation)
            ordinary = "\n\n".join(
                f"{primary.file}:{primary.line} — {group.title}\n{group.description}\n"
                f"Evidence: {group.evidence}\nFix: {group.fix}"
                for group, primary, blocking, _ in ordered_findings(self, report)
                if blocking
            )
        else:
            # Only blockers go back to the builder, including when tests failed.
            findings = dict.fromkeys(
                f.describe() for review in self.reviews for f in review.blocking_findings
            )
            ordinary = "\n\n".join(findings)
        gaps = [
            f"Traceability — {project}: {', '.join(change.changed_paths)}\n"
            f"Behavior: {change.behavior}\nGap: {change.rationale}\nFix: {change.remediation}"
            for project, change in traceability_items(self)
            if change.status == "missing"
        ]
        return "\n\n".join(filter(None, [ordinary, *gaps])) or "No blocking review findings."


def coordinator_prompt(context, traceability=None):
    schema = (
        "Alibaba Reviewer final-response JSON schema (includes the required traceability assessment):\n"
        + json.dumps(TraceableSpecialistReview.model_json_schema())
        if traceability
        else "Specialist final-response JSON schema (no Markdown fences):\n"
        + json.dumps(SpecialistReview.model_json_schema())
    )
    return (
        "FACTORY_SPECIALIST_REVIEW_V2\n"
        "Apply the factory-review skill to this change.\n"
        + schema
        + "\n\nReview context:\n"
        + context
    )


# [impl->req~im-trace-assessment~1]
def validate_assessments(assessments, expected):
    """Check required accounting and outcomes; relevance remains the reviewer's judgment."""
    projects = [item.project for item in assessments]
    if len(set(projects)) != len(projects) or set(projects) != set(expected):
        raise ValueError("Traceability assessment must cover exactly the opted-in repositories")
    for assessment in assessments:
        paths = set(expected[assessment.project]["changed_paths"])
        accounted = set()
        for change in assessment.changes:
            if not set(change.changed_paths) <= paths:
                raise ValueError(
                    f"{assessment.project}: assessment includes paths outside the scoped change"
                )
            accounted.update(change.changed_paths)
            for reference in change.requirement_ids:
                label, identifier = (
                    reference.split(":", 1) if ":" in reference else ("candidate", reference)
                )
                indexed = expected[assessment.project].get("requirement_index", {}).get(label)
                if not isinstance(indexed, dict) or not indexed or "error" in indexed:
                    detail = (
                        indexed.get("error", "snapshot not available")
                        if isinstance(indexed, dict)
                        else "snapshot not available"
                    )
                    raise ValueError(
                        f"{assessment.project}: cannot resolve {reference}: {detail}; a source index is required"
                    )
                if not isinstance(indexed.get("ids"), list) or any(
                    not isinstance(item, str) for item in indexed["ids"]
                ):
                    raise ValueError(
                        f"{assessment.project}: malformed requirement ID index; a fresh review is required"
                    )
                if identifier not in indexed["ids"]:
                    raise ValueError(
                        f"{assessment.project}: unknown requirement ID or revision in {label}: {identifier}"
                    )
            if change.status == "covered" and not all(
                (
                    change.requirement_ids,
                    change.documentation,
                    change.implementation,
                    change.verification,
                )
            ):
                raise ValueError(
                    f"{assessment.project}: covered behavior needs requirement, documentation, implementation and verification references"
                )
            if change.status == "missing" and not change.remediation:
                raise ValueError(
                    f"{assessment.project}: missing traceability needs a concrete repair"
                )
        if accounted != paths:
            raise ValueError(f"{assessment.project}: traceability assessment omits changed paths")


# [impl->req~im-review-evidence~1]
def validate_coverage(coverage, expected):
    required = {
        (project, item["path"], item["status"])
        for project, spec in expected.items()
        for item in spec["files"]
    }
    actual = [(item.project, item.path, item.status) for item in coverage]
    if len(actual) != len(set(actual)) or set(actual) != required:
        raise ValueError("Review coverage must account for every selected file exactly once")
    if any(item.outcome != "reviewed" for item in coverage):
        raise ValueError("Required source or patch evidence remains unavailable")


# [impl->req~im-review-evidence~1]
# [impl->req~im-trace-assessment~1]
def evaluate(events, traceability=None, review_inputs=None):
    # ACPToolCallEvent comes from the adapter, not assistant prose or a shell's output.
    evidence = [
        event
        for event in events
        if event.get("kind") == "ACPToolCallEvent"
        and event.get("title") == "Factory specialist review"
        and isinstance(event.get("raw_input"), dict)
        and event["raw_input"].get("version") == 2
    ]
    errors, results = [], []
    if not evidence:
        errors.append("Native specialist execution evidence is missing.")
    else:
        event = evidence[-1]
        payload = event.get("raw_output") or {}
        if event.get("status") != "completed" or not isinstance(payload, dict):
            errors.append("Could not collect native specialist execution evidence.")
        elif not isinstance(payload.get("agents"), list):
            errors.append("Native specialist results are missing.")
        else:
            agents = payload["agents"]
            root = event["raw_input"].get("threadId")
            if len(agents) != len(ROLES):
                errors.append("Expected exactly one native Alibaba review subagent.")
            seen = set()
            for role in ROLES:
                matches = [a for a in agents if isinstance(a, dict) and a.get("role") == role]
                if len(matches) != 1:
                    errors.append(f"{role}: expected one native execution of this role.")
                    continue
                agent = matches[0]
                identity = agent.get("thread_id")
                if (
                    not identity
                    or identity in seen
                    or not root
                    or agent.get("parent_thread_id") != root
                ):
                    errors.append(f"{role}: invalid native parent/child identity.")
                    continue
                seen.add(identity)
                if agent.get("status") != "completed":
                    errors.append(f"{role}: native review did not complete.")
                    continue
                try:
                    schema = TraceableSpecialistReview if traceability else SpecialistReview
                    review = schema.model_validate_json(agent.get("message") or "")
                    validate_coverage(review.coverage, review_inputs or {})
                    if isinstance(review, TraceableSpecialistReview):
                        validate_assessments(review.traceability_assessment, traceability)
                except (ValidationError, TypeError):
                    required = " with the required traceability assessment" if traceability else ""
                    errors.append(f"{role}: final response is not valid specialist JSON{required}.")
                    continue
                except ValueError as exc:
                    errors.append(f"{role}: {exc}")
                    continue
                blocking, advisory = [], []
                for proposed, findings in (
                    (True, review.blocking_findings),
                    (False, review.non_blocking_findings),
                ):
                    for finding in findings:
                        if finding.blocks(proposed):
                            if not finding.scenario.strip() or not finding.impact.strip():
                                errors.append(
                                    f"{role}: a blocker is missing its scenario or impact."
                                )
                            blocking.append(finding)
                        else:
                            advisory.append(finding)
                assessments = getattr(review, "traceability_assessment", None) or []
                uncertain = [
                    a.project
                    for a in assessments
                    if any(c.status == "uncertain" for c in a.changes)
                ]
                gaps = any(c.status == "missing" for a in assessments for c in a.changes)
                if uncertain:
                    errors.append(
                        f"{role}: required traceability remains uncertain for {', '.join(uncertain)}"
                    )
                if review.verdict == "BLOCKED" or review.infrastructure_error or uncertain:
                    errors.append(f"{role}: {review.infrastructure_error or 'review incomplete'}")
                    verdict = "BLOCKED"
                else:
                    verdict = "CHANGES_REQUESTED" if blocking or gaps else "PASS"
                results.append(
                    RoleReview(
                        **review.model_dump(
                            exclude={"verdict", "blocking_findings", "non_blocking_findings"}
                        ),
                        role=role,
                        thread_id=identity,
                        verdict=verdict,
                        blocking_findings=blocking,
                        non_blocking_findings=advisory,
                    )
                )
    blockers = sum(len(review.blocking_findings) for review in results)
    gaps = sum(
        change.status == "missing"
        for review in results
        for assessment in review.traceability_assessment or []
        for change in assessment.changes
    )
    advisory = sum(len(review.non_blocking_findings) for review in results)
    verdict = "BLOCKED" if errors else "CHANGES_REQUESTED" if blockers or gaps else "PASS"
    summary = f"{blockers} blocking finding(s); {advisory} non-blocking finding(s)."
    if traceability:
        summary += f" {gaps} traceability gap(s)."
    if errors:
        summary += " " + " ".join(errors)
    return ReviewResult(
        verdict=verdict,
        summary=summary,
        reviews=results,
        infrastructure_errors=errors,
        traceability_context={
            project: {
                key: value[key]
                for key in ("scope", "changed_paths", "requirement_index")
                if key in value
            }
            for project, value in (traceability or {}).items()
        },
        review_inputs=review_inputs or {},
    )


def review_code(
    workspace,
    context,
    *,
    title="Independent review",
    transcript=None,
    traceability=None,
    sources=None,
    input_path=None,
    initial_review=False,
):
    events = []
    failure = None
    startup_failure = None
    review_inputs = {}
    try:
        review_inputs = prepare(workspace, sources, input_path)
        if transcript:
            shutil.copyfile(input_path, transcript.with_name(transcript.stem + "-ocr-input.json"))
        context += f"\n\nController-prepared OCR delegation input: {input_path}. Read this file before reviewing."
        converse(
            workspace,
            coordinator_prompt(context, traceability),
            title=title,
            transcript=transcript,
            event_log=events,
            skill="factory-review",
        )
    except Exception as exc:
        failure = f"Review coordinator failed: {type(exc).__name__}: {exc}"
        if isinstance(exc, AgentStartupError):
            startup_failure = exc.details
    result = evaluate(events, traceability, review_inputs)
    result.startup_failure = startup_failure
    if failure:
        result.verdict = "BLOCKED"
        result.infrastructure_errors.append(failure)
        result.summary += " " + failure
    if result.verdict != "BLOCKED":
        try:
            result.presentation = consolidate(
                workspace,
                result,
                transcript=transcript.with_name(transcript.stem + "-report.jsonl")
                if transcript
                else None,
                initial_review=initial_review,
            )
        except Exception as exc:
            message = f"Report consolidation failed: {type(exc).__name__}: {exc}"
            if isinstance(exc, AgentStartupError):
                result.startup_failure = exc.details
            result.verdict = "BLOCKED"
            result.infrastructure_errors.append(message)
            result.summary += " " + message
    return result
