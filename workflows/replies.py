"""Wake native continuations when Canvas persists an explicit task answer."""

import hashlib
import json

import reporting
from common import DATA, api, lock, projects


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
                    return False
                key = hashlib.sha256((conversation_id + ":" + reply["id"]).encode()).hexdigest()
                receipt = DATA / "reply-dispatches" / (key + ".json")
                if receipt.exists():
                    return True
                scheduler = api("GET", "/api/automation/v1/" + route["scheduler_id"], timeout=10)
                if not scheduler["enabled"]:
                    return False
                run = api(
                    "POST", "/api/automation/v1/" + route["automation_id"] + "/dispatch", timeout=10
                )
                receipt.parent.mkdir(parents=True, exist_ok=True)
                temporary = receipt.with_suffix(".tmp")
                temporary.write_text(json.dumps({"answer_id": reply["id"], "run_id": run["id"]}))
                temporary.replace(receipt)
            # Do not advance the question timestamp or consume the answer here.
            # Execution rechecks it under the repository lock. If the HTTP
            # acknowledgement is lost, even a duplicate run cannot apply it twice.
            reporting.post(
                conversation_id,
                "Your answer has queued a continuation. The factory will recheck the task "
                "before starting it. If the repository is busy, the continuation will wait; "
                "no scheduled scan is required.",
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
