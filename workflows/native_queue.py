"""Short repository scans dispatch separate native task runs, never worker threads."""

import hashlib
import json
import sys

import automation_retention
import deployment
from common import ROOT, api


def registration():
    sys.path.insert(0, str(ROOT))
    import configure

    return configure


# [impl->req~im-repository-locks~1]
def enqueue(config, work, *, on_start=lambda: None):
    """One immutable native recipe per work/reply identity; dispatch at most once."""
    identity = [config["repository"], work["kind"], work["key"], work.get("answer_id")]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    label = work.get("task") or str((work.get("item") or {}).get("number", work["kind"]))
    name = f"Task — {config['project']} — {label} — {digest[:24]}"
    native = registration()
    # The caller holds the short repository poll lock. An existing recipe,
    # including an ambiguous failed dispatch, requires inspection or a new reply.
    if automation_retention.retired(name) or any(item["name"] == name for item in native.records()):
        return False
    on_start()
    job = {"config": config, "scheduled_task": work}
    saved = native.install(
        {
            "name": name,
            "trigger": {
                "type": "event",
                "source": "custom",
                "on": "factory.scheduled-task",
                "filter": "`false`",
            },
            "entrypoint": "python monitor.py",
            "timeout": deployment.run_timeout(config),
        },
        native.files(job),
    )
    result = api("POST", "/api/automation/v1/" + saved["id"] + "/dispatch")
    print(f"Queued {work['kind']} {work['key']}: native run {result['id']}", flush=True)
    return True


def execute(config, work, credential):
    """Revalidate delayed work under task exclusion, outside the scan lock."""
    import execution
    import followup
    import monitor
    import reporting
    import review_requests
    from lifecycle import NeedsInput

    kind, item = work["kind"], work.get("item")
    if kind == "triage":
        monitor.triage(config, work["items"])
        return True
    if kind == "submission":
        import submissions

        with execution.task([config], work["task"], wait_task=True):
            count, waiting = submissions.process(
                config,
                credential,
                1,
                selected=work["task"],
                answer_id=work["answer_id"],
                submission_sha256=work["submission_sha256"],
            )
        return count > waiting
    if kind == "continuation":
        import continuations

        records = [
            json.loads(path.read_text()) for path in continuations.directory(config).glob("*.json")
        ]
        record = next(record for record in records if record["id"] == work["key"])
        with execution.task([config], record["package"]["task"], wait_task=True):
            count, waiting = continuations.process(
                config,
                credential,
                1,
                selected=work["key"],
                answer_id=work["answer_id"],
                package_sha256=work["package_sha256"],
            )
        return count > waiting
    name = ("issue-" if kind == "issue" else "pr-") + str(item["number"])
    if kind == "maintenance":
        record = followup.read(config, item["number"])
        if not record:
            raise NeedsInput("The publication receipt is missing; no maintenance started")
        name = record["task"]
    with execution.task([config], name, wait_task=True):
        if kind == "issue":
            report = reporting.read_report(config, name) or {}
            if "report_run_id" in work and report.get("run_id") != work["report_run_id"]:
                raise NeedsInput(
                    "Another attempt advanced the queued task; a fresh answer is required"
                )
            resume = item.get("factory_resume")
            if resume:
                current = reporting.resume_reply(config, name)
                if (
                    not current
                    or current["id"] != resume["id"]
                    or current["snapshot"] != resume["snapshot"]
                ):
                    raise NeedsInput("The queued issue answer is no longer current")
            if (
                deployment.issue_intake(config) == "automatic"
                and work["snapshot"].get("authorization") != "operator"
                and deployment.current_issue_intake(config) != "automatic"
            ):
                raise NeedsInput(
                    "Automatic intake is no longer enabled; no queued issue work started"
                )
            result = monitor.implement_issue(
                config,
                item,
                credential,
                resume=item.get("factory_resume"),
                expected=work["snapshot"],
            )
            return bool(result and result.get("status") != "NEEDS_INPUT")
        elif kind == "review":
            fresh = monitor.reviews._get_pr(credential, config["repository"], item["number"])
            requests = review_requests.ReviewRequests(config, credential)
            if (
                review_requests.snapshot(config, fresh) != review_requests.snapshot(config, item)
                or not requests.eligible(fresh)
                or requests.human_reviewed(fresh)
                or not monitor.pr_eligible(credential, fresh, config)
            ):
                raise NeedsInput(
                    "Queued review revision or eligibility changed; inspect the current PR"
                )
            resume = item.get("factory_resume")
            if resume and not review_requests.retryable(config, fresh, resume):
                raise NeedsInput("Queued review answer is no longer current")
            previous = review_requests.read(config, fresh)
            status = (
                "PUBLICATION_FAILED"
                if previous and previous["status"] == "PUBLICATION_FAILED"
                else "STARTED"
            )
            review_requests.remember(
                config, fresh, status, **({"answer_id": resume["id"]} if resume else {})
            )
            try:
                current = monitor.review_pr(config, fresh, credential)
                review_requests.remember(config, fresh, "REVIEWED" if current else "STALE")
            except NeedsInput:
                review_requests.remember(config, fresh, "NEEDS_INPUT")
                raise
            except Exception as exc:
                if not isinstance(exc, monitor.review_publication.PublicationError):
                    review_requests.remember(config, fresh, "FAILED")
                raise
        elif kind == "maintenance":
            return followup.maintain(config, item, item["maintenance"], credential)
        else:
            raise ValueError("Unsupported native queued task kind: " + str(kind))
    reporting.phase("Queued task finished; inspect its task report for validation results")
    return True
