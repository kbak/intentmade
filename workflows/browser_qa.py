"""Verify retained UI commits and export evidence through native workspace APIs."""

import fnmatch
import hashlib
import json
import re
import shlex
from typing import Literal

from agent import converse
from common import git
from openhands.sdk.mcp.config import MCPServer
from PIL import Image
from pydantic import BaseModel, Field, model_validator
from transfer import worker_git

MAX_IMAGE_BYTES = 10 * 1024 * 1024


class BrowserCheck(BaseModel):
    name: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "BLOCKED"]
    observed: str = Field(min_length=1)
    blocker_kind: Literal["infrastructure", "verification"] = "verification"


class Screenshot(BaseModel):
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.png$")
    caption: str = Field(min_length=1)
    url: str = Field(min_length=1)


class BrowserResult(BaseModel):
    status: Literal["PASS", "FAIL", "BLOCKED"]
    summary: str = Field(min_length=1)
    checks: list[BrowserCheck] = Field(default_factory=list, max_length=30)
    screenshots: list[Screenshot] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def complete(self):
        if self.status == "PASS" and (
            not self.checks or not self.screenshots or any(c.status != "PASS" for c in self.checks)
        ):
            raise ValueError("Browser PASS requires passed checks and screenshots")
        if self.status == "FAIL" and not any(c.status == "FAIL" for c in self.checks):
            raise ValueError("Browser FAIL requires an observed failing check")
        if len({s.filename for s in self.screenshots}) != len(self.screenshots):
            raise ValueError("Screenshot filenames must be unique")
        if len({c.name for c in self.checks}) != len(self.checks):
            raise ValueError("Browser check names must be unique")
        return self


def accepted_gaps(answers):
    """Read explicit directives only from maintainer replies to this specification.

    The last directive replaces the acceptance, including {} to revoke it.
    Issue bodies, repository instructions and worker output cannot grant it.
    """
    directive = "{}"
    for answer in answers:
        for line in answer.splitlines():
            prefix = "accept-browser-gaps:"
            if line.strip().startswith(prefix):
                directive = line.strip()[len(prefix) :]
    try:
        value = json.loads(directive)
    except ValueError as exc:
        raise ValueError(
            "accept-browser-gaps requires a JSON object of check names and reasons"
        ) from exc
    if (
        not isinstance(value, dict)
        or len(value) > 30
        or any(
            not name.strip() or not isinstance(reason, str) or not reason.strip()
            for name, reason in value.items()
        )
    ):
        raise ValueError("accept-browser-gaps requires check names and nonempty reasons")
    return value


def infrastructure_gaps(result):
    """Eligible checks, after startup, evidence retention and source integrity succeed."""
    checks = result.get("checks", [])
    blocked = [c for c in checks if c["status"] == "BLOCKED"]
    if (
        result["status"] != "BLOCKED"
        or not blocked
        or not any(c["status"] == "PASS" for c in checks)
        or any(c["status"] == "FAIL" for c in checks)
        or not result.get("screenshots")
        or any(c.get("blocker_kind") != "infrastructure" for c in blocked)
    ):
        return []
    return blocked


def accept_infrastructure_gaps(result, accepted):
    """Called only after startup, evidence retention and source integrity succeed."""
    blocked = infrastructure_gaps(result)
    if not blocked or any(c["name"] not in accepted for c in blocked):
        return
    result.update(
        status="ACCEPTED_GAPS",
        reported_status="BLOCKED",
        accepted_gaps={c["name"]: accepted[c["name"]] for c in blocked},
    )


def gap_resume_hint(result, accepted):
    blocked = infrastructure_gaps(result)
    if not blocked:
        return ""
    proposed = dict(accepted)
    for check in blocked:
        proposed.setdefault(
            check["name"],
            "Proceed with this infrastructure check unverified and document the limitation in the draft PR.",
        )
    if len(proposed) > 30:
        return ""
    return (
        "To proceed with these named infrastructure checks unverified, send this exact reply "
        "in the paused issue's report (or supply the directive through retry-issue --answer-file):\n\n"
        "```text\nresume: accept-browser-gaps: " + json.dumps(proposed) + "\n```\n\n"
        "A plain `resume: go ahead` or `resume: retry` does not record gap acceptance. "
        "The report assistant must preserve the directive when explaining how to continue; "
        "it cannot record acceptance by acknowledging a conversational approval. "
        "Available checks, retained evidence, source integrity, tests and independent review remain required."
    )


def passed(result):
    return result["status"] in {"PASS", "ACCEPTED_GAPS"}


def limitations(result):
    accepted = result.get("accepted_gaps", {})
    if not accepted:
        return ""
    return "\n\n### Accepted verification gaps\n\n" + "\n".join(
        f"- **{check['name']}** — Not verified: {check['observed']} "
        f"Maintainer acceptance: {accepted[check['name']]}"
        for check in result["checks"]
        if check["name"] in accepted
    )


