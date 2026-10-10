"""Local task measurements; no service, model calls, or changes to acceptance policy."""

import argparse
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lifecycle import FAILURES, failure_status

CURRENT = ContextVar("factory_measurements", default=None)
TOKENS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def now():
    return datetime.now(UTC).isoformat()


def write(path, value):
    # The host show/report/note CLI uses only the standard library.
    from openhands.sdk.utils.files import atomic_write_text

    atomic_write_text(path, json.dumps(value, indent=2) + "\n", mode=0o644)


class Recorder:
    def __init__(self, directory, task, kind, repositories):
        self.path = Path(directory) / "metrics.json"
        self.started = time.monotonic()
        self.attempt_started = None
        self.data = {
            "schema_version": 1,
            "task": task,
            "kind": kind,
            "run_id": os.environ.get("AUTOMATION_RUN_ID"),
            "started_at": now(),
            "completed_at": None,
            "elapsed_seconds": None,
            "status": "RUNNING",
            "repositories": repositories,
            "attempts": [],
            "stages": [],
            "agents": [],
            "human_review_minutes": None,
            "onboarding_minutes": None,
            "escaped_violations": None,
            "incorrect_assurance_claims": None,
        }
        self.save()

    def save(self):
        try:
            write(self.path, self.data)
        except OSError as exc:
            # Measurement failures cannot change validation or publication policy.
            print(f"Factory metrics unavailable: {type(exc).__name__}", flush=True)

    def finish_attempt(self, status):
        if self.attempt_started is not None:
            self.data["attempts"][-1].update(
                status=status,
                completed_at=now(),
                elapsed_seconds=round(time.monotonic() - self.attempt_started, 3),
            )
            self.attempt_started = None
            self.save()


