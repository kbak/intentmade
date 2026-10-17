"""Resume explicitly submitted, unpublished tasks from their captured inputs."""

import json

import execution
import reporting
import sdlc
from common import lock
from lifecycle import NeedsInput


def captured(configs, task, request, bases, publish_draft=None, inputs=None, package=None):
    if len(configs) != 1:
        raise ValueError("Submitted-task replies currently require one repository")
    if package is not None:
        sdlc.accept(package, task, bases, request)
    payload = {
        "configs": configs,
        "task": task,
        "request": request,
        "bases": bases,
        "publish_draft": publish_draft,
        "input_artifacts": inputs,
        "work_package": package,
    }
    return {"payload": payload, "sha256": sdlc.digest(sdlc.encoded(payload))}


def validate(submission, config, task):
    payload = submission["payload"]
    if sdlc.digest(sdlc.encoded(payload)) != submission["sha256"]:
        raise ValueError("Retained submission changed; no continuation started")
    if (
        captured(
            payload["configs"],
            payload["task"],
            payload["request"],
            payload["bases"],
            payload["publish_draft"],
            payload["input_artifacts"],
            payload["work_package"],
        )
        != submission
    ):
        raise ValueError("Invalid retained submission")
    original = payload["configs"][0]
    if payload["task"] != task or any(
        original.get(key) != config.get(key) for key in ("project", "repository", "branch")
    ):
        raise ValueError("Submitted task repository or base branch changed")
    return payload


# [impl->req~im-explicit-resume~1]
def remember(configs, task, request, bases, publish_draft=None, inputs=None, package=None):
    """Capture controller inputs; workers cannot create or replace this receipt."""
    submission = captured(configs, task, request, bases, publish_draft, inputs, package)
    config = configs[0]
    record = reporting.read_report(config, task) or {}
    if record.get("snapshot") is not None:
        raise ValueError("An issue task cannot become a submitted task")
    if record.get("submission") not in (None, submission):
        raise ValueError("Retained submission differs; use a new task ID")
    record.update(task=task, repository=config["repository"], submission=submission)
    reporting.write_report(config, task, record)
    return submission


# [impl->req~im-portable-plan~2]
def authorize_formatting(config, task, event_id, line_ranges):
    """Operator-only amendment: bind named formatting changes to a real user reply."""
    with execution.state(config, task), lock(config["project"] + ".poll"):
        record = reporting.read_report(config, task)
        payload = validate(record["submission"], config, task)
        reply = reporting.resume_reply(config, task)
        if not reply or reply["id"] != event_id or not payload["work_package"]:
            raise ValueError("A fresh explicit answer for this accepted package is required")
        if not line_ranges or set(line_ranges) - {"intent.md", "spec.md"}:
            raise ValueError("Formatting amendments must name accepted intent/spec documents")
        for name, bounds in line_ranges.items():
            if (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or not 1
                <= bounds[0]
                <= bounds[1]
                <= len(payload["work_package"]["documents"][name].splitlines())
            ):
                raise ValueError("Formatting amendments require valid original line bounds")
        amendment = {
            "submission_sha256": record["submission"]["sha256"],
            "package_sha256": sdlc.digest(sdlc.encoded(payload["work_package"])),
            "event_id": event_id,
            "answer": reply["answer"],
            "documents": sorted(line_ranges),
            "line_ranges": line_ranges,
        }
        record["formatting_amendment"] = amendment
        reporting.write_report(config, task, record)
        return amendment


# [impl->req~im-explicit-resume~1]
def process(
    config,
    credential,
    limit,
    on_start=lambda: None,
    *,
    enqueue=None,
    selected=None,
    answer_id=None,
    submission_sha256=None,
):
    """Scans enqueue briefly; the selected child owns task state during execution."""
    from run import build_group

    count, waiting = 0, 0
    for path in sorted(reporting.report_path(config, "unused").parent.glob("*.json")):
        if count >= limit:
            break
        if selected is not None and path.stem != selected:
            continue
        record = json.loads(path.read_text())
        if not record.get("submission") or record.get("snapshot") is not None:
            continue
        if any(
            s.get("pull_request") for s in record.get("result", {}).get("repositories", {}).values()
        ):
            continue
        if submission_sha256 is not None and record["submission"]["sha256"] != submission_sha256:
            raise ValueError("Queued submission identity changed; no continuation started")
        reply = reporting.resume_reply(config, path.stem)
        if not reply or reply["id"] == record.get("answer_id"):
            continue
        if answer_id is not None and reply["id"] != answer_id:
            raise ValueError("The queued submission answer changed; no continuation started")
        payload = validate(record["submission"], config, path.stem)
        if enqueue is not None:
            if enqueue(
                config,
                {
                    "kind": "submission",
                    "key": path.stem,
                    "task": path.stem,
                    "submission_sha256": record["submission"]["sha256"],
                    "answer_id": reply["id"],
                },
                on_start=on_start,
            ):
                count += 1
            continue
        amendment = record.get("formatting_amendment")
        if amendment and (
            amendment["submission_sha256"] != record["submission"]["sha256"]
            or amendment["package_sha256"] != sdlc.digest(sdlc.encoded(payload["work_package"]))
        ):
            raise ValueError("Formatting amendment belongs to another submission")
        failure = record.get("result", {}).get("error", "")
        report = None
        answers = [*record.get("answers", []), reply["answer"]]

        def started():
            nonlocal report, count
            report = reporting.TaskReport(config, path.stem)
            report.record["answers"] = answers
            report.update(
                "RUNNING",
                "Continuing the approved task from its retained source.",
                answer_id=reply["id"],
            )
            count += 1
            on_start()

        try:
            result = build_group(
                payload["configs"],
                payload["task"],
                payload["request"],
                payload["bases"],
                credential,
                publish_draft=payload["publish_draft"],
                input_artifacts_declared=payload["input_artifacts"],
                work_package=payload["work_package"],
                continuation={
                    "answers": answers,
                    "failure": failure,
                    "formatting_amendment": amendment,
                    "on_start": started,
                },
            )
            urls = [
                s["pull_request"] for s in result["repositories"].values() if s.get("pull_request")
            ]
            report.update(
                "PASSED", "\n".join(urls) or "Validated; evidence retained.", result=result
            )
        except BlockingIOError:
            if report is not None:
                raise
            continue
        except NeedsInput as exc:
            waiting += 1
            if report is not None:
                report.update("NEEDS_INPUT", str(exc))
            else:
                raise
        except Exception as exc:
            if report is not None:
                report.update(
                    "FAILED", f"Continuation stopped: {exc}. Retained source is preserved."
                )
            raise
    return count, waiting
