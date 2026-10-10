"""Persistent Canvas task reports, human replies, and truthful run callbacks."""

import base64
import datetime
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import authorization
import harness
import httpx
import measurements
from approval import microseconds
from common import DATA, api, identifier, session_api_key
from lifecycle import NeedsInput  # noqa: F401 (public import for retained workflows)
from openhands.sdk.utils.files import atomic_write_text
from progress import TaskProgress

ACTIVE = None
_RUN_KEY = None
_PROGRESS = ContextVar("canvas_task_progress", default=None)


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def report_path(config, task):
    repository = (config["repository"] or "fixture:" + config["project"]).casefold()
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
    atomic_write_text(path, json.dumps(record, indent=2), mode=0o644)


def post(conversation_id, message, run=False, images=None, *, session_key=None, timeout=120):
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
        timeout=timeout,
        **({"headers": {"X-Session-API-Key": session_key}} if session_key else {}),
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
        if result.get("resume_hint"):
            text += "\n\n" + result["resume_hint"]
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
    progress = _PROGRESS.get()
    if progress:
        progress.refresh()
        return
    publish_phase(message, ACTIVE["run_id"], _RUN_KEY, ACTIVE.get("conversation_id"))


def publish_phase(message, run_id, key, conversation_id):
    try:
        api(
            "POST",
            f"/api/automation/v1/runs/{run_id}/phase",
            json={
                "phase": " ".join(message.split())[:200],
                "conversation_id": conversation_id,
            },
            headers={"X-Session-API-Key": key},
            timeout=5,
        )
    except Exception as exc:
        print(f"Progress update unavailable: {type(exc).__name__}", flush=True)


# [impl->req~im-canvas-progress~1]
@contextmanager
def task_progress(task, artifact, stages, repair_limit=0):
    emit = None
    if ACTIVE:
        run_id, key = ACTIVE["run_id"], _RUN_KEY
        conversation_id = ACTIVE.get("task_conversation_id", ACTIVE.get("conversation_id"))

        def emit(message, summary):
            # Capture parent auth before workers replace their environment.
            publish_phase(message, run_id, key, conversation_id)
            if summary and conversation_id:
                post(conversation_id, summary, session_key=key, timeout=5)

    progress = TaskProgress(task, artifact, stages, repair_limit, emit)
    token = _PROGRESS.set(progress)
    thread = threading.Thread(target=progress.heartbeat, daemon=True) if emit else None
    try:
        progress.refresh(conversation=True)
        if thread:
            try:
                thread.start()
            except RuntimeError as exc:
                print(f"Canvas timer unavailable: {type(exc).__name__}", flush=True)
                thread = None
        try:
            yield progress
        except BaseException as exc:
            progress.finish(str(exc) or type(exc).__name__, isinstance(exc, NeedsInput))
            raise
        else:
            progress.finish()
    finally:
        progress.stop.set()
        if thread and thread.ident is not None:
            thread.join(timeout=6)
        _PROGRESS.reset(token)


def progress_stage(name, status="Running", detail=""):
    if progress := _PROGRESS.get():
        progress.stage(name, status, detail)


def progress_attempt(index):
    if progress := _PROGRESS.get():
        progress.begin_attempt(index)


