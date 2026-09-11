"""Assemble cyt-mcp FastMCP server."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from cyt_mcp.backends import mount_backend_servers
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import register_search_tool
from cyt_mcp.stubs import StubListTransform
from cyt_mcp.tool_list_notify import register_tool_list_changed_middleware

logger = logging.getLogger(__name__)


def build_aggregator(
    config_holder: ConfigHolder,
    cache: RuntimeToolCache,
) -> tuple[FastMCP, object]:
    config = config_holder.config
    server = FastMCP("cyt-mcp")
    degraded = mount_backend_servers(server, config.mcp_servers)
    cache.replace(cache.snapshot(), degraded_servers=degraded)
    list_changed_middleware = None
    if not config.verify_only:
        register_search_tool(server, cache, agent=config.agent)
        server.add_transform(
            StubListTransform(
                cache,
                retain=config.stub_retain,
                config_holder=config_holder,
            ),
        )
        list_changed_middleware = register_tool_list_changed_middleware(
            server,
            cache,
            config_holder,
        )
    if degraded:
        logger.warning("cyt-mcp: degraded backends: %s", ", ".join(degraded))
    return server, list_changed_middleware
