"""Allow custom automations to report the native SKIPPED outcome.

Canvas already models and renders SKIPPED, but its SDK callback only accepts
COMPLETED/FAILED. Keep the patch narrow and fail on pinned-source drift.
"""

import ast
from pathlib import Path


def replace(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Pinned automation callback changed; review the outcome patch")
    result = source.replace(old, new)
    ast.parse(result)
    return result


def patch_schema(source):
    source = replace(
        source,
        'status: Literal["COMPLETED", "FAILED"]',
        'status: Literal["COMPLETED", "FAILED", "SKIPPED"]',
    )
    return replace(
        source,
        "    phase: str = Field(..., min_length=1, max_length=200)",
        "    phase: str = Field(..., min_length=1, max_length=200)\n"
        "    conversation_id: str | None = None",
    )


def patch_router(source):
    source = replace(
        source,
        """    new_status = (
        AutomationRunStatus.COMPLETED
        if body.status == "COMPLETED"
        else AutomationRunStatus.FAILED
    )""",
        """    new_status = AutomationRunStatus(body.status)""",
    )
    source = replace(
        source,
        """    if body.cost is not None:
        values["cost"] = body.cost""",
        """    if body.cost is not None:
        values["cost"] = body.cost
    if body.task_outcome is not None or body.blocking_factor is not None:
        values["run_metadata"] = {
            **(run.run_metadata or {}),
            "task_outcome": body.task_outcome,
            "blocking_factor": body.blocking_factor,
        }""",
    )
    return replace(
        source,
        ".values(current_phase=body.phase)",
        ".values(current_phase=body.phase, **(\n"
        '            {"conversation_id": body.conversation_id} if body.conversation_id else {}\n'
        "        ))",
    )


if __name__ == "__main__":
    import openhands.automation

    root = Path(openhands.automation.__file__).parent
    for name, patch in (("schemas.py", patch_schema), ("router.py", patch_router)):
        file = root / name
        file.write_text(patch(file.read_text()))
