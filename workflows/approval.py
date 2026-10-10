"""Capture issue specifications and verify optional label-based approval."""

import datetime
import hashlib
import json

import authorization
import deployment
from common import github, issues
from policy import issue_snapshot

QUERY = """
query FactoryIssueApproval($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      title
      body
      lastEditedAt
      timelineItems(last: 1, itemTypes: [RENAMED_TITLE_EVENT]) {
        nodes { ... on RenamedTitleEvent { createdAt } }
      }
    }
  }
}
"""


def timestamp(value):
    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timestamp has no timezone")
        return result
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError("GitHub approval history is unavailable; no work authorized") from None


def microseconds(value):
    delta = value - datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


# [impl->req~im-approval-snapshot~1]
# [impl->req~im-authorization~1]
def approved_issue(config, number, credential, expected=None):
    """Return one verified content snapshot, optionally matching a running task.

    Manual submissions verify the operator-captured snapshot without labels.
    For automatic intake without a label, the configured scheduler authorizes implementation
    and the content snapshot protects a running task from specification changes.
    With a label, `updated_at` includes comments, assignments and labels, so it cannot prove
    which specification was approved. GitHub's body-edit and title-rename
    history can: both must precede the latest approval-label event. GitHub
    timestamps have second precision; an edit in the approval's second is
    ambiguous and requires reviewing the content and reapplying the label.
    """
    if (
        deployment.issue_intake(config) == "manual"
        or (expected or {}).get("authorization") == "operator"
    ):
        if expected is None:
            raise RuntimeError("Explicit issue submission snapshot required; no work authorized")
        current = issues._get_issue(credential, config["repository"], number)
        content, snapshot = issue_snapshot(config, current)
        if expected.get("authorization") == "operator":
            snapshot["authorization"] = "operator"
        if snapshot != expected:
            raise RuntimeError(
                "Issue specification changed; submit the current specification again"
            )
        return content, snapshot
    deployment.check_issue_authorization(config)
    repo = config["repository"]
    if not config.get("issue_label"):
        current = issues._get_issue(credential, repo, number)
        content, snapshot = issue_snapshot(config, current)
        if expected is not None and snapshot != expected:
            raise RuntimeError("Issue specification changed; publication withheld")
        return content, snapshot
    events = issues._github_paginate(credential, f"/repos/{repo}/issues/{number}/events")
    matching = [
        event
        for event in events
        if event.get("event") == "labeled"
        and (event.get("label") or {}).get("name", "").lower() == config["issue_label"].lower()
        and event.get("id") is not None
    ]
    if not matching:
        raise RuntimeError("No approval-label event found; no work authorized")
    latest = max(matching, key=lambda event: (timestamp(event.get("created_at")), int(event["id"])))
    approved_at = timestamp(latest["created_at"])
    owner, name = repo.split("/")
    response = github(
        credential,
        "POST",
        "/graphql",
        body={"query": QUERY, "variables": {"owner": owner, "name": name, "number": number}},
    )
    try:
        if response.get("errors"):
            raise ValueError("GraphQL returned errors")
        current = response["data"]["repository"]["issue"]
        content = {"title": current["title"], "body": current["body"]}
        if not all(isinstance(value, str) for value in content.values()):
            raise ValueError("Missing issue content")
        edited_at = current["lastEditedAt"]
        renames = current["timelineItems"]["nodes"]
        if not isinstance(renames, list) or len(renames) > 1:
            raise ValueError("Missing title history")
        changes = [node["createdAt"] for node in renames]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeError("GitHub approval history is unavailable; no work authorized") from None
    if edited_at is not None:
        changes.append(edited_at)
    facts = {
        "has_changes": bool(changes),
        "latest_change_at": max(
            (microseconds(timestamp(changed)) for changed in changes), default=0
        ),
        "approved_at": microseconds(approved_at),
    }
    if not authorization.permit(
        config,
        "issue:approval",
        {"issue": number, "label_event": str(latest["id"])},
        facts,
    ):
        raise RuntimeError(
            "Issue title or body was edited at or after approval; review the current "
            "specification, wait a second, then remove and reapply the approval label"
        )
    snapshot = {
        "repository": repo,
        "issue": number,
        "label_event": str(latest["id"]),
        "approved_at": latest["created_at"],
        "content_sha256": hashlib.sha256(
            json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    if expected is not None and snapshot != expected:
        raise RuntimeError("Issue specification or approval changed; publication withheld")
    return content, snapshot
