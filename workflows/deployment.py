"""Operator-owned deployment settings, separate from captured workflow policy."""

import json
import os
from pathlib import Path

FIELDS = {"worker_runtime", "resource_limits", "authorization", "worker_agent_profile"}
LEGACY_FIELDS = {"worker_runtime", "resource_limits"}


def read(path):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


# [impl->req~im-agent-profile~1]
# [impl->req~im-deployment-authority~1]
def settings(config_dir=None):
    from docker_sandboxes import validate as validate_runtime
    from resource_limits import validate as validate_limits

    directory = (
        Path(config_dir)
        if config_dir is not None
        else Path(os.environ.get("FACTORY_ROOT", "/opt/factory")) / "config"
    )
    options = read(directory / "deployment.json")
    defaults = read(directory / "defaults.json")
    if set(options) - FIELDS:
        raise ValueError("deployment.json contains unsupported settings")
    if "authorization" in defaults:
        raise ValueError("Set factory-wide authorization in deployment.json")
    if "worker_agent_profile" in defaults:
        raise ValueError("Set factory-wide worker_agent_profile in deployment.json")
    # Existing installations keep their settings until the operator moves them.
    # Reject duplicate definitions rather than silently choosing weaker values.
    for name in LEGACY_FIELDS & defaults.keys():
        if name in options:
            raise ValueError(
                f"Define {name} only once; move it from defaults.json to deployment.json"
            )
        options[name] = defaults[name]
    authorization = options.get("authorization", {})
    if not isinstance(authorization, dict) or set(authorization) - {"require_issue_approval"}:
        raise ValueError("authorization must contain supported settings")
    required = authorization.get("require_issue_approval", False)
    if not isinstance(required, bool):
        raise ValueError("authorization.require_issue_approval must be a boolean")
    agent_profile = options.get("worker_agent_profile", "factory-codex")
    if not isinstance(agent_profile, str) or not agent_profile.strip():
        raise ValueError("worker_agent_profile must name a saved OpenHands agent profile")
    runtime = options.get("worker_runtime", {})
    if not isinstance(runtime, dict):
        raise ValueError("worker_runtime must be an object")
    runtime = dict(runtime)
    host_directory = Path(os.environ.get("FACTORY_HOST_CONFIG_DIR", str(directory.resolve())))
    for key in ("kit", "profiles"):
        path = runtime.get(key)
        if isinstance(path, str) and path.strip() and not Path(path).is_absolute():
            runtime[key] = str((host_directory / path).resolve())
    return {
        "worker_agent_profile": agent_profile,
        "worker_runtime": validate_runtime(runtime),
        "resource_limits": validate_limits(options.get("resource_limits", {})),
        "authorization": {"require_issue_approval": required},
    }


# [impl->req~im-deployment-authority~1]
def workflow_settings(defaults, registration):
    """Keep operator settings out of repository overrides and submitted jobs."""
    for name in FIELDS & registration.keys():
        raise ValueError(f"Set factory-wide {name} in deployment.json")
    return {**{key: value for key, value in defaults.items() if key not in FIELDS}, **registration}


# [impl->req~im-deployment-authority~1]
def check_issue_authorization(config, options=None):
    """Apply the current operator requirement even to captured job settings."""
    options = settings() if options is None else options
    if options["authorization"]["require_issue_approval"]:
        label = config.get("issue_label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                "This deployment requires issue approval; configure a nonempty issue_label "
                "and refresh the workflow before running issue work"
            )
