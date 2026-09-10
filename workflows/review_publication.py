"""Publish completed specialist reports and formal verdicts from the parent only."""

import hashlib
import json
from pathlib import Path

from common import github, issues, lock
from policy import pr_eligible
from review import ROLES, evaluate


class PublicationError(RuntimeError):
    pass


def publish(config, pr, review, credential):
    if (
        len(review.reviews) != len(ROLES)
        or {item.role for item in review.reviews} != set(ROLES)
        or review.infrastructure_errors
        or any(item.infrastructure_error or item.verdict == "BLOCKED" for item in review.reviews)
        or review.verdict == "BLOCKED"
    ):
        raise PublicationError("Both code and security reviews must complete before publication")
    blocked = any(item.blocking_findings for item in review.reviews)
    if review.verdict != ("CHANGES_REQUESTED" if blocked else "PASS"):
        raise PublicationError("Review verdict does not match its blocking findings")
    repo, number, sha = config["repository"], pr["number"], pr["head"]["sha"]
    event = "REQUEST_CHANGES" if blocked else "APPROVE"
    expected = "CHANGES_REQUESTED" if blocked else "APPROVED"
    marker = f"<!-- factory-review:v1:{repo}:{number}:{sha} -->"
    body = (
        f"## Automated code and security review\n\n**Verdict: {event}**\n\n"
        f"Reviewed commit `{sha}`. Both Code Reviewer and Application Security Engineer "
        f"completed independently.\n\n{review.report()}\n\n{marker}"
    )
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
    review = evaluate(
        [json.loads(line) for line in (artifact / "review.jsonl").read_text().splitlines()]
    )
    if review.model_dump() != json.loads((artifact / "review.json").read_text()):
        raise PublicationError("Saved report does not match native specialist execution evidence")
    return review
