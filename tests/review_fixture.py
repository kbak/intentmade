"""Scripted specialist results and controller evidence shared by review tests."""

import copy
import json

import review


def finding(**changes):
    return {
        "title": "Cross-account access",
        "category": "security",
        "severity": "high",
        "file": "app/routes.py",
        "line": 12,
        "evidence": "The account lookup uses the request ID without checking ownership.",
        "scenario": "A signed-in user supplies another account's ID.",
        "impact": "The response discloses that account's private records.",
        "remediation": "Check ownership before reading the records.",
        "introduced_or_worsened": True,
        "demonstrated_exploitability": True,
        "material_impact": True,
        **changes,
    }


def specialist(**changes):
    return {
        "verdict": "PASS",
        "summary": "Source reviewed.",
        "blocking_findings": [],
        "non_blocking_findings": [],
        "infrastructure_error": None,
        **changes,
    }


def evidence(code=None, security=None):
    # Assemble correctness and security fixtures into the single Alibaba result.
    result = copy.deepcopy(code or specialist())
    if security:
        for name in ("blocking_findings", "non_blocking_findings"):
            result[name].extend(security[name])
        if security["verdict"] != "PASS":
            result["verdict"] = security["verdict"]
        if security["infrastructure_error"]:
            result["infrastructure_error"] = security["infrastructure_error"]
    return {
        "kind": "ACPToolCallEvent",
        "title": "Factory specialist review",
        "status": "completed",
        "raw_input": {"version": 2, "threadId": "parent", "turnId": "turn"},
        "raw_output": {
            "agents": [
                {
                    "thread_id": "child-0",
                    "parent_thread_id": "parent",
                    "role": review.ROLES[0],
                    "status": "completed",
                    "message": json.dumps(result),
                }
            ]
        },
    }


EXPECTED = {
    "pilot": {
        "changed_paths": ["session.py"],
        "requirement_index": {
            "candidate": {"ids": ["req~session-expiration~1"]},
            "base": {"ids": ["req~session-expiration~0"]},
        },
    }
}


def change(status="covered", **updates):
    return {
        "changed_paths": ["session.py"],
        "behavior": "Session inactivity expiration",
        "status": status,
        "requirement_ids": ["req~session-expiration~1"],
        "documentation": ["requirements.md: Session expiration"],
        "implementation": ["session.py: expired and its impl reference"],
        "verification": ["tests/test_session.py: test_expiration checks both sides of 1800"],
        "rationale": "The existing requirement and boundary assertions support the changed threshold expression.",
        **updates,
    }


def assessed(changes=None, project="pilot"):
    return {
        "project": project,
        "summary": "Scripted assessment fixture.",
        "changes": [change()] if changes is None else changes,
    }
