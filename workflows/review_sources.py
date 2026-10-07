"""Controller-captured reference repositories for standalone PR reviews."""

import re
import shutil
from urllib.parse import quote

from common import github, issues, projects, reviews


def validate(config, registered):
    names = config.get("review_repositories", [])
    if (
        not isinstance(names, list)
        or len(names) > 16
        or any(not isinstance(name, str) or name not in registered for name in names)
        or len(set(names)) != len(names)
        or config.get("project") in names
        or any(not registered[name].get("repository") for name in names)
    ):
        raise ValueError(
            "review_repositories must name up to 16 unique other registered GitHub repositories"
        )
    return names


# [impl->req~im-immutable-pr-comparison~1]
def prepare(config, credential, root):
    if not config.get("review_repositories"):
        return {}
    registered = projects()
    names = validate(config, registered)
    result = {}
    for name in names:
        reference = registered[name]
        repo, branch = reference["repository"], reference["branch"]
        commit = github(credential, "GET", f"/repos/{repo}/commits/{quote(branch, safe='')}")
        sha = commit["sha"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Reference repository did not resolve to an immutable commit")
        # Reuse the pinned, bounded archive loader. WORKSPACE_BASE is the job
        # root, selected by the caller before any repository is downloaded.
        checkout = reviews._prepare_repository(credential, repo, 0, sha)
        destination = root / "review-repositories" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.move(str(checkout), destination)
        pulls = issues._github_paginate(credential, f"/repos/{repo}/commits/{sha}/pulls")
        result[name] = {
            "repository": repo,
            "branch": branch,
            "commit": sha,
            "source": str(destination),
            "associated_pull_requests": [
                {
                    key: pr.get(key)
                    for key in ("number", "html_url", "state", "merged_at", "merge_commit_sha")
                }
                for pr in pulls
            ],
        }
    return result
