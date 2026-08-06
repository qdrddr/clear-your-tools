"""Tests for executor connection health gating."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from cyt.executor.catalog_disk import (
    raw_catalog_content_hash,
    raw_connections_health_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.executor.connection_flapping import (
    FlappingPolicy,
    clear_flapping_cache,
    update_flapping_states,
)
from cyt.executor.connection_health import (
    ConnectionHealthSnapshot,
    ConnectionKey,
    apply_health_snapshot,
    build_healthy_connections,
    clear_connection_health_cache,
    connection_key_from_tool,
    connections_list_to_dict,
    filter_catalog_by_health,
    filter_summaries_for_schema_fetch,
    health_cache_loaded,
    health_snapshot_from_disk,
    health_snapshot_to_disk,
    refresh_connection_health_async,
    tool_schema_eligible,
)
from cyt.executor.http import (
    clear_executor_catalog_cache,
    evict_schemas_for_connections,
    fetch_executor_tools_for_cli,
    get_executor_catalog,
)

_CONFIG = {
    "pruning": {
        "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
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
    clear_flapping_cache()


_FLAP_POLICY = FlappingPolicy(
    window_size=6,
    min_degraded=1,
    min_transitions=2,
    base_quarantine_seconds=90.0,
    per_degraded_seconds=45.0,
    per_transition_seconds=30.0,
    max_quarantine_seconds=600.0,
    recovery_healthy_samples=3,
    per_episode_seconds=60.0,
)


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


def test_build_healthy_connections_all_healthy() -> None:
    connections = [
        _connection(integration="a"),
        _connection(integration="b", name="other"),
    ]
    healthy = build_healthy_connections(connections)
    assert healthy == {
        ConnectionKey("org", "a", "default"),
        ConnectionKey("org", "b", "other"),
    }


def test_build_healthy_connections_mixed_statuses() -> None:
    connections = [
        _connection(integration="healthy_mcp", status="healthy"),
        _connection(integration="healthy_mcp", name="backup", status="degraded"),
        _connection(integration="degraded_mcp", status="degraded"),
        _connection(integration="unknown_mcp", status=None),
    ]
    healthy = build_healthy_connections(connections)
    assert healthy == {ConnectionKey("org", "healthy_mcp", "default")}


def test_build_healthy_connections_empty() -> None:
    assert build_healthy_connections([]) == set()


def test_tool_schema_eligible_exempts_executor_and_static() -> None:
    healthy = {ConnectionKey("org", "semble_mcp", "default")}
    assert tool_schema_eligible(
        {"integration": "executor", "static": True},
        healthy_connections=healthy,
        gated_connections=set(),
    )
    assert tool_schema_eligible(
        {
            "owner": "org",
            "integration": "semble_mcp",
            "connection": "default",
        },
        healthy_connections=healthy,
        gated_connections=set(),
    )
    assert not tool_schema_eligible(
        {
            "owner": "org",
            "integration": "degraded_mcp",
            "connection": "default",
        },
        healthy_connections=healthy,
        gated_connections=set(),
    )


def test_tool_schema_eligible_skips_missing_metadata() -> None:
    assert not tool_schema_eligible(
        {"name": "tools.orphan.search"},
        healthy_connections={ConnectionKey("org", "semble_mcp", "default")},
        gated_connections=set(),
    )


def test_health_snapshot_disk_round_trip() -> None:
    key = ConnectionKey("org", "semble_mcp", "default")
    snapshot = ConnectionHealthSnapshot(
        connections={key: _connection()},
        healthy_connections={key},
        updated_at=1.0,
        loaded=True,
    )
    payload = health_snapshot_to_disk(snapshot)
    restored = health_snapshot_from_disk(payload)
    assert restored is not None
    assert restored.healthy_connections == {key}
    assert restored.connections[key]["integration"] == "semble_mcp"


def test_filter_catalog_by_health_permissive_when_unloaded() -> None:
    tools = [
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
            "connection": "default",
        },
    ]
    assert filter_catalog_by_health(tools, "missing-slug") == tools


def test_filter_catalog_by_health_applies_when_loaded() -> None:
    slug = "test-slug"
    healthy_key = ConnectionKey("org", "semble_mcp", "default")
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections={healthy_key: _connection(integration="semble_mcp")},
            healthy_connections={healthy_key},
            loaded=True,
        ),
    )
    tools = [
        {
            "name": "tools.semble_mcp.org.default.search",
            "owner": "org",
            "integration": "semble_mcp",
            "connection": "default",
        },
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
            "connection": "default",
        },
    ]
    filtered = filter_catalog_by_health(tools, slug)
    assert [tool["name"] for tool in filtered] == ["tools.semble_mcp.org.default.search"]


def test_degraded_connection_gates_only_its_tools() -> None:
    slug = "multi-conn-slug"
    healthy_key = ConnectionKey("org", "code_review_graph_mcp", "otherconn")
    degraded_key = ConnectionKey("org", "code_review_graph_mcp", "localcodereviewgraph")
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections={
                healthy_key: _connection(
                    integration="code_review_graph_mcp",
                    name="otherconn",
                    status="healthy",
                ),
                degraded_key: _connection(
                    integration="code_review_graph_mcp",
                    name="localcodereviewgraph",
                    status="degraded",
                ),
            },
            healthy_connections={healthy_key},
            loaded=True,
        ),
    )
    tools = [
        {
            "name": "tools.code_review_graph_mcp.org.otherconn.search",
            "owner": "org",
            "integration": "code_review_graph_mcp",
            "connection": "otherconn",
        },
        {
            "name": "tools.code_review_graph_mcp.org.localcodereviewgraph.search",
            "owner": "org",
            "integration": "code_review_graph_mcp",
            "connection": "localcodereviewgraph",
        },
    ]
    filtered = filter_catalog_by_health(tools, slug)
    assert [tool["name"] for tool in filtered] == [
        "tools.code_review_graph_mcp.org.otherconn.search",
    ]


def test_filter_catalog_by_health_excludes_gated_flapping_connection() -> None:
    slug = "flap-slug"
    key = ConnectionKey("org", "flappy_mcp", "default")
    tools: list[dict[str, Any]] = [
        {
            "name": "tools.flappy.org.default.search",
            "owner": "org",
            "integration": "flappy_mcp",
            "connection": "default",
        },
        {
            "name": "executor.coreTools.connections.list",
            "integration": "executor",
            "static": True,
        },
    ]
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections={key: _connection(integration="flappy_mcp", status="healthy")},
            healthy_connections={key},
            loaded=True,
        ),
        update_flapping=False,
    )
    now = 1000.0
    update_flapping_states(
        slug,
        [_connection(integration="flappy_mcp", status="healthy")],
        policy=_FLAP_POLICY,
        now=now,
    )
    update_flapping_states(
        slug,
        [_connection(integration="flappy_mcp", status="degraded")],
        policy=_FLAP_POLICY,
        now=now + 10,
    )
    update_flapping_states(
        slug,
        [_connection(integration="flappy_mcp", status="healthy")],
        policy=_FLAP_POLICY,
        now=now + 20,
    )

    filtered = filter_catalog_by_health(tools, slug)
    assert [tool["name"] for tool in filtered] == ["executor.coreTools.connections.list"]


def test_filter_summaries_for_schema_fetch_respects_eligibility() -> None:
    slug = "schema-slug"
    healthy_key = ConnectionKey("org", "semble_mcp", "default")
    degraded_key = ConnectionKey("org", "degraded_mcp", "default")
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections={
                healthy_key: _connection(integration="semble_mcp"),
                degraded_key: _connection(integration="degraded_mcp", status="degraded"),
            },
            healthy_connections={healthy_key},
            loaded=True,
        ),
    )
    summaries: list[tuple[str, str | None, dict[str, Any]]] = [
        (
            "tools.semble_mcp.org.default.search",
            "search",
            {"owner": "org", "integration": "semble_mcp", "connection": "default"},
        ),
        (
            "tools.degraded.org.default.search",
            "search",
            {"owner": "org", "integration": "degraded_mcp", "connection": "default"},
        ),
    ]
    eligible = filter_summaries_for_schema_fetch(summaries, slug)
    assert [item[0] for item in eligible] == ["tools.semble_mcp.org.default.search"]


def test_evict_schemas_for_connections() -> None:
    from cyt.executor import http as executor_http

    key = ConnectionKey("org", "degraded_mcp", "default")
    state = executor_http._ExecutorCatalogState(
        tools=[
            {
                "name": "tools.degraded.org.default.search",
                "owner": "org",
                "integration": "degraded_mcp",
                "connection": "default",
                "input_schema": {"type": "object"},
            },
        ],
    )
    evict_schemas_for_connections(state, {key})
    assert "input_schema" not in state.tools[0]


@pytest.mark.asyncio
async def test_refresh_connection_health_probes_all_connections() -> None:
    slug = "probe-slug"
    connections = [
        _connection(integration="healthy_mcp", status="healthy"),
        _connection(integration="degraded_mcp", name="bad", status="degraded"),
        _connection(integration="unknown_mcp", name="new", status=None),
    ]
    fetch_count = 0

    async def fake_fetch(**kwargs: object) -> list[dict[str, Any]]:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            return copy.deepcopy(connections)
        final = copy.deepcopy(connections)
        for conn in final:
            if conn.get("name") == "bad":
                conn["lastHealth"] = {"status": "healthy", "checkedAt": 99}
            if conn.get("name") == "new":
                conn["lastHealth"] = {"status": "healthy", "checkedAt": 99}
        return final

    probe_calls: list[str] = []

    async def fake_probe(client: httpx.AsyncClient, key: ConnectionKey) -> dict[str, Any]:
        probe_calls.append(f"{key.owner}/{key.integration}/{key.name}")
        return {"status": "healthy", "checkedAt": 99}

    with (
        patch(
            "cyt.executor.connection_health.fetch_connections_async",
            side_effect=fake_fetch,
        ),
        patch(
            "cyt.executor.connection_health.probe_connection_health_async",
            side_effect=fake_probe,
        ),
    ):
        snapshot, _delta = await refresh_connection_health_async(
            base_url="http://localhost:4789",
            token="token",
            slug=slug,
        )

    assert sorted(probe_calls) == [
        "org/degraded_mcp/bad",
        "org/healthy_mcp/default",
        "org/unknown_mcp/new",
    ]
    assert ConnectionKey("org", "healthy_mcp", "default") in snapshot.healthy_connections
    assert ConnectionKey("org", "degraded_mcp", "bad") in snapshot.healthy_connections
    assert ConnectionKey("org", "unknown_mcp", "new") in snapshot.healthy_connections


def test_get_executor_catalog_non_blocking_returns_snapshot_during_refresh(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    stale = [
        {
            "name": "tools.stale.org.default.search",
            "owner": "org",
            "integration": "semble_mcp",
            "connection": "default",
            "input_schema": {},
        },
    ]
    content_hash = raw_catalog_content_hash(stale)
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
                "tools": stale,
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch(
            "cyt.executor.http._ensure_scheduler_started",
        ),
        patch(
            "cyt.executor.connection_health.refresh_connection_health_async",
            side_effect=AssertionError("hook path must not await health refresh"),
        ),
    ):
        loaded = get_executor_catalog(_CONFIG, allow_prompt=False, blocking=False)

    assert loaded is not None
    assert loaded == stale


def test_write_disk_catalog_persists_connections_health(tmp_path: Path) -> None:
    slug = "http___localhost_4789"
    tools = [{"name": "tools.demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    key = ConnectionKey("org", "semble_mcp", "default")
    health = health_snapshot_to_disk(
        ConnectionHealthSnapshot(
            connections={key: _connection()},
            healthy_connections={key},
            loaded=True,
        ),
    )
    cache_dir = tmp_path / "executor-catalog"
    with patch(
        "cyt.executor.catalog_disk.executor_catalog_cache_dir",
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
    assert envelope["connections_health"]["healthy_connections"] == [
        ["org", "semble_mcp", "default"],
    ]


def test_get_executor_catalog_filters_by_loaded_health(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    tools: list[dict[str, Any]] = [
        {
            "name": "tools.semble_mcp.org.default.search",
            "owner": "org",
            "integration": "semble_mcp",
            "connection": "default",
            "input_schema": {},
        },
        {
            "name": "tools.degraded.org.default.search",
            "owner": "org",
            "integration": "degraded_mcp",
            "connection": "default",
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
    healthy_key = ConnectionKey("org", "semble_mcp", "default")
    degraded_key = ConnectionKey("org", "degraded_mcp", "default")
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections={
                healthy_key: _connection(integration="semble_mcp", status="healthy"),
                degraded_key: _connection(integration="degraded_mcp", status="degraded"),
            },
            healthy_connections={healthy_key},
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
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch("cyt.executor.http._ensure_scheduler_started"),
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
            "connection": "default",
            "input_schema": {},
        },
    ]
    content_hash = raw_catalog_content_hash(tools)
    degraded_key = ConnectionKey("org", "degraded_mcp", "default")
    apply_health_snapshot(
        slug,
        ConnectionHealthSnapshot(
            connections={
                degraded_key: _connection(integration="degraded_mcp", status="degraded"),
            },
            healthy_connections=set(),
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
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch("cyt.executor.http._ensure_scheduler_started"),
    ):
        loaded = fetch_executor_tools_for_cli(_CONFIG, allow_prompt=False, blocking=False)

    assert loaded == tools


def test_connection_key_from_tool() -> None:
    assert connection_key_from_tool(
        {"owner": "org", "integration": "semble_mcp", "connection": "default"},
    ) == ConnectionKey("org", "semble_mcp", "default")


@pytest.mark.asyncio
async def test_fetch_list_preserves_metadata_and_include_blocked() -> None:
    from cyt.executor.http import _fetch_list_async

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
                "name": "search",
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

    with patch("cyt.executor.http.httpx.AsyncClient", return_value=_FakeClient()):
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
                "tool_name": "search",
            },
        ),
    ]


def test_connections_list_to_dict() -> None:
    conn = _connection()
    mapping = connections_list_to_dict([conn])
    assert mapping[ConnectionKey("org", "semble_mcp", "default")] == conn


def test_cold_start_disk_load_skips_health_snapshot(tmp_path: Path) -> None:
    slug = "http___localhost_4789__EXECUTOR_TOKEN"
    tools = [{"name": "tools.demo", "input_schema": {}}]
    content_hash = raw_catalog_content_hash(tools)
    key = ConnectionKey("org", "degraded_mcp", "default")
    health = health_snapshot_to_disk(
        ConnectionHealthSnapshot(
            connections={key: _connection(integration="degraded_mcp", status="degraded")},
            healthy_connections=set(),
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
            },
        ),
        encoding="utf-8",
    )

    with (
        patch(
            "cyt.executor.http.resolve_credential",
            return_value=("token", "keyring"),
        ),
        patch(
            "cyt.executor.catalog_disk.executor_catalog_cache_dir",
            return_value=cache_dir,
        ),
        patch("cyt.executor.http._ensure_scheduler_started"),
    ):
        loaded = get_executor_catalog(_CONFIG, allow_prompt=False, blocking=False)

    assert loaded == tools
    assert not health_cache_loaded(slug)


def test_executor_cache_config_defaults() -> None:
    from cyt.config import tools_hook_executor_cache_settings

    settings = tools_hook_executor_cache_settings(_CONFIG)
    assert settings["health_refresh_seconds"] == 1
    assert settings["health_probe_concurrency"] == 4
    assert settings["catalog_schema_refresh_seconds"] == 120
    assert settings["disk_flush_seconds"] == 900


def test_debug_disk_flag() -> None:
    from cyt.executor.connection_health import (
        debug_disk_enabled,
        set_executor_debug_disk,
    )

    set_executor_debug_disk(True)
    assert debug_disk_enabled() is True
    set_executor_debug_disk(False)
    assert debug_disk_enabled() is False
