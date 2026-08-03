"""stdio and streamable HTTP runners."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from cyt_mcp.catalog import catalog_payload
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache


async def refresh_runtime_cache(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    await server.list_tools()


def register_catalog_route(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    path = config.http.catalog_path

    @server.custom_route(path, methods=["GET"])
    async def _catalog_handler(_request: Request) -> JSONResponse:
        payload = catalog_payload(cache, agent=config.agent)
        return JSONResponse(payload)

    _ = _catalog_handler


async def run_http(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    register_catalog_route(server, cache, config)
    await server.run_http_async(
        host=config.http.host,
        port=config.http.port,
        path=config.http.mcp_path,
    )


def run_stdio(server: FastMCP) -> None:
    server.run("stdio")
