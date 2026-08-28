"""Cloudflare portal availability checks for hook CLI and runtime."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Literal

from cyt.cloudflare.mcp import fetch_cloudflare_tools_list_async
from cyt.cloudflare.runtime import (
    load_config,
    resolve_credential,
    tools_hook_cloudflare_access_client_id_var,
    tools_hook_cloudflare_access_client_secret_var,
    tools_hook_cloudflare_url,
    uses_cloudflare_tool_catalog,
)

CloudflarePortalProbe = Literal["ok", "empty", "unavailable"]

CF_ACCESS_HINT = (
    "Configure Cloudflare Access service token env vars "
    "(CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET) and cloudflare_url in config."
)
CF_EMPTY_TOOLS_HINT = (
    "Cloudflare portal returned no upstream tools after filtering portal meta-tools."
)


def _resolve_credentials(config: dict[str, Any]) -> tuple[str | None, str | None]:
    client_id_var = tools_hook_cloudflare_access_client_id_var(config)
    secret_var = tools_hook_cloudflare_access_client_secret_var(config)
    client_id, _ = resolve_credential(client_id_var, allow_prompt=False)
    client_secret, _ = resolve_credential(secret_var, allow_prompt=False)
    return client_id, client_secret


def probe_cloudflare_portal(
    config: dict[str, Any] | None = None,
    *,
    quick: bool = False,
) -> CloudflarePortalProbe | None:
    cfg = config or load_config()
    if not uses_cloudflare_tool_catalog(cfg):
        return None
    portal_url = tools_hook_cloudflare_url(cfg)
    if not portal_url:
        return "unavailable"
    client_id, client_secret = _resolve_credentials(cfg)
    if not client_id or not client_secret:
        return "unavailable"
    if quick:
        return "ok"
    try:
        tools = asyncio.run(
            fetch_cloudflare_tools_list_async(
                portal_url=portal_url,
                client_id=client_id,
                client_secret=client_secret,
            ),
        )
    except Exception:
        return "unavailable"
    if not tools:
        return "empty"
    return "ok"


def report_cloudflare_hook_readiness(
    config: dict[str, Any] | None = None,
    *,
    unattended: bool = False,
    quick: bool = False,
) -> None:
    if unattended:
        return
    probe = probe_cloudflare_portal(config, quick=quick)
    if probe is None:
        return
    if probe == "unavailable":
        print(CF_ACCESS_HINT, file=sys.stderr)
        return
    if probe == "empty":
        print(CF_EMPTY_TOOLS_HINT, file=sys.stderr)


def cloudflare_hook_catalog_usable(config: dict[str, Any] | None = None) -> bool:
    """True when a cloudflare catalog can be loaded without a live MCP preflight fetch."""
    cfg = config or load_config()
    if not uses_cloudflare_tool_catalog(cfg):
        return False
    if not tools_hook_cloudflare_url(cfg):
        return False
    from cyt.cloudflare.catalog import cloudflare_catalog_available_locally

    if cloudflare_catalog_available_locally(cfg):
        return True
    client_id, client_secret = _resolve_credentials(cfg)
    return bool(client_id and client_secret)
