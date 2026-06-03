"""App-owned SDK runtime overrides for scores and default policies."""

from __future__ import annotations

from cyt.config import DEFAULT_MCP_TOOL_POLICY, DEFAULT_SYSTEM_TOOL_POLICY

DECOMPOSED_SCORE: float = 0.5
ENUM_SCORE: float = 0.2
RERANK_SCORE: float = 0.003
EMPTY_OPTIONAL_FALLBACK_K: int = 3


def configure_sdk_runtime_defaults() -> None:
    """Push app runtime overrides into cyt-indexer (Rust core)."""
    from cyt_indexer.runtime_defaults import configure_runtime_defaults

    configure_runtime_defaults(
        decomposed_score=DECOMPOSED_SCORE,
        enum_score=ENUM_SCORE,
        rerank_score=RERANK_SCORE,
        empty_optional_fallback_k=EMPTY_OPTIONAL_FALLBACK_K,
        default_system_policy=DEFAULT_SYSTEM_TOOL_POLICY,
        default_mcp_policy=DEFAULT_MCP_TOOL_POLICY,
    )
