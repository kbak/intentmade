"""Exercise modern Codex permission profiles in a real, disposable container."""

import os
import subprocess
from pathlib import Path

PROBE = r'''
import json
import pathlib
import subprocess

root = pathlib.Path('/home/openhands/factory-sandbox-probe')
repo = root / 'repo'
requests = root / 'requests'
repo.mkdir(parents=True)
requests.mkdir()
(repo / '.git').mkdir()
(repo / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')

checks = """
import errno, pathlib, socket
root = pathlib.Path('/home/openhands/factory-sandbox-probe')
assert pathlib.Path('/etc/os-release').read_text()
assert (root / 'repo/.git/HEAD').read_text()
def denied(operation):
    try:
        operation()
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS), exc
    else:
        raise AssertionError('Sandbox allowed a prohibited operation')
denied(lambda: (root / 'outside').write_text('bad'))
denied(lambda: (root / 'repo/.git/HEAD').write_text('bad'))
denied(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
"""
for mode in ['workspace-write', 'read-only']:
    writes = (
        "(root / 'repo/edit.txt').write_text('allowed')\n"
        "(root / 'requests/spec.md').write_text('allowed')\n"
        if mode == 'workspace-write'
        else "denied(lambda: (root / 'repo/review.txt').write_text('bad'))\n"
        "denied(lambda: (root / 'requests/review.md').write_text('bad'))\n"
    )
    subprocess.run([
        '/acp-node/bin/codex', '-c', 'features.use_legacy_landlock=false',
        '-c', 'sandbox_mode=' + json.dumps(mode),
        '-c', 'sandbox_workspace_write.writable_roots=' + json.dumps([str(requests)]),
        'sandbox', '--', 'python', '-c', checks + writes,
    ], cwd=repo, check=True)
    print('PASS', mode, 'reads, workspace/request writes, Git protection, outside-write and network denial', flush=True)
'''

subprocess.run(
    [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--security-opt",
        "seccomp=" + str(Path(__file__).resolve().parents[1] / "runtime/codex-seccomp.json"),
        "--entrypoint",
        "python",
        os.environ.get("FACTORY_IMAGE", "openhands-factory:dev"),
        "-c",
        PROBE,
    ],
    check=True,
)
