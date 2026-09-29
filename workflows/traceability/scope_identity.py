"""Keep source bytes, exported bytes and canonical policy identities distinct."""

import base64
import hashlib
import json
import re
import subprocess
from pathlib import PurePosixPath


def sha(data):
    return hashlib.sha256(data).hexdigest()


# [impl->req~im-scope-identity~1]
def capture(path, reference):
    from intentbond.common import CheckError

    try:
        data = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise CheckError(f"Cannot read scope {reference}: {exc}") from exc
    return capture_bytes(data, reference)


def capture_bytes(data, reference):
    from intentbond.common import CheckError, parse_json
    from intentbond.config import validate_scope

    try:
        scope = validate_scope(parse_json(data))
    except ValueError as exc:
        raise CheckError(f"Cannot read scope {reference}: {exc}") from exc
    return scope, {
        "reference": reference,
        "size": len(data),
        "sha256": sha(data),
        "bytes_base64": base64.b64encode(data).decode("ascii"),
    }


# [impl->req~im-repository-policy~1]
def capture_git(catalog, declaration):
    """Only an operator-pinned commit can supply policy, never the candidate tip."""
    if not isinstance(declaration, dict) or set(declaration) != {"revision", "path"}:
        raise ValueError("traceability_scope_git needs revision and path")
    revision, path = declaration["revision"], declaration["path"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("traceability_scope_git.revision must be a full commit ID")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or any(part in {"", ".", "..", ".git"} for part in path.split("/"))
        or str(PurePosixPath(path)) != path
    ):
        raise ValueError("traceability_scope_git.path must be a repository-relative file")
    prefix = ["git", "-c", "safe.directory=" + str(catalog), "-C", str(catalog)]

    def read(*args):
        return subprocess.run([*prefix, *args], check=True, capture_output=True, timeout=30).stdout

    if read("cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("Scope revision must identify a commit")
    entry = read("ls-tree", revision, "--", path).decode().split("\t", 1)[0].split()
    if len(entry) != 3 or entry[0] not in {"100644", "100755"} or entry[1] != "blob":
        raise ValueError("Repository scope must be a regular Git file")
    if int(read("cat-file", "-s", entry[2])) > 1024 * 1024:
        raise ValueError("Repository scope exceeds 1 MiB")
    return capture_bytes(read("cat-file", "blob", entry[2]), f"git:{revision}:{path}")


# [impl->req~im-scope-identity~1]
def export(config):
    from intentbond.common import canonical, parse_json
    from intentbond.config import validate_scope

    scope = validate_scope(config["traceability_scope"])
    source = config.get("traceability_scope_source")
    if source is not None:
        data = base64.b64decode(source["bytes_base64"], validate=True)
        if len(data) != source["size"] or sha(data) != source["sha256"]:
            raise ValueError("Captured scope source bytes do not match their identity")
        if canonical(validate_scope(parse_json(data))) != canonical(scope):
            raise ValueError("Captured scope source differs from controller-selected policy")
        source_identity = {key: source[key] for key in ("reference", "size", "sha256")}
    else:
        # Older uploaded tasks and programmatic callers did not capture source bytes.
        # Do not invent the identity of a source we never observed.
        data = (json.dumps(scope, indent=2) + "\n").encode()
        source_identity = None
    identity = {
        "schema_version": 1,
        "source_file": source_identity,
        "worker_file": {"size": len(data), "sha256": sha(data)},
        "canonical_policy": {
            "sha256": sha(canonical(scope)),
            "encoding": "Sorted compact ASCII JSON, no newline; array order preserved",
        },
        "export": "exact_source_bytes" if source is not None else "legacy_value_serialization",
    }
    return data, identity
