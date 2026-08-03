"""Assemble cyt-mcp FastMCP server."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from cyt_mcp.backends import mount_backend_servers
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import register_search_tool
from cyt_mcp.stubs import StubListTransform

logger = logging.getLogger(__name__)


def build_aggregator(
    config: AggregatorConfig,
    cache: RuntimeToolCache,
) -> FastMCP:
    server = FastMCP("cyt-mcp")
    degraded = mount_backend_servers(server, config.mcp_servers)
    cache.replace(cache.snapshot(), degraded_servers=degraded)
    register_search_tool(server, cache, agent=config.agent)
    server.add_transform(
        StubListTransform(cache, include_description=config.codex_stubs_include_description),
    )
    if degraded:
        logger.warning("cyt-mcp: degraded backends: %s", ", ".join(degraded))
    return server
