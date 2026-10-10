"""Requested and follow-up reviews, scoped by account and PR comparison."""

import hashlib
import json
import re
import urllib.error
from urllib.parse import quote

from common import DATA, github, identifier, issues, job_id
from openhands.sdk.utils.files import atomic_write_text


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
        """Continue our changes request on a changed comparison without a new ping."""
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
                and (record.get("head") != pr["head"]["sha"] or record.get("base") != base(pr))
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
            and "<!-- factory-review:" not in (review.get("body") or "")
            and (review.get("user") or {}).get("type") == "User"
            and (review.get("user") or {}).get("login", "").casefold()
            != (pr.get("user") or {}).get("login", "").casefold()
            for review in issues._github_paginate(
                self.credential, f"/repos/{self.repo}/pulls/{pr['number']}/reviews"
            )
        )


def base(pr):
    value = pr.get("base") or {}
    if (
        not isinstance(value.get("ref"), str)
        or not value["ref"]
        or not re.fullmatch(r"[0-9a-f]{40,64}", value.get("sha", ""))
    ):
        raise ValueError("Review requires a pinned PR base branch and commit")
    return {"ref": value["ref"], "sha": value["sha"]}


def revision(pr):
    sha = pr["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ValueError("Invalid PR commit SHA")
    return sha + "-" + hashlib.sha256(json.dumps(base(pr), sort_keys=True).encode()).hexdigest()


# [impl->req~im-immutable-pr-comparison~1]
def comparison_files(config, pr, credential):
    """Read the captured comparison, never the PR's mutable current diff."""
    revision(pr)  # Validate both immutable object names before building the URL.
    before, after = base(pr)["sha"], pr["head"]["sha"]
    comparison = github(
        credential, "GET", f"/repos/{config['repository']}/compare/{before}...{after}"
    )
    if comparison.get("base_commit", {}).get("sha") != before:
        raise ValueError("GitHub comparison does not match the captured base")
    files = comparison.get("files")
    if not isinstance(files, list) or len(files) != pr["changed_files"]:
        # GitHub limits the compare response's file inventory. Never fill gaps
        # with the mutable PR-files endpoint or approve a truncated comparison.
        raise ValueError(
            "GitHub's immutable changed-file inventory is incomplete; no review can be published"
        )
    return files


def receipt_path(config, pr):
    return (
        DATA
        / "pr-reviews"
        / identifier(config["project"])
        / hashlib.sha256(config["repository"].casefold().encode()).hexdigest()
        / str(int(pr["number"]))
        / (revision(pr) + ".json")
    )


def attempted(config, pr):
    record = read(config, pr)
    return bool(record) and record["status"] != "STALE"


def snapshot(config, pr):
    return {
        "repository": config["repository"].casefold(),
        "pr": pr["number"],
        "head": pr["head"]["sha"],
        "base": base(pr),
    }


def retryable(config, pr, reply):
    if not reply:
        return False
    record = read(config, pr) or {}
    return (
        record.get("status") in {"FAILED", "PUBLICATION_FAILED", "NEEDS_INPUT"}
        and record.get("answer_id") != reply["id"]
        # Older standalone reports did not save a snapshot. A failure receipt
        # for this exact comparison must still exist to authorize a retry.
        and reply.get("snapshot") in (None, snapshot(config, pr))
    )


def read(config, pr):
    path = receipt_path(config, pr)
    return json.loads(path.read_text()) if path.exists() else None


def remember(config, pr, status, **details):
    path = receipt_path(config, pr)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            {
                **(read(config, pr) or {}),
                "repository": config["repository"],
                "pr": pr["number"],
                "head": pr["head"]["sha"],
                "base": base(pr),
                "status": status,
                "run_id": job_id(),
                **details,
            },
            indent=2,
        ),
        mode=0o644,
    )
