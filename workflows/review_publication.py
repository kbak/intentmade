"""Publish completed specialist reports and formal verdicts from the parent only."""

import hashlib
import json
from pathlib import Path

from common import github, issues, lock
from policy import pr_eligible
from review import ROLES, ReviewResult, evaluate, validate_assessments
from review_report import ReviewReport, traceability_items, validate_report


class PublicationError(RuntimeError):
    pass


def validate_traceability(config, review):
    from traceability import scope_for

    scope = scope_for(config)
    expected = review.traceability_context
    if scope is None:
        if expected:
            raise PublicationError("Traceability configuration changed; a fresh review is required")
        return
    project = config["project"]
    if set(expected) != {project} or expected[project].get("scope") != scope:
        raise PublicationError(
            "Required traceability scope is missing or changed; a fresh review is required"
        )
    code = next((item for item in review.reviews if item.role == "Code Reviewer"), None)
    try:
        validate_assessments(code.traceability_assessment or [] if code else [], expected)
    except ValueError as exc:
        raise PublicationError(str(exc)) from exc
    if any(change.status == "uncertain" for _, change in traceability_items(review)):
        raise PublicationError("Required traceability assessment remains uncertain")


def publish(config, pr, review, credential):
    validate_traceability(config, review)
    if (
        len(review.reviews) != len(ROLES)
        or {item.role for item in review.reviews} != set(ROLES)
        or review.infrastructure_errors
        or any(item.infrastructure_error or item.verdict == "BLOCKED" for item in review.reviews)
        or review.verdict == "BLOCKED"
    ):
        raise PublicationError("Both code and security reviews must complete before publication")
    if review.presentation is None:
        raise PublicationError("A consolidated report is required before publication")
    validate_report(review, review.presentation)
    blocked = any(item.blocking_findings for item in review.reviews) or any(
        change.status == "missing" for _, change in traceability_items(review)
    )
    if review.verdict != ("CHANGES_REQUESTED" if blocked else "PASS"):
        raise PublicationError(
            "Review verdict does not match its blocking findings or traceability obligations"
        )
    repo, number, sha = config["repository"], pr["number"], pr["head"]["sha"]
    event = "REQUEST_CHANGES" if blocked else "APPROVE"
    expected = "CHANGES_REQUESTED" if blocked else "APPROVED"
    marker = f"<!-- factory-review:v1:{repo}:{number}:{sha} -->"
    body = review.report(repo, sha) + "\n\n" + marker
    # All publication paths share this lock, including recovery after an
    # ambiguous GitHub response. Never retry a POST before looking for its result.
    with lock("review-publish-" + hashlib.sha256(repo.casefold().encode()).hexdigest()):
        login = github(credential, "GET", "/user")["login"]
        posted = next(
            (
                item
                for item in issues._github_paginate(
                    credential, f"/repos/{repo}/pulls/{number}/reviews"
                )
                if (item.get("user") or {}).get("login", "").casefold() == login.casefold()
                and item.get("commit_id") == sha
                and marker in (item.get("body") or "")
                and item.get("submitted_at")
            ),
            None,
        )
        if posted:
            if posted["state"] != expected:
                raise PublicationError(
                    "Existing factory review was dismissed or has a different verdict"
                )
            return {key: posted[key] for key in ("id", "html_url", "state", "commit_id")}
        fresh = github(credential, "GET", f"/repos/{repo}/pulls/{number}")
        if fresh["head"]["sha"] != sha or not pr_eligible(credential, fresh, config):
            raise PublicationError(
                "PR changed or no longer satisfies review CI policy; nothing posted"
            )
        posted = github(
            credential,
            "POST",
            f"/repos/{repo}/pulls/{number}/reviews",
            body={"commit_id": sha, "event": event, "body": body},
        )
        if posted.get("state") != expected or posted.get("commit_id") != sha:
            raise PublicationError("GitHub did not confirm the submitted review verdict")
        return {key: posted[key] for key in ("id", "html_url", "state", "commit_id")}


def load_saved(config, pr, artifact):
    artifact = Path(artifact)
    saved = json.loads((artifact / "result.json").read_text())
    if any(
        saved.get(key) != value
        for key, value in {
            "repository": config["repository"],
            "pr": pr["number"],
            "head": pr["head"]["sha"],
            "status": "REVIEWED",
        }.items()
    ):
        raise PublicationError("Saved review does not match this repository, PR and commit")
    stored = ReviewResult.model_validate_json((artifact / "review.json").read_text())
    validate_traceability(config, stored)
    review = evaluate(
        [json.loads(line) for line in (artifact / "review.jsonl").read_text().splitlines()],
        stored.traceability_context,
    )
    if review.model_dump(exclude={"presentation"}) != stored.model_dump(exclude={"presentation"}):
        raise PublicationError("Saved report does not match native specialist execution evidence")
    legacy_presentation = artifact / "review-presentation.json"
    if stored.presentation is None and legacy_presentation.exists():
        stored.presentation = ReviewReport.model_validate_json(legacy_presentation.read_text())
    if stored.presentation:
        validate_report(review, stored.presentation)
    return stored
