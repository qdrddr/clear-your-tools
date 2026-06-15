"""Keep `cyt hook --stdin` stdout reserved for the final JSON payload."""

from __future__ import annotations

import contextlib
import io
import os
import sys
from collections.abc import Iterator

from cyt.pruners.litellm_quiet import configure_litellm_quiet

_bm25s_tqdm_patched = False


def _noop_tqdm(*args: object, **_kwargs: object) -> object:
    """bm25s-compatible tqdm stand-in that never writes progress to stdout."""
    return args[0] if args else None


def _silence_bm25s_tqdm() -> None:
    """Replace bm25s.tokenization.tqdm after import (no env vars)."""
    global _bm25s_tqdm_patched
    if _bm25s_tqdm_patched:
        return
    try:
        import bm25s.tokenization as tokenization
    except ImportError:
        return
    object.__setattr__(tokenization, "tqdm", _noop_tqdm)
    _bm25s_tqdm_patched = True


def configure_hook_quiet() -> None:
    """Idempotently silence libraries that write progress or debug text to stdout."""
    configure_litellm_quiet()
    _silence_bm25s_tqdm()


@contextlib.contextmanager
def hook_quiet_stderr() -> Iterator[None]:
    """Discard stderr during hook search so libraries cannot leak progress text."""
    real_stderr = sys.stderr
    devnull = open(os.devnull, "w")
    sys.stderr = devnull
    try:
        stderr_fd = real_stderr.fileno()
    except (AttributeError, io.UnsupportedOperation, ValueError):
        try:
            yield
        finally:
            sys.stderr = real_stderr
            devnull.close()
        return

    saved_fd = os.dup(stderr_fd)
    try:
        os.dup2(devnull.fileno(), stderr_fd)
        yield
    finally:
        os.dup2(saved_fd, stderr_fd)
        os.close(saved_fd)
        sys.stderr = real_stderr
        devnull.close()


@contextlib.contextmanager
def hook_safe_stdout() -> Iterator[None]:
    """Redirect process stdout (fd 1) so only the hook JSON payload uses the real pipe."""
    try:
        stdout_fd = sys.stdout.fileno()
    except (AttributeError, io.UnsupportedOperation, ValueError):
        real_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            yield
        finally:
            sys.stdout = real_stdout
        return

    saved_fd = os.dup(stdout_fd)
    try:
        os.dup2(sys.stderr.fileno(), stdout_fd)
        yield
    finally:
        os.dup2(saved_fd, stdout_fd)
        os.close(saved_fd)
