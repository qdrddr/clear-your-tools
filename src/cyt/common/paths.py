"""Path formatting helpers."""

from __future__ import annotations

import os
from pathlib import Path

from cyt.platform.compat import is_windows

__all__ = ["expand_home_path", "shorten_home_path", "user_home"]


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


def _normalize_windows_path(path: Path) -> Path:
    if not is_windows():
        return path.resolve()
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return path.resolve()
    get_long_path_name = windll.kernel32.GetLongPathNameW
    if get_long_path_name(str(path), buffer, len(buffer)):
        return Path(buffer.value)
    return path.resolve()


def shorten_home_path(path: str) -> str:
    """Return ``path`` with the user home directory replaced by ``~``."""
    expanded = Path(path).expanduser()
    home = user_home()
    expanded_norm = _normalize_windows_path(expanded)
    home_norm = _normalize_windows_path(home)
    try:
        rel = expanded_norm.relative_to(home_norm)
        return f"~/{rel.as_posix()}"
    except (OSError, ValueError):
        pass
    return str(expanded)
