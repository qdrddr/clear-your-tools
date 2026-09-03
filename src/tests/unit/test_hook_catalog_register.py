"""Tests for hook daemon catalog register HTTP routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from httpx import ASGITransport

from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash
from cyt.hook.catalog_registry import clear_catalog_registry, register_catalog
from cyt.proxy.reverse import create_app


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    clear_catalog_registry()
    yield
    clear_catalog_registry()


@pytest.fixture
async def catalog_client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(routes={}, config={"skills": {"enabled": False}})
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        yield client


@pytest.mark.asyncio
async def test_hook_catalog_register_full_push(catalog_client: httpx.AsyncClient) -> None:
    tools = [{"name": "alpha", "input_schema": {"type": "object"}}]
    content_hash = raw_catalog_content_hash(tools)
    response = await catalog_client.post(
        "/hook/catalog/register",
        json={
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "stored"}


@pytest.mark.asyncio
async def test_hook_catalog_register_hash_only_204(catalog_client: httpx.AsyncClient) -> None:
    tools = [{"name": "alpha", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    register_catalog(
        {
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    response = await catalog_client.post(
        "/hook/catalog/register",
        json={
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
        },
    )
    assert response.status_code == 204
    assert response.text == ""


@pytest.mark.asyncio
async def test_hook_catalog_register_hash_only_404_then_full(
    catalog_client: httpx.AsyncClient,
) -> None:
    missing = await catalog_client.post(
        "/hook/catalog/register",
        json={
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": "unknown-hash",
        },
    )
    assert missing.status_code == 404

    tools = [{"name": "beta", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    stored = await catalog_client.post(
        "/hook/catalog/register",
        json={
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:1",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    assert stored.status_code == 200


@pytest.mark.asyncio
async def test_hook_catalog_deregister(catalog_client: httpx.AsyncClient) -> None:
    tools = [{"name": "alpha", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    await catalog_client.post(
        "/hook/catalog/register",
        json={
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:99",
            "content_hash": content_hash,
            "tools": tools,
        },
    )
    response = await catalog_client.post(
        "/hook/catalog/deregister",
        json={
            "agent": "cursor",
            "scope": "global",
            "workspace_root": None,
            "instance_id": "pid:99",
        },
    )
    assert response.status_code == 200

    status = await catalog_client.get("/hook/catalog/status")
    payload = status.json()
    assert payload["registrations"] == []


@pytest.mark.asyncio
async def test_hook_catalog_register_rejects_non_localhost(
    catalog_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cyt.hook.http_server._is_localhost_request",
        lambda _request: False,
    )
    response = await catalog_client.post(
        "/hook/catalog/register",
        content=json.dumps({"agent": "cursor", "scope": "global", "content_hash": "x"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 403
