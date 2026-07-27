"""Cloudflare MCP Portal transport: session lifecycle and tool catalog fetch."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MCP_PATH = "/mcp"
_MCP_PROTOCOL_VERSION = "2025-03-26"
_MCP_ACCEPT = "application/json, text/event-stream"
_CLIENT_INFO = {"name": "cyt-cloudflare-catalog", "version": "1.0.0"}

EXCLUDED_TOOL_NAMES = frozenset(
    {
        "portal_toggle_servers",
        "portal_toggle_single_server",
        "portal_list_servers",
        "portal_codemode_search",
        "portal_codemode_execute",
        "agw_reselect_servers",
    },
)


def filter_excluded_cloudflare_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop Cloudflare portal admin tools that must not enter pruning or hook injection."""
    return [
        tool for tool in tools if str(tool.get("name") or "").strip() not in EXCLUDED_TOOL_NAMES
    ]


def cloudflare_portal_base_url(portal_url: str) -> str:
    """Normalize configured portal URL to base (without trailing ``/mcp``)."""
    normalized = str(portal_url or "").strip().rstrip("/")
    if normalized.endswith("/mcp"):
        return normalized[: -len("/mcp")]
    return normalized


def _access_headers(client_id: str, client_secret: str) -> dict[str, str]:
    return {
        "Accept": _MCP_ACCEPT,
        "Content-Type": "application/json",
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }


def _parse_jsonrpc_body(response: httpx.Response) -> dict[str, Any] | None:
    content_type = (response.headers.get("content-type") or "").lower()
    text = response.text.strip()
    if not text:
        return None

    if "text/event-stream" in content_type or text.startswith(("event:", "data:")):
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    try:
        parsed = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _raise_jsonrpc_error(payload: dict[str, Any], *, context: str) -> None:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or str(error)
        raise ValueError(f"cloudflare MCP {context} error: {message}")


async def _post_mcp(
    client: httpx.AsyncClient,
    *,
    body: dict[str, Any],
    session_id: str | None = None,
) -> tuple[httpx.Response, dict[str, Any] | None]:
    headers: dict[str, str] = {}
    if session_id:
        headers["mcp-session-id"] = session_id
    response = await client.post(_MCP_PATH, json=body, headers=headers)
    response.raise_for_status()
    return response, _parse_jsonrpc_body(response)


async def _initialize_session(client: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
    response, payload = await _post_mcp(
        client,
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        },
    )
    session_id = response.headers.get("mcp-session-id")
    if not session_id:
        raise ValueError("cloudflare MCP initialize missing mcp-session-id header")
    if payload is None:
        raise ValueError("cloudflare MCP initialize returned empty body")
    _raise_jsonrpc_error(payload, context="initialize")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("cloudflare MCP initialize missing result")
    return session_id, result


