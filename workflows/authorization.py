"""Shared Cedar authorization and controller-owned decision receipts."""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from openhands.sdk.utils.files import atomic_write_text

ROOT = Path(os.environ.get("FACTORY_ROOT", "/opt/factory"))
BRIDGE = "/opt/factory/cedar/intentmade-cedar"
ARTIFACTS = Path("/projects/artifacts")


def policy_digest():
    root = ROOT / "cedar"
    return hashlib.sha256(
        (
            (root / "schema.cedarschema").read_text() + "\n" + (root / "policy.cedar").read_text()
        ).encode()
    ).hexdigest()


def request(action, task, attempt, artifact, facts):
    return {
        "protocol": 3,
        "request_id": str(uuid4()),
        "run": artifact.name,
        "task": task,
        "attempt": attempt,
        "source_identity": None,
        "action": action,
        "facts": facts,
    }


def evaluate(payload, artifact, filename, source):
    receipt = {"request": payload, "status": "ERROR"}
    start = time.monotonic()
    directory = artifact / "authorization"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload["source_identity"] = source()
        result = subprocess.run(
            [BRIDGE],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        verdict = json.loads(result.stdout)
        if (
            type(verdict.get("protocol")) is not int
            or verdict["protocol"] != 3
            or verdict.get("request_id") != payload["request_id"]
            or verdict.get("action") != payload["action"]
            or verdict.get("engine_version") != "4.13.0"
            or verdict.get("policy_sha256") != policy_digest()
            or type(verdict.get("allowed")) is not bool
        ):
            raise ValueError("Cedar response action, identity, policy or verdict mismatch")
        receipt.update(decision=verdict, status="ALLOW" if verdict["allowed"] else "DENY")
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
    ) as exc:
        receipt["error_type"] = type(exc).__name__
    receipt["elapsed_ms"] = round((time.monotonic() - start) * 1000, 3)
    try:
        path = directory / filename
        # The controller's task lock serializes attempts. Preserve earlier
        # receipts; the SDK writer makes a new receipt atomic and private.
        if os.path.lexists(path):
            raise FileExistsError(path)
        atomic_write_text(path, json.dumps(receipt, indent=2) + "\n", mode=0o600)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("Cedar authorization receipt unavailable; progression blocked") from exc
    print(f"Cedar {payload['action']}: {receipt['status']}", flush=True)
    if receipt["status"] == "ERROR":
        raise RuntimeError("Cedar authorization unavailable; progression blocked")
    return receipt["decision"]["allowed"]


# [impl->req~im-authorization~1]
def permit(config, action, subject, facts):
    """Authorize from current facts without retaining an engine event history."""
    from common import identifier

    run = os.environ.get("AUTOMATION_RUN_ID", str(uuid4()))
    artifact = ARTIFACTS / ("authorization-decisions-" + identifier(run))
    payload = request(action, action, 0, artifact, facts)
    identity = json.dumps(
        {
            "project": config.get("project"),
            "repository": config.get("repository"),
            "subject": subject,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return evaluate(payload, artifact, payload["request_id"] + ".json", lambda: identity)


# [impl->req~im-authorization~1]
def authorize(configs, task, attempt, artifact, states, facts):
    """Authorize the complete group from validated evidence and source identities."""

    def source():
        projects = {c["project"]: c["repository"] for c in configs}
        if not projects or len(projects) != len(configs) or projects.keys() != states.keys():
            raise ValueError("Incomplete group source identity")
        sources = {
            project: {
                "repository": projects[project] or state["repository"],
                "base": state["base"],
                "candidate": state["commit"],
                "work_package": state.get("work_package"),
            }
            for project, state in sorted(states.items())
        }
        if any(
            not isinstance(item[key], str) or not item[key].strip()
            for item in sources.values()
            for key in ("repository", "base", "candidate")
        ):
            raise ValueError("Missing group source identity")
        return json.dumps(sources, sort_keys=True, separators=(",", ":"))

    return evaluate(
        request("validation:complete", task, attempt, artifact, facts),
        artifact,
        f"attempt-{attempt}.json",
        source,
    )
