"""App-owned SDK runtime overrides for scores and default policies."""

from __future__ import annotations

DECOMPOSED_SCORE: float = 0.5
ENUM_SCORE: float = 0.2
RERANK_SCORE: float = 0.003
EMPTY_OPTIONAL_FALLBACK_K: int = 3
LLM_SCORE: int = 30
LLM_ENUM_SCORE: int = 30


def configure_sdk_runtime_defaults() -> None:
    """Push app runtime overrides into cyt-indexer (Rust core)."""
    from cyt_core.bootstrap import configure_sdk_runtime_defaults as _configure

    _configure()
