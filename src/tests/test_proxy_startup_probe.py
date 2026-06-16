"""Tests for Claude Code startup HEAD probe handling."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from cyt.proxy.reverse import create_app, is_startup_probe, upstream_reachable


def test_is_startup_probe() -> None:
    assert is_startup_probe("HEAD", "")
    assert is_startup_probe("HEAD", "/")
    assert not is_startup_probe("HEAD", "/v1/messages")
    assert not is_startup_probe("GET", "")
    assert not is_startup_probe("POST", "")


@pytest.mark.asyncio
async def test_upstream_reachable_on_any_http_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert request.url.host == "api.example.com"
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await upstream_reachable(client, "https://api.example.com", {})

    assert ok is True


@pytest.mark.asyncio
async def test_upstream_reachable_false_on_connection_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await upstream_reachable(client, "https://api.example.com", {})

    assert ok is False


@pytest.mark.asyncio
async def test_health_endpoint_includes_cyt_name() -> None:
    app = create_app({"/anthropic": ("https://api.example.com", "anthropic")})

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "name": "cyt",
        "status": "ok",
        "endpoints": ["anthropic"],
        "debug": False,
        "debug_dry_run": False,
    }


@pytest.mark.asyncio
async def test_health_endpoint_includes_launch_agent_and_all_endpoints() -> None:
    app = create_app(
        {
            "/openrouter": ("https://openrouter.ai/api", "anthropic"),
            "/anthropic": ("https://api.example.com", "anthropic"),
        },
        launch_agent="claude",
    )

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "name": "cyt",
        "status": "ok",
        "endpoints": ["anthropic", "openrouter"],
        "agent": "claude",
        "debug": False,
        "debug_dry_run": False,
    }


@pytest.mark.asyncio
async def test_proxy_startup_probe_returns_200_when_upstream_reachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        return httpx.Response(404)

    mock_upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app({"/anthropic": ("https://api.example.com", "anthropic")})
    app.state.http_client = mock_upstream

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.head("/anthropic")

    await mock_upstream.aclose()
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_proxy_startup_probe_forwards_non_probe_head() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        return httpx.Response(405, stream=httpx.ByteStream(b""))

    mock_upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app({"/anthropic": ("https://api.example.com", "anthropic")})
    app.state.http_client = mock_upstream

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.head("/anthropic/v1/messages")

    await mock_upstream.aclose()
    assert response.status_code == 405


@pytest.mark.asyncio
async def test_proxy_startup_probe_returns_502_when_upstream_unreachable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    mock_upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app({"/anthropic": ("https://api.example.com", "anthropic")})
    app.state.http_client = mock_upstream

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.head("/anthropic")

    await mock_upstream.aclose()
    assert response.status_code == 502
