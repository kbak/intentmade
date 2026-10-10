"""Publish completed specialist reports and formal verdicts from the parent only."""

import hashlib
import json
from pathlib import Path

import review_requests
from common import github, issues, lock
from lifecycle import PublicationError  # noqa: F401 (public import for retained workflows)
from policy import pr_eligible
from review import ROLES, ReviewContractError, ReviewEvidence, ReviewResult, validate_assessments
from review_report import ReviewReport, traceability_items, validate_report


def current_protocol(artifact):
    artifact = Path(artifact)
    if json.loads((artifact / "review.json").read_text()).get("review_protocol") != 3:
        return False
    try:
        review_requests.base(json.loads((artifact / "result.json").read_text()))
    except (OSError, ValueError, TypeError):
        return False
    return True


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
    code = next((item for item in review.reviews if item.role == ROLES[0]), None)
    try:
        validate_assessments(code.traceability_assessment or [] if code else [], expected)
    except ValueError as exc:
        raise PublicationError(str(exc)) from exc
    if any(change.status == "uncertain" for _, change in traceability_items(review)):
        raise PublicationError("Required traceability assessment remains uncertain")


# [impl->req~im-pr-publication~1]
# [impl->req~im-review-retry~1]
def publish(config, pr, review, credential):
    validate_traceability(config, review)
    try:
        decision = review.validate_completed()
    except ReviewContractError as exc:
        raise PublicationError(str(exc)) from exc
    blocked = decision.verdict == "CHANGES_REQUESTED"
    validate_comparison(config, pr, review)
    repo, number, sha = config["repository"], pr["number"], pr["head"]["sha"]
    event = "REQUEST_CHANGES" if blocked else "APPROVE"
    expected = "CHANGES_REQUESTED" if blocked else "APPROVED"
    marker = f"<!-- factory-review:v3:{repo}:{number}:{review_requests.revision(pr)} -->"
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
        fresh = github(credential, "GET", f"/repos/{repo}/pulls/{number}")
        if review_requests.snapshot(config, fresh) != review_requests.snapshot(config, pr):
            raise PublicationError("PR changed its head or base; a fresh review is required")
        if posted:
            if posted["state"] != expected:
                raise PublicationError(
                    "Existing factory review was dismissed or has a different verdict"
                )
            return {key: posted[key] for key in ("id", "html_url", "state", "commit_id")}
        if not pr_eligible(credential, fresh, config):
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


# [impl->req~im-pr-publication~1]
# [impl->req~im-review-retry~1]
def load_saved(config, pr, artifact):
    artifact = Path(artifact)
    if not current_protocol(artifact):
        raise PublicationError(
            "Review protocol or base binding missing; a fresh Alibaba review is required"
        )
    saved = json.loads((artifact / "result.json").read_text())
    if any(
        saved.get(key) != value
        for key, value in {
            "repository": config["repository"],
            "pr": pr["number"],
            "head": pr["head"]["sha"],
            "base": review_requests.base(pr),
            "status": "REVIEWED",
        }.items()
    ):
        raise PublicationError("Saved review does not match this repository, PR, head and base")
    stored = ReviewResult.model_validate_json((artifact / "review.json").read_text())
    validate_traceability(config, stored)
    validate_comparison(config, pr, stored)
    try:
        review = ReviewEvidence.load(artifact, stored).evaluate()
    except (ValueError, TypeError, KeyError) as exc:
        raise PublicationError(
            "Saved report does not match native specialist execution evidence"
        ) from exc
    if review.model_dump(exclude={"presentation"}) != stored.model_dump(exclude={"presentation"}):
        raise PublicationError("Saved report does not match native specialist execution evidence")
    legacy_presentation = artifact / "review-presentation.json"
    if stored.presentation is None and legacy_presentation.exists():
        stored.presentation = ReviewReport.model_validate_json(legacy_presentation.read_text())
    if stored.presentation:
        validate_report(review, stored.presentation)
    return stored


def validate_comparison(config, pr, review):
    expected = review.review_inputs
    if (
        review.review_protocol != 3
        or set(expected) != {config["project"]}
        or expected[config["project"]].get("base") != review_requests.base(pr)["sha"]
        or expected[config["project"]].get("candidate") != pr["head"]["sha"]
    ):
        raise PublicationError(
            "Review inputs are not bound to this immutable comparison; a fresh review is required"
        )
