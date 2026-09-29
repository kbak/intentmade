"""Controller writes into job storage without following worker-created links."""

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


# [impl->req~im-safe-controller-writes~1]
@contextmanager
def parent(root, path):
    relative = Path(path).relative_to(root)
    if not relative.parts or ".." in relative.parts:
        raise ValueError("Job file must stay inside its root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, flags)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory, relative.name
    finally:
        os.close(directory)


def mkdir(root, path):
    with parent(root, path) as (directory, name):
        os.mkdir(name, dir_fd=directory)


# [impl->req~im-safe-controller-writes~1]
def write_text(root, path, text):
    # Write a new inode, then replace the directory entry itself. Never truncate
    # an existing inode: it may be a symlink, FIFO or hardlink planted by a worker.
    with parent(root, path) as (directory, name):
        temporary = ".factory-write-" + uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
