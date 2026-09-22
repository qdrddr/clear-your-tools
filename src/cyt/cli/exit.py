"""Clean CLI exit handling for user interrupts."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

INTERRUPTED_EXIT_CODE = 130
QUIET_STOP_EXIT_CODE = 1

_T = TypeVar("_T")


def parse_quiet_cli_flags(argv: list[str]) -> tuple[bool, Path | None]:
    """Parse ``--verbose`` and ``--config PATH`` from trailing stop flags."""
    verbose = False
    config_path: Path | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--verbose":
            verbose = True
            index += 1
            continue
        if token == "--config" and index + 1 < len(argv):
            config_path = Path(argv[index + 1])
            index += 2
            continue
        index += 1
    return verbose, config_path


def run_main(main_fn: Callable[..., _T], /, *args: object, **kwargs: object) -> _T:
    """Run *main_fn* and exit silently on ``KeyboardInterrupt``."""
    try:
        return main_fn(*args, **kwargs)
    except KeyboardInterrupt:
        raise SystemExit(INTERRUPTED_EXIT_CODE) from None


def run_quiet_stop(main_fn: Callable[[], None], /, *, verbose: bool = False) -> None:
    """Run a stop handler; exit without a traceback when it fails."""
    try:
        main_fn()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(INTERRUPTED_EXIT_CODE) from None
    except Exception as exc:
        if verbose:
            print(f"cyt: {exc}", file=sys.stderr)
        raise SystemExit(QUIET_STOP_EXIT_CODE) from None
