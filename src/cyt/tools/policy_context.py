"""PolicyContext helpers for hook tool catalogs."""

from __future__ import annotations

from typing import Literal

from cyt_indexer.policies import PolicyContext

ToolKindOverride = Literal["mcp"]


def apply_executor_tool_kind(ctx: PolicyContext, kind: ToolKindOverride) -> PolicyContext:
    """Classify all tools in this prune session as MCP (executor hook catalogs)."""
    ctx.tool_kind = kind
    return ctx
