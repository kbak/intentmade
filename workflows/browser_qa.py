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
        return self


def selected(config, state):
    settings = config.get("browser_qa")
    if not settings:
        return False
    files = git(
        [
            "--git-dir",
            state["repository"],
            "diff",
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
            + "\nApproved specification:\n"
            + request,
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
    except Exception as exc:
        result.update(status="BLOCKED", summary=f"{type(exc).__name__}: {exc}")
        # Do not accidentally treat model-reported, unretained filenames as attachments.
        result["screenshots"] = [s for s in result["screenshots"] if s.get("path")]
    (destination / "result.json").write_text(json.dumps(result, indent=2))
    return result
