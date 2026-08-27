"""Path helpers for cyt-client (stdlib only)."""

from __future__ import annotations

import os
from pathlib import Path


def user_home() -> Path:
    """Return the user home directory, honoring ``HOME`` then ``USERPROFILE``."""
    for key in ("HOME", "USERPROFILE"):
        raw = os.environ.get(key)
        if raw:
            return Path(raw)
    return Path.home()


def expand_home_path(path: str) -> Path:
    """Expand a path that may start with ``~/`` using :func:`user_home`."""
    text = path.strip()
    if text.startswith("~/"):
        return user_home() / text[2:]
    return Path(text).expanduser()
