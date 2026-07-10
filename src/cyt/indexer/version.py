"""cyt-indexer native library version."""

from __future__ import annotations

from cyt_indexer import get_version as _get_version

__all__ = ["get_indexer_version"]


def get_indexer_version() -> str:
    """Return the cyt-indexer-sdk (Rust core) version string."""
    return str(_get_version())
