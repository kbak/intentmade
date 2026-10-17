"""Bounded worker-reported feedback activity, used only for Canvas presentation."""

import json
import os
import re
import stat
from datetime import datetime
from itertools import islice

import job_files


def tail(root, path):
    # Workers control this storage. Never follow links or open a blocking device.
    with job_files.parent(root, path) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Activity requires a singly linked regular file")
            source.seek(max(0, info.st_size - 16384))
            return source.read(16384).decode(errors="replace")


# [impl->req~im-canvas-progress~1]
def feedback_activity(selected):
    details = []
    for project, paths in selected.items():
        try:
            # Open the directory through the job root, without following worker links.
            base = paths["scratch"].parent
            with job_files.parent(paths["root"], base / "invocation.json") as (directory, _):
                with os.scandir(directory) as entries:
                    names = [entry.name for entry in islice(entries, 201)]
            if len(names) > 200:
                continue
            latest = None
            for name in names:
                if not re.fullmatch(r"agent-check-[0-9a-f-]{36}", name):
                    continue
                try:
                    record = json.loads(tail(paths["root"], base / name / "invocation.json"))
                    started = datetime.fromisoformat(record["started_at"])
                    if record["phase"] != "feedback" or started.tzinfo is None:
                        continue
                    if latest is None or started > latest[0]:
                        latest = started, name, record
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            if latest is None or latest[2].get("completed_at") is not None:
                continue
            detail = "Worker validation: " + project
            try:
                output = tail(paths["root"], base / latest[1] / "evidence/tests.log")
                markers = re.findall(
                    r"^\[check\] ([A-Za-z0-9 ()/_.-]{1,96}): (starting|exit .*)$", output, re.M
                )
                if markers and markers[-1][1] == "starting":
                    detail += " / " + markers[-1][0]
            except (OSError, ValueError):
                pass
            details.append(detail)
        except (OSError, ValueError):
            continue
    return "; ".join(details)
