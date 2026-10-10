"""Canvas task progress; presentation never grants validation or retry authority."""

import copy
import datetime
import json
import threading
import time
from pathlib import Path

from openhands.sdk.utils.files import atomic_write_text

LABELS = {
    "implementation": "Implementation",
    "tests": "Project tests",
    "browser_qa": "Browser QA",
    "review": "Independent review",
    "publication": "Publication",
}


def duration(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def cell(text):
    return " ".join(str(text).split()).replace("|", "\\|")


# [impl->req~im-canvas-progress~1]
class TaskProgress:
    def __init__(self, task, artifact, stages, repair_limit, emit=None):
        self.path = Path(artifact) / "stage-progress.json"
        self.started = time.monotonic()
        self.finished = None
        self.stage_started = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.emit = emit
        self.data = {
            "task": task,
            "status": "Running",
            "attempt": 0,
            "repair_limit": repair_limit,
            "current_stage": None,
            "stages": {name: {"status": "Waiting", "elapsed_seconds": None} for name in stages},
            "previous_attempts": [],
            "next_action": "No input needed; work is in progress.",
        }

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            at = self.finished if self.finished is not None else time.monotonic()
            data["elapsed_seconds"] = round(at - self.started, 1)
            data["updated_at"] = datetime.datetime.now(datetime.UTC).isoformat()
            for name, started in self.stage_started.items():
                data["stages"][name]["elapsed_seconds"] = round(at - started, 1)
            return data

    def attempt_label(self):
        if self.data["attempt"]:
            return f"Repair {self.data['attempt']} of {self.data['repair_limit']}"
        if not self.data["repair_limit"]:
            return "Initial attempt"
        return f"Initial attempt; up to {self.data['repair_limit']} repairs"

    def headline(self, data):
        name = data["current_stage"]
        stage = data["stages"].get(name, {})
        label = LABELS.get(name, data["status"])
        detail = stage.get("detail")
        if detail:
            label += f" ({cell(detail)[:24]})"
        elapsed = stage.get("elapsed_seconds")
        age = f"; {duration(elapsed)} in stage" if elapsed is not None else ""
        state = data["status"] if data["status"] != "Running" else stage.get("status", "Running")
        return (
            f"{data['task'][:24]}: {label} — {state}"
            f"{age}; {duration(data['elapsed_seconds'])} total; {self.attempt_label()}"
        )[:200]

    def render(self, data):
        lines = [
            f"**Task progress — {cell(data['task'])}: {data['status']}**",
            f"{self.attempt_label()} · {duration(data['elapsed_seconds'])} elapsed at this update",
            "| Stage | State | Time |",
            "| --- | --- | --- |",
        ]
        for name, stage in data["stages"].items():
            elapsed = stage["elapsed_seconds"]
            label = LABELS[name]
            if stage.get("detail"):
                label += " — " + cell(stage["detail"])
            lines.append(
                f"| {label} | {stage['status']} | {duration(elapsed) if elapsed is not None else '—'} |"
            )
        # Blank lines outside the table keep native Markdown rendering predictable.
        return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:]) + "\n\n" + data["next_action"]

    def refresh(self, conversation=False):
        with self.lock:
            data = self.snapshot()
            text = self.render(data)
            try:
                atomic_write_text(self.path, json.dumps(data, indent=2) + "\n", mode=0o644)
                atomic_write_text(self.path.with_suffix(".md"), text + "\n", mode=0o644)
            except Exception as exc:
                print(f"Task progress retention unavailable: {type(exc).__name__}", flush=True)
            if self.emit:
                try:
                    self.emit(self.headline(data), text if conversation else None)
                except Exception as exc:
                    print(f"Canvas task progress unavailable: {type(exc).__name__}", flush=True)

    def begin_attempt(self, index):
        with self.lock:
            if index != self.data["attempt"]:
                previous = self.snapshot()
                previous.pop("previous_attempts")
                self.data["previous_attempts"].append(previous)
                self.data["attempt"] = index
                self.data["current_stage"] = None
                self.data["stages"] = {
                    name: {"status": "Waiting", "elapsed_seconds": None}
                    for name in self.data["stages"]
                }
                self.stage_started.clear()

    def stage(self, name, status="Running", detail=""):
        with self.lock:
            stage = self.data["stages"][name]
            if stage["status"] == status and stage.get("detail", "") == detail:
                return
            if status == "Running":
                self.data["current_stage"] = name
                self.stage_started.setdefault(name, time.monotonic())
            elif name in self.stage_started:
                stage["elapsed_seconds"] = round(time.monotonic() - self.stage_started.pop(name), 1)
            current = self.data["current_stage"]
            if current is None or self.data["stages"][current]["status"] != "Running":
                self.data["current_stage"] = name
            stage.update(status=status, detail=detail)
            self.refresh(conversation=True)

    def finish(self, error=None, needs_input=False):
        with self.lock:
            self.stop.set()
            self.finished = time.monotonic()
            self.data["status"] = (
                "Waiting for input" if needs_input else "Failed" if error else "Done"
            )
            name = self.data["current_stage"]
            if name and self.data["stages"][name]["status"] == "Running":
                self.data["stages"][name]["status"] = self.data["status"]
            at = time.monotonic()
            for name, started in self.stage_started.items():
                self.data["stages"][name]["elapsed_seconds"] = round(at - started, 1)
            self.stage_started.clear()
            self.data["next_action"] = (
                f"**Your input is needed:** {error}\n\nReply `resume: YOUR ANSWER` in this task conversation."
                if needs_input
                else f"**Stopped:** {error}\n\nInspect the failure before replying `resume: retry`."
                if error
                else "Work finished. Inspect the result in this task conversation."
            )
            self.refresh(conversation=True)

    def heartbeat(self):
        while not self.stop.wait(30):
            with self.lock:
                if self.stop.is_set():
                    return
                self.refresh()
