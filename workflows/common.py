"""Shared configuration, native APIs and upstream OpenHands helpers."""

import fcntl
import importlib.util
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

import deployment
import httpx

ROOT = Path(os.environ.get("FACTORY_ROOT", "/opt/factory"))
DATA = Path(os.environ.get("FACTORY_DATA", "/workspaces"))


def load_upstream(name):
    filename = "reviews_bounded.py" if name == "reviews" else name + ".py"
    spec = importlib.util.spec_from_file_location(name, ROOT / "upstream" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Immutable upstream sources, downloaded and verified when building the image.
issues = load_upstream("issues")
reviews = load_upstream("reviews")
git = issues._git


def identifier(value, max_length=80):
    if not re.fullmatch(rf"[a-zA-Z0-9][a-zA-Z0-9_.-]{{0,{max_length - 1}}}", value):
        raise ValueError(
            "Use a short repository/task identifier containing letters, numbers, . _ -"
        )
    return value


# [impl->req~im-trace-opt-in~1]
def projects(config_dir=None):
    directory = Path(config_dir) if config_dir is not None else ROOT / "config"
    defaults = json.loads((directory / "defaults.json").read_text())
    options = deployment.settings(directory)
    result = {}
    for file in sorted((directory / "repositories").glob("*.json")):
        name = identifier(file.stem)
        registration = json.loads(file.read_text())
        config = {**deployment.workflow_settings(defaults, registration), "project": name}
        if config.get("enabled") and config.get("repository"):
            deployment.check_issue_authorization(config, options)
        if config.get("test_profile"):
            identifier(config["test_profile"])
        if not isinstance(config.get("pr_feedback", False), bool):
            raise ValueError(f"{name}: pr_feedback must be true or false")
        bots = config.get("pr_feedback_bots", [])
        if not isinstance(bots, list) or any(
            not isinstance(bot, str) or not bot.strip() for bot in bots
        ):
            raise ValueError(f"{name}: pr_feedback_bots must be a list of GitHub logins")
        limit = config.get("pr_feedback_attempts", 3)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"{name}: pr_feedback_attempts must be a positive integer")
        qa = config.get("browser_qa")
        if qa is not None:
            if not isinstance(qa, dict) or any(
                not isinstance(qa.get(k), str) or not qa[k].strip()
                for k in ("start_command", "url")
            ):
                raise ValueError(f"{name}: browser_qa needs start_command and url")
            for key in ("paths", "exclude"):
                patterns = qa.get(key, [])
                if (
                    not isinstance(patterns, list)
                    or any(not isinstance(p, str) or not p for p in patterns)
                    or (key == "paths" and not patterns)
                ):
                    raise ValueError(f"{name}: browser_qa.{key} must contain file patterns")
            if not isinstance(qa.get("instructions", ""), str):
                raise ValueError(f"{name}: browser_qa.instructions must be text")
        if config["repository"]:
            config["repository"] = issues.normalize_repo(config["repository"])
        if "traceability" in config:
            raise ValueError(f"{name}: replace inline traceability with a traceability_scope path")
        if "traceability_scope_git" in config:
            if "traceability_scope" in config:
                raise ValueError(f"{name}: choose traceability_scope or traceability_scope_git")
            if "test_command" in registration:
                raise ValueError(f"{name}: put the test command only in the traceability scope")
            from traceability.scope_identity import capture_git

            config["traceability_scope"], config["traceability_scope_source"] = capture_git(
                Path("/projects/repos") / name, config["traceability_scope_git"]
            )
            config.pop("test_command", None)
        elif "traceability_scope" in config:
            reference = config["traceability_scope"]
            if (
                not isinstance(reference, str)
                or not reference.strip()
                or Path(reference).is_absolute()
            ):
                raise ValueError(f"{name}: traceability_scope must be a relative config path")
            scope_path = (directory / reference).resolve()
            if not scope_path.is_relative_to(directory.resolve()):
                raise ValueError(
                    f"{name}: traceability_scope must stay inside the config directory"
                )
            if "test_command" in registration:
                raise ValueError(f"{name}: put the test command only in the traceability scope")
            from traceability.scope_identity import capture

            # Capture and validate one read; native payloads retain exact source
            # bytes as well as the parsed policy, independent of later config edits.
            config["traceability_scope"], config["traceability_scope_source"] = capture(
                scope_path, reference
            )
            config.pop("test_command", None)
        elif not config.get("test_command", "").strip():
            raise ValueError(f"{name}: test_command must not be empty")
        if not config["required_checks"]:
            raise ValueError(f"{name}: required_checks must not be empty")
        result[name] = config
    return result


def factories(config_dir=None):
    """Optional repository groups; schedules and test settings remain per repository."""
    directory = Path(config_dir) if config_dir is not None else ROOT / "config"
    configured = projects(directory)
    result = {}
    for file in sorted((directory / "factories").glob("*.json")):
        name = identifier(file.stem)
        definition = json.loads(file.read_text())
        members = definition.get("repositories")
        if name in configured:
            raise ValueError(f"{name}: factory and repository names must be distinct")
        if (
            set(definition) != {"repositories"}
            or not isinstance(members, list)
            or not members
            or any(not isinstance(member, str) or member not in configured for member in members)
            or len(set(members)) != len(members)
        ):
            raise ValueError(f"{name}: specify a nonempty list of unique registered repositories")
        remotes = [configured[member]["repository"] for member in members]
        remotes = [remote for remote in remotes if remote]
        if len(set(remotes)) != len(remotes):
            raise ValueError(f"{name}: the same GitHub repository cannot appear twice")
        result[name] = members
    return result


def session_api_key():
    # Native command runners can filter secret environment variables. The
    # credential-bearing parent retains its protected Docker secret mount.
    return (
        os.environ.get("OH_SESSION_API_KEYS_0")
        or os.environ.get("SESSION_API_KEY")
        or Path("/run/secrets/canvas-key").read_text().strip()
    )


def api(method, path, **kwargs):
    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        headers={"X-Session-API-Key": session_api_key()},
        timeout=120,
    ) as client:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None


def github(token, method, path, **kwargs):
    return issues._github_request(token, method, path, **kwargs)[0]


def token():
    # Native secret storage; never copy the GitHub token into a worker.
    return issues.get_secret("GITHUB_PERSONAL_ACCESS_TOKEN")


# [impl->req~im-repository-locks~1]
@contextmanager
def lock(name, blocking=False):
    directory = DATA / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (identifier(name, 86) + ".lock")).open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def job_id():
    return os.environ["AUTOMATION_RUN_ID"]


def evidence(name):
    path = Path("/projects/artifacts") / identifier(name, 117)
    path.mkdir(exist_ok=True, mode=0o755)
    return path