# [impl->req~im-measurement-outcomes~1]
@contextmanager
def task(directory, name, kind, repositories):
    recorder = Recorder(directory, name, kind, repositories)
    token = CURRENT.set(recorder)
    try:
        yield recorder
    except BaseException as exc:
        if recorder.data["status"] not in FAILURES:
            recorder.data["status"] = failure_status(exc)
        recorder.data["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            recorder.finish_attempt(recorder.data["status"])
            recorder.data.update(
                completed_at=now(), elapsed_seconds=round(time.monotonic() - recorder.started, 3)
            )
            recorder.save()
            recorder.path.with_name("metrics-summary.md").write_text(render(recorder.data) + "\n")
        except Exception as exc:
            print(f"Factory metrics summary unavailable: {type(exc).__name__}", flush=True)
        finally:
            CURRENT.reset(token)


def begin_attempt(index, reason):
    recorder = CURRENT.get()
    if recorder:
        recorder.data.pop("review", None)
        recorder.attempt_started = time.monotonic()
        recorder.data["attempts"].append(
            {
                "attempt": index + 1,
                "reason": reason,
                "started_at": now(),
                "completed_at": None,
                "elapsed_seconds": None,
                "status": "RUNNING",
            }
        )
        recorder.save()


def end_attempt(status):
    recorder = CURRENT.get()
    if recorder:
        recorder.finish_attempt(status)


@contextmanager
def stage(name, project=None):
    recorder = CURRENT.get()
    if recorder is None:
        yield
        return
    entry = {
        "name": name,
        "project": project,
        "attempt": len(recorder.data["attempts"]) or None,
        "started_at": now(),
        "completed_at": None,
        "elapsed_seconds": None,
        "status": "RUNNING",
    }
    recorder.data["stages"].append(entry)
    recorder.save()
    started = time.monotonic()
    try:
        yield
        entry["status"] = "COMPLETED"
    except BaseException as exc:
        entry.update(status="FAILED", error_type=type(exc).__name__)
        raise
    finally:
        entry.update(completed_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
        recorder.save()


# [impl->req~im-measurement-outcomes~1]
def update(outcome):
    recorder = CURRENT.get()
    if not recorder:
        return
    snapshot = {
        key: outcome[key] for key in ("status", "phase", "validation", "tests") if key in outcome
    }
    if outcome.get("response_failure"):
        snapshot["response_failure"] = outcome["response_failure"]
    snapshot["repositories"] = {
        project: {
            **{key: state[key] for key in ("base", "commit", "pull_request") if key in state},
            **(
                {
                    "traceability": {
                        key: state["traceability"][key]
                        for key in ("status", "evidence", "matched_commit", "review")
                        if key in state["traceability"]
                    }
                }
                if "traceability" in state
                else {}
            ),
        }
        for project, state in outcome.get("repositories", {}).items()
    }
    snapshot["browser_qa"] = {
        project: {
            key: result[key] for key in ("status", "commit", "accepted_gaps") if key in result
        }
        for project, result in outcome.get("browser_qa", {}).items()
    }
    # Copies preserve earlier attempts when the build mutates its shared state.
    snapshot = json.loads(json.dumps(snapshot))
    recorder.data.update(
        {key: snapshot[key] for key in ("status", "phase", "validation") if key in snapshot}
    )
    recorder.data["latest"] = snapshot
    if recorder.attempt_started is not None:
        recorder.data["attempts"][-1]["outcome"] = snapshot
    recorder.save()


def response_validation(details):
    recorder = CURRENT.get()
    if recorder:
        recorder.data.setdefault("response_protocol", []).append({"at": now(), **details})
        recorder.save()


def review(result, path):
    recorder = CURRENT.get()
    if not recorder:
        return
    value = {
        "verdict": result.verdict,
        "evidence": str(path),
        "blocking_findings": sum(len(item.blocking_findings) for item in result.reviews),
        "advisory_findings": sum(len(item.non_blocking_findings) for item in result.reviews),
        "traceability_assessments": [
            assessment.model_dump(mode="json")
            for item in result.reviews
            for assessment in (item.traceability_assessment or [])
        ],
    }
    recorder.data["review"] = value
    if recorder.attempt_started is not None:
        recorder.data["attempts"][-1]["review"] = value
    recorder.save()


def usage_snapshot(conversation):
    """Read only numeric usage fields, never persisted agent config or credentials."""
    try:
        value = conversation.conversation_stats.model_dump(mode="json")
        if not isinstance(value, dict):
            return None
        return value.get("usage_to_metrics", {})
    except Exception:
        return None


def usage_evidence(conversation):
    """Keep only the adapter's numeric accounting event, not arbitrary tool output."""
    try:
        events = [event.model_dump(mode="json") for event in conversation.state.events]
    except (AttributeError, TypeError):
        return []
    unique = {}
    for event in events:
        payload = event.get("raw_output")
        if (
            event.get("kind") == "ACPToolCallEvent"
            and event.get("title") == "Factory provider usage"
            and event.get("status") == "completed"
            and isinstance(payload, dict)
            and payload.get("version") == 1
            and isinstance(payload.get("id"), str)
        ):
            unique[payload["id"]] = payload
    return list(unique.values())


# [impl->req~im-measurement-outcomes~1]
def record_agent(conversation, before, started, skill, transcript, before_events=()):
    recorder = CURRENT.get()
    if not recorder:
        return
    after = usage_snapshot(conversation)
    entries = []
    for identity, metric in (after or {}).items():
        previous = (before or {}).get(identity, {})
        total = metric.get("accumulated_token_usage") or {}
        prior = previous.get("accumulated_token_usage") or {}
        delta = {}
        for key in TOKENS:
            current, old = total.get(key), prior.get(key, 0)
            delta[key] = (
                current - old
                if type(current) is int and type(old) is int and current >= old
                else None
            )
        cost, old_cost = metric.get("accumulated_cost"), previous.get("accumulated_cost", 0)
        cost_delta = (
            cost - old_cost
            if type(cost) in (int, float) and type(old_cost) in (int, float) and cost > old_cost
            else None
        )
        entries.append(
            {
                "usage_id": identity,
                "model": metric.get("model_name"),
                "tokens": delta,
                "reported_or_estimated_cost": cost_delta,
            }
        )
    provider = [e for e in usage_evidence(conversation) if e["id"] not in before_events]
    observed = []
    for event in provider:
        delta = event.get("delta") or {}
        observed.append(
            {
                "usage_id": event["id"],
                "model": None,
                "tokens": {
                    "prompt_tokens": delta.get("inputTokens"),
                    "completion_tokens": delta.get("outputTokens"),
                    "cache_read_tokens": delta.get("cachedInputTokens"),
                    "cache_write_tokens": None,
                    "reasoning_tokens": delta.get("reasoningOutputTokens"),
                },
                "reported_or_estimated_cost": None,
            }
        )
    recorder.data["agents"].append(
        {
            "conversation_id": str(conversation.id),
            "attempt": len(recorder.data["attempts"]) or None,
            "skill": skill,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "transcript": str(transcript) if transcript else None,
            "usage": observed
            if provider
            else entries
            if before is not None and after is not None
            else None,
            "sdk_usage": entries if before is not None and after is not None else None,
            "provider_usage": provider,
            "usage_semantics": "root_thread_cumulative_delta"
            if provider
            else "unverified_adapter_counters",
            "delegated_usage": None,
            "billed_cost": None,
        }
    )
    recorder.save()


def render(data):
    elapsed = data.get("elapsed_seconds")
    attempts = data.get("attempts", [])
    lines = ["**Factory measurements**"]
    if elapsed is not None:
        lines.append(
            f"Elapsed: {elapsed / 60:.1f} min; attempts: {len(attempts)}; repairs: {max(0, len(attempts) - 1)}."
        )
    else:
        lines.append("Measurement is incomplete; final elapsed time is unavailable.")
    tests = data.get("latest", {}).get("tests", {})
    lines.append(
        "Controller test exits: " + ", ".join(f"{p}={code}" for p, code in tests.items()) + "."
        if tests
        else "Controller tests: not recorded for this run."
    )
    reviewed = data.get("review")
    if reviewed:
        lines.append(
            f"Review: {reviewed['verdict']}; {reviewed['blocking_findings']} blocking and {reviewed['advisory_findings']} advisory findings reported."
        )
        changes = [c for a in reviewed["traceability_assessments"] for c in a["changes"]]
        if changes:
            missing = sum(c["status"] == "missing" for c in changes)
            uncertain = sum(c["status"] == "uncertain" for c in changes)
            ids = {identity for c in changes for identity in c["requirement_ids"]}
            lines.append(
                f"Traceability review: {len(ids)} requirement IDs cited; {missing} changes reported missing coverage; {uncertain} uncertain."
            )
        else:
            lines.append("Traceability review: not recorded.")
    else:
        lines.append("Independent review: not recorded for the current attempt.")
    qa = data.get("latest", {}).get("browser_qa", {})
    if qa:
        lines.append("Browser QA: " + ", ".join(f"{p}={r['status']}" for p, r in qa.items()) + ".")
    agents = data.get("agents", [])
    if agents:
        lines.append(
            f"Agent session time: {sum(a['elapsed_seconds'] for a in agents) / 60:.1f} min (summed; included in elapsed time)."
        )
        usage = [item for agent in agents for item in (agent.get("usage") or [])]
        measured = sum(bool(agent.get("usage")) for agent in agents)
        if usage:
            values = []
            for key, label in (
                ("prompt_tokens", "input"),
                ("completion_tokens", "output"),
                ("cache_read_tokens", "cache read"),
            ):
                counts = [item["tokens"].get(key) for item in usage]
                if all(value is not None for value in counts):
                    values.append(f"{sum(counts):,} {label}")
            lines.append(
                f"Observed provider tokens ({measured}/{len(agents)} sessions): "
                + (", ".join(values) or "incomplete")
                + ". Cache reads are included in input; reasoning is included in output. "
                "Cancelled prompts may be partial. Delegated coverage and billed spend are unknown. "
                "Records without provider evidence contain unverified adapter counters."
            )
    lines.append(
        "Human effort and later defects: unmeasured unless separately recorded. Usage details and evidence references are in the metrics record."
    )
    return "\n\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="Render a retained task measurement")
    show.add_argument("record", type=Path)
    note = sub.add_parser("note", help="Append an operator-reported effort or later outcome")
    note.add_argument("record", type=Path)
    note.add_argument(
        "--kind",
        required=True,
        choices=[
            "human_review",
            "onboarding",
            "escaped_violation",
            "incorrect_assurance",
            "false_positive",
        ],
    )
    note.add_argument(
        "--by", required=True, help="Attribution supplied by the operator; not authenticated"
    )
    note.add_argument("--minutes", type=float)
    note.add_argument("--reference", help="Supporting issue, review, or evidence location")
    note.add_argument("--note", required=True)
    report = sub.add_parser("report", help="Aggregate retained measurements without a database")
    report.add_argument("directory", type=Path)
    report.add_argument("--since", help="Inclusive UTC date, YYYY-MM-DD")
    args = parser.parse_args()
    if args.command == "report":
        if args.since:
            try:
                datetime.strptime(args.since, "%Y-%m-%d")
            except ValueError:
                parser.error("--since must be YYYY-MM-DD")
        records, observations = [], []
        for path in sorted(args.directory.glob("*/metrics.json")):
            data = json.loads(path.read_text())
            if data.get("schema_version") == 1 and (
                not args.since or data["started_at"][:10] >= args.since
            ):
                records.append(data)
                notes = path.with_name("observations.jsonl")
                if notes.exists():
                    observations.extend(json.loads(line) for line in notes.read_text().splitlines())
        print(
            json.dumps(
                {
                    "runs": len(records),
                    "completed_runs": sum(r["completed_at"] is not None for r in records),
                    "statuses": {
                        status: sum(r["status"] == status for r in records)
                        for status in sorted({r["status"] for r in records})
                    },
                    "attempts": sum(len(r["attempts"]) for r in records),
                    "within_run_repairs": sum(max(0, len(r["attempts"]) - 1) for r in records),
                    "maintenance_runs": sum(r["kind"] == "maintenance" for r in records),
                    "summed_run_seconds": sum(r["elapsed_seconds"] or 0 for r in records),
                    "operator_observations": {
                        "record_count": len(observations),
                        "reported_minutes": {
                            kind: sum(o["minutes"] for o in observations if o["kind"] == kind)
                            if any(o["kind"] == kind for o in observations)
                            else None
                            for kind in ("human_review", "onboarding")
                        },
                        "outcome_reports": {
                            kind: sum(o["kind"] == kind for o in observations)
                            for kind in (
                                "escaped_violation",
                                "incorrect_assurance",
                                "false_positive",
                            )
                        },
                    },
                    "limits": "Run durations may overlap. No defect-rate or causal improvement estimate. Human effort and later outcomes remain separate operator observations.",
                },
                indent=2,
            )
        )
        return
    data = json.loads(args.record.read_text())
    if data.get("schema_version") != 1:
        parser.error("Unsupported metrics schema")
    if args.command == "show":
        print(render(data))
        observations = args.record.with_name("observations.jsonl")
        if observations.exists():
            print("\nOperator-reported follow-ups (do not change the original assessment):")
            for line in observations.read_text().splitlines():
                record = json.loads(line)
                print(
                    f"- {record['kind']}: {record['note']} (minutes={record['minutes']}; reference={record['reference']}; by={record['by']})"
                )
        return
    effort = args.kind in {"human_review", "onboarding"}
    if effort and (args.minutes is None or not 0 <= args.minutes < float("inf")):
        parser.error("Effort needs finite nonnegative --minutes")
    if not effort and (not args.reference or args.minutes is not None):
        parser.error("Later outcomes need --reference and do not accept --minutes")
    record = {
        "id": str(uuid4()),
        "recorded_at": now(),
        "source": "operator_reported",
        "task": data["task"],
        "run_id": data["run_id"],
        "kind": args.kind,
        "by": args.by,
        "minutes": args.minutes,
        "reference": args.reference,
        "note": args.note,
    }
    with args.record.with_name("observations.jsonl").open("a") as handle:
        handle.write(json.dumps(record) + "\n")
    print(record["id"])


if __name__ == "__main__":
    main()
