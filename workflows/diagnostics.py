"""Retain bounded, redacted worker diagnostics before disposable servers exit."""

import io
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from rich.text import Text


# [impl->req~im-bounded-repair~1]
def test_summary(project, output, log_path):
    """Keep failure identities visible when a runner prints a large diagnostic dump."""
    # Decode before selecting/truncating so excerpts cannot begin inside an ANSI code.
    plain = Text.from_ansi(output).plain
    lines = plain.splitlines()
    diagnostic = re.compile(
        r"^\s*(?:FAIL(?:ED)?\b|ERROR\b|E\s+\S|[\w.]*Error:|[\w.]*Exception:"
        r"|Test Files\s|Tests\s|❯\s+\S+:\d+|npm (?:ERR!|error)\b)"
        r"|\b\d+ (?:failed|passed|skipped)(?:[, |]|$)"
    )
    selected = [line for line in lines if diagnostic.search(line)]
    # Unknown runner formats still expose their final output. Full logs are authoritative.
    excerpt = "\n".join(selected) if selected else plain.strip()
    limit = 4000
    if len(excerpt) > limit:
        marker = "\n... excerpt truncated; see full log ...\n"
        half = (limit - len(marker)) // 2
        excerpt = excerpt[:half] + marker + excerpt[-half:]
    return f"{project}:\nFull log: {log_path}\n{excerpt}"


def redact(text, secrets):
    values = set()
    for value in secrets:
        if not isinstance(value, str) or not value:
            continue
        values.add(value)
        try:
            auth = json.loads(value)
        except (ValueError, TypeError):
            continue
        if isinstance(auth, dict):
            tokens = auth.get("tokens") or {}
            if isinstance(tokens, dict):
                values.update(v for v in tokens.values() if isinstance(v, str) and v)
            if isinstance(auth.get("OPENAI_API_KEY"), str) and auth["OPENAI_API_KEY"]:
                values.add(auth["OPENAI_API_KEY"])
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    # Also cover newly rotated tokens if credential readback itself failed.
    text = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", "[REDACTED]", text)
    text = re.sub(
        r'(?i)((?:access_token|refresh_token|id_token|api_key|OH_SESSION_API_KEYS_0)[\\"\s:=]+)[^\s,\\"}]+',
        r"\1[REDACTED]",
        text,
    )
    return text


def retain_worker(workspace, directory, secrets=()):
    session_log = getattr(workspace, "_factory_log", None)
    if isinstance(session_log, io.IOBase):
        size = os.fstat(session_log.fileno()).st_size
        # pread leaves the child process's shared stdout offset untouched.
        data = os.pread(session_log.fileno(), 2_000_000, max(0, size - 2_000_000))
        path = Path(directory) / f"worker-sbx-{uuid4().hex[:12]}.log"
        with path.open("x") as handle:
            handle.write(redact(data.decode("utf-8", errors="replace"), secrets)[-2_000_000:])
        return path
    identifier = getattr(workspace, "_container_id", None)
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{12,64}", identifier):
        return None
    result = subprocess.run(
        ["docker", "logs", "--timestamps", "--tail", "1000", identifier],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError("Worker log capture failed")
    text = redact(result.stdout + result.stderr, secrets)
    path = Path(directory) / f"worker-{identifier[:12]}.log"
    with path.open("x") as handle:
        handle.write(text[-2_000_000:])
    return path
