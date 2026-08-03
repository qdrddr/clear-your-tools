"""Tests for cyt-mcp CLI startup."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cyt_mcp.cli import _run_catalog, _run_search, _run_server
from cyt_mcp.config import AggregatorConfig, HttpSettings
from cyt_mcp.runtime_cache import RuntimeToolCache


def _stdio_config() -> AggregatorConfig:
    return AggregatorConfig(
        agent="cursor",
        mcp_servers={},
        transport="stdio",
        http=HttpSettings(
            host="127.0.0.1",
            port=8765,
            mcp_path="/mcp",
            catalog_path="/catalog",
        ),
        codex_stubs_include_description=False,
        aggregator_path=Path("~/.config/cyt/mcp-aggregator.yaml"),
        agent_mcp_path=Path("~/.config/cyt/mcp/cursor.json"),
    )


def test_run_server_stdio_uses_run_async() -> None:
    """stdio must await run_async; sync run() inside asyncio.run() crashes."""
    config = _stdio_config()
    with (
        patch("cyt_mcp.cli.build_aggregator") as build,
        patch("cyt_mcp.cli.refresh_runtime_cache", new_callable=AsyncMock) as refresh,
    ):
        server = build.return_value
        server.run_async = AsyncMock()
        result = asyncio.run(_run_server(config))
    assert result == 0
    refresh.assert_awaited_once()
    server.run_async.assert_awaited_once_with("stdio", show_banner=False)


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
    result = asyncio.run(_run_catalog(config))
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"] == "cursor"
    assert payload["tools"][0]["name"] == "codebase-memory-mcp_query_graph"


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
        server = build.return_value
        server.run_async = AsyncMock()
        result = asyncio.run(_run_server(config))

    assert result == 0
    repair.assert_not_called()
