"""Assemble cyt-mcp FastMCP server."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from cyt_mcp.backends import mount_backend_servers
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import register_search_tool
from cyt_mcp.stubs import StubListTransform
from cyt_mcp.tool_list_notify import register_tool_list_changed_middleware

logger = logging.getLogger(__name__)


def build_aggregator(
    config: AggregatorConfig,
    cache: RuntimeToolCache,
) -> FastMCP:
    server = FastMCP("cyt-mcp")
    degraded = mount_backend_servers(server, config.mcp_servers)
    cache.replace(cache.snapshot(), degraded_servers=degraded)
    if not config.verify_only:
        register_search_tool(server, cache, agent=config.agent)
        server.add_transform(
            StubListTransform(
                cache,
                include_description=config.codex_stubs_include_description,
                deny_entries=config.mcp_deny,
            ),
        )
    if degraded:
        logger.warning("cyt-mcp: degraded backends: %s", ", ".join(degraded))
    register_tool_list_changed_middleware(server, cache, config)
    return server