def selected(config, state):
    settings = config.get("browser_qa")
    if not settings:
        return False
    files = git(
        [
            "--git-dir",
            state["repository"],
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            state["base"],
            state["commit"],
        ]
    ).stdout.split("\0")
    return any(
        any(fnmatch.fnmatchcase(file, pattern) for pattern in settings["paths"])
        and not any(fnmatch.fnmatchcase(file, pattern) for pattern in settings.get("exclude", []))
        for file in files
        if file
    )


def mcp_config(output):
    return {
        "playwright": MCPServer(
            command="/acp-node/bin/node",
            args=[
                "/acp-node/lib/node_modules/@playwright/mcp/cli.js",
                "--headless",
                "--isolated",
                "--no-sandbox",
                "--executable-path",
                "/usr/bin/chromium",
                "--output-dir",
                str(output),
                "--viewport-size",
                "1280x800",
            ],
        )
    }


def retain(workspace, output, destination, screenshots):
    """Accept only named PNGs; persist in a parent-owned directory, outside Git."""
    destination.mkdir(parents=True, exist_ok=True)
    retained = []
    for index, screenshot in enumerate(screenshots):
        name = screenshot.filename
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.png", name):
            raise ValueError("Unsafe screenshot filename")
        target = destination / f"{index + 1}.png"
        result = workspace.file_download(str(output / name), str(target))
        if not result.success or not target.is_file() or target.stat().st_size > MAX_IMAGE_BYTES:
            target.unlink(missing_ok=True)
            raise RuntimeError("Screenshot missing or exceeds 10 MiB: " + name)
        try:
            with Image.open(target) as image:
                if image.format != "PNG" or min(image.size) < 16:
                    raise ValueError("Invalid screenshot format or dimensions")
                image.verify()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        retained.append(
            {
                **screenshot.model_dump(),
                "path": str(target),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    return retained


def run(workspace, config, state, request, output, destination):
    settings, checkout = config["browser_qa"], state["source"]
    expected = state["commit"]
    accepted = config.get("accepted_browser_gaps", {})
    result = {"status": "BLOCKED", "commit": expected, "screenshots": []}
    destination.mkdir(parents=True, exist_ok=True)
    try:
        actual = worker_git(workspace, ["rev-parse", "HEAD"], checkout).stdout.strip()
        if actual != expected:
            raise RuntimeError("QA checkout differs from the retained commit")
        workspace.working_dir = checkout
        env = {"PROJECT_DIR": checkout, "FACTORY_QA_OUTPUT": str(output)}
        if config.get("test_profile"):
            env["FACTORY_TESTS"] = "/factory-tests/" + config["test_profile"]
        command = " ".join(k + "=" + shlex.quote(v) for k, v in env.items())
        startup = workspace.execute_command(
            command + " bash -c " + shlex.quote(settings["start_command"]),
            cwd=checkout,
            timeout=1800,
        )
        (destination / "startup.log").write_text(startup.stdout + startup.stderr)
        if startup.exit_code:
            raise RuntimeError("Application startup failed; see startup.log")
        response = converse(
            workspace,
            "Verify this retained commit using the factory-browser-qa skill.\n"
            + f"Commit: {expected}\nReview base: {state['base']}\nApplication: {settings['url']}\n"
            + f"Save PNG screenshots with simple filenames under {output}.\n"
            + "Repository QA instructions:\n"
            + settings.get("instructions", "")
            + "\nTask-scoped maintainer acceptance of unavailable infrastructure checks:\n"
            + json.dumps(accepted)
            + "\nFor those same unavailable dependencies, reuse the exact check names above. "
            "Still report them BLOCKED with blocker_kind=infrastructure and explain what was "
            "not tested. Test all available behavior. Do not broaden acceptance to other gaps, "
            "observed defects, or missing evidence. The parent applies acceptance after verifying "
            "screenshots and source integrity.\n" + "\nApproved specification:\n" + request,
            mode="agent-full-access",
            title="Browser acceptance QA",
            response_model=BrowserResult,
            skill="factory-browser-qa",
            transcript=destination / "conversation.jsonl",
            mcp_config=mcp_config(output),
        )
        result.update(response.model_dump())
        result["screenshots"] = retain(workspace, output, destination, response.screenshots)
        head = worker_git(workspace, ["rev-parse", "HEAD"], checkout).stdout.strip()
        changes = worker_git(
            workspace, ["status", "--porcelain", "--untracked-files=normal"], checkout
        ).stdout
        if head != expected or changes.strip():
            raise RuntimeError(
                "QA modified the source checkout; evidence does not verify the retained commit"
            )
        accept_infrastructure_gaps(result, accepted)
        hint = gap_resume_hint(result, accepted)
        if hint:
            result["resume_hint"] = hint
    except Exception as exc:
        result.update(status="BLOCKED", summary=f"{type(exc).__name__}: {exc}")
        # Do not accidentally treat model-reported, unretained filenames as attachments.
        result["screenshots"] = [s for s in result["screenshots"] if s.get("path")]
    (destination / "result.json").write_text(json.dumps(result, indent=2))
    return result
