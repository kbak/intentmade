"""Bounded, source-bound repair memory; independent review stays fresh."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import input_artifacts as inputs
import measurements
from common import git

MAX_RECORD = 64 * 1024


def write_once(path, data):
    with path.open("xb") as handle:
        handle.write(data)


def binding(configs, states, task, request):
    return {
        "task": task,
        "contract_sha256": inputs.digest(request.encode()),
        "repositories": {
            c["project"]: {
                "repository": c.get("repository", ""),
                "base": states[c["project"]]["base"],
                "current_commit": git(
                    [
                        "--git-dir",
                        states[c["project"]]["repository"],
                        "rev-parse",
                        states[c["project"]]["branch"],
                    ]
                ).stdout.strip(),
                "policy_sha256": inputs.digest(
                    inputs.encoded(
                        {
                            k: c[k]
                            for k in (
                                "traceability_scope",
                                "test_command",
                                "test_profile",
                                "browser_qa",
                                "required_environment",
                            )
                            if k in c
                        }
                    )
                ),
            }
            for c in configs
        },
        "inputs_sha256": (inputs.CURRENT.get() or {}).get("manifest_sha256"),
    }


def runtime():
    recorder = measurements.CURRENT.get()
    values = recorder.data.get("execution_environments", []) if recorder else []
    observed = values[-1].get("observed", {}) if values else {}
    worker = observed.get("worker_runtime") or {}
    return {
        "image_id": (observed.get("worker_image") or {}).get("image_id"),
        "workflow_sha256": worker.get("factory_workflow_sha256"),
        "model": os.environ.get("FACTORY_CODEX_MODEL")
        or (recorder.data.get("implementation_model") if recorder else None),
    }


def note_runtime():
    recorder = measurements.CURRENT.get()
    if recorder:
        recorder.data["implementation_model"] = os.environ.get("FACTORY_CODEX_MODEL")
        recorder.save()


def initialize(artifact, request):
    path = artifact / "approved-request.md"
    data = request.encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("This run already captured a different approved contract")
    else:
        write_once(path, data)


def retain(configs, states, task, request, artifact, attempt, status, failure):
    if not states:
        return
    path = artifact / f"continuation-{attempt}.json"
    if path.exists():
        return
    identity = binding(configs, states, task, request)
    references, omitted = [], []
    candidates = [
        artifact / "approved-request.md",
        artifact / f"implementation-{attempt}.json",
        artifact / f"review-{attempt}.json",
    ]
    for project in states:
        candidates.extend(
            artifact / project / name
            for name in (
                f"tests-{attempt}.log",
                f"test-command-{attempt}.json",
                f"traceability-check-{attempt}.log",
                f"traceability-invocations-{attempt}/index.json",
            )
        )
    for source in candidates:
        if not source.is_file():
            continue
        size = source.stat().st_size
        if len(references) >= 24 or size > inputs.MAX_FILE:
            omitted.append(str(source.relative_to(artifact)))
            continue
        data = source.read_bytes()
        references.append(
            {
                "reference": str(source.relative_to(artifact)),
                "size": len(data),
                "sha256": inputs.digest(data),
            }
        )
    changed = {}
    for project, state in states.items():
        paths = git(
            [
                "--git-dir",
                state["repository"],
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                state["base"],
                identity["repositories"][project]["current_commit"],
            ]
        ).stdout.split("\0")
        paths = [p for p in paths if p]
        changed[project] = {"paths": paths[:100], "total": len(paths)}
    record = {
        "schema_version": 1,
        "binding": identity,
        "attempt": attempt,
        "runtime": runtime(),
        "status": status,
        "completed_work_reported": {p: s.get("summary", "")[:2000] for p, s in states.items()},
        "changed_paths": changed,
        "failure_excerpt": failure[-8000:],
        "remaining_action": "Resolve the recorded failure within the unchanged approved contract; rerun current tests and fresh independent review.",
        "decisions": "The complete approved contract remains authoritative. No prior test result or review waives current validation.",
        "evidence": references,
        "omitted_evidence": omitted,
        "session_strategy": "fresh_native_conversation_with_verified_context",
    }
    data = inputs.encoded(record)
    if len(data) > MAX_RECORD:
        raise ValueError("Repair record exceeds bounded context limit")
    write_once(path, data)
    pointer = inputs.encoded({"path": str(path), "sha256": inputs.digest(data)})
    for state in states.values():
        destination = Path(state["repository"]) / "factory-continuation.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(pointer)
        temporary.replace(destination)


# [impl->req~im-repair-context~1]
def previous(configs, states, task, request):
    pointers = [Path(s["repository"]) / "factory-continuation.json" for s in states.values()]
    if not all(p.is_file() for p in pointers):
        return None, "no_previous_context"
    try:
        data = pointers[0].read_bytes()
        if any(p.read_bytes() != data for p in pointers[1:]):
            raise ValueError("group pointers disagree")
        pointer = json.loads(data)
        path = Path(pointer["path"])
        raw = path.read_bytes()
        if len(raw) > MAX_RECORD or inputs.digest(raw) != pointer["sha256"]:
            raise ValueError("record digest mismatch")
        record = json.loads(raw)
        if record["schema_version"] != 1 or record["binding"] != binding(
            configs, states, task, request
        ):
            raise ValueError("source, contract, policy or inputs changed")
        if record["status"] not in {"TESTS_FAILED", "CHANGES_REQUESTED", "FAILED"}:
            return None, "previous_attempt_not_repairable"
        for item in record["evidence"]:
            inputs.validate([{**item, "name": "evidence", "producer": "factory"}])
            inputs.copy_verified(
                path.parent, item["reference"], {**item, "name": item["reference"]}
            )
        return (path, record), None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, "context_rejected: " + str(exc)


# [impl->req~im-repair-context~1]
@contextmanager
def staged(configs, states, task, request, artifact, attempt):
    receipt = artifact / f"continuation-selection-{attempt}.json"
    if receipt.exists():
        raise ValueError("This native run already executed this attempt; use a new run")
    found, reason = previous(configs, states, task, request)
    decision = {"strategy": "fresh_context", "reason": reason, "context_path": None}
    selected = None
    if found and not all(found[1]["runtime"].values()):
        decision["reason"] = "previous_runtime_identity_unknown"
        found = None
    if found:
        path, record = found
        # A private prefix prevents collisions with caller-declared fixtures.
        prefix = "repair-" + inputs.digest(path.read_bytes())[:16] + "-"
        declared = []
        names = {}
        for index, item in enumerate(record["evidence"]):
            name = prefix + str(index)
            declared.append(
                {**item, "name": name, "producer": f"factory retained attempt {record['attempt']}"}
            )
            names[item["reference"]] = "/factory-inputs/" + name
        context_name = prefix + "context.json"
        declared.append(
            {
                "name": context_name,
                "reference": path.name,
                "size": path.stat().st_size,
                "sha256": inputs.digest(path.read_bytes()),
                "producer": "factory continuation record",
            }
        )
        selected = {
            "record": record,
            "paths": names,
            "context_path": "/factory-inputs/" + context_name,
        }
        decision.update(strategy="verified_context_staged", context_path=selected["context_path"])
    try:
        if selected:
            with inputs.additional(
                declared, path.parent, artifact / f"repair-inputs-{attempt}.json"
            ):
                yield selected, decision
        else:
            yield None, decision
    finally:
        write_once(receipt, inputs.encoded(decision))


# [impl->req~im-repair-context~1]
def prompt(selected, decision, original):
    current = runtime()
    if not selected:
        return original
    previous_runtime = selected["record"]["runtime"]
    if not all(current.values()) or current != previous_runtime:
        decision.update(strategy="fresh_context", reason="runtime_identity_unknown_or_changed")
        return original
    record, paths = selected["record"], selected["paths"]
    decision.update(strategy="verified_bounded_context", reason=None)
    result = (
        "Continue this task in a fresh native conversation. First read the complete authoritative "
        "contract at "
        + paths["approved-request.md"]
        + ". Read the source-bound repair record at "
        + selected["context_path"]
        + ". Prior implementation summaries are reported work, not proof. "
        "Preserve current code and fix only the recorded in-scope failure. Do not repeat unrelated "
        "repository discovery or carry stale acceptance forward. Current tests and independent review still run.\n"
        + "Verified prior evidence paths: "
        + json.dumps(paths)
        + "\n"
        + record["remaining_action"]
        + "\nFailure excerpt:\n"
        + record["failure_excerpt"]
    )
    decision.update(
        original_prompt_bytes=len(original.encode()),
        repair_prompt_bytes=len(result.encode()),
        first_useful_edit_seconds=None,
    )
    return result
