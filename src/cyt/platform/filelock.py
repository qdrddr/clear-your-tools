"""Cross-platform advisory file locking for debug JSON append."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from cyt.platform.compat import is_windows

# Windows ``msvcrt.locking`` modes (stable since Python 2).
_WIN_LK_LOCK = 1
_WIN_LK_NBLCK = 2
_WIN_LK_UNLCK = 3


def _win_lock(fd: int, mode: int, length: int = 1) -> None:
    import msvcrt

    msvcrt.locking(fd, mode, length)  # type: ignore[attr-defined]


@contextmanager
def exclusive_file_lock(fd: int) -> Iterator[None]:
    """Take an exclusive lock on an open file descriptor."""
    if not try_exclusive_file_lock(fd):
        if is_windows():
            _win_lock(fd, _WIN_LK_LOCK)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        if is_windows():
            _win_lock(fd, _WIN_LK_UNLCK)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)


def release_exclusive_file_lock(fd: int) -> None:
    """Release an exclusive lock held on *fd*."""
    if is_windows():
        _win_lock(fd, _WIN_LK_UNLCK)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def try_exclusive_file_lock(fd: int) -> bool:
    """Try to take an exclusive lock without blocking."""
    if is_windows():
        try:
            _win_lock(fd, _WIN_LK_NBLCK)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
