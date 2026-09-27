"""Persistent Canvas task reports, human replies, and truthful run callbacks."""

import base64
import datetime
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

import httpx
import measurements
from common import DATA, api, identifier, session_api_key
from openhands.sdk.agent import ACPAgent


class NeedsInput(RuntimeError):
    pass


ACTIVE = None
_RUN_KEY = None


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def report_path(config, task):
    repository = config["repository"].casefold()
    return (
        DATA
        / "reports"
        / identifier(config["project"])
        / hashlib.sha256(repository.encode()).hexdigest()
        / (identifier(task) + ".json")
    )


def read_report(config, task):
    path = report_path(config, task)
    return json.loads(path.read_text()) if path.exists() else None


def write_report(config, task, record):
    path = report_path(config, task)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, indent=2))
    temp.replace(path)


def post(conversation_id, message, run=False, images=None):
    api(
        "POST",
        f"/api/conversations/{conversation_id}/events",
        json={
            # The native message endpoint accepts user input only. Identify
            # generated reports explicitly; never impersonate an agent turn.
            "role": "user",
            "content": [{"type": "text", "text": "Factory update\n\n" + message}]
            + ([{"type": "image", "image_urls": images}] if images else []),
            "run": run,
        },
    )


def browser_evidence(results):
    """Native image attachments survive worker deletion and need no public hosting."""
    if not ACTIVE or not ACTIVE.get("conversation_id"):
        return
    for project, result in results.items():
        text = f"**Browser QA — {project}: {result['status']}**\n\nCommit: `{result['commit']}`\n\n{result['summary']}"
        if result.get("accepted_gaps"):
            from browser_qa import limitations

            text += limitations(result)
        for check in result.get("checks", []):
            text += f"\n\n- {check['name']}: {check['status']} — {check['observed']}"
        try:
            post(ACTIVE["conversation_id"], text)
            for screenshot in result.get("screenshots", []):
                path = Path(screenshot["path"])
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != screenshot["sha256"]:
                    raise RuntimeError("Retained screenshot changed before attachment")
                post(
                    ACTIVE["conversation_id"],
                    screenshot["caption"] + "\n\nRetained file: `" + str(path) + "`",
                    images=["data:image/png;base64," + base64.b64encode(data).decode()],
                )
            result["report_attachment"] = "ATTACHED"
        except Exception as exc:
            result["report_attachment"] = "FAILED"
            print(
                f"Canvas evidence attachment unavailable; files retained: {type(exc).__name__}",
                flush=True,
            )


def phase(message):
    print(message, flush=True)
    if not ACTIVE:
        return
    try:
        api(
            "POST",
            f"/api/automation/v1/runs/{ACTIVE['run_id']}/phase",
            json={
                "phase": " ".join(message.split())[:200],
                "conversation_id": ACTIVE.get("conversation_id"),
            },
            headers={"X-Session-API-Key": _RUN_KEY},
            timeout=5,
        )
    except Exception as exc:
        print(f"Progress update unavailable: {type(exc).__name__}", flush=True)


