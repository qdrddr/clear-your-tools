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


def prepare_hook_mcpc_tool_pruning(
    config: dict[str, Any] | None,
    *contexts: PolicyContext | None,
) -> None:
    """Hook + tools_from mcpc: classify all tools as MCP."""
    from cyt.config import load_config, uses_mcpc_tool_catalog

    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg):
        return

    for ctx in contexts:
        if ctx is not None:
            apply_executor_tool_kind(ctx, "mcp")


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


def prepare_hook_cloudflare_tool_pruning(
    config: dict[str, Any] | None,
    *contexts: PolicyContext,
) -> None:
    from cyt.config import load_config, uses_cloudflare_tool_catalog

    cfg = config or load_config()
    if not uses_cloudflare_tool_catalog(cfg):
        return
    for ctx in contexts:
        if ctx is not None:
            apply_executor_tool_kind(ctx, "mcp")


def prepare_hook_cyt_mcp_tool_pruning(
    config: dict[str, Any] | None,
    *contexts: PolicyContext | None,
) -> None:
    """Classify cyt_mcp catalog chunks as MCP for partition/prune (hook or proxy)."""
    from cyt.config import load_config, tools_enabled, tools_hook_sources

    cfg = config or load_config()
    if not tools_enabled(cfg) or "cyt_mcp" not in tools_hook_sources(cfg):
        return
    for ctx in contexts:
        if ctx is not None:
            apply_executor_tool_kind(ctx, "mcp")


def prepare_hook_tool_pruning(
    config: dict[str, Any] | None,
    *contexts: PolicyContext | None,
) -> dict[str, Any] | None:
    """Classify hook catalog tools as MCP; warm executor transport cache when applicable."""
    from cyt.config import (
        load_config,
        uses_cloudflare_tool_catalog,
        uses_executor_tool_catalog,
        uses_mcpc_tool_catalog,
    )

    cfg = config or load_config()
    executor_cache: dict[str, Any] | None = None
    prepare_hook_cyt_mcp_tool_pruning(cfg, *contexts)
    if uses_mcpc_tool_catalog(cfg):
        prepare_hook_mcpc_tool_pruning(cfg, *contexts)
    if uses_cloudflare_tool_catalog(cfg):
        prepare_hook_cloudflare_tool_pruning(cfg, *contexts)
    if uses_executor_tool_catalog(cfg):
        executor_cache = prepare_hook_executor_tool_pruning(cfg, *contexts)
    return executor_cache
