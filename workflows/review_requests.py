"""Requested and follow-up reviews, scoped by account and per-commit receipts."""

import hashlib
import json
import re
import urllib.error
from urllib.parse import quote

from common import DATA, github, identifier, issues, job_id


class ReviewRequests:
    def __init__(self, config, credential):
        self.config = config
        self.repo = config["repository"]
        self.credential = credential
        self.login = None
        self.memberships = {}

    def eligible(self, pr):
        return self.matches(pr) or self.follows_up(pr)

    def matches(self, pr):
        if pr.get("state") != "open" or pr.get("draft"):
            return False
        users, teams = pr.get("requested_reviewers", []), pr.get("requested_teams", [])
        if not users and not teams:
            return False
        if self.login is None:
            self.login = github(self.credential, "GET", "/user")["login"]
        if (pr.get("user") or {}).get("login", "").casefold() == self.login.casefold():
            return False
        if any(user["login"].casefold() == self.login.casefold() for user in users):
            return True
        org = self.repo.split("/")[0]
        for team in teams:
            slug = team["slug"]
            if slug not in self.memberships:
                path = "/orgs/{}/teams/{}/memberships/{}".format(
                    *(quote(value, safe="") for value in (org, slug, self.login))
                )
                try:
                    membership = github(self.credential, "GET", path)
                    self.memberships[slug] = membership["state"] == "active"
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    self.memberships[slug] = False
            if self.memberships[slug]:
                return True
        return False

    def follows_up(self, pr):
        """Continue our outstanding changes request on a new commit without a new ping."""
        if pr.get("state") != "open" or pr.get("draft"):
            return False
        prior = {}
        for path in receipt_path(self.config, pr).parent.glob("*.json"):
            record = json.loads(path.read_text())
            posted = record.get("github_review") or {}
            if (
                record.get("status") == "REVIEWED"
                and record.get("repository", "").casefold() == self.repo.casefold()
                and record.get("pr") == pr["number"]
                and record.get("head") != pr["head"]["sha"]
                and posted.get("state") == "CHANGES_REQUESTED"
                and posted.get("commit_id") == record.get("head")
                and posted.get("id")
            ):
                prior[posted["id"]] = posted["commit_id"]
        if not prior:
            return False
        if self.login is None:
            self.login = github(self.credential, "GET", "/user")["login"]
        if (pr.get("user") or {}).get("login", "").casefold() == self.login.casefold():
            return False
        # A local receipt proves this factory posted the review, but its live
        # state may now be dismissed or superseded by an approval/manual review.
        # Read again on each eligibility check, including immediately before work.
        latest = max(
            (
                review
                for review in issues._github_paginate(
                    self.credential, f"/repos/{self.repo}/pulls/{pr['number']}/reviews"
                )
                if (review.get("user") or {}).get("login", "").casefold() == self.login.casefold()
                and review.get("submitted_at")
                and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
            ),
            key=lambda review: (review["submitted_at"], review["id"]),
            default=None,
        )
        return bool(
            latest
            and latest["state"] == "CHANGES_REQUESTED"
            and latest["id"] in prior
            and latest["commit_id"] == prior[latest["id"]]
        )

    def human_reviewed(self, pr):
        return any(
            review.get("commit_id") == pr["head"]["sha"]
            and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
            and (review.get("user") or {}).get("type") == "User"
            and (review.get("user") or {}).get("login", "").casefold()
            != (pr.get("user") or {}).get("login", "").casefold()
            for review in issues._github_paginate(
                self.credential, f"/repos/{self.repo}/pulls/{pr['number']}/reviews"
            )
        )


def receipt_path(config, pr):
    sha = pr["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ValueError("Invalid PR commit SHA")
    return (
        DATA
        / "pr-reviews"
        / identifier(config["project"])
        / hashlib.sha256(config["repository"].casefold().encode()).hexdigest()
        / str(int(pr["number"]))
        / (sha + ".json")
    )


def attempted(config, pr):
    record = read(config, pr)
    return bool(record) and record["status"] != "STALE"


def snapshot(config, pr):
    return {
        "repository": config["repository"].casefold(),
        "pr": pr["number"],
        "head": pr["head"]["sha"],
    }


def retryable(config, pr, reply):
    if not reply:
        return False
    record = read(config, pr) or {}
    return (
        record.get("status") in {"FAILED", "PUBLICATION_FAILED"}
        and record.get("answer_id") != reply["id"]
        # Older standalone reports did not save a snapshot. Their per-head
        # failure receipt still has to exist; a reply cannot waive a new head.
        and reply.get("snapshot") in (None, snapshot(config, pr))
    )


def read(config, pr):
    path = receipt_path(config, pr)
    return json.loads(path.read_text()) if path.exists() else None


def remember(config, pr, status, **details):
    path = receipt_path(config, pr)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                **(read(config, pr) or {}),
                "repository": config["repository"],
                "pr": pr["number"],
                "head": pr["head"]["sha"],
                "status": status,
                "run_id": job_id(),
                **details,
            },
            indent=2,
        )
    )
    temporary.replace(path)
