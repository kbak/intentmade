"""Dispose isolated job files only after their work has been retained."""

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from common import DATA
from resource_limits import require_disk_space

# Run after all job processes have stopped. dir_fd operations do not follow
# worker-created links outside the only mounted directory.
CLEAN = """import os, shutil, stat
fd = os.open('/cleanup', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
for name in os.listdir(fd):
    if stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
        shutil.rmtree(name, dir_fd=fd)
    else:
        os.unlink(name, dir_fd=fd)
os.close(fd)
"""


def remove_job(root):
    try:
        shutil.rmtree(root)
    except PermissionError:
        # Root-owned test outputs are common with Docker bind mounts. Give a
        # disposable helper access to this job only, never the parent store.
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "DAC_OVERRIDE",
                "--user",
                "0",
                "--mount",
                f"type=bind,src={root},dst=/cleanup",
                "--entrypoint",
                "python",
                os.environ.get("FACTORY_IMAGE", "intentmade:dev"),
                "-c",
                CLEAN,
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        root.rmdir()


@contextmanager
def job_directory(data=None, artifact=None):
    require_disk_space(data or DATA)
    root = Path(tempfile.mkdtemp(dir=data or DATA, prefix="job-"))
    try:
        yield root
    except BaseException:
        # Export/retention failures must not delete the only remaining copy.
        print(f"Workspace retained for recovery: {root}", flush=True)
        if artifact:
            (artifact / "recovery-workspace.txt").write_text(str(root))
        raise
    else:
        try:
            remove_job(root)
        except Exception as exc:
            # A housekeeping error must not hide validation results or prevent
            # independent review/repair. Leave a concrete cleanup receipt.
            warning = f"Cleanup deferred for {root}: {type(exc).__name__}"
            print(warning, flush=True)
            if artifact:
                with (artifact / "cleanup-warnings.log").open("a") as handle:
                    handle.write(warning + "\n")
