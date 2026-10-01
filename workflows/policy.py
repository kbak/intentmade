"""Deterministic selection policy; no agent decides eligibility or ownership."""

import hashlib
import json
import re
from collections import defaultdict
from fnmatch import fnmatchcase

from common import github, issues
from review_requests import comparison_files


# [impl->req~im-issue-ownership~1]
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
    groups = defaultdict(list)
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
            groups[provider, check["name"]].append(check)
        if len(data["check_runs"]) < 100:
            break
        page += 1
    checks, runs = [], {}
    for group in groups.values():
        if len(group) == 1:
            checks.extend(group)
            continue
        workflows = defaultdict(list)
        for check in group:
            run = re.fullmatch(
                re.escape(f"https://github.com/{repo}/actions/runs/") + r"(\d+)/job/\d+",
                check.get("details_url") or "",
                re.IGNORECASE,
            )
            if not run or (check.get("app") or {}).get("slug") != "github-actions":
                # A shared provider/name alone never establishes a retry relationship.
                checks.append(check)
                continue
            run_id = int(run[1])
            if run_id not in runs:
                runs[run_id] = github(token, "GET", f"/repos/{repo}/actions/runs/{run_id}")
            metadata = runs[run_id]
            if not metadata.get("workflow_id") or not metadata.get("event"):
                checks.append(check)
                continue
            workflows[
                metadata["workflow_id"], metadata["event"], metadata.get("head_branch")
            ].append((run_id, check))
        for attempts in workflows.values():
            latest = max(run_id for run_id, _ in attempts)
            # Keep all jobs in that run, including matrix jobs with identical names.
            # GitHub's filter=latest already selects rerun attempts within a suite.
            checks.extend(check for run_id, check in attempts if run_id == latest)
    return checks


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


def validate_check_overrides(config):
    rules = config.get("required_check_overrides", [])
    if not isinstance(rules, list):
        raise ValueError("required_check_overrides must be a list")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"paths", "required_checks"}:
            raise ValueError("Each required check override needs paths and required_checks")
        for key in ("paths", "required_checks"):
            if (
                not isinstance(rule[key], list)
                or not rule[key]
                or any(not isinstance(value, str) or not value.strip() for value in rule[key])
            ):
                raise ValueError(f"Required check override {key} must contain nonempty patterns")
    return rules


# [impl->req~im-pr-publication~1]
def required_checks_for(token, pr, config):
    rules = validate_check_overrides(config)
    if not rules:
        return config["required_checks"]
    files = comparison_files(config, pr, token)
    paths = []
    for file in files:
        paths.append(file["filename"])
        if file.get("status") == "renamed":
            # A move into a path must not waive checks for its previous location.
            paths.append(file["previous_filename"])
    selected = []
    for rule in rules:
        if paths and all(
            any(fnmatchcase(path, pattern) for pattern in rule["paths"]) for path in paths
        ):
            selected.extend(rule["required_checks"])
    return list(dict.fromkeys(selected)) if selected else config["required_checks"]


def pr_eligible(token, pr, config):
    if pr.get("state") != "open" or pr.get("draft"):
        return False
    config = {**config, "required_checks": required_checks_for(token, pr, config)}
    head = checks_for(token, config["repository"], pr["head"]["sha"])
    merge_sha = pr.get("merge_commit_sha")
    merged = checks_for(token, config["repository"], merge_sha) if merge_sha else {}
    # GitHub may run CI on the synthetic merge revision instead of head.
    # Pending/failing checks on either current revision still block review.
    return checks_pass({**head, **merged}, config) and all(
        value in config["accepted_check_results"] for value in head.values()
    )
