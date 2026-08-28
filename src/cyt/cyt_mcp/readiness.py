"""cyt-mcp availability checks for hook CLI and runtime."""

from __future__ import annotations

import sys
from typing import Any, Literal

from cyt.config import (
    load_config,
    needs_cyt_mcp_catalog,
    tools_hook_cyt_mcp_agent,
    tools_hook_cyt_mcp_catalog_url,
    tools_hook_cyt_mcp_executable,
)
from cyt.cyt_mcp.catalog import get_cyt_mcp_catalog
from cyt.cyt_mcp.cli import cyt_mcp_available, run_cyt_mcp_catalog_json

CytMcpCatalogProbe = Literal["ok", "empty", "unavailable"]

CYT_MCP_INSTALL_HINT = "Please install cyt-mcp: uv tool install 'clear-your-tools[cyt-mcp]'"


def probe_cyt_mcp_catalog(
    config: dict[str, Any] | None = None,
    *,
    quick: bool = False,
) -> CytMcpCatalogProbe | None:
    cfg = config or load_config()
    if not needs_cyt_mcp_catalog(cfg):
        return None

    catalog_url = tools_hook_cyt_mcp_catalog_url(cfg)
    executable = tools_hook_cyt_mcp_executable(cfg)
    if quick:
        if catalog_url:
            return "ok"
        if not cyt_mcp_available(executable):
            return "unavailable"
        return "ok"

    if catalog_url:
        tools = get_cyt_mcp_catalog(cfg, blocking=True)
        if tools is None:
            return "unavailable"
        return "ok" if tools else "empty"

    if not cyt_mcp_available(executable):
        return "unavailable"

    agent = tools_hook_cyt_mcp_agent(cfg)
    payload = run_cyt_mcp_catalog_json(executable, agent=agent)
    if payload is None:
        return "unavailable"
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        return "empty"
    return "ok"


def report_cyt_mcp_hook_readiness(
    config: dict[str, Any] | None = None,
    *,
    unattended: bool = False,
    quick: bool = False,
) -> None:
    if unattended:
        return
    probe = probe_cyt_mcp_catalog(config, quick=quick)
    if probe is None:
        return
    if probe == "unavailable":
        print(CYT_MCP_INSTALL_HINT, file=sys.stderr)
        return
    if probe == "empty":
        print(
            "cyt-mcp catalog is empty; configure backend MCP servers in "
            "~/.config/cyt/mcp/<agent>.json",
            file=sys.stderr,
        )


def cyt_mcp_hook_catalog_usable(
    config: dict[str, Any] | None = None,
    *,
    quick: bool = True,
) -> bool:
    return probe_cyt_mcp_catalog(config, quick=quick) == "ok"
