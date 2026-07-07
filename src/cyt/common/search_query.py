"""Shared search-query formatting for tool and skill pruning."""

from __future__ import annotations

__all__ = ["format_search_query"]


def format_search_query(user_query: str, assistant_message: str | None = None) -> str:
    """Combine user and assistant turns for bm25/rerank/llm tool search."""
    base = f"User_Asks: {user_query}"
    if assistant_message:
        return f"{base}; Assistant_Says: {assistant_message}"
    return base
