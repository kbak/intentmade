"""Adapt controller-validated checks and existing review to portable assurance."""

from pathlib import Path


# [impl->req~im-portable-assurance~1]
def record(configs, states, review, report_path):
    from intentbond.assurance import assess, observation, write_report
    from intentbond.assurance_contract import context, policy
    from intentbond.common import canonical, digest, read_json

    selected = [config["project"] for config in configs if "traceability_scope" in config]
    repositories, artifacts, references = {}, [], {}
    # Source matching and current invocation success have already been checked by
    # collect(). These controller-owned records avoid another clone or test run.
    for name in selected:
        state = states[name]
        receipt = state["traceability"]
        if receipt.get("matched_commit") != state.get("commit") or receipt.get(
            "check_exit_code"
        ) not in {0, 4}:
            raise ValueError("Portable assurance requires the successful current controller check")
        expected = review.traceability_context.get(name, {})
        repositories[name] = {
            "base": receipt["base"],
            "candidate": receipt["verified_candidate"],
            "scope_sha256": digest(canonical(receipt["scope"])),
            "evidence_sha256": receipt["evidence_sha256"],
            "changed_paths": expected.get("changed_paths", []),
            "requirement_index": expected.get("requirement_index", {}),
        }
        references[name] = [receipt["evidence"]]
        package = state.get("work_package")
        if package:
            manifest = {
                entry["path"]: entry
                for entry in read_json(Path(receipt["evidence"]).parent / "candidate-manifest.json")
            }
            for filename, role in (
                ("intent.md", "intent"),
                ("spec.md", "specification"),
                ("plan.md", "plan"),
            ):
                path = package["directory"] + "/" + filename
                entry = manifest.get(path)
                artifacts.append(
                    {
                        "id": name + ":" + filename,
                        "repository": name,
                        "role": role,
                        "path": path,
                        "sha256": entry["sha256"] if entry else None,
                        "accepted_sha256": package["sha256"][filename],
                    }
                )
    checking_policy = policy(
        profile="ai-native-sdlc", required=["execution", "traceability", "review", "intent"]
    )
    request = Path(report_path).parent / "approved-request.md"
    change = context(
        Path(report_path).stem,
        repositories,
        checking_policy,
        artifacts=artifacts,
        accepted_request={"reference": request.name, "sha256": digest(request.read_bytes())}
        if request.is_file()
        else None,
    )
    observations = {
        name: observation(
            change,
            name,
            {"source": "satisfied", "execution": "satisfied", "traceability": "satisfied"},
            references[name],
            producer="intentmade-controller",
        )
        for name in selected
    }
    portable_review = None
    if review.reviews:
        specialist = review.reviews[0]
        portable_review = {
            "schema_version": 1,
            "context_sha256": digest(canonical(change)),
            "producer": specialist.role,
            "verdict": {
                "PASS": "satisfied",
                "CHANGES_REQUESTED": "failed",
                "BLOCKED": "incomplete",
            }[review.verdict],
            "traceability_assessment": [
                item.model_dump() for item in specialist.traceability_assessment or []
            ],
            "intent_alignment": [
                item.model_dump()
                for item in specialist.intent_alignment
                if item.project in selected
            ],
        }
    report = assess(change, checking_policy, observations=observations, review=portable_review)
    output = Path(report_path).with_name(Path(report_path).stem.replace("review-", "assurance-"))
    write_report(output, report)
    for name in selected:
        states[name]["assurance"] = {
            "status": report["status"],
            "report": str(output / "assessment.json"),
            "context_sha256": report["context_sha256"],
            "commit": states[name]["commit"],
        }
    if review.verdict == "PASS" and report["status"] != "satisfied":
        raise ValueError("Portable assurance remains " + report["status"])
