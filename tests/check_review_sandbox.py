"""Check real read-only Codex execution inside ordinary Docker confinement."""

import os
import subprocess

PROBE = """import pathlib, socket
assert pathlib.Path('/etc/os-release').read_text()
denied = 0
try:
    pathlib.Path('/tmp/factory-review-write-probe').write_text('must be denied')
except PermissionError:
    denied += 1
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except PermissionError:
    denied += 1
assert denied == 2, 'Read-only sandbox allowed file writes or network sockets'
print('PASS reviewer reads files; file writes and network sockets are denied')
"""

subprocess.run(
    [
        "docker",
        "run",
        "--rm",
        "--entrypoint",
        "/acp-node/bin/codex",
        os.environ.get("FACTORY_IMAGE", "openhands-factory:dev"),
        "-c",
        "features.use_legacy_landlock=true",
        "-c",
        'sandbox_mode="read-only"',
        "sandbox",
        "--",
        "python",
        "-c",
        PROBE,
    ],
    check=True,
)
