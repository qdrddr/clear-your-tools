"""Keep `cyt hook --stdin` stdout reserved for the final JSON payload."""

from __future__ import annotations

import contextlib
import io
import os
import sys
from collections.abc import Iterator

from cyt.pruners.litellm_quiet import configure_litellm_quiet


def configure_hook_quiet() -> None:
    """Idempotently silence libraries that write progress or debug text to stdout."""
    configure_litellm_quiet()


@contextlib.contextmanager
def hook_quiet_stderr(*, active: bool = True) -> Iterator[None]:
    """Discard stderr during hook search so libraries cannot leak progress text."""
    if not active:
        yield
        return
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
def hook_safe_stdout(*, active: bool = True) -> Iterator[None]:
    """Redirect process stdout (fd 1) so only the hook JSON payload uses the real pipe."""
    if not active:
        yield
        return
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
