"""Clean CLI exit handling for user interrupts."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

INTERRUPTED_EXIT_CODE = 130

_T = TypeVar("_T")


def run_main(main_fn: Callable[..., _T], /, *args: object, **kwargs: object) -> _T:
    """Run *main_fn* and exit silently on ``KeyboardInterrupt``."""
    try:
        return main_fn(*args, **kwargs)
    except KeyboardInterrupt:
        raise SystemExit(INTERRUPTED_EXIT_CODE) from None
