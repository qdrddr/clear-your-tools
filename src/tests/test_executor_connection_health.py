"""Tests for executor connection health gating."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from cyt.tools.sources.executor_catalog_disk import (
    raw_catalog_content_hash,
    raw_connections_health_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.tools.sources.executor_connection_health import (
    ConnectionHealthSnapshot,
    ConnectionKey,
    apply_health_snapshot,
    build_healthy_integrations,
    clear_connection_health_cache,
    filter_catalog_by_health,
    filter_tools_by_integration_health,
    health_snapshot_from_disk,
    health_snapshot_to_disk,
    refresh_connection_health_async,
)
from cyt.tools.sources.executor_http import (
    clear_executor_catalog_cache,
    fetch_executor_tools_for_cli,
    get_executor_catalog,
)

_CONFIG = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "hook": {
                "tools_from": "executor",
                "executor_url": "http://localhost:4789",
                "executor_token_var": "EXECUTOR_TOKEN",
            },
        },
    },
}


def setup_function() -> None:
    clear_executor_catalog_cache()
    clear_connection_health_cache()


def _connection(
    *,
    owner: str = "org",
    integration: str = "semble_mcp",
    name: str = "default",
    status: str | None = "healthy",
) -> dict[str, Any]:
    conn: dict[str, Any] = {
        "owner": owner,
        "name": name,
        "integration": integration,
    }
    if status is not None:
        conn["lastHealth"] = {"status": status, "checkedAt": 1}
    else:
        conn["lastHealth"] = None
    return conn


def test_build_healthy_integrations_all_healthy() -> None:
    connections = [
        _connection(integration="a"),
        _connection(integration="b", name="other"),
    ]
    healthy = build_healthy_integrations(connections)
    assert healthy == {("org", "a"), ("org", "b")}


def test_build_healthy_integrations_mixed_statuses() -> None:
    connections = [
        _connection(integration="healthy_mcp", status="healthy"),
        _connection(integration="healthy_mcp", name="backup", status="degraded"),
        _connection(integration="degraded_mcp", status="degraded"),
        _connection(integration="unknown_mcp", status=None),
    ]
    healthy = build_healthy_integrations(connections)
    assert healthy == {("org", "healthy_mcp")}


def test_build_healthy_integrations_empty() -> None:
    assert build_healthy_integrations([]) == set()


def test_filter_tools_by_integration_health_exempts_executor_and_static() -> None:
    tools: list[dict[str, Any]] = [
        {
            "name": "executor.coreTools.connections.list",
            "integration": "executor",
            "static": True,
        },
        {
            "name": "tools.semble_mcp.org.default.search",
            "owner": "org",
            "integration": "semble_mcp",
        },
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
        },
    ]
    healthy = {("org", "semble_mcp")}
    filtered = filter_tools_by_integration_health(tools, healthy)
    names = [tool["name"] for tool in filtered]
    assert names == [
        "executor.coreTools.connections.list",
        "tools.semble_mcp.org.default.search",
    ]


def test_filter_tools_by_integration_health_skips_missing_metadata() -> None:
    tools = [{"name": "tools.orphan.search", "description": "no routing metadata"}]
    filtered = filter_tools_by_integration_health(tools, {("org", "semble_mcp")})
    assert filtered == []


def test_health_snapshot_disk_round_trip() -> None:
    snapshot = ConnectionHealthSnapshot(
        connections=[_connection()],
        healthy_integrations={("org", "semble_mcp")},
        updated_at=1.0,
        loaded=True,
    )
    payload = health_snapshot_to_disk(snapshot)
    restored = health_snapshot_from_disk(payload)
    assert restored is not None
    assert restored.healthy_integrations == {("org", "semble_mcp")}
    assert restored.connections[0]["integration"] == "semble_mcp"


def test_filter_catalog_by_health_permissive_when_unloaded() -> None:
    tools = [
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
        },
    ]
    assert filter_catalog_by_health(tools, "missing-slug") == tools


def test_filter_catalog_by_health_applies_when_loaded() -> None:
    slug = "test-slug"
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections=[_connection(integration="semble_mcp")],
            healthy_integrations={("org", "semble_mcp")},
            loaded=True,
        ),
    )
    tools = [
        {
            "name": "tools.semble_mcp.org.default.search",
            "owner": "org",
            "integration": "semble_mcp",
        },
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
        },
    ]
    filtered = filter_catalog_by_health(tools, slug)
    assert [tool["name"] for tool in filtered] == ["tools.semble_mcp.org.default.search"]


@pytest.mark.asyncio
async def test_refresh_connection_health_probes_unhealthy_connections() -> None:
    connections = [
        _connection(integration="healthy_mcp", status="healthy"),
        _connection(integration="degraded_mcp", name="bad", status="degraded"),
        _connection(integration="unknown_mcp", name="new", status=None),
    ]

    async def fake_fetch(**kwargs: object) -> list[dict[str, Any]]:
        return copy.deepcopy(connections)

    probe_calls: list[str] = []

    async def fake_probe(client: httpx.AsyncClient, key: ConnectionKey) -> dict[str, Any]:
        probe_calls.append(f"{key.owner}/{key.integration}/{key.name}")
        return {"status": "healthy", "checkedAt": 99}

    with (
        patch(
            "cyt.tools.sources.executor_connection_health.fetch_connections_async",
            side_effect=fake_fetch,
        ),
        patch(
            "cyt.tools.sources.executor_connection_health.probe_connection_health_async",
            side_effect=fake_probe,
        ),
    ):
        snapshot = await refresh_connection_health_async(
            base_url="http://localhost:4789",
            token="token",
        )

    assert sorted(probe_calls) == ["org/degraded_mcp/bad", "org/unknown_mcp/new"]
    assert ("org", "healthy_mcp") in snapshot.healthy_integrations
    assert ("org", "degraded_mcp") in snapshot.healthy_integrations
    assert ("org", "unknown_mcp") in snapshot.healthy_integrations


def test_write_disk_catalog_persists_connections_health(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    tools = [{"name": "tools.demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    health = health_snapshot_to_disk(
        ConnectionHealthSnapshot(
            connections=[_connection()],
            healthy_integrations={("org", "semble_mcp")},
            loaded=True,
        ),
    )
    cache_dir = tmp_path / "executor-catalog"
    with patch(
        "cyt.tools.sources.executor_catalog_disk.executor_catalog_cache_dir",
        return_value=cache_dir,
    ):
        write_disk_catalog(
            slug,
            executor_url="http://localhost:4789",
            tools=tools,
            content_hash=content_hash,
            connections_health=health,
        )
        envelope = read_disk_catalog(slug)

    assert envelope is not None
    assert envelope.get("connections_health_hash") == raw_connections_health_hash(health)
    assert envelope["connections_health"]["healthy_integrations"] == [["org", "semble_mcp"]]


def test_get_executor_catalog_filters_by_loaded_health(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    tools: list[dict[str, Any]] = [
        {
            "name": "tools.semble_mcp.org.default.search",
            "owner": "org",
            "integration": "semble_mcp",
            "input_schema": {},
        },
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
            "input_schema": {},
        },
        {
            "name": "executor.coreTools.connections.list",
            "integration": "executor",
            "static": True,
            "input_schema": {},
        },
    ]
    content_hash = raw_catalog_content_hash(tools)
    health = health_snapshot_to_disk(
        ConnectionHealthSnapshot(
            connections=[
                _connection(integration="semble_mcp", status="healthy"),
                _connection(integration="degraded_mcp", status="degraded"),
            ],
            healthy_integrations={("org", "semble_mcp")},
            loaded=True,
        ),
    )
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": content_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": len(tools),
                "tools": tools,
                "connections_health": health,
                "connections_health_hash": raw_connections_health_hash(health),
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch("cyt.tools.sources.executor_http.schedule_executor_catalog_refresh"),
    ):
        loaded = get_executor_catalog(_CONFIG, allow_prompt=False, blocking=False)

    assert loaded is not None
    names = [tool["name"] for tool in loaded]
    assert names == [
        "tools.semble_mcp.org.default.search",
        "executor.coreTools.connections.list",
    ]


def test_fetch_executor_tools_for_cli_skips_health_filter(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    tools = [
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
            "input_schema": {},
        },
    ]
    content_hash = raw_catalog_content_hash(tools)
    health = health_snapshot_to_disk(
        ConnectionHealthSnapshot(
            connections=[_connection(integration="degraded_mcp", status="degraded")],
            healthy_integrations=set(),
            loaded=True,
        ),
    )
    cache_dir = tmp_path / "executor-catalog"
    path = cache_dir / f"{slug}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "executor_url": "http://localhost:4789",
                "catalog_content_hash": content_hash,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "tool_count": 1,
                "tools": tools,
                "connections_health": health,
                "connections_health_hash": raw_connections_health_hash(health),
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.tools.sources.executor_http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.tools.sources.executor_catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch("cyt.tools.sources.executor_http.schedule_executor_catalog_refresh"),
    ):
        loaded = fetch_executor_tools_for_cli(_CONFIG, allow_prompt=False, blocking=False)

    assert loaded == tools


@pytest.mark.asyncio
async def test_fetch_list_preserves_metadata_and_include_blocked() -> None:
    from cyt.tools.sources.executor_http import _fetch_list_async

    request = httpx.Request(
        "GET",
        "http://localhost:4789/api/tools",
        params={"includeBlocked": "false"},
    )
    response = httpx.Response(
        200,
        json=[
            {
                "address": "tools.semble_mcp.org.default.search",
                "owner": "org",
                "integration": "semble_mcp",
                "connection": "default",
                "description": "search",
                "static": None,
            },
        ],
        request=request,
    )

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
            assert params == {"includeBlocked": "false"}
            return response

    with patch("cyt.tools.sources.executor_http.httpx.AsyncClient", return_value=_FakeClient()):
        summaries = await _fetch_list_async(base_url="http://localhost:4789", token="token")

    assert summaries == [
        (
            "tools.semble_mcp.org.default.search",
            "search",
            {
                "owner": "org",
                "integration": "semble_mcp",
                "connection": "default",
                "static": None,
            },
        ),
    ]
