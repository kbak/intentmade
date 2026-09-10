"""Requested PR reviews: account/team scope and durable per-commit receipts."""

import hashlib
import json
import re
import urllib.error
from urllib.parse import quote

from common import DATA, github, identifier, issues, job_id


class ReviewRequests:
    def __init__(self, config, credential):
        self.repo = config["repository"]
        self.credential = credential
        self.login = None
        self.memberships = {}

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
