"""Tests for cyt-mcp CLI startup."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyt_mcp.cli import _run_catalog, _run_search, _run_server
from cyt_mcp.config import AggregatorConfig, sample_aggregator_config
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import MCP_WIRE_SEARCH_TOOL_NAME


def _stdio_config() -> AggregatorConfig:
    return sample_aggregator_config()


def _mock_coordinator() -> MagicMock:
    return MagicMock()


def test_run_server_stdio_uses_run_async() -> None:
    """stdio must await run_async; cold cache refresh runs in background, not before stdio."""
    config = _stdio_config()
    refresh_calls: list[str] = []

    async def _refresh(*_args: object, **_kwargs: object) -> None:
        refresh_calls.append("refresh")
        await asyncio.sleep(0)

    async def _run_async(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.05)

    with (
        patch("cyt_mcp.cli.build_aggregator") as build,
        patch("cyt_mcp.catalog_build.hydrate_runtime_cache", return_value=False),
        patch("cyt_mcp.catalog_build.refresh_catalog_cache", side_effect=_refresh),
        patch("cyt_mcp.hook_daemon_push.register_push_context"),
        patch("cyt_mcp.hook_daemon_push.deregister_catalog_push"),
    ):
        server = AsyncMock()
        build.return_value = (server, None, _mock_coordinator())
        server.run_async = AsyncMock(side_effect=_run_async)
        result = asyncio.run(_run_server(config))
    assert result == 0
    assert refresh_calls == ["refresh"]
    server.run_async.assert_awaited_once_with("stdio", show_banner=False)


def test_run_server_stdio_skips_background_refresh_when_cache_warmed() -> None:
    config = _stdio_config()
    refresh_calls: list[str] = []

    def _hydrate_warm(cache: RuntimeToolCache, _config: object) -> None:
        cache.replace([{"name": "tool-a"}])

    async def _refresh(*_args: object, **_kwargs: object) -> None:
        refresh_calls.append("refresh")
        await asyncio.sleep(0)

    async def _run_async(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0.01)

    with (
        patch("cyt_mcp.cli.build_aggregator") as build,
        patch("cyt_mcp.catalog_build.hydrate_runtime_cache", side_effect=_hydrate_warm),
        patch("cyt_mcp.catalog_build.refresh_catalog_cache", side_effect=_refresh),
        patch("cyt_mcp.hook_daemon_push.register_push_context"),
        patch("cyt_mcp.hook_daemon_push.deregister_catalog_push"),
    ):
        server = AsyncMock()
        build.return_value = (server, None, _mock_coordinator())
        server.run_async = AsyncMock(side_effect=_run_async)
        result = asyncio.run(_run_server(config))
    assert result == 0
    assert refresh_calls == []


def test_run_search_wires_refresh_and_lookup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _stdio_config()

    async def _fake_refresh(_server: object, cache: object, _config: object) -> None:
        assert isinstance(cache, RuntimeToolCache)
        cache.replace(
            [
                {
                    "name": "codebase-memory-mcp_search_graph",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"project": {"type": "string"}},
                    },
                },
            ],
            search_index={
                "codebase-memory-mcp_search_graph": {
                    "name": "codebase-memory-mcp_search_graph",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"project": {"type": "string"}},
                    },
                },
            },
        )

    monkeypatch.setattr("cyt_mcp.cli.refresh_runtime_cache", _fake_refresh)
    result = asyncio.run(_run_search(config, "codebase-memory-mcp_search_graph"))
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "codebase-memory-mcp_search_graph"


def test_run_catalog_wires_refresh_and_export(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _stdio_config()

    async def _fake_refresh(_server: object, cache: object, _config: object) -> None:
        assert isinstance(cache, RuntimeToolCache)
        cache.replace(
            [
                {
                    "name": "codebase-memory-mcp_query_graph",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            ],
        )

    monkeypatch.setattr("cyt_mcp.cli.refresh_runtime_cache", _fake_refresh)
    args = argparse.Namespace(source="backend", full=False, tokens=False, json=True)
    result = asyncio.run(_run_catalog(config, args))
    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    assert payload["agent"] == "cursor"
    assert "servers" in payload
    unknown_tools = payload["servers"]["unknown"]["tools"]
    assert unknown_tools[0]["name"] == "codebase-memory-mcp_query_graph"


def test_run_catalog_both_source_exports_hook_backend_and_prints_token_summaries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _stdio_config()

    async def _fake_refresh(_server: object, cache: object, _config: object) -> None:
        assert isinstance(cache, RuntimeToolCache)
        cache.replace(
            [
                {
                    "name": "codebase-memory-mcp_query_graph",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    "description": "query graph",
                },
            ],
            search_index={
                "codebase-memory-mcp_query_graph": {
                    "name": "codebase-memory-mcp_query_graph",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    "outputSchema": {"type": "object"},
                    "description": "query graph",
                },
            },
        )

    monkeypatch.setattr("cyt_mcp.cli.refresh_runtime_cache", _fake_refresh)
    args = argparse.Namespace(source="both", full=False, tokens=False, json=True)
    result = asyncio.run(_run_catalog(config, args))
    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    assert "backend" in payload
    assert "frontend" in payload
    backend_tool = payload["backend"]["servers"]["unknown"]["tools"][0]
    assert backend_tool["name"] == "codebase-memory-mcp_query_graph"
    assert "input_schema" in backend_tool
    assert "outputSchema" not in backend_tool
    assert "catalog tokens (compact JSON): backend" in captured.err
    assert "catalog tokens (compact JSON): frontend" in captured.err


def test_run_catalog_full_backend_uses_search_index(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _stdio_config()

    async def _fake_refresh(_server: object, cache: object, _config: object) -> None:
        assert isinstance(cache, RuntimeToolCache)
        cache.replace(
            [{"name": "demo_tool", "inputSchema": {"type": "object", "properties": {}}}],
            search_index={
                "demo_tool": {
                    "name": "demo_tool",
                    "inputSchema": {"type": "object", "properties": {}},
                    "outputSchema": {"type": "object"},
                },
            },
        )

    monkeypatch.setattr("cyt_mcp.cli.refresh_runtime_cache", _fake_refresh)
    args = argparse.Namespace(source="backend", full=True, tokens=False, json=True)
    result = asyncio.run(_run_catalog(config, args))
    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    assert "servers" in payload
    tool = next(
        tool
        for block in payload["servers"].values()
        for tool in block["tools"]
        if tool.get("name") == "demo_tool"
    )
    assert tool["outputSchema"] == {"type": "object"}


def test_run_catalog_frontend_source_prints_token_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _stdio_config()

    async def _fake_refresh(_server: object, cache: object, _config: object) -> None:
        assert isinstance(cache, RuntimeToolCache)
        cache.replace(
            [
                {
                    "name": "codebase-memory-mcp_query_graph",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            ],
            search_index={
                "codebase-memory-mcp_query_graph": {
                    "name": "codebase-memory-mcp_query_graph",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                    "outputSchema": {"type": "object"},
                },
            },
        )

    monkeypatch.setattr("cyt_mcp.cli.refresh_runtime_cache", _fake_refresh)
    args = argparse.Namespace(source="frontend", full=False, tokens=True, json=True)
    result = asyncio.run(_run_catalog(config, args))
    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    assert payload["agent"] == "cursor"
    assert any(tool["name"] == MCP_WIRE_SEARCH_TOOL_NAME for tool in payload["tools"])
    assert "catalog tokens (compact JSON): frontend" in captured.err


def test_run_server_skips_pairing_when_skip_txt_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".cursor" / "cyt").mkdir(parents=True)
    (workspace / ".cursor" / "cyt" / "skip.txt").write_text("", encoding="utf-8")

    config = _stdio_config()
    monkeypatch.chdir(workspace)

    with (
        patch("cyt_mcp.cli.build_aggregator") as build,
        patch("cyt_mcp.cli.refresh_runtime_cache", new_callable=AsyncMock),
        patch("cyt_client.pairing.repair_pairing_from_mcp_runtime") as repair,
    ):
        server = AsyncMock()
        build.return_value = (server, None, _mock_coordinator())
        server.run_async = AsyncMock()
        result = asyncio.run(_run_server(config))

    assert result == 0
    repair.assert_not_called()
