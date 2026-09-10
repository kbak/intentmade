"""Parallel native specialists, with a blocking-only verdict computed by the factory."""

import json
from typing import Annotated, Literal

from agent import converse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from review_report import ReviewReport, consolidate, ordered_findings, render, validate_report

ROLES = ("Code Reviewer", "Application Security Engineer")
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


class SpecialistReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    summary: Text
    blocking_findings: list[Finding]
    non_blocking_findings: list[Finding]
    infrastructure_error: str | None


class RoleReview(SpecialistReview):
    role: str
    thread_id: str


class ReviewResult(BaseModel):
    verdict: Verdict
    summary: str
    reviews: list[RoleReview] = Field(default_factory=list)
    infrastructure_errors: list[str] = Field(default_factory=list)
    presentation: ReviewReport | None = None

    def report(self, repository=None, sha=None):
        return render(self, repository, sha)

    def repair_instructions(self):
        if self.presentation:
            report = validate_report(self, self.presentation)
            return (
                "\n\n".join(
                    f"{primary.file}:{primary.line} — {group.title}\n{group.description}\n"
                    f"Evidence: {group.evidence}\nFix: {group.fix}"
                    for group, primary, blocking, _ in ordered_findings(self, report)
                    if blocking
                )
                or "No blocking review findings."
            )
        # Only blockers go back to the builder, including when tests also failed.
        # Keep each original report in the artifacts; collapse exact duplicates here.
        findings = dict.fromkeys(
            f.describe() for review in self.reviews for f in review.blocking_findings
        )
        return "\n\n".join(findings) or "No blocking review findings."


def coordinator_prompt(context):
    return (
        "FACTORY_SPECIALIST_REVIEW_V1\n"
        "Apply the factory-review skill to this change.\n"
        "Specialist final-response JSON schema (no Markdown fences):\n"
        + json.dumps(SpecialistReview.model_json_schema())
        + "\n\nReview context:\n"
        + context
    )


def evaluate(events):
    # ACPToolCallEvent comes from the adapter, not assistant prose or a shell's output.
    evidence = [
        event
        for event in events
        if event.get("kind") == "ACPToolCallEvent"
        and event.get("title") == "Factory specialist review"
        and isinstance(event.get("raw_input"), dict)
        and event["raw_input"].get("version") == 1
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
            if len(agents) != 2:
                errors.append("Expected exactly two native review subagents.")
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
                    review = SpecialistReview.model_validate_json(agent.get("message") or "")
                except (ValidationError, TypeError):
                    errors.append(f"{role}: final response is not valid specialist JSON.")
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
                if review.verdict == "BLOCKED" or review.infrastructure_error:
                    errors.append(f"{role}: {review.infrastructure_error or 'review incomplete'}")
                    verdict = "BLOCKED"
                else:
                    verdict = "CHANGES_REQUESTED" if blocking else "PASS"
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
    advisory = sum(len(review.non_blocking_findings) for review in results)
    verdict = "BLOCKED" if errors else "CHANGES_REQUESTED" if blockers else "PASS"
    summary = f"{blockers} blocking finding(s); {advisory} non-blocking finding(s)."
    if errors:
        summary += " " + " ".join(errors)
    return ReviewResult(
        verdict=verdict, summary=summary, reviews=results, infrastructure_errors=errors
    )


def review_code(workspace, context, *, title="Independent review", transcript=None):
    events = []
    failure = None
    try:
        converse(
            workspace,
            coordinator_prompt(context),
            title=title,
            transcript=transcript,
            event_log=events,
            skill="factory-review",
        )
    except Exception as exc:
        failure = f"Review coordinator failed: {type(exc).__name__}: {exc}"
    result = evaluate(events)
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
            )
        except Exception as exc:
            message = f"Report consolidation failed: {type(exc).__name__}: {exc}"
            result.verdict = "BLOCKED"
            result.infrastructure_errors.append(message)
            result.summary += " " + message
    return result
