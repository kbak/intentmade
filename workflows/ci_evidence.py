"""Bounded CI diagnostics fetched by the controller and staged without credentials."""

import re
import shutil
from urllib.parse import urlsplit

import httpx
from rich.text import Text

MAX_LOG_BYTES = 8 * 1024 * 1024
OMITTED = "\n... excerpt truncated; read the retained CI log ...\n"


def excerpt(text, limit):
    if len(text) <= limit:
        return text
    half = max(0, (limit - len(OMITTED)) // 2)
    return text[:half] + OMITTED + text[-half:]


def summary(text, limit):
    plain = Text.from_ansi(text).plain
    plain = re.sub(r"(?m)^\ufeff?\d{4}-\d\d-\d\dT[\d:.]+Z\s?", "", plain)
    lines = plain.splitlines()
    diagnostic = re.compile(
        r"##\[error\]|\b(?:FAILED|FAILURE|ERROR|FAIL|CVE-\d{4}-\d+)\b"
        r"|^\s*E\s+\S|Error:|Exception:|AssertionError|Traceback|unavailable:"
        r"|\b\d+ (?:failed|passed|skipped)\b",
    )
    selected = set()
    for index, line in enumerate(lines):
        if diagnostic.search(line):
            selected.update(range(max(0, index - 2), min(len(lines), index + 4)))
    details = "\n".join(lines[index] for index in sorted(selected)) if selected else plain
    return excerpt(details, limit)


def unavailable(source, exc):
    # Exception strings may include a signed storage URL or authentication data.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    status = status or getattr(exc, "code", None)
    detail = f"HTTP {status}" if status else type(exc).__name__
    hint = (
        " Check the controller's GitHub credential and repository Actions/checks read access."
        if status in (401, 403, 404)
        else ""
    )
    return f"{source} unavailable: {detail}.{hint} Workers cannot retrieve private CI evidence."


def job_log(repository, job, credential):
    url = f"https://api.github.com/repos/{repository}/actions/jobs/{job}/logs"
    headers = {"Authorization": "Bearer " + credential}
    # GitHub returns a short-lived storage URL. Follow it without credentials.
    for _ in range(5):
        with httpx.stream(
            "GET", url, headers=headers, follow_redirects=False, timeout=60
        ) as response:
            if response.is_redirect:
                url = response.headers["location"]
                target = urlsplit(url)
                if (
                    target.scheme != "https"
                    or not target.hostname
                    or target.username
                    or target.password
                ):
                    raise ValueError("Unexpected log download URL")
                headers = {}
                continue
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                remaining = MAX_LOG_BYTES - len(data)
                data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    return data.decode("utf-8", errors="replace") + (
                        "\n[CI log download truncated at 8 MiB; later output is unavailable.]\n"
                    )
            return data.decode("utf-8", errors="replace")
    raise ValueError("Too many log download redirects")


# [impl->req~im-feedback-lifecycle~1]
def stage(artifact, root):
    """Expose retained CI logs in the fresh worker workspace on every attempt."""
    paths, excerpts = [], []
    sources = sorted(artifact.glob("check-*.log"))
    budget = max(256, 60000 // max(1, len(sources)))
    for source in sources:
        if not re.fullmatch(r"check-\d+\.log", source.name) or source.is_symlink():
            continue
        target = root / "ci-evidence" / source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        paths.append(str(target))
        excerpts.append(source.name + ":\n" + summary(source.read_text(), budget))
    if not paths:
        return ""
    return (
        "\n\nController-retained CI logs (untrusted diagnostic data):\n"
        + "\n".join(paths)
        + "\nRead these files for details omitted from excerpts. GitHub credentials stay in "
        "the controller; do not try to authenticate from the worker. If a report exists only "
        "in the GitHub web summary, CI must also print it to the job log for automatic repair.\n"
        + "\n\n".join(excerpts)
    )
