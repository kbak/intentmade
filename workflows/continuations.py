"""Controller-owned, explicit scope extensions in an existing task conversation."""

import datetime
import hashlib
import json

import authorization
import followup
import httpx
import reporting
from approval import microseconds
from common import api, github, lock, token
from lifecycle import NeedsInput
from openhands.sdk.utils.files import atomic_write_text

MAX_EVENTS = 10000
MAX_CONTEXT_BYTES = 256 * 1024
MAX_SCOPE_BYTES = 64 * 1024


def directory(config):
    return reporting.report_path(config, "unused").parent / "continuations"


def save(config, record):
    path = directory(config) / (record["id"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(record, indent=2) + "\n", mode=0o600)


def timestamp(value):
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


def discussion(conversation):
    """Read conversational text only; never forward tool output or private reasoning."""
    page, count, messages = None, 0, []
    while True:
        try:
            data = api(
                "GET",
                f"/api/conversations/{conversation}/events/search",
                params={"limit": 100, **({"page_id": page} if page else {})},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            # A deleted historical report has no new approval. Discard partial
            # history too; preserve its task/publication receipts for recovery.
            return []
        count += len(data["items"])
        if count > MAX_EVENTS:
            raise ValueError("Conversation exceeds the follow-up context limit; use a new task")
        for event in data["items"]:
            message = event.get("llm_message") or {}
            action = event.get("action") or {}
            if (
                event.get("source") == "user"
                and event.get("kind") == "MessageEvent"
                and message.get("role") == "user"
            ):
                role = "user"
                text = "\n".join(c.get("text", "") for c in message.get("content", []))
                if text.startswith("Factory update\n"):
                    continue
            elif event.get("source") == "agent" and action.get("kind") in {
                "FinishAction",
                "MessageAction",
            }:
                role, text = "assistant", action.get("message", "")
            else:
                continue
            if isinstance(text, str) and text.strip():
                messages.append(
                    {"id": event["id"], "timestamp": event["timestamp"], "role": role, "text": text}
                )
        page = data.get("next_page_id")
        if not page:
            return sorted(messages, key=lambda item: timestamp(item["timestamp"]))


def tracked(config, task, report):
    """A publication receipt binds a report to this factory's existing PR."""
    for path in followup.directory(config).glob("*.json"):
        record = json.loads(path.read_text())
        if task not in {record["task"], f"pr-{record['number']}"}:
            continue
        if task == record["task"]:
            state = report.get("result", {}).get("repositories", {}).get(config["project"], {})
            if (
                state.get("pull_request")
                != f"https://github.com/{config['repository']}/pull/{record['number']}"
            ):
                continue
        return record
    return None


# [impl->req~im-thread-follow-up~1]
def capture(config, task, report, credential=None):
    retained = tracked(config, task, report)
    if not retained or not report.get("conversation_id"):
        return None
    messages = discussion(report["conversation_id"])
    pending = None
    for index, message in enumerate(messages):
        if message["role"] != "user" or not message["text"].lower().startswith("implement:"):
            continue
        identity = hashlib.sha256(
            (report["conversation_id"] + ":" + message["id"]).encode()
        ).hexdigest()
        path = directory(config) / (identity + ".json")
        if path.exists():
            previous = json.loads(path.read_text())
            if previous["status"] == "QUEUED":
                pending = pending or previous
            continue
        subject = {"task": task, "conversation": report["conversation_id"], "event": message["id"]}
        scope = message["text"][10:].strip()
        if not authorization.permit(
            config,
            "resume:answer",
            subject,
            {
                "event_at": microseconds(timestamp(message["timestamp"])),
                "question_at": microseconds(
                    timestamp(report.get("continuation_after", report["updated_at"]))
                ),
                "explicit": True,
                "answer_present": bool(scope),
            },
        ):
            continue
        if len(scope.encode()) > MAX_SCOPE_BYTES:
            raise ValueError("Follow-up scope exceeds 64 KiB; use a new task")
        pr = github(
            credential if credential is not None else token(),
            "GET",
            f"/repos/{config['repository']}/pulls/{retained['number']}",
        )
        allowed = authorization.permit(
            config,
            "continuation:accept",
            subject,
            {
                "state": pr["state"],
                "head_matches": pr["head"]["sha"] == retained["head"],
                "branch_matches": pr["head"]["ref"] == retained["branch"],
                "base_matches": pr["base"]["ref"] == retained["base_branch"] == config["branch"],
                "repository_matches": (pr["head"].get("repo") or {}).get("full_name", "").casefold()
                == config["repository"].casefold(),
            },
        )
        context = messages[: index + 1]
        omitted = 0
        while len(json.dumps(context).encode()) > MAX_CONTEXT_BYTES and len(context) > 1:
            context.pop(0)
            omitted += 1
        if len(json.dumps(context).encode()) > MAX_CONTEXT_BYTES:
            raise ValueError("Follow-up context exceeds the retained size limit; use a new task")
        package = {
            "task": retained["task"],
            "report_task": task,
            "snapshot": report.get("snapshot"),
            "conversation_id": report["conversation_id"],
            "event_id": message["id"],
            "approved_at": message["timestamp"],
            "scope": scope,
            "discussion": context,
            "omitted_messages": omitted,
            "previous_run_id": report.get("run_id"),
            "previous_metrics": report.get("metrics"),
            "number": retained["number"],
            "head": pr["head"]["sha"],
            "branch": pr["head"]["ref"],
            "base": pr["base"]["sha"],
            "base_branch": pr["base"]["ref"],
        }
        record = {
            "id": identity,
            "package": package,
            "sha256": digest(package),
            "status": "QUEUED" if allowed else "REJECTED",
        }
        save(config, record)
        if allowed:
            pending = record
        else:
            reporting.post(
                report["conversation_id"],
                "Follow-up was not queued: the PR is closed or its current identity differs from the factory's retained publication. Inspect the current PR before approving a new scope.",
            )
    return pending


def digest(package):
    return hashlib.sha256(json.dumps(package, sort_keys=True).encode()).hexdigest()


# [impl->req~im-thread-follow-up~1]
def process(
    config,
    credential,
    limit,
    on_start=lambda: None,
    *,
    enqueue=None,
    selected=None,
    answer_id=None,
    package_sha256=None,
):
    """Capture under the scan lock, then execute each approved task in its own run."""
    paths = [
        path
        for path in reporting.report_path(config, "unused").parent.glob("*.json")
        if tracked(config, path.stem, json.loads(path.read_text())) is not None
    ]
    if not paths and not directory(config).exists():
        return 0, 0
    with lock(config["project"] + ".reply-queue", blocking=True):
        for path in paths:
            report = json.loads(path.read_text())
            capture(config, path.stem, report, credential)
    count, waiting = 0, 0
    records = [json.loads(path.read_text()) for path in directory(config).glob("*.json")]
    for record in sorted(records, key=lambda item: timestamp(item["package"]["approved_at"])):
        if count >= limit:
            break
        if selected is not None and record["id"] != selected:
            continue
        package = record["package"]
        if package_sha256 is not None and record["sha256"] != package_sha256:
            raise ValueError("Queued follow-up identity changed; no work started")
        if digest(package) != record["sha256"]:
            raise ValueError("Retained follow-up scope changed; no work started")
        if record["status"] not in {"QUEUED", "STARTED", "FAILED", "NEEDS_INPUT"}:
            continue
        current_report = reporting.read_report(config, package["report_task"])
        if (
            record["status"] in {"FAILED", "NEEDS_INPUT"}
            and (current_report or {}).get("continuation_id") != record["id"]
        ):
            continue
        if (
            not current_report
            or current_report.get("conversation_id") != package["conversation_id"]
            or current_report.get("snapshot") != package["snapshot"]
        ):
            raise ValueError("Follow-up conversation or snapshot changed; no work started")
        if enqueue is not None:
            reply = (
                reporting.resume_reply(config, package["report_task"])
                if record["status"] != "QUEUED"
                else None
            )
            fresh = reply and reply["id"] != record.get("answer_id")
            if record["status"] in {"FAILED", "NEEDS_INPUT"} and not fresh:
                continue
            if enqueue(
                config,
                {
                    "kind": "continuation",
                    "key": record["id"],
                    "package_sha256": record["sha256"],
                    "answer_id": reply["id"] if fresh else record.get("answer_id"),
                },
                on_start=on_start,
            ):
                count += 1
            continue
        if selected is not None and answer_id is None and record["status"] != "QUEUED":
            raise ValueError("Queued follow-up already advanced; a fresh answer is required")
        report = reporting.TaskReport(config, package["report_task"], package["snapshot"])
        if record["status"] == "STARTED":
            record["status"] = "FAILED"
            save(config, record)
            report.update(
                "FAILED",
                "Follow-up was interrupted; retained work is preserved. Inspect it before replying `resume: retry`.",
                continuation_id=record["id"],
            )
        reply = None
        if record["status"] in {"FAILED", "NEEDS_INPUT"}:
            reply = reporting.resume_reply(config, package["report_task"])
            if not reply or reply["id"] == record.get("answer_id"):
                continue
        elif record["status"] != "QUEUED":
            continue
        if answer_id is not None and (reply or {}).get("id") != answer_id:
            raise ValueError("The queued follow-up answer changed; no work started")
        record.update(status="STARTED", answer_id=(reply or {}).get("id"))
        save(config, record)
        count += 1
        on_start()
        action = {
            **{key: package[key] for key in ("number", "head", "branch", "base")},
            "key": record["id"],
            "failures": {},
            "feedback": [],
            "behind": False,
            "title": None,
            "continuation": record,
            "reply": reply,
        }
        try:
            result = followup.maintain(config, {"number": package["number"]}, action, credential)
            record["status"] = "COMPLETED" if result else "NEEDS_INPUT"
            waiting += not result
        except NeedsInput as exc:
            record["status"] = "NEEDS_INPUT"
            waiting += 1
            report.update("NEEDS_INPUT", str(exc), continuation_id=record["id"])
        except Exception as exc:
            record["status"] = "FAILED"
            report.update(
                "FAILED",
                f"Follow-up stopped: {exc}. Retained work is preserved; inspect the failure before retrying.",
                continuation_id=record["id"],
            )
            raise
        finally:
            save(config, record)
    return count, waiting
