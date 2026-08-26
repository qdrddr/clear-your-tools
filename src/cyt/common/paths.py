"""Path formatting helpers."""

from __future__ import annotations

from pathlib import Path

__all__ = ["shorten_home_path"]


def _path_starts_with_prefix(text: str, prefix: Path) -> bool:
    """Return True when *text* resolves under *prefix* (handles ``\\`` and ``/``)."""
    try:
        Path(text).expanduser().resolve().relative_to(prefix.resolve())
    except (OSError, ValueError):
        return False
    return True


def shorten_home_path(path: str) -> str:
    """Return ``path`` with the user home directory replaced by ``~``."""
    expanded = Path(path).expanduser()
    home = Path.home()
    try:
        rel = expanded.resolve().relative_to(home.resolve())
        return f"~/{rel.as_posix()}"
    except (OSError, ValueError):
        pass
    text = str(expanded)
    if _path_starts_with_prefix(text, home):
        try:
            rel = expanded.resolve().relative_to(home.resolve())
            return f"~/{rel.as_posix()}"
        except (OSError, ValueError):
            pass
    return text