class TaskReport:
    def __init__(self, config, task, snapshot=None):
        self.config, self.task = config, task
        self.record = read_report(config, task) or {}
        if self.record.get("snapshot") != snapshot:
            self.record["answers"] = []
            self.record.pop("accepted_browser_gaps", None)
        self.record.update(task=task, repository=config["repository"], snapshot=snapshot)
        if not self.record.get("conversation_id"):
            created = api(
                "POST",
                "/api/conversations",
                json={
                    "workspace": {"working_dir": "/projects"},
                    "agent": ACPAgent(
                        acp_command=["codex-acp"], acp_server="codex", acp_session_mode="read-only"
                    ).model_dump(mode="json"),
                },
            )
            self.record["conversation_id"] = str(created["id"])
            write_report(config, task, self.record)
            post(
                self.record["conversation_id"],
                "This is a persistent factory task report. The implementation runs in an "
                "isolated worker. If it needs your answer, reply here starting with `resume:` "
                "followed by your answer. To retry a failure, reply `resume: retry`. "
                "An explicit resume message queues the continuation immediately. "
                "If the repository is busy, it waits for the current work to finish. "
                "The report assistant is read-only; native automation runs the continuation. "
                "Only report that it has started when its RUNNING or REVIEWING update appears.",
            )
        if ACTIVE:
            self.record["run_id"] = ACTIVE["run_id"]
            previous = ACTIVE.get("conversation_id")
            current = self.record["conversation_id"]
            if previous and previous != current:
                post(previous, f"Also processing [{task}](/canvas/conversations/{current}).")
            else:
                ACTIVE["conversation_id"] = current
        write_report(config, task, self.record)

    def update(self, status, message, *, metrics=None, wake_assistant=True, **details):
        metrics = metrics or details.get("result", {}).get("metrics")
        if metrics:
            details["metrics"] = str(metrics)
            try:
                measured = json.loads(Path(metrics).read_text())
                message += "\n\n" + measurements.render(measured) + f"\n\nMetrics: `{metrics}`"
            except (OSError, ValueError, KeyError, TypeError):
                message += "\n\nFactory measurements unavailable; validation outcome is unchanged."
        self.record.update(status=status, updated_at=now(), **details)
        write_report(self.config, self.task, self.record)
        # Durable result comes first. An unavailable UI must not lose work.
        phase(f"{self.task}: {status} — {message}")
        try:
            api(
                "PATCH",
                f"/api/conversations/{self.record['conversation_id']}",
                json={"title": f"{self.config['project']} — {self.task} — {status}"},
            )
            post(
                self.record["conversation_id"],
                f"**{status}**\n\n{message}",
                run=status == "NEEDS_INPUT" and wake_assistant,
            )
        except Exception as exc:
            print(f"Canvas report update unavailable: {type(exc).__name__}", flush=True)


def resume_reply(config, task):
    """Only an explicit, new user reply can restart a failed/waiting task."""
    record = read_report(config, task)
    if not record or record.get("status") not in {"NEEDS_INPUT", "FAILED", "PUBLICATION_FAILED"}:
        return None
    page_id = None
    while True:
        # Canvas 1.16's indexed kind/source filters can return no rows for
        # persisted messages. Page the native log and filter its actual events.
        params = {"limit": 100}
        if page_id:
            params["page_id"] = page_id
        try:
            data = api(
                "GET",
                f"/api/conversations/{record['conversation_id']}/events/search",
                params=params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            # A deleted report has no answer. Keep its task and attempt records
            # so losing the conversation cannot authorize another implementation.
            print(f"{task}: Canvas conversation unavailable; no resume reply.", flush=True)
            return None
        for event in data["items"]:
            message = event.get("llm_message", {})
            if event.get("source") != "user" or event.get("kind") != "MessageEvent":
                continue
            if message.get("role") != "user" or event.get("id") == record.get("answer_id"):
                continue
            timestamp = datetime.datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            # SDK event timestamps are UTC without an offset in this version.
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=datetime.UTC)
            if timestamp <= datetime.datetime.fromisoformat(record["updated_at"]):
                continue
            text = "\n".join(c.get("text", "") for c in message.get("content", []))
            if text.lower().startswith("resume:") and text[7:].strip():
                return {
                    "id": event["id"],
                    "answer": text[7:].strip(),
                    "snapshot": record["snapshot"],
                }
        page_id = data.get("next_page_id")
        if not page_id:
            return None


@contextmanager
def run_report():
    global ACTIVE, _RUN_KEY
    _RUN_KEY = session_api_key()
    ACTIVE = {
        "run_id": os.environ["AUTOMATION_RUN_ID"],
        "status": "SKIPPED",
        "summary": "No eligible work",
        "tasks": [],
    }
    try:
        yield ACTIVE
    except BaseException as exc:
        ACTIVE.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report = ACTIVE
        ACTIVE = None
        _RUN_KEY = None
        body = {
            "status": report["status"],
            "conversation_id": report.get("conversation_id"),
            "task_outcome": {
                k: v for k, v in report.items() if k not in {"run_id", "conversation_id"}
            },
        }
        if report.get("error"):
            body["error"] = report["error"]
        api("POST", f"/api/automation/v1/runs/{report['run_id']}/complete", json=body)


def outcome(status, summary):
    if ACTIVE:
        ACTIVE.update(status=status, summary=summary)
