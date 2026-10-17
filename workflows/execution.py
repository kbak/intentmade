"""Cross-process task exclusion and operator-owned worker capacity."""

import hashlib
import time
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar

import common
import deployment
from common import lock

_ACTIVE = ContextVar("factory_execution", default=frozenset())


def identity(config):
    return (config.get("repository") or "fixture:" + config["project"]).casefold()


def task_key(config, task):
    return hashlib.sha256((identity(config) + "\0" + task).encode()).hexdigest()


@contextmanager
def state(config, name):
    """Short task-record mutation; scans skip records owned by active work."""
    key = task_key(config, name)
    if key in _ACTIVE.get():
        yield
    else:
        with lock("task-" + key):
            yield


@contextmanager
def _slot(pool, limit):
    import fcntl

    directory = common.DATA / "locks" / "capacity" / pool
    directory.mkdir(parents=True, exist_ok=True)
    free, busy = None, 0
    try:
        for path in sorted(directory.glob("*.lock")):
            handle = path.open("a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                busy += 1
                handle.close()
            else:
                if free is None:
                    free = handle
                else:
                    handle.close()
        # Count every active slot, including ones above a newly lowered limit.
        if busy >= limit:
            raise BlockingIOError("Worker capacity is full")
        if free is None:
            path = directory / f"{len(list(directory.glob('*.lock')))}.lock"
            free = path.open("a")
            fcntl.flock(free, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        if free is not None:
            free.close()


# [impl->req~im-repository-locks~1]
@contextmanager
def task(configs, name, *, wait=True, wait_task=False):
    """One task owns its stores; independent tasks share bounded worker slots."""
    keys = frozenset(task_key(config, name) for config in configs)
    active = _ACTIVE.get()
    if keys <= active:
        yield
        return
    if active:
        raise ValueError("Nested execution cannot add repositories or change task identity")
    with ExitStack() as held:
        last_notice = float("-inf")
        while True:
            ownership = ExitStack()
            try:
                for key in sorted(keys):
                    ownership.enter_context(lock("task-" + key))
                break
            except BlockingIOError:
                ownership.close()
                if not wait_task:
                    raise
                from reporting import phase

                if time.monotonic() - last_notice >= 15:
                    phase(f"Task {name}: queued behind the same task")
                    last_notice = time.monotonic()
                time.sleep(0.5)
            except BaseException:
                ownership.close()
                raise
        held.enter_context(ownership)
        last_notice = float("-inf")
        while True:
            capacity = ExitStack()
            try:
                options = deployment.validate_concurrency(
                    deployment.settings().get("concurrency", {})
                )
                with lock("execution-admission", blocking=True):
                    capacity.enter_context(_slot("deployment", options["max_tasks"]))
                    repos = {identity(config): config for config in configs}
                    for repo, config in sorted(repos.items()):
                        limit = options["repositories"].get(
                            config["project"], options["max_tasks_per_repository"]
                        )
                        capacity.enter_context(
                            _slot(hashlib.sha256(repo.encode()).hexdigest(), limit)
                        )
                break
            except BlockingIOError:
                capacity.close()
                if not wait:
                    raise
                if time.monotonic() - last_notice >= 15:
                    from reporting import phase

                    phase(f"Task {name}: queued for worker capacity")
                    last_notice = time.monotonic()
                time.sleep(0.5)
            except BaseException:
                capacity.close()
                raise
        held.enter_context(capacity)
        token = _ACTIVE.set(keys)
        try:
            yield
        finally:
            _ACTIVE.reset(token)
