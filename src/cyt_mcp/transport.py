"""stdio and streamable HTTP runners."""

from __future__ import annotations

from fastmcp import FastMCP

from cyt_mcp.catalog_build import refresh_catalog_cache
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache


async def refresh_runtime_cache(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    # Single backend fetch via _list_tools(); do not also call list_tools() —
    # that repeats every stdio handshake and applies StubListTransform to a
    # result we discard anyway.
    await refresh_catalog_cache(server, cache, config)


async def run_http(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    await server.run_http_async(
        host=config.http.host,
        port=config.http.port,
        path=config.http.mcp_path,
    )


def run_stdio(server: FastMCP) -> None:
    """Run stdio transport synchronously (standalone CLI only, not inside asyncio.run)."""
    server.run("stdio")
