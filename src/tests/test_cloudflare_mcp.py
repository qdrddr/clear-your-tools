"""Cloudflare MCP transport helpers."""

from __future__ import annotations

import pytest

from cyt.cloudflare.mcp import (
    EXCLUDED_TOOL_NAMES,
    _access_headers,
    cloudflare_portal_base_url,
    fetch_cloudflare_tools_list_async,
    infer_server_id_from_tool_name,
    normalize_cloudflare_tool,
    normalize_cloudflare_tools,
    parse_portal_list_servers_result,
)


def test_cloudflare_portal_base_url_strips_mcp_suffix() -> None:
    assert cloudflare_portal_base_url("https://mcp.example.com/mcp") == "https://mcp.example.com"
    assert cloudflare_portal_base_url("https://mcp.example.com") == "https://mcp.example.com"


def test_normalize_cloudflare_tool_filters_excluded_names() -> None:
    assert normalize_cloudflare_tool({"name": "portal_list_servers"}) is None
    tool = normalize_cloudflare_tool(
        {
            "name": "context7_query-docs",
            "description": "Query docs",
            "inputSchema": {"type": "object"},
        },
    )
    assert tool is not None
    assert tool["name"] == "context7_query-docs"
    assert tool["input_schema"] == {"type": "object"}
    assert tool["cloudflare_server_id"] == "context7"


def test_normalize_cloudflare_tools_drops_all_excluded() -> None:
    raw = [{"name": name, "inputSchema": {}} for name in EXCLUDED_TOOL_NAMES]
    raw.append({"name": "deepwiki_ask_question", "inputSchema": {}})
    tools = normalize_cloudflare_tools(raw)
    assert len(tools) == 1
    assert tools[0]["name"] == "deepwiki_ask_question"


def test_infer_server_id_from_tool_name() -> None:
    assert infer_server_id_from_tool_name("context7_query-docs") == "context7"
    assert infer_server_id_from_tool_name("single") == ""


def test_parse_portal_list_servers_result_from_text_content() -> None:
    result = {
        "content": [
            {
                "type": "text",
                "text": '{"servers":[{"id":"context7","enabled":true},{"id":"deepwiki","enabled":false}]}',
            },
        ],
    }
    servers = parse_portal_list_servers_result(result)
    assert len(servers) == 2
    assert servers[0]["id"] == "context7"
    assert servers[0]["enabled"] is True
    assert servers[1]["enabled"] is False


def test_access_headers_use_resolved_credential_values() -> None:
    resolved_id = "resolved-client-id"
    resolved_token = "test-ci-stub"  # pragma: allowlist secret
    headers = _access_headers(resolved_id, resolved_token)
    assert headers["CF-Access-Client-Id"] == resolved_id
    assert headers["CF-Access-Client-Secret"] == resolved_token
    assert headers["CF-Access-Client-Id"] != "CF_ACCESS_CLIENT_ID"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_fetch_cloudflare_tools_list_passes_resolved_credentials_in_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_headers: dict[str, str] = {}

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            base_url: str,
            headers: dict[str, str],
            timeout: object,
        ) -> None:
            captured_headers.update(headers)
            self.base_url = base_url

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_initialize_session(_client: object) -> tuple[str, dict[str, object]]:
        return "sess-1", {"protocolVersion": "2025-03-26"}

    async def fake_notify_initialized(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_tools_list(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [{"name": "context7_query-docs", "inputSchema": {}}]

    monkeypatch.setattr("cyt.cloudflare.mcp.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("cyt.cloudflare.mcp._initialize_session", fake_initialize_session)
    monkeypatch.setattr("cyt.cloudflare.mcp._notify_initialized", fake_notify_initialized)
    monkeypatch.setattr("cyt.cloudflare.mcp._tools_list", fake_tools_list)

    resolved_id = "from-resolve-credential-id"
    resolved_token = "test-ci-stub"  # pragma: allowlist secret
    await fetch_cloudflare_tools_list_async(
        portal_url="https://mcp.example.com/mcp",
        client_id=resolved_id,
        client_secret=resolved_token,
    )

    assert captured_headers["CF-Access-Client-Id"] == resolved_id
    assert captured_headers["CF-Access-Client-Secret"] == resolved_token