async def _notify_initialized(client: httpx.AsyncClient, *, session_id: str) -> None:
    response, _payload = await _post_mcp(
        client,
        body={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        session_id=session_id,
    )
    if response.status_code not in (200, 202, 204):
        response.raise_for_status()


async def _tools_list(client: httpx.AsyncClient, *, session_id: str) -> list[dict[str, Any]]:
    _response, payload = await _post_mcp(
        client,
        body={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
        session_id=session_id,
    )
    if payload is None:
        raise ValueError("cloudflare MCP tools/list returned empty body")
    _raise_jsonrpc_error(payload, context="tools/list")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("cloudflare MCP tools/list missing result")
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise ValueError("cloudflare MCP tools/list missing tools array")
    return [tool for tool in tools if isinstance(tool, dict)]


async def _tools_call(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    request_id: int = 3,
) -> dict[str, Any]:
    _response, payload = await _post_mcp(
        client,
        body={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
            },
        },
        session_id=session_id,
    )
    if payload is None:
        raise ValueError(f"cloudflare MCP tools/call {name} returned empty body")
    _raise_jsonrpc_error(payload, context=f"tools/call {name}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"cloudflare MCP tools/call {name} missing result")
    return result


def infer_server_id_from_tool_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if not name or "_" not in name:
        return ""
    return name.split("_", 1)[0]


def normalize_cloudflare_tool(raw: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(raw.get("name") or "").strip()
    if not tool_name or tool_name in EXCLUDED_TOOL_NAMES:
        return None
    schema = raw.get("inputSchema")
    if not isinstance(schema, dict):
        schema = raw.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    normalized: dict[str, Any] = {
        "name": tool_name,
        "description": str(raw.get("description") or "").strip(),
        "input_schema": schema,
        "cloudflare_server_id": infer_server_id_from_tool_name(tool_name),
    }
    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        normalized["title"] = title.strip()
    annotations = raw.get("annotations")
    if isinstance(annotations, dict):
        normalized["annotations"] = annotations
    execution = raw.get("execution")
    if isinstance(execution, dict):
        normalized["execution"] = execution
    output_schema = raw.get("outputSchema")
    if isinstance(output_schema, dict):
        normalized["output_schema"] = output_schema
    return normalized


def normalize_cloudflare_tools(raw_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in raw_tools:
        tool = normalize_cloudflare_tool(raw)
        if tool is not None:
            normalized.append(tool)
    return normalized


async def fetch_cloudflare_tools_list_async(
    *,
    portal_url: str,
    client_id: str,
    client_secret: str,
) -> list[dict[str, Any]]:
    """Run MCP initialize → initialized → tools/list; return normalized tools."""
    base_url = cloudflare_portal_base_url(portal_url)
    if not base_url:
        raise ValueError("cloudflare portal URL is not configured")
    timeout = httpx.Timeout(60.0, connect=10.0)
    headers = _access_headers(client_id, client_secret)
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
    ) as client:
        session_id, _init_result = await _initialize_session(client)
        await _notify_initialized(client, session_id=session_id)
        raw_tools = await _tools_list(client, session_id=session_id)
    tools = normalize_cloudflare_tools(raw_tools)
    logger.info("cloudflare MCP tools/list fetched count=%d", len(tools))
    return tools


async def fetch_portal_list_servers_async(
    *,
    portal_url: str,
    client_id: str,
    client_secret: str,
) -> list[dict[str, Any]]:
    """Internal health probe via ``tools/call portal_list_servers``."""
    base_url = cloudflare_portal_base_url(portal_url)
    if not base_url:
        raise ValueError("cloudflare portal URL is not configured")
    timeout = httpx.Timeout(60.0, connect=10.0)
    headers = _access_headers(client_id, client_secret)
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
    ) as client:
        session_id, _init_result = await _initialize_session(client)
        await _notify_initialized(client, session_id=session_id)
        result = await _tools_call(
            client,
            session_id=session_id,
            name="portal_list_servers",
            arguments={},
        )
    return parse_portal_list_servers_result(result)


def parse_portal_list_servers_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``portal_list_servers`` JSON-RPC result into server records."""
    payloads = _portal_list_payloads(result)
    servers_raw = _portal_list_servers_raw(payloads, result)
    return _normalize_portal_server_records(servers_raw)


_PORTAL_LIST_SERVER_LINE = re.compile(r"^\s*-\s+[^(]+\(([^)]+)\):\s*(.+)$")


def _parse_portal_list_servers_text(text: str) -> list[dict[str, Any]]:
    """Parse human-readable ``portal_list_servers`` text into server records."""
    servers: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _PORTAL_LIST_SERVER_LINE.match(line.strip())
        if not match:
            continue
        server_id = match.group(1).strip()
        if not server_id:
            continue
        status = match.group(2).strip().lower()
        enabled = "enabled" in status and "disabled" not in status
        servers.append({"id": server_id, "enabled": enabled, "name": server_id})
    return servers


def _portal_list_payloads(result: dict[str, Any]) -> list[Any]:
    payloads: list[Any] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                try:
                    payloads.append(json.loads(text))
                except json.JSONDecodeError:
                    payloads.append(text)
            else:
                payloads.append(item)
    structured = result.get("structuredContent")
    if structured is not None:
        payloads.append(structured)
    return payloads


def _portal_list_servers_raw(payloads: list[Any], result: dict[str, Any]) -> list[Any]:
    servers_raw: list[Any] = []
    for payload in payloads:
        if isinstance(payload, dict):
            servers = payload.get("servers")
            if isinstance(servers, list):
                servers_raw.extend(servers)
        elif isinstance(payload, list):
            servers_raw.extend(payload)
        elif isinstance(payload, str):
            servers_raw.extend(_parse_portal_list_servers_text(payload))
    if servers_raw:
        return servers_raw
    servers = result.get("servers")
    if isinstance(servers, list):
        return servers
    return []


def _normalize_portal_server_records(servers_raw: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in servers_raw:
        if not isinstance(item, dict):
            continue
        server_id = str(
            item.get("id") or item.get("server_id") or item.get("name") or "",
        ).strip()
        if not server_id:
            continue
        enabled = item.get("enabled")
        if enabled is None:
            enabled = item.get("is_enabled", True)
        normalized.append(
            {
                "id": server_id,
                "enabled": bool(enabled),
                "name": str(item.get("name") or server_id),
            },
        )
    return normalized
