"""Fetch MCP transport payloads from the Executor ``/mcp`` endpoint."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MCP_PATH = "/mcp"
_MCP_PROTOCOL_VERSION = "2025-03-26"
_MCP_ACCEPT = "application/json, text/event-stream"
_CLIENT_INFO = {"name": "cyt-executor-catalog", "version": "1.0.0"}


def _auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _mcp_headers(token: str | None, *, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": _MCP_ACCEPT,
        "Content-Type": "application/json",
        **_auth_headers(token),
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _parse_jsonrpc_body(response: httpx.Response) -> dict[str, Any] | None:
    """Parse JSON or SSE (``data: {...}``) JSON-RPC body into a dict."""
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
        raise ValueError(f"executor MCP {context} error: {message}")


async def _post_mcp(
    client: httpx.AsyncClient,
    *,
    body: dict[str, Any],
    session_id: str | None = None,
) -> tuple[httpx.Response, dict[str, Any] | None]:
    headers = {}
    if session_id:
        headers["mcp-session-id"] = session_id
    response = await client.post(_MCP_PATH, json=body, headers=headers)
    response.raise_for_status()
    return response, _parse_jsonrpc_body(response)


async def _initialize_session(
    client: httpx.AsyncClient,
) -> tuple[str, dict[str, Any]]:
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
        raise ValueError("executor MCP initialize missing mcp-session-id header")
    if payload is None:
        raise ValueError("executor MCP initialize returned empty body")
    _raise_jsonrpc_error(payload, context="initialize")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("executor MCP initialize missing result")
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
    # 202 Accepted with empty body is expected.
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
        raise ValueError("executor MCP tools/list returned empty body")
    _raise_jsonrpc_error(payload, context="tools/list")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("executor MCP tools/list missing result")
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise ValueError("executor MCP tools/list missing tools array")
    return [tool for tool in tools if isinstance(tool, dict)]


def _skill_text_from_call_result(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("executor MCP skills(execute) missing result")
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise ValueError("executor MCP skills(execute) missing content")
    first = content[0]
    if not isinstance(first, dict):
        raise ValueError("executor MCP skills(execute) content[0] invalid")
    text = first.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("executor MCP skills(execute) missing text")
    return text


async def _skills_execute(client: httpx.AsyncClient, *, session_id: str) -> str:
    _response, payload = await _post_mcp(
        client,
        body={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "skills",
                "arguments": {"name": "execute"},
            },
        },
        session_id=session_id,
    )
    if payload is None:
        raise ValueError("executor MCP skills(execute) returned empty body")
    _raise_jsonrpc_error(payload, context="skills(execute)")
    return _skill_text_from_call_result(payload)


async def fetch_executor_mcp_cache_async(
    *,
    base_url: str,
    token: str | None,
) -> dict[str, Any]:
    """Initialize MCP session, then cache ``tools/list`` + ``skills(execute)``.

    Returns the object stored under the disk envelope ``executor`` key::

        {
          "tools_list": [...],
          "execute_skill": "<markdown>"
        }
    """
    timeout = httpx.Timeout(60.0, connect=10.0)
    headers = _mcp_headers(token)
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    ) as client:
        session_id, _init_result = await _initialize_session(client)
        await _notify_initialized(client, session_id=session_id)
        tools_list = await _tools_list(client, session_id=session_id)
        execute_skill = await _skills_execute(client, session_id=session_id)

    logger.info(
        "executor MCP cache fetched tools_list=%d execute_skill_chars=%d",
        len(tools_list),
        len(execute_skill),
    )
    return {
        "tools_list": tools_list,
        "execute_skill": execute_skill,
    }


def find_execute_tool(tools_list: list[Any] | None) -> dict[str, Any] | None:
    """Return the MCP ``execute`` tool entry from a cached ``tools_list``.

    Accepts ``list[Any]`` because cached JSON may contain non-dict entries.
    """
    if not tools_list:
        return None
    for tool in tools_list:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("name") or "").strip() == "execute":
            return tool
    return None


def normalize_mcp_tool_for_inject(tool: dict[str, Any]) -> dict[str, Any]:
    """Map MCP tool shape (``inputSchema``) to inject shape (``input_schema``)."""
    normalized: dict[str, Any] = {"name": str(tool.get("name") or "").strip()}
    description = tool.get("description")
    if description is not None:
        normalized["description"] = str(description)
    schema = tool.get("input_schema")
    if schema is None:
        schema = tool.get("inputSchema")
    if isinstance(schema, dict):
        normalized["input_schema"] = schema
    else:
        normalized["input_schema"] = {}
    return normalized


def format_executor_mcp_selector_appendix(executor_mcp: dict[str, Any] | None) -> str:
    """Format cached execute tool + skill for appending to the LLM selector system prompt.

    Uses the same minimized single-quote JSON encoding as tool injection.
    Returns ``""`` when the cache is missing the execute tool / skill.
    """
    if not isinstance(executor_mcp, dict):
        return ""

    tools_list = executor_mcp.get("tools_list")
    execute_tool = find_execute_tool(tools_list if isinstance(tools_list, list) else None)
    execute_skill = executor_mcp.get("execute_skill")
    skill_text = str(execute_skill).strip() if isinstance(execute_skill, str) else ""

    if execute_tool is None and not skill_text:
        return ""

    from cyt.tools.inject import format_tool_item

    parts: list[str] = [
        "Executor MCP transport context (use when selecting tools that require sandboxed "
        "TypeScript execution via the execute tool in 'code mode' to be able to access tools):",
    ]
    if execute_tool is not None:
        parts.append(format_tool_item(normalize_mcp_tool_for_inject(execute_tool)))
    if skill_text:
        parts.append("<execute-skill>")
        parts.append(skill_text)
        parts.append("</execute-skill>")
    return "\n".join(parts)
