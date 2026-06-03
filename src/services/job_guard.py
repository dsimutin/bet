"""Process-wide job guard for idempotent manual and scheduled jobs."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator

_locks: dict[str, Lock] = {}
_registry_lock = Lock()


@contextmanager
def job_guard(job_name: str) -> Iterator[bool]:
    with _registry_lock:
        lock = _locks.setdefault(job_name, Lock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