# [impl->req~im-agent-profile~1]
# [impl->req~im-native-read-only~1]
def report_agent():
    name = harness.profile_name()
    from urllib.parse import quote

    from agent import worker_agent

    selected = harness.resolve(
        api("GET", "/api/agent-profiles/" + quote(name, safe=""))["profile"], name, ["/projects"]
    )
    token = harness.CURRENT.set(selected)
    try:
        return worker_agent("read-only")
    finally:
        harness.CURRENT.reset(token)


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
                    "agent": report_agent().model_dump(
                        mode="json", context={"expose_secrets": "plaintext"}
                    ),
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
                "Factory updates do not start the report assistant. This report's terminal "
                "does not show the isolated worker; use Automate for its current phase. "
                "Only report that it has started when its RUNNING or REVIEWING update appears.",
            )
        if ACTIVE:
            ACTIVE["task_conversation_id"] = self.record["conversation_id"]
            self.record["run_id"] = ACTIVE["run_id"]
            previous = ACTIVE.get("conversation_id")
            current = self.record["conversation_id"]
            if previous and previous != current:
                post(previous, f"Also processing [{task}](/canvas/conversations/{current}).")
            else:
                ACTIVE["conversation_id"] = current
        write_report(config, task, self.record)

    # [impl->req~im-report-status~1]
    def update(self, status, message, *, metrics=None, **details):
        metrics = metrics or details.get("result", {}).get("metrics")
        if metrics:
            details["metrics"] = str(metrics)
            try:
                measured = json.loads(Path(metrics).read_text())
                message += "\n\n" + measurements.render(measured) + f"\n\nMetrics: `{metrics}`"
            except (OSError, ValueError, KeyError, TypeError):
                message += "\n\nFactory measurements unavailable; validation outcome is unchanged."
        # Persist the task before even a read-only UI request can fail or stall.
        self.record.update(status=status, updated_at=now(), **details)
        self.record["assistant_execution_status"] = "unknown"
        write_report(self.config, self.task, self.record)
        phase(f"{self.task}: {status} — {message}")
        # The report reader can fail independently of the task. Inspect only its
        # status; raw provider diagnostics may contain secrets and stay native.
        try:
            conversation = api(
                "GET", f"/api/conversations/{self.record['conversation_id']}", timeout=5
            )
            assistant_status = conversation.get("execution_status")
            if not isinstance(assistant_status, str):
                assistant_status = "unknown"
        except Exception:
            assistant_status = "unknown"
        self.record["assistant_execution_status"] = assistant_status
        try:
            write_report(self.config, self.task, self.record)
        except OSError as exc:
            print(f"Report assistant status unavailable: {type(exc).__name__}", flush=True)
        if assistant_status == "error":
            message += (
                "\n\nThe report assistant is unavailable after an agent error. "
                "The Factory task status above is separate; its worker runs independently. "
                "Use Automate for the worker's current phase. "
                "Explicit `resume:` replies still continue eligible failed or waiting tasks."
            )
        try:
            api(
                "PATCH",
                f"/api/conversations/{self.record['conversation_id']}",
                json={"title": f"{self.config['project']} — {self.task} — {status}"},
            )
            post(
                self.record["conversation_id"],
                f"**{status}**\n\n{message}",
                run=False,
            )
        except Exception as exc:
            print(f"Canvas report update unavailable: {type(exc).__name__}", flush=True)


# [impl->req~im-explicit-resume~1]
# [impl->req~im-authorization~1]
def resume_reply(config, task):
    """Only an explicit, new user reply can restart a failed/waiting task."""
    record = read_report(config, task)
    if not record:
        return None
    if not authorization.permit(
        config,
        "resume:task",
        {"task": task, "conversation": record.get("conversation_id")},
        {"status": record.get("status", "")},
    ):
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
            message = event.get("llm_message") or {}
            subject = {
                "task": task,
                "conversation": record["conversation_id"],
                "event": event.get("id"),
            }
            if not authorization.permit(
                config,
                "resume:event",
                subject,
                {
                    "source": event.get("source", ""),
                    "kind": event.get("kind", ""),
                    "role": message.get("role", ""),
                    "event_id": event.get("id"),
                    "answer_id": record.get("answer_id") or "",
                },
            ):
                continue
            timestamp = datetime.datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            # SDK event timestamps are UTC without an offset in this version.
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=datetime.UTC)
            text = "\n".join(c.get("text", "") for c in message.get("content", []))
            if authorization.permit(
                config,
                "resume:answer",
                subject,
                {
                    "event_at": microseconds(timestamp),
                    "question_at": microseconds(
                        datetime.datetime.fromisoformat(record["updated_at"])
                    ),
                    "explicit": text.lower().startswith("resume:"),
                    "answer_present": bool(text[7:].strip()),
                },
            ):
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
    # A factory run spans several native workspaces. Their optional SDK exit
    # callbacks must not complete the automation before tests/review/publication.
    callback_url = os.environ.pop("AUTOMATION_CALLBACK_URL", None)
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
        try:
            api("POST", f"/api/automation/v1/runs/{report['run_id']}/complete", json=body)
        finally:
            if callback_url is not None:
                os.environ["AUTOMATION_CALLBACK_URL"] = callback_url


def outcome(status, summary):
    if ACTIVE:
        ACTIVE.update(status=status, summary=summary)
