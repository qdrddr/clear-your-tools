"""Runtime platform detection helpers (avoid mypy unreachable on sys.platform)."""

from __future__ import annotations

import sys


def is_windows() -> bool:
    """Return True when running on Windows."""
    return sys.platform == "win32"


def is_linux() -> bool:
    """Return True when running on Linux."""
    return sys.platform == "linux"


def is_macos() -> bool:
    """Return True when running on macOS."""
    return sys.platform == "darwin"
