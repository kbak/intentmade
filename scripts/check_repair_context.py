"""Matched scripted repairs: real Git/OFT/tests, no model or session-speed claims.

Run with workflows and tests on PYTHONPATH in the pinned traced test image.
"""

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import input_artifacts
import repair_context
import run
from test_traceability import TraceabilityPipelineTests

IDENTITY = {
    "image_id": "fixture-controlled-image",
    "workflow_sha256": "fixture-controlled-code",
    "model": "scripted-no-model",
}


def sample(strategy, retained):
    fixture = TraceabilityPipelineTests()
    fixture.setUp()
    fixture.config["repair_attempts"] = 1
    fixture.request += "\n" + ("Existing accepted context is unchanged.\n" * 3000)
    observed = {
        "strategy": strategy,
        "agent_kind": "scripted",
        "model_calls": 0,
        "provider_tokens": None,
        "repeated_repository_discovery": None,
    }
    original = run.implementation_attempt

    def implementation(*args, **kwargs):
        if args[-1] == 1:
            observed["start"] = time.monotonic()
        return original(*args, **kwargs)

    def edit(root, attempt):
        path = root / "session.py"
        if attempt == 2:
            prompt = fixture.attempts[-1]
            observed["active_prompt_bytes"] = len(prompt.encode())
            if strategy == "bounded":
                manifest = input_artifacts.CURRENT.get()
                candidates = [
                    Path(manifest["directory"]) / i["name"]
                    for i in manifest["inputs"]
                    if i["reference"] == "approved-request.md"
                ]
                assert len(candidates) == 1 and candidates[0].read_text() == fixture.request
            else:
                assert fixture.request in prompt
            observed["authoritative_contract_bytes_read"] = len(fixture.request.encode())
        path.write_text(
            path.read_text().replace("1800", "3600")
            if attempt == 1
            else path.read_text().replace("3600", "1800")
        )
        if attempt == 2:
            observed["first_scripted_useful_edit_seconds"] = time.monotonic() - observed["start"]

    try:
        from contextlib import nullcontext

        with (
            patch.object(run, "implementation_attempt", implementation),
            patch.object(repair_context, "runtime", return_value=IDENTITY),
            patch.object(input_artifacts, "DATA", fixture.root / "input-cache"),
            patch.object(
                repair_context, "previous", return_value=(None, "comparison_fresh_context")
            )
            if strategy == "fresh"
            else nullcontext(),
        ):
            result = fixture.invoke(edit)
        observed["scripted_repair_seconds"] = time.monotonic() - observed.pop("start")
        observed["status"] = result["status"]
        observed["independent_review_count"] = len(fixture.review_prompts)
        observed["attempts"] = len(fixture.attempts)
        assert (
            result["status"] == "PASSED"
            and observed["independent_review_count"] == 1
            and observed["attempts"] == 2
        )
        observed["selection"] = json.loads(
            (fixture.artifact / "continuation-selection-1.json").read_text()
        )["strategy"]
        retained.mkdir()
        shutil.copytree(fixture.artifact, retained / "artifacts")
        common = __import__("common")
        common.git(
            [
                "--git-dir",
                fixture.states["pilot"]["repository"],
                "bundle",
                "create",
                str(retained / "source.bundle"),
                "--all",
            ]
        )
        observed["base"] = fixture.states["pilot"]["base"]
        observed["candidate"] = result["repositories"]["pilot"]["commit"]
        observed["evidence_directory"] = retained.name + "/artifacts"
        observed["source_bundle_sha256"] = hashlib.sha256(
            (retained / "source.bundle").read_bytes()
        ).hexdigest()
        return observed
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with patch.dict(
        os.environ,
        {"GIT_AUTHOR_DATE": "2026-09-18T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-18T00:00:00Z"},
    ):
        order = ["fresh", "bounded", "bounded", "fresh", "fresh", "bounded"]
        samples = [
            sample(strategy, args.output.parent / f"sample-{index}-{strategy}")
            for index, strategy in enumerate(order)
        ]
    assert len({s["base"] for s in samples}) == len({s["candidate"] for s in samples}) == 1
    report = {
        "schema_version": 1,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "design": "Three paired, counterbalanced scripted repairs; same 30-minute boundary regression, full contract, Git/OFT checks and fresh review.",
        "limits": "No live LLM: these timings measure scripted workflow overhead, not agent productivity. Reading the complete contract is mandatory in both arms; prompt size reduction is not measured token/cost savings. Model latency, discovery repetitions, actual human review and billed cost remain unmeasured.",
        "samples": samples,
    }
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2)
    print("PASS: six matched scripted repairs; all current checks and fresh reviews passed")
