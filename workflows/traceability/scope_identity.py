"""Keep source bytes, exported bytes and canonical policy identities distinct."""

import base64
import hashlib
import json


def sha(data):
    return hashlib.sha256(data).hexdigest()


def capture(path, reference):
    from versioned_traceability.common import CheckError, parse_json
    from versioned_traceability.config import validate_scope

    try:
        data = path.read_bytes()
        scope = validate_scope(parse_json(data))
    except (OSError, ValueError) as exc:
        raise CheckError(f"Cannot read scope {reference}: {exc}") from exc
    return scope, {
        "reference": reference,
        "size": len(data),
        "sha256": sha(data),
        "bytes_base64": base64.b64encode(data).decode("ascii"),
    }


def export(config):
    from versioned_traceability.common import canonical, parse_json
    from versioned_traceability.config import validate_scope

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
