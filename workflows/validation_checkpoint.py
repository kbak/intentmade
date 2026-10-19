"""Reuse controller validation after an explicitly retried, incomplete review."""

import copy
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import deployment
import input_artifacts as inputs
import provenance
import repair_context
import traceability
from common import git

POINTER = "factory-validation-checkpoint.json"
MAX_RECORD = 1024 * 1024


def responses(answers):
    """A bare retry is control input, not an amendment to the approved request."""
    return [answer for answer in answers if answer.strip().casefold() != "retry"]


def is_retry(answer):
    return isinstance(answer, str) and answer.strip().casefold() == "retry"


def tree_identity(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Validation profile or runtime directory unavailable")
    entries = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > inputs.MAX_FILE:
            raise ValueError("Validation inputs must be bounded regular files")
        entries[str(path.relative_to(root))] = {
            "sha256": inputs.digest(path.read_bytes()),
            "mode": stat.S_IMODE(info.st_mode),
        }
    return inputs.digest(inputs.encoded(entries))


def environment(configs):
    """Observe validation inputs without starting another implementation worker."""
    options = deployment.settings()["worker_runtime"]
    kit = None
    image = os.environ.get("FACTORY_IMAGE", "intentmade:dev")
    profiles = Path("/profiles")
    if options["backend"] == "docker-sandboxes":
        import yaml

        kit = tree_identity(options["kit"])
        image = yaml.safe_load((Path(options["kit"]) / "spec.yaml").read_text())["sandbox"]["image"]
        profiles = Path(options.get("profiles", "/profiles"))
        # Native controllers have no Docker socket. Preserve the catalog's
        # actual identity (currently abbreviated), without claiming a full digest.
        result = subprocess.run(
            [options.get("command", "sbx"), "template", "ls", "--json"],
            env={**os.environ, "SBX_NO_TELEMETRY": "1"},
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        image_identity = native_image(json.loads(result.stdout), image)
    else:
        image_id = provenance.docker_json(["image", "inspect", image], "{{json .Id}}")
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise ValueError("Validation image identity unavailable")
        image_identity = {"kind": "docker_image_id", "id": image_id}
    return {
        "controller": provenance.runtime(),
        "runtime": options,
        "kit_sha256": kit,
        "image": image_identity,
        "profiles": {
            name: tree_identity(profiles / name)
            for name in sorted({c["test_profile"] for c in configs if c.get("test_profile")})
        },
    }


def native_image(catalog, image):
    # Resolve common Docker reference forms against the complete native catalog.
    # Unsupported references disable reuse rather than guessing an image ID.
    if not isinstance(image, str) or "@" in image or image.startswith("sha256:"):
        raise ValueError("Native image reference cannot be resolved from the catalog")
    if ":" not in image.rsplit("/", 1)[-1]:
        image += ":latest"
    if "/" not in image:
        image = "docker.io/library/" + image
    elif not any(mark in image.split("/", 1)[0] for mark in (".", ":")) and not image.startswith(
        "localhost/"
    ):
        image = "docker.io/" + image
    if image.startswith("docker.io/") and image.count("/") == 1:
        image = image.replace("docker.io/", "docker.io/library/", 1)
    matches = [
        item
        for item in catalog["images"]
        if item.get("repository", "") + ":" + item.get("tag", "") == image
    ]
    if len(matches) != 1:
        raise ValueError("Native validation image is missing or ambiguous")
    item = matches[0]
    if (
        not re.fullmatch(r"[a-f0-9]{12,64}", item.get("id", ""))
        or not isinstance(item.get("created_at"), str)
        or type(item.get("size")) is not int
    ):
        raise ValueError("Native validation image identity is incomplete")
    return {
        "kind": "native_catalog",
        **{key: item[key] for key in ("repository", "tag", "id", "created_at", "size")},
    }


def observe(configs):
    try:
        return environment(configs)
    except Exception:
        # Unknown environment disables reuse, never the ordinary validation path.
        return None


def binding(configs, states, task, request):
    identity = repair_context.binding(configs, states, task, request)
    for config in configs:
        project = config["project"]
        policy = {key: value for key, value in config.items() if key != "repair_pr"}
        action = config.get("repair_pr")
        if action:
            policy["maintenance"] = {
                key: action.get(key)
                for key in ("number", "head", "base", "feedback", "failures", "continuation")
            }
        identity["repositories"][project].update(
            branch=states[project]["branch"],
            policy_sha256=inputs.digest(inputs.encoded(policy)),
            work_package=states[project].get("work_package"),
            formatting_amendment=states[project].get("formatting_amendment"),
        )
    return identity


def clear(states):
    for state in states.values():
        (Path(state["repository"]) / POINTER).unlink(missing_ok=True)


def file_record(path, root):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > inputs.MAX_FILE:
        raise ValueError("Checkpoint evidence must be bounded regular files")
    return {
        "reference": path.relative_to(root).as_posix(),
        "size": info.st_size,
        "sha256": inputs.digest(path.read_bytes()),
    }


# [impl->req~im-review-validation-reuse~1]
def retain(configs, states, task, request, artifact, attempt, results, output, observed):
    """Called only after validation succeeds and independent review is incomplete."""
    if (
        not states
        or set(results) != set(states)
        or any(type(code) is not int or code != 0 for code in results.values())
        or not traceability.checks_passed(configs, states)
        or observed is None
        or observe(configs) != observed
    ):
        return
    files, saved = [], {}
    for project, state in states.items():
        saved[project] = {
            key: copy.deepcopy(state[key])
            for key in ("commit", "summary", "change_summary", "title", "traceability")
            if key in state
        }
        source = artifact / project
        candidates = [source / f"tests-{attempt}.log", source / f"test-command-{attempt}.json"]
        if state.get("traceability"):
            check = saved[project]["traceability"]
            check.pop("independent_review", None)
            evidence = Path(check["evidence"])
            if evidence.parent != source / f"traceability-{attempt}":
                raise ValueError("Checkpoint evidence is outside this controller attempt")
            candidates.extend(evidence.parent.iterdir())
            candidates.append(source / "traceability-scope.json")
        files.extend(file_record(path, artifact) for path in candidates)
    if len(files) > 2048 or sum(item["size"] for item in files) > inputs.MAX_TOTAL:
        raise ValueError("Checkpoint evidence exceeds retention limits")
    record = {
        "schema_version": 1,
        "binding": binding(configs, states, task, request),
        "environment": observed,
        "attempt": attempt,
        "states": saved,
        "tests": results,
        "test_output": output,
        "files": files,
        "reason": "independent_review_incomplete",
    }
    raw = inputs.encoded(record)
    if len(raw) > MAX_RECORD:
        raise ValueError("Validation checkpoint exceeds record limit")
    path = artifact / f"validation-checkpoint-{attempt}.json"
    repair_context.write_once(path, raw)
    pointer = inputs.encoded({"path": str(path), "size": len(raw), "sha256": inputs.digest(raw)})
    for state in states.values():
        destination = Path(state["repository"]) / POINTER
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(pointer)
        temporary.replace(destination)


def previous(configs, states, task, request, observed):
    pointers = [Path(state["repository"]) / POINTER for state in states.values()]
    if not pointers or not all(path.is_file() for path in pointers):
        return None, "No retained validation checkpoint"
    try:
        if observed is None:
            raise ValueError("Current validation environment is unknown")
        raw = pointers[0].read_bytes()
        if any(path.read_bytes() != raw for path in pointers[1:]):
            raise ValueError("Group checkpoint pointers disagree")
        pointer = json.loads(raw)
        path = Path(pointer["path"])
        if pointer["size"] > MAX_RECORD:
            raise ValueError("Checkpoint record exceeds limit")
        inputs.copy_verified(path.parent, path.name, {**pointer, "name": path.name})
        record = json.loads(path.read_bytes())
        if (
            record["schema_version"] != 1
            or record["reason"] != "independent_review_incomplete"
            or record["binding"] != binding(configs, states, task, request)
            or record["environment"] != observed
        ):
            raise ValueError("Source, request, policy, inputs or validation environment changed")
        if set(record["tests"]) != set(states) or any(
            type(code) is not int or code != 0 for code in record["tests"].values()
        ):
            raise ValueError("Checkpoint does not contain successful controller tests")
        for item in record["files"]:
            inputs.validate([{**item, "name": "evidence", "producer": "controller"}])
            inputs.copy_verified(
                path.parent, item["reference"], {**item, "name": item["reference"]}
            )
        restored = {
            project: {**state, **record["states"][project]} for project, state in states.items()
        }
        if not traceability.checks_passed(configs, restored):
            raise ValueError("Checkpoint traceability does not match the candidate")
        for config in configs:
            project = config["project"]
            state = restored[project]
            if state["commit"] != record["binding"]["repositories"][project]["current_commit"]:
                raise ValueError("Checkpoint candidate does not match retained source")
            if "traceability_scope" in config:
                from intentbond.common import CheckError
                from intentbond.runner import verify

                with tempfile.TemporaryDirectory(prefix="factory-checkpoint-") as directory:
                    repo = Path(directory) / "source"
                    git(["clone", "--no-checkout", state["repository"], str(repo)])
                    try:
                        verify(
                            repo,
                            path.parent / project / "traceability-scope.json",
                            state["base"],
                            state["commit"],
                            Path(state["traceability"]["evidence"]),
                            allow_pending_review=True,
                        )
                    except CheckError as exc:
                        raise ValueError("IntentBond evidence no longer matches") from exc
        return (path, record), None
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return None, "Checkpoint rejected: " + str(exc)


# [impl->req~im-review-validation-reuse~1]
def restore(configs, states, task, request, artifact, observed):
    found, reason = previous(configs, states, task, request, observed)
    receipt = {"strategy": "fresh_validation", "reason": reason}
    if found:
        path, record = found
        old_attempt = record["attempt"]
        for item in record["files"]:
            relative = Path(item["reference"])
            parts = list(relative.parts)
            parts[1] = parts[1].replace(f"-{old_attempt}.", "-0.")
            if parts[1] == f"traceability-{old_attempt}":
                parts[1] = "traceability-0"
            destination = artifact.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            inputs.copy_verified(
                path.parent, item["reference"], {**item, "name": relative.name}, destination
            )
        for project, saved in record["states"].items():
            states[project].update(copy.deepcopy(saved))
            if "traceability" in saved:
                states[project]["traceability"]["evidence"] = str(
                    artifact / project / "traceability-0/evidence.json"
                )
        receipt = {"strategy": "reuse_validation", "checkpoint": str(path), "reason": None}
    repair_context.write_once(artifact / "validation-selection.json", inputs.encoded(receipt))
    return record if found else None
