"""Deterministic selection policy; no agent decides eligibility or ownership."""

import hashlib
import json
from fnmatch import fnmatchcase

from common import github, issues


def issue_eligible(issue, config):
    return (
        issue.get("state") == "open"
        and not issue.get("pull_request")
        and not issue.get("assignees")
        and (not config.get("issue_label") or config["issue_label"] in issues._labels(issue))
    )


def issue_snapshot(config, issue):
    """Identify the specification independently of comments, labels and assignment."""
    content = {"title": issue["title"], "body": issue.get("body") or ""}
    if not all(isinstance(value, str) for value in content.values()):
        raise RuntimeError("Issue specification is unavailable")
    snapshot = {
        "repository": config["repository"],
        "issue": issue["number"],
        "content_sha256": hashlib.sha256(
            json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    return content, snapshot


def checks_pass(checks, config):
    if not checks:
        return False
    allowed = set(config["accepted_check_results"])
    # Every reported check must finish successfully, and each required name or
    # pattern must occur. No checks, missing checks and pending checks fail closed.
    return all(value in allowed for value in checks.values()) and all(
        any(fnmatchcase(name, pattern) for name in checks) for pattern in config["required_checks"]
    )


def latest_check_runs(token, repo, sha):
    checks = {}
    page = 1
    while True:
        data = github(
            token,
            "GET",
            f"/repos/{repo}/commits/{sha}/check-runs",
            params={"per_page": 100, "page": page, "filter": "latest"},
        )
        for check in data["check_runs"]:
            provider = (check.get("app") or {}).get("id")
            # A title edit or rerun can create another suite on the same SHA.
            # Supersede an older result from the same provider, but retain
            # failures from other providers with the same check name.
            key = (
                (provider, check["name"])
                if provider is not None and check.get("id")
                else ("unknown", len(checks))
            )
            if key not in checks or check.get("id", 0) > checks[key].get("id", 0):
                checks[key] = check
        if len(data["check_runs"]) < 100:
            break
        page += 1
    return list(checks.values())


def checks_for(token, repo, sha):
    checks = {}

    def record(name, value):
        if name not in checks or checks[name] in {"success", "neutral", "skipped"}:
            checks[name] = value

    for check in latest_check_runs(token, repo, sha):
        record(
            check["name"], check.get("conclusion") if check["status"] == "completed" else "pending"
        )
    # The combined endpoint includes the latest status per context.
    page = 1
    while True:
        data = github(
            token,
            "GET",
            f"/repos/{repo}/commits/{sha}/status",
            params={"per_page": 100, "page": page},
        )
        for status in data["statuses"]:
            record(status["context"], status["state"])
        if len(data["statuses"]) < 100:
            break
        page += 1
    return checks


def pr_eligible(token, pr, config):
    if pr.get("state") != "open" or pr.get("draft"):
        return False
    head = checks_for(token, config["repository"], pr["head"]["sha"])
    merge_sha = pr.get("merge_commit_sha")
    merged = checks_for(token, config["repository"], merge_sha) if merge_sha else {}
    # GitHub may run CI on the synthetic merge revision instead of head.
    # Pending/failing checks on either current revision still block review.
    return checks_pass({**head, **merged}, config) and all(
        value in config["accepted_check_results"] for value in head.values()
    )
