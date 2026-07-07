"""PolicyContext helpers for hook tool catalogs."""

from __future__ import annotations

from typing import Literal

from cyt_indexer.policies import PolicyContext, apply_tool_kind

ToolKindOverride = Literal["mcp"]


def apply_executor_tool_kind(ctx: PolicyContext, kind: ToolKindOverride) -> PolicyContext:
    """Classify all tools in this prune session as MCP (executor hook catalogs)."""
    return apply_tool_kind(ctx, kind)
