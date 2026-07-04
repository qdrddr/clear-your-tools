"""App-owned SDK BM25 overrides."""

from __future__ import annotations

__all__ = ["configure_sdk_bm25_defaults"]


def configure_sdk_bm25_defaults(config: dict | None = None) -> None:
    """Push app BM25 settings into cyt-indexer (Rust core)."""
    from cyt_core.bootstrap import configure_sdk_bm25_defaults as _configure

    _configure(config)
