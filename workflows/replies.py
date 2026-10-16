"""Wake native continuations when Canvas persists an explicit task answer."""

import hashlib
import json

import authorization
import continuations
import reporting
from common import DATA, api, lock, projects
from openhands.sdk.utils.files import atomic_write_text


# [impl->req~im-explicit-resume~1]
# [impl->req~im-authorization~1]
def queue_reply(conversation_id):
    for config in projects().values():
        if not config.get("enabled") or not config.get("repository"):
            continue
        route = reporting.read_report(config, "reply-trigger") or {}
        if not route.get("automation_id"):
            continue
        for path in reporting.report_path(config, "unused").parent.glob("*.json"):
            record = json.loads(path.read_text())
            if record.get("conversation_id") != conversation_id:
                continue
            # Read the persisted event, rather than treating the callback text
            # as an answer. This applies the same freshness/role/status rules
            # as the scheduler and requires a known task conversation.
            with lock(config["project"] + ".reply-queue", blocking=True):
                reply = reporting.resume_reply(config, path.stem)
                if not reply:
                    continuation = continuations.capture(config, path.stem, record)
                    if continuation:
                        reply = {"id": continuation["package"]["event_id"]}
                if not reply:
                    return False
                key = hashlib.sha256((conversation_id + ":" + reply["id"]).encode()).hexdigest()
                receipt = DATA / "reply-dispatches" / (key + ".json")
                if receipt.exists():
                    return True
                scheduler = api("GET", "/api/automation/v1/" + route["scheduler_id"], timeout=10)
                if not authorization.permit(
                    config,
                    "resume:dispatch",
                    {"task": path.stem, "answer": reply["id"], "scheduler": route["scheduler_id"]},
                    {"scheduler_enabled": scheduler["enabled"]},
                ):
                    return False
                run = api(
                    "POST", "/api/automation/v1/" + route["automation_id"] + "/dispatch", timeout=10
                )
                receipt.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(
                    receipt, json.dumps({"answer_id": reply["id"], "run_id": run["id"]}), mode=0o644
                )
            # Do not advance the question timestamp or consume the answer here.
            # Execution rechecks it under the repository lock. If the HTTP
            # acknowledgement is lost, even a duplicate run cannot apply it twice.
            reporting.post(
                conversation_id,
                "Your explicit request has queued a continuation in this thread. The factory will recheck the task "
                "before starting it. If the repository is busy, the continuation will wait; "
                "implementation and independent read-only review run separately.",
            )
            return True
    return False


if __name__ == "__main__":
    import sys

    try:
        queue_reply(sys.argv[1])
    except Exception as exc:
        print("Immediate continuation unavailable:", type(exc).__name__, file=sys.stderr)
        sys.exit(1)
