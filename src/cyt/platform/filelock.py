"""Cross-platform advisory file locking for debug JSON append."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

from cyt.platform.compat import is_windows


def _msvcrt() -> ModuleType:
    return importlib.import_module("msvcrt")


def _win_lock(fd: int, mode: int, length: int = 1) -> None:
    _msvcrt().locking(fd, mode, length)


@contextmanager
def exclusive_file_lock(fd: int) -> Iterator[None]:
    """Take an exclusive lock on an open file descriptor."""
    if is_windows():
        _win_lock(fd, _msvcrt().LK_LOCK)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    try:
        yield
    finally:
        if is_windows():
            _win_lock(fd, _msvcrt().LK_UNLCK)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def release_exclusive_file_lock(fd: int) -> None:
    """Release an exclusive lock held on *fd*."""
    if is_windows():
        _win_lock(fd, _msvcrt().LK_UNLCK)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def try_exclusive_file_lock(fd: int) -> bool:
    """Try to take an exclusive lock without blocking."""
    if is_windows():
        try:
            _win_lock(fd, _msvcrt().LK_NBLCK)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        return True
    except BlockingIOError:
        return False
