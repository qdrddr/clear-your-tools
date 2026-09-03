"""stdio and streamable HTTP runners."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from cyt_mcp.catalog import catalog_payload
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
    await refresh_catalog_cache(server, cache)


def register_catalog_route(
    server: FastMCP,
    cache: RuntimeToolCache,
    config: AggregatorConfig,
) -> None:
    path = config.http.catalog_path

    @server.custom_route(path, methods=["GET"])
    async def _catalog_handler(request: Request) -> JSONResponse:
        from cyt_mcp.aggregator import build_aggregator
        from cyt_mcp.catalog import merge_catalog_payloads
        from cyt_mcp.workspace_catalog import (
            parse_workspace_root,
            workspace_aggregator_path,
            workspace_server_defs_path,
        )

        payload = catalog_payload(cache, agent=config.agent)
        workspace_raw = request.query_params.get("workspace") or request.headers.get(
            "X-CYT-Workspace-Root",
        )
        workspace_root = parse_workspace_root(workspace_raw)
        if workspace_root is None:
            return JSONResponse(payload)

        defs_path = workspace_server_defs_path(workspace_root, config.agent)
        if defs_path is None:
            return JSONResponse(payload)

        from cyt_mcp.config import AggregatorConfig as AggCfg
        from cyt_mcp.config import load_http_settings, load_mcp_servers

        workspace_config_path = workspace_aggregator_path(workspace_root, config.agent)

        workspace_servers = load_mcp_servers(defs_path, workspace_folder=workspace_root)
        if not workspace_servers:
            return JSONResponse(payload)

        ws_config = AggCfg(
            agent=config.agent,
            mcp_servers=workspace_servers,
            transport=config.transport,
            http=load_http_settings({}),
            codex_stubs_include_description=config.codex_stubs_include_description,
            verify_only=config.verify_only,
            aggregator_path=workspace_config_path,
            agent_mcp_path=defs_path,
        )
        ws_cache = RuntimeToolCache()
        ws_server = build_aggregator(ws_config, ws_cache)
        await refresh_runtime_cache(ws_server, ws_cache, ws_config)
        workspace_payload = catalog_payload(ws_cache, agent=config.agent)
        merged = merge_catalog_payloads(payload, workspace_payload)
        return JSONResponse(merged)

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
    """Run stdio transport synchronously (standalone CLI only, not inside asyncio.run)."""
    server.run("stdio")
