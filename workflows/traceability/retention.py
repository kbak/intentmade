"""Retain bounded portable bundles after worker teardown, before job deletion."""

import hashlib
import json
import os
import re
import stat

import measurements

BUNDLE_NAMES = {
    "evidence.json",
    "scope.json",
    "base-manifest.json",
    "candidate-manifest.json",
    "review.json",
    "review.patch",
    "tests.log",
    "tests.xml",
    "test-result.json",
    "summary.md",
    *{
        f"{label}-{name}"
        for label in ("base", "candidate")
        for name in ("items.xml", "tags.xml", "import.log", "tags-import.log", "trace.log")
    },
}
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_INVOCATIONS = 200


def directory(path, root):
    if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(root):
        raise RuntimeError("Traceability retention directory is missing or unsafe")
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError("Traceability retention refuses linked parent directories")


# [impl->req~im-trace-retention~1]
def copy_file(source, target, budget):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
            raise RuntimeError("Traceability retention requires bounded regular files")
        contents = handle.read(MAX_FILE_BYTES + 1)
    budget[0] += len(contents)
    if len(contents) > MAX_FILE_BYTES or budget[0] > MAX_TOTAL_BYTES:
        raise RuntimeError("Traceability retention size limit exceeded; workspace preserved")
    target.write_bytes(contents)
    return {"sha256": hashlib.sha256(contents).hexdigest(), "bytes": len(contents)}


# [impl->req~im-trace-retention~1]
def retain(paths, attempt):
    root = paths["root"].resolve()
    source = paths["scratch"].parent
    directory(source, root)
    target = paths["retained"].with_name(f"traceability-invocations-{attempt}")
    target.mkdir()
    index = {
        "schema_version": 1,
        "controller_attempt": attempt + 1,
        "external_continuations": None,
        "logical_qualification_cases": None,
        "invocations": [],
        "limits": "Worker claims are retained observations, not controller acceptance.",
    }
    budget = [0]
    candidates = sorted(
        p for p in source.iterdir() if p.name.startswith(("agent-check-", "controller-check-"))
    )
    if len(candidates) > MAX_INVOCATIONS:
        raise RuntimeError(
            "Traceability invocation count exceeds retention limit; workspace preserved"
        )
    for candidate in candidates:
        directory(candidate, root)
        destination = target / candidate.name
        destination.mkdir()
        manifest = {}
        for item in sorted(candidate.iterdir()):
            if item.name == "evidence":
                directory(item, root)
                (destination / "evidence").mkdir()
                for artifact in sorted(item.iterdir()):
                    if artifact.name not in BUNDLE_NAMES and not re.fullmatch(
                        r"tests-\d+\.xml", artifact.name
                    ):
                        raise RuntimeError(
                            f"Unknown evidence artifact {artifact.name!r}; workspace preserved"
                        )
                    relative = "evidence/" + artifact.name
                    manifest[relative] = copy_file(artifact, destination / relative, budget)
            elif item.name in {"invocation.json", "checker.log"}:
                manifest[item.name] = copy_file(item, destination / item.name, budget)
            else:
                raise RuntimeError(
                    f"Unexpected invocation artifact {item.name!r}; workspace preserved"
                )
        receipt = destination / "invocation.json"
        record = (
            json.loads(receipt.read_text())
            if receipt.exists()
            else {
                "id": candidate.name,
                "phase": "unregistered",
                "started_at": None,
                "completed_at": None,
                "exit_code": None,
                "command": None,
                "status": "incomplete",
            }
        )
        evidence = destination / "evidence/evidence.json"
        # Invalid/incomplete bundles still remain intact and indexed.
        try:
            predicate = json.loads(evidence.read_text())["predicate"]
            details = {
                key: predicate.get(key)
                for key in ("status", "base", "candidate", "scope", "diagnostics")
            }
        except (OSError, ValueError, KeyError, TypeError):
            details = {
                "status": "incomplete",
                "diagnostics": ["No readable portable evidence statement"],
            }
        entry = {**record, "bundle": details, "directory": str(destination), "artifacts": manifest}
        index["invocations"].append(entry)
        (destination / "retention-manifest.json").write_text(json.dumps(entry, indent=2) + "\n")
    index["feedback_iterations"] = sum(i.get("phase") == "feedback" for i in index["invocations"])
    index["controller_checks"] = sum(i.get("phase") == "controller" for i in index["invocations"])
    (target / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    recorder = measurements.CURRENT.get()
    if recorder:
        recorder.data.setdefault("traceability_checks", []).append(
            {
                "index": str(target / "index.json"),
                "controller_attempt": attempt + 1,
                "feedback_iterations": index["feedback_iterations"],
                "controller_checks": index["controller_checks"],
                "external_continuations": None,
                "logical_qualification_cases": None,
            }
        )
        recorder.save()
    return index
