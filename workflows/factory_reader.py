"""Read-only source inspection for native OpenHands review conversations.

No shell, editor, network, plugin, or delegation capability is exposed. Git
operations accept only captured commit IDs and disable external diff/textconv.
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
from itertools import islice
from pathlib import Path
from typing import Literal

from openhands.sdk.tool import Action, Observation, ToolDefinition, ToolExecutor, register_tool
from pydantic import Field

MAX_BYTES = 2 * 1024 * 1024


class ReadAction(Action):
    operation: Literal["read", "list", "search", "diff", "show"]
    path: str = Field(description="Absolute file/directory path; repository root for diff/show.")
    text: str = Field(
        default="", description="Literal search text, or relative file path for show."
    )
    base: str = Field(default="", description="Full captured base commit ID for diff/show.")
    candidate: str = Field(default="", description="Full captured candidate commit ID for diff.")
    start_line: int = Field(default=1, ge=1, le=1000000)
    lines: int = Field(default=200, ge=1, le=2000)


class ReadObservation(Observation):
    pass


# [impl->req~im-native-read-only~1]
class Reader(ToolExecutor):
    def __init__(self, roots):
        self.roots = [Path(root).resolve() for root in roots]

    def allowed(self, path):
        path = Path(path).resolve(strict=True)
        if not any(path.is_relative_to(root) for root in self.roots):
            raise ValueError("Path is outside the controller-selected review roots")
        return path

    def read(self, path):
        path = self.allowed(path)
        # Reject devices, sockets, FIFOs and symlink replacement at open time.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("Only regular files can be read")
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("File exceeds the 2 MiB inspection limit; evidence is unavailable")
        return data.decode("utf-8", errors="replace")

    def git(self, action, path):
        for revision in [action.base] + ([action.candidate] if action.operation == "diff" else []):
            if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
                raise ValueError("Git inspection requires full captured commit IDs")
        # Git metadata in factory review workers comes from the controller's
        # fresh clones. Never expose arbitrary Git options or a command string.
        args = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.fsmonitor=false"]
        if action.operation == "diff":
            args += ["diff", "--no-ext-diff", "--no-textconv", action.base, action.candidate, "--"]
            if action.text:
                args.append(action.text)
        else:
            if Path(action.text).is_absolute() or ".." in Path(action.text).parts:
                raise ValueError("show requires a relative source path")
            args += ["show", "--no-ext-diff", "--no-textconv", f"{action.base}:{action.text}"]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/nonexistent",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        # Bound both command duration and output without buffering a huge diff.
        with tempfile.TemporaryFile() as output:
            # A fresh isolated interpreter avoids preexec_fn in threaded servers.
            limit = (
                "import os, resource, sys; "
                f"resource.setrlimit(resource.RLIMIT_FSIZE, ({MAX_BYTES}, {MAX_BYTES})); "
                "resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
                "os.execvp(sys.argv[1], sys.argv[1:])"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", limit, *args],
                cwd=path,
                env=env,
                stdout=output,
                stderr=output,
                timeout=30,
            )
            output.seek(0)
            data = output.read(MAX_BYTES).decode("utf-8", errors="replace")
        if result.returncode:
            raise ValueError("Git evidence unavailable or exceeds 2 MiB: " + data[-1000:])
        return data

    def inspect(self, action):
        path = self.allowed(action.path)
        if action.operation == "read":
            text = self.read(path)
        elif action.operation == "list":
            entries = list(islice(path.iterdir(), 10001))
            if len(entries) > 10000:
                raise ValueError("Directory exceeds 10000 entries; choose a narrower directory")
            text = "\n".join(sorted(item.name + ("/" if item.is_dir() else "") for item in entries))
        elif action.operation == "search":
            if not action.text:
                raise ValueError("Supply nonempty literal search text")
            matches = []
            visited = 0
            inspected_bytes = 0

            def unavailable(error):
                raise error

            entries = (
                [(path.parent, [], [path.name])]
                if path.is_file()
                else os.walk(path, followlinks=False, onerror=unavailable)
            )
            for directory, dirs, files in entries:
                visited += 1
                if visited > 10000:
                    raise ValueError("Search exceeds 10000 entries; choose a narrower directory")
                dirs[:] = sorted(d for d in dirs if d != ".git")
                for name in sorted(files):
                    visited += 1
                    if visited > 10000:
                        raise ValueError("Search exceeds 10000 files; choose a narrower directory")
                    target = Path(directory) / name
                    if target.is_symlink() or not target.is_file():
                        continue
                    content = self.read(target)
                    inspected_bytes += len(content)
                    if inspected_bytes > 32 * MAX_BYTES:
                        raise ValueError(
                            "Search exceeds 64 MiB of source; choose a narrower directory"
                        )
                    for number, line in enumerate(content.splitlines(), 1):
                        if action.text in line:
                            matches.append(f"{target}:{number}: {line[:1000]}")
                            if len(matches) >= 2000:
                                return (
                                    "\n".join(matches)[:64000]
                                    + "\nSearch truncated; narrow the directory/text."
                                )
            text = "\n".join(matches)
        else:
            text = self.git(action, path)
        lines = text.splitlines()
        first = action.start_line - 1
        page = "\n".join(
            f"{i + 1}: {line}" for i, line in enumerate(lines[first : first + action.lines], first)
        )
        return (
            page[:64000]
            + f"\n({len(lines)} total lines; use start_line/lines to page; output capped at 64000 characters)"
        )

    def __call__(self, action, conversation=None):
        try:
            return ReadObservation.from_text(self.inspect(action))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return ReadObservation.from_text(str(exc), is_error=True)


class FactoryReaderTool(ToolDefinition[ReadAction, ReadObservation]):
    @classmethod
    def create(cls, conv_state, roots=None):
        return [
            cls(
                action_type=ReadAction,
                observation_type=ReadObservation,
                description="Read/list/search source and inspect captured Git diffs or historical files. "
                "No writes or shell commands. Missing/truncated evidence must be reported. "
                f"Working directory: {conv_state.workspace.working_dir}",
                executor=Reader(roots or [conv_state.workspace.working_dir]),
            )
        ]


register_tool(FactoryReaderTool.name, FactoryReaderTool)
