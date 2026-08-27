"""Runtime platform detection for cyt-client (stdlib only)."""

from __future__ import annotations

import sys


def is_windows() -> bool:
    """Return True when running on Windows."""
    return sys.platform == "win32"
