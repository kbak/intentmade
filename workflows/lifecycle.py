"""Shared task outcomes, evidence persistence, measurements and report delivery."""

import json
from contextlib import ExitStack
from pathlib import Path

FAILURES = {"NEEDS_INPUT", "FAILED", "PUBLICATION_FAILED"}


class NeedsInput(RuntimeError):
    pass


class PublicationError(RuntimeError):
    pass


def failure_status(exc, result=None):
    # An inner build may already have distinguished a publication failure.
    # Preserve that outcome through issue/maintenance adapters and metrics.
    for retained in (result or {}, getattr(exc, "factory_outcome", None) or {}):
        if retained.get("status") in FAILURES:
            return retained["status"]
    if isinstance(exc, NeedsInput):
        return "NEEDS_INPUT"
    return "PUBLICATION_FAILED" if isinstance(exc, PublicationError) else "FAILED"


class TaskLifecycle:
    def __init__(
        self,
        *,
        artifact=None,
        task=None,
        kind=None,
        repositories=None,
        stages=(),
        repair_limit=0,
        report=None,
        failure_hint="",
    ):
        self.artifact = Path(artifact) if artifact is not None else None
        self.task, self.kind = task, kind
        self.repositories = repositories or {}
        self.stages, self.repair_limit = stages, repair_limit
        self.report, self.failure_hint = report, failure_hint
        self.result = None
        self.handoff = False
        self.failure = None
        self.stack = ExitStack()
        self.active = False
        self.notification = None

    def __enter__(self):
        if self.kind is not None:
            import measurements
            import reporting

            with ExitStack() as setup:
                setup.enter_context(
                    measurements.task(self.artifact, self.task, self.kind, self.repositories)
                )
                setup.enter_context(
                    reporting.task_progress(
                        self.task, self.artifact, self.stages, self.repair_limit
                    )
                )
                self.stack = setup.pop_all()
        self.active = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc is not None and exc is not self.failure:
                self.fail(exc)
        finally:
            self.stack.__exit__(exc_type, exc, traceback)
            self.active = False
            if self.notification is not None:
                status, message, details = self.notification
                self.notify(status, message, **details)
        return False

    # [impl->req~im-measurement-outcomes~1]
    def save(self, result, *, handoff=False):
        import measurements
        from openhands.sdk.utils.files import atomic_write_text

        self.result = result
        self.handoff = self.handoff or handoff
        atomic_write_text(self.artifact / "result.json", json.dumps(result, indent=2), mode=0o644)
        if self.handoff:
            import sdlc

            sdlc.handoff(self.artifact, result)
        measurements.update(result)

    def notify(self, status, message, **details):
        if self.active:
            # Metrics finish before the report renders their final durations.
            self.notification = (status, message, details)
            return
        if self.report is not None:
            if self.artifact is not None:
                details.setdefault("metrics", self.artifact / "metrics.json")
            self.report(status, message, **details)

    def complete(self, status, message, *, result=None, **details):
        if result is not None:
            if self.artifact is not None:
                self.save(result)
            else:
                self.result = result
            details["result"] = result
        elif self.result is not None:
            self.result["status"] = status
            if self.artifact is not None:
                self.save(self.result)
            details["result"] = self.result
        self.notify(status, message, **details)

    # [impl->req~im-needs-input~1]
    def fail(self, exc, **details):
        if exc is self.failure:
            return failure_status(exc, self.result)
        self.failure = exc
        status = failure_status(exc, self.result)
        if self.result is not None:
            self.result.update(status=status, error=f"{type(exc).__name__}: {exc}")
            if self.artifact is not None:
                self.save(self.result)
            details.setdefault("result", self.result)
        # Keep the original exception type and traceback for existing callers.
        exc.factory_outcome = {"status": status}
        self.notify(status, str(exc) + self.failure_hint, **details)
        return status
