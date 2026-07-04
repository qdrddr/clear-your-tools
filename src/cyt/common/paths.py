"""Path formatting helpers."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["shorten_home_path"]


def shorten_home_path(path: str) -> str:
    """Return ``path`` with the user home directory replaced by ``~``."""
    expanded = Path(path).expanduser()
    home = Path.home()
    try:
        rel = expanded.relative_to(home)
        return f"~/{rel.as_posix()}"
    except ValueError:
        text = str(expanded)
        home_env = os.environ.get("HOME")
        if home_env and text.startswith(home_env.rstrip("/") + "/"):
            home_prefix = home_env.rstrip("/")
            path_start = len(home_prefix) + 1
            return "~/" + text[path_start:]
        return text
