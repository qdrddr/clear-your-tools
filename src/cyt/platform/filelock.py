"""Cross-platform advisory file locking for debug JSON append."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from cyt.platform.compat import is_windows


@contextmanager
def exclusive_file_lock(fd: int) -> Iterator[None]:
    """Take an exclusive lock on an open file descriptor."""
    if not try_exclusive_file_lock(fd):
        if is_windows():
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    try:
        yield
    finally:
        if is_windows():
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def release_exclusive_file_lock(fd: int) -> None:
    """Release an exclusive lock held on *fd*."""
    if is_windows():
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def try_exclusive_file_lock(fd: int) -> bool:
    """Try to take an exclusive lock without blocking."""
    if is_windows():
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        return True
    except BlockingIOError:
        return False
