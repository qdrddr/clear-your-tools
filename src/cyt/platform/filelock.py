"""Cross-platform advisory file locking for debug JSON append."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from cyt.platform.compat import is_windows


@contextmanager
def exclusive_file_lock(fd: int) -> Iterator[None]:
    """Take an exclusive lock on an open file descriptor (no-op on Windows)."""
    if is_windows():
        yield
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
