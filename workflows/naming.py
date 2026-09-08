"""Describe the change in GitHub names, independently of its execution tool."""

import re
import unicodedata

TYPES = {
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "chore",
    "revert",
    "ci",
    "build",
    "sec",
    "wip",
}
ALIASES = {"bug": "fix", "bugfix": "fix", "feature": "feat", "enhancement": "feat"}


def change_title(request):
    text = next(
        (line.strip().lstrip("# ") for line in request.splitlines() if line.strip()),
        "Update application",
    )
    text = re.sub(r"^\[#\d+\]\s*", "", text)
    match = re.match(r"^(\w+)(\([^\r\n)]+\))?(!)?:\s*(.+)$", text)
    if match:
        kind = ALIASES.get(match[1].lower(), match[1].lower())
        if kind in TYPES:
            return f"{kind}{match[2] or ''}{match[3] or ''}: {match[4]}"[:180]
    return "chore: " + text[:173]


def branch_name(task, request, prefix=None):
    title = change_title(request)
    kind, _, description = title.partition(": ")
    scope = re.search(r"\(([^)]+)\)", kind)
    description = ((scope[1] + " ") if scope else "") + description
    ascii_text = unicodedata.normalize("NFKD", description).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:60].rstrip("-")
    number = task.removeprefix("issue-")
    category = prefix or re.match(r"\w+", kind)[0]
    return f"{category}/{number}-{slug or 'update'}"


def pull_request_title(request, config):
    title = change_title(request)
    prefix = config.get("pr_title_subject_prefix")
    if prefix:
        kind, _, subject = title.partition(": ")
        if not re.match(r"^(?:[A-Z]+-\d+|NOSTORY): .+", subject):
            title = f"{kind}: {prefix}: {subject}"
    return title
