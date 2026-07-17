"""PolicyContext helpers for hook tool catalogs."""

from __future__ import annotations

import logging
from typing import Any, Literal

from cyt.indexer.policies import PolicyContext, apply_tool_kind

logger = logging.getLogger(__name__)

ToolKindOverride = Literal["mcp"]


def apply_executor_tool_kind(ctx: PolicyContext, kind: ToolKindOverride) -> PolicyContext:
    """Classify all tools in this prune session as MCP (executor hook catalogs)."""
    return apply_tool_kind(ctx, kind)


def prepare_hook_executor_tool_pruning(
    config: dict[str, Any] | None,
    *contexts: PolicyContext | None,
) -> dict[str, Any] | None:
    """Hook + tools_from executor: classify all tools as MCP and warm MCP cache.

    Sets tool_kind=mcp on every non-None PolicyContext (scoring + output + reinstate).
    Loads MCP cache from memory/disk and schedules SWR refresh; never hits the live
    executor API on the request path. Returns the cache dict, or None when inactive.
    """
    from cyt.config import load_config, uses_executor_tool_catalog

    cfg = config or load_config()
    if not uses_executor_tool_catalog(cfg):
        return None

    for ctx in contexts:
        if ctx is not None:
            apply_executor_tool_kind(ctx, "mcp")

    try:
        from cyt.executor.http import get_executor_mcp_cache

        return get_executor_mcp_cache(cfg, allow_prompt=False)
    except Exception as exc:
        logger.debug("executor MCP cache warm skipped: %s", exc)
        return None
